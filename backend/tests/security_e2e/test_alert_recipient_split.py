"""Virgüllü alıcı listesi (ör. stok tükenme uyarısı) her adrese AYRI gönderilir; tek dize olarak
gönderilince sağlayıcı reddediyor ve güvenlik hattı sahte 'kesinti' alarmı veriyordu."""
from conftest import kv_run


def test_comma_joined_to_email_is_split_and_does_not_trip_pipeline(monkeypatch, kv_db):
    import security.alerts as alerts
    from security.selftest import check_security_pipeline
    sent = []

    async def _deliver(subject, text, target):
        sent.append(target)
        ok = "," not in target and " " not in target           # sağlayıcı virgüllü adresi reddeder
        return {"to": target, "smtp": False, "resend": ok, "store": False, "ok": ok,
                "error": "" if ok else "resend_http_422", "warning": ""}
    monkeypatch.setattr(alerts, "_deliver", _deliver)

    res = kv_run(alerts.send_alert(kind="stockout_forecast", level="warning", title="Stok",
                                   body="x", fingerprint="stockout_split_test",
                                   to_email="Depo@Example.com, satis@example.com;depo@example.com"))
    assert sorted(sent) == ["depo@example.com", "satis@example.com"]
    doc = kv_run(kv_db.alerts.find_one({"id": res["alert_id"]}))
    assert not doc.get("delivery_error")
    pipe = kv_run(check_security_pipeline(alert=False, db=kv_db))
    assert "alert_delivery_failed" not in [p["type"] for p in pipe["problems"]]


def test_invalid_custom_recipient_falls_back_to_default_recipients(monkeypatch):
    import security.alerts as alerts
    sent = []

    async def _deliver(subject, text, target):
        sent.append(target)
        return {"to": target, "smtp": False, "resend": True, "store": False, "ok": True, "error": "", "warning": ""}

    async def _defaults():
        return ["sec@example.invalid"]
    monkeypatch.setattr(alerts, "_deliver", _deliver)
    monkeypatch.setattr(alerts, "resolve_default_recipients", _defaults)
    kv_run(alerts.send_alert(kind="stockout_forecast", level="warning", title="Stok", body="x",
                             fingerprint="stockout_fallback_test", to_email="not-an-address"))
    assert sent == ["sec@example.invalid"]
