"""
UrunTeknikDetaylari (uzun-format) teknik özellik import'u.

Excel kolonları: UrunKartID, StokKodu, UrunAdi, Tanim, Ozellik, Deger
Her satır bir özellik (ör. Ozellik='Kumaş Tipi', Deger='Dokuma').

- UrunKartID'ye göre gruplanır ve {Ozellik: Deger} sözlüğü çıkarılır.
- Ürünler urun_karti_id / stock_code / barkod ile eşleştirilir.
- Özellikler ürünün `attributes` sözlüğüne düz anahtar olarak yazılır
  (mevcut 'Cep', 'Kalıp' gibi düz anahtarlarla aynı format) — overwrite.

Kullanım:
  python3 import_technical_details.py /tmp/teknik.xls          # DRY-RUN
  python3 import_technical_details.py /tmp/teknik.xls --apply
"""
import asyncio
import os
import sys
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pandas as pd  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def _bc(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v)))
    except Exception:
        return str(v).strip()


async def main(path, apply):
    df = pd.read_excel(path, engine="openpyxl")
    print(f"Teknik Excel: {len(df)} satır, {len(df.columns)} kolon")

    # UrunKartID -> {Ozellik: Deger}
    by_kart = {}
    by_stok = {}
    for _, r in df.iterrows():
        oz = str(r.get("Ozellik") or "").strip()
        dg = r.get("Deger")
        if not oz or pd.isna(dg):
            continue
        dg = str(dg).strip()
        kid = _bc(r.get("UrunKartID"))
        sk = _bc(r.get("StokKodu"))
        if kid:
            by_kart.setdefault(kid, {})[oz] = dg
        if sk:
            by_stok.setdefault(sk, {})[oz] = dg

    print(f"Benzersiz kart: {len(by_kart)} | benzersiz stok kodu: {len(by_stok)}")

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    stats = {"matched": 0, "no_match": 0, "attrs_written": 0}

    async for p in db.products.find({}):
        attrs_src = None
        kid = str(p.get("urun_karti_id") or "").strip()
        if kid and kid in by_kart:
            attrs_src = by_kart[kid]
        if attrs_src is None:
            for c in (p.get("stock_code"), p.get("barcode")):
                cc = _bc(c)
                if cc and cc in by_stok:
                    attrs_src = by_stok[cc]
                    break
        if attrs_src is None:
            for v in (p.get("variants") or []):
                for c in (v.get("barcode"), v.get("stock_code")):
                    cc = _bc(c)
                    if cc and cc in by_stok:
                        attrs_src = by_stok[cc]
                        break
                if attrs_src:
                    break
        if attrs_src is None:
            stats["no_match"] += 1
            continue

        stats["matched"] += 1
        existing = p.get("attributes")
        # attributes dict (düz anahtar) formatına normalize et
        merged = {}
        if isinstance(existing, dict):
            merged = dict(existing)
        elif isinstance(existing, list):
            for a in existing:
                k = a.get("type") or a.get("name")
                if k:
                    merged[k] = a.get("value")
        for oz, dg in attrs_src.items():
            merged[oz] = dg
            stats["attrs_written"] += 1

        if apply:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {"attributes": merged, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_path")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.excel_path, args.apply))
