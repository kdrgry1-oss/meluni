"""Denetim #6: İYS şifre/appkey eş adları (iys_password, iys_appkey, appkey) at-rest şifrelenir,
okuma uçlarında ASLA düz dönmez; sağlayıcı sınırında (decrypt_provider_block) çözülür."""
from conftest import kv_run
from notification_service import decrypt_provider_block, encrypt_legacy_provider_secrets
from security.crypto import is_encrypted

SECRET = "-".join(["iys", "test", "deger", "bir"])      # sahte test değeri
APPKEY = "-".join(["iys", "test", "deger", "iki"])      # sahte test değeri


def _seed_plain(db):
    kv_run(db.settings.insert_one({"id": "notification_providers", "providers": {"netgsm": {
        "username": "8500000000", "iys_password": SECRET, "iys_appkey": APPKEY, "appkey": APPKEY}}}))


def test_view_only_never_sees_plaintext(kv_client, kv_users, kv_db):
    _seed_plain(kv_db)
    viewer = kv_users.staff(perms=["settings.view", "integrations.view"])
    for url in ("/api/notifications/providers", "/api/admin/iys/settings"):
        r = kv_client.get(url, headers=kv_users.hdr(viewer))
        assert r.status_code == 200, (url, r.text)
        assert SECRET not in r.text and APPKEY not in r.text, url


def test_legacy_plaintext_encrypted_and_still_usable(kv_db):
    _seed_plain(kv_db)
    assert kv_run(encrypt_legacy_provider_secrets(kv_db)) == 3
    blk = kv_run(kv_db.settings.find_one({"id": "notification_providers"}))["providers"]["netgsm"]
    assert all(is_encrypted(blk[f]) for f in ("iys_password", "iys_appkey", "appkey"))
    assert blk["username"] == "8500000000"
    dec = decrypt_provider_block(blk)
    assert dec["iys_password"] == SECRET and dec["iys_appkey"] == APPKEY
    assert kv_run(encrypt_legacy_provider_secrets(kv_db)) == 0      # idempotent


def test_iys_settings_post_encrypts_and_masked_roundtrip_keeps_value(kv_client, kv_users, kv_db):
    admin = kv_users.staff(perms=["integrations.netgsm", "integrations.view"])
    h = kv_users.hdr(admin)
    assert kv_client.post("/api/admin/iys/settings", json={"brand_code": "123", "appkey": APPKEY}, headers=h).status_code == 200
    blk = kv_run(kv_db.settings.find_one({"id": "notification_providers"}))["providers"]["netgsm"]
    assert is_encrypted(blk["iys_appkey"])
    masked = kv_client.get("/api/admin/iys/settings", headers=h).json()["appkey"]
    assert "****" in masked
    kv_client.post("/api/admin/iys/settings", json={"brand_code": "123", "appkey": masked}, headers=h)
    blk2 = kv_run(kv_db.settings.find_one({"id": "notification_providers"}))["providers"]["netgsm"]
    assert decrypt_provider_block(blk2)["iys_appkey"] == APPKEY
