"""
DB'de daha önce yanlış birleştirilmiş (aynı doc'a 2+ renk düşmüş) doc'ları
URUNKARTIID bazında ayrıştırır. Excel'deki BARKOD-URUNKARTIID eşleşmesi
otorite olarak alınır: aynı KART_ID = aynı renk = aynı doc.

Plan:
  1. Excel'i oku → barcode -> (stock_code, urun_karti_id, name, beden) mapping
  2. Her DB doc için variants[]'ı urun_karti_id'ye göre grupla
  3. Eğer 1 doc içinde 2+ kart_id varsa: ilk kart_id mevcut doc'ta kalır,
     diğerleri için yeni doc oluşturulur (renk URUNADI'nın son kelimelerinden)
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


def _bc(v):
    if pd.isna(v): return ""
    try: return str(int(float(v)))
    except: return str(v).strip()


def _color_from_name(name, stock_code=None):
    """URUNADI'dan rengi çıkar. Son 1-3 kelime."""
    if not name: return None
    s = name.strip()
    # Trailing UPPER tag (e.g. "SİYAH") varsa onu al
    parts = s.split()
    if not parts: return None
    # Try last token uppercase
    upper_tail = []
    for t in reversed(parts):
        if t.upper() == t and any(c.isalpha() for c in t):
            upper_tail.insert(0, t)
        else:
            break
    if upper_tail:
        return " ".join(upper_tail).title()
    # Else: son 1-2 title-case kelime
    tail = []
    for t in reversed(parts):
        if t[:1].isupper() and not any(d.isdigit() for d in t):
            tail.insert(0, t)
            if len(tail) >= 2: break
        else:
            break
    return " ".join(tail) if tail else None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    if "Unnamed: 7" in df.columns:
        df = df.rename(columns={"Unnamed: 7": "Beden"})
    df["BC"] = df["BARKOD"].apply(_bc)

    # barcode -> (stock_code, urun_karti_id, name, beden, urun_id)
    bc_info = {}
    for _, row in df.iterrows():
        bc = row["BC"]
        if not bc: continue
        kart = None
        try:
            kart = str(int(float(row.get("URUNKARTIID")))) if pd.notna(row.get("URUNKARTIID")) else None
        except: pass
        urun_id = None
        try:
            urun_id = str(int(float(row.get("URUNID")))) if pd.notna(row.get("URUNID")) else None
        except: pass
        bc_info[bc] = {
            "stock_code": str(row.get("STOKKODU") or "").strip(),
            "kart_id": kart,
            "name": str(row.get("URUNADI") or "").strip(),
            "beden": str(row.get("Beden") or "STD").strip(),
            "urun_id": urun_id,
        }

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    docs_split = 0
    docs_created = 0
    variants_moved = 0
    variants_color_filled = 0

    async for p in db.products.find({}, {"_id": 0}):
        variants = p.get("variants") or []
        if not variants:
            continue
        # Her variant için Excel'den kart_id çek
        kart_groups = {}  # kart_id -> [variant_indexes]
        for i, v in enumerate(variants):
            bc = str(v.get("barcode") or "")
            info = bc_info.get(bc)
            if not info or not info.get("kart_id"):
                kart_groups.setdefault("__unknown__", []).append(i)
            else:
                kart_groups.setdefault(info["kart_id"], []).append(i)

        # Tek bir kart_id varsa zaten doğru
        if len(kart_groups) <= 1:
            # color'ı doldurabiliriz
            updates = {}
            for i, v in enumerate(variants):
                if not v.get("color"):
                    bc = str(v.get("barcode") or "")
                    info = bc_info.get(bc)
                    name = info.get("name") if info else p.get("name")
                    color = _color_from_name(name)
                    if color:
                        updates[f"variants.{i}.color"] = color
                        variants_color_filled += 1
            if updates and args.apply:
                await db.products.update_one({"id": p["id"]}, {"$set": updates})
            continue

        # 2+ kart_id var → SPLIT
        docs_split += 1
        # En çok varyantlı kart_id mevcut doc'ta kalsın
        kart_sorted = sorted(kart_groups.items(), key=lambda x: -len(x[1]))
        keep_kart_id, keep_indexes = kart_sorted[0]
        keep_variants = []
        for idx in keep_indexes:
            v = dict(variants[idx])
            bc = str(v.get("barcode") or "")
            info = bc_info.get(bc) or {}
            if info.get("beden"):
                v["size"] = info["beden"]
            if info.get("urun_id"):
                v["urun_id"] = info["urun_id"]
            # color fill
            if not v.get("color"):
                color = _color_from_name(info.get("name") or p.get("name"))
                if color:
                    v["color"] = color
                    variants_color_filled += 1
            keep_variants.append(v)
        keep_color = next((v.get("color") for v in keep_variants if v.get("color")), None)
        keep_name = next((bc_info.get(str(v.get("barcode") or ""), {}).get("name") for v in keep_variants
                          if bc_info.get(str(v.get("barcode") or ""), {}).get("name")), p.get("name"))

        # Diğer kart_id'leri yeni doc olarak
        new_docs = []
        for kart_id, indexes in kart_sorted[1:]:
            new_vars = []
            doc_name = p.get("name")
            for idx in indexes:
                v = dict(variants[idx])
                bc = str(v.get("barcode") or "")
                info = bc_info.get(bc) or {}
                if info.get("beden"): v["size"] = info["beden"]
                if info.get("urun_id"): v["urun_id"] = info["urun_id"]
                if info.get("name"): doc_name = info["name"]
                color = _color_from_name(info.get("name") or doc_name)
                if color: v["color"] = color
                v["id"] = generate_id()  # her doc'ta unique
                new_vars.append(v)
                variants_moved += 1
            color = next((v.get("color") for v in new_vars if v.get("color")), None)
            now = datetime.now(timezone.utc).isoformat()
            new_doc = {
                "id": generate_id(),
                "name": doc_name,
                "stock_code": p.get("stock_code"),
                "barcode": new_vars[0].get("barcode") if new_vars else None,
                "color": color,
                "description": p.get("description") or "",
                "variants": new_vars,
                "price": p.get("price") or 0,
                "list_price": p.get("list_price") or 0,
                "sale_price": p.get("sale_price") or 0,
                "cost_price": p.get("cost_price") or 0,
                "member_price_1": p.get("member_price_1"),
                "is_active": p.get("is_active", True),
                "in_stock": False,
                "urun_karti_id": kart_id,
                "manufacturer": p.get("manufacturer") or "FACETTE",
                "category_id": p.get("category_id"),
                "category_name": p.get("category_name"),
                "images": p.get("images") or [],
                "created_at": now,
                "updated_at": now,
            }
            new_docs.append(new_doc)
            docs_created += 1

        # Save: update existing doc, insert new ones
        if args.apply:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {
                    "variants": keep_variants,
                    "color": keep_color,
                    "name": keep_name or p.get("name"),
                    "urun_karti_id": keep_kart_id if keep_kart_id != "__unknown__" else p.get("urun_karti_id"),
                }}
            )
            for nd in new_docs:
                await db.products.insert_one(nd)

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}:")
    print(f"  Split edilen doc: {docs_split}")
    print(f"  Oluşturulan yeni doc: {docs_created}")
    print(f"  Taşınan varyant: {variants_moved}")
    print(f"  Color doldurulan varyant: {variants_color_filled}")


if __name__ == "__main__":
    asyncio.run(main())
