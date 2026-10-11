"""BL-040: refund IBAN at-rest encryption + permission-scoped reveal (with audit)."""
from __future__ import annotations

from kv_harness import kv_find, kv_findall, kv_count, kv_order, kv_iso, kv_json, tr_iban
from conftest import KvUsers
from permissions import upgrade_grants_for

IBAN = tr_iban("0001000123456789012345")
HOLDER = "Zeynep Kaya"


def _bank_transfer_order(cust):
    return kv_order(cust, payment_method="bank_transfer", status="delivered", payment_status="paid",
                    delivered_at=kv_iso(-2), created_at=kv_iso(-6), invoice_number="", invoice_issued=False)


def _open_return(kv_client, cust, order):
    r = kv_client.post(f"/api/orders/{order['id']}/return-request", headers=KvUsers.hdr(cust), json={
        "reason": "Beden uymadi", "refund_iban": " ".join([IBAN[i:i + 4] for i in range(0, 26, 4)]),
        "refund_name": HOLDER, "refund_bank": "Test Bank"})
    assert r.status_code == 200, r.text
    assert IBAN not in r.text
    return r.json()["return"]["id"]


def test_return_request_stores_only_encrypted_iban(kv_client, kv_users):
    cust = kv_users.customer()
    order = _bank_transfer_order(cust)
    rid = _open_return(kv_client, cust, order)

    rec = kv_find("customer_returns", {"id": rid})
    info = rec["refund_bank_info"]
    assert info["enc"] == 1 and info["iban_enc"] and info["name_enc"] and info["iban_last4"] == IBAN[-4:]
    assert "iban" not in info and "name" not in info
    assert IBAN not in kv_json(rec) and HOLDER not in kv_json(rec)
    od = kv_find("orders", {"id": order["id"]})
    rr = od["return_request"]["refund_bank_info"]
    assert rr["enc"] == 1 and "iban" not in rr
    assert IBAN not in kv_json(od) and HOLDER not in kv_json(od)
    # decrypts back to the real value (the ciphertext is not garbage)
    from routes.orders import open_refund_bank_info
    assert open_refund_bank_info(info)["iban"] == IBAN


def test_invalid_iban_rejected(kv_client, kv_users):
    cust = kv_users.customer()
    order = _bank_transfer_order(cust)
    bad = IBAN[:-1] + ("0" if IBAN[-1] != "0" else "1")
    r = kv_client.post(f"/api/orders/{order['id']}/return-request", headers=KvUsers.hdr(cust),
                       json={"reason": "x", "refund_iban": bad, "refund_name": HOLDER})
    assert r.status_code == 400
    assert kv_count("customer_returns") == 0


def test_returns_approver_sees_masked_only(kv_client, kv_users):
    cust = kv_users.customer()
    _open_return(kv_client, cust, _bank_transfer_order(cust))
    approver = kv_users.staff(perms=["returns.approve", "returns.view"])
    r = kv_client.get("/api/orders/returns/admin/list", headers=KvUsers.hdr(approver))
    assert r.status_code == 200, r.text
    assert IBAN not in r.text and HOLDER not in r.text
    info = r.json()["returns"][0]["refund_bank_info"]
    assert info["masked"] is True and info["iban"].startswith("TR**") and info["iban"].endswith(
        f"{IBAN[-4:-2]} {IBAN[-2:]}")
    assert kv_count("auth_audit_logs", {"event": "refund_iban_view"}) == 0


def test_refund_payer_sees_full_iban_and_view_is_audited(kv_client, kv_users):
    cust = kv_users.customer()
    _open_return(kv_client, cust, _bank_transfer_order(cust))
    # IAM-02: rol, açılıştaki tek seferlik izin geçişinden geçmiş hâliyle (permissions.upgrade_grants_for).
    payer = kv_users.staff(perms=["returns.refund_pay"] + upgrade_grants_for(["returns.refund_pay"]))
    r = kv_client.get("/api/orders/returns/admin/list", headers=KvUsers.hdr(payer))
    assert r.status_code == 200, r.text
    info = r.json()["returns"][0]["refund_bank_info"]
    assert info["iban"] == IBAN and info["name"] == HOLDER and not info.get("masked")
    logs = kv_findall("auth_audit_logs", {"event": "refund_iban_view"})
    assert len(logs) == 1 and logs[0]["user_id"] == payer["id"] and logs[0]["meta"]["count"] >= 1
    assert IBAN not in kv_json(logs)  # the audit record itself does not contain the IBAN


def test_customer_sees_masked_iban_only(kv_client, kv_users):
    cust = kv_users.customer()
    order = _bank_transfer_order(cust)
    _open_return(kv_client, cust, order)
    r = kv_client.get("/api/my-orders", headers=KvUsers.hdr(cust))
    assert r.status_code == 200, r.text
    assert IBAN not in r.text and HOLDER not in r.text
    o = [x for x in r.json()["orders"] if x["id"] == order["id"]][0]
    info = o["return_request"]["refund_bank_info"]
    assert info["masked"] is True and info["iban"].startswith("TR**")
    r2 = kv_client.get(f"/api/orders/{order['id']}/return", headers=KvUsers.hdr(cust))
    assert r2.status_code == 200 and IBAN not in r2.text
    # another customer cannot read it at all
    other = kv_users.customer()
    assert kv_client.get(f"/api/orders/{order['id']}/return", headers=KvUsers.hdr(other)).status_code == 403
    assert kv_count("auth_audit_logs", {"event": "refund_iban_view"}) == 0
