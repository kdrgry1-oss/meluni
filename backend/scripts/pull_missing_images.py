"""
Görseli olmayan ürünler için SADECE `images` alanını storefront'tan çeker.
Diğer alanlara (fiyat, açıklama, ticimax_fields, variants) DOKUNMAZ.

Kullanım:
    python3 pull_missing_images.py            # tüm görselsiz ürünler
    python3 pull_missing_images.py --apply    # DB'ye yaz
"""
import asyncio
import os
import sys
import argparse
from datetime import datetime, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv("/app/backend/.env")

from ticimax_pull_via_storefront import (  # noqa: E402
    _find_target_urls, _extract_product_detail_model, HEADERS,
)


async def main(apply: bool):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    q = {"$or": [{"images": {"$size": 0}}, {"images": {"$exists": False}}]}

    kid_to_products = {}
    async for p in db.products.find(q, {"id": 1, "urun_karti_id": 1, "name": 1}):
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid.isdigit():
            kid_to_products.setdefault(int(kid), []).append(p)

    target_ids = set(kid_to_products.keys())
    print(f"Görselsiz ürün (kart id'li): {sum(len(v) for v in kid_to_products.values())} | benzersiz kart: {len(target_ids)}")

    found = _find_target_urls(target_ids)
    print(f"Sitemap'te bulunan URL: {len(found)}/{len(target_ids)}")

    stats = {"updated": 0, "no_images": 0, "errors": 0, "not_in_sitemap": len(target_ids - set(found))}
    for kid, url in found.items():
        try:
            r = requests.get(url, timeout=30, headers=HEADERS)
            r.raise_for_status()
            pdm = _extract_product_detail_model(r.text)
        except Exception as e:
            stats["errors"] += 1
            print(f"  ✖ {kid}: {e}")
            continue
        if not pdm:
            stats["errors"] += 1
            continue
        images = []
        for im in (pdm.get("productImages") or []):
            big = im.get("bigImagePath") or im.get("imagePath") or ""
            if big and big not in images:
                images.append(big)
        if not images:
            stats["no_images"] += 1
            continue
        for p in kid_to_products[kid]:
            if apply:
                await db.products.update_one(
                    {"id": p["id"]},
                    {"$set": {"images": images, "updated_at": datetime.now(timezone.utc).isoformat()}},
                )
            stats["updated"] += 1
            print(f"  ✓ {kid} {p['name']}: {len(images)} görsel")

    print("\n=== SONUÇ ===")
    print(f"  {'APPLIED' if apply else 'DRY-RUN'}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
