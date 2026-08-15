"""
auto_setup_mapping.py — Pazaryeri özellik eşleştirmesini uçtan uca kurar.

NEDEN: Kategori eşleştirme ekranında Trendyol özelliklerinin karşısı boş
kalıyor ("Sistem özelliği seç…", "Varsayılan Seçilmedi"). Eşleştirme üç ayrı
adım ister ve elle yapılınca kategori × özellik sayısı kadar tıklama demek:

  1. Trendyol'un o kategorideki özellik ŞEMASINI canlı çek (değer listeleriyle)
  2. Özellik ADLARINI eşle: Trendyol "Kumaş Tipi" ↔ sistem "Kumaş Tipi"
  3. DEĞERLERİ eşle: bizim "Siyah" → Trendyol'un renk value_id'si
  4. Şirket alanlarını (Üretici/İthalatçı Adı, Adres, Mail) doldur
  5. Yaş Grubu / Menşei gibi sabit varsayılanları yaz

Motor zaten depoda (`category_mapping._auto_setup_mapping`) ama yalnız panel
düğmesinden çağrılabiliyordu. Bu betik aynı motoru terminalden, eşleşmiş TÜM
kategoriler ya da tek kategori için çalıştırır.

ELLE YAPILAN EŞLEŞTİRME EZİLMEZ — motor yalnız boş kalanları doldurur.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/auto_setup_mapping.py --liste
    ~/apps/meluni/shared/venv/bin/python ../scripts/auto_setup_mapping.py --uygula
    ... --uygula --kategori takım        # yalnız adı eşleşen kategoriler
    ... --uygula --pazaryeri hepsiburada
"""
import argparse
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true",
                    help="Kurulumu ÇALIŞTIRIR (yazar). Verilmezse yalnız listeler.")
    ap.add_argument("--liste", action="store_true",
                    help="Eşleşmiş kategorileri ve mevcut durumu listeler.")
    ap.add_argument("--kategori", default="", help="Yalnız adı eşleşen kategoriler.")
    ap.add_argument("--pazaryeri", default="trendyol", help="trendyol | hepsiburada")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                                   # noqa: E402
    from routes.category_mapping import _auto_setup_mapping      # noqa: E402

    mp = args.pazaryeri
    sorgu = {"marketplace": mp, "marketplace_category_id": {"$nin": [None, ""]}}
    esleşmisler = await db.category_mappings.find(sorgu, {"_id": 0}).to_list(5000)
    if args.kategori:
        k = args.kategori.lower()
        esleşmisler = [c for c in esleşmisler
                       if k in str(c.get("category_name") or "").lower()]

    if not esleşmisler:
        print(f"{mp}: eşleşmiş kategori yok"
              + (f" (--kategori {args.kategori} süzgeciyle)" if args.kategori else "")
              + ".\nÖnce kategori eşleştirmesi yapın: scripts/match_trendyol_categories.py")
        return 1

    print(f"{mp}: {len(esleşmisler)} eşleşmiş kategori")
    for c in esleşmisler:
        print(f"  {str(c.get('category_name') or '?')[:34]:<36} → "
              f"{c.get('marketplace_category_name') or c.get('marketplace_category_id')}"
              f"   (özellik eşi: {len(c.get('attribute_mappings') or [])}, "
              f"varsayılan: {len(c.get('default_mappings') or {})})")

    if not args.uygula:
        print("\nHiçbir şey yazılmadı. Kurulumu çalıştırmak için: --uygula")
        return 0

    toplam = {"attr_matched": 0, "value_matched": 0,
              "company_filled": 0, "defaults_set": 0}
    tamam = hata = 0
    print()
    for c in esleşmisler:
        cid = c.get("category_id")
        if not cid:
            continue
        ad = str(c.get("category_name") or cid)[:34]
        try:
            r = await _auto_setup_mapping(mp, cid) or {}
        except Exception as e:                       # bir kategori turu durdurmasın
            hata += 1
            print(f"  {ad:<36} HATA: {str(e)[:80]}")
            continue
        if r.get("ok") is False:
            hata += 1
            print(f"  {ad:<36} atlandı: {r.get('reason')}")
            continue
        tamam += 1
        for k in toplam:
            toplam[k] += int(r.get(k, 0) or 0)
        print(f"  {ad:<36} özellik {r.get('attr_matched', 0)} · "
              f"değer {r.get('value_matched', 0)} · "
              f"şirket {r.get('company_filled', 0)} · "
              f"varsayılan {r.get('defaults_set', 0)}")

    print()
    print(f"kurulan kategori   : {tamam}" + (f" ({hata} atlandı/hata)" if hata else ""))
    print(f"eşlenen özellik    : {toplam['attr_matched']}")
    print(f"eşlenen değer      : {toplam['value_matched']}")
    print(f"şirket alanı doldu : {toplam['company_filled']}")
    print(f"sabit varsayılan   : {toplam['defaults_set']}")
    print("\nElle yapılmış eşleştirmelere dokunulmadı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
