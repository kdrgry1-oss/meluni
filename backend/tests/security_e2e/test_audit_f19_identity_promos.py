"""Denetim #19: ilk-sipariş/öğrenci/muafiyet indirimleri kasaya yazılan doğrulanmamış e-postaya değil
hesaba bağlıdır; önceki sipariş iç içe e-posta alanında ve '+etiket' varyantlarında da bulunur."""
from conftest import kv_run
from routes.coupons import _evaluate_single, evaluate_cart_promotions

FIRST = {"id": "c-first", "code": "ILK10", "is_active": True, "type": "percent", "value": 10,
         "first_order_only": True}


def test_guest_cannot_use_first_order_with_fresh_email(kv_db):
    r = kv_run(_evaluate_single(FIRST, 500.0, [], None, "fresh-123@example.com"))
    assert r["valid"] is False and "giriş" in r["reason"]


def test_prior_order_found_via_nested_email_and_plus_alias(kv_db):
    kv_run(kv_db.users.insert_one({"id": "u1", "email": "ayse@example.com", "email_verified": True}))
    kv_run(kv_db.orders.insert_one({"id": "o-old", "user_id": None, "payment_status": "paid", "status": "delivered",
                                    "shipping_address": {"email": "Ayse+promo@Example.com"}}))
    r = kv_run(_evaluate_single(FIRST, 500.0, [], "u1", "ayse@example.com"))
    assert r["valid"] is False and "ilk" in r["reason"].lower()


def test_exemption_only_for_account_email(kv_db):
    kv_run(kv_db.settings.insert_one({"id": "coupon_first_order_exceptions", "emails": ["vip@example.com"]}))
    import routes.coupons as cp
    cp._EXEMPT_CACHE["at"] = 0
    # misafir muaf e-postayı yazarak ilk-sipariş kuralını atlayamaz
    assert kv_run(_evaluate_single(FIRST, 500.0, [], None, "vip@example.com"))["valid"] is False


def test_edu_discount_requires_verified_account_email(kv_db):
    items = [{"product_id": "p", "qty": 1, "price": 1000.0}]
    g = kv_run(evaluate_cart_promotions(1000.0, items, user_id=None, email="ogr@uni.edu.tr"))
    assert g["edu_discount"] == 0
    kv_run(kv_db.users.insert_one({"id": "u2", "email": "ogr@uni.edu.tr", "email_verified": False}))
    assert kv_run(evaluate_cart_promotions(1000.0, items, user_id="u2", email="ogr@uni.edu.tr"))["edu_discount"] == 0
    kv_run(kv_db.users.update_one({"id": "u2"}, {"$set": {"email_verified": True}}))
    assert kv_run(evaluate_cart_promotions(1000.0, items, user_id="u2", email="x@gmail.com"))["edu_discount"] > 0


def test_apply_ignores_client_user_id(kv_client, kv_db):
    kv_run(kv_db.users.insert_one({"id": "victim", "email": "v@example.com", "email_verified": True}))
    kv_run(kv_db.coupons.insert_one({**FIRST, "code": "ILK10"}))
    r = kv_client.post("/api/coupons/apply", json={"code": "ILK10", "cart_total": 500, "items": [],
                                                   "user_id": "victim"})
    assert r.status_code == 200 and r.json()["valid"] is False
