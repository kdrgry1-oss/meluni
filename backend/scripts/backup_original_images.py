"""
Ürün görsellerinin ORİJİNAL (tam çözünürlük/kalite) hallerini her ürün için
ayrı klasöre yedekler. Müşteriye gösterimde WebP/resize uygulanır ama bu script
sayesinde orijinaller her zaman güvende ve geri alınabilir.

- Ticimax görselleri: cdn-cgi/image transform'u soyulur → orijinal URL indirilir.
- Yerel görseller (/api/files, /api/upload/files): orijinal byte'lar MongoDB'den okunur.
- Klasör yapısı: backend/uploads/originals/{slug}/{dosya_adi}
- Kalıcı manifest: db.product_image_backups (disk silinse bile orijinal kaynak URL korunur).

Çalıştırma:
    cd /app/backend && python scripts/backup_original_images.py
"""
import asyncio
import os
import re
import base64
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
ORIGINALS_DIR = os.path.join(BASE_DIR, "uploads", "originals")
os.makedirs(ORIGINALS_DIR, exist_ok=True)

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

CONCURRENCY = 8
_sem = asyncio.Semaphore(CONCURRENCY)


def strip_cdn_transform(url: str) -> str:
    """Ticimax/Cloudflare cdn-cgi/image transform'unu soyup orijinal URL'i döndürür."""
    if not isinstance(url, str):
        return url
    m = re.match(r"^(https?://[^/]+)/cdn-cgi/image/[^/]+/(.*)$", url, re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return url


def safe_name(name: str) -> str:
    name = unquote(name or "")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:150] or "image"


def slug_folder(product: dict) -> str:
    slug = product.get("slug") or product.get("id") or "unknown"
    return safe_name(str(slug))


def image_basename(src: str) -> str:
    parsed = urlparse(src)
    base = os.path.basename(parsed.path) if parsed.path else ""
    if not base:
        base = safe_name(src)[-40:]
    return safe_name(base)


async def fetch_local_bytes(src: str):
    """/api/files/{path} veya /api/upload/files/{path} → MongoDB orijinal byte'ları."""
    path = src
    for prefix in ("/api/upload/files/", "/api/files/"):
        if prefix in path:
            path = path.split(prefix, 1)[1]
            break
    path = path.split("?")[0]
    rec = await db.files.find_one({"storage_path": path}, {"data_b64": 1, "content_type": 1})
    if rec and rec.get("data_b64"):
        try:
            return base64.b64decode(rec["data_b64"]), rec.get("content_type", "image/jpeg")
        except Exception:
            return None, None
    return None, None


async def download_one(http: httpx.AsyncClient, src: str, folder_path: str):
    """Tek bir görseli orijinal haliyle indirip kaydeder. (saved, skipped, error)"""
    if not isinstance(src, str) or not src.strip():
        return ("skip", None, "empty")

    original_url = strip_cdn_transform(src)
    fname = image_basename(original_url)
    dest = os.path.join(folder_path, fname)

    # Idempotent: zaten yedeklenmişse atla
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return ("exists", {"src": src, "original_url": original_url, "file": fname, "size": os.path.getsize(dest)}, None)

    async with _sem:
        try:
            if original_url.startswith("/api/"):
                content, _ctype = await fetch_local_bytes(original_url)
                if content is None:
                    return ("error", None, f"local-not-found:{original_url}")
            else:
                resp = await http.get(original_url, timeout=40, follow_redirects=True)
                if resp.status_code != 200:
                    return ("error", None, f"http-{resp.status_code}:{original_url}")
                content = resp.content
            with open(dest, "wb") as f:
                f.write(content)
            return ("saved", {"src": src, "original_url": original_url, "file": fname, "size": len(content)}, None)
        except Exception as e:
            return ("error", None, f"{type(e).__name__}:{original_url}")


async def process_product(http: httpx.AsyncClient, product: dict, stats: dict):
    folder = slug_folder(product)
    folder_path = os.path.join(ORIGINALS_DIR, folder)
    os.makedirs(folder_path, exist_ok=True)

    images = product.get("images") or []
    # Sadece string URL'ler (size-table dict görselleri vs. atlanır)
    srcs = [i for i in images if isinstance(i, str) and i.strip()]

    manifest_images = []
    for src in srcs:
        status, info, err = await download_one(http, src, folder_path)
        stats[status] = stats.get(status, 0) + 1
        if info:
            manifest_images.append({**info, "status": status})
        elif err:
            stats.setdefault("errors_list", [])
            if len(stats["errors_list"]) < 30:
                stats["errors_list"].append(err)

    await db.product_image_backups.update_one(
        {"product_id": product.get("id")},
        {"$set": {
            "product_id": product.get("id"),
            "slug": product.get("slug"),
            "name": product.get("name"),
            "folder": folder,
            "folder_path": folder_path,
            "images": manifest_images,
            "image_count": len(manifest_images),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def main():
    total = await db.products.count_documents({})
    print(f"[backup] {total} ürün, görseller yedekleniyor → {ORIGINALS_DIR}", flush=True)
    stats = {}
    done = 0
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 facette-backup"}) as http:
        cursor = db.products.find({}, {"id": 1, "slug": 1, "name": 1, "images": 1})
        async for product in cursor:
            await process_product(http, product, stats)
            done += 1
            if done % 25 == 0 or done == total:
                print(f"[backup] {done}/{total} ürün işlendi | "
                      f"saved={stats.get('saved',0)} exists={stats.get('exists',0)} "
                      f"error={stats.get('error',0)} skip={stats.get('skip',0)}", flush=True)

    print("[backup] TAMAMLANDI", flush=True)
    print(f"[backup] saved={stats.get('saved',0)} exists={stats.get('exists',0)} "
          f"error={stats.get('error',0)} skip={stats.get('skip',0)}", flush=True)
    if stats.get("errors_list"):
        print("[backup] örnek hatalar:", flush=True)
        for e in stats["errors_list"][:15]:
            print("   -", e, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
