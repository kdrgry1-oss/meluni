"""
custom_theme.py — Özel Tema Kodu (CSS/JS) — Ticimax benzeri, GÜVENLİ.

GÜVENLİK MODELİ:
  - Özel CSS: mağaza <head>'ine <style> olarak enjekte edilir. Kod çalıştırmaz.
  - Özel JS: SADECE mağaza (storefront) tarafında, sayfa sonunda çalışır. Admin
    panelinde ASLA yüklenmez → enjekte edilen kod admin JWT/oturumuna erişemez
    (en kritik saldırı yüzeyi kapatılır).
  - Düzenleme require_admin ister; her kayıt updated_by + updated_at ile denetlenir.
  - Boyut limiti (kötüye kullanım / DoS'a karşı).
  - JS varsayılan KAPALI; bilinçli açılması gerekir.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from .deps import db, require_admin

admin_router = APIRouter(prefix="/admin/custom-theme", tags=["admin-custom-theme"])
public_router = APIRouter(prefix="/custom-theme", tags=["custom-theme"])

_MAX = 200_000  # ~200 KB üst sınır (css+js her biri)


@admin_router.get("")
async def get_custom_theme(current_user: dict = Depends(require_admin)):
    doc = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    return {
        "custom_css": doc.get("custom_css", ""),
        "custom_js": doc.get("custom_js", ""),
        "js_enabled": bool(doc.get("js_enabled", False)),
        "updated_by": doc.get("updated_by", ""),
        "updated_at": doc.get("updated_at", ""),
    }


@admin_router.put("")
async def save_custom_theme(payload: dict, current_user: dict = Depends(require_admin)):
    css = str(payload.get("custom_css") or "")
    js = str(payload.get("custom_js") or "")
    if len(css) > _MAX or len(js) > _MAX:
        raise HTTPException(status_code=400, detail=f"Kod çok büyük (azami {_MAX//1000} KB).")
    # GÜVENLİK: </script> ile erken kapanış (HTML kırma) engellenir; enjeksiyon anında da
    # kaçışlanır ama burada da temiz tutmak için basit koruma.
    js = js.replace("</script>", "<\\/script>")
    doc = {
        "id": "custom_theme",
        "custom_css": css,
        "custom_js": js,
        "js_enabled": bool(payload.get("js_enabled", False)),
        "updated_by": current_user.get("email") or current_user.get("id") or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.settings.update_one({"id": "custom_theme"}, {"$set": doc}, upsert=True)
    return {"success": True, "updated_at": doc["updated_at"]}


@public_router.get("")
async def public_custom_theme():
    """Mağaza tarafı okur. CSS her zaman; JS yalnız js_enabled ise döner."""
    doc = await db.settings.find_one({"id": "custom_theme"}, {"_id": 0}) or {}
    return {
        "custom_css": doc.get("custom_css", ""),
        "custom_js": doc.get("custom_js", "") if doc.get("js_enabled") else "",
        "js_enabled": bool(doc.get("js_enabled", False)),
    }
