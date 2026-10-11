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
from fastapi import APIRouter, Depends, HTTPException, Request

from fastapi.security import HTTPAuthorizationCredentials
from .deps import (db, require_auth, create_token, JWT_SECRET, JWT_ALGORITHM, JWT_ISSUER, limiter,
                   write_audit_log, client_ip_from_request, logger, security, verify_password,
                   safe_str, is_recent_auth, token_auth_time, now_ts)
from security.crypto import encrypt, decrypt

router = APIRouter(prefix="/auth/mfa", tags=["MFA"])

ISSUER_NAME = "Meluni Admin"
MFA_TOKEN_PURPOSE = "mfa_pending"


def create_mfa_pending_token(user_id: str, token_version=None) -> str:
    """tv/ep: girişin 1. adımındaki token_version ve küresel oturum epoch'u — 2. adımda kontrol edilir
    (bkz. _pending_token_revoked)."""
    from .deps import _EPOCH_CACHE
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "purpose": MFA_TOKEN_PURPOSE,
        "ep": int(_EPOCH_CACHE.get("value") or 0),
        "iat": now,
        "iss": JWT_ISSUER,
        "exp": now + timedelta(minutes=5),
    }
    if token_version is not None:
        payload["tv"] = int(token_version or 0)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def _pending_token_revoked(decoded: dict, user: dict) -> bool:
    """GÜVENLİK: 1. adımdan (şifre) SONRA şifre değiştiyse / tüm cihazlardan çıkış yapıldıysa
    (token_version arttı) ya da küresel oturum iptali (security epoch) yapıldıysa, yarıda kalmış MFA
    adımı artık oturum VEREMEZ — eskiden 5 dk'lık mfa_token bu iptallerden etkilenmiyordu."""
    from .deps import get_security_epoch, _epoch_revoked
    if "tv" in (decoded or {}):
        try:
            if int((user or {}).get("token_version") or 0) > int(decoded.get("tv") or 0):
                return True
        except Exception:
            return True
    ep, bumped_at = await get_security_epoch()
    return _epoch_revoked(decoded, ep, bumped_at)


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


def _mask_email(e: str) -> str:
    """a***@alan.com — panelde hangi adrese gideceği görünsün, adres tam açılmasın."""
    e = str(e or "").strip()
    if "@" not in e:
        return ""
    name, _, dom = e.partition("@")
    return (name[:1] + "***@" + dom) if name else ("***@" + dom)


def _resolve_mfa_email(user: dict) -> str:
    """MFA kodunun gideceği e-posta: hesabın kayıtlı adresi."""
    return (user.get("email") or "").strip()


_ENV_OFF = ("0", "off", "false", "no", "disable")
_ENV_ON = ("1", "on", "true", "yes", "enable")


def _env_is_off(name: str) -> bool:
    return (_os.environ.get(name) or "").strip().lower() in _ENV_OFF


def admin_mfa_break_glass() -> bool:
    """ACİL KAPATMA: Railway env ADMIN_MFA_ENFORCE=off → admin MFA zorunluluğu ve admin
    kanal politikası devre dışı (kilitlenmede panel gerekmeden anında açılır)."""
    return _env_is_off("ADMIN_MFA_ENFORCE")


def admin_channel_policy_active() -> bool:
    """Admin ikinci-faktör KANAL politikası (denetim 2026-10, #2) etkin mi?
    Kapatma: env ADMIN_MFA_ENFORCE=off (genel acil kapatma) ya da ADMIN_MFA_CHANNEL_POLICY=off
    (yalnız kanal kısıtını kaldırır; MFA zorunluluğu sürer)."""
    return not (admin_mfa_break_glass() or _env_is_off("ADMIN_MFA_CHANNEL_POLICY"))


async def admin_mfa_enforced() -> bool:
    """Admin MFA zorunlu mu? Öncelik: env break-glass (ADMIN_MFA_ENFORCE=off → kapat,
    on → aç) → İşletme Kuralı security.admin_mfa_required. Kilitlenme durumunda Railway
    env'den ADMIN_MFA_ENFORCE=off ile ANINDA kapatılabilir (panel gerekmez).
    GÜVENLİK (denetim 2026-10, #1): kural okunamazsa FAIL-CLOSED (zorunlu say) — eskiden
    hata halinde False dönüp MFA'sız admin oturumu veriliyordu."""
    env = (_os.environ.get("ADMIN_MFA_ENFORCE") or "").strip().lower()
    if env in _ENV_OFF:
        return False
    if env in _ENV_ON:
        return True
    try:
        from business_rules import get_rule as _gr
        return bool(await _gr(db, "security.admin_mfa_required", True))
    except Exception:
        return True


def _resolve_profile_phone(user: dict) -> str:
    from security.contact_pii import open_field as _open_contact
    return _open_contact(user or {}, "phone")


def _resolve_mfa_phone(user: dict) -> str:
    """MFA SMS'inin gideceği telefon. Öncelik: kurulmuş mfa_phone_enc (şifreli),
    yoksa HESABIN kayıtlı telefonu (user.phone). Böylece admin ayrıca telefon
    KURMAK/GİRMEK zorunda kalmaz — her e-posta kendi kayıtlı numarasıyla doğrular.
    GÜVENLİK (denetim 2026-10, #4): profilden OTP'siz değiştirilmiş (phone_verified=False)
    hesap telefonu MFA için KULLANILMAZ. Alanı hiç olmayan eski hesaplar doğrulanmış sayılır."""
    if user.get("mfa_phone_enc"):
        try:
            p = decrypt(user["mfa_phone_enc"])
            if p:
                return p
        except Exception:
            pass
    if user.get("phone_verified") is False:
        return ""
    # KVKK: müşteri telefonu at-rest şifreli olabilir (phone_enc) → gerekirse çözülür.
    from security.contact_pii import open_field as _open_contact
    return (_open_contact(user, "phone") or "").strip()


# ─────────────── Admin ikinci-faktör KANAL politikası (denetim 2026-10, #2) ───────────────
# Sorun: admin şifresi e-posta linkiyle sıfırlanıp giriş kodu da e-postaya istenebiliyordu (ya da
# SMS ile sıfırlanıp SMS koduyla girilebiliyordu) → tek kanalı (posta kutusu / SIM) ele geçiren
# hem şifreyi hem ikinci faktörü alıyordu. Kural:
#   • Son 24 saatte şifre X kanalıyla sıfırlandıysa giriş ikinci faktörü X OLAMAZ.
#   • TOTP ya da doğrulanmış telefonu olan admin için e-posta kanalı KULLANILMAZ.
#   • Hiçbir zaman sıfır faktör bırakılmaz: tek faktör sıfırlama kanalıyla aynıysa o kanala izin
#     verilir AMA güvenlik günlüğüne yazılır + süper adminlere uyarı e-postası gider.
_RESET_CHANNEL_WINDOW_H = 24
_CODE_CHANNELS = ("sms", "email")


