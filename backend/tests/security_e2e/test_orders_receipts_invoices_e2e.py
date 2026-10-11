"""E2E: bank-transfer receipts (contact proof, encryption at rest, versioning, 30-day private
archive) and invoices (Doğan e-Arşiv double-invoice protection BL-035, manual invoice in private
archive + signed expiring links)."""
import time
import types
from urllib.parse import parse_qs, urlparse

import pytest

from pay_harness import (
    SHIP, iyz_auth_ok, mock_3ds_auth, now_iso, pay_callback, pay_card_order, pay_create_order,
    pay_drain, pay_find, pay_findall, pay_seed_product, pay_set, pay_staff,
)

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n% test receipt \x00\x01\x02 binary\n%%EOF"


async def _havale_order(http):
    await pay_seed_product("p1", stock=5)
    r = await pay_create_order(http, "bank_transfer")
    assert r.status_code == 200, r.text
    return await pay_find("orders", {"id": r.json()["order_id"]})


async def _upload_receipt(http, order, contact, data=PDF, name="dekont.pdf"):
    return await http.post(f"/api/orders/by-number/{order['order_number']}/payment-notification",
                           files={"file": (name, data, "application/pdf")},
                           data={"contact": contact, "note": "odedim"})


# ═════════════════════════════════════ receipts ══════════════════════════════════════════════
async def test_receipt_upload_requires_contact_proof(pay_http, pay_env):
    o = await _havale_order(pay_http)
    assert (await _upload_receipt(pay_http, o, "")).status_code == 400
    # Denetim #17: telefonun TAMAMI artık geçerli kanıttır; yanlış/eksik değerler 404.
    for bad in ("someone@else.test", "9999", "123", "05559999999"):
        assert (await _upload_receipt(pay_http, o, bad)).status_code == 404, bad
    # unknown order number → same 404 (no enumeration oracle)
    r = await pay_http.post("/api/orders/by-number/W99999/payment-notification",
                            files={"file": ("d.pdf", PDF, "application/pdf")}, data={"contact": SHIP["email"]})
    assert r.status_code == 404
    cur = await pay_find("orders", {"id": o["id"]})
    assert not cur.get("payment_receipt") and cur["status"] == "awaiting_payment"


async def test_receipt_refused_for_card_order(pay_http, pay_env):
    o = await pay_card_order(pay_http)
    r = await _upload_receipt(pay_http, o, SHIP["email"])
    assert r.status_code == 400
    assert not (await pay_find("orders", {"id": o["id"]})).get("payment_receipt")


async def test_receipt_stored_encrypted_and_admin_gets_original(pay_http, pay_env):
    o = await _havale_order(pay_http)
    r = await _upload_receipt(pay_http, o, "4567")             # last 4 digits of the phone
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    rc = cur["payment_receipt"]
    assert rc.get("data_enc") and "data_b64" not in rc
    assert PDF not in rc["data_enc"].encode() and b"test receipt" not in rc["data_enc"].encode()
    assert cur["status"] == "payment_notified" and cur["payment_status"] == "pending"   # NOT paid
    _, hdr = await pay_staff(["orders.view"])
    r = await pay_http.get(f"/api/orders/{o['id']}/payment-receipt", headers=hdr)
    assert r.status_code == 200 and r.content == PDF
    # customers / anonymous cannot read it
    assert (await pay_http.get(f"/api/orders/{o['id']}/payment-receipt")).status_code == 401
    # list/detail responses never carry receipt bytes
    r = await pay_http.get(f"/api/orders/{o['id']}", headers=hdr)
    assert "data_enc" not in r.text and "data_b64" not in r.text


async def test_previous_receipt_is_versioned_encrypted(pay_http, pay_env):
    o = await _havale_order(pay_http)
    assert (await _upload_receipt(pay_http, o, SHIP["email"], data=b"FIRST-RECEIPT")).status_code == 200
    assert (await _upload_receipt(pay_http, o, SHIP["email"], data=PDF)).status_code == 200
    cur = await pay_find("orders", {"id": o["id"]})
    hist = cur["payment_receipts_history"]
    assert len(hist) == 1 and hist[0].get("version_id")
    v = await pay_find("payment_receipt_versions", {"id": hist[0]["version_id"]})
    assert v["receipt"].get("data_enc") and "data_b64" not in v["receipt"]
    from routes.orders import get_receipt_bytes
    assert get_receipt_bytes(v["receipt"]) == b"FIRST-RECEIPT"
    assert get_receipt_bytes(cur["payment_receipt"]) == PDF


