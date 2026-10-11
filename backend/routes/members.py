"""
Admin → Üyeler (Members) module.

Provides CRUD + analytics for registered customers. A "member" here is a
document in the `users` collection that is NOT an admin.

Endpoints (all admin-protected):
  GET    /api/admin/members                – list, filters, pagination
  GET    /api/admin/members/stats          – totals, segments, acquisition sources
  GET    /api/admin/members/{mid}          – detail with orders + addresses
  POST   /api/admin/members                – create manually
  PUT    /api/admin/members/{mid}          – update
  DELETE /api/admin/members/{mid}          – delete
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from datetime import datetime, timezone, timedelta
from typing import Optional

from .deps import (db, require_admin, hash_password, generate_id, logger, require_permission,
                   get_effective_permissions, write_audit_log)

router = APIRouter(prefix="/admin/members", tags=["admin-members"])


from security import contact_pii as _contact  # noqa: E402


def _member_search_or(search) -> list:
    """Üye araması: e-posta / ad / soyad kısmi; TELEFON yalnız TAM numarayla (hash eşleşmesi —
    telefon at-rest şifreli olduğundan parça parça taranamaz)."""
    import re as _re
    _s = _re.escape(str(search).strip())
    ors = [
        {"email": {"$regex": _s, "$options": "i"}},
        {"first_name": {"$regex": _s, "$options": "i"}},
        {"last_name": {"$regex": _s, "$options": "i"}},
    ]
    if len(_contact.phone_digits(search)) >= 10:
        _pq = _contact.phone_lookup("phone", search)
        if _pq:
            ors.extend(_pq["$or"])
    return ors


def _member_order_match(user: dict) -> dict:
    """Üyenin siparişlerini yakalayan koşul: user_id VE e-posta.
    Misafirken verilen (user_id=null) siparişler yalnız user_id ile aranınca
    üye detayında hiç görünmez — e-posta eşleşmesi aynı kişinin geçmiş
    siparişlerini güvenle kapsar (customer.py'deki _owner_or_clauses ile aynı ilke)."""
    import re as _re
    uid = user.get("id")
    ors = [{"user_id": uid}]
    email = (user.get("email") or "").strip()
    if email:
        rx = {"$regex": f"^{_re.escape(email)}$", "$options": "i"}
        ors += [{"shipping_address.email": rx}, {"email": rx}, {"billing_address.email": rx}]
    return {"$or": ors}


# NET CİRO KURALI (kullanıcı isteği): üye istatistikleri / segment (VIP vb.) İPTAL ve İADE
# DÜŞÜLDÜKTEN SONRA hesaplanır. Sayılmayan sipariş durumları: iptal, tam iade, ödemesi
# gelmemiş/başarısız ve iade sürecindeki siparişler. Sayılan siparişte KISMİ iade varsa
# (partial_refunded vb.) onaylanmış iade kaydının tutarı (customer_returns.refund_amount)
# harcamadan düşülür (tam-iade siparişi zaten sayılmadığı için çift düşüm yok).
_NON_REVENUE_STATUSES = [
    "cancelled", "cancel_refunded", "returned", "refunded",
    "awaiting_payment", "payment_failed", "failed", "payment_notified",
    "return_requested", "return_approved", "return_in_transit",
]
_NON_REVENUE_PAYMENT = ["expired", "failed", "refunded"]
_RETURN_COUNTED_STATUSES = ["approved", "return_approved", "refunded", "partial_refunded", "completed"]


def _revenue_order_match() -> dict:
    return {"status": {"$nin": _NON_REVENUE_STATUSES},
            "payment_status": {"$nin": _NON_REVENUE_PAYMENT}}


def _segment_for(net_spent: float, orders: int) -> str:
    return "vip" if net_spent >= 5000 else ("returning" if orders >= 2 else ("new" if orders == 1 else "prospect"))


async def _annotate(user: dict) -> dict:
    uid = user.get("id")
    # Aggregate order stats
    # NET: iptal/iade/ödenmemiş siparişler sayılmaz; sayılan siparişteki kısmi iade tutarı düşülür.
    pipeline = [
        {"$match": {"$and": [_member_order_match(user), _revenue_order_match()]}},
        {"$lookup": {"from": "customer_returns", "localField": "id", "foreignField": "order_id", "as": "_rets"}},
        {"$addFields": {"_ret_amt": {"$sum": {"$map": {
            "input": {"$filter": {"input": "$_rets", "as": "r",
                                  "cond": {"$in": [{"$ifNull": ["$$r.status", ""]}, _RETURN_COUNTED_STATUSES]}}},
            "as": "r", "in": {"$ifNull": ["$$r.refund_amount", 0]}}}}}},
        {
            "$group": {
                "_id": None,
                "orders": {"$sum": 1},
                "gross": {"$sum": {"$ifNull": ["$total", 0]}},
                "ret_amt": {"$sum": "$_ret_amt"},
                "last_order_at": {"$max": "$created_at"},
            }
        },
    ]
    agg = None
    async for row in db.orders.aggregate(pipeline):
        agg = row
    _gross = float(agg["gross"]) if agg else 0.0
    _ret = float(agg.get("ret_amt") or 0) if agg else 0.0
    user["orders_count"] = int(agg["orders"]) if agg else 0
    user["gross_spent"] = round(_gross, 2)
    user["total_spent"] = round(max(0.0, _gross - _ret), 2)
    user["last_order_at"] = agg.get("last_order_at") if agg else None

    # Segment — NET harcamaya göre (iptal/iade düşülmüş)
    user["segment"] = _segment_for(user["total_spent"], user["orders_count"])
    return user


@router.get("")
async def list_members(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    search: Optional[str] = None,
    segment: Optional[str] = None,
    source: Optional[str] = None,
    sort: Optional[str] = "created",
    dir: Optional[str] = "desc",
    date_field: Optional[str] = "created",   # created (Katılım) | last_order (Son Sipariş)
    start: Optional[str] = None,
    end: Optional[str] = None,
    current_user: dict = Depends(require_permission("customers.view")),
):
    # KVKK: silinmiş (anonimleştirilmiş) üye iskeletleri listede/dışa aktarımda görünmez.
    query: dict = {"is_admin": {"$ne": True}, "kvkk_deleted": {"$ne": True}}
    if search:
        query["$or"] = _member_search_or(search)
    if source:
        query["acquisition_source"] = source

    # TARİH FİLTRESİ — Katılım (created_at) veya Son Sipariş (cached_last_order) aralığı.
    _dfld = "cached_last_order" if (date_field == "last_order") else "created_at"
    if start or end:
        _dq = {}
        if start:
            _dq["$gte"] = start
        if end:
            _dq["$lte"] = end if len(end) > 10 else end + "T23:59:59"
        query[_dfld] = _dq

    skip = (page - 1) * limit

    # HIZLANDIRMA: sipariş istatistikleri artık kullanıcı belgesinde ÖNBELLEKTE (cached_*),
    # _refresh_member_stats ile periyodik/isteğe-bağlı tek geçişte yazılır. Liste/segment BU
    # alanları okur → korelasyonlu $lookup (O(üye×sipariş)) KALDIRILDI. Önbellek YOKSA 0/prospect.
    # (Üye 360 detayı tam eşleşmeyi ayrıca canlı yapar; liste görünümü için önbellek yeterli+hızlı.)
    # addFields SIRALAMADAN ÖNCE — türetilmiş alanlara (aov) göre de sıralanabilsin. cached_* zaten
    # kullanıcı belgesinde; aov = harcama/sipariş (0'a bölme korumalı).
    _cached_fields = [{"$addFields": {
        "orders_count": {"$ifNull": ["$cached_orders", 0]},
        "total_spent": {"$ifNull": ["$cached_spent", 0]},          # NET (onaylı iadeler düşülmüş)
        "gross_spent": {"$ifNull": ["$cached_gross_spent", {"$ifNull": ["$cached_spent", 0]}]},  # BRÜT
        "last_order_at": "$cached_last_order",
        "segment": {"$ifNull": ["$cached_segment", "prospect"]},
        "returns_count": {"$ifNull": ["$cached_returns", 0]},
        "returns_amount": {"$ifNull": ["$cached_returns_amount", 0]},
        "aov": {"$cond": [{"$gt": [{"$ifNull": ["$cached_orders", 0]}, 0]},
                          {"$divide": [{"$ifNull": ["$cached_spent", 0]}, "$cached_orders"]}, 0]},
    }}]
    _project = {"$project": {"_id": 0, "password": 0}}

    if segment:
        seg_match = ({"$or": [{"cached_segment": "prospect"}, {"cached_segment": {"$exists": False}}]}
                     if segment == "prospect" else {"cached_segment": segment})
        query = {"$and": [query, seg_match]}

    # SIRALAMA — tüm sütunlar artan/azalan (sunucu-taraflı → tüm sayfalarda geçerli).
    _sort_map = {
        "name": "first_name", "orders": "cached_orders", "spent": "cached_spent",
        "gross": "cached_gross_spent",
        "aov": "aov", "last_order": "cached_last_order", "created": "created_at",
        "segment": "cached_segment", "source": "acquisition_source",
        "returns": "cached_returns",
    }
    _sf = _sort_map.get((sort or "created"), "created_at")
    _dir = -1 if (dir or "desc").lower() != "asc" else 1

    total = await db.users.count_documents(query)
    pipeline = [
        {"$match": query},
        *_cached_fields,
        {"$sort": {_sf: _dir, "_id": 1}},  # _id ikincil → sayfalama kararlı
        {"$skip": skip},
        {"$limit": limit},
        _project,
    ]
    items = [r async for r in db.users.aggregate(pipeline, allowDiskUse=True)]
    # KVKK: üye listesinde telefon yalnız son 2 hane (tam değer: GET /admin/members/{id}/contact).
    _contact.mask_docs(items, "user")
    return {"items": items, "total": total, "page": page, "pages": (total + limit - 1) // limit}


async def _refresh_member_stats() -> dict:
    """orders → users.cached_* (orders/spent/last_order/segment) TEK GEÇİŞTE yazar (bulk).
    user_id ile eşleşen (kayıtlı üye) sipariş­leri baz alır — liste/segment görünümü için hızlı ve
    yeterli. Misafir-email-only siparişler hariç (üye 360 detayı tam eşleşmeyi ayrıca yapar)."""
    from pymongo import UpdateOne
    # NET: iptal/iade/ödenmemiş siparişler sayılmaz (bkz. _revenue_order_match).
    pipeline = [
        {"$match": {"user_id": {"$ne": None}, **_revenue_order_match()}},
        {"$group": {"_id": "$user_id", "o": {"$sum": 1},
                    "t": {"$sum": {"$ifNull": ["$total", 0]}}, "last": {"$max": "$created_at"}}},
    ]
    # İADE önbelleği: customer_returns → orders.user_id join ile üye başına iade SAYISI + TUTARI.
    # (İade kaydı yalnız siparişi olan üyede olur → orders döngüsünde eşleşir; 0'a düşen üye de
    #  orders döngüsünde güncellenir çünkü siparişi vardır.)
    ret_map: dict = {}
    # Sayılan (kısmi iadeli) siparişlerdeki onaylı iade tutarı → NET harcamadan düşülür.
    ded_map: dict = {}
    try:
        async for row in db.customer_returns.aggregate([
            {"$lookup": {"from": "orders", "localField": "order_id",
                         "foreignField": "id", "as": "o"}},
            {"$unwind": "$o"},
            {"$match": {"o.user_id": {"$ne": None}}},
            {"$addFields": {
                "_counted_order": {"$and": [
                    {"$not": [{"$in": [{"$ifNull": ["$o.status", ""]}, _NON_REVENUE_STATUSES]}]},
                    {"$not": [{"$in": [{"$ifNull": ["$o.payment_status", ""]}, _NON_REVENUE_PAYMENT]}]},
                ]},
                "_approved": {"$in": [{"$ifNull": ["$status", ""]}, _RETURN_COUNTED_STATUSES]},
            }},
            {"$group": {"_id": "$o.user_id", "rn": {"$sum": 1},
                        "ra": {"$sum": {"$ifNull": ["$refund_amount", 0]}},
                        "ded": {"$sum": {"$cond": [{"$and": ["$_counted_order", "$_approved"]},
                                                   {"$ifNull": ["$refund_amount", 0]}, 0]}}}},
        ], allowDiskUse=True):
            ret_map[row["_id"]] = (int(row.get("rn") or 0), round(float(row.get("ra") or 0), 2))
            ded_map[row["_id"]] = round(float(row.get("ded") or 0), 2)
    except Exception as e:
        logger.warning(f"[members] iade önbelleği hesaplanamadı: {e}")

    ops, n = [], 0
    seen_users: set = set()
    _now = datetime.now(timezone.utc).isoformat()
    async for row in db.orders.aggregate(pipeline, allowDiskUse=True):
        o = int(row.get("o") or 0)
        gross = round(float(row.get("t") or 0), 2)
        t = round(max(0.0, gross - ded_map.get(row["_id"], 0.0)), 2)  # NET (kısmi iade düşülmüş)
        seg = _segment_for(t, o)
        rn, ra = ret_map.get(row["_id"], (0, 0.0))
        seen_users.add(row["_id"])
        ops.append(UpdateOne({"id": row["_id"]}, {"$set": {
            "cached_orders": o, "cached_spent": t, "cached_gross_spent": gross,
            "cached_last_order": row.get("last"),
            "cached_segment": seg, "cached_returns": rn, "cached_returns_amount": ra,
            "stats_refreshed_at": _now}}))
        if len(ops) >= 500:
            await db.users.bulk_write(ops, ordered=False)
            n += len(ops)
            ops = []
    if ops:
        await db.users.bulk_write(ops, ordered=False)
        n += len(ops)
    # Tüm siparişleri iptal/iade olan üye artık gelir sorgusunda görünmez → eski önbelleği
    # (ör. vip) sıfırla ki segment de net ciroyu yansıtsın.
    try:
        _stale = await db.users.update_many(
            {"cached_orders": {"$gt": 0}, "id": {"$nin": list(seen_users)}},
            {"$set": {"cached_orders": 0, "cached_spent": 0, "cached_gross_spent": 0,
                      "cached_segment": "prospect", "stats_refreshed_at": _now}})
        n += int(_stale.modified_count or 0)
    except Exception as e:
        logger.warning(f"[members] eski önbellek sıfırlama hatası: {e}")
    return {"updated": n, "at": _now}


@router.post("/refresh-stats")
async def refresh_stats(current_user: dict = Depends(require_permission("customers.edit"))):
    """Üye sipariş istatistiklerini (cached_*) yeniden hesapla — liste/segment/stats bundan okur."""
    return await _refresh_member_stats()


_SEGMENT_TR = {"vip": "VIP", "returning": "Tekrar Alan", "new": "Yeni", "prospect": "Potansiyel"}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
import re as _re_x
_PLAIN_PHONE_RE = _re_x.compile(r"^\+[0-9][0-9 ()]*$")


def _xlsx_safe(v):
    """Hesap tablosu formül enjeksiyonu (CSV/Excel injection) koruması: kullanıcı girdisi olan metin
    =,+,-,@,TAB,CR ile başlıyorsa başına ' eklenir → Excel/LibreOffice formül olarak çalıştırmaz.
    (ör. ad alanına '=HYPERLINK(...)' yazan üye, dışa aktarımı açan personele saldıramaz.)"""
    if isinstance(v, str) and v and v.startswith(_FORMULA_PREFIXES):
        if _PLAIN_PHONE_RE.match(v):
            return v  # "+90 5xx ..." gibi saf telefon numarası formül değildir; okunur kalsın
        return "'" + v
    return v


@router.get("/export.xlsx")
async def export_members_xlsx(
    search: Optional[str] = None,
    segment: Optional[str] = None,
    source: Optional[str] = None,
    sort: Optional[str] = "created",
    dir: Optional[str] = "desc",
    date_field: Optional[str] = "created",
    start: Optional[str] = None,
    end: Optional[str] = None,
    current_user: dict = Depends(require_permission("customers.export")),
):
    """Üye listesini (ekrandaki filtre/sıralamayla) Excel'e verir: ad, e-posta, telefon, SEGMENT,
    NET sipariş/harcama (iptal-iade düşülmüş), brüt harcama, iade sayısı/tutarı, son sipariş,
    katılım, kaynak. Tutarlar önbellekten (cached_*) — güncel için önce 'İstatistikleri Yenile'."""
    # ACİL ANAHTAR: freeze_exports açıkken toplu kişisel veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off, emit_security_event
    await require_switch_off("freeze_exports")
    import io
    from fastapi.responses import StreamingResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    # KVKK: silinmiş (anonimleştirilmiş) üye iskeletleri listede/dışa aktarımda görünmez.
    query: dict = {"is_admin": {"$ne": True}, "kvkk_deleted": {"$ne": True}}
    if search:
        query["$or"] = _member_search_or(search)
    if source:
        query["acquisition_source"] = source
    _dfld = "cached_last_order" if (date_field == "last_order") else "created_at"
    if start or end:
        _dq = {}
        if start:
            _dq["$gte"] = start
        if end:
            _dq["$lte"] = end if len(end) > 10 else end + "T23:59:59"
        query[_dfld] = _dq
    if segment:
        seg_match = ({"$or": [{"cached_segment": "prospect"}, {"cached_segment": {"$exists": False}}]}
                     if segment == "prospect" else {"cached_segment": segment})
        query = {"$and": [query, seg_match]}
    _sort_map = {"name": "first_name", "orders": "cached_orders", "spent": "cached_spent",
                 "last_order": "cached_last_order", "created": "created_at",
                 "segment": "cached_segment", "source": "acquisition_source", "returns": "cached_returns"}
    _sf = _sort_map.get((sort or "created"), "created_at")
    _dir = -1 if (dir or "desc").lower() != "asc" else 1

    def _d(v):
        v = str(v or "")
        return v[:10] if len(v) >= 10 else v

    # GÜVENLİK (fail-closed denetim): niyet kaydı (intent) dosya üretilmeden ÖNCE zincire yazılır;
    # yazılamazsa dışa aktarım YAPILMAZ (503). Sonuç done/failed olarak işlenir (activity_audit.run_audited).
    async def _build_export():
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Üyeler"
        headers = ["Ad", "Soyad", "E-posta", "Telefon", "Segment", "Sipariş (net)", "Harcama (net) ₺",
                   "Brüt Harcama ₺", "İade Sayısı", "İade Tutarı ₺", "Son Sipariş", "Katılım", "Kaynak",
                   "E-posta İzni", "SMS İzni", "Aktif"]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="111111")
            c.alignment = Alignment(vertical="center")
        n = 0
        _xs = _xlsx_safe
        async for u in db.users.find(query, {"_id": 0, "password": 0}).sort([(_sf, _dir), ("_id", 1)]).limit(50000):
            seg = u.get("cached_segment") or "prospect"
            ws.append([
                _xs(u.get("first_name") or ""), _xs(u.get("last_name") or ""), _xs(u.get("email") or ""),
                # customers.export yetkili toplu dışa aktarım: telefon şifreliyse burada çözülür.
                _xs(_contact.open_field(u, "phone")), _SEGMENT_TR.get(seg, seg),
                int(u.get("cached_orders") or 0), round(float(u.get("cached_spent") or 0), 2),
                round(float(u.get("cached_gross_spent") if u.get("cached_gross_spent") is not None else (u.get("cached_spent") or 0)), 2),
                int(u.get("cached_returns") or 0), round(float(u.get("cached_returns_amount") or 0), 2),
                _xs(_d(u.get("cached_last_order"))), _xs(_d(u.get("created_at"))),
                _xs(u.get("acquisition_source") or ""),
                "Evet" if u.get("email_marketing_consent") or u.get("kvkk_email") or u.get("newsletter") else "",
                "Evet" if u.get("sms_marketing_consent") or u.get("kvkk_sms") else "",
                "Hayır" if u.get("is_active") is False else "Evet",
            ])
            n += 1
        widths = [14, 14, 30, 16, 12, 12, 16, 16, 12, 14, 12, 12, 14, 12, 10, 8]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for row in ws.iter_rows(min_row=2, min_col=7, max_col=8):
            for c in row:
                c.number_format = "#,##0.00"
        for row in ws.iter_rows(min_row=2, min_col=10, max_col=10):
            for c in row:
                c.number_format = "#,##0.00"
        ws2 = wb.create_sheet("Not")
        ws2.append(["Segment ve harcama NET hesaplanır: iptal, tam iade ve ödemesi gelmemiş siparişler sayılmaz;"])
        ws2.append(["kısmi iadeli siparişte onaylanan iade tutarı harcamadan düşülür. VIP eşiği: net ≥ 5.000 ₺."])
        ws2.append([f"Kayıt: {n} · Oluşturma: {datetime.now(timezone.utc).isoformat()[:16].replace('T', ' ')} UTC"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf, n
    from activity_audit import run_audited
    buf, n = await run_audited(
        db, action="members.export", entity_type="members", entity_id="export.xlsx",
        perform=_build_export, current_user=current_user, source="admin.members",
        metadata={"search": bool(search), "segment": segment or "", "source": source or "",
                  "date_field": date_field or "", "start": start or "", "end": end or ""})
    # GÜVENLİK (denetim 2026-10, #12): toplu kişisel veri dışa aktarımı denetim izine yazılır.
    await write_audit_log(
        "members_export", user_id=current_user.get("id"), email=current_user.get("email"),
        success=True,
        meta={"rows": n, "search": bool(search), "segment": segment or "", "source": source or "",
              "date_field": date_field or "", "start": start or "", "end": end or ""})
    await emit_security_event("member_export", current_user, {"export": "members.xlsx", "rows": n})
    fname = f"uyeler-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}", "Cache-Control": "no-store"})


@router.get("/stats")
async def stats(current_user: dict = Depends(require_permission("customers.view", any_in_area=True))):
    total = await db.users.count_documents({"is_admin": {"$ne": True}})
    since_30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_30 = await db.users.count_documents({"is_admin": {"$ne": True}, "created_at": {"$gte": since_30}})

    # Acquisition by source (from user.acquisition_source OR first order attribution.channel)
    pipeline = [
        {"$match": {"user_id": {"$ne": None}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$user_id", "first_channel": {"$first": "$attribution.channel"}}},
        {"$group": {"_id": "$first_channel", "members": {"$sum": 1}}},
        {"$sort": {"members": -1}},
    ]
    by_channel = []
    async for row in db.orders.aggregate(pipeline):
        by_channel.append({"channel": row["_id"] or "direct", "members": row["members"]})

    # Segments — ÖNBELLEKTEN (cached_segment). Eskiden her üye için ayrı aggregation vardı
    # (O(üye) sorgu → 10k+ üyede dakikalarca). Artık tek grup sorgusu; önbellek _refresh_member_stats
    # ile güncellenir. cached_segment yoksa 'prospect' sayılır.
    seg = {"vip": 0, "returning": 0, "new": 0, "prospect": 0}
    seg_pipe = [
        {"$match": {"is_admin": {"$ne": True}}},
        {"$group": {"_id": {"$ifNull": ["$cached_segment", "prospect"]}, "n": {"$sum": 1}}},
    ]
    async for row in db.users.aggregate(seg_pipe):
        key = row["_id"] if row["_id"] in seg else "prospect"
        seg[key] += int(row["n"])

    return {"total": total, "new_last_30_days": new_30, "segments": seg, "acquisition_by_channel": by_channel}


@router.get("/{mid}")
async def detail(mid: str, current_user: dict = Depends(require_permission("customers.view"))):
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}}, {"_id": 0, "password": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")

    u = await _annotate(u)
    orders = await db.orders.find(_member_order_match(u), {"_id": 0, "payment_receipt.data_b64": 0,
                                                          "payment_receipt.data_enc": 0}).sort("created_at", -1).to_list(200)
    try:  # BL-040: iade IBAN'ı üye kartında maskeli
        from .orders import present_refund_bank_info_in
        await present_refund_bank_info_in(orders, current_user, full=False)
    except Exception:
        for _o in orders:
            _o.pop("refund_bank_info", None)
    addresses = await db.addresses.find({"user_id": mid}, {"_id": 0}).to_list(20)
    # KVKK: üye kartında TCKN/VKN yalnız son 2 hane (sipariş + adres).
    from security.pii import mask_docs_tax_ids as _pii_mask_many
    _pii_mask_many(orders)
    _pii_mask_many(addresses, "address")
    # KVKK: üye kartında telefon / açık adres MASKELİ (adres defteri şifreli olabilir). Tam değer
    # yalnız "Göster" ucunda (GET /admin/members/{id}/contact) — sayaçlı + denetimli.
    _contact.mask_docs(addresses, "address")
    _contact.mask_orders_contact(orders)
    _contact.mask_doc(u, "user")
    u["contact_masked"] = True
    # Adres defteri boşsa geçmiş siparişlerin teslimat adreslerinden türet (görüntüleme amaçlı)
    if not addresses and orders:
        seen = set()
        for o in orders:
            sa = o.get("shipping_address") or {}
            key = ((sa.get("address") or "").strip().lower(), (sa.get("city") or "").strip().lower())
            if not key[0] or key in seen:
                continue
            seen.add(key)
            addresses.append({
                "id": "", "user_id": mid, "title": f"Sipariş Adresi ({o.get('order_number','')})",
                "first_name": sa.get("first_name", ""), "last_name": sa.get("last_name", ""),
                # sipariş listesi yukarıda maskelendi → bu türetilmiş satırlar da maskeli kalır
                "phone": sa.get("phone", ""), "address": sa.get("address", ""),
                "city": sa.get("city", ""), "district": sa.get("district", ""),
                "postal_code": sa.get("postal_code", ""), "is_default": False,
                "source": "order",
            })
            if len(addresses) >= 5:
                break

    # Attribution breakdown from orders
    ch_map: dict = {}
    for o in orders:
        ch = (o.get("attribution") or {}).get("channel") or "direct"
        ch_map[ch] = ch_map.get(ch, 0) + 1
    attribution_summary = [{"channel": k, "orders": v} for k, v in sorted(ch_map.items(), key=lambda x: -x[1])]

    return {
        "member": u,
        "orders": orders,
        "addresses": addresses,
        "attribution_summary": attribution_summary,
    }


async def _member_360_data(u: dict, start=None, end=None):
    """Müşteri 360 çekirdek hesabı (auth'suz) — JSON ucu + PDF/print ucu ortak kullanır."""
    q = {"$and": [_member_order_match(u)]}
    dm = {}
    if start:
        dm["$gte"] = start
    if end:
        dm["$lte"] = end if len(end) > 10 else end + "T23:59:59"
    if dm:
        q["$and"].append({"created_at": dm})
    orders = await db.orders.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)

    CANCELLED = {"cancelled", "cancel_refunded"}
    JUNK = {"awaiting_payment", "payment_failed", "failed"}

    valid, cancels = [], []
    brut, qty_total = 0.0, 0
    pay_ct, ch_ct, monthly, prod_ct, cat_ct, coupons = {}, {}, {}, {}, {}, {}
    size_ct, color_ct = {}, {}
    for o in orders:
        st = (o.get("status") or "").lower()
        tot = float(o.get("total") or o.get("total_amount") or 0)
        if st in CANCELLED:
            cancels.append(o)
            continue
        if st in JUNK:
            continue
        valid.append(o)
        brut += tot
        m = (o.get("created_at") or "")[:7]
        if m:
            monthly[m] = round(monthly.get(m, 0) + tot, 2)
        pm = (o.get("payment_method") or "—").lower()
        pay_ct[pm] = pay_ct.get(pm, 0) + 1
        ch = (o.get("attribution") or {}).get("channel") or "direct"
        ch_ct[ch] = ch_ct.get(ch, 0) + 1
        cc = (o.get("coupon_code") or o.get("coupon") or "").strip()
        if cc:
            coupons[cc] = coupons.get(cc, 0) + 1
        for it in (o.get("items") or o.get("lines") or []):
            qn = int(it.get("quantity") or it.get("qty") or 1)
            qty_total += qn
            nm = (it.get("name") or it.get("product_name") or "Ürün")
            prod_ct[nm] = prod_ct.get(nm, 0) + qn
            ct = (it.get("category") or it.get("category_name") or "")
            if ct:
                cat_ct[ct] = cat_ct.get(ct, 0) + qn
            sz = str(it.get("size") or "").strip()
            if sz:
                size_ct[sz] = size_ct.get(sz, 0) + qn
            cl = str(it.get("color") or "").strip()
            if cl:
                color_ct[cl] = color_ct.get(cl, 0) + qn

    oid = [o.get("id") for o in orders if o.get("id")]
    ret_amt, ret_n, returns_list = 0.0, 0, []
    if oid:
        # SİPARİŞ BAŞINA TEK SATIR: aynı siparişin birden çok iade kaydı (kalem bazlı talep, durum
        # geçişleri: talep → onaylandı) ayrı ayrı sayılıp "İadeler (5)" görünüyordu; oysa 2 sipariş.
        # Tutar = siparişin iade kayıtlarının toplamı; durum = en ileri durum; sebep = ilk dolu sebep.
        _rank = {"approved": 3, "refunded": 3, "completed": 3, "received": 2, "in_transit": 2, "pending": 1, "requested": 1, "rejected": 0}
        by_order: dict = {}
        async for r in db.customer_returns.find({"order_id": {"$in": oid}}, {"_id": 0}):
            ra = float(r.get("refund_amount") or 0)
            ret_amt += ra
            key = r.get("order_id") or r.get("order_number")
            g = by_order.get(key)
            if not g:
                g = by_order[key] = {"order_number": r.get("order_number"), "status": r.get("status"),
                                     "refund_amount": 0.0, "reason": r.get("reason") or "",
                                     "created_at": r.get("created_at") or r.get("updated_at"), "records": 0}
            g["refund_amount"] += ra
            g["records"] += 1
            if _rank.get(str(r.get("status") or ""), 1) >= _rank.get(str(g.get("status") or ""), 1):
                g["status"] = r.get("status")
            if not g["reason"] and r.get("reason"):
                g["reason"] = r.get("reason")
            if (r.get("created_at") or "") and (r.get("created_at") or "") < (g.get("created_at") or "~"):
                g["created_at"] = r.get("created_at")
        for g in by_order.values():
            g["refund_amount"] = round(g["refund_amount"], 2)
            returns_list.append(g)
        returns_list.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        ret_n = len(returns_list)

    sip_n = len(valid)
    iptal_amt = round(sum(float(o.get("total") or 0) for o in cancels), 2)
    net = round(brut - ret_amt, 2)

    def _top(d, n=6):
        return [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda x: -x[1])[:n]]

    def _days(iso):
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).days
        except Exception:
            return None

    first_ord = min((o.get("created_at") for o in valid), default=None)
    last_ord = max((o.get("created_at") for o in valid), default=None)

    # ── Gelişmiş: tekrar-satın-alma aralığı, RFM, churn riski, CLV tahmini ──
    def _parse(iso):
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except Exception:
            return None
    vdates = sorted([d for d in (_parse(o.get("created_at")) for o in valid) if d])
    repurchase_days = None
    if len(vdates) >= 2:
        gaps = [(vdates[i + 1] - vdates[i]).days for i in range(len(vdates) - 1)]
        gaps = [g for g in gaps if g >= 0]
        if gaps:
            repurchase_days = round(sum(gaps) / len(gaps), 1)
    rec = _days(last_ord) if last_ord else None

    def _sc(v, ths):  # artan eşikler → 1..5
        s = 1
        for i, t in enumerate(ths):
            if v is not None and v >= t:
                s = i + 2
        return min(s, 5)
    r_score = 5 if rec is None else (5 if rec <= 30 else 4 if rec <= 60 else 3 if rec <= 120 else 2 if rec <= 240 else 1)
    f_score = _sc(sip_n, [2, 3, 5, 10])
    m_score = _sc(net, [500, 2000, 5000, 10000])
    if sip_n == 0:
        seg = "Ziyaretçi"
    elif r_score >= 4 and f_score >= 4:
        seg = "Şampiyon"
    elif f_score >= 3 and m_score >= 3:
        seg = "Sadık"
    elif r_score >= 4 and f_score <= 2:
        seg = "Yeni / Umut Vaat Eden"
    elif r_score <= 2 and f_score >= 3:
        seg = "Risk Altında"
    elif r_score <= 2:
        seg = "Kayıp / Uykuda"
    else:
        seg = "Gelişmekte"

    churn = {"level": "low", "reason": "Aktif müşteri"}
    if sip_n >= 1 and rec is not None:
        if repurchase_days and rec > repurchase_days * 2 and rec > 45:
            churn = {"level": "high", "reason": f"Ort. {int(repurchase_days)} günde bir alırken {rec} gündür sipariş yok"}
        elif rec > 180:
            churn = {"level": "high", "reason": f"{rec} gündür sipariş yok"}
        elif rec > 90:
            churn = {"level": "medium", "reason": f"{rec} gündür sipariş yok"}

    tenure = _days(u.get("created_at")) or 0
    orders_per_year = (sip_n / (tenure / 365.0)) if (tenure and tenure > 30) else float(sip_n)
    aov_v = (brut / sip_n) if sip_n else 0.0
    proj = (aov_v * orders_per_year * 2) if (churn["level"] != "high" and orders_per_year > 0) else 0.0
    clv_estimate = round(net + proj, 2)

    _m = await _annotate(dict(u))
    _contact.mask_doc(_m, "user")  # KVKK: 360 görünümünde/çıktısında telefon maskeli
    return {
        "member": _m,
        "range": {"start": start, "end": end},
        "kpi": {
            "orders": sip_n, "gross_revenue": round(brut, 2), "net_revenue": net,
            "returns_count": ret_n, "returns_amount": round(ret_amt, 2),
            "cancels_count": len(cancels), "cancels_amount": iptal_amt,
            "return_rate": round(ret_n / sip_n * 100, 1) if sip_n else 0,
            "aov": round(brut / sip_n, 2) if sip_n else 0, "items_total": qty_total,
            "first_order": first_ord, "last_order": last_ord,
            "recency_days": _days(last_ord) if last_ord else None,
            "tenure_days": _days(u.get("created_at")) if u.get("created_at") else None,
            "accepts_marketing": bool(u.get("accepts_marketing")),
        },
        "monthly": [{"month": k, "revenue": monthly[k]} for k in sorted(monthly.keys())],
        "payment_breakdown": _top(pay_ct),
        "channel_breakdown": _top(ch_ct),
        "top_products": _top(prod_ct),
        "top_categories": _top(cat_ct),
        "coupons": _top(coupons),
        "advanced": {
            "rfm": {"r": r_score, "f": f_score, "m": m_score, "total": r_score + f_score + m_score, "segment": seg},
            "churn": churn,
            "repurchase_days": repurchase_days,
            "clv_estimate": clv_estimate,
            "orders_per_year": round(orders_per_year, 1),
            "fav_size": _top(size_ct, 3),
            "fav_color": _top(color_ct, 3),
        },
        "orders": [{"order_number": o.get("order_number") or o.get("id"), "created_at": o.get("created_at"),
                    "status": o.get("status"), "total": float(o.get("total") or 0),
                    "platform": o.get("platform"), "payment_method": o.get("payment_method")} for o in orders[:200]],
        "returns": returns_list[:100],
        "cancels": [{"order_number": o.get("order_number") or o.get("id"), "created_at": o.get("created_at"),
                     "total": float(o.get("total") or 0), "reason": o.get("cancel_reason")} for o in cancels[:100]],
    }


