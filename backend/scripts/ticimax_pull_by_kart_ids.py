"""
Ticimax'tan spesifik URUNKARTIID'lere sahip ürünleri çekip DB'ye işle.
Kullanım: python3 ticimax_pull_by_kart_ids.py 2889 2879 2840 2839
"""
import asyncio
import os
import sys
import re
from uuid import uuid4
from datetime import datetime, timezone
from typing import List
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")


def _slugify(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:200] or "urun"


def _f(x, default=0.0):
    try:
        return float(x) if x not in (None, "") else default
    except Exception:
        return default


def _s(x, default=""):
    if x is None: return default
    s = str(x).strip()
    return s if s and s.lower() != "none" else default


def _category_leaf(crumb: str) -> str:
    if not crumb: return ""
    parts = [p.strip() for p in crumb.split(">") if p.strip()]
    return parts[-1] if parts else ""


async def main(kart_ids: List[int]):
    import ticimax_client as tc

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    sys_cats = await db.categories.find({}, {"_id": 0}).to_list(None)
    cat_by_name = {(c.get("name") or "").strip().lower(): c for c in sys_cats}

    # Ticimax'tan TÜM ürünleri sayfalı çek, sonra Python'da filtre
    target_kart_ids = set(int(k) for k in kart_ids)
    print(f"Hedef URUNKARTIID'ler: {sorted(target_kart_ids)}")

    target_products = []  # bulunan ürünler

    page = 1
    while True:
        try:
            prods = tc.get_products(page=page, page_size=200, aktif=None)
        except Exception as e:
            print(f"get_products page={page} hata: {e}")
            break
        if not prods:
            break
        for p in prods:
            kart_id = p.get("UrunKartiID") or p.get("UrunKartiId")
            if kart_id is None: continue
            try:
                if int(kart_id) in target_kart_ids:
                    target_products.append(p)
            except Exception:
                pass
        print(f"  Sayfa {page}: {len(prods)} ürün incelendi (bulunan toplam: {len(target_products)})")
        if len(prods) < 200:
            break
        page += 1
        if page > 30:
            break

    if not target_products:
        print("❌ Hiçbir hedef ürün bulunamadı.")
        return

    print(f"\n✅ {len(target_products)} hedef ürün bulundu, varyant ve görseller çekiliyor...")

    stats = {"updated": 0, "created": 0, "variants": 0, "errors": []}

    for p in target_products:
        kart_id = int(p.get("UrunKartiID") or p.get("UrunKartiId"))
        urun_id = _s(p.get("UrunID") or p.get("UrunId"))
        urun_adi = _s(p.get("UrunAdi"))
        stok_kodu = _s(p.get("StokKodu") or p.get("UrunKodu"))
        barkod = _s(p.get("Barkod"))
        list_price = _f(p.get("SatisFiyati"))
        sale_price = _f(p.get("IndirimliFiyat")) or list_price
        cost_price = _f(p.get("AlisFiyati"))
        kdv = _f(p.get("KdvOrani"), 10)
        renk = _s(p.get("Renk"))
        beden = _s(p.get("Beden")) or "STD"
        breadcrumb = _s(p.get("KategoriYolu") or p.get("BreadCrumb"))
        category_leaf = _category_leaf(breadcrumb)
        cat_doc = cat_by_name.get(category_leaf.lower()) if category_leaf else None
        description = _s(p.get("Aciklama") or p.get("UrunAciklamasi"))
        vendor = _s(p.get("Tedarikci") or "FACETTE")
        # Üye fiyatı (eğer varsa)
        member_price = list_price
        try:
            ufiyat_list = p.get("UrunUyeTipiFiyat") or []
            if hasattr(ufiyat_list, "UrunUyeTipiFiyat"):
                ufiyat_list = ufiyat_list.UrunUyeTipiFiyat
            for uf in (ufiyat_list or []):
                if (uf.get("UyeTipiID") or uf.get("UyeTipi")) in (1, "1"):
                    member_price = _f(uf.get("Fiyat") or uf.get("UyeFiyati")) or list_price
                    break
        except Exception:
            pass

        # Varyantlar
        try:
            variants_raw = __import__("ticimax_client").get_variants(kart_id)
        except Exception as e:
            variants_raw = []
            stats["errors"].append(f"varyant {kart_id}: {e}")

        variants = []
        if variants_raw:
            for v in variants_raw:
                variants.append({
                    "size": _s(v.get("Beden") or v.get("Ozellik3")).upper() or beden,
                    "color": _s(v.get("Renk") or v.get("Ozellik1") or renk).title(),
                    "barcode": _s(v.get("Barkod")),
                    "stock_code": _s(v.get("StokKodu") or v.get("UrunKodu")) or stok_kodu,
                    "urun_id": _s(v.get("UrunID") or v.get("UrunId")) or urun_id,
                    "stock": int(_f(v.get("StokAdedi") or v.get("Stok"), 5) or 5),
                    "price": _f(v.get("SatisFiyati") or list_price),
                    "sale_price": _f(v.get("IndirimliFiyat") or sale_price),
                })
        else:
            variants.append({
                "size": beden, "color": renk.title(), "barcode": barkod,
                "stock_code": stok_kodu, "urun_id": urun_id,
                "stock": int(_f(p.get("StokAdedi"), 5) or 5),
                "price": list_price, "sale_price": sale_price,
            })
        stats["variants"] += len(variants)

        # Görseller
        try:
            imgs_raw = __import__("ticimax_client").get_product_images(kart_id)
        except Exception as e:
            imgs_raw = []
            stats["errors"].append(f"images {kart_id}: {e}")
        images = []
        for im in imgs_raw:
            url = _s(im.get("ResimUrl") or im.get("Url") or im.get("ResimYolu"))
            if url:
                images.append(url)

        # DB güncelle veya ekle
        existing = await db.products.find_one({"urun_karti_id": str(kart_id)})

        doc = {
            "name": urun_adi,
            "color": renk.title(),
            "stock_code": stok_kodu,
            "sku": stok_kodu,
            "urun_karti_id": str(kart_id),
            "price": list_price,
            "sale_price": sale_price,
            "member_price_1": member_price,
            "cost_price": cost_price,
            "vat_rate": kdv,
            "vendor": vendor,
            "description": description,
            "category_name": category_leaf,
            "breadcrumb": breadcrumb,
            "variants": variants,
        }
        if images:
            doc["images"] = images
        if cat_doc:
            doc["category_id"] = cat_doc.get("id")
            doc["category_name"] = cat_doc.get("name")

        if existing:
            await db.products.update_one({"id": existing["id"]}, {"$set": doc})
            stats["updated"] += 1
            print(f"  ✏️  Güncellendi: kart={kart_id} - {urun_adi[:60]}")
        else:
            doc.update({
                "id": str(uuid4()),
                "slug": _slugify(urun_adi),
                "is_active": True,
                "is_published": True,
                "images": doc.get("images", []),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            await db.products.insert_one(doc)
            stats["created"] += 1
            print(f"  ➕ Eklendi: kart={kart_id} - {urun_adi[:60]}")

    print("\n📊 SONUÇ:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [2889, 2879, 2840, 2839]
    asyncio.run(main(ids))
