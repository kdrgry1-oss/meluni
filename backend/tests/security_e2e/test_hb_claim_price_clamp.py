"""HB iade satır fiyatı sipariş toplamıyla gelirse (HB4029270518) birim fiyata sınırlanır; düzeltilmiş
iade kaydı periyodik senkronla geri EZİLMEZ."""
from conftest import kv_run
from kv_harness import kv_find, kv_insert


def _order(no, unit):
    kv_insert("orders", {"id": f"o-{no}", "order_number": no, "platform": "hepsiburada",
                         "items": [{"barcode": "B1", "merchant_sku": "1001", "name": "Ceket A", "price": unit,
                                    "unit_price": unit, "quantity": 1},
                                   {"barcode": "B2", "merchant_sku": "1002", "name": "Ceket B", "price": unit,
                                    "unit_price": unit, "quantity": 1}],
                         "total": unit * 2})


def _raw(no, cid, sku, price):
    return {"claimNumber": cid, "orderNumber": no.replace("HB", ""), "status": "Accepted", "type": "Return",
            "MerchantSku": sku, "priceAmount": price, "quantity": 1, "refundAmount": price,
            "createdDate": "2099-01-01T00:00:00Z"}


def test_claim_line_price_is_capped_to_order_unit_price():
    from routes.integrations_hepsiburada import _hb_claim_norm
    _order("HB777000111", 3056.25)
    d = kv_run(_hb_claim_norm(_raw("HB777000111", "C-1", "1001", 6112.5)))
    it = d["items"][0]
    assert it["price"] == 3056.25 and it["hb_reported_price"] == 6112.5
    assert d["refund_amount"] == 3056.25
    # doğru gelen fiyata dokunulmaz
    d2 = kv_run(_hb_claim_norm(_raw("HB777000111", "C-2", "1002", 3056.25)))
    assert d2["items"][0]["price"] == 3056.25 and "hb_reported_price" not in d2["items"][0]
    assert d2["refund_amount"] == 3056.25


def test_hb4029_fix_v2_relocks_claims_and_keeps_first_backup():
    import scheduler
    kv_insert("orders", {"id": "o-hb4029", "order_number": "HB4029270518", "platform": "hepsiburada",
                         "items": [{"barcode": "8683851000560", "price": 3056.25}, {"barcode": "8683851000546", "price": 3056.25}],
                         "total": 6112.5, "_fix_backup_tutar": {"items": "v1-yedek"}})
    for cid in ("HB-1", "HB-2"):
        kv_insert("trendyol_claims", {"claim_id": cid, "order_number": "HB4029270518", "platform": "hepsiburada",
                                      "refund_amount": 6112.5, "items": [{"price": 6112.5, "unit_price": 6112.5}],
                                      "_fix_backup_tutar": {"refund_amount": "v1-yedek"}})
    kv_run(scheduler._ensure_hb4029_iade_siparis_tutar_duzelt_v2())
    for cid in ("HB-1", "HB-2"):
        c = kv_find("trendyol_claims", {"claim_id": cid})
        assert c["refund_amount"] == 3056.25 and c["items"][0]["price"] == 3056.25
        assert c["amount_overridden"] is True
        assert c["_fix_backup_tutar"] == {"refund_amount": "v1-yedek"} and c["_fix_backup_tutar_v2"]["refund_amount"] == 6112.5
    o = kv_find("orders", {"order_number": "HB4029270518"})
    assert o["_fix_backup_tutar"] == {"items": "v1-yedek"} and "_fix_backup_tutar_v2" not in o   # zaten doğruydu
    # ikinci çalıştırma bir şey yapmaz (bayrak)
    kv_run(scheduler._ensure_hb4029_iade_siparis_tutar_duzelt_v2())
    assert kv_find("trendyol_claims", {"claim_id": "HB-1"})["refund_amount"] == 3056.25
