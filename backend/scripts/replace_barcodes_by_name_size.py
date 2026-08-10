"""
TicimaxExport v4 — Ürün adı + beden referansıyla BARKOD'ları günceller.

Excel kolonları: URUNKARTIID, URUNID, STOKKODU, BARKOD, URUNADI, BEDEN
Match key: (URUNADI normalized, BEDEN normalized) → BARKOD/URUNID
DB'de aynı (product.name, variant.size) kombolu varyantın:
  - variant.barcode  ← Excel BARKOD
  - variant.sku      ← Excel BARKOD (genelde aynı)
  - variant.urun_id  ← Excel URUNID

Ayrıca parent: product.urun_karti_id ← URUNKARTIID, product.stock_code ← STOKKODU
"""
import asyncio
import os
import re
import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _norm(s):
    """Case-insensitive, whitespace-collapsed."""
    if not s: return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _bc(v):
    if pd.isna(v): return ""
    try: return str(int(float(v)))
    except: return str(v).strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    print(f"Excel satır: {len(df)}, kolon: {list(df.columns)}")

    # (name_norm, size_norm) -> (barcode, urun_id, kart_id, stockkodu)
    key_map = {}
    for _, row in df.iterrows():
        name = str(row.get("URUNADI") or "").strip()
        size = str(row.get("BEDEN") or "").strip()
        bc = _bc(row.get("BARKOD"))
        if not name or not bc:
            continue
        key = (_norm(name), _norm(size))
        try:
            urun_id = str(int(float(row["URUNID"]))) if pd.notna(row.get("URUNID")) else None
        except: urun_id = None
        try:
            kart_id = str(int(float(row["URUNKARTIID"]))) if pd.notna(row.get("URUNKARTIID")) else None
        except: kart_id = None
        sc = str(row.get("STOKKODU") or "").strip()
        # Aynı key birden çok satırda varsa son satır kazanır (Excel duplikatlarına karşı)
        key_map[key] = {"barcode": bc, "urun_id": urun_id, "kart_id": kart_id, "stock_code": sc, "name": name, "size": size}

    print(f"Unique (name, size) anahtarları: {len(key_map)}")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Match: her DB ürünü için variants[]'ı dolaş, (product.name, variant.size) ile eşleştir
    updated_variants = 0
    updated_products = 0
    matched_keys = set()
    unmatched_db_variants = 0

    async for p in db.products.find({}, {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "variants": 1, "urun_karti_id": 1}):
        name_n = _norm(p.get("name"))
        if not name_n:
            continue
        set_doc = {}
        for i, v in enumerate(p.get("variants") or []):
            size_n = _norm(v.get("size") or "STD")
            key = (name_n, size_n)
            info = key_map.get(key)
            if not info:
                # Try without size (single-size product)
                key2 = (name_n, _norm("STD"))
                info = key_map.get(key2) if size_n == "" else None
            if not info:
                unmatched_db_variants += 1
                continue
            matched_keys.add(key)
            # Update variant barcode
            if info["barcode"] and v.get("barcode") != info["barcode"]:
                set_doc[f"variants.{i}.barcode"] = info["barcode"]
                set_doc[f"variants.{i}.sku"] = info["barcode"]
                updated_variants += 1
            # Update variant urun_id
            if info.get("urun_id") and v.get("urun_id") != info["urun_id"]:
                set_doc[f"variants.{i}.urun_id"] = info["urun_id"]
        # Parent updates
        # urun_karti_id: any info card_id we matched in this product
        any_info = None
        for v in p.get("variants") or []:
            size_n = _norm(v.get("size") or "STD")
            k = (name_n, size_n)
            if k in key_map:
                any_info = key_map[k]; break
        if any_info:
            if any_info.get("kart_id") and p.get("urun_karti_id") != any_info["kart_id"]:
                set_doc["urun_karti_id"] = any_info["kart_id"]
        if set_doc:
            updated_products += 1
            if args.apply:
                await db.products.update_one({"id": p["id"]}, {"$set": set_doc})

    unmatched_excel = [k for k in key_map if k not in matched_keys]
    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}:")
    print(f"  Güncellenen varyant barkod: {updated_variants}")
    print(f"  Güncellenen ürün: {updated_products}")
    print(f"  DB'de Excel'de yok olan varyant: {unmatched_db_variants}")
    print(f"  Excel'de var ama DB'de eşleşmeyen anahtarlar: {len(unmatched_excel)}")
    for k in unmatched_excel[:10]:
        info = key_map[k]
        print(f"    - {info['name']!r} | size={info['size']} | bc={info['barcode']}")


if __name__ == "__main__":
    asyncio.run(main())
