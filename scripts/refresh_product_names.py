"""
refresh_product_names.py — Ürün adlarını ham addan yeniden üretir.

Ad kısaltma kuralı geliştiğinde tüm katalogu yeniden aktarmak gerekmiyor:
tedarikçinin KISALTILMAMIŞ adı her üründe `supplier_original_name` alanında
duruyor. Bu betik o alandan normalize_name'i yeniden çalıştırır.

Tam aktarım ~2,5 saat sürüyor ve besleme indirmesi gerektiriyor; bu birkaç
dakikada biter ve yalnız `name` alanına dokunur — fiyat, stok, kategori,
varyantlar ve tedarikçi kodları olduğu gibi kalır.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/refresh_product_names.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/refresh_product_names.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    ap.add_argument("--ornek", type=int, default=15, help="Raporda gösterilecek örnek sayısı.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402
    from routes.supplier_xml import normalize_name   # noqa: E402

    toplam = degisen = ayni = hamsiz = 0
    ornekler = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}, "supplier_original_name": {"$exists": True}},
            {"_id": 0, "id": 1, "name": 1, "supplier_original_name": 1}):
        toplam += 1
        ham = (p.get("supplier_original_name") or "").strip()
        if not ham:
            hamsiz += 1
            continue
        yeni = normalize_name(ham)
        # Boş sonuç ürünü adsız bırakır — asla yazma.
        if not yeni or yeni == (p.get("name") or ""):
            ayni += 1
            continue
        if len(ornekler) < args.ornek:
            ornekler.append((p.get("name"), yeni))
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": {"name": yeni}})
        degisen += 1

    for eski, yeni in ornekler:
        print(f"  eski : {eski}")
        print(f"  yeni : {yeni}\n")
    print(f"ham adı olan ürün : {toplam}")
    print(f"{'değişti' if yaz else 'değişecek'}          : {degisen}")
    print(f"zaten aynı        : {ayni}")
    print(f"ham adı boş       : {hamsiz}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
