"""Marketing e-mail: GET-safe unsubscribe, POST/one-click unsubscribe + İYS RET, SES raw MIME headers,
mid-campaign opt-out honoured."""
from __future__ import annotations

import email
import threading
from urllib.parse import quote, urlsplit, parse_qs

import pytest

from kv_harness import kv_insert, kv_find, kv_findall, kv_count, kv_iso
from conftest import kv_run

UNSUB = "/api/email-marketing/unsubscribe"


def _sub(addr, sid=None, **extra):
    d = {"id": sid or f"sub-{addr.split('@')[0]}", "email": addr, "active": True, "consent": True,
         "consent_at": kv_iso(-30), "created_at": kv_iso(-30)}
    d.update(extra)
    return kv_insert("newsletter_subscribers", d)


def test_get_shows_confirmation_and_changes_nothing(kv_client, kv_users):
    s = _sub("reader@example.com")
    u = kv_users.customer(email="reader@example.com", accepts_marketing=True)
    r = kv_client.get(UNSUB, params={"e": s["email"], "t": s["id"]})
    assert r.status_code == 200
    assert '<form method="post"' in r.text and "reader@example.com" in r.text
    assert r.headers.get("cache-control", "").startswith("no-store")
    assert kv_find("newsletter_subscribers", {"id": s["id"]})["active"] is True
    assert kv_find("users", {"id": u["id"]})["accepts_marketing"] is True
    assert kv_count("iys_consents") == 0
    # link scanners following it repeatedly still change nothing
    for _ in range(3):
        kv_client.get(UNSUB, params={"e": s["email"], "t": s["id"]})
    assert kv_find("newsletter_subscribers", {"id": s["id"]})["active"] is True


def test_post_form_unsubscribes_and_records_iys_ret(kv_client, kv_users):
    s = _sub("reader@example.com")
    u = kv_users.customer(email="reader@example.com", accepts_marketing=True)
    r = kv_client.post(UNSUB, data={"e": s["email"], "t": s["id"]})
    assert r.status_code == 200 and "Abonelikten çıkarıldınız" in r.text
    sub = kv_find("newsletter_subscribers", {"id": s["id"]})
    assert sub["active"] is False and sub["unsubscribed_at"]
    usr = kv_find("users", {"id": u["id"]})
    assert usr["accepts_marketing"] is False and usr["marketing_unsubscribed_at"]
    ret = kv_findall("iys_consents", {"email": "reader@example.com"})
    assert len(ret) == 1 and ret[0]["status"] == "RET" and ret[0]["channels"] == ["EPOSTA"]


