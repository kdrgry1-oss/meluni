"""
company.py — BEYAZ ETİKET firma kimliği tek kaynağı.

Amaç: Yeni bir firmayı KODDAN DEĞİL ayardan geçir. Firma-özel her değer (mağaza adı,
logo, e-posta/telefon, adres, IBAN, vergi, site adresi, sosyal medya) burada
merkezîleşir. Öncelik zinciri:  settings.main.company_info (admin)  →  settings.main
üst-alanları  →  env (BRAND_* / SITE_URL).

Tüketiciler (email_layout, dogan e-fatura tedarikçi, GPSR üretici, site linkleri) bu
tek kaynaktan okur. Kodda hiçbir markanın adı gömülü değildir; yeni marka açmak
için yalnız env değişkenleri ve panel ayarları değişir.
"""
import os
import time

def _env(key: str, fallback: str = "") -> str:
    return (os.environ.get(key) or "").strip() or fallback


# Marka varsayılanları. Hiçbiri koda gömülü DEĞİL — deployment env'inden gelir,
# panelden (settings.main.company_info) girilen değerler bunları ezer.
# Yeni bir marka açarken kod değişmez, yalnız bu env değişkenleri değişir.
_SITE_URL = _env("SITE_URL", "https://meluni.com.tr").rstrip("/")
_HOST = _SITE_URL.replace("https://", "").replace("http://", "").rstrip("/")
_STORE_NAME = _env("BRAND_STORE_NAME", "MELUNI")

_DEFAULTS = {
    "store_name": _STORE_NAME,
    "company_name": _env("BRAND_COMPANY_NAME", _STORE_NAME),
    "logo_url": _env("BRAND_LOGO_URL", f"{_SITE_URL}/logo.png"),
    "site_url": _SITE_URL,
    "website": _HOST,
    "contact_email": _env("BRAND_CONTACT_EMAIL", f"info@{_HOST}"),
    "email": _env("BRAND_CONTACT_EMAIL", f"info@{_HOST}"),
    "contact_phone": _env("BRAND_CONTACT_PHONE"),
    "phone": _env("BRAND_CONTACT_PHONE"),
    "whatsapp": _env("BRAND_WHATSAPP"),
    "address": _env("BRAND_ADDRESS"),
    "city": _env("BRAND_CITY"),
    "tax_office": _env("BRAND_TAX_OFFICE"),
    "tax_number": _env("BRAND_TAX_NUMBER"),
    "iban": _env("BRAND_IBAN"),
    "instagram": _env("BRAND_INSTAGRAM"),
    "facebook": _env("BRAND_FACEBOOK"),
    "x": _env("BRAND_X"),
    "tiktok": _env("BRAND_TIKTOK"),
}

_cache = None
_cache_at = 0.0
_TTL = 60.0


def defaults() -> dict:
    return dict(_DEFAULTS)


async def get_company(db) -> dict:
    """Firma bilgisini birleştirilmiş döndürür (varsayılan → env/main → company_info).
    60 sn cache'lenir (her e-posta/istekte DB'ye gitmemek için)."""
    global _cache, _cache_at
    now = time.time()
    if _cache is not None and (now - _cache_at) < _TTL:
        return _cache
    try:
        s = await db.settings.find_one(
            {"id": "main"},
            {"_id": 0, "company_info": 1, "site_name": 1, "logo_url": 1,
             "contact_email": 1, "contact_phone": 1, "address": 1}) or {}
    except Exception:
        s = {}
    merged = dict(_DEFAULTS)
    # 1) env (deployment düzeyi) — SITE_URL
    _site_env = os.environ.get("SITE_URL")
    if _site_env:
        merged["site_url"] = _site_env.rstrip("/")
        merged["website"] = _site_env.replace("https://", "").replace("http://", "").rstrip("/")
    # 2) settings.main üst-alanları
    if s.get("site_name"):
        merged["store_name"] = s["site_name"]
    if s.get("logo_url"):
        merged["logo_url"] = s["logo_url"]
    if s.get("contact_email"):
        merged["contact_email"] = merged["email"] = s["contact_email"]
    if s.get("contact_phone"):
        merged["contact_phone"] = merged["phone"] = s["contact_phone"]
    if s.get("address"):
        merged["address"] = s["address"]
    # 3) company_info (en özel — admin doğrudan girer) yalnız dolu alanlar ezer
    ci = s.get("company_info") or {}
    for k, v in ci.items():
        if v not in (None, ""):
            merged[k] = v
    # alias tutarlılığı
    if ci.get("email"):
        merged["email"] = merged["contact_email"] = ci["email"]
    if ci.get("phone"):
        merged["phone"] = merged["contact_phone"] = ci["phone"]
    if ci.get("website"):
        merged["website"] = str(ci["website"]).replace("https://", "").replace("http://", "").rstrip("/")
    if ci.get("store_name"):
        merged["store_name"] = ci["store_name"]
    _cache = merged
    _cache_at = now
    return merged


async def get_site_url(db) -> str:
    """Storefront mutlak taban URL'i (e-posta linkleri için). company.site_url → env → default."""
    c = await get_company(db)
    return (c.get("site_url") or _SITE_URL).rstrip("/")


def invalidate() -> None:
    global _cache_at
    _cache_at = 0.0
