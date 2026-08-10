"""Sayfa tasarımı / banner / hero görsellerini (frontend'de hardcoded + page_blocks
DB'sinde) Ticimax'ten R2'ye taşır ve referansları günceller. Tam Ticimax bağımsızlığı.

- Frontend dosyalarındaki (Home.jsx, Header.jsx, admin/PageDesign.jsx) tüm
  static.ticimax.cloud URL'leri R2 WebP URL'leriyle değiştirilir.
- page_blocks koleksiyonundaki ticimax URL'leri (iç içe alanlarda) değiştirilir.
- Boyutlar ürünlerle aynı: 400 / 800 / 1280 (img.js ile uyumlu).

Kullanım:
    python -m scripts.migrate_pagedesign_images_to_r2 --apply
"""
import io
import os
import re
import sys

import requests
from PIL import Image
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from services import r2_storage as r2  # noqa: E402

SIZES = [400, 800, 1280]
DEFAULT_SIZE = 800
QUALITY = 85
TCMX = "static.ticimax.cloud"
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "src")
FILES = ["pages/Home.jsx", "components/Header.jsx", "pages/admin/PageDesign.jsx"]

URL_RE = re.compile(r"https://static\.ticimax\.cloud[^\"'\s\\)]+")


def _strip_transform(url: str) -> str:
    m = re.match(r"https?://static\.ticimax\.cloud/cdn-cgi/image/[^/]+/(.*)$", url, re.I)
    if m:
        return f"https://{TCMX}/{m.group(1)}"
    return url


def _safe_name(orig: str) -> str:
    base = orig.rstrip("/").split("/")[-1]
    base = re.sub(r"\.(jpg|jpeg|png|webp)$", "", base, flags=re.I)
    return re.sub(r"[^a-zA-Z0-9_-]", "-", base)[:80]


_cache = {}


def migrate_url(url: str):
    """Bir ticimax URL'ini R2'ye taşır, default R2 URL'i döndürür. Idempotent + cache."""
    if url in _cache:
        return _cache[url]
    orig = _strip_transform(url)
    try:
        resp = requests.get(orig, timeout=60)
        resp.raise_for_status()
        im = Image.open(io.BytesIO(resp.content))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        name = _safe_name(orig)
        default_url = None
        for w in SIZES:
            target = im
            if im.width > w:
                ratio = w / float(im.width)
                target = im.resize((w, max(1, int(im.height * ratio))), Image.LANCZOS)
            out = io.BytesIO()
            target.save(out, format="WEBP", quality=QUALITY, method=4)
            u = r2.put_object(f"pagedesign/{name}-{w}.webp", out.getvalue(), "image/webp")
            if w == DEFAULT_SIZE:
                default_url = u
        result = default_url or r2.public_url(f"pagedesign/{name}-{SIZES[-1]}.webp")
        _cache[url] = result
        print(f"  ✓ {orig.split('/')[-1]} → {result}")
        return result
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {orig}: {e}")
        _cache[url] = url
        return url


def _walk_replace(obj):
    if isinstance(obj, str):
        if TCMX in obj:
            return migrate_url(obj)
        return obj
    if isinstance(obj, list):
        return [_walk_replace(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_replace(v) for k, v in obj.items()}
    return obj


def main(apply: bool):
    if not r2.is_enabled() or not r2.health_check().get("ok"):
        print("R2 erişilemiyor.")
        return

    # 1) Frontend dosyaları
    print("== Frontend dosyaları ==")
    for rel in FILES:
        path = os.path.join(FRONTEND, rel)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        urls = set(URL_RE.findall(content))
        if not urls:
            continue
        print(f"{rel}: {len(urls)} ticimax url")
        if apply:
            for u in urls:
                content = content.replace(u, migrate_url(u))
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    # 2) page_blocks DB
    print("== page_blocks DB ==")
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    import json
    n = 0
    for doc in db.page_blocks.find({}):
        if TCMX not in json.dumps(doc, default=str):
            continue
        n += 1
        if apply:
            new = _walk_replace({k: v for k, v in doc.items() if k != "_id"})
            db.page_blocks.update_one({"_id": doc["_id"]}, {"$set": new})
    print(f"page_blocks etkilenen: {n}")
    print("\nMod:", "UYGULANDI ✅" if apply else "DRY-RUN")


if __name__ == "__main__":
    main("--apply" in sys.argv)
