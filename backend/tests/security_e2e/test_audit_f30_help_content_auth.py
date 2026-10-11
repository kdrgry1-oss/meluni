"""Denetim #30: panel eğitim/yardım içeriği kimliksiz okunamaz."""
from conftest import kv_run


def test_help_content_requires_staff(kv_client, kv_users, kv_db):
    kv_run(kv_db.settings.insert_one({"id": "help_content", "sections": [{"key": "iade", "title": "İç iade süreci"}]}))
    assert kv_client.get("/api/help-content").status_code in (401, 403)
    cust = kv_users.customer()
    assert kv_client.get("/api/help-content", headers=kv_users.hdr(cust)).status_code in (401, 403)
    st = kv_users.staff(perms=["orders.view"])
    r = kv_client.get("/api/help-content", headers=kv_users.hdr(st))
    assert r.status_code == 200 and r.json()["sections"][0]["key"] == "iade"
