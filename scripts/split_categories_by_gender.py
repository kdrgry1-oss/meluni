"""
split_categories_by_gender.py — Kategorileri cinsiyete göre ayırır.

SORUN: Aktarım aynı ada sahip kategoriyi ÜST KATEGORİYİ GÖZETMEDEN eşleştiriyordu.
Besleme kadın pantolonunu "Pantolon", erkek pantolonunu "Erkek-Pantolon" diye
veriyor; ikisi de "Pantolon" alt adına indiğinden hangi cinsiyet önce
aktarıldıysa kategori orada açılmış, diğerinin ürünleri de oraya girmişti.
Sonuç: kadın pantolon listesinde erkek pantolonu. Aynı ad iki cinsiyette de
geçtiği için (T-Shirt, Gömlek, Ceket, Mont, Bluz, Atlet…) sorun geneldi.

Aktarım kodu düzeltildi. Bu betik ZATEN AKTARILMIŞ ürünleri doğru cinsiyet
kökünün altına taşır; gereken kategoriyi yoksa oluşturur.

Cinsiyet kaynağı (sırayla): ürünün `gender` alanı → `supplier_category_raw`
ön eki ("Erkek-…"). İkisi de yoksa ürüne DOKUNULMAZ — tahmin yürütmeyiz.

Dokunulanlar : category_id, category_ids, category_slug, category_root,
               category_path, category_name
Dokunulmayan : fiyat, stok, ad, görseller, tedarikçi kodları

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/split_categories_by_gender.py --dene
    ~/apps/meluni/shared/venv/bin/python ../scripts/split_categories_by_gender.py --uygula
"""
import argparse
import asyncio
import collections
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

_ERKEK = re.compile(r"^\s*erkek", re.IGNORECASE)
_KADIN = re.compile(r"^\s*kad[ıi]n", re.IGNORECASE)


def _kok_adi(gender: str, ham_kategori: str) -> str:
    """Ürünün ait olduğu cinsiyet kökü; belirlenemezse boş."""
    g = (gender or "").strip()
    if _ERKEK.match(g):
        return "Erkek"
    if _KADIN.match(g):
        return "Kadın"
    k = (ham_kategori or "").strip()
    if _ERKEK.match(k):
        return "Erkek"
    if _KADIN.match(k):
        return "Kadın"
    return ""


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
    from routes.deps import db                                   # noqa: E402
    from routes import supplier_xml as sx                        # noqa: E402

    # Önbellek aktarımdan kalmış olabilir; taze ağaçla çalışalım.
    sx._CAT_CACHE["map"] = None

    cats = {}
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1,
                                           "parent_id": 1, "slug": 1}):
        cats[c["id"]] = c

    kok_id = {}
    for c in cats.values():
        if not c.get("parent_id") and c.get("name") in ("Kadın", "Erkek"):
            kok_id[c["name"]] = c["id"]

    tasinan = collections.Counter()
    toplam = yerinde = belirsiz = 0

    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "category_id": 1, "gender": 1,
             "supplier_category_raw": 1}):
        toplam += 1
        mevcut = cats.get(p.get("category_id") or "")
        if not mevcut:
            belirsiz += 1
            continue
        hedef_kok = _kok_adi(p.get("gender"), p.get("supplier_category_raw"))
        if not hedef_kok:
            belirsiz += 1
            continue
        if (mevcut.get("parent_id") or "") == kok_id.get(hedef_kok, ""):
            yerinde += 1
            continue

        alt_ad = mevcut.get("name") or ""
        tasinan[f"{alt_ad} → {hedef_kok}"] += 1
        if not yaz:
            continue

        root = await sx._ensure_category(hedef_kok, None)
        yeni_id = await sx._ensure_category(alt_ad, root)
        if yeni_id not in cats:                       # yeni açıldıysa ağaca al
            doc = await db.categories.find_one({"id": yeni_id},
                                               {"_id": 0, "id": 1, "name": 1,
                                                "parent_id": 1, "slug": 1})
            if doc:
                cats[yeni_id] = doc
        await db.products.update_one({"id": p["id"]}, {"$set": {
            "category_id": yeni_id,
            "category_ids": [root, yeni_id],
            "category_slug": cats.get(yeni_id, {}).get("slug") or "",
            "category_name": alt_ad,
            "category_root": hedef_kok,
            "category_path": f"{hedef_kok} > {alt_ad}",
        }})

    for k, n in tasinan.most_common():
        print(f"  {n:>5}  {k}")
    print()
    print(f"toplam ürün          : {toplam}")
    print(f"{'taşındı' if yaz else 'taşınacak'}            : {sum(tasinan.values())}")
    print(f"zaten doğru kökte    : {yerinde}")
    print(f"cinsiyeti belirsiz   : {belirsiz}  (dokunulmadı)")
    if not yaz:
        print("\nHiçbir şey yazılmadı. Uygulamak için: --uygula")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
