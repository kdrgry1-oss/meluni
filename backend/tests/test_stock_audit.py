import asyncio

from stock_audit import actor_context, record_stock_audit, record_stock_sync_audit, stock_changes


def test_stock_changes_are_variant_and_sku_specific():
    before = {"variants": [
        {"id": "v1", "stock_code": "SKU-S", "barcode": "111", "size": "S", "stock": 4},
        {"id": "v2", "stock_code": "SKU-M", "barcode": "222", "size": "M", "stock": 8},
    ]}
    after = {"variants": [
        {"id": "v1", "stock_code": "SKU-S", "barcode": "111", "size": "S", "stock": 7},
        {"id": "v2", "stock_code": "SKU-M", "barcode": "222", "size": "M", "stock": 8},
    ]}
    assert stock_changes("p1", before, after) == [{
        "product_id": "p1", "variant_id": "v1", "sku": "SKU-S", "barcode": "111",
        "size": "S", "color": "", "old": 4, "new": 7, "delta": 3,
    }]


def test_root_stock_change_is_exact_and_noop_is_empty():
    assert stock_changes("p1", {"stock": 2, "stock_code": "ROOT"}, {"stock": 5, "stock_code": "ROOT"})[0] == {
        "product_id": "p1", "variant_id": "", "sku": "ROOT", "barcode": "", "size": "",
        "color": "", "old": 2, "new": 5, "delta": 3,
    }
    assert stock_changes("p1", {"stock": 5}, {"stock": 5}) == []


def test_actor_context_hashes_session_and_never_returns_bearer_token():
    class Headers(dict):
        pass
    class Client:
        host = "127.0.0.1"
    class Request:
        headers = Headers({"authorization": "Bearer secret-token", "user-agent": "pytest", "x-forwarded-for": "1.2.3.4"})
        client = Client()

    actor, context = actor_context({"id": "u1", "email": "a@example.com", "login_method": "google"}, Request())
    assert actor["login_method"] == "google"
    assert context["session_hash"] and context["session_hash"] != "Bearer secret-token"
    assert "secret-token" not in str(context)
    assert context["ip"] == "1.2.3.4"


def test_canonical_record_contains_actor_exact_balances_and_local_sync_status():
    class Collection:
        def __init__(self): self.rows = []
        async def insert_one(self, row): self.rows.append(row)
    class Db:
        stock_movements = Collection()

    count = asyncio.run(record_stock_audit(
        Db, product_id="p1", product_name="Elbise",
        before={"stock": 2, "stock_code": "SKU"}, after={"stock": 4, "stock_code": "SKU"},
        source="admin_edit", current_user={"id": "u1", "email": "a@example.com"},
    ))
    row = Db.stock_movements.rows[0]
    assert count == 1
    assert row["schema_version"] == 2
    assert row["actor"]["email"] == "a@example.com"
    assert row["items"][0]["old"] == 2 and row["items"][0]["new"] == 4
    assert row["sync_result"]["status"] == "not_applicable"


def test_user_triggered_sync_records_each_sku_without_fake_stock_delta():
    class Collection:
        def __init__(self): self.rows = []
        async def insert_one(self, row): self.rows.append(row)
    class Db:
        stock_movements = Collection()

    count = asyncio.run(record_stock_sync_audit(
        Db, product={"id": "p1", "name": "Elbise", "variants": [
            {"id": "v1", "stock_code": "SKU-S", "size": "S", "stock": 3},
            {"id": "v2", "stock_code": "SKU-M", "size": "M", "stock": 5},
        ]}, platform="trendyol", status="submitted", batch_id="batch-1",
    ))
    row = Db.stock_movements.rows[0]
    assert count == 2
    assert row["sync_result"] == {"status": "submitted", "platform": "trendyol", "batch_id": "batch-1", "message": ""}
    assert [(item["old"], item["new"], item["delta"]) for item in row["items"]] == [(3, 3, 0), (5, 5, 0)]
