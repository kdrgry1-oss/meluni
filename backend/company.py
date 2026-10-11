"""Backward-compatible company facade over the canonical tenant config.

Amaç: Yeni bir firmayı KODDAN DEĞİL ayardan geçir. Firma-özel her değer (mağaza adı,
logo, e-posta/telefon, adres, IBAN, vergi, site adresi, sosyal medya) burada
merkezîleşir. Canonical tenant_config bulunmazsa eski settings alanları geriye
uyumlu okunur.

Tüketiciler (email_layout, dogan e-fatura tedarikçi, GPSR üretici, site linkleri) bu
tek kaynaktan okur.
"""
from tenant_config import get_tenant_config, invalidate as _invalidate_tenant


def defaults() -> dict:
    """Neutral defaults. Brand-specific fallbacks live only in migration_seed()."""
    return {
        "store_name": "Mağaza", "company_name": "", "logo_url": "",
        "site_url": "", "website": "", "contact_email": "", "email": "",
        "contact_phone": "", "phone": "", "whatsapp": "", "address": "",
        "city": "", "tax_office": "", "tax_number": "", "iban": "",
        "instagram": "", "facebook": "", "x": "", "tiktok": "",
    }


async def get_company(db) -> dict:
    """Firma bilgisini birleştirilmiş döndürür (varsayılan → env/main → company_info).
    60 sn cache'lenir (her e-posta/istekte DB'ye gitmemek için)."""
    cfg = await get_tenant_config(db)
    brand, legal = cfg["brand"], cfg["company"]
    contact, domains = cfg["contact"], cfg["domains"]
    site = domains.get("storefront_url") or ""
    return {
        "store_name": brand.get("store_name") or "Mağaza",
        "company_name": legal.get("legal_name") or "",
        "logo_url": brand.get("logo_url") or "", "site_url": site,
        "website": site.replace("https://", "").replace("http://", "").rstrip("/"),
        "contact_email": contact.get("email") or "", "email": contact.get("email") or "",
        "contact_phone": contact.get("phone") or "", "phone": contact.get("phone") or "",
        "whatsapp": contact.get("whatsapp") or "", "address": legal.get("address") or "",
        "city": legal.get("city") or "", "tax_office": legal.get("tax_office") or "",
        "tax_number": legal.get("tax_number") or "", "iban": legal.get("iban") or "",
        "instagram": contact.get("instagram") or "", "facebook": contact.get("facebook") or "",
        "x": contact.get("x") or "", "tiktok": contact.get("tiktok") or "",
    }


async def get_site_url(db) -> str:
    """Storefront mutlak taban URL'i (e-posta linkleri için). company.site_url → env → default."""
    c = await get_company(db)
    return (c.get("site_url") or "").rstrip("/")


def invalidate() -> None:
    _invalidate_tenant()
