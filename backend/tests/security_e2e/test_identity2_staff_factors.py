"""Staff factor takeover protections.

T23/AUTH16: a super admin changing ANOTHER staff member's password / MFA phone, or clearing that phone (=
disabling SMS MFA), goes through two-person approval when ≥2 active super admins exist; with a single super
admin it is applied immediately + alert + single_admin_exception audit (same rule as IBAN changes).
AUTH24/CHAIN03: after a recovery-code login, high-risk actions are refused for 24h unless a NEW factor was
enrolled since; the chain recovery → new factor → IBAN change still needs the second approver.
AUTH06/CHAIN02: the same valid code used concurrently (admin MFA SMS code, recovery code, invite) → exactly
one success.
"""
import asyncio
from datetime import timedelta

import pyotp
import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_auth, id_ip, id_find, id_findall, id_token,
                        id_audit_actions, id_run, id_update)
from routes import deps
from test_identity2_common import id2_async, id2_hdr, id2_yield_shim, id2_no_ip_limit

pytestmark = pytest.mark.usefixtures("id_env")

USERS = "/api/admin/users"
NEW_PW = "Baska!Personel-Sifre9"


@pytest.fixture
def staff_target():
    id_seed_role("r_ops2", ["orders.view"], "Operasyon")
    return id_seed_user(kind="staff", role_id="r_ops2", email="target-staff@example.test", phone="05551230001",
                        mfa_enabled=True, mfa_method="sms")


def _single_admin_audit(target_id):
    return [r for r in id_findall("audit_logs")
            if r.get("action") == "rbac.user_update" and r.get("entity", {}).get("id") == target_id]


# ─────────────────────────────── T23 / AUTH16 ───────────────────────────────

@pytest.mark.parametrize("change", ["password", "phone", "clear_phone"])
def test_other_staff_factor_change_needs_second_super_admin(id_client, staff_target, change, id_outbox):
    sa1 = id_seed_user(kind="super", email="sa-a@example.test")
    sa2 = id_seed_user(kind="super", email="sa-b@example.test")
    target_tok = id_token(staff_target)
    body = {"password": NEW_PW} if change == "password" else {"phone": "05559998877" if change == "phone" else ""}
    before = id_find("users", {"id": staff_target["id"]})
    r = id_client.put(f"{USERS}/{staff_target['id']}", json=body, headers=id_auth(id_token(sa1)))
    assert r.status_code == 202, r.text
    appr = r.json()["approval"]
    assert "params" not in appr and NEW_PW not in r.text
    # NOT applied yet: same password hash, same phone, target session alive
    now = id_find("users", {"id": staff_target["id"]})
    assert now["password"] == before["password"] and now.get("phone") == before.get("phone")
    assert now.get("mfa_phone_enc") == before.get("mfa_phone_enc") and now["token_version"] == 0
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(target_tok)).status_code == 200
    stored = id_find("pending_approvals", {"id": appr["id"]})
    assert NEW_PW not in repr(stored)  # plaintext password never stored (hash only)
    assert id_findall("alerts", {"kind": "approval_requested"})
    # the requester cannot approve; the other super admin can
    assert id_client.post(f"/api/admin/approvals/{appr['id']}/approve", headers=id_auth(id_token(sa1))).status_code == 403
    r = id_client.post(f"/api/admin/approvals/{appr['id']}/approve", headers=id_auth(id_token(sa2)))
    assert r.status_code == 200, r.text
    after = id_find("users", {"id": staff_target["id"]})
    assert after["token_version"] == 1
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(target_tok)).status_code == 401
    if change == "password":
        assert after["password"] != before["password"]
    elif change == "phone":
        assert after["phone"].endswith("5559998877") and after["mfa_admin_managed"] is True
    else:
        assert not after.get("mfa_phone_enc") and after["mfa_admin_managed"] is False
    recs = _single_admin_audit(staff_target["id"])
    assert recs and recs[0]["actor"]["id"] == sa2["id"] and recs[0]["metadata"].get("approval_id") == appr["id"]
    assert not recs[0]["metadata"].get("single_admin_exception")
    if change != "password":
        assert id_findall("alerts", {"kind": "mfa_reset_by_admin"})


