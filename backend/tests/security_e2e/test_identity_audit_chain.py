"""Tamper-evident admin audit chain: linkage, verification, tamper/delete detection + alert,
archive export and DB-vs-archive comparison."""
import json
from datetime import timedelta
from unittest import mock

import pytest
from freezegun import freeze_time

from id_harness import (id_seed_user, id_seed_role, id_auth, id_find, id_findall, id_token, id_run, id_update)
from routes import deps

pytestmark = pytest.mark.usefixtures("id_env")

VERIFY = "/api/admin/security/audit-chain/verify"


def _write(n, prefix="test.action"):
    from activity_audit import record_admin_audit

    async def _w():
        for i in range(n):
            ok = await record_admin_audit(deps.db, action=f"{prefix}.{i}", entity_type="t", entity_id=str(i),
                                          current_user={"id": "actor-1", "email": "a@example.test"},
                                          metadata={"i": i}, strict=True)
            assert ok
    id_run(_w())


@pytest.fixture
def sa():
    return id_seed_user(kind="super")


def _prime_export_state():
    # mongomock quirk: find_one_and_update returns None (real MongoDB: {}) when the projected fields
    # are all absent, which the export lock reads as "locked". Seed the counter real exports maintain.
    id_update("settings", {"_id": "audit_chain"}, {"$set": {"exported_seq": 0}})


