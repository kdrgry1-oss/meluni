"""
=============================================================================
mobile.py — Mobil uygulama (Capacitor / React Native) için endpoint'ler
=============================================================================
Hedef: Admin panelinin Android & iOS native uygulamaya taşınmasında
gerekli sunucu tarafı altyapısı.

Endpoints:
- GET  /api/app/version-check  (public)
- POST /api/app/devices/register     (auth — push token + device info kaydet)
- DELETE /api/app/devices/{device_id}  (auth — logout / uninstall)
- GET  /api/app/devices/me          (auth — kullanıcının cihazları)
- GET  /api/app/config              (public — runtime config: feature flags, urls)
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

from .deps import (
    db, require_auth, get_current_user, generate_id,
    safe_str, client_ip_from_request,
)

router = APIRouter(prefix="/app", tags=["mobile-app"])


# Mevcut zorunlu app version'ları — admin panel'den güncellenebilir gelecekte
DEFAULT_APP_VERSION = {
    "ios": {
        "min_version": "1.0.0",
        "latest_version": "1.0.0",
        "force_update": False,
        "store_url": "https://apps.apple.com/app/facette/idTBD",
        "release_notes": "İlk sürüm",
    },
    "android": {
        "min_version": "1.0.0",
        "latest_version": "1.0.0",
        "force_update": False,
        "store_url": "https://play.google.com/store/apps/details?id=com.facette.app",
        "release_notes": "İlk sürüm",
    },
}


@router.get("/version-check")
async def version_check(platform: str = "ios", current_version: str = "0.0.0"):
    """Mobil uygulamanın açılışta çağıracağı versiyon kontrol endpoint'i.

    Force-update gerektiriyorsa client store_url'ye yönlendirir.
    """
    if platform not in ("ios", "android"):
        raise HTTPException(status_code=400, detail="platform 'ios' veya 'android' olmalı")

    cfg = await db.settings.find_one({"id": "app_versions"}, {"_id": 0}) or {"versions": DEFAULT_APP_VERSION}
    plat = (cfg.get("versions") or DEFAULT_APP_VERSION).get(platform, DEFAULT_APP_VERSION[platform])

    def _to_tuple(v: str) -> tuple:
        try:
            return tuple(int(x) for x in (v or "0.0.0").split("."))
        except Exception:
            return (0, 0, 0)

    cur_t = _to_tuple(current_version)
    min_t = _to_tuple(plat.get("min_version", "0.0.0"))
    latest_t = _to_tuple(plat.get("latest_version", "0.0.0"))

    needs_force = bool(plat.get("force_update", False)) or cur_t < min_t
    has_update = cur_t < latest_t

    return {
        "platform": platform,
        "current_version": current_version,
        "latest_version": plat.get("latest_version"),
        "min_version": plat.get("min_version"),
        "force_update_required": needs_force,
        "update_available": has_update,
        "store_url": plat.get("store_url"),
        "release_notes": plat.get("release_notes", ""),
    }


# ---------------------------------------------------------------------------
# DEVICE REGISTRATION (push notification token storage)
# ---------------------------------------------------------------------------

class DeviceRegisterReq(BaseModel):
    platform: str = Field(..., pattern="^(ios|android|web)$")
    device_id: str  # client-generated UUID (persisted in keychain)
    push_token: Optional[str] = None  # FCM (android) / APNs (ios) token
    app_version: Optional[str] = None
    os_version: Optional[str] = None
    model: Optional[str] = None  # "iPhone 15 Pro" / "Pixel 8"
    locale: Optional[str] = "tr-TR"


@router.post("/devices/register")
async def register_device(req: DeviceRegisterReq, request: Request,
                          current_user: dict = Depends(require_auth)):
    """Mobile cihaz + push token kaydı. user_id ile bağlanır.
    Aynı device_id+user_id varsa upsert yapılır.
    """
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "user_id": current_user["id"],
        "device_id": safe_str(req.device_id, 128),
        "platform": req.platform,
        "push_token": safe_str(req.push_token or "", 500),
        "app_version": safe_str(req.app_version or "", 32),
        "os_version": safe_str(req.os_version or "", 32),
        "model": safe_str(req.model or "", 64),
        "locale": safe_str(req.locale or "tr-TR", 16),
        "ip": client_ip_from_request(request),
        "user_agent": (request.headers.get("user-agent") or "")[:200],
        "last_seen_at": now,
        "is_active": True,
    }
    res = await db.user_devices.update_one(
        {"user_id": current_user["id"], "device_id": doc["device_id"]},
        {
            "$set": doc,
            "$setOnInsert": {"id": generate_id(), "created_at": now},
        },
        upsert=True,
    )
    return {
        "success": True,
        "device_id": doc["device_id"],
        "is_new": res.upserted_id is not None,
    }


@router.delete("/devices/{device_id}")
async def unregister_device(device_id: str,
                            current_user: dict = Depends(require_auth)):
    """Çıkış / uninstall durumunda push gönderilmesini durdur."""
    res = await db.user_devices.update_one(
        {"user_id": current_user["id"], "device_id": device_id},
        {"$set": {"is_active": False,
                  "deactivated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": True, "matched": res.matched_count}


@router.get("/devices/me")
async def my_devices(current_user: dict = Depends(require_auth)):
    """Kullanıcının kayıtlı cihazları (Apple gibi 'Cihazlarım' ekranı için)."""
    cur = db.user_devices.find(
        {"user_id": current_user["id"], "is_active": True},
        {"_id": 0, "push_token": 0}  # push_token sızdırma
    ).sort("last_seen_at", -1)
    items = await cur.to_list(50)
    return {"items": items}


# ---------------------------------------------------------------------------
# RUNTIME CONFIG — uygulama açılışta feature flags + URL'leri çeker
# ---------------------------------------------------------------------------

@router.get("/config")
async def app_config():
    """Mobile app açılışında çağrılır — feature flags, banner URL'leri,
    canlı destek butonu vb. uzaktan kontrol için."""
    cfg = await db.settings.find_one({"id": "app_config"}, {"_id": 0}) or {}
    return {
        "feature_flags": cfg.get("feature_flags") or {
            "live_support": True,
            "social_login_apple": False,
            "social_login_facebook": False,
            "social_login_google": True,
            "biometric_login": True,
            "instagram_shop": True,
            "live_stream_shop": False,
        },
        "branding": cfg.get("branding") or {
            "primary_color": "#000000",
            "logo_url": "",
            "splash_image_url": "",
        },
        "support": cfg.get("support") or {
            "whatsapp": "",
            "phone": "",
            "email": "destek@facette.com.tr",
        },
        "deep_link_scheme": "facette",  # facette://order/123
        "min_supported_versions": {"ios": "1.0.0", "android": "1.0.0"},
    }
