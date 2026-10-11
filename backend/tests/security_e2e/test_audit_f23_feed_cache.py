"""Denetim #23: herkese açık merchant feed her istekte yeniden üretilmez (TTL önbellek + tek üretim),
dar projeksiyonla okunur ve hız sınırlıdır."""
import asyncio

import routes.products as rp
from conftest import kv_run


def test_feed_built_once_and_cached(kv_client, kv_db, monkeypatch):
    rp._FEED_CACHE.clear()
    kv_run(kv_db.products.insert_one({"id": "f1", "slug": "f1", "name": "Ürün", "price": 10, "is_active": True,
                                      "images": ["https://x/y.jpg"], "trendyol_attributes": {"secret": 1}}))
    calls = []
    orig = rp._feed_site_shop_prods

    async def _counting():
        calls.append(1)
        site, shop, prods = await orig()
        assert all("trendyol_attributes" not in p for p in prods)   # dar projeksiyon
        return site, shop, prods
    monkeypatch.setattr(rp, "_feed_site_shop_prods", _counting)
    for _ in range(3):
        r = kv_client.get("/api/products/google-merchant-feed.xml")
        assert r.status_code == 200 and "f1" in r.text
        assert "max-age" in r.headers.get("cache-control", "")
    assert len(calls) == 1


def test_single_flight_concurrent_builds():
    rp._FEED_CACHE.clear()
    n = {"c": 0}

    async def _build():
        n["c"] += 1
        await asyncio.sleep(0.05)
        return "<xml/>"

    async def _go():
        return await asyncio.gather(*[rp._cached_feed_xml("k", _build) for _ in range(10)])
    assert asyncio.run(_go()) == ["<xml/>"] * 10
    assert n["c"] == 1
    rp._FEED_LOCKS.clear()
