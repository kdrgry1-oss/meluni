"""E2E for owner fixes without a pre-existing xfail test:
  * gift-card-only (C2) order sends the admin "Yeni Sipariş" mail exactly once;
  * legacy PLAINTEXT (data_b64) receipts are encrypted before going to the private archive;
  * coupon_redemptions duplicates are cleaned up and the (coupon_id, order_id) unique index created;
  * _is_site_order treats meluni/site/web/empty as site, marketplaces as not.
"""
import base64

import pytest

from pay_harness import (
    CARD_TOTAL, now_iso, pay_create_order, pay_drain, pay_find, pay_findall, pay_seed_product,
)
from routes import deps

pytestmark = pytest.mark.asyncio


async def test_gift_card_only_order_sends_admin_new_order_mail_once(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    await deps.db.gift_cards.insert_one({"code": "HED-ADM-0001-AAAA", "status": "active", "balance": 1000.0,
                                         "kind": "gift", "value_type": "amount", "transactions": []})
    r = await pay_create_order(pay_http, "credit_card", gift_card_code="hed-adm-0001-aaaa",
                               gift_card_amount=1, total=0.0)
    assert r.status_code == 200, r.text
    await pay_drain()
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["paid_with_gift_card_only"] is True and o["gift_card"]["amount"] == CARD_TOTAL
    assert len(pay_env.outbox.admin_order_mails(o["order_number"])) == 1
    assert o.get("admin_new_order_mail_sent") is True
    assert len(pay_env.outbox.events("order_confirmed")) == 1      # customer mail unchanged


async def test_card_order_still_sends_no_admin_mail_at_creation(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "credit_card")
    assert r.status_code == 200
    await pay_drain()
    assert pay_env.outbox.admin_order_mails() == []                 # invariant 3


async def test_legacy_plaintext_receipt_is_encrypted_in_archive(pay_env, pay_archive_on):
    import scheduler
    from security import crypto
    raw = b"%PDF-legacy-receipt"
    old = now_iso(days=-45)
    await deps.db.orders.insert_one({"id": "o-leg", "order_number": "W1", "payment_receipt": {
        "data_b64": base64.b64encode(raw).decode(), "uploaded_at": old, "filename": "d.pdf",
        "content_type": "application/pdf"}})
    await deps.db.payment_receipt_versions.insert_one({"id": "v-leg", "order_id": "o-leg", "created_at": old,
                                                       "receipt": {"data_b64": base64.b64encode(b"v1").decode(),
                                                                   "uploaded_at": old}})
    await scheduler._run_private_archive()
    rc = (await pay_find("orders", {"id": "o-leg"}))["payment_receipt"]
    assert rc["archive_enc_format"] == "fernet-bytes-v1" and "data_b64" not in rc
    stored = pay_archive_on[rc["archive_key"]][0]
    assert raw not in stored and crypto._fernet_dec.decrypt(stored) == raw
    v = (await pay_find("payment_receipt_versions", {"id": "v-leg"}))["receipt"]
    assert v["archive_enc_format"] == "fernet-bytes-v1"
    assert crypto._fernet_dec.decrypt(pay_archive_on[v["archive_key"]][0]) == b"v1"


async def test_coupon_redemption_duplicates_cleaned_then_unique_index(pay_env):
    from routes.coupons import ensure_coupon_redemption_unique_index, record_coupon_redemption_once
    await deps.db.coupon_redemptions.insert_many([
        {"coupon_id": "c1", "order_id": "o1", "n": 1}, {"coupon_id": "c1", "order_id": "o1", "n": 2},
        {"coupon_id": "c1", "order_id": "o2", "n": 3},
        # (one legacy row only: mongomock ignores partialFilterExpression when BUILDING the index;
        #  real MongoDB excludes non-string rows from the unique constraint)
        {"coupon_id": None, "legacy": True}])
    assert await ensure_coupon_redemption_unique_index() is True
    rows = await pay_findall("coupon_redemptions")
    assert sorted((r.get("coupon_id"), r.get("order_id"), r.get("n")) for r in rows if r.get("coupon_id")) == \
        [("c1", "o1", 1), ("c1", "o2", 3)]                       # oldest kept
    assert len([r for r in rows if r.get("legacy")]) == 1       # legacy rows are not deduped/deleted
    assert await record_coupon_redemption_once("c1", "o1", {"x": 1}) is False
    assert await record_coupon_redemption_once("c1", "o3", {"x": 1}) is True
    assert len(await pay_findall("coupon_redemptions", {"coupon_id": "c1"})) == 3


async def test_is_site_order_helper():
    from routes.orders import _is_site_order
    for p in ("meluni", "site", "web", "", None):
        assert _is_site_order({"platform": p}) is True, p
    for p in ("trendyol", "hepsiburada", "amazon", "temu", "n11", "ticimax"):
        assert _is_site_order({"platform": p}) is False, p
    assert _is_site_order({"platform": "", "marketplace": "trendyol"}) is False


# ═════════════════════ money-out return actions are audited FAIL-CLOSED ═════════════════════
async def _seed_return(oid="o-r", rid="ret-r", status="received", order_status="returned"):
    await deps.db.orders.insert_one({"id": oid, "order_number": "R" + oid, "status": order_status, "items": [],
                                     "total": 100.0, "payment_status": "paid"})
    await deps.db.customer_returns.insert_one({"id": rid, "order_id": oid, "order_number": "R" + oid,
                                               "status": status, "items": [], "refund_amount": 50.0})


async def _audit_actions(prefix):
    return [(a.get("action"), a.get("phase")) for a in await pay_findall("audit_logs")
            if str(a.get("action") or "").startswith(prefix)]


async def test_return_status_refunded_intent_done_and_fails_closed(pay_http, pay_env, id_break_audit):
    from pay_harness import pay_staff
    _, hdr = await pay_staff(super_admin=True)
    await _seed_return()
    r = await pay_http.post("/api/orders/returns/ret-r/status", json={"status": "refunded"}, headers=hdr)
    assert r.status_code == 200, r.text
    acts = await _audit_actions("returns.status_refunded")
    assert [p for _, p in acts] == ["intent", "done"]
    await _seed_return("o-r2", "ret-r2")
    id_break_audit()
    r = await pay_http.post("/api/orders/returns/ret-r2/status", json={"status": "refunded"}, headers=hdr)
    assert r.status_code == 503
    assert (await pay_find("customer_returns", {"id": "ret-r2"}))["status"] == "received"
    assert (await pay_find("orders", {"id": "o-r2"}))["status"] == "returned"


async def test_refund_pay_fails_closed_when_audit_store_down(pay_http, pay_env, id_break_audit):
    from pay_harness import pay_staff
    _, hdr = await pay_staff(super_admin=True)
    await _seed_return(status="approved", order_status="return_approved")
    id_break_audit()
    r = await pay_http.post("/api/orders/returns/ret-r/refund-pay", json={"method": "havale", "amount": 50},
                            headers=hdr)
    assert r.status_code == 503, r.text
    assert (await pay_find("customer_returns", {"id": "ret-r"}))["status"] == "approved"
    assert (await pay_find("orders", {"id": "o-r"}))["status"] == "return_approved"


async def test_bulk_mark_refunded_silent_fails_closed(pay_http, pay_env, id_break_audit):
    from pay_harness import pay_staff
    _, hdr = await pay_staff(super_admin=True)
    await _seed_return(order_status="return_approved")
    id_break_audit()
    r = await pay_http.post("/api/orders/returns/bulk-mark-refunded-silent", json={"order_ids": ["o-r"]},
                            headers=hdr)
    assert r.status_code == 503, r.text
    assert (await pay_find("orders", {"id": "o-r"}))["status"] == "return_approved"
