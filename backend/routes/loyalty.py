"""
C3 — Kademeli Sadakat + Puan (1 puan = 1 TL).

- KAZANMA: ödemesi onaylanmış siparişlerde toplamın %X'i puan olarak yazılır
  (loyalty.earn_rate_pct). Kademe çarpanı: son 12 ay harcamaya göre
  Silver 1x / Gold (loyalty.gold_multiplier) / Platinum (loyalty.platinum_multiplier).
  Scheduler işi (award_loyalty_points) idempotent çalışır — sipariş başına tek ödül
  (orders.loyalty_awarded atomik bayrak). Özellik AÇILIŞINDAN önceki siparişlere
  geriye dönük puan YAZILMAZ (settings.loyalty_state.launched_at kapısı).
- HARCAMA: ödemede "Puan Kullan" — sunucu-otoriter (create_order):
  tavan = (ara toplam - indirim) * payment.points_redeem_max_pct; bakiye ATOMİK düşülür.
  Kart siparişinde kalan tutar 1 TL altına indirilmez (iyzico min. tahsilat).
- İPTAL: sipariş iptalinde kullanılan puan idempotent iade edilir; kazanılmış puan
  iptal/iade durumunda geri alınır (revoke).
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends

from .deps import db, logger, require_auth

router = APIRouter(prefix="/loyalty", tags=["loyalty"])

_SUCCESS_STATUSES = ["confirmed", "processing", "preparing", "shipped", "delivered", "completed"]
# GÜVENLİK (commerce-3): "Ödemeli İptal Onaylandı" (cancel_refunded) = ödenmiş sipariş iptal
# edilip para İADE edildi. Listede yoktu → öde → puan kazan → iptal talebi → iade döngüsüyle
# (1 puan = 1 TL) bedava puan basılabiliyordu. Kısmi iade (partial_refunded) sipariş kısmen
# müşteride kaldığı için burada tam geri alınmaz; iade edilen kısım _adjust_partial_refunds ile
# ORANSAL geri alınır (denetim #13).
_REVOKE_STATUSES = ["cancelled", "cancel_refunded", "returned", "refunded"]
# Denetim #13: kısmi iade olmuş sipariş de (kalan NET tutar üzerinden) puan kazanır; puanı yazılmış
# siparişe sonradan kısmi iade gelirse iade edilen kısmın puanı ORANSAL geri alınır.
_PARTIAL_STATUSES = ["partial_refunded", "partially_refunded"]
# Onaylanmış iade kaydı durumları (members/analytics net ciro kuralıyla aynı).
_RETURN_COUNTED_STATUSES = ["approved", "return_approved", "refunded", "partial_refunded", "completed"]


async def _refunded_amount(_db, order: dict) -> float:
    """Siparişte müşteriye İADE edilmiş tutar: onaylı iade kayıtları (customer_returns.refund_amount)
    ile siparişteki refunded_amount alanından büyük olanı (çift sayım yok)."""
    tot = 0.0
    try:
        async for r in _db.customer_returns.find(
                {"order_id": order.get("id"), "status": {"$in": _RETURN_COUNTED_STATUSES}},
                {"_id": 0, "refund_amount": 1}):
            tot += float(r.get("refund_amount") or 0)
    except Exception:
        pass
    try:
        tot = max(tot, float(order.get("refunded_amount") or 0))
    except (TypeError, ValueError):
        pass
    return round(max(0.0, tot), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _rule(key: str, default):
    try:
        from business_rules import get_rule
        return await get_rule(db, key, default)
    except Exception:
        return default


async def _twelve_month_spend(user_id: str) -> float:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    agg = await db.orders.aggregate([
        {"$match": {"user_id": user_id, "status": {"$in": _SUCCESS_STATUSES},
                    "created_at": {"$gte": since}}},
        {"$group": {"_id": None, "sum": {"$sum": "$total"}}},
    ]).to_list(1)
    return float((agg[0]["sum"] if agg else 0) or 0)


async def _tier_for(user_id: str) -> dict:
    spend = await _twelve_month_spend(user_id)
    gold_thr = float(await _rule("loyalty.tier_gold_threshold", 10000) or 0)
    plat_thr = float(await _rule("loyalty.tier_platinum_threshold", 25000) or 0)
    gold_mult = float(await _rule("loyalty.gold_multiplier", 1.5) or 1.5)
    plat_mult = float(await _rule("loyalty.platinum_multiplier", 2.0) or 2.0)
    if plat_thr and spend >= plat_thr:
        tier, mult, nxt = "platinum", plat_mult, None
    elif gold_thr and spend >= gold_thr:
        tier, mult, nxt = "gold", gold_mult, plat_thr
    else:
        tier, mult, nxt = "silver", 1.0, gold_thr
    return {"tier": tier, "multiplier": mult, "spend_12m": round(spend, 2),
            "next_threshold": nxt}


async def spend_points_for_order(user_id: str, order: dict, payable_total: float,
                                 cap_base: float) -> dict:
    """create_order içinden çağrılır — bakiye ATOMİK düşülür (yarışa dayanıklı).
    Döner: {"ok", "points", "error"}."""
    if not user_id:
        return {"ok": False, "points": 0.0, "error": "Puan kullanmak için giriş yapın"}
    max_pct = float(await _rule("payment.points_redeem_max_pct", 10) or 10)
    cap = round(max(0.0, cap_base) * max_pct / 100.0, 2)
    for _ in range(2):
        u = await db.users.find_one({"id": user_id}, {"_id": 0, "loyalty_points": 1})
        balance = round(float((u or {}).get("loyalty_points") or 0), 2)
        if balance <= 0:
            return {"ok": False, "points": 0.0, "error": "Kullanılabilir puan yok"}
        use = round(min(balance, cap, max(0.0, payable_total)), 2)
        # Kartla ödenecek kalan 1 TL altına inmesin (iyzico min. tahsilat)
        _pm = (order.get("payment_method") or "").lower()
        if _pm in ("credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"):
            remaining = round(payable_total - use, 2)
            if remaining < 1.0:
                use = round(max(0.0, payable_total - 1.0), 2)
        if use <= 0:
            return {"ok": False, "points": 0.0, "error": "Uygulanacak puan tutarı yok"}
        res = await db.users.update_one(
            {"id": user_id, "loyalty_points": {"$gte": use}},
            {"$inc": {"loyalty_points": -use}})
        if res.modified_count:
            await db.loyalty_transactions.insert_one({
                "user_id": user_id, "type": "spend", "points": use,
                "order_id": order.get("id"), "order_number": order.get("order_number"),
                "at": _now_iso()})
            logger.info(f"[loyalty] {user_id} → {use} puan harcandı (sipariş {order.get('order_number')})")
            return {"ok": True, "points": use, "error": ""}
    return {"ok": False, "points": 0.0, "error": "Puan şu an kullanılamadı, tekrar deneyin"}


async def refund_points_once(order: dict, unpersisted: bool = False) -> float:
    """Sipariş iptalinde kullanılan puanın idempotent iadesi (atomik bayrak kilidi).
    unpersisted=True: sipariş henüz DB'de yok (create_order 409 geri alması) → kilit aranmaz."""
    used = round(float(order.get("points_used") or 0), 2)
    uid = order.get("user_id")
    if used <= 0 or not uid:
        return 0.0
    if unpersisted:
        if order.get("points_refunded"):
            return 0.0
        order["points_refunded"] = True
    else:
        lock = await db.orders.update_one(
            {"id": order.get("id"), "points_used": {"$gt": 0}, "points_refunded": {"$ne": True}},
            {"$set": {"points_refunded": True}})
        if not lock.modified_count:
            return 0.0
    await db.users.update_one({"id": uid}, {"$inc": {"loyalty_points": used}})
    await db.loyalty_transactions.insert_one({
        "user_id": uid, "type": "refund", "points": used,
        "order_id": order.get("id"), "order_number": order.get("order_number"),
        "at": _now_iso()})
    logger.info(f"[loyalty] {uid} ← {used} puan iade (sipariş {order.get('order_number')} iptal)")
    return used


