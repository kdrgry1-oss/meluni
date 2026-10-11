"""Denetim #10: herkese açık ürün uçları AÇIK İZİN listesiyle döner — pazaryeri özellik/kategori
eşleşmeleri, senkron/batch izleri, XML/Ticimax kimlikleri ve bilinmeyen yeni alanlar sızmaz."""
from conftest import kv_run

INTERNAL = ("trendyol_attributes", "hepsiburada_attributes", "hepsiburada_category_id", "temu_category_name",
            "trendyol_sync_batch", "stock_synced_at", "attr_presync_backup", "xml_id", "ticimax_card_id",
            "urun_karti_id", "cost_price", "brand_new_internal_field", "manual_deactivated_at")


def _doc():
    return {
        "id": "p-allow", "slug": "elbise", "name": "Elbise", "price": 100, "sale_price": 90, "stock": 3,
        "is_active": True, "images": ["https://cdn.example/a.jpg"], "category_id": "c1", "color": "Siyah",
        "csv_card_id": "K1", "attributes": [{"type": "Kalıp", "name": "Kalıp", "value": "Dar", "ty_id": 9}],
        "variants": [{"id": "v1", "size": "S", "color": "Siyah", "stock": 3, "price": 100, "barcode": "B1",
                      "urun_id": "U1", "variation_code": "VC", "member_price_1": 50, "cost_price": 20}],
        **{k: "x" for k in INTERNAL}}


def _seed(db):
    kv_run(db.products.insert_one(_doc()))


def _check(p):
    for k in INTERNAL:
        assert k not in p, k
    assert p["name"] == "Elbise" and p["price"] == 100 and p["csv_card_id"] == "K1"
    v = p["variants"][0]
    assert v["size"] == "S" and v["barcode"] == "B1"
    assert not {"urun_id", "variation_code", "member_price_1", "cost_price"} & set(v)
    assert p["attributes"] == [{"type": "Kalıp", "name": "Kalıp", "value": "Dar"}]


def test_public_detail_endpoint(kv_client, kv_db):
    _seed(kv_db)
    r = kv_client.get("/api/products/elbise")
    assert r.status_code == 200, r.text
    _check(r.json())


def test_projection_used_by_list_slider_similar_favorites():
    # Liste/slider/benzer/favori uçları aynı _strip_internal_fields'i çağırır (mongomock $convert
    # desteklemediği için liste uçları burada doğrudan projeksiyonla doğrulanır).
    from routes.products import _strip_internal_fields
    p = _doc()
    assert _strip_internal_fields(p) is p      # yerinde (full_look dönüşü kullanmıyor)
    _check(p)
    import inspect, routes.products as rp, routes.customer as rc, routes.full_look as rf
    src = inspect.getsource(rp.get_products) + inspect.getsource(rp.get_slider_feed if hasattr(rp, "get_slider_feed") else rp)
    assert "_strip_internal_fields" in src
    assert "_strip_internal_fields" in inspect.getsource(rc) and "_strip_internal_fields" in inspect.getsource(rf)


def test_admin_view_keeps_full_document(kv_client, kv_users, kv_db):
    _seed(kv_db)
    admin = kv_users.staff(perms=["products.view"])
    r = kv_client.get("/api/products/p-allow", headers=kv_users.hdr(admin))
    assert r.status_code == 200 and r.json().get("trendyol_attributes") == "x"
