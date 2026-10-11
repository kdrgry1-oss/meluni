"""E2E edge cases around the payment invariants: the single documented non-iyzico paid path
(C2: order fully covered by a gift card), idempotency-key race with the startup unique index,
and a bank callback that lands after staff cancelled the (unpaid) order."""
import asyncio

import pytest

from pay_harness import (
    CARD_TOTAL, SHIP, iyz_auth_ok, mock_3ds_auth, mock_3ds_init, order_payload, pay_callback,
    pay_card_order, pay_create_order, pay_drain, pay_find, pay_findall, pay_init_3ds,
    pay_seed_product, pay_staff, pay_stock,
)

pytestmark = pytest.mark.asyncio


async def _gift(code="HED-TEST-0001-AAAA", balance=1000.0):
    from routes import deps
    await deps.db.gift_cards.insert_one({"code": code, "status": "active", "balance": balance,
                                         "kind": "gift", "value_type": "amount", "transactions": []})
    return code


async def _balance(code):
    return round(float((await pay_find("gift_cards", {"code": code}))["balance"]), 2)


# ═════════════════════════════════════ C2: gift card ═════════════════════════════════════════
async def test_gift_card_full_coverage_is_paid_confirmed_server_side(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    code = await _gift(balance=1000)
    r = await pay_create_order(pay_http, "credit_card", gift_card_code=code.lower(),
                               gift_card_amount=1, total=0.0)
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["payment_status"] == "paid" and o["status"] == "confirmed"
    assert o["paid_with_gift_card_only"] is True and o["total"] == 0.0
    assert o["gift_card"]["amount"] == CARD_TOTAL
    assert await _balance(code) == round(1000 - CARD_TOTAL, 2)
    await pay_drain()
    assert len(pay_env.outbox.events("order_confirmed")) == 1
    # nothing to charge → iyzico is never involved
    route = mock_3ds_init(pay_env.iyzico)
    assert (await pay_init_3ds(pay_http, o["id"])).status_code == 400
    assert route.call_count == 0


async def test_gift_card_partial_coverage_still_requires_payment(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    code = await _gift(balance=100)
    r = await pay_create_order(pay_http, "credit_card", gift_card_code=code)
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["status"] == "awaiting_payment" and o["payment_status"] == "pending"
    assert o["total"] == round(CARD_TOTAL - 100, 2) and not o.get("paid_with_gift_card_only")
    await pay_drain()
    assert pay_env.outbox.notifications == [] and pay_env.outbox.new_order_pushes() == []


async def test_gift_card_balance_cannot_be_double_spent_concurrently(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    code = await _gift(balance=600)
    bodies = [order_payload(gift_card_code=code, ship={**SHIP, "email": f"g{i}@example.test",
                                                      "phone": f"0555000000{i}"}) for i in range(3)]
    rs = await asyncio.gather(*[pay_http.post("/api/orders", json=b) for b in bodies])
    await pay_drain()
    assert all(r.status_code in (200, 400) for r in rs), [r.text for r in rs]
    orders = await pay_findall("orders")
    used = round(sum(float((o.get("gift_card") or {}).get("amount") or 0) for o in orders), 2)
    assert used <= 600.0 and await _balance(code) == round(600 - used, 2)
    assert sum(1 for o in orders if o.get("paid_with_gift_card_only")) <= 1


async def test_gift_card_refunded_once_on_cancel(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    code = await _gift(balance=1000)
    oid = (await pay_create_order(pay_http, "credit_card", gift_card_code=code)).json()["order_id"]
    _, hdr = await pay_staff(super_admin=True)
    for _ in range(2):
        r = await pay_http.put(f"/api/orders/{oid}/status?status=cancelled", headers=hdr)
        assert r.status_code == 200
    await pay_drain()
    assert await _balance(code) == 1000.0
    assert await pay_stock("p1") == 5
    assert (await pay_find("orders", {"id": oid}))["gift_card"]["refunded"] is True


# ═════════════════════════════════════ idempotency race ══════════════════════════════════════
async def test_concurrent_same_idempotency_key_single_order_with_startup_index(pay_http, pay_env):
    from routes import deps
    # exactly what server.py lifespan creates
    await deps.db.orders.create_index("idempotency_key", unique=True,
                                      partialFilterExpression={"idempotency_key": {"$type": "string"}})
    await pay_seed_product("p1", stock=5)
    rs = await asyncio.gather(*[pay_http.post("/api/orders", json=order_payload(idempotency_key="race-1"))
                                for _ in range(4)])
    await pay_drain()
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert len({r.json()["order_id"] for r in rs}) == 1
    assert len(await pay_findall("orders")) == 1
    assert await pay_stock("p1") == 4                      # losers' stock reservations reversed


# ═════════════════════════════════════ late callback after cancel ════════════════════════════
async def test_late_verified_callback_after_staff_cancel_keeps_stock_consistent(pay_http, pay_env):
    o = await pay_card_order(pay_http, stock=1)
    assert await pay_stock("p1") == 0
    mock_3ds_init(pay_env.iyzico)
    assert (await pay_init_3ds(pay_http, o["id"])).json()["success"] is True
    _, hdr = await pay_staff(["orders.update_status"])
    assert (await pay_http.put(f"/api/orders/{o['id']}/status?status=cancelled", headers=hdr)).status_code == 200
    assert await pay_stock("p1") == 1                      # restocked by the cancellation
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o))
    await pay_callback(pay_http, o["id"], "pid-1")          # money really captured
    await pay_drain()
    cur = await pay_find("orders", {"id": o["id"]})
    if cur["payment_status"] == "paid" and cur["status"] not in ("cancelled", "cancel_refunded"):
        assert await pay_stock("p1") == 0                  # a live paid order must hold its unit
    else:
        assert cur.get("needs_duplicate_refund") or cur.get("needs_manual_review")
