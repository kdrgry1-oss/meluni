"""Trendyol aynı gün kargo (hızlı teslimat) — bazı ürünlerde yoktu (2026-10-05): özellik listesi BOŞ olan
ürünler toplu termin güncellemesinden atlanıyordu; Trendyol'un reddettiği ürünler de hiç yeniden
denenmiyordu. Artık boş özellikli ürün dahil, atlananlar barkod+nedenle kaydedilir, reddedilen barkodlar
(yalnız onlar) en çok 3 kez yeniden gönderilir. Desen: kv_harness ile bellek içi veritabanı."""
import pytest

import trendyol_delivery as td
from conftest import kv_run
from kv_harness import kv_find, kv_insert


def _row(bc, **kw):
    r = {"barcode": bc, "title": "T", "productMainId": "M", "brandId": 1, "pimCategoryId": 2,
         "description": "D", "images": [{"url": "https://cdn.example.test/a.jpg"}],
         "attributes": [{"attributeId": 1, "attributeValueId": 2}]}
    r.update(kw)
    return r


class _Cli:
    sent = []

    def __init__(self, **k):
        pass

    async def get_filtered_products(self, **k):
        return {"content": [_row("A"), _row("B", attributes=[]), _row("C", description=""),
                            _row("D", attributes=None)], "totalPages": 1}

    async def update_products(self, chunk):
        _Cli.sent.append([i["barcode"] for i in chunk])
        return {"batchRequestId": f"b{len(_Cli.sent)}"}

    async def get_batch_request_result(self, bid):
        if bid == "b1":
            return {"status": "COMPLETED", "items": [
                {"status": "FAILED", "requestItem": {"barcode": "A"}, "failureReasons": ["geçici hata"]},
                {"status": "SUCCESS", "requestItem": {"barcode": "B"}}]}
        return {"status": "COMPLETED", "items": [{"status": "SUCCESS", "requestItem": {"barcode": "A"}}]}


@pytest.fixture
def ty(monkeypatch):
    import routes.integrations_trendyol as it
    import trendyol_client
    _Cli.sent = []

    async def _cfg():
        return {"api_key": "k", "api_secret": "s", "supplier_id": "1"}
    monkeypatch.setattr(it, "get_trendyol_config", _cfg)
    monkeypatch.setattr(trendyol_client, "TrendyolClient", _Cli)
    monkeypatch.setattr(td, "_state_and_option", lambda r, now=None: ("same_day", {"deliveryDuration": 1,
                                                                                   "fastDeliveryType": "SAME_DAY_SHIPPING"}))
    from routes.deps import db
    return db


def test_empty_attribute_products_get_same_day_and_skips_are_explained(ty):
    res = kv_run(td.apply(ty))
    assert _Cli.sent == [["A", "B"]]                       # B (özelliksiz) artık dahil
    st = kv_find("settings", {"id": td._STATE_ID})
    reasons = {x["barcode"]: x["reason"] for x in st["skipped_items"]}
    assert "açıklama" in reasons["C"] and "özellik" in reasons["D"] and res["items"] == 2


def test_rejected_barcodes_are_retried_alone_at_most_three_times(ty):
    kv_run(td.apply(ty))
    kv_run(td.check_batches(ty))
    st = kv_find("settings", {"id": td._STATE_ID})
    assert st["failed_barcodes"] == ["A"]
    kv_run(td.apply(ty))                                   # aynı durum → yalnız A yeniden
    assert _Cli.sent[-1] == ["A"]
    kv_run(td.check_batches(ty))                           # b2 başarılı
    st = kv_find("settings", {"id": td._STATE_ID})
    assert st["failed_barcodes"] == []
    assert kv_run(td.apply(ty)).get("skipped") == "zaten güncel"


def test_skip_reason_units():
    assert td.skip_reason(_row("X", attributes=[])) == ""
    assert td.skip_reason(_row("X", archived=True)) == "arşivde"
    assert "görsel" in td.skip_reason(_row("X", images=[]))
