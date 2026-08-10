"""
TOTP tabanlı MFA (çok faktörlü kimlik doğrulama) — Amazon DPP uyumu.

Google Authenticator / Authy uyumlu. Opsiyoneldir: mfa_enabled=False kullanıcılar
normal login yapar (mevcut akış bozulmaz). mfa_secret AES vault ile şifreli saklanır.

Login akışı:
  POST /api/auth/login -> mfa_enabled ise {mfa_required: true, mfa_token} döner (tam JWT vermez)
  POST /api/auth/mfa/verify {mfa_token, code} -> kod doğruysa tam JWT döner
"""
import io
import base64
from datetime import datetime, timezone, timedelta

import jwt
import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException

from .deps import db, require_auth, create_token, JWT_SECRET, JWT_ALGORITHM, JWT_ISSUER
from security.crypto import encrypt, decrypt

router = APIRouter(prefix="/auth/mfa", tags=["MFA"])

ISSUER_NAME = "Facette Admin"
MFA_TOKEN_PURPOSE = "mfa_pending"


def create_mfa_pending_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "purpose": MFA_TOKEN_PURPOSE,
        "iat": now,
        "iss": JWT_ISSUER,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_mfa_token(token: str) -> dict:
    payload = jwt.decode(
        token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "user_id"]}, issuer=JWT_ISSUER,
    )
    if payload.get("purpose") != MFA_TOKEN_PURPOSE:
        raise HTTPException(status_code=400, detail="Geçersiz MFA token")
    return payload


def _verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip().replace(" ", ""), valid_window=1)


# ─────────────────────── SMS OTP MFA (ikinci yöntem) ───────────────────────
import hashlib as _hl
import os as _os
import secrets as _secrets


def _hash_code(code: str) -> str:
    return _hl.sha256(f"mfa:{str(code).strip()}".encode()).hexdigest()


def _mask_phone(p: str) -> str:
    d = "".join(c for c in str(p or "") if c.isdigit())
    return ("***" + d[-2:]) if len(d) >= 4 else "***"


async def admin_mfa_enforced() -> bool:
    """Admin MFA zorunlu mu? Öncelik: env break-glass (ADMIN_MFA_ENFORCE=off → kapat,
    on → aç) → İşletme Kuralı security.admin_mfa_required. Kilitlenme durumunda Railway
    env'den ADMIN_MFA_ENFORCE=off ile ANINDA kapatılabilir (panel gerekmez)."""
    env = (_os.environ.get("ADMIN_MFA_ENFORCE") or "").strip().lower()
    if env in ("0", "off", "false", "no", "disable"):
        return False
    if env in ("1", "on", "true", "yes", "enable"):
        return True
    try:
        from business_rules import get_rule as _gr
        return bool(await _gr(db, "security.admin_mfa_required", False))
    except Exception:
        return False


def _resolve_mfa_phone(user: dict) -> str:
    """MFA SMS'inin gideceği telefon. Öncelik: kurulmuş mfa_phone_enc (şifreli),
    yoksa HESABIN kayıtlı telefonu (user.phone). Böylece admin ayrıca telefon
    KURMAK/GİRMEK zorunda kalmaz — her e-posta kendi kayıtlı numarasıyla doğrular."""
    if user.get("mfa_phone_enc"):
        try:
            p = decrypt(user["mfa_phone_enc"])
            if p:
                return p
        except Exception:
            pass
    return (user.get("phone") or "").strip()


async def send_mfa_sms_code(user: dict) -> bool:
    """Kullanıcının MFA telefonuna 6 haneli kod gönderir (5 dk geçerli, hash'li saklanır).
    Telefon: mfa_phone_enc → yoksa hesabın user.phone'u. Başarısızsa False."""
    phone = _resolve_mfa_phone(user)
    if not phone:
        return False
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr, send_notification
    pn = normalize_phone_tr(phone)
    code = f"{_secrets.randbelow(1000000):06d}"
    now = datetime.now(timezone.utc)
    await db.mfa_sms_codes.update_many({"user_id": user["id"], "used": False},
                                       {"$set": {"used": True}})
    await db.mfa_sms_codes.insert_one({
        "user_id": user["id"], "code_hash": _hash_code(code),
        "expires_at": now.timestamp() + 300, "used": False, "attempts": 0,
        "created_at": now.isoformat(),
    })
    try:
        await send_notification(db, "password_reset_otp", to_phone=pn,
                                variables={"otp_code": code, "customer_name": user.get("first_name", "")},
                                channels=["sms"])
        return True
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"MFA SMS gönderilemedi user={user.get('id')}: {e}")
        return False


