"""Reviews (no e-mail as display name, 5/hour) and referral reward gating."""
from __future__ import annotations

import uuid

import pytest

from kv_harness import kv_insert, kv_find, kv_findall, kv_count, kv_iso, kv_set_rule
from conftest import KvUsers, kv_run


# ───────────────────────────── reviews ─────────────────────────────
def _submit(client, user, ip, pid="p-rev", **extra):
    return client.post("/api/reviews", headers={**KvUsers.hdr(user), "cf-connecting-ip": ip},
                       json={"product_id": pid, "rating": 5, "title": "iyi", "comment": "begendim", **extra})


@pytest.mark.parametrize("first,last,expected", [("Ayse", "Yilmaz", "Ayse Y."), ("Ayse", "", "Ayse"),
                                                 ("", "", "Müşteri"), ("  ", "Kaya", "Müşteri")])
def test_review_display_name_never_email(kv_client, kv_users, first, last, expected):
    u = kv_users.customer(email="real.person@example.com", first_name=first, last_name=last)
    r = _submit(kv_client, u, "198.51.100.120")
    assert r.status_code == 200, r.text
    assert r.json()["review"]["user_name"] == expected
    stored = kv_find("reviews", {"user_id": u["id"]})
    assert stored["user_name"] == expected and "@" not in stored["user_name"]
    assert stored["status"] == "pending"


def test_public_list_masks_legacy_email_names_and_hides_user_id(kv_client, kv_users):
    kv_insert("reviews", {"id": "r1", "product_id": "p-rev", "user_id": "u-secret", "status": "approved",
                          "user_name": "legacy.person@example.com", "rating": 4, "created_at": kv_iso(-1)})
    kv_insert("reviews", {"id": "r2", "product_id": "p-rev", "user_id": "u2", "status": "pending",
                          "user_name": "Bekleyen K.", "rating": 1, "created_at": kv_iso(-1)})
    r = kv_client.get("/api/reviews/product/p-rev")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == ["r1"]          # pending review not public
    assert items[0]["user_name"] == "Müşteri"
    assert "legacy.person@example.com" not in r.text and "u-secret" not in r.text


def test_review_submit_rate_limited_5_per_hour(kv_client, kv_users):
    u = kv_users.customer()
    ip = "198.51.100.121"
    for i in range(5):
        assert _submit(kv_client, u, ip, comment=f"c{i}").status_code == 200
    r = _submit(kv_client, u, ip, comment="sixth")
    assert r.status_code == 429
    assert kv_count("reviews", {"user_id": u["id"]}) == 5
    # a different client IP is not affected
    assert _submit(kv_client, kv_users.customer(), "198.51.100.122").status_code == 200


def test_review_requires_login_and_valid_rating(kv_client, kv_users):
    assert kv_client.post("/api/reviews", json={"product_id": "p", "rating": 5}).status_code == 401
    u = kv_users.customer()
    for bad in (0, 6, "x", -1):
        r = kv_client.post("/api/reviews", headers={**KvUsers.hdr(u), "cf-connecting-ip": "198.51.100.123"},
                           json={"product_id": "p", "rating": bad})
        assert r.status_code == 400
    r = kv_client.post("/api/reviews", headers={**KvUsers.hdr(u), "cf-connecting-ip": "198.51.100.124"},
                       json={"product_id": {"$ne": ""}, "rating": 5})
    assert r.status_code == 400


# ───────────────────────────── referrals ─────────────────────────────
def _referral(referrer, referee, created_days_ago=30):
    return kv_insert("referrals", {"id": str(uuid.uuid4()), "referrer_user_id": referrer["id"],
                                   "referrer_email": referrer["email"], "referee_user_id": referee["id"],
                                   "referee_email": referee["email"], "code": "ABC1234", "status": "pending",
                                   "created_at": kv_iso(-created_days_ago)})


def _ref_order(referee, **extra):
    o = {"id": str(uuid.uuid4()), "order_number": "R" + uuid.uuid4().hex[:6], "user_id": referee["id"],
         "status": "delivered", "payment_status": "paid", "total": 800.0, "created_at": kv_iso(-25),
         "delivered_at": kv_iso(-20), "email": referee["email"]}
    o.update(extra)
    return kv_insert("orders", o)


