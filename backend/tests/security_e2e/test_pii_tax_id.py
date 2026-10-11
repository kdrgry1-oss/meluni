"""E2E: TCKN/VKN at-rest encryption (security/pii.py) — KVKK.

Covers, against the REAL app (mongomock, iyzico via respx, Doğan mocked):
  * create_order seals billing_info / billing_address tax ids (no plaintext anywhere in the stored doc),
    and the payment invariant still holds (card order → awaiting_payment, not paid);
  * admin detail reveals, lists / customer views are masked, edit form round-trip keeps the value;
  * iyzico buyer.identityNumber receives the REAL 11-digit TCKN (VKN → default placeholder unchanged);
  * Doğan e-Arşiv / e-Fatura UBL builders receive the REAL TCKN / VKN, CheckUser too;
  * Trendyol import (endpoint) + Hepsiburada / Amazon mappers seal; raw payload in error logs is masked;
  * admin search by full TCKN/VKN finds the order via the hash (no partial match);
  * backfill encrypts legacy plaintext (verify-before-unset), is resumable + idempotent;
  * nightly safety net finds stragglers written by a missed path and logs a warning.
"""
import json
import logging

import pytest

from pay_harness import (
    SHIP, iyz_auth_ok, mock_3ds_auth, order_payload, pay_callback, pay_create_order, pay_customer,
    pay_drain, pay_find, pay_findall, pay_init_3ds, pay_seed_product, pay_staff, IYZICO_BASE,
)
from routes import deps

pytestmark = pytest.mark.asyncio

TCKN = "12345678950"
VKN = "9876543210"


def _no_plain(doc, *values):
    blob = json.dumps(doc, default=str)
    for v in values:
        assert v not in blob, f"plaintext {v[:2]}… found in stored document"


async def _raw(coll, q):
    return await deps.db[coll].find_one(q)


def _corp(tax=TCKN):
    return {"is_corporate": True, "company_name": "Deneme AS", "tax_office": "Kadikoy", "tax_number": tax}


async def _card_order_with_tax(http, tax=TCKN, headers=None, billing_address=None):
    if not await pay_find("products", {"id": "p1"}):
        await pay_seed_product("p1", stock=10)
    extra = {"billing_info": _corp(tax)}
    if billing_address is not None:
        extra["billing_address"] = billing_address
    r = await pay_create_order(http, "credit_card", headers=headers,
                               items=[{"product_id": "p1", "quantity": 1, "price": 1}], **extra)
    assert r.status_code == 200, r.text
    return r.json()["order_id"]


def _shim_convert_to_date(monkeypatch):
    """mongomock lacks ``$convert`` (used by GET /api/orders to sort mixed-type created_at)."""
    import mongomock.aggregate as _agg
    from datetime import datetime
    orig = _agg._Parser._handle_type_convertion_operator

    def _conv(self, operator, values):
        if operator == "$convert" and isinstance(values, dict):
            try:
                v = self.parse(values.get("input"))
            except KeyError:
                v = None
            if v is None:
                return values.get("onNull")
            if values.get("to") == "date":
                if isinstance(v, datetime):
                    return v
                try:
                    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    return values.get("onError")
            return v
        return orig(self, operator, values)
    monkeypatch.setattr(_agg._Parser, "_handle_type_convertion_operator", _conv)
    orig_arith = _agg._Parser._handle_arithmetic_operator

    def _arith(self, operator, values):  # $add: date + milliseconds (MongoDB semantics)
        if operator == "$add" and isinstance(values, list):
            parsed = [self.parse(v) for v in values]
            dates = [v for v in parsed if isinstance(v, datetime)]
            if dates:
                from datetime import timedelta
                ms = sum(v for v in parsed if isinstance(v, (int, float)))
                return dates[0] + timedelta(milliseconds=ms)
        return orig_arith(self, operator, values)
    monkeypatch.setattr(_agg._Parser, "_handle_arithmetic_operator", _arith)


