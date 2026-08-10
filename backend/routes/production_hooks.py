"""
Production hooks (Iter 43) — Stockout uyarısı email, üretim planına ekleme.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import db, require_admin, generate_id

router = APIRouter(prefix="/admin/production-hooks", tags=["production-hooks"])


class ProductionPlanItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int = Field(..., ge=1)
    stockout_date: str | None = None
    daily_velocity: float | None = None
    severity: str | None = None


@router.post("/add-to-plan")
async def add_to_production_plan(items: List[ProductionPlanItem], admin=Depends(require_admin)):
    """Stockout forecast'ten gelen ürünleri imalat planı koleksiyonuna ekler.
    Çakışan ürün varsa miktar üzerine güncellenir, durum 'planned' olarak kaydedilir."""
    if not items:
        raise HTTPException(status_code=400, detail="Boş istek")
    now = datetime.now(timezone.utc).isoformat()
    from pymongo import UpdateOne
    ops = []
    for it in items:
        ops.append(UpdateOne(
            {"product_id": it.product_id, "status": "planned"},
            {"$set": {
                "product_id": it.product_id,
                "product_name": it.product_name,
                "quantity": it.quantity,
                "stockout_date": it.stockout_date,
                "daily_velocity": it.daily_velocity,
                "severity": it.severity,
                "status": "planned",
                "added_by": admin.get("email"),
                "updated_at": now,
            }, "$setOnInsert": {"id": generate_id(), "created_at": now}},
            upsert=True,
        ))
    res = await db.production_forecast.bulk_write(ops)  # DENETİM FIX: ayrı koleksiyon (İmalat Planı şemasıyla çakışmasın)
    return {"ok": True, "added": res.upserted_count, "updated": res.modified_count}


@router.get("/plan")
async def list_production_plan(status: str = "planned", admin=Depends(require_admin)):
    items = []
    async for d in db.production_forecast.find({"status": status}, {"_id": 0}).sort("created_at", -1).limit(500):
        items.append(d)
    return {"items": items, "total": len(items)}


@router.post("/send-stockout-alert")
async def send_stockout_alert_email(admin=Depends(require_admin)):
    """Mevcut stok tükenme uyarısını email olarak gönderir."""
    from security.alerts import send_alert
    # En kritik 20 ürünü çek
    from .reports_v2 import stockout_forecast as _forecast
    data = await _forecast(velocity_days=30, horizon_days=60, target_cover_days=60, min_velocity=0.05, _=admin)
    items = data["items"][:20]
    s = data["summary"]
    if not items:
        return {"ok": False, "message": "Uyarılacak ürün yok"}
    lines = [f"Facette Stok Tükenme Uyarısı\n",
             f"🔴 Kritik (≤14g): {s['critical']}",
             f"🟠 Yüksek (≤30g): {s['high']}",
             f"🟡 Uyarı: {s['warning']}",
             f"Toplam üretim önerisi: {s['total_production_units']} adet (₺{s['total_production_value']:,.0f})",
             "", "── EN ACİL ÜRÜNLER ──"]
    for it in items:
        lines.append(f"• {it['name']}")
        lines.append(f"  Stok: {it['current_stock']} | Tükenme: {it['stockout_date_tr']} ({it['days_until_stockout']}g) | Üret: {it['suggested_production_qty']} adet")
    result = await send_alert(
        kind="stockout_forecast",
        level="critical" if s["critical"] > 0 else "warning",
        title=f"Stok Tükenme Uyarısı: {len(items)} ürün için üretim gerekiyor",
        body="\n".join(lines),
        fingerprint="stockout_daily",
        meta={"critical": s["critical"], "high": s["high"], "items": len(items)},
    )
    return {"ok": True, "alert": result, "items_count": len(items)}
