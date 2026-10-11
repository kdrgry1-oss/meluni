"""E2E (commerce-2): order state-machine races and fulfillment safety.

COM08  compare-and-set status transitions (concurrent ship vs cancel → one wins, other 409)
COM09  address change blocked once a cargo label exists / is being created; atomic label lock
COM10  concurrent return requests for the same item → only the allowed quantity, one MNG return
FIN-E06 label exists at MNG but the local record lost it → lookup by siparis_no and LINK (no duplicate)
PRIV09 cargo label payload carries no payment data and no unnecessary profile fields
"""
import asyncio

import pytest

from permissions import upgrade_grants_for
from pay_harness import (
    SHIP, iyz_auth_ok, mock_3ds_auth, now_iso, pay_callback, pay_card_order, pay_customer,
    pay_create_order, pay_drain, pay_find, pay_findall, pay_seed_product, pay_set, pay_staff, pay_stock,
)

pytestmark = pytest.mark.asyncio


async def _paid_order(http, env, pid="p1", stock=5, headers=None):
    if headers:
        if not await pay_find("products", {"id": pid}):
            await pay_seed_product(pid, stock=stock)
        r = await pay_create_order(http, headers=headers, items=[{"product_id": pid, "quantity": 1, "price": 1}])
        assert r.status_code == 200, r.text
        o = await pay_find("orders", {"id": r.json()["order_id"]})
    else:
        o = await pay_card_order(http, pid=pid, stock=stock)
    env.iyzico.routes.clear()
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id=f"pid-{o['id'][:6]}"))
    await pay_callback(http, o["id"], f"pid-{o['id'][:6]}")
    await pay_drain()
    env.iyzico.routes.clear()
    o = await pay_find("orders", {"id": o["id"]})
    assert o["status"] == "confirmed" and o["payment_status"] == "paid"
    return o


# ═════════════════════════════════════ COM08 ═════════════════════════════════════════════════
async def test_concurrent_ship_and_cancel_one_wins_other_409(pay_http, pay_env):
    o = await _paid_order(pay_http, pay_env)
    assert await pay_stock("p1") == 4
    _, shdr = await pay_staff(super_admin=True)
    ship = pay_http.post(f"/api/orders/{o['id']}/ship",
                         params={"cargo_company": "MNG", "tracking_number": "TRK12345",
                                 "expected_status": "confirmed"}, headers=shdr)
    cancel = pay_http.put(f"/api/orders/{o['id']}/status",
                          params={"status": "cancelled", "expected_status": "confirmed"}, headers=shdr)
    r_ship, r_cancel = await asyncio.gather(ship, cancel)
    await pay_drain()
    assert sorted([r_ship.status_code, r_cancel.status_code]) == [200, 409], (r_ship.text, r_cancel.text)
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["payment_status"] == "paid"                              # never touched (invariant 1)
    if r_ship.status_code == 200:
        assert cur["status"] == "shipped" and cur["cargo_tracking_number"] == "TRK12345"
        assert await pay_stock("p1") == 4                               # no restock for a live order
    else:
        assert cur["status"] == "cancelled" and not cur.get("cargo_tracking_number")
        assert await pay_stock("p1") == 5                               # restocked exactly once
    # a cancelled order can never be shipped afterwards, even though it is "paid"
    if cur["status"] == "cancelled":
        r = await pay_http.post(f"/api/orders/{o['id']}/ship",
                                params={"cargo_company": "MNG", "tracking_number": "TRK999"}, headers=shdr)
        assert r.status_code == 409
        assert not (await pay_find("orders", {"id": o["id"]})).get("cargo_tracking_number")


