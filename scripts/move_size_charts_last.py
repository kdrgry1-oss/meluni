"""
move_size_charts_last.py — Ölçü tablosu görsellerini her üründe SONA taşır.

Yönetim isteği: beden tablosu her üründe en sonda görünsün. Vitrin galeriyi
sıraya göre gösteriyor; tablo aralarda kalınca müşteri ürün fotoğrafı beklerken
tabloyla karşılaşıyor.

Yeni üretilen tablolar zaten sona konuyor (routes/ai_product_images.py). Bu
betik MEVCUT kataloğu düzeltir.

Tablo görseli iki işaretten biriyle tanınır:
  • sözlük biçimindeki görsellerde `is_size_table`
  • bizim ürettiğimiz dosya adı (`...-olcu3-ab12cd34.jpg`)
Tedarikçiden gelen HAM tablo görselini tanıyamayız — o, yönetim Türkçeleştirene
kadar sıradan bir görsel gibi durur. Uydurma tahmin yapmıyoruz.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/move_size_charts_last.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/move_size_charts_last.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Sıralamayı YAZAR.")
    ap.add_argument("--liste", type=int, default=15, help="Kaç örnek yazılsın.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402
    from routes import ai_product_images as ai       # noqa: E402

    taranan = tablolu = duzelen = 0
    ornekler = []
    async for p in db.products.find({"is_deleted": {"$ne": True}},
                                    {"_id": 0, "id": 1, "name": 1, "images": 1}):
        taranan += 1
        images = [x for x in (p.get("images") or []) if ai._gorsel_url(x)]
        if not any(ai._olcu_gorseli_mi(x) for x in images):
            continue
        tablolu += 1
        yeni = ai._olcu_sona(images)
        if [ai._gorsel_url(x) for x in yeni] == [ai._gorsel_url(x) for x in images]:
            continue                                  # zaten sonda
        duzelen += 1
        if len(ornekler) < args.liste:
            ornekler.append((p.get("name", "")[:50], len(images)))
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": {"images": yeni}})

    for ad, adet in ornekler:
        print(f"  {ad:<50} {adet} görsel")
    if duzelen > len(ornekler):
        print(f"  … ve {duzelen - len(ornekler)} ürün daha")
    print()
    print(f"taranan ürün           : {taranan}")
    print(f"ölçü tablosu olan      : {tablolu}")
    print(f"{'sırası düzeltildi' if yaz else 'sırası düzeltilecek'}      : {duzelen}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
