"""E2E (commerce-3): iş mantığı avı — mevcut testlerin kapsamadığı para/sipariş açıkları.

1. İade talebinde AYNI kalem index'i tekrarlanarak (items=[1,1]) ucuz kalem iade kaydında
   sipariş kalem sayısına şişirilir → onay "tüm sipariş" sanıp SİPARİŞİN TAMAMINI iade eder.
2. Public /orders/by-number (sıralı numara → herkes tarar) siparişte kullanılan HEDİYE ÇEKİ
   KODUNU (hamiline yazılı bakiye) ve iyzico paymentId'lerini sızdırır.
3. Sadakat puanı: "Ödemeli İptal Onaylandı" (cancel_refunded) siparişte kazanılan puan GERİ
   ALINMAZ → öde-iptal-iade döngüsüyle TL değerinde puan basılır.
4. Ödenmemiş sipariş sınırı (stok kilitleme koruması) müşterinin iptal TALEBİ açtığı (stoğu hâlâ
   tutan) ödenmemiş siparişleri saymıyordu → sınırsız stok kilitleme.
5. Promosyon motoru kategorisiz üründe kalemin İSTEMCİ category_id'sini kullanıyordu → kategoriye
   özel kampanya istenen ürüne uygulanabiliyordu.

Real FastAPI app over ASGI; MongoDB = mongomock; every outbound service mocked (pay_harness)."""
import pytest

from pay_harness import (
    CARD_TOTAL, SHIP, iyz_auth_ok, mock_3ds_auth, now_iso, pay_callback, pay_create_order,
    pay_customer, pay_drain, pay_find, pay_findall, pay_seed_product, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio


async def _paid_two_item_order(http, env, headers=None):
    """p1 = 500 TL (pahalı), p2 = 100 TL (ucuz); kartla ödenmiş + teslim edilmiş site siparişi."""
    await pay_seed_product("p1", stock=5, price=500.0)
    await pay_seed_product("p2", stock=5, price=100.0)
    r = await pay_create_order(http, headers=headers or {},
                               items=[{"product_id": "p1", "quantity": 1, "price": 1},
                                      {"product_id": "p2", "quantity": 1, "price": 1}])
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o, payment_id="pid-two-items"))
    await pay_callback(http, o["id"], "pid-two-items")
    await pay_drain()
    await pay_set("orders", {"id": o["id"]}, {"status": "delivered", "delivered_at": now_iso(days=-2)})
    o = await pay_find("orders", {"id": o["id"]})
    assert o["payment_status"] == "paid" and o["total"] == 650.0
    return o


# ═════════════════════════════ 1) iade: tekrarlanan kalem index'i ═════════════════════════════
async def test_return_request_repeated_line_index_cannot_inflate_to_full_order_refund(pay_http, pay_env):
    o = await _paid_two_item_order(pay_http, pay_env)
    # Müşteri yalnız UCUZ kalemi (index 1) iade ediyor ama index'i tekrarlıyor (+ negatif alias).
    r = await pay_http.post(f"/api/orders/by-number/{o['order_number']}/return-request",
                            json={"items": [1, 1, -1], "reason": "Beden uymadi", "email": SHIP["email"]})
    assert r.status_code == 200, r.text
    rid = r.json()["return"]["id"]
    rec = await pay_find("customer_returns", {"id": rid})
    assert [it["product_id"] for it in rec["items"]] == ["p2"], rec["items"]

    _, hdr = await pay_staff(["returns.approve"])
    pv = await pay_http.get(f"/api/orders/returns/{rid}/refund-preview", headers=hdr)
    assert pv.status_code == 200, pv.text
    assert pv.json()["breakdown"]["is_partial"] is True
    assert pv.json()["breakdown"]["auto_refund"] <= 100.0 + 0.01, pv.json()
    # Varsayılan onay (kalem seçimi göndermeden) siparişin tamamını iade ETMEZ.
    ap = await pay_http.post(f"/api/orders/returns/{rid}/approve", json={"fault": "store"}, headers=hdr)
    assert ap.status_code == 200, ap.text
    assert ap.json()["refund_amount"] <= 100.0 + 0.01, ap.json()
    # Stok da yalnız iade edilen 1 adet kadar geri gelir (p1'e dokunulmaz).
    assert (await pay_find("products", {"id": "p2"}))["stock"] == 5
    assert (await pay_find("products", {"id": "p1"}))["stock"] == 4


