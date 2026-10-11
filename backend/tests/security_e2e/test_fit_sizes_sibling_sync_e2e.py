"""Standart bedenli üründe 'Uyduğu Bedenler' seçimi aynı stok kodlu renk kardeşlerine yansır."""
import uuid

from conftest import KvUsers, kv_ip, kv_run


def _prod(db, stock_code, sizes, color, **extra):
    pid = "p-" + uuid.uuid4().hex[:10]
    doc = {"id": pid, "name": f"Elbise {color}", "slug": f"elbise-{pid}", "stock_code": stock_code,
           "color": color, "price": 100, "stock": 1, "is_active": True,
           "variants": [{"id": f"{pid}-{i}", "size": s, "color": color, "stock": 1, "barcode": f"{pid}{i}"}
                        for i, s in enumerate(sizes)]}
    doc.update(extra)
    kv_run(db.products.insert_one(dict(doc)))
    return pid


def _get(db, pid):
    return kv_run(db.products.find_one({"id": pid}, {"_id": 0}))


def test_fit_sizes_propagate_to_standard_sized_color_siblings(kv_client, kv_db, kv_users):
    sc = "FS-" + uuid.uuid4().hex[:6]
    src = _prod(kv_db, sc, ["STD"], "Siyah")
    sib = _prod(kv_db, sc, ["Standart"], "Kırmızı", fit_sizes=["XS"])
    non_std = _prod(kv_db, sc, ["S", "M"], "Mavi")
    other = _prod(kv_db, "OTHER-" + sc, ["STD"], "Siyah")
    hdr = {**KvUsers.hdr(kv_users.staff(perms=["products.view", "products.edit"])), **kv_ip()}

    r = kv_client.put(f"/api/products/{src}", json={"fit_sizes": ["l", "S", "M"]}, headers=hdr)
    assert r.status_code == 200, r.text
    assert _get(kv_db, src)["fit_sizes"] == ["L", "S", "M"]
    assert _get(kv_db, sib)["fit_sizes"] == ["L", "S", "M"]
    assert "fit_sizes" not in _get(kv_db, non_std)      # standart bedenli olmayan kardeşe yazılmaz
    assert "fit_sizes" not in _get(kv_db, other)        # başka model (stok kodu) etkilenmez

    # Kardeş kendi seçimini değiştirir; kaynakta İLGİSİZ alan kaydı bu seçimi EZMEZ.
    r = kv_client.put(f"/api/products/{sib}", json={"fit_sizes": ["M"]}, headers=hdr)
    assert r.status_code == 200, r.text
    assert _get(kv_db, src)["fit_sizes"] == ["M"]
    r = kv_client.put(f"/api/products/{src}", json={"fit_sizes": ["M"], "description": "x"}, headers=hdr)
    assert r.status_code == 200, r.text
    kv_run(kv_db.products.update_one({"id": sib}, {"$set": {"fit_sizes": ["XS", "S"]}}))
    r = kv_client.put(f"/api/products/{src}", json={"fit_sizes": ["M"], "description": "y"}, headers=hdr)
    assert r.status_code == 200, r.text
    assert _get(kv_db, sib)["fit_sizes"] == ["XS", "S"]

    # Değişmeyen seçimle kayıt: seçimi BOŞ kardeş doldurulur, dolu kardeş korunur.
    empty_sib = _prod(kv_db, sc, ["STD"], "Bej")
    r = kv_client.put(f"/api/products/{src}", json={"fit_sizes": ["M"], "description": "z"}, headers=hdr)
    assert r.status_code == 200, r.text
    assert _get(kv_db, empty_sib)["fit_sizes"] == ["M"]
    assert _get(kv_db, sib)["fit_sizes"] == ["XS", "S"]

    # Boş seçim gruba yayılmaz.
    r = kv_client.put(f"/api/products/{src}", json={"fit_sizes": []}, headers=hdr)
    assert r.status_code == 200, r.text
    assert _get(kv_db, src)["fit_sizes"] == []
    assert _get(kv_db, sib)["fit_sizes"] == ["XS", "S"]
