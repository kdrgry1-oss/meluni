"""Deterministic SEO/GEO resolver backed by canonical tenant_config.

Only supplied catalog/company facts and configured templates are used. Manual
path or entity overrides always win; missing data is omitted, never invented.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict


_TOKEN = re.compile(r"\{([a-z_]+)\}")
_ALLOWED = {
    "store_name", "company_name", "product_name", "category_name", "brand",
    "price", "currency", "city", "country",
}


def clean_text(value: Any, limit: int = 160) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:limit].rstrip() + "…") if len(text) > limit else text


def render_template(template: str, facts: Dict[str, Any], limit: int) -> str:
    """Render allow-listed facts. Unknown tokens and absent facts become empty."""
    def repl(match):
        key = match.group(1)
        return clean_text(facts.get(key), limit) if key in _ALLOWED else ""

    rendered = _TOKEN.sub(repl, str(template or ""))
    rendered = re.sub(r"\s+", " ", rendered).strip(" |,.-")
    return clean_text(rendered, limit)


def _absolute_image(value: Any) -> str:
    value = str(value or "").strip()
    return value if value.startswith(("https://", "http://")) else ""


def _images(entity: dict) -> list[str]:
    result = []
    for item in entity.get("images") or []:
        value = item if isinstance(item, str) else (
            item.get("url") or item.get("src") or item.get("image") if isinstance(item, dict) else ""
        )
        value = _absolute_image(value)
        if value and value not in result:
            result.append(value)
    single = _absolute_image(entity.get("image"))
    if single and single not in result:
        result.insert(0, single)
    return result


def _base_facts(config: dict) -> dict:
    return {
        "store_name": config["brand"].get("store_name"),
        "company_name": config["company"].get("legal_name"),
        "city": config["company"].get("city"),
        "country": config["company"].get("country"),
        "currency": config["commerce"].get("currency_code"),
    }


def _apply_override(result: dict, override: dict | None) -> dict:
    override = override or {}
    if override.get("title"):
        result["title"] = result["og_title"] = clean_text(override["title"], 200)
        result["sources"]["title"] = "path_override"
    if override.get("description"):
        result["description"] = result["og_description"] = clean_text(override["description"], 400)
        result["sources"]["description"] = "path_override"
    if _absolute_image(override.get("og_image")):
        result["og_image"] = override["og_image"]
        result["sources"]["og_image"] = "path_override"
    if override.get("noindex"):
        result["robots"] = "noindex,nofollow"
    return result


def resolve_product(product: dict, config: dict, *, override: dict | None = None) -> dict:
    seo = config["seo_geo"]
    site = (config["domains"].get("storefront_url") or "").rstrip("/")
    slug = product.get("slug") or product.get("id")
    name = clean_text(product.get("name"), 200)
    canonical = f"{site}/urun/{slug}" if site and slug else ""
    facts = {**_base_facts(config), "product_name": name,
             "category_name": product.get("category_name"),
             "brand": product.get("brand") or config["catalog_defaults"].get("product_brand"),
             "price": product.get("sale_price") if product.get("sale_price") is not None else product.get("price")}

    manual_title = clean_text(product.get("meta_title"), 200)
    manual_desc = clean_text(product.get("meta_description"), 400)
    if manual_title:
        title, title_source = manual_title, "entity_manual"
    elif seo.get("auto_generate_products"):
        title, title_source = render_template(seo.get("product_title_template"), facts, 200), "template"
    else:
        title, title_source = name, "fact"
    if manual_desc:
        description, desc_source = manual_desc, "entity_manual"
    elif product.get("seo_description") or product.get("description"):
        description = clean_text(product.get("seo_description") or product.get("description"), 400)
        desc_source = "entity_content"
    elif seo.get("auto_generate_products"):
        description = render_template(seo.get("product_description_template"), facts, 400)
        desc_source = "template"
    else:
        description, desc_source = "", "missing"

    images = _images(product)
    og_image = images[0] if images else _absolute_image(seo.get("default_og_image_url"))
    result = {
        "found": True, "type": "product", "title": title, "description": description,
        "canonical": canonical, "og_title": title, "og_description": description,
        "og_url": canonical, "og_image": og_image, "og_type": "product",
        "og_site_name": config["brand"].get("store_name") or "",
        "og_locale": seo.get("locale") or "",
        "robots": seo.get("default_robots") or "index,follow",
        "sources": {"title": title_source, "description": desc_source,
                    "og_image": "entity" if images else ("global" if og_image else "missing")},
    }

    product_ld = {"@context": "https://schema.org", "@type": "Product", "name": name,
                  "url": canonical, "image": images or None, "description": description or None,
                  "sku": product.get("barcode") or product.get("stock_code") or product.get("sku") or None}
    brand = facts.get("brand")
    if brand:
        product_ld["brand"] = {"@type": "Brand", "name": brand}
    price = facts.get("price")
    if isinstance(price, (int, float)):
        offer = {"@type": "Offer", "url": canonical or None,
                 "priceCurrency": config["commerce"].get("currency_code"), "price": str(price)}
        variants = product.get("variants")
        if isinstance(variants, list) and variants:
            in_stock = any(isinstance(v, dict) and (v.get("stock") or 0) > 0 for v in variants)
            offer["availability"] = "https://schema.org/InStock" if in_stock else "https://schema.org/OutOfStock"
        elif product.get("stock") is not None:
            offer["availability"] = ("https://schema.org/InStock" if (product.get("stock") or 0) > 0
                                     else "https://schema.org/OutOfStock")
        product_ld["offers"] = offer
    product_ld = {k: v for k, v in product_ld.items() if v not in (None, "", [])}

    crumbs = []
    if site:
        crumbs.append({"name": "Ana Sayfa", "item": site})
    if product.get("category_name") and product.get("category_slug") and site:
        crumbs.append({"name": clean_text(product["category_name"], 200),
                       "item": f"{site}/kategori/{product['category_slug']}"})
    if name and canonical:
        crumbs.append({"name": name, "item": canonical})
    jsonld = [product_ld]
    if crumbs:
        jsonld.append({"@context": "https://schema.org", "@type": "BreadcrumbList",
                       "itemListElement": [{"@type": "ListItem", "position": i + 1, **c}
                                           for i, c in enumerate(crumbs)]})
    result["jsonld"] = jsonld
    return _apply_override(result, override)


def resolve_category(category: dict, config: dict, *, override: dict | None = None) -> dict:
    seo = config["seo_geo"]
    site = (config["domains"].get("storefront_url") or "").rstrip("/")
    slug = category.get("slug")
    name = clean_text(category.get("name"), 200)
    canonical = f"{site}/kategori/{slug}" if site and slug else ""
    facts = {**_base_facts(config), "category_name": name}
    manual_title = clean_text(category.get("meta_title"), 200)
    manual_desc = clean_text(category.get("meta_description"), 400)
    title = manual_title or (render_template(seo.get("category_title_template"), facts, 200)
                             if seo.get("auto_generate_categories") else name)
    description = manual_desc or clean_text(category.get("description"), 400)
    desc_source = "entity_manual" if manual_desc else ("entity_content" if description else "missing")
    if not description and seo.get("auto_generate_categories"):
        _tpl = (seo.get("category_description_template") or "").strip()
        # Şablon hiç düzenlenmemiş (yalnız kategori adı) ise arama sonucunda tek kelimelik açıklama
        # çıkıyordu ("Etek") → daha açıklayıcı genel şablon. Panelden yazılan şablon aynen kullanılır.
        if _tpl in ("", "{category_name}"):
            _tpl = ("{category_name} modelleri ve yeni sezon ürünleri {store_name}'te. "
                    "Hızlı kargo, güvenli ödeme ve kolay iade.")
        description = render_template(_tpl, facts, 400)
        desc_source = "template"
    og_image = _absolute_image(category.get("image")) or _absolute_image(seo.get("default_og_image_url"))
    result = {
        "found": True, "type": "category", "title": title, "description": description,
        "canonical": canonical, "og_title": title, "og_description": description,
        "og_url": canonical, "og_image": og_image, "og_type": "website",
        "og_site_name": config["brand"].get("store_name") or "",
        "og_locale": seo.get("locale") or "",
        "robots": seo.get("default_robots") or "index,follow",
        "sources": {"title": "entity_manual" if manual_title else ("template" if title != name else "fact"),
                    "description": desc_source,
                    "og_image": "entity" if _absolute_image(category.get("image")) else ("global" if og_image else "missing")},
    }
    collection = {"@context": "https://schema.org", "@type": "CollectionPage",
                  "name": name, "url": canonical, "description": description or None}
    crumbs = ([{"name": "Ana Sayfa", "item": site}, {"name": name, "item": canonical}]
              if site and canonical and name else [])
    result["jsonld"] = [{k: v for k, v in collection.items() if v not in (None, "") }]
    if crumbs:
        result["jsonld"].append({"@context": "https://schema.org", "@type": "BreadcrumbList",
                                 "itemListElement": [{"@type": "ListItem", "position": i + 1, **c}
                                                     for i, c in enumerate(crumbs)]})
    return _apply_override(result, override)
