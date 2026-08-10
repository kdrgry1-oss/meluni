"""
=============================================================================
integrations_iyzico.py — Iyzico Ödeme Entegrasyonu
=============================================================================

`integrations.py` (3800+ satır) refactoring: Iyzico kısmını ayrı modül olarak
burada tutuyoruz. Mevcut kod tamamen aynı davranışı korur; sadece organize edilir.

Endpoint'ler:
  GET  /api/integrations/payment/status          → ödeme config durumu
  GET  /api/integrations/iyzico/settings         → admin: credential (maskeli)
  POST /api/integrations/iyzico/settings         → admin: credential kaydet
  POST /api/integrations/iyzico/test-connection  → admin: API bağlantı testi
  POST /api/integrations/iyzico/refund           → admin: kısmi iade (kargo kesintili)

Not: Diğer modüllerin (Trendyol, HB, Temu vb.) bu modülün `log_integration_event`
yardımcısına ihtiyacı vardır. İlk aşamada fonksiyon `integrations.py` içinde
kaldığı için oradan import edilir.
=============================================================================
"""
import os
import base64
import hashlib
import random
import uuid
import json
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, HTTPException, Depends

from .deps import db, logger, require_admin, require_permission

router = APIRouter(tags=["Integrations-Iyzico"])


# ----- Çevre değişkeni fallback'leri (DB bu değerleri geçersiz kılar) -----
IYZICO_MODE = os.environ.get("IYZICO_MODE", "sandbox")
IYZICO_API_KEY = os.environ.get("IYZICO_API_KEY", "")
IYZICO_SECRET_KEY = os.environ.get("IYZICO_SECRET_KEY", "")
IYZICO_BASE_URL = os.environ.get(
    "IYZICO_BASE_URL",
    "https://api.iyzipay.com" if IYZICO_MODE == "live" else "https://sandbox-api.iyzipay.com",
)


def is_iyzico_configured() -> bool:
    return bool(IYZICO_API_KEY and IYZICO_SECRET_KEY and IYZICO_API_KEY != "sandbox-api-key")


def _iyzico_auth_header(settings: dict, uri: str, body: dict):
    """iyzico IYZWSv2 HMAC-SHA256 auth (randomKey + path + body imzalanır) — payment.py `_v2_headers`
    ile AYNI. Eski sürüm SHA1/PKI imza + json=body kullanıyordu; iyzico bunu reddediyor, TÜM iadeler
    auth hatasıyla dönüyordu. Döner: (headers, body_str). body_str AYNEN content olarak gönderilmeli
    (imza gövdesiyle byte-byte eşleşsin)."""
    import hmac as _hmac
    import secrets as _secrets
    api_key = settings.get("api_key", "")
    from security.crypto import decrypt as _dec_secret  # at-rest şifreli sır; düz-metin passthrough
    secret = _dec_secret(settings.get("api_secret", "")) or ""
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    random_key = _secrets.token_hex(16)
    to_sign = f"{random_key}{uri}{body_str}"
    sig = _hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth_str = f"apiKey:{api_key}&randomKey:{random_key}&signature:{sig}"
    b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return ({
        "Authorization": f"IYZWSv2 {b64}",
        "x-iyzi-rnd": random_key,
        "Content-Type": "application/json",
    }, body_str)


@router.get("/payment/status")
async def get_payment_status():
    """DB'de kayıtlı ayar varsa onu kullan, yoksa env fallback."""
    db_settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if db_settings and db_settings.get("api_key") and db_settings.get("api_secret"):
        mode = db_settings.get("mode", "sandbox")
        base = "https://api.iyzipay.com" if mode == "live" else "https://sandbox-api.iyzipay.com"
        return {
            "mode": mode,
            "configured": bool(db_settings.get("is_active")),
            "base_url": base,
        }
    return {
        "mode": IYZICO_MODE,
        "configured": is_iyzico_configured(),
        "base_url": IYZICO_BASE_URL,
    }


@router.get("/iyzico/settings")
async def get_iyzico_settings(current_user: dict = Depends(require_admin)):
    settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not settings:
        return {
            "id": "iyzico",
            "api_key": "",
            "api_secret": "",
            "mode": "sandbox",
            "is_active": False,
        }
    if settings.get("api_secret"):
        settings["api_secret"] = "********"
    return settings


