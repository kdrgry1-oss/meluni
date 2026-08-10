"""
Ticimax SOAP UrunServis yetkimiz olmadığı için, public storefront sayfalarını
scrape ederek belirtilen URUNKARTIID'lere sahip ürünleri DB'ye işler.

Kullanım:
    python3 ticimax_pull_via_storefront.py 2889 2879 2840 2839

Strateji:
  1. https://www.facette.com.tr/sitemap/products/0.xml ile tüm ürün URL'lerini al
  2. URL sonundaki "-<ID>" suffix'i hedef URUNKARTIID'lerle eşleştir
  3. Her ürün sayfasındaki `var productDetailModel = {...};` JSON'unu parse et
  4. products + productVariantData + productImages + breadCrumb verilerini
     mevcut DB schema'sına dönüştür ve upsert et.
"""
import asyncio
import os
import re
import sys
import json
import unicodedata
from typing import List, Dict, Set, Optional
from uuid import uuid4
from datetime import datetime, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

BASE = "https://www.facette.com.tr"
SITEMAP = f"{BASE}/sitemap/products/0.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FacetteSync/1.0)"}


def _slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:200] or "urun"


def _extract_product_detail_model(html: str) -> Optional[dict]:
    """productDetailModel JS object'inin tamamını brace-balance ile çıkar ve parse et."""
    idx = html.find("var productDetailModel = ")
    if idx < 0:
        idx = html.find("productDetailModel = ")
        if idx < 0:
            return None
    start = html.index("{", idx)
    depth = 0
    i = start
    in_str = False
    esc = False
    end = None
    while i < len(html):
        ch = html[i]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"' and not esc:
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        i += 1
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except Exception as e:
        print(f"  ✖ productDetailModel JSON parse hatası: {e}")
        return None


def _find_target_urls(target_ids: Set[int]) -> Dict[int, str]:
    """Sitemap'ten hedef URUNKARTIID'lere ait URL'leri bul."""
    r = requests.get(SITEMAP, timeout=30, headers=HEADERS)
    r.raise_for_status()
    urls = re.findall(r"<loc>(https://www\.facette\.com\.tr/[^<]+)</loc>", r.text)
    found: Dict[int, str] = {}
    for u in urls:
        m = re.search(r"-(\d{3,6})$", u)
        if not m:
            continue
        kid = int(m.group(1))
        if kid in target_ids:
            found[kid] = u
    return found


def _detect_color_from_url(url: str) -> str:
    """URL slug'ından renk tahmini (son segment + ID'den önceki kelime)."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-\d+$", "", slug)
    # son kelimeyi renk olarak al
    parts = slug.split("-")
    color_words = {
        "siyah", "beyaz", "kirmizi", "mavi", "yesil", "sari", "mor", "pembe",
        "ekru", "haki", "bej", "lacivert", "gri", "kahverengi", "krem",
        "turuncu", "vizon", "petrol", "fume", "antrasit",
    }
    for w in reversed(parts):
        if w.lower() in color_words:
            return w.title()
    return parts[-1].title() if parts else ""


def _build_product_doc(pdm: dict, kart_id: int, page_url: str,
                       cat_by_name: Dict[str, dict]) -> dict:
    """productDetailModel → bizim DB product schema'mıza dönüştür."""
    name = (pdm.get("productName") or "").strip()
    stock_code = (pdm.get("stockCode") or "").strip()
    brand = (pdm.get("brandName") or "FACETTE").strip()
    description = (pdm.get("productShortDescription") or name).strip()

    bc = pdm.get("breadCrumb") or []
    # Ticimax breadCrumb leaf → root sırasında geliyor. İlk eleman en spesifik kategori.
    category_leaf = ""
    breadcrumb_str = ""
    if bc:
        category_leaf = (bc[0].get("tanim") or "").strip()
        names = [c.get("tanim") for c in reversed(bc) if c.get("tanim")]
        breadcrumb_str = " > ".join(names)

    cat_doc = cat_by_name.get(category_leaf.lower()) if category_leaf else None

    # Products array: her varyant (beden) için bir satır
    products_list = pdm.get("products") or []
    # productVariantData: Beden tanımı için kullanılır
    variant_data = pdm.get("productVariantData") or []
    # urunID → beden tanımı
    beden_map: Dict[int, str] = {}
    color_from_variant = None
    for v in variant_data:
        urun_id = v.get("urunID")
        tip = (v.get("ekSecenekTipiTanim") or "").lower()
        tanim = (v.get("tanim") or "").strip()
        if not urun_id or not tanim:
            continue
        if tip == "beden":
            beden_map[int(urun_id)] = tanim.upper()
        elif tip == "renk" and color_from_variant is None:
            color_from_variant = tanim

    color = color_from_variant or _detect_color_from_url(page_url) or ""

    # Fiyatlar: KDV dahil olarak hesaplayıp koyalım (storefront satisFiyati KDV hariç gelir).
    list_price_kdv_inc = 0.0
    sale_price_kdv_inc = 0.0
    cost_price = 0.0  # storefront'ta yok
    vat_rate = 10.0

    variants = []
    images_set: List[str] = []

    for p in products_list:
        urun_id = int(p.get("id") or 0)
        kdv_orani = float(p.get("kdvOrani") or 10.0)
        satis = float(p.get("satisFiyati") or 0.0)
        indirim = float(p.get("indirimliFiyati") or satis)
        satis_inc = round(satis * (1 + kdv_orani / 100.0), 2)
        indirim_inc = round(indirim * (1 + kdv_orani / 100.0), 2)
        list_price_kdv_inc = list_price_kdv_inc or satis_inc
        sale_price_kdv_inc = sale_price_kdv_inc or indirim_inc
        vat_rate = kdv_orani

        beden = beden_map.get(urun_id) or "STD"
        v_stock_code = (p.get("stokKodu") or stock_code or "").strip()
        v_barcode = (p.get("barkod") or "").strip()
        v_stock = int(p.get("stokAdedi") or 0)

        variants.append({
            "size": beden,
            "color": color.title(),
            "barcode": v_barcode,
            "stock_code": v_stock_code,
            "urun_id": str(urun_id),
            "stock": v_stock,
            "price": satis_inc,
            "sale_price": indirim_inc,
        })

    # Görseller
    for im in (pdm.get("productImages") or []):
        big = im.get("bigImagePath") or im.get("imagePath") or ""
        if big and big not in images_set:
            images_set.append(big)

    doc = {
        "name": name,
        "color": color.title(),
        "stock_code": stock_code,
        "sku": stock_code,
        "urun_karti_id": str(kart_id),
        "price": list_price_kdv_inc,
        "sale_price": sale_price_kdv_inc,
        "member_price_1": list_price_kdv_inc,
        "cost_price": cost_price,
        "vat_rate": vat_rate,
        "vendor": brand or "FACETTE",
        "description": description,
        "category_name": category_leaf,
        "breadcrumb": breadcrumb_str,
        "variants": variants,
        "images": images_set,
        "is_active": True,
        "is_published": True,
    }
    if cat_doc:
        doc["category_id"] = cat_doc.get("id")
        doc["category_name"] = cat_doc.get("name")
    return doc


