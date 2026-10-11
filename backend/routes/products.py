"""
Product routes - CRUD, search, filtering
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from typing import List, Optional
from datetime import datetime, timezone
import re

from .deps import limiter, global_rate_key
from security.ai_budget import ai_slot as _ai_slot
from .deps import db, logger, get_current_user, require_admin, generate_id, generate_short_id, generate_barcode_from_range, build_used_barcode_set, generate_urun_karti_id, build_used_urun_id_set, next_urun_id, urun_id_owner_map, normalize_variant_ids, _search_tr_regex, tr_day_start_utc, tr_day_end_utc, require_permission
from stock_audit import record_stock_audit
from activity_audit import record_admin_audit
from product_schema import BOOL_COLS as PRODUCT_BOOL_COLS
from fastapi import Response, UploadFile, File, Form
import pandas as pd
import io

router = APIRouter(prefix="/products", tags=["Products"])


def _fire_stock_zero_on_passive(product: dict):
    """Ürün PASİFE alınınca (is_active True→FALSE) pazaryerlerine 0 stok gönderimini
    FIRE-AND-FORGET başlatır — kullanıcı yanıtını BLOKLAMAZ, DB stoğuna DOKUNMAZ.
    Import fonksiyon-içi (modül-seviyesi döngüsel import / başlatma riski YOK)."""
    try:
        import asyncio
        from .integrations_common import on_product_deactivated
        asyncio.create_task(on_product_deactivated(dict(product or {})))
    except Exception as _e:
        logger.warning(f"[pasif-stok0] görev başlatılamadı ({(product or {}).get('id')}): {_e}")


# ---------------------------------------------------------------------------
# XML ürün feed'leri (Google Merchant / Facebook Katalog / Genel)
# Çoklu feed: her feed bir "target" ile public URL üretir → /products/feed/<slug>.xml
# google/generic = ürün-seviyesi (g:id = ürün id)
# facebook        = varyant-seviyesi (g:id = varyant id, item_group_id = ürün id,
#                    g:size/g:color) → Meta pixel content_id'leriyle birebir eşleşir.
# ---------------------------------------------------------------------------
import html as _html


_LABEL_SLOTS = (0, 1, 2, 3, 4)


def _normalize_label_rules(rules):
    """Feed custom_label kurallarını temizler.
    Kural: {"label": 0..4, "value": "AW26", "all": bool, "category_ids": [str,...]}.
    Boş değerli / kapsamsız kurallar atılır. Aynı slot için ilk eşleşen kural kazanır."""
    out = []
    for r in (rules or []):
        if not isinstance(r, dict):
            continue
        try:
            n = int(r.get("label", 0))
        except Exception:
            continue
        if n not in _LABEL_SLOTS:
            continue
        val = str(r.get("value") or "").strip()[:100]
        if not val:
            continue
        cids = [str(x).strip() for x in (r.get("category_ids") or []) if x not in (None, "")]
        is_all = bool(r.get("all")) or not cids
        out.append({"label": n, "value": val, "all": is_all, "category_ids": cids})
    return out


def _custom_labels_for(pr, rules):
    """Ürünün custom_label_N değerleri: kategori üyeliği (birincil + category_ids, atalar dâhil)."""
    if not rules:
        return {}
    cats = {str(x) for x in (pr.get("category_ids") or []) if x is not None}
    if pr.get("category_id") not in (None, ""):
        cats.add(str(pr.get("category_id")))
    labels = {}
    for r in rules:
        n = r["label"]
        if n in labels:
            continue
        if r["all"] or (cats & set(r["category_ids"])):
            labels[n] = r["value"]
    return labels


def _build_merchant_xml(prods, site, shop, target="google", in_stock_only=False, group_variants=False, label_rules=None):
    """Ürün listesinden Google/Facebook uyumlu RSS 2.0 (g:) XML üretir.

    group_variants=True iken (google/generic modunda) her ürün satırına
    Meta varyasyon gruplaması için EK alanlar yazılır:
      <g:item_group_id> (parent kod) + <g:color>. (g:size BASILMAZ — renk-düzeyi feed.)
    g:id ve mevcut alanlar AYNEN korunur — sadece ekleme yapılır.
    Tek hamlede geri alınabilir (feature-flag / ?group=off).

    label_rules: feed'e özel custom_label_0..4 kuralları (bkz. _normalize_label_rules).
    Google Ads / Meta'da reklam verilen ürün gruplarını ayırt etmek ve raporlamak için
    her ürüne <g:custom_label_N> yazar (kategori kapsamı veya tüm ürünler)."""
    target = (target or "google").lower()
    label_rules = _normalize_label_rules(label_rules)

    def esc(x):
        return _html.escape(str(x if x is not None else ""), quote=True)

    def first_image(pr):
        for im in (pr.get("images") or []):
            if isinstance(im, str) and im:
                return im
            if isinstance(im, dict):
                u = im.get("url") or im.get("src") or im.get("image")
                if u:
                    return u
        return pr.get("image") or ""

    def abs_url(u):
        if not u:
            return ""
        if str(u).startswith("http"):
            return u
        return site + ("" if str(u).startswith("/") else "/") + str(u)

    def fnum(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    items = []
    for pr in prods:
        pid = pr.get("id") or pr.get("slug")
        if not pid:
            continue
        slug = pr.get("slug") or pid
        link = f"{site}/urun/{slug}"
        img = abs_url(first_image(pr))
        price = fnum(pr.get("price"))
        sale = pr.get("sale_price")
        sale = fnum(sale) if sale not in (None, "", 0) else None
        name = (pr.get("name") or "")[:150]
        desc = re.sub(r"<[^>]+>", " ", str(pr.get("description") or pr.get("short_description") or pr.get("name") or ""))
        desc = re.sub(r"\s+", " ", desc).strip()[:4500] or name
        brand = pr.get("brand") or shop
        cat = pr.get("category_name") or ""
        pcolor = pr.get("color") or ""
        variants = pr.get("variants") or []
        labels = _custom_labels_for(pr, label_rules)

        def price_rows(p_price, p_sale):
            r = []
            if p_sale and p_sale > 0 and p_price and p_sale < p_price:
                r.append(f"<g:price>{p_price:.2f} TRY</g:price>")
                r.append(f"<g:sale_price>{p_sale:.2f} TRY</g:sale_price>")
            else:
                eff = p_sale if (p_sale and p_sale > 0) else p_price
                r.append(f"<g:price>{eff:.2f} TRY</g:price>")
            return r

        def common_rows(item_id, avail, gtin, mpn, extra=None):
            r = [
                f"<g:id>{esc(item_id)}</g:id>",
                f"<g:title>{esc(name)}</g:title>",
                f"<g:description>{esc(desc)}</g:description>",
                f"<g:link>{esc(link)}</g:link>",
            ]
            if img:
                r.append(f"<g:image_link>{esc(img)}</g:image_link>")
            r.append(f"<g:availability>{avail}</g:availability>")
            r.append("<g:condition>new</g:condition>")
            if brand:
                r.append(f"<g:brand>{esc(brand)}</g:brand>")
            has_id = False
            if gtin and str(gtin).isdigit() and len(str(gtin)) in (8, 12, 13, 14):
                r.append(f"<g:gtin>{esc(gtin)}</g:gtin>")
                has_id = True
            if mpn:
                r.append(f"<g:mpn>{esc(mpn)}</g:mpn>")
                has_id = True
            if not has_id:
                r.append("<g:identifier_exists>no</g:identifier_exists>")
            if cat:
                r.append(f"<g:product_type>{esc(cat)}</g:product_type>")
            for _n, _val in labels.items():
                r.append(f"<g:custom_label_{_n}>{esc(_val)}</g:custom_label_{_n}>")
            if extra:
                r.extend(extra)
            return r

        # ---- Facebook: varyant (beden) bazlı satırlar ----
        if target == "facebook" and variants:
            for v in variants:
                vid = v.get("id")
                vstock = 0
                try:
                    vstock = int(v.get("stock") or 0)
                except Exception:
                    vstock = 0
                if in_stock_only and vstock <= 0:
                    continue
                vsize = v.get("size") or ""
                vcolor = v.get("color") or pcolor
                vbarcode = (v.get("barcode") or "").strip()
                item_id = str(vid) if (vid is not None and str(vid) != "") else f"{pid}-{esc(vsize)}"
                vprice = fnum(v.get("price")) or price
                vsale = v.get("sale_price")
                vsale = fnum(vsale) if vsale not in (None, "", 0) else sale
                avail = "in stock" if vstock > 0 else "out of stock"
                extra = [f"<g:item_group_id>{esc(pid)}</g:item_group_id>"]
                if vsize:
                    extra.append(f"<g:size>{esc(vsize)}</g:size>")
                if vcolor:
                    extra.append(f"<g:color>{esc(vcolor)}</g:color>")
                rows = common_rows(item_id, avail, vbarcode, (pr.get("stock_code") or pr.get("sku") or "").strip(), extra)
                rows.extend(price_rows(vprice, vsale))
                items.append("<item>" + "".join(rows) + "</item>")
            continue

        # ---- Google / Generic: ürün-seviyesi tek satır ----
        stock = pr.get("stock") or 0
        if variants:
            # DENETİM FIX: varyant varsa ürün-seviyesi stoğu KOŞULSUZ varyant toplamıyla değiştir.
            # Eski `if vsum:` — tüm varyantlar 0 iken (vsum=0 falsy) bayat ürün-seviyesi stoğu
            # (ör. 10) kalıyor → tükenen ürün feed'de 'in stock' görünüp oversell'e yol açıyordu.
            try:
                stock = sum(int(v.get("stock") or 0) for v in variants)
            except Exception:
                stock = 0
        if in_stock_only and not (stock and stock > 0):
            continue
        avail = "in stock" if (stock and stock > 0) else "out of stock"
        gtin = (pr.get("barcode") or "").strip()
        mpn = (pr.get("stock_code") or pr.get("sku") or "").strip()
        # ---- Meta varyasyon gruplaması (flag arkasında, sadece EKLEME) ----
        extra = None
        if group_variants:
            extra = []
            # item_group_id: aynı ana ürünün renkleri aynı değeri paylaşır.
            # Öncelik mpn (renkler aynı stok kodunu paylaşıyor) → fallback parent kart.
            grp_id = mpn or str(pr.get("csv_card_id") or pr.get("urun_karti_id") or "").strip()
            if grp_id:
                extra.append(f"<g:item_group_id>{esc(grp_id)}</g:item_group_id>")
            # color: ürün rengi (split sonrası dolu); yoksa tek distinct varyant rengi.
            vcolors = []
            for _v in variants:
                _c = (_v.get("color") or "").strip()
                if _c and _c.lower() not in [x.lower() for x in vcolors]:
                    vcolors.append(_c)
            gcolor = (pcolor or "").strip() or (vcolors[0] if len(vcolors) == 1 else "")
            if gcolor:
                extra.append(f"<g:color>{esc(gcolor)}</g:color>")
            # size: BASILMAZ. Renk-düzeyi feed → varyantları item_group_id grupluyor; size'a gerek yok.
            # (Meta kontrol raporu 2026-06-18: tutarsız "STD" etiketlerini temizle, hiç size yazma.)
        rows = common_rows(pid, avail, gtin, mpn, extra)
        rows.extend(price_rows(price, sale))
        items.append("<item>" + "".join(rows) + "</item>")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>'
        f"<title>{esc(shop)}</title><link>{esc(site)}</link>"
        f"<description>{esc(shop)} urun feed</description>"
        + "".join(items) +
        "</channel></rss>"
    )


async def _feed_site_shop_prods():
    main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    site = (main.get("site_url") or "https://meluni.com.tr").rstrip("/")
    shop = main.get("site_name") or "MELUNI"
    _q = {"is_active": True, "is_deleted": {"$ne": True}}
    # ÜYELERE ÖZEL: feed'ler ANONİM (üye kavramı yok) → members_only ürünleri HER ZAMAN hariç.
    _mo_ids = await _members_only_cat_ids()
    if _mo_ids:
        _q["$and"] = members_only_exclusion(_mo_ids)
    # Denetim #23: yalnız feed'in kullandığı alanlar okunur (tam belge yerine dar projeksiyon).
    prods = await db.products.find(_q, _FEED_PROJECTION).to_list(50000)
    return site, shop, prods


_FEED_PROJECTION = {"_id": 0, **{k: 1 for k in (
    "id", "slug", "name", "description", "short_description", "price", "sale_price", "images", "image",
    "brand", "category_name", "category_id", "category_ids", "categories", "color", "variants", "stock",
    "barcode", "sku", "stock_code", "csv_card_id", "urun_karti_id")}}
# Denetim #23: üretilen XML süreç içinde önbelleklenir (TTL) ve aynı anahtar için TEK üretim çalışır
# (single-flight) — kimliksiz tekrar istekler veritabanını/işçileri tüketemez.
_FEED_CACHE: dict = {}
_FEED_LOCKS: dict = {}
_FEED_TTL_S = 600


async def _cached_feed_xml(key: str, build):
    import asyncio as _aio_c
    import time as _t
    hit = _FEED_CACHE.get(key)
    if hit and hit[0] > _t.monotonic():
        return hit[1]
    lock = _FEED_LOCKS.setdefault(key, _aio_c.Lock())
    async with lock:
        hit = _FEED_CACHE.get(key)
        if hit and hit[0] > _t.monotonic():
            return hit[1]
        xml = await build()
        if len(_FEED_CACHE) > 64:
            _FEED_CACHE.clear()
        _FEED_CACHE[key] = (_t.monotonic() + _FEED_TTL_S, xml)
        return xml


def _feed_response(xml):
    return Response(content=xml, media_type="application/xml; charset=utf-8",
                    headers={"Cache-Control": f"public, max-age={_FEED_TTL_S}"})


async def _feed_grouping_enabled(request=None):
    """Meta varyasyon gruplaması açık mı? (item_group_id/color/size üretimi)
    Geri alma: settings.main.feed_variant_grouping=False  VEYA  ?group=off query.
    Varsayılan: AÇIK (alanlar sadece eklenir, g:id sabit kalır)."""
    if request is not None:
        q = (request.query_params.get("group") or "").strip().lower()
        if q in ("0", "off", "false", "no", "kapali"):
            return False
        if q in ("1", "on", "true", "yes", "acik"):
            return True
    main = await db.settings.find_one({"id": "main"}, {"_id": 0}) or {}
    if main.get("feed_variant_grouping") is False:
        return False
    return True


@router.get("/google-merchant-feed.xml")
@(limiter.limit("30/minute") if limiter else (lambda f: f))
@(limiter.limit("240/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def google_merchant_feed(request: Request):
    """Geriye-uyumlu varsayilan feed (tum aktif urunler, urun-seviyesi)."""
    grp = await _feed_grouping_enabled(request)

    async def _build():
        site, shop, prods = await _feed_site_shop_prods()
        # CPU-yoğun XML üretimi iş parçacığında — olay döngüsü (checkout dahil) beklemesin.
        import asyncio as _aio_x
        return await _aio_x.to_thread(_build_merchant_xml, prods, site, shop, "google", False, group_variants=grp)
    return _feed_response(await _cached_feed_xml(f"default|{grp}", _build))


@router.get("/feed/{slug}.xml")
@(limiter.limit("30/minute") if limiter else (lambda f: f))
@(limiter.limit("240/minute", key_func=global_rate_key) if limiter else (lambda f: f))
async def dynamic_feed(slug: str, request: Request):
    """Yapilandirilmis XML feed (slug ile). target'a gore urun/varyant seviyesi."""
    feed = await db.xml_feeds.find_one({"slug": slug}, {"_id": 0})
    if not feed or not feed.get("enabled", True):
        return Response(content='<?xml version="1.0"?><error>feed not found</error>',
                        media_type="application/xml", status_code=404)
    grp = await _feed_grouping_enabled(request)

    async def _build():
        site, shop, prods = await _feed_site_shop_prods()
        import asyncio as _aio_x
        return await _aio_x.to_thread(_build_merchant_xml, prods, site, shop, feed.get("target", "google"),
                                      bool(feed.get("in_stock_only")), group_variants=grp,
                                      label_rules=feed.get("custom_labels"))
    # Anahtar feed ayarlarının özetini içerir → panelde ayar değişince eski önbellek kullanılmaz.
    import hashlib as _hl, json as _js
    _sig = _hl.sha256(_js.dumps({k: feed.get(k) for k in ("target", "in_stock_only", "custom_labels", "updated_at")},
                                sort_keys=True, default=str).encode()).hexdigest()[:16]
    return _feed_response(await _cached_feed_xml(f"{slug}|{grp}|{_sig}", _build))


# =============================================================================
# KATEGORİ BAZLI MANUEL ÜRÜN SIRASI (db.category_product_order)
# Kullanıcı isteği: aynı ürün birden çok kategoride farklı sırada durabilsin.
# Belge: {id: <kategori_id>, ids: [urun_id, ...], updated_at}
# Sıra YALNIZ o kategori sayfasında geçerlidir; ürün belgesine sıra yazılmaz.
# =============================================================================

# Vitrinde TEK kategoriye karşılık gelen takma adlar. Ürün EŞLEŞMESİ bunları zaten
# çözüyordu (ör. /sale sayfası "İNDİRİM" kategorisinin ürünlerini gösterir); manuel sıra
# da AYNI kümeleri kullanmalı — yoksa panelde İNDİRİM'i sıralayıp /sale sayfasında
# sıranın uygulanmadığı görülüyordu (kullanıcı bildirimi).
_EN_YENILER_SLUGS = {"en-yeniler", "en-yeni", "yeniler", "yeni", "yeni-urunler"}
_SALE_SLUGS = {"indirim", "sale", "firsat", "fırsat", "indirimli"}


async def _find_category_id_by_slugs(slugs: set):
    """Verilen takma ad kümesine uyan İLK kategorinin id'si (slug, slug_aliases, ada göre)."""
    try:
        async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1,
                                               "slug_aliases": 1}):
            _sl = (c.get("slug") or "").strip().lower()
            _als = {str(a).strip().lower() for a in (c.get("slug_aliases") or [])}
            if _sl in slugs or (slugs & _als) or generate_slug(c.get("name") or "") in slugs:
                return str(c.get("id"))
    except Exception:
        pass
    return None


async def _resolve_single_category_id(category: str = None, category_id: str = None):
    """Vitrin isteğindeki kategori parametresini TEK bir kategori id'sine çözer.
    category_id doğrudan; slug ise takma adlar (sale/İNDİRİM, en-yeniler) dahil eşleşen
    kategori. Çözülemezse None (manuel sıra uygulanmaz)."""
    if category_id:
        return str(category_id)
    slug = (category or "").strip().lower()
    if not slug or slug in ("tum-urunler", "tumu", "all"):
        return None
    if slug in _SALE_SLUGS:
        return await _find_category_id_by_slugs(_SALE_SLUGS)
    if slug in _EN_YENILER_SLUGS:
        return await _find_category_id_by_slugs(_EN_YENILER_SLUGS)
    return await _find_category_id_by_slugs({slug})


async def _category_manual_order(cat_id: str) -> list:
    """Kategorinin manuel ürün sırası (ürün id listesi). Yoksa boş liste."""
    if not cat_id:
        return []
    try:
        doc = await db.category_product_order.find_one({"id": str(cat_id)}, {"_id": 0, "ids": 1})
        return [str(x) for x in ((doc or {}).get("ids") or []) if x]
    except Exception:
        return []


@router.get("/category-order/{cat_id}", dependencies=[Depends(require_permission("products.view", any_in_area=True))])
async def get_category_order(cat_id: str, limit: int = Query(600, ge=1, le=2000)):
    """Panel: kategorideki ürünleri VİTRİNDEKİ SIRAYLA döndürür (sürükle-bırak ekranı için).
    Manuel sıra varsa önce onu uygular, kalanlar vitrin varsayılanıyla (stokta olanlar önce,
    sonra kart id DESC) gelir. Böylece ekran birebir müşterinin gördüğü sırayı gösterir."""
    manual = await _category_manual_order(cat_id)
    rows = await db.products.find(
        # PASİF ürünler GÖSTERİLMEZ: vitrin de yalnız is_active=True ürünleri listeler,
        # ekran birebir müşterinin gördüğü listeyi göstermeli (kullanıcı isteği).
        {"category_ids": str(cat_id), "is_active": True, "is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "images": 1, "image": 1, "price": 1, "sale_price": 1,
         "stock": 1, "variants": 1, "is_active": 1, "urun_karti_id": 1, "stock_code": 1}
    ).to_list(limit)

    def _eff_stock(p):
        vs = p.get("variants") or []
        if vs:
            try:
                return sum(int(v.get("stock") or 0) for v in vs)
            except Exception:
                return 0
        try:
            return int(p.get("stock") or 0)
        except Exception:
            return 0

    def _first_img(p):
        """İlk GERÇEK görsel (beden tablosu nesneleri atlanır) — kart önizlemesi için."""
        for im in (p.get("images") or []):
            if isinstance(im, str) and im:
                return im
            if isinstance(im, dict) and not im.get("is_size_table"):
                u = im.get("url") or im.get("src") or im.get("image")
                if u:
                    return u
        return p.get("image") or ""

    def _card(p):
        try:
            return int(str(p.get("urun_karti_id") or "0") or 0)
        except Exception:
            return 0

    pos = {pid: i for i, pid in enumerate(manual)}
    _BIG = 10 ** 9
    rows.sort(key=lambda p: (0 if _eff_stock(p) > 0 else 1,
                             pos.get(str(p.get("id")), _BIG),
                             -_card(p)))
    out = []
    for p in rows:
        out.append({
            "id": p.get("id"), "name": p.get("name"),
            "image": _first_img(p),
            "price": p.get("price"), "sale_price": p.get("sale_price"),
            "stock": _eff_stock(p), "is_active": p.get("is_active", True),
            "stock_code": p.get("stock_code") or "",
            "manual": str(p.get("id")) in pos,
        })
    cat = await db.categories.find_one({"id": str(cat_id)}, {"_id": 0, "name": 1, "slug": 1}) or {}
    # Kategoride kaç aktif ürün var: ekran limitle kesildiyse panel bunu SÖYLEMELİ
    # (sessizce eksik liste gösterip "kalanı nerede?" sorusuna yol açmasın).
    total_all = await db.products.count_documents(
        {"category_ids": str(cat_id), "is_active": True, "is_deleted": {"$ne": True}})
    return {"category": {"id": str(cat_id), "name": cat.get("name") or "", "slug": cat.get("slug") or ""},
            "products": out, "manual_count": len(manual), "total": len(out),
            "total_all": total_all, "truncated": total_all > len(out)}


@router.post("/category-order/{cat_id}", dependencies=[Depends(require_permission("products.edit"))])
async def save_category_order(cat_id: str, payload: dict):
    """Panel: kategorinin manuel ürün sırasını kaydeder. Body: {"ids": [urun_id, ...]}
    Boş liste gönderilirse manuel sıra KALDIRILIR (vitrin varsayılanına döner)."""
    ids = [str(x) for x in ((payload or {}).get("ids") or []) if x][:1000]
    now = datetime.now(timezone.utc).isoformat()
    if not ids:
        await db.category_product_order.delete_one({"id": str(cat_id)})
        return {"success": True, "cleared": True}
    await db.category_product_order.update_one(
        {"id": str(cat_id)},
        {"$set": {"id": str(cat_id), "ids": ids, "updated_at": now}}, upsert=True)
    return {"success": True, "count": len(ids)}


@router.get("/feeds", dependencies=[Depends(require_permission("products.view", any_in_area=True))])
async def list_feeds():
    return await db.xml_feeds.find({}, {"_id": 0}).sort("created_at", 1).to_list(200)


@router.post("/feeds", dependencies=[Depends(require_permission("integrations.xml_export"))])
async def create_feed(payload: dict):
    name = (payload.get("name") or "").strip() or "Yeni Feed"
    target = (payload.get("target") or "google").strip().lower()
    if target not in ("google", "facebook", "generic"):
        target = "google"
    base = generate_slug(name) or "feed"
    s = base
    i = 2
    while await db.xml_feeds.find_one({"slug": s}):
        s = f"{base}-{i}"
        i += 1
    doc = {
        "id": generate_id(), "name": name, "slug": s, "target": target,
        "enabled": bool(payload.get("enabled", True)),
        "in_stock_only": bool(payload.get("in_stock_only", False)),
        "custom_labels": _normalize_label_rules(payload.get("custom_labels")),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.xml_feeds.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.put("/feeds/{fid}", dependencies=[Depends(require_permission("integrations.xml_export"))])
async def update_feed(fid: str, payload: dict):
    upd = {}
    for k in ("name", "target", "enabled", "in_stock_only"):
        if k in payload:
            upd[k] = payload[k]
    if "custom_labels" in payload:
        upd["custom_labels"] = _normalize_label_rules(payload.get("custom_labels"))
    if upd.get("target") and upd["target"] not in ("google", "facebook", "generic"):
        upd["target"] = "google"
    if upd:
        await db.xml_feeds.update_one({"id": fid}, {"$set": upd})
    f = await db.xml_feeds.find_one({"id": fid}, {"_id": 0})
    return f or {"ok": True}


@router.delete("/feeds/{fid}", dependencies=[Depends(require_permission("integrations.xml_export"))])
async def delete_feed(fid: str):
    await db.xml_feeds.delete_one({"id": fid})
    return {"ok": True}


def generate_slug(name: str) -> str:
    """Generate URL-friendly slug from name"""
    slug = name.lower()
    # Turkish character replacements
    tr_map = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c', 'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}
    for tr, en in tr_map.items():
        slug = slug.replace(tr, en)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug


def slug_with_card_id(name: str, card_id) -> str:
    """Ürün slug'ını her zaman `{urun-adi}-{kart_id}` biçiminde üretir; böylece
    tüm ürün linkleri TEK formatta olur. card_id yoksa çağıran iç id'yi geçer."""
    base = generate_slug(name or "") or "urun"
    cid = str(card_id).strip() if card_id not in (None, "") else ""
    return f"{base}-{cid}" if cid else base


def _slug_to_diacritic_regex(slug: str) -> str:
    """Türkçe karakter DUYARSIZ regex deseni üretir.

    Storefront menüsü Türkçe karaktersiz slug gönderir (örn. 'takim', 'tisort',
    'giyim') ama DB'de kategori adları/breadcrumb Türkçe karakterlidir
    ('Takım', 'Tişört', 'GİYİM'). Düz regex (case-insensitive) 'i'≠'ı', 's'≠'ş'
    olduğu için eşleşmez. Bu helper her belirsiz latin harfi, Türkçe varyantları
    da kapsayan bir karakter sınıfına çevirir → 'giyim' deseni 'GİYİM'i de yakalar.
    """
    char_map = {
        'i': '[iıİI]', 'o': '[oöÖO]', 'u': '[uüÜU]',
        's': '[sşŞS]', 'c': '[cçÇC]', 'g': '[gğĞG]',
    }
    parts = []
    for ch in slug:
        if ch in '-_ ':
            parts.append(r'[\s_>-]*')
        elif ch in char_map:
            parts.append(char_map[ch])
        else:
            parts.append(re.escape(ch))
    return ''.join(parts)


def _fuzzy_tr_regex(s: str) -> str:
    """Typo toleranslı (subsequence) Türkçe-duyarsız arama deseni.

    Kullanıcı harf düşürdüğünde (örn. 'gmlek', 'ptolon') exact regex eşleşmez.
    Bu desen, yazılan her harfi sırasıyla arar ve harfler arasında en çok 2
    karakterlik boşluğa izin verir → 'gmlek' = gömlek, 'ptolon' = pantolon.
    En az 3 karakterli sorgularda çalışır (kısa sorgularda aşırı geniş eşleşmeyi
    önlemek için). Boşluklar yok sayılır (çok kelimeli sorgular tek dizi olur).
    """
    cls = {
        'i': '[iıİI]', 'ı': '[iıİI]', 'İ': '[iıİI]', 'I': '[iıİI]',
        'o': '[oöÖO]', 'ö': '[oöÖO]', 'O': '[oöÖO]', 'Ö': '[oöÖO]',
        'u': '[uüÜU]', 'ü': '[uüÜU]', 'U': '[uüÜU]', 'Ü': '[uüÜU]',
        's': '[sşŞS]', 'ş': '[sşŞS]', 'S': '[sşŞS]', 'Ş': '[sşŞS]',
        'c': '[cçÇC]', 'ç': '[cçÇC]', 'C': '[cçÇC]', 'Ç': '[cçÇC]',
        'g': '[gğĞG]', 'ğ': '[gğĞG]', 'G': '[gğĞG]', 'Ğ': '[gğĞG]',
    }
    import unicodedata as _ud
    _s = _ud.normalize('NFC', (s or '').strip()).replace('̇', '')
    chars = [ch for ch in _s if not ch.isspace()]
    if len(chars) < 4:   # 3→4: "aaa" gibi 3-harflik gürültü artık fuzzy'yi TETİKLEMEZ
        return ''
    parts = [cls.get(ch, re.escape(ch)) for ch in chars]
    return '.{0,1}'.join(parts)   # .{0,2}→.{0,1}: subsequence sıkılaştı → alakasız "abuk sabuk" eşleşme azalır

@router.post("/ai-description")
async def ai_generate_description(payload: dict, current_user: dict = Depends(require_permission("products.edit")),
    _ai_guard: None = Depends(_ai_slot("product_description")),  # denetim #25: yetki kontrolünden SONRA
):
    """Ürün için yapay zekâ ile Türkçe HTML açıklama üretir.

    AI anahtarı ve sağlayıcı, mevcut AI ayarlarından (Admin → Sorular → AI ayarları,
    `ai_chatbot.custom_api_key`) alınır — ürün açıklama üretici de aynı anahtarı
    kullanır, ayrı token gerekmez. Ürün formundan ad/kategori/marka/özellikler
    gönderilir; dönen HTML doğrudan açıklama alanına yazılır.
    """
    from .ai_chatbot import get_ai_settings, _api_key_for, llm_chat
    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(400, "AI anahtarı tanımlı değil. Admin → AI Asistan → 'API Anahtarı' bölümünden anahtarınızı girin.")

    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Ürün adı gerekli (açıklama üretmek için).")
    from security.ai_budget import charge as _ai_charge
    await _ai_charge(current_user, "product_description")   # denetim #25
    category = (payload.get("category_name") or "").strip()
    brand = (payload.get("brand") or "").strip()
    attrs = payload.get("attributes") or []

    attr_lines = []
    for a in attrs:
        if isinstance(a, dict):
            n = (a.get("name") or "").strip()
            v = (a.get("value") or "").strip()
            if n and v:
                attr_lines.append(f"- {n}: {v}")
    attr_txt = "\n".join(attr_lines[:25])

    # ── SABİT ŞABLON — çıktı bu HTML iskeletini BİREBİR korur; yalnız içerik ürüne göre değişir.
    TEMPLATE = (
        '<p><span style="font-size: 11px;">Kayık yaka. Panço detaylı üst. Yüksek bel etek. Oversize kalıp. Midi boy. Lastikli bel. Cepsiz.</span></p>\n'
        '<p><span style="font-size: 11px;">Kumaş &amp; İçerik Bilgisi</span></p>\n'
        '<p><span style="font-size:11px;">\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Kumaş içeriği: %80 Viskon %20 Polyester<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Dokuma kumaştan üretilmiştir<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Viskon içerikli yapısı sayesinde yumuşak ve akışkan kullanım sunar<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Panço detaylı tasarımı ile modern ve şık görünüm sağlar<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Oversize kalıbı sayesinde rahat kullanım sunar<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Astarsız yapıya sahiptir\n'
        '</span></p>\n'
        '<p>&nbsp;</p>\n'
        '<p><span style="font-size:11px;">\n'
        'Yıkama ve Bakım Talimatı<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;30°C’de benzer renklerle yıkanmalıdır<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Hassas program tercih edilmelidir<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Ağartıcı kullanılmamalıdır<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Kurutma makinesinde kurutulması önerilmez<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Düşük ısıda tersinden ütüleyiniz<br />\n'
        '&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;Uzun ömürlü kullanım için askıda muhafaza ediniz\n'
        '</span></p>\n'
        '<p>&nbsp;</p>\n'
        '<span style="font-size:11px;"> </span>'
    )

    sys = (
        "Sen MELUNİ (Türk kadın giyim) için ürün açıklaması üreten bir asistansın. "
        "Sana SABİT bir HTML ŞABLONU verilecek. Çıktın bu şablonun HTML yapısını, etiketlerini, "
        "inline style'larını (font-size:11px), &nbsp; girintilerini, <br /> satır sonlarını ve ÜÇ "
        "bölümünü (1) kısa özellikler paragrafı, (2) 'Kumaş &amp; İçerik Bilgisi', (3) 'Yıkama ve "
        "Bakım Talimatı' — BİREBİR korumalıdır. SADECE içeriği bu ürüne göre değiştir. "
        "Kural: 'Yıkama ve Bakım Talimatı' bölümünü ve son satırdaki <span> boşluğunu AYNEN bırak. "
        "İlk paragraftaki kısa özellikleri (yaka, kalıp, boy, bel, kol, cep, kapama vb.) ürüne göre, "
        "nokta ile ayrılmış kısa cümleler hâlinde yaz. 'Kumaş içeriği:' maddesini verilen ürün "
        "özelliklerinden al; verilmemişse materyal UYDURMA, o maddeyi genel/uygun bir ifadeyle geç. "
        "Kumaş & İçerik maddeleri 4-6 adet olabilir; bullet biçimi (&nbsp;&nbsp; &nbsp;•&nbsp;&nbsp; &nbsp;) "
        "ve <br /> düzeni aynı kalmalı, SON maddede <br /> olmamalı. "
        "SADECE HTML döndür: kod bloğu (```), başlık, açıklama veya ekstra metin EKLEME."
    )

    user = f"Ürün adı: {name}\n"
    if category:
        user += f"Kategori: {category}\n"
    if brand:
        user += f"Marka: {brand}\n"
    if attr_txt:
        user += f"Ürün özellikleri:\n{attr_txt}\n"
    user += (
        "\nAŞAĞIDAKİ ŞABLONU BİREBİR KULLAN; format/stil/etiketleri ASLA bozma, yalnız içeriği "
        "bu ürüne göre değiştir:\n\n" + TEMPLATE
    )

    try:
        html_out = await llm_chat(
            api_key=api_key,
            provider=settings.get("provider", "anthropic"),
            model=settings.get("model") or "claude-sonnet-4-6",
            system_message=sys,
            user_text=user,
            max_tokens=1100,
        )
    except Exception as e:
        raise HTTPException(502, f"AI açıklama üretimi başarısız: {e}")

    html_out = (html_out or "").strip()
    if html_out.startswith("```"):
        html_out = re.sub(r"^```[a-zA-Z]*\n?", "", html_out)
        html_out = re.sub(r"\n?```$", "", html_out).strip()
    if not html_out:
        raise HTTPException(502, "AI boş yanıt döndürdü, tekrar deneyin.")
    return {"description": html_out}


_MEMBERS_ONLY_CACHE = {"ids": set(), "ts": 0.0}


async def _members_only_cat_ids() -> set:
    """ÜYELERE ÖZEL kategori id'leri (str): `members_only=True` işaretli kategoriler VE
    tüm ALT kategorileri (alt kategoriye eklenen ürün de üyelere özeldir). ~60sn önbellek;
    kategori oluştur/güncelle/sil anında invalidate_members_only_cache() ile tazelenir."""
    import time as _t
    now = _t.monotonic()
    if _MEMBERS_ONLY_CACHE["ts"] and (now - _MEMBERS_ONLY_CACHE["ts"] < 60):
        return _MEMBERS_ONLY_CACHE["ids"]
    ids: set = set()
    try:
        children: dict = {}
        async for c in db.categories.find({}, {"_id": 0, "id": 1, "parent_id": 1, "members_only": 1}):
            cid = c.get("id")
            if cid is None:
                continue
            if c.get("parent_id") not in (None, ""):
                children.setdefault(str(c["parent_id"]), []).append(str(cid))
            if c.get("members_only") is True:
                ids.add(str(cid))
        stack = list(ids)
        while stack:
            for ch in children.get(stack.pop(), []):
                if ch not in ids:
                    ids.add(ch)
                    stack.append(ch)
    except Exception:
        pass
    _MEMBERS_ONLY_CACHE["ids"] = ids
    _MEMBERS_ONLY_CACHE["ts"] = now
    return ids


