"""Denetim #21: kişi-başı sınırlı kuponla açılan ödenmemiş kart siparişi, aynı kişinin yeni siparişiyle
"geçersiz kılınır" (ödenemez) → aynı hak iki siparişte ödenemez; müşteri yine de yeniden deneyebilir.
Statü/stok değişmez (auto-cancel süpürür — değişmez 4); ödeme durumu yazılmaz (değişmez 1)."""
import pytest

from pay_harness import (mock_3ds_init, now_iso, order_payload, pay_create_order, pay_find,
                         pay_init_3ds, pay_seed_product, pay_stock)

pytestmark = pytest.mark.asyncio


async def _seed():
    from routes import deps
    await pay_seed_product("p1", stock=10)
    await deps.db.coupons.insert_one({"id": "c-KISI1", "code": "KISI1", "type": "percent", "value": 10,
                                      "is_active": True, "usage_limit_per_user": 1, "auto_apply": False,
                                      "created_at": now_iso(days=-1)})


async def test_second_order_supersedes_first(pay_http, pay_env):
    await _seed()
    kw = dict(items=[{"product_id": "p1", "quantity": 1, "price": 1}], coupon_code="KISI1")
    r1 = await pay_create_order(pay_http, "credit_card", **kw)
    assert r1.status_code == 200, r1.text
    o1 = await pay_find("orders", {"id": r1.json()["order_id"]})
    assert any(a.get("coupon_id") == "c-KISI1" for a in o1["applied_promotions"])
    r2 = await pay_create_order(pay_http, "credit_card", **kw)        # yeniden deneme serbest
    assert r2.status_code == 200, r2.text
    o2 = await pay_find("orders", {"id": r2.json()["order_id"]})
    assert any(a.get("coupon_id") == "c-KISI1" for a in o2["applied_promotions"])
    o1 = await pay_find("orders", {"id": o1["id"]})
    assert o1["coupon_superseded_by"] == o2["id"]
    assert o1["status"] == "awaiting_payment" and o1.get("payment_status") != "paid"
    assert await pay_stock("p1") == 8                               # stok mantığı değişmedi
    mock_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, o1["id"])
    assert r.status_code == 409 and "indirim hakkınız" in r.text
    r = await pay_init_3ds(pay_http, o2["id"])
    assert r.status_code == 200 and r.json()["success"] is True, r.text


async def test_order_in_active_payment_holds_the_slot(pay_http, pay_env):
    await _seed()
    kw = dict(items=[{"product_id": "p1", "quantity": 1, "price": 1}], coupon_code="KISI1")
    o1 = await pay_find("orders", {"id": (await pay_create_order(pay_http, "credit_card", **kw)).json()["order_id"]})
    mock_3ds_init(pay_env.iyzico)
    assert (await pay_init_3ds(pay_http, o1["id"])).status_code == 200   # ödeme sürüyor (kilit)
    r2 = await pay_create_order(pay_http, "credit_card", **kw)
    # hak, ödemesi süren siparişte → yeni siparişte kupon geçersiz (tutar uyuşmazlığı/indirimsiz)
    if r2.status_code == 200:
        o2 = await pay_find("orders", {"id": r2.json()["order_id"]})
        assert not any(a.get("coupon_id") == "c-KISI1" and a.get("discount") for a in o2.get("applied_promotions") or [])
    o1 = await pay_find("orders", {"id": o1["id"]})
    assert not o1.get("coupon_superseded_by")
