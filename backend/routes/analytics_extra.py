"""
=============================================================================
analytics_extra.py — İleri Seviye Analitik + XML/JSON Feed Çıktıları
=============================================================================

AMAÇ:
  1) **RFM Müşteri Segmentasyonu** — Recency (son alışveriş), Frequency
     (sipariş sayısı), Monetary (toplam ciro) bazlı 1-5 puan. Klasik
     pazarlama modeli; VIP / Riskli / Kaybedilen / Yeni gibi gruplar için
     hedefli kampanya yapmayı sağlar.

  2) **Marketplace Karlılık Raporu** — Her pazaryeri için net kâr =
     brüt - COGS (ürün maliyeti) - komisyon - kargo - iade. Komisyon yüzdesi
     `marketplace_accounts.{key}.transfer_rules.commission_value`'dan okunur.
     COGS, profitability raporuyla AYNI zincirden gelir (purchase_price>0 else
     cost_price; eksikse cog_fallback_ratio ile oranla tahmin).

  3) **Google Merchant Feed** — Google Shopping için XML feed. Ücretsiz
     listeleme ve Google Ads Shopping kampanyaları için zorunludur.

ENDPOINT'LER:
  GET /api/analytics-extra/rfm
  GET /api/analytics-extra/marketplace-profit
  GET /api/feeds/google-merchant.xml   (public — token gerektirmez)
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.sax.saxutils import escape

from .deps import db, require_admin, tr_range_to_utc, require_permission
from .report_dedup import effective_order_date_match, merge_match, load_dup_dep

# IAM-01: load_dup_dep (DB taraması/önbellek ısıtma) router düzeyinde yetkiden ÖNCE çalışıyordu ve herkese
# açık besleme ucunu da kapsıyordu. Artık yalnız personel uçlarında, require_admin'den SONRA çalışır.
router = APIRouter(tags=["Analytics Extra"])
_ADMIN_DEPS = [Depends(require_admin), Depends(load_dup_dep)]


# ---------------------------------------------------------------------------
# RFM SEGMENTATION
# ---------------------------------------------------------------------------
def _rfm_segment(r: int, f: int, m: int) -> str:
    """
    Klasik RFM segment etiketleme. 555 en iyisi, 111 en kötüsü.
    Kaba kurallar; pazarlama içerik ekibinin ihtiyaçlarına göre
    kolayca genişletilebilir.
    """
    if r >= 4 and f >= 4 and m >= 4: return "VIP / Şampiyon"
    if r >= 4 and f >= 3: return "Sadık Müşteri"
    if r >= 4 and f <= 2: return "Yeni Müşteri"
    if r == 3 and f >= 3: return "Potansiyel Sadık"
    if r <= 2 and f >= 4 and m >= 4: return "Risk Altında (Kayıp Uyarısı)"
    if r <= 2 and f >= 3: return "Dikkat Edilmeli"
    if r <= 2 and f <= 2 and m <= 2: return "Kaybedilen"
    if r == 1: return "Hibernasyon"
    return "Standart"


from security.contact_pii import mask_phone as _mask_phone  # noqa: E402


@router.get("/analytics-extra/rfm", dependencies=_ADMIN_DEPS)
async def rfm_analysis(
    lookback_days: int = Query(365, ge=30, le=1460),
    current_user: dict = Depends(require_permission("customers.view", any_in_area=True)),
):
    """
    Son lookback_days içindeki siparişleri kullanıcı e-postasına göre
    grupla, her kullanıcıya Recency/Frequency/Monetary quintile puanları
    (1-5) hesapla ve segment etiketi ata.

    Kullanım: Pazarlama ekibi "Risk Altında" segmentine özel kupon,
              "VIP" segmentine öncelikli teslimat teklifleri üretir.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    # NET CİRO (kullanıcı isteği): iptal/tam iade/ödenmemiş siparişler sayılmaz; KISMİ iadeli
    # sipariş (partial_refunded vb.) sayılır ama onaylanan iade tutarı (customer_returns.refund_amount)
    # harcamadan düşülür → segment (VIP vb.) iptal-iade düştükten sonraki tutarla belirlenir.
    _RET_OK = ["approved", "return_approved", "refunded", "partial_refunded", "completed"]
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff},
                    "status": {"$nin": [
                        "cancelled", "cancel_refunded",
                        "awaiting_payment", "payment_failed", "failed", "pending", "payment_notified",
                        "return_requested", "return_approved", "return_in_transit",
                        "returned", "refunded"]},
                    "payment_status": {"$nin": ["expired", "failed", "refunded"]}}},
        {"$match": merge_match({})},  # ticimax_history ÇİFT kayıtları hariç
        {"$lookup": {"from": "customer_returns", "localField": "id", "foreignField": "order_id", "as": "_rets"}},
        {"$addFields": {"_net": {"$max": [0, {"$subtract": [
            {"$ifNull": ["$total", {"$ifNull": ["$total_amount", 0]}]},
            {"$sum": {"$map": {
                "input": {"$filter": {"input": "$_rets", "as": "r",
                                      "cond": {"$in": [{"$ifNull": ["$$r.status", ""]}, _RET_OK]}}},
                "as": "r", "in": {"$ifNull": ["$$r.refund_amount", 0]}}}}]}]}}},
        {"$group": {
            "_id": {"$ifNull": ["$customer_email",
                                {"$ifNull": ["$user_email",
                                             {"$ifNull": ["$email",
                                                          "$shipping_address.email"]}]}]},
            "last_order": {"$max": "$created_at"},
            "order_count": {"$sum": 1},
            "total_spent": {"$sum": "$_net"},
            "gross_spent": {"$sum": {"$ifNull": ["$total", {"$ifNull": ["$total_amount", 0]}]}},
            "name": {"$last": {"$ifNull": ["$customer_name",
                                           {"$ifNull": ["$shipping_address.full_name",
                                                        "$shipping_address.name"]}]}},
            "phone": {"$last": {"$ifNull": ["$customer_phone", "$shipping_address.phone"]}},
        }},
        {"$match": {"_id": {"$ne": None}}},
        {"$sort": {"total_spent": -1}},
        {"$limit": 5000},
    ]
    rows = await db.orders.aggregate(pipeline).to_list(length=5000)
    if not rows:
        return {"lookback_days": lookback_days, "total": 0, "segments": {}, "items": []}

    # Quintile hesapla (1-5)
    def _quintile(values, asc=True):
        """Değerlere 1..5 arası skor döner; asc=True ise düşük değer=1."""
        sorted_vals = sorted(values, reverse=not asc)
        n = len(sorted_vals)
        thresholds = [sorted_vals[int(n * q / 5)] for q in range(1, 5)] if n else []
        def score(v):
            s = 1
            for t in thresholds:
                if (v > t if asc else v < t): s += 1
            return min(5, s)
        return score

    now = datetime.now(timezone.utc)
    recency_days = []
    for r in rows:
        try:
            lo = datetime.fromisoformat(str(r["last_order"]).replace("Z", "+00:00"))
            if lo.tzinfo is None: lo = lo.replace(tzinfo=timezone.utc)
            r["recency_days"] = (now - lo).days
        except Exception:
            r["recency_days"] = lookback_days
        recency_days.append(r["recency_days"])

    freq_score_fn = _quintile([r["order_count"] for r in rows], asc=True)
    mon_score_fn = _quintile([r["total_spent"] or 0 for r in rows], asc=True)
    # Recency: daha az gün = daha iyi → asc=False
    rec_score_fn = _quintile(recency_days, asc=False)

    items = []
    seg_counts = {}
    for r in rows:
        R = rec_score_fn(r["recency_days"])
        F = freq_score_fn(r["order_count"])
        M = mon_score_fn(r["total_spent"] or 0)
        seg = _rfm_segment(R, F, M)
        seg_counts[seg] = seg_counts.get(seg, 0) + 1
        items.append({
            "email": r["_id"],
            "name": r.get("name"),
            # KVKK: segment listesinde (ve ondan üretilen CSV'de) telefon yalnız son 2 hane.
            "phone": _mask_phone(r.get("phone")),
            "last_order": r["last_order"],
            "recency_days": r["recency_days"],
            "order_count": r["order_count"],
            "total_spent": round(r["total_spent"] or 0, 2),
            "gross_spent": round(r.get("gross_spent") or 0, 2),
            "returns_deducted": round((r.get("gross_spent") or 0) - (r["total_spent"] or 0), 2),
            "r": R, "f": F, "m": M,
            "rfm": f"{R}{F}{M}",
            "segment": seg,
        })
    items.sort(key=lambda x: (-x["m"], -x["f"], x["recency_days"]))
    return {
        "lookback_days": lookback_days,
        "total": len(items),
        "segments": seg_counts,
        "items": items,
        "net_basis": True,
        "note": "Tutarlar NET: iptal/tam iade/ödenmemiş siparişler sayılmaz, kısmi iade tutarı düşülür.",
    }


