"""INFRA-03 (2026-10 denetim #2) — herkese açık ödeme uçları iç istisna metnini istemciye sızdırmaz.

`/api/payment/3ds/initialize` (misafir checkout) iyzico'ya bağlanırken oluşan istisnanın HAM metnini
(`detail=f"iyzico bağlantı hatası: {e}"`) yanıta koyuyordu → iç ağ/proxy/host adları, TLS/soket
ayrıntıları kimliksiz istemciye dönüyordu. Ayrıntı yalnız sunucu loguna yazılır; istemci genel mesaj
alır. Ödeme statü mantığına DOKUNULMAZ (değişmez 1–6): sipariş awaiting_payment kalır.
"""
import httpx
import pytest

from pay_harness import IYZICO_BASE, pay_card_order, pay_find, pay_init_3ds

pytestmark = pytest.mark.asyncio

SENTINEL = "internal-proxy-10.9.8.7.railway.internal:3128"


async def test_3ds_init_connection_error_does_not_leak_exception_text(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    pay_env.iyzico.post(f"{IYZICO_BASE}/payment/3dsecure/initialize").mock(
        side_effect=httpx.ConnectError(f"cannot connect via {SENTINEL}"))
    r = await pay_init_3ds(pay_http, o["id"])
    assert r.status_code == 502
    assert SENTINEL not in r.text
    assert "iyzico" in r.json().get("detail", "")
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["status"] == "awaiting_payment" and cur["payment_status"] != "paid"