def _parse_dt(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _recent_reset_channel(user: dict) -> str:
    ch = str(user.get("password_reset_channel") or "").strip().lower()
    if ch not in _CODE_CHANNELS:
        return ""
    at = _parse_dt(user.get("password_reset_at"))
    if not at or (datetime.now(timezone.utc) - at) > timedelta(hours=_RESET_CHANNEL_WINDOW_H):
        return ""
    return ch


def _is_totp_account(user: dict) -> bool:
    """Hesabın birincil yöntemi authenticator (TOTP) mu (eski davranışla aynı tanım)."""
    return bool(user.get("mfa_secret_enc")) and user.get("mfa_method") not in (None, "", "sms", "email")


def admin_mfa_plan(user: dict) -> dict:
    """Bu hesap için GİRİŞTE kabul edilecek ikinci faktörler.
    Dönen: {allowed, available, reset_channel, degraded, policy}. policy=False → eski davranış
    (müşteri hesabı ya da acil kapatma)."""
    if _is_totp_account(user):
        avail = ["totp"]  # authenticator hesabı: eskisi gibi YALNIZ TOTP
    else:
        avail = []
        if user.get("mfa_secret_enc"):
            avail.append("totp")
        if _resolve_mfa_phone(user):
            avail.append("sms")
        if _resolve_mfa_email(user):
            avail.append("email")
    if not user.get("is_admin") or not admin_channel_policy_active():
        return {"allowed": avail, "available": avail, "reset_channel": "",
                "degraded": False, "policy": False}
    reset_ch = _recent_reset_channel(user)

    def _strip_email(lst):
        return [c for c in lst if c != "email"] if any(c in ("totp", "sms") for c in lst) else lst

    allowed = _strip_email([c for c in avail if c != reset_ch])
    degraded = False
    if not allowed:
        # Sıfır faktör bırakma: tek faktör sıfırlama kanalıyla aynı → izin ver, ama uyar.
        allowed = _strip_email(list(avail))
        degraded = bool(allowed) and bool(reset_ch)
    return {"allowed": allowed, "available": avail, "reset_channel": reset_ch,
            "degraded": degraded, "policy": True}


def admin_mfa_notice(plan: dict) -> str:
    """Giriş ekranında gösterilecek kısa açıklama (kanal kısıtlandıysa)."""
    if not plan or not plan.get("policy"):
        return ""
    rc = plan.get("reset_channel") or ""
    if rc not in (plan.get("available") or []):
        rc = ""  # sıfırlama kanalı bu hesabın giriş faktörü değil (ör. TOTP hesabı) → kısıt yok
    if plan.get("degraded"):
        return ("Şifreniz son 24 saatte sıfırlandı ve başka doğrulama yönteminiz yok; giriş "
                "güvenlik günlüğüne kaydedildi ve yöneticilere bildirildi.")
    if rc == "email":
        return "Şifreniz son 24 saatte e-posta ile sıfırlandığı için kod e-postaya gönderilemez."
    if rc == "sms":
        return "Şifreniz son 24 saatte SMS ile sıfırlandığı için kod SMS ile gönderilemez."
    if "email" in (plan.get("available") or []) and "email" not in (plan.get("allowed") or []):
        return "Yönetici hesaplarında e-posta ile kod yalnız başka doğrulama yöntemi yoksa kullanılabilir."
    return ""


async def notify_admin_mfa_degraded(user: dict, plan: dict, ip: str = "", ua: str = "") -> None:
    """Tek faktör = sıfırlama kanalı durumunda güvenlik kaydı + süper admin uyarısı (en çok saatte 1)."""
    try:
        await write_audit_log("admin_mfa_degraded", user_id=user.get("id"), email=user.get("email"),
                              ip=ip, user_agent=ua, success=True,
                              meta={"reset_channel": plan.get("reset_channel"),
                                    "allowed": plan.get("allowed")})
    except Exception:
        pass
    try:
        _last = _parse_dt(user.get("mfa_degraded_alert_at"))
        if _last and (datetime.now(timezone.utc) - _last) < timedelta(hours=1):
            return
        await db.users.update_one({"id": user["id"]},
                                  {"$set": {"mfa_degraded_alert_at": datetime.now(timezone.utc).isoformat()}})
        import html as _h
        from email_smtp import send_smtp_email
        _html = (
            '<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#2a2a2a">'
            '<h2 style="font-weight:600">Yönetici girişi: tek doğrulama kanalı</h2>'
            f'<p>Personel hesabı <b>{_h.escape(str(user.get("email") or ""))}</b> son 24 saatte şifresini '
            f'<b>{_h.escape(str(plan.get("reset_channel") or ""))}</b> kanalıyla sıfırladı ve şu an giriş '
            'doğrulama kodu da AYNI kanala gidiyor (başka doğrulama yöntemi tanımlı değil).</p>'
            f'<p>IP: {_h.escape(str(ip or "-"))}</p>'
            '<p>Bu işlemi personel yapmadıysa hesabı Kullanıcılar &amp; Roller ekranından pasifleştirin. '
            'Kalıcı çözüm: hesaba Authenticator (TOTP) ya da yönetici-belirlemeli SMS telefonu tanımlayın.</p></div>'
        )
        sent = 0
        async for sa in db.users.find({"is_super_admin": True, "is_active": {"$ne": False}},
                                      {"_id": 0, "email": 1}).limit(20):
            to = (sa.get("email") or "").strip()
            if to:
                try:
                    await send_smtp_email(db, to, "Güvenlik uyarısı — yönetici girişi", _html)
                    sent += 1
                except Exception:
                    pass
        logger.warning(f"[mfa] admin tek-kanal girişi uyarısı user={user.get('id')} alerted={sent}")
    except Exception as _e:
        logger.warning(f"[mfa] degraded uyarısı gönderilemedi: {type(_e).__name__}")


def _target_hash(channel: str, target: str) -> str:
    """Kodun gönderildiği HEDEFİN (telefon / e-posta) özeti (AUTH09). Telefon normalize edilir."""
    t = str(target or "").strip().lower()
    if channel == "sms":
        d = "".join(c for c in t if c.isdigit())
        t = d[-10:]
    return _hl.sha256(f"mfa-target:{channel}:{t}".encode()).hexdigest()


async def _issue_mfa_code(user: dict, channel: str = "sms", target: str = "") -> str:
    """Yeni 6 haneli MFA kodu üretir, öncekileri iptal eder ve HASH'li saklar.
    Kodun gönderildiği KANAL ve HEDEF (telefon/e-posta özeti) kayda yazılır: admin kanal politikası
    doğrulamada kanalı, AUTH09 hedef bağlaması da kod gönderildikten SONRA numara/e-posta değiştiyse
    eski kodun yeni hedef için geçmemesini denetler."""
    code = f"{_secrets.randbelow(1000000):06d}"
    now = datetime.now(timezone.utc)
    await db.mfa_sms_codes.update_many({"user_id": user["id"], "used": False},
                                       {"$set": {"used": True}})
    rec = {
        "user_id": user["id"], "code_hash": _hash_code(code), "channel": channel,
        "expires_at": now.timestamp() + 300, "used": False, "attempts": 0,
        "created_at": now.isoformat(),
    }
    if target:
        rec["target_hash"] = _target_hash(channel, target)
    await db.mfa_sms_codes.insert_one(rec)
    return code


def _is_staff_ctx(user: dict) -> bool:
    return bool((user or {}).get("is_admin") or (user or {}).get("_staff_invite"))


async def send_mfa_email_code(user: dict) -> bool:
    """MFA kodunu E-POSTA ile gönderir (kullanıcı isteği: isteyen SMS, isteyen e-posta).
    SMS sağlayıcısı teslim edemediğinde personelin panele girebilmesi için ikinci kanal."""
    to_mail = _resolve_mfa_email(user)
    if not to_mail:
        return False
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import send_notification
    code = await _issue_mfa_code(user, channel="email", target=to_mail)
    try:
        # GİRİŞ doğrulaması → kendi event'i (şifre sıfırlama şablonu DEĞİL).
        await send_notification(db, "login_verification_code", to_email=to_mail,
                                variables={"otp_code": code, "customer_name": user.get("first_name", "")},
                                channels=["email"])
        return True
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"MFA e-posta gönderilemedi user={user.get('id')}: {type(e).__name__}")  # AUTH23
        return False