async def _verify_sms_code(user_id: str, code: str) -> bool:
    now_ts = datetime.now(timezone.utc).timestamp()
    rec = await db.mfa_sms_codes.find_one(
        {"user_id": user_id, "used": False, "expires_at": {"$gt": now_ts}},
        sort=[("created_at", -1)])
    if not rec:
        return False
    if int(rec.get("attempts") or 0) >= 10:
        return False
    ok = _hmac_eq(_hash_code(code), str(rec.get("code_hash") or ""))
    if ok:
        await db.mfa_sms_codes.update_one({"_id": rec["_id"]}, {"$set": {"used": True}})
    else:
        await db.mfa_sms_codes.update_one({"_id": rec["_id"]}, {"$inc": {"attempts": 1}})
    return ok


def _hmac_eq(a: str, b: str) -> bool:
    import hmac as _hm
    return _hm.compare_digest(str(a), str(b))


@router.post("/setup-sms")
async def mfa_setup_sms(payload: dict, current_user: dict = Depends(require_auth)):
    """SMS MFA kurulumu: telefon kaydeder (şifreli) + doğrulama kodu gönderir (henüz aktif değil)."""
    _me = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_admin_managed": 1})
    if _me and _me.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama telefonunuz yönetici tarafından belirlenir ve değiştirilemez. Değişiklik için yöneticinize başvurun.")
    phone = (payload or {}).get("phone", "")
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr
    pn = normalize_phone_tr(phone)
    if not pn or len(pn) < 10:
        raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası girin")
    await db.users.update_one({"id": current_user["id"]},
                              {"$set": {"mfa_pending_phone_enc": encrypt(pn)}})
    _u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    _u["mfa_phone_enc"] = _u.get("mfa_pending_phone_enc")
    sent = await send_mfa_sms_code(_u)
    return {"success": True, "sent": sent, "phone_masked": _mask_phone(pn)}


@router.post("/enable-sms")
async def mfa_enable_sms(payload: dict, current_user: dict = Depends(require_auth)):
    """Gönderilen SMS kodu doğrulanırsa SMS MFA aktifleşir."""
    code = (payload or {}).get("code")
    _me = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_admin_managed": 1})
    if _me and _me.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama telefonunuz yönetici tarafından belirlenir ve değiştirilemez.")
    u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_pending_phone_enc": 1})
    if not (u and u.get("mfa_pending_phone_enc")):
        raise HTTPException(status_code=400, detail="Önce SMS kurulumunu başlatın")
    if not await _verify_sms_code(current_user["id"], code):
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    await db.users.update_one({"id": current_user["id"]},
                              {"$set": {"mfa_enabled": True, "mfa_method": "sms",
                                        "mfa_phone_enc": u["mfa_pending_phone_enc"],
                                        "mfa_enabled_at": datetime.now(timezone.utc).isoformat()},
                               "$unset": {"mfa_pending_phone_enc": ""}})
    return {"success": True, "mfa_enabled": True, "mfa_method": "sms"}


@router.post("/send")
async def mfa_send_login_code(payload: dict):
    """Login 2. adımında SMS kodunu (yeniden) gönderir. mfa_token ile kimlik doğrular."""
    mfa_token = (payload or {}).get("mfa_token")
    if not mfa_token:
        raise HTTPException(status_code=400, detail="mfa_token zorunlu")
    try:
        decoded = _decode_mfa_token(mfa_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="MFA süresi doldu, tekrar giriş yapın")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Geçersiz MFA token")
    user = await db.users.find_one({"id": decoded["user_id"]}, {"_id": 0})
    # SMS yöntemi kurulu VEYA otomatik SMS (kayıtlı telefon + hesap TOTP secret'i yok) ise gönder.
    _phone = _resolve_mfa_phone(user) if user else ""
    if not user or not _phone or (user.get("mfa_method") not in (None, "", "sms") and user.get("mfa_secret_enc")):
        raise HTTPException(status_code=400, detail="SMS MFA aktif değil")
    sent = await send_mfa_sms_code(user)
    return {"success": True, "sent": sent, "phone_masked": _mask_phone(_phone)}


@router.get("/status")
async def mfa_status(current_user: dict = Depends(require_auth)):
    u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_enabled": 1})
    return {"mfa_enabled": bool(u and u.get("mfa_enabled"))}


@router.post("/setup")
async def mfa_setup(current_user: dict = Depends(require_auth)):
    """Yeni TOTP secret üretir (henüz aktif değil), QR + otpauth URI döner."""
    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.get("email", "user"), issuer_name=ISSUER_NAME
    )
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_pending_secret_enc": encrypt(secret)}},
    )
    # QR PNG -> base64 data URI
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return {"otpauth_uri": uri, "qr_code": f"data:image/png;base64,{qr_b64}", "secret": secret}


