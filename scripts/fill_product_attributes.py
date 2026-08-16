"""
fill_product_attributes.py — Ürün özelliklerini (pazaryeri attribute) doldurur.

NEDEN: Trendyol her kategoride ürün özelliği ister (kol tipi, yaka tipi, kumaş,
desen…), bazıları ZORUNLU. Tedarikçi beslemesi bunları vermiyor; ürünler
özelliksiz gidince ya reddediliyor ya da pazaryeri filtrelerinde hiç çıkmıyor.

Özellikler üründe zaten VAR olan bilgiden çıkarılır (backend/product_attributes.py):
ürün adı, tedarikçi açıklaması, ölçü tablosundaki manken bilgisi ve beslemenin
kumaş/cinsiyet alanları. Hiçbir kaynakta geçmeyen özellik YAZILMAZ — uydurulmuş
özellik, eksik özellikten kötüdür.

Elle girilmiş özelliklere DOKUNMAZ: ürün zaten `attributes` taşıyorsa yalnız
EKSİK olanlar tamamlanır (`--ez` verilirse üzerine yazılır).

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/fill_product_attributes.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/fill_product_attributes.py --uygula
"""
import argparse
import asyncio
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _mevcut_adlar(attrs) -> set:
    """Üründe hâlihazırda duran özellik adları (liste ya da sözlük biçimi)."""
    adlar = set()
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            adlar.add(str((v or {}).get("label") or (v or {}).get("name") or k
                          if isinstance(v, dict) else k).strip().lower())
    elif isinstance(attrs, list):
        for a in attrs:
            if isinstance(a, dict):
                ad = a.get("name") or a.get("label") or a.get("type")
                if ad:
                    adlar.add(str(ad).strip().lower())
    return adlar


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--ez", action="store_true",
                    help="Mevcut özelliklerin üzerine yazar (varsayılan: yalnız eksikleri tamamlar).")
    ap.add_argument("--limit", type=int, default=0, help="En fazla N ürün işle.")
    ap.add_argument("--kategori", default="",
                    help="Yalnız adı/yolu eşleşen kategori (ör. takım).")
    ap.add_argument("--liste", type=int, default=10, help="Kaç örnek yazılsın.")
    ap.add_argument("--kumas-varsayilan", default="",
                    help="Kumaş Tipi boş kalan ürünlere yazılacak değer "
                         "(ör. Dokuma ya da Belirtilmemiş). Verilmezse boş kalır.")
    ap.add_argument("--varsayilan", action="store_true",
                    help="Sezon/kalıp çıkmayan ürünlere güvenli varsayılanı yazar "
                         "(Tüm Sezonlar / normal). Varsayılan: boş bırakılır.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                    # noqa: E402
    import product_attributes as pa               # noqa: E402

    taranan = degisen = bos_kalan = 0
    sezon_yazilan = kalip_yazilan = sezon_bos = kalip_bos = 0
    dagilim = Counter()
    ornekler = []

    sorgu = {"is_deleted": {"$ne": True}}
    if args.kategori:
        sorgu["$or"] = [
            {"category_name": {"$regex": args.kategori, "$options": "i"}},
            {"category_path": {"$regex": args.kategori, "$options": "i"}}]

    async for p in db.products.find(
            sorgu,
            {"_id": 0, "id": 1, "name": 1, "description": 1, "fabric": 1,
             "gender": 1, "brand": 1, "attributes": 1, "category_name": 1,
             "color": 1, "variants": 1, "material": 1,
             "category_path": 1, "season": 1, "size_advice": 1}):
        taranan += 1
        if args.limit and taranan > args.limit:
            break

        st = await db.size_tables.find_one({"product_id": p["id"]}, {"_id": 0})
        uretilen = pa.urun_ozellikleri(p, st,
                                       kumas_varsayilan=args.kumas_varsayilan)
        guncelle = {}

        # ── Ürün kartının zorunlu temel alanları (Products.jsx bunlar boşken
        # kaydı reddediyor). Elle girilmiş değer EZİLMEZ (--ez hariç).
        temel = pa.temel_alanlar(p, st)
        sezon = temel.get("season", "")
        kalip = temel.get("size_advice", "")
        if not sezon:
            sezon_bos += 1
            if args.varsayilan:
                sezon = "Tüm Sezonlar"
        if not kalip:
            kalip_bos += 1
            if args.varsayilan:
                kalip = "normal"
        if sezon and (args.ez or not str(p.get("season") or "").strip()):
            guncelle["season"] = sezon
            sezon_yazilan += 1
        if kalip and (args.ez or not str(p.get("size_advice") or "").strip()):
            guncelle["size_advice"] = kalip
            kalip_yazilan += 1

        mevcut = p.get("attributes")
        var_olan = set() if args.ez else _mevcut_adlar(mevcut)
        eklenecek = [a for a in uretilen if a["name"].strip().lower() not in var_olan]
        if not uretilen:
            bos_kalan += 1
        if eklenecek:
            for a in eklenecek:
                dagilim[a["name"]] += 1
            # Mevcut liste biçimindeyse üstüne ekle; sözlük/boşsa listeye çevir.
            guncelle["attributes"] = (list(mevcut) + eklenecek
                                      if isinstance(mevcut, list) and not args.ez
                                      else uretilen)

        if not guncelle:
            continue
        degisen += 1
        if len(ornekler) < args.liste:
            ozet = ", ".join(f"{a['name']}={a['value']}" for a in eklenecek[:3])
            ek = " · ".join(x for x in (f"sezon={guncelle['season']}" if "season" in guncelle else "",
                                        f"kalıp={guncelle['size_advice']}" if "size_advice" in guncelle else "")
                            if x)
            ornekler.append((p.get("name", "")[:44], ", ".join(x for x in (ozet, ek) if x)))
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": guncelle})

    for ad, ozet in ornekler:
        print(f"  {ad}")
        print(f"      {ozet}")
    print()
    print(f"taranan ürün            : {taranan}")
    print(f"{'güncellenen ürün' if yaz else 'güncellenecek ürün'}      : {degisen}")
    print(f"hiç özellik çıkmayan    : {bos_kalan}  (ad/açıklama işaret taşımıyor)")
    print(f"sezon dolduruldu        : {sezon_yazilan}   (çıkarılamayan: {sezon_bos})")
    print(f"kalıp dolduruldu        : {kalip_yazilan}   (çıkarılamayan: {kalip_bos})")
    print()
    print("hangi özellikten kaç tane:")
    for ad, adet in dagilim.most_common(20):
        print(f"  {ad:<28} {adet}")
    if (sezon_bos or kalip_bos) and not args.varsayilan:
        print("\nSezon/kalıp çıkmayanlar BOŞ bırakıldı (uydurma yok). Güvenli "
              "varsayılanı (Tüm Sezonlar / normal) yazmak için: --varsayilan")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
