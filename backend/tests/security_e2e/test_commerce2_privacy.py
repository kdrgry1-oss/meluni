"""E2E (commerce-2): privacy & output safety.

PRIV01 canary card: a canary PAN/CVC sent through 3DS init and (rule-enabled) non-3DS pay — in success,
       provider-failure and transport-error paths — reaches ONLY the provider request; it is absent
       from every Mongo collection, every log record (raw, before redaction), and every response.
CHAIN04 script payloads in order note / product name / customer name are escaped in every HTML the
       backend renders (invoice print, cargo label, admin + customer e-mails).
PRIV07 every full IBAN / TCKN reveal site calls security.events.note_pii_reveal; a 429 from it
       propagates and no plaintext leaves.
"""
import json
import logging
import random

import httpx
import pytest
from fastapi import HTTPException

from permissions import upgrade_grants_for
from pay_harness import PAY_TOKENS as _PT  # denetim #4: istemcinin ödeme yetkisi
from pay_harness import (
    CALLBACK_URL, IYZICO_BASE, SHIP, iyz_auth_ok, iyz_json, mock_3ds_auth, mock_3ds_init, now_iso,
    pay_callback, pay_card_order, pay_customer, pay_drain, pay_find, pay_findall, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio


def _luhn_pan(prefix="979797", length=16):
    body = prefix + "".join(random.choice("0123456789") for _ in range(length - len(prefix) - 1))
    digits = [int(d) for d in body]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


async def _all_db_text():
    from routes import deps
    out = {}
    for name in await deps.db.list_collection_names():
        docs = await deps.db[name].find({}, {"_id": 0}).to_list(100000)
        out[name] = json.dumps(docs, default=str, ensure_ascii=False)
    return out


async def test_canary_card_never_persists_or_logs(pay_http, pay_env, caplog):
    caplog.set_level(logging.DEBUG)
    pan = _luhn_pan()
    cvc = "9" + str(random.randint(10, 99))
    holder = "CANARY HOLDER QZX"
    card = {"cardHolderName": holder, "cardNumber": " ".join(pan[i:i + 4] for i in range(0, 16, 4)),
            "expireMonth": "07", "expireYear": "2031", "cvc": cvc}
    import business_rules
    from routes import deps
    await business_rules.save_rules(deps.db, {"payment.allow_non3ds_card_pay": True,
                                              "order.max_unpaid_open_orders": 0})
    business_rules.invalidate()
    responses = []

    async def _init(order, mock):
        pay_env.iyzico.routes.clear()
        mock()
        r = await pay_http.post("/api/payment/3ds/initialize",
                                json={"order_id": order["id"], "callback_url": CALLBACK_URL,
                                      "return_url": "https://www.meluni.com.tr/odeme", "card": card,
                                      "pay_token": _PT.get(order["id"], "")})
        responses.append(r)
        return r

    async def _non3ds(order, mock):
        pay_env.iyzico.routes.clear()
        mock()
        r = await pay_http.post("/api/payment/card/pay", json={"order_id": order["id"], "card": card,
                                                                "pay_token": _PT.get(order["id"], "")})
        responses.append(r)
        return r

    sent = []
    # 3DS init — success, provider failure, transport error
    o1 = await pay_card_order(pay_http, pid="c1")
    route = None

    def _m_ok():
        nonlocal route
        route = mock_3ds_init(pay_env.iyzico)
    r = await _init(o1, _m_ok)
    assert r.status_code == 200 and r.json()["success"]
    sent += [c.request.content.decode() for c in route.calls]
    mock_3ds_auth(pay_env.iyzico, iyz_auth_ok(o1, payment_id="pid-can1"))
    responses.append(await pay_callback(pay_http, o1["id"], "pid-can1"))
    o2 = await pay_card_order(pay_http, pid="c2")

    def _m_fail():
        nonlocal route
        route = mock_3ds_init(pay_env.iyzico, ok=False)
    r = await _init(o2, _m_fail)
    assert r.status_code == 200 and r.json()["success"] is False
    sent += [c.request.content.decode() for c in route.calls]
    o3 = await pay_card_order(pay_http, pid="c3")

    def _m_exc():
        nonlocal route
        route = pay_env.iyzico.post(f"{IYZICO_BASE}/payment/3dsecure/initialize").mock(
            side_effect=httpx.ConnectError("iyzico unreachable"))
    r = await _init(o3, _m_exc)
    assert r.status_code == 502
    # non-3DS (enabled by business rule for the test) — success, decline, timeout
    for i, mk in enumerate((
            lambda o: iyz_json(iyz_auth_ok(o, payment_id="pid-n3")),
            lambda o: iyz_json({"status": "failure", "errorCode": "10051", "errorMessage": "Yetersiz bakiye"}),
            None)):
        o = await pay_card_order(pay_http, pid=f"n{i}")

        def _m(o=o, mk=mk):
            nonlocal route
            route = pay_env.iyzico.post(f"{IYZICO_BASE}/payment/auth").mock(
                **({"return_value": mk(o)} if mk else {"side_effect": httpx.ReadTimeout("slow bank")}))
        r = await _non3ds(o, _m)
        assert r.status_code in (200, 502), r.text
        sent += [c.request.content.decode() for c in route.calls]
    await pay_drain()

    # the canary DID reach the provider (proves the test exercised the real data flow)
    assert any(pan in s and f'"cvc":"{cvc}"' in s for s in sent)
    # ...and nowhere else: DB (every collection), logs (raw records), responses
    db_text = await _all_db_text()
    for name, text in db_text.items():
        assert pan not in text, f"PAN persisted in {name}"
        assert pan[:4] + " " not in text or pan not in text.replace(" ", ""), name
        assert f'"cvc": "{cvc}"' not in text and f'"cvc":"{cvc}"' not in text, f"CVC persisted in {name}"
        assert '"cardNumber"' not in text and '"paymentCard"' not in text, f"card object persisted in {name}"
        assert '"cvc"' not in text and '"expireYear"' not in text, f"card fields persisted in {name}"
    for rec in caplog.records:
        msg = rec.getMessage() + " " + repr(getattr(rec, "args", ""))
        assert pan not in msg.replace(" ", ""), f"PAN logged by {rec.name}: {msg[:200]}"
        assert f"'cvc': '{cvc}'" not in msg and f'"cvc":"{cvc}"' not in msg, rec.name
    for r in responses:
        assert pan not in r.text.replace(" ", "") and f'"{cvc}"' not in r.text, r.text[:200]
        assert pan not in str(r.headers.get("location", ""))
    # card-testing velocity rows keep only the BIN
    for row in await pay_findall("card_attempts"):
        assert len(row.get("bin") or "") <= 6


# ═════════════════════════════════════ CHAIN04 ═══════════════════════════════════════════════
XSS = ["<script>alert(1)</script>", "<img src=x onerror=alert(2)>", "\"><svg/onload=alert(3)>",
       "{{7*7}}<iframe src=javascript:alert(4)>"]


_RAW_FRAGMENTS = ("<script>alert(1)", "<img src=x", "<svg/onload", "<iframe src=javascript", "<b>Kadikoy",
                  "\" onerror=\"alert(6)")


def _raw_payload_present(html: str) -> list:
    """Executable fragments of the injected payloads (the page's own <script> blocks are fine)."""
    return [f for f in _RAW_FRAGMENTS if f in html]


async def _xss_order(http):
    from pay_harness import pay_seed_product
    await pay_seed_product("px", stock=5, name=XSS[1], description=XSS[0] + " açıklama")
    ship = {**SHIP, "first_name": XSS[2], "last_name": XSS[0], "address": XSS[3] + " Sk. 1",
            "district": "<b>Kadikoy</b>"}
    from pay_harness import order_payload
    r = await http.post("/api/orders", json=order_payload(
        "bank_transfer", ship=ship, items=[{"product_id": "px", "quantity": 1, "color": XSS[0],
                                             "image": "javascript:alert(5)\" onerror=\"alert(6)"}],
        notes=XSS[0], gift_note=XSS[1]))
    assert r.status_code == 200, r.text
    await pay_drain()
    return await pay_find("orders", {"id": r.json()["order_id"]})


async def test_admin_html_outputs_escape_customer_and_catalog_content(pay_http, pay_env):
    o = await _xss_order(pay_http)
    _, sup = await pay_staff(super_admin=True)
    for url in (f"/api/orders/{o['id']}/invoice/print", f"/api/orders/{o['id']}/cargo-label"):
        r = await pay_http.get(url, headers=sup)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), url
        assert not _raw_payload_present(r.text), (url, _raw_payload_present(r.text))
        assert "&lt;" in r.text                                    # the content is there, but inert
        assert r.headers.get("cache-control") == "no-store"
    # admin JSON views are data (JSON), not markup
    r = await pay_http.get(f"/api/orders/{o['id']}", headers=sup)
    assert r.headers["content-type"].startswith("application/json")


