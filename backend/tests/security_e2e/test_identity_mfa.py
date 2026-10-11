"""Admin MFA: challenge, single-use codes, rate limits, daily cap, channel policy, fail-closed."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_ip, id_auth, id_find, id_findall, id_run, id_decode,
                        id_update)

pytestmark = pytest.mark.usefixtures("id_env")

LOGIN = "/api/auth/login"
VERIFY = "/api/auth/mfa/verify"
SEND = "/api/auth/mfa/send"


def _admin(**kw):
    kw.setdefault("phone", "905551112233")
    return id_seed_user(kind="super", **kw)


def _login(client, user, ip="198.51.100.20"):
    return client.post(LOGIN, json={"email": user["email"], "password": ID_PASSWORD}, headers=id_ip(ip))


def _challenge(client, user, ip="198.51.100.20"):
    r = _login(client, user, ip)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mfa_required") is True, body
    assert "token" not in body
    return body


def test_admin_login_requires_mfa_and_sms_code_is_single_use(id_client, id_outbox):
    admin = _admin()
    body = _challenge(id_client, admin)
    assert body["mfa_channel"] == "sms" and body["phone_masked"] == "***33"
    assert id_outbox.sms("5551112233"), "SMS code must go through the (mocked) SMS sender"
    code = id_outbox.last_code("sms")
    # the pending token is NOT a session
    assert id_client.get("/api/auth/me", headers=id_auth(body["mfa_token"])).status_code == 401
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(body["mfa_token"])).status_code == 401
    # wrong code
    bad = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": "000000" if code != "000000" else "111111"},
                         headers=id_ip("198.51.100.20"))
    assert bad.status_code == 401
    ok = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": code}, headers=id_ip("198.51.100.20"))
    assert ok.status_code == 200, ok.text
    tok = ok.json()["token"]
    claims = id_decode(tok)
    assert claims["typ"] == "staff" and claims["user_id"] == admin["id"] and claims.get("auth_time")
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(tok)).status_code == 200
    # the same code can never be used twice
    again = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": code}, headers=id_ip("198.51.100.20"))
    assert again.status_code == 401
    assert "token" not in again.json()
    rec = id_find("mfa_sms_codes", {"user_id": admin["id"], "channel": "sms"})
    assert rec["used"] is True and rec["code_hash"] != code  # stored hashed only
    assert id_findall("auth_audit_logs", {"event": "mfa_verify", "success": True})


def test_mfa_verify_ip_rate_limit(id_client):
    a1, a2 = _admin(email="ratelimit-a@example.test"), _admin(email="ratelimit-b@example.test", phone="905551112244")
    t1 = _challenge(id_client, a1, ip="198.51.100.30")["mfa_token"]
    t2 = _challenge(id_client, a2, ip="198.51.100.31")["mfa_token"]
    ip = "203.0.113.60"
    codes = [id_client.post(VERIFY, json={"mfa_token": t1, "code": "12345"}, headers=id_ip(ip)).status_code
             for _ in range(10)]
    assert codes == [401] * 10
    # 11th request from the same IP is cut off by the IP limiter, even for ANOTHER account
    r = id_client.post(VERIFY, json={"mfa_token": t2, "code": "12345"}, headers=id_ip(ip))
    assert r.status_code == 429
    assert "Rate limit" in r.text
    # a different IP still reaches the handler for the other account
    r = id_client.post(VERIFY, json={"mfa_token": t2, "code": "12345"}, headers=id_ip("203.0.113.61"))
    assert r.status_code == 401


def test_mfa_per_account_window_limit(id_client, id_outbox):
    admin = _admin()
    with freeze_time("2026-10-04 09:00:00", tick=False) as frozen:
        t = _challenge(id_client, admin)["mfa_token"]
        code = id_outbox.last_code()
        for i in range(10):  # spread over IPs: the per-ACCOUNT window applies regardless of IP
            r = id_client.post(VERIFY, json={"mfa_token": t, "code": "99999"}, headers=id_ip(f"192.0.2.{50 + i}"))
            assert r.status_code == 401
        r = id_client.post(VERIFY, json={"mfa_token": t, "code": code}, headers=id_ip("192.0.2.99"))
        assert r.status_code == 429, "correct code must be refused once the 5-minute window is exhausted"
        assert "token" not in r.json()
        frozen.tick(delta=timedelta(minutes=5, seconds=30))
        t = _challenge(id_client, admin, ip="192.0.2.98")["mfa_token"]
        r = id_client.post(VERIFY, json={"mfa_token": t, "code": id_outbox.last_code()}, headers=id_ip("192.0.2.98"))
        assert r.status_code == 200, r.text


def _burn_daily_fails(client, admin, frozen):
    for w in range(3):
        t = _challenge(client, admin, ip=f"192.0.2.{120 + w}")["mfa_token"]
        for i in range(10):
            r = client.post(VERIFY, json={"mfa_token": t, "code": "77777"}, headers=id_ip(f"192.0.{w + 3}.{i + 1}"))
            assert r.status_code == 401, (w, i, r.text)
        frozen.tick(delta=timedelta(minutes=6))


def test_mfa_daily_failure_cap(id_client, id_outbox):
    admin = _admin(mfa_enabled=True, mfa_method="sms")
    with freeze_time("2026-10-04 08:00:00", tick=False) as frozen:
        _burn_daily_fails(id_client, admin, frozen)
        assert id_find("users", {"id": admin["id"]})["mfa_day_fail_count"] == 30
        t = _challenge(id_client, admin, ip="192.0.2.150")["mfa_token"]
        r = id_client.post(VERIFY, json={"mfa_token": t, "code": id_outbox.last_code()}, headers=id_ip("192.0.2.150"))
        assert r.status_code == 429, r.text
        assert "24 saat" in r.json()["detail"]
        assert id_findall("auth_audit_logs", {"event": "mfa_verify", "meta.reason": "daily_cap"})
        # 24h later the cap is lifted
        frozen.tick(delta=timedelta(hours=24, minutes=1))
        t = _challenge(id_client, admin, ip="192.0.2.151")["mfa_token"]
        r = id_client.post(VERIFY, json={"mfa_token": t, "code": id_outbox.last_code()}, headers=id_ip("192.0.2.151"))
        assert r.status_code == 200, r.text


def test_mfa_daily_cap_lifted_by_break_glass(id_client, id_outbox, monkeypatch):
    admin = _admin(mfa_enabled=True, mfa_method="sms")
    with freeze_time("2026-10-04 08:00:00", tick=False) as frozen:
        _burn_daily_fails(id_client, admin, frozen)
        monkeypatch.setenv("ADMIN_MFA_ENFORCE", "off")
        t = _challenge(id_client, admin, ip="192.0.2.160")["mfa_token"]
        r = id_client.post(VERIFY, json={"mfa_token": t, "code": id_outbox.last_code()}, headers=id_ip("192.0.2.160"))
        assert r.status_code == 200, r.text


# ─────────────────────────────── channel policy ───────────────────────────────

def test_email_channel_blocked_when_phone_exists(id_client, id_outbox):
    from routes import mfa as M
    admin = _admin()
    body = _challenge(id_client, admin)
    assert body["mfa_channel"] == "sms"
    assert body["email_masked"] == ""  # e-mail button hidden for a phone admin
    assert not id_outbox.codes("email")
    r = id_client.post(SEND, json={"mfa_token": body["mfa_token"], "channel": "email"}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 403, r.text
    assert not id_outbox.codes("email")
    # a code that WAS delivered by e-mail is not accepted for this admin
    email_code = id_run(M._issue_mfa_code(id_find("users", {"id": admin["id"]}), channel="email"))
    r = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": email_code}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 401


def test_email_allowed_when_it_is_the_only_factor(id_client, id_outbox):
    admin = id_seed_user(kind="super")  # no phone, no TOTP
    body = _challenge(id_client, admin)
    assert body["mfa_channel"] == "email"
    code = id_outbox.last_code("email")
    r = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": code}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 200, r.text


def test_totp_admin_cannot_receive_code_by_email_or_sms(id_client, id_outbox):
    import pyotp
    from security.crypto import encrypt
    secret = pyotp.random_base32()
    admin = _admin(mfa_enabled=True, mfa_method="totp", mfa_secret_enc=encrypt(secret))
    body = _challenge(id_client, admin)
    assert body["mfa_method"] == "totp"
    assert not id_outbox.codes("sms") and not id_outbox.codes("email")
    for ch in ("email", "sms"):
        r = id_client.post(SEND, json={"mfa_token": body["mfa_token"], "channel": ch}, headers=id_ip("198.51.100.20"))
        assert r.status_code == 400, r.text
    ok = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(secret).now()},
                        headers=id_ip("198.51.100.20"))
    assert ok.status_code == 200, ok.text


def test_reset_channel_blocked_for_24h_after_password_reset(id_client, id_outbox):
    from routes import mfa as M
    admin = _admin(password_reset_channel="sms",
                   password_reset_at=datetime.now(timezone.utc).isoformat())
    body = _challenge(id_client, admin)
    # SMS reset in the last 24h → the login code must NOT go to SMS; e-mail is the remaining factor
    assert body["mfa_channel"] == "email", body
    assert not id_outbox.codes("sms")
    assert "SMS" in body["mfa_notice"]
    r = id_client.post(SEND, json={"mfa_token": body["mfa_token"], "channel": "sms"}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 403
    sms_code = id_run(M._issue_mfa_code(id_find("users", {"id": admin["id"]}), channel="sms"))
    r = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": sms_code}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 401, "a code sent over the reset channel must be rejected"
    # 25h later the restriction is gone
    id_update("users", {"id": admin["id"]},
              {"$set": {"password_reset_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()}})
    body2 = _challenge(id_client, admin, ip="198.51.100.21")
    assert body2["mfa_channel"] == "sms"


def test_degraded_single_factor_is_alerted(id_client, id_outbox):
    sa = id_seed_user(kind="super", email="owner-sa@example.test", phone="905551119999")
    admin = id_seed_user(kind="staff", role_id="r_ops", password_reset_channel="email",
                         password_reset_at=datetime.now(timezone.utc).isoformat())
    from id_harness import id_seed_role
    id_seed_role("r_ops", ["orders.view"])
    body = _challenge(id_client, admin)
    assert body["mfa_channel"] == "email"  # never zero factors
    assert "yöneticilere bildirildi" in body["mfa_notice"]
    assert id_findall("auth_audit_logs", {"event": "admin_mfa_degraded", "user_id": admin["id"]})
    assert any(e["to"] == sa["email"] for e in id_outbox.emails), id_outbox.emails


# ─────────────────────────────── fail-closed ───────────────────────────────

def test_mfa_check_exception_fails_closed(id_client, monkeypatch):
    from routes import mfa as M
    admin = _admin()

    def _boom(*a, **k):
        raise RuntimeError("policy store down")
    monkeypatch.setattr(M, "admin_mfa_plan", _boom)
    r = _login(id_client, admin)
    assert r.status_code == 503, r.text
    assert "token" not in r.json()
    assert id_findall("auth_audit_logs", {"event": "login", "meta.reason": "mfa_check_error"})


def test_mfa_check_exception_with_break_glass_allows_login(id_client, monkeypatch):
    from routes import mfa as M
    admin = _admin()

    async def _boom(*a, **k):
        raise RuntimeError("policy store down")
    monkeypatch.setattr(M, "admin_mfa_enforced", _boom)
    assert _login(id_client, admin).status_code == 503
    monkeypatch.setenv("ADMIN_MFA_ENFORCE", "off")
    r = _login(id_client, admin, ip="198.51.100.22")
    assert r.status_code == 200, r.text
    assert r.json()["token"] and not r.json().get("mfa_required")


def test_rule_read_failure_keeps_mfa_enforced(id_client, monkeypatch):
    import business_rules
    admin = _admin()

    async def _boom(*a, **k):
        raise RuntimeError("rules unavailable")
    monkeypatch.setattr(business_rules, "get_rule", _boom)
    body = _challenge(id_client, admin)
    assert body["mfa_required"] is True


def test_challenge_error_for_enrolled_user_fails_closed(id_client, monkeypatch):
    from routes import mfa as M
    admin = _admin(mfa_enabled=True, mfa_method="sms")

    def _boom(*a, **k):
        raise RuntimeError("signer down")
    monkeypatch.setattr(M, "create_mfa_pending_token", _boom)
    r = _login(id_client, admin)
    assert r.status_code == 503
    assert "token" not in r.json()


def test_mfa_disabled_rule_lets_admin_in_without_code(id_client):
    from id_harness import id_set_rule
    admin = _admin()
    id_set_rule("security.admin_mfa_required", False)
    r = _login(id_client, admin)
    assert r.status_code == 200 and r.json()["token"]


# ─────────────────────────────── pending MFA challenge vs. session revocation ───────────────────────────────

def test_password_change_kills_inflight_mfa_challenge(id_client, id_outbox):
    """An MFA challenge started with the OLD password must not survive a password change / logout-all
    (token_version bump): otherwise whoever holds the old password + intercepts one code still gets a
    fresh session after the owner has already rotated the password."""
    admin = _admin()
    body = _challenge(id_client, admin)
    code = id_outbox.last_code("sms")
    id_update("users", {"id": admin["id"]}, {"$inc": {"token_version": 1}})  # owner rotated password
    r = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": code}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 401, r.text
    assert "token" not in r.json()
    r = id_client.post(SEND, json={"mfa_token": body["mfa_token"], "channel": "sms"}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 401
    # a challenge started after the rotation works normally
    body2 = _challenge(id_client, admin, ip="198.51.100.23")
    r = id_client.post(VERIFY, json={"mfa_token": body2["mfa_token"], "code": id_outbox.last_code("sms")},
                       headers=id_ip("198.51.100.23"))
    assert r.status_code == 200, r.text


def test_global_revoke_kills_inflight_mfa_challenge(id_client, id_outbox):
    from routes import deps
    admin = _admin()
    body = _challenge(id_client, admin)
    code = id_outbox.last_code("sms")
    id_run(deps.bump_security_epoch())
    r = id_client.post(VERIFY, json={"mfa_token": body["mfa_token"], "code": code}, headers=id_ip("198.51.100.20"))
    assert r.status_code == 401, r.text
    body2 = _challenge(id_client, admin, ip="198.51.100.24")
    r = id_client.post(VERIFY, json={"mfa_token": body2["mfa_token"], "code": id_outbox.last_code("sms")},
                       headers=id_ip("198.51.100.24"))
    assert r.status_code == 200, r.text
