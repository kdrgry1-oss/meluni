"""
Import data from data_export/ JSON files into MongoDB.
Usage: python3 import_data.py
"""
import asyncio
import json
import os

async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv()

    MONGO_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "test_database")

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data_export")
    collections = ["products", "categories", "orders", "banners", "settings", "trendyol_category_mappings"]

    for col_name in collections:
        path = os.path.join(data_dir, f"{col_name}.json")
        if not os.path.exists(path):
            print(f"[SKIP] {col_name}: dosya bulunamadı")
            continue
        with open(path, "r", encoding="utf-8") as f:
            docs = json.load(f)
        if not docs:
            print(f"[SKIP] {col_name}: veri yok")
            continue
        # Insert - skip existing by id
        inserted = 0
        for doc in docs:
            try:
                await db[col_name].update_one(
                    {"id": doc["id"]} if "id" in doc else {"_id": doc.get("_id")},
                    {"$setOnInsert": doc},
                    upsert=True
                )
                inserted += 1
            except Exception as e:
                print(f"  [!] Atlandı: {e}")
        print(f"[OK] {col_name}: {inserted}/{len(docs)} kayıt işlendi")

    client.close()
    print("\nİçe aktarma tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())
