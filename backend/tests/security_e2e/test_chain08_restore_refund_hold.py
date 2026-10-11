"""CHAIN08 — refunds during / after a database restore are blocked AUTOMATICALLY (not by procedure).

* While ``restore_mode`` is ON every money-out refund path (iyzico card refund, refund-pay marking, bulk
  silent refund marking, manual order/return status → refunded/partial_refunded/cancel_refunded, bulk
  status, the silent rooftr status fix, Temu refund/approve) is closed exactly like ``freeze_refunds``
  (same guard: ``security_switches.require_refunds_allowed`` via ``require_switch_off("freeze_refunds")``).
* Turning ``restore_mode`` OFF sets a "post-restore refund hold" IN THE SAME WRITE and schedules (does not
  await in the request) an iyzico reconciliation over the restore window. Only a complete, clean run
  clears the hold; otherwise the hold stays and a critical alert is sent.
* A super admin can lift the hold with a written reason (fail-closed audit, critical alert).
iyzico is mocked with respx; nothing leaves the process.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pay_harness import (
    IYZICO_BASE, iyz_auth_ok, iyz_json, mock_3ds_auth, pay_callback, pay_card_order, pay_drain,
    pay_find, pay_findall, pay_set, pay_set_switch, pay_staff,
)
from routes import deps

pytestmark = pytest.mark.asyncio
REFUND = f"{IYZICO_BASE}/payment/refund"
DETAIL = f"{IYZICO_BASE}/payment/detail"
HOLD = "post_restore_refund_hold"
MSG = "Veritabanı geri yükleme sonrası mutabakat tamamlanana kadar iade yapılamaz"


def _iso(days=0.0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def _paid_order(http, env):
    o = await pay_card_order(http)
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id="pid-1", paid=o["total"]))
    await pay_callback(http, o["id"], "pid-1")
    await pay_drain()
    env.iyzico.routes.clear()
    o = await pay_find("orders", {"id": o["id"]})
    assert o["payment_status"] == "paid"
    return o


def _refund_ok(router):
    return router.post(REFUND).mock(return_value=iyz_json({"status": "success", "paymentId": "pid-1"}))


def _detail_for(order, **extra):
    d = {"status": "success", "paymentStatus": "SUCCESS", "paymentId": order["iyzico_payment_id"],
         "conversationId": order["id"], "basketId": order["order_number"], "currency": "TRY",
         "paidPrice": f"{float(order['iyzico_retrieve_response']['paidPrice']):.2f}"
         if order.get("iyzico_retrieve_response", {}).get("paidPrice") else f"{order['total']:.2f}",
         "price": f"{order['total']:.2f}", "fraudStatus": 1, "refunds": []}
    d.update(extra)
    return d


def _mock_detail(router, responses):
    calls = []

    def _h(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body["paymentId"])
        return httpx.Response(200, json=responses[body["paymentId"]])
    router.post(DETAIL).mock(side_effect=_h)
    return calls


async def _toggle(http, hdr, name, on, reason="geri yukleme tatbikati"):
    import security_switches
    r = await http.post(f"/api/admin/security-switches/{name}", headers=hdr,
                        json={"on": on, "reason": reason} if on else {"on": False})
    security_switches.invalidate_cache()
    return r


async def _hold():
    doc = await pay_find("settings", {"id": "security_switches"}) or {}
    return (doc.get("switches") or {}).get(HOLD) or {}


async def _actions(prefix=""):
    return [a["action"] for a in await pay_findall("audit_logs") if a["action"].startswith(prefix)]


async def _seed_return(order, rid="ret-1"):
    await deps.db.customer_returns.insert_one({"id": rid, "order_id": order["id"], "status": "approved",
                                               "order_number": order["order_number"], "refund_amount": 100})
    await pay_set("orders", {"id": order["id"]}, {"status": "return_approved"})


async def _hit_every_refund_path(http, hdr, order, rid):
    """Each money-out refund path once. Returns {label: response}."""
    oid, onum = order["id"], order["order_number"]
    out = {
        "iyzico_refund": await http.post("/api/integrations/iyzico/refund",
                                         json={"order_id": oid, "amount": 10}, headers=hdr),
        "refund_pay": await http.post(f"/api/orders/returns/{rid}/refund-pay",
                                      json={"amount": 100, "method": "havale"}, headers=hdr),
        "bulk_mark_refunded": await http.post("/api/orders/returns/bulk-mark-refunded-silent",
                                              json={"order_ids": [oid]}, headers=hdr),
        "return_status_refunded": await http.post(f"/api/orders/returns/{rid}/status",
                                                  json={"status": "refunded"}, headers=hdr),
        "return_status_partial": await http.post(f"/api/orders/returns/{rid}/status",
                                                 json={"status": "partial_refunded"}, headers=hdr),
        "rooftr_silent_refunded": await http.post("/api/admin/rooftr/set-return-status",
                                                  json={"order_numbers": [onum], "status": "refunded"},
                                                  headers=hdr),
        "temu_refund": await http.post("/api/integrations/temu/returns/t-1/refund",
                                       json={"refund_amount": 10}, headers=hdr),
        "temu_approve": await http.post("/api/integrations/temu/returns/t-1/approve",
                                        json={"refund_amount": 10}, headers=hdr),
    }
    for st in ("refunded", "partial_refunded", "cancel_refunded"):
        out[f"order_status_{st}"] = await http.put(f"/api/orders/{oid}/status?status={st}", headers=hdr)
    return out


# ═════════════════════ 1) every refund path is closed by each of the three blockers ═════════════════════
@pytest.mark.parametrize("blocker", ["restore_mode", HOLD, "freeze_refunds"])
async def test_every_refund_path_blocked_by_restore_mode_hold_and_freeze(pay_http, pay_env, blocker):
    o = await _paid_order(pay_http, pay_env)
    await _seed_return(o)
    _, hdr = await pay_staff(super_admin=True)
    route = _refund_ok(pay_env.iyzico)
    await pay_set_switch(blocker, True)
    res = await _hit_every_refund_path(pay_http, hdr, o, "ret-1")
    bad = {k: (r.status_code, r.text[:120]) for k, r in res.items() if r.status_code != 423}
    assert not bad, bad
    if blocker != "freeze_refunds":
        assert all(MSG in r.json()["detail"] for r in res.values()), {k: r.json() for k, r in res.items()}
    # bulk status goes through the same guard per order (reported as a per-order error, nothing changes)
    r = await pay_http.post(f"/api/orders/bulk-status?status=refunded", json=[o["id"]], headers=hdr)
    assert r.status_code == 200 and r.json()["error_count"] == 1 and r.json()["success_count"] == 0
    # nothing moved, nothing was marked
    assert route.call_count == 0
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "return_approved" and cur["payment_status"] == "paid" and not cur.get("refunds")
    assert not cur.get("refund_paid_at")
    assert (await pay_find("customer_returns", {"id": "ret-1"}))["status"] == "approved"
    # no-money-movement actions stay available: uncertain-refund resolve, non-refund status changes
    r = await pay_http.post("/api/integrations/iyzico/refund/resolve",
                            json={"order_id": o["id"], "claim_id": "nope", "outcome": "failed"}, headers=hdr)
    assert r.status_code == 404
    r = await pay_http.put(f"/api/orders/{o['id']}/status?status=returned", headers=hdr)
    assert r.status_code == 200, r.text


async def test_refund_paths_open_again_without_blockers(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _, hdr = await pay_staff(super_admin=True)
    route = _refund_ok(pay_env.iyzico)
    for name in ("restore_mode", HOLD, "freeze_refunds"):
        await pay_set_switch(name, False)
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "done" and route.call_count == 1


# ═════════════════════ 2) restore OFF → hold in the same write + scheduled reconciliation ═════════════════════
async def test_restore_off_sets_hold_atomically_and_schedules_reconciliation(pay_http, pay_env, monkeypatch):
    import security_switches as ss
    o = await _paid_order(pay_http, pay_env)
    # an order paid BEFORE the backup but refundable after it — the window's refund look-back must cover it
    old = {"id": "old-1", "order_number": "FCOLD1", "total": 300.0, "payment_method": "credit_card",
           "payment_status": "paid", "status": "delivered", "iyzico_payment_id": "pid-old",
           "paid_at": _iso(-25), "created_at": _iso(-25), "refunds": [],
           "iyzico_retrieve_response": {"paymentId": "pid-old", "paidPrice": "300.00"}}
    await deps.db.orders.insert_one(dict(old))
    real_run = ss.run_post_restore_reconciliation
    scheduled = []

    async def _rec(hold_id, *, actor=None, days=None):
        scheduled.append(hold_id)
    monkeypatch.setattr(ss, "run_post_restore_reconciliation", _rec)

    sa, hdr = await pay_staff(super_admin=True)
    assert (await _toggle(pay_http, hdr, "restore_mode", True)).status_code == 200
    assert not (await _hold()).get("on")
    # the restore_mode entry must be "older" than the restore → move it 3 days back
    await pay_set("settings", {"id": "security_switches"}, {"switches.restore_mode.at": _iso(-3)})
    r = await _toggle(pay_http, hdr, "restore_mode", False)
    assert r.status_code == 200, r.text
    fin = r.json()["restore_finalize"]
    assert fin["refund_hold"]["on"] is True and fin["refund_hold"]["reconciliation"] == "background_task"
    hold = await _hold()
    assert hold["on"] is True and hold["status"] == "pending" and scheduled == [hold["id"]]
    # same write as the restore_mode OFF entry (no window with "restore off, no hold")
    doc = await pay_find("settings", {"id": "security_switches"})
    assert doc["switches"]["restore_mode"]["on"] is False and doc["switches"]["restore_mode"]["at"] == hold["set_at"]
    assert [h["name"] for h in doc["history"][-2:]] == ["restore_mode", HOLD]
    # window: restore start − margin − refund look-back, never shorter than 7 days
    since = datetime.fromisoformat(hold["window_since"])
    assert since <= datetime.now(timezone.utc) - timedelta(days=3 + 2 + 30 - 0.01)
    # the generic switch endpoint cannot touch the hold
    _, hdr2 = await pay_staff(super_admin=True)          # epoch was bumped by finalize → fresh token
    assert (await _toggle(pay_http, hdr2, HOLD, False)).status_code == 404
    listed = (await pay_http.get("/api/admin/security-switches", headers=hdr2)).json()
    assert listed["refund_hold"]["on"] is True and listed["refund_hold"]["id"] == hold["id"]
    # refunds are blocked while the hold is on (no iyzico call)
    route = _refund_ok(pay_env.iyzico)
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=hdr2)
    assert r.status_code == 423 and MSG in r.json()["detail"] and route.call_count == 0

    # the daily 3-day reconciliation never clears the hold
    calls = _mock_detail(pay_env.iyzico, {"pid-1": _detail_for(o), "pid-old": _detail_for(old)})
    from security.reconcile_iyzico import run_iyzico_reconciliation
    await run_iyzico_reconciliation()
    assert (await _hold())["on"] is True and "pid-old" not in calls

    # the scheduled post-restore run covers the whole window (incl. the 25-day-old order) and clears it
    calls.clear()
    res = await real_run(hold["id"])
    assert res["cleared"] is True and res["days"] >= 35, res
    assert sorted(calls) == ["pid-1", "pid-old"]
    h2 = await _hold()
    assert h2["on"] is False and h2["status"] == "cleared" and h2["attempts"] == 1
    acts = await _actions("security.refund_hold_")
    assert "security.refund_hold_clear" in acts and "security.refund_hold_cleared" in acts
    clear_rec = [a for a in await pay_findall("audit_logs") if a["action"] == "security.refund_hold_clear"]
    assert all(a["actor"]["id"].startswith("system:") for a in clear_rec)
    # refunds open again
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=hdr2)
    assert r.status_code == 200 and r.json()["status"] == "done" and route.call_count == 1
    # invariant 1: reconciliation never touched payment/order status
    cur = await pay_find("orders", {"id": "old-1"})
    assert (cur["payment_status"], cur["status"]) == ("paid", "delivered")


async def test_restore_off_runs_reconciliation_automatically_end_to_end(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    _mock_detail(pay_env.iyzico, {"pid-1": _detail_for(o)})
    _, hdr = await pay_staff(super_admin=True)
    assert (await _toggle(pay_http, hdr, "restore_mode", True)).status_code == 200
    r = await _toggle(pay_http, hdr, "restore_mode", False)
    assert r.status_code == 200
    # the background task ran after the response (reconciliation clean → hold cleared, refunds open)
    h = await _hold()
    assert h["on"] is False and h["status"] == "cleared", h
    st = await pay_find("settings", {"id": "iyzico_reconciliation"})
    assert st["last"]["checked"] == 1 and st["last"]["matched"] == 1
    _, hdr2 = await pay_staff(super_admin=True)
    route = _refund_ok(pay_env.iyzico)
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=hdr2)
    assert r.status_code == 200 and route.call_count == 1


# ═════════════════════ 3) failed / incomplete reconciliation keeps the hold + alerts ═════════════════════
@pytest.mark.parametrize("case", ["refund_unknown_locally", "refund_info_missing", "provider_error",
                                  "iyzico_not_configured"])
async def test_failed_reconciliation_keeps_hold_and_alerts(pay_http, pay_env, case):
    o = await _paid_order(pay_http, pay_env)
    if case == "refund_unknown_locally":
        # CHAIN08: a refund completed at iyzico after the backup → the restored DB does not know it
        _mock_detail(pay_env.iyzico, {"pid-1": _detail_for(o, refunds=[{"refundPrice": "300.00"}])})
        expect = "uyuşmazlık:1"
    elif case == "refund_info_missing":
        d = _detail_for(o)
        d.pop("refunds")
        _mock_detail(pay_env.iyzico, {"pid-1": d})
        expect = "iade_bilgisi_yok:1"
    elif case == "provider_error":
        pay_env.iyzico.post(DETAIL).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))
        expect = "doğrulanamayan:1"
    else:
        await deps.db.settings.delete_one({"id": "iyzico"})
        expect = "atlandı:iyzico_not_configured"
    _, hdr = await pay_staff(super_admin=True)
    assert (await _toggle(pay_http, hdr, "restore_mode", True)).status_code == 200
    assert (await _toggle(pay_http, hdr, "restore_mode", False)).status_code == 200
    h = await _hold()
    assert h["on"] is True and h["status"] == "failed" and expect in h["last_problems"], h
    alerts = await pay_findall("alerts", {"kind": "post_restore_reconciliation_failed"})
    assert alerts and alerts[0]["level"] == "critical"
    assert "security.post_restore_reconciliation_failed" in await _actions("security.")
    if case != "iyzico_not_configured":
        _, hdr2 = await pay_staff(super_admin=True)
        route = _refund_ok(pay_env.iyzico)
        r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10},
                                headers=hdr2)
        assert r.status_code == 423 and MSG in r.json()["detail"] and route.call_count == 0
        cur = await pay_find("orders", {"id": o["id"]})
        assert not cur.get("refunds") and cur["payment_status"] == "paid"


async def test_truncated_reconciliation_does_not_clear(pay_env, monkeypatch):
    import security_switches as ss
    import security.reconcile_iyzico as rec

    async def _fake(days=3, limit=1000):
        return {"ok": True, "checked": limit, "matched": limit, "mismatches": 0, "unverified": 0,
                "refund_check_unavailable": 0, "truncated": True}
    monkeypatch.setattr(rec, "run_iyzico_reconciliation", _fake)
    h = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))
    res = await ss.run_post_restore_reconciliation(h["id"])
    assert res["cleared"] is False and "sonuç_kesildi" in res["problems"]
    assert (await _hold())["on"] is True


async def test_reconciliation_audit_failure_keeps_hold(pay_env, monkeypatch):
    import security_switches as ss
    _mock_detail(pay_env.iyzico, {})
    h = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))
    import security.audit_chain as ac

    async def _down(*a, **k):
        raise RuntimeError("audit store down (test)")
    monkeypatch.setattr(ac, "append_chained", _down)
    res = await ss.run_post_restore_reconciliation(h["id"])
    assert res["cleared"] is False and "denetim_kaydı_yazılamadı" in res["problems"]
    assert (await _hold())["on"] is True


async def test_stale_run_cannot_clear_a_newer_hold(pay_env):
    import security_switches as ss
    _mock_detail(pay_env.iyzico, {})
    first = await ss.ensure_refund_hold(None, restore_on_at=_iso(-10))
    second = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))      # another restore
    assert second["id"] != first["id"]
    # window of the new hold still covers the older restore's window
    assert second["window_since"] <= first["window_since"]
    res = await ss.run_post_restore_reconciliation(first["id"])
    assert res.get("skipped") == "hold_replaced"
    assert (await _hold())["id"] == second["id"] and (await _hold())["on"] is True


# ═════════════════════ 4) super-admin manual release / re-run ═════════════════════
async def test_manual_release_requires_super_admin_reason_and_fail_closed_audit(pay_http, pay_env, monkeypatch):
    import security_switches as ss
    o = await _paid_order(pay_http, pay_env)
    h = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))
    _, staff_hdr = await pay_staff(["returns.iyzico_refund", "orders.view"])
    sa, hdr = await pay_staff(super_admin=True)
    url = "/api/admin/security-switches/refund-hold/release"
    assert (await pay_http.post(url, json={"reason": "iyzico panelinde kontrol edildi"}, headers=staff_hdr)).status_code == 403
    assert (await pay_http.post(url, json={"reason": "kısa"}, headers=hdr)).status_code == 400
    await pay_set_switch("restore_mode", True)
    assert (await pay_http.post(url, json={"reason": "iyzico panelinde kontrol edildi"}, headers=hdr)).status_code == 409
    await pay_set_switch("restore_mode", False)
    # audit store down → 503, the hold stays
    import security.audit_chain as ac
    real = ac.append_chained

    async def _down(*a, **k):
        raise RuntimeError("audit store down (test)")
    monkeypatch.setattr(ac, "append_chained", _down)
    r = await pay_http.post(url, json={"reason": "iyzico panelinde kontrol edildi"}, headers=hdr)
    assert r.status_code == 503
    assert (await _hold())["on"] is True
    monkeypatch.setattr(ac, "append_chained", real)
    # success
    r = await pay_http.post(url, json={"reason": "iyzico panelinde tum iadeler kontrol edildi"}, headers=hdr)
    assert r.status_code == 200 and r.json()["hold_id"] == h["id"]
    cur = await _hold()
    assert cur["on"] is False and cur["status"] == "released" and cur["released_by"] == sa["email"]
    assert cur["release_reason"] == "iyzico panelinde tum iadeler kontrol edildi"
    acts = await _actions("security.refund_hold_")
    assert acts.count("security.refund_hold_release") == 2          # intent + done
    assert "security.refund_hold_released" in acts
    alerts = await pay_findall("alerts", {"kind": "refund_hold_released"})
    assert alerts and alerts[0]["level"] == "critical"
    assert await pay_findall("auth_audit_logs", {"event": "refund_hold_release"})
    # second release → 409 (nothing to release)
    assert (await pay_http.post(url, json={"reason": "iyzico panelinde kontrol edildi"}, headers=hdr)).status_code == 409
    route = _refund_ok(pay_env.iyzico)
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": 10}, headers=hdr)
    assert r.status_code == 200 and route.call_count == 1


async def test_manual_rerun_endpoint_clears_after_fix(pay_http, pay_env):
    import security_switches as ss
    o = await _paid_order(pay_http, pay_env)
    _, staff_hdr = await pay_staff(["returns.iyzico_refund"])
    _, hdr = await pay_staff(super_admin=True)
    url = "/api/admin/security-switches/refund-hold/reconcile"
    assert (await pay_http.post(url, json={}, headers=hdr)).status_code == 409        # no hold
    h = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))
    assert (await pay_http.post(url, json={}, headers=staff_hdr)).status_code == 403
    _mock_detail(pay_env.iyzico, {"pid-1": _detail_for(o)})
    r = await pay_http.post(url, json={"days": 60}, headers=hdr)
    assert r.status_code == 200 and r.json()["scheduled"] == "background_task"
    cur = await _hold()
    assert cur["on"] is False and cur["status"] == "cleared" and cur["id"] == h["id"]
    assert cur["last_run"]["checked"] == 1
    assert "security.refund_hold_reconcile_requested" in await _actions("security.")


async def test_interrupted_reconciliation_is_resumed_by_scheduler_but_failed_is_not(pay_env, monkeypatch):
    """Süreç mutabakat sırasında yeniden başlarsa kilit 'running'de kalır; 30 dk'lık iş 2 saat sonra
    yeniden başlatır. 'failed' koşu (insan incelemesi gerekir) tekrar tekrar çalıştırılmaz."""
    import security_switches as ss
    from routes.deps import db
    calls = []

    async def _rec(hold_id=None, **k):
        calls.append(hold_id)
        return {"ok": True, "cleared": True}
    h = await ss.ensure_refund_hold(None, restore_on_at=_iso(-1))
    monkeypatch.setattr(ss, "run_post_restore_reconciliation", _rec)
    _k = f"switches.{ss.REFUND_HOLD_KEY}"
    # yeni başladı → dokunma
    await db.settings.update_one({"id": ss._DOC_ID}, {"$set": {f"{_k}.status": "running",
                                                               f"{_k}.last_run_at": _iso(0)}})
    assert (await ss.resume_interrupted_post_restore_reconciliation())["resumed"] is False
    # 3 saattir 'running' → yarıda kalmış → yeniden başlat
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    await db.settings.update_one({"id": ss._DOC_ID}, {"$set": {f"{_k}.last_run_at": old}})
    assert (await ss.resume_interrupted_post_restore_reconciliation())["resumed"] is True
    assert calls == [h["id"]]
    # başarısız koşu → otomatik tekrar YOK
    await db.settings.update_one({"id": ss._DOC_ID}, {"$set": {f"{_k}.status": "failed"}})
    assert (await ss.resume_interrupted_post_restore_reconciliation())["resumed"] is False
    assert len(calls) == 1


def test_resume_job_registered_every_30_minutes():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler)
    assert 'id="post_restore_refund_hold_resume"' in src
    assert "resume_interrupted_post_restore_reconciliation, \"interval\", minutes=30" in src
