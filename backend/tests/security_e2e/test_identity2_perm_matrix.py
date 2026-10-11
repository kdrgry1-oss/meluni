"""IAM-01: role × critical-endpoint permission matrix.

For every critical admin endpoint:
  * DENIED — a staff member holding EVERY permission of the catalogue except the one the endpoint needs
    (for super-admin-only endpoints: every permission, but no '*') gets 403 and NOTHING changes in the
    database (all business collections compared before/after; only security logs may grow);
  * ALLOWED — a super admin succeeds (200/202) and the action is in the tamper-evident audit chain;
  * FAIL-CLOSED — with the audit store down, the allowed call returns 503 and again nothing changes
    (for endpoints marked fail_closed).
Endpoints already covered row-by-row in test_identity_rbac.py (bulk order status, return refund, chargebacks,
KVKK apply) are referenced there; everything else critical is here.
"""
import pytest

from id_harness import id_seed_user, id_seed_role, id_auth, id_findall, id_token, id_run, id_find
from routes import deps
from security.crypto import encrypt

pytestmark = pytest.mark.usefixtures("id_env")

_LOG_COLLS = {"auth_audit_logs", "audit_logs", "alerts", "staff_sessions", "staff_known_devices",
              "login_ip_failures", "pii_reveal_counters", "error_logs", "audit_outbox"}


def _all_perms():
    from permissions import ALL_PERMISSION_KEYS
    return sorted(set(ALL_PERMISSION_KEYS) | {"customers.export", "customers.credentials"})


def _state():
    async def _s():
        out = {}
        for name in sorted(await deps.db.list_collection_names()):
            if name in _LOG_COLLS:
                continue
            docs = await deps.db[name].find({}, {"_id": 0}).to_list(100000)
            if name == "settings":
                docs = [d for d in docs if d.get("id") not in ("audit_chain",)]
            if docs:
                out[name] = sorted(repr(sorted(d.items())) for d in docs)
        return out
    return id_run(_s())


@pytest.fixture
def world():
    id_seed_role("r_target", ["orders.view"], "Hedef")
    id_seed_role("r_target2", ["orders.view"], "Silinecek")
    sa = id_seed_user(kind="super", email="matrix-owner@example.test")
    staff2 = id_seed_user(kind="staff", role_id="r_target", email="staff2@example.test")
    staff3 = id_seed_user(kind="staff", role_id="r_target", email="staff3@example.test")
    cust = id_seed_user(kind="customer", email="cust-matrix@example.test", phone="05551110099",
                        locked_until="2099-01-01T00:00:00+00:00", failed_attempts=9)
    id_run(deps.db.vault_secrets.insert_one({"key": "k", "value_enc": encrypt("matrix-secret-1")}))
    id_run(deps.db.blocked_customers.insert_one({"id": "blk-1", "email": "old@example.test", "active": True}))
    id_run(deps.db.ip_blocklist.insert_one({"ip": "203.0.113.78", "permanent": True}))
    return {"sa": sa, "staff2": staff2, "staff3": staff3, "cust": cust}


