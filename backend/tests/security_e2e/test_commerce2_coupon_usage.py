"""E2E (commerce-2) COM03: coupon usage_limit is enforced ATOMICALLY at order creation via a per-coupon
reservation counter, while the redemption record keeps its timing (CLAUDE.md invariant 5: a card
order's coupon is only redeemed after the verified payment). Concurrency is real interleaving at
DB-operation granularity (pay_harness yields once per collection call)."""
import asyncio

import pytest

from pay_harness import (
    iyz_auth_ok, mock_3ds_auth, now_iso, order_payload, pay_callback, pay_customer, pay_drain,
    pay_find, pay_findall, pay_seed_product, pay_set, pay_staff, pay_stock,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_usage_indexes():
    from routes import coupons
    coupons._USAGE_IDX_DONE["ok"] = False
    coupons.invalidate_codes_cache()          # fuzzy-code resolver caches active codes for 60 s   # pay_env drops collections → recreate unique indexes
    yield


async def _coupon(code="SON1", limit=1, **extra):
    from routes import deps
    c = {"id": f"c-{code}", "code": code, "type": "percent", "value": 10, "is_active": True,
         "usage_limit": limit, "auto_apply": False, "created_at": now_iso(days=-1)}
    c.update(extra)
    await deps.db.coupons.insert_one(dict(c))
    return c


def _ship(i):
    return {"first_name": "Ayse", "last_name": f"K{i}", "email": f"buyer{i}@example.test",
            "phone": f"0555{i:07d}", "city": "Istanbul", "district": "Kadikoy",
            "address": f"Test Mah. {i}", "postal_code": "34000"}


async def _order(http, i, code="SON1", pm="credit_card", hdr=None):
    body = order_payload(pm, ship=_ship(i), coupon_code=code)
    r = await http.post("/api/orders", json=body, headers=hdr or {})
    return r


async def _assert_coupon_refused(r):
    """Sequential attempt after the limit is reached: the engine already sees the coupon as used up,
    so the order is created WITHOUT the discount (a client that sends its approved total gets 409
    from the total-match check). Either way no use is reserved/consumed."""
    if r.status_code == 409:
        return
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert float(o.get("discount") or 0) == 0.0 and not o.get("coupon_reservations")
    assert not any(a.get("coupon_id") for a in (o.get("applied_promotions") or []))


async def _counter(cid="c-SON1"):
    return await pay_find("coupon_usage_counters", {"coupon_id": cid}) or {}


async def _holds(cid="c-SON1"):
    return {h["order_id"]: h["state"] for h in await pay_findall("coupon_usage_holds", {"coupon_id": cid})}


async def test_last_use_is_granted_once_under_concurrent_checkouts(pay_http, pay_env):
    await pay_seed_product("p1", stock=20)
    c = await _coupon(limit=1)
    rs = await asyncio.gather(*[_order(pay_http, i) for i in range(5)])
    await pay_drain()
    codes = sorted(r.status_code for r in rs)
    assert codes == [200, 409, 409, 409, 409], [r.text for r in rs]
    assert all("kullanım limiti" in r.json()["detail"] for r in rs if r.status_code == 409)
    orders = await pay_findall("orders")
    assert len(orders) == 1
    o = orders[0]
    assert o["coupon_reservations"] == [c["id"]] and float(o["discount"]) == 50.0
    assert o["status"] == "awaiting_payment" and o["payment_status"] != "paid"
    assert await pay_stock("p1") == 19                 # rejected checkouts took no stock
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (1, 0, 1)
    assert await _holds() == {o["id"]: "reserved"}
    # invariant 5: nothing redeemed before the payment is verified
    assert await pay_findall("coupon_redemptions") == []


async def test_reservation_released_on_cancel_and_on_unpaid_auto_cancel(pay_http, pay_env):
    import scheduler
    await pay_seed_product("p1", stock=20)
    await _coupon(limit=1)
    r1 = await _order(pay_http, 1)
    assert r1.status_code == 200, r1.text
    await _assert_coupon_refused(await _order(pay_http, 2))
    # staff cancels the first order → the use is handed back
    _, shdr = await pay_staff(["orders.update_status"])
    r = await pay_http.put(f"/api/orders/{r1.json()['order_id']}/status", params={"status": "cancelled"},
                           headers=shdr)
    assert r.status_code == 200, r.text
    assert (await _counter())["total"] == 0
    assert (await _holds())[r1.json()["order_id"]] == "released"
    r2 = await _order(pay_http, 3)
    assert r2.status_code == 200, r2.text
    # unpaid card order is swept by the 3-hour auto-cancel → reservation released (invariant 4)
    await pay_set("orders", {"id": r2.json()["order_id"]}, {"created_at": now_iso(hours=-4)})
    await scheduler.auto_cancel_unpaid_card_orders()
    await scheduler.auto_cancel_unpaid_card_orders()              # idempotent
    cur = await pay_find("orders", {"id": r2.json()["order_id"]})
    assert cur["status"] == "payment_failed" and cur["payment_status"] == "expired"
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (0, 0, 0)
    r3 = await _order(pay_http, 4)
    assert r3.status_code == 200, r3.text
    assert await pay_findall("coupon_redemptions") == []


async def test_paid_order_converts_reservation_to_used_and_failed_payment_does_not_burn(pay_http, pay_env):
    await pay_seed_product("p1", stock=20)
    await _coupon(limit=2)
    ra = await _order(pay_http, 1)
    rb = await _order(pay_http, 2)
    assert ra.status_code == rb.status_code == 200
    await _assert_coupon_refused(await _order(pay_http, 3))         # 2 of 2 reserved
    oa = await pay_find("orders", {"id": ra.json()["order_id"]})
    ob = await pay_find("orders", {"id": rb.json()["order_id"]})
    # A pays (verified 3DS auth) → redemption written at payment time, hold reserved → used
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(oa, payment_id="pid-a"))
    await pay_callback(pay_http, oa["id"], "pid-a")
    await pay_callback(pay_http, oa["id"], "pid-a")                 # replayed callback
    await pay_drain()
    assert (await pay_find("orders", {"id": oa["id"]}))["payment_status"] == "paid"
    assert [r["order_id"] for r in await pay_findall("coupon_redemptions")] == [oa["id"]]
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (1, 1, 2)
    # B's payment fails (unverified failure callback) → nothing redeemed, B still holds its use
    pay_env.iyzico.routes.clear()
    await pay_callback(pay_http, ob["id"], "pid-b", status="failure", md="0")
    await pay_drain()
    assert len(await pay_findall("coupon_redemptions")) == 1
    assert (await _holds())[ob["id"]] == "reserved"
    # paid order later cancelled by finance → use released as well
    _, fhdr = await pay_staff(["orders.update_status", "returns.expense_note"])
    r = await pay_http.put(f"/api/orders/{oa['id']}/status", params={"status": "cancelled"}, headers=fhdr)
    assert r.status_code == 200, r.text
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (1, 0, 1)
    assert await pay_findall("coupon_redemptions") == []


