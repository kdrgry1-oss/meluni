"""Kritik uyarıda TEKRARLAYAN SMS çağrısı (sahip isteği, 2026-10-05: "SMS'ten 1 dk sonra panele girmemişsem
girene kadar 1 dk arayla tekrar SMS yollasın"; "kdrgry1342@gmail.com admin olarak panele girmeli").

Akış:
- Kritik uyarı anlık SMS'i gidince (security.alert_sms) bir "çağrı" (page) başlar/güncellenir — aynı anda
  TEK etkin çağrı vardır; yeni kritik uyarılar ona eklenir (SMS sağanağı yok).
- Zamanlayıcı her dakika (security_alert_paging) bakar: çağrı başladıktan sonra yetkili kişi PANELE
  GİRDİYSE (personel girişi ya da panelin açılışında çağrılan /api/admin/me/permissions) ya da uyarıları
  onayladıysa çağrı kapanır. Girmediyse `security.alert_page_interval_sec` (varsayılan 60 sn) arayla
  tekrar SMS gider.
- Yetkili kişi: İşletme Kuralı `security.alert_page_stop_emails` (virgüllü e-postalar; kodda e-posta YOK).
  Boşsa herhangi bir aktif SÜPER ADMIN. Yalnız PERSONEL hesabı (is_admin) sayılır — müşteri hesabıyla
  vitrine girmek çağrıyı durdurmaz.
- Maliyet/pompalama sınırları: ilk ESCALATE_AFTER tekrardan (3 saat) sonra aralık SLOW_INTERVAL_SEC
  (10 dk) olur; günde en çok DAILY_PAGE_SMS_MAX tekrar SMS'i; TÜM SMS'lerin geçtiği küresel bütçe kapısı
  "normal" öncelikle (müşteri SMS'lerini ve yönetici giriş kodlarının yedek payını tüketmesin diye).
İçerikte kişisel veri yok (başlık maskelenir). Asla istisna fırlatmaz.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_COLL = "security_pages"
_ID = "active"
ESCALATE_AFTER = 180          # 1 dk aralıkla en çok bu kadar tekrar (≈ 3 saat)…
SLOW_INTERVAL_SEC = 600       # …sonra 10 dk aralık (girene kadar)
DAILY_PAGE_SMS_MAX = 300      # günlük tekrar SMS tavanı (küresel SMS bütçesini korur)
_IDX = {"done": False}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(v):
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _db():
    from routes.deps import db
    return db


async def _stop_emails(db) -> set:
    from business_rules import get_rule
    raw = str(await get_rule(db, "security.alert_page_stop_emails", "") or "")
    return {x.strip().lower() for x in raw.replace(";", ",").split(",") if x.strip()}


async def _counts_as_responder(db, user: dict) -> bool:
    if not user or not (user.get("is_admin") is True or user.get("is_super_admin") is True):
        return False
    if user.get("is_active") is False:
        return False
    emails = await _stop_emails(db)
    if emails:
        return str(user.get("email") or "").strip().lower() in emails
    try:
        from routes.deps import get_effective_permissions
        return user.get("is_super_admin") is True or "*" in (await get_effective_permissions(user))
    except Exception:
        return user.get("is_super_admin") is True


async def start_or_extend(alert_id: str, title: str) -> None:
    """Kritik uyarı anlık SMS'i gönderildiğinde çağrılır."""
    try:
        db = await _db()
        now = _iso(_now())
        await db[_COLL].update_one(
            {"id": _ID, "status": "open"},
            {"$addToSet": {"alert_ids": alert_id},
             "$set": {"last_title": str(title or "")[:160], "updated_at": now},
             "$setOnInsert": {"id": _ID, "status": "open", "started_at": now, "last_sms_at": now,
                              "repeats": 0}},
            upsert=True)
    except Exception as e:  # noqa: BLE001 — aynı anda iki başlatma (unique) → mevcut çağrıya eklenir
        try:
            db = await _db()
            await db[_COLL].update_one({"id": _ID, "status": "open"},
                                       {"$addToSet": {"alert_ids": alert_id},
                                        "$set": {"last_title": str(title or "")[:160]}})
        except Exception:
            logger.warning("[guvenlik] çağrı başlatılamadı: %s", type(e).__name__)


