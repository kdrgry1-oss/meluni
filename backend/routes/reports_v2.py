"""
Reports v2 — Yeni rapor seti (Stok Değer, Yavaş/Hızlı Satan, İade Oranı Uyarısı,
Kanal Bazlı Net Kâr) + product_costs (manuel maliyet) yönetimi.

Endpoints (admin-only):
  GET    /api/admin/reports2/stock-valuation                  — Toplam alış + satış değeri
  GET    /api/admin/reports2/slow-movers?days=60&min_stock=1  — N gündür satılmayan ürünler
  GET    /api/admin/reports2/fast-movers?days=30&top=50       — En hızlı satanlar (velocity)
  GET    /api/admin/reports2/return-rate?threshold=20         — İade oranı X% üzerinde olan ürünler
  GET    /api/admin/reports2/profit-by-channel?days=30        — Site/Trendyol/HB net kâr
  GET    /api/admin/reports2/dead-stock?days=90               — N gündür hiç satılmamış (ölü) stok

  Product costs (manuel maliyet):
  GET    /api/admin/product-costs?q=&page=&limit=
  POST   /api/admin/product-costs                            — { product_id, cost_price }
  POST   /api/admin/product-costs/bulk                        — toplu (Excel sonrası)
"""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import re
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field

from .deps import db, require_admin, generate_id


