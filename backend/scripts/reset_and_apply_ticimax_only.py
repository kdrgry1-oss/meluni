"""Önceki çalıştırmalarda uydurma kategori-bazlı default'lar ürünlere yazılmıştı
(örn. Bandana → Yaka Tipi=Straplez). Kullanıcı haklı uyarıda bulundu —
Ticimax'ta gerçekten YOKSA bu alan BOŞ olmalı.

Bu script:
  1. Tüm uydurma kategori-default'larını temizler (Cep, Yaka Tipi, Kapama Şekli,
     Astar Durumu, Kalıp, Bel, Kalınlık, Koleksiyon, Sezon — sadece bunlar reset
     edilir; manuel girilenleri tanıyamayız çünkü mark'lamadık).
  2. attr_parser ile description'dan TEKRAR yazılır.
  3. Ticimax master eşleme TEKRAR uygulanır (sadece description'da gerçek master
     değeri geçenler).
  4. Globaller (Cinsiyet=Kadın/Kız, Yaş Grubu=Yetişkin, Menşei=TR) tek doğru
     sabit olarak yazılır.

Bandana gibi Ticimax'ta Yaka Tipi olmayan ürünlerde alan BOŞ kalır.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402
from utils.attr_parser import parse_description_attributes  # noqa: E402


# Önceki "uydurma default"larda kullanılan key'ler — bunları temizleyeceğiz.
UYDURMA_KEYS = [
    "Cep", "Yaka Tipi", "Kapama Şekli", "Astar Durumu",
    "Kalıp", "Bel", "Kalınlık", "Koleksiyon", "Sezon",
    "Kol Boyu",
]

# Sadece bu 3'ü zorunlu global sabit (kullanıcı açıkça istedi).
GLOBAL_DEFAULTS = {
    "Cinsiyet": "Kadın / Kız",
    "Yaş Grubu": "Yetişkin",
    "Menşei": "TR",
}


def _build_value_pattern(text: str):
    """attr_parser._build_value_pattern ile aynı mantık."""
    import unicodedata
    norm = unicodedata.normalize("NFKD", text or "")
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = (norm.lower()
                .replace("ı", "i").replace("ş", "s").replace("ç", "c")
                .replace("ğ", "g").replace("ü", "u").replace("ö", "o")).strip()
    if not norm or len(norm) < 3:
        return None
    if "%" in norm:
        escaped = re.escape(norm).replace(r"\ ", r"\s*")
    else:
        escaped = re.escape(norm)
    return re.compile(rf"(?<![a-z0-9])(?:{escaped})(?![a-z0-9])", re.IGNORECASE)


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = (s.lower()
           .replace("ı", "i").replace("ş", "s").replace("ç", "c")
           .replace("ğ", "g").replace("ü", "u").replace("ö", "o"))
    return re.sub(r"\s+", " ", s).strip()


async def main():
    # 1) Master cache'den özellik+değer yükle
    cached = await db.ticimax_attribute_master.find({}, {"_id": 0}).to_list(None)
    ozellik_map = {c["ozellik_id"]: c["ozellik_tanim"] for c in cached}
    deger_by_ozellik: dict = {}
    for c in cached:
        ozid = c["ozellik_id"]
        deger_by_ozellik[ozid] = []
        for d in c.get("degerler", []):
            pat = _build_value_pattern(d["tanim"])
            if pat:
                deger_by_ozellik[ozid].append({
                    "id": d["id"], "tanim": d["tanim"], "pattern": pat,
                })
    print(f"📦 Master cache: {len(ozellik_map)} özellik")

    prods = await db.products.find(
        {"source": {"$in": ["xml_feed", "ticimax", "csv_xml_merge"]}},
        {"_id": 0, "id": 1, "name": 1, "description": 1,
         "attributes": 1, "hepsiburada_attributes": 1, "temu_attributes": 1},
    ).to_list(None)

    total = len(prods)
    print(f"📥 {total} ürün işlenecek...")

    for p in prods:
        name = p.get("name") or ""
        desc_html = p.get("description") or ""

        # Mevcut attributes (3 marketplace)
        attrs = p.get("attributes") or {}
        if isinstance(attrs, list):
            new_dict = {}
            for it in attrs:
                if isinstance(it, dict) and it.get("name"):
                    new_dict[it["name"]] = str(it.get("value", ""))
            attrs = new_dict
        hb = p.get("hepsiburada_attributes") or {}
        temu = p.get("temu_attributes") or {}

        # ADIM A: Önceki uydurma default'ları SİL (sadece bu key'ler)
        for k in UYDURMA_KEYS:
            attrs.pop(k, None)
            hb.pop(k, None)
            temu.pop(k, None)

        # ADIM B: Description'dan dinamik parser çıkarımı
        parsed, _ = parse_description_attributes(desc_html)
        # parsed: {slug: {label, value}}  — sadece title-case label'ları
        # `attributes` dict'ine yazıyoruz; UYDURMA_KEYS arasında label varsa skip
        for slug, info in parsed.items():
            if isinstance(info, dict):
                lbl = info.get("label", "")
                val = info.get("value", "")
                if lbl and val and lbl not in attrs:
                    attrs[lbl] = val

        # ADIM C: Ticimax master eşleme — description+name'de geçen değer
        text = _norm(name + " " + re.sub(r"<[^>]+>", " ", desc_html))
        for ozid, tanim in ozellik_map.items():
            if tanim in attrs:  # zaten yazıldı (manuel veya başka yerden)
                continue
            for d in deger_by_ozellik.get(ozid, []):
                if d["pattern"].search(text):
                    attrs[tanim] = d["tanim"]
                    # HB ve Temu için de aynısını yaz (boşsa)
                    if not hb.get(tanim):
                        hb[tanim] = d["tanim"]
                    if not temu.get(tanim):
                        temu[tanim] = d["tanim"]
                    break

        # ADIM D: Global sabitler (Cinsiyet/Yaş Grubu/Menşei) — sadece bunlar
        for k, v in GLOBAL_DEFAULTS.items():
            if not attrs.get(k):
                attrs[k] = v
            if not hb.get(k):
                hb[k] = v
            if not temu.get(k):
                temu[k] = v
            # "Kadın" → "Kadın / Kız" düzelt
            if k == "Cinsiyet" and attrs.get(k) == "Kadın":
                attrs[k] = "Kadın / Kız"
            if k == "Cinsiyet" and hb.get(k) == "Kadın":
                hb[k] = "Kadın / Kız"
            if k == "Cinsiyet" and temu.get(k) == "Kadın":
                temu[k] = "Kadın / Kız"

        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {
                "attributes": attrs,
                "hepsiburada_attributes": hb,
                "temu_attributes": temu,
            }},
        )

    print(f"✅ {total} ürün temizlendi ve sadece Ticimax-uyumlu değerlerle yeniden yazıldı.")


if __name__ == "__main__":
    asyncio.run(main())
