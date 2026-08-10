import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
import pandas as pd
from dotenv import load_dotenv

async def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), "backend", ".env"))
    db = AsyncIOMotorClient(os.getenv("MONGODB_URL", "mongodb://localhost:27017"))["test_database"]
    
    df = pd.read_excel('test_attributes.xlsx')
    standard_columns = {"Stok Kodu", "Renk", "Beden", "Barkod"}
    
    attribute_columns = [col for col in df.columns if str(col).strip() not in standard_columns]
    print(f"Detected attribute columns: {attribute_columns}")
    
    updated_count = 0
    not_found_count = 0
    
    for _, row in df.iterrows():
        stock_code = str(row.get("Stok Kodu", "")).strip()
        color = str(row.get("Renk", "")).strip()
        size = str(row.get("Beden", "")).strip()
        
        parsed_attrs = []
        for col in attribute_columns:
            val = str(row.get(col, "")).strip()
            if val and val != "nan":
                parsed_attrs.append({"name": str(col).strip(), "value": val})
                
        if not parsed_attrs:
            continue
            
        # Find the product by variant match
        query = {
            "variants": {
                "$elemMatch": {
                    "stock_code": stock_code
                }
            }
        }
        
        if color and color != "nan":
            query["variants"]["$elemMatch"]["color"] = color
        if size and size != "nan":
            query["variants"]["$elemMatch"]["size"] = size
            
        # Update product attributes
        existing = await db.products.find_one(query)
        if existing:
            # Check if this attribute already exists for the product, update or push
            current_attrs = existing.get("attributes", [])
            
            # Convert to dictionary for easy updating by name
            attr_dict = {a["name"]: a["value"] for a in current_attrs if "name" in a}
            
            # Update with new attributes
            for attr in parsed_attrs:
                attr_dict[attr["name"]] = attr["value"]
                
            # Re-convert to list
            new_attrs = [{"name": k, "value": v} for k, v in attr_dict.items()]
            
            await db.products.update_one(
                {"_id": existing["_id"]},
                {"$set": {"attributes": new_attrs}}
            )
            updated_count += 1
            print(f"[OK] Assigned attributes {parsed_attrs} to product '{existing['name']}' (Stock Code: {stock_code})")
        else:
            not_found_count += 1
            print(f"[ERROR] Product not found for Stock Code: {stock_code}, Color: {color}, Size: {size}")
            
    print(f"\nSuccessfully updated attributes for {updated_count} products.")
    if not_found_count > 0:
        print(f"Could not find matching products for {not_found_count} rows.")

if __name__ == "__main__":
    asyncio.run(main())
