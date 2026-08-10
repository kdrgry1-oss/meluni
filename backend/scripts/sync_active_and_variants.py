"""
Sistemi Excel'e (TicimaxExport 1) göre senkronlar:
  1) is_active = (KARTAKTIF == 1)  [Excel kaynak doğru]
  2) Eksik varyantları (barkodları) Excel'den ekler
  3) stok'u varyant toplamına göre günceller

Kullanım:
  python3 sync_active_and_variants.py /tmp/ticimax1.xls            # DRY-RUN
  python3 sync_active_and_variants.py /tmp/ticimax1.xls --apply
"""
import asyncio
import os
import sys
import uuid
import argparse
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from import_ticimax_113 import parse_varyasyon, _bc, _i  # noqa: E402

NON = {"KARGO", "BANKA KOMİSYONU", "BANKA KOMISYONU", "ACIKLAMA", "AÇIKLAMA"}


async def main(path, apply):
    df = pd.read_excel(path, engine="openpyxl")
    cards = defaultdict(list)
    kartaktif = {}
    for _, r in df.iterrows():
        if str(r.get("URUNADI") or "").strip().upper() in NON:
            continue
        kid = _bc(r.get("URUNKARTIID"))
        if not kid:
            continue
        cards[kid].append(r.to_dict())
        ka = _i(r.get("KARTAKTIF"), 0) or 0
        kartaktif[kid] = max(kartaktif.get(kid, 0), ka)

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc).isoformat()

    stats = {"activated": 0, "deactivated": 0, "variants_added": 0, "products_touched": 0}
    will_active = 0

    async for p in db.products.find({"is_deleted": {"$ne": True}}):
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid not in cards:
            if p.get("is_active"):
                will_active += 1
            continue

        target_active = bool(kartaktif.get(kid, 0))
        if target_active:
            will_active += 1
        set_doc = {}

        # Güvenli: yalnızca KARTAKTIF=1 olup pasif kalanları AKTİVE et; asla pasife alma
        if target_active and not bool(p.get("is_active")):
            set_doc["is_active"] = True
            stats["activated"] += 1

        # Eksik varyantları ekle
        variants = list(p.get("variants") or [])
        have = set(_bc(v.get("barcode")) for v in variants if v.get("barcode"))
        added = 0
        for row in cards[kid]:
            vbc = _bc(row.get("BARKOD"))
            if not vbc or vbc in have:
                continue
            color, size = parse_varyasyon(row.get("VARYASYON"))
            variants.append({
                "id": str(uuid.uuid4()),
                "size": size,
                "color": color,
                "barcode": vbc,
                "stock_code": str(row.get("STOKKODU") or "").strip(),
                "variation_code": str(row.get("VARYASYONKODU") or "").strip(),
                "urun_id": _bc(row.get("URUNID")),
                "stock": _i(row.get("STOKADEDI"), 0) or 0,
            })
            have.add(vbc)
            added += 1

        if added:
            set_doc["variants"] = variants
            set_doc["stock"] = sum((v.get("stock") or 0) for v in variants)
            stats["variants_added"] += added

        if set_doc:
            set_doc["updated_at"] = now
            stats["products_touched"] += 1
            if apply:
                await db.products.update_one({"id": p["id"]}, {"$set": set_doc})

    print(f"{'APPLIED' if apply else 'DRY-RUN'}")
    print(f"  Aktive edilen: {stats['activated']}")
    print(f"  Pasife alınan: {stats['deactivated']}")
    print(f"  Eklenen eksik varyant: {stats['variants_added']}")
    print(f"  Etkilenen ürün: {stats['products_touched']}")
    print(f"  Hizalama sonrası aktif olacak (tahmini): {will_active}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.excel_path, args.apply))