async def test_private_archive_moves_old_receipts_and_admin_still_reads(pay_http, pay_env, pay_archive_on):
    import scheduler
    o = await _havale_order(pay_http)
    await _upload_receipt(pay_http, o, SHIP["email"], data=b"OLD-RECEIPT")
    await _upload_receipt(pay_http, o, SHIP["email"], data=PDF)
    old = now_iso(days=-40)
    await pay_set("orders", {"id": o["id"]}, {"payment_receipt.uploaded_at": old})
    from routes import deps
    await deps.db.payment_receipt_versions.update_many({"order_id": o["id"]},
                                                       {"$set": {"created_at": old, "receipt.uploaded_at": old}})
    await scheduler._run_private_archive()
    cur = await pay_find("orders", {"id": o["id"]})
    rc = cur["payment_receipt"]
    assert rc.get("archive_key") and "data_enc" not in rc and "data_b64" not in rc
    # Archive holds the ENCRYPTED token (encryption at rest continues after 30 days), never plaintext.
    from security import crypto as _c
    assert rc.get("archive_enc_format") == "fernet-bytes-v1"
    assert pay_archive_on[rc["archive_key"]][0] != PDF
    assert _c._fernet_dec.decrypt(pay_archive_on[rc["archive_key"]][0]) == PDF   # verified copy
    v = (await pay_findall("payment_receipt_versions", {"order_id": o["id"]}))[0]
    assert v["receipt"].get("archive_key") and "data_enc" not in v["receipt"]
    assert v["receipt"].get("archive_enc_format") == "fernet-bytes-v1"
    assert b"OLD-RECEIPT" not in pay_archive_on[v["receipt"]["archive_key"]][0]
    assert _c._fernet_dec.decrypt(pay_archive_on[v["receipt"]["archive_key"]][0]) == b"OLD-RECEIPT"
    _, hdr = await pay_staff(["orders.view"])
    r = await pay_http.get(f"/api/orders/{o['id']}/payment-receipt", headers=hdr)
    assert r.status_code == 200 and r.content == PDF


async def test_private_archive_keeps_recent_and_unverifiable_receipts(pay_http, pay_env, pay_archive_on,
                                                                     monkeypatch):
    import scheduler
    from services import r2_storage
    recent = await _havale_order(pay_http)
    await _upload_receipt(pay_http, recent, SHIP["email"])
    await scheduler._run_private_archive()
    assert (await pay_find("orders", {"id": recent["id"]}))["payment_receipt"].get("data_enc")
    # archive write that cannot be read back → DB copy is NOT deleted
    await pay_set("orders", {"id": recent["id"]}, {"payment_receipt.uploaded_at": now_iso(days=-45)})
    monkeypatch.setattr(r2_storage, "archive_get", lambda key: (b"corrupted", "application/pdf"))
    await scheduler._run_private_archive()
    rc = (await pay_find("orders", {"id": recent["id"]}))["payment_receipt"]
    assert rc.get("data_enc") and not rc.get("archive_key")


