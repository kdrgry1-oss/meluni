"""RBAC on critical admin actions: a staff role WITHOUT the permission is refused (403), a super admin
is allowed; allowed critical actions leave an audit record, and (fail-closed) are NOT performed when
the audit store is unavailable."""
import pytest

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_auth, id_find, id_findall, id_token,
                        id_run, id_audit_actions, id_set_rule)
from routes import deps
from permissions import upgrade_grants_for

pytestmark = pytest.mark.usefixtures("id_env")

# A realistic "operations" role: lots of day-to-day rights, none of the critical ones under test.
OPS_PERMS = ["orders.view", "orders.edit", "customers.view", "customers.edit", "returns.view", "returns.approve",
             "products.view", "settings.site"]


@pytest.fixture
def actors():
    # IAM-02: rol, açılıştaki tek seferlik izin geçişinden geçmiş hâliyle (permissions.upgrade_grants_for).
    # Denetim #3: settings.edit hassas izin — otomatik verilmez; bu rolde süper admin bilinçli vermiş.
    id_seed_role("r_ops", OPS_PERMS + upgrade_grants_for(OPS_PERMS) + ["settings.edit"], "Operasyon")
    staff = id_seed_user(kind="staff", role_id="r_ops", email="ops@example.test")
    sa = id_seed_user(kind="super", email="owner@example.test")
    return {"staff": staff, "sa": sa, "staff_h": id_auth(id_token(staff)), "sa_h": id_auth(id_token(sa))}


def _seed(coll, doc):
    id_run(deps.db[coll].insert_one(dict(doc)))
    return doc


# ─────────────────────────────── denied for staff / allowed for super admin ───────────────────────────────

