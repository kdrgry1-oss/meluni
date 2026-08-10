"""Trendyol'a fiyat güncellemesi gönderir: aktif ürünlerin listPrice/salePrice
değerlerine %MARKUP zammı uygulayıp Trendyol price-and-inventory API'sine basar.

Güncel v2 endpoint için TrendyolClient.update_price_and_inventory kullanılır.

Kullanım:
    DRY-RUN:  python -m scripts.push_trendyol_prices --markup 25
    CANLIYA:  python -m scripts.push_trendyol_prices --markup 25 --apply
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from trendyol_client import TrendyolClient  # noqa: E402


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def build_items(products, markup: float):
    factor = 1 + markup / 100.0
    items = []
    for p in products:
        base = float(p.get("price") or 0)
        sale = float(p.get("sale_price") or base)
        variants = p.get("variants") or []
        if variants:
            for v in variants:
                bc = (v.get("barcode") or "").strip()
                if not bc:
                    continue
                diff = float(v.get("price_diff") or 0)
                items.append({
                    "barcode": bc,
                    "quantity": int(v.get("stock") or 0),
                    "salePrice": round(sale * factor + diff, 2),
                    "listPrice": round(base * factor + diff, 2),
                })
        else:
            bc = (p.get("barcode") or "").strip()
            if bc:
                items.append({
                    "barcode": bc,
                    "quantity": int(p.get("stock") or 0),
                    "salePrice": round(sale * factor, 2),
                    "listPrice": round(base * factor, 2),
                })
    return items


async def main():
    markup = float(_arg("--markup", "25"))
    apply = "--apply" in sys.argv

    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    cfg = await db.settings.find_one({"id": "trendyol"}, {"_id": 0})
    if not cfg or not cfg.get("is_active"):
        print("Trendyol ayarı aktif değil.")
        return
    print(f"Trendyol: mode={cfg.get('mode')} | supplier={cfg.get('supplier_id')} | markup=%{markup}")

    products = await db.products.find(
        {"is_active": True},
        {"_id": 0, "id": 1, "name": 1, "price": 1, "sale_price": 1, "barcode": 1, "variants": 1},
    ).to_list(2000)

    items = build_items(products, markup)
    print(f"Aktif ürün: {len(products)} | gönderilecek barkod (item): {len(items)}")
    print("Örnekler:")
    for it in items[:6]:
        print(f"  {it['barcode']} | qty {it['quantity']} | list {it['listPrice']} | sale {it['salePrice']}")

    if not apply:
        print("\nMod: DRY-RUN (Trendyol'a GÖNDERİLMEDİ). Canlıya basmak için --apply ekleyin.")
        return

    client = TrendyolClient(
        supplier_id=cfg["supplier_id"], api_key=cfg["api_key"],
        api_secret=cfg["api_secret"], mode=cfg.get("mode", "live"),
    )

    # 100'lük partiler halinde gönder
    batch_ids = []
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        res = await client.update_price_and_inventory(chunk)
        bid = (res or {}).get("batchRequestId", "")
        batch_ids.append(bid)
        print(f"  parti {i // 100 + 1}: {len(chunk)} item → batchRequestId={bid} | resp={str(res)[:120]}")

    # Global markup ayarını güncelle (admin panelden tek yerden yönetilsin)
    from datetime import datetime, timezone
    await db.settings.update_one({"id": "main"}, {"$set": {"trendyol_markup": markup}}, upsert=True)
    await db.settings.update_one({"id": "trendyol"}, {"$set": {"default_markup": markup, "updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    print(f"\nUYGULANDI ✅ | {len(items)} item gönderildi | batch sayısı: {len(batch_ids)}")
    print(f"Global Trendyol markup ayarı %{markup} olarak kaydedildi (admin panelden değiştirilebilir).")
    print("Batch durumlarını Trendyol panelinden veya batch-request API'sinden kontrol edebilirsiniz.")
    print("batch_ids:", batch_ids)


if __name__ == "__main__":
    asyncio.run(main())
