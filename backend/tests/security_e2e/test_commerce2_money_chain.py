"""E2E (commerce-2): money integrity.

SHOP03 inactive/archived product or hidden/disabled variant cannot be bought by a direct request
COM02  excessive decimals / rounding / KDV split: every amount is server-rounded to 2 decimals and
       the totals identity (lines − discounts + fees = total = iyzico price = invoice lines) holds
COM16  whole chain — coupon + gift card + stock + 3DS payment + cargo label + invoice + partial
       return/refund — with replayed and out-of-order events at every step; ledger stays explainable
"""
import asyncio
import json

import httpx
import pytest

from pay_harness import PAY_TOKENS as _PT  # denetim #4: istemcinin ödeme yetkisi
from pay_harness import (
    CALLBACK_URL, CARD, IYZICO_BASE, SHIP, iyz_auth_ok, iyz_json, mock_3ds_auth, mock_3ds_init, now_iso,
    order_payload, pay_callback, pay_customer, pay_drain, pay_find, pay_findall, pay_seed_product, pay_set,
    pay_set_rule, pay_staff, pay_stock,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_usage_indexes():
    from routes import coupons
    coupons._USAGE_IDX_DONE["ok"] = False
    coupons.invalidate_codes_cache()          # fuzzy-code resolver caches active codes for 60 s
    yield


def _cents_ok(x) -> bool:
    return abs(float(x) * 100 - round(float(x) * 100)) < 1e-6


async def _gift_card(code="HED-TEST-AAAA-0001", balance=100.0):
    from routes import deps
    await deps.db.gift_cards.insert_one({"code": code, "status": "active", "kind": "gift", "balance": balance,
                                         "initial_balance": balance, "created_at": now_iso(days=-1),
                                         "transactions": []})
    return code


async def _coupon(code="ZINCIR10", pct=10, limit=3, **extra):
    from routes import deps
    c = {"id": f"c-{code}", "code": code, "type": "percent", "value": pct, "is_active": True,
         "usage_limit": limit, "auto_apply": False, "created_at": now_iso(days=-1)}
    c.update(extra)
    await deps.db.coupons.insert_one(dict(c))
    return c


# ═════════════════════════════════════ SHOP03 ════════════════════════════════════════════════
async def test_inactive_archived_product_and_hidden_variant_cannot_be_ordered(pay_http, pay_env):
    await pay_seed_product("live", stock=5, variants=[
        {"id": "v-m", "size": "M", "stock": 3, "barcode": "BC-M"},
        {"id": "v-l", "size": "L", "stock": 3, "barcode": "BC-L", "is_active": False},
        {"id": "v-xl", "size": "XL", "stock": 3, "barcode": "BC-XL", "is_deleted": True}])
    await pay_seed_product("off", stock=5, is_active=False)
    await pay_seed_product("arch", stock=5, is_deleted=True)
    gc = await _gift_card(balance=1000.0)
    cases = [
        ([{"product_id": "off", "quantity": 1}], 409),
        ([{"product_id": "arch", "quantity": 1}], 409),
        ([{"product_id": "live", "variant_id": "v-l", "quantity": 1}], 409),
        ([{"product_id": "live", "barcode": "BC-L", "quantity": 1}], 409),
        ([{"product_id": "live", "size": "L", "quantity": 1}], 409),
        ([{"product_id": "live", "variant_id": "v-xl", "quantity": 1}], 409),
        ([{"product_id": "live", "variant_id": "v-m", "quantity": 1},
          {"product_id": "live", "variant_id": "v-l", "quantity": 1}], 409),   # one bad line poisons the cart
        ([{"product_id": "live", "variant_id": "nope", "quantity": 1}], 400),
        ([{"product_id": "other-store-product", "quantity": 1}], 400),
    ]
    for items, want in cases:
        r = await pay_http.post("/api/orders", json=order_payload("credit_card", items=items, gift_card_code=gc))
        assert r.status_code == want, (items, r.text)
    await pay_drain()
    assert await pay_findall("orders") == []
    assert await pay_stock("live") == 5
    prod = await pay_find("products", {"id": "live"})
    assert [v["stock"] for v in prod["variants"]] == [3, 3, 3]
    assert (await pay_find("gift_cards", {"code": gc}))["balance"] == 1000.0    # reservation handed back
    # control: the visible variant is purchasable, at the SERVER price
    r = await pay_http.post("/api/orders", json=order_payload("credit_card", items=[
        {"product_id": "live", "variant_id": "v-m", "quantity": 1, "price": 0.01}]))
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["items"][0]["price"] == 500.0 and o["items"][0]["barcode"] == "BC-M"


async def test_client_supplied_applied_promotions_are_ignored(pay_http, pay_env, monkeypatch):
    await pay_seed_product("p1", stock=5)
    victim = await _coupon(code="KITKUPON", limit=1)
    from routes import coupons

    async def _boom(**kw):
        raise RuntimeError("promo engine down")
    monkeypatch.setattr(coupons, "evaluate_cart_promotions", _boom)
    body = order_payload("bank_transfer", applied_promotions=[{"coupon_id": victim["id"], "code": "KITKUPON",
                                                               "discount": 999}])
    r = await pay_http.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["applied_promotions"] == [] and float(o["discount"]) == 0.0
    assert await pay_findall("coupon_redemptions") == []           # victim's last use not burned
    assert await pay_findall("coupon_usage_holds") == []


# ═════════════════════════════════════ COM02 ═════════════════════════════════════════════════
MONEY_KEYS = ("subtotal", "discount", "payment_discount", "shipping_cost", "total", "discount_total",
              "gift_wrap_price", "cod_fee")


@pytest.mark.parametrize("pm", ["credit_card", "bank_transfer"])
async def test_rounding_and_totals_identity_with_excessive_decimals(pay_http, pay_env, pm):
    await pay_seed_product("a", stock=50, price=33.3349999, vat_rate=10)
    await pay_seed_product("b", stock=50, price=19.995, sale_price=19.987654, vat_rate=20,
                           variants=[{"id": "b-s", "size": "S", "stock": 9, "barcode": "BC-BS",
                                      "price_adjustment": 0.0049}])
    await pay_seed_product("c", stock=50, price=0.015)                 # binary float → 0.01 (still > 0)
    await _coupon(code="KESIRLI", pct=12.345, limit=0)
    items = [{"product_id": "a", "quantity": 3, "price": 1.111111},
             {"product_id": "b", "variant_id": "b-s", "quantity": 7, "price": 0.0001},
             {"product_id": "c", "quantity": 1}]
    r = await pay_http.post("/api/orders", json=order_payload(pm, items=items, coupon_code="KESIRLI",
                                                              gift_wrap=True, gift_wrap_price=0.001))
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    for k in MONEY_KEYS:
        if o.get(k) is not None:
            assert _cents_ok(o[k]), (k, o[k])
    for it in o["items"]:
        assert _cents_ok(it["price"]) and _cents_ok(it["discount_amount"]), it
    for d in o["discount_breakdown"]:
        assert _cents_ok(d["amount"]), d
    assert [it["price"] for it in o["items"]] == [33.33, 19.99, round(0.015, 2)]
    sub = round(sum(it["price"] * it["quantity"] for it in o["items"]), 2)
    assert o["subtotal"] == sub
    ident = round(o["subtotal"] - o["discount"] - o["payment_discount"] + o["shipping_cost"]
                  + o["gift_wrap_price"] + o["cod_fee"], 2)
    assert o["total"] == ident
    assert round(sum(it["discount_amount"] for it in o["items"]), 2) == round(o["discount"] + o["payment_discount"], 2)
    assert o["discount"] > 0 and o["gift_wrap_price"] > 0                # coupon + server gift-wrap fee applied
    if pm == "bank_transfer":
        assert o["payment_discount"] == round((o["subtotal"] - o["discount"]) * 0.05, 2)

    # iyzico basket: Σ items == price == paidPrice == order.total, every amount 2-decimal
    from routes.payment import _build_initialize_payload
    body = _build_initialize_payload(o, CALLBACK_URL)
    prices = [float(b["price"]) for b in body["basketItems"]]
    assert all(len(b["price"].split(".")[-1]) <= 2 for b in body["basketItems"] if "." in b["price"])
    assert round(sum(prices), 2) == float(body["price"]) == float(body["paidPrice"]) == o["total"]

    # KDV split: invoice lines (+ shipping + service lines) reproduce the collected total exactly,
    # and net + KDV per rate adds up to the gross with 2-decimal amounts
    from routes.orders import _apply_invoice_discounts
    lines = [{"name": it["name"], "qty": it["quantity"], "unit_price": it["price"],
              "kdv_rate": (10.0 if it["product_id"] == "a" else 20.0)} for it in o["items"]]
    res = _apply_invoice_discounts(o, lines)
    gross_lines = [round(li["unit_price"] * li["qty"], 2) for li in lines]
    extra = round(sum(x["unit_price"] * x["qty"] for x in res["extra_lines"]), 2)
    assert abs(round(sum(gross_lines) + o["shipping_cost"] + extra, 2) - o["total"]) <= 0.01 * len(lines)
    by_rate = {}
    for li, g in zip(lines + res["extra_lines"], gross_lines + [x["unit_price"] for x in res["extra_lines"]]):
        r_ = li["kdv_rate"]
        net = round(g / (1 + r_ / 100.0), 2)
        kdv = round(g - net, 2)
        assert _cents_ok(net) and _cents_ok(kdv) and round(net + kdv, 2) == round(g, 2)
        by_rate[r_] = round(by_rate.get(r_, 0) + g, 2)
    assert round(sum(by_rate.values()), 2) == round(sum(gross_lines) + extra, 2)


async def test_client_total_with_excessive_decimals_is_compared_at_cent_precision(pay_http, pay_env):
    await pay_seed_product("a", stock=5, price=33.3349999)
    r = await pay_http.post("/api/orders", json=order_payload("credit_card", items=[{"product_id": "a",
                                                                                   "quantity": 3}],
                                                              total=150.0000000001))
    assert r.status_code == 200, r.text                                 # 99.99 + 50 shipping = 149.99 ≈ ok
    for bad in (149.95, 1e308, -149.99):
        r = await pay_http.post("/api/orders", json=order_payload("credit_card", items=[{"product_id": "a",
                                                                                       "quantity": 3}],
                                                                  total=bad))
        assert r.status_code in (400, 409), (bad, r.text)
    assert len(await pay_findall("orders")) == 1


# ═════════════════════════════════════ COM16 ═════════════════════════════════════════════════
class _Mng:
    def __init__(self):
        self.creates, self.fail_next = [], 0

    def create(self, **kw):
        self.creates.append(kw)
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("MNG timeout after the shipment was stored")
        return {"ok": True, "barkod": "MNG-CHAIN-1", "gonderi_no": ""}

    def status(self, **kw):
        return ({"ok": True, "mng_siparis_no": "MNG-CHAIN-1", "gonderi_no": "NZ-CHAIN", "kargo_statu": "1"}
                if self.creates else {"ok": False, "error": "yok"})

    def nz(self, **kw):
        return {"ok": False, "hata": "yok"}


async def test_full_order_chain_with_replays_and_out_of_order_events(pay_http, pay_env, monkeypatch):
    import mng_kargo_client
    mng = _Mng()
    monkeypatch.setattr(mng_kargo_client, "create_shipment", mng.create)
    monkeypatch.setattr(mng_kargo_client, "get_mng_shipment_status", mng.status)
    monkeypatch.setattr(mng_kargo_client, "get_mng_barcode_immediately", mng.nz)
    from routes import deps
    await deps.db.settings.insert_one({"id": "mng_kargo", "username": "u", "password": "p", "is_active": True})
    await pay_seed_product("p1", stock=5)
    await pay_seed_product("p2", stock=5, price=300.0)
    coupon = await _coupon(limit=3)
    gc = await _gift_card(balance=100.0)
    member, mhdr = await pay_customer(email=SHIP["email"])
    _, sup = await pay_staff(super_admin=True)

    # ── 1. quote + coupon reservation + gift-card reservation + stock allocation (idempotent replay) ──
    body = order_payload("credit_card", items=[{"product_id": "p1", "quantity": 1}, {"product_id": "p2", "quantity": 1}],
                         coupon_code=coupon["code"], gift_card_code=gc, idempotency_key="idem-chain-1")
    r1, r2 = await asyncio.gather(pay_http.post("/api/orders", json=body, headers=mhdr),
                                  pay_http.post("/api/orders", json=body, headers=mhdr))
    await pay_drain()
    # double submit: one order; the twin either gets the same order back (idempotent) or is refused
    # because the gift card is already reserved by its twin — never a second order / reservation
    oks = [x for x in (r1, r2) if x.status_code == 200]
    assert oks and all(x.status_code in (200, 400, 409) for x in (r1, r2)), (r1.text, r2.text)
    assert len({x.json()["order_id"] for x in oks}) == 1
    r1 = oks[0]
    r3 = await pay_http.post("/api/orders", json=body, headers=mhdr)       # late replay
    assert r3.json()["order_id"] == r1.json()["order_id"] and r3.json().get("idempotent")
    _PT[r3.json()["order_id"]] = r3.json()["pay_token"]   # istemci en son aldığı ödeme yetkisini kullanır
    o = await pay_find("orders", {"id": r1.json()["order_id"]})
    assert len(await pay_findall("orders")) == 1
    assert (o["subtotal"], o["discount"], o["shipping_cost"], o["gift_card"]["amount"], o["total"]) == \
        (800.0, 80.0, 50.0, 100.0, 670.0)
    assert o["status"] == "awaiting_payment" and o["payment_status"] == "pending"
    assert (await pay_stock("p1"), await pay_stock("p2")) == (4, 4)
    assert (await pay_find("gift_cards", {"code": gc}))["balance"] == 0.0
    cnt = await pay_find("coupon_usage_counters", {"coupon_id": coupon["id"]})
    assert (cnt["reserved"], cnt["used"]) == (1, 0)
    assert await pay_findall("coupon_redemptions") == [] and pay_env.outbox.events("order_confirmed") == []

    # ── 2. 3DS: init, then OUT-OF-ORDER unverified failure, then verified success, then replays ──
    mock_3ds_init(pay_env.iyzico)
    r = await pay_http.post("/api/payment/3ds/initialize", json={"order_id": o["id"], "callback_url": CALLBACK_URL,
                                                                 "return_url": "https://www.meluni.com.tr/odeme",
                                                                 "card": CARD, "pay_token": _PT.get(o["id"], "")})
    assert r.status_code == 200 and r.json()["success"]
    await pay_callback(pay_http, o["id"], "pid-chain", status="failure", md="0")
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "awaiting_payment" and cur["payment_status"] == "pending"
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, payment_id="pid-chain"))
    for _ in range(3):
        await pay_callback(pay_http, o["id"], "pid-chain")
    await pay_drain()
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "confirmed" and cur["payment_status"] == "paid"
    assert len(pay_env.outbox.events("order_confirmed")) == 1 and len(pay_env.outbox.new_order_pushes(o["id"])) == 1
    assert len(await pay_findall("coupon_redemptions")) == 1
    cnt = await pay_find("coupon_usage_counters", {"coupon_id": coupon["id"]})
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (0, 1, 1)
    assert (await pay_stock("p1"), await pay_stock("p2")) == (4, 4)
    # a stale/late failure after success changes nothing
    await pay_callback(pay_http, o["id"], "pid-chain", status="failure", md="0")
    assert (await pay_find("orders", {"id": o["id"]}))["payment_status"] == "paid"

    # ── 3. cargo label: crash after MNG stored it, retry relinks; concurrent replays → one shipment ──
    mng.fail_next = 1
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 502
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur.get("cargo_label_attempted_at") and not cur.get("cargo_barcode_number")
    rs = await asyncio.gather(*[pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
                                for _ in range(3)])
    assert any(x.status_code == 200 for x in rs) and all(x.status_code in (200, 409) for x in rs)
    assert len(mng.creates) == 1                                        # never a second MNG record
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["cargo_barcode_number"] == "MNG-CHAIN-1" and cur["status"] == "preparing"

    # ── 4. invoice (manual/mock) — replay keeps one number ──
    for _ in range(2):
        assert (await pay_http.post(f"/api/orders/{o['id']}/mark-invoiced", json={"invoice_number": "FCT2026000001"},
                                    headers=sup)).status_code == 200
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_number"] == "FCT2026000001" and cur["payment_status"] == "paid"

    # ── 5. delivery, partial return of p2, concurrent duplicate requests, refund with replay ──
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered", "delivered_at": now_iso(days=-1)})
    rs = await asyncio.gather(*[pay_http.post(f"/api/orders/{o['id']}/return-request",
                                              json={"items": [1], "reason": "Beden"}, headers=mhdr)
                                for _ in range(3)])
    assert all(x.status_code in (200, 409) for x in rs)
    rets = await pay_findall("customer_returns", {"order_id": o["id"]})
    assert len(rets) == 1 and [i["product_id"] for i in rets[0]["items"]] == ["p2"]
    rid = rets[0]["id"]
    r = await pay_http.post(f"/api/orders/returns/{rid}/approve", json={"fault": "store"}, headers=sup)
    assert r.status_code == 200, r.text
    rec = await pay_find("customer_returns", {"id": rid})
    amount = float(rec["refund_amount"])
    assert 0 < amount < cur["total"]
    route = pay_env.iyzico.post(f"{IYZICO_BASE}/payment/refund").mock(
        return_value=iyz_json({"status": "success", "paymentId": "pid-chain"}))
    r = await pay_http.post("/api/integrations/iyzico/refund", json={"order_id": o["id"], "amount": amount},
                            headers=sup)
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    # anything beyond the captured amount is refused (cap = what iyzico actually captured)
    r = await pay_http.post("/api/integrations/iyzico/refund",
                            json={"order_id": o["id"], "amount": round(cur["total"] - amount + 0.05, 2)}, headers=sup)
    assert r.status_code == 400 and route.call_count == 1
    cur = await pay_find("orders", {"id": o["id"]})
    refunded = round(sum(float(x["net_refund"]) for x in cur["refunds"] if x["status"] == "done"), 2)
    assert refunded == round(amount, 2) <= cur["total"]
    r = await pay_http.post(f"/api/orders/returns/{rid}/refund-pay", json={"method": "iyzico"}, headers=sup)
    assert r.status_code == 200 and r.json()["order_status"] in ("partial_refunded", "refunded")
    r = await pay_http.post(f"/api/orders/returns/{rid}/refund-pay", json={"method": "iyzico"}, headers=sup)
    assert r.json().get("already") is True                                # replay is a no-op

    # ── 6. ledger: money, goods, coupon, gift card all explainable ──
    final = await pay_find("orders", {"id": o["id"]})
    assert final["payment_status"] == "paid"                              # refunds never rewrite payment status
    assert final["total"] == 670.0
    assert len(await pay_findall("coupon_redemptions")) == 1
    assert (await pay_find("coupon_usage_counters", {"coupon_id": coupon["id"]}))["total"] == 1
    card = await pay_find("gift_cards", {"code": gc})
    assert card["balance"] == 0.0 and [t["type"] for t in card["transactions"]] == ["redeem"]
    moves = await pay_findall("stock_movements", {"order_id": o["id"]})
    created = [m for m in moves if m["type"] == "order_created"]
    assert len(created) == 1 and sum(-i["delta"] for i in created[0]["items"]) == 2
    restocked = sum(int(i.get("delta") or 0) for m in moves if m["type"] == "return_restock" for i in m["items"])
    assert (await pay_stock("p1"), await pay_stock("p2")) == (4, 4 + restocked) and restocked in (0, 1)
    outbound = [c for c in mng.creates if c["siparis_no"] == o["order_number"]]
    returns = [c for c in mng.creates if str(c["siparis_no"]).startswith("IW")]
    assert len(outbound) == 1 and len(returns) == 1 and len(mng.creates) == 2
    assert len(pay_env.outbox.events("order_confirmed")) == 1


