"""Denetim #12: dosya yükleme gövde sınırları — uç bazında, BELLEĞE ALMADAN ÖNCE uygulanır.

* ``multipart_cap_for(path, is_staff)``: BodySizeLimitMiddleware'in multipart tavanı. Eskiden her
  multipart istek (kimliksiz dahil) 120 MB alıyordu; uç kendi sınırını ancak tüm dosyayı
  ``file.read()`` ile belleğe aldıktan SONRA uyguluyordu.
* ``read_upload_capped(upload, max_bytes)``: UploadFile'ı parça parça okur; sınır aşılınca hemen 413
  (tüm dosya belleğe alınmaz).
"""
from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException

MB = 1024 * 1024
# Kimliksiz / müşteri multipart gövdesi (dekont 15 MB + form alanları payı).
PUBLIC_MULTIPART_MAX_BYTES = 16 * MB
# Personel multipart: bilinen uçlarda uç sınırı (+ küçük pay), bilinmeyen uçta genel tavan.
STAFF_MULTIPART_DEFAULT_MAX = 120 * MB
_STAFF_PATH_CAPS = (
    (re.compile(r"^/api/upload/video/?$"), 120 * MB),
    (re.compile(r"^/api/upload/image/?$"), 26 * MB),
    (re.compile(r"^/api/orders/[^/]+/upload-invoice/?$"), 16 * MB),
    (re.compile(r"^/api/admin/reports/assistant/transcribe/?$"), 11 * MB),
    (re.compile(r"^/api/amazon/spapi/orders/parse-packing-slip/?$"), 16 * MB),
    (re.compile(r"(^|/)(import-xlsx|import-technical-xlsx|import/excel(/analyze)?|upload-excel|"
                r"price-stock/(preview|apply)|production-plan/import|import-mng-tracking)/?$"), 21 * MB),
)
# Kimliksiz multipart için eşzamanlılık tavanı (bellek/disk yükseltmesine karşı).
PUBLIC_MULTIPART_CONCURRENCY = 6
_public_sem: asyncio.Semaphore | None = None


def public_multipart_semaphore() -> asyncio.Semaphore:
    global _public_sem
    if _public_sem is None:
        _public_sem = asyncio.Semaphore(PUBLIC_MULTIPART_CONCURRENCY)
    return _public_sem


def multipart_cap_for(path: str, is_staff: bool) -> int:
    if not is_staff:
        return PUBLIC_MULTIPART_MAX_BYTES
    p = path or ""
    for rx, cap in _STAFF_PATH_CAPS:
        if rx.search(p):
            return cap
    return STAFF_MULTIPART_DEFAULT_MAX


async def read_upload_capped(upload, max_bytes: int, detail: str = "Dosya çok büyük") -> bytes:
    """UploadFile'ı en fazla max_bytes okur; aşılırsa 413 (fazlası belleğe alınmaz)."""
    max_bytes = int(max_bytes)
    buf = bytearray()
    chunk = 1 * MB
    while True:
        part = await upload.read(min(chunk, max_bytes + 1 - len(buf)))
        if not part:
            break
        buf.extend(part)
        if len(buf) > max_bytes:
            raise HTTPException(status_code=413, detail=detail)
    return bytes(buf)