async def test_status_update_is_compare_and_set(pay_http, pay_env, monkeypatch):
    """Race injection: the status changes between update_order_status' read and its write."""
    o = await _paid_order(pay_http, pay_env)
    _, shdr = await pay_staff(super_admin=True)
    from routes import deps
    coll_cls = type(deps.db.orders)
    real_find_one = coll_cls.find_one
    fired = {"n": 0}

    async def _racy_find_one(self, flt=None, projection=None, *a, **k):
        doc = await real_find_one(self, flt, projection, *a, **k)
        if (self.name == "orders" and isinstance(projection, dict) and "chargeback_open" in projection
                and projection.get("status") == 1 and not fired["n"]):
            fired["n"] += 1                      # a concurrent worker ships the order right now
            await real_find_one(self, {"id": o["id"]})
            await deps.db.orders.update_one({"id": o["id"]}, {"$set": {"status": "shipped",
                                                                       "cargo_tracking_number": "RACE1"}})
        return doc

    monkeypatch.setattr(coll_cls, "find_one", _racy_find_one)
    r = await pay_http.put(f"/api/orders/{o['id']}/status", params={"status": "cancelled"}, headers=shdr)
    assert fired["n"] == 1
    assert r.status_code == 409, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "shipped" and cur["payment_status"] == "paid"
    assert await pay_stock("p1") == 4                                    # the losing cancel did NOT restock
    # stale expected_status from the operator's screen is refused too
    monkeypatch.setattr(coll_cls, "find_one", real_find_one)
    r = await pay_http.put(f"/api/orders/{o['id']}/status",
                           params={"status": "delivered", "expected_status": "confirmed"}, headers=shdr)
    assert r.status_code == 409
    # bulk endpoint still works (calls the handler directly) and reports the 404 per id
    r = await pay_http.post("/api/orders/bulk-status", params={"status": "delivered"},
                            json=[o["id"], "nope"], headers=shdr)
    assert r.status_code == 200 and r.json()["success_count"] == 1 and r.json()["error_count"] == 1
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "delivered"


# ═════════════════════════════════════ COM09 / FIN-E06 / PRIV09 ══════════════════════════════
class _Mng:
    def __init__(self, create_result=None, lookup=None):
        self.create_calls, self.lookup_calls, self.nz_calls = [], [], []
        self.create_result = create_result or {"ok": True, "barkod": "MNGSELF1", "gonderi_no": ""}
        self.lookup = lookup or {"ok": True, "mng_siparis_no": "MNGSELF1", "gonderi_no": "",
                                 "kargo_statu": "0"}

    def create(self, **kw):
        self.create_calls.append(kw)
        return dict(self.create_result)

    def status(self, **kw):
        self.lookup_calls.append(kw)
        return dict(self.lookup)

    def nz(self, **kw):
        self.nz_calls.append(kw)
        return {"ok": False, "hata": "yetki yok"}


@pytest.fixture
def mng(pay_env, monkeypatch):
    import mng_kargo_client
    m = _Mng()
    monkeypatch.setattr(mng_kargo_client, "create_shipment", m.create)
    monkeypatch.setattr(mng_kargo_client, "get_mng_shipment_status", m.status)
    monkeypatch.setattr(mng_kargo_client, "get_mng_barcode_immediately", m.nz)
    from routes import deps
    asyncio.run(deps.db.settings.insert_one({"id": "mng_kargo", "username": "mng-user", "password": "mng-pass",
                                             "is_active": True, "customer_code": "MAGAZA"}))
    return m


