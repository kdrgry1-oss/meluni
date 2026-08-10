"""
Authentication routes - Login, Register, Google OAuth
"""
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from datetime import datetime, timezone
import uuid
import os

from .deps import (
    db, logger, hash_password, verify_password, 
    create_token, get_current_user, generate_id,
    safe_str, is_safe_email, write_audit_log,
    is_account_locked, register_failed_login, reset_failed_login,
    is_ip_blocked, register_failed_login_ip,
    client_ip_from_request, limiter,
    validate_strong_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# A2.3: Login timing yan-kanalı savunması. Kullanıcı YOKKEN bcrypt hiç çalışmıyordu →
# yanıt daha hızlı dönüp geçerli e-postalar zamanlamayla ayırt edilebiliyordu. Var-olmayan
# kullanıcıda da bu sabit dummy hash'e karşı bcrypt.checkpw çalıştırıp süreyi eşitleriz.
_DUMMY_PW_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO3G1xIS3vJhWvNqfa5eLPBz3XjE5rLZC"

# ── Amazon DPP §7: personel/admin şifre geçmişi + yaş politikası ─────────────
_PW_HISTORY_KEEP = 10          # son 10 şifre tekrar kullanılamaz
_PW_MIN_AGE_HOURS = 24         # min yaş: 1 gün (yalnız kullanıcı-başlatan değişimde)
_PW_MAX_AGE_DAYS = 365         # max ömür: 365 gün (login'de flag)



async def _store_name() -> str:
    """Marka adi — company.py (env/panel). E-posta konu/metinlerinde kullanilir."""
    try:
        import company as _company
        return (await _company.get_company(db)).get("store_name") or ""
    except Exception:
        return ""


async def _sms_mfa_possible() -> bool:
    """Giriş sırasında SMS ile MFA gerçekten yapılabilir mi?

    SMS sağlayıcı (Netgsm vb.) panelden ayarlanmamışsa gönderim katmanı
    sessizce mock döner: kod üretilir, kaydedilir, ama telefona ASLA ulaşmaz.
    Kullanıcı da OTP ekranından çıkamaz. Bu kontrol o senaryoyu engeller.

    Env ile tamamen kapatmak için: LOGIN_SMS_MFA=off
    """
    if (os.environ.get("LOGIN_SMS_MFA") or "").strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from notification_service import sms_available
        return await sms_available(db)
    except Exception:
        # Kontrol edemiyorsak MFA'yı zorlamıyoruz — aksi halde tek bir
        # yapılandırma hatası tüm adminleri panelden kilitler.
        return False


def _parse_iso_dt(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        # KRİTİK: naive (tz'siz) timestamp'i UTC-aware'e çevir. Aksi halde
        # `datetime.now(timezone.utc) - dt` TypeError atıp login'i 500'e düşürüyordu
        # (eski password_changed_at/updated_at kayıtları tz'siz olabiliyor).
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _enforce_pw_history(user: dict, new_plain: str) -> None:
    """Yeni şifre mevcut + son geçmiştekilerden biriyle aynıysa 400 (personel/admin)."""
    _prev = ([user.get("password", "")] + list(user.get("password_history") or []))[:_PW_HISTORY_KEEP]
    for _oh in _prev:
        if _oh and verify_password(new_plain, _oh):
            raise HTTPException(
                status_code=400,
                detail=f"Yeni şifre son {_PW_HISTORY_KEEP} şifrenizden biriyle aynı olamaz.",
            )


def _next_pw_history(user: dict) -> list:
    """Mevcut hash'i geçmişin başına ekleyip son N'e kırpar."""
    _h = [user.get("password", "")] + list(user.get("password_history") or [])
    return [h for h in _h if h][:_PW_HISTORY_KEEP]

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "") or "49503095707-cahr1ntbc30lqeho6nj1pbggq3tatien.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

@router.post("/register")
@(limiter.limit("5/minute") if limiter else (lambda f: f))
async def register(request: Request):
    """Register new user.
    GÜVENLİK (O19+): Kimlik bilgileri YALNIZCA istek GÖVDESİNDEN okunur; şifrenin
    URL query'de log'a sızma yolu kaldırıldı."""
    email = password = first_name = last_name = phone = None
    if not email or not password:
        try:
            _b = await request.json()
        except Exception:
            _b = {}
        if isinstance(_b, dict):
            email = email or _b.get("email")
            password = password or _b.get("password")
            first_name = first_name or _b.get("first_name")
            last_name = last_name or _b.get("last_name")
            phone = phone or _b.get("phone")
    # Boy/kilo — beden önerisi için (opsiyonel). Gövdeden okunur.
    _body = {}
    try:
        _body = await request.json()
    except Exception:
        _body = {}
    def _num_or_none(v, lo, hi):
        try:
            n = float(v)
            return n if lo <= n <= hi else None
        except Exception:
            return None
    height_cm = _num_or_none((_body or {}).get("height_cm"), 100, 230)
    weight_kg = _num_or_none((_body or {}).get("weight_kg"), 30, 250)
    # Doğum tarihi (opsiyonel) — "YYYY-MM-DD" beklenir; geçersizse yok sayılır (kayıt bloklanmaz).
    _birth_date = ""
    _bd_raw = str((_body or {}).get("birth_date") or (_body or {}).get("dob") or "").strip()
    import re as _re_bd
    if _re_bd.match(r"^\d{4}-\d{2}-\d{2}$", _bd_raw):
        try:
            datetime.strptime(_bd_raw, "%Y-%m-%d")
            _birth_date = _bd_raw
        except Exception:
            _birth_date = ""
    email = safe_str(email or "", 256).lower().strip()
    password = safe_str(password or "", 200)
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Geçersiz e-posta adresi")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Şifre en az 6 karakter olmalı")

    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")

    # Telefon: normalize + mukerrer kontrol (ayni cep no ile ikinci hesap acilamaz)
    from notification_service import normalize_phone_tr
    phone_norm = normalize_phone_tr(safe_str(phone, 32) or "") if phone else ""
    _last10 = phone_norm[-10:]
    if len(_last10) == 10 and _last10.isdigit():
        dup_phone = await db.users.find_one({"phone": {"$regex": _last10}}, {"_id": 0, "id": 1})
        if dup_phone:
            raise HTTPException(status_code=400, detail="Bu telefon numarası zaten kayıtlı")

    user = {
        "id": generate_id(),
        "email": email,
        "password": hash_password(password),
        "first_name": safe_str(first_name, 100) or "",
        "last_name": safe_str(last_name, 100) or "",
        "phone": phone_norm,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "birth_date": _birth_date,  # Bölüm C: doğum günü kuponu otomasyonu için (opsiyonel)
        "is_admin": False,
        "is_active": True,
        "email_verified": False,  # yumuşak doğrulama — hesabı bloke etmez, sadece işaretler
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.users.insert_one(user)
    token = create_token(user["id"])

    # Bölüm C — Referans kaydı (kod girildiyse) + hoş geldin kuponu (etkinse). Best-effort:
    # kayıt akışını ASLA bloklamaz.
    try:
        from .referrals import record_referral_on_register, issue_welcome_coupon
        _ref_code = (_body.get("referral_code") or _body.get("ref") or "").strip()
        if _ref_code:
            await record_referral_on_register(user, _ref_code)
        await issue_welcome_coupon(user)
    except Exception as _rce:
        logger.warning(f"referans/hoş geldin işlemi atlandı: {_rce}")
    # E-POSTA DOĞRULAMA (yumuşak): checkout'u bloke etmez; sahte-e-posta caydırıcısı +
    # gelecekte gating için temel. Doğrulama linki e-posta ile gider (best-effort).
    try:
        import os as _os2
        vtok = secrets.token_urlsafe(32)
        await db.email_verifications.insert_one({
            "token": vtok, "user_id": user["id"], "email": email,
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + 7 * 24 * 3600,
            "used": False, "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _site = (_os2.environ.get("SITE_URL") or "").rstrip("/")
        _brand_name = await _store_name()
        _vlink = f"{_site}/api/auth/verify-email?token={vtok}"
        _vhtml = (
            f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#2a2a2a">'
            f'<h2 style="font-weight:600">E-posta adresini doğrula</h2>'
            f'<p>{_brand_name} hesabın oluşturuldu. Adresini doğrulamak için:</p>'
            f'<p style="margin:24px 0"><a href="{_vlink}" style="background:#1a1a1a;color:#fff;'
            f'padding:12px 28px;text-decoration:none;border-radius:4px">E-postamı Doğrula</a></p>'
            f'<p style="font-size:12px;color:#888">Link 7 gün geçerlidir. Bu işlemi sen yapmadıysan yok say.</p></div>'
        )
        from email_smtp import send_smtp_email
        await send_smtp_email(db, email, f"E-posta Doğrulama — {_brand_name}", _vhtml)
    except Exception as _ve:
        logger.warning(f"verification email failed for {email}: {_ve}")
    # Hoş geldin e-postası (best-effort; kaydı asla bloklamaz)
    try:
        import os as _os
        from notification_service import send_notification

        # Aktif "hoş geldin" kuponu (ilk siparişe özel) — DİNAMİK, son oluşturulan baz alınır.
        coupon_code, coupon_discount, coupon_block = "", "", ""
        try:
            _c = await db.coupons.find_one(
                {"is_active": True, "first_order_only": True},
                {"_id": 0}, sort=[("created_at", -1)],
            )
            if _c and (_c.get("code") or "").strip():
                coupon_code = _c["code"].strip()
                _v = float(_c.get("value") or 0)
                if (_c.get("type") or "percent") == "percent":
                    coupon_discount = f"%{int(_v)}" if _v == int(_v) else f"%{_v:g}"
                else:
                    coupon_discount = f"{int(_v)} TL" if _v == int(_v) else f"{_v:g} TL"
                coupon_block = (
                    '<div style="margin:20px 0 2px;border:1px dashed #c9c4bb;background:#faf8f5;'
                    'padding:24px 18px;text-align:center;">'
                    '<div style="font-size:11px;letter-spacing:3px;text-transform:uppercase;'
                    'color:#8a8579;margin:0 0 8px;">Hoş Geldin Hediyen</div>'
                    f'<div style="font-size:15px;color:#3a3631;margin:0 0 14px;">İlk siparişinde '
                    f'<b>{coupon_discount} indirim</b></div>'
                    '<div style="display:inline-block;border:1px solid #1a1a1a;padding:13px 30px;'
                    'font-size:22px;font-weight:700;letter-spacing:5px;color:#1a1a1a;">'
                    f'{coupon_code}</div>'
                    '<div style="font-size:11px;color:#a39e95;margin:12px 0 0;">'
                    'Kodu sepette uygula · ilk siparişe özel</div>'
                    '</div>'
                )
        except Exception as _ce:
            logger.warning(f"welcome coupon lookup failed: {_ce}")

        await send_notification(
            db, "welcome",
            to_email=email,
            variables={
                "customer_name": (user.get("first_name") or "").strip() or "değerli müşterimiz",
                "site_url": (_os.environ.get("SITE_URL") or "").rstrip("/"),
                "coupon_code": coupon_code,
                "coupon_discount": coupon_discount,
                "coupon_block": coupon_block,  # her zaman geçilir (boşsa "") → {coupon_block} literal kalmaz
            },
            channels=["email"],
        )
    except Exception as _e:
        logger.warning(f"welcome email failed for {email}: {_e}")
    await write_audit_log(
        "register", user_id=user["id"], email=email,
        ip=client_ip_from_request(request),
        user_agent=request.headers.get("user-agent"),
        success=True,
    )

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "is_admin": user["is_admin"]
        }
    }

@router.get("/verify-email")
async def verify_email(token: str = ""):
    """E-posta doğrulama linki hedefi. Geçerli token → email_verified=True; siteye döner."""
    from fastapi.responses import RedirectResponse
    import os as _os
    site = (_os.environ.get("SITE_URL") or "").rstrip("/")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    rec = None
    if token:
        rec = await db.email_verifications.find_one(
            {"token": token, "used": False, "expires_at": {"$gt": now_ts}})
    if not rec:
        return RedirectResponse(url=f"{site}/?email_verified=0", status_code=302)
    await db.users.update_one({"id": rec["user_id"]}, {"$set": {"email_verified": True}})
    await db.email_verifications.update_one({"token": token}, {"$set": {"used": True}})
    return RedirectResponse(url=f"{site}/?email_verified=1", status_code=302)


@router.post("/login")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def login(request: Request):
    """Login with email and password (rate-limited + lockout-protected).
    GÜVENLİK (O19+): Kimlik bilgileri YALNIZCA istek GÖVDESİNDEN okunur. Şifrenin
    URL query parametresi olarak access log / tarayıcı geçmişi / Referer'a sızma
    yolu tamamen kaldırıldı (query desteği çıkarıldı)."""
    email = None
    password = None
    if not email or not password:
        try:
            _b = await request.json()
        except Exception:
            _b = {}
        email = email or (_b.get("email") if isinstance(_b, dict) else None)
        password = password or (_b.get("password") if isinstance(_b, dict) else None)
    email = safe_str(email or "", 256).lower().strip()
    password = safe_str(password or "", 200)
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")

    if not is_safe_email(email):
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "invalid_email_format"})
        raise HTTPException(status_code=400, detail="Geçersiz e-posta veya şifre")

    # IP-level brute force blocklist (account lockout'tan önce kontrol — saldırı için DoS bypass)
    ip_blocked, ip_retry = await is_ip_blocked(ip)
    if ip_blocked:
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "ip_blocked", "retry_after": ip_retry})
        msg = "Bu IP adresinden çok fazla başarısız deneme yapıldı."
        if ip_retry > 0:
            msg += f" {max(1, ip_retry // 3600)} saat sonra tekrar deneyin."
        raise HTTPException(status_code=429, detail=msg)

    locked, retry_after = await is_account_locked(email)
    if locked:
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "locked", "retry_after": retry_after})
        raise HTTPException(status_code=429,
                            detail=f"Çok fazla başarısız deneme. {retry_after // 60 + 1} dk sonra tekrar deneyin.")

    user = await db.users.find_one({"email": email}, {"_id": 0})
    # A2.3: sabit-zaman. Kullanıcı yoksa da bcrypt çalıştır (dummy hash) → enumeration timing yok.
    _pw_ok = verify_password(password, user.get("password", "") if user else _DUMMY_PW_HASH)
    if not user or not _pw_ok:
        await register_failed_login(email)
        # IP-level threshold de tetiklensin
        await register_failed_login_ip(ip)
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "bad_credentials"})
        raise HTTPException(status_code=401, detail="Geçersiz e-posta veya şifre")

    if not user.get("is_active", True):
        await write_audit_log("login", user_id=user["id"], email=email,
                              ip=ip, user_agent=ua, success=False,
                              meta={"reason": "inactive"})
        raise HTTPException(status_code=403, detail="Hesabınız devre dışı")

    await reset_failed_login(email)

    # MFA aktifse: tam token verme, ikinci adım iste
    if user.get("mfa_enabled"):
        from .mfa import create_mfa_pending_token, send_mfa_sms_code, _mask_phone, _resolve_mfa_phone
        _method = user.get("mfa_method") or "totp"
        _pmask = ""
        if _method == "sms":
            # SMS sağlayıcı yapılandırılmamışsa kod ASLA ulaşmaz ve kullanıcı
            # OTP ekranında kilitlenir (send_notification sessizce mock döner).
            # Bu durumda SMS adımını atlayıp normal girişe izin veriyoruz.
            if not await _sms_mfa_possible():
                await write_audit_log("login_mfa_skipped_no_sms", user_id=user["id"], email=email,
                                      ip=ip, user_agent=ua, success=True,
                                      meta={"reason": "sms_provider_unavailable"})
                _method = None
            else:
                # Login'de SMS kodunu OTOMATİK gönder + maskeli numara döndür.
                try:
                    await send_mfa_sms_code(user)
                    _pmask = _mask_phone(_resolve_mfa_phone(user))
                except Exception:
                    pass
        if _method:
            await write_audit_log("login_mfa_challenge", user_id=user["id"], email=email,
                                  ip=ip, user_agent=ua, success=True)
            return {
                "mfa_required": True,
                "mfa_method": _method,
                "phone_masked": _pmask,
                "mfa_token": create_mfa_pending_token(user["id"]),
            }

    # ZORUNLU MFA (Amazon DPP): admin MFA kurmamışsa —
    #  • Hesabın KAYITLI TELEFONU varsa: kurulum ekranı GÖSTERME; OTOMATİK SMS gönder ve
    #    doğrudan OTP adımına geç (telefon girmeye gerek yok, her e-posta kendi numarasıyla).
    #  • Hiç telefon yoksa: mfa_setup_required=True (bir kere numara girilir).
    # Break-glass: env ADMIN_MFA_ENFORCE=off.
    _mfa_setup_required = False
    if user.get("is_admin"):
        try:
            from .mfa import admin_mfa_enforced, send_mfa_sms_code, create_mfa_pending_token, _mask_phone, _resolve_mfa_phone
            # SMS gönderemiyorsak zorunlu SMS-MFA admini tamamen kilitler:
            # kod gelmez, kurulum ekranı da numara ister ama doğrulayamaz.
            if await admin_mfa_enforced() and await _sms_mfa_possible():
                _auto_phone = _resolve_mfa_phone(user)
                if _auto_phone:
                    try:
                        await send_mfa_sms_code(user)
                    except Exception:
                        pass
                    await write_audit_log("login_mfa_challenge", user_id=user["id"], email=email,
                                          ip=ip, user_agent=ua, success=True)
                    return {
                        "mfa_required": True,
                        "mfa_method": "sms",
                        "phone_masked": _mask_phone(_auto_phone),
                        "mfa_token": create_mfa_pending_token(user["id"]),
                    }
                _mfa_setup_required = True
        except Exception:
            _mfa_setup_required = False

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0))
    await write_audit_log("login", user_id=user["id"], email=email,
                          ip=ip, user_agent=ua, success=True)

    # Amazon DPP §7: personel/admin şifresi 365 günden eskiyse flag (bloklamaz — panel değişim ister).
    # Bu bilgilendirme flag'i HİÇBİR koşulda login'i kıramaz → tamamen try/except'te.
    _pw_expired = False
    try:
        if user.get("is_admin"):
            _pcd = _parse_iso_dt(user.get("password_changed_at") or user.get("password_updated_at"))
            if _pcd and (datetime.now(timezone.utc) - _pcd).days > _PW_MAX_AGE_DAYS:
                _pw_expired = True
    except Exception:
        _pw_expired = False

    return {
        "token": token,
        "mfa_setup_required": _mfa_setup_required,
        "password_expired": _pw_expired,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "phone": user.get("phone", ""),
            "is_admin": user.get("is_admin", False),
            "created_at": user.get("created_at")
        }
    }

