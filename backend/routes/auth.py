"""
Authentication routes - Login, Register, Google OAuth
"""
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlencode
import hashlib
import hmac
import uuid
import os
import secrets

from .deps import (
    db, logger, hash_password, verify_password, secure_social_link,
    create_token, get_current_user, generate_id, require_admin,
    safe_str, is_safe_email, write_audit_log,
    is_account_locked, register_failed_login, reset_failed_login,
    is_ip_blocked, register_failed_login_ip,
    client_ip_from_request, limiter,
    validate_strong_password, now_ts, invalidate_password_reset_tokens,
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

# Google OAuth Configuration. Client ID secret değildir; yine de marka/ortam sabiti
# olarak koda gömülmez. Client secret yalnız deployment secret'ından okunur.
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


async def _google_client_ids() -> list[str]:
    configured = []
    raw = ",".join(filter(None, [
        os.environ.get("GOOGLE_CLIENT_IDS", ""),
        os.environ.get("GOOGLE_CLIENT_ID", ""),
    ]))
    configured.extend(x.strip() for x in raw.split(",") if x.strip())
    try:
        social = await db.settings.find_one(
            {"id": "social_auth"}, {"_id": 0, "google_client_id": 1, "google_enabled": 1}
        ) or {}
        if "google_enabled" in social and not social.get("google_enabled"):
            return []
        if social.get("google_client_id"):
            configured.append(safe_str(social["google_client_id"], 512))
    except Exception:
        pass
    return list(dict.fromkeys(x for x in configured if x))


async def _google_user_from_credential(credential: str, expected_nonce: str = "") -> dict:
    """Google GIS ID tokenini doğrula ve yerel kullanıcıyı bul/oluştur."""
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
    except Exception:
        raise HTTPException(status_code=500, detail="Google dogrulama kutuphanesi yuklu degil")

    credential = safe_str(credential, 4096)
    if not credential:
        raise HTTPException(status_code=400, detail="Google kimlik tokeni eksik")
    idinfo = None
    audiences = await _google_client_ids()
    if not audiences:
        raise HTTPException(status_code=503, detail="Google ile giris yapilandirilmamis")
    for audience in audiences:
        try:
            idinfo = google_id_token.verify_oauth2_token(
                credential, google_requests.Request(), audience
            )
            break
        except Exception:
            continue
    if not idinfo:
        raise HTTPException(status_code=401, detail="Google tokeni dogrulanamadi")
    if expected_nonce:
        token_nonce = safe_str(idinfo.get("nonce", ""), 512)
        if not token_nonce or not hmac.compare_digest(token_nonce, expected_nonce):
            raise HTTPException(status_code=401, detail="Google giris guvenlik kodu dogrulanamadi")

    if not idinfo.get("email_verified", False):
        raise HTTPException(status_code=400, detail="Google e-postasi dogrulanmamis")
    email = safe_str(idinfo.get("email", ""), 256).lower().strip()
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Google hesabindan gecerli e-posta alinamadi")
    name = safe_str(idinfo.get("name", ""), 200)
    picture = safe_str(idinfo.get("picture", ""), 500)
    first_name, _, last_name = name.partition(" ")
    # GÜVENLİK (denetim 2026-10, #9): Google hesabının DEĞİŞMEZ kimliği `sub`'dır; e-posta değişebilir /
    # yeniden atanabilir (ör. Workspace'te silinip başkasına verilen adres). Hesap önce sub ile
    # aranır; e-posta ile bulunan hesap BAŞKA bir sub'a bağlıysa giriş reddedilir. İlk girişte sub
    # hesaba yazılır.
    sub = safe_str(idinfo.get("sub", ""), 256).strip()
    if not sub:
        raise HTTPException(status_code=401, detail="Google tokeni dogrulanamadi")

    user = await db.users.find_one({"auth_providers.google.sub": sub}, {"_id": 0})
    if not user:
        user = await db.users.find_one({"email": email}, {"_id": 0})
        if user:
            _bound = safe_str(((user.get("auth_providers") or {}).get("google") or {}).get("sub", ""), 256)
            if _bound and not hmac.compare_digest(_bound, sub):
                logger.warning("[auth] Google sub uyuşmazlığı — giriş reddedildi")
                raise HTTPException(status_code=409, detail=_GOOGLE_MISMATCH_MSG)
    # Personel/admin hesabı sosyal girişle AÇILAMAZ: parola + MFA zorunlu (kilit/MFA atlanmasın).
    if user and (user.get("is_admin") is True or user.get("is_super_admin") is True):
        raise HTTPException(status_code=403, detail=_GOOGLE_ADMIN_MSG)
    _now_iso = datetime.now(timezone.utc).isoformat()
    if not user:
        user = {
            "id": generate_id(),
            "email": email,
            "password": hash_password(uuid.uuid4().hex),
            "first_name": first_name or name or email.split("@")[0],
            "last_name": last_name or "",
            "phone": "",
            "is_admin": False,
            "is_active": True,
            "auth_provider": "google",
            "auth_providers": {"google": {"sub": sub, "linked_at": _now_iso}},
            "picture": picture,
            "created_at": _now_iso,
        }
        await db.users.insert_one(user)
        user.pop("_id", None)
    else:
        user = await secure_social_link(user, "google")
        updates = {}
        if not user.get("auth_provider"):
            updates["auth_provider"] = "google"
        if picture and not user.get("picture"):
            updates["picture"] = picture
        if not ((user.get("auth_providers") or {}).get("google") or {}).get("sub"):
            updates["auth_providers.google.sub"] = sub
            updates["auth_providers.google.linked_at"] = _now_iso
        if updates:
            # Yarış koruması: sub'ı yalnız henüz bağlanmamışsa yaz.
            _flt = {"id": user["id"]}
            if "auth_providers.google.sub" in updates:
                _flt["$or"] = [{"auth_providers.google.sub": {"$exists": False}},
                               {"auth_providers.google.sub": {"$in": [None, ""]}}]
            _r = await db.users.update_one(_flt, {"$set": updates})
            if "auth_providers.google.sub" in updates and getattr(_r, "matched_count", 1) == 0:
                _fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "auth_providers": 1}) or {}
                _b2 = safe_str(((_fresh.get("auth_providers") or {}).get("google") or {}).get("sub", ""), 256)
                if _b2 and not hmac.compare_digest(_b2, sub):
                    raise HTTPException(status_code=409, detail=_GOOGLE_MISMATCH_MSG)
            for _k, _v in updates.items():
                if not _k.startswith("auth_providers."):
                    user[_k] = _v

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Hesabiniz devre disi")
    return user


_GOOGLE_ADMIN_MSG = ("Yönetici hesapları Google ile giriş yapamaz. Lütfen e-posta ve şifrenizle "
                     "(doğrulama koduyla) giriş yapın.")
_GOOGLE_MISMATCH_MSG = ("Bu e-posta adresi farklı bir Google hesabına bağlı. Lütfen e-posta ve şifrenizle "
                        "giriş yapın.")


def _google_error_code(exc: HTTPException) -> str:
    if exc.detail == _GOOGLE_ADMIN_MSG:
        return "admin_account"
    if exc.detail == _GOOGLE_MISMATCH_MSG:
        return "account_mismatch"
    return "inactive" if exc.status_code == 403 else "verification"


def _public_auth_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "is_admin": user.get("is_admin", False),
        "picture": user.get("picture", ""),
    }

