"""
Reports module — aggregated analytics for admin dashboard.

Endpoints (all admin-protected):
  GET /api/admin/reports/sales?start_date=&end_date=&group_by=day|week|month
  GET /api/admin/reports/products/top?limit=20
  GET /api/admin/reports/categories
  GET /api/admin/reports/members
  GET /api/admin/reports/stock
  GET /api/admin/reports/cargo
  GET /api/admin/reports/payments
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from datetime import datetime, timezone, timedelta
from typing import Optional

from .deps import db, require_admin, tr_range_to_utc


router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])


def _iso_range(start: Optional[str], end: Optional[str], days_default: int = 30):
    # Seçilen tarihler TÜRKİYE yerel günü kabul edilir (00:00–23:59:59.999 +03:00) ve UTC ISO
    # sınırına çevrilir → bitiş günü tam dahil olur, saat-dilimi kayması OLMAZ. (deps.tr_range_to_utc)
    if start and end:
        return tr_range_to_utc(start, end)
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=days_default)).isoformat(), now.isoformat()


# Pazaryeri kaynakları — Site dışı her şey. (platform VEYA marketplace alanında durabilir;
# örn. Temu siparişleri yalnız marketplace="temu" taşır, platform boş olabilir.)
_MARKETPLACES = ["trendyol", "hepsiburada", "temu"]

# Ciro/sipariş tutarlarına DAHİL EDİLMEYECEK durumlar: iptal + iade + ÖDENMEMİŞ grubu.
# return_rejected (iade reddedildi) HARİÇ — satış geçerli sayıldığı için ciroda kalır.
# ÖDENMEMİŞ (kritik-para): awaiting_payment (3DS/havale tamamlanmamış), payment_failed
# (başarısız kart — kalıcı birikir), pending, payment_notified (havale bildirimi onaysız).
# Pazaryeri siparişleri DAİMA confirmed+ geldiği için (integrations_trendyol.py:2875) bu
# ekleme pazaryeri cirosunu düşürmez; yalnız gerçekten ödenmemiş site siparişlerini eler.
_EXCLUDED_STATUSES = [
    "cancelled", "cancel_refunded",
    "awaiting_payment", "payment_failed", "pending", "payment_notified",
    "return_requested", "return_approved", "return_in_transit",
    "returned", "refunded", "partial_refunded",
]


def _source_cond(source: Optional[str]) -> dict:
    """Rapor kaynak filtresi → Mongo koşulu.
    'all'/boş = toplu (filtre yok). 'site' = pazaryeri olmayan tüm siparişler.
    'trendyol'/'hepsiburada'/'temu' = ilgili pazaryeri (platform VEYA marketplace).
    """
    s = (source or "all").strip().lower()
    if s in ("", "all", "toplu", "hepsi", "tum", "tümü"):
        return {}
    if s in ("site", "facette", "web", "kendi"):
        # Site = pazaryeri olmayan: platform da marketplace da pazaryeri listesinde DEĞİL
        # (alan hiç yoksa $nin yine eşleşir → boş platform site sayılır).
        return {"platform": {"$nin": _MARKETPLACES}, "marketplace": {"$nin": _MARKETPLACES}}
    if s in ("trendyol", "ty"):
        return {"$or": [{"platform": "trendyol"}, {"marketplace": "trendyol"}]}
    if s in ("hepsiburada", "hb"):
        return {"$or": [{"platform": "hepsiburada"}, {"marketplace": "hepsiburada"}]}
    if s == "temu":
        return {"$or": [{"platform": "temu"}, {"marketplace": "temu"}]}
    return {}  # bilinmeyen kaynak → toplu


def _collection_from_code(*codes) -> str:
    """Stok kodu ön ekinden koleksiyon türetir: 'fcfw…' → FcFw (Sonbahar/Kış),
    'fcss…' → FCss (İlkbahar/Yaz). Büyük/küçük harf duyarsız; ilk eşleşen kod kazanır."""
    for c in codes:
        cc = (str(c) if c is not None else "").strip().lower()
        if cc.startswith("fcfw"):
            return "FcFw"
        if cc.startswith("fcss"):
            return "FCss"
    return ""


def _season_from_attrs(attrs) -> str:
    """Ürünün 'Sezon' özniteliğini 4 kanonik değere normalize eder:
    İlkbahar/Sonbahar · Tüm Sezonlar · Yaz · Kış. Veri yoksa BOŞ döner (varsayım
    yapılmaz). Kombine etiketlerde (SPRING-SUMMER, FALL-WINTER) geç sezon esas alınır;
    ara sezonlar (ilkbahar/sonbahar) tek 'İlkbahar/Sonbahar' grubunda toplanır."""
    val = ""
    for a in (attrs or []):
        if isinstance(a, dict) and str(a.get("name") or a.get("type") or "").strip().lower() in ("sezon", "season"):
            v = str(a.get("value") or "").strip()
            if v:
                val = v
                break
    v = val.lower().replace("i̇", "i")
    if not v:
        return ""
    if "tüm sezon" in v or "tum sezon" in v or "4 mevsim" in v or "all season" in v or "mevsim" in v:
        return "Tüm Sezonlar"
    if "kış" in v or "kis" in v or "winter" in v:
        return "Kış"
    if "yaz" in v or "summer" in v:
        return "Yaz"
    if "sonbahar" in v or "fall" in v or "autumn" in v or "ilkbahar" in v or "spring" in v or "bahar" in v:
        return "İlkbahar/Sonbahar"
    return ""


def _base_match(s: str, e: str, source: Optional[str] = None) -> dict:
    """Tüm satış raporlarının ortak $match'i: tarih aralığı + iptal/iade hariç + kaynak."""
    m = {"created_at": {"$gte": s, "$lte": e}, "status": {"$nin": _EXCLUDED_STATUSES}}
    sc = _source_cond(source)
    if sc:
        m.update(sc)
    return m


