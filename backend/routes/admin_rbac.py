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
  POST   /api/admin/users/invite         - invite a panel user (ADM-01)
  GET    /api/admin/users/invites        - list invites
  POST   /api/admin/users/invites/{id}/resend | DELETE /api/admin/users/invites/{id}
  GET    /api/admin/approvals            - two-person approvals (BL-027)
  POST   /api/admin/approvals/{id}/approve | /reject

GÜVENLİK (2026-10):
  • Her RBAC değişikliği FAIL-CLOSED denetlenir (activity_audit.run_audited: önce "intent" kaydı,
    yazılamazsa işlem YAPILMAZ; sonra "done"/"failed").
  • '*' vermek / süper admin oluşturmak / bir süper adminin rolünü değiştirmek, pasifleştirmek
    ya da silmek İKİ KİŞİ kuralına tabidir (security.approvals). Tek aktif süper admin varsa
    işlem hemen uygulanır ama denetimde single_admin_exception=True işaretlenir + uyarı gider.
  • Yeni personel DAVETLE eklenir; doğrudan oluşturma yalnız env ALLOW_DIRECT_STAFF_CREATE=1.
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from datetime import datetime, timezone
import os
import uuid

from .deps import (require_permission, db, require_admin, require_super_admin, hash_password, validate_strong_password,
                   get_effective_permissions, verify_password, write_audit_log, security, is_recent_auth)
from permissions import PERMISSION_TREE, DEFAULT_ROLES, ALL_PERMISSION_KEYS
from activity_audit import run_audited
from security import approvals as _approvals

router = APIRouter(prefix="/admin", tags=["admin-rbac"])


# GÜVENLİK (denetim 2026-10, #3/#12): ayrı verilebilen yeni müşteri yetkileri. Kalıcı yeri
# permissions.py kataloğudur; o dosyada yoksa burada (idempotent) eklenir ki rol ekranında
# işaretlenebilsin. Süper admin ('*') zaten tümünü geçer.
_EXTRA_PERMISSIONS = {
    "customers": [
        {"key": "customers.export", "label": "Excel'e Aktar (toplu kişisel veri)"},
        {"key": "customers.credentials", "label": "Şifre / Telefon Değiştir"},
    ],
    # KVKK (sahip talebi): influencer Excel'lerinde TAM telefon/adres yalnız bu yetkiyle; yetkisiz
    # dışa aktarım çalışmaya devam eder ama telefon/adres sütunları maskeli gelir.
    "campaigns": [
        {"key": "campaigns.influencers_export", "label": "Influencer Excel'i — tam telefon/adres (kişisel veri)"},
    ],
}


def _ensure_extra_permissions() -> None:
    try:
        for grp in PERMISSION_TREE:
            extras = _EXTRA_PERMISSIONS.get(grp.get("key"))
            if not extras:
                continue
            children = grp.setdefault("children", [])
            have = {c.get("key") for c in children}
            for e in extras:
                if e["key"] not in have:
                    children.append(dict(e))
                if e["key"] not in ALL_PERMISSION_KEYS:
                    ALL_PERMISSION_KEYS.append(e["key"])
    except Exception:
        pass


_ensure_extra_permissions()


