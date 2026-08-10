"""Kategori temizleme — TANI + GÜVENLİ TEMİZLİK.

Tespit eder ve raporlar:
  1) Test/placeholder kategoriler (HB_CAT_TEST_123 vb.) + bağlı ürün sayısı.
  2) Duplike kategoriler (aynı isim VEYA aynı slug, farklı id) + her birinin ürün sayısı
     + önerilen "ana" kategori + önerilen 301 map'i (YALNIZCA RAPOR — birleştirme otomatik YAPILMAZ).
  3) Bozuk slug'lar (generate_slug(name) != mevcut slug).

Kullanım:
    DRY-RUN (hiçbir şey değişmez, sadece rapor):
        python -m scripts.clean_categories
    UYGULA (yalnızca GÜVENLİ işler: ürünsüz test kategorisini sil + bozuk slug düzelt):
        python -m scripts.clean_categories --apply

GÜVENLİK:
  - DRY-RUN varsayılan. --apply olmadan DB'ye HİÇBİR yazma yapılmaz.
  - --apply yalnızca: (a) hiç ürünü olmayan test/placeholder kategoriyi siler,
    (b) bozuk slug'ı düzeltir (eski slug 'slug_aliases'a eklenir → eski link korunur).
  - Duplike BİRLEŞTİRME (ürün taşıma + kategori silme) OTOMATİK YAPILMAZ — geri alınamaz ve
    ürünler çok alanlı bağlı (category_ids/category_slug/category_name). Sadece rapor üretilir;
    onay sonrası ayrı bir merge adımı çalıştırılmalıdır.
"""
import asyncio
import os
import re
import sys
from collections import defaultdict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Storefront ile aynı test/placeholder deseni (routes/categories.py visible_only ile uyumlu)
TEST_PAT = re.compile(r"hb_cat_test|cat_test|_test_|test_\d|^test[_\-]|[_\-]test$", re.I)

_TR = {"ı": "i", "İ": "i", "I": "i", "i": "i", "ş": "s", "Ş": "s",
       "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u"}


def gen_slug(name: str) -> str:
    """Frontend lib/slug.js ile birebir uyumlu Türkçe-duyarlı slug."""
    s = str(name or "")
    s = re.sub(r"[ıİIişŞçÇğĞöÖüÜ]", lambda m: _TR.get(m.group(0), m.group(0)), s)
    s = s.lower()
    s = re.sub(r"[\u0300-\u036f]", "", s)  # birleşik işaretleri temizle
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def is_test(c) -> bool:
    return any(TEST_PAT.search(str(c.get(f) or "")) for f in ("id", "slug", "name"))


def norm_name(c) -> str:
    return re.sub(r"\s+", " ", str(c.get("name") or "").strip().lower())


async def product_count(db, cat) -> int:
    """Kategoriye bağlı (çok alanlı) ürün sayısı."""
    cid, cslug, cname = cat.get("id"), cat.get("slug"), cat.get("name")
    ors = []
    if cid:
        ors.append({"category_ids": cid})
        ors.append({"category_id": cid})
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

    cats = [x async for x in db.categories.find({}, {"_id": 0})]
    print(f"Toplam kategori: {len(cats)}\n")

    # --- 1) TEST / PLACEHOLDER ---
    test_cats = [x for x in cats if is_test(x)]
    print(f"=== 1) TEST/PLACEHOLDER KATEGORİLER: {len(test_cats)} ===")
    deleted = 0
    for x in test_cats:
        n = await product_count(db, x)
        tag = "ürünsüz → silinebilir" if n == 0 else f"⚠ {n} ÜRÜN BAĞLI — silinmeyecek"
        print(f"   [{x.get('id')}] {x.get('name')!r}  slug={x.get('slug')!r}  ({tag})")
        if apply and n == 0:
            await db.categories.delete_one({"id": x["id"]})
            deleted += 1
    if apply:
        print(f"   → Silinen ürünsüz test kategorisi: {deleted}")

    # --- 2) DUPLİKE (aynı isim veya aynı slug) — YALNIZCA RAPOR ---
    by_name = defaultdict(list)
    by_slug = defaultdict(list)
    for x in cats:
        if is_test(x):
            continue
        if norm_name(x):
            by_name[norm_name(x)].append(x)
        if x.get("slug"):
            by_slug[x["slug"]].append(x)
    dup_groups = [v for v in by_name.values() if len(v) > 1] + \
                 [v for v in by_slug.values() if len(v) > 1]
    # tekilleştir (id setine göre)
    seen, uniq_groups = set(), []
    for g in dup_groups:
        key = tuple(sorted(p["id"] for p in g))
        if key not in seen:
            seen.add(key)
            uniq_groups.append(g)
    print(f"\n=== 2) DUPLİKE KATEGORİLER: {len(uniq_groups)} grup (RAPOR — birleştirme otomatik YAPILMAZ) ===")
    for g in uniq_groups:
        counts = [(x, await product_count(db, x)) for x in g]
        counts.sort(key=lambda t: (-t[1], t[0].get("sort_order") or 999, t[0].get("created_at") or ""))
        main_cat = counts[0][0]
        print(f"   • '{main_cat.get('name')}' grubu:")
        for x, n in counts:
            role = "ANA (öneri)" if x["id"] == main_cat["id"] else "→ birleştir"
            print(f"       [{x.get('id')}] slug={x.get('slug')!r} ürün={n}  {role}")
        merges = [x.get("slug") for x, _ in counts[1:] if x.get("slug")]
        if merges:
            print(f"       301 önerisi: {merges}  →  /{main_cat.get('slug')}")

    # --- 3) BOZUK SLUG ---
    print("\n=== 3) BOZUK SLUG'LAR (generate_slug(name) ile uyuşmayan) ===")
    fixed = 0
    bad = 0
    for x in cats:
        if is_test(x):
            continue
        want = gen_slug(x.get("name") or "")
        have = x.get("slug") or ""
        if want and have and want != have:
            bad += 1
            print(f"   [{x.get('id')}] {x.get('name')!r}  {have!r}  →  {want!r}")
            if apply:
                aliases = list(x.get("slug_aliases") or [])
                if have not in aliases:
                    aliases.append(have)
                await db.categories.update_one(
                    {"id": x["id"]},
                    {"$set": {"slug": want, "slug_aliases": aliases}},
                )
                fixed += 1
    if bad == 0:
        print("   (bozuk slug yok)")
    if apply:
        print(f"   → Düzeltilen slug: {fixed} (eski slug 'slug_aliases'a eklendi)")

    print("\nMod:", "UYGULANDI ✅ (test-sil + slug-fix)" if apply else "DRY-RUN (hiçbir şey değişmedi)")
    if not apply:
        print("Uygulamak için: python -m scripts.clean_categories --apply")
    print("Not: Duplike birleştirme (ürün taşıma) her iki modda da OTOMATİK YAPILMAZ — rapora göre onayla.")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
