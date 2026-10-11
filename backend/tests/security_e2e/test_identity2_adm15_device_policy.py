"""ADM-15: personel ayrılınca cihaz önbelleğini sildiren makine-okunur oturum-iptal kodu ve asgari
native uygulama sürümü politikası (İşletme Kuralları app.min_version_admin / app.min_version_customer).

Sunucu tarafı sözleşmesi:
  * İptal edilmiş oturum (pasif/silinmiş hesap, logout-all / şifre → token_version, küresel epoch)
    401 + `X-Auth-Code: session_revoked` döner; `detail` metni DEĞİŞMEZ (mevcut istemciler bozulmaz).
  * Bozuk / süresi dolmuş token → 401 ama session_revoked YOK (yanlış silme tetiklenmez).
  * Boşta-kalma → `X-Auth-Code: session_idle`.
  * Başlık CORS'ta expose edilir; X-App-* istek başlıkları preflight'tan geçer.
  * Native istemci (X-App-Platform ios|android) asgari sürümün altındaysa → 426 + Türkçe mesaj;
    web istemcisi (başlıksız) etkilenmez; kural boş/geçersizse zorlama yok.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from freezegun import freeze_time

import server
from id_harness import (id_seed_user, id_seed_role, id_auth, id_token, id_update, id_set_rule, id_run,
                        id_find, id_audit_actions)
from routes import deps
from security import app_version_policy as AVP

pytestmark = pytest.mark.usefixtures("id_env")

ME = "/api/auth/me"
ADMIN_PING = "/api/admin/me/permissions"
RULES = "/api/admin/business-rules"
PUBLIC_RULES = "/api/business-rules"


def _staff():
    id_seed_role("r_view", ["orders.view"])
    return id_seed_user(kind="staff", role_id="r_view")


def _code(r):
    return r.headers.get("x-auth-code")


def _native(platform="ios", version="", target="admin"):
    h = {"X-App-Platform": platform, "X-App-Target": target}
    if version is not None:
        h["X-App-Version"] = version
    return h


# ───────────────────────────── oturum iptal kodu ─────────────────────────────
def test_deactivated_staff_gets_session_revoked_code_and_same_detail(id_client):
    staff = _staff()
    tok = id_token(staff)
    ok = id_client.get(ADMIN_PING, headers=id_auth(tok))
    assert ok.status_code == 200 and _code(ok) is None
    id_update("users", {"id": staff["id"]}, {"$set": {"is_active": False}})
    r = id_client.get(ADMIN_PING, headers=id_auth(tok))
    assert r.status_code == 401
    assert r.json()["detail"] == "Hesap devre dışı"          # metin değişmedi
    assert _code(r) == deps.SESSION_REVOKED_CODE
    me = id_client.get(ME, headers=id_auth(tok))              # açılış kontrolü de kodu taşır
    assert me.status_code == 401 and _code(me) == "session_revoked"
    assert me.json()["detail"] == "Giriş yapmanız gerekiyor"


def test_deleted_staff_account_is_revoked(id_client):
    staff = _staff()
    tok = id_token(staff)
    id_run(deps.db.users.delete_one({"id": staff["id"]}))
    r = id_client.get(ADMIN_PING, headers=id_auth(tok))
    assert r.status_code == 401 and _code(r) == "session_revoked"
    assert _code(id_client.get(ME, headers=id_auth(tok))) == "session_revoked"


def test_logout_all_marks_other_device_tokens_revoked(id_client):
    staff = _staff()
    other_device, this_device = id_token(staff), id_token(staff)
    r = id_client.post("/api/auth/logout-all", headers=id_auth(this_device))
    assert r.status_code == 200, r.text
    fresh = r.json()["token"]
    old = id_client.get(ADMIN_PING, headers=id_auth(other_device))
    assert old.status_code == 401 and _code(old) == "session_revoked"
    assert old.json()["detail"] == "Oturum sonlandırıldı, tekrar giriş yapın"
    assert _code(id_client.get(ME, headers=id_auth(other_device))) == "session_revoked"
    good = id_client.get(ADMIN_PING, headers=id_auth(fresh))
    assert good.status_code == 200 and _code(good) is None


def test_admin_deactivation_endpoint_revokes_with_code(id_client):
    """Gerçek offboarding yolu: süper admin personeli panelden pasifleştirir."""
    sa = id_seed_user(kind="super")
    staff = _staff()
    tok = id_token(staff)
    r = id_client.put(f"/api/admin/users/{staff['id']}", json={"is_active": False},
                      headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    assert (id_find("users", {"id": staff["id"]}) or {}).get("is_active") is False
    res = id_client.get(ADMIN_PING, headers=id_auth(tok))
    assert res.status_code == 401 and _code(res) == "session_revoked"


def test_security_epoch_revocation_carries_code(id_client):
    sa = id_seed_user(kind="super")
    staff = _staff()
    staff_tok, cust_tok = id_token(staff), id_token(id_seed_user(kind="customer"))
    r = id_client.post("/api/admin/security/revoke-all-sessions", headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    s = id_client.get(ADMIN_PING, headers=id_auth(staff_tok))
    assert s.status_code == 401 and _code(s) == "session_revoked"
    c = id_client.get(ME, headers=id_auth(cust_tok))
    assert c.status_code == 401 and _code(c) == "session_revoked"


def test_garbage_or_expired_tokens_do_not_claim_revocation(id_client):
    staff = _staff()
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"user_id": staff["id"], "is_admin": True, "typ": "staff", "iat": now,
                         "exp": now + timedelta(hours=1), "iss": deps.JWT_ISSUER}, "x" * 64, algorithm="HS256")
    expired = jwt.encode({"user_id": staff["id"], "is_admin": True, "typ": "staff", "iat": now - timedelta(days=2),
                          "exp": now - timedelta(days=1), "iss": deps.JWT_ISSUER}, deps.JWT_SECRET, algorithm="HS256")
    for tok in (forged, expired, "not-a-jwt"):
        for path in (ADMIN_PING, ME):
            r = id_client.get(path, headers=id_auth(tok))
            assert r.status_code == 401
            assert _code(r) != "session_revoked", (path, tok[:12])
    # no token at all
    assert _code(id_client.get(ME)) is None


def test_idle_timeout_has_its_own_code(id_client):
    staff = _staff()
    id_set_rule("panel.auto_logout_minutes", 15)
    with freeze_time("2026-10-04 07:00:00") as frozen:
        tok = id_token(staff)
        assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
        frozen.tick(delta=timedelta(minutes=20))
        r = id_client.get(ADMIN_PING, headers=id_auth(tok))
        assert r.status_code == 401 and _code(r) == deps.SESSION_IDLE_CODE


def test_cors_exposes_auth_code_and_allows_app_headers(id_client):
    origin = next((o for o in server._origins_list if o != "*"), None)
    if not origin:
        pytest.skip("CORS wildcard configured in this environment")
    staff = _staff()
    tok = id_token(staff)
    id_update("users", {"id": staff["id"]}, {"$set": {"is_active": False}})
    r = id_client.get(ADMIN_PING, headers={**id_auth(tok), "Origin": origin})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == origin
    assert "x-auth-code" in r.headers.get("access-control-expose-headers", "").lower()
    pre = id_client.options(ADMIN_PING, headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-app-version,x-app-platform,x-app-target"})
    assert pre.status_code == 200, pre.text
    allowed = pre.headers.get("access-control-allow-headers", "").lower()
    for h in ("x-app-version", "x-app-platform", "x-app-target"):
        assert h in allowed


# ───────────────────────────── asgari uygulama sürümü ─────────────────────────────
def test_version_helpers():
    assert AVP.parse_version("1.2.3") == (1, 2, 3, 0)
    assert AVP.parse_version("v2") == (2, 0, 0, 0)
    assert AVP.parse_version("1.10.0") > AVP.parse_version("1.9.9")
    for bad in ("", None, "abc", "1..2", "1.2.3.4.5", "1.2-beta", "9" * 50):
        assert AVP.parse_version(bad) is None
    assert AVP.is_below_minimum("1.0.0", "") is False          # kural boş → zorlama yok
    assert AVP.is_below_minimum("1.0.0", "garbage") is False   # geçersiz kural → zorlama yok
    assert AVP.is_below_minimum("", "1.0.0") is True           # sürüm beyan etmeyen native → eski say
    assert AVP.is_below_minimum("1.0.0", "1.0") is False
    assert AVP.policy_rule_key("/api/admin/orders", "customer") == AVP.RULE_ADMIN
    assert AVP.policy_rule_key("/api/auth/me", "admin") == AVP.RULE_ADMIN
    assert AVP.policy_rule_key("/api/products", "customer") == AVP.RULE_CUSTOMER


def test_default_rules_are_empty_and_do_not_block_native(id_client):
    staff = _staff()
    tok = id_token(staff)
    r = id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native(version="0.0.1")})
    assert r.status_code == 200
    pub = id_client.get(PUBLIC_RULES).json()
    assert pub["app.min_version_admin"] == "" and pub["app.min_version_customer"] == ""


def test_old_admin_app_is_blocked_with_426_and_web_is_not(id_client):
    staff = _staff()
    tok = id_token(staff)
    id_set_rule("app.min_version_admin", "2.0.0")
    old = id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("ios", "1.9.9")})
    assert old.status_code == 426, old.text
    body = old.json()
    assert body["detail"] == "Uygulamanın güncellenmesi gerekiyor"
    assert body["code"] == "app_update_required" and body["min_version"] == "2.0.0"
    assert body["app"] == "admin" and body["store_url"] == ""
    assert old.headers.get("x-app-update-required") == "1"
    # Android, sürüm başlığı yok / bozuk → yine 426 (eski sürüm kapıyı atlayamaz)
    assert id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("android", None)}).status_code == 426
    assert id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("android", "latest")}).status_code == 426
    # yönetim uygulamasının admin-dışı uçları (açılış /auth/me) da kapsanır
    assert id_client.get(ME, headers={**id_auth(tok), **_native("ios", "1.0.0")}).status_code == 426
    # güncel / daha yeni sürüm geçer
    assert id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("ios", "2.0.0")}).status_code == 200
    assert id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("ios", "2.1")}).status_code == 200
    # web istemcisi (X-App-Platform yok / "web") ETKİLENMEZ
    assert id_client.get(ADMIN_PING, headers=id_auth(tok)).status_code == 200
    assert id_client.get(ADMIN_PING, headers={**id_auth(tok), **_native("web", "0.0.1")}).status_code == 200
    # sürüm sorgusu muaf (güncelleme ekranı mağaza bilgisini alabilsin)
    vc = id_client.get("/api/app/version-check?platform=ios&current_version=1.0.0", headers=_native("ios", "1.0.0"))
    assert vc.status_code == 200


def test_customer_rule_is_independent_and_carries_store_url(id_client):
    id_set_rule("app.min_version_admin", "5.0.0")
    # admin kuralı müşteri uygulamasını etkilemez
    assert id_client.get(PUBLIC_RULES, headers=_native("ios", "1.0.0", target="customer")).status_code == 200
    id_run(deps.db.settings.insert_one({"id": "app_versions", "versions": {
        "ios": {"store_url": "https://apps.apple.com/app/example/id1"},
        "android": {"store_url": "javascript:alert(1)"}}}))
    id_set_rule("app.min_version_customer", "1.2.0")
    r = id_client.get(PUBLIC_RULES, headers=_native("ios", "1.1.9", target="customer"))
    assert r.status_code == 426
    assert r.json()["app"] == "customer" and r.json()["store_url"] == "https://apps.apple.com/app/example/id1"
    # https olmayan mağaza bağlantısı istemciye verilmez
    a = id_client.get(PUBLIC_RULES, headers=_native("android", "1.0.0", target="customer"))
    assert a.status_code == 426 and a.json()["store_url"] == ""
    assert id_client.get(PUBLIC_RULES, headers=_native("ios", "1.2.0", target="customer")).status_code == 200
    assert id_client.get(PUBLIC_RULES).status_code == 200  # web


def test_426_carries_cors_headers_for_native_webview(id_client):
    origin = next((o for o in server._origins_list if o != "*"), None)
    if not origin:
        pytest.skip("CORS wildcard configured in this environment")
    id_set_rule("app.min_version_customer", "3.0.0")
    r = id_client.get(PUBLIC_RULES, headers={**_native("android", "1.0.0", target="customer"), "Origin": origin})
    assert r.status_code == 426
    assert r.headers.get("access-control-allow-origin") == origin


def test_invalid_rule_value_stored_directly_never_locks_anyone_out(id_client):
    staff = _staff()
    id_set_rule("app.min_version_admin", "bir-iki")
    assert id_client.get(ADMIN_PING, headers={**id_auth(id_token(staff)), **_native("ios", "0.1")}).status_code == 200


def test_min_version_rules_are_super_admin_only_validated_and_audited(id_client):
    id_seed_role("r_settings", ["settings.site", "admin.business_rules"])
    staff = id_seed_user(kind="staff", role_id="r_settings")
    sa = id_seed_user(kind="super")
    # rutin personel asgari sürümü düşüremez/değiştiremez
    r = id_client.put(RULES, json={"values": {"app.min_version_admin": "0.0.1"}}, headers=id_auth(id_token(staff)))
    assert r.status_code == 403
    # biçim doğrulaması
    for bad in ("abc", "1.2.3-beta", {"$gt": ""}, True):
        r = id_client.put(RULES, json={"values": {"app.min_version_admin": bad}}, headers=id_auth(id_token(sa)))
        assert r.status_code == 400, (bad, r.text)
    r = id_client.put(RULES, json={"values": {"app.min_version_admin": " 1.4.0 "}}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    import business_rules
    business_rules.invalidate()
    assert id_client.get(PUBLIC_RULES).json()["app.min_version_admin"] == "1.4.0"
    assert "business_rules.security_update" in id_audit_actions("business_rules.security_update")
    # boş = zorlamayı kapat (süper admin)
    r = id_client.put(RULES, json={"values": {"app.min_version_admin": ""}}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200
    # katalogda admin-düzenlenebilir olarak görünür
    groups = id_client.get(RULES, headers=id_auth(id_token(sa))).json()["groups"]
    keys = {x["key"] for g in groups for x in g["rules"]}
    assert {"app.min_version_admin", "app.min_version_customer"} <= keys
