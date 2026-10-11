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

# logger: reports.py bunu İTHAL ETMEDEN kullanıyordu. Üç kullanım da except
# bloğundaydı, bu yüzden hata yıllarca gizli kaldı — asıl istisnayı NameError ile
# maskeliyordu. Dördüncü kullanım (mükerrer pusula bilgisi) normal akışta olduğu
# için panel özetini anında kırdı ve eksik ithal görünür oldu.
from .deps import db, require_admin, tr_range_to_utc, logger, require_permission
from .report_dedup import (
    dup_nor, merge_match, load_dup_dep, canonical_order_stages,
    load_dup_order_numbers, dup_filter_state,
    effective_order_date_match, split_confirmed_return, accepted_claim_items,
    claim_items_with_status, product_quantity_metrics, kept_gross_revenue,
    reconciled_platform_breakdown,
    payment_report_group_key, partial_cancel_net_values, product_platform_metrics,
    OPEN_MARKETPLACE_CLAIM_STATUSES, marketplace_claim_status_bucket,
    dedupe_return_records, dedupe_return_items,
)


# IAM-01: require_admin, önbellek ısıtan load_dup_dep'ten ÖNCE çalışır — yetkisiz istek uç koduna (DB
# taramasına) hiç dokunmaz. FastAPI bağımlılık önbelleği sayesinde uçtaki require_admin tekrar çalışmaz.
router = APIRouter(prefix="/admin/reports", tags=["admin-reports"],
                   dependencies=[Depends(require_admin), Depends(load_dup_dep)])


def _iso_range(start: Optional[str], end: Optional[str], days_default: int = 30):
    # Seçilen tarihler TÜRKİYE yerel günü kabul edilir (00:00–23:59:59.999 +03:00) ve UTC ISO
    # sınırına çevrilir → bitiş günü tam dahil olur, saat-dilimi kayması OLMAZ. (deps.tr_range_to_utc)
    if bool(start) != bool(end):
        raise HTTPException(status_code=400, detail="start_date ve end_date birlikte verilmelidir")
    if start and end:
        # Ters aralık (başlangıç > bitiş) eskiden 200 + sessizce SIFIR veri dönüyordu;
        # ekran "Filtre uygulandı" deyip boş rapor gösteriyordu. Açık hata daha dürüst.
        if str(start).strip()[:10] > str(end).strip()[:10]:
            raise HTTPException(status_code=400,
                                detail="Başlangıç tarihi bitiş tarihinden sonra olamaz")
        return tr_range_to_utc(start, end)
    now_tr = datetime.now(timezone.utc) + timedelta(hours=3)
    end_day = now_tr.date()
    start_day = end_day - timedelta(days=max(1, days_default) - 1)
    return tr_range_to_utc(start_day.isoformat(), end_day.isoformat())


# Pazaryeri kaynakları — Site dışı her şey. (platform VEYA marketplace alanında durabilir;
# örn. Temu siparişleri yalnız marketplace="temu" taşır, platform boş olabilir.)
_MARKETPLACES = ["trendyol", "hepsiburada", "temu", "n11", "amazon"]

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

# ÖDENMEMİŞ grubu (kritik-para): 3DS/havale tamamlanmamış, başarısız kart, bekleyen,
# havale bildirimi onaysız. DENETİM K1: sales_summary brüt ciroda BİLE sayılmamalı —
# aksi halde headline ciro/sipariş/adet ödenmemiş siparişlerle şişiyordu.
_UNPAID_STATUSES = ["awaiting_payment", "payment_failed", "pending", "payment_notified"]

# HİÇ ÖDENMEMİŞ SİTE SİPARİŞİ İPTALİ satış DEĞİLDİR. Havale süpürmesi / elle iptal,
# ödenmemiş site siparişini status="cancelled" yapıyor (payment_status pending/expired
# kalıyor); statü süzgeci onu "iptal edilmiş satış" sanıp brüte ve iptal kartlarına
# katıyordu. Ölçüt "alan yoksa" DEĞİL, "AÇIKÇA ödenmemiş": payment_status alanı olmayan
# eski Ticimax / pazaryeri kayıtları etkilenmez; kapıda ödeme (parası teslimde alınır)
# ve paid_at / yalnız-hediye-çeki ödemeli siparişler de hariç tutulmaz.
_EXPLICIT_UNPAID_PAYMENT = ["pending", "expired", "failed", "awaiting_payment", "unpaid"]
_COD_METHODS = ["cash_on_delivery", "kapida", "kapida_odeme", "cod"]


def _unpaid_site_cancel_nor() -> dict:
    """Mongo: ödenmemiş site iptallerini DIŞLAYAN koşul (bir $and dalı olarak kullanın —
    merge_match üst düzey $nor'u kendi kopya süzgeciyle ezer)."""
    return {"$nor": [{
        "status": {"$in": ["cancelled", "cancel_refunded"]},
        "platform": {"$nin": _MARKETPLACES},
        "marketplace": {"$nin": _MARKETPLACES},
        "payment_status": {"$in": _EXPLICIT_UNPAID_PAYMENT},
        "payment_method": {"$nin": _COD_METHODS},
        "paid_at": {"$in": [None, ""]},
        "paid_with_gift_card_only": {"$ne": True},
    }]}


def _is_unpaid_site_cancel(o: dict) -> bool:
    """_unpaid_site_cancel_nor() ile AYNI kural — Python tarafı (bellek içi süzgeç)."""
    o = o or {}
    if str(o.get("status") or "") not in ("cancelled", "cancel_refunded"):
        return False
    if _channel_of(o) != "site":
        return False
    if str(o.get("payment_method") or "").strip().lower() in _COD_METHODS:
        return False
    if o.get("paid_at") or o.get("paid_with_gift_card_only") is True:
        return False
    return str(o.get("payment_status") or "").strip().lower() in _EXPLICIT_UNPAID_PAYMENT


# Kaynak takma adları TEK yerde normalize edilir. Eskiden _source_cond "ty/hb/amz"
# takma adlarını kabul ediyor ama aksiyon tarafı (_src_allows) kanal anahtarını düz
# karşılaştırıyordu → takma adla sorguda TÜM iptal/iadeler düşüyor, net = brüt oluyordu.
_ALL_SOURCE_ALIASES = ("", "all", "toplu", "hepsi", "tum", "tumu", "tümü")
_SITE_SOURCE_ALIASES = ("site", "meluni", "web", "kendi", "storefront")
_SOURCE_ALIASES = {"ty": "trendyol", "hb": "hepsiburada", "amz": "amazon"}


def _canon_source(source: Optional[str]) -> str:
    """'all' | 'site' | pazaryeri adı | (bilinmeyen değer olduğu gibi)."""
    s = (source or "all").strip().lower()
    if s in _ALL_SOURCE_ALIASES:
        return "all"
    if s in _SITE_SOURCE_ALIASES:
        return "site"
    return _SOURCE_ALIASES.get(s, s)


def _source_cond(source: Optional[str]) -> dict:
    """Rapor kaynak filtresi → Mongo koşulu.
    'all'/boş = toplu (filtre yok). 'site' = pazaryeri olmayan tüm siparişler.
    'trendyol'/'hepsiburada'/'temu' = ilgili pazaryeri (platform VEYA marketplace).
    Takma adlar (_canon_source) aksiyon tarafıyla (_src_allows) AYNI normalize edilir.
    """
    s = _canon_source(source)
    if s == "all":
        return {}
    if s == "site":
        # Site = pazaryeri olmayan: platform da marketplace da pazaryeri listesinde DEĞİL
        # (alan hiç yoksa $nin yine eşleşir → boş platform site sayılır).
        return {"platform": {"$nin": _MARKETPLACES}, "marketplace": {"$nin": _MARKETPLACES}}
    if s in _MARKETPLACES:
        return {"$or": [{"platform": s}, {"marketplace": s}]}
    # Yazım hatası/eskimiş istemci değeri hiçbir zaman sessizce "tüm satışlar"a
    # genişlemesin; finansal raporda bu davranış veri sızıntısı ve yanlış toplamdır.
    raise HTTPException(status_code=400, detail=f"Geçersiz rapor kaynağı: {source}")


def _channel_expr() -> dict:
    """Bilinen pazaryerini platform/marketplace alanlarından öncelikli seç."""
    platform = {"$toLower": {"$ifNull": ["$platform", ""]}}
    marketplace = {"$toLower": {"$ifNull": ["$marketplace", ""]}}
    return {"$cond": [
        {"$in": [platform, _MARKETPLACES]}, platform,
        {"$cond": [{"$in": [marketplace, _MARKETPLACES]}, marketplace,
                    {"$cond": [{"$in": [platform, ["", "meluni", "web"]]}, "site", platform]}]},
    ]}


def _norm_size(s) -> str:
    """Beden anahtarını TEK-BİÇİM yapar → aynı beden farklı ayraçla (XS-S / XS/S / 'XS S')
    RAPORDA AYRI SATIR ÇIKMASIN. Büyük harf; '/ \\ _ - boşluk' ayraçları tek '/' olur.
    Örn: 'xs-s'→'XS/S', 'M L'→'M/L', '38'→'38'. Boş → '—'.

    Eşanlamlı beden adları da TEK ada iner: aynı beden pazaryeri kaleminde 'STANDART' /
    'TEK EBAT' / '2XS', ürün kartı varyantında 'STD' / 'XXS' yazılıyordu → satış satırı
    ile stok satırı ayrı çıkıyor, satış satırında Kalan Stok '—' görünüyordu.
    (Genel giyim beden sözlüğü — firmaya özel değer değildir.)"""
    import re as _re_sz
    v = str(s or "").strip().upper()
    if not v:
        return "—"
    v = _re_sz.sub(r"[\\/_\-\s]+", "/", v).strip("/")
    if not v:
        return "—"
    return _SIZE_ALIASES.get(v, v)


# _norm_size ayraç birleştirmesinden SONRA uygulanan eşanlamlılar (anahtarlar '/' ayraçlı).
_SIZE_ALIASES = {
    "STANDART": "STD", "STANDARD": "STD", "STD": "STD",
    "TEK/EBAT": "STD", "TEKEBAT": "STD", "TEK/BEDEN": "STD", "TEKBEDEN": "STD",
    "ONE/SIZE": "STD", "ONESIZE": "STD",
    "2XS": "XXS", "3XS": "XXXS", "2XL": "XXL", "3XL": "XXXL",
}


def _norm_size(s) -> str:
    """Beden anahtarını TEK-BİÇİM yapar → aynı beden farklı ayraçla (XS-S / XS/S / 'XS S')
    RAPORDA AYRI SATIR ÇIKMASIN. Büyük harf; '/ \\ _ - boşluk' ayraçları tek '/' olur.
    Örn: 'xs-s'→'XS/S', 'M L'→'M/L', '38'→'38'. Boş → '—'."""
    import re as _re_sz
    v = str(s or "").strip().upper()
    if not v:
        return "—"
    v = _re_sz.sub(r"[\\/_\-\s]+", "/", v).strip("/")
    return v or "—"


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


# DÖNEM MUHASEBESİ temeli (kullanıcı kararı): satış SATILDIĞI aya yazılır; iptal ve
# iade kendi gerçekleştiği aya AYRI yazılır. Dolayısıyla satış nüfusundan yalnız
# gerçekten ödenmemiş siparişler elenir — sonradan iade edilen sipariş, satıldığı ayın
# satışı olmaya devam eder. (Eski temel iade edilen siparişi satış ayından tamamen
# siliyordu: yalnız temmuz Trendyol'da 1.607 → 1.111 adet / 2.940.419 → 2.038.065 TL.)
_DONEM_EXCLUDED = list(_UNPAID_STATUSES)
# Kargo hacmi istisnası: iptal edilen sipariş KARGOLANMAZ, bu yüzden kargo raporunda
# iptaller de elenir. İade edilen sipariş kargolandığı için kargo raporunda KALIR.
_KARGO_EXCLUDED = list(_UNPAID_STATUSES) + ["cancelled", "cancel_refunded"]


def _fnum(v, default: float = 0.0) -> float:
    """Sayıya çevir; NaN/Infinity ise `default`.

    Canlı veride tutarı NaN olan sipariş bulundu (haziran). Python'da NaN "truthy"
    olduğu için `float(v or 0)` deyimi onu OLDUĞU GİBİ geçiriyor ve tek bozuk kayıt
    toplandığı HER ŞEYİ NaN yapıyordu: haziranın iptal toplamı, net'i, kohort net'i
    ve ortalama sepeti çöküyor, yanıt JSON'a yazılamadığı için istek 500 veriyordu.
    Rapor tek bir bozuk kayıt yüzünden ayın tamamını kaybetmemeli.
    """
    import math as _m
    try:
        x = float(v or 0)
    except Exception:
        return default
    return x if _m.isfinite(x) else default


def _sales_stages(s: str, e: str, source: Optional[str] = None,
                  exclude: Optional[list] = None) -> list:
    """Canonical report population in the only safe operation order.

    The latest terminal copy must win *before* valid-sale statuses are filtered;
    otherwise an older paid copy of a later-cancelled marketplace order survives.

    ``exclude`` verilmezse eski (kohort-net) davranış korunur: iptal + iade + ödenmemiş
    elenir. Dönem muhasebesi uygulayan ekranlar ``_DONEM_EXCLUDED`` geçirir.
    """
    clauses = [effective_order_date_match(s, e)]
    sc = _source_cond(source)
    if sc:
        clauses.append(sc)
    return [
        {"$match": merge_match({"$and": clauses})},
        *canonical_order_stages(),
        {"$match": {"$and": [
            {"status": {"$nin": list(
                _EXCLUDED_STATUSES if exclude is None else exclude)}},
            # Ödenmemiş site iptali satış değildir (dönem temelinde iptal DAHİL
            # ekranlarda da girmesin — kartlarla aynı kural).
            _unpaid_site_cancel_nor(),
        ]}},
    ]


def _effective_date_expr() -> dict:
    """Pazaryeri sipariş tarihi; YOK/boş/null ise created_at'e düşer.

    KRİTİK HATA DÜZELTMESİ: Önceki hâli
        {"$cond": [{"$in": ["$marketplace_order_date", [None, ""]]}, "$created_at",
                   "$marketplace_order_date"]}
    idi. MongoDB aggregation'ında EKSİK alan (field hiç yok) ile null AYNI ŞEY DEĞİLDİR:
    $in eksik değeri [None, ""] içinde saymaz → koşul false → eksik alanın kendisi
    döner ve hesaplanan tarih NULL olur.

    Site (meluni) ve Hepsiburada siparişlerinde marketplace_order_date alanı HİÇ
    BULUNMADIĞI için etkin tarihleri null oluyordu; bu yüzden bu siparişler HİÇBİR
    tarih aralığına girmiyor ve satış özetinden tamamen düşüyorlardı (yalnız Eylül
    2026'da 466 sipariş / ~997.356 TL). Trendyol'da alan dolu olduğu için yalnız
    Trendyol cirosu görünüyordu.

    $ifNull eksik alanı da yakalar; sorgu dilindeki effective_order_date_match()
    zaten doğru davranıyordu (query-language'de null eksik alanı da eşler) — bu
    yüzden iki yol birbirinden farklı sonuç veriyordu.
    """
    return {"$cond": [
        {"$in": [{"$ifNull": ["$marketplace_order_date", ""]}, [None, ""]]},
        "$created_at",
        "$marketplace_order_date",
    ]}