def _mask_email(e: str) -> str:
    e = (e or "").strip()
    if "@" not in e:
        return "***"
    local, _, dom = e.partition("@")
    lm = (local[0] + "***") if local else "***"
    return f"{lm}@{dom}"


@router.post("/guest-convert/send-code")
@(limiter.limit("4/minute") if limiter else (lambda f: f))
async def guest_convert_send_code(payload: dict, request: Request):
    """A2.4 — Misafir siparişini hesaba dönüştürmeden ÖNCE sipariş e-postasına 6 haneli kod
    gönderir. Böylece (ardışık, tahmin edilebilir) sipariş numarasını bilen biri, e-postaya
    erişmeden hesap açıp kurbanın PII'sini/oturumunu ele geçiremez — kod yalnız gerçek posta
    kutusuna gider. Zaten hesabı olan e-posta için kod göndermez (girişe yönlendirir)."""
    order_id = ((payload or {}).get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    order = await db.orders.find_one({"$or": [{"id": order_id}, {"order_number": order_id}]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    if order.get("user_id"):
        raise HTTPException(status_code=400, detail="Bu sipariş zaten bir hesaba bağlı")
    # Tazelik penceresi (convert ile aynı): eski numaraları tarayarak istismarı sınırlar.
    try:
        _created = order.get("created_at") or ""
        _cdt = datetime.fromisoformat(_created.replace("Z", "+00:00")) if _created else None
        if _cdt is not None:
            if _cdt.tzinfo is None:
                _cdt = _cdt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - _cdt).total_seconds() / 3600.0 > 12:
                raise HTTPException(status_code=400, detail="Bu sipariş hesaba dönüştürme için çok eski. Lütfen giriş yapın.")
    except HTTPException:
        raise
    except Exception:
        pass
    addr = order.get("shipping_address") or {}
    email = (addr.get("email") or order.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(status_code=400, detail="Sipariş e-postası bulunamadı")
    # Zaten hesabı olan e-posta/telefon → kod gönderme, girişe yönlendir (ATO koruması).
    from notification_service import normalize_phone_tr as _npn
    _phone_norm = _npn(addr.get("phone") or order.get("phone") or "")
    _last10 = _phone_norm[-10:]
    existing = await db.users.find_one({"email": email}, {"_id": 0, "id": 1})
    if not existing and len(_last10) == 10 and _last10.isdigit():
        existing = await db.users.find_one({"phone": {"$regex": _last10}}, {"_id": 0, "id": 1})
    if existing:
        return {"existing_account": True,
                "message": "Bu e-posta ile zaten bir hesabınız var. Lütfen giriş yapın; siparişiniz hesabınızda görünecektir."}
    import secrets as _secrets
    now = datetime.now(timezone.utc)
    # Bu sipariş için eski kullanılmamış kodları iptal et
    await db.guest_convert_codes.update_many(
        {"order_id": order["id"], "used": False}, {"$set": {"used": True, "invalidated": True}})
    code = f"{_secrets.randbelow(1000000):06d}"
    await db.guest_convert_codes.insert_one({
        "order_id": order["id"],
        "email": email,
        "code_hash": _hash_otp(code),
        "expires_at": now.timestamp() + 600,  # 10 dk
        "used": False,
        "attempts": 0,
        "created_at": now.isoformat(),
    })
    try:
        _brand_name = await _store_name()
        _html = (
            f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#2a2a2a">'
            f'<h2 style="font-weight:600">Hesap Oluşturma Doğrulama Kodu</h2>'
            f'<p>Siparişinizi bir hesaba dönüştürmek için doğrulama kodunuz:</p>'
            f'<p style="font-size:30px;font-weight:700;letter-spacing:6px;margin:20px 0">{code}</p>'
            f'<p style="font-size:12px;color:#888">Kod 10 dakika geçerlidir. Bu işlemi siz yapmadıysanız bu e-postayı yok sayın.</p></div>'
        )
        from email_smtp import send_smtp_email
        await send_smtp_email(db, email, f"Hesap Doğrulama Kodu — {_brand_name}", _html)
    except Exception as _e:
        logger.warning(f"guest-convert kod maili başarısız: {_e}")
    return {"sent": True, "email_masked": _mask_email(email)}


@router.post("/convert-guest-order")
@(limiter.limit("5/minute") if limiter else (lambda f: f))
async def convert_guest_order(payload: dict, request: Request):
    """Checkout sonrası guest sipariş veren kullanıcı için hızlı hesap oluşturma.
    payload: {order_id: str, password: str}
    Sipariş bilgilerinden email + first_name + last_name otomatik alınır.

    GÜVENLİK (K2): Sipariş numaraları ardışık ve tahmin edilebilir olduğundan bu uç
    HESAP ELE GEÇİRME için istismar edilebiliyordu. Artık:
      - Eğer bu e-posta ile ZATEN bir hesap varsa TOKEN VERİLMEZ ve otomatik bağlama
        yapılmaz — kullanıcı giriş yapmaya yönlendirilir (aksi halde saldırgan kurbanın
        oturumunu ele geçirebiliyordu).
      - Yalnızca YENİ ve TAZE (son birkaç saat içinde oluşturulmuş) misafir siparişleri
        için hesap açılabilir; eski siparişlerin numarasını tarayarak istismar engellenir.
      - Rate-limit uygulanır.
    """
    order_id = (payload or {}).get("order_id", "").strip()
    password = (payload or {}).get("password", "")
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id gerekli")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="Şifre en az 6 karakter olmalı")

    order = await db.orders.find_one({"$or": [{"id": order_id}, {"order_number": order_id}]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")

    if order.get("user_id"):
        raise HTTPException(status_code=400, detail="Bu sipariş zaten bir hesaba bağlı")

    # Tazelik penceresi: yalnızca yakın zamanda oluşturulmuş siparişler dönüştürülebilir.
    # Böylece eski sipariş numaralarını tarayarak yapılan ön-kayıt kaçırma saldırısı kapanır.
    try:
        _created = order.get("created_at") or ""
        _cdt = datetime.fromisoformat(_created.replace("Z", "+00:00")) if _created else None
        if _cdt is not None:
            if _cdt.tzinfo is None:
                _cdt = _cdt.replace(tzinfo=timezone.utc)
            _age_h = (datetime.now(timezone.utc) - _cdt).total_seconds() / 3600.0
            if _age_h > 12:
                raise HTTPException(status_code=400,
                                    detail="Bu sipariş hesaba dönüştürme için çok eski. Lütfen giriş yapın.")
    except HTTPException:
        raise
    except Exception:
        pass

    addr = order.get("shipping_address") or {}
    email = (addr.get("email") or order.get("email") or "").lower().strip()
    first_name = addr.get("first_name") or addr.get("full_name", "").split(" ")[0] or ""
    last_name = addr.get("last_name") or ""
    phone = addr.get("phone") or order.get("phone") or ""
    if not email:
        raise HTTPException(status_code=400, detail="Sipariş e-postası bulunamadı")

    from notification_service import normalize_phone_tr
    phone_norm = normalize_phone_tr(phone) if phone else ""
    _last10 = phone_norm[-10:]

    async def _save_guest_address(uid, _order):
        try:
            a = _order.get("shipping_address") or {}
            if not (a.get("address") and a.get("city")):
                return
            dup = await db.addresses.find_one(
                {"user_id": uid, "address": a.get("address", ""),
                 "city": a.get("city", ""), "district": a.get("district", "")},
                {"_id": 0, "id": 1})
            if dup:
                return
            has_any = await db.addresses.find_one({"user_id": uid}, {"_id": 0, "id": 1})
            await db.addresses.insert_one({
                "id": generate_id(), "user_id": uid,
                "title": a.get("title") or "Teslimat Adresi",
                "first_name": a.get("first_name", ""), "last_name": a.get("last_name", ""),
                "phone": a.get("phone", ""), "address": a.get("address", ""),
                "city": a.get("city", ""), "district": a.get("district", ""),
                "postal_code": a.get("postal_code", ""),
                "is_default": not bool(has_any),
                "is_corporate": False, "company_name": "", "tax_no": "", "tax_office": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as _e:
            logger.warning(f"convert-guest adres kaydi atlandi: {_e}")

    existing = await db.users.find_one({"email": email}, {"_id": 0, "id": 1})
    if not existing and len(_last10) == 10 and _last10.isdigit():
        # Ayni cep no baska bir hesaba aitse mukerrer uyelik acma — o hesaba bagla
        existing = await db.users.find_one({"phone": {"$regex": _last10}}, {"_id": 0, "id": 1})

    if existing:
        # GÜVENLİK (K2): Bu e-posta/telefon ile zaten bir hesap var. TOKEN VERME ve otomatik
        # bağlama YAPMA — aksi halde sipariş numarasını bilen biri kurbanın oturumunu alırdı.
        # Kullanıcıyı giriş yapmaya yönlendir; girişten sonra siparişini kendisi ilişkilendirir.
        return {
            "existing_account": True,
            "token": None,
            "message": "Bu e-posta ile zaten bir hesabınız var. Lütfen giriş yapın; siparişiniz hesabınızda görünecektir.",
        }

    # A2.4: E-POSTA DOĞRULAMA KODU zorunlu. Sipariş e-postasına gönderilen 6 haneli kod
    # doğrulanmadan hesap açılmaz → sipariş numarasını tahmin eden saldırgan, posta kutusuna
    # erişemeden kurbanın adına hesap açamaz (misafir-ATO penceresi kapanır).
    code = str((payload or {}).get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="E-posta doğrulama kodu gerekli. Lütfen size gönderilen kodu girin.")
    _now_ts = datetime.now(timezone.utc).timestamp()
    _crec = await db.guest_convert_codes.find_one(
        {"order_id": order["id"], "used": False, "expires_at": {"$gt": _now_ts}},
        sort=[("created_at", -1)])
    if not _crec:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş. Lütfen yeni kod isteyin.")
    if _crec.get("attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen yeni kod isteyin.")
    await db.guest_convert_codes.update_one({"_id": _crec["_id"]}, {"$inc": {"attempts": 1}})
    import hmac as _hmac
    if not _hmac.compare_digest(_hash_otp(code), str(_crec.get("code_hash") or "")):
        raise HTTPException(status_code=400, detail="Kod hatalı")
    # Kodun ait olduğu e-posta ile siparişin e-postası aynı olmalı (defans).
    if (_crec.get("email") or "").lower().strip() != email:
        raise HTTPException(status_code=400, detail="Kod bu siparişe ait değil")
    await db.guest_convert_codes.update_one({"_id": _crec["_id"]}, {"$set": {"used": True}})

    # Yeni hesap oluştur
    user = {
        "id": generate_id(),
        "email": email,
        "password": hash_password(password),
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone_norm or phone,
        "role": "customer",
        "is_active": True,
        "email_verified": True,  # A2.4 — e-posta kodu doğrulandı (posta kutusu sahipliği kanıtlandı)
        "source": "checkout_guest_convert",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(user)
    await db.orders.update_one({"id": order["id"]}, {"$set": {"user_id": user["id"]}})
    await _save_guest_address(user["id"], order)

    from .deps import create_token as _ct
    token = _ct(user["id"], is_admin=False)
    user.pop("_id", None)
    user.pop("password", None)
    return {"token": token, "user": user, "existing_account": False, "message": "Hesabınız oluşturuldu ve sipariş bağlandı"}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user info"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    return current_user


@router.post("/change-password")
async def change_password(
    payload: dict,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Mevcut kullanıcı kendi şifresini değiştirir.
    Body: { current_password, new_password }
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    cur = safe_str((payload or {}).get("current_password", ""), 200)
    new = safe_str((payload or {}).get("new_password", ""), 200)
    if not cur or not new:
        raise HTTPException(status_code=400, detail="Mevcut ve yeni şifre zorunlu")
    # Personel/admin hesapları için güçlü şifre politikası (Amazon DPP); müşteri min 6
    if current_user.get("is_admin"):
        validate_strong_password(new, identifiers=[current_user.get("email"), current_user.get("name")])
    elif len(new) < 6:
        raise HTTPException(status_code=400, detail="Yeni şifre en az 6 karakter olmalı")
    user = await db.users.find_one({"id": current_user["id"]})
    if not user or not verify_password(cur, user.get("password", "")):
        await write_audit_log(
            "password_change", user_id=current_user["id"], email=current_user.get("email"),
            ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
            success=False, meta={"reason": "wrong_current_password"},
        )
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı")
    # Amazon DPP §7 (personel/admin): son-10 tekrar yasağı + min yaş (24s).
    if user.get("is_admin"):
        _enforce_pw_history(user, new)
        _pca = _parse_iso_dt(user.get("password_changed_at"))
        if _pca:
            _age_h = (datetime.now(timezone.utc) - _pca).total_seconds() / 3600.0
            if 0 <= _age_h < _PW_MIN_AGE_HOURS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Şifre en erken {_PW_MIN_AGE_HOURS} saatte bir değiştirilebilir.",
                )
    # GÜVENLİK: token_version'ı bump et → çalınmış/eski TÜM oturumlar geçersizleşir.
    new_tv = int(user.get("token_version", 0) or 0) + 1
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"password": hash_password(new),
                  "password_changed_at": datetime.now(timezone.utc).isoformat(),
                  "password_history": _next_pw_history(user),
                  "token_version": new_tv}}
    )
    await write_audit_log(
        "password_change", user_id=current_user["id"], email=current_user.get("email"),
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
        success=True,
    )
    # Mevcut oturum düşmesin diye TAZE token dön (yeni tv ile). Diğer cihazlar çıkış yapar.
    fresh = create_token(current_user["id"], user.get("is_admin", False), token_version=new_tv)
    return {"success": True, "message": "Şifre güncellendi", "token": fresh}


# =============================================================================
# SMS OTP PASSWORD RESET (FAZ 3)
# =============================================================================
import random
import hashlib
import secrets
import re
from pydantic import BaseModel


class OTPRequestReq(BaseModel):
    phone: str


class OTPVerifyReq(BaseModel):
    phone: str
    code: str


class OTPResetReq(BaseModel):
    reset_token: str
    new_password: str


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/forgot-password/request-otp")
@(limiter.limit("3/minute") if limiter else (lambda f: f))
async def forgot_password_request_otp(request: Request, req: OTPRequestReq):
    """Telefon numarasına 6 haneli SMS OTP gönderir.
    Privacy: numara sistemde olmasa bile aynı yanıt döner (enumeration önleme).
    Rate limit: Aynı telefon için 60 sn içinde tek istek.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr, send_notification

    phone_norm = normalize_phone_tr(req.phone)

    # Rate limit — aynı numaraya son 60 sn içinde kod atılmışsa sessizce aynı cevabı döndür
    now = datetime.now(timezone.utc)
    recent = await db.password_reset_otps.find_one(
        {"phone": phone_norm, "created_at": {"$gt": (now.replace(microsecond=0).isoformat()[:-6])}},
        sort=[("created_at", -1)],
    )
    # Daha güvenli: zaman karşılaştırması timestamp'le
    if recent:
        try:
            prev = datetime.fromisoformat(recent["created_at"])
            if (now - prev).total_seconds() < 60:
                return {"success": True, "message": "Eğer numara sistemimizde kayıtlıysa SMS kodu gönderildi."}
        except Exception:
            pass

    user = await db.users.find_one({"phone": {"$regex": phone_norm[-10:], "$options": "i"}}, {"_id": 0, "id": 1, "email": 1, "phone": 1, "first_name": 1}) if phone_norm else None

    # Bu telefon için var olan kullanılmamış kodları iptal et
    await db.password_reset_otps.update_many(
        {"phone": phone_norm, "used": False},
        {"$set": {"used": True, "invalidated": True}},
    )

    import secrets as _secrets
    code = f"{_secrets.randbelow(1000000):06d}"  # CSPRNG — tahmin edilebilir Mersenne Twister yerine
    expires = (now.timestamp() + 300)  # 5 dk
    record = {
        "phone": phone_norm,
        "code_hash": _hash_otp(code),
        "user_id": (user or {}).get("id"),
        "expires_at": expires,
        "used": False,
        "attempts": 0,
        "created_at": now.isoformat(),
    }
    await db.password_reset_otps.insert_one(record)

    if user:
        try:
            await send_notification(
                db, "password_reset_otp",
                to_phone=phone_norm,
                variables={"otp_code": code, "customer_name": user.get("first_name", "")},
                channels=["sms"],
            )
        except Exception as e:
            logger.warning(f"OTP sms failed: {e}")
    else:
        logger.info(f"OTP request for unknown phone (silent): ***{str(phone_norm)[-4:]}")  # PII/CRLF sızıntısı yok

    return {"success": True, "message": "Eğer numara sistemimizde kayıtlıysa SMS kodu gönderildi."}


@router.post("/forgot-password/verify-otp")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def forgot_password_verify_otp(request: Request, req: OTPVerifyReq):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr

    phone_norm = normalize_phone_tr(req.phone)
    now_ts = datetime.now(timezone.utc).timestamp()

    rec = await db.password_reset_otps.find_one(
        {"phone": phone_norm, "used": False, "expires_at": {"$gt": now_ts}},
        sort=[("created_at", -1)],
    )
    if not rec:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş")

    if rec.get("attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Çok fazla deneme yaptınız")

    await db.password_reset_otps.update_one({"_id": rec["_id"]}, {"$inc": {"attempts": 1}})

    import hmac as _hmac
    if not _hmac.compare_digest(_hash_otp(req.code), str(rec.get("code_hash") or "")):
        raise HTTPException(status_code=400, detail="Kod hatalı")

    if not rec.get("user_id"):
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")

    reset_token = secrets.token_urlsafe(32)
    await db.password_reset_otps.update_one(
        {"_id": rec["_id"]},
        {"$set": {"used": True, "reset_token": reset_token, "reset_token_expires": now_ts + 600}},
    )
    return {"reset_token": reset_token, "expires_in": 600}


@router.post("/forgot-password/reset")
async def forgot_password_reset(req: OTPResetReq):
    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Şifre en az 6 karakter olmalı")

    now_ts = datetime.now(timezone.utc).timestamp()
    rec = await db.password_reset_otps.find_one(
        {"reset_token": req.reset_token, "reset_token_expires": {"$gt": now_ts}}
    )
    if not rec:
        raise HTTPException(status_code=400, detail="Reset token geçersiz veya süresi dolmuş")

    user_id = rec.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")

    # Amazon DPP §7: reset'te de son-10 tekrar yasağı (personel/admin). Min-yaş reset'te UYGULANMAZ (güvenlik).
    _u = await db.users.find_one({"id": user_id}) or {}
    if _u.get("is_admin"):
        _enforce_pw_history(_u, req.new_password)
    _pw_now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"password": hash_password(req.new_password),
                  "password_updated_at": _pw_now_iso,
                  "password_changed_at": _pw_now_iso,
                  "password_history": _next_pw_history(_u)},
         "$inc": {"token_version": 1}},  # GÜVENLİK: sıfırlama tüm eski oturumları geçersiz kılar
    )
    await db.password_reset_otps.delete_one({"_id": rec["_id"]})
    return {"success": True, "message": "Şifreniz güncellendi"}


class EmailResetReq(BaseModel):
    email: str


@router.post("/forgot-password/email")
@(limiter.limit("3/minute") if limiter else (lambda f: f))
async def forgot_password_email(request: Request, req: EmailResetReq):
    """E-posta ile şifre sıfırlama BAĞLANTISI gönderir (ZeptoMail üzerinden).
    Privacy: e-posta sistemde olmasa bile aynı yanıt döner (enumeration önleme).
    Üretilen token mevcut /forgot-password/reset endpoint'i ile tüketilir.
    """
    import os as _os
    email = (req.email or "").strip().lower()
    generic = {"success": True, "message": "Eğer e-posta adresi sistemimizde kayıtlıysa şifre sıfırlama bağlantısı gönderildi."}
    if not email or "@" not in email:
        return generic

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    user = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
        {"_id": 0, "id": 1, "email": 1, "first_name": 1},
    )
    if not user or not user.get("id"):
        _em = str(email or ""); _dom = _em.split("@")[-1] if "@" in _em else "?"
        logger.info(f"Email reset for unknown email (silent): ***@{_dom.replace(chr(10),'').replace(chr(13),'')}")  # PII/CRLF sızıntısı yok
        return generic

    # Rate limit — aynı e-postaya son 60 sn içinde link atıldıysa sessizce aynı cevap
    recent = await db.password_reset_otps.find_one(
        {"email": email, "channel": "email"}, sort=[("created_at", -1)]
    )
    if recent:
        try:
            if (now - datetime.fromisoformat(recent["created_at"])).total_seconds() < 60:
                return generic
        except Exception:
            pass

    # Önceki e-posta token'larını geçersiz kıl
    await db.password_reset_otps.update_many(
        {"email": email, "channel": "email"},
        {"$set": {"invalidated": True, "reset_token_expires": 0}},
    )

    reset_token = secrets.token_urlsafe(32)
    await db.password_reset_otps.insert_one({
        "email": email,
        "channel": "email",
        "user_id": user.get("id"),
        "used": True,  # OTP doğrulama adımı yok; token doğrudan geçerli
        "reset_token": reset_token,
        "reset_token_expires": now_ts + 1800,  # 30 dk
        "created_at": now.isoformat(),
    })

    site = (_os.environ.get("SITE_URL") or "").rstrip("/")
    link = f"{site}/sifre-sifirla?token={reset_token}"
    name = (user.get("first_name") or "").strip()
    _brand_name = await _store_name()
    from email_layout import email_shell
    html = email_shell(
        icon="🔒",
        eyebrow="HESAP GÜVENLİĞİ",
        title="Şifrenizi sıfırlayın",
        intro_html=(
            "Merhaba " + (name or "değerli müşterimiz") + ",<br>"
            f"{_brand_name} hesabınız için bir şifre sıfırlama talebi aldık. "
            "Yeni bir şifre belirlemek için aşağıdaki butona tıklamanız yeterli."
        ),
        cta_text="ŞİFREYİ SIFIRLA", cta_url=link,
        fallback_url=link,
        note_title="Bu bağlantı 30 dakika geçerlidir.",
        note_html="Eğer bu talebi siz oluşturmadıysanız bu e-postayı yok sayabilirsiniz; şifreniz değişmeden kalır.",
        preheader="Şifre sıfırlama bağlantınız",
    )
    try:
        from email_smtp import send_smtp_email
        res = await send_smtp_email(db, user["email"], f"Şifre Sıfırlama — {_brand_name}", html)
        if not res.get("success"):
            logger.warning(f"reset email send failed for {email}: {res.get('response')}")
    except Exception as e:
        logger.warning(f"reset email exception for {email}: {e}")

    return generic

@router.post("/google")
async def google_signin(request: Request, payload: dict):
    """Standart Google Sign-In — frontend GIS'ten gelen ID token'i dogrular,
    kullaniciyi olusturur/bulur ve uygulama JWT'sini dondurur (kendi Client ID'miz)."""
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
    except Exception:
        raise HTTPException(status_code=500, detail="Google dogrulama kutuphanesi yuklu degil")

    credential = safe_str((payload or {}).get("credential", ""), 4096)
    if not credential:
        raise HTTPException(status_code=400, detail="Google kimlik tokeni eksik")
    try:
        idinfo = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Google tokeni dogrulanamadi")

    if not idinfo.get("email_verified", False):
        raise HTTPException(status_code=400, detail="Google e-postasi dogrulanmamis")
    email = safe_str(idinfo.get("email", ""), 256).lower().strip()
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Google hesabindan gecerli e-posta alinamadi")
    name = safe_str(idinfo.get("name", ""), 200)
    picture = safe_str(idinfo.get("picture", ""), 500)
    first_name, _, last_name = name.partition(" ")
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")

    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user:
        user = {
            "id": generate_id(),
            "email": email,
            "password": hash_password(uuid.uuid4().hex),  # Google kullanicisi
            "first_name": first_name or name or email.split("@")[0],
            "last_name": last_name or "",
            "phone": "",
            "is_admin": False,
            "is_active": True,
            "auth_provider": "google",
            "picture": picture,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(user)
    else:
        updates = {}
        if not user.get("auth_provider"):
            updates["auth_provider"] = "google"
        if picture and not user.get("picture"):
            updates["picture"] = picture
        if updates:
            await db.users.update_one({"id": user["id"]}, {"$set": updates})

    if not user.get("is_active", True):
        await write_audit_log("google_login", user_id=user["id"], email=email, ip=ip,
                              user_agent=ua, success=False, meta={"reason": "inactive"})
        raise HTTPException(status_code=403, detail="Hesabiniz devre disi")

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0))
    await write_audit_log("google_login", user_id=user["id"], email=email, ip=ip,
                          user_agent=ua, success=True)
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "is_admin": user.get("is_admin", False),
            "picture": user.get("picture", ""),
        },
    }


@router.post("/google/session")
async def google_session(request: Request, session_id: str = Query(...)):
    """Emergent-managed Google Auth — session_id'yi kullanıcı + app JWT token'a çevirir.

    Frontend, auth.emergentagent.com'dan dönen session_id'yi buraya POST eder.
    Backend, Emergent session-data API'sini çağırıp kullanıcıyı oluşturur/günceller
    ve uygulamanın kendi JWT token'ını döndürür (mevcut JWT auth ile uyumlu).
    """
    import httpx
    session_id = safe_str(session_id, 512)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": session_id},
            )
    except Exception:
        raise HTTPException(status_code=502, detail="Kimlik doğrulama servisine ulaşılamadı")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Google oturumu doğrulanamadı")

    data = resp.json()
    email = safe_str(data.get("email", ""), 256).lower().strip()
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Google hesabından geçerli e-posta alınamadı")
    name = safe_str(data.get("name", ""), 200)
    picture = safe_str(data.get("picture", ""), 500)
    first_name, _, last_name = name.partition(" ")
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")

    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user:
        user = {
            "id": generate_id(),
            "email": email,
            "password": hash_password(uuid.uuid4().hex),  # Google kullanıcısı — kullanılamaz şifre
            "first_name": first_name or name or email.split("@")[0],
            "last_name": last_name or "",
            "phone": "",
            "is_admin": False,
            "is_active": True,
            "auth_provider": "google",
            "picture": picture,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(user)
    else:
        updates = {}
        if not user.get("auth_provider"):
            updates["auth_provider"] = "google"
        if picture and not user.get("picture"):
            updates["picture"] = picture
        if updates:
            await db.users.update_one({"id": user["id"]}, {"$set": updates})

    if not user.get("is_active", True):
        await write_audit_log("google_login", user_id=user["id"], email=email, ip=ip,
                              user_agent=ua, success=False, meta={"reason": "inactive"})
        raise HTTPException(status_code=403, detail="Hesabınız devre dışı")

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0))
    await write_audit_log("google_login", user_id=user["id"], email=email, ip=ip,
                          user_agent=ua, success=True)
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "is_admin": user.get("is_admin", False),
            "picture": user.get("picture", ""),
        },
    }


@router.get("/google/login")
async def google_login(request: Request):
    """Initiate Google OAuth login.
    GÜVENLİK: Bu legacy redirect akışı `state` (CSRF) korumasından yoksundu ve token'ı
    URL'de döndürüyordu; frontend yalnız modern /auth/google (GIS) kullanıyor. Devre dışı."""
    raise HTTPException(status_code=410, detail="Bu giriş yöntemi kaldırıldı. Google ile Giriş'i kullanın.")
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth yapılandırılmamış")
    
    # Get the base URL from request
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/api/auth/google/callback"
    
    google_auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=email%20profile"
        f"&access_type=offline"
    )
    
    return {"auth_url": google_auth_url}

@router.get("/google/callback")
async def google_callback(code: str, request: Request):
    """Handle Google OAuth callback.
    GÜVENLİK: `state` doğrulaması yoktu (login-CSRF) ve token URL query'de dönüyordu
    (Referer sızıntısı). Legacy + kullanılmıyor → devre dışı."""
    raise HTTPException(status_code=410, detail="Bu giriş yöntemi kaldırıldı.")
    import httpx

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth yapılandırılmamış")
    
    base_url = str(request.base_url).rstrip('/')
    redirect_uri = f"{base_url}/api/auth/google/callback"
    
    # Exchange code for token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Google token alınamadı")
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        
        # Get user info
        user_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if user_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Google kullanıcı bilgisi alınamadı")
        
        google_user = user_response.json()
    
    # Find or create user
    email = google_user.get("email", "").lower()
    user = await db.users.find_one({"email": email})
    
    if not user:
        user = {
            "id": generate_id(),
            "email": email,
            "password": "",  # No password for OAuth users
            "first_name": google_user.get("given_name", ""),
            "last_name": google_user.get("family_name", ""),
            "google_id": google_user.get("id"),
            "avatar": google_user.get("picture"),
            "is_admin": False,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(user)
    else:
        # Update Google info
        await db.users.update_one(
            {"email": email},
            {"$set": {
                "google_id": google_user.get("id"),
                "avatar": google_user.get("picture"),
            }}
        )
        user = await db.users.find_one({"email": email}, {"_id": 0, "password": 0})

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0))

    # Güvenlik: yanıt gövdesinde bcrypt şifre hash'i ASLA dönmemeli.
    user.pop("password", None)
    user.pop("_id", None)

    # Redirect to frontend with token
    frontend_url = os.environ.get("FRONTEND_URL", "")
    if frontend_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"{frontend_url}/auth/callback?token={token}")

    return {"token": token, "user": user}
