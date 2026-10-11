"""Denetim #18: elektronik tablo içe aktarımında sıkıştırma bombası / dev çalışma kitabı koruması.

OOXML (.xlsx) bir ZIP arşividir: birkaç MB'lık dosya açılınca GB'larca XML'e dönüşebilir ve
openpyxl/pandas onu tamamen belleğe alır. Burada dosya AYRIŞTIRILMADAN önce yalnız ZIP dizini
okunur (açma yapılmaz) ve toplam açılmış boyut, parça sayısı, sıkıştırma oranı sınırlanır;
ayrıştırmadan sonra satır sayısı da tavanlanır.
"""
from __future__ import annotations

import io
import zipfile

from fastapi import HTTPException

from security.uploads import MB, read_upload_capped

MAX_SPREADSHEET_BYTES = 20 * MB          # yüklenen dosya
MAX_EXPANDED_BYTES = 150 * MB            # arşivdeki tüm parçaların açılmış toplamı
MAX_ENTRY_BYTES = 120 * MB               # tek parça (ör. sheet1.xml)
MAX_ENTRIES = 3000
MAX_RATIO = 150                          # açılmış / sıkışmış (normal xlsx ≈ 5–30)
MAX_ROWS = 60000


def inspect_spreadsheet(content: bytes) -> None:
    if len(content) > MAX_SPREADSHEET_BYTES:
        raise HTTPException(status_code=413, detail="Dosya çok büyük (en fazla 20 MB)")
    if not content.startswith(b"PK"):
        return  # .xls (OLE) / .csv: yalnız boyut sınırı
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        infos = zf.infolist()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Excel dosyası okunamadı (bozuk arşiv)")
    if len(infos) > MAX_ENTRIES:
        raise HTTPException(status_code=400, detail="Excel dosyası çok fazla parça içeriyor")
    total = 0
    for i in infos:
        total += int(i.file_size)
        if i.file_size > MAX_ENTRY_BYTES:
            raise HTTPException(status_code=413, detail="Excel sayfası çok büyük")
        if i.compress_size > 0 and i.file_size / i.compress_size > MAX_RATIO and i.file_size > 1 * MB:
            raise HTTPException(status_code=400, detail="Excel dosyası olağan dışı sıkıştırılmış (reddedildi)")
    if total > MAX_EXPANDED_BYTES:
        raise HTTPException(status_code=413, detail="Excel dosyası açıldığında çok büyük")


async def read_spreadsheet_upload(upload, max_bytes: int = MAX_SPREADSHEET_BYTES) -> bytes:
    content = await read_upload_capped(upload, max_bytes, "Dosya çok büyük (en fazla 20 MB)")
    inspect_spreadsheet(content)
    return content


def check_rows(n: int) -> None:
    if int(n or 0) > MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"Dosyada çok fazla satır var (en fazla {MAX_ROWS})")


PARSE_TIMEOUT_S = 90


async def parse_offloop(fn, *args, timeout: float = PARSE_TIMEOUT_S, **kwargs):
    """Denetim #18: elektronik tablo ayrıştırması (openpyxl/pandas, CPU-yoğun ve SENKRON) olay
    döngüsünü — dolayısıyla ödeme/sepet dahil tüm istekleri — bloke etmesin diye iş parçacığında
    ve süre sınırıyla çalışır. Süre aşılırsa istek 422 ile reddedilir."""
    import asyncio
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=422, detail="Excel dosyası işlenemedi (çok büyük ya da karmaşık).")
