"""
Reports v2 — Yeni rapor seti (Stok Değer, Yavaş/Hızlı Satan, İade Oranı Uyarısı,
Kanal Bazlı Net Kâr) + product_costs (manuel maliyet) yönetimi.

Endpoints (admin-only):
  GET    /api/admin/reports2/stock-valuation                  — Toplam alış + satış değeri
  GET    /api/admin/reports2/slow-movers?days=60&min_stock=1  — N gündür satılmayan ürünler
  GET    /api/admin/reports2/fast-movers?days=30&top=50       — En hızlı satanlar (velocity)
  GET    /api/admin/reports2/return-rate?threshold=20         — İade oranı X% üzerinde olan ürünler
  GET    /api/admin/reports2/profit-by-channel?days=30        — Site/Trendyol/HB tahmini brüt marj
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

from .deps import db, require_admin, generate_id, tr_range_to_utc, require_permission
from .report_dedup import (
    canonical_order_stages,
    effective_order_date_match,
    load_dup_dep,
    merge_match,
)


async def _warm_category_resolver():
    """Ürün tipi kategori haritasını (seçili ALT kategori, promo hariç) istek başında yükler."""
    try:
        from .report_category import load_category_resolver
        await load_category_resolver()
    except Exception:
        pass


def _catname(p: dict):
    """Raporlarda gösterilen kategori — report_category kuralı (yüklenemediyse birincil ad)."""
    from .report_category import _CACHE
    fn = _CACHE.get("fn")
    return fn(p) if fn else ((p or {}).get("category_name") or None)


# IAM-01: require_admin önbellek ısıtıcılarından (DB okuma) ÖNCE — yetkisiz istek uç koduna dokunmaz.
router = APIRouter(prefix="/admin/reports2", tags=["admin-reports-v2"],
                   dependencies=[Depends(require_admin), Depends(load_dup_dep), Depends(_warm_category_resolver)])
costs_router = APIRouter(prefix="/admin/product-costs", tags=["product-costs"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _today_tr():
    """Europe/Istanbul (UTC+3, yaz saati yok) takvim günü. Tükenme tarihi, ölü stok
    penceresi vb. TR gününe göre hesaplanır; UTC günü TR 00:00-03:00 arasında bir gün
    geride kalıyordu."""
    return (_now() + timedelta(hours=3)).date()


def _calendar_window(days: int) -> tuple[str, str]:
    """Son N Türkiye takvim gününü, bugün dahil, ortak UTC sınırlarıyla döndür."""
    today_tr = _today_tr()
    start_day = today_tr - timedelta(days=max(1, days) - 1)
    return tr_range_to_utc(start_day.isoformat(), today_tr.isoformat())


def _intval(v) -> int:
    """Güvenli int — string/None stok değerlerini tolere eder."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _fval(v) -> float:
    """Güvenli float — string/None fiyat/maliyet değerlerini tolere eder."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _eff_sale_price(p: dict) -> float:
    """Ürünün GEÇERLİ satış fiyatı — orders.py::_eff_unit_price ile aynı kural:
    0 < sale_price < price ise indirimli fiyat, aksi halde liste fiyatı (price).
    Liste fiyatı yoksa (0) sale_price kullanılır."""
    price = _fval(p.get("price"))
    sp = _fval(p.get("sale_price"))
    if price <= 0:
        return sp
    return sp if 0 < sp < price else price


def _effective_unit_cost(manual, p: dict) -> tuple:
    """Rapor hesaplarının TEK maliyet kuralı (_product_cost_lookup ile aynı zincir):
    manuel product_costs (>0) > products.purchase_price (alış) > products.cost_price
    (senkron). 0 / boş değer "maliyet yok" sayılır (D4 kuralı). Döner: (maliyet, kaynak)
    kaynak ∈ {"manuel", "alis", "senkron", None}."""
    m = _fval(manual)
    if m > 0:
        return m, "manuel"
    pp = _fval(p.get("purchase_price"))
    if pp > 0:
        return pp, "alis"
    cp = _fval(p.get("cost_price"))
    if cp > 0:
        return cp, "senkron"
    return 0.0, None


def _effective_stock(p: dict) -> int:
    """O5 DENETİM FIX: EFEKTİF stok = varyant varsa Σ(variants[].stock), yoksa top-level
    'stock'. reports.py::stock_report ile AYNI formül. Eskiden bu uçlar yalnız top-level
    'stock'a bakıyordu → varyantlı ürün (S:0,M:8,L:2) top-level stock=0 taşıdığından
    ölü-stok'a düşüyor / hızlı-satan'da stok 0 görünüyordu."""
    variants = p.get("variants") or []
    if variants:
        return sum(_intval(v.get("stock")) for v in variants)
    return _intval(p.get("stock"))


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
def _canonical_order_window(days: int, excluded: list[str]) -> list:
    """One date/order population shared by every v2 order-backed report."""
    start, end = _calendar_window(days)
    return [
        {"$match": merge_match(effective_order_date_match(start, end))},
        *canonical_order_stages(),
        {"$match": {"status": {"$nin": excluded}}},
    ]


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
    async for p in db.products.find(q, {"_id": 0, "id": 1, "cost_price": 1,
                                        "purchase_price": 1, "barcode": 1,
                                        "variants.barcode": 1, "variants.urun_id": 1}):
        pid = str(p.get("id"))
        c = (float(out.get(pid) or 0)
             or float(p.get("purchase_price") or 0)
             or float(p.get("cost_price") or 0))
        if c > 0:
            out[pid] = c
            aliases = [p.get("barcode")]
            for variant in p.get("variants") or []:
                aliases.extend([variant.get("barcode"), variant.get("urun_id")])
            for alias in aliases:
                if alias not in (None, ""):
                    out.setdefault(str(alias).strip(), c)
    return out


# ---------------------------------------------------------------------------
# 1) STOK DEĞER RAPORU — Toplam alış + satış değeri
# ---------------------------------------------------------------------------
@router.get("/stock-valuation")
async def stock_valuation(
    brand: Optional[str] = None,
    category: Optional[str] = None,
    manufacturer: Optional[str] = None,
    _=Depends(require_permission("reports.stock")),
):
    """Elinizdeki stoğun ALIŞ ve SATIŞ değerini hesaplar.
    - Alış (maliyet) zinciri: Maliyet Girişi (manuel product_costs) > ürünün Alış fiyatı
      (purchase_price) > senkron maliyet (cost_price). 0 değer "yok" sayılır.
    - Satış fiyatı: geçerli satış fiyatı (0 < sale_price < price ise indirimli, yoksa liste).
    - Hiç maliyeti olmayan ürünler TAHMİN EDİLMEZ; ayrı `missing_cost` listesinde döner
      (alış değeri toplamına katılmaz, satış değerine katılır).
    - Potansiyel kâr ve marj YALNIZ maliyeti bilinen ürünlerden hesaplanır
      (valued_sale_value − cost_value); maliyetsiz ürünün satış değeri kâra karışmaz.
    - Stok varyantlıysa gerçek adet = varyant stoklarının toplamı.
    """
    # O11 DENETİM FIX: silinmiş (çöp kutusu) + pasif ürünler stok değerine KATILMASIN —
    # reports.py::stock_report ile aynı süzgeç. Eskiden bunlar sayılıp toplam değer şişiyordu.
    q: dict = {
        "$or": [{"stock": {"$gt": 0}}, {"variants.stock": {"$gt": 0}}],
        "is_deleted": {"$ne": True},
        "is_active": {"$ne": False},
    }
    if brand: q["brand"] = brand
    if category: q["category_name"] = category  # Y18: ürünler category değil category_name tutar
    if manufacturer: q["manufacturer"] = manufacturer

    cost_map = await _build_cost_map()  # product_costs (ikincil kaynak)

    total_units = 0
    valued_units = 0
    total_sale_value = 0.0
    valued_sale_value = 0.0   # yalnız maliyeti bilinen ürünlerin satış değeri (kâr/marj tabanı)
    total_cost_value = 0.0
    by_brand: dict = defaultdict(lambda: {"units": 0, "cost": 0.0, "sale": 0.0, "valued_sale": 0.0})
    by_category: dict = defaultdict(lambda: {"units": 0, "cost": 0.0, "sale": 0.0, "valued_sale": 0.0})
    missing: list = []
    from .report_category import load_category_resolver
    _cat_of = await load_category_resolver()
    cursor = db.products.find(q, {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1,
                                    "sale_price": 1, "purchase_price": 1, "cost_price": 1, "brand": 1,
                                    "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1, "stock_code": 1, "variants": 1})
    async for p in cursor:
        variants = p.get("variants") or []
        units = (sum(int(v.get("stock") or 0) for v in variants) if variants
                 else int(p.get("stock") or 0))
        if units <= 0:
            continue
        # Satış değeri BEDEN bazında: varyant fiyat farkı (price_adjustment/price_diff — ör. eritme
        # L bedeni 440) dahil — orders._eff_unit_price ile aynı kural. Eskiden tüm bedenler ürün
        # fiyatıyla sayılıp potansiyel satış değeri şişiyordu. `sale` = adet-ağırlıklı ort. birim.
        _base = _eff_sale_price(p)
        if variants:
            _sv = 0.0
            for _v in variants:
                _q = int(_v.get("stock") or 0)
                if _q > 0:
                    _sv += _q * max(0.0, _base + (_fval(_v.get("price_adjustment")) or _fval(_v.get("price_diff"))))
            sale = _sv / units if units else _base
        else:
            sale = _base
        # Maliyet zinciri: manuel product_costs > purchase_price > cost_price (canlı senkron).
        # Eskiden yalnız purchase_price+manuel okunup cost_price yok sayılıyordu → entegrasyon
        # ürünleri "maliyeti yok" sayılıp kâr şişiyordu.
        cost, _src = _effective_unit_cost(cost_map.get(str(p.get("id"))), p)

        total_units += units
        total_sale_value += units * sale
        b = p.get("brand") or "—"
        c = _cat_of(p)
        by_brand[b]["units"] += units
        by_brand[b]["sale"] += units * sale
        by_category[c]["units"] += units
        by_category[c]["sale"] += units * sale

        if cost > 0:
            valued_units += units
            valued_sale_value += units * sale
            total_cost_value += units * cost
            by_brand[b]["cost"] += units * cost
            by_brand[b]["valued_sale"] += units * sale
            by_category[c]["cost"] += units * cost
            by_category[c]["valued_sale"] += units * sale
            by_category[c]["valued_units"] = by_category[c].get("valued_units", 0) + units
        else:
            missing.append({
                "id": p.get("id"), "name": p.get("name") or "—",
                "stock_code": p.get("stock_code") or "", "brand": b,
                "units": units, "sale_price": round(sale, 2),
                "sale_value": round(units * sale, 2),
            })

    # Kâr ve marj YALNIZ maliyeti bilinen ürünler üzerinden: maliyetsiz ürünün satış
    # değeri kâra (%100 marjla) karışmasın. Maliyetsiz ürünler missing_cost'ta ayrıca döner.
    margin = (valued_sale_value - total_cost_value)
    margin_pct = (margin / valued_sale_value * 100) if valued_sale_value else 0
    missing.sort(key=lambda x: -x["sale_value"])

    return {
        "totals": {
            "units": total_units,
            "valued_units": valued_units,
            "missing_count": len(missing),
            "missing_units": total_units - valued_units,
            "cost_value": round(total_cost_value, 2),
            "sale_value": round(total_sale_value, 2),
            "valued_sale_value": round(valued_sale_value, 2),
            "missing_sale_value": round(total_sale_value - valued_sale_value, 2),
            "potential_profit": round(margin, 2),
            "potential_margin_pct": round(margin_pct, 2),
        },
        "by_brand": [{"name": k, **{x: round(v[x], 2) if x != "units" else v[x] for x in v}}
                     for k, v in sorted(by_brand.items(), key=lambda kv: -kv[1]["sale"])[:50]],
        # Kategoriler ADET'e göre (çoktan aza); valued_units = maliyeti bilinen adet (ort. alış tabanı).
        "by_category": [{"name": k, **{x: (round(v[x], 2) if x not in ("units", "valued_units") else v[x]) for x in v}}
                        for k, v in sorted(by_category.items(), key=lambda kv: (-kv[1]["units"], -kv[1]["sale"]))[:80]],
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
    # O5 DENETİM FIX: varyant-stoklu ürünler (top-level stock=0) de dahil edilir; efektif stok
    # için variants.stock projeksiyona eklenir.
    async for p in db.products.find({"$and": [
            {"$or": [{"stock": {"$gt": 0}}, {"variants.stock": {"$gt": 0}}]},
            {"is_deleted": {"$ne": True}}, {"is_active": {"$ne": False}}]},
                                       {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1,
                                        "sale_price": 1,
                                        "stock_code": 1, "barcode": 1, "brand": 1, "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1,
                                        "manufacturer": 1, "variants.stock": 1}):
        nm = (p.get("name") or "").lower()
        # Kelime tokenize + filtre
        words = {w for w in re.findall(r"[a-zçğıöşü]{4,}", nm) if w not in STOPWORDS}
        if not words:
            continue
        products.append({"p": p, "kw": words})
    return products


async def _catalog_link_keys() -> tuple:
    """Kanonik ürün motorunun (reports.top_products) bir sipariş kalemini kataloğa
    bağlarken kullandığı anahtarlar: silinmemiş ürünlerin id'leri + ürün/varyant barkodları."""
    ids: set = set()
    barcodes: set = set()
    async for p in db.products.find({"is_deleted": {"$ne": True}},
                                    {"_id": 0, "id": 1, "barcode": 1, "variants.barcode": 1}):
        if p.get("id") not in (None, ""):
            ids.add(str(p.get("id")))
        if p.get("barcode") not in (None, ""):
            barcodes.add(str(p.get("barcode")).strip())
        for v in p.get("variants") or []:
            if v.get("barcode") not in (None, ""):
                barcodes.add(str(v.get("barcode")).strip())
    return ids, barcodes


