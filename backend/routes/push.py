"""
push.py — MODERN FCM v1 push gönderici (yeni-sipariş vb. OTOMATİK bildirimler).
============================================================================
NOT: Cihaz KAYDI zaten routes/mobile.py `/app/devices/register` ile db.user_devices'a
yapılıyor; BURADA TEKRAR ETMEYİZ. admin_mobile.py'deki mevcut gönderici LEGACY FCM
(`fcm.googleapis.com/fcm/send` + `key=...`) kullanıyor — o API Google tarafından
Haziran 2024'te KAPATILDI. Bu modül modern **FCM HTTP v1** ile gönderir.

Yapılandırma (birini seç):
  • env  FCM_PROJECT_ID + FCM_SERVICE_ACCOUNT (service account JSON, tek satır)
  • ya da db.settings id="push": { fcm_project_id, fcm_service_account }
Yapılandırma YOKSA gönderim SESSİZCE atlanır → sipariş akışı ASLA bozulmaz.
"""
import os
import re
from fastapi import APIRouter, Depends
from datetime import datetime, timezone

from .deps import db, logger, require_admin, require_permission

router = APIRouter(prefix="/push", tags=["push"])


# ── ADM-15: PUSH İÇERİĞİNDE KİŞİSEL VERİ YOK ─────────────────────────────────────────────
# Push başlığı/gövdesi kilit ekranında görünür ve Google (FCM) / Apple (APNs) üzerinden geçer.
# Çağıran hangi metni verirse versin, gönderimden ÖNCE tek noktada süzülür:
#   • tür bazlı ŞABLON: sipariş/WhatsApp gibi müşteriye ait bildirimler sabit, PII'siz metinle
#     yeniden kurulur (müşteri adı / tutar / mesaj içeriği / telefon gitmez — ayrıntı panelde);
#   • genel süzgeç: e-posta, telefon, IBAN, TCKN/VKN biçimli değerler maskelenir;
#   • data alanında kişisel veri anahtarları (phone/email/name/address…) atılır.
_PII_DATA_KEYS = {"phone", "email", "name", "customer", "customer_name", "first_name", "last_name",
                  "full_name", "address", "tckn", "tax_number", "iban", "message", "body", "text",
                  # KVKK: adres/telefon taşıyabilen diğer anahtarlar
                  "adres", "shipping_address", "billing_address", "address_line", "gsm", "mobile",
                  "telefon", "customer_phone", "city_address", "street"}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN_RE = re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.I)
_PHONE_RE = re.compile(r"(?<![\w])(?:\+?90[\s-]?)?\(?0?5\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")
_LONG_DIGITS_RE = re.compile(r"(?<![\w])\d{10,11}(?!\d)")   # TCKN (11) / VKN (10) / çıplak telefon
_ITEMS_RE = re.compile(r"(\d+)\s*ürün")


def _scrub_text(v: str, keep: tuple = ()) -> str:
    """PII biçimli değerleri maskeler; `keep` içindeki değerler (sipariş no — pazaryeri sipariş
    numaraları 10-11 haneli olabilir) korunur."""
    s = str(v or "")
    holders = {}
    for i, k in enumerate(x for x in keep if x):
        ph = f"\x00{i}\x00"
        if k in s:
            s = s.replace(k, ph)
            holders[ph] = k
    s = _EMAIL_RE.sub("[e-posta]", s)
    s = _IBAN_RE.sub("[IBAN]", s)
    s = _PHONE_RE.sub("[telefon]", s)
    # KVKK: serbest metindeki adres kalıbı (Mah./Sok./Cad. …) ve kalan TR telefon biçimleri
    from security.contact_pii import scrub_contact_text as _sct
    s = _sct(s)
    s = _LONG_DIGITS_RE.sub("[numara]", s)
    for ph, k in holders.items():
        s = s.replace(ph, k)
    return s


