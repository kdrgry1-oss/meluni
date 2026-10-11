"""Entegrasyon logları: `platform` alanıyla yazılan kayıtlar (ör. Trendyol aynı gün kargo) pazaryeri
filtresinde görünür."""
from conftest import KvUsers, kv_ip, kv_run


def test_platform_field_logs_are_listed_under_marketplace_filter(kv_client, kv_db, kv_users):
    from routes.integrations_common import log_integration_event as legacy_log
    from routes.marketplace_hub import log_integration_event as hub_log
    kv_run(legacy_log("trendyol", "delivery_option", "bulk", "120", "success", "Termin: Aynı gün → 120 ürün"))
    kv_run(hub_log(marketplace="trendyol", action="order_pull", status="success", message="cron"))
    kv_run(hub_log(marketplace="hepsiburada", action="order_pull", status="success", message="hb"))
    hdr = {**KvUsers.hdr(kv_users.staff(super_admin=True)), **kv_ip()}
    r = kv_client.get("/api/marketplace-hub/logs?marketplace=trendyol", headers=hdr)
    assert r.status_code == 200, r.text
    logs = r.json()["logs"]
    assert {l["action"] for l in logs} == {"delivery_option", "order_pull"}
    assert all(l["marketplace"] == "trendyol" for l in logs)
    r = kv_client.get("/api/marketplace-hub/logs?marketplace=trendyol&action=delivery_option", headers=hdr)
    assert [l["message"] for l in r.json()["logs"]] == ["Termin: Aynı gün → 120 ürün"]
    # kayıt kendisi değişmez (freshness izleyicisi `marketplace` alanına bakar)
    assert "marketplace" not in kv_run(kv_db.integration_logs.find_one({"action": "delivery_option"}))
