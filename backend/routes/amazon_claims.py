"""
AMAZON İADELERİ (RETURNS) → ORTAK İADE / GİDER PUSULASI EKRANI

TASARIM (Hepsiburada köprüsüyle AYNI): Amazon iade talepleri Trendyol/HB ile ortak iade
ekranını kullanır. Kayıtlar db.trendyol_claims koleksiyonuna platform="amazon" ile upsert
edilir; böylece gider pusulası üretimi (/trendyol/claims/{id}/gider-pusulasi), toplu GP,
onay/ret, stok iadesi ve Excel export uçları DEĞİŞİKSİZ çalışır. Liste ucu platform=amazon
ile süzer.

Kaynak: SP-API Reports — GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE (satıcı gönderimli/MFN
iadeler; tab ayrılmış). Sütun adları rapor sürümüne göre değişebildiği için tolere edilir.

GET /integrations/amazon/claims/sync?days_back=30   — manuel/otomatik senkron
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from .deps import db, logger, require_admin, require_permission

router = APIRouter(tags=["Integrations-Amazon-Claims"])

# Amazon return-request-status / resolution → ekran kovalarının beklediği Trendyol statü seti
_AMZ_STATUS_MAP = {
    "completed": "Accepted", "closed": "Accepted", "refunded": "Accepted", "refund": "Accepted",
    "approved": "WaitingInAction",      # iade onaylandı, ürün yolda/bekleniyor
    "pending": "Created", "open": "Created", "new": "Created",
    "declined": "Rejected", "rejected": "Rejected", "denied": "Rejected",
    "cancelled": "Cancelled", "canceled": "Cancelled", "withdrawn": "Cancelled",
}


def _norm_key(h: str) -> str:
    return str(h or "").strip().lower().replace("_", "-").replace(" ", "-")


def _g(row: dict, *keys, default=""):
    """Sütun adı toleranslı okuma: birebir → içeren."""
    for k in keys:
        if k in row and str(row[k]).strip():
            return str(row[k]).strip()
    for k in keys:
        for rk, rv in row.items():
            if k in rk and str(rv).strip():
                return str(rv).strip()
    return default


def _money(v) -> float:
    try:
        return round(float(str(v or "0").replace(",", ".")), 2)
    except Exception:
        return 0.0


def _amz_status(status_raw: str, resolution: str = "") -> str:
    s = _norm_key(status_raw)
    r = _norm_key(resolution)
    if s in _AMZ_STATUS_MAP:
        st = _AMZ_STATUS_MAP[s]
        # "Approved" + iade tutarı çözümü (refund) → kabul edilmiş say
        if st == "WaitingInAction" and ("refund" in r or "iade" in r):
            return "Accepted"
        return st
    for k, v in _AMZ_STATUS_MAP.items():
        if k in s:
            return v
    return "Created"


async def _resolve_amazon_item(seller_sku: str, asin: str, order: dict) -> dict:
    """SellerSKU → Meluni varyantı (barkod/beden/ad). Sıra: amazon_sku_map → sipariş kalemi →
    SKU kalıbı (stokkodu-renk-beden) → ürün stok kodu."""
    out = {"barcode": "", "size": "", "name": "", "unit_price": 0.0, "product_id": ""}
    sku = str(seller_sku or "").strip()
    # 1) Katalog eşleştirmesi (amazon_sku_map)
    if sku:
        m = await db.amazon_sku_map.find_one({"sku": sku}, {"_id": 0, "product_id": 1, "variant_barcode": 1,
                                                          "variant_size": 1, "product_name": 1})
        if m:
            out["barcode"] = str(m.get("variant_barcode") or "")
            out["size"] = str(m.get("variant_size") or "")
            out["name"] = str(m.get("product_name") or "")
            out["product_id"] = str(m.get("product_id") or "")
    # 2) Sipariş kalemi (fiyat/ad)
    for it in (order.get("items") or []):
        if sku and str(it.get("sku") or it.get("barcode") or "") == sku or (asin and str(it.get("asin") or "") == asin):
            out["unit_price"] = float(it.get("unit_price") or it.get("price") or 0)
            out["name"] = out["name"] or str(it.get("product_name") or it.get("name") or "")
            out["barcode"] = out["barcode"] or (str(it.get("barcode") or "") if str(it.get("barcode") or "") != sku else "")
            break
    # 3) SKU kalıbı: STOKKODU-Renk-Beden → ürün + beden
    if not out["barcode"] and sku and "-" in sku:
        parts = sku.split("-")
        stock_code, size = parts[0], parts[-1]
        prod = await db.products.find_one({"$or": [{"stock_code": stock_code}, {"variants.stock_code": stock_code}]},
                                          {"_id": 0, "id": 1, "name": 1, "variants": 1})
        if prod:
            out["name"] = out["name"] or str(prod.get("name") or "")
            out["product_id"] = out["product_id"] or str(prod.get("id") or "")
            for vv in (prod.get("variants") or []):
                if str(vv.get("size") or "").replace("/", "-") == size:
                    out["barcode"] = str(vv.get("barcode") or "")
                    out["size"] = str(vv.get("size") or "")
                    if not out["unit_price"]:
                        out["unit_price"] = float(vv.get("price") or 0)
                    break
    # 4) Barkoddan beden/ad tamamla
    if out["barcode"] and (not out["size"] or not out["name"]):
        prod = await db.products.find_one({"variants.barcode": out["barcode"]}, {"_id": 0, "id": 1, "name": 1, "variants": 1})
        if prod:
            out["name"] = out["name"] or str(prod.get("name") or "")
            out["product_id"] = out["product_id"] or str(prod.get("id") or "")
            for vv in (prod.get("variants") or []):
                if str(vv.get("barcode") or "") == out["barcode"]:
                    out["size"] = out["size"] or str(vv.get("size") or "")
                    break
    return out


async def _amazon_claim_docs(rows: list[dict]) -> list[dict]:
    """Rapor satırlarını (bir iade talebi = 1+ kalem satırı) RMA bazında claim belgelerine çevirir."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        rma = _g(r, "amazon-rma-id", "rma-id", "merchant-rma-id", "return-request-id")
        oid = _g(r, "order-id", "amazon-order-id")
        key = rma or (oid + ":" + _g(r, "return-request-date"))
        if not key.strip(":"):
            continue
        groups.setdefault(key, []).append(r)

    docs = []
    for key, grp in groups.items():
        r0 = grp[0]
        oid = _g(r0, "order-id", "amazon-order-id")
        order = await db.orders.find_one({"order_number": oid, "platform": "amazon"}, {"_id": 0}) or {}
        st = _amz_status(_g(r0, "return-request-status", "status"), _g(r0, "resolution"))
        items, refund = [], 0.0
        for r in grp:
            sku = _g(r, "merchant-sku", "seller-sku", "sku")
            asin = _g(r, "asin")
            qty = int(_money(_g(r, "return-quantity", "quantity", default="1")) or 1)
            res = await _resolve_amazon_item(sku, asin, order)
            unit = res["unit_price"]
            if not unit:
                oa = _money(_g(r, "order-amount"))
                oq = _money(_g(r, "order-quantity", default="1")) or 1
                unit = round(oa / oq, 2) if oa else 0.0
            refunded = _money(_g(r, "refunded-amount", "refund-amount"))
            items.append({
                "claim_item_id": f"{key}:{sku or asin}",
                "productName": res["name"] or _g(r, "item-name", "product-name"),
                "barcode": res["barcode"], "merchantSku": sku, "asin": asin, "size": res["size"],
                "unit_price": unit, "discount_amount": 0, "price": unit, "quantity": qty,
                "reason": _g(r, "return-reason", "reason"),
            })
            refund += (refunded if refunded else unit * qty)
        created_raw = _g(r0, "return-request-date", "return-date", "request-date")
        created_iso = ""
        if created_raw:
            try:
                created_iso = datetime.fromisoformat(created_raw.replace("Z", "+00:00").replace(" ", "T")[:19]).replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                created_iso = created_raw
        ship = order.get("shipping_address") or {}
        cust = f"{ship.get('first_name', '')} {ship.get('last_name', '')}".strip() or str(order.get("customer_name") or "")
        docs.append({
            "claim_id": f"AMZ-{key}",
            "amazon_rma_id": key,
            "platform": "amazon",
            "order_number": oid,
            "claim_type": "RETURN",
            "claim_reason": _g(r0, "return-reason", "reason"),
            "claim_status": st,
            "customer_name": cust,
            "created_date": created_iso or datetime.now(timezone.utc).isoformat(),
            "items": items,
            "refund_amount": round(refund, 2),
            "invoice_number": str(order.get("invoice_number") or _g(r0, "invoice-number") or ""),
            "cargo_tracking_number": _g(r0, "tracking-id", "tracking-number"),
            "cargo_provider_name": _g(r0, "return-carrier", "carrier") or "Amazon",
            "amazon_resolution": _g(r0, "resolution"),
            "amazon_return_type": _g(r0, "return-type"),
            "a_to_z_claim": _g(r0, "a-to-z-claim").lower() in ("y", "yes", "true", "1"),
            "raw_data": {"rows": grp[:20]},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return docs


async def _sync_amazon_claims_core(days_back: int = 60) -> dict:
    """Amazon iade raporu → db.trendyol_claims (platform=amazon) upsert.
    Endpoint + scheduler ortak çekirdeği. HB ile aynı kurallar: manuel kilit (manual_locked)
    durumu ezmez; tutar üzerine yazıldıysa (amount_overridden) tutar/kalemler korunur."""
    try:
        from .amazon_spapi import _get_config
        cfg = await _get_config()
        if not cfg:
            return {"success": False, "error": "Amazon SP-API yapılandırılmadı", "synced": 0}
    except Exception as e:
        return {"success": False, "error": f"Amazon yapılandırması okunamadı: {e}", "synced": 0}
    from .amazon_catalog import fetch_amazon_report_rows
    days = max(1, min(int(days_back or 60), 365))
    start = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0)
    try:
        rows = await fetch_amazon_report_rows(
            "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE",
            {"dataStartTime": start.isoformat().replace("+00:00", "Z")},
            timeout_s=300)
    except Exception as e:
        logger.warning(f"[amazon claims] rapor alınamadı: {e}")
        return {"success": False, "error": str(e)[:300], "synced": 0}
    docs = await _amazon_claim_docs(rows)
    synced = 0
    for doc in docs:
        existing = await db.trendyol_claims.find_one(
            {"claim_id": doc["claim_id"]},
            {"_id": 0, "manual_locked": 1, "amount_overridden": 1, "return_approved_at": 1, "return_rejected_at": 1})
        if existing and existing.get("amount_overridden"):
            doc.pop("refund_amount", None)
            doc.pop("items", None)
        if existing and existing.get("manual_locked"):
            doc.pop("claim_status", None)
        else:
            now_iso = datetime.now(timezone.utc).isoformat()
            if doc.get("claim_status") == "Accepted" and not (existing or {}).get("return_approved_at"):
                doc["return_approved_at"] = now_iso
            if doc.get("claim_status") in ("Rejected", "Cancelled") and not (existing or {}).get("return_rejected_at"):
                doc["return_rejected_at"] = now_iso
        await db.trendyol_claims.update_one(
            {"claim_id": doc["claim_id"]},
            {"$set": doc, "$setOnInsert": {"id": str(_uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
        synced += 1
    try:
        from .integrations_common import apply_accepted_claims_to_orders
        await apply_accepted_claims_to_orders(platforms=["amazon"], dry_run=False)
    except Exception as _ae:
        logger.error(f"[amazon claims->orders return] {_ae}")
    await db.settings.update_one({"id": "amazon_claims_sync_health"}, {"$set": {
        "id": "amazon_claims_sync_health", "at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows), "claims": len(docs), "synced": synced, "days_back": days}}, upsert=True)
    return {"success": True, "synced": synced, "claims": len(docs), "rows": len(rows),
            "message": f"Amazon: {len(rows)} iade satırı, {synced} iade kaydı güncellendi"}


@router.get("/amazon/claims/sync")
async def amazon_claims_sync(days_back: int = Query(30, ge=1, le=365), current_user: dict = Depends(require_permission("returns.view"))):
    return await _sync_amazon_claims_core(days_back=days_back)


@router.get("/amazon/claims/health")
async def amazon_claims_health(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    return await db.settings.find_one({"id": "amazon_claims_sync_health"}, {"_id": 0}) or {"status": "never"}
