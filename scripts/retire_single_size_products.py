"""
retire_single_size_products.py — Tek bedene düşmüş, o bedeni de biten ürünleri
katalogdan kaldırır ve beslemeye kalıcı olarak kapatır.

Kural (yönetim isteği): Bir ürünün diğer bedenleri tükenmişse ve elde kalan TEK
bedenin stoğu eşiğin (varsayılan 9) altındaysa o ürün ölmüştür. Vitrinde
kategoriyi doluymuş gibi gösterir, müşterilerin çoğu kendi bedenini bulamadan
sayfayı terk eder; gelen tek tük sipariş de tedarikçide bulunamama riski taşır.

Ne yapar:
  • Ürünü siler (SOFT delete: `is_deleted`) — geçmiş siparişler ürüne referans
    verdiği için kayıt yok edilmez, yoksa sipariş detayı bozulur.
  • `supplier_key`'i kara listeye yazar → XML aktarımı ürünü BİR DAHA getirmez.

KALICI BİR KARARDIR. Tedarikçi sonradan stok bassa bile ürün geri gelmez;
geri almak için panelden (Ürün → kara listeden çıkar) elle açmak gerekir.
Bu yüzden varsayılan YALNIZ RAPORDUR; yazmak için `--uygula` gerekir.

Aynı kural içe aktarma ve stok senkronunda otomatik çalışır
(routes/supplier_xml.py). Bu betik MEVCUT katalog için tek seferliktir.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/retire_single_size_products.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/retire_single_size_products.py --uygula
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    ap.add_argument("--esik", type=int, default=None,
                    help="Tek beden stok eşiği (varsayılan: ayarlardaki değer, 9).")
    ap.add_argument("--liste", type=int, default=25, help="Kaç örnek yazılsın.")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                 # noqa: E402
    from routes import supplier_xml as sx      # noqa: E402

    cfg = await sx.get_config()
    esik = args.esik if args.esik is not None else int(
        cfg.get("single_size_max_stock", sx.SINGLE_SIZE_MAX_STOCK_DEFAULT))
    print(f"tek beden stok eşiği: {esik}\n")

    taranan = uyan = kapatilan = anahtarsiz = 0
    ornekler = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "supplier": 1, "supplier_key": 1,
             "variants": 1}):
        taranan += 1
        kapat, kalan, beden = sx.tek_beden_bitiyor(p.get("variants") or [], esik)
        if not kapat:
            continue
        uyan += 1
        if len(ornekler) < args.liste:
            ornekler.append((p.get("name", "")[:52], beden or "tek ebat", kalan,
                             bool(p.get("supplier_key"))))
        if not p.get("supplier_key"):
            anahtarsiz += 1
        if yaz:
            await sx.kapat_ve_engelle(
                p, f"tek beden ({beden or 'tek ebat'}) kaldı, stok {kalan} <= {esik}",
                kalan, beden)
            kapatilan += 1

    for ad, beden, kalan, anahtarli in ornekler:
        isaret = "" if anahtarli else "  (tedarikçi anahtarı yok — kara liste gerekmez)"
        print(f"  {ad:<52} {beden:>10}  stok {kalan:>2}{isaret}")
    if uyan > len(ornekler):
        print(f"  … ve {uyan - len(ornekler)} ürün daha")
    print()
    print(f"taranan ürün            : {taranan}")
    print(f"kurala uyan             : {uyan}")
    print(f"{'kapatıldı' if yaz else 'kapatılacak'}             : {kapatilan if yaz else uyan}")
    print(f"tedarikçi anahtarı yok  : {anahtarsiz}  (silinir ama kara listeye yazılmaz)")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
        print("DİKKAT: kapatma KALICIDIR — besleme bu ürünleri bir daha getirmez.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
