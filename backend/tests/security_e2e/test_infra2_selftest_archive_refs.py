"""CHAIN08 / CHAIN10 — restore reconciliation in the live self-test.

After a DB restore, records may point at private-archive (R2) objects that no longer exist (or the
other way round). The self-test samples up to 50 archive references — payment receipts, receipt
versions, archived files / manual invoice PDFs, KVKK tombstones — and verifies each object exists;
missing ones fail the check and are listed in the integration log (keys only, no personal data).
"""
from __future__ import annotations

import pytest

from conftest import kv_run
from kv_harness import kv_findall, kv_insert, kv_r2  # noqa: F401


def _seed(kv_r2, n_ok=3):
    for i in range(n_ok):
        k = f"dekontlar/2026-09/FC{i}-abc.png.enc"
        kv_r2.put_object(Bucket="kv-archive", Key=k, Body=b"x")
        kv_insert("orders", {"id": f"o{i}", "order_number": f"FC{i}", "payment_receipt": {"archive_key": k}})
    kv_r2.put_object(Bucket="kv-archive", Key="faturalar/2026-09/inv1.pdf", Body=b"%PDF")
    kv_insert("files", {"id": "f1", "storage_path": "inv1.pdf", "archive_key": "faturalar/2026-09/inv1.pdf"})
    kv_r2.put_object(Bucket="kv-archive", Key="kvkk-tombstones/t1.json", Body=b"{}")
    kv_insert("deletion_tombstones", {"id": "t1", "deleted_at": "2026-09-01"})
    kv_r2.put_object(Bucket="kv-archive", Key="manuel/FC9.pdf", Body=b"%PDF")
    kv_insert("orders", {"id": "o9", "order_number": "FC9", "invoice_manual_file": {"archive_key": "manuel/FC9.pdf"}})
    kv_r2.put_object(Bucket="kv-archive", Key="dekontlar/surumler/2026-08/FC1-v.png.enc", Body=b"x")
    kv_insert("payment_receipt_versions", {"id": "v1", "receipt": {"archive_key": "dekontlar/surumler/2026-08/FC1-v.png.enc"}})


def test_all_references_present_is_ok(kv_r2):
    from routes.deps import db
    from security import selftest
    _seed(kv_r2)
    r = kv_run(selftest.check_archive_references(db))
    assert r["enabled"] and r["checked"] == 7 and r["missing"] == []
    st, detail = kv_run(selftest._c_archive_refs(db))
    assert st == "ok" and "7 örnek" in detail


def test_missing_objects_after_restore_are_reported(kv_r2):
    from routes.deps import db
    from security import selftest
    _seed(kv_r2)
    # restore brought back rows whose archive objects were never written / already purged
    kv_insert("orders", {"id": "ox", "order_number": "FCX", "payment_receipt": {"archive_key": "dekontlar/2026-01/FCX-gone.png.enc"}})
    kv_insert("deletion_tombstones", {"id": "t-gone"})
    kv_insert("files", {"id": "fx", "archive_key": "faturalar/2026-01/gone.pdf"})
    st, detail = kv_run(selftest._c_archive_refs(db))
    assert st == "fail"
    assert "3/10" in detail and "dekont: 1" in detail and "kvkk_mezar_tasi: 1" in detail and "dosya: 1" in detail
    log = kv_findall("integration_logs", {"action": "archive_reference_missing"})
    keys = {m["key"] for m in log[0]["details"]["missing"]}
    assert keys == {"dekontlar/2026-01/FCX-gone.png.enc", "kvkk-tombstones/t-gone.json", "faturalar/2026-01/gone.pdf"}


def test_sample_is_capped_at_50(kv_r2):
    from routes.deps import db
    from security import selftest
    for i in range(80):
        kv_insert("files", {"id": f"f{i}", "archive_key": f"faturalar/x/{i}.pdf"})
        kv_insert("deletion_tombstones", {"id": f"tt{i}"})
    refs = kv_run(selftest.collect_archive_refs(db))
    assert len(refs) <= 50


def test_archive_disabled_warns(monkeypatch):
    from routes.deps import db
    from security import selftest
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_enabled", lambda: False)
    st, _ = kv_run(selftest._c_archive_refs(db))
    assert st == "warn"


def test_check_is_part_of_the_daily_selftest(kv_r2):
    import inspect
    from security import selftest
    src = inspect.getsource(selftest.run_selftest)
    for name in ("Arşiv referansları (restore mutabakatı)", "Vitrin script host manifesti", "Denetim outbox'ı",
                 "Pazarlama ret listesi arşivi", "Entegrasyon tazeliği", "Vitrin bütünlüğü",
                 "Görsel dönüştürme izolasyonu"):
        assert name in src
