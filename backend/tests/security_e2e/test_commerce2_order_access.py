"""E2E (commerce-2): cross-customer order access (T01 / PAY-01) with policy-denial audit, address
IDs + order address snapshot (SHOP06), shared-cart links (SHOP04), search/autocomplete leakage
(SHOP01), review mass-assignment (SHOP05) and same-site redirect targets (SHOP09).

Real FastAPI app over ASGI; MongoDB = mongomock; every outbound service mocked (pay_harness)."""
import time

import pytest

from kv_harness import kv_mongomock_convert  # noqa: F401  (fixture)
from pay_harness import PAY_TOKENS as _PT  # denetim #4: istemcinin ödeme yetkisi
from pay_harness import (
    SHIP, iyz_auth_ok, mock_3ds_auth, now_iso, pay_callback, pay_create_order, pay_customer,
    pay_drain, pay_find, pay_findall, pay_seed_product, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_denial_limiter():
    from routes import orders
    orders._POLICY_DENIAL_SEEN.clear()
    orders._POLICY_DENIAL_GLOBAL.update({"window": 0, "n": 0})
    yield
    orders._POLICY_DENIAL_SEEN.clear()


async def _member_order(http, hdr, pid="p1", **kw):
    if not await pay_find("products", {"id": pid}):
        await pay_seed_product(pid, stock=10)
    r = await pay_create_order(http, headers=hdr, items=[{"product_id": pid, "quantity": 1, "price": 1}], **kw)
    assert r.status_code == 200, r.text
    return await pay_find("orders", {"id": r.json()["order_id"]})


async def _denials(user_id=None):
    q = {"event": "policy_denial"}
    if user_id:
        q["user_id"] = user_id
    return await pay_findall("auth_audit_logs", q)


def _strip_volatile(o):
    return {k: v for k, v in (o or {}).items() if k not in ("updated_at",)}


# ═════════════════════════════════════ T01 / PAY-01 ══════════════════════════════════════════
async def test_customer_cannot_touch_another_customers_order_via_any_endpoint(pay_http, pay_env):
    a, ahdr = await pay_customer(email="alice@example.test")
    b, bhdr = await pay_customer(email="bob@example.test")
    o = await _member_order(pay_http, ahdr)
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered", "payment_status": "paid",
                                              "delivered_at": now_iso(days=-1), "invoice_number": "INV-A-1",
                                              "admin_notes": [{"text": "RISK-NOTU-A"}]})
    before = _strip_volatile(await pay_find("orders", {"id": o["id"]}))
    oid, ono = o["id"], o["order_number"]

    # control: the owner can read it
    r = await pay_http.get(f"/api/orders/{oid}", headers=ahdr)
    assert r.status_code == 200 and r.json()["id"] == oid and "admin_notes" not in r.json()

    checks = [
        ("GET", f"/api/orders/{oid}", None, 403),
        ("PUT", f"/api/orders/{oid}", {"shipping_address": {**SHIP, "address": "Hacker Sk."}, "notes": "x"}, (401, 403)),
        ("PUT", f"/api/orders/{oid}/status?status=cancelled", None, (401, 403)),
        ("POST", f"/api/my-orders/{oid}/cancel", {}, 404),
        ("POST", f"/api/my-orders/{ono}/cancel", {}, 404),
        ("POST", f"/api/orders/{oid}/return-request", {"items": [0], "reason": "x"}, 403),
        ("POST", f"/api/orders/{ono}/return-request", {"items": [0], "reason": "x"}, 403),
        ("POST", f"/api/orders/{oid}/return-request/cancel", {}, 403),
        ("GET", f"/api/orders/{oid}/return", None, 403),
        ("GET", f"/api/orders/{oid}/payment-receipt", None, (401, 403)),
        ("GET", f"/api/orders/{oid}/invoice/print", None, (401, 403)),
        ("GET", f"/api/orders/{oid}/manual-invoice-file", None, (401, 403)),
        ("GET", f"/api/orders/{oid}/events", None, (401, 403)),
        ("POST", f"/api/orders/{oid}/note", {"note": "pwn"}, (401, 403)),
        ("POST", f"/api/orders/{oid}/admin-return", {}, (401, 403)),
        ("GET", "/api/orders", None, (401, 403)),
    ]
    for method, url, body, want in checks:
        r = await pay_http.request(method, url, json=body, headers=bhdr)
        ok = r.status_code in (want if isinstance(want, tuple) else (want,))
        assert ok, (method, url, r.status_code, r.text)
        assert "RISK-NOTU-A" not in r.text and "alice@example.test" not in r.text, (method, url)

    # B's own order list never contains A's order
    r = await pay_http.get("/api/my-orders", headers=bhdr)
    assert r.status_code == 200 and all(x["id"] != oid for x in r.json()["orders"])
    # anonymous detail is refused outright
    assert (await pay_http.get(f"/api/orders/{oid}")).status_code == 401

    # no side effect on A's order, no return record
    assert _strip_volatile(await pay_find("orders", {"id": oid})) == before
    assert await pay_findall("customer_returns") == []

    # PAY-01: policy-denial audit rows exist for the denied customer actions (with B's identity)
    rows = await _denials(b["id"])
    actions = {r["meta"]["action"] for r in rows}
    assert {"order.read", "order.cancel", "order.return_request", "order.return_cancel",
            "order.return_info"} <= actions, actions
    assert all(r["success"] is False and r["meta"]["order_id"] == oid for r in rows)
    assert not await _denials(a["id"])