router = APIRouter(prefix="/admin/reports2", tags=["admin-reports-v2"])
costs_router = APIRouter(prefix="/admin/product-costs", tags=["product-costs"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


# Ciro/hız/kâr uçları için GEÇERLİ SATIŞ dışı durumlar: iptal + ÖDENMEMİŞ + iade grubu
# (reports.py:_EXCLUDED_STATUSES ile aynı disiplin). Pazaryeri siparişi daima confirmed+
# geldiği için pazaryeri satışı düşmez; yalnız gerçekten ödenmemiş/iptal/iade elenir.
_EXCLUDED = [
    "cancelled", "cancel_refunded",
    "awaiting_payment", "payment_failed", "pending", "payment_notified",
    "return_requested", "return_approved", "return_in_transit",
    "returned", "refunded", "partial_refunded",
]
# İADE ORANI paydası için: yalnız iptal + ödenmemiş elenir; iade edilen (returned/refunded/
# partial) siparişler SATILDI sayılıp paydada KALIR (iade oranı = iade/satılan).
_UNPAID_CANCEL = [
    "cancelled", "cancel_refunded",
    "awaiting_payment", "payment_failed", "pending", "payment_notified",
]
# İADE sayılan durumlar (return-rate pay'ı): order-seviyesi statü iade grubuna düştüyse.
_RETURN_STATUSES = ["returned", "refunded", "partial_refunded"]


async def _build_cost_map(product_ids: Optional[List[str]] = None) -> dict:
    """product_id → cost_price (manuel girilen). Eksik olanlar fallback olarak
    products.cost_price → products.price*0.5 olarak alınır."""
    q = {}
    if product_ids:
        q["product_id"] = {"$in": product_ids}
    cost_map: dict = {}
    async for c in db.product_costs.find(q, {"_id": 0}):
        cost_map[str(c.get("product_id"))] = float(c.get("cost_price") or 0)
    return cost_map


async def _product_cost_lookup(product_ids: Optional[List[str]] = None) -> dict:
    """product_id → BİRİM MALİYET, tek doğru zincirle: manuel product_costs >
    products.purchase_price (alış fiyatı) > products.cost_price (canlı senkron maliyeti).
    Son çare (price*0.5) çağıran tarafta uygulanır. Denetim bulgusu: eskiden
    stock-valuation yalnız purchase_price, profit-by-channel yalnız manuel maliyet
    okuyup cost_price'ı yok sayıyordu → entegrasyondan gelen ürünlerin maliyeti
    kaybolup kâr şişiyordu."""
    out: dict = dict(await _build_cost_map(product_ids))  # manuel öncelik
    q: dict = {}
    if product_ids:
        q["id"] = {"$in": product_ids}
    async for p in db.products.find(q, {"_id": 0, "id": 1, "cost_price": 1, "purchase_price": 1}):
        pid = str(p.get("id"))
        if out.get(pid, 0) > 0:
            continue
        c = float(p.get("purchase_price") or 0) or float(p.get("cost_price") or 0)
        if c > 0:
            out[pid] = c
    return out


# ---------------------------------------------------------------------------
# 1) STOK DEĞER RAPORU — Toplam alış + satış değeri
# ---------------------------------------------------------------------------
@router.get("/stock-valuation")
async def stock_valuation(
    brand: Optional[str] = None,
    category: Optional[str] = None,
    manufacturer: Optional[str] = None,
    _=Depends(require_admin),
):
    """Elinizdeki stoğun ALIŞ (purchase_price) ve SATIŞ (sale_price||price) değerini hesaplar.
    - Maliyet kaynağı: ürünün `purchase_price` alanı; yoksa manuel product_costs kaydı.
    - Hiç alış fiyatı olmayan ürünler TAHMİN EDİLMEZ; ayrı `missing_cost` listesinde döner
      (alış değeri toplamına katılmaz, satış değerine katılır).
    - Stok varyantlıysa gerçek adet = varyant stoklarının toplamı.
    """
    q: dict = {"$or": [{"stock": {"$gt": 0}}, {"variants.stock": {"$gt": 0}}]}
    if brand: q["brand"] = brand
    if category: q["category_name"] = category  # Y18: ürünler category değil category_name tutar
    if manufacturer: q["manufacturer"] = manufacturer

    cost_map = await _build_cost_map()  # product_costs (ikincil kaynak)

    total_units = 0
    valued_units = 0
    total_sale_value = 0.0
    total_cost_value = 0.0
    by_brand: dict = defaultdict(lambda: {"units": 0, "cost": 0.0, "sale": 0.0})
    by_category: dict = defaultdict(lambda: {"units": 0, "cost": 0.0, "sale": 0.0})
    missing: list = []
    cursor = db.products.find(q, {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1,
                                    "sale_price": 1, "purchase_price": 1, "cost_price": 1, "brand": 1,
                                    "category_name": 1, "stock_code": 1, "variants": 1})
    async for p in cursor:
        variants = p.get("variants") or []
        units = (sum(int(v.get("stock") or 0) for v in variants) if variants
                 else int(p.get("stock") or 0))
        if units <= 0:
            continue
        sale = float(p.get("sale_price") or 0) or float(p.get("price") or 0)
        # Maliyet zinciri: manuel product_costs > purchase_price > cost_price (canlı senkron).
        # Eskiden yalnız purchase_price+manuel okunup cost_price yok sayılıyordu → entegrasyon
        # ürünleri "maliyeti yok" sayılıp kâr şişiyordu.
        pp = float(p.get("purchase_price") or 0)
        cost = (float(cost_map.get(str(p.get("id"))) or 0)
                or pp
                or float(p.get("cost_price") or 0))

        total_units += units
        total_sale_value += units * sale
        b = p.get("brand") or "—"
        c = p.get("category_name") or "—"
        by_brand[b]["units"] += units
        by_brand[b]["sale"] += units * sale
        by_category[c]["units"] += units
        by_category[c]["sale"] += units * sale

        if cost > 0:
            valued_units += units
            total_cost_value += units * cost
            by_brand[b]["cost"] += units * cost
            by_category[c]["cost"] += units * cost
        else:
            missing.append({
                "id": p.get("id"), "name": p.get("name") or "—",
                "stock_code": p.get("stock_code") or "", "brand": b,
                "units": units, "sale_price": round(sale, 2),
                "sale_value": round(units * sale, 2),
            })

    margin = (total_sale_value - total_cost_value)
    margin_pct = (margin / total_sale_value * 100) if total_sale_value else 0
    missing.sort(key=lambda x: -x["sale_value"])

    return {
        "totals": {
            "units": total_units,
            "valued_units": valued_units,
            "missing_count": len(missing),
            "missing_units": total_units - valued_units,
            "cost_value": round(total_cost_value, 2),
            "sale_value": round(total_sale_value, 2),
            "potential_profit": round(margin, 2),
            "potential_margin_pct": round(margin_pct, 2),
        },
        "by_brand": [{"name": k, **{x: round(v[x], 2) if x != "units" else v[x] for x in v}}
                     for k, v in sorted(by_brand.items(), key=lambda kv: -kv[1]["sale"])[:50]],
        "by_category": [{"name": k, **{x: round(v[x], 2) if x != "units" else v[x] for x in v}}
                        for k, v in sorted(by_category.items(), key=lambda kv: -kv[1]["sale"])[:50]],
        "missing_cost": missing[:500],
        "checked_at": _now().isoformat(),
    }


async def _build_product_lookup() -> dict:
    """Ürünleri name-keyword'lere göre indeksler. Her ürün için:
    {'product': {...}, 'keywords': set(...)}.
    Lookup'ta sipariş kalem adı içinde bu keyword'lerin hepsi geçen ürün arar.
    """
    STOPWORDS = {"kadın", "erkek", "çocuk", "kız", "oğlan", "ürün", "yeni", "standart",
                 "fit", "the", "and", "ve", "ile", "de", "da", "için", "bir", "mevsimlik", "ince"}
    products = []
    async for p in db.products.find({"stock": {"$gt": 0}},
                                       {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1,
                                        "stock_code": 1, "barcode": 1, "brand": 1, "category_name": 1,
                                        "manufacturer": 1}):
        nm = (p.get("name") or "").lower()
        # Kelime tokenize + filtre
        words = {w for w in re.findall(r"[a-zçğıöşü]{4,}", nm) if w not in STOPWORDS}
        if not words:
            continue
        products.append({"p": p, "kw": words})
    return products


async def _velocity_smart_match(days: int, product_index: list) -> dict:
    """Her ürün için sipariş kalemleri içinden 'ürün isminin temel kelimelerini
    içeren' kalemlerin toplam adedini hesaplar.

    Returns: { product_id: daily_velocity }
    """
    since = _days_ago(days)
    # Tüm sipariş kalemlerini bir kere çek
    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "status": {"$nin": _EXCLUDED}}},
        {"$unwind": "$items"},
        {"$project": {"name": {"$ifNull": ["$items.product_name", "$items.name"]},
                       "qty": {"$ifNull": ["$items.quantity", 1]}}},
    ]
    items = []
    async for r in db.orders.aggregate(pipeline):
        nm = (r.get("name") or "").lower()
        if nm:
            items.append((nm, int(r["qty"])))

    velocity: dict = {}
    for entry in product_index:
        kw = entry["kw"]
        if not kw:
            continue
        sold = 0
        # En distinctive 2-3 kelime varsa daha sıkı eşleşme — ilk 4 kelime yeterli
        kw_list = list(kw)[:4]
        if not kw_list:
            continue
        for nm, qty in items:
            if all(k in nm for k in kw_list):
                sold += qty
        if sold > 0:
            velocity[str(entry["p"]["id"])] = sold / max(days, 1)
    return velocity


