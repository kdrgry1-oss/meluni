"""
Secrets Vault — encrypted credentials store.

Endpoints:
  POST /api/admin/vault/secret           upsert (super_admin)
  GET  /api/admin/vault/secrets          list (admin → masked, super_admin → masked too;
                                              raw value requires explicit /reveal)
  GET  /api/admin/vault/secret/{key}/reveal  (super_admin only — audited)
  DELETE /api/admin/vault/secret/{key}   (super_admin)

Schema (collection: `vault_secrets`):
  { key, value_enc, description, scope, updated_by, updated_at, created_at }
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .deps import db, require_admin, write_audit_log, client_ip_from_request, require_permission, require_super_admin
from security.crypto import encrypt, decrypt
from security.redactor import mask_value
from activity_audit import run_audited

router = APIRouter(prefix="/admin/vault", tags=["secrets-vault"])


def _vault_super_emails() -> set:
    """Acil durum (break-glass) listesi: env VAULT_SUPER_EMAILS (virgülle). Varsayılan BOŞ."""
    import os as _os
    return {e.strip().lower() for e in (_os.environ.get("VAULT_SUPER_EMAILS") or "").split(",") if e.strip()}


def _is_super_admin(user: dict) -> bool:
    """Super-admins can reveal raw secrets. Field is `is_super_admin` on the user document.
    GÜVENLİK (denetim 2026-10, #6): sabit bir e-posta adresine (bootstrap admin) örtük süper yetki
    VERİLMEZ — yalnız açık is_super_admin bayrağı (deps.get_effective_permissions ile aynı kural).
    Bootstrap admin zaten is_super_admin=True ile oluşturulur; eksikse Railway env
    OWNER_SUPER_ADMIN_EMAILS (açılışta bayrağı yazar) ya da yalnız kasa için VAULT_SUPER_EMAILS."""
    if not user:
        return False
    if user.get("is_super_admin") is True:
        return True
    _em = (user.get("email") or "").strip().lower()
    return bool(_em) and _em in _vault_super_emails()


class SecretIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=120)
    value: str = Field(..., min_length=1, max_length=8192)
    description: Optional[str] = None
    scope: Optional[str] = "global"   # global | trendyol | iyzico | dhl | dogan | resend ...


@router.post("/secret")
async def upsert_secret(
    payload: SecretIn,
    request: Request,
    admin=Depends(require_super_admin),
):
    if not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="Sadece süper admin gizli değer yazabilir")

    enc = encrypt(payload.value)
    now = datetime.now(timezone.utc).isoformat()
    _existed = bool(await db.vault_secrets.find_one({"key": payload.key}, {"_id": 1}))

    async def _perform():
        await db.vault_secrets.update_one(
            {"key": payload.key},
            {
                "$set": {
                    "key": payload.key,
                    "value_enc": enc,
                    "description": payload.description or "",
                    "scope": payload.scope or "global",
                    "updated_by": admin.get("email"),
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    # Fail-closed denetim (değer ASLA denetime yazılmaz; yalnız anahtar adı/kapsam).
    await run_audited(db, action="vault.secret_write", entity_type="vault_secret", entity_id=payload.key,
                      perform=_perform, current_user=admin, request=request, source="admin.vault",
                      metadata={"scope": payload.scope or "global", "replaced": _existed})
    await write_audit_log(
        "vault_secret_write",
        user_id=admin.get("id"), email=admin.get("email"),
        ip=client_ip_from_request(request),
        success=True,
        meta={"key": payload.key, "scope": payload.scope},
    )
    return {"ok": True, "key": payload.key, "masked": mask_value(payload.value)}


@router.get("/secrets")
async def list_secrets(admin=Depends(require_admin)):  # IAM-02: tasarımı gereği her personel (değerler MASKELİ)
    """List all secrets — values are MASKED for everyone (super_admin reveals
    individually via /reveal). This avoids accidental shoulder-surfing leaks."""
    items = []
    async for doc in db.vault_secrets.find({}, {"_id": 0}).sort("key", 1):
        plain = decrypt(doc.get("value_enc")) or ""
        items.append({
            "key": doc.get("key"),
            "masked_value": mask_value(plain),
            "is_set": bool(plain),
            "description": doc.get("description") or "",
            "scope": doc.get("scope") or "global",
            "updated_by": doc.get("updated_by"),
            "updated_at": doc.get("updated_at"),
        })
    return {"items": items, "can_reveal": _is_super_admin(admin)}


@router.get("/secret/{key}/reveal")
async def reveal_secret(
    key: str,
    request: Request,
    # IAM-02: bilinçli olarak her personel — gövde süper-admin değilse 403 verir VE reddi güvenlik
    # günlüğüne yazar (vault_secret_reveal_denied: içeriden yoklama sinyali). Bağımlılıkta reddedilseydi
    # bu iz kaybolurdu.
    admin=Depends(require_admin),
):
    if not _is_super_admin(admin):
        await write_audit_log(
            "vault_secret_reveal_denied",
            user_id=admin.get("id"), email=admin.get("email"),
            ip=client_ip_from_request(request),
            success=False, meta={"key": key},
        )
        raise HTTPException(status_code=403, detail="Sadece süper admin görüntüleyebilir")

    doc = await db.vault_secrets.find_one({"key": key}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Secret bulunamadı")
    # AUTH24: kurtarma koduyla girişten sonra (yeni faktör bağlanana kadar, ≤24 sa) kasa açılamaz.
    from .deps import assert_no_recovery_restriction
    await assert_no_recovery_restriction(admin, "vault.reveal")
    # PRIV07: toplu çözme alarmı — personel başına saatlik açık-değer sayacı (eşik aşılırsa 429 + kritik uyarı).
    from security.events import note_pii_reveal
    await note_pii_reveal(admin, "vault_secret", 1, request=request)

    async def _perform():
        return decrypt(doc.get("value_enc"))
    # FAIL-CLOSED: görüntüleme denetime yazılamazsa değer GÖSTERİLMEZ.
    plain = await run_audited(db, action="vault.secret_reveal", entity_type="vault_secret", entity_id=key,
                              perform=_perform, current_user=admin, request=request, source="admin.vault",
                              metadata={"scope": doc.get("scope") or "global"})
    await write_audit_log(
        "vault_secret_reveal",
        user_id=admin.get("id"), email=admin.get("email"),
        ip=client_ip_from_request(request),
        success=True, meta={"key": key},
    )
    try:
        from security.events import security_event
        await security_event("vault_reveal", admin, {"target": key, "secret_key": key,
                                                     "ip": client_ip_from_request(request), "fp": key},
                             request=request, audit=False)
    except Exception:
        pass
    return {"key": key, "value": plain}


@router.delete("/secret/{key}")
async def delete_secret(
    key: str,
    request: Request,
    admin=Depends(require_super_admin),
):
    if not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="Sadece süper admin silebilir")
    _out = {}

    async def _perform():
        _out["res"] = await db.vault_secrets.delete_one({"key": key})
    await run_audited(db, action="vault.secret_delete", entity_type="vault_secret", entity_id=key,
                      perform=_perform, current_user=admin, request=request, source="admin.vault")
    res = _out["res"]
    await write_audit_log(
        "vault_secret_delete",
        user_id=admin.get("id"), email=admin.get("email"),
        ip=client_ip_from_request(request),
        success=res.deleted_count > 0,
        meta={"key": key},
    )
    return {"ok": res.deleted_count > 0}


# Convenience helper for backend modules that need the actual secret value
async def get_secret(key: str) -> Optional[str]:
    """Used internally by integration modules. NEVER expose this in HTTP responses."""
    doc = await db.vault_secrets.find_one({"key": key}, {"_id": 0, "value_enc": 1})
    if not doc:
        return None
    return decrypt(doc.get("value_enc"))
