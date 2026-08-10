#!/usr/bin/env python3
"""Import Ticimax Excel - Each color is a product, sizes are variants"""
import pandas as pd
import re
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
import uuid
import os
from collections import defaultdict

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

def slugify(text):
    if not text:
        return ""
    text = str(text).lower()
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
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s if s else None

def parse_variation(var_str):
    """Parse 'Renk Seçiniz;BEJ,Beden Seçiniz;STD' format"""
    color = None
    size = None
    
    if not var_str or pd.isna(var_str):
        return color, size
    
    parts = str(var_str).split(',')
    for part in parts:
        if ';' in part:
            key, value = part.split(';', 1)
            key = key.lower().strip()
            value = value.strip()
            if 'renk' in key:
                color = value
            elif 'beden' in key:
                size = value
    
    return color, size

async def import_products_color_based():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("Reading Excel file...")
    df = pd.read_excel('/tmp/ticimax_export.xls')
    print(f"Found {len(df)} rows in Excel")
    
    # Group by URUNADI (product name includes color already)
    # Each unique URUNADI becomes a product, sizes become variants
    product_groups = defaultdict(list)
    
    for idx, row in df.iterrows():
        name = safe_str(row.get('URUNADI'))
        if not name:
            continue
        
        # URUNADI already includes color (e.g., "Tina Straight Fit Jean MAVİ")
        product_groups[name].append(row)
    
    print(f"Found {len(product_groups)} unique products (each color is separate)")
    
    # GÜVENLİK KİLİDİ: bu tek-seferlik migration betiği TÜM ürünleri siliyordu (delete_many({})).
    # Kazara çalıştırma canlı katalogu yok ediyordu ("ürünler kayboldu"). Artık yalnızca
    # ALLOW_WIPE_IMPORT=1 ortam değişkeni AÇIKÇA verilirse siler; aksi halde durur.
    if os.getenv("ALLOW_WIPE_IMPORT") != "1":
        print("GUVENLIK: ALLOW_WIPE_IMPORT=1 verilmedi — TUM urunleri silen import iptal edildi.")
        return
    await db.products.delete_many({})
    print("Cleared existing products")
    
    imported = 0
    
    for product_name, rows in product_groups.items():
        try:
            first_row = rows[0]
            
            # Parse color from first row's variation
            var_str = safe_str(first_row.get('VARYASYON'))
            product_color, _ = parse_variation(var_str)
            
            # Variants are ONLY sizes
            variants = []
            for row in rows:
                var_str = safe_str(row.get('VARYASYON'))
                color, size = parse_variation(var_str)
                
                # Skip if no size (shouldn't happen but just in case)
                if not size:
                    size = "STD"
                
                variant = {
                    "id": str(uuid.uuid4()),
                    "size": size,
                    "stock_code": safe_str(row.get('STOKKODU')),
                    "barcode": safe_str(row.get('BARKOD')),
                    "variation_code": safe_str(row.get('VARYASYONKODU')),
                    "urun_id": safe_str(row.get('URUNID')),
                    "stock": safe_int(row.get('STOKADEDI')),
                    "price": safe_float(row.get('SATISFIYATI')),
                    "sale_price": safe_float(row.get('INDIRIMLIFIYAT')) or None,
                    "is_active": safe_int(row.get('URUNAKTIF')) == 1
                }
                variants.append(variant)
            
            # Sort variants by size order
            size_order = {'XS': 0, 'S': 1, 'M': 2, 'L': 3, 'XL': 4, 'XXL': 5, 'STD': 6, '36': 10, '38': 11, '40': 12, '42': 13, '44': 14}
            variants.sort(key=lambda v: size_order.get(v['size'], 99))
            
            # Use first variant's data as main product codes
            main_stock_code = variants[0]['stock_code'] if variants else None
            main_barcode = variants[0]['barcode'] if variants else None
            
            # Total stock from all size variants
            total_stock = sum(v['stock'] for v in variants)
            
            # Available sizes
            available_sizes = [v['size'] for v in variants]
            
            slug = slugify(product_name)
            
            product = {
                "id": str(uuid.uuid4()),
                "name": product_name,
                "slug": slug,
                "color": product_color,  # Store color at product level
                "description": safe_str(first_row.get('ACIKLAMA')),
                "short_description": safe_str(first_row.get('ONYAZI')),
                "price": safe_float(first_row.get('SATISFIYATI')),
                "sale_price": safe_float(first_row.get('INDIRIMLIFIYAT')) or None,
                "category_name": safe_str(first_row.get('BREADCRUMBKAT')) or "Giyim",
                "brand": safe_str(first_row.get('MARKA')) or "FACETTE",
                "is_active": safe_int(first_row.get('KARTAKTIF')) == 1,
                "is_featured": safe_int(first_row.get('VITRIN')) == 1,
                "is_new": safe_int(first_row.get('YENIURUN')) == 1,
                "stock": total_stock,
                
                # Main product codes
                "stock_code": main_stock_code,
                "barcode": main_barcode,
                "urun_karti_id": safe_str(first_row.get('URUNKARTIID')),
                
                # Variants - ONLY sizes
                "variants": variants,
                "available_sizes": available_sizes,
                
                # Additional fields
                "keywords": safe_str(first_row.get('ANAHTARKELIME')),
                "supplier": safe_str(first_row.get('TEDARIKCI')),
                "vat_rate": safe_float(first_row.get('KDVORANI'), 10),
                "vat_included": safe_int(first_row.get('KDVDAHIL')) == 1,
                "currency": safe_str(first_row.get('PARABIRIMI')) or "TRY",
                "cargo_weight": safe_float(first_row.get('KARGOAGIRLIGI')),
                
                # SEO
                "meta_title": safe_str(first_row.get('SEO_SAYFABASLIK')),
                "meta_description": safe_str(first_row.get('SEO_SAYFAACIKLAMA')),
                
                "images": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            await db.products.insert_one(product)
            imported += 1
            
            if imported % 50 == 0:
                print(f"Progress: {imported} products imported")
                
        except Exception as e:
            print(f"Error on product {product_name}: {e}")
    
    print(f"\n=== Import Complete ===")
    print(f"Imported: {imported} products")
    
    # Show sample
    sample = await db.products.find({}, {"_id": 0, "name": 1, "color": 1, "stock_code": 1, "variants": 1}).limit(3).to_list(3)
    print("\n=== Sample Products ===")
    for p in sample:
        print(f"\nProduct: {p.get('name')}")
        print(f"  Color: {p.get('color')}")
        print(f"  Stock Code: {p.get('stock_code')}")
        print(f"  Variants (sizes only):")
        for v in p.get('variants', []):
            print(f"    - {v.get('size')}: Stock Code={v.get('stock_code')}, Barcode={v.get('barcode')}, Stock={v.get('stock')}")
    
    client.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv('/app/backend/.env')
    asyncio.run(import_products_color_based())
