"""
Banner routes - CRUD
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from datetime import datetime, timezone

from .deps import db, logger, require_admin, generate_id

router = APIRouter(prefix="/banners", tags=["Banners"])

@router.get("")
async def get_banners(
    position: Optional[str] = None,
    is_active: Optional[bool] = None
):
    """Get banners"""
    query = {}
    if position:
        query["position"] = position
    if is_active is not None:
        query["is_active"] = is_active
    
    banners = await db.banners.find(query, {"_id": 0}).sort("sort_order", 1).to_list(50)
    return banners

@router.get("/{banner_id}")
async def get_banner(banner_id: str):
    """Get single banner"""
    banner = await db.banners.find_one({"id": banner_id}, {"_id": 0})
    if not banner:
        raise HTTPException(status_code=404, detail="Banner bulunamadı")
    return banner

@router.post("")
async def create_banner(
    banner_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Create banner (admin only)"""
    # DENETİM FIX (#4): Frontend `image_url/video_url/link_url/device` gönderiyor; eski kod
    # yalnız `image/mobile_image/link` okuyordu → görsel/video/cihaz sessizce KAYBOLUYORDU.
    # Artık her iki adlandırma da kabul edilir ve tek dokümanda tutulur (legacy `image`/`link`
    # de yansıtılır ki storefront'un iki yönü de okuyabilsin).
    _image = banner_data.get("image_url") or banner_data.get("image") or ""
    _link = banner_data.get("link_url") or banner_data.get("link") or "/"
    banner = {
        "id": generate_id(),
        "title": banner_data.get("title", ""),
        "subtitle": banner_data.get("subtitle", ""),
        "image": _image,
        "image_url": _image,
        "mobile_image": banner_data.get("mobile_image", ""),
        "video_url": banner_data.get("video_url", ""),
        "link": _link,
        "link_url": _link,
        "device": banner_data.get("device", "all"),
        "position": banner_data.get("position", "home"),
        "sort_order": banner_data.get("sort_order", 0),
        "is_active": banner_data.get("is_active", True),
        "start_date": banner_data.get("start_date"),
        "end_date": banner_data.get("end_date"),
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    await db.banners.insert_one(banner)
    return {"id": banner["id"], "message": "Banner oluşturuldu"}

@router.put("/{banner_id}")
async def update_banner(
    banner_id: str,
    banner_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Update banner (admin only)"""
    existing = await db.banners.find_one({"id": banner_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Banner bulunamadı")
    
    # DENETİM FIX: filtresiz $set şema kaymasına/alan enjeksiyonuna açıktı. Değişmez alanları
    # (id/_id/created_at) update dışında tut.
    for _k in ("id", "_id", "created_at"):
        banner_data.pop(_k, None)
    # DENETİM FIX (#4): image_url↔image ve link_url↔link ikili adlandırmayı senkron tut.
    if "image_url" in banner_data:
        banner_data["image"] = banner_data["image_url"]
    elif "image" in banner_data:
        banner_data["image_url"] = banner_data["image"]
    if "link_url" in banner_data:
        banner_data["link"] = banner_data["link_url"]
    elif "link" in banner_data:
        banner_data["link_url"] = banner_data["link"]
    banner_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.banners.update_one({"id": banner_id}, {"$set": banner_data})
    return {"message": "Banner güncellendi"}

@router.delete("/{banner_id}")
async def delete_banner(
    banner_id: str,
    current_user: dict = Depends(require_admin)
):
    """Delete banner (admin only)"""
    result = await db.banners.delete_one({"id": banner_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Banner bulunamadı")
    return {"message": "Banner silindi"}
