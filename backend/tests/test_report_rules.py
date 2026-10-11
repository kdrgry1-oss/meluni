import importlib.util
import asyncio
from pathlib import Path
import sys
import types


# Load the pure aggregation helpers without importing routes/__init__.py (which
# intentionally registers the complete FastAPI application dependency graph).
routes_pkg = types.ModuleType("routes")
routes_pkg.__path__ = []
deps_stub = types.ModuleType("routes.deps")
deps_stub.db = object()
sys.modules.setdefault("routes", routes_pkg)
sys.modules.setdefault("routes.deps", deps_stub)
spec = importlib.util.spec_from_file_location(
    "routes.report_dedup", Path(__file__).parents[1] / "routes" / "report_dedup.py"
)
report_dedup = importlib.util.module_from_spec(spec)
sys.modules["routes.report_dedup"] = report_dedup
spec.loader.exec_module(report_dedup)
canonical_order_stages = report_dedup.canonical_order_stages
effective_order_date_match = report_dedup.effective_order_date_match
split_confirmed_return = report_dedup.split_confirmed_return
accepted_claim_items = report_dedup.accepted_claim_items
product_quantity_metrics = report_dedup.product_quantity_metrics
kept_gross_revenue = report_dedup.kept_gross_revenue
reconciled_platform_breakdown = report_dedup.reconciled_platform_breakdown
product_platform_metrics = report_dedup.product_platform_metrics
payment_report_group_key = report_dedup.payment_report_group_key
allocate_order_total = report_dedup.allocate_order_total

mapping_spec = importlib.util.spec_from_file_location(
    "marketplace_order_mapping", Path(__file__).parents[1] / "routes" / "marketplace_order_mapping.py"
)
marketplace_mapping = importlib.util.module_from_spec(mapping_spec)
mapping_spec.loader.exec_module(marketplace_mapping)
partial_cancel_net_values = report_dedup.partial_cancel_net_values


def test_effective_date_prefers_marketplace_and_falls_back_only_when_empty():
    match = effective_order_date_match("2026-08-01", "2026-08-31")
    assert match["$or"][0] == {
        "marketplace_order_date": {"$gte": "2026-08-01", "$lte": "2026-08-31"}
    }
    assert match["$or"][1] == {
        "marketplace_order_date": {"$in": [None, ""]},
        "created_at": {"$gte": "2026-08-01", "$lte": "2026-08-31"},
    }


def test_canonical_order_preference_is_terminal_partial_then_newest():
    stages = canonical_order_stages()
    assert [next(iter(stage)) for stage in stages] == [
        "$addFields", "$addFields", "$sort", "$group", "$replaceRoot"
    ]
    sort = stages[2]["$sort"]
    assert sort["_report_terminal"] == -1
    assert sort["_report_partial_cancel"] == -1
    assert sort["updated_at"] == -1


def test_empty_order_number_uses_internal_identity():
    key = canonical_order_stages()[1]["$addFields"]["_report_dedupe_key"]
    assert key["$cond"][1] == {"$concat": ["order:", "$_report_order_number"]}
    assert key["$cond"][2]["$concat"][0] == "id:"


def test_confirmed_return_is_removed_from_net_but_cancel_is_not_in_denominator():
    net_qty, net_amount, return_qty, return_amount = split_confirmed_return(8, 800.0, 4)
    assert (net_qty, net_amount, return_qty, return_amount) == (4, 400.0, 4, 400.0)
    assert return_qty / (net_qty + return_qty) * 100 == 50.0


def test_confirmed_return_cannot_exceed_sold_quantity():
    assert split_confirmed_return(2, 300.0, 9) == (0, 0.0, 2, 300.0)


def test_hepsiburada_flat_raw_items_fall_back_to_normalized_items():
    claim = {
        "claim_id": "HB-1", "claim_status": "Accepted",
        "raw_data": {"items": [{"sku": "HBCV1", "quantity": 2}]},
        "items": [{"claim_item_id": "line-1", "barcode": "869", "quantity": 2,
                   "price": 125.0}],
    }
    assert accepted_claim_items(claim) == [{
        "key": "HB-1:line-1", "barcode": "869", "quantity": 2,
        "amount": 250.0, "status": "Accepted",
    }]