async def main(kart_ids: List[int]):
    target_ids = set(int(k) for k in kart_ids)
    print(f"🎯 Hedef URUNKARTIID'ler: {sorted(target_ids)}")

    print("→ Sitemap taranıyor...")
    found = _find_target_urls(target_ids)
    print(f"  Bulunan URL'ler: {len(found)}/{len(target_ids)}")
    for kid, u in found.items():
        print(f"   • {kid}: {u}")

    missing = target_ids - set(found.keys())
    if missing:
        print(f"⚠️  Sitemap'te bulunamayan ID'ler: {sorted(missing)}")
        print("   (Bu ürünler ya yayında değil ya da URL suffix farklı.)")

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    sys_cats = await db.categories.find({}, {"_id": 0}).to_list(None)
    cat_by_name = {(c.get("name") or "").strip().lower(): c for c in sys_cats}

    stats = {"updated": 0, "created": 0, "errors": [], "variants": 0}

    for kid in sorted(found.keys()):
        url = found[kid]
        print(f"\n→ {kid}: {url}")
        try:
            r = requests.get(url, timeout=30, headers=HEADERS)
            r.raise_for_status()
        except Exception as e:
            stats["errors"].append(f"{kid}: fetch error: {e}")
            print(f"  ✖ fetch hatası: {e}")
            continue

        pdm = _extract_product_detail_model(r.text)
        if not pdm:
            stats["errors"].append(f"{kid}: productDetailModel bulunamadı")
            print("  ✖ productDetailModel bulunamadı")
            continue

        doc = _build_product_doc(pdm, kid, url, cat_by_name)
        print(f"  → {doc['name']} | renk={doc['color']} | stok_kodu={doc['stock_code']} | varyant={len(doc['variants'])}")

        existing = await db.products.find_one({"urun_karti_id": str(kid)})
        if existing:
            await db.products.update_one(
                {"id": existing["id"]},
                {"$set": {**doc, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            stats["updated"] += 1
            print(f"  ✏️  GÜNCELLENDİ (id={existing.get('id')})")
        else:
            doc.update({
                "id": str(uuid4()),
                "slug": _slugify(doc["name"]),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            await db.products.insert_one(doc)
            stats["created"] += 1
            print(f"  ➕ EKLENDİ (id={doc['id']})")
        stats["variants"] += len(doc["variants"])

    print("\n" + "=" * 60)
    print("📊 SONUÇ:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [2889, 2879, 2840, 2839]
    asyncio.run(main(ids))