def test_single_super_admin_applies_immediately_with_exception_audit(id_client, staff_target):
    sa = id_seed_user(kind="super", email="only-sa@example.test")
    r = id_client.put(f"{USERS}/{staff_target['id']}", json={"phone": "05559998866", "password": NEW_PW},
                      headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    u = id_find("users", {"id": staff_target["id"]})
    assert u["phone"].endswith("5559998866") and u["token_version"] == 1
    recs = _single_admin_audit(staff_target["id"])
    assert recs and all(rec["metadata"].get("single_admin_exception") is True for rec in recs
                        if rec.get("phase") == "intent")
    alerts = id_findall("alerts", {"kind": "single_admin_exception"})
    assert alerts, "single super admin exception must alert"
    assert id_findall("auth_audit_logs", {"event": "panel_user_credentials_change"})


def test_pending_codes_of_target_die_when_admin_changes_phone(id_client, staff_target, id_outbox):
    sa = id_seed_user(kind="super", email="only-sa2@example.test")
    login = id_client.post("/api/auth/login", json={"email": staff_target["email"], "password": ID_PASSWORD},
                           headers=id_ip("198.51.100.190"))
    assert login.status_code == 200 and login.json()["mfa_required"]
    assert id_find("mfa_sms_codes", {"user_id": staff_target["id"], "used": False})
    r = id_client.put(f"{USERS}/{staff_target['id']}", json={"phone": "05559998855"}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200
    rec = id_find("mfa_sms_codes", {"user_id": staff_target["id"]})
    assert rec["used"] is True and rec["invalidated"] == "factor_changed"


def test_own_phone_change_is_not_gated_but_needs_password(id_client):
    sa1 = id_seed_user(kind="super", email="self-a@example.test", phone="05551112200")
    id_seed_user(kind="super", email="self-b@example.test")
    r = id_client.put(f"{USERS}/{sa1['id']}", json={"phone": "05551112201"}, headers=id_auth(id_token(sa1)))
    assert r.status_code == 403
    r = id_client.put(f"{USERS}/{sa1['id']}", json={"phone": "05551112201", "current_password": ID_PASSWORD},
                      headers=id_auth(id_token(sa1)))
    assert r.status_code == 200, r.text


# ─────────────────────────────── AUTH24 / CHAIN03 ───────────────────────────────

def _recovery_login(client, admin, ip="198.51.100.191"):
    r = client.post("/api/auth/mfa/recovery-codes", json={}, headers=id_auth(id_token(admin)))
    assert r.status_code == 200, r.text
    codes = r.json()["codes"]
    login = client.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD}, headers=id_ip(ip))
    r = client.post("/api/auth/mfa/verify", json={"mfa_token": login.json()["mfa_token"], "recovery_code": codes[0]},
                    headers=id_ip(ip))
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _high_risk_calls(client, tok, sa_other_id):
    h = id_auth(tok)
    out = {}
    out["iban"] = client.post("/api/settings/bank-accounts", json={"bank_name": "B", "iban": "TR330006100519786457841326"},
                              headers=h).status_code
    out["role_create"] = client.post("/api/admin/roles", json={"name": "x", "permissions": ["orders.view"]},
                                     headers=h).status_code
    out["user_update"] = client.put(f"{USERS}/{sa_other_id}", json={"role_id": ""}, headers=h).status_code
    out["vault"] = client.get("/api/admin/vault/secret/k1/reveal", headers=h).status_code
    out["export"] = client.get("/api/admin/members/export.xlsx", headers=h).status_code
    out["switch"] = client.post("/api/admin/security-switches/freeze_refunds", json={"on": True, "reason": "test 123"},
                                headers=h).status_code
    out["mfa_disable"] = client.post("/api/auth/mfa/disable", json={"code": "000000"}, headers=h).status_code
    return out


