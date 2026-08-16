"""
catalog_readiness.py — Katalog hazırlık durumu (kategori kategori ilerleme).

NEDEN: Takım kategorisini pazaryerine hazır hâle getirmek için sırayla beş iş
yapıldı — ölçü tablosu üretimi, görsellerin R2'ye taşınması, ürün
özelliklerinin doldurulması, zorunlu temel alanlar (sezon/kalıp) ve Trendyol
kategori eşleştirmesi. Aynı işler tüm kategorilere uygulanacak ve hangi
kategoride ne kadar yol alındığı hiçbir yerde görünmüyordu; her seferinde tek
tek betik çalıştırıp elle saymak gerekiyordu.

Bu uç nokta kategori başına ölçüp yüzde veriyor: neyin eksik olduğu ve hangi
kategorinin sırada olduğu tek bakışta görünüyor.

ÖLÇÜLEN BEŞ ADIM (ürün başına):
  1. ölçü tablosu   → size_tables kaydı VAR mı (beden×ölçü verisi)
  2. tablo görseli  → ürünün görselleri arasında bizim Türkçe tablomuz
  3. kendi görselimiz → görseller tedarikçi sunucusunda değil, R2/CDN'de
  4. öznitelik      → ürün en az 6 pazaryeri özelliği taşıyor
  5. temel alanlar  → sezon + beden önerisi dolu

Kategori düzeyinde ayrıca Trendyol eşleştirmesi ve kaç ürünün pazaryerine
gönderildiği gösterilir.
"""
from fastapi import APIRouter, Depends

from .deps import db, require_admin

router = APIRouter(prefix="/catalog-readiness", tags=["Catalog Readiness"])

_OLCU_GORSEL = "-olcu"
_KENDI_SUNUCU = ("cdn.meluni.com.tr", "/api/upload/files/", "/api/files/")


def _url(x) -> str:
    return (x.get("url") if isinstance(x, dict) else x) or ""


def _oz_sayisi(attrs) -> int:
    if isinstance(attrs, list):
        return sum(1 for a in attrs if isinstance(a, dict) and a.get("value"))
    if isinstance(attrs, dict):
        return sum(1 for v in attrs.values() if v)
    return 0


@router.get("")
async def katalog_hazirlik(current_user: dict = Depends(require_admin)):
    """Kategori kategori hazırlık yüzdeleri + genel özet."""
    # Ölçü tablosu olan ürünler (tek sorgu; ürün başına sorgu atmıyoruz).
    tablolu = set()
    async for st in db.size_tables.find({}, {"_id": 0, "product_id": 1, "sizes": 1}):
        if st.get("sizes"):
            tablolu.add(st.get("product_id"))

    # Trendyol kategori eşleştirmeleri
    esli_kategoriler = set()
    async for cm in db.category_mappings.find(
            {"marketplace": {"$regex": "^trendyol$", "$options": "i"},
             "marketplace_category_id": {"$nin": [None, ""]}},
            {"_id": 0, "category_id": 1}):
        esli_kategoriler.add(str(cm.get("category_id")))

    kategoriler: dict = {}
    genel = {"urun": 0, "olcu_verisi": 0, "tablo_gorseli": 0, "kendi_gorseli": 0,
             "oznitelik": 0, "temel_alan": 0, "trendyola_gitti": 0}

    async for p in db.products.find(
            {"is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "category_id": 1, "category_name": 1,
             "images": 1, "attributes": 1, "season": 1, "size_advice": 1,
             "trendyol_push_status": 1}):
        kid = str(p.get("category_id") or "")
        kad = p.get("category_name") or "(kategorisiz)"
        k = kategoriler.setdefault(kid or kad, {
            "kategori_id": kid, "kategori": kad, "urun": 0, "olcu_verisi": 0,
            "tablo_gorseli": 0, "kendi_gorseli": 0, "oznitelik": 0,
            "temel_alan": 0, "trendyola_gitti": 0,
            "trendyol_eslesti": kid in esli_kategoriler})

        gorseller = [_url(x) for x in (p.get("images") or []) if _url(x)]
        olculdu = {
            "olcu_verisi": p["id"] in tablolu,
            "tablo_gorseli": any(_OLCU_GORSEL in u for u in gorseller),
            "kendi_gorseli": bool(gorseller) and any(
                any(s in u for s in _KENDI_SUNUCU) for u in gorseller),
            "oznitelik": _oz_sayisi(p.get("attributes")) >= 6,
            "temel_alan": bool(str(p.get("season") or "").strip())
                          and bool(str(p.get("size_advice") or "").strip()),
            "trendyola_gitti": p.get("trendyol_push_status") == "sent",
        }
        k["urun"] += 1
        genel["urun"] += 1
        for ad, deger in olculdu.items():
            if deger:
                k[ad] += 1
                genel[ad] += 1

    def _yuzde(k):
        n = max(1, k["urun"])
        adimlar = ("olcu_verisi", "tablo_gorseli", "kendi_gorseli",
                   "oznitelik", "temel_alan")
        k["yuzde"] = round(sum(k[a] for a in adimlar) / (len(adimlar) * n) * 100)
        for a in adimlar + ("trendyola_gitti",):
            k[f"{a}_yuzde"] = round(k[a] / n * 100)
        return k

    liste = [_yuzde(k) for k in kategoriler.values()]
    liste.sort(key=lambda x: (x["yuzde"], -x["urun"]))
    genel_yuzde = round(sum(
        genel[a] for a in ("olcu_verisi", "tablo_gorseli", "kendi_gorseli",
                           "oznitelik", "temel_alan")) /
        (5 * max(1, genel["urun"])) * 100)

    return {"genel": {**genel, "yuzde": genel_yuzde,
                      "eslesmis_kategori": len(esli_kategoriler),
                      "kategori": len(liste)},
            "kategoriler": liste}