def sanitize_admin_push(title: str, body: str, data: dict = None) -> tuple:
    """(title, body, data) — gönderilecek PII'siz içerik. Saf fonksiyon (test edilir)."""
    d = {str(k): str(v) for k, v in (data or {}).items() if str(k).lower() not in _PII_DATA_KEYS}
    kind = d.get("type") or ""
    onum = str(d.get("order_number") or "").strip()
    if kind == "new_order":
        _m = _ITEMS_RE.search(str(body or ""))
        title = "🛍️ Yeni Sipariş" + (" (ödendi)" if "ödendi" in str(title or "") else "")
        body = f"Yeni sipariş {onum}".strip() + (f" · {_m.group(1)} ürün" if _m else "")
    elif kind == "whatsapp_handoff":
        title = "📞 Yanıt bekleyen müşteri"
        body = "WhatsApp'ta yanıt bekleyen bir müşteri var — ayrıntı panelde."
    keep = tuple(x for x in (onum, str(d.get("order_id") or "")) if x)
    return (_scrub_text(title, keep)[:120], _scrub_text(body, keep)[:300],
            {k: (v if k in ("order_id", "order_number", "type") else _scrub_text(v, keep))[:120] for k, v in d.items()})


async def _fcm_config():
    """FCM v1 için (project_id, service_account_info) döndürür; yoksa (None, None)."""
    project = os.environ.get("FCM_PROJECT_ID", "").strip()
    sa = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not project or not sa:
        cfg = await db.settings.find_one({"id": "push"}, {"_id": 0}) or {}
        project = project or str(cfg.get("fcm_project_id") or "").strip()
        sa = sa or cfg.get("fcm_service_account")
    return (project or None), (sa or None)


