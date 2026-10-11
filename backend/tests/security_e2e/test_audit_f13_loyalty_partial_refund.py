"""Denetim #13: kısmi iadede iade edilen tutarın puanı oransal geri alınır; puan net tutardan verilir."""
from datetime import datetime, timezone

from conftest import kv_run
from routes.loyalty import award_loyalty_points


def _setup(db, status="delivered"):
    kv_run(db.settings.insert_one({"id": "loyalty_state", "launched_at": "2020-01-01T00:00:00+00:00"}))
    kv_run(db.users.insert_one({"id": "u1", "email": "u@example.com", "loyalty_points": 0}))
    kv_run(db.orders.insert_one({"id": "o1", "order_number": "W1", "user_id": "u1", "total": 1000.0,
                                 "payment_status": "paid", "status": status,
                                 "created_at": datetime.now(timezone.utc).isoformat()}))


def _pts(db):
    return kv_run(db.users.find_one({"id": "u1"}))["loyalty_points"]


def test_partial_refund_revokes_proportionally(kv_db):
    _setup(kv_db)
    kv_run(award_loyalty_points(kv_db))
    assert _pts(kv_db) == 20.0                     # %2
    kv_run(kv_db.customer_returns.insert_one({"id": "r1", "order_id": "o1", "status": "approved",
                                              "refund_amount": 400.0,
                                              "created_at": datetime.now(timezone.utc).isoformat()}))
    kv_run(kv_db.orders.update_one({"id": "o1"}, {"$set": {"status": "partial_refunded"}}))
    kv_run(award_loyalty_points(kv_db))
    assert _pts(kv_db) == 12.0                     # 600 × %2
    kv_run(award_loyalty_points(kv_db))            # idempotent
    assert _pts(kv_db) == 12.0
    o = kv_run(kv_db.orders.find_one({"id": "o1"}))
    assert o["loyalty_points_earned"] == 12.0
    tx = kv_run(kv_db.loyalty_transactions.find({"type": "partial_revoke"}).to_list(5))
    assert len(tx) == 1 and tx[0]["points"] == 8.0


def test_award_after_partial_refund_uses_net(kv_db):
    _setup(kv_db, status="partial_refunded")
    kv_run(kv_db.orders.update_one({"id": "o1"}, {"$set": {"refunded_amount": 250.0}}))
    kv_run(award_loyalty_points(kv_db))
    assert _pts(kv_db) == 15.0                     # 750 × %2
