"""Scheduler wiring (ADM-02 system identity + new security jobs) — no frequency changes.

`start_scheduler._add` wraps every job as `_lead(_outbound_guard(as_system_job(fn)))`:
  * the wrapper keeps __name__/__qualname__ (outbound-kind lookup, RUNNING_JOBS, lead-retry id);
  * the outbound kill switch still resolves for marketplace jobs;
  * audit records written inside a job carry actor.id = "system:<job id>".
A fake APScheduler records registrations, so nothing is started.
"""
from __future__ import annotations

import pytest

from conftest import kv_run
from kv_harness import kv_findall


class _FakeJob:
    def __init__(self, func, args, kw):
        self.func, self.args, self.kw = func, args, kw
        self.id = kw.get("id")


class _FakeScheduler:
    def __init__(self, *a, **k):
        self.jobs = {}

    def add_job(self, func, *a, **k):
        self.jobs[k.get("id") or getattr(func, "__name__", "?")] = _FakeJob(func, a, k)

    def start(self):
        pass

    def get_jobs(self):
        return list(self.jobs.values())


@pytest.fixture
def fake_sched(monkeypatch):
    import scheduler
    from activity_audit import record_admin_audit
    from routes.deps import db
    calls = []

    def _mk(name):
        async def _job(*a, **k):
            calls.append(name)
            await record_admin_audit(db, action=f"test.{name}", entity_type="job", entity_id=name)
        _job.__name__ = name
        _job.__qualname__ = name
        return _job
    for name in ("_marketplace_sync_tick", "_run_private_archive", "_run_r2_files_migration"):
        if hasattr(scheduler, name):
            monkeypatch.setattr(scheduler, name, _mk(name))
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", _FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_lead", lambda fn: fn)     # inspect what _lead receives

    async def _not_paused(kind="marketplace"):
        return False
    monkeypatch.setattr(scheduler, "_outbound_paused", _not_paused)
    sch = scheduler.start_scheduler()
    return sch, calls


def _job_by_func_name(sch, fname):
    return next(j for j in sch.jobs.values() if getattr(j.func, "__name__", "") == fname)


def test_wrapped_jobs_keep_name_kind_and_system_actor(fake_sched, monkeypatch):
    import scheduler
    sch, calls = fake_sched
    # marketplace job (outbound-guarded), archive job, one-time date job
    for fname, jid, kind in (("_marketplace_sync_tick", "marketplace_auto_sync_tick", "marketplace"),
                             ("_run_private_archive", None, None),
                             ("_run_r2_files_migration", "r2_files_migration_once", None)):
        job = _job_by_func_name(sch, fname)
        assert job.func.__name__ == fname and job.func.__qualname__ == fname
        assert scheduler._OUTBOUND_JOB_KINDS.get(job.func.__name__) == kind
        kv_run(job.func())
        rec = kv_findall("audit_logs", {"action": f"test.{fname}"})[-1]
        assert rec["actor"]["id"] == f"system:{job.id}", rec["actor"]
    assert _job_by_func_name(sch, "_run_r2_files_migration").args[0] == "date"
    # outbound kill switch still applies to the (wrapped) marketplace job
    n = len(calls)

    async def _paused(kind="marketplace"):
        return True
    monkeypatch.setattr(scheduler, "_outbound_paused", _paused)
    kv_run(_job_by_func_name(sch, "_marketplace_sync_tick").func())
    assert len(calls) == n


def test_heartbeat_not_wrapped_and_new_security_jobs_registered(fake_sched):
    import scheduler
    sch, _ = fake_sched
    hb = sch.jobs["scheduler_leader_heartbeat"]
    assert hb.func is scheduler._leader_heartbeat
    expected = {"integration_freshness_check": ("interval", {"minutes": 15}),
                "storefront_integrity_watch": ("interval", {"minutes": 15}),
                "audit_outbox_flush": ("interval", {"minutes": 5}),
                "security_pipeline_check": ("interval", {"minutes": 5}),
                "iyzico_daily_reconciliation": ("cron", {"hour": 4, "minute": 23}),
                "kvkk_suppression_archive": ("cron", {"hour": 3, "minute": 37})}
    for jid, (trig, kw) in expected.items():
        j = sch.jobs[jid]
        assert j.args[0] == trig and all(j.kw.get(k) == v for k, v in kw.items()), (jid, j.args, j.kw)
