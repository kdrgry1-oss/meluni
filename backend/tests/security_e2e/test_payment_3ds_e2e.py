"""E2E: iyzico 3DS initialize / callback / paid transition (CLAUDE.md invariants 1, 3, 4, 5).

iyzico is mocked with respx at the HTTP layer; the app's own verification
(_is_paid + _payment_matches_order + replay shield + atomic transition) runs unmodified."""
import asyncio

import httpx
import pytest

from pay_harness import (
    CARD_TOTAL, IYZICO_BASE, iyz_auth_ok, mock_3ds_auth, mock_3ds_init, pay_callback, pay_card_order,
    pay_drain, pay_find, pay_findall, pay_init_3ds, pay_mark_paid_via_callback, pay_set,
)

pytestmark = pytest.mark.asyncio


async def _side_effects(env, order):
    await pay_drain()
    return (len(env.outbox.events("order_confirmed")),
            len(env.outbox.new_order_pushes(order["id"])),
            len(env.outbox.admin_order_mails(order["order_number"])),
            len(await pay_findall("coupon_redemptions", {"order_id": order["id"]})))


async def _with_coupon(order):
    from routes import deps
    await deps.db.coupons.insert_one({"id": "cpn-1", "code": "TEST10", "is_active": True})
    await pay_set("orders", {"id": order["id"]},
                  {"applied_promotions": [{"coupon_id": "cpn-1", "code": "TEST10", "discount": 10.0}],
                   "coupon_code": "TEST10"})
    return await pay_find("orders", {"id": order["id"]})


# ═════════════════════════════════════ 3DS initialize ════════════════════════════════════════
async def test_concurrent_3ds_init_second_is_409(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    route = mock_3ds_init(pay_env.iyzico)
    r1, r2 = await asyncio.gather(pay_init_3ds(pay_http, o["id"]), pay_init_3ds(pay_http, o["id"]))
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (r1.text, r2.text)
    assert route.call_count == 1                        # only ONE charge attempt reached iyzico
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur.get("payment_init_lock_until")          # lock kept until bank callback
    assert cur["status"] == "awaiting_payment" and cur["payment_status"] == "pending"


async def test_failed_3ds_init_releases_lock(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    mock_3ds_init(pay_env.iyzico, ok=False)
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 200 and r.json()["success"] is False
    cur = await pay_find("orders", {"id": o["id"]})
    assert not cur.get("payment_init_lock_until") and not cur.get("payment_init_lock_id")
    assert cur["payment_status"] == "pending" and cur["status"] == "awaiting_payment"
    pay_env.iyzico.routes.clear()
    mock_3ds_init(pay_env.iyzico, ok=True)
    r = await pay_init_3ds(pay_http, o["id"])           # immediate retry is allowed
    assert r.status_code == 200 and r.json()["success"] is True


async def test_3ds_init_transport_error_releases_lock(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(f"{IYZICO_BASE}/payment/3dsecure/initialize").mock(
        side_effect=httpx.ConnectError("boom"))
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 502
    cur = await pay_find("orders", {"id": o["id"]})
    assert not cur.get("payment_init_lock_until")


async def test_3ds_init_on_paid_order_rejected(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, o)
    assert (await pay_find("orders", {"id": o["id"]}))["payment_status"] == "paid"
    route = mock_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 400
    assert route.call_count == 0


@pytest.mark.parametrize("switch", ["payment_methods", "iyzico_inactive"])
async def test_kill_switch_blocks_new_init_but_callback_still_finalizes(pay_http, pay_env, switch):
    o = await pay_card_order(pay_http)
    mock_3ds_init(pay_env.iyzico)
    assert (await pay_init_3ds(pay_http, o["id"])).json()["success"] is True   # payment started
    if switch == "payment_methods":
        await pay_set("settings", {"id": "main"}, {"payment_methods.credit_card": False})
    else:
        await pay_set("settings", {"id": "iyzico"}, {"is_active": False})
    # A NEW initialisation is refused …
    await pay_set("orders", {"id": o["id"]}, {"payment_init_lock_until": None})
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 400
    # … but the bank callback of the payment that already started is still finalised.
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, o)
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"


# ═════════════════════════════════════ callback / paid ═══════════════════════════════════════
async def test_verified_success_marks_paid_with_single_side_effects(pay_http, pay_env):
    o = await _with_coupon(await pay_card_order(pay_http))
    r = await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, o)
    assert r.status_code == 303 and "status=success" in r.headers["location"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"
    assert cur["iyzico_payment_id"] == "pid-1" and cur.get("paid_at")
    assert cur.get("order_confirmed_notified") is True
    assert cur.get("coupon_redemptions_recorded") is True
    assert await _side_effects(pay_env, o) == (1, 1, 1, 1)


async def test_callback_replay_has_no_extra_side_effects(pay_http, pay_env):
    o = await _with_coupon(await pay_card_order(pay_http))
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, o)
    # order moves on (shipped) — a replayed bank callback must not drag it back to 'confirmed'
    await pay_set("orders", {"id": o["id"]}, {"status": "shipped"})
    for _ in range(3):
        r = await pay_callback(pay_http, o["id"], "pid-1")
        assert r.status_code == 303
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "shipped" and cur["payment_status"] == "paid"
    assert not cur.get("duplicate_payments")
    assert await _side_effects(pay_env, o) == (1, 1, 1, 1)


async def test_two_concurrent_finalizations_one_set_of_side_effects(pay_http, pay_env):
    o = await _with_coupon(await pay_card_order(pay_http))
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o))
    rs = await asyncio.gather(*[pay_callback(pay_http, o["id"], "pid-1") for _ in range(3)])
    assert all(r.status_code == 303 for r in rs)
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"
    assert await _side_effects(pay_env, o) == (1, 1, 1, 1)