async def test_public_by_number_view_is_masked_and_carries_no_owner_data(pay_http, pay_env):
    a, ahdr = await pay_customer(email="alice@example.test")
    o = await _member_order(pay_http, ahdr)
    # Denetim #27: kanıtsız istek yalnız durum özeti alır (içerik/adres/tutar yok).
    r0 = await pay_http.get(f"/api/orders/by-number/{o['order_number']}")
    assert r0.status_code == 200 and r0.json()["verification_required"] is True
    assert "shipping_address" not in r0.json() and "items" not in r0.json() and "total" not in r0.json()
    # Kanıtlı (sahibin oturumu) görünüm de maskeli kalır ve sahip verisi taşımaz.
    r = await pay_http.get(f"/api/orders/by-number/{o['order_number']}", headers=ahdr)
    assert r.status_code == 200
    j = r.json()
    assert "user_id" not in j and "customer_ip" not in j and "payment_id" not in j and "pay_cap_hashes" not in j
    assert j["shipping_address"]["email"] != SHIP["email"] and "***" in j["shipping_address"]["email"]
    assert SHIP["phone"] not in r.text and a["id"] not in r.text
    assert j["shipping_address"]["first_name"] == "A***" and len(j["shipping_address"]["address"]) <= 43
    assert "billing_address" not in j and "billing_info" not in j and "admin_notes" not in j


async def test_forged_invoice_links_are_denied_and_audited(pay_http, pay_env):
    _, ahdr = await pay_customer(email="alice@example.test")
    mine = await _member_order(pay_http, ahdr)
    other = await _member_order(pay_http, ahdr, pid="p2")
    from routes import orders as orders_mod
    exp = int(time.time()) + 3600
    sig_mine = orders_mod._einvoice_pdf_sig_v2(mine["id"], exp)
    msig_mine = orders_mod._manual_invoice_sig(mine["id"], exp)
    # a valid signature for MY order cannot be replayed on another order id
    r = await pay_http.get(f"/api/orders/{other['id']}/einvoice-pdf", params={"exp": exp, "sig": sig_mine})
    assert r.status_code == 403
    r = await pay_http.get(f"/api/orders/{other['id']}/manual-invoice-file/signed",
                           params={"exp": exp, "sig": msig_mine})
    assert r.status_code == 403
    r = await pay_http.get(f"/api/orders/{other['id']}/einvoice-pdf", params={"sig": "0" * 32})
    assert r.status_code == 403
    rows = await _denials()
    acts = {x["meta"]["action"] for x in rows}
    assert {"order.einvoice_link", "order.manual_invoice_link"} <= acts
    assert all(x["meta"]["reason"] == "bad_signature" for x in rows)