# ═════════════════════════════════════ Doğan e-Arşiv (BL-035) ════════════════════════════════
class FakeDogan:
    """Scripted Doğan SOAP behaviour; records every send / status lookup."""

    def __init__(self):
        self.sends = []          # (uuid, invoice_number)
        self.send_results = []   # queue of dicts
        self.lookups = []
        self.lookup_results = []  # queue of dict | Exception

    def install(self, monkeypatch):
        import dogan_client
        fake = self

        def _send(self_, ubl_xml, invoice_uuid, cust_email="", archive_note=""):
            import re
            m = re.search(r"<cbc:ID>([A-Z]{3}\d{13})</cbc:ID>", ubl_xml or "")
            fake.sends.append((invoice_uuid, m.group(1) if m else ""))
            return fake.send_results.pop(0)

        def _status(REQUEST_HEADER=None, UUID=None):
            fake.lookups.append(UUID)
            res = fake.lookup_results.pop(0)
            if isinstance(res, Exception):
                raise res
            return res(UUID) if callable(res) else res

        monkeypatch.setattr(dogan_client.DoganClient, "send_earsiv_invoice", _send)
        monkeypatch.setattr(dogan_client.DoganClient, "_get_earsiv_client",
                            lambda self_: types.SimpleNamespace(
                                service=types.SimpleNamespace(GetEArchiveInvoiceStatus=_status)))
        monkeypatch.setattr(dogan_client.DoganClient, "_make_header", lambda self_, compressed="N": {})
        monkeypatch.setattr(dogan_client.DoganClient, "login", lambda self_: "sess")
        monkeypatch.setattr(dogan_client.DoganClient, "logout", lambda self_: None)
        monkeypatch.setattr(dogan_client.DoganClient, "check_user",
                            lambda self_, vkn: {"is_efatura": False})
        return self

    @staticmethod
    def found(uuid, number):
        return {"INVOICE": [{"HEADER": {"UUID": uuid, "INVOICE_ID": number, "STATUS": "130",
                                        "STATUS_DESC": "Basarili"}}]}

    NOT_FOUND = {"ERROR_TYPE": {"ERROR_CODE": "2005", "ERROR_SHORT_DES": "Fatura bulunamadi"}}
    TIMEOUT = {"success": False, "code": "", "message": "HTTPSConnectionPool: Read timed out."}
    DUP = {"success": False, "code": "10009", "message": "Bu fatura daha önce gönderilmiş (mükerrer)"}
    REJECT = {"success": False, "code": "10013", "message": "Alici e-fatura mukellefi"}

    @staticmethod
    def ok(number):
        return {"success": True, "code": "0", "message": "OK", "invoice_id": number,
                "web_key": "https://earsiv.example.test/view/abc", "intl_txn_id": "itx"}


@pytest.fixture
def pay_dogan(pay_env, monkeypatch):
    import asyncio
    from routes import deps
    asyncio.run(deps.db.settings.insert_one({"id": "dogan_edonusum", "enabled": True, "username": "u",
                                             "password": "p", "is_test": True}))
    return FakeDogan().install(monkeypatch)


async def _invoiceable_order(http, env):
    o = await pay_card_order(http)
    mock_3ds_auth(env.iyzico, iyz_auth_ok(o))
    await pay_callback(http, o["id"], "pid-1")
    await pay_drain()
    o = await pay_find("orders", {"id": o["id"]})
    assert o["status"] == "confirmed" and o["payment_status"] == "paid"
    return o


async def _counter():
    from datetime import datetime, timezone
    c = await pay_find("counters", {"_id": f"invoice_seq_FCT{datetime.now(timezone.utc).year}"}, {"seq": 1})
    return (c or {}).get("seq")


async def _invoice(http, hdr, oid):
    return await http.post(f"/api/orders/{oid}/create-invoice", headers=hdr)


