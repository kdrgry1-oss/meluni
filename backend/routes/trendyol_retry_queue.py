"""
Trendyol Sıkışmış Barkod Retry Kuyruğu

- Trendyol cache tutarsızlığı sebebiyle push edilemeyen barkodlar bir kuyruğa kaydedilir.
- Background task her saatte bir kuyruktaki tüm barkodları sync'e gönderir.
- Trendyol cache temizlenince (genelde 24-72 saat) barkod başarılı geçer ve kuyruktan çıkar.
- Manuel trigger için /admin/trendyol-retry endpoint'i mevcuttur.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from .deps import db as _db, require_admin, generate_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations/trendyol/retry-queue", tags=["Trendyol Retry Queue"])

MAX_ATTEMPTS = 12  # 12 deneme x 1 saat = 12 saat → genelde Trendyol cache temizlenir


def get_db():
    """Dependency for retry queue endpoints — uses shared motor client."""
    return _db

# ---------- HELPER ----------
async def add_to_queue(db: AsyncIOMotorDatabase, barcodes: List[str], reason: str = "Trendyol cache conflict"):
    """Sıkışmış barkodları kuyruğa ekler (varsa attempt_count'u artırmaz, sadece pending bırakır)."""
    if not barcodes:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for bc in barcodes:
        existing = await db.trendyol_stuck_queue.find_one({"barcode": str(bc), "resolved": False})
        if existing:
            continue
        await db.trendyol_stuck_queue.insert_one({
            "id": generate_id(),
            "barcode": str(bc),
            "reason": reason,
            "attempt_count": 0,
            "last_attempt_at": None,
            "last_result": None,
            "resolved": False,
            "resolved_at": None,
            "created_at": now,
        })
        added += 1
    if added:
        logger.info(f"Trendyol retry queue: {added} barkod eklendi")
    return added


async def mark_resolved(db: AsyncIOMotorDatabase, barcodes: List[str]):
    """Başarıyla push edilen barkodları kuyruktan kaldırır."""
    if not barcodes:
        return 0
    res = await db.trendyol_stuck_queue.update_many(
        {"barcode": {"$in": [str(b) for b in barcodes]}, "resolved": False},
        {"$set": {"resolved": True, "resolved_at": datetime.now(timezone.utc).isoformat()}}
    )
    return res.modified_count


async def update_attempt(db: AsyncIOMotorDatabase, barcode: str, result: str):
    """Bir denemenin sonucunu kaydeder."""
    await db.trendyol_stuck_queue.update_one(
        {"barcode": str(barcode), "resolved": False},
        {"$set": {
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
            "last_result": result[:300],
        }, "$inc": {"attempt_count": 1}}
    )


# ---------- ENDPOINTS ----------
@router.get("/list")
async def list_queue(
    resolved: bool = False,
    limit: int = 200,
    current_user: dict = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Kuyruktaki barkodları listeler."""
    query = {"resolved": resolved}
    docs = await db.trendyol_stuck_queue.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    total = await db.trendyol_stuck_queue.count_documents(query)
    return {"total": total, "items": docs}


@router.post("/run-now")
async def run_now(
    current_user: dict = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Kuyruğu manuel tetikler — pending barkodları tekrar push eder."""
    result = await process_queue(db)
    return result


@router.delete("/barcode/{barcode}")
async def remove_barcode(
    barcode: str,
    current_user: dict = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Bir barkodu kuyruktan kaldırır."""
    res = await db.trendyol_stuck_queue.delete_one({"barcode": barcode})
    return {"deleted": res.deleted_count}


@router.post("/add")
async def add_barcodes(
    payload: dict,
    current_user: dict = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Manuel olarak barkod ekleme (ileri-test için)."""
    barcodes = payload.get("barcodes", [])
    added = await add_to_queue(db, barcodes, reason="Manuel ekleme")
    return {"added": added, "total": len(barcodes)}


# ---------- CORE PROCESS ----------
async def process_queue(db: AsyncIOMotorDatabase) -> dict:
    """Pending barkodları sync_products_to_trendyol'a gönderir."""
    pending = await db.trendyol_stuck_queue.find(
        {"resolved": False, "attempt_count": {"$lt": MAX_ATTEMPTS}},
        {"_id": 0, "barcode": 1}
    ).to_list(500)
    if not pending:
        return {"processed": 0, "succeeded": 0, "still_stuck": 0, "msg": "Kuyruk boş"}

    barcodes = [str(p["barcode"]) for p in pending]
    logger.info(f"Trendyol retry queue: {len(barcodes)} barkod işleniyor")

    # Internal HTTP call ile kendi sync endpoint'ini çağır (döngü kırılması için
    # x-internal-trigger header'ı ile mark ediyoruz, ana sync queue_failures yapmaz)
    import httpx
    import os as _os
    backend_url = _os.environ.get("INTERNAL_BACKEND_URL", "http://localhost:8001")
    # Admin user için temporary token oluştur
    admin = await db.users.find_one({"email": "admin@facette.com"})
    if not admin:
        return {"processed": 0, "succeeded": 0, "still_stuck": len(barcodes), "error": "Admin user yok"}
    from routes.deps import create_token
    token = create_token(admin.get("id"), admin.get("is_admin", True), token_version=admin.get("token_version", 0))

    result = {}
    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            resp = await http.post(
                f"{backend_url}/api/integrations/trendyol/products/sync",
                json={"barcodes": barcodes},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Internal-Retry": "true",
                },
            )
            try:
                result = resp.json()
            except Exception:
                result = {"text": resp.text}
    except Exception as e:
        logger.error(f"Retry queue HTTP call error: {e}")
        for bc in barcodes:
            await update_attempt(db, bc, f"HTTP hatası: {str(e)[:100]}")
        return {"processed": len(barcodes), "succeeded": 0, "still_stuck": len(barcodes), "error": str(e)}

    # Sonuçları kuyruğa yansıt
    failed_barcodes = set()
    for f in (result.get("failed_items") or []):
        if f.get("barcode"):
            failed_barcodes.add(str(f["barcode"]))
    succeeded_candidates = [bc for bc in barcodes if bc not in failed_barcodes]
    still_stuck = [bc for bc in barcodes if bc in failed_barcodes]

    # Trendyol filter API ile gerçekten kayıtlı mı doğrula (sahte SUCCESS önle)
    try:
        from .integrations import get_trendyol_config
        from trendyol_client import TrendyolClient
        cfg = await get_trendyol_config()
        if cfg:
            client = TrendyolClient(
                supplier_id=cfg.get("supplier_id"),
                api_key=cfg.get("api_key"),
                api_secret=cfg.get("api_secret"),
                mode=cfg.get("mode", "live"),
            )
            real_succeeded = []
            for bc in succeeded_candidates:
                try:
                    r = await client.get_filtered_products(barcode=bc, archived=False, size=1)
                    if (r or {}).get("totalElements", 0) > 0:
                        real_succeeded.append(bc)
                    else:
                        still_stuck.append(bc)
                except Exception:
                    still_stuck.append(bc)
            succeeded_candidates = real_succeeded
    except Exception as e:
        logger.warning(f"Retry queue Trendyol verify failed: {e}")

    # DB güncelle
    if succeeded_candidates:
        await mark_resolved(db, succeeded_candidates)
    for bc in still_stuck:
        reason = next(
            (
                (f.get("reasons") or [""])[0]
                for f in (result.get("failed_items") or [])
                if str(f.get("barcode")) == str(bc)
            ),
            "Trendyol kabul etmedi (Sahte SUCCESS — filter API'de yok)",
        )
        await update_attempt(db, bc, reason)

    logger.info(f"Trendyol retry queue tamamlandı: {len(succeeded_candidates)} resolved, {len(still_stuck)} hala stuck")
    return {
        "processed": len(barcodes),
        "succeeded": len(succeeded_candidates),
        "succeeded_barcodes": succeeded_candidates,
        "still_stuck": len(still_stuck),
    }


# ---------- BACKGROUND TASK ----------
async def background_retry_loop(db_factory):
    """Saatte bir kuyruğu işler."""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 saat
            db = db_factory()
            await process_queue(db)
        except Exception as e:
            logger.error(f"Background retry loop error: {e}")
            await asyncio.sleep(300)  # hatada 5 dk bekle
