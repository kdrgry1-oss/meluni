"""
=============================================================================
brand_mapping.py — Çok Pazaryerli Marka Eşleştirme
=============================================================================

AMAÇ:
  Sistem markalarını (Brands koleksiyonundaki) her pazaryerinin kendi marka
  katalog değerleriyle eşleştirmek. Ticimax "Marka Eşleştir" ekranının ayna.

NASIL ÇALIŞIR?
  `brand_mappings` koleksiyonunda her satır:
    {
      "brand_id": "<sistem marka id>",
      "brand_name": "FACETTE",
      "marketplace": "trendyol" | "hepsiburada" | ...
      "marketplace_brand_id": "1234",
      "marketplace_brand_name": "FACETTE",
      "status": "matched" | "unmatched",
      "updated_at": "..."
    }

ENDPOINT'LER:
  GET  /api/brand-mapping/{marketplace}
       Pazaryeri bazlı tüm marka eşleşme durumunu döner.
  POST /api/brand-mapping/{marketplace}/auto-match
       İsim bazlı otomatik eşleşme dener.
  POST /api/brand-mapping/{marketplace}/{brand_id}
       Tek marka için manuel eşleştirme.
  DELETE /api/brand-mapping/{marketplace}/{brand_id}
       Eşleşmeyi kaldır.

KULLANAN FRONTEND:
  /app/frontend/src/pages/admin/BrandMapping.jsx
=============================================================================
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone

from .deps import db, require_admin

router = APIRouter(prefix="/brand-mapping", tags=["Brand Mapping"])


MARKETPLACES = ["trendyol", "hepsiburada", "temu", "n11", "amazon-tr",
                "amazon-de", "aliexpress", "etsy", "hepsi-global",
                "fruugo", "emag", "trendyol-ihracat", "ciceksepeti"]


@router.get("/{marketplace}")
async def list_brand_mappings(marketplace: str,
                              current_user: dict = Depends(require_admin)):
    """
    Sistem markaları + seçili pazaryerindeki eşleşme durumlarını birleştirip döner.
    Hiçbir eşleşme yoksa tüm markalar "unmatched" olarak gelir.
    """
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")

    # Sistem markaları — önce brands koleksiyonu, yoksa products.brand distinct
    brands = await db.brands.find({}, {"_id": 0}).to_list(length=1000)
    if not brands:
        distinct_names = await db.products.distinct("brand")
        brands = [{"id": f"brand-{i}", "name": n} for i, n in enumerate(distinct_names) if n]
    # Pazaryeri eşleşmeleri
    mappings = await db.brand_mappings.find(
        {"marketplace": marketplace}, {"_id": 0}
    ).to_list(length=2000)
    mp_map = {m.get("brand_id"): m for m in mappings}

    rows = []
    for b in brands:
        bid = b.get("id") or b.get("_id")
        m = mp_map.get(bid) or {}
        rows.append({
            "brand_id": bid,
            "brand_name": b.get("name", ""),
            "marketplace_brand_id": m.get("marketplace_brand_id"),
            "marketplace_brand_name": m.get("marketplace_brand_name"),
            "status": m.get("status") or ("matched" if m.get("marketplace_brand_name") else "unmatched"),
            "updated_at": m.get("updated_at"),
        })

    matched = sum(1 for r in rows if r["status"] == "matched")
    return {
        "marketplace": marketplace,
        "total": len(rows),
        "matched": matched,
        "unmatched": len(rows) - matched,
        "items": rows,
    }


@router.post("/{marketplace}/auto-match")
async def auto_match_brands(marketplace: str,
                             current_user: dict = Depends(require_admin)):
    """
    Basit isim bazlı otomatik eşleşme. İlerleyen aşamada gerçek pazaryeri
    marka listesine karşı fuzzy match yapılabilir (Trendyol /suppliers/brands).
    Şu anda sistem markasının adını aynen eşleştirme değeri olarak işaretler.
    """
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")

    brands = await db.brands.find({}, {"_id": 0}).to_list(length=1000)
    if not brands:
        distinct_names = await db.products.distinct("brand")
        brands = [{"id": f"brand-{i}", "name": n} for i, n in enumerate(distinct_names) if n]
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for b in brands:
        bid = b.get("id") or b.get("_id")
        name = (b.get("name") or "").strip()
        if not bid or not name: continue
        await db.brand_mappings.update_one(
            {"brand_id": bid, "marketplace": marketplace},
            {"$set": {
                "brand_id": bid,
                "brand_name": name,
                "marketplace": marketplace,
                "marketplace_brand_id": None,
                "marketplace_brand_name": name,
                "status": "matched",
                "updated_at": now,
                "updated_by": current_user.get("email"),
            }},
            upsert=True,
        )
        count += 1
    return {"success": True, "matched": count,
            "message": f"{count} marka otomatik eşleştirildi ({marketplace})."}


# NOTE: Aşağıdaki iki route, generic /{brand_id} route'undan ÖNCE tanımlanmalı;
# aksi takdirde "bulk-delete" ve "options" path segment'i `brand_id` olarak yakalanır.
@router.get("/{marketplace}/options")
async def search_marketplace_brands(
    marketplace: str,
    q: str = "",
    limit: int = 50,
    current_user: dict = Depends(require_admin),
):
    """Pazaryerinin kendi marka listesinden arama."""
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    coll_map = {"trendyol": "trendyol_brands"}
    coll_name = coll_map.get(marketplace)
    if not coll_name:
        return {"items": [], "hint": f"{marketplace} için marka cache yok, manuel ID girin"}
    q_ = (q or "").strip()
    query = {}
    if q_:
        import re as _re
        query["name"] = {"$regex": _re.escape(q_), "$options": "i"}  # ReDoS koruması
    rows = await db[coll_name].find(query, {"_id": 0}).limit(max(1, min(200, int(limit)))).to_list(length=limit)
    return {"items": [{"id": r.get("id"), "name": r.get("name", "")} for r in rows], "count": len(rows)}


@router.post("/{marketplace}/bulk-delete")
async def bulk_delete_brand_mappings(
    marketplace: str,
    payload: dict,
    current_user: dict = Depends(require_admin),
):
    """Seçili marka eşleşmelerini toplu sil. Body: {brand_ids: [...]}."""
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    ids = (payload or {}).get("brand_ids") or []
    if not ids:
        return {"success": True, "deleted": 0}
    res = await db.brand_mappings.delete_many({
        "marketplace": marketplace,
        "brand_id": {"$in": ids},
    })
    return {"success": True, "deleted": res.deleted_count}


@router.post("/{marketplace}/reset-all")
async def reset_all(marketplace: str,
                    current_user: dict = Depends(require_admin)):
    """Pazaryerine ait tüm eşleşmeleri siler."""
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    res = await db.brand_mappings.delete_many({"marketplace": marketplace})
    return {"success": True, "deleted": res.deleted_count}


@router.post("/{marketplace}/{brand_id}")
async def set_brand_mapping(marketplace: str, brand_id: str, payload: dict,
                             current_user: dict = Depends(require_admin)):
    """Tek bir markayı manuel eşleştirir veya günceller."""
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    brand = await db.brands.find_one({"$or": [{"id": brand_id}]}, {"_id": 0})
    if not brand:
        # Fallback: products distinct brand isminden türetilmiş brand-{i}
        if brand_id.startswith("brand-"):
            try:
                idx = int(brand_id.split("-", 1)[1])
                names = await db.products.distinct("brand")
                names = [n for n in names if n]
                if 0 <= idx < len(names):
                    brand = {"id": brand_id, "name": names[idx]}
            except Exception:
                pass
    if not brand:
        raise HTTPException(status_code=404, detail="Marka bulunamadı")
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "brand_id": brand_id,
        "brand_name": brand.get("name", ""),
        "marketplace": marketplace,
        "marketplace_brand_id": (payload or {}).get("marketplace_brand_id"),
        "marketplace_brand_name": (payload or {}).get("marketplace_brand_name") or brand.get("name", ""),
        "status": "matched",
        "updated_at": now,
        "updated_by": current_user.get("email"),
    }
    await db.brand_mappings.update_one(
        {"brand_id": brand_id, "marketplace": marketplace},
        {"$set": doc}, upsert=True
    )
    return {"success": True, "mapping": doc}


@router.delete("/{marketplace}/{brand_id}")
async def clear_brand_mapping(marketplace: str, brand_id: str,
                               current_user: dict = Depends(require_admin)):
    """Eşleşmeyi siler (markayı 'unmatched' durumuna döndürür)."""
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=404, detail="Pazaryeri bulunamadı")
    await db.brand_mappings.delete_one({"brand_id": brand_id, "marketplace": marketplace})
    return {"success": True}
