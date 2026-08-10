"""
Sepeti Paylaş — ziyaretçi sepetini kısa bir linkle paylaşır.

- POST /shared-carts  (kimliksiz, rate-limitli): {items:[{product_id, variant_id?, quantity}]}
  → kısa id üretir, db.shared_carts'a yazar (60 gün TTL) → {id} döner.
  Link: {site}/sepet?paylasim={id}
- GET  /shared-carts/{id} (kimliksiz): kaydı okur ve her kalemi GÜNCEL ürün verisiyle
  zenginleştirir (ad/fiyat/indirim/görsel/stok/varyant). Fiyat OTORİTESİ her zaman
  sunucudur — paylaşan kişinin gördüğü fiyat değil, alıcının açtığı andaki fiyat geçerlidir.

Güvenlik: yazma ucunda kalem sayısı/adet tavanı + rate limit; okumada yalnız
katalogda var olan aktif ürünler döner (silinen ürün kalemden düşer). PII yok.
"""
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request

from .deps import db, logger, limiter

router = APIRouter(prefix="/shared-carts", tags=["shared-carts"])

_MAX_ITEMS = 50
_MAX_QTY = 20
_TTL_DAYS = 60


@router.post("")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def create_shared_cart(request: Request, payload: dict):
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Sepet boş")
    if len(items) > _MAX_ITEMS:
        raise HTTPException(status_code=400, detail="Sepette çok fazla kalem var")

    clean = []
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "").strip()
        if not pid or len(pid) > 64:
            continue
        vid = it.get("variant_id")
        vid = str(vid).strip()[:64] if vid else None
        try:
            qty = int(it.get("quantity") or 1)
        except Exception:
            qty = 1
        qty = max(1, min(qty, _MAX_QTY))
        clean.append({"product_id": pid, "variant_id": vid, "quantity": qty})
    if not clean:
        raise HTTPException(status_code=400, detail="Geçerli kalem bulunamadı")

    # Yalnız katalogda gerçekten var olan ürünler kaydedilir (id uydurma koruması).
    pids = list({c["product_id"] for c in clean})
    existing = set()
    async for p in db.products.find({"id": {"$in": pids}}, {"_id": 0, "id": 1}):
        existing.add(p["id"])
    clean = [c for c in clean if c["product_id"] in existing]
    if not clean:
        raise HTTPException(status_code=400, detail="Ürünler bulunamadı")

    share_id = secrets.token_urlsafe(8)  # ~11 karakter, tahmin edilemez
    now = datetime.now(timezone.utc)
    await db.shared_carts.insert_one({
        "id": share_id,
        "items": clean,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=_TTL_DAYS)).isoformat(),
    })
    logger.info(f"[shared-cart] oluşturuldu id={share_id} kalem={len(clean)}")
    return {"id": share_id}


@router.get("/{share_id}")
async def get_shared_cart(share_id: str):
    rec = await db.shared_carts.find_one({"id": share_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Paylaşılan sepet bulunamadı")
    exp = rec.get("expires_at") or ""
    if exp and exp < datetime.now(timezone.utc).isoformat():
        raise HTTPException(status_code=404, detail="Paylaşılan sepetin süresi dolmuş")

    # Otomatik kampanya rozetini TAZE hesapla (bayat campaign_discount_percent gösterme).
    try:
        from .products import _auto_campaigns_for_badges, _apply_campaign_badge
        _camps = await _auto_campaigns_for_badges()
    except Exception:
        _camps, _apply_campaign_badge = [], None

    out = []
    for it in rec.get("items") or []:
        p = await db.products.find_one(
            {"id": it.get("product_id")},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
             "campaign_discount_percent": 1, "images": 1, "thumbnail": 1, "stock": 1,
             "stock_code": 1, "barcode": 1, "category_id": 1, "category_ids": 1,
             "variants": 1, "is_active": 1},
        )
        if not p or p.get("is_active") is False:
            continue  # silinmiş/pasif ürün paylaşılan sepette görünmez
        if _apply_campaign_badge:
            _apply_campaign_badge(p, _camps)   # bayat/hayalet indirimi temizle (rozet ⊆ motor)
        # Kapak görseli: dict-form ({url}) veya düz string olabilir
        img = ""
        for im in (p.get("images") or []):
            if isinstance(im, dict):
                if im.get("is_size_table"):
                    continue
                img = im.get("url") or im.get("src") or ""
            else:
                img = im
            if img:
                break
        img = img or p.get("thumbnail") or ""

        variant = None
        if it.get("variant_id"):
            for v in (p.get("variants") or []):
                if str(v.get("id")) == str(it["variant_id"]):
                    variant = {
                        "id": v.get("id"), "size": v.get("size"), "color": v.get("color"),
                        "stock": v.get("stock"), "price_diff": v.get("price_diff") or v.get("price_adjustment") or 0,
                        "barcode": v.get("barcode"), "stock_code": v.get("stock_code"),
                    }
                    break
            if variant is None:
                continue  # varyant artık yok → kalemi düşür (yanlış beden eklenmesin)

        out.append({
            "quantity": it.get("quantity") or 1,
            "variant": variant,
            "product": {
                "id": p.get("id"), "name": p.get("name"), "slug": p.get("slug"),
                "price": p.get("price"), "sale_price": p.get("sale_price"),
                "campaign_discount_percent": p.get("campaign_discount_percent"),
                "images": [img] if img else [], "stock": p.get("stock"),
                "stock_code": p.get("stock_code"), "barcode": p.get("barcode"),
                "category_id": p.get("category_id"),
            },
        })
    if not out:
        raise HTTPException(status_code=404, detail="Paylaşılan sepetteki ürünler artık mevcut değil")
    return {"id": share_id, "items": out}
