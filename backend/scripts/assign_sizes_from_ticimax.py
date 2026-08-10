"""
Excel'deki BARKOD-URUNKARTIID eşleşmesi + URUNID sırasını kullanarak
varyantlara standart beden ataması yapar.

MANTIK:
  Aynı URUNKARTIID altındaki birden çok URUNID, aynı ürünün farklı bedenleri
  olarak yorumlanır. URUNID artan sıraya göre küçük → büyük beden:

  1 variant → ["STD"]
  2 variant → ["S", "M"]
  3 variant → ["S", "M", "L"]
  4 variant → ["XS", "S", "M", "L"]   (en yaygın fashion)
  5 variant → ["XS", "S", "M", "L", "XL"]
  6 variant → ["XXS", "XS", "S", "M", "L", "XL"]
  7 variant → ["XXS", "XS", "S", "M", "L", "XL", "XXL"]

Excel'de Beden sütununda GERÇEK BEDEN (S/M/L/XL/STD/numeric) varsa,
onu KORUR (Excel öncelikli, varsayım yedek).

Kullanım:
    python3 -m scripts.assign_sizes_from_ticimax /tmp/ticimax.xlsx --apply
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
    if re.fullmatch(r"\d{2,3}(/\d{2,3})?", up):
        return True
    tokens = re.split(r"[/\-]", up)
    if tokens and all(t in SIZE_TOKENS for t in tokens if t):
        return True
    return up in SIZE_TOKENS


SIZE_PATTERNS = {
    1: ["STD"],
    2: ["S", "M"],
    3: ["S", "M", "L"],
    4: ["XS", "S", "M", "L"],
    5: ["XS", "S", "M", "L", "XL"],
    6: ["XXS", "XS", "S", "M", "L", "XL"],
    7: ["XXS", "XS", "S", "M", "L", "XL", "XXL"],
    8: ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"],
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")

    def _bc(v):
        if pd.isna(v):
            return ""
        try:
            return str(int(float(v)))
        except Exception:
            return str(v).strip()
    df["BARKOD_str"] = df["BARKOD"].apply(_bc)

    # Group by KARTI ID, sort by URUNID
    barcode_to_assigned = {}  # barcode -> (kart_id, assigned_size, source)
    for kid, group in df.groupby("URUNKARTIID"):
        rows = group.sort_values("URUNID").to_dict("records")
        n = len(rows)
        pattern = SIZE_PATTERNS.get(min(n, 8), ["STD"] * n)
        if n > 8:
            pattern = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"] + ["STD"] * (n - 8)
        for idx, row in enumerate(rows):
            bc = row["BARKOD_str"]
            if not bc:
                continue
            # Excel Beden gerçek beden mi?
            excel_beden = str(row.get("Beden") or "").strip()
            if _is_size(excel_beden):
                assigned = excel_beden  # Excel'in gerçek bedeni öncelikli
                src = "excel"
            else:
                assigned = pattern[idx] if idx < len(pattern) else "STD"
                src = "pattern"
            barcode_to_assigned[bc] = (kid, assigned, src)

    print(f"Excel'de {len(barcode_to_assigned)} barkoda beden ataması üretildi.")
    src_counts = {}
    for _, (_, _, src) in barcode_to_assigned.items():
        src_counts[src] = src_counts.get(src, 0) + 1
    print(f"  - Excel'den (gerçek beden): {src_counts.get('excel', 0)}")
    print(f"  - Pattern'den (varsayım):    {src_counts.get('pattern', 0)}")

    # DB
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    updated_products = 0
    updated_variants = 0
    async for p in db.products.find({}, {"_id": 0, "id": 1, "stock_code": 1, "variants": 1}):
        set_doc = {}
        for i, v in enumerate(p.get("variants") or []):
            bc = str(v.get("barcode") or "")
            if not bc or bc not in barcode_to_assigned:
                continue
            _, new_size, _ = barcode_to_assigned[bc]
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
