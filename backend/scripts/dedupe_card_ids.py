"""dedupe_card_ids.py — Aynı `urun_karti_id`'yi paylaşan RENK KARDEŞİ ürünlere
BENZERSİZ Ürün Kart ID atar (Kadir: "renkleri aynı ürün kart id si almış, ikisi de
farklı olsun, sıraya göre 1'er artır").

KORUMA: Renk kardeşliği `csv_card_id` ile tutulur → her grupta `csv_card_id` ORİJİNAL
(paylaşımlı) değerde KALIR. Böylece storefront "Diğer Renkler" swatch'ı bozulmaz; sadece
listede gösterilen `urun_karti_id` her renk için ayrı/benzersiz olur.

- Her grupta EN ESKİ ürün (created_at asc) taban kart id'sini KORUR.
- Sonraki kardeşler sistemdeki max + 1'den başlayıp boş numaraları atlayarak artar.
- `slug` DEĞİŞTİRİLMEZ (eski linkler/SEO kırılmasın).

Kullanım:
    DRY-RUN: python -m scripts.dedupe_card_ids
    UYGULA : python -m scripts.dedupe_card_ids --apply
"""
import asyncio
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def _is_int(s) -> bool:
    return str(s or "").strip().isdigit()


async def main(apply: bool):
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    # 1) Tüm ürünleri topla (silinmemiş)
    prods = []
    async for p in db.products.find(
        {"is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "color": 1, "urun_karti_id": 1,
         "csv_card_id": 1, "ticimax_fields": 1, "created_at": 1},
    ):
        prods.append(p)

    # 2) urun_karti_id -> ürün listesi (sayısal kart id'liler)
    by_card = defaultdict(list)
    used = set()
    mx = 0
    for p in prods:
        cid = str(p.get("urun_karti_id") or "").strip()
        if _is_int(cid):
            used.add(cid)
            mx = max(mx, int(cid))
            by_card[cid].append(p)

    # 3) Sonraki boş sayısal kart id üreteci
    counter = {"n": mx}

    def next_free():
        counter["n"] += 1
        while str(counter["n"]) in used:
            counter["n"] += 1
        used.add(str(counter["n"]))
        return str(counter["n"])

    # 4) Çiftleri (farklı id, aynı kart id) bul ve planla
    planned = []  # (product_id, name, old_card, new_card, group_key)
    dup_groups = 0
    for cid, members in by_card.items():
        # farklı ürün id'leri varsa = renk kardeşi grubu
        distinct = {m["id"]: m for m in members}.values()
        distinct = list(distinct)
        if len(distinct) <= 1:
            continue
        dup_groups += 1
        # Grup anahtarı: mevcut paylaşımlı csv_card_id (varsa) yoksa paylaşılan kart id
        group_key = cid
        for m in distinct:
            gk = str(m.get("csv_card_id") or "").strip()
            if gk:
                group_key = gk
                break
        # En eski önce → taban kart id'yi korusun
        distinct.sort(key=lambda m: str(m.get("created_at") or ""))
        for idx, m in enumerate(distinct):
            if idx == 0:
                # taban: kart id aynı kalır, sadece csv_card_id grup anahtarına sabitlenir
                planned.append((m["id"], m.get("name"), cid, cid, group_key, False))
            else:
                new_card = next_free()
                planned.append((m["id"], m.get("name"), cid, new_card, group_key, True))

    # 5) Raporla
    changes = [x for x in planned if x[5]]  # sadece kart id değişenler
    print(f"Toplam ürün: {len(prods)}  ·  Çift kart-id grubu: {dup_groups}  ·  "
          f"Yeniden numaralanacak ürün: {len(changes)}")
    for pid, name, old_c, new_c, gk, changed in planned:
        tag = "→ YENİ" if changed else "  (taban, korunur)"
        print(f"  [{old_c}] {str(name)[:48]:48s}  {tag} {new_c if changed else ''}  (grup csv_card_id={gk})")

    if not apply:
        print("\nDRY-RUN — hiçbir şey yazılmadı. Uygulamak için: python -m scripts.dedupe_card_ids --apply")
        return

    # 6) Uygula
    n_card, n_group = 0, 0
    for pid, name, old_c, new_c, gk, changed in planned:
        setdoc = {"csv_card_id": gk}
        if changed:
            setdoc["urun_karti_id"] = new_c
        # ticimax_fields.URUNKARTIID senkron
        doc = await db.products.find_one({"id": pid}, {"_id": 0, "ticimax_fields": 1})
        tf = (doc or {}).get("ticimax_fields") or {}
        if changed:
            tf = {**tf, "URUNKARTIID": new_c}
            setdoc["ticimax_fields"] = tf
        await db.products.update_one({"id": pid}, {"$set": setdoc})
        if changed:
            n_card += 1
        n_group += 1
    print(f"\nUYGULANDI — {n_card} ürün yeni kart id aldı, {n_group} üründe csv_card_id sabitlendi.")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