def _verify(client, sa, **body):
    r = client.post(VERIFY, json={"full": True, **body}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    return r.json()


def test_records_are_chained_and_verify_ok(id_client, sa):
    _write(5)
    recs = sorted(id_findall("audit_logs"), key=lambda r: r["chain_seq"])
    assert [r["chain_seq"] for r in recs] == [1, 2, 3, 4, 5]
    assert recs[0]["prev_hash"] == "0" * 64
    for a, b in zip(recs, recs[1:]):
        assert b["prev_hash"] == a["hash"]
    head = id_find("settings", {"_id": "audit_chain"})
    assert head["seq"] == 5 and head["head_hash"] == recs[-1]["hash"]
    out = _verify(id_client, sa)
    assert out["chain"]["ok"] is True and out["chain"]["checked"] == 5 and not out["chain"]["problems"]
    # status endpoint is super-admin only
    id_seed_role("r_x", ["orders.view"])
    staff = id_seed_user(kind="staff", role_id="r_x")
    assert id_client.post(VERIFY, json={}, headers=id_auth(id_token(staff))).status_code == 403
    assert id_client.get("/api/admin/security/audit-chain", headers=id_auth(id_token(staff))).status_code == 403
    st = id_client.get("/api/admin/security/audit-chain", headers=id_auth(id_token(sa))).json()
    assert st["head_seq"] >= 5 and st["last_verify_ok"] is True


def test_tampered_record_detected_and_alerted(id_client, sa):
    _write(5)
    id_update("audit_logs", {"chain_seq": 3}, {"$set": {"metadata.i": 999}})
    out = _verify(id_client, sa)
    probs = out["chain"]["problems"]
    assert out["chain"]["ok"] is False
    assert {"seq": 3, "type": "hash_mismatch"} in probs
    alerts = id_findall("alerts", {"kind": "audit_chain_broken"})
    assert alerts and alerts[0]["level"] == "critical"
    assert "security.audit_chain_broken" in [r.get("action") for r in id_findall("audit_logs")]
    # recomputing the record's own hash does not help: the NEXT link breaks
    from security.audit_chain import compute_hash
    rec = id_find("audit_logs", {"chain_seq": 3})
    id_update("audit_logs", {"chain_seq": 3}, {"$set": {"hash": compute_hash(rec["prev_hash"], rec)}})
    probs = _verify(id_client, sa)["chain"]["problems"]
    assert {"seq": 4, "type": "link_mismatch"} in probs


def test_deleted_record_reported_missing(id_client, sa):
    _write(5)
    id_run(deps.db.audit_logs.delete_one({"chain_seq": 2}))
    out = _verify(id_client, sa)
    assert out["chain"]["ok"] is False
    assert {"seq": 2, "type": "missing"} in out["chain"]["problems"]
    assert id_findall("alerts", {"kind": "audit_chain_broken"})


def test_deleted_tail_reported_after_inflight_grace(id_client, sa):
    with freeze_time("2026-10-03 10:00:00") as frozen:
        _write(5)
        id_run(deps.db.audit_logs.delete_one({"chain_seq": 5}))
        frozen.tick(delta=timedelta(minutes=2))
        from security.audit_chain import verify_chain
        res = id_run(verify_chain(deps.db, full=True))
    assert res["ok"] is False
    assert {"seq": 5, "type": "missing_tail"} in res["problems"]


def test_failed_write_is_a_known_gap_not_tampering(id_client, sa):
    from activity_audit import record_admin_audit
    _write(2)
    cls = type(deps.db.audit_logs)
    orig = cls.insert_one

    async def _boom(self_, doc, *a, **k):
        if getattr(self_, "name", "") == "audit_logs":
            raise RuntimeError("down")
        return await orig(self_, doc, *a, **k)
    with mock.patch.object(cls, "insert_one", _boom):
        ok = id_run(record_admin_audit(deps.db, action="lost.one", entity_type="t", entity_id="x"))
    assert ok is False
    _write(1, prefix="after")
    from security.audit_chain import verify_chain
    res = id_run(verify_chain(deps.db, full=True))
    assert res["ok"] is True, res
    assert 3 in res["known_gaps"]


def test_export_to_archive_and_db_vs_archive(id_client, sa, id_archive):
    _write(4)
    _prime_export_state()
    r = id_client.post("/api/admin/security/audit-chain/export", headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["written"] >= 4 and out["objects"]
    key = out["objects"][0]
    assert key.startswith("denetim/") and key.endswith(".jsonl")
    data, ctype = id_archive[key]
    assert ctype == "application/x-ndjson"
    lines = [json.loads(x) for x in data.decode().splitlines() if x.strip()]
    assert [x["chain_seq"] for x in lines][:4] == [1, 2, 3, 4]
    assert all("hash" in x and "prev_hash" in x for x in lines)
    head = id_find("settings", {"_id": "audit_chain"})
    assert head["exported_seq"] == lines[-1]["chain_seq"]
    # archive verifies against itself and the DB
    v = _verify(id_client, sa, archive_key=key)
    assert v["archive"]["ok"] is True and v["archive"]["checked"] == len(lines)
    # someone edits the DB copy after export → detected by comparing with the archive
    id_update("audit_logs", {"chain_seq": 2}, {"$set": {"action": "something.else"}})
    v = _verify(id_client, sa, archive_key=key)
    assert v["archive"]["ok"] is False
    assert {"seq": 2, "type": "db_differs_from_archive"} in v["archive"]["problems"]
    # deleting from the DB is detected too
    id_run(deps.db.audit_logs.delete_one({"chain_seq": 3}))
    v = _verify(id_client, sa, archive_key=key)
    assert {"seq": 3, "type": "db_record_missing"} in v["archive"]["problems"]
    # an archive object that was itself altered is detected
    bad = data.decode().replace('"i": 0', '"i": 7', 1).encode()
    id_archive[key] = (bad, ctype)
    v = _verify(id_client, sa, archive_key=key)
    assert {"seq": 1, "type": "archive_hash_mismatch"} in v["archive"]["problems"]
    # path traversal / foreign keys are refused
    assert _verify(id_client, sa, archive_key="../secrets.txt")["archive"]["error"] == "invalid_key"


def test_export_is_incremental(id_client, sa, id_archive):
    _write(3)
    _prime_export_state()
    from security.audit_chain import export_batch_to_archive
    first = id_run(export_batch_to_archive(deps.db))
    assert first.get("written") == 3, first
    again = id_run(export_batch_to_archive(deps.db))
    assert again["ok"] and again["written"] == 0
    _write(2, prefix="more")
    third = id_run(export_batch_to_archive(deps.db))
    assert third["written"] == 2
    assert len(id_archive) == 2


def test_audit_metadata_keeps_ids_but_redacts_pii(id_client):
    """Regression: 32-hex / uuid ids containing a 7-digit run were mistaken for phone numbers and
    redacted, randomly breaking the approval ↔ action ↔ intent linkage in the audit trail."""
    from activity_audit import record_admin_audit, run_audited
    appr = "04fed6336dbc4a09831d6c76a6500924"  # contains "6500924"
    user_uuid = "3f2a1b4c-1234-4567-8901-123456789012"

    async def _go():
        await record_admin_audit(deps.db, action="t.link", entity_type="t", entity_id="x",
                                 metadata={"approval_id": appr, "requested_by": user_uuid,
                                           "note": "call 0555 123 45 67", "contact": "a@b.example.test"},
                                 strict=True)

        async def _p():
            return 1
        await run_audited(deps.db, action="t.run", entity_type="t", entity_id="y", perform=_p,
                          metadata={"approval_id": appr})
    id_run(_go())
    link = id_find("audit_logs", {"action": "t.link"})
    assert link["metadata"]["approval_id"] == appr and link["metadata"]["requested_by"] == user_uuid
    assert link["metadata"]["note"] == "[REDACTED_PII]" and link["metadata"]["contact"] == "[REDACTED_PII]"
    intent = id_find("audit_logs", {"action": "t.run", "phase": "intent"})
    done = id_find("audit_logs", {"action": "t.run", "phase": "done"})
    assert done["metadata"]["intent_id"] == intent["id"] and done["metadata"]["approval_id"] == appr