def _award():
    from routes.referrals import award_pending_referrals
    return kv_run(award_pending_referrals())


def _pair(kv_users, verified=True):
    referrer = kv_users.customer()
    referee = kv_users.customer(email_verified=verified)
    ref = _referral(referrer, referee)
    return referrer, referee, ref


@pytest.mark.parametrize("case", ["not_delivered", "inside_return_window", "return_requested",
                                  "return_approved", "refunded_amount", "refund_paid", "unpaid",
                                  "order_before_referral", "deleted_order", "below_min_total"])
def test_no_reward_until_delivered_window_passed_and_no_return(kv_users, case):
    kv_set_rule("return.window_days", 14)
    kv_set_rule("referral.min_order_total", 300)
    _referrer, referee, ref = _pair(kv_users)
    overrides = {
        "not_delivered": {"status": "shipped", "delivered_at": None},
        "inside_return_window": {"delivered_at": kv_iso(-5)},
        "return_requested": {"return_request": {"status": "created"}},
        "return_approved": {"return_request": {"status": "approved"}},
        "refunded_amount": {"refund_amount": 100.0},
        "refund_paid": {"refund_paid_at": kv_iso(-1)},
        "unpaid": {"payment_status": "awaiting_payment"},
        "order_before_referral": {"created_at": kv_iso(-40), "delivered_at": kv_iso(-35)},
        "deleted_order": {"is_deleted": True},
        "below_min_total": {"total": 120.0},
    }[case]
    _ref_order(referee, **overrides)
    assert _award() == 0
    assert kv_find("referrals", {"id": ref["id"]})["status"] == "pending"
    assert kv_count("coupons") == 0


def test_unverified_email_gets_no_reward(kv_users):
    _referrer, referee, ref = _pair(kv_users, verified=False)
    _ref_order(referee)
    assert _award() == 0
    assert kv_find("referrals", {"id": ref["id"]})["status"] == "pending" and kv_count("coupons") == 0
    # after verification the very same order qualifies
    from routes.deps import db
    kv_run(db.users.update_one({"id": referee["id"]}, {"$set": {"email_verified": True}}))
    assert _award() == 1


def test_deactivated_referee_gets_no_reward(kv_users):
    _referrer, referee, ref = _pair(kv_users)
    from routes.deps import db
    kv_run(db.users.update_one({"id": referee["id"]}, {"$set": {"is_active": False}}))
    _ref_order(referee)
    assert _award() == 0 and kv_count("coupons") == 0


def test_qualifying_order_rewards_both_once(kv_users, kv_isolation):
    referrer, referee, ref = _pair(kv_users)
    _ref_order(referee)
    assert _award() == 1
    doc = kv_find("referrals", {"id": ref["id"]})
    assert doc["status"] == "rewarded" and doc["referrer_coupon"] and doc["referee_coupon"]
    coupons = {c["reward_source"]: c for c in kv_findall("coupons")}
    assert coupons["referral_referrer"]["reward_user_id"] == referrer["id"]
    assert coupons["referral_referee"]["reward_user_id"] == referee["id"]
    assert all(c["usage_limit"] == 1 for c in coupons.values())
    # idempotent
    assert _award() == 0 and kv_count("coupons") == 2
    assert kv_isolation.smtp.await_count == 2   # one mail per party, sent via the mocked mailer


def test_self_referral_and_duplicate_registration_ignored(kv_users):
    from routes.referrals import record_referral_on_register
    u = kv_users.customer(referral_code="SELF123")
    kv_run(record_referral_on_register(u, "SELF123"))
    assert kv_count("referrals") == 0
    referrer = kv_users.customer(referral_code="REFX999")
    new = kv_users.customer()
    kv_run(record_referral_on_register(new, "refx999"))
    kv_run(record_referral_on_register(new, "REFX999"))
    assert kv_count("referrals", {"referee_user_id": new["id"]}) == 1
    assert kv_find("referrals", {"referee_user_id": new["id"]})["referrer_user_id"] == referrer["id"]
