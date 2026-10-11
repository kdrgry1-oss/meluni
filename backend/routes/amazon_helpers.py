"""Amazon entegrasyonu icin yan etkisiz donusum ve hata siniflandirma yardimcilari."""
from __future__ import annotations

import html
import re
from typing import Any

_COLOR_KEYS = {"color", "colour", "renk", "web color", "web renk"}
_SECRET_RE = re.compile(
    r"(?i)(authorization|bearer|access[_-]?token|refresh[_-]?token|client[_-]?secret)"
    r"\s*[:=]?\s*[^\s,;]+"
)


def _color_from_attributes(attributes: Any) -> str:
    if isinstance(attributes, dict):
        for key, raw in attributes.items():
            if isinstance(raw, dict):
                label = raw.get("label") or raw.get("name") or key
                value = raw.get("value") or raw.get("attribute_value")
            else:
                label, value = key, raw
            if str(label or "").strip().lower() in _COLOR_KEYS and value not in (None, ""):
                return str(value).strip()
    elif isinstance(attributes, list):
        for raw in attributes:
            if not isinstance(raw, dict):
                continue
            label = raw.get("label") or raw.get("name") or raw.get("type") or raw.get("attribute_name")
            value = raw.get("value") or raw.get("attribute_value")
            if str(label or "").strip().lower() in _COLOR_KEYS and value not in (None, ""):
                return str(value).strip()
    return ""


def canonical_amazon_color(product: dict, variant: dict | None = None, fallback: str = "") -> str:
    """Rengi varyant -> urun -> attributes -> kanonik ad-cozucu sirasiyla bulur."""
    product, variant = product or {}, variant or {}
    return (
        str(variant.get("color") or "").strip()
        or str(product.get("color") or "").strip()
        or _color_from_attributes(variant.get("attributes"))
        or _color_from_attributes(product.get("attributes"))
        or str(fallback or "").strip()
    )


def amazon_image_candidates(product: dict, variant: dict | None = None) -> list[str]:
    """Amazon galerisi için görselleri sıralar.

    Beden tablosu mağaza vitrininin galerisine bilinçli olarak konmaz; ancak
    pazar yerlerinde son görsel olarak gösterilmelidir. Bu yüzden normal ürün
    görsellerini önce, işaretli beden tablosu görsellerini sonra ekleriz.
    """
    urls: list[str] = []
    size_table_urls: list[str] = []

    def add(raw: Any) -> None:
        if isinstance(raw, str):
            url = raw
        elif isinstance(raw, dict):
            # Video Amazon listing görseli değildir. Ölçü tablosu ise mağazada
            # gizli kalır fakat pazar yeri aktarımında son görsel olmalıdır.
            media_type = str(raw.get("type") or raw.get("media_type") or "").lower()
            if raw.get("is_video") or media_type.startswith("video"):
                return
            url = raw.get("url") or raw.get("src") or raw.get("image")
        else:
            return
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return
        target = size_table_urls if isinstance(raw, dict) and raw.get("is_size_table") else urls
        if url not in urls and url not in size_table_urls:
            target.append(url)

    variant = variant or {}
    for key in ("image", "main_image"):
        add(variant.get(key))
    for image in (variant.get("images") or []):
        add(image)
    for key in ("image", "main_image"):
        add((product or {}).get(key))
    for image in ((product or {}).get("images") or []):
        add(image)
    return (urls + size_table_urls)[:9]