async def send_mfa_sms_code(user: dict) -> bool:
    """Kullanıcının MFA telefonuna 6 haneli kod gönderir (5 dk geçerli, hash'li saklanır).
    Telefon: mfa_phone_enc → yoksa hesabın user.phone'u. Başarısızsa False."""
    phone = _resolve_mfa_phone(user)
    if not phone:
        return False
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr, send_notification
    from notification_service import sms_priority
    pn = normalize_phone_tr(phone)
    code = await _issue_mfa_code(user, channel="sms", target=pn)
    try:
        # BL-006: personel giriş kodu küresel SMS tavanı dolduğunda da küçük bir YEDEK payla gider.
        with sms_priority("admin_mfa" if _is_staff_ctx(user) else "normal"):
            await send_notification(db, "login_verification_code", to_phone=pn,
                                    variables={"otp_code": code, "customer_name": user.get("first_name", "")},
                                    channels=["sms"])
        return True
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"MFA SMS gönderilemedi user={user.get('id')}: {type(e).__name__}")  # AUTH23
        return False


_MFA_CODE_MAX_TRY = 10


async def _consume_code(user_id: str, code: str, allowed_channels=None, targets=None) -> str:
    """Kodu ATOMİK doğrular ve tek kez tüketir (denetim 2026-10, #6). Başarılıysa kodun kanalını
    ("sms"/"email"; eski kayıtta "") döndürür; başarısızsa None.
    • Deneme hakkı find_one_and_update ile koşullu ($lt) artırılır → eşzamanlı isteklerle
      10 deneme sınırı aşılamaz.
    • 'used' yalnız {used: False} filtresiyle set edilir → aynı kod iki kez kullanılamaz.
    • allowed_channels verilirse kod yalnız o kanallardan gönderilmişse kabul edilir.
    • targets ({kanal: güncel hedef}) verilirse, kod kaydındaki hedef özeti GÜNCEL hedefle eşleşmeli
      (AUTH09: kod gönderildikten sonra telefon/e-posta değiştiyse eski kod reddedilir)."""
    if not code:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    rec = await db.mfa_sms_codes.find_one_and_update(
        {"user_id": user_id, "used": False, "expires_at": {"$gt": now_ts},
         "attempts": {"$lt": _MFA_CODE_MAX_TRY}},
        {"$inc": {"attempts": 1}},
        sort=[("created_at", -1)])
    if not rec:
        return None
    ch = str(rec.get("channel") or "")
    if allowed_channels is not None and ch not in allowed_channels:
        return None
    if not _hmac_eq(_hash_code(code), str(rec.get("code_hash") or "")):
        return None
    if targets is not None and rec.get("target_hash"):
        _cur = (targets or {}).get(ch)
        if not _cur or not _hmac_eq(_target_hash(ch, _cur), str(rec.get("target_hash"))):
            # Hedef değişti: kod artık hiçbir hedef için geçerli değil (tek kullanımlık iptal).
            await db.mfa_sms_codes.update_one({"_id": rec["_id"], "used": False},
                                              {"$set": {"used": True, "invalidated": "target_changed"}})
            return None
    res = await db.mfa_sms_codes.update_one(
        {"_id": rec["_id"], "used": False},
        {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}})
    if getattr(res, "modified_count", 0) != 1:
        return None
    return ch


def _code_targets(user: dict) -> dict:
    """Kodun geçerli olabileceği GÜNCEL hedefler (AUTH09)."""
    return {"sms": _resolve_mfa_phone(user or {}), "email": _resolve_mfa_email(user or {})}


async def _verify_sms_code(user_id: str, code: str, allowed_channels=None) -> bool:
    return (await _consume_code(user_id, code, allowed_channels)) is not None


def _hmac_eq(a: str, b: str) -> bool:
    import hmac as _hm
    return _hm.compare_digest(str(a), str(b))


# ─────────────── Faktör bağlama/değiştirme için YENİDEN KİMLİK DOĞRULAMA (denetim 2026-10) ───────────────
# Sorun: /setup, /enable, /setup-sms, /enable-sms, /disable, /channel yalnız oturum token'ıyla
# çalışıyordu → çalınmış bir oturum token'ı ikinci faktörü saldırganın telefonuna/uygulamasına
# bağlayabiliyordu (kalıcı ele geçirme). Artık her biri şunlardan birini ister:
#   • "yakın zamanda giriş" kanıtı: token'daki auth_time son 5 dk içinde (giriş/MFA doğrulama anı), ya da
#   • mevcut şifre (current_password) + MFA zaten açıksa geçerli MEVCUT faktör kodu (current_code;
#     authenticator hesabında). Kod-tabanlı (SMS/e-posta) MFA hesabında şifre yolu yetmez →
#     yeniden giriş (yakın-zaman kanıtı) gerekir.
# /enable ve /enable-sms ek olarak, yeniden-doğrulamayla başlatılmış bir kurulumu (≤30 dk) kabul eder.
# Faktör değişince token_version artırılır (DİĞER tüm oturumlar düşer), çağırana TAZE token döner ve
# hesabın e-postasına bildirim gider.
_REAUTH_NEEDED_MSG = ("Güvenlik için mevcut şifrenizi girin (ya da çıkış yapıp yeniden giriş yaptıktan "
                      "sonraki 5 dakika içinde tekrar deneyin).")
_REAUTH_RELOGIN_MSG = ("Doğrulama yöntemini değiştirmek için çıkış yapıp yeniden giriş yapın ve "
                       "5 dakika içinde tekrar deneyin.")
_PENDING_SETUP_MAX_MIN = 30


