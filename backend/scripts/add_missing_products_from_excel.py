"""
TicimaxExport v3'den DB'de eksik VARYANTLARI ve EKSİK ÜRÜNLERİ ekler.

Excel kolonları: URUNKARTIID, URUNID, STOKKODU, BARKOD, URUNADI, ACIKLAMA, VARYASYON, Unnamed: 7 (=Beden)

Senaryo 1 (eksik varyant): STOKKODU DB'de var ama BARKOD yok → variants[]'e push et.
Senaryo 2 (eksik ürün):    STOKKODU da yok → yeni product oluştur, varyantları ile birlikte.

Renk URUNADI'nın son kelimesinden çıkarılır (UPPER kelime: "Acı Kahve", "BEJ", "EKRU", "SİYAH" vb.).
Mevcut DB'de aynı stok_code'a sahip ürünlerin renk dağılımı incelenir; yeni varyant bu renge eklenir.
Yoksa o renkte yeni doc oluşturulur.
"""
import asyncio
import os
import re
import argparse
import sys
import html
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


def _extract_color_from_name(name):
    """URUNADI'nın sonundaki UPPERCASE renk kelimesini çıkar."""
    if not name: return None
    # Son birkaç kelimeyi al
    parts = name.strip().split()
    if not parts: return None
    # Sondan başla, ardışık UPPERCASE kelimeleri topla
    color_tokens = []
    for t in reversed(parts):
        if t.upper() == t and any(ch.isalpha() for ch in t):
            color_tokens.insert(0, t)
        else:
            break
    if not color_tokens:
        return None
    raw = " ".join(color_tokens)
    # Title case
    return raw.title()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    # Rename Unnamed: 7 → Beden
    if "Unnamed: 7" in df.columns:
        df = df.rename(columns={"Unnamed: 7": "Beden"})

    df["BC"] = df["BARKOD"].apply(_bc)

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Existing barcodes & products
    existing_barcodes = set()
    existing_by_stockcode = {}  # stock_code -> list of product docs
    async for p in db.products.find({}, {"_id": 0}):
        if p.get("barcode"): existing_barcodes.add(str(p["barcode"]))
        for v in (p.get("variants") or []):
            if v.get("barcode"): existing_barcodes.add(str(v["barcode"]))
        sc = p.get("stock_code")
        if sc:
            existing_by_stockcode.setdefault(str(sc), []).append(p)

    added_variants = 0
    created_products = 0
    skipped = 0

    # Group by (STOKKODU, color extracted) — her renk için ayrı DB doc
    rows_by_key = {}
    for _, row in df.iterrows():
        bc = row["BC"]
        if not bc or bc in existing_barcodes:
            continue
        sc = str(row.get("STOKKODU") or "").strip()
        name = str(row.get("URUNADI") or "").strip()
        color = _extract_color_from_name(name)
        key = (sc, color or "")
        rows_by_key.setdefault(key, []).append(row)

    print(f"Toplam yeni satır: {sum(len(v) for v in rows_by_key.values())} | Grup: {len(rows_by_key)}")

    for (sc, color), rows in rows_by_key.items():
        existing_products = existing_by_stockcode.get(sc, [])
        # Aynı stock_code + aynı renkte mevcut ürün var mı?
        target_doc = None
        for ep in existing_products:
            ep_color = ""
            # Try: parent's color or first variant's color or extracted from name
            if ep.get("color"):
                ep_color = ep["color"]
            elif ep.get("variants"):
                ep_color = (ep["variants"][0].get("color") or "")
            if not ep_color:
                ep_color = _extract_color_from_name(ep.get("name") or "") or ""
            if ep_color.lower().strip() == (color or "").lower().strip():
                target_doc = ep
                break

        if target_doc:
            # APPEND eksik varyantları variants[]'e
            new_variants = list(target_doc.get("variants") or [])
            existing_v_barcodes = {str(v.get("barcode") or "") for v in new_variants}
            for row in rows:
                bc = row["BC"]
                if bc in existing_v_barcodes:
                    continue
                size = str(row.get("Beden") or "STD").strip()
                urun_id = None
                try:
                    urun_id = str(int(float(row.get("URUNID")))) if pd.notna(row.get("URUNID")) else None
                except Exception:
                    pass
                new_variants.append({
                    "id": generate_id(),
                    "size": size,
                    "color": color,
                    "barcode": bc,
                    "sku": bc,
                    "stock_code": sc,
                    "stock": 0,
                    "price_adjustment": 0,
                    "urun_id": urun_id,
                })
                added_variants += 1
                existing_v_barcodes.add(bc)
                existing_barcodes.add(bc)
            if args.apply:
                await db.products.update_one({"id": target_doc["id"]}, {"$set": {"variants": new_variants}})
        else:
            # YENİ ürün oluştur (sc + color kombosu)
            first_row = rows[0]
            name = str(first_row.get("URUNADI") or "").strip()
            desc = str(first_row.get("ACIKLAMA") or "")
            try:
                kart_id = str(int(float(first_row.get("URUNKARTIID")))) if pd.notna(first_row.get("URUNKARTIID")) else None
            except Exception:
                kart_id = None
            variants_list = []
            for row in rows:
                bc = row["BC"]
                size = str(row.get("Beden") or "STD").strip()
                try:
                    urun_id = str(int(float(row.get("URUNID")))) if pd.notna(row.get("URUNID")) else None
                except Exception:
                    urun_id = None
                variants_list.append({
                    "id": generate_id(),
                    "size": size,
                    "color": color,
                    "barcode": bc,
                    "sku": bc,
                    "stock_code": sc,
                    "stock": 0,
                    "price_adjustment": 0,
                    "urun_id": urun_id,
                })
                existing_barcodes.add(bc)
            now = datetime.now(timezone.utc).isoformat()
            new_doc = {
                "id": generate_id(),
                "name": name,
                "stock_code": sc,
                "barcode": variants_list[0]["barcode"] if variants_list else None,
                "color": color,
                "description": desc,
                "variants": variants_list,
                "price": 0, "list_price": 0, "sale_price": 0, "cost_price": 0,
                "is_active": True,
                "in_stock": False,
                "urun_karti_id": kart_id,
                "manufacturer": "FACETTE",
                "category_id": None,
                "category_name": None,
                "images": [],
                "created_at": now,
                "updated_at": now,
            }
            if args.apply:
                await db.products.insert_one(new_doc)
            created_products += 1
            added_variants += len(variants_list)

    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}:")
    print(f"  Eklenen varyant: {added_variants}")
    print(f"  Oluşturulan yeni ürün: {created_products}")
    print(f"  Skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
