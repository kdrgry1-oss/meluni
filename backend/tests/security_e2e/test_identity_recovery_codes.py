"""MFA recovery codes: re-auth to generate, single use, alerting, regeneration invalidates old set."""
import pyotp
import pytest

from id_harness import (ID_PASSWORD, id_seed_user, id_ip, id_auth, id_find, id_findall, id_token,
                        id_audit_actions)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

RC = "/api/auth/mfa/recovery-codes"


def _stale(user):
    # authenticated an hour ago → no "recent auth" proof in the token
    return id_token(user, auth_time=deps.now_ts() - 3600)


def _sms_admin():
    return id_seed_user(kind="super", phone="905551113344", mfa_enabled=True, mfa_method="sms")


def _generate(client, user):
    r = client.post(RC, json={}, headers=id_auth(id_token(user)))
    assert r.status_code == 200, r.text
    codes = r.json()["codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return codes


def _mfa_token(client, user, ip="198.51.100.40"):
    r = client.post("/api/auth/login", json={"email": user["email"], "password": ID_PASSWORD}, headers=id_ip(ip))
    assert r.status_code == 200 and r.json().get("mfa_required"), r.text
    return r.json()["mfa_token"]


def test_generation_requires_reauth(id_client):
    admin = _sms_admin()
    stale = _stale(admin)
    r = id_client.post(RC, json={}, headers=id_auth(stale))
    assert r.status_code == 403
    r = id_client.post(RC, json={"current_password": "wrong-password"}, headers=id_auth(stale))
    assert r.status_code == 403
    # SMS-MFA account: password alone is not enough → must re-login (recent auth)
    r = id_client.post(RC, json={"current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 403
    assert not id_find("users", {"id": admin["id"]}).get("mfa_recovery_codes")
    fails = id_findall("auth_audit_logs", {"event": "mfa_reauth", "success": False})
    assert any(f["meta"]["reason"] == "bad_password" for f in fails)
    # recent authentication → allowed, codes stored only as hashes
    codes = _generate(id_client, admin)
    stored = id_find("users", {"id": admin["id"]})["mfa_recovery_codes"]
    assert len(stored) == 10 and all(not c["used"] for c in stored)
    flat = repr(stored)
    assert not any(c.replace("-", "") in flat or c in flat for c in codes)
    assert id_audit_actions("mfa.recovery_codes_generate").count("mfa.recovery_codes_generate") == 2  # intent+done


def test_totp_account_can_reauth_with_password_and_current_code(id_client):
    from security.crypto import encrypt
    secret = pyotp.random_base32()
    admin = id_seed_user(kind="super", mfa_enabled=True, mfa_method="totp", mfa_secret_enc=encrypt(secret))
    stale = _stale(admin)
    r = id_client.post(RC, json={"current_password": ID_PASSWORD}, headers=id_auth(stale))
    assert r.status_code == 403  # factor proof missing
    r = id_client.post(RC, json={"current_password": ID_PASSWORD, "current_code": "000000"}, headers=id_auth(stale))
    assert r.status_code == 403
    r = id_client.post(RC, json={"current_password": ID_PASSWORD, "current_code": pyotp.TOTP(secret).now()},
                       headers=id_auth(stale))
    assert r.status_code == 200, r.text


def test_customer_cannot_generate(id_client):
    cust = id_seed_user(kind="customer")
    r = id_client.post(RC, json={}, headers=id_auth(id_token(cust)))
    assert r.status_code == 403


def test_recovery_code_logs_in_once_and_alerts(id_client, id_outbox):
    sa2 = id_seed_user(kind="super", email="second-sa@example.test")
    admin = _sms_admin()
    codes = _generate(id_client, admin)
    id_outbox.emails.clear()
    t = _mfa_token(id_client, admin)
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": t, "recovery_code": codes[3].lower()},
                       headers=id_ip("198.51.100.40"))
    assert r.status_code == 200, r.text
    assert r.json()["recovery_codes_remaining"] == 9
    tok = r.json()["token"]
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(tok)).status_code == 200
    # alert: chained security event + alert record + e-mail to the account owner
    assert "security.mfa_recovery_used" in id_audit_actions("security.")
    alerts = id_findall("alerts", {"kind": "mfa_recovery_used"})
    assert alerts and alerts[0]["level"] == "critical"
    assert any(e["to"] == admin["email"] and "kurtarma" in e["html"] for e in id_outbox.emails)
    assert id_findall("auth_audit_logs", {"event": "mfa_verify", "success": True, "meta.channel": "recovery_code"})
    # the same recovery code cannot be used again
    t2 = _mfa_token(id_client, admin, ip="198.51.100.41")
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": t2, "recovery_code": codes[3]},
                       headers=id_ip("198.51.100.41"))
    assert r.status_code == 401
    assert "token" not in r.json()
    # a different, unused one still works exactly once
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": t2, "recovery_code": codes[4]},
                       headers=id_ip("198.51.100.41"))
    assert r.status_code == 200 and r.json()["recovery_codes_remaining"] == 8
    assert sa2  # second super admin exists so the alert has a recipient


def test_regenerate_invalidates_old_codes(id_client):
    admin = _sms_admin()
    old = _generate(id_client, admin)
    new = _generate(id_client, admin)
    assert not set(old) & set(new)
    t = _mfa_token(id_client, admin)
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": t, "recovery_code": old[0]},
                       headers=id_ip("198.51.100.40"))
    assert r.status_code == 401
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": t, "recovery_code": new[0]},
                       headers=id_ip("198.51.100.40"))
    assert r.status_code == 200, r.text


def test_generation_fails_closed_when_audit_store_down(id_client, id_break_audit):
    admin = _sms_admin()
    tok = id_token(admin)
    id_break_audit()
    r = id_client.post(RC, json={}, headers=id_auth(tok))
    assert r.status_code == 503
    assert not id_find("users", {"id": admin["id"]}).get("mfa_recovery_codes")
