"""Google sign-in: immutable `sub` binding, sub mismatch → 409, staff accounts refused (403)."""
import sys
import types

import pytest

from id_harness import ID_PASSWORD, id_seed_user, id_auth, id_ip, id_find, id_findall, id_decode, id_token

pytestmark = pytest.mark.usefixtures("id_env")

GOOGLE = "/api/auth/google"
CLIENT_ID = "e2e-client.apps.googleusercontent.test"


@pytest.fixture
def google(monkeypatch):
    """Stub google.oauth2.id_token: credential string → idinfo (audience checked)."""
    tokens = {}
    seen = []

    def verify_oauth2_token(credential, request, audience):
        seen.append(audience)
        info = tokens.get(credential)
        if info is None or audience != CLIENT_ID:
            raise ValueError("Token verification failed")
        return dict(info)

    g = types.ModuleType("google")
    g.__path__ = []
    oauth2 = types.ModuleType("google.oauth2")
    oauth2.__path__ = []
    idt = types.ModuleType("google.oauth2.id_token")
    idt.verify_oauth2_token = verify_oauth2_token
    oauth2.id_token = idt
    auth = types.ModuleType("google.auth")
    auth.__path__ = []
    transport = types.ModuleType("google.auth.transport")
    transport.__path__ = []
    req = types.ModuleType("google.auth.transport.requests")
    req.Request = lambda *a, **k: object()
    transport.requests = req
    auth.transport = transport
    g.oauth2, g.auth = oauth2, auth
    for name, mod in {"google": g, "google.oauth2": oauth2, "google.oauth2.id_token": idt, "google.auth": auth,
                      "google.auth.transport": transport, "google.auth.transport.requests": req}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.delenv("GOOGLE_CLIENT_IDS", raising=False)

    def issue(cred, *, sub, email, verified=True, name="Gül Test"):
        tokens[cred] = {"sub": sub, "email": email, "email_verified": verified, "name": name, "aud": CLIENT_ID}
        return cred
    issue.seen = seen
    return issue


def _g(client, cred, ip="198.51.100.90"):
    return client.post(GOOGLE, json={"credential": cred}, headers=id_ip(ip))


def test_first_login_creates_account_bound_to_sub(id_client, google):
    cred = google("cred-new", sub="g-sub-100", email="newbie@example.test")
    r = _g(id_client, cred)
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    assert id_decode(tok)["typ"] == "customer" and id_decode(tok)["is_admin"] is False
    u = id_find("users", {"email": "newbie@example.test"})
    assert u["auth_providers"]["google"]["sub"] == "g-sub-100" and u["is_admin"] is False
    assert id_client.get("/api/auth/me", headers=id_auth(tok)).status_code == 200
    assert id_findall("auth_audit_logs", {"event": "google_login", "user_id": u["id"]})
    # same sub, later with a CHANGED e-mail address → still the same account (sub is the identity)
    cred2 = google("cred-new-2", sub="g-sub-100", email="renamed@example.test")
    r = _g(id_client, cred2)
    assert r.status_code == 200 and r.json()["user"]["id"] == u["id"]
    assert not id_find("users", {"email": "renamed@example.test"})


def test_existing_password_account_gets_sub_bound_and_secured(id_client, google):
    cust = id_seed_user(kind="customer", email="existing@example.test", email_verified=False)
    old_tok = id_token(cust)
    r = _g(id_client, google("cred-ex", sub="g-sub-200", email="existing@example.test"))
    assert r.status_code == 200, r.text
    u = id_find("users", {"id": cust["id"]})
    assert u["auth_providers"]["google"]["sub"] == "g-sub-200"
    # pre-hijack protection: unverified pre-existing account → old password + sessions invalidated
    assert u["email_verified"] is True and u["token_version"] == 1 and u["password"] != cust["password"]
    assert id_client.get("/api/auth/me", headers=id_auth(old_tok)).status_code == 401
    r = id_client.post("/api/auth/login", json={"email": "existing@example.test", "password": ID_PASSWORD},
                       headers=id_ip("198.51.100.91"))
    assert r.status_code == 401


def test_different_sub_for_bound_email_is_409(id_client, google):
    _g(id_client, google("cred-a", sub="g-sub-300", email="victim@example.test"))
    r = _g(id_client, google("cred-b", sub="g-sub-ATTACKER", email="victim@example.test"))
    assert r.status_code == 409, r.text
    assert "token" not in r.json()
    u = id_find("users", {"email": "victim@example.test"})
    assert u["auth_providers"]["google"]["sub"] == "g-sub-300"


@pytest.mark.parametrize("kind", ["super", "staff"])
def test_admin_accounts_cannot_use_google(id_client, google, kind):
    from id_harness import id_seed_role
    id_seed_role("r_v", ["orders.view"])
    staff = id_seed_user(kind=kind, role_id="r_v", email=f"{kind}-g@example.test")
    r = _g(id_client, google("cred-adm", sub="g-sub-400", email=staff["email"]))
    assert r.status_code == 403, r.text
    assert "token" not in r.json()
    u = id_find("users", {"id": staff["id"]})
    assert "auth_providers" not in u and u["password"] == staff["password"]  # untouched
    # also when the sub was already bound to the staff account
    from id_harness import id_update
    id_update("users", {"id": staff["id"]}, {"$set": {"auth_providers.google.sub": "g-sub-401"}})
    r = _g(id_client, google("cred-adm2", sub="g-sub-401", email="other-address@example.test"))
    assert r.status_code == 403


def test_unverified_or_invalid_google_tokens_refused(id_client, google):
    r = _g(id_client, google("cred-unv", sub="g-sub-500", email="unverified@example.test", verified=False))
    assert r.status_code == 400
    r = _g(id_client, "forged-credential")
    assert r.status_code == 401
    assert not id_find("users", {"email": "unverified@example.test"})
    assert CLIENT_ID in google.seen