def invalidate_members_only_cache():
    _MEMBERS_ONLY_CACHE["ts"] = 0.0


def _mo_forms(mo_ids) -> list:
    """Kategori id'leri hem str hem int biçimiyle (eski kayıtlar int tutabiliyor)."""
    out = []
    for x in (mo_ids or []):
        sx = str(x)
        out.append(sx)
        if sx.isdigit():
            out.append(int(sx))
    return out


def request_is_member(request) -> bool:
    """Geçerli kullanıcı JWT'si taşıyan istek = üye (admin dahil). Misafir = yok/geçersiz."""
    from .categories import _req_is_member
    return _req_is_member(request)


async def strip_members_only(request, items: list, id_key: str = "id") -> list:
    """Misafire gidecek ürün listesinden üyelere özel ürünleri ÇIKARIR (projeksiyondan
    bağımsız: id'lerle kategori alanlarını ayrıca sorgular). Üyeye liste aynen döner."""
    if not items or request_is_member(request):
        return items
    mo = await _members_only_cat_ids()
    if not mo:
        return items
    ids = [str(i.get(id_key)) for i in items if isinstance(i, dict) and i.get(id_key)]
    if not ids:
        return items
    forms = _mo_forms(mo)
    blocked = set()
    async for b in db.products.find({"id": {"$in": ids}, "$or": [
            {"category_id": {"$in": forms}}, {"category_ids": {"$in": forms}},
            {"categories": {"$in": forms}}]}, {"_id": 0, "id": 1}):
        blocked.add(str(b.get("id")))
    return [i for i in items if not (isinstance(i, dict) and str(i.get(id_key)) in blocked)]


async def product_id_is_members_only(pid) -> bool:
    mo = await _members_only_cat_ids()
    if not mo or not pid:
        return False
    forms = _mo_forms(mo)
    return bool(await db.products.find_one({"$or": [{"id": str(pid)}, {"slug": str(pid)}], "$and": [{"$or": [
        {"category_id": {"$in": forms}}, {"category_ids": {"$in": forms}},
        {"categories": {"$in": forms}}]}]}, {"_id": 1}))


def members_only_exclusion(mo_ids) -> list:
    """Misafir sorgusuna eklenecek $and koşulları: ürünün birincil kategorisi, seçili
    kategorileri (categories) ve atalı kategori listesi (category_ids) HİÇBİRİ üyelere özel
    olmamalı. Ürün başka kategorilerde de olsa, üyelere özel birine girdiyse gizlenir."""
    lst = _mo_forms(mo_ids)
    if not lst:
        return []
    return [{"category_id": {"$nin": lst}}, {"category_ids": {"$nin": lst}}, {"categories": {"$nin": lst}}]


def product_is_members_only(p: dict, mo_ids) -> bool:
    if not mo_ids:
        return False
    pc = {str((p or {}).get("category_id"))}
    pc |= {str(x) for x in ((p or {}).get("category_ids") or [])}
    pc |= {str(x) for x in ((p or {}).get("categories") or [])}
    return bool(pc & set(mo_ids))


async def _build_products_query(
    request: Request,
    *,
    category: Optional[str] = None,
    category_id: Optional[str] = None,
    season: Optional[str] = None,
    search: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_new: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    status: Optional[str] = None,
    admin_view: Optional[str] = None,
    brand: Optional[str] = None,
    min_stock: Optional[int] = None,
    max_stock: Optional[int] = None,
    is_showcase: Optional[bool] = None,
    is_opportunity: Optional[bool] = None,
    is_free_shipping: Optional[bool] = None,
    stock_code: Optional[str] = None,
    barcode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    urun_karti_id: Optional[str] = None,
    varyasyon_id: Optional[str] = None,
    name: Optional[str] = None,
    gtip: Optional[str] = None,
    breadcrumb: Optional[str] = None,
    supplier: Optional[str] = None,
    tag: Optional[str] = None,
    has_image: Optional[str] = None,
    has_variants: Optional[str] = None,
    has_video: Optional[str] = None,
    multi_barcode: Optional[str] = None,
    discounted: Optional[str] = None,
    attr_key: Optional[str] = None,
    attr_value: Optional[str] = None,
    pub_date_from: Optional[str] = None,
    pub_date_to: Optional[str] = None,
    sizes: Optional[str] = None,
    colors: Optional[str] = None,
):
    """Urun liste + Excel disa-aktarim icin ORTAK Mongo sorgu ureticisi.
    get_products ve export_products_excel AYNI filtre/arama mantigini kullansin
    diye cikarildi -> panelde hangi filtre/arama aciksa Excel de birebir onu indirir.
    Doner: (query, _admin_view).
    """
    query = {}
    # `and_clauses` — yeni gelişmiş filtreler birbirini ezmeden eklenir.
    and_clauses: list = []
    # Çöp kutusundaki (soft-deleted) ürünler normal listelerde/storefront'ta görünmez
    query["is_deleted"] = {"$ne": True}

    # Admin (token'lı istek): status verilmemişse pasif ürünler de görünsün ki
    # arama/listede hiçbir ürün "kaybolmasın". Storefront token göndermez →
    # aktif default korunur, vitrin etkilenmez.
    _is_admin = False
    # ÜYE = doğrulanmış kullanıcı oturumu (admin olması gerekmez). Misafir = token yok/geçersiz/iptal (#11).
    _is_member = request_is_member(request)
    _auth = request.headers.get("authorization") or ""
    # GÜVENLİK (denetim 2026-10): admin tespiti token'daki imzalı is_admin iddiasına TEK BAŞINA
    # dayanmaz — pasif/iptal edilmiş/rolü alınmış personelin eski token'ı iç alanları (maliyet,
    # tedarikçi…) açamasın. require_admin ile AYNI DB kontrolleri (optional_admin); yalnız panel
    # admin_view bayrağını gönderdiğinde çalışır (vitrin isteklerine ek DB yükü yok).
    _admin_flag = str(admin_view or "").strip().lower() in ("1", "true", "yes", "evet")
    if _admin_flag and _is_member:
        from .deps import optional_admin
        _is_admin = (await optional_admin(request)) is not None
    # KRİTİK: AuthContext token'ı GLOBAL eklediği için (axios.defaults) storefront
    # istekleri de admin token taşır → _is_admin TEK BAŞINA storefront/admin ayrımı
    # YAPAMAZ. Bu yüzden pasif/görselsiz ürünleri yalnızca admin panelin AÇIK
    # `admin_view=1` bayrağıyla gösteririz. Storefront bu bayrağı göndermez → token
    # olsa bile MÜŞTERİ görünümü alır (pasifler gizli).
    _admin_view = _is_admin and _admin_flag
    if status is None and _admin_view:
        status = "all"

    # Status default to active for storefront compatibility unless specified differently
    if status == "all":
        pass
    elif status == "passive":
        query["is_active"] = False
    else:
        query["is_active"] = True

    # ===================================================================
    # STOREFRONT (müşteri) GÖRÜNÜRLÜK KURALI — admin DEĞİLSE zorunlu:
    #   • Yalnızca aktif ürünler (pasife alınanlar müşteriye görünmez).
    #   • En az bir görseli olan ürünler — görselsiz/placeholder ürünler
    #     (çoğu import'tan gelen yarım kayıt) vitrinde gizlenir.
    # Bu, status parametresi ne gelirse gelsin storefront'u korur.
    # ===================================================================
    if not _admin_view:
        query["is_active"] = True
        query["images.0"] = {"$exists": True}

    # ── ÜYELERE ÖZEL KATEGORİLER: MİSAFİR (geçerli JWT yok) bunların ürünlerini GÖRMEZ ──
    # Üye (geçerli kullanıcı token'ı) ve admin_view her şeyi görür. Ürünün category_id VEYA
    # category_ids'inden (atalar dâhil) herhangi biri members_only kümesindeyse misafirden gizlenir.
    if not _admin_view and not _is_member:
        _mo_ids = await _members_only_cat_ids()
        if _mo_ids:
            and_clauses.extend(members_only_exclusion(_mo_ids))

    if brand:
        query["brand"] = {"$regex": re.escape(brand.strip()), "$options": "i"}  # ReDoS/regex-injection koruması

    if season and season.strip():
        # Sezon filtresi (ürün listesi + Excel). Sabit dropdown değerleri (İlkbahar/Sonbahar,
        # Tüm Sezonlar, Yaz, Kış) — tam eşleşme yeterli.
        query["season"] = season.strip()

    if min_stock is not None:
        query["stock"] = {"$gte": min_stock}

    if max_stock is not None:
        if "stock" in query:
            query["stock"]["$lte"] = max_stock
        else:
            query["stock"] = {"$lte": max_stock}
            
    if is_showcase is not None:
        query["is_showcase"] = is_showcase
        
    if is_opportunity is not None:
        query["is_opportunity"] = is_opportunity
        
    if is_free_shipping is not None:
        query["is_free_shipping"] = is_free_shipping

    if stock_code:
        # VARYANT VE TEDARİKÇİ KODLARINI DA ARA. Filtre yalnız ürün düzeyindeki
        # `stock_code`e bakıyordu; oysa Trendyol/pazaryeri ekranlarından ve
        # siparişlerden kopyalanan kod her zaman VARYANT kodudur
        # ("MEL-M010196-01"). Ürün panelde dururken filtre boş dönüyor,
        # "ürün kayıp" sanılıyordu. Tedarikçi kodu da aranıyor: dropshipping'de
        # elde çoğu zaman tedarikçinin kodu oluyor.
        _sce = re.escape(stock_code.strip())  # ReDoS/regex-injection koruması
        query.setdefault("$and", []).append({"$or": [
            {"stock_code": {"$regex": _sce, "$options": "i"}},
            {"variants.stock_code": {"$regex": _sce, "$options": "i"}},
            {"model_code": {"$regex": _sce, "$options": "i"}},
            {"supplier_model_ref": {"$regex": _sce, "$options": "i"}},
            {"supplier_group_ref": {"$regex": _sce, "$options": "i"}},
            {"urun_karti_id": {"$regex": _sce, "$options": "i"}},
            {"variants.supplier_stock_code": {"$regex": _sce, "$options": "i"}},
            {"variants.supplier_barcode": {"$regex": _sce, "$options": "i"}},
        ]})

    if barcode:
        import re as _re2
        bce = _re2.escape(barcode.strip())
        query.setdefault("$and", []).append({"$or": [
            {"barcode": {"$regex": bce, "$options": "i"}},
            {"variants.barcode": {"$regex": bce, "$options": "i"}},
        ]})
    
    if date_from or date_to:
        date_q = {}
        try:
            # TR yerel günü → UTC sınırı (bitiş günü tam dahil, saat-dilimi kayması yok).
            if date_from:
                date_q["$gte"] = tr_day_start_utc(date_from)
            if date_to:
                date_q["$lte"] = tr_day_end_utc(date_to)
            query["created_at"] = date_q
        except Exception:
            pass
    
    if category:
        # =====================================================================
        # KATEGORİ FİLTRESİ — Türkçe karakter duyarsız + breadcrumb ağaç eşleşmesi
        # Storefront 'takim'/'tisort'/'giyim' gibi diakritiksiz slug yollar; DB
        # 'Takım'/'Tişört'/'GİYİM' tutar. _slug_to_diacritic_regex ile eşleştirilir.
        # =====================================================================
        cat_slug = category.strip().lower()
        SHOW_ALL = {"tum-urunler", "tumu", "all"}
        EN_YENILER = _EN_YENILER_SLUGS
        if cat_slug in SHOW_ALL:
            # "Tümü" — gerçekten tüm aktif ürünler, default sıralama (created_at desc)
            pass
        elif cat_slug in EN_YENILER:
            # "En Yeniler" GERÇEK kategori üyeliğiyle filtrelenir (tüm ürünler DEĞİL).
            # Yerel "En Yeniler" kategorisinin id'sini bulup category_ids ile eşleştir.
            # (Ticimax En Yeniler eşitlemesi bu kategoriye üyelik yazar.)
            _en_id = None
            async for _c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1}):
                _sl = (_c.get("slug") or "").strip().lower()
                _nm = (_c.get("name") or "").strip()
                if _sl in EN_YENILER or generate_slug(_nm) in EN_YENILER:
                    _en_id = _c.get("id")
                    break
            if _en_id:
                and_clauses.append({"category_ids": _en_id})
            else:
                # Yerel "En Yeniler" kategorisi yoksa: yanlışlıkla tüm ürünleri
                # göstermek yerine boş döndür (eşleşmeyen sentinel).
                and_clauses.append({"id": "__en_yeniler_category_missing__"})
        elif cat_slug == "sale":
            # İndirimli fiyatlılar VE elle "İndirim/Sale" kategorisine eklenen ürünler (manuel etiket).
            _sale_or = [
                {"sale_price": {"$gt": 0}},
                {"discount_price": {"$gt": 0}},
                {"is_on_sale": True},
                {"sale_active": True},
            ]
            # "İNDİRİM"/"Sale" kategorisine üye ürünleri de kapsa (slug: indirim/sale/firsat).
            _sale_slugs = _SALE_SLUGS
            _sale_cat_ids, _sale_cat_names = [], []
            async for _c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "slug_aliases": 1}):
                _csl = (_c.get("slug") or "").strip().lower()
                _als = {str(a).strip().lower() for a in (_c.get("slug_aliases") or [])}
                if _csl in _sale_slugs or (_sale_slugs & _als) or generate_slug(_c.get("name") or "") in _sale_slugs:
                    if _c.get("id"):
                        _sale_cat_ids.append(_c["id"])
                    if _c.get("name"):
                        _sale_cat_names.append(_c["name"])
            # KURAL (kullanıcı, 2026-09-16): SALE vitrini YALNIZ "İNDİRİM/Sale" kategorisine ÜYE
            # ürünleri gösterir. İndirimli fiyatı olup kategoriye eklenmemiş ürün (ör. Mold Balon
            # Pantolon, Yüksek Bel Kapri) SALE'de görünmez — panelde kategori ayarı neyse vitrin o.
            # Kategori hiç tanımlı değilse eski davranış (indirimli fiyatlılar) korunur.
            if _sale_cat_ids or _sale_cat_names:
                _sale_or = []
            if _sale_cat_ids:
                _sale_or.append({"category_ids": {"$in": _sale_cat_ids}})
            if _sale_cat_names:
                _sale_or.append({"category_name": {"$in": _sale_cat_names}})
            and_clauses.append({"$or": _sale_or})
        else:
            dia = _slug_to_diacritic_regex(cat_slug)
            # Slug'tan tam kategori adını çöz (generate_slug ile ters eşleme)
            distinct_names = await db.products.distinct("category_name", {"is_deleted": {"$ne": True}})
            matched_names = [n for n in distinct_names if n and generate_slug(n) == cat_slug]
            cat_or = []
            # ADA GÖRE eşleşme YALNIZ o ad tek bir kategoride geçiyorsa güvenli.
            # Cinsiyet ayrımından sonra "Pantolon" hem Kadın hem Erkek altında
            # var; ada bakan kol ikisini birden döndürüp kadın listesine erkek
            # pantolonu karıştırıyordu. Ad birden fazla kategoride geçiyorsa
            # yalnız slug ve kategori kimliği yetkilidir.
            _ad_sayisi = {}
            async for _c in db.categories.find({}, {"_id": 0, "name": 1}):
                _n = (_c.get("name") or "").strip().lower()
                if _n:
                    _ad_sayisi[_n] = _ad_sayisi.get(_n, 0) + 1
            _tekil = [n for n in (matched_names or []) if _ad_sayisi.get(n.strip().lower(), 0) <= 1]
            if _tekil:
                cat_or.append({"category_name": {"$in": _tekil}})
            if len(_tekil) == len(matched_names or []) and not any(
                    v > 1 for k, v in _ad_sayisi.items() if generate_slug(k) == cat_slug):
                # Kategori adı tam eşleşme (diakritik duyarsız)
                cat_or.append({"category_name": {"$regex": f"^{dia}$", "$options": "i"}})
            # NOT: 'breadcrumb' segment eşleşmesi KALDIRILDI. Ürün başka kategoriye taşınınca
            # category_name/category_ids güncelleniyor ama breadcrumb BAYAT kalabiliyordu →
            # kategoriden çıkarılan ürün eski kategoride görünmeye devam ediyordu (kullanıcı bug).
            # Artık YALNIZ güncel category_name + category_ids (atalar dahil) + category_slug yetkili.
            # Üst kategori listeleme category_ids ataları taşıdığı için bozulmaz (doğrulandı).
            cat_or.append({"category_slug": cat_slug})
            # ÇOKLU KATEGORİ: slug'a karşılık gelen kategori id'lerini category_ids içinde de ara.
            # Ürün ana kategoride olmasa bile (ör. ana "Pantolon") admin'de ekstra eklendiği
            # "Şort" kategorisinde de müşteriye görünür.
            _slug_cat_ids = []
            # ÖNCE: bu slug'ı GERÇEKTEN taşıyan kategori var mı?
            # Varsa ad-slug'ı üzerinden eşleşen DİĞER kategoriler kapsanmaz.
            # Kadın "Bluz" (slug: bluz) ile Erkek "Bluz" (slug: erkek-bluz)
            # aynı ADA sahip; ad-slug kolu ikisini birden topluyor ve kadın
            # listesine erkek ürünü karışıyordu — cinsiyet ayrımı için yukarıda
            # konan koruma tam burada deliniyordu. Ad-slug yalnız HİÇBİR
            # kategorinin o slug'ı taşımadığı eski kayıtlar için yedek yoldur.
            _gercek_slug_var = False
            _kategoriler = []
            async for _c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "slug_aliases": 1}):
                _kategoriler.append(_c)
                _csl = (_c.get("slug") or "").strip().lower()
                _als = [str(a).strip().lower() for a in (_c.get("slug_aliases") or [])]
                if _csl == cat_slug or cat_slug in _als:
                    _gercek_slug_var = True
            for _c in _kategoriler:
                _csl = (_c.get("slug") or "").strip().lower()
                _cnm = (_c.get("name") or "").strip()
                _als = [str(a).strip().lower() for a in (_c.get("slug_aliases") or [])]
                _eslesti = _csl == cat_slug or cat_slug in _als
                if not _eslesti and not _gercek_slug_var:
                    _eslesti = bool(_cnm) and generate_slug(_cnm) == cat_slug
                if _eslesti and _c.get("id"):
                    _slug_cat_ids.append(_c["id"])
            if _slug_cat_ids:
                cat_or.append({"category_ids": {"$in": _slug_cat_ids}})
            and_clauses.append({"$or": cat_or})
    
    if search:
        import re as _re
        esc = _search_tr_regex(search)  # Türkçe duyarsız (İ/ı/ş/ç/ğ/ö/ü dahil)
        # NOT: 'description' arama alanlarından ÇIKARILDI — ürün açıklamaları stil/kombin metni
        # içerir ("bu ceketi eteğinizle kombinleyin" gibi) ve "etek" araması alakasız ceket/bluz
        # getiriyordu. Arama artık isim/anahtar kelime/kod/kategori/özellik/renk üzerinden yürür.
        query["$or"] = [
            {"name": {"$regex": esc, "$options": "i"}},
            {"keywords": {"$regex": esc, "$options": "i"}},
            {"stock_code": {"$regex": esc, "$options": "i"}},
            {"sku": {"$regex": esc, "$options": "i"}},
            {"barcode": {"$regex": esc, "$options": "i"}},
            {"variants.barcode": {"$regex": esc, "$options": "i"}},
            {"variants.sku": {"$regex": esc, "$options": "i"}},
            {"variants.stock_code": {"$regex": esc, "$options": "i"}},
            {"urun_karti_id": {"$regex": esc, "$options": "i"}},
            {"variants.urun_id": {"$regex": esc, "$options": "i"}},
            {"id": {"$regex": esc, "$options": "i"}},
            {"brand": {"$regex": esc, "$options": "i"}},
            {"variants.color": {"$regex": esc, "$options": "i"}},
            {"variants.size": {"$regex": esc, "$options": "i"}},
            # Ürün tipi/özellik değerleri (ör. attributes[].value = "Büstiyer"),
            # kategori adı ve ana renk — müşteri "büstiyer" arayınca tipi büstiyer
            # olan ürün de gelsin (isim/açıklamada geçmese bile).
            {"attributes.value": {"$regex": esc, "$options": "i"}},
            {"category_name": {"$regex": esc, "$options": "i"}},
            {"color": {"$regex": esc, "$options": "i"}},
        ]

    if is_featured is not None:
        query["is_featured"] = is_featured
    
    if is_new is not None:
        query["is_new"] = is_new
    
    if min_price is not None:
        query["price"] = {"$gte": min_price}
    
    if max_price is not None:
        if "price" in query:
            query["price"]["$lte"] = max_price
        else:
            query["price"] = {"$lte": max_price}

    # ============================================================
    # GELİŞMİŞ FİLTRELER (Ticimax paneli)
    # ============================================================
    # --- Metin (regex) filtreleri ---
    if category_id:
        # category_ids atalar dahil tutulduğu için üst kategori seçilince
        # tüm alt kategori ürünleri de eşleşir (ağaç filtreleme).
        and_clauses.append({"category_ids": category_id})
    if urun_karti_id:
        and_clauses.append({"urun_karti_id": {"$regex": re.escape(urun_karti_id.strip()), "$options": "i"}})
    if varyasyon_id:
        and_clauses.append({"variants.urun_id": {"$regex": re.escape(varyasyon_id.strip()), "$options": "i"}})
    if name:
        and_clauses.append({"name": {"$regex": re.escape(name.strip()), "$options": "i"}})
    if gtip:
        ge = re.escape(gtip.strip())
        and_clauses.append({"$or": [
            {"gtip_code": {"$regex": ge, "$options": "i"}},
            {"ticimax_fields.GTIPKODU": {"$regex": ge, "$options": "i"}},
        ]})
    if breadcrumb:
        be = re.escape(breadcrumb.strip())
        and_clauses.append({"$or": [
            {"breadcrumb": {"$regex": be, "$options": "i"}},
            {"breadcrumb_category": {"$regex": be, "$options": "i"}},
            {"ticimax_fields.BREADCRUMBKAT": {"$regex": be, "$options": "i"}},
        ]})
    # GÜVENLİK: tedarikçi iç bilgidir — filtre yalnız doğrulanmış admin görünümünde uygulanır
    # (aksi halde vitrinden ?supplier=… ile ürün-tedarikçi eşleşmesi sızdırılabiliyordu).
    if supplier and _admin_view:
        se = re.escape(supplier.strip())
        and_clauses.append({"$or": [
            {"supplier": {"$regex": se, "$options": "i"}},
            {"ticimax_fields.TEDARIKCI": {"$regex": se, "$options": "i"}},
        ]})
    if tag:
        te = re.escape(tag.strip())
        and_clauses.append({"$or": [
            {"keywords": {"$regex": te, "$options": "i"}},
            {"tags": {"$regex": te, "$options": "i"}},
            {"ticimax_fields.ANAHTARKELIME": {"$regex": te, "$options": "i"}},
        ]})

    # --- Varlık (Var/Yok) filtreleri ---
    def _bool_flag(v):
        return str(v).strip() in ("1", "true", "evet", "yes")

    if has_image is not None and has_image != "":
        and_clauses.append({"images.0": {"$exists": _bool_flag(has_image)}})
    if has_variants is not None and has_variants != "":
        and_clauses.append({"variants.0": {"$exists": _bool_flag(has_variants)}})
    if has_video is not None and has_video != "":
        if _bool_flag(has_video):
            and_clauses.append({"video_url": {"$nin": [None, ""]}})
        else:
            and_clauses.append({"$or": [{"video_url": {"$in": [None, ""]}}, {"video_url": {"$exists": False}}]})
    if multi_barcode is not None and multi_barcode != "":
        if _bool_flag(multi_barcode):
            and_clauses.append({"variants.1": {"$exists": True}})
        else:
            and_clauses.append({"variants.1": {"$exists": False}})
    if discounted is not None and discounted != "":
        if _bool_flag(discounted):
            and_clauses.append({"$or": [
                {"sale_price": {"$gt": 0}},
                {"ticimax_fields.INDIRIMLIFIYAT": {"$gt": 0}},
            ]})

    # --- Teknik detay (attributes) filtresi ---
    if attr_key:
        if attr_value:
            and_clauses.append({f"attributes.{attr_key}.value": {"$regex": re.escape(attr_value.strip()), "$options": "i"}})
        else:
            and_clauses.append({f"attributes.{attr_key}": {"$exists": True}})

    # --- Yayın tarihi aralığı (ticimax_fields.YAYINTARIHI, metinsel ISO karşılaştırma) ---
    if pub_date_from or pub_date_to:
        pub_q = {}
        if pub_date_from:
            pub_q["$gte"] = pub_date_from
        if pub_date_to:
            pub_q["$lte"] = pub_date_to + "T23:59:59"
        and_clauses.append({"ticimax_fields.YAYINTARIHI": pub_q})

    # --- Dinamik ticimax_fields parametreleri (tf_ / tfmin_ / tfmax_) ---
    # DENETİM (injection F5): ticimax_fields MALİYET/tedarikçi gibi GİZLİ iç alanlar içerir.
    # Public'te bunlarla filtreleme, tfmin_/tfmax_ aralık taramasıyla ürün MALİYETİNİN
    # binary-search ile sızdırılmasına yol açıyordu → YALNIZ admin görünümünde uygulanır.
    range_acc: dict = {}
    for pkey, pval in request.query_params.items():
        if not _admin_view:
            continue
        if pval is None or pval == "":
            continue
        if pkey.startswith("tfmin_"):
            col = pkey[6:]
            try:
                range_acc.setdefault(col, {})["$gte"] = float(pval)
            except ValueError:
                pass
        elif pkey.startswith("tfmax_"):
            col = pkey[6:]
            try:
                range_acc.setdefault(col, {})["$lte"] = float(pval)
            except ValueError:
                pass
        elif pkey.startswith("tf_"):
            col = pkey[3:]
            field = f"ticimax_fields.{col}"
            if pval == "__nonempty__":
                and_clauses.append({field: {"$nin": ["", None, 0]}})
            elif pval == "__empty__":
                and_clauses.append({"$or": [{field: {"$in": ["", None, 0]}}, {field: {"$exists": False}}]})
            elif col in PRODUCT_BOOL_COLS:
                try:
                    and_clauses.append({field: int(float(pval))})
                except ValueError:
                    pass
            else:
                and_clauses.append({field: {"$regex": re.escape(str(pval).strip()), "$options": "i"}})
    for col, rng in range_acc.items():
        and_clauses.append({f"ticimax_fields.{col}": rng})

    # --- Storefront beden filtresi (variants.size; çoklu seçim = OR) ---
    if sizes:
        size_list = [s.strip() for s in sizes.split(",") if s.strip()]
        if size_list:
            and_clauses.append({"$or": [
                {"variants.size": {"$regex": f"^{re.escape(s)}$", "$options": "i"}}
                for s in size_list
            ]})

    # --- Storefront renk filtresi (variants.color / color / attributes; çoklu = OR) ---
    if colors:
        color_list = [c.strip() for c in colors.split(",") if c.strip()]
        if color_list:
            col_ors: list = []
            for c in color_list:
                rx = {"$regex": re.escape(c), "$options": "i"}
                col_ors.append({"variants.color": rx})
                col_ors.append({"color": rx})
                col_ors.append({"attributes": {"$elemMatch": {
                    "name": {"$regex": "^(web color|renk|color)$", "$options": "i"},
                    "value": rx,
                }}})
            and_clauses.append({"$or": col_ors})

    if and_clauses:
        query.setdefault("$and", []).extend(and_clauses)

    # ------------------------------------------------------------------
    # Typo toleransı: "gmlek"/"ptolon" gibi harf düşürülen aramalarda exact
    # regex 0 sonuç verir. Bu durumda yakın (subsequence) eşleşmeye düşeriz →
    # müşteri "gmlek" yazsa bile gömlekler listelenir. Yalnızca exact 0 ise
    # devreye girer; normal aramaların kesinliğini bozmaz.
    # ------------------------------------------------------------------
    if search and isinstance(query.get("$or"), list):
        try:
            _exact_n = await db.products.count_documents(query)
        except Exception:
            _exact_n = -1
        if _exact_n == 0:
            _fz = _fuzzy_tr_regex(search)
            if _fz:
                # Fuzzy YALNIZ name/keywords/brand'de aransın — description (uzun serbest
                # metin) çıkarıldı; subsequence deseni orada rastgele "abuk sabuk" eşleşiyordu.
                query["$or"] = [
                    {"name": {"$regex": _fz, "$options": "i"}},
                    {"keywords": {"$regex": _fz, "$options": "i"}},
                    {"brand": {"$regex": _fz, "$options": "i"}},
                ]
    return query, _admin_view


@router.get("")
async def get_products(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=500),
    category: Optional[str] = None,
    category_id: Optional[str] = None,
    season: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    is_featured: Optional[bool] = None,
    is_new: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    status: Optional[str] = None,
    admin_view: Optional[str] = None,
    brand: Optional[str] = None,
    min_stock: Optional[int] = None,
    max_stock: Optional[int] = None,
    is_showcase: Optional[bool] = None,
    is_opportunity: Optional[bool] = None,
    is_free_shipping: Optional[bool] = None,
    stock_code: Optional[str] = None,
    barcode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    # --- Gelişmiş Ticimax tarzı filtreler ---
    urun_karti_id: Optional[str] = None,
    varyasyon_id: Optional[str] = None,
    name: Optional[str] = None,
    gtip: Optional[str] = None,
    breadcrumb: Optional[str] = None,
    supplier: Optional[str] = None,
    tag: Optional[str] = None,
    has_image: Optional[str] = None,
    has_variants: Optional[str] = None,
    has_video: Optional[str] = None,
    multi_barcode: Optional[str] = None,
    discounted: Optional[str] = None,
    attr_key: Optional[str] = None,
    attr_value: Optional[str] = None,
    pub_date_from: Optional[str] = None,
    pub_date_to: Optional[str] = None,
    # --- Storefront facet filtreleri (Mango usulü beden/renk) ---
    sizes: Optional[str] = None,   # virgülle ayrık beden listesi (ör. "S,M,L")
    colors: Optional[str] = None,  # virgülle ayrık renk adı listesi (ör. "Siyah,Mavi")
    campaign: Optional[str] = None,  # kampanya id → yalnız kapsamındaki ürünler (SALE menüsü)
):
    """Get products with filtering and pagination.

    Gelişmiş filtreler (Ticimax paneli ile birebir): yukarıdaki açık parametrelerin
    yanı sıra `ticimax_fields` ham verisi üzerinde çalışan dinamik parametreler de
    kabul edilir:
      - tf_<KOLON>=deger        → ticimax_fields.<KOLON> (BOOL ise eşitlik, değilse regex)
      - tf_<KOLON>=__nonempty__ → alan dolu (Var)
      - tf_<KOLON>=__empty__    → alan boş (Yok)
      - tfmin_<KOLON> / tfmax_<KOLON> → sayısal aralık (ticimax_fields.<KOLON>)
    Veri henüz yoksa bile yapı hazırdır; senkron aktifleşince otomatik sorgulanır.
    """
    skip = (page - 1) * limit
    query, _admin_view = await _build_products_query(
        request,
        category=category, category_id=category_id, season=season, search=search, is_featured=is_featured,
        is_new=is_new, min_price=min_price, max_price=max_price, status=status,
        admin_view=admin_view, brand=brand, min_stock=min_stock, max_stock=max_stock,
        is_showcase=is_showcase, is_opportunity=is_opportunity,
        is_free_shipping=is_free_shipping, stock_code=stock_code, barcode=barcode,
        date_from=date_from, date_to=date_to, urun_karti_id=urun_karti_id,
        varyasyon_id=varyasyon_id, name=name, gtip=gtip, breadcrumb=breadcrumb,
        supplier=supplier, tag=tag, has_image=has_image, has_variants=has_variants,
        has_video=has_video, multi_barcode=multi_barcode, discounted=discounted,
        attr_key=attr_key, attr_value=attr_value, pub_date_from=pub_date_from,
        pub_date_to=pub_date_to, sizes=sizes, colors=colors,
    )
    if campaign:
        # SALE menüsü kampanya sayfası: yalnız YAYINDAKİ kampanyanın kapsamı (motor/rozet kuralı).
        from .sale_menu import campaign_live, campaign_scope_query
        from .deps import safe_str as _safe_str
        _camp = await db.coupons.find_one({"id": _safe_str(campaign, 64), "auto_apply": True}, {"_id": 0})
        if not campaign_live(_camp):
            query = {"$and": [query, {"id": "__kampanya_yok__"}]}
        else:
            _scope = campaign_scope_query(_camp)
            if _scope:
                query = {"$and": [query, _scope]}

    sort_order = -1 if order == "desc" else 1
    # DENETİM (injection F5): public'te YALNIZ güvenli alanlarla sıralama — aksi halde
    # sort=ticimax_fields.MALIYET ile ürünler maliyete göre sıralanıp gizli maliyet sızardı.
    _SAFE_SORT = {"created_at", "updated_at", "price", "sale_price", "member_price_1",
                  "name", "stock", "popularity", "sales_count", "order", "discount_percentage"}
    if not _admin_view and sort not in _SAFE_SORT:
        sort = "created_at"
    # Kategori sayfasında kullanıcı özel sıralama seçmediyse (default created_at):
    # urun_karti_id (sayısal) DESC — yüksek kart id = en yeni ürün, kategoride en üstte.
    _cat_view = bool(category or category_id)
    _card_sort = _cat_view and sort == "created_at"

    if not _admin_view:
        # ===============================================================
        # STOREFRONT: tükenen ürünler (efektif stok 0) ilgili listenin/
        # kategorinin EN SONUNDA gösterilir. Efektif stok = varyant varsa
        # varyant stokları toplamı, yoksa ürün stoğu. Önce stoğu olanlar,
        # sonra istenen sıralama (created_at/price vb.).
        # ===============================================================
        pipeline = [
            {"$match": query},
            {"$addFields": {
                "_eff_stock": {
                    "$cond": [
                        {"$gt": [{"$size": {"$ifNull": ["$variants", []]}}, 0]},
                        {"$sum": {"$map": {
                            "input": {"$ifNull": ["$variants", []]},
                            "as": "v",
                            "in": {"$ifNull": ["$$v.stock", 0]},
                        }}},
                        {"$ifNull": ["$stock", 0]},
                    ]
                }
            }},
            {"$addFields": {"_in_stock": {"$cond": [{"$gt": ["$_eff_stock", 0]}, 1, 0]}}},
            {"$addFields": {"_card_num": {"$convert": {"input": "$urun_karti_id", "to": "long", "onError": 0, "onNull": 0}}}},
        ]
        # ALAKA (E3): arama varken İSİMDE geçen ürün önce gelsin (stoktakiler arasında).
        # Böylece "büstiyer" arayınca yalnız açıklamada geçen alakasız ürün üste çıkmaz.
        if search:
            _rx = _search_tr_regex(search)
            pipeline.append({"$addFields": {"_rel": {"$add": [
                {"$cond": [{"$regexMatch": {"input": {"$toString": {"$ifNull": ["$name", ""]}}, "regex": _rx, "options": "i"}}, 100, 0]},
                {"$cond": [{"$regexMatch": {"input": {"$toString": {"$ifNull": ["$category_name", ""]}}, "regex": _rx, "options": "i"}}, 30, 0]},
            ]}}})
            pipeline.append({"$sort": {"_in_stock": -1, "_rel": -1, sort: sort_order, "_id": 1}})
        elif _card_sort:
            # MANUEL KATEGORİ SIRASI: panelden sürükle-bırak ile belirlenmişse ona uyulur.
            # Sırada olmayan ürünler arkaya, kendi aralarında kart id DESC ile dizilir.
            # Stok önceliği KORUNUR: tükenen ürün manuel sırada üstte olsa da sona düşer.
            _mo = await _category_manual_order(await _resolve_single_category_id(category, category_id))
            if _mo:
                pipeline.append({"$addFields": {"_mpos_raw": {"$indexOfArray": [_mo, "$id"]}}})
                pipeline.append({"$addFields": {"_mpos": {
                    "$cond": [{"$lt": ["$_mpos_raw", 0]}, 999999, "$_mpos_raw"]}}})
                pipeline.append({"$sort": {"_in_stock": -1, "_mpos": 1, "_card_num": -1, "_id": -1}})
            else:
                pipeline.append({"$sort": {"_in_stock": -1, "_card_num": -1, "_id": -1}})
        else:
            pipeline.append({"$sort": {"_in_stock": -1, sort: sort_order, "_id": 1}})
        pipeline += [
            {"$skip": skip},
            {"$limit": limit},
            {"$project": {"_id": 0, "_eff_stock": 0, "_in_stock": 0, "_card_num": 0, "_rel": 0,
                          "_mpos": 0, "_mpos_raw": 0}},
        ]
        products = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(limit)
    else:
        if sort == "stock":
            # Admin stok sıralaması: gösterilen stok = varyant varsa varyant stokları
            # toplamı, yoksa ürün stoğu. DB 'stock' alanı varyantlı üründe güncel
            # olmayabildiği için efektif stoğu hesaplayıp ona göre sıralıyoruz —
            # böylece sıralama, listede görünen stok değeriyle birebir tutarlı olur.
            stock_pipeline = [
                {"$match": query},
                {"$addFields": {
                    "_eff_stock": {
                        "$cond": [
                            {"$gt": [{"$size": {"$ifNull": ["$variants", []]}}, 0]},
                            {"$sum": {"$map": {
                                "input": {"$ifNull": ["$variants", []]},
                                "as": "v",
                                "in": {"$ifNull": ["$$v.stock", 0]},
                            }}},
                            {"$ifNull": ["$stock", 0]},
                        ]
                    }
                }},
                {"$sort": {"_eff_stock": sort_order, "_id": 1}},
                {"$skip": skip},
                {"$limit": limit},
                {"$project": {"_id": 0, "_eff_stock": 0}},
            ]
            products = await db.products.aggregate(stock_pipeline, allowDiskUse=True).to_list(limit)
        elif search:
            # ALAKA SIRALAMASI: arama varken İSİMDE geçen ürünler önce gelsin (aksi halde
            # yalnız açıklama/özellikte geçen alakasız ürün üste çıkabiliyordu — "büstiyer"
            # arayınca isimsiz Takım'ın en üstte gelmesi gibi). name > kategori > diğer.
            _rx = _search_tr_regex(search)
            rel_pipeline = [
                {"$match": query},
                {"$addFields": {"_rel": {"$add": [
                    {"$cond": [{"$regexMatch": {"input": {"$toString": {"$ifNull": ["$name", ""]}}, "regex": _rx, "options": "i"}}, 100, 0]},
                    {"$cond": [{"$regexMatch": {"input": {"$toString": {"$ifNull": ["$category_name", ""]}}, "regex": _rx, "options": "i"}}, 30, 0]},
                ]}}},
                {"$sort": {"_rel": -1, sort: sort_order, "_id": 1}},
                {"$skip": skip},
                {"$limit": limit},
                {"$project": {"_id": 0, "_rel": 0}},
            ]
            products = await db.products.aggregate(rel_pipeline, allowDiskUse=True).to_list(limit)
        else:
            products = await db.products.find(query, {"_id": 0}).sort(sort, sort_order).skip(skip).limit(limit).to_list(limit)
    total = await db.products.count_documents(query)

    # Otomatik kampanya rozeti: aktif auto_apply yüzde kampanyaları kapsama giren
    # ürünlere işlenir ki vitrin kartları indirim oranını sepete girmeden gösterebilsin.
    # KOŞULSUZ uygulanır — kampanya olmasa/eşleşmese bile BAYAT campaign_discount_percent
    # temizlensin (rozet ⊆ motor; müşteri uygulanmayan sahte %X görmesin — para hatası).
    try:
        camps = await _auto_campaigns_for_badges()
        for p in products:
            _apply_campaign_badge(p, camps)
    except Exception as _ce:
        logger.warning(f"Kampanya rozeti işlenemedi: {_ce}")

    # GÜVENLİK: Storefront (admin olmayan) listelerinde alış fiyatı, marj, tedarikçi
    # gibi iç/ticari alanları temizle — public /products taraması kâr marjını sızdırmasın.
    # (Admin panel admin_view=1 gönderir; ona tam alanlar döner.)
    if not _admin_view:
        products = [_strip_internal_fields(p) for p in products]
    else:
        # PANEL LİSTESİ PERFORMANSI: admin_view tüm belgeyi döndürüyordu. Ölçüm: tek ürünün
        # ~11 KB'ının %63'ü liste tablosunda HİÇ kullanılmayan özellik blobları
        # (attributes 3,9 KB + hepsiburada 1,8 KB + temu 1,5 KB). 50 satırlık sayfada
        # ~350 KB, 200 satırda ~1,4 MB boşuna aktarılıyor → "filtre sonucu geç geliyor".
        # Bu alanların TEK tüketicisi düzenleme modalı; o da ürünü GET /products/{id} ile
        # tek tek taze çekiyor (Products.jsx openEditModal) → listede gerekmiyor.
        for _p in products:
            for _k in ("attributes", "trendyol_attributes", "hepsiburada_attributes",
                       "temu_attributes", "description", "ticimax_fields"):
                _p.pop(_k, None)

    return {
        "products": products,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }

