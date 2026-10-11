"""Hesap tablosu (XLSX/CSV) FORMÜL ENJEKSİYONU koruması — ortak yardımcı (INJ-01).

Müşteri / pazaryeri / üçüncü taraf kaynaklı metin ("=HYPERLINK(...)", "=cmd|' /C calc'!A0",
"@SUM(...)") Excel'e ham yazılırsa:
  * openpyxl "=" ile başlayan string'i doğrudan FORMÜL hücresi olarak kaydeder,
  * Excel/LibreOffice "+", "-", "@", TAB, CR ile başlayan metni de formül sayabilir (CSV / yeniden kayıt).
Dışa aktarımı açan personelin bilgisayarında veri sızdırma (HYPERLINK) / DDE komut çalıştırma riski.

Kural (members.py / consents.py ile aynı): bu öneklerle başlayan METİN hücresinin başına ' eklenir →
düz metin. Saf sayı/telefon ("+90 555 ...", "-12,5", "-") formül yükü taşıyamaz → okunurluk için olduğu
gibi bırakılır. Sayı/bool/None değerlere dokunulmaz.
"""
from __future__ import annotations

import re

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMERIC = re.compile(r"^[+\-]?[\d\s().,\-]*$")


def xlsx_safe(value):
    """Tek hücre değeri: formül önekiyle başlayan METİN → başına ' eklenir."""
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(FORMULA_PREFIXES) and not _PLAIN_NUMERIC.match(value):
        return "'" + value
    return value


def neutralize_workbook(wb) -> int:
    """openpyxl Workbook'taki TÜM metin hücrelerini xlsx_safe'ten geçirir (kaydetmeden hemen önce).
    Formül olarak işaretlenmiş (data_type='f') hücreler de metne çevrilir — dışa aktarımlarımız
    hiçbir zaman bilerek formül yazmaz. Değiştirilen hücre sayısını döndürür."""
    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str) or not v:
                    continue
                safe = xlsx_safe(v)
                if safe != v or cell.data_type == "f":
                    cell.value = safe
                    if cell.data_type == "f":   # güvenli değer yine '=' ile başlamaz; savunma
                        cell.data_type = "s"
                    changed += 1
    return changed