# ═══════════════════════════════════ create_order ═══════════════════════════════════
async def test_create_order_seals_tax_ids_and_keeps_payment_invariant(pay_http, pay_env):
    from security import pii
    oid = await _card_order_with_tax(pay_http, billing_address={**SHIP, "tax_no": VKN})
    o = await _raw("orders", {"id": oid})
    bi, ba = o["billing_info"], o["billing_address"]
    assert "tax_number" not in bi and "tax_no" not in ba
    assert bi["tax_number_enc"].startswith("v1:") and bi["tax_number_last2"] == TCKN[-2:]
    assert ba["tax_no_enc"].startswith("v1:") and ba["tax_no_last2"] == VKN[-2:]
    assert bi["tax_number_hash"] == pii.tax_id_hash(TCKN)
    assert pii.reveal_tax_id(bi, "tax_number") == TCKN and pii.reveal_tax_id(ba, "tax_no") == VKN
    _no_plain(o, TCKN, VKN)
    # CLAUDE.md invariants 1/2: a card order is created awaiting payment, never paid.
    assert o["status"] == "awaiting_payment" and o["payment_status"] != "paid"


async def test_client_cannot_inject_sealed_fields(pay_http, pay_env):
    await pay_seed_product("p1", stock=3)
    bad = {**SHIP, "tax_number_hash": "jwt:deadbeef", "tax_number_enc": "v1:forged"}
    r = await pay_create_order(pay_http, "credit_card", billing_address=bad,
                               items=[{"product_id": "p1", "quantity": 1, "price": 1}])
    assert r.status_code == 200, r.text
    ba = (await _raw("orders", {"id": r.json()["order_id"]}))["billing_address"]
    assert "tax_number_hash" not in ba and "tax_number_enc" not in ba


async def test_member_address_saved_from_order_is_sealed(pay_http, pay_env):
    cust, hdr = await pay_customer("pii-member@example.test")
    await _card_order_with_tax(pay_http, headers=hdr)
    await pay_drain()
    addrs = await deps.db.addresses.find({"user_id": cust["id"]}).to_list(10)
    assert addrs, "order address was not saved to the member address book"
    from security import pii
    assert "tax_no" not in addrs[0] and pii.reveal_tax_id(addrs[0], "tax_no") == TCKN
    _no_plain(addrs[0], TCKN)
    r = await pay_http.get("/api/my-addresses", headers=hdr)
    assert r.status_code == 200 and r.json()["addresses"][0]["tax_no"] == "*********" + TCKN[-2:]
    # Saving the form unchanged (masked) keeps the encrypted value; typing a new one replaces it.
    aid = addrs[0]["id"]
    r = await pay_http.put(f"/api/addresses/{aid}", headers=hdr,
                           json={"tax_no": "*********" + TCKN[-2:], "title": "Ev"})
    assert r.status_code == 200
    a = await _raw("addresses", {"id": aid})
    assert pii.reveal_tax_id(a, "tax_no") == TCKN and a["title"] == "Ev"
    r = await pay_http.put(f"/api/addresses/{aid}", headers=hdr, json={"tax_no": VKN})
    a = await _raw("addresses", {"id": aid})
    assert pii.reveal_tax_id(a, "tax_no") == VKN and "tax_no" not in a
    _no_plain(a, VKN)


