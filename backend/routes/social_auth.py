"""
=============================================================================
social_auth.py — Apple + Facebook Sosyal Login (scaffold)
=============================================================================

Bu modül:
  - Backend endpoint'leri hazır
  - Kullanıcı Apple Developer veya Facebook Developer Console'dan credential
    girip settings.social_auth altında kaydedince devreye alınır

Endpoint'ler:
  GET  /api/auth/social/providers   → aktif sağlayıcıların listesi (ui için)
  GET  /api/auth/social/settings    → admin: mevcut credential'lar (maskeli)
  POST /api/auth/social/settings    → admin: credential kaydet
  POST /api/auth/apple              → frontend'den identity_token ile login
  POST /api/auth/facebook           → frontend'den authorization code ile login

Not: JWT issuance mevcut auth.py ile aynı format (`token` alanı) uyumludur.
=============================================================================
"""
import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import httpx
import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from .deps import (db, require_admin, generate_id, create_token, secure_social_link, require_permission,
                   public_self_user)
try:
    from .auth import create_access_token
except Exception:
    create_access_token = None  # runtime fallback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["social-auth"])


# =============================================================================
# Provider settings (admin)
# =============================================================================

class SocialSettingsReq(BaseModel):
    google_enabled: bool = False
    google_client_id: Optional[str] = ""       # OAuth Web client ID; secret değildir
    apple_enabled: bool = False
    apple_client_id: Optional[str] = ""       # Services ID, ör. com.meluni.web
    apple_team_id: Optional[str] = ""
    apple_key_id: Optional[str] = ""
    apple_private_key: Optional[str] = ""     # PEM
    facebook_enabled: bool = False
    facebook_app_id: Optional[str] = ""
    facebook_app_secret: Optional[str] = ""
    facebook_redirect_uri: Optional[str] = ""


_SOCIAL_SECRETS = {"apple_private_key", "facebook_app_secret"}


async def _get_social_settings() -> Dict[str, Any]:
    doc = await db.settings.find_one({"id": "social_auth"}, {"_id": 0}) or {}
    # DENETİM (kimlik B-2): Apple özel anahtarı + Facebook app secret at-rest şifreli;
    # okurken çöz (legacy düz-metin olduğu gibi döner → migration güvenli).
    try:
        from security.crypto import decrypt as _dec
        for f in _SOCIAL_SECRETS:
            if doc.get(f):
                doc[f] = _dec(doc[f])
    except Exception:
        pass
    return doc


@router.get("/social/providers")
async def get_providers():
    """Public — frontend hangi butonu gösterecek karar verir."""
    s = await _get_social_settings()
    env_google_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    google_id = (s.get("google_client_id") or env_google_id).strip()
    google_on = bool(google_id and (s.get("google_enabled") if "google_enabled" in s else env_google_id))
    fb_on = bool(s.get("facebook_enabled") and s.get("facebook_app_id"))
    return {
        "google": google_on,
        "google_client_id": google_id if google_on else "",
        "apple": bool(s.get("apple_enabled") and s.get("apple_client_id")),
        "apple_client_id": s.get("apple_client_id", "") if s.get("apple_enabled") else "",
        "facebook": fb_on,
        # App ID gizli değildir (client_id) — frontend Facebook dialog URL'ini kurar.
        "facebook_app_id": s.get("facebook_app_id", "") if fb_on else "",
    }


@router.get("/social/settings")
async def admin_get_settings(current_user: dict = Depends(require_permission("settings.view"))):
    s = await _get_social_settings()
    env_google_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    # Secret alanları tamamen maskele (yalnız bayrak döndür, ham değer asla dönmez)
    has_apple_key = bool(s.get("apple_private_key"))
    has_fb_secret = bool(s.get("facebook_app_secret"))
    return {
        "google_enabled": bool(s.get("google_enabled") if "google_enabled" in s else env_google_id),
        "google_client_id": s.get("google_client_id") or env_google_id,
        "apple_enabled": bool(s.get("apple_enabled")),
        "apple_client_id": s.get("apple_client_id", ""),
        "apple_team_id": s.get("apple_team_id", ""),
        "apple_key_id": s.get("apple_key_id", ""),
        "apple_private_key": "****" if has_apple_key else "",
        "has_apple_private_key": has_apple_key,
        "facebook_enabled": bool(s.get("facebook_enabled")),
        "facebook_app_id": s.get("facebook_app_id", ""),
        "facebook_app_secret": "****" if has_fb_secret else "",
        "has_facebook_app_secret": has_fb_secret,
        "facebook_redirect_uri": s.get("facebook_redirect_uri", ""),
    }


