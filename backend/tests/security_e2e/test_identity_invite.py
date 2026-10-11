"""Staff invite flow: hashed single-use token, password + MFA before any session, 48h expiry,
direct staff creation blocked."""
import hashlib
import re
from datetime import timedelta

import pyotp
import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_auth, id_ip, id_find, id_findall, id_token,
                        id_decode, id_audit_actions)

pytestmark = pytest.mark.usefixtures("id_env")

NEW_PW = "Davetli!Guclu-Sifre9"


@pytest.fixture
def sa():
    id_seed_role("r_ops", ["orders.view", "orders.edit"], "Operasyon")
    return id_seed_user(kind="super", email="inviter@example.test")


def _invite(client, sa, email="new.staff@example.test", **extra):
    r = client.post("/api/admin/users/invite", json={"email": email, "role_id": "r_ops", "first_name": "Ece", **extra},
                    headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    return r.json()["invite"]


def _token_from_mail(outbox, email):
    mails = [m for m in outbox.emails if m["to"] == email]
    assert mails, outbox.emails
    m = re.search(r"/admin/davet#([A-Za-z0-9_\-]{20,})", mails[-1]["html"])
    assert m, "invite link with token fragment expected"
    return m.group(1)


def test_invite_stores_only_hashed_token(id_client, sa, id_outbox):
    inv = _invite(id_client, sa)
    assert "token_hash" not in inv and "token" not in inv
    tok = _token_from_mail(id_outbox, "new.staff@example.test")
    doc = id_find("staff_invites", {"id": inv["id"]})
    assert doc["token_hash"] == hashlib.sha256(f"staffinv:{tok}".encode()).hexdigest()
    assert tok not in repr(id_findall("staff_invites")) and tok not in repr(id_findall("audit_logs"))
    assert not id_find("users", {"email": "new.staff@example.test"})  # no account before acceptance
    assert id_audit_actions("rbac.invite_create").count("rbac.invite_create") == 2
    r = id_client.post("/api/auth/invite/inspect", json={"token": tok}, headers=id_ip("198.51.100.70"))
    assert r.status_code == 200 and r.json()["email_masked"] == "n***@example.test"


def test_accept_requires_valid_token_and_mfa(id_client, sa, id_outbox):
    _invite(id_client, sa)
    tok = _token_from_mail(id_outbox, "new.staff@example.test")
    ip = id_ip("198.51.100.71")
    bad = "x" * 43
    assert id_client.post("/api/auth/invite/inspect", json={"token": bad}, headers=ip).status_code == 400
    r = id_client.post("/api/auth/invite/accept", json={"token": bad, "password": NEW_PW, "code": "123456"}, headers=ip)
    assert r.status_code == 400
    start = id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=ip)
    assert start.status_code == 200 and start.json()["method"] == "totp"
    secret = start.json()["secret"]
    # password alone (no MFA code) never yields a session
    r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": NEW_PW}, headers=ip)
    assert r.status_code == 400 and "token" not in r.json()
    # weak password refused
    r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": "short", "code": "1"}, headers=ip)
    assert r.status_code == 400
    # wrong MFA code → no account, attempt counted
    r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": NEW_PW, "code": "000000"}, headers=ip)
    assert r.status_code == 400
    assert not id_find("users", {"email": "new.staff@example.test"})
    assert id_find("staff_invites", {"email": "new.staff@example.test"})["attempts"] == 1
    # correct code → account + session
    r = id_client.post("/api/auth/invite/accept",
                       json={"token": tok, "password": NEW_PW, "code": pyotp.TOTP(secret).now()}, headers=ip)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["recovery_codes"]) == 10
    claims = id_decode(body["token"])
    assert claims["typ"] == "staff"
    u = id_find("users", {"email": "new.staff@example.test"})
    assert u["is_admin"] is True and u["role_id"] == "r_ops" and u["mfa_enabled"] is True
    assert u["mfa_method"] == "totp" and u["mfa_secret_enc"] and secret not in repr(u)
    assert not u.get("is_super_admin")
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(body["token"])).json()["permissions"] == \
        ["orders.view", "orders.edit"]
    inv = id_find("staff_invites", {"email": "new.staff@example.test"})
    assert inv["status"] == "accepted" and "token_hash" not in inv and "totp_secret_enc" not in inv
    assert "rbac.invite_accept" in id_audit_actions("rbac.")
    # single use: the same link is dead now
    r = id_client.post("/api/auth/invite/accept",
                       json={"token": tok, "password": NEW_PW, "code": pyotp.TOTP(secret).now()}, headers=ip)
    assert r.status_code in (400, 410)
    assert id_client.post("/api/auth/invite/inspect", json={"token": tok}, headers=ip).status_code in (400, 410)
    # later password login still requires the second factor
    r = id_client.post("/api/auth/login", json={"email": "new.staff@example.test", "password": NEW_PW},
                       headers=id_ip("198.51.100.72"))
    assert r.status_code == 200 and r.json()["mfa_required"] is True and "token" not in r.json()


