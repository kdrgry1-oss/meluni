"""Admin operations hardening.

ADM-01 invite edge cases · ADM-02 explicit system identity in audit · ADM-10 vault reveal alert + masking ·
ADM-11/CHAIN12 restore finalize (epoch, KVKK tombstones, staff revocations from the R2 audit archive) and the
boot break-glass alert · ADM-16 routine support cannot change customer block / risk rules · PRIV07 bulk
decryption alarm · SOC-01/SOC-02 security pipeline outage detection.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from freezegun import freeze_time

from id_harness import (ID_PASSWORD, id_seed_user, id_seed_role, id_auth, id_ip, id_find, id_findall, id_token,
                        id_audit_actions, id_run, id_update, id_set_rule)
from routes import deps
from permissions import upgrade_grants_for

pytestmark = pytest.mark.usefixtures("id_env")


def _actions(prefix=""):
    return id_audit_actions(prefix)


# ─────────────────────────────── ADM-01 ───────────────────────────────

@pytest.fixture
def inviter():
    id_seed_role("r_ops3", ["orders.view"], "Operasyon")
    return id_seed_user(kind="super", email="inviter3@example.test")


def _invite(client, sa, email="edge@example.test", role_id="r_ops3"):
    r = client.post("/api/admin/users/invite", json={"email": email, "role_id": role_id}, headers=id_auth(id_token(sa)))
    assert r.status_code == 200, r.text
    return r.json()["invite"]


def _tok(outbox, email):
    m = [x for x in outbox.emails if x["to"] == email][-1]
    return re.search(r"/admin/davet#([A-Za-z0-9_\-]{20,})", m["html"]).group(1)


def test_invite_accept_ignores_injected_identity_and_privilege(id_client, inviter, id_outbox):
    _invite(id_client, inviter)
    tok = _tok(id_outbox, "edge@example.test")
    secret = id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=id_ip("198.51.100.200")).json()["secret"]
    r = id_client.post("/api/auth/invite/accept",
                       json={"token": tok, "password": "Davetli!Guclu-Sifre9", "code": pyotp.TOTP(secret).now(),
                             "email": "attacker@example.test", "role_id": "r_all", "is_super_admin": True,
                             "user_id": "u-attacker"}, headers=id_ip("198.51.100.200"))
    assert r.status_code == 200, r.text
    u = id_find("users", {"email": "edge@example.test"})
    assert u["role_id"] == "r_ops3" and not u.get("is_super_admin") and u["id"] != "u-attacker"
    assert not id_find("users", {"email": "attacker@example.test"})
    # a completed invite cannot be resent into a second membership
    inv = id_find("staff_invites", {"email": "edge@example.test"})
    r = id_client.post(f"/api/admin/users/invites/{inv['id']}/resend", headers=id_auth(id_token(inviter)))
    assert r.status_code in (400, 404)
    assert len(id_findall("users", {"email": "edge@example.test"})) == 1


def test_revoked_invite_and_deleted_role_never_create_an_account(id_client, inviter, id_outbox):
    inv = _invite(id_client, inviter, email="revoked@example.test")
    tok = _tok(id_outbox, "revoked@example.test")
    secret = id_client.post("/api/auth/invite/mfa-start", json={"token": tok}, headers=id_ip("198.51.100.201")).json()["secret"]
    assert id_client.delete(f"/api/admin/users/invites/{inv['id']}", headers=id_auth(id_token(inviter))).status_code == 200
    r = id_client.post("/api/auth/invite/accept", json={"token": tok, "password": "Davetli!Guclu-Sifre9",
                                                        "code": pyotp.TOTP(secret).now()}, headers=id_ip("198.51.100.201"))
    assert r.status_code in (400, 410) and "token" not in r.json()
    assert not id_find("users", {"email": "revoked@example.test"})
    # role deleted between invite and acceptance → refused, no account
    id_seed_role("r_tmp", ["orders.view"], "Geçici")
    _invite(id_client, inviter, email="norole@example.test", role_id="r_tmp")
    tok2 = _tok(id_outbox, "norole@example.test")
    s2 = id_client.post("/api/auth/invite/mfa-start", json={"token": tok2}, headers=id_ip("198.51.100.202")).json()["secret"]
    id_run(deps.db.roles.delete_one({"id": "r_tmp"}))
    r = id_client.post("/api/auth/invite/accept", json={"token": tok2, "password": "Davetli!Guclu-Sifre9",
                                                        "code": pyotp.TOTP(s2).now()}, headers=id_ip("198.51.100.202"))
    assert r.status_code == 409
    assert not id_find("users", {"email": "norole@example.test"})
    # an e-mail that already has an account cannot be taken over through an invite
    id_seed_user(kind="customer", email="exists@example.test")
    r = id_client.post("/api/admin/users/invite", json={"email": "exists@example.test", "role_id": "r_ops3"},
                       headers=id_auth(id_token(inviter)))
    assert r.status_code == 400 and not id_find("staff_invites", {"email": "exists@example.test"})
    # … and if the account appears AFTER the invite was sent, acceptance is refused too
    _invite(id_client, inviter, email="late@example.test")
    tok3 = _tok(id_outbox, "late@example.test")
    s3 = id_client.post("/api/auth/invite/mfa-start", json={"token": tok3}, headers=id_ip("198.51.100.203")).json()["secret"]
    id_seed_user(kind="customer", email="late@example.test")
    r = id_client.post("/api/auth/invite/accept", json={"token": tok3, "password": "Davetli!Guclu-Sifre9",
                                                        "code": pyotp.TOTP(s3).now()}, headers=id_ip("198.51.100.203"))
    assert r.status_code == 409
    assert id_find("users", {"email": "exists@example.test"})["is_admin"] is False
    assert [u["is_admin"] for u in id_findall("users", {"email": "late@example.test"})] == [False]


def test_non_super_staff_cannot_invite_or_manage_invites(id_client, inviter, id_outbox):
    id_seed_role("r_hr", ["orders.view", "customers.view", "settings.site"], "İK")
    staff = id_seed_user(kind="staff", role_id="r_hr", email="hr@example.test")
    h = id_auth(id_token(staff))
    assert id_client.post("/api/admin/users/invite", json={"email": "x@example.test", "role_id": "r_ops3"},
                          headers=h).status_code == 403
    inv = _invite(id_client, inviter, email="managed@example.test")
    assert id_client.get("/api/admin/users/invites", headers=h).status_code == 403
    assert id_client.post(f"/api/admin/users/invites/{inv['id']}/resend", headers=h).status_code == 403
    assert id_client.delete(f"/api/admin/users/invites/{inv['id']}", headers=h).status_code == 403
    assert id_find("staff_invites", {"id": inv["id"]})["status"] == "pending"


# ─────────────────────────────── ADM-02 ───────────────────────────────

def test_system_actions_carry_explicit_system_identity():
    from activity_audit import record_admin_audit, as_system_job
    from routes.settings import promote_due_bank_changes
    # scheduler job without a user: IBAN cooling-off promotion
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    id_run(deps.db.settings.insert_one({"id": "payment", "bank_accounts": [], "bank_accounts_pending": [
        {"account": {"id": "b1", "bank_name": "B", "iban": "TR330006100519786457841326"}, "pending_activation_at": past}]}))
    assert id_run(promote_due_bank_changes()) == 1
    rec = [r for r in id_findall("audit_logs") if r["action"] == "settings.bank_account_activate"][0]
    assert rec["actor"]["id"] == "system:iban_cooling_off" and rec["actor"]["role"] == "system"

    # a job wrapped for the scheduler gets its own name
    async def nightly_cleanup():
        await record_admin_audit(deps.db, action="test.system_job", entity_type="x", entity_id="1",
                                 source="scheduler")
    id_run(as_system_job(nightly_cleanup)())
    rec = [r for r in id_findall("audit_logs") if r["action"] == "test.system_job"][0]
    assert rec["actor"]["id"] == "system:nightly_cleanup"
    # security events raised by the system
    from security.events import security_event
    id_run(security_event("audit_export_failed", None, {"error": "x", "fp": "x"}, alert=False))
    rec = [r for r in id_findall("audit_logs") if r["action"] == "security.audit_export_failed"][0]
    assert rec["actor"]["id"] == "system:security"
    # human actions keep the human id
    sa = id_seed_user(kind="super")
    id_run(record_admin_audit(deps.db, action="test.human", entity_type="x", entity_id="1", current_user=sa))
    assert [r for r in id_findall("audit_logs") if r["action"] == "test.human"][0]["actor"]["id"] == sa["id"]
    assert not [r for r in id_findall("audit_logs") if not r.get("actor", {}).get("id")]


# ─────────────────────────────── ADM-10 ───────────────────────────────

def test_vault_values_masked_reveal_alerts_and_never_logged(id_client, caplog):
    caplog.set_level(logging.DEBUG)
    id_seed_role("r_view", ["settings.site"])
    staff = id_seed_user(kind="staff", role_id="r_view")
    sa = id_seed_user(kind="super", email="vault-owner@example.test")
    secret = "sk_live_" + "Q7v" * 10
    r = id_client.post("/api/admin/vault/secret", json={"key": "stripe_like", "value": secret, "scope": "test"},
                       headers=id_auth(id_token(sa)))
    assert r.status_code == 200 and secret not in r.text and r.json()["masked"].endswith(secret[-4:])
    for who in (staff, sa):
        lst = id_client.get("/api/admin/vault/secrets", headers=id_auth(id_token(who)))
        assert lst.status_code == 200 and secret not in lst.text
        assert lst.json()["items"][0]["masked_value"].endswith(secret[-4:])
    assert lst.json()["can_reveal"] is True
    r = id_client.get("/api/admin/vault/secret/stripe_like/reveal", headers=id_auth(id_token(staff)))
    assert r.status_code == 403 and secret not in r.text
    assert id_findall("auth_audit_logs", {"event": "vault_secret_reveal_denied"})
    r = id_client.get("/api/admin/vault/secret/stripe_like/reveal", headers=id_auth(id_token(sa)))
    assert r.status_code == 200 and r.json()["value"] == secret
    alerts = id_findall("alerts", {"kind": "vault_reveal"})
    assert alerts and alerts[0]["level"] == "critical"
    assert {"vault.secret_reveal", "vault.secret_write"} <= set(_actions("vault."))
    stored = id_find("vault_secrets", {"key": "stripe_like"})
    assert secret not in repr(stored)
    for coll in ("audit_logs", "auth_audit_logs", "alerts", "pii_reveal_counters"):
        assert secret not in repr(id_findall(coll)), coll
    assert secret not in "\n".join(r.getMessage() for r in caplog.records)


# ─────────────────────────────── ADM-11 / CHAIN12 ───────────────────────────────

def _prime_export_state():
    # mongomock quirk (see test_identity_audit_chain): find_one_and_update returns None instead of {} when
    # every projected field is absent, which the export lock reads as "locked". Seed the export counter.
    if "exported_seq" not in (id_find("settings", {"_id": "audit_chain"}) or {}):
        id_update("settings", {"_id": "audit_chain"}, {"$set": {"exported_seq": 0}})


def _snapshot():
    async def _s():
        out = {}
        for name in await deps.db.list_collection_names():
            out[name] = await deps.db[name].find({}).to_list(100000)
        return out
    return id_run(_s())


def _restore(snap):
    async def _r():
        for name in await deps.db.list_collection_names():
            await deps.db.drop_collection(name)
        for name, docs in snap.items():
            if docs:
                await deps.db[name].insert_many([dict(d) for d in docs])
    id_run(_r())
    deps._EPOCH_CACHE.update({"value": 0, "bumped_at": 0.0, "loaded_at": 0.0})
    import security_switches
    security_switches._cache.update({"at": 0.0, "switches": None})


def test_restore_finalize_reapplies_revocations_epoch_and_tombstones(id_client, monkeypatch):
    from security.audit_chain import export_batch_to_archive
    from activity_audit import record_admin_audit
    import routes.kvkk as kvkk
    calls = []
    real_reapply = kvkk.reapply_deletion_tombstones

    async def _spy():
        calls.append(1)
        return await real_reapply()
    monkeypatch.setattr(kvkk, "reapply_deletion_tombstones", _spy)
    import routes.email_marketing as em
    mk_calls = []

    async def _mk_spy(archive_key=None):   # archive mocked: the function has its own tests
        mk_calls.append(archive_key)
        return {"ok": True, "entries": 2, "users_optout_reapplied": 1, "archive_key": "x"}
    monkeypatch.setattr(em, "reapply_marketing_suppressions", _mk_spy)
    id_seed_role("r_ops4", ["orders.view"], "Ops")
    sa = id_seed_user(kind="super", email="restore-owner@example.test")
    leaver = id_seed_user(kind="staff", role_id="r_ops4", email="leaver@example.test")
    returner = id_seed_user(kind="staff", role_id="r_ops4", email="returner@example.test")
    deleted = id_seed_user(kind="staff", role_id="r_ops4", email="deleted@example.test")
    old_story = id_seed_user(kind="staff", role_id="r_ops4", email="old-story@example.test")
    H = lambda: id_auth(id_token(sa))  # noqa: E731
    with freeze_time("2026-09-01 08:00:00"):
        # an old deactivation that was later undone OUTSIDE rbac — before the snapshot, must not be replayed
        id_client.put(f"/api/admin/users/{old_story['id']}", json={"is_active": False}, headers=H())
        id_update("users", {"id": old_story["id"]}, {"$set": {"is_active": True}})
    with freeze_time("2026-09-01 12:00:00"):
        id_run(record_admin_audit(deps.db, action="test.snapshot_marker", entity_type="x", entity_id="1",
                                  current_user=sa))
        snap = _snapshot()                       # ← the backup that will be restored
    with freeze_time("2026-09-02 10:00:00"):
        assert id_client.put(f"/api/admin/users/{leaver['id']}", json={"is_active": False}, headers=H()).status_code == 200
        assert id_client.put(f"/api/admin/users/{returner['id']}", json={"is_active": False}, headers=H()).status_code == 200
    with freeze_time("2026-09-02 11:00:00"):
        assert id_client.put(f"/api/admin/users/{returner['id']}", json={"is_active": True}, headers=H()).status_code == 200
        assert id_client.delete(f"/api/admin/users/{deleted['id']}", headers=H()).status_code == 200
        _prime_export_state()
        res = id_run(export_batch_to_archive(deps.db))
        assert res.get("ok") and res.get("written", 0) > 0, res
    # disaster → database restored from the 2026-09-01 12:00 backup (the R2 archive survives)
    _restore(snap)
    assert id_find("users", {"id": leaver["id"]})["is_active"] is True
    assert id_find("users", {"id": deleted["id"]})
    with freeze_time("2026-09-03 09:00:00") as frozen:
        leaver_tok = id_token(id_find("users", {"id": leaver["id"]}))
        sa_tok = id_token(sa)
        r = id_client.post("/api/admin/security-switches/restore_mode", json={"on": True, "reason": "DB restore drill"},
                           headers=id_auth(sa_tok))
        assert r.status_code == 200
        frozen.tick(timedelta(minutes=30))
        assert id_client.get("/api/admin/me/permissions", headers=id_auth(leaver_tok)).status_code == 200
        r = id_client.post("/api/admin/security-switches/restore_mode", json={"on": False}, headers=id_auth(sa_tok))
        assert r.status_code == 200, r.text
        fin = r.json()["restore_finalize"]
        frozen.tick(timedelta(seconds=5))
        # 1) everybody is out (old tokens dead), the caller got a fresh token
        assert id_client.get("/api/admin/me/permissions", headers=id_auth(sa_tok)).status_code == 401
        assert id_client.get("/api/admin/me/permissions", headers=id_auth(r.json()["token"])).status_code == 200
        assert fin["epoch"] >= 1
    # 2) staff revocations made after the backup are re-applied; later re-activation respected
    assert id_find("users", {"id": leaver["id"]})["is_active"] is False
    assert id_find("users", {"id": deleted["id"]})["is_active"] is False
    assert id_find("users", {"id": returner["id"]})["is_active"] is True
    assert id_find("users", {"id": old_story["id"]})["is_active"] is True
    assert sorted(fin["staff"]["redeactivated_ids"]) == sorted([leaver["id"], deleted["id"]])
    assert id_find("users", {"id": leaver["id"]})["token_version"] >= 1
    recs = [x for x in id_findall("audit_logs") if x["action"] == "rbac.restore_reapply_deactivation"]
    assert {x["entity"]["id"] for x in recs} == {leaver["id"], deleted["id"]}
    assert all(x["actor"]["id"] == sa["id"] for x in recs)
    # 3) KVKK tombstones re-applied + critical alert + audit
    assert calls == [1]
    assert mk_calls == [None] and fin["marketing"]["users_optout_reapplied"] == 1
    assert id_findall("alerts", {"kind": "restore_finalized"})[0]["level"] == "critical"
    assert "security.restore_finalized" in _actions("security.")


def test_turning_restore_mode_on_or_other_switches_does_not_finalize(id_client):
    sa = id_seed_user(kind="super", email="switch-owner@example.test")
    h = id_auth(id_token(sa))
    r = id_client.post("/api/admin/security-switches/freeze_refunds", json={"on": True, "reason": "drill 123"}, headers=h)
    assert r.status_code == 200 and "restore_finalize" not in r.json()
    r = id_client.post("/api/admin/security-switches/freeze_refunds", json={"on": False}, headers=h)
    assert "restore_finalize" not in r.json()
    assert id_client.get("/api/admin/me/permissions", headers=h).status_code == 200  # no epoch bump


def test_break_glass_boot_alert(monkeypatch):
    import security.events as ev
    monkeypatch.setattr(ev, "_BOOT_ALERT_SENT", False)
    id_run(ev.alert_break_glass_on_boot())   # nothing active → no alert
    assert not id_findall("alerts", {"kind": "break_glass_active"})
    monkeypatch.setattr(ev, "_BOOT_ALERT_SENT", False)
    monkeypatch.setenv("ADMIN_MFA_ENFORCE", "off")
    monkeypatch.setenv("TWO_PERSON_RULE", "off")
    id_seed_user(kind="super", email="bg-owner@example.test")
    id_run(ev.alert_break_glass_on_boot())
    alerts = id_findall("alerts", {"kind": "break_glass_active"})
    assert len(alerts) == 1 and alerts[0]["level"] == "critical"
    assert "ADMIN_MFA_ENFORCE=off" in alerts[0]["body"] and "TWO_PERSON_RULE=off" in alerts[0]["body"]
    rec = [r for r in id_findall("audit_logs") if r["action"] == "security.break_glass_active"]
    assert rec and rec[0]["actor"]["id"] == "system:security"
    id_run(ev.alert_break_glass_on_boot())   # once per boot
    assert len(id_findall("alerts", {"kind": "break_glass_active"})) == 1


# ─────────────────────────────── ADM-16 ───────────────────────────────

def test_routine_support_cannot_change_block_or_risk_rules(id_client):
    _support = ["customers.view", "customers.edit", "orders.view", "orders.edit", "returns.view", "settings.site"]
    # IAM-02: rol, açılıştaki tek seferlik izin geçişinden geçmiş hâliyle (permissions.upgrade_grants_for).
    # Denetim #3: settings.edit hassas izin — otomatik verilmez; bu rolde süper admin bilinçli vermiş.
    id_seed_role("r_support", _support + upgrade_grants_for(_support) + ["settings.edit"], "Destek")
    support = id_seed_user(kind="staff", role_id="r_support", email="support@example.test")
    sa = id_seed_user(kind="super", email="risk-owner@example.test")
    hs, ha = id_auth(id_token(support)), id_auth(id_token(sa))
    r = id_client.post("/api/customer-risk/block", json={"email": "fraud@example.test", "reason": "x"}, headers=ha)
    assert r.status_code == 200
    bid = r.json()["block"]["id"]
    # support can neither unblock nor add blocks
    assert id_client.delete(f"/api/customer-risk/blocked/{bid}", headers=hs).status_code == 403
    assert id_find("blocked_customers", {"id": bid})["active"] is True
    assert id_client.post("/api/customer-risk/block", json={"email": "x@example.test"}, headers=hs).status_code == 403
    assert len(id_findall("blocked_customers")) == 1
    # IP blocklist is super-admin only
    assert id_client.post("/api/admin/security/ip-blocklist", json={"ip": "203.0.113.99"}, headers=hs).status_code == 403
    assert id_client.delete("/api/admin/security/ip-blocklist/203.0.113.99", headers=hs).status_code == 403
    # payment / order risk rules cannot be relaxed by routine staff (fail-closed audited for super admin)
    for key, val in (("payment.allow_non3ds_card_pay", True), ("payment.card_velocity_max_fails", 20),
                     ("order.enforce_total_match", False), ("order.block_oversell", False)):
        r = id_client.put("/api/admin/business-rules", json={"values": {key: val}}, headers=hs)
        assert r.status_code == 403, key
    rules = (id_find("settings", {"id": "business_rules"}) or {}).get("values") or {}
    assert "payment.allow_non3ds_card_pay" not in rules
    # ordinary operational rules are still editable by staff
    r = id_client.put("/api/admin/business-rules", json={"values": {"return.window_days": 15}}, headers=hs)
    assert r.status_code == 200
    r = id_client.put("/api/admin/business-rules", json={"values": {"payment.card_velocity_max_fails": 10}}, headers=ha)
    assert r.status_code == 200
    assert "business_rules.security_update" in _actions("business_rules.")
    # the limited risk view does not dump customer records
    r = id_client.get("/api/customer-risk/bulk?emails=fraud@example.test", headers=hs)
    assert r.status_code == 200 and set(r.json()["risks"]["fraud@example.test"]) <= {
        "total_orders", "returned", "return_rate", "return_rate_pct", "risk_level", "suspicious"}


# ─────────────────────────────── PRIV07 ───────────────────────────────

def test_bulk_reveal_alarm_blocks_actor_for_an_hour(monkeypatch):
    from fastapi import HTTPException
    from security.events import note_pii_reveal, pii_reveal_allowed
    staff = id_seed_user(kind="super", email="bulk@example.test")
    other = id_seed_user(kind="super", email="calm@example.test")
    with freeze_time("2026-10-04 13:00:00") as frozen:
        for i in range(25):
            id_run(note_pii_reveal(staff, "refund_iban", 2))      # 50 reveals: allowed
        assert id_run(pii_reveal_allowed(staff)) is True
        with pytest.raises(HTTPException) as ei:
            id_run(note_pii_reveal(staff, "tax_id", 1))           # 51st → blocked
        assert ei.value.status_code == 429
        alerts = id_findall("alerts", {"kind": "pii_bulk_reveal"})
        assert len(alerts) == 1 and alerts[0]["level"] == "critical"
        assert "security.pii_bulk_reveal" in _actions("security.")
        # further reveals refused for an hour (no new alert storm); other staff unaffected
        frozen.tick(timedelta(minutes=30))
        with pytest.raises(HTTPException):
            id_run(note_pii_reveal(staff, "tax_id", 1))
        assert len(id_findall("alerts", {"kind": "pii_bulk_reveal"})) == 1
        assert id_run(note_pii_reveal(other, "tax_id", 1)) == 1
        frozen.tick(timedelta(minutes=31))
        assert id_run(pii_reveal_allowed(staff)) is True
        assert id_run(note_pii_reveal(staff, "tax_id", 1)) == 1  # new window


def test_bulk_reveal_through_vault_endpoint_and_fail_closed(id_client, monkeypatch):
    from security.crypto import encrypt
    monkeypatch.setenv("PII_REVEAL_HOURLY_MAX", "3")
    sa = id_seed_user(kind="super", email="vault-bulk@example.test")
    for i in range(5):
        id_run(deps.db.vault_secrets.insert_one({"key": f"k{i}", "value_enc": encrypt(f"value-{i}-xyz")}))
    h = id_auth(id_token(sa))
    codes = [id_client.get(f"/api/admin/vault/secret/k{i}/reveal", headers=h).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert id_findall("alerts", {"kind": "pii_bulk_reveal"})
    # counter store down → no plaintext (fail-closed)
    sa2 = id_seed_user(kind="super", email="vault-bulk2@example.test")
    coll_cls = type(deps.db.pii_reveal_counters)
    orig = coll_cls.find_one_and_update

    async def _boom(self, *a, **k):
        if self.name == "pii_reveal_counters":
            raise RuntimeError("down")
        return await orig(self, *a, **k)
    monkeypatch.setattr(coll_cls, "find_one_and_update", _boom)
    r = id_client.get("/api/admin/vault/secret/k0/reveal", headers=id_auth(id_token(sa2)))
    assert r.status_code == 503 and "value-0" not in r.text


# ─────────────────────────────── SOC-01 / SOC-02 ───────────────────────────────

def test_pipeline_check_ok_then_detects_export_lag_delivery_failure_and_gap(monkeypatch):
    from security.selftest import check_security_pipeline, run_selftest
    from security.audit_chain import export_batch_to_archive
    from activity_audit import record_admin_audit
    from services import r2_storage
    sa = id_seed_user(kind="super", email="soc@example.test")
    with freeze_time("2026-10-04 14:00:00") as frozen:
        id_run(record_admin_audit(deps.db, action="test.soc", entity_type="x", entity_id="1", current_user=sa))
        _prime_export_state()
        assert id_run(export_batch_to_archive(deps.db)).get("written")
        res = id_run(check_security_pipeline())
        assert res["ok"], res
        # 1) export lag: new records not archived for > 30 min
        id_run(record_admin_audit(deps.db, action="test.soc2", entity_type="x", entity_id="2", current_user=sa))
        frozen.tick(timedelta(minutes=31))
        res = id_run(check_security_pipeline())
        assert not res["ok"] and "audit_export_lag" in [p["type"] for p in res["problems"]]
        alerts = id_findall("alerts", {"kind": "security_pipeline_degraded"})
        assert len(alerts) == 1 and alerts[0]["level"] == "critical"
        # same problem inside the window → no second alert e-mail (throttled)
        id_run(check_security_pipeline())
        assert sum(1 for a in id_findall("alerts", {"kind": "security_pipeline_degraded"})
                   if not a.get("delivery_error") and a.get("delivered", {}).get("store") is not None) <= 1
        assert id_run(export_batch_to_archive(deps.db)).get("written")
        frozen.tick(timedelta(minutes=31))
        # 2) alert delivery failure in the last hour
        id_run(deps.db.alerts.insert_one({"id": "a-fail", "kind": "vault_reveal", "level": "critical",
                                          "created_at": datetime.now(timezone.utc).isoformat(),
                                          "delivery_error": "resend_http_403;store_mail:x"}))
        res = id_run(check_security_pipeline(alert=False))
        assert [p["type"] for p in res["problems"]] == ["alert_delivery_failed"]
        # teşhis: hangi uyarı türü ve sağlayıcı hatası (adres maskelenir)
        assert "vault_reveal: resend_http_403" in res["problems"][0]["detail"]
        id_run(deps.db.alerts.delete_one({"id": "a-fail"}))
        # 3) audit chain gap (a write failed after the head moved) / deleted record
        id_run(deps.db.settings.update_one({"_id": "audit_chain"}, {"$push": {"gaps": 999}}))
        res = id_run(check_security_pipeline(alert=False))
        assert "audit_chain_gap" in [p["type"] for p in res["problems"]]
        res = id_run(check_security_pipeline(alert=False))   # known now → not reported again
        assert "audit_chain_gap" not in [p["type"] for p in res["problems"]]
        # a freshly written (not yet verified) record is deleted from the database
        id_run(record_admin_audit(deps.db, action="test.soc4", entity_type="x", entity_id="4", current_user=sa))
        victim = id_find("audit_logs", {"action": "test.soc4"})
        id_run(deps.db.audit_logs.delete_one({"chain_seq": victim["chain_seq"]}))
        frozen.tick(timedelta(minutes=2))  # past the in-flight grace
        res = id_run(check_security_pipeline(alert=False, db=deps.db))
        assert "audit_chain_problem" in [p["type"] for p in res["problems"]]
        # 4) archive switched off with pending records
        monkeypatch.setattr(r2_storage, "archive_enabled", lambda: False)
        id_run(record_admin_audit(deps.db, action="test.soc3", entity_type="x", entity_id="3", current_user=sa))
        res = id_run(check_security_pipeline(alert=False))
        assert "audit_archive_disabled" in [p["type"] for p in res["problems"]]
    # the daily self-test carries the same check
    rep = id_run(run_selftest(send_email=False))
    assert any(c["name"] == "Güvenlik kayıt/uyarı hattı" and c["status"] == "fail" for c in rep["checks"])


def test_pipeline_scheduler_entry_never_raises(monkeypatch):
    from security import selftest
    async def _boom(**k):
        raise RuntimeError("x")
    monkeypatch.setattr(selftest, "check_security_pipeline", _boom)
    out = id_run(selftest.run_security_pipeline_check())
    assert out["ok"] is False


def test_mfa_verified_staff_session_is_not_blocked_on_bulk_reveal(monkeypatch):
    """Sahip kararı (2026-10-05): SMS/MFA doğrulamasıyla giren personel toplu görüntülemede ENGELLENMEZ —
    görüntülemeler sayılır, pencere başına tek bilgi uyarısı (warning, SMS yok); doğrulamasız oturum ve
    kural kapalıyken eski engel aynen sürer."""
    from routes.deps import CURRENT_STAFF_TOKEN
    from security.events import note_pii_reveal
    staff = id_seed_user(kind="staff")
    plain = id_seed_user(kind="staff")
    tok = CURRENT_STAFF_TOKEN.set({"amr": ["mfa", "sms"]})
    try:
        for i in range(30):
            id_run(note_pii_reveal(staff, "tax_id", 3))           # 90 > 50 → engel YOK
        info = id_findall("alerts", {"kind": "pii_bulk_reveal_mfa"})
        assert len(info) == 1 and info[0]["level"] == "warning"
        assert not id_findall("alerts", {"kind": "pii_bulk_reveal"})
        # kural kapatılırsa doğrulamalı oturum da engellenir
        id_set_rule("security.pii_reveal_mfa_exempt", False)
        with pytest.raises(Exception) as ei:
            id_run(note_pii_reveal(plain, "tax_id", 60))
        assert getattr(ei.value, "status_code", None) == 429
    finally:
        CURRENT_STAFF_TOKEN.reset(tok)


def test_mfa_login_token_carries_amr_but_recovery_code_does_not():
    from routes import deps
    t = deps.create_token("u1", True, amr=["mfa", "sms"])
    assert deps.jwt.decode(t, options={"verify_signature": False})["amr"] == ["mfa", "sms"]
    assert "amr" not in deps.jwt.decode(deps.create_token("u1", True), options={"verify_signature": False})
    import inspect
    from routes import mfa
    src = inspect.getsource(mfa)
    assert '_used_channel != "recovery_code"' in src and "amr=_amr" in src


def test_bulk_reveal_alert_lists_which_records_were_opened():
    """Sahip isteği (2026-10-06): 'hangi verileri açtığını da bilelim' — eşik uyarısı, açılan kayıtları
    (sipariş no + tür) listeler; kişisel verinin kendisi yazılmaz."""
    from routes.deps import CURRENT_STAFF_TOKEN
    from security.events import note_pii_reveal
    staff = id_seed_user(kind="staff")
    tok = CURRENT_STAFF_TOKEN.set({"amr": ["mfa", "sms"]})
    try:
        for i in range(26):
            id_run(note_pii_reveal(staff, "iban", 2, refs=[f"W1{i:04d}", f"W2{i:04d}"]))
        id_run(note_pii_reveal(staff, "tax_id", 1, refs=["W90001"]))
    finally:
        CURRENT_STAFF_TOKEN.reset(tok)
    a = id_findall("alerts", {"kind": "pii_bulk_reveal_mfa"})
    assert len(a) == 1
    body = a[0]["body"]
    assert "Açılan kayıtlar" in body and "W10025 — iade IBAN" in body and "W20000 — iade IBAN" in body
    assert "TR" not in body.split("Açılan kayıtlar", 1)[1]          # IBAN değeri yazılmaz