# (id, method, path, body, required permission or None = super admin only, audit action, fail_closed)
MATRIX = [
    ("role_create", "POST", "/api/admin/roles", {"name": "Yeni", "permissions": ["orders.view"]}, None,
     "rbac.role_create", True),
    ("role_update", "PUT", "/api/admin/roles/r_target", {"name": "Hedef", "permissions": ["orders.view", "orders.edit"]},
     None, "rbac.role_update", True),
    ("role_delete", "DELETE", "/api/admin/roles/r_target2", None, None, "rbac.role_delete", True),
    ("user_role_change", "PUT", "/api/admin/users/{staff2}", {"role_id": "r_target2"}, None, "rbac.user_update", True),
    ("user_deactivate", "PUT", "/api/admin/users/{staff2}", {"is_active": False}, None, "rbac.user_update", True),
    ("user_password_other", "PUT", "/api/admin/users/{staff2}", {"password": "Matris!Sifre-2026x"}, None,
     "rbac.user_update", True),
    ("user_delete", "DELETE", "/api/admin/users/{staff3}", None, None, "rbac.user_delete", True),
    ("invite", "POST", "/api/admin/users/invite", {"email": "m-new@example.test", "role_id": "r_target"}, None,
     "rbac.invite_create", True),
    ("approvals_list", "GET", "/api/admin/approvals", None, None, None, False),
    ("ip_block", "POST", "/api/admin/security/ip-blocklist", {"ip": "203.0.113.77"}, None, "security.ip_block", True),
    ("ip_unblock", "DELETE", "/api/admin/security/ip-blocklist/203.0.113.78", None, None, "security.ip_unblock", True),
    ("unlock_user", "POST", "/api/admin/security/unlock-user", {"email": "cust-matrix@example.test"}, None,
     "security.unlock_user", True),
    ("revoke_all_sessions", "POST", "/api/admin/security/revoke-all-sessions", None, None,
     "security.revoke_all_sessions", True),
    ("audit_chain_export", "POST", "/api/admin/security/audit-chain/export", None, None, None, False),
    ("selftest_run", "POST", "/api/admin/security/selftest/run", None, None, None, False),
    ("kill_switch", "POST", "/api/admin/security-switches/freeze_refunds", {"on": True, "reason": "matrix 1"}, None,
     "security.switch_toggle", True),
    ("vault_write", "POST", "/api/admin/vault/secret", {"key": "m1", "value": "v-123456"}, None, "vault.secret_write", True),
    ("vault_reveal", "GET", "/api/admin/vault/secret/k/reveal", None, None, "vault.secret_reveal", True),
    ("vault_delete", "DELETE", "/api/admin/vault/secret/k", None, None, "vault.secret_delete", True),
    ("iban_change", "POST", "/api/settings/bank-accounts",
     {"bank_name": "M", "iban": "TR330006100519786457841326", "account_holder": "Shop"}, "settings.payment",
     "settings.bank_account_upsert", True),
    ("rules_security", "PUT", "/api/admin/business-rules", {"values": {"security.alert_emails": "a@example.test"}},
     None, "business_rules.security_update", True),
    ("rules_risk", "PUT", "/api/admin/business-rules", {"values": {"payment.allow_non3ds_card_pay": True}}, None,
     "business_rules.security_update", True),
    ("rules_sms_cap", "PUT", "/api/admin/business-rules", {"values": {"sms.global_hourly_cap": 1000}}, None,
     "business_rules.security_update", True),
    ("customer_credentials", "PUT", "/api/admin/members/{cust}", {"phone": "05552223344"}, "customers.credentials",
     "members.credentials_change", True),
    ("customer_block", "POST", "/api/customer-risk/block", {"email": "fraud@example.test"}, "customers.block",
     "customers.block", False),
    ("customer_unblock", "DELETE", "/api/customer-risk/blocked/blk-1", None, "customers.block", "customers.unblock",
     False),
    ("members_export", "GET", "/api/admin/members/export.xlsx", None, "customers.export", "security.member_export",
     False),
    ("consents_export", "GET", "/api/admin/consents/export.xlsx", None, "customers.export", None, False),
    ("kvkk_requests", "GET", "/api/admin/kvkk/requests", None, "admin.compliance", None, False),
]


def _call(client, method, path, body, w, headers):
    path = path.format(staff2=w["staff2"]["id"], staff3=w["staff3"]["id"], cust=w["cust"]["id"])
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    return client.request(method, path, **kw)


@pytest.mark.parametrize("case", MATRIX, ids=[m[0] for m in MATRIX])
def test_denied_for_everything_but_the_needed_permission(id_client, world, case):
    name, method, path, body, perm, _audit, _fc = case
    granted = [p for p in _all_perms() if p != perm]
    id_seed_role("r_almost_all", granted, "Neredeyse her şey")
    staff = id_seed_user(kind="staff", role_id="r_almost_all", email=f"almost-{name}@example.test")
    before = _state()
    r = _call(id_client, method, path, body, world, id_auth(id_token(staff)))
    assert r.status_code == 403, (name, r.status_code, r.text[:200])
    after = _state()
    # only the attacker's own seed row differs (users/roles inserted above, before the snapshot)
    assert after == before, name
    assert "matrix-secret-1" not in r.text


@pytest.mark.parametrize("case", MATRIX, ids=[m[0] for m in MATRIX])
def test_allowed_for_super_admin_and_audited(id_client, world, case):
    name, method, path, body, _perm, audit, _fc = case
    r = _call(id_client, method, path, body, world, id_auth(id_token(world["sa"])))
    assert r.status_code in (200, 202), (name, r.status_code, r.text[:300])
    if audit:
        acts = [a.get("action") for a in id_findall("audit_logs")]
        assert audit in acts, (name, acts)


FAIL_CLOSED = [m for m in MATRIX if m[6]]


@pytest.mark.parametrize("case", FAIL_CLOSED, ids=[m[0] for m in FAIL_CLOSED])
def test_fail_closed_when_audit_store_down(id_client, world, case, id_break_audit):
    name, method, path, body, _perm, _audit, _fc = case
    before = _state()
    tok = id_token(world["sa"])
    id_break_audit()
    r = _call(id_client, method, path, body, world, id_auth(tok))
    assert r.status_code == 503, (name, r.status_code, r.text[:200])
    assert _state() == before, name
    assert "matrix-secret-1" not in r.text
