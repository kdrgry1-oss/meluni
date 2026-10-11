"""E2E: iyzico card refunds (cap = captured amount, uncertain-result reservation, resolve flow),
refund-pay bookkeeping cap, freeze_refunds kill switch."""
import asyncio

import httpx
import pytest

from pay_harness import (
    IYZICO_BASE, iyz_auth_ok, iyz_json, mock_3ds_auth, pay_callback, pay_card_order, pay_drain,
    pay_find, pay_set, pay_set_switch, pay_staff,
)

pytestmark = pytest.mark.asyncio
REFUND = f"{IYZICO_BASE}/payment/refund"


async def _paid_order(http, env, paid=None, installment=1):
    o = await pay_card_order(http)
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id="pid-1",
                                          paid=paid if paid is not None else o["total"],
                                          installment=installment))
    await pay_callback(http, o["id"], "pid-1")
    await pay_drain()
    env.iyzico.routes.clear()
    o = await pay_find("orders", {"id": o["id"]})
    assert o["payment_status"] == "paid"
    return o


async def _refund(http, hdr, order_id, amount, **kw):
    return await http.post("/api/integrations/iyzico/refund",
                           json={"order_id": order_id, "amount": amount, **kw}, headers=hdr)


def _ok(router):
    return router.post(REFUND).mock(return_value=iyz_json({"status": "success", "paymentId": "pid-1"}))