def test_bulk_status(id_client, actors):
    _seed("orders", {"id": "o-bulk-1", "order_number": "B1", "status": "preparing", "payment_method": "havale",
                     "payment_status": "paid", "total": 100, "items": []})
    r = id_client.post("/api/orders/bulk-status?status=shipped", json=["o-bulk-1"], headers=actors["staff_h"])
    assert r.status_code == 403
    assert id_find("orders", {"id": "o-bulk-1"})["status"] == "preparing"
    r = id_client.post("/api/orders/bulk-status?status=shipped", json=["o-bulk-1"], headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert r.json()["success_count"] == 1, r.json()
    assert id_find("orders", {"id": "o-bulk-1"})["status"] == "shipped"
    ev = id_findall("order_events", {"order_id": "o-bulk-1", "event_type": "status"})
    assert ev and ev[-1]["actor"] == "owner@example.test" and ev[-1]["meta"]["status"] == "shipped"
    # payment state untouched by a status change (CLAUDE.md invariant 1)
    assert id_find("orders", {"id": "o-bulk-1"})["payment_status"] == "paid"


def test_return_status_refunded(id_client, actors):
    _seed("orders", {"id": "o-ret-1", "order_number": "R1", "status": "returned", "items": []})
    _seed("customer_returns", {"id": "ret-1", "order_id": "o-ret-1", "order_number": "R1", "status": "received",
                               "items": []})
    # ops staff may approve returns but may NOT mark them as refunded (money out)
    r = id_client.post("/api/orders/returns/ret-1/status", json={"status": "refunded"}, headers=actors["staff_h"])
    assert r.status_code == 403
    assert id_find("customer_returns", {"id": "ret-1"})["status"] == "received"
    r = id_client.post("/api/orders/returns/ret-1/status", json={"status": "approved"}, headers=actors["staff_h"])
    assert r.status_code == 200  # within the role
    r = id_client.post("/api/orders/returns/ret-1/status", json={"status": "refunded"}, headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert id_find("customer_returns", {"id": "ret-1"})["status"] == "refunded"


def test_return_status_refunded_is_audited(id_client, actors):
    _seed("orders", {"id": "o-ret-2", "order_number": "R2", "status": "returned", "items": []})
    _seed("customer_returns", {"id": "ret-2", "order_id": "o-ret-2", "status": "received", "items": []})
    r = id_client.post("/api/orders/returns/ret-2/status", json={"status": "refunded"}, headers=actors["sa_h"])
    assert r.status_code == 200
    assert [a for a in id_audit_actions() if "return" in a] or \
        id_findall("auth_audit_logs", {"meta.return_id": "ret-2"})


def test_bank_account_create(id_client, actors):
    body = {"bank_name": "Test Bank", "iban": "TR330006100519786457841326", "account_holder": "Shop Ltd",
            "is_default": True}
    r = id_client.post("/api/settings/bank-accounts", json=body, headers=actors["staff_h"])
    assert r.status_code == 403
    assert not id_find("settings", {"id": "payment"})
    r = id_client.post("/api/settings/bank-accounts", json=body, headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    pend = id_find("settings", {"id": "payment"})["bank_accounts_pending"]
    assert pend and pend[0]["account"]["iban"] == body["iban"]
    acts = id_audit_actions("settings.bank_account_upsert")
    assert len(acts) == 2  # intent + done
    recs = [r for r in id_findall("audit_logs") if r.get("action") == "settings.bank_account_upsert"]
    assert recs[0]["diff"]  # what changed is recorded …
    assert "0519786457841326" not in repr(recs)  # … but never the full IBAN


def test_bank_account_create_fails_closed(id_client, actors, id_break_audit):
    id_break_audit()
    r = id_client.post("/api/settings/bank-accounts", json={"bank_name": "X", "iban": "TR330006100519786457841326"},
                       headers=actors["sa_h"])
    assert r.status_code == 503, r.text
    assert not (id_find("settings", {"id": "payment"}) or {}).get("bank_accounts_pending")


def test_members_export(id_client, actors):
    id_seed_user(kind="customer", email="member1@example.test")
    r = id_client.get("/api/admin/members/export.xlsx", headers=actors["staff_h"])
    assert r.status_code == 403
    r = id_client.get("/api/admin/members/export.xlsx", headers=actors["sa_h"])
    assert r.status_code == 200, r.text[:300]
    assert r.content[:2] == b"PK"  # xlsx
    assert id_findall("auth_audit_logs", {"event": "members_export", "user_id": actors["sa"]["id"]})
    assert "security.member_export" in id_audit_actions("security.")
    # kill switch freeze_exports closes it even for the super admin
    r = id_client.post("/api/admin/security-switches/freeze_exports", json={"on": True, "reason": "incident 42"},
                       headers=actors["sa_h"])
    assert r.status_code == 200
    assert id_client.get("/api/admin/members/export.xlsx", headers=actors["sa_h"]).status_code == 423


def test_members_export_fails_closed(id_client, actors, id_break_audit, monkeypatch):
    id_seed_user(kind="customer", email="member2@example.test")
    id_break_audit("audit_logs", "auth_audit_logs")
    r = id_client.get("/api/admin/members/export.xlsx", headers=actors["sa_h"])
    assert r.status_code == 503


def test_customer_credentials_change(id_client, actors):
    cust = id_seed_user(kind="customer", phone="905551234567")
    before = id_find("users", {"id": cust["id"]})
    r = id_client.put(f"/api/admin/members/{cust['id']}", json={"password": "Hijack-Pass-123"},
                      headers=actors["staff_h"])
    assert r.status_code == 403
    r = id_client.put(f"/api/admin/members/{cust['id']}", json={"phone": "905559999999"}, headers=actors["staff_h"])
    assert r.status_code == 403
    after = id_find("users", {"id": cust["id"]})
    assert after["password"] == before["password"] and after["phone"] == before["phone"]
    assert id_findall("auth_audit_logs", {"event": "member_credentials_change", "success": False})
    # harmless field edits stay within the ops role
    assert id_client.put(f"/api/admin/members/{cust['id']}", json={"notes": "vip"},
                         headers=actors["staff_h"]).status_code == 200
    # super admin may; the customer's sessions are revoked
    cust_tok = id_token(cust)
    r = id_client.put(f"/api/admin/members/{cust['id']}", json={"password": "Reset-Pass-1234"}, headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert id_find("users", {"id": cust["id"]})["token_version"] == 1
    assert id_client.get("/api/auth/me", headers=id_auth(cust_tok)).status_code == 401
    assert id_findall("auth_audit_logs", {"event": "member_credentials_change", "success": True})


def test_customer_credentials_change_fails_closed(id_client, actors, id_break_audit, monkeypatch):
    cust = id_seed_user(kind="customer")
    before = id_find("users", {"id": cust["id"]})["password"]
    id_break_audit("audit_logs", "auth_audit_logs")
    r = id_client.put(f"/api/admin/members/{cust['id']}", json={"password": "Reset-Pass-1234"}, headers=actors["sa_h"])
    assert r.status_code == 503
    assert id_find("users", {"id": cust["id"]})["password"] == before


def test_security_switches(id_client, actors):
    assert id_client.get("/api/admin/security-switches", headers=actors["staff_h"]).status_code == 403
    r = id_client.post("/api/admin/security-switches/freeze_refunds", json={"on": True, "reason": "fraud wave"},
                       headers=actors["staff_h"])
    assert r.status_code == 403
    assert not id_find("settings", {"id": "security_switches"})
    r = id_client.post("/api/admin/security-switches/freeze_refunds", json={"on": True, "reason": "fraud wave"},
                       headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert id_find("settings", {"id": "security_switches"})["switches"]["freeze_refunds"]["on"] is True
    assert "security.switch_toggle" in id_audit_actions("security.switch_toggle")
    assert id_findall("auth_audit_logs", {"event": "security_switch_toggle"})


def test_security_switches_fail_closed(id_client, actors, id_break_audit):
    id_break_audit()
    r = id_client.post("/api/admin/security-switches/close_checkout", json={"on": True, "reason": "test outage"},
                       headers=actors["sa_h"])
    assert r.status_code == 503, r.text
    doc = id_find("settings", {"id": "security_switches"}) or {}
    assert not ((doc.get("switches") or {}).get("close_checkout") or {}).get("on")


def test_chargebacks(id_client, actors):
    _seed("orders", {"id": "o-cb-1", "order_number": "C1", "status": "delivered", "payment_status": "paid",
                     "total": 250.0, "items": []})
    body = {"order_id": "o-cb-1", "amount": 100, "reason_code": "4837"}
    assert id_client.get("/api/admin/chargebacks", headers=actors["staff_h"]).status_code == 403
    assert id_client.post("/api/admin/chargebacks", json=body, headers=actors["staff_h"]).status_code == 403
    assert not id_findall("chargebacks")
    r = id_client.post("/api/admin/chargebacks", json=body, headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    o = id_find("orders", {"id": "o-cb-1"})
    assert o["chargeback_open"] is True and o["payment_status"] == "paid" and o["status"] == "delivered"
    assert id_findall("auth_audit_logs", {"event": "chargeback_create"})


def test_chargebacks_fail_closed(id_client, actors, id_break_audit, monkeypatch):
    _seed("orders", {"id": "o-cb-2", "order_number": "C2", "status": "delivered", "total": 250.0, "items": []})
    id_break_audit("audit_logs", "auth_audit_logs")
    r = id_client.post("/api/admin/chargebacks", json={"order_id": "o-cb-2", "amount": 10}, headers=actors["sa_h"])
    assert r.status_code == 503
    assert not id_findall("chargebacks")


def _kvkk_request():
    cust = id_seed_user(kind="customer", email="forget-me@example.test")
    _seed("kvkk_requests", {"id": "kv-1", "type": "delete", "user_id": cust["id"], "status": "processing",
                            "created_at": "2026-10-01T00:00:00+00:00"})
    return cust


def test_kvkk_admin(id_client, actors):
    cust = _kvkk_request()
    assert id_client.get("/api/admin/kvkk/requests", headers=actors["staff_h"]).status_code == 403
    assert id_client.post("/api/admin/kvkk/requests/kv-1/apply", json={}, headers=actors["staff_h"]).status_code == 403
    assert not id_find("users", {"id": cust["id"]}).get("kvkk_deleted")
    r = id_client.get("/api/admin/kvkk/requests", headers=actors["sa_h"])
    assert r.status_code == 200 and r.json()["total"] == 1
    r = id_client.post("/api/admin/kvkk/requests/kv-1/apply", json={}, headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert id_find("users", {"id": cust["id"]}).get("kvkk_deleted") is True
    assert "kvkk.delete_apply" in id_audit_actions("kvkk.")


def test_kvkk_apply_fails_closed(id_client, actors, id_break_audit):
    cust = _kvkk_request()
    id_break_audit()
    r = id_client.post("/api/admin/kvkk/requests/kv-1/apply", json={}, headers=actors["sa_h"])
    assert r.status_code == 503, r.text
    u = id_find("users", {"id": cust["id"]})
    assert not u.get("kvkk_deleted") and u["email"] == "forget-me@example.test"
    assert id_find("kvkk_requests", {"id": "kv-1"})["status"] == "processing"


def test_business_rules_security_keys(id_client, actors):
    r = id_client.put("/api/admin/business-rules", json={"values": {"security.alert_emails": "evil@example.test"}},
                      headers=actors["staff_h"])
    assert r.status_code == 403
    # a non-security rule is fine for the same staff member
    r = id_client.put("/api/admin/business-rules", json={"values": {"panel.auto_logout_minutes": 30}},
                      headers=actors["staff_h"])
    assert r.status_code == 200
    import business_rules
    business_rules.invalidate()
    vals = (id_find("settings", {"id": "business_rules"}) or {}).get("values") or {}
    assert "security.alert_emails" not in vals
    r = id_client.put("/api/admin/business-rules", json={"values": {"security.alert_emails": "sec@example.test"}},
                      headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert id_find("settings", {"id": "business_rules"})["values"]["security.alert_emails"] == "sec@example.test"
    assert "business_rules.security_update" in id_audit_actions("business_rules.")


def test_business_rules_security_keys_fail_closed(id_client, actors, id_break_audit):
    id_break_audit()
    r = id_client.put("/api/admin/business-rules", json={"values": {"security.alert_emails": "sec@example.test"}},
                      headers=actors["sa_h"])
    assert r.status_code == 503, r.text
    vals = (id_find("settings", {"id": "business_rules"}) or {}).get("values") or {}
    assert "security.alert_emails" not in vals


def test_vault_reveal(id_client, actors):
    r = id_client.post("/api/admin/vault/secret", json={"key": "resend_api", "value": "s3cr3t-value-123"},
                       headers=actors["staff_h"])
    assert r.status_code == 403
    r = id_client.post("/api/admin/vault/secret", json={"key": "resend_api", "value": "s3cr3t-value-123"},
                       headers=actors["sa_h"])
    assert r.status_code == 200, r.text
    assert "s3cr3t-value-123" not in repr(id_findall("vault_secrets"))
    r = id_client.get("/api/admin/vault/secret/resend_api/reveal", headers=actors["staff_h"])
    assert r.status_code == 403
    assert id_findall("auth_audit_logs", {"event": "vault_secret_reveal_denied"})
    listing = id_client.get("/api/admin/vault/secrets", headers=actors["staff_h"]).json()
    assert listing["can_reveal"] is False and "s3cr3t-value-123" not in repr(listing)
    r = id_client.get("/api/admin/vault/secret/resend_api/reveal", headers=actors["sa_h"])
    assert r.status_code == 200 and r.json()["value"] == "s3cr3t-value-123"
    assert id_audit_actions("vault.secret_reveal").count("vault.secret_reveal") == 2
    assert id_findall("alerts", {"kind": "vault_reveal"})
    assert "s3cr3t-value-123" not in repr(id_findall("audit_logs"))


def test_vault_reveal_fails_closed(id_client, actors, id_break_audit):
    from security.crypto import encrypt
    _seed("vault_secrets", {"key": "k1", "value_enc": encrypt("top-secret-zz"), "scope": "global"})
    id_break_audit()
    r = id_client.get("/api/admin/vault/secret/k1/reveal", headers=actors["sa_h"])
    assert r.status_code == 503
    assert "top-secret-zz" not in r.text


def test_role_with_star_permission_counts_as_super_admin_for_rbac(id_client):
    id_seed_role("r_star", ["*"])
    star = id_seed_user(kind="staff", role_id="r_star")
    h = id_auth(id_token(star))
    assert id_client.get("/api/admin/security-switches", headers=h).status_code == 200
    assert id_client.get("/api/admin/kvkk/requests", headers=h).status_code == 200


def test_staff_cannot_escalate_via_rbac_endpoints(id_client, actors):
    h = actors["staff_h"]
    assert id_client.post("/api/admin/roles", json={"name": "x", "permissions": ["*"]}, headers=h).status_code == 403
    assert id_client.put(f"/api/admin/users/{actors['staff']['id']}", json={"role_id": "super_admin"},
                         headers=h).status_code == 403
    assert id_client.post("/api/admin/users/invite", json={"email": "a@example.test", "role_id": "r_ops"},
                          headers=h).status_code == 403
    assert not id_findall("roles", {"permissions": "*"})