@router.get("/slider-feed")
async def slider_feed(
    request: Request,
    source: str = Query("newest"),
    category_ids: Optional[str] = None,
    limit: int = Query(8, ge=1, le=24),
):
    """Sayfa Tasarımı ürün slider'ı için kaynak beslemesi.
    source: favorites (en çok favorilenen) | discounted (indirimde: sale_price
    veya aktif otomatik kampanya kapsamı) | category (category_ids CSV) | newest.
    Yalnız aktif+silinmemiş ürünler; kampanya rozet alanları işlenir.
    ÜYELERE ÖZEL: misafire members_only ürünleri gösterilmez."""
    base_q = {"is_active": True, "is_deleted": {"$ne": True}}
    # Misafir (geçerli JWT yok) → members_only kategorilerin ürünlerini gizle.
    _is_member_sf = request_is_member(request)   # denetim #11: doğrulanmış oturum
    if not _is_member_sf:
        _mo_ids = await _members_only_cat_ids()
        if _mo_ids:
            base_q["$and"] = members_only_exclusion(_mo_ids)
    prods: list = []

    if source == "favorites":
        pipeline = [
            {"$group": {"_id": "$product_id", "cnt": {"$sum": 1}}},
            {"$sort": {"cnt": -1}},
            {"$limit": limit * 3},
        ]
        fav_ids = [str(r["_id"]) async for r in db.favorites.aggregate(pipeline) if r.get("_id")]
        if fav_ids:
            found = await db.products.find({**base_q, "id": {"$in": fav_ids}}, {"_id": 0}).to_list(limit * 3)
            by_id = {str(x.get("id")): x for x in found}
            prods = [by_id[i] for i in fav_ids if i in by_id][:limit]

    elif source == "discounted":
        camps = await _auto_campaigns_for_badges()
        ors = [{"$expr": {"$and": [{"$gt": [{"$ifNull": ["$sale_price", 0]}, 0]},
                                   {"$lt": ["$sale_price", "$price"]}]}}]
        for c in camps:
            ac = [str(x) for x in (c.get("categories") or []) if x]
            ap = [str(x) for x in (c.get("products") or []) if x]
            if ac:
                ors.append({"category_ids": {"$in": ac}})
            if ap:
                ors.append({"id": {"$in": ap}})
            if not ac and not ap:
                ors.append({})  # genel kampanya → tüm ürünler indirimde
        q = {**base_q, "$or": [o for o in ors if o] or ors}
        if any(o == {} for o in ors):
            q = dict(base_q)  # genel kampanya varsa filtreye gerek yok
        prods = await db.products.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)

    elif source == "category":
        cids = [c.strip() for c in (category_ids or "").split(",") if c.strip()]
        if cids:
            q = {**base_q, "$or": [{"category_ids": {"$in": cids}}, {"category_id": {"$in": cids}}]}
            prods = await db.products.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)

    if not prods and source not in ("category",):
        prods = await db.products.find(base_q, {"_id": 0}).sort("created_at", -1).to_list(limit)

    try:
        camps = await _auto_campaigns_for_badges()
        for pp in prods:
            _apply_campaign_badge(pp, camps)   # koşulsuz: bayat rozeti de temizler
    except Exception:
        pass
    # slider-feed PUBLIC bir uçtur → iç alanlar her zaman temizlenir. (Önceki kod tanımsız
    # _admin_view değişkenine bakıp NameError → 500 veriyordu; anasayfa ürün slider'ı bu
    # yüzden boş kalıyordu.)
    prods = [_strip_internal_fields(_p) for _p in prods]
    return {"products": prods, "source": source}


@router.get("/meta/ticimax-schema")
async def get_ticimax_schema(current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Ürün kartında tüm Ticimax (113) alanını gruplu render etmek için şema."""
    from product_schema import build_schema
    return {"groups": build_schema()}

@router.get("/meta/next-card-id")
async def get_next_card_id(current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Yeni urun icin onerilen Urun Kart ID (sistemdeki en buyuk + 1).
    Sayac TUKETMEZ; bir urun olusturmanin tum renk/varyantlarina ayni id verilebilsin
    diye frontend bunu bir kez alip hepsine atar (boylece kart id 'kendi icinde' artmaz)."""
    cid = await generate_urun_karti_id()
    return {"card_id": cid}

@router.get("/meta/filter-options")
async def get_filter_options(current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Gelişmiş filtre panelinin dropdown'larını besleyen dinamik veri.

    Markalar, tedarikçiler, para birimleri ve teknik detay (attribute) grupları
    canlı katalogdan distinct çekilir. Böylece sistem aktifleştikçe seçenekler
    otomatik büyür.
    """
    base = {"is_deleted": {"$ne": True}}
    brands = [b for b in await db.products.distinct("brand", base) if b]
    suppliers = sorted(set(
        [s for s in await db.products.distinct("supplier", base) if s]
        + [s for s in await db.products.distinct("ticimax_fields.TEDARIKCI", base) if s]
    ))
    currencies = sorted(set(
        [c for c in await db.products.distinct("currency", base) if c]
        + [c for c in await db.products.distinct("ticimax_fields.PARABIRIMI", base) if c]
    ))
    # Teknik detay grupları: attributes anahtarları + label'ları
    attr_groups: dict = {}
    async for d in db.products.find(base, {"attributes": 1, "_id": 0}):
        a = d.get("attributes")
        if isinstance(a, dict):
            for k, v in a.items():
                if k not in attr_groups:
                    label = v.get("label") if isinstance(v, dict) else None
                    attr_groups[k] = label or k
    attr_options = [{"key": k, "label": lbl} for k, lbl in sorted(attr_groups.items(), key=lambda x: str(x[1]))]
    # Kategori başına ürün sayısı (category_ids atalar dahil → üst kategori toplamı verir)
    category_counts: dict = {}
    pipeline = [
        {"$match": {"is_deleted": {"$ne": True}}},
        {"$unwind": "$category_ids"},
        {"$group": {"_id": "$category_ids", "count": {"$sum": 1}}},
    ]
    async for row in db.products.aggregate(pipeline):
        if row.get("_id"):
            category_counts[str(row["_id"])] = row["count"]
    return {
        "brands": sorted(brands),
        "suppliers": suppliers,
        "currencies": currencies,
        "attribute_groups": attr_options,
        "category_counts": category_counts,
    }

@router.get("/trash/list")
async def list_trashed_products(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = None,
    current_user: dict = Depends(require_permission("products.view", any_in_area=True))
):
    """Çöp kutusundaki (soft-deleted) ürünleri listeler."""
    skip = (page - 1) * limit
    query = {"is_deleted": True}
    if search:
        esc = re.escape(search.strip())
        query["$or"] = [
            {"name": {"$regex": esc, "$options": "i"}},
            {"stock_code": {"$regex": esc, "$options": "i"}},
            {"urun_karti_id": {"$regex": esc, "$options": "i"}},
        ]
    products = await db.products.find(query, {"_id": 0}).sort("deleted_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db.products.count_documents(query)
    return {"products": products, "total": total, "page": page, "pages": (total + limit - 1) // limit}


@router.get("/{product_id}")
async def get_product(product_id: str, request: Request):
    """Get single product by ID or slug"""
    # Aynı slug hem AKTİF bir kartta hem çöp kutusundaki eski bir kopyada olabilir
    # (ör. koptalanıp silinen kartlar). find_one ilk ekleneni döndürüp aktif ürünü
    # gölgeliyordu → müşteri "Ürün bulunamadı" görüyordu. Önce aktif+silinmemiş
    # eşleşme seçilir; yoksa (admin çöp görüntüleme vb. için) ilk eşleşmeye düşülür.
    _cands = await db.products.find(
        {"$or": [{"id": product_id}, {"slug": product_id}, {"slug_aliases": product_id}]},
        {"_id": 0}
    ).to_list(10)
    product = (next((c for c in _cands if c.get("is_active") is True and not c.get("is_deleted")), None)
               or next((c for c in _cands if not c.get("is_deleted")), None)
               or (_cands[0] if _cands else None))
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    # PASİF/SİLİNMİŞ ürün direkt link ile açılamaz: yalnızca admin görebilir,
    # müşteri için "bulunamadı" döner (vitrin gizliliği detay sayfasında da geçerli).
    _is_admin = False
    _is_member = request_is_member(request)   # denetim #11: doğrulanmış oturum
    _auth = request.headers.get("authorization") or ""
    _claims_admin = False
    if _auth.lower().startswith("bearer "):
        try:
            from .deps import _decode_jwt_strict
            _payload = _decode_jwt_strict(_auth.split(" ", 1)[1]) or {}
            _claims_admin = bool(_payload.get("is_admin"))
        except Exception:
            pass
    # GÜVENLİK (denetim 2026-10): imzalı is_admin iddiası yetmez — require_admin ile aynı DB
    # kontrolleri (aktif, token_version, personel rolü). Yalnız iddia varsa DB'ye gidilir.
    if _claims_admin:
        from .deps import optional_admin
        _is_admin = (await optional_admin(request)) is not None
    if not _is_admin and (product.get("is_active") is not True or product.get("is_deleted")):
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    # ÜYELERE ÖZEL: misafir, members_only kategorideki ürünün detayına DİREKT URL ile de erişemez.
    if not _is_admin and not _is_member:
        _mo_ids = await _members_only_cat_ids()
        if _mo_ids:
            if product_is_members_only(product, _mo_ids):
                # 404 kalır (içerik sızmaz); vitrin 'members_only' koduyla girişe yönlendirir.
                raise HTTPException(status_code=404, detail="members_only")
    # Varyantları global Beden Havuzu (variant_options) sırasına göre diz —
    # böylece storefront'ta XS, S, M, L, XL... admin'in tanımladığı sırayla görünür.
    product["variants"] = await _sort_variants_by_pool(product.get("variants") or [])
    # Otomatik kampanya rozeti (vitrin kartlarıyla aynı mantık — detayda da görünsün).
    # KOŞULSUZ: eşleşmezse bayat değer temizlenir (detay "%X" gösterip sepet uygulamasın).
    try:
        camps = await _auto_campaigns_for_badges()
        _apply_campaign_badge(product, camps)
    except Exception:
        pass
    if not _is_admin:
        product = _strip_internal_fields(product)
    return product


# #22: Ürün stok hareketleri — her hareketin sebebiyle (sipariş düşme, iptal/iade artma,
# stok girişi vb.) listelenir. db.stock_movements: {type, items:[{product_id,delta}], ...}
_STOCK_MOVE_REASONS = {
    "new_order": "Sipariş — stok düştü",
    "order_created": "Sipariş — stok düştü",
    "manual_decrement": "Manuel sipariş — stok düştü",
    "order_imported": "Pazaryeri siparişi — stok düştü",
    "backfill_decrement": "Geçmiş düzeltme (backfill)",
    "order_cancelled": "İptal — stok geri eklendi",
    "return_approved": "İade onaylandı — stok geri eklendi",
    "return_restock": "İade — stok geri eklendi",
    "stock_in": "Stok girişi",
    "manual_increment": "Manuel stok girişi",
    "manual_adjust": "Manuel stok düzeltmesi",
    "production": "Üretim — stok girişi",
    "product_created": "Ürün oluşturuldu — başlangıç stoğu",
    "manufacturing_delivered": "İmalat teslimi — stok girişi",
    "marketplace_stock_sync": "Pazaryeri stok senkronu",
    "marketplace_stock_imported": "Pazaryerinden stok güncellendi",
    "bulk_stock_adjusted": "Toplu varyant stok düzeltmesi",
    "negative_stock_fixed": "Negatif stok sıfırlandı",
    "reconcile_redecrement": "Ödeme sonradan onaylandı — stok yeniden düşüldü",
    "restore_deleted_redecrement": "Silinen sipariş geri alındı — stok yeniden düşüldü",
    "restock_skipped_no_deduction": "İade ENGELLENDİ — sipariş stoktan hiç düşülmemişti",
    "influencer_seeding": "Influencer gönderimi — stok düştü",
    "influencer_seeding_restock": "Influencer gönderimi geri alındı — stok eklendi",
}


async def _log_manual_stock_change(product_id: str, product_name: str, existing: dict,
                                   new_variants, new_root_stock, by_email: str,
                                   source: str = "admin_edit", current_user: dict = None,
                                   request: Request = None) -> None:
    """Manuel stok düzeltmelerini db.stock_movements'a yazar.

    Neden: Ürün düzenleme formu / toplu Excel yalnız variants[].stock'u $set eder,
    HİÇBİR hareket kaydı bırakmazdı → çalışanın 'sıfırladım' dediği düzeltmeler
    stok hareketleri panelinde GÖRÜNMÜYORDU ve 'en son hangi kayıt' izlenemiyordu.
    Bu fonksiyon eski (DB) ile yeni (forma girilen) stoğu barkod/id ile eşleştirip
    yalnız DEĞİŞEN varyantlar için delta (yeni-eski) kaydı üretir. Böylece manuel
    düzeltmeler, gider_pusulası/iade restock'larıyla AYNI zaman çizelgesine düşer.
    """
    try:
        after = dict(existing or {})
        if new_variants is not None:
            after["variants"] = new_variants
        if new_root_stock is not None:
            after["stock"] = new_root_stock
        actor = dict(current_user or {})
        if by_email and not actor.get("email"):
            actor["email"] = by_email
        await record_stock_audit(
            db, product_id=product_id, product_name=product_name or existing.get("name", ""),
            before=existing, after=after, source=source, current_user=actor,
            request=request, action="manual_adjust",
        )
    except Exception as e:
        logger.error(f"[manual_stock_log {product_id}] {e}")


def _fill_old_new_from_chain(moves: list, live_stock: dict) -> None:
    """Eski/yeni stoğu kaydedilmemiş hareketleri (ör. eski influencer/sipariş kayıtları)
    aynı barkodun ZİNCİRİNDEN doldurur — tahmin değil, doğrulanmış türetme:
      eski = bir önceki bilinen 'yeni', yeni = eski + fark.
    Türetilen parça ancak bir SONRAKİ kesin kaydın 'eski'si ya da (en sonda) bugünkü
    canlı stok ile birebir tutarsa gösterilir; tutmazsa (arada kayıt dışı değişiklik
    var demektir) o parça boş bırakılır. `moves` en yeni önce gelir; yerinde günceller."""
    by_bc: dict = {}
    for m in moves:
        bc = (m.get("barcode") or "").strip()
        if bc:
            by_bc.setdefault(bc, []).append(m)
    for bc, rows in by_bc.items():
        rows = sorted(rows, key=lambda r: str(r.get("date") or ""))   # eskiden yeniye (stabil)
        running = None
        pending: list = []

        def _drop(ps):
            for x in ps:
                x["old_stock"] = None
                x["new_stock"] = None
                x.pop("derived", None)

        for m in rows:
            if m.get("old_stock") is not None and m.get("new_stock") is not None:
                if pending and running != m["old_stock"]:
                    _drop(pending)
                pending = []
                running = m["new_stock"]
            elif running is not None:
                m["old_stock"] = running
                m["new_stock"] = running + int(m.get("delta") or 0)
                m["derived"] = True
                running = m["new_stock"]
                pending.append(m)
        if pending and (bc not in live_stock or live_stock[bc] != running):
            _drop(pending)


@router.get("/{product_id}/stock-movements")
async def get_product_stock_movements(product_id: str, limit: int = Query(300, ge=1, le=2000),
                                      current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Bir ürünün stok hareketlerini varyant/SKU bazında döndürür (en yeni önce).

    Eski hareketlerde kaydedilmemiş old/new veya sync sonucunu geriye dönük tahmin
    etmeyiz; ``None/not_recorded`` döner. Kesin alanlar schema v2 kayıt başlangıcından
    itibaren mevcuttur.
    """
    q = {"$or": [
        {"items.product_id": product_id}, {"moves.product_id": product_id},
        {"product_id": product_id},
    ]}
    rows = await db.stock_movements.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    out = []
    # Bedeni kaydedilmemiş (yalnız barkod taşıyan) eski hareketlerde beden/renk, ürünün
    # KENDİ varyantından barkodla bulunur — "Ana stok" yazıp bedeni gizlemesin. Bu tahmin
    # değil birebir eşleşmedir; eski/yeni stok ise kaydedilmediyse boş kalır.
    _vmap: dict = {}
    _live_stock: dict = {}
    try:
        _pdoc = await db.products.find_one({"id": product_id}, {"_id": 0, "variants": 1})
        for _v in (_pdoc or {}).get("variants") or []:
            _b = str(_v.get("barcode") or "").strip()
            if _b:
                _vmap[_b] = (str(_v.get("size") or ""), str(_v.get("color") or ""))
                try:
                    _live_stock[_b] = int(_v.get("stock") or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:
        _vmap = {}
    def _optional_int(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    for r in rows:
        _t = r.get("type", "") or ""
        # 'tüketildi' işaretli (reaktivasyon/reconcile'de guard dışına taşınan) restore hareketi:
        # ham type + '_consumed'. Silinmiyor → tarihçede kalıyor; okunaklı etiketle.
        if _t.endswith("_consumed"):
            _base = _t[:-len("_consumed")]
            _reason = _STOCK_MOVE_REASONS.get(_base, _base or "—") + " → sonradan tekrar düşüldü (tüketildi)"
        else:
            _reason = _STOCK_MOVE_REASONS.get(_t, _t or "—")
        _actor = r.get("actor") or {}
        _sync = r.get("sync_result") if isinstance(r.get("sync_result"), dict) else None
        _base = {
            "date": r.get("created_at", ""), "type": _t, "reason": _reason,
            "action": r.get("action") or _t, "source": r.get("source") or "",
            "order_number": r.get("order_number", ""),
            "by": _actor.get("email") or r.get("created_by") or r.get("source") or "Sistem",
            "actor": _actor, "context": r.get("context") or {},
            "sync_result": _sync or {"status": "not_recorded"},
            "schema_version": int(r.get("schema_version") or 1),
        }
        _items = r.get("items") or r.get("moves") or []
        if not _items and isinstance(r.get("size_distribution"), dict):
            _items = [{"product_id": product_id, "size": size, "delta": qty}
                      for size, qty in r["size_distribution"].items()]
        if not _items:
            _items = [{
                "product_id": product_id,
                "delta": r.get("delta") if r.get("delta") is not None else r.get("quantity") or r.get("total_increment") or 0,
                "old": r.get("old") if r.get("old") is not None else r.get("old_stock"),
                "new": r.get("new") if r.get("new") is not None else r.get("new_stock"),
                "sku": r.get("sku") or r.get("stock_code") or "",
                "barcode": r.get("barcode") or "",
            }]
        for it in _items:
            if it.get("product_id") not in (None, "", product_id):
                continue
            try:
                _delta = int(it.get("delta") if it.get("delta") is not None
                             else it.get("qty") if it.get("qty") is not None
                             else it.get("quantity") or 0)
            except Exception:
                _delta = 0
            out.append({
                **_base,
                "delta": _delta,
                "old_stock": _optional_int(it.get("old") if it.get("old") is not None else it.get("old_stock")),
                "new_stock": _optional_int(it.get("new") if it.get("new") is not None else it.get("new_stock")),
                "variant_id": str(it.get("variant_id") or it.get("id") or ""),
                "sku": str(it.get("sku") or it.get("stock_code") or it.get("barcode") or ""),
                "barcode": str(it.get("barcode") or ""),
                "size": str(it.get("size") or _vmap.get(str(it.get("barcode") or "").strip(), ("", ""))[0]),
                "color": str(it.get("color") or _vmap.get(str(it.get("barcode") or "").strip(), ("", ""))[1]),
            })
    _fill_old_new_from_chain(out, _live_stock)
    return {
        "movements": out,
        "count": len(out),
        "coverage": "Eski/yeni stoğu kaydedilmemiş hareketlerde değer, aynı bedenin önceki kaydından zincirle hesaplanır ve sonraki kayıt ya da bugünkü canlı stokla DOĞRULANIRSA gösterilir; doğrulanamayan boş kalır (tahmin edilmez).",
        "schema_version": 2,
    }


# GÜVENLİK: Public yanıtlardan iç/ticari alanları (alış fiyatı, tedarikçi, marj vb.)
# temizle — rakip/istismarcıya kâr marjı sızmasın.
_PRODUCT_INTERNAL_FIELDS = (
    "cost_price", "purchase_price", "alis_fiyati", "buy_price", "supplier", "tedarikci",
    "supplier_code", "member_price_1", "member_price_2", "member_price_3", "member_price",
    # Tedarikçi XML aktarımının yazdığı izler: kimden, hangi kodla ve hangi
    # adresten aldığımız müşteriye gitmemeli (rakip aynı tedarikçiyi bulur).
    "supplier_barcode", "supplier_stock_code", "supplier_model_ref", "supplier_url",
    "supplier_original_name", "supplier_category_raw",
    "margin", "profit", "profit_margin", "kar", "kar_marji", "ticimax_fields",
    "markup_rate", "use_default_markup",
    "admin_notes", "internal_notes", "vendor", "vendor_id",
)


# Denetim #10: vitrine (herkese açık uçlara) dönen ürün alanları YASAK listesiyle değil, AÇIK İZİN
# listesiyle belirlenir — ürün belgesine sonradan eklenen her yeni alan (pazaryeri özellik
# haritaları, kategori eşleşmeleri, senkron/batch izleri, XML/Ticimax kimlikleri…) varsayılan
# olarak GİZLİ kalır. Vitrin yeni bir alana ihtiyaç duyarsa buraya bilinçli olarak eklenir.
PUBLIC_PRODUCT_FIELDS = frozenset({
    "id", "name", "slug", "description", "short_description",
    "price", "sale_price", "list_price", "currency", "vat_included", "vat_rate",
    "campaign_discount_percent", "campaign_label", "promo_badge", "tier_badge",
    "images", "image", "thumbnail", "videos", "video_url",
    "variants", "stock", "in_stock", "availability", "sizes", "fit_sizes", "size_advice",
    "size_table", "size_chart", "size_guide",
    "brand", "color", "colors", "color_group", "color_siblings", "csv_card_id", "season", "unit",
    "sku", "barcode", "stock_code",
    "category_id", "category_ids", "category_name", "category_slug", "categories", "breadcrumb",
    "is_active", "is_new", "is_featured", "is_free_shipping", "free_shipping", "is_opportunity",
    "is_showcase", "members_only",
    "rating", "review_count",
    "meta_title", "meta_description", "meta_keywords", "keywords", "seo_description",
    "max_installment", "min_order_qty", "max_order_qty", "estimated_delivery",
    "width", "height", "depth", "product_weight",
    "attributes", "created_at",
})
PUBLIC_VARIANT_FIELDS = frozenset({
    "id", "size", "color", "stock", "price", "sale_price", "list_price", "price_diff",
    "price_adjustment", "sku", "barcode", "stock_code", "is_active", "active", "image", "images",
})
# Ürün özellikleri listesinde (attributes) yalnız vitrinde gösterilen ad/değer çiftleri kalır.
_PUBLIC_ATTR_KEYS = frozenset({"name", "type", "value", "key", "label", "values"})


def _strip_internal_fields(p: dict) -> dict:
    """Ürünü YERİNDE vitrin projeksiyonuna indirger (izin listesi) ve aynı nesneyi döndürür."""
    if not isinstance(p, dict):
        return p
    for _k in [k for k in p.keys() if k not in PUBLIC_PRODUCT_FIELDS]:
        p.pop(_k, None)
    _vs = p.get("variants")
    if isinstance(_vs, list):
        for _v in _vs:
            if isinstance(_v, dict):
                for _k in [k for k in _v.keys() if k not in PUBLIC_VARIANT_FIELDS]:
                    _v.pop(_k, None)
    _attrs = p.get("attributes")
    if isinstance(_attrs, list):
        _clean = []
        for _a in _attrs:
            if isinstance(_a, dict):
                _clean.append({k: v for k, v in _a.items() if k in _PUBLIC_ATTR_KEYS})
            elif isinstance(_a, (str, int, float)):
                _clean.append(_a)
        p["attributes"] = _clean
    elif isinstance(_attrs, dict):
        _clean_d = {}
        for k, v in _attrs.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                _clean_d[k] = v
            elif isinstance(v, dict) and "value" in v:
                _clean_d[k] = {"value": v.get("value")}
        p["attributes"] = _clean_d
    return p


# ── Otomatik kampanya rozeti yardımcıları ─────────────────────────────────────
# Sepet motoru (coupons.evaluate) indirimi sepette uygular; vitrinde oranın
# görünmesi için ürünün kapsam eşleşmesi burada hesaplanır. YALNIZ koşulsuz
# kampanyalar rozet olur: auto_apply + percent + min_cart_total yok +
# first_order_only değil — aksi hâlde rozet yanıltıcı olur (koşullu indirim).
_CAMP_BADGE_CACHE: dict = {"t": 0.0, "rows": [], "multi": []}


async def _auto_campaigns_for_badges() -> list:
    import time as _time
    if _time.time() - _CAMP_BADGE_CACHE["t"] < 30:
        return _CAMP_BADGE_CACHE["rows"]
    # Rozet, promo motoruyla TUTARLI olmalı: otomatik uygulama kapalıysa motor indirimi
    # uygulamaz → rozet de gösterilmemeli (aksi halde %10 rozet var ama sepette indirim yok).
    try:
        _s = await db.settings.find_one({"id": "main"}, {"_id": 0, "promo_auto_apply_enabled": 1}) or {}
        if "promo_auto_apply_enabled" in _s and not bool(_s.get("promo_auto_apply_enabled")):
            _CAMP_BADGE_CACHE["t"] = _time.time()
            _CAMP_BADGE_CACHE["rows"] = []
            _CAMP_BADGE_CACHE["multi"] = []
            _CAMP_BADGE_CACHE["tiers"] = []
            return []
    except Exception:
        pass
    now_iso = datetime.now(timezone.utc).isoformat()
    # KÖK SEBEP (rozet-var/indirim-yok): tarih penceresi eskiden MONGO'da ham karşılaştırmayla
    # süzülüyordu. Mongo'nun string/BSON-Date karşılaştırması ile motorun Python kontrolü
    # (coupons._evaluate_single) AYNI sonucu vermiyor → SÜRESİ DOLMUŞ kampanya rozet olarak
    # görünmeye devam ediyor, sepet ise indirimi uygulamıyordu (müşteri %10 görüp tam fiyat
    # ödüyor). Artık tarih/limit kontrolleri Mongo'da DEĞİL, motorun mantığıyla BİREBİR aynı
    # şekilde Python'da yapılır → rozet ⊆ motorun uygulayacağı indirim.
    q = {"is_active": True, "auto_apply": True, "type": "percent"}
    rows = await db.coupons.find(q, {"_id": 0, "id": 1, "name": 1, "title": 1, "code": 1, "value": 1,
                                     "categories": 1, "products": 1, "excluded_products": 1,
                                     "min_cart_total": 1, "first_order_only": 1,
                                     "start_at": 1, "end_at": 1,
                                     "usage_limit": 1, "min_quantity": 1}).to_list(200)

    def _as_str(v):
        """BSON Date / datetime de gelebilir → karşılaştırılabilir ISO string'e çevir."""
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    def _window_ok(c: dict) -> bool:
        # coupons._evaluate_single ile AYNI mantık (L1 dahil: yalnız-tarih end_at → gün SONU).
        _start = _as_str(c.get("start_at"))
        if _start and _start > now_iso:
            return False
        _end = _as_str(c.get("end_at"))
        if _end:
            if len(_end) == 10 and "T" not in _end:
                _end = f"{_end}T23:59:59+00:00"
            if _end < now_iso:
                return False
        return True

    _pre = [r for r in rows
            if not r.get("first_order_only")
            and float(r.get("min_cart_total") or 0) <= 0
            and float(r.get("value") or 0) > 0
            # Rozet tek ürün kartında gösterilir; çok-adet koşullu kampanya yanıltıcı olur.
            and int(r.get("min_quantity") or 0) <= 1
            and _window_ok(r)]

    # Kullanım limiti dolmuş kampanya da motorda uygulanmaz → rozeti gösterilmemeli.
    rows = []
    for r in _pre:
        if r.get("usage_limit"):
            try:
                from .coupons import _coupon_used_count
                # Otomatik kampanya: in-flight sayılmaz (motorla aynı çağrı).
                if await _coupon_used_count(r["id"], r.get("code", ""), count_inflight=False) >= r["usage_limit"]:
                    continue
            except Exception as _ue:
                logger.warning(f"Kampanya kullanım sayımı yapılamadı ({r.get('code')}): {_ue}")
        rows.append(r)

    # "X AL Y ÖDE" (nth_discount) otomatik kampanyaları → ürün kartında KIRMIZI ibare
    # (promo_badge). Fiyat/yüzde rozetini ETKİLEMEZ (indirim sepette, adet koşuluyla oluşur).
    # Tarih/limit/kapsam kuralları motorla (coupons._evaluate_single/_compute_discount) aynı.
    multi = []
    try:
        _nrows = await db.coupons.find(
            {"is_active": True, "auto_apply": True, "type": "nth_discount"},
            {"_id": 0, "id": 1, "code": 1, "title": 1, "categories": 1, "products": 1,
             "excluded_products": 1, "first_order_only": 1, "start_at": 1, "end_at": 1,
             "usage_limit": 1, "buy_quantity": 1, "free_quantity": 1, "get_discount": 1,
             "skip_discounted": 1, "badge_text": 1, "priority": 1}).to_list(100)
        for r in _nrows:
            if r.get("first_order_only") or not _window_ok(r) or float(r.get("get_discount") or 0) <= 0:
                continue
            if r.get("usage_limit"):
                from .coupons import _coupon_used_count
                if await _coupon_used_count(r["id"], r.get("code", ""), count_inflight=False) >= r["usage_limit"]:
                    continue
            r["_badge"] = multibuy_badge_text(r)
            if r["_badge"]:
                multi.append(r)
        multi.sort(key=lambda r: -int(r.get("priority") or 0))
    except Exception as _me:
        logger.warning(f"X-al-Y-öde rozetleri okunamadı: {_me}")

    # SEPET-TUTARI BASAMAKLARI ("5000 TL üzerine 500 TL indirim") → ürün kartında fiyatın ALTINDA
    # kırmızı ibare (tier_badge). Fiyatı ETKİLEMEZ (indirim sepette, tutar koşuluyla oluşur).
    # Yalnız motorun HERKESE ve her ödeme yönteminde uygulayacağı otomatik kampanyalar: ilk-sipariş,
    # ödeme-yöntemi kısıtlı (ör. havale), adet koşullu, süresi/limiti dolmuş olanlar gösterilmez.
    # İşletme Kuralı `product.tier_campaign_badge_enabled` ile kapatılabilir.
    tiers = []
    try:
        import business_rules as _BR
        if bool(await _BR.get_rule(db, "product.tier_campaign_badge_enabled", True)):
            _trows = await db.coupons.find(
                {"is_active": True, "auto_apply": True, "type": {"$in": ["fixed", "percent"]},
                 "min_cart_total": {"$gt": 0}},
                {"_id": 0, "id": 1, "code": 1, "type": 1, "value": 1, "max_discount": 1,
                 "min_cart_total": 1, "categories": 1, "products": 1, "excluded_products": 1,
                 "first_order_only": 1, "start_at": 1, "end_at": 1, "usage_limit": 1,
                 "min_quantity": 1, "payment_methods": 1, "skip_discounted": 1}).to_list(100)
            for r in _trows:
                if (r.get("first_order_only") or not _window_ok(r) or float(r.get("value") or 0) <= 0
                        or (r.get("payment_methods") or []) or int(r.get("min_quantity") or 0) > 1):
                    continue
                if r.get("usage_limit"):
                    from .coupons import _coupon_used_count
                    if await _coupon_used_count(r["id"], r.get("code", ""), count_inflight=False) >= r["usage_limit"]:
                        continue
                tiers.append(r)
            tiers.sort(key=lambda r: float(r.get("min_cart_total") or 0))
    except Exception as _te:
        logger.warning(f"Sepet-tutarı kampanya ibareleri okunamadı: {_te}")

    _CAMP_BADGE_CACHE["t"] = _time.time()
    _CAMP_BADGE_CACHE["rows"] = rows
    _CAMP_BADGE_CACHE["multi"] = multi
    _CAMP_BADGE_CACHE["tiers"] = tiers
    return rows


def _fmt_tl(v: float) -> str:
    """5000.0 → '5000', 1250.5 → '1250,5' (kart fiyatıyla aynı: binlik ayırıcı yok)."""
    return f"{float(v):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def tier_badge_text(c: dict) -> str:
    mn = float(c.get("min_cart_total") or 0)
    val = float(c.get("value") or 0)
    if mn <= 0 or val <= 0:
        return ""
    if c.get("type") == "percent":
        return f"{_fmt_tl(mn)} TL üzerine %{val:g} indirim"
    return f"{_fmt_tl(mn)} TL üzerine {_fmt_tl(val)} TL indirim"


def _tier_badge_for_product(p: dict, tiers: list, unit_price: float) -> str:
    """Kapsamdaki basamaklardan ürünün fiyatının ÜSTÜNDEKİ en küçük eşik (ör. 3.000 TL'lik ürüne
    '5.000 TL üzerine…', 6.000 TL'lik ürüne '10.000 TL üzerine…'); fiyat tüm eşikleri aşıyorsa en
    büyük eşik. Kapsam/hariç/indirimli-ürün kuralları motorla (coupons._compute_discount) aynı."""
    pid = str(p.get("id") or "")
    cats = {str(c) for c in (p.get("category_ids") or []) if c}
    if p.get("category_id"):
        cats.add(str(p["category_id"]))
    _lp = float(p.get("price") or 0)
    _sp = float(p.get("sale_price") or 0)
    has_manual_sale = bool(_sp > 0 and _sp < _lp)
    eligible = []
    for c in tiers or []:
        if has_manual_sale and c.get("skip_discounted", True):
            continue
        if pid in {str(x) for x in (c.get("excluded_products") or []) if x}:
            continue
        ac = {str(x) for x in (c.get("categories") or []) if x}
        ap = {str(x) for x in (c.get("products") or []) if x}
        if (not ac and not ap) or (pid in ap) or bool(cats & ac):
            eligible.append(c)
    if not eligible:
        return ""
    above = [c for c in eligible if float(c.get("min_cart_total") or 0) > unit_price]
    pick = above[0] if above else eligible[-1]  # liste eşiğe göre artan sıralı
    return tier_badge_text(pick)


def multibuy_badge_text(c: dict) -> str:
    """nth_discount kampanyasının vitrin ibaresi. Panelde 'Rozet yazısı' girildiyse o;
    yoksa kurgudan üretilir: 3 al 1 bedava → '3 AL 2 ÖDE'; kısmi → '2. ÜRÜNE %50'."""
    t = str(c.get("badge_text") or "").strip()
    if t:
        return t[:40]
    try:
        bq = int(c.get("buy_quantity") or 2)
        fq = int(c.get("free_quantity") or 1)
        gd = float(c.get("get_discount") or 0)
    except Exception:
        return ""
    if bq <= 0 or fq <= 0 or gd <= 0:
        return ""
    if gd >= 100 and fq < bq:
        return f"{bq} AL {bq - fq} ÖDE"
    _g = f"{gd:g}"
    if fq == 1:
        return f"{bq}. ÜRÜNE %{_g} İNDİRİM" if bq > 1 else f"%{_g} İNDİRİM"
    return f"{bq} ALANA {fq} ÜRÜNDE %{_g}"


def _multibuy_badge_for_product(p: dict, multi: list) -> str:
    pid = str(p.get("id") or "")
    cats = {str(c) for c in (p.get("category_ids") or []) if c}
    if p.get("category_id"):
        cats.add(str(p["category_id"]))
    _lp = float(p.get("price") or 0)
    _sp = float(p.get("sale_price") or 0)
    has_manual_sale = bool(_sp > 0 and _sp < _lp)
    for c in multi or []:
        if has_manual_sale and c.get("skip_discounted", True):
            continue
        ex = {str(x) for x in (c.get("excluded_products") or []) if x}
        if pid in ex:
            continue
        ac = {str(x) for x in (c.get("categories") or []) if x}
        ap = {str(x) for x in (c.get("products") or []) if x}
        if (not ac and not ap) or (pid in ap) or bool(cats & ac):
            return c.get("_badge") or ""
    return ""


def _campaign_pct_for_product(p: dict, camps: list):
    """Ürünün kapsama girdiği en yüksek yüzdeli kampanyayı döner -> (pct, label)."""
    pid = str(p.get("id") or "")
    cats = {str(c) for c in (p.get("category_ids") or []) if c}
    if p.get("category_id"):
        cats.add(str(p["category_id"]))
    # Ürün kartında İNDİRİMLİ FİYAT (sale_price) girili mi?
    _lp = float(p.get("price") or 0)
    _sp = float(p.get("sale_price") or 0)
    has_manual_sale = bool(_sp > 0 and _sp < _lp)
    best, label = 0.0, ""
    for c in camps:
        # KAMPANYA-BAŞINA ANAHTAR: skip_discounted (varsayılan True) → indirimli-fiyatlı ürüne
        # bu kampanya UYGULANMAZ. Admin kampanya formundan kapatırsa (False) kampanya indirimli
        # ürünlere de uygulanır. Rozet ⊆ motor: aynı kural coupons._compute_discount'ta da var.
        if has_manual_sale and c.get("skip_discounted", True):
            continue
        ac = {str(x) for x in (c.get("categories") or []) if x}
        ap = {str(x) for x in (c.get("products") or []) if x}
        ex = {str(x) for x in (c.get("excluded_products") or []) if x}
        if pid in ex:  # HARİÇ TUTULAN ürün: kategori kapsamında olsa da rozet/indirim gösterme
            continue
        in_scope = (not ac and not ap) or (pid in ap) or bool(cats & ac)
        if in_scope and float(c.get("value") or 0) > best:
            best = float(c["value"])
            # KAMPANYA ADI: kampanya belgesinde ad `title` alanında tutulur (campaign UI'daki
            # "ad" → coupon.title). Kupon KODUNU (özellikle otomatik "AUTO-xxxx") gösterme —
            # kullanıcı kampanyanın ADINI görmek istiyor. Title yoksa: anlamlı bir kod varsa
            # onu göster, otomatik AUTO- kodu ise boş bırak (frontend "Kampanya" der).
            _title = (c.get("title") or c.get("name") or "").strip()
            _code = (c.get("code") or "").strip()
            label = _title or ("" if _code.upper().startswith("AUTO-") else _code)
    return best, label


def _apply_campaign_badge(p: dict, camps: list) -> dict:
    """Ürüne TAZE otomatik-kampanya rozetini yazar (rozet ⊆ motor garantisi).

    KRİTİK (para): campaign_discount_percent TÜRETİLMİŞ bir alandır — aktif auto_apply
    kampanyalardan HER OKUMADA yeniden hesaplanmalı. Bazı ürün belgelerinde eski bir
    kampanyadan KALMIŞ bayat bir değer bulunabiliyor (ör. import/geçmiş kampanya). Eski kod
    yalnız `pct>0` iken üzerine yazıp 0 olduğunda BAYAT değeri TEMİZLEMİYORDU → vitrin/detay
    "%10 indirim" gösteriyor ama sepet/sipariş motoru kapsam dışı olduğu için indirimi
    UYGULAMIYOR (müşteri %10 görüp indirimli fiyatı DEĞİL 'ilk satış fiyatını' ödüyordu).
    Artık kampanya eşleşmezse alan 0'a çekilir → gösterim ile tahsil BİREBİR tutarlı olur."""
    # KURAL (kullanıcı): ürün kartında İNDİRİMLİ FİYAT (sale_price) girili ise, kampanya-başına
    # `skip_discounted` (varsayılan True) o kampanyayı bu ürüne uygulatmaz. Karar artık
    # _campaign_pct_for_product içinde kampanya-başına verilir (rozet ⊆ motor). Böylece admin
    # bir kampanyada anahtarı kapatırsa indirimli ürüne de rozet/indirim gider.
    try:
        pct, label = _campaign_pct_for_product(p, camps or [])
    except Exception:
        pct, label = 0.0, ""
    try:
        p["promo_badge"] = _multibuy_badge_for_product(p, _CAMP_BADGE_CACHE.get("multi") or [])
    except Exception:
        p["promo_badge"] = ""
    try:
        _lp = float(p.get("price") or 0)
        _sp = float(p.get("sale_price") or 0)
        _unit = _sp if 0 < _sp < _lp else _lp
        if pct and pct > 0:
            _unit = _unit * (1 - pct / 100.0)
        p["tier_badge"] = _tier_badge_for_product(p, _CAMP_BADGE_CACHE.get("tiers") or [], _unit)
    except Exception:
        p["tier_badge"] = ""
    if pct and pct > 0:
        p["campaign_discount_percent"] = pct
        p["campaign_label"] = label
    else:
        # BAYAT hayalet indirimi temizle (motorun uygulamayacağı sahte %'yi gösterme)
        p["campaign_discount_percent"] = 0
        p["campaign_label"] = ""
    return p


async def _attach_campaign_badges(prods: list) -> list:
    """Bir ürün listesine SEPET otomatik kampanya rozetini (campaign_discount_percent +
    campaign_label) ekler — vitrin, arama, kombin, öneri, kasa-önü HER YERDE aynı indirim
    görünsün diye TEK kaynak. Kapsam (kategori/ürün) doğru eşleşsin diye category_ids gerekir;
    çağıran uçlar projeksiyona category_ids/category_id eklemeli."""
    if not prods:
        return prods
    try:
        camps = await _auto_campaigns_for_badges()
    except Exception:
        camps = []
    # KOŞULSUZ normalize: camps boş olsa da bayat campaign_discount_percent 0'a çekilir
    # (rozet ⊆ motor — gösterilen indirim sepette MUTLAKA uygulanır).
    for p in prods:
        if not isinstance(p, dict):
            continue
        _apply_campaign_badge(p, camps)
    return prods


@router.post("/cart-pricing")
async def cart_pricing(payload: dict):
    """Sepet kalemleri için CANLI fiyat/indirim verisi — sepet açılınca frontend bunu
    çağırıp kalemlerin price/sale_price/campaign_discount_percent alanlarını tazeler.
    Böylece eski sepet kalemi (kampanya kaydedilmeden eklenmiş) veya sonradan değişen
    fiyat/kampanya, sepette/kasada DOĞRU indirimli birim fiyatı gösterir.
    payload: {product_ids: [...], variant_ids?: [...]}. Döner: {items:{id:{...}}}."""
    ids = [str(x) for x in (payload.get("product_ids") or []) if x][:200]
    if not ids:
        return {"items": {}}
    prods = await db.products.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "price": 1, "sale_price": 1, "category_id": 1,
         "category_ids": 1, "variants": 1, "is_active": 1}).to_list(200)
    prods = await _attach_campaign_badges(prods)   # otomatik kampanya yüzdesini ekle
    out = {}
    for p in prods:
        vmap = {}
        for v in (p.get("variants") or []):
            if v.get("id"):
                vmap[str(v["id"])] = {
                    "stock": v.get("stock"),
                    "price_diff": v.get("price_diff") or v.get("price_adjustment") or 0,
                }
        out[str(p["id"])] = {
            "price": p.get("price"),
            "sale_price": p.get("sale_price"),
            "campaign_discount_percent": p.get("campaign_discount_percent") or 0,
            "campaign_label": p.get("campaign_label") or "",
            "is_active": p.get("is_active", True),
            "variants": vmap,
        }
    return {"items": out}


async def _sort_variants_by_pool(variants: list) -> list:
    """Ürün varyantlarını `variant_options` (type=size) sort_order'ına göre sıralar.
    Havuzda olmayan bedenler (kombinasyonlar/numeric) orijinal sırada en sona eklenir
    (stable sort). Renk sıralaması beden eşitliğinde korunur."""
    if not variants:
        return variants
    size_order = {}
    async for vo in db.variant_options.find({"type": "size"}, {"_id": 0, "value": 1, "sort_order": 1}):
        key = str(vo.get("value", "")).strip().lower()
        if key:
            size_order[key] = vo.get("sort_order", 9999)
    if not size_order:
        return variants
    def _k(v):
        s = str(v.get("size", "")).strip().lower()
        return size_order.get(s, 10000)
    return sorted(variants, key=_k)


@router.post("/{product_id}/copy-attributes-to-siblings")
async def copy_attributes_to_siblings(product_id: str, current_user: dict = Depends(require_permission("products.edit"))):
    """Bu rengin ürün ÖZELLİKLERİNİ (Kol Tipi, Yaka Stili, Kumaş, Kalıp... + HB/Temu map'leri)
    AYNI modelin diğer renk kartlarına (csv_card_id) kopyalar. RENK/BEDEN'e DOKUNMAZ:
    Renk/Web Color özellikleri ve her kartın kendi bedenleri/varyant urun_id/barkod/stok korunur.
    Tekten bölünen renk kartlarının özelliklerini tek tıkla birebir eşitlemek için."""
    src = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not src:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    card_id = src.get("csv_card_id")
    if not card_id:
        return {"updated": 0, "siblings": 0, "detail": "Renk kardeşi yok (csv_card_id boş)."}

    COLOR_NAMES = {"renk", "web color", "color"}
    def _n(s): return (s or "").strip().lower()

    src_attrs_nc = [a for a in (src.get("attributes") or []) if _n(a.get("name")) not in COLOR_NAMES]
    src_hb_nc = {k: v for k, v in (src.get("hepsiburada_attributes") or {}).items() if _n(k) not in COLOR_NAMES}
    src_temu_nc = {k: v for k, v in (src.get("temu_attributes") or {}).items() if _n(k) not in COLOR_NAMES}

    # VERİ KORUMASI: kaynak üründe kopyalanacak (renk-dışı) ÖZELLİK yoksa, kardeşlerin dolu
    # özelliklerini BOŞLA ezme — kaza eseri boş kaynaktan tüm renk grubu silinmesin.
    if not src_attrs_nc and not src_hb_nc and not src_temu_nc:
        return {"updated": 0, "siblings": 0, "sibling_ids": [],
                "detail": "Kaynak üründe kopyalanacak özellik yok — kardeşler korundu (boş ezme engellendi)."}

    updated, sib_ids = 0, []
    cursor = db.products.find({"csv_card_id": card_id, "id": {"$ne": src["id"]}}, {"_id": 0})
    async for s in cursor:
        s_color = [a for a in (s.get("attributes") or []) if _n(a.get("name")) in COLOR_NAMES]
        s_hb_color = {k: v for k, v in (s.get("hepsiburada_attributes") or {}).items() if _n(k) in COLOR_NAMES}
        s_temu_color = {k: v for k, v in (s.get("temu_attributes") or {}).items() if _n(k) in COLOR_NAMES}
        await db.products.update_one(
            {"id": s["id"]},
            {"$set": {
                "attributes": src_attrs_nc + s_color,            # renk hariç özellikler kaynakla aynı
                "hepsiburada_attributes": {**src_hb_nc, **s_hb_color},
                "temu_attributes": {**src_temu_nc, **s_temu_color},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }})
        updated += 1
        sib_ids.append(s["id"])
    return {"updated": updated, "siblings": updated, "sibling_ids": sib_ids}


@router.get("/{product_id}/color-siblings")
async def get_color_siblings(product_id: str, request: Request):
    """Aynı modelin (csv_card_id) farklı renk varyantlarını getir.
    Ürün detay sayfasında "Diğer Renkler" swatch listesi için kullanılır.
    """
    import re as _re
    p = await db.products.find_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]},
        {"_id": 0, "id": 1, "csv_card_id": 1, "urun_karti_id": 1, "name": 1, "color": 1,
         "stock_code": 1, "variants": 1, "attributes": 1,
         "category_name": 1}  # variants/attributes: model-guard renk çözümü
    )
    if not p:
        return {"siblings": []}
    _PROJ = {"_id": 0, "id": 1, "slug": 1, "name": 1, "thumbnail": 1, "images": 1,
             "variants": 1, "attributes": 1, "color": 1, "category_name": 1}

    def _row(s):
        color = (s.get("color") or "").strip()
        if not color and s.get("variants"):
            for v in s["variants"]:
                if v.get("color"):
                    color = v["color"]; break
        if not color and isinstance(s.get("attributes"), list):
            for a in s["attributes"]:
                if (a.get("name") or "").strip().lower() in ("web color", "renk", "color"):
                    color = a.get("value") or ""; break
        if not color:  # son çare: addan sondaki renk kelimesi
            color = _trailing_color(s.get("name") or "")
        return {
            "id": s["id"], "slug": s.get("slug") or s["id"],
            "name": s.get("name") or "", "color": color,
            "image": (s.get("images") or [s.get("thumbnail")] or [None])[0],
        }

    siblings = []
    seen_ids = {p["id"]}

    # ÜRÜN TİPİ GUARD'I (Kadir, W-bildirimi): bir rengin "kardeşi" AYNI ÜRÜN TİPİ olmalıdır.
    # Ceketin renk seçeneği gömlek olamaz. Tip, ürün adından (Ceket/Gömlek/Elbise…), yoksa
    # kategori adından çözülür; iki taraf da çözülebiliyor ve FARKLIYSA aday elenir.
    def _type_key(x) -> str:
        _nm = _cnorm(x.get("name") or "")
        for _kw, _canon in _TYPE_KEYWORDS:
            if _kw in _nm:
                return _cnorm(_canon)
        return _cnorm(x.get("category_name") or "")

    _self_type = _type_key(p)

    def _type_ok(cand) -> bool:
        _ct = _type_key(cand)
        return not (_self_type and _ct and _ct != _self_type)

    # 1) AÇIK ANAHTAR: urun_karti_id ve csv_card_id — AMA HER ALAN KENDİ KARŞILIĞIYLA.
    #    KÖK SEBEP (bildirilen hata): eskiden ürünün urun_karti_id'si KARŞI TARAFIN
    #    csv_card_id'siyle de eşleştiriliyordu. Bu iki numara AYRI dizilerdir ve pratikte
    #    birbirine göre kaymış olabiliyor (ör. Ceket: urun_karti_id=2992/csv_card_id=2991,
    #    Gömlek: 2993/2992) → alakasız ürünler birbirinin "rengi" gibi görünüyordu
    #    (Bond Pötikare Ceket'in renk swatch'ında Silva Gömlek açılıyordu).
    def _id_cands(v):
        c = {v, str(v)}
        try:
            c.add(int(v))
        except Exception:
            pass
        return list(c)

    _card_ors = []
    if p.get("urun_karti_id") not in (None, ""):
        _card_ors.append({"urun_karti_id": {"$in": _id_cands(p.get("urun_karti_id"))}})
    if p.get("csv_card_id") not in (None, ""):
        _card_ors.append({"csv_card_id": {"$in": _id_cands(p.get("csv_card_id"))}})
    if _card_ors:
        cur = db.products.find(
            {"$or": _card_ors, "id": {"$ne": p["id"]}, "is_active": True}, _PROJ).limit(20)
        async for s in cur:
            if s["id"] in seen_ids or not _type_ok(s):
                continue
            seen_ids.add(s["id"]); siblings.append(_row(s))

    # 2) STOK KODU ile de eşleştir — renk varyantları AYNI stok kodunu paylaşır (ör. FCFW1300005).
    #    DENETİM FIX: anchor (kart id) TUTARSIZ olabilir (ör. Beyaz=2698 ama Acı Kahve/Siyah=2696)
    #    → yalnız kart id ile gruplayınca Beyaz DÜŞÜYORDU. Artık stok kodu + model adı eşleşmesini
    #    anchor ile BİRLİKTE kullanıp birleştiriyoruz (seen_ids dedup) → 3 renk de görünür.
    #    İSİM GUARD (Kadir): aynı stock_code'u FARKLI modeller paylaşabiliyor (ör.
    #    "Siyah Bermuda Şort" ile "Mini Bermuda Şort" ikisi de FCSS2700004). Bu yüzden
    #    stock_code eşleşmesi TEK BAŞINA yeterli değil — adayın "tüm renkler soyulmuş model
    #    adı", mevcut ürününkiyle EŞLEŞMELİ (renk konumdan bağımsız). Böylece farklı model
    #    aynı SKU'da olsa bile renk swatch'ına KARIŞMAZ; gerçek renk aileleri korunur.
    #    Not: color-siblings YALNIZ ürün-seviyesi stock_code kullanır (varyant-seviyesi merge yok).
    _sc = str(p.get("stock_code") or "").strip()
    if _sc:
        _self_model = _model_key(p)
        cur = db.products.find(
            {"stock_code": _sc, "id": {"$ne": p["id"]}, "is_active": True}, _PROJ).limit(20)
        async for s in cur:
            if s["id"] in seen_ids:
                continue
            # Her iki taraf da anlamlı model kimliğine indirgenebiliyorsa VE farklıysa →
            # aynı SKU olsa da FARKLI ürün → renk swatch'ına katma.
            if not _type_ok(s):
                continue
            if _self_model:
                _cand_model = _model_key(s)
                if _cand_model and _cand_model != _self_model:
                    continue
            seen_ids.add(s["id"]); siblings.append(_row(s))

    # 3) MODEL ADI ile eşleştir (renk kelimesi soyulmuş taban ad) — kart id VE stok kodu farklı
    #    eklenmiş renkleri de yakalar. Yalnız fallback DEĞİL: her zaman çalışır ve birleşir.
    base = _strip_trailing_color(p.get("name") or "")
    if base and len(base) >= 6:
        rx = "^" + _re.escape(base) + r"(\s|$)"
        cur = db.products.find(
            {"name": {"$regex": rx, "$options": "i"}, "id": {"$ne": p["id"]},
             "is_active": True}, _PROJ).limit(20)
        async for s in cur:
            if s["id"] in seen_ids or not _type_ok(s):
                continue
            seen_ids.add(s["id"]); siblings.append(_row(s))

    # ÜYELERE ÖZEL: misafire üyelere özel renk kardeşi gösterilmez; kaynak ürün üyelere
    # özelse hiç kardeş dönmez.
    if not request_is_member(request):
        if await product_id_is_members_only(product_id):
            return {"siblings": []}
        siblings = await strip_members_only(request, siblings)
    return {"siblings": siblings}