@router.get("/stockout-forecast")
async def stockout_forecast(
    velocity_days: int = Query(30, ge=7, le=180, description="Hız hesabı için bakılan gün sayısı"),
    horizon_days: int = Query(60, ge=7, le=365, description="Bu kadar gün içinde tükenecekleri göster"),
    target_cover_days: int = Query(60, ge=14, le=365, description="Üretim önerisi için hedef stok süresi"),
    min_velocity: float = Query(0.05, ge=0, description="Minimum günlük satış hızı"),
    _=Depends(require_admin),
):
    """Hızlı satan ürünler için stok tükenme tahmini + üretim önerisi.

    Mantık:
      • Son `velocity_days` gün satış verisinden günlük ortalama hız hesaplanır
      • Eşleştirme: doğrudan product_id → değilse ürün adındaki anahtar kelime kombinasyonu
      • Mevcut stok / hız = `tükenecek gün`
      • Üretim önerisi = (`target_cover_days` * hız) - mevcut stok (negatifse 0)
      • `horizon_days` içinde tükenecek olanlar listelenir
    """
    # 1) Doğrudan product_id eşleşmesi
    items_pid = await _velocity_aggregate(velocity_days)
    velocity_by_pid = {i["product_id"]: i["daily_velocity"] for i in items_pid}

    # 2) Ürün adı anahtar-kelime smart match (Trendyol/Ticimax orders için)
    product_index = await _build_product_lookup()
    velocity_by_smart = await _velocity_smart_match(velocity_days, product_index)

    result = []
    today = _now().date()
    for entry in product_index:
        p = entry["p"]
        pid = str(p["id"])
        velocity = max(velocity_by_pid.get(pid, 0.0), velocity_by_smart.get(pid, 0.0))
        if velocity < min_velocity:
            continue
        stock = int(p.get("stock") or 0)
        if stock <= 0:
            continue
        days_left = stock / velocity if velocity > 0 else 9999
        if days_left > horizon_days:
            continue
        stockout_dt = today + timedelta(days=int(days_left))
        suggested_qty = max(0, int(round(target_cover_days * velocity)) - stock)
        if days_left <= 14:
            severity = "critical"
        elif days_left <= 30:
            severity = "high"
        else:
            severity = "warning"

        result.append({
            "product_id": pid,
            "name": p.get("name") or "—",
            "stock_code": p.get("stock_code"),
            "brand": p.get("brand"),
            "category": p.get("category_name"),
            "manufacturer": p.get("manufacturer"),
            "current_stock": stock,
            "daily_velocity": round(velocity, 3),
            "days_until_stockout": int(days_left),
            "stockout_date": stockout_dt.isoformat(),
            "stockout_date_tr": stockout_dt.strftime("%d.%m.%Y"),
            "suggested_production_qty": suggested_qty,
            "suggested_production_value": round(suggested_qty * float(p.get("price") or 0), 2),
            "severity": severity,
        })

    result.sort(key=lambda x: (x["days_until_stockout"], -x["suggested_production_value"]))
    return {
        "velocity_days": velocity_days,
        "horizon_days": horizon_days,
        "target_cover_days": target_cover_days,
        "total": len(result),
        "summary": {
            "critical": sum(1 for r in result if r["severity"] == "critical"),
            "high": sum(1 for r in result if r["severity"] == "high"),
            "warning": sum(1 for r in result if r["severity"] == "warning"),
            "total_production_units": sum(r["suggested_production_qty"] for r in result),
            "total_production_value": round(sum(r["suggested_production_value"] for r in result), 2),
        },
        "items": result,
        "checked_at": _now().isoformat(),
    }


