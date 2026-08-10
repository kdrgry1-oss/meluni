"""
Ticimax 113-sütun Excel import / senkronizasyon.

- Excel kaynak doğrudur: tüm 113 alan `product.ticimax_fields` altına yazılır.
- Önemli ticari/açıklayıcı alanlar tipli ürün alanlarına da maplenir
  (price, sale_price, member_price_1, description, SEO, KDV, boyut/kargo, bayraklar).
- Eşleştirme: önce varyant BARKOD/STOKKODU, yoksa URUNKARTIID.
- GÜVENLİK: storefront'u kazara gizlememek için `is_active` toplu ezilmez
  (URUNAKTIF değeri yine ticimax_fields'a yazılır, admin tek tek yönetir).

Kullanım:
  python import_ticimax_113.py /tmp/ticimax1.xls            # DRY-RUN
  python import_ticimax_113.py /tmp/ticimax1.xls --apply    # uygula
"""
import asyncio
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from product_schema import ORDERED_COLUMNS, normalize_value, parse_price  # noqa: E402


def _bc(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


def parse_varyasyon(s):
    """'Renk Seçiniz;GRİ,Beden Seçiniz;S' -> (color, size)"""
    color, size = None, None
    if not s or pd.isna(s):
        return color, size
    for part in str(s).split(","):
        if ";" not in part:
            continue
        k, v = part.split(";", 1)
        k = k.strip().lower()
        v = v.strip()
        if "renk" in k:
            color = v or None
        elif "beden" in k:
            size = v or None
    return color, size


def _i(v, default=None):
    f = parse_price(v)
    if f is None:
        return default
    try:
        return int(f)
    except Exception:
        return default


def build_typed_fields(row, matched_by_barcode):
    """Excel satırından tipli ürün alanlarını üret."""
    out = {}
    price = parse_price(row.get("SATISFIYATI"))
    if price is not None:
        out["price"] = price
    sale = parse_price(row.get("INDIRIMLIFIYAT"))
    if sale is not None and sale > 0 and (price is None or sale < price):
        out["sale_price"] = sale
    else:
        out["sale_price"] = None
    mp1 = parse_price(row.get("UYETIPIFIYAT1"))
    if mp1 is not None and mp1 > 0:
        out["member_price_1"] = mp1
    pp = parse_price(row.get("ALISFIYATI"))
    if pp is not None:
        out["purchase_price"] = pp
    mkt = parse_price(row.get("PIYASAFIYATI"))
    if mkt is not None:
        out["market_price"] = mkt
    vat = parse_price(row.get("KDVORANI"))
    if vat is not None:
        out["vat_rate"] = vat

    def _s(col):
        val = row.get(col)
        if pd.isna(val):
            return ""
        return str(val).strip()

    out["description"] = _s("ACIKLAMA")
    out["brand"] = _s("MARKA") or "FACETTE"
    out["supplier"] = _s("TEDARIKCI")
    out["gtip_code"] = _s("GTIPKODU")
    out["keywords"] = _s("ANAHTARKELIME")
    out["meta_title"] = _s("SEO_SAYFABASLIK")
    out["meta_description"] = _s("SEO_SAYFAACIKLAMA")
    out["meta_keywords"] = _s("SEO_ANAHTARKELIME")
    out["breadcrumb"] = _s("BREADCRUMBKAT")
    out["unit"] = _s("SATISBIRIMI") or "ADET"
    cur = _s("PARABIRIMI")
    out["currency"] = "TRY" if cur.upper() in ("TL", "TRY", "") else cur
    out["max_installment"] = _i(row.get("MAKSTAKSITSAYISI"), 0)
    out["is_showcase"] = bool(_i(row.get("VITRIN"), 0))
    out["is_new"] = bool(_i(row.get("YENIURUN"), 0))
    out["is_opportunity"] = bool(_i(row.get("FIRSATURUNU"), 0))
    out["is_free_shipping"] = bool(_i(row.get("UCRETSIZKARGO"), 0))
    out["vat_included"] = bool(_i(row.get("KDVDAHIL"), 1))
    cw = parse_price(row.get("KARGOAGIRLIGI"))
    if cw is not None:
        out["cargo_weight"] = cw
    pw = parse_price(row.get("URUNAGIRLIGI"))
    if pw is not None:
        out["product_weight"] = pw
    for col, fld in (("URUNGENISLIK", "width"), ("URUNDERINLIK", "depth"), ("URUNYUKSEKLIK", "height")):
        val = parse_price(row.get(col))
        if val is not None:
            out[fld] = val
    # Sadece doğru renk satırı (barkod eşleşmesi) varsa ürün adını ez
    if matched_by_barcode:
        nm = _s("URUNADI")
        if nm:
            out["name"] = nm
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    df = pd.read_excel(args.excel_path, engine="openpyxl")
    print(f"Excel: {len(df)} satır, {len(df.columns)} kolon")

    # İndeksler
    by_barcode = {}   # barkod_str -> row(dict)
    card_rows = {}    # kartid_str -> [rows]
    for _, r in df.iterrows():
        row = r.to_dict()
        bc = _bc(row.get("BARKOD"))
        sk = str(row.get("STOKKODU") or "").strip()
        if bc:
            by_barcode[bc] = row
        if sk and sk.lower() != "nan":
            by_barcode.setdefault(sk, row)
        kid = _bc(row.get("URUNKARTIID"))
        if kid:
            card_rows.setdefault(kid, []).append(row)

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    stats = {"matched_barcode": 0, "matched_kartid": 0, "no_match": 0, "variants_updated": 0}
    bulk = []

    async for p in db.products.find({}):
        pid = p["id"]
        # Aday kodlar
        codes = set()
        for c in (p.get("barcode"), p.get("stock_code")):
            if c:
                codes.add(str(c).strip())
        for v in (p.get("variants") or []):
            for c in (v.get("barcode"), v.get("stock_code")):
                if c:
                    codes.add(str(c).strip())

        rep_row = None
        matched_by_barcode = False
        for c in codes:
            if c in by_barcode:
                rep_row = by_barcode[c]
                matched_by_barcode = True
                break
        if rep_row is None:
            kid = str(p.get("urun_karti_id") or "").strip()
            if kid and kid in card_rows:
                rep_row = card_rows[kid][0]
        if rep_row is None:
            stats["no_match"] += 1
            continue

        if matched_by_barcode:
            stats["matched_barcode"] += 1
        else:
            stats["matched_kartid"] += 1

        # 1) Tam 113 alanı ticimax_fields'a yaz
        tf = {col: normalize_value(col, rep_row.get(col)) for col in ORDERED_COLUMNS}

        # 2) Tipli alanlar
        set_doc = build_typed_fields(rep_row, matched_by_barcode)
        set_doc["ticimax_fields"] = tf

        # 3) Varyant güncelle (barkod eşleşince)
        variants = p.get("variants") or []
        total_stock = 0
        any_variant_matched = False
        for v in variants:
            vbc = str(v.get("barcode") or "").strip()
            vsk = str(v.get("stock_code") or "").strip()
            vrow = by_barcode.get(vbc) or by_barcode.get(vsk)
            if not vrow:
                continue
            any_variant_matched = True
            color, size = parse_varyasyon(vrow.get("VARYASYON"))
            if size:
                v["size"] = size
            if color:
                v["color"] = color
            st = _i(vrow.get("STOKADEDI"), None)
            if st is not None:
                v["stock"] = st
                total_stock += st
            v["variation_code"] = str(vrow.get("VARYASYONKODU") or "").strip() or v.get("variation_code")
            stats["variants_updated"] += 1
        if any_variant_matched:
            set_doc["variants"] = variants
            set_doc["stock"] = total_stock
        else:
            st = _i(rep_row.get("STOKADEDI"), None)
            if st is not None:
                set_doc["stock"] = st

        from datetime import datetime, timezone
        set_doc["updated_at"] = datetime.now(timezone.utc).isoformat()

        if args.apply:
            await db.products.update_one({"id": pid}, {"$set": set_doc})
        bulk.append(pid)

    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}")
    print(f"  Barkod eşleşen: {stats['matched_barcode']}")
    print(f"  Sadece KartID eşleşen: {stats['matched_kartid']}")
    print(f"  Eşleşmeyen (atlandı): {stats['no_match']}")
    print(f"  Güncellenen varyant: {stats['variants_updated']}")
    print(f"  Toplam etkilenen ürün: {len(bulk)}")


if __name__ == "__main__":
    asyncio.run(main())
