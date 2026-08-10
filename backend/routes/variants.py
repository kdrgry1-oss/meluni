from fastapi import APIRouter, HTTPException, Depends
from typing import List
from models import VariantOption, VariantOptionCreate
from routes.deps import db, require_admin
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/variants", tags=["variants"])

@router.get("", response_model=List[VariantOption])
async def get_all_variants():
    """Tüm varyant değerlerini getirir (Sıraya göre)"""
    variants = []
    async for variant in db.variant_options.find().sort("sort_order", 1):
        variants.append(VariantOption(**variant))
    return variants

@router.get("/{variant_type}", response_model=List[VariantOption])
async def get_variants_by_type(variant_type: str):
    """Beden, Renk gibi türe göre varyantları getirir"""
    variants = []
    async for variant in db.variant_options.find({"type": variant_type}).sort("sort_order", 1):
        variants.append(VariantOption(**variant))
    return variants

@router.post("", response_model=VariantOption)
async def create_variant(variant: VariantOptionCreate, admin=Depends(require_admin)):
    """Yeni bir varyant değeri ekler"""
    # Aynı değerde var mı kontrol et
    existing = await db.variant_options.find_one({
        "type": variant.type,
        "value": variant.value
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Bu varyant değeri zaten mevcut")
        
    new_variant = VariantOption(**variant.model_dump())
    
    await db.variant_options.insert_one(new_variant.model_dump())
    return new_variant

@router.put("/reorder", response_model=dict)
async def reorder_variants(items: List[dict], admin=Depends(require_admin)):
    """
    Birden fazla varyantın sırasını günceller.
    Beklenen format: [{"id": "...", "sort_order": 1}, {"id": "...", "sort_order": 2}]
    """
    from pymongo import UpdateOne
    
    if not items:
        return {"success": True, "message": "No items to reorder"}
        
    ops = []
    for item in items:
        if "id" in item and "sort_order" in item:
            ops.append(
                UpdateOne(
                    {"id": item["id"]}, 
                    {"$set": {"sort_order": int(item["sort_order"])}}
                )
            )
            
    if ops:
        result = await db.variant_options.bulk_write(ops, ordered=False)
        return {
            "success": True, 
            "message": f"Sıralama güncellendi: {result.modified_count} kayıt değiştirildi"
        }
    
    return {"success": False, "message": "Geçerli veri bulunamadı"}

@router.post("/bulk-delete")
async def bulk_delete_variants(payload: dict, admin=Depends(require_admin)):
    """Seçili birden fazla varyant değerini TOPLU siler. payload: {"ids": ["...", "..."]}"""
    ids = payload.get("ids") or []
    ids = [str(i) for i in ids if i]
    if not ids:
        raise HTTPException(status_code=400, detail="Silinecek değer seçilmedi")
    result = await db.variant_options.delete_many({"id": {"$in": ids}})
    return {"success": True, "deleted": result.deleted_count,
            "message": f"{result.deleted_count} değer silindi"}

@router.delete("/{variant_id}")
async def delete_variant(variant_id: str, admin=Depends(require_admin)):
    result = await db.variant_options.delete_one({"id": variant_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Varyant bulunamadı")
    return {"success": True, "message": "Varyant silindi"}

@router.put("/{variant_id}", response_model=VariantOption)
async def update_variant(variant_id: str, update_data: dict, admin=Depends(require_admin)):
    if "value" in update_data:
        existing = await db.variant_options.find_one({
            "type": update_data.get("type"),
            "value": update_data.get("value"),
            "id": {"$ne": variant_id}
        })
        if existing:
            raise HTTPException(status_code=400, detail="Bu varyant değeri zaten mevcut")
            
    result = await db.variant_options.update_one(
        {"id": variant_id},
        {"$set": {"value": update_data.get("value"), "is_active": update_data.get("is_active", True)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Varyant bulunamadı")
    
    updated = await db.variant_options.find_one({"id": variant_id})
    return VariantOption(**updated)

@router.post("/aggregate")
async def aggregate_variants(admin=Depends(require_admin)):
    """Mevcut ürünlerden beden ve renk değerlerini toplayıp varyant seçeneklerine ekler."""
    products = await db.products.find({}, {"variants": 1}).to_list(length=None)
    
    sizes = set()
    colors = set()
    
    for p in products:
        for v in p.get("variants", []):
            if v.get("size"):
                s = str(v.get("size")).strip()
                if s and s.lower() not in ['null', 'none']:
                    sizes.add(s)
            
            if v.get("color"):
                c = str(v.get("color")).strip()
                if c and c.lower() not in ['null', 'none']:
                    colors.add(c)
                    
    added_count = 0
    sort_order_size = await db.variant_options.count_documents({"type": "size"})
    sort_order_color = await db.variant_options.count_documents({"type": "color"})
    
    for s in sizes:
        if not await db.variant_options.find_one({"type": "size", "value": s}):
            sort_order_size += 1
            new_opt = VariantOption(type="size", value=s, sort_order=sort_order_size)
            await db.variant_options.insert_one(new_opt.model_dump())
            added_count += 1
            
    for c in colors:
        if not await db.variant_options.find_one({"type": "color", "value": c}):
            sort_order_color += 1
            new_opt = VariantOption(type="color", value=c, sort_order=sort_order_color)
            await db.variant_options.insert_one(new_opt.model_dump())
            added_count += 1
            
    return {"success": True, "message": f"{added_count} yeni varyant değeri sisteme eklendi."}