# ---------------------------------------------------------------------------
# 2) HIZLI / YAVAŞ SATAN ÜRÜNLER — velocity bazlı
# ---------------------------------------------------------------------------
async def _velocity_aggregate(days: int):
    since = _days_ago(days)
    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "status": {"$nin": _EXCLUDED}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": "$items.product_id",
            "name": {"$first": {"$ifNull": ["$items.name", "$items.product_name"]}},
            "sold_qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.quantity", 1]},
                                                 {"$ifNull": ["$items.price", 0]}]}},
            "order_count": {"$sum": 1},
        }},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        if not r["_id"]:
            continue
        out.append({
            "product_id": str(r["_id"]),
            "name": r.get("name") or "—",
            "sold_qty": int(r["sold_qty"]),
            "revenue": round(float(r["revenue"]), 2),
            "order_count": int(r["order_count"]),
            "daily_velocity": round(r["sold_qty"] / max(days, 1), 3),
        })
    return out


@router.get("/fast-movers")
async def fast_movers(
    days: int = Query(30, ge=1, le=365),
    top: int = Query(50, ge=1, le=500),
    _=Depends(require_admin),
):
    """En hızlı satan ürünler. velocity = adet / gün. Stok tükenme tahmini eklenir."""
    items = await _velocity_aggregate(days)
    items.sort(key=lambda x: -x["sold_qty"])
    items = items[:top]
    # Stok bilgisini ekle
    ids = [i["product_id"] for i in items]
    stock_map = {}
    _proj = {"_id": 0, "id": 1, "name": 1, "stock": 1, "stock_code": 1, "price": 1,
             "brand": 1, "category_name": 1, "variants.urun_id": 1}
    async for p in db.products.find({"id": {"$in": ids}}, _proj):
        stock_map[str(p["id"])] = p
    # Eski (Ticimax/pazaryeri) siparişlerde product_id yerel UUID değil varyant urun_id'si
    # olabilir — çözülemeyenler varyant numarasından eşlenir ki ad/SKU boş kalmasın.
    missing = [pid for pid in ids if pid not in stock_map]
    if missing:
        async for p in db.products.find({"variants.urun_id": {"$in": missing}}, _proj):
            for vv in (p.get("variants") or []):
                u = str(vv.get("urun_id") or "")
                if u in missing:
                    stock_map.setdefault(u, p)
    for it in items:
        p = stock_map.get(it["product_id"]) or {}
        if p.get("name") and (not it.get("name") or it["name"] == "—"):
            it["name"] = p["name"]
        it["stock"] = int(p.get("stock") or 0)
        it["stock_code"] = p.get("stock_code")
        it["price"] = float(p.get("price") or 0)
        it["brand"] = p.get("brand")
        it["category"] = p.get("category_name")
        # Stok tükenme tahmini (gün)
        it["days_until_stockout"] = int(it["stock"] / it["daily_velocity"]) if it["daily_velocity"] > 0 else None
    return {"days": days, "items": items}


