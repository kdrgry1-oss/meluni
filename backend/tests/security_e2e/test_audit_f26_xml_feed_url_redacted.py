"""Denetim #26: XML feed durumu token'lı tedarikçi adresini döndürmez; DB'de düz saklanmaz; yalnız
XML yetkili personel görür."""
from conftest import kv_run

TOKEN_URL = "https://supplier.example/feed.xml?token=SUPERSECRET123&u=me"


def test_status_redacted_and_scoped(kv_client, kv_users, kv_db):
    kv_run(kv_db.settings.insert_one({"id": "xml_feed", "xml_url": TOKEN_URL, "last_sync": "x"}))
    viewer = kv_users.staff(perms=["integrations.view", "integrations.trendyol"])
    assert kv_client.get("/api/integrations/xml/status", headers=kv_users.hdr(viewer)).status_code == 403
    xm = kv_users.staff(perms=["integrations.xml_export"])
    r = kv_client.get("/api/integrations/xml/status", headers=kv_users.hdr(xm))
    assert r.status_code == 200, r.text
    assert "SUPERSECRET123" not in r.text and r.json()["xml_url"] == "https://supplier.example/feed.xml"


def test_startup_job_redacts_stored_url(kv_db):
    import scheduler
    kv_run(kv_db.settings.insert_one({"id": "xml_feed", "xml_url": TOKEN_URL}))
    kv_run(scheduler._ensure_provider_secrets_encrypted())
    doc = kv_run(kv_db.settings.find_one({"id": "xml_feed"}))
    assert "xml_url" not in doc and doc["xml_url_display"] == "https://supplier.example/feed.xml"