# ═══════════════════════════════ views: reveal vs mask ═══════════════════════════════
async def test_admin_detail_reveals_lists_and_customer_views_mask(pay_http, pay_env, monkeypatch):
    _shim_convert_to_date(monkeypatch)
    cust, chdr = await pay_customer("pii-view@example.test")
    oid = await _card_order_with_tax(pay_http, headers=chdr)
    _, ahdr = await pay_staff(super_admin=True)
    det = (await pay_http.get(f"/api/orders/{oid}", headers=ahdr)).json()
    assert det["billing_info"]["tax_number"] == TCKN
    assert not any(k.startswith("tax_number_") for k in det["billing_info"])
    _lr = await pay_http.get("/api/orders?show_hidden=1", headers=ahdr)
    assert _lr.status_code == 200, _lr.text
    lst = _lr.json()["orders"]
    row = next(x for x in lst if x["id"] == oid)
    assert row["billing_info"]["tax_number"] == "*********50"
    assert "tax_number_enc" not in row["billing_info"]
    own = (await pay_http.get(f"/api/orders/{oid}", headers=chdr)).json()
    assert own["billing_info"]["tax_number"] == "*********50"
    mine = (await pay_http.get("/api/my-orders", headers=chdr)).json()["orders"]
    assert mine[0]["billing_info"]["tax_number"] == "*********50"
    _no_plain(mine, TCKN)
    pub = (await pay_http.get(f"/api/orders/by-number/{det['order_number']}")).json()
    _no_plain(pub, TCKN)
    assert "billing_info" not in pub


async def test_admin_edit_roundtrip_masked_keeps_value_new_value_reseals(pay_http, pay_env):
    from security import pii
    oid = await _card_order_with_tax(pay_http)
    _, ahdr = await pay_staff(super_admin=True)
    # Panel form saved WITHOUT touching the field (list fallback → masked value) → unchanged.
    r = await pay_http.put(f"/api/orders/{oid}", headers=ahdr,
                           json={"billing_info": {**_corp("*********50"), "company_name": "Yeni AS"}})
    assert r.status_code == 200, r.text
    o = await _raw("orders", {"id": oid})
    assert pii.reveal_tax_id(o["billing_info"], "tax_number") == TCKN
    assert o["billing_info"]["company_name"] == "Yeni AS"
    # New value typed → sealed, never plaintext; the order event log carries only a masked view.
    r = await pay_http.put(f"/api/orders/{oid}", headers=ahdr, json={"billing_info": _corp(VKN)})
    assert r.status_code == 200
    o = await _raw("orders", {"id": oid})
    assert pii.reveal_tax_id(o["billing_info"], "tax_number") == VKN
    _no_plain(o, VKN, TCKN)
    evs = await pay_findall("order_events", {"order_id": oid})
    assert evs and all(TCKN not in json.dumps(e, default=str) and VKN not in json.dumps(e, default=str)
                       for e in evs)


# ═══════════════════════════════════ search ═══════════════════════════════════
async def test_admin_search_by_full_tax_id_uses_hash(pay_http, pay_env, monkeypatch):
    _shim_convert_to_date(monkeypatch)
    oid = await _card_order_with_tax(pay_http)
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.get(f"/api/orders?search={TCKN}", headers=ahdr)
    assert r.status_code == 200
    assert [o["id"] for o in r.json()["orders"]] == [oid]
    # Partial TCKN no longer matches (no regex scan over identity numbers).
    r = await pay_http.get(f"/api/orders?search={TCKN[3:10]}", headers=ahdr)   # 7-digit slice, not in phone
    assert oid not in [o["id"] for o in r.json()["orders"]]


async def test_vendor_identity_number_sealed_masked_and_searchable(pay_http, pay_env):
    from security import pii
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.post("/api/vendors", headers=ahdr,
                            json={"name": "Atolye", "identity_number": TCKN, "tax_number": VKN})
    assert r.status_code == 200, r.text
    vid = r.json()["id"]
    v = await _raw("vendors", {"id": vid})
    _no_plain(v, TCKN, VKN)
    assert pii.reveal_tax_id(v, "identity_number") == TCKN
    lst = (await pay_http.get(f"/api/vendors?search={VKN}", headers=ahdr)).json()["vendors"]
    assert [x["id"] for x in lst] == [vid] and lst[0]["tax_number"] == "*********10"
    r = await pay_http.put(f"/api/vendors/{vid}", headers=ahdr,
                           json={"identity_number": "*********50", "tax_number": "", "name": "Atolye 2"})
    assert r.status_code == 200
    v = await _raw("vendors", {"id": vid})
    assert pii.reveal_tax_id(v, "identity_number") == TCKN and pii.reveal_tax_id(v, "tax_number") == ""


