"""Google Shopping XML feed import: XXE/entity refusal, stock & price protection, concurrency lock.

The network fetch (`_fetch_url_pinned`, HTTPS + pinned socket) is replaced by returning the test
feed bytes; everything after it (parse, merge rules, DB writes, lock) is the real endpoint.
"""
from __future__ import annotations

import pytest

from kv_harness import kv_find, kv_findall, kv_count, kv_insert, kv_set_rule, kv_mongomock_fam_fidelity  # noqa: F401
from conftest import KvUsers, kv_run

URL = "/api/integrations/xml/products/import"
FEED_URL = "https://feed.kv-test.invalid/google.xml"
NS = 'xmlns:g="http://base.google.com/ns/1.0"'


def _item(xml_id, title="Elbise", price="110.00 TRY", availability="in stock", qty=None, extra=""):
    p = f"<g:price>{price}</g:price>" if price is not None else ""
    q = f"<g:quantity>{qty}</g:quantity>" if qty is not None else ""
    return (f"<item><g:id>{xml_id}</g:id><g:title>{title}</g:title>{p}"
            f"<g:availability>{availability}</g:availability>{q}"
            f"<g:image_link>https://cdn.kv-test.invalid/{xml_id}.jpg</g:image_link>"
            f"<g:link>https://shop.kv-test.invalid/{xml_id}-slug</g:link>{extra}</item>")


def _feed(*items) -> bytes:
    return (f'<?xml version="1.0"?><rss {NS} version="2.0"><channel><title>t</title>'
            + "".join(items) + "</channel></rss>").encode()


pytestmark = pytest.mark.usefixtures("kv_mongomock_fam_fidelity")


@pytest.fixture
def kv_feed(monkeypatch):
    """Serve `holder['bytes']` instead of fetching over the network."""
    import fastapi.concurrency as fc
    holder = {"bytes": _feed(), "fetches": 0}
    real = fc.run_in_threadpool

    async def _ritp(fn, *a, **k):
        if getattr(fn, "__name__", "") == "_fetch_url_pinned":
            holder["fetches"] += 1
            return holder["bytes"]
        return await real(fn, *a, **k)
    monkeypatch.setattr(fc, "run_in_threadpool", _ritp)
    return holder


def _run_import(client, user):
    return client.post(URL, params={"xml_url": FEED_URL}, headers=KvUsers.hdr(user))


def _existing(xml_id="X1", **extra):
    d = {"id": "p-" + xml_id, "xml_id": xml_id, "name": "Eski", "slug": "eski", "price": 100.0,
         "sale_price": 90.0, "stock": 0, "is_active": True, "images": ["old.jpg"], "source": "xml_feed"}
    d.update(extra)
    return kv_insert("products", d)


ENTITY_PAYLOADS = [
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item>'
    b'<g:id xmlns:g="http://base.google.com/ns/1.0">&x;</g:id></item></channel></rss>',
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
    b"<rss><channel><item>&lol2;</item></channel></rss>",
    b'<?xml version="1.0"?><!DOCTYPE r [<!eNtItY % p SYSTEM "http://169.254.169.254/">%p;]><rss/>',
]


@pytest.mark.parametrize("payload", ENTITY_PAYLOADS)
def test_entity_payload_rejected_422(kv_client, kv_users, kv_feed, payload):
    sa = kv_users.staff(super_admin=True)
    _existing()
    kv_feed["bytes"] = payload
    r = _run_import(kv_client, sa)
    assert r.status_code == 422, r.text
    assert "root:" not in r.text
    assert kv_count("products") == 1 and kv_find("products", {"xml_id": "X1"})["name"] == "Eski"
    # lock released after the failure
    assert kv_find("settings", {"id": "xml_feed_import_lock"})["running"] is False


def test_requires_staff(kv_client, kv_users, kv_feed):
    kv_feed["bytes"] = _feed(_item("N1"))
    assert kv_client.post(URL, params={"xml_url": FEED_URL}).status_code == 401
    assert _run_import(kv_client, kv_users.customer()).status_code == 403
    assert kv_feed["fetches"] == 0 and kv_count("products") == 0