def _phone_reset_trusted_filter() -> dict:
    """SMS ile şifre sıfırlamada telefonun GÜVENİLİR sayılma koşulu (denetim #2).
    - phone_verified=False olan numara asla hedef olamaz.
    - Sosyal girişle sahipliği sonradan kesinleşen hesapta (social_link_secured_at) numara, hesabı
      önceden açan kişiye ait olabilir → yalnız sonradan açıkça doğrulandıysa (True) güvenilir.
    Eski (alanı olmayan) hesaplar geriye uyumluluk için güvenilir kalır."""
    # $and içinde: phone_lookup() kendi "$or" koşulunu döndürür — çakışıp ezilmesin.
    return {"phone_verified": {"$ne": False},
            "$and": [{"$or": [{"phone_verified": True}, {"social_link_secured_at": {"$exists": False}}]}]}


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
    first_name = safe_str(first_name, 100).strip()
    last_name = safe_str(last_name, 100).strip()
    if not first_name or not last_name:
        raise HTTPException(status_code=400, detail="Ad ve soyad zorunludur")
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Geçersiz e-posta adresi")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Şifre en az 8 karakter olmalı")

    # AUTH14: toplu hesap-varlığı taramasına karşı ağ başına tavan + kademeli bekleme + alarm.
    from security.register_guard import check_before_register, note_exists_hit
    from routes.deps import client_ip_from_request
    _reg_net = await check_before_register(db, client_ip_from_request(request))

    existing = await db.users.find_one({"email": email})
    if existing:
        await note_exists_hit(db, _reg_net)
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")

    # Telefon: normalize + mukerrer kontrol (ayni cep no ile ikinci hesap acilamaz)
    from notification_service import normalize_phone_tr
    phone_norm = normalize_phone_tr(safe_str(phone, 32) or "") if phone else ""
    _last10 = phone_norm[-10:]
    if len(_last10) == 10 and _last10.isdigit():
        # KVKK: telefon şifreli saklanır → mükerrer kontrolü HASH eşitliğiyle (regex/parça tarama YOK).
        dup_phone = await db.users.find_one(_contact.phone_lookup("phone", _last10), {"_id": 0, "id": 1})
        if dup_phone:
            await note_exists_hit(db, _reg_net)
            raise HTTPException(status_code=400, detail="Bu telefon numarası zaten kayıtlı")

    user = {
        "id": generate_id(),
        "email": email,
        "password": hash_password(password),
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone_norm,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "birth_date": _birth_date,  # Bölüm C: doğum günü kuponu otomasyonu için (opsiyonel)
        "is_admin": False,
        "is_active": True,
        "email_verified": False,  # yumuşak doğrulama — hesabı bloke etmez, sadece işaretler
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Denetim #2: kayıtta telefon OTP'siz alınır → numara sahibi kanıtlanana (İYS/OTP doğrulaması)
    # kadar SMS ile şifre sıfırlama/MFA hedefi OLAMAZ. Sıfırlama e-posta ile her zaman mümkündür.
    if phone_norm:
        user["phone_verified"] = False
        user["phone_unverified_reason"] = "registered_without_otp"
    # KVKK (silme kaydı / tombstone): daha önce silinmiş bir kişi yeniden kayıt olabilir, ama
    # pazarlama izni kendiliğinden AÇILMAZ ve hoş geldin kuponu / davet ödülü TEKRAR verilmez.
    _kvkk_reregister = False
    try:
        from .kvkk import find_deletion_tombstone
        if await find_deletion_tombstone(email, phone_norm):
            _kvkk_reregister = True
            user["accepts_marketing"] = False
            user["reregistered_after_kvkk_deletion"] = True
    except Exception as _te:
        logger.warning(f"KVKK silme kaydı kontrolü atlandı: {_te}")

    await db.users.insert_one(_contact.seal_doc(dict(user), "user"))  # KVKK: telefon şifreli
    token = create_token(user["id"], auth_time=now_ts())

    # Bölüm C — Referans kaydı (kod girildiyse) + hoş geldin kuponu (etkinse). Best-effort:
    # kayıt akışını ASLA bloklamaz.
    try:
        from .referrals import record_referral_on_register, issue_welcome_coupon
        _ref_code = (_body.get("referral_code") or _body.get("ref") or "").strip()
        if not _kvkk_reregister:
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
        logger.warning("verification email failed (%s)", type(_ve).__name__)
    # Hoş geldin e-postası (best-effort; kaydı asla bloklamaz). Kod kutusu welcome_mail'de
    # üretilir (popup/bülten aboneliği de aynı yolu kullanır).
    try:
        from welcome_mail import send_welcome_email
        await send_welcome_email(db, email, user.get("first_name") or "")
    except Exception as _e:
        logger.warning("welcome email failed (%s)", type(_e).__name__)
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

def _verify_site_url() -> str:
    return (os.environ.get("SITE_URL") or "https://meluni.com.tr").rstrip("/")


async def _verify_email_rec(token: str):
    token = safe_str(token, 256).strip()
    if not token:
        return None
    now_ts_ = int(datetime.now(timezone.utc).timestamp())
    return await db.email_verifications.find_one(
        {"token": token, "used": False, "expires_at": {"$gt": now_ts_}})


@router.get("/verify-email")
async def verify_email(token: str = ""):
    """E-posta doğrulama linki hedefi — YALNIZ onay sayfası gösterir, doğrulama YAPMAZ.
    GÜVENLİK (denetim 2026-10, #2): eskiden tek bir GET doğruluyordu → e-posta güvenlik
    tarayıcıları/önizleyiciler linki otomatik açınca, başkasının e-postasıyla açılmış hesap
    kendiliğinden "doğrulanmış" oluyordu. Artık doğrulama yalnız kullanıcının butona basmasıyla
    (POST) yapılır. Eski e-postalardaki linkler çalışmaya devam eder (bu sayfaya düşer)."""
    from fastapi.responses import HTMLResponse, RedirectResponse
    import html as _h
    site = _verify_site_url()
    rec = await _verify_email_rec(token)
    if not rec:
        return RedirectResponse(url=f"{site}/giris?email_verified=0", status_code=302)
    logo = ""
    try:
        from company import get_company
        logo = (await get_company(db)).get("logo_url") or ""
    except Exception:
        logo = ""
    logo = logo or f"{site}/logo.png"
    page = (
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>E-posta doğrulama</title></head>'
        '<body style="margin:0;background:#f5f5f5;font-family:Arial,sans-serif;color:#2a2a2a">'
        '<div style="max-width:420px;margin:64px auto;background:#fff;border-radius:8px;'
        'padding:32px 24px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.08)">'
        f'<img src="{_h.escape(logo, quote=True)}" alt="" style="height:36px;width:auto;margin:0 auto 24px;display:block">'
        '<h1 style="font-size:20px;font-weight:600;margin:0 0 12px">E-posta adresini doğrula</h1>'
        '<p style="font-size:14px;line-height:1.5;margin:0 0 24px">Hesabının e-posta adresini doğrulamak '
        'için aşağıdaki butona bas.</p>'
        '<form method="post" action="verify-email" style="margin:0">'
        f'<input type="hidden" name="token" value="{_h.escape(str(rec.get("token") or ""), quote=True)}">'
        '<button type="submit" style="background:#1a1a1a;color:#fff;border:0;border-radius:4px;'
        'padding:12px 28px;font-size:14px;cursor:pointer">E-postamı Doğrula</button></form>'
        '<p style="font-size:12px;color:#888;line-height:1.5;margin:24px 0 0">Bu hesabı sen oluşturmadıysan '
        'butona basma. Doğruladıktan sonra hesaptaki açık oturumlar kapatılır; şifreni bilmiyorsan '
        '"Şifremi unuttum" ile yeni şifre belirleyebilirsin.</p>'
        '</div></body></html>'
    )
    return HTMLResponse(content=page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/verify-email")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def verify_email_confirm(request: Request):
    """Onay sayfasındaki butonun hedefi (form POST; JSON {token} da kabul edilir).
    Geçerli token → email_verified=True. Hesap ŞİFREYLE kayıt olmuşsa (sosyal sağlayıcı değil)
    token_version artırılır: e-postanın gerçek sahibi doğrulayınca, adresi kullanıp hesabı açmış
    olabilecek başka birinin AÇIK OTURUMLARI düşer (meşru kullanıcı yeniden giriş yapar)."""
    from fastapi.responses import RedirectResponse
    site = _verify_site_url()
    token = ""
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            _b = await request.json()
            token = (_b or {}).get("token", "") if isinstance(_b, dict) else ""
        else:
            raw = await request.body()
            if len(raw) <= 4096:
                token = (parse_qs(raw.decode("utf-8"), keep_blank_values=True).get("token") or [""])[0]
    except Exception:
        token = ""
    rec = await _verify_email_rec(token)
    if not rec:
        return RedirectResponse(url=f"{site}/giris?email_verified=0", status_code=303)
    # Token'ı ATOMİK tüket (çift tıklama / eşzamanlı istekte tek kez işlenir).
    used = await db.email_verifications.find_one_and_update(
        {"token": rec["token"], "used": False},
        {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}})
    if not used:
        return RedirectResponse(url=f"{site}/giris?email_verified=1", status_code=303)
    u = await db.users.find_one({"id": rec["user_id"]},
                                {"_id": 0, "id": 1, "email": 1, "email_verified": 1, "auth_provider": 1})
    # Kayıttan sonra hesabın e-postası değiştiyse eski doğrulama linki yeni adresi doğrulamaz.
    if not u or (u.get("email") or "").lower() != (rec.get("email") or "").lower():
        return RedirectResponse(url=f"{site}/giris?email_verified=0", status_code=303)
    if u.get("email_verified") is not True:
        _upd = {"$set": {"email_verified": True,
                         "email_verified_at": datetime.now(timezone.utc).isoformat()}}
        if not u.get("auth_provider"):  # şifreyle kayıt (sosyal sağlayıcı hesabı değil)
            _upd["$inc"] = {"token_version": 1}
        await db.users.update_one({"id": u["id"], "email_verified": {"$ne": True}}, _upd)
        await write_audit_log("email_verified", user_id=u["id"], email=u.get("email"),
                              ip=client_ip_from_request(request),
                              user_agent=request.headers.get("user-agent"), success=True,
                              meta={"sessions_revoked": not u.get("auth_provider")})
    return RedirectResponse(url=f"{site}/giris?email_verified=1", status_code=303)


_MFA_UNAVAILABLE_MSG = "Güvenlik doğrulaması şu an yapılamıyor, tekrar deneyin"


def _admin_mfa_break_glass_env() -> bool:
    """mfa.py'deki acil kapatmanın (ADMIN_MFA_ENFORCE=off) import'suz kopyası — mfa modülü
    yüklenemese bile acil kapatma çalışsın diye."""
    return (os.environ.get("ADMIN_MFA_ENFORCE") or "").strip().lower() in ("0", "off", "false", "no", "disable")


async def _mfa_challenge_response(user: dict, *, email: str, ip: str, ua: str, auto: bool) -> dict:
    """Login 2. adım yanıtı: kodu uygun kanaldan gönderir (TOTP hesabında göndermez) ve
    mfa_token döner. Admin hesaplarında kanal politikası (denetim 2026-10, #2) uygulanır:
    son 24 saatteki şifre sıfırlama kanalı ve (TOTP/telefon varken) e-posta kullanılmaz."""
    from .mfa import (create_mfa_pending_token, send_mfa_sms_code, send_mfa_email_code,
                      _mask_phone, _mask_email, _resolve_mfa_phone, _resolve_mfa_email,
                      admin_mfa_plan, admin_mfa_notice, notify_admin_mfa_degraded)
    _method = "sms" if auto else (user.get("mfa_method") or "totp")
    _ph, _em = _resolve_mfa_phone(user), _resolve_mfa_email(user)
    _plan = admin_mfa_plan(user)
    _policy = bool(_plan.get("policy"))
    _chan = ""
    if _method == "sms":
        # KANAL (kullanıcı isteği): kişinin varsayılanı — SMS ya da e-posta. Giriş ekranından
        # diğer kanal alternatif olarak seçilebilir (/auth/mfa/send?channel=...).
        _chan = str(user.get("mfa_channel_default") or "sms").lower()
        if _chan not in ("sms", "email"):
            _chan = "sms"
        if _policy:
            _code_ok = [c for c in _plan["allowed"] if c in ("sms", "email")]
            if _chan not in _code_ok:
                _chan = _code_ok[0] if _code_ok else ""
            if not _code_ok and "totp" in _plan["allowed"]:
                _method = "totp"  # tek izinli faktör authenticator
            if "sms" not in _code_ok:
                _ph = ""   # izinsiz kanal maskesi dönmez → ekranda o buton pasif
            if "email" not in _code_ok:
                _em = ""
        else:
            if _chan == "email" and not _em:
                _chan = "sms"
            if _chan == "sms" and not _ph and _em:
                _chan = "email"
        if _chan:
            try:
                if _chan == "email":
                    await send_mfa_email_code(user)
                else:
                    await send_mfa_sms_code(user)
            except Exception:
                pass
    if _method != "sms":
        _ph = _em = ""  # authenticator adımı: kanal butonları/maskeleri yok (eski davranış)
    if _policy and _plan.get("degraded"):
        await notify_admin_mfa_degraded(user, _plan, ip=ip, ua=ua)
    await write_audit_log("login_mfa_challenge", user_id=user["id"], email=email,
                          ip=ip, user_agent=ua, success=True,
                          meta={"channel": _chan, "allowed": _plan.get("allowed"),
                                "reset_channel": _plan.get("reset_channel") or "",
                                "degraded": bool(_plan.get("degraded"))})
    return {
        "mfa_required": True,
        "mfa_method": _method,
        "mfa_channel": _chan or _method,
        "phone_masked": _mask_phone(_ph) if _ph else "",
        "email_masked": _mask_email(_em) if _em else "",
        "mfa_notice": admin_mfa_notice(_plan),
        "mfa_token": create_mfa_pending_token(user["id"], token_version=user.get("token_version", 0)),
    }


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

    # Denetim 2026-10 #8: kilit IP+hesap bazlı (başkası sizin hesabınızı kilitleyemez) + hesap-geneli
    # yüksek eşik (personelde artan bekleme). Ayrıntı: deps.is_account_locked.
    locked, retry_after = await is_account_locked(email, ip)
    if locked:
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "locked", "retry_after": retry_after})
        _wait = (f"{retry_after // 60 + 1} dk" if retry_after >= 60 else f"{max(1, retry_after)} sn")
        raise HTTPException(status_code=429,
                            detail=f"Çok fazla başarısız deneme. {_wait} sonra tekrar deneyin.")

    user = await db.users.find_one({"email": email}, {"_id": 0})
    from .deps import open_user_phone as _open_user_phone
    _open_user_phone(user)  # KVKK: şifreli telefon yanıt için bellekte açılır (DB'ye geri yazılmaz)
    # A2.3: sabit-zaman. Kullanıcı yoksa da bcrypt çalıştır (dummy hash) → enumeration timing yok.
    _pw_ok = verify_password(password, user.get("password", "") if user else _DUMMY_PW_HASH)
    if not user or not _pw_ok:
        await register_failed_login(email, ip)
        # Önce bu başarısız deneme kaydedilir, SONRA IP eşiği sayılır (eskiden sayım kaydı
        # yazmadan yapılıyordu → ban 50 yerine 51. denemede devreye giriyordu).
        await write_audit_log("login", email=email, ip=ip, user_agent=ua,
                              success=False, meta={"reason": "bad_credentials"})
        await register_failed_login_ip(ip)
        raise HTTPException(status_code=401, detail="Geçersiz e-posta veya şifre")

    if not user.get("is_active", True):
        await write_audit_log("login", user_id=user["id"], email=email,
                              ip=ip, user_agent=ua, success=False,
                              meta={"reason": "inactive"})
        raise HTTPException(status_code=403, detail="Hesabınız devre dışı")

    await reset_failed_login(email, ip)

    # MFA aktifse: tam token verme, ikinci adım iste
    if user.get("mfa_enabled"):
        try:
            return await _mfa_challenge_response(user, email=email, ip=ip, ua=ua, auto=False)
        except HTTPException:
            raise
        except Exception as _mx:
            logger.error(f"[auth] MFA challenge hatası ({type(_mx).__name__}) — giriş reddedildi")
            raise HTTPException(status_code=503, detail=_MFA_UNAVAILABLE_MSG)

    # ZORUNLU MFA (Amazon DPP): admin MFA kurmamışsa —
    #  • Hesabın KAYITLI TELEFONU/E-POSTASI varsa: kurulum ekranı GÖSTERME; OTOMATİK kod gönder ve
    #    doğrudan OTP adımına geç (telefon girmeye gerek yok, her e-posta kendi numarasıyla).
    #  • Hiç kanal yoksa: mfa_setup_required=True (bir kere numara girilir).
    # Break-glass: env ADMIN_MFA_ENFORCE=off.
    # GÜVENLİK (denetim 2026-10, #1): bu blokta beklenmeyen bir hata olursa eskiden
    # _mfa_setup_required=False ile MFA'SIZ tam admin oturumu veriliyordu (fail-open). Artık
    # FAIL-CLOSED: acil kapatma (env) açık değilse giriş 503 ile reddedilir.
    _mfa_setup_required = False
    if user.get("is_admin"):
        try:
            from .mfa import admin_mfa_enforced, admin_mfa_plan
            if await admin_mfa_enforced():
                _plan = admin_mfa_plan(user)
                if any(c in ("sms", "email") for c in _plan.get("allowed") or []):
                    return await _mfa_challenge_response(user, email=email, ip=ip, ua=ua, auto=True)
                _mfa_setup_required = True
        except HTTPException:
            raise
        except Exception as _mx:
            if _admin_mfa_break_glass_env():
                logger.error(f"[auth] admin MFA kontrol hatası ({type(_mx).__name__}) — "
                             "ADMIN_MFA_ENFORCE=off (acil kapatma) nedeniyle MFA'sız giriş")
                _mfa_setup_required = False
            else:
                logger.error(f"[auth] admin MFA kontrol hatası ({type(_mx).__name__}) — giriş reddedildi")
                await write_audit_log("login", user_id=user["id"], email=email, ip=ip, user_agent=ua,
                                      success=False, meta={"reason": "mfa_check_error"})
                raise HTTPException(status_code=503, detail=_MFA_UNAVAILABLE_MSG)

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0),
                         auth_time=now_ts())
    await write_audit_log("login", user_id=user["id"], email=email,
                          ip=ip, user_agent=ua, success=True)
    if user.get("is_admin"):
        # Yeni IP bloğu / cihazdan personel girişi → süper adminlere uyarı (arka planda; girişi yavaşlatmaz).
        try:
            from security.events import note_staff_login, fire_and_forget
            fire_and_forget(note_staff_login(user, ip, ua, method="password"))
        except Exception:
            pass

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
        existing = await db.users.find_one(_contact.phone_lookup("phone", _last10), {"_id": 0, "id": 1})
    if existing:
        return {"existing_account": True,
                "message": "Bu e-posta ile zaten bir hesabınız var. Lütfen giriş yapın; siparişiniz hesabınızda görünecektir."}
    import secrets as _secrets
    now = datetime.now(timezone.utc)
    try:
        # AUTH10: sipariş başına 24 saatte en çok 5 kod (yeniden istemek bütçeyi sıfırlamaz);
        # sınır deposu hatasında FAIL-CLOSED — kod üretilmez/gönderilmez.
        _sent24 = await db.guest_convert_codes.count_documents(
            {"order_id": order["id"], "created_at": {"$gte": (now - timedelta(hours=24)).isoformat()}})
        if _sent24 >= 5:
            raise HTTPException(status_code=429,
                                detail="Bu sipariş için çok fazla kod istendi. Lütfen daha sonra tekrar deneyin.")
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
    except HTTPException:
        raise
    except Exception as _se:
        logger.error("guest-convert: sınır deposu kullanılamıyor — kod gönderilmedi (%s)", type(_se).__name__)
        raise HTTPException(status_code=503, detail="Şu an doğrulama kodu gönderilemiyor. Lütfen birkaç dakika sonra tekrar deneyin.")
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
        logger.warning(f"guest-convert kod maili başarısız: {type(_e).__name__}")  # AUTH23: istisna metni loglanmaz
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
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Şifre en az 8 karakter olmalı")

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
            # KVKK: adres defteri şifreli → tekilleştirme address_hash ile (+ backfill öncesi düz kayıt).
            dup = await db.addresses.find_one(
                {"user_id": uid, "$or": [
                    {"address_hash": {"$in": _contact.address_hashes(a.get("address"), a.get("city"),
                                                                     a.get("district")) or ["\x00"]}},
                    {"address": a.get("address", ""), "city": a.get("city", ""),
                     "district": a.get("district", "")}]},
                {"_id": 0, "id": 1})
            if dup:
                return
            has_any = await db.addresses.find_one({"user_id": uid}, {"_id": 0, "id": 1})
            await db.addresses.insert_one(_contact.seal_doc({
                "id": generate_id(), "user_id": uid,
                "title": a.get("title") or "Teslimat Adresi",
                "first_name": a.get("first_name", ""), "last_name": a.get("last_name", ""),
                "phone": a.get("phone", ""), "address": a.get("address", ""),
                "city": a.get("city", ""), "district": a.get("district", ""),
                "postal_code": a.get("postal_code", ""),
                "is_default": not bool(has_any),
                "is_corporate": False, "company_name": "", "tax_no": "", "tax_office": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }, "address"))
        except Exception as _e:
            logger.warning(f"convert-guest adres kaydi atlandi: {_e}")

    existing = await db.users.find_one({"email": email}, {"_id": 0, "id": 1})
    if not existing and len(_last10) == 10 and _last10.isdigit():
        # Ayni cep no baska bir hesaba aitse mukerrer uyelik acma — o hesaba bagla (hash eşitliği)
        existing = await db.users.find_one(_contact.phone_lookup("phone", _last10), {"_id": 0, "id": 1})

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
    _cbase = {"order_id": order["id"], "used": False, "expires_at": {"$gt": _now_ts}}
    _cpeek = await db.guest_convert_codes.find_one(_cbase, {"_id": 1, "attempts": 1},
                                                   sort=[("created_at", -1)])
    if not _cpeek:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş. Lütfen yeni kod isteyin.")
    # GÜVENLİK (denetim 2026-10, #6): deneme hakkı ATOMİK/koşullu alınır, kod TEK KEZ tüketilir.
    _crec = await db.guest_convert_codes.find_one_and_update(
        {**_cbase, "_id": _cpeek["_id"], "attempts": {"$lt": 5}},
        {"$inc": {"attempts": 1}})
    if not _crec:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen yeni kod isteyin.")
    import hmac as _hmac
    if not _hmac.compare_digest(_hash_otp(code), str(_crec.get("code_hash") or "")):
        raise HTTPException(status_code=400, detail="Kod hatalı")
    # Kodun ait olduğu e-posta ile siparişin e-postası aynı olmalı (defans).
    if (_crec.get("email") or "").lower().strip() != email:
        raise HTTPException(status_code=400, detail="Kod bu siparişe ait değil")
    _cres = await db.guest_convert_codes.update_one({"_id": _crec["_id"], "used": False},
                                                    {"$set": {"used": True}})
    if getattr(_cres, "modified_count", 0) != 1:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş. Lütfen yeni kod isteyin.")

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
    await db.users.insert_one(_contact.seal_doc(dict(user), "user"))  # KVKK: telefon şifreli
    await db.orders.update_one({"id": order["id"]}, {"$set": {"user_id": user["id"]}})
    await _save_guest_address(user["id"], order)

    from .deps import create_token as _ct
    token = _ct(user["id"], is_admin=False)
    user.pop("_id", None)
    user.pop("password", None)
    return {"token": token, "user": user, "existing_account": False, "message": "Hesabınız oluşturuldu ve sipariş bağlandı"}


