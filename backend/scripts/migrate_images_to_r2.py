"""Mevcut MongoDB'de base64 saklanan görselleri Cloudflare R2'ye taşır.

- db.files içindeki `data_b64` dolu kayıtları R2'ye yükler.
- Kayda `r2_key` + `r2_url` ekler.
- --purge verilirse, başarıyla taşınan kayıtların `data_b64` alanını siler (DB'yi rahatlatır).

Kullanım:
    python -m scripts.migrate_images_to_r2            # taşır, base64'ü korur
    python -m scripts.migrate_images_to_r2 --purge    # taşır + base64'ü temizler
"""
import os
import sys
import base64
import asyncio

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from services import r2_storage as r2  # noqa: E402


async def main(purge: bool):
    if not r2.is_enabled():
        print("R2 yapılandırılmamış. .env içindeki R2_* anahtarlarını kontrol edin.")
        return

    health = r2.health_check()
    if not health.get("ok"):
        print(f"R2 bağlantı hatası: {health.get('error')}")
        return
    print(f"R2 bağlantısı OK → bucket: {health['bucket']}")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    query = {"data_b64": {"$exists": True, "$ne": None}, "r2_url": {"$exists": False}}
    total = await db.files.count_documents(query)
    print(f"Taşınacak görsel sayısı: {total}")

    migrated, failed = 0, 0
    cursor = db.files.find(query)
    async for doc in cursor:
        path = doc.get("storage_path") or f"{doc['id']}"
        ctype = doc.get("content_type", "image/jpeg")
        try:
            data = base64.b64decode(doc["data_b64"])
            key = f"uploads/{path}"
            url = r2.put_object(key, data, ctype)
            update = {"$set": {"r2_key": key, "r2_url": url}}
            if purge:
                update["$unset"] = {"data_b64": ""}
            await db.files.update_one({"_id": doc["_id"]}, update)
            migrated += 1
            print(f"  ✓ {path} → {url}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {path}: {e}")

    print(f"\nBitti. Taşınan: {migrated}, Başarısız: {failed}" + (" (base64 temizlendi)" if purge else ""))


if __name__ == "__main__":
    asyncio.run(main(purge="--purge" in sys.argv))
