"""Ticimax CDN'deki tüm ürün görsellerini indirip WebP'e çevirerek Cloudflare R2'ye
taşır ve ürün kayıtlarındaki URL'leri R2 URL'leriyle değiştirir.

- Tam Ticimax bağımsızlığı: images[] + thumbnail.
- Her görsel için çoklu boyut WebP üretilir (responsive): 400 / 800 / 1280 px.
- DB'de images[i] = R2'deki 800px sürümünün URL'i olarak saklanır; frontend
  (lib/img.js) gerekli boyutu URL'deki sayıyı değiştirerek seçer.
- Idempotent: zaten r2.dev olan URL'ler atlanır.

Kullanım:
    DRY-RUN (sayar):  python -m scripts.migrate_product_images_to_r2
    UYGULA:           python -m scripts.migrate_product_images_to_r2 --apply
    Sınırla (test):   python -m scripts.migrate_product_images_to_r2 --apply --limit 5
"""
import io
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from services import r2_storage as r2  # noqa: E402

SIZES = [400, 800, 1280]
DEFAULT_SIZE = 800
QUALITY = 82
R2_HOST = "r2.dev"
TCMX = "static.ticimax.cloud"


def _strip_transform(url: str) -> str:
    """Ticimax cdn-cgi transform parçasını soyar → orijinal görsel URL'i."""
    m = re.match(r"https?://static\.ticimax\.cloud/cdn-cgi/image/[^/]+/(.*)$", url, re.I)
    if m:
        return f"https://{TCMX}/{m.group(1)}"
    return url


def _process_one(prod_id: str, kind: str, idx: int, url: str):
    """Bir görseli indir → WebP üret → R2'ye yükle. (yeni_default_url) döndürür."""
    orig = _strip_transform(url)
    resp = requests.get(orig, timeout=60)
    resp.raise_for_status()
    im = Image.open(io.BytesIO(resp.content))
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    src_w = im.width
    default_url = None
    base = f"products/{prod_id}/{kind}{idx}"
    for w in SIZES:
        target = im
        if src_w > w:
            ratio = w / float(src_w)
            target = im.resize((w, max(1, int(im.height * ratio))), Image.LANCZOS)
        out = io.BytesIO()
        target.save(out, format="WEBP", quality=QUALITY, method=4)
        key = f"{base}-{w}.webp"
        u = r2.put_object(key, out.getvalue(), "image/webp")
        if w == DEFAULT_SIZE:
            default_url = u
    return default_url or r2.public_url(f"{base}-{SIZES[-1]}.webp")


def main(apply: bool, limit: int):
    if not r2.is_enabled() or not r2.health_check().get("ok"):
        print("R2 yapılandırılmamış/erişilemiyor.")
        return

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    q = {"$or": [{"images": {"$elemMatch": {"$regex": TCMX}}},
                 {"thumbnail": {"$regex": TCMX}}]}
    cursor = db.products.find(q, {"id": 1, "name": 1, "images": 1, "thumbnail": 1})
    products = list(cursor)
    if limit:
        products = products[:limit]
    print(f"İşlenecek ürün: {len(products)} | Mod: {'UYGULA' if apply else 'DRY-RUN'}")

    total_imgs = sum(len([u for u in (p.get("images") or []) if TCMX in str(u)]) +
                     (1 if TCMX in str(p.get("thumbnail") or "") else 0) for p in products)
    print(f"Taşınacak görsel (boyutsuz): {total_imgs}")
    if not apply:
        return

    done = {"prod": 0, "img": 0, "err": 0}

    def handle_product(p):
        pid = p["id"]
        changed = {}
        new_images = []
        for i, u in enumerate(p.get("images") or []):
            # Dict-form görselleri (ör. {url, is_size_table:true}) OLDUĞU GİBİ koru —
            # str()'e çevirmek işareti düşürür ve ölçü tablosu galeriye sızardı.
            if isinstance(u, dict):
                new_images.append(u)
                continue
            u = str(u)
            if TCMX in u:
                try:
                    new_images.append(_process_one(pid, "img", i, u))
                    done["img"] += 1
                except Exception as e:  # noqa: BLE001
                    done["err"] += 1
                    print(f"  ✗ {pid} img{i}: {e}")
                    new_images.append(u)  # hata → eski URL kalsın
            else:
                new_images.append(u)
        changed["images"] = new_images
        thumb = str(p.get("thumbnail") or "")
        if TCMX in thumb:
            try:
                changed["thumbnail"] = _process_one(pid, "thumb", 0, thumb)
                done["img"] += 1
            except Exception as e:  # noqa: BLE001
                done["err"] += 1
                print(f"  ✗ {pid} thumb: {e}")
        db.products.update_one({"id": pid}, {"$set": changed})
        done["prod"] += 1
        if done["prod"] % 25 == 0:
            print(f"  ... {done['prod']}/{len(products)} ürün | {done['img']} görsel | {done['err']} hata")

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(handle_product, p) for p in products]
        for f in as_completed(futures):
            f.result()

    print(f"\nBitti. Ürün: {done['prod']} | Görsel: {done['img']} | Hata: {done['err']}")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    main(apply, limit)
