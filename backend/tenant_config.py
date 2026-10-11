"""Typed, canonical white-label configuration.

This module contains no provider credentials.  Integrations may only keep an
opaque ``secret_ref`` here; secret material belongs to the encrypted vault.

Read precedence is: neutral defaults -> deployment environment -> legacy
settings aliases -> canonical ``settings.id=tenant_config``.  This lets older
installations upgrade without changing existing orders, products or CMS pages.
"""
from __future__ import annotations

import copy
import os
import re
import time
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BrandConfig(_Section):
    store_name: str = "Mağaza"
    logo_url: str = ""
    favicon_url: str = ""


class CompanyLegalConfig(_Section):
    legal_name: str = ""
    tax_office: str = ""
    tax_number: str = ""
    mersis_number: str = ""
    iban: str = ""
    address: str = ""
    city: str = ""
    district: str = ""
    country: str = "Türkiye"


class ContactSocialConfig(_Section):
    email: str = ""
    support_email: str = ""
    phone: str = ""
    whatsapp: str = ""
    instagram: str = ""
    facebook: str = ""
    x: str = ""
    tiktok: str = ""


class DomainsConfig(_Section):
    storefront_url: str = ""
    api_url: str = ""
    cdn_url: str = ""
    allowed_origins: List[str] = Field(default_factory=list)

    @field_validator("storefront_url", "api_url", "cdn_url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not value:
            return ""
        if value.startswith("//"):
            value = "https:" + value
        elif not re.match(r"^https?://", value, flags=re.I):
            # Legacy settings commonly stored only ``example.com``. Treat a
            # hostname as HTTPS, never as a relative path (which produced
            # duplicated canonicals such as example.com/example.com/urun/…).
            if re.match(r"^(localhost|127\.0\.0\.1)(:\d+)?(?:/|$)", value, flags=re.I):
                value = "http://" + value
            elif re.match(r"^[a-z0-9.-]+(?::\d+)?(?:/|$)", value, flags=re.I):
                value = "https://" + value
            else:
                return ""
        return value.rstrip("/")


class OrderNumberingConfig(_Section):
    prefix: str = "ORD"
    manual_prefix: str = "MNL"
    format: str = "{prefix}{timestamp}{random5}"
    invoice_prefix: str = "INV"


class CommerceConfig(_Section):
    currency_code: str = "TRY"
    currency_symbol: str = "₺"
    locale: str = "tr-TR"
    default_vat_rate: float = 10.0
    prices_include_vat: bool = True
    order_numbering: OrderNumberingConfig = Field(default_factory=OrderNumberingConfig)

    @field_validator("currency_code")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return (value or "TRY").strip().upper()[:3]


class ShippingConfig(_Section):
    default_carrier: str = ""
    fee: float = 0.0
    carrier_fees: Dict[str, float] = Field(default_factory=dict)
    free_shipping_threshold: float | None = None
    sender_name: str = ""
    sender_phone: str = ""
    sender_address: str = ""
    sender_city: str = ""
    sender_district: str = ""
    sender_tax_number: str = ""


class CatalogDefaultsConfig(_Section):
    product_brand: str = ""
    marketplace_attributes: Dict[str, Any] = Field(default_factory=dict)


class SeoGeoConfig(_Section):
    default_title: str = ""
    default_description: str = ""
    title_template: str = "{page} | {store_name}"
    product_title_template: str = "{product_name} | {store_name}"
    product_description_template: str = "{product_name}"
    category_title_template: str = "{category_name} | {store_name}"
    category_description_template: str = "{category_name}"
    organization_description: str = ""
    llms_description: str = ""
    default_og_image_url: str = ""
    default_robots: str = "index,follow"
    locale: str = "tr_TR"
    country_code: str = "TR"
    auto_generate_products: bool = True
    auto_generate_categories: bool = True


class MobileConfig(_Section):
    ios_store_url: str = ""
    android_store_url: str = ""
    deep_link_scheme: str = "app"
    support_email: str = ""


class AutomationConfig(_Section):
    timezone: str = "Europe/Istanbul"
    jobs: Dict[str, Any] = Field(default_factory=dict)


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "tenant_config"
    schema_version: int = 1
    brand: BrandConfig = Field(default_factory=BrandConfig)
    company: CompanyLegalConfig = Field(default_factory=CompanyLegalConfig)
    contact: ContactSocialConfig = Field(default_factory=ContactSocialConfig)
    domains: DomainsConfig = Field(default_factory=DomainsConfig)
    commerce: CommerceConfig = Field(default_factory=CommerceConfig)
    shipping: ShippingConfig = Field(default_factory=ShippingConfig)
    catalog_defaults: CatalogDefaultsConfig = Field(default_factory=CatalogDefaultsConfig)
    seo_geo: SeoGeoConfig = Field(default_factory=SeoGeoConfig)
    mobile: MobileConfig = Field(default_factory=MobileConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    secret_refs: Dict[str, str] = Field(default_factory=dict)

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, refs: Dict[str, str]) -> Dict[str, str]:
        """Only opaque vault keys are accepted; never credential-shaped values."""
        out: Dict[str, str] = {}
        for key, value in (refs or {}).items():
            k, v = str(key).strip(), str(value).strip()
            if (k and v.startswith("vault:") and len(k) <= 120 and len(v) <= 120
                    and "\n" not in v and " " not in v):
                out[k] = v
        return out


def _deep_merge(base: dict, patch: dict, *, skip_empty: bool = False) -> dict:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value, skip_empty=skip_empty)
        elif not skip_empty or value not in (None, ""):
            out[key] = copy.deepcopy(value)
    return out