@router.get("/slow-movers")
async def slow_movers(
    days: int = Query(60, ge=1, le=365),
    min_stock: int = Query(1, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _=Depends(require_admin),
):
    """N gün içinde N adetten az satan ama stoğu olan ürünler.
    `days` = bakılan periyot, `min_stock` = minimum stok eşiği.
    """
    # Önce satılanları topla
    sold = {it["product_id"]: it for it in await _velocity_aggregate(days)}
    items = []
    cursor = db.products.find({"stock": {"$gte": min_stock}},
                               {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1, "stock_code": 1,
                                "brand": 1, "category_name": 1, "created_at": 1})
    async for p in cursor:
        pid = str(p["id"])
        sold_info = sold.get(pid)
        sold_qty = sold_info["sold_qty"] if sold_info else 0
        # "Yavaş satan" tanımı: günlük velocity < 0.1 (yani 30 günde 3 adetten az)
        velocity = (sold_qty / days) if days else 0
        if velocity < 0.1:
            items.append({
                "product_id": pid,
                "name": p.get("name"),
                "stock_code": p.get("stock_code"),
                "stock": int(p.get("stock") or 0),
                "sold_qty_period": sold_qty,
                "daily_velocity": round(velocity, 3),
                "price": float(p.get("price") or 0),
                "brand": p.get("brand"),
                "category": p.get("category_name"),
                "tied_value": round(int(p.get("stock") or 0) * float(p.get("price") or 0), 2),
            })
    items.sort(key=lambda x: -x["tied_value"])
    return {"days": days, "min_stock": min_stock, "total": len(items), "items": items[:limit]}


@router.get("/dead-stock")
async def dead_stock(
    days: int = Query(90, ge=30, le=730),
    _=Depends(require_admin),
):
    """N gündür HİÇ satılmamış stokta olan ürünler — likidasyon/kampanya adayları."""
    sold_ids = set()
    pipeline = [
        # İptal/ödenmemiş siparişte geçen ürün "satıldı" SAYILMAZ (aksi halde gerçek ölü
        # stok gizlenirdi). İade edilenler hareket sayılır (paydada değil, sold-set'te kalır).
        {"$match": {"created_at": {"$gte": _days_ago(days)}, "status": {"$nin": _UNPAID_CANCEL}}},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.product_id"}},
    ]
    async for r in db.orders.aggregate(pipeline):
        if r["_id"]: sold_ids.add(str(r["_id"]))

    items = []
    cursor = db.products.find({"stock": {"$gt": 0}},
                               {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1, "stock_code": 1, "brand": 1})
    async for p in cursor:
        if str(p["id"]) in sold_ids:
            continue
        items.append({
            "product_id": str(p["id"]),
            "name": p.get("name"),
            "stock_code": p.get("stock_code"),
            "stock": int(p.get("stock") or 0),
            "price": float(p.get("price") or 0),
            "brand": p.get("brand"),
            "tied_value": round(int(p.get("stock") or 0) * float(p.get("price") or 0), 2),
        })
    items.sort(key=lambda x: -x["tied_value"])
    return {"days": days, "total": len(items), "items": items[:500]}


# ---------------------------------------------------------------------------
# 3) İADE ORANI UYARISI — eşik aşan ürünler
# ---------------------------------------------------------------------------
@router.get("/return-rate")
async def return_rate(
    threshold: float = Query(20.0, ge=0, le=100, description="Yüzde eşiği (örn: 20)"),
    days: int = Query(90, ge=7, le=365),
    min_orders: int = Query(5, ge=1, description="En az kaç sipariş olmalı"),
    _=Depends(require_admin),
):
    """Belirli periyotta iade oranı `threshold`% üzerinde olan ürünleri listeler."""
    since = _days_ago(days)
    pipeline = [
        # Payda (total_sold): iptal/ödenmemiş HARİÇ; iade edilenler SATILDI sayılır (paydada kalır).
        # Pay (returned_qty): order-seviyesi statü iade grubuna düşenler (returned/refunded/partial).
        # NOT: sitedeki KISMİ iadeler siparişi açık bırakabildiğinden bu order-statü tabanlı oran
        # bir ALT SINIR'dır; ürün-bazlı ayrıntılı iade için /reports/returns/by-product kullanılır.
        {"$match": {"created_at": {"$gte": since}, "status": {"$nin": _UNPAID_CANCEL}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": "$items.product_id",
            "name": {"$first": "$items.name"},
            "total_sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "returned_qty": {"$sum": {"$cond": [{"$in": ["$status", _RETURN_STATUSES]},
                                                  {"$ifNull": ["$items.quantity", 1]}, 0]}},
        }},
        {"$match": {"total_sold": {"$gte": min_orders}}},
    ]
    items = []
    async for r in db.orders.aggregate(pipeline):
        sold = int(r.get("total_sold") or 0)
        ret = int(r.get("returned_qty") or 0)
        if sold == 0:
            continue
        rate = (ret / sold) * 100
        if rate >= threshold:
            items.append({
                "product_id": str(r["_id"]),
                "name": r.get("name") or "—",
                "sold": sold,
                "returned": ret,
                "return_rate_pct": round(rate, 2),
                "severity": "critical" if rate >= 40 else ("high" if rate >= 30 else "warning"),
            })
    items.sort(key=lambda x: -x["return_rate_pct"])
    return {"threshold": threshold, "days": days, "total": len(items), "items": items}