async def test_invoice_timeout_then_found_in_dogan_records_existing_no_second_invoice(pay_http, pay_env,
                                                                                    pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    pay_dogan.send_results.append(FakeDogan.TIMEOUT)
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 400
    cur = await pay_find("orders", {"id": o["id"]})
    pend = cur["invoice_pending"]
    assert not cur.get("invoice_issued") and pend["uuid"] and pend["number"]
    seq_after_first = await _counter()
    # retry: Doğan had actually accepted it → recorded, NOT sent again
    pay_dogan.lookup_results.append(FakeDogan.found(pend["uuid"], pend["number"]))
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_issued"] is True and cur["invoice_uuid"] == pend["uuid"]
    assert cur["invoice_number"] == pend["number"] and cur["invoice_recovered_from_dogan"] is True
    assert "invoice_pending" not in cur
    assert len(pay_dogan.sends) == 1 and await _counter() == seq_after_first


async def test_invoice_duplicate_answer_recovers_existing(pay_http, pay_env, pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    pay_dogan.send_results.append(FakeDogan.DUP)
    pay_dogan.lookup_results.append(lambda uuid: FakeDogan.found(uuid, "FCT2026000000777"))
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_issued"] and cur["invoice_number"] == "FCT2026000000777"
    assert len(pay_dogan.sends) == 1                      # never a second document
    assert cur["invoice_uuid"] == pay_dogan.sends[0][0] == pay_dogan.lookups[0]


async def test_invoice_pending_uuid_and_number_reused_on_retry(pay_http, pay_env, pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    pay_dogan.send_results.append(FakeDogan.TIMEOUT)
    assert (await _invoice(pay_http, hdr, o["id"])).status_code == 400
    pend = (await pay_find("orders", {"id": o["id"]}))["invoice_pending"]
    seq = await _counter()
    pay_dogan.lookup_results.append(FakeDogan.NOT_FOUND)
    pay_dogan.send_results.append(FakeDogan.ok(pend["number"]))
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 200, r.text
    assert [s[0] for s in pay_dogan.sends] == [pend["uuid"], pend["uuid"]]
    assert [s[1] for s in pay_dogan.sends] == [pend["number"]] * 2      # same number in the UBL
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_uuid"] == pend["uuid"] and cur["invoice_number"] == pend["number"]
    assert await _counter() == seq                        # no new number consumed


async def test_invoice_ambiguous_lookup_needs_review_409(pay_http, pay_env, pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    pay_dogan.send_results.append(FakeDogan.TIMEOUT)
    assert (await _invoice(pay_http, hdr, o["id"])).status_code == 400
    seq = await _counter()
    pay_dogan.lookup_results.append(RuntimeError("SOAP fault: service unavailable"))
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 409
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_needs_review"] is True and not cur.get("invoice_issued")
    assert cur.get("invoice_pending") and cur["invoice_in_progress"] is False
    assert len(pay_dogan.sends) == 1 and await _counter() == seq


async def test_invoice_duplicate_with_ambiguous_lookup_needs_review(pay_http, pay_env, pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    pay_dogan.send_results.append(FakeDogan.DUP)
    pay_dogan.lookup_results.append(RuntimeError("SOAP fault"))
    seq_before = await _counter() or 0
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 409
    cur = await pay_find("orders", {"id": o["id"]})
    assert cur["invoice_needs_review"] is True and not cur.get("invoice_issued")
    assert len(pay_dogan.sends) == 1
    assert await _counter() == seq_before + 1            # only the first number, no advance


async def test_invoice_definite_reject_rolls_counter_back(pay_http, pay_env, pay_dogan):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    seq_before = await _counter() or 0
    pay_dogan.send_results.append(FakeDogan.REJECT)
    r = await _invoice(pay_http, hdr, o["id"])
    assert r.status_code == 400
    cur = await pay_find("orders", {"id": o["id"]})
    assert "invoice_pending" not in cur and not cur.get("invoice_issued")
    assert await _counter() == seq_before
    # next attempt re-uses the same (rolled back) number
    pay_dogan.send_results.append(FakeDogan.ok("x"))
    assert (await _invoice(pay_http, hdr, o["id"])).status_code == 200
    assert (await pay_find("orders", {"id": o["id"]}))["invoice_number"].endswith(f"{seq_before + 1:09d}")


async def test_invoice_refused_for_unpaid_card_and_unapproved_havale(pay_http, pay_env, pay_dogan):
    _, hdr = await pay_staff(["orders.invoice"])
    card = await pay_card_order(pay_http)
    assert (await _invoice(pay_http, hdr, card["id"])).status_code == 400
    await pay_seed_product("p9", stock=3)
    r = await pay_create_order(pay_http, "bank_transfer", items=[{"product_id": "p9", "quantity": 1}],
                               ship={**SHIP, "email": "hv@example.test", "phone": "05553332211"})
    hv = r.json()["order_id"]
    await pay_set("orders", {"id": hv}, {"status": "confirmed"})       # status only, payment pending
    assert (await _invoice(pay_http, hdr, hv)).status_code == 400
    assert pay_dogan.sends == []


# ═════════════════════════════════════ manual invoice (private archive) ═════════════════════
async def _upload_manual(http, hdr, oid, data=PDF):
    return await http.post(f"/api/orders/{oid}/upload-invoice", headers=hdr,
                           files={"file": ("fatura.pdf", data, "application/pdf")},
                           data={"invoice_number": "MAN2026000001"})


def _signed_path(url):
    u = urlparse(url)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


async def test_manual_invoice_archive_only_and_signed_link(pay_http, pay_env, pay_archive_on):
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    r = await _upload_manual(pay_http, hdr, o["id"])
    assert r.status_code == 200, r.text
    cur = await pay_find("orders", {"id": o["id"]})
    key = cur["invoice_manual_file"]["archive_key"]
    assert pay_archive_on[key][0] == PDF
    assert await pay_findall("files") == []                         # not in DB / public bucket
    assert "/manual-invoice-file/signed?" in cur["invoice_pdf_url"]
    path, q = _signed_path(cur["invoice_pdf_url"])
    r = await pay_http.get(path, params=q)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers.get("cache-control", "").startswith("private")
    # tampered signature / exp / other order → 403
    bad_sig = dict(q, sig=("0" if q["sig"][0] != "0" else "1") + q["sig"][1:])
    assert (await pay_http.get(path, params=bad_sig)).status_code == 403
    assert (await pay_http.get(path, params=dict(q, exp=str(int(q["exp"]) + 86400)))).status_code == 403
    assert (await pay_http.get(path.replace(o["id"], "other-order"), params=q)).status_code == 403
    assert (await pay_http.get(path, params={})).status_code == 403
    # panel endpoint requires orders.invoice
    _, nohdr = await pay_staff(["orders.view"])
    assert (await pay_http.get(f"/api/orders/{o['id']}/manual-invoice-file", headers=nohdr)).status_code == 403
    assert (await pay_http.get(f"/api/orders/{o['id']}/manual-invoice-file", headers=hdr)).status_code == 200


async def test_manual_invoice_expired_signature_403(pay_http, pay_env, pay_archive_on):
    from routes.orders import _manual_invoice_sig
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    assert (await _upload_manual(pay_http, hdr, o["id"])).status_code == 200
    exp = int(time.time()) - 5
    r = await pay_http.get(f"/api/orders/{o['id']}/manual-invoice-file/signed",
                           params={"exp": exp, "sig": _manual_invoice_sig(o["id"], exp)})
    assert r.status_code == 403


async def test_old_key_link_still_valid_after_invoice_link_secret_set(pay_http, pay_env, pay_archive_on,
                                                                     monkeypatch):
    from routes.orders import _manual_invoice_sig
    o = await _invoiceable_order(pay_http, pay_env)
    _, hdr = await pay_staff(["orders.invoice"])
    assert (await _upload_manual(pay_http, hdr, o["id"])).status_code == 200
    old_url = (await pay_find("orders", {"id": o["id"]}))["invoice_pdf_url"]   # JWT-derived key
    monkeypatch.setenv("INVOICE_LINK_SECRET", "n" * 40)
    path, q = _signed_path(old_url)
    r = await pay_http.get(path, params=q)
    assert r.status_code == 200 and r.content == PDF
    exp = int(time.time()) + 3600
    new_sig = _manual_invoice_sig(o["id"], exp)                                # new key
    assert new_sig != _manual_invoice_sig(o["id"], exp, key=b"z" * 40)
    assert (await pay_http.get(path, params={"exp": exp, "sig": new_sig})).status_code == 200
    foreign = _manual_invoice_sig(o["id"], exp, key=b"attacker-key-" * 3)
    assert (await pay_http.get(path, params={"exp": exp, "sig": foreign})).status_code == 403


async def test_manual_invoice_refused_on_unpaid_card_order(pay_http, pay_env, pay_archive_on):
    o = await pay_card_order(pay_http)
    _, hdr = await pay_staff(["orders.invoice"])
    assert (await _upload_manual(pay_http, hdr, o["id"])).status_code == 400
    assert pay_archive_on == {}