async def test_return_request_out_of_range_indexes_are_ignored(pay_http, pay_env):
    o = await _paid_two_item_order(pay_http, pay_env)
    r = await pay_http.post(f"/api/orders/by-number/{o['order_number']}/return-request",
                            json={"items": [0, -2, 7], "reason": "Begenmedim", "email": SHIP["email"]})
    assert r.status_code == 200, r.text
    rec = await pay_find("customer_returns", {"id": r.json()["return"]["id"]})
    assert [it["product_id"] for it in rec["items"]] == ["p1"], rec["items"]


# ═════════════════════════════ 2) by-number: hediye çeki kodu sızıntısı ═══════════════════════
async def test_public_by_number_does_not_leak_gift_card_code_or_payment_ids(pay_http, pay_env):
    from routes import deps
    code = "HED-LEAK-AAAA-0001"
    await deps.db.gift_cards.insert_one({"id": "gc-leak", "code": code, "status": "active", "kind": "gift",
                                         "value_type": "amount", "balance": 1000.0, "initial_amount": 1000.0,
                                         "transactions": [], "created_at": now_iso()})
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, items=[{"product_id": "p1", "quantity": 1, "price": 1}],
                               gift_card_code=code)
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["gift_card"]["code"] == code and o["gift_card"]["amount"] == CARD_TOTAL
    # Kalan bakiye (450 TL) hâlâ harcanabilir → kod gizli bir taşıyıcı değerdir.
    assert (await pay_find("gift_cards", {"code": code}))["balance"] == 450.0
    await pay_set("orders", {"id": o["id"]}, {
        "refunds": [{"amount": 10.0, "status": "done", "payment_id": "pid-secret-77"}],
        "duplicate_payments": [{"paymentId": "pid-secret-88", "status": "needs_refund"}]})

    r = await pay_http.get(f"/api/orders/by-number/{o['order_number']}")
    assert r.status_code == 200, r.text
    assert code not in r.text and "LEAK" not in r.text
    assert "pid-secret-77" not in r.text and "pid-secret-88" not in r.text
    # Sayfa için gereken tutar bilgisi kalır.
    assert r.json()["order_number"] == o["order_number"]


# ═════════════════════════════ 3) sadakat puanı: cancel_refunded geri alımı ════════════════════
async def test_loyalty_points_are_revoked_when_paid_order_is_cancelled_with_refund(pay_http, pay_env):
    from routes import deps
    from routes.loyalty import award_loyalty_points
    await deps.db.settings.insert_one({"id": "loyalty_state", "launched_at": now_iso(days=-30)})
    cust, hdr = await pay_customer(email="loyal@example.test")
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, headers=hdr, items=[{"product_id": "p1", "quantity": 1, "price": 1}])
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o, payment_id="pid-loyal"))
    await pay_callback(pay_http, o["id"], "pid-loyal")
    await pay_drain()
    assert (await pay_find("orders", {"id": o["id"]}))["status"] == "confirmed"

    await award_loyalty_points()
    earned = (await pay_find("users", {"id": cust["id"]})).get("loyalty_points") or 0
    assert earned > 0                                   # %2 × 550 = 11 puan

    # Müşteri iptal talebi → finans "Ödemeli İptal Onaylandı" (para karta iade edildi).
    await pay_set("orders", {"id": o["id"]}, {"status": "cancel_refunded", "payment_status": "refunded"})
    await award_loyalty_points()
    assert ((await pay_find("users", {"id": cust["id"]})).get("loyalty_points") or 0) == 0
    o2 = await pay_find("orders", {"id": o["id"]})
    assert o2.get("loyalty_revoked") is True
    assert [t["type"] for t in await pay_findall("loyalty_transactions", {"order_id": o["id"]})] == \
        ["earn", "revoke"]


