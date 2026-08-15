"""
append_color_to_names.py — Ürün adının SONUNA rengini ekler.

NEDEN: Aynı modelin her rengi ayrı ürün kartı; adları birebir aynı olunca
listede, aramada, siparişte ve pazaryerinde hangi renk olduğu görünmüyor
("Kısa Kollu Aerobin Tulum" × 4). Renk adın sonuna gelince hem müşteri hem
biz ayırt ediyoruz, ayrıca vitrin renk-kardeşi yedek eşleştirmesi (ad sonundaki
rengi soyup taban adı karşılaştırır) doğru çalışıyor.

SLUG'A DOKUNULMAZ: adres değişirse mevcut linkler ve arama motoru kayıtları
kırılır. Yalnız görünen ad güncellenir.

Renk ürünün `color` alanından, yoksa varyantların ortak renginden alınır.
Ad zaten o renkle bitiyorsa dokunulmaz (tekrar tekrar eklenmez).

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/append_color_to_names.py
    ~/apps/meluni/shared/venv/bin/python ../scripts/append_color_to_names.py --uygula
    ... --kategori takım
"""
import argparse
import asyncio
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

_TR_UP = {"i": "İ", "ı": "I", "ö": "Ö", "ü": "Ü", "ş": "Ş", "ç": "Ç", "ğ": "Ğ"}


def _kucult(s) -> str:
    return str(s or "").replace("I", "ı").replace("İ", "i").lower().strip()


def _basliklastir(s: str) -> str:
    """Türkçe başlık biçimi: 'AÇIK mavi' → 'Açık Mavi' (i→İ tuzağı dahil)."""
    parcalar = []
    for k in re.split(r"\s+", str(s or "").strip()):
        if not k:
            continue
        ilk = _TR_UP.get(k[0].lower(), k[0].upper())
        parcalar.append(ilk + _kucult(k[1:]))
    return " ".join(parcalar)


def _urun_rengi(p) -> str:
    renk = str(p.get("color") or "").strip()
    if renk:
        return renk
    renkler = {str(v.get("color") or "").strip()
               for v in (p.get("variants") or []) if isinstance(v, dict)}
    renkler = {r for r in renkler if r}
    return renkler.pop() if len(renkler) == 1 else ""   # iki renk varsa ada yazılmaz


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="Adları YAZAR.")
    ap.add_argument("--kategori", default="", help="Yalnız adı eşleşen kategori.")
    ap.add_argument("--limit", type=int, default=0, help="En fazla N ürün işle.")
    ap.add_argument("--liste", type=int, default=12, help="Kaç örnek yazılsın.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                            # noqa: E402

    sorgu = {"is_deleted": {"$ne": True}}
    if args.kategori:
        sorgu["$or"] = [
            {"category_name": {"$regex": args.kategori, "$options": "i"}},
            {"category_path": {"$regex": args.kategori, "$options": "i"}}]

    taranan = degisen = renksiz = zaten = 0
    ornekler = []
    async for p in db.products.find(sorgu, {"_id": 0, "id": 1, "name": 1,
                                            "color": 1, "variants": 1}):
        if args.limit and taranan >= args.limit:
            break
        taranan += 1
        ad = str(p.get("name") or "").strip()
        renk = _basliklastir(_urun_rengi(p))
        if not ad or not renk:
            renksiz += 1
            continue
        if _kucult(ad).endswith(_kucult(renk)):
            zaten += 1
            continue
        yeni = f"{ad} {renk}"
        degisen += 1
        if len(ornekler) < args.liste:
            ornekler.append(yeni)
        if args.uygula:
            await db.products.update_one({"id": p["id"]}, {"$set": {"name": yeni}})

    for a in ornekler:
        print("  " + a[:80])
    if degisen > len(ornekler):
        print(f"  … ve {degisen - len(ornekler)} ürün daha")
    print()
    print(f"taranan ürün         : {taranan}")
    print(f"{'ad güncellendi' if args.uygula else 'ad güncellenecek'}     : {degisen}")
    print(f"zaten renkle bitiyor : {zaten}")
    print(f"rengi belirsiz       : {renksiz}  (renk alanı boş ya da birden çok renk)")
    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    else:
        print("\nSlug'lara dokunulmadı — mevcut ürün linkleri çalışmaya devam eder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