async def test_emails_escape_customer_and_catalog_content(pay_http, pay_env):
    o = await _xss_order(pay_http)
    import notification_service as ns
    from routes.orders import _order_notify_vars
    html = ns.build_admin_order_email_html(o, o["shipping_address"], "Yeni Sipariş", XSS[0])
    assert not _raw_payload_present(html), _raw_payload_present(html)
    variables = await _order_notify_vars(o)
    tpl = ("<p>Merhaba {customer_name}</p><div>{items_html}</div><p>{shipping_address} {shipping_district}</p>"
           "<p>{first_name}</p>")
    out = ns.render_template(tpl, variables, escape_values=True)
    assert not _raw_payload_present(out), _raw_payload_present(out)
    assert 'onerror="alert(6)' not in out and "javascript:alert(5)\"" not in out
    # the SMS/plain channel keeps the text (no HTML context)
    assert XSS[2] in ns.render_template("{first_name}", variables, escape_values=False)


# ═════════════════════════════════════ PRIV07 hooks ══════════════════════════════════════════
class _Calls(list):
    fn = None


@pytest.fixture
def reveal_calls(monkeypatch):
    import security.events as ev
    calls = _Calls()

    def _note(actor, kind, n=1, refs=None, **_kw):
        calls.append({"actor": (actor or {}).get("id"), "kind": kind, "n": n})
        if getattr(_note, "limit", None) is not None and sum(c["n"] for c in calls) > _note.limit:
            raise HTTPException(status_code=429, detail="Çok fazla hassas veri görüntüleme")
    _note.limit = None
    monkeypatch.setattr(ev, "note_pii_reveal", _note, raising=False)
    calls.fn = _note
    return calls