def test_sms_invite_uses_admin_given_phone(id_client, sa, id_outbox):
    _invite(id_client, sa, email="sms.staff@example.test", phone="05551230000")
    tok = _token_from_mail(id_outbox, "sms.staff@example.test")
    ip = id_ip("198.51.100.73")
    r = id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=ip)
    assert r.status_code == 200 and r.json()["method"] == "sms" and r.json()["phone_masked"] == "***00"
    code = id_outbox.last_code("sms")
    assert id_outbox.sms("5551230000")
    r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": NEW_PW, "code": code}, headers=ip)
    assert r.status_code == 200, r.text
    u = id_find("users", {"email": "sms.staff@example.test"})
    assert u["mfa_method"] == "sms" and u["mfa_admin_managed"] is True and u["phone"].endswith("5551230000")


def test_invite_locked_after_10_wrong_codes(id_client, sa, id_outbox):
    _invite(id_client, sa)
    tok = _token_from_mail(id_outbox, "new.staff@example.test")
    id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=id_ip("198.51.100.74"))
    for i in range(10):
        r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": NEW_PW, "code": "000000"},
                           headers=id_ip(f"198.51.101.{i + 1}"))
        assert r.status_code == 400
    assert id_find("staff_invites", {"email": "new.staff@example.test"})["status"] == "locked"
    r = id_client.post("/api/auth/invite/inspect", json={"token": tok}, headers=id_ip("198.51.100.75"))
    assert r.status_code == 410


def test_invite_expires_after_48h(id_client, sa, id_outbox):
    with freeze_time("2026-09-01 08:00:00") as frozen:
        _invite(id_client, sa)
        tok = _token_from_mail(id_outbox, "new.staff@example.test")
        frozen.tick(delta=timedelta(hours=47))
        assert id_client.post("/api/auth/invite/inspect", json={"token": tok},
                              headers=id_ip("198.51.100.76")).status_code == 200
        frozen.tick(delta=timedelta(hours=1, minutes=5))
        r = id_client.post("/api/auth/invite/inspect", json={"token": tok}, headers=id_ip("198.51.100.76"))
        assert r.status_code == 410
        r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": NEW_PW, "code": "123456"},
                           headers=id_ip("198.51.100.76"))
        assert r.status_code == 410
    assert id_find("staff_invites", {"email": "new.staff@example.test"})["status"] == "expired"
    assert not id_find("users", {"email": "new.staff@example.test"})


def test_reinvite_supersedes_old_link(id_client, sa, id_outbox):
    _invite(id_client, sa)
    old = _token_from_mail(id_outbox, "new.staff@example.test")
    _invite(id_client, sa)
    new = _token_from_mail(id_outbox, "new.staff@example.test")
    assert old != new
    assert id_client.post("/api/auth/invite/inspect", json={"token": old}, headers=id_ip("198.51.100.77")).status_code == 410
    assert id_client.post("/api/auth/invite/inspect", json={"token": new}, headers=id_ip("198.51.100.77")).status_code == 200


def test_direct_staff_create_blocked_unless_env(id_client, sa, monkeypatch):
    body = {"email": "direct@example.test", "password": "Direkt!Sifre-2026x", "role_id": "r_ops",
            "first_name": "Can", "last_name": "Er"}
    r = id_client.post("/api/admin/users", json=body, headers=id_auth(id_token(sa)))
    assert r.status_code == 403
    assert not id_find("users", {"email": "direct@example.test"})
    monkeypatch.setenv("ALLOW_DIRECT_STAFF_CREATE", "1")
    r = id_client.post("/api/admin/users", json=body, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    u = id_find("users", {"email": "direct@example.test"})
    assert u["is_admin"] is True and u["role_id"] == "r_ops" and u["password"] != body["password"]
    assert id_audit_actions("rbac.user_create").count("rbac.user_create") == 2


def test_super_admin_invite_needs_second_approval(id_client, sa, id_outbox):
    id_seed_role("r_all", ["*"], "Her şey")
    sa2 = id_seed_user(kind="super", email="second@example.test")
    r = id_client.post("/api/admin/users/invite", json={"email": "boss@example.test", "role_id": "r_all"},
                       headers=id_auth(id_token(sa)))
    assert r.status_code == 202, r.text
    assert not id_findall("staff_invites")
    assert not [m for m in id_outbox.emails if m["to"] == "boss@example.test"]
    aid = r.json()["approval"]["id"]
    r = id_client.post(f"/api/admin/approvals/{aid}/approve", headers=id_auth(id_token(sa2)))
    assert r.status_code == 200, r.text
    assert id_find("staff_invites", {"email": "boss@example.test"})["status"] == "pending"
    assert _token_from_mail(id_outbox, "boss@example.test")
