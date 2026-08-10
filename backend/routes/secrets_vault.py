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

from .deps import db, require_admin, write_audit_log, client_ip_from_request
from security.crypto import encrypt, decrypt
from security.redactor import mask_value

router = APIRouter(prefix="/admin/vault", tags=["secrets-vault"])


def _is_super_admin(user: dict) -> bool:
    """Super-admins can reveal raw secrets. Field is `is_super_admin` on the
    user document. The bootstrap admin (admin@facette.com) is implicit super."""
    if not user:
        return False
    if user.get("is_super_admin"):
        return True
    return user.get("email") == "admin@facette.com"


class SecretIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=120)
    value: str = Field(..., min_length=1, max_length=8192)
    description: Optional[str] = None
    scope: Optional[str] = "global"   # global | trendyol | iyzico | dhl | dogan | resend ...


@router.post("/secret")
async def upsert_secret(
    payload: SecretIn,
    request: Request,
    admin=Depends(require_admin),
):
    if not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="Sadece süper admin gizli değer yazabilir")

    enc = encrypt(payload.value)
    now = datetime.now(timezone.utc).isoformat()
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
    await write_audit_log(
        "vault_secret_write",
        user_id=admin.get("id"), email=admin.get("email"),
        ip=client_ip_from_request(request),
        success=True,
        meta={"key": payload.key, "scope": payload.scope},
    )
    return {"ok": True, "key": payload.key, "masked": mask_value(payload.value)}


@router.get("/secrets")
async def list_secrets(admin=Depends(require_admin)):
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
    plain = decrypt(doc.get("value_enc"))
    await write_audit_log(
        "vault_secret_reveal",
        user_id=admin.get("id"), email=admin.get("email"),
        ip=client_ip_from_request(request),
        success=True, meta={"key": key},
    )
    return {"key": key, "value": plain}


@router.delete("/secret/{key}")
async def delete_secret(
    key: str,
    request: Request,
    admin=Depends(require_admin),
):
    if not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="Sadece süper admin silebilir")
    res = await db.vault_secrets.delete_one({"key": key})
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