def test_marketplace_status_mappers_are_conservative():
    assert marketplace_mapping.canonical_hb_order_number("hb4600") == "HB4600"
    assert marketplace_mapping.hb_internal_status("Cancelled") == ("cancelled", "paid")
    assert marketplace_mapping.hb_internal_status("Open") == ("confirmed", "paid")
    assert marketplace_mapping.hb_internal_status("Unpacked") == ("confirmed", "paid")
    assert marketplace_mapping.hb_internal_status("Packed") == ("confirmed", "paid")
    assert marketplace_mapping.hb_internal_status("brand-new-state") == ("pending", "pending")
    assert marketplace_mapping.amazon_internal_status("Unshipped") == ("confirmed", "paid")
    assert marketplace_mapping.amazon_internal_status("Pending") == ("pending", "pending")
    assert marketplace_mapping.amazon_internal_status("FutureStatus") == ("pending", "pending")


def test_temu_payload_maps_only_present_financial_data():
    full = marketplace_mapping.temu_order_fields({
        "status": "paid", "total_amount": "450.25", "order_date": "2026-09-01T10:00:00Z",
        "items": [{"sku": "SKU1", "name": "Ürün", "qty": 2, "unitPrice": "225.125"}],
    })
    assert (full["status"], full["payment_status"], full["total"]) == ("confirmed", "paid", 450.25)
    assert full["items"][0]["quantity"] == 2
    assert full["integration_incomplete"] is False
    partial = marketplace_mapping.temu_order_fields({"status": "processing"})
    assert partial["integration_incomplete"] is True
    assert "total" not in partial and "items" not in partial


def test_product_quantity_metrics_separates_trendyol_and_operational_rates():
    metrics = product_quantity_metrics(net=2, cancelled=2, returned=4)
    assert metrics == {
        "gross_qty": 8,
        "trendyol_return_rate_pct": 50.0,
        "return_rate_excluding_cancels_pct": 66.67,
    }


def test_product_quantity_metrics_are_non_negative_and_zero_safe():
    assert product_quantity_metrics(-1, 0, 0) == {
        "gross_qty": 0,
        "trendyol_return_rate_pct": 0.0,
        "return_rate_excluding_cancels_pct": 0.0,
    }


def test_kept_gross_revenue_applies_discount_once():
    # Brüt 1000, indirim 200; kalan net satış 400 ise kalan brüt 500'dür.
    assert kept_gross_revenue(400, 1000, 200) == 500
    assert kept_gross_revenue(400, 0, 0) == 400


def test_product_lines_close_to_authoritative_order_total_to_the_cent():
    # Legacy item sum is 1,400 although the paid order total is 1,000.
    rows = allocate_order_total([700, 700], 1000)
    assert rows == [500, 500]
    assert sum(rows) == 1000


def test_order_total_allocation_puts_rounding_remainder_on_last_line():
    rows = allocate_order_total([1, 1, 1], 100)
    assert rows == [33.33, 33.33, 33.34]
    assert round(sum(rows), 2) == 100


def test_active_scope_partial_cancel_is_not_subtracted_twice():
    assert partial_cancel_net_values(800, 2, 200, 1, "active") == (800, 2)


def test_legacy_full_scope_partial_cancel_keeps_historical_subtraction():
    assert partial_cancel_net_values(1000, 3, 200, 1, "") == (800, 2)


def test_profitability_platform_parts_equal_canonical_product_totals():
    rows = reconciled_platform_breakdown({
        "qty": 3, "revenue": 100.01,
        "platform_breakdown": [
            {"platform": "site", "qty": 1, "revenue": 33.33},
            {"platform": "trendyol", "qty": 2, "revenue": 66.67},
        ],
    })
    assert sum(row["qty"] for row in rows) == 3
    assert round(sum(row["revenue"] for row in rows), 2) == 100.01