async def test_counter_backfills_from_existing_redemptions_and_inflight_orders(pay_http, pay_env):
    from routes import deps
    await pay_seed_product("p1", stock=20)
    c = await _coupon(limit=3)
    # legacy state (before the counter existed): one paid redemption + one unpaid in-flight order
    await deps.db.coupon_redemptions.insert_one({"coupon_id": c["id"], "order_id": "legacy-paid"})
    await deps.db.orders.insert_one({"id": "legacy-inflight", "order_number": "L1", "status": "awaiting_payment",
                                     "payment_status": "pending", "created_at": now_iso(hours=-1),
                                     "applied_promotions": [{"coupon_id": c["id"], "code": c["code"]}]})
    await deps.db.orders.insert_one({"id": "legacy-cancelled", "order_number": "L2", "status": "cancelled",
                                     "payment_status": "pending", "created_at": now_iso(hours=-1),
                                     "applied_promotions": [{"coupon_id": c["id"], "code": c["code"]}]})
    r = await _order(pay_http, 1)
    assert r.status_code == 200, r.text                  # 2 legacy uses + 1 = 3 (limit)
    await _assert_coupon_refused(await _order(pay_http, 2))
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (2, 1, 3) and cnt["backfilled"] is True
    assert await _holds() == {"legacy-paid": "used", "legacy-inflight": "reserved",
                              r.json()["order_id"]: "reserved"}


async def test_failed_creation_after_reservation_hands_the_use_back(pay_http, pay_env):
    await pay_seed_product("p1", stock=1)
    await _coupon(limit=5)
    r = await _order(pay_http, 1)
    assert r.status_code == 200
    r = await _order(pay_http, 2)                        # out of stock → 409 AFTER the reservation
    assert r.status_code == 409 and "stok" in r.json()["detail"]
    cnt = await _counter()
    assert (cnt["reserved"], cnt["total"]) == (1, 1)
    assert sorted((await _holds()).values()) == ["released", "reserved"]


async def test_auto_apply_campaign_and_unlimited_coupon_need_no_reservation(pay_http, pay_env):
    await pay_seed_product("p1", stock=20)
    await _coupon(code="OTO", limit=1, auto_apply=True)
    await _coupon(code="SINIRSIZ", limit=0)
    for i in range(2):
        assert (await _order(pay_http, i, code="SINIRSIZ")).status_code == 200
    assert await pay_findall("coupon_usage_counters") == []
    assert all(not o.get("coupon_reservations") for o in await pay_findall("orders"))


async def test_havale_order_records_redemption_immediately_and_counts_as_used(pay_http, pay_env):
    await pay_seed_product("p1", stock=20)
    await _coupon(limit=1)
    r = await _order(pay_http, 1, pm="bank_transfer")
    assert r.status_code == 200, r.text
    cnt = await _counter()
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (0, 1, 1)
    assert len(await pay_findall("coupon_redemptions")) == 1
    await _assert_coupon_refused(await _order(pay_http, 2, pm="bank_transfer"))
    assert (await _counter())["total"] == 1
