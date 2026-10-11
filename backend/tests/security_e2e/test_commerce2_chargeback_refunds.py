"""E2E (commerce-2) COM11 / FIN-E03: while an OPEN chargeback exists on an order, no new refund
(iyzico card refund, refund-pay bookkeeping, bulk "refund paid", status → refunded) can move money a
second time. Only a super admin may override, with a written reason that is audited (fail-closed)."""
import pytest

from pay_harness import (
    IYZICO_BASE, iyz_auth_ok, iyz_json, mock_3ds_auth, now_iso, pay_callback, pay_card_order, pay_drain,
    pay_find, pay_findall, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio
REFUND = f"{IYZICO_BASE}/payment/refund"
CB = "/api/admin/chargebacks"
REASON = "Banka itirazı kabul etmedi; müşteriye kısmi iade onaylandı (finans)"


async def _paid(http, env, pid="p1"):
    o = await pay_card_order(http, pid=pid)
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id=f"pid-{pid}"))
    await pay_callback(http, o["id"], f"pid-{pid}")
    await pay_drain()
    env.iyzico.routes.clear()
    o = await pay_find("orders", {"id": o["id"]})
    assert o["payment_status"] == "paid"
    return o


async def _open_cb(http, order, hdr):
    r = await http.post(CB, json={"order_id": order["id"], "amount": 100, "deadline": "2026-12-01",
                                  "status": "open"}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["chargeback"]["id"]


def _ok_refund(router):
    return router.post(REFUND).mock(return_value=iyz_json({"status": "success", "paymentId": "x"}))


async def test_iyzico_refund_blocked_while_chargeback_open_and_override_is_audited(pay_http, pay_env):
    o = await _paid(pay_http, pay_env)
    _, fin = await pay_staff(["returns.refund_pay", "returns.iyzico_refund"])
    _, sup = await pay_staff(super_admin=True)
    cid = await _open_cb(pay_http, o, fin)
    route = _ok_refund(pay_env.iyzico)
    url = "/api/integrations/iyzico/refund"
    r = await pay_http.post(url, json={"order_id": o["id"], "amount": 100}, headers=fin)
    assert r.status_code == 409 and "itiraz" in r.json()["detail"]
    r = await pay_http.post(url, json={"order_id": o["id"], "amount": 100, "chargeback_override_reason": REASON},
                            headers=fin)
    assert r.status_code == 403                                    # override is super-admin only
    r = await pay_http.post(url, json={"order_id": o["id"], "amount": 100, "chargeback_override_reason": "kısa"},
                            headers=sup)
    assert r.status_code == 400
    assert route.call_count == 0
    cur = await pay_find("orders", {"id": o["id"]})
    assert not cur.get("refunds") and cur["status"] == "confirmed" and cur["payment_status"] == "paid"
    # super admin + reason → refund goes through, reason stored on the refund entry, audited twice
    r = await pay_http.post(url, json={"order_id": o["id"], "amount": 100, "chargeback_override_reason": REASON},
                            headers=sup)
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    assert route.call_count == 1
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["refunds"][0]["chargeback_override_reason"] == REASON
    assert (await pay_findall("auth_audit_logs", {"event": "chargeback_refund_override"}))[0]["meta"]["reason"] == REASON
    chain = [a for coll in ("admin_audit_logs", "audit_logs") for a in await pay_findall(coll)
             if a.get("action") == "chargebacks.refund_override"]
    assert chain and chain[0]["entity"]["id"] == o["id"] and chain[0]["metadata"]["reason"] == REASON
    # once the dispute is resolved, normal staff can refund again without override
    r = await pay_http.patch(f"{CB}/{cid}", json={"status": "won"}, headers=fin)
    assert r.status_code == 200
    r = await pay_http.post(url, json={"order_id": o["id"], "amount": 50}, headers=fin)
    assert r.status_code == 200 and route.call_count == 2


async def test_chargeback_opened_between_check_and_claim_still_blocks(pay_http, pay_env):
    """Race: the chargeback row is not visible yet but the order flag is (atomic claim filter)."""
    o = await _paid(pay_http, pay_env)
    _, fin = await pay_staff(["returns.iyzico_refund"])
    await pay_set("orders", {"id": o["id"]}, {"chargeback_open": True})
    route = _ok_refund(pay_env.iyzico)
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=fin)
    assert r.status_code == 409 and route.call_count == 0
    assert not (await pay_find("orders", {"id": o["id"]})).get("refunds")


async def _approved_return(order, rid):
    from routes import deps
    await deps.db.customer_returns.insert_one({
        "id": rid, "order_id": order["id"], "order_number": order["order_number"], "status": "approved",
        "refund_amount": 100.0, "items": [{"product_id": "p1", "quantity": 1, "price": 500.0}],
        "created_at": now_iso()})
    await pay_set("orders", {"id": order["id"]}, {"status": "return_approved"})


async def test_refund_pay_and_status_refunded_blocked_while_chargeback_open(pay_http, pay_env):
    o = await _paid(pay_http, pay_env)
    _, fin = await pay_staff(["returns.refund_pay", "orders.update_status", "returns.expense_note"])
    _, sup = await pay_staff(super_admin=True)
    await _open_cb(pay_http, o, fin)
    await _approved_return(o, "ret-cb")
    r = await pay_http.post("/api/orders/returns/ret-cb/refund-pay", json={"amount": 100, "method": "havale"},
                            headers=fin)
    assert r.status_code == 409
    assert (await pay_find("customer_returns", {"id": "ret-cb"}))["status"] == "approved"
    for st in ("refunded", "partial_refunded", "cancel_refunded"):
        r = await pay_http.put(f"/api/orders/{o['id']}/status", params={"status": st}, headers=fin)
        assert r.status_code == 409, (st, r.text)
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "return_approved"
    r = await pay_http.post("/api/orders/returns/ret-cb/refund-pay",
                            json={"amount": 100, "method": "havale", "chargeback_override_reason": REASON},
                            headers=sup)
    assert r.status_code == 200, r.text
    rec = await pay_find("customer_returns", {"id": "ret-cb"})
    assert rec["status"] == "refunded" and rec["refund_payment"]["chargeback_override_reason"] == REASON
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["chargeback_open"] is True   # dispute record untouched


async def test_bulk_mark_refunded_skips_orders_with_open_chargeback(pay_http, pay_env):
    a = await _paid(pay_http, pay_env, pid="pa")
    b = await _paid(pay_http, pay_env, pid="pb")
    _, sup = await pay_staff(super_admin=True)
    await _open_cb(pay_http, a, sup)
    for o in (a, b):
        await pay_set("orders", {"id": o["id"]}, {"status": "return_approved"})
    r = await pay_http.post("/api/orders/returns/bulk-mark-refunded-silent",
                            json={"order_ids": [a["id"], b["id"]]}, headers=sup)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["updatedIds"] == [b["id"]] and j["blockedChargebackIds"] == [a["id"]]
    assert (await pay_find("orders", {"id": a["id"]}))["status"] == "return_approved"
    assert (await pay_find("orders", {"id": b["id"]}))["status"] == "refunded"
    # explicit, audited override
    r = await pay_http.post("/api/orders/returns/bulk-mark-refunded-silent",
                            json={"order_ids": [a["id"]], "chargeback_override_reason": REASON}, headers=sup)
    assert r.status_code == 200 and r.json()["updatedIds"] == [a["id"]]
    assert len(await pay_findall("auth_audit_logs", {"event": "chargeback_refund_override"})) == 1
