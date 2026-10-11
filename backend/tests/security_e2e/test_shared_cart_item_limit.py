"""Paylaşılan sepet: sipariş tavanı (100 kalem) kadar kalem paylaşılabilir; fazlası reddedilir."""
from conftest import kv_run, kv_ip


def _seed(db, n):
    kv_run(db.products.insert_many([{"id": f"sp{i}", "name": f"Ürün {i}", "price": 10, "is_active": True}
                                     for i in range(n)]))


def test_59_items_shareable_101_refused(kv_client, kv_db):
    _seed(kv_db, 101)
    items = [{"product_id": f"sp{i}", "quantity": 1} for i in range(59)]
    r = kv_client.post("/api/shared-carts", json={"items": items}, headers=kv_ip())
    assert r.status_code == 200, r.text
    items = [{"product_id": f"sp{i}", "quantity": 1} for i in range(101)]
    assert kv_client.post("/api/shared-carts", json={"items": items}, headers=kv_ip()).status_code == 400
