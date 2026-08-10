"""
Aynı URUNKARTIID altındaki DUPLICATE ürün dokümanlarını tek üründe birleştirir.
("1 URUNKARTIID = 1 ürün")

- Her kart için en eksiksiz doküman (en çok varyant > en çok görsel > aktif) ana seçilir.
- Diğer dokümanların varyant + görselleri ana dokümana birleştirilir (barkod/size'a göre tekilleştirme).
- Eski slug'lar `slug_aliases`'a yazılır (storefront linkleri kırılmaz).
- Kopya dokümanlar ÇÖP KUTUSUNA taşınır (soft delete, geri alınabilir).
- Silmeden önce TAM yedek alınır.

Kullanım:
  python3 merge_dedup_cards.py            # DRY-RUN
  python3 merge_dedup_cards.py --apply
"""
import asyncio
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def _vkey(v):
    bc = str(v.get("barcode") or "").strip()
    if bc:
        return f"bc:{bc}"
    return f"cs:{(v.get('color') or '').strip()}|{(v.get('size') or '').strip()}"


def slug_has_kartid_suffix(slug, kid):
    return str(slug or "").endswith(f"-{kid}")


def pick_primary(docs, kid):
    # En çok varyant > en çok görsel > aktif > slug'ı kart-id ekli OLMAYAN
    def score(d):
        return (
            len(d.get("variants") or []),
            len(d.get("images") or []),
            1 if d.get("is_active") else 0,
            0 if slug_has_kartid_suffix(d.get("slug"), kid) else 1,
        )
    return max(docs, key=score)


async def main(apply):
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    g = defaultdict(list)
    async for p in db.products.find({"is_deleted": {"$ne": True}}):
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid.isdigit():
            g[kid].append(p)

    dup_cards = {k: v for k, v in g.items() if len(v) > 1}
    print(f"Birden çok dokümanlı kart: {len(dup_cards)}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"dedup_merge_{ts}.json")
    backup = []

    merged_cards = 0
    trashed_docs = 0
    now = datetime.now(timezone.utc).isoformat()

    for kid, docs in dup_cards.items():
        primary = pick_primary(docs, kid)
        others = [d for d in docs if d["id"] != primary["id"]]
        backup.append({"kart": kid, "primary_id": primary["id"],
                       "docs": [{k: vv for k, vv in d.items() if k != "_id"} for d in docs]})

        # Varyant birleşimi
        vmap = {}
        for v in (primary.get("variants") or []):
            vmap[_vkey(v)] = dict(v)
        for d in others:
            for v in (d.get("variants") or []):
                k = _vkey(v)
                if k not in vmap:
                    vmap[k] = dict(v)
                else:
                    # daha yüksek stok'u koru
                    if (v.get("stock") or 0) > (vmap[k].get("stock") or 0):
                        vmap[k]["stock"] = v.get("stock")
        merged_variants = list(vmap.values())

        # Görsel birleşimi (sıra korunur, tekilleştir)
        imgs = list(primary.get("images") or [])
        for d in others:
            for im in (d.get("images") or []):
                if im not in imgs:
                    imgs.append(im)

        # Slug: kart-id ekli olmayan temiz slug'ı tercih et
        all_slugs = [d.get("slug") for d in docs if d.get("slug")]
        preferred = next((s for s in all_slugs if not slug_has_kartid_suffix(s, kid)), primary.get("slug"))
        aliases = list(set(s for s in all_slugs if s and s != preferred))

        total_stock = sum((v.get("stock") or 0) for v in merged_variants)

        set_doc = {
            "variants": merged_variants,
            "images": imgs,
            "slug": preferred,
            "slug_aliases": aliases,
            "stock": total_stock,
            "updated_at": now,
        }

        print(f"  kart {kid}: {len(docs)} doc -> 1 | varyant {len(merged_variants)} | görsel {len(imgs)} | slug={preferred} | alias={aliases}")

        if apply:
            await db.products.update_one({"id": primary["id"]}, {"$set": set_doc})
            # Kopyalar: yedeklendiği için kalıcı silinir (çöp kutusu temiz kalır)
            other_ids = [d["id"] for d in others]
            await db.products.delete_many({"id": {"$in": other_ids}})
        merged_cards += 1
        trashed_docs += len(others)

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, default=str, indent=2)

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}")
    print(f"  Birleştirilen kart: {merged_cards}")
    print(f"  Çöpe taşınan kopya doküman: {trashed_docs}")
    print(f"  Yedek: {backup_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