def test_recovery_login_blocks_high_risk_actions_for_24h(id_client, id_outbox):
    from security.crypto import encrypt
    admin = id_seed_user(kind="super", email="recover@example.test", phone="05551113300", mfa_enabled=True,
                         mfa_method="totp", mfa_secret_enc=encrypt(pyotp.random_base32()))
    other = id_seed_user(kind="staff", role_id="r_none", email="other@example.test")
    id_run(deps.db.vault_secrets.insert_one({"key": "k1", "value_enc": encrypt("s3cret-value-1")}))
    tok = _recovery_login(id_client, admin)
    assert id_find("users", {"id": admin["id"]})["mfa_recovery_login_at"]
    res = _high_risk_calls(id_client, tok, other["id"])
    assert all(v == 403 for v in res.values()), res
    assert not id_find("settings", {"id": "payment"}) and not id_findall("roles", {"name": "x"})
    assert not (id_find("settings", {"id": "security_switches"}) or {}).get("switches")
    fails = id_findall("auth_audit_logs", {"event": "recovery_restricted_action"})
    assert {f["meta"]["action"] for f in fails} >= {"settings.bank_account", "rbac.role_create", "vault.reveal",
                                                     "export", "security.switch_toggle", "mfa_disable"}
    # a token refresh (logout-all) does not lift the restriction
    fresh = id_client.post("/api/auth/logout-all", headers=id_auth(tok)).json()["token"]
    assert id_client.get("/api/admin/vault/secret/k1/reveal", headers=id_auth(fresh)).status_code == 403
    # ordinary work keeps working
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(fresh)).status_code == 200
    # after 24h the restriction lapses
    with freeze_time(deps.datetime.now(deps.timezone.utc) + timedelta(hours=24, minutes=1)):
        t2 = id_token(id_find("users", {"id": admin["id"]}))
        r = id_client.get("/api/admin/vault/secret/k1/reveal", headers=id_auth(t2))
        assert r.status_code == 200 and r.json()["value"] == "s3cret-value-1"


def test_new_factor_lifts_restriction_but_iban_still_needs_second_approver(id_client, id_outbox):
    """CHAIN03: recovery → enrol a new factor → IBAN change: restriction lifted, two-person rule still holds."""
    from security.crypto import encrypt
    admin = id_seed_user(kind="super", email="chain03@example.test", mfa_enabled=True, mfa_method="totp",
                         mfa_secret_enc=encrypt(pyotp.random_base32()))
    sa2 = id_seed_user(kind="super", email="chain03-b@example.test")
    tok = _recovery_login(id_client, admin)
    body = {"bank_name": "Yeni", "iban": "TR330006100519786457841326", "account_holder": "Shop", "is_default": True}
    assert id_client.post("/api/settings/bank-accounts", json=body, headers=id_auth(tok)).status_code == 403
    # enrol a NEW authenticator (recent auth from the recovery login)
    r = id_client.post("/api/auth/mfa/setup", json={}, headers=id_auth(tok))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = id_client.post("/api/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=id_auth(tok))
    assert r.status_code == 200, r.text
    tok2 = r.json()["token"]
    u = id_find("users", {"id": admin["id"]})
    assert u["mfa_factor_enrolled_at"] > u["mfa_recovery_login_at"]
    # restriction gone, but the IBAN change still waits for the OTHER super admin
    r = id_client.post("/api/settings/bank-accounts", json=body, headers=id_auth(tok2))
    assert r.status_code == 202, r.text
    assert not (id_find("settings", {"id": "payment"}) or {}).get("bank_accounts_pending")
    aid = r.json()["approval"]["id"]
    assert id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(tok2)).status_code == 403
    assert id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(id_token(sa2))).status_code == 200
    assert id_find("settings", {"id": "payment"})["bank_accounts_pending"]


