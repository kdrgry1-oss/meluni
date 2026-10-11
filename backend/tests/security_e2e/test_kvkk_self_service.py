"""KVKK self-service e2e: data export, account deletion, tombstones, restore re-application."""
from __future__ import annotations

import json
import uuid

import pytest

from kv_harness import (kv_r2, kv_insert, kv_find, kv_findall, kv_count, kv_order, kv_json,  # noqa: F401
                        kv_all_keys, kv_set_rule, kv_iso, tr_iban)
from conftest import KvUsers, kv_run

MY_DATA = "/api/kvkk/my-data"
DELETE = "/api/kvkk/delete-request"
FULL_IBAN = tr_iban("0006200119000006672315")


def _seal(info):
    from routes.orders import seal_refund_bank_info
    return seal_refund_bank_info(info)


def _seed_person(kv_users, tag: str, *, closed_order=True, **extra):
    u = kv_users.customer(email=f"{tag}@example.com", phone=f"0555{abs(hash(tag)) % 10**7:07d}",
                          mfa_secret="JBSWY3DPEHPK3PXP", totp_secret="SECRET-TOTP", mfa_enabled=True,
                          failed_attempts=2, recovery_codes=["a", "b"], birth_date="1990-01-02",
                          height_cm=170, **extra)
    kv_insert("addresses", {"id": str(uuid.uuid4()), "user_id": u["id"], "title": f"Ev-{tag}",
                            "address": f"{tag} Sokak 1", "city": "Istanbul", "phone": u["phone"]})
    kv_insert("products", {"id": f"prod-{tag}", "name": f"Urun {tag}", "is_active": True, "price": 100,
                           "images": ["x.jpg"]})
    kv_insert("favorites", {"id": str(uuid.uuid4()), "user_id": u["id"], "product_id": f"prod-{tag}",
                            "created_at": kv_iso()})
    kv_insert("user_devices", {"id": str(uuid.uuid4()), "user_id": u["id"], "token": f"push-{tag}"})
    kv_insert("cart_sessions", {"session_id": f"cart-{tag}", "user_id": u["id"], "email": u["email"]})
    kv_insert("reviews", {"id": str(uuid.uuid4()), "user_id": u["id"], "product_id": f"prod-{tag}",
                          "user_name": "Ayse Y.", "rating": 5, "comment": "guzel", "status": "approved"})
    kv_insert("newsletter_subscribers", {"id": f"nl-{tag}", "email": u["email"], "active": True,
                                         "consent": True, "consent_at": kv_iso(-100), "created_at": kv_iso(-100)})
    o = None
    if closed_order:
        o = kv_order(u, attribution={"utm_source": "ig"}, user_data={"em": "hash"},
                     billing_info={"name": "Ayse Yilmaz", "tax_number": "11111111110", "tax_office": "Kadikoy"},
                     refund_bank_info=_seal({"iban": FULL_IBAN, "name": "Ayse Yilmaz", "bank": "X"}))
        kv_insert("customer_returns", {"id": str(uuid.uuid4()), "order_id": o["id"], "user_id": u["id"],
                                       "order_number": o["order_number"], "status": "refunded",
                                       "refund_bank_info": _seal({"iban": FULL_IBAN, "name": "Ayse Yilmaz"})})
    return u, o


