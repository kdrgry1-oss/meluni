"""
sync_attribute_library.py — Katalogdaki renk/beden/özellik değerlerini
sistemin özellik kütüphanesine (db.attributes) işler.

NEDEN: Panelde "Sistemde tanımlı 0 özellik var" yazıyordu; kategori
eşleştirme ekranı yerel değerleri buradan okuyup Trendyol değerlerine
bağlıyor, kütüphane boşken hiçbir eşleştirme yapılamıyor. Mevcut "Ürünlerden
Yükle" işlevi özellikleri yalnız `type` alanından okuyordu (bizimkiler `name`
taşıyor) ve VARYANTLARA hiç bakmıyordu — oysa renk ile beden yalnız varyantta
duruyor.

Üç kaynak taranır: ürünün `attributes` listesi, varyantların renk/bedeni ve
ürün düzeyindeki `color`. Renkler tek biçime getirilir (SİYAH → Siyah), aksi
hâlde aynı renk kütüphaneye üç yazımla giriyor.

Mevcut değerler SİLİNMEZ; yalnız eksik olanlar eklenir.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/sync_attribute_library.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/sync_attribute_library.py --uygula
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Kütüphaneye YAZAR.")
    ap.add_argument("--liste", type=int, default=12, help="Kaç özellik yazılsın.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db, generate_id                       # noqa: E402
    from routes.attributes import urunlerden_ozellik_haritasi     # noqa: E402

    harita = await urunlerden_ozellik_haritasi()
    if not harita:
        print("Katalogda özellik değeri bulunamadı.")
        return 1

    yeni = guncel = 0
    for ad in sorted(harita, key=lambda k: -len(harita[k]))[:args.liste]:
        degerler = sorted(harita[ad])
        print(f"  {ad:<22} {len(degerler):>4} değer   "
              f"{', '.join(degerler[:6])}{' …' if len(degerler) > 6 else ''}")

    for ad, kume in harita.items():
        mevcut = await db.attributes.find_one(
            {"name": {"$regex": f"^{ad}$", "$options": "i"}})
        if mevcut:
            eski = set(mevcut.get("values") or [])
            birlesik = eski | kume
            if len(birlesik) > len(eski):
                guncel += 1
                if args.uygula:
                    await db.attributes.update_one(
                        {"_id": mevcut["_id"]},
                        {"$set": {"values": sorted(birlesik),
                                  "updated_at": datetime.now(timezone.utc).isoformat()}})
        else:
            yeni += 1
            if args.uygula:
                await db.attributes.insert_one({
                    "id": generate_id(), "name": ad, "values": sorted(kume),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()})

    print()
    print(f"katalogdaki özellik türü : {len(harita)}")
    print(f"toplam değer             : {sum(len(v) for v in harita.values())}")
    print(f"{'kütüphaneye eklendi' if args.uygula else 'kütüphaneye eklenecek'}      : {yeni} yeni özellik")
    print(f"{'değer eklendi' if args.uygula else 'değer eklenecek'}            : {guncel} mevcut özelliğe")
    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    else:
        print("\nMevcut değerler silinmedi; yalnız eksikler eklendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
