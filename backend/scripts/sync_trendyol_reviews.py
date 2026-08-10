"""
Site ürünleri için Trendyol public yorumlarını (>= min-rating) TOPLU çeker.

Eşleştirme: Trendyol getProducts (barcode -> contentId) ile site ürünlerinin
barcode'ları (ana + varyant) eşlenir; bulunan her contentId için public yorum
API'sinden 4 ve 5 yıldız yorumlar çekilip `product_reviews` koleksiyonuna yazılır,
ürünün `rating` / `review_count` alanları güncellenir. Yorumlar external_id ile
tekrarsız (idempotent) — script tekrar çalıştırılınca sadece yeni yorumlar eklenir.

Kullanım:
    DRY-RUN (yazmaz, sadece sayar):
        python -m scripts.sync_trendyol_reviews --min-rating 4 --dry-run
    CANLI (4 ve 5 yıldız):
        python -m scripts.sync_trendyol_reviews --min-rating 4
    Sadece ilk N ürün (test):
        python -m scripts.sync_trendyol_reviews --min-rating 4 --limit 20
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


async def main():
    min_rating = int(_arg("--min-rating", "4"))
    limit = int(_arg("--limit", "0") or 0)
    dry_run = "--dry-run" in sys.argv

    # Import burada (load_dotenv sonrası) — deps.db env'den kurulsun.
    from routes.integrations_trendyol_qna import sync_all_trendyol_reviews_core

    print(
        f"Trendyol yorum senkronu basliyor: min_rating={min_rating} "
        f"limit={limit or 'hepsi'} dry_run={dry_run}"
    )
    summary = await sync_all_trendyol_reviews_core(
        min_rating=min_rating, limit=limit, dry_run=dry_run
    )

    print("\n--- OZET ---")
    for k, v in summary.items():
        if k == "errors":
            print(f"  errors: {len(v)}")
            for e in v[:10]:
                print(f"    - {e}")
        else:
            print(f"  {k}: {v}")
    print("\nBitti." + ("  (DRY-RUN: hicbir sey yazilmadi)" if dry_run else ""))


if __name__ == "__main__":
    asyncio.run(main())