async def award_loyalty_points(db_=None) -> int:
    """Scheduler: ödemesi onaylanmış üye siparişlerine puan yazar; iptal/iadede geri alır.
    İdempotent (atomik bayraklar). Açılıştan önceki siparişlere geriye dönük yazmaz."""
    _db = db_ if db_ is not None else db  # Motor Database bool() desteklemez
    if await _rule("loyalty.enabled", True) is False:
        return 0
    # Açılış tarihi (ilk çalıştırmada damgalanır) — geriye dönük ödül kapısı.
    st = await _db.settings.find_one({"id": "loyalty_state"}, {"_id": 0}) or {}
    launched = st.get("launched_at")
    if not launched:
        launched = _now_iso()
        await _db.settings.update_one({"id": "loyalty_state"},
                                      {"$set": {"launched_at": launched}}, upsert=True)
    earn_pct = float(await _rule("loyalty.earn_rate_pct", 2) or 0)
    awarded = 0
    if earn_pct > 0:
        cursor = _db.orders.find(
            {"payment_status": {"$in": ["paid", "partially_refunded", "partial_refunded"]},
             "status": {"$in": _SUCCESS_STATUSES + _PARTIAL_STATUSES},
             "user_id": {"$nin": [None, ""]}, "created_at": {"$gte": launched},
             "loyalty_awarded": {"$ne": True}},
            {"_id": 0, "id": 1, "user_id": 1, "total": 1, "order_number": 1, "refunded_amount": 1}).limit(300)
        async for o in cursor:
            claim = await _db.orders.update_one(
                {"id": o["id"], "loyalty_awarded": {"$ne": True}},
                {"$set": {"loyalty_awarded": True}})
            if not claim.modified_count:
                continue
            tier = await _tier_for(o["user_id"])
            rate = earn_pct * float(tier["multiplier"])
            # Denetim #13: puan, iade düşüldükten sonra müşteride KALAN net tutar üzerinden.
            net = max(0.0, float(o.get("total") or 0) - await _refunded_amount(_db, o))
            pts = round(net * rate / 100.0, 2)
            await _db.orders.update_one({"id": o["id"]}, {"$set": {"loyalty_rate_pct": rate}})
            if pts <= 0:
                continue
            await _db.orders.update_one({"id": o["id"]}, {"$set": {"loyalty_points_earned": pts}})
            await _db.users.update_one({"id": o["user_id"]},
                                       {"$inc": {"loyalty_points": pts, "loyalty_earned_total": pts}})
            await _db.loyalty_transactions.insert_one({
                "user_id": o["user_id"], "type": "earn", "points": pts,
                "tier": tier["tier"], "order_id": o["id"], "order_number": o.get("order_number"),
                "at": _now_iso()})
            awarded += 1
    # Geri alma: puan yazılmış ama sonradan iptal/iade olmuş siparişler.
    cursor = _db.orders.find(
        {"loyalty_awarded": True, "loyalty_revoked": {"$ne": True},
         "status": {"$in": _REVOKE_STATUSES}},
        {"_id": 0, "id": 1, "user_id": 1, "loyalty_points_earned": 1, "order_number": 1}).limit(300)
    async for o in cursor:
        claim = await _db.orders.update_one(
            {"id": o["id"], "loyalty_revoked": {"$ne": True}},
            {"$set": {"loyalty_revoked": True}})
        if not claim.modified_count:
            continue
        pts = round(float(o.get("loyalty_points_earned") or 0), 2)
        if pts <= 0:
            continue
        await _db.users.update_one({"id": o["user_id"]}, {"$inc": {"loyalty_points": -pts}})
        # Bakiye eksiye düşmesin (puan zaten harcanmışsa sıfırda durdur)
        await _db.users.update_one({"id": o["user_id"], "loyalty_points": {"$lt": 0}},
                                   {"$set": {"loyalty_points": 0}})
        await _db.loyalty_transactions.insert_one({
            "user_id": o["user_id"], "type": "revoke", "points": pts,
            "order_id": o["id"], "order_number": o.get("order_number"), "at": _now_iso()})
    await _adjust_partial_refunds(_db)
    if awarded:
        logger.info(f"[loyalty] {awarded} siparişe puan yazıldı")
    return awarded


