"""
Ticimax Excel TAM RESYNC

Kullanıcı isteği: "Ticimax'tan ürünleri yeniden temiz bir şekilde işle.
Barkodlar, ürün kart id'leri, stok kodları, fiyatlar, indirimli fiyatlar, tedarikçi
vb. nereye ne yazdıysan hepsini bu yeni Excel ile güncelle/düzelt."

Excel sütunları (TicimaxExport 5):
URUNKARTIID, URUNID, STOKKODU, BARKOD, URUNADI, ACIKLAMA, BREADCRUMBKAT,
TEDARIKCI, ALISFIYATI, SATISFIYATI, INDIRIMLIFIYAT, UYETIPIFIYAT1, KDVORANI,
RENK, BEDEN

Strateji:
1. Excel'i URUNKARTIID'e göre grupla (her grup 1 parent ürün).
2. Her parent için DB'de match et: önce urun_karti_id, sonra stock_code+color, sonra barcode.
3. Match olanları TAM güncelle (barkod/stock_code/fiyat/varyantlar Excel hangiyse).
4. Match olmayanları yeni ürün olarak ekle.
5. RAPOR: kaç ürün güncellendi, kaç yeni eklendi, kaç DB ürünü Excel'de yok.
"""
import asyncio
import os
import sys
import re
from typing import Optional
import pandas as pd
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

EXCEL_PATH = "/tmp/excel/ticimax5.xls"
ENGINE = "openpyxl"


def _slugify(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:200] or "urun"


def _f(x, default=0.0):
    try:
        if pd.isna(x): return default
        return float(x)
    except Exception:
        return default


def _s(x, default=""):
    if x is None or (isinstance(x, float) and pd.isna(x)): return default
    return str(x).strip()


def _int(x, default=0):
    try:
        if pd.isna(x): return default
        return int(x)
    except Exception:
        return default


def _category_from_breadcrumb(crumb: str) -> str:
    """GİYİM>Alt Giyim>Trençkot → "Trençkot" (yaprak kategori)"""
    if not crumb:
        return ""
    parts = [p.strip() for p in crumb.split(">") if p.strip()]
    return parts[-1] if parts else ""


async def main():
    df = pd.read_excel(EXCEL_PATH, engine=ENGINE)
    print(f"Excel yüklendi: {len(df)} satır, {df['URUNKARTIID'].nunique()} parent")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Sistem kategorilerini map et (name → category doc)
    sys_cats = await db.categories.find({}, {"_id": 0}).to_list(None)
    cat_by_name = {(c.get("name") or "").strip().lower(): c for c in sys_cats}

    stats = {
        "parents_in_excel": 0,
        "parents_updated_db": 0,
        "parents_created_new": 0,
        "variants_total": 0,
        "errors": [],
    }

    # URUNKARTIID'e göre grupla
    by_kart = df.groupby("URUNKARTIID", sort=False)

    for kart_id_raw, grp in by_kart:
        stats["parents_in_excel"] += 1
        kart_id = str(_int(kart_id_raw))
        first = grp.iloc[0]

        urun_adi = _s(first["URUNADI"])
        renk = _s(first["RENK"]).strip()
        # Ürün adından "Renk" kelimesini sondan temizleyip BASE ad oluştur
        base_name = urun_adi
        if renk and base_name.lower().endswith(" " + renk.lower()):
            base_name = base_name[: -(len(renk) + 1)].strip()

        # Parent fiyatları (ilk satır referans, hepsi aynı varsayalım)
        list_price = _f(first["SATISFIYATI"])
        sale_price = _f(first["INDIRIMLIFIYAT"]) or list_price
        member_price_1 = _f(first["UYETIPIFIYAT1"]) or list_price
        cost_price = _f(first["ALISFIYATI"])
        vat_rate = _f(first["KDVORANI"], 10)
        vendor = _s(first["TEDARIKCI"], "FACETTE")
        description = _s(first["ACIKLAMA"])
        breadcrumb = _s(first["BREADCRUMBKAT"])
        category_leaf = _category_from_breadcrumb(breadcrumb)
        cat_doc = cat_by_name.get(category_leaf.lower()) if category_leaf else None

        # Parent stock_code (ilk satırın STOKKODU — aslında varyant bazlı ama
        # önceki implementasyonda parent için kullanılan stock_code)
        parent_stock_code = _s(first["STOKKODU"])

        # Varyantlar
        variants = []
        for _, row in grp.iterrows():
            v = {
                "size": _s(row["BEDEN"]).upper() or "STD",
                "color": _s(row["RENK"]).title() or renk.title(),
                "barcode": _s(row["BARKOD"]),
                "stock_code": _s(row["STOKKODU"]),
                "urun_id": _s(row["URUNID"]),
                "stock": 5,  # Excel'de stok yok, varsayılan 5
                "price": _f(row["SATISFIYATI"]),
                "sale_price": _f(row["INDIRIMLIFIYAT"]) or _f(row["SATISFIYATI"]),
            }
            variants.append(v)
            stats["variants_total"] += 1

        # DB'de bul (urun_karti_id öncelik)
        existing = await db.products.find_one({"urun_karti_id": kart_id})
        if not existing and parent_stock_code:
            existing = await db.products.find_one({
                "$or": [
                    {"stock_code": parent_stock_code, "color": renk.title()},
                    {"variants.stock_code": parent_stock_code, "color": renk.title()},
                ]
            })
        if not existing and renk:
            # Aynı base_name + renk
            existing = await db.products.find_one({
                "name": {"$regex": f"^{re.escape(base_name)}.*{re.escape(renk)}$", "$options": "i"}
            })

        update_doc = {
            "name": urun_adi,
            "color": renk.title(),
            "stock_code": parent_stock_code,
            "sku": parent_stock_code,
            "urun_karti_id": kart_id,
            "price": list_price,
            "sale_price": sale_price,
            "member_price_1": member_price_1,
            "cost_price": cost_price,
            "vat_rate": vat_rate,
            "vendor": vendor,
            "vendor_name": vendor,
            "description": description,
            "category_name": category_leaf,
            "breadcrumb": breadcrumb,
            "variants": variants,
        }
        if cat_doc:
            update_doc["category_id"] = cat_doc.get("id")
            update_doc["category_name"] = cat_doc.get("name")

        if existing:
            await db.products.update_one(
                {"id": existing["id"]},
                {"$set": update_doc},
            )
            stats["parents_updated_db"] += 1
        else:
            # Yeni ürün ekle
            from uuid import uuid4
            from datetime import datetime, timezone
            new_doc = {
                "id": str(uuid4()),
                "slug": _slugify(urun_adi),
                "is_active": True,
                "is_published": True,
                "images": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                **update_doc,
            }
            await db.products.insert_one(new_doc)
            stats["parents_created_new"] += 1

    # Excel'de OLMAYAN DB ürünlerini topla
    excel_kart_ids = set(str(_int(k)) for k in df["URUNKARTIID"].unique())
    db_orphans = await db.products.count_documents({
        "urun_karti_id": {"$exists": True, "$nin": list(excel_kart_ids)}
    })
    stats["db_orphan_with_kart_id"] = db_orphans

    print("\n📊 SONUÇ:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
