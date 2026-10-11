"""Two-person approval (BL-027) for critical admin actions.

Flow
----
    decision = await gate(action, params=..., summary=..., current_user=..., request=...)
    if decision["mode"] == "pending":   # ≥1 OTHER active super admin exists
        return 202 + decision["approval"]          # nothing executed yet
    # immediate: no other active super admin (single_admin_exception=True, alert sent)
    #            or env TWO_PERSON_RULE=off (break-glass, alert sent)
    await _do_the_thing(..., single_admin_exception=decision["single_admin_exception"])

Approval: a DIFFERENT active super admin who authenticated within the last 5 minutes calls
``approve(approval_id, approver, request)`` → the registered executor runs the action with
the stored params. Pending items expire after 24h. Requester may cancel (reject) their own.

Executors are registered by the owning route modules at import time:
    register("rbac.role_create", async_fn(params, requester, approver, request) -> dict)

``params`` must never contain plaintext secrets (passwords are stored pre-hashed; phones
encrypted). ``summary`` is a human-readable, non-secret description shown in the panel.

Lock-out safety: env ``TWO_PERSON_RULE=off`` executes immediately (alerted + audited).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

APPROVAL_TTL_HOURS = 24
_EXECUTORS: Dict[str, Callable[..., Awaitable[dict]]] = {}
_LABELS: Dict[str, str] = {}


def _db():
    from routes.deps import db
    return db


def register(action: str, fn: Callable[..., Awaitable[dict]], label: str = "") -> None:
    _EXECUTORS[action] = fn
    if label:
        _LABELS[action] = label


def action_label(action: str) -> str:
    return _LABELS.get(action, action)


def two_person_disabled() -> bool:
    return (os.environ.get("TWO_PERSON_RULE") or "").strip().lower() in ("0", "off", "false", "no", "disable")


async def star_role_ids() -> set:
    ids = set()
    try:
        async for r in _db().roles.find({"permissions": "*"}, {"_id": 0, "id": 1}):
            if r.get("id"):
                ids.add(r["id"])
    except Exception:
        pass
    return ids


async def active_super_admins() -> list:
    """Active staff whose effective permissions include '*' (flag or '*' role)."""
    star = await star_role_ids()
    q = {"is_active": {"$ne": False}, "is_admin": True,
         "$or": [{"is_super_admin": True}] + ([{"role_id": {"$in": list(star)}}] if star else [])}
    out = []
    try:
        async for u in _db().users.find(q, {"_id": 0, "id": 1, "email": 1}).limit(200):
            out.append(u)
    except Exception as e:
        logger.warning("active_super_admins failed: %s", type(e).__name__)
    return out


def _who(u: Optional[dict]) -> dict:
    u = u or {}
    return {"id": str(u.get("id") or ""), "email": str(u.get("email") or "")}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def gate(action: str, *, params: dict, summary: str, current_user: dict, request=None,
               entity_type: str = "", entity_id: str = "") -> dict:
    """Decide: store as pending approval, or execute immediately (single admin / break-glass)."""
    from security.events import security_event
    supers = await active_super_admins()
    me = str((current_user or {}).get("id") or "")
    others = [s for s in supers if str(s.get("id") or "") != me]
    if two_person_disabled():
        await security_event("single_admin_exception", current_user,
                             {"action": action, "summary": summary[:300], "reason": "TWO_PERSON_RULE=off",
                              "fp": action}, request=request, audit=False)
        return {"mode": "immediate", "single_admin_exception": True, "reason": "break_glass"}
    if not others:
        await security_event("single_admin_exception", current_user,
                             {"action": action, "summary": summary[:300], "reason": "only_one_super_admin",
                              "fp": action}, request=request, audit=False)
        return {"mode": "immediate", "single_admin_exception": True, "reason": "single_super_admin"}
    now = datetime.now(timezone.utc)
    doc = {
        "id": uuid.uuid4().hex,
        "action": action,
        "label": action_label(action),
        "summary": str(summary)[:600],
        "params": params,
        "entity_type": entity_type, "entity_id": entity_id,
        "status": "pending",
        "requested_by": _who(current_user),
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(hours=APPROVAL_TTL_HOURS)),
    }
    # Fail-closed: the request itself is audited before it is stored.
    from activity_audit import record_admin_audit
    try:
        ok = await record_admin_audit(
            _db(), action=f"approval.request.{action}", entity_type="pending_approval", entity_id=doc["id"],
            current_user=current_user, request=request, source="security.approvals",
            metadata={"summary": doc["summary"], "target_type": entity_type, "target_id": entity_id},
            phase="intent", strict=True)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=503, detail="Denetim kaydı yazılamadı; onay talebi oluşturulmadı.")
    await _db().pending_approvals.insert_one(dict(doc))
    await security_event("approval_requested", current_user,
                         {"action": action_label(action), "summary": doc["summary"], "approval_id": doc["id"],
                          "fp": doc["id"]},
                         request=request, audit=False,
                         body="Kullanıcılar & Roller → Onay Bekleyen İşlemler ekranından onaylayın ya da reddedin.")
    return {"mode": "pending", "approval": public_view(doc)}


def public_view(doc: dict) -> dict:
    d = dict(doc or {})
    d.pop("_id", None)
    d.pop("params", None)  # params may contain hashes/encrypted values — never sent to the UI
    return d


def pending_response(decision: dict) -> dict:
    a = decision.get("approval") or {}
    return {"success": True, "pending_approval": True, "approval": a,
            "message": "İşlem ikinci bir süper admin onayına gönderildi (24 saat geçerli)."}


async def expire_old() -> int:
    try:
        r = await _db().pending_approvals.update_many(
            {"status": "pending", "expires_at": {"$lt": _iso(datetime.now(timezone.utc))}},
            {"$set": {"status": "expired"}})
        return int(getattr(r, "modified_count", 0) or 0)
    except Exception:
        return 0


async def list_approvals(include_decided: bool = True) -> list:
    await expire_old()
    q: dict = {"status": "pending"}
    if include_decided:
        since = _iso(datetime.now(timezone.utc) - timedelta(days=7))
        q = {"$or": [{"status": "pending"}, {"created_at": {"$gte": since}}]}
    rows = await _db().pending_approvals.find(q, {"_id": 0, "params": 0}).sort("created_at", -1).to_list(200)
    return rows


async def pending_count() -> int:
    await expire_old()
    try:
        return await _db().pending_approvals.count_documents({"status": "pending"})
    except Exception:
        return 0


async def approve(approval_id: str, approver: dict, request=None) -> dict:
    from activity_audit import record_admin_audit
    from security.events import security_event
    db = _db()
    await expire_old()
    doc = await db.pending_approvals.find_one({"id": str(approval_id or "")}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Onay talebi bulunamadı")
    if doc.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Bu talep artık beklemede değil ({doc.get('status')}).")
    if str((doc.get("requested_by") or {}).get("id") or "") == str(approver.get("id") or ""):
        raise HTTPException(status_code=403, detail="Kendi talebinizi onaylayamazsınız; başka bir süper admin onaylamalı.")
    fn = _EXECUTORS.get(doc.get("action"))
    if fn is None:
        raise HTTPException(status_code=500, detail="Bu işlem türü için yürütücü bulunamadı")
    # Fail-closed audit of the approval itself.
    try:
        ok = await record_admin_audit(
            db, action=f"approval.approve.{doc.get('action')}", entity_type="pending_approval",
            entity_id=doc["id"], current_user=approver, request=request, source="security.approvals",
            metadata={"summary": doc.get("summary"), "requested_by": (doc.get("requested_by") or {}).get("id")},
            phase="intent", strict=True)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=503, detail="Denetim kaydı yazılamadı; onay uygulanmadı.")
    claimed = await db.pending_approvals.find_one_and_update(
        {"id": doc["id"], "status": "pending",
         "expires_at": {"$gte": _iso(datetime.now(timezone.utc))}},
        {"$set": {"status": "executing", "approved_by": _who(approver),
                  "approved_at": _iso(datetime.now(timezone.utc))}},
        projection={"_id": 0})
    if not claimed:
        raise HTTPException(status_code=409, detail="Talep başka biri tarafından işlendi ya da süresi doldu.")
    requester = doc.get("requested_by") or {}
    try:
        req_user = await db.users.find_one({"id": requester.get("id")}, {"_id": 0, "password": 0}) or requester
        result = await fn(dict(doc.get("params") or {}), req_user, approver, request,
                          approval_id=doc["id"])
    except HTTPException as e:
        await db.pending_approvals.update_one(
            {"id": doc["id"]}, {"$set": {"status": "failed", "error": str(e.detail)[:300],
                                         "decided_at": _iso(datetime.now(timezone.utc))}})
        raise
    except Exception as e:
        logger.exception("approval executor failed action=%s", doc.get("action"))
        await db.pending_approvals.update_one(
            {"id": doc["id"]}, {"$set": {"status": "failed", "error": type(e).__name__,
                                         "decided_at": _iso(datetime.now(timezone.utc))}})
        raise HTTPException(status_code=500, detail="Onaylanan işlem uygulanamadı")
    await db.pending_approvals.update_one(
        {"id": doc["id"]}, {"$set": {"status": "executed", "decided_at": _iso(datetime.now(timezone.utc)),
                                     "result": {k: v for k, v in (result or {}).items()
                                                if isinstance(v, (str, int, float, bool))}}})
    await security_event("approval_decided", approver,
                         {"action": action_label(doc.get("action")), "decision": "onaylandı",
                          "summary": doc.get("summary"), "approval_id": doc["id"], "fp": doc["id"]},
                         request=request, audit=False)
    return {"success": True, "status": "executed", "result": result or {}}


async def reject(approval_id: str, actor: dict, request=None, reason: str = "") -> dict:
    from activity_audit import record_admin_audit
    from security.events import security_event
    db = _db()
    doc = await db.pending_approvals.find_one({"id": str(approval_id or "")}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Onay talebi bulunamadı")
    own = str((doc.get("requested_by") or {}).get("id") or "") == str(actor.get("id") or "")
    res = await db.pending_approvals.find_one_and_update(
        {"id": doc["id"], "status": "pending"},
        {"$set": {"status": "cancelled" if own else "rejected", "rejected_by": _who(actor),
                  "reject_reason": str(reason or "")[:300],
                  "decided_at": _iso(datetime.now(timezone.utc))}},
        projection={"_id": 0})
    if not res:
        raise HTTPException(status_code=409, detail="Bu talep artık beklemede değil")
    await record_admin_audit(db, action=f"approval.{'cancel' if own else 'reject'}.{doc.get('action')}",
                             entity_type="pending_approval", entity_id=doc["id"], current_user=actor,
                             request=request, source="security.approvals",
                             metadata={"summary": doc.get("summary"), "reason": str(reason or "")[:200]})
    if not own:
        await security_event("approval_decided", actor,
                             {"action": action_label(doc.get("action")), "decision": "reddedildi",
                              "summary": doc.get("summary"), "approval_id": doc["id"], "fp": doc["id"]},
                             request=request, audit=False)
    return {"success": True, "status": "cancelled" if own else "rejected"}


# ───────────── İşletme Kuralları: MFA-zorunluluğunu kapatma koruması ─────────────
# business_rules_api.py (bu ajanın dosyası değil) bu yardımcıyı çağırmalıdır:
#     from security.approvals import guard_business_rules_patch
#     blocked = await guard_business_rules_patch(patch, current_user, request)
#     if blocked: return blocked          # 202 benzeri yanıt; değişiklik onaya gitti
# Kritik anahtarlar kapatılıyorsa (ör. security.admin_mfa_required=False) değişiklik iki-kişi
# kuralına tabi olur; onaylanınca "business_rules.security_patch" yürütücüsü uygular.
_CRITICAL_RULE_KEYS = {"security.admin_mfa_required": False}


async def guard_business_rules_patch(patch: dict, current_user: dict, request=None) -> Optional[dict]:
    crit = {k: v for k, v in (patch or {}).items()
            if k in _CRITICAL_RULE_KEYS and v == _CRITICAL_RULE_KEYS[k]}
    if not crit:
        return None
    decision = await gate("business_rules.security_patch", params={"patch": crit},
                          summary="İşletme kuralı kapatma: " + ", ".join(sorted(crit)),
                          current_user=current_user, request=request,
                          entity_type="business_rules", entity_id=",".join(sorted(crit)))
    if decision["mode"] == "pending":
        # Kritik anahtarlar onaya gider; diğer anahtarlar çağıran tarafından normal kaydedilir.
        for k in crit:
            patch.pop(k, None)
        return pending_response(decision)
    return None


async def _exec_business_rules_patch(params: dict, requester: dict, approver: dict, request=None,
                                     approval_id: str = "") -> dict:
    import business_rules as BR
    from activity_audit import run_audited
    patch = {k: v for k, v in (params.get("patch") or {}).items() if k in _CRITICAL_RULE_KEYS}

    async def _perform():
        return await BR.save_rules(_db(), patch)
    await run_audited(_db(), action="business_rules.security_patch", entity_type="business_rules",
                      entity_id=",".join(sorted(patch)), perform=_perform, after=patch,
                      current_user=approver, request=request, source="security.approvals",
                      metadata={"approval_id": approval_id, "requested_by": (requester or {}).get("id")})
    return {"applied": len(patch)}


register("business_rules.security_patch", _exec_business_rules_patch, "İşletme kuralı (güvenlik) kapatma")