# ═══════════════════════════════════ iyzico ═══════════════════════════════════
def _capture_3ds_init(router):
    seen = []

    def _cb(request):
        import httpx
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"status": "success", "paymentId": "pid-init",
                                         "threeDSHtmlContent": "PGh0bWwvPg=="})
    router.post(f"{IYZICO_BASE}/payment/3dsecure/initialize").mock(side_effect=_cb)
    return seen


async def test_iyzico_buyer_identity_number_is_real_tckn(pay_http, pay_env):
    oid = await _card_order_with_tax(pay_http, tax=TCKN)
    seen = _capture_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, oid)
    assert r.status_code == 200, r.text
    assert seen and seen[0]["buyer"]["identityNumber"] == TCKN
    o = await _raw("orders", {"id": oid})
    _no_plain(o, TCKN)                       # nothing written back in clear
    assert o["payment_status"] != "paid"     # invariant 1: init never marks paid


async def test_iyzico_vkn_still_uses_default_placeholder(pay_http, pay_env):
    oid = await _card_order_with_tax(pay_http, tax=VKN)
    seen = _capture_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, oid)
    assert r.status_code == 200, r.text
    assert seen[0]["buyer"]["identityNumber"] == "11111111111"


async def test_build_initialize_payload_reads_sealed_and_legacy():
    from routes.payment import _build_initialize_payload
    from security import pii
    base = {"id": "o1", "order_number": "W1", "total": 10, "subtotal": 10, "items": [
        {"product_id": "p", "name": "x", "price": 10, "quantity": 1}], "shipping_address": dict(SHIP)}
    sealed = {**base, "billing_info": pii.seal_doc_tax_ids({"billing_info": {"tax_number": TCKN}})["billing_info"]}
    legacy = {**base, "billing_info": {"tax_number": TCKN}}
    for o in (sealed, legacy):
        assert _build_initialize_payload(o, "https://www.meluni.com.tr/cb")["buyer"]["identityNumber"] == TCKN


# ═══════════════════════════════════ Doğan UBL ═══════════════════════════════════
class _DoganSpy:
    def __init__(self, monkeypatch, efatura=False):
        import dogan_client
        self.earsiv, self.efatura, self.check = [], [], []
        DC = dogan_client.DoganClient
        orig_ea, orig_ef = DC.build_earsiv_ubl_xml, DC.build_efatura_ubl_xml
        spy = self

        def _ea(**kw):
            spy.earsiv.append(kw)
            return orig_ea(**kw)

        def _ef(**kw):
            spy.efatura.append(kw)
            return orig_ef(**kw)

        def _check(self_, vkn):
            spy.check.append(vkn)
            return {"is_efatura": efatura, "invoice_alias": "urn:mail:defaultpk@test" if efatura else ""}

        ok = {"success": True, "code": "0", "message": "OK", "invoice_id": "X",
              "web_key": "https://earsiv.example.test/v/1", "intl_txn_id": "itx"}
        monkeypatch.setattr(DC, "build_earsiv_ubl_xml", staticmethod(_ea))
        monkeypatch.setattr(DC, "build_efatura_ubl_xml", staticmethod(_ef))
        monkeypatch.setattr(DC, "check_user", _check)
        monkeypatch.setattr(DC, "send_earsiv_invoice", lambda self_, *a, **k: dict(ok))
        monkeypatch.setattr(DC, "send_efatura_invoice", lambda self_, *a, **k: dict(ok))
        monkeypatch.setattr(DC, "login", lambda self_: "sess")
        monkeypatch.setattr(DC, "logout", lambda self_: None)
        monkeypatch.setattr(DC, "_make_header", lambda self_, compressed="N": {})


