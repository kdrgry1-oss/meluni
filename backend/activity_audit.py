"""Security-safe admin mutation audit helpers.

This module appends schema-v2 records to the existing ``audit_logs`` collection.
It never changes or rewrites legacy audit/auth/order/stock records.
"""
from datetime import datetime, timezone
import hashlib
import logging
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


_SECRET_KEY = re.compile(
    r"password|passwd|token|secret|api.?key|private.?key|authorization|cookie|session.?id|"
    r"client.?secret|access.?key|refresh.?key|cvv|cvc|card|pan|iban",
    re.I,
)
_PII_KEY = re.compile(
    r"(^|_)(email|phone|mobile|address|first.?name|last.?name|full.?name|sender.?name|"
    r"tax.?(?:number|no)|identity|tckn|vkn|recipient|customer)(_|$)",
    re.I,
)
_BULKY_KEY = re.compile(r"content|html|description|body|image|video|template", re.I)
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?90\s*)?(?:\(?0?5\d{2}\)?[\s.-]*)?\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)?\b")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


_ID_TOKEN = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _safe_text(value: str) -> str:
    text = str(value or "")
    # Kimlik (uuid / 32-hex: kullanıcı, onay, intent id'leri) PII değildir. Eskiden içindeki ≥7 haneli
    # rakam dizisi telefon sanılıp rastgele "[REDACTED_PII]" yapılıyor, denetim kayıtları arasındaki
    # bağ (approval_id, intent_id, requested_by) kayboluyordu.
    if _ID_TOKEN.fullmatch(text):
        return text
    if _JWT.search(text) or _CARD.search(text):
        return "[REDACTED]"
    if _EMAIL.search(text) or _PHONE.search(text):
        return "[REDACTED_PII]"
    if text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        text = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return text[:500] + ("…" if len(text) > 500 else "")


