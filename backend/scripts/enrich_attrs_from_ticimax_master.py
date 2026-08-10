"""
Ticimax master "Teknik Detay Özellik + Değer" listesini çekip her ürünün
adı/açıklamasında bu değerleri arar ve eşleştiği özelliği `attributes`
dict'ine `ticimax_*` prefix'iyle ekler.

KULLANIM: `python /app/backend/scripts/enrich_attrs_from_ticimax_master.py`

Bu yaklaşım, Ticimax SOAP'ta `SelectUrun` çağrımıza yetki olmadığı için
(WS Key Yetki Kodu yetersiz) ürünlere doğrudan eşlenmiş özellik kimliklerini
çekemediğimizdeki en güvenilir alternatiftir.
"""
import asyncio
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402
from ticimax_client import _urun_client, TICIMAX_API_KEY  # noqa: E402
from zeep import helpers as _zh  # noqa: E402


def _slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = (t.replace("ı", "i").replace("İ", "i")
           .replace("ş", "s").replace("ç", "c")
           .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return t.strip("_") or "ozellik"


def _norm(s: str) -> str:
    """Türkçe karakterleri ASCII'leştir, küçük harf, gereksiz boşlukları sil."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = (s.lower()
           .replace("ı", "i").replace("ş", "s").replace("ç", "c")
           .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Bir özellik için: ürün metnine bakarken hangi değer seçilebilir?
# Kısa string'lerin (örn "Sahte" 5 char) yanlış pozitif olmaması için
# her değer için minimum uzunluk kontrolü.
def _build_value_pattern(deger_tanim: str) -> re.Pattern | None:
    """Değer tanımını metinde aramak için bir kelime-sınırlı regex."""
    norm = _norm(deger_tanim).strip()
    if not norm or len(norm) < 3:
        return None
    # Yüzde değerleri (% 100 Polyester gibi) için boşlukları esnet
    if "%" in norm:
        # \s* ile boşlukları toleranslı yap
        escaped = re.escape(norm).replace(r"\ ", r"\s*")
    else:
        escaped = re.escape(norm)
    # Kelime sınırı kullan ama Türkçe karakter sonrası `\b` çalışmıyor;
    # bunun yerine non-alphanumeric edge kontrolü
    pattern = rf"(?<![a-z0-9])(?:{escaped})(?![a-z0-9])"
    return re.compile(pattern, re.IGNORECASE)


def fetch_master():
    """Ticimax'tan özellik + değer master listelerini çek."""
    c = _urun_client()
    print("📥 SelectTeknikDetayOzellik çekiliyor...")
    ozel = c.service.SelectTeknikDetayOzellik(
        UyeKodu=TICIMAX_API_KEY,
        teknikDetayOzellikId=0, dil="tr",
        kategoriId=0, grupId=0,
    )
    ozellik_map = {}  # OzellikID → Tanim
    for o in (ozel or []):
        d = _zh.serialize_object(o, dict)
        ozellik_map[int(d["ID"])] = str(d["Tanim"])
    print(f"   ✅ {len(ozellik_map)} özellik")

    time.sleep(13)
    print("📥 SelectTeknikDetayDeger çekiliyor...")
    deg = c.service.SelectTeknikDetayDeger(
        UyeKodu=TICIMAX_API_KEY,
        teknikDetayDegerId=0, dil="tr",
        kategoriId=0, ozellikId=0,
    )
    # OzellikID → [{ID, Tanim, pattern}]
    deger_by_ozellik: dict[int, list[dict]] = {}
    total_deger = 0
    for d in (deg or []):
        s = _zh.serialize_object(d, dict)
        ozid = int(s["OzellikID"])
        tanim = str(s["Tanim"]).strip()
        if not tanim:
            continue
        pat = _build_value_pattern(tanim)
        if not pat:
            continue
        deger_by_ozellik.setdefault(ozid, []).append({
            "id": int(s["ID"]),
            "tanim": tanim,
            "pattern": pat,
        })
        total_deger += 1
    print(f"   ✅ {total_deger} değer ({len(deger_by_ozellik)} özelliğe dağıtılmış)")

    return ozellik_map, deger_by_ozellik


async def enrich_products(ozellik_map: dict, deger_by_ozellik: dict):
    """Her ürün için name+description'da master değerleri ara, attributes'a ekle.

    ÖNEMLİ: attributes dict'i Trendyol UI'sının beklediği format'ta yazılır:
        `attributes[Ticimax Özellik Tanımı] = "Master Değer"`  (örn. attributes["Boy"] = "Midi")
    Bu sayede admin'de "Trendyol için Özellikler" formundaki Boy/Cep/Astar/Bel/Web Color
    dropdown'ları OTOMATİK doldurulmuş olarak gelir.
    """
    # Master listeyi DB'ye cache'le (frontend dropdown vb. için)
    await db.ticimax_attribute_master.delete_many({})
    docs = []
    for ozid, tanim in ozellik_map.items():
        docs.append({
            "ozellik_id": ozid,
            "ozellik_tanim": tanim,
            "degerler": [
                {"id": d["id"], "tanim": d["tanim"]}
                for d in deger_by_ozellik.get(ozid, [])
            ],
        })
    if docs:
        await db.ticimax_attribute_master.insert_many(docs)

    prods = await db.products.find(
        {"source": {"$in": ["xml_feed", "ticimax", "csv_xml_merge"]}},
        {"_id": 0, "id": 1, "name": 1, "description": 1, "attributes": 1,
         "hepsiburada_attributes": 1, "temu_attributes": 1},
    ).to_list(None)

    total = len(prods)
    enriched = 0
    added_keys: dict[str, int] = {}

    for p in prods:
        name = p.get("name") or ""
        desc = re.sub(r"<[^>]+>", " ", p.get("description") or "")
        text = _norm(name + " " + desc)

        existing = p.get("attributes") or {}
        # attributes Array ise dict'e çevir (eski Trendyol formatı)
        if isinstance(existing, list):
            new_attrs = {}
            for item in existing:
                if isinstance(item, dict) and item.get("name"):
                    new_attrs[item["name"]] = str(item.get("value", ""))
            existing = new_attrs

        hb = p.get("hepsiburada_attributes") or {}
        temu = p.get("temu_attributes") or {}

        added = False
        for ozid, tanim in ozellik_map.items():
            # Her özellik için tüm değerleri sıralı dene; ilk eşleşeni al
            matched = None
            for d in deger_by_ozellik.get(ozid, []):
                if d["pattern"].search(text):
                    matched = d
                    break
            if not matched:
                continue
            # Trendyol kütüphanesi ile aynı `Tanim` kullanılır (Boy, Cep, Web Color, ...).
            # Mevcut değeri override ETME — kullanıcının manuel girdiği değer korunur.
            if existing.get(tanim):
                continue
            value = matched["tanim"]
            existing[tanim] = value
            # HB & Temu otomatik sync (boşsa doldur)
            if not hb.get(tanim):
                hb[tanim] = value
            if not temu.get(tanim):
                temu[tanim] = value
            added_keys[tanim] = added_keys.get(tanim, 0) + 1
            added = True

        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {
                "attributes": existing,
                "hepsiburada_attributes": hb,
                "temu_attributes": temu,
            }},
        )
        if added:
            enriched += 1

    print(f"\n✅ {enriched}/{total} ürüne otomatik teknik detay eklendi.")
    print("Ekleme dağılımı (master özelliklere göre):")
    for k, v in sorted(added_keys.items(), key=lambda x: -x[1]):
        print(f"   {v:4d}  {k}")


async def main(use_cache: bool = False):
    if use_cache:
        # Cache'ten oku (DB'de varsa Ticimax'a sormaz)
        cached = await db.ticimax_attribute_master.find({}, {"_id": 0}).to_list(None)
        if cached:
            ozellik_map = {c["ozellik_id"]: c["ozellik_tanim"] for c in cached}
            deger_by_ozellik = {}
            for c in cached:
                ozid = c["ozellik_id"]
                deger_by_ozellik[ozid] = []
                for d in c.get("degerler", []):
                    pat = _build_value_pattern(d["tanim"])
                    if pat:
                        deger_by_ozellik[ozid].append({
                            "id": d["id"], "tanim": d["tanim"], "pattern": pat,
                        })
            print(f"📦 Cache'ten okundu: {len(ozellik_map)} özellik")
            await enrich_products(ozellik_map, deger_by_ozellik)
            return
    ozellik_map, deger_by_ozellik = fetch_master()
    await enrich_products(ozellik_map, deger_by_ozellik)


if __name__ == "__main__":
    import sys
    use_cache = "--cache" in sys.argv
    asyncio.run(main(use_cache=use_cache))
