"""
Production hooks (Iter 43) — Stockout uyarısı email, üretim planına ekleme.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import db, require_admin, generate_id, require_permission

router = APIRouter(prefix="/admin/production-hooks", tags=["production-hooks"])


class ProductionPlanItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int = Field(..., ge=1)
    stockout_date: str | None = None
    daily_velocity: float | None = None
    severity: str | None = None


@router.post("/add-to-plan")
async def add_to_production_plan(items: List[ProductionPlanItem], admin=Depends(require_permission("manufacturing.create"))):
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
async def list_production_plan(status: str = "planned", admin=Depends(require_permission("manufacturing.view"))):
    items = []
    async for d in db.production_forecast.find({"status": status}, {"_id": 0}).sort("created_at", -1).limit(500):
        items.append(d)
    return {"items": items, "total": len(items)}


def _check_range(name: str, value, lo: int, hi: int) -> int:
    """Parametre doğrulaması. Varsayılanlar düz Python int'tir (Query DEĞİL): zamanlayıcı
    (scheduler._daily_stockout_alert) bu fonksiyonu FastAPI'siz, yalnız admin= ile çağırır."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} tam sayı olmalı")
    if v < lo or v > hi:
        raise HTTPException(status_code=422, detail=f"{name} {lo}-{hi} aralığında olmalı")
    return v


@router.post("/send-stockout-alert")
async def send_stockout_alert_email(admin=Depends(require_permission("manufacturing.create")), velocity_days: int = 30,
                                    horizon_days: int = 60, target_cover_days: int = 60):
    """Stok tükenme uyarısını email olarak gönderir. Parametreler (query) ekrandaki
    Üretim Önerisi seçimleriyle aynıdır; verilmezse günlük cron varsayılanları 30/60/60."""
    from security.alerts import send_alert
    velocity_days = _check_range("velocity_days", velocity_days, 7, 180)
    horizon_days = _check_range("horizon_days", horizon_days, 7, 365)
    target_cover_days = _check_range("target_cover_days", target_cover_days, 14, 365)
    params = {"velocity_days": velocity_days, "horizon_days": horizon_days,
              "target_cover_days": target_cover_days}
    # En kritik 20 ürünü çek
    from .reports_v2 import stockout_forecast as _forecast
    data = await _forecast(velocity_days=velocity_days, horizon_days=horizon_days,
                           target_cover_days=target_cover_days, min_velocity=0.05, _=admin)
    items = data["items"][:20]
    s = data["summary"]
    if not items:
        return {"ok": False, "message": "Uyarılacak ürün yok", "params": params}
    _val = f"{s['total_production_value']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    lines = [f"Meluni Stok Tükenme Uyarısı\n",
             f"Parametre: son {velocity_days} gün satış hızı, {horizon_days} gün tükenme ufku, "
             f"{target_cover_days} gün hedef stok",
             f"🔴 Kritik (≤14g): {s['critical']}",
             f"🟠 Yüksek (≤30g): {s['high']}",
             f"🟡 Uyarı: {s['warning']}",
             f"Toplam üretim önerisi: {s['total_production_units']} adet "
             f"(satış fiyatıyla ₺{_val})",
             "", f"── EN ACİL ÜRÜNLER (ilk {len(items)} / {data.get('total', len(items))}) ──"]
    for it in items:
        lines.append(f"• {it['name']}")
        lines.append(f"  Stok: {it['current_stock']} | Tükenme: {it['stockout_date_tr']} ({it['days_until_stockout']}g) | Üret: {it['suggested_production_qty']} adet")
    # ALICI (beyaz etiket): İşletme Kuralları `stock.alert_email` → firma iletişim
    # e-postası → (boşsa) security/alerts varsayılanı. Firma-özel adres kodda DEĞİL.
    _to = None
    try:
        from scheduler import _stock_alert_recipients
        from .deps import db as _db
        _rcpts = await _stock_alert_recipients(_db)
        _to = ",".join(_rcpts) if _rcpts else None   # send_alert her adresi ayrı alıcıya böler
    except Exception:
        _to = None
    result = await send_alert(
        kind="stockout_forecast",
        level="critical" if s["critical"] > 0 else "warning",
        title=f"Stok Tükenme Uyarısı: {len(items)} ürün için üretim gerekiyor",
        body="\n".join(lines),
        fingerprint="stockout_daily",
        meta={"critical": s["critical"], "high": s["high"], "items": len(items), **params},
        to_email=_to,
    )
    return {"ok": True, "alert": result, "items_count": len(items),
            "total": data.get("total", len(items)), "params": params}
