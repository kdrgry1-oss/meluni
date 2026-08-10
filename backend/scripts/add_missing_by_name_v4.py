"""
Excel v4'den (URUNADI, BEDEN, BARKOD) mapping'i kullanarak DB'de
ÜRÜN ADI eşleşmeyen anahtarları yeni varyant olarak EKLE.

Strateji:
  1. Excel'i grupla: (name, color_extracted) → varyantlar listesi
  2. DB'de aynı name'li ürün varsa variants[]'a ekle
  3. Yoksa yeni ürün oluştur (renk URUNADI'nın sonundan)
"""
import asyncio
import os
import re
import argparse
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import generate_id  # noqa: E402


COLOR_WORDS = {
    "Siyah", "Beyaz", "Bordo", "Lacivert", "Bej", "Camel", "Antrasit", "Haki",
    "Krem", "Kırmızı", "Sarı", "Yeşil", "Mavi", "Mor", "Pembe", "Gri", "Turuncu",
    "Ekru", "Vizon", "Kahve", "Kahverengi", "Taş", "Petrol", "Mint", "Lila",
    "Fuşya", "Hardal", "Indigo", "Şampanya", "Buz", "Çağla", "Yavruağzı",
}
MODIFIERS = {"Açık", "Koyu", "Acı", "Toz", "Soft", "Pastel", "Neon", "Buz", "Bal"}


def _tr_title(s):
    if not s: return s
    def cap(word):
        if not word: return word
        if len(word) <= 1: return word.upper()
        return word[0].upper() + word[1:].replace("İ", "i").replace("I", "ı").lower()
    return " ".join(cap(w) for w in s.split())


def extract_color(name):
    if not name: return None
    parts = name.strip().split()
    if not parts: return None
    tail = []
    for t in reversed(parts):
        norm = _tr_title(t.strip(",."))
        if norm in COLOR_WORDS:
            tail.insert(0, norm)
            continue
        if tail and norm in MODIFIERS:
            tail.insert(0, norm)
            break
        break
    return " ".join(tail) if tail else None


def _norm(s):
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
    df["BC"] = df["BARKOD"].apply(_bc)
    df["NAME_N"] = df["URUNADI"].fillna("").apply(_norm)
    df["SIZE_N"] = df["BEDEN"].fillna("").apply(_norm)

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # DB index: (name_n, size_n) -> set of barcodes
    db_keys = set()
    db_name_index = {}  # name_n -> list of products
    async for p in db.products.find({}, {"_id": 0}):
        n = _norm(p.get("name"))
        if not n: continue
        db_name_index.setdefault(n, []).append(p)
        for v in (p.get("variants") or []):
            db_keys.add((n, _norm(v.get("size") or "STD")))

    # Find unmatched excel rows
    rows_to_add = []
    for _, row in df.iterrows():
        key = (row["NAME_N"], row["SIZE_N"])
        if key in db_keys:
            continue
        if not row["BC"] or not row["NAME_N"]:
            continue
        rows_to_add.append(row)
    print(f"DB'de eksik (name, size) satırları: {len(rows_to_add)}")

    # Group by name
    groups = {}
    for row in rows_to_add:
        groups.setdefault(row["NAME_N"], []).append(row)
    print(f"Eşsiz ürün adı: {len(groups)}")

    added_variants = 0
    appended_to = 0
    created_products = 0

    for name_n, rows in groups.items():
        # DB'de aynı isimli ürün var mı?
        existing = db_name_index.get(name_n) or []
        if existing:
            # En çok varyantlı doc'a append et
            target = max(existing, key=lambda p: len(p.get("variants") or []))
            new_variants = list(target.get("variants") or [])
            existing_v_keys = {(_norm(v.get("size") or "STD")) for v in new_variants}
            color = target.get("color") or extract_color(rows[0].get("URUNADI"))
            for row in rows:
                size = str(row.get("BEDEN") or "STD").strip() or "STD"
                if _norm(size) in existing_v_keys:
                    continue
                try:
                    urun_id = str(int(float(row["URUNID"]))) if pd.notna(row.get("URUNID")) else None
                except: urun_id = None
                new_variants.append({
                    "id": generate_id(),
                    "size": size,
                    "color": color,
                    "barcode": row["BC"],
                    "sku": row["BC"],
                    "stock_code": str(row.get("STOKKODU") or "").strip(),
                    "stock": 0,
                    "price_adjustment": 0,
                    "urun_id": urun_id,
                })
                added_variants += 1
                existing_v_keys.add(_norm(size))
            if args.apply:
                await db.products.update_one({"id": target["id"]}, {"$set": {"variants": new_variants}})
            appended_to += 1
        else:
            # Yeni ürün oluştur
            first = rows[0]
            name = str(first.get("URUNADI") or "").strip()
            color = extract_color(name)
            try:
                kart_id = str(int(float(first["URUNKARTIID"]))) if pd.notna(first.get("URUNKARTIID")) else None
            except: kart_id = None
            sc = str(first.get("STOKKODU") or "").strip()
            variants_list = []
            for row in rows:
                try:
                    urun_id = str(int(float(row["URUNID"]))) if pd.notna(row.get("URUNID")) else None
                except: urun_id = None
                variants_list.append({
                    "id": generate_id(),
                    "size": str(row.get("BEDEN") or "STD").strip() or "STD",
                    "color": color,
                    "barcode": row["BC"],
                    "sku": row["BC"],
                    "stock_code": str(row.get("STOKKODU") or "").strip(),
                    "stock": 0,
                    "price_adjustment": 0,
                    "urun_id": urun_id,
                })
                added_variants += 1
            now = datetime.now(timezone.utc).isoformat()
            doc = {
                "id": generate_id(),
                "name": name,
                "stock_code": sc,
                "barcode": variants_list[0].get("barcode") if variants_list else None,
                "color": color,
                "description": "",
                "variants": variants_list,
                "price": 0, "list_price": 0, "sale_price": 0, "cost_price": 0,
                "is_active": True,
                "in_stock": False,
                "urun_karti_id": kart_id,
                "manufacturer": "FACETTE",
                "category_id": None, "category_name": None,
                "images": [],
                "created_at": now, "updated_at": now,
            }
            if args.apply:
                await db.products.insert_one(doc)
            created_products += 1

    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}:")
    print(f"  Eklenen varyant: {added_variants}")
    print(f"  Mevcut doc'a append edilen ürün: {appended_to}")
    print(f"  Oluşturulan yeni doc: {created_products}")


if __name__ == "__main__":
    asyncio.run(main())