def clean_amazon_text(value: Any) -> str:
    """Site açıklamasındaki HTML'i Amazon'un düz metin alanları için temizler."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def infer_amazon_fabric(value: Any) -> str:
    """Açıklamadaki kumaş bilgisini ürün bazında çıkarır; kategoriye yanlış sabit yazmaz."""
    text = clean_amazon_text(value)
    patterns = (
        r"(?i)kumaş\s*(?:içeriği|türü|tipi|malzemesi)?\s*[:\-]\s*([^\n.;]{2,80})",
        r"(?i)(%\s*\d{1,3}\s*(?:pamuk|polyester|viskon|keten|yün|akrilik|elastan|modal|naylon)(?:\s*[,/+&]\s*%?\s*\d{1,3}\s*[a-zçğıöşü]+)*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" -:•")[:100]
    return ""


def build_amazon_seller_sku(variant: dict, product: dict, color: str = "") -> str:
    """Stok kodu + kanonik renk + beden ile renkler arasinda benzersiz SellerSKU kurar."""
    variant, product = variant or {}, product or {}
    # SellerSKU Listings API yolunun bir parçasıdır. Özellikle M/L gibi bedenlerdeki
    # slash ayrı bir URL segmenti olarak yorumlanıp yanlış endpoint'e gitmemelidir.
    def safe_part(value: Any, *, compact: bool = False, limit: int = 40) -> str:
        value = str(value or "").strip()
        replacement = "" if compact else "-"
        value = re.sub(r"[^A-Za-z0-9ğüşöçıİĞÜŞÖÇ._-]+", replacement, value)
        value = re.sub(r"-+", "-", value).strip("-._")
        return value[:limit]

    stock_code = safe_part(variant.get("stock_code") or product.get("stock_code"))
    size = safe_part(variant.get("size"))
    color_slug = safe_part(color, compact=True, limit=20)
    parts = [part for part in (stock_code, color_slug, size) if part]
    return "-".join(parts) or str(variant.get("barcode") or "").strip()


def pick_existing_amazon_sku(rows: list[dict], generated_sku: str) -> dict | None:
    """Ayni Meluni varyantina ait Amazon SKU kayitlarindan korunacak olani sec.

    Eski kurulumlarda renk buyuk/kucuk harfi veya farkli bir SKU kalibi kullanilmis
    olabilir. EAN'i Amazon'da zaten bir ASIN'e baglanmis bu SKU'yu yeni bir SKU ile
    tekrar yaratmak 8541/13013 ve ikinci bir varyant ailesi uretir. ASIN'i bulunan
    kaydi koru; birden fazlaysa guncel uretilen SKU'yu, sonra aktif kaydi tercih et.
    """
    candidates = [r for r in (rows or []) if str((r or {}).get("sku") or "").strip()]
    if not candidates:
        return None

    def _active(row: dict) -> bool:
        status = row.get("amazon_status") or row.get("listing_status") or ""
        if isinstance(status, list):
            status = " ".join(str(x) for x in status)
        text = str(status).lower()
        return any(word in text for word in ("active", "buyable", "discoverable", "aktif"))

    ranked = sorted(
        candidates,
        key=lambda row: (
            bool(row.get("asin")),
            str(row.get("sku") or "") == str(generated_sku or ""),
            _active(row),
        ),
        reverse=True,
    )
    return ranked[0] if ranked[0].get("asin") else None


def amazon_sibling_query(codes: list[str]) -> dict:
    """_resolve_stock_code'un tum kaynaklariyla ayni kapsama sahip Mongo kardes sorgusu."""
    text_codes = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    numeric_codes = [int(code) for code in text_codes if code.isdigit()]
    card_codes: list[Any] = text_codes + [code for code in numeric_codes if code not in text_codes]
    return {"$or": [
        {"stock_code": {"$in": text_codes}},
        {"sku": {"$in": text_codes}},
        {"variants.stock_code": {"$in": text_codes}},
        {"variants.sku": {"$in": text_codes}},
        {"urun_karti_id": {"$in": card_codes}},
        {"csv_card_id": {"$in": card_codes}},
    ]}


def safe_amazon_failure(res: dict | None = None, error: Exception | str | None = None) -> dict:
    """Amazon hatasindan yalniz kod/mesaj/http alir; token benzeri degerleri maskeler."""
    res = res or {}
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    candidates = data.get("issues") or data.get("errors") or res.get("issues") or []
    first = candidates[0] if isinstance(candidates, list) and candidates else {}
    if not isinstance(first, dict):
        first = {}
    code = first.get("code") or res.get("reject_status") or data.get("status") or "AMAZON_SYNC_ERROR"
    message = first.get("message") or res.get("error") or (str(error) if error else "Amazon stok guncellemesi reddedildi")
    message = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", str(message))[:300]
    return {"code": str(code)[:80], "message": message, "http": res.get("status")}


def amazon_retry_policy(failure_count: int) -> tuple[int, bool]:
    """Ilk 7 hatada katlanan backoff; 8. ve sonrasinda 24 saatlik karantina."""
    count = max(1, int(failure_count or 1))
    if count >= 8:
        return 24 * 60 * 60, True
    return min(6 * 60 * 60, 60 * (2 ** (count - 1))), False
