"""
Category routes - CRUD
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from datetime import datetime, timezone
import re

from .deps import db, logger, require_admin, generate_id, generate_short_id

router = APIRouter(prefix="/categories", tags=["Categories"])

def generate_slug(name: str) -> str:
    slug = name.lower()
    tr_map = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c'}
    for tr, en in tr_map.items():
        slug = slug.replace(tr, en)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug

@router.get("")
async def get_categories(
    parent_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    visible_only: bool = False
):
    """Get categories.

    visible_only=True (storefront): pasif ve test/placeholder kategorileri gizler.
    Varsayılan False (admin): tüm kategoriler döner — mevcut davranış korunur.
    """
    query = {}
    if parent_id is not None:
        query["parent_id"] = parent_id
    if is_active is not None:
        query["is_active"] = is_active
    if visible_only and "is_active" not in query:
        query["is_active"] = {"$ne": False}

    categories = await db.categories.find(query, {"_id": 0}).to_list(500)

    if visible_only:
        # Sızan test/placeholder kategorileri (ör. HB_CAT_TEST_123) storefront'tan gizle.
        _test_pat = re.compile(r"hb_cat_test|cat_test|_test_|test_\d|^test[_\-]|[_\-]test$", re.I)
        categories = [
            c for c in categories
            if not any(_test_pat.search(str(c.get(f) or "")) for f in ("id", "slug", "name"))
        ]

    # Build hierarchical full_name
    cat_dict = {c["id"]: c for c in categories}
    for c in categories:
        path = []
        curr = c
        while curr:
            path.append(curr.get("name", ""))
            parent_id = curr.get("parent_id")
            if parent_id and parent_id in cat_dict and parent_id != curr.get("id"):
                curr = cat_dict[parent_id]
            else:
                curr = None
        c["full_name"] = " > ".join(reversed(path))

    # Sort: by sort_order if available, else by full_name
    categories.sort(key=lambda c: (c.get("sort_order") or 999, c.get("full_name", "")))
    return categories

@router.get("/{category_id}")
async def get_category(category_id: str):
    """Get single category"""
    category = await db.categories.find_one(
        {"$or": [{"id": category_id}, {"slug": category_id}, {"slug_aliases": category_id}]},
        {"_id": 0}
    )
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    return category

@router.post("")
async def create_category(
    category_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Create category (admin only)"""
    category = {
        "id": await generate_short_id("categories"),
        "name": category_data.get("name", ""),
        "slug": category_data.get("slug") or generate_slug(category_data.get("name", "")),
        "description": category_data.get("description", ""),
        "image": category_data.get("image", ""),
        "image_url": category_data.get("image_url", ""),
        "parent_id": category_data.get("parent_id"),
        "trendyol_category_id": category_data.get("trendyol_category_id"),
        "hepsiburada_category_id": category_data.get("hepsiburada_category_id"),
        "hepsiburada_category_name": category_data.get("hepsiburada_category_name", ""),
        "temu_category_id": category_data.get("temu_category_id"),
        "temu_category_name": category_data.get("temu_category_name", ""),
        "amazon_category_id": category_data.get("amazon_category_id"),
        "attribute_mapping": category_data.get("attribute_mapping", {}),
        "sort_order": category_data.get("sort_order", 0),
        "is_active": category_data.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.categories.insert_one(category)
    return {"id": category["id"], "message": "Kategori oluşturuldu"}

@router.put("/{category_id}")
async def update_category(
    category_id: str,
    category_data: dict,
    current_user: dict = Depends(require_admin)
):
    """Update category (admin only)"""
    existing = await db.categories.find_one({"id": category_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    
    if category_data.get("name"):
        category_data["slug"] = generate_slug(category_data["name"])
    
    category_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.categories.update_one({"id": category_id}, {"$set": category_data})
    return {"message": "Kategori güncellendi"}

def _id_variants(v):
    """Kategori id'si DB'de int VEYA string olabilir — ikisini de eşlemek için liste döndürür."""
    out = [v]
    s = str(v)
    out.append(s)
    try:
        out.append(int(s))
    except (TypeError, ValueError):
        pass
    # tekilleştir (sırayı koru)
    seen, uniq = set(), []
    for x in out:
        k = (type(x).__name__, str(x))
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq


@router.post("/merge")
async def merge_categories(payload: dict, current_user: dict = Depends(require_admin)):
    """İki kategoriyi GÜVENLİ birleştirir (mükerrer/çift yapıyı temizlemek için).
    source kategorisi altındaki ALT KATEGORİLER ve ürün referansları target'a taşınır,
    sonra source silinir. Ürünler asla yetim kalmaz.

    payload: {"source_id": <id>, "target_id": <id>}
    Reassign edilenler: alt kategori parent_id; ürün category_ids[] / category_id /
    category_slug / category_name (yalnız source'a EŞİT olanlar).
    """
    src_id = payload.get("source_id")
    tgt_id = payload.get("target_id")
    if src_id is None or tgt_id is None:
        raise HTTPException(status_code=400, detail="source_id ve target_id gerekli")
    if str(src_id) == str(tgt_id):
        raise HTTPException(status_code=400, detail="source ve target aynı olamaz")

    src = await db.categories.find_one({"id": {"$in": _id_variants(src_id)}}, {"_id": 0})
    tgt = await db.categories.find_one({"id": {"$in": _id_variants(tgt_id)}}, {"_id": 0})
    if not src:
        raise HTTPException(status_code=404, detail="Kaynak kategori bulunamadı")
    if not tgt:
        raise HTTPException(status_code=404, detail="Hedef kategori bulunamadı")

    src_real = src["id"]; tgt_real = tgt["id"]
    src_vars = _id_variants(src_real)
    report = {"source": {"id": src_real, "name": src.get("name")},
              "target": {"id": tgt_real, "name": tgt.get("name")}}

    # 1) Alt kategorileri target'a taşı
    r_children = await db.categories.update_many(
        {"parent_id": {"$in": src_vars}}, {"$set": {"parent_id": tgt_real}})
    report["children_moved"] = r_children.modified_count

    # 2) Ürün category_ids[] dizisinde source → target
    #    Önce target'ı ekle (source içerenlere), sonra source'u çıkar.
    await db.products.update_many(
        {"category_ids": {"$in": src_vars}}, {"$addToSet": {"category_ids": tgt_real}})
    r_arr = await db.products.update_many(
        {"category_ids": {"$in": src_vars}}, {"$pull": {"category_ids": {"$in": src_vars}}})
    report["products_category_ids"] = r_arr.modified_count

    # 3) Skaler category_id source → target
    r_scalar = await db.products.update_many(
        {"category_id": {"$in": src_vars}}, {"$set": {"category_id": tgt_real}})
    report["products_category_id"] = r_scalar.modified_count

    # 4) category_slug source.slug → target.slug (yalnız tam eşleşen)
    if src.get("slug"):
        r_slug = await db.products.update_many(
            {"category_slug": src["slug"]}, {"$set": {"category_slug": tgt.get("slug") or src["slug"]}})
        report["products_category_slug"] = r_slug.modified_count

    # 5) category_name source.name → target.name (yalnız tam eşleşen)
    if src.get("name"):
        r_name = await db.products.update_many(
            {"category_name": src["name"]}, {"$set": {"category_name": tgt.get("name") or src["name"]}})
        report["products_category_name"] = r_name.modified_count

    # 6) source kategorisini sil
    await db.categories.delete_one({"id": src_real})
    report["source_deleted"] = True
    logger.info(f"[category-merge] {src_real}({src.get('name')}) → {tgt_real}({tgt.get('name')}) {report}")
    return {"message": "Kategoriler birleştirildi", "report": report}


@router.delete("/{category_id}")
async def delete_category(
    category_id: str,
    current_user: dict = Depends(require_admin)
):
    """Delete category (admin only)"""
    result = await db.categories.delete_one({"id": {"$in": _id_variants(category_id)}})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    return {"message": "Kategori silindi"}
