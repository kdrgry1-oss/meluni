"""
Customer account routes - Profile, addresses, orders
"""
import re
from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime, timezone

from .deps import db, logger, get_current_user, require_auth, generate_id


def _owner_or_clauses(current_user: dict) -> list:
    """Bir siparişin bu üyeye ait olduğunu gösteren eşleşme koşulları.
    user_id ile birlikte e-postayı da kapsar: misafir (üyesiz) verilen ya da
    auth token'ı eksik gönderilmiş eski siparişlerde user_id boş kalmış olabilir;
    bunlar yalnız user_id ile aranınca üye panelinde GÖRÜNMEZ. E-posta eşleşmesi
    aynı kişinin siparişlerini güvenle geri kazandırır (e-posta = aynı kişi)."""
    uid = current_user.get("id")
    ors = [{"user_id": uid}]
    email = (current_user.get("email") or "").strip()
    if email:
        rx = {"$regex": f"^{re.escape(email)}$", "$options": "i"}
        ors.append({"shipping_address.email": rx})
        ors.append({"email": rx})
        ors.append({"billing_address.email": rx})
    return ors

router = APIRouter(tags=["Customer Account"])

@router.get("/my-orders")
async def get_my_orders(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(require_auth)
):
    """Get current user's orders"""
    skip = (page - 1) * limit
    # user_id + e-posta ile eşleştir (eski/misafir siparişlerini de kapsar)
    query = {"$or": _owner_or_clauses(current_user)}

    # GÜVENLİK: Müşteriye personel-iç/pazarlama/ham-gateway alanlarını sızdırma
    # (admin_notes = risk/dolandırıcılık notları). Public by-number ile tutarlı.
    _hide = {"admin_notes": 0, "customer_ip": 0, "user_agent": 0, "attribution": 0,
             "payment_id": 0, "iyzico_retrieve_response": 0, "iyzico_init_response": 0,
             "iyzico_response": 0}
    orders = await db.orders.find(query, {"_id": 0, **_hide}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.orders.count_documents(query)
    
    return {
        "orders": orders,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }


# Müşterinin kendi siparişini iptal edebileceği durumlar = "Hazırlanıyor" ÖNCESİ.
# Bu set, frontend butonunun görünürlüğüyle birebir aynı tutulmalıdır.
_CUSTOMER_CANCELLABLE = {"pending", "awaiting_payment", "payment_notified", "confirmed"}


@router.post("/my-orders/{order_id}/cancel")
async def cancel_my_order(order_id: str, current_user: dict = Depends(require_auth)):
    """Üye, kendi siparişini YALNIZCA 'Hazırlanıyor' durumuna geçmeden iptal edebilir.
    Sunucu tarafı guard zorunludur — frontend'in butonu gizlemesine güvenilmez."""
    order = await db.orders.find_one(
        {"$and": [
            {"$or": [{"id": order_id}, {"order_number": order_id}]},
            {"$or": _owner_or_clauses(current_user)},
        ]},
        {"_id": 0},
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    cur = order.get("status") or "pending"
    if cur == "cancelled":
        return {"success": True, "status": "cancelled", "message": "Sipariş zaten iptal edilmiş."}
    if cur not in _CUSTOMER_CANCELLABLE:
        raise HTTPException(
            status_code=409,
            detail="Siparişiniz hazırlanmaya başladığı için artık iptal edilemiyor. "
                   "Lütfen bizimle iletişime geçin.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    # PARA İADESİ AYRIMI: Ödemesi ALINMIŞ (payment_status=paid — kart/iyzico veya onaylı havale)
    # siparişte müşteri iptal ederse DOĞRUDAN "cancelled" YAPILMAZ; "cancel_requested"
    # (İptal Talebi Alındı) durumuna çekilir ki personel para iadesini ATLAMASIN. Ödeme
    # alınmamışsa (pending/COD) eskisi gibi doğrudan iptal edilir (iade gerekmez).
    _was_paid = (order.get("payment_status") or "").lower() == "paid"
    _new_status = "cancel_requested" if _was_paid else "cancelled"
    _set = {
        "status": _new_status,
        "updated_at": now_iso,
        "cancel_source": "customer",
        "cancel_reason": "Müşteri iptali (üye paneli)",
    }
    if _new_status == "cancelled":
        _set["cancelled_at"] = now_iso
    else:
        _set["cancel_requested_at"] = now_iso
        _set["refund_pending"] = True   # personel: para iadesi bekliyor
    await db.orders.update_one({"id": order.get("id")}, {"$set": _set})

    # Stok geri ekleme — YALNIZCA sipariş GERÇEKTEN iptal edildiyse (cancelled).
    # E1 fix: Ödenmiş siparişte durum 'cancel_requested' (talep alındı, para iadesi bekliyor,
    # sipariş HÂLÂ CANLI). Bu aşamada stok GERİ EKLENMEZ — personel talebi reddedip sevk ederse
    # stok çift sayılırdı (oversell). Stok, personel iptali onaylayınca (update_order_status →
    # cancelled → _restock_order_once) idempotent olarak geri eklenir.
    if _new_status == "cancelled":
        try:
            already = await db.stock_movements.find_one(
                {"order_id": order.get("id"), "type": {"$in": ["order_cancelled", "return_restock",
                    "auto_cancel_expired", "havale_auto_cancel", "manual_increment"]}}, {"_id": 1}
            )
            if not already:
                from routes.orders import _stock_delta_for_order
                moves = await _stock_delta_for_order(order, +1)
                await db.stock_movements.insert_one({
                    "id": generate_id(),
                    "type": "order_cancelled",
                    "order_id": order.get("id"),
                    "order_number": order.get("order_number", ""),
                    "items": moves,
                    "source": "customer_cancel",
                    "created_at": now_iso,
                })
        except Exception as _e:
            logger.error(f"[customer cancel restock {order_id}] {_e}")

    # Sipariş olay günlüğü (admin tarafında görünür)
    try:
        await db.order_events.insert_one({
            "id": generate_id(),
            "order_id": order.get("id"),
            "order_number": order.get("order_number", ""),
            "event_type": "status",
            "description": ("Müşteri iptal talebi oluşturdu — ÖDEME ALINDIĞI İÇİN PARA İADESİ BEKLİYOR (üye paneli)"
                            if _new_status == "cancel_requested" else "Müşteri siparişi iptal etti (üye paneli)"),
            "actor": current_user.get("email", "") or "müşteri",
            "created_at": now_iso,
        })
    except Exception:
        pass

    if _new_status == "cancel_requested":
        return {"success": True, "status": "cancel_requested",
                "message": "İptal talebiniz alındı. Ödemeniz en kısa sürede iade edilecektir."}
    return {"success": True, "status": "cancelled", "message": "Siparişiniz iptal edildi."}


# ==================== PUBLIC ORDER TRACKING (footer "Sipariş Takibi") ====================
# Frontend: GET /api/track/{code}  (TrackOrder.jsx) — auth GEREKTİRMEZ.
# Bu endpoint refactor sırasında server.py.old'dan modüler yapıya taşınmamıştı; bu yüzden
# footer'dan sipariş no / kargo takip no ile arama 404 dönüyordu. Mevcut DÜZ şema alanlarını
# kullanır: cargo_company / cargo_tracking_number / shipped_at (eski nested cargo.* fallback).

_CARGO_NAME_MAP = {
    "MNG": "MNG Kargo", "DHL": "DHL", "Yurtici": "Yurtiçi Kargo",
    "Aras": "Aras Kargo", "PTT": "PTT Kargo", "UPS": "UPS",
    "HepsiJet": "HepsiJet", "Trendyol": "Trendyol Express", "Other": "Kargo",
}


def _public_track_link(order: dict, tn: str) -> str:
    """Müşteriye gösterilecek kargo takip linki (kanonik öncelik sırası)."""
    cargo = order.get("cargo") or {}
    stored = (order.get("cargo_tracking_link") or order.get("cargo_tracking_url")
              or cargo.get("tracking_link") or "")
    if stored:
        return stored
    if not tn:
        return ""
    code = (order.get("cargo_company") or "").strip()
    if code == "Yurtici":
        return f"https://www.yurticikargo.com/tr/online-servisler/gonderi-sorgula?code={tn}"
    if code == "Aras":
        return f"https://kargotakip.araskargo.com.tr/CargoStatusByTrackingNumber.aspx?code={tn}"
    if code == "PTT":
        return f"https://gonderitakip.ptt.gov.tr/Track/Verify?q={tn}"
    if code == "UPS":
        return f"https://www.ups.com/track?tracknum={tn}"
    # MNG canlı entegrasyonda DHL eCommerce üzerinden taşınır → DHL deep-link
    return f"https://kargotakip.dhlecommerce.com.tr/?takipNo={tn}"


@router.get("/track/{tracking_code}")
async def public_order_tracking(tracking_code: str):
    """Genel sipariş/kargo takibi — auth gerektirmez.
    Sipariş numarası VEYA kargo takip numarası ile arar."""
    code = (tracking_code or "").strip()
    if not code:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    order = await db.orders.find_one(
        {"$or": [
            {"order_number": code},
            {"cargo_tracking_number": code},
            {"cargo.tracking_number": code},
        ]},
        {"_id": 0, "user_id": 0},
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    status = order.get("status") or "pending"
    cargo = order.get("cargo") or {}
    shipped_at = order.get("shipped_at") or cargo.get("shipped_at")
    delivered_at = order.get("delivered_at")
    tn = (str(order.get("cargo_tracking_number") or "").strip()
          or str(cargo.get("tracking_number") or "").strip())
    carrier_code = (order.get("cargo_company") or "").strip()
    carrier = _CARGO_NAME_MAP.get(
        carrier_code,
        carrier_code or order.get("cargo_provider_name") or cargo.get("provider_name") or "Kargo",
    )
    track_link = _public_track_link(order, tn)

    SHIPPED = {"shipped", "delivered"}
    PROCESSING = {"processing", "preparing", "shipped", "delivered"}
    CONFIRMED = {"confirmed", "processing", "preparing", "shipped", "delivered"}

    if status == "cancelled":
        timeline = [
            {"status": "placed", "title": "Sipariş Alındı", "date": order.get("created_at"), "completed": True},
            {"status": "cancelled", "title": "Sipariş İptal Edildi",
             "date": order.get("cancelled_at"), "completed": True},
        ]
    else:
        shipped_step = {
            "status": "shipped", "title": "Kargoya Verildi",
            "date": shipped_at, "completed": status in SHIPPED,
        }
        if status in SHIPPED and tn:
            shipped_step.update({"tracking_number": tn, "tracking_url": track_link, "carrier": carrier})
        timeline = [
            {"status": "placed", "title": "Sipariş Alındı", "date": order.get("created_at"), "completed": True},
            {"status": "confirmed", "title": "Sipariş Onaylandı",
             "date": order.get("confirmed_at") or (order.get("created_at") if status in CONFIRMED else None),
             "completed": status in CONFIRMED},
            {"status": "processing", "title": "Hazırlanıyor",
             "date": order.get("processing_at"), "completed": status in PROCESSING},
            shipped_step,
            {"status": "delivered", "title": "Teslim Edildi",
             "date": delivered_at if status == "delivered" else None, "completed": status == "delivered"},
        ]

    ship = order.get("shipping_address") or {}

    def _mask(v):
        v = (v or "").strip()
        return (v[:1] + "***") if v else ""

    masked = {
        "first_name": _mask(ship.get("first_name")),
        "last_name": _mask(ship.get("last_name")),
        "city": ship.get("city", ""),
        "district": ship.get("district", ""),
    }

    try:
        from order_statuses import customer_label_for as _clf
        status_text = _clf(status) or status
    except Exception:
        status_text = {
            "pending": "Beklemede", "awaiting_payment": "Ödeme Bekleniyor",
            "payment_notified": "Ödeme Bildirildi", "confirmed": "Onaylandı",
            "processing": "Hazırlanıyor", "shipped": "Kargoda",
            "delivered": "Teslim Edildi", "cancelled": "İptal Edildi",
        }.get(status, status)

    return {
        "order_number": order.get("order_number"),
        "status": status,
        "status_text": status_text,
        "timeline": timeline,
        "shipping_address": masked,
        "cargo": ({"company": carrier, "tracking_number": tn, "tracking_url": track_link} if tn else None),
        # A3: total / item_count kaldırıldı — kimliksiz uç, sipariş no ardışık olduğundan
        # tutar/kalem sayısı numaraları tarayarak sızabiliyordu (iş-istihbaratı + enumeration).
    }


@router.put("/users/me")
async def update_my_profile(
    profile_data: dict,
    current_user: dict = Depends(require_auth)
):
    """Update current user's profile"""
    allowed_fields = ["first_name", "last_name", "phone"]
    update_data = {k: v for k, v in profile_data.items() if k in allowed_fields}
    # Boy/kilo — beden önerisi için (doğrulanmış aralık; boş bırakılırsa temizlenir).
    def _num_or_none(v, lo, hi):
        if v in (None, ""):
            return None
        try:
            n = float(v)
            return n if lo <= n <= hi else None
        except Exception:
            return None
    if "height_cm" in profile_data:
        update_data["height_cm"] = _num_or_none(profile_data.get("height_cm"), 100, 230)
    if "weight_kg" in profile_data:
        update_data["weight_kg"] = _num_or_none(profile_data.get("weight_kg"), 30, 250)
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.users.update_one(
        {"id": current_user.get("id")},
        {"$set": update_data}
    )
    
    return {"success": True, "message": "Profil güncellendi"}

@router.get("/my-addresses")
async def get_my_addresses(current_user: dict = Depends(require_auth)):
    """Get current user's addresses"""
    addresses = await db.addresses.find(
        {"user_id": current_user.get("id")}, 
        {"_id": 0}
    ).to_list(20)
    
    return {"addresses": addresses}

@router.post("/addresses")
async def create_address(
    address_data: dict,
    current_user: dict = Depends(require_auth)
):
    """Create new address"""
    address = {
        "id": generate_id(),
        "user_id": current_user.get("id"),
        "title": address_data.get("title", ""),
        "first_name": address_data.get("first_name", ""),
        "last_name": address_data.get("last_name", ""),
        "phone": address_data.get("phone", ""),
        "address": address_data.get("address", ""),
        "city": address_data.get("city", ""),
        "district": address_data.get("district", ""),
        "postal_code": address_data.get("postal_code", ""),
        "is_default": address_data.get("is_default", False),
        # Kurumsal (Şirket) Fatura Bilgileri
        "is_corporate": bool(address_data.get("is_corporate", False)),
        "company_name": address_data.get("company_name", ""),
        "tax_no": address_data.get("tax_no", ""),
        "tax_office": address_data.get("tax_office", ""),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    # If setting as default, unset others
    if address["is_default"]:
        await db.addresses.update_many(
            {"user_id": current_user.get("id")},
            {"$set": {"is_default": False}}
        )
    
    await db.addresses.insert_one(address)
    
    return {"success": True, "address_id": address["id"]}

@router.put("/addresses/{address_id}")
async def update_address(
    address_id: str,
    address_data: dict,
    current_user: dict = Depends(require_auth)
):
    """Update address"""
    # Verify ownership
    address = await db.addresses.find_one({
        "id": address_id, 
        "user_id": current_user.get("id")
    })
    
    if not address:
        raise HTTPException(status_code=404, detail="Adres bulunamadı")
    
    # If setting as default, unset others
    if address_data.get("is_default"):
        await db.addresses.update_many(
            {"user_id": current_user.get("id"), "id": {"$ne": address_id}},
            {"$set": {"is_default": False}}
        )
    
    allowed_fields = ["title", "first_name", "last_name", "phone", "address", "city", "district",
                      "postal_code", "is_default",
                      "is_corporate", "company_name", "tax_no", "tax_office"]
    update_data = {k: v for k, v in address_data.items() if k in allowed_fields}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.addresses.update_one({"id": address_id}, {"$set": update_data})
    
    return {"success": True}

@router.delete("/addresses/{address_id}")
async def delete_address(
    address_id: str,
    current_user: dict = Depends(require_auth)
):
    """Delete address"""
    result = await db.addresses.delete_one({
        "id": address_id,
        "user_id": current_user.get("id")
    })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Adres bulunamadı")
    
    return {"success": True}


# ==================== FAVORITES (WISHLIST) ====================

@router.get("/favorites/ids")
async def get_my_favorite_ids(current_user: dict = Depends(require_auth)):
    """Sadece favori ürün ID'lerini döndürür (UI kalp durumu için hızlı)."""
    docs = await db.favorites.find(
        {"user_id": current_user.get("id")}, {"_id": 0, "product_id": 1}
    ).to_list(1000)
    return {"product_ids": [d["product_id"] for d in docs]}


@router.get("/favorites")
async def get_my_favorites(current_user: dict = Depends(require_auth)):
    """Favori ürünlerin tam objelerini döndürür."""
    docs = await db.favorites.find(
        {"user_id": current_user.get("id")}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    product_ids = [d["product_id"] for d in docs]
    if not product_ids:
        return {"favorites": []}
    products = await db.products.find(
        {"id": {"$in": product_ids}}, {"_id": 0}
    ).to_list(1000)
    by_id = {p["id"]: p for p in products}
    # favorilerin eklenme sırasını koru
    ordered = [by_id[pid] for pid in product_ids if pid in by_id]
    return {"favorites": ordered}


@router.post("/favorites/merge")
async def merge_favorites(payload: dict, current_user: dict = Depends(require_auth)):
    """Misafir (localStorage) favorilerini login sonrası hesaba taşır.
    NOT: Bu literal route, /favorites/{product_id} parametre route'undan ÖNCE
    tanımlanmalı yoksa FastAPI 'merge'i product_id olarak yakalar."""
    ids = (payload.get("product_ids") or [])[:200]
    added = 0
    for pid in ids:
        if not pid:
            continue
        res = await db.favorites.update_one(
            {"user_id": current_user.get("id"), "product_id": pid},
            {"$setOnInsert": {
                "id": generate_id(),
                "user_id": current_user.get("id"),
                "product_id": pid,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        if res.upserted_id is not None:
            added += 1
    return {"success": True, "merged": added}


@router.post("/favorites/{product_id}")
async def add_favorite(product_id: str, current_user: dict = Depends(require_auth)):
    """Ürünü favorilere ekler (idempotent)."""
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "id": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    await db.favorites.update_one(
        {"user_id": current_user.get("id"), "product_id": product_id},
        {"$setOnInsert": {
            "id": generate_id(),
            "user_id": current_user.get("id"),
            "product_id": product_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"success": True, "is_favorite": True}


@router.delete("/favorites/{product_id}")
async def remove_favorite(product_id: str, current_user: dict = Depends(require_auth)):
    """Ürünü favorilerden çıkarır."""
    await db.favorites.delete_one({
        "user_id": current_user.get("id"),
        "product_id": product_id,
    })
    return {"success": True, "is_favorite": False}
