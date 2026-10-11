"""Denetim #27: sıralı sipariş numarasıyla kanıtsız erişimde içerik/tutar/konum/kargo bilgisi dönmez;
sipariş anahtarı, sahibin oturumu ya da siparişteki e-posta/telefonla (deneme kilitli) tam görünüm."""
import pytest

from pay_harness import PAY_TOKENS, pay_create_order, pay_customer, pay_find, pay_seed_product

pytestmark = pytest.mark.asyncio
LEAK_KEYS = ("items", "total", "shipping_address", "created_at", "subtotal", "timeline", "cargo")


async def _order(http, headers=None):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(http, "bank_transfer", headers=headers)
    assert r.status_code == 200, r.text
    return await pay_find("orders", {"id": r.json()["order_id"]})


async def test_anonymous_number_gets_summary_only(pay_http, pay_env):
    o = await _order(pay_http)
    from routes.deps import db
    await db.orders.update_one({"id": o["id"]}, {"$set": {"cargo_tracking_number": "TRK123456789"}})
    for url in (f"/api/orders/by-number/{o['order_number']}", f"/api/track/{o['order_number']}"):
        j = (await pay_http.get(url)).json()
        assert j["verification_required"] is True, url
        assert not set(LEAK_KEYS) & set(j), (url, j)
        assert "TRK123456789" not in str(j)
    # kargo takip numarasıyla (tahmin edilemez) arama tam takip verir
    j = (await pay_http.get("/api/track/TRK123456789")).json()
    assert j.get("timeline") and not j.get("verification_required")


async def test_token_owner_and_contact_unlock(pay_http, pay_env):
    cust, hdr = await pay_customer(email="own@example.test")
    o = await _order(pay_http, headers=hdr)
    url = f"/api/orders/by-number/{o['order_number']}"
    assert "items" in (await pay_http.get(url, headers={"X-Order-Token": PAY_TOKENS[o["id"]]})).json()
    assert "items" in (await pay_http.get(url, headers=hdr)).json()
    _, other = await pay_customer(email="other@example.test")
    assert (await pay_http.get(url, headers=other)).json().get("verification_required") is True
    phone = "".join(ch for ch in o["shipping_address"]["phone"] if ch.isdigit())
    assert "items" in (await pay_http.get(url, headers={"X-Order-Contact": phone})).json()
    for _ in range(5):
        assert (await pay_http.get(url, headers={"X-Order-Contact": "wrong@example.test"})).status_code == 403
    assert (await pay_http.get(url, headers={"X-Order-Contact": phone})).status_code == 429
