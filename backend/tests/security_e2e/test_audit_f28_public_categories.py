"""Denetim #28: herkese açık kategori uçları pasif, test, üyelere-özel kayıtları ve pazaryeri
eşleşmelerini döndürmez; yönetim görünümü yalnız personele."""
from conftest import kv_run


def _seed(db):
    kv_run(db.categories.insert_many([
        {"id": "a", "name": "Elbise", "slug": "elbise", "is_active": True, "trendyol_category_id": 1,
         "hepsiburada_category_name": "HB-Gizli", "attribute_mapping": {"x": 1}, "note": "iç not"},
        {"id": "p", "name": "Yeni Sezon Taslak", "slug": "taslak", "is_active": False},
        {"id": "m", "name": "VIP", "slug": "vip", "is_active": True, "members_only": True,
         "trendyol_category_id": 7},
    ]))


def test_guest_list_and_detail(kv_client, kv_db):
    _seed(kv_db)
    for url in ("/api/categories", "/api/categories?is_active=false", "/api/categories?visible_only=false"):
        rows = kv_client.get(url).json()
        ids = {c["id"] for c in rows}
        assert ids == {"a"}, (url, ids)
        assert set(rows[0]) <= {"id", "name", "slug", "parent_id", "full_name", "is_active", "slug_aliases",
                                "sort_order", "image", "image_url", "description", "members_only"}
    assert kv_client.get("/api/categories/taslak").status_code == 404
    vip = kv_client.get("/api/categories/vip").json()
    assert vip["members_only_effective"] is True and "trendyol_category_id" not in vip
    assert "hepsiburada_category_name" not in kv_client.get("/api/categories/elbise").json()


def test_staff_gets_management_view(kv_client, kv_users, kv_db):
    _seed(kv_db)
    st = kv_users.staff(perms=["products.view"])
    rows = kv_client.get("/api/categories", headers=kv_users.hdr(st)).json()
    assert {c["id"] for c in rows} == {"a", "p", "m"}
    assert any(c.get("trendyol_category_id") for c in rows)
