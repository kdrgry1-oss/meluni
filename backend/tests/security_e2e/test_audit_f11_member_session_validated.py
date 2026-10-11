"""Denetim #11: üyelere özel katalog kararı yalnız JWT imzasına değil, DOĞRULANMIŞ oturuma dayanır —
pasif edilmiş, token_version'ı artmış (şifre sıfırlama) kullanıcı ya da küresel oturum iptali
sonrası eski token misafir sayılır."""
import routes.deps as deps
from conftest import kv_run


def _seed(db):
    kv_run(db.categories.insert_one({"id": "mo", "name": "VIP", "slug": "vip", "members_only": True, "is_active": True}))
    kv_run(db.products.insert_one({"id": "vp", "slug": "vip-urun", "name": "VIP Ürün", "price": 10,
                                   "is_active": True, "category_id": "mo", "images": ["https://x/y.jpg"]}))


def _detail(c, hdr=None):
    return c.get("/api/products/vip-urun", headers=hdr or {})


def test_member_sees_until_revoked(kv_client, kv_users, kv_db):
    _seed(kv_db)
    u = kv_users.customer()
    hdr = kv_users.hdr(u)
    assert _detail(kv_client).status_code == 404                  # misafir
    assert _detail(kv_client, hdr).status_code == 200             # üye
    # şifre sıfırlama → token_version artar
    kv_run(kv_db.users.update_one({"id": u["id"]}, {"$inc": {"token_version": 1}}))
    deps._MEMBER_CACHE.clear()
    assert _detail(kv_client, hdr).status_code == 404


def test_disabled_user_is_guest(kv_client, kv_users, kv_db):
    _seed(kv_db)
    u = kv_users.customer()
    hdr = kv_users.hdr(u)
    assert _detail(kv_client, hdr).status_code == 200
    kv_run(kv_db.users.update_one({"id": u["id"]}, {"$set": {"is_active": False}}))
    deps._MEMBER_CACHE.clear()
    assert _detail(kv_client, hdr).status_code == 404
    r = kv_client.get("/api/categories", headers=hdr)
    assert r.status_code == 200 and all(c.get("id") != "mo" for c in (r.json() if isinstance(r.json(), list) else r.json().get("categories", [])))


def test_sync_helper_fails_closed_without_middleware():
    class _R:
        scope = {}
        headers = {"authorization": "Bearer whatever"}
    assert deps.request_member_ok(_R()) is False
