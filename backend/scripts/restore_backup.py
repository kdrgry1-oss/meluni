"""
align_to_ticimax_349.py ile silinen ürünleri yedek JSON'dan geri yükler.

Kullanım:
  python3 restore_backup.py backups/deleted_products_YYYYMMDD_HHMMSS.json
"""
import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main(path):
    with open(path, encoding="utf-8") as f:
        docs = json.load(f)
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    restored = 0
    for d in docs:
        exists = await db.products.find_one({"id": d["id"]}, {"id": 1})
        if exists:
            continue
        await db.products.insert_one(d)
        restored += 1
    print(f"Geri yüklendi: {restored} / {len(docs)} (zaten var olanlar atlandı)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
