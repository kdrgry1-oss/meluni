"""Denetim #8: kargo/e-fatura sağlayıcı bağlamalarını yalnız integrations.edit yazabilir; görüntüleme
yetkisi yazamaz; gönderilmeyen provider/sır KORUNUR; şema dışı alan yazılmaz; değişiklik denetlenir."""
from conftest import kv_run
from security.crypto import decrypt


def _seed(db):
    from security.crypto import encrypt
    kv_run(db.providers_config.insert_one({"kind": "cargo", "active_provider": "mng", "providers": {
        "mng": {"customer_number": "111", "username": "u", "password": encrypt("OrigPw-1")},
        "yurtici": {"username": "y"}}}))


def test_view_only_cannot_overwrite(kv_client, kv_users, kv_db):
    _seed(kv_db)
    viewer = kv_users.staff(perms=["integrations.view", "settings.view"])
    r = kv_client.post("/api/provider-settings/cargo/config", headers=kv_users.hdr(viewer),
                       json={"active_provider": "mng", "providers": {"mng": {"username": "evil"}}})
    assert r.status_code == 403
    assert kv_run(kv_db.providers_config.find_one({"kind": "cargo"}))["providers"]["mng"]["username"] == "u"


def test_editor_merge_preserves_secrets_and_other_providers(kv_client, kv_users, kv_db):
    _seed(kv_db)
    ed = kv_users.staff(perms=["integrations.edit", "integrations.view"])
    r = kv_client.post("/api/provider-settings/cargo/config", headers=kv_users.hdr(ed),
                       json={"active_provider": "mng",
                             "providers": {"mng": {"username": "u2", "password": "********", "evil_url": "http://x"}}})
    assert r.status_code == 200, r.text
    doc = kv_run(kv_db.providers_config.find_one({"kind": "cargo"}))
    mng = doc["providers"]["mng"]
    assert mng["username"] == "u2" and mng["customer_number"] == "111"
    assert decrypt(mng["password"]) == "OrigPw-1"
    assert "evil_url" not in mng
    assert doc["providers"]["yurtici"] == {"username": "y"}
    audits = kv_run(kv_db.audit_logs.find({"action": "provider_settings.update"}).to_list(10)) or \
        kv_run(kv_db.activity_audit.find({"action": "provider_settings.update"}).to_list(10))
    assert audits, "değişiklik denetim kaydına yazılmalı"
    assert "OrigPw-1" not in str(audits) and "u2" not in str(audits)


def test_field_types_validated(kv_client, kv_users, kv_db):
    _seed(kv_db)
    ed = kv_users.staff(perms=["integrations.edit", "integrations.view"])
    h = kv_users.hdr(ed)
    url = "/api/provider-settings/cargo/config"
    r = kv_client.post(url, headers=h, json={"active_provider": "mng", "providers": {"mng": {"env": "hack"}}})
    assert r.status_code == 400 and "seçim" in r.text
    assert kv_client.post(url, headers=h, json={"active_provider": "mng",
                                                "providers": {"mng": {"env": "prod"}}}).status_code == 200
    eu = "/api/provider-settings/einvoice/config"
    for bad in ("http://example.com/api", "https://127.0.0.1/x", "https://169.254.169.254/latest"):
        r = kv_client.post(eu, headers=h, json={"active_provider": "mikro", "providers": {"mikro": {"api_url": bad}}})
        assert r.status_code == 400, bad
    r = kv_client.post(eu, headers=h, json={"active_provider": "mikro",
                                            "providers": {"mikro": {"api_url": "https://api.mikro.example/v1"}}})
    assert r.status_code == 200, r.text
