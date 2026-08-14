"""
backfill_category_ids.py — Ürünlere kategori ATA ZİNCİRİNİ yazar.

SORUN: Tedarikçi aktarımı ürüne yalnız `category_id` (alt kategori) yazıyordu.
Vitrin ise ÜST kategoriyi `category_ids` alanı üzerinden süzüyor (ata zinciri
orada tutulur). Alan yazılmadığı için "Kadın" ya da "Erkek" ana kategorisine
girildiğinde liste BOŞ dönüyordu; alt kategoriler çalışıyordu, çünkü onlar
kategori ADIna göre de eşleşiyor.

Aktarım kodu düzeltildi; bu betik ise ZATEN AKTARILMIŞ ürünleri, tam bir
yeniden aktarım beklemeden onarır (yeniden aktarım ~2,5 saat sürüyor).

Yazdığı alanlar:  category_ids (kök → alt), category_slug, category_root,
                  category_path
Dokunmadıkları:   fiyat, stok, ad, varyantlar — hiçbiri.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/backfill_category_ids.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/backfill_category_ids.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true",
                    help="Değişiklikleri YAZAR. Verilmezse yalnız rapor çıkarılır.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db  # noqa: E402

    # Kategori ağacını bir kez oku: id → (ad, üst id, slug)
    cats = {}
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1,
                                           "parent_id": 1, "slug": 1}):
        cats[c["id"]] = c

    def zincir(cid: str):
        """Alt kategoriden köke: [kök_id, …, cid]. Döngüye karşı korumalı."""
        out, gorulen = [], set()
        while cid and cid in cats and cid not in gorulen:
            gorulen.add(cid)
            out.append(cid)
            cid = cats[cid].get("parent_id")
        out.reverse()
        return out

    toplam = guncel = atlanan = kayip = 0
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "category_id": 1, "category_ids": 1}):
        toplam += 1
        cid = p.get("category_id")
        if not cid or cid not in cats:
            kayip += 1
            continue
        ids = zincir(cid)
        if p.get("category_ids") == ids:
            atlanan += 1
            continue
        alt = cats[cid]
        kok_id = ids[0] if ids else cid
        yeni = {
            "category_ids": ids,
            "category_slug": alt.get("slug") or "",
            "category_root": cats.get(kok_id, {}).get("name") or "",
            "category_path": " > ".join(cats[i].get("name") or "" for i in ids),
        }
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": yeni})
        guncel += 1

    print(f"toplam ürün        : {toplam}")
    print(f"{'güncellendi' if yaz else 'güncellenecek'}        : {guncel}")
    print(f"zaten doğru        : {atlanan}")
    print(f"kategorisi yok/bozuk: {kayip}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