# ── BENZER ÜRÜNLER (similar) — promo/sanal kategorileri HARİÇ tutup GERÇEK ürün-tipini kullanır.
# Kök promo kategorileri (İNDİRİM/KOLEKSİYONLAR/EN YENİLER…) ve tüm alt ağaçları benzerlik
# temeli OLAMAZ; ürünün en spesifik GERÇEK tip kategorisi (Şort/Etek/Elbise…) esas alınır.
_PROMO_ROOT_NAMES = {
    "indirim", "indirimler", "koleksiyon", "koleksiyonlar", "en yeniler", "yeniler",
    "yeni gelenler", "yeni", "outlet", "firsat", "firsatlar", "kampanya", "kampanyalar",
    "cok satanlar", "one cikanlar", "one cikan", "populer", "sepette indirim", "tum urunler",
}
# Ürün ADINDAN tip çıkarımı — (normalize anahtar, kanonik tip kategori adı). Sıra: SPESİFİK önce.
_TYPE_KEYWORDS = [
    ("sortolon", "Şortolon"), ("sweatshirt", "Sweatshirt"), ("trenckot", "Trençkot"),
    ("trench", "Trençkot"), ("tisort", "Tişört"), ("t-shirt", "Tişört"), ("tshirt", "Tişört"),
    ("bermuda", "Şort"), ("kapri", "Şort"), ("sort", "Şort"), ("etek", "Etek"),
    ("elbise", "Elbise"), ("gomlek", "Gömlek"), ("ceket", "Ceket"), ("pantolon", "Pantolon"),
    ("jean", "Pantolon"), ("kot", "Pantolon"), ("tayt", "Tayt"), ("bluz", "Bluz"),
    ("kazak", "Kazak"), ("hirka", "Hırka"), ("yelek", "Yelek"), ("tunik", "Tunik"),
    ("sweat", "Sweatshirt"), ("mont", "Mont"), ("kaban", "Kaban"), ("body", "Body"),
    ("takim", "Takım"), ("pelerin", "Pelerin"), ("fular", "Fular"), ("atki", "Atkı"),
    ("canta", "Çanta"), ("bodi", "Body"),
]


def _cnorm(s: str) -> str:
    """Türkçe-duyarsız normalize (kategori/tip isim eşleştirme için).

    KRİTİK: Türkçe 'İ'.casefold() → 'i' + U+0307 (birleşik nokta) üretir; düz harf-değişimi
    bu birleşik noktayı BIRAKIR → 'İNDİRİM'/'KOLEKSİYONLAR' promo setine EŞLEŞMEZDİ. Bu yüzden
    ı/İ elle 'i'ye çevrilir, sonra NFKD + birleşik-işaret (combining) temizliğiyle ş/ğ/ü/ö/ç
    aksanları da güvenle soyulur."""
    import unicodedata as _ud
    s = (s or "").replace("ı", "i").replace("İ", "i")
    s = _ud.normalize("NFKD", s.casefold())
    s = "".join(ch for ch in s if not _ud.combining(ch))
    return " ".join(s.split()).strip()


@router.get("/{product_id}/similar")
async def get_similar_products(product_id: str, request: Request, limit: int = 4):
    """Benzer ürünler: ürünün GERÇEK tip kategorisindeki (promo/sanal kategoriler HARİÇ)
    ürünleri döndürür. Tip kategorisi yoksa ürün ADINDAN tip çıkarır. Hiçbiri tutmazsa
    BOŞ döner (rastgele promo ürünüyle DOLDURMAZ — yanlış öneriden iyidir). PUBLIC.
    ÜYELERE ÖZEL: misafire members_only ürünleri gösterilmez."""
    try:
        limit = max(1, min(int(limit or 4), 24))
    except Exception:
        limit = 4
    p = await db.products.find_one(
        {"$or": [{"id": product_id}, {"slug": product_id}]},
        {"_id": 0, "id": 1, "name": 1, "category_ids": 1, "category_id": 1, "category_name": 1},
    )
    if not p:
        return {"similar": [], "basis": None}

    cats = await db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "parent_id": 1}).to_list(5000)
    by_id = {str(c.get("id")): c for c in cats if c.get("id") is not None}
    name_to_cat = {}
    for c in cats:
        nm = _cnorm(c.get("name"))
        # Aynı isimden birden çok varsa promo-olmayanı tercih et (aşağıda kök kontrolü ile).
        name_to_cat.setdefault(nm, c)

    def _root_name(cid):
        cur = by_id.get(str(cid)); g = 0
        while cur and cur.get("parent_id") and g < 25:
            nxt = by_id.get(str(cur.get("parent_id")))
            if not nxt:
                break
            cur = nxt; g += 1
        return _cnorm((cur or {}).get("name"))

    def _depth(cid):
        cur = by_id.get(str(cid)); d = 0; g = 0
        while cur and cur.get("parent_id") and g < 25:
            cur = by_id.get(str(cur.get("parent_id"))); d += 1; g += 1
        return d

    def _is_promo_cat(cid):
        c = by_id.get(str(cid))
        if not c:
            return False
        # Kategorinin KENDİSİ promo-kök adı VEYA kök atası promo → benzerlik temeli olamaz.
        return _cnorm(c.get("name")) in _PROMO_ROOT_NAMES or _root_name(cid) in _PROMO_ROOT_NAMES

    # 1) Ürünün category_ids'inden GERÇEK (promo olmayan) tip kategorilerini süz, EN SPESİFİK (derin) seç.
    cand_ids = [str(x) for x in (p.get("category_ids") or []) if x not in (None, "")]
    if not cand_ids and p.get("category_id"):
        cand_ids = [str(p.get("category_id"))]
    real_ids = [cid for cid in cand_ids if cid in by_id and not _is_promo_cat(cid)]
    basis_cat_id = None
    basis = None
    if real_ids:
        basis_cat_id = max(real_ids, key=_depth)  # en spesifik tip
        basis = {"type": "category", "category_id": basis_cat_id,
                 "category_name": (by_id.get(basis_cat_id) or {}).get("name")}

    # 2) Gerçek kategori yoksa: ADINDAN tip çıkar → o tip kategorisini bul.
    if not basis_cat_id:
        nm = _cnorm(p.get("name"))
        for kw, canon in _TYPE_KEYWORDS:
            if kw in nm:
                cat = name_to_cat.get(_cnorm(canon))
                if cat and not _is_promo_cat(str(cat.get("id"))):
                    basis_cat_id = str(cat.get("id"))
                    basis = {"type": "name->category", "keyword": kw,
                             "category_id": basis_cat_id, "category_name": cat.get("name")}
                else:
                    # Tip kategorisi yoksa ürün ADINDA tipi geçen ürünleri getir (regex fallback).
                    basis = {"type": "name->regex", "keyword": kw, "canonical": canon}
                break

    _PROJ = {"_id": 0, "id": 1, "slug": 1, "name": 1, "price": 1, "sale_price": 1,
             "market_price": 1, "images": 1, "image": 1, "thumbnail": 1, "stock": 1,
             "variants": 1, "is_new": 1, "is_featured": 1, "category_name": 1,
             "discount_percentage": 1, "brand": 1,
             # Kampanya kapsamı (rozet: % ve "3 AL 2 ÖDE") için gerekli
             "category_ids": 1, "category_id": 1}

    results = []
    base_q = {"is_active": True, "is_deleted": {"$ne": True}, "id": {"$ne": p["id"]}}
    # ÜYELERE ÖZEL: misafir (geçerli JWT yok) members_only ürünleri benzer listesinde görmesin.
    _is_member_sim = request_is_member(request)   # denetim #11: doğrulanmış oturum
    if not _is_member_sim:
        _mo_ids = await _members_only_cat_ids()
        if _mo_ids:
            base_q["$and"] = members_only_exclusion(_mo_ids)
    if basis_cat_id:
        cur = db.products.find(
            {**base_q, "$or": [{"category_ids": basis_cat_id}, {"category_id": basis_cat_id}]},
            _PROJ).limit(limit * 3)
        async for s in cur:
            results.append(s)
    elif basis and basis.get("type") == "name->regex":
        import re as _re2
        rx = _re2.escape(basis["keyword"])
        cur = db.products.find(
            {**base_q, "name": {"$regex": rx, "$options": "i"}}, _PROJ).limit(limit * 3)
        async for s in cur:
            results.append(s)

    # Dedup + kırp. (Kaynak yoksa BOŞ döner — rastgele doldurma YOK.)
    seen = set()
    out = []
    for s in results:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        out.append(s)
        if len(out) >= limit:
            break
    # Vitrinin geri kalanıyla AYNI kampanya rozetleri (yüzde + "X AL Y ÖDE"); eskiden yoktu.
    out = await _attach_campaign_badges(out)
    # GÜVENLİK (IAM-01): projeksiyon 'variants'ı TAM alır; varyantlardaki alış fiyatı/üye fiyatı/
    # tedarikçi/marj herkese açık bu uçta sızıyordu → public /products ile aynı süzgeç.
    out = [_strip_internal_fields(s) for s in out]
    return {"similar": out, "basis": basis}


# Türkçe moda renk sözlüğü — ad-tabanlı renk-kardeşi eşleştirmesi için (sondaki renk sözcüğü).
_TR_COLOR_WORDS = {
    "siyah", "beyaz", "ekru", "krem", "krem rengi", "bej", "kahverengi", "kahve", "vizon",
    "camel", "taş", "tas", "gri", "antrasit", "füme", "fume", "lacivert", "mavi", "açık mavi",
    "koyu mavi", "bebe mavi", "buz mavi", "petrol", "petrol mavisi", "mint", "su yeşili",
    "yeşil", "yesil", "haki", "koyu yeşil", "açık yeşil", "zümrüt", "benetton", "kırmızı",
    "kirmizi", "bordo", "pembe", "açık pembe", "koyu pembe", "pudra", "gül kurusu", "fuşya",
    "fusya", "somon", "mor", "lila", "leylak", "turuncu", "sarı", "sari", "hardal", "altın",
    "altin", "gold", "gümüş", "gumus", "silver", "metalik", "leopar", "zebra", "yılan",
    "çok renkli", "desenli", "ebru", "koyu gri", "açık gri", "menekşe", "nar çiçeği",
    # Ek yaygın renkler (color-siblings model-guard'ı için — eksik renk aileyi BÖLMESİN).
    # SADECE net renkler; tip/kumaş sözcükleri (kot, jean…) EKLENMEZ.
    "acı kahve", "aci kahve", "koyu kahve", "açık kahve", "acik kahve", "mürdüm", "murdum",
    "vişne", "visne", "indigo", "kiremit", "tarçın", "tarcin", "bakır", "bakir",
    "fıstık", "fistik", "gece mavisi", "koyu bordo", "bronz",
}


def _strip_trailing_color(name: str) -> str:
    """Ad sonundaki renk sözcüğünü (1-2 kelime) soyup taban model adını döndürür."""
    words = (name or "").strip().split()
    if not words:
        return ""
    last1 = words[-1].lower()
    last2 = " ".join(words[-2:]).lower() if len(words) >= 2 else ""
    if last2 and last2 in _TR_COLOR_WORDS:
        return " ".join(words[:-2]).strip()
    if last1 in _TR_COLOR_WORDS:
        return " ".join(words[:-1]).strip()
    return ""  # renk sözcüğü bulunamadı → ada göre gruplama yapma (yanlış eşleşme riski)


def _trailing_color(name: str) -> str:
    """Ad sonundaki renk sözcüğünü (etiket için) döndürür."""
    words = (name or "").strip().split()
    if not words:
        return ""
    last2 = " ".join(words[-2:]).lower() if len(words) >= 2 else ""
    if last2 and last2 in _TR_COLOR_WORDS:
        return " ".join(words[-2:])
    if words[-1].lower() in _TR_COLOR_WORDS:
        return words[-1]
    return ""


def _strip_all_colors(name: str) -> str:
    """Addaki TÜM renk sözcüklerini (KONUMDAN BAĞIMSIZ — başta/sonda/ortada; çok-kelimeli
    renkler dahil) çıkarıp Türkçe-duyarsız normalize eder → model kimliği.

    color-siblings adım (2) guard'ı için: aynı stock_code'u paylaşan ama FARKLI model olan
    ürünler (ör. "Siyah Bermuda Şort" vs "Mini Bermuda Şort Siyah") birbirine karışmasın.
      "Siyah Bermuda Şort"       → "bermuda sort"
      "Mini Bermuda Şort Siyah"  → "mini bermuda sort"   (eşleşmez → ayrılır)
    Ad tamamen renkten ibaretse '' döner → çağıran guard'ı UYGULAMAZ (yanlış boş-eşleşme yok)."""
    words = (name or "").strip().split()
    if not words:
        return ""
    low = [w.lower() for w in words]
    n = len(words)
    keep = [True] * n
    i = 0
    while i < n:
        # Önce 2-kelimeli renkler ("Açık Mavi", "Gül Kurusu", "Krem Rengi"…).
        if i + 1 < n and f"{low[i]} {low[i + 1]}" in _TR_COLOR_WORDS:
            keep[i] = keep[i + 1] = False
            i += 2
            continue
        if low[i] in _TR_COLOR_WORDS:
            keep[i] = False
        i += 1
    base = " ".join(w for w, k in zip(words, keep) if k)
    return _cnorm(base)  # küçük harf + Türkçe-duyarsız + boşluk normalize


