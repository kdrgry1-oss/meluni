"""Security alert delivery: recipients from the business rule, fallback to super admins, Resend
payload/sender, delivery-failure recording, throttling."""
import json

import httpx
import pytest

from id_harness import id_seed_user, id_auth, id_findall, id_token, id_run, id_set_rule
from security import alerts

pytestmark = pytest.mark.usefixtures("id_env")

RESEND = "https://api.resend.com/emails"


def _transactional_sender():
    # the store's transactional mail account → sender domain fallback (no domain is hard-coded)
    from routes import deps
    id_run(deps.db.settings.insert_one({"id": "email_smtp", "username": "noreply@shop-domain.test"}))


@pytest.fixture
def resend(id_respx, monkeypatch):
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    _transactional_sender()
    route = id_respx.post(RESEND).mock(return_value=httpx.Response(200, json={"id": "msg-1"}))
    return route


def _payloads(route):
    return [json.loads(c.request.content) for c in route.calls]


def test_rule_recipients_each_receive_with_rule_sender(id_client, resend):
    sa = id_seed_user(kind="super", email="owner@example.test")
    id_set_rule("security.alert_emails", "Sec-One@Example.test, sec-two@example.test\nsec-one@example.test")
    id_set_rule("security.alert_from", "info@shop-mail.test")
    r = id_client.post("/api/admin/security/alerts/test", headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(x["to"] for x in body["recipients"]) == ["sec-one@example.test", "sec-two@example.test"]
    assert all(x["ok"] and x["resend"] for x in body["recipients"])
    sent = _payloads(resend)
    assert sorted(p["to"][0] for p in sent) == ["sec-one@example.test", "sec-two@example.test"]
    assert all(len(p["to"]) == 1 for p in sent)  # one delivery per recipient, no shared To: list
    assert all("info@shop-mail.test" in p["from"] for p in sent)
    assert all(c.request.headers["Authorization"] == "Bearer re_test_dummy_key" for c in resend.calls)
    assert "owner@example.test" not in [p["to"][0] for p in sent]  # rule overrides the super-admin fallback
    doc = id_findall("alerts", {"kind": "manual_test"})[0]
    assert doc["delivered"]["resend"] is True and not doc.get("delivery_error")
    # the test action itself is audited
    assert "security.alerts_test" in [x.get("action") for x in id_findall("audit_logs")]


def test_env_sender_overrides_rule(id_client, resend, monkeypatch):
    id_seed_user(kind="super", email="owner@example.test")
    id_set_rule("security.alert_from", "info@shop-mail.test")
    monkeypatch.setenv("RESEND_FROM", "Ops <ops@verified.test>")
    res = id_run(alerts.send_alert("unit", "Env sender", "b", fingerprint="fp-env"))
    assert res["recipients"][0]["ok"]
    assert _payloads(resend)[0]["from"] == "Ops <ops@verified.test>"


def test_fallback_to_active_super_admins(id_client, resend):
    id_seed_user(kind="super", email="sa-a@example.test")
    id_seed_user(kind="super", email="sa-b@example.test")
    id_seed_user(kind="super", email="sa-off@example.test", is_active=False)
    from id_harness import id_seed_role
    id_seed_role("r_v", ["orders.view"])
    id_seed_user(kind="staff", role_id="r_v", email="staff@example.test")
    id_seed_user(kind="customer", email="cust@example.test")
    res = id_run(alerts.send_alert("unit", "Fallback", "body", fingerprint="fp-fallback"))
    assert sorted(x["to"] for x in res["recipients"]) == ["sa-a@example.test", "sa-b@example.test"]
    sent = _payloads(resend)
    assert sorted(p["to"][0] for p in sent) == ["sa-a@example.test", "sa-b@example.test"]
    assert all(p["from"].endswith("<guvenlik@shop-domain.test>") for p in sent)  # derived store sender


def _store_mail(monkeypatch, ok: bool):
    """Mağaza işlemsel mail altyapısı (yedek kanal) — başarılı ya da başarısız taklit."""
    sent = []

    async def _fake(subject, text, target):
        sent.append(target)
        return (True, "") if ok else (False, "store_mail:email_not_configured")
    monkeypatch.setattr(alerts, "_send_store_mail", _fake)
    return sent


def test_no_sender_configured_is_recorded_not_guessed(id_client, id_respx, monkeypatch):
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    _store_mail(monkeypatch, ok=False)
    route = id_respx.post(RESEND).mock(return_value=httpx.Response(200, json={"id": "x"}))
    id_seed_user(kind="super", email="owner@example.test")
    res = id_run(alerts.send_alert("unit", "No sender", "b", fingerprint="fp-nosender"))
    assert res["recipients"][0]["ok"] is False and "resend_no_sender" in res["recipients"][0]["error"]
    assert not route.calls


def test_resend_403_falls_back_to_store_mail_and_records_reason(id_client, id_respx, monkeypatch):
    """Resend alan adı doğrulanmamışsa (403) uyarı mağaza mail altyapısıyla yine ulaşır; Resend
    nedeni teşhis için kaydedilir."""
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    _transactional_sender()
    sent = _store_mail(monkeypatch, ok=True)
    id_respx.post(RESEND).mock(return_value=httpx.Response(403, json={"message": "domain not verified"}))
    id_seed_user(kind="super", email="owner@example.test")
    res = id_run(alerts.send_alert("unit", "Rejected", "body", fingerprint="fp-403"))
    assert res["throttled"] is False
    r0 = res["recipients"][0]
    assert r0["ok"] is True and r0["store"] is True and r0["resend"] is False
    assert r0["warning"].startswith("resend_http_403") and "domain not verified" in r0["warning"]
    assert sent == ["owner@example.test"]
    doc = id_findall("alerts", {"id": res["alert_id"]})[0]
    assert doc["delivered"]["store"] is True and "delivery_error" not in doc


def test_all_channels_failing_recorded_as_delivery_failure(id_client, id_respx, monkeypatch):
    monkeypatch.setattr(alerts, "RESEND_API_KEY", "re_test_dummy_key")
    _transactional_sender()
    _store_mail(monkeypatch, ok=False)
    id_respx.post(RESEND).mock(return_value=httpx.Response(403, json={"message": "domain not verified"}))
    id_seed_user(kind="super", email="owner@example.test")
    res = id_run(alerts.send_alert("unit", "Rejected", "body", fingerprint="fp-403b"))
    r0 = res["recipients"][0]
    assert r0["ok"] is False
    assert "resend_http_403" in r0["error"] and "store_mail" in r0["error"]
    doc = id_findall("alerts", {"id": res["alert_id"]})[0]
    assert "resend_http_403" in doc["delivery_error"]


def test_no_recipients_is_recorded(id_client, resend):
    res = id_run(alerts.send_alert("unit", "Nobody", "body", fingerprint="fp-none"))
    assert res["recipients"] == []
    assert id_findall("alerts", {"id": res["alert_id"]})[0]["delivery_error"] == "no_recipients"
    assert not resend.calls


def test_throttling_per_fingerprint(id_client, resend):
    id_seed_user(kind="super", email="owner@example.test")
    first = id_run(alerts.send_alert("unit", "Storm", "1", fingerprint="fp-storm"))
    second = id_run(alerts.send_alert("unit", "Storm", "2", fingerprint="fp-storm"))
    other = id_run(alerts.send_alert("unit", "Storm", "3", fingerprint="fp-other"))
    assert first["throttled"] is False and second["throttled"] is True and other["throttled"] is False
    assert len(resend.calls) == 2  # first + other; the duplicate was not e-mailed
    assert len(id_findall("alerts", {"fingerprint": "fp-storm"})) == 2  # but both are kept in-app


def test_security_event_fans_out_to_rule_recipients(id_client, resend):
    """End-to-end: a real security event (vault reveal) reaches the configured recipients."""
    sa = id_seed_user(kind="super", email="owner@example.test")
    id_set_rule("security.alert_emails", "soc@example.test")
    from security.crypto import encrypt
    from routes import deps
    id_run(deps.db.vault_secrets.insert_one({"key": "k", "value_enc": encrypt("v-123456"), "scope": "global"}))
    r = id_client.get("/api/admin/vault/secret/k/reveal", headers=id_auth(id_token(sa)))
    assert r.status_code == 200
    sent = _payloads(resend)
    assert [p["to"] for p in sent] == [["soc@example.test"]]
    assert "v-123456" not in json.dumps(sent)  # the secret itself never goes into the alert
