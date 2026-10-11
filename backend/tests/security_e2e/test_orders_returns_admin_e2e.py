"""E2E: returns (guest verify lock, BOLA on guest orders, returnable-quantity cap), refund-pay cap
and partial refunds, MNG webhook terminal-state protection, coupon redemption idempotency, and
admin order endpoints (mass assignment, RBAC, havale confirmation path — CLAUDE.md invariant 1)."""
import asyncio

import pytest

from pay_harness import (
    SHIP, iyz_auth_ok, mock_3ds_auth, now_iso, pay_callback, pay_card_order, pay_create_order,
    pay_customer, pay_drain, pay_find, pay_findall, pay_seed_product, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio


async def _delivered_paid_order(http, env, pid="p1", ship=None):
    kw = {"ship": ship} if ship else {}
    o = await pay_card_order(http, pid=pid, **kw)
    env.iyzico.routes.clear()
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id=f"pid-{o['id'][:6]}"))
    await pay_callback(http, o["id"], f"pid-{o['id'][:6]}")
    await pay_drain()
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered", "delivered_at": now_iso(days=-2)})
    o = await pay_find("orders", {"id": o["id"]})
    assert o["payment_status"] == "paid" and o["user_id"] is None
    return o


async def _guest_return(http, order, email=None, phone=None, items=(0,)):
    body = {"items": list(items), "reason": "Beden uymadi"}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    return await http.post(f"/api/orders/by-number/{order['order_number']}/return-request", json=body)