def _name_match_velocity(lines: list, product_index: list, days: int) -> dict:
    """Ad eşleştirmesi (saf fonksiyon). `lines`: [(ad_küçük_harf, adet)] — YALNIZ
    kataloğa bağlanamayan kalemler. Ürünün TÜM anahtar kelimeleri kalem adında geçmelidir
    (deterministik: eskiden `list(set)[:4]` süreçten sürece farklı 4 kelimeyi seçip ayırt
    edici kelimeyi — ör. 'çizgili' — düşürebiliyor, başka ürünün satırlarını sayıyordu)."""
    velocity: dict = {}
    for entry in product_index:
        kw_list = sorted(entry.get("kw") or ())
        if not kw_list:
            continue
        sold = 0
        for nm, qty in lines:
            if all(k in nm for k in kw_list):
                sold += qty
        if sold > 0:
            velocity[str(entry["p"]["id"])] = sold / max(days, 1)
    return velocity


async def _velocity_smart_match(days: int, product_index: list) -> dict:
    """Kataloğa BAĞLANAMAYAN eski/pazaryeri sipariş kalemleri için ad eşleştirmesiyle
    günlük hız. Barkodu veya product_id'si katalogdaki bir ürüne bağlanan kalemler burada
    SAYILMAZ — onları kanonik motor (iptal/iade netleştirilmiş) zaten ilgili ürüne yazar;
    ad eşleştirmesi onları başka ürüne de sayabiliyordu.

    Returns: { product_id: daily_velocity }
    """
    link_ids, link_bcs = await _catalog_link_keys()
    # Tüm sipariş kalemlerini bir kere çek
    pipeline = [
        *_canonical_order_window(days, _EXCLUDED),
        {"$unwind": "$items"},
        {"$project": {"name": {"$ifNull": ["$items.product_name", "$items.name"]},
                       "pid": {"$ifNull": ["$items.product_id", ""]},
                       "bc": {"$ifNull": ["$items.barcode", ""]},
                       "qty": {"$ifNull": ["$items.quantity", 1]}}},
    ]
    lines = []
    async for r in db.orders.aggregate(pipeline):
        if str(r.get("bc") or "").strip() in link_bcs or str(r.get("pid") or "") in link_ids:
            continue  # kanonik motor bu kalemi kataloğa bağlıyor
        nm = (r.get("name") or "").lower()
        if nm:
            lines.append((nm, _intval(r.get("qty")) or 1))
    return _name_match_velocity(lines, product_index, days)


