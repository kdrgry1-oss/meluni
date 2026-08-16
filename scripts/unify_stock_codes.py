"""
unify_stock_codes.py — Renk kardeşlerine ORTAK stok kodu verir.

KURAL: stok kodu KART düzeyindedir — aynı modelin bütün renkleri aynı stok
kodunu taşır. Barkod VARYANT düzeyindedir ve her zaman benzersizdir; bu betik
barkodlara ve varyantların kendi kodlarına DOKUNMAZ.

NEDEN: ürüne ilk varyantın kodu yazılıyordu ("MEL-M006783-01"), her renk farklı
görünüyordu. Panelden stok koduyla gönderince altı rengin yalnız biri gidiyor,
pazaryerinde renkler tek kartta toplanmıyordu.

Ortak kod, ürünün model kodudur (`model_code`, ör. "MEL-M006783"); yoksa
grubun ilk ürününün stok kodundan türetilir (sondaki "-01" atılır).

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/unify_stock_codes.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/unify_stock_codes.py --uygula
"""
import argparse
import asyncio
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _kart_kodu(p) -> str:
    """Ürünün kart düzeyi stok kodu: model kodu, yoksa stok kodunun kökü."""
    mk = str(p.get("model_code") or "").strip()
    if mk:
        return mk
    sk = str(p.get("stock_code") or "").strip()
    return re.sub(r"-\d{1,3}$", "", sk)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Kodları YAZAR.")
    ap.add_argument("--liste", type=int, default=12, help="Kaç örnek yazılsın.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                     # noqa: E402
    from group_ref import gecerli_grup             # noqa: E402

    gruplar = defaultdict(list)
    tekil = 0
    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "stock_code": 1, "model_code": 1,
             "urun_karti_id": 1, "csv_card_id": 1, "color": 1}):
        anahtar = str(p.get("urun_karti_id") or p.get("csv_card_id") or "").strip()
        if not anahtar or not gecerli_grup(anahtar):
            tekil += 1
            continue
        gruplar[anahtar].append(p)

    degisen = grup_sayisi = 0
    ornekler = []
    for anahtar, urunler in gruplar.items():
        kodlar = {_kart_kodu(u) for u in urunler if _kart_kodu(u)}
        if not kodlar:
            continue
        ortak = sorted(kodlar)[0]           # aynı grupta birden çok kök varsa en küçüğü
        hedefler = [u for u in urunler if str(u.get("stock_code") or "").strip() != ortak]
        if not hedefler:
            continue
        grup_sayisi += 1
        for u in hedefler:
            degisen += 1
            if len(ornekler) < args.liste:
                ornekler.append((u.get("name", "")[:44], u.get("color") or "-",
                                 str(u.get("stock_code") or "-"), ortak))
            if args.uygula:
                await db.products.update_one({"id": u["id"]},
                                             {"$set": {"stock_code": ortak}})

    for ad, renk, eski, yeni in ornekler:
        print(f"  {ad:<46} {renk:<12} {eski:<20} → {yeni}")
    if degisen > len(ornekler):
        print(f"  … ve {degisen - len(ornekler)} ürün daha")
    print()
    print(f"renk grubu             : {len(gruplar)}")
    print(f"kodu değişecek grup    : {grup_sayisi}")
    print(f"{'kodu değişen ürün' if args.uygula else 'kodu değişecek ürün'}    : {degisen}")
    print(f"grubu olmayan ürün     : {tekil}  (dokunulmadı)")
    print("\nBarkodlara ve varyant stok kodlarına DOKUNULMADI.")
    if not args.uygula:
        print("Hiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