def legacy_aliases(main: dict | None, payment: dict | None = None,
                   store_info: dict | None = None) -> dict:
    """Translate the old settings shape without modifying its source document."""
    main, payment, store_info = main or {}, payment or {}, store_info or {}
    ci = main.get("company_info") or {}
    threshold = main.get("free_shipping_threshold")
    if threshold in (None, ""):
        threshold = main.get("free_shipping_limit")
    return {
        "brand": {
            "store_name": ci.get("store_name") or main.get("site_name"),
            "logo_url": ci.get("logo_url") or main.get("logo_url"),
            "favicon_url": main.get("favicon_url"),
        },
        "company": {
            "legal_name": ci.get("company_name"), "tax_office": ci.get("tax_office"),
            "tax_number": ci.get("tax_number"), "mersis_number": ci.get("mersis_number"),
            "iban": ci.get("iban"), "address": ci.get("address") or main.get("address"),
            "city": ci.get("city"), "district": ci.get("district"), "country": ci.get("country"),
        },
        "contact": {
            "email": ci.get("contact_email") or ci.get("email") or main.get("contact_email"),
            "support_email": ci.get("support_email"),
            "phone": ci.get("contact_phone") or ci.get("phone") or main.get("contact_phone"),
            "whatsapp": ci.get("whatsapp"), "instagram": ci.get("instagram"),
            "facebook": ci.get("facebook"), "x": ci.get("x"), "tiktok": ci.get("tiktok"),
        },
        "domains": {"storefront_url": ci.get("site_url") or main.get("site_url") or ci.get("website")},
        "commerce": {
            "currency_code": main.get("currency_code") or main.get("currency"),
            "currency_symbol": main.get("currency_symbol"),
            "locale": main.get("locale"), "default_vat_rate": main.get("default_vat_rate"),
            "prices_include_vat": main.get("prices_include_vat"),
            "order_numbering": main.get("order_numbering") or {},
        },
        "shipping": {
            "default_carrier": main.get("default_cargo_company"),
            "fee": main.get("shipping_fee"), "carrier_fees": main.get("cargo_fees") or {},
            "free_shipping_threshold": threshold,
            "sender_name": store_info.get("sender_name"), "sender_phone": store_info.get("sender_phone"),
            "sender_address": store_info.get("sender_address"), "sender_city": store_info.get("sender_city"),
            "sender_district": store_info.get("sender_district"),
            "sender_tax_number": store_info.get("sender_tax_no"),
        },
        "catalog_defaults": {"product_brand": main.get("default_product_brand") or ci.get("store_name") or main.get("site_name")},
    }


def legacy_write_through(config: dict) -> dict:
    """Compatibility mirror for consumers not migrated to the resolver yet.

    Canonical data remains authoritative. This patch can be removed after legacy
    read telemetry reaches zero.
    """
    cfg = TenantConfig.model_validate(config).model_dump()
    brand, company, contact = cfg["brand"], cfg["company"], cfg["contact"]
    domains, commerce, shipping = cfg["domains"], cfg["commerce"], cfg["shipping"]
    return {
        "site_name": brand["store_name"], "logo_url": brand["logo_url"],
        "favicon_url": brand["favicon_url"], "site_url": domains["storefront_url"],
        "contact_email": contact["email"], "contact_phone": contact["phone"],
        "address": company["address"], "currency_code": commerce["currency_code"],
        "currency_symbol": commerce["currency_symbol"], "locale": commerce["locale"],
        "default_vat_rate": commerce["default_vat_rate"],
        "prices_include_vat": commerce["prices_include_vat"],
        "order_numbering": commerce["order_numbering"],
        "default_cargo_company": shipping["default_carrier"],
        "cargo_fees": shipping["carrier_fees"], "shipping_fee": shipping["fee"],
        "free_shipping_threshold": shipping["free_shipping_threshold"],
        "free_shipping_limit": shipping["free_shipping_threshold"],
        "default_product_brand": cfg["catalog_defaults"]["product_brand"],
        "company_info": {
            "store_name": brand["store_name"], "logo_url": brand["logo_url"],
            "site_url": domains["storefront_url"], "website": domains["storefront_url"],
            "company_name": company["legal_name"], "tax_office": company["tax_office"],
            "tax_number": company["tax_number"], "mersis_number": company["mersis_number"],
            "iban": company["iban"], "address": company["address"], "city": company["city"],
            "district": company["district"], "country": company["country"],
            "email": contact["email"], "support_email": contact["support_email"],
            "phone": contact["phone"], "whatsapp": contact["whatsapp"],
            "instagram": contact["instagram"], "facebook": contact["facebook"],
            "x": contact["x"], "tiktok": contact["tiktok"],
        },
    }