def test_product_platform_metrics_keep_all_and_trendyol_scopes_separate():
    rows = product_platform_metrics(
        [
            {"platform": "trendyol", "qty": 2, "revenue": 200},
            {"platform": "site", "qty": 7, "revenue": 700},
        ],
        [
            {"platform": "trendyol", "cancel": 2, "return": 4,
             "cancel_total": 150, "return_total": 400},
            {"platform": "site", "cancel": 1, "return": 0,
             "cancel_total": 100, "return_total": 0},
        ],
    )
    by_platform = {row["platform"]: row for row in rows}
    trendyol = by_platform["trendyol"]
    assert trendyol["gross_qty"] == 8
    assert trendyol["trendyol_return_rate_pct"] == 50.0
    assert trendyol["net_revenue"] == 200.0
    assert trendyol["gross_revenue"] == 750.0
    assert trendyol["net_qty"] + trendyol["cancel_qty"] + trendyol["return_qty"] == trendyol["gross_qty"]
    assert sum(row["gross_qty"] for row in rows) == 16
    assert sum(row["net_qty"] for row in rows) == 9
    assert sum(row["cancel_qty"] for row in rows) == 3
    assert sum(row["return_qty"] for row in rows) == 4


def test_payment_report_group_keeps_marketplace_over_default_payment_method():
    assert payment_report_group_key({
        "platform": "meluni", "marketplace": "Trendyol",
        "payment_method": "marketplace",
    }) == "trendyol"
    assert payment_report_group_key({
        "platform": "meluni", "payment_method": "bank_transfer",
    }) == "bank_transfer"


def test_mixed_trendyol_claim_counts_only_accepted_child_items():
    claim = {
        "claim_id": "claim-1",
        "claim_status": "Created",
        "items": [{"barcode": "legacy-would-be-wrong", "quantity": 3}],
        "raw_data": {"items": [{
            "orderLine": {"barcode": "8680001"},
            "claimItems": [
                {"id": "a", "claimItemStatus": {"name": "Accepted"}},
                {"id": "b", "claimItemStatus": {"name": "Created"}},
                {"id": "c", "claimItemStatus": {"name": "Rejected"}},
            ],
        }]},
    }
    assert accepted_claim_items(claim) == [
        {"key": "claim-1:a", "barcode": "8680001", "quantity": 1,
         "amount": 0.0, "status": "Accepted"}
    ]


def test_accepted_claim_item_ids_are_deduplicated():
    claim = {
        "claim_id": "claim-2",
        "raw_data": {"items": [{
            "orderLine": {"barcode": "8680002"},
            "claimItems": [
                {"id": "same", "claimItemStatus": {"name": "Accepted"}},
                {"id": "same", "claimItemStatus": {"name": "Accepted"}},
            ],
        }]},
    }
    assert len(accepted_claim_items(claim)) == 1


def test_legacy_accepted_claim_uses_normalized_quantity_without_raw_data():
    claim = {
        "claim_id": "legacy-1",
        "claim_status": "Accepted",
        "items": [{"claim_item_id": "x", "barcode": "8680003", "quantity": 2}],
    }
    assert accepted_claim_items(claim) == [
        {"key": "legacy-1:x", "barcode": "8680003", "quantity": 2,
         "amount": 0.0, "status": "Accepted"}
    ]


def test_accepted_child_uses_its_own_normalized_net_price():
    claim = {
        "claim_id": "priced",
        "items": [{"claim_item_id": "accepted", "barcode": "868", "price": 799.5}],
        "raw_data": {"items": [{
            "orderLine": {"barcode": "868", "price": 999},
            "claimItems": [{"id": "accepted", "claimItemStatus": {"name": "Accepted"}}],
        }]},
    }
    assert accepted_claim_items(claim)[0]["amount"] == 799.5


def test_trendyol_reconciliation_can_request_created_date(monkeypatch):
    import trendyol_client

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": []}

    class ClientContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, _url, **kwargs):
            captured.update(kwargs.get("params") or {})
            return Response()

    monkeypatch.setattr(trendyol_client.httpx, "AsyncClient", lambda **_: ClientContext())
    client = trendyol_client.TrendyolClient("supplier", "key", "secret", "production")
    asyncio.run(client.get_orders(
        start_date_ms=1, end_date_ms=2, order_by_field="CreatedDate"
    ))
    assert captured["orderByField"] == "CreatedDate"
