"""Denetim #18: elektronik tablo içe aktarma ZIP bombası / dev çalışma kitabı reddedilir (ayrıştırmadan
önce yalnız ZIP dizini incelenir), satır sayısı tavanlanır."""
import io
import zipfile

import pytest
from fastapi import HTTPException

from security import spreadsheet_guard as sg


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_high_ratio_bomb_rejected():
    bomb = _zip([("xl/worksheets/sheet1.xml", b"<c>0</c>" * (4 * 1024 * 1024))])   # ~32 MB → çok küçük zip
    assert len(bomb) < 1024 * 1024
    with pytest.raises(HTTPException) as e:
        sg.inspect_spreadsheet(bomb)
    assert e.value.status_code in (400, 413)


def test_normal_workbook_ok_and_row_cap():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(50):
        ws.append(["Barkod", i, "x" * 10])
    b = io.BytesIO(); wb.save(b)
    sg.inspect_spreadsheet(b.getvalue())
    sg.check_rows(100)
    with pytest.raises(HTTPException):
        sg.check_rows(sg.MAX_ROWS + 1)


def test_endpoint_rejects_bomb(kv_client, kv_users):
    st = kv_users.staff(perms=["products.import_xlsx", "products.view"])
    bomb = _zip([("xl/worksheets/sheet1.xml", b"<c>0</c>" * (4 * 1024 * 1024))])
    r = kv_client.post("/api/bulk-ops/price-stock/preview", headers=kv_users.hdr(st),
                       files={"file": ("x.xlsx", bomb, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code in (400, 413), r.text
