#!/usr/bin/env python3
"""Import Ticimax Excel products to MongoDB with proper variants"""
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
    """Convert text to URL-friendly slug"""
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
    # Remove .0 from numeric strings
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

def get_base_product_name(name):
    """Get base product name without color/size suffix"""
    if not name:
        return name
    
    # Common color suffixes to remove
    colors = ['BEJ', 'HAKİ', 'HAKI', 'SİYAH', 'SIYAH', 'CAMEL', 'GRİ', 'GRI', 'KAHVERENGİ', 'KAHVERENGI', 
              'BEYAZ', 'MAVİ', 'MAVI', 'KIRMIZI', 'YEŞİL', 'YESIL', 'PEMBE', 'MOR', 'TURUNCU', 'EKRU',
              'KREM', 'ANTRASİT', 'ANTRASIT', 'LACİVERT', 'LACIVERT', 'BORDO', 'VİZON', 'VIZON',
              'ÇOK RENK', 'COK RENK', 'BUZ MAVİSİ', 'BUZ MAVISI', 'ACIK KAHVE', 'ACI KAHVE']
    
    # Remove color suffix from end of name
    name_upper = name.upper()
    for color in sorted(colors, key=len, reverse=True):
        if name_upper.endswith(' ' + color):
            return name[:-len(color)-1].strip()
    
    return name

async def import_products_with_variants():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("Reading Excel file...")
    df = pd.read_excel('/tmp/ticimax_export.xls')
    print(f"Found {len(df)} rows in Excel")
    
    # Group products by base name to collect variants
    product_groups = defaultdict(list)
    
    for idx, row in df.iterrows():
        name = safe_str(row.get('URUNADI'))
        if not name:
            continue
        
        base_name = get_base_product_name(name)
        product_groups[base_name].append(row)
    
    print(f"Found {len(product_groups)} unique base products")
    
    # GÜVENLİK KİLİDİ: kazara çalıştırmada TÜM ürünleri silmesin (canlı katalog koruması).
    # Yalnızca ALLOW_WIPE_IMPORT=1 açıkça verilirse siler.
    if os.getenv("ALLOW_WIPE_IMPORT") != "1":
        print("GUVENLIK: ALLOW_WIPE_IMPORT=1 verilmedi — TUM urunleri silen import iptal edildi.")
        return
    await db.products.delete_many({})
    print("Cleared existing products")
    
    imported = 0
    
    for base_name, rows in product_groups.items():
        try:
            # Use first row for base product data
            first_row = rows[0]
            
            # Get all unique images from all variants
            all_images = set()
            variants = []
            
            for row in rows:
                # Parse variation info
                var_str = safe_str(row.get('VARYASYON'))
                color, size = parse_variation(var_str)
                
                # Create variant
                variant = {
                    "id": str(uuid.uuid4()),
                    "urun_id": safe_str(row.get('URUNID')),
                    "urun_karti_id": safe_str(row.get('URUNKARTIID')),
                    "stock_code": safe_str(row.get('STOKKODU')),
                    "barcode": safe_str(row.get('BARKOD')),
                    "variation_code": safe_str(row.get('VARYASYONKODU')),
                    "color": color,
                    "size": size,
                    "stock": safe_int(row.get('STOKADEDI')),
                    "price": safe_float(row.get('SATISFIYATI')),
                    "sale_price": safe_float(row.get('INDIRIMLIFIYAT')) or None,
                    "is_active": safe_int(row.get('URUNAKTIF')) == 1
                }
                variants.append(variant)
            
            # Use first variant's stock code as main product stock code
            main_stock_code = variants[0]['stock_code'] if variants else None
            main_barcode = variants[0]['barcode'] if variants else None
            
            # Calculate total stock from all variants
            total_stock = sum(v['stock'] for v in variants)
            
            # Get unique sizes and colors
            sizes = list(set([v['size'] for v in variants if v['size']]))
            colors = list(set([v['color'] for v in variants if v['color']]))
            
            slug = slugify(base_name)
            
            product = {
                "id": str(uuid.uuid4()),
                "name": base_name,
                "slug": slug,
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
                
                # Main product codes (from first variant)
                "stock_code": main_stock_code,
                "barcode": main_barcode,
                "urun_karti_id": safe_str(first_row.get('URUNKARTIID')),
                
                # Variants with their own stock codes and barcodes
                "variants": variants,
                
                # Available options
                "available_sizes": sizes,
                "available_colors": colors,
                
                # Additional fields
                "keywords": safe_str(first_row.get('ANAHTARKELIME')),
                "supplier": safe_str(first_row.get('TEDARIKCI')),
                "vat_rate": safe_float(first_row.get('KDVORANI'), 10),
                "vat_included": safe_int(first_row.get('KDVDAHIL')) == 1,
                "currency": safe_str(first_row.get('PARABIRIMI')) or "TRY",
                "cargo_weight": safe_float(first_row.get('KARGOAGIRLIGI')),
                "product_weight": safe_float(first_row.get('URUNAGIRLIGI')),
                "width": safe_float(first_row.get('URUNGENISLIK')),
                "depth": safe_float(first_row.get('URUNDERINLIK')),
                "height": safe_float(first_row.get('URUNYUKSEKLIK')),
                "is_free_shipping": safe_int(first_row.get('UCRETSIZKARGO')) == 1,
                "marketplace_active": safe_int(first_row.get('MARKETPLACEAKTIF')) == 1,
                
                # SEO
                "meta_title": safe_str(first_row.get('SEO_SAYFABASLIK')),
                "meta_description": safe_str(first_row.get('SEO_SAYFAACIKLAMA')),
                "meta_keywords": safe_str(first_row.get('SEO_ANAHTARKELIME')),
                
                # Keep images empty for now - will be fetched from XML
                "images": [],
                
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            await db.products.insert_one(product)
            imported += 1
            
            if imported % 50 == 0:
                print(f"Progress: {imported} products imported")
                
        except Exception as e:
            print(f"Error on product {base_name}: {e}")
    
    print(f"\n=== Import Complete ===")
    print(f"Imported: {imported} products")
    
    # Show sample
    sample = await db.products.find({}, {"_id": 0, "name": 1, "stock_code": 1, "barcode": 1, "variants": 1}).limit(3).to_list(3)
    print("\n=== Sample Products ===")
    for p in sample:
        print(f"\nProduct: {p.get('name')}")
        print(f"  Stock Code: {p.get('stock_code')}")
        print(f"  Barcode: {p.get('barcode')}")
        print(f"  Variants ({len(p.get('variants', []))}):")
        for v in p.get('variants', [])[:3]:
            print(f"    - Size: {v.get('size')}, Color: {v.get('color')}")
            print(f"      Stock Code: {v.get('stock_code')}, Barcode: {v.get('barcode')}")
            print(f"      Stock: {v.get('stock')}")
    
    client.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv('/app/backend/.env')
    asyncio.run(import_products_with_variants())
