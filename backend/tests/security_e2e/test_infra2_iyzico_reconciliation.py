"""COM12 / CHAIN08 — daily iyzico reconciliation (security/reconcile_iyzico.py), iyzico mocked with respx.

Paid card orders of the last 3 days are compared with iyzico's payment detail (signed with
payment.py's own helpers): provider status, order/amount match, paymentId, charged amount vs the
recorded snapshot, recorded refunds vs provider refunds (when the response carries them).
Mismatches → integration log + critical alert. Orders and payment status are NEVER modified.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pay_harness import IYZICO_BASE, pay_findall, pay_find
from routes import deps

pytestmark = pytest.mark.asyncio
DETAIL = f"{IYZICO_BASE}/payment/detail"


def _iso(days=0.0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def _paid(oid, total=550.0, pid=None, days=-1, refunds=None, **extra):
    doc = {"id": oid, "order_number": f"FC{oid.upper()}", "total": total, "payment_method": "credit_card",
           "payment_status": "paid", "status": "confirmed", "iyzico_payment_id": pid or f"pid-{oid}",
           "paid_at": _iso(days), "created_at": _iso(days),
           "iyzico_retrieve_response": {"paymentId": pid or f"pid-{oid}", "paidPrice": f"{total:.2f}"},
           "refunds": refunds or []}
    doc.update(extra)
    await deps.db.orders.insert_one(dict(doc))
    return doc


def _ok(order, paid=None, **extra):
    d = {"status": "success", "paymentStatus": "SUCCESS", "paymentId": order["iyzico_payment_id"],
         "conversationId": order["id"], "basketId": order["order_number"], "currency": "TRY",
         "paidPrice": f"{(paid if paid is not None else order['total']):.2f}", "price": f"{order['total']:.2f}",
         "fraudStatus": 1}
    d.update(extra)
    return d


async def test_reconciliation_flags_mismatches_and_never_touches_orders(pay_env):
    from security.reconcile_iyzico import run_iyzico_reconciliation
    good = await _paid("good")
    under = await _paid("under")
    failed = await _paid("failed")
    refund = await _paid("refund", payment_status="partially_refunded",
                         refunds=[{"status": "done", "provider": "iyzico", "net_refund": 100.0, "amount": 100.0}])
    refund_ok = await _paid("refundok", payment_status="refunded",
                            refunds=[{"status": "done", "provider": "iyzico", "net_refund": 550.0}])
    changed = await _paid("changed")
    flaky = await _paid("flaky")
    await _paid("old", days=-10)                                            # outside the 3-day window
    await _paid("gift", paid_with_gift_card_only=True)                       # no card payment
    await _paid("havale", payment_method="bank_transfer")                    # not a card order
    responses = {
        good["iyzico_payment_id"]: _ok(good),
        under["iyzico_payment_id"]: _ok(under, paid=1.0),
        failed["iyzico_payment_id"]: _ok(failed, paymentStatus="FAILURE"),
        refund["iyzico_payment_id"]: _ok(refund, itemTransactions=[{"refunds": [{"refundPrice": "50.00", "status": "success"}]}]),
        refund_ok["iyzico_payment_id"]: _ok(refund_ok, refunds=[{"price": "550.00"}]),
        changed["iyzico_payment_id"]: _ok(changed, paid=600.0),
    }
    calls = []

    def _detail(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body["paymentId"])
        if body["paymentId"] == flaky["iyzico_payment_id"]:
            return httpx.Response(502, text="<html>bad gateway</html>")
        return httpx.Response(200, json=responses[body["paymentId"]])
    pay_env.iyzico.post(DETAIL).mock(side_effect=_detail)
    before = {o["id"]: (o["payment_status"], o["status"]) for o in await pay_findall("orders")}

    res = await run_iyzico_reconciliation(days=3)
    assert res["ok"] is True and res["checked"] == 7, res
    assert res["matched"] == 2 and res["mismatches"] == 4 and res["unverified"] == 1
    assert sorted(calls) == sorted(o["iyzico_payment_id"] for o in (good, under, failed, refund, refund_ok, changed, flaky))
    logs = {x["order_number"]: {d["kind"] for d in x["diffs"]}
            for x in await pay_findall("integration_logs", {"event": "reconciliation_mismatch"})}
    assert logs == {
        "FCUNDER": {"amount_or_order_mismatch", "charged_amount_changed"},
        "FCFAILED": {"not_paid_at_provider"},
        "FCREFUND": {"refund_mismatch"},
        "FCCHANGED": {"charged_amount_changed"},
    }
    ref = next(x for x in await pay_findall("integration_logs", {"event": "reconciliation_mismatch"})
               if x["order_number"] == "FCREFUND")
    assert ref["diffs"][0]["recorded_refunds"] == 100.0 and ref["diffs"][0]["provider_refunds"] == 50.0
    assert await pay_findall("integration_logs", {"event": "reconciliation_unverified"})
    alerts = await pay_findall("alerts", {"kind": "iyzico_reconciliation_mismatch"})
    assert len(alerts) == 1 and alerts[0]["level"] == "critical" and "FCUNDER" in alerts[0]["body"]
    # invariant 1: reconciliation never changes payment / order status
    after = {o["id"]: (o["payment_status"], o["status"]) for o in await pay_findall("orders")}
    assert after == before
    assert not await pay_findall("orders", {"needs_reconciliation": True})
    st = await pay_find("settings", {"id": "iyzico_reconciliation"})
    assert st["last"]["mismatches"] == 4


async def test_reconciliation_skips_when_iyzico_not_configured(pay_env):
    from security.reconcile_iyzico import run_iyzico_reconciliation
    await deps.db.settings.delete_one({"id": "iyzico"})
    await _paid("x")
    res = await run_iyzico_reconciliation()
    assert res["skipped"] == "iyzico_not_configured" and res["checked"] == 0


async def test_refund_total_extraction_unit():
    from security.reconcile_iyzico import provider_refund_total, recorded_refund_total
    assert provider_refund_total({"status": "success"}) is None
    assert provider_refund_total({"refunds": []}) == 0.0
    assert provider_refund_total({"itemTransactions": [{"refundedPrice": "10.5"}, {"refundPrice": "4.5"}]}) == 15.0
    assert provider_refund_total({"refunds": [{"price": "5", "status": "failure"}, {"refundPrice": "7"}]}) == 7.0
    assert recorded_refund_total({"refunds": [{"status": "done", "net_refund": 10}, {"status": "unknown", "amount": 99},
                                              {"status": "done", "provider": "manual", "amount": 5}]}) == 10.0


async def test_job_registered_daily():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    assert 'run_iyzico_reconciliation, "cron", hour=4, minute=23, id="iyzico_daily_reconciliation"' in src
