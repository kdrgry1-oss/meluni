"""ADM-13 / PRIV04 — a NON-deleted customer's marketing opt-out survives a database restore.

Nightly `archive_marketing_suppressions()` writes the opt-out list (HMAC'd e-mails only) to the
private R2 archive as `kvkk-suppression/<date>.json`. After a restore from an older backup,
`reapply_marketing_suppressions()` (restore-finalize hook) re-applies every archived opt-out the DB
no longer shows — unless the customer explicitly opted in again AFTER the opt-out (their latest
choice wins, CLAUDE.md "kullanıcı değişikliği kalıcıdır").
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import kv_run
from kv_harness import kv_find, kv_findall, kv_insert, kv_r2  # noqa: F401

UNSUB = "/api/email-marketing/unsubscribe"


def _iso(days=0.0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _opted_in(kv_users, email, days=-30):
    u = kv_users.customer(email=email, accepts_marketing=True, marketing_consent_at=_iso(days))
    sub = kv_insert("newsletter_subscribers", {"id": uuid.uuid4().hex, "email": email, "active": True,
                                               "consent": True, "consent_at": _iso(days), "created_at": _iso(days)})
    kv_insert("iys_consents", {"id": uuid.uuid4().hex, "email": email, "phone": "", "channels": ["EPOSTA"],
                               "status": "ONAY", "source": "HS_WEB", "consent_date": _iso(days),
                               "created_at": _iso(days), "reported": True})
    return u, sub


def _audience_emails():
    from routes.email_marketing import _campaign_audience
    return {r["email"] for r in kv_run(_campaign_audience())["rows"]}


def test_optout_reapplied_after_restore_and_newer_optin_kept(kv_client, kv_users, kv_r2):
    from routes import email_marketing as em
    from routes.deps import db
    # U: opts out through the real unsubscribe endpoint
    u, u_sub = _opted_in(kv_users, "u.optout@example.com")
    r = kv_client.post(UNSUB, data={"e": u["email"], "t": u_sub["id"]})
    assert r.status_code == 200
    # V: hard bounce → technical suppression only (consent fields untouched)
    _opted_in(kv_users, "v.bounce@example.com")
    kv_run(em._suppress("v.bounce@example.com", "bounce", "ses:permanent"))
    # W: opted out (profile) — will re-opt-in after the archive
    w, _ = _opted_in(kv_users, "w.again@example.com")
    kv_run(em._apply_unsubscribe("w.again@example.com"))
    # X: never opted out
    _opted_in(kv_users, "x.happy@example.com")
    # D: KVKK-deleted user is not part of the suppression archive (tombstones handle it)
    kv_users.customer(email="d.deleted@example.com", accepts_marketing=False, kvkk_deleted=True,
                      marketing_unsubscribed_at=_iso(-2))

    res = kv_run(em.archive_marketing_suppressions())
    assert res["ok"] is True and res["count"] == 3
    key = res["key"]
    assert key == f"kvkk-suppression/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    raw, _ = kv_r2.bucket("kv-archive")[key]
    body = raw.decode()
    for e in ("u.optout@", "v.bounce@", "w.again@", "x.happy@", "d.deleted@", "example.com"):
        assert e not in body                       # HMAC only, no plaintext address
    payload = json.loads(body)
    assert payload["count"] == 3 and all(set(x) == {"h", "at", "k", "r"} for x in payload["entries"])

    # ── DB restored from an OLD backup: opt-outs gone, everybody "opted in" again ──
    async def _restore():
        await db.users.update_many({"email": {"$in": ["u.optout@example.com", "w.again@example.com"]}},
                                   {"$set": {"accepts_marketing": True}, "$unset": {"marketing_unsubscribed_at": ""}})
        await db.newsletter_subscribers.update_many({}, {"$set": {"active": True},
                                                         "$unset": {"unsubscribed_at": "", "suppressed_at": "",
                                                                    "suppressed_reason": ""}})
        await db.iys_consents.delete_many({"status": "RET"})
        await db.email_suppressions.delete_many({})
        # W, after the opt-out, explicitly opted in again (newer than the archived opt-out time)
        await db.users.update_one({"email": "w.again@example.com"}, {"$set": {"marketing_consent_at": _iso(1)}})
    kv_run(_restore())
    assert {"u.optout@example.com", "v.bounce@example.com"} <= _audience_emails()   # the danger

    stats = kv_run(em.reapply_marketing_suppressions())
    assert stats["ok"] is True and stats["archive_key"] == key and stats["matched"] == 3
    assert stats["kept_newer_optin"] == 1 and stats["suppressions_restored"] == 1

    usr = kv_find("users", {"email": "u.optout@example.com"})
    assert usr["accepts_marketing"] is False and usr["marketing_optout_reapplied_at"]
    assert kv_find("newsletter_subscribers", {"email": "u.optout@example.com"})["active"] is False
    ret = kv_findall("iys_consents", {"email": "u.optout@example.com", "status": "RET"})
    assert len(ret) == 1 and ret[0]["restore_reapply"] is True and ret[0]["reported"] is False
    assert kv_find("email_suppressions", {"email": "v.bounce@example.com"})["reason"] == "bounce"
    # bounce-only: consent flags are NOT rewritten (technical suppression, not a consent decision)
    assert kv_find("users", {"email": "v.bounce@example.com"})["accepts_marketing"] is True
    # newer explicit opt-in is respected; untouched customers stay untouched
    assert kv_find("users", {"email": "w.again@example.com"})["accepts_marketing"] is True
    assert kv_find("users", {"email": "x.happy@example.com"})["accepts_marketing"] is True
    aud = _audience_emails()
    assert "u.optout@example.com" not in aud and "v.bounce@example.com" not in aud
    assert {"w.again@example.com", "x.happy@example.com"} <= aud
    # idempotent
    again = kv_run(em.reapply_marketing_suppressions())
    assert again["users_optout_reapplied"] == 0 and again["iys_ret_reapplied"] == 0
    assert again["suppressions_restored"] == 0
    assert kv_findall("integration_logs", {"action": "marketing_suppression_reapply"})


def test_reapply_without_archive_is_reported_not_crashing(kv_r2):
    from routes import email_marketing as em
    stats = kv_run(em.reapply_marketing_suppressions())
    assert stats["ok"] is False and stats["error"] == "archive_missing"


def test_archive_disabled_is_reported(monkeypatch):
    from routes import email_marketing as em
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_enabled", lambda: False)
    assert kv_run(em.archive_marketing_suppressions()) == {"ok": False, "error": "archive_disabled"}


def test_selftest_check_and_scheduler_registration(kv_r2):
    import inspect
    import scheduler
    from routes import email_marketing as em
    from routes.deps import db
    from security import selftest
    st, _ = kv_run(selftest._c_suppression_archive(db))
    assert st == "warn"
    kv_run(em.archive_marketing_suppressions())
    st, detail = kv_run(selftest._c_suppression_archive(db))
    assert st == "ok", detail
    src = inspect.getsource(scheduler.start_scheduler)
    assert 'archive_marketing_suppressions, "cron", hour=3, minute=37, id="kvkk_suppression_archive"' in src