def _log_entry(data: dict, sent: int, targets: int) -> dict:
    """push_notifications_log satırı — PII'siz: tür + sipariş no + sayaçlar."""
    d = data or {}
    return {
        "kind": str(d.get("type") or "generic")[:40],
        "order_number": str(d.get("order_number") or "")[:40],
        "sent": int(sent or 0), "targets": int(targets or 0), "via": "fcm_v1",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


async def send_push_to_admins(title: str, body: str, data: dict = None) -> int:
    """Kayıtlı TÜM aktif admin cihazlarına (db.user_devices) modern FCM v1 ile push.
    Best-effort: yapılandırma/hata yoksa 0 döner, ASLA exception fırlatmaz.
    İçerik gönderimden önce `sanitize_admin_push` ile kişisel veriden arındırılır (ADM-15)."""
    try:
        title, body, data = sanitize_admin_push(title, body, data)
        project, sa = await _fcm_config()
        if not project or not sa:
            logger.info("push: FCM v1 yapılandırılmamış — gönderim atlandı")
            return 0
        # GÜVENLİK: "admin" bildirimi (sipariş PII içerebilir) YALNIZCA admin
        # kullanıcıların cihazlarına gitmeli. user_devices müşteri cihazlarını da
        # içerdiğinden, önce admin user_id kümesini çıkar ve cihazları ona göre filtrele.
        # Denetim 2026-10 #7: yalnız AKTİF ve GERÇEK personel (require_admin ile aynı ölçüt:
        # is_admin + aktif + müşteri rolü değil + is_super_admin ya da atanmış role_id). Pasif
        # bırakılmış ya da is_admin bayrağı kalmış eski müşteri hesaplarına sipariş bildirimi gitmez.
        admin_ids = [u["id"] async for u in db.users.find(
            {"is_admin": True, "is_active": {"$ne": False}, "role": {"$ne": "customer"},
             "$or": [{"is_super_admin": True},
                     {"role_id": {"$exists": True, "$nin": [None, ""]}}]},
            {"_id": 0, "id": 1}) if u.get("id")]
        if not admin_ids:
            return 0
        devices = await db.user_devices.find(
            {"is_active": True, "push_token": {"$nin": [None, ""]}, "user_id": {"$in": admin_ids}},
            {"_id": 0, "push_token": 1, "device_id": 1}).to_list(5000)
        tokens = list(dict.fromkeys(d["push_token"] for d in devices if d.get("push_token")))
        # SHOP10: hesap değişimi — aynı cihaz/push token'ı personel çıkışından SONRA başka (müşteri)
        # hesabıyla kaydedildiyse token artık o hesabındır → yönetici bildirimi GÖNDERİLMEZ.
        # Token başına EN SON kayıt (last_seen_at) belirleyicidir.
        if tokens:
            _latest: dict = {}
            async for _d in db.user_devices.find({"push_token": {"$in": tokens}},
                                                 {"_id": 0, "push_token": 1, "user_id": 1, "last_seen_at": 1,
                                                  "is_active": 1}):
                _cur = _latest.get(_d.get("push_token"))
                if _cur is None or str(_d.get("last_seen_at") or "") > str(_cur.get("last_seen_at") or ""):
                    _latest[_d.get("push_token")] = _d
            _admins = set(admin_ids)
            tokens = [t for t in tokens if (_latest.get(t) or {}).get("user_id") in _admins
                      and (_latest.get(t) or {}).get("is_active") is not False]
        if not tokens:
            return 0
        _data = {str(k): str(v) for k, v in (data or {}).items()}
        sent, stale = await _fcm_send(project, sa, tokens, title, body, _data)
        if stale:
            try:
                await db.user_devices.update_many(
                    {"push_token": {"$in": stale}}, {"$set": {"is_active": False}})
            except Exception:
                pass
        try:
            # GÜVENLİK (ADM-15): log'a başlık/gövde METNİ yazılmaz (müşteri adı, ürün, tutar gibi PII
            # içerebilir) — yalnız tür + sipariş no (+ sayaçlar). İçerik gerekiyorsa sipariş panelden açılır.
            await db.push_notifications_log.insert_one(_log_entry(_data, sent, len(tokens)))
        except Exception:
            pass
        return sent
    except ModuleNotFoundError as e:
        logger.warning(f"push: FCM bağımlılığı eksik ({e})")
        return 0
    except Exception as e:
        logger.warning(f"push: FCM v1 gönderim hatası ({e})")
        return 0


async def _fcm_send(project: str, sa, tokens: list, title: str, body: str, data: dict) -> tuple:
    """FCM v1 gönderimi (her cihaz için ayrı mesaj). Dönüş: (gönderilen, bayat_tokenlar)."""
    import json as _json
    import httpx
    from google.oauth2 import service_account as _gsa
    from google.auth.transport.requests import Request as _GRequest

    info = _json.loads(sa) if isinstance(sa, str) else sa
    creds = _gsa.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    creds.refresh(_GRequest())
    access_token = creds.token
    url = f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"
    sent, stale = 0, []
    async with httpx.AsyncClient(timeout=15) as c:
        for t in tokens:
            msg = {"message": {
                "token": t,
                "notification": {"title": title, "body": body},
                "data": data,
                "apns": {"payload": {"aps": {"sound": "default", "badge": 1}}},
                "android": {"priority": "high", "notification": {"sound": "default"}},
            }}
            try:
                r = await c.post(url, headers={"Authorization": f"Bearer {access_token}"}, json=msg)
                if r.status_code == 200:
                    sent += 1
                elif r.status_code in (404, 410):
                    stale.append(t)
            except Exception:
                pass
    return sent, stale


@router.post("/test")
async def push_test(current_user: dict = Depends(require_permission("settings.edit"))):
    """Yönetici: tüm kayıtlı admin cihazlarına modern FCM v1 test bildirimi."""
    n = await send_push_to_admins("Test Bildirimi",
                                  "Meluni admin push (FCM v1) çalışıyor ✅",
                                  {"type": "test"})
    return {"success": True, "sent": n}