def _resolve_prod_color(prod: dict) -> str:
    """Ürünün rengini bulur: color alanı → varyant color → 'Web Color'/'Renk' attribute →
    son çare addaki sondaki renk sözcüğü. (Statik sözlükten BAĞIMSIZ — asıl renk verisi.)"""
    c = (prod.get("color") or "").strip()
    if c:
        return c
    for v in (prod.get("variants") or []):
        if isinstance(v, dict) and (v.get("color") or "").strip():
            return v["color"].strip()
    for a in (prod.get("attributes") or []):
        if isinstance(a, dict) and (a.get("name") or a.get("type") or "").strip().lower() in (
                "web color", "renk", "color"):
            if (a.get("value") or "").strip():
                return str(a["value"]).strip()
    return _trailing_color(prod.get("name") or "")


def _model_key(prod: dict) -> str:
    """Ürünün RENK-BAĞIMSIZ model kimliği — color-siblings adım (2) guard'ı için.

    SAĞLAM: önce ürünün KENDİ rengini (color/variant/attribute) addan çıkarır (statik
    sözlük eksikse bile Acı Kahve/Mürdüm gibi renkler doğru soyulur), sonra kalanı statik
    renk sözlüğüyle de temizler (renk-önde 'Siyah X' gibi durumlar). Böylece aynı SKU'yu
    paylaşan FARKLI modeller ('Siyah Bermuda Şort' vs 'Mini Bermuda Şort') ayrılır; gerçek
    çok-renkli aileler ('...Ceket Bej' / '...Ceket Mürdüm') AYNI kimliğe iner → bozulmaz."""
    name = prod.get("name") or ""
    if not name.strip():
        return ""
    color = _resolve_prod_color(prod)
    base = name
    if color:
        cwords = set(_cnorm(color).split())
        if cwords:
            base = " ".join(w for w in name.split() if _cnorm(w) not in cwords)
    return _strip_all_colors(base)

async def _expand_category_ids(selected_ids):
    """Seçilen kategori id'lerini atalarıyla birlikte düzleştirir (vitrin category_ids için)."""
    sel = [str(c) for c in (selected_ids or []) if c]
    if not sel:
        return []
    cats = await db.categories.find({}, {"_id": 0, "id": 1, "parent_id": 1}).to_list(5000)
    parent = {c.get("id"): c.get("parent_id") for c in cats if c.get("id")}
    result, seen = [], set()
    for cid in sel:
        cur, guard = cid, 0
        while cur and cur not in seen and guard < 50:
            seen.add(cur); result.append(cur)
            cur = parent.get(cur); guard += 1
    return result


def _distinct_variant_colors(variants):
    """Varyant listesindeki BENZERSIZ renkleri (ilk görülme sırasıyla) döndürür."""
    seen = []
    for v in (variants or []):
        c = (v.get("color") or "").strip()
        if c and c.lower() not in [s.lower() for s in seen]:
            seen.append(c)
    return seen


def _variants_for_color(variants, color):
    """Verilen renge ait varyantları (beden vb.) döndürür."""
    cl = (color or "").strip().lower()
    return [v for v in (variants or []) if (v.get("color") or "").strip().lower() == cl]



def _size_tables_last(images):
    """Galeri sırası: normal görseller önce, is_size_table işaretli beden tablosu nesneleri EN SONA.
    Beden tablosu ilk sıraya düşünce ürün kartları (images[0]) nesneyi görsel sanıp boş kalıyordu."""
    if not isinstance(images, list):
        return images
    normal = [im for im in images if not (isinstance(im, dict) and im.get("is_size_table"))]
    tables = [im for im in images if isinstance(im, dict) and im.get("is_size_table")]
    return normal + tables


def _clean_fit_sizes(v) -> list:
    """Standart bedenli ürünün uyduğu bedenler: kısa metin listesi, tekrar yok, en fazla 12."""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        t = str(x or "").strip().upper()[:12]
        if t and t not in out:
            out.append(t)
    return out[:12]


# Panel/vitrin ile AYNI kural (frontend/src/lib/fitSizes.js::isStandardSized).
_STD_SIZE_NAMES = {"STD", "STANDART", "STANDARD", "TEK BEDEN", "TEKBEDEN", "ONE SIZE", "ONESIZE", "TEK"}


def _is_standard_sized(variants) -> bool:
    """Ürünün TÜM (bedenli) varyantları standart beden mi? En az bir bedenli varyant olmalı."""
    sizes = [" ".join(str((v or {}).get("size") or "").upper().split())
             for v in (variants or []) if isinstance(v, dict)]
    sizes = [x for x in sizes if x]
    return bool(sizes) and all(x in _STD_SIZE_NAMES for x in sizes)


def _sync_main_color_into_card(product_data: dict, existing: dict) -> None:
    """Ürün kartı TEK renktir (her renk ayrı kart). "Renk (Ana)" değiştirilince, eski renkte (ya da
    renksiz) kalan varyant renkleri ve Renk/Web Color özellikleri de aynı kayıtta yeni renge çekilir.
    Sorun (2026-10-07, Nova Pelerinli Gömlek Lacivert): kart Ekru'dan kopyalanmış, renk Ekru kalmıştı;
    panelde mevcut varyantın rengi düzenlenemediğinden ana rengi düzeltmek varyantları Ekru bırakıyordu.
    Kartta başka renkte varyant varsa (çok renkli eski kayıt) varyantlara DOKUNULMAZ."""
    if "color" not in product_data:
        return
    new = str(product_data.get("color") or "").strip()
    old = str((existing or {}).get("color") or "").strip()
    if not new or not old or new.lower() == old.lower():
        return
    vs = product_data.get("variants")
    if isinstance(vs, list) and vs and all(
            isinstance(v, dict) and str(v.get("color") or "").strip().lower() in ("", old.lower()) for v in vs):
        product_data["variants"] = [{**v, "color": new} for v in vs]
    attrs = product_data.get("attributes")
    if isinstance(attrs, list):
        out = []
        for a in attrs:
            if isinstance(a, dict) and str(a.get("name") or a.get("type") or "").strip().lower() in (
                    "renk", "web color", "color") and str(a.get("value") or "").strip().lower() == old.lower():
                a = {**a, "value": new}
            out.append(a)
        product_data["attributes"] = out


def _fit_sizes_to_push(product_data: dict, existing: dict):
    """Renk kardeşlerine yansıtılacak 'Uyduğu Bedenler': (liste, değişti_mi) ya da (None, False).
    Seçim bu kayıtta DEĞİŞTİYSE tüm standart bedenli kardeşlere yazılır; değişmediyse yalnız seçimi
    BOŞ kardeşler doldurulur (ilgisiz bir alan kaydı kardeşin elle girilmiş seçimini ezmez).
    Boş seçim (panel boşken Kaydet) gruba hiç yayılmaz."""
    if "fit_sizes" not in product_data:
        return None, False
    new = _clean_fit_sizes(product_data.get("fit_sizes"))
    if not new:
        return None, False
    return new, new != _clean_fit_sizes((existing or {}).get("fit_sizes"))


@router.post("")
async def create_product(
    product_data: dict,
    request: Request,
    current_user: dict = Depends(require_permission("products.create"))
):
    """Create new product (admin only)"""
    product_data.pop("images_clear_confirmed", None)   # yalnız düzenleme isteğinde anlamlı işaret
    variants = product_data.get("variants", [])
    
    # Fetch settings for default VAT
    settings = await db.settings.find_one({"id": "main"})
    default_vat = settings.get("default_vat_rate", 10) if settings else 10

    # Auto-generate barcodes efficiently
    used_barcodes_set = await build_used_barcode_set()
    
    # Auto-generate barcodes for variants if missing
    for v in variants:
        if not v.get("barcode"):
            barcode = await generate_barcode_from_range(used_barcodes_set)
            if barcode:
                v["barcode"] = barcode

    # Auto-generate urun_id (beden ID) for variants if missing:
    # sistemdeki EN YUKSEK urun_id + 1'den baslayip her bos bedene +1 ilerler.
    used_uid_set = await build_used_urun_id_set()
    for v in variants:
        if not str(v.get("urun_id") or "").strip():
            v["urun_id"] = next_urun_id(used_uid_set)
    # Beden kimliği kuralı: id = 4 haneli urun_id, ürünler arası tekil (kopyalanan ürün dahil).
    if variants:
        normalize_variant_ids(variants, used_uid_set, await urun_id_owner_map())

    # Also generate for main product if no variants and barcode is empty
    if not variants and not product_data.get("barcode"):
        barcode = await generate_barcode_from_range(used_barcodes_set)
        if barcode:
            product_data["barcode"] = barcode

    _pid = await generate_short_id("products")
    # Urun Kart ID: form "Kimlik & Kodlar > Urun Kart ID" (ticimax_fields.URUNKARTIID)
    # veya ust-seviye urun_karti_id; ikisi de bossa SON kart id + 1 otomatik atanir.
    # ÖNEMLİ: csv_card_id'ye FALLBACK YOK — yoksa renk kardesleri AYNI kart id'yi alirdi.
    # Renk kardesligi ayri bir grup anahtariyla (csv_card_id) tutulur.
    _tf_in = product_data.get("ticimax_fields") or {}
    urun_karti_id = str(_tf_in.get("URUNKARTIID") or product_data.get("urun_karti_id") or "").strip()
    if not urun_karti_id:
        urun_karti_id = await generate_urun_karti_id()
    # Renk-kardesi gruplama anahtari: gelen csv_card_id (coklu-renk POST'larinda PAYLASILIR)
    # yoksa bu urunun kendi kart id'si. Storefront "Diger Renkler" swatch'i bununla baglar.
    group_card_id = str(product_data.get("csv_card_id") or "").strip() or urun_karti_id
    # liste/etiket (urun_karti_id) ile form (ticimax_fields.URUNKARTIID) senkron
    product_data["ticimax_fields"] = {**_tf_in, "URUNKARTIID": urun_karti_id}
    _card = urun_karti_id
    _sel_cats = product_data.get("categories")
    if not _sel_cats and product_data.get("category_id"):
        _sel_cats = [product_data.get("category_id")]
    _sel_cats = [str(x) for x in (_sel_cats or []) if x]
    _cat_ids_all = await _expand_category_ids(_sel_cats)
    _primary_cat = _sel_cats[0] if _sel_cats else ""
    product = {
        "id": _pid,
        "urun_karti_id": urun_karti_id,
        "ticimax_fields": product_data.get("ticimax_fields", {}),
        "name": product_data.get("name", ""),
        # Slug HER ZAMAN {ad}-{ürün kart id} (Kadir: "ürün kart id olması gerekir"). Eskiden admin
        # formu {ad}-{Date.now()} gönderiyordu (ör. ...-1788251144324) ve olduğu gibi kabul ediliyordu.
        "slug": slug_with_card_id(product_data.get("name", ""), _card),
        "description": product_data.get("description", ""),
        "short_description": product_data.get("short_description", ""),
        "price": float(product_data.get("price", 0)),
        "sale_price": product_data.get("sale_price"),
        "category_name": product_data.get("category_name", ""),
        "category_id": _primary_cat,
        "category_ids": _cat_ids_all,
        "categories": _sel_cats,
        "brand": product_data.get("brand", "MELUNI"),
        "images": _size_tables_last(product_data.get("images", [])),
        "variants": product_data.get("variants", []),
        "attributes": product_data.get("attributes", []),
        # KÖK-NEDEN FIX: varyantlı üründe parent 'stock' istemciden ALINMAZ — Σvaryant'tan türetilir.
        # Aksi halde (admin formu/import parent=300 + varyant=0 gönderdiğinde) parent≠Σvaryant desync
        # doğuyordu (yüksek hayalet parent stok, satılamayan/yanıltıcı rakam). Varyantsız üründe istemci.
        "stock": (sum(int(v.get("stock", 0) or 0) for v in variants) if variants
                  else int(product_data.get("stock", 0) or 0)),
        "stock_code": product_data.get("stock_code", ""),
        "barcode": product_data.get("barcode", ""),
        "sku": product_data.get("sku", ""),
        "supplier": product_data.get("supplier", ""),
        "manufacturer": product_data.get("manufacturer", "MELUNİ"),
        "season": (product_data.get("season") or "").strip(),  # İlkbahar/Yaz/Sonbahar/Kış — rapor sezon filtresi buradan beslenir
        # Standart (tek) bedenli üründe "hangi bedenlere uyar" (panelden seçilir; vitrin: "X – Y bedenler arası uyumludur")
        "fit_sizes": _clean_fit_sizes(product_data.get("fit_sizes")),
        # FAZ 7 — İmalat modülü için ek alanlar
        "collection": product_data.get("collection", ""),   # ör. "2026 İlkbahar/Yaz"
        "purchase_price": float(product_data.get("purchase_price", 0) or 0),  # Alış fiyatı
        "color": product_data.get("color", ""),  # Renk (varyant dışı global)
        "is_active": product_data.get("is_active", True),
        "is_featured": product_data.get("is_featured", False),
        "is_new": product_data.get("is_new", False),
        "is_showcase": product_data.get("is_showcase", False),
        "is_opportunity": product_data.get("is_opportunity", False),
        "is_free_shipping": product_data.get("is_free_shipping", False),
        "vat_rate": product_data.get("vat_rate", default_vat),
        "use_default_markup": product_data.get("use_default_markup", True),
        "markup_rate": float(product_data.get("markup_rate", 0)),
        "trendyol_attributes": product_data.get("trendyol_attributes", {}),
        "hepsiburada_attributes": product_data.get("hepsiburada_attributes", {}),
        "temu_attributes": product_data.get("temu_attributes", {}),
        "hepsiburada_category_id": product_data.get("hepsiburada_category_id", ""),
        "hepsiburada_category_name": product_data.get("hepsiburada_category_name", ""),
        "temu_category_id": product_data.get("temu_category_id", ""),
        "temu_category_name": product_data.get("temu_category_name", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    # FARKLI RENK = AYRI ÜRÜN kuralı:
    # Varyantlarda birden fazla renk varsa her renk AYRI ürün olarak açılır.
    # Hepsi aynı csv_card_id + urun_karti_id'yi paylaşır → storefront "Diğer Renkler"
    # swatch'ında renk kardeşi olarak bağlanır. Bedenler her renk altında varyant kalır.
    _all_variants = product.get("variants") or []
    _colors = _distinct_variant_colors(_all_variants)
    # Renk-kardeşi gruplama anahtarı HER ZAMAN yazılır (manuel ürünler de bağlansın)
    product["csv_card_id"] = group_card_id

    if len(_colors) <= 1:
        if _colors and not product.get("color"):
            product["color"] = _colors[0]
        await db.products.insert_one(product)
        await record_stock_audit(
            db, product_id=product["id"], product_name=product.get("name", ""),
            before={}, after=product, source="admin_create", current_user=current_user,
            request=request, action="product_created",
        )
        await record_admin_audit(
            db, action="product.create", entity_type="product", entity_id=product["id"],
            before={}, after=product, current_user=current_user, request=request,
            source="products",
        )
        logger.info(f"Product created: {product['id']}")
        return {"id": product["id"], "message": "Ürün oluşturuldu"}

    # Çok renkli → her renk ayrı ürün (ilk renk ana üründe, diğerleri yeni id)
    _base_name = product.get("name") or ""
    _created_ids = []
    for _idx, _col in enumerate(_colors):
        _doc = {k: v for k, v in product.items()}
        _doc["id"] = product["id"] if _idx == 0 else await generate_short_id("products")
        # Her renk AYRI (benzersiz) Urun Kart ID alir: ilk renk taban id'de kalir,
        # sonraki renkler sistemdeki max + 1 ile otomatik artar. Insert'ler sirali
        # (await) oldugu icin generate_urun_karti_id her seferinde bir oncekini gorur.
        _color_card = urun_karti_id if _idx == 0 else await generate_urun_karti_id()
        _doc["urun_karti_id"] = _color_card
        _doc["ticimax_fields"] = {**(_doc.get("ticimax_fields") or {}), "URUNKARTIID": _color_card}
        _doc["csv_card_id"] = group_card_id   # renk kardesligi PAYLASIMLI → "Diger Renkler" bagli kalir
        _doc["color"] = _col
        _doc["variants"] = _variants_for_color(_all_variants, _col)
        _doc["slug"] = slug_with_card_id(f"{_base_name} {_col}", _color_card)
        _now = datetime.now(timezone.utc).isoformat()
        _doc["created_at"] = _doc.get("created_at") or _now
        _doc["updated_at"] = _now
        await db.products.insert_one(_doc)
        await record_stock_audit(
            db, product_id=_doc["id"], product_name=_doc.get("name", ""),
            before={}, after=_doc, source="admin_create", current_user=current_user,
            request=request, action="product_created",
        )
        await record_admin_audit(
            db, action="product.create", entity_type="product", entity_id=_doc["id"],
            before={}, after=_doc, current_user=current_user, request=request,
            source="products",
        )
        _created_ids.append(_doc["id"])
    logger.info(f"Product created with color split: {_created_ids} (card {urun_karti_id})")
    return {
        "id": _created_ids[0],
        "split": True,
        "color_count": len(_colors),
        "product_ids": _created_ids,
        "message": f"{len(_colors)} renk ayrı ürün olarak oluşturuldu",
    }


@router.post("/{product_id}/split-by-color", dependencies=[Depends(require_permission("products.edit"))])
async def split_product_by_color(product_id: str):
    """Mevcut bir ürünün farklı RENK varyantlarını AYRI ürünlere böler.
    İlk renk ana üründe kalır; diğer renkler yeni ürün olur. Hepsi aynı
    csv_card_id + urun_karti_id'yi paylaşır → "Diğer Renkler" swatch'ında bağlı kalır.
    Bedenler her renk ürününün altında varyant olarak kalır.
    """
    p = await db.products.find_one({"id": product_id})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    variants = p.get("variants") or []
    colors = _distinct_variant_colors(variants)
    if len(colors) <= 1:
        return {
            "success": False,
            "color_count": len(colors),
            "message": "Bu üründe birden fazla renk yok; ayırma gerekmedi.",
        }
    card = str(p.get("csv_card_id") or p.get("urun_karti_id") or p.get("id"))
    base_name = p.get("name") or ""
    now = datetime.now(timezone.utc).isoformat()
    created_ids = [product_id]
    # İlk renk → ana üründe kalır
    first = colors[0]
    await db.products.update_one({"id": product_id}, {"$set": {
        "variants": _variants_for_color(variants, first),
        "color": first,
        "csv_card_id": card,
        "urun_karti_id": p.get("urun_karti_id") or card,
        "slug": slug_with_card_id(f"{base_name} {first}", card),
        "updated_at": now,
    }})
    # Diğer renkler → yeni ürün
    for col in colors[1:]:
        nid = await generate_short_id("products")
        clone = {k: v for k, v in p.items() if k not in ("_id", "id", "slug")}
        clone["id"] = nid
        clone["csv_card_id"] = card
        clone["urun_karti_id"] = p.get("urun_karti_id") or card
        clone["color"] = col
        clone["variants"] = _variants_for_color(variants, col)
        clone["slug"] = slug_with_card_id(f"{base_name} {col}", card)
        clone["created_at"] = now
        clone["updated_at"] = now
        await db.products.insert_one(clone)
        created_ids.append(nid)
    logger.info(f"Product split by color: {product_id} -> {created_ids} (card {card})")
    return {
        "success": True,
        "color_count": len(colors),
        "product_ids": created_ids,
        "message": f"{len(colors)} renk ayrı ürüne bölündü.",
    }

@router.post("/{product_id}/duplicate", dependencies=[Depends(require_permission("products.create"))])
async def duplicate_product(product_id: str):
    """Bir ürünü BAĞIMSIZ kopya olarak çoğaltır.

    - Her çoğaltmada YENİ ve benzersiz Ürün Kart ID atanır (paylaşılmaz; orijinalle
      aynı kart id'yi ALMAZ). Böylece "farklı ürün = farklı kart id" sağlanır.
    - Tüm varyantlara aralıktan YENİ benzersiz barkod üretilir → orijinalin
      barkodlarıyla çakışmaz (eski 'duplicate'in patlama sebebi buydu).
    - Varyant id'leri yenilenir; Ticimax varyant id'si (urun_id) temizlenir.
    - stock_code AYNI bırakılır (aynı modelin başka rengini açmak için pratik;
      gerekiyorsa kopyada elle değiştirilir).
    Kopya orijinalin renk-kardeşi DEĞİLDİR (csv_card_id yeni kart id'ye eşitlenir).
    """
    p = await db.products.find_one({"id": product_id})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    new_card = await generate_urun_karti_id()
    new_id = await generate_short_id("products")
    used = await build_used_barcode_set()
    used_uid = await build_used_urun_id_set()

    clone = {k: v for k, v in p.items() if k not in ("_id", "id", "slug", "slug_aliases")}
    clone["id"] = new_id
    clone["urun_karti_id"] = new_card
    clone["csv_card_id"] = new_card          # bağımsız kart (renk-kardeşi değil)
    clone["urun_id"] = ""
    _tf = dict(clone.get("ticimax_fields") or {})
    _tf["URUNKARTIID"] = new_card
    _tf["URUNID"] = ""
    clone["ticimax_fields"] = _tf

    base_name = p.get("name") or ""
    clone["name"] = f"{base_name} (Kopya)"
    clone["slug"] = slug_with_card_id(clone["name"], new_card)

    # Varyantlar: yeni id + yeni barkod, Ticimax varyant id'si temizlenir
    new_vars = []
    for v in (p.get("variants") or []):
        nv = dict(v)
        nv["id"] = generate_id()
        nv["urun_id"] = next_urun_id(used_uid)
        nv["barcode"] = (await generate_barcode_from_range(used)) or ""
        new_vars.append(nv)
    clone["variants"] = new_vars
    if new_vars:
        clone["barcode"] = ""
    else:
        clone["barcode"] = (await generate_barcode_from_range(used)) or ""

    now = datetime.now(timezone.utc).isoformat()
    clone["created_at"] = now
    clone["updated_at"] = now

    await db.products.insert_one(clone)
    logger.info(f"Product duplicated: {product_id} -> {new_id} (card {new_card})")
    return {"id": new_id, "urun_karti_id": new_card, "message": "Ürün kopyalandı"}


@router.post("/assign-variant-ids", dependencies=[Depends(require_permission("products.edit"))])
async def assign_variant_ids(payload: dict):
    """Varyantlara BEDEN bazında Ticimax varyant id'si (urun_id) atar.

    Beden id'si eksik ürünler için tek seferlik düzeltme (ör. siyah bermuda şort).
    Body:
      {"product_id": "...", "map": {"S":"8618","XS":"8620",...}}
      veya
      {"name": "bermuda", "color": "siyah", "map": {...}}
    Yalnızca urun_id'si BOŞ olan varyantlara yazar (dolu olanı ezmez).
    """
    size_map = {
        str(k).strip().upper(): str(v).strip()
        for k, v in (payload.get("map") or {}).items()
        if str(v).strip()
    }
    if not size_map:
        raise HTTPException(status_code=400, detail="map (beden->id) zorunlu")

    pid = str(payload.get("product_id") or "").strip()
    if pid:
        query = {"id": pid}
    else:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="product_id veya name gerekli")
        query = {"name": {"$regex": re.escape(name), "$options": "i"}}

    color = str(payload.get("color") or "").strip().lower()
    updated_products = 0
    updated_variants = 0
    touched = []
    async for p in db.products.find(query):
        variants = p.get("variants") or []
        if color and not any((v.get("color") or "").strip().lower() == color for v in variants):
            continue
        changed = False
        for v in variants:
            sz = str(v.get("size") or "").strip().upper()
            if sz in size_map and not str(v.get("urun_id") or "").strip():
                v["urun_id"] = size_map[sz]
                updated_variants += 1
                changed = True
        if changed:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"variants": variants, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            updated_products += 1
            touched.append({"id": p["id"], "name": p.get("name")})
    return {
        "updated_products": updated_products,
        "updated_variants": updated_variants,
        "products": touched,
        "map": size_map,
    }


@router.post("/backfill-variant-ids", dependencies=[Depends(require_permission("products.edit"))])
async def backfill_variant_ids(payload: dict = None):
    """id / urun_id'si EKSİK varyantlara OTOMATİK id + urun_id atar (elle map gerekmez).

    Sonradan eklenen varyantlar (ör. XXS) id'siz kalmışsa toplu düzeltir. Yalnız BOŞ olanı
    doldurur, dolu id/urun_id'yi EZMEZ.
    Body (opsiyonel):
      {}                          -> TÜM ürünler
      {"product_id": "..."}       -> tek ürün
      {"name": "keten elbise"}    -> isme göre (regex, birden çok ürün olabilir)
    """
    payload = payload or {}
    pid = str(payload.get("product_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if pid:
        query = {"id": pid}
    elif name:
        query = {"name": {"$regex": re.escape(name), "$options": "i"}}
    else:
        query = {}

    used_uid_set = await build_used_urun_id_set()
    updated_products = 0
    updated_variants = 0
    touched = []
    async for p in db.products.find(query):
        variants = p.get("variants") or []
        changed = False
        for v in variants:
            if not str(v.get("id") or "").strip():
                v["id"] = generate_id()
                changed = True
            if not str(v.get("urun_id") or "").strip():
                v["urun_id"] = next_urun_id(used_uid_set)
                updated_variants += 1
                changed = True
        if changed:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"variants": variants, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            updated_products += 1
            touched.append({"id": p["id"], "name": p.get("name")})
    return {
        "updated_products": updated_products,
        "updated_variants": updated_variants,
        "products": touched[:100],
    }


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    product_data: dict,
    request: Request,
    current_user: dict = Depends(require_permission("products.edit"))
):
    """Update product (admin only)"""
    existing = await db.products.find_one({"id": product_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    
    # Update slug if name changed — format: isim-kartid (tüm linkler tek biçim).
    # Eski slug, kırılan link/SEO olmaması için slug_aliases'a eklenir.
    if product_data.get("name") and product_data.get("name") != existing.get("name"):
        # Ürünün KENDİ kart id'si (csv_card_id renk-grubu anahtarıdır, slug'a yazılmaz).
        _card = existing.get("urun_karti_id") or existing.get("csv_card_id") or product_id
        new_slug = slug_with_card_id(product_data["name"], _card)
        old_slug = existing.get("slug")
        product_data["slug"] = new_slug
        if old_slug and old_slug != new_slug:
            aliases = list(existing.get("slug_aliases") or [])
            if old_slug not in aliases:
                aliases.append(old_slug)
            product_data["slug_aliases"] = aliases
    elif product_data.get("slug") and product_data.get("slug") != existing.get("slug"):
        # Ad değişmeden gelen slug yalnız {..}-{ürün kart id} biçimindeyse kabul edilir.
        _card = str(existing.get("urun_karti_id") or "").strip()
        if not (_card and str(product_data["slug"]).endswith("-" + _card)):
            product_data.pop("slug", None)
    
    
    # Auto-generate barcodes for variants if missing
    variants = product_data.get("variants", [])
    if variants:
        used_barcodes_set = await build_used_barcode_set()
        # Mevcut ürüne SONRADAN eklenen varyantlara da id + urun_id (beden ID) ata.
        # Önceden yalnızca create_product atıyordu; update_product yalnız barkod üretip
        # id/urun_id'yi BOŞ bırakıyordu → yeni varyantlar id'siz kalıyor, varyant eşleşmesi
        # (sepet/HB/Trendyol) ve stok işlemleri bozuluyordu.
        used_uid_set = await build_used_urun_id_set()
        for v in variants:
            if not v.get("barcode") or v.get("barcode") == "":
                barcode = await generate_barcode_from_range(used_barcodes_set)
                if barcode:
                    v["barcode"] = barcode
            if not str(v.get("id") or "").strip():
                v["id"] = generate_id()
            if not str(v.get("urun_id") or "").strip():
                v["urun_id"] = next_urun_id(used_uid_set)
        # Beden kimliği kuralı: id = 4 haneli urun_id, başka ürünün ID'si kopyalanmış olamaz.
        normalize_variant_ids(variants, used_uid_set, await urun_id_owner_map(product_id))

    # KÖK-NEDEN FIX (desync önleme): varyantlı üründe parent 'stock' HER ZAMAN Σvaryant'tır —
    # istemci (admin formu / import) parent'ı bağımsız YAZAMAZ. Yeni varyant geldiyse ondan, yoksa
    # mevcut varyanttan hesaplanır. Bu, 14 REVIEW ürününde görülen "parent yüksek, varyant 0"
    # hayalet stok desync'inin TEK gerçek kaynağıydı (ürün oluşturma/import parent'ı doğrudan yazıyordu).
    _eff_variants = variants if variants else (existing.get("variants") or [])
    if _eff_variants:
        product_data["stock"] = sum(int(v.get("stock", 0) or 0) for v in _eff_variants)

    if ("categories" in product_data) or ("category_id" in product_data):
        _sel = product_data.get("categories")
        if _sel is None and product_data.get("category_id"):
            _sel = [product_data.get("category_id")]
        _sel = [str(x) for x in (_sel or []) if x]
        product_data["categories"] = _sel
        product_data["category_ids"] = await _expand_category_ids(_sel)
        if _sel:
            product_data["category_id"] = _sel[0]
            # KRİTİK: Birincil kategoriden TÜREYEN alanları (category_name / category_slug /
            # breadcrumb) YENİDEN yaz. Aksi halde bir kategoriden çıkarılan ürün, ESKİ
            # breadcrumb/category_slug/category_name yüzünden site kategori sorgusunda (çoklu-alan
            # OR eşleşmesi) o kategoride GÖRÜNMEYE DEVAM ediyordu ("çıkardığım kategoride hâlâ çıkıyor").
            _cmap = {}
            async for _c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "parent_id": 1}):
                if _c.get("id"):
                    _cmap[_c["id"]] = _c
            _prim = _cmap.get(_sel[0], {})
            _pname = _prim.get("name") or ""
            product_data["category_name"] = _pname
            product_data["category_slug"] = _prim.get("slug") or (generate_slug(_pname) if _pname else "")
            # breadcrumb = birincil kategori + ataları (kök→yaprak), ">" ile birleştir.
            _names, _cur, _g = [], _sel[0], 0
            while _cur and _cur in _cmap and _g < 50:
                _names.append(_cmap[_cur].get("name") or "")
                _cur = _cmap[_cur].get("parent_id"); _g += 1
            product_data["breadcrumb"] = ">".join([n for n in reversed(_names) if n])
        else:
            # Tüm kategoriler çıkarıldı → TÜREV alanları TEMİZLE ki site sorgusu artık eşleşmesin.
            product_data["category_id"] = None
            product_data["category_name"] = ""
            product_data["category_slug"] = ""
            product_data["breadcrumb"] = ""

    # Form "Urun Kart ID" alani ticimax_fields.URUNKARTIID'e yazar -> ust-seviye ile senkronla
    _tf_u = product_data.get("ticimax_fields") or {}
    _kid = str(_tf_u.get("URUNKARTIID") or product_data.get("urun_karti_id") or "").strip()
    if _kid:
        product_data["urun_karti_id"] = _kid
    # Bos urun_karti_id gonderilirse mevcut degeri ezme (sadece dolu ise guncelle)
    if "urun_karti_id" in product_data and not str(product_data.get("urun_karti_id") or "").strip():
        product_data.pop("urun_karti_id", None)

    # GÖRSEL KİLİDİ. Yönetim panelden görsel listesini değiştirdiyse (yapay
    # zekâ ile ürettiyse, sıraladıysa, sildiyse) o ürünün görselleri artık
    # bize aittir: tedarikçi aktarımı bir daha üzerine yazmaz. Bayrak
    # konmadığında yeni görseller ertesi günkü aktarımda siliniyor ve ürün
    # tedarikçinin düşük çözünürlüklü fotoğraflarına geri dönüyordu.
    if isinstance(product_data.get("images"), list):
        _yeni_g = [str(u) for u in product_data["images"] if u]
        _eski_g = [str(u) for u in (existing.get("images") or []) if u]
        if _yeni_g and _yeni_g != _eski_g:
            product_data["images_custom"] = True
            product_data["images_custom_at"] = datetime.now(timezone.utc).isoformat()

    # ALIŞ FİYATI KORUMASI. Bu uç gelen gövdeyi olduğu gibi $set ediyor; admin
    # formu alanı boş/0 gönderdiğinde (alan doldurulmamış, sekme hiç açılmamış
    # ya da yanıt alanı taşımamışsa) DOLU olan maliyet sıfırlanıyordu. Maliyet
    # kârlılık raporlarının ve dropshipping alım listesinin dayanağı; sessizce
    # silinmesi kabul edilemez. Boş/sıfır geldiğinde mevcut değer korunur —
    # gerçekten sıfırlamak isteyen özel alanı (purchase_price_clear) gönderir.
    for _alan in ("purchase_price", "cost_price"):
        if _alan in product_data:
            try:
                _yeni = float(product_data.get(_alan) or 0)
            except (TypeError, ValueError):
                _yeni = 0.0
            _mevcut = float(existing.get(_alan) or 0)
            if _yeni <= 0 and _mevcut > 0 and not product_data.get("purchase_price_clear"):
                product_data.pop(_alan, None)
    product_data.pop("purchase_price_clear", None)
    # Varyantlarda da aynı koruma: form varyant satırında maliyet taşımıyorsa
    # o varyantın mevcut maliyeti korunur.
    if variants:
        _eski_v = {str(v.get("id") or v.get("barcode") or v.get("stock_code") or ""): v
                   for v in (existing.get("variants") or [])}
        for v in variants:
            _anahtar = str(v.get("id") or v.get("barcode") or v.get("stock_code") or "")
            _onceki = _eski_v.get(_anahtar) or {}
            for _alan in ("purchase_price", "cost_price"):
                try:
                    _y = float(v.get(_alan) or 0)
                except (TypeError, ValueError):
                    _y = 0.0
                if _y <= 0 and float(_onceki.get(_alan) or 0) > 0:
                    v[_alan] = _onceki[_alan]

    product_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # MANUEL STOK DÜZELTMESİ DENETİMİ (delta için TAZE okuma): üstteki 'existing'
    # fonksiyon başında okundu; buraya kadarki await'lerde araya bir gider_pusulası/iade
    # $inc girmiş olabilir. Delta'yı doğru (canlı stokla tutarlı) hesaplamak için stoğu
    # $set'ten HEMEN ÖNCE taze oku. Yazım semantiği değişmiyor (son yazan kazanır) — yalnız
    # ledger doğru: 'en son kayıt = canlı stok' korunur.
    _pre = None
    if ("variants" in product_data) or ("stock" in product_data):
        _pre = await db.products.find_one(
            {"id": product_id}, {"_id": 0, "variants": 1, "stock": 1, "name": 1})

    # ══ VERİ KORUMASI: DOLU alanı BOŞ payload'la ezme ══════════════════════════════════════
    # Panel yüklenmeden/boşken Kaydet edilirse istemci boş liste/dize gönderebilir. Mevcut
    # veri doluyken boş gelirse o alan $set'ten ÇIKARILIR (mevcut korunur). Gerçek temizlik
    # nadir ve ayrı bir işlem olmalı; kaza eseri boş kayıt veri kaybı yapmasın.
    _guard_keys = ("variants", "images", "attributes", "description",
                   "hepsiburada_attributes", "temu_attributes",
                   # Panel listesi bu iki ağır alanı ARTIK GÖNDERMİYOR (liste kırpması).
                   # Düzenleme modalı ürünü tek tek yeniden çekiyor; o çekim başarısız
                   # olup listedeki kırpılmış nesneye düşülürse boş {} ile kaydetmek
                   # mevcut veriyi silerdi — kalkana eklendi.
                   "trendyol_attributes", "ticimax_fields")
    # Panelde kullanıcı galerideki SON görseli de bilerek sildiyse (ör. yalnız ölçü görseli olan ürün)
    # boş galeri kaydedilir; işaret yalnız bu istekte okunur, ürüne yazılmaz.
    _img_clear_ok = bool(product_data.pop("images_clear_confirmed", False)) \
        and isinstance(product_data.get("images"), list) and not product_data.get("images")
    _maybe_blank = [k for k in _guard_keys if k in product_data and not product_data.get(k)
                    and not (k == "images" and _img_clear_ok)]
    if _maybe_blank:
        _ex = await db.products.find_one(
            {"id": product_id}, {"_id": 0, **{k: 1 for k in _maybe_blank}})
        for _k in _maybe_blank:
            if (_ex or {}).get(_k):  # mevcut DOLU + gelen BOŞ → ezme
                product_data.pop(_k, None)
                logger.warning(f"[ürün-koruma] boş '{_k}' ile mevcut dolu veri EZİLMEDİ "
                               f"(product_id={product_id})")

    if isinstance(product_data.get("images"), list):
        product_data["images"] = _size_tables_last(product_data["images"])

    # ══ KAYIP-GÜNCELLEME KORUMASI (stok şaşmalarının KÖK NEDENİ) ═══════════════════════
    # Bu uç varyant dizisinin TAMAMINI $set ile yazıyordu ("son yazan kazanır"). Form açıkken
    # geçen sürede (dakikalar) araya giren HER stok hareketi — sipariş düşümü, iade artışı,
    # pazaryeri siparişi, imalat teslimi — kaydet'e basıldığı anda SESSİZCE siliniyordu:
    # forma yüklenen ESKİ stok geri yazılıyordu. Ürün sayfası açıkken sipariş gelen ürünlerde
    # stok olduğundan fazla görünüyor, sonra oversell/şaşma olarak patlıyordu.
    #
    # Çözüm: yazımdan hemen önce CANLI stok okunur ve
    #   • istemci temel değeri (_stock0) gönderdiyse ve stok DEĞİŞMEMİŞSE → canlı değer korunur,
    #   • _stock0 YOKSA (eski istemci / kısmi payload) → canlı değer korunur (güvenli taraf),
    #   • admin stoğu GERÇEKTEN değiştirdiyse (stock != _stock0) → niyet açıktır, mutlak yazılır.
    # Böylece "ürünün adını düzelttim, stok geri geldi" sınıfı kayıplar biter; kasıtlı stok
    # düzeltmesi ise aynen çalışır.
    if isinstance(product_data.get("variants"), list):
        _live = await db.products.find_one({"id": product_id}, {"_id": 0, "variants": 1})
        _live_map = {}
        for _lv in ((_live or {}).get("variants") or []):
            for _f in ("barcode", "id", "urun_id"):
                _k = str(_lv.get(_f) or "").strip()
                if _k:
                    _live_map.setdefault(f"{_f}:{_k}", _lv)
        _kept = []
        for _v in product_data["variants"]:
            _cur = None
            for _f in ("barcode", "id", "urun_id"):
                _k = str(_v.get(_f) or "").strip()
                if _k and f"{_f}:{_k}" in _live_map:
                    _cur = _live_map[f"{_f}:{_k}"]
                    break
            _base = _v.pop("_stock0", None)          # istemcinin forma yüklediği stok
            if _cur is None:
                continue                              # yeni varyant → gelen stok aynen yazılır
            try:
                _live_stock = int(_cur.get("stock") or 0)
            except Exception:
                _live_stock = 0
            try:
                _sent = int(_v.get("stock") or 0)
            except Exception:
                _sent = 0
            _touched = (_base is not None and int(_base) != _sent)
            if not _touched and _sent != _live_stock:
                _v["stock"] = _live_stock            # araya giren hareketi EZME
                _kept.append({"barcode": _v.get("barcode"), "form": _sent, "canli": _live_stock})
        if _kept:
            logger.warning(f"[stok-koruma] {product_id}: {len(_kept)} varyantta form stoğu ESKİYDİ, "
                           f"canlı stok korundu → {_kept[:8]}")
        # Parent stok her zaman Σvaryant (korunan değerlerle yeniden hesapla).
        product_data["stock"] = sum(int(v.get("stock", 0) or 0) for v in product_data["variants"])

    if "fit_sizes" in product_data:
        product_data["fit_sizes"] = _clean_fit_sizes(product_data.get("fit_sizes"))
    _sync_main_color_into_card(product_data, existing)
    await db.products.update_one({"id": product_id}, {"$set": product_data})

    if _pre is not None:
        await _log_manual_stock_change(
            product_id,
            product_data.get("name") or existing.get("name"),
            _pre,
            product_data.get("variants"),
            product_data.get("stock"),
            (current_user or {}).get("email") or (current_user or {}).get("username") or "",
            current_user=current_user,
            request=request,
        )

    # ⛔→0 PASİFE ALMA KANCASI: yalnız is_active TRUE→FALSE GEÇİŞİNDE (spam yok) pazaryerlerine
    # 0 stok gönder. DB stoğu KORUNUR (helper dokunmaz) → tekrar aktifte cron gerçek stoğu geri
    # yazar. Fire-and-forget (yanıtı bloklamaz); barkodlar için pre-update 'existing' yeterli.
    if existing.get("is_active") and ("is_active" in product_data) and not product_data.get("is_active"):
        _fire_stock_zero_on_passive(dict(existing))

    # İMALAT SENKRONU (kullanıcı isteği): bu üründen oluşturulmuş imalat kaydı varsa,
    # üründeki KİMLİK değişiklikleri (ürün adı, stok kodu, renkler) imalata da yansır.
    # Üretim/maliyet alanları (unit_price, size_distribution, ödeme, tarihler vb.) DEĞİŞMEZ.
    try:
        _mfg_set = {}
        _new_name = product_data.get("name")
        if _new_name and _new_name != existing.get("name"):
            _mfg_set["product_name"] = _new_name
        if "stock_code" in product_data:
            _new_sc = str(product_data.get("stock_code") or "").strip()
            if _new_sc and _new_sc != str(existing.get("stock_code") or "").strip():
                _mfg_set["stock_code"] = _new_sc
        # Renk listesi ürün varyantlarından türetilir (varyant güncellendiyse).
        if variants:
            _colors = []
            for _v in variants:
                _c = (_v.get("color") or _v.get("renk") or "").strip()
                if _c and _c not in _colors:
                    _colors.append(_c)
            if _colors and _colors != (existing.get("colors") if isinstance(existing.get("colors"), list) else None):
                _mfg_set["colors"] = _colors
        if _mfg_set:
            _mfg_set["updated_at"] = datetime.now(timezone.utc).isoformat()
            _mres = await db.manufacturing.update_many(
                {"$or": [{"product_id": product_id}, {"created_product_ids": product_id}]},
                {"$set": _mfg_set},
            )
            if _mres.modified_count:
                logger.info(f"[imalat-senkron] ürün {product_id} değişikliği {_mres.modified_count} imalat kaydına yansıtıldı: {list(_mfg_set)}")
    except Exception as _mfg_e:
        logger.warning(f"[imalat-senkron] ürün→imalat güncelleme başarısız {product_id}: {_mfg_e}")

    # RENK KARDEŞİ OTOMATİK SENKRONU (kullanıcı isteği): stok kodu AYNI olan kartlarda
    # model-düzeyi alanlar bu kayıtla birlikte eşitlenir — Sezon, Beden Önerisi (Kalıp),
    # Uyduğu Bedenler (standart bedenli kardeşlere; seçim değişmediyse yalnız BOŞ olanlara)
    # ve Özellikler (Renk/Web Color HARİÇ; kardeşin kendi rengi korunur). Varyantlar,
    # stok, fiyat, görseller, isim/slug ASLA kopyalanmaz.
    try:
        _scode = str((product_data.get("stock_code") if "stock_code" in product_data
                      else existing.get("stock_code")) or "").strip()
        COLOR_NAMES = {"renk", "web color", "color"}
        def _cn(s): return (s or "").strip().lower()
        # ══ VERİ KORUMASI (ölçü tablosu SİLİNME bugının ikizi) ══════════════════════════════
        # Kardeşe YALNIZ DOLU değer yansıtılır. Boş liste/dict/string yansıtılırsa (panel
        # yüklenmeden/boşken Kaydet) aynı stok kodlu TÜM renk kardeşlerinin Özellikleri/Sezonu
        # tek hamlede silinir. Bu yüzden boş değerler senkron dışı bırakılır (tek üründe temizlik
        # istenirse o üründe yapılır; kardeşlere boşluk YAYILMAZ).
        _sib_base = {}
        if str(product_data.get("season") or "").strip():
            _sib_base["season"] = product_data.get("season")
        if str(product_data.get("size_advice") or "").strip():
            _sib_base["size_advice"] = product_data.get("size_advice")
        _has_attrs = isinstance(product_data.get("attributes"), list) and len(product_data["attributes"]) > 0
        _mp_keys = [k for k in ("hepsiburada_attributes", "temu_attributes")
                    if isinstance(product_data.get(k), dict) and len(product_data.get(k)) > 0]
        _fit_push, _fit_changed = _fit_sizes_to_push(product_data, existing)
        if _scode and (_sib_base or _has_attrs or _mp_keys or _fit_push):
            _src_attrs_nc = ([a for a in product_data["attributes"]
                              if isinstance(a, dict) and _cn(a.get("name") or a.get("type")) not in COLOR_NAMES]
                             if _has_attrs else None)
            async for s in db.products.find(
                    {"stock_code": _scode, "id": {"$ne": product_id}, "is_deleted": {"$ne": True}},
                    {"_id": 0, "id": 1, "attributes": 1, "hepsiburada_attributes": 1, "temu_attributes": 1,
                     "variants.size": 1, "fit_sizes": 1}):
                _set = dict(_sib_base)
                if (_fit_push and _is_standard_sized(s.get("variants"))
                        and (_fit_changed or not _clean_fit_sizes(s.get("fit_sizes")))):
                    _set["fit_sizes"] = list(_fit_push)
                if _src_attrs_nc is not None:
                    _s_color = [a for a in (s.get("attributes") or [])
                                if isinstance(a, dict) and _cn(a.get("name") or a.get("type")) in COLOR_NAMES]
                    _set["attributes"] = _src_attrs_nc + _s_color
                for _k in _mp_keys:
                    _src_map = {k: v for k, v in product_data[_k].items() if _cn(k) not in COLOR_NAMES}
                    _s_map_color = {k: v for k, v in (s.get(_k) or {}).items() if _cn(k) in COLOR_NAMES}
                    _set[_k] = {**_src_map, **_s_map_color}
                if _set:
                    _set["updated_at"] = product_data["updated_at"]
                    await db.products.update_one({"id": s["id"]}, {"$set": _set})
    except Exception as _sib_e:
        logger.error(f"[renk-kardeşi senkron {product_id}] {_sib_e}")

    await record_admin_audit(
        db, action="product.update", entity_type="product", entity_id=product_id,
        before=existing, after={**existing, **product_data}, current_user=current_user,
        request=request, source="products",
    )
    return {"message": "Ürün güncellendi"}

@router.delete("/{product_id}")
async def delete_product(
    product_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("products.delete"))
):
    """Ürünü çöp kutusuna taşır (soft delete). Kalıcı silme için /permanent kullanın."""
    # Full doc: pazaryeri 0-stok kancası barkod/varyant/isim ister.
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    await db.products.update_one(
        {"id": product_id},
        {"$set": {
            "is_deleted": True,
            "is_active": False,
            "prev_active": product.get("is_active", True),
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            # ELLE silme işareti: otomatik telafi (restore) bunu geri getirmez.
            "manual_deleted": True,
        }}
    )
    # Çöpe atma da bir TRUE→FALSE geçişidir → aktifti ise pazaryerlerine 0 stok.
    if product.get("is_active"):
        _fire_stock_zero_on_passive(dict(product))
    await record_admin_audit(
        db, action="product.soft_delete", entity_type="product", entity_id=product_id,
        before=product, after={**product, "is_deleted": True, "is_active": False},
        current_user=current_user, request=request, source="products",
    )
    return {"message": "Ürün çöp kutusuna taşındı"}

@router.post("/{product_id}/restore")
async def restore_product(
    product_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("products.edit"))
):
    """Çöp kutusundaki ürünü geri yükler."""
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    await db.products.update_one(
        {"id": product_id},
        {"$set": {"is_deleted": False, "is_active": product.get("prev_active", True),
                  "updated_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"deleted_at": "", "prev_active": ""}}
    )
    await record_admin_audit(
        db, action="product.restore", entity_type="product", entity_id=product_id,
        before=product, after={**product, "is_deleted": False,
                               "is_active": product.get("prev_active", True)},
        current_user=current_user, request=request, source="products",
    )
    return {"message": "Ürün geri yüklendi"}

@router.delete("/{product_id}/permanent")
async def permanent_delete_product(
    product_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("products.delete"))
):
    """Ürünü veritabanından KALICI olarak siler (geri alınamaz)."""
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    result = await db.products.delete_one({"id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    await record_admin_audit(
        db, action="product.permanent_delete", entity_type="product", entity_id=product_id,
        before=product or {}, after={}, current_user=current_user, request=request,
        source="products",
    )
    return {"message": "Ürün kalıcı olarak silindi"}

@router.post("/{product_id}/toggle-active")
async def toggle_product_active(
    product_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("products.edit"))
):
    """Toggle product active status"""
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    
    new_status = not product.get("is_active", True)
    _now = datetime.now(timezone.utc).isoformat()
    if new_status:
        # ELLE AKTİF: pasif işaretlerini temizle.
        _op = {"$set": {"is_active": True, "updated_at": _now},
               "$unset": {"manual_deactivated": "", "deactivated_reason": ""}}
    else:
        # ELLE PASİF: manual_deactivated işareti bırak → otomatik telafi (restore) bunu GERİ AÇMAZ.
        _op = {"$set": {"is_active": False, "manual_deactivated": True,
                        "manual_deactivated_at": _now, "updated_at": _now}}
    await db.products.update_one({"id": product_id}, _op)

    # TRUE→FALSE geçişi (new_status False = önceki aktifti) → pazaryerlerine 0 stok.
    if not new_status:
        _fire_stock_zero_on_passive(dict(product))

    await record_admin_audit(
        db, action="product.toggle_active", entity_type="product", entity_id=product_id,
        before=product, after={**product, "is_active": new_status},
        current_user=current_user, request=request, source="products",
    )

    return {"is_active": new_status}

@router.post("/{product_id}/block")
async def block_product(product_id: str, current_user: dict = Depends(require_admin)):
    """Ürünü vitrinden kaldırır ve BİR DAHA AKTARILMAMAK ÜZERE kara listeye alır.

    Neden ayrı bir uç: normal silme (soft delete) ürünü çöp kutusuna atar, ama
    tedarikçi beslemesi aynı ürünü `supplier_key` ile bulup yeniden açar —
    silinen ürün ertesi gün vitrine geri gelir. Burada anahtar kalıcı bir kara
    listeye yazılır; aktarım o anahtarı gördüğünde ürünü hiç oluşturmaz.

    Ürün KALICI SİLİNMEZ: is_deleted işaretlenir. Geçmiş siparişler ürüne
    referans veriyor olabilir; kaydı yok etmek sipariş detayını bozar.
    """
    product = await db.products.find_one(
        {"id": product_id},
        {"_id": 0, "id": 1, "name": 1, "supplier_key": 1, "supplier": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    _now = datetime.now(timezone.utc).isoformat()
    await db.products.update_one({"id": product_id}, {"$set": {
        "is_active": False,
        "is_deleted": True,
        "manual_deactivated": True,
        "manual_deactivated_at": _now,
        "blocked_from_feed": True,
        "deactivated_reason": "yönetim tarafından vitrinden silindi",
        "updated_at": _now,
    }})

    key = product.get("supplier_key")
    if key:
        # Aynı ürün iki kez engellenirse tek kayıt kalsın.
        await db.supplier_blocklist.update_one(
            {"supplier_key": key},
            {"$set": {"supplier_key": key, "product_id": product_id,
                      "name": product.get("name") or "",
                      "supplier": product.get("supplier") or "",
                      "blocked_by": (current_user or {}).get("email") or "",
                      "created_at": _now}},
            upsert=True)
    # Vitrinden kalkan ürün pazaryerinde de satılamaz.
    from .trendyol_stock_guard import urunu_pazaryerinde_kapat
    await urunu_pazaryerinde_kapat(product_id)
    return {"success": True, "blocked_from_feed": bool(key),
            "warning": "" if key else
            "Ürünün tedarikçi anahtarı yok; beslemeden gelmiyor, kara liste gerekmedi."}


@router.delete("/{product_id}/block")
async def unblock_product(product_id: str, current_user: dict = Depends(require_admin)):
    """Kara listeden çıkarır ve ürünü çöp kutusundan geri alır (pasif kalır)."""
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "supplier_key": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    if product.get("supplier_key"):
        await db.supplier_blocklist.delete_one({"supplier_key": product["supplier_key"]})
    await db.products.update_one({"id": product_id}, {"$set": {
        "is_deleted": False, "blocked_from_feed": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }})
    return {"success": True}


@router.get("/search/popular")
async def get_popular_searches():
    """Get popular search terms"""
    # In production, track and return actual popular searches
    return [
        {"term": "elbise", "count": 150},
        {"term": "bluz", "count": 120},
        {"term": "pantolon", "count": 100},
        {"term": "jean", "count": 90},
        {"term": "kazak", "count": 80},
    ]


# ==================== PRODUCT ATTRIBUTE IMPORT ====================

@router.post("/attributes/import-xlsx")
async def import_attributes_from_xlsx(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_permission("products.attributes"))
):
    """
    Parse an XLSX file and extract product attributes per stock_code.
    Columns: one column for stock_code, rest are attribute types with values in cells.
    Returns: list of {stock_code, attributes: [{type, value}], matched_product_id}
    """
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl yuklenmemis. pip install openpyxl")

    from security.spreadsheet_guard import read_spreadsheet_upload, check_rows  # denetim #18
    content = await read_spreadsheet_upload(file)
    from security.spreadsheet_guard import parse_offloop as _pol
    wb = await _pol(openpyxl.load_workbook, io.BytesIO(content))  # denetim #18: olay döngüsü dışında
    ws = wb.active
    check_rows(ws.max_row)

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="Bos dosya")

    headers = [str(h).strip() if h else "" for h in rows[0]]

    # Find stock code column - look for "stok kodu", "stock_code", "barkod", "kod" etc.
    stock_col_idx = None
    stock_col_keywords = ["stok kodu", "stock code", "stock_code", "barkod", "barcode", "kod", "urun kodu", "urun_kodu"]
    for i, h in enumerate(headers):
        if any(kw in h.lower().replace(" ", " ") for kw in stock_col_keywords):
            stock_col_idx = i
            break
    if stock_col_idx is None:
        stock_col_idx = 0  # Default to first column

    # Attribute columns = all other columns
    attr_headers = [(i, h) for i, h in enumerate(headers) if i != stock_col_idx and h]

    results = []
    stock_codes = []

    for row in rows[1:]:
        stock_code = str(row[stock_col_idx]).strip() if row[stock_col_idx] else None
        if not stock_code or stock_code.lower() in ("none", "null", ""):
            continue

        attributes = []
        for col_idx, attr_type in attr_headers:
            value = row[col_idx] if col_idx < len(row) else None
            if value is not None and str(value).strip() not in ("", "None", "null"):
                attributes.append({
                    "type": attr_type,
                    "value": str(value).strip()
                })

        if attributes:
            results.append({
                "stock_code": stock_code,
                "attributes": attributes,
                "matched_product_id": None,
                "matched_product_name": None
            })
            stock_codes.append(stock_code)

    # Match with products by stock_code
    if stock_codes:
        products = await db.products.find(
            {"stock_code": {"$in": stock_codes}},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1}
        ).to_list(1000)
        product_map = {p["stock_code"]: p for p in products}

        for r in results:
            p = product_map.get(r["stock_code"])
            if p:
                r["matched_product_id"] = p["id"]
                r["matched_product_name"] = p["name"]

    return {
        "total_rows": len(results),
        "matched": sum(1 for r in results if r["matched_product_id"]),
        "unmatched": sum(1 for r in results if not r["matched_product_id"]),
        "attribute_types": [h for _, h in attr_headers],
        "results": results
    }


