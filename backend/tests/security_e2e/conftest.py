"""Shared fixtures for the security end-to-end suite (backend/tests/security_e2e).

Everything runs against the REAL FastAPI app (`server.app`) with:
  * MongoDB  -> mongomock_motor (in-memory; never a real database)
  * R2       -> in-memory fake S3 client (public bucket + private archive bucket)
  * e-mail / SMS / push / SES -> mocks (nothing leaves the process)
  * outbound sockets -> blocked except loopback (safety net for "never touch external services")

The app lifespan (scheduler start, admin bootstrap, index creation) is NOT executed: TestClient
is used without its context manager.

Fixtures are prefixed with ``kv_``.
"""
from __future__ import annotations

import asyncio
import io
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# ───────────────────────────── environment (before ANY backend import) ─────────────────────────────
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["MONGO_URL"] = "mongodb://kv-test.invalid:27017"
os.environ["DB_NAME"] = "kv_security_e2e"
os.environ["JWT_SECRET"] = "kv-test-jwt-secret-" + "x" * 48
os.environ.setdefault("SECRETS_MASTER_KEY", "")
for _k in ("CF_EDGE_SECRET", "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "RESEND_API_KEY",
           "ALERT_TO_EMAIL", "RESTORE_MODE", "KVKK_TOMBSTONE_SECRET", "ADMIN_INITIAL_PASSWORD"):
    os.environ.pop(_k, None)
# Fake R2 credentials: only consumed by the in-memory fake client below.
os.environ.update({
    "R2_ENDPOINT": "https://r2.kv-test.invalid",
    "R2_ACCESS_KEY_ID": "kv-test-key",
    "R2_SECRET_ACCESS_KEY": "kv-test-secret",
    "R2_BUCKET": "kv-public",
    "R2_PUBLIC_URL": "https://cdn.kv-test.invalid",
    "R2_ARCHIVE_BUCKET": "kv-archive",
})

import motor.motor_asyncio  # noqa: E402
import mongomock_motor  # noqa: E402

motor.motor_asyncio.AsyncIOMotorClient = mongomock_motor.AsyncMongoMockClient

# ───────────────────────────── outbound network guard ─────────────────────────────
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_GETADDRINFO = socket.getaddrinfo
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}


class KvBlockedNetwork(OSError):
    pass


def _is_loopback_addr(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX
        return True
    try:
        host = address[0]
    except Exception:
        return False
    return str(host) in ("127.0.0.1", "::1", "localhost") or str(host).startswith("127.")


def _guard_connect(self, address):
    if not _is_loopback_addr(address):
        raise KvBlockedNetwork(f"kv-test: outbound connection blocked to {address!r}")
    return _REAL_CONNECT(self, address)


def _guard_connect_ex(self, address):
    if not _is_loopback_addr(address):
        raise KvBlockedNetwork(f"kv-test: outbound connection blocked to {address!r}")
    return _REAL_CONNECT_EX(self, address)


def _guard_getaddrinfo(host, *a, **k):
    h = host.decode() if isinstance(host, bytes) else str(host or "")
    if h in _LOCAL_NAMES or h.startswith("127."):
        return _REAL_GETADDRINFO(host, *a, **k)
    raise socket.gaierror(socket.EAI_NONAME, f"kv-test: DNS blocked for {h!r}")


socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex
socket.getaddrinfo = _guard_getaddrinfo


# ───────────────────────────── fake R2 (S3) ─────────────────────────────
class KvFakeS3:
    """Minimal boto3 S3 client replacement: {bucket: {key: (bytes, content_type)}}."""

    def __init__(self):
        self.buckets: dict = {}

    def reset(self):
        self.buckets.clear()

    def bucket(self, name):
        return self.buckets.setdefault(name, {})

    def put_object(self, Bucket, Key, Body, ContentType="application/octet-stream", **_):
        data = Body if isinstance(Body, (bytes, bytearray)) else Body.read()
        self.bucket(Bucket)[Key] = (bytes(data), ContentType)
        return {"ETag": uuid.uuid4().hex}

    def get_object(self, Bucket, Key, **_):
        from botocore.exceptions import ClientError
        try:
            data, ct = self.bucket(Bucket)[Key]
        except KeyError:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        return {"Body": io.BytesIO(data), "ContentType": ct}

    def head_bucket(self, Bucket, **_):
        self.bucket(Bucket)
        return {}

    def delete_object(self, Bucket, Key, **_):
        self.bucket(Bucket).pop(Key, None)
        return {}

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, ContinuationToken=None, **_):
        keys = sorted(k for k in self.bucket(Bucket) if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + MaxKeys]
        out = {"Contents": [{"Key": k, "Size": len(self.bucket(Bucket)[k][0])} for k in page],
               "IsTruncated": start + MaxKeys < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + MaxKeys)
        return out