def environment_defaults() -> dict:
    """Deployment brand defaults; existing panel and canonical settings win."""
    def env(key, fallback=""):
        return (os.environ.get(key) or "").strip() or fallback
    site = env("SITE_URL", env("FRONTEND_PUBLIC_URL", "https://meluni.com.tr")).rstrip("/")
    host = re.sub(r"^https?://", "", site).split("/")[0]
    store = env("BRAND_STORE_NAME", "MELUNI")
    return {
        "brand": {"store_name": store, "logo_url": env("BRAND_LOGO_URL", site + "/logo.png")},
        "company": {"legal_name": env("BRAND_COMPANY_NAME"), "address": env("BRAND_ADDRESS"),
                    "city": env("BRAND_CITY"), "tax_office": env("BRAND_TAX_OFFICE"),
                    "tax_number": env("BRAND_TAX_NUMBER"), "iban": env("BRAND_IBAN")},
        "contact": {"email": env("BRAND_CONTACT_EMAIL", "info@" + host),
                    "phone": env("BRAND_CONTACT_PHONE"), "whatsapp": env("BRAND_WHATSAPP"),
                    "instagram": env("BRAND_INSTAGRAM"), "facebook": env("BRAND_FACEBOOK"),
                    "x": env("BRAND_X"), "tiktok": env("BRAND_TIKTOK")},
        "domains": {"storefront_url": site, "api_url": env("PUBLIC_API_URL"), "cdn_url": env("CDN_URL")},
        "catalog_defaults": {"product_brand": store},
        "commerce": {"order_numbering": {"prefix": env("ORDER_NUMBER_PREFIX", "MEL")}},
        "seo_geo": {"default_title": store + " | Yeni Sezon Kadın Giyim & Moda",
                    "default_description": store + " yeni sezon kadın giyim koleksiyonu.",
                    "default_og_image_url": site + "/og-image.jpg"},
    }


def migration_seed(main: dict | None, payment: dict | None = None,
                   store_info: dict | None = None) -> dict:
    """Build a deployment-compatible seed. It does not write to Mongo."""
    brand_seed = environment_defaults()

    return TenantConfig.model_validate(
        _deep_merge(_deep_merge(TenantConfig().model_dump(), brand_seed),
                    legacy_aliases(main, payment, store_info), skip_empty=True)
    ).model_dump()


_cache: dict[str, tuple[float, dict]] = {}
_TTL_SECONDS = 60.0


def invalidate(db=None) -> None:
    if db is None:
        _cache.clear()
    else:
        _cache.pop(str(getattr(db, "name", id(db))), None)


async def get_tenant_config(db, *, use_cache: bool = True) -> dict:
    key = str(getattr(db, "name", id(db)))
    now = time.monotonic()
    cached = _cache.get(key)
    if use_cache and cached and now - cached[0] < _TTL_SECONDS:
        return copy.deepcopy(cached[1])
    canonical = await db.settings.find_one({"id": "tenant_config"}, {"_id": 0}) or {}
    main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    payment = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    store = await db.settings.find_one({"id": "store_info"}, {"_id": 0}) or {}
    env_layer = environment_defaults()
    merged = _deep_merge(TenantConfig().model_dump(), env_layer, skip_empty=True)
    merged = _deep_merge(merged, legacy_aliases(main, payment, store), skip_empty=True)
    merged = _deep_merge(merged, canonical)
    result = TenantConfig.model_validate(merged).model_dump()
    _cache[key] = (now, result)
    return copy.deepcopy(result)


async def migrate_tenant_config(db, *, apply: bool = False) -> dict:
    """Preview or insert the canonical document. Existing canonical data is untouched."""
    existing = await db.settings.find_one({"id": "tenant_config"}, {"_id": 0})
    if existing:
        return {"action": "none", "reason": "already_exists", "config": existing}
    main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    payment = await db.settings.find_one({"id": "payment"}, {"_id": 0}) or {}
    store = await db.settings.find_one({"id": "store_info"}, {"_id": 0}) or {}
    seed = migration_seed(main, payment, store)
    if apply:
        await db.settings.update_one({"id": "tenant_config"}, {"$setOnInsert": seed}, upsert=True)
        invalidate(db)
    return {"action": "insert" if apply else "would_insert", "config": seed}
