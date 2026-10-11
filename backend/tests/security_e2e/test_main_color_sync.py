"""Tek renkli ürün kartında 'Renk (Ana)' değişince varyant renkleri + Renk/Web Color özellikleri de değişir."""
import uuid

from conftest import KvUsers, kv_ip, kv_run


def _ins(db, color, variants, attrs):
    pid = "p-" + uuid.uuid4().hex[:8]
    kv_run(db.products.insert_one({"id": pid, "name": "Nova Gömlek", "slug": f"nova-{pid}", "color": color,
                                   "stock_code": "SC-" + pid, "price": 100, "stock": 3, "is_active": True,
                                   "variants": variants, "attributes": attrs}))
    return pid


def _get(db, pid):
    return kv_run(db.products.find_one({"id": pid}, {"_id": 0}))


def test_main_color_change_updates_single_color_variants_and_attrs(kv_client, kv_db, kv_users):
    hdr = {**KvUsers.hdr(kv_users.staff(perms=["products.view", "products.edit"])), **kv_ip()}
    vs = [{"id": "v1", "size": "S", "color": "Ekru", "stock": 1, "barcode": "B1"},
          {"id": "v2", "size": "M", "color": "", "stock": 2, "barcode": "B2"}]
    attrs = [{"name": "Renk", "value": "Ekru"}, {"name": "Web Color", "value": "ekru"}, {"name": "Kumaş", "value": "Pamuk"}]
    pid = _ins(kv_db, "Ekru", vs, attrs)
    r = kv_client.put(f"/api/products/{pid}", json={"color": "Lacivert", "variants": vs, "attributes": attrs}, headers=hdr)
    assert r.status_code == 200, r.text
    p = _get(kv_db, pid)
    assert p["color"] == "Lacivert"
    assert [v["color"] for v in p["variants"]] == ["Lacivert", "Lacivert"]
    assert [v["barcode"] for v in p["variants"]] == ["B1", "B2"]          # barkod/kimlik korunur
    assert {a["name"]: a["value"] for a in p["attributes"]} == {"Renk": "Lacivert", "Web Color": "Lacivert", "Kumaş": "Pamuk"}


def test_multi_color_legacy_card_variants_untouched(kv_client, kv_db, kv_users):
    hdr = {**KvUsers.hdr(kv_users.staff(perms=["products.view", "products.edit"])), **kv_ip()}
    vs = [{"id": "v1", "size": "S", "color": "Ekru", "stock": 1}, {"id": "v2", "size": "S", "color": "Siyah", "stock": 1}]
    pid = _ins(kv_db, "Ekru", vs, [])
    r = kv_client.put(f"/api/products/{pid}", json={"color": "Lacivert", "variants": vs}, headers=hdr)
    assert r.status_code == 200, r.text
    assert [v["color"] for v in _get(kv_db, pid)["variants"]] == ["Ekru", "Siyah"]
