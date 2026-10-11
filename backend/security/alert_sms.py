"""Kritik güvenlik/hata uyarısında ANINDA SMS (sahip isteği, 2026-10-05: "hata, sızma tespit edildiğinde
SMS entegrasyonuyla o anki sorunla ilgili mesaj gönder").

- Yalnız `critical` düzeydeki ve kısılmamış (aynı parmak izi yakın zamanda gönderilmemiş) uyarılarda.
  Kaynaklar: güvenlik olayları (security_event: toplu kişisel veri çözme, sızma/tarama, vitrin kurcalama,
  CSP zayıflaması, denetim zinciri kırılması…), hata patlaması (security/monitoring.py), öz-test hatası,
  ödeme mutabakatı uyuşmazlığı, tatbikat.
- Numaralar: İşletme Kuralları `security.alert_sms_phones` (boşsa aktif süper adminlerin telefonu) — kodda
  numara YOK (beyaz etiket). Aç/kapa: `security.alert_sms_instant` (varsayılan açık).
- Maliyet/pompalama koruması: saatte en çok INSTANT_SMS_HOURLY_MAX, günde INSTANT_SMS_DAILY_MAX anlık SMS;
  ayrıca TÜM SMS'lerin geçtiği küresel bütçe kapısı (notification_service) "normal" öncelikle —
  yönetici giriş kodlarının yedek payı kullanılmaz.
- İçerik: mağaza adı + "GUVENLIK KRITIK" + uyarı başlığı + saat (TR). Başlıktaki olası e-posta/telefon
  maskelenir; gövde/meta (kişisel veri içerebilir) SMS'e KONMAZ. Asla istisna fırlatmaz.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

INSTANT_SMS_HOURLY_MAX = 5
INSTANT_SMS_DAILY_MAX = 20
_TR = timezone(timedelta(hours=3))
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+?90|0)?\s*5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")


def _scrub(s: str) -> str:
    s = _EMAIL_RE.sub("[e-posta]", str(s or ""))
    return _PHONE_RE.sub("[telefon]", s)


async def instant_critical_sms(alert_id: str, kind: str, title: str) -> dict:
    """Kritik uyarı için anlık SMS. Dönüş: {sent, attempted, reason?}. Asla fırlatmaz."""
    try:
        from routes.deps import db
        import notification_service as ns
        from business_rules import get_rule
        from security.alert_ack import _sms_phones

        if not bool(await get_rule(db, "security.alert_sms_instant", True)):
            return {"sent": 0, "attempted": 0, "reason": "disabled"}
        if not await _sms_phones():
            return {"sent": 0, "attempted": 0, "reason": "no_phone"}
        if not (await ns._get_providers_config(db)).get("sms_active"):
            return {"sent": 0, "attempted": 0, "reason": "sms_not_configured"}
        now = datetime.now(timezone.utc)
        h1 = (now - timedelta(hours=1)).isoformat()
        d1 = (now - timedelta(days=1)).isoformat()
        if await db.alerts.count_documents({"sms_instant.sent": {"$gt": 0}, "sms_instant.at": {"$gte": h1}}) \
                >= INSTANT_SMS_HOURLY_MAX:
            return {"sent": 0, "attempted": 0, "reason": "hourly_cap"}
        if await db.alerts.count_documents({"sms_instant.sent": {"$gt": 0}, "sms_instant.at": {"$gte": d1}}) \
                >= INSTANT_SMS_DAILY_MAX:
            return {"sent": 0, "attempted": 0, "reason": "daily_cap"}
        out = await send_security_sms(
            f"GUVENLIK KRITIK: {_scrub(title)[:110]} ({now.astimezone(_TR).strftime('%d.%m %H:%M')}). "
            f"Detay e-postada ve Guvenlik Paneli'nde.",
            event="security_alert_instant")
        out["at"] = now.isoformat()
        try:
            await db.alerts.update_one({"id": alert_id}, {"$set": {"sms_instant": out}})
        except Exception:
            pass
        # Panele girilene kadar dakikada bir tekrar (security.alert_paging).
        try:
            from security.alert_paging import start_or_extend
            await start_or_extend(alert_id, _scrub(title))
        except Exception:
            pass
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[guvenlik] anlık kritik SMS gönderilemedi: %s", type(e).__name__)
        return {"sent": 0, "attempted": 0, "reason": f"error:{type(e).__name__}"}


async def send_security_sms(text: str, *, event: str) -> dict:
    """Güvenlik SMS'i (mağaza adı öneki, ASCII, ≤300) — kural numaralarına, küresel SMS kapısından "normal"
    öncelikle. Dönüş: {sent, attempted, errors}. Asla fırlatmaz."""
    try:
        from routes.deps import db
        import notification_service as ns
        from security.alert_ack import _ascii, _sms_phones
        phones = await _sms_phones()
        cfg = await ns._get_providers_config(db)
        active = cfg.get("sms_active")
        if not phones or not active:
            return {"sent": 0, "attempted": 0, "reason": "no_phone" if not phones else "sms_not_configured"}
        store = ""
        try:
            from security.alerts import _company_cached
            store = str((await _company_cached() or {}).get("store_name") or "").strip()
        except Exception:
            pass
        msg = _ascii(f"{store + ' ' if store else ''}{_scrub(text)}")[:300]
        impl = ns.SMS_IMPL.get(active, ns._sms_generic)
        prov = (cfg.get("providers") or {}).get(active, {})
        sent, errors = 0, []
        with ns.sms_priority("normal"):
            for p in phones:
                try:
                    res = await impl(prov, p, msg)
                except Exception as e:  # noqa: BLE001
                    res = {"success": False, "response": type(e).__name__}
                await ns._log_event(db, event=event, channel="sms", to=p,
                                    status="success" if res.get("success") else "failed",
                                    response=str(res.get("response") or "")[:200])
                if res.get("success"):
                    sent += 1
                else:
                    errors.append(str(res.get("response") or "hata")[:80])
        return {"sent": sent, "attempted": len(phones), "errors": errors}
    except Exception as e:  # noqa: BLE001
        logger.warning("[guvenlik] güvenlik SMS'i gönderilemedi: %s", type(e).__name__)
        return {"sent": 0, "attempted": 0, "reason": f"error:{type(e).__name__}"}
