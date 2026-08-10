"""
Trendyol'da var olan ürünlerin attribute'larındaki SIZE bilgisini çekip
DB'ye geri yükler. Excel "Beden=renk" yanılgısı yüzünden bozulan size
verilerini onarmak için kullanılır.

Çalışma:
  - Trendyol'dan tüm seller products listesi
  - Her ürün için: response içindeki attributes'ten Beden/Size attribute'unu çek
  - barcode -> size eşleştirmesini DB'de variant.size'a geri yaz

Kullanım:
    python3 -m scripts.restore_sizes_from_trendyol --apply
"""
import asyncio
import os
import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trendyol_client import TrendyolClient  # noqa: E402


SIZE_ATTR_NAMES = {"beden", "boy", "size"}


def _extract_size(attributes):
    """Trendyol product detail attributes'ten beden değerini çıkar."""
    if not isinstance(attributes, list):
        return None
    for a in attributes:
        nm = (a.get("attributeName") or a.get("name") or "").lower().strip()
        if nm in SIZE_ATTR_NAMES:
            v = a.get("attributeValue") or a.get("value") or a.get("customAttributeValue")
            if v:
                return str(v).strip()
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--page-limit", type=int, default=30)
    args = parser.parse_args()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    cfg = await db.settings.find_one({"id": "trendyol"}, {"_id": 0})
    if not cfg or not cfg.get("api_key"):
        print("Trendyol config eksik."); return
    cli = TrendyolClient(cfg["supplier_id"], cfg["api_key"], cfg["api_secret"], cfg.get("mode", "live"))

    # Trendyol'dan tüm ürünleri pagelı çek
    barcode_to_size = {}
    page = 0
    while page < args.page_limit:
        try:
            res = await cli.get_filtered_products(page=page, size=200, archived=False)
        except Exception as e:
            print(f"Page {page} error: {e}")
            break
        content = res.get("content") or []
        total_pages = res.get("totalPages") or 0
        if not content:
            break
        for row in content:
            bc = str(row.get("barcode") or "")
            if not bc:
                continue
            sz = _extract_size(row.get("attributes") or [])
            if sz:
                barcode_to_size[bc] = sz
        page += 1
        if page >= total_pages:
            break

    print(f"Trendyol'dan {len(barcode_to_size)} barkod-beden eşleşmesi çekildi.")

    # DB'ye apply
    updated_products = 0
    updated_variants = 0
    async for p in db.products.find({}, {"_id": 0, "id": 1, "stock_code": 1, "variants": 1}):
        variants = p.get("variants") or []
        changes = []
        for i, v in enumerate(variants):
            bc = str(v.get("barcode") or "")
            if not bc:
                continue
            new_size = barcode_to_size.get(bc)
            if new_size and v.get("size") != new_size:
                changes.append((i, new_size))
        if changes:
            if args.apply:
                set_doc = {f"variants.{i}.size": s for i, s in changes}
                await db.products.update_one({"id": p["id"]}, {"$set": set_doc})
            updated_products += 1
            updated_variants += len(changes)

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {updated_products} ürün, {updated_variants} varyant boyut güncellendi.")


if __name__ == "__main__":
    asyncio.run(main())
