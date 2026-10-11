"""Session lifecycle: logout-all, global revoke (security epoch), TTL, token type, idle timeout,
deactivation."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_ip, id_auth, id_find, id_token, id_decode,
                        id_update, id_set_rule, id_audit_actions, id_findall)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

ME = "/api/auth/me"
ADMIN_PING = "/api/admin/me/permissions"


def _staff():
    id_seed_role("r_view", ["orders.view"])
    return id_seed_user(kind="staff", role_id="r_view")


def test_logout_all_kills_other_sessions_and_returns_working_token(id_client):
    cust = id_seed_user(kind="customer")
    a, b = id_token(cust), id_token(cust)
    assert id_client.get(ME, headers=id_auth(b)).status_code == 200
    r = id_client.post("/api/auth/logout-all", headers=id_auth(a, "198.51.100.50"))
    assert r.status_code == 200, r.text
    fresh = r.json()["token"]
    assert id_client.get(ME, headers=id_auth(a)).status_code == 401
    assert id_client.get(ME, headers=id_auth(b)).status_code == 401
    me = id_client.get(ME, headers=id_auth(fresh))
    assert me.status_code == 200 and me.json()["id"] == cust["id"]
    assert id_decode(fresh)["tv"] == 1
    assert id_findall("auth_audit_logs", {"event": "logout_all", "user_id": cust["id"]})
    # unauthenticated call is refused
    assert id_client.post("/api/auth/logout-all").status_code == 401


def test_logout_all_for_staff_keeps_admin_access_only_on_fresh_token(id_client):
    staff = _staff()
    old = id_token(staff)
    r = id_client.post("/api/auth/logout-all", headers=id_auth(old))
    assert r.status_code == 200
    assert id_client.get(ADMIN_PING, headers=id_auth(old)).status_code == 401
    assert id_client.get(ADMIN_PING, headers=id_auth(r.json()["token"])).status_code == 200


def test_security_epoch_revokes_everyone(id_client):
    sa = id_seed_user(kind="super")
    staff = _staff()
    cust = id_seed_user(kind="customer")
    sa_tok, staff_tok, cust_tok = id_token(sa), id_token(staff), id_token(cust)
    # non-super staff cannot pull the global kill switch
    assert id_client.post("/api/admin/security/revoke-all-sessions", headers=id_auth(staff_tok)).status_code == 403
    assert id_client.get(ME, headers=id_auth(cust_tok)).status_code == 200
    r = id_client.post("/api/admin/security/revoke-all-sessions", headers=id_auth(sa_tok))
    assert r.status_code == 200, r.text
    assert r.json()["epoch"] == 1
    fresh_sa = r.json()["token"]
    assert id_client.get(ME, headers=id_auth(cust_tok)).status_code == 401
    assert id_client.get(ADMIN_PING, headers=id_auth(staff_tok)).status_code == 401
    assert id_client.get(ADMIN_PING, headers=id_auth(sa_tok)).status_code == 401
    assert id_client.get(ADMIN_PING, headers=id_auth(fresh_sa)).status_code == 200
    # audited (fail-closed intent + done) and alerted
    assert id_audit_actions("security.revoke_all_sessions").count("security.revoke_all_sessions") == 2
    assert id_findall("alerts", {"kind": "sessions_revoked_all"})
    # a NEW login after the revoke works
    r = id_client.post("/api/auth/login", json={"email": cust["email"], "password": ID_PASSWORD},
                       headers=id_ip("198.51.100.51"))
    assert r.status_code == 200
    assert id_client.get(ME, headers=id_auth(r.json()["token"])).status_code == 200
    # a different worker with a stale epoch cache still rejects the old token once it reloads
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    assert id_client.get(ME, headers=id_auth(cust_tok)).status_code == 401


def test_revoke_all_fails_closed_without_audit(id_client, id_break_audit):
    sa = id_seed_user(kind="super")
    cust = id_seed_user(kind="customer")
    sa_tok, cust_tok = id_token(sa), id_token(cust)
    id_break_audit()
    r = id_client.post("/api/admin/security/revoke-all-sessions", headers=id_auth(sa_tok))
    assert r.status_code == 503
    assert not (id_find("settings", {"_id": "security_epoch"}) or {}).get("epoch")
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    assert id_client.get(ME, headers=id_auth(cust_tok)).status_code == 200


def test_token_ttls(id_client):
    staff = _staff()
    cust = id_seed_user(kind="customer")
    s = id_decode(id_token(staff))
    c = id_decode(id_token(cust))
    assert s["typ"] == "staff" and s["exp"] - s["iat"] == 24 * 3600
    assert c["typ"] == "customer" and c["exp"] - c["iat"] == 7 * 24 * 3600
    with freeze_time("2026-10-04 06:00:00") as frozen:
        tok = id_token(staff)
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
        frozen.tick(delta=timedelta(hours=24, minutes=1))
        r = id_client.get(ADMIN_PING, headers=id_auth(tok))
        assert r.status_code == 401


def test_customer_type_token_rejected_by_admin_api(id_client):
    staff = _staff()
    cust = id_seed_user(kind="customer")
    assert id_client.get(ADMIN_PING, headers=id_auth(id_token(cust))).status_code == 403
    # a staff account holding a customer-type token (e.g. minted by social login) gets no admin access
    cust_typed = deps.create_token(staff["id"], False, token_version=0, auth_time=deps.now_ts())
    assert id_client.get(ADMIN_PING, headers=id_auth(cust_typed)).status_code == 403
    # even a validly-signed token claiming is_admin=True but typ=customer is refused
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"user_id": staff["id"], "is_admin": True, "tv": 0, "typ": "customer", "ep": 0,
                         "iat": now, "exp": now + timedelta(hours=1), "iss": deps.JWT_ISSUER},
                        deps.JWT_SECRET, algorithm="HS256")
    assert id_client.get(ADMIN_PING, headers=id_auth(forged)).status_code == 403
    # wrong signature / alg=none
    bad = jwt.encode({"user_id": staff["id"], "is_admin": True, "typ": "staff", "iat": now,
                      "exp": now + timedelta(hours=1), "iss": deps.JWT_ISSUER}, "x" * 64, algorithm="HS256")
    assert id_client.get(ADMIN_PING, headers=id_auth(bad)).status_code == 401
    none_tok = jwt.encode({"user_id": staff["id"], "is_admin": True, "typ": "staff", "iat": now,
                           "exp": now + timedelta(hours=1), "iss": deps.JWT_ISSUER}, None, algorithm="none")
    assert id_client.get(ADMIN_PING, headers=id_auth(none_tok)).status_code == 401
    # the genuine staff token works
    assert id_client.get(ADMIN_PING, headers=id_auth(id_token(staff))).status_code == 200


def test_staff_idle_timeout(id_client, monkeypatch):
    staff = _staff()
    id_set_rule("panel.auto_logout_minutes", 15)
    with freeze_time("2026-10-04 07:00:00") as frozen:
        tok = id_token(staff)
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
        frozen.tick(delta=timedelta(minutes=10))
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200  # activity keeps it alive
        frozen.tick(delta=timedelta(minutes=16))
        r = id_client.get(ADMIN_PING, headers=id_auth(tok))
        assert r.status_code == 401, r.text
        assert "işlem yapılmadığı" in r.json()["detail"]
        # still dead on the next call, and the persisted last-seen survives a process restart
        deps._IDLE_SEEN.clear()
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 401
        # a fresh login token works
        assert id_client.get(ADMIN_PING, headers=id_auth(id_token(staff))).status_code == 200


def test_idle_timeout_off_switch(id_client, monkeypatch):
    staff = _staff()
    id_set_rule("panel.auto_logout_minutes", 15)
    monkeypatch.setenv("STAFF_IDLE_TIMEOUT", "off")
    with freeze_time("2026-10-04 07:00:00") as frozen:
        tok = id_token(staff)
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
        frozen.tick(delta=timedelta(minutes=40))
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200


def test_deactivated_staff_token_rejected_immediately(id_client):
    staff = _staff()
    tok = id_token(staff)
    assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
    id_update("users", {"id": staff["id"]}, {"$set": {"is_active": False}})
    assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 401
    assert id_client.get(ME, headers=id_auth(tok)).status_code == 401
    # login is refused too
    r = id_client.post("/api/auth/login", json={"email": staff["email"], "password": ID_PASSWORD},
                       headers=id_ip("198.51.100.52"))
    assert r.status_code == 403


def test_role_removed_or_role_deleted_loses_admin_access(id_client):
    staff = _staff()
    tok = id_token(staff)
    from id_harness import id_run
    id_run(deps.db.roles.delete_one({"id": "r_view"}))
    assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 403


def test_password_change_revokes_other_sessions(id_client):
    cust = id_seed_user(kind="customer")
    a, b = id_token(cust), id_token(cust)
    r = id_client.post("/api/auth/change-password",
                       json={"current_password": ID_PASSWORD, "new_password": "BrandNew-pass-123"},
                       headers=id_auth(a))
    assert r.status_code == 200
    assert id_client.get(ME, headers=id_auth(b)).status_code == 401
    assert id_client.get(ME, headers=id_auth(r.json()["token"])).status_code == 200


@pytest.mark.parametrize("env,hours", [("1000", 168), ("abc", 24), ("-5", 24), ("8", 8)])
def test_staff_ttl_env_is_bounded(monkeypatch, env, hours):
    staff = _staff()
    monkeypatch.setenv("STAFF_TOKEN_TTL_HOURS", env)
    c = id_decode(id_token(staff))
    assert c["exp"] - c["iat"] == hours * 3600