KV_S3 = KvFakeS3()


# ───────────────────────────── app import (once) ─────────────────────────────
_APP_STATE: dict = {}


def _import_app():
    if "server" in _APP_STATE:
        return _APP_STATE["server"]
    import server  # noqa: WPS433
    from services import r2_storage
    r2_storage._client = KV_S3
    r2_storage._get_client = lambda: KV_S3
    _APP_STATE["server"] = server
    return server


@pytest.fixture(scope="session")
def kv_server():
    return _import_app()


@pytest.fixture(scope="session")
def kv_app(kv_server):
    return kv_server.app


@pytest.fixture(scope="session")
def kv_db(kv_server):
    from routes.deps import db
    return db


def kv_run(coro):
    """Run a coroutine to completion on a fresh event loop (mongomock_motor is loop-agnostic)."""
    return asyncio.run(coro)


@pytest.fixture
def kv_arun():
    return kv_run


# ───────────────────────────── per-test isolation ─────────────────────────────
@pytest.fixture(autouse=True)
def kv_isolation(kv_server, kv_db, monkeypatch):
    """Fresh DB, fresh caches, fresh rate-limit counters, all outbound messaging mocked."""
    async def _wipe():
        for name in await kv_db.list_collection_names():
            await kv_db[name].delete_many({})
    kv_run(_wipe())
    KV_S3.reset()
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "_client", KV_S3, raising=False)
    monkeypatch.setattr(r2_storage, "_get_client", lambda: KV_S3)

    # caches
    import security_switches
    security_switches._cache.update({"at": 0.0, "switches": None})
    import routes.deps as deps
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    deps._IDLE_SEEN.clear()
    deps._MEMBER_CACHE.clear()
    try:
        import routes.products as _rp
        _rp._FEED_CACHE.clear()
        _rp._FEED_LOCKS.clear()
        _rp.invalidate_members_only_cache()
        import routes.coupons as _cp
        _cp._CODES_CACHE.update({"rows": [], "at": 0.0})
        _cp._EXEMPT_CACHE.update({"emails": set(), "at": 0.0})
        from security import ai_budget as _aib
        _aib._ACTIVE.clear()
    except Exception:
        pass
    try:
        deps.limiter.reset()
    except Exception:
        pass
    try:
        import business_rules
        business_rules._cache = {}
        business_rules._cache_at = 0.0
    except Exception:
        pass
    try:
        import company
        company.invalidate()
    except Exception:
        pass

    # outbound messaging → mocks (recorded on the namespace for assertions)
    sent = SimpleNamespace(smtp=AsyncMock(return_value={"success": True}),
                           notify=AsyncMock(return_value={"success": True}),
                           welcome=AsyncMock(return_value=None),
                           alert=AsyncMock(return_value={"to": "x", "smtp": True, "resend": False,
                                                         "ok": True, "error": ""}))
    import email_smtp
    monkeypatch.setattr(email_smtp, "send_smtp_email", sent.smtp)
    import notification_service
    monkeypatch.setattr(notification_service, "send_notification", sent.notify)
    try:
        import welcome_mail
        monkeypatch.setattr(welcome_mail, "send_welcome_email", sent.welcome)
    except Exception:
        pass
    import security.alerts as _alerts
    monkeypatch.setattr(_alerts, "_deliver", sent.alert)
    monkeypatch.setattr(_alerts, "resolve_default_recipients", AsyncMock(return_value=["sec@example.invalid"]))
    yield sent