async def _require_reauth(credentials, current_user: dict, payload: dict, request: Request,
                          *, need_factor: bool = True, action: str = "") -> dict:
    """Yeniden kimlik doğrulama. Başarılıysa {"method", "auth_time"} döner; değilse 403."""
    if is_recent_auth(credentials):
        return {"method": "recent", "auth_time": token_auth_time(credentials)}
    payload = payload or {}
    pw = safe_str(payload.get("current_password"), 200)
    if not pw:
        raise HTTPException(status_code=403, detail=_REAUTH_NEEDED_MSG)
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "password": 1, "mfa_enabled": 1, "mfa_secret_enc": 1,
                                 "mfa_method": 1}) or {}
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")
    # ID3-05: şifreyle yeniden doğrulama da giriş kilidi sayaçlarına tabidir (çalınmış oturumla
    # şifreyi çevrimiçi tahmin edip faktörü değiştirme engeli).
    from .deps import is_account_locked, register_failed_login
    _ra_email = (current_user.get("email") or "").lower()
    _locked, _retry = await is_account_locked(_ra_email, ip)
    if _locked:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen daha sonra tekrar deneyin.")
    if not verify_password(pw, u.get("password", "")):
        await register_failed_login(_ra_email, ip)
        await write_audit_log("mfa_reauth", user_id=current_user["id"], email=current_user.get("email"),
                              ip=ip, user_agent=ua, success=False,
                              meta={"reason": "bad_password", "action": action})
        raise HTTPException(status_code=403, detail="Mevcut şifre hatalı")
    if need_factor and u.get("mfa_enabled"):
        # Authenticator hesabı: secret var ve yöntem kod-tabanlı (sms/email) değil (/enable
        # mfa_method yazmaz → None da TOTP sayılır; login/verify ile aynı yorum).
        if u.get("mfa_secret_enc") and u.get("mfa_method") not in ("sms", "email"):
            cur = str(payload.get("current_code") or "").strip().replace(" ", "")
            try:
                _sec = decrypt(u.get("mfa_secret_enc"))
            except Exception:
                _sec = None
            if not _verify_totp(_sec, cur):
                await write_audit_log("mfa_reauth", user_id=current_user["id"],
                                      email=current_user.get("email"), ip=ip, user_agent=ua,
                                      success=False, meta={"reason": "bad_factor", "action": action})
                raise HTTPException(status_code=403,
                                    detail="Mevcut Authenticator kodunuz doğrulanamadı (current_code).")
        else:
            raise HTTPException(status_code=403, detail=_REAUTH_RELOGIN_MSG)
    return {"method": "password", "auth_time": now_ts()}


def _pending_setup_fresh(u: dict, field: str) -> bool:
    at = _parse_dt((u or {}).get(field))
    return bool(at) and (datetime.now(timezone.utc) - at) <= timedelta(minutes=_PENDING_SETUP_MAX_MIN)


async def _notify_factor_change(user: dict, what: str, request: Request) -> None:
    """Hesabın e-postasına güvenlik bildirimi (best-effort; akışı asla bozmaz)."""
    to = (user.get("email") or "").strip()
    if not to:
        return
    try:
        import html as _h
        from email_layout import email_shell
        from email_smtp import send_smtp_email
        _when = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        _ip = client_ip_from_request(request) or "-"
        html = email_shell(
            icon="🔐", eyebrow="HESAP GÜVENLİĞİ", title="Giriş doğrulama ayarınız değişti",
            intro_html=(f"Hesabınızda şu değişiklik yapıldı: <b>{_h.escape(what)}</b><br>"
                        f"Zaman: {_h.escape(_when)} · IP: {_h.escape(str(_ip))}<br>"
                        "Güvenliğiniz için diğer cihazlardaki oturumlar kapatıldı."),
            note_title="Bu işlemi siz yapmadıysanız",
            note_html=("Hemen şifrenizi sıfırlayın ve yöneticinize/destek ekibine haber verin."),
            preheader="Giriş doğrulama ayarınız değişti",
        )
        await send_smtp_email(db, to, "Güvenlik bildirimi — giriş doğrulama ayarı değişti", html)
    except Exception as _e:
        logger.warning(f"[mfa] faktör değişikliği bildirimi gönderilemedi: {type(_e).__name__}")


async def _after_factor_change(current_user: dict, request: Request, *, what: str, event: str,
                               auth_time) -> str:
    """token_version artır (diğer oturumlar düşer), denetim kaydı + e-posta; çağırana taze token."""
    from pymongo import ReturnDocument
    doc = await db.users.find_one_and_update(
        {"id": current_user["id"]}, {"$inc": {"token_version": 1}},
        projection={"_id": 0, "id": 1, "email": 1, "is_admin": 1, "token_version": 1},
        return_document=ReturnDocument.AFTER) or {}
    await write_audit_log(event, user_id=current_user["id"], email=current_user.get("email"),
                          ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
                          success=True, meta={"change": what})
    await _notify_factor_change({**current_user, **doc}, what, request)
    return create_token(current_user["id"], bool(doc.get("is_admin", current_user.get("is_admin"))),
                        token_version=doc.get("token_version", 0), auth_time=auth_time or None)


@router.post("/setup-sms")
@(limiter.limit("3/minute;20/day") if limiter else (lambda f: f))
async def mfa_setup_sms(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                        credentials: HTTPAuthorizationCredentials = Depends(security)):
    """SMS MFA kurulumu: telefon kaydeder (şifreli) + doğrulama kodu gönderir (henüz aktif değil).
    DENETİM (cost-redteam #1): bu uç limiter/cooldown/TR-only KONTROLÜ OLMADAN keyfi numaraya
    SMS gönderiyordu (ücretsiz üye → sınırsız SMS pompası/bombardıman). IP limiti + TR-mobil +
    kullanıcı-başı 30sn bekleme eklendi (iys/otp ve /mfa/send ile simetrik)."""
    _me = await db.users.find_one({"id": current_user["id"]},
                                  {"_id": 0, "mfa_admin_managed": 1, "mfa_last_sms_at": 1})
    if _me and _me.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama telefonunuz yönetici tarafından belirlenir ve değiştirilemez. Değişiklik için yöneticinize başvurun.")
    # Denetim 2026-10: faktör bağlama yeniden kimlik doğrulama ister.
    await _require_reauth(credentials, current_user, payload, request, need_factor=True, action="setup_sms")
    # Kullanıcı başına 30 sn bekleme (kredi koruması)
    try:
        _last = (_me or {}).get("mfa_last_sms_at")
        if _last:
            _lt = datetime.fromisoformat(str(_last).replace("Z", "+00:00"))
            if _lt.tzinfo is None:
                _lt = _lt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - _lt).total_seconds() < 30:
                raise HTTPException(status_code=429, detail="Çok sık deneme. Birkaç saniye sonra tekrar deneyin.")
    except HTTPException:
        raise
    except Exception:
        pass
    phone = (payload or {}).get("phone", "")
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from notification_service import normalize_phone_tr
    pn = normalize_phone_tr(phone)
    # Yalnız TR mobil (90 5XX…) — yurtdışı/premium numaralarla drenaj engeli.
    if not (len(pn) == 12 and pn.startswith("905") and pn.isdigit()):
        raise HTTPException(status_code=400, detail="Geçerli bir Türkiye cep telefonu numarası girin")
    await db.users.update_one({"id": current_user["id"]},
                              {"$set": {"mfa_pending_phone_enc": encrypt(pn),
                                        "mfa_pending_phone_at": datetime.now(timezone.utc).isoformat(),
                                        "mfa_last_sms_at": datetime.now(timezone.utc).isoformat()}})
    _u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    _u["mfa_phone_enc"] = _u.get("mfa_pending_phone_enc")
    sent = await send_mfa_sms_code(_u)
    return {"success": True, "sent": sent, "phone_masked": _mask_phone(pn)}


