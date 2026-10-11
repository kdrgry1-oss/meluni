"""
Customer account routes - Profile, addresses, orders
"""
import re
from fastapi import APIRouter, HTTPException, Query, Depends, Body, Request
from datetime import datetime, timezone

from .deps import db, logger, get_current_user, require_auth, generate_id, limiter, global_rate_key


def valid_tr_iban(iban: str) -> bool:
    """TR IBAN doğrulaması (format TR+24 hane + mod-97 checksum). DENETİM (bizlogic F11):
    havale iadesinde yanlış/eksik IBAN'a para gönderimi engellenir."""
    s = "".join(str(iban or "").split()).upper()
    if not re.fullmatch(r"TR\d{24}", s):
        return False
    rearranged = s[4:] + s[:4]
    num = "".join((str(ord(ch) - 55) if ch.isalpha() else ch) for ch in rearranged)
    try:
        return int(num) % 97 == 1
    except Exception:
        return False


def _owner_or_clauses(current_user: dict) -> list:
    """Bir siparişin bu üyeye ait olduğunu gösteren eşleşme koşulları.
    user_id ile birlikte e-postayı da kapsar: misafir (üyesiz) verilen ya da
    auth token'ı eksik gönderilmiş eski siparişlerde user_id boş kalmış olabilir;
    bunlar yalnız user_id ile aranınca üye panelinde GÖRÜNMEZ. E-posta eşleşmesi
    aynı kişinin siparişlerini güvenle geri kazandırır (e-posta = aynı kişi)."""
    uid = current_user.get("id")
    ors = [{"user_id": uid}]
    email = (current_user.get("email") or "").strip()
    # GÜVENLİK (denetim 2026-09-29, HIGH): e-posta eşleşmesi YALNIZ doğrulanmış e-postada. Eskiden
    # misafir siparişi veren birinin e-postasıyla (doğrulamasız) üye olan kişi o siparişleri,
    # adres/fatura bilgisini görüp iptal/iade (IBAN) talebi açabiliyordu.
    if email and current_user.get("email_verified") is True:
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
             "iyzico_response": 0,
             # BL-040: dekont baytı listede taşınmaz; iade IBAN'ı aşağıda maskelenir.
             "payment_receipt.data_b64": 0, "payment_receipt.data_enc": 0}
    orders = await db.orders.find(query, {"_id": 0, **_hide}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.orders.count_documents(query)
    # KVKK: TCKN/VKN müşteri listesinde yalnız son 2 hane (şifreli alan/hash yanıta konmaz).
    from security.pii import mask_docs_tax_ids as _pii_mask_many
    _pii_mask_many(orders)
    try:
        from .orders import present_refund_bank_info_in
        await present_refund_bank_info_in(orders, current_user, full=False)
    except Exception:
        for _o in orders:
            _o.pop("refund_bank_info", None)
            if isinstance(_o.get("return_request"), dict):
                _o["return_request"].pop("refund_bank_info", None)
    
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
async def cancel_my_order(order_id: str, request: Request, payload: dict = Body(default={}),
                          current_user: dict = Depends(require_auth)):
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
        # T01/PAY-01: sipariş VAR ama bu üyenin değilse politika reddi kaydı (yanıt yine 404 —
        # sipariş varlığı sızdırılmaz).
        try:
            _other = await db.orders.find_one({"$or": [{"id": order_id}, {"order_number": order_id}]},
                                              {"_id": 0, "id": 1})
            if _other:
                from .orders import note_policy_denial
                await note_policy_denial(current_user, "order.cancel", order=_other, request=request)
        except Exception:
            pass
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
    # KURAL (kullanıcı, 2026-09-16): müşterinin HER iptal talebi — ödeme alınmış olsun olmasın —
    # siparişi DOĞRUDAN "cancelled" YAPMAZ. Sipariş "cancel_requested" (İptal Talebi Alındı) olur,
    # panelde MOR satır olarak görünür ve iptale PERSONEL alır (stok/hediye çeki/puan iadesi o anda
    # idempotent yapılır). Ödeme alınmışsa ayrıca refund_pending=True (para iadesi bekliyor).
    _was_paid = (order.get("payment_status") or "").lower() == "paid"
    _new_status = "cancel_requested"
    _set = {
        "status": _new_status,
        "updated_at": now_iso,
        "cancel_source": "customer",
        "cancel_reason": "Müşteri iptal talebi (üye paneli)",
        "cancel_requested_at": now_iso,
        "refund_pending": bool(_was_paid),   # personel: para iadesi bekliyor (yalnız ödenmişse)
    }
    if _was_paid:
        # Havale/EFT ile ödenmiş siparişte para BANKA HESABINA iade edilir → müşterinin girdiği
        # IBAN + ad soyad saklanır (personel iadeyi bu hesaba yapar). Kart iadesinde gerekmez.
        _pm = str(order.get("payment_method") or "").lower()
        if _pm in ("bank_transfer", "havale", "eft", "bank"):
            _iban = "".join(str((payload or {}).get("refund_iban") or "").split()).upper()[:34]
            _name = str((payload or {}).get("refund_name") or "").strip()[:120]
            # F11: havale iadesinde geçerli TR IBAN + ad soyad ZORUNLU (yanlış hesaba iade engeli)
            if not valid_tr_iban(_iban):
                raise HTTPException(status_code=400, detail="Geçerli bir TR IBAN girin (iade bu hesaba yapılacak).")
            if not _name:
                raise HTTPException(status_code=400, detail="Hesap sahibi ad soyad zorunlu.")
            # BL-040: IBAN + ad soyad at-rest ŞİFRELİ (yalnız iadeyi ödeyen yetkili personel çözer).
            from .orders import seal_refund_bank_info
            _set["refund_bank_info"] = seal_refund_bank_info({
                "iban": _iban,
                "name": _name,
                "bank": str((payload or {}).get("refund_bank") or "").strip()[:80],
            })
    await db.orders.update_one({"id": order.get("id")}, {"$set": _set})

    # Stok geri ekleme — YALNIZCA sipariş GERÇEKTEN iptal edildiyse (cancelled).
    # E1 fix: Ödenmiş siparişte durum 'cancel_requested' (talep alındı, para iadesi bekliyor,
    # sipariş HÂLÂ CANLI). Bu aşamada stok GERİ EKLENMEZ — personel talebi reddedip sevk ederse
    # stok çift sayılırdı (oversell). Stok, personel iptali onaylayınca (update_order_status →
    # cancelled → _restock_order_once) idempotent olarak geri eklenir.
    if _new_status == "cancelled":
        try:
            # DENETİM (payment redteam F1): eskiden yalnız stok geri ekleniyordu; müşteri hediye
            # çeki/puan kullanmışsa REZERVE BAKİYE İADE EDİLMİYORDU (kalıcı müşteri kaybı, sipariş
            # 'cancelled' olduğu için scheduler da süpürmez). _restock_order_once stok + hediye çeki
            # (refund_gift_card_once) + puan (refund_points_once) iadesini idempotent yapar.
            from routes.orders import _restock_order_once
            await _restock_order_once(order, "order_cancelled")
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
                            if _was_paid else "Müşteri iptal talebi oluşturdu — personel iptale alacak (üye paneli)"),
            "actor": current_user.get("email", "") or "müşteri",
            "created_at": now_iso,
        })
    except Exception:
        pass

    if _was_paid:
        return {"success": True, "status": "cancel_requested",
                "message": "İptal talebiniz alındı. Ödemeniz en kısa sürede iade edilecektir."}
    return {"success": True, "status": "cancel_requested",
            "message": "İptal talebiniz alındı. En kısa sürede işleme alınacaktır."}


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
@(limiter.limit("20/minute") if limiter else (lambda f: f))
@(limiter.limit("120/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def public_order_tracking(tracking_code: str, request: Request):
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
        {"_id": 0},
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    # Denetim #27: kargo takip numarasıyla (taşıyıcının verdiği, tahmin edilemez kod) arama tam takip
    # gösterir; SIRALI sipariş numarasıyla aramada sahiplik kanıtı (oturum / sipariş anahtarı /
    # siparişteki e-posta-telefon) yoksa yalnız durum döner — tarih, konum, takip no sızmaz.
    _by_tracking = code != str(order.get("order_number") or "")
    if not _by_tracking:
        from .orders import _order_view_authorized
        if not await _order_view_authorized(request, order):
            try:
                from order_statuses import customer_label_for as _clf0
                _st_txt = _clf0(order.get("status") or "") or order.get("status")
            except Exception:
                _st_txt = order.get("status")
            return {"order_number": order.get("order_number"), "status": order.get("status"),
                    "status_text": _st_txt, "verification_required": True}
    order.pop("user_id", None)
    order.pop("pay_cap_hashes", None)

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
    # AD SOYAD ZORUNLU: bu uç boş dizeyi kabul edip kaydediyordu → üye profilden ismini
    # silebiliyor, sipariş/fatura/kargo etiketi isimsiz kalıyordu. Alan GÖNDERİLDİYSE
    # dolu olmalı (kısmi güncellemede gönderilmeyen alana dokunulmaz).
    for _k, _lbl in (("first_name", "Ad"), ("last_name", "Soyad")):
        if _k in update_data:
            _v = str(update_data[_k] or "").strip()
            if not _v:
                raise HTTPException(status_code=400, detail=f"{_lbl} alanı boş bırakılamaz.")
            update_data[_k] = _v[:100]
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
    # GÜVENLİK (denetim 2026-10, #4): telefon SMS ile şifre sıfırlama / giriş doğrulama hedefidir.
    # Profilden OTP'siz değişen numara DOĞRULANMAMIŞ işaretlenir (phone_verified=False) → sıfırlama
    # ve MFA SMS'i bu numaraya GİTMEZ (çalınmış oturumla numarayı değiştirip kalıcı ele geçirme
    # engeli). Oturum düşürülmez (normal profil kaydı kullanıcıyı çıkarmasın). Eski e-postaya bilgi
    # maili gider. Alanı hiç olmayan eski hesaplar doğrulanmış sayılır (geriye uyum).
    _phone_changed = False
    if "phone" in update_data:
        _new_raw = str(update_data.get("phone") or "").strip()[:32]
        update_data["phone"] = _new_raw
        try:
            from notification_service import normalize_phone_tr as _npn
            _old_n = _npn(str(current_user.get("phone") or "")) if current_user.get("phone") else ""
            _new_n = _npn(_new_raw) if _new_raw else ""
        except Exception:
            _old_n = "".join(c for c in str(current_user.get("phone") or "") if c.isdigit())
            _new_n = "".join(c for c in _new_raw if c.isdigit())
        if _old_n[-10:] != _new_n[-10:]:
            _phone_changed = True
            update_data["phone_verified"] = False
            update_data["phone_changed_at"] = datetime.now(timezone.utc).isoformat()
        else:
            update_data.pop("phone", None)  # biçim farkı: değişiklik sayılmaz, mevcut kayıt korunur
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # KVKK: müşteri telefonu at-rest şifreli yazılır (phone_enc/phone_hash/phone_last2).
    if current_user.get("is_admin") is True or current_user.get("is_super_admin") is True:
        _ops = {"$set": update_data}   # personel telefonu MFA akışında ayrıca yönetilir
    else:
        _ops = _contact.sealed_set_update(update_data, "user")
    await db.users.update_one({"id": current_user.get("id")}, _ops)
    if _phone_changed:
        try:
            from .deps import write_audit_log
            await write_audit_log("profile_phone_change", user_id=current_user.get("id"),
                                  email=current_user.get("email"), success=True,
                                  meta={"verified": False})
        except Exception:
            pass
        _to = (current_user.get("email") or "").strip()
        if _to:
            try:
                import html as _h
                from email_smtp import send_smtp_email
                _nm = _h.escape(str(current_user.get("first_name") or "").strip())
                _html = (
                    '<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#2a2a2a">'
                    '<h2 style="font-weight:600">Telefon numaranız değiştirildi</h2>'
                    f'<p>Merhaba {_nm or "değerli müşterimiz"},</p>'
                    '<p>Hesabınızdaki telefon numarası az önce güncellendi. Güvenliğiniz için yeni numara '
                    'doğrulanana kadar SMS ile şifre sıfırlamada kullanılmaz.</p>'
                    '<p style="font-size:12px;color:#888">Bu değişikliği siz yapmadıysanız hemen şifrenizi '
                    'e-posta ile sıfırlayın ve bizimle iletişime geçin.</p></div>'
                )
                await send_smtp_email(db, _to, "Hesap güvenliği: telefon numarası değişti", _html)
            except Exception as _e:
                logger.warning(f"telefon değişikliği bildirimi gönderilemedi ({type(_e).__name__})")
    
    return {"success": True, "message": "Profil güncellendi"}

from security import contact_pii as _contact  # noqa: E402  (KVKK: adres defteri / profil telefonu)


@router.get("/my-addresses")
async def get_my_addresses(current_user: dict = Depends(require_auth)):
    """Get current user's addresses"""
    addresses = await db.addresses.find(
        {"user_id": current_user.get("id")}, 
        {"_id": 0}
    ).to_list(20)
    # KVKK: adres defterindeki telefon/açık adres at-rest şifreli — SAHİBİNE (kendi hesabı) açık döner.
    addresses = [_contact.reveal_doc(a, "address") for a in addresses]
    # KVKK: vergi/kimlik no maskeli döner ("*********12"); form değiştirilmeden geri gönderilirse
    # sunucu mevcut şifreli değeri korur (update_address).
    from security.pii import mask_docs_tax_ids as _pii_mask_many
    _pii_mask_many(addresses, "address")
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
    # KVKK: tax_no (VKN/TCKN) düz yazılmaz — mühürlenir (maskeli değer gelirse atılır).
    from security.pii import seal_doc_tax_ids as _pii_seal
    _pii_seal(address, "address")
    # KVKK: telefon + açık adres şifreli (address_hash ile tekilleştirme sorgusu düz değersiz çalışır).
    _contact.seal_doc(address, "address")
    
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
    # KVKK: tax_no + telefon + açık adres mühürlenir; maskeli (değiştirilmemiş) değer → mevcut değer korunur.
    from security.pii import sealed_set_update as _pii_set_update
    _c_upd = _contact.sealed_set_update(update_data, "address", address)
    _t_upd = _pii_set_update(_c_upd["$set"], "address", address)
    _ops = {"$set": _t_upd["$set"]}
    _uns = {**(_c_upd.get("$unset") or {}), **(_t_upd.get("$unset") or {})}
    _uns = {k: v for k, v in _uns.items() if k not in _ops["$set"]}
    if _uns:
        _ops["$unset"] = _uns
    await db.addresses.update_one({"id": address_id}, _ops)
    
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
# GÜVENLİK (denetim 2026-10): favoriler yalnız AKTİF + silinmemiş ürünlere eklenebilir/listelenir,
# ürün dokümanından iç alanlar (alış/maliyet fiyatı, tedarikçi, admin notları…) vitrinle AYNI
# kuralla (products._strip_internal_fields) ayıklanır; hesap başına en çok _FAV_MAX favori.
_FAV_MAX = 500
_FAV_ACTIVE_Q = {"is_active": True, "is_deleted": {"$ne": True}}

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
        {"id": {"$in": product_ids}, **_FAV_ACTIVE_Q}, {"_id": 0}
    ).to_list(1000)
    from .products import _strip_internal_fields
    by_id = {p["id"]: _strip_internal_fields(p) for p in products}
    # favorilerin eklenme sırasını koru
    ordered = [by_id[pid] for pid in product_ids if pid in by_id]
    # Vitrinle aynı TAZE kampanya rozetleri (bayat campaign_discount_percent temizlenir).
    try:
        from .products import _attach_campaign_badges
        ordered = await _attach_campaign_badges(ordered)
    except Exception:
        pass
    return {"favorites": ordered}


@router.post("/favorites/merge")
async def merge_favorites(payload: dict, current_user: dict = Depends(require_auth)):
    """Misafir (localStorage) favorilerini login sonrası hesaba taşır.
    NOT: Bu literal route, /favorites/{product_id} parametre route'undan ÖNCE
    tanımlanmalı yoksa FastAPI 'merge'i product_id olarak yakalar."""
    _raw = (payload or {}).get("product_ids") or []
    if not isinstance(_raw, list):
        _raw = []
    ids = list(dict.fromkeys(str(x)[:64] for x in _raw[:200] if isinstance(x, (str, int)) and str(x).strip()))
    if not ids:
        return {"success": True, "merged": 0}
    # Yalnız var olan, aktif ve silinmemiş ürünler.
    _valid = {p["id"] async for p in db.products.find({"id": {"$in": ids}, **_FAV_ACTIVE_Q},
                                                       {"_id": 0, "id": 1})}
    _room = _FAV_MAX - await db.favorites.count_documents({"user_id": current_user.get("id")})
    added = 0
    for pid in ids:
        if pid not in _valid:
            continue
        if added >= _room:
            break
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
    product = await db.products.find_one({"id": product_id, **_FAV_ACTIVE_Q}, {"_id": 0, "id": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    _exists = await db.favorites.find_one({"user_id": current_user.get("id"), "product_id": product_id},
                                          {"_id": 1})
    if not _exists and await db.favorites.count_documents({"user_id": current_user.get("id")}) >= _FAV_MAX:
        raise HTTPException(status_code=400, detail=f"En fazla {_FAV_MAX} ürünü favorilere ekleyebilirsiniz.")
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
