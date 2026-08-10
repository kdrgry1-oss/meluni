from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Header
from typing import Dict, Any, Optional
from routes.deps import logger, db
import traceback
import hmac
import hashlib
import os
from datetime import datetime, timezone

router = APIRouter(tags=["Webhooks"])


def _verify_trendyol_signature(body: bytes, signature: Optional[str]) -> bool:
    """Trendyol webhook HMAC-SHA256 imza doğrulaması — FAIL-CLOSED.
    GÜVENLİK: Secret set DEĞİLSE istek REDDEDİLİR (eskiden True dönüp kimliksiz
    event kabul ediyordu → sipariş durumu/stok forge edilebiliyordu). Yalnızca
    açıkça TRENDYOL_WEBHOOK_ALLOW_UNSIGNED=1 verilen dev ortamında imzasız geçer.
    """
    secret = os.environ.get("TRENDYOL_WEBHOOK_SECRET", "").strip()
    if not secret:
        if os.environ.get("TRENDYOL_WEBHOOK_ALLOW_UNSIGNED", "").strip() == "1":
            logger.warning("Trendyol webhook: secret YOK, ALLOW_UNSIGNED=1 (yalnız DEV) — imzasız kabul")
            return True
        logger.error("Trendyol webhook: TRENDYOL_WEBHOOK_SECRET ayarlı değil — istek REDDEDİLDİ (fail-closed)")
        return False
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Trendyol base64 veya hex formatında gönderebilir; iki karşılaştırma yap
    import base64
    expected_b64 = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(signature, expected) or hmac.compare_digest(signature, expected_b64)


@router.post("/webhooks/trendyol")
async def trendyol_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_trendyol_signature: Optional[str] = Header(default=None, alias="X-Trendyol-Signature"),
):
    """
    Trendyol Event Notification Webhook — HMAC-SHA256 imza doğrulamalı.
    """
    body = await request.body()

    if not _verify_trendyol_signature(body, x_trendyol_signature):
        logger.warning("Trendyol webhook imza doğrulaması başarısız — istek reddedildi")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        import json
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}

    if not payload:
        return {"status": "ok"}

    logger.info(f"Received Trendyol Webhook: {payload.get('eventType')} - {payload.get('orderNumber')}")

    # Process in background
    background_tasks.add_task(process_trendyol_event, payload)

    return {"status": "ok", "message": "Event received"}

async def process_trendyol_event(payload: dict):
    """Background task to process the event"""
    try:
        event_type = payload.get("eventType")
        
        if event_type in ["OrderCancelled", "ClaimApproved"]:
            await handle_stock_restoration(payload)
            
        elif event_type == "OrderStatusChanged":
            await handle_order_status_change(payload)
            
    except Exception as e:
        logger.error(f"Error processing Trendyol webhook: {str(e)}\n{traceback.format_exc()}")


async def _webhook_event_is_new(source: str, payload: dict) -> bool:
    """İDEMPOTENS: Aynı webhook eventi (Trendyol retry / replay) stoğu tekrar
    geri yüklemesin. Event için kararlı bir anahtar üretip atomik upsert ile
    ilk kez mi işleniyor kontrol eder. True → yeni (işlenebilir), False → mükerrer.
    """
    try:
        import json as _json
        key_src = _json.dumps({
            "s": source,
            "e": payload.get("eventType"),
            "o": payload.get("orderNumber"),
            "sp": payload.get("shipmentPackageId") or payload.get("id"),
            "l": [(l.get("barcode"), l.get("quantity")) for l in (payload.get("orderLines") or [])],
        }, sort_keys=True, ensure_ascii=False)
        ekey = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
        res = await db.processed_webhook_events.update_one(
            {"key": ekey},
            {"$setOnInsert": {"key": ekey, "source": source, "created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return res.upserted_id is not None
    except Exception as e:
        logger.warning(f"webhook idempotency check failed ({e}) — işleniyor kabul edildi")
        return True


async def handle_stock_restoration(payload: dict):
    """
    Sipariş iptal edildiğinde veya iade onaylandığında ilgili stokları geri yükle.
    Trendyol 'ClaimApproved' ve 'OrderCancelled' eventlarında orderLineItem listesi atar.
    GÜVENLİK: idempotent — mükerrer event stoğu tekrar şişirmez.
    """
    # B2: Bu webhook ARTIK stoğu DOĞRUDAN DÜŞMEZ/EKLEMEZ. Eskiden ham `$inc variants.$.stock`
    # yapıyordu → (a) parent 'stock'u recompute etmiyor (parent/variant desync), (b) stock_movements
    # yazmıyordu → iptal-senkron cron'u (_update_existing_trendyol_order / claims-sync) ile AYRI
    # idempotency deposu kullanıp aynı iptali/iadeyi İKİ kez stoğa ekliyordu (hayalet şişme).
    # Stok iadesi artık TEK yetkili yola bırakıldı: Trendyol iptal/iade senkron cron'u
    # _stock_delta_for_order + parent recompute + broad _RESTORE_MOVE_TYPES guard ile işler.
    # Webhook yalnız event'i loglar (statü/bildirim tarafı ayrı handler'larda).
    lines = payload.get("orderLines", [])
    if not lines:
        return
    if not await _webhook_event_is_new("trendyol", payload):
        return
    logger.info(f"Trendyol Webhook: stok geri yükleme eventi alındı ({len(lines)} kalem) — "
                f"stok iadesi iptal/iade senkron cron'unda tek yetkili yolda yapılır (webhook stoğa dokunmaz).")


async def handle_order_status_change(payload: dict):
    """
    Trendyol'da sipariş kargoya verildi veya teslim edildi statüsüne geçtiyse 
    bizim paneldeki 'status' alanını da güncelleyebiliriz.
    """
    order_number = payload.get("orderNumber")
    new_status = payload.get("status")
    
    if order_number and new_status:
        # Trendyol status mapping
        status_map = {
            "Shipped": "shipped",
            "Delivered": "delivered",
            "Cancelled": "cancelled",
            "UnDelivered": "returned"
        }
        mapped_status = status_map.get(new_status)
        if mapped_status:
            await db.orders.update_one(
                {"order_number": str(order_number), "platform": "trendyol"},
                {"$set": {"status": mapped_status}}
            )
            logger.info(f"Trendyol Webhook: Order {order_number} status updated to {mapped_status}")