@router.post("/enable-sms")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def mfa_enable_sms(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                         credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Gönderilen SMS kodu doğrulanırsa SMS MFA aktifleşir. Kurulum yeniden-doğrulamayla
    başlatılmış (≤30 dk) olmalı ya da bu istekte yeniden doğrulama yapılmalı."""
    code = (payload or {}).get("code")
    _me = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_admin_managed": 1})
    if _me and _me.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama telefonunuz yönetici tarafından belirlenir ve değiştirilemez.")
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "mfa_pending_phone_enc": 1, "mfa_pending_phone_at": 1})
    if not (u and u.get("mfa_pending_phone_enc")):
        raise HTTPException(status_code=400, detail="Önce SMS kurulumunu başlatın")
    if _pending_setup_fresh(u, "mfa_pending_phone_at"):
        _auth_at = token_auth_time(credentials) or None
    else:
        _auth_at = (await _require_reauth(credentials, current_user, payload, request,
                                          need_factor=True, action="enable_sms"))["auth_time"]
    try:
        _pending_pn = decrypt(u["mfa_pending_phone_enc"]) or ""
    except Exception:
        _pending_pn = ""
    if (await _consume_code(current_user["id"], code, ("sms",), targets={"sms": _pending_pn})) is None:
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    _now_en = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": current_user["id"]},
                              {"$set": {"mfa_enabled": True, "mfa_method": "sms",
                                        "mfa_phone_enc": u["mfa_pending_phone_enc"],
                                        "mfa_enabled_at": _now_en, "mfa_factor_enrolled_at": _now_en},
                               "$unset": {"mfa_pending_phone_enc": "", "mfa_pending_phone_at": ""}})
    token = await _after_factor_change(current_user, request, what="SMS doğrulama telefonu tanımlandı",
                                       event="mfa_enable_sms", auth_time=_auth_at)
    return {"success": True, "mfa_enabled": True, "mfa_method": "sms", "token": token}


@router.post("/send")
@(limiter.limit("3/minute") if limiter else (lambda f: f))
async def mfa_send_login_code(payload: dict, request: Request):
    """Login 2. adımında SMS kodunu (yeniden) gönderir. mfa_token ile kimlik doğrular.
    GÜVENLİK: IP hız-sınırı (3/dk) + kullanıcı başına 30 sn bekleme → SMS drenajı/bombardımanı engeli."""
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
    if not user:
        raise HTTPException(status_code=400, detail="MFA aktif değil")
    if user.get("is_active") is False:
        raise HTTPException(status_code=403, detail="Hesap devre dışı.")
    if await _pending_token_revoked(decoded, user):
        raise HTTPException(status_code=401, detail="MFA süresi doldu, tekrar giriş yapın")
    # KANAL SEÇİMİ (kullanıcı isteği): kod SMS ya da E-POSTA ile istenebilir. İstekte kanal
    # belirtilmezse kişinin VARSAYILANI (mfa_channel_default), o da yoksa SMS kullanılır.
    _ch = str((payload or {}).get("channel") or "").strip().lower()
    _req_ch = _ch if _ch in ("sms", "email") else ""
    if _ch not in ("sms", "email"):
        _ch = str(user.get("mfa_channel_default") or "sms").strip().lower()
        if _ch not in ("sms", "email"):
            _ch = "sms"
    _phone = _resolve_mfa_phone(user)
    _mail = _resolve_mfa_email(user)
    # TOTP (authenticator) kurulu hesapta kod gönderimi yapılmaz — uygulamadan okunur.
    if user.get("mfa_method") not in (None, "", "sms", "email") and user.get("mfa_secret_enc"):
        raise HTTPException(status_code=400, detail="Bu hesap doğrulayıcı uygulama kullanıyor")
    # ADMIN KANAL POLİTİKASI (denetim 2026-10, #2): yalnız izinli kanallara kod gönderilir.
    _plan = admin_mfa_plan(user)
    _mail_mask_hidden = False
    if _plan.get("policy"):
        _code_ok = [c for c in _plan["allowed"] if c in _CODE_CHANNELS]
        if not _code_ok:
            raise HTTPException(status_code=400,
                                detail="Bu hesapta giriş doğrulaması Authenticator (TOTP) uygulamasıyla yapılmalı.")
        if _req_ch and _req_ch not in _code_ok:
            raise HTTPException(status_code=403,
                                detail=admin_mfa_notice(_plan) or "Bu doğrulama kanalı bu hesap için kullanılamaz.")
        if _ch not in _code_ok:
            _ch = _code_ok[0]
        if "sms" not in _code_ok:
            _phone = ""  # izinsiz kanalın maskesi dönmez → giriş ekranı o butonu pasif gösterir
        _mail_mask_hidden = "email" not in _code_ok
    if _ch == "email":
        if not _mail:
            raise HTTPException(status_code=400, detail="Hesapta kayıtlı e-posta yok")
        # Kullanıcı başına 30 sn bekleme (e-posta bombardımanı engeli; SMS ile simetrik).
        _lt = _parse_dt(user.get("mfa_last_email_at"))
        if _lt and (datetime.now(timezone.utc) - _lt).total_seconds() < 30:
            raise HTTPException(status_code=429, detail="Çok sık deneme. Lütfen birkaç saniye sonra tekrar deneyin.")
        _sent = await send_mfa_email_code(user)
        try:
            await db.users.update_one({"id": user["id"]},
                                      {"$set": {"mfa_last_email_at": datetime.now(timezone.utc).isoformat()}})
        except Exception:
            pass
        return {"success": True, "sent": _sent, "channel": "email",
                "email_masked": _mask_email(_mail),
                "phone_masked": _mask_phone(_phone) if _phone else ""}
    if not _phone:
        raise HTTPException(status_code=400, detail="Hesapta kayıtlı telefon yok")
    # Kullanıcı başına 30 sn bekleme — aynı token'la sınırsız SMS gönderimini engeller (kredi koruması).
    try:
        _last = user.get("mfa_last_sms_at")
        if _last:
            _lt = datetime.fromisoformat(str(_last).replace("Z", "+00:00"))
            if _lt.tzinfo is None:
                _lt = _lt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - _lt).total_seconds() < 30:
                raise HTTPException(status_code=429, detail="Çok sık deneme. Lütfen birkaç saniye sonra tekrar deneyin.")
    except HTTPException:
        raise
    except Exception:
        pass
    sent = await send_mfa_sms_code(user)
    try:
        await db.users.update_one({"id": user["id"]}, {"$set": {"mfa_last_sms_at": datetime.now(timezone.utc).isoformat()}})
    except Exception:
        pass
    return {"success": True, "sent": sent, "channel": "sms",
            "phone_masked": _mask_phone(_phone),
            "email_masked": _mask_email(_mail) if (_mail and not _mail_mask_hidden) else ""}


@router.post("/channel")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def set_mfa_default_channel(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                                  credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Kişinin VARSAYILAN doğrulama kanalı: 'sms' veya 'email' (kullanıcı isteği —
    herkes kendi yöntemini seçsin). Girişte kod bu kanaldan gönderilir; giriş ekranından
    diğer kanal her zaman alternatif olarak seçilebilir."""
    ch = str((payload or {}).get("channel") or "").strip().lower()
    if ch not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="Kanal 'sms' veya 'email' olmalı")
    # Denetim 2026-10: varsayılan kanal değişimi de yeniden doğrulama ister (şifre ya da son 5 dk giriş).
    # Kanal hesabın KENDİ kayıtlı telefon/e-postası arasında seçilir (yeni faktör bağlanmaz) →
    # oturumlar düşürülmez, yalnız e-posta bildirimi gider.
    await _require_reauth(credentials, current_user, payload, request, need_factor=False, action="channel")
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "phone": 1, "phone_enc": 1, "email": 1, "mfa_phone_enc": 1, "phone_verified": 1})
    if ch == "email" and not _resolve_mfa_email(u or {}):
        raise HTTPException(status_code=400, detail="Hesapta kayıtlı e-posta yok")
    if ch == "sms" and not _resolve_mfa_phone(u or {}):
        raise HTTPException(status_code=400, detail="Hesapta kayıtlı telefon yok")
    await db.users.update_one({"id": current_user["id"]},
                              {"$set": {"mfa_channel_default": ch,
                                        "updated_at": datetime.now(timezone.utc).isoformat()}})
    await write_audit_log("mfa_channel_change", user_id=current_user["id"], email=current_user.get("email"),
                          ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
                          success=True, meta={"channel": ch})
    await _notify_factor_change(current_user, "Varsayılan doğrulama kanalı: " + ("E-posta" if ch == "email" else "SMS"),
                                request)
    return {"success": True, "channel": ch}


