"""
=============================================================================
analytics_extra.py — İleri Seviye Analitik + XML/JSON Feed Çıktıları
=============================================================================

AMAÇ:
  1) **RFM Müşteri Segmentasyonu** — Recency (son alışveriş), Frequency
     (sipariş sayısı), Monetary (toplam ciro) bazlı 1-5 puan. Klasik
     pazarlama modeli; VIP / Riskli / Kaybedilen / Yeni gibi gruplar için
     hedefli kampanya yapmayı sağlar.

  2) **Marketplace Karlılık Raporu** — Her pazaryeri için net ciro =
     brüt - komisyon - kargo - iade. Komisyon yüzdesi
     `marketplace_accounts.{key}.transfer_rules.commission_value`'dan okunur.

  3) **Google Merchant Feed** — Google Shopping için XML feed. Ücretsiz
     listeleme ve Google Ads Shopping kampanyaları için zorunludur.

ENDPOINT'LER:
  GET /api/analytics-extra/rfm
  GET /api/analytics-extra/marketplace-profit
  GET /api/feeds/google-merchant.xml   (public — token gerektirmez)
=============================================================================
"""
from fastapi import APIRouter, Depends, Query, Response
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.sax.saxutils import escape

from .deps import db, require_admin

router = APIRouter(tags=["Analytics Extra"])


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


@router.get("/analytics-extra/rfm")
async def rfm_analysis(
    lookback_days: int = Query(365, ge=30, le=1460),
    current_user: dict = Depends(require_admin),
):
    """
    Son lookback_days içindeki siparişleri kullanıcı e-postasına göre
    grupla, her kullanıcıya Recency/Frequency/Monetary quintile puanları
    (1-5) hesapla ve segment etiketi ata.

    Kullanım: Pazarlama ekibi "Risk Altında" segmentine özel kupon,
              "VIP" segmentine öncelikli teslimat teklifleri üretir.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff},
                    "status": {"$nin": [
                        "cancelled", "cancel_refunded",
                        "awaiting_payment", "payment_failed", "pending", "payment_notified",
                        "return_requested", "return_approved", "return_in_transit",
                        "returned", "refunded", "partial_refunded"]}}},
        {"$group": {
            "_id": {"$ifNull": ["$customer_email",
                                {"$ifNull": ["$user_email",
                                             {"$ifNull": ["$email",
                                                          "$shipping_address.email"]}]}]},
            "last_order": {"$max": "$created_at"},
            "order_count": {"$sum": 1},
            "total_spent": {"$sum": {"$ifNull": ["$total", "$total_amount"]}},
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
            "phone": r.get("phone"),
            "last_order": r["last_order"],
            "recency_days": r["recency_days"],
            "order_count": r["order_count"],
            "total_spent": round(r["total_spent"] or 0, 2),
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
    }


# ---------------------------------------------------------------------------
# MARKETPLACE PROFIT REPORT
# ---------------------------------------------------------------------------
@router.get("/analytics-extra/marketplace-profit")
async def marketplace_profit(
    days: int = Query(30, ge=1, le=3650),
    current_user: dict = Depends(require_admin),
):
    """
    Her kanal/pazaryeri için brüt ciro, komisyon (transfer_rules'tan),
    kargo maliyeti, iade tutarı ve net kâr hesaplar.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

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
        {"$match": {"created_at": {"$gte": cutoff},
                    "status": {"$nin": ["cancelled", "cancel_refunded",
                                        "awaiting_payment", "payment_failed",
                                        "pending", "payment_notified"]}}},
        {"$group": {
            # Denetim: var olmayan 'channel' alanı yüzünden HER sipariş 'web'e düşüyordu.
            # Gerçek kaynak platform/marketplace'ten: trendyol/hepsiburada/temu/site.
            "_id": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}},
            "orders": {"$sum": 1},
            "gross": {"$sum": {"$ifNull": ["$total", "$total_amount"]}},
            "shipping_cost": {"$sum": {"$ifNull": ["$shipping_cost", 0]}},
            "refunded": {"$sum": {"$cond": [
                {"$in": ["$status", ["returned", "refunded", "partial_refunded"]]},
                {"$ifNull": ["$total", "$total_amount"]},
                0
            ]}},
        }},
        {"$sort": {"gross": -1}},
    ]
    agg = await db.orders.aggregate(pipeline).to_list(length=50)

    result = []
    totals = {"orders": 0, "gross": 0, "commission": 0, "shipping_cost": 0, "refunded": 0, "net": 0}
    for r in agg:
        ch = r["_id"] or "site"
        cfg = comm_map.get(ch, {"type": "none", "value": 0})
        commission = 0
        if cfg["type"] == "percent":
            commission = (r["gross"] or 0) * cfg["value"] / 100
        elif cfg["type"] == "amount":
            commission = (r["orders"] or 0) * cfg["value"]
        net = (r["gross"] or 0) - commission - (r["shipping_cost"] or 0) - (r["refunded"] or 0)
        result.append({
            "channel": ch,
            "orders": r["orders"],
            "gross": round(r["gross"] or 0, 2),
            "commission": round(commission, 2),
            "commission_rate": cfg["value"],
            "commission_type": cfg["type"],
            "shipping_cost": round(r["shipping_cost"] or 0, 2),
            "refunded": round(r["refunded"] or 0, 2),
            "net": round(net, 2),
            "net_margin_pct": round((net / r["gross"] * 100) if r["gross"] else 0, 2),
        })
        for k in ["orders", "gross", "commission", "shipping_cost", "refunded", "net"]:
            totals[k] += result[-1][k] if k != "orders" else r["orders"]
    return {"days": days, "items": result, "totals": {
        **{k: round(v, 2) for k, v in totals.items() if k != "orders"},
        "orders": totals["orders"],
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
    site_url = os.environ.get("SITE_URL", "https://facette.com.tr")
    items_xml = []
    # Yayında olan ürünler: `is_active=True` veya `status=active` veya filtre yok.
    q = {"$or": [{"is_active": True}, {"status": "active"}, {"is_active": {"$exists": False}, "status": {"$exists": False}}]}
    async for p in db.products.find(q, {"_id": 0}).limit(5000):
        pid = p.get("id", "")
        name = p.get("name", "")
        desc = p.get("description") or name
        img = (p.get("images") or [None])[0] or ""
        brand = p.get("brand") or "FACETTE"
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
  <title>FACETTE</title>
  <link>{site_url}</link>
  <description>FACETTE Google Merchant Product Feed</description>
  {''.join(items_xml)}
</channel>
</rss>"""
    return Response(content=xml, media_type="application/xml; charset=utf-8")
