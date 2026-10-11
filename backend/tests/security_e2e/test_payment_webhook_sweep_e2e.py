"""E2E: iyzico webhook, payment-detail verification, missed-side-effect sweep, reconcile cron and
unpaid-card auto-cancel (CLAUDE.md invariants 1, 3, 4, 5, 6)."""
import httpx
import pytest

from pay_harness import (
    IYZICO_BASE, iyz_auth_ok, iyz_json, mock_3ds_auth, pay_callback, pay_card_order, pay_drain,
    pay_find, pay_findall, pay_set, pay_stock, now_iso, webhook_signature,
)

pytestmark = pytest.mark.asyncio

DETAIL = f"{IYZICO_BASE}/payment/detail"


def _wh_payload(order, pid="pid-w", status="SUCCESS"):
    return {"iyziEventType": "THREE_DS_AUTH", "iyziPaymentId": pid, "token": "",
            "paymentConversationId": order["id"], "status": status}


async def _post_webhook(http, payload, sig=None):
    return await http.post("/api/payment/webhook", json=payload,
                           headers={"X-IYZ-SIGNATURE-V3": sig if sig is not None else webhook_signature(payload)})


async def _snapshot(order_id):
    o = await pay_find("orders", {"id": order_id})
    return {k: o.get(k) for k in ("status", "payment_status", "iyzico_payment_id", "needs_reconciliation",
                                  "reconcile_payment_id", "order_confirmed_notified")}


# ═════════════════════════════════════ webhook ═══════════════════════════════════════════════
async def test_webhook_invalid_signature_rejected(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    detail = pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json(iyz_auth_ok(o, paymentStatus="SUCCESS")))
    before = await _snapshot(o["id"])
    for sig in ("", "deadbeef", webhook_signature(_wh_payload(o), secret="wrong-secret")):
        r = await _post_webhook(pay_http, _wh_payload(o), sig=sig)
        assert r.status_code == 401
    assert detail.call_count == 0
    assert await _snapshot(o["id"]) == before


async def test_webhook_valid_signature_finalize_exception_returns_500(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(DETAIL).mock(side_effect=httpx.ConnectError("iyzico down"))
    before = await _snapshot(o["id"])
    r = await _post_webhook(pay_http, _wh_payload(o))
    assert r.status_code == 500                      # iyzico will retry the signed notification
    assert await _snapshot(o["id"]) == before


async def test_webhook_detail_with_empty_payment_status_leaves_order_untouched(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json(iyz_auth_ok(o, payment_id="pid-w", paymentStatus="")))
    before = await _snapshot(o["id"])
    r = await _post_webhook(pay_http, _wh_payload(o))
    assert r.status_code == 500
    assert await _snapshot(o["id"]) == before
    # the 15-min reconcile cron (raise_on_uncertain=False) must not touch it either
    await pay_set("orders", {"id": o["id"]}, {"reconcile_payment_id": "pid-w"})
    import scheduler
    await scheduler.reconcile_charged_but_unrecorded_orders()
    cur = await _snapshot(o["id"])
    assert cur["payment_status"] == "pending" and cur["status"] == "awaiting_payment"


async def test_webhook_verified_success_marks_paid_once(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json(iyz_auth_ok(o, payment_id="pid-w", paymentStatus="SUCCESS")))
    for _ in range(2):
        r = await _post_webhook(pay_http, _wh_payload(o))
        assert r.status_code == 200
    await pay_drain()
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"
    assert len(pay_env.outbox.events("order_confirmed")) == 1
    assert len(pay_env.outbox.new_order_pushes(o["id"])) == 1


async def test_webhook_body_alone_is_never_trusted(pay_http, pay_env):
    """Signed SUCCESS webhook but iyzico payment-detail says FAILURE → not paid."""
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json(
        iyz_auth_ok(o, payment_id="pid-w", status="success", paymentStatus="FAILURE")))
    r = await _post_webhook(pay_http, _wh_payload(o))
    assert r.status_code == 200
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] != "paid" and cur["status"] == "awaiting_payment"


async def test_webhook_non_success_status_ignored(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    detail = pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json({}))
    before = await _snapshot(o["id"])
    r = await _post_webhook(pay_http, _wh_payload(o, status="FAILURE"))
    assert r.status_code == 200 and r.json().get("ignored")
    assert detail.call_count == 0
    assert await _snapshot(o["id"]) == before


