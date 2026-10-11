"""E2E: POST /api/orders — input validation, server-authoritative pricing, status at creation
(CLAUDE.md invariant 2), notification gating (invariant 3), atomic stock / oversell (invariant 6),
idempotency, kill switches, maintenance mode. Real DB state is asserted after every call."""
import asyncio

import pytest

from pay_harness import (
    CARD_TOTAL, HAVALE_TOTAL, PRODUCT_PRICE, SHIP, order_payload, pay_create_order, pay_drain,
    pay_find, pay_findall, pay_seed_product, pay_set, pay_set_switch, pay_stock,
)

pytestmark = pytest.mark.asyncio


async def _n_orders():
    return len(await pay_findall("orders"))


async def test_empty_items_rejected_without_writes(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, items=[])
    assert r.status_code == 400
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 3


async def test_missing_address_rejected(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    for bad in ({}, {**SHIP, "address": ""}, {**SHIP, "phone": ""}, {**SHIP, "city": " "}):
        r = await pay_create_order(pay_http, ship=bad)
        assert r.status_code == 400, (bad, r.text)
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 3


async def test_unknown_product_rejected(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, items=[{"product_id": "nope", "quantity": 1, "price": -999}])
    assert r.status_code == 400
    assert await _n_orders() == 0


@pytest.mark.parametrize("price", [0, -10])
async def test_zero_or_negative_price_product_rejected(pay_http, pay_env, price):
    await pay_seed_product("p0", stock=3, price=price)
    r = await pay_create_order(pay_http, items=[{"product_id": "p0", "quantity": 1, "price": 100}])
    assert r.status_code == 400, r.text
    assert await _n_orders() == 0
    assert await pay_stock("p0") == 3


async def test_client_price_ignored_server_price_used(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, items=[{"product_id": "p1", "quantity": 2, "price": 0.01,
                                                  "discount_amount": 999}],
                               subtotal=0.02, shipping_cost=0, discount=0)
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["items"][0]["price"] == PRODUCT_PRICE
    assert o["subtotal"] == PRODUCT_PRICE * 2
    assert o["total"] == PRODUCT_PRICE * 2 + 50.0
    assert float(o["items"][0].get("discount_amount") or 0) == 0.0   # client discount stripped


async def test_client_total_mismatch_is_rejected(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, total=1.00)
    assert r.status_code == 409
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 3   # nothing reserved


async def test_card_order_starts_awaiting_payment_and_sends_nothing(pay_http, pay_env):
    """Invariants 2 + 3: card order → awaiting_payment, not paid; NO customer 'Siparişiniz
    Alındı' / bank mail and NO admin push / admin mail at creation."""
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, "credit_card", total=CARD_TOTAL)
    assert r.status_code == 200, r.text
    await pay_drain()
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["status"] == "awaiting_payment"
    assert o["payment_status"] != "paid"
    assert not o.get("paid_at")
    assert pay_env.outbox.notifications == []          # no order_confirmed, no awaiting-payment mail
    assert pay_env.outbox.new_order_pushes() == []
    assert pay_env.outbox.admin_order_mails() == []
    assert await pay_findall("coupon_redemptions") == []


async def test_havale_order_gets_bank_details_mail_only(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, "bank_transfer", total=HAVALE_TOTAL)
    assert r.status_code == 200, r.text
    await pay_drain()
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["status"] == "awaiting_payment" and o["payment_status"] == "pending"
    assert o["total"] == HAVALE_TOTAL
    evs = [n["event"] for n in pay_env.outbox.notifications]
    assert evs == ["order_awaiting_payment"], evs
    assert "order_confirmed" not in evs
    # Havale does generate the admin "new order" notifications (actionable order).
    assert len(pay_env.outbox.admin_order_mails(o["order_number"])) == 1
    assert len(pay_env.outbox.new_order_pushes(o["id"])) == 1


async def test_card_order_never_receives_bank_mail(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, "credit_card")
    assert r.status_code == 200
    await pay_drain()
    assert not pay_env.outbox.events("order_awaiting_payment")


