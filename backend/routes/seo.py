"""
=============================================================================
seo.py — Dinamik sitemap.xml + robots.txt (app köküne, /api PREFIX'SIZ)
=============================================================================
Google'ın bulabilmesi için bu router api_router'a DEĞİL, doğrudan app'e
include edilir (server.py: app.include_router(seo_router)). Böylece yollar
/sitemap.xml ve /robots.txt olarak kök seviyede yayınlanır.

sitemap.xml: yayında (is_active) ürünler + kategoriler + statik sayfalar +
ana sayfa. URL'ler canonical tenant ayarlarından üretilir.
=============================================================================
"""
from fastapi import APIRouter
from fastapi.responses import Response
from datetime import datetime, timezone
import os
import html

from .deps import db, logger
from tenant_config import get_tenant_config

router = APIRouter(tags=["seo"])  # PREFIX YOK — kök seviye

_FRONTEND = (os.environ.get("FRONTEND_PUBLIC_URL") or "").rstrip("/")


def _esc(u: str) -> str:
    return html.escape(u or "", quote=True)


def _url_block(loc: str, lastmod: str = "", changefreq: str = "weekly", priority: str = "0.6") -> str:
    parts = [f"<loc>{_esc(loc)}</loc>"]
    if lastmod:
        parts.append(f"<lastmod>{lastmod}</lastmod>")
    parts.append(f"<changefreq>{changefreq}</changefreq>")
    parts.append(f"<priority>{priority}</priority>")
    return "<url>" + "".join(parts) + "</url>"


@router.get("/sitemap.xml")
async def sitemap_xml():
    """Yayındaki ürünler, kategoriler ve sayfalardan dinamik sitemap üretir."""
    urls: list[str] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cfg = await get_tenant_config(db)
    storefront = (cfg["domains"].get("storefront_url") or _FRONTEND).rstrip("/")

    # Ana sayfa
    urls.append(_url_block(f"{storefront}/", today, "daily", "1.0"))

    try:
        # ÜYELERE ÖZEL kategoriler ANONİM sitemap'te YER ALMAZ (misafir göremez → indekslenmez).
        from .products import _members_only_cat_ids, members_only_exclusion
        _mo_ids = await _members_only_cat_ids()          # işaretli + TÜM alt kategoriler
        _mo_list = list(_mo_ids)

        # Kategoriler (üyelere-özel ve alt kategorileri hariç)
        async for c in db.categories.find(
            {"is_active": {"$ne": False}, "members_only": {"$ne": True}}, {"_id": 0, "slug": 1, "id": 1}
        ):
            slug = c.get("slug")
            if str(c.get("id")) in _mo_ids:
                continue
            if slug:
                urls.append(_url_block(f"{storefront}/kategori/{slug}", today, "weekly", "0.8"))

        # Statik sayfalar (CMS)
        async for p in db.pages.find(
            {"is_active": {"$ne": False}}, {"_id": 0, "slug": 1}
        ):
            slug = p.get("slug")
            if slug:
                urls.append(_url_block(f"{storefront}/sayfa/{slug}", today, "monthly", "0.4"))

        # Ürünler (yalnızca aktif; üyelere-özel kategori ürünleri HARİÇ) — lastmod = updated_at
        _prod_q = {"is_active": {"$ne": False}}
        if _mo_list:
            _prod_q["$and"] = members_only_exclusion(_mo_list)
        async for prod in db.products.find(
            _prod_q,
            {"_id": 0, "slug": 1, "updated_at": 1},
        ):
            slug = prod.get("slug")
            if not slug:
                continue
            lm = (prod.get("updated_at") or "")[:10] or today
            urls.append(_url_block(f"{storefront}/urun/{slug}", lm, "weekly", "0.7"))
    except Exception as e:
        logger.warning(f"[seo] sitemap üretim hatası: {e}")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/robots.txt")
async def robots_txt():
    """Canonical tenant alan adını kullanan dinamik robots.txt."""
    cfg = await get_tenant_config(db)
    public_base = (cfg["domains"].get("api_url") or cfg["domains"].get("storefront_url") or _FRONTEND).rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /sepet\n"
        "Disallow: /odeme\n"
        "Disallow: /hesabim\n"
        "Disallow: /giris\n"
        "Disallow: /kayit\n"
        "Disallow: /odeme-bildirimi\n"
        "Disallow: /admin\n"
        f"Sitemap: {public_base}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")


@router.get("/llms.txt")
async def llms_txt():
    """AI asistanları için yalnız yapılandırılmış ve yayınlanmış gerçekleri listeler."""
    cfg = await get_tenant_config(db)
    brand = cfg["brand"].get("store_name") or "Mağaza"
    site = (cfg["domains"].get("storefront_url") or _FRONTEND).rstrip("/")
    ig = cfg["contact"].get("instagram") or ""
    tk = cfg["contact"].get("tiktok") or ""
    email = cfg["contact"].get("email") or ""
    lines = [f"# {brand}", ""]
    llms_description = (cfg["seo_geo"].get("llms_description") or "").strip()
    if llms_description:
        lines.append(f"> {llms_description}")
    lines.append("")
    lines.append("## Kategoriler")
    try:
        cats = await db.categories.find(
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}],
             "members_only": {"$ne": True}},
            {"_id": 0, "name": 1, "slug": 1, "parent_id": 1}).to_list(200)
        tops = [c for c in cats if not c.get("parent_id")][:40]
        for c in tops:
            nm = (c.get("name") or "").strip()
            sl = (c.get("slug") or "").strip()
            if nm and sl:
                lines.append(f"- {nm}: {site}/kategori/{sl}")
    except Exception as e:
        logger.warning(f"[llms] kategori listesi alınamadı: {e}")
    lines.append("")
    lines.append("## Bağlantılar")
    lines.append(f"- Ana sayfa: {site}/")
    sitemap_base = (cfg["domains"].get("api_url") or site).rstrip("/")
    lines.append(f"- Tüm ürünler (sitemap): {sitemap_base}/sitemap.xml")
    if ig:
        lines.append(f"- Instagram: {ig}")
    if tk:
        lines.append(f"- TikTok: {tk}")
    if email:
        lines.append(f"- İletişim: {email}")
    lines.append("")
    lines.append("Bu dosya, AI asistanlarının mağazayı ve yayınlanan ürünleri doğru anlaması içindir.")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")
