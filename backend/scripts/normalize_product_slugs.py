"""Tüm ürün slug'larını `{urun-adi}-{urun_karti_id}` formatına getirir.
Örn: 'Noctia Pelerinli Ceket Mavi' (card_id 2752) -> noctia-pelerinli-ceket-mavi-2752

- card_id benzersiz olduğu için ÇAKIŞMA olmaz.
- Eski slug'lar `slug_aliases`'a eklenir → eskiden paylaşılan/indexlenen linkler kırılmaz.
- card_id olmayan ürünlerde (8 adet) iç id'den kısa fallback eklenir.
"""
import asyncio
import os
import re

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

TR = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
      'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}


def generate_slug(name: str) -> str:
    s = (name or "").lower()
    for a, b in TR.items():
        s = s.replace(a, b)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    products = await db.products.find({}, {"_id": 0}).to_list(2000)
    seen = {}
    changed = 0
    collisions = 0

    for p in products:
        name = p.get("name") or ""
        base = generate_slug(name) or "urun"
        card_id = p.get("urun_karti_id") or p.get("ticimax_card_id") or p.get("xml_id")
        if card_id:
            new_slug = f"{base}-{card_id}"
        else:
            new_slug = f"{base}-{str(p.get('id', ''))[:6]}"

        # benzersizlik güvencesi (card_id zaten unique ama yine de koru)
        if new_slug in seen:
            collisions += 1
            n = 2
            while f"{new_slug}-{n}" in seen:
                n += 1
            new_slug = f"{new_slug}-{n}"
        seen[new_slug] = p["id"]

        old_slug = p.get("slug")
        if old_slug == new_slug:
            continue

        aliases = set(p.get("slug_aliases") or [])
        if old_slug:
            aliases.add(old_slug)
        aliases.discard(new_slug)

        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {"slug": new_slug, "slug_aliases": sorted(aliases)}},
        )
        changed += 1

    print(f"products: {len(products)} | slugs updated: {changed} | forced-disambiguation: {collisions}")

    # doğrulama: benzersiz mi?
    all_slugs = await db.products.distinct("slug")
    total = await db.products.count_documents({})
    print(f"distinct slugs: {len(all_slugs)} | total products: {total} (eşit olmalı)")
    # örnek
    sample = await db.products.find_one({"name": {"$regex": "noctia", "$options": "i"}}, {"_id": 0, "name": 1, "slug": 1, "slug_aliases": 1})
    print("sample:", sample)


if __name__ == "__main__":
    asyncio.run(main())
