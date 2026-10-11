"""Denetim #12: multipart gövdeler uç bazında ve BELLEĞE ALINMADAN önce sınırlanır; kimliksiz yükleme
16 MB ve eşzamanlılık tavanıyla sınırlıdır; uç okuması parça parça (read_upload_capped)."""
import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from security import uploads


def _mp(size):
    return {"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(size)}


def test_public_multipart_capped_at_16mb(kv_client):
    r = kv_client.post("/api/orders/by-number/W10001/payment-notification", content=b"x",
                       headers=_mp(17 * 1024 * 1024))
    assert r.status_code == 413


def test_staff_known_endpoint_uses_endpoint_cap(kv_client, kv_users):
    st = kv_users.staff(perms=["orders.invoice"], typ="staff")
    from routes.deps import create_token, now_ts
    tok = create_token(st["id"], is_admin=True, token_version=0, auth_time=now_ts())
    h = {**_mp(17 * 1024 * 1024), "Authorization": f"Bearer {tok}"}
    assert kv_client.post("/api/orders/o1/upload-invoice", content=b"x", headers=h).status_code == 413
    assert uploads.multipart_cap_for("/api/upload/video", True) == 120 * uploads.MB
    assert uploads.multipart_cap_for("/api/products/import/excel", True) == 21 * uploads.MB
    assert uploads.multipart_cap_for("/api/upload/video", False) == uploads.PUBLIC_MULTIPART_MAX_BYTES


def test_public_multipart_concurrency_cap(kv_client, monkeypatch):
    sem = asyncio.Semaphore(0)          # dolu
    monkeypatch.setattr(uploads, "_public_sem", sem)
    r = kv_client.post("/api/orders/by-number/W10001/payment-notification", content=b"x", headers=_mp(10))
    assert r.status_code == 429


def test_read_upload_capped_stops_early():
    big = UploadFile(io.BytesIO(b"a" * (3 * uploads.MB)), filename="f")
    with pytest.raises(HTTPException) as e:
        asyncio.run(uploads.read_upload_capped(big, 2 * uploads.MB))
    assert e.value.status_code == 413
    ok = UploadFile(io.BytesIO(b"b" * 1000), filename="g")
    assert asyncio.run(uploads.read_upload_capped(ok, 2000)) == b"b" * 1000
