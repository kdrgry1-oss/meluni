"""
normalize_slugs_v2.py — TÜM ürün slug'larını `{urun-adi}-{kart_id}` biçimine getirir.

Örn: "Noctia Pelerinli Ceket Mavi" (csv_card_id 2752) -> noctia-pelerinli-ceket-mavi-2752

- Kart id alanı olarak DOĞRU alan kullanılır: csv_card_id (yoksa urun_karti_id, yoksa iç id[:6]).
- Eski slug, slug_aliases'a eklenir -> eskiden paylaşılan/indexlenen linkler KIRILMAZ
  (ürün getirme zaten {slug} ve {slug_aliases} üzerinden eşleştiriyor).
- Aynı slug'a düşen olursa (renk varyantları aynı isimdeyse) sonuna -2, -3 eklenir.

GÜVENLİ KULLANIM:
  1) Önce KURU çalıştır (hiçbir şeyi değiştirmez, sadece raporlar):
        python scripts/normalize_slugs_v2.py
  2) Çıktıyı kontrol et. İyi görünüyorsa UYGULA:
        python scripts/normalize_slugs_v2.py --apply

Gereken ortam değişkenleri: MONGO_URL, DB_NAME (backend ile aynı .env).
ÖNERİ: Uygulamadan önce DB yedeği al (mongodump).
"""
import asyncio
import os
import re
import sys

from motor.motor_asyncio import AsyncIOMotorClient
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

APPLY = "--apply" in sys.argv

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


def card_of(p):
    return p.get("csv_card_id") or p.get("urun_karti_id") or p.get("ticimax_card_id") or str(p.get("id", ""))[:6]


async def main():
    mongo = os.environ.get("MONGO_URL")
    dbname = os.environ.get("DB_NAME")
    if not mongo or not dbname:
        print("HATA: MONGO_URL ve DB_NAME ortam değişkenleri gerekli.")
        sys.exit(1)

    db = AsyncIOMotorClient(mongo)[dbname]
    products = await db.products.find({}, {"_id": 0, "id": 1, "name": 1, "slug": 1,
                                           "slug_aliases": 1, "csv_card_id": 1,
                                           "urun_karti_id": 1, "ticimax_card_id": 1}).to_list(5000)

    print(f"Toplam ürün: {len(products)}")
    print(f"Mod: {'UYGULA (yazacak)' if APPLY else 'KURU ÇALIŞMA (hiçbir şey değişmez)'}")
    print("-" * 60)

    seen = {}
    changes = []
    for p in products:
        base = generate_slug(p.get("name")) or "urun"
        cid = str(card_of(p)).strip()
        new_slug = f"{base}-{cid}" if cid else base
        # Çakışma güvencesi
        if new_slug in seen and seen[new_slug] != p["id"]:
            n = 2
            while f"{new_slug}-{n}" in seen:
                n += 1
            new_slug = f"{new_slug}-{n}"
        seen[new_slug] = p["id"]

        old_slug = p.get("slug")
        if old_slug == new_slug:
            continue
        changes.append((p["id"], old_slug, new_slug, p.get("slug_aliases") or []))

    print(f"Değişecek ürün sayısı: {len(changes)}")
    for pid, old, new, _ in changes[:25]:
        print(f"  {old}  ->  {new}")
    if len(changes) > 25:
        print(f"  ... ve {len(changes) - 25} ürün daha")

    if not APPLY:
        print("-" * 60)
        print("KURU ÇALIŞMA bitti. Uygulamak için tekrar --apply ile çalıştır.")
        return

    print("-" * 60)
    print("Uygulanıyor...")
    updated = 0
    for pid, old, new, aliases in changes:
        new_aliases = list(aliases)
        if old and old not in new_aliases:
            new_aliases.append(old)  # eski link kırılmasın
        await db.products.update_one(
            {"id": pid},
            {"$set": {"slug": new, "slug_aliases": new_aliases}},
        )
        updated += 1
    print(f"BİTTİ. {updated} ürünün slug'ı güncellendi (eski slug'lar alias olarak saklandı).")


if __name__ == "__main__":
    asyncio.run(main())
