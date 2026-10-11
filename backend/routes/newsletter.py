"""
=============================================================================
newsletter.py — marka kulübü bülten aboneliği
=============================================================================
Footer üstündeki bülten bloğu ve genel e-posta abonelikleri için basit,
tekilleştirilmiş kayıt. İzin (İYS/EPOSTA) NetGSM entegratörü üzerinden
best-effort bildirilir.

ENDPOINTS:
  POST /api/newsletter/subscribe          — Public (e-posta ile abone ol)
  GET  /api/admin/newsletter/subscribers  — Admin (abone listesi)
=============================================================================
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from datetime import datetime, timezone

from .deps import db, require_admin, generate_id, limiter, require_permission


async def _brand() -> str:
    """Marka adi — company.py'den (env/panel). Okunamazsa bos doner ve
    mesaj markasiz ama anlasilir kalir."""
    try:
        import company as _company
        return (await _company.get_company(db)).get("store_name") or ""
    except Exception:
        return ""


def _club(brand: str) -> str:
    """'meluni Kulübü' / marka bos ise sade 'Kulüp'."""
    return f"{brand} Kulübü" if brand else "Kulüp"


public_router = APIRouter(prefix="/newsletter", tags=["newsletter-public"])
admin_router = APIRouter(prefix="/admin/newsletter", tags=["newsletter-admin"])

def _valid_email(value: str) -> bool:
    if not value or len(value) > 254 or any(ch.isspace() for ch in value):
        return False
    local, separator, domain = value.rpartition("@")
    return bool(separator and local and "." in domain and not domain.startswith(".") and not domain.endswith("."))


@public_router.post("/subscribe")
@(limiter.limit("10/minute;100/day") if limiter else (lambda f: f))
async def subscribe(payload: dict, request: Request):
    """Bülten aboneliği. E-postayı tekilleştirerek kaydeder; İYS iznini
    NetGSM üzerinden bildirir (best-effort — hata aboneliği engellemez).
    DENETİM (cost-redteam #10): hız-sınırı yoktu → her benzersiz e-posta bir İYS API çağrısı;
    sınırsız İYS çağrısı/rıza sahteciliği. IP limiti eklendi."""
    email = (payload or {}).get("email", "")
    email = email.strip().lower() if isinstance(email, str) else ""
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Geçerli bir e-posta adresi girin.")

    # KVKK / ticari-ileti onayı ZORUNLU — açık rıza olmadan İYS'ye ONAY işlenmez (yasal).
    consent = bool((payload or {}).get("consent"))
    consent_text = str((payload or {}).get("consent_text") or "")[:1000]
    if not consent:
        raise HTTPException(status_code=400, detail="Ticari ileti / KVKK onayı gerekli.")

    now = datetime.now(timezone.utc).isoformat()
    ip = request.client.host if request and request.client else ""
    ua = request.headers.get("user-agent", "")[:400] if request else ""
    source = (payload or {}).get("source") or "footer"

    # Popup "Üye Ol ve Kazan %10" vaat ediyor → kodu hem ekranda göster hem e-postayla gönder
    # (eskiden bu yol HİÇ kod/mail üretmiyordu). Kod panelde aktif ilk-sipariş kampanyasından.
    from welcome_mail import welcome_coupon_vars, send_welcome_email
    _cv = await welcome_coupon_vars(db)
    _kod_msg = (f" İlk siparişine özel {_cv['coupon_discount']} indirim kodun: {_cv['coupon_code']}"
                if _cv.get("coupon_code") else "")

    existing = await db.newsletter_subscribers.find_one({"email": email})
    if existing:
        # Zaten kayıtlı — başarı döndür; kodu yine göster (mail kaybolmuş olabilir).
        return {"success": True, "already": True, "coupon_code": _cv.get("coupon_code") or None,
                "message": "Zaten Meluni Kulübü üyesisiniz." + _kod_msg}

    await db.newsletter_subscribers.insert_one({
        "id": generate_id(),
        "email": email,
        "source": source,
        "ip": ip,
        "active": True,
        # Açık rıza kanıtı (İYS/KVKK): onay verildi mi, hangi metinle, ne zaman, hangi IP/UA.
        "consent": True,
        "consent_text": consent_text,
        "consent_at": now,
        "consent_ip": ip,
        "consent_ua": ua,
        "created_at": now,
    })

    # İYS/EPOSTA iznini NetGSM entegratörüne bildir (best-effort).
    try:
        from .iys import record_consent
        await record_consent(
            recipient_email=email, recipient_phone="",
            channels=["EPOSTA"], status="ONAY",
            source="HS_WEB", ip=ip,
        )
    except Exception:
        pass

    if _cv.get("coupon_code"):
        _r = await send_welcome_email(db, email)
        if _r.get("ok"):
            await db.newsletter_subscribers.update_one(
                {"email": email}, {"$set": {"welcome_sent_at": datetime.now(timezone.utc).isoformat()}})

    return {"success": True, "already": False, "coupon_code": _cv.get("coupon_code") or None,
            "message": "Meluni Kulübü'ne hoş geldiniz!" + _kod_msg
                       + (" (e-postana da gönderdik)" if _kod_msg else "")}


@admin_router.get("/subscribers")
async def list_subscribers(limit: int = 500, current_user: dict = Depends(require_permission("customers.view", any_in_area=True))):
    """Bülten abonelerini listeler (en yeni önce)."""
    limit = max(1, min(limit, 5000))
    rows = await db.newsletter_subscribers.find(
        {}, {"_id": 0}).sort("created_at", -1).to_list(length=limit)
    total = await db.newsletter_subscribers.count_documents({})
    return {"total": total, "subscribers": rows}