# ═════════════════════════════ 4) ödenmemiş sipariş sınırı: iptal talebi ══════════════════════
async def test_unpaid_cap_counts_customer_cancel_requested_unpaid_orders(pay_http, pay_env):
    _, hdr = await pay_customer(email="locker@example.test")
    await pay_seed_product("p1", stock=50)
    for _ in range(3):
        r = await pay_create_order(pay_http, headers=hdr,
                                   items=[{"product_id": "p1", "quantity": 5, "price": 1}])
        assert r.status_code == 200, r.text
        c = await pay_http.post(f"/api/my-orders/{r.json()['order_id']}/cancel", json={}, headers=hdr)
        assert c.status_code == 200 and c.json()["status"] == "cancel_requested", c.text
    # 3 ödenmemiş iptal talebi stoğu hâlâ tutuyor (15 adet) → 4. sipariş sınırda reddedilir.
    assert (await pay_find("products", {"id": "p1"}))["stock"] == 35
    r = await pay_create_order(pay_http, headers=hdr, items=[{"product_id": "p1", "quantity": 5, "price": 1}])
    assert r.status_code == 429, r.text
    assert (await pay_find("products", {"id": "p1"}))["stock"] == 35


async def test_unpaid_cap_ignores_paid_cancel_requests(pay_http, pay_env):
    """Ödenmiş siparişin iptal talebi stok-kilitleme sayılmaz (meşru müşteri engellenmez)."""
    _, hdr = await pay_customer(email="payer@example.test")
    await pay_seed_product("p1", stock=50)
    for i in range(3):
        r = await pay_create_order(pay_http, headers=hdr, items=[{"product_id": "p1", "quantity": 1, "price": 1}])
        assert r.status_code == 200, r.text
        await pay_set("orders", {"id": r.json()["order_id"]},
                      {"status": "cancel_requested", "payment_status": "paid"})
    r = await pay_create_order(pay_http, headers=hdr, items=[{"product_id": "p1", "quantity": 1, "price": 1}])
    assert r.status_code == 200, r.text


# ═════════════════════════════ 5) promo motoru: istemci category_id ═══════════════════════════
async def test_client_category_id_cannot_pull_uncategorised_product_into_category_campaign(pay_http, pay_env):
    from routes import deps
    await pay_seed_product("p9", stock=5, price=500.0, category_id="")
    await deps.db.coupons.insert_one({
        "id": "camp-sale", "code": "", "title": "Outlet %50", "type": "percent", "value": 50,
        "categories": ["outlet-cat"], "products": [], "is_active": True, "auto_apply": True,
        "combinable": True, "priority": 0, "created_at": now_iso()})
    r = await pay_create_order(pay_http, items=[{"product_id": "p9", "quantity": 1, "price": 1,
                                                 "category_id": "outlet-cat"}])
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o["discount"] == 0.0 and o["total"] == CARD_TOTAL, (o["discount"], o["total"])
    assert all(p.get("coupon_id") != "camp-sale" for p in o.get("applied_promotions") or [])


# ═════════════════════════════ 6) kalem başı adet tavanı: bölünmüş kalemler ═══════════════════
async def test_per_product_qty_cap_cannot_be_bypassed_by_splitting_lines(pay_http, pay_env):
    import business_rules
    from routes import deps
    # Kural anahtarı noktalı → düz 'values' sözlüğü olarak yazılır (pay_set_rule iç içe yazar).
    await deps.db.settings.update_one({"id": "business_rules"},
                                      {"$set": {"id": "business_rules", "values": {"order.max_qty_per_item": 5}}},
                                      upsert=True)
    business_rules.invalidate()
    await pay_seed_product("p1", stock=50)
    r = await pay_create_order(pay_http, items=[{"product_id": "p1", "quantity": 5, "price": 1},
                                                {"product_id": "p1", "quantity": 5, "price": 1}])
    assert r.status_code == 400, r.text
    assert await pay_findall("orders") == [] and (await pay_find("products", {"id": "p1"}))["stock"] == 50
    # Sınır içinde (toplam 5) bölünmüş kalemler kabul edilir.
    r = await pay_create_order(pay_http, items=[{"product_id": "p1", "quantity": 2, "price": 1},
                                                {"product_id": "p1", "quantity": 3, "price": 1}])
    assert r.status_code == 200, r.text
    assert (await pay_find("products", {"id": "p1"}))["stock"] == 45