@router.get("/status")
async def mfa_status(current_user: dict = Depends(require_auth)):
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "mfa_enabled": 1, "mfa_channel_default": 1,
                                 "phone": 1, "phone_enc": 1, "email": 1, "mfa_phone_enc": 1, "phone_verified": 1,
                                 "mfa_recovery_codes": 1, "mfa_recovery_generated_at": 1,
                                 "is_admin": 1}) or {}
    _p, _m = _resolve_mfa_phone(u), _resolve_mfa_email(u)
    _rc = [c for c in (u.get("mfa_recovery_codes") or []) if isinstance(c, dict)]
    return {"mfa_enabled": bool(u.get("mfa_enabled")),
            "channel_default": (u.get("mfa_channel_default") or "sms"),
            "sms_available": bool(_p), "email_available": bool(_m),
            "phone_masked": _mask_phone(_p) if _p else "",
            "email_masked": _mask_email(_m) if _m else "",
            "recovery_codes_supported": bool(u.get("is_admin")),
            "recovery_codes_total": len(_rc),
            "recovery_codes_remaining": sum(1 for c in _rc if not c.get("used")),
            "recovery_codes_generated_at": u.get("mfa_recovery_generated_at")}


@router.post("/setup")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def mfa_setup(request: Request, current_user: dict = Depends(require_auth),
                    credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Yeni TOTP secret üretir (henüz aktif değil), QR + otpauth URI döner.
    Denetim 2026-10: yeniden kimlik doğrulama zorunlu (gövde: current_password [+ current_code]
    ya da son 5 dk içinde giriş); yönetici-yönetimli hesapta kapalı."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _me = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "mfa_admin_managed": 1})
    if _me and _me.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama yönteminiz yönetici tarafından belirlenir ve değiştirilemez. "
                   "Değişiklik için yöneticinize başvurun.")
    await _require_reauth(credentials, current_user, payload, request, need_factor=True, action="setup_totp")
    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.get("email", "user"), issuer_name=ISSUER_NAME
    )
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_pending_secret_enc": encrypt(secret),
                  "mfa_pending_secret_at": datetime.now(timezone.utc).isoformat()}},
    )
    # QR PNG -> base64 data URI
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return {"otpauth_uri": uri, "qr_code": f"data:image/png;base64,{qr_b64}", "secret": secret}


@router.post("/enable")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def mfa_enable(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                     credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Pending secret'a karşı kod doğrulanırsa MFA aktifleşir. Kurulum yeniden-doğrulamayla
    başlatılmış (≤30 dk) olmalı ya da bu istekte yeniden doğrulama yapılmalı."""
    code = (payload or {}).get("code")
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "mfa_pending_secret_enc": 1, "mfa_pending_secret_at": 1,
                                 "mfa_admin_managed": 1})
    if u and u.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama yönteminiz yönetici tarafından belirlenir ve değiştirilemez.")
    pending = decrypt(u.get("mfa_pending_secret_enc")) if u and u.get("mfa_pending_secret_enc") else None
    if not pending:
        raise HTTPException(status_code=400, detail="Önce MFA kurulumunu başlatın (setup)")
    if _pending_setup_fresh(u, "mfa_pending_secret_at"):
        _auth_at = token_auth_time(credentials) or None
    else:
        _auth_at = (await _require_reauth(credentials, current_user, payload, request,
                                          need_factor=True, action="enable_totp"))["auth_time"]
    if not _verify_totp(pending, code):
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_enabled": True, "mfa_secret_enc": encrypt(pending),
                  "mfa_enabled_at": datetime.now(timezone.utc).isoformat(),
                  "mfa_factor_enrolled_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"mfa_pending_secret_enc": "", "mfa_pending_secret_at": ""}},
    )
    token = await _after_factor_change(current_user, request, what="Authenticator (TOTP) uygulaması tanımlandı",
                                       event="mfa_enable_totp", auth_time=_auth_at)
    return {"success": True, "mfa_enabled": True, "token": token}


@router.post("/disable")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def mfa_disable(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                      credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Geçerli TOTP kodu + yeniden kimlik doğrulama (şifre ya da son 5 dk giriş) ile MFA kapatılır."""
    code = (payload or {}).get("code")
    u = await db.users.find_one({"id": current_user["id"]},
                                {"_id": 0, "mfa_secret_enc": 1, "mfa_enabled": 1, "mfa_admin_managed": 1})
    if not (u and u.get("mfa_enabled")):
        return {"success": True, "mfa_enabled": False}
    if u.get("mfa_admin_managed"):
        raise HTTPException(status_code=403,
            detail="Giriş doğrulama yönteminiz yönetici tarafından belirlenir ve kapatılamaz.")
    # AUTH24: kurtarma koduyla girişten sonra (yeni faktör bağlanana kadar, ≤24 sa) MFA kapatılamaz.
    from .deps import assert_no_recovery_restriction
    _fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "password": 0}) or current_user
    await assert_no_recovery_restriction(_fresh, "mfa_disable")
    _ra = await _require_reauth(credentials, current_user, payload, request, need_factor=False,
                                action="disable")
    secret = decrypt(u.get("mfa_secret_enc")) if u.get("mfa_secret_enc") else None
    if not _verify_totp(secret, code):
        raise HTTPException(status_code=400, detail="Kod doğrulanamadı")
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"mfa_enabled": False},
         "$unset": {"mfa_secret_enc": "", "mfa_pending_secret_enc": "", "mfa_pending_secret_at": ""}},
    )
    token = await _after_factor_change(current_user, request, what="Authenticator (TOTP) doğrulaması kapatıldı",
                                       event="mfa_disable", auth_time=_ra.get("auth_time"))
    return {"success": True, "mfa_enabled": False, "token": token}