async def test_stock_decremented_atomically_and_movement_written(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, items=[{"product_id": "p1", "quantity": 2}])
    assert r.status_code == 200, r.text
    assert await pay_stock("p1") == 1
    mv = await pay_findall("stock_movements", {"order_id": r.json()["order_id"]})
    assert len(mv) == 1 and mv[0]["type"] == "order_created"
    assert sum(i["delta"] for i in mv[0]["items"]) == -2
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o.get("stock_deducted") is True


async def test_oversell_two_concurrent_orders_for_last_unit(pay_http, pay_env):
    await pay_seed_product("p1", stock=1)
    ship2 = {**SHIP, "email": "other@example.test", "phone": "05559876543"}
    r1, r2 = await asyncio.gather(
        pay_http.post("/api/orders", json=order_payload()),
        pay_http.post("/api/orders", json=order_payload(ship=ship2)))
    await pay_drain()
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (r1.text, r2.text)
    assert await pay_stock("p1") == 0
    assert await _n_orders() == 1


async def test_oversell_variant_product(pay_http, pay_env):
    await pay_seed_product("pv", stock=1, variants=[
        {"id": "v-s", "size": "S", "barcode": "VB-S", "stock": 1},
        {"id": "v-m", "size": "M", "barcode": "VB-M", "stock": 0}])
    r = await pay_create_order(pay_http, items=[{"product_id": "pv", "variant_id": "v-m", "quantity": 1}])
    assert r.status_code == 409
    r = await pay_create_order(pay_http, items=[{"product_id": "pv", "variant_id": "v-s", "quantity": 1}])
    assert r.status_code == 200, r.text
    p = await pay_find("products", {"id": "pv"})
    assert [v["stock"] for v in p["variants"]] == [0, 0]
    assert p["stock"] == 0
    r = await pay_create_order(pay_http, items=[{"product_id": "pv", "variant_id": "v-s", "quantity": 1}],
                               ship={**SHIP, "email": "x2@example.test", "phone": "05550000001"})
    assert r.status_code == 409


async def test_idempotency_same_key_same_payload_returns_same_order(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r1 = await pay_create_order(pay_http, idempotency_key="idem-1")
    r2 = await pay_create_order(pay_http, idempotency_key="idem-1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["order_id"] == r2.json()["order_id"]
    assert r2.json().get("idempotent") is True
    assert await _n_orders() == 1
    assert await pay_stock("p1") == 4


async def test_idempotency_same_key_different_address_conflict(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r1 = await pay_create_order(pay_http, idempotency_key="idem-2")
    assert r1.status_code == 200
    r2 = await pay_create_order(pay_http, idempotency_key="idem-2",
                                ship={**SHIP, "address": "Baska Adres 99"})
    assert r2.status_code == 409
    assert r2.json()["code"] == "idempotency_conflict"
    assert await _n_orders() == 1
    assert await pay_stock("p1") == 4


async def test_close_checkout_switch_503_without_writes(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    await pay_set_switch("close_checkout", True)
    r = await pay_create_order(pay_http)
    assert r.status_code == 503
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 5
    assert await pay_findall("stock_movements") == []
    assert pay_env.outbox.notifications == [] and pay_env.outbox.pushes == []


async def test_disabled_credit_card_method_rejected(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    await pay_set("settings", {"id": "main"}, {"payment_methods.credit_card": False})
    r = await pay_create_order(pay_http, "credit_card")
    assert r.status_code == 400
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 5
    # Havale still works.
    r = await pay_create_order(pay_http, "bank_transfer")
    assert r.status_code == 200


async def test_unknown_payment_method_rejected(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "marketplace")
    assert r.status_code == 400
    assert await _n_orders() == 0


async def test_maintenance_mode_503_for_customers(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    await pay_set("settings", {"id": "main"}, {"maintenance_mode": True})
    r = await pay_create_order(pay_http)
    assert r.status_code == 503
    assert await _n_orders() == 0
    assert await pay_stock("p1") == 5


async def test_client_cannot_create_paid_order(pay_http, pay_env):
    """Client-supplied payment_status/status are never trusted (invariant 1)."""
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "credit_card", payment_status="paid", status="confirmed",
                               paid_with_gift_card_only=True)
    assert r.status_code == 200
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["payment_status"] == "pending" and o["status"] == "awaiting_payment"
    assert not o.get("paid_with_gift_card_only")
