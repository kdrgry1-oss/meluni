"""
=============================================================================
admin_mobile.py — Admin tarafında mobil uygulama yönetimi
=============================================================================
- App version'ları (ios/android) güncelle
- Feature flags güncelle
- Push notification gönder (FCM/APNs)
- Cihazları listele
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
import os
import httpx

from .deps import db, require_admin

router = APIRouter(prefix="/admin/mobile", tags=["admin-mobile"])


@router.get("/versions")
async def get_versions(current_user: dict = Depends(require_admin)):
    cfg = await db.settings.find_one({"id": "app_versions"}, {"_id": 0})
    return cfg or {"id": "app_versions", "versions": {
        "ios": {"min_version": "1.0.0", "latest_version": "1.0.0", "force_update": False, "store_url": "", "release_notes": ""},
        "android": {"min_version": "1.0.0", "latest_version": "1.0.0", "force_update": False, "store_url": "", "release_notes": ""},
    }}


class VersionUpdateReq(BaseModel):
    versions: dict


@router.post("/versions")
async def update_versions(req: VersionUpdateReq, current_user: dict = Depends(require_admin)):
    await db.settings.update_one(
        {"id": "app_versions"},
        {"$set": {"versions": req.versions, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": current_user.get("email")}},
        upsert=True,
    )
    return {"success": True}


@router.get("/config")
async def get_app_config(current_user: dict = Depends(require_admin)):
    cfg = await db.settings.find_one({"id": "app_config"}, {"_id": 0})
    return cfg or {"id": "app_config"}


@router.post("/config")
async def update_app_config(payload: dict, current_user: dict = Depends(require_admin)):
    await db.settings.update_one(
        {"id": "app_config"},
        {"$set": {**(payload or {}), "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": current_user.get("email")}},
        upsert=True,
    )
    return {"success": True}


@router.get("/devices")
async def list_devices(platform: Optional[str] = None, limit: int = 100,
                       current_user: dict = Depends(require_admin)):
    q = {"is_active": True}
    if platform:
        q["platform"] = platform
    total = await db.user_devices.count_documents(q)
    items = await db.user_devices.find(q, {"_id": 0, "push_token": 0}) \
        .sort("last_seen_at", -1).limit(limit).to_list(limit)
    # platform breakdown
    pipeline = [
        {"$match": {"is_active": True}},
        {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
    ]
    breakdown = {r["_id"]: r["count"] async for r in db.user_devices.aggregate(pipeline)}
    return {"total": total, "items": items, "by_platform": breakdown}


# ---------------------------------------------------------------------------
# PUSH NOTIFICATION SENDER (FCM HTTP v1 API)
# ---------------------------------------------------------------------------

class PushSendReq(BaseModel):
    target: str = Field("all", pattern="^(all|user|device|platform)$")
    target_value: Optional[str] = None  # user_id / device_id / 'ios'/'android'
    title: str
    body: str
    data: Optional[dict] = None  # custom payload (deep link vb.)
    image_url: Optional[str] = None


@router.post("/push/send")
async def send_push(req: PushSendReq, current_user: dict = Depends(require_admin)):
    """Push notification gönder. FCM_SERVER_KEY env'de yoksa mock mode'da
    kuyruklamayı simüle eder (gönderim logu integration_logs koleksiyonunda).
    """
    fcm_key = os.environ.get("FCM_SERVER_KEY", "").strip()

    # Hedef cihazları topla
    q = {"is_active": True, "push_token": {"$nin": [None, ""]}}
    if req.target == "user" and req.target_value:
        q["user_id"] = req.target_value
    elif req.target == "device" and req.target_value:
        q["device_id"] = req.target_value
    elif req.target == "platform" and req.target_value in ("ios", "android"):
        q["platform"] = req.target_value
    devices = await db.user_devices.find(q, {"_id": 0}).to_list(5000)

    if not devices:
        return {"success": False, "sent": 0, "failed": 0, "message": "Hedef cihaz bulunamadı"}

    sent = 0
    failed = 0
    errors = []

    if not fcm_key:
        # Mock — sadece kuyruklamayı simüle et + log
        for d in devices:
            await db.push_notifications_log.insert_one({
                "to_user": d["user_id"],
                "to_device": d["device_id"],
                "platform": d.get("platform"),
                "title": req.title,
                "body": req.body,
                "data": req.data or {},
                "status": "queued_no_fcm_key",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_by": current_user.get("email"),
            })
        return {
            "success": True,
            "sent": 0,
            "failed": 0,
            "queued": len(devices),
            "message": f"FCM_SERVER_KEY tanımlı değil — {len(devices)} cihaz için bildirim kuyruklandı (mock).",
        }

    # FCM legacy HTTP API (key:value JSON)
    async with httpx.AsyncClient(timeout=15) as client:
        for d in devices:
            try:
                payload = {
                    "to": d["push_token"],
                    "notification": {
                        "title": req.title,
                        "body": req.body,
                        **({"image": req.image_url} if req.image_url else {}),
                    },
                    "data": req.data or {},
                    "priority": "high",
                }
                resp = await client.post(
                    "https://fcm.googleapis.com/fcm/send",
                    headers={"Authorization": f"key={fcm_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                ok = resp.status_code == 200 and resp.json().get("success") == 1
                if ok:
                    sent += 1
                else:
                    failed += 1
                    errors.append(f"{d['device_id']}: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                failed += 1
                errors.append(f"{d['device_id']}: {e}")

    await db.push_notifications_log.insert_one({
        "title": req.title,
        "body": req.body,
        "target": req.target,
        "target_value": req.target_value,
        "sent": sent,
        "failed": failed,
        "errors": errors[:5],
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "sent_by": current_user.get("email"),
    })

    return {"success": True, "sent": sent, "failed": failed,
            "total_targets": len(devices), "errors": errors[:5]}
