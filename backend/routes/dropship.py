"""
dropship.py — Sipariş gelince tedarikçiden ne sipariş edileceğini gösterir.

Dropshipping akışı: müşteri bizden alır, biz aynı ürünü tedarikçiden alıp
müşteriye gönderttiririz. Bunun için sipariş ekranında BİZİM kodlarımız değil,
TEDARİKÇİNİN kodları gerekir — ana sitede aratacağımız kod odur.

Neden ayrı bir uç:
  • Sipariş oluşturma yolu (orders.py::create_order) ödeme ve stok
    değişmezlerini taşıyor; oraya alan eklemek o yolu riske atar. Bu uç
    siparişi HİÇ değiştirmez, yalnız okur ve eşleştirir.
  • Yalnız yönetim erişebilir; tedarikçi bilgisi müşteriye asla gitmez.

Eşleştirme: sipariş kalemi → ürün (product_id) → varyant (barkod/stok kodu
veya beden+renk). Varyant bulunamazsa ürün seviyesindeki tedarikçi bilgisi
döner ve `matched=False` ile işaretlenir — sessizce yanlış kod vermeyiz.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from .deps import db, require_admin

router = APIRouter(prefix="/dropship", tags=["dropship"])


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _find_variant(product: dict, item: dict) -> tuple[dict | None, str]:
    """Sipariş kalemine karşılık gelen varyantı bulur.

    Sırayla: variant_id → bizim barkodumuz → bizim stok kodumuz → beden+renk.
    Hangi yolla bulunduğunu da döndürür ki panelde şüpheli eşleşme görünsün.
    """
    variants = product.get("variants") or []
    if not variants:
        return None, "varyant yok"

    vid = item.get("variant_id")
    if vid:
        for v in variants:
            if v.get("id") == vid:
                return v, "variant_id"

    for key, label in (("barcode", "barkod"), ("stock_code", "stok kodu"), ("sku", "sku")):
        val = _norm(item.get(key))
        if val:
            for v in variants:
                if _norm(v.get("barcode")) == val or _norm(v.get("stock_code")) == val:
                    return v, label

    size = _norm(item.get("size") or item.get("variant_size"))
    color = _norm(item.get("color") or item.get("variant_color"))
    if size or color:
        for v in variants:
            if _norm(v.get("size")) == size and _norm(v.get("color")) == color:
                return v, "beden+renk"

    return None, "eşleşmedi"


@router.get("/orders/{order_id}")
async def order_procurement(order_id: str, _admin=Depends(require_admin)):
    """Bir siparişin tedarikçi alım listesi (YALNIZ yönetim)."""
    order = await db.orders.find_one(
        {"$or": [{"id": order_id}, {"order_number": order_id}]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")

    items = order.get("items") or []
    pids = [i.get("product_id") for i in items if i.get("product_id")]
    prods = {}
    if pids:
        async for p in db.products.find({"id": {"$in": pids}}, {"_id": 0}):
            prods[p["id"]] = p

    lines: List[Dict[str, Any]] = []
    total_cost = 0.0
    for it in items:
        p = prods.get(it.get("product_id")) or {}
        v, how = _find_variant(p, it) if p else (None, "ürün bulunamadı")
        qty = int(it.get("quantity") or it.get("qty") or 1)
        cost = float((v or {}).get("cost_price") or p.get("cost_price") or 0)
        total_cost += cost * qty

        lines.append({
            "name": it.get("name") or p.get("name") or "",
            "size": it.get("size") or (v or {}).get("size") or "",
            "color": it.get("color") or (v or {}).get("color") or "",
            "quantity": qty,
            # Tedarikçide arayacağımız kodlar:
            "supplier_barcode": (v or {}).get("supplier_barcode") or "",
            "supplier_stock_code": (v or {}).get("supplier_stock_code") or "",
            "supplier_model_ref": p.get("supplier_model_ref") or "",
            "supplier_url": p.get("supplier_url") or "",
            "supplier": p.get("supplier") or "",
            # Bizim kodlarımız (referans için):
            "our_barcode": (v or {}).get("barcode") or "",
            "our_stock_code": (v or {}).get("stock_code") or "",
            "cost_price": cost,
            "line_cost": round(cost * qty, 2),
            "sale_price": float(it.get("unit_price") or it.get("price") or 0),
            "matched_by": how,
            "matched": bool(v),
        })

    unmatched = [l for l in lines if not l["matched"]]
    revenue = float(order.get("total") or 0)
    return {
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "status": order.get("status"),
        "lines": lines,
        "total_cost": round(total_cost, 2),
        "revenue": revenue,
        # Kâr = satış - alış. Kargo ve komisyon dahil DEĞİL, kaba göstergedir.
        "gross_profit": round(revenue - total_cost, 2),
        "unmatched_count": len(unmatched),
        "warning": (
            f"{len(unmatched)} kalem tedarikçi koduyla eşleşmedi — elle kontrol edin."
            if unmatched else ""
        ),
    }


# ─────────────────── otomasyon uçları ───────────────────
# Dış yazılım şu döngüyü kurar:
#   1) GET  /dropship/pending            → tedarikçiye geçilmemiş siparişler
#   2) kendi tarafında tedarikçiye sipariş verir
#   3) POST /dropship/orders/{id}/mark-ordered  → işaretler
# Böylece aynı sipariş iki kez geçilmez.

# ÖDEME KURALI: yalnız GERÇEKTEN ödenmiş siparişler listelenir. Ödenmemiş
# siparişe tedarikçiden mal almak doğrudan zarardır; 3DS yarıda kalan veya
# havalesi gelmemiş siparişler bu listeye ASLA girmez.
_PAID_Q = {"payment_status": "paid", "status": {"$in": ["confirmed", "preparing", "processing"]}}


@router.get("/pending")
async def pending_orders(limit: int = 200, _admin=Depends(require_admin)):
    """Tedarikçiye henüz geçilmemiş, ÖDENMİŞ siparişlerin alım listesi."""
    limit = max(1, min(limit, 500))
    q = {**_PAID_Q, "supplier_ordered_at": {"$exists": False}}
    orders = await db.orders.find(q, {"_id": 0, "id": 1}).sort("created_at", 1).to_list(limit)

    out = []
    for o in orders:
        try:
            out.append(await order_procurement(o["id"], _admin=True))
        except HTTPException:
            continue
    return {"count": len(out), "orders": out}


@router.post("/orders/{order_id}/mark-ordered")
async def mark_ordered(order_id: str, payload: dict | None = None,
                       _admin=Depends(require_admin)):
    """Siparişi 'tedarikçiye geçildi' diye işaretler.

    Sipariş DURUMUNA ve ödeme alanlarına DOKUNMAZ — yalnız kendi izleme
    alanlarımızı yazar. Aynı sipariş ikinci kez işaretlenirse already=True
    döner ve üzerine yazılmaz (mükerrer sipariş uyarısı yazılımda görünsün).
    """
    o = await db.orders.find_one(
        {"$or": [{"id": order_id}, {"order_number": order_id}]},
        {"_id": 0, "id": 1, "supplier_ordered_at": 1})
    if not o:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if o.get("supplier_ordered_at"):
        return {"success": True, "already": True,
                "supplier_ordered_at": o["supplier_ordered_at"]}

    now = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": o["id"]}, {"$set": {
        "supplier_ordered_at": now,
        "supplier_order_ref": str((payload or {}).get("reference") or "")[:120],
    }})
    return {"success": True, "already": False, "supplier_ordered_at": now}
