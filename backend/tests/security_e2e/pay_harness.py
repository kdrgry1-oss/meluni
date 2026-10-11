"""Payment / order end-to-end harness (fixtures prefixed ``pay_``).

Imported by conftest.py AFTER id_harness (which sets env, swaps Motor for mongomock_motor, blocks
raw sockets and imports ``server``). This module adds what the payment suite needs:

* async in-process HTTP client (httpx.AsyncClient + ASGITransport) so that background tasks the
  app spawns (``routes.orders._spawn`` — notification mails, admin push) can be DRAINED and their
  side effects asserted deterministically (TestClient would cancel them with its per-request loop);
* recorded outbox for customer notifications, admin push and admin e-mails;
* iyzico mocked with respx (sandbox base URL derived from settings.iyzico.mode);
* Doğan client / MNG client mocked; private R2 archive in memory;
* mongomock fidelity shims (applied per test via monkeypatch, never globally):
    - ``$sum`` over a single array expression (``{"$sum": {"$map": ...}}``) — mongomock iterated
      the dict keys and returned 0, which silently disables the iyzico refund cap ``$expr`` and
      the pipeline stock recompute;
    - ``array_filters`` (``variants.$[v].stock``) — mongomock raises NotImplementedError;
    - every collection coroutine yields to the loop once before executing, so ``asyncio.gather``
      really interleaves concurrent requests at DB-operation granularity (each single op stays
      atomic, exactly like a single-document MongoDB write).
Nothing here talks to a real database or external service.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

import id_harness  # noqa: F401  (bootstrap: env, mock Mongo, blocked sockets, server import)

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import respx  # noqa: E402

import server  # noqa: E402
from routes import deps  # noqa: E402

IYZICO_BASE = "https://sandbox-api.iyzipay.com"
IYZICO_SECRET = "pay-e2e-iyzico-secret-0123456789"
IYZICO_KEY = "pay-e2e-iyzico-key"
CALLBACK_URL = "https://www.meluni.com.tr/api/payment/3ds/callback"
CARD = {"cardHolderName": "Test Kart", "cardNumber": "5528790000000008",
        "expireMonth": "12", "expireYear": "2030", "cvc": "123"}
SHIP = {"first_name": "Ayse", "last_name": "Test", "email": "buyer@example.test",
        "phone": "05551234567", "city": "Istanbul", "district": "Kadikoy",
        "address": "Test Mah. Deneme Sok. No:1", "postal_code": "34000"}
PRODUCT_PRICE = 500.0
SHIPPING_FEE = 50.0
CARD_TOTAL = PRODUCT_PRICE + SHIPPING_FEE                 # 550.00
HAVALE_TOTAL = PRODUCT_PRICE * 0.95 + SHIPPING_FEE        # 525.00 (5 % bank-transfer discount)


# ════════════════════════════════════════ mongomock shims ════════════════════════════════════
def _install_mongomock_shims(monkeypatch):
    import mongomock.aggregate as _agg
    import mongomock.collection as _mc
    from mongomock import filtering as _flt

    # 1) $sum / $avg ... with ONE expression operand that evaluates to an array.
    _orig_project = _agg._Parser._handle_project_operator

    def _project(self, operator, values):
        if operator in _agg._GROUPING_OPERATOR_MAP and isinstance(values, dict):
            v = self.parse(values)
            return _agg._GROUPING_OPERATOR_MAP[operator](v if isinstance(v, list) else [v])
        return _orig_project(self, operator, values)

    monkeypatch.setattr(_agg._Parser, "_handle_project_operator", _project)

    # 1b) $toDouble (MongoDB ≥4.0) — unknown to mongomock ("Unrecognized expression").
    if "$toDouble" not in _agg.type_convertion_operators:
        monkeypatch.setattr(_agg, "type_convertion_operators",
                            list(_agg.type_convertion_operators) + ["$toDouble"])
    _orig_conv = _agg._Parser._handle_type_convertion_operator

    def _conv(self, operator, values):
        if operator == "$toDouble":
            try:
                v = self.parse(values)
            except KeyError:
                return None
            return None if v is None else float(v)
        return _orig_conv(self, operator, values)

    monkeypatch.setattr(_agg._Parser, "_handle_type_convertion_operator", _conv)

    # 2) array_filters → concrete positional paths, then delegate to mongomock.
    _orig_update = _mc.Collection._update

    def _elem_filter(ident, array_filters):
        out = {}
        for af in array_filters or []:
            for k, v in af.items():
                if k == ident:
                    out["__self__"] = v
                elif k.startswith(ident + "."):
                    out[k[len(ident) + 1:]] = v
        return out

    def _expand(node, parts, array_filters):
        if not parts:
            return [[]]
        head, rest = parts[0], parts[1:]
        if head.startswith("$[") and head.endswith("]"):
            ident = head[2:-1]
            if not isinstance(node, list):
                return []
            flt = _elem_filter(ident, array_filters) if ident else {}
            res = []
            for i, el in enumerate(node):
                ok = True
                if flt:
                    if "__self__" in flt:
                        ok = _flt.filter_applies({"x": flt["__self__"]}, {"x": el})
                    sub = {k: v for k, v in flt.items() if k != "__self__"}
                    if ok and sub:
                        ok = isinstance(el, dict) and _flt.filter_applies(sub, el)
                if ok:
                    res += [[str(i)] + tail for tail in _expand(el, rest, array_filters)]
            return res
        nxt = None
        if isinstance(node, dict):
            nxt = node.get(head)
        elif isinstance(node, list) and head.isdigit() and int(head) < len(node):
            nxt = node[int(head)]
        return [[head] + tail for tail in _expand(nxt, rest, array_filters)]

    def _update(self, spec, document, upsert=False, manipulate=False, multi=False,
                check_keys=False, hint=None, session=None, collation=None, let=None,
                array_filters=None, **kwargs):
        if not array_filters or isinstance(document, list):
            return _orig_update(self, spec, document, upsert=upsert, manipulate=manipulate,
                                multi=multi, check_keys=check_keys, hint=hint, session=session,
                                collation=collation, let=let, **kwargs)
        ids = [d["_id"] for d in self._iter_documents(spec)]
        if not multi:
            ids = ids[:1]
        total = {"n": 0, "nModified": 0, "ok": 1, "upserted": None, "updatedExisting": False,
                 "err": None, "connectionId": self.database.client._id}
        for _id in ids:
            doc = next(iter(self._iter_documents({"_id": _id})))
            concrete = {}
            for op, fields in document.items():
                out = {}
                for path, val in (fields or {}).items():
                    if "$[" in path:
                        for cp in _expand(doc, path.split("."), array_filters):
                            out[".".join(cp)] = val
                    else:
                        out[path] = val
                if out:
                    concrete[op] = out
            if not concrete:
                total["n"] += 1
                continue
            r = _orig_update(self, {"_id": _id}, concrete, multi=False)
            total["n"] += r.get("n", 0)
            total["nModified"] += r.get("nModified", 0)
        total["updatedExisting"] = total["n"] > 0
        return total

    monkeypatch.setattr(_mc.Collection, "_update", _update)

    # 3) Yield once per collection coroutine → real interleaving under asyncio.gather.
    coll_cls = type(deps.db.orders)
    for name in ("count_documents", "delete_many", "delete_one", "find_one",
                 "find_one_and_delete", "find_one_and_replace", "find_one_and_update",
                 "insert_many", "insert_one", "replace_one", "update_many", "update_one"):
        orig = getattr(coll_cls, name)

        def _mk(orig):
            async def _yielding(self, *a, **k):
                await asyncio.sleep(0)
                return await orig(self, *a, **k)
            return _yielding
        monkeypatch.setattr(coll_cls, name, _mk(orig))


# ════════════════════════════════════════ outbox ═════════════════════════════════════════════
class PayOutbox:
    def __init__(self):
        self.notifications = []   # notification_service.send_notification (customer SMS/mail)
        self.pushes = []          # routes.push.send_push_to_admins
        self.emails = []          # email_smtp.send_smtp_email (admin order mail, etc.)
        self.mng = []             # mng_kargo_client.create_shipment

    def events(self, name=None):
        return [n for n in self.notifications if name is None or n["event"] == name]

    def new_order_pushes(self, order_id=None):
        return [p for p in self.pushes if (p.get("data") or {}).get("type") == "new_order"
                and (order_id is None or (p.get("data") or {}).get("order_id") == order_id)]

    def admin_order_mails(self, order_number=None):
        return [e for e in self.emails if str(e.get("subject", "")).startswith("Yeni Sipariş")
                and (order_number is None or order_number in e.get("subject", ""))]


def _patch_everywhere(monkeypatch, module_name, attr, replacement):
    mod = __import__(module_name, fromlist=["_"])
    orig = getattr(mod, attr)
    monkeypatch.setattr(mod, attr, replacement)
    for m in list(sys.modules.values()):
        try:
            if m is not None and m is not mod and getattr(m, attr, None) is orig:
                monkeypatch.setattr(m, attr, replacement)
        except Exception:
            pass


# ════════════════════════════════════════ helpers ════════════════════════════════════════════
async def pay_drain(rounds: int = 5):
    """Wait for every background task the app spawned (notifications, push, CAPI)."""
    me = asyncio.current_task()
    for _ in range(rounds):
        pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=5)


def now_iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


async def pay_find(coll, q, proj=None):
    return await deps.db[coll].find_one(q, proj or {"_id": 0})


async def pay_findall(coll, q=None):
    return await deps.db[coll].find(q or {}, {"_id": 0}).to_list(10000)


async def pay_set(coll, q, fields):
    return await deps.db[coll].update_one(q, {"$set": fields})


async def pay_set_switch(name, on=True):
    import security_switches
    await deps.db.settings.update_one(
        {"id": "security_switches"},
        {"$set": {"id": "security_switches", f"switches.{name}": {"on": bool(on), "reason": "test"}}},
        upsert=True)
    security_switches.invalidate_cache()


async def pay_set_rule(key, value):
    import business_rules
    await deps.db.settings.update_one({"id": "business_rules"},
                                      {"$set": {"id": "business_rules", f"values.{key}": value}},
                                      upsert=True)
    business_rules.invalidate()


async def pay_seed_product(pid="p1", stock=5, price=PRODUCT_PRICE, barcode=None, **extra):
    doc = {"id": pid, "name": f"Urun {pid}", "price": price, "stock": stock,
           "barcode": barcode or f"BC-{pid}", "is_active": True, "variants": [],
           "category_id": "cat1", "created_at": now_iso(days=-30)}
    doc.update(extra)
    await deps.db.products.insert_one(dict(doc))
    return doc


async def pay_stock(pid="p1"):
    return int((await pay_find("products", {"id": pid}, {"_id": 0, "stock": 1}) or {}).get("stock") or 0)


_SEQ = {"n": 0}


async def pay_seed_role(role_id, perms):
    await deps.db.roles.insert_one({"id": role_id, "name": role_id, "permissions": list(perms)})


async def pay_staff(perms=None, *, super_admin=False, email=None):
    _SEQ["n"] += 1
    n = _SEQ["n"]
    uid = f"pay-staff-{n}"
    doc = {"id": uid, "email": email or f"staff{n}@example.test", "is_active": True, "is_admin": True,
           "token_version": 0, "first_name": "Staff", "last_name": str(n)}
    if super_admin:
        doc["is_super_admin"] = True
    else:
        rid = f"pay-role-{n}"
        await pay_seed_role(rid, perms or [])
        doc["role_id"] = rid
    await deps.db.users.insert_one(dict(doc))
    tok = deps.create_token(uid, True, token_version=0, auth_time=deps.now_ts())
    return doc, {"Authorization": f"Bearer {tok}"}


async def pay_customer(email="member@example.test", verified=True):
    _SEQ["n"] += 1
    uid = f"pay-cust-{_SEQ['n']}"
    doc = {"id": uid, "email": email, "email_verified": verified, "is_active": True,
           "is_admin": False, "token_version": 0, "role": "customer"}
    await deps.db.users.insert_one(dict(doc))
    tok = deps.create_token(uid, False, token_version=0)
    return doc, {"Authorization": f"Bearer {tok}"}


def order_payload(pm="credit_card", items=None, ship=None, **extra):
    body = {"items": items if items is not None else [{"product_id": "p1", "quantity": 1, "price": 1}],
            "shipping_address": dict(SHIP if ship is None else ship), "payment_method": pm}
    body.update(extra)
    return body


# ────────────────────────────── iyzico response builders ─────────────────────────────────────
def iyz_auth_ok(order, *, payment_id="pid-1", paid=None, currency="TRY", basket=None, conv=None,
                drop=(), **extra):
    d = {"status": "success", "paymentId": payment_id, "conversationId": conv or order["id"],
         "basketId": basket if basket is not None else order["order_number"],
         "currency": currency, "paidPrice": f"{(paid if paid is not None else order['total']):.2f}",
         "price": f"{order['total']:.2f}", "fraudStatus": 1, "installment": 1,
         "binNumber": "552879", "lastFourDigits": "0008"}
    d.update(extra)
    for k in drop:
        d.pop(k, None)
    return d


def iyz_json(data, status=200):
    return httpx.Response(status, json=data)


def webhook_signature(payload, secret=IYZICO_SECRET):
    msg = secret + "".join(str(payload.get(k) or "") for k in
                           ("iyziEventType", "iyziPaymentId", "token", "paymentConversationId", "status"))
    return base64.b64encode(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()


# ════════════════════════════════════════ fixtures ═══════════════════════════════════════════
class PayEnv:
    def __init__(self, outbox, router, archive):
        self.outbox = outbox
        self.iyzico = router
        self.archive = archive
        self.dogan = None


async def _wipe():
    for name in await deps.db.list_collection_names():
        await deps.db.drop_collection(name)


def _reset_caches():
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    deps._IDLE_SEEN.clear()
    deps._MEMBER_CACHE.clear()
    import business_rules
    business_rules.invalidate()
    business_rules._cache = {}
    import security_switches
    security_switches._cache.update({"at": 0.0, "switches": None})
    try:
        import company
        for _n in ("_cache", "_CACHE"):
            c = getattr(company, _n, None)
            if isinstance(c, dict):
                c.clear()
    except Exception:
        pass
    try:
        from routes import coupons
        coupons._EXEMPT_CACHE.update({"emails": set(), "at": 0.0})
    except Exception:
        pass


async def _seed_base():
    from security.crypto import encrypt
    await deps.db.settings.insert_many([
        {"id": "iyzico", "api_key": IYZICO_KEY, "api_secret": encrypt(IYZICO_SECRET),
         "mode": "sandbox", "is_active": True},
        {"id": "main", "payment_methods": {"credit_card": True, "bank_transfer": True,
                                           "cash_on_delivery": False},
         "shipping_fee": SHIPPING_FEE, "free_shipping_threshold": 100000,
         "bank_transfer_discount_pct": 5, "maintenance_mode": False},
        {"id": "payment", "bank_accounts": [{"bank_name": "Test Bank", "iban": "TR000000000000000000000001",
                                             "account_holder": "Test AS", "is_default": True}]},
        # The status config enables customer mail on the states we assert on.
    ])


@pytest.fixture
def pay_env(monkeypatch):
    outbox = PayOutbox()

    async def _notify(db, event, *a, **k):
        outbox.notifications.append({"event": event, **k})
        return {"success": True}

    async def _smtp(db, to, subject, html, *a, **k):
        outbox.emails.append({"to": to, "subject": subject})
        return {"success": True}

    async def _push(title, body, data=None):
        outbox.pushes.append({"title": title, "body": body, "data": data or {}})
        return 1

    _patch_everywhere(monkeypatch, "notification_service", "send_notification", _notify)
    _patch_everywhere(monkeypatch, "email_smtp", "send_smtp_email", _smtp)
    import routes.push as _push_mod
    monkeypatch.setattr(_push_mod, "send_push_to_admins", _push)

    import mng_kargo_client

    def _mng_create(**kw):
        outbox.mng.append(kw)
        return {"ok": False}
    monkeypatch.setattr(mng_kargo_client, "create_shipment", _mng_create, raising=False)

    # In-memory private R2 archive (+ public bucket disabled).
    from services import r2_storage
    archive = {}
    monkeypatch.setattr(r2_storage, "is_enabled", lambda: False)
    monkeypatch.setattr(r2_storage, "archive_enabled", lambda: False)
    monkeypatch.setattr(r2_storage, "archive_health", lambda: {"ok": True})

    def _aput(key, data, content_type="application/octet-stream"):
        archive[key] = (bytes(data), content_type)

    def _aget(key):
        return archive.get(key, (None, None))
    monkeypatch.setattr(r2_storage, "archive_put", _aput)
    monkeypatch.setattr(r2_storage, "archive_get", _aget)

    # Rate limiting is not what these tests measure (the guest-verify lock has its own 429).
    monkeypatch.setattr(deps.limiter, "enabled", False, raising=False)
    try:
        monkeypatch.setattr(server.app.state.limiter, "enabled", False, raising=False)
    except Exception:
        pass
    for k in ("INVOICE_LINK_SECRET", "MNG_WEBHOOK_SECRET", "STAFF_IDLE_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)

    _install_mongomock_shims(monkeypatch)
    asyncio.run(_wipe())
    _reset_caches()
    asyncio.run(_seed_base())

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield PayEnv(outbox, router, archive)
    _reset_caches()


@pytest_asyncio.fixture
async def pay_http(pay_env):
    transport = httpx.ASGITransport(app=server.app, client=("203.0.113.10", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="https://www.meluni.com.tr") as c:
        yield c
    await pay_drain()


@pytest.fixture
def pay_archive_on(pay_env, monkeypatch):
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_enabled", lambda: True)
    return pay_env.archive


# ────────────────────────────── composite flows ──────────────────────────────────────────────
PAY_TOKENS: dict = {}   # order_id → istemciye verilen ödeme yetkisi (denetim #4)


async def pay_create_order(http, pm="credit_card", headers=None, **kw):
    r = await http.post("/api/orders", json=order_payload(pm, **kw), headers=headers or {})
    await pay_drain()
    try:
        _j = r.json()
        if isinstance(_j, dict) and _j.get("order_id") and _j.get("pay_token"):
            PAY_TOKENS[_j["order_id"]] = _j["pay_token"]
    except Exception:
        pass
    return r


async def pay_card_order(http, pid="p1", stock=5, **kw):
    """Seed a product and create a card order through POST /api/orders. Returns the DB doc."""
    if not await pay_find("products", {"id": pid}):
        await pay_seed_product(pid, stock=stock)
    r = await pay_create_order(http, "credit_card",
                               items=[{"product_id": pid, "quantity": 1, "price": 1}], **kw)
    assert r.status_code == 200, r.text
    return await pay_find("orders", {"id": r.json()["order_id"]})


def mock_3ds_init(router, ok=True, payment_id="pid-init"):
    data = ({"status": "success", "threeDSHtmlContent": base64.b64encode(b"<html/>").decode(),
             "paymentId": payment_id} if ok else {"status": "failure", "errorCode": "10051",
                                                   "errorMessage": "Kart limiti yetersiz"})
    return router.post(f"{IYZICO_BASE}/payment/3dsecure/initialize").mock(return_value=iyz_json(data))


async def pay_init_3ds(http, order_id, pay_token=None):
    return await http.post("/api/payment/3ds/initialize",
                           json={"order_id": order_id, "callback_url": CALLBACK_URL,
                                 "return_url": "https://www.meluni.com.tr/odeme", "card": CARD,
                                 "pay_token": PAY_TOKENS.get(order_id, "") if pay_token is None else pay_token})


async def pay_callback(http, order_id, payment_id="pid-1", status="success", md="1"):
    return await http.post("/api/payment/3ds/callback",
                           data={"status": status, "mdStatus": md, "paymentId": payment_id,
                                 "conversationId": order_id})


def mock_3ds_auth(router, data_or_exc):
    route = router.post(f"{IYZICO_BASE}/payment/3dsecure/auth")
    if isinstance(data_or_exc, Exception):
        return route.mock(side_effect=data_or_exc)
    return route.mock(return_value=iyz_json(data_or_exc))


async def pay_mark_paid_via_callback(http, router, order, payment_id="pid-1"):
    mock_3ds_auth(router, iyz_auth_ok(order, payment_id=payment_id))
    r = await pay_callback(http, order["id"], payment_id)
    await pay_drain()
    return r


def new_id():
    return uuid.uuid4().hex


def deep(o):
    return copy.deepcopy(o)


def as_json(o):
    return json.dumps(o, sort_keys=True, default=str)
