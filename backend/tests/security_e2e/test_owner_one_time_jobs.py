"""Kullanıcının istediği tek seferlik işler: W12254 boş test siparişi silme (yalnız boşsa)."""
import pytest

from id_harness import id_find, id_run

pytestmark = pytest.mark.usefixtures("id_env")


def _seed(order):
    from routes import deps
    id_run(deps.db.orders.insert_one(dict(order)))


def _run():
    import scheduler
    id_run(scheduler._ensure_w12254_test_siparisi_sil())


def test_w12254_empty_test_order_archived_and_removed(id_client):
    _seed({"id": "o-test", "order_number": "W12254", "items": [], "total": 99.0, "payment_status": "pending",
           "status": "pending"})
    _run()
    assert id_find("orders", {"order_number": "W12254"}) is None
    arch = id_find("orders_deleted", {"order_number": "W12254"})
    assert arch and arch["deleted_by"].startswith("sistem")
    # tek seferlik: tekrar çalışınca bir şey yapmaz
    _run()


def test_w12254_real_order_is_never_deleted(id_client):
    _seed({"id": "o-real", "order_number": "W12254", "items": [{"product_id": "p1", "quantity": 1, "price": 690}],
           "total": 789.0, "payment_status": "paid", "status": "confirmed"})
    _run()
    assert id_find("orders", {"order_number": "W12254"}) is not None
    assert id_find("orders_deleted", {"order_number": "W12254"}) is None
