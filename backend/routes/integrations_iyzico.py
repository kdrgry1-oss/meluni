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
import math
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
async def get_payment_status(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """DB'de kayıtlı ayar varsa onu kullan, yoksa env fallback.
    IAM-01: yalnız panel (Entegrasyonlar, Authorization başlığıyla) kullanır; kimliksiz dönmez."""
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
async def get_iyzico_settings(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
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
async def save_iyzico_settings(payload: dict, current_user: dict = Depends(require_permission("integrations.iyzico"))):
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
async def test_iyzico_connection(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not settings or not settings.get("api_key") or not settings.get("api_secret"):
        return {"success": False, "message": "Iyzico API bilgileri eksik"}
    return {"success": True, "message": "Iyzico API bilgileri kayıtlı. Refund test siparişle yapılabilir."}


def _num(v) -> float:
    """Tutar alanını güvenli float'a çevirir; NaN/inf/geçersiz → HTTP 400 (NaN, Mongo karşılaştırmasında
    tavan kontrolünü atlatabilirdi)."""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Geçersiz tutar")
    if not math.isfinite(f):
        raise HTTPException(status_code=400, detail="Geçersiz tutar")
    return f


def _captured_amount(order: dict, pid: str) -> float:
    """Bu ödeme için iyzico'nun GERÇEKTEN tahsil ettiği tutar. Öncelik: aynı paymentId'ye ait
    iyzico yanıt görüntüsündeki paidPrice (taksit vade farkı dahil gerçek çekim); yoksa sipariş
    toplamı. Bilinemezse 0 (→ iade REDDEDİLİR; tavansız iade yapılmaz)."""
    snap = order.get("iyzico_retrieve_response") or {}
    try:
        if isinstance(snap, dict) and str(snap.get("paymentId") or "") == str(pid) \
                and snap.get("paidPrice") not in (None, ""):
            v = round(float(snap.get("paidPrice")), 2)
            if math.isfinite(v) and v > 0:
                return v
    except (TypeError, ValueError):
        pass
    for k in ("total", "total_amount"):
        try:
            v = round(float(order.get(k) or 0), 2)
            if math.isfinite(v) and v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


_UNKNOWN_REFUND_MSG = (
    "iyzico iade isteğinin SONUCU BELİRSİZ (bağlantı/zaman aşımı). İade gerçekleşmiş olabilir: "
    "iyzico panelinden bu ödemenin iadelerini kontrol edip 'İade sonucunu onayla' ile işaretleyin. "
    "Bu kontrol yapılana kadar bu siparişte yeni iade başlatılamaz (çift iade önlemi).")


@router.post("/iyzico/refund")
async def iyzico_refund(payload: dict, current_user: dict = Depends(require_permission("returns.iyzico_refund"))):
    """
    Iyzico kısmi iade. Kargo bedeli düşülerek iade yapılır.

    Body:
      order_id: str
      amount: float (iadesi yapılacak ürün tutarı, KDV dahil)
      shipping_deduction: float (opsiyonel, müşteriden kesilecek kargo bedeli, >= 0)
      reason: str (opsiyonel)

    ÇİFT İADE KALKANI (denetim 2026-10):
      - Toplam iade (bekleyen + belirsiz + tamamlanan) iyzico'nun tahsil ettiği tutarı AŞAMAZ;
        tahsil tutarı bilinmiyorsa iade yapılmaz.
      - Zaman aşımı/bağlantı kopması gibi BELİRSİZ sonuçta rezervasyon KORUNUR ve kalem
        status="unknown" olur; admin /iyzico/refund/resolve ile sonucu onaylayana kadar o
        siparişte yeni iade başlatılamaz (eskiden rezervasyon bırakılıyor, tekrar deneme ikinci
        kez iade edebiliyordu).
      - Her iade denemesi benzersiz conversationId (refund-<claim_id>) taşır; iyzico panelinde/
        raporlamada bu referansla aranabilir.
    """
    # ACİL ANAHTAR (freeze_refunds): para iadesi uçları dondurulduysa iyzico'ya istek GÖNDERİLMEZ (423).
    # CHAIN08: aynı kapı restore_mode açıkken ve geri yükleme sonrası iade kilidi (iyzico mutabakatı
    # tamamlanana kadar) açıkken de 423 döner — eski yedekte görünmeyen bir iade ikinci kez yapılmaz.
    # Not: /iyzico/refund/resolve (belirsiz sonucun elle mutabakatı) para hareketi yapmadığı için
    # dondurulmaz — olay sırasında da mutabakat yapılabilmeli.
    from security_switches import require_switch_off
    await require_switch_off("freeze_refunds")
    # Geç import — log_integration_event hâlâ integrations.py'de (sonraki refactor'da ortak modüle taşınacak)
    from .integrations import log_integration_event  # noqa

    order_id = str(payload.get("order_id") or "").strip()
    amount = round(_num(payload.get("amount")), 2)
    shipping_deduction = round(_num(payload.get("shipping_deduction")), 2)
    reason = str(payload.get("reason") or "Kısmi iade")[:200]
    if not order_id or amount <= 0:
        raise HTTPException(status_code=400, detail="order_id ve amount zorunlu")
    if shipping_deduction < 0:
        # Negatif kesinti net iadeyi ürün tutarının ÜSTÜNE çıkarırdı.
        raise HTTPException(status_code=400, detail="Kargo kesintisi negatif olamaz")

    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    pid = order.get("payment_id") or order.get("iyzico_payment_id")
    if not pid:
        raise HTTPException(status_code=400, detail="Bu sipariş Iyzico ödemesi içermiyor veya payment_id yok")
    # COM11 / FIN-E03: AÇIK ödeme itirazı (chargeback) varken iyzico iadesi yapılmaz (çift para çıkışı).
    # Süper admin gerekçe (chargeback_override_reason) ile denetimli istisna yapabilir.
    from .chargebacks import assert_refund_allowed
    _cb_gate = await assert_refund_allowed(order_id, current_user, payload, action="iyzico.refund")

    net_refund = round(amount - shipping_deduction, 2)
    if net_refund <= 0:
        raise HTTPException(status_code=400, detail="Kargo kesintisi sonrası iade tutarı 0 ya da negatif")

    # GÜVENLİK: TUTAR SINIRI + IDEMPOTENCY — toplam iade, tahsil edilen tutarı AŞAMAZ.
    # Cap kontrolü + rezervasyon ATOMİK yapılır (aşağıda, iyzico çağrısından ÖNCE). Eski
    # "önce oku, sonra yaz" deseni iki eşzamanlı istekte (çift-tık / iki operatör) çift iade
    # çağrısına yol açabiliyordu.
    _charged = _captured_amount(order, pid)
    if _charged <= 0:
        raise HTTPException(
            status_code=400,
            detail="Bu ödemenin tahsil tutarı belirlenemedi; tavansız iade yapılamaz. İadeyi iyzico panelinden yapın.")

    settings = await db.settings.find_one({"id": "iyzico"}, {"_id": 0})
    if not settings or not settings.get("api_key"):
        raise HTTPException(status_code=400, detail="Iyzico API bilgileri eksik")
    base = (
        "https://api.iyzipay.com"
        if settings.get("mode") == "live"
        else "https://sandbox-api.iyzipay.com"
    )

    # ATOMİK CLAIM + REZERVASYON: mevcut iade toplamı (net_refund/amount) + bu iade, tahsil
    # edilen tutarı AŞMIYORSA ve sonucu BELİRSİZ bir iade YOKSA 'pending' iade kalemi tek atomik
    # işlemde PUSH edilir. İki eşzamanlı istekten yalnız BİRİ geçer (ikincisi ilkinin pending
    # kaydını sayıp reddedilir). iyzico KESİN başarısız derse rezervasyon $pull ile geri alınır;
    # sonuç belirsizse KORUNUR (status=unknown).
    _claim_id = uuid.uuid4().hex
    _conv_id = f"refund-{_claim_id}"   # deneme-başına sabit referans (iyzico'da aranabilir)
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
        "conversation_id": _conv_id,
        "captured_amount": _charged,
        "status": "pending",
    }
    if _cb_gate.get("override"):
        _pending_entry["chargeback_override_reason"] = _cb_gate.get("reason")
    _cap = round(_charged + 0.02, 2)
    _claim_filter_cb = {} if _cb_gate.get("override") else {"chargeback_open": {"$ne": True}}
    _claim = await db.orders.find_one_and_update(
        {"id": order_id,
         **_claim_filter_cb,   # COM11: kontrol ile claim arasında itiraz açılırsa claim tutmaz
         "refunds": {"$not": {"$elemMatch": {"status": "unknown"}}},
         "$expr": {"$lte": [
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
        _o2 = await db.orders.find_one({"id": order_id}, {"_id": 0, "refunds": 1, "chargeback_open": 1}) or {}
        if _o2.get("chargeback_open") is True and not _cb_gate.get("override"):
            raise HTTPException(status_code=409, detail=(
                "Bu siparişte AÇIK bir ödeme itirazı (chargeback) var; itiraz sonuçlanana kadar yeni iade yapılamaz."))
        if any(str((_r or {}).get("status") or "") == "unknown" for _r in (_o2.get("refunds") or [])):
            raise HTTPException(status_code=409, detail=_UNKNOWN_REFUND_MSG)
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

    body = {
        "locale": "tr",
        "conversationId": _conv_id,
        "paymentId": pid,
        "price": f"{net_refund:.2f}",
        "ip": "85.34.78.112",
        "currency": "TRY",
        "reason": reason,
    }
    uri = "/payment/refund"

    # outcome: "done" (iyzico kesin başarı) | "failed" (iyzico kesin ret / istek HİÇ gönderilmedi)
    #          | "unknown" (istek gitti ama sonucu okunamadı → iade olmuş OLABİLİR)
    outcome = "unknown"
    data: dict = {}
    err = ""
    try:
        headers, body_str = _iyzico_auth_header(settings, uri, body)
    except Exception as e:
        # İmza üretilemedi → istek gönderilmedi → kesin başarısız.
        outcome, err = "failed", f"imza hatası: {e}"
    else:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                resp = await c.post(f"{base}{uri}", content=body_str.encode("utf-8"), headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # Bağlantı HİÇ kurulamadı → istek iyzico'ya ulaşmadı → kesin başarısız.
            outcome, err = "failed", f"bağlantı kurulamadı: {e}"
        except Exception as e:
            # Okuma zaman aşımı / bağlantı kopması: istek iyzico'ya ulaşmış olabilir → BELİRSİZ.
            outcome, err = "unknown", f"{type(e).__name__}: {e}"
        else:
            _is_json = resp.headers.get("content-type", "").startswith("application/json")
            try:
                data = resp.json() if _is_json else {"raw": resp.text[:500]}
            except Exception:
                data, _is_json = {"raw": resp.text[:500]}, False
            if not isinstance(data, dict):
                data, _is_json = {"raw": str(data)[:500]}, False
            if _is_json and resp.status_code == 200 and data.get("status") == "success":
                outcome = "done"
            elif _is_json and str(data.get("status") or "").lower() == "failure":
                outcome = "failed"   # iyzico açıkça reddetti → para hareket etmedi
                err = str(data.get("errorMessage") or "")
            else:
                outcome = "unknown"  # 5xx / JSON olmayan yanıt → sonuç bilinmiyor
                err = f"HTTP {resp.status_code} belirsiz yanıt"

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await log_integration_event(
            "iyzico", "refund", "order", order_id,
            "success" if outcome == "done" else ("unknown" if outcome == "unknown" else "error"),
            f"Iyzico iade: gross={amount} kargo_kesinti={shipping_deduction} net={net_refund} "
            f"ref={_conv_id} → {outcome}{(' (' + err[:200] + ')') if err else ''}",
            {"response": data, "request": body, "outcome": outcome},
        )
    except Exception:
        pass

    if outcome == "done":
        # Rezervasyonu KESİNLEŞTİR (pending → done) + refunded_at yaz.
        await db.orders.update_one(
            {"id": order_id, "refunds.claim_id": _claim_id},
            {"$set": {
                "refunds.$.status": "done",
                "refunds.$.refunded_at": now_iso,
            }},
        )
        return {
            "success": True,
            "status": "done",
            "net_refund": net_refund,
            "reference": _conv_id,
            "message": "Iade başarılı",
            "provider_response": data,
        }
    if outcome == "failed":
        # KESİN BAŞARISIZ: rezervasyonu geri al ki cap kilitlenmesin ve tutar 'iade edildi' sanılmasın.
        await db.orders.update_one({"id": order_id}, {"$pull": {"refunds": {"claim_id": _claim_id}}})
        return {
            "success": False,
            "status": "failed",
            "net_refund": net_refund,
            "reference": _conv_id,
            "message": data.get("errorMessage") or err or "Iade başarısız",
            "provider_response": data,
        }
    # BELİRSİZ: rezervasyon KORUNUR (tavana sayılır) + kalem 'unknown' → elle doğrulama gerekir.
    await db.orders.update_one(
        {"id": order_id, "refunds.claim_id": _claim_id},
        {"$set": {
            "refunds.$.status": "unknown",
            "refunds.$.unknown_at": now_iso,
            "refunds.$.error": err[:300],
            "refund_needs_verification": True,
        }},
    )
    logger.error(f"[IYZICO IADE] sonuç BELİRSİZ order_id={order_id} ref={_conv_id} net={net_refund}: {err}")
    return {
        "success": False,
        "status": "unknown",
        "needs_verification": True,
        "claim_id": _claim_id,
        "net_refund": net_refund,
        "reference": _conv_id,
        "message": _UNKNOWN_REFUND_MSG,
        "provider_response": data,
    }


@router.post("/iyzico/refund/resolve")
async def iyzico_refund_resolve(payload: dict, current_user: dict = Depends(require_permission("returns.iyzico_refund"))):
    """Sonucu BELİRSİZ (status=unknown) bir iyzico iadesini, admin iyzico panelinden kontrol ettikten
    sonra elle sonuçlandırır.
      outcome="done"   → iade iyzico'da GERÇEKLEŞMİŞ: kalem 'done' olur (tavana sayılmaya devam eder).
      outcome="failed" → iade iyzico'da YOK: rezervasyon kaldırılır (yeniden iade yapılabilir).
    Body: order_id, claim_id, outcome, note (opsiyonel)."""
    from .integrations import log_integration_event  # noqa
    order_id = str(payload.get("order_id") or "").strip()
    claim_id = str(payload.get("claim_id") or "").strip()
    outcome = str(payload.get("outcome") or "").strip().lower()
    note = str(payload.get("note") or "")[:300]
    if not order_id or not claim_id or outcome not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="order_id, claim_id ve outcome (done|failed) zorunlu")
    now_iso = datetime.now(timezone.utc).isoformat()
    who = current_user.get("email", "") or current_user.get("id", "")
    _match = {"id": order_id, "refunds": {"$elemMatch": {"claim_id": claim_id, "status": "unknown"}}}
    if outcome == "done":
        res = await db.orders.update_one(_match, {"$set": {
            "refunds.$.status": "done",
            "refunds.$.refunded_at": now_iso,
            "refunds.$.resolved_by": who,
            "refunds.$.resolved_at": now_iso,
            "refunds.$.resolve_note": note,
        }})
    else:
        res = await db.orders.update_one(_match, {"$pull": {"refunds": {"claim_id": claim_id, "status": "unknown"}}})
    if res.modified_count != 1:
        raise HTTPException(status_code=404, detail="Sonucu belirsiz böyle bir iade kalemi bulunamadı (zaten sonuçlanmış olabilir)")
    # Başka belirsiz kalem kalmadıysa sipariş bayrağını kaldır.
    await db.orders.update_one(
        {"id": order_id, "refunds": {"$not": {"$elemMatch": {"status": "unknown"}}}},
        {"$unset": {"refund_needs_verification": ""}})
    try:
        await log_integration_event(
            "iyzico", "refund_resolve", "order", order_id, "success",
            f"Belirsiz iade elle sonuçlandırıldı: claim={claim_id} → {outcome} ({who}) {note}".strip(),
            {"claim_id": claim_id, "outcome": outcome, "by": who},
        )
    except Exception:
        pass
    return {"success": True, "claim_id": claim_id, "status": "done" if outcome == "done" else "removed"}