@router.get("/me")
async def get_me(request: Request, current_user: dict = Depends(get_current_user)):
    """Get current user info"""
    if not current_user:
        # ADM-15: iptal edilmiş oturumda (pasif hesap / logout-all / epoch) `X-Auth-Code:
        # session_revoked` — uygulama açılışındaki /auth/me kontrolü cihaz önbelleğini sildirir.
        from .deps import unauthorized_401
        _hdr = (request.headers.get("authorization") or "")
        _raw = _hdr.split(" ", 1)[1].strip() if _hdr.lower().startswith("bearer ") else ""
        raise await unauthorized_401(_raw, "Giriş yapmanız gerekiyor")
    # ID3-01: eski şifre hash'leri / MFA sırları / sayaçlar yanıtta taşınmaz.
    from .deps import public_self_user
    return public_self_user(current_user)


@router.post("/staff/refresh")
@(limiter.limit("30/minute") if limiter else (lambda f: f))
async def staff_refresh(request: Request, current_user: dict = Depends(require_admin)):
    """Aktif personel oturumunu sessizce yeniler (panel, kullanıcı ETKİNKEN birkaç dakikada bir çağırır).
    Böylece panelde çalışan personel 24 saatlik token süresi ya da sunucu boşta-kalma sayacı yüzünden
    çıkarılmaz. Yeni token AYNI kimlik kanıtını taşır (auth_time + amr korunur → "yakın zamanda doğrulandı"
    gerektiren işlemler yine yeniden doğrulama ister). Güvenlik: ilk girişten itibaren en çok
    `panel.max_session_days` (varsayılan 7) gün yenilenir; sonra yeniden giriş istenir. Oturum iptali
    (token_version / küresel epoch / pasif hesap) require_admin'de zaten uygulanır."""
    from .deps import _decode_jwt_strict, now_ts
    _hdr = request.headers.get("authorization") or ""
    _raw = _hdr.split(" ", 1)[1].strip() if _hdr.lower().startswith("bearer ") else ""
    try:
        payload = _decode_jwt_strict(_raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Geçersiz token")
    try:
        from business_rules import get_rule as _gr
        max_days = float(await _gr(db, "panel.max_session_days", 7) or 7)
    except Exception:
        max_days = 7.0
    max_days = min(max(max_days, 1.0), 30.0)
    first = payload.get("auth_time") or payload.get("iat") or 0
    try:
        first = int(first)
    except Exception:
        first = 0
    if not first or (now_ts() - first) > max_days * 86400:
        raise HTTPException(status_code=401, detail="Güvenlik için oturum süresi doldu; lütfen yeniden giriş yapın.")
    token = create_token(current_user["id"], True, token_version=current_user.get("token_version", 0),
                         auth_time=first, amr=payload.get("amr") or None)
    return {"token": token}


@router.post("/logout-all")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def logout_all(request: Request, current_user: dict = Depends(get_current_user)):
    """Tüm cihazlardaki oturumları kapatır (token_version+1); bu cihaz için TAZE token döner."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor")
    from pymongo import ReturnDocument
    from .deps import token_auth_time
    doc = await db.users.find_one_and_update(
        {"id": current_user["id"]}, {"$inc": {"token_version": 1}},
        projection={"_id": 0, "token_version": 1, "is_admin": 1},
        return_document=ReturnDocument.AFTER) or {}
    _hdr = (request.headers.get("authorization") or "")
    _raw = _hdr.split(" ", 1)[1].strip() if _hdr.lower().startswith("bearer ") else ""
    _at = token_auth_time(_raw) or None
    await write_audit_log("logout_all", user_id=current_user["id"], email=current_user.get("email"),
                          ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
                          success=True)
    fresh = create_token(current_user["id"], bool(doc.get("is_admin", current_user.get("is_admin"))),
                         token_version=int(doc.get("token_version") or 0), auth_time=_at)
    return {"success": True, "token": fresh,
            "message": "Diğer tüm cihazlardaki oturumlar kapatıldı."}


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
    elif len(new) < 8:
        raise HTTPException(status_code=400, detail="Yeni şifre en az 8 karakter olmalı")
    # ID3-05: mevcut şifre denemesi giriş kilidiyle AYNI sayaçlara tabidir (IP+hesap 5 / hesap-geneli 30).
    # Eskiden sınırsızdı → çalınmış bir oturum token'ıyla şifre çevrimiçi kaba kuvvetle bulunup
    # şifreyle yeniden-doğrulama (MFA değişimi, hesap silme) kapıları aşılabiliyordu.
    _cp_ip = client_ip_from_request(request)
    _cp_email = (current_user.get("email") or "").lower()
    _locked, _retry = await is_account_locked(_cp_email, _cp_ip)
    if _locked:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen daha sonra tekrar deneyin.")
    user = await db.users.find_one({"id": current_user["id"]})
    if not user or not verify_password(cur, user.get("password", "")):
        await register_failed_login(_cp_email, _cp_ip)
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
    # ID3-04: bekleyen şifre-sıfırlama link/token'ları şifre değişince ölür.
    await invalidate_password_reset_tokens(current_user["id"])
    await write_audit_log(
        "password_change", user_id=current_user["id"], email=current_user.get("email"),
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
        success=True,
    )
    # Mevcut oturum düşmesin diye TAZE token dön (yeni tv ile). Diğer cihazlar çıkış yapar.
    fresh = create_token(current_user["id"], user.get("is_admin", False), token_version=new_tv,
                         auth_time=now_ts())
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



_OTP_USER_MAX_FAILS = 10


from security import contact_pii as _contact  # noqa: E402  (KVKK: telefon hash araması / mühürleme)


async def _otp_failed_attempts(user_id: str) -> int:
    """Hesap başına son 24 saatteki toplam OTP doğrulama denemesi (kayıt başına 5 sınırını,
    farklı numara biçimleriyle yeni kayıt açarak aşmayı engeller)."""
    since = datetime.now(timezone.utc).timestamp() - 86400
    n = 0
    async for r in db.password_reset_otps.find({"user_id": user_id, "expires_at": {"$gt": since}},
                                               {"_id": 0, "attempts": 1, "used": 1, "reset_token": 1,
                                                "reset_token_hash": 1}):
        n += int(r.get("attempts") or 0) - (1 if (r.get("reset_token") or r.get("reset_token_hash")) else 0)
    return n


def _hash_reset_token(token: str) -> str:
    """Şifre sıfırlama token'ı DB'de yalnız SHA-256 özetiyle saklanır (denetim 2026-10, #8)."""
    return hashlib.sha256(("pwreset:" + str(token or "")).encode("utf-8")).hexdigest()

# AUTH10/AUTH19 (BL-005): müşteri SMS OTP'si için 24 saatlik TOPLAM gönderim bütçesi — numara başına
# ve hesap başına. Yeniden kod istemek bütçeyi SIFIRLAMAZ (her istek ayrı kayıttır ve 24 sa sayılır).
# Sınır deposu (MongoDB) okunamazsa FAIL-CLOSED: SMS gönderilmez, 503 döner.
_OTP_SENDS_PER_NUMBER_24H = 5
_OTP_SENDS_PER_ACCOUNT_24H = 5
_OTP_GENERIC_MSG = "Eğer numara sistemimizde kayıtlıysa SMS kodu gönderildi."
_OTP_STORE_DOWN_MSG = "Şu an doğrulama kodu gönderilemiyor. Lütfen birkaç dakika sonra tekrar deneyin."


async def _otp_send_budget_left(phone_norm: str, user_id) -> bool:
    """Son 24 saatte bu numaraya / bu hesaba gönderilen kod sayısı tavanın altında mı?
    Depo hatasında istisna fırlatır (çağıran fail-closed davranır)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    n_phone = await db.password_reset_otps.count_documents(
        {"phone": phone_norm, "created_at": {"$gte": since}})
    if n_phone >= _OTP_SENDS_PER_NUMBER_24H:
        return False
    if user_id:
        n_acc = await db.password_reset_otps.count_documents(
            {"user_id": user_id, "channel": {"$ne": "email"}, "created_at": {"$gte": since}})
        if n_acc >= _OTP_SENDS_PER_ACCOUNT_24H:
            return False
    return True


async def _send_reset_sms_bg(phone_norm: str, code: str, first_name: str) -> None:
    """Arka planda SMS (yanıt süresi kayıtlı/kayıtsız numara için aynı kalsın — AUTH14)."""
    try:
        from notification_service import send_notification
        await send_notification(
            db, "password_reset_otp", to_phone=phone_norm,
            variables={"otp_code": code, "customer_name": first_name or ""},
            channels=["sms"],
        )
    except Exception as e:
        logger.warning("OTP sms failed (%s)", type(e).__name__)


def _background(coro) -> None:
    try:
        from security.events import fire_and_forget
        fire_and_forget(coro)
    except Exception:
        try:
            coro.close()
        except Exception:
            pass


@router.post("/forgot-password/request-otp")
@(limiter.limit("3/minute") if limiter else (lambda f: f))
async def forgot_password_request_otp(request: Request, req: OTPRequestReq):
    """Telefon numarasına 6 haneli SMS OTP gönderir.
    Privacy (AUTH14): numara sistemde olsa da olmasa da AYNI yanıt ve aynı iş (kayıt + bütçe sayımı);
    SMS arka planda gönderilir → yanıt süresi sağlayıcı gecikmesinden bağımsız.
    Limitler: aynı numaraya 60 sn'de bir; numara ve hesap başına 24 saatte en çok 5 kod; hesap başına
    24 saatte 10 hatalı deneme. Sınır deposu hatasında SMS GÖNDERİLMEZ (503).
    """
    from notification_service import normalize_phone_tr

    phone_norm = normalize_phone_tr(req.phone)
    generic = {"success": True, "message": _OTP_GENERIC_MSG}
    now = datetime.now(timezone.utc)
    try:
        # Rate limit — aynı numaraya son 60 sn içinde kod atılmışsa sessizce aynı cevabı döndür
        recent = await db.password_reset_otps.find_one({"phone": phone_norm}, sort=[("created_at", -1)])
        if recent:
            try:
                prev = datetime.fromisoformat(recent["created_at"])
                if (now - prev).total_seconds() < 60:
                    return generic
            except Exception:
                pass

        # GÜVENLİK (denetim 2026-09-29, HIGH): yalnız geçerli TR cep (905xxxxxxxxx) ve numaranın TAMAMI
        # (ayraç toleranslı, ^…$) eşleşir. Profilden OTP'siz değiştirilmiş telefon (phone_verified=False)
        # şifre sıfırlama hedefi OLAMAZ (denetim 2026-10, #4).
        user = None
        if len(phone_norm) == 12 and phone_norm.startswith("905"):
            # KVKK: telefon şifreli → numaranın TAMAMIYLA hash eşitliği (regex yok).
            user = await db.users.find_one({**_contact.phone_lookup("phone", phone_norm),
                                            **_phone_reset_trusted_filter()},
                                           {"_id": 0, "id": 1, "email": 1, "first_name": 1})
            if user and await _otp_failed_attempts(user["id"]) >= _OTP_USER_MAX_FAILS:
                logger.warning("OTP: hesap başına deneme sınırı aşıldı — kod gönderilmedi")
                return generic
        if not await _otp_send_budget_left(phone_norm, (user or {}).get("id")):
            logger.warning("OTP: 24 saatlik numara/hesap gönderim bütçesi doldu — kod gönderilmedi")
            return generic

        # Bu telefon için var olan kullanılmamış kodları iptal et (bütçe sayımı ETKİLENMEZ)
        await db.password_reset_otps.update_many(
            {"phone": phone_norm, "used": False},
            {"$set": {"used": True, "invalidated": True}},
        )
        code = f"{secrets.randbelow(1000000):06d}"  # CSPRNG
        await db.password_reset_otps.insert_one({
            "phone": phone_norm,
            "code_hash": _hash_otp(code),
            "user_id": (user or {}).get("id"),
            "expires_at": now.timestamp() + 300,  # 5 dk
            "used": False,
            "attempts": 0,
            "created_at": now.isoformat(),
        })
    except HTTPException:
        raise
    except Exception as e:
        # FAIL-CLOSED (AUTH19): sınır deposu yoksa kod üretilmez/gönderilmez.
        logger.error("OTP: sınır deposu kullanılamıyor — SMS gönderilmedi (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail=_OTP_STORE_DOWN_MSG)

    if user:
        _background(_send_reset_sms_bg(phone_norm, code, user.get("first_name", "")))
    return generic


@router.post("/forgot-password/verify-otp")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def forgot_password_verify_otp(request: Request, req: OTPVerifyReq):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr

    phone_norm = normalize_phone_tr(req.phone)
    now_ts = datetime.now(timezone.utc).timestamp()
    _base = {"phone": phone_norm, "used": False, "expires_at": {"$gt": now_ts}}

    _peek = await db.password_reset_otps.find_one(_base, {"_id": 1, "user_id": 1, "attempts": 1},
                                                  sort=[("created_at", -1)])
    if not _peek:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş")
    if int(_peek.get("attempts") or 0) >= 5:
        raise HTTPException(status_code=429, detail="Çok fazla deneme yaptınız")
    if _peek.get("user_id") and await _otp_failed_attempts(_peek["user_id"]) >= _OTP_USER_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Çok fazla deneme yaptınız. Lütfen daha sonra tekrar deneyin.")

    # GÜVENLİK (denetim 2026-10, #6): deneme hakkı ATOMİK ve koşullu alınır (eşzamanlı isteklerle
    # 5 deneme sınırı aşılamaz); kod yalnız {used: False} filtresiyle TEK KEZ tüketilir.
    rec = await db.password_reset_otps.find_one_and_update(
        {**_base, "_id": _peek["_id"], "attempts": {"$lt": 5}},
        {"$inc": {"attempts": 1}},
    )
    if not rec:
        raise HTTPException(status_code=429, detail="Çok fazla deneme yaptınız")

    import hmac as _hmac
    if not _hmac.compare_digest(_hash_otp(str(req.code or "").strip()), str(rec.get("code_hash") or "")):
        raise HTTPException(status_code=400, detail="Kod hatalı")

    if not rec.get("user_id"):
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")

    # AUTH09: kod gönderildikten SONRA hesabın telefonu değiştiyse (ya da doğrulanmamış olarak
    # işaretlendiyse) eski numaraya giden kod artık bu hesabın şifresini sıfırlayamaz.
    _owner = await db.users.find_one({"id": rec["user_id"], **_contact.phone_lookup("phone", phone_norm),
                                      **_phone_reset_trusted_filter()}, {"_id": 0, "id": 1})
    if not _owner:
        await db.password_reset_otps.update_one({"_id": rec["_id"], "used": False},
                                                {"$set": {"used": True, "invalidated": "phone_changed"}})
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş")

    reset_token = secrets.token_urlsafe(32)
    _res = await db.password_reset_otps.update_one(
        {"_id": rec["_id"], "used": False},
        {"$set": {"used": True, "reset_token_hash": _hash_reset_token(reset_token),
                  "reset_token_expires": now_ts + 600}},
    )
    if getattr(_res, "modified_count", 0) != 1:
        raise HTTPException(status_code=400, detail="Kod geçersiz veya süresi dolmuş")
    return {"reset_token": reset_token, "expires_in": 600}


@router.post("/forgot-password/reset")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def forgot_password_reset(request: Request, req: OTPResetReq):
    """SMS-OTP ya da e-posta linkiyle alınan reset token'ı ile yeni şifre belirler.
    GÜVENLİK (denetim 2026-10, #8): token DB'de SHA-256 özetiyle aranır (eski düz-metin kayıtlar
    süreleri dolana kadar kabul edilir); token ATOMİK olarak tek kez tüketilir; personel/admin
    hesabında güçlü şifre politikası (validate_strong_password) uygulanır.
    #2: sıfırlama kanalı (sms/email) + zamanı kullanıcıya yazılır → admin girişinde 24 saat
    boyunca AYNI kanal ikinci faktör olarak kullanılamaz."""
    tok = safe_str(req.reset_token, 512).strip()
    new_pw = safe_str(req.new_password, 200)
    if not tok:
        raise HTTPException(status_code=400, detail="Reset token geçersiz veya süresi dolmuş")
    if not new_pw or len(new_pw) < 8:
        raise HTTPException(status_code=400, detail="Şifre en az 8 karakter olmalı")

    now_ts = datetime.now(timezone.utc).timestamp()
    _tok_filter = {"$or": [{"reset_token_hash": _hash_reset_token(tok)}, {"reset_token": tok}],
                   "reset_token_expires": {"$gt": now_ts}}
    rec = await db.password_reset_otps.find_one(_tok_filter)
    if not rec:
        raise HTTPException(status_code=400, detail="Reset token geçersiz veya süresi dolmuş")

    user_id = rec.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")

    _u = await db.users.find_one({"id": user_id}) or {}
    if not _u:
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı")
    # Personel/admin: güçlü şifre (Amazon DPP) + son-10 tekrar yasağı. Min-yaş reset'te UYGULANMAZ.
    # Müşteri: kayıt/şifre değiştirme ile aynı kural (en az 8 karakter).
    if _u.get("is_admin"):
        validate_strong_password(new_pw, identifiers=[
            _u.get("email"), _u.get("first_name"), _u.get("last_name")])
        _enforce_pw_history(_u, new_pw)

    # Token'ı ATOMİK tüket (iki eşzamanlı istekte yalnız biri geçer).
    consumed = await db.password_reset_otps.find_one_and_delete(
        {"_id": rec["_id"], "reset_token_expires": {"$gt": now_ts}})
    if not consumed:
        raise HTTPException(status_code=400, detail="Reset token geçersiz veya süresi dolmuş")

    _channel = "email" if rec.get("channel") == "email" else "sms"
    _pw_now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"password": hash_password(new_pw),
                  "password_updated_at": _pw_now_iso,
                  "password_changed_at": _pw_now_iso,
                  "password_reset_at": _pw_now_iso,
                  "password_reset_channel": _channel,
                  "password_history": _next_pw_history(_u)},
         # Günlük MFA hata sınırı sıfırlanır (meşru sahip kilitte kalmasın); 5 dk penceresi sürer.
         "$unset": {"mfa_day_fail_count": "", "mfa_day_window": ""},
         "$inc": {"token_version": 1}},  # GÜVENLİK: sıfırlama tüm eski oturumları geçersiz kılar
    )
    # ID3-04: aynı hesabın DİĞER bekleyen sıfırlama token'ları/kodları da ölür (tek kullanımlık zincir).
    await invalidate_password_reset_tokens(user_id)
    await write_audit_log(
        "password_reset", user_id=user_id, email=_u.get("email"),
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
        success=True, meta={"channel": _channel, "is_admin": bool(_u.get("is_admin"))},
    )
    return {"success": True, "message": "Şifreniz güncellendi"}


class EmailResetReq(BaseModel):
    email: str


_EMAIL_RESETS_PER_ADDRESS_24H = 5


async def _send_reset_email_bg(to_email: str, subject: str, html: str, masked: str) -> None:
    try:
        from email_smtp import send_smtp_email
        res = await send_smtp_email(db, to_email, subject, html)
        # GÜVENLİK (denetim 2026-10, #10): log'a tam e-posta adresi YAZILMAZ (maskeli).
        if not (res or {}).get("success"):
            logger.warning(f"reset email send failed for {masked}: {str((res or {}).get('response'))[:200]}")
    except Exception as e:
        logger.warning(f"reset email exception for {masked}: {type(e).__name__}")


@router.post("/forgot-password/email")
@(limiter.limit("3/minute") if limiter else (lambda f: f))
async def forgot_password_email(request: Request, req: EmailResetReq):
    """E-posta ile şifre sıfırlama BAĞLANTISI gönderir.
    Privacy (AUTH14): e-posta sistemde olsa da olmasa da AYNI yanıt; kayıtsız adreste de aynı sorgu
    işi yapılır (dummy) ve e-posta arka planda gönderilir → yanıt süresi hesabın varlığını ele vermez.
    AUTH13: bağlantının alan adı YALNIZ SITE_URL ortam değişkeninden gelir — Host / X-Forwarded-Host
    başlıkları hiçbir zaman kullanılmaz. Üretilen token /forgot-password/reset ile tüketilir.
    """
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
    known = bool(user and user.get("id"))
    # Aynı sorgu işi her iki durumda da yapılır (zaman eşitliği). Kayıtsız adres için filtre hiçbir
    # kayda uymaz (sahte kanal) — kayıtsız kişinin adresi DB'ye yazılmaz.
    _ch = "email" if known else "email-unknown-noop"
    recent = await db.password_reset_otps.find_one({"email": email, "channel": _ch}, sort=[("created_at", -1)])
    _since = (now - timedelta(hours=24)).isoformat()
    _n24 = await db.password_reset_otps.count_documents(
        {"email": email, "channel": _ch, "created_at": {"$gte": _since}})
    reset_token = secrets.token_urlsafe(32)
    _tok_hash = _hash_reset_token(reset_token)
    if not known:
        _dom = email.split("@")[-1]
        logger.info(f"Email reset for unknown email (silent): ***@{_dom.replace(chr(10),'').replace(chr(13),'')}")  # PII/CRLF sızıntısı yok
        return generic
    if recent:
        try:
            if (now - datetime.fromisoformat(recent["created_at"])).total_seconds() < 60:
                return generic
        except Exception:
            pass
    if _n24 >= _EMAIL_RESETS_PER_ADDRESS_24H:
        logger.warning(f"Email reset: 24 saatlik gönderim bütçesi doldu ({_mask_email(email)})")
        return generic

    # Önceki e-posta token'larını geçersiz kıl
    await db.password_reset_otps.update_many(
        {"email": email, "channel": "email"},
        {"$set": {"invalidated": True, "reset_token_expires": 0}},
    )
    await db.password_reset_otps.insert_one({
        "email": email,
        "channel": "email",
        "user_id": user.get("id"),
        "used": True,  # OTP doğrulama adımı yok; token doğrudan geçerli
        # GÜVENLİK (denetim 2026-10, #8): token düz metin SAKLANMAZ; yalnız özeti.
        "reset_token_hash": _tok_hash,
        "reset_token_expires": now_ts + 1800,  # 30 dk
        "created_at": now.isoformat(),
    })

    site = (os.environ.get("SITE_URL") or "https://meluni.com.tr").rstrip("/")
    link = f"{site}/sifre-sifirla?token={reset_token}"
    import html as _html_esc
    # GÜVENLİK (denetim 2026-09-29): ad HTML-kaçışlı — aksi halde ada link/HTML koyup Meluni
    # alan adından oltalama maili gönderilebiliyordu.
    name = _html_esc.escape((user.get("first_name") or "").strip())
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
    _background(_send_reset_email_bg(user["email"], f"Şifre Sıfırlama — {_brand_name}", html, _mask_email(email)))
    return generic

@router.post("/google")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def google_signin(request: Request, payload: dict):
    """Standart Google Sign-In — frontend GIS'ten gelen ID token'i dogrular,
    kullaniciyi olusturur/bulur ve uygulama JWT'sini dondurur (kendi Client ID'miz)."""
    credential = safe_str((payload or {}).get("credential", ""), 4096)
    user = await _google_user_from_credential(credential)
    email = user["email"]
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")

    # GÜVENLİK (denetim H1): sosyal girişte admin yetkisi VERİLMEZ — Google yolu parola/MFA/kilit
    # kontrollerinden geçmez. Personel panele e-posta+şifre (+MFA) ile girer.
    token = create_token(user["id"], False, token_version=user.get("token_version", 0), auth_time=now_ts())
    await write_audit_log("google_login", user_id=user["id"], email=email, ip=ip,
                          user_agent=ua, success=True)
    return {
        "success": True,
        "token": token,
        "user": _public_auth_user(user),
    }


@router.post("/google/callback")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def google_redirect_callback(request: Request):
    """GIS tam-sayfa dönüşü: Google credential -> kısa ömürlü, tek kullanımlık kod.

    Uygulama JWT'si URL'ye asla yazılmaz. Google'ın çift-gönderimli CSRF çerezi de
    form alanıyla sabit-zamanlı karşılaştırılır.
    """
    from fastapi.responses import RedirectResponse

    site_url = await _google_storefront_url()
    body = await request.body()
    if len(body) > 16_384:
        return RedirectResponse(f"{site_url}/giris?google_error=invalid_response", status_code=303)
    try:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except Exception:
        form = {}
    credential = safe_str((form.get("credential") or [""])[0], 4096)
    form_csrf = safe_str((form.get("g_csrf_token") or [""])[0], 512)
    cookie_csrf = safe_str(request.cookies.get("g_csrf_token", ""), 512)
    csrf_valid = bool(
        form_csrf and cookie_csrf and hmac.compare_digest(form_csrf, cookie_csrf)
    )
    if not csrf_valid:
        return RedirectResponse(f"{site_url}/giris?google_error=csrf", status_code=303)

    try:
        user = await _google_user_from_credential(credential)
    except HTTPException as exc:
        code = _google_error_code(exc)
        return RedirectResponse(f"{site_url}/giris?google_error={code}", status_code=303)

    raw_code = secrets.token_urlsafe(32)
    now_ts = datetime.now(timezone.utc).timestamp()
    await db.google_login_codes.delete_many({"expires_at": {"$lte": now_ts}})
    await db.google_login_codes.insert_one({
        "code_hash": hashlib.sha256(raw_code.encode("utf-8")).hexdigest(),
        "user_id": user["id"],
        "used": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": now_ts + 120,
    })
    return RedirectResponse(
        f"{site_url}/giris?{urlencode({'google_code': raw_code})}", status_code=303
    )


@router.post("/google/exchange")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def google_exchange(request: Request, payload: dict):
    """Tam-sayfa Google dönüş kodunu atomik olarak yalnız bir kez JWT'ye çevir."""
    raw_code = safe_str((payload or {}).get("code", ""), 256)
    if not raw_code:
        raise HTTPException(status_code=400, detail="Google giris kodu eksik")
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    now_ts = datetime.now(timezone.utc).timestamp()
    code_doc = await db.google_login_codes.find_one_and_update(
        {"code_hash": code_hash, "used": False, "expires_at": {"$gt": now_ts}},
        {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}},
    )
    if not code_doc:
        raise HTTPException(status_code=401, detail="Google giris kodu gecersiz veya suresi dolmus")
    user = await db.users.find_one({"id": code_doc["user_id"]}, {"_id": 0})
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Hesabiniz devre disi")
    if user.get("is_admin") is True or user.get("is_super_admin") is True:
        raise HTTPException(status_code=403, detail=_GOOGLE_ADMIN_MSG)

    # GÜVENLİK (denetim H1): sosyal girişte admin yetkisi VERİLMEZ — Google yolu parola/MFA/kilit
    # kontrollerinden geçmez. Personel panele e-posta+şifre (+MFA) ile girer.
    token = create_token(user["id"], False, token_version=user.get("token_version", 0), auth_time=now_ts())
    await write_audit_log(
        "google_login", user_id=user["id"], email=user["email"],
        ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"), success=True,
    )
    return {"success": True, "token": token, "user": _public_auth_user(user)}