async def test_address_change_blocked_after_label_and_label_uses_current_address(pay_http, pay_env, mng):
    o = await _paid_order(pay_http, pay_env)
    # IAM-02: rol, açılıştaki tek seferlik izin geçişinden geçmiş hâliyle (permissions.upgrade_grants_for).
    _, ops = await pay_staff(["orders.update_status"] + upgrade_grants_for(["orders.update_status"]))
    _, sup = await pay_staff(super_admin=True)
    # before a label exists the address can change; the label then carries the NEW address
    new_addr = {**SHIP, "address": "Guncel Mah. Yeni Sk. 5", "district": "Besiktas"}
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"shipping_address": new_addr}, headers=ops)
    assert r.status_code == 200, r.text
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200, r.text
    assert len(mng.create_calls) == 1
    assert mng.create_calls[0]["adres"] == "Guncel Mah. Yeni Sk. 5" and mng.create_calls[0]["ilce"] == "Besiktas"
    # after the label: address change refused (even for operations staff), data unchanged
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"shipping_address": {**SHIP, "address": "Baska"}},
                           headers=ops)
    assert r.status_code == 409
    assert (await pay_find("orders", {"id": o["id"]}))["shipping_address"]["address"] == "Guncel Mah. Yeni Sk. 5"
    assert len(mng.create_calls) == 1
    # non-address edits still work — also when the panel form re-sends the UNCHANGED address with
    # extra empty keys / whitespace (no false "address changed" 409)
    assert (await pay_http.put(f"/api/orders/{o['id']}", json={"notes": "kapıya bırak"}, headers=ops)).status_code == 200
    same = dict((await pay_find("orders", {"id": o["id"]}))["shipping_address"])
    same.update({"apartment": "", "address": " " + same["address"] + " ", "full_name": None})
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"shipping_address": same, "notes": "tekrar"}, headers=ops)
    assert r.status_code == 200, r.text
    # super admin override needs a reason; it is flagged + audited
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"shipping_address": {**SHIP, "address": "Baska"},
                                                           "address_change_override_reason": "kısa"}, headers=sup)
    assert r.status_code == 409
    r = await pay_http.put(f"/api/orders/{o['id']}",
                           json={"shipping_address": {**SHIP, "address": "Baska Mah. 7"},
                                 "address_change_override_reason": "Müşteri aradı, MNG kaydı elle düzeltildi"},
                           headers=sup)
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["shipping_address"]["address"] == "Baska Mah. 7" and cur["cargo_address_changed_after_label"] is True
    assert "address_change_override_reason" not in cur
    audits = await pay_findall("admin_audit_logs", {"action": "orders.address_change_after_label"}) or \
        [a for a in await pay_findall("audit_logs") if a.get("action") == "orders.address_change_after_label"]
    assert audits, "override must be audited"


async def test_label_lock_blocks_concurrent_label_and_address_change(pay_http, pay_env, mng):
    o = await _paid_order(pay_http, pay_env)
    _, sup = await pay_staff(super_admin=True)
    from routes import orders as orders_mod
    tok = await orders_mod._acquire_order_op_lock(o["id"], "cargo_label", ttl_s=60)   # worker is mid-flight
    assert tok
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"shipping_address": {**SHIP, "address": "Yarış Sk."}},
                           headers=sup)
    assert r.status_code == 409
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 409 and mng.create_calls == []
    await orders_mod._release_order_op_lock(o["id"], "cargo_label", tok)
    # two concurrent label requests (double click / bulk + single) → ONE MNG shipment
    r1, r2 = await asyncio.gather(pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup),
                                  pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup))
    assert sorted([r1.status_code, r2.status_code]) in ([200, 409], [200, 200]), (r1.text, r2.text)
    assert len(mng.create_calls) == 1
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["cargo_barcode_number"] == "MNGSELF1" and not cur.get("cargo_label_lock_id")
    # replay after success → existing label returned, no MNG call
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200 and "zaten" in r.json()["message"] and len(mng.create_calls) == 1
    # an expired lock (crashed worker) does not block forever
    await pay_set("orders", {"id": o["id"]}, {"cargo_label_lock_until": now_iso(minutes=-1),
                                              "cargo_label_lock_id": "dead"})
    assert (await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)).status_code == 200
    # lock fields cannot be forged through the generic update endpoint
    r = await pay_http.put(f"/api/orders/{o['id']}", json={"cargo_label_lock_until": "2999-01-01",
                                                           "coupon_reservations": ["x"]}, headers=sup)
    assert set(r.json()["ignored_fields"]) >= {"cargo_label_lock_until", "coupon_reservations"}


