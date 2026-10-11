"""T17 — integration freshness alarms (scheduler job `check_integration_freshness`, every 15 min).

A marketplace order pull / stock push (or the XML feed, when an expected interval is configured)
that has not SUCCEEDED within N× its schedule interval (default 5×, at least 30 min) raises one
alert per window; a later success sends one "recovered" notice. Time is driven with freezegun.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from kv_harness import kv_find, kv_findall, kv_insert
from conftest import kv_run

T0 = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)


def _log(mp, action, status, at):
    kv_insert("integration_logs", {"marketplace": mp, "action": action, "status": status,
                                   "created_at": at.isoformat()})


def _account(key="trendyol", orders=True, products=False, orders_iv=2, products_iv=3, enabled=True):
    kv_insert("marketplace_accounts", {"key": key, "enabled": enabled, "auto_sync": {
        "orders_enabled": orders, "products_enabled": products,
        "orders_interval_min": orders_iv, "products_interval_min": products_iv}})


def _check(at):
    import scheduler
    with freeze_time(at, real_asyncio=True):
        return kv_run(scheduler.check_integration_freshness())


def _stale_alerts():
    return kv_findall("alerts", {"kind": "integration_stale"})


@pytest.fixture(autouse=True)
def _no_amazon(monkeypatch):
    import scheduler

    async def _no():
        return False
    monkeypatch.setattr(scheduler, "_freshness_amazon_connected", _no)


def test_stale_order_pull_alerts_once_per_window_then_recovers():
    _account("trendyol", orders=True, orders_iv=2)
    _log("trendyol", "order_pull", "success", T0)
    _log("trendyol", "order_pull", "queued", T0 + timedelta(minutes=50))   # queued ≠ success
    _log("trendyol", "order_pull", "failed", T0 + timedelta(minutes=55))

    r = _check(T0 + timedelta(minutes=10))
    assert r["trendyol_orders"]["status"] == "ok" and not _stale_alerts()
    # threshold = max(30, 5×2) = 30 min
    r = _check(T0 + timedelta(minutes=31))
    assert r["trendyol_orders"] == {"status": "stale", "age_min": 31.0, "threshold_min": 30.0, "alerted": True}
    assert len(_stale_alerts()) == 1
    a = _stale_alerts()[0]
    assert "Trendyol sipariş çekme" in a["title"] and a["level"] == "warning"
    # same window → no second alert (job runs every 15 min)
    r = _check(T0 + timedelta(minutes=46))
    assert r["trendyol_orders"]["alerted"] is False and len(_stale_alerts()) == 1
    # next window → reminded once more
    r = _check(T0 + timedelta(minutes=62))
    assert r["trendyol_orders"]["alerted"] is True and len(_stale_alerts()) == 2
    assert kv_findall("integration_logs", {"event": "integration_stale", "source": "trendyol_orders"})
    st = kv_find("settings", {"id": "integration_freshness_state"})["sources"]["trendyol_orders"]
    assert st["stale"] is True and st["stale_since"]
    # recovery → single "recovered" notice, state cleared
    _log("trendyol", "order_pull", "success", T0 + timedelta(minutes=70))
    r = _check(T0 + timedelta(minutes=75))
    assert r["trendyol_orders"]["status"] == "ok"
    assert len(kv_findall("alerts", {"kind": "integration_recovered"})) == 1
    _check(T0 + timedelta(minutes=90))
    assert len(kv_findall("alerts", {"kind": "integration_recovered"})) == 1
    assert kv_find("settings", {"id": "integration_freshness_state"})["sources"]["trendyol_orders"]["stale"] is False


def test_threshold_scales_with_interval_and_multiplier_setting():
    _account("hepsiburada", orders=False, products=True, products_iv=20)
    _log("hepsiburada", "stock_update", "success", T0)
    # 5 × 20 = 100 min
    assert _check(T0 + timedelta(minutes=90))["hepsiburada_stock"]["status"] == "ok"
    assert _check(T0 + timedelta(minutes=101))["hepsiburada_stock"]["status"] == "stale"
    # admin lowers multiplier to 3 → 60 min (setting, not code)
    kv_insert("settings", {"id": "integration_freshness", "multiplier": 3})
    kv_run(__import__("routes.deps", fromlist=["db"]).db.settings.delete_one({"id": "integration_freshness_state"}))
    r = _check(T0 + timedelta(minutes=61))
    assert r["hepsiburada_stock"]["threshold_min"] == 60.0 and r["hepsiburada_stock"]["status"] == "stale"


def test_disabled_or_unconfigured_sources_and_outbound_pause_are_silent(monkeypatch):
    _account("trendyol", orders=True, enabled=False)          # account disabled
    _account("hepsiburada", orders=False, products=False)     # nothing enabled
    r = _check(T0 + timedelta(days=2))
    assert r == {} and not _stale_alerts()
    # enabled but outbound writes paused (kill switch / restore mode) → paused, no alarm
    from routes.deps import db
    kv_run(db.marketplace_accounts.update_one({"key": "trendyol"}, {"$set": {"enabled": True}}))
    import scheduler

    async def _paused(kind="marketplace"):
        return True
    monkeypatch.setattr(scheduler, "_outbound_paused", _paused)
    r = _check(T0 + timedelta(days=3))
    assert r["trendyol_orders"] == {"status": "paused"} and not _stale_alerts()


def test_newly_enabled_source_without_history_gets_grace_period():
    _account("trendyol", orders=True)
    assert _check(T0)["trendyol_orders"]["status"] == "ok"              # monitoring starts now
    assert _check(T0 + timedelta(minutes=20))["trendyol_orders"]["status"] == "ok"
    r = _check(T0 + timedelta(minutes=40))
    assert r["trendyol_orders"]["status"] == "stale" and len(_stale_alerts()) == 1


def test_amazon_and_xml_feed_sources(monkeypatch):
    import scheduler

    async def _yes():
        return True
    monkeypatch.setattr(scheduler, "_freshness_amazon_connected", _yes)
    _log("amazon", "order_pull", "success", T0)
    _log("amazon", "stock_sync", "partial", T0)
    kv_insert("settings", {"id": "xml_feed", "last_sync": T0.isoformat()})
    kv_insert("settings", {"id": "integration_freshness", "xml_feed_interval_hours": 24})
    r = _check(T0 + timedelta(minutes=29))
    assert r["amazon_orders"]["status"] == "ok" and r["amazon_stock"]["status"] == "ok"
    assert r["xml_feed"]["status"] == "ok" and r["xml_feed"]["threshold_min"] == 5 * 24 * 60
    r = _check(T0 + timedelta(minutes=35))
    assert r["amazon_orders"]["status"] == "stale" and r["amazon_stock"]["status"] == "stale"
    assert r["xml_feed"]["status"] == "ok"
    r = _check(T0 + timedelta(days=5, minutes=1))
    assert r["xml_feed"]["status"] == "stale"
    titles = {a["title"] for a in _stale_alerts()}
    assert "Entegrasyon durdu: Ürün XML feed içe aktarma" in titles


def test_job_registered_every_15_minutes_without_touching_existing_jobs():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    assert 'check_integration_freshness, "interval", minutes=15, id="integration_freshness_check"' in src


def _amazon_on(monkeypatch):
    import scheduler

    async def _yes():
        return True
    monkeypatch.setattr(scheduler, "_freshness_amazon_connected", _yes)


def test_amazon_quiet_but_healthy_run_heartbeat_prevents_false_alarm(monkeypatch):
    """Sipariş gelmeyen dönemde başarı logu yazılmaz; sağlıklı tur kalp atışı alarmı susturur."""
    import scheduler
    _amazon_on(monkeypatch)
    scheduler._freshness_hb_mem.clear()
    _log("amazon", "order_pull", "success", T0 - timedelta(days=30))
    with freeze_time(T0 + timedelta(minutes=50), real_asyncio=True):
        kv_run(scheduler.note_integration_heartbeat("amazon_orders"))
        kv_run(scheduler.note_integration_heartbeat("amazon_stock"))
    r = _check(T0 + timedelta(minutes=60))
    assert r["amazon_orders"]["status"] == "ok" and r["amazon_stock"]["status"] == "ok"
    assert not _stale_alerts()
    # kalp atışı süreç içinde 10 dk'da bir yazılır (her 1-2 dk değil)
    hb0 = kv_find("settings", {"id": "integration_heartbeats"})["sources"]["amazon_orders"]
    with freeze_time(T0 + timedelta(minutes=55), real_asyncio=True):
        kv_run(scheduler.note_integration_heartbeat("amazon_orders"))
    assert kv_find("settings", {"id": "integration_heartbeats"})["sources"]["amazon_orders"] == hb0


def test_amazon_stale_alert_includes_last_error(monkeypatch):
    import scheduler
    _amazon_on(monkeypatch)
    scheduler._freshness_hb_mem.clear()
    _log("amazon", "order_pull", "success", T0)
    _log("amazon", "stock_sync", "success", T0)
    kv_insert("integration_logs", {"marketplace": "amazon", "action": "order_pull", "status": "failed",
                                   "message": "[cron] Amazon getOrders HTTP 403: Unauthorized",
                                   "created_at": (T0 + timedelta(minutes=20)).isoformat()})
    _check(T0 + timedelta(minutes=40))
    body = next(a["body"] for a in _stale_alerts() if "Amazon sipariş" in a["title"])
    assert "Son hata: [cron] Amazon getOrders HTTP 403" in body
    stock = next(a["body"] for a in _stale_alerts() if "Amazon stok" in a["title"])
    assert "hata kaydı da yok" in stock


def test_amazon_jobs_write_heartbeat_only_on_healthy_runs():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler._run_amazon_auto_orders_pull)
    assert "_fetch_ok = False" in src and 'note_integration_heartbeat("amazon_orders")' in src
    assert src.index("if _fetch_ok:") < src.index('note_integration_heartbeat("amazon_orders")')
    assert 'note_integration_heartbeat("amazon_stock")' in inspect.getsource(scheduler._run_amazon_auto_stock_sync)


def _hb_setup(monkeypatch, client):
    import scheduler
    import routes.category_mapping as cm
    import routes.integrations_hepsiburada as ih
    scheduler._freshness_hb_mem.clear()

    async def _cli():
        return client, None

    async def _cargo(*a, **k):
        return {}
    monkeypatch.setattr(cm, "_get_hb_client", _cli)
    monkeypatch.setattr(ih, "hb_sync_cargo_tracking", _cargo)
    _account("hepsiburada", orders=True, orders_iv=2)
    _log("hepsiburada", "order_pull", "success", T0)


def _hb_run_quiet_period():
    import scheduler
    for m in range(2, 40, 2):
        scheduler._freshness_hb_mem.clear()   # süreç-içi 10 dk sınırı gerçek saate bağlı; testte her turu yaz
        with freeze_time(T0 + timedelta(minutes=m), real_asyncio=True):
            kv_run(scheduler._run_hepsiburada_auto_orders_pull())


def test_hepsiburada_quiet_open_order_list_is_healthy_not_stopped(monkeypatch):
    """Açık sipariş yokken HB boş liste döner; başarı logu yazılmaz ama kalp atışı 'durdu' alarmını önler."""
    class _Quiet:
        def get_orders(self, *a, **k):
            return {"items": [], "totalCount": 0}
    _hb_setup(monkeypatch, _Quiet())
    _hb_run_quiet_period()
    r = _check(T0 + timedelta(minutes=40))
    assert r["hepsiburada_orders"]["status"] == "ok" and not _stale_alerts()


def test_hepsiburada_failing_or_unrecognised_pull_still_alarms(monkeypatch):
    from hepsiburada_client import HepsiburadaError

    class _Down:
        def get_orders(self, *a, **k):
            raise HepsiburadaError("OMS HTTP 401: Unauthorized")
    _hb_setup(monkeypatch, _Down())
    _hb_run_quiet_period()
    r = _check(T0 + timedelta(minutes=40))
    assert r["hepsiburada_orders"]["status"] == "stale"
    assert "401" in next(a["body"] for a in _stale_alerts())
    assert not (kv_find("settings", {"id": "integration_heartbeats"}) or {}).get("sources", {}).get("hepsiburada_orders")


def test_hepsiburada_unexpected_response_shape_writes_no_heartbeat(monkeypatch):
    class _Weird:
        def get_orders(self, *a, **k):
            return {"message": "maintenance"}
    _hb_setup(monkeypatch, _Weird())
    _hb_run_quiet_period()
    assert _check(T0 + timedelta(minutes=40))["hepsiburada_orders"]["status"] == "stale"
