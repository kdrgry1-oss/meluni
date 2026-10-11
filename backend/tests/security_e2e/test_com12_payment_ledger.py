"""COM12 — iyzico commission / settlement ledger (security/payment_ledger.py), iyzico mocked with respx.

Daily job pulls iyzico's reporting API (GET /v2/reporting/payment/transactions, signed IYZWSv2 over the
path WITHOUT the query string) for the previous 2 TR days into ``payment_ledger`` (idempotent by iyzico
transaction id), rebuilds ``payment_ledger_months`` and records discrepancies (alerted exactly once).
Orders / payment status / stock are NEVER modified (snapshot). No live iyzico call, no PII.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pay_harness import (IYZICO_BASE, IYZICO_KEY, IYZICO_SECRET, pay_find, pay_findall, pay_set_switch,
                         pay_staff)
from routes import deps

pytestmark = pytest.mark.asyncio

TX_RX = re.compile(re.escape(IYZICO_BASE) + r"/v2/reporting/payment/transactions\?.*")
DETAIL_RX = re.compile(re.escape(IYZICO_BASE) + r"/v2/reporting/payment/details\?.*")
PAYOUT_URL = f"{IYZICO_BASE}/reporting/settlement/payoutcompleted"


async def _set_rule(key, value):
    # İşletme Kuralı anahtarı noktalı ("payment.x") → save_rules ile düz anahtar olarak yazılır.
    import business_rules
    await business_rules.save_rules(deps.db, {key: value})
    business_rules.invalidate()


def _days():
    from security.payment_ledger import _tr_today
    t = _tr_today()
    return (t - timedelta(days=2)).isoformat(), (t - timedelta(days=1)).isoformat()


def _tr_noon_utc(day: str, hour=12, minute=0) -> str:
    from security.payment_ledger import TR_TZ
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=TR_TZ).astimezone(timezone.utc).isoformat()


def _tx(pid, paid, *, typ="PAYMENT", txid=None, conv="", basket="", fee=0.25, rate=None, payout=None,
        installment=1, payout_date=None, tx_item=None, **extra):
    rate_amt = round(paid * 0.0299, 2) if rate is None else rate
    d = {"transactionType": typ, "transactionId": txid or f"tx-{typ.lower()}-{pid}", "paymentId": pid,
         "paymentTxId": tx_item or f"ptx-{pid}", "conversationId": conv, "basketId": basket,
         "paidPrice": f"{paid:.2f}", "price": f"{paid:.2f}", "currency": "TRY", "installment": installment,
         "transactionDate": "2026-01-01 12:00:00"}
    if typ == "PAYMENT":
        d.update(iyziCommissionFee=f"{fee:.2f}", iyziCommissionRateAmount=f"{rate_amt:.2f}",
                 merchantPayoutAmount=f"{(payout if payout is not None else paid - fee - rate_amt):.2f}")
        if payout_date:
            d["blockageResolvedDate"] = f"{payout_date} 00:00:00"
    d.update(extra)
    return d


class IyzMock:
    """Reporting API mock: transactions per day (paged), payout-completed per day, payment details."""

    def __init__(self, env, per_day=None, page_size=50, payouts=None, detail_found=(), fail_days=()):
        self.per_day = per_day or {}
        self.page_size = page_size
        self.payouts = payouts or {}
        self.detail_found = set(detail_found)
        self.fail_days = set(fail_days)
        self.requests = []
        env.iyzico.get(url__regex=TX_RX.pattern).mock(side_effect=self._tx)
        env.iyzico.get(url__regex=DETAIL_RX.pattern).mock(side_effect=self._detail)
        env.iyzico.post(PAYOUT_URL).mock(side_effect=self._payout)

    def _tx(self, request: httpx.Request):
        self.requests.append(request)
        day = request.url.params["transactionDate"]
        page = int(request.url.params["page"])
        if day in self.fail_days:
            return httpx.Response(502, text="<html>bad gateway</html>")
        rows = self.per_day.get(day, [])
        pages = max(1, (len(rows) + self.page_size - 1) // self.page_size)
        chunk = rows[(page - 1) * self.page_size: page * self.page_size]
        return httpx.Response(200, json={"status": "success", "locale": "tr", "currentPage": page,
                                         "totalPageCount": pages, "transactions": chunk})

    def _detail(self, request: httpx.Request):
        self.requests.append(request)
        conv = request.url.params.get("paymentConversationId")
        pays = [{"paymentId": f"pid-{conv}", "paymentStatus": 1}] if conv in self.detail_found else []
        return httpx.Response(200, json={"status": "success", "payments": pays})

    def _payout(self, request: httpx.Request):
        self.requests.append(request)
        body = json.loads(request.content)
        day = body["date"][:10]
        return httpx.Response(200, json={"status": "success",
                                         "payoutCompletedTransactions": self.payouts.get(day, [])})


async def _order(oid, total=550.0, *, pid=None, paid_day=None, status="confirmed", payment_status="paid",
                 refunds=None, snapshot=True, method="credit_card", **extra):
    pid = pid if pid is not None else f"pid-{oid}"
    doc = {"id": oid, "order_number": f"FC{oid.upper()}", "total": total, "payment_method": method,
           "payment_status": payment_status, "status": status, "iyzico_payment_id": pid,
           "paid_at": _tr_noon_utc(paid_day) if paid_day else None, "created_at": _tr_noon_utc(paid_day or _days()[1]),
           "refunds": refunds or []}
    if snapshot:
        doc["iyzico_retrieve_response"] = {"paymentId": pid, "paidPrice": f"{total:.2f}"}
    doc.update(extra)
    await deps.db.orders.insert_one(dict(doc))
    return doc


async def _snapshot():
    out = {}
    for coll in ("orders", "products", "coupons", "coupon_redemptions", "gift_cards"):
        out[coll] = sorted((await pay_findall(coll)), key=lambda x: str(x.get("id")))
    return copy.deepcopy(out)


def _decode_auth(h: str) -> dict:
    assert h.startswith("IYZWSv2 ")
    raw = base64.b64decode(h.split(" ", 1)[1]).decode()
    return dict(p.split(":", 1) for p in raw.split("&"))


# ═══════════════════════════════════ signing ═══════════════════════════════════
async def test_signing_header_format_get_and_post(pay_env):
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    m = IyzMock(pay_env, per_day={d1: [_tx("pid-x", 10.0, conv="nope")]})
    await run_payment_ledger()
    gets = [r for r in m.requests if r.method == "GET"]
    posts = [r for r in m.requests if r.method == "POST"]
    assert {r.url.params["transactionDate"] for r in gets} == {d2, d1}
    for r in gets + posts:
        parts = _decode_auth(r.headers["authorization"])
        assert parts["apiKey"] == IYZICO_KEY and parts["randomKey"] == r.headers["x-iyzi-rnd"]
        body = r.content.decode() if r.method == "POST" else ""
        if r.method == "GET":
            assert r.content == b""           # GET: no body, signature over path only (query excluded)
        signed = parts["randomKey"] + r.url.path + body
        assert parts["signature"] == hmac.new(IYZICO_SECRET.encode(), signed.encode(), hashlib.sha256).hexdigest()
        assert "?" not in signed and "transactionDate" not in signed
        assert IYZICO_SECRET not in r.headers["authorization"]
    assert posts and json.loads(posts[0].content)["date"].endswith(" 00:00:00")


async def test_signed_headers_helper_strips_query(pay_env):
    from security.payment_ledger import signed_headers
    st = {"api_key": "k", "api_secret": "s"}
    h = signed_headers(st, "/v2/reporting/payment/transactions?transactionDate=2026-01-01&page=3")
    p = _decode_auth(h["Authorization"])
    exp = hmac.new(b"s", (p["randomKey"] + "/v2/reporting/payment/transactions").encode(), hashlib.sha256).hexdigest()
    assert p["signature"] == exp and h["x-iyzi-rnd"] == p["randomKey"] and h["Accept"] == "application/json"


# ═══════════════════════════════════ ingestion ═════════════════════════════════
async def test_ingestion_idempotent_and_paginated(pay_env):
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    o = await _order("ok1", 550.0, paid_day=d1)
    rows = [_tx(o["iyzico_payment_id"], 550.0, rate=16.45, conv=o["id"], basket=o["order_number"])]
    rows += [_tx(f"pid-n{i}", 100.0 + i, conv=f"none-{i}", txid=f"tx-n{i}") for i in range(4)]
    m = IyzMock(pay_env, per_day={d1: rows}, page_size=2)
    res1 = await run_payment_ledger()
    assert res1["ok"] is True and res1["rows"] == 5, res1
    assert len([r for r in m.requests if r.method == "GET" and r.url.params["transactionDate"] == d1]) == 3
    first = {x["id"]: x for x in await pay_findall("payment_ledger")}
    assert len(first) == 5
    res2 = await run_payment_ledger()
    assert res2["rows"] == 5
    second = {x["id"]: x for x in await pay_findall("payment_ledger")}
    assert set(second) == set(first)
    assert all(second[k]["first_ingested_at"] == first[k]["first_ingested_at"] for k in first)
    led = second[f"iyzico:payment:tx-payment-{o['iyzico_payment_id']}"]
    assert led["order_id"] == "ok1" and led["order_number"] == "FCOK1"
    assert led["amount"] == 550.0 and led["commission"] == round(0.25 + 16.45, 2)
    assert led["net_payout"] == round(550 - 0.25 - 16.45, 2) and led["commission_rate_pct"] == 2.991
    # no raw provider payload / personal data stored
    assert not any(k in led for k in ("buyer", "buyerName", "email", "cardHolderName", "raw"))
    # the unmatched payments are alerted ONCE (second run: no new alert)
    assert res1["new_discrepancies"] == 4 and res2["new_discrepancies"] == 0
    assert len(await pay_findall("alerts", {"kind": "payment_ledger_discrepancy"})) == 4
    d = await pay_find("payment_ledger_discrepancies", {"ref": "pid-n0"})
    assert d["occurrences"] == 2 and d["status"] == "open"


async def test_skips_when_iyzico_not_configured(pay_env):
    from security.payment_ledger import run_payment_ledger
    await deps.db.settings.delete_one({"id": "iyzico"})
    res = await run_payment_ledger()
    assert res["skipped"] == "iyzico_not_configured" and res["rows"] == 0
    st = await pay_find("settings", {"id": "payment_ledger_state"})
    assert "last_success_at" not in st


# ═══════════════════════════════════ discrepancies ═════════════════════════════
async def test_every_discrepancy_kind_and_orders_never_touched(pay_env):
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    await _set_rule("payment.ledger_payout_grace_days", 1)
    good = await _order("good", 550.0, paid_day=d1)
    notpaid = await _order("notpaid", 300.0, pid="", status="awaiting_payment", payment_status="pending",
                           snapshot=False)
    voided = await _order("voided", 200.0, pid="", status="cancelled", payment_status="failed", snapshot=False)
    missing = await _order("missing", 400.0, paid_day=d1)
    missing2 = await _order("missingb", 410.0, paid_day=d1)
    amount = await _order("amount", 550.0, paid_day=d1)
    inst = await _order("inst", 550.0, paid_day=d1, snapshot=False)
    ref_unrec = await _order("refunrec", 500.0, paid_day=d2)
    ref_miss = await _order("refmiss", 500.0, paid_day=d2, payment_status="partially_refunded",
                            refunds=[{"status": "done", "provider": "iyzico", "net_refund": 50.0,
                                      "refunded_at": _tr_noon_utc(d1)}])
    ref_ok = await _order("refok", 500.0, paid_day=d2, payment_status="partially_refunded",
                          refunds=[{"status": "done", "provider": "iyzico", "net_refund": 70.0,
                                    "refunded_at": _tr_noon_utc(d1)}])
    late_ref = await _order("lateref", 500.0, paid_day=d2, payment_status="partially_refunded",
                            refunds=[{"status": "done", "provider": "iyzico", "net_refund": 30.0,
                                      "refunded_at": _tr_noon_utc(d1, hour=23, minute=55)}])   # 5 dk sınır payı
    comm = await _order("comm", 1000.0, paid_day=d1)
    comm_inst = await _order("comminst", 1000.0, paid_day=d1, snapshot=False)
    await _order("gift", 300.0, paid_day=d1, paid_with_gift_card_only=True, pid="")
    await _order("havale", 300.0, paid_day=d1, method="bank_transfer", pid="")
    late = await _order("late", 100.0, paid_day=d2)
    paid_ok = await _order("paidok", 100.0, paid_day=d2)
    await deps.db.products.insert_one({"id": "p1", "stock": 7, "price": 500.0})
    await deps.db.coupons.insert_one({"id": "c1", "code": "X", "used_count": 1})

    def p(o, paid, **kw):
        return _tx(o["iyzico_payment_id"] or f"pid-{o['id']}", paid, conv=o["id"], basket=o["order_number"], **kw)
    per_day = {
        d2: [p(ref_unrec, 500.0), p(ref_miss, 500.0), p(ref_ok, 500.0), p(late_ref, 500.0),
             p(late, 100.0, payout_date=d2, tx_item="ptx-late"),
             p(paid_ok, 100.0, payout_date=d2, tx_item="ptx-paidok")],
        d1: [p(good, 550.0),
             _tx("pid-orphan", 999.0, conv="unknown-order", basket="FCNOPE"),
             _tx("pid-notpaid", 300.0, conv=notpaid["id"], basket=notpaid["order_number"]),
             _tx("pid-voided", 200.0, conv=voided["id"], basket=voided["order_number"]),
             _tx("pid-voided", 200.0, typ="CANCEL", txid="tx-cancel-voided"),
             p(amount, 500.0),
             p(inst, 580.0, installment=3),
             _tx(ref_unrec["iyzico_payment_id"], 100.0, typ="REFUND", txid="tx-r-unrec"),
             _tx(ref_ok["iyzico_payment_id"], 70.0, typ="REFUND", txid="tx-r-ok"),
             p(comm, 1000.0, rate=80.0),
             p(comm_inst, 1000.0, rate=150.0, installment=6)],
    }
    m = IyzMock(pay_env, per_day=per_day, payouts={d1: [{"paymentTransactionId": "ptx-paidok", "payoutAmount": "96.76"}]},
                detail_found={missing2["id"]})
    before = await _snapshot()

    res = await run_payment_ledger()
    assert res["ok"] is True, res
    discs = {(d["kind"], d["ref"]): d for d in await pay_findall("payment_ledger_discrepancies")}
    got = set(discs)
    expected = {
        ("ledger_payment_without_paid_order", "pid-orphan"),
        ("ledger_payment_without_paid_order", "pid-notpaid"),
        ("paid_order_without_ledger", "missing"),
        ("paid_order_without_ledger", "missingb"),
        ("amount_mismatch", amount["iyzico_payment_id"]),
        ("refund_not_recorded_on_order", ref_unrec["iyzico_payment_id"]),
        ("refund_missing_in_ledger", ref_miss["iyzico_payment_id"]),
        ("commission_rate_high", f"iyzico:payment:tx-payment-{comm['iyzico_payment_id']}"),
        ("payout_overdue", f"iyzico:payment:tx-payment-{late['iyzico_payment_id']}"),
    }
    assert got == expected, sorted(got ^ expected)
    assert discs[("ledger_payment_without_paid_order", "pid-orphan")]["details"]["reason"] == "order_not_found"
    assert discs[("ledger_payment_without_paid_order", "pid-notpaid")]["details"]["reason"] == "order_not_paid"
    assert discs[("paid_order_without_ledger", "missing")]["details"]["provider_detail"] == "not_found"
    assert discs[("paid_order_without_ledger", "missingb")]["details"]["provider_detail"] == "found"
    am = discs[("amount_mismatch", amount["iyzico_payment_id"])]["details"]
    assert am["ledger_paid"] == 500.0 and am["recorded_paid"] == 550.0
    ru = discs[("refund_not_recorded_on_order", ref_unrec["iyzico_payment_id"])]
    assert ru["details"]["ledger_refunds"] == 100.0 and ru["order_number"] == "FCREFUNREC"
    cr = discs[("commission_rate_high", f"iyzico:payment:tx-payment-{comm['iyzico_payment_id']}")]["details"]
    assert cr["rate_pct"] == 8.0 and cr["max_rate_pct"] == 5.0
    assert discs[("payout_overdue", f"iyzico:payment:tx-payment-{late['iyzico_payment_id']}")]["details"][
        "expected_payout_date"] == d2
    settled = await pay_find("payment_ledger", {"payment_tx_id": "ptx-paidok"})
    assert settled["settled"] is True and settled["settled_source"] == "payoutcompleted"
    # every discrepancy alerted exactly once
    alerts = await pay_findall("alerts", {"kind": "payment_ledger_discrepancy"})
    assert len(alerts) == len(expected)
    assert {a["level"] for a in alerts} == {"critical", "warning"}
    # invariant: orders / stock / coupons byte-identical; nothing marked paid/needs_reconciliation
    assert await _snapshot() == before
    assert not await pay_findall("orders", {"needs_reconciliation": True})

    # rerun → idempotent, still no new alert, orders untouched
    res2 = await run_payment_ledger()
    assert res2["new_discrepancies"] == 0 and res2["alerts"] == 0
    assert len(await pay_findall("alerts", {"kind": "payment_ledger_discrepancy"})) == len(expected)
    assert await _snapshot() == before


async def test_resolved_when_condition_clears(pay_env):
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    IyzMock(pay_env, per_day={d1: [_tx("pid-later", 120.0, conv="later", basket="FCLATER")]})
    await run_payment_ledger()
    assert (await pay_find("payment_ledger_discrepancies", {"ref": "pid-later"}))["status"] == "open"
    # the order shows up later as paid (e.g. recovered by the existing reconcile cron) → resolved
    await _order("later", 120.0, paid_day=d1)
    res = await run_payment_ledger()
    d = await pay_find("payment_ledger_discrepancies", {"ref": "pid-later"})
    assert d["status"] == "resolved" and res["resolved"] >= 1 and res["open_discrepancies"] == 0


async def test_failed_day_is_not_success_and_skips_missing_check(pay_env):
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    await _order("x1", 100.0, paid_day=d1)
    IyzMock(pay_env, per_day={}, fail_days={d1})
    res = await run_payment_ledger()
    assert res["ok"] is False and res["failed_dates"][0]["date"] == d1
    assert not await pay_findall("payment_ledger_discrepancies", {"kind": "paid_order_without_ledger"})
    st = await pay_find("settings", {"id": "payment_ledger_state"})
    assert "last_success_at" not in st and d2 in st["ingested_dates"] and d1 not in st["ingested_dates"]
    assert await pay_findall("alerts", {"kind": "payment_ledger_ingest_failed"})


# ═══════════════════════════════════ monthly rollup ════════════════════════════
async def test_monthly_rollup_math(pay_env):
    from security.payment_ledger import normalize_transaction, rollup_rows, run_payment_ledger
    rows = [normalize_transaction(_tx("a", 1000.0, rate=29.9, fee=0.25), "2026-09-10"),
            normalize_transaction(_tx("b", 500.0, rate=14.95, fee=0.25, payout=480.0), "2026-09-11"),
            normalize_transaction(_tx("a", 200.0, typ="REFUND", txid="r1"), "2026-09-12"),
            normalize_transaction(_tx("c", 50.0, typ="CANCEL", txid="c1"), "2026-09-12")]
    t = rollup_rows(rows)["TRY"]
    assert t["count"] == 2 and t["refund_count"] == 1 and t["cancel_count"] == 1
    assert t["gross"] == 1500.0
    assert t["commission"] == round(29.9 + 0.25 + 14.95 + 0.25, 2)
    assert t["payout_expected"] == round((1000 - 30.15) + 480.0, 2)
    assert t["refunds"] == 250.0
    assert t["net"] == round(t["payout_expected"] - 250.0, 2)
    assert t["unexplained"] == round(500 - 15.2 - 480.0, 2)      # 4.80 not explained by fee/rate
    assert t["commission_rate_pct"] == round(t["commission"] / 1500 * 100, 3)

    d2, d1 = _days()
    IyzMock(pay_env, per_day={d2: [_tx("m1", 100.0, rate=3.0, fee=0.25, conv="n1")],
                              d1: [_tx("m2", 300.0, rate=9.0, fee=0.25, conv="n2"),
                                   _tx("m1", 40.0, typ="REFUND", txid="rm1")]})
    await run_payment_ledger()
    months = {m["id"]: m for m in await pay_findall("payment_ledger_months")}
    assert set(months) == {d2[:7], d1[:7]}
    if d2[:7] == d1[:7]:
        mo = months[d1[:7]]
        assert mo["gross"] == 400.0 and mo["commission"] == 12.5 and mo["refunds"] == 40.0
        assert mo["count"] == 2 and mo["refund_count"] == 1 and mo["net"] == round(400 - 12.5 - 40, 2)
        assert mo["discrepancies"] >= 2 and "ledger_payment_without_paid_order" in mo["discrepancies_by_kind"]
    else:
        assert months[d2[:7]]["gross"] == 100.0 and months[d1[:7]]["gross"] == 300.0


async def test_rollup_commission_installment_partial_refund_chargeback_and_currency_balance():
    """COM12 kabul: komisyonlu, taksitli, kısmi iadeli, chargeback'li ve farklı para birimli örneklerde
    defter dengesi açıklanabilir: net = brüt − komisyon − açıklanamayan − iade − itiraz (para birimi başına)."""
    from security.payment_ledger import normalize_transaction, rollup_rows
    rows = [
        normalize_transaction(_tx("s1", 1000.0, rate=29.9, fee=0.25), "2026-09-01"),                 # tek çekim
        normalize_transaction(_tx("i6", 1120.0, rate=150.0, fee=0.25, installment=6), "2026-09-02"),  # taksitli
        normalize_transaction(_tx("s1", 300.0, typ="REFUND", txid="pr1"), "2026-09-03"),             # kısmi iade
        normalize_transaction(_tx("i6", 1120.0, typ="CHARGEBACK", txid="cb1"), "2026-09-04"),        # itiraz
        normalize_transaction(_tx("u1", 100.0, rate=3.5, fee=0.3, currency="USD"), "2026-09-05"),   # USD
    ]
    assert rows[3]["type"] == "chargeback" and rows[3]["net_payout"] == -1120.0
    by = rollup_rows(rows)
    t, u = by["TRY"], by["USD"]
    assert t["count"] == 2 and t["refund_count"] == 1 and t["chargeback_count"] == 1
    assert t["gross"] == 2120.0 and t["commission"] == round(29.9 + 0.25 + 150.0 + 0.25, 2)
    assert t["refunds"] == 300.0 and t["chargebacks"] == 1120.0
    assert t["net"] == round(t["gross"] - t["commission"] - t["unexplained"] - t["refunds"] - t["chargebacks"], 2)
    assert u["count"] == 1 and u["gross"] == 100.0 and u["commission"] == 3.8 and u["net"] == 96.2
    assert "USD" not in json.dumps(t)                                  # para birimleri karışmaz


async def test_export_denied_again_after_permission_removed(pay_env, pay_http):
    d2, d1 = _days()
    IyzMock(pay_env, per_day={d1: [_tx("pid-z", 10.0, conv="z")]})
    sup_doc, sup = await pay_staff(super_admin=True)
    await pay_staff(super_admin=True)                                   # en az bir süper admin kalsın
    assert (await pay_http.post("/api/admin/payment-ledger/rerun", json={"date_from": d2, "date_to": d1},
                                headers=sup)).status_code == 200
    url = f"/api/admin/payment-ledger/export.csv?month={d1[:7]}"
    assert (await pay_http.get(url, headers=sup)).status_code == 200
    await deps.db.users.update_one({"id": sup_doc["id"]}, {"$unset": {"is_super_admin": ""}})
    r = await pay_http.get(url, headers=sup)
    assert r.status_code in (401, 403) and "pid-z" not in r.text


async def test_normalize_unit():
    from security.payment_ledger import classify_type, normalize_transaction
    assert [classify_type(x) for x in ("PAYMENT", "refund", "CANCEL", "VOID", "x")] == \
        ["payment", "refund", "cancel", "cancel", "other"]
    row = {"transactionType": "PAYMENT", "paymentId": "p", "paidPrice": "10.00"}
    a, b = normalize_transaction(row, "2026-09-01"), normalize_transaction(dict(row), "2026-09-01")
    assert a["id"] == b["id"] and a["id"].startswith("iyzico:payment:h")      # stable synthetic id
    assert a["commission"] is None and a["commission_rate_pct"] is None and a["net_payout"] == 10.0
    r = normalize_transaction({"transactionType": "REFUND", "transactionId": "r", "paidPrice": "-5.5"}, "2026-09-01")
    assert r["amount"] == 5.5 and r["net_payout"] == -5.5 and r["month"] == "2026-09"
    n = normalize_transaction(_tx("q", 100.0, payout_date="2026-09-20"), "2026-09-01")
    assert n["expected_payout_date"] == "2026-09-20"


# ═══════════════════════════════════ endpoints / permissions ═══════════════════
async def test_endpoints_super_admin_only_export_guarded_and_rerun_audited(pay_env, pay_http):
    d2, d1 = _days()
    m = IyzMock(pay_env, per_day={d1: [_tx("pid-e", 77.0, conv="e1")]})
    _, sup = await pay_staff(super_admin=True)
    _, ops = await pay_staff(["orders.view", "orders.update_status", "reports.view"])
    urls = ["/api/admin/payment-ledger/summary", "/api/admin/payment-ledger/discrepancies",
            "/api/admin/payment-ledger/export.csv"]
    for u in urls:
        assert (await pay_http.get(u)).status_code in (401, 403)
        assert (await pay_http.get(u, headers=ops)).status_code == 403
    body = {"date_from": d2, "date_to": d1}
    assert (await pay_http.post("/api/admin/payment-ledger/rerun", json=body, headers=ops)).status_code == 403
    assert not m.requests

    r = await pay_http.post("/api/admin/payment-ledger/rerun", json=body, headers=sup)
    assert r.status_code == 200, r.text
    assert r.json()["trigger"] == "admin_rerun" and r.json()["rows"] == 1
    acts = [(a.get("action"), a.get("phase")) for a in await pay_findall("audit_logs")
            if a.get("action") == "payment_ledger.rerun"]
    assert ("payment_ledger.rerun", "intent") in acts and ("payment_ledger.rerun", "done") in acts

    r = await pay_http.get("/api/admin/payment-ledger/summary", headers=sup)
    assert r.status_code == 200 and r.json()["months"][0]["gross"] == 77.0
    r = await pay_http.get("/api/admin/payment-ledger/discrepancies", headers=sup)
    assert r.status_code == 200 and r.json()["items"][0]["ref"] == "pid-e"
    r = await pay_http.get(f"/api/admin/payment-ledger/export.csv?month={d1[:7]}", headers=sup)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "pid-e" in r.text and "TOPLAM (TRY)" in r.text
    await pay_set_switch("freeze_exports", True)
    r = await pay_http.get(f"/api/admin/payment-ledger/export.csv?month={d1[:7]}", headers=sup)
    assert r.status_code == 423
    await pay_set_switch("freeze_exports", False)

    # validation
    for bad in ({"date_from": d1, "date_to": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()},
                {"date_from": "2026-01-01", "date_to": "2026-03-01"}, {"date_from": "x", "date_to": d1}):
        assert (await pay_http.post("/api/admin/payment-ledger/rerun", json=bad, headers=sup)).status_code == 400
    assert (await pay_http.get("/api/admin/payment-ledger/summary?month=2026-13", headers=sup)).status_code == 400


async def test_rerun_fail_closed_when_audit_unavailable(pay_env, pay_http, monkeypatch):
    d2, d1 = _days()
    m = IyzMock(pay_env, per_day={})
    _, sup = await pay_staff(super_admin=True)
    cls = type(deps.db.audit_logs)
    orig = cls.insert_one

    async def _boom(self_, doc, *a, **k):
        if getattr(self_, "name", "") == "audit_logs":
            raise RuntimeError("audit store down (test)")
        return await orig(self_, doc, *a, **k)
    monkeypatch.setattr(cls, "insert_one", _boom)
    r = await pay_http.post("/api/admin/payment-ledger/rerun", json={"date_from": d2, "date_to": d1}, headers=sup)
    assert r.status_code == 503
    assert not m.requests and not await pay_findall("payment_ledger")


# ═══════════════════════════════════ self-test / scheduler / rules ═════════════
async def test_selftest_check_states(pay_env):
    import inspect
    from security import selftest
    from security.payment_ledger import selftest_status
    assert "iyzico mutabakat defteri" in inspect.getsource(selftest.run_selftest)
    st, _ = await selftest_status(deps.db)
    assert st == "warn"                                       # configured, never ran
    now = datetime.now(timezone.utc)
    await deps.db.settings.update_one({"id": "payment_ledger_state"},
                                      {"$set": {"last_success_at": (now - timedelta(hours=40)).isoformat()}},
                                      upsert=True)
    st, detail = await selftest_status(deps.db)
    assert st == "fail" and "36" in detail
    await deps.db.settings.update_one({"id": "payment_ledger_state"},
                                      {"$set": {"last_success_at": (now - timedelta(hours=2)).isoformat()}})
    assert (await selftest_status(deps.db))[0] == "ok"
    await deps.db.payment_ledger_discrepancies.insert_one({"id": "x", "status": "open", "kind": "amount_mismatch"})
    assert (await selftest_status(deps.db))[0] == "warn"
    await deps.db.settings.delete_one({"id": "iyzico"})
    assert (await selftest_status(deps.db))[0] == "ok"        # iyzico not configured → nothing to check


async def test_scheduler_registers_new_daily_job_without_changing_others(monkeypatch):
    import scheduler

    class _J:
        def __init__(self, func, a, k):
            self.func, self.args, self.kw = func, a, k

    class _S:
        def __init__(self, *a, **k):
            self.jobs = {}

        def add_job(self, func, *a, **k):
            self.jobs[k.get("id") or getattr(func, "__name__", "?")] = _J(func, a, k)

        def start(self):
            pass

        def get_jobs(self):
            return list(self.jobs.values())
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", _S)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_lead", lambda fn: fn)
    sch = scheduler.start_scheduler()
    j = sch.jobs["iyzico_payment_ledger_daily"]
    assert j.args[0] == "cron" and j.kw["hour"] == 5 and j.kw["minute"] == 41
    assert j.func.__name__ == "run_payment_ledger"
    old = sch.jobs["iyzico_daily_reconciliation"]
    assert old.args[0] == "cron" and old.kw["hour"] == 4 and old.kw["minute"] == 23


async def test_business_rules_catalog_entries(pay_env):
    import business_rules
    keys = {r["key"]: r for r in business_rules.RULE_CATALOG}
    assert keys["payment.expected_commission_rate_max"]["default"] == 5
    assert keys["payment.ledger_payout_grace_days"]["default"] == 2
    await _set_rule("payment.expected_commission_rate_max", 0)       # 0 = check off
    from security.payment_ledger import run_payment_ledger
    d2, d1 = _days()
    IyzMock(pay_env, per_day={d1: [_tx("pid-hi", 100.0, rate=20.0, conv="none")]})
    await run_payment_ledger()
    assert not await pay_findall("payment_ledger_discrepancies", {"kind": "commission_rate_high"})


async def test_kurus_rounding_is_not_a_mismatch_but_real_difference_is(pay_env):
    """Canlı olay W12301: snapshot 9031.64, iyzico 9031.63 (3 taksit) → 1 kuruş, alarm YOK.
    Taksitli çok kalemli ödemede kalem yuvarlaması (⌈kalem/2⌉ kuruş) da alarm üretmez; TL düzeyi fark üretir."""
    from security.payment_ledger import run_payment_ledger
    _d2, d1 = _days()
    one = await _order("kurus1", 9031.64, paid_day=d1)
    multi = await _order("kurusm", 400.00, paid_day=d1)
    real = await _order("realdiff", 9031.64, paid_day=d1)
    rows = [_tx(one["iyzico_payment_id"], 9031.63, conv=one["id"], basket=one["order_number"], installment=3)]
    for i, amt in enumerate((100.005, 100.005, 100.005, 99.985)):   # iyzico kalem dağıtımı, 2 haneye yuvarlanınca 399.98
        rows.append(_tx(multi["iyzico_payment_id"], round(amt, 2), conv=multi["id"], basket=multi["order_number"],
                        installment=3, txid=f"tx-m{i}", tx_item=f"ptx-m{i}"))
    rows.append(_tx(real["iyzico_payment_id"], 9030.64, conv=real["id"], basket=real["order_number"], installment=3))
    IyzMock(pay_env, per_day={d1: rows})
    res = await run_payment_ledger()
    assert res["ok"] is True, res
    kinds = {(d["kind"], d["ref"]) for d in await pay_findall("payment_ledger_discrepancies")}
    assert ("amount_mismatch", one["iyzico_payment_id"]) not in kinds
    assert ("amount_mismatch", multi["iyzico_payment_id"]) not in kinds
    assert ("amount_mismatch", real["iyzico_payment_id"]) in kinds