@router.post("/social/settings")
async def admin_save_settings(req: SocialSettingsReq, current_user: dict = Depends(require_permission("settings.edit"))):
    existing = await _get_social_settings()
    data: Dict[str, Any] = {"id": "social_auth"}
    fields = [
        "google_enabled", "google_client_id",
        "apple_enabled", "apple_client_id", "apple_team_id", "apple_key_id", "apple_private_key",
        "facebook_enabled", "facebook_app_id", "facebook_app_secret", "facebook_redirect_uri",
    ]
    payload = req.model_dump()
    SECRETS = _SOCIAL_SECRETS
    for f in fields:
        v = payload.get(f)
        if f in SECRETS and (not v or (isinstance(v, str) and "****" in v)):
            v = existing.get(f, "")  # eski değeri koru (çözülmüş düz metin)
        data[f] = v
    # DENETİM B-2: sırları at-rest Fernet ile şifrele (idempotent, boş/şifreli atlanır).
    try:
        from security.crypto import encrypt as _enc2, is_encrypted as _isenc
        for f in SECRETS:
            v = data.get(f)
            if isinstance(v, str) and v and not _isenc(v):
                data[f] = _enc2(v)
    except Exception:
        pass
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["updated_by"] = current_user.get("email", "")
    await db.settings.update_one({"id": "social_auth"}, {"$set": data}, upsert=True)
    return {"success": True}


# =============================================================================
# Helpers — user upsert + token issue
# =============================================================================

async def _issue_jwt(user_id: str, email: str, extra: Optional[Dict] = None) -> str:
    """Mevcut create_token ile uyumlu JWT üret (7 gün)."""
    # GÜVENLİK: zayıf sabit "change-me" fallback secret kaldırıldı. Yalnızca vetted
    # create_token (env JWT_SECRET + issuer + exp) kullanılır; başarısız olursa hata ver.
    _tv = 0
    try:
        _u = await db.users.find_one({"id": user_id}, {"_id": 0, "token_version": 1})
        _tv = (_u or {}).get("token_version", 0)
    except Exception:
        pass
    return create_token(user_id, is_admin=False, token_version=_tv)


_SOCIAL_ADMIN_MSG = ("Yönetici hesapları sosyal girişle açılamaz. Lütfen e-posta ve şifrenizle "
                     "(doğrulama koduyla) giriş yapın.")


def _is_staff_account(user: dict) -> bool:
    return bool(user) and (user.get("is_admin") is True or user.get("is_super_admin") is True)


