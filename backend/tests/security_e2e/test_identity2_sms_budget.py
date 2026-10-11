"""BL-006 / CHAIN13: global SMS spend cap (hourly + daily), admin-MFA reserve, one alert per window,
fail-closed counter store — exercised through the REAL notification_service send path with a fake
provider registered in SMS_IMPL (so the single SMS gate ``_sms_guard`` is what is tested).
Also AUTH23: the OTP value never reaches a log line or notification_logs."""
import logging
from datetime import timedelta

import pytest
from freezegun import freeze_time

import notification_service as ns
from id_harness import id_seed_user, id_ip, id_find, id_findall, id_run, id_set_rule, ID_PASSWORD
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

# Captured at import time (before any fixture swaps it for the outbox mock).
REAL_SEND = ns.send_notification


@pytest.fixture
def sms_sink(monkeypatch):
    sent = []

    async def _fake_provider(cfg, to, message, *a, **k):
        sent.append({"to": to, "message": message})
        return {"success": True, "response": "00 test"}
    monkeypatch.setitem(ns.SMS_IMPL, "kvfake", ns._sms_guard(_fake_provider))
    id_run(deps.db.settings.insert_one({"id": "notification_providers", "sms_active": "kvfake",
                                        "whatsapp_active": False, "email_active": False, "providers": {}}))
    ns._SMS_BLOCK.update({"set": set(), "at": 0.0})
    return sent


def _send(event="password_reset_otp", phone="05551112233", code="123456"):
    return id_run(REAL_SEND(deps.db, event, to_phone=phone, variables={"otp_code": code}, channels=["sms"]))


def _send_admin_mfa(phone="05551112233", code="654321"):
    async def _go():
        with ns.sms_priority("admin_mfa"):
            return await REAL_SEND(deps.db, "login_verification_code", to_phone=phone,
                                   variables={"otp_code": code}, channels=["sms"])
    return id_run(_go())


def test_every_sms_path_uses_the_single_guard():
    # every provider implementation and the generic fallback are wrapped by the same gate
    for k, fn in ns.SMS_IMPL.items():
        assert fn.__code__ is ns._sms_guard(lambda *a: None).__code__, k
    assert ns._sms_generic.__code__ is ns._sms_guard(lambda *a: None).__code__
    # the other modules that send SMS import the guarded objects from notification_service
    import routes.iys as iys
    assert iys.SMS_IMPL is ns.SMS_IMPL and iys._sms_generic is ns._sms_generic


def test_hourly_cap_blocks_routine_sms_but_admin_mfa_uses_reserve(sms_sink, id_env):
    id_set_rule("sms.global_hourly_cap", 5)
    id_set_rule("sms.global_daily_cap", 100)
    with freeze_time("2026-10-04 12:05:00") as frozen:
        for i in range(5):
            assert _send(code=f"10000{i}")["results"]["sms"]["success"] is True
        r = _send(code="999999")
        assert r["results"]["sms"]["success"] is False and r["results"]["sms"]["response"] == "sms_budget_exceeded"
        assert len(sms_sink) == 5
        # one critical alert for the window, not one per refused SMS
        _send(code="999998")
        alerts = id_findall("alerts", {"kind": "sms_budget_exceeded"})
        assert len(alerts) == 1 and alerts[0]["level"] == "critical"
        assert "security.sms_budget_exceeded" in [a.get("action") for a in id_findall("audit_logs")]
        # admin MFA codes still go out, up to the small reserve (max(10, cap/20) = 10)
        ok = [_send_admin_mfa(code=f"2000{i:02d}")["results"]["sms"]["success"] for i in range(12)]
        assert ok == [True] * 10 + [False] * 2
        assert len(sms_sink) == 15
        # refused attempts are visible in the notification log (masked, without the code)
        logs = id_findall("notification_logs", {"response": "sms_budget_exceeded"})
        assert logs and all(l["variables"].get("otp_code") == "***" for l in logs)
        # next hour: routine SMS flows again (daily cap not reached)
        frozen.tick(timedelta(hours=1))
        assert _send(code="300000")["results"]["sms"]["success"] is True


def test_daily_cap_and_hourly_refund(sms_sink):
    id_set_rule("sms.global_hourly_cap", 100)
    id_set_rule("sms.global_daily_cap", 3)
    with freeze_time("2026-10-04 06:00:00") as frozen:
        for i in range(3):
            assert _send(code=f"40000{i}")["results"]["sms"]["success"] is True
            frozen.tick(timedelta(hours=1))
        assert _send(code="400009")["results"]["sms"]["response"] == "sms_budget_exceeded"
        # the refused SMS did not consume the hourly window (refund)
        assert id_find("sms_budget", {"_id": "h:2026100409"})["count"] == 0
        assert len(id_findall("alerts", {"kind": "sms_budget_exceeded"})) == 1
        frozen.tick(timedelta(days=1))
        assert _send(code="400010")["results"]["sms"]["success"] is True


