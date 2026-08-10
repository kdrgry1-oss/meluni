"""
İkinci Ticimax Excel re-sync:
  - BARKOD → variant.size (Excel'de gerçek beden varsa ÖNCELİKLİ)
  - BARKOD → product.member_price_1 (UYETIPIFIYAT1)

Beden whitelist: STD, XS-XXXL, numeric (35-38 gibi), slash kombo (XS/S, M/L).
Gerçek beden DEĞİLse mevcut DB değerine dokunulmaz.
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


SIZE_TOKENS = {
    "STD", "STANDART", "STANDARD", "TEK", "TEK BEDEN",
    "XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL",
    "FREESIZE", "FREE", "OS", "ONE SIZE",
}


def _is_size(s):
    if not s:
        return False
    up = str(s).upper().strip()
    if re.fullmatch(r"\d{2,3}([\-/]\d{2,3})?", up):
        return True
    tokens = re.split(r"[/\-]", up)
    if tokens and all(t.strip() in SIZE_TOKENS for t in tokens if t.strip()):
        return True
    return up in SIZE_TOKENS


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    print(f"Excel satır: {len(df)}, kolon: {list(df.columns)}")

    def _bc(v):
        if pd.isna(v):
            return ""
        try:
            return str(int(float(v)))
        except Exception:
            return str(v).strip()
    df["BARKOD_str"] = df["BARKOD"].apply(_bc)

    # Excel: barcode -> (size_if_real, member_price_1)
    bc_map = {}
    for _, row in df.iterrows():
        bc = row["BARKOD_str"]
        if not bc:
            continue
        beden = str(row.get("Beden") or "").strip()
        size = beden if _is_size(beden) else None
        mp1 = None
        try:
            mp1 = float(row["UYETIPIFIYAT1"]) if pd.notna(row.get("UYETIPIFIYAT1")) else None
        except Exception:
            mp1 = None
        bc_map[bc] = (size, mp1)

    real_size_count = sum(1 for _, (s, _) in bc_map.items() if s)
    print(f"Excel'de unique barkod: {len(bc_map)} | Gerçek beden veren: {real_size_count}")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    updated_products = 0
    updated_size = 0
    updated_price = 0
    async for p in db.products.find({}, {"_id": 0, "id": 1, "stock_code": 1, "variants": 1, "member_price_1": 1}):
        set_doc = {}
        # Variant size (Excel gerçek beden veriyorsa öncelikli)
        for i, v in enumerate(p.get("variants") or []):
            bc = str(v.get("barcode") or "")
            if not bc or bc not in bc_map:
                continue
            new_size, _ = bc_map[bc]
            if new_size and v.get("size") != new_size:
                set_doc[f"variants.{i}.size"] = new_size
                updated_size += 1
        # member_price_1: aynı parent altındaki herhangi bir varyantın barkodunun mp1'i parent'a yazılır
        new_mp1 = None
        for v in (p.get("variants") or []):
            bc = str(v.get("barcode") or "")
            if bc and bc in bc_map and bc_map[bc][1] is not None:
                new_mp1 = bc_map[bc][1]
                break
        if new_mp1 is not None and p.get("member_price_1") != new_mp1:
            set_doc["member_price_1"] = new_mp1
            updated_price += 1
        if set_doc:
            if args.apply:
                await db.products.update_one({"id": p["id"]}, {"$set": set_doc})
            updated_products += 1

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {updated_products} ürün etkilenecek "
          f"({updated_size} varyant beden + {updated_price} parent member_price_1)")


if __name__ == "__main__":
    asyncio.run(main())