async def _upsert_social_user(provider: str, provider_id: str, email: str, name: str = "") -> dict:
    """Kullanıcıyı bul/oluştur ve JWT'de kullanılacak dict döndür."""
    # 1) provider_id ile bul
    q = {f"auth_providers.{provider}.id": provider_id}
    user = await db.users.find_one(q, {"_id": 0})
    if user and _is_staff_account(user):
        raise HTTPException(status_code=403, detail=_SOCIAL_ADMIN_MSG)
    if user:
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {f"auth_providers.{provider}.linked_at": datetime.now(timezone.utc).isoformat()}}
        )
        return user
    # 2) email ile varolan hesaba bağla
    if email:
        u2 = await db.users.find_one({"email": email.lower()}, {"_id": 0})
        if u2 and provider == "facebook":
            # GÜVENLİK (denetim H2): Facebook Graph e-postası doğrulanmış sayılamaz → mevcut hesaba
            # e-posta eşleşmesiyle OTOMATİK bağlanmaz (başkasının hesabına girişi engeller).
            from fastapi import HTTPException as _HE
            raise _HE(status_code=409, detail=(
                "Bu e-posta adresiyle kayıtlı bir hesabınız var. Lütfen e-posta ve şifrenizle giriş yapın."))
        if u2 and _is_staff_account(u2):
            # GÜVENLİK (denetim 2026-10, #9): personel/admin hesabı sosyal girişle açılamaz
            # (parola + MFA zorunlu).
            raise HTTPException(status_code=403, detail=_SOCIAL_ADMIN_MSG)
        if u2:
            _bound = str(((u2.get("auth_providers") or {}).get(provider) or {}).get("id") or "")
            if _bound and _bound != str(provider_id):
                # Hesap bu sağlayıcıda BAŞKA bir kimliğe bağlı → e-posta eşleşmesiyle üzerine yazma.
                raise HTTPException(status_code=409, detail=(
                    "Bu e-posta adresi farklı bir hesaba bağlı. Lütfen e-posta ve şifrenizle giriş yapın."))
            u2 = await secure_social_link(u2, provider)
            await db.users.update_one(
                {"id": u2["id"]},
                {"$set": {
                    f"auth_providers.{provider}.id": provider_id,
                    f"auth_providers.{provider}.linked_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            return u2
    # 3) yeni kullanıcı
    first_name, _, last_name = (name or "").partition(" ")
    new_user = {
        "id": generate_id(),
        "email": (email or f"{provider}_{provider_id}@meluni.com.tr").lower(),
        "first_name": first_name or "",
        "last_name": last_name or "",
        "password": "",  # sosyal girişli
        "role": "customer",
        "is_active": True,
        # ID3-03: yalnız sağlayıcının İMZALI/doğrulanmış e-postası (Apple email_verified claim'i)
        # doğrulanmış sayılır. Facebook Graph e-postası doğrulanmış SAYILMAZ (bkz. yukarıdaki H2 notu) —
        # aksi halde başkasının e-postasıyla açılan FB hesabı o e-postanın misafir siparişlerini
        # (adres/telefon, iade-IBAN) customer._owner_or_clauses üzerinden sahiplenebiliyordu.
        # E-postası olmayan (sentetik adresli) hesap da doğrulanmış sayılmaz.
        "email_verified": bool(email) and provider != "facebook",
        "auth_providers": {provider: {"id": provider_id, "linked_at": datetime.now(timezone.utc).isoformat()}},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": f"social_{provider}",
    }
    await db.users.insert_one(new_user)
    new_user.pop("_id", None)
    return new_user


# =============================================================================
# APPLE
# =============================================================================

class AppleLoginReq(BaseModel):
    identity_token: str
    user_email: Optional[str] = None
    user_name: Optional[str] = ""   # ör. "Ahmet Yılmaz"


@router.post("/apple")
async def apple_login(req: AppleLoginReq):
    s = await _get_social_settings()
    if not s.get("apple_enabled") or not s.get("apple_client_id"):
        raise HTTPException(status_code=503, detail="Apple Sign-In admin tarafından etkinleştirilmedi")
    # Apple public key ile verify
    try:
        header = pyjwt.get_unverified_header(req.identity_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Geçersiz token formatı")  # istisna metni sızdırılmaz (INFRA-03)
    kid = header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="Geçersiz token (kid eksik)")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://appleid.apple.com/auth/keys")
        keys = (r.json() or {}).get("keys", [])
    match = next((k for k in keys if k.get("kid") == kid), None)
    if not match:
        raise HTTPException(status_code=401, detail="Apple public key bulunamadı")
    pub = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(match))
    try:
        payload = pyjwt.decode(req.identity_token, pub, algorithms=["RS256"],
                               audience=s["apple_client_id"])
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Apple token geçersiz")  # istisna metni sızdırılmaz (INFRA-03)
    if payload.get("iss") != "https://appleid.apple.com":
        raise HTTPException(status_code=401, detail="Apple token yayıncısı geçersiz")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="Apple token sub eksik")
    # GÜVENLİK (ATO düzeltmesi): Hesap eşleştirme/bağlama ASLA istemci girdisine
    # (req.user_email) dayanamaz — aksi halde saldırgan kendi geçerli Apple token'ı +
    # user_email=kurban ile kurbanın hesabını ele geçirir. Yalnızca Apple'ın İMZALADIĞI
    # ve doğrulanmış (email_verified) e-posta claim'i eşleştirmede kullanılır.
    _token_email = payload.get("email")
    _ev = str(payload.get("email_verified", "")).lower() in ("true", "1")
    verified_email = _token_email if (_token_email and _ev) else None

    user = await _upsert_social_user("apple", sub, verified_email or "", req.user_name or "")
    token = await _issue_jwt(user["id"], user["email"])
    # ID3-01 + KVKK: şifreli telefon sahibine açık döner; kimlik materyali ve mühür alanları ayıklanır.
    from .deps import open_user_phone as _open_phone
    return {"token": token, "user": public_self_user(_open_phone(dict(user)))}


# =============================================================================
# FACEBOOK
# =============================================================================

class FacebookLoginReq(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


@router.post("/facebook")
async def facebook_login(req: FacebookLoginReq):
    s = await _get_social_settings()
    if not s.get("facebook_enabled") or not s.get("facebook_app_id"):
        raise HTTPException(status_code=503, detail="Facebook Login admin tarafından etkinleştirilmedi")
    redirect_uri = req.redirect_uri or s.get("facebook_redirect_uri") or ""
    async with httpx.AsyncClient(timeout=15) as client:
        # Code → access_token
        r = await client.get(
            "https://graph.facebook.com/v23.0/oauth/access_token",
            params={
                "client_id": s["facebook_app_id"],
                "client_secret": s["facebook_app_secret"],
                "redirect_uri": redirect_uri,
                "code": req.code,
            },
        )
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Facebook token alınamadı: {r.text[:200]}")
        token_data = r.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="Access token yok")

        # access_token → user profile
        u = await client.get(
            "https://graph.facebook.com/v23.0/me",
            params={"fields": "id,name,email", "access_token": access_token},
        )
        if u.status_code != 200:
            raise HTTPException(status_code=401, detail=f"FB kullanıcı alınamadı: {u.text[:200]}")
        prof = u.json()

    fb_id = prof.get("id")
    email = prof.get("email")
    name = prof.get("name", "")
    if not fb_id:
        raise HTTPException(status_code=400, detail="FB user id yok")

    user = await _upsert_social_user("facebook", fb_id, email or "", name)
    token = await _issue_jwt(user["id"], user["email"])
    # ID3-01 + KVKK: şifreli telefon sahibine açık döner; kimlik materyali ve mühür alanları ayıklanır.
    from .deps import open_user_phone as _open_phone
    return {"token": token, "user": public_self_user(_open_phone(dict(user)))}
