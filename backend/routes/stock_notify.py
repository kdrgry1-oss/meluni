"""Stok bildirimi ("Gelince Haber Ver") — stokta olmayan beden için müşteri
e-postası toplar. Ürün tekrar stoğa girince admin bu listeden bilgilendirme yapar.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from datetime import datetime, timezone

from routes.deps import db, generate_id, is_safe_email, safe_str, require_admin

router = APIRouter(prefix="/stock-notify", tags=["stock-notify"])


class StockNotifyRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    size: str = Field(default="")
    email: str = Field(..., min_length=3, max_length=256)


@router.post("")
async def create_stock_notify(payload: StockNotifyRequest):
    """Müşteri stok bildirimi talebi (public)."""
    email = (payload.email or "").strip().lower()
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Geçerli bir e-posta adresi giriniz.")

    product = await db.products.find_one(
        {"$or": [{"id": payload.product_id}, {"slug": payload.product_id}]},
        {"_id": 0, "id": 1, "name": 1},
    )
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")

    size = safe_str(payload.size, 50)
    pid = product["id"]

    # Aynı e-posta + ürün + beden için bekleyen talep varsa tekrar oluşturma
    existing = await db.stock_notifications.find_one({
        "product_id": pid, "size": size, "email": email, "notified": False,
    })
    if existing:
        return {"success": True, "message": "Bu beden için zaten kayıtlısınız. Stoğa girince haber vereceğiz."}

    doc = {
        "id": generate_id(),
        "product_id": pid,
        "product_name": product.get("name", ""),
        "size": size,
        "email": email,
        "notified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notified_at": None,
    }
    await db.stock_notifications.insert_one(doc)
    return {"success": True, "message": "Talebiniz alındı. Ürün stoğa girince e-posta ile bilgilendireceğiz."}


@router.get("")
async def list_stock_notifications(notified: bool | None = None, _admin=Depends(require_admin)):
    """Admin — stok bildirim taleplerini listeler."""
    query = {}
    if notified is not None:
        query["notified"] = notified
    items = await db.stock_notifications.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return {"items": items, "total": len(items)}
