"""(1) Ürün kartı sepet-tutarı kampanya ibaresi (tier_badge): fiyatın üstündeki en yakın eşik,
kapsam/ilk-sipariş/ödeme-yöntemi kuralları motorla tutarlı. (2) İade gider pusulası/stok iadesi
yalnız KABUL edilen kalemleri baz alır (reddedilen kalem pusulaya/stoka girmez)."""
from conftest import kv_run
from routes import products as P
from routes.integrations_common import claim_effective_items

T5 = {"id": "t5", "type": "fixed", "value": 500.0, "min_cart_total": 5000.0}
T10 = {"id": "t10", "type": "fixed", "value": 1000.0, "min_cart_total": 10000.0}


def _badge(price, sale=None, tiers=(T5, T10), **extra):
    p = {"id": "p1", "price": price, "sale_price": sale, "category_ids": ["c1"], **extra}
    return P._tier_badge_for_product(p, list(tiers), sale if sale and sale < price else price)


def test_tier_badge_picks_next_threshold_above_price():
    assert _badge(2950) == "5000 TL üzerine 500 TL indirim"
    assert _badge(4999.99) == "5000 TL üzerine 500 TL indirim"
    assert _badge(5000) == "10000 TL üzerine 1000 TL indirim"
    assert _badge(7500) == "10000 TL üzerine 1000 TL indirim"
    assert _badge(12000) == "10000 TL üzerine 1000 TL indirim"   # tüm eşikler aşıldı → en büyük
    assert _badge(1000, tiers=()) == ""


def test_tier_badge_respects_scope_and_discounted_products():
    scoped = {**T5, "categories": ["other"]}
    assert _badge(3000, tiers=(scoped, T10)) == "10000 TL üzerine 1000 TL indirim"
    excl = {**T5, "excluded_products": ["p1"]}
    assert _badge(3000, tiers=(excl,)) == ""
    # indirimli-fiyatlı ürün: skip_discounted (varsayılan) → ibare yok; kapalıysa var
    assert _badge(3000, sale=2500) == ""
    assert _badge(3000, sale=2500, tiers=({**T5, "skip_discounted": False},)) == "5000 TL üzerine 500 TL indirim"
    assert P.tier_badge_text({"type": "percent", "value": 10, "min_cart_total": 7500}) == "7500 TL üzerine %10 indirim"


def test_tier_loader_skips_first_order_and_payment_restricted(kv_db):
    base = {"is_active": True, "auto_apply": True, "type": "fixed", "end_at": "2099-01-01"}
    kv_run(kv_db.coupons.insert_many([
        {**base, "id": "a", "code": "AUTO-A", "value": 500, "min_cart_total": 5000},
        {**base, "id": "b", "code": "AUTO-B", "value": 300, "min_cart_total": 3000, "first_order_only": True},
        {**base, "id": "c", "code": "AUTO-C", "value": 200, "min_cart_total": 2000, "payment_methods": ["bank_transfer"]},
        {**base, "id": "d", "code": "AUTO-D", "value": 900, "min_cart_total": 9000, "end_at": "2001-01-01"},
    ]))
    P._CAMP_BADGE_CACHE["t"] = 0  # önbelleği bayatlat
    kv_run(P._auto_campaigns_for_badges())
    assert [t["id"] for t in P._CAMP_BADGE_CACHE.get("tiers")] == ["a"]
    p = {"id": "x", "price": 1200.0}
    P._apply_campaign_badge(p, [])
    assert p["tier_badge"] == "5000 TL üzerine 500 TL indirim"
    assert "tier_badge" in P.PUBLIC_PRODUCT_FIELDS


def _claim(**kw):
    items = [{"claim_item_id": i, "barcode": f"b{i}", "price": 100, "quantity": 1} for i in ("1", "2", "3")]
    return {"items": items, **kw}


def _ids(items):
    return [i["claim_item_id"] for i in items]


def test_claim_effective_items_excludes_rejected():
    raw = {"items": [{"claimItems": [{"id": 1, "claimItemStatus": {"name": "Accepted"}},
                                     {"id": 2, "claimItemStatus": {"name": "Accepted"}}]},
                     {"claimItems": [{"id": 3, "claimItemStatus": {"name": "Rejected"}}]}]}
    assert _ids(claim_effective_items(_claim(raw_data=raw))) == ["1", "2"]
    # panelde onaylanan kalemler önceliklidir
    assert _ids(claim_effective_items(_claim(approved_item_ids=["2"]))) == ["2"]
    # bilgi yoksa eski davranış: tümü
    assert _ids(claim_effective_items(_claim())) == ["1", "2", "3"]


def test_restock_skips_rejected_item(kv_db):
    from routes.integrations_common import restock_claim_once
    # (mongomock arrayFilters desteklemez → varyantsız ürün-barkodu yolu)
    kv_run(kv_db.products.insert_many([{"id": f"pr{i}", "barcode": f"b{i}", "stock": 0} for i in (1, 2, 3)]))
    kv_run(kv_db.trendyol_claims.insert_one({"claim_id": "cl1", **_claim(approved_item_ids=["1", "2"])}))
    kv_run(restock_claim_once("cl1", "test"))
    v = {p["barcode"]: p["stock"] for p in kv_run(kv_db.products.find({}, {"_id": 0}).to_list(10))}
    assert v == {"b1": 1, "b2": 1, "b3": 0}
