"""
3. Ticimax Excel — BARKOD → BEDEN direkt eşleştirme.

Bu Excel'de Beden değeri "Unnamed: 7" kolonunda (Excel header'da boş bırakılmış).
Pattern tahminine GEREK YOK — Excel BARKOD-spesifik gerçek bedeni veriyor.

Kullanım:
    python3 -m scripts.sync_ticimax_v3_sizes /tmp/ticimax3.xlsx --apply
"""
import asyncio
import os
import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    print(f"Excel satır: {len(df)}, kolon: {list(df.columns)}")

    # Beden değeri "Unnamed: 7" kolonunda (header satırı boştu)
    size_col = None
    for c in df.columns:
        if "unnamed" in str(c).lower() or str(c).lower().strip() == "beden":
            size_col = c
            break
    if size_col is None:
        # Yine de fallback: VARYASYON sütununda "Beden" yazıyorsa son kolon beden
        size_col = df.columns[-1]
    print(f"Beden kolonu: {size_col!r}")

    def _bc(v):
        if pd.isna(v):
            return ""
        try:
            return str(int(float(v)))
        except Exception:
            return str(v).strip()
    df["BARKOD_str"] = df["BARKOD"].apply(_bc)

    # barcode -> size (Excel'den direkt)
    bc_size = {}
    for _, row in df.iterrows():
        bc = row["BARKOD_str"]
        sz = row.get(size_col)
        if not bc or pd.isna(sz):
            continue
        sz = str(sz).strip()
        if sz:
            bc_size[bc] = sz

    print(f"Excel'de {len(bc_size)} barkod için beden eşleşmesi var.")
    # Beden dağılımı
    from collections import Counter
    cnt = Counter(bc_size.values())
    print("Beden dağılımı:")
    for k, v in cnt.most_common(15):
        print(f"  {k}: {v}")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    updated_products = 0
    updated_variants = 0
    async for p in db.products.find({}, {"_id": 0, "id": 1, "stock_code": 1, "variants": 1}):
        set_doc = {}
        for i, v in enumerate(p.get("variants") or []):
            bc = str(v.get("barcode") or "")
            if not bc or bc not in bc_size:
                continue
            new_size = bc_size[bc]
            if v.get("size") != new_size:
                set_doc[f"variants.{i}.size"] = new_size
                updated_variants += 1
        if set_doc:
            if args.apply:
                await db.products.update_one({"id": p["id"]}, {"$set": set_doc})
            updated_products += 1

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {updated_products} ürün, {updated_variants} varyant beden güncellendi.")


if __name__ == "__main__":
    asyncio.run(main())
