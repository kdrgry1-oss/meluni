"""
Geçmiş SİTE gider pusulalarını yeniden hesaplar (eski hesapta kargo + sipariş-seviyesi
indirim eksikti; tam iadeler order.total yerine yalnız ürün toplamı yazılmıştı).

Çalıştırma (Railway shell, backend/ dizininde, MONGO_URL + DB_NAME ortamda):
    python -m scripts.recompute_site_gp            # DRY-RUN: yalnız rapor, HİÇBİR ŞEY değişmez
    python -m scripts.recompute_site_gp --apply    # TAM iade GP'lerini order.total'a düzeltir (YEDEKLİ)

Kurallar:
- DRY-RUN varsayılandır. --apply verilmedikçe tek kayıt bile değişmez.
- --apply YALNIZ tam iade GP'lerini düzeltir: net = order.total (tüm fatura tutarı, kargo dahil).
  Eski totals, geri alınabilsin diye GP üzerinde `_recompute_backup` alanına yazılır.
- KISMİ iade GP'leri DEĞİŞTİRİLMEZ — kargo dahil/mahsup niyeti eski kayıtta yok.
  Bunlar raporda listelenir; doğru tutar için panelden (kalem + kargo seçerek) yeniden üretilmeli.
"""
import os
import sys
import asyncio
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402


def _r2(v):
    try:
        return round(float(v or 0) + 1e-9, 2)
    except Exception:
        return 0.0


async def main(apply: bool):
    full_fix = []        # (gp, row) — düzeltilecek tam iadeler
    partial_flag = []    # row — manuel gözden geçirilecek kısmiler
    nochange = 0

    async for gp in db.gider_pusulasi.find({"source": "site"}, {"_id": 0}):
        rid = gp.get("return_id")
        ret = await db.customer_returns.find_one({"id": rid}, {"_id": 0}) if rid else None
        order = await db.orders.find_one({"id": (ret or {}).get("order_id")}, {"_id": 0}) if ret else None

        old_net = _r2((gp.get("totals") or {}).get("net"))
        gp_prod = [i for i in (gp.get("items") or []) if not str(i.get("name", "")).startswith("Kargo")]
        ret_items = (ret or {}).get("items") or []
        is_full = bool(ret_items) and len(gp_prod) >= len(ret_items)
        order_total = _r2((order or {}).get("total"))

        row = {"no": gp.get("display_number") or gp.get("number"), "order": gp.get("order_number"), "old": old_net}
        if is_full and order_total > 0:
            if abs(order_total - old_net) > 0.01:
                row["new"] = order_total
                full_fix.append((gp, row))
            else:
                nochange += 1
        else:
            partial_flag.append(row)

    print("\n===== TAM İADE — düzeltilecek (net = tüm fatura tutarı) =====")
    print(f"{'GP No':<16}{'Sipariş':<18}{'Eski':>12}{'Yeni':>12}{'Fark':>12}")
    tot_delta = 0.0
    for _, r in full_fix:
        d = r["new"] - r["old"]
        tot_delta += d
        print(f"{str(r['no']):<16}{str(r['order']):<18}{r['old']:>12.2f}{r['new']:>12.2f}{d:>12.2f}")
    print(f"  Toplam {len(full_fix)} kayıt · net fark {tot_delta:+.2f} TL")

    print("\n===== KISMİ İADE — manuel (DEĞİŞTİRİLMEDİ) =====")
    print("  (kargo dahil/mahsup niyeti eski kayıtta yok → panelden kalem+kargo seçilerek yeniden üretilmeli)")
    for r in partial_flag:
        print(f"  {str(r['no']):<16}{str(r['order']):<18} eski net {r['old']:.2f}")
    print(f"  Toplam {len(partial_flag)} kayıt")
    print(f"\nDeğişmeyen (zaten doğru) tam iade: {nochange}")

    if not apply:
        print("\nDRY-RUN — hiçbir kayıt değişmedi. Uygulamak için: python -m scripts.recompute_site_gp --apply")
        return

    n = 0
    for gp, r in full_fix:
        new_net = r["new"]
        tot = dict(gp.get("totals") or {})
        vat_rate = tot.get("vat_rate") or 10
        vat_amount = round(new_net * vat_rate / (100 + vat_rate), 2)
        await db.gider_pusulasi.update_one(
            {"return_id": gp.get("return_id")},
            {"$set": {
                "totals.net": new_net,
                "totals.vat_amount": vat_amount,
                "totals.net_without_vat": round(new_net - vat_amount, 2),
                "_recompute_backup": {"totals": gp.get("totals"), "at": datetime.datetime.utcnow().isoformat()},
            }},
        )
        n += 1
    print(f"\nUYGULANDI: {n} tam iade GP'si order.total'a güncellendi (eski değerler _recompute_backup'ta).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Tam iade GP'lerini gerçekten güncelle")
    asyncio.run(main(ap.parse_args().apply))
