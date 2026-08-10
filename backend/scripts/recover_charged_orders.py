"""
Parası çekilip siparişi ONAYLANMAYAN müşterileri kurtarır.

Arka plan: Ödeme doğrulaması (Y1) bir dönem iyzico'nun 'price' (indirim ÖNCESİ sepet brütü)
alanını order.total (indirim SONRASI) ile karşılaştırıyordu. Kupon/havale indirimi olan
siparişlerde para çekilmesine rağmen sipariş 'failed' işaretleniyordu. Buggy kod bu siparişlerde
bile iyzico'nun GERÇEK yanıtını (iyzico_retrieve_response) kaydettiği için, gerçekten ödenmiş
siparişleri güvenle tespit edip onaylayabiliriz.

Çalıştırma (Railway shell, backend/ dizininde, MONGO_URL + DB_NAME ortamda):
    python -m scripts.recover_charged_orders                 # DRY-RUN: yalnız rapor, HİÇBİR ŞEY değişmez
    python -m scripts.recover_charged_orders --days 5        # son 5 günü tara (varsayılan 3)
    python -m scripts.recover_charged_orders --apply         # kurtar: paid/confirmed + onay bildirimi

Kurallar:
- DRY-RUN varsayılandır. --apply verilmedikçe tek kayıt bile değişmez.
- YALNIZCA iyzico yanıtı BAŞARILI (status=success + paymentId var + paymentStatus FAILURE değil)
  VE tahsil edilen tutar (paidPrice) sipariş toplamına EŞİT/FAZLA olan siparişler kurtarılır.
  Böylece gerçekten ödenmemiş bir sipariş yanlışlıkla onaylanmaz.
- --apply: payment_status='paid', status='confirmed', paid_at yazar; ardından onay bildirimini
  + kupon kaydını + CAPI purchase'ı tetikler (idempotent — çift mail/çift sayım olmaz).
- Zaten 'paid' olan siparişlere dokunulmaz.
"""
import os
import sys
import asyncio
import argparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes.deps import db  # noqa: E402


def _f(v):
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def _is_real_success(snap: dict) -> bool:
    """iyzico snapshot'ı GERÇEK başarılı ödeme mi? (status=success + paymentId + FAILURE değil)"""
    if not isinstance(snap, dict):
        return False
    if not snap.get("paymentId"):
        return False
    if str(snap.get("status") or "").lower() != "success":
        return False
    ps = str(snap.get("paymentStatus") or "").upper()
    if ps and ps not in ("SUCCESS",):
        return False
    return True


async def main(apply: bool, days: int):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    query = {
        "payment_status": {"$in": ["failed", "pending"]},
        "created_at": {"$gte": cutoff},
    }
    recoverable = []   # kurtarılacaklar
    undercharge = []   # ödendi ama eksik (elle bak)
    skipped = 0

    async for o in db.orders.find(query, {"_id": 0}):
        snap = o.get("iyzico_retrieve_response") or {}
        if not _is_real_success(snap):
            skipped += 1
            continue
        total = _f(o.get("total"))
        paid = _f(snap.get("paidPrice"))
        row = {
            "id": o.get("id"), "order_number": o.get("order_number"),
            "total": total, "paid": paid, "paymentId": snap.get("paymentId"),
            "payment_status": o.get("payment_status"), "status": o.get("status"),
            "email": (o.get("shipping_address") or {}).get("email") or o.get("email"),
        }
        if paid + 0.02 >= total and total > 0:
            recoverable.append((o, row))
        else:
            undercharge.append(row)

    print(f"\n{'='*70}")
    print(f"Tarama: son {days} gün · aday (failed/pending) siparişler")
    print(f"  Kurtarılabilir (ödendi & tutar tamam): {len(recoverable)}")
    print(f"  Ödendi ama EKSİK tahsilat (elle bak):  {len(undercharge)}")
    print(f"  İlgisiz/gerçekten başarısız atlanan:    {skipped}")
    print(f"{'='*70}\n")

    for _o, r in recoverable:
        print(f"  [KURTAR] {r['order_number']:<14} total={r['total']:<10} "
              f"paid={r['paid']:<10} pid={r['paymentId']} {r['email'] or ''}")
    if undercharge:
        print("\n  --- EKSİK TAHSİLAT (otomatik kurtarılmaz, elle inceleyin) ---")
        for r in undercharge:
            print(f"  [EKSIK]  {r['order_number']:<14} total={r['total']:<10} "
                  f"paid={r['paid']:<10} pid={r['paymentId']} {r['email'] or ''}")

    if not apply:
        print(f"\nDRY-RUN — hiçbir şey değişmedi. Uygulamak için: --apply\n")
        return

    if not recoverable:
        print("\nKurtarılacak sipariş yok.\n")
        return

    # --- UYGULA ---
    from routes.payment import _notify_paid_order_confirmed  # noqa: E402
    try:
        from routes.orders import dispatch_purchase_capi  # noqa: E402
    except Exception:
        dispatch_purchase_capi = None

    fixed = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for o, r in recoverable:
        oid = o["id"]
        snap = o.get("iyzico_retrieve_response") or {}
        await db.orders.update_one(
            {"id": oid, "payment_status": {"$ne": "paid"}},
            {"$set": {
                "payment_status": "paid",
                "status": "confirmed",
                "paid_at": now_iso,
                "payment_id": snap.get("paymentId"),
                "iyzico_payment_id": snap.get("paymentId"),
                "recovered_by_script": True,
                "recovered_at": now_iso,
                "updated_at": now_iso,
            }},
        )
        # Onay bildirimi + kupon kaydı (idempotent) + CAPI purchase
        try:
            await _notify_paid_order_confirmed(oid)
        except Exception as e:
            print(f"    ! bildirim hatası {r['order_number']}: {e}")
        if dispatch_purchase_capi:
            try:
                await dispatch_purchase_capi(oid, source="recovery_script")
            except Exception:
                pass
        fixed += 1
        print(f"  ✓ kurtarıldı: {r['order_number']}")

    print(f"\nTOPLAM {fixed} sipariş 'Ödendi/Onaylandı' yapıldı ve onay bildirimi tetiklendi.\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Değişiklikleri uygula (yoksa DRY-RUN)")
    ap.add_argument("--days", type=int, default=3, help="Kaç gün geriye taransın (varsayılan 3)")
    args = ap.parse_args()
    asyncio.run(main(apply=args.apply, days=args.days))
