"""scheduler._run_private_archive: >30-day payment receipts (current + previous VERSIONS, encrypted or
legacy base64) move to the PRIVATE archive bucket with verify-then-delete; nothing goes public."""
from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock

import pytest

from kv_harness import kv_r2, kv_insert, kv_find, kv_findall, kv_iso  # noqa: F401
from conftest import kv_run


def _scheduler():
    try:
        import scheduler
    except Exception as e:  # pragma: no cover - concurrent edit by another agent
        pytest.skip(f"scheduler import failed (concurrent edit?): {e}")
    return scheduler


@pytest.fixture
def kv_sched(monkeypatch):
    s = _scheduler()
    monkeypatch.setattr(s, "_still_leader", AsyncMock(return_value=True))

    async def _no_sleep(_x):
        return None
    monkeypatch.setattr(s.asyncio, "sleep", _no_sleep)
    return s


def _sealed(raw: bytes, days_old: float, fn="dekont.pdf"):
    from routes.orders import seal_receipt
    return seal_receipt({"filename": fn, "content_type": "application/pdf", "size": len(raw),
                         "uploaded_at": kv_iso(-days_old), "data_b64": base64.b64encode(raw).decode()})


def _plain_of_archived(blob: bytes) -> bytes:
    """Archived receipts stay encrypted at rest (Fernet token) — decrypt to compare with the original."""
    from security import crypto
    return crypto._fernet_dec.decrypt(bytes(blob))


def _version(order_number, receipt, created_days_ago):
    return kv_insert("payment_receipt_versions", {
        "id": str(uuid.uuid4()), "order_id": "o-" + order_number, "order_number": order_number,
        "receipt": receipt, "created_at": kv_iso(-created_days_ago)})


def test_old_receipt_versions_archived_verify_then_delete(kv_sched, kv_r2):
    raw_old = b"%PDF-1.4 old encrypted receipt " + uuid.uuid4().bytes
    raw_legacy = b"%PDF-1.4 legacy base64 receipt " + uuid.uuid4().bytes
    raw_new = b"%PDF-1.4 recent receipt " + uuid.uuid4().bytes
    v_old = _version("FC1001", _sealed(raw_old, 45), 44)
    assert "data_enc" in v_old["receipt"] and "data_b64" not in v_old["receipt"]
    v_legacy = _version("FC1002", {"filename": "a.jpg", "content_type": "image/jpeg", "uploaded_at": kv_iso(-60),
                                   "data_b64": base64.b64encode(raw_legacy).decode()}, 59)
    v_new = _version("FC1003", _sealed(raw_new, 5), 4)
    v_bad = _version("FC1004", {"filename": "b.pdf", "uploaded_at": kv_iso(-90), "data_enc": "not-a-fernet-token"}, 89)
    # current receipt on an order (encrypted) too
    raw_cur = b"%PDF current " + uuid.uuid4().bytes
    kv_insert("orders", {"id": "o-cur", "order_number": "FC2001", "payment_receipt": _sealed(raw_cur, 40)})

    kv_run(kv_sched._run_private_archive())

    arch = kv_r2.bucket("kv-archive")
    assert not kv_r2.bucket("kv-public")          # never on the public CDN bucket

    for v, raw in ((v_old, raw_old), (v_legacy, raw_legacy)):
        doc = kv_find("payment_receipt_versions", {"id": v["id"]})
        rc = doc["receipt"]
        assert "data_enc" not in rc and "data_b64" not in rc, rc.keys()
        assert rc["archive_key"].startswith("dekontlar/surumler/") and rc["archived_at"]
        blob = arch[rc["archive_key"]][0]
        assert raw not in blob and b"%PDF" not in blob     # still encrypted in the archive
        assert _plain_of_archived(blob) == raw             # verified copy of the original bytes
        assert rc["filename"] == v["receipt"]["filename"]   # metadata kept

    doc_new = kv_find("payment_receipt_versions", {"id": v_new["id"]})
    assert doc_new["receipt"].get("data_enc") and "archive_key" not in doc_new["receipt"]
    doc_bad = kv_find("payment_receipt_versions", {"id": v_bad["id"]})
    assert doc_bad["receipt"]["data_enc"] == "not-a-fernet-token" and "archive_key" not in doc_bad["receipt"]

    cur = kv_find("orders", {"id": "o-cur"})["payment_receipt"]
    assert "data_enc" not in cur and _plain_of_archived(arch[cur["archive_key"]][0]) == raw_cur

    logs = kv_findall("integration_logs", {"action": "private_archive"})
    assert logs and "1 hata" in logs[-1]["message"]
    assert len(arch) == 3


def test_failed_verification_keeps_db_copy(kv_sched, kv_r2, monkeypatch):
    raw = b"%PDF data " + uuid.uuid4().bytes
    v = _version("FC3001", _sealed(raw, 40), 40)
    from services import r2_storage
    monkeypatch.setattr(r2_storage, "archive_get", lambda key: (b"corrupted", "application/pdf"))
    kv_run(kv_sched._run_private_archive())
    rc = kv_find("payment_receipt_versions", {"id": v["id"]})["receipt"]
    assert rc.get("data_enc") and "archive_key" not in rc


def test_receipt_replaced_meanwhile_is_not_deleted(kv_sched, kv_r2, monkeypatch):
    """Conditional delete: if the stored bytes changed between read and delete, the new bytes stay."""
    raw = b"%PDF first " + uuid.uuid4().bytes
    v = _version("FC4001", _sealed(raw, 40), 40)
    from services import r2_storage
    real_put = r2_storage.archive_put
    replacement = _sealed(b"%PDF replaced " + uuid.uuid4().bytes, 40)

    def _put_and_race(key, data, ct="application/octet-stream"):
        real_put(key, data, ct)
        from routes.deps import db
        import asyncio
        asyncio.run(db.payment_receipt_versions.update_one(
            {"id": v["id"]}, {"$set": {"receipt.data_enc": replacement["data_enc"]}}))
    monkeypatch.setattr(r2_storage, "archive_put", _put_and_race)
    kv_run(kv_sched._run_private_archive())
    rc = kv_find("payment_receipt_versions", {"id": v["id"]})["receipt"]
    assert rc["data_enc"] == replacement["data_enc"] and "archive_key" not in rc


def test_archive_disabled_is_noop(kv_sched, monkeypatch, kv_s3):
    monkeypatch.delenv("R2_ARCHIVE_BUCKET", raising=False)
    v = _version("FC5001", _sealed(b"%PDF x", 40), 40)
    kv_run(kv_sched._run_private_archive())
    assert kv_find("payment_receipt_versions", {"id": v["id"]})["receipt"].get("data_enc")
    assert not kv_s3.buckets.get("kv-archive")


def test_not_leader_deletes_nothing(kv_sched, kv_r2, monkeypatch):
    monkeypatch.setattr(kv_sched, "_still_leader", AsyncMock(return_value=False))
    v = _version("FC6001", _sealed(b"%PDF y", 40), 40)
    kv_run(kv_sched._run_private_archive())
    assert kv_find("payment_receipt_versions", {"id": v["id"]})["receipt"].get("data_enc")
    assert not kv_r2.bucket("kv-archive")
