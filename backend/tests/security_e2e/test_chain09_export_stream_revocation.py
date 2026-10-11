"""CHAIN09 / ADM-02 — an export response that is already streaming stops when access is revoked.

Bulk exports are marked by the shared ``freeze_exports`` gate (``security_switches.require_switch_off``) and
``security.export_guard.ExportGuardMiddleware`` re-checks — right before the response starts (in-memory
exports) and between body chunks (every ~5 s / every N chunks) — that the staff member is still active,
still staff with the same role, the token is not revoked (token_version / global session epoch) and
``freeze_exports`` is still off. On failure the stream is cut with an error marker line and a security event
(audit chain + critical alert) is written. One small DB read per check, never per row.

The app is driven at the raw ASGI level so the test can act BETWEEN body chunks.
"""
from __future__ import annotations

import asyncio
import io

import pytest

from pay_harness import pay_findall, pay_set_switch, pay_staff
from routes import deps

pytestmark = pytest.mark.asyncio
MEMBERS = "/api/admin/members/export.xlsx"


async def _seed_members(n=60):
    await deps.db.users.insert_many([
        {"id": f"m-{i}", "email": f"member{i}@example.test", "first_name": f"Ad{i}", "last_name": "Soyad",
         "phone": "", "is_admin": False, "is_active": True, "created_at": f"2026-09-{(i % 28) + 1:02d}T10:00:00",
         "cached_segment": "loyal", "cached_orders": i, "cached_spent": 100.0 * i}
        for i in range(n)])


async def _asgi_get(path, headers, on_body=None):
    """GET via raw ASGI. on_body(k) is awaited after the k-th body message reached the 'client'."""
    import server
    raw, _, qs = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "GET", "scheme": "https", "path": raw, "raw_path": raw.encode(),
             "query_string": qs.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in {**headers, "host": "www.meluni.com.tr"}.items()],
             "client": ("203.0.113.10", 40000), "server": ("www.meluni.com.tr", 443)}
    msgs = []
    state = {"sent": False}
    forever = asyncio.Event()

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await forever.wait()

    async def send(m):
        msgs.append(m)
        if m["type"] == "http.response.body" and on_body is not None:
            await on_body(sum(1 for x in msgs if x["type"] == "http.response.body"))
    await server.app(scope, receive, send)
    start = next(m for m in msgs if m["type"] == "http.response.start")
    bodies = [m for m in msgs if m["type"] == "http.response.body"]
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, \
        b"".join(m.get("body", b"") for m in bodies), bodies


@pytest.fixture
def small_chunks(monkeypatch):
    import security.export_guard as eg
    monkeypatch.setattr(eg, "CHUNK_BYTES", 512)
    monkeypatch.setattr(eg, "RECHECK_INTERVAL_SEC", 0.0)     # check before every chunk (after the first)
    return eg


def _xlsx_rows(data: bytes) -> int:
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(data)).active.max_row


async def _events(kind="export_aborted_access_revoked"):
    return [a for a in await pay_findall("audit_logs") if a["action"] == f"security.{kind}"]


async def test_export_streams_complete_when_access_stays_valid(pay_env, small_chunks):
    await _seed_members()
    _, hdr = await pay_staff(super_admin=True)
    status, headers, body, bodies = await _asgi_get(MEMBERS, hdr)
    assert status == 200 and "spreadsheetml" in headers["content-type"]
    assert "content-length" not in headers                    # cut-able (chunked) stream
    assert len(bodies) > 4 and not body.endswith(small_chunks.ABORT_MARKER)
    assert _xlsx_rows(body) == 61                             # header + 60 members, file intact
    assert not await _events()


async def _deactivate(uid):
    await deps.db.users.update_one({"id": uid}, {"$set": {"is_active": False}})


async def _logout_all(uid):
    await deps.db.users.update_one({"id": uid}, {"$inc": {"token_version": 1}})


async def _revoke_all(uid):
    await deps.bump_security_epoch()


async def _freeze(uid):
    await pay_set_switch("freeze_exports", True)


async def _drop_super(uid):
    await deps.db.users.update_one({"id": uid}, {"$unset": {"is_super_admin": ""}})


async def _change_role(uid):
    await deps.db.users.update_one({"id": uid}, {"$set": {"role_id": "role-other"}})


@pytest.mark.parametrize("action,reason", [
    (_deactivate, "user_deactivated"),
    (_logout_all, "token_revoked"),
    (_revoke_all, "sessions_revoked"),
    (_freeze, "exports_frozen"),
    (_drop_super, "staff_role_removed"),
    (_change_role, "staff_role_changed"),
])
async def test_revocation_between_chunks_stops_the_stream(pay_env, small_chunks, action, reason):
    await _seed_members()
    if action is _change_role:
        u, hdr = await pay_staff(["customers.export"])
    else:
        u, hdr = await pay_staff(super_admin=True)
    _, _, full, full_bodies = await _asgi_get(MEMBERS, hdr)
    assert len(full_bodies) > 4
    done = {"n": 0}

    async def _on_body(k):
        if k == 2 and not done["n"]:
            done["n"] = 1
            await action(u["id"])
    status, _, body, bodies = await _asgi_get(MEMBERS, hdr, on_body=_on_body)
    assert status == 200                                       # headers had already gone out
    assert body.endswith(small_chunks.ABORT_MARKER)
    data = body[:-len(small_chunks.ABORT_MARKER)]
    # exactly the two chunks delivered before the revocation — nothing after it
    assert len(bodies) == 3 and bodies[-1].get("more_body") is False
    assert len(data) == sum(len(b["body"]) for b in bodies[:2]) < len(full)
    # xlsx her üretimde zaman damgası (zip başlığı + docProps/core.xml) yazar → iki üretimin baytları
    # birebir aynı olmayabilir; kesilen akışın aynı dosyanın BAŞI olduğu zip imzası ve uzunlukla doğrulanır.
    assert data[:4] == full[:4] == b"PK\x03\x04"
    with pytest.raises(Exception):
        _xlsx_rows(body)                                       # truncated file is unusable
    ev = await _events()
    assert len(ev) == 1, ev
    meta = ev[0]["metadata"]
    assert meta["reason"] == reason and meta["phase"] == "mid_stream" and meta["path"] == MEMBERS
    assert meta["bytes_sent"] == len(data) and ev[0]["actor"]["id"] == u["id"]
    alerts = await pay_findall("alerts", {"kind": "export_aborted_access_revoked"})
    assert alerts and alerts[0]["level"] == "critical"


