"""
Vendors (Cari Kartları) Routes - Tedarikçi ve Üretici yönetimi
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from routes.deps import db, require_admin, generate_short_id
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_vendors(
    vendor_type: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 50,
    current_user: dict = Depends(require_admin)
):
    """Get all vendors (suppliers and manufacturers)"""
    query = {}
    
    if vendor_type and vendor_type in ["supplier", "manufacturer"]:
        query["vendor_type"] = vendor_type
    
    if search:
        import re as _re
        _s = _re.escape(str(search))  # ReDoS koruması
        query["$or"] = [
            {"name": {"$regex": _s, "$options": "i"}},
            {"company_name": {"$regex": _s, "$options": "i"}},
            {"tax_number": {"$regex": _s, "$options": "i"}}
        ]
    
    skip = (page - 1) * limit
    
    vendors = await db.vendors.find(query).sort("name", 1).skip(skip).limit(limit).to_list(limit)
    total = await db.vendors.count_documents(query)
    
    # Remove MongoDB _id from results
    for v in vendors:
        v.pop("_id", None)
    
    return {
        "vendors": vendors,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }


@router.get("/search")
async def search_vendors(
    q: str = "",
    vendor_type: str = None,
    limit: int = 20,
    current_user: dict = Depends(require_admin)
):
    """Search vendors for autocomplete"""
    query = {"is_active": True}
    
    if vendor_type and vendor_type in ["supplier", "manufacturer"]:
        query["vendor_type"] = vendor_type
    
    if q:
        import re as _re
        _q = _re.escape(str(q))  # ReDoS koruması
        query["$or"] = [
            {"name": {"$regex": _q, "$options": "i"}},
            {"company_name": {"$regex": _q, "$options": "i"}}
        ]
    
    vendors = await db.vendors.find(query, {"_id": 0, "id": 1, "name": 1, "company_name": 1, "vendor_type": 1}).sort("name", 1).limit(limit).to_list(limit)
    
    return vendors


@router.get("/{vendor_id}")
async def get_vendor(vendor_id: str, current_user: dict = Depends(require_admin)):
    """Get single vendor"""
    vendor = await db.vendors.find_one({"id": vendor_id}, {"_id": 0})
    if not vendor:
        raise HTTPException(status_code=404, detail="Cari bulunamadı")
    return vendor


@router.post("")
async def create_vendor(vendor_data: dict, current_user: dict = Depends(require_admin)):
    """Create new vendor"""
    
    vendor = {
        "id": await generate_short_id("vendors"),
        "name": vendor_data.get("name", ""),
        "vendor_type": vendor_data.get("vendor_type", "supplier"),  # supplier or manufacturer
        "company_name": vendor_data.get("company_name", ""),
        "tax_office": vendor_data.get("tax_office", ""),
        "tax_number": vendor_data.get("tax_number", ""),
        "identity_number": vendor_data.get("identity_number", ""),
        "address": vendor_data.get("address", ""),
        "city": vendor_data.get("city", ""),
        "district": vendor_data.get("district", ""),
        "postal_code": vendor_data.get("postal_code", ""),
        "country": vendor_data.get("country", "Türkiye"),
        "phone": vendor_data.get("phone", ""),
        "email": vendor_data.get("email", ""),
        "website": vendor_data.get("website", ""),
        "contact_person": vendor_data.get("contact_person", ""),
        "contact_phone": vendor_data.get("contact_phone", ""),
        "bank_name": vendor_data.get("bank_name", ""),
        "iban": vendor_data.get("iban", ""),
        "notes": vendor_data.get("notes", ""),
        "is_active": vendor_data.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.vendors.insert_one(vendor)
    logger.info(f"Vendor created: {vendor['id']} - {vendor['name']}")
    
    return {"id": vendor["id"], "message": "Cari oluşturuldu"}


@router.put("/{vendor_id}")
async def update_vendor(vendor_id: str, vendor_data: dict, current_user: dict = Depends(require_admin)):
    """Update vendor"""
    existing = await db.vendors.find_one({"id": vendor_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Cari bulunamadı")
    
    vendor_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Remove id and created_at from update
    vendor_data.pop("id", None)
    vendor_data.pop("created_at", None)
    vendor_data.pop("_id", None)
    
    await db.vendors.update_one({"id": vendor_id}, {"$set": vendor_data})
    
    return {"message": "Cari güncellendi"}


@router.delete("/{vendor_id}")
async def delete_vendor(vendor_id: str, current_user: dict = Depends(require_admin)):
    """Delete vendor"""
    result = await db.vendors.delete_one({"id": vendor_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cari bulunamadı")
    
    return {"message": "Cari silindi"}


@router.post("/seed-default")
async def seed_default_vendor(current_user: dict = Depends(require_admin)):
    """Seed default FACETTE manufacturer"""
    
    existing = await db.vendors.find_one({"name": "FACETTE", "vendor_type": "manufacturer"})
    if existing:
        return {"message": "FACETTE üretici zaten mevcut", "id": existing["id"]}
    
    vendor = {
        "id": await generate_short_id("vendors"),
        "name": "FACETTE",
        "vendor_type": "manufacturer",
        "company_name": "FACETTE Tekstil Ltd. Şti.",
        "tax_office": "",
        "tax_number": "",
        "identity_number": "",
        "address": "",
        "city": "İstanbul",
        "district": "",
        "postal_code": "",
        "country": "Türkiye",
        "phone": "+90 212 000 00 00",
        "email": "info@facette.com",
        "website": "www.facette.com",
        "contact_person": "",
        "contact_phone": "",
        "bank_name": "",
        "iban": "",
        "notes": "Varsayılan üretici",
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.vendors.insert_one(vendor)
    
    return {"id": vendor["id"], "message": "FACETTE üretici oluşturuldu"}
