import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

async def main():
    load_dotenv(os.path.join("/Users/kdrgry/.gemini/antigravity/playground/ruby-nova/backend", ".env"))
    db = AsyncIOMotorClient(os.getenv("MONGODB_URL", "mongodb://localhost:27017"))["test_database"]
    products = await db.products.find({}, {"_id": 1, "id": 1, "name": 1, "attributes": 1}).to_list(None)
    
    updated_count = 0
    
    for p in products:
        name = str(p.get("name", "")).lower()
        new_attrs = {}
        
        # Analyze name for Kumaş
        if "triko" in name:
            new_attrs["Kumaş"] = "Triko"
        elif "kot" in name or "jean" in name or "denim" in name:
            new_attrs["Kumaş"] = "Denim"
        elif "keten" in name:
            new_attrs["Kumaş"] = "Keten"
        elif "pamuk" in name or "cotton" in name:
            new_attrs["Kumaş"] = "Pamuklu"
        elif "saten" in name:
            new_attrs["Kumaş"] = "Saten"
            
        # Analyze name for Kalıp
        if "oversize" in name:
            new_attrs["Kalıp"] = "Oversize"
        elif "slim" in name or "dar" in name:
            new_attrs["Kalıp"] = "Slim Fit"
        elif "havuç" in name or "carrot" in name:
            new_attrs["Kalıp"] = "Havuç Kalıp"
        elif "regular" in name or "standart" in name:
            new_attrs["Kalıp"] = "Regular"
            
        # Analyze name for Yaka Tipi
        if "bisiklet yaka" in name:
            new_attrs["Yaka Tipi"] = "Bisiklet Yaka"
        elif "v yaka" in name:
            new_attrs["Yaka Tipi"] = "V Yaka"
        elif "polo" in name:
            new_attrs["Yaka Tipi"] = "Polo Yaka"
        elif "balıkçı" in name:
            new_attrs["Yaka Tipi"] = "Balıkçı Yaka"
            
        # Detailed item type
        if "pantolon" in name:
            new_attrs["Ürün Tipi"] = "Pantolon"
        elif "kazak" in name:
            new_attrs["Ürün Tipi"] = "Kazak"
        elif "ceket" in name:
            new_attrs["Ürün Tipi"] = "Ceket"
        elif "gömlek" in name:
            new_attrs["Ürün Tipi"] = "Gömlek"
        elif "t-shirt" in name or "tişört" in name:
            new_attrs["Ürün Tipi"] = "T-Shirt"
            
        # Details
        if "cepli" in name:
            new_attrs["Detay"] = "Cepli"
        elif "pile" in name:
            new_attrs["Detay"] = "Pileli"
        elif "çıtçıtlı" in name:
            new_attrs["Detay"] = "Çıtçıtlı"
            
        if not new_attrs:
            # Add a generic attribute so it's not totally empty
            new_attrs["Materyal"] = "Karışımlı"
            
        # Keep existing attrs, override with new
        current_attrs = {a["name"]: a["value"] for a in p.get("attributes", []) if "name" in a}
        current_attrs.update(new_attrs)
        
        final_attrs = [{"name": k, "value": v} for k, v in current_attrs.items()]
        
        await db.products.update_one(
            {"_id": p["_id"]},
            {"$set": {"attributes": final_attrs}}
        )
        updated_count += 1
        
    print(f"Successfully auto-assigned attributes to {updated_count} products.")

if __name__ == "__main__":
    asyncio.run(main())
