"""
Sistemi TicimaxExport(1).xls'teki 349 URUNKARTIID listesine göre hizalar.

- Silinecekler: urun_karti_id'si Excel'in 349 kart setinde OLMAYAN ürünler
  + geçerli kart id'si olmayan çöp kayıtlar (KARGO/BANKA KOMİSYONU/DENEME/csv_xml_merge vb.)
- GÜVENLİK: silmeden önce silinecek tüm dökümanların TAM yedeği JSON'a yazılır.
  Geri yükleme: restore_backup.py <backup.json>

Kullanım:
  python3 align_to_ticimax_349.py /tmp/ticimax1.xls            # DRY-RUN (rapor + yedek)
  python3 align_to_ticimax_349.py /tmp/ticimax1.xls --apply    # yedekle + sil
"""
import asyncio
import os
import sys
import json
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def bc(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


async def main(path, apply):
    df = pd.read_excel(path, engine="openpyxl")
    excel_kartids = set(bc(x) for x in df["URUNKARTIID"].dropna().unique())
    print(f"Excel kart (ürün) sayısı: {len(excel_kartids)}")

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    to_delete = []
    keep = 0
    async for p in db.products.find({}):
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid.isdigit() and kid in excel_kartids:
            keep += 1
        else:
            to_delete.append(p)

    print(f"Korunacak (349 kart eşleşen): {keep}")
    print(f"Silinecek (Excel'de olmayan): {len(to_delete)}")

    # Yedek
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"deleted_products_{ts}.json")

    def _ser(doc):
        d = dict(doc)
        d.pop("_id", None)
        return d

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump([_ser(d) for d in to_delete], f, ensure_ascii=False, default=str, indent=2)
    print(f"Yedek yazıldı: {backup_path} ({len(to_delete)} kayıt)")

    print("\n--- Silinecek ürünler ---")
    for p in sorted(to_delete, key=lambda x: str(x.get("urun_karti_id") or "")):
        print(f"  kart={p.get('urun_karti_id')} | {p.get('name')} | aktif={p.get('is_active')}")

    if apply:
        ids = [p["id"] for p in to_delete]
        res = await db.products.delete_many({"id": {"$in": ids}})
        print(f"\nSİLİNDİ: {res.deleted_count} kayıt. Yedek: {backup_path}")
    else:
        print("\nDRY-RUN — silme yapılmadı. Uygulamak için --apply ekle.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.excel_path, args.apply))