@router.get("/{mid}/contact")
async def member_contact(mid: str, request: Request,
                         current_user: dict = Depends(require_permission("customers.view"))):
    """Üyenin TAM telefonu + adres defteri ("Göster"). customers.view; her açma PRIV07 toplu çözme
    sayacına işlenir (saatte eşik aşımında uyarı + 1 saat engel) ve denetim kaydına yazılır."""
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}},
                                {"_id": 0, "id": 1, "phone": 1, "phone_enc": 1})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    addrs = await db.addresses.find({"user_id": mid}, {"_id": 0}).to_list(20)
    await _contact.note_contact_reveal(current_user, "member", mid, 1 + len(addrs), request=request,
                                       entity_type="user")
    from security.pii import mask_docs_tax_ids as _pii_mask_many
    out_addrs = [_contact.reveal_doc(a, "address") for a in addrs]
    _pii_mask_many(out_addrs, "address")  # vergi no burada da son 2 hane (ayrı yetki/sayaç)
    return {"id": mid, "phone": _contact.open_field(u, "phone"), "addresses": out_addrs}


@router.get("/{mid}/360")
async def member_360(mid: str, start: Optional[str] = None, end: Optional[str] = None,
                     current_user: dict = Depends(require_permission("customers.view", any_in_area=True))):
    """Müşteri 360 JSON — tarih-bazlı ciro/sipariş/iade/iptal + RFM/CLV/churn/favori beden-renk."""
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}}, {"_id": 0, "password": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    return await _member_360_data(u, start, end)


@router.get("/{mid}/360/print")
async def member_360_print(mid: str, start: Optional[str] = None, end: Optional[str] = None,
                           current_user: dict = Depends(require_permission("customers.view"))):
    """Müşteri 360 A4 rapor. Authorization başlığı zorunlu; ?print=1 otomatik yazdırır."""
    from fastapi.responses import HTMLResponse
    import html as _h
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}}, {"_id": 0, "password": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    d = await _member_360_data(u, start, end)
    m, k, a = d["member"], d["kpi"], d["advanced"]

    def esc(v):
        return _h.escape(str(v if v is not None else ""))

    def tl(n):
        return "₺" + f"{float(n or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    name = f"{m.get('first_name','')} {m.get('last_name','')}".strip() or m.get("email") or "Üye"
    rng = (f"{esc(start or '…')} – {esc(end or '…')}") if (start or end) else "Tüm zamanlar"
    _churn_tr = {"low": "Düşük", "medium": "Orta", "high": "Yüksek"}
    kpis = [
        ("Net Ciro", tl(k["net_revenue"])), ("Brüt Ciro", tl(k["gross_revenue"])),
        ("Sipariş", str(k["orders"])), ("Ort. Sepet", tl(k["aov"])),
        ("İade", f"{tl(k['returns_amount'])} ({k['returns_count']})"),
        ("İptal", f"{tl(k['cancels_amount'])} ({k['cancels_count']})"),
        ("İade Oranı", f"%{k['return_rate']}"), ("Ürün Adedi", str(k["items_total"])),
        ("RFM Segment", esc(a['rfm']['segment']) + f" (R{a['rfm']['r']}/F{a['rfm']['f']}/M{a['rfm']['m']})"),
        ("Kayıp Riski", esc(_churn_tr.get(a['churn']['level'], a['churn']['level']))),
        ("CLV (tahmini)", tl(a["clv_estimate"])),
        ("Tekrar Alım", (f"{a['repurchase_days']} günde bir" if a.get("repurchase_days") else "—")),
        ("Son Sipariş", (f"{k['recency_days']} gün önce" if k.get("recency_days") is not None else "—")),
        ("Üyelik Yaşı", (f"{k['tenure_days']} gün" if k.get("tenure_days") is not None else "—")),
    ]
    kpi_html = "".join(f"<div class='k'><div class='kl'>{esc(l)}</div><div class='kv'>{v}</div></div>" for l, v in kpis)

    def _rows(items, cols):
        r = "".join("<tr>" + "".join(f"<td>{esc(c(it))}</td>" for c in cols) + "</tr>" for it in items)
        return r or "<tr><td colspan='9' style='color:#999'>—</td></tr>"

    ord_rows = _rows(d["orders"], [lambda o: o.get("order_number"), lambda o: (o.get("created_at") or "")[:10],
                                   lambda o: o.get("status"), lambda o: tl(o.get("total"))])
    ret_rows = _rows(d["returns"], [lambda o: o.get("order_number"), lambda o: o.get("reason") or o.get("status"),
                                    lambda o: tl(o.get("refund_amount"))])

    def chips(arr):
        return ", ".join(f"{esc(x['name'])}·{x['count']}" for x in (arr or [])) or "—"

    html = f"""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><title>Müşteri 360 - {esc(name)}</title>
<style>@page{{size:A4;margin:13mm}} *{{box-sizing:border-box}} body{{font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:#141414;margin:0}}
.h{{display:flex;justify-content:space-between;border-bottom:2px solid #111;padding-bottom:8px}} .brand{{font-weight:800;letter-spacing:2px;font-size:18px}}
.t{{font-weight:800;font-size:15px}} .sub{{font-size:11px;color:#555}} h2{{font-size:12px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.5px;color:#444;border-bottom:1px solid #ddd;padding-bottom:3px}}
.who{{margin-top:10px}} .who .n{{font-size:16px;font-weight:800}} .who .m{{font-size:11px;color:#555}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}} .k{{border:1px solid #e2e2e2;border-radius:6px;padding:7px 9px}}
.kl{{font-size:9px;color:#777;text-transform:uppercase}} .kv{{font-size:13px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:10.5px;margin-top:4px}} th{{background:#111;color:#fff;text-align:left;padding:5px 7px}} td{{border-bottom:1px solid #eee;padding:5px 7px}}
.chips{{font-size:11px;color:#333;line-height:1.7}}</style></head><body>
<div class="h"><div class="brand">MELUNI</div><div style="text-align:right"><div class="t">MÜŞTERİ 360 RAPORU</div><div class="sub">Dönem: {rng}</div></div></div>
<div class="who"><div class="n">{esc(name)}</div><div class="m">{esc(m.get('email'))}{(' · '+esc(m.get('phone'))) if m.get('phone') else ''} · Segment: {esc(a['rfm']['segment'])}</div></div>
<div class="grid">{kpi_html}</div>
<div style="font-size:9px;color:#888;margin-top:4px">RFM: R = Recency (Yakınlık — son siparişten bu yana) · F = Frequency (Sıklık — sipariş sayısı) · M = Monetary (Parasal — toplam harcama); her biri 1–5 puan.</div>
<h2>Kırılımlar &amp; Tercihler</h2>
<div class="chips"><b>Ödeme:</b> {chips(d['payment_breakdown'])}<br><b>Kanal:</b> {chips(d['channel_breakdown'])}<br>
<b>En çok ürün:</b> {chips(d['top_products'])}<br><b>Kategori:</b> {chips(d['top_categories'])}<br>
<b>Favori beden:</b> {chips(a['fav_size'])} &nbsp;&nbsp; <b>Favori renk:</b> {chips(a['fav_color'])}<br>
<b>Kuponlar:</b> {chips(d['coupons'])}{('<br><b>Kayıp riski:</b> '+esc(a['churn']['reason'])) if a['churn']['level']!='low' else ''}</div>
<h2>Siparişler ({len(d['orders'])})</h2><table><thead><tr><th>Sipariş No</th><th>Tarih</th><th>Durum</th><th>Tutar</th></tr></thead><tbody>{ord_rows}</tbody></table>
<h2>İadeler ({len(d['returns'])})</h2><table><thead><tr><th>Sipariş No</th><th>Sebep / Durum</th><th>İade Tutarı</th></tr></thead><tbody>{ret_rows}</tbody></table>
<script>window.addEventListener('load',()=>{{if(location.search.includes('print=1'))setTimeout(()=>window.print(),300)}})</script>
</body></html>"""
    return HTMLResponse(content=html, headers={"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("")
async def create_member(payload: dict, current_user: dict = Depends(require_permission("customers.edit"))):
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="E-posta gerekli")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-posta zaten kayıtlı")
    # GÜVENLİK (denetim 2026-09-29): şifre verilmezse eskiden BİLİNEN sabit "Meluni123!" (form boş
    # gönderince de BOŞ şifre) atanıyordu. Artık rastgele güçlü geçici şifre üretilir, yanıtta
    # YALNIZ BİR KEZ döner.
    import secrets as _sec
    _pw = str(payload.get("password") or "").strip()
    _generated = False
    if len(_pw) < 8:
        _pw = _sec.token_urlsafe(9)
        _generated = True
    doc = {
        "id": generate_id(),
        "email": email,
        "password": hash_password(_pw),
        "must_change_password": True,
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "phone": payload.get("phone", ""),
        "is_admin": False,
        "is_active": True,
        "accepts_marketing": bool(payload.get("accepts_marketing", False)),
        "acquisition_source": payload.get("acquisition_source", "admin_manual"),
        "notes": payload.get("notes", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by_admin": current_user.get("email", ""),
    }
    _contact.drop_client_sealed(doc, "user")
    await db.users.insert_one(_contact.seal_doc(dict(doc), "user"))  # KVKK: telefon şifreli
    doc.pop("_id", None)
    doc.pop("password", None)
    _contact.mask_doc(doc, "user")
    return {"success": True, "member": doc, "temp_password": _pw if _generated else None}


def _phone_key(v) -> str:
    d = "".join(c for c in str(v or "") if c.isdigit())
    return d[-10:]


@router.put("/{mid}")
async def update_member(mid: str, payload: dict, current_user: dict = Depends(require_permission("customers.edit"))):
    # DENETİM FIX (#41): group_id atanabilir olmalı — Üye Grupları indirimi gruba bağlı üyeye uygulanır.
    allowed = ("first_name", "last_name", "phone", "is_active", "accepts_marketing", "acquisition_source", "notes", "segment_tags", "group_id")
    update = {k: v for k, v in payload.items() if k in allowed}
    _before = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}},
                                      {"_id": 0, "accepts_marketing": 1, "email": 1, "phone": 1,
                                       "phone_enc": 1, "phone_hash": 1, "phone_last2": 1})
    if not _before:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    # KVKK: telefon şifreli saklanır; karşılaştırma/İYS için gerçek değer bellekte açılır.
    # Ekrandan maskeli ("+90 *** *** ** 33") geri gelen telefon "değişmedi" sayılır.
    _before["phone"] = _contact.open_field(_before, "phone")
    if "phone" in update and _contact.is_masked(update.get("phone")):
        update.pop("phone", None)
    # GÜVENLİK (denetim 2026-10, #3): müşterinin ŞİFRESİ ya da TELEFONU (SMS şifre sıfırlama hedefi)
    # yalnız 'customers.credentials' yetkisiyle (süper admin '*' geçer) değiştirilebilir; aksi halde
    # herhangi bir personel müşteri hesabını ele geçirebiliyordu. Değişiklik denetim izine yazılır,
    # şifre değişince müşterinin tüm oturumları düşer (token_version+1).
    _pw_change = bool(payload.get("password"))
    _phone_change = "phone" in update and _phone_key(update.get("phone")) != _phone_key(_before.get("phone"))
    if "phone" in update and not _phone_change:
        update.pop("phone", None)  # aynı numara (biçim farkı) → dokunma
    if _pw_change or _phone_change:
        _perms = await get_effective_permissions(current_user)
        if "*" not in _perms and "customers.credentials" not in _perms:
            await write_audit_log("member_credentials_change", user_id=current_user.get("id"),
                                  email=current_user.get("email"), success=False,
                                  meta={"member_id": mid, "reason": "forbidden",
                                        "password": _pw_change, "phone": _phone_change})
            raise HTTPException(status_code=403,
                                detail="Müşteri şifresi/telefonu değiştirmek için yetkiniz yok (customers.credentials)")
    _inc = {}
    if _pw_change:
        _pw = str(payload.get("password") or "")
        if len(_pw) < 8:
            raise HTTPException(status_code=400, detail="Şifre en az 8 karakter olmalı")
        update["password"] = hash_password(_pw)
        update["password_changed_at"] = datetime.now(timezone.utc).isoformat()
        _inc["token_version"] = 1
    if _phone_change:
        # Personelin (yetkili) girdiği numara doğrulanmış kabul edilir; kim girdiği kayıtlıdır.
        update["phone_verified"] = True
        update["phone_set_by_admin"] = current_user.get("email", "")
        update["phone_changed_at"] = datetime.now(timezone.utc).isoformat()
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    _ops = _contact.sealed_set_update(update, "user", _before)  # KVKK: yeni telefon şifreli yazılır
    if _inc:
        _ops["$inc"] = _inc
    async def _apply_update():
        _r = await db.users.update_one({"id": mid, "is_admin": {"$ne": True}}, _ops)
        if _r.matched_count == 0:
            raise HTTPException(status_code=404, detail="Üye bulunamadı")
        return _r

    if _pw_change or _phone_change:
        # GÜVENLİK (fail-closed denetim): kimlik bilgisi (şifre/telefon) değişikliği ÖNCE niyet kaydı
        # olarak denetim zincirine yazılır; yazılamazsa değişiklik YAPILMAZ (503).
        from activity_audit import run_audited
        await run_audited(db, action="members.credentials_change", entity_type="user", entity_id=mid,
                          perform=_apply_update, current_user=current_user, source="admin.members",
                          metadata={"member_id": mid, "password": _pw_change, "phone": _phone_change})
    else:
        await _apply_update()
    if _pw_change or _phone_change:
        # ID3-04: kimlik bilgisi değişince müşterinin bekleyen şifre-sıfırlama token'ları/kodları ölür.
        from .deps import invalidate_password_reset_tokens
        await invalidate_password_reset_tokens(mid)
        await write_audit_log("member_credentials_change", user_id=current_user.get("id"),
                              email=current_user.get("email"), success=True,
                              meta={"member_id": mid, "password": _pw_change, "phone": _phone_change})
    # İzin panelden KAPATILDIYSA İYS'ye RET (günlük toplu bildirilir). Panelden açma İYS'ye
    # ONAY yazmaz — ticari ileti onayı müşterinin kendisinden alınmalıdır.
    if "accepts_marketing" in update and not update["accepts_marketing"] and (_before or {}).get("accepts_marketing"):
        try:
            from .iys import record_consent
            chans = ["EPOSTA"] + (["MESAJ"] if (_before or {}).get("phone") else [])
            await record_consent((_before or {}).get("email") or "", (_before or {}).get("phone") or "",
                                 chans, status="RET", source="HS_WEB", user_id=mid)
        except Exception:
            pass
    return {"success": True}


