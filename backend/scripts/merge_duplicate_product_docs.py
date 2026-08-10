"""
Aynı (stock_code, color_norm) için birden çok DB doc'u varsa BIRLEŞTIR.

Strateji:
  1. (stock_code, color_norm) bazında grupla
  2. Her gruptan EN ZENGİN doc (en çok image, en yüksek price, en yüksek stock toplamı)
     ana doc olarak seçilir
  3. Diğer doc'ların variants[]'ı ana doc'a MERGE edilir:
     - Barkod aynıysa: variant'ı UPDATE et (size/urun_id/stock_code gelen + price/stock korunur)
     - Barkod farklıysa: yeni varyant olarak ekle
  4. Ana doc'un eksik alanları (price=0, list_price=0, images=[]) diğerlerinden DOLDURULUR
  5. Diğer (boş kalan) doc'lar silinir
"""
import asyncio
import os
import re
import argparse
import sys
from dotenv import load_dotenv

load_dotenv()
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _norm_color(c):
    if not c: return ""
    return re.sub(r"\s+", " ", str(c)).strip().lower()


def _doc_richness(d):
    """Doc'un zenginlik skoru — en yüksek seçilir."""
    imgs = len(d.get("images") or [])
    price = d.get("price") or 0
    list_p = d.get("list_price") or 0
    stocks = sum((v.get("stock") or 0) for v in (d.get("variants") or []))
    has_desc = 1 if (d.get("description") and len(str(d.get("description"))) > 30) else 0
    has_cat = 1 if d.get("category_id") else 0
    return (imgs * 1000) + (price > 0) * 500 + (list_p > 0) * 500 + (stocks > 0) * 300 + has_desc * 200 + has_cat * 100 + stocks


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    groups = {}  # (sc, color) -> [docs]
    async for p in db.products.find({}, {"_id": 0}):
        sc = p.get("stock_code") or ""
        color = _norm_color(p.get("color"))
        # color yoksa variant.color'dan al
        if not color:
            for v in (p.get("variants") or []):
                if v.get("color"):
                    color = _norm_color(v.get("color"))
                    break
        if not sc: continue
        groups.setdefault((sc, color), []).append(p)

    duplicates = [g for g in groups.values() if len(g) > 1]
    print(f"Toplam grup: {len(groups)} | Duplicate grup: {len(duplicates)}")

    merged = 0
    deleted = 0
    appended_variants = 0
    enriched_fields = 0

    for docs in duplicates:
        # En zengin → ana
        docs.sort(key=_doc_richness, reverse=True)
        main_doc = docs[0]
        others = docs[1:]

        main_variants = list(main_doc.get("variants") or [])
        main_v_bcs = {str(v.get("barcode") or ""): i for i, v in enumerate(main_variants) if v.get("barcode")}

        # Diğer doc'lardan varyant merge
        for od in others:
            for v in od.get("variants") or []:
                bc = str(v.get("barcode") or "")
                if not bc:
                    continue
                if bc in main_v_bcs:
                    # Aynı barkod — main_doc'taki varyantı eksik alanlarla doldur
                    idx = main_v_bcs[bc]
                    mv = main_variants[idx]
                    for field in ("size", "color", "urun_id", "stock_code", "sku"):
                        if not mv.get(field) and v.get(field):
                            mv[field] = v[field]
                    # Stock: max
                    if (v.get("stock") or 0) > (mv.get("stock") or 0):
                        mv["stock"] = v["stock"]
                else:
                    main_variants.append(v)
                    main_v_bcs[bc] = len(main_variants) - 1
                    appended_variants += 1

        # Parent-level merge: eksik alanları doldur
        set_doc = {"variants": main_variants}
        for field in ("price", "list_price", "sale_price", "cost_price", "member_price_1", "purchase_price"):
            main_val = main_doc.get(field)
            if not main_val or main_val == 0:
                for od in others:
                    val = od.get(field)
                    if val and val != 0:
                        set_doc[field] = val
                        enriched_fields += 1
                        break
        for field in ("description", "category_id", "category_name", "urun_karti_id"):
            if not main_doc.get(field):
                for od in others:
                    if od.get(field):
                        set_doc[field] = od[field]
                        enriched_fields += 1
                        break
        # images: main'inkini al, yoksa en uzun olanı al
        if not main_doc.get("images"):
            best_imgs = []
            for od in others:
                if (od.get("images") or []) and len(od["images"]) > len(best_imgs):
                    best_imgs = od["images"]
            if best_imgs:
                set_doc["images"] = best_imgs
                enriched_fields += 1
        # name: en uzun
        names = [main_doc.get("name")] + [od.get("name") for od in others]
        names = [n for n in names if n]
        if names:
            longest_name = max(names, key=len)
            if longest_name != main_doc.get("name"):
                set_doc["name"] = longest_name

        if args.apply:
            await db.products.update_one({"id": main_doc["id"]}, {"$set": set_doc})
            for od in others:
                await db.products.delete_one({"id": od["id"]})
        merged += 1
        deleted += len(others)

    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}:")
    print(f"  Merge edilen grup: {merged}")
    print(f"  Silinen duplicate doc: {deleted}")
    print(f"  Eklenen varyant (cross-doc): {appended_variants}")
    print(f"  Zenginleştirilen alan: {enriched_fields}")


if __name__ == "__main__":
    asyncio.run(main())
