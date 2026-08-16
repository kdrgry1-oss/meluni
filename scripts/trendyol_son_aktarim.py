"""
trendyol_son_aktarim.py — Son Trendyol aktarımlarının GERÇEK sonucunu gösterir.

NEDEN: Panel "Son aktarım: 0 başarı" yazıyor ama NEDEN reddedildiğini
göstermiyor. Sebep `trendyol_sync_logs` koleksiyonuna yazılıyor (batch id,
hata metinleri, reddedilen ürün başına Trendyol'un gerekçesi). Bu betik onu
okunur biçimde basar; aktarımın neden tutmadığını tahmin etmek yerine
Trendyol'un kendi cevabını okuruz.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ~/apps/meluni/shared/venv/bin/python ../scripts/trendyol_son_aktarim.py
    ... --adet 5          # daha fazla kayıt
    ... --tam             # ham kaydı olduğu gibi bas
"""
import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))


def _yaz(baslik, deger):
    if deger in (None, "", [], {}):
        return
    print(f"  {baslik:<22} {deger}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adet", type=int, default=3, help="Kaç aktarım gösterilsin.")
    ap.add_argument("--tam", action="store_true", help="Ham kaydı bas.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    from routes.deps import db                       # noqa: E402

    kayitlar = await db.trendyol_sync_logs.find({}, {"_id": 0}) \
        .sort("started_at", -1).limit(args.adet).to_list(args.adet)
    if not kayitlar:
        print("Hiç aktarım kaydı yok (trendyol_sync_logs boş).")
        return 1

    for k in kayitlar:
        print("=" * 74)
        _yaz("zaman", k.get("started_at"))
        _yaz("durum", k.get("status"))
        _yaz("denenen ürün", k.get("products_attempted"))
        _yaz("gönderilen", k.get("products_sent"))
        _yaz("batch id", k.get("batch_request_id"))
        _yaz("mesaj", str(k.get("message") or "")[:300])
        for h in (k.get("errors") or [])[:10]:
            print(f"  HATA                   {str(h)[:260]}")
        for it in (k.get("failed_items") or [])[:20]:
            if isinstance(it, dict):
                sebep = it.get("reasons") or it.get("failureReasons") or []
                sebep_txt = "; ".join(str(x) for x in sebep)[:220] if sebep else "?"
                print(f"  RED  {str(it.get('stock_code') or it.get('barcode') or '')[:20]:<22}"
                      f" {sebep_txt}")
            else:
                print(f"  RED  {str(it)[:240]}")
        if k.get("not_found_codes"):
            _yaz("bulunamayan kod", ", ".join(map(str, k["not_found_codes"]))[:200])
        if args.tam:
            print(json.dumps(k, ensure_ascii=False, indent=2, default=str)[:4000])
    print("=" * 74)
    print("Trendyol'un gerekçesi yukarıda; 'RED' satırları ürün başına sebeptir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
