"""Customer password reset (SMS OTP + e-mail link) and code binding — end to end.

AUTH02 (profile privilege fields ignored), AUTH06/CHAIN02 (concurrent single use), AUTH07/AUTH08 (purpose and
account binding), AUTH09 (target change invalidates code), AUTH10/AUTH19 (24h budgets, fail-closed store),
AUTH11 (expiry), AUTH12 (GET does not verify), AUTH13 (link domain), AUTH14 (identical responses + timing),
AUTH15 (reset revokes sessions and pending MFA tickets), AUTH20 (verification not carried to a new e-mail).
Real app, mongomock, every SMS/e-mail captured by id_outbox; nothing leaves the process.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, ID_NEW_PASSWORD, id_seed_user, id_auth, id_ip, id_find, id_findall, id_token,
                        id_update, id_run)
from routes import deps
from test_identity2_common import id2_async, id2_hdr, id2_yield_shim, id2_no_ip_limit

pytestmark = pytest.mark.usefixtures("id_env")

REQ = "/api/auth/forgot-password/request-otp"
VER = "/api/auth/forgot-password/verify-otp"
RESET = "/api/auth/forgot-password/reset"
EMAIL = "/api/auth/forgot-password/email"
PHONE = "05551234567"
PHONE_N = "905551234567"


@pytest.fixture(autouse=True)
def _no_ip_limits(monkeypatch):
    id2_no_ip_limit(monkeypatch)


def _codes(outbox, tail="5551234567"):
    return [str((n.get("variables") or {}).get("otp_code")) for n in outbox.notifications
            if n.get("event") == "password_reset_otp" and str(n.get("to_phone") or "").endswith(tail)]


def _wrong(code):
    return "000000" if code != "000000" else "111111"


# ─────────────────────────────── AUTH02 ───────────────────────────────

def test_profile_update_ignores_privilege_and_identity_fields(id_client):
    cust = id_seed_user(kind="customer", phone=PHONE, email_verified=False)
    tok = id_token(cust)
    evil = {"first_name": "Ayse", "is_admin": True, "is_super_admin": True, "role_id": "r_any", "role": "admin",
            "email_verified": True, "phone_verified": True, "token_version": -5, "email": "attacker@example.test",
            "password": "Hacked!Passw0rd#1", "mfa_enabled": False, "is_active": True, "id": "u-other",
            "permissions": ["*"], "auth_provider": "google", "kvkk_deleted": True}
    r = id_client.put("/api/users/me", json=evil, headers=id_auth(tok))
    assert r.status_code == 200, r.text
    u = id_find("users", {"id": cust["id"]})
    assert u["first_name"] == "Ayse"
    assert u["is_admin"] is False and not u.get("is_super_admin") and not u.get("role_id") and not u.get("role")
    assert u.get("email_verified") is False and u["email"] == cust["email"] and u["token_version"] == 0
    assert u["password"] == cust["password"] and "permissions" not in u and not u.get("auth_provider")
    assert not u.get("kvkk_deleted") and not id_find("users", {"id": "u-other"})
    # still a customer token: no admin surface, session intact
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(tok)).status_code in (401, 403)
    assert id_client.get("/api/auth/me", headers=id_auth(tok)).status_code == 200


# ─────────────────────────────── AUTH15 (+ full happy path) ───────────────────────────────

def test_reset_via_sms_revokes_sessions_and_pending_mfa_ticket(id_outbox):
    staff = id_seed_user(kind="super", phone=PHONE)
    old_tok = id_token(staff)

    async def scenario(c):
        assert (await c.get("/api/admin/me/permissions", headers=id2_hdr(old_tok))).status_code == 200
        login = await c.post("/api/auth/login", json={"email": staff["email"], "password": ID_PASSWORD},
                             headers=id2_hdr(ip="198.51.100.150"))
        assert login.status_code == 200 and login.json()["mfa_required"] is True
        ticket = login.json()["mfa_token"]
        mfa_code = [n for n in id_outbox.notifications if n["event"] == "login_verification_code"][-1]["variables"]["otp_code"]
        r = await c.post(REQ, json={"phone": PHONE})
        assert r.status_code == 200
        await asyncio.sleep(0.05)
        code = _codes(id_outbox)[-1]
        v = await c.post(VER, json={"phone": PHONE, "code": code})
        assert v.status_code == 200, v.text
        rt = v.json()["reset_token"]
        rs = await c.post(RESET, json={"reset_token": rt, "new_password": ID_NEW_PASSWORD})
        assert rs.status_code == 200, rs.text
        # old staff session is dead …
        assert (await c.get("/api/admin/me/permissions", headers=id2_hdr(old_tok))).status_code == 401
        # … and the MFA step started BEFORE the reset can no longer yield a session, even with the right code
        late = await c.post("/api/auth/mfa/verify", json={"mfa_token": ticket, "code": mfa_code},
                            headers=id2_hdr(ip="198.51.100.150"))
        assert late.status_code == 401 and "token" not in late.json()
        # the reset token is single use
        again = await c.post(RESET, json={"reset_token": rt, "new_password": ID_NEW_PASSWORD + "x"})
        assert again.status_code == 400
    id2_async(scenario)
    u = id_find("users", {"id": staff["id"]})
    assert u["token_version"] == 1 and u["password_reset_channel"] == "sms"
    import bcrypt
    assert bcrypt.checkpw(ID_NEW_PASSWORD.encode(), u["password"].encode())
    assert id_findall("auth_audit_logs", {"event": "password_reset", "user_id": staff["id"]})
    rec = id_findall("password_reset_otps", {"phone": PHONE_N})
    assert all(not r.get("reset_token") for r in rec)  # only hashes ever stored


# ─────────────────────────────── AUTH06 / CHAIN02 ───────────────────────────────

def test_concurrent_reset_code_and_token_single_success(id_outbox, monkeypatch):
    id2_yield_shim(monkeypatch)
    id_seed_user(kind="customer", phone=PHONE)

    async def scenario(c):
        assert (await c.post(REQ, json={"phone": PHONE})).status_code == 200
        await asyncio.sleep(0.05)
        code = _codes(id_outbox)[-1]
        rs = await asyncio.gather(*[c.post(VER, json={"phone": PHONE, "code": code}) for _ in range(4)])
        ok = [r for r in rs if r.status_code == 200]
        assert len(ok) == 1, [(r.status_code, r.text) for r in rs]
        assert all(r.status_code in (400, 429) and "reset_token" not in r.json() for r in rs if r not in ok)
        rt = ok[0].json()["reset_token"]
        resets = await asyncio.gather(*[c.post(RESET, json={"reset_token": rt, "new_password": f"Yeni-Sifre-{i}x9"})
                                        for i in range(3)])
        assert sorted(r.status_code for r in resets) == [200, 400, 400]
    id2_async(scenario)


# ─────────────────────────────── AUTH07 / AUTH08 ───────────────────────────────

def test_code_purpose_and_account_binding(id_outbox):
    a = id_seed_user(kind="super", phone=PHONE, email="mfa-a@example.test")
    b = id_seed_user(kind="super", phone="05559876543", email="mfa-b@example.test")

    async def scenario(c):
        la = await c.post("/api/auth/login", json={"email": a["email"], "password": ID_PASSWORD},
                          headers=id2_hdr(ip="198.51.100.151"))
        lb = await c.post("/api/auth/login", json={"email": b["email"], "password": ID_PASSWORD},
                          headers=id2_hdr(ip="198.51.100.152"))
        ta, tb = la.json()["mfa_token"], lb.json()["mfa_token"]
        mfa = {n["to_phone"][-10:]: n["variables"]["otp_code"] for n in id_outbox.notifications
               if n["event"] == "login_verification_code"}
        code_a, code_b = mfa["5551234567"], mfa["5559876543"]
        # reset OTP for A's phone
        await c.post(REQ, json={"phone": PHONE})
        await asyncio.sleep(0.05)
        reset_a = _codes(id_outbox)[-1]
        # AUTH08: A's MFA ticket + B's code → refused (B's code stays usable for B)
        if code_b != code_a:
            r = await c.post("/api/auth/mfa/verify", json={"mfa_token": ta, "code": code_b},
                             headers=id2_hdr(ip="198.51.100.151"))
            assert r.status_code == 401 and "token" not in r.json()
        # AUTH07: a password-reset OTP is not an MFA code …
        if reset_a != code_a:
            r = await c.post("/api/auth/mfa/verify", json={"mfa_token": ta, "code": reset_a},
                             headers=id2_hdr(ip="198.51.100.151"))
            assert r.status_code == 401
        # … and an MFA code is not a password-reset OTP
        if code_a != reset_a:
            r = await c.post(VER, json={"phone": PHONE, "code": code_a})
            assert r.status_code == 400 and "reset_token" not in r.json()
        # AUTH08 for reset OTPs: B's number with A's reset code
        r = await c.post(VER, json={"phone": "05559876543", "code": reset_a})
        assert r.status_code == 400 and "reset_token" not in r.json()
        # the legitimate pairs still work (nothing above consumed them)
        rb = await c.post("/api/auth/mfa/verify", json={"mfa_token": tb, "code": code_b},
                          headers=id2_hdr(ip="198.51.100.152"))
        assert rb.status_code == 200, rb.text
        ra = await c.post("/api/auth/mfa/verify", json={"mfa_token": ta, "code": code_a},
                          headers=id2_hdr(ip="198.51.100.151"))
        assert ra.status_code == 200, ra.text
        assert deps.jwt.decode(ra.json()["token"], options={"verify_signature": False})["user_id"] == a["id"]
    id2_async(scenario)


def test_guest_convert_code_is_not_a_reset_or_mfa_code(id_client, id_outbox):
    now = datetime.now(timezone.utc).isoformat()
    id_run(deps.db.orders.insert_one({"id": "o-guest-1", "order_number": "G1001", "created_at": now,
                                      "shipping_address": {"email": "guest1@example.test", "phone": "05550001122",
                                                           "first_name": "Misafir"}}))
    r = id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-guest-1"}, headers=id_ip("198.51.100.153"))
    assert r.status_code == 200, r.text
    code = re.search(r"letter-spacing:6px[^>]*>(\d{6})<", id_outbox.emails[-1]["html"]).group(1)
    # a reset record exists for the order phone, but the guest code is not valid there
    id_run(deps.db.password_reset_otps.insert_one({"phone": "905550001122", "code_hash": "x" * 64, "user_id": "nobody",
                                                   "expires_at": time.time() + 300, "used": False, "attempts": 0,
                                                   "created_at": now}))
    r = id_client.post(VER, json={"phone": "05550001122", "code": code})
    assert r.status_code == 400
    # the guest code still converts exactly once
    r = id_client.post("/api/auth/convert-guest-order", json={"order_id": "o-guest-1", "password": "Misafir!2026x",
                                                               "code": code}, headers=id_ip("198.51.100.154"))
    assert r.status_code == 200, r.text
    r = id_client.post("/api/auth/convert-guest-order", json={"order_id": "o-guest-1", "password": "Misafir!2026x",
                                                               "code": code}, headers=id_ip("198.51.100.155"))
    assert r.status_code == 400


# ─────────────────────────────── AUTH09 ───────────────────────────────

def test_reset_code_dies_when_account_phone_changes(id_outbox):
    cust = id_seed_user(kind="customer", phone=PHONE)
    tok = id_token(cust)

    async def scenario(c):
        await c.post(REQ, json={"phone": PHONE})
        await asyncio.sleep(0.05)
        code = _codes(id_outbox)[-1]
        # the account owner (or a stolen session) moves the account to another number
        r = await c.put("/api/users/me", json={"phone": "05557778899"}, headers=id2_hdr(tok))
        assert r.status_code == 200
        r = await c.post(VER, json={"phone": PHONE, "code": code})
        assert r.status_code == 400 and "reset_token" not in r.json()
        # the new (unverified) number cannot receive a reset code either
        await c.post(REQ, json={"phone": "05557778899"})
        await asyncio.sleep(0.05)
        assert not _codes(id_outbox, "5557778899")
    id2_async(scenario)
    rec = id_find("password_reset_otps", {"phone": PHONE_N})
    assert rec["used"] is True and rec["invalidated"] == "phone_changed"


def test_reset_code_dies_when_staff_changes_customer_phone(id_outbox):
    cust = id_seed_user(kind="customer", phone=PHONE)
    sa = id_seed_user(kind="super", email="owner-cust@example.test")

    async def scenario(c):
        await c.post(REQ, json={"phone": PHONE})
        await asyncio.sleep(0.05)
        code = _codes(id_outbox)[-1]
        r = await c.put(f"/api/admin/members/{cust['id']}", json={"phone": "05553334455"}, headers=id2_hdr(id_token(sa)))
        assert r.status_code == 200, r.text
        r = await c.post(VER, json={"phone": PHONE, "code": code})
        assert r.status_code == 400
    id2_async(scenario)


def test_mfa_login_code_dies_when_target_phone_changes(id_client, id_outbox):
    staff = id_seed_user(kind="super", phone=PHONE, email="target-change@example.test")
    r = id_client.post("/api/auth/login", json={"email": staff["email"], "password": ID_PASSWORD},
                       headers=id_ip("198.51.100.156"))
    ticket = r.json()["mfa_token"]
    code = id_outbox.last_code("sms")
    rec = id_find("mfa_sms_codes", {"user_id": staff["id"], "used": False})
    assert rec["target_hash"] and PHONE_N not in repr(rec)
    # the account phone changes after the code went out (no session revocation on this path)
    id_update("users", {"id": staff["id"]}, {"$set": {"phone": "05550009988"}})
    r = id_client.post("/api/auth/mfa/verify", json={"mfa_token": ticket, "code": code}, headers=id_ip("198.51.100.156"))
    assert r.status_code == 401 and "token" not in r.json()
    assert id_find("mfa_sms_codes", {"user_id": staff["id"]})["invalidated"] == "target_changed"


# ─────────────────────────────── AUTH10 / AUTH19 ───────────────────────────────

def test_number_budget_is_24h_and_not_reset_by_resending(id_outbox):
    id_seed_user(kind="customer", phone=PHONE)
    bodies = []
    with freeze_time("2026-10-04 08:00:00") as frozen:
        async def scenario(c):
            for i in range(7):
                r = await c.post(REQ, json={"phone": PHONE})
                assert r.status_code == 200
                bodies.append(r.json())
                await asyncio.sleep(0.02)
                frozen.tick(timedelta(seconds=61))
            # unknown numbers have the same budget (no enumeration through the limiter)
            for i in range(6):
                r = await c.post(REQ, json={"phone": "05550000001"})
                bodies.append(r.json())
                frozen.tick(timedelta(seconds=61))
        id2_async(scenario)
        assert len(_codes(id_outbox)) == 5  # 6th and 7th request inside 24h → no SMS
        assert len(id_findall("password_reset_otps", {"phone": "905550000001"})) == 5
        assert all(b == bodies[0] for b in bodies)  # identical answer whatever happened
        frozen.tick(timedelta(hours=24))

        async def later(c):
            await c.post(REQ, json={"phone": PHONE})
        id2_async(later)
        assert len(_codes(id_outbox)) == 6  # budget window rolled


def test_account_failure_budget_survives_new_codes(id_outbox):
    id_seed_user(kind="customer", phone=PHONE)
    with freeze_time("2026-10-04 09:00:00") as frozen:
        async def scenario(c):
            for round_ in range(2):
                await c.post(REQ, json={"phone": PHONE})
                await asyncio.sleep(0.02)
                code = _codes(id_outbox)[-1]
                for _ in range(5):
                    r = await c.post(VER, json={"phone": PHONE, "code": _wrong(code)})
                    assert r.status_code in (400, 429)
                frozen.tick(timedelta(seconds=61))
            # 10 failures across two codes → a third code is NOT sent, and verify is closed
            n_before = len(_codes(id_outbox))
            r = await c.post(REQ, json={"phone": PHONE})
            assert r.status_code == 200
            await asyncio.sleep(0.02)
            assert len(_codes(id_outbox)) == n_before
        id2_async(scenario)


def test_limit_store_outage_fails_closed(id_outbox, monkeypatch):
    id_seed_user(kind="customer", phone=PHONE)
    coll_cls = type(deps.db.password_reset_otps)
    orig = coll_cls.count_documents

    async def _boom(self, *a, **k):
        if self.name == "password_reset_otps":
            raise RuntimeError("limit store down (test)")
        return await orig(self, *a, **k)
    monkeypatch.setattr(coll_cls, "count_documents", _boom)

    async def scenario(c):
        r = await c.post(REQ, json={"phone": PHONE})
        assert r.status_code == 503
        r2 = await c.post(REQ, json={"phone": "05550000002"})  # same answer for unknown numbers
        assert r2.status_code == 503 and r2.json() == r.json()
    id2_async(scenario)
    assert not _codes(id_outbox) and not id_outbox.notifications
    assert not id_findall("password_reset_otps")


def test_guest_convert_code_budget_and_fail_closed(id_client, id_outbox, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    id_run(deps.db.orders.insert_one({"id": "o-guest-2", "order_number": "G1002", "created_at": now,
                                      "shipping_address": {"email": "guest2@example.test"}}))
    with freeze_time(datetime.now(timezone.utc)) as frozen:
        for i in range(5):
            assert id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-guest-2"},
                                  headers=id_ip(f"198.51.100.{160 + i}")).status_code == 200
            frozen.tick(timedelta(seconds=30))
        r = id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-guest-2"},
                           headers=id_ip("198.51.100.170"))
        assert r.status_code == 429
    assert len([m for m in id_outbox.emails if m["to"] == "guest2@example.test"]) == 5
    coll_cls = type(deps.db.guest_convert_codes)
    orig = coll_cls.count_documents

    async def _boom(self, *a, **k):
        if self.name == "guest_convert_codes":
            raise RuntimeError("down")
        return await orig(self, *a, **k)
    monkeypatch.setattr(coll_cls, "count_documents", _boom)
    id_run(deps.db.orders.insert_one({"id": "o-guest-3", "order_number": "G1003", "created_at": now,
                                      "shipping_address": {"email": "guest3@example.test"}}))
    r = id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-guest-3"}, headers=id_ip("198.51.100.171"))
    assert r.status_code == 503
    assert not [m for m in id_outbox.emails if m["to"] == "guest3@example.test"]


# ─────────────────────────────── AUTH11 ───────────────────────────────

def test_expired_codes_and_tokens_rejected(id_client, id_outbox):
    id_seed_user(kind="customer", phone=PHONE, email="exp@example.test")
    staff = id_seed_user(kind="super", phone="05559990011", email="exp-staff@example.test")
    with freeze_time("2026-10-04 10:00:00") as frozen:
        async def scenario(c):
            # SMS reset OTP: 5 minutes
            await c.post(REQ, json={"phone": PHONE})
            await asyncio.sleep(0.02)
            code = _codes(id_outbox)[-1]
            frozen.tick(timedelta(minutes=5, seconds=2))
            r = await c.post(VER, json={"phone": PHONE, "code": code})
            assert r.status_code == 400 and "reset_token" not in r.json()
            # reset token from a valid code: 10 minutes
            frozen.tick(timedelta(seconds=61))
            await c.post(REQ, json={"phone": PHONE})
            await asyncio.sleep(0.02)
            v = await c.post(VER, json={"phone": PHONE, "code": _codes(id_outbox)[-1]})
            assert v.status_code == 200
            frozen.tick(timedelta(minutes=10, seconds=2))
            r = await c.post(RESET, json={"reset_token": v.json()["reset_token"], "new_password": "Yeni-Sifre-77x"})
            assert r.status_code == 400
            # e-mail link: 30 minutes
            await c.post(EMAIL, json={"email": "exp@example.test"})
            await asyncio.sleep(0.02)
            tok = re.search(r"sifre-sifirla\?token=([A-Za-z0-9_\-]+)", id_outbox.emails[-1]["html"]).group(1)
            frozen.tick(timedelta(minutes=30, seconds=2))
            r = await c.post(RESET, json={"reset_token": tok, "new_password": "Yeni-Sifre-77x"})
            assert r.status_code == 400
            # admin MFA code: 5 minutes (a fresh, valid ticket does not revive an expired code)
            r = await c.post("/api/auth/login", json={"email": staff["email"], "password": ID_PASSWORD},
                             headers=id2_hdr(ip="198.51.100.172"))
            mcode = id_outbox.last_code("sms")
            frozen.tick(timedelta(minutes=5, seconds=2))
            from routes.mfa import create_mfa_pending_token
            fresh_ticket = create_mfa_pending_token(staff["id"], 0)
            r = await c.post("/api/auth/mfa/verify", json={"mfa_token": fresh_ticket, "code": mcode},
                             headers=id2_hdr(ip="198.51.100.172"))
            assert r.status_code == 401 and "token" not in r.json()
        id2_async(scenario)
    u = id_find("users", {"email": "exp@example.test"})
    assert u["token_version"] == 0  # nothing was reset


def test_expired_guest_convert_code_rejected(id_client, id_outbox):
    with freeze_time("2026-10-04 11:00:00") as frozen:
        id_run(deps.db.orders.insert_one({"id": "o-guest-4", "order_number": "G1004",
                                          "created_at": "2026-10-04T10:59:00+00:00",
                                          "shipping_address": {"email": "guest4@example.test"}}))
        id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-guest-4"}, headers=id_ip("198.51.100.173"))
        code = re.search(r"letter-spacing:6px[^>]*>(\d{6})<", id_outbox.emails[-1]["html"]).group(1)
        frozen.tick(timedelta(minutes=10, seconds=2))
        r = id_client.post("/api/auth/convert-guest-order", json={"order_id": "o-guest-4", "password": "Misafir!2026x",
                                                                   "code": code}, headers=id_ip("198.51.100.174"))
        assert r.status_code == 400 and not r.json().get("token")
    assert not id_find("users", {"email": "guest4@example.test"})


# ─────────────────────────────── AUTH12 / AUTH20 ───────────────────────────────

def _verification(user, token="v" * 43, email=None):
    id_run(deps.db.email_verifications.insert_one({
        "token": token, "user_id": user["id"], "email": (email or user["email"]), "used": False,
        "expires_at": int(time.time()) + 3600, "created_at": datetime.now(timezone.utc).isoformat()}))
    return token


def test_verify_email_get_shows_page_and_changes_nothing(id_client):
    cust = id_seed_user(kind="customer", email_verified=False)
    tok = _verification(cust)
    r = id_client.get(f"/api/auth/verify-email?token={tok}", follow_redirects=False)
    assert r.status_code == 200 and "<form method=\"post\"" in r.text and r.headers.get("cache-control") == "no-store"
    assert id_find("users", {"id": cust["id"]})["email_verified"] is False
    assert id_find("email_verifications", {"token": tok})["used"] is False
    # HEAD/GET by mail scanners repeated → still nothing
    id_client.get(f"/api/auth/verify-email?token={tok}", follow_redirects=False)
    assert id_find("users", {"id": cust["id"]})["email_verified"] is False
    # explicit POST (button) verifies once
    r = id_client.post("/api/auth/verify-email", data={"token": tok}, follow_redirects=False)
    assert r.status_code == 303 and "email_verified=1" in r.headers["location"]
    assert id_find("users", {"id": cust["id"]})["email_verified"] is True


def test_verification_not_carried_to_changed_email(id_client):
    cust = id_seed_user(kind="customer", email="old-addr@example.test", email_verified=False)
    tok = _verification(cust)
    # neither the profile endpoint nor the staff member editor can change the e-mail or its verified flag
    r = id_client.put("/api/users/me", json={"email": "new-addr@example.test", "email_verified": True},
                      headers=id_auth(id_token(cust)))
    assert r.status_code == 200
    sa = id_seed_user(kind="super", email="owner-ev@example.test")
    r = id_client.put(f"/api/admin/members/{cust['id']}", json={"email": "new-addr@example.test", "email_verified": True},
                      headers=id_auth(id_token(sa)))
    assert r.status_code == 200
    u = id_find("users", {"id": cust["id"]})
    assert u["email"] == "old-addr@example.test" and u["email_verified"] is False
    # any out-of-band e-mail change (DB edit / future flow) → the old link must not verify the new address
    id_update("users", {"id": cust["id"]}, {"$set": {"email": "new-addr@example.test"}})
    r = id_client.post("/api/auth/verify-email", data={"token": tok}, follow_redirects=False)
    assert r.status_code == 303 and "email_verified=0" in r.headers["location"]
    assert id_find("users", {"id": cust["id"]})["email_verified"] is False


# ─────────────────────────────── AUTH13 ───────────────────────────────

def test_reset_link_domain_ignores_host_headers(id_outbox, monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://shop.example.test")
    id_seed_user(kind="customer", email="host-hdr@example.test")

    async def scenario(c):
        r = await c.post(EMAIL, json={"email": "host-hdr@example.test"},
                         headers={"host": "evil.example", "x-forwarded-host": "evil.example",
                                  "x-forwarded-proto": "http", "origin": "https://evil.example",
                                  "referer": "https://evil.example/x"})
        assert r.status_code == 200
    id2_async(scenario)
    html = id_outbox.emails[-1]["html"]
    assert "https://shop.example.test/sifre-sifirla?token=" in html
    assert "evil" not in html


# ─────────────────────────────── AUTH14 ───────────────────────────────

def test_forgot_password_answers_identical_for_known_and_unknown(monkeypatch, id_outbox):
    id_seed_user(kind="customer", phone=PHONE, email="known@example.test")

    async def _slow_sms(db, event, *a, **k):
        await asyncio.sleep(0.4)  # slow SMS provider
        id_outbox.notifications.append({"event": event, **k})
        return {"success": True}

    async def _slow_mail(db, to, subject, html, *a, **k):
        await asyncio.sleep(0.4)
        id_outbox.emails.append({"to": to, "subject": subject, "html": html})
        return {"success": True}
    import notification_service
    import email_smtp
    monkeypatch.setattr(notification_service, "send_notification", _slow_sms)
    monkeypatch.setattr(email_smtp, "send_smtp_email", _slow_mail)
    out = {}

    async def scenario(c):
        for name, path, body in (("sms_known", REQ, {"phone": PHONE}), ("sms_unknown", REQ, {"phone": "05550000003"}),
                                 ("mail_known", EMAIL, {"email": "known@example.test"}),
                                 ("mail_unknown", EMAIL, {"email": "nobody@example.test"})):
            t0 = time.perf_counter()
            r = await c.post(path, json=body)
            out[name] = (r.status_code, r.json(), time.perf_counter() - t0)
    id2_async(scenario)
    assert out["sms_known"][:2] == out["sms_unknown"][:2]
    assert out["mail_known"][:2] == out["mail_unknown"][:2]
    # the slow provider runs in the background: response time does not reveal the account
    assert abs(out["sms_known"][2] - out["sms_unknown"][2]) < 0.2, out
    assert abs(out["mail_known"][2] - out["mail_unknown"][2]) < 0.2, out
    # … and the messages were still delivered for the known account only
    assert len(_codes(id_outbox)) == 1 and [m["to"] for m in id_outbox.emails] == ["known@example.test"]
