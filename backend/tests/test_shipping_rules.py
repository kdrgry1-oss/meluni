import ast
from pathlib import Path
import pytest
from shipping_rules import shipping_quote


@pytest.mark.parametrize("net,free", [(3999.99, False), (4000, True), (4000.01, True)])
def test_inclusive_boundary(net, free):
    assert shipping_quote(net, [], 4000, 99, True) == {"basis": net, "free": free, "cost": 0 if free else 99}


def test_all_discounts_before_shipping_and_promo_cannot_bypass():
    assert shipping_quote(6000, [600, 1080, 216, 105], 4000, 99, True) == {
        "basis": 3999, "free": False, "cost": 99}
    assert shipping_quote(6000, [600, 1080, 216, 104], 4000, 99, True)["free"]


def test_bank_changes_eligibility():
    assert shipping_quote(5700, [570, 1026], 4000, 99)["free"]
    assert not shipping_quote(5700, [570, 1026, 205.2], 4000, 99, True)["free"]


def test_other_tenant_and_no_threshold():
    assert shipping_quote(100, [], 100, 15)["free"]
    assert not shipping_quote(5000, [], None, 15)["free"]
    assert shipping_quote(100, [], None, 15, True)["free"]


def test_order_final_shipping_is_after_actual_points_before_gift_card_and_total_check():
    # Guard the real orchestration placement, not just the pure arithmetic.
    source = (Path(__file__).parents[1] / "routes/orders.py").read_text()
    ast.parse(source)
    start = source.index('order["points_used"] = _sp["points"]')
    final = source.index('order["shipping_eligibility_basis"] = _ship_quote["basis"]')
    gift = source.index('from .gift_cards import redeem_gift_card_for_order')
    check = source.index('if _client_total > 0 and abs(_client_total - _server_total)')
    assert start < final < gift < check


def test_expired_campaign_not_live():
    from datetime import datetime, timezone
    from shipping_rules import campaign_live
    now = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    assert not campaign_live({"start_at": "2026-09-22T21:00:00+00:00", "end_at": "2026-09-25T20:59:59+00:00"}, now)
    assert not campaign_live({"start_at": "2026-09-27T00:00:00+00:00"}, now)
    assert campaign_live({"end_at": "2026-09-26"}, now)
    assert campaign_live({}, now)