def _order_datetime_tr(order: dict) -> Optional[datetime]:
    """Canonical order timestamp converted to Turkey local time for Python grouping."""
    raw = order.get("marketplace_order_date") or order.get("created_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone(timedelta(hours=3)))
    except Exception:
        return None


@router.get("/sales-summary")
async def sales_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.sales")),
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

    # ── DÖNEM MUHASEBESİ ÖN-ÇEKİMİ (bir kez, tüm aralıklar için) ───────────────
    # Kullanıcı kararı: iptal İPTALİN kesinleştiği güne, iade GİDER PUSULASININ
    # kesildiği güne ve PUSULADAKİ tutar kadar yazılır. Bu blok bugün/dün/hafta/ay/
    # yıl + seçili aralık + önceki dönem için TEK sorgu kümesi çalıştırır; dilimleme
    # bellekte (_slice_actions) yapılır.
    _rng = {}
    _rng["today"] = (_u(day0), _u(now_tr))
    _rng["yesterday"] = (_u(day0 - timedelta(days=1)), _u(day0))
    _rng["week"] = (_u(day0 - timedelta(days=day0.weekday())), _u(now_tr))
    _rng["month"] = (_u(day0.replace(day=1)), _u(now_tr))
    _rng["year"] = (_u(day0.replace(month=1, day=1)), _u(now_tr))
    if start_date and end_date:
        try:
            _s2, _e2 = _iso_range(start_date, end_date)
            _sd = datetime.fromisoformat(str(_s2).replace("Z", "+00:00"))
            _ed = datetime.fromisoformat(str(_e2).replace("Z", "+00:00"))
            _rng["period"] = (_s2, _e2)
            _rng["period_prev"] = ((_sd - (_ed - _sd)).isoformat(), _s2)
        except Exception:
            pass
    _lo = min(v[0] for v in _rng.values())
    _hi = max(v[1] for v in _rng.values())
    _acts = await _action_rows(_lo, _hi)

    async def _agg(s_iso: str, e_iso: str) -> dict:
        _sc = _source_cond(source)
        # ── 1) CİRO (brüt) + net sipariş/adet: SİPARİŞ tarihine göre (satışın gerçekleştiği ay).
        # DENETİM K1: revenue_all iptal+iade DAHİL brüt kalır AMA ödenmemiş grubu (awaiting_payment/
        # payment_failed/pending/payment_notified) elenir — aksi halde headline rakamlar şişiyordu.
        # Efektif satış tarihi: pazaryeri siparişleri Trendyol'un orderDate'ine göre sayılır
        # (created_at = senkron zamanı, sapma yaratıyordu). Site siparişi created_at kalır.
        _m = {"_eff_date": {"$gte": s_iso, "$lt": e_iso}, "status": {"$nin": _UNPAID_STATUSES}}
        if _sc:
            _m.update(_sc)
        # Hiç ödenmemiş site iptali brüte girmez ($and dalı: merge_match $nor'u ezmesin).
        _m = merge_match({"$and": [_m, _unpaid_site_cancel_nor()]})  # + ticimax ÇİFT kayıtları hariç
        pipe = [
            {"$addFields": {
                "_eff_date": _effective_date_expr()}},
            {"$match": _m},
            *canonical_order_stages(),
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

        # ── 2-3) İPTAL ve İADE: İŞLEMİN KESİNLEŞTİĞİ döneme yazılır.
        #   İptal → cancelled_at (yoksa updated_at)
        #   İade  → GİDER PUSULASI tarihi, tutar = pusuladaki net (muhasebe evrakı)
        # Tüm raporlar (kartlar, kanal tablosu, kanal/ödeme kırılımı) aynı kaynağı
        # kullanır — aksi halde aynı ekranda iki farklı "İade" rakamı çıkıyordu.
        _act = _slice_actions(_acts, s_iso, e_iso, source, end_exclusive=True)
        _ct = _sum_actions(_act["cancels"])
        _rt = _sum_actions(_act["returns"])
        cancel_total, _cn = _ct["revenue"], _ct["orders"]
        return_total, _ret_cnt = _rt["revenue"], _rt["orders"]

        # NET = o ayki ciro − o ay İPTAL edilen − o ay İADE onaylanan (eylem tarihine göre).
        net_revenue = revenue_all - cancel_total - return_total

        # ── KOHORT NET: "BU AYIN SATIŞININ net'i" ────────────────────────────
        # Dönem net'i bu ay kesinleşen TÜM iptal/iadeleri düşer — ÖNCEKİ ayların
        # siparişlerinden gelenler dahil. Eylül 2026'da bu ayki 974.534 TL iadenin
        # 186 belgesi temmuz/ağustos siparişlerine aitti; o tutar eylülün satışıyla
        # ilgisiz olduğu hâlde eylülün net'ini düşürüyordu ve rakam "hatalı" görünüyordu.
        # Pazaryeri panelleri (Trendyol vb.) iadeyi siparişin SATILDIĞI aydan düşer.
        # İkisi farklı soruların cevabı; kıyas yapılabilsin diye ikisi de döner.
        # Siparişi bulunamayan belge ihtiyatlı biçimde "önceki dönem" sayılır.
        # Köprü (onceki_donem_*) DÖNEM penceresinden: bu dönemde kesinleşen ama önceki
        # ayların siparişine ait olan kısım.
        _coh = _slice_actions(_acts, s_iso, e_iso, source,
                              keyfn=lambda r: _cohort_key(r, s_iso, e_iso, True),
                              end_exclusive=True)
        _ret_onceki = round(float((_coh["returns"].get("onceki") or {}).get("revenue") or 0), 2)
        _can_onceki = round(float((_coh["cancels"].get("onceki") or {}).get("revenue") or 0), 2)
        # KOHORT PENCERESİ = BUGÜNE KADAR: bu dönemin siparişlerinin, dönem bittikten
        # SONRA kesinleşen iptal/iadeleri de düşülür (Trendyol paneli gibi). _acts zaten
        # bugüne (_hi) kadar okunuyor; yalnız işlem-tarihi üst sınırı genişler.
        _coh_ext = _slice_actions(_acts, s_iso, e_iso, source,
                                  keyfn=lambda r: _cohort_key(r, s_iso, e_iso, True),
                                  end_exclusive=True, at_until=_hi)
        _can_bu = float((_coh_ext["cancels"].get("bu_donem") or {}).get("revenue") or 0)
        _ret_bu = float((_coh_ext["returns"].get("bu_donem") or {}).get("revenue") or 0)
        net_kohort = revenue_all - _can_bu - _ret_bu
        return {
            "revenue": round(revenue_all, 2),
            "net": round(net_revenue, 2),
            "net_kohort": round(net_kohort, 2),
            "onceki_donem_iadesi": _ret_onceki,
            "onceki_donem_iptali": _can_onceki,
            "cancels": round(cancel_total, 2),
            "returns": round(return_total, 2),
            "cancel_count": int(_cn),      # o ay İPTAL edilen sipariş SAYISI
            "return_count": int(_ret_cnt), # o ay İADE onaylanan sipariş SAYISI
            "orders": int(d.get("net_orders") or 0),
            "items": int(d.get("items_sold") or 0),
        }

    now_iso = _u(now_tr)
    today = await _agg(*_rng["today"])
    yesterday = await _agg(*_rng["yesterday"])
    week = await _agg(*_rng["week"])
    month = await _agg(*_rng["month"])
    year = await _agg(*_rng["year"])

    _cmp = None
    if yesterday["revenue"] > 0:
        _cmp = round((today["revenue"] - yesterday["revenue"]) / yesterday["revenue"] * 100, 1)

    # SEÇİLİ ARALIK özeti (sayfadaki tarih filtresi) + bir önceki eşit uzunluktaki
    # dönemle % karşılaştırma — "günlük seçtiysem günlük, aylık seçtiysem aylık".
    period = period_prev = None
    period_cmp = None
    if start_date and end_date:
        try:
            s2, e2 = _rng["period"]
            period = await _agg(s2, e2)
            period["aov"] = round(period["net"] / period["orders"], 2) if period["orders"] else 0
            period["items_per_order"] = round(period["items"] / period["orders"], 2) if period["orders"] else 0
            period_prev = await _agg(*_rng["period_prev"])
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
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Saat Analizi (00-24, TR saati): hangi saatlerde satış geliyor — sipariş + ciro.
    Reklam planlaması için zirve saat aralığı da döner."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    grouped = {}
    for order in orders:
        local = _order_datetime_tr(order)
        if local:
            grouped.setdefault(local.hour, []).append(order)
    by = {key: _bucket_orders(value, closed, open_)["included"] for key, value in grouped.items()}
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
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Gün Analizi: haftanın hangi günü daha çok satıyor (TR saati) — sipariş + ciro."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    _DAYS = {1: "Pazartesi", 2: "Salı", 3: "Çarşamba", 4: "Perşembe",
             5: "Cuma", 6: "Cumartesi", 7: "Pazar"}
    grouped = {}
    for order in orders:
        local = _order_datetime_tr(order)
        if local:
            grouped.setdefault(local.isoweekday(), []).append(order)
    by = {key: _bucket_orders(value, closed, open_)["included"] for key, value in grouped.items()}
    # Haftanın her günü aralıkta KAÇ KEZ geçiyor (TR günleri). Tam hafta katı olmayan
    # aralıkta 4 kez geçen gün, 3 kez geçenin önüne yapısal olarak geçiyordu → "En güçlü
    # gün" gün başına ORTALAMAYA göre seçilir; toplamlar (orders/revenue) aynen kalır.
    occ = {d: 0 for d in range(1, 8)}
    try:
        _d0 = (datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
               + timedelta(hours=3)).date()
        _d1 = (datetime.fromisoformat(str(e).replace("Z", "+00:00")).astimezone(timezone.utc)
               + timedelta(hours=3)).date()
        _n = (_d1 - _d0).days + 1
        for _i in range(max(0, min(_n, 3700))):
            occ[(_d0 + timedelta(days=_i)).isoweekday()] += 1
    except Exception:
        pass
    rows = []
    for d in range(1, 8):
        _o = int(by.get(d, {}).get("orders", 0))
        _r = round(float(by.get(d, {}).get("revenue", 0)), 2)
        rows.append({"day": d, "label": _DAYS[d], "orders": _o, "revenue": _r,
                     "occurrences": occ[d],
                     "avg_orders": round(_o / occ[d], 2) if occ[d] else 0,
                     "avg_revenue": round(_r / occ[d], 2) if occ[d] else 0})
    peak = (max(rows, key=lambda r: (r["avg_orders"], r["orders"]))
            if any(r["orders"] for r in rows) else None)
    return {"rows": rows, "peak": (peak["label"] if peak else None),
            "peak_basis": "gun_basina_ortalama"}


@router.get("/day-orders")
async def day_orders(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (tek TR günü — geriye uyumluluk)"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.sales")),
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
    pipeline = [
        *_sales_stages(s, e, source, exclude=_DONEM_EXCLUDED),
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
    _counts = await db.orders.aggregate([
        *_sales_stages(s, e, source, exclude=_DONEM_EXCLUDED),
        {"$count": "n"},
    ]).to_list(1)
    order_count = int(_counts[0]["n"]) if _counts else 0
    # DENETİM HATA-2: total_qty 300 satır LİMİTİNDEN SONRA toplanıyordu → uzun listelerde
    # sessizce eksik (30g'de 87 adet kayıp). Toplam artık limitsiz ayrı toplamadan gelir.
    _tq = 0
    async for r in db.orders.aggregate([
            *_sales_stages(s, e, source, exclude=_DONEM_EXCLUDED),
            {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
            {"$group": {"_id": None, "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}}}}]):
        _tq = int(r["qty"])
    return {"date": date, "order_count": order_count,
            "total_qty": _tq, "rows": rows}


@router.get("/sales")
async def sales(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    s, e = _iso_range(start_date, end_date)
    # Haftalık anahtar ISO YILI (%G) ile: '%Y-%V' Aralık sonunda W01'e düşen günlere
    # takvim yılını yazıyor, aynı ISO haftası iki kovaya bölünüp eksenin yanlış ucuna
    # sıralanıyordu. '%G-%V' → '2026-53' < '2027-01' (metin sıralaması da doğru).
    fmt = {"day": "%Y-%m-%d", "week": "%G-%V", "month": "%Y-%m"}[group_by]
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    grouped = {}
    for order in orders:
        local = _order_datetime_tr(order)
        if local:
            grouped.setdefault(local.strftime(fmt), []).append(order)
    # KOHORT (kartlarla TEK "net" kavramı): her sipariş KENDİ sipariş gününe yazılır; o
    # siparişin BUGÜNE KADAR kesinleşmiş iptal/iadesi de SİPARİŞİN gününden düşülür.
    # Önceki ayların siparişlerine ait iptal/iade bu grafiğe girmez (üyelik = bu
    # dönemin sipariş kümesi). Böylece Σ satır = sales-breakdown.net_kohort (ciro,
    # sipariş, adet) — Net kartıyla aynı. Kırpma (max 0) YOK: özdeşliği bozuyordu.
    _hi = await _cohort_until_rule(e)
    _members = _cohort_members(orders)
    coh = _slice_actions(await _action_rows(s, _hi), s, e, source,
                         keyfn=lambda r: _cohort_period_key(r, _members, fmt),
                         at_until=_hi)
    _z = {"revenue": 0.0, "orders": 0, "units": 0}
    rows = []
    for period in sorted(set(grouped) | set(coh["cancels"]) | set(coh["returns"])):
        inc = _bucket_orders(grouped.get(period, []), closed, open_)["included"]
        c = coh["cancels"].get(period, _z)
        r = coh["returns"].get(period, _z)
        rows.append({"period": period,
                     "orders": inc["orders"] - c["orders"] - r["orders"],
                     "revenue": round(inc["revenue"] - c["revenue"] - r["revenue"], 2),
                     "items": inc["units"] - c["units"] - r["units"],
                     "gross_revenue": inc["revenue"], "gross_orders": inc["orders"],
                     "cancel_total": c["revenue"], "return_total": r["revenue"],
                     "cancel_orders": c["orders"], "return_orders": r["orders"]})

    total_orders = sum(r["orders"] for r in rows)
    total_revenue = round(sum(r["revenue"] for r in rows), 2)
    aov = round(total_revenue / total_orders, 2) if total_orders else 0
    return {"rows": rows, "group_by": group_by, "basis": "kohort",
            "totals": {"orders": total_orders, "revenue": total_revenue, "aov": aov}}


_CANCEL_STATUSES = ["cancelled", "cancel_refunded"]
# İade grubu (return_rejected HARİÇ — satış geçerli sayılır, ciroda kalır)
_RETURN_OPEN_STATUSES_BD = ["return_requested", "return_in_transit"]
_RETURN_CLOSED_STATUSES_BD = ["return_approved", "returned", "refunded", "partial_refunded"]
_RETURN_STATUSES_BD = _RETURN_OPEN_STATUSES_BD + _RETURN_CLOSED_STATUSES_BD
# Pazaryeri iade talebi AÇIK (henüz onaylanmamış) sayılan claim durumları. Trendyol'un
# satış raporu bu adetleri ANINDA "İade"ye yazar; biz yalnız Accepted olanı siparişe
# yansıtıyoruz (integrations_trendyol.py:3951). Aradaki fark bu kovadır.
_OPEN_CLAIM_STATUSES = list(OPEN_MARKETPLACE_CLAIM_STATUSES)


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

    # ① Pazaryeri iadeleri — KARIŞIK statülü claim'lerde genel claim_status yerine
    #    her claimItem'in kendi statüsünü kullan. Aksi halde bir Accepted kalem,
    #    kardeşi Created olduğu için geçmiş raporların tamamından kayboluyordu.
    for i in range(0, len(order_numbers), 5000):
        chunk = order_numbers[i:i + 5000]
        _seen_claim_items: set[str] = set()
        async for c in db.trendyol_claims.find(
                {"order_number": {"$in": chunk}, "claim_type": {"$ne": "CANCEL"}},
                {"_id": 0, "claim_id": 1, "order_number": 1, "claim_status": 1,
                 "refund_amount": 1, "items": 1, "raw_data.items": 1}):
            onum = str(c.get("order_number") or "")
            if not onum:
                continue
            for bucket, statuses in (
                    (closed, {"Accepted"}), (open_, set(_OPEN_CLAIM_STATUSES))):
                for item in claim_items_with_status(c, statuses):
                    if item["key"] in _seen_claim_items:
                        continue
                    _seen_claim_items.add(item["key"])
                    _acc(bucket, onum, item.get("amount") or 0, item.get("quantity") or 0)
            # Rejected/Cancelled/Unresolved kalemler gerçekleşmiş iadeye girmez.

    # ② Site iadeleri — customer_returns kalemleri (onaylı kalemler öncelikli).
    #    Site siparişlerinde statü zaten iade grubuna düşüyor; buradan yalnız KISMİ
    #    iadenin tutarını çıkarıyoruz ki kalan ürünler satışta kalsın.
    if order_ids:
        for i in range(0, len(order_ids), 5000):
            chunk = order_ids[i:i + 5000]
            _return_rows = await db.customer_returns.find(
                    {"order_id": {"$in": chunk}},
                    {"_id": 1, "id": 1, "order_id": 1, "order_number": 1, "status": 1,
                     "approved_items": 1, "items": 1, "refund_amount": 1,
                     "created_at": 1, "updated_at": 1}).to_list(None)
            for r in dedupe_return_records(_return_rows):
                st = str(r.get("status") or "").lower()
                # "expired": süresi dolmuş, ürünü hiç gelmemiş iade talebi — gerçekleşmiş
                # iade DEĞİL; satış tamamlanmış sayılır (sipariş de normale döndürülür).
                if st in ("cancelled", "canceled", "rejected", "return_rejected", "expired"):
                    continue
                onum = str(r.get("order_number") or "")
                if not onum:
                    continue
                its = dedupe_return_items(r.get("approved_items") or r.get("items") or [])
                qty = sum(max(1, int((it or {}).get("quantity") or 1)) for it in its)
                # DENETİM (finansal F2): iade tutarı GERÇEK iade (refund_amount) ile hizalanmalı;
                # eskiden ham liste fiyatı × adet alınıp donmuş KUPON İNDİRİMİ yok sayılıyordu →
                # kısmi kuponlu iadede rapor gerçek kasadan çıkandan (net) yüksek gösteriyordu.
                amt = 0.0
                try:
                    _ra = float(r.get("refund_amount") or 0)
                except Exception:
                    _ra = 0.0
                if _ra > 0:
                    amt = _ra   # onaylı iadede sunucunun hesapladığı net iade
                else:
                    for it in its:
                        try:
                            _p = float((it or {}).get("price") or (it or {}).get("unit_price") or 0)
                            _d = float((it or {}).get("discount_amount") or 0)  # donmuş kupon indirimi (birim)
                            amt += max(0.0, _p - _d) * max(1, int((it or {}).get("quantity") or 1))
                        except Exception:
                            pass
                # Site tarafında "onaylı" = terminal statüler; gerisi açık talep.
                tgt = closed if st in ("returned", "refunded", "partial_refunded",
                                       "return_approved", "approved", "completed") else open_
                _acc(tgt, onum, amt, qty)
    return closed, open_


def _dedupe_by_order_number(orders: list) -> list:
    """Aynı order_number'a düşmüş KOPYA belgeleri tekilleştirir — bir sipariş = bir kayıt.

    Kopya, farklı içe-aktarım yollarından doğabiliyor: sipariş bir yolda
    `platform="trendyol"`, başka bir yolda yalnız `marketplace="trendyol"` ile
    yazılınca senkron `find_one({order_number, platform})` mevcut kaydı GÖREMEYİP
    ikinci bir belge insert ediyor. Rapor her belgeyi AYRI saydığı için adet/ciro
    şişiyordu (Trendyol paneliyle sipariş-adedi sapmasının bir kaynağı).

    Tekilleştirmede TERMİNAL durumlu (iptal/iade) kopya tercih edilir ki Trendyol'un
    gerçek durumu yansısın; eşitlikte en güncel (updated_at/created_at) kazanır.
    Boş order_number'lı belgeler olduğu gibi bırakılır. Salt-okunur — stok/kalem/
    belge verisine DOKUNMAZ, yalnız sayım girdisini tekilleştirir."""
    _TERMINAL = set(_CANCEL_STATUSES) | set(_RETURN_STATUSES_BD)

    def _rank(o):
        st = str(o.get("status") or "")
        terminal = 1 if st in _TERMINAL else 0
        try:
            has_pc = 1 if float(o.get("partial_cancel_amount") or 0) > 0 else 0
        except Exception:
            has_pc = 0
        recency = str(o.get("updated_at") or o.get("created_at") or "")
        return (terminal, has_pc, recency)

    best: dict = {}
    passthrough: list = []
    for o in orders:
        onum = str(o.get("order_number") or "").strip()
        if not onum:
            passthrough.append(o)
            continue
        cur = best.get(onum)
        if cur is None or _rank(o) > _rank(cur):
            best[onum] = o
    return list(best.values()) + passthrough


# =============================================================================
# DÖNEM MUHASEBESİ — İPTAL ve İADE "İŞLEMİN KESİNLEŞTİĞİ" GÜNE YAZILIR
# -----------------------------------------------------------------------------
# Kullanıcı kararı: ciro siparişin verildiği güne, İPTAL iptalin kesinleştiği güne,
# İADE ise GİDER PUSULASININ kesildiği güne ve pusuladaki tutar kadar yazılır.
# Böylece rapor muhasebe evrakıyla birebir tutar (pusula = iadenin resmî belgesi).
#
# Eskiden ikisi de siparişin tarihine göre sayılıyordu: ağustosta verilip eylülde
# iptal edilen sipariş eylül raporunda hiç görünmüyordu ve aynı ekrandaki iki uç
# (sales-summary ile sales-breakdown) farklı rakam veriyordu.
# =============================================================================
_ACT_CANCEL_ST = ["cancelled", "cancel_refunded"]


def _channel_of(o: dict) -> str:
    platform = str((o or {}).get("platform") or "").strip().lower()
    marketplace = str((o or {}).get("marketplace") or "").strip().lower()
    if platform in _MARKETPLACES:
        return platform
    if marketplace in _MARKETPLACES:
        return marketplace
    return "site"


def _src_allows(key: str, source: Optional[str]) -> bool:
    """Kanal, ekrandaki kaynak süzgecine uyuyor mu? (takma adlar _source_cond ile AYNI
    normalize edilir: ty/hb/amz → trendyol/hepsiburada/amazon)."""
    sv = _canon_source(source)
    if sv == "all":
        return True
    return key == sv


# Siparişi bulunamayan ve kaynağı belgeden de çıkarılamayan iade belgesi bu kanala/ödemeye
# yazılır (eskiden sessizce "Site" + "Kredi Kartı" sayılıyordu).
_UNKNOWN_CH = "bilinmeyen"


def _cohort_until(e: str) -> str:
    """Kohort işlem-tarihi üst sınırı = BUGÜNÜN (TR) sonu; seçili bitiş daha ileriyse o.

    Kohort = bu dönemin siparişleri − onların BUGÜNE KADAR kesinleşmiş iptal/iadesi
    (Trendyol paneli de böyle sayar). Gün sonuna yuvarlanır ki _action_rows önbellek
    anahtarı gün içinde sabit kalsın."""
    try:
        _today = (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()
        _t_end = tr_range_to_utc(_today, _today)[1]
    except Exception:
        return e
    return max(str(e), str(_t_end))


async def _cohort_until_rule(e: str) -> str:
    """İşletme Kuralı 'report.kohort_bugune_kadar' açıksa kohort penceresi bugüne kadar
    (_cohort_until; varsayılan AÇIK — yönetici onayı 2026-09-24), kapalıysa seçili aralığın sonu."""
    try:
        from business_rules import get_rule
        if await get_rule(db, "report.kohort_bugune_kadar", True):
            return _cohort_until(e)
    except Exception:
        pass
    return e


def _cohort_members(orders: list) -> set:
    """Kohort üyeliği = bu dönemin (bağlamdaki) sipariş kümesi. İptal/iade satırı, bağlı
    olduğu sipariş bu kümedeyse "bu_donem" sayılır — kartlar, kanal tablosu, grafik ve
    ödeme paneli AYNI kümeyi kullanır (Σ grafik = Σ ödeme = net_kohort)."""
    return {str(o.get("order_number")) for o in (orders or []) if o.get("order_number")}


def _cohort_member_key(row: dict, members: set) -> str:
    onum = str((row.get("order") or {}).get("order_number") or "")
    return "bu_donem" if (onum and onum in members) else "onceki"


def _order_items_total(o: dict) -> float:
    """Kalemlerden sipariş tutarı (Σ fiyat × adet) — total 0/boş gelen pazaryeri
    iptallerinde (Hepsiburada) rapor tutarı için yedek. İçe aktarma verisi DEĞİŞMEZ."""
    tot = 0.0
    for it in ((o or {}).get("items") or []):
        it = it or {}
        try:
            q = max(1, int(it.get("quantity") or 1))
        except Exception:
            q = 1
        tot += max(0.0, _fnum(it.get("price") if it.get("price") not in (None, "")
                              else it.get("unit_price"))) * q
    return round(tot, 2)


def _fill_cancel_total(o: dict) -> dict:
    """İptal siparişin total'ı 0/boşsa tutarı kalemlerden hesaplar (yalnız rapor belleği)."""
    if str((o or {}).get("status") or "") in _ACT_CANCEL_ST and _fnum(o.get("total")) <= 0.005:
        _it = _order_items_total(o)
        if _it > 0.005:
            o["total"] = _it
            o["_tutar_kalemlerden"] = True
    return o


# ── GİDER PUSULASI KALEMLERİ: ürün satırı / ücret satırı ─────────────────────
# Site iade akışı pusulaya barkodsuz ücret satırları ekler: "Kargo Bedeli" ve
# "Vade Farkı (Taksit xN)" (orders.py ~9867-9889). Bunlar iade edilen ÜRÜN ADEDİ
# değildir. Tutar tarafında: kargo sipariş toplamının (order.total) İÇİNDEDİR
# (orders.py ~9739-9752: ürün satırları = ödenen − kargo − vade) — kargo iadesi
# siparişin cirosundan düşülmeye devam eder. VADE FARKI ise order.total'da YOKTUR
# (iyzico taksit faizi); pusula tutarından çıkarılır, yoksa iade satışı aşıyordu.
_FEE_SKUS = {"VADEFARKI", "KARGO", "HEDIYEPAKETI", "KAPIDAODEME"}
_FEE_NAME_PREFIXES = ("kargo bedeli", "vade farkı", "vade farki", "hediye paketi",
                      "kapıda ödeme", "kapida odeme")


def _pusula_line_kind(it: dict) -> str:
    """'urun' | 'kargo' | 'vade' | 'ucret' — pusula kalem türü."""
    it = it or {}
    sku = str(it.get("sku") or "").strip().upper()
    name = _tr_lower(it.get("name") or "").strip()
    reason = _tr_lower(it.get("reason") or "").strip()
    if sku == "VADEFARKI" or name.startswith(("vade farkı", "vade farki")) \
            or reason.startswith(("vade farkı", "vade farki")):
        return "vade"
    if sku == "KARGO" or name.startswith("kargo bedeli"):
        return "kargo"
    if sku in _FEE_SKUS or name.startswith(_FEE_NAME_PREFIXES):
        return "ucret"
    bc = str(it.get("barcode") or it.get("sku") or "").strip()
    if not bc and reason == "kargo":
        return "kargo"
    return "urun"


def _pusula_values(g: dict) -> dict:
    """Pusuladan rapor değerleri: rev = totals.net − vade farkı satırları (order.total
    tabanında), units = yalnız ÜRÜN satırlarının adedi (ürün satırı yoksa 1)."""
    net = _fnum((g.get("totals") or {}).get("net"))
    vade = 0.0
    units = 0
    lines = 0
    for it in (g.get("items") or []):
        kind = _pusula_line_kind(it)
        try:
            q = max(1, int((it or {}).get("quantity") or 1))
        except Exception:
            q = 1
        if kind == "urun":
            units += q
            lines += 1
        elif kind == "vade":
            _np = (it or {}).get("net_price")
            vade += max(0.0, _fnum(_np if _np not in (None, "") else (it or {}).get("unit_price"))) * q
    rev = round(max(0.0, net - vade), 2) if net > 0 else net
    return {"rev": rev, "units": units or 1, "prod_units": units,
            "has_lines": lines > 0, "vade": round(vade, 2)}


def _parmak_izi(g) -> tuple:
    """Mükerrer pusula parmak izi: aynı siparişte kalem kümesi (barkod+adet) birebir
    tekrar eden pusulalardan yalnız biri sayılır; kalemsizde tutar + saniye."""
    sig = []
    for it in (g.get("items") or []):
        bc = str((it or {}).get("barcode") or (it or {}).get("sku") or "").strip()
        try:
            q = max(1, int((it or {}).get("quantity") or 1))
        except Exception:
            q = 1
        if bc:
            sig.append((bc, q))
    if sig:
        return tuple(sorted(sig))
    return ("__kalemsiz__", round(_fnum((g.get("totals") or {}).get("net")), 2),
            str(g.get("created_at") or "")[:19])


# KESİNLEŞMİŞ İADE = numarası (koçan no) ATANMIŞ gider pusulası.
_NUMBERED_PUSULA = {"$or": [
    {"number": {"$exists": True, "$ne": None}},
    {"display_number": {"$exists": True, "$nin": ["", None]}}]}


def _hb_variants(nos) -> dict:
    """Pusula no → aday sipariş no'ları. Eski Hepsiburada pusulaları sipariş numarasını
    'HB' ön eki OLMADAN taşıyor (sipariş 'HB4801893473', pusula '4801893473')."""
    out = {}
    for n in nos:
        n = str(n or "").strip()
        if not n:
            continue
        c = [n]
        if n.isdigit():
            c.append("HB" + n)
            if n.lstrip("0") and n.lstrip("0") != n:
                c.append("HB" + n.lstrip("0"))
        out[n] = c
    return out


async def _voucher_totals(order_numbers) -> dict:
    """{sipariş_no: {"rev", "units", "docs", "has_lines"}} — TÜM ZAMANLARDA kesilmiş
    numaralı gider pusulaları (mükerrer elenmiş, ürün-satırı tabanında).

    Kullanım: (a) sonradan 'cancelled' statüsüne düşmüş iadeli siparişin iptal satırını
    pusula kadar küçültmek, (b) pusulası kesilmiş siparişi 'Açık İade'den çıkarmak."""
    want = {str(x).strip() for x in (order_numbers or []) if str(x or "").strip()}
    out: dict = {}
    if not want:
        return out
    cands = set(want)
    for n in want:
        if n.upper().startswith("HB") and n[2:].isdigit():
            cands.add(n[2:])
    cl = sorted(cands)
    raw = []
    try:
        for i in range(0, len(cl), 5000):
            async for g in db.gider_pusulasi.find(
                    {"order_number": {"$in": cl[i:i + 5000]}, **_NUMBERED_PUSULA},
                    {"_id": 0, "order_number": 1, "totals": 1, "items": 1, "created_at": 1}):
                raw.append(g)
    except Exception as _ve:
        logger.warning(f"[rapor] pusula haritası okunamadı: {_ve}")
        return out
    raw.sort(key=lambda g: str(g.get("created_at") or ""))
    seen = set()
    for g in raw:
        on = str(g.get("order_number") or "").strip()
        target = on if on in want else ("HB" + on if ("HB" + on) in want else None)
        if not target:
            continue
        k = (target, _parmak_izi(g))
        if k in seen:
            continue
        seen.add(k)
        v = _pusula_values(g)
        d = out.setdefault(target, {"rev": 0.0, "units": 0, "docs": 0, "has_lines": False})
        d["rev"] += max(0.0, v["rev"])
        d["units"] += v["prod_units"]
        d["docs"] += 1
        d["has_lines"] = d["has_lines"] or v["has_lines"]
    for d in out.values():
        d["rev"] = round(d["rev"], 2)
    return out


def _orphan_channel(g: dict) -> str:
    """Siparişi bulunamayan pusulanın kanalı — belgenin KENDİ kaynağından çıkarılır:
    source alanı → claim_id (Trendyol iade talebinden kesilmiş) → numara biçimi
    ('HB…' Hepsiburada, 'W…' site). Hiçbiri yoksa 'bilinmeyen' (Site'a YAZILMAZ)."""
    sv = str(g.get("source") or "").strip().lower()
    if sv in _MARKETPLACES:
        return sv
    if sv in ("site", "meluni", "web", "storefront"):
        return "site"
    if g.get("claim_id"):
        return "trendyol"
    on = str(g.get("order_number") or "").strip().upper()
    if on.startswith("HB") and on[2:].isdigit():
        return "hepsiburada"
    if on.startswith("W") and on[1:].isdigit():
        return "site"
    return _UNKNOWN_CH


# Ham işlem satırları kısa ömürlü önbellekte tutulur: Raporlar sayfası tek açılışta
# 7+ ucu paralel çağırıyor ve sales-summary tek istekte 7 farklı aralık hesaplıyor.
_ACT_TTL_SEC = 90
_ACT_CACHE: dict = {}
_ACT_LOCKS: dict = {}


async def _action_rows(s: str, e: str) -> dict:
    """[s, e] aralığında KESİNLEŞEN iptal/iade HAM satırları (kaynak süzgeci UYGULANMAZ).

    Satır: {"at", "ch", "rev", "units", "pay", "order"}
      at    : işlemin kesinleştiği an  — iptalde cancelled_at ?? updated_at,
              iadede gider_pusulasi.created_at
      rev   : tutar — iptalde siparişin toplamı, iadede PUSULADAKİ net tutar
      ch    : kanal (site/trendyol/hepsiburada/temu/n11/amazon)
      pay   : ödeme kırılımı anahtarı (siparişten; ödeme raporu için)
      order : işlemin bağlı olduğu sipariş (boyut kırılımları için; iadede join'lenir)

    Süzme ve dönem dilimleme BELLEKTE yapılır (_slice_actions) — böylece aynı istekte
    bugün/dün/hafta/ay/yıl için tek sorgu yeter.
    """
    import asyncio as _aio, time as _time
    # Ticimax kopya elemesi (merge_match) çağrı sırasına bağlı kalmasın: doğrudan
    # çağrılarda (teşhis/asistan) da yüklenir; durum önbellek anahtarına girer ki
    # elemesiz hesaplanmış sonuç elemeli isteğe servis edilmesin.
    await _ensure_dup_loaded()
    key = (str(s), str(e), dup_filter_state())
    now = _time.monotonic()
    gen = await _shared_report_gen()
    hit = _ACT_CACHE.get(key)
    if hit and hit[2] == gen and (now - hit[0]) < _ACT_TTL_SEC:
        return hit[1]
    lock = _ACT_LOCKS.setdefault(key, _aio.Lock())
    async with lock:
        gen = await _shared_report_gen()
        hit = _ACT_CACHE.get(key)
        if hit and hit[2] == gen and (_time.monotonic() - hit[0]) < _ACT_TTL_SEC:
            return hit[1]
        res = await _action_rows_uncached(s, e)
        _ACT_CACHE[key] = (_time.monotonic(), res, gen)
        if len(_ACT_CACHE) > 32:
            for k in sorted(_ACT_CACHE, key=lambda k: _ACT_CACHE[k][0])[:16]:
                _ACT_CACHE.pop(k, None); _ACT_LOCKS.pop(k, None)
        return res


_ACT_ORDER_PROJ = {
    "_id": 0, "order_number": 1, "total": 1, "items.quantity": 1,
    "items.price": 1, "items.unit_price": 1, "status": 1,
    "partial_cancel_amount": 1, "partial_cancel_units": 1,
    "partial_cancel_total_scope": 1,
    "platform": 1, "marketplace": 1, "payment_method": 1,
    "marketplace_order_date": 1, "created_at": 1,
    "shipping_address.city": 1, "shipping_address.district": 1,
}


async def _ensure_dup_loaded() -> None:
    try:
        await load_dup_order_numbers()
    except Exception:
        pass


async def _action_rows_uncached(s: str, e: str) -> dict:
    cancels: list = []
    returns: list = []
    _atlanan_say = 0
    _atlanan_tut = 0.0
    _mukerrer_rows: list = []     # elenen mükerrer pusulalar (dönem/kaynak dilimi için)
    _cakisma_rows: list = []      # pusulası olduğu için düşülen/küçültülen iptaller

    # ── İPTALLER — iptalin kesinleştiği tarihe göre ──────────────────────────
    try:
        async for o in db.orders.aggregate([
            # Hiç ödenmemiş site siparişinin iptali satış iptali DEĞİLDİR (brüte de girmez).
            {"$match": merge_match({"$and": [{"status": {"$in": _ACT_CANCEL_ST}},
                                             _unpaid_site_cancel_nor()]})},
            {"$addFields": {"_ad": {"$ifNull": ["$cancelled_at", "$updated_at"]}}},
            {"$match": {"_ad": {"$gte": s, "$lte": e}}},
            {"$project": {**_ACT_ORDER_PROJ, "_ad": 1,
                          "cancelled_at": 1, "updated_at": 1}},
        ], allowDiskUse=True):
            # Hepsiburada iptalleri 0 TL içe aktarılabiliyor: tutar kalemlerden.
            _fill_cancel_total(o)
            rev = _fnum(o.get("total"))
            # _ad boruda hesaplanır; beklenmedik bir şekilde gelmezse satır DÜŞMESİN.
            _at = str(o.get("_ad") or o.get("cancelled_at") or o.get("updated_at") or "")
            cancels.append({"at": _at, "ch": _channel_of(o),
                            "rev": rev, "units": _order_units(o),
                            "pay": payment_report_group_key(o), "order": o,
                            "onum": str(o.get("order_number") or "")})
    except Exception as _ce:
        logger.warning(f"[rapor] dönem iptalleri okunamadı: {_ce}")

    # ── AYNI SİPARİŞ HEM İPTAL HEM İADE OLMASIN ─────────────────────────────
    # Numaralı gider pusulası kesilmiş (iadesi KESİNLEŞMİŞ) sipariş sonradan
    # 'cancelled' statüsüne düşebiliyor (ör. Trendyol "teslim edilemedi" senkronu).
    # Muhasebe gerçeği pusuladır → iade sayılır. KURAL (basit, güvenli, tutar
    # tabanlı): iptal tutarı = sipariş toplamı − siparişin TÜM ZAMANLARDAKİ numaralı
    # pusula toplamı; kalan adet = sipariş adedi − pusuladaki ÜRÜN adedi. Kalan tutar
    # ~0 ya da kalan adet 0 ise iptal satırı tamamen düşer; aksi halde yalnız pusulada
    # olmayan kısım iptal kalır. Böylece iptal + iade hiçbir zaman sipariş tutarını
    # aşmaz ve tarih penceresinden bağımsızdır (pusula hangi ay kesilmiş olursa olsun).
    try:
        _full = [r for r in cancels if not r.get("partial") and r.get("onum")]
        _vmap = await _voucher_totals([r["onum"] for r in _full]) if _full else {}
        if _vmap:
            _keep = []
            for r in cancels:
                v = _vmap.get(r.get("onum") or "") if not r.get("partial") else None
                if not v:
                    _keep.append(r)
                    continue
                _rem = round(max(0.0, float(r["rev"]) - float(v["rev"])), 2)
                _rem_u = (max(0, int(r["units"]) - int(v["units"]))
                          if v.get("has_lines") else int(r["units"]))
                if _rem <= 0.005 or _rem_u <= 0:
                    _cakisma_rows.append({"at": r["at"], "ch": r["ch"], "rev": r["rev"],
                                          "tur": "dusuldu"})
                    continue
                _cakisma_rows.append({"at": r["at"], "ch": r["ch"],
                                      "rev": round(float(r["rev"]) - _rem, 2),
                                      "tur": "kucultuldu"})
                r = dict(r, rev=_rem, units=_rem_u, pusulali=True)
                _keep.append(r)
            cancels = _keep
    except Exception as _xe:
        logger.warning(f"[rapor] iptal/pusula çakışma kontrolü yapılamadı: {_xe}")

    # ── KISMİ İPTALLER — sipariş AKTİF kalır, bir kısmı iptal edilmiştir ────
    # Trendyol mutabakatı bu tutarı iptale yazar; tam iptal sorgusu bunları GÖRMEZ
    # (statü "cancelled" değildir). Tarihi: partial_cancel_at, yoksa updated_at.
    try:
        async for o in db.orders.aggregate([
            {"$match": merge_match({"partial_cancel_amount": {"$gt": 0},
                                    "status": {"$nin": _ACT_CANCEL_ST}})},
            {"$addFields": {"_ad": {"$ifNull": ["$partial_cancel_at", "$updated_at"]}}},
            {"$match": {"_ad": {"$gte": s, "$lte": e}}},
            {"$project": {**_ACT_ORDER_PROJ, "_ad": 1, "updated_at": 1,
                          "partial_cancel_amount": 1, "partial_cancel_units": 1}},
        ], allowDiskUse=True):
            rev = max(0.0, _fnum(o.get("partial_cancel_amount")))
            try:
                unit = max(1, int(o.get("partial_cancel_units") or 1))
            except Exception:
                unit = 1
            cancels.append({"at": str(o.get("_ad") or o.get("updated_at") or ""),
                            "ch": _channel_of(o), "rev": rev, "units": unit,
                            "pay": payment_report_group_key(o), "partial": True,
                            "order": o, "onum": str(o.get("order_number") or "")})
    except Exception as _pe:
        logger.warning(f"[rapor] dönem kısmi iptalleri okunamadı: {_pe}")

    # ── İADELER — gider pusulasının kesildiği tarihe göre, PUSULA tutarıyla ──
    # Kanal/ödeme/şehir kırılımı için pusula sipariş numarasından siparişe bağlanır;
    # sipariş bulunamazsa pusulanın kendi `source` alanına düşülür.
    try:
        raw = []
        # KESİNLEŞMİŞ İADE = numarası (koçan no) ATANMIŞ gider pusulası.
        # Kadir: "sadece gider pusulası oluşturulup numarası atanmış siparişleri
        # iadeden say; diğerleri talep veya onaylanmamış iade, kesinleşmiş değil."
        # Numarası temizlenen (clear-numbers) pusula havuza GERİ döner ve kesinleşmiş
        # sayılmaz — sistemin kendi ölçütü (integrations_trendyol._has_no) ile aynı.
        # Kesinleşmemiş iadeler HİÇBİR ŞEY DÜŞÜLMEDEN normal satış olarak kalır.
        async for g in db.gider_pusulasi.find(
                {"created_at": {"$gte": s, "$lte": e}, **_NUMBERED_PUSULA},
                {"_id": 0, "order_number": 1, "source": 1, "totals": 1,
                 "items": 1, "created_at": 1, "number": 1, "claim_id": 1}):
            raw.append(g)
        # Deterministik eleme: en ERKEN belge tutulur (okuma penceresi dönem ya da
        # "bugüne kadar" olsa da aynı belge kalsın).
        raw.sort(key=lambda g: str(g.get("created_at") or ""))

        # ── MÜKERRER PUSULA ELEME ───────────────────────────────────────────
        # Aynı siparişe birden çok pusula kesilmiş olabilir ve İKİSİ ÇOK FARKLI:
        #   • GERÇEK kısmi iade — sipariş 11351567857: iki pusula, biri "Şort Siyah XS",
        #     diğeri "Şort Ekru S". Tutarlar AYNI (1.425,28) çünkü ürünler aynı fiyatta.
        #     İkisi de geçerli; silinirse gerçekten olmuş iade yok olur.
        #   • MÜKERRER — sipariş 4598214509: üç pusula (koçan 881/882/883), üçü de AYNI
        #     SANİYEDE, AYNI üç barkodla, aynı tutarla. Aynı iade üç kez yazılmış.
        # Bu yüzden ölçüt TUTAR DEĞİL, KALEMLERDİR: aynı siparişte kalem kümesi
        # (barkod+adet) birebir tekrar eden pusulalardan yalnız biri sayılır.
        # Kalemi olmayan pusulada tutar + saniye birlikte parmak izi olarak kullanılır
        # (modül düzeyindeki _parmak_izi — _voucher_totals da AYNI ölçütü kullanır).
        _gorulen = set()
        _temiz = []
        _atlanan = 0
        _atlanan_tutar = 0.0
        for g in raw:
            _on = str(g.get("order_number") or "")
            if not _on:
                _temiz.append(g)      # sipariş numarasız belge eşleştirilemez, dokunma
                continue
            _k = (_on, _parmak_izi(g))
            if _k in _gorulen:
                _atlanan += 1
                _atlanan_tutar += _fnum((g.get("totals") or {}).get("net"))
                _mukerrer_rows.append({"at": str(g.get("created_at") or ""), "onum": _on,
                                       "rev": _fnum((g.get("totals") or {}).get("net"))})
                continue
            _gorulen.add(_k)
            _temiz.append(g)
        _atlanan_say, _atlanan_tut = _atlanan, _atlanan_tutar
        if _atlanan:
            logger.info(f"[rapor] mükerrer gider pusulası elendi: {_atlanan} belge / "
                        f"{round(_atlanan_tutar, 2)} TL (aynı sipariş + aynı kalemler)")
        raw = _temiz
        nos = list({str(g.get("order_number") or "") for g in raw if g.get("order_number")})
        omap: dict = {}
        for i in range(0, len(nos), 5000):
            async for o in db.orders.find(
                    {"order_number": {"$in": nos[i:i + 5000]}}, _ACT_ORDER_PROJ):
                omap[str(o.get("order_number"))] = o
        # Eşleşmeyen salt-rakam numaralar: eski Hepsiburada pusulaları 'HB' ön eksiz
        # (sipariş 'HB4801893473', pusula '4801893473') — ön ekli adayla yeniden ara.
        _hbv = _hb_variants([n for n in nos if n not in omap and n.isdigit()])
        _alt = {c: n for n, cs in _hbv.items() for c in cs[1:]}
        _altl = list(_alt)
        for i in range(0, len(_altl), 5000):
            async for o in db.orders.find(
                    {"order_number": {"$in": _altl[i:i + 5000]}}, _ACT_ORDER_PROJ):
                _src_no = _alt.get(str(o.get("order_number")))
                if _src_no and _src_no not in omap:
                    omap[_src_no] = o
        for _m in _mukerrer_rows:        # elenen belgenin kanalı (kaynak süzgeci için)
            _mo = omap.get(_m["onum"])
            _m["ch"] = _channel_of(_mo) if _mo is not None else _UNKNOWN_CH
        for g in raw:
            onum = str(g.get("order_number") or "")
            o = omap.get(onum)
            if o is not None:
                ch = _channel_of(o)
                pay = payment_report_group_key(o)
            else:
                # YETİM PUSULA: sessizce Site/Kredi Kartı YAZILMAZ — kanal belgenin
                # kendi kaynağından (source/claim_id/numara biçimi), yoksa 'bilinmeyen'.
                ch = _orphan_channel(g)
                pay = ch if ch in _MARKETPLACES else _UNKNOWN_CH
            # Tutar: pusula neti − vade farkı satırı (order.total tabanı); adet:
            # yalnız ÜRÜN satırları (Kargo Bedeli / Vade Farkı iade ADEDİ değildir).
            _pv = _pusula_values(g)
            returns.append({"at": str(g.get("created_at") or ""), "ch": ch,
                            "rev": _pv["rev"], "units": _pv["units"],
                            "prod_units": _pv["prod_units"],
                            "has_lines": _pv["has_lines"], "vade": _pv["vade"],
                            "pay": pay, "order": o or {},
                            "onum": str((o or {}).get("order_number") or onum),
                            "orphan": o is None,
                            # KISMİ (satır düzeyi, geriye uyum). Kartlar/tablolar kısmiliği
                            # SİPARİŞ düzeyinde _slice_actions içinde yeniden hesaplar.
                            "partial": bool(_fnum((o or {}).get("total")) > 0.005
                                            and _pv["rev"] < _fnum((o or {}).get("total")) - 0.005)})
    except Exception as _re2:
        logger.warning(f"[rapor] dönem iadeleri (gider pusulası) okunamadı: {_re2}")

    return {"cancels": cancels, "returns": returns,
            "mukerrer_elenen": {"belge": _atlanan_say, "tutar": round(_atlanan_tut, 2)},
            "mukerrer_rows": _mukerrer_rows, "cakisma_rows": _cakisma_rows}


def _slice_actions(rows: dict, s: str, e: str, source: Optional[str] = None,
                   keyfn=None, end_exclusive: bool = False,
                   at_until: Optional[str] = None) -> dict:
    """Ham satırları [s, e] dönemine ve kaynak süzgecine göre kovala.

    keyfn(row) verilmezse kanal (`ch`) kullanılır; ödeme/gün/şehir kırılımları için
    başka bir anahtar fonksiyonu geçilir. end_exclusive=True → [s, e) (gün sınırı
    bitişik aralıklarda — bugün/dün — çift sayım olmasın diye)."""
    def _blank():
        return {"revenue": 0.0, "orders": 0, "units": 0, "partial_orders": 0,
                "docs": 0}

    # at_until: KOHORT penceresi — işlem tarihi [s, at_until] (dahil). Verilmezse
    # dönem penceresi [s, e] (end_exclusive'e göre).
    out = {"cancels": {}, "returns": {}}
    for kind in ("cancels", "returns"):
        bucket = out[kind]
        per_order: dict = {}      # k -> {sipariş: birikim} — tekil sipariş + kısmilik
        for r in rows.get(kind) or []:
            at = r.get("at") or ""
            if not at or at < s:
                continue
            if at_until is not None:
                if at > at_until:
                    continue
            elif (at >= e if end_exclusive else at > e):
                continue
            ch = r.get("ch") or "site"
            if not _src_allows(ch, source):
                continue
            k = keyfn(r) if keyfn else ch
            if k is None:
                continue
            d = bucket.setdefault(k, _blank())
            d["revenue"] += float(r.get("rev") or 0)
            d["units"] += int(r.get("units") or 0)
            d["docs"] += 1
            o = r.get("order") or {}
            oid = str(r.get("onum") or o.get("order_number") or "") or ("__satir__", id(r))
            a = per_order.setdefault(k, {}).setdefault(oid, {
                "rev": 0.0, "units": 0, "has_lines": False, "any_full": False,
                "all_partial": True, "order": o})
            a["rev"] += float(r.get("rev") or 0)
            a["units"] += int(r.get("prod_units", r.get("units")) or 0)
            a["has_lines"] = a["has_lines"] or bool(r.get("has_lines"))
            if not r.get("partial"):
                a["all_partial"] = False
        for k, orders_ in per_order.items():
            d = bucket[k]
            # SİPARİŞ = tekil sipariş numarası (aynı siparişe iki pusula 2 sipariş değil).
            d["orders"] = len(orders_)
            n_part = 0
            for a in orders_.values():
                if kind == "cancels":
                    part = a["all_partial"]
                else:
                    # İADE kısmiliği SİPARİŞ düzeyinde: tüm pusulaları birlikte siparişin
                    # tamamını karşılamıyorsa kısmi (iki pusulayla tam iade ≠ 2 kısmi).
                    o = a["order"] or {}
                    if not o:
                        part = False
                    elif a["has_lines"] and (o.get("items") or []):
                        _ou = _order_units(o)
                        # Kısmi iptal edilmiş kalemler (legacy/full kapsam) iade edilemez.
                        if (_fnum(o.get("partial_cancel_amount")) > 0 and str(
                                o.get("partial_cancel_total_scope") or "").lower() != "active"):
                            try:
                                _ou -= max(0, int(o.get("partial_cancel_units") or 0))
                            except Exception:
                                pass
                        part = a["units"] < max(1, _ou)
                    else:
                        _ot = _fnum(o.get("total"))
                        part = _ot > 0.005 and a["rev"] < _ot - 0.005
                n_part += 1 if part else 0
            d["partial_orders"] = n_part
        for v in bucket.values():
            v["revenue"] = round(v["revenue"], 2)
    return out


def _cohort_key(row: dict, s: str, e: str, end_exclusive: bool = False) -> str:
    """Bir iptal/iade satırını, ilgili SİPARİŞİN verildiği döneme göre etiketler.

    "bu_donem" = iade/iptal, bu dönemde satılmış bir siparişe ait.
    "onceki"   = sipariş DAHA ÖNCE satılmış (ya da belgenin siparişi bulunamamış).

    Dönem net'i bu ay kesinleşen tüm iptal/iadeleri düşer; pazaryeri panelleri ise
    iadeyi siparişin satıldığı aydan düşer. Bu ayrım olmadan iki rakam kıyaslanamaz.
    Siparişi bulunamayan belge ihtiyatlı biçimde "onceki" sayılır — bu ayın satışına
    yazılmaz.
    """
    o = row.get("order") or {}
    sd = str(o.get("marketplace_order_date") or o.get("created_at") or "")
    if not sd:
        return "onceki"
    inside = sd >= s and (sd < e if end_exclusive else sd <= e)
    return "bu_donem" if inside else "onceki"


def _cohort_period_key(row: dict, members: set, fmt: str) -> Optional[str]:
    """Kohort iptal/iade satırını SİPARİŞİN verildiği TR dönem anahtarına yazar
    (gün/hafta/ay grafiği). Bu dönemin sipariş kümesinde olmayan satır → None (dilimde
    atlanır). Sipariş zamanı `_order_datetime_tr` ile — siparişleri kovalayan AYNI
    fonksiyon."""
    if _cohort_member_key(row, members) != "bu_donem":
        return None
    local = _order_datetime_tr(row.get("order") or {})
    return local.strftime(fmt) if local else None


def _tr_key_of(iso: str, fmt: str) -> Optional[str]:
    """İşlem zamanından (UTC ISO) TR yerel dönem anahtarı — gün/hafta/ay serileri için."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(timezone.utc) + timedelta(hours=3)).strftime(fmt)
    except Exception:
        return None


def _sum_actions(bucket: dict) -> dict:
    """Kova sözlüğünü tek toplama indirger."""
    return {"revenue": round(sum(v["revenue"] for v in bucket.values()), 2),
            "orders": sum(v["orders"] for v in bucket.values()),
            "units": sum(v["units"] for v in bucket.values()),
            # "465 iade · 43'ü kısmi" notu — kartların altındaki kısmi sayacı
            "partial_orders": sum(v.get("partial_orders", 0) for v in bucket.values()),
            # Belge (pusula/iptal kaydı) sayısı — 'orders' TEKİL sipariştir.
            "docs": sum(v.get("docs", 0) for v in bucket.values())}


def _side_rows_summary(rows: list, s: str, e: str, source: Optional[str] = None) -> dict:
    """Yan listeleri (mükerrer/çakışma/yetim) dönem + kaynak süzgeciyle özetler."""
    n, t = 0, 0.0
    for r in rows or []:
        at = r.get("at") or ""
        if not at or at < s or at > e:
            continue
        if not _src_allows(r.get("ch") or "site", source):
            continue
        n += 1
        t += float(r.get("rev") or 0)
    return {"belge": n, "tutar": round(t, 2)}


async def _period_actions(s: str, e: str, source: Optional[str] = None,
                          keyfn=None, end_exclusive: bool = False) -> dict:
    """[s, e] aralığında KESİNLEŞEN iptal ve iadeler — varsayılan kırılım kanal.

    İptal tarihi : cancelled_at ?? updated_at
    İade tarihi  : gider_pusulasi.created_at   (tutar = totals.net)
    Sipariş tarihi FARK ETMEZ; işlemin yapıldığı döneme yazılır.
    """
    rows = await _action_rows(s, e)
    return _slice_actions(rows, s, e, source, keyfn, end_exclusive)


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
    included_order_count = 0

    for o in orders:
        st = str(o.get("status") or "")
        if st in _EXCLUDED_STATUSES and st not in _CANCEL_STATUSES and st not in _RETURN_STATUSES_BD:
            continue  # ödenmemiş grubu (awaiting_payment/pending/failed) — ciroya girmez
        included_order_count += 1
        total = _fnum(o.get("total"))
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
        pc = max(0.0, _fnum(o.get("partial_cancel_amount")))
        if pc > 0:
            try:
                pc_u = max(1, int(o.get("partial_cancel_units") or 1))
            except Exception:
                pc_u = 1
            active_scope = str(o.get("partial_cancel_total_scope") or "").lower() == "active"
            # Active scope'ta items/total zaten kalan pakettir; iptal metriğini ayrıca
            # göster ama netten ikinci kez düşme. Legacy/full kayıtta eski davranış korunur.
            if not active_scope:
                pc = min(pc, total)
                pc_u = min(pc_u, max(0, units - 1))
            partial_cancels += 1
            cancels["revenue"] += pc
            cancels["orders"] += 1
            cancels["units"] += pc_u
            total, units = partial_cancel_net_values(
                total, units, pc, pc_u, o.get("partial_cancel_total_scope"))

        c = closed.get(onum)
        op = open_.get(onum)
        if st in _RETURN_CLOSED_STATUSES_BD:
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
            returns["orders"] += 1
            total = round(total - r_amt, 2)
            units -= r_qty
            if total <= 0.005 and units <= 0:
                continue
            partial_split += 1
            partial_returns += 1

        # Aktif satış
        net["revenue"] += max(0.0, total)
        net["orders"] += 1
        net["units"] += max(0, units)
        # Legacy site rows can carry an operational open order status even when
        # their customer_returns bridge is missing.  Do not silently turn those
        # requests into completed returns; project the remaining order instead.
        if st in _RETURN_OPEN_STATUSES_BD and not op:
            op = {"amount": total, "qty": units}
        if op and (op["amount"] > 0.005 or op["qty"] > 0):
            pending["revenue"] += min(float(op["amount"]), total)
            pending["orders"] += 1
            pending["units"] += min(int(op["qty"]) or 1, units)

    def _fin(d):
        return {"revenue": round(d["revenue"], 2), "orders": d["orders"], "units": d["units"]}


    cancels, returns, net, pending = _fin(cancels), _fin(returns), _fin(net), _fin(pending)
    included = {
        "revenue": round(net["revenue"] + cancels["revenue"] + returns["revenue"], 2),
        # Kısmi iptal/iade aynı siparişi hem net hem aksiyon kovasında gösterebilir;
        # brüt sipariş sayısı yine gerçek tekil sipariş sayısıdır.
        "orders": included_order_count,
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


# PERFORMANS: Raporlar sayfası açılışta 7+ ucu PARALEL çağırır ve her biri aynı tarih aralığı
# için aynı ağır aggregation'ı (hesaplanmış anahtarla $sort + $group → indeks kullanamaz) ve
# aynı iade eşlemelerini yeniden çalıştırıyordu → Mongo'da 7 kat yük, sayfa saniyelerce bekliyordu.
# Çözüm: (s, e, source) anahtarıyla kısa ömürlü (90 sn) bellek önbelleği + tek-uçuş kilidi
# (aynı anda gelen özdeş istekler tek sorguyu bekler). Rapor verisi 90 sn bayat olabilir; kabul.
_CTX_TTL_SEC = 90
# Son rapor yüklemelerinin süreleri (teşhis; /health?diag=1 → report_perf)
REPORT_PERF: list = []
_CTX_CACHE: dict = {}
_CTX_LOCKS: dict = {}
# Veri sürümü — PAYLAŞILAN. Uygulama birden fazla instance'ta koşuyor (scheduler'daki
# lider seçimi/lease bunun için var; deploy sırasında eski+yeni instance da bir arada).
# Sürüm süreç-içi tutulursa siparişi YAZAN instance kendi önbelleğini temizler ama raporu
# BAŞKA instance servis edince orada bayat kalır. Bu yüzden damga Mongo'da tek küçük
# dokümanda durur; her instance onu okur.
_GEN_DOC_ID = "report_cache_gen"


async def _shared_report_gen() -> int:
    """Tüm instance'ların paylaştığı rapor veri sürümü. Tek, id ile indeksli find_one
    (mikro-saniyeler) — ağır aggregation'ın yanında maliyeti yok sayılır."""
    try:
        d = await db.settings.find_one({"id": _GEN_DOC_ID}, {"_id": 0, "n": 1})
        return int((d or {}).get("n") or 0)
    except Exception:
        return 0     # damga okunamazsa süre bazlı davranışa (TTL) düş


async def invalidate_report_cache() -> None:
    """Sipariş verisi değiştiğinde rapor önbelleğini ANINDA düşür — TÜM instance'larda.

    Önbellek yalnızca "raporlar sayfası açılışta 7 ucu paralel çağırıp aynı ağır
    aggregation'ı tekrarlamasın" diye var; veri değiştiyse beklemenin anlamı yok.

    Paylaşılan sürümü artırır: bu andan sonra HERHANGİ bir instance'ta üretilmiş eski
    girdiler geçersiz olur. Temizlik anında SÜRMEKTE OLAN bir hesaplamanın sonradan
    bayat değer yazması da engellenir (o hesaplama eski sürümle etiketlenir).
    """
    _CTX_CACHE.clear()               # bu instance'ta hemen
    try:
        await db.settings.update_one(
            {"id": _GEN_DOC_ID},
            {"$inc": {"n": 1},
             "$set": {"id": _GEN_DOC_ID, "at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass                         # damga yazılamazsa TTL yine de sınırlar


async def _canonical_report_context(s: str, e: str, source: Optional[str] = None) -> tuple:
    import asyncio as _aio, time as _time
    # Ticimax kopya elemesi çağrı sırasına bağlı olmasın (teşhis/asistan doğrudan
    # çağırıyor, router bağımlılığı çalışmıyor): burada yüklenir ve eleme DURUMU anahtara
    # girer — elemesiz hesaplanmış sonuç 90 sn boyunca sayfaya servis edilemez.
    await _ensure_dup_loaded()
    key = (str(s), str(e), _canon_source(source), dup_filter_state())
    now = _time.monotonic()
    _gen = await _shared_report_gen()
    hit = _CTX_CACHE.get(key)
    if hit and hit[2] == _gen and (now - hit[0]) < _CTX_TTL_SEC:
        return hit[1]
    lock = _CTX_LOCKS.setdefault(key, _aio.Lock())
    async with lock:
        _gen = await _shared_report_gen()
        hit = _CTX_CACHE.get(key)
        if hit and hit[2] == _gen and (_time.monotonic() - hit[0]) < _CTX_TTL_SEC:
            return hit[1]
        res = await _canonical_report_context_uncached(s, e, source)
        # Hesaplama sırasında sipariş girdiyse sürüm değişmiştir → girdi doğar doğmaz
        # geçersiz sayılır (bayat değer önbellekte kalmaz).
        _CTX_CACHE[key] = (_time.monotonic(), res, _gen)
        if len(_CTX_CACHE) > 64:   # sınırsız büyümesin
            for k in sorted(_CTX_CACHE, key=lambda k: _CTX_CACHE[k][0])[:32]:
                _CTX_CACHE.pop(k, None); _CTX_LOCKS.pop(k, None)
        return res


async def _canonical_report_context_uncached(s: str, e: str, source: Optional[str] = None) -> tuple:
    """Load the one canonical order population used by every sales summary block.

    Cards, channel rows and payment rows used to build their date population with
    three slightly different pipelines.  On legacy marketplace/site copies this
    allowed one block to select a Site document while another selected its
    Trendyol twin.  Returning the exact same documents and return maps makes such
    a contradiction impossible.
    """
    clauses = [effective_order_date_match(s, e)]
    sc = _source_cond(source)
    if sc:
        clauses.append(sc)
    pipeline = [
        {"$match": merge_match({"$and": clauses})},
        *canonical_order_stages(),
        {"$project": {
            "_id": 0, "id": 1, "order_number": 1, "status": 1, "total": 1,
            "items.quantity": 1, "partial_cancel_amount": 1,
            "partial_cancel_units": 1, "partial_cancel_total_scope": 1,
            "platform": 1, "marketplace": 1,
            "payment_method": 1, "marketplace_order_date": 1, "created_at": 1,
            "shipping_address.city": 1, "shipping_address.district": 1,
            "attribution.source": 1, "attribution.channel": 1,
            # ödenmemiş site iptali süzgeci + 0 TL iptal tutarı (kalemlerden) için
            "payment_status": 1, "paid_at": 1, "paid_with_gift_card_only": 1,
            "items.price": 1, "items.unit_price": 1,
        }},
    ]
    import time as _t
    _t0 = _t.monotonic()
    orders = []
    async for order in db.orders.aggregate(pipeline, allowDiskUse=True):
        # Hiç ödenmemiş site siparişi (kart/havale; sonradan iptal/auto-cancel) satış
        # DEĞİLDİR: brüte ve iptal kartlarına girmez. Pazaryeri/kapıda ödeme etkilenmez.
        if _is_unpaid_site_cancel(order):
            continue
        # Hepsiburada iptalleri 0 TL içe aktarılabiliyor → tutar kalemlerden (rapor belleği).
        orders.append(_fill_cancel_total(order))
    _t1 = _t.monotonic()
    onums = list({str(o.get("order_number")) for o in orders if o.get("order_number")})
    oids = list({str(o.get("id")) for o in orders if o.get("id")})
    closed, open_ = await _split_maps(onums, oids)
    # AÇIK İADE ≠ KESİNLEŞMİŞ İADE: numaralı gider pusulası kesilmiş siparişin pusulada
    # olan kısmı artık 'Açık İade' değildir (iade kartında zaten sayılıyor). Açık talep
    # tutarı/adedi pusula kadar düşülür; statü yedeği (return_requested ama talep kaydı
    # yok) pusulalı siparişe HİÇ uygulanmaz. İşaret open_ içinde taşınır → _bucket_orders
    # imzası (reports_v2 vb. doğrudan çağıranlar) değişmez.
    try:
        _vt = await _voucher_totals(onums)
    except Exception:
        _vt = {}
    for _on, _v in _vt.items():
        _op = open_.get(_on)
        if _op:
            open_[_on] = {"amount": round(max(0.0, float(_op.get("amount") or 0) - _v["rev"]), 2),
                          "qty": (max(0, int(_op.get("qty") or 0) - int(_v["units"]))
                                  if _v.get("has_lines") else int(_op.get("qty") or 0)),
                          "_pusula": dict(_v)}
            if not _v.get("has_lines") and open_[_on]["amount"] <= 0.005:
                open_[_on]["qty"] = 0
        else:
            open_[_on] = {"amount": 0.0, "qty": 0, "_pusula": dict(_v)}
    # ÖLÇÜM: rapor yavaşlığını tahminle değil rakamla takip edebilmek için son 20 yükleme
    # /health?diag=1 → report_perf altında görünür (kişisel veri YOK, yalnız süre/adet).
    try:
        REPORT_PERF.append({
            "aralik": f"{str(s)[:10]}..{str(e)[:10]}", "kaynak": source or "all",
            "siparis": len(orders),
            "sorgu_ms": int((_t1 - _t0) * 1000),
            "iade_haritasi_ms": int((_t.monotonic() - _t1) * 1000),
            "toplam_ms": int((_t.monotonic() - _t0) * 1000),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        del REPORT_PERF[:-20]
    except Exception:
        pass
    return orders, closed, open_


async def _returned_barcode_qty(order_numbers: list) -> dict:
    """{order_number: {barcode: iade_adedi}} — hangi KALEMİN kaç adedi iade edildi.

    products/top ve cancel-return-products eskiden iade statüsündeki siparişin TÜM
    kalemlerini iade sayıyordu; 3 ürünlük siparişten 1 ürün iade edilse ürün raporunda
    3 iade + 0 net satış görünüyordu. Bu harita ile yalnız gerçekten iade edilen kalem
    İade'ye yazılır, kalanlar satışa geri döner. Kalem bilgisi YOKSA sipariş için hiç
    kayıt dönmez; çağıran taraf yalnız kesin tam-iade durumunda tüm siparişi iade sayabilir.
    """
    out: dict = {}
    if not order_numbers:
        return out

    def _put(onum: str, bc: str, q: int):
        # DENETİM O7: barkod anahtarları İKİ tarafta da str().strip() ile normalize edilir —
        # kaynak barkodlarındaki boşluk farkı iade satırlarını 0'a düşürüyordu.
        onum = str(onum or "").strip()
        bc = str(bc or "").strip()
        if not onum or not bc or q <= 0:
            return
        d = out.setdefault(onum, {})
        d[bc] = d.get(bc, 0) + int(q)

    for i in range(0, len(order_numbers), 5000):
        chunk = order_numbers[i:i + 5000]
        # Claim'in genel statüsü kullanılamaz: aynı claim içinde bir kalem Accepted,
        # diğeri Created/Rejected olabilir. Trendyol raporuyla eşleşmek için ham
        # claimItems içindeki HER kalemin kendi statüsünü say.
        _seen_claim_items: set[str] = set()
        async for c in db.trendyol_claims.find(
                {"order_number": {"$in": chunk}, "claim_type": {"$ne": "CANCEL"}},
                {"_id": 0, "claim_id": 1, "order_number": 1, "claim_status": 1,
                 "items": 1, "raw_data.items": 1}):
            for it in accepted_claim_items(c):
                if it["key"] in _seen_claim_items:
                    continue
                _seen_claim_items.add(it["key"])
                _put(c.get("order_number"), it.get("barcode"), int(it.get("quantity") or 0))
        async for r in db.customer_returns.find(
                {"order_number": {"$in": chunk}},
                {"_id": 0, "order_number": 1, "status": 1, "approval": 1,
                 "approved_items": 1, "items": 1}):
            _rst = str(r.get("status") or "").lower()
            if _rst in ("cancelled", "canceled", "rejected", "return_rejected"):
                continue
            _approved = r.get("approved_items") or []
            _approval_at = str((r.get("approval") or {}).get("at") or "")
            if not _approved and not _approval_at and _rst not in (
                    "approved", "return_approved", "returned", "refunded", "completed", "complete"):
                continue
            for it in (_approved or r.get("items") or []):
                _put(r.get("order_number"),
                     (it or {}).get("barcode") or (it or {}).get("sku"),
                     max(1, int((it or {}).get("quantity") or 1)))
    return out


@router.get("/sales-breakdown")
async def sales_breakdown(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Ciro kırılımı — 4 kademe + adet + açık-iade projeksiyonu:
      ① included = İptal + İade DAHİL toplam (net + iptal + iade)
      ② cancels  = iptal edilen tutar (kaybedilen)
      ③ returns  = iade edilen tutar — KISMİ iadede yalnız iade edilen ürünlerin payı
      ④ net      = elimizde kalan net ciro (kısmi iadede kalan ürünler burada kalır)
      ⑤ pending_returns = henüz onaylanmamış iade talepleri; onaylanırsa net'ten düşecek
      ⑥ projected_net    = net_kohort − ⑤ (açık iadeler onaylanırsa oluşacak net;
                           Net kartıyla aynı taban)

    Her kovada `units` = ÜRÜN ADEDİ (kalem adetleri toplamı) — pazaryeri raporlarıyla
    (Trendyol 'Brüt Satış Adedi') aynı birim. `orders` = sipariş sayısı.
    Tarih aralığı TR yerel gün, kaynak filtreli."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    legacy = _bucket_orders(orders, closed, open_)

    # DÖNEM MUHASEBESİ (kullanıcı kararı): iptal ve iade, İŞLEMİN KESİNLEŞTİĞİ döneme
    # yazılır — iptal için iptal tarihi, iade için GİDER PUSULASI tarihi ve tutarı.
    # Ciro ise siparişin verildiği dönemde kalır. Net = Ciro − İptal − İade.
    # İşlem satırları BİR KEZ, bugüne kadar okunur (önbellekli); dönem ve kohort
    # dilimleri bellekte kesilir.
    _hi = await _cohort_until_rule(e)
    _rows = await _action_rows(s, _hi)
    act = _slice_actions(_rows, s, e, source)
    cancels, returns = _sum_actions(act["cancels"]), _sum_actions(act["returns"])
    included = legacy["included"]          # dönemde verilen siparişlerin brütü
    net = {
        "revenue": round(included["revenue"] - cancels["revenue"] - returns["revenue"], 2),
        "orders": max(0, included["orders"] - cancels["orders"] - returns["orders"]),
        "units": max(0, included["units"] - cancels["units"] - returns["units"]),
    }

    # ── KOHORT NET — "BU DÖNEMİN SATIŞININ net'i" ────────────────────────────
    # Dönem net'i (yukarıdaki `net`) bu dönemde kesinleşen TÜM iptal/iadeleri düşer;
    # bunların bir kısmı ÖNCEKİ ayların siparişlerine aittir ve bu dönemin satışıyla
    # ilgisi yoktur. Pazaryeri panelleri (Trendyol vb.) iadeyi siparişin SATILDIĞI
    # aydan düşer. İki rakam farklı soruların cevabıdır; kıyas yapılabilsin diye
    # ikisi birden döner. `onceki_donem` alanı ikisi arasındaki köprüdür.
    # Üyelik: bu dönemin sipariş kümesi (grafik/ödeme/kanal tablosu ile AYNI).
    _members = _cohort_members(orders)
    _mkey = lambda r: _cohort_member_key(r, _members)  # noqa: E731
    # Köprü (onceki_donem) DÖNEM penceresinden: bu dönemde kesinleşen, önceki siparişler.
    _coh = _slice_actions(_rows, s, e, source, keyfn=_mkey)
    _c_onc = _coh["cancels"].get("onceki") or {"revenue": 0.0, "orders": 0, "units": 0}
    _r_onc = _coh["returns"].get("onceki") or {"revenue": 0.0, "orders": 0, "units": 0}
    # KOHORT PENCERESİ = BUGÜNE KADAR. Eskiden yalnız seçili aralığın SONUNA kadar
    # kesinleşenler düşülüyordu: geçmiş bir ay görüntülenince o ayın siparişlerinin
    # sonradan kesilen pusulaları/iptalleri hiç görünmüyordu (Haziran 'Sadece İadeler'
    # 0, Ağustos net şişik). Artık bu dönemin siparişlerinin BUGÜNE KADAR kesinleşmiş
    # tüm iptal/iadeleri düşülür (Trendyol paneli de böyle).
    _coh_ext = _slice_actions(_rows, s, e, source, keyfn=_mkey, at_until=_hi)
    # KARTLAR TEK KURALDA: "bu dönemin siparişlerinden" iptal ve iade. Brüt − bunlar = net
    # birebir kapanır (Kadir: "1874 sipariş, 160 iptal, 464 iade — net nasıl 1488?" —
    # iptal/iade kartları dönem, net kartı kohort temelindeydi; toplam kapanmıyordu).
    _bos = {"revenue": 0.0, "orders": 0, "units": 0, "partial_orders": 0}
    _c_bu = _coh_ext["cancels"].get("bu_donem") or dict(_bos)
    _r_bu = _coh_ext["returns"].get("bu_donem") or dict(_bos)
    _c_bu_d = _coh["cancels"].get("bu_donem") or dict(_bos)
    _r_bu_d = _coh["returns"].get("bu_donem") or dict(_bos)
    net_kohort = {
        "revenue": round(included["revenue"] - float(_c_bu["revenue"] or 0)
                         - float(_r_bu["revenue"] or 0), 2),
        "orders": max(0, included["orders"] - int(_c_bu["orders"] or 0)
                      - int(_r_bu["orders"] or 0)),
        "units": max(0, included["units"] - int(_c_bu["units"] or 0)
                     - int(_r_bu["units"] or 0)),
        # Ortalama Sepet paydası: kısmi iade/iptalli siparişin KALAN tutarı net
        # cirodadır → o sipariş de paydada. Yalnız TAMAMEN iptal/iade edilenler düşer.
        "aov_orders": max(0, included["orders"]
                          - (int(_c_bu["orders"] or 0) - int(_c_bu.get("partial_orders") or 0))
                          - (int(_r_bu["orders"] or 0) - int(_r_bu.get("partial_orders") or 0))),
    }
    # AÇIK İADE PROJEKSİYONU kohort tabanında: net_kohort − açık iade (eskiden eski
    # statü-bazlı net'ten türüyordu ve Net kartıyla çelişiyordu).
    _pend = legacy["pending_returns"]
    projected_net = {
        "revenue": round(net_kohort["revenue"] - float(_pend["revenue"] or 0), 2),
        "orders": max(0, net_kohort["orders"] - int(_pend["orders"] or 0)),
        "units": max(0, net_kohort["units"] - int(_pend["units"] or 0)),
    }

    def _fark(a: dict, b: dict) -> dict:
        return {"revenue": round(float(a.get("revenue") or 0) - float(b.get("revenue") or 0), 2),
                "orders": max(0, int(a.get("orders") or 0) - int(b.get("orders") or 0)),
                "units": max(0, int(a.get("units") or 0) - int(b.get("units") or 0))}

    # Siparişi bulunamayan (yetim) iade belgeleri — dönemde kesilen; hiçbir dönemin
    # satışına yazılamaz, kanalı belgeden çıkarılamayanlar 'Bilinmeyen'.
    _orph = [r for r in (_rows.get("returns") or []) if r.get("orphan")]
    _orph_sum = _side_rows_summary(_orph, s, e, source)
    _orph_unknown = _side_rows_summary(
        [r for r in _orph if r.get("ch") == _UNKNOWN_CH], s, e, source)
    return {
        **legacy,
        "included": included,
        "cancels": cancels,
        "returns": returns,
        "net": net,
        "net_kohort": net_kohort,
        "projected_net": projected_net,
        "cancels_kohort": {"revenue": round(float(_c_bu.get("revenue") or 0), 2),
                           "orders": int(_c_bu.get("orders") or 0),
                           "units": int(_c_bu.get("units") or 0),
                           "partial_orders": int(_c_bu.get("partial_orders") or 0),
                           "docs": int(_c_bu.get("docs") or 0)},
        "returns_kohort": {"revenue": round(float(_r_bu.get("revenue") or 0), 2),
                           "orders": int(_r_bu.get("orders") or 0),
                           "units": int(_r_bu.get("units") or 0),
                           "partial_orders": int(_r_bu.get("partial_orders") or 0),
                           "docs": int(_r_bu.get("docs") or 0)},
        # Kohortun ne kadarı seçili dönem BİTTİKTEN SONRA kesinleşti (etiket/köprü).
        "donem_sonrasi_kesinlesen": {"iptal": _fark(_c_bu, _c_bu_d),
                                     "iade": _fark(_r_bu, _r_bu_d)},
        "kohort_islem_bitis": _hi,
        # Aynı siparişe aynı kalemlerle tekrar kesilmiş pusulalar rapordan elendi
        # (dönem + kaynak süzgeçli).
        "mukerrer_elenen": _side_rows_summary(_rows.get("mukerrer_rows"), s, e, source),
        # Numaralı pusulası olduğu için düşülen/küçültülen iptaller (aynı sipariş hem
        # iptal hem iade sayılmasın) — dönem + kaynak süzgeçli.
        "iptal_iade_cakisma": _side_rows_summary(_rows.get("cakisma_rows"), s, e, source),
        "eslesmeyen_belge": {**_orph_sum, "bilinmeyen_kanal": _orph_unknown,
                             "not": ("Bu dönemde kesilen ama siparişi veritabanında "
                                     "bulunamayan iade belgeleri. Kanal belgenin kendi "
                                     "kaynağından çıkarılır; çıkarılamayan 'Bilinmeyen' "
                                     "sayılır (Site'a yazılmaz).")},
        "onceki_donem": {
            "iade": {"revenue": round(float(_r_onc["revenue"] or 0), 2),
                     "orders": int(_r_onc["orders"] or 0),
                     "units": int(_r_onc["units"] or 0)},
            "iptal": {"revenue": round(float(_c_onc["revenue"] or 0), 2),
                      "orders": int(_c_onc["orders"] or 0),
                      "units": int(_c_onc["units"] or 0)},
            "not": ("Bu dönemde kesinleşen ama ÖNCEKİ ayların siparişlerine ait "
                    "iptal/iade. Dönem net'ini düşürür, bu dönemin satışıyla ilgisi "
                    "yoktur. Pazaryeri paneliyle kıyaslarken bu kadarı geri eklenir."),
        },
        "basis": {
            "ciro": "siparis_tarihi",
            "iptal": "iptalin_kesinlestigi_tarih",
            "iade": "gider_pusulasi_tarihi",
            "iade_tutari": "gider_pusulasi_net_eksi_vade_farki",
            "iade_adedi": "yalniz_urun_satirlari",
            "kohort": "donem_siparisleri_bugune_kadar_kesinlesen_iptal_iade",
            "siparis_sayisi": "tekil_siparis_no",
        },
        # Eski (sipariş-tarihli, kalem bazlı) hesap kıyas için saklanıyor.
        "legacy_order_dated": {k: legacy.get(k) for k in
                               ("included", "cancels", "returns", "net", "pending_returns")},
    }



def _tr_lower(v) -> str:
    """Türkçe küçük harf: 'İ'→'i', 'I'→'ı' (tarayıcının toLocaleLowerCase("tr") karşılığı).
    str.casefold() Türkçe kuralını bilmez: 'İ'yi 'i̇' (birleşik nokta) yapıyor, 'I'yı 'i'."""
    return str(v or "").replace("İ", "i").replace("I", "ı").lower()


_TR_ALPHABET = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"
_TR_ALPHA_IDX = {ch: i for i, ch in enumerate(_TR_ALPHABET)}


def _tr_collate(v) -> tuple:
    """Metin sıralama anahtarı — ekrandaki localeCompare(..., "tr") yaklaşığı:
    büyük/küçük harf duyarsız, Türkçe alfabe sırası (ç c'den, ğ g'den, ı i'den, ö o'dan,
    ş s'den, ü u'dan sonra); boşluk/noktalama < rakam < harf."""
    import unicodedata as _ud_c
    out = []
    for ch in _tr_lower(v):
        if ch in _TR_ALPHA_IDX:
            out.append((3, _TR_ALPHA_IDX[ch], 0))
        elif ch.isdigit():
            out.append((2, ord(ch), 0))
        elif ch.isalpha():
            base = _ud_c.normalize("NFD", ch)[0]
            out.append((3, _TR_ALPHA_IDX.get(base, 100), ord(ch)))
        else:
            out.append((1, ord(ch), 0))
    return tuple(out)


def _row_velocity(row: dict) -> dict:
    """Satırın gösterilen hızı — platform süzgecinde TÜM KANAL hızı (velocity_all),
    aksi halde velocity. Ekrandaki velOf() ile aynı kural."""
    return (row or {}).get("velocity_all") or (row or {}).get("velocity") or {}


def _row_has_platform(row: dict, platform: str) -> bool:
    """Ekrandaki productReportScope(p, platform).hasPlatformData karşılığı."""
    want = str(platform or "").strip().lower()
    if want in ("site", "meluni", "web", "kendi"):
        want = "site"
    for x in ((row or {}).get("platform_metrics") or []) + \
             ((row or {}).get("platform_breakdown") or []) + \
             ((row or {}).get("cancel_return_by_platform") or []):
        if str((x or {}).get("platform") or "").strip().lower() == want:
            return True
    return False


@router.get("/products/export-xlsx")
async def products_export_xlsx(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    size: Optional[str] = Query(None),
    season: Optional[str] = Query(None),
    velocity: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    # GÖRSELLİ RAPOR (kullanıcı isteği): with_images=1 → her satırın başına ürün fotoğrafı.
    with_images: Optional[str] = None,
    img_size: Optional[str] = "medium",
    # İvme (_mom) sıralaması için ekranın kullandığı taban pencere başlangıcı (≈90 gün).
    base_start_date: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Ürün raporunun Excel çıktısı — ekrandaki listeyle aynı veri (tüm ürünler,
    iptal/iade kolonları dahil). Kolon sıralamayı Excel içinde yapabilirsiniz."""
    # ACİL ANAHTAR: freeze_exports açıkken toplu veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off as _sw_off, emit_security_event as _sec_emit
    await _sw_off("freeze_exports")
    import openpyxl
    from io import BytesIO as _BytesIO
    from fastapi.responses import Response as _Response
    data = await top_products(limit=5000, start_date=start_date, end_date=end_date,
                              source=source, current_user=current_user)
    _want_img = str(with_images or "").lower() in ("1", "true", "evet", "yes", "on")
    _isize = str(img_size or "medium").lower()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ürün Raporu"
    _source_key = str(source or "all").strip().lower()
    _scope_label = {
        "all": "Tüm Platformlar", "": "Tüm Platformlar", "site": "Site",
        "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu",
        "n11": "n11", "amazon": "Amazon",
    }.get(_source_key, _source_key.title())
    _off = 1 if _want_img else 0            # görsel sütunu eklendiyse diğerleri 1 sağa kayar
    ws.append((["Görsel"] if _want_img else []) +
              ["Ürün", "Sezon", "Brüt Satış Adedi", "İptal Adet", "İade Adet",
               "Net Satış Adedi", "Brüt Ciro (TL)", "İndirim (TL)", "Net Ciro (TL)",
               f"İade % ({_scope_label}: İade/Brüt)", "İade % (İptal Hariç)", "Sipariş",
               "Güncel Stok", "Kapsama (Hafta)", "RPT Durumu", "En Çok Satan Beden",
               "En Çok Satan Platform", "Haftalık Hız", "Platform İptal/İade Detay"])
    # Satış hızı: renkli hücre (yeşil/sarı/kırmızı) + etiket — panelle birebir aynı kodlama
    from openpyxl.styles import PatternFill
    _VEL_FILL = {"green": PatternFill("solid", fgColor="C6EFCE"),
                 "yellow": PatternFill("solid", fgColor="FFEB9C"),
                 "red": PatternFill("solid", fgColor="FFC7CE")}
    _VEL_LABEL = {"green": "Hızlı", "yellow": "Orta", "red": "Yavaş"}
    export_rows = list(data.get("items", []))
    # EKRANLA AYNI SATIRLAR: platform seçiliyken ekran o platformda verisi olmayan
    # (satışsız katalog) satırları gizler (productReportScope.hasPlatformData). Excel
    # eskiden bunları da yazıyordu (ör. 323 satır ↔ ekranda 219).
    if _source_cond(source):
        export_rows = [r for r in export_rows if _row_has_platform(r, _source_key)]
    if q and q.strip():
        # Türkçe küçük harf (İ→i, I→ı) — ekrandaki toLocaleLowerCase("tr") ile aynı.
        needle = _tr_lower(q.strip())
        export_rows = [r for r in export_rows if needle in _tr_lower(r.get("name"))]
    if size:
        _szn = _norm_size(size)
        export_rows = [r for r in export_rows
                       if any(x.get("size") == _szn for x in r.get("size_breakdown") or [])]
    if season:
        export_rows = [r for r in export_rows if r.get("season") == season]
    if velocity:
        export_rows = [r for r in export_rows if _row_velocity(r).get("code") == velocity]
    # API bu çağrıda zaten seçili source'a daraltılmıştır. Ekrandaki oran ve Excel
    # aynı görünür pay/paydayı kullanır: iade / (net + iptal + iade).
    for row in export_rows:
        gross = int(row.get("gross_qty") or 0)
        _raw_pct = (int(row.get("return_qty") or 0) / gross * 100) if gross else 0.0
        row["_scope_return_pct"] = round(_raw_pct, 2)
        row["_scope_return_pct_raw"] = _raw_pct
        _wr0 = _row_velocity(row).get("weekly_rate")
        row["_vel_rate"] = _wr0
        _st0 = row.get("current_stock")
        # Kapsama — ekranla aynı: stok biliniyor ve hız > 0 ise stok / hız (yuvarlamasız)
        row["_cover_raw"] = (_st0 / float(_wr0)) if (_st0 is not None and _wr0 and float(_wr0) > 0) else None
    # İvme (_mom) — ekranla aynı: şimdiki hız − taban penceredeki hız (≈90 gün).
    if sort_by == "_mom":
        _prev_map: dict = {}
        if base_start_date:
            try:
                _base = await top_products(limit=5000, start_date=base_start_date,
                                           end_date=end_date, source=source,
                                           include_all_velocity=False,
                                           current_user=current_user)
                for _b in _base.get("items", []):
                    if _b.get("product_id"):
                        _prev_map[_b["product_id"]] = _row_velocity(_b).get("weekly_rate") or 0
            except Exception as _be:
                logger.warning(f"[excel] ivme tabanı hesaplanamadı: {_be}")
        for row in export_rows:
            _wr1 = float(row.get("_vel_rate") or 0)
            _pv = _prev_map.get(row.get("product_id"))
            row["_mom_raw"] = (_wr1 - float(_pv)) if (_pv is not None and (_wr1 > 0 or _pv > 0)) else None
    # Paneldeki sıralamayı Excel'e de taşı — ekranın karşılaştırıcısıyla BİREBİR:
    #   • metin alanları Türkçe alfabe sırası (localeCompare "tr"),
    #   • Kapsama ve İvme'de hesaplanamayan (boş) satırlar iki yönde de EN SONA,
    #   • diğer sayısal alanlarda boş değer −1 sayılır (ekrandaki `?? -1`).
    # Bilinmeyen anahtarda mevcut kanonik (ciro) sıra korunur.
    _sort_fields = {
        "name": "name", "revenue": "revenue", "qty": "qty", "_gross": "gross_qty",
        "cancel_qty": "cancel_qty", "return_qty": "return_qty",
        "_retpct": "return_rate_excluding_cancels_pct",
        "_tyretpct": "trendyol_return_rate_pct",
        "_scopeReturnPct": "_scope_return_pct_raw", "current_stock": "current_stock",
        "best_size": "best_size", "top_platform": "top_platform", "season": "season",
        "velocity": "_vel_rate", "_cover": "_cover_raw", "_mom": "_mom_raw",
    }
    _field = _sort_fields.get(sort_by or "")
    if _field == "top_platform" and _source_cond(source):
        _field = None     # ekranda platform seçiliyken bu sütun her satırda aynı → sıra değişmez
    if _field:
        _reverse = sort_dir == "desc"
        if _field in {"name", "best_size", "top_platform", "season"}:
            export_rows = sorted(export_rows, key=lambda row: _tr_collate(row.get(_field)),
                                 reverse=_reverse)
        elif _field in {"_cover_raw", "_mom_raw"}:
            _present = [row for row in export_rows if row.get(_field) is not None]
            _missing = [row for row in export_rows if row.get(_field) is None]
            _present.sort(key=lambda row: float(row.get(_field)), reverse=_reverse)
            export_rows = _present + _missing
        else:
            export_rows = sorted(
                export_rows,
                key=lambda row: (-1.0 if row.get(_field) is None else _fnum(row.get(_field))),
                reverse=_reverse)
    for r in export_rows:
        _crd = "; ".join(f"{x['platform']}: iptal {x['cancel']} / iade {x['return']}"
                         for x in (r.get("cancel_return_by_platform") or []))
        _vel = _row_velocity(r)
        _vcode = _vel.get("code") or ""
        _stok = r.get("current_stock")
        _cov_raw = r.get("_cover_raw")
        _cover = round(_cov_raw, 1) if _cov_raw is not None else ""
        # RPT (yeniden üretim) durumu — panelle AYNI eşikler ve AYNI (yuvarlanmamış)
        # değer: ≤4 hf kritik (RPT AÇ), ≥26 hf aşırı stok, arası normal. Eskiden eşik
        # yuvarlanmış değere bakıyordu; 4,0x haftada Excel "RPT AÇ", ekran normal diyordu.
        if _cov_raw is not None:
            _rpt = "RPT AÇ" if _cov_raw <= 4 else ("Aşırı Stok" if _cov_raw >= 26 else "Normal")
        else:
            _rpt = "Satışsız / izlenmiyor"
        ws.append((["" ] if _want_img else []) + [
            r.get("name"), r.get("season") or "", r.get("gross_qty", 0),
            r.get("cancel_qty", 0), r.get("return_qty", 0), r.get("qty", 0),
            r.get("gross_revenue", 0), r.get("discount_amount", 0), r.get("revenue", 0),
            r.get("_scope_return_pct", 0), r.get("return_rate_excluding_cancels_pct", 0),
            r.get("orders"), _stok, _cover, _rpt, r.get("best_size"),
            r.get("top_platform") or "—",
            f"{_VEL_LABEL.get(_vcode, '')} ({_vel.get('weekly_rate', 0)}/hafta)", _crd,
        ])
        # Satış hızı rengi Haftalık Hız sütunundadır.
        _fill = _VEL_FILL.get(_vcode)
        if _fill:
            ws.cell(row=ws.max_row, column=18 + _off).fill = _fill
        # RPT AÇ (kritik) hücresini kırmızı, Aşırı Stok'u sarı vurgula (Excel'de göze çarpsın).
        _rpt_fill = _VEL_FILL.get("red") if _rpt == "RPT AÇ" else (_VEL_FILL.get("yellow") if _rpt == "Aşırı Stok" else None)
        if _rpt_fill:
            ws.cell(row=ws.max_row, column=15 + _off).fill = _rpt_fill
    from openpyxl.utils import get_column_letter as _gcl
    _widths = [42, 10, 15, 10, 10, 15, 15, 14, 15, 18, 18, 10, 12, 14, 16, 16, 18, 18, 40]
    for _i, w in enumerate(_widths, start=1 + _off):
        ws.column_dimensions[_gcl(_i)].width = w

    # ── ÜRÜN GÖRSELLERİ ───────────────────────────────────────────────────────────
    import logging as _lg
    _rlog = _lg.getLogger(__name__)
    if _want_img and export_rows:
        try:
            import xlsx_images as _XI
            from openpyxl.styles import Alignment as _Al
            _pids = [str(r.get("product_id")) for r in export_rows if r.get("product_id")]
            _names = [str(r.get("name")) for r in export_rows if r.get("name")]
            _img_by_id, _img_by_name = {}, {}
            if _pids or _names:
                _q = {"$or": ([{"id": {"$in": _pids}}] if _pids else []) +
                             ([{"name": {"$in": _names[:2000]}}] if _names else [])}
                async for _p in db.products.find(_q, {"_id": 0, "id": 1, "name": 1, "images": 1,
                                                      "image": 1, "thumbnail": 1}):
                    _u = _XI.first_image_url(_p)
                    if not _u:
                        continue
                    _img_by_id[str(_p.get("id"))] = _u
                    _img_by_name.setdefault(str(_p.get("name") or ""), _u)
            _row_urls = [(_img_by_id.get(str(r.get("product_id") or ""))
                          or _img_by_name.get(str(r.get("name") or "")) or "")
                         for r in export_rows]
            _cache = await _XI.fetch_images([u for u in _row_urls if u], _isize)
            _ok = 0
            for _i, _u in enumerate(_row_urls, start=2):
                if _u and _cache.get(_u) and _XI.put_image(ws, _i, 1, _cache[_u], _isize):
                    _ok += 1
            ws.column_dimensions["A"].width = _XI.col_width_chars(_isize)
            for _r in range(2, len(export_rows) + 2):
                for _c in range(1, 20 + _off):
                    ws.cell(row=_r, column=_c).alignment = _Al(vertical="center", wrap_text=(_c != 1))
            _rlog.info(f"[excel] ürün raporu: {_ok}/{len(export_rows)} satıra görsel gömüldü")
        except Exception as _ie:
            _rlog.warning(f"[excel] ürün raporu görselleri atlandı: {_ie}")

    # Sunum kalitesi: başlık şeridi + donmuş başlık + süzgeç
    try:
        from openpyxl.styles import Font as _Fn, PatternFill as _Pf, Alignment as _Al2
        for _c in range(1, 20 + _off):
            _hc = ws.cell(row=1, column=_c)
            _hc.fill = _Pf("solid", fgColor="1F2937")
            _hc.font = _Fn(bold=True, color="FFFFFF", size=11)
            _hc.alignment = _Al2(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "B2" if _want_img else "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = f"A1:{_gcl(19 + _off)}{ws.max_row}"
    except Exception:
        pass
    buf = _BytesIO()
    # DENETİM (injection F8): Excel/CSV formül enjeksiyonu — =+-@ ile başlayan hücreleri kaçır
    for _ws in wb.worksheets:
        for _row in _ws.iter_rows():
            for _c in _row:
                if isinstance(_c.value, str) and _c.value[:1] in ('=', '+', '-', '@', '\t', '\r'):
                    _c.value = "'" + _c.value
    wb.save(buf)
    # Güvenlik olayı: toplu dışa aktarım (denetim izi + süper admin uyarısı; asla istisna fırlatmaz).
    await _sec_emit("product_report_export", current_user, {"export": "urun-raporu.xlsx", "rows": len(export_rows)})
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
    # Platform süzgecinde tüm kanal hızını (velocity_all) da hesapla. Hızı kullanmayan
    # iç çağrılar (İade & İptal raporu) False geçer → gereksiz ikinci hesap yapılmaz.
    include_all_velocity: bool = Query(True, include_in_schema=False),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """ÜRÜN bazında satış raporu. Aynı ürünün farklı beden/renk kalemleri TEK satırda
    birleşir (mükerrer yok). Her ürün için: toplam adet + ciro + sipariş, güncel stok,
    EN ÇOK SATAN BEDEN ve PLATFORM DAĞILIMI döner. Frontend cirodan yükseğe sıralar/filtreler."""
    s, e = _iso_range(start_date, end_date, days_default=90)
    # Ürün tipi kategorisi: seçili ALT kategori (promo/sanal hariç) — tüm raporlarda tek kural.
    from .report_category import load_category_resolver
    _cat_of = await load_category_resolver()
    pipeline = [
        *_sales_stages(s, e, source),
        # Sipariş platformu: platform > marketplace > 'site'. Kalem fiyatları dağıtım
        # ağırlığıdır; net ürün ciroları siparişin tahsil edilen total'ına kapatılır.
        {"$addFields": {"_plat": _channel_expr(),
                        "_line_count": {"$size": {"$ifNull": ["$items", []]}},
                        "_partial_cancel_amount": {"$ifNull": ["$partial_cancel_amount", 0]},
                        "_partial_cancel_units": {"$ifNull": ["$partial_cancel_units", 0]},
                        "_partial_cancel_total_scope": {"$ifNull": ["$partial_cancel_total_scope", ""]},
                        "_sub": {"$reduce": {"input": {"$ifNull": ["$items", []]}, "initialValue": 0,
                                 "in": {"$add": ["$$value", {"$multiply": [
                                     {"$ifNull": ["$$this.quantity", 1]},
                                     {"$ifNull": ["$$this.price", {"$ifNull": ["$$this.unit_price", 0]}]}]}]}}},
                        # Satış raporunun tek parasal gerçeği orders.total'dır. Kısmi
                        # iptal tutarı satış breakdown'ında netten ayrıca düşülür.
                        "_order_net_total": {"$cond": [
                            {"$eq": [{"$toLower": {"$ifNull": ["$partial_cancel_total_scope", ""]}}, "active"]},
                            {"$max": [0, {"$ifNull": ["$total", 0]}]},
                            {"$max": [0, {"$subtract": [
                                {"$ifNull": ["$total", 0]},
                                {"$ifNull": ["$partial_cancel_amount", 0]},
                            ]}]},
                        ]}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$addFields": {
            "_nm": {"$ifNull": ["$items.name",
                     {"$ifNull": ["$items.product_name",
                       {"$ifNull": ["$items.productName", ""]}]}]},
            "_bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
            "_pid": {"$toString": {"$ifNull": ["$items.product_id", ""]}},
            "_sz": {"$toString": {"$ifNull": ["$items.size", ""]}},
            # Pazaryerinde unit_price=lineGrossAmount, price=indirimli satır fiyatıdır.
            # Site siparişlerinde unit_price yoksa price brüt baz olarak kullanılır.
            "_gross": {"$multiply": [
                {"$ifNull": ["$items.unit_price", {"$ifNull": ["$items.price", 0]}]},
                {"$ifNull": ["$items.quantity", 1]}]},
            "_line_net": {"$multiply": [
                {"$ifNull": ["$items.price", {"$ifNull": ["$items.unit_price", 0]}]},
                {"$ifNull": ["$items.quantity", 1]}]},
        }},
        {"$addFields": {"_net": {"$cond": [
            {"$gt": ["$_sub", 0]},
            # Kalem fiyatı yalnız DAĞITIM AĞIRLIĞIDIR. Gerçek net ciro siparişin
            # total alanıdır; bu oran legacy TY fiyat semantiği ve sipariş indirimi
            # farklarını tek seferde kapatır. Tüm kalemlerin toplamı order.total'a
            # (kısmi iptal varsa kalanına) kuruşuna eşittir.
            {"$multiply": ["$_line_net", {"$divide": ["$_order_net_total", "$_sub"]}]},
            "$_line_net",
        ]}}},
        # Kalem anahtarı: barkod > product_id > ad. Beden ve platform gruplamaya dahil edilir
        # ki EN ÇOK SATAN BEDEN + platform dağılımı çıkarılabilsin (parent birleştirme Python'da).
        {"$addFields": {"_key": {"$switch": {"branches": [
            {"case": {"$ne": ["$_bc", ""]}, "then": {"$concat": ["bc:", "$_bc"]}},
            {"case": {"$ne": ["$_pid", ""]}, "then": {"$concat": ["pid:", "$_pid"]}},
        ], "default": {"$concat": ["nm:", "$_nm"]}}}}},
        {"$group": {
            "_id": {"k": "$_key", "sz": "$_sz", "plat": "$_plat",
                    "on": {"$toString": {"$ifNull": ["$order_number", ""]}}},
            "name": {"$first": "$_nm"},
            "barcode": {"$first": "$_bc"},
            "pid": {"$first": "$_pid"},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            "gross_revenue": {"$sum": "$_gross"},
            "line_count": {"$first": "$_line_count"},
            "partial_cancel_amount": {"$first": "$_partial_cancel_amount"},
            "partial_cancel_units": {"$first": "$_partial_cancel_units"},
            # DENETİM O1: Ciro (Net) — sipariş indirimi düşülmüş kalem tutarı (_net).
            "revenue": {"$sum": "$_net"},
            # DENETİM O10: "Sipariş" = ürünün DISTINCT sipariş no'su. Eskiden $sum:1 idi →
            # aynı siparişte S+M alan ürün 2 sayılıyordu (order×beden×platform satırı sayımı).
            "onums": {"$addToSet": {"$ifNull": ["$order_number", ""]}},
        }},
    ]
    raw = []
    async for r in db.orders.aggregate(pipeline):
        raw.append(r)
    # Sipariş statüsü henüz iadeye çevrilmemiş olsa bile Accepted Trendyol claim / onaylı
    # site iadesi kanoniktir. Bu adetler net satıştan çıkarılıp iade sütununa taşınır.
    _valid_ret_bc = await _returned_barcode_qty(list({
        str(r.get("_id", {}).get("on") or "") for r in raw if r.get("_id", {}).get("on")
    }))
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
        # Silinmiş ürün kartları da GRUPLAMA için okunur ama katalog ürünü sayılmaz
        # (catalog_match=False, stok yok). Eskiden hiç okunmuyordu: silinmiş kartın satışı
        # varyant başına (Trendyol productCode) ayrı satırlara bölünüyor, aynı ürünün İADE
        # tarafı ise (ek sorgu silinmişleri de okuduğu için) başka satıra düşüyordu →
        # iade % ve adetler parçalanıyordu. Artık iki taraf da AYNI parent id'de birleşir.
        q = {"$or": _ors}
        async for p in db.products.find(q, {"_id": 0, "id": 1, "name": 1, "stock": 1, "variants": 1,
                                             "barcode": 1, "collection": 1, "created_at": 1, "stock_code": 1,
                                             "attributes": 1, "season": 1, "category_name": 1,
                                             "categories": 1, "category_ids": 1, "category_id": 1,
                                             "is_deleted": 1}):
            _deleted = p.get("is_deleted") is True
            variants = p.get("variants") or []
            stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(p.get("stock") or 0)
            # Beden bazında KALAN stok (açılır satırdaki mini tabloya)
            _sbs = {}
            for v in variants:
                _vs = _norm_size(v.get("size"))
                _sbs[_vs] = _sbs.get(_vs, 0) + int(v.get("stock") or 0)
            if _deleted:
                stock, _sbs = None, {}
            info = {"id": str(p.get("id")), "name": p.get("name") or "", "stock": stock,
                    "deleted": _deleted,
                    "stock_by_size": _sbs,
                    "category": _cat_of(p),
                    "collection": (p.get("collection") or "").strip(),
                    "created_at": p.get("created_at") or None,
                    "stock_code": (p.get("stock_code") or "").strip(),
                    # Sezon: önce ürün kartındaki zorunlu 'season' alanı; eskiler için öznitelik fallback
                    "season": (p.get("season") or "").strip()}  # TEK KAYNAK: ürün kartındaki Sezon alanı
            by_id[str(p.get("id"))] = info
            # Aynı barkod hem silinmiş hem canlı kartta olabilir (ürün yeniden açılmış):
            # canlı kart DAİMA kazanır, silinmiş kart yalnız boşluğu doldurur.
            _bc_list = ([str(p["barcode"])] if p.get("barcode") else []) + [
                str(v["barcode"]) for v in variants if v.get("barcode")]
            for _b in _bc_list:
                if _deleted:
                    by_bc.setdefault(_b, info)
                else:
                    by_bc[_b] = info

    def _pick_card(*cands) -> dict:
        """Aday kartlardan önce CANLI olanı, yoksa silinmişi, yoksa boş sözlüğü döndür."""
        for c in cands:
            if c and not c.get("deleted"):
                return c
        return next((c for c in cands if c), {})

    # PARENT ürün bazında birleştir → mükerrer beden/renk satırları tek ürün olur.
    merged = {}
    claim_cr_map: dict = {}
    for r in raw:
        pm = _pick_card(by_bc.get(r.get("barcode") or ""), by_id.get(r.get("pid") or ""))
        name = pm.get("name") or r.get("name") or "(isimsiz ürün)"
        # Grup anahtarı: çözülen parent id > pid > ad (isim NFC normalize edilerek NFD mükerreri de birleşsin)
        import unicodedata as _ud
        _nkey = _ud.normalize("NFC", name).strip().lower()
        gkey = pm.get("id") or (r.get("pid") or None) or f"nm:{_nkey}"
        m = merged.get(gkey)
        if not m:
            m = merged[gkey] = {
                "product_id": pm.get("id") or r.get("pid"), "name": name,
                # katalogda hâlâ var olan (SİLİNMEMİŞ) bir ürüne bağlandı mı?
                "_matched": bool(pm) and not pm.get("deleted"),
                "qty": 0, "revenue": 0.0, "gross_revenue": 0.0,
                "orders": 0, "_onums": set(),  # O10: distinct sipariş no
                "current_stock": pm.get("stock", None), "_sizes": {}, "_plats": {},
                "stock_by_size": pm.get("stock_by_size") or {},
                "category": pm.get("category") or "(Kategorisiz)",
                # Koleksiyon: stok kodu ön ekinden (fcfw/fcss) — filtre değerleri tek tip (FcFw/FCss)
                # kalsın diye önce kod ön eki; kod yoksa serbest metin collection alanına düşer.
                "collection": _collection_from_code(pm.get("stock_code"), r.get("barcode"), r.get("pid"))
                              or (pm.get("collection") or "").strip(),
                "created_at": pm.get("created_at"),
                "stock_code": pm.get("stock_code") or "",
                "season": pm.get("season") or "",
            }
        _q_gross = int(r["qty"])
        _rev_gross = float(r["revenue"])
        _catalog_gross = float(r.get("gross_revenue") or _rev_gross)
        _on = str(r.get("_id", {}).get("on") or "")
        _bc = str(r.get("barcode") or "").strip()
        _claim_q = 0
        _rb = _valid_ret_bc.get(_on)
        if _rb and _bc:
            _claim_q = min(_q_gross, int(_rb.get(_bc, 0)))
            _rb[_bc] = int(_rb.get(_bc, 0)) - _claim_q
        _q, _rev, _claim_q, _claim_rev = split_confirmed_return(
            _q_gross, _rev_gross, _claim_q)
        # Trendyol kısmi iptal mutabakatı order-level adet/tutar saklar. Siparişte tek
        # ürün kalemi varsa iptal edilen birim kesin olarak bu ürüne aittir; net satışı
        # azaltmadan (orders.items aktif paketi taşır) iptal ve brüt kolonuna eklenir.
        # Birden çok ürünlü siparişte barkod kanıtı olmadığı için tahmini dağıtım yapma.
        _pc_q = 0
        _pc_rev = 0.0
        if int(r.get("line_count") or 0) == 1:
            _pc_q = max(0, int(r.get("partial_cancel_units") or 0))
            _pc_rev = max(0.0, float(r.get("partial_cancel_amount") or 0))
        if _pc_q or _pc_rev:
            _cd = claim_cr_map.setdefault(gkey, {"cancel": 0, "return": 0,
                                                  "gross_revenue": 0.0,
                                                  "cancel_revenue": 0.0,
                                                  "return_revenue": 0.0,
                                                  "by_plat": {}, "by_size": {}})
            _cd["cancel"] += _pc_q
            _cd["cancel_revenue"] += _pc_rev
            _cd["gross_revenue"] += _pc_rev
            _cpl = (r["_id"].get("plat") or "site").strip().lower() or "site"
            _cp = _cd["by_plat"].setdefault(
                _cpl, {"cancel": 0, "return": 0, "cancel_total": 0.0, "return_total": 0.0})
            _cp["cancel"] += _pc_q
            _cp["cancel_total"] += _pc_rev
            _csz = _norm_size(r["_id"].get("sz"))
            _cs = _cd["by_size"].setdefault(_csz, {"cancel": 0, "return": 0})
            _cs["cancel"] += _pc_q
        if _claim_q > 0:
            _cd = claim_cr_map.setdefault(gkey, {"cancel": 0, "return": 0,
                                                  "gross_revenue": 0.0,
                                                  "cancel_revenue": 0.0,
                                                  "return_revenue": 0.0,
                                                  "by_plat": {}, "by_size": {}})
            _cd["return"] += _claim_q
            _cd["return_revenue"] += _claim_rev
            _cpl = (r["_id"].get("plat") or "site").strip().lower() or "site"
            _cp = _cd["by_plat"].setdefault(
                _cpl, {"cancel": 0, "return": 0, "cancel_total": 0.0, "return_total": 0.0})
            _cp["return"] += _claim_q
            _cp["return_total"] += _claim_rev
            _csz = _norm_size(r["_id"].get("sz"))
            _cs = _cd["by_size"].setdefault(_csz, {"cancel": 0, "return": 0})
            _cs["return"] += _claim_q
        m["qty"] += _q
        m["revenue"] += _rev
        # Aktif siparişte onaylı iade claim'i olsa da brüt satış ilk gerçekleşen
        # satışın tamamıdır; Trendyol Brüt Ciro tanımıyla aynı kalır.
        m["gross_revenue"] += _catalog_gross
        # DENETİM O10: sipariş sayısı DISTINCT order_number üzerinden — grup satırı sayımı değil.
        for _onv in (r.get("onums") or []):
            if _onv and _q > 0:
                m["_onums"].add(str(_onv))
        _sz = _norm_size(r["_id"].get("sz"))
        m["_sizes"][_sz] = m["_sizes"].get(_sz, 0) + _q
        _pl = (r["_id"].get("plat") or "site").strip().lower() or "site"
        # Platform kırılımı: adet + NET ciro (platform filtresinde satırı o platforma daraltmak için).
        _pv = m["_plats"].get(_pl) or {"qty": 0, "revenue": 0.0}
        _pv["qty"] += _q
        _pv["revenue"] += _rev
        m["_plats"][_pl] = _pv
    # Satış kalemini katalogla eşleştirememek finansal hareketi yok etmemelidir.
    # Eşleşmeyen/eski kartlar stok bilgisi olmadan görünür ve UI tarafından açıkça
    # işaretlenebilir; böylece ürün toplamı ana satış toplamından sessizce eksilmez.
    for m in merged.values():
        m["catalog_match"] = bool(m.pop("_matched", False))
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
    # Gerçek hafta sayısı (en az 1 gün = 1/7 hafta). Eskiden alt sınır 1 HAFTA idi:
    # Bugün/Dün gibi 7 günden kısa aralıkta "haftalık hız" = o günlerin ham adedi
    # çıkıyor, gerçek hızın 7/gün katı kadar düşük görünüyordu → kapsama şişiyor, RPT
    # uyarısı kaçıyor, ivme yanlışlıkla "Düşüyor" diyordu. Şimdi hız = adet / gün × 7.
    _weeks = max(1.0 / 7.0, _range_days / 7.0)

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
             "created_at": 1, "stock_code": 1, "attributes": 1, "season": 1,
             "category_name": 1, "categories": 1, "category_ids": 1, "category_id": 1}):
        if str(p.get("id")) in _seen_ids:
            continue
        variants = p.get("variants") or []
        stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(p.get("stock") or 0)
        _sbs0 = {}
        for v in variants:
            # Satış/iade tarafıyla AYNI beden anahtarı (aksi halde kısmi iadeden geri gelen
            # satış 'STD' satırına, stok 'Standart' satırına düşüp iki satır çıkıyordu).
            _vs = _norm_size(v.get("size"))
            _sbs0[_vs] = _sbs0.get(_vs, 0) + int(v.get("stock") or 0)
        merged[f"zero:{p.get('id')}"] = {
            "product_id": str(p.get("id")), "name": p.get("name") or "",
            "catalog_match": True,
            "qty": 0, "revenue": 0.0, "gross_revenue": 0.0,
            "orders": 0, "current_stock": stock,
            "stock_by_size": _sbs0,
            "category": _cat_of(p),
            "_sizes": {}, "_plats": {},
            "collection": _collection_from_code(p.get("stock_code")) or (p.get("collection") or "").strip(),
            "created_at": p.get("created_at"), "stock_code": (p.get("stock_code") or "").strip(),
            "season": (p.get("season") or "").strip(),  # TEK KAYNAK: ürün kartındaki Sezon alanı (öznitelik fallback kaldırıldı)
        }

    # İPTAL & İADE — ürün bazında, platform kırılımlı (aynı kalem-anahtar çözümüyle)
    _CR_CANCEL = ["cancelled", "cancel_refunded"]
    _CR_RETURN = ["return_requested", "return_approved", "return_in_transit",
                  "returned", "refunded", "partial_refunded"]
    _cr_clauses = [effective_order_date_match(s, e)]
    if source and _source_cond(source):
        _cr_clauses.append(_source_cond(source))
    _cr_pipe = [
        {"$match": merge_match({"$and": _cr_clauses})},
        *canonical_order_stages(),
        {"$match": {"status": {"$in": _CR_CANCEL + _CR_RETURN}}},
        {"$addFields": {"_plat": _channel_expr(),
                        "_kind": {"$cond": [{"$in": ["$status", _CR_CANCEL]}, "cancel", "return"]},
                        "_sub": {"$reduce": {"input": {"$ifNull": ["$items", []]}, "initialValue": 0,
                                 "in": {"$add": ["$$value", {"$multiply": [
                                     {"$ifNull": ["$$this.quantity", 1]},
                                     {"$ifNull": ["$$this.price", {"$ifNull": ["$$this.unit_price", 0]}]}]}]}}},
                        # Tam iptal siparişinde tüm total iptaldir; iade statüsündeki
                        # kısmi iptalde ise satış breakdown gibi iptal payı önce düşer.
                        "_order_net_total": {"$cond": [
                            {"$in": ["$status", _CR_CANCEL]},
                            {"$max": [0, {"$ifNull": ["$total", 0]}]},
                            {"$cond": [
                                {"$eq": [{"$toLower": {"$ifNull": ["$partial_cancel_total_scope", ""]}}, "active"]},
                                {"$max": [0, {"$ifNull": ["$total", 0]}]},
                                {"$max": [0, {"$subtract": [
                                    {"$ifNull": ["$total", 0]},
                                    {"$ifNull": ["$partial_cancel_amount", 0]},
                                ]}]},
                            ]},
                        ]}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$addFields": {"_line_net": {"$multiply": [
            {"$ifNull": ["$items.price", {"$ifNull": ["$items.unit_price", 0]}]},
            {"$ifNull": ["$items.quantity", 1]}]}}},
        {"$addFields": {"_allocated_net": {"$cond": [
            {"$gt": ["$_sub", 0]},
            {"$multiply": ["$_line_net", {"$divide": ["$_order_net_total", "$_sub"]}]},
            "$_line_net",
        ]}}},
        {"$group": {"_id": {"bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
                            "pid": {"$toString": {"$ifNull": ["$items.product_id", ""]}},
                            "nm": {"$ifNull": ["$items.name", {"$ifNull": ["$items.product_name", ""]}]},
                            "sz": {"$toString": {"$ifNull": ["$items.size", ""]}},
                            # Kısmi iade ayrıştırması sipariş bazında yapılır → grup anahtarına
                            # sipariş no eklendi (hangi kalemin iade edildiği siparişe bağlı).
                            "on": {"$toString": {"$ifNull": ["$order_number", ""]}},
                            "kind": "$_kind", "status": "$status", "plat": "$_plat"},
                    "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                    # Terminal siparişte de ürün payları aynı orders.total oranını
                    # kullanır. Böylece kısmi iadede keep_map'e geri eklenen tutar,
                    # aktif siparişlerdeki dağıtım tabanından sapmaz.
                    "rev": {"$sum": "$_allocated_net"},
                    "gross_rev": {"$sum": {"$multiply": [
                        {"$ifNull": ["$items.unit_price", {"$ifNull": ["$items.price", 0]}]},
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
        async for p in db.products.find(_q2, {"_id": 0, "id": 1, "name": 1, "barcode": 1,
                                              "variants.barcode": 1, "is_deleted": 1}):
            _inf = {"id": str(p.get("id")), "name": p.get("name") or "",
                    "deleted": p.get("is_deleted") is True}
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
    _cr_meta: dict = {}   # gk → ad/ürün id (satışı olmayan ürünün iptal/iadesi için satır açmak)
    keep_map: dict = {}   # kısmi iadede müşteride KALAN kalemler → satışa geri döner
    for r in _cr_rows:
        i = r["_id"]
        pm = _pick_card(by_bc.get(i.get("bc") or ""), by_id.get(i.get("pid") or ""))
        _cname = pm.get("name") or i.get("nm") or "(isimsiz ürün)"
        gk = pm.get("id") or (i.get("pid") or None) or f"nm:{_ud3.normalize('NFC', _cname).strip().lower()}"
        _cr_meta.setdefault(gk, {"name": _cname, "product_id": pm.get("id") or i.get("pid"),
                                 "matched": bool(pm) and not pm.get("deleted"),
                                 "plat": i.get("plat")})
        qty = int(r["qty"])
        rev = float(r.get("rev") or 0)
        gross_rev = float(r.get("gross_rev") or rev)
        keep_q, keep_r, keep_gross = 0, 0.0, 0.0
        if i["kind"] == "return":
            _rb = _ret_bc.get(i.get("on") or "")
            if _rb:  # kalem bilgisi VAR → yalnız onaylı/adetli kalemi böl.
                _bc = str(i.get("bc") or "").strip()  # DENETİM O7: barkod anahtarı iki tarafta da strip'li
                _allowed = int(_rb.get(_bc, 0))
                keep_q, keep_r, qty, rev = split_confirmed_return(qty, rev, _allowed)
                _, keep_gross, _, gross_rev = split_confirmed_return(
                    keep_q + qty, gross_rev, qty)
                _rb[_bc] = _allowed - qty              # aynı barkod başka satırda tekrar sayılmasın
            elif i.get("status") not in ("returned", "refunded"):
                # Açık talebi veya kalemi bilinmeyen kısmi iadeyi tam iade varsayma.
                keep_q, keep_r, keep_gross = qty, rev, gross_rev
                qty, rev, gross_rev = 0, 0.0, 0.0
        if keep_q > 0:
            k = keep_map.setdefault(gk, {"qty": 0, "revenue": 0.0, "gross_revenue": 0.0,
                                         "sizes": {}, "plats": {}})
            k["qty"] += keep_q
            k["revenue"] += keep_r
            k["gross_revenue"] += keep_gross
            _ksz = _norm_size(i.get("sz"))
            k["sizes"][_ksz] = k["sizes"].get(_ksz, 0) + keep_q
            _kpl = (i.get("plat") or "site")
            _kpv = k["plats"].get(_kpl) or {"qty": 0, "revenue": 0.0}
            _kpv["qty"] += keep_q
            _kpv["revenue"] += keep_r
            k["plats"][_kpl] = _kpv
        if qty <= 0:
            continue
        d = cr_map.setdefault(gk, {"cancel": 0, "return": 0, "gross_revenue": 0.0,
                                   "cancel_revenue": 0.0, "return_revenue": 0.0,
                                   "by_plat": {}, "by_size": {}})
        d[i["kind"]] += qty
        d["gross_revenue"] += gross_rev
        d[f"{i['kind']}_revenue"] += rev
        bp = d["by_plat"].setdefault(
            (i.get("plat") or "site"),
            {"cancel": 0, "return": 0, "cancel_total": 0.0, "return_total": 0.0})
        bp[i["kind"]] += qty
        bp[f"{i['kind']}_total"] += rev
        bs = d["by_size"].setdefault(_norm_size(i.get("sz")), {"cancel": 0, "return": 0})
        bs[i["kind"]] += qty

    # Statüsü aktif kalmış fakat claim/return kaynağında onaylı iadesi bulunan siparişler.
    for _gk, _src in claim_cr_map.items():
        _dst = cr_map.setdefault(_gk, {"cancel": 0, "return": 0, "gross_revenue": 0.0,
                                       "cancel_revenue": 0.0, "return_revenue": 0.0,
                                       "by_plat": {}, "by_size": {}})
        _dst["cancel"] += _src["cancel"]
        _dst["return"] += _src["return"]
        _dst["gross_revenue"] += float(_src.get("gross_revenue") or 0)
        _dst["cancel_revenue"] += float(_src.get("cancel_revenue") or 0)
        _dst["return_revenue"] += float(_src.get("return_revenue") or 0)
        for _pk, _pv in _src["by_plat"].items():
            _d = _dst["by_plat"].setdefault(
                _pk, {"cancel": 0, "return": 0, "cancel_total": 0.0, "return_total": 0.0})
            _d["cancel"] += _pv["cancel"]
            _d["return"] += _pv["return"]
            _d["cancel_total"] += float(_pv.get("cancel_total") or 0)
            _d["return_total"] += float(_pv.get("return_total") or 0)
        for _sk, _sv in _src["by_size"].items():
            _d = _dst["by_size"].setdefault(_sk, {"cancel": 0, "return": 0})
            _d["cancel"] += _sv["cancel"]
            _d["return"] += _sv["return"]

    # Satış tarafında satırı OLMAYAN ürünlerin (pasif/silinmiş ya da dönemdeki tüm
    # siparişi iptal/iade olmuş) iptal/iadesi ve kısmi iadeden müşteride kalan satışı
    # eskiden çıktıya hiç girmiyordu (döngü yalnız `merged` üzerinde) → İade & İptal
    # TOPLAM'ı ve ürün toplamları sessizce eksik kalıyordu. Bu anahtarlar qty=0 satırı alır.
    _looked = set()
    for _gk0, _m0 in merged.items():
        _looked.add(_gk0)
        if _m0.get("product_id"):
            _looked.add(_m0["product_id"])
    for _ok in sorted((set(cr_map) | set(keep_map)) - _looked, key=str):
        _meta = _cr_meta.get(_ok) or {}
        merged[_ok] = {
            "product_id": _meta.get("product_id"),
            "name": _meta.get("name") or "(isimsiz ürün)",
            "catalog_match": bool(_meta.get("matched")),
            "qty": 0, "revenue": 0.0, "gross_revenue": 0.0, "orders": 0,
            "current_stock": None, "stock_by_size": {}, "category": "(Kategorisiz)",
            "_sizes": {}, "_plats": {}, "collection": "", "created_at": None,
            "stock_code": "", "season": "",
        }

    out = []
    for gkey, m in merged.items():
        _sizes = sorted(m.pop("_sizes").items(), key=lambda x: -x[1])
        # _plats: {platform: {qty, revenue}} → adet'e göre sırala.
        _plats = sorted(m.pop("_plats").items(), key=lambda x: -x[1]["qty"])
        # DENETİM O10: distinct sipariş no seti → "orders" adedi (JSON'a set serileşmesin).
        if "_onums" in m:
            m["orders"] = len(m.pop("_onums"))
        _cr = cr_map.get(m.get("product_id") or gkey) or cr_map.get(gkey) or {}
        # Kısmi iadede müşteride kalan kalemler satış tarafına geri eklenir (satış
        # pipeline'ı iade statüsündeki siparişin TAMAMINI dışladığı için burada telafi).
        _kp = keep_map.get(m.get("product_id") or gkey) or keep_map.get(gkey)
        if _kp:
            m["qty"] = int(m.get("qty") or 0) + int(_kp["qty"])
            m["revenue"] = float(m.get("revenue") or 0) + float(_kp["revenue"])
            m["gross_revenue"] = float(m.get("gross_revenue") or 0) + float(_kp.get("gross_revenue") or 0)
            _sd = dict(_sizes)
            for _sk, _sv in _kp["sizes"].items():
                _sd[_sk] = _sd.get(_sk, 0) + _sv
            _sizes = sorted(_sd.items(), key=lambda x: -x[1])
            _pd = dict(_plats)
            for _pk, _pv in _kp["plats"].items():
                _cur = _pd.get(_pk) or {"qty": 0, "revenue": 0.0}
                _pd[_pk] = {"qty": _cur["qty"] + _pv["qty"], "revenue": _cur["revenue"] + _pv["revenue"]}
            _plats = sorted(_pd.items(), key=lambda x: -x[1]["qty"])
        _gross_revenue = float(m.get("gross_revenue") or 0) + float(_cr.get("gross_revenue") or 0)
        _all_net_before_returns = (float(m.get("revenue") or 0)
                                   + float(_cr.get("cancel_revenue") or 0)
                                   + float(_cr.get("return_revenue") or 0))
        _quantity_metrics = product_quantity_metrics(
            int(m.get("qty") or 0), int(_cr.get("cancel", 0)), int(_cr.get("return", 0)))
        _platform_metrics = product_platform_metrics(
            [{"platform": k, "qty": v["qty"], "revenue": v["revenue"]} for k, v in _plats],
            [{"platform": k, **v} for k, v in (_cr.get("by_plat") or {}).items()],
        )
        _trendyol_metrics = next(
            (row for row in _platform_metrics if row["platform"] == "trendyol"), None)
        out.append({
            **m,
            "revenue": round(m["revenue"], 2),
            "gross_revenue": round(_gross_revenue, 2),
            "discount_amount": round(max(0.0, _gross_revenue - _all_net_before_returns), 2),
            **_quantity_metrics,
            # Adı Trendyol olan oran yalnız Trendyol pay/paydasından hesaplanır.
            # Tüm-kanal operasyonel oran yukarıdaki return_rate_excluding_cancels_pct'dir.
            "trendyol_return_rate_pct": (
                _trendyol_metrics["trendyol_return_rate_pct"] if _trendyol_metrics else 0.0),
            "trendyol_gross_qty": int(_trendyol_metrics["gross_qty"] if _trendyol_metrics else 0),
            "trendyol_return_qty": int(_trendyol_metrics["return_qty"] if _trendyol_metrics else 0),
            "best_size": _sizes[0][0] if _sizes else "—",
            "size_breakdown": [{"size": k, "qty": v} for k, v in _sizes],
            # Satışı olmayan üründe "en çok satan platform" YOKTUR (eskiden 'site' yazıyordu).
            "top_platform": _plats[0][0] if _plats else "",
            "platform_breakdown": [{"platform": k, "qty": v["qty"], "revenue": round(v["revenue"], 2)}
                                   for k, v in _plats],
            "platform_metrics": _platform_metrics,
            "velocity": _velocity(int(m["qty"])),
            "cancel_qty": int(_cr.get("cancel", 0)),
            "return_qty": int(_cr.get("return", 0)),
            "cancel_total": round(float(_cr.get("cancel_revenue") or 0), 2),
            "return_total": round(float(_cr.get("return_revenue") or 0), 2),
            "cancel_return_by_platform": [
                {"platform": k, "cancel": v["cancel"], "return": v["return"],
                 "cancel_total": round(float(v.get("cancel_total") or 0), 2),
                 "return_total": round(float(v.get("return_total") or 0), 2)}
                for k, v in sorted((_cr.get("by_plat") or {}).items())],
            # Beden bazlı iptal/iade — açılır satırdaki Toplam/İptal/İade/Net kırılımı için
            "cancel_return_by_size": [
                {"size": k, "cancel": v["cancel"], "return": v["return"]}
                for k, v in sorted((_cr.get("by_size") or {}).items())],
        })
    # Platform süzgecinde stok TÜM kanalların ortak stoğudur; kapsama/tükenme/RPT'yi
    # yalnız o platformun hızına bölmek stoğu olduğundan uzun gösteriyordu. Bu yüzden
    # kaynak seçiliyken her satıra TÜM KANAL hızı (velocity_all) da eklenir; `velocity`
    # geriye uyum için platform hızı olarak kalır. (source=None iç çağrısı özyinelemez.)
    # (Python'dan doğrudan çağrıda varsayılan bir Query nesnesidir → True kabul edilir.)
    _want_all_vel = include_all_velocity if isinstance(include_all_velocity, bool) else True
    if _want_all_vel and _source_cond(source):   # boş koşul = tüm kaynaklar → ek hesap yok
        try:
            _all = await top_products(limit=5000, start_date=start_date, end_date=end_date,
                                      source=None, include_all_velocity=False,
                                      current_user=current_user)

            def _vk(row):
                return row.get("product_id") or f"nm:{str(row.get('name') or '').strip().lower()}"
            _vmap = {_vk(it): it.get("velocity") for it in _all.get("items", [])}
            for row in out:
                row["velocity_all"] = _vmap.get(_vk(row)) or _velocity(0)
        except Exception as _ve:
            logger.warning(f"[rapor] tüm kanal hızı hesaplanamadı: {_ve}")
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
async def get_profitability_config(current_user: dict = Depends(require_permission("reports.financial"))):
    """Kârlılık analizi gider oran/varsayımları (komisyon, reklam, hizmet bedeli, KDV, kurumlar vergisi)."""
    return {"config": await _profitability_config(), "defaults": _PROFIT_DEFAULTS}


@router.put("/profitability-config")
async def set_profitability_config(payload: dict, current_user: dict = Depends(require_permission("reports.financial"))):
    """Kârlılık gider varsayımlarını günceller (yalnız gönderilen alanlar birleşir)."""
    payload = {k: v for k, v in (payload or {}).items() if k in _PROFIT_DEFAULTS}
    await db.settings.update_one({"id": "profitability_config"}, {"$set": {"id": "profitability_config", **payload}}, upsert=True)
    return {"success": True, "config": await _profitability_config()}


@router.get("/profitability")
async def profitability(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_permission("reports.financial")),
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
    _CH_ALIAS = {"meluni": "site", "": "site", "web": "site", "admin_manual": "manual", "admin": "manual"}
    from collections import defaultdict as _dd

    # Ana satış gerçekliği ürün raporunun kanonik motorudur. Ayrı bir item pipeline'ı
    # kısmi iadeleri ve claim güncellemelerini kaçırdığı için aynı aralıkta büyük ciro
    # farkı üretiyordu. Buradaki qty/revenue doğrudan top_products ile birebirdir.
    if start_date and end_date:
        canonical_start, canonical_end = start_date, end_date
    else:
        today_tr = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        canonical_start = (today_tr - timedelta(days=29)).isoformat()
        canonical_end = today_tr.isoformat()
    canonical = await top_products(
        limit=5000, start_date=canonical_start, end_date=canonical_end,
        source=source, include_all_velocity=False, current_user=current_user,
    )
    product_rows = [row for row in canonical.get("items", [])
                    if int(row.get("qty") or 0) > 0 or float(row.get("revenue") or 0) > 0]
    product_ids = [str(row.get("product_id")) for row in product_rows if row.get("product_id")]

    manual_costs = {}
    async for cost in db.product_costs.find(
            {"product_id": {"$in": product_ids}}, {"_id": 0, "product_id": 1, "cost_price": 1}):
        manual_costs[str(cost.get("product_id"))] = float(cost.get("cost_price") or 0)
    unit_costs = {}
    async for product in db.products.find(
            {"id": {"$in": product_ids}},
            {"_id": 0, "id": 1, "purchase_price": 1, "cost_price": 1}):
        pid = str(product.get("id"))
        unit_costs[pid] = (float(manual_costs.get(pid) or 0)
                           or float(product.get("purchase_price") or 0)
                           or float(product.get("cost_price") or 0))
    # Kanal başına toplam kargo (net ciro payına göre kategorilere dağıtılır).
    cargo_by_ch = _dd(float)
    async for r in db.orders.aggregate([
        *_sales_stages(s, e, source),
        {"$addFields": {"_ch": _channel_expr()}},
        {"$group": {"_id": "$_ch", "shipping": {"$sum": {"$ifNull": ["$shipping_cost", 0]}},
                    "orders": {"$sum": 1}}},
    ]):
        cargo_by_ch[_CH_ALIAS.get((r["_id"] or "site"), r["_id"] or "site")] = {
            "shipping": float(r["shipping"] or 0), "orders": int(r["orders"])}

    # Kategori-kanal satırlarını kanonik ürün/platform kırılımından topla.
    grouped = _dd(lambda: {"qty": 0, "gross_revenue": 0.0, "revenue": 0.0, "cogs": 0.0})
    rev_by_ch = _dd(float)
    for product in product_rows:
        category = product.get("category") or "(Kategorisiz)"
        p_qty = int(product.get("qty") or 0)
        p_rev = float(product.get("revenue") or 0)
        p_gross = kept_gross_revenue(
            p_rev, float(product.get("gross_revenue") or 0),
            float(product.get("discount_amount") or 0))
        breakdown = reconciled_platform_breakdown(product)
        cost = float(unit_costs.get(str(product.get("product_id"))) or 0)
        for part in breakdown:
            qty = max(0, int(part.get("qty") or 0))
            rev = max(0.0, float(part.get("revenue") or 0))
            ch = _CH_ALIAS.get(str(part.get("platform") or "site").lower(),
                               str(part.get("platform") or "site").lower())
            share = (rev / p_rev) if p_rev > 0 else ((qty / p_qty) if p_qty > 0 else 0)
            dst = grouped[(category, ch)]
            dst["qty"] += qty
            dst["revenue"] += rev
            dst["gross_revenue"] += p_gross * share
            dst["cogs"] += qty * cost if cost > 0 else rev * float(cfg["cog_fallback_ratio"])
            rev_by_ch[ch] += rev
    rows = [{"category": category, "channel": ch, **values,
             "discount": max(0.0, values["gross_revenue"] - values["revenue"])}
            for (category, ch), values in grouped.items()]

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
        # Reklam: AYLIK bütçe girildiyse tarih aralığına orantıla (aylık × gün/30), yoksa dönem toplamı.
        _monthly = float((cfg.get("ad_spend_monthly") or {}).get(ch, 0.0))
        ad_total = round(_monthly * _days_range / 30.0, 2) if _monthly > 0 else float(cfg["ad_spend"].get(ch, 0.0))
        ad_alloc = ad_total * (rev / rev_by_ch[ch]) if rev_by_ch.get(ch) else 0.0
        # rev zaten indirim sonrası NET ürün cirosudur; indirimi burada tekrar düşme.
        operating = rev - cogs - commission - service_fee - cargo - ad_alloc
        # KDV (net ödenecek — katma değer üzerinden): (net ciro - maliyet) içindeki KDV.
        vat_payable = max(0.0, (rev - cogs)) * vat / (100.0 + vat)
        pre_tax = operating - vat_payable
        corporate_tax = max(0.0, pre_tax) * corp / 100.0
        net = pre_tax - corporate_tax
        out.append({
            "category": row["category"], "channel": ch, "qty": row["qty"],
            "gross_revenue": round(row["gross_revenue"], 2),
            "revenue": round(rev, 2), "discount": round(row["discount"], 2), "cogs": round(cogs, 2),
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
    totals = {k: _sum(k) for k in ("gross_revenue", "revenue", "discount", "cogs", "commission", "service_fee", "cargo",
                                    "ad_spend", "vat_payable", "corporate_tax", "net_profit")}
    # Headline değerleri doğrudan kanonik ürün satırlarından al; kategori bazında
    # kuruş yuvarlaması toplamı bir-iki kuruş oynatmasın.
    totals["revenue"] = round(sum(float(row.get("revenue") or 0) for row in product_rows), 2)
    totals["qty"] = sum(int(row.get("qty") or 0) for row in product_rows)
    totals["margin_pct"] = round(totals["net_profit"] / totals["revenue"] * 100.0, 1) if totals["revenue"] else 0.0
    return {"items": out, "totals": totals, "config": cfg}


@router.get("/categories")
async def category_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    # Kategori raporu bağımsız bir sipariş pipeline'ı çalıştırdığında kısmi iade,
    # item-level claim ve pazaryeri indirimi ana ürün raporundan sapıyordu. Tek
    # kanonik ürün motorunu kullan; böylece kategori toplamı ürün/Excel/hız raporuyla
    # aynı net adet ve net ciroyu verir.
    canonical = await top_products(
        limit=5000, start_date=start_date, end_date=end_date,
        source=source, include_all_velocity=False, current_user=current_user,
    )
    grouped: dict = {}
    for row in canonical.get("items", []):
        qty = int(row.get("qty") or 0)
        revenue = float(row.get("revenue") or 0)
        if qty <= 0 and revenue <= 0:
            continue
        category = (row.get("category") or "(Kategorisiz)").strip() or "(Kategorisiz)"
        dst = grouped.setdefault(category, {"qty": 0, "revenue": 0.0})
        dst["qty"] += qty
        dst["revenue"] += revenue
    out = [
        {"category": category, "qty": values["qty"],
         "revenue": round(values["revenue"], 2)}
        for category, values in grouped.items()
    ]
    out.sort(key=lambda row: -row["revenue"])
    return {"items": out, "range_days": canonical.get("range_days")}


_CAT_INSIGHT_CACHE: dict = {}


@router.get("/category-insights")
async def category_insights(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    months: int = Query(6, ge=2, le=12),
    min_date: Optional[str] = Query(None, description="rapor kapsam başlangıcı (YYYY-MM-DD); öncesi aylara girilmez"),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Kategori bazlı SATIŞ performansı + AYLIK trend (Kâr & Stok Değer sayfası).
    Tüm adet/ciro, Satış Raporları'nın KANONİK ürün motorundan (top_products: iptal,
    kısmi iptal, onaylı iade düşülmüş NET satış) gelir → iki sayfa aynı rakamı verir.
    - period: seçili dönem kategori satırları (net adet, net ciro, pay %, ort. satış fiyatı,
      satılan malın ALIŞ maliyeti — maliyet zinciri: Maliyet Girişi > alış > senkron;
      maliyeti bilinmeyen adet ayrıca sayılır, tahmin edilmez).
    - monthly: bitiş ayından geriye `months` takvim ayı, her ay ayrı kanonik hesap.
    - accelerating: son TAM ay, bir önceki aya göre en çok büyüyen kategoriler."""
    import asyncio as _aio
    import time as _t
    from .reports_v2 import _product_cost_lookup
    if not isinstance(months, int):
        months = 6
    if not isinstance(min_date, str):
        min_date = None
    _key = (start_date, end_date, source or "", months, min_date or "")
    _hit = _CAT_INSIGHT_CACHE.get(_key)
    if _hit and _t.time() - _hit[0] < 300:
        return _hit[1]

    async def _rows(sd, ed):
        r = await top_products(limit=5000, start_date=sd, end_date=ed, source=source,
                               include_all_velocity=False, current_user=current_user)
        return r.get("items", []) or []

    # --- dönem ---
    period_rows = await _rows(start_date, end_date)
    pids = list({str(r.get("product_id")) for r in period_rows if r.get("product_id")})
    cost = await _product_cost_lookup(pids) if pids else {}
    cats: dict = {}
    for r in period_rows:
        qty = int(r.get("qty") or 0)
        rev = float(r.get("revenue") or 0)
        if qty <= 0 and rev <= 0:
            continue
        c = (r.get("category") or "(Kategorisiz)").strip() or "(Kategorisiz)"
        d = cats.setdefault(c, {"qty": 0, "revenue": 0.0, "products": 0, "cogs": 0.0,
                                "cost_qty": 0, "cost_rev": 0.0})
        d["qty"] += qty
        d["revenue"] += rev
        d["products"] += 1
        uc = float(cost.get(str(r.get("product_id") or "")) or 0)
        if uc > 0 and qty > 0:
            d["cogs"] += uc * qty
            d["cost_qty"] += qty
            d["cost_rev"] += rev
    tq = sum(v["qty"] for v in cats.values()) or 0
    tr_ = sum(v["revenue"] for v in cats.values()) or 0.0
    period = []
    for c, v in cats.items():
        margin = (v["cost_rev"] - v["cogs"]) if v["cost_qty"] else None
        period.append({
            "category": c, "qty": v["qty"], "revenue": round(v["revenue"], 2),
            "products": v["products"],
            "qty_share": round(v["qty"] / tq * 100, 1) if tq else 0.0,
            "revenue_share": round(v["revenue"] / tr_ * 100, 1) if tr_ else 0.0,
            "avg_price": round(v["revenue"] / v["qty"], 2) if v["qty"] else None,
            "avg_cost": round(v["cogs"] / v["cost_qty"], 2) if v["cost_qty"] else None,
            "cogs": round(v["cogs"], 2) if v["cost_qty"] else None,
            "cost_known_qty": v["cost_qty"],
            "gross_margin": round(margin, 2) if margin is not None else None,
            "gross_margin_pct": (round(margin / v["cost_rev"] * 100, 1)
                                 if (margin is not None and v["cost_rev"]) else None),
        })
    period.sort(key=lambda x: (-x["qty"], -x["revenue"]))

    # --- aylık (takvim ayları, TR) ---
    now_tr = datetime.now(timezone.utc) + timedelta(hours=3)
    try:
        end_ref = datetime.strptime((end_date or "")[:10], "%Y-%m-%d") if end_date else now_tr
    except Exception:
        end_ref = now_tr
    ym = []
    y, m = end_ref.year, end_ref.month
    for _ in range(months):
        ym.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    ym.reverse()
    # Rapor kapsamı (Satış Raporları ile aynı başlangıç): kapsam öncesi aylar gösterilmez.
    _min = (min_date or "")[:10]
    if len(_min) == 10:
        _my = (int(_min[:4]), int(_min[5:7]))
        ym = [x for x in ym if x >= _my] or [_my]
    import calendar as _cal

    def _bounds(yy, mm):
        last = _cal.monthrange(yy, mm)[1]
        sd = f"{yy:04d}-{mm:02d}-01"
        if len(_min) == 10 and _min[:7] == sd[:7] and _min > sd:
            sd = _min
        ed_day = last
        if (yy, mm) == (now_tr.year, now_tr.month):
            ed_day = now_tr.day
        return sd, f"{yy:04d}-{mm:02d}-{ed_day:02d}"

    sem = _aio.Semaphore(3)

    async def _month(yy, mm):
        sd, ed = _bounds(yy, mm)
        async with sem:
            rows = await _rows(sd, ed)
        agg: dict = {}
        for r in rows:
            q_ = int(r.get("qty") or 0)
            rv = float(r.get("revenue") or 0)
            if q_ <= 0 and rv <= 0:
                continue
            c = (r.get("category") or "(Kategorisiz)").strip() or "(Kategorisiz)"
            a = agg.setdefault(c, [0, 0.0])
            a[0] += q_
            a[1] += rv
        return agg

    month_aggs = await _aio.gather(*[_month(yy, mm) for yy, mm in ym])
    _TR_AY = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
    month_meta = [{"key": f"{yy:04d}-{mm:02d}", "label": f"{_TR_AY[mm - 1]} {str(yy)[2:]}",
                   "partial": (yy, mm) == (now_tr.year, now_tr.month),
                   "qty": sum(a[0] for a in agg.values()),
                   "revenue": round(sum(a[1] for a in agg.values()), 2)}
                  for (yy, mm), agg in zip(ym, month_aggs)]
    all_cats = set()
    for agg in month_aggs:
        all_cats.update(agg.keys())
    series = []
    for c in all_cats:
        qs = [int((agg.get(c) or [0, 0])[0]) for agg in month_aggs]
        rs = [round(float((agg.get(c) or [0, 0])[1]), 2) for agg in month_aggs]
        series.append({"category": c, "qty": qs, "revenue": rs, "total_qty": sum(qs),
                       "total_revenue": round(sum(rs), 2)})
    series.sort(key=lambda x: (-x["total_qty"], -x["total_revenue"]))

    # Hızlanan: son TAM ay vs bir önceki ay (devam eden ay kısmi olduğundan kıyasta kullanılmaz)
    full_idx = [i for i, mm in enumerate(month_meta) if not mm["partial"]]
    accelerating, slowing = [], []
    if len(full_idx) >= 2:
        a_i, b_i = full_idx[-1], full_idx[-2]
        for srow in series:
            cur, prev = srow["qty"][a_i], srow["qty"][b_i]
            if cur < 5 and prev < 5:
                continue   # çok küçük hacim — yüzdesi yanıltıcı
            ch = cur - prev
            pct = round(ch / prev * 100, 1) if prev else None
            item = {"category": srow["category"], "prev": prev, "cur": cur, "change": ch, "pct": pct}
            if ch > 0:
                accelerating.append(item)
            elif ch < 0:
                slowing.append(item)
        accelerating.sort(key=lambda x: (-(x["change"]), -(x["pct"] or 0)))
        slowing.sort(key=lambda x: (x["change"]))
        compare = {"cur": month_meta[a_i]["label"], "prev": month_meta[b_i]["label"]}
    else:
        compare = None

    out = {
        "period": period,
        "totals": {"qty": tq, "revenue": round(tr_, 2), "categories": len(period)},
        "top_by_qty": period[0] if period else None,
        "top_by_revenue": (max(period, key=lambda x: x["revenue"]) if period else None),
        "months": month_meta,
        "series": series,
        "accelerating": accelerating[:8],
        "slowing": slowing[:8],
        "compare": compare,
    }
    _CAT_INSIGHT_CACHE[_key] = (_t.time(), out)
    if len(_CAT_INSIGHT_CACHE) > 60:
        _CAT_INSIGHT_CACHE.pop(next(iter(_CAT_INSIGHT_CACHE)))
    return out


@router.get("/stock")
async def stock_report(current_user: dict = Depends(require_permission("reports.stock"))):
    # DENETİM FIX: stok varyantlı üründe top-level 'stock'ta DEĞİL variants[].stock'ta tutulur.
    # Eski rapor yalnız top-level 'stock'a bakıyordu → varyantlı ürünler yanlışlıkla 'tükendi'
    # sayılıyor, toplam adet/değer ve kritik/tükenen listeleri hatalı çıkıyordu. Efektif stok:
    # varyant varsa varyant toplamı, yoksa top-level stock.
    _eff = {"$cond": [{"$gt": [{"$size": {"$ifNull": ["$variants", []]}}, 0]},
                      {"$sum": {"$map": {"input": "$variants", "as": "v",
                                         "in": {"$convert": {"input": "$$v.stock", "to": "int", "onError": 0, "onNull": 0}}}}},
                      {"$convert": {"input": "$stock", "to": "int", "onError": 0, "onNull": 0}}]}
    low, out_of_stock, passive_out = [], [], []
    units = 0
    value = 0.0
    async for r in db.products.aggregate([
        # Denetim: silinmiş (çöp kutusu) + pasif ürünler stok toplam/değerine katılmaz.
        # PASİF ürünler yine okunur: stok senkronu stoğu 0'a inen ürünü pasife alıyor
        # (sync_stock_from_xls), bu yüzden "Stoğu Biten" listesi onları hiç gösteremiyordu.
        # Artık pasif stoksuzlar listede "Pasif" işaretiyle (aktiflerden SONRA) yer alır.
        {"$match": {"is_deleted": {"$ne": True}}},
        {"$project": {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "is_active": 1,
                      "price": 1, "sale_price": 1, "eff": _eff}}
    ]):
        s = int(r.get("eff") or 0)
        if r.get("is_active") is False:
            if s <= 0:
                passive_out.append({"id": r.get("id"), "name": r.get("name"),
                                    "stock_code": r.get("stock_code"), "stock": s,
                                    "passive": True})
            continue
        units += s
        try:
            # Gelişmiş Stok Değer raporuyla aynı tanım: varsa güncel indirimli
            # satış fiyatı, yoksa normal fiyat. İki stok ekranı artık aynı toplamı verir.
            value += s * (float(r.get("sale_price") or 0) or float(r.get("price") or 0))
        except Exception:
            pass
        row = {"id": r.get("id"), "name": r.get("name"), "stock_code": r.get("stock_code"), "stock": s}
        if s <= 0:
            out_of_stock.append(row)
        elif s <= 5:
            low.append(row)
    low.sort(key=lambda x: x["stock"])
    return {
        # Kritik stok yalnız AKTİF ürünler (satıştaki ürünler için yeniden üretim uyarısı).
        "low_stock": low[:100],
        # Aktif stoksuzlar önce, pasif (stok bitince pasife alınmış) stoksuzlar sonra.
        "out_of_stock": (out_of_stock + passive_out)[:200],
        "out_of_stock_counts": {"active": len(out_of_stock), "passive": len(passive_out)},
        "totals": {"units": units, "value": round(value, 2)},
    }


# Ödeme yöntemi etiketleri — İKİ ödeme ekranı (Satış Raporları > Ödeme Yöntemi ve
# Gelişmiş Raporlar > Ödeme Tipi) TEK sözlükten beslenir: aynı yöntem aynı adla
# ('Kredi Kartı' / 'Kredi/Banka Kartı' ya da küçük harf 'trendyol' ayrışması bitti).
_PAYMENT_LABELS = {
    "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu", "n11": "n11",
    "amazon": "Amazon",
    "credit_card": "Kredi Kartı", "card": "Kredi Kartı", "iyzico": "Kredi Kartı",
    "iyzipay": "Kredi Kartı",
    "bank_transfer": "Havale/EFT", "havale": "Havale/EFT", "eft": "Havale/EFT",
    "havale_eft": "Havale/EFT", "banka_havale": "Havale/EFT", "transfer": "Havale/EFT",
    "cash_on_delivery": "Kapıda Ödeme", "kapida": "Kapıda Ödeme", "cod": "Kapıda Ödeme",
    "kapida_odeme": "Kapıda Ödeme",
    "gift_card": "Hediye Çeki", "marketplace": "Pazaryeri (diğer)",
    # Ticimax geçmişinden aktarılan site siparişleri (ödeme yöntemi bilinmiyor)
    "ticimax": "Site (eski Ticimax kaydı)",
    # Siparişi bulunamayan iade belgesi (ödeme yöntemi çıkarılamadı)
    "bilinmeyen": "Bilinmeyen",
}


def _payment_label(key) -> str:
    k = str(key or "").strip().lower()
    if not k or k == "—":
        return "Belirtilmemiş"
    return _PAYMENT_LABELS.get(k) or k.replace("_", " ").title()


@router.get("/payments")
async def payment_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    current_user: dict = Depends(require_permission("reports.financial")),
):
    s, e = _iso_range(start_date, end_date)
    # Ödeme raporu da satış kartlarıyla aynı sipariş evrenini ve aynı KALEM-BAZLI
    # iptal/iade ayrıştırmasını kullanır. `_sales_stages` terminal siparişleri tamamen
    # dışladığı için kısmi iade/iptalde müşteride kalan net tutarı da kaybediyordu;
    # bu da ödeme kırılımı toplamını ana "Net Satış" kartıyla çeliştiriyordu.
    orders, closed, open_ = await _canonical_report_context(s, e, source)

    grouped: dict = {}
    for order in orders:
        grouped.setdefault(payment_report_group_key(order), []).append(order)
    # KOHORT (Net kartıyla aynı taban): her yöntemin brütü − YALNIZ bu dönemin
    # siparişlerinden BUGÜNE KADAR kesinleşmiş iptal/iade. Ödeme anahtarı işlemin bağlı
    # olduğu siparişten gelir. Böylece Σ yöntem = sales-breakdown.net_kohort (ciro,
    # sipariş, adet). Eski dönem-neti önceki ayların iadesini de düşüyor, kırpılmış
    # sipariş sayısı hiçbir karta uymuyordu; negatif dilim de buradan çıkıyordu.
    _hi = await _cohort_until_rule(e)
    _members = _cohort_members(orders)
    coh = _slice_actions(
        await _action_rows(s, _hi), s, e, source,
        keyfn=lambda r: (r.get("pay") if _cohort_member_key(r, _members) == "bu_donem" else None),
        at_until=_hi)
    _z = {"revenue": 0.0, "orders": 0, "units": 0}
    merged: dict = {}
    for key in sorted(set(grouped) | set(coh["cancels"]) | set(coh["returns"])):
        inc = _bucket_orders(grouped.get(key, []), closed, open_)["included"]
        c = coh["cancels"].get(key, _z)
        r = coh["returns"].get(key, _z)
        label = _payment_label(key)
        m = merged.setdefault(label, {"orders": 0, "units": 0, "revenue": 0.0,
                                      "gross_revenue": 0.0, "cancel_total": 0.0,
                                      "return_total": 0.0})
        m["orders"] += inc["orders"] - c["orders"] - r["orders"]
        m["units"] += inc["units"] - c["units"] - r["units"]
        m["revenue"] += inc["revenue"] - c["revenue"] - r["revenue"]
        m["gross_revenue"] += inc["revenue"]
        m["cancel_total"] += c["revenue"]
        m["return_total"] += r["revenue"]
    out = [{"method": k, "orders": v["orders"], "units": v["units"],
            "revenue": round(v["revenue"], 2),
            "gross_revenue": round(v["gross_revenue"], 2),
            "cancel_total": round(v["cancel_total"], 2),
            "return_total": round(v["return_total"], 2)}
           for k, v in sorted(merged.items(), key=lambda kv: -kv[1]["revenue"])]
    return {"items": out, "basis": "kohort"}


@router.get("/sales-by-platform")
async def sales_by_platform(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Kanal Bazında Satış — yalnız SİTE + PAZARYERLERİ (Instagram/Google gibi trafik
    kaynakları DEĞİL; sipariş platform alanından). İptal/iade hariç."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e)
    _SRC = {"site": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu",
            "n11": "n11", "amazon": "Amazon", _UNKNOWN_CH: "Bilinmeyen"}
    grouped = {}
    for order in orders:
        platform = str(order.get("platform") or "").strip().lower()
        marketplace = str(order.get("marketplace") or "").strip().lower()
        channel = platform if platform in _MARKETPLACES else (
            marketplace if marketplace in _MARKETPLACES else "site")
        grouped.setdefault(channel, []).append(order)
    # Kanal tablosu ve ciro kartlarıyla AYNI dönem muhasebesi.
    act = await _period_actions(s, e)
    _z = {"revenue": 0.0, "orders": 0, "units": 0}
    rows = []
    for channel in sorted(set(grouped) | set(act["cancels"]) | set(act["returns"])):
        inc = _bucket_orders(grouped.get(channel, []), closed, open_)["included"]
        c = act["cancels"].get(channel, _z)
        r = act["returns"].get(channel, _z)
        rows.append({"channel": _SRC.get(channel, channel),
                     "orders": max(0, inc["orders"] - c["orders"] - r["orders"]),
                     "units": max(0, inc["units"] - c["units"] - r["units"]),
                     "revenue": round(inc["revenue"] - c["revenue"] - r["revenue"], 2)})
    rows.sort(key=lambda row: -row["revenue"])
    return {"rows": rows}


@router.get("/cancel-return-products")
async def cancel_return_products(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Ürün bazlı İADE & İPTAL raporu (Ürün Raporları altındaki alan): tarih aralığında
    iptal/iade edilen siparişlerin kalemleri — ürün adı + platform kırılımıyla.
    Frontend ada göre arar, platforma göre süzer."""
    # Ürün ana tablosunun aynı kanonik motorunu kullan: böylece terminal işlemlere
    # ek olarak aktif siparişteki accepted claim ve partial-cancel da burada görünür.
    canonical = await top_products(
        limit=5000, start_date=start_date, end_date=end_date,
        source=source, include_all_velocity=False, current_user=current_user,
    )
    canonical_rows = {}
    for product in canonical.get("items", []):
        for part in product.get("cancel_return_by_platform") or []:
            cq, rq = int(part.get("cancel") or 0), int(part.get("return") or 0)
            if cq <= 0 and rq <= 0:
                continue
            platform = str(part.get("platform") or "site").lower()
            if platform == "meluni":
                platform = "site"
            key = (product.get("name") or "Ürün", platform)
            dst = canonical_rows.setdefault(key, {
                "name": key[0], "platform": platform.title() if platform != "n11" else "n11",
                "cancel_qty": 0, "cancel_total": 0.0,
                "return_qty": 0, "return_total": 0.0,
            })
            dst["cancel_qty"] += cq
            dst["return_qty"] += rq
            # GERÇEK platform tutarı (top_products platform kırılımında kalem payı olarak
            # toplanır). Eskiden ürün toplamı ADET ORANIYLA dağıtılıyordu; kanallar arası
            # fiyat/indirim farkında platform satırları kayıyordu (toplam doğru kalıyordu).
            dst["cancel_total"] += float(part.get("cancel_total") or 0)
            dst["return_total"] += float(part.get("return_total") or 0)
    for row in canonical_rows.values():
        row["cancel_total"] = round(row["cancel_total"], 2)
        row["return_total"] = round(row["return_total"], 2)
    result = sorted(canonical_rows.values(),
                    key=lambda row: -(row["cancel_qty"] + row["return_qty"]))[:800]
    return {"items": result, "range_days": canonical.get("range_days")}

    # Legacy implementation retained below temporarily for rollback readability;
    # unreachable after the canonical return above.
    s, e = _iso_range(start_date, end_date)
    _RETURN_ST = ["return_requested", "return_approved", "return_in_transit",
                  "returned", "refunded", "partial_refunded"]
    _CANCEL_ST = ["cancelled", "cancel_refunded"]
    clauses = [effective_order_date_match(s, e)]
    sc = _source_cond(source)
    if sc:
        clauses.append(sc)
    _plat = _channel_expr()
    pipeline = [
        {"$match": merge_match({"$and": clauses})},
        *canonical_order_stages(),
        {"$match": {"status": {"$in": _RETURN_ST + _CANCEL_ST}}},
        {"$addFields": {"_plat": _plat,
                        "_kind": {"$cond": [{"$in": ["$status", _CANCEL_ST]}, "cancel", "return"]},
                        # DENETİM O2: kısmi iptal/iade tutarına sipariş-düzeyi havale/EFT indirimi
                        # (payment_discount) yansısın. _sub = sipariş kalem toplamı, _coupon = kupon,
                        # _pdisc = havale/EFT indirimi. Grup sipariş bazlı (_id.on) → $first sabittir.
                        "_sub": {"$reduce": {"input": {"$ifNull": ["$items", []]}, "initialValue": 0,
                                 "in": {"$add": ["$$value", {"$multiply": [
                                     {"$ifNull": ["$$this.quantity", 1]},
                                     {"$ifNull": ["$$this.price", {"$ifNull": ["$$this.unit_price", 0]}]}]}]}}},
                        "_coupon": {"$ifNull": ["$discount", {"$ifNull": ["$discount_amount", 0]}]},
                        "_pdisc": {"$ifNull": ["$payment_discount", 0]}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$group": {
            "_id": {"name": {"$ifNull": ["$items.name", {"$ifNull": ["$items.product_name", "Ürün"]}]},
                    "plat": "$_plat", "kind": "$_kind",
                    # Kısmi iade ayrıştırması sipariş+barkod bazında yapılır.
                    "on": {"$toString": {"$ifNull": ["$order_number", ""]}},
                    "bc": {"$toString": {"$ifNull": ["$items.barcode", ""]}},
                    "status": "$status"},
            "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
            # DENETİM HATA-3: unit_price İSKONTO ÖNCESİ liste fiyatı — tutarlar %11-14 şişiyordu.
            # Önce items.price (ödenen net birim), yoksa unit_price kullanılır (by-source ile eşitlenir).
            "total": {"$sum": {"$multiply": [
                {"$ifNull": ["$items.quantity", 1]},
                {"$ifNull": ["$items.price", {"$ifNull": ["$items.unit_price", 0]}]}]}},
            "_sub": {"$first": "$_sub"}, "_coupon": {"$first": "$_coupon"}, "_pdisc": {"$first": "$_pdisc"},
        }},
    ]
    _SRC = {"site": "Site", "meluni": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu", "n11": "n11", "amazon": "Amazon"}
    rows: dict = {}
    _agg = [r async for r in db.orders.aggregate(pipeline)]
    # KISMİ İADE: yalnız gerçekten iade edilen kalem İade'ye yazılır (ürün raporunun
    # ana tablosuyla aynı mantık — aksi halde aynı ekranda iki farklı iade adedi çıkar).
    _ret_bc = await _returned_barcode_qty(
        list({r["_id"].get("on") for r in _agg
              if r["_id"].get("kind") == "return" and r["_id"].get("on")}))
    for r in _agg:
        # 'meluni' etiketi de Site'dır (DENETİM HATA-3 eki) — aynı ürünün iki satırı birleşsin
        _pl = "site" if (r["_id"]["plat"] or "site") in ("site", "meluni") else r["_id"]["plat"]
        key = (r["_id"]["name"], _pl)
        d = rows.setdefault(key, {"name": r["_id"]["name"],
                                  "platform": _SRC.get(_pl, _pl or "Site"),
                                  "cancel_qty": 0, "cancel_total": 0.0,
                                  "return_qty": 0, "return_total": 0.0})
        _q = int(r["qty"])
        _t = float(r["total"] or 0)
        # DENETİM O2: kalem tutarına sipariş-düzeyi havale/EFT indirimini yansıt →
        # oran = (subtotal − kupon − havale_indirimi)/(subtotal − kupon). Veri yoksa oran 1 (fallback).
        _denom = float(r.get("_sub") or 0) - float(r.get("_coupon") or 0)
        if _denom > 0:
            _t *= min(1.0, max(0.0, (_denom - float(r.get("_pdisc") or 0)) / _denom))
        if r["_id"]["kind"] == "cancel":
            d["cancel_qty"] += _q
            d["cancel_total"] += _t
        else:
            _rb = _ret_bc.get(r["_id"].get("on") or "")
            if _rb:  # kalem bilgisi VAR → böl; yoksa eski davranış (tüm sipariş iade)
                _bc = str(r["_id"].get("bc") or "").strip()  # DENETİM O7: barkod anahtarı strip'li
                _allowed = int(_rb.get(_bc, 0))
                _rq = min(_q, _allowed)
                _rb[_bc] = _allowed - _rq
                _t = _t * (_rq / _q) if _q else 0.0
                _q = _rq
            elif r["_id"].get("status") not in ("returned", "refunded"):
                # Talep/açık/kısmi durumunda kalem kanıtı yoksa tüm siparişi iade
                # varsaymak oranı şişirir. Yalnız kesin tam-iade statüsü fallback'tir.
                _q = 0
                _t = 0.0
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
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Kanal (Site/Trendyol/Hepsiburada/Temu) bazında satış · iptal · iade.

    Ciro kartlarıyla (sales-breakdown) AYNI `_bucket_orders` mantığını kullanır:
    kısmi iadede yalnız iade edilen kalemin tutarı/adedi İade'ye yazılır, kalan
    ürünler Net'te kalır. Eskiden bu uç siparişin TAMAMINI iadeye yazdığı için
    aynı ekranda kartlarla çelişen bir "İade Tutarı" gösteriyordu.

    Her kanal için: sipariş · ADET · tutar (net/iptal/iade) + açık iade projeksiyonu.
    """
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)

    _SRC = {"site": "Site", "trendyol": "Trendyol", "hepsiburada": "Hepsiburada", "temu": "Temu",
            "n11": "n11", "amazon": "Amazon", _UNKNOWN_CH: "Bilinmeyen"}
    by_ch: dict = {}
    for o in orders:
        # Alanlardan herhangi biri bilinen pazaryeriyse onu seç; aksi halde Meluni/web/
        # boş değerlerin tamamı Site'dır. ``platform or marketplace`` kullanmak, örn.
        # platform="meluni" + marketplace="trendyol" eski kaydını yanlışlıkla Site'a atardı.
        platform = str(o.get("platform") or "").strip().lower()
        marketplace = str(o.get("marketplace") or "").strip().lower()
        key = platform if platform in _MARKETPLACES else (
            marketplace if marketplace in _MARKETPLACES else "site")
        by_ch.setdefault(key, []).append(o)

    # Site satırı tarih aralığında sıfır olsa da kaybolmasın. Böylece "Dün" / "Son 7 Gün"
    # geçişinde tablo yalnız Trendyol varmış izlenimi vermez; gerçek sıfır açıkça görünür.
    if _canon_source(source) in ("all", "site"):
        by_ch.setdefault("site", [])

    # Kartlarla AYNI dönem muhasebesi: iptal/iade işlemin kesinleştiği döneme,
    # iade tutarı gider pusulasından. Aksi halde aynı ekranda yine iki rakam olur.
    # İşlem satırları bir kez BUGÜNE KADAR okunur; dönem ve kohort bellekte dilimlenir.
    _hi = await _cohort_until_rule(e)
    _rows = await _action_rows(s, _hi)
    act = _slice_actions(_rows, s, e, source)
    _z = {"revenue": 0.0, "orders": 0, "units": 0}

    # KANAL TABLOSU KOHORT TEMELİNE ÇEVRİLDİ.
    # Eskiden satırın Net Ciro'su dönem muhasebesindeydi: bu dönemde kesinleşen TÜM
    # iptal/iadeler düşülüyordu, ÖNCEKİ ayların siparişlerinden gelenler dahil. Trendyol
    # satırı bu yüzden pazaryeri panelinden düşük çıkıyor ve "siparişleri düzgün
    # saymıyor musun?" sorusuna yol açıyordu — oysa sipariş SAYISI doğruydu (eylül 1.443
    # sipariş, Trendyol API'siyle birebir, kayıp yok); düşük olan NET'ti.
    # Satır artık kendi içinde tutarlı: brüt − bu dönemin satışından iptal − bu dönemin
    # satışından iade = net. Muhasebenin dönem rakamları KAYBOLMADI, ayrı alanlarda döner.
    # Kohort penceresi BUGÜNE KADAR; üyelik = bu dönemin sipariş kümesi (kartlarla aynı).
    _members = _cohort_members(orders)
    _coh_ch = _slice_actions(_rows, s, e, source,
                             # Satır kanalını YENİDEN hesaplama: _action_rows zaten
                             # 'ch' alanını _channel_of ile doldurdu; ikinci bir hesap
                             # iki yerde farklı kanal üretme riski taşır.
                             keyfn=lambda r: (_cohort_member_key(r, _members),
                                              r.get("ch") or "site"),
                             at_until=_hi)
    def _coh(kind: str, ch: str) -> dict:
        return _coh_ch[kind].get(("bu_donem", ch)) or _z

    items = []
    # KANAL EVRENİ = kartlarla aynı KOHORT evreni: sipariş kanalları ∪ bu dönemin
    # siparişlerine ait iptal/iade kanalları. Eskiden dönem-muhasebesi kovaları da
    # ekleniyordu; yalnız ÖNCEKİ ayın siparişine iptal/iadesi olan bir kanal (ör. Eylül'de
    # Amazon) tüm hücreleri sıfır bir satır olarak görünüyordu. Kart ve tablo artık aynı
    # (kohort) tabanda olduğu için o birleşimin gerekçesi kalmadı.
    _coh_channels = {ch for kind in ("cancels", "returns")
                     for (b, ch) in _coh_ch[kind] if b == "bu_donem"}
    for key in sorted(set(by_ch) | _coh_channels):
        rows = by_ch.get(key, [])
        b = _bucket_orders(rows, closed, open_)
        _c = act["cancels"].get(key, _z)
        _r = act["returns"].get(key, _z)
        _inc = b["included"]
        # Dönem (muhasebe) rakamları — kaybolmasın diye ayrı alanlarda korunur.
        _net_donem = {
            "revenue": round(_inc["revenue"] - _c["revenue"] - _r["revenue"], 2),
            "orders": max(0, _inc["orders"] - _c["orders"] - _r["orders"]),
            "units": max(0, _inc["units"] - _c["units"] - _r["units"]),
        }
        # Kohort (bu dönemin kendi satışı) — tablonun gösterdiği temel.
        _ck = _coh("cancels", key)
        _rk = _coh("returns", key)
        _net = {
            "revenue": round(_inc["revenue"] - _ck["revenue"] - _rk["revenue"], 2),
            "orders": max(0, _inc["orders"] - _ck["orders"] - _rk["orders"]),
            "units": max(0, _inc["units"] - _ck["units"] - _rk["units"]),
        }
        items.append({
            "source": _SRC.get(key, key or "Site"),
            # Net (elde kalan) — kanal tablosunun "Sipariş / Ciro" sütunları.
            # Bu dönemde satılanın net'i; pazaryeri paneliyle kıyaslanacak rakam budur.
            "orders": _net["orders"], "units": _net["units"], "revenue": _net["revenue"],
            # İptal — bu dönemin satışından iptal edilen
            "cancel_orders": _ck["orders"], "cancel_units": _ck["units"],
            "cancel_total": _ck["revenue"],
            # İade — bu dönemin satışından iade edilen (gider pusulası tutarıyla)
            "return_orders": _rk["orders"], "return_units": _rk["units"],
            "return_total": _rk["revenue"],
            # MUHASEBE (dönem) karşılıkları — işlemin kesinleştiği döneme yazılan hâli.
            "donem_revenue": _net_donem["revenue"], "donem_orders": _net_donem["orders"],
            "donem_units": _net_donem["units"],
            "donem_cancel_total": _c["revenue"], "donem_cancel_orders": _c["orders"],
            "donem_return_total": _r["revenue"], "donem_return_orders": _r["orders"],
            # Kaç iade siparişi KISMİ (siparişin bir kısmı iade, kalanı satışta) —
            # satırın iade/iptal sütunlarıyla (ve üst kartla) AYNI kohort tabanından.
            "return_partial_orders": _rk.get("partial_orders", 0),
            "cancel_partial_orders": _ck.get("partial_orders", 0),
            "donem_return_partial_orders": _r.get("partial_orders", 0),
            "donem_cancel_partial_orders": _c.get("partial_orders", 0),
            # Toplam + açık iade projeksiyonu
            "total_orders": b["included"]["orders"], "total_units": b["included"]["units"],
            "total_revenue": b["included"]["revenue"],
            "pending_orders": b["pending_returns"]["orders"],
            "pending_units": b["pending_returns"]["units"],
            "pending_total": b["pending_returns"]["revenue"],
            # Açık iadeler onaylanırsa kalacak net — satırın kendi (kohort) net'inden.
            "projected_revenue": round(_net["revenue"] - b["pending_returns"]["revenue"], 2),
        })
    # Tamamen sıfır satır gizlenir (ör. yalnız ödenmemiş siparişi olan kanal). Site
    # satırı bilinçli istisna: "Dün"/"Son 7 Gün"de sitenin GERÇEK sıfırı görünsün.
    items = [x for x in items if x["source"] == "Site" or any(
        x.get(f) for f in ("total_orders", "total_units", "total_revenue", "cancel_orders",
                           "return_orders", "pending_orders"))]
    items.sort(key=lambda x: -x["total_revenue"])
    return {"items": items}


@router.get("/open-returns/reconciliation")
async def open_returns_reconciliation(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Read-only explanation of operational-open vs dated-report return counts.

    No order/claim identifiers or customer data leave this endpoint.  Counts are
    deliberately split into exclusion reasons so an admin can prove why an
    all-time Returns badge differs from the selected sales-order cohort.
    """
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e)
    by_onum = {str(o.get("order_number") or ""): o for o in orders if o.get("order_number")}

    def _channel(o: dict) -> str:
        platform = str(o.get("platform") or "").strip().lower()
        marketplace = str(o.get("marketplace") or "").strip().lower()
        return platform if platform in _MARKETPLACES else (
            marketplace if marketplace in _MARKETPLACES else "site")

    report_pending = {}
    for channel in ("site", "trendyol", "hepsiburada", "temu", "n11", "amazon"):
        rows = [o for o in orders if _channel(o) == channel]
        bucket = _bucket_orders(rows, closed, open_)["pending_returns"]
        report_pending[channel] = bucket

    # Build the exact read-side marketplace population used by the Returns page,
    # including its order-derived manual fallback rows, but perform no sync/write.
    from .integrations_common import _claim_bucket
    from .integrations_trendyol import (
        _dedup_claims_by_content, _group_hb_claims_by_order,
        _order_derived_trendyol_returns,
    )

    raw_claims = await db.trendyol_claims.find(
        {"claim_type": {"$ne": "CANCEL"}}, {"_id": 0}
    ).sort("created_date", -1).to_list(None)
    seen_claims, deduped = set(), []
    for claim in raw_claims:
        key = claim.get("claim_id") or claim.get("order_number")
        if key in seen_claims:
            continue
        seen_claims.add(key)
        deduped.append(claim)
    visible_orders = {c.get("order_number") for c in deduped
                      if c.get("order_number") and _claim_bucket(c) != "iptal"}
    manual = await _order_derived_trendyol_returns(
        exclude_order_numbers=visible_orders)
    manual_onums = {r.get("order_number") for r in manual if r.get("order_number")}
    if manual_onums:
        deduped = [c for c in deduped
                   if not (c.get("order_number") in manual_onums and not c.get("manual"))]
    deduped.extend(manual)
    deduped = _dedup_claims_by_content(deduped)

    platform_claims = {
        "trendyol": [c for c in deduped
                     if str(c.get("platform") or "").lower() != "hepsiburada"],
        "hepsiburada": _group_hb_claims_by_order([
            c for c in deduped if str(c.get("platform") or "").lower() == "hepsiburada"
        ]),
    }
    all_claim_onums = list({str(c.get("order_number") or "")
                            for rows in platform_claims.values() for c in rows
                            if c.get("order_number")})
    existing_onums = set()
    for i in range(0, len(all_claim_onums), 5000):
        async for row in db.orders.find(
                {"order_number": {"$in": all_claim_onums[i:i + 5000]}},
                {"_id": 0, "order_number": 1}):
            existing_onums.add(str(row.get("order_number") or ""))

    marketplace = {}
    for platform, claims in platform_claims.items():
        events, seen_items = [], set()
        operational_open = action_waiting = 0

        def _safe_float(value) -> float:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        def _safe_qty(value, default=0) -> int:
            try:
                return max(0, int(value if value not in (None, "") else default))
            except (TypeError, ValueError):
                return max(0, int(default))

        for claim in claims:
            if _claim_bucket(claim) == "iptal":
                continue
            has_cargo = bool(str(claim.get("cargo_tracking_number") or "").strip())
            if claim.get("manual"):
                bucket = _claim_bucket(claim)
                if bucket not in ("talep_olusturulan", "kargoya_verilen", "aksiyon_bekleyen"):
                    continue
                for index, item in enumerate(claim.get("items") or [{}]):
                    qty = max(1, _safe_qty((item or {}).get("quantity"), 1))
                    amount = max(0.0, _safe_float((item or {}).get("price"))) * qty
                    events.append((str(claim.get("order_number") or ""), qty, amount, bucket))
                    operational_open += qty if bucket in ("talep_olusturulan", "kargoya_verilen") else 0
                    action_waiting += qty if bucket == "aksiyon_bekleyen" else 0
                continue
            for item in claim_items_with_status(claim, set(OPEN_MARKETPLACE_CLAIM_STATUSES)):
                if item["key"] in seen_items:
                    continue
                seen_items.add(item["key"])
                bucket = marketplace_claim_status_bucket(item.get("status"), has_cargo)
                qty = _safe_qty(item.get("quantity"))
                events.append((str(claim.get("order_number") or ""), qty,
                               max(0.0, _safe_float(item.get("amount"))), bucket))
                operational_open += qty if bucket in ("talep_olusturulan", "kargoya_verilen") else 0
                action_waiting += qty if bucket == "aksiyon_bekleyen" else 0

        reasons = {"no_matching_order": 0, "outside_selected_order_scope": 0,
                   "terminal_cancelled_or_unpaid_order": 0,
                   "exceeds_remaining_order_units": 0,
                   # Numaralı gider pusulası kesilmiş (iadesi KESİNLEŞMİŞ) kısım — rapor
                   # onu 'Açık İade' değil 'İade' sayar (_canonical_report_context).
                   "numbered_voucher_issued": 0,
                   "included_in_report": 0}
        scoped = {}
        for onum, qty, amount, _bucket in events:
            if not onum or onum not in existing_onums:
                reasons["no_matching_order"] += qty
            elif onum not in by_onum or _channel(by_onum[onum]) != platform:
                reasons["outside_selected_order_scope"] += qty
            else:
                d = scoped.setdefault(onum, {"amount": 0.0, "qty": 0})
                d["amount"] += amount
                d["qty"] += qty
        for onum, pending in scoped.items():
            order = by_onum[onum]
            _pv = (open_.get(onum) or {}).get("_pusula")
            if _pv:
                _q0 = int(pending["qty"])
                pending = {"amount": max(0.0, float(pending["amount"]) - float(_pv.get("rev") or 0)),
                           "qty": (max(0, _q0 - int(_pv.get("units") or 0))
                                   if _pv.get("has_lines") else _q0)}
                if not _pv.get("has_lines") and pending["amount"] <= 0.005:
                    pending["qty"] = 0
                reasons["numbered_voucher_issued"] += _q0 - pending["qty"]
            applied = _bucket_orders([order], closed, {onum: pending})["pending_returns"]["units"]
            reasons["included_in_report"] += applied
            rejected = max(0, pending["qty"] - applied)
            if rejected:
                status = str(order.get("status") or "")
                if status in (_CANCEL_STATUSES + _RETURN_CLOSED_STATUSES_BD + _UNPAID_STATUSES):
                    reasons["terminal_cancelled_or_unpaid_order"] += rejected
                else:
                    reasons["exceeds_remaining_order_units"] += rejected
        marketplace[platform] = {
            "operational_open_units_all_time": operational_open,
            "action_waiting_units_all_time": action_waiting,
            "report_candidate_units_all_time": operational_open + action_waiting,
            "report_open_units_selected_period": report_pending[platform]["units"],
            "report_open_amount_selected_period": report_pending[platform]["revenue"],
            "breakdown_units": reasons,
            "breakdown_total_units": sum(reasons.values()),
            "reconciliation_delta_units": (
                report_pending[platform]["units"] - reasons["included_in_report"]),
        }

    site_match = {"platform": {"$nin": _MARKETPLACES},
                  "marketplace": {"$nin": _MARKETPLACES},
                  "status": {"$in": _RETURN_OPEN_STATUSES_BD}}
    site_status_counts = {}
    async for row in db.orders.aggregate([
        {"$match": site_match}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]):
        site_status_counts[str(row.get("_id") or "unknown")] = int(row.get("count") or 0)
    selected_site_open_orders = sum(
        1 for o in orders if _channel(o) == "site"
        and str(o.get("status") or "") in _RETURN_OPEN_STATUSES_BD)

    return {
        "scope": {"start_utc": s, "end_utc": e,
                  "basis": "Sipariş tarihi; bitiş günü Türkiye saatinde dahildir"},
        "definitions": {
            "open_return": "Created/return_requested ve kargodaki sonuçlanmamış iadeler",
            "action_waiting": "WaitingInAction/InAnalysis; Açık İade rozetinden ayrı",
            "report_value": "Yalnız seçili dönemde sipariş edilmiş ürünler",
        },
        "site": {
            "operational_open_orders_all_time_by_status": site_status_counts,
            "operational_open_orders_selected_period": selected_site_open_orders,
            "report_open_units_selected_period": report_pending["site"]["units"],
            "report_open_amount_selected_period": report_pending["site"]["revenue"],
        },
        "marketplaces": marketplace,
    }


@router.get("/cargo")
async def cargo_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(require_permission("reports.sales")),
):
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        # Denetim: ödenmemiş/iptal/iade siparişler kargolanmaz → kargo hacmine katılmasın.
        *_sales_stages(s, e, exclude=_KARGO_EXCLUDED),
        {"$group": {"_id": {"$ifNull": ["$cargo_provider_name", "$cargo.company"]}, "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$shipping_cost", 0]}}}},
        {"$sort": {"orders": -1}},
    ]
    out = []
    async for r in db.orders.aggregate(pipeline):
        out.append({"provider": r["_id"] or "Belirtilmemiş", "orders": r["orders"], "shipping_revenue": round(r["revenue"], 2)})
    return {"items": out}


@router.get("/members")
async def members_report(current_user: dict = Depends(require_permission("reports.sales"))):
    # Top 20 members by spend
    pipeline = [
        {"$match": merge_match({"user_id": {"$ne": None}})},
        *canonical_order_stages(),
        {"$match": {"status": {"$nin": _EXCLUDED_STATUSES}}},
        {"$group": {"_id": "$user_id", "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}, "last_order": {"$max": _effective_date_expr()}}},
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
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Sipariş-tarihi kohortunda kabul edilmiş iadeleri beden bazında döndürür."""
    data = await top_products(
        limit=5000, start_date=start_date, end_date=end_date,
        source=source, include_all_velocity=False, current_user=current_user,
    )
    counts: dict = {}
    for product in data.get("items", []):
        for row in product.get("cancel_return_by_size") or []:
            qty = max(0, int(row.get("return") or 0))
            if qty:
                size = _norm_size(row.get("size"))
                counts[size] = counts.get(size, 0) + qty
    out = [{"size": size, "count": qty, "order_count": None}
           for size, qty in sorted(counts.items(), key=lambda item: -item[1])[:30]]
    return {"by_size": out, "range_days": data.get("range_days")}


@router.get("/returns/by-product")
async def returns_by_product(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(20, ge=1, le=500),
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Ürün bazında kesin iade adedi ve oranı; ana ürün raporuyla aynı veri kümesi.

    Dönem sipariş tarihine göredir. Accepted Trendyol claim ve onaylı site iade
    kalemleri sayılır; iptal adedi oran paydasına girmez.
    """
    data = await top_products(
        limit=5000, start_date=start_date, end_date=end_date,
        source=None, current_user=current_user,
    )
    out = []
    for r in data.get("items", []):
        returned = int(r.get("return_qty") or 0)
        if returned <= 0:
            continue
        sold_excluding_cancels = int(r.get("qty") or 0) + returned
        gross_sold = int(r.get("gross_qty") or (sold_excluding_cancels + int(r.get("cancel_qty") or 0)))
        out.append({
            "product_id": r.get("product_id"),
            "product_name": r.get("name") or "—",
            "returned": returned,
            "sold": sold_excluding_cancels,
            "gross_sold": gross_sold,
            "order_count": int(r.get("orders") or 0),
            # Kullanıcının operasyonel oranı: iptaller hariç.
            "return_rate_pct": round(returned / sold_excluding_cancels * 100, 1)
                               if sold_excluding_cancels else None,
            # Trendyol İş Analizi karşılaştırma oranı: İade / Brüt Satış.
            "trendyol_return_rate_pct": round(returned / gross_sold * 100, 1)
                                        if gross_sold else None,
        })
    out.sort(key=lambda r: (-r["returned"], -(r["return_rate_pct"] or 0)))
    return {"items": out[:limit], "range_days": data.get("range_days")}


@router.get("/returns/reasons")
async def returns_by_reason(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.returns")),
):
    """Sipariş-tarihi kohortundaki kabul edilmiş iadelerin kalem bazlı sebepleri."""
    start, end = _iso_range(start_date, end_date, 180)
    order_pipe = [
        {"$match": merge_match({"$and": [
            effective_order_date_match(start, end), _source_cond(source) or {},
        ]})},
        *canonical_order_stages(),
        {"$project": {"_id": 0, "id": 1, "order_number": 1,
                       "platform": 1, "marketplace": 1}},
    ]
    orders = [row async for row in db.orders.aggregate(order_pipe)]
    onums = [str(row.get("order_number")) for row in orders if row.get("order_number")]
    oids = [str(row.get("id")) for row in orders if row.get("id")]
    counts: dict = {}

    def _add(reason, quantity=1):
        label = str(reason or "Belirtilmemiş").strip() or "Belirtilmemiş"
        counts[label] = counts.get(label, 0) + max(1, int(quantity or 1))

    # Pazaryeri: claim genel statüsü yerine her claimItem'in Accepted durumunu say.
    seen_claim_items = set()
    for offset in range(0, len(onums), 5000):
        async for claim in db.trendyol_claims.find(
                {"order_number": {"$in": onums[offset:offset + 5000]},
                 "claim_type": {"$ne": "CANCEL"}},
                {"_id": 0, "claim_id": 1, "claim_status": 1, "items": 1,
                 "raw_data.items": 1}):
            claim_id = str(claim.get("claim_id") or "")
            raw_items = ((claim.get("raw_data") or {}).get("items") or [])
            if raw_items:
                for line_index, line in enumerate(raw_items):
                    for item_index, item in enumerate((line or {}).get("claimItems") or []):
                        if str(((item or {}).get("claimItemStatus") or {}).get("name") or "") != "Accepted":
                            continue
                        item_id = str((item or {}).get("id") or "")
                        key = (claim_id, item_id or f"{line_index}:{item_index}")
                        if key in seen_claim_items:
                            continue
                        seen_claim_items.add(key)
                        reason = ((item or {}).get("customerClaimItemReason") or {}).get("name")
                        _add(reason)
            elif str(claim.get("claim_status") or "") == "Accepted":
                for item_index, item in enumerate(claim.get("items") or []):
                    key = (claim_id, str((item or {}).get("claim_item_id") or item_index))
                    if key in seen_claim_items:
                        continue
                    seen_claim_items.add(key)
                    _add((item or {}).get("reason"), (item or {}).get("quantity") or 1)

    # Site: yalnız onay kanıtı veya terminal kabul statüsü bulunan kalemler.
    terminal = {"approved", "return_approved", "returned", "refunded",
                "partial_refunded", "completed", "complete"}
    if oids or onums:
        site_query = {"$or": []}
        if oids:
            site_query["$or"].append({"order_id": {"$in": oids}})
        if onums:
            site_query["$or"].append({"order_number": {"$in": onums}})
        async for ret in db.customer_returns.find(
                site_query, {"_id": 0, "status": 1, "approval": 1, "reason": 1,
                             "approved_items": 1, "items": 1}):
            status = str(ret.get("status") or "").lower()
            approved = ret.get("approved_items") or []
            if not approved and not (ret.get("approval") or {}).get("at") and status not in terminal:
                continue
            for item in (approved or ret.get("items") or []):
                _add((item or {}).get("reason") or ret.get("reason"),
                     (item or {}).get("quantity") or 1)

    out = [{"reason": reason, "count": count}
           for reason, count in sorted(counts.items(), key=lambda item: -item[1])]
    return {"reasons": out, "start": start, "end": end}


@router.get("/fast-selling")
async def fast_selling_products(
    window_days: int = Query(14, ge=1, le=90),
    min_sold: int = Query(10, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Hızlı satış dedektörü: son N gün içinde ≥ min_sold adet satan ürünler.
    recommend_ads = true → ilk 60 gün içindeki yeni ürün + min sold'u geçti → reklam önerisi.
    """
    now = datetime.now(timezone.utc)
    start_day = (now - timedelta(days=window_days - 1)).date().isoformat()
    end_day = now.date().isoformat()
    # Tek ürün motoru: ürün raporu, Excel ve hızlı-satan aynı barkod/varyant/parent
    # eşleştirmesini, sipariş tekilleştirmesini ve net ciro hesabını kullanır.
    canonical = await top_products(
        limit=5000, start_date=start_day, end_date=end_day,
        source=None, current_user=current_user,
    )
    out = []
    rows = [r for r in canonical.get("items", []) if int(r.get("qty") or 0) >= min_sold]
    rows.sort(key=lambda r: (-int(r.get("qty") or 0), -float(r.get("revenue") or 0)))
    for r in rows[:limit]:
        pid = r.get("product_id")
        product = await db.products.find_one(
            {"id": pid}, {"_id": 0, "created_at": 1, "name": 1, "images": 1, "stock": 1}) or {}
        product_age_days = None
        created_at = r.get("created_at") or product.get("created_at")
        if created_at:
            try:
                c = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                product_age_days = (now - c).days
            except Exception:
                pass
        age = product_age_days if product_age_days is not None else 999
        recommend_ads = age <= 60 and int(r.get("qty") or 0) >= min_sold
        out.append({
            "product_id": pid,
            "product_name": product.get("name") or r.get("name") or "—",
            "sold_in_window": int(r.get("qty") or 0),
            "order_count": int(r.get("orders") or 0),
            "revenue": round(float(r.get("revenue") or 0), 2),
            "product_age_days": product_age_days,
            "window_days": window_days,
            "stock": r.get("current_stock"),
            "image": (product.get("images") or [None])[0] if product.get("images") else None,
            "recommend_ads": recommend_ads,
        })
    return {"items": out, "window_days": window_days, "min_sold": min_sold}


# =============================================================================
# Üretici performans (FAZ 7 potansiyel iyileştirme)
# =============================================================================

@router.get("/manufacturer-performance")
async def manufacturer_performance(current_user: dict = Depends(require_permission("reports.sales"))):
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
    group: str = Query("city", pattern="^(city|district)$"),
    source: Optional[str] = Query(None, description="all|site|trendyol|hepsiburada|temu"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Satışları İL (city) veya İLÇE (district) bazında gruplar. Tarih + kaynak filtresi."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    grouped = {}
    for order in orders:
        address = order.get("shipping_address") or {}
        # Parantez hatası düzeltildi: `or ''` eskiden yalnız ilçe koluna uygulanıyordu;
        # city=None → str(None) = 'None' adlı sahte bir il çıkıyordu.
        _raw_loc = address.get("city") if group == "city" else address.get("district")
        location = str(_raw_loc or "").strip()
        if not location or location.lower() in ("none", "null", "undefined", "-"):
            location = "Bilinmiyor"
        city = str(address.get("city") or "").strip()
        grouped.setdefault((location, city if group == "district" else ""), []).append(order)
    rows = []
    for (location, city), location_orders in grouped.items():
        # DÖNEM MUHASEBESİ: dönemde verilen siparişlerin BRÜTÜ. Sonradan iade edilen
        # sipariş bu ilin o ayki satışı olmaya devam eder; iade kendi ayına yazılır.
        brut = _bucket_orders(location_orders, closed, open_)["included"]
        if not brut["orders"]:
            continue    # yalnız ödenmemiş siparişi olan kova — 0 siparişli satır listelenmez
        row = {"location": location, "orders": brut["orders"],
               "revenue": brut["revenue"], "items": brut["units"]}
        if group == "district":
            row["city"] = city
        rows.append(row)
    rows.sort(key=lambda row: -row["revenue"])
    rows = rows[:limit]
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
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Satışları KANAL bazında gruplar: pazaryerleri (Trendyol/HB/Temu) + site trafiği kaynağı
    (Instagram/Google/Meta/direct) — attribution.source/channel'dan türetilir."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e)
    agg: dict = {}
    grouped = {}
    for order in orders:
        attr = order.get("attribution") or {}
        label = _channel_label({"platform": order.get("platform"),
                                "marketplace": order.get("marketplace"),
                                "src": attr.get("source"), "channel": attr.get("channel")})
        grouped.setdefault(label, []).append(order)
    for label, source_orders in grouped.items():
        # DÖNEM MUHASEBESİ: kanalın o dönemde getirdiği BRÜT satış.
        brut = _bucket_orders(source_orders, closed, open_)["included"]
        agg[label] = {"orders": brut["orders"], "revenue": brut["revenue"],
                      "units": brut["units"]}
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
    current_user: dict = Depends(require_permission("reports.stock")),
):
    """Son N günde HİÇ satılmayan aktif ürünler (uzun süredir satış yok). Stok değerine göre sıralı."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sold = set()
    pipeline = [
        *_sales_stages(cutoff, datetime.now(timezone.utc).isoformat()),
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
    source: Optional[str] = None, current_user: dict = Depends(require_permission("reports.sales")),
):
    """Günün SAATLERİNE göre satış dağılımı (TR saati, +03:00). En yoğun saatler."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    grouped = {}
    for order in orders:
        local = _order_datetime_tr(order)
        if local:
            grouped.setdefault(f"{local.hour:02d}", []).append(order)
    by = {f"{h:02d}": {"orders": 0, "revenue": 0.0, "units": 0} for h in range(24)}
    for hour, hour_orders in grouped.items():
        by[hour] = _bucket_orders(hour_orders, closed, open_)["included"]
    rows = [{"hour": h, "orders": v["orders"], "revenue": v["revenue"]} for h, v in sorted(by.items())]
    return {"rows": rows, "totals": {"orders": sum(x["orders"] for x in rows), "revenue": round(sum(x["revenue"] for x in rows), 2)}}


# Geriye uyum adı — artık Satış Raporları > Ödeme Yöntemi ile AYNI sözlük.
_PM_LABELS = _PAYMENT_LABELS


@router.get("/by-payment")
async def sales_by_payment(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    source: Optional[str] = None, current_user: dict = Depends(require_permission("reports.sales")),
):
    """Ödeme tipine göre satış (Havale / Kart / Kapıda vb.)."""
    s, e = _iso_range(start_date, end_date)
    orders, closed, open_ = await _canonical_report_context(s, e, source)
    agg = {}
    grouped = {}
    for order in orders:
        grouped.setdefault(payment_report_group_key(order), []).append(order)
    for key, group_orders in grouped.items():
        r = _bucket_orders(group_orders, closed, open_)["included"]
        label = _payment_label(key)
        a = agg.setdefault(label, {"orders": 0, "revenue": 0.0})
        a["orders"] += r["orders"]; a["revenue"] += r["revenue"] or 0
    rows = [{"method": k, "orders": v["orders"], "revenue": round(v["revenue"], 2)} for k, v in agg.items()]
    rows.sort(key=lambda x: -x["revenue"])
    # Taban açıkça söylenir: bu ekran BRÜT (iptal & iade dahil) — Satış Raporları'ndaki
    # Ödeme Yöntemi paneli ise kohort NET'i gösterir.
    return {"rows": rows, "basis": "brut_iptal_iade_dahil",
            "totals": {"orders": sum(x["orders"] for x in rows), "revenue": round(sum(x["revenue"] for x in rows), 2)}}


@router.get("/coupon-performance")
async def coupon_performance(
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Kupon performansı: her kupon kaç siparişte kullanıldı, ne kadar indirim + ciro getirdi."""
    s, e = _iso_range(start_date, end_date)
    pipeline = [
        *_sales_stages(s, e, exclude=_DONEM_EXCLUDED),
        {"$match": {"coupon_code": {"$nin": [None, ""]}}},
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
    current_user: dict = Depends(require_permission("reports.sales")),
):
    """Bu aralıkta sipariş veren müşteriler: YENİ (ilk siparişi bu aralıkta) vs TEKRAR EDEN
    (daha önce de sipariş vermiş). Müşteri anahtarı: e-posta."""
    s, e = _iso_range(start_date, end_date)
    key = {"$toLower": {"$ifNull": ["$email", {"$ifNull": ["$shipping_address.email", "$user_id"]}]}}
    # Denetim: firstOrder (ilk sipariş tarihi) TÜM siparişlerden hesaplanmalı — iptal/iade dahil.
    # Aksi halde gerçek ilk siparişi iptal olan müşteri yanlışlıkla "YENİ" sayılıyordu. Ciro/adet
    # ise yalnız GEÇERLİ (dışlanmamış) siparişlerden sayılır.
    _in_range = {"$and": [{"$gte": ["$_report_date", s]}, {"$lte": ["$_report_date", e]}]}
    _valid = {"$not": [{"$in": ["$status", _EXCLUDED_STATUSES]}]}
    _valid_in_range = {"$and": [_in_range, _valid]}
    pipeline = [
        {"$match": merge_match({})},  # ticimax_history ÇİFT kayıtları hariç (aynı müşteriyi/ciroyu şişirir)
        *canonical_order_stages(),
        {"$addFields": {"_report_date": _effective_date_expr()}},
        {"$group": {
            "_id": key,
            "firstOrder": {"$min": "$_report_date"},
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
        # Hangi küme sayılıyor (ekranda yazılır): sipariş STATÜSÜNE göre — iptal, iade
        # (açık talep dahil), kısmi iadeli ve ödenmemiş statüdeki siparişler TAMAMEN hariç;
        # tüm kanallar (kaynak süzgeci yok). Satış kartlarının brüt/net kümesiyle birebir
        # tutmaz.
        "kapsam": "statu_bazli_iptal_iade_odenmemis_haric_tum_kanallar",
    }


@router.get("/gated/stock-restock-audit")
async def gated_stock_restock_audit(
    days: int = Query(180, ge=1, le=730),
    apply: bool = Query(False, description="false=yalnız rapor (dry-run); true=idempotent stok düzeltmesi"),
    limit: int = Query(1000, ge=1, le=5000),
    source: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("reports.stock")),
):
    """GATED (dry-run VARSAYILAN): iptal/iade edilmiş, stoğu GERÇEKTEN DÜŞÜLMÜŞ ama GERİ
    EKLENMEMİŞ siparişleri bulur (stok sızıntısı). apply=true ile idempotent + STOK-ONLY düzeltir.

    GÜVENLİK (CLAUDE.md kritik alan):
      • Yalnız DEDUCT hareketi OLAN (gerçekten düşülmüş) + RESTORE hareketi OLMAYAN sipariş
        seçilir → hiç düşülmemiş (iptalle gelen) sipariş yanlışlıkla +stok ALMAZ.
      • Düzeltme: _restock_order_once(order,'backfill_increment') → _restock_authoritative
        yalnız GERÇEK düşülen delta'yı geri ekler (kalem-bazlı idempotent, guard'lı).
        'backfill_increment' finansal iade (hediye çeki/puan/kupon) TETİKLEMEZ — SADECE STOK.
      • apply=false iken HİÇBİR ŞEY değişmez. Ödeme/sipariş-statüsü invariantlarına dokunmaz.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    cutoff = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    q = {"created_at": {"$gte": cutoff},
         "status": {"$in": _CANCEL_STATUSES + _RETURN_STATUSES_BD}}
    sc = _source_cond(source)
    if sc:
        q.update(sc)
    from .orders import _RESTORE_MOVE_TYPES, _DEDUCT_MOVE_TYPES, _restock_order_once
    orders = [o async for o in db.orders.find(q, {"_id": 0}).limit(limit)]
    suspects = []
    fixed = 0
    restocked_units = 0
    for o in orders:
        oid = o.get("id")
        if not oid:
            continue
        has_deduct = await db.stock_movements.find_one(
            {"order_id": oid, "type": {"$in": _DEDUCT_MOVE_TYPES}}, {"_id": 1})
        if not has_deduct:
            continue  # hiç düşülmemiş (iptalle gelen / stoksuz) → DOKUNMA
        has_restore = await db.stock_movements.find_one(
            {"order_id": oid, "type": {"$in": _RESTORE_MOVE_TYPES}}, {"_id": 1})
        if has_restore:
            continue  # zaten geri eklenmiş
        rec = {"order_number": o.get("order_number"), "status": o.get("status"),
               "units": _order_units(o), "total": round(_fnum(o.get("total")), 2),
               "platform": (o.get("platform") or o.get("marketplace") or "site")}
        if apply:
            try:
                moves = await _restock_order_once(o, "backfill_increment")
                if moves:
                    fixed += 1
                    restocked_units += sum(int((m or {}).get("qty") or 0) for m in moves if isinstance(m, dict))
                    rec["fixed"] = True
            except Exception as _ex:
                rec["error"] = str(_ex)[:200]
        suspects.append(rec)
    return {"apply": apply, "days": days, "source": source or "all",
            "scanned": len(orders), "leaked_count": len(suspects),
            "fixed": fixed, "restocked_units": restocked_units,
            "note": ("DRY-RUN: hiçbir şey değişmedi. apply=true ile stok düzeltilir."
                     if not apply else "UYGULANDI: stok geri eklendi (idempotent, stok-only)."),
            "samples": suspects[:100]}


# =============================================================================
# BAĞIMSIZ SATIŞ DENETİMİ — /health?diag=1&audit_key=... ile okunur.
#
# Panelin rakamına GÜVENİLMEDİĞİ için burada AYRI bir hesap yapılır: rapor
# yardımcıları (_bucket_orders, iade haritaları, kısmi iptal mantığı) KULLANILMAZ.
# Ham db.orders üzerinden düz, denetlenebilir aritmetik. Panelin sonucu da yanına
# konur ki fark varsa görünsün. HİÇBİR YAZMA YAPMAZ.
# =============================================================================
_AUDIT_CANCEL = ["cancelled", "cancel_refunded"]
_AUDIT_RETURN = ["returned", "refunded", "partial_refunded"]
_AUDIT_RETURN_OPEN = ["return_requested", "return_approved", "return_in_transit"]
_AUDIT_UNPAID = ["awaiting_payment", "payment_failed", "pending", "payment_notified"]


async def sales_audit_summary(start_date: str, end_date: str,
                              source: Optional[str] = None) -> dict:
    s, e = _iso_range(start_date, end_date)

    # --- Popülasyon: etkin tarih (pazaryeri sipariş tarihi ?? oluşturulma) aralıkta ---
    _eff = {"$ifNull": ["$marketplace_order_date", "$created_at"]}
    _in_range = {"$and": [{"$gte": ["$_eff", s]}, {"$lte": ["$_eff", e]}]}
    base = [
        {"$addFields": {"_eff": _eff}},
        {"$match": {"$expr": _in_range}},
    ]
    # audit_source (kanal süzgeci) eskiden yalnız panel özetine geçiyordu; bağımsız
    # hesap, ödeme/statü/platform kırılımları hep TÜM kanalları sayıyordu.
    _src_m = _source_cond(source)
    if _src_m:
        base.append({"$match": _src_m})
    # GEÇERSİZ SAYI KORUMASI: canlı veride tutarı NaN olan 8 eski kayıt var (2025
    # tarihli, kalemi olmayan, 11 Haziran 2026'da toplu iptal edilmiş Ticimax
    # artıkları). Mongo'da NaN toplanınca sonucun TAMAMI NaN oluyor ve haziranın
    # iptal toplamı ölçülemiyordu. MongoDB eşitlikte NaN == NaN kabul ettiği için
    # bu üç değer doğrudan sıfırlanabiliyor. Sipariş ADET olarak sayılmaya devam eder.
    _tl_ham = {"$convert": {"input": "$total", "to": "double", "onError": 0, "onNull": 0}}
    _tl = {"$cond": [{"$in": [_tl_ham, [float("nan"), float("inf"), float("-inf")]]},
                     0, _tl_ham]}

    async def _one(stages):
        r = await db.orders.aggregate(stages, allowDiskUse=True).to_list(1)
        r = r[0] if r else {}
        return {"adet": int(r.get("adet") or 0), "tutar": round(float(r.get("tutar") or 0), 2)}

    async def _by(field, extra_match=None):
        st = list(base)
        if extra_match:
            st.append({"$match": extra_match})
        st += [{"$group": {"_id": {"$ifNull": [f"${field}", "(boş)"]},
                           "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}},
               {"$sort": {"tutar": -1}}]
        return [{"deger": r.get("_id") or "(boş)", "adet": int(r.get("adet") or 0),
                 "tutar": round(float(r.get("tutar") or 0), 2)}
                async for r in db.orders.aggregate(st, allowDiskUse=True)]

    async def _sum(match=None):
        st = list(base)
        if match:
            st.append({"$match": match})
        st.append({"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}})
        return await _one(st)

    ham = await _sum()

    # Aynı sipariş numarasından birden fazla belge varsa (pazaryeri ikizi / ticimax
    # kopyası) tekilleştirilmiş sayım — çift saymayı görebilmek için AYRI verilir.
    tekil = await _one(base + [
        {"$group": {"_id": {"$ifNull": ["$order_number", {"$toString": "$_id"}]},
                    "t": {"$first": _tl}}},
        {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": "$t"}}},
    ])

    iptal = await _sum({"status": {"$in": _AUDIT_CANCEL}})
    iade = await _sum({"status": {"$in": _AUDIT_RETURN}})
    iade_acik = await _sum({"status": {"$in": _AUDIT_RETURN_OPEN}})
    odenmemis = await _sum({"status": {"$in": _AUDIT_UNPAID}})

    # KALEM BAZLI AYIRMA (doğru muhasebe): kısmi iadede sipariş BÜTÜN olarak iadeye
    # yazılamaz — iade edilen kısım iadeye, müşterinin ödediği ve elinde kalan kısım
    # satışa gider. Gider pusulasının totals.net'i TAM OLARAK iade edilen tutardır;
    # brütten yalnız o düşülünce kalan kısım satışta kalır.
    _gp_net_e = {"$convert": {"input": "$totals.net", "to": "double",
                              "onError": 0, "onNull": 0}}
    _nos = []
    try:
        async for _r in db.orders.aggregate(
                base + [{"$match": {"order_number": {"$nin": [None, ""]}}},
                        {"$group": {"_id": "$order_number"}}], allowDiskUse=True):
            _nos.append(_r["_id"])
    except Exception:
        _nos = []
    iade_kalem = {"adet": 0, "tutar": 0.0}
    try:
        _r = await db.gider_pusulasi.aggregate([
            {"$match": {"order_number": {"$in": _nos}}},
            {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _gp_net_e}}},
        ], allowDiskUse=True).to_list(1)
        _r = _r[0] if _r else {}
        iade_kalem = {"adet": int(_r.get("adet") or 0),
                      "tutar": round(float(_r.get("tutar") or 0), 2)}
    except Exception as _ie:
        iade_kalem = {"hata": str(_ie)[:120]}

    _ik_tutar = float(iade_kalem.get("tutar") or 0)
    net = round(ham["tutar"] - iptal["tutar"] - _ik_tutar - odenmemis["tutar"], 2)
    net_durum_bazli = round(ham["tutar"] - iptal["tutar"] - iade["tutar"] - odenmemis["tutar"], 2)

    # --- Bu AY İÇİNDE yapılan iptal/iade (sipariş hangi ay olursa olsun) ---
    async def _by_action(statuses, date_field, fallback):
        st = [
            {"$match": {"status": {"$in": statuses}}},
            {"$addFields": {"_ad": {"$ifNull": [f"${date_field}", f"${fallback}"]}}},
            {"$match": {"$expr": {"$and": [{"$gte": ["$_ad", s]}, {"$lte": ["$_ad", e]}]}}},
            {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}},
        ]
        return await _one(st)

    # --- PAZARYERİ MUTABAKATI: bizim sayımız pazaryeri paneliyle neden farklı? ---
    # Aynı dönem için FARKLI sayma ölçütleri yan yana konur; hangisinin panelle
    # tuttuğu görülünce fark tek bakışta anlaşılır.
    async def _pazaryeri_mutabakat(plat: str) -> dict:
        _p = {"platform": plat}
        out = {}
        try:
            # a) Sipariş tarihine göre (pazaryerinin kendi orderDate'i) — bizim ölçütümüz
            out["siparis_tarihine_gore"] = await _one([
                {"$match": _p},
                {"$addFields": {"_d": {"$ifNull": ["$marketplace_order_date", "$created_at"]}}},
                {"$match": {"$expr": {"$and": [{"$gte": ["$_d", s]}, {"$lte": ["$_d", e]}]}}},
                {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}}])
            # b) Bize DÜŞTÜĞÜ (senkron) tarihe göre
            out["senkron_tarihine_gore"] = await _one([
                {"$match": {**_p, "created_at": {"$gte": s, "$lte": e}}},
                {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}}])
            # c) Sipariş tarihi HİÇ YOK olanlar (yalnız senkron tarihiyle sayılabilenler)
            out["siparis_tarihi_olmayan"] = await _one([
                {"$match": {**_p, "$or": [{"marketplace_order_date": {"$exists": False}},
                                          {"marketplace_order_date": None},
                                          {"marketplace_order_date": ""}],
                            "created_at": {"$gte": s, "$lte": e}}},
                {"$group": {"_id": None, "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}}])
            # d) Statü kırılımı (panel genelde iptalleri ayrı gösterir)
            out["statu"] = [
                {"deger": r.get("_id") or "(boş)", "adet": int(r.get("adet") or 0),
                 "tutar": round(float(r.get("tutar") or 0), 2)}
                async for r in db.orders.aggregate([
                    {"$match": _p},
                    {"$addFields": {"_d": {"$ifNull": ["$marketplace_order_date", "$created_at"]}}},
                    {"$match": {"$expr": {"$and": [{"$gte": ["$_d", s]}, {"$lte": ["$_d", e]}]}}},
                    {"$group": {"_id": "$status", "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}},
                    {"$sort": {"adet": -1}}], allowDiskUse=True)]
            # e) GÜN GÜN (TR günü) — panelle satır satır karşılaştırmak için
            out["gunluk"] = {}
            async for r in db.orders.aggregate([
                    {"$match": _p},
                    {"$addFields": {"_d": {"$ifNull": ["$marketplace_order_date", "$created_at"]}}},
                    {"$match": {"$expr": {"$and": [{"$gte": ["$_d", s]}, {"$lte": ["$_d", e]}]}}},
                    {"$addFields": {"_g": {"$substrBytes": [
                        {"$dateToString": {"format": "%Y-%m-%d", "timezone": "+03:00",
                                           "date": {"$toDate": "$_d"}}}, 0, 10]}}},
                    {"$group": {"_id": "$_g", "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}},
                    {"$sort": {"_id": 1}}], allowDiskUse=True):
                out["gunluk"][str(r.get("_id"))] = {
                    "adet": int(r.get("adet") or 0),
                    "tutar": round(float(r.get("tutar") or 0), 2)}
            # f) Tekil sipariş numarası (pazaryeri PAKET bazlı sayarsa fark buradan çıkar)
            _u = await db.orders.aggregate([
                {"$match": _p},
                {"$addFields": {"_d": {"$ifNull": ["$marketplace_order_date", "$created_at"]}}},
                {"$match": {"$expr": {"$and": [{"$gte": ["$_d", s]}, {"$lte": ["$_d", e]}]}}},
                {"$group": {"_id": "$order_number"}},
                {"$count": "n"}], allowDiskUse=True).to_list(1)
            out["tekil_siparis_no"] = int((_u[0] if _u else {}).get("n") or 0)
        except Exception as _me:
            out["hata"] = f"{type(_me).__name__}: {str(_me)[:200]}"
        out["not"] = ("Pazaryeri paneli genelde PAKET (shipment package) sayar ve kendi "
                      "sipariş tarihine göre filtreler. Farkı bulmak için: panelin "
                      "rakamı hangi satırla tutuyorsa sayma ölçütü odur.")
        return out

    iptal_bu_ay = await _by_action(_AUDIT_CANCEL, "cancelled_at", "updated_at")
    iade_bu_ay = await _by_action(_AUDIT_RETURN, "refund_paid_at", "updated_at")

    # --- GERÇEK İADE SAYIMI ---------------------------------------------------
    # Sipariş DURUMUNDAN saymak yanıltıcı: kısmi iadede sipariş "delivered" kalır,
    # durumu değişmez → iade görünmez. Muhasebede her iade için GİDER PUSULASI
    # kesildiğinden otantik kaynak odur. Site iadeleri customer_returns'ta,
    # pazaryeri iadeleri trendyol_claims'te tutulur; üçü de ayrı ayrı sayılır.
    async def _count(coll, match, amount_expr=None):
        try:
            pipe = [{"$match": match},
                    {"$group": {"_id": None, "adet": {"$sum": 1},
                                "tutar": {"$sum": amount_expr or 0}}}]
            r = await coll.aggregate(pipe, allowDiskUse=True).to_list(1)
            r = r[0] if r else {}
            return {"adet": int(r.get("adet") or 0),
                    "tutar": round(float(r.get("tutar") or 0), 2)}
        except Exception as _ce:
            return {"hata": f"{type(_ce).__name__}: {str(_ce)[:120]}"}

    _gp_net_ham = {"$convert": {"input": "$totals.net", "to": "double",
                                "onError": 0, "onNull": 0}}
    _gp_net = {"$cond": [{"$in": [_gp_net_ham, [float("nan"), float("inf"),
                                                float("-inf")]]}, 0, _gp_net_ham]}
    _dt_all = {"created_at": {"$gte": s, "$lte": e}}
    # KESİNLEŞMİŞ = koçan numarası atanmış. Raporlar yalnız bunu iade sayar.
    _NUMARALI = {"$or": [{"number": {"$exists": True, "$ne": None}},
                         {"display_number": {"$exists": True, "$nin": ["", None]}}]}
    _dt = {**_dt_all, **_NUMARALI}
    gp_toplam = await _count(db.gider_pusulasi, _dt, _gp_net)
    gp_hepsi = await _count(db.gider_pusulasi, _dt_all, _gp_net)
    gp_kaynak = []
    try:
        async for r in db.gider_pusulasi.aggregate([
            {"$match": _dt},
            {"$group": {"_id": {"$ifNull": ["$source", "(boş)"]},
                        "adet": {"$sum": 1}, "tutar": {"$sum": _gp_net}}},
            {"$sort": {"adet": -1}},
        ], allowDiskUse=True):
            gp_kaynak.append({"kaynak": r.get("_id") or "(boş)",
                              "adet": int(r.get("adet") or 0),
                              "tutar": round(float(r.get("tutar") or 0), 2)})
    except Exception as _ge:
        gp_kaynak = [{"hata": str(_ge)[:120]}]

    iade_kaynaklari = {
        "gider_pusulasi": gp_toplam,
        "gider_pusulasi_TUM_KAYITLAR": gp_hepsi,
        "numarasiz_pusula": {
            "adet": int((gp_hepsi or {}).get("adet", 0)) - int((gp_toplam or {}).get("adet", 0)),
            "tutar": round(float((gp_hepsi or {}).get("tutar", 0))
                           - float((gp_toplam or {}).get("tutar", 0)), 2),
            "not": ("Koçan numarası ATANMAMIŞ pusulalar. Kesinleşmiş iade sayılmaz; "
                    "raporlarda normal satış olarak kalır."),
        },
        "gider_pusulasi_kaynak_bazinda": gp_kaynak,
        # Koçan numarası YALNIZ gider pusulasında var; bu iki koleksiyon numara süzgeciyle
        # sayılınca her zaman 0 çıkıyordu → yalnız tarih süzgeci.
        "site_iade_kaydi": await _count(db.customer_returns, _dt_all),
        "trendyol_talebi": await _count(db.trendyol_claims, _dt_all),
        "not": ("Otantik sayım GİDER PUSULASIDIR. Sipariş durumundan sayım kısmi "
                "iadeleri kaçırır (sipariş 'delivered' kalır)."),
    }

    # --- "Bu ay N iade mi oldu?" — belge sayısı ≠ sipariş sayısı ≠ bu ayın satışı ---
    # Kart "N sipariş" yazıyor ama aslında PUSULA BELGESİ sayıyor. Bir siparişe birden
    # çok pusula kesilmişse o sipariş birden çok sayılır. Ayrıca dönem muhasebesinde
    # pusulanın bağlı olduğu sipariş BAŞKA BİR AYA ait olabilir — "bu ayın iade oranı"
    # diye okunursa yanıltır. Üçünü de ayrı ayrı ölçüyoruz.
    try:
        _gp_rows = []
        _gp_tutar = {}          # sipariş no -> bu dönemde kesilen pusula TUTARI
        _gp_liste = {}          # sipariş no -> [her pusulanın tutarı] (mükerrer teşhisi)
        async for _g in db.gider_pusulasi.find(
                _dt, {"_id": 0, "order_number": 1, "created_at": 1, "totals": 1}):
            _on = str(_g.get("order_number") or "")
            _gp_rows.append(_on)
            try:
                _amt = float(((_g.get("totals") or {}).get("net")) or 0)
            except Exception:
                _amt = 0.0
            if _on:
                _gp_tutar[_on] = _gp_tutar.get(_on, 0.0) + _amt
                _gp_liste.setdefault(_on, []).append(round(_amt, 2))
        _nos = [x for x in _gp_rows if x]
        _uniq = sorted(set(_nos))
        _tek = {}
        _otot = {}
        for i in range(0, len(_uniq), 5000):
            async for _o in db.orders.find(
                    {"order_number": {"$in": _uniq[i:i + 5000]}},
                    {"_id": 0, "order_number": 1, "marketplace_order_date": 1,
                     "created_at": 1, "total": 1}):
                _d = str(_o.get("marketplace_order_date") or _o.get("created_at") or "")[:7]
                _tek[str(_o.get("order_number"))] = _d or "?"
                try:
                    _otot[str(_o.get("order_number"))] = _fnum(_o.get("total"))
                except Exception:
                    _otot[str(_o.get("order_number"))] = 0.0
        _ay_dagilim = {}
        _ay_tutar = {}
        for _n in _uniq:
            _k = _tek.get(_n, "sipariş bulunamadı")
            _ay_dagilim[_k] = _ay_dagilim.get(_k, 0) + 1
            _ay_tutar[_k] = round(_ay_tutar.get(_k, 0.0) + _gp_tutar.get(_n, 0.0), 2)
        # DİKKAT: s, TR gününün UTC karşılığıdır (1 Eylül TR = 31 Ağustos 21:00 UTC),
        # bu yüzden str(s)[:7] BİR ÖNCEKİ ayı verir ve kırılım yanlış etiketlenirdi.
        # TR yerel aya çevir.
        try:
            _bu_ay = (datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                      + timedelta(hours=3)).strftime("%Y-%m")
        except Exception:
            _bu_ay = str(s)[:7]
        # KOHORT NET: "bu ayın SATIŞININ net'i" — dönem net'inden farklıdır.
        # Dönem net'i, bu ay kesinleşen TÜM iadeleri düşer (önceki ayların siparişleri
        # dahil). Pazaryeri panelleri ise iadeyi siparişin SATILDIĞI aydan düşer.
        # İki rakam farklı soruları cevaplar; kıyas yapılabilsin diye ikisi de verilir.
        # ── MÜKERRER PUSULA TEŞHİSİ ─────────────────────────────────────────
        # Aynı siparişe birden çok gider pusulası kesilmiş olabilir. İKİ FARKLI
        # durum var ve ayırt edilmeden düzeltme yapılamaz:
        #   (a) GERÇEK kısmi iade — farklı ürünler ayrı zamanlarda iade edilmiş;
        #       toplam sipariş tutarını AŞMAZ, iki belge de geçerlidir.
        #   (b) MÜKERRER kayıt — aynı iade iki kez yazılmış; pusula toplamı
        #       siparişin tutarını AŞAR. Bu, iadeyi şişirir.
        # Ölçüt: sipariş tutarını aşan kısım fazladan sayılmış demektir.
        _muk = []
        _muk_fazla = 0.0
        for _n, _lst in _gp_liste.items():
            if len(_lst) < 2:
                continue
            _tp = round(sum(_lst), 2)
            _ot = round(float(_otot.get(_n) or 0), 2)
            _asim = round(_tp - _ot, 2) if (_ot > 0.005 and _tp > _ot + 0.01) else 0.0
            if _asim > 0:
                _muk_fazla += _asim
            _muk.append({"siparis": _n, "pusula_sayisi": len(_lst),
                         "tutarlar": _lst, "pusula_toplami": _tp,
                         "siparis_tutari": _ot,
                         "siparisi_asan_tutar": _asim,
                         "ayni_tutar_tekrari": len(_lst) != len(set(_lst))})
        _muk.sort(key=lambda x: -x["siparisi_asan_tutar"])
        _mukerrer = {
            "siparis_sayisi": len(_muk),
            "siparisi_ASAN_siparis": sum(1 for x in _muk if x["siparisi_asan_tutar"] > 0),
            "toplam_FAZLA_tutar": round(_muk_fazla, 2),
            "ayni_tutar_tekrari_olan": sum(1 for x in _muk if x["ayni_tutar_tekrari"]),
            "ornekler": _muk[:30],
            "not": ("pusula_toplami sipariş tutarını AŞIYORSA iade şişmiş demektir "
                    "(mükerrer kayıt). Aşmıyorsa büyük ihtimalle GERÇEK kısmi iade — "
                    "silinmemeli. 'toplam_FAZLA_tutar' raporlardan düşülmesi gereken "
                    "fazlalıktır."),
        }

        _onceki_ay_iadesi = round(sum(v for k, v in _ay_tutar.items() if k != _bu_ay), 2)
        _bu_ay_iadesi = round(_ay_tutar.get(_bu_ay, 0.0), 2)
        iade_kaynaklari["belge_vs_siparis"] = {
            "pusula_belgesi": len(_gp_rows),
            "tekil_siparis": len(_uniq),
            "siparis_no_bos_belge": len(_gp_rows) - len(_nos),
            "ayni_siparise_birden_cok_belge": len(_nos) - len(_uniq),
            "iade_edilen_siparisin_VERILDIGI_ay": dict(sorted(_ay_dagilim.items())),
            "iade_TUTARI_siparisin_VERILDIGI_aya_gore": dict(sorted(_ay_tutar.items())),
            "mukerrer_pusula": _mukerrer,
            "bu_ayin_satisindan_iade": _bu_ay_iadesi,
            "ONCEKI_AYLARIN_satisindan_iade": _onceki_ay_iadesi,
            "not": ("pusula_belgesi = kartta 'N sipariş' diye görünen sayı. Gerçek "
                    "tekil sipariş adedi 'tekil_siparis'. 'iade_edilen_siparisin_"
                    "VERILDIGI_ay' dağılımı, bu ayki iadelerin kaçının bu ayın "
                    "satışından geldiğini gösterir — iade ORANI ancak bununla "
                    "hesaplanabilir, dönem toplamıyla değil. "
                    "'ONCEKI_AYLARIN_satisindan_iade' bu ayın NET'ini düşüren ama bu "
                    "ayın satışıyla ilgisi olmayan tutardır; pazaryeri panelleriyle "
                    "kıyas yaparken bu kadarını geri eklemek gerekir."),
        }
    except Exception as _be:
        iade_kaynaklari["belge_vs_siparis"] = {"hata": str(_be)[:200]}

    # --- Veri sağlığı: raporu çarpıtabilecek kayıtlar ---
    tutarsiz = await _sum({"$or": [{"total": {"$exists": False}}, {"total": None},
                                   {"total": {"$lte": 0}}]})
    kalemsiz = await _sum({"$or": [{"items": {"$exists": False}}, {"items": {"$size": 0}}]})
    _dup = await db.orders.aggregate(base + [
        {"$match": {"order_number": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$order_number", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$group": {"_id": None, "adet": {"$sum": 1}, "fazla": {"$sum": {"$subtract": ["$n", 1]}}}},
    ], allowDiskUse=True).to_list(1)
    _dup = _dup[0] if _dup else {}

    # --- Panelin kendi rakamı (kıyas için) ---
    panel = {}
    try:
        _full = await sales_summary(start_date=start_date, end_date=end_date,
                                    source=source, current_user={"id": "audit"})
        panel = _full.get("period") or {}
    except Exception as _pe:
        panel = {"hata": str(_pe)[:200]}

    # --- TEŞHİS: panelin boru hattı AŞAMA AŞAMA, platform kırılımıyla.
    # Hangi aşamada hangi platformun düştüğünü gösterir (ciro kaybı avı).
    # Kopya süzgeci (3_ticimax_dedup aşaması) soğuk süreçte boş kalmasın.
    try:
        await load_dup_order_numbers()
    except Exception:
        pass

    async def _stage(extra_stages, extra_match=None):
        st = [{"$addFields": {"_ed": _effective_date_expr()}},
              {"$match": {"_ed": {"$gte": s, "$lt": e}}}]
        if _src_m:
            st.append({"$match": _src_m})
        if extra_match:
            st.append({"$match": extra_match})
        st += list(extra_stages)
        st += [{"$group": {"_id": {"$ifNull": ["$platform", "(boş)"]},
                           "adet": {"$sum": 1}, "tutar": {"$sum": _tl}}},
               {"$sort": {"tutar": -1}}]
        return {str(r.get("_id") or "(boş)"): {"adet": int(r.get("adet") or 0),
                                               "tutar": round(float(r.get("tutar") or 0), 2)}
                async for r in db.orders.aggregate(st, allowDiskUse=True)}

    try:
        _asama = {
            "1_tarih": await _stage([]),
            "2_odenmemis_elendi": await _stage([], {"status": {"$nin": _AUDIT_UNPAID}}),
            "3_ticimax_dedup": await _stage([], merge_match({"status": {"$nin": _AUDIT_UNPAID}})),
            "4_tek_belge": await _stage(list(canonical_order_stages()),
                                        merge_match({"status": {"$nin": _AUDIT_UNPAID}})),
        }
    except Exception as _ae:
        _asama = {"hata": f"{type(_ae).__name__}: {str(_ae)[:200]}"}

    # TEŞHİS 2: site siparişlerinin ham tarih alanları ve panelin hesapladığı _ed.
    # Tarih eşleşmesi neden tutmuyor, gözle görülecek.
    try:
        _ornek = []
        async for _o in db.orders.aggregate([
            {"$match": {"platform": {"$nin": ["trendyol", "hepsiburada", "amazon", "n11", "temu"]}}},
            {"$addFields": {"_ed_panel": _effective_date_expr(),
                            "_ed_bagimsiz": {"$ifNull": ["$marketplace_order_date", "$created_at"]}}},
            {"$sort": {"created_at": -1}},
            {"$limit": 5},
            {"$project": {"_id": 0, "order_number": 1, "platform": 1, "status": 1,
                          "marketplace_order_date": 1, "created_at": 1,
                          "_ed_panel": 1, "_ed_bagimsiz": 1,
                          "_tip_mod": {"$type": "$marketplace_order_date"},
                          "_tip_ca": {"$type": "$created_at"}}},
        ], allowDiskUse=True):
            _ornek.append(_o)
        _asama["site_ornekleri"] = _ornek
        _asama["aralik_sinirlari"] = {"s": s, "e": e}
    except Exception as _pe:
        _asama["ornek_hata"] = f"{type(_pe).__name__}: {str(_pe)[:200]}"

    return {
        "aralik": {"baslangic": start_date, "bitis": end_date, "utc_sinir": [s, e]},
        "panel_boru_hatti_asamalari": _asama,
        "bagimsiz_hesap": {
            "tum_siparisler": ham,
            "siparis_no_ile_tekil": tekil,
            "iptal": iptal,
            "iade_kalem_bazli": iade_kalem,
            "iade_durum_bazli": iade,
            "iade_surecte": iade_acik,
            "odenmemis": odenmemis,
            "net_tutar": net,
            "net_aciklama": ("tüm siparişler − iptal − İADE EDİLEN TUTAR (gider pusulası) "
                             "− ödenmemiş. Kısmi iadede yalnız iade edilen kısım düşülür, "
                             "müşterinin ödediği kalan kısım satışta kalır."),
            "net_durum_bazli_yanlis": net_durum_bazli,
            "net_durum_bazli_notu": ("Sipariş durumundan sayım: kısmi iadede siparişin "
                                     "TAMAMI iadeye yazılır → satış olduğundan az görünür. "
                                     "Kıyas için bırakıldı."),
        },
        "iade_gercek_sayim": iade_kaynaklari,
        "bu_ay_yapilan": {
            "iptal": iptal_bu_ay,
            # DİKKAT: bu satır siparişin İADE ÖDEME tarihinden sayar (eski ölçüm, kıyas
            # için duruyor). Raporların kullandığı OTANTİK sayım gider pusulasıdır —
            # aşağıdaki alan panelle birebir aynı olmalıdır (iade_gercek_sayim ile de).
            "iade_siparis_durumundan": iade_bu_ay,
            "iade": gp_toplam,
            "not": "işlem tarihi bu aralıkta olanlar (sipariş tarihi fark etmez). "
                   "'iade' = gider pusulası (panelin kullandığı temel); "
                   "'iade_siparis_durumundan' eski ölçüm, yalnız kıyas içindir.",
        },
        "odeme_durumu": await _by("payment_status"),
        "siparis_durumu": await _by("status"),
        "platform": await _by("platform"),
        "pazaryeri_mutabakat": {"trendyol": await _pazaryeri_mutabakat("trendyol")},
        "veri_sagligi": {
            "tutari_yok_veya_sifir": tutarsiz,
            "kalemi_yok": kalemsiz,
            "cift_siparis_no": {"benzersiz_no": int(_dup.get("adet") or 0),
                                "fazla_belge": int(_dup.get("fazla") or 0)},
        },
        "panel_ozeti": panel,
        "uretildi": datetime.now(timezone.utc).isoformat(),
    }