# ─────────────── MFA KURTARMA KODLARI (BL-003) ───────────────
# 10 adet tek kullanımlık kod; YALNIZ bir kez gösterilir, sha256 hash'i saklanır. Yeniden üretim
# eskileri geçersiz kılar. Girişte (MFA adımı) kod yerine kullanılabilir — atomik tüketim
# (positional $elemMatch + used:false). Kullanım: denetim kaydı + süper admin uyarısı + e-posta.
RECOVERY_CODE_COUNT = 10
_RC_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # karışan karakterler (0/O, 1/I) yok


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    return _hl.sha256(f"mfa-rc:{normalize_recovery_code(code)}".encode()).hexdigest()


def new_recovery_codes(n: int = RECOVERY_CODE_COUNT) -> tuple:
    """(gösterilecek kodlar, saklanacak kayıtlar)."""
    plain, recs = [], []
    for _ in range(n):
        raw = "".join(_secrets.choice(_RC_ALPHABET) for _ in range(10))
        plain.append(f"{raw[:5]}-{raw[5:]}")
        recs.append({"h": hash_recovery_code(raw), "used": False})
    return plain, recs


async def consume_recovery_code(user_id: str, code: str) -> bool:
    norm = normalize_recovery_code(code)
    if len(norm) != 10:
        return False
    h = hash_recovery_code(norm)
    res = await db.users.find_one_and_update(
        {"id": user_id, "mfa_recovery_codes": {"$elemMatch": {"h": h, "used": False}}},
        {"$set": {"mfa_recovery_codes.$.used": True,
                  "mfa_recovery_codes.$.used_at": datetime.now(timezone.utc).isoformat()}},
        projection={"_id": 0, "id": 1})
    return bool(res)


@router.post("/recovery-codes")
@(limiter.limit("5/minute") if limiter else (lambda f: f))
async def mfa_generate_recovery_codes(payload: dict, request: Request, current_user: dict = Depends(require_auth),
                                      credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Yeni 10 kurtarma kodu üretir (eskiler geçersiz). Yeniden kimlik doğrulama zorunlu.
    Kodlar YALNIZ bu yanıtta görünür."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kurtarma kodları yalnız personel hesapları içindir")
    await _require_reauth(credentials, current_user, payload or {}, request, need_factor=True,
                          action="recovery_codes")
    plain, recs = new_recovery_codes()
    now_iso = datetime.now(timezone.utc).isoformat()
    from activity_audit import run_audited

    async def _perform():
        await db.users.update_one({"id": current_user["id"]},
                                  {"$set": {"mfa_recovery_codes": recs, "mfa_recovery_generated_at": now_iso}})
    await run_audited(db, action="mfa.recovery_codes_generate", entity_type="user", entity_id=current_user["id"],
                      perform=_perform, current_user=current_user, request=request, source="auth.mfa",
                      metadata={"count": len(recs)})
    await write_audit_log("mfa_recovery_codes_generate", user_id=current_user["id"], email=current_user.get("email"),
                          ip=client_ip_from_request(request), user_agent=request.headers.get("user-agent"),
                          success=True, meta={"count": len(recs)})
    await _notify_factor_change(current_user, "MFA kurtarma kodları yeniden oluşturuldu (eskiler geçersiz)", request)
    return {"success": True, "codes": plain, "generated_at": now_iso,
            "message": "Bu kodları güvenli bir yere kaydedin; tekrar gösterilmeyecek."}


async def _after_recovery_code_login(user: dict, ip: str, ua: str, request: Request) -> int:
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "mfa_recovery_codes": 1}) or {}
    remaining = sum(1 for c in (u.get("mfa_recovery_codes") or []) if isinstance(c, dict) and not c.get("used"))
    try:
        from security.events import security_event
        await security_event("mfa_recovery_used", user,
                             {"target": user.get("id"), "staff": user.get("email") or user.get("id"),
                              "ip": ip or "-", "remaining_codes": remaining, "fp": user.get("id")},
                             request=request,
                             body="Personel ikinci faktör yerine tek kullanımlık kurtarma kodu ile giriş yaptı.")
    except Exception:
        pass
    await _notify_factor_change(user, f"Girişte MFA kurtarma kodu kullanıldı (kalan: {remaining})", request)
    return remaining


_MFA_WINDOW_MIN = 5
_MFA_WINDOW_MAX = 10          # 5 dk pencerede en çok 10 deneme (başarı sayacı sıfırlar)
_MFA_DAILY_MAX_FAILS = 30     # hesap başına 24 saatte en çok 30 HATALI deneme (#7)


async def _reserve_mfa_attempt(user_id: str) -> None:
    """ATOMİK deneme rezervasyonu (denetim 2026-10, #6/#7). Eskiden sayaç önce okunup sonra
    artırılıyordu → eşzamanlı isteklerle 10 sınırı aşılabiliyordu. Artık deneme hakkı koşullu
    $inc ile ÖNCEDEN alınır; hak yoksa 429."""
    now = datetime.now(timezone.utc)
    win_start = (now - timedelta(minutes=_MFA_WINDOW_MIN)).isoformat()
    # Pencere yoksa/eskidiyse yeni pencere aç (sayaç 0).
    await db.users.update_one(
        {"id": user_id, "$or": [{"mfa_fail_window": {"$exists": False}},
                                {"mfa_fail_window": None},
                                {"mfa_fail_window": {"$lt": win_start}}]},
        {"$set": {"mfa_fail_count": 0, "mfa_fail_window": now.isoformat()}})
    got = await db.users.find_one_and_update(
        {"id": user_id, "$or": [{"mfa_fail_count": {"$lt": _MFA_WINDOW_MAX}},
                                {"mfa_fail_count": {"$exists": False}},
                                {"mfa_fail_count": None}]},
        {"$inc": {"mfa_fail_count": 1}},
        projection={"_id": 0, "id": 1})
    if not got:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme, 5 dakika sonra tekrar deneyin")


def _daily_fail_capped(user: dict) -> bool:
    start = _parse_dt(user.get("mfa_day_window"))
    if not start or (datetime.now(timezone.utc) - start) > timedelta(hours=24):
        return False
    return int(user.get("mfa_day_fail_count") or 0) >= _MFA_DAILY_MAX_FAILS


async def _register_daily_fail(user_id: str) -> None:
    now = datetime.now(timezone.utc)
    day_start = (now - timedelta(hours=24)).isoformat()
    r = await db.users.update_one({"id": user_id, "mfa_day_window": {"$gte": day_start}},
                                  {"$inc": {"mfa_day_fail_count": 1}})
    if getattr(r, "matched_count", 0) == 0:
        await db.users.update_one({"id": user_id},
                                  {"$set": {"mfa_day_fail_count": 1, "mfa_day_window": now.isoformat()}})