@router.get("/sales-summary")
async def sales_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Genel Satış Özeti (anlık dashboard bloğu) — TR yerel güne göre:
    bugünkü ciro (iptal+iade DAHİL brüt), dünle karşılaştırma, hafta/ay/yıl cirosu,
    bugünkü sipariş adedi, satılan ürün adedi (adet toplamı), ortalama sepet,
    sipariş başına ürün adedi, iade tutarı, iptal tutarı, net satış (iptal+iade hariç)."""
    _tr = timedelta(hours=3)
    now_tr = datetime.now(timezone.utc) + _tr
    day0 = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)

    def _u(dt_tr):  # TR yerel → UTC ISO (orders.created_at UTC saklanır)
        return (dt_tr - _tr).isoformat()

    _RETURN_ST = ["return_requested", "return_approved", "return_in_transit",
                  "returned", "refunded", "partial_refunded"]
    _CANCEL_ST = ["cancelled", "cancel_refunded"]

    # ── İADE için ÖN-ÇEKİM (bir kez): iade ONAY tarihi OTANTİK kaynaktan gelmeli.
    #    SİTE iadesinde onay tarihi orders.return_approved_at'te DEĞİL (o sadece iade bedeli
    #    ödenince yazılır) → customer_returns.approval.at otantik kaynaktır (gider pusulası
    #    raporu / iade sayfası da bunu kullanır). Pazaryeri iadesinde orders.return_approved_at.
    _DENY_RET = {"return_requested", "created", "pending", "preparing", "shipped",
                 "return_rejected", "rejected", "cancelled", "canceled", "expired",
                 "error", "exception", "undelivered", "waitinginaction", "inanalysis", "unresolved"}
    _ret_meta = {}   # order_number -> {"total", "site", "oa"(pazaryeri onay tarihi)}
    async for _o in db.orders.find(
            {"status": {"$in": _RETURN_ST}},
            {"_id": 0, "order_number": 1, "total": 1, "platform": 1, "marketplace": 1,
             "return_approved_at": 1, "refund_paid_at": 1, "updated_at": 1}):
        _on = str(_o.get("order_number") or "")
        if not _on:
            continue
        _pf = str(_o.get("platform") or "").lower()
        _mk = str(_o.get("marketplace") or "").lower()
        _ret_meta[_on] = {
            "total": float(_o.get("total") or 0),
            "site": _pf not in _MARKETPLACES and _mk not in _MARKETPLACES,
            "oa": str(_o.get("return_approved_at") or _o.get("refund_paid_at") or _o.get("updated_at") or ""),
        }
    _site_appr = {}  # order_number -> otantik onay tarihi (customer_returns.approval.at)
    async for _cr in db.customer_returns.find(
            {}, {"_id": 0, "order_number": 1, "status": 1, "approval": 1}):
        _on = str(_cr.get("order_number") or "")
        _apd = str((_cr.get("approval") or {}).get("at") or "")
        if not _on or not _apd:
            continue  # onaylanmamış iade → "onaylandı" sayılmaz
        if str(_cr.get("status") or "").lower() in _DENY_RET:
            continue
        if _on not in _site_appr or _apd > _site_appr[_on]:  # aynı siparişte birden çok iade → en yeni onay
            _site_appr[_on] = _apd

    async def _agg(s_iso: str, e_iso: str) -> dict:
        _sc = _source_cond(source)
        # ── 1) CİRO (brüt) + net sipariş/adet: SİPARİŞ tarihine göre (satışın gerçekleştiği ay).
        _m = {"created_at": {"$gte": s_iso, "$lt": e_iso}}
        if _sc:
            _m.update(_sc)
        pipe = [
            {"$match": _m},
            {"$group": {
                "_id": None,
                "revenue_all": {"$sum": {"$ifNull": ["$total", 0]}},
                "net_orders": {"$sum": {"$cond": [{"$in": ["$status", _CANCEL_ST + _RETURN_ST]}, 0, 1]}},
                "items_sold": {"$sum": {"$cond": [
                    {"$in": ["$status", _CANCEL_ST + _RETURN_ST]}, 0,
                    {"$sum": {"$map": {"input": {"$ifNull": ["$items", []]}, "as": "it",
                                       "in": {"$ifNull": ["$$it.quantity", 1]}}}}]}},
            }},
        ]
        r = await db.orders.aggregate(pipe).to_list(1)
        d = r[0] if r else {}
        revenue_all = float(d.get("revenue_all") or 0)

        # ── 2) İPTAL: İPTALİN YAPILDIĞI tarihe göre (cancelled_at; yoksa updated_at) — sipariş
        #    hangi ay verilmiş olursa olsun, iptal HANGİ AY yapıldıysa o aya yansır (Kadir isteği).
        async def _sum_by_action(status_list, date_field, fallback_field):
            _mm = {"status": {"$in": status_list}}
            if _sc:
                _mm.update(_sc)
            _pipe = [
                {"$match": _mm},
                {"$addFields": {"_ad": {"$ifNull": [f"${date_field}", f"${fallback_field}"]}}},
                {"$match": {"_ad": {"$gte": s_iso, "$lt": e_iso}}},
                {"$group": {"_id": None, "t": {"$sum": {"$ifNull": ["$total", 0]}},
                            "n": {"$sum": 1}}},
            ]
            _r = await db.orders.aggregate(_pipe).to_list(1)
            _x = _r[0] if _r else {}
            return float(_x.get("t") or 0), int(_x.get("n") or 0)

        cancel_total, _cn = await _sum_by_action(_CANCEL_ST, "cancelled_at", "updated_at")
        # ── 3) İADE: İADE ONAY tarihine göre. SİTE → customer_returns.approval.at (otantik),
        #    PAZARYERİ → orders.return_approved_at. Sipariş no ile tekilleştirilir; kaynağa göre süzülür.
        _src = (source or "").lower().strip()
        return_total = 0.0
        _ret_cnt = 0
        for _on, _mt in _ret_meta.items():
            if _mt["site"]:
                if _src in ("trendyol", "hepsiburada"):
                    continue  # site istenmedi
                _appr = _site_appr.get(_on)
                if not _appr or not (s_iso <= _appr < e_iso):
                    continue
            else:
                if _src == "site":
                    continue  # pazaryeri istenmedi
                _appr = _mt["oa"]
                if not _appr or not (s_iso <= _appr < e_iso):
                    continue
            return_total += _mt["total"]
            _ret_cnt += 1

        # NET = o ayki ciro − o ay İPTAL edilen − o ay İADE onaylanan (eylem tarihine göre).
        net_revenue = revenue_all - cancel_total - return_total
        return {
            "revenue": round(revenue_all, 2),
            "net": round(net_revenue, 2),
            "cancels": round(cancel_total, 2),
            "returns": round(return_total, 2),
            "cancel_count": int(_cn),      # o ay İPTAL edilen sipariş SAYISI
            "return_count": int(_ret_cnt), # o ay İADE onaylanan sipariş SAYISI
            "orders": int(d.get("net_orders") or 0),
            "items": int(d.get("items_sold") or 0),
        }

    now_iso = _u(now_tr)
    today = await _agg(_u(day0), now_iso)
    yesterday = await _agg(_u(day0 - timedelta(days=1)), _u(day0))
    week = await _agg(_u(day0 - timedelta(days=day0.weekday())), now_iso)
    month = await _agg(_u(day0.replace(day=1)), now_iso)
    year = await _agg(_u(day0.replace(month=1, day=1)), now_iso)

    _cmp = None
    if yesterday["revenue"] > 0:
        _cmp = round((today["revenue"] - yesterday["revenue"]) / yesterday["revenue"] * 100, 1)

    # SEÇİLİ ARALIK özeti (sayfadaki tarih filtresi) + bir önceki eşit uzunluktaki
    # dönemle % karşılaştırma — "günlük seçtiysem günlük, aylık seçtiysem aylık".
    period = period_prev = None
    period_cmp = None
    if start_date and end_date:
        try:
            s2, e2 = _iso_range(start_date, end_date)
            period = await _agg(s2, e2)
            period["aov"] = round(period["net"] / period["orders"], 2) if period["orders"] else 0
            period["items_per_order"] = round(period["items"] / period["orders"], 2) if period["orders"] else 0
            _sd = datetime.fromisoformat(str(s2).replace("Z", "+00:00"))
            _ed = datetime.fromisoformat(str(e2).replace("Z", "+00:00"))
            period_prev = await _agg((_sd - (_ed - _sd)).isoformat(), s2)
            if period_prev["revenue"] > 0:
                period_cmp = round((period["revenue"] - period_prev["revenue"]) / period_prev["revenue"] * 100, 1)
        except Exception:
            period = None
    return {
        "period": period, "period_prev": period_prev, "period_vs_prev_pct": period_cmp,
        "today": {
            **today,
            "aov": round(today["net"] / today["orders"], 2) if today["orders"] else 0,
            "items_per_order": round(today["items"] / today["orders"], 2) if today["orders"] else 0,
        },
        "yesterday": yesterday,
        "vs_yesterday_pct": _cmp,
        "week_revenue": week["revenue"],
        "month_revenue": month["revenue"],
        "year_revenue": year["revenue"],
        "as_of": now_iso,
    }


@router.get("/sales-by-hour")
async def sales_by_hour(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Saat Analizi (00-24, TR saati): hangi saatlerde satış geliyor — sipariş + ciro.
    Reklam planlaması için zirve saat aralığı da döner."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$addFields": {"_d": {"$dateFromString": {"dateString": "$created_at", "onError": None}}}},
        {"$match": {"_d": {"$ne": None}}},
        {"$group": {"_id": {"$hour": {"date": "$_d", "timezone": "+03:00"}},
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"_id": 1}},
    ]
    by = {int(r["_id"]): r async for r in db.orders.aggregate(pipeline)}
    rows = [{"hour": h, "label": f"{h:02d}:00",
             "orders": int(by.get(h, {}).get("orders", 0)),
             "revenue": round(float(by.get(h, {}).get("revenue", 0)), 2)} for h in range(24)]
    peak = max(rows, key=lambda r: r["orders"]) if any(r["orders"] for r in rows) else None
    return {"rows": rows,
            "peak": ({"range": f"{peak['hour']:02d}:00-{(peak['hour'] + 1) % 24:02d}:00",
                      "orders": peak["orders"]} if peak else None)}


@router.get("/sales-by-weekday")
async def sales_by_weekday(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Gün Analizi: haftanın hangi günü daha çok satıyor (TR saati) — sipariş + ciro."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$addFields": {"_d": {"$dateFromString": {"dateString": "$created_at", "onError": None}}}},
        {"$match": {"_d": {"$ne": None}}},
        {"$group": {"_id": {"$isoDayOfWeek": {"date": "$_d", "timezone": "+03:00"}},
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"_id": 1}},
    ]
    _DAYS = {1: "Pazartesi", 2: "Salı", 3: "Çarşamba", 4: "Perşembe",
             5: "Cuma", 6: "Cumartesi", 7: "Pazar"}
    by = {int(r["_id"]): r async for r in db.orders.aggregate(pipeline)}
    rows = [{"day": d, "label": _DAYS[d],
             "orders": int(by.get(d, {}).get("orders", 0)),
             "revenue": round(float(by.get(d, {}).get("revenue", 0)), 2)} for d in range(1, 8)]
    peak = max(rows, key=lambda r: r["orders"]) if any(r["orders"] for r in rows) else None
    return {"rows": rows, "peak": (peak["label"] if peak else None)}


@router.get("/day-orders")
async def day_orders(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (tek TR günü — geriye uyumluluk)"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Sipariş edilen ürünler: SEÇİLİ TARİH ARALIĞINDA (sayfa filtresi) ürün/beden
    bazında adet + ciro. Tek 'date' verilirse o gün (geriye uyumlu)."""
    if start_date and end_date:
        s, e = _iso_range(start_date, end_date)
        date = f"{start_date[:10]} → {end_date[:10]}"
    elif date:
        try:
            d0 = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) - timedelta(hours=3)
        except Exception:
            raise HTTPException(status_code=400, detail="Geçersiz tarih (YYYY-MM-DD)")
        s, e = d0.isoformat(), (d0 + timedelta(days=1)).isoformat()
    else:
        raise HTTPException(status_code=400, detail="date veya start_date+end_date gerekli")
    m = {"created_at": {"$gte": s, "$lt": e}, "status": {"$nin": _EXCLUDED_STATUSES}}
    sc = _source_cond(source)
    if sc:
        m.update(sc)
    pipeline = [
        {"$match": m},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {
            "_id": {"name": {"$ifNull": ["$items.product_name", {"$ifNull": ["$items.name", "Ürün"]}]},
                    "size": {"$ifNull": ["$items.size", ""]}},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "revenue": {"$sum": {"$multiply": [
                {"$ifNull": ["$items.quantity", 1]},
                {"$ifNull": ["$items.price", {"$ifNull": ["$items.unit_price", 0]}]}]}},
        }},
        {"$sort": {"qty": -1}},
        {"$limit": 300},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"name": r["_id"]["name"], "size": r["_id"]["size"],
                     "qty": int(r["qty"]), "revenue": round(float(r["revenue"]), 2)})
    order_count = await db.orders.count_documents(m)
    # DENETİM HATA-2: total_qty 300 satır LİMİTİNDEN SONRA toplanıyordu → uzun listelerde
    # sessizce eksik (30g'de 87 adet kayıp). Toplam artık limitsiz ayrı toplamadan gelir.
    _tq = 0
    async for r in db.orders.aggregate([
            {"$match": m},
            {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
            {"$group": {"_id": None, "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}}}}]):
        _tq = int(r["qty"])
    return {"date": date, "order_count": order_count,
            "total_qty": _tq, "rows": rows}


@router.get("/sales")
async def sales(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = Query("day", regex="^(day|week|month)$"),
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    s, e = _iso_range(start_date, end_date)
    fmt = {"day": "%Y-%m-%d", "week": "%Y-%V", "month": "%Y-%m"}[group_by]
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {
            "$group": {
                # DENETİM HATA-6: timezone verilmeyince günler UTC'ye göre kesiliyordu —
                # TR gününün ilk 3 saati önceki güne yazılıyordu. TR saatiyle grupla.
                "_id": {"$dateToString": {"format": fmt, "timezone": "+03:00",
                                          "date": {"$dateFromString": {"dateString": "$created_at"}}}},
                "orders": {"$sum": 1},
                "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
                # ADET = kalem adetleri toplamı. Eskiden $size (KALEM SAYISI) idi: aynı üründen
                # 3 adet alan sipariş 1 sayılıyordu → pazaryeri raporlarıyla mutabakatta
                # sistematik eksik. Trendyol "Brüt Satış Adedi" ile aynı birim.
                "items": {"$sum": {"$reduce": {
                    "input": {"$ifNull": ["$items", []]}, "initialValue": 0,
                    "in": {"$add": ["$$value", {"$max": [1, {"$ifNull": ["$$this.quantity", 1]}]}]}}}},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"period": r["_id"], "orders": r["orders"], "revenue": round(r["revenue"], 2), "items": r["items"]})

    total_orders = sum(r["orders"] for r in rows)
    total_revenue = round(sum(r["revenue"] for r in rows), 2)
    aov = round(total_revenue / total_orders, 2) if total_orders else 0
    return {"rows": rows, "totals": {"orders": total_orders, "revenue": total_revenue, "aov": aov}}


_CANCEL_STATUSES = ["cancelled", "cancel_refunded"]
# İade grubu (return_rejected HARİÇ — satış geçerli sayılır, ciroda kalır)
_RETURN_STATUSES_BD = ["return_requested", "return_approved", "return_in_transit",
                       "returned", "refunded", "partial_refunded"]
# Pazaryeri iade talebi AÇIK (henüz onaylanmamış) sayılan claim durumları. Trendyol'un
# satış raporu bu adetleri ANINDA "İade"ye yazar; biz yalnız Accepted olanı siparişe
# yansıtıyoruz (integrations_trendyol.py:3951). Aradaki fark bu kovadır.
_OPEN_CLAIM_STATUSES = ["Created", "WaitingInAction", "InAnalysis"]


def _order_units(o: dict) -> int:
    """Siparişteki ÜRÜN ADEDİ (kalem adetleri toplamı). Trendyol'un 'Brüt Satış Adedi'
    ile aynı birim — sipariş sayısı değil. Kalem yoksa 1 kabul edilir."""
    items = o.get("items") or []
    if not items:
        return 1
    n = 0
    for it in items:
        try:
            n += max(1, int(it.get("quantity") or 1))
        except Exception:
            n += 1
    return n or 1


async def _split_maps(order_numbers: list, order_ids: list) -> tuple:
    """Sipariş no listesi için KALEM BAZLI iade tutarlarını toplar.

    Döner: (kapali, acik) — her biri {order_number: {"amount": float, "qty": int}}
      kapali = onaylanmış (Accepted) pazaryeri iadeleri + site'de onaylı iade kalemleri
      acik   = henüz sonuçlanmamış (Created/WaitingInAction/InAnalysis) iade talepleri

    Neden kalem bazlı: 3 ürünlü siparişten 1 ürün iade edildiğinde siparişin TAMAMI
    iadeye yazılıyordu (apply_accepted_claims_to_orders sipariş statüsünü komple
    'returned' yapar). Trendyol adet bazlı saydığı için tutarlar tutmuyordu.
    """
    closed: dict = {}
    open_: dict = {}
    if not order_numbers:
        return closed, open_

    def _acc(bucket: dict, onum: str, amount: float, qty: int):
        d = bucket.setdefault(onum, {"amount": 0.0, "qty": 0})
        d["amount"] += max(0.0, float(amount or 0))
        d["qty"] += max(0, int(qty or 0))

    # ① Pazaryeri (Trendyol/Hepsiburada) iade talepleri — refund_amount kalem NET
    #    fiyatlarının toplamıdır (integrations_trendyol.py:3866), yani tam da iade
    #    edilen ürünlerin parası.
    for i in range(0, len(order_numbers), 5000):
        chunk = order_numbers[i:i + 5000]
        async for c in db.trendyol_claims.find(
                {"order_number": {"$in": chunk}, "claim_type": {"$ne": "CANCEL"}},
                {"_id": 0, "order_number": 1, "claim_status": 1,
                 "refund_amount": 1, "items": 1}):
            st = str(c.get("claim_status") or "")
            onum = str(c.get("order_number") or "")
            if not onum:
                continue
            qty = sum(max(1, int((it or {}).get("quantity") or 1))
                      for it in (c.get("items") or [])) or 1
            amt = c.get("refund_amount")
            try:
                amt = float(amt or 0)
            except Exception:
                amt = 0.0
            if st == "Accepted":
                _acc(closed, onum, amt, qty)
            elif st in _OPEN_CLAIM_STATUSES:
                _acc(open_, onum, amt, qty)
            # Rejected/Cancelled/Unresolved → iade GERÇEKLEŞMEDİ, satış geçerli.

    # ② Site iadeleri — customer_returns kalemleri (onaylı kalemler öncelikli).
    #    Site siparişlerinde statü zaten iade grubuna düşüyor; buradan yalnız KISMİ
    #    iadenin tutarını çıkarıyoruz ki kalan ürünler satışta kalsın.
    if order_ids:
        for i in range(0, len(order_ids), 5000):
            chunk = order_ids[i:i + 5000]
            async for r in db.customer_returns.find(
                    {"order_id": {"$in": chunk}},
                    {"_id": 0, "order_id": 1, "order_number": 1, "status": 1,
                     "approved_items": 1, "items": 1, "refund_amount": 1}):
                st = str(r.get("status") or "").lower()
                if st in ("cancelled", "canceled", "rejected", "return_rejected"):
                    continue
                onum = str(r.get("order_number") or "")
                if not onum:
                    continue
                its = r.get("approved_items") or r.get("items") or []
                qty = sum(max(1, int((it or {}).get("quantity") or 1)) for it in its)
                amt = 0.0
                for it in its:
                    try:
                        amt += float((it or {}).get("price") or (it or {}).get("unit_price") or 0) \
                               * max(1, int((it or {}).get("quantity") or 1))
                    except Exception:
                        pass
                if amt <= 0:
                    try:
                        amt = float(r.get("refund_amount") or 0)
                    except Exception:
                        amt = 0.0
                # Site tarafında "onaylı" = terminal statüler; gerisi açık talep.
                tgt = closed if st in ("returned", "refunded", "partial_refunded",
                                       "return_approved", "approved", "completed") else open_
                _acc(tgt, onum, amt, qty)
    return closed, open_


def _bucket_orders(orders: list, closed: dict, open_: dict) -> dict:
    """İptal/iade kovalarını KALEM BAZINDA hesaplayan TEK kaynak.

    sales_breakdown (ciro kartları) ve cancel_return_by_source (kanal tablosu) aynı
    ekranda göründüğü için ikisi de bu fonksiyonu kullanır — aksi halde aynı sayfada
    iki farklı "İade" rakamı çıkıyordu (biri sipariş-bütünü, biri kalem bazlı).
    """
    def _blank():
        return {"revenue": 0.0, "orders": 0, "units": 0}

    cancels, returns, net, pending = _blank(), _blank(), _blank(), _blank()
    partial_split = 0      # kısmi ayrıştırma uygulanan toplam sipariş (iade + iptal)
    partial_returns = 0    # bunların KISMİ İADE olanı — "465 iade, 43'ü kısmi" notu için
    partial_cancels = 0

    for o in orders:
        st = str(o.get("status") or "")
        if st in _EXCLUDED_STATUSES and st not in _CANCEL_STATUSES and st not in _RETURN_STATUSES_BD:
            continue  # ödenmemiş grubu (awaiting_payment/pending/failed) — ciroya girmez
        try:
            total = float(o.get("total") or 0)
        except Exception:
            total = 0.0
        units = _order_units(o)
        onum = str(o.get("order_number") or "")

        if st in _CANCEL_STATUSES:
            cancels["revenue"] += total
            cancels["orders"] += 1
            cancels["units"] += units
            continue

        # KISMİ İPTAL: sipariş bizde AKTİF kalır (aktif paket var) ama Trendyol iptal
        # edilen kalemi 'İptal' sayar. Mutabakat bu tutarı/adedi iptale yazar.
        # Tutar VE adet siparişten DÜŞÜLÜR — yoksa aynı kalem hem iptalde hem nette
        # sayılıp toplam adedi şişirir.
        try:
            pc = float(o.get("partial_cancel_amount") or 0)
        except Exception:
            pc = 0.0
        pc = min(max(0.0, pc), total)
        if pc > 0:
            try:
                pc_u = max(1, int(o.get("partial_cancel_units") or 1))
            except Exception:
                pc_u = 1
            pc_u = min(pc_u, max(0, units - 1))  # en az 1 adet aktif kalmalı
            partial_cancels += 1
            cancels["revenue"] += pc
            cancels["units"] += pc_u
            total -= pc
            units -= pc_u

        c = closed.get(onum)
        if st in _RETURN_STATUSES_BD:
            # İade edilmiş sipariş — kalem bazlı tutar varsa YALNIZ o kadarı iadeye gider.
            r_amt = min(float(c["amount"]), total) if (c and c["amount"] > 0.005) else total
            r_qty = min(int(c["qty"]), units) if (c and c["qty"] > 0) else units
            returns["revenue"] += r_amt
            returns["orders"] += 1
            returns["units"] += r_qty
            kept = round(total - r_amt, 2)
            kept_u = units - r_qty
            if kept > 0.005 or kept_u > 0:
                partial_split += 1
                partial_returns += 1
                net["revenue"] += max(0.0, kept)
                net["units"] += max(0, kept_u)
                # Sipariş sayısı çift sayılmasın: kısmi iadede sipariş İADE'de sayılır.
            continue

        # ONAYLI İADE var ama sipariş statüsü henüz senkronlanmamış (claims-sync gecikmesi
        # veya kısmi iadede statünün değişmemesi). Rapor kendi kendini onarır: iade edilen
        # kalemin payı İade'ye gider, kalan ürünler satışta kalır.
        if c and (c["amount"] > 0.005 or c["qty"] > 0):
            r_amt = min(float(c["amount"]), total)
            r_qty = min(int(c["qty"]) or 1, units)
            returns["revenue"] += r_amt
            returns["units"] += r_qty
            total = round(total - r_amt, 2)
            units -= r_qty
            if total <= 0.005 and units <= 0:
                returns["orders"] += 1
                continue
            partial_split += 1
            partial_returns += 1

        # Aktif satış
        net["revenue"] += max(0.0, total)
        net["orders"] += 1
        net["units"] += max(0, units)
        op = open_.get(onum)
        if op and (op["amount"] > 0.005 or op["qty"] > 0):
            pending["revenue"] += min(float(op["amount"]), total)
            pending["orders"] += 1
            pending["units"] += min(int(op["qty"]) or 1, units)

    def _fin(d):
        return {"revenue": round(d["revenue"], 2), "orders": d["orders"], "units": d["units"]}


    cancels, returns, net, pending = _fin(cancels), _fin(returns), _fin(net), _fin(pending)
    included = {
        "revenue": round(net["revenue"] + cancels["revenue"] + returns["revenue"], 2),
        "orders": net["orders"] + cancels["orders"] + returns["orders"],
        "units": net["units"] + cancels["units"] + returns["units"],
    }
    projected_net = {
        "revenue": round(net["revenue"] - pending["revenue"], 2),
        "orders": net["orders"] - pending["orders"],
        "units": net["units"] - pending["units"],
    }
    # Kısmi iade sayısı İADE kovasının içine de konur: kanal tablosu ve kart altı
    # notu ("465 iade · 43'ü kısmi") tek yerden beslensin.
    returns["partial_orders"] = partial_returns
    cancels["partial_orders"] = partial_cancels
    return {"included": included, "cancels": cancels, "returns": returns, "net": net,
            "pending_returns": pending, "projected_net": projected_net,
            "partial_split_orders": partial_split,
            "partial_return_orders": partial_returns,
            "partial_cancel_orders": partial_cancels}


async def _returned_barcode_qty(order_numbers: list) -> dict:
    """{order_number: {barcode: iade_adedi}} — hangi KALEMİN kaç adedi iade edildi.

    products/top ve cancel-return-products eskiden iade statüsündeki siparişin TÜM
    kalemlerini iade sayıyordu; 3 ürünlük siparişten 1 ürün iade edilse ürün raporunda
    3 iade + 0 net satış görünüyordu. Bu harita ile yalnız gerçekten iade edilen kalem
    İade'ye yazılır, kalanlar satışa geri döner. Kalem bilgisi YOKSA sipariş için hiç
    kayıt dönmez ve çağıran taraf eski (tüm sipariş iade) davranışına düşer — güvenli.
    """
    out: dict = {}
    if not order_numbers:
        return out

    def _put(onum: str, bc: str, q: int):
        if not onum or not bc or q <= 0:
            return
        d = out.setdefault(str(onum), {})
        d[str(bc)] = d.get(str(bc), 0) + int(q)

    for i in range(0, len(order_numbers), 5000):
        chunk = order_numbers[i:i + 5000]
        async for c in db.trendyol_claims.find(
                {"order_number": {"$in": chunk}, "claim_status": "Accepted",
                 "claim_type": {"$ne": "CANCEL"}},
                {"_id": 0, "order_number": 1, "items": 1}):
            for it in (c.get("items") or []):
                _put(c.get("order_number"), (it or {}).get("barcode"),
                     max(1, int((it or {}).get("quantity") or 1)))
        async for r in db.customer_returns.find(
                {"order_number": {"$in": chunk}},
                {"_id": 0, "order_number": 1, "status": 1, "approved_items": 1, "items": 1}):
            if str(r.get("status") or "").lower() in ("cancelled", "canceled", "rejected", "return_rejected"):
                continue
            for it in (r.get("approved_items") or r.get("items") or []):
                _put(r.get("order_number"),
                     (it or {}).get("barcode") or (it or {}).get("sku"),
                     max(1, int((it or {}).get("quantity") or 1)))
    return out


@router.get("/sales-breakdown")
async def sales_breakdown(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    """Ciro kırılımı — 4 kademe + adet + açık-iade projeksiyonu:
      ① included = İptal + İade DAHİL toplam (net + iptal + iade)
      ② cancels  = iptal edilen tutar (kaybedilen)
      ③ returns  = iade edilen tutar — KISMİ iadede yalnız iade edilen ürünlerin payı
      ④ net      = elimizde kalan net ciro (kısmi iadede kalan ürünler burada kalır)
      ⑤ pending_returns = henüz onaylanmamış iade talepleri; onaylanırsa net'ten düşecek
      ⑥ projected_net    = ④ − ⑤ (açık iadeler onaylanırsa oluşacak net)

    Her kovada `units` = ÜRÜN ADEDİ (kalem adetleri toplamı) — pazaryeri raporlarıyla
    (Trendyol 'Brüt Satış Adedi') aynı birim. `orders` = sipariş sayısı.
    Tarih aralığı TR yerel gün, kaynak filtreli."""
    s, e = _iso_range(start_date, end_date)
    base = {"created_at": {"$gte": s, "$lte": e}}
    sc = _source_cond(source)
    if sc:
        base.update(sc)

    proj = {"_id": 0, "id": 1, "order_number": 1, "status": 1, "total": 1,
            "items.quantity": 1, "partial_cancel_amount": 1, "partial_cancel_units": 1}
    orders = [o async for o in db.orders.find(base, proj)]
    onums = list({str(o.get("order_number")) for o in orders if o.get("order_number")})
    oids = list({str(o.get("id")) for o in orders if o.get("id")})
    closed, open_ = await _split_maps(onums, oids)

    return _bucket_orders(orders, closed, open_)



@router.get("/products/export-xlsx")
async def products_export_xlsx(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Ürün raporunun Excel çıktısı — ekrandaki listeyle aynı veri (tüm ürünler,
    iptal/iade kolonları dahil). Kolon sıralamayı Excel içinde yapabilirsiniz."""
    import openpyxl
    from io import BytesIO as _BytesIO
    from fastapi.responses import Response as _Response
    data = await top_products(limit=5000, start_date=start_date, end_date=end_date,
                              source=source, current_user=current_user)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ürün Raporu"
    ws.append(["Ürün", "Sezon", "Satış Adedi", "Ciro (TL)", "Sipariş", "Güncel Stok",
               "Kapsama (Hafta)", "En Çok Satan Beden", "En Çok Satan Platform", "Haftalık Hız",
               "İptal Adet", "İade Adet", "İade %", "Platform İptal/İade Detay"])
    # Satış hızı: renkli hücre (yeşil/sarı/kırmızı) + etiket — panelle birebir aynı kodlama
    from openpyxl.styles import PatternFill
    _VEL_FILL = {"green": PatternFill("solid", fgColor="C6EFCE"),
                 "yellow": PatternFill("solid", fgColor="FFEB9C"),
                 "red": PatternFill("solid", fgColor="FFC7CE")}
    _VEL_LABEL = {"green": "Hızlı", "yellow": "Orta", "red": "Yavaş"}
    for r in data.get("items", []):
        _crd = "; ".join(f"{x['platform']}: iptal {x['cancel']} / iade {x['return']}"
                         for x in (r.get("cancel_return_by_platform") or []))
        _vel = r.get("velocity") or {}
        _vcode = _vel.get("code") or ""
        _wr = float(_vel.get("weekly_rate") or 0)
        _stok = r.get("current_stock")
        _cover = round(_stok / _wr, 1) if (_stok is not None and _wr > 0) else ""
        _rq = int(r.get("return_qty") or 0)
        _tq = int(r.get("qty") or 0) + _rq
        _rpct = round(100 * _rq / _tq, 1) if _tq > 0 else ""
        ws.append([r.get("name"), r.get("season") or "", r.get("qty"), r.get("revenue"),
                   r.get("orders"), _stok, _cover, r.get("best_size"),
                   r.get("top_platform"),
                   f"{_VEL_LABEL.get(_vcode, '')} ({_vel.get('weekly_rate', 0)}/hafta)",
                   r.get("cancel_qty", 0), _rq, _rpct, _crd])
        _fill = _VEL_FILL.get(_vcode)
        if _fill:
            ws.cell(row=ws.max_row, column=10).fill = _fill
    for col, w in zip("ABCDEFGHIJKLMN", [42, 10, 12, 14, 10, 12, 14, 16, 18, 14, 10, 10, 8, 40]):
        ws.column_dimensions[col].width = w
    buf = _BytesIO()
    wb.save(buf)
    return _Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=urun-raporu.xlsx"})


@router.get("/products/top")
async def top_products(
    limit: int = Query(1000, ge=1, le=5000),   # varsayılan TÜM ürünler (yüksek tavan)
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    """ÜRÜN bazında satış raporu. Aynı ürünün farklı beden/renk kalemleri TEK satırda
    birleşir (mükerrer yok). Her ürün için: toplam adet + ciro + sipariş, güncel stok,
    EN ÇOK SATAN BEDEN ve PLATFORM DAĞILIMI döner. Frontend cirodan yükseğe sıralar/filtreler."""
    s, e = _iso_range(start_date, end_date, days_default=90)
    pipeline = [
        {"$match": _base_match(s, e, source)},
        # Sipariş platformu: platform > marketplace > 'site'
        {"$addFields": {"_plat": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$addFields": {
            "_nm": {"$ifNull": ["$items.name",
                     {"$ifNull": ["$items.product_name",
                       {"$ifNull": ["$items.productName", ""]}]}]},
            "_bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
            "_pid": {"$toString": {"$ifNull": ["$items.product_id", ""]}},
            "_sz": {"$toString": {"$ifNull": ["$items.size", ""]}},
        }},
        # Kalem anahtarı: barkod > product_id > ad. Beden ve platform gruplamaya dahil edilir
        # ki EN ÇOK SATAN BEDEN + platform dağılımı çıkarılabilsin (parent birleştirme Python'da).
        {"$addFields": {"_key": {"$switch": {"branches": [
            {"case": {"$ne": ["$_bc", ""]}, "then": {"$concat": ["bc:", "$_bc"]}},
            {"case": {"$ne": ["$_pid", ""]}, "then": {"$concat": ["pid:", "$_pid"]}},
        ], "default": {"$concat": ["nm:", "$_nm"]}}}}},
        {"$group": {
            "_id": {"k": "$_key", "sz": "$_sz", "plat": "$_plat"},
            "name": {"$first": "$_nm"},
            "barcode": {"$first": "$_bc"},
            "pid": {"$first": "$_pid"},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            "orders": {"$sum": 1},
        }},
    ]
    raw = []
    async for r in db.orders.aggregate(pipeline):
        raw.append(r)
    # Ürün eşleştirme: barkod/pid -> PARENT ürün (id, ad, stok). Birleştirme parent id ile yapılır.
    pids = [r.get("pid") for r in raw if r.get("pid")]
    bcs = [r.get("barcode") for r in raw if r.get("barcode")]
    by_id, by_bc = {}, {}
    if pids or bcs:
        _ors = []
        if pids:
            _ors.append({"id": {"$in": pids}})
        if bcs:
            _ors += [{"barcode": {"$in": bcs}}, {"variants.barcode": {"$in": bcs}}]
        # Silinmiş ürün kartları eşleşmeye girmez (kullanıcı isteği: silinmiş ürün raporda görünmesin)
        q = {"$and": [{"$or": _ors}, {"is_deleted": {"$ne": True}}]}
        async for p in db.products.find(q, {"_id": 0, "id": 1, "name": 1, "stock": 1, "variants": 1,
                                             "barcode": 1, "collection": 1, "created_at": 1, "stock_code": 1,
                                             "attributes": 1, "season": 1}):
            variants = p.get("variants") or []
            stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(p.get("stock") or 0)
            # Beden bazında KALAN stok (açılır satırdaki mini tabloya)
            _sbs = {}
            for v in variants:
                _vs = str(v.get("size") or "").strip() or "—"
                _sbs[_vs] = _sbs.get(_vs, 0) + int(v.get("stock") or 0)
            info = {"id": str(p.get("id")), "name": p.get("name") or "", "stock": stock,
                    "stock_by_size": _sbs,
                    "collection": (p.get("collection") or "").strip(),
                    "created_at": p.get("created_at") or None,
                    "stock_code": (p.get("stock_code") or "").strip(),
                    # Sezon: önce ürün kartındaki zorunlu 'season' alanı; eskiler için öznitelik fallback
                    "season": (p.get("season") or "").strip()}  # TEK KAYNAK: ürün kartındaki Sezon alanı
            by_id[str(p.get("id"))] = info
            if p.get("barcode"):
                by_bc[str(p["barcode"])] = info
            for v in variants:
                if v.get("barcode"):
                    by_bc[str(v["barcode"])] = info
    # PARENT ürün bazında birleştir → mükerrer beden/renk satırları tek ürün olur.
    merged = {}
    for r in raw:
        pm = by_bc.get(r.get("barcode") or "") or by_id.get(r.get("pid") or "") or {}
        name = pm.get("name") or r.get("name") or "(isimsiz ürün)"
        # Grup anahtarı: çözülen parent id > pid > ad (isim NFC normalize edilerek NFD mükerreri de birleşsin)
        import unicodedata as _ud
        _nkey = _ud.normalize("NFC", name).strip().lower()
        gkey = pm.get("id") or (r.get("pid") or None) or f"nm:{_nkey}"
        m = merged.get(gkey)
        if not m:
            m = merged[gkey] = {
                "product_id": pm.get("id") or r.get("pid"), "name": name,
                "_matched": bool(pm),  # katalogda hâlâ var olan bir ürüne bağlandı mı?
                "qty": 0, "revenue": 0.0, "orders": 0,
                "current_stock": pm.get("stock", None), "_sizes": {}, "_plats": {},
                "stock_by_size": pm.get("stock_by_size") or {},
                # Koleksiyon: stok kodu ön ekinden (fcfw/fcss) — filtre değerleri tek tip (FcFw/FCss)
                # kalsın diye önce kod ön eki; kod yoksa serbest metin collection alanına düşer.
                "collection": _collection_from_code(pm.get("stock_code"), r.get("barcode"), r.get("pid"))
                              or (pm.get("collection") or "").strip(),
                "created_at": pm.get("created_at"),
                "stock_code": pm.get("stock_code") or "",
                "season": pm.get("season") or "",
            }
        _q = int(r["qty"])
        m["qty"] += _q
        m["revenue"] += float(r["revenue"])
        m["orders"] += int(r["orders"])
        _sz = (r["_id"].get("sz") or "").strip() or "—"
        m["_sizes"][_sz] = m["_sizes"].get(_sz, 0) + _q
        _pl = (r["_id"].get("plat") or "site").strip().lower() or "site"
        m["_plats"][_pl] = m["_plats"].get(_pl, 0) + _q
    # Kullanıcı isteği: kataloğdan SİLİNMİŞ ya da hiçbir ürün kartına eşleşmeyen
    # kalemlerin satırları ürün raporunda GÖSTERİLMEZ (yalnız mevcut kartlar listelenir).
    merged = {k: m for k, m in merged.items() if m.pop("_matched", False)}
    # D4 — Satış hızı (velocity) renk kodu. Seçili tarih aralığının hafta sayısına göre
    # HAFTALIK ortalama satış hesaplanır: yeşil ≥5/hafta, sarı 1-4/hafta, kırmızı <1/hafta (~ayda 0-2).
    try:
        _sd = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        _ed = datetime.fromisoformat(str(e).replace("Z", "+00:00"))
        # tz'siz gelen sınır UTC varsayılır — aware−naive çıkarması TypeError atıp
        # sessizce 90 güne düşüyordu (30g raporda hız 3× düşük görünüyordu).
        if _sd.tzinfo is None:
            _sd = _sd.replace(tzinfo=timezone.utc)
        if _ed.tzinfo is None:
            _ed = _ed.replace(tzinfo=timezone.utc)
        _range_days = max(1.0, (_ed - _sd).total_seconds() / 86400.0)
    except Exception:
        _range_days = float(90)
    _weeks = max(1.0, _range_days / 7.0)

    # Hız eşikleri İşletme Kuralları'ndan (admin-ayarlanabilir): yeşil ≥13, sarı ≥5 varsayılan
    from business_rules import get_rule as _vel_rule
    try:
        _green_min = float(await _vel_rule(db, "report.velocity_green_min", 13) or 13)
        _yellow_min = float(await _vel_rule(db, "report.velocity_yellow_min", 5) or 5)
    except Exception:
        _green_min, _yellow_min = 13.0, 5.0

    def _velocity(qty: int):
        wr = qty / _weeks
        if wr >= _green_min:
            code, label = "green", "Hızlı"
        elif wr >= _yellow_min:
            code, label = "yellow", "Orta"
        else:
            code, label = "red", "Yavaş"
        return {"weekly_rate": round(wr, 1), "code": code, "label": label}

    # SATIŞI OLMAYAN ürünler de listelensin ("143 ürün" yalnız satışı olanlardı) —
    # aktif katalogda olup raporda görünmeyenler qty=0 satırıyla eklenir.
    _seen_ids = {m.get("product_id") for m in merged.values() if m.get("product_id")}
    async for p in db.products.find(
            {"is_active": True, "is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "stock": 1, "variants": 1, "collection": 1,
             "created_at": 1, "stock_code": 1, "attributes": 1, "season": 1}):
        if str(p.get("id")) in _seen_ids:
            continue
        variants = p.get("variants") or []
        stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(p.get("stock") or 0)
        _sbs0 = {}
        for v in variants:
            _vs = str(v.get("size") or "").strip() or "—"
            _sbs0[_vs] = _sbs0.get(_vs, 0) + int(v.get("stock") or 0)
        merged[f"zero:{p.get('id')}"] = {
            "product_id": str(p.get("id")), "name": p.get("name") or "",
            "qty": 0, "revenue": 0.0, "orders": 0, "current_stock": stock,
            "stock_by_size": _sbs0,
            "_sizes": {}, "_plats": {},
            "collection": _collection_from_code(p.get("stock_code")) or (p.get("collection") or "").strip(),
            "created_at": p.get("created_at"), "stock_code": (p.get("stock_code") or "").strip(),
            "season": (p.get("season") or "").strip(),  # TEK KAYNAK: ürün kartındaki Sezon alanı (öznitelik fallback kaldırıldı)
        }

    # İPTAL & İADE — ürün bazında, platform kırılımlı (aynı kalem-anahtar çözümüyle)
    _CR_CANCEL = ["cancelled", "cancel_refunded"]
    _CR_RETURN = ["return_requested", "return_approved", "return_in_transit",
                  "returned", "refunded", "partial_refunded"]
    _cr_pipe = [
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$in": _CR_CANCEL + _CR_RETURN}}},
        {"$addFields": {"_plat": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}},
                        "_kind": {"$cond": [{"$in": ["$status", _CR_CANCEL]}, "cancel", "return"]}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": {"bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
                            "pid": {"$toString": {"$ifNull": ["$items.product_id", ""]}},
                            "nm": {"$ifNull": ["$items.name", {"$ifNull": ["$items.product_name", ""]}]},
                            "sz": {"$toString": {"$ifNull": ["$items.size", ""]}},
                            # Kısmi iade ayrıştırması sipariş bazında yapılır → grup anahtarına
                            # sipariş no eklendi (hangi kalemin iade edildiği siparişe bağlı).
                            "on": {"$toString": {"$ifNull": ["$order_number", ""]}},
                            "kind": "$_kind", "plat": "$_plat"},
                    "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                    "rev": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]},
                                                   {"$ifNull": ["$items.quantity", 1]}]}}}},
    ]
    import unicodedata as _ud3
    _cr_rows = [r async for r in db.orders.aggregate(_cr_pipe)]
    # DENETİM HATA-4: pencerede hiç SATIŞI olmayan ürünün iadesi by_bc/by_id'de
    # bulunamayıp isim-anahtarına düşüyor, katalogdaki ürün satırıyla birleşemiyordu
    # (iade kayboluyordu). İade kalemlerinin barkod/pid'leri için ek ürün sorgusu yapılır.
    _miss_bc = {r["_id"].get("bc") for r in _cr_rows
                if r["_id"].get("bc") and r["_id"]["bc"] not in by_bc}
    _miss_pid = {r["_id"].get("pid") for r in _cr_rows
                 if r["_id"].get("pid") and r["_id"]["pid"] not in by_id}
    if _miss_bc or _miss_pid:
        _q2 = {"$or": []}
        if _miss_pid:
            _q2["$or"].append({"id": {"$in": list(_miss_pid)}})
        if _miss_bc:
            _q2["$or"] += [{"barcode": {"$in": list(_miss_bc)}},
                           {"variants.barcode": {"$in": list(_miss_bc)}}]
        async for p in db.products.find(_q2, {"_id": 0, "id": 1, "name": 1, "barcode": 1, "variants.barcode": 1}):
            _inf = {"id": str(p.get("id")), "name": p.get("name") or ""}
            by_id.setdefault(str(p.get("id")), _inf)
            if p.get("barcode"):
                by_bc.setdefault(str(p["barcode"]), _inf)
            for v in (p.get("variants") or []):
                if v.get("barcode"):
                    by_bc.setdefault(str(v["barcode"]), _inf)
    # KISMİ İADE AYRIŞTIRMASI: iade statüsündeki siparişin yalnız GERÇEKTEN iade edilen
    # kalemleri İade'ye yazılır; müşteride kalan kalemler satış tarafına geri eklenir.
    _ret_onums = list({r["_id"].get("on") for r in _cr_rows
                       if r["_id"].get("kind") == "return" and r["_id"].get("on")})
    _ret_bc = await _returned_barcode_qty(_ret_onums)
    cr_map: dict = {}
    keep_map: dict = {}   # kısmi iadede müşteride KALAN kalemler → satışa geri döner
    for r in _cr_rows:
        i = r["_id"]
        pm = by_bc.get(i.get("bc") or "") or by_id.get(i.get("pid") or "") or {}
        _cname = pm.get("name") or i.get("nm") or "(isimsiz ürün)"
        gk = pm.get("id") or (i.get("pid") or None) or f"nm:{_ud3.normalize('NFC', _cname).strip().lower()}"
        qty = int(r["qty"])
        rev = float(r.get("rev") or 0)
        keep_q, keep_r = 0, 0.0
        if i["kind"] == "return":
            _rb = _ret_bc.get(i.get("on") or "")
            if _rb:  # kalem bilgisi VAR → böl. Yoksa eski davranış (tüm sipariş iade).
                _bc = str(i.get("bc") or "")
                _allowed = int(_rb.get(_bc, 0))
                _ret_q = min(qty, _allowed)
                _rb[_bc] = _allowed - _ret_q          # aynı barkod başka satırda tekrar sayılmasın
                keep_q = qty - _ret_q
                if keep_q > 0 and qty > 0:
                    keep_r = rev * (keep_q / qty)
                qty = _ret_q
        if keep_q > 0:
            k = keep_map.setdefault(gk, {"qty": 0, "revenue": 0.0, "sizes": {}, "plats": {}})
            k["qty"] += keep_q
            k["revenue"] += keep_r
            _ksz = (i.get("sz") or "").strip() or "—"
            k["sizes"][_ksz] = k["sizes"].get(_ksz, 0) + keep_q
            _kpl = i.get("plat") or "site"
            k["plats"][_kpl] = k["plats"].get(_kpl, 0) + keep_q
        if qty <= 0:
            continue
        d = cr_map.setdefault(gk, {"cancel": 0, "return": 0, "by_plat": {}, "by_size": {}})
        d[i["kind"]] += qty
        bp = d["by_plat"].setdefault((i.get("plat") or "site"), {"cancel": 0, "return": 0})
        bp[i["kind"]] += qty
        bs = d["by_size"].setdefault(((i.get("sz") or "").strip() or "—"), {"cancel": 0, "return": 0})
        bs[i["kind"]] += qty

    out = []
    for gkey, m in merged.items():
        _sizes = sorted(m.pop("_sizes").items(), key=lambda x: -x[1])
        _plats = sorted(m.pop("_plats").items(), key=lambda x: -x[1])
        _cr = cr_map.get(m.get("product_id") or gkey) or cr_map.get(gkey) or {}
        # Kısmi iadede müşteride kalan kalemler satış tarafına geri eklenir (satış
        # pipeline'ı iade statüsündeki siparişin TAMAMINI dışladığı için burada telafi).
        _kp = keep_map.get(m.get("product_id") or gkey) or keep_map.get(gkey)
        if _kp:
            m["qty"] = int(m.get("qty") or 0) + int(_kp["qty"])
            m["revenue"] = float(m.get("revenue") or 0) + float(_kp["revenue"])
            _sd = dict(_sizes)
            for _sk, _sv in _kp["sizes"].items():
                _sd[_sk] = _sd.get(_sk, 0) + _sv
            _sizes = sorted(_sd.items(), key=lambda x: -x[1])
            _pd = dict(_plats)
            for _pk, _pv in _kp["plats"].items():
                _pd[_pk] = _pd.get(_pk, 0) + _pv
            _plats = sorted(_pd.items(), key=lambda x: -x[1])
        out.append({
            **m,
            "revenue": round(m["revenue"], 2),
            "best_size": _sizes[0][0] if _sizes else "—",
            "size_breakdown": [{"size": k, "qty": v} for k, v in _sizes],
            "top_platform": _plats[0][0] if _plats else "site",
            "platform_breakdown": [{"platform": k, "qty": v} for k, v in _plats],
            "velocity": _velocity(int(m["qty"])),
            "cancel_qty": int(_cr.get("cancel", 0)),
            "return_qty": int(_cr.get("return", 0)),
            "cancel_return_by_platform": [
                {"platform": k, "cancel": v["cancel"], "return": v["return"]}
                for k, v in sorted((_cr.get("by_plat") or {}).items())],
            # Beden bazlı iptal/iade — açılır satırdaki Toplam/İptal/İade/Net kırılımı için
            "cancel_return_by_size": [
                {"size": k, "cancel": v["cancel"], "return": v["return"]}
                for k, v in sorted((_cr.get("by_size") or {}).items())],
        })
    out.sort(key=lambda x: -x["revenue"])
    return {"items": out[:limit], "range_days": round(_range_days, 1), "weeks": round(_weeks, 1)}


# ============================================================================
# KÂRLILIK ANALİZİ (Melontik-tarzı) — kategori × pazaryeri NET kâr
# Gider kalemleri: COGS + komisyon + kargo + hizmet bedeli + reklam + KDV + kurumlar vergisi.
# Oranlar db.settings id="profitability_config" tan; yoksa TR gerçeğine göre varsayılan.
# ============================================================================
_PROFIT_DEFAULTS = {
    # Pazaryeri komisyonu (%) — kategori bazında değişir; buradan pazaryeri-geneli ayarlanır.
    "commission_pct": {"trendyol": 18.0, "hepsiburada": 17.0, "temu": 5.0,
                       "n11": 12.0, "amazon": 15.0, "site": 3.0, "manual": 0.0},
    # Hizmet/işlem bedeli (%) — Trendyol hizmet bedeli vb. (varsayılan 0, kullanıcı girer)
    "service_fee_pct": {"trendyol": 0.0, "hepsiburada": 0.0, "temu": 0.0, "site": 0.0},
    # Dönem TOPLAM reklam gideri (TL) — kanal bazında; ciro payına göre kategorilere dağıtılır.
    "ad_spend": {"trendyol": 0.0, "hepsiburada": 0.0, "site": 0.0},
    # AYLIK reklam bütçesi (TL) — kanal bazında; seçili tarih aralığına OTOMATİK orantılanır
    # (Trendyol reklam verisi API'de olmadığından: aylık gir, rapor gün sayısına göre böler).
    # Bir kanalda ad_spend_monthly>0 ise o kanalda ad_spend yerine bu (orantılı) kullanılır.
    "ad_spend_monthly": {"trendyol": 0.0, "hepsiburada": 0.0, "site": 0.0},
    "packaging_per_order": 0.0,   # sipariş başı paketleme/operasyon (TL)
    "vat_rate": 10.0,             # KDV (%)
    "corporate_tax_pct": 25.0,    # Kurumlar vergisi (2025 TR)
    "cog_fallback_ratio": 0.5,    # maliyet bilinmiyorsa satış fiyatının %'si
}


async def _profitability_config() -> dict:
    doc = await db.settings.find_one({"id": "profitability_config"}, {"_id": 0}) or {}
    cfg = {**_PROFIT_DEFAULTS}
    for k, v in doc.items():
        if k == "id":
            continue
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


@router.get("/profitability-config")
async def get_profitability_config(current_user: dict = Depends(require_admin)):
    """Kârlılık analizi gider oran/varsayımları (komisyon, reklam, hizmet bedeli, KDV, kurumlar vergisi)."""
    return {"config": await _profitability_config(), "defaults": _PROFIT_DEFAULTS}


@router.put("/profitability-config")
async def set_profitability_config(payload: dict, current_user: dict = Depends(require_admin)):
    """Kârlılık gider varsayımlarını günceller (yalnız gönderilen alanlar birleşir)."""
    payload = {k: v for k, v in (payload or {}).items() if k in _PROFIT_DEFAULTS}
    await db.settings.update_one({"id": "profitability_config"}, {"$set": {"id": "profitability_config", **payload}}, upsert=True)
    return {"success": True, "config": await _profitability_config()}


@router.get("/profitability")
async def profitability(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    """KÂRLILIK ANALİZİ — kategori bazında (kaynak filtreli) tüm giderler düşülerek NET kâr.
    Kalemler: Ciro − COGS − Komisyon − Kargo − Hizmet Bedeli − Reklam − İade − KDV − Kurumlar Vergisi.
    Oranlar profitability-config'ten; maliyet ürün purchase_price/product_costs'tan (yoksa oranla tahmin)."""
    s, e = _iso_range(start_date, end_date, days_default=30)
    cfg = await _profitability_config()
    # Tarih aralığı gün sayısı — aylık reklam bütçesini orantılamak için.
    try:
        _d0 = datetime.fromisoformat(s.replace("Z", "+00:00"))
        _d1 = datetime.fromisoformat(e.replace("Z", "+00:00"))
        _days_range = max(1, (_d1 - _d0).days + 1)
    except Exception:
        _days_range = 30
    _CH_ALIAS = {"facette": "site", "": "site", "web": "site", "admin_manual": "manual", "admin": "manual"}
    from collections import defaultdict as _dd

    # Ana kırılım: (kategori, kanal) → ciro, maliyet, adet, sipariş. Maliyet: purchase_price köprüsü.
    pipeline = [
        {"$match": _base_match(s, e, source)},
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
                {"$project": {"_id": 0, "category_name": 1, "purchase_price": 1, "cost_price": 1}},
            ],
            "as": "p",
        }},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        # Birim maliyet zinciri: purchase_price (>0) → cost_price. Denetim: eskiden yalnız
        # purchase_price okunup cost_price (canlı senkron maliyeti) yok sayılıyordu.
        {"$addFields": {"_unit_cost": {"$let": {
            "vars": {"pp": {"$ifNull": ["$p.purchase_price", 0]}, "cp": {"$ifNull": ["$p.cost_price", 0]}},
            "in": {"$cond": [{"$gt": ["$$pp", 0]}, "$$pp", "$$cp"]}}}}},
        {"$group": {
            "_id": {"cat": {"$ifNull": ["$p.category_name",
                        {"$ifNull": ["$items.category_name", {"$ifNull": ["$items.category", "(Kategorisiz)"]}]}]},
                    "ch": "$_ch"},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            "cogs": {"$sum": {"$multiply": [{"$ifNull": ["$_unit_cost", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            "revenue_nocost": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$_unit_cost", 0]}, 0]}, 0,
                                {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}]}},
        }},
    ]
    # Kanal başına toplam kargo + İNDİRİM (ciro payına göre kategorilere dağıtılır).
    # İndirim: order.discount = promo motorunun kupon+otomatik kampanya toplamı; kalem
    # fiyatındaki (sale_price) indirim ZATEN items.price'ta olduğundan bu AYRI/EK indirimdir.
    # Denetim bulgusu: eskiden kâr hesabından hiç düşülmüyordu → kâr indirim kadar şişiyordu.
    cargo_by_ch = _dd(float)
    async for r in db.orders.aggregate([
        {"$match": _base_match(s, e, source)},
        {"$addFields": {"_ch": {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}}}},
        {"$group": {"_id": "$_ch", "shipping": {"$sum": {"$ifNull": ["$shipping_cost", 0]}},
                    "discount": {"$sum": {"$ifNull": ["$discount", {"$ifNull": ["$discount_amount", 0]}]}},
                    "orders": {"$sum": 1}}},
    ]):
        cargo_by_ch[_CH_ALIAS.get((r["_id"] or "site"), r["_id"] or "site")] = {
            "shipping": float(r["shipping"] or 0), "discount": float(r["discount"] or 0),
            "orders": int(r["orders"])}

    # Kategori-kanal satırlarını topla
    rows = []
    rev_by_ch = _dd(float)
    async for r in db.orders.aggregate(pipeline):
        ch = _CH_ALIAS.get((r["_id"].get("ch") or "site"), r["_id"].get("ch") or "site")
        rev = float(r["revenue"] or 0)
        cogs = float(r["cogs"] or 0)
        # Maliyeti bilinmeyen kalemler için oranla tahmin ekle
        cogs += float(r.get("revenue_nocost") or 0) * float(cfg["cog_fallback_ratio"])
        rows.append({"category": r["_id"].get("cat") or "(Kategorisiz)", "channel": ch,
                     "qty": int(r["qty"]), "revenue": rev, "cogs": round(cogs, 2)})
        rev_by_ch[ch] += rev
    total_rev = sum(rev_by_ch.values()) or 1.0

    vat = float(cfg["vat_rate"])
    corp = float(cfg["corporate_tax_pct"])
    out = []
    for row in rows:
        ch = row["channel"]
        rev = row["revenue"]
        cogs = row["cogs"]
        commission = rev * float(cfg["commission_pct"].get(ch, 5.0)) / 100.0
        service_fee = rev * float(cfg["service_fee_pct"].get(ch, 0.0)) / 100.0
        # Kargo: kanal toplam kargosunu bu satırın ciro payına göre dağıt
        _ch_share = (rev / rev_by_ch[ch]) if rev_by_ch.get(ch) else 0.0
        ch_cargo = (cargo_by_ch.get(ch) or {}).get("shipping", 0.0)
        cargo = ch_cargo * _ch_share
        # İndirim (kupon+kampanya): kanal toplam indirimini ciro payına göre dağıtıp DÜŞ.
        ch_disc = (cargo_by_ch.get(ch) or {}).get("discount", 0.0)
        discount_alloc = ch_disc * _ch_share
        # Reklam: AYLIK bütçe girildiyse tarih aralığına orantıla (aylık × gün/30), yoksa dönem toplamı.
        _monthly = float((cfg.get("ad_spend_monthly") or {}).get(ch, 0.0))
        ad_total = round(_monthly * _days_range / 30.0, 2) if _monthly > 0 else float(cfg["ad_spend"].get(ch, 0.0))
        ad_alloc = ad_total * (rev / rev_by_ch[ch]) if rev_by_ch.get(ch) else 0.0
        operating = rev - discount_alloc - cogs - commission - service_fee - cargo - ad_alloc
        # KDV (net ödenecek — katma değer üzerinden): (net ciro - maliyet) içindeki KDV.
        # Net ciro = brüt ciro - indirim (matrah indirim düşülmüş tutar üzerinden hesaplanır).
        vat_payable = max(0.0, (rev - discount_alloc - cogs)) * vat / (100.0 + vat)
        pre_tax = operating - vat_payable
        corporate_tax = max(0.0, pre_tax) * corp / 100.0
        net = pre_tax - corporate_tax
        out.append({
            "category": row["category"], "channel": ch, "qty": row["qty"],
            "revenue": round(rev, 2), "discount": round(discount_alloc, 2), "cogs": round(cogs, 2),
            "commission": round(commission, 2), "service_fee": round(service_fee, 2),
            "cargo": round(cargo, 2), "ad_spend": round(ad_alloc, 2),
            "vat_payable": round(vat_payable, 2), "corporate_tax": round(corporate_tax, 2),
            "net_profit": round(net, 2),
            "margin_pct": round(net / rev * 100.0, 1) if rev else 0.0,
        })
    out.sort(key=lambda x: -x["net_profit"])
    # Toplamlar
    def _sum(k):
        return round(sum(x[k] for x in out), 2)
    totals = {k: _sum(k) for k in ("revenue", "cogs", "commission", "service_fee", "cargo",
                                    "ad_spend", "vat_payable", "corporate_tax", "net_profit")}
    totals["qty"] = sum(x["qty"] for x in out)
    totals["margin_pct"] = round(totals["net_profit"] / totals["revenue"] * 100.0, 1) if totals["revenue"] else 0.0
    return {"items": out, "totals": totals, "config": cfg}


@router.get("/categories")
async def category_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    s, e = _iso_range(start_date, end_date, days_default=90)
    # items -> product: product_id VEYA barkod (üst/varyant) ile eşleştir (pazaryeri kalemleri
    # productCode taşır, Facette id'siyle eşleşmez → barkod köprüsü şart). Kategori adı: kategori
    # dokümanı > ürünün category_name'i > kalemin kendi category'si > (Kategorisiz).
    pipeline = [
        {"$match": _base_match(s, e, source)},
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
                {"$project": {"_id": 0, "category_id": 1, "category_name": 1}},
            ],
            "as": "p",
        }},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {"from": "categories", "localField": "p.category_id", "foreignField": "id", "as": "c"}},
        {"$unwind": {"path": "$c", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {"$ifNull": ["$c.name",
                         {"$ifNull": ["$p.category_name",
                           {"$ifNull": ["$items.category_name",
                             {"$ifNull": ["$items.category", "(Kategorisiz)"]}]}]}]},
                "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            }
        },
        {"$sort": {"revenue": -1}},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        out.append({"category": r["_id"] or "(Kategorisiz)", "qty": r["qty"], "revenue": round(r["revenue"], 2)})
    return {"items": out}


@router.get("/stock")
async def stock_report(current_user: dict = Depends(require_admin)):
    # DENETİM FIX: stok varyantlı üründe top-level 'stock'ta DEĞİL variants[].stock'ta tutulur.
    # Eski rapor yalnız top-level 'stock'a bakıyordu → varyantlı ürünler yanlışlıkla 'tükendi'
    # sayılıyor, toplam adet/değer ve kritik/tükenen listeleri hatalı çıkıyordu. Efektif stok:
    # varyant varsa varyant toplamı, yoksa top-level stock.
    _eff = {"$cond": [{"$gt": [{"$size": {"$ifNull": ["$variants", []]}}, 0]},
                      {"$sum": {"$map": {"input": "$variants", "as": "v",
                                         "in": {"$convert": {"input": "$$v.stock", "to": "int", "onError": 0, "onNull": 0}}}}},
                      {"$convert": {"input": "$stock", "to": "int", "onError": 0, "onNull": 0}}]}
    low, out_of_stock = [], []
    units = 0
    value = 0.0
    async for r in db.products.aggregate([
        # Denetim: silinmiş (çöp kutusu) + pasif ürünler stok toplam/değerine katılmasın.
        {"$match": {"is_deleted": {"$ne": True}, "is_active": {"$ne": False}}},
        {"$project": {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "price": 1, "eff": _eff}}
    ]):
        s = int(r.get("eff") or 0)
        units += s
        try:
            value += s * float(r.get("price") or 0)
        except Exception:
            pass
        row = {"id": r.get("id"), "name": r.get("name"), "stock_code": r.get("stock_code"), "stock": s}
        if s <= 0:
            out_of_stock.append(row)
        elif s <= 5:
            low.append(row)
    low.sort(key=lambda x: x["stock"])
    return {
        "low_stock": low[:100],
        "out_of_stock": out_of_stock[:200],
        "totals": {"units": units, "value": round(value, 2)},
    }


@router.get("/payments")
async def payment_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_admin),
):
    s, e = _iso_range(start_date, end_date)
    # Pazaryeri siparişleri "marketplace" olarak tek kalemde toplanıyordu — artık
    # platforma göre Trendyol / Hepsiburada / Temu olarak AYRI gösterilir.
    _plat = {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", ""]}]}}
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$group": {"_id": {"$cond": [
            {"$in": [_plat, ["trendyol", "hepsiburada", "temu"]]},
            _plat,
            {"$ifNull": ["$payment_method", "—"]}]},
            "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"revenue": -1}},
    ]
    _LABELS = {
        "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu",
        "credit_card": "Kredi Kartı", "card": "Kredi Kartı", "iyzico": "Kredi Kartı",
        "bank_transfer": "Havale/EFT", "havale": "Havale/EFT", "eft": "Havale/EFT",
        "havale_eft": "Havale/EFT", "banka_havale": "Havale/EFT",
        "cash_on_delivery": "Kapıda Ödeme", "kapida": "Kapıda Ödeme", "cod": "Kapıda Ödeme",
        "gift_card": "Hediye Çeki", "marketplace": "Pazaryeri (diğer)",
    }
    merged: dict = {}
    async for r in db.orders.aggregate(pipeline):
        key = str(r["_id"] or "—").lower()
        label = _LABELS.get(key, r["_id"] or "—")
        m = merged.setdefault(label, {"orders": 0, "revenue": 0.0})
        m["orders"] += r["orders"]
        m["revenue"] += float(r["revenue"] or 0)
    out = [{"method": k, "orders": v["orders"], "revenue": round(v["revenue"], 2)}
           for k, v in sorted(merged.items(), key=lambda kv: -kv[1]["revenue"])]
    return {"items": out}


@router.get("/sales-by-platform")
async def sales_by_platform(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Kanal Bazında Satış — yalnız SİTE + PAZARYERLERİ (Instagram/Google gibi trafik
    kaynakları DEĞİL; sipariş platform alanından). İptal/iade hariç."""
    s, e = _iso_range(start_date, end_date)
    _plat = {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}}
    pipeline = [
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$group": {"_id": {"$cond": [{"$in": [_plat, ["trendyol", "hepsiburada", "temu"]]}, _plat, "site"]},
                    "orders": {"$sum": 1},
                    "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"revenue": -1}},
    ]
    _SRC = {"site": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu"}
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"channel": _SRC.get(r["_id"], r["_id"]), "orders": r["orders"],
                     "revenue": round(float(r["revenue"] or 0), 2)})
    return {"rows": rows}


@router.get("/cancel-return-products")
async def cancel_return_products(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin),
):
    """Ürün bazlı İADE & İPTAL raporu (Ürün Raporları altındaki alan): tarih aralığında
    iptal/iade edilen siparişlerin kalemleri — ürün adı + platform kırılımıyla.
    Frontend ada göre arar, platforma göre süzer."""
    s, e = _iso_range(start_date, end_date)
    _RETURN_ST = ["return_requested", "return_approved", "return_in_transit",
                  "returned", "refunded", "partial_refunded"]
    _CANCEL_ST = ["cancelled", "cancel_refunded"]
    m = {"created_at": {"$gte": s, "$lte": e}, "status": {"$in": _RETURN_ST + _CANCEL_ST}}
    sc = _source_cond(source)
    if sc:
        m.update(sc)
    _plat = {"$toLower": {"$ifNull": ["$platform", {"$ifNull": ["$marketplace", "site"]}]}}
    pipeline = [
        {"$match": m},
        {"$addFields": {"_plat": _plat,
                        "_kind": {"$cond": [{"$in": ["$status", _CANCEL_ST]}, "cancel", "return"]}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {
            "_id": {"name": {"$ifNull": ["$items.name", {"$ifNull": ["$items.product_name", "Ürün"]}]},
                    "plat": "$_plat", "kind": "$_kind",
                    # Kısmi iade ayrıştırması sipariş+barkod bazında yapılır.
                    "on": {"$toString": {"$ifNull": ["$order_number", ""]}},
                    "bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}}},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            # DENETİM HATA-3: unit_price İSKONTO ÖNCESİ liste fiyatı — tutarlar %11-14 şişiyordu.
            # Önce items.price (ödenen net birim), yoksa unit_price kullanılır (by-source ile eşitlenir).
            "total": {"$sum": {"$multiply": [
                {"$ifNull": ["$items.quantity", 1]},
                {"$ifNull": ["$items.price", {"$ifNull": ["$items.unit_price", 0]}]}]}},
        }},
    ]
    _SRC = {"site": "Site", "facette": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu"}
    rows: dict = {}
    _agg = [r async for r in db.orders.aggregate(pipeline)]
    # KISMİ İADE: yalnız gerçekten iade edilen kalem İade'ye yazılır (ürün raporunun
    # ana tablosuyla aynı mantık — aksi halde aynı ekranda iki farklı iade adedi çıkar).
    _ret_bc = await _returned_barcode_qty(
        list({r["_id"].get("on") for r in _agg
              if r["_id"].get("kind") == "return" and r["_id"].get("on")}))
    for r in _agg:
        # 'facette' etiketi de Site'dır (DENETİM HATA-3 eki) — aynı ürünün iki satırı birleşsin
        _pl = "site" if (r["_id"]["plat"] or "site") in ("site", "facette") else r["_id"]["plat"]
        key = (r["_id"]["name"], _pl)
        d = rows.setdefault(key, {"name": r["_id"]["name"],
                                  "platform": _SRC.get(_pl, _pl or "Site"),
                                  "cancel_qty": 0, "cancel_total": 0.0,
                                  "return_qty": 0, "return_total": 0.0})
        _q = int(r["qty"])
        _t = float(r["total"] or 0)
        if r["_id"]["kind"] == "cancel":
            d["cancel_qty"] += _q
            d["cancel_total"] += _t
        else:
            _rb = _ret_bc.get(r["_id"].get("on") or "")
            if _rb:  # kalem bilgisi VAR → böl; yoksa eski davranış (tüm sipariş iade)
                _bc = str(r["_id"].get("bc") or "")
                _allowed = int(_rb.get(_bc, 0))
                _rq = min(_q, _allowed)
                _rb[_bc] = _allowed - _rq
                _t = _t * (_rq / _q) if _q else 0.0
                _q = _rq
            if _q <= 0:
                continue
            d["return_qty"] += _q
            d["return_total"] += _t
    out = sorted(rows.values(), key=lambda x: -(x["cancel_qty"] + x["return_qty"]))[:800]
    for d in out:
        d["cancel_total"] = round(d["cancel_total"], 2)
        d["return_total"] = round(d["return_total"], 2)
    return {"items": out}


@router.get("/cancel-return-by-source")
async def cancel_return_by_source(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Kanal (Site/Trendyol/Hepsiburada/Temu) bazında satış · iptal · iade.

    Ciro kartlarıyla (sales-breakdown) AYNI `_bucket_orders` mantığını kullanır:
    kısmi iadede yalnız iade edilen kalemin tutarı/adedi İade'ye yazılır, kalan
    ürünler Net'te kalır. Eskiden bu uç siparişin TAMAMINI iadeye yazdığı için
    aynı ekranda kartlarla çelişen bir "İade Tutarı" gösteriyordu.

    Her kanal için: sipariş · ADET · tutar (net/iptal/iade) + açık iade projeksiyonu.
    """
    s, e = _iso_range(start_date, end_date)
    proj = {"_id": 0, "id": 1, "order_number": 1, "status": 1, "total": 1,
            "items.quantity": 1, "partial_cancel_amount": 1, "partial_cancel_units": 1,
            "platform": 1, "marketplace": 1}
    orders = [o async for o in db.orders.find({"created_at": {"$gte": s, "$lte": e}}, proj)]
    onums = list({str(o.get("order_number")) for o in orders if o.get("order_number")})
    oids = list({str(o.get("id")) for o in orders if o.get("id")})
    closed, open_ = await _split_maps(onums, oids)

    _SRC = {"site": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu"}
    by_ch: dict = {}
    for o in orders:
        plat = str(o.get("platform") or o.get("marketplace") or "").strip().lower()
        key = plat if plat in _MARKETPLACES else "site"
        by_ch.setdefault(key, []).append(o)

    items = []
    for key, rows in by_ch.items():
        b = _bucket_orders(rows, closed, open_)
        items.append({
            "source": _SRC.get(key, key or "Site"),
            # Net (elde kalan) — kanal tablosunun "Sipariş / Ciro" sütunları
            "orders": b["net"]["orders"], "units": b["net"]["units"], "revenue": b["net"]["revenue"],
            # İptal
            "cancel_orders": b["cancels"]["orders"], "cancel_units": b["cancels"]["units"],
            "cancel_total": b["cancels"]["revenue"],
            # İade (kalem bazlı)
            "return_orders": b["returns"]["orders"], "return_units": b["returns"]["units"],
            "return_total": b["returns"]["revenue"],
            # Kaç iade siparişi KISMİ (siparişin bir kısmı iade, kalanı satışta)
            "return_partial_orders": b["returns"].get("partial_orders", 0),
            "cancel_partial_orders": b["cancels"].get("partial_orders", 0),
            # Toplam + açık iade projeksiyonu
            "total_orders": b["included"]["orders"], "total_units": b["included"]["units"],
            "total_revenue": b["included"]["revenue"],
            "pending_orders": b["pending_returns"]["orders"],
            "pending_units": b["pending_returns"]["units"],
            "pending_total": b["pending_returns"]["revenue"],
            "projected_revenue": b["projected_net"]["revenue"],
        })
    items.sort(key=lambda x: -x["total_revenue"])
    return {"items": items}


@router.get("/cargo")
async def cargo_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        # Denetim: ödenmemiş/iptal/iade siparişler kargolanmaz → kargo hacmine katılmasın.
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$group": {"_id": {"$ifNull": ["$cargo_provider_name", "$cargo.company"]}, "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$shipping_cost", 0]}}}},
        {"$sort": {"orders": -1}},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        out.append({"provider": r["_id"] or "Belirtilmemiş", "orders": r["orders"], "shipping_revenue": round(r["revenue"], 2)})
    return {"items": out}


@router.get("/members")
async def members_report(current_user: dict = Depends(require_admin)):
    # Top 20 members by spend
    pipeline = [
        {"$match": {"user_id": {"$ne": None}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$group": {"_id": "$user_id", "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}, "last_order": {"$max": "$created_at"}}},
        {"$sort": {"revenue": -1}},
        {"$limit": 20},
        {"$lookup": {"from": "users", "localField": "_id", "foreignField": "id", "as": "u"}},
        {"$unwind": {"path": "$u", "preserveNullAndEmptyArrays": True}},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        u = r.get("u") or {}
        out.append({
            "user_id": r["_id"],
            "name": f"{u.get('first_name','')} {u.get('last_name','')}".strip() or u.get("email", "—"),
            "email": u.get("email", "—"),
            "orders": r["orders"],
            "revenue": round(r["revenue"], 2),
            "last_order_at": r["last_order"],
        })
    return {"top_members": out}



# =============================================================================
# FAZ 8 — İade analizleri + hızlı satış dedektörü
# =============================================================================

@router.get("/returns/by-size")
async def returns_by_size(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """En çok iade edilen bedenleri döner (returns koleksiyonu)."""
    start, end = _iso_range(start_date, end_date, 180)
    pipeline = [
        {"$match": {"created_at": {"$gte": start, "$lte": end}, "status": {"$ne": "rejected"}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": {"size": {"$ifNull": ["$items.size", "—"]}},
            "count": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "orders": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 30},
    ]
    out = []
    async for r in db.customer_returns.aggregate(pipeline):
        out.append({"size": r["_id"]["size"], "count": r["count"], "order_count": r["orders"]})
    return {"by_size": out, "start": start, "end": end}


@router.get("/returns/by-product")
async def returns_by_product(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(require_admin),
):
    """En çok iade edilen ürünler + iade oranları (aynı dönem satışa göre)."""
    start, end = _iso_range(start_date, end_date, 180)
    ret_pipe = [
        {"$match": {"created_at": {"$gte": start, "$lte": end}, "status": {"$ne": "rejected"}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": "$items.product_id",
            "returned": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "product_name": {"$first": {"$ifNull": ["$items.name", "$items.product_name"]}},
        }},
        {"$sort": {"returned": -1}},
        {"$limit": limit},
    ]
    returns_map = {}
    async for r in db.customer_returns.aggregate(ret_pipe):
        returns_map[r["_id"]] = r

    product_ids = list(returns_map.keys())
    sales_map = {}
    if product_ids:
        sales_pipe = [
            {"$match": {"created_at": {"$gte": start, "$lte": end}, "status": {"$nin": _EXCLUDED_STATUSES}}},
            {"$unwind": "$items"},
            {"$match": {"items.product_id": {"$in": product_ids}}},
            {"$group": {
                "_id": "$items.product_id",
                "sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            }},
        ]
        async for r in db.orders.aggregate(sales_pipe):
            sales_map[r["_id"]] = r["sold"]

    out = []
    for pid, r in returns_map.items():
        sold = sales_map.get(pid, 0)
        rate = (r["returned"] / sold * 100) if sold else None
        out.append({
            "product_id": pid,
            "product_name": r.get("product_name") or "—",
            "returned": r["returned"],
            "sold": sold,
            "return_rate_pct": round(rate, 1) if rate is not None else None,
        })
    return {"items": out, "start": start, "end": end}


@router.get("/returns/reasons")
async def returns_by_reason(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """İade sebeplerine göre dağılım."""
    start, end = _iso_range(start_date, end_date, 180)
    pipeline = [
        {"$match": {"created_at": {"$gte": start, "$lte": end}, "status": {"$ne": "rejected"}}},
        {"$group": {"_id": {"$ifNull": ["$reason", "Belirtilmemiş"]}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    out = []
    async for r in db.customer_returns.aggregate(pipeline):
        out.append({"reason": r["_id"], "count": r["count"]})
    return {"reasons": out, "start": start, "end": end}


@router.get("/fast-selling")
async def fast_selling_products(
    window_days: int = Query(14, ge=1, le=90),
    min_sold: int = Query(10, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_admin),
):
    """Hızlı satış dedektörü: son N gün içinde ≥ min_sold adet satan ürünler.
    recommend_ads = true → ilk 60 gün içindeki yeni ürün + min sold'u geçti → reklam önerisi.
    """
    now = datetime.now(timezone.utc)
    start_ts = (now - timedelta(days=window_days)).isoformat()
    pipeline = [
        {"$match": {"created_at": {"$gte": start_ts}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": "$items.product_id",
            "sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "product_name": {"$first": {"$ifNull": ["$items.name", "$items.product_name"]}},
            "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
        }},
        {"$match": {"sold": {"$gte": min_sold}}},
        {"$sort": {"sold": -1}},
        {"$limit": limit},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        pid = r["_id"]
        product = await db.products.find_one({"id": pid}, {"_id": 0, "created_at": 1, "name": 1, "sale_price": 1, "images": 1, "stock": 1})
        product_age_days = None
        if product and product.get("created_at"):
            try:
                c = datetime.fromisoformat(product["created_at"])
                product_age_days = (now - c).days
            except Exception:
                pass
        age = product_age_days if product_age_days is not None else 999
        recommend_ads = age <= 60 and r["sold"] >= min_sold
        out.append({
            "product_id": pid,
            "product_name": (product or {}).get("name") or r.get("product_name") or "—",
            "sold_in_window": r["sold"],
            "revenue": round(r["revenue"], 2),
            "product_age_days": product_age_days,
            "window_days": window_days,
            "stock": (product or {}).get("stock"),
            "image": ((product or {}).get("images") or [None])[0] if (product or {}).get("images") else None,
            "recommend_ads": recommend_ads,
        })
    return {"items": out, "window_days": window_days, "min_sold": min_sold}


# =============================================================================
# Üretici performans (FAZ 7 potansiyel iyileştirme)
# =============================================================================

@router.get("/manufacturer-performance")
async def manufacturer_performance(current_user: dict = Depends(require_admin)):
    """Üretici bazında ortalama gecikme, sipariş adedi ve +/-% fark.
    Skor: 100 başlangıç, her gün gecikme -3, |qty_diff| -0.5.
    """
    pipeline = [
        {"$match": {"manufacturer_id": {"$ne": None, "$exists": True}}},
        {"$group": {
            "_id": "$manufacturer_id",
            "name": {"$first": "$manufacturer_name"},
            "rows": {"$sum": 1},
            "avg_delay": {"$avg": "$delay_days"},
            "max_delay": {"$max": "$delay_days"},
            "avg_qty_diff": {"$avg": "$qty_diff_pct"},
            "delivered_count": {"$sum": {"$cond": [{"$ne": ["$delivered_qty", 0]}, 1, 0]}},
        }},
        {"$sort": {"avg_delay": 1}},
    ]
    out = []
    async for r in db.production_plan.aggregate(pipeline):
        avg_delay = r.get("avg_delay")
        avg_qty = r.get("avg_qty_diff")
        score = 100
        if avg_delay is not None:
            score -= max(0, avg_delay) * 3
        if avg_qty is not None:
            score -= abs(avg_qty) * 0.5
        score = max(0, round(score, 1))
        out.append({
            "manufacturer_id": r["_id"],
            "name": r.get("name") or "—",
            "rows": r["rows"],
            "delivered": r.get("delivered_count", 0),
            "avg_delay_days": round(avg_delay, 1) if avg_delay is not None else None,
            "max_delay_days": r.get("max_delay"),
            "avg_qty_diff_pct": round(avg_qty, 1) if avg_qty is not None else None,
            "score": score,
        })
    out.sort(key=lambda x: -x["score"])
    return {"items": out}


# ============================================================================
# EK RAPORLAR — İl/İlçe, Kaynak (Instagram/Google/Pazaryeri), Uzun süredir satılmayan
# ============================================================================

@router.get("/by-location")
async def sales_by_location(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group: str = Query("city", regex="^(city|district)$"),
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(require_admin),
):
    """Satışları İL (city) veya İLÇE (district) bazında gruplar. Tarih + kaynak filtresi."""
    s, e = _iso_range(start_date, end_date)
    field = "$shipping_address.city" if group == "city" else "$shipping_address.district"
    gid = {"loc": field}
    if group == "district":
        gid["city"] = "$shipping_address.city"
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$group": {
            "_id": gid,
            "orders": {"$sum": 1},
            "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
            # ADET = kalem adetleri toplamı. Eskiden $size (KALEM SAYISI) idi: aynı üründen
                # 3 adet alan sipariş 1 sayılıyordu → pazaryeri raporlarıyla mutabakatta
                # sistematik eksik. Trendyol "Brüt Satış Adedi" ile aynı birim.
                "items": {"$sum": {"$reduce": {
                    "input": {"$ifNull": ["$items", []]}, "initialValue": 0,
                    "in": {"$add": ["$$value", {"$max": [1, {"$ifNull": ["$$this.quantity", 1]}]}]}}}},
        }},
        {"$sort": {"revenue": -1}},
        {"$limit": limit},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        loc = (r["_id"].get("loc") or "").strip() or "Bilinmiyor"
        row = {"location": loc, "orders": r["orders"], "revenue": round(r["revenue"], 2), "items": r["items"]}
        if group == "district":
            row["city"] = (r["_id"].get("city") or "").strip()
        rows.append(row)
    return {
        "group": group,
        "rows": rows,
        "totals": {
            "orders": sum(x["orders"] for x in rows),
            "revenue": round(sum(x["revenue"] for x in rows), 2),
        },
    }


# Kaynak kısaltmaları → tek kanal. ig=instagram, fb=meta, gads=google… (aynı kanal ayrı satır
# olmasın diye). TAM değer eşleşmesiyle uygulanır (substring değil — "ig" pek çok kelimede geçer).
_CHANNEL_ALIASES = {
    "ig": "instagram", "insta": "instagram", "instagram": "instagram", "instagramshop": "instagram",
    "ig_shopping": "instagram", "instagram_shop": "instagram", "instagram-feed": "instagram", "igshopping": "instagram",
    "fb": "meta", "facebook": "meta", "meta": "meta", "fb_ig": "meta",
    "gl": "google", "google": "google", "gads": "google", "adwords": "google", "googleads": "google",
    "google_ads": "google", "google-ads": "google", "cpc": "google",
    "tt": "tiktok", "tiktok": "tiktok",
    "yt": "youtube", "youtube": "youtube",
    "pin": "pinterest", "pinterest": "pinterest",
    "eposta": "email", "e-posta": "email", "email": "email", "mail": "email", "sms": "sms",
    "referral": "referral", "direct": "direct", "organic": "organic",
}


def _channel_label(idv: dict) -> str:
    """Bir siparişin satış kanalını tek etikete indirger: pazaryeri > sosyal kaynak > direct.
    'ig'/'insta' gibi kısaltmalar 'instagram'a, 'fb' 'meta'ya vb. normalize edilir → aynı kanal
    tek satırda toplanır."""
    pf = (idv.get("platform") or "").strip().lower()
    mk = (idv.get("marketplace") or "").strip().lower()
    for m in _MARKETPLACES:
        if pf == m or mk == m:
            return m
    src = (idv.get("src") or "").strip().lower()
    ch = (idv.get("channel") or "").strip().lower()
    # 1) TAM değer alias'ı (ig=instagram gibi kısaltmalar) — hem src hem channel denenir.
    for v in (src, ch):
        if v in _CHANNEL_ALIASES:
            return _CHANNEL_ALIASES[v]
    # 2) İçerik taraması: tam kanal adı metnin içinde geçiyorsa (utm_source=instagram_stories vb.)
    blob = f"{src} {ch}"
    for k in ("instagram", "google", "facebook", "tiktok", "meta", "youtube", "pinterest", "email", "sms"):
        if k in blob:
            return "meta" if k in ("facebook", "meta") else k
    if src or ch:
        return src or ch
    return "direct"


@router.get("/by-source")
async def sales_by_source(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Satışları KANAL bazında gruplar: pazaryerleri (Trendyol/HB/Temu) + site trafiği kaynağı
    (Instagram/Google/Meta/direct) — attribution.source/channel'dan türetilir."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$group": {
            "_id": {
                "platform": {"$ifNull": ["$platform", ""]},
                "marketplace": {"$ifNull": ["$marketplace", ""]},
                "src": {"$ifNull": ["$attribution.source", ""]},
                "channel": {"$ifNull": ["$attribution.channel", ""]},
            },
            "orders": {"$sum": 1},
            "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
        }},
    ]
    agg: dict = {}
    async for r in db.orders.aggregate(pipeline):
        label = _channel_label(r["_id"])
        a = agg.setdefault(label, {"orders": 0, "revenue": 0.0})
        a["orders"] += r["orders"]
        a["revenue"] += r["revenue"] or 0
    rows = [{"channel": k, "orders": v["orders"], "revenue": round(v["revenue"], 2)} for k, v in agg.items()]
    rows.sort(key=lambda x: -x["revenue"])
    return {
        "rows": rows,
        "totals": {
            "orders": sum(x["orders"] for x in rows),
            "revenue": round(sum(x["revenue"] for x in rows), 2),
        },
    }


@router.get("/never-sold")
async def never_sold(
    days: int = Query(90, ge=1, le=3650),
    limit: int = Query(500, ge=1, le=5000),
    current_user: dict = Depends(require_admin),
):
    """Son N günde HİÇ satılmayan aktif ürünler (uzun süredir satış yok). Stok değerine göre sıralı."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sold = set()
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}, "status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": {"$ifNull": ["$items.product_id", "$items.id"]}}},
    ]
    async for r in db.orders.aggregate(pipeline):
        if r.get("_id"):
            sold.add(str(r["_id"]))
    rows = []
    async for p in db.products.find(
        {"is_active": True, "is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "stock": 1, "price": 1, "sale_price": 1,
         "created_at": 1, "stock_code": 1, "images": 1, "variants": 1},
    ):
        if str(p.get("id")) in sold:
            continue
        variants = p.get("variants") or []
        # Stok: varyant varsa varyant stoklarının TOPLAMI (top-level 'stock' varyantlıda 0 olabilir).
        v_stock = sum(int(v.get("stock") or 0) for v in variants)
        stock = v_stock if variants else int(p.get("stock") or 0)
        if not variants and stock == 0:
            stock = int(p.get("stock") or 0)
        price = float(p.get("sale_price") or p.get("price") or 0)
        # Bedenler: stok bilgisiyle birlikte (S:3, M:0, L:5 gibi)
        sizes = []
        for v in variants:
            sz = (v.get("size") or "").strip()
            if sz:
                sizes.append(f"{sz}:{int(v.get('stock') or 0)}")
        img = ""
        try:
            im0 = (p.get("images") or [None])[0]
            img = im0.get("url") if isinstance(im0, dict) else (im0 or "")
        except Exception:
            img = ""
        rows.append({
            "product_id": p.get("id"),
            "name": p.get("name") or "(isimsiz ürün)",
            "stock_code": p.get("stock_code") or "",
            "stock": stock,
            "sizes": ", ".join(sizes) if sizes else "—",
            "variant_count": len(variants),
            "price": price,
            "stock_value": round(stock * price, 2),
            "created_at": p.get("created_at") or "",
            "image": img,
        })
    rows.sort(key=lambda x: -x["stock_value"])
    total_value = round(sum(x["stock_value"] for x in rows), 2)
    return {"days": days, "count": len(rows), "total_stock_value": total_value, "items": rows[:limit]}


# ============================================================================
# EK RAPORLAR 2 — Saatlik, Ödeme Tipi, Kupon Performansı, Yeni/Tekrar Eden Müşteri
# ============================================================================

@router.get("/by-hour")
async def sales_by_hour(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    source: Optional[str] = None, current_user: dict = Depends(require_admin),
):
    """Günün SAATLERİNE göre satış dağılımı (TR saati, +03:00). En yoğun saatler."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$group": {
            "_id": {"$dateToString": {"format": "%H", "date": {"$dateFromString": {"dateString": "$created_at"}}, "timezone": "+03:00"}},
            "orders": {"$sum": 1},
            "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    by = {f"{h:02d}": {"orders": 0, "revenue": 0.0} for h in range(24)}
    async for r in db.orders.aggregate(pipeline):
        by[r["_id"]] = {"orders": r["orders"], "revenue": round(r["revenue"], 2)}
    rows = [{"hour": h, "orders": v["orders"], "revenue": v["revenue"]} for h, v in sorted(by.items())]
    return {"rows": rows, "totals": {"orders": sum(x["orders"] for x in rows), "revenue": round(sum(x["revenue"] for x in rows), 2)}}


_PM_LABELS = {
    "transfer": "Havale/EFT", "havale": "Havale/EFT", "bank_transfer": "Havale/EFT",
    "eft": "Havale/EFT", "havale_eft": "Havale/EFT", "banka_havale": "Havale/EFT",
    "card": "Kredi/Banka Kartı", "credit_card": "Kredi/Banka Kartı", "iyzico": "Kredi/Banka Kartı",
    "iyzipay": "Kredi/Banka Kartı", "cod": "Kapıda Ödeme", "kapida": "Kapıda Ödeme",
}


@router.get("/by-payment")
async def sales_by_payment(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    source: Optional[str] = None, current_user: dict = Depends(require_admin),
):
    """Ödeme tipine göre satış (Havale / Kart / Kapıda vb.)."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": _base_match(s, e, source)},
        {"$group": {"_id": {"$ifNull": ["$payment_method", ""]}, "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
    ]
    agg = {}
    async for r in db.orders.aggregate(pipeline):
        label = _PM_LABELS.get(str(r["_id"]).strip().lower(), (str(r["_id"]).strip() or "Belirtilmemiş"))
        a = agg.setdefault(label, {"orders": 0, "revenue": 0.0})
        a["orders"] += r["orders"]; a["revenue"] += r["revenue"] or 0
    rows = [{"method": k, "orders": v["orders"], "revenue": round(v["revenue"], 2)} for k, v in agg.items()]
    rows.sort(key=lambda x: -x["revenue"])
    return {"rows": rows, "totals": {"orders": sum(x["orders"] for x in rows), "revenue": round(sum(x["revenue"] for x in rows), 2)}}


@router.get("/coupon-performance")
async def coupon_performance(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Kupon performansı: her kupon kaç siparişte kullanıldı, ne kadar indirim + ciro getirdi."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$nin": _EXCLUDED_STATUSES},
                    "coupon_code": {"$nin": [None, ""]}}},
        {"$group": {
            "_id": "$coupon_code",
            "orders": {"$sum": 1},
            "revenue": {"$sum": {"$ifNull": ["$total", 0]}},
            "discount": {"$sum": {"$ifNull": ["$discount", {"$ifNull": ["$discount_amount", 0]}]}},
        }},
        {"$sort": {"orders": -1}},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"coupon": r["_id"], "orders": r["orders"], "revenue": round(r["revenue"], 2), "discount": round(r.get("discount") or 0, 2)})
    return {"rows": rows, "totals": {
        "orders": sum(x["orders"] for x in rows),
        "revenue": round(sum(x["revenue"] for x in rows), 2),
        "discount": round(sum(x["discount"] for x in rows), 2),
    }}


@router.get("/customer-type")
async def customer_type(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    """Bu aralıkta sipariş veren müşteriler: YENİ (ilk siparişi bu aralıkta) vs TEKRAR EDEN
    (daha önce de sipariş vermiş). Müşteri anahtarı: e-posta."""
    s, e = _iso_range(start_date, end_date)
    key = {"$toLower": {"$ifNull": ["$email", {"$ifNull": ["$shipping_address.email", "$user_id"]}]}}
    # Denetim: firstOrder (ilk sipariş tarihi) TÜM siparişlerden hesaplanmalı — iptal/iade dahil.
    # Aksi halde gerçek ilk siparişi iptal olan müşteri yanlışlıkla "YENİ" sayılıyordu. Ciro/adet
    # ise yalnız GEÇERLİ (dışlanmamış) siparişlerden sayılır.
    _in_range = {"$and": [{"$gte": ["$created_at", s]}, {"$lte": ["$created_at", e]}]}
    _valid = {"$not": [{"$in": ["$status", _EXCLUDED_STATUSES]}]}
    _valid_in_range = {"$and": [_in_range, _valid]}
    pipeline = [
        {"$group": {
            "_id": key,
            "firstOrder": {"$min": "$created_at"},
            "ordersInRange": {"$sum": {"$cond": [_valid_in_range, 1, 0]}},
            "revInRange": {"$sum": {"$cond": [_valid_in_range, {"$ifNull": ["$total", 0]}, 0]}},
        }},
        {"$match": {"ordersInRange": {"$gt": 0}}},
    ]
    new_c = ret_c = 0
    new_rev = ret_rev = 0.0
    new_ord = ret_ord = 0
    async for r in db.orders.aggregate(pipeline):
        is_new = (r.get("firstOrder") or "") >= s
        if is_new:
            new_c += 1; new_rev += r.get("revInRange") or 0; new_ord += r.get("ordersInRange") or 0
        else:
            ret_c += 1; ret_rev += r.get("revInRange") or 0; ret_ord += r.get("ordersInRange") or 0
    total_c = new_c + ret_c
    return {
        "new": {"customers": new_c, "orders": new_ord, "revenue": round(new_rev, 2)},
        "returning": {"customers": ret_c, "orders": ret_ord, "revenue": round(ret_rev, 2)},
        "repeat_rate": round((ret_c / total_c) * 100, 1) if total_c else 0,
    }
