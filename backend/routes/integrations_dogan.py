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
from fastapi import APIRouter, Depends, HTTPException, Request

from .deps import db, require_admin, require_permission
from activity_audit import record_admin_audit

router = APIRouter(tags=["Integrations - Doğan"])


@router.get("/dogan/settings")
async def get_dogan_settings(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Get Doğan e-Dönüşüm settings"""
    settings = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0})
    if not settings:
        return {"id": "dogan_edonusum", "enabled": False, "username": "", "password": "", "is_test": True}
    # Mask password — A3: ilk-3-karakter sızıntısı (password_masked) kaldırıldı, yalnız var/yok bilgisi.
    if settings.get("password"):
        settings["password"] = "********"
        settings["has_password"] = True
    return settings


# GÜVENLİK (denetim 2026-10): yalnız kodun GERÇEKTEN okuduğu ayar anahtarları yazılabilir
# (eskiden gövdenin tamamı $set ile yazılıyordu → keyfi alan enjeksiyonu). Yeni bir Doğan
# ayarı eklenirse buraya da eklenmeli.
_DOGAN_ALLOWED_KEYS = {
    "enabled", "username", "password", "is_test",
    "vkn", "supplier_name", "supplier_district", "supplier_city", "supplier_street",
    "supplier_tax_office", "supplier_phone", "supplier_email", "supplier_website",
    "earchive_prefix", "einvoice_prefix",
    "earchive_start_year", "earchive_start_number", "einvoice_start_year", "einvoice_start_number",
    "einvoice_profile", "sender_alias", "sender_unit_alias",
    "earsiv_link_template", "einvoice_link_template",
}
_DOGAN_BOOL_KEYS = {"enabled", "is_test"}


@router.post("/dogan/settings")
async def save_dogan_settings(payload: dict, request: Request,
                              current_user: dict = Depends(require_permission("integrations.dogan_edonusum"))):
    """Save Doğan e-Dönüşüm settings (yetki: integrations.dogan_edonusum; alan beyaz listesi; denetim kaydı)."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Geçersiz istek gövdesi")
    # Beyaz liste dışı alanlar YAZILMAZ (GET yanıtını geri gönderen istemci kırılmasın diye
    # 400 yerine sessizce atlanır; adları denetim kaydına düşer).
    unknown = sorted(str(k)[:60] for k in payload.keys() if k not in _DOGAN_ALLOWED_KEYS and k not in ("id", "has_password"))
    clean = {}
    for k in _DOGAN_ALLOWED_KEYS:
        if k not in payload:
            continue
        v = payload.get(k)
        if k in _DOGAN_BOOL_KEYS:
            clean[k] = bool(v)
        elif v is None or isinstance(v, (str, int, float)) and not isinstance(v, bool):
            clean[k] = (v.strip()[:500] if isinstance(v, str) else v)
        else:
            raise HTTPException(status_code=400, detail=f"Geçersiz değer: {k}")
    existing = await db.settings.find_one({"id": "dogan_edonusum"}, {"_id": 0}) or {}
    # Required alan validasyonu — enabled=True ise username/password zorunlu
    if clean.get("enabled"):
        username = clean.get("username") or existing.get("username")
        password = clean.get("password")
        if password in (None, "", "********"):
            password = existing.get("password")
        if not username or not password:
            raise HTTPException(
                status_code=400,
                detail="Doğan e-Dönüşüm aktif etmek için username ve password zorunludur"
            )
    clean["id"] = "dogan_edonusum"
    # Maskeli değer gelirse mevcut password'ü koru
    credential_rotated = False
    if clean.get("password") in (None, "", "********"):
        clean.pop("password", None)
    else:
        credential_rotated = True
        # GÜVENLİK: yeni parolayı at-rest ŞİFRELE (DoganClient okurken çözülür).
        try:
            from security.crypto import encrypt as _enc_secret
            clean["password"] = _enc_secret(clean["password"])
        except Exception:
            pass
    await db.settings.update_one({"id": "dogan_edonusum"}, {"$set": clean}, upsert=True)
    # Denetim: parola ASLA yazılmaz (yalnız 'değişti mi' bilgisi).
    _before = {k: existing.get(k) for k in clean if k not in ("password", "id")}
    _after = {k: clean.get(k) for k in clean if k not in ("password", "id")}
    await record_admin_audit(
        db, action="integrations.dogan_settings_update", entity_type="settings", entity_id="dogan_edonusum",
        before=_before, after=_after, current_user=current_user, request=request,
        source="admin.integrations", metadata={"credential_rotated": credential_rotated, "ignored_keys": unknown[:20]})
    return {"success": True, "message": "Doğan e-Dönüşüm ayarları kaydedildi"}


@router.post("/dogan/test-connection")
async def test_dogan_connection(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
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
async def check_dogan_user(payload: dict, current_user: dict = Depends(require_permission("orders.view"))):
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