def redact_value(value, *, key: str = "", depth: int = 0):
    """Return a bounded, JSON-safe representation with secrets and PII removed."""
    if _SECRET_KEY.search(key or ""):
        return "[REDACTED]"
    if _PII_KEY.search(key or ""):
        return "[REDACTED_PII]"
    if _BULKY_KEY.search(key or ""):
        if isinstance(value, (list, tuple, dict)):
            return f"[{type(value).__name__} {len(value)}]"
        return f"[text {len(str(value or ''))} chars]"
    if depth >= 4:
        return "[nested]"
    if isinstance(value, dict):
        return {str(k)[:80]: redact_value(v, key=str(k), depth=depth + 1)
                for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [redact_value(v, depth=depth + 1) for v in list(value)[:30]]
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(str(value))


def redacted_diff(before: Optional[dict], after: Optional[dict]) -> dict:
    before, after = before or {}, after or {}
    changed = sorted(k for k in set(before) | set(after)
                     if k not in {"_id", "updated_at"} and before.get(k) != after.get(k))
    return {
        str(k)[:80]: {
            "old": redact_value(before.get(k), key=str(k)),
            "new": redact_value(after.get(k), key=str(k)),
        }
        for k in changed[:100]
    }


def audit_actor(current_user: Optional[dict], request=None) -> tuple[dict, dict]:
    user = current_user or {}
    email = str(user.get("email") or user.get("username") or "").strip().lower()
    actor = {
        "id": str(user.get("id") or user.get("user_id") or ""),
        # Identity remains joinable by internal user id. Email is not duplicated
        # into the audit collection; its hash supports correlation if an account
        # is later deleted.
        "email_hash": hashlib.sha256(email.encode()).hexdigest()[:16] if email else "",
        "role": str(user.get("role") or ("admin" if user.get("is_admin") else "")),
        "login_method": str(user.get("login_method") or user.get("auth_provider") or user.get("provider") or ""),
    }
    auth, ua, ip = "", "", ""
    if request is not None:
        auth = str(request.headers.get("authorization") or "")
        ua = str(request.headers.get("user-agent") or "")[:300]
        forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = forwarded or str(getattr(getattr(request, "client", None), "host", "") or "")
    return actor, {
        "session_hash": hashlib.sha256(auth.encode()).hexdigest()[:12] if auth else "",
        "ip": ip[:64], "user_agent": ua,
    }


def _new_audit_id(seed: str) -> str:
    import uuid as _uuid
    return hashlib.sha256(f"{seed}|{_uuid.uuid4().hex}".encode()).hexdigest()[:32]


async def record_admin_audit(db, *, action: str, entity_type: str, entity_id: str,
                             before: Optional[dict] = None, after: Optional[dict] = None,
                             current_user: Optional[dict] = None, request=None,
                             source: str = "admin", success: bool = True,
                             metadata: Optional[dict] = None, phase: Optional[str] = None,
                             strict: bool = False, return_id: bool = False):
    """Append an admin audit record (schema v2) to the tamper-evident hash chain.

    Default (strict=False): best-effort — returns True/False and never raises, so it cannot
    turn an applied mutation into a retryable error. If the chain head cannot be advanced the
    record is still written (unchained, ``chain_error=True``) so nothing is silently lost.
    strict=True: the chained write MUST succeed, otherwise the exception propagates (used by
    ``run_audited`` for fail-closed critical actions). return_id=True returns the record id
    (or None on best-effort failure) instead of a bool.
    """
    actor, context = audit_actor(current_user, request)
    if not actor.get("id"):
        # ADM-02: kullanıcısız (zamanlayıcı / sistem) kayıtlar AÇIK sistem kimliği taşır: "system:<iş>".
        actor["id"] = system_actor_id(source)
        actor["role"] = "system"
    now = datetime.now(timezone.utc).isoformat()
    seed = f"{now}|{actor.get('id')}|{action}|{entity_type}|{entity_id}"
    rec = {
        "id": _new_audit_id(seed),
        "schema_version": 2, "category": "admin_mutation", "action": str(action)[:120],
        "entity": {"type": str(entity_type)[:80], "id": str(entity_id)[:160]},
        "actor": actor, "context": context, "source": str(source)[:120],
        "success": bool(success), "diff": redacted_diff(before, after),
        "metadata": redact_value(metadata or {}), "created_at": now,
    }
    if phase:
        rec["phase"] = str(phase)[:20]
    chained = getattr(db, "settings", None) is not None
    try:
        if chained:
            from security.audit_chain import append_chained
            await append_chained(db, rec)
        else:  # minimal/test db without a settings collection
            await db.audit_logs.insert_one(dict(rec))
        return rec["id"] if return_id else True
    except Exception:
        logging.getLogger(__name__).exception("admin audit chained insert failed action=%s entity=%s",
                                              action, entity_id)
        if strict:
            raise
    try:
        rec["chain_error"] = True
        await db.audit_logs.insert_one(dict(rec))
        return rec["id"] if return_id else True
    except Exception:
        logging.getLogger(__name__).exception("admin audit insert failed action=%s entity=%s", action, entity_id)
        try:  # CHAIN07: kayıt kaybolmasın → outbox (DB; o da yoksa yerel JSONL); iş sonra zincire aktarır
            from security.audit_outbox import stash as _audit_outbox_stash
            await _audit_outbox_stash(db, rec)
        except Exception:
            logging.getLogger(__name__).exception("audit outbox stash failed action=%s", action)
        return None if return_id else False


async def run_audited(db, *, action: str, entity_type: str, entity_id: str, perform,
                      before: Optional[dict] = None, after: Optional[dict] = None,
                      current_user: Optional[dict] = None, request=None,
                      source: str = "admin", metadata: Optional[dict] = None):
    """FAIL-CLOSED wrapper for critical admin actions.

    1. writes an ``intent`` record (chained, strict) — if that fails the action is NOT
       performed and HTTP 503 is raised;
    2. awaits ``perform()`` (a zero-arg coroutine function);
    3. writes ``done`` (or ``failed`` with the error type) referencing the intent id.
    Records are append-only — the intent is never mutated, so the hash chain stays valid.
    """
    from fastapi import HTTPException as _HTTPException
    meta = dict(metadata or {})
    try:
        intent_id = await record_admin_audit(
            db, action=action, entity_type=entity_type, entity_id=entity_id, before=before, after=after,
            current_user=current_user, request=request, source=source, success=True,
            metadata=meta, phase="intent", strict=True, return_id=True)
    except Exception:
        raise _HTTPException(status_code=503,
                             detail="Denetim kaydı yazılamadı; güvenlik gereği işlem yapılmadı. Tekrar deneyin.")
    if not intent_id:
        raise _HTTPException(status_code=503,
                             detail="Denetim kaydı yazılamadı; güvenlik gereği işlem yapılmadı. Tekrar deneyin.")
    try:
        result = await perform()
    except Exception as e:
        _detail = getattr(e, "detail", None)
        await record_admin_audit(
            db, action=action, entity_type=entity_type, entity_id=entity_id,
            current_user=current_user, request=request, source=source, success=False,
            metadata={"intent_id": intent_id, "error": type(e).__name__,
                      "status": getattr(e, "status_code", None),
                      "detail": str(_detail)[:200] if _detail else ""},
            phase="failed")
        raise
    await record_admin_audit(
        db, action=action, entity_type=entity_type, entity_id=entity_id,
        current_user=current_user, request=request, source=source, success=True,
        metadata={"intent_id": intent_id, **{k: v for k, v in meta.items()
                                            if k in ("single_admin_exception", "approval_id")}},
        phase="done")
    return result


# ───────────────── Tamper-evident chain: scheduler / admin entry points ─────────────────

async def verify_audit_chain(full: bool = False, db=None) -> dict:
    """Verify the audit hash chain (incremental from the last clean checkpoint, or full).
    On failure an alert goes to all active super admins (throttled)."""
    if db is None:
        from routes.deps import db as _db
        db = _db
    from security.audit_chain import verify_chain
    res = await verify_chain(db, full=full)
    if not res.get("ok"):
        try:
            from security.events import security_event
            first = (res.get("problems") or [{}])[0]
            await security_event(
                "audit_chain_broken", None,
                {"problems": len(res.get("problems") or []), "first_seq": first.get("seq"),
                 "first_type": first.get("type"), "full": bool(full),
                 "fp": f"{first.get('seq')}:{first.get('type')}"},
                body="Denetim kayıtlarında silinmiş/değiştirilmiş kayıt tespit edildi. Arşiv (R2) kopyasıyla "
                     "karşılaştırın: Güvenlik Paneli → Denetim Zinciri.")
        except Exception:
            pass
    return res


async def export_audit_to_archive() -> dict:
    """Scheduler job (every 15 min): incremental chain verification + export of new chained
    audit records to the PRIVATE R2 archive bucket (denetim/YYYY/MM/DD/<seq>.jsonl).
    Never raises."""
    try:
        from routes.deps import db as _db
        from security.audit_chain import export_batch_to_archive
        try:
            await verify_audit_chain(full=False, db=_db)
        except Exception as _ve:
            logging.getLogger(__name__).warning("audit chain verify failed: %s", type(_ve).__name__)
        res = await export_batch_to_archive(_db)
        if not res.get("ok") and not res.get("skipped"):
            try:
                from security.events import security_event
                await security_event("audit_export_failed", None,
                                     {"error": res.get("error"), "fp": str(res.get("error"))}, audit=False)
            except Exception:
                pass
        return res
    except Exception as e:
        logging.getLogger(__name__).error("export_audit_to_archive failed: %s", type(e).__name__)
        return {"ok": False, "error": type(e).__name__}



# ───────────────── ADM-02: sistem (zamanlayıcı / otomasyon) kimliği ─────────────────
# Kullanıcısız denetim kayıtlarında actor.id boş kalmaz: "system:<iş adı>". İş adı önce bağlamdan
# (system_job(...) ile sarılmış zamanlayıcı işi), yoksa kaydın source alanından ("system." öneki atılır).
import contextlib as _ctxlib
import contextvars as _ctxvars
import functools as _functools

_SYSTEM_JOB: "_ctxvars.ContextVar[str]" = _ctxvars.ContextVar("audit_system_job", default="")


def system_actor_id(source: str = "") -> str:
    job = _SYSTEM_JOB.get() or str(source or "").strip()
    if job.startswith("system:"):
        job = job[len("system:"):]
    elif job.startswith("system."):
        job = job[len("system."):]
    job = re.sub(r"[^A-Za-z0-9_.\-]", "_", job)[:80] or "unknown"
    return f"system:{job}"


@_ctxlib.contextmanager
def system_job(name: str):
    """Bu bağlamda yazılan kullanıcısız denetim kayıtları actor.id="system:<name>" taşır."""
    tok = _SYSTEM_JOB.set(str(name or ""))
    try:
        yield
    finally:
        _SYSTEM_JOB.reset(tok)


def as_system_job(fn, name: str = ""):
    """Zamanlayıcı işi sarmalayıcı (async fonksiyon): scheduler._add içinde kullanılabilir."""
    job = name or getattr(fn, "__name__", "job")

    @_functools.wraps(fn)
    async def _wrapped(*a, **k):
        with system_job(job):
            return await fn(*a, **k)
    return _wrapped
