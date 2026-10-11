"""Yalnız ölçü görseli olan üründe görseli silip kaydetmek kalıcıdır; işaretsiz boş galeri kaydı ezmez."""
import uuid

from conftest import KvUsers, kv_ip, kv_run


def _prod(db):
    pid = "p-" + uuid.uuid4().hex[:8]
    kv_run(db.products.insert_one({"id": pid, "name": "Rosso", "slug": f"r-{pid}", "price": 1, "stock": 0,
                                   "images": [{"url": "/uploads/olcu.png", "is_size_table": True}]}))
    return pid


def test_confirmed_clear_deletes_last_image_but_blank_payload_does_not(kv_client, kv_db, kv_users):
    hdr = {**KvUsers.hdr(kv_users.staff(perms=["products.view", "products.edit"])), **kv_ip()}
    a, b = _prod(kv_db), _prod(kv_db)
    # kaza eseri boş galeri (panel yarım yüklendi) → mevcut korunur
    assert kv_client.put(f"/api/products/{a}", json={"images": []}, headers=hdr).status_code == 200
    assert kv_run(kv_db.products.find_one({"id": a}))["images"]
    # kullanıcı son görseli bilerek sildi → boş kaydedilir, işaret ürüne yazılmaz
    assert kv_client.put(f"/api/products/{b}", json={"images": [], "images_clear_confirmed": True},
                         headers=hdr).status_code == 200
    doc = kv_run(kv_db.products.find_one({"id": b}))
    assert doc["images"] == [] and "images_clear_confirmed" not in doc