async def test_policy_denial_logging_is_rate_limited(pay_http, pay_env):
    _, ahdr = await pay_customer(email="alice@example.test")
    b, bhdr = await pay_customer(email="bob@example.test")
    orders = [await _member_order(pay_http, ahdr) for _ in range(3)]
    for _ in range(4):
        for o in orders:
            assert (await pay_http.get(f"/api/orders/{o['id']}", headers=bhdr)).status_code == 403
    # 12 denied reads by the same customer → ONE audit row per (user, action) per minute
    assert len(await _denials(b["id"])) == 1
    # global cap: the per-process budget bounds the write volume under a distributed scan
    from routes import orders as orders_mod
    orders_mod._POLICY_DENIAL_GLOBAL.update({"window": int(time.time() // 60),
                                             "n": orders_mod._POLICY_DENIAL_GLOBAL_MAX})
    c, chdr = await pay_customer(email="carol@example.test")
    assert (await pay_http.get(f"/api/orders/{orders[0]['id']}", headers=chdr)).status_code == 403
    assert await _denials(c["id"]) == []


# ═════════════════════════════════════ SHOP06 ════════════════════════════════════════════════
async def test_address_ids_are_owner_scoped_and_order_snapshot_is_immutable(pay_http, pay_env):
    a, ahdr = await pay_customer(email="alice@example.test")
    b, bhdr = await pay_customer(email="bob@example.test")
    o = await _member_order(pay_http, ahdr)
    snap = (await pay_find("orders", {"id": o["id"]}))["shipping_address"]
    addr = (await pay_http.get("/api/my-addresses", headers=ahdr)).json()["addresses"]
    assert len(addr) == 1 and addr[0]["source"] == "order"      # order address copied to A's book
    aid = addr[0]["id"]

    # B cannot update / delete A's address by id, nor plant one into A's book
    r = await pay_http.put(f"/api/addresses/{aid}", json={"address": "B'nin adresi", "user_id": b["id"]},
                           headers=bhdr)
    assert r.status_code == 404
    assert (await pay_http.delete(f"/api/addresses/{aid}", headers=bhdr)).status_code == 404
    r = await pay_http.post("/api/addresses", json={"title": "x", "address": "Sahte", "city": "Ankara",
                                                    "user_id": a["id"], "id": aid}, headers=bhdr)
    assert r.status_code == 200 and r.json()["address_id"] != aid
    assert (await pay_find("addresses", {"id": r.json()["address_id"]}))["user_id"] == b["id"]
    assert len((await pay_http.get("/api/my-addresses", headers=ahdr)).json()["addresses"]) == 1
    cur = await pay_find("addresses", {"id": aid})
    # KVKK: adres defteri at-rest şifreli (security.contact_pii) → gerçek değer çözülerek karşılaştırılır
    from security.contact_pii import open_field as _open
    assert _open(cur, "address") == SHIP["address"] and cur["user_id"] == a["id"]

    # the owner edits the address-book entry → the ORDER's shipping snapshot does not change
    r = await pay_http.put(f"/api/addresses/{aid}", json={"address": "Yeni Mah. Yeni Sk. 9", "city": "Izmir",
                                                          "user_id": b["id"]}, headers=ahdr)
    assert r.status_code == 200
    cur = await pay_find("addresses", {"id": aid})
    assert _open(cur, "address") == "Yeni Mah. Yeni Sk. 9" and cur["user_id"] == a["id"]
    assert "address" not in cur and "Yeni Mah." not in str(cur)          # düz değer saklanmaz
    assert (await pay_find("orders", {"id": o["id"]}))["shipping_address"] == snap
    assert (await pay_http.delete(f"/api/addresses/{aid}", headers=ahdr)).status_code == 200
    assert (await pay_find("orders", {"id": o["id"]}))["shipping_address"] == snap


# ═════════════════════════════════════ SHOP04 ════════════════════════════════════════════════
async def test_shared_cart_link_is_not_an_account_or_order_credential(pay_http, pay_env):
    a, ahdr = await pay_customer(email="alice@example.test")
    o = await _member_order(pay_http, ahdr)
    await pay_seed_product("p2", stock=4)
    r = await pay_http.post("/api/shared-carts", json={"items": [{"product_id": "p1", "quantity": 2},
                                                               {"product_id": "p2", "quantity": 999}]},
                            headers=ahdr)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    rec = await pay_find("shared_carts", {"id": sid})
    assert set(rec.keys()) <= {"id", "items", "created_at", "expires_at"}          # no owner/PII stored
    assert [i["quantity"] for i in rec["items"]] == [2, 20]                          # qty capped
    r = await pay_http.get(f"/api/shared-carts/{sid}")
    assert r.status_code == 200
    assert a["id"] not in r.text and "alice@example.test" not in r.text and o["order_number"] not in r.text

    # the share id does not authenticate anything
    for hdr in ({"Authorization": f"Bearer {sid}"}, {"Authorization": sid}):
        assert (await pay_http.get("/api/my-orders", headers=hdr)).status_code == 401
        assert (await pay_http.get(f"/api/orders/{o['id']}", headers=hdr)).status_code == 401
        assert (await pay_http.get("/api/my-addresses", headers=hdr)).status_code == 401
        assert (await pay_http.get("/api/favorites", headers=hdr)).status_code == 401
    for url in (f"/api/orders/{sid}", f"/api/orders/by-number/{sid}", f"/api/track/{sid}"):
        assert (await pay_http.get(url)).status_code in (401, 404)
    assert (await pay_http.post(f"/api/orders/by-number/{sid}/return-request",
                                json={"email": SHIP["email"], "reason": "x"})).status_code == 404
    # malformed / oversized ids never reach the DB lookup
    for bad in ("x" * 65, "abc", "a.b$c", "%24ne"):
        assert (await pay_http.get(f"/api/shared-carts/{bad}")).status_code == 404


async def test_shared_cart_size_limits_and_inactive_items(pay_http, pay_env):
    await pay_seed_product("p1", stock=5, variants=[{"id": "v-m", "size": "M", "stock": 3, "barcode": "BC-M"},
                                                    {"id": "v-l", "size": "L", "stock": 3, "barcode": "BC-L",
                                                     "is_active": False}])
    await pay_seed_product("off", stock=5, is_active=False)
    await pay_seed_product("gone", stock=5, is_deleted=True)
    too_many = [{"product_id": "p1", "quantity": 1}] * 101   # tavan = sipariş tavanı (100 kalem)
    assert (await pay_http.post("/api/shared-carts", json={"items": too_many})).status_code == 400
    assert (await pay_http.post("/api/shared-carts", json={"items": []})).status_code == 400
    assert (await pay_http.post("/api/shared-carts", json={"items": "x"})).status_code == 400
    r = await pay_http.post("/api/shared-carts", json={"items": [{"product_id": "off"}, {"product_id": "gone"},
                                                               {"product_id": "x" * 65}]})
    assert r.status_code == 400                                   # nothing sellable → nothing stored
    r = await pay_http.post("/api/shared-carts", json={"items": [{"product_id": "p1", "variant_id": "v-m"},
                                                               {"product_id": "p1", "variant_id": "v-l"},
                                                               {"product_id": "off"}]})
    assert r.status_code == 200
    out = (await pay_http.get(f"/api/shared-carts/{r.json()['id']}")).json()["items"]
    assert [i["variant"]["id"] for i in out] == ["v-m"]           # hidden variant dropped
    # product archived after sharing → disappears from the shared cart
    await pay_set("products", {"id": "p1"}, {"is_deleted": True})
    assert (await pay_http.get(f"/api/shared-carts/{r.json()['id']}")).status_code == 404


# ═════════════════════════════════════ SHOP01 ════════════════════════════════════════════════
INTERNAL = {"cost_price", "purchase_price", "alis_fiyati", "buy_price", "supplier", "tedarikci",
            "supplier_code", "member_price_1", "margin", "admin_notes", "internal_notes", "ticimax_fields",
            "vendor_id"}


async def test_search_and_autocomplete_never_leak_internal_fields(pay_http, pay_env, kv_mongomock_convert):
    from kv_harness import kv_all_keys
    secret = {"cost_price": 99.0, "purchase_price": 98.0, "alis_fiyati": 97.0, "supplier": "GIZLI-TEDARIK",
              "tedarikci": "GIZLI-TEDARIK", "supplier_code": "SUP-9", "member_price_1": 1.0, "margin": 0.5,
              "admin_notes": "GIZLI-NOT", "internal_notes": "GIZLI-NOT", "ticimax_fields": {"M": "GIZLI-TF"},
              "vendor_id": "v1", "images": ["https://cdn.example.invalid/s.webp"]}
    await pay_seed_product("s1", name="Keten Elbise", slug="keten-elbise", stock_code="KTN-1",
                           variants=[{"id": "s1-m", "size": "M", "stock": 2, "barcode": "869111",
                                      "cost_price": 50.0, "supplier": "GIZLI-TEDARIK"}], **secret)
    await pay_seed_product("s2", name="Keten Elbise Pasif", slug="keten-pasif", is_active=False, **secret)
    await pay_seed_product("s3", name="Keten Elbise Silinmis", slug="keten-silinmis", is_deleted=True, **secret)
    _, chdr = await pay_customer(email="c@example.test")
    for hdr in ({}, chdr):
        for params in ({"search": "keten", "limit": 12},            # header autocomplete
                       {"search": "keten", "limit": 40},            # search page
                       {"search": "KTN-1", "limit": 12},            # by stock code
                       {"search": "869111", "limit": 12},           # by barcode
                       {"search": "GIZLI-TEDARIK", "limit": 12}):   # by an internal value
            r = await pay_http.get("/api/products", params=params, headers=hdr)
            assert r.status_code == 200, r.text
            prods = r.json()["products"]
            assert {p["id"] for p in prods} <= {"s1"}, params      # inactive / deleted never surface
            if params["search"] in ("keten", "KTN-1", "869111"):
                assert [p["id"] for p in prods] == ["s1"], params  # the live product IS found
            assert not (kv_all_keys(r.json()) & INTERNAL), params
            for v in ("GIZLI-TEDARIK", "GIZLI-NOT", "GIZLI-TF", "SUP-9"):
                assert v not in r.text, (params, v)
        # the internal value is not a search oracle
        r = await pay_http.get("/api/products", params={"search": "GIZLI-TEDARIK"}, headers=hdr)
        assert r.json()["products"] == []


# ═════════════════════════════════════ SHOP05 ════════════════════════════════════════════════
async def test_review_cannot_self_approve_or_forge_author_and_rating_is_strict(pay_http, pay_env):
    u, hdr = await pay_customer(email="reviewer@example.test")
    await pay_set("users", {"id": u["id"]}, {"first_name": "Ayse", "last_name": "Kaya"})
    other, ohdr = await pay_customer(email="other@example.test")
    for bad in (0, 6, -1, 3.7, True, "4.5", "x", None, [5], {"$gt": 0}):
        r = await pay_http.post("/api/reviews", json={"product_id": "p-rev", "rating": bad}, headers=hdr)
        assert r.status_code == 400, bad
    forged = {"product_id": "p-rev", "rating": "5", "title": "t", "comment": "<script>alert(1)</script>",
              "status": "approved", "approved": True, "verified": True, "verified_purchase": True,
              "is_verified": True, "user_name": "Mağaza Yetkilisi", "user_id": other["id"], "id": "fixed-id",
              "source": "trendyol", "admin_reply": "onaylandı", "created_at": "2020-01-01T00:00:00+00:00",
              "moderated_by": "admin@example.test"}
    r = await pay_http.post("/api/reviews", json=forged, headers=hdr)
    assert r.status_code == 200, r.text
    rows = await pay_findall("reviews")
    assert len(rows) == 1
    rv = rows[0]
    assert rv["status"] == "pending" and rv["rating"] == 5 and rv["user_id"] == u["id"]
    assert rv["user_name"] == "Ayse K." and rv["id"] != "fixed-id"
    for k in ("approved", "verified", "verified_purchase", "is_verified", "source", "admin_reply",
              "moderated_by"):
        assert k not in rv, k
    assert rv["created_at"] > "2026"
    # pending review is not public; the customer has no moderation / delete surface
    assert (await pay_http.get("/api/reviews/product/p-rev")).json()["items"] == []
    for method, url, body in (("PUT", f"/api/admin/reviews/{rv['id']}", {"status": "approved"}),
                              ("DELETE", f"/api/admin/reviews/{rv['id']}", None),
                              ("GET", "/api/admin/reviews", None)):
        assert (await pay_http.request(method, url, json=body, headers=hdr)).status_code in (401, 403)
        assert (await pay_http.request(method, url, json=body, headers=ohdr)).status_code in (401, 403)
    assert (await pay_find("reviews", {"id": rv["id"]}))["status"] == "pending"
    # once approved by staff the script payload is served as inert JSON data, never as HTML
    _, shdr = await pay_staff(super_admin=True)
    assert (await pay_http.put(f"/api/admin/reviews/{rv['id']}", json={"status": "approved",
                                                                      "admin_reply": {"$set": 1}},
                               headers=shdr)).status_code == 200
    r = await pay_http.get("/api/reviews/product/p-rev")
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["items"][0]["comment"] == "<script>alert(1)</script>"
    assert "user_id" not in r.json()["items"][0]


# ═════════════════════════════════════ SHOP09 ════════════════════════════════════════════════
BAD_TARGETS = ["https://evil.example/x", "//evil.example/x", "/\\evil.example", "\\\\evil.example",
               "https://www.meluni.com.tr@evil.example/", "http://evil.example",
               "https://evil.example\\@www.meluni.com.tr/", "/ok\n//evil", "https:evil.example"]
# Legacy rows written straight into the DB may hold anything (the create endpoint would have
# prefixed scheme-less values with "/" — e.g. "javascript:x" became the harmless "/javascript:x").
BAD_LEGACY = BAD_TARGETS + ["javascript:alert(1)", "data:text/html,x", " //evil.example",
                            "ftp://www.meluni.com.tr/x"]


async def test_seo_redirect_targets_must_be_same_site(pay_http, pay_env, monkeypatch):
    monkeypatch.setenv("FRONTEND_PUBLIC_URL", "https://www.meluni.com.tr")
    _, shdr = await pay_staff(super_admin=True)
    for i, bad in enumerate(BAD_TARGETS):
        r = await pay_http.post("/api/admin/seo/redirects", json={"from_path": f"/eski-{i}", "to_path": bad},
                                headers=shdr)
        assert r.status_code == 400, (bad, r.text)
    for i, good in enumerate(["/yeni-sayfa", "yeni-kategori", "https://www.meluni.com.tr/kampanya",
                              "javascript:alert(1)"]):   # → stored as the relative path "/javascript:alert(1)"
        r = await pay_http.post("/api/admin/seo/redirects", json={"from_path": f"/ok-{i}", "to_path": good},
                                headers=shdr)
        assert r.status_code == 200, (good, r.text)
        rr = await pay_http.get("/api/seo/resolve-redirect", params={"path": f"/ok-{i}"})
        assert rr.json()["found"] is True
        to = rr.json()["to"]
        assert to.startswith("/") and not to.startswith("//") or to.startswith("https://www.meluni.com.tr/")
    # legacy unsafe rows (written before the check) are kept but never followed
    from routes import deps
    for i, bad in enumerate(BAD_LEGACY):
        await deps.db.seo_redirects.insert_one({"id": f"leg-{i}", "from_path": f"/leg-{i}", "to_path": bad,
                                                "status_code": 302, "is_active": True})
        rr = await pay_http.get("/api/seo/resolve-redirect", params={"path": f"/leg-{i}"})
        assert rr.json() == {"found": False}, bad
    assert len(await pay_findall("seo_redirects", {"id": {"$regex": "^leg-"}})) == len(BAD_LEGACY)


async def test_payment_return_url_cannot_redirect_off_site(pay_http, pay_env):
    """3DS return path: whatever return_url the client sends, the browser lands on our own host."""
    from urllib.parse import urlsplit
    from pay_harness import CALLBACK_URL, CARD, mock_3ds_init, pay_card_order
    for i, bad in enumerate(BAD_TARGETS + ["https://evil.example%5C@www.meluni.com.tr/x",
                                           "https://www.meluni.com.tr.evil.example/odeme"]):
        o = await pay_card_order(pay_http, pid=f"pr{i}")
        pay_env.iyzico.routes.clear()
        mock_3ds_init(pay_env.iyzico)
        r = await pay_http.post("/api/payment/3ds/initialize",
                                json={"order_id": o["id"], "callback_url": CALLBACK_URL, "return_url": bad,
                                      "card": CARD, "pay_token": _PT.get(o["id"], "")})
        assert r.status_code == 200 and r.json()["success"], r.text
        mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, payment_id=f"pid-r{i}"))
        r = await pay_callback(pay_http, o["id"], f"pid-r{i}")
        await pay_drain()
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "\\" not in loc and "\n" not in loc
        host = urlsplit(loc.replace("\\", "/")).hostname
        assert host == "www.meluni.com.tr", (bad, loc)
        assert "token" not in loc.lower()


async def test_referral_share_url_is_built_server_side(pay_http, pay_env):
    u, hdr = await pay_customer(email="ref@example.test")
    from routes import deps
    await deps.db.settings.update_one({"id": "tenant_config"},
                                      {"$set": {"id": "tenant_config",
                                                "domains": {"storefront_url": "https://www.meluni.com.tr"}}},
                                      upsert=True)
    try:
        import company
        company.invalidate()
    except Exception:
        pass
    r = await pay_http.get("/api/referrals/my", headers={**hdr, "Origin": "https://evil.example",
                                                         "Referer": "https://evil.example/x",
                                                         "X-Forwarded-Host": "evil.example",
                                                         "Host": "evil.example"})
    assert r.status_code == 200, r.text
    assert "evil.example" not in r.text
    assert r.json()["share_url"].startswith("https://www.meluni.com.tr/kayit?ref=")
