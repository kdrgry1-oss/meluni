"""PRIV11 — a rotated / revoked integration credential takes effect on the very next external call.

No integration keeps credentials in process memory: every call re-reads the (encrypted) settings.
Rotating the settings between two calls must make the second call use the NEW value; removing them
must stop the call. Covered: iyzico (payment-detail finalize, daily reconciliation lookup), Doğan
e-invoice, MNG cargo, Trendyol, Hepsiburada, Amazon SP-API, SES campaign sender.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json

import httpx
import pytest

from conftest import KvUsers, kv_run
from pay_harness import IYZICO_BASE, IYZICO_KEY, IYZICO_SECRET, pay_card_order
from routes import deps


def _auth_parts(request: httpx.Request) -> dict:
    raw = request.headers["Authorization"].split(" ", 1)[1]
    return dict(p.split(":", 1) for p in base64.b64decode(raw).decode().split("&"))


def _sig_ok(request: httpx.Request, secret: str, path: str) -> bool:
    parts = _auth_parts(request)
    exp = hmac.new(secret.encode(), f"{parts['randomKey']}{path}{request.content.decode()}".encode(),
                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, parts["signature"])


async def _rotate_iyzico(key, secret):
    from security.crypto import encrypt
    await deps.db.settings.update_one({"id": "iyzico"}, {"$set": {"api_key": key, "api_secret": encrypt(secret)}})


@pytest.mark.asyncio
async def test_iyzico_rotation_between_two_calls_uses_new_key(pay_http, pay_env):
    from routes.payment import _finalize_by_payment_id, PAYMENT_DETAIL_PATH
    from security.reconcile_iyzico import _lookup
    seen = []

    def _detail(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "paymentStatus": ""})   # uncertain → no write
    pay_env.iyzico.post(f"{IYZICO_BASE}{PAYMENT_DETAIL_PATH}").mock(side_effect=_detail)
    order = await pay_card_order(pay_http)

    await _finalize_by_payment_id(order["id"], "pid-x")
    await _rotate_iyzico("rotated-key", "rotated-secret-0123456789abcdef")
    await _finalize_by_payment_id(order["id"], "pid-x")
    assert _auth_parts(seen[0])["apiKey"] == IYZICO_KEY and _sig_ok(seen[0], IYZICO_SECRET, PAYMENT_DETAIL_PATH)
    assert _auth_parts(seen[1])["apiKey"] == "rotated-key"
    assert _sig_ok(seen[1], "rotated-secret-0123456789abcdef", PAYMENT_DETAIL_PATH)

    # the daily reconciliation lookup re-reads too; a REVOKED key (settings removed) → no call at all
    o = {**order, "iyzico_payment_id": "pid-x"}
    await _lookup(o)
    assert _auth_parts(seen[2])["apiKey"] == "rotated-key"
    await deps.db.settings.update_one({"id": "iyzico"}, {"$set": {"api_key": "", "api_secret": ""}})
    data, err = await _lookup(o)
    assert data is None and err == "credentials_unavailable" and len(seen) == 3
    # the order was never touched by these lookups
    cur = await deps.db.orders.find_one({"id": order["id"]}, {"_id": 0, "payment_status": 1, "status": 1})
    assert cur["payment_status"] != "paid" and cur["status"] == "awaiting_payment"


def test_dogan_rotation_between_two_calls(kv_client, kv_users, monkeypatch):
    import dogan_client
    made = []

    class _FakeDogan:
        def __init__(self, username, password, is_test=True):
            made.append((username, password, is_test))

        def _list_efatura_operations(self):
            return ["GetInvoiceWithType"]

        def _efatura_op_signature(self, op):
            return "sig"

        def get_efatura_pdf(self, uuid, iid):
            return {"success": False, "operation": "none", "kind": "-"}
    monkeypatch.setattr(dogan_client, "DoganClient", _FakeDogan)
    from routes.deps import db
    kv_run(db.settings.insert_one({"id": "dogan_edonusum", "enabled": True, "username": "dogan-u1",
                                   "password": "dogan-p1", "is_test": True}))
    kv_run(db.orders.insert_one({"id": "o-dg", "order_number": "FCDG1", "invoice_uuid": "u-1"}))
    h = KvUsers.hdr(kv_users.staff(super_admin=True))
    assert kv_client.get("/api/orders/o-dg/einvoice-pdf-debug", headers=h).status_code == 200
    kv_run(db.settings.update_one({"id": "dogan_edonusum"}, {"$set": {"username": "dogan-u2", "password": "dogan-p2"}}))
    assert kv_client.get("/api/orders/o-dg/einvoice-pdf-debug", headers=h).status_code == 200
    assert [m[:2] for m in made] == [("dogan-u1", "dogan-p1"), ("dogan-u2", "dogan-p2")]
    # revoked (disabled) → no client is built at all
    kv_run(db.settings.update_one({"id": "dogan_edonusum"}, {"$set": {"enabled": False}}))
    r = kv_client.get("/api/orders/o-dg/einvoice-pdf-debug", headers=h)
    assert r.json()["ok"] is False and len(made) == 2


def test_mng_settings_reread_and_client_takes_credentials_per_call():
    import mng_kargo_client
    from routes.orders import _get_mng_settings
    from routes.deps import db
    from security.crypto import encrypt
    kv_run(db.settings.insert_one({"id": "mng_kargo", "username": "mng-u1", "password": encrypt("mng-p1")}))
    a = kv_run(_get_mng_settings())
    kv_run(db.settings.update_one({"id": "mng_kargo"}, {"$set": {"username": "mng-u2", "password": encrypt("mng-p2")}}))
    b = kv_run(_get_mng_settings())
    assert (a["username"], a["password"]) == ("mng-u1", "mng-p1")
    assert (b["username"], b["password"]) == ("mng-u2", "mng-p2")
    # the cached zeep client holds NO credentials: every operation receives them as arguments
    for fn in (mng_kargo_client.create_shipment, mng_kargo_client.get_mng_shipment_status,
               mng_kargo_client.cancel_shipment, mng_kargo_client.track_by_reference):
        params = inspect.signature(fn).parameters
        assert "username" in params and "password" in params, fn.__name__


def test_marketplace_credentials_reread_each_call():
    from routes.deps import db
    from security.crypto import encrypt
    from routes.integrations_trendyol import get_trendyol_config
    from routes.category_mapping import _get_hb_client
    from routes import amazon_spapi
    kv_run(db.settings.insert_one({"id": "trendyol", "supplier_id": "1", "api_key": "ty-k1",
                                   "api_secret": encrypt("ty-s1"), "is_active": True}))
    assert kv_run(get_trendyol_config())["api_key"] == "ty-k1"
    kv_run(db.settings.update_one({"id": "trendyol"}, {"$set": {"api_key": "ty-k2", "api_secret": encrypt("ty-s2")}}))
    cfg = kv_run(get_trendyol_config())
    assert cfg["api_key"] == "ty-k2" and cfg["api_secret"] == "ty-s2"

    kv_run(db.settings.insert_one({"id": "hepsiburada", "merchant_id": "m1", "secret_key": "hb-s1",
                                   "dev_username": "dev1", "mode": "test"}))
    c1, _ = kv_run(_get_hb_client())
    kv_run(db.settings.update_one({"id": "hepsiburada"}, {"$set": {"secret_key": "hb-s2"}}))
    c2, _ = kv_run(_get_hb_client())
    assert (c1.secret_key, c2.secret_key) == ("hb-s1", "hb-s2")
    kv_run(db.settings.update_one({"id": "hepsiburada"}, {"$set": {"secret_key": ""}}))
    c3, err = kv_run(_get_hb_client())
    assert c3 is None and err

    used = []

    async def _exchange(cid, secret, refresh):
        used.append(refresh)
        return {"access_token": f"at-{refresh}", "expires_in": 3600}
    amazon_spapi._exchange_refresh_for_access, _real = _exchange, amazon_spapi._exchange_refresh_for_access
    try:
        kv_run(db.integration_settings.insert_one({"key": amazon_spapi.CONFIG_KEY, "client_id": "cid",
                                                   "client_secret_enc": encrypt("cs"),
                                                   "refresh_token_enc": encrypt("rt-1")}))
        assert kv_run(amazon_spapi.get_valid_access_token())[0] == "at-rt-1"
        # rotating the refresh token (save endpoint semantics) drops the cached access token
        kv_run(db.integration_settings.update_one({"key": amazon_spapi.CONFIG_KEY}, {"$set": {
            "refresh_token_enc": encrypt("rt-2"), "access_token": None, "access_expires_at": 0}}))
        assert kv_run(amazon_spapi.get_valid_access_token())[0] == "at-rt-2"
        assert used == ["rt-1", "rt-2"]
        kv_run(db.integration_settings.update_one({"key": amazon_spapi.CONFIG_KEY},
                                                  {"$set": {"refresh_token_enc": None}}))
        with pytest.raises(Exception):
            kv_run(amazon_spapi.get_valid_access_token())
    finally:
        amazon_spapi._exchange_refresh_for_access = _real


def test_ses_campaign_stops_when_credentials_revoked_mid_send(monkeypatch):
    import uuid
    from routes import email_marketing as em
    from routes.deps import db
    from security.crypto import encrypt
    monkeypatch.setattr(em, "_OPTOUT_RECHECK_BATCH", 3)
    kv_run(db.settings.insert_one({"id": "email_ses", "enabled": True, "region": "eu-central-1",
                                   "access_key": "AKIA-K1", "secret_key": encrypt("ses-s1"),
                                   "from_email": "news@shop.example"}))
    for i in range(8):
        kv_run(db.newsletter_subscribers.insert_one({"id": uuid.uuid4().hex, "email": f"r{i}@example.com",
                                                     "active": True, "consent": True}))
    kv_run(db.email_campaigns.insert_one({"id": "camp-1", "subject": "S", "html": "<p>x</p>", "status": "queued"}))
    keys = []

    async def _send(cfg, to, subject, html, reply_to="", **kw):
        keys.append(cfg["access_key"])
        if len(keys) == 2:    # admin rotates the key while the campaign is running
            await db.settings.update_one({"id": "email_ses"}, {"$set": {"access_key": "AKIA-K2"}})
        if len(keys) == 5:    # … and then disables SES altogether
            await db.settings.update_one({"id": "email_ses"}, {"$set": {"enabled": False}})
        return {"success": True}
    monkeypatch.setattr(em, "send_ses_email", _send)
    kv_run(em._run_ses_campaign("camp-1"))
    # batches of 3: the in-flight batch finishes with K1, the next batch re-reads and uses K2
    assert keys == ["AKIA-K1"] * 3 + ["AKIA-K2"] * 3
    camp = kv_run(db.email_campaigns.find_one({"id": "camp-1"}, {"_id": 0}))
    assert camp["status"] == "failed" and camp.get("aborted") is True
    assert len(keys) == 6     # stopped at the next batch boundary after SES was disabled
