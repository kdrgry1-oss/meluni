"""Dekont yükleme: giriş yapmış müşteri KENDİ siparişine ek doğrulama bilgisi girmeden yükleyebilir
("Hesabım → Ödeme Bildirimi Yap"); başka bir üyenin oturumu doğrulama yerine geçmez."""
import pytest

from pay_harness import pay_create_order, pay_customer, pay_find, pay_seed_product

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n% test receipt \x00\x01\x02 binary\n%%EOF"


async def _upload(http, number, contact="", headers=None):
    return await http.post(f"/api/orders/by-number/{number}/payment-notification",
                           files={"file": ("dekont.pdf", PDF, "application/pdf")},
                           data={"contact": contact, "note": ""}, headers=headers or {})


async def test_logged_in_owner_needs_no_contact_but_others_do(pay_http, pay_env):
    owner, owner_hdr = await pay_customer(email="owner@example.test")
    _other, other_hdr = await pay_customer(email="other@example.test")
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(pay_http, "bank_transfer", headers=owner_hdr)
    assert r.status_code == 200, r.text
    o = await pay_find("orders", {"id": r.json()["order_id"]})
    assert o.get("user_id") == owner["id"]
    # başka üye: oturum kanıt değildir → doğrulama alanı zorunlu, yanlışsa 404
    assert (await _upload(pay_http, o["order_number"], headers=other_hdr)).status_code == 400
    assert (await _upload(pay_http, o["order_number"], "9999", headers=other_hdr)).status_code == 404
    # misafir (oturumsuz): eskisi gibi zorunlu
    assert (await _upload(pay_http, o["order_number"])).status_code == 400
    # sahip: doğrulama bilgisi olmadan yükler
    r = await _upload(pay_http, o["order_number"], headers=owner_hdr)
    assert r.status_code == 200, r.text
    o2 = await pay_find("orders", {"id": o["id"]})
    assert o2["payment_notified"] is True and o2["status"] == "payment_notified"
    assert o2.get("payment_status") != "paid"     # dekont ödeme ONAYI değildir
