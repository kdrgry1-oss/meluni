"""T10 / COM15 / FIN-E04 / CHAIN05 — XML feed: parse budgets, import manifest, fencing.

* T10: element depth cap, total element cap and wall-clock parse budget (parse runs in a dedicated
  worker thread; budget enforced inside the parser loop) → 422, quickly, nothing written.
* COM15/FIN-E04: every import attempt writes a manifest (sha256, item count, token-free source,
  generated_at / sequence). An import whose item count drops >40 % vs the last applied one, or whose
  generation timestamp / sequence is older than the last applied one, is rejected (unless a SUPER
  admin forces it) and an "affected products" report is written.
* CHAIN05: the import lock carries a token; every write re-checks it. A stale worker whose lock was
  taken over stops writing (409) and does not release the new owner's lock.

The network fetch (`_fetch_url_pinned`) is replaced by returning the test bytes; everything after
it (parse, integrity checks, merge, DB writes, lock) is the real endpoint.
"""
from __future__ import annotations

import threading
import time

import pytest

from kv_harness import kv_find, kv_findall, kv_count, kv_insert, kv_mongomock_fam_fidelity  # noqa: F401
from conftest import KvUsers, kv_run

URL = "/api/integrations/xml/products/import"
FEED_URL = "https://feed.kv-test.invalid/google.xml?token=kv-secret-token"
NS = 'xmlns:g="http://base.google.com/ns/1.0"'

pytestmark = pytest.mark.usefixtures("kv_mongomock_fam_fidelity")


def _item(xml_id, price="110.00 TRY", title="Elbise"):
    return (f"<item><g:id>{xml_id}</g:id><g:title>{title}</g:title><g:price>{price}</g:price>"
            f"<g:availability>in stock</g:availability>"
            f"<g:link>https://shop.kv-test.invalid/{xml_id}-slug</g:link></item>")


def _feed(*items, build=None, seq=None) -> bytes:
    meta = (f"<lastBuildDate>{build}</lastBuildDate>" if build else "") + \
           (f"<sequence>{seq}</sequence>" if seq is not None else "")
    return (f'<?xml version="1.0"?><rss {NS} version="2.0"><channel><title>t</title>{meta}'
            + "".join(items) + "</channel></rss>").encode()


def _n_items(n, prefix="X", price="110.00 TRY"):
    return [_item(f"{prefix}{i}", price=price) for i in range(n)]


@pytest.fixture
def kv_feed(monkeypatch):
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


def _run(client, user, **params):
    return client.post(URL, params={"xml_url": FEED_URL, **params}, headers=KvUsers.hdr(user))