# ---------------------------------------------------------------------------
# 4) KANAL BAZLI NET KÂR — Site / Trendyol / HB ...
# ---------------------------------------------------------------------------
@router.get("/profit-by-channel")
async def profit_by_channel(
    days: int = Query(30, ge=1, le=365),
    _=Depends(require_admin),
):
    """Her kanal için: satış, maliyet, komisyon (varsa), kargo, iade, net kâr."""
    since = _days_ago(days)

    # Pazaryeri komisyon varsayılanları (yüzde) — gelecekte ayrı config'den okunabilir
    DEFAULT_COMMISSION_PCT = {
        "trendyol": 18.0, "hepsiburada": 17.0, "n11": 12.0, "amazon": 15.0,
        "temu": 5.0, "ciceksepeti": 12.0, "pttavm": 8.0,
        "site": 3.0, "manual": 0.0,
    }

    cost_map = await _product_cost_lookup()  # manuel > cost_price > purchase_price

    pipeline = [
        {"$match": {"created_at": {"$gte": since}, "status": {"$nin": _EXCLUDED}}},
        {"$project": {
            # Y16: Kanal `platform` alanında tutulur (marketplace/source değil). Ayrıca 2-arg
            # $ifNull kullanılır (3-arg Mongo 5.0 gerektiriyordu, eski sürümde patlıyordu).
            "channel": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]},
            "status": 1, "items": 1, "total": 1, "shipping_cost": 1,
        }},
    ]
    # Kanal adı normalizasyonu: mağaza siparişleri platform='facette' taşır → komisyon
    # haritasındaki 'site'; admin manuel siparişler → 'manual'.
    _CH_ALIAS = {"facette": "site", "": "site", "web": "site",
                 "admin_manual": "manual", "admin": "manual", "manuel": "manual"}
    rows: dict = defaultdict(lambda: {
        "orders": 0, "revenue": 0.0, "cost": 0.0, "shipping": 0.0,
        "commission": 0.0, "refunds": 0.0,
    })
    async for o in db.orders.aggregate(pipeline):
        ch = (o.get("channel") or "site").lower()
        ch = _CH_ALIAS.get(ch, ch)
        rows[ch]["orders"] += 1
        rev = float(o.get("total") or 0)
        rows[ch]["revenue"] += rev
        rows[ch]["shipping"] += float(o.get("shipping_cost") or 0)
        # Maliyet: items üzerinden cost_map ile çarpım
        for it in (o.get("items") or []):
            pid = str(it.get("product_id") or "")
            qty = int(it.get("quantity") or 1)
            price = float(it.get("price") or 0)
            cost = cost_map.get(pid)
            if cost is None:
                cost = round(price * 0.5, 2)
            rows[ch]["cost"] += qty * cost
        # Komisyon (yaklaşık)
        rows[ch]["commission"] += rev * (DEFAULT_COMMISSION_PCT.get(ch, 5.0) / 100)
        # İade
        if o.get("status") == "returned":
            rows[ch]["refunds"] += rev

    out = []
    for ch, r in rows.items():
        net = r["revenue"] - r["cost"] - r["commission"] - r["refunds"]
        margin = (net / r["revenue"] * 100) if r["revenue"] else 0
        out.append({
            "channel": ch,
            "orders": r["orders"],
            "revenue": round(r["revenue"], 2),
            "cost": round(r["cost"], 2),
            "commission": round(r["commission"], 2),
            "shipping": round(r["shipping"], 2),
            "refunds": round(r["refunds"], 2),
            "net_profit": round(net, 2),
            "margin_pct": round(margin, 2),
            "commission_pct": DEFAULT_COMMISSION_PCT.get(ch, 5.0),
        })
    out.sort(key=lambda x: -x["net_profit"])

    # Toplam satır
    totals = {
        "orders": sum(r["orders"] for r in out),
        "revenue": round(sum(r["revenue"] for r in out), 2),
        "cost": round(sum(r["cost"] for r in out), 2),
        "commission": round(sum(r["commission"] for r in out), 2),
        "refunds": round(sum(r["refunds"] for r in out), 2),
        "net_profit": round(sum(r["net_profit"] for r in out), 2),
    }
    if totals["revenue"]:
        totals["margin_pct"] = round(totals["net_profit"] / totals["revenue"] * 100, 2)
    else:
        totals["margin_pct"] = 0
    return {"days": days, "items": out, "totals": totals}


