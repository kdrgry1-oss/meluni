"""Duplike kategori BİRLEŞTİRME — clean_categories.py raporunun uygulama adımı.

GÜVENLİK MODELİ:
  - DRY-RUN varsayılan. --apply olmadan DB'ye HİÇBİR yazma yapılmaz.
  - Yalnızca GÜVENLİ duplike'ler birleştirilir: aynı normalize isim VE aynı parent_id.
    Farklı parent'taki aynı isimli kategoriler (ör. iki ayrı "Şort") BİRLEŞTİRİLMEZ — atlanır + uyarılır.
  - --apply her grup için önce GERİ-DÖNÜŞ LOG'u yazar (/tmp/category_merge_backup_<ts>.json):
    etkilenen her ürünün eski category_ids/category_slug/category_name değeri saklanır.
  - "ana" kategori = en çok ürünlü > küçük sort_order > eski created_at.
  - Birleştirme: duplike'nin ürünleri ana'ya taşınır (category_ids: dup→ana; slug/name güncellenir),
    duplike slug ana'nın slug_aliases'ına eklenir (eski link korunur: get_category + products slug
    çevirisi slug_aliases'ı arar), duplike kategori SİLİNİR (geri alınamaz — log ile geri dönülebilir).

Kullanım:
    DRY-RUN (plan, hiçbir şey değişmez):
        python -m scripts.merge_duplicate_categories
    UYGULA:
        python -m scripts.merge_duplicate_categories --apply

ÖNERİLEN AKIŞ: önce `python -m scripts.clean_categories` (rapor) → bu script DRY-RUN → --apply.
"""
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

TEST_PAT = re.compile(r"hb_cat_test|cat_test|_test_|test_\d|^test[_\-]|[_\-]test$", re.I)
_TR = {"ı": "i", "İ": "i", "I": "i", "i": "i", "ş": "s", "Ş": "s",
       "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u"}


def is_test(c) -> bool:
    return any(TEST_PAT.search(str(c.get(f) or "")) for f in ("id", "slug", "name"))


def norm_name(c) -> str:
    return re.sub(r"\s+", " ", str(c.get("name") or "").strip().lower())


async def product_count(db, cat) -> int:
    cid, cslug, cname = cat.get("id"), cat.get("slug"), cat.get("name")
    ors = []
    if cid:
        ors += [{"category_ids": cid}, {"category_id": cid}]
    if cslug:
        ors.append({"category_slug": cslug})
    if cname:
        ors.append({"category_name": cname})
    if not ors:
        return 0
    return await db.products.count_documents({"$and": [{"is_deleted": {"$ne": True}}, {"$or": ors}]})


async def main(apply: bool):
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    cats = [x async for x in db.categories.find({}, {"_id": 0}) if not is_test(x)]

    # Aynı normalize isim + aynı parent_id → güvenli grup
    groups = defaultdict(list)
    for x in cats:
        if norm_name(x):
            groups[(norm_name(x), x.get("parent_id"))].append(x)
    safe = {k: v for k, v in groups.items() if len(v) > 1}

    # Aynı isim ama FARKLI parent → riskli (atlanır, uyarılır)
    by_name_only = defaultdict(set)
    for (nm, par), v in groups.items():
        by_name_only[nm].add(par)
    risky_names = [nm for nm, pars in by_name_only.items() if len(pars) > 1]

    print(f"Toplam (test hariç) kategori: {len(cats)}")
    print(f"Güvenli birleştirilebilir grup (aynı isim + aynı parent): {len(safe)}")
    if risky_names:
        print(f"⚠ ATLANAN — aynı isim FARKLI parent ({len(risky_names)}): {risky_names}")
        print("   (Bunlar otomatik birleştirilmez; gerçekten aynıysalar admin'den elle birleştir.)\n")

    backup = {"ts": datetime.now(timezone.utc).isoformat(), "merges": []}
    total_moved = total_deleted = 0

    for (nm, par), items in safe.items():
        counts = [(x, await product_count(db, x)) for x in items]
        counts.sort(key=lambda t: (-t[1], t[0].get("sort_order") or 999, t[0].get("created_at") or ""))
        main_cat = counts[0][0]
        ana_id, ana_slug, ana_name = main_cat.get("id"), main_cat.get("slug"), main_cat.get("name")
        dups = [x for x, _ in counts[1:]]

        print(f"• '{ana_name}' (parent={par}) → ANA [{ana_id}] slug={ana_slug!r} ürün={counts[0][1]}")
        new_aliases = list(main_cat.get("slug_aliases") or [])

        for dup in dups:
            dup_id, dup_slug, dup_name = dup.get("id"), dup.get("slug"), dup.get("name")
            q = {"$or": [{"category_ids": dup_id}, {"category_slug": dup_slug}, {"category_name": dup_name}]}
            affected = [p async for p in db.products.find(q, {"_id": 0, "id": 1, "category_ids": 1,
                                                              "category_slug": 1, "category_name": 1})]
            print(f"    → birleştir [{dup_id}] slug={dup_slug!r}  ({len(affected)} ürün taşınacak)")

            if apply:
                backup["merges"].append({
                    "ana_id": ana_id, "dup_id": dup_id, "dup_slug": dup_slug,
                    "products": affected,
                })
                for p in affected:
                    ids = [ana_id if x == dup_id else x for x in (p.get("category_ids") or [])]
                    ids = list(dict.fromkeys(ids))  # dedup, sıra korunur
                    set_doc = {"category_ids": ids}
                    if (p.get("category_slug") or "") == dup_slug:
                        set_doc["category_slug"] = ana_slug
                    if (p.get("category_name") or "") == dup_name:
                        set_doc["category_name"] = ana_name
                    await db.products.update_one({"id": p["id"]}, {"$set": set_doc})
                    total_moved += 1
                # 301/alias: eski slug ana'da yaşasın (get_category + products slug çevirisi arar)
                if dup_slug and dup_slug not in new_aliases:
                    new_aliases.append(dup_slug)
                await db.categories.delete_one({"id": dup_id})
                total_deleted += 1

        if apply and new_aliases != list(main_cat.get("slug_aliases") or []):
            await db.categories.update_one({"id": ana_id}, {"$set": {"slug_aliases": new_aliases}})

    if apply:
        path = f"/tmp/category_merge_backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print(f"\nUYGULANDI ✅  taşınan ürün={total_moved}  silinen duplike={total_deleted}")
        print(f"Geri-dönüş log'u: {path}")
    else:
        print("\nDRY-RUN (hiçbir şey değişmedi). Uygula: python -m scripts.merge_duplicate_categories --apply")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
