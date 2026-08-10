"""
Ticimax Excel Re-sync Script

Verilen Ticimax export Excel'inden barkod referans alınarak:
  - products koleksiyonunda parent product için ticimax_kart_id yazılır
  - variants[] içinde matching barcode için ticimax_urun_id, size (Beden) güncellenir
  - description (Açıklama) HTML olarak set edilir
  - sale_price (INDIRIMLIFIYAT), list_price (SATISFIYATI), cost_price (ALISFIYATI) güncellenir

Kullanım:
    python3 -m scripts.sync_from_ticimax_excel /tmp/ticimax.xls          # dry-run (default)
    python3 -m scripts.sync_from_ticimax_excel /tmp/ticimax.xls --apply  # gerçekten yaz
"""
import asyncio
import os
import sys
import argparse
import html
import re
from dotenv import load_dotenv

load_dotenv()

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _clean_html_short(s):
    """HTML açıklamayı kısa hale getir (boşluk normalize, basit tag temizliği)."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel_path")
    parser.add_argument("--apply", action="store_true", help="Gerçekten DB'ye yaz")
    parser.add_argument("--limit", type=int, default=0, help="İlk N satır (debug)")
    args = parser.parse_args()

    # Excel oku (xlrd yoksa openpyxl ile)
    try:
        df = pd.read_excel(args.excel_path, engine="openpyxl")
    except Exception:
        df = pd.read_excel(args.excel_path, engine="xlrd")
    if args.limit:
        df = df.head(args.limit)

    print(f"Excel satır: {len(df)}, kolon: {list(df.columns)}")

    # Barkodu str'e normalize et (float'tan)
    def _bc(v):
        if pd.isna(v):
            return ""
        try:
            s = str(int(float(v)))
        except Exception:
            s = str(v).strip()
        return s

    df["BARKOD_str"] = df["BARKOD"].apply(_bc)

    # DB bağlantısı
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Barkod → Excel satırı index
    bc_to_row = {}
    for _, row in df.iterrows():
        bc = row["BARKOD_str"]
        if bc:
            bc_to_row[bc] = row

    print(f"Excel'de unique barkod: {len(bc_to_row)}")

    # Tüm DB ürünlerini gez
    matched_variants = 0
    matched_parents = set()
    not_in_db = []
    updates = []  # (product_id, mongo_update_doc, debug)

    async for prod in db.products.find({}, {"_id": 0}):
        pid = prod.get("id")
        stock_code = prod.get("stock_code") or ""
        variants = prod.get("variants") or []

        prod_update = {}
        variant_updates = []  # list of dicts to set in variants[i]
        had_match = False

        for i, v in enumerate(variants):
            vbc = str(v.get("barcode") or "").strip()
            if not vbc:
                continue
            row = bc_to_row.get(vbc)
            if not row is None and not row.empty if isinstance(row, pd.Series) else False:
                pass  # awkward — handle below
            if row is None or (isinstance(row, pd.Series) and row.empty):
                continue
            # Match!
            matched_variants += 1
            had_match = True

            new_size_raw = str(row["Beden"]).strip() if pd.notna(row.get("Beden")) else None
            new_urunid = int(row["URUNID"]) if pd.notna(row.get("URUNID")) else None
            new_kartid = int(row["URUNKARTIID"]) if pd.notna(row.get("URUNKARTIID")) else None

            # Beden değeri GERÇEK beden mi yoksa renk mi?
            # Whitelist: STD/standard, XS-XXXL, numeric (24-60), ya da slash kombo (M/L), 36/38
            SIZE_TOKENS = {"STD", "STANDART", "STANDARD", "TEK", "TEK BEDEN",
                           "XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL",
                           "FREESIZE", "FREE", "OS", "ONE SIZE"}
            def _is_size(s):
                if not s:
                    return False
                up = s.upper().strip()
                # Pure numeric (28, 30…60) veya numeric/numeric (36/38)
                if re.fullmatch(r"\d{2,3}(/\d{2,3})?", up):
                    return True
                # Letter token veya letter/letter (M/L)
                tokens = re.split(r"[/\-]", up)
                if all(t in SIZE_TOKENS for t in tokens if t):
                    return True
                return up in SIZE_TOKENS

            new_size = new_size_raw if _is_size(new_size_raw) else None
            new_color_from_beden = new_size_raw if (new_size_raw and not _is_size(new_size_raw)) else None

            v_set = {}
            # Excel TEK referans — mevcut yanlış değerleri (Trendyol restore'undan
            # gelen "Standart"/"Normal Boy" gibi) Excel'in gerçek bedeni ile EZ.
            if new_size and v.get("size") != new_size:
                v_set["size"] = new_size
            elif new_color_from_beden:
                # Beden sütununda renk var → variant.color'a yaz
                if v.get("color") != new_color_from_beden:
                    v_set["color"] = new_color_from_beden
                # Mevcut size yanlış (önceki Trendyol restore'undan "Standart" gibi)
                # ve gerçek bir beden değilse → "STD"e düşür
                cur_size = v.get("size") or ""
                if cur_size and not _is_size(cur_size):
                    v_set["size"] = "STD"
            if new_urunid and v.get("urun_id") != str(new_urunid):
                v_set["urun_id"] = str(new_urunid)

            if v_set:
                variant_updates.append((i, v_set))

            # Parent-level değişiklikler (her match için aynı; sonuncu kazanır ama hepsi aynı olmalı)
            if new_kartid and prod.get("urun_karti_id") != str(new_kartid):
                prod_update["urun_karti_id"] = str(new_kartid)

            # Description (her variant aynı parent'ı pointar; ilk dolu yeterli)
            new_desc = row.get("ACIKLAMA")
            if isinstance(new_desc, str) and new_desc.strip() and len(_clean_html_short(new_desc)) > 30:
                # Sadece mevcut açıklama boşsa veya çok kısaysa override
                cur_desc = prod.get("description") or ""
                if len(_clean_html_short(cur_desc)) < 30:
                    prod_update["description"] = new_desc

            # Fiyatlar (parent level): SATISFIYATI=list_price, INDIRIMLIFIYAT=sale_price, ALISFIYATI=cost_price
            def _num(x):
                try:
                    return float(x) if pd.notna(x) else None
                except Exception:
                    return None

            new_list = _num(row.get("SATISFIYATI"))
            new_sale = _num(row.get("INDIRIMLIFIYAT"))
            new_cost = _num(row.get("ALISFIYATI"))

            if new_list and prod.get("list_price") != new_list:
                prod_update["list_price"] = new_list
                # ana price alanı da güncellensin (storefront/markup'ta kullanılıyor)
                if not prod.get("price") or prod.get("price") == prod.get("list_price"):
                    prod_update["price"] = new_list
            if new_sale and prod.get("sale_price") != new_sale:
                prod_update["sale_price"] = new_sale
            if new_cost and prod.get("cost_price") != new_cost:
                prod_update["cost_price"] = new_cost

        if had_match and (prod_update or variant_updates):
            matched_parents.add(pid)
            # Build mongo update doc
            mongo_set = dict(prod_update)
            for i, vs in variant_updates:
                for k, val in vs.items():
                    mongo_set[f"variants.{i}.{k}"] = val
            updates.append((pid, stock_code, mongo_set, len(variant_updates), len(prod_update)))

    # Excel'de var ama DB'de hiç bulunamayanlar
    db_all_barcodes = set()
    async for p in db.products.find({}, {"_id": 0, "variants.barcode": 1, "barcode": 1}):
        if p.get("barcode"):
            db_all_barcodes.add(str(p["barcode"]))
        for v in (p.get("variants") or []):
            if v.get("barcode"):
                db_all_barcodes.add(str(v["barcode"]))
    for bc in bc_to_row:
        if bc not in db_all_barcodes:
            not_in_db.append(bc)

    print()
    print("=" * 60)
    print(f"Eşleşen varyant sayısı: {matched_variants}")
    print(f"Etkilenecek parent ürün: {len(matched_parents)}")
    print(f"Yapılacak update count: {len(updates)}")
    print(f"Excel'de olup DB'de OLMAYAN barkodlar: {len(not_in_db)}")
    if not_in_db[:5]:
        print(f"  ilk 5: {not_in_db[:5]}")

    if updates[:3]:
        print()
        print("Örnek güncellemeler:")
        for pid, sc, ms, vc, pc in updates[:5]:
            print(f"  {sc} (id={pid}): {vc} varyant alanı, {pc} parent alanı — {list(ms.keys())[:5]}…")

    if not args.apply:
        print()
        print("DRY-RUN. --apply ekleyerek gerçekten yazın.")
        return

    # APPLY
    print()
    print("APPLY ediliyor…")
    written = 0
    for pid, sc, ms, vc, pc in updates:
        if not ms:
            continue
        await db.products.update_one({"id": pid}, {"$set": ms})
        written += 1
    print(f"✓ {written} ürün güncellendi.")


if __name__ == "__main__":
    asyncio.run(main())
