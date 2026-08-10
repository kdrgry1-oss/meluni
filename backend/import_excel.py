#!/usr/bin/env python3
"""Import Ticimax Excel products to MongoDB"""
import pandas as pd
import re
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
import uuid
import os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

def slugify(text):
    """Convert text to URL-friendly slug"""
    if not text:
        return ""
    text = str(text).lower()
    # Turkish char conversion
    tr_map = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c', 'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}
    for tr, en in tr_map.items():
        text = text.replace(tr, en)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')

def safe_float(val, default=0):
    try:
        if pd.isna(val):
            return default
        return float(str(val).replace(',', '.'))
    except:
        return default

def safe_int(val, default=0):
    try:
        if pd.isna(val):
            return default
        return int(float(val))
    except:
        return default

def safe_str(val):
    if pd.isna(val):
        return None
    return str(val).strip() if val else None

def parse_variations(var_str):
    """Parse 'Renk Seçiniz;BEJ,Beden Seçiniz;STD' format"""
    variants = []
    if not var_str or pd.isna(var_str):
        return variants
    
    parts = str(var_str).split(',')
    color = None
    size = None
    
    for part in parts:
        if ';' in part:
            key, value = part.split(';', 1)
            if 'renk' in key.lower():
                color = value.strip()
            elif 'beden' in key.lower():
                size = value.strip()
    
    if size:
        variants.append({
            "id": str(uuid.uuid4()),
            "size": size,
            "color": color,
            "stock": 10,
            "sku": f"{size}-{color}" if color else size
        })
    
    return variants

async def import_products():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("Reading Excel file...")
    df = pd.read_excel('/tmp/products.xls', engine='openpyxl')
    print(f"Found {len(df)} products")
    
    # Clear existing products (optional)
    # await db.products.delete_many({})
    
    imported = 0
    updated = 0
    errors = 0
    
    for idx, row in df.iterrows():
        try:
            stock_code = safe_str(row.get('STOKKODU'))
            name = safe_str(row.get('URUNADI'))
            
            if not name:
                continue
            
            slug = slugify(name)
            
            # Check if product exists
            existing = await db.products.find_one({"stock_code": stock_code}) if stock_code else None
            
            product = {
                "id": str(uuid.uuid4()) if not existing else existing.get('id'),
                "name": name,
                "slug": slug,
                "description": safe_str(row.get('ACIKLAMA')),
                "short_description": safe_str(row.get('ONYAZI')),
                "price": safe_float(row.get('SATISFIYATI')),
                "sale_price": safe_float(row.get('INDIRIMLIFIYAT')) or None,
                "category_name": safe_str(row.get('BREADCRUMBKAT')) or "Giyim",
                "brand": safe_str(row.get('MARKA')) or "FACETTE",
                "is_active": safe_int(row.get('KARTAKTIF')) == 1,
                "is_featured": safe_int(row.get('VITRIN')) == 1,
                "is_new": safe_int(row.get('YENIURUN')) == 1,
                "stock": safe_int(row.get('STOKADEDI')),
                "variants": parse_variations(row.get('VARYASYON')),
                
                # Ticimax fields
                "urun_karti_id": safe_str(row.get('URUNKARTIID')),
                "urun_id": safe_str(row.get('URUNID')),
                "stock_code": stock_code,
                "variation_code": safe_str(row.get('VARYASYONKODU')),
                "barcode": safe_str(row.get('BARKOD')),
                "gtip_code": safe_str(row.get('GTIPKODU')),
                "unit": safe_str(row.get('SATISBIRIMI')),
                "keywords": safe_str(row.get('ANAHTARKELIME')),
                "adwords_description": safe_str(row.get('ADWORDSACIKLAMA')),
                "breadcrumb_category": safe_str(row.get('BREADCRUMBKAT')),
                "custom_field_1": safe_str(row.get('OZELALAN1')),
                "custom_field_2": safe_str(row.get('OZELALAN2')),
                "custom_field_3": safe_str(row.get('OZELALAN3')),
                "custom_field_4": safe_str(row.get('OZELALAN4')),
                "custom_field_5": safe_str(row.get('OZELALAN5')),
                "supplier": safe_str(row.get('TEDARIKCI')),
                "max_installment": safe_int(row.get('MAKSTAKSITSAYISI')),
                "is_showcase": safe_int(row.get('VITRIN')) == 1,
                "is_opportunity": safe_int(row.get('FIRSATURUNU')) == 1,
                "is_free_shipping": safe_int(row.get('UCRETSIZKARGO')) == 1,
                "consignment_stock": safe_int(row.get('KONSINYESTOKADEDI')),
                "purchase_price": safe_float(row.get('ALISFIYATI')),
                "market_price": safe_float(row.get('PIYASAFIYATI')),
                "vat_rate": safe_float(row.get('KDVORANI'), 10),
                "vat_included": safe_int(row.get('KDVDAHIL')) == 1,
                "currency": safe_str(row.get('PARABIRIMI')) or "TRY",
                "cargo_weight": safe_float(row.get('KARGOAGIRLIGI')),
                "product_weight": safe_float(row.get('URUNAGIRLIGI')),
                "width": safe_float(row.get('URUNGENISLIK')),
                "depth": safe_float(row.get('URUNDERINLIK')),
                "height": safe_float(row.get('URUNYUKSEKLIK')),
                "min_order_qty": safe_int(row.get('UYEALIMMIN'), 1),
                "max_order_qty": safe_int(row.get('UYEALIMMAKS')),
                "estimated_delivery": safe_str(row.get('TAHMINITESLIMSURESI')),
                "marketplace_active": safe_int(row.get('MARKETPLACEAKTIF')) == 1,
                
                # SEO fields
                "meta_title": safe_str(row.get('SEO_SAYFABASLIK')),
                "meta_description": safe_str(row.get('SEO_SAYFAACIKLAMA')),
                "meta_keywords": safe_str(row.get('SEO_ANAHTARKELIME')),
                
                # Keep existing images if updating
                "images": existing.get('images', []) if existing else [],
                
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "created_at": existing.get('created_at') if existing else datetime.now(timezone.utc).isoformat()
            }
            
            if existing:
                await db.products.update_one(
                    {"id": existing['id']},
                    {"$set": product}
                )
                updated += 1
            else:
                await db.products.insert_one(product)
                imported += 1
            
            if (imported + updated) % 100 == 0:
                print(f"Progress: {imported} imported, {updated} updated")
                
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"Error on row {idx}: {e}")
    
    print(f"\nImport complete!")
    print(f"Imported: {imported}")
    print(f"Updated: {updated}")
    print(f"Errors: {errors}")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(import_products())
