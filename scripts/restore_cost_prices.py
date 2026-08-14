"""
restore_cost_prices.py — Kaybolan alış (maliyet) fiyatlarını geri yükler.

Alış fiyatı kârlılık raporlarının, dropshipping alım listesinin ve satış
fiyatı hesabının dayanağı. Ürün güncelleme ucu gelen gövdeyi olduğu gibi
yazdığı için, admin formu alanı boş/0 gönderdiğinde dolu maliyet siliniyordu.
(Uç düzeltildi: boş/sıfır gelen maliyet artık mevcut değeri ezmiyor.)

Bu betik kaybolanları BESLEMEDEN geri getirir — tek güvenilir kaynak orası.
Eşleşme `supplier_key` üzerinden yapılır.

Varsayılan olarak YALNIZ maliyeti eksik olan ürünlere dokunur; dolu olanı
değiştirmez. `--hepsi` verilirse beslemedeki değerle tamamı tazelenir.

Satış fiyatına DOKUNMAZ. Maliyet geri geldikten sonra fiyatları yeniden
hesaplamak isterseniz: scripts/reprice_products.py --katsayi 3 --uygula

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/restore_cost_prices.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/restore_cost_prices.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _sayi(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    ap.add_argument("--hepsi", action="store_true",
                    help="Dolu olanları da beslemedeki değerle tazeler.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                 # noqa: E402
    from routes import supplier_xml as sx      # noqa: E402
    import supplier_codes                      # noqa: E402

    cfg = await sx.get_config()
    supplier = cfg.get("supplier_name") or "modayakamoz"

    print("besleme indiriliyor…")
    records, _ = await sx.load_items()
    fiyat = {}
    for rec in records:
        if not rec.get("model_ref"):
            continue
        key = supplier_codes.supplier_key(supplier, rec["model_ref"])
        fiyat[key] = _sayi(rec.get("price"))
    print(f"{len(fiyat)} ürünün bayi fiyatı okundu\n")

    # ÖNCE TANI: "hâlâ işlenmemiş" şikayetinin sebebi çoğu zaman betiğin hiç
    # çalışmaması değil, alanın başka isimde dolu olması ya da ürünün beslemede
    # bulunamaması. Neyin ne durumda olduğunu açıkça sayıyoruz.
    tani = {"cost_dolu": 0, "purchase_dolu": 0, "ikisi_de_bos": 0,
            "varyant_dolu": 0, "supplier_key_yok": 0}
    async for _p in db.products.find({"is_deleted": {"$ne": True}},
                                     {"_id": 0, "cost_price": 1, "purchase_price": 1,
                                      "supplier_key": 1, "variants": 1}):
        if _sayi(_p.get("cost_price")) > 0:
            tani["cost_dolu"] += 1
        if _sayi(_p.get("purchase_price")) > 0:
            tani["purchase_dolu"] += 1
        if _sayi(_p.get("cost_price")) <= 0 and _sayi(_p.get("purchase_price")) <= 0:
            tani["ikisi_de_bos"] += 1
            if any(_sayi(v.get("cost_price")) > 0 or _sayi(v.get("purchase_price")) > 0
                   for v in (_p.get("variants") or [])):
                tani["varyant_dolu"] += 1
        if not _p.get("supplier_key"):
            tani["supplier_key_yok"] += 1
    print("mevcut durum:")
    print(f"  cost_price dolu        : {tani['cost_dolu']}")
    print(f"  purchase_price dolu    : {tani['purchase_dolu']}")
    print(f"  ikisi de boş           : {tani['ikisi_de_bos']}")
    print(f"    …ama varyantta dolu  : {tani['varyant_dolu']}  (varyanttan kurtarılabilir)")
    print(f"  supplier_key yok       : {tani['supplier_key_yok']}  (beslemeden gelmemiş)")
    print()

    toplam = eksik = duzelen = eslesmeyen = varyanttan = 0
    ornek = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}, "supplier_key": {"$exists": True}},
            {"_id": 0, "id": 1, "name": 1, "supplier_key": 1, "price": 1,
             "cost_price": 1, "purchase_price": 1, "variants": 1}):
        toplam += 1
        mevcut = _sayi(p.get("cost_price")) or _sayi(p.get("purchase_price"))
        if mevcut > 0 and not args.hepsi:
            continue
        eksik += 1
        yeni = fiyat.get(p.get("supplier_key") or "")
        if not yeni:
            # Beslemede yok — ama varyantlarından biri maliyet taşıyor olabilir
            # (ürün seviyesi silinmiş, varyant kalmış). Onu kullan.
            for v in (p.get("variants") or []):
                aday = _sayi(v.get("cost_price")) or _sayi(v.get("purchase_price"))
                if aday > 0:
                    yeni = aday
                    varyanttan += 1
                    break
        if not yeni:
            eslesmeyen += 1
            continue

        # Varyant maliyetleri de aynı kaynaktan; varyant bazlı ayrı fiyat
        # beslemede yok, ürün maliyeti tüm bedenler için geçerli.
        variants = []
        for v in (p.get("variants") or []):
            if _sayi(v.get("cost_price")) <= 0 or args.hepsi:
                v = {**v, "cost_price": yeni, "purchase_price": yeni}
            variants.append(v)

        if len(ornek) < 10:
            ornek.append((p.get("name", "")[:46], mevcut, yeni))
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": {
                "cost_price": yeni, "purchase_price": yeni, "variants": variants,
            }})
        duzelen += 1

    for ad, eski, yeni in ornek:
        print(f"  {ad:<46} {eski:>8.2f} → {yeni:>8.2f}")
    print()
    print(f"tedarikçiden gelen ürün : {toplam}")
    print(f"maliyeti eksik olan     : {eksik}")
    print(f"{'geri yüklendi' if yaz else 'geri yüklenecek'}         : {duzelen}")
    print(f"varyanttan kurtarılan   : {varyanttan}")
    print(f"hiçbir kaynakta yok     : {eslesmeyen}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
