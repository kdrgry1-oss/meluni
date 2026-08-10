"""
TicimaxExport(2) 'KATEGORILER' kolonundan kategori ağacını kurar ve
ürünleri ilgili kategorilere atar.

KATEGORILER formatı: 'GİYİM;İNDİRİM;GİYİM>Dış Giyim;GİYİM>Dış Giyim>Trençkot;'
  - ';' ile ayrı yollar, '>' ile hiyerarşi.

- Eksik kategori düğümleri (name + parent) oluşturulur; mevcutlar yeniden kullanılır.
- Her ürüne:
    category_id   = en derin (yaprak) kategori
    category_name = o yaprağın adı
    category_ids  = ait olduğu tüm düğüm id'leri (atalar dahil; kırılım filtresi için)

Kullanım:
  python3 import_categories.py /tmp/ticimax2.xls            # DRY-RUN
  python3 import_categories.py /tmp/ticimax2.xls --apply
"""
import asyncio
import os
import sys
import re
import random
import argparse
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

NON = {"KARGO", "BANKA KOMİSYONU", "BANKA KOMISYONU", "ACIKLAMA", "AÇIKLAMA"}


def _bc(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


def slugify(text):
    tr = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
          'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}
    s = str(text or "").lower()
    for k, v in tr.items():
        s = s.replace(k, v)
    s = re.sub(r'[^a-z0-9\s>-]', '', s)
    s = re.sub(r'[\s_>]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-') or "kategori"


async def main(path, apply):
    df = pd.read_excel(path, engine="openpyxl")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # Mevcut kategoriler
    cats = await db.categories.find({}).to_list(2000)
    used_ids = set(c["id"] for c in cats)
    used_slugs = set(c.get("slug") for c in cats if c.get("slug"))
    # (name_lower, parent_id) -> cat
    by_np = {}
    for c in cats:
        by_np[(str(c.get("name", "")).strip().lower(), c.get("parent_id"))] = c

    created = []

    def new_id():
        for _ in range(500):
            i = str(random.randint(1000, 9999))
            if i not in used_ids:
                used_ids.add(i)
                return i
        raise RuntimeError("id tükendi")

    def uniq_slug(base):
        s = base
        n = 2
        while s in used_slugs:
            s = f"{base}-{n}"
            n += 1
        used_slugs.add(s)
        return s

    now = datetime.now(timezone.utc).isoformat()

    def resolve_path(parts):
        """Yolu yürüt; eksik düğümleri oluştur. Düğüm id zincirini döndür."""
        parent_id = None
        chain = []
        accum = []
        for name in parts:
            accum.append(name)
            key = (name.strip().lower(), parent_id)
            cat = by_np.get(key)
            if not cat:
                cid = new_id()
                cat = {
                    "id": cid,
                    "name": name.strip(),
                    "slug": uniq_slug(slugify("-".join(accum))),
                    "parent_id": parent_id,
                    "is_active": True,
                    "source": "ticimax_excel",
                    "sort_order": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                by_np[key] = cat
                created.append(cat)
            chain.append(cat["id"])
            parent_id = cat["id"]
        return chain  # son eleman yaprak

    # Kart -> kategori bilgisi
    card_cat = {}
    for _, r in df.iterrows():
        if str(r.get("URUNADI") or "").strip().upper() in NON:
            continue
        kid = _bc(r.get("URUNKARTIID"))
        if not kid or pd.isna(r.get("KATEGORILER")):
            continue
        if kid in card_cat:
            continue
        all_ids = set()
        leaves = []  # (depth, leaf_id, leaf_name)
        for raw in str(r.get("KATEGORILER")).split(";"):
            raw = raw.strip()
            if not raw:
                continue
            parts = [p.strip() for p in raw.split(">") if p.strip()]
            if not parts:
                continue
            chain = resolve_path(parts)
            all_ids.update(chain)
            leaf = by_np[(parts[-1].strip().lower(), (chain[-2] if len(chain) > 1 else None))]
            leaves.append((len(parts), leaf["id"], leaf["name"]))
        if not leaves:
            continue
        leaves.sort(key=lambda x: -x[0])  # en derin önce
        primary_id, primary_name = leaves[0][1], leaves[0][2]
        card_cat[kid] = {"category_id": primary_id, "category_name": primary_name,
                         "category_ids": sorted(all_ids)}

    print(f"Excel kart (kategorili): {len(card_cat)}")
    print(f"Oluşturulacak yeni kategori: {len(created)}")
    for c in created:
        print(f"  + {c['name']} (parent={c['parent_id']}) slug={c['slug']}")

    # Ürünlere uygula
    touched = 0
    async for p in db.products.find({"is_deleted": {"$ne": True}}, {"id": 1, "urun_karti_id": 1}):
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid not in card_cat:
            continue
        touched += 1
        if apply:
            await db.products.update_one({"id": p["id"]}, {"$set": {**card_cat[kid], "updated_at": now}})

    if apply and created:
        await db.categories.insert_many(created)

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}")
    print(f"  Yeni kategori: {len(created)} | Kategori atanan ürün: {touched}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.excel_path, args.apply))
