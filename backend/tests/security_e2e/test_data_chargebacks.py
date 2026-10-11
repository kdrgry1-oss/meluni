"""Chargebacks: permission, amount bound, never touch order status/payment_status, daily reminder."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from kv_harness import kv_find, kv_findall, kv_count, kv_order, kv_insert
from conftest import KvUsers, kv_run

BASE = "/api/admin/chargebacks"


def _order():
    return kv_order(None, status="delivered", payment_status="paid", total=1000.0,
                    iyzico_payment_id="iyz-123")


def test_create_requires_permission(kv_client, kv_users):
    o = _order()
    body = {"order_id": o["id"], "amount": 100, "deadline": "2026-12-01"}
    assert kv_client.post(BASE, json=body).status_code == 401
    assert kv_client.post(BASE, json=body, headers=KvUsers.hdr(kv_users.customer())).status_code == 403
    no_perm = kv_users.staff(perms=["orders.view", "returns.approve"])
    assert kv_client.post(BASE, json=body, headers=KvUsers.hdr(no_perm)).status_code == 403
    assert kv_client.get(BASE, headers=KvUsers.hdr(no_perm)).status_code == 403
    assert kv_count("chargebacks") == 0
    ok = kv_users.staff(perms=["returns.refund_pay"])
    r = kv_client.post(BASE, json=body, headers=KvUsers.hdr(ok))
    assert r.status_code == 200, r.text


def test_amount_bounds(kv_client, kv_users):
    o = _order()
    h = KvUsers.hdr(kv_users.staff(perms=["returns.refund_pay"]))
    for amt in (1000.02, 5000, 0, -5, "abc"):
        r = kv_client.post(BASE, json={"order_id": o["id"], "amount": amt}, headers=h)
        assert r.status_code == 400, (amt, r.text)
    assert kv_count("chargebacks") == 0
    assert kv_client.post(BASE, json={"order_id": o["id"], "amount": 1000.0}, headers=h).status_code == 200
    # update cannot exceed either
    cb = kv_findall("chargebacks")[0]
    r = kv_client.patch(f"{BASE}/{cb['id']}", json={"amount": 1500}, headers=h)
    assert r.status_code == 400
    assert kv_find("chargebacks", {"id": cb["id"]})["amount"] == 1000.0
    # unknown order
    assert kv_client.post(BASE, json={"order_id": "nope", "amount": 1}, headers=h).status_code == 404
    # NoSQL operator injection in order_id is neutralised
    assert kv_client.post(BASE, json={"order_id": {"$ne": None}, "amount": 1}, headers=h).status_code == 404


def test_order_status_and_payment_status_never_change(kv_client, kv_users):
    o = _order()
    before = kv_find("orders", {"id": o["id"]})
    h = KvUsers.hdr(kv_users.staff(perms=["returns.refund_pay"]))
    r = kv_client.post(BASE, json={"order_id": o["id"], "amount": 250.5, "deadline": "2026-11-01",
                                   "reason_code": "4837", "status": "open"}, headers=h)
    assert r.status_code == 200, r.text
    cid = r.json()["chargeback"]["id"]
    mid = kv_find("orders", {"id": o["id"]})
    assert (mid["status"], mid["payment_status"]) == (before["status"], before["payment_status"])
    assert mid["chargeback_open"] is True and mid["chargebacks"][0]["id"] == cid
    for patch in ({"status": "lost"}, {"status": "won", "outcome_notes": "kazandik"},
                  {"deadline": "2026-11-05"}, {"status": "accepted"}, {"status": "open"}):
        r = kv_client.patch(f"{BASE}/{cid}", json=patch, headers=h)
        assert r.status_code == 200, r.text
        now = kv_find("orders", {"id": o["id"]})
        assert (now["status"], now["payment_status"]) == (before["status"], before["payment_status"]), patch
        assert now["total"] == before["total"]
    kv_client.patch(f"{BASE}/{cid}", json={"status": "won"}, headers=h)
    assert kv_find("orders", {"id": o["id"]})["chargeback_open"] is False
    # invalid status refused
    assert kv_client.patch(f"{BASE}/{cid}", json={"status": "paid"}, headers=h).status_code == 400
    assert kv_count("auth_audit_logs", {"event": "chargeback_create"}) == 1
    phases = [(a["action"], a.get("phase")) for a in kv_findall("audit_logs") if a["action"].startswith("chargebacks.")]
    assert ("chargebacks.create", "intent") in phases and ("chargebacks.create", "done") in phases
    assert ("chargebacks.update", "intent") in phases and ("chargebacks.update", "done") in phases
    # a refused update (invalid status) is validated BEFORE the intent → no orphan intent record
    assert phases.count(("chargebacks.update", "intent")) == phases.count(("chargebacks.update", "done"))
    assert kv_count("auth_audit_logs", {"event": "chargeback_update"}) >= 5


def _cb(cid, deadline, status="open", order_number="FC1"):
    kv_insert("chargebacks", {"id": cid, "order_id": "o-" + cid, "order_number": order_number,
                              "amount": 99.0, "currency": "TRY", "status": status, "deadline": deadline,
                              "reason_code": "4837", "created_at": "2026-09-01T00:00:00+00:00"})


def test_reminder_alerts_once_per_day_for_deadlines_within_3_days(monkeypatch):
    import security.alerts as alerts
    send = AsyncMock(return_value={"alert_id": "a"})
    monkeypatch.setattr(alerts, "send_alert", send)
    from routes.chargebacks import remind_chargeback_deadlines
    _cb("due2", "2026-10-06")             # 2 days → alert
    _cb("overdue", "2026-10-01")          # overdue → alert
    _cb("far", "2026-10-20")              # far → no
    _cb("closed", "2026-10-05", "won")    # closed → no
    _cb("nodl", "")                       # no deadline → no

    with freeze_time("2026-10-04 09:00:00"):
        assert kv_run(remind_chargeback_deadlines()) == 2
        assert send.await_count == 1
        title, body = send.await_args.args[1], send.await_args.args[2]
        assert "2 açık itiraz" in title and "FC1" in body
        assert send.await_args.kwargs["level"] == "critical"   # overdue present
        # same day again → nothing
        assert kv_run(remind_chargeback_deadlines()) == 0
        assert send.await_count == 1
    with freeze_time("2026-10-04 23:30:00"):
        assert kv_run(remind_chargeback_deadlines()) == 0
        assert send.await_count == 1
    sent = {c["id"]: c.get("reminder_sent_on") for c in kv_findall("chargebacks")}
    assert sent["due2"] == "2026-10-04" and sent["overdue"] == "2026-10-04" and not sent["far"]
    # next day → reminded again (still open)
    with freeze_time("2026-10-05 08:00:00"):
        assert kv_run(remind_chargeback_deadlines()) == 2
        assert send.await_count == 2
    # 'far' enters the window 3 days before its deadline
    with freeze_time("2026-10-17 08:00:00"):
        n = kv_run(remind_chargeback_deadlines())
        assert n == 3 and "far" in [c["id"] for c in kv_findall("chargebacks", {"reminder_sent_on": "2026-10-17"})]


def test_reminder_failure_is_retried_same_day(monkeypatch):
    import security.alerts as alerts
    send = AsyncMock(side_effect=RuntimeError("smtp down"))
    monkeypatch.setattr(alerts, "send_alert", send)
    from routes.chargebacks import remind_chargeback_deadlines
    _cb("x", "2026-10-05")
    with freeze_time("2026-10-04 09:00:00"):
        kv_run(remind_chargeback_deadlines())
        assert not kv_find("chargebacks", {"id": "x"}).get("reminder_sent_on")
        send.side_effect = None
        send.return_value = {"alert_id": "ok"}
        assert kv_run(remind_chargeback_deadlines()) == 1
        assert kv_find("chargebacks", {"id": "x"})["reminder_sent_on"] == "2026-10-04"
