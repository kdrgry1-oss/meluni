"""Extra helpers for the kv_* security e2e tests (KVKK / data / infra).

Fixtures here are opt-in (imported by the kv test modules), so they never affect other suites in
this directory. Base fixtures (kv_client, kv_users, kv_db, kv_s3, kv_isolation…) live in conftest.py.
"""
from __future__ import annotations

import asyncio
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import KV_S3, kv_run, _REAL_CONNECT, _REAL_CONNECT_EX  # noqa: F401

R2_ENV = {
    "R2_ENDPOINT": "https://r2.kv-test.invalid",
    "R2_ACCESS_KEY_ID": "kv-test-key",
    "R2_SECRET_ACCESS_KEY": "kv-test-secret",
    "R2_BUCKET": "kv-public",
    "R2_PUBLIC_URL": "https://cdn.kv-test.invalid",
    "R2_ARCHIVE_BUCKET": "kv-archive",
}


@pytest.fixture
def kv_r2(monkeypatch):
    """Enable R2 (public + private archive bucket) backed by the in-memory fake client."""
    for k, v in R2_ENV.items():
        monkeypatch.setenv(k, v)
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "_client", KV_S3, raising=False)
    monkeypatch.setattr(r2_storage, "_get_client", lambda: KV_S3)
    return KV_S3


@pytest.fixture
def kv_mongomock_fam_fidelity(monkeypatch):
    """mongomock bug: find_one_and_update() treats a matched document whose PROJECTION is empty ({})
    as "no match" (no update, returns None). Real MongoDB updates and returns {}. Patch for the test."""
    from mongomock import collection as _mc
    from pymongo import ReturnDocument

    def _fam(self, query, projection=None, update=None, upsert=False, sort=None,
             return_document=ReturnDocument.BEFORE, session=None, **kwargs):
        remove = kwargs.get("remove", False)
        if not (remove or update):
            raise ValueError("Must either update or remove")
        old = self.find_one(query, projection=projection, sort=sort)
        if old is None and not upsert:
            return None
        raw = self.find_one(query, sort=sort)
        if raw is not None:
            query = {"_id": raw["_id"]}
        if remove:
            self.delete_one(query)
        else:
            updated = self._update(query, update, upsert)
            if updated["upserted"]:
                query = {"_id": updated["upserted"]}
        if return_document is ReturnDocument.AFTER or kwargs.get("new"):
            return self.find_one(query, projection)
        return old

    monkeypatch.setattr(_mc.Collection, "_find_and_modify", _fam)


@pytest.fixture
def kv_mongomock_convert(monkeypatch):
    """mongomock does not implement the aggregation `$convert` operator (used by the storefront
    product listing). Minimal implementation (to: int/long/double/decimal/string, onError, onNull)."""
    from mongomock import aggregate as _ag
    real = _ag._Parser._handle_type_convertion_operator

    def _h(self, operator, values):
        if operator != "$convert":
            return real(self, operator, values)
        spec = values or {}
        try:
            v = self.parse(spec.get("input"))
        except KeyError:
            v = None
        if v is None:
            return self.parse(spec["onNull"]) if "onNull" in spec else None
        to = str(spec.get("to") or "").lower()
        try:
            if to in ("int", "long"):
                return int(float(v))
            if to in ("double", "decimal"):
                return float(v)
            if to == "string":
                return str(v)
            if to == "bool":
                return bool(v)
            return v
        except (TypeError, ValueError):
            if "onError" in spec:
                return self.parse(spec["onError"])
            raise

    monkeypatch.setattr(_ag._Parser, "_handle_type_convertion_operator", _h)


def _loopback_only(real):
    def _c(self, address):
        if isinstance(address, (str, bytes)):
            return real(self, address)
        host = str(address[0])
        if host == "127.0.0.1" or host == "::1" or host.startswith("127."):
            return real(self, address)
        raise OSError(f"kv-test: outbound connection blocked to {address!r}")
    return _c


@pytest.fixture
def kv_loopback(monkeypatch):
    """Allow TCP connections to loopback ONLY (for the local HTTP server used by SSRF tests)."""
    monkeypatch.setattr(socket.socket, "connect", _loopback_only(_REAL_CONNECT))
    monkeypatch.setattr(socket.socket, "connect_ex", _loopback_only(_REAL_CONNECT_EX))
    yield


# ───────────────────────────── db helpers ─────────────────────────────
def kv_db():
    from routes.deps import db
    return db


def kv_insert(coll: str, doc: dict) -> dict:
    kv_run(kv_db()[coll].insert_one(dict(doc)))
    return doc


def kv_find(coll: str, q: dict, proj=None):
    return kv_run(kv_db()[coll].find_one(q, proj or {"_id": 0}))


def kv_findall(coll: str, q: dict = None) -> list:
    async def _f():
        return await kv_db()[coll].find(q or {}, {"_id": 0}).to_list(100000)
    return kv_run(_f())


def kv_count(coll: str, q: dict = None) -> int:
    return kv_run(kv_db()[coll].count_documents(q or {}))


def kv_set_rule(key: str, value):
    import business_rules

    async def _s():
        await business_rules.save_rules(kv_db(), {key: value})
    kv_run(_s())
    business_rules._cache = {}
    business_rules._cache_at = 0.0


def kv_iso(days: float = 0.0, hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def kv_order(user: dict = None, **extra) -> dict:
    oid = str(uuid.uuid4())
    o = {
        "id": oid, "order_number": "FC" + oid[:6].upper(), "user_id": (user or {}).get("id"),
        "status": "delivered", "payment_status": "paid", "payment_method": "credit_card",
        "platform": "meluni", "total": 500.0, "subtotal": 500.0, "created_at": kv_iso(-40),
        "delivered_at": kv_iso(-35), "invoice_number": "INV2026000001", "invoice_issued": True,
        "email": (user or {}).get("email", ""), "phone": (user or {}).get("phone", ""),
        "items": [{"product_id": "p1", "product_name": "Elbise", "size": "M", "quantity": 1, "price": 500.0}],
        "shipping_address": {"first_name": "Ayse", "last_name": "Yilmaz", "address": "Moda Cd. 1",
                             "city": "Istanbul", "phone": (user or {}).get("phone", ""),
                             "email": (user or {}).get("email", "")},
    }
    o.update(extra)
    return kv_insert("orders", o)


def kv_json(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def kv_all_keys(obj) -> set:
    """Every dict key anywhere inside a JSON-like structure."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= kv_all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= kv_all_keys(v)
    return out


def tr_iban(bban22: str) -> str:
    """Valid TR IBAN (mod-97) for a 22-digit BBAN."""
    assert len(bban22) == 22 and bban22.isdigit()
    num = int(bban22 + "2927" + "00")
    check = 98 - (num % 97)
    return f"TR{check:02d}{bban22}"


def kv_await(coro):
    return asyncio.run(coro)