def test_recovery_restricted_admin_cannot_approve(id_client):
    from security.crypto import encrypt
    sa1 = id_seed_user(kind="super", email="req@example.test")
    admin = id_seed_user(kind="super", email="approver-rc@example.test", mfa_enabled=True, mfa_method="totp",
                         mfa_secret_enc=encrypt(pyotp.random_base32()))
    tok = _recovery_login(id_client, admin)
    r = id_client.post("/api/settings/bank-accounts", json={"bank_name": "B", "iban": "TR330006100519786457841326"},
                       headers=id_auth(id_token(sa1)))
    assert r.status_code == 202
    aid = r.json()["approval"]["id"]
    assert id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(tok)).status_code == 403
    assert id_find("pending_approvals", {"id": aid})["status"] == "pending"


# ─────────────────────────────── AUTH06 / CHAIN02 (staff codes) ───────────────────────────────

def test_concurrent_admin_mfa_code_single_session(id_outbox, monkeypatch):
    id2_yield_shim(monkeypatch)
    id2_no_ip_limit(monkeypatch)
    admin = id_seed_user(kind="super", phone="05551113311", email="conc-mfa@example.test")

    async def scenario(c):
        login = await c.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD})
        ticket = login.json()["mfa_token"]
        code = id_outbox.last_code("sms")
        rs = await asyncio.gather(*[c.post("/api/auth/mfa/verify", json={"mfa_token": ticket, "code": code})
                                    for _ in range(5)])
        assert sorted(r.status_code for r in rs) == [200, 401, 401, 401, 401], [r.text for r in rs]
    id2_async(scenario)
    assert len(id_findall("auth_audit_logs", {"event": "mfa_verify", "success": True})) == 1


def test_concurrent_recovery_code_single_session(id_client, id_outbox, monkeypatch):
    admin = id_seed_user(kind="super", phone="05551113322", email="conc-rc@example.test", mfa_enabled=True,
                         mfa_method="sms")
    codes = id_client.post("/api/auth/mfa/recovery-codes", json={}, headers=id_auth(id_token(admin))).json()["codes"]
    id2_yield_shim(monkeypatch)
    id2_no_ip_limit(monkeypatch)

    async def scenario(c):
        login = await c.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD})
        ticket = login.json()["mfa_token"]
        rs = await asyncio.gather(*[c.post("/api/auth/mfa/verify", json={"mfa_token": ticket, "recovery_code": codes[2]})
                                    for _ in range(4)])
        assert sorted(r.status_code for r in rs) == [200, 401, 401, 401], [r.text for r in rs]
    id2_async(scenario)
    rc = id_find("users", {"id": admin["id"]})["mfa_recovery_codes"]
    assert sum(1 for c in rc if c["used"]) == 1


def test_concurrent_invite_accept_single_account(id_client, id_outbox, monkeypatch):
    import re
    id_seed_role("r_inv", ["orders.view"], "Ops")
    sa = id_seed_user(kind="super", email="inviter2@example.test")
    r = id_client.post("/api/admin/users/invite", json={"email": "conc-new@example.test", "role_id": "r_inv"},
                       headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    tok = re.search(r"/admin/davet#([A-Za-z0-9_\-]{20,})", id_outbox.emails[-1]["html"]).group(1)
    secret = id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=id_ip("198.51.100.192")).json()["secret"]
    id2_yield_shim(monkeypatch)
    id2_no_ip_limit(monkeypatch)

    async def scenario(c):
        code = pyotp.TOTP(secret).now()
        rs = await asyncio.gather(*[c.post("/api/auth/invite/accept",
                                           json={"token": tok, "password": f"Davetli!Guclu-{i}x9", "code": code})
                                    for i in range(3)])
        oks = [r for r in rs if r.status_code == 200]
        assert len(oks) == 1, [(r.status_code, r.text[:120]) for r in rs]
        assert all(r.status_code in (400, 409, 410) for r in rs if r not in oks)
    id2_async(scenario)
    assert len(id_findall("users", {"email": "conc-new@example.test"})) == 1
    assert id_find("staff_invites", {"email": "conc-new@example.test"})["status"] == "accepted"