@router.post("/google/session")
async def google_session(request: Request, session_id: str = Query(...)):
    """DEVRE DIŞI (DENETİM auth-3): Bu uç, 3. taraf demo host'unun (demobackend.emergentagent.com)
    döndürdüğü kimliğe GÜVENİP e-posta ile mevcut hesaba otomatik bağlanıyordu → o host ele
    geçirilir/taklit edilirse HESAP ELE GEÇİRME (ATO). Frontend kullanmıyor (gerçek Google login
    ayrı akışta). Kapatıldı."""
    raise HTTPException(status_code=410, detail="Bu giriş yöntemi kullanımdan kaldırıldı.")
    import httpx  # noqa (ulaşılmaz — güvenlik için devre dışı)
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
        user = await secure_social_link(user, "google")
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

    # GÜVENLİK (denetim H1): sosyal girişte admin yetkisi VERİLMEZ — Google yolu parola/MFA/kilit
    # kontrollerinden geçmez. Personel panele e-posta+şifre (+MFA) ile girer.
    token = create_token(user["id"], False, token_version=user.get("token_version", 0), auth_time=now_ts())
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


def _safe_google_return_to(value: str) -> str:
    # GÜVENLİK (açık yönlendirme, 2026-10): tarayıcı "\" karakterini "/" sayar, TAB/CR/LF'yi yutar →
    # "/\evil.com" ya da "/<TAB>/evil.com" fiilen "//evil.com" olur ve vitrin (React Router)
    # pushState'in SecurityError'ında location.assign ile DIŞ siteye gider. Bunlar reddedilir.
    raw = str(value or "")
    if "\\" in raw or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        return "/hesabim"
    value = safe_str(value, 1024)
    return value if value.startswith("/") and not value.startswith("//") else "/hesabim"