@router.post("/enable")
async def mfa_enable(payload: dict, current_user: dict = Depends(require_auth)):
    """Pending secret'a karşı kod doğrulanırsa MFA aktifleşir."""
    code = (payload or {}).get("code")
    u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_pending_secret_enc": 1})
    pending = decrypt(u.get("mfa_pending_secret_enc")) if u and u.get("mfa_pending_secret_enc") else None
    if not pending:
        raise HTTPException(status_code=400, detail="Önce MFA kurulumunu başlatın (setup)")
    if not _verify_totp(pending, code):
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_enabled": True, "mfa_secret_enc": encrypt(pending),
                  "mfa_enabled_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"mfa_pending_secret_enc": ""}},
    )
    return {"success": True, "mfa_enabled": True}


@router.post("/disable")
async def mfa_disable(payload: dict, current_user: dict = Depends(require_auth)):
    """Geçerli TOTP kodu ile MFA devre dışı bırakılır."""
    code = (payload or {}).get("code")
    u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_secret_enc": 1, "mfa_enabled": 1})
    if not (u and u.get("mfa_enabled")):
        return {"success": True, "mfa_enabled": False}
    secret = decrypt(u.get("mfa_secret_enc")) if u.get("mfa_secret_enc") else None
    if not _verify_totp(secret, code):
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_enabled": False}, "$unset": {"mfa_secret_enc": "", "mfa_pending_secret_enc": ""}},
    )
    return {"success": True, "mfa_enabled": False}


@router.post("/verify")
async def mfa_verify(payload: dict):
    """Login 2. adımı: mfa_token + TOTP kodu -> tam JWT.
    Brute-force koruması: kullanıcı başına 5 dakikada en fazla 10 hatalı deneme."""
    mfa_token = (payload or {}).get("mfa_token")
    code = (payload or {}).get("code")
    if not mfa_token or not code:
        raise HTTPException(status_code=400, detail="mfa_token ve kod zorunlu")
    try:
        decoded = _decode_mfa_token(mfa_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="MFA süresi doldu, tekrar giriş yapın")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Geçersiz MFA token")

    user = await db.users.find_one({"id": decoded["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=400, detail="MFA aktif değil")
    # OTOMATİK SMS-MFA: hesap resmî olarak MFA kurmamış olsa bile, admin + zorunlu MFA +
    # kayıtlı telefon varsa SMS doğrulaması geçerlidir (kurulum ekranı gerekmez).
    _auto_sms = False
    if not user.get("mfa_enabled"):
        if user.get("is_admin") and await admin_mfa_enforced() and _resolve_mfa_phone(user):
            _auto_sms = True
        else:
            raise HTTPException(status_code=400, detail="MFA aktif değil")

    # Brute-force kilidi
    now = datetime.now(timezone.utc)
    fails = user.get("mfa_fail_count") or 0
    win = user.get("mfa_fail_window")
    try:
        win_dt = datetime.fromisoformat(win) if win else None
    except Exception:
        win_dt = None
    if win_dt and (now - win_dt) < timedelta(minutes=5) and fails >= 10:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme, 5 dakika sonra tekrar deneyin")

    # Yöntem: SMS (kurulu veya otomatik) ise SMS kodunu, TOTP secret'i olan hesapta
    # authenticator kodunu doğrula.
    if _auto_sms or user.get("mfa_method") == "sms" or not user.get("mfa_secret_enc"):
        _code_ok = await _verify_sms_code(user["id"], code)
    else:
        secret = decrypt(user.get("mfa_secret_enc")) if user.get("mfa_secret_enc") else None
        _code_ok = _verify_totp(secret, code)
    if not _code_ok:
        # pencere dışındaysa sıfırla, içindeyse artır
        if win_dt and (now - win_dt) < timedelta(minutes=5):
            await db.users.update_one({"id": user["id"]}, {"$inc": {"mfa_fail_count": 1}})
        else:
            await db.users.update_one({"id": user["id"]}, {"$set": {"mfa_fail_count": 1, "mfa_fail_window": now.isoformat()}})
        raise HTTPException(status_code=401, detail="Kod doğrulanamadı")

    # başarı -> sayaç sıfırla
    _succ_set = {"mfa_fail_count": 0}
    # OTOMATİK SMS-MFA'yı ilk başarılı doğrulamada KALICI kaydet: hesap telefonu
    # mfa_phone_enc'e şifreli yazılır → bundan sonra kurulum/telefon girme sorulmaz.
    if _auto_sms:
        _succ_set["mfa_enabled"] = True
        _succ_set["mfa_method"] = "sms"
        if not user.get("mfa_phone_enc"):
            _pn = _resolve_mfa_phone(user)
            if _pn:
                _succ_set["mfa_phone_enc"] = encrypt(_pn)
        _succ_set["mfa_enabled_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": user["id"]}, {"$set": _succ_set, "$unset": {"mfa_fail_window": ""}})

    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0))
    return {
        "token": token,
        "user": {
            "id": user["id"], "email": user["email"],
            "first_name": user.get("first_name", ""), "last_name": user.get("last_name", ""),
            "phone": user.get("phone", ""), "is_admin": user.get("is_admin", False),
            "created_at": user.get("created_at"),
        },
    }