async def test_concurrent_mark_order_from_payment_direct(pay_http, pay_env):
    """Same race exercised on the single authoritative function (callback + webhook + reconcile)."""
    from routes.payment import _mark_order_from_payment
    o = await _with_coupon(await pay_card_order(pay_http))
    data = iyz_auth_ok(o)
    res = await asyncio.gather(*[_mark_order_from_payment(o["id"], dict(data)) for _ in range(4)])
    assert all(res)
    assert await _side_effects(pay_env, o) == (1, 1, 1, 1)


@pytest.mark.parametrize("variant", ["amount_low", "basket_mismatch", "currency_usd", "conv_mismatch",
                                     "fraud_review", "no_amount"])
async def test_mismatched_iyzico_response_is_not_paid(pay_http, pay_env, variant):
    o = await _with_coupon(await pay_card_order(pay_http))
    kw = {"amount_low": dict(paid=1.00), "basket_mismatch": dict(basket="W99999"),
          "currency_usd": dict(currency="USD"), "conv_mismatch": dict(conv="other-order"),
          "fraud_review": dict(fraudStatus=0), "no_amount": dict(drop=("paidPrice", "price"))}[variant]
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, **kw))
    r = await pay_callback(pay_http, o["id"], "pid-1")
    assert "status=fail" in r.headers["location"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] != "paid"
    assert cur["status"] == "awaiting_payment"           # invariant 2/4: stays awaiting payment
    assert not cur.get("iyzico_payment_id")
    # paymentId came back → money may have moved → reconcile flag for the 15-min cron (invariant 4)
    assert cur.get("needs_reconciliation") is True and cur.get("reconcile_payment_id") == "pid-1"
    assert await _side_effects(pay_env, o) == (0, 0, 0, 0)   # invariant 3 + 5: nothing sent, coupon not burnt


async def test_missing_currency_field_is_accepted(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, drop=("currency",)))
    r = await pay_callback(pay_http, o["id"], "pid-1")
    assert "status=success" in r.headers["location"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"


async def test_unverified_failure_callback_does_not_touch_order(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    mock_3ds_init(pay_env.iyzico)
    await pay_init_3ds(pay_http, o["id"])
    auth = mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o))
    for status, md in (("failure", "0"), ("success", "0"), ("failure", "1")):
        r = await pay_callback(pay_http, o["id"], "pid-x", status=status, md=md)
        assert "status=fail" in r.headers["location"]
    assert auth.call_count == 0                       # never asked iyzico → never trusted
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "awaiting_payment" and cur["payment_status"] == "pending"
    assert not cur.get("payment_init_lock_until")     # customer may retry immediately
    assert await _side_effects(pay_env, o) == (0, 0, 0, 0)


async def test_forged_success_callback_without_iyzico_confirmation_not_paid(pay_http, pay_env):
    """status=success&mdStatus=1 from the browser is NOT trusted: iyzico auth says failure."""
    o = await pay_card_order(pay_http)
    mock_3ds_auth(pay_env.iyzico, {"status": "failure", "errorCode": "10012", "errorMessage": "x"})
    r = await pay_callback(pay_http, o["id"], "pid-forged")
    assert "status=fail" in r.headers["location"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "failed" and cur["status"] == "awaiting_payment"


async def test_callback_auth_timeout_is_uncertain_not_failed(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    mock_3ds_auth(pay_env.iyzico, httpx.ReadTimeout("slow"))
    await pay_callback(pay_http, o["id"], "pid-t")
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "pending" and cur["status"] == "awaiting_payment"
    assert cur["needs_reconciliation"] is True and cur["reconcile_payment_id"] == "pid-t"


async def test_second_successful_payment_recorded_as_duplicate(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, o, payment_id="pid-1")
    await pay_set("orders", {"id": o["id"]}, {"status": "shipped"})
    pay_env.iyzico.routes.clear()
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, payment_id="pid-2"))
    for _ in range(2):                                 # idempotent per paymentId
        await pay_callback(pay_http, o["id"], "pid-2")
    await pay_drain()
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "shipped" and cur["payment_status"] == "paid"
    assert cur["iyzico_payment_id"] == "pid-1"
    assert [d["paymentId"] for d in cur["duplicate_payments"]] == ["pid-2"]
    assert cur["needs_duplicate_refund"] is True and cur["needs_reconciliation"] is True
    assert len([p for p in pay_env.outbox.pushes if p["data"].get("type") == "duplicate_payment"]) == 1
    assert len(pay_env.outbox.events("order_confirmed")) == 1


async def test_cross_order_payment_id_replay_rejected(pay_http, pay_env):
    a = await pay_card_order(pay_http)
    await pay_mark_paid_via_callback(pay_http, pay_env.iyzico, a, payment_id="pid-A")
    b = await pay_card_order(pay_http, pid="p2", ship={"first_name": "B", "last_name": "C",
                                                         "email": "b@example.test", "phone": "05550001122",
                                                         "city": "Ankara", "address": "Adres B"})
    pay_env.iyzico.routes.clear()
    # attacker replays A's paymentId on B, with B's own ids/amount
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(b, payment_id="pid-A"))
    await pay_callback(pay_http, b["id"], "pid-A")
    cur = await pay_find("orders", {"id": b["id"]})
    assert cur["payment_status"] != "paid" and cur["status"] == "awaiting_payment"


async def test_non3ds_card_pay_is_gone_by_default(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    r = await pay_http.post("/api/payment/card/pay", json={"order_id": o["id"], "card": {}})
    assert r.status_code == 410
    assert (await pay_find("orders", {"id": o["id"]}))["payment_status"] == "pending"


async def test_card_order_total_matches_server(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    assert o["total"] == CARD_TOTAL
