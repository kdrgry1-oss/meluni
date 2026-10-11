"""CHAIN07 / FIN-E07 — audit outbox for the best-effort (non-critical) audit path.

* audit_logs write fails but Mongo is up → the record goes to the `audit_outbox` collection; the
  flush job appends it to the hash chain once audit writes work again (exactly once).
* Mongo itself is down → the record goes to a bounded local JSONL file (backend/data/audit_outbox/,
  overridable with AUDIT_OUTBOX_DIR); on recovery the flush moves it into the chain and deletes the file.
* The bounded file never grows past AUDIT_OUTBOX_MAX_BYTES (drops are counted and reported).
* Critical (fail-closed) actions still refuse to run when the audit cannot be written — no outbox.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from conftest import kv_run
from kv_harness import kv_find, kv_findall, kv_count


@pytest.fixture
def outbox_dir(tmp_path, monkeypatch):
    d = tmp_path / "audit_outbox"
    monkeypatch.setenv("AUDIT_OUTBOX_DIR", str(d))
    return d


def _break(*names):
    """Make writes fail: for the named collections, or for EVERY collection with "*" (Mongo down)."""
    import contextlib
    from routes.deps import db
    cls = type(db.audit_logs)
    stack = contextlib.ExitStack()
    for meth in (("insert_one", "update_one") if "*" in names else ("insert_one",)):
        orig = getattr(cls, meth)

        def _mk(orig):
            async def _boom(self_, *a, **k):
                if "*" in names or getattr(self_, "name", "") in names:
                    raise RuntimeError("mongo down")
                return await orig(self_, *a, **k)
            return _boom
        stack.enter_context(mock.patch.object(cls, meth, _mk(orig)))
    return stack


def _audit(action, **kw):
    from activity_audit import record_admin_audit
    from routes.deps import db
    return kv_run(record_admin_audit(db, action=action, entity_type="t", entity_id="e1",
                                     metadata={"k": "v"}, **kw))


def test_audit_write_failure_goes_to_db_outbox_and_is_replayed_into_chain(outbox_dir):
    from routes.deps import db
    from security.audit_outbox import flush_audit_outbox
    from security.audit_chain import verify_chain
    assert _audit("before.one") is True
    with _break("audit_logs"):
        assert _audit("lost.but.queued") is False         # contract unchanged: the write itself failed
    assert kv_count("audit_logs", {"action": "lost.but.queued"}) == 0
    row = kv_find("audit_outbox", {})
    assert row["via"] == "db" and row["record"]["action"] == "lost.but.queued"
    assert not list(outbox_dir.glob("*.jsonl"))
    # still broken → flush keeps it (no loss, attempt counted)
    with _break("audit_logs"):
        res = kv_run(flush_audit_outbox(db))
    assert res["chained"] == 0 and res["failed"] == 1 and kv_count("audit_outbox") == 1
    # recovered → appended to the chain exactly once
    res = kv_run(flush_audit_outbox(db))
    assert res["chained"] == 1 and res["pending"] == 0
    recs = kv_findall("audit_logs", {"action": "lost.but.queued"})
    assert len(recs) == 1 and recs[0]["chain_seq"] and recs[0]["outbox"]["via"] == "db"
    assert recs[0]["outbox"]["stashed_at"] <= recs[0]["outbox"]["replayed_at"]
    assert kv_run(flush_audit_outbox(db))["chained"] == 0
    v = kv_run(verify_chain(db, full=True))
    assert v["ok"] is True, v


def test_mongo_down_goes_to_local_jsonl_and_flushes_on_recovery(outbox_dir):
    from routes.deps import db
    from security.audit_outbox import flush_audit_outbox
    with _break("*"):
        assert _audit("offline.one") is False
        assert _audit("offline.two") is False
        files = list(outbox_dir.glob("outbox-*.jsonl"))
        assert len(files) == 1
        lines = [json.loads(x) for x in files[0].read_text().splitlines()]
        assert [x["record"]["action"] for x in lines] == ["offline.one", "offline.two"]
        # Mongo still down → flush cannot drain; the file stays
        res = kv_run(flush_audit_outbox(db))
        assert res["ok"] is False and files[0].exists()
    res = kv_run(flush_audit_outbox(db))
    assert res["from_local"] == 2 and res["chained"] == 2
    assert not list(outbox_dir.glob("*.jsonl"))
    got = sorted(r["action"] for r in kv_findall("audit_logs", {"outbox.via": "local"}))
    assert got == ["offline.one", "offline.two"]
    assert kv_count("audit_outbox") == 0


def test_local_outbox_is_bounded(outbox_dir, monkeypatch):
    from security import audit_outbox as ob
    monkeypatch.setenv("AUDIT_OUTBOX_MAX_BYTES", "1500")
    monkeypatch.setitem(ob._STATS, "local_dropped", 0)     # process counter restored after the test
    dropped0 = ob._STATS["local_dropped"]
    with _break("*"):
        for i in range(20):
            _audit(f"flood.{i}")
    total = sum(p.stat().st_size for p in outbox_dir.glob("*.jsonl"))
    assert 0 < total <= 1500
    assert ob._STATS["local_dropped"] - dropped0 >= 10
    from routes.deps import db
    from security import selftest
    st, detail = kv_run(selftest._c_audit_outbox(db))
    assert st == "fail" and "düştü" in detail


def test_critical_actions_stay_fail_closed_without_outbox(outbox_dir):
    from fastapi import HTTPException
    from activity_audit import run_audited
    from routes.deps import db
    performed = []

    async def _perform():
        performed.append(1)
        return "done"
    with _break("audit_logs"):
        with pytest.raises(HTTPException) as ei:
            kv_run(run_audited(db, action="critical.thing", entity_type="t", entity_id="x", perform=_perform))
    assert ei.value.status_code == 503 and not performed
    assert kv_count("audit_outbox") == 0 and not list(outbox_dir.glob("*.jsonl"))


def test_flush_job_registered():
    import inspect
    import scheduler
    src = inspect.getsource(scheduler.start_scheduler)
    assert 'flush_audit_outbox, "interval", minutes=5, id="audit_outbox_flush"' in src
