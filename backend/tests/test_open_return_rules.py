import logging
import importlib.util
from pathlib import Path
import sys
import types


def _load_reports_module():
    """Load reports helpers without starting the application/database graph."""
    routes_dir = Path(__file__).parents[1] / "routes"
    routes_pkg = types.ModuleType("open_return_test_routes")
    routes_pkg.__path__ = [str(routes_dir)]
    sys.modules[routes_pkg.__name__] = routes_pkg

    deps = types.ModuleType(f"{routes_pkg.__name__}.deps")
    deps.db = object()
    deps.require_admin = lambda: None
    deps.require_permission = lambda _perm: (lambda: None)  # IAM-02: rapor uçları reports.* izni ister
    deps.tr_range_to_utc = lambda start, end: (start, end)
    deps.logger = logging.getLogger("open_return_test_routes")
    sys.modules[deps.__name__] = deps

    dedup_spec = importlib.util.spec_from_file_location(
        f"{routes_pkg.__name__}.report_dedup", routes_dir / "report_dedup.py"
    )
    dedup = importlib.util.module_from_spec(dedup_spec)
    sys.modules[dedup_spec.name] = dedup
    dedup_spec.loader.exec_module(dedup)

    reports_spec = importlib.util.spec_from_file_location(
        f"{routes_pkg.__name__}.reports", routes_dir / "reports.py"
    )
    reports = importlib.util.module_from_spec(reports_spec)
    sys.modules[reports_spec.name] = reports
    reports_spec.loader.exec_module(reports)
    return reports, dedup


def _order(status, total=200, quantity=2):
    return {"order_number": "W1", "status": status, "total": total,
            "items": [{"quantity": quantity}]}


def test_site_requested_return_stays_net_and_is_projected_as_pending():
    reports, _ = _load_reports_module()
    result = reports._bucket_orders(
        [_order("return_requested")], {}, {"W1": {"amount": 80, "qty": 1}}
    )
    assert result["returns"] == {"revenue": 0.0, "orders": 0, "units": 0,
                                 "partial_orders": 0}
    assert result["net"] == {"revenue": 200.0, "orders": 1, "units": 2}
    assert result["pending_returns"] == {"revenue": 80.0, "orders": 1, "units": 1}


def test_site_return_in_transit_without_bridge_uses_conservative_full_fallback():
    reports, _ = _load_reports_module()
    result = reports._bucket_orders([_order("return_in_transit")], {}, {})
    assert result["returns"]["units"] == 0
    assert result["net"]["units"] == 2
    assert result["pending_returns"] == {"revenue": 200.0, "orders": 1, "units": 2}


def test_financially_closed_return_remains_closed_not_pending():
    reports, _ = _load_reports_module()
    result = reports._bucket_orders(
        [_order("refunded")], {"W1": {"amount": 80, "qty": 1}},
        {"W1": {"amount": 50, "qty": 1}},
    )
    assert result["returns"]["units"] == 1
    assert result["returns"]["revenue"] == 80
    assert result["pending_returns"]["units"] == 0


def test_customer_return_records_and_items_dedupe_only_stable_ids():
    _, dedup = _load_reports_module()
    rows = dedup.dedupe_return_records([
        {"id": "R1", "status": "created", "updated_at": "2026-09-01"},
        {"id": "R1", "status": "approved", "updated_at": "2026-09-02"},
        {"id": "R2", "status": "created", "updated_at": "2026-09-02"},
    ])
    assert sorted((r["id"], r["status"]) for r in rows) == [
        ("R1", "approved"), ("R2", "created")]
    items = dedup.dedupe_return_items([
        {"id": "I1", "quantity": 1}, {"id": "I1", "quantity": 1},
        {"quantity": 1}, {"quantity": 1},
    ])
    assert len(items) == 3


def test_marketplace_open_status_mapping_is_shared_and_counts_missing_barcode():
    _, dedup = _load_reports_module()
    assert dedup.marketplace_claim_status_bucket("Created", False) == "talep_olusturulan"
    assert dedup.marketplace_claim_status_bucket("Created", True) == "kargoya_verilen"
    assert dedup.marketplace_claim_status_bucket("InAnalysis", False) == "aksiyon_bekleyen"
    claim = {"claim_id": "C1", "raw_data": {"items": [{
        "orderLine": {},
        "claimItems": [{"id": "I1", "claimItemStatus": {"name": "Created"}}],
    }]}}
    assert dedup.claim_items_with_status(claim, {"Created"})[0]["quantity"] == 1
