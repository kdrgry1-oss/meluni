"""
audit_pixels.py — Veritabanında kayıtlı ölçüm/pixel yapılandırmalarını listeler.

NEDEN: Kodda sabit bir pixel kimliği bırakmıyoruz; hepsi `marketing_pixels`
koleksiyonundan geliyor. Bu yüzden "koda gömülü pixel var mı" sorusunu koda
bakarak yanıtlamak yetmez — önceki kurulumdan taşınmış bir kayıt hâlâ CAPI
üzerinden olay gönderiyor olabilir. Bu betik neyin kayıtlı olduğunu gösterir.

Token'lar MASKELENİR (son 4 hane) — denetim çıktısı sohbete/loga yapıştırılsa
bile gizli anahtar sızmaz.

Kullanım (sunucuda):
    cd ~/apps/meluni/current/backend
    ../../shared/venv/bin/python ../scripts/audit_pixels.py

Silmek için (yalnız istediğiniz kaydı, id vererek):
    ../../shared/venv/bin/python ../scripts/audit_pixels.py --sil <id>
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../backend")


def mask(v: str) -> str:
    v = str(v or "")
    if len(v) <= 4:
        return "****" if v else "(boş)"
    return f"{'*' * max(4, len(v) - 4)}{v[-4:]}  ({len(v)} karakter)"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sil", metavar="ID", help="Bu id'li pixel kaydını siler.")
    args = ap.parse_args()

    from routes.deps import db  # noqa: E402  (sys.path ayarından sonra)

    if args.sil:
        doc = await db.marketing_pixels.find_one({"id": args.sil}, {"_id": 0})
        if not doc:
            print(f"Böyle bir kayıt yok: {args.sil}")
            return 1
        print(f"Siliniyor: {doc.get('provider')} / {doc.get('name') or '—'} "
              f"/ tag_id={doc.get('tag_id')}")
        await db.marketing_pixels.delete_one({"id": args.sil})
        print("Silindi.")
        return 0

    rows = await db.marketing_pixels.find({}, {"_id": 0}).to_list(200)
    if not rows:
        print("marketing_pixels boş — hiçbir ölçüm yapılandırması kayıtlı değil.")
    else:
        print(f"{len(rows)} kayıt bulundu:\n")
        for r in rows:
            aktif = "AKTİF" if r.get("is_active") else "pasif"
            capi = "CAPI açık" if r.get("capi_enabled") else "CAPI kapalı"
            print(f"  id        : {r.get('id')}")
            print(f"  sağlayıcı : {r.get('provider')}   [{aktif}, {capi}]")
            print(f"  ad        : {r.get('name') or '—'}")
            print(f"  tag_id    : {r.get('tag_id') or '(boş)'}")
            print(f"  token     : {mask(r.get('access_token'))}")
            print(f"  eklenme   : {r.get('created_at') or '—'}")
            print()
        print("Tanımadığınız bir kayıt varsa:")
        print("  python ../scripts/audit_pixels.py --sil <id>")

    # Bekleyen CAPI kuyruğu: silinen bir pixel'e ait olaylar burada kalmış olabilir.
    try:
        kuyruk = await db.capi_event_queue.count_documents({})
        if kuyruk:
            print(f"\nUYARI: capi_event_queue içinde {kuyruk} bekleyen olay var.")
            print("Bunlar eski bir yapılandırmaya aitse gönderilmeyi denemeye devam eder.")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