async def _paid_order(http, env, tax):
    oid = await _card_order_with_tax(http, tax=tax)
    o = await pay_find("orders", {"id": oid})
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o))
    r = await pay_callback(http, oid, "pid-1")
    assert r.status_code in (200, 302, 303), r.text
    await pay_drain()
    o = await pay_find("orders", {"id": oid})
    assert o["status"] == "confirmed" and o["payment_status"] == "paid"   # invariant 1 (real payment)
    return o


async def _enable_dogan():
    await deps.db.settings.insert_one({"id": "dogan_edonusum", "enabled": True, "username": "u",
                                       "password": "p", "is_test": True})


async def test_dogan_earsiv_ubl_receives_real_tckn(pay_http, pay_env, monkeypatch):
    spy = _DoganSpy(monkeypatch, efatura=False)
    await _enable_dogan()
    o = await _paid_order(pay_http, pay_env, TCKN)
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.post(f"/api/orders/{o['id']}/create-invoice", headers=ahdr)
    assert r.status_code == 200, r.text
    assert spy.check == [TCKN]
    assert spy.earsiv and spy.earsiv[0]["customer_vkn_or_tckn"] == TCKN
    after = await _raw("orders", {"id": o["id"]})
    assert after["invoice_issued"] is True
    _no_plain(after, TCKN)


async def test_dogan_efatura_ubl_receives_real_vkn(pay_http, pay_env, monkeypatch):
    spy = _DoganSpy(monkeypatch, efatura=True)
    await _enable_dogan()
    o = await _paid_order(pay_http, pay_env, VKN)
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.post(f"/api/orders/{o['id']}/create-invoice", headers=ahdr)
    assert spy.check and spy.check[0] == VKN
    assert spy.efatura and spy.efatura[0]["customer_vkn"] == VKN, r.text
    _no_plain(await _raw("orders", {"id": o["id"]}), VKN)


async def test_invoice_diagnose_sees_sealed_vkn(pay_http, pay_env):
    oid = await _card_order_with_tax(pay_http, tax=VKN)
    o = await pay_find("orders", {"id": oid})
    _, ahdr = await pay_staff(super_admin=True)
    d = (await pay_http.get(f"/api/orders/diagnose/invoice?q={o['order_number']}", headers=ahdr)).json()
    assert d["has_vkn_tckn"] is True and d["vkn_tckn_len"] == 10
    _no_plain(d, VKN)


async def test_order_is_efatura_with_sealed_vkn():
    from routes.orders import _order_is_efatura
    from security import pii
    o = pii.seal_doc_tax_ids({"billing_address": {"tax_number": VKN}})
    assert _order_is_efatura(o) is True
    assert _order_is_efatura(pii.seal_doc_tax_ids({"billing_address": {"tax_number": TCKN}})) is False


# ═══════════════════════════════ marketplace import ═══════════════════════════════
def _ty_order(no="TY-PII-1", lines=None):
    return {"orderNumber": no, "id": 555001, "status": "Created", "orderDate": 1760000000000,
            "identityNumber": TCKN, "taxNumber": "",
            "customerEmail": "ty@example.test", "totalPrice": 100, "grossAmount": 100, "totalDiscount": 0,
            "shipmentAddress": {"firstName": "Ali", "lastName": "Veli", "fullAddress": "Adres", "city": "Ankara",
                                "district": "Cankaya", "phone": "05550000000", "country": "Türkiye"},
            "invoiceAddress": {"firstName": "Ali", "lastName": "Veli", "fullAddress": "Adres", "city": "Ankara",
                               "district": "Cankaya", "taxNumber": VKN, "taxOffice": "Cankaya",
                               "company": "Veli Ltd", "country": "Türkiye"},
            "lines": lines if lines is not None else [
                {"productName": "Elbise", "quantity": 1, "price": 100, "amount": 100, "barcode": "BC-p1",
                 "merchantSku": "S1", "productSize": "M", "productColor": "Siyah", "discount": 0}]}