@router.post("/attributes/save-bulk")
async def save_attributes_bulk(payload: dict, current_user: dict = Depends(require_permission("products.attributes"))):
    """
    Save attributes to multiple products.
    Payload: { updates: [{product_id, attributes: [{type, value, trendyol_attr_id, trendyol_attr_value_id}]}] }
    """
    updates = payload.get("updates", [])
    if not updates:
        raise HTTPException(status_code=400, detail="Guncellenecek urun yok")

    updated = 0
    for update in updates:
        product_id = update.get("product_id")
        attributes = update.get("attributes", [])
        if not product_id or not attributes:
            continue

        await db.products.update_one(
            {"id": product_id},
            {"$set": {
                "attributes": attributes,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        updated += 1

    return {"success": True, "updated": updated}


@router.get("/{product_id}/combine-products")
async def get_combine_products(product_id: str, request: Request):
    """Bu ürünle birlikte gösterilecek kombin ürünlerin LİSTESİNİ döner.
    Public endpoint — sepet/ürün detay sayfası kullanır."""
    product = await db.products.find_one(
        {"id": product_id},
        {"_id": 0, "combine_products": 1, "category_id": 1, "categories": 1}
    )
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    combine_ids = product.get("combine_products") or []
    items = []
    if combine_ids:
        async for p in db.products.find(
            {"id": {"$in": combine_ids}, "is_active": {"$ne": False}},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
             "images": 1, "image": 1, "stock": 1, "category_id": 1, "category_ids": 1}
        ):
            items.append(p)
        await _attach_campaign_badges(items)
    # ÜYELERE ÖZEL: misafir, üyelere özel ürünün kombinini ve üyelere özel kombin ürününü göremez.
    if not request_is_member(request):
        if await product_id_is_members_only(product_id):
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        items = await strip_members_only(request, items)
    return {"items": items, "source": "combine"}


@router.put("/{product_id}/combine-products")
async def update_combine_products(
    product_id: str,
    payload: dict,
    current_user: dict = Depends(require_permission("products.edit"))
):
    """Bu ürün için kombin ürün ID listesini günceller (admin)."""
    combine_ids = payload.get("combine_products") or []
    if not isinstance(combine_ids, list):
        raise HTTPException(status_code=400, detail="combine_products bir liste olmalıdır")
    # Self-reference temizliği
    combine_ids = [str(cid) for cid in combine_ids if str(cid) != product_id]
    # Azami kombin ürün sayısı — admin panelinden (İşletme Kuralları) yönetilir; varsayılan 12.
    try:
        import business_rules as _BR
        _maxc = int(await _BR.get_rule(db, "product.max_combine", 12) or 12)
    except Exception:
        _maxc = 12
    combine_ids = combine_ids[:_maxc]

    # Var olan ürün ID'lerini doğrula — fake/stale id'leri filtrele
    if combine_ids:
        existing = await db.products.distinct("id", {"id": {"$in": combine_ids}})
        existing_set = set(existing)
        combine_ids = [cid for cid in combine_ids if cid in existing_set]

    result = await db.products.update_one(
        {"id": product_id},
        {"$set": {
            "combine_products": combine_ids,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    return {"success": True, "count": len(combine_ids)}


@router.post("/{product_id}/auto-combine")
async def auto_assign_combine_products(
    product_id: str,
    payload: dict = None,
    current_user: dict = Depends(require_permission("products.edit")),
):
    """Geçmiş siparişlerdeki co-occurrence verisinden bu ürünle en sık birlikte
    satılan top-N ürünü otomatik kombin olarak atar."""
    payload = payload or {}
    max_n = min(int(payload.get("max", 8)), 12)
    dry_run = bool(payload.get("dry_run", False))
    replace = bool(payload.get("replace", True))

    base = await db.products.find_one({"id": product_id}, {"_id": 0, "combine_products": 1})
    if not base:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    co_count = {}
    async for order in db.orders.find(
        {"items.product_id": product_id}, {"_id": 0, "items.product_id": 1}
    ).limit(2000):
        ids_in_order = {it.get("product_id") for it in (order.get("items") or []) if it.get("product_id")}
        if product_id not in ids_in_order:
            continue
        for pid in ids_in_order:
            if pid and pid != product_id:
                co_count[pid] = co_count.get(pid, 0) + 1

    sorted_ids = sorted(co_count.items(), key=lambda kv: kv[1], reverse=True)
    if not sorted_ids:
        return {"success": False, "message": "Bu ürün için yeterli sipariş geçmişi yok", "candidates": []}

    candidate_ids = [pid for pid, _ in sorted_ids[:max_n * 3]]
    existing_ids = set(await db.products.distinct(
        "id", {"id": {"$in": candidate_ids}, "is_active": {"$ne": False}}
    ))

    candidates = []
    for pid, cnt in sorted_ids:
        if len(candidates) >= max_n:
            break
        if pid not in existing_ids:
            continue
        prod = await db.products.find_one(
            {"id": pid}, {"_id": 0, "id": 1, "name": 1, "price": 1, "images": 1, "image": 1}
        )
        if prod:
            candidates.append({**prod, "_co_count": cnt})

    selected_ids = [c["id"] for c in candidates]
    if dry_run:
        return {"success": True, "candidates": candidates, "would_assign": selected_ids, "dry_run": True}

    new_ids = selected_ids if replace else list(dict.fromkeys((base.get("combine_products") or []) + selected_ids))[:12]
    await db.products.update_one(
        {"id": product_id},
        {"$set": {
            "combine_products": new_ids,
            "combine_auto_generated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return {
        "success": True, "assigned": new_ids, "candidates": candidates,
        "count": len(new_ids),
        "message": f"{len(new_ids)} kombin ürün atandı (geçmiş siparişlerden)",
    }


@router.post("/auto-combine-all")
async def auto_assign_combine_all(
    payload: dict = None,
    current_user: dict = Depends(require_permission("products.edit")),
):
    """Tüm aktif ürünler için tek tıkla otomatik kombin atama (admin)."""
    payload = payload or {}
    max_n = min(int(payload.get("max", 8)), 12)
    only_empty = bool(payload.get("only_empty", True))

    query = {"is_active": {"$ne": False}}
    if only_empty:
        query["$or"] = [
            {"combine_products": {"$exists": False}},
            {"combine_products": {"$size": 0}},
        ]

    processed = 0
    assigned_total = 0
    skipped_no_data = 0

    cursor = db.products.find(query, {"_id": 0, "id": 1})
    async for p in cursor:
        pid = p["id"]
        co_count = {}
        async for order in db.orders.find(
            {"items.product_id": pid}, {"_id": 0, "items.product_id": 1}
        ).limit(500):
            ids_in_order = {it.get("product_id") for it in (order.get("items") or []) if it.get("product_id")}
            for cid in ids_in_order:
                if cid and cid != pid:
                    co_count[cid] = co_count.get(cid, 0) + 1
        sorted_ids = sorted(co_count.items(), key=lambda kv: kv[1], reverse=True)
        if not sorted_ids:
            skipped_no_data += 1
            processed += 1
            continue
        candidate_ids = [cid for cid, _ in sorted_ids[:max_n * 2]]
        existing_ids = set(await db.products.distinct(
            "id", {"id": {"$in": candidate_ids}, "is_active": {"$ne": False}}
        ))
        selected = [cid for cid, _ in sorted_ids if cid in existing_ids][:max_n]
        if selected:
            await db.products.update_one(
                {"id": pid},
                {"$set": {
                    "combine_products": selected,
                    "combine_auto_generated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            assigned_total += 1
        processed += 1

    return {
        "success": True, "processed": processed,
        "products_with_combine_assigned": assigned_total,
        "skipped_no_order_history": skipped_no_data,
        "message": f"{assigned_total}/{processed} ürüne kombin atandı",
    }


@router.post("/combine/clear-auto")
async def clear_auto_combine_products(
    payload: dict = None,
    current_user: dict = Depends(require_permission("products.edit")),
):
    """TEK SEFERLİK: Sistemin OTOMATİK atadığı kombin ('Görünümü Tamamla') ürünlerini temizler.
    Otomatik atamalar `combine_auto_generated_at` damgası taşır; MANUEL atamalar taşımaz →
    yalnız otomatik olanlar silinir, elle eklenenler KORUNUR.

    payload: {dry_run: bool (varsayılan True), mode: 'auto'|'all'}
      - dry_run=True  → hiçbir şey silmez, sadece kaç ürünün etkileneceğini sayar
      - mode='auto'   → yalnız otomatik damgalı ürünler (varsayılan, güvenli)
      - mode='all'    → combine_products dolu TÜM ürünler (damga aranmaz)
    """
    payload = payload or {}
    dry_run = payload.get("dry_run", True)
    mode = (payload.get("mode") or "auto").lower()

    auto_q = {"combine_auto_generated_at": {"$exists": True}}
    all_q = {"combine_products": {"$exists": True, "$ne": []}}
    manual_q = {"combine_products": {"$exists": True, "$ne": []},
                "combine_auto_generated_at": {"$exists": False}}

    auto_cnt = await db.products.count_documents(auto_q)
    all_cnt = await db.products.count_documents(all_q)
    manual_cnt = await db.products.count_documents(manual_q)

    target_q = all_q if mode == "all" else auto_q

    if dry_run:
        return {
            "dry_run": True, "mode": mode,
            "auto_assigned_count": auto_cnt,
            "with_any_combine_count": all_cnt,
            "manual_only_count": manual_cnt,
            "would_clear": (all_cnt if mode == "all" else auto_cnt),
            "message": f"KURU ÇALIŞMA — mode={mode}: {(all_cnt if mode=='all' else auto_cnt)} ürün "
                       f"temizlenecek (otomatik={auto_cnt}, elle={manual_cnt}). "
                       f"Silmek için dry_run:false gönderin.",
        }

    res = await db.products.update_many(
        target_q,
        {"$set": {"combine_products": [],
                  "updated_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"combine_auto_generated_at": ""}},
    )
    return {
        "success": True, "dry_run": False, "mode": mode,
        "cleared": res.modified_count,
        "message": f"{res.modified_count} üründe otomatik kombin ('Görünümü Tamamla') temizlendi. "
                   f"Artık manuel ekleyebilirsiniz.",
    }


@router.post("/cart-suggestions")
async def get_cart_suggestions(payload: dict, request: Request):
    """Sepetteki ürünlere göre öneriler döner (public).
    
    Öncelik:
      1) Sepetteki ürünlerin combine_products listesi (cross-sell, manuel atama)
      2) Sale/indirim kategorisindeki aktif ürünler (fallback)
    """
    cart_product_ids = payload.get("product_ids") or []
    limit = int(payload.get("limit", 8))

    suggestions = []
    seen = set(cart_product_ids)

    # 1) Sepetteki her ürünün combine_products'ını topla
    if cart_product_ids:
        cart_products = []
        async for p in db.products.find(
            {"id": {"$in": cart_product_ids}},
            {"_id": 0, "combine_products": 1}
        ):
            cart_products.append(p)
        combine_ids = []
        for cp in cart_products:
            for cid in (cp.get("combine_products") or []):
                if cid not in seen:
                    combine_ids.append(cid)
                    seen.add(cid)
        if combine_ids:
            async for p in db.products.find(
                {"id": {"$in": combine_ids[:limit]}, "is_active": {"$ne": False}},
                {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
                 "images": 1, "image": 1, "stock": 1, "category_id": 1, "category_ids": 1}
            ):
                suggestions.append({**p, "_source": "combine"})

    # 2) Yetersizse → indirimli aktif ürünlerle doldur
    # Y15: discount_price/is_on_sale/sale_active alanları HİÇBİR YERE yazılmıyor. Gerçek indirim
    # alanı sale_price'tir (0 < sale_price < price). Önceki sorgu her zaman boş dönüyordu.
    needed = max(0, limit - len(suggestions))
    if needed > 0:
        sale_query = {
            "is_active": {"$ne": False},
            "id": {"$nin": list(seen)},
            "$expr": {"$and": [
                {"$gt": [{"$ifNull": ["$sale_price", 0]}, 0]},
                {"$lt": [{"$ifNull": ["$sale_price", 0]}, {"$ifNull": ["$price", 0]}]},
            ]},
        }
        async for p in db.products.find(
            sale_query,
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
             "images": 1, "image": 1, "stock": 1, "category_id": 1, "category_ids": 1}
        ).limit(needed):
            suggestions.append({**p, "_source": "sale"})
            seen.add(p["id"])

    # 3) Hala yetersizse → en son eklenmiş aktif ürünler
    needed = max(0, limit - len(suggestions))
    if needed > 0:
        async for p in db.products.find(
            {"is_active": {"$ne": False}, "id": {"$nin": list(seen)}},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
             "images": 1, "image": 1, "stock": 1, "category_id": 1, "category_ids": 1}
        ).sort("created_at", -1).limit(needed):
            suggestions.append({**p, "_source": "new"})

    await _attach_campaign_badges(suggestions)
    suggestions = await strip_members_only(request, suggestions)   # misafire üyelere özel yok
    return {"items": suggestions[:limit], "total": len(suggestions[:limit])}


@router.post("/checkout-deals")
async def get_checkout_deals(payload: dict, request: Request):
    """Sepet sayfasındaki "Kasa Önü Fırsatları" — yalnızca indirimdeki aktif ürünler.

    Sepetteki ürünleri hariç tutar, indirimli olanları rastgele döner.
    """
    cart_product_ids = payload.get("product_ids") or []
    limit = int(payload.get("limit", 8))

    # Y15: Gerçek indirim alanı sale_price'tir (0 < sale_price < price). Önceki sorgu
    # hiç yazılmayan discount_price/is_on_sale/sale_active alanlarına baktığı için "Kasa Önü
    # Fırsatları" HER ZAMAN boştu.
    sale_query = {
        "is_active": {"$ne": False},
        "id": {"$nin": cart_product_ids},
        "$expr": {"$and": [
            {"$gt": [{"$ifNull": ["$sale_price", 0]}, 0]},
            {"$lt": [{"$ifNull": ["$sale_price", 0]}, {"$ifNull": ["$price", 0]}]},
        ]},
    }

    deals = []
    async for p in db.products.find(
        sale_query,
        {"_id": 0, "id": 1, "name": 1, "slug": 1, "price": 1, "sale_price": 1,
         "images": 1, "image": 1, "stock": 1, "category_id": 1, "category_ids": 1}
    ).limit(limit * 2):
        sp = p.get("sale_price") or 0
        if sp > 0 and sp < (p.get("price") or 0):
            deals.append(p)
        if len(deals) >= limit:
            break

    await _attach_campaign_badges(deals)
    deals = await strip_members_only(request, deals)   # misafire üyelere özel yok
    return {"items": deals[:limit], "total": len(deals[:limit])}


@router.get("/{product_id}/attributes")
async def get_product_attributes(product_id: str, current_user: dict = Depends(require_permission("products.view", any_in_area=True))):
    """Get attributes for a single product"""
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "attributes": 1, "name": 1, "stock_code": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Urun bulunamadi")
    return {"attributes": product.get("attributes", []), "name": product.get("name"), "stock_code": product.get("stock_code")}


@router.post("/backfill-season")
async def backfill_product_seasons_endpoint(current_user: dict = Depends(require_permission("products.edit"))):
    """TEK SEFERLİK/idempotent: 'season' alanı boş ürünlere, öznitelikteki 'Sezon'
    değerinden normalize edilmiş değer yazar (İlkbahar/Sonbahar · Tüm Sezonlar · Yaz · Kış).
    Ayrıca eski taksonomideki kayıtları yeni değerlere taşır (İlkbahar→İlkbahar/Sonbahar vb.)."""
    from .reports import _season_from_attrs
    # Eski değerleri yeni taksonomiye taşı (idempotent)
    _mig = await db.products.update_many(
        {"season": {"$in": ["İlkbahar", "Sonbahar"]}},
        {"$set": {"season": "İlkbahar/Sonbahar"}})
    migrated = _mig.modified_count
    updated = 0
    scanned = 0
    async for p in db.products.find(
            {"$or": [{"season": {"$exists": False}}, {"season": ""}, {"season": None}]},
            {"_id": 0, "id": 1, "attributes": 1}):
        scanned += 1
        s = _season_from_attrs(p.get("attributes"))
        if s:
            await db.products.update_one({"id": p["id"]}, {"$set": {"season": s}})
            updated += 1
    return {"scanned": scanned, "updated": updated, "migrated": migrated}


@router.put("/{product_id}/attributes")
async def update_product_attributes(product_id: str, payload: dict, current_user: dict = Depends(require_permission("products.edit"))):
    """Update attributes for a single product"""
    attributes = payload.get("attributes", [])
    await db.products.update_one(
        {"id": product_id},
        {"$set": {"attributes": attributes, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": True, "message": "Ozellikler guncellendi"}

@router.post("/fix-negative-stock")
async def fix_negative_stock(request: Request, current_user: dict = Depends(require_permission("products.edit"))):
    """İDEMPOTENT: eksiye düşmüş (stok < 0) varyant/ürün stoklarını 0'a sabitler ve
    parent stoğu varyant toplamından yeniden hesaplar. Negatif stok artık oluşamaz
    (sipariş düşümleri 0'da kelepçeli) — bu uç geçmişten kalanları temizler."""
    fixed = []
    async for p in db.products.find(
            {"$or": [{"variants.stock": {"$lt": 0}}, {"stock": {"$lt": 0}}]},
            {"_id": 0, "id": 1, "name": 1, "stock": 1, "variants": 1}):
        _bad = [{"barcode": v.get("barcode"), "size": v.get("size"), "stock": v.get("stock")}
                for v in (p.get("variants") or []) if int(v.get("stock") or 0) < 0]
        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {"variants.$[v].stock": 0,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
            array_filters=[{"v.stock": {"$lt": 0}}],
        ) if _bad else None
        # parent stok = varyant toplamı (varyantsızsa negatif parent 0'a çekilir)
        if p.get("variants"):
            await db.products.update_one(
                {"id": p["id"]},
                [{"$set": {"stock": {"$sum": {"$map": {
                    "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                    "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}]},
                }}}}}],
            )
        elif int(p.get("stock") or 0) < 0:
            await db.products.update_one({"id": p["id"]}, {"$set": {"stock": 0}})
        _after = dict(p)
        if p.get("variants"):
            _after["variants"] = [
                {**v, "stock": max(0, int(v.get("stock") or 0))}
                for v in (p.get("variants") or [])
            ]
            _after["stock"] = sum(int(v.get("stock") or 0) for v in _after["variants"])
        else:
            _after["stock"] = max(0, int(p.get("stock") or 0))
        await record_stock_audit(
            db, product_id=p["id"], product_name=p.get("name", ""), before=p, after=_after,
            source="admin_maintenance", current_user=current_user, request=request,
            action="negative_stock_fixed",
        )
        fixed.append({"id": p["id"], "name": p.get("name"), "negative_variants": _bad,
                      "old_parent_stock": p.get("stock")})
    return {"fixed_count": len(fixed), "fixed": fixed[:50]}


@router.post("/reconcile-stock-desync")
async def reconcile_stock_desync(
    dry_run: bool = Query(True),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """TEŞHİS + İDEMPOTENT ONARIM — parent `stock` ile Σvaryant.stock ayrışması (desync).

    Varyantlı üründe parent `stock` HER ZAMAN Σvaryant olmalıdır (kaynak-doğru = varyant;
    bkz. create/update save-fix ~L2155 + sipariş düşüm/iade yolları orders.py). Bu ayrışma iki
    yönde müşteriye yansır:
      • phantom_sold_out (parent>0 ama Σvaryant=0): admin'de "stok var" görünür, STOREFRONT
        efektif stoğu (=Σvaryant) 0 hesaplayıp **TÜKENDİ** gösterir. (WhatsApp'ta bildirilen durum.)
      • phantom_stock (parent=0 ama Σvaryant>0): tersi.
    Save/sipariş DIŞI yollardan (ör. pazaryeri/Ticimax stok senkronu) gelen drift burada yakalanır.

    dry_run=True (VARSAYILAN): yalnız RAPOR — hiçbir şey YAZILMAZ.
    dry_run=False: parent := Σvaryant olarak sabitler. OVERSELL RİSKİ YOK (parent türetilmiş alandır;
    satılabilirlik varyanttan hesaplanır) ve tekrar çalıştırılabilir (idempotent). Otomatik değildir —
    rapor gözden geçirildikten sonra admin bilinçli tetikler.
    """
    drifted = []
    pipeline = [
        {"$match": {"variants.0": {"$exists": True}}},  # en az 1 varyant
        {"$addFields": {"_vsum": {"$sum": {"$map": {
            "input": {"$ifNull": ["$variants", []]}, "as": "v",
            "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$v.stock", 0]}}]},
        }}}}},
        {"$match": {"$expr": {"$ne": [{"$toInt": {"$ifNull": ["$stock", 0]}}, "$_vsum"]}}},
        {"$project": {"_id": 0, "id": 1, "name": 1, "stock_code": 1,
                      "parent_stock": {"$toInt": {"$ifNull": ["$stock", 0]}},
                      "variant_sum": "$_vsum"}},
    ]
    async for p in db.products.aggregate(pipeline, allowDiskUse=True):
        _ps = int(p.get("parent_stock") or 0)
        _vs = int(p.get("variant_sum") or 0)
        drifted.append({
            "id": p.get("id"), "name": p.get("name"), "stock_code": p.get("stock_code"),
            "parent_stock": _ps, "variant_sum": _vs,
            "phantom_sold_out": bool(_ps > 0 and _vs == 0),
            "phantom_stock": bool(_ps == 0 and _vs > 0),
        })
    applied = 0
    if not dry_run:
        for d in drifted:
            await db.products.update_one(
                {"id": d["id"]},
                [{"$set": {"stock": {"$sum": {"$map": {
                    "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                    "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}]},
                }}}, "updated_at": datetime.now(timezone.utc).isoformat()}}],
            )
            applied += 1
    # En kritikler (phantom_sold_out = müşteriye TÜKENDİ) en üstte
    drifted.sort(key=lambda x: (not x["phantom_sold_out"], not x["phantom_stock"], x.get("name") or ""))
    return {
        "dry_run": dry_run,
        "desync_count": len(drifted),
        "phantom_sold_out_count": sum(1 for d in drifted if d["phantom_sold_out"]),
        "phantom_stock_count": sum(1 for d in drifted if d["phantom_stock"]),
        "applied": applied,
        "items": drifted[:200],
    }


@router.post("/bulk-update-vat")
async def bulk_update_vat(
    payload: dict,
    current_user: dict = Depends(require_permission("settings.edit"))
):
    """Bulk update VAT for all products"""
    vat_rate = payload.get("vat_rate")
    if vat_rate is None:
        raise HTTPException(status_code=400, detail="VAT rate is required")
    
    result = await db.products.update_many({}, {"$set": {"vat_rate": vat_rate}})
    return {"message": f"{result.modified_count} ürünün KDV oranı %{vat_rate} olarak güncellendi."}


@router.post("/bulk/add-to-category-after")
async def bulk_add_to_category_after(
    after_card_id: int = Query(..., description="Bu Ürün Kart ID'den BÜYÜK sayısal kartlı ürünler kategoriye eklenir"),
    category_slug: str = Query("en-yeniler", description="Hedef kategori slug'ı (varsayılan: en-yeniler)"),
    set_is_new: bool = Query(True, description="Eşleşen ürünlerde is_new=True yapılsın mı"),
    dry_run: bool = Query(True, description="True → sadece önizleme (YAZMAZ). False → uygular."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """urun_karti_id (sayısal) > after_card_id olan TÜM ürünleri verilen kategoriye ekler.

    - Üyelik çoklu kategori dizilerine (category_ids + categories) addToSet ile yazılır;
      ürünün mevcut/ana kategorisini BOZMAZ (category_id/category_name'e dokunulmaz).
    - İdempotent: tekrar çalıştırmak güvenli (zaten ekliyse değişmez).
    - dry_run=True (VARSAYILAN) → yalnız eşleşen sayıyı + örnek döner, hiçbir şey yazmaz.
      Önce dry-run ile sayıyı gör, doğruysa dry_run=false ile uygula.
    """
    target_slug = (category_slug or "").strip().lower()
    cat_id = None
    cat_name = None
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1}):
        sl = (c.get("slug") or "").strip().lower()
        nm = (c.get("name") or "").strip()
        if sl == target_slug or generate_slug(nm) == target_slug:
            cat_id = c.get("id")
            cat_name = nm
            break
    if not cat_id:
        raise HTTPException(status_code=404, detail=f"'{category_slug}' kategorisi bulunamadı.")

    cat_ids_all = await _expand_category_ids([cat_id])  # ataları dahil (En Yeniler kökse sadece kendisi)

    matched = 0
    updated = 0
    sample = []
    cursor = db.products.find(
        {"urun_karti_id": {"$nin": [None, ""]}},
        {"_id": 0, "id": 1, "urun_karti_id": 1, "name": 1},
    )
    async for p in cursor:
        v = str(p.get("urun_karti_id") or "").strip()
        if not v.isdigit() or int(v) <= after_card_id:
            continue
        matched += 1
        if len(sample) < 15:
            sample.append({"urun_karti_id": v, "name": (p.get("name") or "")[:40]})
        if not dry_run:
            ops = {
                "$addToSet": {"category_ids": {"$each": cat_ids_all}, "categories": cat_id},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
            }
            if set_is_new:
                ops["$set"]["is_new"] = True
            await db.products.update_one({"id": p["id"]}, ops)
            updated += 1

    return {
        "category": {"id": cat_id, "name": cat_name, "expanded_ids": cat_ids_all},
        "after_card_id": after_card_id,
        "matched": matched,
        "updated": (0 if dry_run else updated),
        "dry_run": dry_run,
        "sample": sample,
        "message": (
            f"DRY-RUN: Kart ID > {after_card_id} olan {matched} ürün '{cat_name}' kategorisine eklenecek (henüz YAZILMADI). "
            f"Uygulamak için aynı isteği dry_run=false ile çağırın."
            if dry_run else
            f"{updated} ürün '{cat_name}' kategorisine eklendi (Kart ID > {after_card_id})."
        ),
    }


# product.id referansı tutan koleksiyon/alanlar (ajan haritası). kind: scalar | arr_scalar | arr_obj
_PID_REFS = [
    ("orders", "arr_obj", "items.product_id"),
    ("orders", "arr_obj", "items.meluni_product_id"),
    ("orders", "arr_obj", "products.product_id"),
    ("orders_deleted", "arr_obj", "items.product_id"),
    ("cart_sessions", "arr_obj", "items.product_id"),
    ("shared_carts", "arr_obj", "items.product_id"),
    ("stock_movements", "scalar", "product_id"),
    ("stock_movements", "arr_obj", "items.product_id"),
    ("stock_movements", "arr_obj", "moves.product_id"),
    ("favorites", "scalar", "product_id"),
    ("reviews", "scalar", "product_id"),
    ("product_reviews", "scalar", "product_id"),
    ("trendyol_review_sync_products", "scalar", "product_id"),
    ("stock_notifications", "scalar", "product_id"),
    ("stock_alerts", "scalar", "product_id"),
    ("product_costs", "scalar", "product_id"),
    ("product_stock_flags", "scalar", "product_id"),
    ("product_image_backups", "scalar", "product_id"),
    ("size_tables", "scalar", "product_id"),
    ("whatsapp_active_product", "scalar", "product_id"),
    ("bin_stock", "scalar", "product_id"),
    ("manufacturing", "scalar", "product_id"),
    ("manufacturing", "arr_scalar", "created_product_ids"),
    ("coupons", "arr_scalar", "products"),
    ("coupons", "arr_scalar", "excluded_products"),
    ("referrals", "arr_scalar", "products"),
    ("instagram_posts", "arr_obj", "products.id"),
    ("products", "arr_scalar", "combo_product_ids"),
    ("products", "arr_scalar", "similar_product_ids"),
]


def _is_short_numeric_id(v) -> bool:
    s = str(v or "").strip()
    return len(s) == 4 and s.isdigit()


@router.post("/migrate-uuid-ids")
async def migrate_uuid_product_ids(
    dry_run: bool = Query(True, description="True → önizleme (YAZMAZ). False → uygular."),
    limit: int = Query(0, description="0=tümü; test için küçük bir sayı verilebilir."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """UUID/uzun product.id'leri BENZERSİZ 4-haneliye çevirir + TÜM referansları günceller
    (_PID_REFS). Eski→yeni map product_id_migration_map'e kalıcı yazılır (geri-alınabilir).
    product_visual_index (_id=product.id, immutable) delete+reinsert ile taşınır. dry_run'da
    yalnız etkilenecek doküman sayıları raporlanır (YAZMAZ). Idempotent."""
    # 1) Aday ürünler: id'si 4-haneli-sayısal OLMAYANLAR
    cands = []
    async for p in db.products.find({}, {"_id": 0, "id": 1, "name": 1}):
        if not _is_short_numeric_id(p.get("id")):
            cands.append({"id": p.get("id"), "name": (p.get("name") or "")[:40]})
    if limit and limit > 0:
        cands = cands[:limit]

    # 2) Yeni id'ler (benzersiz)
    id_map = {}
    for c in cands:
        old = c["id"]
        prev = await db.product_id_migration_map.find_one({"old": old}, {"_id": 0, "new": 1})
        if prev and prev.get("new"):
            id_map[old] = prev["new"]
            continue
        nid = await generate_short_id("products")
        # çakışma güvenliği: bu batch'te veya map'te kullanılmışsa yeniden üret
        while nid in id_map.values() or await db.product_id_migration_map.find_one({"new": nid}, {"_id": 1}):
            nid = await generate_short_id("products")
        id_map[old] = nid

    olds = list(id_map.keys())

    # 3) DRY-RUN: etkilenecek doküman sayıları
    if dry_run:
        impact = {}
        for coll, kind, path in _PID_REFS:
            try:
                n = await db[coll].count_documents({path: {"$in": olds}})
            except Exception:
                n = -1
            if n:
                impact[f"{coll}.{path}"] = n
        try:
            pvi = await db.product_visual_index.count_documents({"_id": {"$in": olds}})
        except Exception:
            pvi = 0
        return {
            "dry_run": True, "uuid_products": len(cands), "sample_map": dict(list(id_map.items())[:8]),
            "product_visual_index_move": pvi, "impact_by_field": impact,
            "message": (f"{len(cands)} UUID ürün 4-haneliye çevrilecek; yukarıdaki alanlardaki "
                        f"referanslar güncellenecek (YAZILMADI). Uygulamak için dry_run=false."),
        }

    # 4) UYGULA
    _now = datetime.now(timezone.utc).isoformat()
    ref_updates = 0
    for old, new in id_map.items():
        # 4a) products.id
        await db.products.update_one({"id": old}, {"$set": {"id": new, "updated_at": _now}})
        # 4b) referanslar
        for coll, kind, path in _PID_REFS:
            try:
                if kind == "scalar":
                    r = await db[coll].update_many({path: old}, {"$set": {path: new}})
                elif kind == "arr_scalar":
                    r = await db[coll].update_many(
                        {path: old}, {"$set": {f"{path}.$[e]": new}}, array_filters=[{"e": old}])
                else:  # arr_obj: "arr.sub"
                    arr, sub = path.split(".", 1)
                    r = await db[coll].update_many(
                        {path: old}, {"$set": {f"{arr}.$[e].{sub}": new}},
                        array_filters=[{f"e.{sub}": old}])
                ref_updates += r.modified_count
            except Exception as _e:
                logger.error(f"[id-migrate] {coll}.{path} {old}->{new}: {_e}")
        # 4c) product_visual_index (_id immutable → delete+reinsert)
        try:
            vi = await db.product_visual_index.find_one({"_id": old})
            if vi and not await db.product_visual_index.find_one({"_id": new}, {"_id": 1}):
                vi["_id"] = new
                vi["product_id"] = new
                await db.product_visual_index.insert_one(vi)
                await db.product_visual_index.delete_one({"_id": old})
        except Exception as _e:
            logger.error(f"[id-migrate] visual_index {old}: {_e}")
        # 4d) map kaydı (geri-alma)
        await db.product_id_migration_map.update_one(
            {"old": old}, {"$set": {"old": old, "new": new, "migrated_at": _now}}, upsert=True)

    return {"dry_run": False, "migrated": len(id_map), "ref_updates": ref_updates,
            "message": f"{len(id_map)} ürün 4-haneli id'ye taşındı; {ref_updates} referans güncellendi. "
                       f"Eski→yeni map: product_id_migration_map (geri-alınabilir)."}


@router.post("/bulk/set-variant-stock")
async def bulk_set_variant_stock(
    payload: dict,
    request: Request,
    dry_run: bool = Query(True, description="True → önizleme (YAZMAZ). False → uygular."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """BEDEN BAZINDA stok güncelle. payload: { items: [{ key, sizes: {BEDEN: adet} }] }.
    key = id / urun_karti_id / urun_id. Her ürünün variants[].size'ı (normalize: boşluksuz+büyük)
    BEDEN ile eşleşen varyantına stok YAZAR (0 dahil). Excel'de olmayan (gönderilmeyen) beden
    DOKUNULMAZ. Parent stock = varyant stok toplamı olarak yeniden hesaplanır. Idempotent, dry_run."""
    items = payload.get("items") or []

    def _norm(s):
        return str(s or "").strip().upper().replace(" ", "")

    results, total_updated, not_found = [], 0, []
    _now = datetime.now(timezone.utc).isoformat()
    for it in items:
        key = str(it.get("key") or "").strip()
        sizes = {_norm(k): v for k, v in (it.get("sizes") or {}).items() if v is not None}
        cands = [key]
        if key.isdigit():
            try:
                cands.append(int(key))
            except Exception:
                pass
        p = await db.products.find_one(
            {"$or": [{"id": {"$in": cands}}, {"urun_karti_id": {"$in": cands}}, {"urun_id": {"$in": cands}}]},
            {"_id": 0, "id": 1, "name": 1, "variants": 1})
        if not p:
            not_found.append(key)
            continue
        variants = p.get("variants") or []
        applied, unmatched = {}, []
        new_vs = []
        for v in variants:
            vsz = _norm(v.get("size"))
            if vsz in sizes:
                qty = int(sizes[vsz] or 0)
                applied[v.get("size")] = qty
                v2 = dict(v); v2["stock"] = qty
                new_vs.append(v2)
            else:
                new_vs.append(v)
        for sz in sizes:
            if not any(_norm(v.get("size")) == sz for v in variants):
                unmatched.append(sz)
        new_total = sum(int(x.get("stock") or 0) for x in new_vs)
        results.append({"key": key, "id": p.get("id"), "name": (p.get("name") or "")[:45],
                        "applied": applied, "unmatched_sizes": unmatched, "new_total": new_total})
        if not dry_run and applied:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"variants": new_vs, "stock": new_total, "updated_at": _now}})
            await record_stock_audit(
                db, product_id=p["id"], product_name=p.get("name", ""), before=p,
                after={**p, "variants": new_vs, "stock": new_total},
                source="bulk_variant_stock", current_user=current_user, request=request,
                action="bulk_stock_adjusted",
            )
            total_updated += 1
    return {
        "requested": len(items), "matched": len(results), "not_found": not_found,
        "updated": (0 if dry_run else total_updated), "dry_run": dry_run, "results": results[:60],
        "message": (f"DRY-RUN: {len(results)} ürün bulundu; beden stokları yazılacak (YAZILMADI). "
                    f"dry_run=false ile uygulayın." if dry_run
                    else f"{total_updated} ürünün beden stokları güncellendi.")
        + (f" {len(not_found)} ID bulunamadı." if not_found else ""),
    }


async def _find_category_by_slug(slug: str):
    """slug (veya isimden türetilmiş slug) ile kategoriyi bul → (id, name) | (None, None)."""
    s = (slug or "").strip().lower()
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1}):
        if (c.get("slug") or "").strip().lower() == s or generate_slug(c.get("name") or "") == s:
            return c.get("id"), c.get("name")
    return None, None


@router.post("/bulk/move-category")
async def bulk_move_category(
    from_slug: str = Query(..., description="Kaynak kategori slug'ı"),
    to_slug: str = Query(..., description="Hedef kategori slug'ı"),
    only_active: bool = Query(True, description="Yalnız aktif (is_active) ürünler"),
    dry_run: bool = Query(True, description="True → önizleme (YAZMAZ). False → uygular."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """from_slug kategorisindeki ürünleri to_slug'a TAŞI: hedef eklenir (+ataları), kaynak
    category_ids+categories'ten ÇIKARILIR. only_active=True → yalnız aktif ürünler. İdempotent."""
    from_id, from_name = await _find_category_by_slug(from_slug)
    to_id, to_name = await _find_category_by_slug(to_slug)
    if not from_id:
        raise HTTPException(status_code=404, detail=f"'{from_slug}' kategorisi bulunamadı.")
    if not to_id:
        raise HTTPException(status_code=404, detail=f"'{to_slug}' kategorisi bulunamadı.")
    to_ids_all = await _expand_category_ids([to_id])
    q = {"$or": [{"category_ids": from_id}, {"categories": from_id}]}
    if only_active:
        q["is_active"] = True
    matched = updated = 0
    sample = []
    _now = datetime.now(timezone.utc).isoformat()
    async for p in db.products.find(q, {"_id": 0, "id": 1, "name": 1, "is_active": 1}):
        matched += 1
        if len(sample) < 20:
            sample.append({"id": p.get("id"), "name": (p.get("name") or "")[:45]})
        if not dry_run:
            # $addToSet ve $pull AYNI alanda tek update'te çakışır → iki ayrı update.
            await db.products.update_one(
                {"id": p["id"]},
                {"$addToSet": {"category_ids": {"$each": to_ids_all}, "categories": to_id},
                 "$set": {"updated_at": _now}})
            await db.products.update_one(
                {"id": p["id"]},
                {"$pull": {"category_ids": from_id, "categories": from_id}})
            updated += 1
    return {
        "from": {"id": from_id, "name": from_name}, "to": {"id": to_id, "name": to_name, "expanded": to_ids_all},
        "only_active": only_active, "matched": matched, "updated": (0 if dry_run else updated),
        "dry_run": dry_run, "sample": sample,
        "message": (f"DRY-RUN: {matched} ürün '{from_name}' → '{to_name}' taşınacak (YAZILMADI). "
                    f"dry_run=false ile uygulayın." if dry_run
                    else f"{updated} ürün '{from_name}' kategorisinden çıkarılıp '{to_name}' kategorisine taşındı."),
    }


@router.post("/bulk/add-category-by-ids")
async def bulk_add_category_by_ids(
    payload: dict,
    dry_run: bool = Query(True, description="True → önizleme (YAZMAZ). False → uygular."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """payload: { ids:[...], category_slugs:[...], activate:bool }. Verilen ürün ID'lerini
    (id / urun_karti_id / urun_id ile eşler) kategorilere ekler (+ataları) ve activate ise
    is_active=True yapar. İdempotent."""
    ids = [str(x).strip() for x in (payload.get("ids") or []) if str(x).strip()]
    slugs = payload.get("category_slugs") or []
    activate = bool(payload.get("activate"))
    if not ids or not slugs:
        raise HTTPException(status_code=400, detail="ids ve category_slugs zorunlu.")
    cat_ids, cat_names, bad_slugs = [], [], []
    for sl in slugs:
        cid, cn = await _find_category_by_slug(sl)
        if cid:
            cat_ids.append(cid); cat_names.append(cn)
        else:
            bad_slugs.append(sl)
    if not cat_ids:
        raise HTTPException(status_code=404, detail=f"Kategori bulunamadı: {bad_slugs}")
    all_cat_ids = await _expand_category_ids(cat_ids)
    matched = updated = 0
    missing, sample = [], []
    _now = datetime.now(timezone.utc).isoformat()
    for pid in ids:
        _cands = [pid]
        if pid.isdigit():
            try:
                _cands.append(int(pid))
            except Exception:
                pass
        p = await db.products.find_one(
            {"$or": [{"id": {"$in": _cands}}, {"urun_karti_id": {"$in": _cands}}, {"urun_id": {"$in": _cands}}]},
            {"_id": 0, "id": 1, "name": 1, "is_active": 1})
        if not p:
            missing.append(pid)
            continue
        matched += 1
        if len(sample) < 40:
            sample.append({"id": pid, "name": (p.get("name") or "")[:45], "was_active": p.get("is_active")})
        if not dry_run:
            ops = {"$addToSet": {"category_ids": {"$each": all_cat_ids}, "categories": {"$each": cat_ids}},
                   "$set": {"updated_at": _now}}
            if activate:
                ops["$set"]["is_active"] = True
            await db.products.update_one({"id": p["id"]}, ops)
            updated += 1
    return {
        "categories": [{"slug": s, "id": i, "name": n} for s, i, n in zip(slugs, cat_ids + [None] * 9, cat_names + [None] * 9)][:len(cat_ids)],
        "bad_slugs": bad_slugs, "activate": activate, "requested": len(ids),
        "matched": matched, "missing": missing, "updated": (0 if dry_run else updated),
        "dry_run": dry_run, "sample": sample,
        "message": (f"DRY-RUN: {matched}/{len(ids)} ürün bulundu, kategorilere eklenecek"
                    f"{' + aktifleştirilecek' if activate else ''} (YAZILMADI). dry_run=false ile uygulayın."
                    if dry_run else
                    f"{updated} ürün kategorilere eklendi{' + aktifleştirildi' if activate else ''}."
                    + (f" {len(missing)} ID bulunamadı." if missing else "")),
    }


# openpyxl, hücrede kontrol karakteri (0x00–0x1F, tab/satır-sonu hariç) görünce
# IllegalCharacterError fırlatıp TÜM dışa aktarımı patlatır. Ürün açıklamaları
# (HTML) ve içe aktarılan kayıtlar bu karakterleri içerebildiği için her metin
# hücresini temizliyoruz. Ayrıca Excel hücre sınırı 32.767 karaktere kırpılır.
_XLSX_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

def _xlsx_clean(v):
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    s = _XLSX_ILLEGAL_RE.sub("", s)
    # CSV/Excel FORMÜL INJECTION koruması: = + - @ ile (veya tab/CR ile) başlayan
    # hücreler Excel'de formül olarak çalışır (DDE/HYPERLINK saldırısı). Başına
    # tek tırnak koyarak metin olarak zorla.
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    if len(s) > 32767:
        s = s[:32767]
    return s


def _polish_product_sheet(ws, df, img_col: int = 0, img_size: str = "medium") -> None:
    """Excel çıktısını SUNULABİLİR hale getirir (kullanıcı: "patrona rapor olarak sunacağım").

    Başlık şeridi, donmuş başlık satırı, otomatik süzgeç, sütun genişlikleri ve dikey
    ortalama. img_col verilirse o sütun görsel için ayrılır (genişlik + ortalama).
    """
    try:
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        import xlsx_images as _XI
    except Exception:
        return
    try:
        _ncol = len(df.columns)
        for _c in range(1, _ncol + 1):
            _cell = ws.cell(row=1, column=_c)
            _cell.fill = PatternFill("solid", fgColor="1F2937")
            _cell.font = Font(bold=True, color="FFFFFF", size=11)
            _cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 28
        ws.freeze_panes = "B2" if img_col else "A2"
        if len(df) > 0:
            ws.auto_filter.ref = f"A1:{get_column_letter(_ncol)}{len(df) + 1}"
        # Sütun genişlikleri: içeriğe göre makul aralıkta (ad/açıklama geniş, sayılar dar)
        for _i, _name in enumerate(df.columns, start=1):
            if img_col and _i == img_col:
                ws.column_dimensions[get_column_letter(_i)].width = _XI.col_width_chars(img_size)
                continue
            _n = str(_name)
            _w = 14
            if "Ad" in _n or "Açıklama" in _n or "Kategori" in _n:
                _w = 38 if "Açıklama" in _n else 30
            elif "Fiyat" in _n or "Tarih" in _n or "Kod" in _n or "Barkod" in _n:
                _w = 16
            elif _n.startswith("Özellik:"):
                _w = 18
            ws.column_dimensions[get_column_letter(_i)].width = _w
        # Görselli satırlarda metin dikeyde ORTALI dursun (görselin yanında "asılı" durmasın)
        if img_col:
            for _r in range(2, len(df) + 2):
                for _c in range(1, _ncol + 1):
                    ws.cell(row=_r, column=_c).alignment = Alignment(
                        vertical="center", wrap_text=(_c != img_col))
    except Exception as _pe:
        logger.debug(f"[excel] biçimlendirme atlandı: {_pe}")


def _group_rows_by_color(rows: list, img_urls):
    """Excel satırlarını (her satır bir beden) ÜRÜN + RENK başına TEK satıra indirir.
    Beden/Barkod/Varyant ID birleştirilir; Stok toplanır ve "Beden Stokları" sütunu eklenir;
    bedenler arasında fiyat farklıysa "min – max" yazılır. Görsel URL'leri satırla hizalı kalır."""
    from collections import OrderedDict
    groups = OrderedDict()
    for i, r in enumerate(rows):
        k = (r.get("Ürün ID"), r.get("Renk"))
        groups.setdefault(k, []).append((r, img_urls[i] if img_urls else None))

    def _n(x):
        try:
            return float(x)
        except Exception:
            return None

    def _span(vals):
        nums = [v for v in (_n(x) for x in vals) if v is not None]
        if not nums:
            return vals[0] if vals else ""
        lo, hi = min(nums), max(nums)
        if lo == hi:
            return lo
        f = lambda v: (f"{v:.2f}".rstrip("0").rstrip("."))
        return f"{f(lo)} – {f(hi)}"

    out_rows, out_imgs = [], []
    for _, lst in groups.items():
        rs = [x[0] for x in lst]
        base = rs[0]
        sizes = [str(r.get("Beden") or "").strip() for r in rs]
        stocks = [_n(r.get("Stok")) or 0 for r in rs]
        new = {}
        for key, val in base.items():
            if key == "Beden":
                new[key] = ", ".join(s for s in sizes if s)
            elif key in ("Barkod", "Varyant ID"):
                new[key] = ", ".join(str(r.get(key) or "") for r in rs if str(r.get(key) or ""))
            elif key in ("Piyasa Fiyatı", "Satış Fiyatı", "Alış Fiyatı", "Trendyol Satış Fiyatı"):
                new[key] = _span([r.get(key) for r in rs])
            elif key == "Stok":
                new[key] = int(sum(stocks))
                new["Beden Stokları"] = " · ".join(f"{s or '—'}: {int(st)}" for s, st in zip(sizes, stocks))
            else:
                new[key] = val
        out_rows.append(new)
        out_imgs.append(lst[0][1])
    return out_rows, (out_imgs if img_urls else img_urls)


@router.get("/export/excel")
async def export_products_excel(
    request: Request,
    current_user: dict = Depends(require_permission("products.view", any_in_area=True)),
    # Liste ekranıyla AYNI filtreler — panelde hangi arama/filtre açıksa Excel de onu indirir.
    category: Optional[str] = None,
    category_id: Optional[str] = None,
    search: Optional[str] = None,
    is_featured: Optional[bool] = None,
    is_new: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    status: Optional[str] = None,
    brand: Optional[str] = None,
    min_stock: Optional[int] = None,
    max_stock: Optional[int] = None,
    is_showcase: Optional[bool] = None,
    is_opportunity: Optional[bool] = None,
    is_free_shipping: Optional[bool] = None,
    stock_code: Optional[str] = None,
    barcode: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    urun_karti_id: Optional[str] = None,
    varyasyon_id: Optional[str] = None,
    name: Optional[str] = None,
    gtip: Optional[str] = None,
    breadcrumb: Optional[str] = None,
    supplier: Optional[str] = None,
    tag: Optional[str] = None,
    has_image: Optional[str] = None,
    has_variants: Optional[str] = None,
    has_video: Optional[str] = None,
    multi_barcode: Optional[str] = None,
    discounted: Optional[str] = None,
    attr_key: Optional[str] = None,
    attr_value: Optional[str] = None,
    pub_date_from: Optional[str] = None,
    pub_date_to: Optional[str] = None,
    sizes: Optional[str] = None,
    colors: Optional[str] = None,
    # GÖRSELLİ DIŞA AKTARMA (kullanıcı isteği): with_images=1 → her satıra ürün fotoğrafı
    # gömülür (oran korunur, kırpma yok). img_size: small | medium | large.
    with_images: Optional[str] = None,
    img_size: Optional[str] = "medium",
    # group_sizes=1 → aynı ürün+renk TEK SATIR (bedenler "XS, S, M" birleşik, stok toplam +
    # beden bazlı stok ayrı sütunda). Varsayılan (boş) eski davranış: her beden ayrı satır.
    group_sizes: Optional[str] = None,
):
    """Ürünleri Excel'e aktar (varyantlar satır satır + dinamik özellikler).

    Liste ekranındaki filtre/aramayla BİREBİR aynı sonuç indirilir: aynı
    `_build_products_query` yardımcı fonksiyonu kullanılır. Hiç filtre yoksa
    admin görünümünün tamamı (aktif + pasif ürünler) aktarılır.
    """
    # ACİL ANAHTAR: freeze_exports açıkken toplu veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off as _sw_off, emit_security_event as _sec_emit
    await _sw_off("freeze_exports")
    try:
        # admin_view="1" → liste ekranındaki admin görünümünün AYNISI (status verilmedikçe hepsi).
        query, _ = await _build_products_query(
            request,
            category=category, category_id=category_id, search=search,
            is_featured=is_featured, is_new=is_new, min_price=min_price, max_price=max_price,
            status=status, admin_view="1", brand=brand,
            min_stock=min_stock, max_stock=max_stock,
            is_showcase=is_showcase, is_opportunity=is_opportunity, is_free_shipping=is_free_shipping,
            stock_code=stock_code, barcode=barcode, date_from=date_from, date_to=date_to,
            urun_karti_id=urun_karti_id, varyasyon_id=varyasyon_id, name=name, gtip=gtip,
            breadcrumb=breadcrumb, supplier=supplier, tag=tag,
            has_image=has_image, has_variants=has_variants, has_video=has_video,
            multi_barcode=multi_barcode, discounted=discounted,
            attr_key=attr_key, attr_value=attr_value,
            pub_date_from=pub_date_from, pub_date_to=pub_date_to,
            sizes=sizes, colors=colors,
        )
        # PERF: Excel için yalnızca KULLANILAN alanları çek (ticimax_fields, pazaryeri
        # attribute blob'ları, images vb. ÇEKİLMEZ) → büyük katalogda bellek/süre düşer,
        # Railway timeout/OOM riski azalır.
        _proj = {
            "_id": 0, "id": 1, "name": 1, "category_name": 1, "brand": 1,
            "stock_code": 1, "barcode": 1, "price": 1, "sale_price": 1, "stock": 1,
            "description": 1, "is_active": 1, "variants": 1, "attributes": 1, "season": 1,
            # #71: alış fiyatı (ALISFIYATI) + pazaryeri taban fiyatı (Trendyol fiyatı hesabı için)
            "purchase_price": 1, "cost_price": 1, "member_price_1": 1,
            # Liste ekranındaki "Ürün Kart ID" ve "Eklenme Tarihi" sütunları Excel'de de olsun.
            "urun_karti_id": 1, "created_at": 1, "updated_at": 1,
        }
        _row_img_urls = []
        _want_img = str(with_images or "").lower() in ("1", "true", "evet", "yes", "on")
        _isize = str(img_size or "medium").lower()
        if _want_img:
            # Görseller YALNIZ istendiğinde çekilir → görselsiz aktarımın hızı/belleği aynı kalır.
            _proj.update({"images": 1, "image": 1, "thumbnail": 1})
        products = await db.products.find(query, _proj).to_list(None)

        def _tr_date(val):
            """ISO/tarih değerini liste ekranıyla aynı biçimde (GG.AA.YYYY) yazar; boşsa ''."""
            if not val:
                return ""
            try:
                if isinstance(val, datetime):
                    return val.strftime("%d.%m.%Y")
                sv = str(val).strip()
                return datetime.fromisoformat(sv.replace("Z", "+00:00")[:26]).strftime("%d.%m.%Y")
            except Exception:
                return str(val)[:10]

        # #71: Trendyol SATIŞ fiyatı sütunu — pazaryeri feed'iyle AYNI formül:
        #   taban (member_price_1 ya da price) × (1 + markup/100). markup: Ana Ayarlar >
        #   trendyol_markup ÖNCELİKLİ, yoksa Trendyol config default_markup.
        _ty_markup = 0.0
        try:
            _main_s = await db.settings.find_one({"id": "main"}, {"_id": 0, "trendyol_markup": 1}) or {}
            _ty_s = await db.settings.find_one({"id": "trendyol"}, {"_id": 0, "default_markup": 1}) or {}
            _mk = _main_s.get("trendyol_markup")
            if _mk in (None, ""):
                _mk = _ty_s.get("default_markup")
            _ty_markup = float(_mk or 0)
        except Exception:
            _ty_markup = 0.0

        # #71: manuel maliyet (product_costs) — alış fiyatı için toplu ön-yükleme (yedek kaynak).
        _cost_map = {}
        try:
            _pids = [p.get("id") for p in products if p.get("id")]
            if _pids:
                async for _c in db.product_costs.find({"product_id": {"$in": _pids}}, {"_id": 0, "product_id": 1, "cost_price": 1}):
                    _cost_map[_c.get("product_id")] = _c.get("cost_price")
        except Exception:
            _cost_map = {}

        def _num(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        def _alis_fiyati(v, p):
            """Alış (maliyet) fiyatı: varyant/ürün purchase_price (ALISFIYATI) → product_costs → cost_price."""
            for cand in (v.get("purchase_price"), p.get("purchase_price"),
                         _cost_map.get(p.get("id")), p.get("cost_price")):
                if cand not in (None, "", 0, 0.0):
                    return _num(cand)
            return 0.0

        def _trendyol_price(v, p):
            base = _num(v.get("member_price_1")) or _num(p.get("member_price_1")) \
                or _num(v.get("price")) or _num(p.get("price"))
            return round(base * (1 + _ty_markup / 100.0), 2) if base > 0 else 0
        
        # ── ÖZELLİKLERİ BİÇİMDEN BAĞIMSIZ OKU ───────────────────────────────────────
        # Ürün özellikleri katalogda BİRDEN ÇOK biçimde duruyor (panel kaydı, Excel içe
        # aktarımı, XML/Ticimax açıklama ayrıştırması, pazaryeri eşlemeleri). Dışa aktarma
        # eskiden YALNIZ [{name|type, value}] biçimini tanıyordu; diğerleri Excel'e HİÇ
        # yazılmıyordu — "özellik alanını doldurduk ama Excel'de yok" şikâyetinin kaynağı.
        # Ayrıca `if attr.get("value")` kullanıldığı için 0 / False / [] gibi FALSY ama
        # ANLAMLI değerler de düşüyordu.
        def _attr_val_str(v):
            if v is None:
                return ""
            if isinstance(v, bool):
                return "Evet" if v else "Hayır"
            if isinstance(v, (list, tuple, set)):
                return ", ".join(x for x in (_attr_val_str(i) for i in v) if x)
            if isinstance(v, dict):
                return _attr_val_str(v.get("value", v.get("label")))
            return str(v).strip()

        def _attr_pairs(raw):
            """Her biçimi (ad, değer) çiftine indirger; tanınmayan biçimde boş liste."""
            out = []
            if isinstance(raw, dict):
                # {slug: {label, value}}  ya da  {ad: değer}
                for k, v in raw.items():
                    if isinstance(v, dict):
                        out.append((v.get("label") or v.get("name") or k,
                                    v.get("value", v.get("val"))))
                    else:
                        out.append((k, v))
            elif isinstance(raw, list):
                for a in raw:
                    if isinstance(a, dict):
                        _n = (a.get("name") or a.get("type") or a.get("label")
                              or a.get("key") or a.get("attribute_name"))
                        _v = a.get("value", a.get("val", a.get("value_name", a.get("values"))))
                        out.append((_n, _v))
                    elif isinstance(a, str) and ":" in a:
                        _k, _, _v = a.partition(":")      # "Kumaş: Pamuk" düz metin
                        out.append((_k, _v))
            res = []
            for _n, _v in out:
                _n = str(_n or "").strip()
                if not _n:
                    continue
                res.append((_n, _attr_val_str(_v)))
            return res

        # Collect all unique attribute names (sütun sırası SABİT: set sırası her aktarımda
        # değişiyordu, aynı rapor iki kez alındığında sütunlar yer değiştiriyordu).
        all_attr_names = []
        _seen_attr = set()
        for p in products:
            for _an, _ in _attr_pairs(p.get("attributes")):
                if _an not in _seen_attr:
                    _seen_attr.add(_an)
                    all_attr_names.append(_an)
        all_attr_names.sort(key=lambda x: str(x).casefold())
        
        rows = []
        for p in products:
            variants = p.get("variants", [])
            if not variants:
                variants = [{
                    "barcode": p.get("barcode", ""),
                    "stock_code": p.get("stock_code", ""),
                    "price": p.get("price", 0),
                    "sale_price": p.get("sale_price"),
                    "stock": p.get("stock", 0),
                    "size": "",
                    "color": ""
                }]
            
            for v in variants:
                if not isinstance(v, dict):
                    continue
                # ID'ler METİN olarak yazılır (Excel uzun/sayısal id'yi 1.23E+15'e çevirmesin) ve
                # her SATIR bir VARYANT olduğundan varyantın KENDİ id'si ayrı kolonda verilir —
                # önceden tüm varyant satırlarına aynı ÜRÜN id'si basılıyordu (hatalı aktarım).
                row = {
                    "Ürün Kart ID": str(p.get("urun_karti_id") or ""),
                    "Ürün ID": str(p.get("id") or ""),
                    "Varyant ID": str(v.get("id") or v.get("urun_id") or ""),
                    "Ürün Adı": p.get("name"),
                    "Kategori": p.get("category_name"),
                    "Marka": p.get("brand"),
                    "Stok Kodu": v.get("stock_code") or p.get("stock_code"),
                    "Sezon": p.get("season", ""),
                    "Barkod": v.get("barcode") or p.get("barcode"),
                    "Beden": v.get("size", ""),
                    "Renk": v.get("color", ""),
                    "Piyasa Fiyatı": v.get("price") or p.get("price", 0),
                    "Satış Fiyatı": v.get("sale_price") or p.get("sale_price") or p.get("price", 0),
                    "Alış Fiyatı": _alis_fiyati(v, p),
                    "Trendyol Satış Fiyatı": _trendyol_price(v, p),
                    "Stok": v.get("stock", 0),
                    "Açıklama": p.get("description", ""),
                    "Aktif": "Evet" if p.get("is_active") else "Hayır",
                    "Eklenme Tarihi": _tr_date(p.get("created_at")),
                    "Güncelleme Tarihi": _tr_date(p.get("updated_at")),
                }
                
                # pre-fill attributes with empty string
                for attr_name in all_attr_names:
                    row[f"Özellik: {attr_name}"] = ""

                # apply product attributes (biçimden bağımsız; boş METİN dışında hepsi yazılır)
                for _an, _av in _attr_pairs(p.get("attributes")):
                    if _av != "":
                        row[f"Özellik: {_an}"] = _av
                        
                # Her hücreyi openpyxl-güvenli hale getir (kontrol karakteri temizliği + 32k kırpma)
                _clean = {k: _xlsx_clean(val) for k, val in row.items()}
                if _want_img:
                    from xlsx_images import first_image_url as _fiu
                    _row_img_urls.append(_fiu(p))
                    _clean = {"Görsel": "", **_clean}   # görsel EN SOLDA
                rows.append(_clean)
        
        if str(group_sizes or "").lower() in ("1", "true", "evet", "yes", "on"):
            rows, _row_img_urls = _group_rows_by_color(rows, _row_img_urls if _want_img else None)
        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Ürünler')
            ws = writer.sheets['Ürünler']
            _polish_product_sheet(ws, df, img_col=1 if _want_img else 0, img_size=_isize)
            if _want_img and _row_img_urls:
                import xlsx_images as _XI
                _cache = await _XI.fetch_images(_row_img_urls, _isize)
                _ok = 0
                for _i, _u in enumerate(_row_img_urls, start=2):   # 1. satır başlık
                    _png = _cache.get(_u)
                    if _png and _XI.put_image(ws, _i, 1, _png, _isize):
                        _ok += 1
                logger.info(f"[excel] ürün listesi: {_ok}/{len(_row_img_urls)} satıra görsel gömüldü")
        
        headers = {
            'Content-Disposition': 'attachment; filename="urunler.xlsx"',
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }
        # Güvenlik olayı: toplu dışa aktarım (denetim izi + süper admin uyarısı; asla istisna fırlatmaz).
        await _sec_emit("products_export", current_user, {"export": "urunler.xlsx", "rows": len(products)})
        return Response(content=output.getvalue(), headers=headers)
        
    except Exception as e:
        logger.error(f"Excel export error: {e}")
        raise HTTPException(status_code=500, detail=f"Dışa aktarma hatası: {str(e)}")

def _norm_season_cell(val) -> str:
    """Excel'deki Sezon hücresini 4 kanonik değere indirger:
    İlkbahar/Sonbahar · Tüm Sezonlar · Yaz · Kış.
    Türkçe İ/ı büyük-küçük tuzağına takılmamak için aksan/kombine işaretler soyulur.
    Boş/tanınmayan değer '' döner — mevcut sezon SİLİNMEZ, satır atlanır."""
    import unicodedata
    s = str(val or "").strip()
    if not s or s.lower() == "nan":
        return ""
    folded = "".join(c for c in unicodedata.normalize("NFKD", s.casefold())
                     if not unicodedata.combining(c)).replace("ı", "i")
    return {
        "yaz": "Yaz", "kis": "Kış",
        # Ara sezonlar tek grupta: tek başına ilkbahar/sonbahar da bu gruba düşer
        "ilkbahar": "İlkbahar/Sonbahar", "sonbahar": "İlkbahar/Sonbahar",
        "ilkbahar/sonbahar": "İlkbahar/Sonbahar", "ilkbahar-sonbahar": "İlkbahar/Sonbahar",
        "tum sezonlar": "Tüm Sezonlar", "tum sezon": "Tüm Sezonlar",
        "4 mevsim": "Tüm Sezonlar", "all season": "Tüm Sezonlar",
    }.get(folded, "")


# Seçmeli güncellemede işlenebilen ürün/varyant sütunları (Özellik: * ayrıca desteklenir)
_IMPORT_UPDATABLE = ["Piyasa Fiyatı", "Satış Fiyatı", "Stok", "Sezon", "Açıklama", "Aktif"]


@router.post("/import/excel/analyze")
async def analyze_products_excel(file: UploadFile = File(...), current_user: dict = Depends(require_permission("products.import_xlsx"))):
    """Excel'i YAZMADAN çözümler: format uygun mu, hangi sütunlar var, kaç satır/ürün
    eşleşiyor, dosyada hangi kategoriler geçiyor. Panel bu bilgiyle 'hangi sütunlar +
    hangi kategoriler güncellensin' seçtirir; asıl yazma /import/excel'e seçimlerle gider."""
    from security.spreadsheet_guard import read_spreadsheet_upload, check_rows  # denetim #18
    contents = await read_spreadsheet_upload(file)
    try:
        from security.spreadsheet_guard import parse_offloop as _pol
        df = await _pol(pd.read_excel, io.BytesIO(contents))  # denetim #18: olay döngüsü dışında
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel okunamadı: {str(e)[:200]}")
    check_rows(len(df))
    cols = [str(c) for c in df.columns]
    if "Barkod" not in cols:
        raise HTTPException(status_code=400, detail="Format uygun değil: 'Barkod' sütunu zorunlu. 'Excel İndir' çıktısını temel alın.")
    updatable = [c for c in cols if c in _IMPORT_UPDATABLE or str(c).startswith("Özellik: ")]
    barcodes = [str(b).strip() for b in df["Barkod"].tolist()
                if str(b).strip() and str(b).strip().lower() != "nan"]
    uniq = list(dict.fromkeys(barcodes))
    matched = 0
    for i in range(0, len(uniq), 5000):
        matched += await db.products.count_documents({"variants.barcode": {"$in": uniq[i:i + 5000]}})
    cats = []
    if "Kategori" in cols:
        cats = sorted({str(v).strip() for v in df["Kategori"].dropna().tolist()
                       if str(v).strip() and str(v).strip().lower() != "nan"})
    return {"success": True, "rows": int(len(df)), "unique_barcodes": len(uniq),
            "matched_products": matched, "columns": cols,
            "updatable_columns": updatable, "categories": cats[:300]}


@router.post("/import/excel")
async def import_products_excel(
    request: Request,
    file: UploadFile = File(...),
    columns: str = Form(""),      # seçmeli mod: virgüllü sütun listesi (boş = eski tam aktarım)
    categories: str = Form(""),   # seçmeli mod: yalnız bu kategorilerdeki satırlar güncellenir
    current_user: dict = Depends(require_permission("products.import_xlsx")),
):
    """Import or update products from an Excel file.

    `columns` doluysa SEÇMELİ GÜNCELLEME modu: yeni ürün AÇILMAZ, yalnız seçilen
    sütunlar ve (verildiyse) seçilen kategorilerdeki satırlar güncellenir — toplu
    sorgu + küçük yazımlarla eski tam aktarımdan çok daha hızlıdır."""
    sel_cols = [c.strip() for c in (columns or "").split(",") if c.strip()]
    sel_cats = {c.strip() for c in (categories or "").split(",") if c.strip()}
    if sel_cols:
        return await _selective_import(file, sel_cols, sel_cats, current_user, request)
    from security.spreadsheet_guard import read_spreadsheet_upload, check_rows  # denetim #18
    contents = await read_spreadsheet_upload(file)
    try:
        from security.spreadsheet_guard import parse_offloop as _pol
        df = await _pol(pd.read_excel, io.BytesIO(contents))  # denetim #18: olay döngüsü dışında
        check_rows(len(df))
        
        # Validation
        required = ["Ürün Adı", "Kategori", "Satış Fiyatı"]
        for col in required:
            if col not in df.columns:
                raise Exception(f"Eksik sütun: {col}")
        
        stats = {"created": 0, "updated": 0, "errors": 0}
        
        for _, row in df.iterrows():
            try:
                barcode = str(row.get("Barkod", "")).strip()
                if not barcode or barcode == "nan":
                    continue

                # Sezon (ürün düzeyi) — geçerli değer varsa güncellenir, boş/bilinmeyen dokunmaz
                _season = _norm_season_cell(row.get("Sezon")) if "Sezon" in df.columns else ""
                
                # Parse dynamic attributes from columns
                parsed_attrs = []
                import uuid
                for col in df.columns:
                    if str(col).startswith("Özellik: "):
                        attr_name = str(col).replace("Özellik: ", "").strip()
                        val = str(row.get(col, "")).strip()
                        
                        # Ensure attribute exists in global library
                        existing_global = await db.attributes.find_one({"name": attr_name})
                        if not existing_global:
                            await db.attributes.insert_one({
                                "id": f"attr_{uuid.uuid4().hex[:8]}",
                                "name": attr_name,
                                "values": []
                            })
                            
                        if val and val != "nan":
                            parsed_attrs.append({"type": attr_name, "name": attr_name, "value": val})
                
                # Try finding product by variant barcode
                existing = await db.products.find_one({"variants.barcode": barcode})
                
                if existing:
                    # Update variant in existing product
                    update_fields = {
                        "variants.$.stock": int(row.get("Stok", 0) if pd.notna(row.get("Stok")) else 0),
                        "variants.$.price": float(row.get("Piyasa Fiyatı", row.get("Satış Fiyatı", 0)) if pd.notna(row.get("Piyasa Fiyatı", row.get("Satış Fiyatı", 0))) else 0),
                        "variants.$.sale_price": float(row.get("Satış Fiyatı", 0) if pd.notna(row.get("Satış Fiyatı")) else 0),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    if parsed_attrs:
                        update_fields["attributes"] = parsed_attrs
                    if _season:
                        update_fields["season"] = _season

                    await db.products.update_one(
                        {"id": existing["id"], "variants.barcode": barcode},
                        {"$set": update_fields}
                    )
                    await db.products.update_one(
                        {"id": existing["id"]},
                        [{"$set": {"stock": {"$sum": {"$map": {
                            "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                            "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}]},
                        }}}}}],
                    )
                    _after = await db.products.find_one({"id": existing["id"]}, {"_id": 0}) or existing
                    await record_stock_audit(
                        db, product_id=existing["id"], product_name=existing.get("name", ""),
                        before=existing, after=_after, source="product_excel_import",
                        current_user=current_user, request=request, action="bulk_stock_adjusted",
                    )
                    stats["updated"] += 1
                else:
                    # Create new product or add as variant to existing product with same name
                    name = str(row.get("Ürün Adı"))
                    prod_by_name = await db.products.find_one({"name": name})
                    
                    variant = {
                        "barcode": barcode,
                        "stock_code": str(row.get("Stok Kodu", "")).replace("nan", ""),
                        "size": str(row.get("Beden", "")).replace("nan", ""),
                        "color": str(row.get("Renk", "")).replace("nan", ""),
                        "price": float(row.get("Piyasa Fiyatı", row.get("Satış Fiyatı", 0)) if pd.notna(row.get("Piyasa Fiyatı", row.get("Satış Fiyatı", 0))) else 0),
                        "sale_price": float(row.get("Satış Fiyatı", 0) if pd.notna(row.get("Satış Fiyatı")) else 0),
                        "stock": int(row.get("Stok", 0) if pd.notna(row.get("Stok")) else 0)
                    }
                    
                    if prod_by_name:
                        # Add as new variant
                        update_fields = {
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                        if parsed_attrs:
                            update_fields["attributes"] = parsed_attrs
                        if _season:
                            update_fields["season"] = _season

                        await db.products.update_one(
                            {"id": prod_by_name["id"]},
                            {"$push": {"variants": variant}, "$set": update_fields}
                        )
                        await db.products.update_one(
                            {"id": prod_by_name["id"]},
                            [{"$set": {"stock": {"$sum": {"$map": {
                                "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                                "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}]},
                            }}}}}],
                        )
                        _after = await db.products.find_one({"id": prod_by_name["id"]}, {"_id": 0}) or prod_by_name
                        await record_stock_audit(
                            db, product_id=prod_by_name["id"], product_name=prod_by_name.get("name", ""),
                            before=prod_by_name, after=_after, source="product_excel_import",
                            current_user=current_user, request=request, action="bulk_stock_adjusted",
                        )
                        stats["updated"] += 1
                    else:
                        # Create full new product
                        new_id = await generate_short_id("products")
                        new_p = {
                            "id": new_id,
                            "name": name,
                            "slug": generate_slug(name),
                            "category_name": str(row.get("Kategori", "")).replace("nan", ""),
                            "brand": str(row.get("Marka", "")).replace("nan", ""),
                            "description": str(row.get("Açıklama", "")).replace("nan", ""),
                            "price": variant["price"],
                            "sale_price": variant["sale_price"],
                            "stock": variant["stock"],
                            "is_active": True,
                            "season": _season,
                            "variants": [variant],
                            "images": [],
                            "attributes": parsed_attrs,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                        await db.products.insert_one(new_p)
                        await record_stock_audit(
                            db, product_id=new_p["id"], product_name=new_p.get("name", ""),
                            before={}, after=new_p, source="product_excel_import",
                            current_user=current_user, request=request, action="product_created",
                        )
                        stats["created"] += 1
            except Exception as row_err:
                logger.error(f"Import row error: {row_err}")
                stats["errors"] += 1
                
        return {"success": True, "stats": stats}

    except Exception as e:
        logger.error(f"Excel import error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _selective_import(file: UploadFile, sel_cols: list, sel_cats: set,
                            current_user: dict = None, request: Request = None):
    """Seçmeli güncelleme: yalnız seçilen sütunlar + (verildiyse) seçilen kategorilerdeki
    satırlar. Yeni ürün AÇILMAZ. Barkod→ürün eşlemesi TOPLU sorgu ile önceden çekilir."""
    import time as _time
    _t0 = _time.monotonic()
    from security.spreadsheet_guard import read_spreadsheet_upload, check_rows  # denetim #18
    contents = await read_spreadsheet_upload(file)
    try:
        from security.spreadsheet_guard import parse_offloop as _pol
        df = await _pol(pd.read_excel, io.BytesIO(contents))  # denetim #18: olay döngüsü dışında
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel okunamadı: {str(e)[:200]}")
    check_rows(len(df))
    if "Barkod" not in [str(c) for c in df.columns]:
        raise HTTPException(status_code=400, detail="'Barkod' sütunu zorunlu")

    attr_cols = [c for c in sel_cols if c.startswith("Özellik: ")]
    plain_cols = [c for c in sel_cols if not c.startswith("Özellik: ")]

    # 1) Satırları topla
    rows = []
    for _, row in df.iterrows():
        bc = str(row.get("Barkod", "")).strip()
        if not bc or bc.lower() == "nan":
            continue
        rows.append((bc, row))

    # 2) Barkod → ürün eşlemesi (toplu)
    uniq = list(dict.fromkeys(bc for bc, _ in rows))
    _proj = {"_id": 0, "id": 1, "variants.barcode": 1, "category_name": 1}
    if attr_cols:
        _proj["attributes"] = 1
    bc_map = {}
    for i in range(0, len(uniq), 5000):
        async for p in db.products.find({"variants.barcode": {"$in": uniq[i:i + 5000]}}, _proj):
            for v in (p.get("variants") or []):
                b = str(v.get("barcode") or "").strip()
                if b:
                    bc_map[b] = p

    stats = {"updated_rows": 0, "skipped": 0, "no_match": 0, "errors": 0}
    updated_products = set()
    _deact_fired = set()   # toplu import'ta pasife-alma kancası ürün başına 1 kez
    _now = datetime.now(timezone.utc).isoformat()

    def _num(row, col):
        try:
            v = row.get(col)
            return float(v) if pd.notna(v) else None
        except Exception:
            return None

    for bc, row in rows:
        try:
            p = bc_map.get(bc)
            if not p:
                stats["no_match"] += 1
                continue
            # Kategori süzgeci: önce dosyadaki hücre, boşsa üründeki kategori
            if sel_cats:
                _rc = str(row.get("Kategori", "") or "").strip()
                _cat = _rc if _rc and _rc.lower() != "nan" else str(p.get("category_name") or "").strip()
                if _cat not in sel_cats:
                    stats["skipped"] += 1
                    continue

            vset = {"updated_at": _now}   # variants.$ hedefli
            pset = {}                     # ürün düzeyi
            if "Stok" in plain_cols:
                _v = _num(row, "Stok")
                if _v is not None:
                    vset["variants.$.stock"] = int(_v)
            if "Piyasa Fiyatı" in plain_cols:
                _v = _num(row, "Piyasa Fiyatı")
                if _v is not None:
                    vset["variants.$.price"] = _v
            if "Satış Fiyatı" in plain_cols:
                _v = _num(row, "Satış Fiyatı")
                if _v is not None:
                    vset["variants.$.sale_price"] = _v
            if "Sezon" in plain_cols:
                _s = _norm_season_cell(row.get("Sezon"))
                if _s:
                    pset["season"] = _s
            if "Açıklama" in plain_cols:
                _d = str(row.get("Açıklama", "") or "").strip()
                if _d and _d.lower() != "nan":
                    pset["description"] = _d
            if "Aktif" in plain_cols:
                _a = str(row.get("Aktif", "") or "").strip().lower()
                if _a in ("evet", "hayır", "hayir"):
                    pset["is_active"] = (_a == "evet")
            if attr_cols:
                # Seçilen özellikleri mevcut listeye İSİMLE birleştir (diğer özellikler korunur)
                attrs = [a for a in (p.get("attributes") or []) if isinstance(a, dict)]
                for col in attr_cols:
                    name = col.replace("Özellik: ", "").strip()
                    val = str(row.get(col, "") or "").strip()
                    if not val or val.lower() == "nan":
                        continue
                    attrs = [a for a in attrs if str(a.get("name") or a.get("type") or "").strip() != name]
                    attrs.append({"type": name, "name": name, "value": val})
                pset["attributes"] = attrs
                p["attributes"] = attrs  # aynı ürünün sonraki satırları güncel listeyi görsün

            if len(vset) <= 1 and not pset:
                stats["skipped"] += 1
                continue
            _upd = {**vset, **pset} if len(vset) > 1 else {"updated_at": _now, **pset}
            _before = None
            if "variants.$.stock" in _upd:
                _before = await db.products.find_one({"id": p["id"]}, {"_id": 0})
            await db.products.update_one({"id": p["id"], "variants.barcode": bc}, {"$set": _upd})
            if _before is not None:
                await db.products.update_one(
                    {"id": p["id"]},
                    [{"$set": {"stock": {"$sum": {"$map": {
                        "input": {"$ifNull": ["$variants", []]}, "as": "vv",
                        "in": {"$max": [0, {"$toInt": {"$ifNull": ["$$vv.stock", 0]}}]},
                    }}}}}],
                )
                _after = await db.products.find_one({"id": p["id"]}, {"_id": 0}) or _before
                await record_stock_audit(
                    db, product_id=p["id"], product_name=_before.get("name", ""),
                    before=_before, after=_after, source="product_excel_selective",
                    current_user=current_user, request=request, action="bulk_stock_adjusted",
                )
            stats["updated_rows"] += 1
            updated_products.add(p["id"])
            # Toplu import ile is_active TRUE→FALSE geçişi → pazaryerlerine 0 stok (ürün başına 1 kez).
            if pset.get("is_active") is False and p.get("is_active") and p["id"] not in _deact_fired:
                _deact_fired.add(p["id"])
                _fire_stock_zero_on_passive(dict(p))
        except Exception as row_err:
            logger.error(f"Selective import row error ({bc}): {row_err}")
            stats["errors"] += 1

    stats["updated_products"] = len(updated_products)
    stats["duration_sec"] = round(_time.monotonic() - _t0, 1)
    stats["mode"] = "selective"
    return {"success": True, "stats": stats}



@router.post("/attributes/import-technical-xlsx")
async def import_technical_details_xlsx(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_permission("products.import_technical"))
):
    """
    Import technical details from Excel in format:
    UrunKartID | StokKodu | UrunAdi | Ozellik | Deger
    Groups by UrunAdi, fuzzy matches with existing products, returns preview.
    """
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl yüklenmemiş")

    from security.spreadsheet_guard import read_spreadsheet_upload, check_rows  # denetim #18
    content = await read_spreadsheet_upload(file)
    from security.spreadsheet_guard import parse_offloop as _pol
    wb = await _pol(openpyxl.load_workbook, io.BytesIO(content))  # denetim #18: olay döngüsü dışında
    ws = wb.active
    check_rows(ws.max_row)

    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="Dosya boş veya başlık satırı eksik")

    headers = [str(h).strip() if h else "" for h in rows[0]]

    # Find column indices
    name_col = None
    ozellik_col = None
    deger_col = None
    stok_kodu_col = None

    for i, h in enumerate(headers):
        hl = h.lower().replace("ı", "i").replace("ö", "o").replace("ü", "u")
        if "urunadi" in hl.replace(" ", "") or "ürün adı" in h.lower() or "urun adi" in h.lower():
            name_col = i
        elif "ozellik" in hl.replace(" ", "") or "özellik" in h.lower():
            ozellik_col = i
        elif "deger" in hl.replace(" ", "") or "değer" in h.lower():
            deger_col = i
        elif "stokkodu" in hl.replace(" ", "") or "stok kodu" in h.lower():
            stok_kodu_col = i

    if name_col is None:
        raise HTTPException(status_code=400, detail="UrunAdi sütunu bulunamadı")
    if deger_col is None:
        raise HTTPException(status_code=400, detail="Deger sütunu bulunamadı")

    # Group by product name - deduplicate attributes (last value wins for same type)
    product_groups = {}
    # Metadata column headers that should not be treated as attributes
    meta_headers_lower = {h.lower().strip() for h in headers if h}

    for row in rows[1:]:
        name = str(row[name_col]).strip() if row[name_col] else None
        if not name or name.lower() in ("none", "null", ""):
            continue

        ozellik = str(row[ozellik_col]).strip() if ozellik_col is not None and row[ozellik_col] else ""
        deger = str(row[deger_col]).strip() if row[deger_col] else ""
        stok_kodu = str(row[stok_kodu_col]).strip() if stok_kodu_col is not None and row[stok_kodu_col] else ""

        if not deger or deger.lower() in ("none", "null"):
            continue

        if name not in product_groups:
            product_groups[name] = {"stok_kodu": stok_kodu, "attributes": {}, "extra_colors": []}

        if ozellik and ozellik.lower() not in ("none", "null", "") and ozellik.lower() not in meta_headers_lower:
            # Use dict to deduplicate (last value wins)
            product_groups[name]["attributes"][ozellik] = deger
        elif not ozellik or ozellik.lower() in ("none", "null", ""):
            # Empty ozellik with a deger = extra color variant
            product_groups[name]["extra_colors"].append(deger)

    # Convert attribute dicts to list format
    for name, data in product_groups.items():
        data["attributes_list"] = [{"type": k, "value": v} for k, v in data["attributes"].items()]

    # Now match products by name - one Excel product can match MULTIPLE DB products
    all_products = await db.products.find({}, {"_id": 0, "id": 1, "name": 1, "stock_code": 1}).to_list(None)

    def normalize(s):
        return s.lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c").replace("İ", "i").strip()

    results = []
    used_product_ids = set()

    for excel_name, data in product_groups.items():
        excel_norm = normalize(excel_name)

        # Find ALL matching products (for color variants)
        matches = []
        for p in all_products:
            p_norm = normalize(p["name"])
            if p_norm == excel_norm:
                matches.append((p, 100))
            elif excel_norm in p_norm:
                # Excel name is a substring of DB name (e.g., "Basic Triko" in "Basic Triko Siyah")
                overlap = len(excel_norm.split()) / len(p_norm.split()) * 100
                if overlap >= 50:
                    matches.append((p, round(overlap, 1)))
            elif p_norm in excel_norm:
                overlap = len(p_norm.split()) / len(excel_norm.split()) * 100
                if overlap >= 50:
                    matches.append((p, round(overlap, 1)))

        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            for match_p, match_score in matches:
                if match_p["id"] not in used_product_ids:
                    results.append({
                        "excel_name": excel_name,
                        "stok_kodu": data["stok_kodu"],
                        "attributes": data["attributes_list"],
                        "extra_colors": data["extra_colors"],
                        "matched_product_id": match_p["id"],
                        "matched_product_name": match_p["name"],
                        "match_score": match_score
                    })
                    used_product_ids.add(match_p["id"])
        else:
            results.append({
                "excel_name": excel_name,
                "stok_kodu": data["stok_kodu"],
                "attributes": data["attributes_list"],
                "extra_colors": data["extra_colors"],
                "matched_product_id": None,
                "matched_product_name": None,
                "match_score": 0
            })

    results.sort(key=lambda r: r["match_score"], reverse=True)

    return {
        "success": True,
        "total_excel_products": len(results),
        "matched": sum(1 for r in results if r["matched_product_id"]),
        "unmatched": sum(1 for r in results if not r["matched_product_id"]),
        "results": results
    }


@router.post("/attributes/apply-technical-xlsx")
async def apply_technical_details(payload: dict, current_user: dict = Depends(require_permission("products.import_technical"))):
    """
    Apply matched technical details to products.
    Payload: { updates: [{product_id, attributes: [{type, value}], extra_colors: []}] }
    """
    updates = payload.get("updates", [])
    if not updates:
        raise HTTPException(status_code=400, detail="Güncellenecek ürün yok")

    updated = 0
    attr_lib_updates = {}

    for update in updates:
        product_id = update.get("product_id")
        attributes = update.get("attributes", [])
        if not product_id or not attributes:
            continue

        # Replace attributes with Excel data (clean import)
        new_attrs = [{"type": a["type"], "name": a["type"], "value": a["value"]} for a in attributes]

        # Track for attribute library
        for new_attr in attributes:
            if new_attr["type"] not in attr_lib_updates:
                attr_lib_updates[new_attr["type"]] = set()
            attr_lib_updates[new_attr["type"]].add(new_attr["value"])

        await db.products.update_one(
            {"id": product_id},
            {"$set": {
                "attributes": new_attrs,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        updated += 1

    # Update global attribute library with new values
    for attr_name, val_set in attr_lib_updates.items():
        existing_lib = await db.attributes.find_one({"name": {"$regex": f"^{re.escape(attr_name)}$", "$options": "i"}})
        if existing_lib:
            current_vals = set(existing_lib.get("values", []))
            merged_vals = list(current_vals.union(val_set))
            if len(merged_vals) > len(current_vals):
                await db.attributes.update_one(
                    {"_id": existing_lib["_id"]},
                    {"$set": {"values": merged_vals, "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
        else:
            await db.attributes.insert_one({
                "id": generate_id(),
                "name": attr_name,
                "values": list(val_set),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })

    return {"success": True, "updated": updated, "message": f"{updated} ürünün özellikleri güncellendi"}


# ─────────────────────────────────────────────────────────────────────────────
# HAYALET-DUPLİKE TEMİZLİĞİ — aynı `id` alanına sahip birden çok products dökümanı
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/cleanup/ghost-duplicates")
async def cleanup_ghost_duplicates(
    dry_run: bool = Query(True, description="True → yalnız rapor (YAZMAZ/SİLMEZ). False → hayaletleri siler."),
    current_user: dict = Depends(require_permission("products.edit")),
):
    """Aynı `id` değerine sahip BİRDEN ÇOK products dökümanını bulur; en sağlıklı
    KOPYAYI KORUR, diğer (hayalet/iskelet) kopyaları siler.

    NEDEN VAR: Ticimax migrasyonundan aynı `id`'li ikiz dökümanlar kaldı. Liste
    endpoint'i `is_active: True` filtresiyle hayaleti GİZLİYOR, ama filtresiz
    `find_one({"id": ...})` çağrıları (HB motoru, debug-payload) İLK eşleşen olarak
    HAYALETİ buluyor → ürün kartına yazılan özellikler motor tarafında GÖRÜNMÜYOR
    ("Kalıp eksik" hatasının kökü). update_one ile find_one aynı id'de FARKLI
    dökümanlara gidebildiği için yazma-okuma tutarsızlığı oluşuyor.

    KORUNACAK kopya seçimi (yüksek skor kazanır):
      is_deleted != True  → +8   (çöpteki asla korunmaz, aktif dururken)
      is_active == True   → +4
      variants dolu       → +2
      stock_code dolu     → +1
      eşitlikte: updated_at en yeni olan.

    `id` string/int karışıklığına dayanıklıdır ($toString ile gruplar).
    Her zaman ÖNCE dry_run=true ile raporu gör, sonra dry_run=false uygula.
    """
    pipeline = [
        {"$group": {
            "_id": {"$toString": {"$ifNull": ["$id", "$_id"]}},
            "n": {"$sum": 1},
            "docs": {"$push": {
                "oid": "$_id",
                "name": "$name",
                "is_active": "$is_active",
                "is_deleted": "$is_deleted",
                "stock_code": "$stock_code",
                "updated_at": "$updated_at",
                "variants_n": {"$cond": [{"$isArray": "$variants"}, {"$size": "$variants"}, 0]},
                "attrs_n": {"$cond": [{"$isArray": "$attributes"}, {"$size": "$attributes"}, 0]},
            }},
        }},
        {"$match": {"n": {"$gt": 1}}},
    ]
    groups = await db.products.aggregate(pipeline).to_list(length=5000)

    def _score(doc: dict):
        s = 0
        if doc.get("is_deleted") is not True:
            s += 8
        if doc.get("is_active") is True:
            s += 4
        if (doc.get("variants_n") or 0) > 0:
            s += 2
        if doc.get("stock_code"):
            s += 1
        return (s, str(doc.get("updated_at") or ""))

    report, delete_oids = [], []
    for g in groups:
        docs = sorted(g.get("docs") or [], key=_score, reverse=True)
        keep, ghosts = docs[0], docs[1:]
        delete_oids.extend(d["oid"] for d in ghosts)
        report.append({
            "id": g["_id"], "count": g["n"],
            "keep": {k: keep.get(k) for k in ("name", "is_active", "stock_code", "variants_n", "attrs_n", "updated_at")},
            "ghosts": [{k: d.get(k) for k in ("name", "is_active", "is_deleted", "stock_code", "variants_n", "attrs_n", "updated_at")} for d in ghosts],
        })

    deleted = 0
    if not dry_run and delete_oids:
        res = await db.products.delete_many({"_id": {"$in": delete_oids}})
        deleted = res.deleted_count

    return {
        "success": True, "dry_run": dry_run,
        "duplicate_groups": len(groups), "ghost_docs": len(delete_oids), "deleted": deleted,
        "message": (f"{len(groups)} duplike id grubu, {len(delete_oids)} hayalet döküman bulundu"
                    + ("" if dry_run else f"; {deleted} silindi")),
        "report": report[:200],
    }
