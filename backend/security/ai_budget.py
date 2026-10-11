"""Denetim #25: personel yapay zekâ uçları için harcama/kullanım koruması.

Her ücretli LLM çağrısı ``async with ai_guard(user, op, units):`` içinde yapılır:
  * kişi başı SAATLİK birim sınırı (ai.user_hourly_limit),
  * firma geneli GÜNLÜK birim sınırı (ai.daily_limit),
  * kişi başı EŞZAMANLI çağrı tavanı (ai.user_concurrency),
  * her çağrı db.ai_usage'a yazılır (kim, hangi işlem, kaç birim, ne zaman) — denetlenebilir.
Sınırlar İşletme Kuralları'ndan (admin düzenler); 0 = sınırsız.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

_ACTIVE: dict = {}


async def _rule(key: str, default: int) -> int:
    try:
        from business_rules import get_rule
        from routes.deps import db
        v = await get_rule(db, key, default)
        return int(v if v not in (None, "") else default)
    except Exception:
        return int(default)


async def charge(user: dict, op: str, units: int = 1) -> None:
    """Saatlik (kişi) + günlük (firma) bütçeyi kontrol eder ve kullanımı kaydeder; aşımda 429."""
    from routes.deps import db
    uid = str((user or {}).get("id") or "anon")
    units = max(1, int(units or 1))
    now = datetime.now(timezone.utc)
    hourly = await _rule("ai.user_hourly_limit", 120)
    if hourly > 0:
        used = 0
        async for r in db.ai_usage.find({"user_id": uid, "at": {"$gte": (now - timedelta(hours=1)).isoformat()}},
                                        {"_id": 0, "units": 1}):
            used += int(r.get("units") or 1)
        if used + units > hourly:
            raise HTTPException(status_code=429, detail="Saatlik yapay zekâ kullanım sınırınıza ulaştınız; daha sonra deneyin.")
    daily = await _rule("ai.daily_limit", 2000)
    if daily > 0:
        used_d = 0
        async for r in db.ai_usage.find({"at": {"$gte": (now - timedelta(days=1)).isoformat()}},
                                        {"_id": 0, "units": 1}):
            used_d += int(r.get("units") or 1)
        if used_d + units > daily:
            raise HTTPException(status_code=429, detail="Günlük yapay zekâ bütçesi doldu; yönetici İşletme Kuralları'ndan artırabilir.")
    await db.ai_usage.insert_one({"user_id": uid, "email": (user or {}).get("email"), "op": op,
                                  "units": units, "at": now.isoformat()})


def _uid_from_request(request) -> str:
    try:
        auth = request.headers.get("authorization") or ""
        if auth[:7].lower() == "bearer ":
            from routes.deps import _decode_jwt_strict
            return str(_decode_jwt_strict(auth[7:].strip()).get("user_id") or "anon")
    except Exception:
        pass
    return "anon"


def ai_slot(op: str):
    """FastAPI bağımlılığı: kişi başı EŞZAMANLI yapay zekâ isteği tavanı (istek bitince bırakılır).
    Kullanım: dependencies=[Depends(ai_slot("…"))] ya da parametre olarak."""
    async def _dep(request: Request):
        uid = _uid_from_request(request)
        conc = await _rule("ai.user_concurrency", 2)
        if conc > 0 and _ACTIVE.get(uid, 0) >= conc:
            raise HTTPException(status_code=429, detail="Yapay zekâ isteğiniz zaten işleniyor; lütfen bitmesini bekleyin.")
        _ACTIVE[uid] = _ACTIVE.get(uid, 0) + 1
        try:
            yield
        finally:
            _ACTIVE[uid] = max(0, _ACTIVE.get(uid, 1) - 1)
    return _dep