async def _tax_order(http):
    o = await pay_card_order(http, pid="pt")
    from security.pii import seal_doc_tax_ids
    sealed = seal_doc_tax_ids({"billing_info": {"is_corporate": True, "tax_number": "10000000146",
                                                "company_name": "Test AS"}})
    await pay_set("orders", {"id": o["id"]}, {"billing_info": sealed["billing_info"]})
    return o


async def test_admin_order_detail_tax_id_reveal_is_counted_and_429_propagates(pay_http, pay_env, reveal_calls):
    o = await _tax_order(pay_http)
    staff, hdr = await pay_staff(["orders.view"])
    r = await pay_http.get(f"/api/orders/{o['id']}", headers=hdr)
    assert r.status_code == 200 and "10000000146" in r.text
    assert reveal_calls == [{"actor": staff["id"], "kind": "tax_id", "n": 1}]
    reveal_calls.fn.limit = 1
    r = await pay_http.get(f"/api/orders/{o['id']}", headers=hdr)
    assert r.status_code == 429 and "10000000146" not in r.text
    # the owner (customer view) is masked → nothing revealed, nothing counted
    reveal_calls.clear()
    reveal_calls.fn.limit = None


async def test_full_iban_reveal_is_counted_and_429_propagates(pay_http, pay_env, reveal_calls):
    from kv_harness import tr_iban
    from routes.orders import seal_refund_bank_info
    from routes import deps
    iban = tr_iban("0001000123456789012345")
    o = await pay_card_order(pay_http, pid="pi")
    for i in range(2):
        await deps.db.customer_returns.insert_one({
            "id": f"ret-i{i}", "order_id": o["id"], "order_number": o["order_number"], "status": "approved",
            "items": [], "created_at": now_iso(),
            "refund_bank_info": seal_refund_bank_info({"iban": iban, "name": "Zeynep Kaya", "bank": "B"})})
    # IAM-02: rol, açılıştaki tek seferlik izin geçişinden geçmiş hâliyle (permissions.upgrade_grants_for).
    payer, phdr = await pay_staff(["returns.refund_pay"] + upgrade_grants_for(["returns.refund_pay"]))
    r = await pay_http.get("/api/orders/returns/admin/list", headers=phdr)
    assert r.status_code == 200 and iban in r.text
    assert reveal_calls == [{"actor": payer["id"], "kind": "iban", "n": 2}]
    # masked viewers never trigger the counter
    _, ahdr = await pay_staff(["returns.approve", "returns.view"])
    r = await pay_http.get("/api/orders/returns/admin/list", headers=ahdr)
    assert r.status_code == 200 and iban not in r.text and len(reveal_calls) == 1
    reveal_calls.fn.limit = 2
    r = await pay_http.get("/api/orders/returns/admin/list", headers=phdr)
    assert r.status_code == 429 and iban not in r.text


async def test_reveal_sites_work_when_counter_is_not_installed(pay_http, pay_env, monkeypatch):
    import security.events as ev
    monkeypatch.delattr(ev, "note_pii_reveal", raising=False)
    o = await _tax_order(pay_http)
    _, hdr = await pay_staff(["orders.view"])
    r = await pay_http.get(f"/api/orders/{o['id']}", headers=hdr)
    assert r.status_code == 200 and "10000000146" in r.text
