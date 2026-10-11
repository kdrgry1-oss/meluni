"""ADM-15 — admin push payloads carry no personal data.

Every admin push goes through routes.push.send_push_to_admins, which sanitises title/body/data
BEFORE handing them to FCM (`_fcm_send` is captured here — nothing leaves the process). The real
builders are exercised end-to-end: card order paid (payment.py), bank-transfer new order at creation
(orders.py), duplicate card payment (payment.py), WhatsApp hand-off (whatsapp_webhook.py); plus a
static inventory so a NEW push call site cannot appear without review.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import routes.push as push_mod
from pay_harness import (pay_card_order, pay_create_order, pay_drain, pay_find, pay_mark_paid_via_callback,
                         pay_seed_product, SHIP)
from routes import deps

REAL_SEND = push_mod.send_push_to_admins          # captured before pay_env replaces it
BACKEND = Path(__file__).resolve().parents[2]
PII = ["Ayse", "Test", "buyer@example.test", "05551234567", "5551234567", "Kadikoy", "Deneme Sok"]


@pytest.fixture
def fcm(pay_env, monkeypatch):
    sent = []

    async def _cfg():
        return "kv-project", '{"type": "service_account"}'

    async def _send(project, sa, tokens, title, body, data):
        sent.append({"tokens": list(tokens), "title": title, "body": body, "data": dict(data)})
        return len(tokens), []
    monkeypatch.setattr(push_mod, "send_push_to_admins", REAL_SEND)
    monkeypatch.setattr(push_mod, "_fcm_config", _cfg)
    monkeypatch.setattr(push_mod, "_fcm_send", _send)
    return sent


async def _admin_device():
    await deps.db.users.insert_one({"id": "adm-1", "email": "boss@example.test", "is_admin": True,
                                    "is_super_admin": True, "is_active": True})
    await deps.db.user_devices.insert_one({"user_id": "adm-1", "device_id": "d1", "push_token": "tok-admin",
                                           "is_active": True})
    # a customer device must never receive admin pushes
    await deps.db.users.insert_one({"id": "cust-9", "email": "c9@example.test", "is_admin": False})
    await deps.db.user_devices.insert_one({"user_id": "cust-9", "device_id": "d9", "push_token": "tok-cust",
                                           "is_active": True})


def _assert_clean(msg, order=None):
    blob = json.dumps(msg, ensure_ascii=False)
    for s in PII:
        assert s not in blob, (s, msg)
    if order is not None:
        total = f"{float(order['total']):.2f}"
        assert total not in blob and total.replace(".", ",") not in blob, msg
    assert msg["tokens"] == ["tok-admin"]



@pytest.mark.asyncio
async def test_card_paid_push_has_no_pii(pay_http, pay_env, fcm):
    await _admin_device()
    order = await pay_card_order(pay_http)
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, order)
    await pay_drain()
    pushes = [m for m in fcm if m["data"].get("type") == "new_order"]
    assert len(pushes) == 1
    m = pushes[0]
    assert m["title"].startswith("🛍️ Yeni Sipariş") and order["order_number"] in m["body"]
    _assert_clean(m, order)
    log = await pay_find("push_notifications_log", {"kind": "new_order"})
    assert log and set(log) <= {"kind", "order_number", "sent", "targets", "via", "sent_at"}


@pytest.mark.asyncio
async def test_bank_transfer_new_order_push_from_orders_py_has_no_pii(pay_http, pay_env, fcm):
    """orders.py builds '🛍️ Yeni Sipariş · <tutar> TL' / '<no> · <müşteri adı> · <platform>' —
    the central sanitiser must strip customer name and amount before FCM."""
    await _admin_device()
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "bank_transfer")
    assert r.status_code == 200, r.text
    await pay_drain()
    order = await pay_find("orders", {"id": r.json()["order_id"]})
    pushes = [m for m in fcm if m["data"].get("type") == "new_order"]
    assert len(pushes) == 1, fcm
    assert order["order_number"] in pushes[0]["body"]
    _assert_clean(pushes[0], order)


@pytest.mark.asyncio
async def test_duplicate_payment_push_has_no_pii(pay_http, pay_env, fcm):
    await _admin_device()
    from routes.payment import _record_duplicate_payment
    order = await pay_card_order(pay_http)
    await _record_duplicate_payment(order["id"], {"paymentId": "pid-dup", "paidPrice": "550.00", "currency": "TRY"},
                                    "pid-1")
    m = [x for x in fcm if x["data"].get("type") == "duplicate_payment"]
    assert len(m) == 1 and order["order_number"] in m[0]["body"]
    _assert_clean(m[0])


@pytest.mark.asyncio
async def test_whatsapp_handoff_push_has_no_pii(pay_env, fcm, monkeypatch):
    await _admin_device()
    import routes.whatsapp_webhook as wa

    async def _cfg():
        return {}
    monkeypatch.setattr(wa, "_wa_cfg", _cfg)
    await wa._handoff("905551234567", "Merhaba, adresim Deneme Sok. No:1 Kadikoy, mailim buyer@example.test", "Ayse Test")
    m = [x for x in fcm if x["data"].get("type") == "whatsapp_handoff"]
    assert len(m) == 1
    _assert_clean(m[0])
    assert "phone" not in m[0]["data"]


@pytest.mark.asyncio
async def test_sanitizer_masks_generic_pii_patterns():
    t, b, d = push_mod.sanitize_admin_push(
        "Uyarı", "Müşteri ali.veli@example.org 0532 111 22 33 IBAN TR33 0006 1005 1978 6457 8413 26 TC 10000000146",
        {"type": "x", "email": "ali.veli@example.org", "phone": "05321112233", "order_number": "10234567890"})
    blob = json.dumps([t, b, d], ensure_ascii=False)
    for s in ("ali.veli", "0532", "1112233", "TR33", "10000000146"):
        assert s not in blob, blob
    assert d == {"type": "x", "order_number": "10234567890"}   # marketplace order no kept


def test_push_call_sites_inventory():
    """Every send_push_to_admins call site is known and reviewed (new ones fail this test)."""
    expected = {"scheduler.py": 1, "routes/payment.py": 2, "routes/push.py": 1, "routes/orders.py": 2,
                "routes/instagram.py": 1, "routes/catalog_extras.py": 1, "routes/whatsapp_webhook.py": 1}
    found = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("tests/") or "/node_modules/" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                if name == "send_push_to_admins":
                    found[rel] = found.get(rel, 0) + 1
    assert found == expected, found


@pytest.mark.asyncio
async def test_shared_device_after_account_switch_gets_no_admin_push(pay_env, fcm):
    """SHOP10: staff logged out (device row left active) and a customer logged in on the same phone
    → the push token now belongs to the customer; admin pushes must not reach it."""
    await _admin_device()
    await deps.db.user_devices.update_one({"push_token": "tok-admin"}, {"$set": {"last_seen_at": "2026-10-01T10:00:00+00:00"}})
    await push_mod.send_push_to_admins("x", "y", {"type": "test"})
    assert fcm[-1]["tokens"] == ["tok-admin"]
    await deps.db.user_devices.insert_one({"user_id": "cust-9", "device_id": "d1", "push_token": "tok-admin",
                                           "is_active": True, "last_seen_at": "2026-10-02T10:00:00+00:00"})
    n = len(fcm)
    assert await push_mod.send_push_to_admins("x", "y", {"type": "test"}) == 0
    assert len(fcm) == n
    # staff signs in again later on that phone → admin pushes resume
    await deps.db.user_devices.update_one({"user_id": "adm-1", "push_token": "tok-admin"},
                                          {"$set": {"last_seen_at": "2026-10-03T10:00:00+00:00"}})
    await push_mod.send_push_to_admins("x", "y", {"type": "test"})
    assert fcm[-1]["tokens"] == ["tok-admin"]