def test_counter_store_outage_fails_closed(sms_sink, monkeypatch):
    coll_cls = type(deps.db.sms_budget)
    orig = coll_cls.find_one_and_update

    async def _boom(self, *a, **k):
        if self.name == "sms_budget":
            raise RuntimeError("counter store down (test)")
        return await orig(self, *a, **k)
    monkeypatch.setattr(coll_cls, "find_one_and_update", _boom)
    r = _send()
    assert r["results"]["sms"]["success"] is False and r["results"]["sms"]["response"] == "sms_budget_unavailable"
    assert _send_admin_mfa()["results"]["sms"]["success"] is False
    assert not sms_sink


def test_cap_rules_are_super_admin_only(id_client):
    from id_harness import id_seed_role, id_token, id_auth
    id_seed_role("r_rules", ["settings.site"])
    staff = id_seed_user(kind="staff", role_id="r_rules")
    sa = id_seed_user(kind="super")
    r = id_client.put("/api/admin/business-rules", json={"sms.global_daily_cap": 100000}, headers=id_auth(id_token(staff)))
    assert r.status_code == 403
    r = id_client.put("/api/admin/business-rules", json={"sms.global_daily_cap": 5000}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    assert "business_rules.security_update" in [a.get("action") for a in id_findall("audit_logs")]


def test_admin_mfa_login_sms_runs_with_admin_priority(id_client, monkeypatch, id_outbox):
    seen = []
    import notification_service

    async def _spy(db, event, *a, **k):
        seen.append((event, ns._SMS_PRIORITY.get()))
        return {"success": True}
    monkeypatch.setattr(notification_service, "send_notification", _spy)
    admin = id_seed_user(kind="super", phone="05551119988")
    r = id_client.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD},
                       headers=id_ip("198.51.100.180"))
    assert r.status_code == 200 and r.json()["mfa_required"]
    assert ("login_verification_code", "admin_mfa") in seen
    # a customer's SMS (password reset) never gets the reserve
    assert ns._SMS_PRIORITY.get() == "normal"


# ─────────────────────────────── AUTH23 ───────────────────────────────

def test_otp_values_never_logged(id_client, id_outbox, sms_sink, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    from test_identity2_common import id2_async, id2_no_ip_limit
    id2_no_ip_limit(monkeypatch)
    import asyncio
    from datetime import datetime, timezone
    import re
    # 1) staff MFA login code
    admin = id_seed_user(kind="super", phone="05551117766")
    id_client.post("/api/auth/login", json={"email": admin["email"], "password": ID_PASSWORD},
                   headers=id_ip("198.51.100.181"))
    codes = [id_outbox.last_code("sms")]
    # 2) customer reset OTP (+ real provider path through notification_service)
    id_seed_user(kind="customer", phone="05551115544")

    async def scen(c):
        await c.post("/api/auth/forgot-password/request-otp", json={"phone": "05551115544"})
        await asyncio.sleep(0.05)
    id2_async(scen)
    codes.append([n for n in id_outbox.notifications if n["event"] == "password_reset_otp"][-1]["variables"]["otp_code"])
    _send(code="731954")
    codes.append("731954")
    # 3) guest-convert e-mail code
    id_run(deps.db.orders.insert_one({"id": "o-log-1", "order_number": "L1", "created_at": datetime.now(timezone.utc).isoformat(),
                                      "shipping_address": {"email": "guest-log@example.test"}}))
    id_client.post("/api/auth/guest-convert/send-code", json={"order_id": "o-log-1"}, headers=id_ip("198.51.100.182"))
    codes.append(re.search(r"letter-spacing:6px[^>]*>(\d{6})<", id_outbox.emails[-1]["html"]).group(1))
    # wrong attempts are logged as failures — still without the submitted value
    id_client.post("/api/auth/mfa/verify", json={"mfa_token": "x" * 40, "code": "482913"}, headers=id_ip("198.51.100.183"))
    codes.append("482913")
    text = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert caplog.records, "log capture must see the flow"
    for c in codes:
        assert c and c not in text, f"OTP {c} leaked into logs"
    # persisted logs: codes only as hashes / "***"
    for coll in ("notification_logs", "auth_audit_logs", "audit_logs", "password_reset_otps", "mfa_sms_codes",
                 "guest_convert_codes", "alerts"):
        dump = repr(id_findall(coll))
        for c in codes:
            assert c not in dump, f"OTP {c} persisted in {coll}"