async def test_lost_local_label_is_relinked_from_mng_not_duplicated(pay_http, pay_env, mng):
    _, sup = await pay_staff(super_admin=True)
    # (1) previous attempt reached MNG (attempt stamp written) but the result was never stored
    o = await _paid_order(pay_http, pay_env)
    await pay_set("orders", {"id": o["id"]}, {"cargo_label_attempted_at": now_iso(minutes=-5)})
    mng.lookup = {"ok": True, "mng_siparis_no": "MNGLOST1", "gonderi_no": "NZ777", "kargo_statu": "1"}
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200, r.text
    assert mng.create_calls == []                                       # no second MNG record
    assert mng.lookup_calls and mng.lookup_calls[0]["siparis_no"] == o["order_number"]
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["cargo_barcode_number"] == "MNGLOST1" and cur["cargo_tracking_number"] == "NZ777"
    # (2) even the attempt stamp was lost: MNG refuses the duplicate siparis_no (E005) → lookup → link
    o2 = await _paid_order(pay_http, pay_env, pid="p2")
    mng.create_calls.clear()
    mng.lookup_calls.clear()
    mng.create_result = {"ok": False, "hata": "E005 BU SIPARIS NO ILE KAYIT ZATEN VAR"}
    mng.lookup = {"ok": True, "mng_siparis_no": "MNGLOST2", "gonderi_no": "", "kargo_statu": "0"}
    r = await pay_http.post(f"/api/orders/{o2['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200, r.text
    assert len(mng.create_calls) == 1
    cur = await pay_find("orders", {"id": o2["id"]})
    assert cur["cargo_barcode_number"] == "MNGLOST2"
    assert not await pay_findall("cargo_logs", {"order_id": o2["id"], "status": "error"})
    # (3) a stamped order with NOTHING at MNG creates the shipment normally (lookup miss)
    o3 = await _paid_order(pay_http, pay_env, pid="p3")
    await pay_set("orders", {"id": o3["id"]}, {"cargo_label_attempted_at": now_iso(minutes=-5)})
    mng.create_calls.clear()
    mng.create_result = {"ok": True, "barkod": "MNGNEW3", "gonderi_no": ""}
    mng.lookup = {"ok": False, "error": "kayıt yok"}
    r = await pay_http.post(f"/api/orders/{o3['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200 and len(mng.create_calls) == 1
    assert (await pay_find("orders", {"id": o3["id"]}))["cargo_barcode_number"] == "MNGNEW3"


async def test_cargo_label_payload_is_minimised(pay_http, pay_env, mng):
    member, mhdr = await pay_customer(email="member.private@example.test")
    o = await _paid_order(pay_http, pay_env, headers=mhdr)
    from security.pii import seal_doc_tax_ids
    sealed = seal_doc_tax_ids({"billing_info": {"is_corporate": False, "tax_number": "10000000146"}})
    await pay_set("orders", {"id": o["id"]}, {"billing_info": sealed["billing_info"],
                                              "customer_ip": "198.51.100.77", "user_agent": "UA-SECRET",
                                              "notes": "kapı kodu 1234", "gift_note": "Seni seviyorum"})
    _, sup = await pay_staff(super_admin=True)
    r = await pay_http.post(f"/api/orders/{o['id']}/cargo-barcode", headers=sup)
    assert r.status_code == 200, r.text
    assert len(mng.create_calls) == 1
    kw = mng.create_calls[0]
    allowed = {"username", "password", "siparis_no", "irsaliye_no", "kiymet", "icerik", "hizmet_sekli",
               "teslim_sekli", "al_sms", "gn_sms", "parca_list", "alici_ad", "odeme_sekli", "adres_farkli",
               "il", "ilce", "adres", "tel_cep", "email", "kapida_odeme", "platform_adi", "platform_kodu"}
    assert set(kw) <= allowed, set(kw) - allowed
    assert kw["email"] == ""                                       # e-mail is NOT shared with the carrier
    assert kw["kiymet"] == pytest.approx(o["total"])              # declared value only
    assert kw["odeme_sekli"] == "P" and kw["kapida_odeme"] == 0
    blob = repr(kw) + repr(mng.nz_calls) + repr(mng.lookup_calls)
    cur = await pay_find("orders", {"id": o["id"]})
    forbidden = [SHIP["email"], member["email"], "10000000146", "198.51.100.77", "UA-SECRET", "kapı kodu",
                 "Seni seviyorum", member["id"], str(cur.get("iyzico_payment_id") or "iyz-none"),
                 "credit_card", "5528790000000008", "552879", "0008"]
    for f in forbidden:
        assert f not in blob, f
    # the stored cargo log keeps only a minimal request summary
    log = (await pay_findall("cargo_logs", {"order_id": o["id"]}))[0]
    assert set(log["request_summary"]) <= {"siparis_no", "alici_ad", "il", "ilce"}


# ═════════════════════════════════════ COM10 ═════════════════════════════════════════════════
async def _delivered(http, env, headers, pid="p1"):
    o = await _paid_order(http, env, pid=pid, headers=headers)
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered", "delivered_at": now_iso(days=-1)})
    return await pay_find("orders", {"id": o["id"]})


async def test_concurrent_return_requests_for_same_item_yield_one_return(pay_http, pay_env):
    _, mhdr = await pay_customer(email="ret@example.test")
    o = await _delivered(pay_http, pay_env, mhdr)
    body = {"items": [0], "reason": "Beden uymadi"}
    rs = await asyncio.gather(*[pay_http.post(f"/api/orders/{o['id']}/return-request", json=body, headers=mhdr)
                                for _ in range(4)])
    await pay_drain()
    assert all(r.status_code in (200, 409) for r in rs), [r.text for r in rs]
    assert any(r.status_code == 200 for r in rs)
    recs = await pay_findall("customer_returns", {"order_id": o["id"]})
    assert len(recs) == 1 and sum(int(i["quantity"]) for i in recs[0]["items"]) == 1
    assert len([c for c in pay_env.outbox.mng if str(c.get("siparis_no", "")).startswith("IW")]) == 1
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "return_requested" and cur["payment_status"] == "paid"
    assert not cur.get("return_req_lock_id")


async def test_panel_return_and_customer_return_cannot_exceed_quantity(pay_http, pay_env):
    _, mhdr = await pay_customer(email="ret2@example.test")
    o = await _delivered(pay_http, pay_env, mhdr)
    _, sup = await pay_staff(super_admin=True)
    r_cust, r_admin = await asyncio.gather(
        pay_http.post(f"/api/orders/{o['id']}/return-request", json={"items": [0], "reason": "x"}, headers=mhdr),
        pay_http.post(f"/api/orders/{o['id']}/admin-return", json={"item_indexes": [0], "reason": "panel"},
                      headers=sup))
    await pay_drain()
    codes = sorted([r_cust.status_code, r_admin.status_code])
    assert codes in ([200, 400], [200, 409]), (r_cust.text, r_admin.text)
    recs = await pay_findall("customer_returns", {"order_id": o["id"]})
    live = [r for r in recs if r["status"] not in ("cancelled", "expired", "rejected")]
    assert sum(int(i["quantity"]) for r in live for i in r["items"]) == 1
    # sequential panel return after an approved customer return is refused as well
    if r_cust.status_code == 200:
        await pay_set("customer_returns", {"id": live[0]["id"]}, {"status": "approved"})
    r = await pay_http.post(f"/api/orders/{o['id']}/admin-return", json={"item_indexes": [0]}, headers=sup)
    assert r.status_code == 400
    assert len(await pay_findall("customer_returns", {"order_id": o["id"]})) == len(recs)
