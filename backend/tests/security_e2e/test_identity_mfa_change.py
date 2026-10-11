"""Binding / changing an MFA factor requires re-authentication and revokes other sessions."""
from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from id_harness import (ID_PASSWORD, id_seed_user, id_auth, id_find, id_findall, id_token, id_decode,
                        id_update, id_seed_role)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

SETUP = "/api/auth/mfa/setup"
ENABLE = "/api/auth/mfa/enable"


def _stale(user):
    return id_token(user, auth_time=deps.now_ts() - 3600)


def test_setup_without_password_or_recent_auth_is_refused(id_client):
    cust = id_seed_user(kind="customer")
    stale = _stale(cust)
    assert id_client.post(SETUP, json={}, headers=id_auth(stale)).status_code == 403
    assert id_client.post(SETUP, json={"current_password": "nope-nope-1"}, headers=id_auth(stale)).status_code == 403
    assert id_client.post(SETUP, json={}).status_code == 401  # no session at all
    assert not id_find("users", {"id": cust["id"]}).get("mfa_pending_secret_enc")
    # legacy token without auth_time is never "recent"
    legacy = deps.create_token(cust["id"], False, token_version=0)
    assert id_client.post(SETUP, json={}, headers=id_auth(legacy)).status_code == 403


def test_setup_with_password_then_enable_bumps_token_version(id_client, id_outbox):
    cust = id_seed_user(kind="customer")
    stale = _stale(cust)
    other_device = _stale(cust)
    r = id_client.post(SETUP, json={"current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    doc = id_find("users", {"id": cust["id"]})
    assert doc["mfa_pending_secret_enc"] and secret not in repr(doc)  # stored encrypted
    r = id_client.post(ENABLE, json={"code": pyotp.TOTP(secret).now()}, headers=id_auth(stale))
    assert r.status_code == 200, r.text
    new_tok = r.json()["token"]
    assert id_decode(new_tok)["tv"] == 1
    assert id_find("users", {"id": cust["id"]})["token_version"] == 1
    # every pre-existing session is now dead; the returned token works
    for old in (stale, other_device):
        assert id_client.get("/api/auth/me", headers=id_auth(old)).status_code == 401
    assert id_client.get("/api/auth/me", headers=id_auth(new_tok)).status_code == 200
    assert id_findall("auth_audit_logs", {"event": "mfa_enable_totp", "success": True})
    assert any(e["to"] == cust["email"] for e in id_outbox.emails), "owner must be notified of factor change"


def test_recent_auth_allows_setup_without_password(id_client):
    cust = id_seed_user(kind="customer")
    r = id_client.post(SETUP, json={}, headers=id_auth(id_token(cust)))
    assert r.status_code == 200, r.text


def test_staff_factor_change_revokes_old_admin_token(id_client):
    id_seed_role("r_view", ["orders.view"])
    staff = id_seed_user(kind="staff", role_id="r_view")
    old = id_token(staff)
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(old)).status_code == 200
    secret = id_client.post(SETUP, json={}, headers=id_auth(old)).json()["secret"]
    r = id_client.post(ENABLE, json={"code": pyotp.TOTP(secret).now()}, headers=id_auth(old))
    assert r.status_code == 200, r.text
    new_tok = r.json()["token"]
    assert id_decode(new_tok)["typ"] == "staff"
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(old)).status_code == 401
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(new_tok)).status_code == 200


def test_enrolled_totp_needs_current_code_to_rebind(id_client):
    from security.crypto import encrypt
    secret = pyotp.random_base32()
    cust = id_seed_user(kind="customer", mfa_enabled=True, mfa_method="totp", mfa_secret_enc=encrypt(secret))
    stale = _stale(cust)
    r = id_client.post(SETUP, json={"current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 403
    r = id_client.post(SETUP, json={"current_password": ID_PASSWORD, "current_code": pyotp.TOTP(secret).now()},
                       headers=id_auth(stale))
    assert r.status_code == 200, r.text


def test_sms_enrolled_account_must_relogin(id_client):
    cust = id_seed_user(kind="customer", phone="905551117777", mfa_enabled=True, mfa_method="sms")
    stale = _stale(cust)
    r = id_client.post(SETUP, json={"current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 403
    assert "yeniden giriş" in r.json()["detail"]
    assert id_client.post(SETUP, json={}, headers=id_auth(id_token(cust))).status_code == 200


def test_enable_with_stale_pending_setup_requires_reauth(id_client):
    cust = id_seed_user(kind="customer")
    secret = id_client.post(SETUP, json={}, headers=id_auth(id_token(cust))).json()["secret"]
    id_update("users", {"id": cust["id"]},
              {"$set": {"mfa_pending_secret_at": (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()}})
    stale = _stale(cust)
    r = id_client.post(ENABLE, json={"code": pyotp.TOTP(secret).now()}, headers=id_auth(stale))
    assert r.status_code == 403
    assert not id_find("users", {"id": cust["id"]}).get("mfa_enabled")
    r = id_client.post(ENABLE, json={"code": pyotp.TOTP(secret).now(), "current_password": ID_PASSWORD},
                       headers=id_auth(stale))
    assert r.status_code == 200, r.text


def test_setup_sms_and_channel_change_require_reauth(id_client, id_outbox):
    cust = id_seed_user(kind="customer", phone="905551118888")
    stale = _stale(cust)
    r = id_client.post("/api/auth/mfa/setup-sms", json={"phone": "05551119999"}, headers=id_auth(stale))
    assert r.status_code == 403
    assert not id_outbox.sms()
    r = id_client.post("/api/auth/mfa/channel", json={"channel": "email"}, headers=id_auth(stale))
    assert r.status_code == 403
    r = id_client.post("/api/auth/mfa/channel", json={"channel": "email", "current_password": ID_PASSWORD},
                       headers=id_auth(stale))
    assert r.status_code == 200
    # admin-managed MFA phone cannot be changed by the user at all
    id_update("users", {"id": cust["id"]}, {"$set": {"mfa_admin_managed": True}})
    r = id_client.post("/api/auth/mfa/setup-sms", json={"phone": "05551119999"}, headers=id_auth(id_token(cust)))
    assert r.status_code == 403


def test_disable_requires_reauth_and_code(id_client):
    from security.crypto import encrypt
    secret = pyotp.random_base32()
    cust = id_seed_user(kind="customer", mfa_enabled=True, mfa_method="totp", mfa_secret_enc=encrypt(secret))
    stale = _stale(cust)
    r = id_client.post("/api/auth/mfa/disable", json={"code": pyotp.TOTP(secret).now()}, headers=id_auth(stale))
    assert r.status_code == 403
    assert id_find("users", {"id": cust["id"]})["mfa_enabled"] is True
    r = id_client.post("/api/auth/mfa/disable", json={"code": pyotp.TOTP(secret).now(),
                                                       "current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 200, r.text
    assert id_client.get("/api/auth/me", headers=id_auth(stale)).status_code == 401
    assert id_client.get("/api/auth/me", headers=id_auth(r.json()["token"])).status_code == 200
