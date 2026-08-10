"""
Admin routes - Dashboard, stats, settings
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime, timezone, timedelta

from .deps import db, require_admin, generate_barcode_from_range, logger, generate_id

router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/dashboard-stats")
async def get_dashboard_stats(
    days: int = Query(30, ge=1, le=365),
    platform: str = Query("all"),
    current_user: dict = Depends(require_admin)
):
    """Admin dashboard istatistikleri.

    days: aralık (gün). platform: 'all' | 'site' | 'trendyol' | 'hepsiburada' | 'ticimax' | ...
    TÜM hesaplar MongoDB aggregation ile yapılır (eski .to_list(1000) limiti KALDIRILDI) →
    28k+ siparişte grafik/ciro/gün eksiksiz gelir; önceki günler/aylar 0 görünmez.
    """
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        prev_start = start_date - timedelta(days=days)
        start_iso = start_date.isoformat()
        prev_start_iso = prev_start.isoformat()
        today_iso = end_date.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        # ── Platform filtresi (sadece site / sadece trendyol / sadece hb ...) ──
        _pf = (platform or "all").strip().lower()
        if _pf in ("", "all", "hepsi", "tumu", "tümü"):
            plat_match = {}
        elif _pf in ("site", "facette", "web", "storefront"):
            plat_match = {"$or": [
                {"platform": {"$in": ["facette", "web", "site", "storefront"]}},
                {"platform": {"$in": [None, ""]}},
                {"platform": {"$exists": False}},
            ]}
        else:
            plat_match = {"platform": _pf}

        _rev = {"$convert": {"input": "$total", "to": "double", "onError": 0, "onNull": 0}}

        def _with(*extra):
            m = dict(plat_match)
            for e in extra:
                m.update(e)
            return m

        # Ciro/sipariş sayımı: iptal/başarısız/iade edilmiş siparişler GELİR DEĞİL → dışla
        # (denetim: total_revenue eskiden iptal+ödenmemişi de topluyordu, ciro şişiyordu).
        # Raporlar modülüyle AYNI liste kullanılır — eskiden burada 3 statü vardı
        # (cancel_refunded / returned / partial_refunded / iade-sürecindekiler /
        # ödenmemişler eksikti) ve Dashboard cirosu Raporlar'daki net ciroyla asla
        # tutmuyordu. Tek kaynak: reports._EXCLUDED_STATUSES.
        from routes.reports import _EXCLUDED_STATUSES as _NON_REVENUE
        async def _sum_range(dt_from, dt_to=None):
            dtq = {"$gte": dt_from}
            if dt_to:
                dtq["$lt"] = dt_to
            r = await db.orders.aggregate([
                {"$match": _with({"created_at": dtq, "status": {"$nin": _NON_REVENUE}})},
                {"$group": {"_id": None, "count": {"$sum": 1}, "revenue": {"$sum": _rev}}},
            ]).to_list(1)
            return (r[0]["count"], r[0]["revenue"] or 0) if r else (0, 0)

        cnt_range, total_revenue = await _sum_range(start_iso)
        prev_cnt, prev_revenue = await _sum_range(prev_start_iso, start_iso)
        cnt_today, revenue_today = await _sum_range(today_iso)

        total_orders = await db.orders.count_documents(plat_match if plat_match else {})
        # Satıştaki (aktif) ürün — silinmişler hariç (aksi halde Ürünler sayfasıyla çelişiyordu)
        total_products = await db.products.count_documents({"is_active": True, "is_deleted": {"$ne": True}})
        total_customers = await db.users.count_documents({"is_admin": {"$ne": True}})

        growth_orders = ((cnt_range - prev_cnt) / max(prev_cnt, 1)) * 100 if prev_cnt else 0
        growth_revenue = ((total_revenue - prev_revenue) / max(prev_revenue, 1)) * 100 if prev_revenue else 0

        # Bekleyen/Kargodaki: TÜM-zaman yerine platform + seçili aralık (anlamlı/aksiyon bekleyen).
        # Eski hâli 11.914 gibi pazaryeri-şişkin toplamlar gösteriyordu.
        pending_orders = await db.orders.count_documents(_with({"status": "pending", "created_at": {"$gte": start_iso}}))
        shipped_orders = await db.orders.count_documents(_with({"status": "shipped", "created_at": {"$gte": start_iso}}))

        # Günlük seri (aggregation — limitsiz)
        daily_agg = await db.orders.aggregate([
            {"$match": _with({"created_at": {"$gte": start_iso}})},
            {"$group": {"_id": {"$substr": [{"$toString": "$created_at"}, 0, 10]},
                        "orders": {"$sum": 1}, "revenue": {"$sum": _rev}}},
        ]).to_list(500)
        daily_map = {r["_id"]: r for r in daily_agg}
        daily_series = []
        for i in range(days, -1, -1):
            d = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            b = daily_map.get(d)
            daily_series.append({"date": d, "orders": (b["orders"] if b else 0),
                                 "revenue": round((b["revenue"] if b else 0) or 0, 2)})
        if len(daily_series) > 92:
            daily_series = daily_series[-92:]

        avg_cart = round(total_revenue / cnt_range, 2) if cnt_range else 0

        # Ödeme tipine göre
        pay_agg = await db.orders.aggregate([
            {"$match": _with({"created_at": {"$gte": start_iso}})},
            {"$group": {"_id": {"$ifNull": ["$payment_method", {"$ifNull": ["$payment_type", "diğer"]}]},
                        "count": {"$sum": 1}, "revenue": {"$sum": _rev}}},
        ]).to_list(50)
        payment_type_breakdown = {str(r["_id"] or "diğer"): {"count": r["count"], "revenue": round(r["revenue"] or 0, 2)}
                                  for r in pay_agg}

        # Durum dağılımı
        status_agg = await db.orders.aggregate([
            {"$match": _with({"created_at": {"$gte": start_iso}})},
            {"$group": {"_id": {"$ifNull": ["$status", "pending"]}, "count": {"$sum": 1}}},
        ]).to_list(60)
        status_breakdown = {str(r["_id"] or "pending"): r["count"] for r in status_agg}
        try:
            from order_statuses import ORDER_STATUS_CATALOG as _CAT
            _smeta = {s["key"]: {"label": s.get("label") or s["key"], "color": s.get("color") or "#9CA3AF"} for s in _CAT}
        except Exception:
            _smeta = {}
        status_breakdown_list = []
        for _k, _cnt in status_breakdown.items():
            _m = _smeta.get(_k) or {"label": str(_k).replace("_", " ").title(), "color": "#9CA3AF"}
            status_breakdown_list.append({"key": _k, "label": _m["label"], "color": _m["color"], "count": _cnt})
        status_breakdown_list.sort(key=lambda x: x["count"], reverse=True)

        # Son siparişler
        recent_orders = await db.orders.find(
            _with({}), {"_id": 0, "id": 1, "order_number": 1, "total": 1, "status": 1,
                        "created_at": 1, "platform": 1}
        ).sort("created_at", -1).limit(5).to_list(5)

        # En çok satan ürünler — ürün adını name/title/product_name'den ÇÖZ (pazaryeri
        # kalemlerinde 'name' boş olabiliyor → adsız dev bir kalem #1 çıkıyordu). Boş adları ele.
        top_agg = await db.orders.aggregate([
            {"$match": _with({"created_at": {"$gte": start_iso}})},
            {"$unwind": "$items"},
            {"$addFields": {"_pname": {"$ifNull": ["$items.name",
                {"$ifNull": ["$items.title", {"$ifNull": ["$items.product_name", "$items.productName"]}]}]}}},
            {"$match": {"_pname": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$_pname",
                        "sold": {"$sum": {"$convert": {"input": "$items.quantity", "to": "int", "onError": 0, "onNull": 0}}},
                        "revenue": {"$sum": {"$multiply": [
                            {"$convert": {"input": "$items.price", "to": "double", "onError": 0, "onNull": 0}},
                            {"$convert": {"input": "$items.quantity", "to": "int", "onError": 0, "onNull": 0}}]}}}},
            {"$sort": {"sold": -1}}, {"$limit": 5},
        ]).to_list(5)
        top_products = [{"name": r["_id"], "sold": r["sold"], "revenue": round(r["revenue"] or 0, 2)} for r in top_agg]

        # Terk edilen sepet (platformdan bağımsız — site sepeti)
        try:
            _ab_cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            _ab_q = {"updated_at": {"$lte": _ab_cutoff}, "total": {"$gt": 0},
                     "$expr": {"$gt": [{"$size": {"$ifNull": ["$items", []]}}, 0]}}
            _ab_count = 0; _ab_items = 0; _ab_total = 0.0
            async for c in db.cart_sessions.find(_ab_q, {"_id": 0, "total": 1, "items": 1}):
                _ab_count += 1
                _ab_total += float(c.get("total") or 0)
                for it in (c.get("items") or []):
                    _ab_items += int(it.get("quantity") or 1)
            abandoned_carts = {"count": _ab_count, "item_count": _ab_items, "total_value": round(_ab_total, 2)}
        except Exception:
            abandoned_carts = {"count": 0, "item_count": 0, "total_value": 0}

        try:
            total_categories = await db.categories.count_documents({})
        except Exception:
            total_categories = 0
        try:
            _stock_pipe = [
                {"$match": {"is_active": True}},
                {"$project": {"s": {"$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$variants", []]}}, 0]},
                    {"$sum": "$variants.stock"}, {"$ifNull": ["$stock", 0]}]}}},
                {"$group": {"_id": None, "total": {"$sum": "$s"}}},
            ]
            _sr = await db.products.aggregate(_stock_pipe).to_list(1)
            total_stock = int((_sr[0]["total"] if _sr else 0) or 0)
        except Exception:
            total_stock = 0
        try:
            pending_messages = await db.tickets.count_documents({"status": {"$in": ["open", "in_progress"]}})
        except Exception:
            pending_messages = 0
        # Satıştaki toplam marka
        try:
            _brands = await db.products.distinct("brand", {"is_active": True})
            total_brands = len([b for b in _brands if b and str(b).strip()])
        except Exception:
            total_brands = 0
        # Sipariş gelen KAYNAKLAR (dinamik platform filtresi için) — pazaryeri değerleri
        try:
            _pl_agg = await db.orders.aggregate([
                {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]).to_list(50)
            available_platforms = []
            for r in _pl_agg:
                _key = r.get("_id")
                if _key in (None, "", "facette", "web", "site", "storefront"):
                    continue  # 'Sadece Site' seçeneği ayrı ekleniyor
                available_platforms.append({"platform": str(_key), "count": r.get("count", 0)})
        except Exception:
            available_platforms = []

        return {
            "platform": _pf,
            "total_orders": total_orders,
            "total_revenue": round(total_revenue or 0, 2),
            "total_products": total_products,
            "total_customers": total_customers,
            "pending_orders": pending_orders,
            "shipped_orders": shipped_orders,
            "orders_today": cnt_today,
            "revenue_today": round(revenue_today or 0, 2),
            "growth_orders": round(growth_orders, 1),
            "growth_revenue": round(growth_revenue, 1),
            "recent_orders": recent_orders,
            "top_products": top_products,
            "order_status_breakdown": status_breakdown,
            "order_status_list": status_breakdown_list,
            "orders_in_range": cnt_range,
            "avg_cart": avg_cart,
            "daily_series": daily_series,
            "payment_type_breakdown": payment_type_breakdown,
            "abandoned_carts": abandoned_carts,
            "total_categories": total_categories,
            "total_stock": total_stock,
            "total_brands": total_brands,
            "pending_messages": pending_messages,
            "available_platforms": available_platforms,
        }

    except Exception as e:
        logger.error(f"Dashboard stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users")
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    current_user: dict = Depends(require_admin)
):
    """PANEL personeli listesi (admin only).

    ÖNEMLİ: Bu uç, /admin/users path'inde admin_rbac.list_panel_users ile ÇAKIŞIYORDU ve
    filtresiz olduğu için 20 MÜŞTERİYİ 'kullanıcı' gibi gösteriyordu. Artık yalnız GERÇEK
    panel personeli döner (elle eklenen = created_by; süper-admin; varsayılan admin). Müşteri
    (register) hesapları 'Üyeler' (/admin/members) sayfasında görünür; burada GÖRÜNMEZ."""
    import re as _re
    skip = (page - 1) * limit
    # Panel personeli ayırt edici işaretleri (admin_rbac.PANEL_STAFF_OR ile birebir).
    _staff_or = [
        {"created_by": {"$exists": True, "$nin": [None, ""]}},
        {"is_super_admin": True},
        {"email": "admin@facette.com"},
        {"role_id": {"$exists": True, "$nin": [None, ""]}},
    ]
    query = {"is_admin": True, "$or": _staff_or}

    if search:
        _s = _re.escape(search.strip())  # ReDoS/regex-injection koruması
        query["$and"] = [{"$or": [
            {"email": {"$regex": _s, "$options": "i"}},
            {"first_name": {"$regex": _s, "$options": "i"}},
            {"last_name": {"$regex": _s, "$options": "i"}},
        ]}]

    users = await db.users.find(query, {"_id": 0, "password": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.users.count_documents(query)
    
    return {
        "users": users,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }

import random

@router.get("/generate-stock-code")
async def generate_stock_code(
    prefix: str = Query("FCSS"),
    current_user: dict = Depends(require_admin)
):
    """Generate a unique 7-digit stock code suffix under the given prefix"""
    existing = await db.products.distinct("stock_code")
    for v_doc in await db.products.find({}, {"variants": 1}).to_list(None):
        for var in v_doc.get("variants", []):
            sc = var.get("stock_code", "")
            if sc:
                existing.append(sc)
    existing_set = set(existing)

    for _ in range(1000):
        suffix = str(random.randint(1000000, 9999999))
        code = f"{prefix}{suffix}"
        if code not in existing_set:
            return {"stock_code": code}
    
    raise HTTPException(status_code=500, detail="Benzersiz stok kodu üretilemedi")

@router.get("/generate-barcode")
async def generate_barcode(
    current_user: dict = Depends(require_admin)
):
    """Generate a unique 13-digit GTIN barcode within the configured range"""
    barcode = await generate_barcode_from_range()
    if not barcode:
        raise HTTPException(
            status_code=400, 
            detail="Barkod üretilemedi. Lütfen Ayarlar sayfasında barkod aralığınızı kontrol edin ve ürünlerinizde çakışma olmadığından emin olun."
        )
    return {"barcode": barcode}

@router.get("/cleanup/dry-run")
async def cleanup_dry_run(current_user: dict = Depends(require_admin)):
    """Salt-okunur veri temizlik raporu (#1 combine + #3 mükerrer varyant).
    HİÇBİR ŞEY DEĞİŞTİRMEZ; sadece rapor döner."""
    from collections import defaultdict

    def _norm(s):
        return str(s or "").strip().lower()

    def _has_image(p):
        imgs = p.get("images") or []
        return bool((imgs and str(imgs[0]).strip()) or str(p.get("image") or "").strip())

    SAMPLE = 50

    # Hafif ürün indeksi (id -> minimal)
    prod = {}
    async for p in db.products.find(
        {}, {"_id": 0, "id": 1, "name": 1, "is_active": 1, "images": 1, "image": 1}
    ):
        if p.get("id") is not None:
            prod[str(p["id"])] = p
    total_products = len(prod)

    # ---- #1  BOZUK combine_products ----
    c1 = {"missing": 0, "inactive": 0, "no_image": 0}
    n_with_combine = 0
    affected = 0
    would_empty = 0
    s1 = []
    async for p in db.products.find(
        {"combine_products": {"$exists": True, "$ne": []}},
        {"_id": 0, "id": 1, "name": 1, "combine_products": 1},
    ):
        ids = p.get("combine_products") or []
        if not ids:
            continue
        n_with_combine += 1
        bad = []
        good = 0
        for cid in ids:
            cid = str(cid)
            ref = prod.get(cid)
            if ref is None:
                c1["missing"] += 1; bad.append([cid, "missing"])
            elif ref.get("is_active") is False:
                c1["inactive"] += 1; bad.append([cid, "inactive"])
            elif not _has_image(ref):
                c1["no_image"] += 1; bad.append([cid, "no_image"])
            else:
                good += 1
        if bad:
            affected += 1
            if good == 0:
                would_empty += 1
            if len(s1) < SAMPLE:
                s1.append({"id": p.get("id"), "name": (p.get("name") or "")[:60],
                           "bad": len(bad), "total": len(ids), "detay": bad[:8]})

    # ---- #3  AYNI BARKOD + AYNI BEDEN mükerrer varyant ----
    # ---- #3  Ürün İÇİNDE aynı BEDEN birden fazla (2x S, 2x L ...) ----
    dup_products = 0
    dup_groups = 0
    dup_extra = 0
    s3 = []
    async for p in db.products.find(
        {"variants.0": {"$exists": True}},
        {"_id": 0, "id": 1, "name": 1, "variants": 1},
    ):
        variants = p.get("variants") or []
        groups = defaultdict(list)
        for i, v in enumerate(variants):
            groups[_norm(v.get("size"))].append(i)   # bedene göre grupla (barkod bağımsız)
        dups = {sz: idxs for sz, idxs in groups.items() if len(idxs) > 1}
        if dups:
            dup_products += 1
            for sz, idxs in dups.items():
                dup_groups += 1
                dup_extra += len(idxs) - 1
                if len(s3) < SAMPLE:
                    detay = [{"barcode": (variants[i].get("barcode") or ""),
                              "stock": variants[i].get("stock")} for i in idxs[:6]]
                    s3.append({"id": p.get("id"), "name": (p.get("name") or "")[:50],
                               "size": sz or "(boş)", "count": len(idxs), "detay": detay})

    # ---- #3b  GLOBAL: farklı ürünlerde AYNI BARKOD (top-level + varyant) ----
    barcode_products = defaultdict(set)
    async for p in db.products.find(
        {}, {"_id": 0, "id": 1, "barcode": 1, "variants": 1}
    ):
        pid = str(p.get("id"))
        bc = _norm(p.get("barcode"))
        if bc:
            barcode_products[bc].add(pid)
        for v in (p.get("variants") or []):
            vb = _norm(v.get("barcode"))
            if vb:
                barcode_products[vb].add(pid)
    cross = {bc: sorted(pids) for bc, pids in barcode_products.items() if len(pids) > 1}
    cross_samples = [{"barcode": bc, "product_ids": pids[:8], "product_count": len(pids)}
                     for bc, pids in list(cross.items())[:SAMPLE]]

    return {
        "total_products": total_products,
        "combine_broken": {
            "products_with_combine": n_with_combine,
            "affected_products": affected,
            "would_empty_after_clean": would_empty,
            "counts": c1,
            "samples": s1,
        },
        "duplicate_variants": {
            "affected_products": dup_products,
            "duplicate_groups": dup_groups,
            "removable_extra_variants": dup_extra,
            "samples": s3,
        },
        "duplicate_barcodes_cross_product": {
            "barcodes_used_by_multiple_products": len(cross),
            "samples": cross_samples,
        },
        "note": "DRY-RUN — hicbir veri degistirilmedi.",
    }


@router.post("/cleanup/dedupe-barcodes")
async def cleanup_dedupe_barcodes(payload: dict = None, current_user: dict = Depends(require_admin)):
    """Ürün İÇİNDE aynı barkodlu mükerrer varyantları siler.
    Her barkoddan EN YÜKSEK STOKLU olanı tutar, diğer kopyalarını kaldırır.
    Barkodsuz varyantlara ve farklı barkodlu (aynı beden bile olsa) varyantlara
    DOKUNMAZ. Başka hiçbir şeyi silmez/değiştirmez.

    payload: {"confirm": true}  -> uygular.   confirm yok/false -> sadece ÖNİZLEME."""
    payload = payload or {}
    confirm = bool(payload.get("confirm", False))

    def _norm(s):
        return str(s or "").strip().lower()

    preview = []
    total_removed = 0
    affected = 0

    async for p in db.products.find(
        {"variants.1": {"$exists": True}},
        {"_id": 0, "id": 1, "name": 1, "variants": 1},
    ):
        variants = p.get("variants") or []
        by_bc = {}
        for v in variants:
            bc = _norm(v.get("barcode"))
            if bc:
                by_bc.setdefault(bc, []).append(v)
        dup_bcs = {bc: vs for bc, vs in by_bc.items() if len(vs) > 1}
        if not dup_bcs:
            continue

        # Her mükerrer barkod için EN YÜKSEK STOKLU varyantın indeksini seç
        best_idx = {}
        for i, v in enumerate(variants):
            bc = _norm(v.get("barcode"))
            if not bc:
                continue
            if bc not in best_idx or (v.get("stock") or 0) > (variants[best_idx[bc]].get("stock") or 0):
                best_idx[bc] = i

        new_variants = []
        removed_here = 0
        for i, v in enumerate(variants):
            bc = _norm(v.get("barcode"))
            if not bc or len(by_bc.get(bc, [])) == 1 or best_idx.get(bc) == i:
                new_variants.append(v)   # barkodsuz / tekil / tutulan -> KORU
            else:
                removed_here += 1        # aynı barkodun fazlası -> SİL

        if removed_here > 0:
            affected += 1
            total_removed += removed_here
            preview.append({
                "id": p.get("id"),
                "name": (p.get("name") or "")[:50],
                "removed": removed_here,
                "kept_total": len(new_variants),
                "barcodes": [
                    {"barcode": bc, "had": len(vs),
                     "kept_stock": max(vs, key=lambda x: (x.get("stock") or 0)).get("stock")}
                    for bc, vs in dup_bcs.items()
                ][:8],
            })
            if confirm:
                await db.products.update_one(
                    {"id": p.get("id")},
                    {"$set": {"variants": new_variants,
                              "updated_at": datetime.now(timezone.utc).isoformat()}},
                )

    return {
        "mode": "APPLIED" if confirm else "PREVIEW",
        "affected_products": affected,
        "removed_variants": total_removed,
        "samples": preview[:80],
        "note": ("Silme uygulandi." if confirm else
                 "ONIZLEME — hicbir sey silinmedi. Uygulamak icin confirm:true gonder."),
    }


@router.post("/cleanup/combine-dead-refs")
async def cleanup_combine_dead_refs(payload: dict = None, current_user: dict = Depends(require_admin)):
    """combine_products ('Görünümü Tamamla') içindeki ÖLÜ referansları temizler:
    var olmayan / is_active=False / görselsiz ürün id'leri listeden çıkarılır.
    Ürünleri/varyantları SİLMEZ — sadece kırık işaretçileri temizler.

    payload: {"confirm": true} -> uygular.  confirm yok/false -> ÖNİZLEME."""
    payload = payload or {}
    confirm = bool(payload.get("confirm", False))

    prod = {}
    async for p in db.products.find(
        {}, {"_id": 0, "id": 1, "is_active": 1, "images": 1, "image": 1}
    ):
        if p.get("id") is not None:
            prod[str(p["id"])] = p

    def _has_image(p):
        imgs = p.get("images") or []
        return bool((imgs and str(imgs[0]).strip()) or str(p.get("image") or "").strip())

    preview = []
    total_removed = 0
    affected = 0
    async for p in db.products.find(
        {"combine_products": {"$exists": True, "$ne": []}},
        {"_id": 0, "id": 1, "name": 1, "combine_products": 1},
    ):
        ids = [str(c) for c in (p.get("combine_products") or [])]
        good = []
        bad = 0
        for cid in ids:
            ref = prod.get(cid)
            if ref is not None and ref.get("is_active") is not False and _has_image(ref):
                good.append(cid)
            else:
                bad += 1
        if bad > 0:
            affected += 1
            total_removed += bad
            preview.append({"id": p.get("id"), "name": (p.get("name") or "")[:50],
                            "removed": bad, "kept": len(good)})
            if confirm:
                await db.products.update_one(
                    {"id": p.get("id")},
                    {"$set": {"combine_products": good,
                              "updated_at": datetime.now(timezone.utc).isoformat()}},
                )

    return {
        "mode": "APPLIED" if confirm else "PREVIEW",
        "affected_products": affected,
        "removed_refs": total_removed,
        "samples": preview[:80],
        "note": ("Temizlik uygulandi." if confirm else
                 "ONIZLEME — hicbir sey degismedi. Uygulamak icin confirm:true gonder."),
    }
