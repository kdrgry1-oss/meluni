"""
Catalog extras: Brands, Tags, Stock/Price Alerts, Admin Order Create,
Havale Bildirimleri, Deleted Orders archive, Shipping/Payment rule campaigns,
Popups, Announcements, Tickets, Bulk Mail, Currency, Member Groups.

One file on purpose — many tiny CRUD domains with the same shape.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid
import os
import httpx

from .deps import db, require_admin, require_auth, get_current_user, generate_id, logger, tr_day_start_utc, tr_day_end_utc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _simple_crud(collection_name: str, prefix: str, tags: str, required_field: str = "name", extra_allowed: tuple = ()):
    """Create a tiny CRUD router for a flat admin-managed collection."""
    r = APIRouter(prefix=prefix, tags=[tags])

    @r.get("")
    async def list_all(current_user: dict = Depends(require_admin)):
        col = getattr(db, collection_name)
        items = await col.find({}, {"_id": 0}).sort("created_at", -1).to_list(1000)
        return {"items": items, "total": len(items)}

    @r.post("")
    async def create(payload: dict, current_user: dict = Depends(require_admin)):
        val = (payload.get(required_field) or "").strip()
        if not val:
            raise HTTPException(status_code=400, detail=f"{required_field} zorunlu")
        col = getattr(db, collection_name)
        allowed = (required_field, "slug", "description", "is_active", "sort_order", "image", "color", *extra_allowed)
        doc = {k: payload[k] for k in allowed if k in payload}
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = _now()
        doc.setdefault("is_active", True)
        await col.insert_one(doc)
        doc.pop("_id", None)
        return {"success": True, "item": doc}

    @r.put("/{item_id}")
    async def update(item_id: str, payload: dict, current_user: dict = Depends(require_admin)):
        col = getattr(db, collection_name)
        allowed = (required_field, "slug", "description", "is_active", "sort_order", "image", "color", *extra_allowed)
        update = {k: v for k, v in payload.items() if k in allowed}
        update["updated_at"] = _now()
        res = await col.update_one({"id": item_id}, {"$set": update})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        return {"success": True}

    @r.delete("/{item_id}")
    async def delete(item_id: str, current_user: dict = Depends(require_admin)):
        col = getattr(db, collection_name)
        res = await col.delete_one({"id": item_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        return {"success": True}

    return r


# ---------- Brands / Tags / Member Groups / Announcements / Popups ----------
brands_router = _simple_crud("brands", "/brands", "brands")
tags_router = _simple_crud("product_tags", "/product-tags", "product-tags",
                           extra_allowed=("bg_color", "text_color", "icon"))
member_groups_router = _simple_crud("member_groups", "/admin/member-groups", "admin-member-groups",
                                    extra_allowed=("discount_percent", "price_list", "is_b2b"))
announcements_router = _simple_crud("announcements", "/admin/announcements", "admin-announcements",
                                    extra_allowed=("content", "link", "start_at", "end_at", "position", "bg_color"))
popups_router = _simple_crud("popups", "/admin/popups", "admin-popups",
                             extra_allowed=("content", "image", "link", "delay_seconds", "trigger", "show_once",
                                            "start_at", "end_at",
                                            # Bülten kayıt formu modu (ekran görüntüsündeki gibi): e-posta +
                                            # KVKK onayı + buton. newsletter=True ise storefront modalı form gösterir.
                                            "newsletter", "subtitle", "button_text"))


# DENETİM FIX (#35): Popup ve Duyuru için PUBLIC endpoint yoktu → storefront hiç okuyamıyor,
# oluşturulan popup/duyurular hiçbir yerde görünmüyordu. Aktif + tarih aralığındaki kayıtları
# döndüren korumasız GET uçları eklenir (start_at<=now<=end_at, boş tarih = sınırsız).
storefront_extras_router = APIRouter(prefix="/storefront", tags=["storefront-extras"])


def _active_dated_filter():
    """is_active=True ve (start_at yok ya da <=now) ve (end_at yok ya da >=now)."""
    now = _now()
    return {
        "is_active": {"$ne": False},
        "$and": [
            {"$or": [{"start_at": {"$in": [None, ""]}}, {"start_at": {"$exists": False}}, {"start_at": {"$lte": now}}]},
            {"$or": [{"end_at": {"$in": [None, ""]}}, {"end_at": {"$exists": False}}, {"end_at": {"$gte": now}}]},
        ],
    }


@storefront_extras_router.get("/announcements")
async def public_announcements():
    items = await db.announcements.find(_active_dated_filter(), {"_id": 0}).sort("sort_order", 1).to_list(50)
    return {"items": items}


@storefront_extras_router.get("/popups")
async def public_popups():
    items = await db.popups.find(_active_dated_filter(), {"_id": 0}).sort("sort_order", 1).to_list(20)
    return {"items": items}


@storefront_extras_router.get("/member-discount")
async def member_group_discount(current_user: Optional[dict] = Depends(get_current_user)):
    """DENETİM FIX (#41): Oturum açan üyenin üye-grubu indirimini döndürür (checkout tüketir).
    Üye bir gruba (users.group_id) atanmışsa ve grup discount_percent tanımlıysa → o yüzde.
    Anonim / grupsuz / indirimsiz → 0."""
    if not current_user:
        return {"discount_percent": 0, "group_name": None}
    gid = current_user.get("group_id")
    if not gid:
        # users dokümanı token'da group_id taşımıyor olabilir → DB'den oku
        u = await db.users.find_one({"id": current_user.get("id")}, {"_id": 0, "group_id": 1})
        gid = (u or {}).get("group_id")
    if not gid:
        return {"discount_percent": 0, "group_name": None}
    grp = await db.member_groups.find_one({"id": gid}, {"_id": 0})
    if not grp:
        return {"discount_percent": 0, "group_name": None}
    try:
        pct = float(grp.get("discount_percent") or 0)
    except Exception:
        pct = 0
    return {"discount_percent": max(0, min(pct, 90)), "group_name": grp.get("name") or ""}


# ---------- Stock / Price Alerts (public + admin) ----------
alerts_public_router = APIRouter(prefix="/alerts", tags=["alerts"])
alerts_admin_router = APIRouter(prefix="/admin/alerts", tags=["admin-alerts"])


@alerts_public_router.post("")
async def register_alert(payload: dict, request: Request):
    t = (payload.get("type") or "stock").lower()  # stock | price
    if t not in {"stock", "price"}:
        raise HTTPException(status_code=400, detail="Geçersiz tip")
    if not payload.get("product_id") or not payload.get("email"):
        raise HTTPException(status_code=400, detail="product_id ve email gerekli")
    _email = payload["email"].lower().strip()
    # A3: user_id istemciden ALINMAZ — varsa Bearer token'dan türet (sahte sahiplik yazılmasın).
    _uid = None
    try:
        _auth = request.headers.get("authorization") or ""
        if _auth.lower().startswith("bearer "):
            from .deps import _decode_jwt_strict
            _uid = (_decode_jwt_strict(_auth.split(" ", 1)[1].strip()) or {}).get("user_id")
    except Exception:
        _uid = None
    # A3: Ürünün varlığını doğrula (rastgele product_id ile çöp kayıt engellenir).
    if not await db.products.find_one({"id": payload["product_id"]}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    # A3: Aynı (tip, ürün, varyant, e-posta) için bekleyen kayıt varsa idempotent — spam büyümesini önler.
    _dup = await db.stock_alerts.find_one({
        "type": t, "product_id": payload["product_id"],
        "variant_id": payload.get("variant_id"), "email": _email, "notified": False}, {"_id": 0, "id": 1})
    if _dup:
        return {"success": True, "id": _dup["id"], "message": "Zaten kayıtlısınız. Koşul gerçekleştiğinde bilgilendirileceksiniz."}
    doc = {
        "id": str(uuid.uuid4()),
        "type": t,
        "product_id": payload["product_id"],
        "variant_id": payload.get("variant_id"),
        "email": _email,
        "phone": (payload.get("phone") or "").strip(),
        "user_id": _uid,
        "target_price": float(payload.get("target_price") or 0) or None,
        "notified": False,
        "created_at": _now(),
    }
    await db.stock_alerts.insert_one(doc)
    return {"success": True, "id": doc["id"], "message": "Kayıt alındı. Koşul gerçekleştiğinde bilgilendirileceksiniz."}


@alerts_admin_router.get("")
async def list_alerts(
    type_: Optional[str] = Query(None, alias="type"),
    notified: Optional[bool] = None,
    current_user: dict = Depends(require_admin),
):
    q = {}
    if type_: q["type"] = type_
    if notified is not None: q["notified"] = notified
    rows = await db.stock_alerts.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    for r in rows:
        p = await db.products.find_one({"id": r["product_id"]}, {"_id": 0, "name": 1, "price": 1, "stock": 1, "stock_code": 1})
        r["product"] = p or {}
    return {"items": rows, "total": len(rows)}


@alerts_admin_router.delete("/{aid}")
async def delete_alert(aid: str, current_user: dict = Depends(require_admin)):
    res = await db.stock_alerts.delete_one({"id": aid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"success": True}


# ---------- Havale / EFT Notifications ----------
havale_public_router = APIRouter(prefix="/payments/havale-notify", tags=["havale"])
havale_admin_router = APIRouter(prefix="/admin/havale-notifications", tags=["admin-havale"])


@havale_public_router.post("")
async def customer_havale_notify(payload: dict):
    """Customer tells us they paid via bank transfer."""
    order_id = payload.get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    # A3: Siparişin varlığını doğrula (rastgele order_id ile sahte bildirim/çöp engellenir).
    _ord = await db.orders.find_one({"$or": [{"id": order_id}, {"order_number": order_id}]}, {"_id": 0, "id": 1})
    if not _ord:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    order_id = _ord["id"]
    # A3: Aynı sipariş için bekleyen bildirim varsa idempotent — spam/çift kayıt önlenir.
    _dup = await db.havale_notifications.find_one({"order_id": order_id, "status": "pending"}, {"_id": 0, "id": 1})
    if _dup:
        return {"success": True, "id": _dup["id"], "message": "Bildiriminiz zaten alındı, en kısa sürede kontrol edilecek."}
    doc = {
        "id": str(uuid.uuid4()),
        "order_id": order_id,
        "bank": payload.get("bank", ""),
        "sender_name": payload.get("sender_name", ""),
        "amount": float(payload.get("amount") or 0),
        "transfer_date": payload.get("transfer_date", ""),
        "note": payload.get("note", ""),
        "status": "pending",  # pending | confirmed | rejected
        "created_at": _now(),
    }
    await db.havale_notifications.insert_one(doc)
    return {"success": True, "id": doc["id"], "message": "Bildiriminiz alındı, en kısa sürede onaylanacaktır."}


@havale_admin_router.get("")
async def list_havale(
    status: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    q = {}
    if status: q["status"] = status
    rows = await db.havale_notifications.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    for r in rows:
        o = await db.orders.find_one({"id": r["order_id"]}, {"_id": 0, "order_number": 1, "total": 1, "status": 1, "payment_status": 1})
        r["order"] = o or {}
    return {"items": rows, "total": len(rows)}


@havale_admin_router.put("/{nid}")
async def update_havale_status(nid: str, payload: dict, current_user: dict = Depends(require_admin)):
    status = payload.get("status")
    if status not in {"pending", "confirmed", "rejected"}:
        raise HTTPException(status_code=400, detail="Geçersiz durum")
    n = await db.havale_notifications.find_one({"id": nid})
    if not n:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")
    update = {"status": status, "reviewed_at": _now(), "reviewed_by": current_user.get("email", "")}
    await db.havale_notifications.update_one({"id": nid}, {"$set": update})
    if status == "confirmed" and n.get("order_id"):
        # Mark order paid
        await db.orders.update_one(
            {"id": n["order_id"]},
            {"$set": {"payment_status": "paid", "status": "processing", "havale_confirmed_at": _now()}},
        )
    return {"success": True}


# ---------- Admin Manual Order Create ----------
admin_orders_router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])


@admin_orders_router.post("/create-manual")
async def create_manual_order(payload: dict, current_user: dict = Depends(require_admin)):
    """Admin-created order (phone/in-store sales). Applies stock deduction."""
    items = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="En az 1 ürün gerekli")
    subtotal = sum(float(i.get("price", 0)) * int(i.get("quantity", 1)) for i in items)
    order_number = f"MNL-{datetime.now(timezone.utc).strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
    doc = {
        "id": str(uuid.uuid4()),
        "order_number": order_number,
        "items": items,
        "user_id": payload.get("user_id"),
        "shipping_address": payload.get("shipping_address") or {},
        "subtotal": subtotal,
        "shipping_cost": float(payload.get("shipping_cost") or 0),
        "discount": float(payload.get("discount") or 0),
        "total": float(payload.get("total") or subtotal),
        "payment_method": payload.get("payment_method", "cash"),
        "payment_status": payload.get("payment_status", "paid"),
        # DENETİM FIX: 'Ödeme Bekliyor' seçilse bile status sabit 'processing' yazılıyordu →
        # ödenmemiş sipariş 'işleme alındı' görünüyordu. Ödeme durumundan türet.
        "status": payload.get("status") or (
            "processing" if str(payload.get("payment_status", "paid")).lower() == "paid" else "awaiting_payment"),
        "source": "admin_manual",
        "created_by_admin": current_user.get("email", ""),
        "note": payload.get("note", ""),
        "platform": "facette",
        "attribution": {"channel": "admin_manual", "source": "", "medium": "", "campaign": ""},
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.orders.insert_one(doc)

    # Y17: Stok düşümü VARYANT-farkında olmalı. Önceden yalnızca parent `stock` düşürülüyordu →
    # varyantlı üründe (beden) vitrin oversell yapıyor, parent negatife düşüyor ve stok_movements
    # yazılmıyordu. Artık barkod/variant_id ile atomik varyant düşümü + parent yeniden hesap +
    # hareket kaydı yapan ortak yardımcı kullanılır (siparişteki mantıkla birebir).
    try:
        from routes.orders import _stock_delta_for_order
        _now_iso = _now()
        _moves = []
        for it in items:
            qty = int(it.get("quantity", 1) or 1)
            if qty <= 0:
                continue
            barcode = it.get("barcode") or it.get("sku") or ""
            variant_id = it.get("variant_id")
            pid = it.get("product_id")
            if barcode:
                _moves += await _stock_delta_for_order({"items": [it]}, -1)
            elif pid and variant_id:
                # variant_id ile atomik düşüm + parent = varyant toplamı
                await db.products.update_one(
                    {"id": pid, "variants.id": variant_id},
                    {"$inc": {"variants.$[v].stock": -qty}, "$set": {"updated_at": _now_iso}},
                    array_filters=[{"v.id": variant_id}],
                )
                await db.products.update_one(
                    {"id": pid},
                    [{"$set": {"stock": {"$sum": {"$map": {
                        "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                        "in": {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}}}}}}],
                )
                _moves.append({"variant_id": variant_id, "delta": -qty, "product_id": pid})
            elif pid:
                # B11: variant_id/barcode çözülemeyen kalem. VARYANTLI üründe parent 'stock'u
                # doğrudan düşmek YANLIŞ — sonraki herhangi bir varyant hareketi parent'ı
                # $sum(variants) ile recompute edince bu düşüş SİLİNİR (parent yukarı sıçrar,
                # hayalet stok). Ürünü çek ve dallan.
                _p = await db.products.find_one({"id": pid}, {"_id": 0, "id": 1, "variants": 1, "name": 1})
                if _p and (_p.get("variants") or []):
                    # Varyantlı ama varyant çözülemedi → sessiz bozulma yerine UYAR, stoğa DOKUNMA.
                    logger.warning(f"[manuel-sipariş] varyant çözülemedi, stok DÜŞÜLMEDİ: "
                                   f"ürün={pid} ({_p.get('name','')}) sipariş={order_number}")
                    try:
                        from routes.push import send_push_to_admins
                        await send_push_to_admins(
                            "⚠️ Manuel siparişte varyant seçilmedi",
                            f"{_p.get('name','ürün')} — beden/varyant çözülemediği için stok düşülmedi. "
                            f"Manuel kontrol edin (sipariş {order_number}).",
                            {"type": "manual_order_variant_missing"})
                    except Exception:
                        pass
                elif _p:
                    # Gerçekten varyantsız ürün → oversell guard'lı koşullu düşüm.
                    _r = await db.products.update_one(
                        {"id": pid, "stock": {"$gte": qty}},
                        {"$inc": {"stock": -qty}, "$set": {"updated_at": _now_iso}})
                    if _r.modified_count:
                        _moves.append({"product_id": pid, "delta": -qty, "level": "product"})
                    else:
                        logger.warning(f"[manuel-sipariş] stok yetersiz, düşülmedi: "
                                       f"ürün={pid} sipariş={order_number}")
        await db.stock_movements.insert_one({
            "id": str(uuid.uuid4()), "type": "manual_decrement",
            "order_id": doc["id"], "order_number": order_number,
            "items": _moves, "created_by": current_user.get("email", ""),
            "created_at": _now_iso,
        })
    except Exception as _se:
        # Stok düşümü hata verse bile sipariş oluşturuldu; loglayıp devam et.
        import logging as _lg
        _lg.getLogger(__name__).error(f"Manuel siparis stok dususu hatasi: {_se}")

    # İYS: manuel (telefon/mağaza) siparişteki iletişim bilgilerini de İYS'ye bildir.
    # Kaynak HS_FIZIKSEL_ORTAM (yüz yüze/telefon — web değil). İletişim bilgisi olan kanal
    # bildirilir; admin payload.marketing_consent ile kısıtlayabilir ({email:false}/{sms:false}).
    try:
        import asyncio as _aio
        from routes.iys import record_consent as _rec_consent
        _sa = doc.get("shipping_address") or {}
        _mc = payload.get("marketing_consent") or {}
        _email = (_sa.get("email") or "").strip()
        _phone = (_sa.get("phone") or "").strip()
        _channels = []
        if _email and _mc.get("email", True):
            _channels.append("EPOSTA")
        if _phone and _mc.get("sms", True):
            _channels.append("MESAJ")
        if _channels:
            _aio.create_task(_rec_consent(
                recipient_email=_email, recipient_phone=_phone, channels=_channels,
                status="ONAY", source="HS_FIZIKSEL_ORTAM", order_id=doc["id"],
                user_id=doc.get("user_id"),
            ))
    except Exception as _iys_err:
        logger.warning(f"[iys] manuel siparis izin bildirimi hata: {_iys_err}")

    doc.pop("_id", None)
    return {"success": True, "order": doc}


@admin_orders_router.get("/deleted")
async def list_deleted_orders(current_user: dict = Depends(require_admin)):
    rows = await db.orders_deleted.find({}, {"_id": 0}).sort("deleted_at", -1).to_list(500)
    return {"items": rows, "total": len(rows)}


@admin_orders_router.post("/{oid}/restore")
async def restore_order(oid: str, current_user: dict = Depends(require_admin)):
    doc = await db.orders_deleted.find_one({"id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Silinmiş sipariş bulunamadı")
    doc.pop("_id", None)
    doc.pop("deleted_at", None)
    await db.orders.insert_one(doc)
    await db.orders_deleted.delete_one({"id": oid})
    return {"success": True}


# ---------- Shipping / Payment Campaign Rules ----------
rules_router = APIRouter(prefix="/admin/rules", tags=["admin-rules"])


@rules_router.get("/shipping")
async def list_shipping(current_user: dict = Depends(require_admin)):
    items = await db.shipping_rules.find({}, {"_id": 0}).sort("min_cart", 1).to_list(100)
    return {"items": items}


@rules_router.post("/shipping")
async def upsert_shipping(payload: dict, current_user: dict = Depends(require_admin)):
    rid = payload.get("id") or str(uuid.uuid4())
    doc = {
        "id": rid,
        "name": payload.get("name", "Kargo Kuralı"),
        "min_cart": float(payload.get("min_cart") or 0),
        "max_cart": float(payload.get("max_cart") or 0) or None,
        "shipping_cost": float(payload.get("shipping_cost") or 0),
        "free_shipping": bool(payload.get("free_shipping")),
        "city_filter": payload.get("city_filter") or [],
        "is_active": bool(payload.get("is_active", True)),
        "updated_at": _now(),
    }
    await db.shipping_rules.update_one({"id": rid}, {"$set": doc, "$setOnInsert": {"created_at": _now()}}, upsert=True)
    return {"success": True, "id": rid}


@rules_router.delete("/shipping/{rid}")
async def delete_shipping(rid: str, current_user: dict = Depends(require_admin)):
    res = await db.shipping_rules.delete_one({"id": rid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kural bulunamadı")
    return {"success": True}


@rules_router.get("/payment-discounts")
async def list_pay(current_user: dict = Depends(require_admin)):
    items = await db.payment_discounts.find({}, {"_id": 0}).to_list(100)
    return {"items": items}


@rules_router.post("/payment-discounts")
async def upsert_pay(payload: dict, current_user: dict = Depends(require_admin)):
    rid = payload.get("id") or str(uuid.uuid4())
    doc = {
        "id": rid,
        "payment_method": payload.get("payment_method", "havale"),
        "discount_type": payload.get("discount_type", "percent"),  # percent | fixed
        "discount_value": float(payload.get("discount_value") or 0),
        "min_cart": float(payload.get("min_cart") or 0),
        "is_active": bool(payload.get("is_active", True)),
        "label": payload.get("label", ""),
        "updated_at": _now(),
    }
    await db.payment_discounts.update_one({"id": rid}, {"$set": doc, "$setOnInsert": {"created_at": _now()}}, upsert=True)
    return {"success": True}


@rules_router.delete("/payment-discounts/{rid}")
async def delete_pay(rid: str, current_user: dict = Depends(require_admin)):
    await db.payment_discounts.delete_one({"id": rid})
    return {"success": True}


# Public: resolve shipping rule for a given cart total
@rules_router.get("/shipping/resolve")
async def resolve_shipping(cart_total: float = Query(0)):
    rules = await db.shipping_rules.find({"is_active": True}, {"_id": 0}).sort("min_cart", -1).to_list(100)
    for r in rules:
        if cart_total >= r.get("min_cart", 0) and (not r.get("max_cart") or cart_total <= r["max_cart"]):
            return {"matched": True, "rule": r, "shipping_cost": 0 if r.get("free_shipping") else r.get("shipping_cost", 0)}
    return {"matched": False, "shipping_cost": None}


# DENETİM FIX (#45): "Ödeme Tipi İndirimleri" hiçbir yerde tüketilmiyordu — public resolve
# ucu ile checkout, seçili ödeme yöntemine göre aktif indirimi çözebilir. Yalnız aktif kayıtlar.
@rules_router.get("/payment-discounts/resolve")
async def resolve_payment_discounts():
    items = await db.payment_discounts.find({"is_active": {"$ne": False}}, {"_id": 0}).to_list(100)
    # method → {type: 'percent'|'fixed', value: X, label}
    out = {}
    for it in items:
        m = (it.get("method") or it.get("payment_method") or "").strip()
        if not m:
            continue
        out[m] = {
            "type": (it.get("type") or ("percent" if it.get("percent") is not None else "fixed")),
            "percent": float(it.get("percent") or 0),
            "amount": float(it.get("amount") or 0),
            "label": it.get("label") or it.get("name") or "",
        }
    return {"discounts": out}


# ---------- Extra Reports: hourly sales, city, profit, stock movements ----------
extra_reports_router = APIRouter(prefix="/admin/reports-extra", tags=["admin-reports-extra"])


# Gelişmiş Raporlar kaynak (site/trendyol/hepsiburada/temu) filtresi — pazaryeri platform VEYA
# marketplace alanında durabilir; 'site' = pazaryeri OLMAYAN. Boş/all = filtre yok.
_MARKETPLACES_EX = ["trendyol", "hepsiburada", "temu", "n11", "amazon"]
def _src_match_ex(source):
    s = (source or "all").strip().lower()
    if s in ("", "all", "tum", "tümü", "hepsi"):
        return {}
    if s in ("site", "facette", "web", "kendi"):
        return {"platform": {"$nin": _MARKETPLACES_EX}, "marketplace": {"$nin": _MARKETPLACES_EX}}
    return {"$or": [{"platform": s}, {"marketplace": s}]}


@extra_reports_router.get("/hourly")
async def hourly_sales(days: int = Query(7, ge=1, le=90), source: Optional[str] = None, current_user: dict = Depends(require_admin)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}, "status": {"$ne": "cancelled"}, **_src_match_ex(source)}},
        {"$group": {"_id": {"$hour": {"date": {"$dateFromString": {"dateString": "$created_at"}}, "timezone": "Europe/Istanbul"}}, "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"_id": 1}},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"hour": r["_id"], "orders": r["orders"], "revenue": round(r["revenue"], 2)})
    # Fill missing hours with zeros
    by_hour = {r["hour"]: r for r in rows}
    full = [by_hour.get(h) or {"hour": h, "orders": 0, "revenue": 0} for h in range(24)]
    return {"rows": full}


@extra_reports_router.get("/by-city")
async def by_city(start_date: Optional[str] = None, end_date: Optional[str] = None, source: Optional[str] = None, current_user: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    # TR yerel günü → UTC sınırı (bitiş günü tam dahil, saat-dilimi kayması yok).
    s = tr_day_start_utc(start_date) if start_date else (now - timedelta(days=30)).isoformat()
    e = tr_day_end_utc(end_date) if end_date else now.isoformat()
    pipeline = [
        {"$match": {"created_at": {"$gte": s, "$lte": e}, "status": {"$ne": "cancelled"}, **_src_match_ex(source)}},
        {"$group": {"_id": {"$ifNull": ["$shipping_address.city", "—"]}, "orders": {"$sum": 1}, "revenue": {"$sum": {"$ifNull": ["$total", 0]}}}},
        {"$sort": {"revenue": -1}},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"city": r["_id"], "orders": r["orders"], "revenue": round(r["revenue"], 2)})
    return {"rows": rows}


@extra_reports_router.get("/profit")
async def profit(current_user: dict = Depends(require_admin)):
    """Rough profit calc: sum over orders' items — price*qty - cost*qty where product.cost exists."""
    pipeline = [
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$unwind": "$items"},
        {"$lookup": {"from": "products", "localField": "items.product_id", "foreignField": "id", "as": "p"}},
        {"$unwind": {"path": "$p", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$items.product_id",
                "name": {"$first": {"$ifNull": ["$items.name", "$p.name"]}},
                "qty": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                "revenue": {"$sum": {"$multiply": [{"$ifNull": ["$items.price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
                "cost": {"$sum": {"$multiply": [{"$ifNull": ["$p.cost_price", 0]}, {"$ifNull": ["$items.quantity", 1]}]}},
            }
        },
        {"$addFields": {"profit": {"$subtract": ["$revenue", "$cost"]}, "margin_pct": {"$cond": [{"$gt": ["$revenue", 0]}, {"$multiply": [{"$divide": [{"$subtract": ["$revenue", "$cost"]}, "$revenue"]}, 100]}, 0]}}},
        {"$sort": {"profit": -1}},
        {"$limit": 50},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        rows.append({"product_id": r.get("_id"), "name": r.get("name") or "—", "qty": r["qty"], "revenue": round(r["revenue"], 2), "cost": round(r["cost"], 2), "profit": round(r["profit"], 2), "margin_pct": round(r["margin_pct"], 1)})
    return {"rows": rows}


@extra_reports_router.get("/stock-movements")
async def stock_movements(days: int = Query(30, ge=1, le=365), current_user: dict = Depends(require_admin)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # Stock-out from orders
    pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}, "status": {"$ne": "cancelled"}}},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.product_id", "units_out": {"$sum": {"$ifNull": ["$items.quantity", 1]}}}},
        {"$sort": {"units_out": -1}},
        {"$limit": 50},
    ]
    rows = []
    async for r in db.orders.aggregate(pipeline):
        p = await db.products.find_one({"id": r["_id"]}, {"_id": 0, "name": 1, "stock": 1, "stock_code": 1})
        rows.append({"product_id": r["_id"], "name": (p or {}).get("name", "—"), "stock_code": (p or {}).get("stock_code", ""), "current_stock": (p or {}).get("stock", 0), "units_out": r["units_out"]})
    return {"rows": rows}


# ---------- Support Tickets ----------
tickets_public_router = APIRouter(prefix="/tickets", tags=["tickets"])
tickets_admin_router = APIRouter(prefix="/admin/tickets", tags=["admin-tickets"])


@tickets_public_router.post("")
async def create_ticket(payload: dict, current_user: Optional[dict] = Depends(get_current_user)):
    # O18: Önceden require_auth (401) yüzünden misafirler talep AÇAMIYORDU; guest fallback'ları
    # ölü koddu. get_current_user anonimde None döner → misafir de destek talebi açabilir.
    doc = {
        "id": str(uuid.uuid4()),
        "ticket_number": f"TKT-{str(uuid.uuid4())[:8].upper()}",
        "user_id": current_user.get("id") if current_user else None,
        "email": payload.get("email") or (current_user.get("email") if current_user else ""),
        "subject": (payload.get("subject") or "")[:200],
        "message": (payload.get("message") or "")[:5000],
        "priority": payload.get("priority", "normal"),
        "status": "open",  # open | in_progress | resolved | closed
        "order_id": payload.get("order_id"),
        "replies": [],
        "created_at": _now(),
    }
    if not doc["subject"] or not doc["message"]:
        raise HTTPException(status_code=400, detail="Konu ve mesaj zorunlu")
    if not doc["email"]:
        raise HTTPException(status_code=400, detail="E-posta adresi zorunlu")
    await db.tickets.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "ticket": doc}


@tickets_admin_router.get("")
async def list_tickets(status: Optional[str] = None, current_user: dict = Depends(require_admin)):
    q = {}
    if status: q["status"] = status
    rows = await db.tickets.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {"items": rows, "total": len(rows)}


@tickets_admin_router.put("/{tid}")
async def update_ticket(tid: str, payload: dict, current_user: dict = Depends(require_admin)):
    update = {}
    if payload.get("status") in {"open", "in_progress", "resolved", "closed"}:
        update["status"] = payload["status"]
    if payload.get("priority") in {"low", "normal", "high", "urgent"}:
        update["priority"] = payload["priority"]
    if payload.get("reply"):
        reply = {
            "id": str(uuid.uuid4()),
            "by": current_user.get("email", ""),
            "message": payload["reply"][:4000],
            "created_at": _now(),
        }
        await db.tickets.update_one({"id": tid}, {"$push": {"replies": reply}})
    update["updated_at"] = _now()
    res = await db.tickets.update_one({"id": tid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")
    return {"success": True}


@tickets_admin_router.delete("/{tid}")
async def delete_ticket(tid: str, current_user: dict = Depends(require_admin)):
    res = await db.tickets.delete_one({"id": tid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")
    return {"success": True}


# ---------- Bulk Mail / Email via Resend ----------
email_admin_router = APIRouter(prefix="/admin/email", tags=["admin-email"])


async def _send_email_via_resend(to_list, subject: str, html: str, from_addr: Optional[str] = None):
    """E-postaları kurumsal SMTP (Zoho) ile gönderir. Returns (success, failed, errors[]).
    (İsim geriye dönük uyumluluk için korunmuştur; artık Resend değil SMTP kullanır.)"""
    from email_smtp import send_smtp_email, get_smtp_config, is_configured
    cfg = await get_smtp_config(db)
    if not is_configured(cfg):
        return 0, len(to_list), ["E-posta (SMTP/Zoho) yapılandırılmamış"]
    success = 0
    failed = 0
    errors = []
    for t in to_list:
        r = await send_smtp_email(db, t, subject, html)
        if r.get("success"):
            success += 1
        else:
            failed += 1
            if len(errors) < 5:
                errors.append(r.get("response", "hata"))
    return success, failed, errors


@email_admin_router.post("/send-bulk")
async def send_bulk(payload: dict, current_user: dict = Depends(require_admin)):
    segment = payload.get("segment", "all")  # all | newsletter | vip | abandoned
    subject = (payload.get("subject") or "").strip()
    html = (payload.get("html") or "").strip()
    if not subject or not html:
        raise HTTPException(status_code=400, detail="Konu ve HTML içerik zorunlu")

    # Resolve recipients
    recipients: set = set()

    async def _add_newsletter_subscribers():
        # DENETİM FIX (#49): Footer bülten aboneleri ayrı koleksiyonda (newsletter_subscribers)
        # tutuluyor ve toplu maile hiç dahil edilmiyordu. active olanları union'la.
        async for s in db.newsletter_subscribers.find(
            {"$or": [{"active": True}, {"active": {"$exists": False}}]}, {"_id": 0, "email": 1}
        ):
            if s.get("email"):
                recipients.add(str(s["email"]).strip().lower())

    if segment == "all":
        async for u in db.users.find({"is_admin": {"$ne": True}, "is_active": {"$ne": False}}, {"_id": 0, "email": 1}):
            if u.get("email"): recipients.add(u["email"])
        await _add_newsletter_subscribers()
    elif segment == "newsletter":
        async for u in db.users.find({"accepts_marketing": True}, {"_id": 0, "email": 1}):
            if u.get("email"): recipients.add(u["email"])
        await _add_newsletter_subscribers()
    elif segment == "abandoned":
        async for s in db.cart_sessions.find({"email": {"$ne": ""}, "total": {"$gt": 0}}, {"_id": 0, "email": 1}):
            if s.get("email"): recipients.add(s["email"])

    recipients = list(recipients)
    success, failed, errors = await _send_email_via_resend(recipients, subject, html)

    record = {
        "id": str(uuid.uuid4()),
        "subject": subject,
        "segment": segment,
        "recipient_count": len(recipients),
        "success": success,
        "failed": failed,
        "errors": errors[:5],
        "sent_at": _now(),
        "sent_by": current_user.get("email", ""),
    }
    await db.email_campaigns.insert_one(record)
    record.pop("_id", None)
    return {"success": True, "result": record}


@email_admin_router.get("/campaigns")
async def list_campaigns(current_user: dict = Depends(require_admin)):
    items = await db.email_campaigns.find({}, {"_id": 0}).sort("sent_at", -1).to_list(100)
    return {"items": items}


@email_admin_router.post("/send-to-emails")
async def send_to_emails(payload: dict, current_user: dict = Depends(require_admin)):
    """RFM segment vb. dinamik liste için gönderim.

    Body:
      {
        "emails": ["a@b.com", "c@d.com"],   // zorunlu
        "subject": "Konu",
        "html": "<p>...</p>",
        "segment_label": "VIP / Şampiyon"   // opsiyonel — kayıt için
      }
    """
    emails = payload.get("emails") or []
    if not isinstance(emails, list):
        raise HTTPException(status_code=400, detail="emails listesi gerekli")
    subject = (payload.get("subject") or "").strip()
    html = (payload.get("html") or "").strip()
    if not subject or not html:
        raise HTTPException(status_code=400, detail="Konu ve HTML içerik zorunlu")
    # E-posta validasyonu + dedupe
    valid = list({e.strip().lower() for e in emails if e and "@" in e})
    if not valid:
        raise HTTPException(status_code=400, detail="Geçerli e-posta yok")
    if len(valid) > 5000:
        raise HTTPException(status_code=400, detail="Maks 5000 alıcı")

    success, failed, errors = await _send_email_via_resend(valid, subject, html)
    record = {
        "id": str(uuid.uuid4()),
        "subject": subject,
        "segment": payload.get("segment_label") or "custom",
        "recipient_count": len(valid),
        "success": success,
        "failed": failed,
        "errors": errors[:5],
        "sent_at": _now(),
        "sent_by": current_user.get("email", ""),
    }
    await db.email_campaigns.insert_one(record)
    record.pop("_id", None)
    return {"success": True, "result": record}


# ---------- Currency Rates ----------
currency_router = APIRouter(prefix="/admin/currency", tags=["admin-currency"])


@currency_router.get("/rates")
async def get_rates(current_user: dict = Depends(require_admin)):
    doc = await db.currency_rates.find_one({"id": "latest"}, {"_id": 0})
    return doc or {"rates": {}, "updated_at": None}


@currency_router.post("/refresh")
async def refresh_rates(current_user: dict = Depends(require_admin)):
    """Fetch TCMB or exchangerate.host — free API, no key needed."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.exchangerate.host/latest?base=TRY&symbols=USD,EUR,GBP")
            data = r.json()
        rates = {}
        if data.get("rates"):
            # Flip to TRY per 1 unit of foreign currency
            for k, v in data["rates"].items():
                if v > 0:
                    rates[k] = round(1.0 / v, 4)
        doc = {"id": "latest", "rates": rates, "source": "exchangerate.host", "updated_at": _now()}
        await db.currency_rates.update_one({"id": "latest"}, {"$set": doc}, upsert=True)
        return {"success": True, "rates": rates}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}
