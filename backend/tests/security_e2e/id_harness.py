"""Shared harness for the identity/security end-to-end tests (fixtures prefixed ``id_``).

Imported by conftest.py. Everything runs in-process:
  * MongoDB  -> mongomock_motor (AsyncMongoMockClient), wiped before every test
  * SMS / e-mail senders -> in-memory outbox (notification_service.send_notification,
    email_smtp.send_smtp_email)
  * httpx (Resend etc.) -> respx router; any un-mocked HTTP call fails the test
  * raw TCP sockets -> blocked (no real network can be reached)
  * R2 archive -> in-memory dict
  * scheduler -> never started (the app lifespan is not run: TestClient is used without ``with``)
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

# ── environment BEFORE any backend import ────────────────────────────────────────────────────
os.environ.setdefault("MONGO_URL", "mongodb://mock-only.invalid:27017")
os.environ.setdefault("DB_NAME", "security_e2e")
os.environ.setdefault("JWT_SECRET", "e2e-" + "k" * 60)
os.environ.setdefault("ENVIRONMENT", "test")
for _k in ("RESEND_API_KEY", "ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD",
           "ADMIN_MFA_ENFORCE", "ADMIN_MFA_CHANNEL_POLICY", "TWO_PERSON_RULE",
           "ALLOW_DIRECT_STAFF_CREATE", "STAFF_IDLE_TIMEOUT", "CF_EDGE_SECRET", "RESEND_FROM",
           "ALERT_TO_EMAIL", "VAULT_SUPER_EMAILS", "IBAN_COOLING_OFF_MINUTES", "STAFF_TOKEN_TTL_HOURS"):
    os.environ.pop(_k, None)

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import motor.motor_asyncio  # noqa: E402
import mongomock_motor  # noqa: E402

motor.motor_asyncio.AsyncIOMotorClient = mongomock_motor.AsyncMongoMockClient


# ── hard network guard: nothing in these tests may open a TCP connection ─────────────────────
class _NetworkBlocked(RuntimeError):
    pass


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guard(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise _NetworkBlocked(f"security_e2e: outbound network blocked ({address!r})")
    return _real_connect(self, address)


def _guard_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise _NetworkBlocked(f"security_e2e: outbound network blocked ({address!r})")
    return _real_connect_ex(self, address)


if getattr(socket.socket.connect, "__name__", "") == "connect":  # not yet guarded by conftest
    socket.socket.connect = _guard
    socket.socket.connect_ex = _guard_ex

import freezegun  # noqa: E402

# Event-loop / thread internals keep real time so frozen clocks never stall asyncio/anyio.
freezegun.configure(extend_ignore_list=["asyncio", "anyio", "concurrent", "selectors"])

import bcrypt  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402  (imports every router; lifespan/scheduler NOT started)
from routes import deps  # noqa: E402
from security import alerts as _alerts_mod  # noqa: E402

# Real alert dispatch functions, captured before any (autouse) fixture can replace them.
_REAL_ALERT_DELIVER = _alerts_mod._deliver
_REAL_ALERT_RECIPIENTS = _alerts_mod.resolve_default_recipients

ID_PASSWORD = "Str0ng!Passw0rd#2026"
ID_NEW_PASSWORD = "An0ther!Str0ngPw#77"


def id_run(coro):
    """Run a coroutine against the (loop-agnostic) mongomock db from sync test code."""
    return asyncio.run(coro)


def id_hash(pw: str = ID_PASSWORD) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


def id_decode(token: str) -> dict:
    return jwt.decode(token, deps.JWT_SECRET, algorithms=["HS256"], issuer=deps.JWT_ISSUER,
                      options={"verify_exp": False})


async def _wipe_db():
    db = deps.db
    for name in await db.list_collection_names():
        await db.drop_collection(name)


def _reset_module_caches():
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    deps._IDLE_SEEN.clear()
    deps._MEMBER_CACHE.clear()
    deps._IDLE_INDEX_DONE["v"] = False
    import business_rules
    business_rules.invalidate()
    business_rules._cache = {}
    import security_switches
    security_switches._cache.update({"at": 0.0, "switches": None})
    from security import alerts
    alerts._cache.update({"at": 0.0, "company": None, "smtp_user": ""})
    try:
        deps.limiter.reset()
    except Exception:
        pass
    try:
        server.app.state.limiter.reset()
    except Exception:
        pass


class IdOutbox:
    """Captures every SMS / e-mail the backend tries to send."""

    def __init__(self):
        self.notifications = []   # send_notification calls
        self.emails = []          # send_smtp_email calls

    def sms(self, phone_tail: str = ""):
        return [n for n in self.notifications if "sms" in (n.get("channels") or [])
                and str(n.get("to_phone") or "").endswith(phone_tail)]

    def codes(self, channel: str = "sms"):
        return [str((n.get("variables") or {}).get("otp_code")) for n in self.notifications
                if channel in (n.get("channels") or []) and (n.get("variables") or {}).get("otp_code")]

    def last_code(self, channel: str = "sms") -> str:
        c = self.codes(channel)
        assert c, f"no {channel} code was sent; outbox={self.notifications}"
        return c[-1]


def _patch_everywhere(monkeypatch, module_name: str, attr: str, replacement):
    mod = __import__(module_name)
    orig = getattr(mod, attr)
    monkeypatch.setattr(mod, attr, replacement)
    for m in list(sys.modules.values()):
        try:
            if m is not None and m is not mod and getattr(m, attr, None) is orig:
                monkeypatch.setattr(m, attr, replacement)
        except Exception:
            pass


@pytest.fixture
def id_outbox(monkeypatch):
    box = IdOutbox()

    async def _fake_notify(db, event, *args, **kwargs):
        box.notifications.append({"event": event, **kwargs})
        return {"success": True}

    async def _fake_smtp(db, to, subject, html, *args, **kwargs):
        box.emails.append({"to": to, "subject": subject, "html": html})
        return {"success": True}

    _patch_everywhere(monkeypatch, "notification_service", "send_notification", _fake_notify)
    _patch_everywhere(monkeypatch, "email_smtp", "send_smtp_email", _fake_smtp)
    return box


@pytest.fixture
def id_archive(monkeypatch):
    """In-memory private R2 archive bucket."""
    from services import r2_storage
    store = {}
    monkeypatch.setattr(r2_storage, "archive_enabled", lambda: True)

    def _put(key, data, content_type="application/octet-stream"):
        store[key.lstrip("/")] = (bytes(data), content_type)

    def _get(key):
        return store.get(key.lstrip("/"), (None, None))

    monkeypatch.setattr(r2_storage, "archive_put", _put)
    monkeypatch.setattr(r2_storage, "archive_get", _get)
    return store


@pytest.fixture
def id_respx():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def id_env(monkeypatch, id_outbox, id_respx):
    """Clean db + caches + rate limits; outbound fully mocked."""
    for k in ("ADMIN_MFA_ENFORCE", "ADMIN_MFA_CHANNEL_POLICY", "TWO_PERSON_RULE", "ALLOW_DIRECT_STAFF_CREATE",
              "STAFF_IDLE_TIMEOUT", "IBAN_COOLING_OFF_MINUTES", "STAFF_TOKEN_TTL_HOURS", "RESEND_FROM",
              "ALERT_TO_EMAIL", "VAULT_SUPER_EMAILS"):
        monkeypatch.delenv(k, raising=False)
    from security import alerts
    monkeypatch.setattr(alerts, "RESEND_API_KEY", None)
    monkeypatch.setattr(alerts, "SMTP_HOST", None)
    monkeypatch.setattr(alerts, "_deliver", _REAL_ALERT_DELIVER)
    monkeypatch.setattr(alerts, "resolve_default_recipients", _REAL_ALERT_RECIPIENTS)
    id_run(_wipe_db())
    _reset_module_caches()
    yield {"outbox": id_outbox, "respx": id_respx}
    _reset_module_caches()


@pytest.fixture
def id_client(id_env):
    return TestClient(server.app, raise_server_exceptions=False)


# ── seeding helpers ──────────────────────────────────────────────────────────────────────────
async def _insert(coll, doc):
    await deps.db[coll].insert_one(dict(doc))
    return doc


def id_seed_role(role_id: str, permissions, name: str = "") -> dict:
    return id_run(_insert("roles", {"id": role_id, "name": name or role_id, "permissions": list(permissions),
                                    "is_system": False}))


_SEQ = {"n": 0}


def id_seed_user(*, kind: str = "customer", email: str = "", password: str = ID_PASSWORD, role_id: str = "",
                 phone: str = "", **extra) -> dict:
    """kind: customer | staff (role_id) | super."""
    _SEQ["n"] += 1
    n = _SEQ["n"]
    doc = {"id": f"u-{kind}-{n}", "email": (email or f"{kind}{n}@example.test").lower(),
           "password": id_hash(password), "first_name": "Test", "last_name": f"User{n}",
           "is_active": True, "token_version": 0, "created_at": "2026-01-01T00:00:00+00:00"}
    if kind == "staff":
        doc.update({"is_admin": True, "role_id": role_id or "r_basic", "created_by": "seed"})
    elif kind == "super":
        doc.update({"is_admin": True, "is_super_admin": True, "created_by": "seed"})
    else:
        doc.update({"is_admin": False})
    if phone:
        doc["phone"] = phone
    doc.update(extra)
    return id_run(_insert("users", doc))


def id_token(user: dict, *, recent: bool = True, auth_time=None) -> str:
    at = auth_time if auth_time is not None else (deps.now_ts() if recent else None)
    return deps.create_token(user["id"], bool(user.get("is_admin")), token_version=user.get("token_version", 0),
                             auth_time=at)


def id_auth(token: str, ip: str = "") -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if ip:
        h["cf-connecting-ip"] = ip
    return h


def id_ip(ip: str) -> dict:
    return {"cf-connecting-ip": ip}


def id_find(coll: str, q: dict, proj=None):
    return id_run(deps.db[coll].find_one(q, proj or {"_id": 0}))


def id_findall(coll: str, q: dict = None):
    async def _f():
        return await deps.db[coll].find(q or {}, {"_id": 0}).to_list(10000)
    return id_run(_f())


def id_update(coll: str, q: dict, upd: dict):
    return id_run(deps.db[coll].update_one(q, upd))


def id_audit_actions(prefix: str = "") -> list:
    return [r.get("action") for r in id_findall("audit_logs") if str(r.get("action") or "").startswith(prefix)]


def id_set_rule(key: str, value):
    import business_rules

    async def _s():
        await business_rules.save_rules(deps.db, {key: value})
    id_run(_s())
    business_rules.invalidate()


class IdAuditBreaker:
    """Makes insert_one on the given audit collections raise (simulates an unavailable audit store).
    Default: the tamper-evident admin audit chain (audit_logs). Pass also "auth_audit_logs" to take
    down the auth/security event log as well. Other collections keep working."""

    def __init__(self, monkeypatch, names=("audit_logs",)):
        coll = deps.db.audit_logs
        cls = type(coll)
        orig = cls.insert_one
        broken = set(names)

        async def _boom(self_, doc, *a, **k):
            if getattr(self_, "name", "") in broken:
                raise RuntimeError("audit store down (test)")
            return await orig(self_, doc, *a, **k)
        monkeypatch.setattr(cls, "insert_one", _boom)


@pytest.fixture
def id_break_audit(monkeypatch):
    return lambda *names: IdAuditBreaker(monkeypatch, names or ("audit_logs",))
