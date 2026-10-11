import abandoned_cart_mail as m


def test_fmt_tl():
    assert m.fmt_tl(1299.9) == "1.299,90 TL"
    assert m.fmt_tl(0) == "0,00 TL"


def test_price_view_matches_storefront_rules():
    # indirimli fiyat varsa o geçerli, kampanya uygulanmaz
    v = m.price_view({"price": 2400, "sale_price": 1500, "campaign_discount_percent": 20})
    assert (v["list"], v["display"], v["discount_pct"]) == (2400, 1500, 38)
    # indirimli fiyat yoksa otomatik kampanya
    v = m.price_view({"price": 5790, "campaign_discount_percent": 20})
    assert (v["display"], v["discount_pct"]) == (4632, 20)
    # indirim yok
    v = m.price_view({"price": 1000})
    assert (v["display"], v["discount_pct"]) == (1000, 0)


def test_html_grid_escapes_and_centers_odd_card():
    it = {"name": "<b>X</b>", "image": "https://x/y.jpg", "url": "https://s/x", "list": 100.0,
          "display": 80.0, "discount_pct": 20, "size": "M", "qty": 1}
    html = m.build_html(brand={"store_name": "S", "site_url": "https://s"}, items=[it, it, it],
                        cta_url="https://s/sepet?paylasim=a&hatirlatma=1", unsub_url="https://api/u")
    assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html
    assert html.count('colspan="2" align="center"') == 1      # 3. kart ortalı
    assert "%20" in html and "Abonelikten çık" in html
    html2 = m.build_html(brand={"store_name": "S"}, items=[it, it], cta_url="c", unsub_url="")
    assert 'colspan="2" align="center"' not in html2


def test_jpeg_url_roundtrip():
    import base64
    u = m.jpeg_url("https://api.x", "https://cdn.x/a.webp")
    tok = u.split("/jpeg/")[1].split(".jpg")[0]
    assert base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)).decode() == "https://cdn.x/a.webp"
