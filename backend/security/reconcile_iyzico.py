"""
iyzico ödeme mutabakatı (COM12 / CHAIN08) — günlük, SALT-OKUNUR.

Son N günün (varsayılan 3) ödenmiş kart siparişleri için iyzico'dan ödeme ayrıntısı
(`/payment/detail`, payment.py'nin imzalı istek yardımcılarıyla) sorgulanır ve karşılaştırılır:
  * ödeme sağlayıcıda gerçekten SUCCESS mi (payment._is_paid, strict),
  * yanıt bu siparişe ve tutara ait mi (payment._payment_matches_order — conversationId/basketId/
    para birimi/eksik tahsilat),
  * paymentId, siparişteki iyzico_payment_id ile aynı mı,
  * tahsil edilen tutar siparişte kayıtlı iyzico görüntüsündeki paidPrice ile aynı mı,
  * iade toplamı: siparişte "done" iade kalemleri ↔ iyzico yanıtındaki iade toplamı (yanıt iade
    bilgisini taşıyorsa; taşımıyorsa "unavailable" sayılır, uyuşmazlık üretmez).
Uyuşmazlık → `integration_logs` (service=iyzico, event=reconciliation_mismatch) + güvenlik uyarısı.
Siparişe ve ödeme durumuna ASLA yazılmaz (CLAUDE.md değişmez 1: "paid" yalnız
_mark_order_from_payment'tan). Kimlik bilgisi HER sağlayıcı çağrısından önce yeniden okunur (PRIV11).
Restore sonrası (CHAIN08): restore_mode kapatılınca security_switches.run_post_restore_reconciliation
restore penceresi için bunu OTOMATİK çalıştırır; sonuç tam ve temizse "geri yükleme sonrası iade
kilidi" kalkar, değilse kilit kalır ve kritik uyarı gider. Elle: `await run_iyzico_reconciliation(days=N)`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

STATE_ID = "iyzico_reconciliation"
_CARD_METHODS = ["credit_card", "card", "kredi_karti", "kart", "iyzico", "creditcard"]
_PAID_LIKE = ["paid", "refunded", "partially_refunded"]


def _f(v) -> Optional[float]:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def provider_refund_total(data: dict) -> Optional[float]:
    """iyzico yanıtındaki iade toplamı; yanıt iade bilgisi taşımıyorsa None."""
    if not isinstance(data, dict):
        return None
    for k in ("totalRefundedPrice", "refundedPrice", "refundPrice"):
        v = _f(data.get(k))
        if v is not None:
            return v
    found, total = False, 0.0

    def _sum_list(lst) -> None:
        nonlocal found, total
        for r in lst or []:
            if not isinstance(r, dict):
                continue
            st = str(r.get("status") or r.get("refundStatus") or "success").lower()
            if st not in ("success", "1", "true"):
                continue
            v = _f(r.get("refundPrice") if r.get("refundPrice") not in (None, "") else
                   r.get("price") if r.get("price") not in (None, "") else
                   r.get("paidPrice") if r.get("paidPrice") not in (None, "") else r.get("amount"))
            if v is not None:
                found, total = True, total + v

    if isinstance(data.get("refunds"), list):
        found = True
        _sum_list(data["refunds"])
    for it in data.get("itemTransactions") or []:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("refunds"), list):
            found = True
            _sum_list(it["refunds"])
        else:
            v = _f(it.get("refundedPrice") if it.get("refundedPrice") not in (None, "") else it.get("refundPrice"))
            if v is not None:
                found, total = True, total + v
    return round(total, 2) if found else None


def recorded_refund_total(order: dict) -> float:
    tot = 0.0
    for r in order.get("refunds") or []:
        if not isinstance(r, dict) or str(r.get("status") or "done") != "done":
            continue
        if str(r.get("provider") or "iyzico") != "iyzico":
            continue
        v = _f(r.get("net_refund") if r.get("net_refund") not in (None, "") else r.get("amount"))
        tot += v or 0.0
    return round(tot, 2)


async def _lookup(order: dict) -> tuple:
    """(data | None, error) — kimlik bilgisi bu çağrı için YENİDEN okunur."""
    from routes.payment import _get_iyzico_settings, _v2_headers, PAYMENT_DETAIL_PATH
    try:
        settings = await _get_iyzico_settings()
    except Exception:  # noqa: BLE001 — anahtar iptal edildi/silindi → bu siparişte çağrı YAPILMAZ
        return None, "credentials_unavailable"
    payload = {"locale": "tr", "conversationId": str(order.get("id") or ""),
               "paymentId": str(order.get("iyzico_payment_id") or "")}
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = _v2_headers(settings["api_key"], settings["api_secret"], PAYMENT_DETAIL_PATH, body_str)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.post(f"{settings['base_url']}{PAYMENT_DETAIL_PATH}",
                                content=body_str.encode("utf-8"), headers=headers)
    except Exception as e:  # noqa: BLE001
        return None, f"request:{type(e).__name__}"
    if not resp.headers.get("content-type", "").startswith("application/json"):
        return None, f"http_{resp.status_code}_non_json"
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None, "bad_json"
    return (data if isinstance(data, dict) else None), ("" if isinstance(data, dict) else "bad_json")


def compare(order: dict, data: dict) -> list:
    """Sipariş ↔ iyzico yanıtı uyuşmazlıkları: [{"kind", ...}]. Saf fonksiyon."""
    from routes.payment import _is_paid, _payment_matches_order
    out = []
    pid = str(order.get("iyzico_payment_id") or "")
    if str(data.get("status") or "") != "success":
        out.append({"kind": "provider_lookup_failed", "error_code": str(data.get("errorCode") or "")[:20]})
        return out
    if str(data.get("paymentId") or "") and str(data.get("paymentId")) != pid:
        out.append({"kind": "payment_id_mismatch"})
    if not _is_paid(data, strict=True):
        out.append({"kind": "not_paid_at_provider", "provider_status": str(data.get("paymentStatus") or "")[:20]})
    if not _payment_matches_order(data, order):
        out.append({"kind": "amount_or_order_mismatch", "order_total": _f(order.get("total")),
                    "provider_paid": _f(data.get("paidPrice"))})
    snap = order.get("iyzico_retrieve_response") or {}
    rec_paid, prov_paid = _f((snap or {}).get("paidPrice")), _f(data.get("paidPrice"))
    if rec_paid is not None and prov_paid is not None and abs(rec_paid - prov_paid) > 0.02:
        out.append({"kind": "charged_amount_changed", "recorded_paid": rec_paid, "provider_paid": prov_paid})
    prov_ref = provider_refund_total(data)
    rec_ref = recorded_refund_total(order)
    if prov_ref is not None and abs(prov_ref - rec_ref) > 0.02:
        out.append({"kind": "refund_mismatch", "recorded_refunds": rec_ref, "provider_refunds": prov_ref})
    return out


async def run_iyzico_reconciliation(days: int = 3, limit: int = 1000) -> dict:
    """ZAMANLAYICI İŞİ (günlük). Dönüş: özet (kişisel veri yok). Asla istisna fırlatmaz."""
    from routes.deps import db
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=max(1, int(days or 3)))).isoformat()
    summary = {"ok": True, "checked": 0, "matched": 0, "mismatches": 0, "unverified": 0,
               "refund_check_unavailable": 0, "since": since, "at": now.isoformat()}
    try:
        try:
            from routes.payment import _get_iyzico_settings
            await _get_iyzico_settings()
        except Exception:  # noqa: BLE001 — iyzico yapılandırılmamış → yapılacak iş yok
            summary.update(ok=True, skipped="iyzico_not_configured")
            return summary
        q = {"payment_status": {"$in": _PAID_LIKE}, "iyzico_payment_id": {"$nin": [None, ""]},
             "payment_method": {"$in": _CARD_METHODS}, "paid_with_gift_card_only": {"$ne": True},
             "$or": [{"paid_at": {"$gte": since}}, {"created_at": {"$gte": since}}]}
        proj = {"_id": 0, "id": 1, "order_number": 1, "total": 1, "iyzico_payment_id": 1, "refunds": 1,
                "iyzico_retrieve_response.paidPrice": 1, "iyzico_retrieve_response.paymentId": 1,
                "payment_status": 1}
        orders = await db.orders.find(q, proj).limit(limit).to_list(limit)
        # Sınıra ulaşıldıysa pencerede taranmamış sipariş kalmış olabilir → sonuç "tam" sayılmaz
        # (geri yükleme sonrası iade kilidi bu durumda kalkmaz; security_switches._recon_problems).
        summary["truncated"] = len(orders) >= int(limit)
        mismatches = []
        for o in orders:
            summary["checked"] += 1
            data, err = await _lookup(o)
            if data is None:
                summary["unverified"] += 1
                await db.integration_logs.insert_one({
                    "service": "iyzico", "event": "reconciliation_unverified", "status": "unknown",
                    "order_id": o.get("id"), "order_number": o.get("order_number"), "error": err,
                    "created_at": datetime.now(timezone.utc).isoformat()})
                continue
            if provider_refund_total(data) is None:
                summary["refund_check_unavailable"] += 1
            diffs = compare(o, data)
            if not diffs:
                summary["matched"] += 1
                continue
            summary["mismatches"] += 1
            mismatches.append({"order_number": o.get("order_number"), "kinds": [d["kind"] for d in diffs]})
            await db.integration_logs.insert_one({
                "service": "iyzico", "event": "reconciliation_mismatch", "status": "error",
                "order_id": o.get("id"), "order_number": o.get("order_number"),
                "payment_id": o.get("iyzico_payment_id"), "diffs": diffs,
                "created_at": datetime.now(timezone.utc).isoformat()})
        if mismatches:
            try:
                from security.alerts import send_alert
                lines = [f"• {m['order_number']}: {', '.join(m['kinds'])}" for m in mismatches[:20]]
                await send_alert(
                    "iyzico_reconciliation_mismatch",
                    f"iyzico mutabakatı: {len(mismatches)} siparişte uyuşmazlık",
                    "Son günlerin ödenmiş kart siparişleri iyzico kayıtlarıyla karşılaştırıldı:\n\n"
                    + "\n".join(lines)
                    + "\n\nAyrıntı: Entegrasyon Logları (iyzico / reconciliation_mismatch). Sipariş ve ödeme "
                      "durumu otomatik DEĞİŞTİRİLMEDİ; iyzico panelinden doğrulayın.",
                    level="critical", fingerprint=f"iyzico_recon:{now.strftime('%Y-%m-%d')}:{len(mismatches)}",
                    meta={"mismatches": len(mismatches)})
            except Exception as e:  # noqa: BLE001
                logger.warning("iyzico recon alert failed: %s", type(e).__name__)
    except Exception as e:  # noqa: BLE001
        summary.update(ok=False, error=type(e).__name__)
        logger.error("[iyzico-recon] hata: %s", type(e).__name__)
    try:
        await db.settings.update_one({"id": STATE_ID}, {"$set": {"id": STATE_ID, "last": summary}}, upsert=True)
        await db.integration_logs.insert_one({
            "service": "iyzico", "event": "reconciliation_run",
            "status": "success" if summary.get("ok") and not summary["mismatches"] else "error",
            **{k: v for k, v in summary.items() if k not in ("ok",)},
            "created_at": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
    return summary
