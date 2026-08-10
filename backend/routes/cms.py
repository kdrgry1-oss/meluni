"""
CMS routes - Page blocks, homepage content management
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from datetime import datetime, timezone

from .deps import db, logger, require_admin, generate_id

router = APIRouter(prefix="/page-blocks", tags=["CMS"])

@router.get("")
async def get_page_blocks(
    page: str = Query("home"),
    is_active: Optional[bool] = None
):
    """Get page blocks for a specific page"""
    query = {"page": page}
    if is_active is not None:
        query["is_active"] = is_active
    
    blocks = await db.page_blocks.find(query, {"_id": 0}).sort("sort_order", 1).to_list(50)
    return blocks

# ── HEADER MENÜ (Menü Yönetimi) ─────────────────────────────────────────────
# ÖNEMLİ: /{block_id} rotasından ÖNCE tanımlı olmalı, yoksa "header-menu"
# block_id olarak yakalanır.

@router.get("/header-menu")
async def get_header_menu():
    """Vitrin header menüsü (public). Kayıt yoksa boş döner — frontend
    kendi varsayılanını (mevcut sabit menü) kullanır, görünüm bozulmaz."""
    doc = await db.settings.find_one({"id": "header_menu"}, {"_id": 0})
    return {"tabs": (doc or {}).get("tabs") or []}


@router.put("/header-menu")
async def save_header_menu(payload: dict, current_user: dict = Depends(require_admin)):
    """Header menüsünü kaydeder. tabs: [{id,label,type,link,style,active,columns:[{title,link,items:[{name,link}]}]}]"""
    tabs = payload.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise HTTPException(status_code=400, detail="tabs boş olamaz")

    def _s(v, maxlen=300):
        return str(v or "").strip()[:maxlen]

    clean = []
    for i, t in enumerate(tabs):
        if not isinstance(t, dict) or not _s(t.get("label")):
            continue
        tab = {
            "id": _s(t.get("id")) or f"tab{i}",
            "label": _s(t.get("label"), 60),
            "type": "mega" if t.get("type") == "mega" else "link",
            "link": _s(t.get("link")) or "/",
            "style": t.get("style") if t.get("style") in ("accent", "sale") else "normal",
            "active": t.get("active") is not False,
        }
        if tab["type"] == "mega":
            cols = []
            for c in (t.get("columns") or []):
                if not isinstance(c, dict):
                    continue
                items = [{"name": _s(it.get("name"), 60), "link": _s(it.get("link")) or "/"}
                         for it in (c.get("items") or [])
                         if isinstance(it, dict) and _s(it.get("name"))]
                cols.append({"title": _s(c.get("title"), 60), "link": _s(c.get("link")) or "/", "items": items})
            tab["columns"] = cols
        clean.append(tab)
    if not clean:
        raise HTTPException(status_code=400, detail="Geçerli sekme yok")

    await db.settings.update_one(
        {"id": "header_menu"},
        {"$set": {"tabs": clean, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": current_user.get("email") or current_user.get("id")}},
        upsert=True,
    )
    return {"success": True, "tabs": clean}


@router.get("/{block_id}")
async def get_page_block(block_id: str):
    """Get single page block"""
    block = await db.page_blocks.find_one({"id": block_id}, {"_id": 0})
    if not block:
        raise HTTPException(status_code=404, detail="Blok bulunamadı")
    return block

@router.post("")
async def create_page_block(
    block_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Create page block (admin only)"""
    block = {
        "id": generate_id(),
        "type": block_data.get("type", "hero_slider"),
        "title": block_data.get("title", ""),
        "images": block_data.get("images", []),
        "links": block_data.get("links", []),
        "settings": block_data.get("settings", {}),
        "page": block_data.get("page", "home"),
        "sort_order": block_data.get("sort_order", 0),
        "is_active": block_data.get("is_active", True),
        "show_desktop": block_data.get("show_desktop", True),
        "show_mobile": block_data.get("show_mobile", True),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.page_blocks.insert_one(block)
    return {"id": block["id"], "message": "Blok oluşturuldu"}

@router.put("/{block_id}")
async def update_page_block(
    block_id: str,
    block_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Update page block (admin only).
    Whitelist'li güncelleme: id, created_at gibi sistem alanları override edilemez.
    """
    existing = await db.page_blocks.find_one({"id": block_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Blok bulunamadı")

    allowed = {"type", "title", "images", "links", "settings", "page", "sort_order", "is_active", "show_desktop", "show_mobile"}
    update_set = {k: v for k, v in block_data.items() if k in allowed}
    update_set["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.page_blocks.update_one({"id": block_id}, {"$set": update_set})
    return {"message": "Blok güncellendi"}

@router.delete("/{block_id}")
async def delete_page_block(
    block_id: str,
    current_user: dict = Depends(require_admin)
):
    """Delete page block (admin only)"""
    result = await db.page_blocks.delete_one({"id": block_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Blok bulunamadı")
    return {"message": "Blok silindi"}


@router.post("/reorder")
async def reorder_page_blocks(
    payload: dict,
    current_user: dict = Depends(require_admin)
):
    """Toplu sıralama güncellemesi.
    Body: { "ids": ["id1", "id2", "id3", ...] } — listedeki sıraya göre sort_order=1,2,3,... atanır.
    Tek tek PUT'a gerek kalmadan hızlı sürükle-bırak kaydetmeyi sağlar.
    """
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids listesi gerekli")
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for idx, bid in enumerate(ids, start=1):
        r = await db.page_blocks.update_one(
            {"id": bid}, {"$set": {"sort_order": idx, "updated_at": now}}
        )
        updated += r.modified_count
    return {"success": True, "updated": updated, "total": len(ids)}


@router.post("/seed-default-home")
async def seed_default_home_blocks(
    overwrite: bool = Query(False),
    current_user: dict = Depends(require_admin)
):
    """Mevcut Home.jsx default tasarımını DB bloklarına aktarır (admin'den
    yönetilebilir hale getirir). overwrite=True ise mevcut home blokları silinir.
    """
    if overwrite:
        await db.page_blocks.delete_many({"page": "home"})

    existing_count = await db.page_blocks.count_documents({"page": "home"})
    if existing_count > 0 and not overwrite:
        return {
            "success": False,
            "message": f"Anasayfada zaten {existing_count} blok var. overwrite=true ile çağırın.",
            "existing_count": existing_count,
        }

    now = datetime.now(timezone.utc).isoformat()
    base = "https://cdn.facette.com.tr/pagedesign"
    default_blocks = [
        {
            "type": "hero_slider",
            "title": "Ana Slider",
            "images": [
                f"{base}/en-yeniler-dc2e-800.webp",
                f"{base}/ae79c961-ba0b-49e3-b274-2c6cc78ab700-800.webp",
            ],
            "links": ["/kategori/en-yeniler", "/kategori/sale"],
            "settings": {"autoplay": True, "interval_ms": 5000},
            "sort_order": 1,
        },
        {
            "type": "full_banner",
            "title": "Tek Banner",
            "images": [f"{base}/title-cb23757c-6-800.webp"],
            "links": ["/kategori/en-yeniler"],
            "sort_order": 2,
        },
        {
            "type": "half_banners",
            "title": "İki Banner",
            "images": [
                f"{base}/title-65777bd3-0-800.webp",
                f"{base}/title-7b3e27f9-5-800.webp",
            ],
            "links": ["/kategori/gomlek", "/kategori/aksesuar"],
            "sort_order": 3,
        },
        {
            "type": "product_slider",
            "title": "Yeni Sezon",
            "images": [],
            "links": [],
            "settings": {"category_slug": "en-yeniler", "limit": 8},
            "sort_order": 4,
        },
        {
            "type": "instashop",
            "title": "Stilini Yarat",
            "images": [
                f"{base}/orj-ce09fd5d-c580-40eb-87f2-e4637265bad9-800.webp",
                f"{base}/orj-114d3d37-9c7f-495c-8bc2-28d32781818d-800.webp",
                f"{base}/orj-e18eff06-8597-4f10-92cb-64b11151a74d-800.webp",
                f"{base}/orj-fa071a71-bcaf-452b-90d5-e8cb0c352fe0-800.webp",
                f"{base}/orj-87d15ba0-0081-4b65-acc5-b12328de368b-800.webp",
            ],
            "links": [
                "/urun/basic-atki",
                "/kategori/ceket",
                "/kategori/kaban",
                "/kategori/pantolon",
                "/kategori/elbise",
            ],
            "sort_order": 5,
        },
    ]

    inserted = []
    for b in default_blocks:
        b.update({
            "id": generate_id(),
            "page": "home",
            "is_active": True,
            "created_at": now,
        })
        b.setdefault("settings", {})
        await db.page_blocks.insert_one(b)
        inserted.append({"id": b["id"], "type": b["type"], "title": b["title"]})

    return {
        "success": True,
        "message": f"{len(inserted)} default blok eklendi",
        "blocks": inserted,
    }