def test_existing_stock_not_overwritten_unless_feed_is_stock_source(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    _existing("X1", stock=0)
    _existing("X2", stock=3)
    kv_feed["bytes"] = _feed(_item("X1", availability="in stock", qty=9),
                             _item("X2", availability="out of stock"))
    r = _run_import(kv_client, sa)
    assert r.status_code == 200, r.text
    assert kv_find("products", {"xml_id": "X1"})["stock"] == 0   # sold-out stays sold-out
    assert kv_find("products", {"xml_id": "X2"})["stock"] == 3
    assert kv_find("products", {"xml_id": "X1"})["name"] == "Elbise"  # other fields DO update

    # explicitly mark the feed as stock source → explicit quantity is written, availability-only is not
    from routes.deps import db
    kv_run(db.settings.update_one({"id": "xml_feed"}, {"$set": {"feed_is_stock_source": True}}, upsert=True))
    r = _run_import(kv_client, sa)
    assert r.status_code == 200, r.text
    assert kv_find("products", {"xml_id": "X1"})["stock"] == 9
    assert kv_find("products", {"xml_id": "X2"})["stock"] == 3


def test_variant_product_stock_never_overwritten(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_insert("settings", {"id": "xml_feed", "feed_is_stock_source": True})
    _existing("XV", stock=0, variants=[{"id": "v1", "size": "M", "stock": 0}])
    kv_feed["bytes"] = _feed(_item("XV", qty=50))
    assert _run_import(kv_client, sa).status_code == 200
    p = kv_find("products", {"xml_id": "XV"})
    assert p["stock"] == 0 and p["variants"][0]["stock"] == 0


def test_missing_price_leaves_price_untouched(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    _existing("X1", price=100.0, sale_price=90.0)
    kv_feed["bytes"] = _feed(_item("X1", price=None), _item("X3", price="abc TRY"), _item("NEW", price=None),
                             _item("X4", price="0.00 TRY"))
    _existing("X3", price=250.0)
    _existing("X4", price=40.0)
    r = _run_import(kv_client, sa)
    assert r.status_code == 200, r.text
    assert kv_find("products", {"xml_id": "X1"})["price"] == 100.0
    assert kv_find("products", {"xml_id": "X1"})["sale_price"] == 90.0
    assert kv_find("products", {"xml_id": "X3"})["price"] == 250.0
    assert kv_find("products", {"xml_id": "X4"})["price"] == 40.0
    new = kv_find("products", {"xml_id": "NEW"})
    assert "price" not in new or new["price"] not in (0, 0.0)
    assert new["is_active"] is False and new["deactivated_reason"] == "xml_price_missing"
    log = kv_findall("integration_logs", {"event": "price_needs_review"})
    assert log and set(log[0]["price_missing_xml_ids"]) >= {"X1", "X3", "NEW", "X4"}


@pytest.mark.parametrize("new_price,held", [("300.00 TRY", True), ("40.00 TRY", True), ("140.00 TRY", False)])
def test_large_price_jump_held_for_review(kv_client, kv_users, kv_feed, new_price, held):
    sa = kv_users.staff(super_admin=True)
    _existing("X1", price=100.0, sale_price=None)
    kv_feed["bytes"] = _feed(_item("X1", price=new_price))
    assert _run_import(kv_client, sa).status_code == 200
    p = kv_find("products", {"xml_id": "X1"})
    if held:
        assert p["price"] == 100.0
        assert p["xml_price_review"]["old"] == 100.0 and p["xml_price_review"]["field"] == "price"
    else:
        assert p["price"] == 140.0 and "xml_price_review" not in p


def test_manually_edited_price_is_kept(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    _existing("X1", price=100.0, price_by="admin@example.com")
    kv_feed["bytes"] = _feed(_item("X1", price="120.00 TRY"))
    assert _run_import(kv_client, sa).status_code == 200
    assert kv_find("products", {"xml_id": "X1"})["price"] == 100.0


def test_concurrent_import_409(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    _existing("X1")
    kv_feed["bytes"] = _feed(_item("X1", price="105.00 TRY"))
    from routes.integrations_common import _acquire_xml_import_lock, _release_xml_import_lock
    token = kv_run(_acquire_xml_import_lock("other-admin"))
    assert token
    r = _run_import(kv_client, sa)
    assert r.status_code == 409
    assert kv_feed["fetches"] == 0 and kv_find("products", {"xml_id": "X1"})["price"] == 100.0
    # a second acquisition is also refused while held
    assert kv_run(_acquire_xml_import_lock("third")) is None
    kv_run(_release_xml_import_lock(token))
    r = _run_import(kv_client, sa)
    assert r.status_code == 200, r.text
    assert kv_find("products", {"xml_id": "X1"})["price"] == 105.0
    assert kv_find("settings", {"id": "xml_feed_import_lock"})["running"] is False
    # only one lock document ever exists
    assert kv_count("settings", {"id": "xml_feed_import_lock"}) == 1


def test_stale_lock_is_taken_over(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_insert("settings", {"id": "xml_feed_import_lock", "running": True, "token": "dead",
                           "started_at": "2020-01-01T00:00:00+00:00"})
    kv_feed["bytes"] = _feed(_item("N1"))
    assert _run_import(kv_client, sa).status_code == 200
