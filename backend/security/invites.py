"""Staff invite flow (ADM-01).

A super admin invites a panel user (email + role [+ optional admin-managed SMS phone]).
A one-time token (only its sha256 is stored) bound to that email + role is e-mailed as a link
``<SITE_URL>/admin/davet#<token>`` (fragment → never sent to servers / Referer / access logs).
The invitee sets a strong password AND completes MFA (TOTP, or SMS to the admin-given phone)
before any session is issued. Invites expire after 48h; 10 wrong MFA codes lock the invite.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

INVITE_TTL_HOURS = 48
INVITE_MAX_ATTEMPTS = 10


def _db():
    from routes.deps import db
    return db


def hash_token(token: str) -> str:
    return hashlib.sha256(f"staffinv:{str(token or '').strip()}".encode()).hexdigest()


async def _site_url() -> str:
    """Panel taban URL'i: env ADMIN_SITE_URL → firma ayarı (storefront URL) → env SITE_URL."""
    env_admin = (os.environ.get("ADMIN_SITE_URL") or "").strip()
    if env_admin:
        return env_admin.rstrip("/")
    try:
        from company import get_site_url
        u = await get_site_url(_db())
        if u:
            return u.rstrip("/")
    except Exception:
        pass
    return (os.environ.get("SITE_URL") or "").rstrip("/")


def public_view(doc: dict) -> dict:
    d = dict(doc or {})
    for k in ("_id", "token_hash", "totp_secret_enc", "phone_enc"):
        d.pop(k, None)
    return d


def _mask_email(e: str) -> str:
    e = str(e or "").strip()
    if "@" not in e:
        return "***"
    n, _, dom = e.partition("@")
    return (n[:1] + "***@" + dom) if n else ("***@" + dom)


async def _send_invite_email(doc: dict, token: str) -> bool:
    try:
        import html as _h
        from email_layout import email_shell
        from email_smtp import send_smtp_email
        link = f"{await _site_url()}/admin/davet#{token}"
        exp = str(doc.get("expires_at") or "")[:16].replace("T", " ")
        html = email_shell(
            icon="🔐", eyebrow="YÖNETİM PANELİ", title="Yönetim paneline davet edildiniz",
            intro_html=(f"Merhaba {_h.escape(str(doc.get('first_name') or ''))},<br>"
                        f"<b>{_h.escape(str(doc.get('role_name') or doc.get('role_id') or ''))}</b> rolüyle "
                        "yönetim paneline davet edildiniz. Daveti kabul etmek için şifrenizi belirleyip "
                        "iki adımlı doğrulamayı (MFA) kurmanız gerekir."),
            cta_text="Daveti Kabul Et", cta_url=link, fallback_url=link,
            note_title="Güvenlik",
            note_html=(f"Bu bağlantı tek kullanımlıktır ve {_h.escape(exp)} UTC'ye kadar geçerlidir. "
                       "Daveti beklemiyorsanız bu e-postayı yok sayın ve yöneticinize haber verin."),
            preheader="Yönetim paneli daveti",
        )
        res = await send_smtp_email(_db(), doc["email"], "Yönetim paneli daveti", html)
        return bool((res or {}).get("success", True)) if isinstance(res, dict) else True
    except Exception as e:
        logger.warning("invite e-mail failed: %s", type(e).__name__)
        return False