async def test_trendyol_import_selected_seals_and_error_log_is_masked(pay_http, pay_env):
    from security import pii
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.post("/api/integrations/trendyol/orders/import-selected", headers=ahdr,
                            json={"orders": [_ty_order(), _ty_order("TY-PII-BAD", lines=5)]})
    assert r.status_code == 200, r.text
    o = await _raw("orders", {"order_number": "TY-PII-1"})
    assert o, r.json()
    _no_plain(o, TCKN, VKN)
    assert pii.reveal_tax_id(o, "trendyol_identity_number") == TCKN
    assert pii.reveal_tax_id(o["billing_address"], "tax_number") == VKN
    assert pii.reveal_tax_id(o["billing_info"], "tax_number") == VKN
    # The failing order's raw payload went to integration_logs — identity numbers masked there.
    logs = await pay_findall("integration_logs", {"entity_id": "TY-PII-BAD"})
    assert logs and logs[0]["details"]["raw"]["identityNumber"] == "*********50"
    _no_plain(logs, TCKN, VKN)


async def test_trendyol_reimport_of_legacy_order_drops_plaintext(pay_http, pay_env):
    await deps.db.orders.insert_one({"id": "legacy-ty", "order_number": "TY-PII-1", "platform": "trendyol",
                                     "status": "confirmed", "trendyol_identity_number": TCKN,
                                     "billing_info": {"tax_number": VKN}})
    _, ahdr = await pay_staff(super_admin=True)
    r = await pay_http.post("/api/integrations/trendyol/orders/import-selected", headers=ahdr,
                            json={"orders": [_ty_order()]})
    assert r.status_code == 200 and r.json()["updated"] == 1
    _no_plain(await _raw("orders", {"id": "legacy-ty"}), TCKN, VKN)


async def test_hepsiburada_and_amazon_mappers_seal():
    from routes.integrations_hepsiburada import map_hepsiburada_order
    from routes.amazon_spapi import map_amazon_order
    from security import pii
    hb = map_hepsiburada_order({"orderNumber": "HB1", "lines": [{"merchantSku": "S", "quantity": 1, "price": 10}],
                                "invoiceAddress": {"name": "A", "taxNumber": VKN, "taxOffice": "X"},
                                "shippingAddress": {"name": "A B"}})
    _no_plain(hb, VKN)
    assert pii.reveal_tax_id(hb["billing_address"], "tax_number") == VKN
    amz = map_amazon_order({"AmazonOrderId": "A1", "OrderStatus": "Unshipped", "OrderTotal": {"Amount": "1"}}, [])
    assert amz["billing_info"]["tax_number"] == "" and "tax_number_enc" not in amz["billing_info"]


async def test_integration_logs_scrub_nested_payloads():
    from security.pii import scrub_tax_ids, log_has_plain_tax_ids
    raw = {"raw": {"identityNumber": TCKN, "invoiceAddress": {"taxNumber": VKN}, "lines": [{"vkn": VKN}],
                   "billing_info": {"tax_number_enc": "v1:x", "tax_number_hash": "jwt:y", "tax_number_last2": "50"}}}
    out = scrub_tax_ids(raw)
    _no_plain(out, TCKN, VKN)
    assert not log_has_plain_tax_ids(out) and log_has_plain_tax_ids(raw)
    assert "tax_number_enc" not in out["raw"]["billing_info"]