async def note_panel_presence(user: dict) -> None:
    """Personel girişi / panel açılışı: yetkili kişiyse etkin çağrıyı kapatır. Ucuz: çağrı yoksa tek okuma."""
    try:
        db = await _db()
        page = await db[_COLL].find_one({"id": _ID, "status": "open"}, {"_id": 0, "started_at": 1})
        if not page or not await _counts_as_responder(db, user):
            return
        now = _iso(_now())
        res = await db[_COLL].update_one(
            {"id": _ID, "status": "open"},
            {"$set": {"status": "closed", "closed_at": now, "closed_by": str(user.get("email") or "")[:120],
                      "closed_reason": "panel_girisi", "id": f"closed:{now}"}})
        if getattr(res, "modified_count", 0):
            logger.warning("[guvenlik] kritik uyarı çağrısı kapandı: yetkili panele girdi")
    except Exception as e:  # noqa: BLE001
        logger.warning("[guvenlik] panel varlığı işlenemedi: %s", type(e).__name__)


async def _all_acked(db, ids) -> bool:
    if not ids:
        return False
    n_open = await db.alerts.count_documents({"id": {"$in": list(ids)}, "acked_at": {"$in": [None, ""]}})
    return n_open == 0


async def paging_tick() -> dict:
    """ZAMANLAYICI İŞİ (her dakika). Dönüş: {state, sent?}. Asla fırlatmaz."""
    try:
        db = await _db()
        if not _IDX["done"]:
            _IDX["done"] = True
            try:
                await db.security_page_sms.create_index("expire_at", expireAfterSeconds=0)
            except Exception:
                pass
        page = await db[_COLL].find_one({"id": _ID, "status": "open"}, {"_id": 0})
        if not page:
            return {"state": "idle"}
        now = _now()
        from business_rules import get_rule as _gr
        # Sahip isteği (2026-10-05): dakikalık tekrar ŞİMDİLİK KAPALI — kural açılana kadar açık çağrı
        # sessizce kapatılır (yalnız ilk anlık SMS gider).
        if not bool(await _gr(db, "security.alert_page_enabled", False)):
            await db[_COLL].update_one({"id": _ID, "status": "open"},
                                       {"$set": {"status": "closed", "closed_at": _iso(now),
                                                 "closed_reason": "tekrar_kapali", "id": f"closed:{_iso(now)}"}})
            return {"state": "disabled"}
        if await _all_acked(db, page.get("alert_ids") or []):
            await db[_COLL].update_one({"id": _ID, "status": "open"},
                                       {"$set": {"status": "closed", "closed_at": _iso(now),
                                                 "closed_reason": "onaylandi", "id": f"closed:{_iso(now)}"}})
            return {"state": "closed_acked"}
        from business_rules import get_rule
        try:
            interval = max(60, int(float(await get_rule(db, "security.alert_page_interval_sec", 60) or 60)))
        except (TypeError, ValueError):
            interval = 60
        repeats = int(page.get("repeats") or 0)
        if repeats >= ESCALATE_AFTER:
            interval = max(interval, SLOW_INTERVAL_SEC)
        last = _parse(page.get("last_sms_at")) or _parse(page.get("started_at")) or now
        if (now - last).total_seconds() < interval - 5:
            return {"state": "waiting"}
        d1 = _iso(now - timedelta(days=1))
        used = await db.security_page_sms.count_documents({"at": {"$gte": d1}})
        if used >= DAILY_PAGE_SMS_MAX:
            return {"state": "daily_cap"}
        from security.alert_sms import send_security_sms
        n = repeats + 1
        started = _parse(page.get("started_at")) or now
        mins = max(1, int((now - started).total_seconds() // 60))
        cnt = len(page.get("alert_ids") or [])
        body = (f"TEKRAR {n}: KRITIK uyari{' (' + str(cnt) + ' adet)' if cnt > 1 else ''} {mins} dk'dir "
                f"yanitsiz: {page.get('last_title') or ''}. Panele giris yapin.")
        res = await send_security_sms(body, event="security_alert_page")
        await db[_COLL].update_one({"id": _ID, "status": "open"},
                                   {"$set": {"last_sms_at": _iso(now), "last_result": res}, "$inc": {"repeats": 1}})
        if res.get("sent"):
            await db.security_page_sms.insert_one({"at": _iso(now), "n": n, "sent": res.get("sent"),
                                                   "expire_at": now + timedelta(days=3)})
        return {"state": "sent", "repeat": n, "sent": res.get("sent", 0)}
    except Exception as e:  # noqa: BLE001
        logger.warning("[guvenlik] çağrı işi hatası: %s", type(e).__name__)
        return {"state": "error", "error": type(e).__name__}
