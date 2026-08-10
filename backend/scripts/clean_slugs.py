"""Slug temizleme: benzersiz isimli ürünlerde sondaki kart-no'yu kaldırır
(temiz SEO slug), aynı isimli (ayrı kart) ürünlerde numarayı korur (çakışma olmasın).
Eski slug `slug_aliases`'a eklenir → eski linkler canonical redirect ile çalışmaya devam eder.

Kullanım:
    DRY-RUN: python -m scripts.clean_slugs
    UYGULA : python -m scripts.clean_slugs --apply
"""
import asyncio
import os
import re
import sys
from collections import defaultdict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def base_slug(slug: str) -> str:
    return re.sub(r"-\d+$", "", slug or "")


async def main(apply: bool):
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    products = []
    async for p in db.products.find({}, {"id": 1, "slug": 1, "name": 1, "slug_aliases": 1}):
        products.append(p)

    groups = defaultdict(list)
    for p in products:
        groups[base_slug(p.get("slug") or "")].append(p)

    changed = kept = 0
    samples = []
    for base, items in groups.items():
        unique = len(items) == 1
        for p in items:
            old = p.get("slug") or ""
            new = base if unique else old  # benzersiz → numarasız; çakışan → numara kalsın
            if new and new != old:
                changed += 1
                if len(samples) < 12:
                    samples.append((old, new))
                if apply:
                    aliases = list(p.get("slug_aliases") or [])
                    if old and old not in aliases:
                        aliases.append(old)
                    await db.products.update_one(
                        {"id": p["id"]},
                        {"$set": {"slug": new, "slug_aliases": aliases}},
                    )
            else:
                kept += 1

    print(f"Toplam ürün       : {len(products)}")
    print(f"Slug temizlenen   : {changed} (numara kaldırıldı, eski slug alias'a eklendi)")
    print(f"Numara korunan    : {kept} (aynı isimli ayrı ürünler — çakışma önlendi)")
    print("\nÖrnekler (eski → yeni):")
    for o, n in samples:
        print(f"   {o}  →  {n}")
    print("\nMod:", "UYGULANDI ✅" if apply else "DRY-RUN")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