def _google_redirect_uri(request: Request) -> str:
    configured = (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip()
    return configured or f"{str(request.base_url).rstrip('/')}/api/auth/google/callback"


async def _google_storefront_url() -> str:
    try:
        from tenant_config import get_tenant_config
        cfg = await get_tenant_config(db)
        site = safe_str((cfg.get("domains") or {}).get("storefront_url", ""), 1000).rstrip("/")
        if site.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            return site
    except Exception:
        pass
    site = (os.environ.get("SITE_URL") or os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")
    if site.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        return site
    raise HTTPException(status_code=503, detail="Magaza adresi yapilandirilmamis")


@router.get("/google/login")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def google_login(request: Request, return_to: str = Query("/hesabim")):
    """Popup kullanamayan webview'lar için state+nonce korumalı tam sayfa OAuth başlangıcı."""
    from fastapi.responses import RedirectResponse

    audiences = await _google_client_ids()
    if not audiences or not GOOGLE_CLIENT_SECRET:
        site = await _google_storefront_url()
        return RedirectResponse(f"{site}/giris?google_error=configuration", status_code=303)

    state = secrets.token_urlsafe(32)
    browser = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    now_ts = datetime.now(timezone.utc).timestamp()
    await db.google_oauth_states.delete_many({"expires_at": {"$lte": now_ts}})
    await db.google_oauth_states.insert_one({
        "state_hash": hashlib.sha256(state.encode()).hexdigest(),
        "browser_hash": hashlib.sha256(browser.encode()).hexdigest(),
        "nonce": nonce,
        "return_to": _safe_google_return_to(return_to),
        "redirect_uri": _google_redirect_uri(request),
        "used": False,
        "expires_at": now_ts + 600,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    params = urlencode({
        "client_id": audiences[0],
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    })
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=303)
    response.set_cookie(
        "google_oauth_browser", browser, max_age=600, httponly=True,
        secure=request.url.scheme == "https", samesite="lax", path="/api/auth/google/callback",
    )
    return response


@router.get("/google/callback")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def google_callback(request: Request, code: str = Query(""), state: str = Query(""), error: str = Query("")):
    """Tam sayfa OAuth callback; uygulama JWT'si URL'ye hiçbir zaman yazılmaz."""
    import httpx
    from fastapi.responses import RedirectResponse

    site = await _google_storefront_url()
    if error:
        return RedirectResponse(f"{site}/giris?google_error=access_denied", status_code=303)
    browser = safe_str(request.cookies.get("google_oauth_browser", ""), 256)
    if not code or not state or not browser:
        return RedirectResponse(f"{site}/giris?google_error=csrf", status_code=303)
    now_ts = datetime.now(timezone.utc).timestamp()
    state_doc = await db.google_oauth_states.find_one_and_update(
        {
            "state_hash": hashlib.sha256(state.encode()).hexdigest(),
            "browser_hash": hashlib.sha256(browser.encode()).hexdigest(),
            "used": False,
            "expires_at": {"$gt": now_ts},
        },
        {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}},
    )
    if not state_doc:
        return RedirectResponse(f"{site}/giris?google_error=csrf", status_code=303)
    audiences = await _google_client_ids()
    if not audiences or not GOOGLE_CLIENT_SECRET:
        return RedirectResponse(f"{site}/giris?google_error=configuration", status_code=303)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": audiences[0],
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": state_doc["redirect_uri"],
            })
        if token_response.status_code != 200:
            raise ValueError("oauth exchange rejected")
        user = await _google_user_from_credential(
            safe_str(token_response.json().get("id_token", ""), 4096),
            expected_nonce=safe_str(state_doc.get("nonce", ""), 512),
        )
    except HTTPException as exc:
        return RedirectResponse(f"{site}/giris?google_error={_google_error_code(exc)}", status_code=303)
    except Exception:
        return RedirectResponse(f"{site}/giris?google_error=verification", status_code=303)

    raw_code = secrets.token_urlsafe(32)
    await db.google_login_codes.insert_one({
        "code_hash": hashlib.sha256(raw_code.encode()).hexdigest(),
        "user_id": user["id"], "used": False,
        "created_at": datetime.now(timezone.utc).isoformat(), "expires_at": now_ts + 120,
    })
    query = urlencode({"google_code": raw_code, "redirect": state_doc.get("return_to", "/hesabim")})
    response = RedirectResponse(f"{site}/giris?{query}", status_code=303)
    response.delete_cookie("google_oauth_browser", path="/api/auth/google/callback")
    return response


