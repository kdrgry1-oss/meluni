"""Denetim #29: yayınlanmamış blok/banner/tema taslakları vitrine (personel dışına) dönmez; aktif tema
yedeği rastgele taslak seçmez."""
from conftest import kv_run


def test_blocks_and_banners_hidden_until_published(kv_client, kv_users, kv_db):
    kv_run(kv_db.page_blocks.insert_many([
        {"id": "b-live", "page": "home", "is_active": True, "sort_order": 1},
        {"id": "b-draft", "page": "home", "is_active": False, "sort_order": 2, "title": "Gizli kampanya"}]))
    kv_run(kv_db.banners.insert_many([
        {"id": "bn-live", "is_active": True},
        {"id": "bn-off", "is_active": False},
        {"id": "bn-future", "is_active": True, "start_date": "2099-01-01T00:00:00+00:00"}]))
    for url in ("/api/page-blocks?page=home", "/api/page-blocks?page=home&is_active=false"):
        assert {b["id"] for b in kv_client.get(url).json()} == {"b-live"}, url
    assert kv_client.get("/api/page-blocks/b-draft").status_code == 404
    assert {b["id"] for b in kv_client.get("/api/banners?is_active=false").json()} == {"bn-live"}
    assert {b["id"] for b in kv_client.get("/api/banners").json()} == {"bn-live"}
    assert kv_client.get("/api/banners/bn-future").status_code == 404
    st = kv_users.staff(perms=["tasarim.view"])
    assert len(kv_client.get("/api/page-blocks?page=home&all=1", headers=kv_users.hdr(st)).json()) == 2
    assert len(kv_client.get("/api/banners", headers=kv_users.hdr(st)).json()) == 3


def test_theme_drafts_and_fallback(kv_client, kv_users, kv_db, monkeypatch):
    import routes.themes as th

    async def _noseed():
        return None
    monkeypatch.setattr(th, "_ensure_seed", _noseed)
    kv_run(kv_db.themes.insert_many([
        {"id": "t-draft", "slug": "taslak", "name": "Taslak", "is_active": False, "blocks": []}]))
    assert kv_client.get("/api/storefront/themes/taslak").status_code == 404
    assert kv_client.get("/api/storefront/themes/active").status_code == 404    # rastgele taslak seçilmez
    st = kv_users.staff(perms=["tasarim.view"])
    assert kv_client.get("/api/storefront/themes/taslak", headers=kv_users.hdr(st)).status_code == 200