async def _adjust_partial_refunds(_db) -> int:
    """Denetim #13: puanı yazılmış ve sonradan KISMİ iade almış siparişte, iade edilen kısmın puanı
    oransal geri alınır (hedef = kalan net × uygulanan oran). İdempotent: siparişteki
    loyalty_points_earned karşılaştır-ve-değiştir ile güncellenir; puan yalnız AZALIR."""
    since = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    oids = set()
    try:
        async for r in _db.customer_returns.find(
                {"status": {"$in": _RETURN_COUNTED_STATUSES}, "order_id": {"$nin": [None, ""]},
                 "$or": [{"updated_at": {"$gte": since}}, {"created_at": {"$gte": since}}]},
                {"_id": 0, "order_id": 1}).limit(1000):
            oids.add(r["order_id"])
    except Exception:
        return 0
    async for o in _db.orders.find(
            {"status": {"$in": _PARTIAL_STATUSES}, "refunded_amount": {"$gt": 0},
             "loyalty_awarded": True, "loyalty_revoked": {"$ne": True}},
            {"_id": 0, "id": 1}).limit(500):
        oids.add(o["id"])
    if not oids:
        return 0
    changed = 0
    async for o in _db.orders.find(
            {"id": {"$in": list(oids)}, "loyalty_awarded": True, "loyalty_revoked": {"$ne": True},
             "status": {"$nin": _REVOKE_STATUSES}, "loyalty_points_earned": {"$gt": 0}},
            {"_id": 0, "id": 1, "user_id": 1, "total": 1, "order_number": 1, "refunded_amount": 1,
             "loyalty_points_earned": 1, "loyalty_rate_pct": 1}):
        earned = round(float(o.get("loyalty_points_earned") or 0), 2)
        total = float(o.get("total") or 0)
        rate = o.get("loyalty_rate_pct")
        if rate is None:
            rate = (earned / total * 100.0) if total > 0 else 0.0
        net = max(0.0, total - await _refunded_amount(_db, o))
        target = round(net * float(rate) / 100.0, 2)
        delta = round(earned - target, 2)
        if delta <= 0:
            continue
        res = await _db.orders.update_one(
            {"id": o["id"], "loyalty_points_earned": o.get("loyalty_points_earned")},
            {"$set": {"loyalty_points_earned": target, "loyalty_partial_adjusted_at": _now_iso()}})
        if not res.modified_count:
            continue
        await _db.users.update_one({"id": o["user_id"]}, {"$inc": {"loyalty_points": -delta}})
        await _db.users.update_one({"id": o["user_id"], "loyalty_points": {"$lt": 0}},
                                   {"$set": {"loyalty_points": 0}})
        await _db.loyalty_transactions.insert_one({
            "user_id": o["user_id"], "type": "partial_revoke", "points": delta,
            "order_id": o["id"], "order_number": o.get("order_number"), "at": _now_iso()})
        changed += 1
    if changed:
        logger.info(f"[loyalty] {changed} kısmi iadeli siparişte puan oransal geri alındı")
    return changed


@router.get("/me")
async def loyalty_me(current_user: dict = Depends(require_auth)):
    """Üyenin puan bakiyesi + kademesi (ödeme sayfası ve hesabım için)."""
    enabled = await _rule("loyalty.enabled", True) is not False
    u = await db.users.find_one({"id": current_user.get("id")}, {"_id": 0, "loyalty_points": 1, "loyalty_earned_total": 1})
    tier = await _tier_for(current_user.get("id"))
    max_pct = float(await _rule("payment.points_redeem_max_pct", 10) or 10)
    earn_pct = float(await _rule("loyalty.earn_rate_pct", 2) or 0)
    return {
        "enabled": enabled,
        "points": round(float((u or {}).get("loyalty_points") or 0), 2),
        "earned_total": round(float((u or {}).get("loyalty_earned_total") or 0), 2),
        **tier,
        "earn_rate_pct": earn_pct,
        "effective_earn_pct": round(earn_pct * tier["multiplier"], 2),
        "redeem_max_pct": max_pct,
    }
