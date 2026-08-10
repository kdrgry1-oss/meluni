"""Tüm ürünlere Trendyol/HB/Temu attribute formundaki BOŞ alanları akıllı
default'larla doldurur.

Trendyol attribute kütüphanesindeki TAM değerlerle eşleşir:
  - Cinsiyet      → "Kadın / Kız"
  - Yaş Grubu     → "Yetişkin"
  - Menşei        → "TR"
  - Astar Durumu  → "Astarsız"  (kategori "Ceket/Trençkot/Yelek" ise "Astarlı")
  - Koleksiyon   → "Casual / Günlük"
  - Sezon        → "Tüm Sezonlar"
  - Kapama Şekli → "Kapamasız"
  - Cep          → "Cepsiz"     (Pantolon/Şort/Bermuda kategorisinde "Yan Cep")
  - Yaka Tipi   → Kategori-bazlı (Elbise/Bluz/Tunik→"Yuvarlak Yaka"; Şort/Etek/Pantolon→"Yakasız")
  - Kalıp       → "Regular"     (yoksa)
  - Bel         → "Normal Bel"  (Pantolon/Etek/Şort'ta yoksa)
  - Kalınlık    → "Orta"        (yoksa)
  - Kol Boyu    → Kategori-bazlı (önceden zaten yapıldı)

Mevcut manuel değerler KORUNUR — sadece boş alanlara yazıyor.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402


# Trendyol kütüphanesi formatıyla tam eşleşen değerler
GLOBAL_DEFAULTS = {
    "Cinsiyet": "Kadın / Kız",
    "Yaş Grubu": "Yetişkin",
    "Menşei": "TR",
    "Koleksiyon": "Casual / Günlük",
    "Sezon": "Tüm Sezonlar",
    "Kapama Şekli": "Kapamasız",
    "Astar Durumu": "Astarsız",
    "Cep": "Cepsiz",
    "Kalıp": "Regular",
    "Kalınlık": "Orta",
}

# Kategori → ek default'lar
CATEGORY_OVERRIDES = {
    "ceket":   {"Astar Durumu": "Astarlı", "Yaka Tipi": "Ceket Yaka", "Kapama Şekli": "Düğmeli"},
    "trençkot": {"Astar Durumu": "Astarlı", "Yaka Tipi": "Klasik Yaka", "Kapama Şekli": "Kuşaklı"},
    "yelek":   {"Astar Durumu": "Astarlı", "Yaka Tipi": "V Yaka"},
    "elbise":  {"Yaka Tipi": "Yuvarlak Yaka"},
    "tunik":   {"Yaka Tipi": "Yuvarlak Yaka"},
    "bluz":    {"Yaka Tipi": "Yuvarlak Yaka"},
    "gömlek":  {"Yaka Tipi": "Klasik Yaka", "Kapama Şekli": "Düğmeli"},
    "kazak":   {"Yaka Tipi": "Yuvarlak Yaka"},
    "atlet":   {"Yaka Tipi": "Yuvarlak Yaka", "Kol Boyu": "Kolsuz"},
    "büstiyer": {"Yaka Tipi": "Straplez", "Kol Boyu": "Askılı"},
    "bandana": {"Yaka Tipi": "Straplez", "Kol Boyu": "Kolsuz"},
    "şort":    {"Yaka Tipi": "Yakasız", "Kol Boyu": "Yok", "Cep": "Yan Cep", "Bel": "Normal Bel"},
    "bermuda": {"Yaka Tipi": "Yakasız", "Kol Boyu": "Yok", "Cep": "Yan Cep", "Bel": "Normal Bel"},
    "pantolon": {"Yaka Tipi": "Yakasız", "Kol Boyu": "Yok", "Cep": "Yan Cep", "Bel": "Normal Bel"},
    "etek":    {"Yaka Tipi": "Yakasız", "Kol Boyu": "Yok", "Bel": "Normal Bel"},
    "tulum":   {"Yaka Tipi": "Yuvarlak Yaka", "Bel": "Normal Bel"},
    "takım":   {"Yaka Tipi": "Yuvarlak Yaka"},
    "kimono":  {"Yaka Tipi": "Devrik Yaka", "Astar Durumu": "Astarsız"},
}


BOY_CATEGORIES = ("elbise", "tunik", "etek", "tulum")
BOY_KEYWORDS = [("maxi", "Maxi"), ("midi", "Midi"), ("mini", "Mini"),
                ("uzun", "Uzun"), ("kısa", "Kısa")]


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _set_if_empty(target: dict, key: str, value: str, counts: dict) -> bool:
    """Yalnızca boş ise yaz; mevcut değeri override etme. Önceden 'Kadın'
    yazılmış (yanlış format) varsa onu da düzelt."""
    cur = target.get(key)
    if not cur:
        target[key] = value
        counts[key] = counts.get(key, 0) + 1
        return True
    # Cinsiyet "Kadın" → "Kadın / Kız" düzeltmesi
    if key == "Cinsiyet" and cur == "Kadın":
        target[key] = "Kadın / Kız"
        counts[key + "_fixed"] = counts.get(key + "_fixed", 0) + 1
        return True
    return False


async def main():
    prods = await db.products.find(
        {"source": {"$in": ["xml_feed", "ticimax", "csv_xml_merge"]}},
        {"_id": 0, "id": 1, "name": 1, "category_name": 1,
         "attributes": 1, "hepsiburada_attributes": 1, "temu_attributes": 1},
    ).to_list(None)

    total = len(prods)
    changed = 0
    counts: dict = {}

    for p in prods:
        cat = _norm(p.get("category_name") or "")
        name = _norm(p.get("name") or "")

        attrs = p.get("attributes") or {}
        if isinstance(attrs, list):
            new_dict = {}
            for it in attrs:
                if isinstance(it, dict) and it.get("name"):
                    new_dict[it["name"]] = str(it.get("value", ""))
            attrs = new_dict
        hb = p.get("hepsiburada_attributes") or {}
        temu = p.get("temu_attributes") or {}

        # Hangi default'ları uygulayacağız?
        applied_defaults = dict(GLOBAL_DEFAULTS)
        # Kategori override
        for ckey, vals in CATEGORY_OVERRIDES.items():
            if ckey in cat or ckey in name:
                applied_defaults.update(vals)

        # "Boy" — sadece elbise/etek/tulum/tunik için ad'dan çıkar
        if any(c in cat for c in BOY_CATEGORIES) or any(c in name for c in BOY_CATEGORIES):
            for kw, val in BOY_KEYWORDS:
                if kw in name and "Boy" not in applied_defaults:
                    applied_defaults["Boy"] = val
                    break

        applied_changes = False
        for key, val in applied_defaults.items():
            for target in (attrs, hb, temu):
                if _set_if_empty(target, key, val, counts):
                    applied_changes = True

        if applied_changes:
            await db.products.update_one(
                {"id": p["id"]},
                {"$set": {
                    "attributes": attrs,
                    "hepsiburada_attributes": hb,
                    "temu_attributes": temu,
                }},
            )
            changed += 1

    print(f"✅ {changed}/{total} ürüne default uygulandı.")
    print("Eklenen / düzeltilen alan dağılımı:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"   {v:5d}  {k}")


if __name__ == "__main__":
    asyncio.run(main())
