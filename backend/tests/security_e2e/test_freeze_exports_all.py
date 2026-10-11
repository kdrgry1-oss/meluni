"""E2E: the ``freeze_exports`` kill switch closes EVERY bulk data export endpoint (423).

Export routes are DISCOVERED from the live app (endpoint source mentions openpyxl / csv / xlsx /
StreamingResponse / spreadsheet, or the path looks like an export) — a new export endpoint that
forgets the guard fails this test. A small, documented allow-list holds routes that are NOT data
exports (file IMPORTS, the empty price/stock template, the forensic audit-archive push, static docs).
Each export also emits a security event when the switch is off (checked for a representative set).
"""
import inspect
import re

import pytest
from fastapi.routing import APIRoute

_SRC_RX = re.compile(r"openpyxl|csv\.writer|csv\.DictWriter|StreamingResponse|\.xlsx|text/csv|spreadsheetml")
_PATH_RX = re.compile(r"export|\.xlsx|\.csv|print-range")

# path → reason (NOT bulk data exports; documented in the report)
NOT_EXPORTS = {
    "/api/products/attributes/import-xlsx": "upload/import (reads a file, returns nothing)",
    "/api/products/attributes/import-technical-xlsx": "upload/import",
    "/api/integrations/rooftr/products/upload-excel": "upload/import",
    "/api/production-plan/import": "upload/import",
    "/api/bulk-ops/price-stock/template": "empty template (headers only, no data)",
    "/api/admin/security/audit-chain/export": "pushes the audit chain to the PRIVATE archive "
                                              "(forensics must keep working during an incident)",
}

# minimal valid bodies so FastAPI validation passes and the guard is reached
BODIES = {"/api/admin/reports2/export-xlsx": {"title": "t", "columns": [{"k": "a", "l": "A"}], "rows": []}}
QUERY = {"/api/orders/returns/gider-pusulasi/print-range": "?from_no=1&to_no=2"}


def _discover(app):
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        try:
            src = inspect.getsource(r.endpoint)
        except (OSError, TypeError):
            src = ""
        if not (_SRC_RX.search(src) or _PATH_RX.search(r.path)):
            continue
        if r.path in NOT_EXPORTS:
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path))
    return sorted(set(out))


def _concrete(path):
    return re.sub(r"\{[^}]+\}", "0123456789abcdef0123456789abcdef", path) + QUERY.get(path, "")


def _switch(db, on):
    import asyncio
    import security_switches

    async def _s():
        await db.settings.update_one(
            {"id": "security_switches"},
            {"$set": {"switches.freeze_exports": {"on": on, "reason": "test freeze"}}}, upsert=True)
    asyncio.run(_s())
    security_switches.invalidate_cache()


def test_every_export_route_is_discovered(kv_app):
    found = {p for _, p in _discover(kv_app)}
    must = {
        "/api/admin/members/export.xlsx", "/api/admin/consents/export.xlsx",
        "/api/admin/rooftr/return-orders/export", "/api/orders/returns/gider-pusulasi/export",
        "/api/orders/returns/gider-pusulasi/print-range", "/api/admin/reports/products/export-xlsx",
        "/api/products/export/excel", "/api/integrations/trendyol/claims/export",
        "/api/influencer-pr/export", "/api/influencer-pr/calendar-export", "/api/influencer-registry/export",
        "/api/marketing-pixels/capi/logs/export", "/api/production-plan/export",
        "/api/admin/reports2/export-xlsx", "/api/amazon/spapi/catalog/report.xlsx",
        "/api/admin/email-marketing/campaigns/{campaign_id}/report.xlsx",
        "/api/admin/reports/assistant/file/{file_id}",
    }
    assert must <= found, sorted(must - found)


def test_freeze_exports_returns_423_on_every_export(kv_app, kv_client, kv_users, kv_db):
    routes = _discover(kv_app)
    assert len(routes) >= 17
    admin = kv_users.staff(super_admin=True)
    hdr = kv_users.hdr(admin)
    _switch(kv_db, True)
    bad = []
    for method, path in routes:
        url = _concrete(path)
        r = kv_client.request(method, url, headers=hdr, json=BODIES.get(path))
        if r.status_code != 423:
            bad.append((method, path, r.status_code, r.text[:120]))
    assert not bad, bad
    # switch off → no longer 423 (guard is the switch, not a hard block)
    _switch(kv_db, False)
    for method, path in routes:
        r = kv_client.request(method, _concrete(path), headers=hdr, json=BODIES.get(path))
        assert r.status_code != 423, (method, path)


def test_export_emits_security_event(kv_client, kv_users, kv_db, monkeypatch):
    import security.events as ev
    seen = []

    async def _rec(kind, actor=None, meta=None, **kw):
        seen.append((kind, (actor or {}).get("id"), dict(meta or {})))
    monkeypatch.setattr(ev, "security_event", _rec)
    admin = kv_users.staff(super_admin=True)
    hdr = kv_users.hdr(admin)
    _switch(kv_db, False)
    for url, kind in (("/api/production-plan/export", "production_plan_export"),
                      ("/api/influencer-registry/export", "influencer_registry_export"),
                      ("/api/marketing-pixels/capi/logs/export", "capi_logs_export")):
        r = kv_client.get(url, headers=hdr)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
        assert any(k == kind and a == admin["id"] and "rows" in m for k, a, m in seen), (kind, seen)
    r = kv_client.post("/api/admin/reports2/export-xlsx", headers=hdr,
                       json={"title": "t", "columns": [{"k": "a", "l": "A"}], "rows": [{"a": 1}, {"a": 2}]})
    assert r.status_code == 200
    assert ("report_table_export", admin["id"], {"export": "rapor.xlsx", "rows": 2}) in seen