# ---------------------------------------------------------------------------
# Manuel maliyet (product_costs) yönetimi
# ---------------------------------------------------------------------------
class ProductCostIn(BaseModel):
    product_id: str
    cost_price: float = Field(..., ge=0)
    currency: str = "TRY"


@costs_router.get("")
async def list_costs(
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    only_missing: bool = Query(False, description="Sadece maliyeti girilmemiş ürünleri göster"),
    _=Depends(require_admin),
):
    """Ürün listesi + manuel girilmiş maliyet. only_missing=true ile sadece eksik olanlar."""
    cost_map = await _build_cost_map()
    pq: dict = {}
    if q:
        pq["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"stock_code": {"$regex": q, "$options": "i"}},
            {"sku": {"$regex": q, "$options": "i"}},
        ]
    skip = (page - 1) * limit
    cursor = db.products.find(pq, {"_id": 0, "id": 1, "name": 1, "stock_code": 1,
                                    "price": 1, "stock": 1, "brand": 1, "category_name": 1}) \
                       .skip(skip).limit(limit if not only_missing else limit * 3)
    items = []
    async for p in cursor:
        pid = str(p["id"])
        cost = cost_map.get(pid)
        if only_missing and cost is not None:
            continue
        price = float(p.get("price") or 0)
        items.append({
            "product_id": pid,
            "name": p.get("name"),
            "stock_code": p.get("stock_code"),
            "stock": int(p.get("stock") or 0),
            "price": price,
            "cost_price": cost,
            "brand": p.get("brand"),
            "category": p.get("category_name"),
            "margin_pct": round((price - cost) / price * 100, 2) if (cost and price) else None,
        })
        if len(items) >= limit:
            break
    total = await db.products.count_documents(pq)
    return {"items": items, "total": total, "page": page, "limit": limit}


@costs_router.post("")
async def upsert_cost(payload: ProductCostIn, admin=Depends(require_admin)):
    now = _now().isoformat()
    await db.product_costs.update_one(
        {"product_id": payload.product_id},
        {"$set": {
            "product_id": payload.product_id,
            "cost_price": payload.cost_price,
            "currency": payload.currency,
            "updated_by": admin.get("email"),
            "updated_at": now,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"ok": True, "product_id": payload.product_id, "cost_price": payload.cost_price}


class BulkCostIn(BaseModel):
    items: List[ProductCostIn]


@costs_router.post("/bulk")
async def bulk_upsert_costs(payload: BulkCostIn, admin=Depends(require_admin)):
    if not payload.items:
        return {"ok": True, "count": 0}
    now = _now().isoformat()
    from pymongo import UpdateOne
    ops = []
    for it in payload.items:
        ops.append(UpdateOne(
            {"product_id": it.product_id},
            {"$set": {
                "product_id": it.product_id,
                "cost_price": it.cost_price,
                "currency": it.currency,
                "updated_by": admin.get("email"),
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        ))
    if ops:
        await db.product_costs.bulk_write(ops)
    return {"ok": True, "count": len(ops)}