@router.post("/verify")
@(limiter.limit("10/minute") if limiter else (lambda f: f))
async def mfa_verify(payload: dict, request: Request):
    """Login 2. adımı: mfa_token + kod -> tam JWT.
    Brute-force koruması: IP başına 10/dk + kullanıcı başına 5 dakikada en fazla 10 deneme
    (atomik) + hesap başına 24 saatte en çok 30 hatalı deneme."""
    mfa_token = (payload or {}).get("mfa_token")
    code = str((payload or {}).get("code") or "").strip().replace(" ", "")
    recovery_code = safe_str((payload or {}).get("recovery_code"), 64).strip()
    if not mfa_token or not (code or recovery_code):
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
    # DENETİM (auth-redteam #7): login 1. adımı ile MFA doğrulama arasında hesap DEVRE DIŞI
    # bırakılmışsa oturum verilmemeli — is_active False ise reddet.
    if user.get("is_active") is False:
        raise HTTPException(status_code=403, detail="Hesap devre dışı.")
    if await _pending_token_revoked(decoded, user):
        raise HTTPException(status_code=401, detail="MFA süresi doldu, tekrar giriş yapın")
    # OTOMATİK KOD-MFA: hesap resmî olarak MFA kurmamış olsa bile, admin + zorunlu MFA +
    # kayıtlı telefon ya da e-posta varsa kod doğrulaması geçerlidir (kurulum ekranı gerekmez).
    # (Eskiden yalnız telefon aranıyordu → girişte e-postaya kod giden telefonsuz admin
    # doğrulamada "MFA aktif değil" alıp kilitleniyordu.)
    _auto_sms = False
    if recovery_code:
        pass  # kurtarma kodu: hesapta kod tanımlıysa geçerlidir (aşağıda atomik tüketilir)
    elif not user.get("mfa_enabled"):
        if (user.get("is_admin") and await admin_mfa_enforced()
                and (_resolve_mfa_phone(user) or _resolve_mfa_email(user))):
            _auto_sms = True
        else:
            raise HTTPException(status_code=400, detail="MFA aktif değil")

    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent")
    # Günlük sınır: acil kapatma (ADMIN_MFA_ENFORCE=off) açıksa uygulanmaz (sahibi kilitte kalmasın).
    if _daily_fail_capped(user) and not admin_mfa_break_glass():
        await write_audit_log("mfa_verify", user_id=user["id"], email=user.get("email"), ip=ip,
                              user_agent=ua, success=False, meta={"reason": "daily_cap"})
        raise HTTPException(status_code=429,
                            detail="Son 24 saatte çok fazla hatalı doğrulama denemesi yapıldı. "
                                   "Şifrenizi sıfırlayın ya da daha sonra tekrar deneyin.")
    await _reserve_mfa_attempt(user["id"])

    _plan = admin_mfa_plan(user)
    _used_channel = None
    if recovery_code:
        # Kurtarma kodu: deneme sınırları (yukarıda) aynen geçerli; kod tek kullanımlık, atomik.
        if await consume_recovery_code(user["id"], recovery_code):
            _used_channel = "recovery_code"
    elif _plan.get("policy"):
        # ADMIN KANAL POLİTİKASI: yalnız izinli faktörler kabul edilir (TOTP ve/veya izinli
        # kanaldan GÖNDERİLMİŞ kod).
        _allowed = _plan["allowed"]
        if "totp" in _allowed and user.get("mfa_secret_enc"):
            try:
                _sec = decrypt(user.get("mfa_secret_enc"))
            except Exception:
                _sec = None
            if _verify_totp(_sec, code):
                _used_channel = "totp"
        if _used_channel is None:
            _code_allowed = [c for c in _allowed if c in _CODE_CHANNELS]
            if _code_allowed:
                _used_channel = await _consume_code(user["id"], code, _code_allowed,
                                                    targets=_code_targets(user))
    else:
        # Eski davranış (müşteri hesabı / acil kapatma): SMS (kurulu veya otomatik) ise kodu,
        # TOTP secret'i olan hesapta authenticator kodunu doğrula.
        if _auto_sms or user.get("mfa_method") == "sms" or not user.get("mfa_secret_enc"):
            _used_channel = await _consume_code(user["id"], code, targets=_code_targets(user))
        else:
            secret = decrypt(user.get("mfa_secret_enc")) if user.get("mfa_secret_enc") else None
            if _verify_totp(secret, code):
                _used_channel = "totp"
    if _used_channel is None:
        await _register_daily_fail(user["id"])
        await write_audit_log("mfa_verify", user_id=user["id"], email=user.get("email"), ip=ip,
                              user_agent=ua, success=False, meta={"reason": "bad_code"})
        raise HTTPException(status_code=401, detail="Kod doğrulanamadı")

    # başarı -> sayaç sıfırla
    _succ_set = {"mfa_fail_count": 0}
    # OTOMATİK KOD-MFA'yı ilk başarılı doğrulamada KALICI kaydet: hesap telefonu
    # mfa_phone_enc'e şifreli yazılır → bundan sonra kurulum/telefon girme sorulmaz.
    if _auto_sms:
        _succ_set["mfa_enabled"] = True
        _succ_set["mfa_method"] = "sms"
        if not user.get("mfa_phone_enc"):
            _pn = _resolve_mfa_phone(user)
            if _pn:
                _succ_set["mfa_phone_enc"] = encrypt(_pn)
        _succ_set["mfa_enabled_at"] = datetime.now(timezone.utc).isoformat()
    if _used_channel == "recovery_code":
        # AUTH24: kurtarma girişi anı → 24 sa yüksek riskli işlem kısıtı (yeni faktör bağlanınca kalkar).
        _succ_set["mfa_recovery_login_at"] = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"id": user["id"]}, {"$set": _succ_set, "$unset": {"mfa_fail_window": ""}})
    await write_audit_log("mfa_verify", user_id=user["id"], email=user.get("email"), ip=ip,
                          user_agent=ua, success=True,
                          meta={"channel": _used_channel or "legacy",
                                "degraded": bool(_plan.get("degraded")),
                                "reset_channel": _plan.get("reset_channel") or ""})

    _rc_remaining = None
    if _used_channel == "recovery_code":
        _rc_remaining = await _after_recovery_code_login(user, ip, ua, request)
    if user.get("is_admin"):
        try:
            from security.events import note_staff_login, fire_and_forget
            fire_and_forget(note_staff_login(user, ip, ua, method=f"mfa:{_used_channel or 'legacy'}"))
        except Exception:
            pass

    # İkinci faktör (SMS/e-posta kodu ya da TOTP) doğrulandı → oturum "mfa" işaretli; kurtarma kodu HARİÇ
    # (o oturum zaten 24 sa kısıtlı). Toplu kişisel veri görüntüleme engeli bu oturumlara uygulanmaz.
    _amr = ["mfa", str(_used_channel or "kod")[:12]] if _used_channel and _used_channel != "recovery_code" else None
    token = create_token(user["id"], user.get("is_admin", False), token_version=user.get("token_version", 0),
                         auth_time=now_ts(), amr=_amr)
    return {
        "token": token,
        "recovery_codes_remaining": _rc_remaining,
        "user": {
            "id": user["id"], "email": user["email"],
            "first_name": user.get("first_name", ""), "last_name": user.get("last_name", ""),
            # KVKK: müşteri telefonu şifreli olabilir → yanıt için çözülür (sahibine).
            "phone": _resolve_profile_phone(user), "is_admin": user.get("is_admin", False),
            "created_at": user.get("created_at"),
        },
    }
