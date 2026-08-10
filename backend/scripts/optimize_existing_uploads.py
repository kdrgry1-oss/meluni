"""Mevcut R2 yüklemelerini (db.files, key prefix 'uploads/') resize+WebP'e çevirir.

Neden gerekli: /upload/image eskiden hiç resize yapmadan ham görseli (bazıları
1700-2500px PNG/JPG) doğrudan R2'ye basıyordu. Sonuç: ürün ızgarasında ilk
ziyarette (soğuk cache) görseller decode/paint için 3-4sn beklediği için "ürün
boş çıkıyor, yenileyince çıkıyor" şikayetine yol açtı. routes/upload.py artık
yeni yüklemelerde resize+WebP yapıyor; bu script GEÇMİŞTE yüklenmiş ham
görselleri aynı işlemden geçirir.

Önemli — AYNI R2 key'e geri yazar (içerik değişir, URL değişmez):
- product.images[] gibi hiçbir yerde DB URL güncellemesi GEREKMEZ.
- Content-Type header'ı image/webp olarak güncellenir; dosya uzantısı
  (örn. .png) aynı kalsa da tarayıcılar Content-Type'a göre decode eder,
  sorun olmaz.
- DİKKAT: Orijinal upload'larda Cache-Control: public, max-age=31536000,
  immutable set edilmişti. Bu, Cloudflare edge cache + ziyaretçi tarayıcı
  cache'inde eski (büyük) sürümün bir süre daha servis edilebileceği anlamına
  gelir. Anında etkisi görmek için Cloudflare panelinden
  cdn.facette.com.tr/uploads/* için "Purge Cache" yapılması gerekir (bu
  script'in API erişimi yok, hesapta CF API token yapılandırılmamış).

Kullanım:
    DRY-RUN (sayar, hiçbir şey yazmaz):  python -m scripts.optimize_existing_uploads
    UYGULA:                              python -m scripts.optimize_existing_uploads --apply
    Sınırla (test):                      python -m scripts.optimize_existing_uploads --apply --limit 20
"""
import argparse
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from services import r2_storage as r2  # noqa: E402

MAX_LONG_EDGE = 2000
QUALITY = 88
KEY_PREFIX = "uploads/"


def _optimize(data: bytes):
    """(yeni_bytes, yeni_content_type, atlandı_mı, sebep) döndürür."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))

    if getattr(im, "is_animated", False):
        return None, None, True, "animasyonlu, atlandı"

    already_webp = (im.format or "").upper() == "WEBP"
    long_edge = max(im.width, im.height)
    if already_webp and long_edge <= MAX_LONG_EDGE:
        return None, None, True, "zaten optimize (webp + boyut uygun)"

    im = ImageOps.exif_transpose(im)
    if long_edge > MAX_LONG_EDGE:
        ratio = MAX_LONG_EDGE / float(long_edge)
        im = im.resize(
            (max(1, int(im.width * ratio)), max(1, int(im.height * ratio))),
            Image.LANCZOS,
        )

    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
    elif im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")

    out = io.BytesIO()
    im.save(out, format="WEBP", quality=QUALITY, method=6)
    return out.getvalue(), "image/webp", False, f"{im.width}x{im.height}"


def _process_one(rec: dict, apply: bool):
    key = rec.get("r2_key")
    path = rec.get("storage_path")
    try:
        data, _ctype = r2.get_object(key)
        if data is None:
            return (path, "ATLANDI", "R2'de bulunamadı")

        before = len(data)
        new_data, new_ctype, skipped, info = _optimize(data)
        if skipped:
            return (path, "ATLANDI", info)

        after = len(new_data)
        saved_pct = round(100 * (1 - after / before), 1) if before else 0

        if apply:
            r2.put_object(key, new_data, new_ctype)
            # DB kaydını güncelle — key/url aynı kalır, sadece content_type/size.
            db.files.update_one(
                {"r2_key": key},
                {"$set": {"content_type": new_ctype, "size": after}},
            )
        return (
            path,
            "UYGULANDI" if apply else "UYGULANACAK (dry-run)",
            f"{info} · {before//1024}KB → {after//1024}KB (-%{saved_pct})",
        )
    except Exception as e:  # noqa: BLE001
        return (path, "HATA", str(e))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Gerçekten R2'ye yaz (yoksa dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Test için ilk N kayıtla sınırla")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    if not r2.is_enabled():
        print("R2 yapılandırılmamış (.env eksik) — çıkılıyor.")
        return

    global db
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ.get("DB_NAME", "facette")
    client = MongoClient(mongo_url)
    db = client[db_name]

    query = {
        "is_deleted": {"$ne": True},
        "r2_key": {"$regex": f"^{KEY_PREFIX}"},
        "content_type": {"$regex": "^image/"},
    }
    cursor = db.files.find(query, {"r2_key": 1, "storage_path": 1})
    if args.limit:
        cursor = cursor.limit(args.limit)
    records = list(cursor)
    total = len(records)
    print(f"{total} görsel kontrol edilecek (apply={args.apply})...")

    counts = {"UYGULANDI": 0, "UYGULANACAK (dry-run)": 0, "ATLANDI": 0, "HATA": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_process_one, rec, args.apply): rec for rec in records}
        for i, fut in enumerate(as_completed(futures), 1):
            path, status, info = fut.result()
            counts[status] = counts.get(status, 0) + 1
            if status in ("UYGULANDI", "UYGULANACAK (dry-run)", "HATA"):
                print(f"[{i}/{total}] {status}: {path} — {info}")

    print("\n--- ÖZET ---")
    for k, v in counts.items():
        print(f"{k}: {v}")
    if not args.apply and counts.get("UYGULANACAK (dry-run)", 0) > 0:
        print("\nGerçekten uygulamak için --apply ekleyerek tekrar çalıştır.")


if __name__ == "__main__":
    main()
