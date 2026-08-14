"""
reprice_products.py — Satış fiyatlarını alış fiyatına göre yeniden hesaplar.

Kâr katsayısı değiştiğinde tüm katalogu yeniden aktarmak (~2,5 saat, besleme
indirmesi) gerekmiyor: alış fiyatı her üründe ve her varyantta `cost_price`
alanında duruyor. Bu betik satış fiyatını oradan yeniden üretir.

FORMÜL — beslemedeki fiyat BAYİ fiyatıdır ve KDV HARİÇtir:

    satış = alış × katsayı × (1 + KDV)

Yani katsayı 3 ve KDV %10 ise müşterinin gördüğü fiyat alışın 3,30 katıdır.
Müşteri fiyatının TAM 3 katı olmasını istiyorsanız `--kdv-dahil` verin; o zaman
katsayı KDV dahil fiyata uygulanır ve satış = alış × 3 olur.

Dokunulanlar : price (ürün ve varyant)
Dokunulmayan : alış fiyatı, stok, ad, kategori, tedarikçi kodları, indirimler

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/reprice_products.py --katsayi 3 --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/reprice_products.py --katsayi 3 --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--katsayi", type=float, required=True, help="Kâr katsayısı (ör. 3).")
    ap.add_argument("--kdv", type=float, default=10.0, help="Ürün KDV yoksa kullanılacak oran (%%).")
    ap.add_argument("--kdv-dahil", action="store_true",
                    help="Katsayıyı KDV DAHİL fiyata uygula → satış = alış × katsayı.")
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    args = ap.parse_args()
    yaz = bool(args.uygula)
    if args.katsayi <= 0:
        print("Katsayı sıfırdan büyük olmalı.")
        return 1

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402
    from routes.supplier_xml import sale_price       # noqa: E402

    toplam = degisen = maliyetsiz = 0
    ornekler = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "price": 1, "cost_price": 1,
             "purchase_price": 1, "vat_rate": 1, "vat_included": 1, "variants": 1}):
        toplam += 1
        vat = float(p.get("vat_rate") or args.kdv)
        # --kdv-dahil verildiyse KDV eklemesini atlıyoruz: katsayı son fiyatı verir.
        vat_included = True if args.kdv_dahil else bool(p.get("vat_included"))

        def hesapla(maliyet) -> float:
            return sale_price(float(maliyet or 0), vat_rate=vat,
                              vat_included=vat_included, markup=args.katsayi)

        p_cost = p.get("cost_price") or p.get("purchase_price") or 0
        yeni_variants = []
        variant_degisti = False
        for v in (p.get("variants") or []):
            v_cost = v.get("cost_price") or v.get("purchase_price") or p_cost
            v_yeni = hesapla(v_cost)
            if v_cost and v_yeni != round(float(v.get("price") or 0), 2):
                v = {**v, "price": v_yeni}
                variant_degisti = True
            yeni_variants.append(v)

        if not p_cost:
            maliyetsiz += 1
            if not variant_degisti:
                continue

        yeni_fiyat = hesapla(p_cost) if p_cost else float(p.get("price") or 0)
        eski_fiyat = round(float(p.get("price") or 0), 2)
        if yeni_fiyat == eski_fiyat and not variant_degisti:
            continue

        if len(ornekler) < 12:
            ornekler.append((p.get("name"), float(p_cost or 0), eski_fiyat, yeni_fiyat))
        if yaz:
            upd = {"price": yeni_fiyat}
            if variant_degisti:
                upd["variants"] = yeni_variants
            await db.products.update_one({"id": p["id"]}, {"$set": upd})
        degisen += 1

    for ad, maliyet, eski, yeni in ornekler:
        print(f"  {ad[:52]:<52} alış {maliyet:>8.2f}   {eski:>9.2f} → {yeni:>9.2f}")
    print()
    kdv_notu = "KDV dahil fiyata uygulandı" if args.kdv_dahil else f"üzerine %{args.kdv:g} KDV eklendi"
    print(f"katsayı           : {args.katsayi:g}  ({kdv_notu})")
    print(f"toplam ürün       : {toplam}")
    print(f"{'fiyatı değişti' if yaz else 'fiyatı değişecek'}    : {degisen}")
    print(f"alış fiyatı yok   : {maliyetsiz}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
