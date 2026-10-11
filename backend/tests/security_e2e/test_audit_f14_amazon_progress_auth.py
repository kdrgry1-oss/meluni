"""Denetim #14: Amazon toplu aktarım ilerleme ucu artık kimlik ister (iç katalog/tanılama sızmaz)."""
from conftest import kv_run


def test_progress_requires_integration_staff(kv_client, kv_users, kv_db):
    kv_run(kv_db.settings.insert_one({"id": "amazon_push_missing", "status": "done",
                                      "issues": [{"sku": "SKU-SECRET", "asin": "B0SECRET"}]}))
    r = kv_client.get("/api/amazon/spapi/auto/progress")
    assert r.status_code in (401, 403)
    assert "SKU-SECRET" not in r.text
    cust = kv_users.customer()
    assert kv_client.get("/api/amazon/spapi/auto/progress", headers=kv_users.hdr(cust)).status_code in (401, 403)
    st = kv_users.staff(perms=["integrations.view"])
    assert kv_client.get("/api/amazon/spapi/auto/progress", headers=kv_users.hdr(st)).status_code == 200
