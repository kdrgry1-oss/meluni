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
from fastapi import APIRouter, Depends
from datetime import datetime, timezone

from .deps import db, logger, require_admin

router = APIRouter(prefix="/push", tags=["push"])


async def _fcm_config():
    """FCM v1 için (project_id, service_account_info) döndürür; yoksa (None, None)."""
    project = os.environ.get("FCM_PROJECT_ID", "").strip()
    sa = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not project or not sa:
        cfg = await db.settings.find_one({"id": "push"}, {"_id": 0}) or {}
        project = project or str(cfg.get("fcm_project_id") or "").strip()
        sa = sa or cfg.get("fcm_service_account")
    return (project or None), (sa or None)


async def send_push_to_admins(title: str, body: str, data: dict = None) -> int:
    """Kayıtlı TÜM aktif admin cihazlarına (db.user_devices) modern FCM v1 ile push.
    Best-effort: yapılandırma/hata yoksa 0 döner, ASLA exception fırlatmaz."""
    try:
        project, sa = await _fcm_config()
        if not project or not sa:
            logger.info("push: FCM v1 yapılandırılmamış — gönderim atlandı")
            return 0
        # GÜVENLİK: "admin" bildirimi (sipariş PII içerebilir) YALNIZCA admin
        # kullanıcıların cihazlarına gitmeli. user_devices müşteri cihazlarını da
        # içerdiğinden, önce admin user_id kümesini çıkar ve cihazları ona göre filtrele.
        admin_ids = [u["id"] async for u in db.users.find(
            {"is_admin": True}, {"_id": 0, "id": 1}) if u.get("id")]
        if not admin_ids:
            return 0
        devices = await db.user_devices.find(
            {"is_active": True, "push_token": {"$nin": [None, ""]}, "user_id": {"$in": admin_ids}},
            {"_id": 0, "push_token": 1, "device_id": 1}).to_list(5000)
        tokens = [d["push_token"] for d in devices if d.get("push_token")]
        if not tokens:
            return 0

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
        _data = {str(k): str(v) for k, v in (data or {}).items()}
        sent, stale = 0, []
        async with httpx.AsyncClient(timeout=15) as c:
            for t in tokens:
                msg = {"message": {
                    "token": t,
                    "notification": {"title": title, "body": body},
                    "data": _data,
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
        if stale:
            try:
                await db.user_devices.update_many(
                    {"push_token": {"$in": stale}}, {"$set": {"is_active": False}})
            except Exception:
                pass
        try:
            await db.push_notifications_log.insert_one({
                "title": title, "body": body, "data": _data,
                "sent": sent, "targets": len(tokens), "via": "fcm_v1",
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        return sent
    except ModuleNotFoundError as e:
        logger.warning(f"push: FCM bağımlılığı eksik ({e})")
        return 0
    except Exception as e:
        logger.warning(f"push: FCM v1 gönderim hatası ({e})")
        return 0


@router.post("/test")
async def push_test(current_user: dict = Depends(require_admin)):
    """Yönetici: tüm kayıtlı admin cihazlarına modern FCM v1 test bildirimi."""
    n = await send_push_to_admins("Test Bildirimi",
                                  "Facette admin push (FCM v1) çalışıyor ✅",
                                  {"type": "test"})
    return {"success": True, "sent": n}