# ---------------------------------------------------------------------------
# MARKETPLACE PROFIT REPORT
# ---------------------------------------------------------------------------
@router.get("/analytics-extra/marketplace-profit", dependencies=_ADMIN_DEPS)
async def marketplace_profit(
    days: int = Query(30, ge=1, le=3650),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.financial")),
):
    """
    Her kanal/pazaryeri için brüt ciro, COGS (ürün maliyeti), komisyon
    (transfer_rules'tan), kargo maliyeti, iade tutarı ve net kâr hesaplar.

    DENETİM DÜZELTMESİ (K3): Eskiden net kâr = brüt − komisyon − kargo − iade
    idi; ürün maliyeti (COGS) HİÇ düşülmüyordu → 80₺'ye alıp 100₺'ye satılan
    (%18 komisyon) üründe gerçek kâr 2₺ iken rapor ~82₺ (~40x şişik) gösteriyordu.
    Artık net = brüt − COGS − komisyon − kargo − iade. COGS, profitability
    raporuyla AYNI zincir/eşleşmeyle çekilir (aşağıdaki _cogs_pipeline).
    """
    if bool(start_date) != bool(end_date):
        raise HTTPException(status_code=400, detail="start_date ve end_date birlikte verilmelidir")
    if start_date and end_date:
        try:
            range_start, range_end = tr_range_to_utc(start_date, end_date)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Geçersiz tarih aralığı")
    else:
        # `days` geriye uyumlu kalır; sınırlar Türkiye yerel günüdür.
        today_tr = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        first_tr = today_tr - timedelta(days=days - 1)
        range_start, range_end = tr_range_to_utc(first_tr.isoformat(), today_tr.isoformat())
    date_match = effective_order_date_match(range_start, range_end)

    # Maliyet-oranı yapılandırması (profitability ile ortak): maliyeti bilinmeyen
    # kalemlerde satış fiyatının bu oranı COGS sayılır → eksik maliyet SAHTE %100
    # marj üretmesin (denetim bulgusu D4). Yerelde import: modül-yükleme sırasında
    # döngüsel bağımlılık riskini önlemek için fonksiyon içinde (feed'deki `import os` stili).
    try:
        from .reports import _profitability_config
        _cfg = await _profitability_config()
        cog_fallback_ratio = float(_cfg.get("cog_fallback_ratio") or 0.5)
    except Exception:
        cog_fallback_ratio = 0.5

    # Komisyon ayarlarını çek
    accounts_cursor = db.marketplace_accounts.find({}, {"_id": 0, "key": 1, "transfer_rules": 1})
    accounts = await accounts_cursor.to_list(length=100)
    # Anahtarı KÜÇÜK HARF sakla: gruplama _id = $toLower(platform) → aksi halde
    # "Trendyol" anahtarı "trendyol" ile eşleşmez, komisyon sessizce 0 kalırdı.
    comm_map = {
        str(a.get("key") or "").strip().lower(): {
            "type": (a.get("transfer_rules") or {}).get("commission_type", "percent"),
            "value": float((a.get("transfer_rules") or {}).get("commission_value") or 0),
        } for a in accounts
    }

    pipeline = [
        # Denetim: iptal/ödenmemiş siparişler brüt/net kâra girmesin (yalnız gerçek satışlar).
        # İade grubu brütte KALIR ve aşağıda 'refunded' olarak düşülür.
        # DENETİM (finansal F1): TAM iade (returned/refunded) siparişler hariç — eskiden gross'a
        # dahil edilip sonra tam total'i "refunded" düşülüyordu ama COGS sayılmaya devam ettiği
        # için her iade siparişi kârı kendi COGS'u kadar HAYALET zarara çekiyordu. Artık tam
        # iadeler hem gross hem cogs'tan çıkarılır (net katkı 0). KISMİ iade (partial_refunded)
        # gross'ta kalır; yalnız GERÇEK iade tutarı (refund_amount) net'ten düşülür (eskiden tüm
        # sipariş total'i düşülüp koca siparişi zarara çekiyordu).
        {"$match": merge_match({"$and": [date_match, {
                    "status": {"$nin": ["cancelled", "cancel_refunded",
                                        "awaiting_payment", "payment_failed",
                                        "pending", "payment_notified",
                                        "returned", "refunded"]}}]})},
        {"$group": {
            # Denetim: var olmayan 'channel' alanı yüzünden HER sipariş 'web'e düşüyordu.
            # Gerçek kaynak platform/marketplace'ten: trendyol/hepsiburada/temu/site.
            "_id": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}},
            "orders": {"$sum": 1},
            "gross": {"$sum": {"$ifNull": ["$total", "$total_amount"]}},
            "shipping_cost": {"$sum": {"$ifNull": ["$shipping_cost", 0]}},
            "refunded": {"$sum": {"$cond": [
                {"$eq": ["$status", "partial_refunded"]},
                {"$ifNull": ["$refund_amount", 0]},
                0
            ]}},
        }},
        {"$sort": {"gross": -1}},
    ]
    agg = await db.orders.aggregate(pipeline).to_list(length=50)

    # COGS (ürün maliyeti) — kanal bazında. Kalemleri aç, ürünü profitability'nin
    # $lookup'ı ile TIPATIP aynı şekilde eşleştir: product_id VEYA barcode (üst-seviye
    # ya da varyant). Pazaryeri kalemleri Meluni id'si değil productCode/barcode taşır.
    # Birim maliyet zinciri: purchase_price(>0) → cost_price. Maliyeti bilinmeyen
    # kalemin cirosu revenue_nocost'ta toplanır ve aşağıda cog_fallback_ratio ile tahmin edilir.
    cogs_pipeline = [
        # F1: tam iadeler COGS'tan da çıkar (mal stoğa döndü → COGS geri çevrilmeli).
        {"$match": merge_match({"$and": [date_match, {
                    "status": {"$nin": ["cancelled", "cancel_refunded",
                                        "awaiting_payment", "payment_failed",
                                        "pending", "payment_notified",
                                        "returned", "refunded"]}}]})},
        {"$addFields": {"_ch": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}}}},
        {"$unwind": "$items"},
        {"$addFields": {"_bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}}}},
        {"$lookup": {
            "from": "products",
            "let": {"pid": "$items.product_id", "bc": "$_bc"},
            "pipeline": [
                {"$match": {"$expr": {"$or": [
                    {"$eq": ["$id", "$$pid"]},
                    {"$and": [{"$ne": ["$$bc", ""]}, {"$eq": [{"$toString": "$barcode"}, "$$bc"]}]},
                    {"$and": [{"$ne": ["$$bc", ""]}, {"$in": ["$$bc", {"$map": {"input": {"$ifNull": ["$variants", []]}, "as": "v", "in": {"$toString": "$$v.barcode"}}}]}]},
                ]}}},
                {"$limit": 1},
                {"$project": {"_id": 0, "purchase_price": 1, "cost_price": 1}},
            ],
            "as": "p",
        }},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {"_unit_cost": {"$let": {
            "vars": {"pp": {"$ifNull": ["$p.purchase_price", 0]}, "cp": {"$ifNull": ["$p.cost_price", 0]}},
            "in": {"$cond": [{"$gt": ["$$pp", 0]}, "$$pp", "$$cp"]}}}}},
        {"$group": {
            "_id": "$_ch",
            "cogs": {"$sum": {"$multiply": [{"$ifNull": ["$_unit_cost", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            "revenue_nocost": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$_unit_cost", 0]}, 0]}, 0,
                                {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}]}},
        }},
    ]
    cogs_map = {}
    async for r in db.orders.aggregate(cogs_pipeline):
        ch = (r["_id"] or "site")
        # Maliyeti bilinmeyen kalemler için oranla tahmin ekle (sahte %100 marj önlenir).
        est = float(r.get("cogs") or 0) + float(r.get("revenue_nocost") or 0) * cog_fallback_ratio
        cogs_map[ch] = est

    result = []
    totals = {"orders": 0, "gross": 0, "cogs": 0, "commission": 0, "shipping_cost": 0, "refunded": 0, "net": 0}
    for r in agg:
        ch = r["_id"] or "site"
        cfg = comm_map.get(ch, {"type": "none", "value": 0})
        commission = 0
        if cfg["type"] == "percent":
            commission = (r["gross"] or 0) * cfg["value"] / 100
        elif cfg["type"] == "amount":
            commission = (r["orders"] or 0) * cfg["value"]
        cogs = float(cogs_map.get(ch, 0) or 0)
        net = (r["gross"] or 0) - cogs - commission - (r["shipping_cost"] or 0) - (r["refunded"] or 0)
        result.append({
            "channel": ch,
            "orders": r["orders"],
            "gross": round(r["gross"] or 0, 2),
            "cogs": round(cogs, 2),
            "commission": round(commission, 2),
            "commission_rate": cfg["value"],
            "commission_type": cfg["type"],
            "shipping_cost": round(r["shipping_cost"] or 0, 2),
            "refunded": round(r["refunded"] or 0, 2),
            "net": round(net, 2),
            "net_margin_pct": round((net / r["gross"] * 100) if r["gross"] else 0, 2),
        })
        for k in ["orders", "gross", "cogs", "commission", "shipping_cost", "refunded", "net"]:
            totals[k] += result[-1][k] if k != "orders" else r["orders"]
    return {"days": days, "start_date": start_date, "end_date": end_date,
            "effective_start": range_start, "effective_end": range_end,
            "items": result, "totals": {
        **{k: round(v, 2) for k, v in totals.items() if k != "orders"},
        "orders": totals["orders"],
    }, "warning": {
        "code": "legacy_marketplace_profit_scope",
        "message": "Bu sonuç legacy/tahmini kapsamdadır; finansal karar için kanonik Kârlılık Analizi kullanılmalıdır.",
        "authoritative_endpoint": "/api/admin/reports/profitability",
    }}


# ---------------------------------------------------------------------------
# GOOGLE MERCHANT XML FEED
# ---------------------------------------------------------------------------
@router.get("/feeds/google-merchant.xml")
async def google_merchant_feed():
    """
    Google Shopping için XML feed. Google Merchant Center tarafından
    otomatik olarak çekilmek üzere PUBLIC (token'sız). Gizli ürün
    olmasın diye status='active' filtresiyle yalnızca yayındakiler dahil.
    """
    import os
    import company as _company
    # Feed basligi ve varsayilan marka adi company.py'den (env/panel) gelir.
    _c = await _company.get_company(db)
    shop_name = _c.get("store_name") or ""
    site_url = (_c.get("site_url") or os.environ.get("SITE_URL") or "").rstrip("/")
    items_xml = []
    # Yayında olan ürünler: `is_active=True` veya `status=active` veya filtre yok.
    q = {"$or": [{"is_active": True}, {"status": "active"}, {"is_active": {"$exists": False}, "status": {"$exists": False}}]}
    async for p in db.products.find(q, {"_id": 0}).limit(5000):
        pid = p.get("id", "")
        name = p.get("name", "")
        desc = p.get("description") or name
        img = (p.get("images") or [None])[0] or ""
        brand = p.get("brand") or shop_name
        cat = p.get("category_name") or "Giyim"
        price = p.get("sale_price") or p.get("price") or 0
        variants = p.get("variants") or []
        total_stock = sum((v.get("stock") or 0) for v in variants) if variants else (p.get("stock") or 0)
        availability = "in_stock" if total_stock > 0 else "out_of_stock"
        gtin = p.get("barcode") or ""
        mpn = p.get("stock_code") or pid
        link = f"{site_url}/urun/{pid}"
        items_xml.append(f"""
  <item>
    <g:id>{escape(str(pid))}</g:id>
    <g:title>{escape(name)}</g:title>
    <g:description>{escape(str(desc)[:5000])}</g:description>
    <g:link>{escape(link)}</g:link>
    <g:image_link>{escape(img)}</g:image_link>
    <g:availability>{availability}</g:availability>
    <g:price>{float(price):.2f} TRY</g:price>
    <g:brand>{escape(brand)}</g:brand>
    <g:condition>new</g:condition>
    <g:product_type>{escape(cat)}</g:product_type>
    {f"<g:gtin>{escape(str(gtin))}</g:gtin>" if gtin else ""}
    <g:mpn>{escape(str(mpn))}</g:mpn>
    <g:identifier_exists>{'yes' if gtin else 'no'}</g:identifier_exists>
  </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">
<channel>
  <title>{escape(shop_name)}</title>
  <link>{site_url}</link>
  <description>{escape(shop_name)} Google Merchant Product Feed</description>
  {''.join(items_xml)}
</channel>
</rss>"""
    return Response(content=xml, media_type="application/xml; charset=utf-8")