# =============================================================================
# PERSONEL DAVETİ KABULÜ (ADM-01)
# Davetli: şifresini belirler + MFA kurar (TOTP ya da yöneticinin verdiği telefona SMS) →
# ancak ondan sonra oturum alır. Token yalnız hash'iyle saklanır; 48 saat; tek kullanımlık.
# =============================================================================
@router.post("/invite/inspect")
@(limiter.limit("20/minute") if limiter else (lambda f: f))
async def invite_inspect(payload: dict, request: Request):
    from security.invites import load_invite, masked
    doc = await load_invite((payload or {}).get("token"))
    return {"valid": True, **masked(doc)}


@router.post("/invite/mfa-start")
@(limiter.limit("6/minute") if limiter else (lambda f: f))
async def invite_mfa_start(payload: dict, request: Request):
    """TOTP: QR + anahtar döner (davet başına tek anahtar; tekrar çağrı aynı QR'ı verir).
    SMS: yöneticinin belirlediği telefona 6 haneli kod gönderir (30 sn bekleme)."""
    import io as _io
    import base64 as _b64
    from security.invites import load_invite
    from security.crypto import encrypt, decrypt
    doc = await load_invite((payload or {}).get("token"))
    if doc.get("mfa_method") == "sms":
        _last = doc.get("last_sms_at")
        try:
            if _last and (datetime.now(timezone.utc) - datetime.fromisoformat(_last)).total_seconds() < 30:
                raise HTTPException(status_code=429, detail="Çok sık deneme. Birkaç saniye sonra tekrar deneyin.")
        except HTTPException:
            raise
        except Exception:
            pass
        from .mfa import send_mfa_sms_code, _mask_phone
        phone = decrypt(doc.get("phone_enc")) if doc.get("phone_enc") else ""
        if not phone:
            raise HTTPException(status_code=400, detail="Davette telefon tanımlı değil")
        await db.staff_invites.update_one({"id": doc["id"]},
                                          {"$set": {"last_sms_at": datetime.now(timezone.utc).isoformat()}})
        sent = await send_mfa_sms_code({"id": doc["user_id"], "mfa_phone_enc": doc["phone_enc"],
                                        "first_name": doc.get("first_name", ""), "_staff_invite": True})
        return {"method": "sms", "sent": bool(sent), "phone_masked": _mask_phone(phone)}
    import pyotp
    import qrcode
    secret = decrypt(doc.get("totp_secret_enc")) if doc.get("totp_secret_enc") else None
    if not secret:
        secret = pyotp.random_base32()
        await db.staff_invites.update_one({"id": doc["id"], "totp_secret_enc": {"$exists": False}},
                                          {"$set": {"totp_secret_enc": encrypt(secret)}})
        doc2 = await db.staff_invites.find_one({"id": doc["id"]}, {"_id": 0, "totp_secret_enc": 1}) or {}
        secret = decrypt(doc2.get("totp_secret_enc")) or secret
    from .mfa import ISSUER_NAME
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=doc.get("email", "user"), issuer_name=ISSUER_NAME)
    img = qrcode.make(uri)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return {"method": "totp", "otpauth_uri": uri, "secret": secret,
            "qr_code": "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()}


@router.post("/invite/accept")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def invite_accept(payload: dict, request: Request):
    """Şifre + MFA kodu doğrulanınca personel hesabı oluşturulur ve oturum verilir.
    10 hatalı MFA kodu daveti kilitler (yönetici yeniden göndermeli)."""
    from security.invites import load_invite, register_failed_attempt
    from security.crypto import decrypt
    from .mfa import _verify_totp, _consume_code, new_recovery_codes
    token = (payload or {}).get("token")
    password = safe_str((payload or {}).get("password"), 200)
    code = str((payload or {}).get("code") or "").strip().replace(" ", "")
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")
    doc = await load_invite(token)
    if not password or not code:
        raise HTTPException(status_code=400, detail="Şifre ve doğrulama kodu zorunlu")
    validate_strong_password(password, identifiers=[doc.get("email"), doc.get("first_name"), doc.get("last_name")])
    role = await db.roles.find_one({"id": doc.get("role_id")}, {"_id": 0, "id": 1, "name": 1})
    if not role:
        raise HTTPException(status_code=409, detail="Davetteki rol artık mevcut değil; yöneticinizden yeni davet isteyin")
    if await db.users.find_one({"email": doc["email"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Bu e-posta ile zaten bir hesap var")
    # MFA kodu
    ok = False
    if doc.get("mfa_method") == "sms":
        _inv_pn = decrypt(doc.get("phone_enc")) if doc.get("phone_enc") else ""
        ok = (await _consume_code(doc["user_id"], code, ("sms",), targets={"sms": _inv_pn})) is not None
    else:
        _sec = decrypt(doc.get("totp_secret_enc")) if doc.get("totp_secret_enc") else None
        ok = _verify_totp(_sec, code)
    if not ok:
        await register_failed_attempt(doc)
        await write_audit_log("staff_invite_accept", email=doc.get("email"), ip=ip, user_agent=ua,
                              success=False, meta={"reason": "bad_mfa_code", "invite_id": doc["id"]})
        raise HTTPException(status_code=400, detail="Doğrulama kodu hatalı")
    # Daveti ATOMİK tüket (çift kabul / yarış yok).
    claimed = await db.staff_invites.find_one_and_update(
        {"id": doc["id"], "status": "pending"},
        {"$set": {"status": "accepting", "accepting_at": datetime.now(timezone.utc).isoformat()}},
        projection={"_id": 0, "id": 1})
    if not claimed:
        raise HTTPException(status_code=410, detail="Bu davet artık geçerli değil")
    now_iso = datetime.now(timezone.utc).isoformat()
    rc_plain, rc_recs = new_recovery_codes()
    user_doc = {
        "id": doc["user_id"],
        "email": doc["email"],
        "password": hash_password(password),
        "password_changed_at": now_iso,
        "first_name": doc.get("first_name", ""),
        "last_name": doc.get("last_name", ""),
        "role_id": doc.get("role_id"),
        "is_admin": True,
        "is_active": True,
        "email_verified": True,
        "token_version": 0,
        "created_at": now_iso,
        "created_by": (doc.get("created_by") or {}).get("email") or "invite",
        "invited_via": doc["id"],
        "mfa_enabled": True,
        "mfa_enabled_at": now_iso,
        "mfa_recovery_codes": rc_recs,
        "mfa_recovery_generated_at": now_iso,
    }
    if doc.get("mfa_method") == "sms":
        _pn = decrypt(doc.get("phone_enc")) or ""
        user_doc.update({"mfa_method": "sms", "mfa_phone_enc": doc.get("phone_enc"), "phone": _pn,
                         "phone_verified": True, "mfa_admin_managed": True})
    else:
        from security.crypto import encrypt as _enc
        user_doc.update({"mfa_method": "totp", "mfa_secret_enc": _enc(decrypt(doc.get("totp_secret_enc")) or "")})
    try:
        await db.users.insert_one(dict(user_doc))
    except Exception as _ie:
        await db.staff_invites.update_one({"id": doc["id"], "status": "accepting"}, {"$set": {"status": "pending"}})
        logger.error(f"[invite] kullanıcı oluşturulamadı: {type(_ie).__name__}")
        raise HTTPException(status_code=409, detail="Hesap oluşturulamadı (e-posta kullanımda olabilir)")
    await db.staff_invites.update_one({"id": doc["id"]},
                                      {"$set": {"status": "accepted", "accepted_at": now_iso},
                                       "$unset": {"totp_secret_enc": "", "token_hash": ""}})
    try:
        from activity_audit import record_admin_audit
        await record_admin_audit(db, action="rbac.invite_accept", entity_type="panel_user", entity_id=doc["user_id"],
                                 after={"role_id": doc.get("role_id"), "mfa_method": user_doc.get("mfa_method")},
                                 current_user=user_doc, request=request, source="auth.invite",
                                 metadata={"invite_id": doc["id"],
                                           "invited_by": (doc.get("created_by") or {}).get("id")})
    except Exception:
        pass
    await write_audit_log("staff_invite_accept", user_id=doc["user_id"], email=doc["email"], ip=ip,
                          user_agent=ua, success=True, meta={"invite_id": doc["id"]})
    await write_audit_log("login", user_id=doc["user_id"], email=doc["email"], ip=ip, user_agent=ua,
                          success=True, meta={"via": "invite"})
    try:
        from security.events import security_event, note_staff_login, fire_and_forget
        fire_and_forget(security_event("staff_invite_accepted", user_doc,
                                       {"target": doc["user_id"], "staff": doc["email"],
                                        "role": role.get("name") or doc.get("role_id"), "fp": doc["id"]},
                                       request=request, audit=False))
        fire_and_forget(note_staff_login(user_doc, ip, ua, method="invite"))
    except Exception:
        pass
    token_out = create_token(doc["user_id"], True, token_version=0, auth_time=now_ts())
    return {
        "token": token_out,
        "recovery_codes": rc_plain,
        "user": {"id": doc["user_id"], "email": doc["email"], "first_name": user_doc["first_name"],
                 "last_name": user_doc["last_name"], "phone": user_doc.get("phone", ""),
                 "is_admin": True, "created_at": now_iso},
    }
