import asyncio
import random
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

async def generate_unique_short_id(db, collection_name: str, used_ids: set) -> str:
    """Generate a unique 4-digit numeric ID string"""
    for _ in range(500):
        new_id = str(random.randint(1000, 9999))
        if new_id in used_ids:
            continue
        existing = await db[collection_name].find_one({"id": new_id}, {"_id": 1})
        if not existing:
            used_ids.add(new_id)
            return new_id
    raise Exception(f"Failed to generate unique 4-digit ID for {collection_name}")

async def main():
    load_dotenv()
    MONGO_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "test_database")

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print("--- CATEGORY MIGRATION ---")
    categories = await db.categories.find({}).to_list(None)
    cat_id_mapping = {}  # old_id -> new_id
    used_cat_ids = set()

    for cat in categories:
        old_id = cat.get("id")
        if not old_id or len(str(old_id)) == 4:
            continue
        new_id = await generate_unique_short_id(db, "categories", used_cat_ids)
        cat_id_mapping[old_id] = new_id

    # Apply category mapping
    for old_id, new_id in cat_id_mapping.items():
        # Update the category itself
        await db.categories.update_one({"id": old_id}, {"$set": {"id": new_id}})
        # Update references in categories
        await db.categories.update_many({"parent_id": old_id}, {"$set": {"parent_id": new_id}})
        # Update references in products
        await db.products.update_many({"category_id": old_id}, {"$set": {"category_id": new_id}})
        # Update references in trendyol_category_mappings
        await db.trendyol_category_mappings.update_many({"local_category_id": old_id}, {"$set": {"local_category_id": new_id}})
    
    print(f"Updated {len(cat_id_mapping)} categories to 4-digit IDs.")

    print("--- PRODUCT MIGRATION ---")
    products = await db.products.find({}).to_list(None)
    prod_id_mapping = {}
    used_prod_ids = set()

    for p in products:
        old_id = p.get("id")
        if not old_id or len(str(old_id)) == 4:
            continue
        new_id = await generate_unique_short_id(db, "products", used_prod_ids)
        prod_id_mapping[old_id] = new_id

    # Apply product mapping
    for old_id, new_id in prod_id_mapping.items():
        # Update the product itself
        await db.products.update_one({"id": old_id}, {"$set": {"id": new_id}})
        # No other direct references to product ID in the system currently to update
        # Orders usually copy the product details dynamically, we can skip retrospectively updating orders unless required.
    
    print(f"Updated {len(prod_id_mapping)} products to 4-digit IDs.")

    client.close()
    print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(main())
