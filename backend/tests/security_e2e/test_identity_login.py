"""Login brute-force protection: wrong password, IP+account lock, staff progressive delay, IP ban."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_ip, id_find, id_findall, id_run, id_decode)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

LOGIN = "/api/auth/login"


def _login(client, email, password, ip):
    return client.post(LOGIN, json={"email": email, "password": password}, headers=id_ip(ip))


def test_wrong_password_401_and_correct_password_token(id_client):
    u = id_seed_user(kind="customer")
    r = _login(id_client, u["email"], "wrong-password-1", "198.51.100.10")
    assert r.status_code == 401, r.text
    assert "token" not in r.json()
    # unknown account gets the SAME answer (no enumeration)
    r2 = _login(id_client, "nobody@example.test", "wrong-password-1", "198.51.100.10")
    assert r2.status_code == 401 and r2.json()["detail"] == r.json()["detail"]
    ok = _login(id_client, u["email"], ID_PASSWORD, "198.51.100.10")
    assert ok.status_code == 200, ok.text
    claims = id_decode(ok.json()["token"])
    assert claims["user_id"] == u["id"] and claims["typ"] == "customer"
    fails = [r for r in id_findall("auth_audit_logs", {"event": "login", "success": False})]
    assert len(fails) == 2 and all(f["meta"]["reason"] == "bad_credentials" for f in fails)


def test_ip_account_lock_after_5_failures_other_ip_still_logs_in(id_client):
    u = id_seed_user(kind="customer")
    bad_ip, good_ip = "203.0.113.5", "198.51.100.77"
    for i in range(5):
        r = _login(id_client, u["email"], f"bad-{i}-password", bad_ip)
        assert r.status_code == 401, (i, r.text)
    # 6th attempt from the same IP — even with the CORRECT password — is locked
    r = _login(id_client, u["email"], ID_PASSWORD, bad_ip)
    assert r.status_code == 429, r.text
    assert "token" not in r.json()
    rec = id_find("login_ip_failures", {"key": f"{u['email']}|{bad_ip}"})
    assert rec["count"] == 5 and rec.get("locked_until")
    # the real owner on another IP is NOT locked out
    r = _login(id_client, u["email"], ID_PASSWORD, good_ip)
    assert r.status_code == 200, r.text
    assert r.json()["token"]
    # ...and that success did NOT clear the attacker IP's lock
    r = _login(id_client, u["email"], ID_PASSWORD, bad_ip)
    assert r.status_code == 429
    # no account-wide hard lock for 5 failures
    assert not id_find("users", {"id": u["id"]}).get("locked_until")


def _thirty_failures(client, email):
    # 6 different IPs x 5 failures = 30 account-wide failures (stays under the per-IP lock and rate limit)
    for n in range(6):
        for i in range(5):
            r = _login(client, email, f"bad-{n}-{i}-pw", f"192.0.2.{10 + n}")
            assert r.status_code == 401, (n, i, r.text)


def test_staff_not_hard_locked_progressive_delay(id_client):
    staff = id_seed_user(kind="super", phone="905551110001")
    with freeze_time("2026-10-04 10:00:00", tick=False) as frozen:
        _thirty_failures(id_client, staff["email"])
        doc = id_find("users", {"id": staff["id"]})
        assert doc["failed_attempts"] == 30
        assert not doc.get("locked_until"), "staff must never get a hard lock"
        # brand-new IP, correct password, immediately → short progressive delay (429 with seconds)
        r = _login(id_client, staff["email"], ID_PASSWORD, "198.51.100.200")
        assert r.status_code == 429, r.text
        assert "sn" in r.json()["detail"]
        # after the (<=60s) delay the owner gets through (MFA challenge for staff)
        frozen.tick(delta=timedelta(seconds=3))
        r = _login(id_client, staff["email"], ID_PASSWORD, "198.51.100.200")
        assert r.status_code == 200, r.text
        assert r.json().get("mfa_required") is True


def test_customer_hard_locked_after_30_account_failures(id_client):
    cust = id_seed_user(kind="customer")
    with freeze_time("2026-10-04 10:00:00", tick=False) as frozen:
        _thirty_failures(id_client, cust["email"])
        assert id_find("users", {"id": cust["id"]}).get("locked_until")
        frozen.tick(delta=timedelta(seconds=90))
        r = _login(id_client, cust["email"], ID_PASSWORD, "198.51.100.201")
        assert r.status_code == 429, r.text
        assert "dk" in r.json()["detail"]
        frozen.tick(delta=timedelta(minutes=16))
        r = _login(id_client, cust["email"], ID_PASSWORD, "198.51.100.201")
        assert r.status_code == 200, r.text


def test_ip_ban_after_threshold_blocks_ip_only(id_client):
    u = id_seed_user(kind="customer")
    ip = "203.0.113.99"
    now = datetime.now(timezone.utc)

    async def _seed():
        docs = [{"id": f"f{i}", "event": "login", "success": False, "ip": ip, "email": f"x{i}@example.test",
                 "meta": {"reason": "bad_credentials"}, "created_at": (now - timedelta(minutes=5)).isoformat()}
                for i in range(deps.IP_BLOCK_THRESHOLD - 1)]
        await deps.db.auth_audit_logs.insert_many(docs)
    id_run(_seed())
    # the THRESHOLD-th failure itself triggers the 24h ban (the current failure is logged before
    # the IP counter is evaluated — no off-by-one)
    r = _login(id_client, "someone-else0@example.test", "bad-password-x", ip)
    assert r.status_code == 401
    ban = id_find("ip_blocklist", {"ip": ip})
    assert ban and ban.get("auto_blocked") is True
    # banned IP: even correct credentials of an unrelated account are refused
    r = _login(id_client, u["email"], ID_PASSWORD, ip)
    assert r.status_code == 429, r.text
    assert "IP" in r.json()["detail"]
    assert id_findall("auth_audit_logs", {"ip": ip, "meta.reason": "ip_blocked"})
    # a different IP is unaffected
    r = _login(id_client, u["email"], ID_PASSWORD, "198.51.100.3")
    assert r.status_code == 200, r.text


def test_unknown_email_lockout_does_not_reveal_existence(id_client):
    """5 hatalı denemeden sonra KAYITLI ve KAYITSIZ e-postaya aynı yanıt (429) dönmeli."""
    u = id_seed_user(kind="customer")
    codes = {}
    for label, email, ip in (("known", u["email"], "203.0.113.171"),
                             ("unknown", "nobody-here@example.test", "203.0.113.172")):
        for _ in range(5):
            assert _login(id_client, email, "wrong-password-1", ip).status_code == 401
        codes[label] = _login(id_client, email, "wrong-password-1", ip).status_code
    assert codes["known"] == codes["unknown"] == 429, codes


def test_login_rate_limit_per_ip(id_client):
    u = id_seed_user(kind="customer")
    codes = [_login(id_client, f"nobody{i}@example.test", "bad-password-zz", "203.0.113.150").status_code
             for i in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    # other IPs keep their own budget
    assert _login(id_client, u["email"], ID_PASSWORD, "203.0.113.151").status_code == 200
