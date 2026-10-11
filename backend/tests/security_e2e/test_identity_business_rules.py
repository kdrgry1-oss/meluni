"""Security business rules: only super admins may change security.* keys; turning admin MFA off needs a
second super admin; the change is audited."""
import pytest

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_auth, id_ip, id_find, id_findall, id_token,
                        id_audit_actions)

pytestmark = pytest.mark.usefixtures("id_env")

RULES = "/api/admin/business-rules"


def _vals():
    import business_rules
    business_rules.invalidate()
    return (id_find("settings", {"id": "business_rules"}) or {}).get("values") or {}


def _admin_login_needs_mfa(client, admin, ip):
    r = client.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD}, headers=id_ip(ip))
    assert r.status_code == 200, r.text
    return bool(r.json().get("mfa_required"))


@pytest.mark.parametrize("key,value", [("security.alert_emails", "attacker@example.test"),
                                       ("security.alert_from", "attacker@example.test"),
                                       ("security.admin_mfa_required", False)])
def test_non_super_admin_cannot_change_security_rules(id_client, key, value):
    # even a role with every settings permission is not enough
    id_seed_role("r_settings", ["settings.site", "settings.payment", "settings.emails", "admin.business_rules"])
    staff = id_seed_user(kind="staff", role_id="r_settings")
    r = id_client.put(RULES, json={"values": {key: value}}, headers=id_auth(id_token(staff)))
    assert r.status_code == 403
    assert key not in _vals()
    # mixing in a harmless key does not smuggle the security key through
    r = id_client.put(RULES, json={"values": {key: value, "panel.auto_logout_minutes": 45}},
                      headers=id_auth(id_token(staff)))
    assert r.status_code == 403
    assert key not in _vals() and "panel.auto_logout_minutes" not in _vals()


def test_super_admin_changes_alert_emails_and_is_audited(id_client):
    sa = id_seed_user(kind="super")
    r = id_client.put(RULES, json={"values": {"security.alert_emails": "soc@example.test, ciso@example.test"}},
                      headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    assert _vals()["security.alert_emails"] == "soc@example.test, ciso@example.test"
    recs = [x for x in id_findall("audit_logs") if x.get("action") == "business_rules.security_update"]
    assert [x["phase"] for x in recs] == ["intent", "done"]
    assert recs[0]["actor"]["id"] == sa["id"] and recs[0]["entity"]["id"] == "security.alert_emails"
    # e-mail addresses are personal data → redacted in the audit diff
    assert "soc@example.test" not in repr(recs)


def test_disabling_admin_mfa_with_two_super_admins_needs_approval(id_client):
    sa1 = id_seed_user(kind="super", email="sa1@example.test", phone="905551112200")
    sa2 = id_seed_user(kind="super", email="sa2@example.test", phone="905551112211")
    r = id_client.put(RULES, json={"values": {"security.admin_mfa_required": False, "panel.auto_logout_minutes": 30}},
                      headers=id_auth(id_token(sa1)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_approval"] is True
    vals = _vals()
    assert "security.admin_mfa_required" not in vals  # NOT applied …
    assert vals["panel.auto_logout_minutes"] == 30    # … while the harmless key in the same request is saved
    assert _admin_login_needs_mfa(id_client, sa1, "198.51.100.110") is True
    aid = body["approval"]["id"]
    assert id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(id_token(sa1))).status_code == 403
    r = id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(id_token(sa2)))
    assert r.status_code == 200, r.text
    assert _vals()["security.admin_mfa_required"] is False
    assert "business_rules.security_patch" in id_audit_actions("business_rules.")
    assert _admin_login_needs_mfa(id_client, sa1, "198.51.100.111") is False


def test_enabling_admin_mfa_needs_no_approval(id_client):
    sa1 = id_seed_user(kind="super", email="sa1@example.test")
    id_seed_user(kind="super", email="sa2@example.test")
    r = id_client.put(RULES, json={"values": {"security.admin_mfa_required": True}}, headers=id_auth(id_token(sa1)))
    assert r.status_code == 200 and not r.json().get("pending_approval")
    assert _vals()["security.admin_mfa_required"] is True


def test_single_super_admin_disables_immediately_with_alert(id_client):
    sa = id_seed_user(kind="super")
    r = id_client.put(RULES, json={"values": {"security.admin_mfa_required": False}}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200 and not r.json().get("pending_approval")
    assert _vals()["security.admin_mfa_required"] is False
    assert id_findall("alerts", {"kind": "single_admin_exception"})


def test_approval_gate_error_fails_closed(id_client, monkeypatch):
    from security import approvals
    sa1 = id_seed_user(kind="super", email="sa1@example.test")
    id_seed_user(kind="super", email="sa2@example.test")

    async def _boom(*a, **k):
        raise RuntimeError("approval store unavailable")
    monkeypatch.setattr(approvals, "active_super_admins", _boom)
    r = id_client.put(RULES, json={"values": {"security.admin_mfa_required": False}}, headers=id_auth(id_token(sa1)))
    assert r.status_code == 503, r.text
    assert "security.admin_mfa_required" not in _vals()
