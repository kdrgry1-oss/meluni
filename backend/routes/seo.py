"""
=============================================================================
seo.py — Dinamik sitemap.xml + robots.txt (app köküne, /api PREFIX'SIZ)
=============================================================================
Google'ın bulabilmesi için bu router api_router'a DEĞİL, doğrudan app'e
include edilir (server.py: app.include_router(seo_router)). Böylece yollar
/sitemap.xml ve /robots.txt olarak kök seviyede yayınlanır.

sitemap.xml: yayında (is_active) ürünler + kategoriler + statik sayfalar +
ana sayfa. URL'ler FRONTEND_PUBLIC_URL (varsayılan https://facette.com.tr)
üzerinden üretilir.
=============================================================================
"""
from fastapi import APIRouter
from fastapi.responses import Response
from datetime import datetime, timezone
import os
import html

from .deps import db, logger

router = APIRouter(tags=["seo"])  # PREFIX YOK — kök seviye

_FRONTEND = (os.environ.get("FRONTEND_PUBLIC_URL") or "https://facette.com.tr").rstrip("/")


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

    # Ana sayfa
    urls.append(_url_block(f"{_FRONTEND}/", today, "daily", "1.0"))

    try:
        # Kategoriler
        async for c in db.categories.find(
            {"is_active": {"$ne": False}}, {"_id": 0, "slug": 1}
        ):
            slug = c.get("slug")
            if slug:
                urls.append(_url_block(f"{_FRONTEND}/kategori/{slug}", today, "weekly", "0.8"))

        # Statik sayfalar (CMS)
        async for p in db.pages.find(
            {"is_active": {"$ne": False}}, {"_id": 0, "slug": 1}
        ):
            slug = p.get("slug")
            if slug:
                urls.append(_url_block(f"{_FRONTEND}/sayfa/{slug}", today, "monthly", "0.4"))

        # Ürünler (yalnızca aktif) — lastmod olarak updated_at kullan
        async for prod in db.products.find(
            {"is_active": {"$ne": False}},
            {"_id": 0, "slug": 1, "updated_at": 1},
        ):
            slug = prod.get("slug")
            if not slug:
                continue
            lm = (prod.get("updated_at") or "")[:10] or today
            urls.append(_url_block(f"{_FRONTEND}/urun/{slug}", lm, "weekly", "0.7"))
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
    """Yedek robots.txt (asıl olan frontend/public/robots.txt — facette.com.tr/robots.txt)."""
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
        f"Sitemap: {_FRONTEND}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")
