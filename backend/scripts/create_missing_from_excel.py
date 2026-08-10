"""
Excel'de (TicimaxExport 1) olup sistemde olmayan GERÇEK ürün kartlarını
doğrudan Excel verisinden oluşturur. "1 URUNKARTIID = 1 ürün" (tüm varyantlar
tek üründe). KARGO/BANKA KOMİSYONU/aciklama gibi ürün-olmayan kalemler atlanır.

Kullanım:
  python3 create_missing_from_excel.py /tmp/ticimax1.xls            # DRY-RUN
  python3 create_missing_from_excel.py /tmp/ticimax1.xls --apply
"""
import asyncio
import os
import sys
import re
import argparse
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from product_schema import ORDERED_COLUMNS, normalize_value, parse_price  # noqa: E402
from import_ticimax_113 import parse_varyasyon, build_typed_fields, _bc, _i  # noqa: E402

NON_PRODUCT = {"KARGO", "BANKA KOMİSYONU", "BANKA KOMISYONU", "ACIKLAMA", "AÇIKLAMA"}


def slugify(text):
    tr = {'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
          'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'}
    s = str(text or "").lower()
    for k, v in tr.items():
        s = s.replace(k, v)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-') or "urun"


async def main(path, apply):
    df = pd.read_excel(path, engine="openpyxl")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    sys_kartids = set()
    async for p in db.products.find({}, {"urun_karti_id": 1}):
        k = str(p.get("urun_karti_id") or "").strip()
        if k.isdigit():
            sys_kartids.add(k)

    # Excel kart -> satırlar
    cards = {}
    for _, r in df.iterrows():
        kid = _bc(r.get("URUNKARTIID"))
        if kid:
            cards.setdefault(kid, []).append(r.to_dict())

    missing = [k for k in cards if k not in sys_kartids]
    created = 0
    for kid in sorted(missing, key=lambda x: int(x)):
        rows = cards[kid]
        name = str(rows[0].get("URUNADI") or "").strip()
        if name.upper() in NON_PRODUCT:
            print(f"  atlandı (ürün değil): kart {kid} | {name}")
            continue

        rep = rows[0]
        typed = build_typed_fields(rep, matched_by_barcode=True)
        tf = {col: normalize_value(col, rep.get(col)) for col in ORDERED_COLUMNS}

        variants = []
        total_stock = 0
        for row in rows:
            color, size = parse_varyasyon(row.get("VARYASYON"))
            st = _i(row.get("STOKADEDI"), 0) or 0
            total_stock += st
            variants.append({
                "id": str(uuid.uuid4()),
                "size": size,
                "color": color,
                "barcode": _bc(row.get("BARKOD")),
                "stock_code": str(row.get("STOKKODU") or "").strip(),
                "variation_code": str(row.get("VARYASYONKODU") or "").strip(),
                "urun_id": _bc(row.get("URUNID")),
                "stock": st,
            })

        doc = {
            "id": str(uuid.uuid4()),
            "urun_karti_id": kid,
            "name": name,
            "slug": f"{slugify(name)}-{kid}",
            "stock_code": str(rep.get("STOKKODU") or "").strip(),
            "images": [],
            "variants": variants,
            "stock": total_stock,
            "ticimax_fields": tf,
            "attributes": {"Yaş Grubu": "Yetişkin", "Menşei": "TR"},
            # KARTAKTIF=0 olanlar pasif oluşturulur (storefront'u etkilemez)
            "is_active": bool(_i(rep.get("KARTAKTIF"), 0)),
            "is_featured": False,
            "source": "ticimax_excel_create",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **typed,
        }
        print(f"  + kart {kid} | {name} | varyant={len(variants)} | aktif={doc['is_active']}")
        if apply:
            await db.products.insert_one(doc)
        created += 1

    print(f"\n{'OLUŞTURULDU' if apply else 'DRY-RUN'}: {created} ürün")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.excel_path, args.apply))
