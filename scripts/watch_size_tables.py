"""
watch_size_tables.py — Ölçü tablosu turunu ANLIK izler: hangi ürün değişti?

NEDEN: Katalog turu saatler sürebiliyor ve tek satır özet ancak sonda geliyor.
"Şu an hangi ürünler değişti?" sorusunun cevabı üründeki `size_chart_localized_at`
damgasında duruyor — bu betik onu okuyup son değişenleri sıralar. Çalışan tura
DOKUNMAZ, yalnız okur; turu kesmeden başka bir kabuktan çalıştırılabilir.

    ~/apps/meluni/shared/venv/bin/python ../scripts/watch_size_tables.py
    ... --izle              # 10 saniyede bir yenile, yeni değişenleri bas
    ... --izle --aralik 30  # daha seyrek
    ... --adet 40           # daha uzun liste

Kullanım (sunucuda, backend dizininden — .env oradan okunuyor):
    cd ~/apps/meluni/current/backend
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _saat(iso: str) -> str:
    s = str(iso or "")
    return s[11:19] if len(s) >= 19 else s


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adet", type=int, default=20, help="Kaç ürün listelensin.")
    ap.add_argument("--izle", action="store_true", help="Sürekli yenile.")
    ap.add_argument("--aralik", type=int, default=10, help="İzleme aralığı (sn).")
    ap.add_argument("--kategori", default="", help="Yalnız bu kategori.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                          # noqa: E402
    from routes import ai_product_images as ai          # noqa: E402

    sorgu = {"size_chart_localized_at": {"$exists": True}}
    if args.kategori:
        sorgu["$or"] = [
            {"category_name": {"$regex": args.kategori, "$options": "i"}},
            {"category_path": {"$regex": args.kategori, "$options": "i"}}]

    async def _liste(adet):
        return await db.products.find(
            sorgu, {"_id": 0, "id": 1, "name": 1, "images": 1,
                    "size_chart_localized_at": 1}
        ).sort("size_chart_localized_at", -1).limit(adet).to_list(adet)

    async def _satir(p):
        tablo = sum(1 for x in (p.get("images") or []) if ai._olcu_gorseli_mi(x))
        st = await db.size_tables.find_one({"product_id": p["id"]},
                                           {"_id": 0, "sizes": 1})
        beden = len((st or {}).get("sizes") or [])
        return (f"{_saat(p.get('size_chart_localized_at')):<9} "
                f"{(p.get('name') or '')[:56]:<58} "
                f"{tablo} tablo görseli · {beden} beden verisi")

    if not args.izle:
        for p in await _liste(args.adet):
            print(await _satir(p))
        toplam = await db.products.count_documents(sorgu)
        print(f"\ntoplam tablosu işlenmiş ürün: {toplam}")
        return 0

    # İzleme: yalnız YENİ görüneni bas, ekranı doldurmadan.
    gorulen = set()
    ilk = True
    while True:
        kayitlar = await _liste(args.adet)
        for p in reversed(kayitlar):
            anahtar = (p["id"], p.get("size_chart_localized_at"))
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            if not ilk:
                print(await _satir(p), flush=True)
        if ilk:
            for p in reversed(kayitlar):
                print(await _satir(p), flush=True)
            print(f"--- izleniyor ({args.aralik} sn), çıkmak için Ctrl+C ---",
                  flush=True)
            ilk = False
        await asyncio.sleep(max(3, args.aralik))


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
