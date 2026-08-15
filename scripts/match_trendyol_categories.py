"""
match_trendyol_categories.py — Yerel kategorileri Trendyol'a otomatik eşler.

NEDEN: Ürün kartındaki Trendyol özellik şeması, yerel kategorideki
`trendyol_category_id` ile çekiliyor. Alan boşken panel "özellik kütüphanesi
boş" diyor, zorunlu alanlar dolmuyor ve ürün pazaryerinde reddediliyor.

Eşleştirme adlardan puanlanır (bkz. backend/trendyol_category_match.py):
cinsiyet kapısı zorunlu, yalnız YAPRAK kategori seçilir, eşiğin altındaki
eşleşme YAZILMAZ — "elle seçilecek" diye listelenir. Yanlış kategori, eksik
kategoriden pahalıdır.

Önce Trendyol kategori ağacı senkronlanmış olmalı (panel > Trendyol >
Kategoriler > Senkronize et, ya da POST /integrations/trendyol/categories/sync).

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/match_trendyol_categories.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/match_trendyol_categories.py --uygula
    ... --esik 60          # daha gevşek eşleştir (varsayılan 70)
    ... --uygula --ez      # elle yapılmış eşleşmeleri de yeniden hesapla
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Eşleşmeleri YAZAR.")
    ap.add_argument("--esik", type=int, default=70, help="Kabul puanı (0-100).")
    ap.add_argument("--ez", action="store_true",
                    help="Zaten eşleşmiş kategorileri de yeniden hesaplar.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402
    import trendyol_category_match as tcm            # noqa: E402

    agac = await db.trendyol_categories.find({}, {"_id": 0}).to_list(5000)
    if not agac:
        print("Trendyol kategori ağacı BOŞ. Önce kategori senkronunu çalıştırın:")
        print("  panel > Pazaryerleri > Trendyol > Kategoriler > Senkronize et")
        return 2
    adaylar = tcm.duz_kategoriler(agac)
    yapraklar = [a for a in adaylar if a["yaprak"]]
    print(f"Trendyol kategorisi: {len(adaylar)} ({len(yapraklar)} yaprak)\n")

    kategoriler = await db.categories.find({}, {"_id": 0}).to_list(2000)
    kokler = {str(c.get("id")): c for c in kategoriler}

    yazilan = atlanan = zayif = zaten = 0
    zayif_liste = []
    for c in sorted(kategoriler, key=lambda x: str(x.get("name") or "")):
        ad = str(c.get("name") or "").strip()
        if not ad or not c.get("parent_id"):
            continue                                  # cinsiyet kökü: ürün almaz
        if c.get("trendyol_category_id") and not args.ez:
            zaten += 1
            continue
        kok = str((kokler.get(str(c.get("parent_id"))) or {}).get("name") or "")
        en_iyi, puan, alternatif = tcm.eslestir(ad, kok, adaylar)
        etiket = f"{kok} > {ad}"
        if not en_iyi or puan < args.esik:
            zayif += 1
            zayif_liste.append((etiket, en_iyi, puan, alternatif))
            continue
        print(f"  {etiket:<34} → {en_iyi['yol']}  (#{en_iyi['id']}, puan {puan})")
        if args.uygula:
            await db.categories.update_one(
                {"id": c["id"]},
                {"$set": {"trendyol_category_id": int(en_iyi["id"]),
                          "trendyol_category_name": en_iyi["name"]}})
        yazilan += 1

    if zayif_liste:
        print("\nELLE SEÇİLECEK (puan eşiğin altında — yazılmadı):")
        for etiket, en_iyi, puan, alternatif in zayif_liste:
            if en_iyi:
                print(f"  {etiket:<34} en yakın: {en_iyi['yol']} (puan {puan})")
                for a in alternatif:
                    print(f"  {'':<34}   ya da: {a['yol']}")
            else:
                print(f"  {etiket:<34} aday bulunamadı")
            atlanan += 1

    print()
    print(f"{'eşleşme yazıldı' if args.uygula else 'eşleşme yazılacak'} : {yazilan}")
    print(f"zaten eşliydi        : {zaten}")
    print(f"elle seçilecek       : {atlanan}")
    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    else:
        print("\nSıradaki adım: ürün kartını açınca Trendyol özellikleri artık "
              "gelir. Şemaya göre otomatik doldurmak için:")
        print("  python ../scripts/fill_product_attributes.py --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