@router.delete("/{mid}")
async def delete_member(mid: str, force: bool = Query(False),
                        current_user: dict = Depends(require_permission("customers.delete"))):
    """KVKK uyumlu üye silme — müşterinin kendi "Hesabımı sil" akışıyla AYNI anonimleştirme
    (routes/kvkk.anonymize_user): kişisel veriler silinir/anonimleşir, yasal saklama gerektiren sipariş/
    fatura kayıtları kişiden koparılır, İYS'ye RET yazılır, mezar taşı bırakılır. Eskiden yalnız users
    belgesi siliniyordu (adres/favori/cihaz/bülten kayıtları ve pazarlama izni geride kalıyordu).
    Açık sipariş/iade varsa 409 döner; personel `force=true` ile yine de uygulayabilir."""
    from .kvkk import anonymize_user, open_order_count
    u = await db.users.find_one({"id": mid, "is_admin": {"$ne": True}}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Üye bulunamadı")
    if u.get("kvkk_deleted"):
        return {"success": True, "already": True}
    n_open = await open_order_count(u)
    if n_open and not force:
        raise HTTPException(status_code=409, detail=(
            f"Bu üyenin {n_open} açık siparişi/iadesi var (kargo, iade ya da para iadesi sürüyor). "
            "Silinirse iletişim bilgileri maskelenir. Yine de silmek istiyor musunuz?"))
    rid = generate_id()
    now = datetime.now(timezone.utc).isoformat()
    _em = str(u.get("email") or "")
    await db.kvkk_requests.insert_one({
        "id": rid, "type": "delete", "user_id": mid,
        "email_masked": (_em[:2] + "***@" + _em.split("@", 1)[1]) if "@" in _em else "",
        "source": "admin", "status": "processing", "created_at": now,
        "actor_email": current_user.get("email"), "forced": bool(n_open)})
    summary = await anonymize_user(u, source="admin", request_id=rid, actor=current_user)
    await db.kvkk_requests.update_one({"id": rid}, {"$set": {
        "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(), "summary": summary}})
    await write_audit_log("member_delete", user_id=current_user.get("id"), email=current_user.get("email"),
                          success=True, meta={"member_id": mid, "request_id": rid, "forced": bool(n_open)})
    return {"success": True, "anonymized": True, "summary": summary}
