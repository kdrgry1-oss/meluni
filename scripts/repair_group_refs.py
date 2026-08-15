"""
repair_group_refs.py — Bozuk renk-kardeşi grup kodlarını temizler.

NEDEN: Tedarikçi beslemesi grup kodunu bazı üründe eksik yolluyor; değer yalnız
önekten ibaret kalıyor ("MLN-"). Renk kardeşleri bu alandan bulunduğu için o
değeri taşıyan BÜTÜN ürünler birbirinin kardeşi sayıldı — tulumun altında bluz,
elbise, takım çıktı. Canlıda yaşandı.

Bu betik kimlik taşımayan grup kodlarını (ayırıcılar atıldıktan sonra 4 harften
kısa, ya da tek karakterin tekrarı) BOŞALTIR. Boş kodda ürün kendi başına kalır
ve vitrin ada dayalı yedek yola düşer (aynı taban adlı diğer renkler) — yanlış
kardeş göstermektense hiç göstermemek doğrudur.

Kod yalnız KİMLİK TAŞIMAYAN değerleri temizler; sağlam grup kodlarına
(MLN-6a2c03c1c0ab9 gibi) dokunmaz.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/repair_group_refs.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/repair_group_refs.py --uygula
"""
import argparse
import asyncio
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Temizliği YAZAR.")
    ap.add_argument("--liste", type=int, default=10, help="Kaç örnek yazılsın.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                  # noqa: E402
    from group_ref import gecerli_grup          # noqa: E402

    taranan = bozuk = 0
    degerler = Counter()
    ornekler = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "category_name": 1,
             "urun_karti_id": 1, "csv_card_id": 1, "supplier_group_ref": 1}):
        taranan += 1
        alanlar = {}
        for alan in ("urun_karti_id", "csv_card_id", "supplier_group_ref"):
            deger = str(p.get(alan) or "").strip()
            if deger and not gecerli_grup(deger):
                alanlar[alan] = ""
                degerler[f"{alan}={deger!r}"] += 1
        if not alanlar:
            continue
        bozuk += 1
        if len(ornekler) < args.liste:
            ornekler.append((p.get("name", "")[:46], p.get("category_name"),
                             ", ".join(sorted(alanlar))))
        if args.uygula:
            await db.products.update_one({"id": p["id"]}, {"$set": alanlar})

    # ── Kardeşi olan ürüne SAĞLAM grup kodunu geri ver ─────────────────
    # Bozuk kodu boşaltmak yanlış kardeşi keser ama doğru kardeşi de koparır:
    # aynı modelin bir rengi geçerli kod taşırken (Kırmızı) diğerine besleme
    # "MLN-" göndermiş olabiliyor (Siyah). Taban adı (sondaki renk sözcüğü
    # soyulmuş) aynı olan ürünlerden geçerli kodu taşıyanı bulup kodsuz kalana
    # yazıyoruz — böylece renkler yine tek kartta buluşuyor.
    from routes.products import _strip_trailing_color              # noqa: E402
    taban = {}
    kodsuz = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "urun_karti_id": 1}):
        # _strip_trailing_color renk BULAMAZSA boş döner (vitrin orada yanlış
        # gruplamaktansa hiç gruplamamayı seçiyor). Burada tam ad da geçerli
        # bir tabandır: adlarımız tedarikçiden gelen 8-12 kelimelik özgün
        # tanımlar, aynısını taşıyan iki ürün gerçekten aynı modeldir.
        tam = str(p.get("name") or "").strip()
        ad = (_strip_trailing_color(tam) or tam).strip().lower()
        if not ad or len(ad.split()) < 3:
            continue
        kod = str(p.get("urun_karti_id") or "").strip()
        if kod and gecerli_grup(kod):
            taban.setdefault(ad, kod)
        else:
            kodsuz.append((p["id"], ad, p.get("name", "")))

    baglanan = 0
    for pid, ad, tam_ad in kodsuz:
        kod = taban.get(ad)
        if not kod:
            continue
        baglanan += 1
        if baglanan <= 8:
            print(f"  kardeşe bağlanıyor: {tam_ad[:52]:<54} → {kod}")
        if args.uygula:
            await db.products.update_one(
                {"id": pid}, {"$set": {"urun_karti_id": kod,
                                       "supplier_group_ref": kod}})
    if baglanan:
        print(f"  ({baglanan} ürün aynı modelin geçerli kodunu devraldı)\n")

    for ad, kat, alan in ornekler:
        print(f"  {ad:<48} [{kat}]  → {alan} boşaltılacak")
    if bozuk > len(ornekler):
        print(f"  … ve {bozuk - len(ornekler)} ürün daha")
    print()
    print(f"taranan ürün           : {taranan}")
    print(f"{'temizlenen ürün' if args.uygula else 'temizlenecek ürün'}      : {bozuk}")
    print(f"{'kardeşe bağlanan' if args.uygula else 'kardeşe bağlanacak'}     : {baglanan}")
    print()
    print("hangi bozuk değer kaç kez:")
    for d, adet in degerler.most_common(15):
        print(f"  {d:<44} {adet}")
    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    else:
        print("\nSağlam grup kodlarına dokunulmadı. Ürünler bir sonraki XML "
              "aktarımında beslemedeki kod düzelirse yeniden gruplanır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
