"""
Ticimax ürün açıklamalarındaki yapılandırılmış (etiketli) özellikleri ÇIKARMA.

Açıklama HTML'i tipik olarak şu kalıbı içerir:

    <p><strong>Etiket Adı:</strong>&nbsp; Değer ...</p>
    <p><strong>Kumaş Bilgisi:</strong>% 100 Polyester</p>
    <p><strong>STD Beden Ölçüleri:</strong> Göğüs: 102 cm Boy: 117 cm ...</p>

Bu parser TÜM etiketleri dinamik olarak yakalar — predefined liste GEREKTİRMEZ.
SADECE ":" ile biten bold metinler "etiket" sayılır; geri kalan bold'lar süsleme
(<strong>STD</strong>, <strong>&nbsp;</strong>) olarak değerlendirilir.

İçerideki ölçü grupları (Beden Ölçüleri / Model Ölçüleri) için alt-alanları da
ayrı `_boy`, `_gogus` gibi composite key'lerle çıkarır.

Çıktı format:
    {
        "kumas_bilgisi":   {"label": "Kumaş Bilgisi",  "value": "% 100 Polyester"},
        "model_olculeri":  {"label": "Model Ölçüleri", "value": "Boy: 1.65 cm..."},
        "model_olculeri_boy":   {"label": "Model Ölçüleri · Boy",   "value": "1.65 cm"},
        ...
    }
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Tuple


_SUB_LABELS = [
    "Boy", "Bel", "Basen", "Göğüs", "Kalça", "Kol Boyu", "Kol",
    "Omuz", "Beden", "Kilo", "Bilek",
]

# "Etiket" sayılması için bold içeriğinin ":" / "：" ile bitmesi şart.
_LABEL_END_RE = re.compile(r"[:：]\s*$")


def _slugify(text: str) -> str:
    """Türkçe karakterleri ASCII'ye indir, snake_case key üret."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = (t.replace("ı", "i").replace("İ", "i")
           .replace("ş", "s").replace("ç", "c")
           .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return t.strip("_") or "ozellik"


def _normalize_html(html_text: str) -> str:
    """HTML'i parser için normalize et: bold/strong'lar tek bir marker'da,
    block-level kapanışlar newline, diğer tag'ler boşluk, entity decode."""
    text = html.unescape(html_text)
    # <br> → newline
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.I)
    # <strong>/<b>: hem açan hem kapanan tag — marker'a çevir
    text = re.sub(r"<\s*(strong|b)\s*>", "‹‹BOLD››", text, flags=re.I)
    text = re.sub(r"</\s*(strong|b)\s*>", "‹‹/BOLD››", text, flags=re.I)
    # Block-level (p/div/li/tr/h*) ve inline (i/em/span) kapanışlarını newline yap
    text = re.sub(
        r"</\s*(p|div|li|tr|h[1-6]|i|em|span)\s*>",
        "\n",
        text,
        flags=re.I,
    )
    # diğer tag'leri boşluk yap
    text = re.sub(r"<[^>]+>", " ", text)
    # NBSP & whitespace normalize
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text


def _merge_adjacent_bolds(text: str) -> str:
    """`‹‹/BOLD›› ‹‹BOLD››` gibi yan yana kapanıp açılan boldları (sadece arada
    whitespace varsa) birleştir. Böylece `<strong>STD</strong><strong>&nbsp;</strong><strong>Beden Ölçüleri:</strong>`
    → `‹‹BOLD››STD Beden Ölçüleri:‹‹/BOLD››` olur."""
    pattern = re.compile(r"‹‹/BOLD››\s*‹‹BOLD››", re.DOTALL)
    prev = None
    while prev != text:
        prev = text
        text = pattern.sub(" ", text)
    return text


