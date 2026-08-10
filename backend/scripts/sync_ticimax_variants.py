"""One-off: Pull ALL Ticimax variants and enrich DB products with sizes/colors.

Bypasses the API endpoint (auth); runs in the backend container.
Output: print progress to stdout. Run with: `python /app/backend/scripts/sync_ticimax_variants.py`
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.deps import db  # noqa: E402
from ticimax_client import _urun_client  # noqa: E402
from zeep import helpers as _zh  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("variant_sync")

OLD_KEY = "HANXFWINXLDBY0WH47WMB6QKTE20T5"


async def main():
    c = _urun_client()
    vf = c.get_type("ns2:VaryasyonFiltre")
    sf = c.get_type("ns2:UrunSayfalama")
    ayar = c.get_type("ns2:SelectVaryasyonAyar")

    cards: dict[int, list[dict]] = {}
    barcode_to_card: dict[str, int] = {}
    stockcode_to_card: dict[str, int] = {}
    name_to_card: dict[int, str] = {}  # ek match kanalı yok ama saklayalım
    total_variants = 0
    page_size = 500
    max_pages = 50

    consecutive_empty = 0
    for page in range(max_pages):
        s = sf(BaslangicIndex=page * page_size, KayitSayisi=page_size,
               KayitSayisinaGoreGetir=False)
        attempts = 0
        r = None
        while attempts < 3:
            try:
                # Aktif=1 ile Ticimax tüm sayfayı doldurur (boş filtrede 139'da takılır)
                r = c.service.SelectVaryasyon(
                    UyeKodu=OLD_KEY, f=vf(Aktif=1), s=s, varyasyonAyar=ayar())
                break
            except Exception as e:
                err_msg = str(e)
                attempts += 1
                wait = 20 if "Next Query" in err_msg else 8
                log.warning(f"page {page} attempt {attempts}: {err_msg[:120]} → wait {wait}s")
                time.sleep(wait)
        if r is None:
            log.error(f"page {page} skipped after retries")
            break
        if len(r) == 0:
            consecutive_empty += 1
            log.info(f"page {page}: empty ({consecutive_empty}/2)")
            if consecutive_empty >= 2:
                break
            time.sleep(15)
            continue
        consecutive_empty = 0

        for v in r:
            d = _zh.serialize_object(v, dict)
            kart_id = d.get("UrunKartiID")
            barkod = (d.get("Barkod") or "").strip()
            stokkodu = (d.get("StokKodu") or "").strip()
            if not kart_id:
                continue
            ozel = d.get("Ozellikler") or {}
            ozel_list = ozel.get("VaryasyonOzellik") if isinstance(ozel, dict) else None
            ozel_list = ozel_list or []
            renk = None
            beden = None
            for op in ozel_list:
                if not isinstance(op, dict):
                    continue
                tan = (op.get("Tanim") or "").lower()
                deg = op.get("Deger")
                if "renk" in tan:
                    renk = deg
                elif "beden" in tan:
                    beden = deg
            vrec = {
                "id": int(d.get("ID")) if d.get("ID") else None,
                "barcode": barkod,
                "stock_code": stokkodu,
                "stock": int(d.get("StokAdedi") or 0),
                "active": bool(d.get("Aktif")),
                "color": renk,
                "size": beden,
                "price": float(d.get("AlisFiyati") or 0),
                "sale_price": float(d.get("IndirimliFiyati") or 0) or None,
            }
            cards.setdefault(int(kart_id), []).append(vrec)
            if barkod:
                barcode_to_card[barkod] = int(kart_id)
            if stokkodu:
                stockcode_to_card[stokkodu] = int(kart_id)
            total_variants += 1

        log.info(f"page {page}: +{len(r)} variants (total={total_variants}, cards={len(cards)})")
        # Eğer dönen sayı 0 veya beklenenden büyük ölçüde küçükse bile devam et —
        # Ticimax bazen sayfa başına eksik veri verebiliyor.
        time.sleep(15)

    log.info(f"FETCH DONE: {total_variants} variants in {len(cards)} cards")

    # Match to DB
    # Renk eşlemesi için yardımcı: ürün adında geçen renk
    def detect_color(name: str) -> str | None:
        if not name:
            return None
        n = name.upper()
        for r in ["BEJ", "SIYAH", "SİYAH", "BEYAZ", "KAHVERENGİ", "KAHVERENGI",
                  "GRİ", "GRI", "HAKİ", "HAKI", "LACİVERT", "LACIVERT",
                  "BORDO", "EKRU", "VİZON", "VIZON", "PUDRA", "MAVİ", "MAVI",
                  "YEŞİL", "YESIL", "KIRMIZI", "SARI", "TURUNCU", "MOR",
                  "PEMBE", "ALTIN", "GÜMÜŞ", "GUMUS"]:
            if r in n:
                return r.replace("İ", "I").replace("Ş", "S").replace("Ğ", "G")
        return None

    # Card → color mapping
    card_to_color: dict[int, str] = {}
    for kid, vs in cards.items():
        colors = {v.get("color") for v in vs if v.get("color")}
        if len(colors) == 1:
            card_to_color[kid] = next(iter(colors)).upper().replace("İ", "I").replace("Ş", "S").replace("Ğ", "G")

    prods = await db.products.find({"source": "xml_feed"},
                                    {"_id": 0, "id": 1, "barcode": 1, "sku": 1, "name": 1,
                                     "stock_code": 1, "xml_label_0": 1, "xml_label_1": 1}
                                    ).to_list(None)
    matched = 0
    unmatched_examples = []
    for p in prods:
        bc = (p.get("barcode") or "").strip()
        sc = (p.get("sku") or "").strip()
        stck = (p.get("stock_code") or "").strip()
        kart_id = (barcode_to_card.get(bc) or stockcode_to_card.get(sc)
                   or stockcode_to_card.get(bc) or barcode_to_card.get(sc)
                   or stockcode_to_card.get(stck) or barcode_to_card.get(stck))
        # Son çare: xml_label_0 prefix → stockcode prefix match
        if not kart_id and p.get("xml_label_0"):
            lbl = str(p["xml_label_0"]).strip()
            # SOAP varyant stok kodları çoğunlukla "<lbl>-RENK-BEDEN" formatında olabilir
            for sckey, kid in stockcode_to_card.items():
                if sckey and (sckey.startswith(lbl) or lbl in sckey):
                    kart_id = kid
                    break
        if not kart_id:
            if len(unmatched_examples) < 5:
                unmatched_examples.append({"name": p.get("name"),
                                            "barcode": bc, "sku": sc,
                                            "stock_code": stck,
                                            "xml_label_0": p.get("xml_label_0")})
            continue
        variants = cards.get(kart_id, [])
        sizes = sorted({v["size"] for v in variants if v.get("size")},
                       key=lambda x: (len(x), x))
        colors = sorted({v["color"] for v in variants if v.get("color")})
        await db.products.update_one(
            {"id": p["id"]},
            {"$set": {
                "ticimax_card_id": int(kart_id),
                "variants": variants,
                "sizes": sizes,
                "colors": colors,
            }}
        )
        matched += 1

    log.info(f"MATCH DONE: {matched}/{len(prods)} products enriched")
    if unmatched_examples:
        log.info(f"Unmatched samples: {unmatched_examples}")


if __name__ == "__main__":
    asyncio.run(main())