# ═════════════════════════════════════ T10: parse budgets ═════════════════════════════════════
def test_deep_nesting_rejected_fast_nothing_written(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    depth = 200_000
    kv_feed["bytes"] = (f"<rss {NS}><channel>".encode() + b"<a>" * depth + b"x" + b"</a>" * depth
                        + b"</channel></rss>")
    t0 = time.monotonic()
    r = _run(kv_client, sa)
    assert r.status_code == 422, r.text
    assert "derinlik" in r.json()["detail"]
    assert time.monotonic() - t0 < 5
    assert kv_count("products") == 0 and kv_count("feed_import_manifests") == 0
    assert kv_find("settings", {"id": "xml_feed_import_lock"})["running"] is False


def test_element_count_cap_rejected(kv_client, kv_users, kv_feed, monkeypatch):
    import routes.integrations_common as ic
    monkeypatch.setattr(ic, "_XML_FEED_MAX_ELEMENTS", 500)
    sa = kv_users.staff(super_admin=True)
    kv_feed["bytes"] = _feed(*_n_items(200))      # 200 items × 6 elements > 500
    r = _run(kv_client, sa)
    assert r.status_code == 422 and "eleman" in r.json()["detail"]
    assert kv_count("products") == 0


def test_parse_time_budget_aborts_large_payload(kv_client, kv_users, kv_feed, monkeypatch):
    import routes.integrations_common as ic
    monkeypatch.setattr(ic, "_XML_FEED_PARSE_BUDGET_S", 0.02)
    sa = kv_users.staff(super_admin=True)
    big = _feed(*_n_items(60_000))                # ~10 MB, far more work than 20 ms
    assert len(big) > 8 * 1024 * 1024
    kv_feed["bytes"] = big
    t0 = time.monotonic()
    r = _run(kv_client, sa)
    elapsed = time.monotonic() - t0
    assert r.status_code == 422, r.text
    assert "süre" in r.json()["detail"]
    assert elapsed < 5, elapsed
    assert kv_count("products") == 0


def test_budget_is_enforced_inside_the_worker_and_parse_runs_off_loop(monkeypatch):
    import asyncio
    import routes.integrations_common as ic
    seen = {}
    real = ic._safe_parse_feed_xml

    def _spy(b, *a, **k):
        seen["thread"] = threading.current_thread().name
        return real(b, *a, **k)
    monkeypatch.setattr(ic, "_safe_parse_feed_xml", _spy)
    root = asyncio.run(ic._parse_feed_with_budget(_feed(_item("A1"))))
    assert root.tag == "rss" and seen["thread"].startswith("xml-feed-parse")
    # direct: the in-loop deadline stops the parser itself (the thread does not keep burning CPU)
    big = _feed(*_n_items(60_000))
    t0 = time.monotonic()
    with pytest.raises(ic._FeedParseTimeout):
        real(big, budget_s=0.01)
    assert time.monotonic() - t0 < 2


def test_internal_dtd_subset_rejected():
    import xml.etree.ElementTree as ET
    import routes.integrations_common as ic
    with pytest.raises(ET.ParseError):
        ic._safe_parse_feed_xml(b'<?xml version="1.0"?><!DOCTYPE r [<!ATTLIST r a CDATA "x">]><r/>')


# ═════════════════════════════ COM15 / FIN-E04: manifest + integrity ═════════════════════════════
def test_manifest_recorded_without_token_and_report_written(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_feed["bytes"] = _feed(*_n_items(12), build="2026-10-01T10:00:00+00:00", seq=7)
    r = _run(kv_client, sa)
    assert r.status_code == 200, r.text
    body = r.json()
    m = kv_find("feed_import_manifests", {"id": body["manifest_id"]})
    import hashlib
    assert m["status"] == "applied" and m["item_count"] == 12 and m["sequence"] == 7
    assert m["sha256"] == hashlib.sha256(kv_feed["bytes"]).hexdigest()
    assert m["generated_at"].startswith("2026-10-01T10:00:00")
    assert "kv-secret-token" not in str(m) and m["source"] == "https://feed.kv-test.invalid/google.xml"
    rep = kv_find("feed_import_reports", {"id": body["report_id"]})
    assert rep["status"] == "applied" and len(rep["imported"]) == 12


def test_item_count_drop_over_40pct_rejected_with_affected_report(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_feed["bytes"] = _feed(*_n_items(20))
    assert _run(kv_client, sa).status_code == 200
    kv_insert("orders", {"id": "o-open", "order_number": "FCOPEN1", "status": "confirmed",
                         "items": [{"product_id": kv_find("products", {"xml_id": "X15"})["id"], "quantity": 1}]})
    # 11 items (45 % drop) and a price change on X0 → nothing must be written
    kv_feed["bytes"] = _feed(_item("X0", price="130.00 TRY"), *_n_items(11)[1:])
    r = _run(kv_client, sa)
    assert r.status_code == 422, r.text
    det = r.json()["detail"]
    assert any(x.startswith("item_count_drop:20->11") for x in det["reasons"])
    assert kv_find("products", {"xml_id": "X0"})["price"] == 110.0
    rep = kv_find("feed_import_reports", {"id": det["report_id"]})
    missing = {x["xml_id"] for x in rep["would_go_missing"]}
    assert missing == {f"X{i}" for i in range(11, 20)}
    assert [x["xml_id"] for x in rep["would_change_price"]] == ["X0"]
    assert rep["open_orders"] == ["FCOPEN1"]
    assert kv_find("feed_import_manifests", {"report_id": det["report_id"]})["status"] == "rejected"
    assert kv_findall("integration_logs", {"event": "feed_integrity_rejected"})
    assert kv_findall("alerts", {"kind": "feed_integrity_rejected"})


def test_force_requires_super_admin_and_is_recorded(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    staff = kv_users.staff(perms=["integrations.manage", "products.edit"])
    kv_feed["bytes"] = _feed(*_n_items(20))
    assert _run(kv_client, sa).status_code == 200
    kv_feed["bytes"] = _feed(*_n_items(5))
    r = _run(kv_client, staff, force_integrity="true")
    assert r.status_code in (403,), r.text
    r = _run(kv_client, sa, force_integrity="true")
    assert r.status_code == 200, r.text
    assert r.json()["integrity_forced"] is True
    m = kv_find("feed_import_manifests", {"id": r.json()["manifest_id"]})
    assert m["status"] == "applied" and m["forced"] is True and m["item_count"] == 5
    assert kv_findall("alerts", {"kind": "feed_integrity_forced"})


def test_stale_or_replayed_feed_rejected(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    new = _feed(*_n_items(12, price="120.00 TRY"), build="Thu, 01 Oct 2026 12:00:00 GMT", seq=10)
    old = _feed(*_n_items(12, price="110.00 TRY"), build="Wed, 30 Sep 2026 12:00:00 GMT", seq=9)
    kv_feed["bytes"] = new
    assert _run(kv_client, sa).status_code == 200
    kv_feed["bytes"] = old
    r = _run(kv_client, sa)
    assert r.status_code == 422
    reasons = r.json()["detail"]["reasons"]
    assert any(x.startswith("stale_generated_at") for x in reasons)
    assert any(x.startswith("sequence_regressed") for x in reasons)
    assert kv_find("products", {"xml_id": "X3"})["price"] == 120.0
    rep = kv_find("feed_import_reports", {"id": r.json()["detail"]["report_id"]})
    assert len(rep["would_change_price"]) == 12
    # same sequence, different content → rejected as well
    kv_feed["bytes"] = _feed(*_n_items(12, price="125.00 TRY"), build="Thu, 01 Oct 2026 12:00:00 GMT", seq=10)
    r = _run(kv_client, sa)
    assert r.status_code == 422 and any("sequence_reused" in x for x in r.json()["detail"]["reasons"])
    # identical replay of the last applied feed is idempotent (accepted, marked duplicate)
    kv_feed["bytes"] = new
    r = _run(kv_client, sa)
    assert r.status_code == 200, r.text
    assert kv_find("feed_import_manifests", {"id": r.json()["manifest_id"]})["duplicate"] is True


def test_applied_report_lists_open_orders_with_changed_price(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_feed["bytes"] = _feed(*_n_items(3))
    assert _run(kv_client, sa).status_code == 200
    pid = kv_find("products", {"xml_id": "X1"})["id"]
    from kv_harness import kv_iso
    kv_insert("orders", {"id": "o-1", "order_number": "FCQUOTE", "status": "awaiting_payment",
                         "created_at": kv_iso(hours=-1), "items": [{"product_id": pid, "quantity": 1}]})
    kv_feed["bytes"] = _feed(_item("X0"), _item("X1", price="130.00 TRY"), _item("X2"))
    r = _run(kv_client, sa)
    assert r.status_code == 200, r.text
    rep = kv_find("feed_import_reports", {"id": r.json()["report_id"]})
    assert rep["price_changed"] == [pid] and rep["open_orders_with_price_change_24h"] == ["FCQUOTE"]


# ═════════════════════════════════════ CHAIN05: fencing ═════════════════════════════════════
def test_stale_worker_cannot_write_after_lock_takeover(kv_client, kv_users, kv_feed, monkeypatch):
    sa = kv_users.staff(super_admin=True)
    import routes.integrations_common as ic
    from routes.deps import db
    calls = {"n": 0}
    real_gen = ic.generate_short_id

    async def _gen(coll):
        calls["n"] += 1
        if calls["n"] == 1:
            # another worker declares this lock stale and takes it over (new token)
            await db.settings.update_one({"id": "xml_feed_import_lock"},
                                         {"$set": {"token": "new-owner", "running": True, "owner": "other"}})
        return await real_gen(coll)
    monkeypatch.setattr(ic, "generate_short_id", _gen)
    kv_feed["bytes"] = _feed(*_n_items(5))
    r = _run(kv_client, sa)
    assert r.status_code == 409, r.text
    assert "kilid" in r.json()["detail"].lower()
    # the fence runs immediately before each write → not even the in-flight item was written
    assert kv_count("products") == 0
    lock = kv_find("settings", {"id": "xml_feed_import_lock"})
    assert lock["token"] == "new-owner" and lock["running"] is True   # new owner's lock untouched
    assert not (kv_find("settings", {"id": "xml_feed"}) or {}).get("last_sync")
    assert kv_count("feed_import_manifests", {"status": "applied"}) == 0


def test_live_heartbeat_prevents_takeover_and_fence_unit():
    from routes.integrations_common import (_acquire_xml_import_lock, _xml_import_fence,
                                            _XmlImportLockLost, _release_xml_import_lock)
    from kv_harness import kv_iso
    kv_insert("settings", {"id": "xml_feed_import_lock", "running": True, "token": "alive",
                           "started_at": "2020-01-01T00:00:00+00:00", "heartbeat_at": kv_iso()})
    assert kv_run(_acquire_xml_import_lock("x")) is None            # heartbeat fresh → not stale
    kv_run(_xml_import_fence("alive"))                                # owner passes
    with pytest.raises(_XmlImportLockLost):
        kv_run(_xml_import_fence("someone-else"))
    kv_run(_release_xml_import_lock("alive"))
    tok = kv_run(_acquire_xml_import_lock("x"))
    assert tok and kv_find("settings", {"id": "xml_feed_import_lock"})["fence_epoch"] >= 1
    with pytest.raises(_XmlImportLockLost):
        kv_run(_xml_import_fence("alive"))                            # old token is fenced out


# ═══════════════════ CHAIN11: a new feed target does not inherit the old approval ═══════════════════
def test_changed_feed_source_needs_super_admin_force_but_token_rotation_does_not(kv_client, kv_users, kv_feed):
    sa = kv_users.staff(super_admin=True)
    kv_feed["bytes"] = _feed(*_n_items(12))
    assert _run(kv_client, sa).status_code == 200
    # token rotated in the query string → same source, accepted
    r = kv_client.post(URL, params={"xml_url": "https://feed.kv-test.invalid/google.xml?token=rotated"},
                       headers=KvUsers.hdr(sa))
    assert r.status_code == 200, r.text
    # different host → rejected until a super admin explicitly approves it
    other = "https://other-feed.kv-test.invalid/google.xml"
    r = kv_client.post(URL, params={"xml_url": other}, headers=KvUsers.hdr(sa))
    assert r.status_code == 422
    assert any(x.startswith("source_changed:") for x in r.json()["detail"]["reasons"])
    r = kv_client.post(URL, params={"xml_url": other, "force_integrity": "true"}, headers=KvUsers.hdr(sa))
    assert r.status_code == 200 and r.json()["integrity_forced"] is True
    r = kv_client.post(URL, params={"xml_url": other}, headers=KvUsers.hdr(sa))
    assert r.status_code == 200      # the approved target is the new baseline
