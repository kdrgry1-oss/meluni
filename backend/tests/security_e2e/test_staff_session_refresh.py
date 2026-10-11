"""Panelde ETKİN çalışan personel çıkarılmaz: /auth/staff/refresh aynı kimlik kanıtıyla (auth_time + amr)
yeni token verir; ilk girişten panel.max_session_days (7) gün sonra reddeder; iptal edilmiş oturum ve
müşteri token'ı yenilenemez."""
import jwt as pyjwt
import pytest

from id_harness import id_auth, id_seed_role, id_seed_user, id_set_rule, id_update
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")
URL = "/api/auth/staff/refresh"


def _staff():
    id_seed_role("ops-r", ["orders.view"])
    return id_seed_user(kind="staff", role_id="ops-r")


def _claims(t):
    return pyjwt.decode(t, options={"verify_signature": False})


def test_refresh_keeps_identity_proof_and_extends_expiry(id_client):
    staff = _staff()
    at = deps.now_ts() - 11 * 3600                      # 11 saat önce giriş (12 sa TTL'e yakın)
    old = deps.create_token(staff["id"], True, token_version=staff.get("token_version", 0), auth_time=at,
                            amr=["mfa", "sms"])
    r = id_client.post(URL, headers=id_auth(old))
    assert r.status_code == 200, r.text
    new = r.json()["token"]
    c_old, c_new = _claims(old), _claims(new)
    assert c_new["auth_time"] == at and c_new["amr"] == ["mfa", "sms"]
    assert c_new["exp"] >= c_old["exp"] and c_new["jti"] != c_old["jti"]
    assert id_client.get("/api/admin/me/permissions", headers=id_auth(new)).status_code == 200


def test_refresh_refused_after_max_session_days(id_client):
    staff = _staff()
    id_set_rule("panel.max_session_days", 7)
    old = deps.create_token(staff["id"], True, token_version=staff.get("token_version", 0),
                            auth_time=deps.now_ts() - 8 * 86400)
    r = id_client.post(URL, headers=id_auth(old))
    assert r.status_code == 401 and "yeniden giriş" in r.json()["detail"]


def test_revoked_or_customer_tokens_cannot_refresh(id_client):
    staff = _staff()
    tok = deps.create_token(staff["id"], True, token_version=staff.get("token_version", 0), auth_time=deps.now_ts())
    id_update("users", {"id": staff["id"]}, {"$inc": {"token_version": 1}})   # tüm cihazlardan çıkış
    assert id_client.post(URL, headers=id_auth(tok)).status_code == 401
    cust = id_seed_user(kind="customer")
    ct = deps.create_token(cust["id"], False, auth_time=deps.now_ts())
    assert id_client.post(URL, headers=id_auth(ct)).status_code in (401, 403)