async def test_refund_requires_permission(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.view"])
    route = _ok(pay_env.iyzico)
    r = await _refund(pay_http, hdr, o["id"], 10)
    assert r.status_code == 403 and route.call_count == 0


async def test_refund_capped_at_captured_amount(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)                   # captured 550.00
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    route = _ok(pay_env.iyzico)
    r = await _refund(pay_http, hdr, o["id"], 600)
    assert r.status_code == 400 and route.call_count == 0
    r = await _refund(pay_http, hdr, o["id"], 300)
    assert r.status_code == 200 and r.json()["status"] == "done"
    r = await _refund(pay_http, hdr, o["id"], 300)            # 300 + 300 > 550
    assert r.status_code == 400
    r = await _refund(pay_http, hdr, o["id"], 250)
    assert r.status_code == 200 and r.json()["status"] == "done"
    r = await _refund(pay_http, hdr, o["id"], 1)
    assert r.status_code == 400
    cur = await pay_find("orders", {"id": o["id"]})
    assert [x["status"] for x in cur["refunds"]] == ["done", "done"]
    assert round(sum(x["net_refund"] for x in cur["refunds"]), 2) == 550.0
    assert route.call_count == 2
    # iyzico got the exact amount and the original paymentId
    import json
    sent = [json.loads(c.request.content) for c in route.calls]
    assert [s["price"] for s in sent] == ["300.00", "250.00"] and {s["paymentId"] for s in sent} == {"pid-1"}


async def test_refund_cap_uses_installment_paid_price(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env, paid=580.0, installment=3)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    _ok(pay_env.iyzico)
    assert (await _refund(pay_http, hdr, o["id"], 580)).status_code == 200
    assert (await _refund(pay_http, hdr, o["id"], 0.5)).status_code == 400


async def test_concurrent_refunds_cannot_exceed_capture(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    route = _ok(pay_env.iyzico)
    rs = await asyncio.gather(*[_refund(pay_http, hdr, o["id"], 300) for _ in range(3)])
    assert sorted(r.status_code for r in rs) == [200, 400, 400]
    assert route.call_count == 1
    cur = await pay_find("orders", {"id": o["id"]})
    assert len(cur["refunds"]) == 1


async def test_refund_timeout_is_unknown_and_blocks_until_resolved(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    route = pay_env.iyzico.post(REFUND).mock(side_effect=httpx.ReadTimeout("no answer"))
    r = await _refund(pay_http, hdr, o["id"], 200)
    assert r.status_code == 200 and r.json()["status"] == "unknown"
    claim = r.json()["claim_id"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["refund_needs_verification"] is True
    assert [(x["status"], x["net_refund"]) for x in cur["refunds"]] == [("unknown", 200.0)]
    # any new refund is refused (double-refund guard) — even a small one
    r = await _refund(pay_http, hdr, o["id"], 1)
    assert r.status_code == 409
    assert route.call_count == 1
    # admin checks iyzico panel: refund did NOT happen → release
    r = await pay_http.post("/api/integrations/iyzico/refund/resolve",
                            json={"order_id": o["id"], "claim_id": claim, "outcome": "failed"}, headers=hdr)
    assert r.status_code == 200
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["refunds"] == [] and not cur.get("refund_needs_verification")
    pay_env.iyzico.routes.clear()
    _ok(pay_env.iyzico)
    assert (await _refund(pay_http, hdr, o["id"], 550)).status_code == 200


async def test_refund_unknown_resolved_done_still_counts_to_cap(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    pay_env.iyzico.post(REFUND).mock(side_effect=httpx.ReadTimeout("no answer"))
    claim = (await _refund(pay_http, hdr, o["id"], 500)).json()["claim_id"]
    r = await pay_http.post("/api/integrations/iyzico/refund/resolve",
                            json={"order_id": o["id"], "claim_id": claim, "outcome": "done"}, headers=hdr)
    assert r.status_code == 200
    pay_env.iyzico.routes.clear()
    _ok(pay_env.iyzico)
    assert (await _refund(pay_http, hdr, o["id"], 100)).status_code == 400   # 500 + 100 > 550
    assert (await _refund(pay_http, hdr, o["id"], 50)).status_code == 200


async def test_refund_connect_error_releases_reservation(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    pay_env.iyzico.post(REFUND).mock(side_effect=httpx.ConnectError("refused"))
    r = await _refund(pay_http, hdr, o["id"], 550)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur.get("refunds") == [] and not cur.get("refund_needs_verification")


async def test_refund_definite_iyzico_rejection_releases_reservation(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    pay_env.iyzico.post(REFUND).mock(return_value=iyz_json({"status": "failure", "errorMessage": "x"}))
    r = await _refund(pay_http, hdr, o["id"], 550)
    assert r.json()["status"] == "failed"
    assert (await pay_find("orders", {"id": o["id"]})).get("refunds") == []


async def test_refund_on_unpaid_order_rejected(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    route = _ok(pay_env.iyzico)
    assert (await _refund(pay_http, hdr, o["id"], 10)).status_code == 400
    assert route.call_count == 0


async def test_negative_shipping_deduction_and_nan_rejected(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(["returns.iyzico_refund"])
    route = _ok(pay_env.iyzico)
    assert (await _refund(pay_http, hdr, o["id"], 100, shipping_deduction=-500)).status_code == 400
    assert (await _refund(pay_http, hdr, o["id"], "NaN")).status_code == 400
    assert route.call_count == 0


# ═════════════════════════════════════ freeze_refunds ════════════════════════════════════════
async def test_freeze_refunds_switch_blocks_every_refund_endpoint(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    from routes import deps
    await deps.db.customer_returns.insert_one({"id": "ret-f", "order_id": o["id"], "status": "approved",
                                               "order_number": o["order_number"], "refund_amount": 100})
    await pay_set("orders", {"id": o["id"]}, {"status": "return_approved"})
    _, hdr = await pay_staff(super_admin=True)
    route = _ok(pay_env.iyzico)
    await pay_set_switch("freeze_refunds", True)
    r1 = await _refund(pay_http, hdr, o["id"], 10)
    r2 = await pay_http.post("/api/orders/returns/ret-f/refund-pay", json={"amount": 100, "method": "havale"},
                             headers=hdr)
    r3 = await pay_http.post("/api/orders/returns/bulk-mark-refunded-silent", json={"order_ids": [o["id"]]},
                             headers=hdr)
    assert (r1.status_code, r2.status_code, r3.status_code) == (423, 423, 423)
    assert route.call_count == 0
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "return_approved" and not cur.get("refunds")
    assert (await pay_find("customer_returns", {"id": "ret-f"}))["status"] == "approved"
    # resolve (no money movement) stays available during an incident
    r = await pay_http.post("/api/integrations/iyzico/refund/resolve",
                            json={"order_id": o["id"], "claim_id": "nope", "outcome": "failed"}, headers=hdr)
    assert r.status_code == 404
