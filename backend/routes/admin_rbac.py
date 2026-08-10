"""
Role & user-management routes (RBAC).

Endpoints:
  GET    /api/admin/permissions          - permission tree + defaults
  GET    /api/admin/roles                - list roles
  POST   /api/admin/roles                - create role
  PUT    /api/admin/roles/{role_id}      - update role
  DELETE /api/admin/roles/{role_id}      - delete role (non-system only)
  GET    /api/admin/users                - list panel users
  POST   /api/admin/users                - create panel user
  PUT    /api/admin/users/{user_id}      - update user (role, status)
  DELETE /api/admin/users/{user_id}      - delete user
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
import uuid

from .deps import db, require_admin, require_super_admin, hash_password, validate_strong_password, get_effective_permissions
from permissions import PERMISSION_TREE, DEFAULT_ROLES, ALL_PERMISSION_KEYS

router = APIRouter(prefix="/admin", tags=["admin-rbac"])


# Panel personeli AYIRT EDİCİ işaretleri: create_panel_user personele created_by + role_id
# yazar; ayrıca is_super_admin ve varsayılan admin@facette.com personeldir. Bunlardan biri
# varsa GERÇEK personel; hiçbiri yoksa is_admin=True olsa bile MÜŞTERİdir (eski/bozuk register).
PANEL_STAFF_OR = [
    {"created_by": {"$exists": True, "$nin": [None, ""]}},
    {"is_super_admin": True},
    {"email": "admin@facette.com"},
    {"role_id": {"$exists": True, "$nin": [None, ""]}},
]


async def demote_customer_admins():
    """is_admin=True kalmış ama personel işareti taşımayan MÜŞTERİ hesaplarını admin'likten
    düşürür + oturumlarını iptal eder (token_version++). İdempotent; başlangıçta da çağrılır."""
    q = {"is_admin": True, "$nor": PANEL_STAFF_OR}
    affected = await db.users.find(q, {"_id": 0, "email": 1}).to_list(5000)
    if affected:
        await db.users.update_many(q, {"$set": {"is_admin": False}, "$inc": {"token_version": 1}})
    return {"demoted": len(affected), "emails": [u.get("email", "") for u in affected][:200]}


async def _ensure_default_roles():
    existing_ids = set()
    async for r in db.roles.find({}, {"_id": 0, "id": 1}):
        existing_ids.add(r["id"])
    for role in DEFAULT_ROLES:
        if role["id"] not in existing_ids:
            doc = dict(role)
            doc["created_at"] = datetime.now(timezone.utc).isoformat()
            await db.roles.insert_one(doc)


@router.get("/permissions")
async def get_permissions(current_user: dict = Depends(require_admin)):
    await _ensure_default_roles()
    return {
        "tree": PERMISSION_TREE,
        "all_keys": ALL_PERMISSION_KEYS,
    }


@router.get("/roles")
async def list_roles(current_user: dict = Depends(require_admin)):
    await _ensure_default_roles()
    roles = await db.roles.find({}, {"_id": 0}).to_list(500)
    return {"roles": roles}


@router.post("/roles")
async def create_role(payload: dict, current_user: dict = Depends(require_super_admin)):
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Rol adı boş olamaz")
    perms = payload.get("permissions") or []
    if not isinstance(perms, list):
        raise HTTPException(status_code=400, detail="Yetkiler liste olmalı")
    role_id = str(uuid.uuid4())
    doc = {
        "id": role_id,
        "name": name,
        "description": payload.get("description", ""),
        "permissions": perms,
        "is_system": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
    }
    await db.roles.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "role": doc}


@router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: dict, current_user: dict = Depends(require_super_admin)):
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol bulunamadı")
    # System roles: only permissions can be changed for non-super_admin; super_admin is frozen
    if role.get("is_system") and role.get("id") == "super_admin":
        raise HTTPException(status_code=400, detail="Süper Admin rolü değiştirilemez")

    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if "name" in payload and not role.get("is_system"):
        update["name"] = payload["name"].strip()
    if "description" in payload:
        update["description"] = payload["description"]
    if "permissions" in payload:
        if not isinstance(payload["permissions"], list):
            raise HTTPException(status_code=400, detail="Yetkiler liste olmalı")
        update["permissions"] = payload["permissions"]
    await db.roles.update_one({"id": role_id}, {"$set": update})
    return {"success": True}


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, current_user: dict = Depends(require_super_admin)):
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol bulunamadı")
    if role.get("is_system"):
        raise HTTPException(status_code=400, detail="Sistem rolleri silinemez")
    # Reassign users with this role to none
    await db.users.update_many({"role_id": role_id}, {"$unset": {"role_id": ""}})
    await db.roles.delete_one({"id": role_id})
    return {"success": True}


@router.get("/users")
async def list_panel_users(current_user: dict = Depends(require_admin)):
    # Yalnız GERÇEK panel personeli. Personel create_panel_user ile oluşur → created_by +
    # role_id taşır; ya da is_super_admin / varsayılan admin@facette.com. Bu işaretlerin
    # HİÇBİRİ olmayan is_admin=True hesap = eski/bozuk MÜŞTERİ (register kaydı) → gösterilmez.
    users = await db.users.find(
        {"is_admin": True, "$or": PANEL_STAFF_OR},
        {"_id": 0, "password": 0}
    ).to_list(500)
    return {"users": users}


@router.post("/users/cleanup-customer-admins")
async def cleanup_customer_admins(current_user: dict = Depends(require_super_admin)):
    """GÜVENLİK: is_admin=True kalmış MÜŞTERİ hesaplarını (personel işareti taşımayan)
    admin'likten düşürür + oturumlarını iptal eder → Üyeler listesine geçerler."""
    res = await demote_customer_admins()
    return {"success": True, **res}