async def test_revoked_while_building_returns_error_instead_of_file(pay_http, pay_env, monkeypatch):
    """In-memory export: the staff member is deactivated after the data was read but before the response
    is returned → the file is NOT sent (re-check right before returning)."""
    await _seed_members(10)
    u, hdr = await pay_staff(super_admin=True)
    import security.events as ev
    real = ev.security_event
    seen = []

    async def _spy(kind, actor=None, meta=None, **kw):
        seen.append((kind, dict(meta or {})))
        if kind == "member_export":
            await _deactivate(u["id"])
        return await real(kind, actor, meta, **kw)
    monkeypatch.setattr(ev, "security_event", _spy)
    r = await pay_http.get(MEMBERS, headers=hdr)
    assert r.status_code == 401, r.text[:200]
    assert "spreadsheet" not in r.headers.get("content-type", "") and b"PK" not in r.content[:4]
    assert "devre dışı" in r.json()["detail"] and r.json()["code"] == "export_aborted:user_deactivated"
    assert ("export_aborted_access_revoked",) == tuple(k for k, _ in seen if k.startswith("export_aborted"))
    m = next(m for k, m in seen if k == "export_aborted_access_revoked")
    assert m["phase"] == "before_send" and m["bytes_sent"] == 0 and m["reason"] == "user_deactivated"


async def test_freeze_switched_on_while_building_returns_423(pay_http, pay_env, monkeypatch):
    await _seed_members(5)
    _, hdr = await pay_staff(super_admin=True)
    import security.events as ev
    real = ev.security_event

    async def _spy(kind, actor=None, meta=None, **kw):
        if kind == "member_export":
            await pay_set_switch("freeze_exports", True)
        return await real(kind, actor, meta, **kw)
    monkeypatch.setattr(ev, "security_event", _spy)
    r = await pay_http.get(MEMBERS, headers=hdr)
    assert r.status_code == 423 and "dışa aktarım" in r.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/api/admin/consents/export.xlsx", "/api/production-plan/export",
                                  "/api/influencer-registry/export", "/api/marketing-pixels/capi/logs/export"])
async def test_other_exports_are_guarded_by_the_same_gate(pay_env, monkeypatch, path):
    import security.export_guard as eg
    monkeypatch.setattr(eg, "CHUNK_BYTES", 64)
    monkeypatch.setattr(eg, "RECHECK_INTERVAL_SEC", 0.0)
    u, hdr = await pay_staff(super_admin=True)
    status, _, full, _ = await _asgi_get(path, hdr)
    assert status == 200 and len(full) > 64, (path, status, len(full))

    async def _on_body(k):
        if k == 1:
            await _deactivate(u["id"])
    status, _, body, _ = await _asgi_get(path, hdr, on_body=_on_body)
    # xlsx zaman damgası iki üretim arasında değişebilir → bayt eşitliği yerine uzunluk + imza + işaret.
    assert body.endswith(eg.ABORT_MARKER) and len(body) == 64 + len(eg.ABORT_MARKER) and body[:4] == full[:4]
    ev = await _events()
    assert ev and ev[-1]["metadata"]["reason"] == "user_deactivated" and ev[-1]["metadata"]["path"] == path


async def test_recheck_is_per_interval_not_per_row(pay_env, monkeypatch):
    import security.export_guard as eg
    monkeypatch.setattr(eg, "CHUNK_BYTES", 256)
    monkeypatch.setattr(eg, "RECHECK_INTERVAL_SEC", 3600.0)
    monkeypatch.setattr(eg, "RECHECK_EVERY_CHUNKS", 4)
    real = eg.export_block_reason
    checks = []

    async def _count(ex):
        checks.append(1)
        return await real(ex)
    monkeypatch.setattr(eg, "export_block_reason", _count)
    await _seed_members(200)
    _, hdr = await pay_staff(super_admin=True)
    status, _, body, bodies = await _asgi_get(MEMBERS, hdr)
    pieces = len([b for b in bodies if b.get("body")])
    assert status == 200 and pieces > 12 and _xlsx_rows(body) == 201
    # one check before the response starts + one per 4 chunks — independent of the 200 rows
    assert len(checks) == 1 + (pieces - 1) // 4, (len(checks), pieces)


async def test_non_export_requests_are_not_rechecked(pay_http, pay_env, monkeypatch):
    import security.export_guard as eg
    calls = []

    async def _count(ex):
        calls.append(1)
        return None
    monkeypatch.setattr(eg, "export_block_reason", _count)
    _, hdr = await pay_staff(super_admin=True)
    r = await pay_http.get("/api/admin/security-switches", headers=hdr)
    assert r.status_code == 200 and calls == []
    assert r.headers.get("content-length")                    # untouched response