def _severity_for_days(days_int: int) -> str:
    """Kalan gün (tam sayı, ekranda görünen) → durum. KPI başlıkları (≤14g / ≤30g) ile aynı."""
    if days_int <= 14:
        return "critical"
    if days_int <= 30:
        return "high"
    return "warning"


@router.get("/stockout-forecast")
async def stockout_forecast(
    velocity_days: int = Query(30, ge=7, le=180, description="Hız hesabı için bakılan gün sayısı"),
    horizon_days: int = Query(60, ge=7, le=365, description="Bu kadar gün içinde tükenecekleri göster"),
    target_cover_days: int = Query(60, ge=14, le=365, description="Üretim önerisi için hedef stok süresi"),
    min_velocity: float = Query(0.05, ge=0, description="Minimum günlük satış hızı"),
    _=Depends(require_permission("reports.stock")),
):
    """Hızlı satan ürünler için stok tükenme tahmini + üretim önerisi.

    Mantık:
      • Son `velocity_days` TR günü NET satıştan (iptal & iade hariç) günlük ortalama hız
      • Eşleştirme: kanonik ürün motoru (barkod/product_id) → yalnız orada kaydı olmayan
        ürün için kataloğa bağlanamayan eski kalemlerden ad eşleştirmesi (velocity_source)
      • Mevcut stok / hız = `tükenecek gün` (tam gün, aşağı yuvarlı; durum eşikleri de bu değerle)
      • Üretim önerisi = (`target_cover_days` * hız) - mevcut stok (negatifse 0)
      • `suggested_production_value` = öneri adedi × geçerli SATIŞ fiyatı (maliyet değil)
      • `horizon_days` içinde tükenecek olanlar listelenir
    """
    # 1) Kanonik motor (barkod/product_id → katalog ürünü; iptal/iade netleştirilmiş)
    items_pid, canon_seen = await _canonical_velocity(velocity_days)
    velocity_by_pid = {i["product_id"]: i["daily_velocity"] for i in items_pid}

    # 2) Ürün adı anahtar-kelime eşleşmesi — YALNIZ kataloğa bağlanamayan eski kalemler için
    product_index = await _build_product_lookup()
    velocity_by_smart = await _velocity_smart_match(velocity_days, product_index)

    result = []
    today = _today_tr()  # tükenme tarihi TR gününe göre
    for entry in product_index:
        p = entry["p"]
        pid = str(p["id"])
        # Kanonik motor bu ürünün satışını gördüyse (net 0 olsa bile — ör. 1 satış 1 iade)
        # YALNIZ kanonik hız kullanılır; ad eşleşmesi yalnız kanonik kaydı olmayan ürüne.
        if pid in velocity_by_pid or pid in canon_seen:
            velocity = velocity_by_pid.get(pid, 0.0)
            velocity_source = "canonical"
        else:
            velocity = velocity_by_smart.get(pid, 0.0)
            velocity_source = "name_match"
        if velocity <= 0 or velocity < min_velocity:
            continue
        stock = _effective_stock(p)  # O5: varyant stoğu dahil efektif stok
        if stock <= 0:
            continue
        # Ekranda görünen tam gün (aşağı yuvarlı) durum, KPI, tarih ve ufuk süzgecinde
        # TEK değer olarak kullanılır — "14g" görünen satır Kritik (≤14g) sayılır.
        days_int = int(stock / velocity)
        if days_int > horizon_days:
            continue
        stockout_dt = today + timedelta(days=days_int)
        suggested_qty = max(0, int(round(target_cover_days * velocity)) - stock)
        severity = _severity_for_days(days_int)

        result.append({
            "product_id": pid,
            "name": p.get("name") or "—",
            "stock_code": p.get("stock_code"),
            "brand": p.get("brand"),
            "category": _catname(p),
            "manufacturer": p.get("manufacturer"),
            "current_stock": stock,
            "daily_velocity": round(velocity, 3),
            "velocity_source": velocity_source,
            "days_until_stockout": days_int,
            "stockout_date": stockout_dt.isoformat(),
            "stockout_date_tr": stockout_dt.strftime("%d.%m.%Y"),
            "suggested_production_qty": suggested_qty,
            # SATIŞ fiyatıyla (geçerli satış fiyatı × adet) — maliyet DEĞİL.
            "suggested_production_value": round(suggested_qty * _eff_sale_price(p), 2),
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
def _velocity_rows(data: dict, days: int) -> tuple:
    """top_products çıktısından (satırlar, kanonik_görülen_idler). Satırlar yalnız net
    adedi > 0 olanlar; `seen` ise kataloğa bağlanmış VE pencerede herhangi bir satış
    hareketi (brüt > 0: net + iptal + iade) olan ürünler — net 0 da olsa kanonik kayıttır."""
    rows, seen = [], set()
    for r in data.get("items", []):
        pid = str(r.get("product_id") or "")
        if not pid:
            continue
        qty = _intval(r.get("qty"))
        if r.get("catalog_match") and _intval(r.get("gross_qty")) > 0:
            seen.add(pid)
        if qty <= 0:
            continue
        rows.append({
            "product_id": pid,
            "name": r.get("name") or "—",
            "sold_qty": qty,
            "revenue": round(_fval(r.get("revenue")), 2),
            "order_count": _intval(r.get("orders")),
            "catalog_match": bool(r.get("catalog_match")),
            "daily_velocity": round(qty / max(days, 1), 3),
        })
    return rows, seen


async def _canonical_velocity(days: int) -> tuple:
    # v1 ürün raporu barkod → varyant → parent ürün çözümünün kanonik sahibidir.
    # Böylece hızlı/yavaş satan, Excel ve ürün raporu aynı adet/sipariş/ciroyu gösterir.
    from .reports import top_products
    today_tr = _today_tr()
    data = await top_products(
        limit=5000,
        start_date=(today_tr - timedelta(days=days - 1)).isoformat(),
        end_date=today_tr.isoformat(),
        source=None,
        current_user={},
    )
    return _velocity_rows(data, days)


async def _velocity_aggregate(days: int):
    rows, _seen = await _canonical_velocity(days)
    return rows


@router.get("/fast-movers")
async def fast_movers(
    days: int = Query(30, ge=1, le=365),
    top: int = Query(50, ge=1, le=500),
    _=Depends(require_permission("reports.stock")),
):
    """En hızlı satan ürünler. velocity = adet / gün. Stok tükenme tahmini eklenir."""
    items = await _velocity_aggregate(days)
    items.sort(key=lambda x: -x["sold_qty"])
    items = items[:top]
    # Stok bilgisini ekle
    ids = [i["product_id"] for i in items]
    stock_map = {}
    _proj = {"_id": 0, "id": 1, "name": 1, "stock": 1, "stock_code": 1, "price": 1,
             "sale_price": 1, "brand": 1, "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1, "variants.urun_id": 1, "variants.stock": 1}
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
        p = stock_map.get(it["product_id"])
        if not p:
            # Katalogda karşılığı bulunamayan (eski/pazaryeri) kalem: stok BİLİNMİYOR.
            # Eskiden {} ile stok 0 / "Tükenir 0 gün" gösteriliyordu (ürün tükenmiş gibi).
            it["catalog_match"] = False
            it["stock"] = None
            it["stock_code"] = None
            it["price"] = None
            it["brand"] = None
            it["category"] = None
            it["days_until_stockout"] = None
            continue
        it["catalog_match"] = True
        if p.get("name") and (not it.get("name") or it["name"] == "—"):
            it["name"] = p["name"]
        it["stock"] = _effective_stock(p)  # O5: varyant stoğu dahil efektif stok
        it["stock_code"] = p.get("stock_code")
        it["price"] = _eff_sale_price(p)
        it["brand"] = p.get("brand")
        it["category"] = _catname(p)
        # Stok tükenme tahmini (gün)
        it["days_until_stockout"] = int(it["stock"] / it["daily_velocity"]) if it["daily_velocity"] > 0 else None
    return {"days": days, "items": items,
            "unmatched_count": sum(1 for it in items if not it.get("catalog_match"))}


@router.get("/slow-movers")
async def slow_movers(
    days: int = Query(60, ge=1, le=365),
    min_stock: int = Query(1, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _=Depends(require_permission("reports.stock")),
):
    """N gün içinde N adetten az satan ama stoğu olan ürünler.
    `days` = bakılan periyot, `min_stock` = minimum stok eşiği.
    `tied_value` = stok × geçerli SATIŞ fiyatı (satış fiyatıyla stok değeri);
    `tied_cost_value` = stok × birim maliyet (manuel > alış > senkron; yoksa None).
    Liste tied_value'ya göre azalan sıralıdır; `total` tüm eşleşen ürün sayısıdır,
    `items` ilk `limit` kadarıdır (kesilme `total > len(items)` ile görünür).
    """
    # Önce satılanları topla
    sold = {it["product_id"]: it for it in await _velocity_aggregate(days)}
    cost_map = await _product_cost_lookup()
    items = []
    # O5 DENETİM FIX: varyant-stoklu ürünler (top-level stock=0) de aranır; efektif stok
    # eşiği Python tarafında uygulanır (variants projeksiyona eklendi).
    cursor = db.products.find({"$and": [
            {"$or": [{"stock": {"$gte": min_stock}}, {"variants.stock": {"$gt": 0}}]},
            {"is_deleted": {"$ne": True}}, {"is_active": {"$ne": False}}]},
                               {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1, "sale_price": 1,
                                "stock_code": 1, "brand": 1, "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1, "created_at": 1,
                                "variants.stock": 1})
    async for p in cursor:
        pid = str(p["id"])
        eff_stock = _effective_stock(p)  # O5: varyant stoğu dahil
        if eff_stock < min_stock:
            continue
        sold_info = sold.get(pid)
        sold_qty = sold_info["sold_qty"] if sold_info else 0
        # "Yavaş satan" tanımı: günlük velocity < 0.1 (yani 30 günde 3 adetten az)
        velocity = (sold_qty / days) if days else 0
        if velocity < 0.1:
            price = _eff_sale_price(p)
            unit_cost = _fval(cost_map.get(pid))
            items.append({
                "product_id": pid,
                "name": p.get("name"),
                "stock_code": p.get("stock_code"),
                "stock": eff_stock,
                "sold_qty_period": sold_qty,
                "daily_velocity": round(velocity, 3),
                "price": price,
                "brand": p.get("brand"),
                "category": _catname(p),
                "tied_value": round(eff_stock * price, 2),
                "tied_cost_value": round(eff_stock * unit_cost, 2) if unit_cost > 0 else None,
            })
    items.sort(key=lambda x: -x["tied_value"])
    return {"days": days, "min_stock": min_stock, "total": len(items),
            "shown": min(len(items), limit), "items": items[:limit]}


@router.get("/dead-stock")
async def dead_stock(
    days: int = Query(90, ge=30, le=730),
    _=Depends(require_permission("reports.stock")),
):
    """N gündür HİÇ satılmamış stokta olan ürünler — likidasyon/kampanya adayları.
    Pencere son N TR takvim günüdür (bugün dahil; yavaş satan penceresiyle aynı).
    `tied_value` satış fiyatıyla, `tied_cost_value` maliyetle stok değeridir."""
    # Kanonik ürün motoru barkod/varyant/productCode → yerel parent ürün köprüsünü uygular.
    # İade edilen ürün hareket sayılır; yalnız iptaller satış hareketi değildir.
    from .reports import top_products
    today_tr = _today_tr()
    sold_data = await top_products(
        limit=5000,
        start_date=(today_tr - timedelta(days=days - 1)).isoformat(),
        end_date=today_tr.isoformat(), source=None, current_user={},
    )
    sold_ids = {str(r.get("product_id")) for r in sold_data.get("items", [])
                if r.get("product_id") and
                int(r.get("gross_qty") or 0) - int(r.get("cancel_qty") or 0) > 0}
    cost_map = await _product_cost_lookup()

    items = []
    # O5 DENETİM FIX: varyant-stoklu ürünler (top-level stock=0) de dahil; efektif stok kullanılır.
    cursor = db.products.find({"$and": [
            {"$or": [{"stock": {"$gt": 0}}, {"variants.stock": {"$gt": 0}}]},
            {"is_deleted": {"$ne": True}}, {"is_active": {"$ne": False}}]},
                               {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1, "sale_price": 1,
                                "stock_code": 1, "brand": 1, "variants.stock": 1})
    async for p in cursor:
        if str(p["id"]) in sold_ids:
            continue
        eff_stock = _effective_stock(p)  # O5: varyant stoğu dahil
        if eff_stock <= 0:
            continue
        price = _eff_sale_price(p)
        unit_cost = _fval(cost_map.get(str(p["id"])))
        items.append({
            "product_id": str(p["id"]),
            "name": p.get("name"),
            "stock_code": p.get("stock_code"),
            "stock": eff_stock,
            "price": price,
            "brand": p.get("brand"),
            "tied_value": round(eff_stock * price, 2),
            "tied_cost_value": round(eff_stock * unit_cost, 2) if unit_cost > 0 else None,
        })
    items.sort(key=lambda x: -x["tied_value"])
    return {"days": days, "total": len(items), "shown": min(len(items), 500), "items": items[:500]}


# ---------------------------------------------------------------------------
# 3) İADE ORANI UYARISI — eşik aşan ürünler
# ---------------------------------------------------------------------------
@router.get("/return-rate")
async def return_rate(
    threshold: float = Query(20.0, ge=0, le=100, description="Yüzde eşiği (örn: 20)"),
    days: int = Query(90, ge=7, le=365),
    min_orders: int = Query(1, ge=1, description="En az kaç FARKLI sipariş olmalı (sipariş sayısı)"),
    min_units: int = Query(1, ge=1, description="En az kaç ADET satılmış olmalı (iptal hariç satış adedi = `sold`)"),
    _=Depends(require_permission("reports.returns")),
):
    """Belirli periyotta kesin kalem-bazlı iade oranı eşiğini aşan ürünleri listeler.

    Kaynak, v1 ürün-iade raporuyla aynıdır: onaylı site iadeleri ve Accepted Trendyol
    claim kalemleri. Ana oran Trendyol ile aynı şekilde İade / Brüt Satış'tır;
    iptaller iade adedine eklenmez ancak brüt satış paydasında kalır.

    Süzgeçler: `min_units` ADET (tablodaki "İptal Hariç Satış Ürün Adedi" = sold) üzerinden,
    `min_orders` ise farklı sipariş sayısı (order_count) üzerinden uygulanır; ikisi ayrı
    anlamdadır. Eskiden ekrandaki "Min Satılan Adet" kutusu sipariş sayısını süzüyordu.
    """
    from .reports import returns_by_product
    today_tr = _today_tr()
    start_day = today_tr - timedelta(days=days - 1)
    exact = await returns_by_product(
        start_date=start_day.isoformat(), end_date=today_tr.isoformat(), limit=500,
        current_user={},
    )
    items = []
    for r in exact.get("items", []):
        sold = int(r.get("sold") or 0)
        gross_sold = int(r.get("gross_sold") or sold)
        ret = int(r.get("returned") or 0)
        rate = r.get("trendyol_return_rate_pct")
        order_count = int(r.get("order_count") or 0)
        if (sold >= min_units and order_count >= min_orders
                and rate is not None and float(rate) >= threshold):
            items.append({
                "product_id": str(r.get("product_id") or ""),
                "name": r.get("product_name") or "—",
                "sold": sold,
                "gross_sold": gross_sold,
                "returned": ret,
                "order_count": order_count,
                "return_rate_pct": r.get("return_rate_pct"),
                "trendyol_return_rate_pct": round(float(rate), 2),
                "severity": "critical" if rate >= 40 else ("high" if rate >= 30 else "warning"),
            })
    items.sort(key=lambda x: -x["trendyol_return_rate_pct"])
    return {"threshold": threshold, "days": days, "min_units": min_units,
            "min_orders": min_orders, "total": len(items), "items": items}


# ---------------------------------------------------------------------------
# 4) KANAL BAZLI BRÜT MARJ — Site / Trendyol / HB ...
# ---------------------------------------------------------------------------
# Kanal adı normalizasyonu: mağaza siparişleri platform='meluni' taşır → komisyon
# haritasındaki 'site'; admin manuel siparişler → 'manual'.
_CH_ALIAS = {"meluni": "site", "": "site", "web": "site",
             "admin_manual": "manual", "admin": "manual", "manuel": "manual"}
# Pazaryeri komisyon varsayılanları (yüzde). Kârlılık ayarı (profitability-config →
# commission_pct) varsa o önceliklidir — iki ekran aynı oranı kullanır.
_DEFAULT_COMMISSION_PCT = {
    "trendyol": 18.0, "hepsiburada": 17.0, "n11": 12.0, "amazon": 15.0,
    "temu": 5.0, "ciceksepeti": 12.0, "pttavm": 8.0,
    "site": 3.0, "manual": 0.0,
}


def _norm_channel(ch) -> str:
    ch = str(ch or "site").strip().lower()
    return _CH_ALIAS.get(ch, ch)


def _order_channel(o: dict) -> str:
    """reports._channel_expr'in Python karşılığı (+ _CH_ALIAS): bilinen pazaryeri
    platform/marketplace alanından; meluni/web/boş → site; diğerleri olduğu gibi."""
    from .reports import _MARKETPLACES
    platform = str((o or {}).get("platform") or "").strip().lower()
    marketplace = str((o or {}).get("marketplace") or "").strip().lower()
    if platform in _MARKETPLACES:
        ch = platform
    elif marketplace in _MARKETPLACES:
        ch = marketplace
    elif platform in ("", "meluni", "web"):
        ch = "site"
    else:
        ch = platform
    return _norm_channel(ch)


def _channel_margin_rows(product_rows: list, cost_map: dict, fallback_ratio: float,
                         commission_pct: dict, orders_by_ch: dict, shipping_by_ch: dict) -> tuple:
    """Kanal brüt marjı (saf fonksiyon — birim test edilir).

    product_rows: reports.top_products satırları (kanonik NET: iptal, kısmi iptal ve
      onaylı/kabul edilmiş iadeler düşülmüş). Kanal kırılımı reconciled_platform_breakdown
      ile ürünün net ciro/adet toplamına kuruşu kuruşuna eşitlenir (Kârlılık ile aynı).
    Maliyet: birim maliyet (manuel > alış > senkron) × net adet; maliyet yoksa net ciro ×
      fallback_ratio (tahmini — `estimated_cost_revenue` alanında görünür).
    """
    from .report_dedup import reconciled_platform_breakdown
    rows: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0, "cost": 0.0,
                                      "estimated_cost_revenue": 0.0})
    for product in product_rows:
        if _intval(product.get("qty")) <= 0 and _fval(product.get("revenue")) <= 0:
            continue
        unit_cost = _fval(cost_map.get(str(product.get("product_id") or "")))
        for part in reconciled_platform_breakdown(product):
            qty = max(0, _intval(part.get("qty")))
            rev = max(0.0, _fval(part.get("revenue")))
            ch = _norm_channel(part.get("platform"))
            dst = rows[ch]
            dst["units"] += qty
            dst["revenue"] += rev
            if unit_cost > 0:
                dst["cost"] += qty * unit_cost
            else:
                dst["cost"] += rev * fallback_ratio
                dst["estimated_cost_revenue"] += rev
    out = []
    for ch in set(rows) | set(orders_by_ch) | set(shipping_by_ch):
        r = rows[ch]
        pct = float(commission_pct.get(ch, 5.0))
        commission = r["revenue"] * pct / 100
        shipping = float(shipping_by_ch.get(ch) or 0)
        gross = r["revenue"] - r["cost"] - commission - shipping
        out.append({
            "channel": ch,
            "orders": int(orders_by_ch.get(ch) or 0),
            "units": r["units"],
            "revenue": round(r["revenue"], 2),
            "cost": round(r["cost"], 2),
            "estimated_cost_revenue": round(r["estimated_cost_revenue"], 2),
            "commission": round(commission, 2),
            "shipping": round(shipping, 2),
            "gross_margin": round(gross, 2),
            "margin_pct": round(gross / r["revenue"] * 100, 2) if r["revenue"] else 0,
            "commission_pct": pct,
        })
    out.sort(key=lambda x: -x["gross_margin"])
    totals = {k: round(sum(r[k] for r in out), 2)
              for k in ("revenue", "cost", "estimated_cost_revenue", "commission",
                        "shipping", "gross_margin")}
    totals["orders"] = sum(r["orders"] for r in out)
    totals["units"] = sum(r["units"] for r in out)
    totals["margin_pct"] = (round(totals["gross_margin"] / totals["revenue"] * 100, 2)
                            if totals["revenue"] else 0)
    return out, totals


@router.get("/profit-by-channel")
async def profit_by_channel(
    days: int = Query(30, ge=1, le=365),
    _=Depends(require_permission("reports.financial")),
):
    """Her kanal için: NET ciro, maliyet, komisyon, kargo → KANAL BRÜT MARJI.

    K2 DENETİM FIX: Bu bir NET KÂR DEĞİLDİR: yalnız kanal maliyetleri (ürün maliyeti +
    komisyon + kargo) düşülür; KDV, kurumlar vergisi, reklam, hizmet bedeli DÜŞÜLMEZ.
    O4: kargo düşülür.

    DENETİM (kanal kârı "Net Ciro"): ciro eskiden yalnız sipariş STATÜSÜNE göre süzülüp
    orders.total toplanıyordu → statüsü henüz iadeye dönmemiş kabul edilmiş Trendyol
    claim'leri, onaylı site iadeleri ve kısmi iptaller ciroda kalıyordu (Trendyol ~%17
    şişkin). Artık ciro/adet, satış raporunun kanonik ürün motorundan (reports.top_products,
    reports.profitability ile aynı) gelir: iptal, kısmi iptal ve onaylı/kabul edilmiş
    iadeler düşülmüş NET satıştır. Sipariş sayısı da satış raporunun kanonik kovalarından
    (reports._bucket_orders → net sipariş) alınır.
    """
    from .reports import (top_products, _channel_expr, _profitability_config,
                          _canonical_report_context, _bucket_orders)
    today_tr = _today_tr()
    start_day = today_tr - timedelta(days=days - 1)

    cfg = await _profitability_config()
    commission_pct = {**_DEFAULT_COMMISSION_PCT, **(cfg.get("commission_pct") or {})}
    try:
        fallback_ratio = float(cfg.get("cog_fallback_ratio"))
    except (TypeError, ValueError):
        fallback_ratio = 0.5
    if not (0 <= fallback_ratio <= 1):
        fallback_ratio = 0.5

    # 1) NET ciro/adet — kanonik ürün motoru (satış raporu / Kârlılık ile aynı kaynak)
    canonical = await top_products(limit=5000, start_date=start_day.isoformat(),
                                   end_date=today_tr.isoformat(), source=None, current_user={})
    cost_map = await _product_cost_lookup()  # manuel > purchase_price > cost_price (+ barkod/urun_id)

    # 2) NET sipariş sayısı — satış raporunun kanonik sipariş kovaları (aynı TR penceresi)
    s, e = _calendar_window(days)
    orders, closed, open_ = await _canonical_report_context(s, e, None)
    by_ch_orders: dict = defaultdict(list)
    for o in orders:
        by_ch_orders[_order_channel(o)].append(o)
    orders_by_ch = {ch: _bucket_orders(lst, closed, open_)["net"]["orders"]
                    for ch, lst in by_ch_orders.items()}
    orders_by_ch = {ch: n for ch, n in orders_by_ch.items() if n > 0}

    # 3) Kargo — geçerli (iptal/iade/ödenmemiş statüsünde olmayan) siparişlerin kargo bedeli
    shipping_by_ch: dict = defaultdict(float)
    async for o in db.orders.aggregate([
        *_canonical_order_window(days, _EXCLUDED),
        {"$project": {"channel": _channel_expr(), "shipping_cost": 1}},
    ]):
        shipping_by_ch[_norm_channel(o.get("channel"))] += _fval(o.get("shipping_cost"))
    shipping_by_ch = {ch: v for ch, v in shipping_by_ch.items() if v}

    out, totals = _channel_margin_rows(canonical.get("items", []), cost_map, fallback_ratio,
                                       commission_pct, orders_by_ch, shipping_by_ch)
    return {"days": days,
            "range": {"start": start_day.isoformat(), "end": today_tr.isoformat()},
            "items": out, "totals": totals,
            "cost_fallback_ratio": fallback_ratio,
            "warning": {
                "code": "channel_gross_margin_scope",
                "message": ("Tahmini kanal brüt marjıdır. Net Ciro; iptaller, kısmi iptaller ve "
                            "onaylanmış/kabul edilmiş iadeler düşülmüş net satıştır (ürün satış "
                            "raporuyla aynı kaynak). Henüz sonuçlanmamış iade talepleri ciroda "
                            "kalır. KDV, kurumlar vergisi, reklam ve hizmet bedeli düşülmez; "
                            "maliyeti girilmemiş ürünlerde maliyet = net ciro × "
                            f"%{fallback_ratio * 100:.0f} varsayılır."),
            }}


# ---------------------------------------------------------------------------
# Manuel maliyet (product_costs) yönetimi
# ---------------------------------------------------------------------------
class ProductCostIn(BaseModel):
    product_id: str
    # 0 TL maliyet REDDEDİLİR: rapor hesapları (D4 kuralı) 0'ı "maliyet yok" sayıp alış/
    # senkron maliyete ya da tahmine düşer; 0 kaydı ekranda "mevcut" görünüp hesapta yok
    # sayılıyordu. Maliyet bilinmiyorsa kayıt girilmemeli.
    cost_price: float = Field(..., gt=0)
    currency: str = "TRY"


def _cost_rows(products: list, manual_map: dict, only_missing: bool) -> list:
    """Maliyet Girişi satırları (saf fonksiyon). `cost_price` = rapor hesaplarının kullandığı
    ETKİN birim maliyet (manuel > alış > senkron; 0 = yok), `manual_cost` = manuel kayıt,
    `cost_source` = etkin maliyetin kaynağı. `only_missing` → etkin maliyeti OLMAYANLAR.
    Sıra deterministiktir (ad, id) — sayfalama tekrar/atlama yapmaz."""
    rows = []
    for p in products:
        pid = str(p.get("id"))
        manual = manual_map.get(pid)
        cost, source = _effective_unit_cost(manual, p)
        if only_missing and cost > 0:
            continue
        list_price = _fval(p.get("price"))
        eff_price = _eff_sale_price(p)
        rows.append({
            "product_id": pid,
            "name": p.get("name"),
            "stock_code": p.get("stock_code"),
            "stock": _effective_stock(p),
            "price": list_price,
            "effective_price": eff_price,
            "cost_price": cost if cost > 0 else None,
            "cost_source": source,
            "manual_cost": _fval(manual) if _fval(manual) > 0 else None,
            "brand": p.get("brand"),
            "category": _catname(p),
            "margin_pct": (round((eff_price - cost) / eff_price * 100, 2)
                           if (cost > 0 and eff_price > 0) else None),
        })
    rows.sort(key=lambda r: (str(r.get("name") or "").casefold(), r["product_id"]))
    return rows


@costs_router.get("")
async def list_costs(
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    only_missing: bool = Query(False, description="Sadece etkin maliyeti (manuel/alış/senkron) olmayan ürünler"),
    _=Depends(require_permission("reports.financial")),
):
    """Ürün listesi + ETKİN maliyet (rapor hesaplarıyla aynı kural: manuel > alış >
    senkron). only_missing=true ile yalnız hiç maliyeti olmayanlar. Süzgeç sayfalamadan
    ÖNCE uygulanır; `total` süzülmüş kümenin sayısıdır."""
    cost_map = await _build_cost_map()
    pq: dict = {}
    if q:
        pq["$or"] = [
            {"name": {"$regex": re.escape(q), "$options": "i"}},
            {"stock_code": {"$regex": re.escape(q), "$options": "i"}},
            {"sku": {"$regex": re.escape(q), "$options": "i"}},
        ]
    products = [p async for p in db.products.find(
        pq, {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "price": 1, "sale_price": 1,
             "purchase_price": 1, "cost_price": 1, "stock": 1, "variants.stock": 1,
             "brand": 1, "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1})]
    rows = _cost_rows(products, cost_map, only_missing)
    skip = (page - 1) * limit
    return {"items": rows[skip:skip + limit], "total": len(rows), "page": page, "limit": limit}


@costs_router.post("")
async def upsert_cost(payload: ProductCostIn, admin=Depends(require_permission("reports.financial"))):
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
async def bulk_upsert_costs(payload: BulkCostIn, admin=Depends(require_permission("reports.financial"))):
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


# =============================================================================
# TARİH BAZLI MAL ALIMI (kullanıcı, 2026-09-30): "hangi tarih aralığında ne kadar mal almışız,
# o dönemde aldığımız ürünler ne kadar potansiyele sahip". Kaynak: İmalat Takip DEPO
# SEVKİYATLARI (tarih + renk|beden adet). Alış fiyatı ürün kartındaki alış fiyatıyla AYNI
# kural (_effective_unit_cost; ürün bulunamazsa imalat birim fiyatı × 1,10 KDV — ürün açarken
# kullanılan kural). Potansiyel satış = adet × ürünün GEÇERLİ satış fiyatı (_eff_sale_price).
# SALT OKUR.
# =============================================================================
def _norm_color(s) -> str:
    import unicodedata as _ud
    s = str(s or "").strip().replace("İ", "i").replace("I", "ı").lower()
    s = _ud.normalize("NFKD", s)
    return "".join(c for c in s if not _ud.combining(c)).replace("ı", "i")


@router.get("/purchases")
async def purchases_by_date(start_date: str = Query(...), end_date: str = Query(...),
                            _=Depends(require_permission("reports.financial"))):
    sd, ed = str(start_date)[:10], str(end_date)[:10]
    if not sd or not ed or sd > ed:
        raise HTTPException(status_code=400, detail="Geçersiz tarih aralığı")
    recs = await db.manufacturing.find(
        {"deliveries": {"$elemMatch": {"date": {"$gte": sd, "$lte": ed + "T99"}}}},
        {"_id": 0, "id": 1, "code": 1, "order_no": 1, "partner_name": 1, "product_name": 1,
         "stock_code": 1, "unit_price": 1, "product_id": 1, "created_product_ids": 1, "deliveries": 1},
    ).to_list(5000)
    # Ürün eşleme: bağlı ürün id'leri + aynı stok kodlu ürünler (renk başına ayrı kart olabilir)
    pids, codes = set(), set()
    for r in recs:
        for x in [r.get("product_id")] + list(r.get("created_product_ids") or []):
            if x:
                pids.add(str(x))
        if r.get("stock_code"):
            codes.add(str(r["stock_code"]).strip())
    prods = await db.products.find(
        {"$or": [{"id": {"$in": list(pids)}}, {"stock_code": {"$in": list(codes)}}],
         "is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "color": 1, "price": 1, "sale_price": 1,
         "purchase_price": 1, "cost_price": 1, "urun_karti_id": 1, "variants.color": 1,
         "variants.size": 1, "variants.price_adjustment": 1, "variants.price_diff": 1}).to_list(20000)
    # Yedek eşleme: imalat ürün adı + renk = ürün kartı adı (ürün kartı imalattan bağımsız
    # açıldıysa bağlantı/stok kodu tutmayabiliyor — ör. "Elen Yarasa Kol Basic" + "Beyaz").
    _names = set()
    for r in recs:
        base = str(r.get("product_name") or "").strip()
        if not base:
            continue
        _names.add(base)
        for d in (r.get("deliveries") or []):
            for key in (d.get("items") or {}):
                if "|" in str(key):
                    _names.add(f"{base} {str(key).split('|', 1)[0].strip()}")
    if _names:
        _seen = {p.get("id") for p in prods}
        async for p in db.products.find(
                {"name": {"$in": list(_names)}, "is_deleted": {"$ne": True}},
                {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "color": 1, "price": 1, "sale_price": 1,
                 "purchase_price": 1, "cost_price": 1, "urun_karti_id": 1, "variants.color": 1,
                 "variants.size": 1, "variants.price_adjustment": 1, "variants.price_diff": 1}):
            if p.get("id") not in _seen:
                prods.append(p)
    by_name = {_norm_color(p.get("name")): p for p in prods if p.get("name")}
    cost_map = await _build_cost_map([p["id"] for p in prods if p.get("id")])
    by_id = {str(p["id"]): p for p in prods}
    by_code: dict = defaultdict(list)
    for p in prods:
        by_code[str(p.get("stock_code") or "").strip()].append(p)

    def _pick(rec, color):
        cands = [by_id[x] for x in ([rec.get("product_id")] + list(rec.get("created_product_ids") or []))
                 if x and str(x) in by_id]
        cands += [p for p in by_code.get(str(rec.get("stock_code") or "").strip(), []) if p not in cands]
        nc = _norm_color(color)
        if nc:
            for p in cands:
                pcs = {_norm_color(p.get("color"))} | {_norm_color(v.get("color")) for v in (p.get("variants") or [])}
                if nc in pcs:
                    return p
        base = str(rec.get("product_name") or "").strip()
        byn = by_name.get(_norm_color(f"{base} {color}".strip())) or by_name.get(_norm_color(base))
        if byn:
            return byn
        return cands[0] if cands else None

    agg: dict = {}
    for r in recs:
        for d in (r.get("deliveries") or []):
            dd = str(d.get("date") or "")[:10]
            if not dd or dd < sd or dd > ed:
                continue
            for key, q in (d.get("items") or {}).items():
                try:
                    q = int(float(q or 0))
                except Exception:
                    q = 0
                if q <= 0:
                    continue
                color = str(key).split("|", 1)[0] if "|" in str(key) else ""
                size = str(key).split("|", 1)[1] if "|" in str(key) else str(key)
                p = _pick(r, color)
                unit_cost = 0.0
                if p:
                    unit_cost, _src = _effective_unit_cost(cost_map.get(str(p.get("id"))), p)
                if unit_cost <= 0:
                    unit_cost = round(_fval(r.get("unit_price")) * 1.10, 2)
                unit_sale = _eff_sale_price(p) if p else 0.0
                if p and unit_sale:
                    for _v in (p.get("variants") or []):
                        if str(_v.get("size") or "").strip().upper() == size.strip().upper():
                            unit_sale = max(0.0, unit_sale + (_fval(_v.get("price_adjustment")) or _fval(_v.get("price_diff"))))
                            break
                k = (r.get("id"), p.get("id") if p else color)
                a = agg.setdefault(k, {
                    "product": (p or {}).get("name") or (f"{r.get('product_name') or ''} {color}".strip()),
                    "card_id": str((p or {}).get("urun_karti_id") or ""),
                    "stock_code": r.get("stock_code") or "", "partner": r.get("partner_name") or "",
                    "order_no": r.get("order_no") or r.get("code") or "",
                    "first_date": dd, "last_date": dd, "units": 0, "cost": 0.0, "sale": 0.0,
                    "matched": bool(p), "sale_price": round(unit_sale, 2), "unit_cost": round(unit_cost, 2),
                })
                a["first_date"] = min(a["first_date"], dd)
                a["last_date"] = max(a["last_date"], dd)
                a["units"] += q
                a["cost"] += q * unit_cost
                a["sale"] += q * unit_sale
    rows = []
    for a in agg.values():
        a["cost"] = round(a["cost"], 2)
        a["sale"] = round(a["sale"], 2)
        a["profit"] = round(a["sale"] - a["cost"], 2) if a["matched"] else None
        a["margin_pct"] = round((a["sale"] - a["cost"]) / a["sale"] * 100, 2) if (a["matched"] and a["sale"]) else None
        rows.append(a)
    rows.sort(key=lambda x: -x["cost"])
    tu = sum(x["units"] for x in rows)
    tc = round(sum(x["cost"] for x in rows), 2)
    ts = round(sum(x["sale"] for x in rows if x["matched"]), 2)
    tcm = round(sum(x["cost"] for x in rows if x["matched"]), 2)
    return {
        "range": {"start": sd, "end": ed},
        "totals": {"units": tu, "cost": tc, "sale": ts,
                   "profit": round(ts - tcm, 2), "margin_pct": round((ts - tcm) / ts * 100, 2) if ts else None,
                   "unmatched": sum(1 for x in rows if not x["matched"]), "rows": len(rows)},
        "rows": rows,
    }


# =============================================================================
# EKRANDAKİ TABLOYU EXCEL'E ÇEVİR (kullanıcı: "her bölümü ayrı ayrı excele alabilelim").
# Panel ekranda gördüğü tabloyu (sütun + sıralı satırlar + toplam) gönderir; dosya ekranla
# BİREBİR aynı olur. Yalnız admin; veri tabanına dokunmaz.
# =============================================================================
class _XCol(BaseModel):
    k: str
    l: str
    type: str = "text"   # text | num | money | pct


class _XReq(BaseModel):
    title: str = Field("Rapor", max_length=80)
    columns: List[_XCol] = Field(..., max_length=60)
    rows: List[dict] = Field(default_factory=list, max_length=50000)
    footer: Optional[dict] = None


@router.post("/export-xlsx")
async def export_table_xlsx(req: _XReq, _=Depends(require_permission("reports.sales"))):
    # ACİL ANAHTAR: freeze_exports açıkken toplu veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off as _sw_off, emit_security_event as _sec_emit
    await _sw_off("freeze_exports")
    import io
    import asyncio
    from fastapi.responses import Response
    from urllib.parse import quote

    def _build() -> bytes:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = re.sub(r"[\[\]\:\*\?\/\\]", " ", req.title)[:31] or "Rapor"
        cols = req.columns
        ws.append([c.l for c in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="111827")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        def _val(c, v):
            if v is None or v == "":
                return None
            if c.type in ("num", "money", "pct"):
                try:
                    f = float(v)
                except Exception:
                    return str(v)[:32000]
                return f / 100.0 if c.type == "pct" else f
            return str(v)[:32000]

        data = list(req.rows) + ([req.footer] if req.footer else [])
        for r in data:
            ws.append([_val(c, (r or {}).get(c.k)) for c in cols])
        fmt = {"num": "#,##0", "money": '#,##0.00 "₺"', "pct": "0.0%"}
        for j, c in enumerate(cols, start=1):
            letter = get_column_letter(j)
            width = max([len(str(c.l))] + [len(str((r or {}).get(c.k) or "")) for r in data[:500]]) + 2
            ws.column_dimensions[letter].width = min(max(width, 10), 60)
            if c.type in fmt:
                for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                    row[0].number_format = fmt[c.type]
                    row[0].alignment = Alignment(horizontal="right")
        if req.footer:
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="F3F4F6")
        ws.freeze_panes = "A2"
        # GÜVENLİK (INJ-01): satırlar istemciden gelir ve müşteri kaynaklı metin (ilçe/ad/not…)
        # taşır → "=HYPERLINK(...)" FORMÜL hücresi olmasın (Excel formül enjeksiyonu).
        from utils.spreadsheet_safe import neutralize_workbook
        neutralize_workbook(wb)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    content = await asyncio.to_thread(_build)   # openpyxl senkron → olay döngüsünü kilitlemesin
    fname = f"{req.title}_{_now().strftime('%Y-%m-%d')}.xlsx"
    # Güvenlik olayı: toplu dışa aktarım (denetim izi + süper admin uyarısı; asla istisna fırlatmaz).
    await _sec_emit("report_table_export", _, {"export": "rapor.xlsx", "rows": len(req.rows)})
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=\"rapor.xlsx\"; filename*=UTF-8''{quote(fname)}"})
