"""
Product Attributes routes - CRUD and Sync
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from bson.objectid import ObjectId
import logging

from .deps import db, require_admin, generate_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attributes", tags=["Attributes"])

class AttributeBase(BaseModel):
    name: str
    values: List[str] = []

class AttributeCreate(AttributeBase):
    pass

class AttributeUpdate(AttributeBase):
    pass

@router.get("")
async def get_attributes(current_user: dict = Depends(require_admin)):
    """Get all global product attributes"""
    try:
        attrs = await db.attributes.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
        return {"success": True, "attributes": attrs}
    except Exception as e:
        logger.error(f"Error fetching attributes: {e}")
        raise HTTPException(status_code=500, detail="Özellikler alınamadı.")

@router.post("")
async def create_attribute(req: AttributeCreate, current_user: dict = Depends(require_admin)):
    """Create a new global product attribute"""
    try:
        existing = await db.attributes.find_one({"name": {"$regex": f"^{req.name}$", "$options": "i"}})
        if existing:
            raise HTTPException(status_code=400, detail="Bu özellik zaten mevcut.")

        attr_doc = {
            "id": generate_id(),
            "name": req.name.strip(),
            "values": list(set([str(v).strip() for v in req.values if str(v).strip()])),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await db.attributes.insert_one(attr_doc)
        attr_doc["_id"] = str(attr_doc["_id"])
        return {"success": True, "attribute": attr_doc, "message": "Özellik oluşturuldu"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik oluşturulamadı.")

@router.put("/{attr_id}")
async def update_attribute(attr_id: str, req: AttributeUpdate, current_user: dict = Depends(require_admin)):
    """Update an existing global product attribute (e.g. add/remove values)"""
    try:
        existing = await db.attributes.find_one({"id": attr_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Özellik bulunamadı.")
            
        duplicate = await db.attributes.find_one({
            "id": {"$ne": attr_id},
            "name": {"$regex": f"^{req.name}$", "$options": "i"}
        })
        if duplicate:
            raise HTTPException(status_code=400, detail="Bu isimde başka bir özellik zaten mevcut.")

        cleaned_values = list(set([str(v).strip() for v in req.values if str(v).strip()]))
        
        await db.attributes.update_one(
            {"id": attr_id},
            {"$set": {
                "name": req.name.strip(),
                "values": cleaned_values,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        return {"success": True, "message": "Özellik güncellendi"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik güncellenemedi.")

@router.delete("/{attr_id}")
async def delete_attribute(attr_id: str, current_user: dict = Depends(require_admin)):
    """Delete a global product attribute"""
    try:
        result = await db.attributes.delete_one({"id": attr_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Özellik bulunamadı.")
        return {"success": True, "message": "Özellik silindi"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting attribute: {e}")
        raise HTTPException(status_code=500, detail="Özellik silinemedi.")

@router.post("/sync-from-trendyol")
async def sync_attributes_from_trendyol(current_user: dict = Depends(require_admin)):
    """Pull ALL attributes and values from cached Trendyol categories into the global attributes list"""
    try:
        trendyol_caches = await db.trendyol_attributes.find({}).to_list(1000)
        if not trendyol_caches:
            return {"success": False, "message": "Trendyol'dan çekilmiş kategori özelliği bulunamadı. Önce kategorilerde 'Özellikler' butonuna tıklayarak bazı kategorileri çekin."}
            
        # Extract unique names and unique values per name
        attribute_map = {}
        for cache in trendyol_caches:
            attrs = cache.get("attributes", [])
            for attr in attrs:
                # attr format: {"attribute": {"name": "Renk"}, "attributeValues": [{"name": "Kırmızı"}]}
                attr_name = attr.get("name") or (attr.get("attribute", {}).get("name"))
                if not attr_name:
                    continue
                    
                attr_name = attr_name.strip()
                if attr_name not in attribute_map:
                    attribute_map[attr_name] = set()
                    
                vals = attr.get("attributeValues", [])
                for val in vals:
                    val_name = val.get("name")
                    if val_name:
                        attribute_map[attr_name].add(str(val_name).strip())
                        
        if not attribute_map:
            return {"success": False, "message": "Kategorilerin içinde geçerli bir özellik veya değer bulunamadı."}
            
        # Upsert into db.attributes
        new_count = 0
        update_count = 0
        for attr_name, val_set in attribute_map.items():
            existing = await db.attributes.find_one({"name": {"$regex": f"^{attr_name}$", "$options": "i"}})
            if existing:
                # Merge values
                current_vals = set(existing.get("values", []))
                merged_vals = list(current_vals.union(val_set))
                if len(merged_vals) > len(current_vals):
                    await db.attributes.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {"values": merged_vals, "updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                    update_count += 1
            else:
                # Create new
                await db.attributes.insert_one({
                    "id": generate_id(),
                    "name": attr_name,
                    "values": list(val_set),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                new_count += 1
                
        return {"success": True, "message": f"Trendyol'dan {new_count} yeni özellik eklendi, {update_count} özellik güncellendi."}
    except Exception as e:
        logger.error(f"Error syncing from trendyol: {e}")
        raise HTTPException(status_code=500, detail="Trendyol özellikleri senkronize edilemedi.")

@router.post("/sync-from-products")
async def sync_attributes_from_products(current_user: dict = Depends(require_admin)):
    """Scan all existing products' attributes arrays and populate the global attributes list"""
    try:
        products = await db.products.find({"attributes": {"$exists": True, "$ne": []}}).to_list(None)
        
        attribute_map = {}
        for product in products:
            attrs = product.get("attributes", [])
            for attr in attrs:
                # attr format: {"type": "Beden", "value": "M"}
                attr_type = attr.get("type")
                attr_val = attr.get("value")
                
                if not attr_type:
                    continue
                    
                attr_type = str(attr_type).strip()
                if attr_type not in attribute_map:
                    attribute_map[attr_type] = set()
                    
                if attr_val:
                    attribute_map[attr_type].add(str(attr_val).strip())
                    
        if not attribute_map:
            return {"success": False, "message": "Ürünlerinizin içinde herhangi bir özellik (Beden, Renk vb.) bulunamadı."}
            
        # Upsert into db.attributes
        new_count = 0
        update_count = 0
        for attr_name, val_set in attribute_map.items():
            existing = await db.attributes.find_one({"name": {"$regex": f"^{attr_name}$", "$options": "i"}})
            if existing:
                current_vals = set(existing.get("values", []))
                merged_vals = list(current_vals.union(val_set))
                if len(merged_vals) > len(current_vals):
                    await db.attributes.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {"values": merged_vals, "updated_at": datetime.now(timezone.utc).isoformat()}}
                    )
                    update_count += 1
            else:
                await db.attributes.insert_one({
                    "id": generate_id(),
                    "name": attr_name,
                    "values": list(val_set),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                new_count += 1
                
        return {"success": True, "message": f"Mevcut ürünlerden {new_count} yeni özellik eklendi, {update_count} özellik güncellendi."}
    except Exception as e:
        logger.error(f"Error syncing from products: {e}")
        raise HTTPException(status_code=500, detail="Ürün özellikleri taraması başarısız oldu.")


@router.post("/cleanup-non-textile")
async def cleanup_non_textile_attributes(current_user: dict = Depends(require_admin)):
    """Remove non-textile attributes from the library"""
    remove_names = [
        "Kadran Renk", "Kasa Materyali", "Kasa Renk", "Kasa Çapı", "Kordon Materyali", "Kordon Renk",
        "Mekanizma", "Cam Tipi", "Cam Şekli", "Dönence", "Su Geçirmezlik", "Kutu Durumu",
        "Batarya Boyutu", "Batarya Türü", "Mp3 Çalar", "CE Uygunluk Sembolu", "Berraklık",
        "Raf Sayısı", "Sineklik", "Tekerlek", "Taşıma Kapasitesi", "Taşıma Çantası", "Klips Sayısı",
        "Altın Ayar", "Karat", "Taş Cinsi", "Ayar",
        "TEST_Attr1", "TEST_Attr2", "Test", "Test Kalıp", "Test Kumaş",
        "Birincil İthalatçı Adres Bilgisi", "Birincil İthalatçı Adı", "Birincil İthalatçı Mail Adresi",
        "İkincil İthalatçı Adres Bilgisi", "İkincil İthalatçı Adı", "İkincil İthalatçı Mail Adresi",
        "Üçüncül İthalatçı Adres Bilgisi", "Üçüncül İthalatçı Adı", "Üçüncül İthalatçı Mail Adresi",
        "Üretici Adres Bilgisi", "Üretici Adı", "Üretici Mail Adresi",
        "Paket Derinlik", "Paket Genişlik", "Paket Yükseklik", "Paket İçeriği",
        "Paket Görseli (arka)", "Paket Görseli (ön)",
        "Ağırlık", "Boyut/Ebat", "Derinlik", "Genişlik", "Yükseklik", "Ölçü",
        "Alt Açma Ünitesi", "Bant Stili", "Garanti Süresi", "Kap", "Karakter",
        "Kullanım Alanı", "Kullanım Talimatı/Uyarıları", "Model", "Parça Sayısı",
        "Teknik", "Tema / Stil", "Özellik", "Ürün Tipi", "Yaş", "Ara Kat"
    ]
    result = await db.attributes.delete_many({"name": {"$in": remove_names}})
    return {"success": True, "deleted": result.deleted_count, "message": f"{result.deleted_count} alakasız özellik silindi"}


@router.post("/bulk-set-defaults")
async def bulk_set_default_attributes(current_user: dict = Depends(require_admin)):
    """Set Yaş Grubu=Yetişkin, Menşei=TR ve Sürdürülebilirlik Detayı=Hayır
    for all products that don't have them"""
    _defaults = [("Yaş Grubu", "Yetişkin"), ("Menşei", "TR"),
                 ("Sürdürülebilirlik Detayı", "Hayır")]
    products = await db.products.find({}, {"_id": 0, "id": 1, "attributes": 1}).to_list(None)
    updated = 0
    for p in products:
        attrs = p.get("attributes") or []
        # Veri hijyeni: bazı eski kayıtlarda attributes listesi string öğeler
        # içerebiliyor ('str' has no attribute 'get' → 500). Dict olmayanları atla.
        if isinstance(attrs, dict):
            changed = False
            for _nm, _val in _defaults:
                if not attrs.get(_nm):
                    attrs[_nm] = _val
                    changed = True
            if changed:
                await db.products.update_one(
                    {"id": p["id"]},
                    {"$set": {"attributes": attrs, "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                updated += 1
            continue
        if not isinstance(attrs, list):
            continue
        have = set()
        for a in attrs:
            if isinstance(a, dict):
                nm = a.get("type") or a.get("name")
                if nm:
                    have.add(nm)
            elif isinstance(a, str) and a.strip():
                have.add(a.strip())
        changed = False

        for _nm, _val in _defaults:
            if _nm not in have:
                attrs.append({"type": _nm, "name": _nm, "value": _val})
                changed = True

        if changed:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"attributes": attrs, "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            updated += 1

    # Also ensure these values exist in attribute library
    for attr_name, val in [("Yaş Grubu", "Yetişkin"), ("Menşei", "TR")]:
        existing = await db.attributes.find_one({"name": attr_name})
        if existing:
            vals = existing.get("values", [])
            if val not in vals:
                vals.append(val)
                await db.attributes.update_one({"name": attr_name}, {"$set": {"values": vals}})
        else:
            await db.attributes.insert_one({
                "id": generate_short_id(),
                "name": attr_name,
                "values": [val],
                "created_at": datetime.now(timezone.utc).isoformat()
            })

    return {"success": True, "updated": updated, "message": f"{updated} ürüne Yaş Grubu ve Menşei eklendi"}
