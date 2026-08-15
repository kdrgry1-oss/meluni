"""
purge_db_image_blobs.py — Veritabanındaki gereksiz base64 görsel kopyalarını siler.

NEDEN: `_store()` üretilen her görseli diske yazıyor AMA bir de base64 kopyasını
`files` koleksiyonuna koyuyordu. Görsel dosyadır; veritabanına konunca her kayıt
~%33 şişerek yer kaplar. Toplu iyileştirmede 654 görsel Atlas'ın 512 MB kotasını
doldurdu ve veritabanı YAZMAYI REDDETTİ — o noktadan sonra sipariş bile
kaydedilemez. (Kaynak düzeltildi: base64 artık yalnız diske yazılamadığında
saklanıyor.)

Bu betik YALNIZ diskte gerçekten karşılığı olan kayıtların `data_b64` alanını
siler. Dosya diskte yoksa kopya TEK nüshadır, dokunulmaz — yoksa görsel kaybolur.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/purge_db_image_blobs.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/purge_db_image_blobs.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _mb(bayt: float) -> str:
    return f"{bayt / (1024 * 1024):.1f} MB"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Kopyaları GERÇEKTEN siler.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db          # noqa: E402
    from routes.upload import UPLOAD_DIR  # noqa: E402

    print(f"disk dizini: {UPLOAD_DIR}\n")

    toplam = kopyali = silinebilir = tek_nusha = 0
    kazanc = 0
    async for f in db.files.find({"data_b64": {"$exists": True, "$ne": ""}},
                                 {"_id": 0, "id": 1, "storage_path": 1,
                                  "data_b64": 1, "size": 1}):
        toplam += 1
        kopyali += 1
        ad = f.get("storage_path") or ""
        diskte = bool(ad) and os.path.exists(os.path.join(UPLOAD_DIR, ad))
        b64_boyut = len(f.get("data_b64") or "")
        if diskte:
            silinebilir += 1
            kazanc += b64_boyut
            if yaz:
                await db.files.update_one({"id": f["id"]}, {"$unset": {"data_b64": ""}})
        else:
            tek_nusha += 1

    print(f"base64 kopyası olan kayıt : {kopyali}")
    print(f"diskte de var (silinebilir): {silinebilir}")
    print(f"YALNIZ veritabanında      : {tek_nusha}  (dokunulmadı — tek nüsha)")
    print(f"{'kazanılan' if yaz else 'kazanılacak'} yer         : {_mb(kazanc)}")
    if not yaz:
        print("\nHiçbir şey silinmedi. Uygulamak için: --uygula")
    else:
        print("\nMongoDB silinen alanı hemen geri vermeyebilir; Atlas kota "
              "göstergesi birkaç dakikada güncellenir.")
        if tek_nusha:
            print(f"\n{tek_nusha} görsel HÂLÂ yalnız veritabanında duruyor. Bunlar "
                  "diske yazılamadığı için oradalar; kalıcı çözüm R2 (nesne "
                  "depolama) açmaktır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