def _split_sub_attrs(text: str) -> dict[str, str]:
    """
    "Bel: 64 cm Basen: 88 cm Boy: 1.65 cm Kilo: 49 Kg" gibi inline ölçüleri
    alt-anahtarlar olarak çıkar.
    """
    out: dict[str, str] = {}
    if not text:
        return out
    label_alt = "|".join(re.escape(lbl) for lbl in _SUB_LABELS)
    pattern = re.compile(
        rf"\b({label_alt})\s*[:\-]\s*"
        rf"([0-9][0-9.,]*\s*(?:cm|m|kg|Kg|KG|kilo)?(?:\s*(?:beden|Beden))?)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        lbl = m.group(1)
        val = re.sub(r"\s+", " ", m.group(2)).strip()
        slug = _slugify(lbl)
        if slug not in out and val:
            out[slug] = val[:120]
    return out


def parse_description_attributes(html_text: str) -> Tuple[dict, str]:
    """
    Açıklama HTML'inden TÜM `<strong>Etiket:</strong>Değer` kalıplarını dinamik
    olarak çıkarır. Predefined liste gerekmez. Bold markup yoksa plain-text
    "Label: value" satırlarına da bakar (fallback).

    Returns:
        (attributes_dict, plain_text)
    """
    if not html_text:
        return {}, ""

    text = _normalize_html(html_text)
    text = _merge_adjacent_bolds(text)

    bold_re = re.compile(r"‹‹BOLD››(.*?)‹‹/BOLD››", re.DOTALL)
    matches = list(bold_re.finditer(text))
    attrs: dict = {}

    for i, m in enumerate(matches):
        bold_content = m.group(1).strip()
        if not _LABEL_END_RE.search(bold_content):
            continue
        lbl = bold_content.rstrip(": ").rstrip("：").strip()
        if not lbl or len(lbl) > 60:
            continue
        start = m.end()
        end = len(text)
        for j in range(i + 1, len(matches)):
            nxt = matches[j]
            if _LABEL_END_RE.search(nxt.group(1).strip()):
                end = nxt.start()
                break
        raw_value = text[start:end]
        raw_value = bold_re.sub(r"\1", raw_value)
        raw_value = raw_value.replace("‹‹BOLD››", "").replace("‹‹/BOLD››", "")
        val = re.sub(r"\s+", " ", raw_value).strip().strip(" -·•:|").strip()
        if not val:
            continue
        slug = _slugify(lbl)
        if slug in attrs:
            continue
        attrs[slug] = {"label": lbl, "value": val[:1000]}
        low = lbl.lower()
        if "ölçü" in low or "olcu" in low:
            for sk, sv in _split_sub_attrs(val).items():
                composite = f"{slug}_{sk}"
                if composite not in attrs:
                    attrs[composite] = {
                        "label": f"{lbl} · {sk.capitalize()}",
                        "value": sv,
                    }

    # Fallback: bold/markup yok ise (ya da çok az etiket bulunduysa)
    # plain-text içinde "Label: value" satır kalıbına bak.
    if len(attrs) < 2:
        plain = re.sub(r"‹‹/?BOLD››", "", text)
        plain = re.sub(r"[ \t]+", " ", plain)
        # Her satırı kontrol et
        for raw_line in plain.split("\n"):
            line = raw_line.strip()
            # Leading bullet/dash karakterlerini at: •, ·, ○, -, *, →, »
            line = re.sub(r"^[•·○◦▪▫\-*→»–—]+\s*", "", line).strip()
            if not line:
                continue
            # "Label: value" pattern — label en fazla 5 kelime ve sadece
            # harf/türkçe/boşluk/parantez içersin
            m = re.match(
                r"^([A-Za-zÇĞİıÖŞÜçğıöşü][A-Za-zÇĞİıÖŞÜçğıöşü0-9 ()/]{1,50}?)\s*[:：]\s+(.+)$",
                line,
            )
            if not m:
                continue
            lbl = m.group(1).strip().rstrip(":：")
            val = m.group(2).strip().strip(" -·•:|").strip()
            # Aşırı uzun label'ı veya rakam ağırlıklı olanı atla
            if not lbl or len(lbl.split()) > 6:
                continue
            if not val or len(val) < 1:
                continue
            slug = _slugify(lbl)
            if slug in attrs:
                continue
            attrs[slug] = {"label": lbl, "value": val[:1000]}
            low = lbl.lower()
            if "ölçü" in low or "olcu" in low:
                for sk, sv in _split_sub_attrs(val).items():
                    composite = f"{slug}_{sk}"
                    if composite not in attrs:
                        attrs[composite] = {
                            "label": f"{lbl} · {sk.capitalize()}",
                            "value": sv,
                        }

    plain = re.sub(r"<[^>]+>", " ", html.unescape(html_text))
    plain = re.sub(r"\s+", " ", plain).strip()
    return attrs, plain