# ═══════════════════════════════ backfill + nightly net ═══════════════════════════════
async def _seed_legacy():
    await deps.db.orders.insert_many([
        {"id": f"lg-{i}", "order_number": f"L{i}", "billing_info": {"tax_number": TCKN, "is_corporate": True},
         "billing_address": {"tax_no": VKN, "first_name": "A"}, "trendyol_identity_number": TCKN}
        for i in range(5)])
    await deps.db.orders.insert_one({"id": "lg-empty", "billing_info": {"tax_number": ""}})
    await deps.db.orders_deleted.insert_one({"id": "lg-del", "billing_info": {"tax_number": VKN}})
    await deps.db.addresses.insert_one({"id": "ad-1", "user_id": "u1", "tax_no": VKN})
    await deps.db.vendors.insert_one({"id": "v-1", "identity_number": TCKN})
    await deps.db.integration_logs.insert_one({"platform": "trendyol", "entity_id": "x", "created_at": "2026-01-01",
                                               "details": {"raw": {"identityNumber": TCKN}}})
    await deps.db.order_events.insert_one({"id": "ev1", "order_id": "lg-0", "created_at": "2026-01-01",
                                           "meta": {"old_billing_info": {"tax_number": TCKN}}})


async def _assert_all_sealed():
    from security import pii
    for coll in ("orders", "orders_deleted", "addresses", "vendors", "integration_logs", "order_events"):
        docs = await deps.db[coll].find({}).to_list(100)
        _no_plain(docs, TCKN, VKN)
    for o in await deps.db.orders.find({"id": {"$regex": "^lg-[0-9]"}}).to_list(10):
        assert pii.reveal_tax_id(o["billing_info"], "tax_number") == TCKN
        assert pii.reveal_tax_id(o["billing_address"], "tax_no") == VKN
        assert pii.reveal_tax_id(o, "trendyol_identity_number") == TCKN
        assert o["billing_info"]["is_corporate"] is True and o["billing_address"]["first_name"] == "A"
    assert (await _raw("orders", {"id": "lg-empty"}))["billing_info"]["tax_number"] == ""
    assert pii.reveal_tax_id(await _raw("addresses", {"id": "ad-1"}), "tax_no") == VKN
    assert pii.reveal_tax_id(await _raw("vendors", {"id": "v-1"}), "identity_number") == TCKN


async def test_backfill_encrypts_legacy_plaintext_and_is_idempotent(pay_env):
    import scheduler
    await _seed_legacy()
    rep = await scheduler.encrypt_existing_tax_ids(batch_size=2)
    assert rep["encrypted"]["orders"] == 5 and rep["encrypted"]["orders_deleted"] == 1
    assert rep["encrypted"]["addresses"] == 1 and rep["encrypted"]["vendors"] == 1
    assert rep["logs_scrubbed"] == {"integration_logs": 1, "order_events": 1}
    await _assert_all_sealed()
    flag = await pay_find("settings", {"id": scheduler.PII_BACKFILL_FLAG})
    assert flag["done"] is True
    logs = await pay_findall("integration_logs", {"platform": "pii_backfill"})
    assert logs and logs[-1]["action"] == "encrypt_tax_ids" and logs[-1]["status"] == "success"
    before = await deps.db.orders.find({}, {"_id": 0}).to_list(100)
    again = await scheduler.encrypt_existing_tax_ids()
    assert again.get("skipped") is True
    assert await deps.db.orders.find({}, {"_id": 0}).to_list(100) == before   # untouched


async def test_backfill_resumes_after_leadership_loss(pay_env, monkeypatch):
    import scheduler
    await _seed_legacy()
    calls = {"n": 0}

    async def _leader():
        calls["n"] += 1
        return calls["n"] <= 1          # first batch only, then "lost leadership"
    monkeypatch.setattr(scheduler, "_still_leader", _leader)
    rep = await scheduler.encrypt_existing_tax_ids(batch_size=2)
    assert rep["aborted"] is True and rep["encrypted"]["orders"] == 2
    flag = await pay_find("settings", {"id": scheduler.PII_BACKFILL_FLAG})
    assert not flag.get("done") and flag["cursors"]["orders"]
    monkeypatch.setattr(scheduler, "_still_leader", lambda: _true())
    rep2 = await scheduler.encrypt_existing_tax_ids(batch_size=2)
    assert rep2["aborted"] is False and rep2["encrypted"]["orders"] == 3
    await _assert_all_sealed()


