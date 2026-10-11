"""Denetim #4: ödeme başlatma yalnız siparişi oluşturan istemcinin aldığı pay_token ile yapılır;
sipariş id'sini bilen üçüncü kişi başkasının bekleyen ödeme akışını başlatamaz/bozamaz.
Ödeme DURUMUNA dokunulmaz (değişmez 1–4)."""
import pytest

import routes.payment as payment_mod
from pay_harness import (PAY_TOKENS, mock_3ds_init, pay_card_order, pay_find, pay_init_3ds,
                         pay_create_order, pay_seed_product)

pytestmark = pytest.mark.asyncio


async def test_create_returns_token_and_only_hash_is_stored(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    tok = PAY_TOKENS[o["id"]]
    assert len(tok) >= 40
    assert o["pay_cap_hashes"] and tok not in str(o)
    assert o["status"] == "awaiting_payment" and o.get("payment_status") != "paid"


async def test_enforced_rejects_missing_or_wrong_token(pay_http, pay_env, monkeypatch):
    monkeypatch.setattr(payment_mod, "PAY_CAP_ENFORCED", True)
    o = await pay_card_order(pay_http)
    mock_3ds_init(pay_env.iyzico)
    for bad in ("", "x" * 43):
        r = await pay_init_3ds(pay_http, o["id"], pay_token=bad)
        assert r.status_code == 403, r.text
    after = await pay_find("orders", {"id": o["id"]})
    assert not after.get("threeds_started_at")
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 200 and r.json()["success"] is True, r.text
    after = await pay_find("orders", {"id": o["id"]})
    assert after["status"] == "awaiting_payment" and after.get("payment_status") != "paid"


async def test_idempotent_replay_issues_fresh_valid_token(pay_http, pay_env, monkeypatch):
    monkeypatch.setattr(payment_mod, "PAY_CAP_ENFORCED", True)
    await pay_seed_product("p1", stock=5)
    kw = dict(items=[{"product_id": "p1", "quantity": 1, "price": 1}], idempotency_key="idem-audit-f04-0001")
    r1 = await pay_create_order(pay_http, "credit_card", **kw)
    r2 = await pay_create_order(pay_http, "credit_card", **kw)
    assert r1.json()["order_id"] == r2.json()["order_id"]
    assert r2.json().get("pay_token") and r2.json()["pay_token"] != r1.json()["pay_token"]
    mock_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, r1.json()["order_id"], pay_token=r2.json()["pay_token"])
    assert r.status_code == 200, r.text


async def test_legacy_order_without_capability_still_payable(pay_http, pay_env, monkeypatch):
    monkeypatch.setattr(payment_mod, "PAY_CAP_ENFORCED", True)
    o = await pay_card_order(pay_http)
    from routes.deps import db
    await db.orders.update_one({"id": o["id"]}, {"$unset": {"pay_cap_hashes": ""}})
    mock_3ds_init(pay_env.iyzico)
    r = await pay_init_3ds(pay_http, o["id"], pay_token="")
    assert r.status_code == 200, r.text
