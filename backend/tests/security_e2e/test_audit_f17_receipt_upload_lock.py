"""Denetim #17: herkese açık sipariş görünümü telefon hanesi göstermez; dekont yüklemede telefon
kanıtı sipariş başına deneme kilidiyle korunur (son 4 hane kaba kuvvetle denenemez)."""
import pytest

from pay_harness import pay_create_order, pay_find, pay_seed_product

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n% receipt\n%%EOF"


async def _upload(http, number, contact):
    return await http.post(f"/api/orders/by-number/{number}/payment-notification",
                           files={"file": ("d.pdf", PDF, "application/pdf")}, data={"contact": contact})


async def test_no_digits_exposed_and_lock_after_5_failures(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "bank_transfer")
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    phone = "".join(ch for ch in str(o["shipping_address"]["phone"]) if ch.isdigit())
    from pay_harness import PAY_TOKENS
    pub = (await pay_http.get(f"/api/orders/by-number/{o['order_number']}")).json()
    assert "shipping_address" not in pub                      # kanıtsız: hiç adres/telefon yok (#27)
    own = (await pay_http.get(f"/api/orders/by-number/{o['order_number']}",
                              headers={"X-Order-Token": PAY_TOKENS[o["id"]]})).json()
    assert own["shipping_address"]["phone"] == "+90 *** *** ** **"   # kanıtlıda bile hane yok
    wrong = [f"{i:04d}" for i in range(10000) if f"{i:04d}" != phone[-4:]][:5]
    for w in wrong:
        assert (await _upload(pay_http, o["order_number"], w)).status_code == 404
    # 6. deneme DOĞRU olsa bile kilitli
    assert (await _upload(pay_http, o["order_number"], phone[-4:])).status_code == 429
    after = await pay_find("orders", {"id": o["id"]})
    assert not after.get("payment_receipt") and after["status"] == "awaiting_payment"


async def test_full_phone_or_last4_accepted(pay_http, pay_env):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "bank_transfer")
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    phone = "".join(ch for ch in str(o["shipping_address"]["phone"]) if ch.isdigit())
    r = await _upload(pay_http, o["order_number"], "0" + phone[-10:])
    assert r.status_code == 200, r.text
    after = await pay_find("orders", {"id": o["id"]})
    assert after["payment_status"] != "paid"      # dekont ödeme onayı değildir (değişmez 1)
