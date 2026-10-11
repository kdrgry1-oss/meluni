"""AUTH14 — kayıt ucunda hesap-varlığı taramasına (enumeration) karşı koruma.

Kayıt akışı bilinçli olarak doğrulamasız kalır (kullanıcı kararı: yeni müşteri hemen giriş yapar);
bu yüzden "Bu e-posta / telefon zaten kayıtlı" yanıtı tek tek sorguda görünür. Bu modül TOPLU
taramayı engeller ve görünür kılar:

- Ağ başına (IPv4 /24, IPv6 /48) son 24 saatte "zaten kayıtlı" yanıtı alan deneme sayısı ve toplam
  kayıt denemesi sayılır. Tavan aşılınca o ağ, e-postanın kayıtlı olup olmadığına BAKILMADAN aynı
  429 yanıtını alır (tarayıcıya artık cevap sızmaz) ve süper adminlere TEK uyarı gider.
- Ağ başına son 1 saatte birden fazla "zaten kayıtlı" yanıtından sonra kademeli bekleme (otomasyonu
  yavaşlatır; gerçek müşteri ilk denemede beklemez).
- Küresel saatlik "zaten kayıtlı" sayısı eşiği aşılırsa (dağıtık tarama) uyarı.
Eşikler İşletme Kuralları'ndan (yalnız süper admin). Kişisel veri saklanmaz: yalnız ağ öneki ve
sayaçlar; e-posta/telefon yazılmaz.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_COLL = "register_probe_counters"
_IDX = {"done": False}
BLOCK_DETAIL = ("Çok fazla kayıt denemesi yapıldı. Lütfen daha sonra tekrar deneyin; hesabınız varsa "
                "giriş yapabilir ya da şifrenizi sıfırlayabilirsiniz.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


async def _ensure_ttl(db) -> None:
    """Sayaçlar 3 gün sonra kendiliğinden silinir (TTL)."""
    if _IDX["done"]:
        return
    _IDX["done"] = True
    try:
        await db[_COLL].create_index("expire_at", expireAfterSeconds=0)
    except Exception:
        pass


def _ins(net: str, hour: str) -> dict:
    return {"net": net, "hour": hour, "created_at": _now().isoformat(), "expire_at": _now() + timedelta(days=3)}


async def _rules(db) -> dict:
    from business_rules import get_rule

    async def _int(key, default):
        try:
            return max(1, int(await get_rule(db, key, default)))
        except (TypeError, ValueError):
            return default
    return {
        "exists_cap": await _int("security.register_exists_daily_cap_per_net", 10),
        "attempt_cap": await _int("security.register_attempt_daily_cap_per_net", 40),
        "global_alert": await _int("security.register_exists_hourly_alert", 60),
    }


async def _sum_net(db, net: str, field: str, hours: int) -> int:
    since = _hour_key(_now() - timedelta(hours=hours - 1))
    total = 0
    async for d in db[_COLL].find({"net": net, "hour": {"$gte": since}}, {"_id": 0, field: 1}):
        total += int(d.get(field) or 0)
    return total


async def check_before_register(db, ip: str) -> str:
    """Kayıt mantığından ÖNCE çağrılır. Ağ tavanı aşıldıysa 429 fırlatır. Dönüş: ağ öneki."""
    from security.events import ip_prefix, security_event
    net = ip_prefix(ip) or "?"
    try:
        await _ensure_ttl(db)
        r = await _rules(db)
        hk = _hour_key(_now())
        await db[_COLL].update_one({"net": net, "hour": hk},
                                   {"$inc": {"attempts": 1}, "$setOnInsert": _ins(net, hk)}, upsert=True)
        exists_24h = await _sum_net(db, net, "exists", 24)
        attempts_24h = await _sum_net(db, net, "attempts", 24)
    except Exception as e:  # sayaç deposu yoksa kayıt bloklanmaz (müşteri kaybı yok), uyarı loglanır
        logger.warning("[register_guard] sayaç okunamadı: %s", type(e).__name__)
        return net
    if exists_24h >= r["exists_cap"] or attempts_24h > r["attempt_cap"]:
        try:
            day = _now().strftime("%Y-%m-%d")
            res = await db[_COLL].update_one({"net": net, "hour": f"alert-{day}"},
                                             {"$setOnInsert": _ins(net, f"alert-{day}")}, upsert=True)
            if getattr(res, "upserted_id", None) is not None:
                await security_event("register_enumeration_blocked", None,
                                     {"net": net, "exists_24h": exists_24h, "attempts_24h": attempts_24h,
                                      "fp": f"{net}:{day}"})
        except Exception:
            pass
        raise HTTPException(status_code=429, detail=BLOCK_DETAIL)
    return net


async def note_exists_hit(db, net: str) -> None:
    """'Zaten kayıtlı' yanıtı verilmeden hemen önce çağrılır: sayaç + kademeli bekleme + küresel alarm."""
    from security.events import security_event
    try:
        hk = _hour_key(_now())
        await db[_COLL].update_one({"net": net, "hour": hk},
                                   {"$inc": {"exists": 1}, "$setOnInsert": _ins(net, hk)}, upsert=True)
        g = await db[_COLL].find_one_and_update({"net": "*", "hour": hk},
                                                {"$inc": {"exists": 1}, "$setOnInsert": _ins("*", hk)},
                                                upsert=True, return_document=True)
        n_hour = await _sum_net(db, net, "exists", 1)
        r = await _rules(db)
        g_n = int((g or {}).get("exists") or 0)
        if g_n == r["global_alert"]:
            await security_event("register_enumeration_wave", None,
                                 {"exists_last_hour": g_n, "fp": hk})
    except Exception as e:
        logger.warning("[register_guard] sayaç yazılamadı: %s", type(e).__name__)
        return
    if n_hour >= 2:
        await asyncio.sleep(min(2 ** (n_hour - 2), 8))