async def _true():
    return True


async def test_backfill_never_unsets_plaintext_when_verification_fails(pay_env, monkeypatch):
    import scheduler
    from security import pii
    await deps.db.orders.insert_one({"id": "bad", "billing_info": {"tax_number": TCKN}})
    monkeypatch.setattr(pii, "reveal_tax_id", lambda c, f: "00000000000")   # decrypt mismatch
    rep = await scheduler.encrypt_existing_tax_ids()
    assert rep["verify_failed"]["orders"] == 1 and rep["encrypted"]["orders"] == 0
    o = await _raw("orders", {"id": "bad"})
    assert o["billing_info"]["tax_number"] == TCKN and "tax_number_enc" not in o["billing_info"]
    logs = await pay_findall("integration_logs", {"platform": "pii_backfill"})
    assert logs[-1]["status"] == "warning"


async def test_nightly_net_catches_stragglers_and_warns(pay_env, caplog):
    import scheduler
    from security import pii
    await deps.db.orders.insert_one({"id": "clean", "billing_info": pii.seal_doc_tax_ids(
        {"billing_info": {"tax_number": VKN}})["billing_info"]})
    rep = await scheduler.encrypt_existing_tax_ids(nightly=True)
    assert sum(rep["encrypted"].values()) == 0
    # A path that forgot to seal writes plaintext → caught the next night.
    await deps.db.orders.insert_one({"id": "straggler", "shipping_address": {"tckn": TCKN}})
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        rep = await scheduler.encrypt_existing_tax_ids(nightly=True)
    assert rep["encrypted"]["orders"] == 1 and rep["paths"]["orders"] == {"shipping_address.tckn": 1}
    assert any("GECE AĞI" in m and "orders" in m for m in caplog.messages)
    o = await _raw("orders", {"id": "straggler"})
    assert pii.reveal_tax_id(o["shipping_address"], "tckn") == TCKN
    _no_plain(o, TCKN)
    logs = await pay_findall("integration_logs", {"platform": "pii_backfill", "action": "nightly_net"})
    assert logs[-1]["status"] == "warning"
    _no_plain(logs, TCKN)
    # Search by hash finds the straggler after sealing.
    hits = await deps.db.orders.find({"$or": pii.tax_id_search_clauses(TCKN)}).to_list(10)
    assert [h["id"] for h in hits] == ["straggler"]


async def test_nightly_net_rehashes_when_primary_hash_key_changes(pay_env, monkeypatch):
    import scheduler
    from security import pii
    await deps.db.orders.insert_one({"id": "rh", "billing_info": pii.seal_doc_tax_ids(
        {"billing_info": {"tax_number": TCKN}})["billing_info"]})
    old_hash = (await _raw("orders", {"id": "rh"}))["billing_info"]["tax_number_hash"]
    monkeypatch.setenv("PII_HASH_SECRET", "p" * 40)
    assert pii.primary_hash_kid() == "env"
    # Before rehash, search still finds it (all keys are tried).
    assert await deps.db.orders.find_one({"$or": pii.tax_id_search_clauses(TCKN)})
    rep = await scheduler.encrypt_existing_tax_ids(nightly=True)
    assert rep["rehashed"] == {"orders": 1}
    new_hash = (await _raw("orders", {"id": "rh"}))["billing_info"]["tax_number_hash"]
    assert new_hash.startswith("env:") and new_hash != old_hash


async def test_scheduler_registers_backfill_and_nightly_jobs():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler)
    assert 'id="pii_tax_id_backfill_once"' in src and "timedelta(seconds=90)" in src
    assert 'id="pii_tax_id_nightly_net"' in src
