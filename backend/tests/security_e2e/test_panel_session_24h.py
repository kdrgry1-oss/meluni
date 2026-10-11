"""Kullanıcı isteği: panele girenlerin oturumu aynı cihazda 24 saat sonlanmasın."""
import pytest

from conftest import kv_run
from kv_harness import kv_find


def _rules():
    return (kv_find("settings", {"id": "business_rules"}) or {}).get("values") or {}


def test_defaults_are_24_hours(monkeypatch):
    import business_rules
    from routes import deps
    monkeypatch.delenv("STAFF_TOKEN_TTL_HOURS", raising=False)
    assert business_rules._default("panel.auto_logout_minutes") == 1440
    assert deps._staff_token_ttl().total_seconds() == 24 * 3600


@pytest.mark.parametrize("before,after", [(60, 1440), (15, 1440), (0, 0), (2880, 2880), (None, None)])
def test_one_time_migration_raises_short_idle_timeout_once(before, after):
    import business_rules
    import scheduler
    if before is not None:
        kv_run(business_rules.save_rules(scheduler_db(), {"panel.auto_logout_minutes": before}))
    kv_run(scheduler._ensure_panel_session_24h())
    assert _rules().get("panel.auto_logout_minutes") == after
    assert kv_run(business_rules.get_rule(scheduler_db(), "panel.auto_logout_minutes")) == (after if after is not None else 1440)
    # kullanıcı sonradan değiştirirse tek seferlik iş tekrar EZMEZ
    kv_run(business_rules.save_rules(scheduler_db(), {"panel.auto_logout_minutes": 30}))
    kv_run(scheduler._ensure_panel_session_24h())
    assert _rules()["panel.auto_logout_minutes"] == 30


def scheduler_db():
    from routes.deps import db
    return db