async def test_cancel_chain_returns_every_reservation_exactly_once(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    coupon = await _coupon(limit=1)
    gc = await _gift_card(balance=60.0)
    _, fin = await pay_staff(["orders.update_status", "returns.expense_note"])
    r = await pay_http.post("/api/orders", json=order_payload("credit_card", coupon_code=coupon["code"],
                                                              gift_card_code=gc))
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    assert await pay_stock("p1") == 2 and (await pay_find("gift_cards", {"code": gc}))["balance"] == 0.0
    rs = await asyncio.gather(*[pay_http.put(f"/api/orders/{oid}/status", params={"status": "cancelled"},
                                             headers=fin) for _ in range(3)])
    assert sorted(x.status_code for x in rs).count(200) >= 1
    for _ in range(2):                                                    # sweeper replays too
        import scheduler
        await pay_set("orders", {"id": oid}, {"created_at": now_iso(hours=-5)})
        await scheduler.auto_cancel_unpaid_card_orders()
    assert await pay_stock("p1") == 3
    card = await pay_find("gift_cards", {"code": gc})
    assert card["balance"] == 60.0 and [t["type"] for t in card["transactions"]] == ["redeem", "refund"]
    cnt = await pay_find("coupon_usage_counters", {"coupon_id": coupon["id"]})
    assert (cnt["reserved"], cnt["used"], cnt["total"]) == (0, 0, 0)
    assert (await pay_find("orders", {"id": oid}))["payment_status"] != "paid"
