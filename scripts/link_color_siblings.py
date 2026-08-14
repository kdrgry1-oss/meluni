"""
link_color_siblings.py — Renk kardeşlerini bağlar ve ortak model kodu verir.

SORUN: Tedarikçi aynı modelin her rengini AYRI ürün olarak veriyor (her birinin
kendi UrunKartiID'si var), ama hepsi ortak bir grup kodu taşıyor (OzelAlan2).
Aktarım o kodu okumuyordu; sonuç:
  • Vitrinde "diğer renkler" hiç çıkmıyordu — 6 rengi olan ürün tek renkmiş
    gibi görünüyordu.
  • Her renk ayrı model kodu alıyordu; pazaryerine (Trendyol) gönderildiğinde
    tek kart yerine altı ayrı ürün olarak listelenirdi.

Aktarım düzeltildi. Bu betik ZATEN AKTARILMIŞ ürünleri, tam bir yeniden
aktarım (~2,5 saat, besleme indirmesi) beklemeden bağlar.

Grup kodunu beslemeden DEĞİL, ürünün kendi `supplier_key`inden türetemeyiz —
bu yüzden besleme bir kez indirilir ve yalnız kimlik alanları okunur.

Yazdığı alanlar: urun_karti_id, supplier_group_ref, model_code, color
Dokunmadıkları: fiyat, stok, ad, kategori, görseller, barkod, stok kodu

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/link_color_siblings.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/link_color_siblings.py --uygula
"""
import argparse
import asyncio
import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Değişiklikleri YAZAR.")
    ap.add_argument("--dene", action="store_true", help="Yalnız rapor (varsayılan).")
    args = ap.parse_args()
    yaz = bool(args.uygula)

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                        # noqa: E402
    from routes import supplier_xml as sx             # noqa: E402
    import supplier_codes                             # noqa: E402

    cfg = await sx.get_config()
    supplier = cfg.get("supplier_name") or "modayakamoz"

    print("besleme indiriliyor…")
    records, _ = await sx.load_items()
    print(f"{len(records)} kayıt okundu\n")

    # supplier_key → (grup kodu, renk). Ürünler bu anahtarla eşleşiyor.
    bilgi = {}
    gruplar = collections.Counter()
    for rec in records:
        if not rec.get("model_ref"):
            continue
        key = supplier_codes.supplier_key(supplier, rec["model_ref"])
        grup = (rec.get("group_ref") or "").strip()
        renk = ""
        for v in (rec.get("variants") or []):
            if v.get("color"):
                renk = v["color"]
                break
        bilgi[key] = (grup, renk)
        if grup:
            gruplar[grup] += 1

    coklu = sum(1 for g, n in gruplar.items() if n > 1)
    print(f"beslemede {len(gruplar)} grup, bunların {coklu} tanesi çok renkli\n")

    toplam = eslesen = degisen = gruped = 0
    ornek = []
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}, "supplier_key": {"$exists": True}},
            {"_id": 0, "id": 1, "name": 1, "supplier_key": 1,
             "urun_karti_id": 1, "color": 1, "model_code": 1}):
        toplam += 1
        veri = bilgi.get(p.get("supplier_key"))
        if not veri:
            continue
        eslesen += 1
        grup, renk = veri
        yeni = {}
        if grup and p.get("urun_karti_id") != grup:
            yeni["urun_karti_id"] = grup
            yeni["supplier_group_ref"] = grup
        if renk and (p.get("color") or "") != renk:
            yeni["color"] = renk
        if grup and gruplar.get(grup, 0) > 1:
            # Ortak kart: aynı gruptaki renkler AYNI model kodunu paylaşır.
            # Stok kodu ve barkod varyant bazlı benzersiz kalır — onlara
            # dokunmuyoruz, envanter karışmasın.
            kod = await supplier_codes.get_or_create_model_code(
                db, supplier, p["supplier_key"].split("::", 1)[-1], grup) if yaz else "(hesaplanacak)"
            if yaz and p.get("model_code") != kod:
                yeni["model_code"] = kod
            gruped += 1
        if not yeni:
            continue
        if len(ornek) < 10:
            ornek.append((p.get("name", "")[:44], grup, renk))
        if yaz:
            await db.products.update_one({"id": p["id"]}, {"$set": yeni})
        degisen += 1

    for ad, grup, renk in ornek:
        print(f"  {ad:<44} grup={grup} renk={renk or '(yok)'}")
    print()
    print(f"tedarikçiden gelen ürün : {toplam}")
    print(f"beslemede eşleşen       : {eslesen}")
    print(f"{'güncellendi' if yaz else 'güncellenecek'}           : {degisen}")
    print(f"çok renkli gruba giren  : {gruped}")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