def test_one_click_post_rfc8058(kv_client):
    s = _sub("oneclick@example.com")
    r = kv_client.post(f"{UNSUB}?e={quote(s['email'])}&t={s['id']}", content=b"List-Unsubscribe=One-Click",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert kv_find("newsletter_subscribers", {"id": s["id"]})["active"] is False
    assert kv_count("iys_consents", {"email": "oneclick@example.com", "status": "RET"}) == 1


def test_one_click_for_profile_only_recipient_uses_hmac_token(kv_client, kv_users):
    from routes.email_marketing import _unsub_token
    u = kv_users.customer(email="profile.only@example.com", accepts_marketing=True)
    tok = _unsub_token(u["email"])
    r = kv_client.post(f"{UNSUB}?e={quote(u['email'])}&t={tok}", content=b"List-Unsubscribe=One-Click",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert kv_find("users", {"id": u["id"]})["accepts_marketing"] is False


@pytest.mark.parametrize("t", ["", "wrong", "sub-other", "u" + "0" * 28])
def test_unsubscribe_needs_valid_token(kv_client, kv_users, t):
    s = _sub("victim@example.com")
    _sub("other@example.com", sid="sub-other")
    u = kv_users.customer(email="victim@example.com", accepts_marketing=True)
    r = kv_client.post(UNSUB, data={"e": s["email"], "t": t})
    assert r.status_code == 200 and "geçersiz" in r.text
    assert kv_find("newsletter_subscribers", {"id": s["id"]})["active"] is True
    assert kv_find("users", {"id": u["id"]})["accepts_marketing"] is True
    assert kv_count("iys_consents") == 0


def test_unsubscribe_page_escapes_input(kv_client):
    s = _sub("x@example.com")
    r = kv_client.get(UNSUB, params={"e": '"><script>alert(1)</script>', "t": s["id"]})
    assert "<script>alert(1)</script>" not in r.text


# ───────────────────────────── SES campaign ─────────────────────────────
class _FakeSES:
    def __init__(self, on_send=None):
        self.raw = []
        self.on_send = on_send
        self.lock = threading.Lock()

    def send_raw_email(self, **kw):
        with self.lock:
            self.raw.append(kw)
            n = len(self.raw)
        if self.on_send:
            self.on_send(n, kw)
        return {"MessageId": f"m-{n}"}

    def send_email(self, **kw):  # the non-raw path must NOT be used for campaigns
        raise AssertionError("campaign used send_email (no List-Unsubscribe headers)")


@pytest.fixture
def kv_ses(monkeypatch):
    from security.crypto import encrypt
    kv_insert("settings", {"id": "email_ses", "enabled": True, "region": "eu-central-1",
                           "access_key": "AKIAKVTEST", "secret_key": encrypt("kv-test-secret"),
                           "from_email": "bulten@news.kv-test.invalid", "from_name": "Magaza",
                           "unsubscribe_mailto": "unsubscribe@news.kv-test.invalid"})
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.kv-test.invalid")
    import email_ses
    fake = _FakeSES()
    monkeypatch.setattr(email_ses, "_client", lambda cfg: fake)
    import routes.email_marketing as em
    import asyncio as _aio

    async def _fast_sleep(_s):
        return None
    monkeypatch.setattr(em, "asyncio", type("A", (), {"sleep": staticmethod(_fast_sleep),
                                                     "create_task": _aio.create_task,
                                                     "to_thread": _aio.to_thread})())
    return fake


def _campaign(cid="camp-1"):
    return kv_insert("email_campaigns", {"id": cid, "subject": "Yeni sezon {customer_name}",
                                         "html": "<p>Merhaba {customer_name}</p>", "provider": "ses",
                                         "status": "queued", "created_at": kv_iso()})


def test_campaign_raw_email_has_list_unsubscribe_headers(kv_client, kv_ses):
    s = _sub("alpha@example.com", name="Alpha")
    _campaign()
    from routes.email_marketing import _run_ses_campaign
    kv_run(_run_ses_campaign("camp-1"))
    assert len(kv_ses.raw) == 1
    call = kv_ses.raw[0]
    assert call["Destinations"] == ["alpha@example.com"]
    msg = email.message_from_bytes(call["RawMessage"]["Data"])
    lu = msg["List-Unsubscribe"]
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "<mailto:unsubscribe@news.kv-test.invalid>" in lu
    url = lu.split("<", 1)[1].split(">", 1)[0]
    assert url.startswith("https://api.kv-test.invalid/api/email-marketing/unsubscribe?")
    q = parse_qs(urlsplit(url).query)
    assert q["e"] == ["alpha@example.com"] and q["t"] == [s["id"]]
    assert msg.get_content_type() == "multipart/alternative"
    unfolded = " ".join(lu.split())   # RFC 5322 folding is fine; no extra header was injected
    assert unfolded.count("<") == 2 and unfolded.startswith("<https://")
    # the header URL really works as a one-click endpoint
    r = kv_client.post(urlsplit(url).path + "?" + urlsplit(url).query, content=b"List-Unsubscribe=One-Click",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert kv_find("newsletter_subscribers", {"id": s["id"]})["active"] is False
    camp = kv_find("email_campaigns", {"id": "camp-1"})
    assert camp["status"] == "sent" and camp["sent"] == 1


def test_mid_campaign_optout_is_skipped(kv_client, kv_ses):
    from fastapi.testclient import TestClient
    import server
    subs = [_sub(f"r{i:02d}@example.com") for i in range(55)]
    late = subs[52]
    _campaign()

    def _on_send(n, _kw):
        if n == 1:  # while the campaign is running, r52 clicks "unsubscribe"
            c = TestClient(server.app)
            rr = c.post(UNSUB, data={"e": late["email"], "t": late["id"]})
            assert rr.status_code == 200
    kv_ses.on_send = _on_send
    from routes.email_marketing import _run_ses_campaign
    kv_run(_run_ses_campaign("camp-1"))
    sent_to = [c["Destinations"][0] for c in kv_ses.raw]
    assert late["email"] not in sent_to
    assert len(sent_to) == 54 and len(set(sent_to)) == 54
    camp = kv_find("email_campaigns", {"id": "camp-1"})
    assert camp["sent"] == 54 and camp["skipped"] == 1
    sends = {x["email"] for x in kv_findall("email_campaign_sends", {"campaign_id": "camp-1", "status": "sent"})}
    assert late["email"] not in sends


def test_suppressed_and_ret_recipients_never_mailed(kv_ses, kv_users):
    _sub("ok@example.com")
    _sub("bounced@example.com")
    _sub("ret@example.com")
    kv_insert("email_suppressions", {"email": "bounced@example.com", "reason": "bounce"})
    kv_insert("iys_consents", {"id": "i1", "email": "ret@example.com", "channels": ["EPOSTA"], "status": "RET",
                               "consent_date": kv_iso(), "created_at": kv_iso()})
    kv_users.customer(email="profile@example.com", accepts_marketing=True)
    kv_users.customer(email="noconsent@example.com", accepts_marketing=False)
    _campaign()
    from routes.email_marketing import _run_ses_campaign
    kv_run(_run_ses_campaign("camp-1"))
    sent_to = sorted(c["Destinations"][0] for c in kv_ses.raw)
    assert sent_to == ["ok@example.com", "profile@example.com"]
