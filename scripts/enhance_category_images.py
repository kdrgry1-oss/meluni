"""
enhance_category_images.py — Bir kategorideki TÜM ürünlerin görsellerini
Gemini ile iyileştirir (1200x1600, kırpma yok).

Neden betik, panel değil: kategori yüzlerce ürün, her ürün 3-6 görsel demek.
Bu iş dakikalarca sürer; tarayıcıdan yapılırsa vekilin 100 saniyelik sınırına
takılır. Betik sunucuda çalışır, süre sınırı yoktur.

ÜCRETLİDİR: her görsel bir Gemini çağrısıdır. Bu yüzden varsayılan YALNIZ
RAPORDUR — kaç ürün, kaç görsel, kaç çağrı olacağını yazar, hiçbir şey
değiştirmez. Uygulamak için `--uygula` gerekir.

GÜVENLİK: kredi/kota biterse model görseli işleyemez. O görsel ATLANIR
(`sadece_ai`); düz büyütülmüş hâliyle ürünün üstüne YAZILMAZ. Aksi hâlde
kredi ortada bitince yüzlerce görsel sessizce "yalnız ölçeklenmiş" olur ve
`images_custom` işareti yüzünden tedarikçi güncellemesi de kilitlenir.

Kaldığı yerden devam eder: daha önce iyileştirilmiş ürünler atlanır
(`--tekrar` ile onlar da işlenir).

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/enhance_category_images.py --kategori takim
    ~/apps/meluni/shared/venv/bin/python ../scripts/enhance_category_images.py --kategori takim --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kategori", required=True,
                    help="Kategori slug'ı, adı ya da id'si. Birden çok eşleşirse "
                         "hepsi listelenir ve hiçbiri işlenmez — kesin olanı yazın.")
    ap.add_argument("--uygula", action="store_true", help="Görselleri GERÇEKTEN üretir.")
    ap.add_argument("--tekrar", action="store_true",
                    help="Daha önce iyileştirilmiş ürünleri de yeniden işler.")
    ap.add_argument("--limit", type=int, default=0, help="En fazla N ürün işle.")
    ap.add_argument("--gecikme", type=float, default=1.0,
                    help="Ürünler arası bekleme (sn) — sağlayıcı hız sınırına takılmamak için.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                      # noqa: E402
    from routes import ai_product_images as ai      # noqa: E402

    # ── Kategoriyi bul ──────────────────────────────────────────────────
    aranan = args.kategori.strip().lower()
    adaylar = []
    async for k in db.categories.find({}, {"_id": 0, "id": 1, "name": 1,
                                           "slug": 1, "parent_id": 1}):
        if aranan in (str(k.get("id") or "").lower(),
                      str(k.get("slug") or "").lower(),
                      str(k.get("name") or "").strip().lower()):
            adaylar.append(k)
    if not adaylar:
        print(f"'{args.kategori}' diye bir kategori yok.")
        return 1
    if len(adaylar) > 1:
        print(f"'{args.kategori}' birden çok kategoriye uyuyor — hangisi olduğunu "
              f"slug ya da id ile belirtin:")
        for k in adaylar:
            ust = await db.categories.find_one({"id": k.get("parent_id")},
                                               {"_id": 0, "name": 1})
            print(f"  id={k['id']:<6} slug={k.get('slug','') :<24} "
                  f"{k.get('name','')}  (üst: {(ust or {}).get('name','-')})")
        return 1
    kat = adaylar[0]
    ust = await db.categories.find_one({"id": kat.get("parent_id")}, {"_id": 0, "name": 1})
    print(f"kategori: {kat.get('name')} (slug={kat.get('slug')}, id={kat['id']}, "
          f"üst={(ust or {}).get('name','-')})\n")

    # ── Ürünleri topla ──────────────────────────────────────────────────
    sorgu = {"is_deleted": {"$ne": True},
             "$or": [{"category_id": kat["id"]},
                     {"category_ids": kat["id"]},
                     {"category": kat.get("name")}]}
    urunler = []
    async for p in db.products.find(sorgu, {"_id": 0, "id": 1, "name": 1,
                                            "images": 1, "images_enhanced_at": 1}):
        urunler.append(p)

    islenecek = [p for p in urunler
                 if args.tekrar or not p.get("images_enhanced_at")]
    if args.limit:
        islenecek = islenecek[:args.limit]

    gorsel_sayisi = sum(len([u for u in (p.get("images") or []) if ai._gorsel_url(u)])
                        for p in islenecek)
    print(f"kategorideki ürün          : {len(urunler)}")
    print(f"daha önce iyileştirilmiş   : {len(urunler) - len([p for p in urunler if not p.get('images_enhanced_at')])}")
    print(f"işlenecek ürün             : {len(islenecek)}")
    print(f"işlenecek görsel           : {gorsel_sayisi}")
    print(f"tahmini Gemini çağrısı     : {gorsel_sayisi * 2}"
          f"   (her görsel için 1 sınıflandırma + 1 iyileştirme)\n")

    if not yaz:
        print("Hiçbir şey üretilmedi. Uygulamak için: --uygula")
        print("DİKKAT: her görsel ücretli bir çağrıdır.")
        return 0
    if not islenecek:
        return 0

    ok = atlandi = hatali = 0
    for i, p in enumerate(islenecek, 1):
        try:
            sonuc = await ai.enhance_product_images(
                p["id"], replace=True, sadece_ai=True)
            r = sonuc.get("results") or []
            basarili = [x for x in r if x.get("url")]
            bos = [x for x in r if not x.get("url")]
            ok += len(basarili)
            atlandi += len([x for x in bos if x.get("atlandi")])
            hatali += len([x for x in bos if not x.get("atlandi")])
            durum = f"{len(basarili)}/{len(r)} görsel"
            if bos:
                durum += f"  ({(bos[0].get('error') or '')[:60]})"
            print(f"[{i}/{len(islenecek)}] {p.get('name','')[:44]:<44} {durum}", flush=True)
        except Exception as e:
            hatali += 1
            print(f"[{i}/{len(islenecek)}] {p.get('name','')[:44]:<44} HATA: {e}",
                  flush=True)
        if args.gecikme:
            await asyncio.sleep(args.gecikme)

    print()
    print(f"iyileştirilen görsel : {ok}")
    print(f"atlanan (model yok)  : {atlandi}  (üzerine YAZILMADI)")
    print(f"hatalı               : {hatali}")
    if atlandi:
        print("\nAtlananlar kredi/kota bittiği için işlenemedi. Kredi yükleyip "
              "betiği tekrar çalıştırın — işlenmiş ürünler atlanır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
