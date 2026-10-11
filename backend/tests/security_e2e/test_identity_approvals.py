"""Two-person approval for IBAN changes + IBAN cooling-off period."""
from datetime import timedelta

import pytest
from freezegun import freeze_time

from id_harness import (id_seed_user, id_seed_role, id_auth, id_find, id_findall, id_token, id_audit_actions,
                        id_run)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

BANKS = "/api/settings/bank-accounts"
IBAN = "TR330006100519786457841326"
BODY = {"bank_name": "Yeni Banka", "iban": IBAN, "account_holder": "Shop Ltd", "is_default": True}


def _stale(u):
    return id_token(u, auth_time=deps.now_ts() - 3600)


def _approve(client, approval_id, user, recent=True):
    tok = id_token(user) if recent else _stale(user)
    return client.post(f"/api/admin/approvals/{approval_id}/approve", headers=id_auth(tok))


def _pay():
    return id_find("settings", {"id": "payment"}) or {}


@pytest.fixture
def two_supers():
    return id_seed_user(kind="super", email="sa-one@example.test"), id_seed_user(kind="super", email="sa-two@example.test")


def test_iban_create_needs_second_super_admin(id_client, two_supers):
    sa1, sa2 = two_supers
    r = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(sa1)))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["pending_approval"] is True
    appr = body["approval"]
    assert "params" not in appr and IBAN not in r.text
    # NOT applied
    pay = _pay()
    assert not pay.get("bank_accounts") and not pay.get("bank_accounts_pending")
    assert id_find("pending_approvals", {"id": appr["id"]})["status"] == "pending"
    assert any(a.startswith("approval.request.settings.bank_account") for a in id_audit_actions("approval."))
    assert id_findall("alerts", {"kind": "approval_requested"})
    # requester cannot approve their own request
    r = _approve(id_client, appr["id"], sa1)
    assert r.status_code == 403
    # second super admin without a recent login is refused
    r = _approve(id_client, appr["id"], sa2, recent=False)
    assert r.status_code == 403
    assert id_find("pending_approvals", {"id": appr["id"]})["status"] == "pending"
    assert not _pay().get("bank_accounts_pending")
    # second super admin with recent auth → applied (into the cooling-off queue)
    r = _approve(id_client, appr["id"], sa2)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "executed"
    pend = _pay()["bank_accounts_pending"]
    assert len(pend) == 1 and pend[0]["account"]["iban"] == IBAN
    doc = id_find("pending_approvals", {"id": appr["id"]})
    assert doc["status"] == "executed" and doc["approved_by"]["id"] == sa2["id"]
    recs = [r for r in id_findall("audit_logs") if r.get("action") == "settings.bank_account_upsert"]
    assert recs and recs[0]["actor"]["id"] == sa2["id"] and recs[0]["metadata"].get("approval_id") == appr["id"]
    # cannot be executed twice
    assert _approve(id_client, appr["id"], sa2).status_code == 409


def test_non_super_staff_cannot_list_or_approve(id_client, two_supers):
    sa1, _ = two_supers
    id_seed_role("r_pay", ["settings.payment"])
    staff = id_seed_user(kind="staff", role_id="r_pay")
    # staff WITH settings.payment can request (it still goes through two-person approval) …
    r = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(staff)))
    assert r.status_code == 202, r.text
    aid = r.json()["approval"]["id"]
    # … but can neither see the approval queue nor approve
    assert id_client.get("/api/admin/approvals", headers=id_auth(id_token(staff))).status_code == 403
    assert _approve(id_client, aid, staff).status_code == 403
    assert not _pay().get("bank_accounts_pending")
    r = id_client.get("/api/admin/approvals", headers=id_auth(id_token(sa1)))
    assert r.status_code == 200 and r.json()["pending"] == 1 and r.json()["active_super_admins"] == 2


def test_rejected_request_is_not_applied(id_client, two_supers):
    sa1, sa2 = two_supers
    aid = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(sa1))).json()["approval"]["id"]
    r = id_client.post(f"/api/admin/approvals/{aid}/reject", json={"reason": "unknown IBAN"},
                       headers=id_auth(id_token(sa2)))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _approve(id_client, aid, sa2).status_code == 409
    assert not _pay().get("bank_accounts_pending")


def test_expired_approval_cannot_be_applied(id_client, two_supers):
    sa1, sa2 = two_supers
    with freeze_time("2026-10-01 09:00:00") as frozen:
        aid = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(sa1))).json()["approval"]["id"]
        frozen.tick(delta=timedelta(hours=24, minutes=5))
        r = _approve(id_client, aid, sa2)
        assert r.status_code == 409, r.text
    assert id_find("pending_approvals", {"id": aid})["status"] == "expired"
    assert not _pay().get("bank_accounts_pending") and not _pay().get("bank_accounts")


def test_single_super_admin_applies_immediately_with_exception_flag(id_client):
    sa = id_seed_user(kind="super")
    id_seed_user(kind="super", email="inactive-sa@example.test", is_active=False)  # does not count
    r = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    assert r.json()["cooling_off"] is True
    assert _pay()["bank_accounts_pending"][0]["account"]["iban"] == IBAN
    assert not id_findall("pending_approvals")
    recs = [r for r in id_findall("audit_logs") if r.get("action") == "settings.bank_account_upsert"]
    assert recs and all(r["metadata"].get("single_admin_exception") is True for r in recs)
    alerts = id_findall("alerts", {"kind": "single_admin_exception"})
    assert alerts and alerts[0]["level"] == "warning"
    assert id_findall("alerts", {"kind": "bank_account_change"})  # IBAN change alert to super admins


def test_iban_cooling_off_on_public_endpoint(id_client):
    sa = id_seed_user(kind="super")
    id_run(deps.db.settings.insert_one({"id": "payment", "bank_rev": 1, "bank_accounts": [
        {"id": "old", "bank_name": "Eski Banka", "iban": "TR000000000000000000000001", "account_holder": "Shop Ltd",
         "is_default": True}], "bank_accounts_pending": []}))
    with freeze_time("2026-10-02 10:00:00") as frozen:
        r = id_client.post(BANKS, json=BODY, headers=id_auth(id_token(sa)))
        assert r.status_code == 200 and r.json()["cooling_off"] is True
        pub = id_client.get("/api/settings/public/bank-default").json()["bank"]
        assert pub["id"] == "old" and pub["iban"] != IBAN
        frozen.tick(delta=timedelta(minutes=59))
        assert id_client.get("/api/settings/public/bank-default").json()["bank"]["id"] == "old"
        frozen.tick(delta=timedelta(minutes=2))
        pub = id_client.get("/api/settings/public/bank-default").json()["bank"]
        assert pub["iban"] == IBAN and pub["is_default"] is True
    pay = _pay()
    assert not pay["bank_accounts_pending"]
    assert [b["id"] for b in pay["bank_accounts"] if b.get("is_default")] == [pay["bank_accounts"][-1]["id"]]
    assert "settings.bank_account_activate" in id_audit_actions("settings.")
