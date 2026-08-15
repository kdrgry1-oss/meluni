"""
diag_stock_sync.py — Stok senkronunun neden sıfırladığını gösterir. YAZMAZ.

Katalog bir turda 0 stoğa düştüyse iki ihtimal vardır ve ikisi bambaşkadır:

  A) Tedarikçide gerçekten stok yok.
  B) Ürün varyantlarını beslemedeki varyantlarla EŞLEŞTİREMİYORUZ; eşleşmeyen
     her varyanta 0 yazılıyor. (Kod üretimi değişince tam bu oldu: renk
     kardeşleri için model kodu grup koduna bağlandı, eski ürünlerin kayıtlı
     stok kodu artık üretilenle tutmuyor.)

Bu betik ikisini ayırır: kaç varyant hangi anahtarla eşleşiyor, eşleşenlerin
beslemedeki stoğu ne, eşleşmeyenler hangi ürünler.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/diag_stock_sync.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/diag_stock_sync.py --ornek 20
"""
import argparse
import asyncio
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ornek", type=int, default=10, help="Kaç örnek yazılsın.")
    ap.add_argument("--limit", type=int, default=400,
                    help="İncelenecek besleme kaydı sayısı (0 = hepsi). "
                         "Teşhis için örnek yeter; tamamı binlerce tur atar.")
    args = ap.parse_args()

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
    min_stock = int(cfg.get("min_stock", sx.MIN_STOCK_DEFAULT))

    # Besleme indirmesi tek başına uzun sürüyor (tedarikçinin tüm kataloğu).
    # Ekranda hiçbir şey akmayınca betik donmuş sanılıyor; ne beklendiğini yaz.
    print("besleme indiriliyor (tedarikçinin tüm kataloğu — birkaç dakika sürebilir)…",
          flush=True)
    records, _ = await sx.load_items()
    if args.limit:
        # Baştan almak yerine ARALIKLI örnek: besleme sıralı geldiği için ilk N
        # kayıt tek bir kategoriden olabiliyor, tablo yanıltıyordu.
        adim = max(1, len(records) // args.limit)
        records = records[::adim][:args.limit]
    print(f"{len(records)} besleme kaydı, asgari stok eşiği: {min_stock}\n")

    urun_yok = 0
    urun_var = 0
    hic_eslesmeyen = 0
    v_toplam = v_eslesen = 0
    anahtar_dagilim = {"sb": 0, "ss": 0, "kod": 0, "ref": 0, "bs": 0}
    besleme_stok_dolu = besleme_stok_esik_alti = 0
    ornekler = []

    for _i, rec in enumerate(records, 1):
        if _i % 50 == 0:
            print(f"  … {_i}/{len(records)} kayıt incelendi", flush=True)
        key = supplier_codes.supplier_key(supplier, rec["model_ref"])
        product = await db.products.find_one(
            {"supplier_key": key},
            {"_id": 0, "id": 1, "name": 1, "variants": 1, "stock": 1})
        if not product:
            urun_yok += 1
            continue
        urun_var += 1

        # KODLARI YAZMADAN OKU. get_or_create_variant_codes eksik kaydı
        # OLUŞTURUR — teşhis betiği veriyi değiştirmemeli. Eşleme tablosunu
        # tek turda okuyup mevcut kodları kullanıyoruz; kayıt yoksa o anahtar
        # atlanıyor (zaten tedarikçi kimlikleri asıl eşleme yolu).
        gkey = supplier_codes.supplier_key(
            supplier, rec.get("group_ref") or rec["model_ref"])
        harita_doc = await db.supplier_code_map.find_one(
            {"key": gkey}, {"_id": 0, "variants": 1})
        kayitli = (harita_doc or {}).get("variants") or {}
        kodlar = []
        for v in rec["variants"]:
            vref = str(v.get("ref") or "0").strip().lower()
            if rec.get("group_ref"):
                vref = f"{str(rec['model_ref']).strip().lower()}|{vref}"
            vkey = re.sub(r"[.$\x00]", "_", vref) or "0"
            kodlar.append((kayitli.get(vkey) or {}).get("stock_code") or "")
        harita = sx.besleme_haritasi(rec["variants"], kodlar)

        eslesen = 0
        for v in (product.get("variants") or []):
            v_toplam += 1
            src = sx.varyant_bul(v, harita)
            if src is None:
                continue
            eslesen += 1
            v_eslesen += 1
            # Hangi anahtar tuttu? (teşhis için tek tek dene)
            for tur, deger in (("sb", v.get("supplier_barcode")),
                               ("ss", v.get("supplier_stock_code")),
                               ("kod", v.get("stock_code"))):
                if deger and harita.get(f"{tur}:{sx._t(deger)}") is not None:
                    anahtar_dagilim[tur] += 1
                    break
            else:
                anahtar_dagilim["bs"] += 1
            _st = int(src.get("stock") or 0)
            if _st > min_stock:
                besleme_stok_dolu += 1
            else:
                besleme_stok_esik_alti += 1

        if (product.get("variants") and rec.get("variants") and eslesen == 0):
            hic_eslesmeyen += 1
            if len(ornekler) < args.ornek:
                uv = (product.get("variants") or [])[0]
                bv = (rec.get("variants") or [])[0]
                ornekler.append((
                    product.get("name", "")[:40],
                    f"bizde stok_code={uv.get('stock_code')} "
                    f"sb={uv.get('supplier_barcode') or '-'} "
                    f"beden={uv.get('size') or '-'}",
                    f"beslemede ref={bv.get('ref')} "
                    f"sb={bv.get('supplier_barcode') or '-'} "
                    f"beden={bv.get('size') or '-'}"))

    print("── ÜRÜN DÜZEYİ ─────────────────────────────────────────")
    print(f"beslemede olup bizde bulunan   : {urun_var}")
    print(f"beslemede olup bizde OLMAYAN   : {urun_yok}")
    print(f"HİÇBİR varyantı eşleşmeyen     : {hic_eslesmeyen}"
          f"  {'← EŞLEME ARIZASI' if hic_eslesmeyen > urun_var * 0.1 else ''}")
    print()
    print("── VARYANT DÜZEYİ ──────────────────────────────────────")
    print(f"toplam ürün varyantı           : {v_toplam}")
    print(f"beslemeyle eşleşen             : {v_eslesen}")
    print(f"eşleşmeyen (0 yazılacak olan)  : {v_toplam - v_eslesen}")
    print()
    print("hangi anahtarla eşleşti:")
    print(f"  tedarikçi barkodu            : {anahtar_dagilim['sb']}")
    print(f"  tedarikçi stok kodu          : {anahtar_dagilim['ss']}")
    print(f"  bizim stok kodumuz           : {anahtar_dagilim['kod']}")
    print(f"  beden+renk (son çare)        : {anahtar_dagilim['bs']}")
    print()
    print("── EŞLEŞENLERİN BESLEMEDEKİ STOĞU ──────────────────────")
    print(f"eşiğin ÜSTÜNDE (satılabilir)   : {besleme_stok_dolu}")
    print(f"eşiğin altında (0 yazılır)     : {besleme_stok_esik_alti}")
    print()
    if ornekler:
        print("── HİÇ EŞLEŞMEYEN ÜRÜN ÖRNEKLERİ ───────────────────────")
        for ad, bizde, beslemede in ornekler:
            print(f"  {ad}")
            print(f"      {bizde}")
            print(f"      {beslemede}")
    print("\nHiçbir şey yazılmadı — bu betik yalnız okur.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