# ═════════════════════════════════════ side-effect sweep ═════════════════════════════════════
async def test_sweep_sends_missed_confirmation_exactly_once(pay_http, pay_env):
    import scheduler
    from routes import deps
    o = await pay_card_order(pay_http)
    await deps.db.coupons.insert_one({"id": "cpn-s", "code": "SWEEP", "is_active": True})
    # paid transition happened (atomic) but the process died before side effects
    await pay_set("orders", {"id": o["id"]}, {
        "payment_status": "paid", "status": "confirmed", "iyzico_payment_id": "pid-s",
        "payment_id": "pid-s", "paid_at": now_iso(minutes=-30),
        "applied_promotions": [{"coupon_id": "cpn-s", "code": "SWEEP", "discount": 5.0}]})
    for _ in range(2):
        await scheduler.sweep_paid_order_side_effects()
        await pay_drain()
    assert len(pay_env.outbox.events("order_confirmed")) == 1
    assert len(pay_env.outbox.new_order_pushes(o["id"])) == 1
    assert len(await pay_findall("coupon_redemptions", {"order_id": o["id"]})) == 1
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["order_confirmed_notified"] is True and cur["coupon_redemptions_recorded"] is True
    assert cur["status"] == "confirmed" and cur["payment_status"] == "paid"   # sweep never changes status


async def test_sweep_skips_unpaid_and_recent_orders(pay_http, pay_env):
    import scheduler
    a = await pay_card_order(pay_http)                         # unpaid
    b = await pay_card_order(pay_http, pid="p2")
    await pay_set("orders", {"id": b["id"]}, {"payment_status": "paid", "status": "confirmed",
                                              "iyzico_payment_id": "pid-b", "paid_at": now_iso(minutes=-2)})
    await scheduler.sweep_paid_order_side_effects()
    await pay_drain()
    assert pay_env.outbox.notifications == []
    assert (await pay_find("orders", {"id": a["id"]}))["status"] == "awaiting_payment"


# ═════════════════════════════════════ reconcile / auto-cancel ═══════════════════════════════
async def test_reconcile_recovers_charged_order(pay_http, pay_env):
    import scheduler
    o = await pay_card_order(pay_http)
    mock_3ds_auth(pay_env.iyzico, httpx.ReadTimeout("slow bank"))
    await pay_callback(pay_http, o["id"], "pid-r")             # uncertain → needs_reconciliation
    pay_env.iyzico.post(DETAIL).mock(return_value=iyz_json(
        iyz_auth_ok(o, payment_id="pid-r", paymentStatus="SUCCESS")))
    await scheduler.reconcile_charged_but_unrecorded_orders()
    await pay_drain()
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"
    assert not cur.get("needs_reconciliation")
    assert len(pay_env.outbox.events("order_confirmed")) == 1


async def test_auto_cancel_unpaid_card_order_restocks_and_spares_paid(pay_http, pay_env):
    import scheduler
    unpaid = await pay_card_order(pay_http, stock=5)
    paid = await pay_card_order(pay_http)
    assert await pay_stock("p1") == 3
    await pay_set("orders", {"id": paid["id"]}, {"payment_status": "paid", "status": "confirmed",
                                                 "iyzico_payment_id": "pid-p"})
    for oid in (unpaid["id"], paid["id"]):
        await pay_set("orders", {"id": oid}, {"created_at": now_iso(hours=-4)})
    await scheduler.auto_cancel_unpaid_card_orders()
    await scheduler.auto_cancel_unpaid_card_orders()          # idempotent restock
    u = await pay_find("orders", {"id": unpaid["id"]})
    assert u["status"] == "payment_failed" and u["payment_status"] == "expired"
    assert u["_restocked_by_autocancel"] is True
    p = await pay_find("orders", {"id": paid["id"]})
    assert p["status"] == "confirmed" and p["payment_status"] == "paid"
    assert await pay_stock("p1") == 4                          # exactly one unit back
    # Money actually captured later (late callback / reconcile): the order is recovered as paid
    # (invariant 4) and the stock that auto-cancel gave back is taken again EXACTLY once.
    from routes.payment import _mark_order_from_payment
    data = iyz_auth_ok(unpaid, payment_id="pid-late")
    assert await _mark_order_from_payment(unpaid["id"], dict(data)) is True
    assert await _mark_order_from_payment(unpaid["id"], dict(data)) is True   # replay
    u = await pay_find("orders", {"id": unpaid["id"]})
    assert u["status"] == "confirmed" and u["payment_status"] == "paid"
    assert u.get("_redecremented_after_reconcile") is True and not u.get("_restocked_by_autocancel")
    assert await pay_stock("p1") == 3
    mv = await pay_findall("stock_movements", {"order_id": unpaid["id"], "type": "reconcile_redecrement"})
    assert len(mv) == 1