async def create_invite(*, email: str, role_id: str, role_name: str, first_name: str = "",
                        last_name: str = "", phone_norm: str = "", created_by: Optional[dict] = None,
                        approval_id: str = "") -> dict:
    db = _db()
    email = str(email or "").strip().lower()
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")
    now = datetime.now(timezone.utc)
    # Aynı e-postaya bekleyen eski davetler geçersiz kılınır (tek geçerli davet).
    await db.staff_invites.update_many({"email": email, "status": "pending"},
                                       {"$set": {"status": "revoked", "revoked_at": now.isoformat(),
                                                 "revoke_reason": "superseded"}})
    token = secrets.token_urlsafe(32)
    doc = {
        "id": uuid.uuid4().hex,
        "token_hash": hash_token(token),
        "email": email,
        "role_id": role_id,
        "role_name": role_name,
        "first_name": str(first_name or "")[:80],
        "last_name": str(last_name or "")[:80],
        "mfa_method": "sms" if phone_norm else "totp",
        "user_id": str(uuid.uuid4()),
        "status": "pending",
        "attempts": 0,
        "created_by": {"id": str((created_by or {}).get("id") or ""),
                       "email": str((created_by or {}).get("email") or "")},
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=INVITE_TTL_HOURS)).isoformat(),
    }
    if approval_id:
        doc["approval_id"] = approval_id
    if phone_norm:
        from security.crypto import encrypt
        doc["phone_enc"] = encrypt(phone_norm)
        doc["phone_tail"] = phone_norm[-2:]
    await db.staff_invites.insert_one(dict(doc))
    sent = await _send_invite_email(doc, token)
    await db.staff_invites.update_one({"id": doc["id"]}, {"$set": {"email_sent": sent}})
    doc["email_sent"] = sent
    return public_view(doc)


async def resend_invite(invite_id: str) -> dict:
    db = _db()
    doc = await db.staff_invites.find_one({"id": str(invite_id or "")}, {"_id": 0})
    if not doc or doc.get("status") not in ("pending", "expired", "locked", "accepting"):
        raise HTTPException(status_code=404, detail="Yeniden gönderilebilecek davet bulunamadı")
    if await db.users.find_one({"email": doc["email"]}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Bu e-posta artık kayıtlı")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    upd = {"token_hash": hash_token(token), "status": "pending", "attempts": 0,
           "expires_at": (now + timedelta(hours=INVITE_TTL_HOURS)).isoformat(),
           "resent_at": now.isoformat()}
    await db.staff_invites.update_one({"id": doc["id"]}, {"$set": upd, "$unset": {"totp_secret_enc": ""}})
    doc.update(upd)
    sent = await _send_invite_email(doc, token)
    await db.staff_invites.update_one({"id": doc["id"]}, {"$set": {"email_sent": sent}})
    doc["email_sent"] = sent
    return public_view(doc)


async def load_invite(token: str) -> dict:
    """Valid, pending, unexpired invite for ``token`` — else 400/410 (no enumeration detail)."""
    tok = str(token or "").strip()
    if len(tok) < 20 or len(tok) > 200:
        raise HTTPException(status_code=400, detail="Davet bağlantısı geçersiz")
    h = hash_token(tok)
    doc = await _db().staff_invites.find_one({"token_hash": h}, {"_id": 0})
    if not doc or not hmac.compare_digest(str(doc.get("token_hash") or ""), h):
        raise HTTPException(status_code=400, detail="Davet bağlantısı geçersiz")
    if doc.get("status") != "pending":
        raise HTTPException(status_code=410, detail="Bu davet artık geçerli değil")
    try:
        exp = datetime.fromisoformat(str(doc.get("expires_at")))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        exp = None
    if not exp or exp < datetime.now(timezone.utc):
        await _db().staff_invites.update_one({"id": doc["id"], "status": "pending"},
                                             {"$set": {"status": "expired"}})
        raise HTTPException(status_code=410, detail="Davetin süresi dolmuş; yöneticinizden yeni davet isteyin")
    return doc


async def register_failed_attempt(doc: dict) -> None:
    db = _db()
    from pymongo import ReturnDocument
    r = await db.staff_invites.find_one_and_update(
        {"id": doc["id"], "status": "pending"}, {"$inc": {"attempts": 1}},
        projection={"_id": 0, "attempts": 1}, return_document=ReturnDocument.AFTER)
    if r and int(r.get("attempts") or 0) >= INVITE_MAX_ATTEMPTS:
        await db.staff_invites.update_one({"id": doc["id"], "status": "pending"},
                                          {"$set": {"status": "locked"}})


def masked(doc: dict) -> dict:
    return {
        "email_masked": _mask_email(doc.get("email")),
        "first_name": doc.get("first_name") or "",
        "role_name": doc.get("role_name") or "",
        "mfa_method": doc.get("mfa_method") or "totp",
        "phone_masked": ("***" + str(doc.get("phone_tail") or "")) if doc.get("phone_enc") else "",
        "expires_at": doc.get("expires_at"),
    }