@router.post("/users")
async def create_panel_user(payload: dict, current_user: dict = Depends(require_super_admin)):
    email = (payload or {}).get("email", "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="E-posta ve parola zorunlu")
    validate_strong_password(password, identifiers=[
        email, payload.get("first_name"), payload.get("last_name")])
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password": hash_password(password),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "role_id": payload.get("role_id", ""),
        "is_admin": True,
        "is_active": payload.get("is_active", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
    }
    # Telefon (giriş SMS'i) — admin belirler; kullanıcı değiştiremez (mfa_admin_managed).
    _ph = (payload.get("phone") or "").strip()
    if _ph:
        from notification_service import normalize_phone_tr
        from security.crypto import encrypt as _enc
        pn = normalize_phone_tr(_ph)
        if not pn or len(pn) < 12:
            raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası girin (05xx…).")
        doc["phone"] = pn
        doc["mfa_phone_enc"] = _enc(pn)
        doc["mfa_method"] = "sms"
        doc["mfa_enabled"] = True
        doc["mfa_admin_managed"] = True
    await db.users.insert_one(doc)
    doc.pop("_id", None)
    doc.pop("password", None)
    return {"success": True, "user": doc}


@router.put("/users/{user_id}")
async def update_panel_user(user_id: str, payload: dict, current_user: dict = Depends(require_super_admin)):
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    unset = {}
    for f in ("first_name", "last_name", "role_id", "is_active"):
        if f in payload:
            update[f] = payload[f]
    if payload.get("password"):
        validate_strong_password(payload["password"], identifiers=[
            user.get("email"), payload.get("first_name") or user.get("first_name"),
            payload.get("last_name") or user.get("last_name")])
        update["password"] = hash_password(payload["password"])
        update["password_changed_at"] = datetime.now(timezone.utc).isoformat()
    # TELEFON (giriş SMS'i): YALNIZ admin buradan belirler. Set edilince MFA telefonu
    # (mfa_phone_enc, öncelikli) admin-yönetimli olur → kullanıcı kendisi DEĞİŞTİREMEZ.
    if "phone" in payload:
        _ph = (payload.get("phone") or "").strip()
        if _ph:
            from notification_service import normalize_phone_tr
            from security.crypto import encrypt as _enc
            pn = normalize_phone_tr(_ph)
            if not pn or len(pn) < 12:
                raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası girin (05xx…).")
            update["phone"] = pn
            update["mfa_phone_enc"] = _enc(pn)
            update["mfa_method"] = "sms"
            update["mfa_enabled"] = True
            update["mfa_admin_managed"] = True
        else:
            # Boş bırakıldı → admin-yönetimli MFA telefonunu temizle.
            update["phone"] = ""
            update["mfa_admin_managed"] = False
            unset["mfa_phone_enc"] = ""
    _ops = {"$set": update}
    if unset:
        _ops["$unset"] = unset
    await db.users.update_one({"id": user_id}, _ops)
    return {"success": True}


@router.delete("/users/{user_id}")
async def delete_panel_user(user_id: str, current_user: dict = Depends(require_super_admin)):
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "email": 1})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    # Prevent self-delete
    if user.get("email") == current_user.get("email"):
        raise HTTPException(status_code=400, detail="Kendinizi silemezsiniz")
    await db.users.delete_one({"id": user_id})
    return {"success": True}


@router.get("/me/permissions")
async def get_my_permissions(current_user: dict = Depends(require_admin)):
    """Return effective permissions of the current user."""
    await _ensure_default_roles()
    # GÜVENLİK: süper-admin yalnızca e-posta VEYA is_super_admin bayrağı ile
    # (rolsüz kullanıcı artık otomatik ["*"] ALMAZ → deps.get_effective_permissions ile aynı).
    if current_user.get("email") == "admin@facette.com" or current_user.get("is_super_admin") is True:
        return {"permissions": ["*"], "role": "Süper Admin"}
    role_id = current_user.get("role_id") or ""
    if not role_id:
        # Rolsüz personel: tüm operasyonel yetkiler (süper-admin değil) — deps ile aynı.
        try:
            from permissions import ALL_PERMISSION_KEYS
            return {"permissions": list(ALL_PERMISSION_KEYS), "role": "Yönetici (rol atanmamış)"}
        except Exception:
            return {"permissions": [], "role": None}
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        return {"permissions": [], "role": None}
    return {"permissions": role.get("permissions", []), "role": role.get("name")}