# ═════════════════════════════════════ returns ═══════════════════════════════════════════════
async def test_guest_return_wrong_contact_locks_order_after_5(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    other = await _delivered_paid_order(pay_http, pay_env, pid="p2")
    for i in range(5):
        r = await _guest_return(pay_http, o, email=f"wrong{i}@example.test", phone="05000000000")
        assert r.status_code == 403, r.text
    r = await _guest_return(pay_http, o, email=SHIP["email"])     # correct contact, but locked now
    assert r.status_code == 429 and "deneme" in r.json()["detail"]
    assert await pay_findall("customer_returns", {"order_id": o["id"]}) == []
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "delivered"
    # the lock is per order — another order of the same customer is unaffected
    r = await _guest_return(pay_http, other, email=SHIP["email"])
    assert r.status_code == 200, r.text


async def test_member_with_unverified_email_cannot_return_guest_order(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    _, hdr = await pay_customer(email=SHIP["email"], verified=False)
    r = await pay_http.post(f"/api/orders/{o['id']}/return-request", json={"items": [0], "reason": "x"},
                            headers=hdr)
    assert r.status_code == 403
    assert await pay_findall("customer_returns") == []
    _, hdr2 = await pay_customer(email=SHIP["email"], verified=True)
    r = await pay_http.post(f"/api/orders/{o['id']}/return-request", json={"items": [0], "reason": "x"},
                            headers=hdr2)
    assert r.status_code == 200, r.text


async def test_member_cannot_return_other_members_order(pay_http, pay_env):
    owner, ohdr = await pay_customer(email="owner@example.test")
    await pay_seed_product("p1", stock=3)
    r = await pay_create_order(pay_http, headers=ohdr)
    oid = r.json()["order_id"]
    await pay_set("orders", {"id": oid}, {"status": "delivered", "payment_status": "paid",
                                          "delivered_at": now_iso(days=-1)})
    _, hdr = await pay_customer(email="intruder@example.test")
    r = await pay_http.post(f"/api/orders/{oid}/return-request", json={"items": [0], "reason": "x"}, headers=hdr)
    assert r.status_code == 403


async def test_returnable_quantity_cap_across_returns(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    r = await _guest_return(pay_http, o, email=SHIP["email"])
    assert r.status_code == 200, r.text
    rid = r.json()["return"]["id"]
    await pay_set("customer_returns", {"id": rid}, {"status": "approved"})   # admin approved it
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered"})
    r = await _guest_return(pay_http, o, email=SHIP["email"])
    assert r.status_code == 400, r.text
    assert len(await pay_findall("customer_returns", {"order_id": o["id"]})) == 1


async def _approved_return(order, rid="ret-1", amount=300.0, partial=False):
    from routes import deps
    rec = {"id": rid, "order_id": order["id"], "order_number": order["order_number"], "status": "approved",
           "refund_amount": amount, "items": [{"product_id": "p1", "quantity": 1, "price": 500.0}],
           "created_at": now_iso()}
    if partial:
        rec["refund_breakdown"] = {"is_partial": True}
    await deps.db.customer_returns.insert_one(rec)
    await pay_set("orders", {"id": order["id"]}, {"status": "return_approved"})


async def test_refund_pay_amount_capped_at_captured_total(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    await _approved_return(o)
    _, hdr = await pay_staff(["returns.refund_pay"])
    r = await pay_http.post("/api/orders/returns/ret-1/refund-pay", json={"amount": 10000, "method": "havale"},
                            headers=hdr)
    assert r.status_code == 400
    assert (await pay_find("customer_returns", {"id": "ret-1"}))["status"] == "approved"
    r = await pay_http.post("/api/orders/returns/ret-1/refund-pay", json={"amount": -5, "method": "havale"},
                            headers=hdr)
    assert r.status_code == 400
    r = await pay_http.post("/api/orders/returns/ret-1/refund-pay", json={"amount": 300, "method": "havale"},
                            headers=hdr)
    assert r.status_code == 200 and r.json()["order_status"] == "refunded"
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "refunded"


async def test_refund_pay_counts_card_refunds_and_other_returns(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)                        # captured 550
    await pay_set("orders", {"id": o["id"]}, {"refunds": [{"net_refund": 300.0, "amount": 300.0,
                                                          "status": "done", "provider": "iyzico"}]})
    await _approved_return(o, "ret-a", amount=300)
    _, hdr = await pay_staff(["returns.refund_pay"])
    r = await pay_http.post("/api/orders/returns/ret-a/refund-pay", json={"amount": 300, "method": "havale"},
                            headers=hdr)
    assert r.status_code == 400                                               # 300 + 300 > 550
    r = await pay_http.post("/api/orders/returns/ret-a/refund-pay", json={"amount": 250, "method": "havale"},
                            headers=hdr)
    assert r.status_code == 200


async def test_partial_return_sets_partial_refunded(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    await _approved_return(o, "ret-p", amount=200, partial=True)
    _, hdr = await pay_staff(["returns.refund_pay"])
    r = await pay_http.post("/api/orders/returns/ret-p/refund-pay", json={"method": "iyzico"}, headers=hdr)
    assert r.status_code == 200 and r.json()["order_status"] == "partial_refunded"
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "partial_refunded" and cur["payment_status"] == "paid"


async def test_refund_pay_requires_permission(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    await _approved_return(o)
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.post("/api/orders/returns/ret-1/refund-pay", json={"amount": 1}, headers=hdr)
    assert r.status_code == 403


# ═════════════════════════════════════ MNG webhook ═══════════════════════════════════════════
MNG_SECRET = "mng-e2e-secret-123456"


async def _mng(http, barkod, code, at="2026-10-01T10:00:00", secret=MNG_SECRET):
    return await http.post("/api/orders/cargo/mng-webhook",
                           json={"BARKOD": barkod, "ISLEM_KODU": code, "ISLEM_ADI": "x", "TARIH": at},
                           headers={"X-Webhook-Secret": secret})


async def _cargo_order(http, env, status, barkod):
    o = await _delivered_paid_order(http, env, pid=f"p-{barkod}")
    await pay_set("orders", {"id": o["id"]}, {"status": status, "cargo_tracking_number": barkod,
                                              "delivered_at": None if status != "delivered" else now_iso(days=-1)})
    return o


async def test_mng_webhook_requires_secret(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("MNG_WEBHOOK_SECRET", MNG_SECRET)
    o = await _cargo_order(pay_http, pay_env, "shipped", "NZ0")
    assert (await _mng(pay_http, "NZ0", "400", secret="wrong")).status_code == 401
    assert (await _mng(pay_http, "NZ0", "400", secret="")).status_code == 401
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "shipped"


async def test_mng_late_event_does_not_regress_delivered(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("MNG_WEBHOOK_SECRET", MNG_SECRET)
    o = await _cargo_order(pay_http, pay_env, "delivered", "NZ1")
    for code in ("100", "200", "300"):
        r = await _mng(pay_http, "NZ1", code, at=f"2026-10-01T0{code[0]}:00:00")
        assert r.status_code == 200 and r.json()["new_status"] is None
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "delivered" and cur["payment_status"] == "paid"


async def test_mng_cancelled_order_not_set_delivered(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("MNG_WEBHOOK_SECRET", MNG_SECRET)
    o = await _cargo_order(pay_http, pay_env, "cancelled", "NZ2")
    r = await _mng(pay_http, "NZ2", "400")
    assert r.status_code == 200 and r.json()["new_status"] is None
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "cancelled" and not cur.get("delivered_at")


async def test_mng_forward_transition_and_replay(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("MNG_WEBHOOK_SECRET", MNG_SECRET)
    o = await _cargo_order(pay_http, pay_env, "shipped", "NZ3")
    r = await _mng(pay_http, "NZ3", "400")
    assert r.json()["new_status"] == "delivered"
    r = await _mng(pay_http, "NZ3", "400")
    assert r.json().get("duplicate") is True
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "delivered" and len(cur["cargo_status_history"]) == 1


async def test_mng_return_code_500_is_not_a_customer_return(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("MNG_WEBHOOK_SECRET", MNG_SECRET)
    o = await _cargo_order(pay_http, pay_env, "shipped", "NZ4")
    await _mng(pay_http, "NZ4", "500")
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "shipped" and cur["payment_status"] == "paid"


# ═════════════════════════════════════ coupons ═══════════════════════════════════════════════
def _order_with_coupon():
    return {"id": "ord-c1", "shipping_address": {"email": "Buyer@Example.test"}, "user_id": None,
            "applied_promotions": [{"coupon_id": "cpn-1", "discount": 10.0},
                                   {"coupon_id": "edu_discount", "discount": 5.0}]}


async def test_concurrent_record_order_redemptions_single_row(pay_env):
    from routes.orders import record_order_redemptions
    res = await asyncio.gather(*[record_order_redemptions(_order_with_coupon()) for _ in range(5)])
    assert all(res)
    rows = await pay_findall("coupon_redemptions")
    assert [(r["coupon_id"], r["order_id"]) for r in rows] == [("cpn-1", "ord-c1")]   # synthetic id skipped
    assert rows[0]["customer_email"] == "buyer@example.test"


def _racy_upserts(monkeypatch):
    """MongoDB semantics for an upsert whose filter fields have NO unique index: the
    'no match → insert' step is not atomic across concurrent operations (MongoDB manual,
    updateOne → "Upsert with Duplicate Values"). With a unique index the loser gets E11000."""
    from routes import deps
    from pymongo.results import UpdateResult
    cls = type(deps.db.coupon_redemptions)
    orig_update, orig_find, orig_insert = cls.update_one, cls.find_one, cls.insert_one

    async def _update_one(self, flt, upd, upsert=False, **kw):
        if upsert and getattr(self, "name", "") == "coupon_redemptions":
            if await orig_find(self, flt) is None:
                await asyncio.sleep(0)
                doc = {k: v for k, v in flt.items() if not k.startswith("$")}
                doc.update(upd.get("$setOnInsert") or {})
                doc.update(upd.get("$set") or {})
                res = await orig_insert(self, doc)
                return UpdateResult({"n": 1, "nModified": 0, "upserted": res.inserted_id}, True)
        return await orig_update(self, flt, upd, upsert=upsert, **kw)
    monkeypatch.setattr(cls, "update_one", _update_one)


async def test_concurrent_redemptions_mongodb_upsert_semantics_without_unique_index(pay_env, monkeypatch):
    from routes.orders import record_order_redemptions
    _racy_upserts(monkeypatch)
    await asyncio.gather(*[record_order_redemptions(_order_with_coupon()) for _ in range(5)])
    assert len(await pay_findall("coupon_redemptions")) == 1


async def test_concurrent_redemptions_with_proposed_unique_index(pay_env, monkeypatch):
    """Proposed fix (unique index) makes the same race safe: losers get E11000 and are ignored."""
    from routes import deps
    from routes.orders import record_order_redemptions
    await deps.db.coupon_redemptions.create_index([("coupon_id", 1), ("order_id", 1)], unique=True)
    _racy_upserts(monkeypatch)
    res = await asyncio.gather(*[record_order_redemptions(_order_with_coupon()) for _ in range(5)])
    assert all(res)
    assert len(await pay_findall("coupon_redemptions")) == 1


async def test_failed_payment_does_not_burn_coupon(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    await pay_set("orders", {"id": o["id"]}, {"applied_promotions": [{"coupon_id": "cpn-9", "discount": 1}]})
    mock_3ds_auth(pay_env.iyzico, {"status": "failure", "errorCode": "10051"})
    await pay_callback(pay_http, o["id"], "pid-f")
    await pay_drain()
    assert await pay_findall("coupon_redemptions") == []


# ═════════════════════════════════════ admin order endpoints ═════════════════════════════════
async def test_put_order_cannot_change_protected_fields(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(super_admin=True)
    before = await pay_find("orders", {"id": o["id"]})
    r = await pay_http.put(f"/api/orders/{o['id']}", headers=hdr, json={
        "payment_method": "bank_transfer", "items": [], "refunds": [{"net_refund": -999}],
        "payment_status": "paid", "status": "confirmed", "total": 1, "iyzico_payment_id": "forged",
        "duplicate_payments": [], "order_confirmed_notified": True, "paid_at": now_iso(),
        "notes": "kapıya bırakın"})
    assert r.status_code == 200
    ign = set(r.json()["ignored_fields"])
    assert {"payment_method", "items", "refunds", "payment_status", "status", "total",
            "iyzico_payment_id", "order_confirmed_notified"} <= ign
    cur = await pay_find("orders", {"id": o["id"]})
    for k in ("payment_method", "items", "payment_status", "status", "total", "paid_at",
              "iyzico_payment_id", "order_confirmed_notified"):
        assert cur.get(k) == before.get(k), k
    assert not cur.get("refunds")
    assert cur["notes"] == "kapıya bırakın"


async def test_bulk_status_requires_update_status_permission(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["orders.view", "orders.edit"])
    r = await pay_http.post("/api/orders/bulk-status?status=confirmed", json=[o["id"]], headers=hdr)
    assert r.status_code == 403
    _, chdr = await pay_customer()
    r = await pay_http.post("/api/orders/bulk-status?status=confirmed", json=[o["id"]], headers=chdr)
    assert r.status_code in (401, 403)
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "awaiting_payment"


_BUG_SITE_PLATFORM = (  # FIXED: routes/orders.py::_is_site_order (kept for documentation)
    "BUG (invariant 1/2/3): routes/orders.py::update_order_status W11874 guard only fires when "
    "(order.platform or 'site') == 'site', but create_order stores platform='meluni' — so the guard "
    "NEVER matches a site order. Staff with orders.update_status can set an UNPAID card order "
    "(awaiting_payment/failed) to 'confirmed' (single or bulk-status): it leaves the auto-cancel "
    "sweep, becomes invoiceable/shippable and the 'confirmed' customer notification is dispatched.")


async def _assert_unpaid_card_untouched(env, oid):
    cur = await pay_find("orders", {"id": oid})
    assert cur["status"] == "awaiting_payment" and cur["payment_status"] == "pending"
    await pay_drain()
    assert env.outbox.notifications == []


async def test_unpaid_card_order_cannot_be_confirmed_via_status(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.put(f"/api/orders/{o['id']}/status?status=confirmed", headers=hdr)
    assert r.status_code == 409
    await _assert_unpaid_card_untouched(pay_env, o["id"])


async def test_unpaid_card_order_cannot_be_confirmed_via_bulk_status(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.post("/api/orders/bulk-status?status=confirmed", json=[o["id"]], headers=hdr)
    assert r.status_code == 200 and r.json()["error_count"] == 1
    await _assert_unpaid_card_untouched(pay_env, o["id"])


async def test_unpaid_card_order_confirm_guard_works_for_legacy_site_platform(pay_http, pay_env):
    """The guard itself is correct when platform == 'site' (legacy docs) → proves the root cause
    is only the 'meluni' vs 'site' platform value."""
    o = await pay_card_order(pay_http)
    await pay_set("orders", {"id": o["id"]}, {"platform": "site"})
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.put(f"/api/orders/{o['id']}/status?status=confirmed", headers=hdr)
    assert r.status_code == 409
    await _assert_unpaid_card_untouched(pay_env, o["id"])


async def test_card_order_cannot_be_marked_paid_manually(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.put(f"/api/orders/{o['id']}/mark-paid", headers=hdr)
    assert r.status_code == 400
    await _assert_unpaid_card_untouched(pay_env, o["id"])


async def test_havale_admin_confirmation_is_the_documented_manual_paid_path(pay_http, pay_env):
    """Bank transfer: the ONLY way money is confirmed is a staff member with orders.update_status
    who saw it on the bank statement — via PUT /status?status=confirmed (payment_approved_via=
    status_confirm) or PUT /mark-paid. Both stamp who did it. Card orders are refused (above)."""
    await pay_seed_product("p1", stock=5)
    staff, hdr = await pay_staff(["orders.update_status"])
    r = await pay_create_order(pay_http, "bank_transfer")
    oid = r.json()["order_id"]
    r = await pay_http.put(f"/api/orders/{oid}/status?status=confirmed", headers=hdr)
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": oid})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed"
    assert cur["payment_approved_via"] == "status_confirm" and cur["paid_by"] == staff["email"]
    ev = await pay_findall("order_events", {"order_id": oid})
    assert any(e.get("event_type") == "payment" or e.get("type") == "payment" for e in ev)

    r = await pay_create_order(pay_http, "bank_transfer", ship={**SHIP, "email": "h2@example.test",
                                                                "phone": "05557770000"})
    oid2 = r.json()["order_id"]
    r = await pay_http.put(f"/api/orders/{oid2}/mark-paid", headers=hdr)
    assert r.status_code == 200
    cur = await pay_find("orders", {"id": oid2})
    assert cur["payment_status"] == "paid" and cur["status"] == "confirmed" and cur["paid_by"] == staff["email"]


async def test_havale_confirmation_requires_permission(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    _, hdr = await pay_staff(["orders.view"])
    oid = (await pay_create_order(pay_http, "bank_transfer")).json()["order_id"]
    assert (await pay_http.put(f"/api/orders/{oid}/status?status=confirmed", headers=hdr)).status_code == 403
    assert (await pay_http.put(f"/api/orders/{oid}/mark-paid", headers=hdr)).status_code == 403
    assert (await pay_find("orders", {"id": oid}))["payment_status"] == "pending"


async def test_cancel_paid_order_requires_finance_permission(pay_http, pay_env):
    o = await _delivered_paid_order(pay_http, pay_env)
    await pay_set("orders", {"id": o["id"]}, {"status": "confirmed"})
    _, hdr = await pay_staff(["orders.update_status"])
    r = await pay_http.put(f"/api/orders/{o['id']}/status?status=cancelled", headers=hdr)
    assert r.status_code == 403
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "confirmed"
