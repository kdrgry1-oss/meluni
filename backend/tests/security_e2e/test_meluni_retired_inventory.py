"""Meluni: retired supplier products must clear existing marketplace stock."""
from conftest import kv_run


def test_retired_items_zero_even_with_force_quantity(monkeypatch):
    import routes.integrations_trendyol as route
    import trendyol_client
    sent = []

    async def config():
        return {"is_active": True, "supplier_id": "test", "api_key": "test",
                "api_secret": "test", "mode": "sandbox", "default_markup": 0}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def update_price_and_inventory(self, items):
            sent.extend(items)
            return {"batchRequestId": "mock-batch"}

    monkeypatch.setattr(route, "get_trendyol_config", config)
    monkeypatch.setattr(trendyol_client, "TrendyolClient", Client)
    products = [
        {"id": "inactive", "barcode": "inactive", "is_active": False, "stock": 8, "price": 100},
        {"id": "deleted", "is_deleted": True, "price": 100,
         "variants": [{"barcode": "deleted", "stock": 9, "price": 100}]},
        {"id": "active", "barcode": "active", "is_active": True, "stock": 2, "price": 100},
    ]
    kv_run(route._sync_inventory_to_trendyol(products))
    assert {i["barcode"]: i["quantity"] for i in sent} == {"inactive": 0, "deleted": 0, "active": 2}
    sent.clear()
    kv_run(route._sync_inventory_to_trendyol(products, force_quantity=5))
    assert {i["barcode"]: i["quantity"] for i in sent} == {"inactive": 0, "deleted": 0, "active": 5}
