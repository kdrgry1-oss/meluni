"""
Export products, categories and orders from MongoDB to JSON files for GitHub.
"""
import asyncio
import json
import os
from datetime import datetime

async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv()

    MONGO_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "test_database")

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    output_dir = os.path.join(os.path.dirname(__file__), "..", "data_export")
    os.makedirs(output_dir, exist_ok=True)

    collections = ["products", "categories", "orders", "banners", "settings", "trendyol_category_mappings"]

    for col_name in collections:
        docs = await db[col_name].find({}, {"_id": 0}).to_list(None)
        path = os.path.join(output_dir, f"{col_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False, indent=2, default=str)
        print(f"[OK] {col_name}: {len(docs)} kayıt -> {path}")

    client.close()
    print(f"\nTüm veriler data_export/ klasörüne aktarıldı.")

if __name__ == "__main__":
    asyncio.run(main())