# ───────────────────────────── /kvkk/my-data ─────────────────────────────
def test_my_data_returns_only_own_data_masked_and_rate_limited(kv_client, kv_users):
    a, _ = _seed_person(kv_users, "alice")
    b, _ = _seed_person(kv_users, "bobby")
    r = kv_client.get(MY_DATA, headers=KvUsers.hdr(a))
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control", "").startswith("no-store")
    body = r.json()
    text = r.text
    # own data present
    assert body["profile"]["email"] == a["email"]
    assert [x["title"] for x in body["addresses"]] == ["Ev-alice"]
    assert body["favorites"][0]["product_id"] == "prod-alice"
    assert len(body["orders"]) == 1
    # another user's data absent
    for needle in (b["email"], b["id"], "Ev-bobby", "prod-bobby", "bobby Sokak"):
        assert needle not in text
    # IBAN masked everywhere (export lists returns' refund_bank_info masked)
    assert FULL_IBAN not in text and FULL_IBAN[4:] not in text
    rb = body["returns"][0]["refund_bank_info"]
    assert rb["masked"] is True and rb["iban"].startswith("TR**")
    # no password / mfa / lockout secrets
    keys = {k.lower() for k in kv_all_keys(body["profile"])}
    for bad in ("password", "mfa_secret", "totp_secret", "mfa_enabled", "failed_attempts", "recovery_codes",
                "token_version"):
        assert bad not in keys
    assert "$2b$" not in text and "JBSWY3DPEHPK3PXP" not in text
    # audit trail
    assert kv_count("auth_audit_logs", {"event": "kvkk_data_export", "user_id": a["id"]}) == 1

    # 24h limit: 3 allowed in total, 4th → 429
    for _ in range(2):
        assert kv_client.get(MY_DATA, headers=KvUsers.hdr(a)).status_code == 200
    r4 = kv_client.get(MY_DATA, headers=KvUsers.hdr(a))
    assert r4.status_code == 429
    # the other user is unaffected by A's quota
    assert kv_client.get(MY_DATA, headers=KvUsers.hdr(b)).status_code == 200


def test_my_data_requires_auth(kv_client):
    assert kv_client.get(MY_DATA).status_code == 401


# ───────────────────────────── /kvkk/delete-request ─────────────────────────────
def test_delete_wrong_password_refused_nothing_changes(kv_client, kv_users):
    u, _ = _seed_person(kv_users, "wrongpw")
    r = kv_client.post(DELETE, json={"password": "not-my-password", "confirm": True}, headers=KvUsers.hdr(u))
    assert r.status_code == 400
    doc = kv_find("users", {"id": u["id"]})
    assert doc["email"] == u["email"] and not doc.get("kvkk_deleted")
    assert kv_count("addresses", {"user_id": u["id"]}) == 1
    assert kv_count("deletion_tombstones") == 0
    assert kv_count("auth_audit_logs", {"event": "kvkk_delete_request", "success": False}) == 1
    # missing confirm flag is also refused
    r2 = kv_client.post(DELETE, json={"password": u["_password_plain"]}, headers=KvUsers.hdr(u))
    assert r2.status_code == 400
    assert not kv_find("users", {"id": u["id"]}).get("kvkk_deleted")


def test_delete_with_open_order_is_pending_and_deletes_nothing(kv_client, kv_users):
    u, _ = _seed_person(kv_users, "openorder")
    kv_order(u, status="shipped", invoice_number="", invoice_issued=False, delivered_at=None, created_at=kv_iso(-2))
    r = kv_client.post(DELETE, json={"password": u["_password_plain"], "confirm": True}, headers=KvUsers.hdr(u))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    doc = kv_find("users", {"id": u["id"]})
    assert doc["email"] == u["email"] and doc["is_active"] is True and not doc.get("kvkk_deleted")
    assert kv_count("addresses", {"user_id": u["id"]}) == 1
    assert kv_count("favorites", {"user_id": u["id"]}) == 1
    assert kv_count("deletion_tombstones") == 0
    assert kv_count("iys_consents") == 0
    req = kv_findall("kvkk_requests", {"user_id": u["id"], "type": "delete"})
    assert len(req) == 1 and req[0]["status"] == "pending_open_orders"
    # the session still works (account not deleted)
    assert kv_client.get(MY_DATA, headers=KvUsers.hdr(u)).status_code == 200


