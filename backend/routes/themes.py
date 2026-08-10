"""
Theme Management Routes
- Themes are pluggable storefront designs (e.g., Miu Miu clone, minimal, classic)
- Each theme has editable blocks (hero, card, product_scroller, newsletter, etc.)
- Admin can activate one theme; public storefront route reads active theme
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
from datetime import datetime, timezone

from .deps import db, logger, require_admin, generate_id

admin_router = APIRouter(prefix="/admin/themes", tags=["Themes Admin"])
public_router = APIRouter(prefix="/storefront/themes", tags=["Storefront Themes"])


# ----- Defaults / seed -----
def _miumiu_default_blocks():
    """Default block configuration for Miu Miu clone theme."""
    base = [
        {
            "type": "announcement_bar",
            "title": "ÜCRETSİZ KARGO · 500₺ ÜZERİ",
            "subtitle": "",
            "image": "",
            "mobile_image": "",
            "link_url": "",
            "link_label": "",
            "order": 0,
            "settings": {"bg": "#000000", "color": "#ffffff"},
        },
        {
            "type": "hero_fullscreen",
            "title": "Holiday",
            "subtitle": "Eclectic spirit, festive charm",
            "image": "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?auto=format&fit=crop&w=1900&q=80",
            "mobile_image": "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?auto=format&fit=crop&w=900&q=80",
            "link_url": "/tema/miumiu/kategori/canta",
            "link_label": "Shop",
            "order": 1,
            "settings": {"text_color": "#ffffff", "align": "center", "overlay": 0.35},
        },
        {
            "type": "editorial_card",
            "title": "L'Eté à la Plage",
            "subtitle": "New essentials for summer",
            "image": "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=1900&q=80",
            "mobile_image": "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=900&q=80",
            "link_url": "/tema/miumiu/kategori/yeni-sezon",
            "link_label": "Shop",
            "order": 2,
            "settings": {"text_color": "#ffffff", "align": "left", "overlay": 0.3},
        },
        {
            "type": "editorial_card",
            "title": "Bags",
            "subtitle": "Versatile lines in luminous hues",
            "image": "https://images.unsplash.com/photo-1584917865442-de89df76afd3?auto=format&fit=crop&w=1900&q=80",
            "mobile_image": "https://images.unsplash.com/photo-1584917865442-de89df76afd3?auto=format&fit=crop&w=900&q=80",
            "link_url": "/tema/miumiu/kategori/canta",
            "link_label": "Shop",
            "order": 3,
            "settings": {"text_color": "#ffffff", "align": "right", "overlay": 0.3},
        },
        {
            "type": "editorial_card",
            "title": "Shoes",
            "subtitle": "Textures and artisanal details",
            "image": "https://images.unsplash.com/photo-1543163521-1bf539c55dd2?auto=format&fit=crop&w=1900&q=80",
            "mobile_image": "https://images.unsplash.com/photo-1543163521-1bf539c55dd2?auto=format&fit=crop&w=900&q=80",
            "link_url": "/tema/miumiu/kategori/ayakkabi",
            "link_label": "Shop",
            "order": 4,
            "settings": {"text_color": "#ffffff", "align": "center", "overlay": 0.35},
        },
        {
            "type": "product_scroller",
            "title": "The new Miu Miu L'Eté collection",
            "subtitle": "",
            "image": "",
            "mobile_image": "",
            "link_url": "/tema/miumiu/kategori/yeni-sezon",
            "link_label": "Shop",
            "order": 5,
            "settings": {"category_slug": "yeni-sezon", "limit": 12},
        },
        {
            "type": "editorial_card",
            "title": "Eyewear",
            "subtitle": "Bold, distinctive, expressive",
            "image": "https://images.unsplash.com/photo-1572635196237-14b3f281503f?auto=format&fit=crop&w=1900&q=80",
            "mobile_image": "https://images.unsplash.com/photo-1572635196237-14b3f281503f?auto=format&fit=crop&w=900&q=80",
            "link_url": "/tema/miumiu/kategori/aksesuar",
            "link_label": "Shop",
            "order": 6,
            "settings": {"text_color": "#ffffff", "align": "left", "overlay": 0.3},
        },
        {
            "type": "newsletter",
            "title": "SUBSCRIBE TO OUR NEWSLETTER",
            "subtitle": "Be first to discover new collections and exclusive events.",
            "image": "",
            "mobile_image": "",
            "link_url": "",
            "link_label": "Subscribe",
            "order": 7,
            "settings": {"bg": "#000000", "color": "#ffffff"},
        },
    ]
    for b in base:
        b["id"] = generate_id()
        b["is_active"] = True
    return base


def _miumiu_default_menu():
    return [
        {"label": "HIGHLIGHTS", "url": "/tema/miumiu/kategori/yeni-sezon", "columns": []},
        {"label": "READY TO WEAR", "url": "/tema/miumiu/kategori/giyim", "columns": [
            {"title": "Categories", "links": [
                {"label": "Dresses", "url": "/tema/miumiu/kategori/elbise"},
                {"label": "Tops", "url": "/tema/miumiu/kategori/ust-giyim"},
                {"label": "Knitwear", "url": "/tema/miumiu/kategori/triko"},
                {"label": "Skirts", "url": "/tema/miumiu/kategori/etek"},
                {"label": "Trousers", "url": "/tema/miumiu/kategori/pantolon"},
            ]},
        ]},
        {"label": "BAGS", "url": "/tema/miumiu/kategori/canta", "columns": []},
        {"label": "SHOES", "url": "/tema/miumiu/kategori/ayakkabi", "columns": []},
        {"label": "ACCESSORIES", "url": "/tema/miumiu/kategori/aksesuar", "columns": []},
        {"label": "WALLETS", "url": "/tema/miumiu/kategori/cuzdan", "columns": []},
        {"label": "FASHION JEWELLERY", "url": "/tema/miumiu/kategori/taki", "columns": []},
        {"label": "GIFTS", "url": "/tema/miumiu/kategori/hediye", "columns": [], "accent": True},
    ]


def _build_default_theme():
    return {
        "id": generate_id(),
        "name": "Miu Miu Clone",
        "slug": "miumiu",
        "description": "Miu Miu birebir kopya — siyah-beyaz minimal, full-screen editorial bloklar, mega menü, scroll-to-explore davranışı.",
        "preview_image": "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?auto=format&fit=crop&w=600&q=70",
        "is_active": True,
        "is_default": True,
        "blocks": _miumiu_default_blocks(),
        "menu": _miumiu_default_menu(),
        "settings": {
            "brand_name": "miu miu",
            "brand_font": "italic-serif",  # logo style flag
            "primary": "#000000",
            "secondary": "#ffffff",
            "accent": "#c8b87a",
            "scroll_to_explore": True,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _ensure_seed():
    """Seed default Miu Miu theme if no themes exist."""
    cnt = await db.themes.count_documents({})
    if cnt == 0:
        theme = _build_default_theme()
        await db.themes.insert_one(theme)
        logger.info(f"Seeded default theme: {theme['slug']}")


# ----- Admin Routes -----
@admin_router.get("")
async def list_themes(current_user: dict = Depends(require_admin)):
    """List all themes."""
    await _ensure_seed()
    themes = await db.themes.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return {"items": themes, "total": len(themes)}


@admin_router.get("/{theme_id}")
async def get_theme(theme_id: str, current_user: dict = Depends(require_admin)):
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    return theme


@admin_router.post("")
async def create_theme(data: dict, current_user: dict = Depends(require_admin)):
    """Create a new theme (with empty or default blocks)."""
    theme = {
        "id": generate_id(),
        "name": data.get("name", "Yeni Tema"),
        "slug": data.get("slug", "yeni-tema"),
        "description": data.get("description", ""),
        "preview_image": data.get("preview_image", ""),
        "is_active": False,
        "is_default": False,
        "blocks": data.get("blocks", []),
        "menu": data.get("menu", []),
        "settings": data.get("settings", {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Ensure block ids
    for b in theme["blocks"]:
        if not b.get("id"):
            b["id"] = generate_id()
    # Slug uniqueness
    existing = await db.themes.find_one({"slug": theme["slug"]}, {"_id": 0})
    if existing:
        raise HTTPException(400, f"Slug zaten kullanılıyor: {theme['slug']}")
    await db.themes.insert_one(theme)
    return {k: v for k, v in theme.items() if k != "_id"}


@admin_router.put("/{theme_id}")
async def update_theme(theme_id: str, data: dict, current_user: dict = Depends(require_admin)):
    """Update theme fields and/or all blocks."""
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    update = {}
    for key in ("name", "slug", "description", "preview_image", "settings", "menu"):
        if key in data:
            update[key] = data[key]
    if "blocks" in data:
        blocks = data["blocks"] or []
        for b in blocks:
            if not b.get("id"):
                b["id"] = generate_id()
            if "is_active" not in b:
                b["is_active"] = True
        update["blocks"] = blocks
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.themes.update_one({"id": theme_id}, {"$set": update})
    return await db.themes.find_one({"id": theme_id}, {"_id": 0})


@admin_router.put("/{theme_id}/blocks/{block_id}")
async def update_block(theme_id: str, block_id: str, data: dict, current_user: dict = Depends(require_admin)):
    """Update a single block of a theme."""
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    blocks = theme.get("blocks", [])
    found = False
    for b in blocks:
        if b.get("id") == block_id:
            for key in ("type", "title", "subtitle", "image", "mobile_image", "link_url", "link_label", "order", "is_active", "settings"):
                if key in data:
                    b[key] = data[key]
            found = True
            break
    if not found:
        raise HTTPException(404, "Block bulunamadı")
    await db.themes.update_one({"id": theme_id}, {"$set": {"blocks": blocks, "updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True, "blocks": blocks}


@admin_router.post("/{theme_id}/activate")
async def activate_theme(theme_id: str, current_user: dict = Depends(require_admin)):
    """Set this theme active (deactivates others)."""
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    await db.themes.update_many({}, {"$set": {"is_active": False}})
    await db.themes.update_one({"id": theme_id}, {"$set": {"is_active": True, "updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True, "active_theme": theme_id}


@admin_router.delete("/{theme_id}")
async def delete_theme(theme_id: str, current_user: dict = Depends(require_admin)):
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    if theme.get("is_default"):
        raise HTTPException(400, "Varsayılan tema silinemez")
    await db.themes.delete_one({"id": theme_id})
    return {"ok": True}


@admin_router.post("/{theme_id}/reset")
async def reset_theme(theme_id: str, current_user: dict = Depends(require_admin)):
    """Reset theme blocks to factory defaults (only works for Miu Miu)."""
    theme = await db.themes.find_one({"id": theme_id}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    if theme.get("slug") != "miumiu":
        raise HTTPException(400, "Sadece Miu Miu temasının fabrika ayarlarına dönüş desteği mevcut")
    defaults = _build_default_theme()
    await db.themes.update_one({"id": theme_id}, {"$set": {
        "blocks": defaults["blocks"],
        "menu": defaults["menu"],
        "settings": defaults["settings"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }})
    return await db.themes.find_one({"id": theme_id}, {"_id": 0})


# ----- Public Storefront Routes -----
@public_router.get("/active")
async def get_active_theme():
    """Public: get currently active theme."""
    await _ensure_seed()
    theme = await db.themes.find_one({"is_active": True}, {"_id": 0})
    if not theme:
        # Fall back to first theme available
        theme = await db.themes.find_one({}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Aktif tema yok")
    # Public-safe blocks (only active)
    theme["blocks"] = [b for b in theme.get("blocks", []) if b.get("is_active", True)]
    theme["blocks"].sort(key=lambda b: b.get("order", 0))
    return theme


@public_router.get("/{slug}")
async def get_theme_by_slug(slug: str):
    """Public: get a theme by slug (for preview)."""
    await _ensure_seed()
    theme = await db.themes.find_one({"slug": slug}, {"_id": 0})
    if not theme:
        raise HTTPException(404, "Tema bulunamadı")
    theme["blocks"] = [b for b in theme.get("blocks", []) if b.get("is_active", True)]
    theme["blocks"].sort(key=lambda b: b.get("order", 0))
    return theme
