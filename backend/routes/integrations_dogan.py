"""
=============================================================================
integrations_dogan.py — Doğan e-Dönüşüm entegrasyonu (extracted)
=============================================================================
Iter35 refactor adımı: integrations.py'den Doğan kısmı (4 endpoint, ~80 satır)
ayrı modüle taşındı. iyzico ve temu modülleri ile aynı pattern.

Endpoints:
- GET  /api/integrations/dogan/settings
- POST /api/integrations/dogan/settings
- POST /api/integrations/dogan/test-connection
- POST /api/integrations/dogan/check-user
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException

from .deps import db, require_admin

router = APIRouter(tags=["Integrations - Doğan"])


@router.get("/dogan/settings")
async def get_dogan_settings(current_user: dict = Depends(require_admin)):
    """Get Doğan e-Dönüşüm settings"""
    settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0})
    if not settings:
        return {"id": "dogan_edonusum", "enabled": False, "username": "", "password": "", "is_test": True}
    # Mask password — A3: ilk-3-karakter sızıntısı (password_masked) kaldırıldı, yalnız var/yok bilgisi.
    if settings.get("password"):
        settings["password"] = "********"
        settings["has_password"] = True
    return settings


@router.post("/dogan/settings")
async def save_dogan_settings(payload: dict, current_user: dict = Depends(require_admin)):
    """Save Doğan e-Dönüşüm settings"""
    # Required alan validasyonu — enabled=True ise username/password zorunlu
    if payload.get("enabled"):
        existing = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
        username = payload.get("username") or existing.get("username")
        password = payload.get("password")
        if password in (None, "", "********"):
            password = existing.get("password")
        if not username or not password:
            raise HTTPException(
                status_code=400,
                detail="Doğan e-Dönüşüm aktif etmek için username ve password zorunludur"
            )
    payload["id"] = "dogan_edonusum"
    # Maskeli değer gelirse mevcut password'ü koru
    if payload.get("password") in (None, "", "********"):
        payload.pop("password", None)
    else:
        # GÜVENLİK: yeni parolayı at-rest ŞİFRELE (DoganClient okurken çözülür).
        try:
            from security.crypto import encrypt as _enc_secret
            payload["password"] = _enc_secret(payload["password"])
        except Exception:
            pass
    await db.settings.update_one({"id": "dogan_edonusum"}, {"$set": payload}, upsert=True)
    return {"success": True, "message": "Doğan e-Dönüşüm ayarları kaydedildi"}


@router.post("/dogan/test-connection")
async def test_dogan_connection(current_user: dict = Depends(require_admin)):
    """Test connection to Doğan e-Dönüşüm"""
    from fastapi.concurrency import run_in_threadpool
    settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0})
    if not settings or not settings.get("username"):
        raise HTTPException(status_code=400, detail="Doğan e-Dönüşüm ayarları eksik")

    from dogan_client import DoganClient
    client = DoganClient(
        username=settings["username"],
        password=settings["password"],
        is_test=settings.get("is_test", True)
    )
    # Sync SOAP çağrısı event loop'u bloklamasın diye threadpool'da çalıştır
    result = await run_in_threadpool(client.test_connection)
    return result


@router.post("/dogan/check-user")
async def check_dogan_user(payload: dict, current_user: dict = Depends(require_admin)):
    """Check if a VKN is registered for e-Fatura"""
    vkn = payload.get("vkn", "")
    if not vkn:
        raise HTTPException(status_code=400, detail="VKN gerekli")

    settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0})
    if not settings or not settings.get("username"):
        raise HTTPException(status_code=400, detail="Doğan e-Dönüşüm ayarları eksik")

    from dogan_client import DoganClient
    client = DoganClient(
        username=settings["username"],
        password=settings["password"],
        is_test=settings.get("is_test", True)
    )
    result = client.check_user(vkn)
    return result
