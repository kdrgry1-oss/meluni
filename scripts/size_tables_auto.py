"""
size_tables_auto.py — Ölçü tablolarını KREDİSİZ, otomatik, katalog geneli işler.

Üç iş bir arada (yönetim isteği):
  1. Ölçüler ürün kartının ölçü tablosu VERİSİNE yazılır — görselden ayrı.
     Vitrindeki beden tablosu, beden önerisi ve pazaryeri bu veriyi okur;
     ölçü yalnız resimde durursa hiçbiri kullanamaz.
  2. Türkçe tablo görseli çizilir ve ürüne (varsa eski tablonun yerine, yoksa
     sona) konur.
  3. Tablosu OLMAYAN renk kardeşlerine aynı tablo kopyalanır. Kendi tablosu
     olan kardeşe DOKUNULMAZ (elle düzeltilmiş olabilir).

Ölçü kaynağı sırayla ürünün kayıtlı tablosu, renk kardeşinin tablosu ve
tedarikçi açıklamasıdır — bunlar KREDİSİZDİR. Hiçbirinde yoksa (ve --kredisiz
verilmediyse) ham tablo görseli modelle bulunur ve sayılar ondan okunur.
Hiçbir yolla bulunamazsa ürün ATLANIR — uydurma ölçü yanlış beden aldırır.

KURU ÇALIŞMA YALNIZ KREDİSİZ KAYNAKLARI SAYAR: "ölçüsü bulunamayan" satırı
model devreye girince küçülür, çünkü tespit/okuma adımı kuru çalışmada
çalıştırılmaz (boşuna kredi harcamamak için).

Aynı tur her gece 05:15 UTC'de zamanlanmış iş olarak da koşuyor
(scheduler._olcu_tablosu_oto_tick); bu betik "şimdi çalıştır" içindir.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/size_tables_auto.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/size_tables_auto.py --uygula
    ... --uygula --kategori takım       # yalnız bir kategori
    ... --uygula --sadece-veri          # görsele dokunma, yalnız ölçü verisi
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
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--limit", type=int, default=0, help="En fazla N ürün işle.")
    ap.add_argument("--kategori", default="", help="Yalnız adı eşleşen kategori.")
    ap.add_argument("--sadece-veri", action="store_true",
                    help="Görsel üretme; yalnız ölçü verisini yaz.")
    ap.add_argument("--ez", action="store_true",
                    help="Mevcut Türkçe tabloyu da yeniden çiz ve kardeşlere yay.")
    ap.add_argument("--kredisiz", action="store_true",
                    help="Modeli hiç çağırma: ham tabloyu arama, görselden okuma.")
    ap.add_argument("--tahmin", action="store_true",
                    help="Hiçbir kaynakta ölçü yoksa manken ölçüsünden TAHMİNİ "
                         "tablo üretir (tabloda 'tahmini' notu yazar).")
    ap.add_argument("--kredi-limit", type=int, default=40,
                    help="Bir turda en fazla kaç üründe model çağrılsın (0=sınırsız).")
    ap.add_argument("--sinirsiz", action="store_true",
                    help="Sınır ve erken vazgeçme YOK: tüm ürünler işlenir, kota "
                         "hatasında vazgeçmek yerine bekleyip devam edilir. "
                         "Kredi harcar ve uzun sürer.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                            # noqa: E402
    from routes import ai_product_images as ai            # noqa: E402

    if args.uygula:
        sonuc = await ai.olcu_tablolari_otomatik(
            limit=args.limit, gorsel=not args.sadece_veri,
            ez=args.ez, kategori=args.kategori,
            tara=not args.kredisiz, ocr=not args.kredisiz, tahmin=args.tahmin,
            kredi_limit=0 if args.sinirsiz else args.kredi_limit,
            hata_tolerans=0 if args.sinirsiz else 3,
            ilerleme=lambda d: print(
                "  … " + " · ".join(f"{k}={v}" for k, v in d.items() if v),
                flush=True))
        for k, v in sonuc.items():
            print(f"{k:<20}: {v}")
        return 0

    # ── Kuru çalışma: hiçbir şey yazmadan ne olacağını sayar ────────────
    q = {"is_deleted": {"$ne": True}}
    if args.kategori:
        q["$or"] = [{"category_name": {"$regex": args.kategori, "$options": "i"}},
                    {"category_path": {"$regex": args.kategori, "$options": "i"}}]
    taranan = veri_yok = 0
    kaynaklar = Counter()
    gorsel_uretilecek = kardes_eksik = 0
    ornekler = []
    async for p in db.products.find(q, {"_id": 0, "id": 1, "name": 1,
                                        "images": 1, "stock_code": 1}):
        if args.limit and taranan >= args.limit:
            break
        taranan += 1
        try:
            veri = await ai._kredisiz_olcu_verisi(p["id"])
        except Exception:
            veri = {}
        if not (veri.get("satirlar") or []):
            veri_yok += 1
            continue
        kaynaklar[veri.get("kaynak_adi") or veri.get("kaynak") or "?"] += 1

        ham = [x for x in (p.get("images") or []) if ai._gorsel_url(x)]
        tablosu_var = any(ai._olcu_gorseli_mi(x) for x in ham)
        if not tablosu_var or args.ez:
            gorsel_uretilecek += 1
            if len(ornekler) < 12:
                ornekler.append((p.get("name", "")[:46],
                                 veri.get("kaynak_adi") or "",
                                 len(veri.get("satirlar") or [])))
        # Kardeşler stok koduyla DEĞİL, vitrinle aynı anahtarla bulunur
        # (her rengin stok kodu farklı; eski sayaç bu yüzden hep 0 diyordu).
        kardes_q = await ai._kardes_sorgusu(p["id"])
        if kardes_q:
            async for s in db.products.find(kardes_q, {"_id": 0, "images": 1}):
                if not any(ai._olcu_gorseli_mi(x) for x in (s.get("images") or [])):
                    kardes_eksik += 1

    for ad, kaynak, satir in ornekler:
        print(f"  {ad:<48} {satir} beden · {kaynak}")
    print()
    print(f"taranan ürün             : {taranan}")
    print(f"ölçüsü bulunan           : {taranan - veri_yok}")
    print(f"ölçüsü bulunamayan       : {veri_yok}  (kayıtlı tablo/kardeş/açıklama boş)")
    print(f"tablo görseli üretilecek : {gorsel_uretilecek}")
    print(f"tablosuz renk kardeşi    : {kardes_eksik}  (tablo kopyalanacak)")
    print()
    print("ölçü nereden geliyor:")
    for ad, adet in kaynaklar.most_common():
        print(f"  {ad:<40} {adet}")
    print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