# Panel personeli AYIRT EDİCİ işaretleri: create_panel_user personele created_by + role_id
# yazar; ayrıca açık is_super_admin bayrağı personeli tanımlar. Bunlardan biri
# varsa GERÇEK personel; hiçbiri yoksa is_admin=True olsa bile MÜŞTERİdir (eski/bozuk register).
PANEL_STAFF_OR = [
    {"created_by": {"$exists": True, "$nin": [None, ""]}},
    {"is_super_admin": True},
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


async def grant_least_privilege_upgrade_permissions() -> dict:
    """IAM-02 TEK SEFERLİK geçiş (başlangıçta çağrılır; bayrak: settings.id=iam_least_privilege_v1).

    Önceden her personele açık (require_admin) uçlar artık sayfa/özellik izni istiyor. Bu iş, o uçlar
    için kullanılan yeni/uyuyan izinlerin TAMAMINI en az bir izni olan MEVCUT her role BİR KEZ ekler
    (permissions.upgrade_grants_for_existing_role) → bugün çalışan kimse erişim kaybetmez; daraltma
    süper adminin rol ekranındaki kararıdır. Yalnız EKLER ($addToSet; hiçbir izin silinmez), süper admin ('*') rolüne dokunmaz,
    önceden zorunlu olan izinleri (PRE_ENFORCED_PERMISSIONS) asla eklemez. Bayrak yazıldıktan sonra
    tekrar çalışmaz: geçişten sonra süper adminin rol ekranında kaldırdığı izin GERİ EKLENMEZ
    (kullanıcının yaptığı değişiklik kalıcıdır). Eşzamanlı iki açılışta da güvenli (idempotent)."""
    from permissions import upgrade_grants_for_existing_role, UPGRADE_GRANTS_FLAG
    flag = await db.settings.find_one({"id": UPGRADE_GRANTS_FLAG}, {"_id": 0, "done": 1})
    if (flag or {}).get("done"):
        return {"skipped": True}
    await _ensure_default_roles()
    changed = []
    async for role in db.roles.find({}, {"_id": 0, "id": 1, "permissions": 1}):
        add = upgrade_grants_for_existing_role(role.get("permissions") or [])
        if not add:
            continue
        await db.roles.update_one({"id": role["id"]}, {"$addToSet": {"permissions": {"$each": add}}})
        changed.append({"role_id": role["id"], "added": add})
    await db.settings.update_one(
        {"id": UPGRADE_GRANTS_FLAG},
        {"$set": {"id": UPGRADE_GRANTS_FLAG, "done": True, "at": datetime.now(timezone.utc).isoformat(),
                  "roles": changed}},
        upsert=True)
    return {"skipped": False, "roles": changed}


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
async def get_permissions(current_user: dict = Depends(require_permission("settings.view"))):
    await _ensure_default_roles()
    return {
        "tree": PERMISSION_TREE,
        "all_keys": ALL_PERMISSION_KEYS,
    }


@router.get("/roles")
async def list_roles(current_user: dict = Depends(require_permission("settings.view"))):
    await _ensure_default_roles()
    roles = await db.roles.find({}, {"_id": 0}).to_list(500)
    return {"roles": roles}


# ───────────────────────────── yardımcılar ─────────────────────────────

async def _no_recovery(current_user: dict, action: str) -> None:
    """AUTH24: kurtarma koduyla girişten sonra (yeni faktör bağlanana kadar, ≤24 sa) rol/yetki/personel
    değişikliği ve onay verme kapalıdır."""
    from .deps import assert_no_recovery_restriction
    await assert_no_recovery_restriction(current_user, action)

def _pending(decision: dict) -> JSONResponse:
    return JSONResponse(status_code=202, content=_approvals.pending_response(decision))


def _role_public(role: dict) -> dict:
    r = role or {}
    perms = r.get("permissions") or []
    return {"id": r.get("id"), "name": r.get("name"), "is_system": bool(r.get("is_system")),
            "grants_all": "*" in perms, "permission_count": len(perms),
            "permissions": perms[:200]}


def _user_public(u: dict) -> dict:
    u = u or {}
    return {"id": u.get("id"), "role_id": u.get("role_id"), "is_active": u.get("is_active"),
            "is_super_admin": bool(u.get("is_super_admin")),
            "first_name_set": bool(u.get("first_name")), "last_name_set": bool(u.get("last_name")),
            "mfa_admin_managed": bool(u.get("mfa_admin_managed"))}


def _flag_meta(single: bool, approval_id: str = "", approver: dict = None, extra: dict = None) -> dict:
    m = dict(extra or {})
    if single:
        m["single_admin_exception"] = True
    if approval_id:
        m["approval_id"] = approval_id
        m["approved_by"] = (approver or {}).get("id")
    return m


async def _role_grants_all(role_id: str) -> bool:
    if not role_id:
        return False
    r = await db.roles.find_one({"id": role_id}, {"_id": 0, "permissions": 1})
    return bool(r and "*" in (r.get("permissions") or []))


async def _user_is_super(u: dict) -> bool:
    return bool(u and (u.get("is_super_admin") is True or await _role_grants_all(u.get("role_id") or "")))


def _clean_perms(perms) -> list:
    if not isinstance(perms, list):
        raise HTTPException(status_code=400, detail="Yetkiler liste olmalı")
    out = []
    for p in perms:
        p = str(p or "").strip()[:120]
        if p and p not in out:
            out.append(p)
    return out


async def _star_alert(actor: dict, request, what: str, target: str) -> None:
    try:
        from security.events import security_event
        await security_event("role_grant_star", actor, {"what": what, "target": target, "fp": target},
                             request=request, audit=False)
    except Exception:
        pass


# ───────────────────────────── ROLLER ─────────────────────────────

async def _do_role_create(doc: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    async def _perform():
        await db.roles.insert_one(dict(doc))
    await run_audited(db, action="rbac.role_create", entity_type="role", entity_id=doc["id"], perform=_perform,
                      after=_role_public(doc), current_user=approver or actor, request=request,
                      source="admin.rbac", metadata=_flag_meta(single, approval_id, approver,
                                                                {"requested_by": actor.get("id")}))
    if "*" in (doc.get("permissions") or []):
        await _star_alert(approver or actor, request, "'*' yetkili rol oluşturuldu", doc.get("name") or doc["id"])
    out = dict(doc)
    out.pop("_id", None)
    return {"success": True, "role": out}


@router.post("/roles")
async def create_role(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.role_create")
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Rol adı boş olamaz")
    perms = _clean_perms((payload or {}).get("permissions") or [])
    doc = {
        "id": str(uuid.uuid4()),
        "name": name[:120],
        "description": str(payload.get("description") or "")[:500],
        "permissions": perms,
        "is_system": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
    }
    single = False
    if "*" in perms:
        decision = await _approvals.gate("rbac.role_create", params={"doc": doc},
                                         summary=f"Tüm yetkili ('*') rol oluşturma: {doc['name']}",
                                         current_user=current_user, request=request,
                                         entity_type="role", entity_id=doc["id"])
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_role_create(doc, current_user, request, single=single)


async def _exec_role_create(params, requester, approver, request, approval_id=""):
    doc = dict(params.get("doc") or {})
    if not doc.get("id") or await db.roles.find_one({"id": doc["id"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Rol zaten mevcut")
    res = await _do_role_create(doc, requester, request, approval_id=approval_id, approver=approver)
    return {"role_id": res["role"]["id"]}


async def _do_role_update(role_id: str, before: dict, update: dict, actor: dict, request, *,
                          single=False, approval_id="", approver=None) -> dict:
    async def _perform():
        await db.roles.update_one({"id": role_id}, {"$set": update})
    after = {**before, **update}
    await run_audited(db, action="rbac.role_update", entity_type="role", entity_id=role_id, perform=_perform,
                      before=_role_public(before), after=_role_public(after), current_user=approver or actor,
                      request=request, source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver, {"requested_by": actor.get("id")}))
    if "*" in (update.get("permissions") or []) and "*" not in (before.get("permissions") or []):
        await _star_alert(approver or actor, request, "Role '*' yetkisi verildi", before.get("name") or role_id)
    return {"success": True}


@router.put("/roles/{role_id}")
async def update_role(role_id: str, payload: dict, request: Request,
                      current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.role_update")
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol bulunamadı")
    # System roles: only permissions can be changed for non-super_admin; super_admin is frozen
    if role.get("is_system") and role.get("id") == "super_admin":
        raise HTTPException(status_code=400, detail="Süper Admin rolü değiştirilemez")

    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if "name" in payload and not role.get("is_system"):
        update["name"] = str(payload["name"] or "").strip()[:120]
    if "description" in payload:
        update["description"] = str(payload["description"] or "")[:500]
    if "permissions" in payload:
        update["permissions"] = _clean_perms(payload["permissions"])
    old_star = "*" in (role.get("permissions") or [])
    new_star = "*" in (update.get("permissions", role.get("permissions")) or [])
    perms_changed = "permissions" in update and sorted(update["permissions"]) != sorted(role.get("permissions") or [])
    single = False
    if perms_changed and (old_star or new_star):
        what = "'*' yetkisi veriliyor" if new_star and not old_star else "Tüm yetkili rolün yetkileri değişiyor"
        decision = await _approvals.gate("rbac.role_update", params={"role_id": role_id, "update": update},
                                         summary=f"{what}: {role.get('name') or role_id}",
                                         current_user=current_user, request=request,
                                         entity_type="role", entity_id=role_id)
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_role_update(role_id, role, update, current_user, request, single=single)


async def _exec_role_update(params, requester, approver, request, approval_id=""):
    role_id = str(params.get("role_id") or "")
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol artık mevcut değil")
    if role.get("is_system") and role.get("id") == "super_admin":
        raise HTTPException(status_code=400, detail="Süper Admin rolü değiştirilemez")
    await _do_role_update(role_id, role, dict(params.get("update") or {}), requester, request,
                          approval_id=approval_id, approver=approver)
    return {"role_id": role_id}


async def _do_role_delete(role: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    role_id = role["id"]
    affected = await db.users.count_documents({"role_id": role_id})

    async def _perform():
        # Reassign users with this role to none
        await db.users.update_many({"role_id": role_id}, {"$unset": {"role_id": ""}})
        await db.roles.delete_one({"id": role_id})
    await run_audited(db, action="rbac.role_delete", entity_type="role", entity_id=role_id, perform=_perform,
                      before=_role_public(role), current_user=approver or actor, request=request,
                      source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver,
                                          {"users_unassigned": affected, "requested_by": actor.get("id")}))
    return {"success": True}


_AUTOGRANT_REVIEW_ID = "iam_autogrant_review"


async def _autogrant_review_items() -> list:
    """Denetim #3: IAM-02 geçişinin rollere OTOMATİK eklediği ve hâlâ rolde duran HASSAS izinler.
    Süper admin bunları gözden geçirip kaldırır ya da bilinçli olarak korur (kalıcı karar)."""
    from permissions import SENSITIVE_NO_AUTO_GRANT, UPGRADE_GRANTS_FLAG
    flag = await db.settings.find_one({"id": UPGRADE_GRANTS_FLAG}, {"_id": 0, "roles": 1}) or {}
    review = await db.settings.find_one({"id": _AUTOGRANT_REVIEW_ID}, {"_id": 0, "kept": 1}) or {}
    kept = review.get("kept") or {}
    out = []
    for rec in flag.get("roles") or []:
        rid = str((rec or {}).get("role_id") or "")
        added = set((rec or {}).get("added") or []) & SENSITIVE_NO_AUTO_GRANT
        if not rid or not added:
            continue
        role = await db.roles.find_one({"id": rid}, {"_id": 0, "id": 1, "name": 1, "permissions": 1})
        if not role:
            continue
        still = sorted(added & set(role.get("permissions") or []) - set(kept.get(rid) or []))
        if still:
            out.append({"role_id": rid, "role_name": role.get("name") or rid, "permissions": still})
    return out


@router.get("/roles/autogrant-review")
async def get_autogrant_review(current_user: dict = Depends(require_super_admin)):
    return {"items": await _autogrant_review_items()}


@router.post("/roles/autogrant-review")
async def resolve_autogrant_review(payload: dict, request: Request,
                                   current_user: dict = Depends(require_super_admin)):
    """Body: {"role_id": "...", "action": "revoke"|"keep", "permissions": [...]}
    revoke → izinler rolden çıkarılır (denetim kaydıyla); keep → bilinçli olarak korunur, bir daha sorulmaz.
    Yalnız inceleme listesindeki izinler işlenir (başka izne dokunulmaz)."""
    await _no_recovery(current_user, "rbac.role_update")
    p = payload or {}
    rid = str(p.get("role_id") or "")
    action = str(p.get("action") or "")
    if action not in ("revoke", "keep"):
        raise HTTPException(status_code=400, detail="Geçersiz işlem")
    pending = {i["role_id"]: set(i["permissions"]) for i in await _autogrant_review_items()}
    perms = sorted(set(p.get("permissions") or []) & pending.get(rid, set()))
    if not perms:
        raise HTTPException(status_code=400, detail="İncelenecek izin bulunamadı")
    if action == "keep":
        await db.settings.update_one({"id": _AUTOGRANT_REVIEW_ID},
                                     {"$set": {"id": _AUTOGRANT_REVIEW_ID},
                                      "$addToSet": {f"kept.{rid}": {"$each": perms}}}, upsert=True)
        return {"success": True, "kept": perms}
    role = await db.roles.find_one({"id": rid}, {"_id": 0})
    if not role or "*" in (role.get("permissions") or []):
        raise HTTPException(status_code=400, detail="Rol işlenemez")
    new_perms = [x for x in (role.get("permissions") or []) if x not in set(perms)]
    update = {"permissions": new_perms, "updated_at": datetime.now(timezone.utc).isoformat()}
    await _do_role_update(rid, role, update, current_user, request)
    return {"success": True, "revoked": perms}


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, request: Request, current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.role_delete")
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol bulunamadı")
    if role.get("is_system"):
        raise HTTPException(status_code=400, detail="Sistem rolleri silinemez")
    single = False
    if "*" in (role.get("permissions") or []):
        decision = await _approvals.gate("rbac.role_delete", params={"role_id": role_id},
                                         summary=f"Tüm yetkili rolü silme: {role.get('name') or role_id}",
                                         current_user=current_user, request=request,
                                         entity_type="role", entity_id=role_id)
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_role_delete(role, current_user, request, single=single)


async def _exec_role_delete(params, requester, approver, request, approval_id=""):
    role = await db.roles.find_one({"id": str(params.get("role_id") or "")}, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Rol artık mevcut değil")
    if role.get("is_system"):
        raise HTTPException(status_code=400, detail="Sistem rolleri silinemez")
    await _do_role_delete(role, requester, request, approval_id=approval_id, approver=approver)
    return {"role_id": role["id"]}


_STAFF_DIRECTORY_PROJECTION = {"_id": 0, "id": 1, "email": 1, "first_name": 1, "last_name": 1, "phone": 1,
                               "role_id": 1, "is_active": 1, "is_admin": 1, "is_super_admin": 1,
                               "created_at": 1}


@router.get("/users")
async def list_panel_users(current_user: dict = Depends(require_admin)):
    # Yalnız GERÇEK panel personeli. Personel create_panel_user ile oluşur → created_by +
    # role_id taşır; ya da is_super_admin. Bu işaretlerin
    # HİÇBİRİ olmayan is_admin=True hesap = eski/bozuk MÜŞTERİ (register kaydı) → gösterilmez.
    # Denetim #16: AÇIK izin listesi — kimlik bilgisi / MFA / kilit sayaçları / oturum meta verisi
    # (yeni eklenen alanlar dahil) personel dizininde ASLA dönmez.
    users = await db.users.find({"is_admin": True, "$or": PANEL_STAFF_OR}, _STAFF_DIRECTORY_PROJECTION).to_list(500)
    return {"users": users}


@router.post("/users/cleanup-customer-admins")
async def cleanup_customer_admins(request: Request, current_user: dict = Depends(require_super_admin)):
    """GÜVENLİK: is_admin=True kalmış MÜŞTERİ hesaplarını (personel işareti taşımayan)
    admin'likten düşürür + oturumlarını iptal eder → Üyeler listesine geçerler."""
    out = {}

    async def _perform():
        out.update(await demote_customer_admins())
    await run_audited(db, action="rbac.cleanup_customer_admins", entity_type="panel_user", entity_id="*",
                      perform=_perform, current_user=current_user, request=request, source="admin.rbac")
    return {"success": True, **out}


# ───────────────────────────── PERSONEL ─────────────────────────────

def _direct_create_allowed() -> bool:
    return (os.environ.get("ALLOW_DIRECT_STAFF_CREATE") or "").strip().lower() in ("1", "on", "true", "yes")


async def _do_user_create(doc: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    if await db.users.find_one({"email": doc["email"]}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")

    async def _perform():
        await db.users.insert_one(dict(doc))
    await run_audited(db, action="rbac.user_create", entity_type="panel_user", entity_id=doc["id"],
                      perform=_perform, after=_user_public(doc), current_user=approver or actor, request=request,
                      source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver,
                                          {"direct_create": True, "requested_by": actor.get("id")}))
    if await _role_grants_all(doc.get("role_id") or ""):
        await _star_alert(approver or actor, request, "Süper admin (tüm yetkili) personel oluşturuldu", doc["id"])
    out = dict(doc)
    out.pop("_id", None)
    out.pop("password", None)
    out.pop("mfa_phone_enc", None)
    return {"success": True, "user": out}


@router.post("/users")
async def create_panel_user(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    """DOĞRUDAN oluşturma — yalnız env ALLOW_DIRECT_STAFF_CREATE=1 iken (acil durum). Normal yol: davet."""
    await _no_recovery(current_user, "rbac.user_create")
    if not _direct_create_allowed():
        raise HTTPException(status_code=403,
                            detail="Yeni personel davet ile eklenir (Kullanıcılar & Roller → Personel Davet Et).")
    email = str((payload or {}).get("email") or "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="E-posta ve parola zorunlu")
    validate_strong_password(password, identifiers=[
        email, payload.get("first_name"), payload.get("last_name")])
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")
    role_id = str(payload.get("role_id") or "")
    if role_id and not await db.roles.find_one({"id": role_id}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Rol bulunamadı")
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password": hash_password(password),
        "password_changed_at": datetime.now(timezone.utc).isoformat(),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "role_id": role_id,
        "is_admin": True,
        "is_active": payload.get("is_active", True) is not False,
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
    single = False
    if await _role_grants_all(role_id):
        decision = await _approvals.gate("rbac.user_create", params={"doc": doc},
                                         summary=f"Süper admin (tüm yetkili) personel oluşturma: {email}",
                                         current_user=current_user, request=request,
                                         entity_type="panel_user", entity_id=doc["id"])
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_user_create(doc, current_user, request, single=single)


async def _exec_user_create(params, requester, approver, request, approval_id=""):
    doc = dict(params.get("doc") or {})
    res = await _do_user_create(doc, requester, request, approval_id=approval_id, approver=approver)
    return {"user_id": res["user"]["id"]}


# ── DAVET (ADM-01) ──
async def _do_invite(params: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    from security.invites import create_invite
    out = {}

    async def _perform():
        out["invite"] = await create_invite(
            email=params["email"], role_id=params["role_id"], role_name=params.get("role_name", ""),
            first_name=params.get("first_name", ""), last_name=params.get("last_name", ""),
            phone_norm=params.get("phone_norm", ""), created_by=actor, approval_id=approval_id)
    await run_audited(db, action="rbac.invite_create", entity_type="staff_invite", entity_id=params["email_hash"],
                      perform=_perform, after={"role_id": params["role_id"],
                                               "mfa_method": "sms" if params.get("phone_norm") else "totp"},
                      current_user=approver or actor, request=request, source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver, {"requested_by": actor.get("id")}))
    if params.get("grants_all"):
        await _star_alert(approver or actor, request, "Süper admin (tüm yetkili) rolüyle personel davet edildi",
                          params["email_hash"])
    return {"success": True, "invite": out.get("invite")}


@router.post("/users/invite")
async def invite_panel_user(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.invite_create")
    import hashlib as _hl
    from .deps import is_safe_email
    email = str((payload or {}).get("email") or "").strip().lower()
    if not is_safe_email(email):
        raise HTTPException(status_code=400, detail="Geçerli bir e-posta girin")
    role_id = str((payload or {}).get("role_id") or "").strip()
    role = await db.roles.find_one({"id": role_id}, {"_id": 0, "id": 1, "name": 1, "permissions": 1}) if role_id else None
    if not role:
        raise HTTPException(status_code=400, detail="Geçerli bir rol seçin")
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayıtlı")
    phone_norm = ""
    _ph = str((payload or {}).get("phone") or "").strip()
    if _ph:
        from notification_service import normalize_phone_tr
        phone_norm = normalize_phone_tr(_ph)
        if not (len(phone_norm) == 12 and phone_norm.startswith("905") and phone_norm.isdigit()):
            raise HTTPException(status_code=400, detail="Geçerli bir Türkiye cep telefonu girin (05xx…).")
    grants_all = "*" in (role.get("permissions") or [])
    params = {"email": email, "email_hash": _hl.sha256(email.encode()).hexdigest()[:16],
              "role_id": role["id"], "role_name": role.get("name") or "",
              "first_name": str(payload.get("first_name") or "").strip()[:80],
              "last_name": str(payload.get("last_name") or "").strip()[:80],
              "phone_norm": phone_norm, "grants_all": grants_all}
    single = False
    if grants_all:
        decision = await _approvals.gate("rbac.invite_create", params=params,
                                         summary=f"Süper admin (tüm yetkili) rolüyle davet: {email}",
                                         current_user=current_user, request=request,
                                         entity_type="staff_invite", entity_id=params["email_hash"])
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_invite(params, current_user, request, single=single)


async def _exec_invite(params, requester, approver, request, approval_id=""):
    res = await _do_invite(dict(params), requester, request, approval_id=approval_id, approver=approver)
    return {"invite_id": (res.get("invite") or {}).get("id", "")}


@router.get("/users/invites")
async def list_invites(current_user: dict = Depends(require_super_admin)):
    from security.invites import public_view
    rows = await db.staff_invites.find({}, {"_id": 0, "token_hash": 0, "totp_secret_enc": 0, "phone_enc": 0}) \
        .sort("created_at", -1).to_list(100)
    now_iso = datetime.now(timezone.utc).isoformat()
    for r in rows:
        if r.get("status") == "pending" and str(r.get("expires_at") or "") < now_iso:
            r["status"] = "expired"
    return {"invites": [public_view(r) for r in rows],
            "direct_create_allowed": _direct_create_allowed()}


@router.post("/users/invites/{invite_id}/resend")
async def resend_invite(invite_id: str, request: Request, current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.invite_resend")
    from security.invites import resend_invite as _resend
    doc = await db.staff_invites.find_one({"id": invite_id}, {"_id": 0, "role_id": 1, "email": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Davet bulunamadı")
    if await _role_grants_all(doc.get("role_id") or ""):
        # Süper admin daveti yeniden göndermek = yeni geçerli token → iki kişi kuralı.
        decision = await _approvals.gate("rbac.invite_resend", params={"invite_id": invite_id},
                                         summary="Süper admin davetini yeniden gönderme",
                                         current_user=current_user, request=request,
                                         entity_type="staff_invite", entity_id=invite_id)
        if decision["mode"] == "pending":
            return _pending(decision)
    out = {}

    async def _perform():
        out["invite"] = await _resend(invite_id)
    await run_audited(db, action="rbac.invite_resend", entity_type="staff_invite", entity_id=invite_id,
                      perform=_perform, current_user=current_user, request=request, source="admin.rbac")
    return {"success": True, "invite": out.get("invite")}


async def _exec_invite_resend(params, requester, approver, request, approval_id=""):
    from security.invites import resend_invite as _resend
    out = {}

    async def _perform():
        out["invite"] = await _resend(str(params.get("invite_id") or ""))
    await run_audited(db, action="rbac.invite_resend", entity_type="staff_invite",
                      entity_id=str(params.get("invite_id") or ""), perform=_perform, current_user=approver,
                      request=request, source="admin.rbac",
                      metadata={"approval_id": approval_id, "requested_by": (requester or {}).get("id")})
    return {"invite_id": (out.get("invite") or {}).get("id", "")}


@router.delete("/users/invites/{invite_id}")
async def revoke_invite(invite_id: str, request: Request, current_user: dict = Depends(require_super_admin)):
    doc = await db.staff_invites.find_one({"id": invite_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Davet bulunamadı")

    async def _perform():
        await db.staff_invites.update_one(
            {"id": invite_id, "status": {"$in": ["pending", "expired", "locked"]}},
            {"$set": {"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat(),
                      "revoked_by": current_user.get("id")}, "$unset": {"token_hash": "", "totp_secret_enc": ""}})
    await run_audited(db, action="rbac.invite_revoke", entity_type="staff_invite", entity_id=invite_id,
                      perform=_perform, before={"status": doc.get("status")}, after={"status": "revoked"},
                      current_user=current_user, request=request, source="admin.rbac")
    return {"success": True}


async def _do_user_update(user_id: str, before: dict, ops: dict, actor: dict, request, *, flags: dict,
                          single=False, approval_id="", approver=None) -> dict:
    _set = dict(ops.get("set") or {})
    _unset = dict(ops.get("unset") or {})
    _inc_tv = bool(ops.get("inc_tv"))
    mongo_ops = {"$set": _set}
    if _unset:
        mongo_ops["$unset"] = _unset
    if _inc_tv:
        mongo_ops["$inc"] = {"token_version": 1}

    async def _perform():
        res = await db.users.update_one({"id": user_id}, mongo_ops)
        if not getattr(res, "matched_count", 1):
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
        if flags.get("mfa_phone") or flags.get("password"):
            # AUTH09: hedef telefon/şifre değişti → o kişiye daha önce gönderilmiş, henüz kullanılmamış
            # giriş kodları da geçersiz (yeni numara eski kodla doğrulanamaz).
            await db.mfa_sms_codes.update_many({"user_id": user_id, "used": False},
                                               {"$set": {"used": True, "invalidated": "factor_changed"}})
    after = {**before, **_set}
    for k in _unset:
        after.pop(k, None)
    await run_audited(db, action="rbac.user_update", entity_type="panel_user", entity_id=user_id, perform=_perform,
                      before=_user_public(before), after=_user_public(after), current_user=approver or actor,
                      request=request, source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver, {**flags, "requested_by": actor.get("id")}))
    if flags.get("mfa_phone"):
        try:
            from security.events import security_event
            await security_event("mfa_reset_by_admin", approver or actor,
                                 {"target": user_id, "cleared": bool(flags.get("mfa_phone_cleared")),
                                  "self": bool(flags.get("self")), "fp": user_id},
                                 request=request, audit=False)
        except Exception:
            pass
    if flags.get("grants_all_new"):
        await _star_alert(approver or actor, request, "Personele tüm yetkili rol atandı", user_id)
    if flags.get("password") or flags.get("mfa_phone"):
        await write_audit_log(
            "panel_user_credentials_change", user_id=(approver or actor).get("id"),
            email=(approver or actor).get("email"), success=True,
            meta={"target_user_id": user_id, "self": bool(flags.get("self")),
                  "password": bool(flags.get("password")), "mfa_phone": bool(flags.get("mfa_phone")),
                  "mfa_phone_cleared": bool(flags.get("mfa_phone_cleared"))})
    return {"success": True}


@router.put("/users/{user_id}")
async def update_panel_user(user_id: str, payload: dict, request: Request,
                            current_user: dict = Depends(require_super_admin)):
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    update = {"updated_at": datetime.now(timezone.utc).isoformat()}
    unset = {}
    _bump_tv = False
    _is_self = user.get("id") == current_user.get("id")
    # TELEFON değişikliği mi? (form numarayı her kayıtta gönderir — aynı numara değişiklik sayılmaz)
    _phone_change = False
    _pn_new = ""
    if "phone" in payload:
        from notification_service import normalize_phone_tr
        _ph_raw = (payload.get("phone") or "").strip()
        _pn_new = normalize_phone_tr(_ph_raw) if _ph_raw else ""
        _pn_old = normalize_phone_tr(user.get("phone") or "") if user.get("phone") else ""
        _phone_change = _pn_new[-10:] != _pn_old[-10:]
    # GÜVENLİK (denetim 2026-10, #5): KENDİ giriş-doğrulama (MFA) telefonunu buradan değiştirmek
    # yeniden kimlik doğrulaması (mevcut şifre) ister — ele geçirilmiş süper-admin oturumu MFA
    # telefonunu saldırgan numarasına çeviremesin.
    if _is_self and _phone_change:
        _cur_pw = str(payload.get("current_password") or "")
        _me = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "password": 1}) or {}
        if not _cur_pw or not verify_password(_cur_pw, _me.get("password", "")):
            await write_audit_log("panel_user_mfa_phone_change", user_id=current_user.get("id"),
                                  email=current_user.get("email"), success=False,
                                  meta={"target_user_id": user_id, "self": True, "reason": "reauth_failed"})
            raise HTTPException(status_code=403,
                                detail="Kendi giriş doğrulama telefonunuzu değiştirmek için mevcut şifrenizi girin.")
    for f in ("first_name", "last_name"):
        if f in payload:
            update[f] = str(payload[f] or "")[:80]
    if "role_id" in payload:
        _rid = str(payload.get("role_id") or "")
        if _rid and not await db.roles.find_one({"id": _rid}, {"_id": 1}):
            raise HTTPException(status_code=400, detail="Rol bulunamadı")
        update["role_id"] = _rid
    if "is_active" in payload:
        update["is_active"] = payload.get("is_active") is not False
    if _is_self and update.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Kendinizi pasifleştiremezsiniz")
    if payload.get("password"):
        validate_strong_password(payload["password"], identifiers=[
            user.get("email"), payload.get("first_name") or user.get("first_name"),
            payload.get("last_name") or user.get("last_name")])
        update["password"] = hash_password(payload["password"])
        update["password_changed_at"] = datetime.now(timezone.utc).isoformat()
        # DENETİM (SESN-26): admin şifre sıfırlarsa token_version artır → çalınmış/eski
        # JWT'ler geçersizleşir. Aksi halde ele geçirilmiş oturum 7 gün canlı kalırdı.
        _bump_tv = True
    # TELEFON (giriş SMS'i): YALNIZ admin buradan belirler. Set edilince MFA telefonu
    # (mfa_phone_enc, öncelikli) admin-yönetimli olur → kullanıcı kendisi DEĞİŞTİREMEZ.
    # Numara DEĞİŞMEDİYSE (form aynı değeri geri gönderdi) MFA telefonuna dokunulmaz — eskiden boş
    # gönderilen alan, setup-sms ile kurulmuş MFA telefonunu sessizce siliyordu.
    if _phone_change:
        if _pn_new:
            from security.crypto import encrypt as _enc
            pn = _pn_new
            if not pn or len(pn) < 12:
                raise HTTPException(status_code=400, detail="Geçerli bir telefon numarası girin (05xx…).")
            update["phone"] = pn
            update["phone_verified"] = True
            update["mfa_phone_enc"] = _enc(pn)
            update["mfa_method"] = "sms"
            update["mfa_enabled"] = True
            update["mfa_admin_managed"] = True
            update["mfa_factor_enrolled_at"] = datetime.now(timezone.utc).isoformat()
        else:
            # Boş bırakıldı → admin-yönetimli MFA telefonunu temizle.
            update["phone"] = ""
            update["mfa_admin_managed"] = False
            unset["mfa_phone_enc"] = ""
    if _bump_tv:
        # Süper admin şifreyi belirlediyse günlük MFA hata kilidi de kalkar (personel kilitte kalmasın).
        unset["mfa_day_fail_count"] = ""
        unset["mfa_day_window"] = ""
    # Pasifleştirme / rol değişimi / MFA telefonu değişimi diğer oturumları da düşürür.
    if update.get("is_active") is False and user.get("is_active") is not False:
        _bump_tv = True
    if _phone_change and not _is_self:
        _bump_tv = True  # başkasının MFA telefonu değişti → o kişinin açık oturumları düşer
    ops = {"set": update, "unset": unset, "inc_tv": _bump_tv}
    target_super = await _user_is_super(user)
    role_changed = "role_id" in update and update["role_id"] != (user.get("role_id") or "")
    deactivating = update.get("is_active") is False and user.get("is_active") is not False
    grants_all_new = role_changed and await _role_grants_all(update["role_id"]) and not target_super
    flags = {"self": _is_self, "password": bool(payload.get("password")), "mfa_phone": _phone_change,
             "mfa_phone_cleared": bool(_phone_change and not _pn_new), "role_changed": role_changed,
             "deactivated": deactivating, "grants_all_new": grants_all_new}
    single = False
    # T23/AUTH16: BAŞKA bir personelin şifresini, MFA (giriş doğrulama) telefonunu değiştirmek ya da
    # MFA telefonunu temizlemek (= SMS doğrulamasını kapatmak) tek kişiyle faktör devralma yoludur →
    # iki kişi kuralı (≥2 aktif süper admin: ikinci onay; tek süper admin: hemen + uyarı +
    # single_admin_exception denetimi — IBAN kuralıyla aynı).
    other_factor_change = (not _is_self) and (bool(payload.get("password")) or _phone_change)
    if role_changed or deactivating or other_factor_change or "is_active" in update:
        from .deps import assert_no_recovery_restriction
        await assert_no_recovery_restriction(current_user, "rbac.user_update")
    if (target_super and (role_changed or deactivating)) or grants_all_new or other_factor_change:
        if target_super and deactivating:
            what = "Süper admini pasifleştirme"
        elif target_super and role_changed:
            what = "Süper adminin rolünü değiştirme"
        elif grants_all_new:
            what = "Personele tüm yetkili ('*') rol atama"
        else:
            _parts = []
            if payload.get("password"):
                _parts.append("şifre")
            if _phone_change:
                _parts.append("MFA telefonunu kaldırma" if not _pn_new else "MFA telefonu")
            what = "Başka personelin giriş bilgisi değişikliği (" + ", ".join(_parts) + ")"
        decision = await _approvals.gate("rbac.user_update",
                                         params={"user_id": user_id, "ops": ops, "flags": flags},
                                         summary=f"{what}: {user.get('email') or user_id}",
                                         current_user=current_user, request=request,
                                         entity_type="panel_user", entity_id=user_id)
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_user_update(user_id, user, ops, current_user, request, flags=flags, single=single)


async def _exec_user_update(params, requester, approver, request, approval_id=""):
    user_id = str(params.get("user_id") or "")
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı artık mevcut değil")
    ops = dict(params.get("ops") or {})
    ops["set"] = {**(ops.get("set") or {}), "updated_at": datetime.now(timezone.utc).isoformat()}
    await _do_user_update(user_id, user, ops, requester, request, flags=dict(params.get("flags") or {}),
                          approval_id=approval_id, approver=approver)
    return {"user_id": user_id}


async def _do_user_delete(user: dict, actor: dict, request, *, single=False, approval_id="", approver=None) -> dict:
    async def _perform():
        await db.users.delete_one({"id": user["id"]})
    await run_audited(db, action="rbac.user_delete", entity_type="panel_user", entity_id=user["id"],
                      perform=_perform, before=_user_public(user), current_user=approver or actor,
                      request=request, source="admin.rbac",
                      metadata=_flag_meta(single, approval_id, approver, {"requested_by": actor.get("id")}))
    return {"success": True}


@router.delete("/users/{user_id}")
async def delete_panel_user(user_id: str, request: Request, current_user: dict = Depends(require_super_admin)):
    await _no_recovery(current_user, "rbac.user_delete")
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    # Prevent self-delete
    if user.get("id") == current_user.get("id") or user.get("email") == current_user.get("email"):
        raise HTTPException(status_code=400, detail="Kendinizi silemezsiniz")
    single = False
    if await _user_is_super(user):
        decision = await _approvals.gate("rbac.user_delete", params={"user_id": user_id},
                                         summary=f"Süper admini silme: {user.get('email') or user_id}",
                                         current_user=current_user, request=request,
                                         entity_type="panel_user", entity_id=user_id)
        if decision["mode"] == "pending":
            return _pending(decision)
        single = decision["single_admin_exception"]
    return await _do_user_delete(user, current_user, request, single=single)


async def _exec_user_delete(params, requester, approver, request, approval_id=""):
    user = await db.users.find_one({"id": str(params.get("user_id") or "")}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı artık mevcut değil")
    if user.get("id") == (approver or {}).get("id"):
        raise HTTPException(status_code=400, detail="Onaylayan kendini silemez")
    await _do_user_delete(user, requester, request, approval_id=approval_id, approver=approver)
    return {"user_id": user["id"]}


_approvals.register("rbac.role_create", _exec_role_create, "Tüm yetkili rol oluşturma")
_approvals.register("rbac.role_update", _exec_role_update, "Tüm yetkili rol değişikliği")
_approvals.register("rbac.role_delete", _exec_role_delete, "Tüm yetkili rol silme")
_approvals.register("rbac.user_create", _exec_user_create, "Süper admin oluşturma")
_approvals.register("rbac.invite_create", _exec_invite, "Süper admin daveti")
_approvals.register("rbac.invite_resend", _exec_invite_resend, "Süper admin davetini yeniden gönderme")
_approvals.register("rbac.user_update", _exec_user_update,
                   "Personel rol/durum ya da başka personelin şifre/MFA telefonu değişikliği")
_approvals.register("rbac.user_delete", _exec_user_delete, "Süper admin silme")


# ───────────────────── İKİ KİŞİ ONAYI (BL-027) ─────────────────────

@router.get("/approvals")
async def list_approvals(current_user: dict = Depends(require_super_admin)):
    rows = await _approvals.list_approvals(include_decided=True)
    supers = await _approvals.active_super_admins()
    return {"items": rows, "pending": sum(1 for r in rows if r.get("status") == "pending"),
            "active_super_admins": len(supers), "two_person_disabled": _approvals.two_person_disabled(),
            "me": current_user.get("id")}


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str, request: Request, current_user: dict = Depends(require_super_admin),
                           credentials: HTTPAuthorizationCredentials = Depends(security)):
    await _no_recovery(current_user, "approval.approve")
    if not is_recent_auth(credentials):
        raise HTTPException(status_code=403,
                            detail="Onay için son 5 dakika içinde giriş yapmış olmalısınız. Çıkış yapıp yeniden "
                                   "giriş yaptıktan sonra tekrar deneyin.")
    return await _approvals.approve(approval_id, current_user, request)


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(approval_id: str, request: Request, payload: dict = None,
                          current_user: dict = Depends(require_super_admin)):
    return await _approvals.reject(approval_id, current_user, request, reason=str((payload or {}).get("reason") or ""))


@router.get("/me/permissions")
async def get_my_permissions(current_user: dict = Depends(require_admin)):
    """Return effective permissions of the current user."""
    # Panel açılışı (AdminLayout) → kritik uyarı çağrısı yetkili panele girdiyse kapanır (tekrar SMS durur).
    try:
        from security.alert_paging import note_panel_presence
        await note_panel_presence(current_user)
    except Exception:
        pass
    await _ensure_default_roles()
    permissions = await get_effective_permissions(current_user)
    # Panel boşta-kalma süresi (İşletme Kuralı panel.auto_logout_minutes; 0 = kapalı) —
    # AdminLayout istemci zamanlayıcısı bunu kullanır; sunucu da aynı kuralı uygular.
    try:
        from business_rules import get_rule as _gr
        _idle = int(float(await _gr(db, "panel.auto_logout_minutes", 1440) or 0))
    except Exception:
        _idle = 60
    if "*" in permissions:
        return {"permissions": ["*"], "role": "Süper Admin", "auto_logout_minutes": _idle}
    role_id = current_user.get("role_id") or ""
    if not role_id:
        return {"permissions": [], "role": "Rol atanmamış", "auto_logout_minutes": _idle}
    role = await db.roles.find_one({"id": role_id}, {"_id": 0})
    if not role:
        return {"permissions": [], "role": None, "auto_logout_minutes": _idle}
    return {"permissions": permissions, "role": role.get("name"), "auto_logout_minutes": _idle}
