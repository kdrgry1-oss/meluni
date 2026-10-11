"""Favorites + public product listing never leak internal (cost/supplier) fields."""
from __future__ import annotations

import pytest

from kv_harness import kv_insert, kv_find, kv_count, kv_all_keys, kv_mongomock_convert  # noqa: F401
from conftest import KvUsers, kv_run

INTERNAL = {"cost_price", "purchase_price", "alis_fiyati", "buy_price", "supplier", "tedarikci",
            "supplier_code", "member_price_1", "member_price_2", "member_price_3", "margin", "profit",
            "kar_marji", "ticimax_fields", "admin_notes", "internal_notes", "vendor", "vendor_id"}
SECRET_VALUES = ("ACME Tekstil", "GIZLI-NOT", "TF-MALIYET")

pytestmark = pytest.mark.usefixtures("kv_mongomock_convert")


def _product(pid, *, active=True, supplier="ACME Tekstil", **extra):
    d = {"id": pid, "name": f"Urun {pid}", "slug": pid, "price": 500.0, "sale_price": 450.0, "stock": 7,
         "is_active": active, "images": [f"https://cdn.kv-test.invalid/{pid}.webp"],
         "created_at": "2026-09-01T00:00:00+00:00",
         "cost_price": 123.0, "purchase_price": 120.0, "alis_fiyati": 121.0, "supplier": supplier,
         "tedarikci": supplier, "supplier_code": "SUP-1", "member_price_1": 400.0, "margin": 0.4,
         "admin_notes": "GIZLI-NOT", "internal_notes": "GIZLI-NOT", "ticimax_fields": {"MALIYET": "TF-MALIYET"},
         "vendor_id": "v1",
         "variants": [{"id": f"{pid}-m", "size": "M", "stock": 3, "barcode": "869000000001",
                       "cost_price": 99.0, "purchase_price": 98.0, "supplier": supplier}]}
    d.update(extra)
    return kv_insert("products", d)


def _assert_clean(payload, text):
    leaked = kv_all_keys(payload) & INTERNAL
    assert not leaked, f"internal fields leaked: {leaked}"
    for v in SECRET_VALUES:
        assert v not in text


def test_favorites_never_expose_internal_fields(kv_client, kv_users):
    _product("pa")
    cust = kv_users.customer()
    h = KvUsers.hdr(cust)
    assert kv_client.post("/api/favorites/pa", headers=h).status_code == 200
    r = kv_client.get("/api/favorites", headers=h)
    assert r.status_code == 200, r.text
    favs = r.json()["favorites"]
    assert [f["id"] for f in favs] == ["pa"] and favs[0]["price"] == 500.0
    _assert_clean(r.json(), r.text)
    assert r.headers.get("cache-control", "").startswith("no-store")


def test_inactive_or_deleted_or_missing_product_not_addable(kv_client, kv_users):
    _product("off", active=False)
    _product("del", is_deleted=True)
    h = KvUsers.hdr(kv_users.customer())
    for pid in ("off", "del", "nope", "$ne"):
        assert kv_client.post(f"/api/favorites/{pid}", headers=h).status_code == 404, pid
    r = kv_client.post("/api/favorites/merge", headers=h, json={"product_ids": ["off", "del", "nope", {"$ne": 1}]})
    assert r.status_code == 200 and r.json()["merged"] == 0
    assert kv_count("favorites") == 0


def test_product_deactivated_after_favoriting_disappears(kv_client, kv_users):
    _product("pb")
    h = KvUsers.hdr(kv_users.customer())
    kv_client.post("/api/favorites/pb", headers=h)
    from routes.deps import db
    kv_run(db.products.update_one({"id": "pb"}, {"$set": {"is_active": False}}))
    assert kv_client.get("/api/favorites", headers=h).json()["favorites"] == []


def test_favorites_are_per_user(kv_client, kv_users):
    _product("pc")
    a, b = kv_users.customer(), kv_users.customer()
    kv_client.post("/api/favorites/pc", headers=KvUsers.hdr(a))
    assert kv_client.get("/api/favorites", headers=KvUsers.hdr(b)).json()["favorites"] == []
    kv_client.delete("/api/favorites/pc", headers=KvUsers.hdr(b))  # B cannot remove A's
    assert kv_count("favorites", {"user_id": a["id"]}) == 1


def _list(client, headers=None, **params):
    r = client.get("/api/products", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    return r


@pytest.mark.parametrize("who", ["guest", "customer", "deactivated_staff", "roleless_staff",
                                 "revoked_token_staff", "customer_forged_flag"])
def test_public_list_hides_internal_fields_even_with_stale_admin_token(kv_client, kv_users, who):
    _product("p1")
    _product("p2", active=False)
    hdr = {}
    if who == "customer":
        hdr = KvUsers.hdr(kv_users.customer())
    elif who == "deactivated_staff":
        hdr = KvUsers.hdr(kv_users.staff(perms=["products.view"], is_active=False))
    elif who == "roleless_staff":
        st = kv_users.staff(perms=["products.view"])
        from routes.deps import db
        kv_run(db.users.update_one({"id": st["id"]}, {"$unset": {"role_id": ""}}))
        hdr = KvUsers.hdr(st)
    elif who == "revoked_token_staff":
        st = kv_users.staff(perms=["products.view"])
        hdr = KvUsers.hdr(st)
        from routes.deps import db
        kv_run(db.users.update_one({"id": st["id"]}, {"$set": {"token_version": 5}}))
    elif who == "customer_forged_flag":
        # a customer whose token says is_admin (e.g. stale flag) — DB says not staff
        c = kv_users.customer()
        from routes.deps import create_token
        hdr = {"Authorization": f"Bearer {create_token(c['id'], is_admin=True)}"}
    r = _list(kv_client, hdr, admin_view="1", status="all")
    ids = [p["id"] for p in r.json()["products"]]
    assert ids == ["p1"], ids   # inactive product NOT visible → admin view was NOT granted
    _assert_clean(r.json(), r.text)


def test_real_staff_admin_view_still_sees_internal_fields(kv_client, kv_users):
    """Control: proves the previous test would detect leakage."""
    _product("p1")
    _product("p2", active=False)
    st = kv_users.staff(perms=["products.view"])
    r = _list(kv_client, KvUsers.hdr(st), admin_view="1", status="all")
    ids = sorted(p["id"] for p in r.json()["products"])
    assert ids == ["p1", "p2"]
    assert "cost_price" in kv_all_keys(r.json())


def test_supplier_filter_ignored_for_public(kv_client, kv_users):
    _product("acme1", supplier="ACME Tekstil")
    _product("other1", supplier="Baska Firma")
    for hdr in ({}, KvUsers.hdr(kv_users.customer())):
        r = _list(kv_client, hdr, supplier="ACME")
        ids = sorted(p["id"] for p in r.json()["products"])
        assert ids == ["acme1", "other1"], ids   # filter NOT applied → no supplier↔product oracle
        _assert_clean(r.json(), r.text)
        # sorting by an internal field is not honoured either
        r2 = _list(kv_client, hdr, sort="ticimax_fields.MALIYET", order="asc")
        _assert_clean(r2.json(), r2.text)
    # staff admin view DOES filter
    st = KvUsers.hdr(kv_users.staff(perms=["products.view"]))
    r = _list(kv_client, st, supplier="ACME", admin_view="1")
    assert [p["id"] for p in r.json()["products"]] == ["acme1"]