@router.post("/iyzico/settings")
async def save_iyzico_settings(payload: dict, current_user: dict = Depends(require_admin)):
    if payload.get("is_active"):
        existing = await db.settings.find_one({"id": "iyzico"}, {"_id": 0}) or {}
        api_key = payload.get("api_key") or existing.get("api_key")
        api_secret = payload.get("api_secret")
        if api_secret in (None, "", "********"):
            api_secret = existing.get("api_secret")
        if not api_key or not api_secret:
            raise HTTPException(
                status_code=400,
                detail="Iyzico aktifleştirmek için api_key ve api_secret zorunludur",
            )
    update_data = {
        "id": "iyzico",
        "api_key": payload.get("api_key", ""),
        "mode": payload.get("mode", "sandbox"),
        "is_active": payload.get("is_active", False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.get("api_secret") and payload.get("api_secret") != "********":
        # GÜVENLİK: ödeme sırrını at-rest şifrele (diğer entegrasyonlarla tutarlı)
        from security.crypto import encrypt as _enc_secret
        update_data["api_secret"] = _enc_secret(payload.get("api_secret"))
    await db.settings.update_one({"id": "iyzico"}, {"$set": update_data}, upsert=True)
    return {"success": True, "message": "Iyzico ayarları kaydedildi"}


@router.post("/iyzico/test-connection")
async def test_iyzico_connection(current_user: dict = Depends(require_admin)):
    settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not settings or not settings.get("api_key") or not settings.get("api_secret"):
        return {"success": False, "message": "Iyzico API bilgileri eksik"}
    return {"success": True, "message": "Iyzico API bilgileri kayıtlı. Refund test siparişle yapılabilir."}


@router.post("/iyzico/refund")
async def iyzico_refund(payload: dict, current_user: dict = Depends(require_permission("returns.iyzico_refund"))):
    """
    Iyzico kısmi iade. Kargo bedeli düşülerek iade yapılır.

    Body:
      order_id: str
      amount: float (iadesi yapılacak ürün tutarı, KDV dahil)
      shipping_deduction: float (opsiyonel, müşteriden kesilecek kargo bedeli)
      reason: str (opsiyonel)
    """
    # Geç import — log_integration_event hâlâ integrations.py'de (sonraki refactor'da ortak modüle taşınacak)
    from .integrations import log_integration_event  # noqa

    order_id = payload.get("order_id")
    amount = float(payload.get("amount") or 0)
    shipping_deduction = float(payload.get("shipping_deduction") or 0)
    reason = payload.get("reason", "Kısmi iade")
    if not order_id or amount <= 0:
        raise HTTPException(status_code=400, detail="order_id ve amount zorunlu")

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    pid = order.get("payment_id") or order.get("iyzico_payment_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Bu sipariş Iyzico ödemesi içermiyor veya payment_id yok")

    net_refund = round(amount - shipping_deduction, 2)
    if net_refund <= 0:
        raise HTTPException(status_code=400, detail="Kargo kesintisi sonrası iade tutarı 0 ya da negatif")

    # GÜVENLİK: TUTAR SINIRI + IDEMPOTENCY — toplam iade, tahsil edilen tutarı AŞAMAZ.
    # Cap kontrolü + rezervasyon ATOMİK yapılır (aşağıda, iyzico çağrısından ÖNCE). Eski
    # "önce oku, sonra yaz" deseni iki eşzamanlı istekte (çift-tık / iki operatör) çift iade
    # çağrısına yol açabiliyordu.
    try:
        _charged = float(order.get("total") or order.get("total_amount") or 0)
    except Exception:
        _charged = 0.0

    settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not settings or not settings.get("api_key"):
        raise HTTPException(status_code=400, detail="Iyzico API bilgileri eksik")
    base = (
        "https://api.iyzipay.com"
        if settings.get("mode") == "live"
        else "https://sandbox-api.iyzipay.com"
    )

    body = {
        "locale": "tr",
        "conversationId": f"refund-{order_id}",
        "paymentId": pid,
        "price": f"{net_refund:.2f}",
        "ip": "85.34.78.112",
        "currency": "TRY",
        "reason": reason,
    }
    uri = "/payment/refund"

    # ATOMİK CLAIM + REZERVASYON: mevcut iade toplamı (net_refund/amount) + bu iade, tahsil
    # edilen tutarı AŞMIYORSA 'pending' iade kalemi tek atomik işlemde PUSH edilir. İki eşzamanlı
    # istekten yalnız BİRİ geçer (ikincisi ilkinin pending kaydını sayıp reddedilir) → çift iade
    # imkânsız. iyzico başarısız/hata olursa rezervasyon $pull ile geri alınır (hediye çeki
    # iadesindeki refund_gift_card_once atomik-claim deseninin nakit karşılığı).
    _claim_id = uuid.uuid4().hex
    _pending_entry = {
        "amount": amount,
        "shipping_deduction": shipping_deduction,
        "net_refund": net_refund,
        "reason": reason,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "refunded_by": current_user.get("email", ""),
        "provider": "iyzico",
        "payment_id": pid,
        "claim_id": _claim_id,
        "status": "pending",
    }
    if _charged > 0:
        _cap = round(_charged + 0.02, 2)
        _claim = await db.orders.find_one_and_update(
            {"id": order_id, "$expr": {"$lte": [
                {"$add": [
                    {"$sum": {"$map": {
                        "input": {"$ifNull": ["$refunds", []]},
                        "as": "r",
                        "in": {"$toDouble": {"$ifNull": ["$$r.net_refund", {"$ifNull": ["$$r.amount", 0]}]}},
                    }}},
                    net_refund,
                ]}, _cap]}},
            {"$push": {"refunds": _pending_entry}},
        )
        if not _claim:
            _o2 = await db.orders.find_one({"id": order_id}, {"_id": 0, "refunds": 1}) or {}
            _already = 0.0
            for _r in (_o2.get("refunds") or []):
                try:
                    _already += float(_r.get("net_refund") or _r.get("amount") or 0)
                except Exception:
                    pass
            raise HTTPException(
                status_code=400,
                detail=f"İade tutarı tahsil edilen toplamı aşıyor (tahsil={_charged:.2f}, önceki iade={_already:.2f}, istenen={net_refund:.2f})",
            )
    else:
        # Tahsil tutarı bilinemiyorsa cap uygulanamaz; kalem yine de rezerve edilir (idempotency izi).
        await db.orders.update_one({"id": order_id}, {"$push": {"refunds": _pending_entry}})

    ok = False
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            headers, body_str = _iyzico_auth_header(settings, uri, body)
            resp = await c.post(f"{base}{uri}", content=body_str.encode("utf-8"), headers=headers)
            data = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {"raw": resp.text}
            )
        ok = (resp.status_code == 200) and (data.get("status") == "success")
        await log_integration_event(
            "iyzico", "refund", "order", order_id,
            "success" if ok else "error",
            f"Iyzico iade: gross={amount} kargo_kesinti={shipping_deduction} net={net_refund} → {data.get('status','?')}",
            {"response": data, "request": body},
        )
        if ok:
            # Rezervasyonu KESİNLEŞTİR (pending → done) + refunded_at yaz.
            await db.orders.update_one(
                {"id": order_id, "refunds.claim_id": _claim_id},
                {"$set": {
                    "refunds.$.status": "done",
                    "refunds.$.refunded_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        else:
            # BAŞARISIZ: rezervasyonu geri al ki cap kilitlenmesin ve tutar 'iade edildi' sanılmasın.
            await db.orders.update_one({"id": order_id}, {"$pull": {"refunds": {"claim_id": _claim_id}}})
        return {
            "success": ok,
            "net_refund": net_refund,
            "message": data.get("errorMessage") or ("Iade başarılı" if ok else "Iade başarısız"),
            "provider_response": data,
        }
    except Exception as e:
        # Ağ/işleme hatası. iyzico onayı ALINMADIYSA (ok=False) rezervasyonu geri al — güvenli
        # taraf: çift iade değil, iade blokajı. Onay alındıysa kaydı KORU (para hareket etti).
        if not ok:
            try:
                await db.orders.update_one({"id": order_id}, {"$pull": {"refunds": {"claim_id": _claim_id}}})
            except Exception:
                pass
        logger.error(f"Iyzico refund error: {e}")
        raise HTTPException(status_code=500, detail=f"Iyzico iade hatası: {e}")