# ───────────────────────────── users / tokens ─────────────────────────────
@pytest.fixture
def kv_client(kv_app):
    from fastapi.testclient import TestClient
    return TestClient(kv_app)  # NOT a context manager → lifespan/scheduler never starts


def _hash(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


class KvUsers:
    def __init__(self, db):
        self.db = db

    def customer(self, email=None, password="Passw0rd!x", **extra) -> dict:
        uid = str(uuid.uuid4())
        doc = {"id": uid, "email": (email or f"c-{uid[:8]}@example.com").lower(),
               "password": _hash(password), "first_name": "Ayse", "last_name": "Yilmaz",
               "phone": "05551112233", "is_admin": False, "is_active": True, "email_verified": True,
               "created_at": datetime.now(timezone.utc).isoformat(), "token_version": 0}
        doc.update(extra)
        kv_run(self.db.users.insert_one(dict(doc)))
        doc["_password_plain"] = password
        return doc

    def staff(self, perms=None, super_admin=False, email=None, **extra) -> dict:
        uid = str(uuid.uuid4())
        doc = {"id": uid, "email": (email or f"s-{uid[:8]}@example.com").lower(),
               "password": _hash("Staff-Passw0rd!"), "first_name": "Per", "last_name": "Sonel",
               "is_admin": True, "is_active": True, "created_at": datetime.now(timezone.utc).isoformat(),
               "token_version": 0}
        if super_admin:
            doc["is_super_admin"] = True
        else:
            rid = "role-" + uid[:8]
            kv_run(self.db.roles.insert_one({"id": rid, "name": "kv role", "permissions": list(perms or [])}))
            doc["role_id"] = rid
        doc.update(extra)
        kv_run(self.db.users.insert_one(dict(doc)))
        return doc

    @staticmethod
    def token(user: dict) -> str:
        from routes.deps import create_token, now_ts
        return create_token(user["id"], is_admin=bool(user.get("is_admin")),
                            token_version=int(user.get("token_version") or 0), auth_time=now_ts())

    @classmethod
    def hdr(cls, user: dict) -> dict:
        return {"Authorization": f"Bearer {cls.token(user)}"}


@pytest.fixture
def kv_users(kv_db):
    return KvUsers(kv_db)


@pytest.fixture
def kv_s3():
    return KV_S3


def kv_ip() -> dict:
    """Unique client IP header (rate-limit keys never collide between tests)."""
    import random
    return {"cf-connecting-ip": f"203.0.113.{random.randint(1, 254)}",
            "x-kv-ip": uuid.uuid4().hex}


@pytest.fixture
def kv_unique_ip():
    import random
    n = random.randint(1, 250)
    return {"cf-connecting-ip": f"198.51.100.{n}"}


# ───────────────────────────── identity suite (fixtures prefixed ``id_``) ─────────────────────────────
# Kept in id_harness.py; imported here so pytest discovers them. The id_env fixture restores the REAL
# alert dispatcher (security.alerts._deliver / resolve_default_recipients) for identity tests only and
# mocks Resend at the HTTP layer with respx instead.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from id_harness import (  # noqa: E402,F401
    id_outbox, id_archive, id_respx, id_env, id_client, id_break_audit,
)

# ───────────────────────────── payment / order suite (fixtures prefixed ``pay_``) ─────────────────────────────
# Kept in pay_harness.py (async httpx client over ASGI, iyzico via respx, mongomock fidelity shims).
from pay_harness import (  # noqa: E402,F401
    pay_env, pay_http, pay_archive_on,
)