def _delete_ok(kv_client, u):
    tok_hdr = KvUsers.hdr(u)
    r = kv_client.post(DELETE, json={"password": u["_password_plain"], "confirm": True}, headers=tok_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    return tok_hdr


def test_delete_without_open_orders_anonymizes_everything(kv_client, kv_users, kv_r2):
    u, o = _seed_person(kv_users, "deleteme")
    original_order = kv_find("orders", {"id": o["id"]})
    old_hdr = _delete_ok(kv_client, u)

    doc = kv_find("users", {"id": u["id"]})
    assert doc["kvkk_deleted"] is True and doc["is_active"] is False
    assert doc["email"] == f"deleted-{u['id']}@deleted.invalid"
    assert doc["first_name"] == "Silinmiş" and doc["last_name"] == "Üye"
    assert doc["phone"] == "" and doc["password"] == ""
    assert doc["accepts_marketing"] is False
    for gone in ("birth_date", "height_cm", "mfa_secret", "totp_secret", "recovery_codes"):
        assert gone not in doc
    assert u["email"] not in kv_json(doc)

    for coll in ("addresses", "favorites", "user_devices", "cart_sessions"):
        assert kv_count(coll, {"user_id": u["id"]}) == 0, coll
    assert kv_count("newsletter_subscribers", {"email": u["email"]}) == 0
    rv = kv_find("reviews", {"user_id": u["id"]})
    assert rv["user_name"] == "Silinmiş Üye"

    # sessions invalid: old token rejected
    assert kv_client.get(MY_DATA, headers=old_hdr).status_code == 401
    assert kv_client.get("/api/favorites", headers=old_hdr).status_code == 401

    # orders detached, contact masked, invoice/legal fields kept
    od = kv_find("orders", {"id": o["id"]})
    assert od["kvkk_detached"] is True and od["kvkk_legal_hold_until"]
    assert od["user_id"] == u["id"]  # pseudonym stays
    assert od["email"] == "" and od["phone"] == ""
    assert od["shipping_address"]["phone"] == "" and od["shipping_address"]["email"] == ""
    assert od["shipping_address"]["address"] == original_order["shipping_address"]["address"]
    assert od["invoice_number"] == original_order["invoice_number"]
    assert od["billing_info"] == original_order["billing_info"]
    assert od["total"] == original_order["total"] and od["items"] == original_order["items"]
    assert "attribution" not in od and "user_data" not in od

    # İYS RET for both channels
    ret = kv_findall("iys_consents", {"status": "RET", "user_id": u["id"]})
    assert ret and set(ret[-1]["channels"]) == {"EPOSTA", "MESAJ"}

    # tombstone in DB (hash only) + mirrored to the private archive
    tombs = kv_findall("deletion_tombstones")
    assert len(tombs) == 1
    t = tombs[0]
    assert t["status"] == "applied" and t["user_ids"] == [u["id"]] and t.get("archived_at")
    assert t["email_hmacs"] and t["phone_hmacs"]
    assert u["email"] not in kv_json(t) and u["phone"][-10:] not in kv_json(t)
    arch = kv_r2.bucket("kv-archive")
    key = f"kvkk-tombstones/{t['id']}.json"
    assert key in arch
    archived = json.loads(arch[key][0])
    assert archived["email_hmacs"] == t["email_hmacs"]
    assert u["email"] not in arch[key][0].decode()
    assert not kv_r2.bucket("kv-public")  # never on the public CDN bucket

    req = kv_findall("kvkk_requests", {"user_id": u["id"], "type": "delete"})
    assert req[0]["status"] == "completed"
    assert kv_count("auth_audit_logs", {"event": "kvkk_account_deleted"}) == 1


def test_reregister_after_deletion_no_marketing_no_welcome_coupon(kv_client, kv_users, kv_r2):
    kv_set_rule("welcome.enabled", True)
    u, _ = _seed_person(kv_users, "comeback")
    _delete_ok(kv_client, u)
    ip = {"cf-connecting-ip": "198.51.100.21"}

    r = kv_client.post("/api/auth/register", headers=ip, json={
        "email": u["email"], "password": "N3w-Passw0rd!", "first_name": "Ayse", "last_name": "Yilmaz"})
    assert r.status_code == 200, r.text
    new = kv_find("users", {"email": u["email"]})
    assert new["id"] != u["id"]
    assert new["accepts_marketing"] is False and new["reregistered_after_kvkk_deletion"] is True
    assert kv_count("coupons", {"reward_source": "welcome", "reward_user_id": new["id"]}) == 0

    # control: a never-deleted person DOES get the welcome coupon (the rule really is on)
    r2 = kv_client.post("/api/auth/register", headers=ip, json={
        "email": "fresh-person@example.com", "password": "N3w-Passw0rd!", "first_name": "Can", "last_name": "Ak"})
    assert r2.status_code == 200, r2.text
    fresh = kv_find("users", {"email": "fresh-person@example.com"})
    assert kv_count("coupons", {"reward_source": "welcome", "reward_user_id": fresh["id"]}) == 1
    assert not fresh.get("reregistered_after_kvkk_deletion")


def test_db_restore_reapply_tombstones_reanonymizes(kv_client, kv_users, kv_r2):
    u, o = _seed_person(kv_users, "restored")
    snapshot_user = kv_find("users", {"id": u["id"]})
    snapshot_addr = kv_findall("addresses", {"user_id": u["id"]})
    snapshot_order = kv_find("orders", {"id": o["id"]})
    _delete_ok(kv_client, u)

    # simulate a restore from a backup taken BEFORE the deletion: user, address and order come back,
    # and the tombstone collection itself is lost (only the private archive still has it)
    from routes.deps import db

    async def _restore():
        await db.users.replace_one({"id": u["id"]}, snapshot_user)
        for a in snapshot_addr:
            await db.addresses.insert_one(dict(a))
        await db.orders.replace_one({"id": o["id"]}, snapshot_order)
        await db.deletion_tombstones.delete_many({})
    kv_run(_restore())
    assert kv_find("users", {"id": u["id"]})["email"] == u["email"]

    from routes.kvkk import reapply_deletion_tombstones
    stats = kv_run(reapply_deletion_tombstones())
    assert stats["restored_tombstones"] == 1
    assert stats["users_reanonymized"] == 1
    doc = kv_find("users", {"id": u["id"]})
    assert doc["kvkk_deleted"] is True and doc["email"].endswith("@deleted.invalid") and doc["phone"] == ""
    assert kv_count("addresses", {"user_id": u["id"]}) == 0
    od = kv_find("orders", {"id": o["id"]})
    assert od["kvkk_detached"] is True and od["shipping_address"]["email"] == ""
    assert kv_count("deletion_tombstones") == 1
    # restored user cannot log in with the old password
    r = kv_client.post("/api/auth/login", json={"email": u["email"], "password": u["_password_plain"]},
                       headers={"cf-connecting-ip": "198.51.100.33"})
    assert r.status_code in (400, 401, 403)


def test_newsletter_optin_after_deletion_survives_reapply(kv_client, kv_users, kv_r2):
    u, _ = _seed_person(kv_users, "rejoin")
    _delete_ok(kv_client, u)
    # an OLD (pre-deletion) subscription restored from backup must be removed again…
    kv_insert("newsletter_subscribers", {"id": "nl-old", "email": "rejoin-old@example.com"})
    # …but a NEW explicit opt-in given AFTER the deletion is kept
    r = kv_client.post("/api/newsletter/subscribe", json={"email": u["email"], "consent": True},
                       headers={"cf-connecting-ip": "198.51.100.40"})
    assert r.status_code == 200, r.text
    from routes.kvkk import reapply_deletion_tombstones
    kv_run(reapply_deletion_tombstones())
    subs = kv_findall("newsletter_subscribers", {"email": u["email"]})
    assert len(subs) == 1 and subs[0]["active"] is True


def test_newsletter_optin_after_deletion_survives_partial_user_restore(kv_client, kv_users, kv_r2):
    """Partial restore (only `users` from an old backup) + reapply must not wipe a consent that the
    person gave AFTER the deletion (kvkk.py documents: 'Silmeden SONRA verilmiş açık rıza korunur')."""
    u, _ = _seed_person(kv_users, "rejoin2")
    snapshot_user = kv_find("users", {"id": u["id"]})
    _delete_ok(kv_client, u)
    r = kv_client.post("/api/newsletter/subscribe", json={"email": u["email"], "consent": True},
                       headers={"cf-connecting-ip": "198.51.100.41"})
    assert r.status_code == 200, r.text
    from routes.deps import db
    kv_run(db.users.replace_one({"id": u["id"]}, snapshot_user))
    from routes.kvkk import reapply_deletion_tombstones
    stats = kv_run(reapply_deletion_tombstones())
    assert stats["users_reanonymized"] == 1
    assert kv_find("users", {"id": u["id"]})["kvkk_deleted"] is True
    subs = kv_findall("newsletter_subscribers", {"email": u["email"]})
    assert len(subs) == 1 and subs[0]["active"] is True, "post-deletion opt-in was wiped by reapply"
    # and no new İYS RET is sent for a fresh opt-in
    rets = kv_findall("iys_consents", {"email": u["email"], "status": "RET"})
    assert len(rets) == 1  # only the one from the original deletion


# ───────────────────────────── admin / staff paths ─────────────────────────────
def test_admin_member_delete_uses_same_anonymization(kv_client, kv_users, kv_r2):
    u, o = _seed_person(kv_users, "admindel")
    weak = kv_users.staff(perms=["customers.view"])
    r = kv_client.delete(f"/api/admin/members/{u['id']}", headers=KvUsers.hdr(weak))
    assert r.status_code == 403
    assert not kv_find("users", {"id": u["id"]}).get("kvkk_deleted")

    staff = kv_users.staff(perms=["customers.delete"])
    r = kv_client.delete(f"/api/admin/members/{u['id']}", headers=KvUsers.hdr(staff))
    assert r.status_code == 200, r.text
    assert r.json()["anonymized"] is True
    doc = kv_find("users", {"id": u["id"]})
    assert doc["kvkk_deleted"] is True and doc["kvkk_delete_source"] == "admin"
    assert doc["email"].endswith("@deleted.invalid")
    assert kv_count("addresses", {"user_id": u["id"]}) == 0
    assert kv_find("orders", {"id": o["id"]})["kvkk_detached"] is True
    t = kv_findall("deletion_tombstones")
    assert len(t) == 1 and t[0]["source"] == "admin"
    assert f"kvkk-tombstones/{t[0]['id']}.json" in kv_r2.bucket("kv-archive")
    assert kv_findall("iys_consents", {"status": "RET", "user_id": u["id"]})
    # the admin can't use the member endpoint to wipe a staff account
    other_staff = kv_users.staff(perms=["orders.view"])
    r = kv_client.delete(f"/api/admin/members/{other_staff['id']}", headers=KvUsers.hdr(staff))
    assert r.status_code == 404
    assert kv_find("users", {"id": other_staff["id"]})["email"] == other_staff["email"]


def test_admin_member_delete_with_open_order_needs_force(kv_client, kv_users, kv_r2):
    u, _ = _seed_person(kv_users, "adminopen")
    kv_order(u, status="processing", invoice_number="", invoice_issued=False, created_at=kv_iso(-1))
    staff = kv_users.staff(perms=["customers.delete"])
    r = kv_client.delete(f"/api/admin/members/{u['id']}", headers=KvUsers.hdr(staff))
    assert r.status_code == 409
    assert not kv_find("users", {"id": u["id"]}).get("kvkk_deleted")


@pytest.mark.parametrize("kind", ["role_staff", "super_admin"])
def test_staff_account_cannot_self_delete_via_kvkk(kv_client, kv_users, kind):
    st = kv_users.staff(perms=["orders.view"]) if kind == "role_staff" else kv_users.staff(super_admin=True)
    r = kv_client.post(DELETE, json={"password": "Staff-Passw0rd!", "confirm": True}, headers=KvUsers.hdr(st))
    assert r.status_code == 403
    doc = kv_find("users", {"id": st["id"]})
    assert doc["email"] == st["email"] and doc["is_active"] is True and not doc.get("kvkk_deleted")
    assert kv_count("deletion_tombstones") == 0


def test_anonymize_user_refuses_staff_directly(kv_users):
    from fastapi import HTTPException
    from routes.kvkk import anonymize_user
    st = kv_users.staff(perms=["orders.view"])
    with pytest.raises(HTTPException) as e:
        kv_run(anonymize_user(kv_find("users", {"id": st["id"]}), source="test", request_id="r"))
    assert e.value.status_code == 403
