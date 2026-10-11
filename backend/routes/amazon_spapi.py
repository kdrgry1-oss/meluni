"""
Amazon Selling Partner API (SP-API) entegrasyonu.

ÖNEMLİ: SP-API artık AWS IAM / SigV4 GEREKTİRMİYOR (2 Ekim 2023'ten beri).
Sadece LWA (Login with Amazon) access token yeterli:
  refresh_token -> access_token (https://api.amazon.com/auth/o2/token)
  SP-API çağrılarında header: x-amz-access-token: <access_token>

Kimlik bilgileri (client_secret, refresh_token) AES şifreli vault'ta saklanır.
Config (client_id, marketplace, region) `integration_settings` koleksiyonunda.

Endpoints (hepsi /api prefix + require_admin, public callback hariç):
  GET    /api/amazon/spapi/status         -> bağlantı durumu
  POST   /api/amazon/spapi/config         -> kimlik bilgilerini kaydet
  POST   /api/amazon/spapi/test           -> token üret + marketplaceParticipations ile doğrula
  GET    /api/amazon/spapi/orders         -> son siparişleri çek (örnek)
  GET    /api/amazon/spapi/authorize-url  -> OAuth consent URL (website workflow)
  GET    /api/amazon/spapi/oauth/callback -> spapi_oauth_code -> refresh_token (public)
"""
import asyncio
import os
import re
import time
import secrets as _secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Body, UploadFile, File
from fastapi.responses import RedirectResponse

from .deps import db, logger, require_admin, require_permission
from .amazon_helpers import (
    amazon_image_candidates,
    amazon_sibling_query,
    build_amazon_seller_sku,
    pick_existing_amazon_sku,
    canonical_amazon_color,
    clean_amazon_text,
    infer_amazon_fabric,
)

router = APIRouter(prefix="/amazon/spapi", tags=["Amazon SP-API"])

LWA_TOKEN_ENDPOINT = "https://api.amazon.com/auth/o2/token"
CONFIG_KEY = "amazon_spapi"

# Bölge endpoint'leri (SP-API)
SPAPI_REGIONS = {
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "na": "https://sellingpartnerapi-na.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}
# Türkiye marketplace -> EU region
DEFAULT_MARKETPLACE_ID = "A33AVAJ2PDY3EV"  # Amazon Türkiye
DEFAULT_REGION = "eu"

# Seller Central consent domain (bölge/ülkeye göre)
SELLERCENTRAL_CONSENT = {
    "eu": "https://sellercentral.amazon.com.tr",
    "na": "https://sellercentral.amazon.com",
    "fe": "https://sellercentral-japan.amazon.com",
}


# ── İlk Faz PII/Restricted guard (Amazon DPP §1) ────────────────────────────
# Restricted Role alınana kadar PII döndüren / RDT gerektiren yollar KAPALI.
# Açmak için: env AMAZON_ALLOW_RESTRICTED=1 (Restricted Role onaylanınca).
RESTRICTED_ALLOWED = os.environ.get("AMAZON_ALLOW_RESTRICTED", "0") == "1"
# PII döndürebilen / RDT gerektiren SP-API yol işaretleri — flag kapalıyken çağrı ENGELLENİR.
_RESTRICTED_PATH_MARKERS = ("/buyerinfo", "buyerinfo", "/address", "shippingaddress",
                            "/tokens", "restricted")
# Yanıtta BEKLENMEDİK şekilde gelirse tamamen SÖKÜLECEK PII container anahtarları
# (defense-in-depth: ilk fazda PII beklenmez; gelirse saklanmadan/loglanmadan atılır).
_PII_KEYS = {
    "buyerinfo", "buyeremail", "buyername", "buyercompanyname",
    "buyertaxinfo", "buyertaxinformation", "shippingaddress", "billingaddress",
    "defaultshipfromlocationaddress", "buyercustomizedinformation",
}


def _assert_restricted_allowed(path: str) -> None:
    """Restricted/PII yol çağrısını flag kapalıyken 403 ile engeller (§1)."""
    if RESTRICTED_ALLOWED:
        return
    low = (path or "").lower()
    if any(m in low for m in _RESTRICTED_PATH_MARKERS):
        raise HTTPException(
            status_code=403,
            detail="Restricted/PII SP-API yolu ilk fazda kapalıdır (AMAZON_ALLOW_RESTRICTED=0).",
        )


def _scrub_pii(obj):
    """Yanıttan beklenmedik PII container'larını özyinelemeli SÖKER (§1). Değer saklanmaz."""
    if isinstance(obj, dict):
        return {k: _scrub_pii(v) for k, v in obj.items() if str(k).lower() not in _PII_KEYS}
    if isinstance(obj, list):
        return [_scrub_pii(x) for x in obj]
    return obj


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


async def _log_spapi_call(action: str, status: int = None, ok: bool = None, actor: str = "") -> None:
    """SP-API çağrı denetim logu (Amazon DPP: SP-API çağrıları merkezi loglanır).
    PII İÇERMEZ — yalnız işlem tipi/path, HTTP durum, zaman, (varsa) admin e-postası.
    İstek parametreleri/gövdesi ve alıcı bilgisi LOGLANMAZ."""
    try:
        await db.spapi_call_logs.insert_one({
            "action": action, "status": status, "ok": ok,
            "actor": actor or "", "at": _now_iso(),
        })
    except Exception:
        pass


async def _get_config(include_secrets: bool = False) -> Optional[dict]:
    doc = await db.integration_settings.find_one({"key": CONFIG_KEY}, {"_id": 0})
    if not doc:
        return None
    if include_secrets:
        from security.crypto import decrypt
        doc["client_secret"] = decrypt(doc.get("client_secret_enc")) if doc.get("client_secret_enc") else None
        doc["refresh_token"] = decrypt(doc.get("refresh_token_enc")) if doc.get("refresh_token_enc") else None
    return doc


async def _exchange_refresh_for_access(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """LWA refresh_token -> access_token."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            LWA_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = r.json()
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"LWA token hatası: {data.get('error_description') or data}")
        return data


async def _exchange_code_for_refresh(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """LWA authorization_code -> refresh_token (+access_token)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            LWA_TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = r.json()
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"LWA code exchange hatası: {data.get('error_description') or data}")
        return data


async def get_valid_access_token() -> tuple[str, str, str]:
    """Geçerli (gerekirse yenilenmiş) access token döndürür.
    Dönüş: (access_token, endpoint, marketplace_id). Diğer modüller bunu kullanır."""
    cfg = await _get_config(include_secrets=True)
    if not cfg or not cfg.get("refresh_token") or not cfg.get("client_id") or not cfg.get("client_secret"):
        raise HTTPException(status_code=400, detail="Amazon SP-API yapılandırılmamış (refresh token eksik)")

    # Önbellekteki erişim anahtarı, onu üreten kimlik bilgilerine BAĞLI: istemci sırrı / refresh
    # token değişince (rotasyon, sızıntı sonrası) eski anahtar 1 saat daha kullanılmaz.
    import hashlib as _hl_amz
    _cred_fp = _hl_amz.sha256(f"{cfg['client_id']}|{cfg['client_secret']}|{cfg['refresh_token']}"
                              .encode()).hexdigest()[:24]
    cached = cfg.get("access_token")
    expires_at = cfg.get("access_expires_at") or 0
    if cached and expires_at > int(time.time()) + 60 and cfg.get("access_token_cred_fp") == _cred_fp:
        access_token = cached
    else:
        td = await _exchange_refresh_for_access(cfg["client_id"], cfg["client_secret"], cfg["refresh_token"])
        access_token = td["access_token"]
        expires_at = int(time.time()) + int(td.get("expires_in", 3600))
        await db.integration_settings.update_one(
            {"key": CONFIG_KEY},
            {"$set": {"access_token": access_token, "access_expires_at": expires_at,
                      "access_token_cred_fp": _cred_fp, "updated_at": _now_iso()}},
        )
    region = cfg.get("region") or DEFAULT_REGION
    endpoint = SPAPI_REGIONS.get(region, SPAPI_REGIONS[DEFAULT_REGION])
    return access_token, endpoint, cfg.get("marketplace_id") or DEFAULT_MARKETPLACE_ID


# İlk faz: Amazon'a YAZMA varsayılan KAPALI (dry-run). Restricted-flag pattern'iyle aynı.
# Açmak için: env AMAZON_ALLOW_WRITE=1 (canlı test SKU'su ile doğrulanınca). Rollback: =0.
ALLOW_WRITE = os.environ.get("AMAZON_ALLOW_WRITE", "0") == "1"


async def _spapi_send(method: str, path: str, body: dict = None, params: dict = None) -> dict:
    """PATCH/PUT/POST yazma çağrısı. ALLOW_WRITE KAPALIYSA Amazon'a GİTMEZ (dry-run):
    ne gönderileceğini döndürür → SKU/payload'ı canlı yazmadan doğrulayabilirsiniz.
    Açıkken gerçek çağrı yapılır. PII yol koruması (§1) yine geçerli; gövde loglanmaz."""
    _assert_restricted_allowed(path)
    if not ALLOW_WRITE:
        await _log_spapi_call(f"DRYRUN {method} {path}", None, True)
        return {"status": 0, "ok": True, "dry_run": True,
                "would_send": {"method": method, "path": path, "params": params or {}, "body": body}}
    access_token, endpoint, _ = await get_valid_access_token()
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.request(
            method, f"{endpoint}{path}", params=params or {}, json=body,
            headers={
                "x-amz-access-token": access_token,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:500]}
        data = _scrub_pii(data)  # §1: beklenmedik PII saklanmadan sökülür
        _ok = 200 <= r.status_code < 300
        await _log_spapi_call(f"{method} {path}", r.status_code, _ok)  # gövde loglanmaz
        return {"status": r.status_code, "ok": _ok, "data": data}


async def _require_seller_id() -> str:
    cfg = await _get_config()
    seller = (cfg or {}).get("selling_partner_id")
    if not seller:
        raise HTTPException(status_code=400,
                            detail="selling_partner_id yok — önce OAuth ile bağlanın (consent akışı)")
    return seller


async def _spapi_get(path: str, params: dict = None) -> dict:
    # §1: Restricted/PII yol ise (flag kapalı) çağrıyı hiç yapma.
    _assert_restricted_allowed(path)
    access_token, endpoint, _ = await get_valid_access_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{endpoint}{path}",
            params=params or {},
            headers={
                "x-amz-access-token": access_token,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:500]}
        # §1 defense-in-depth: beklenmedik PII container'ları saklanmadan/loglanmadan SÖKÜLÜR.
        data = _scrub_pii(data)
        _ok = 200 <= r.status_code < 300
        # PII'siz çağrı logu (yalnız path + durum) — parametreler/gövde loglanmaz.
        await _log_spapi_call(f"GET {path}", r.status_code, _ok)
        return {"status": r.status_code, "ok": _ok, "data": data}


# ============================== SİPARİŞ İÇE-AKTARIM (panele düşürme) ==============================
# NOT: Bu yol Amazon siparişlerini Meluni paneline (db.orders) YAZAR — Trendyol/Hepsiburada
# ile AYNI pazaryeri deseni. Pazaryeri siparişi ZATEN ödemeyi almış DIŞ sipariştir; panele
# payment_method="marketplace", payment_status="paid", status="confirmed" (Amazon işlem
# durumuna göre) düşer. Müşteri-yönlü iyzico ödeme akışı (CLAUDE.md değişmezleri) bundan
# TAMAMEN AYRIDIR ve burada DEĞİŞMEZ. Alıcı adı/adresi (PII) yalnız Restricted Role onaylı +
# AMAZON_ALLOW_RESTRICTED=1 iken RDT ile çekilir; kapalıyken sipariş kabuğu (PII'siz) düşer.


from .marketplace_order_mapping import amazon_internal_status


def _amz_status_of(status_raw: str) -> str:
    """Amazon OrderStatus -> Meluni operasyonel durumu.
    Yalnız açık allowlist satış sayılır; bekleyen/bilinmeyen durumlar pending kalır.
    (Amazon Orders API 'Delivered' döndürmez; teslim ayrı izlenir.)"""
    return amazon_internal_status(status_raw)[0]


async def _get_restricted_data_token(order_id: str) -> Optional[str]:
    """Sipariş PII'si (buyerInfo + shippingAddress) için Restricted Data Token (RDT) alır.
    AMAZON_ALLOW_RESTRICTED=0 iken None döner → PII çekilmez (yalnız sipariş kabuğu).
    Amazon Restricted Role onayı + env flag gerekir."""
    if not RESTRICTED_ALLOWED:
        return None
    try:
        access_token, endpoint, _ = await get_valid_access_token()
        body = {"restrictedResources": [{
            "method": "GET",
            "path": f"/orders/v0/orders/{order_id}",
            "dataElements": ["buyerInfo", "shippingAddress"],
        }]}
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{endpoint}/tokens/2021-03-01/restrictedDataToken",
                json=body,
                headers={"x-amz-access-token": access_token,
                         "content-type": "application/json", "accept": "application/json"},
            )
        await _log_spapi_call("POST /tokens/restrictedDataToken", r.status_code, 200 <= r.status_code < 300)
        if r.status_code // 100 != 2:
            return None
        return (r.json() or {}).get("restrictedDataToken")
    except Exception:
        return None


async def _fetch_amazon_order_full(order_id: str) -> dict:
    """Tek siparişi çeker. RDT alınabildiyse (Restricted açık) yanıt alıcı adı/adresini
    İÇERİR ve KORUNUR; RDT yoksa Amazon PII döndürmez → defense-in-depth scrub uygulanır.
    Döner: Orders API 'payload' (tek sipariş objesi) veya {}."""
    access_token, endpoint, _ = await get_valid_access_token()
    rdt = await _get_restricted_data_token(order_id)
    token = rdt or access_token
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{endpoint}/orders/v0/orders/{order_id}",
                headers={"x-amz-access-token": token, "accept": "application/json"},
            )
        _ok = 200 <= r.status_code < 300
        await _log_spapi_call(f"GET /orders/v0/orders/{order_id}", r.status_code, _ok)
        try:
            payload = (r.json() or {}).get("payload") or {}
        except Exception:
            payload = {}
        if not rdt:
            # RDT yoksa beklenmedik PII saklanmadan/loglanmadan SÖKÜLÜR (§1).
            payload = _scrub_pii(payload)
        return payload
    except Exception:
        return {}


async def _fetch_amazon_order_items(order_id: str) -> list:
    """Sipariş kalemleri (SellerSKU/ASIN/adet/fiyat). PII'siz — RDT gerekmez."""
    items, next_token = [], None
    for _ in range(10):
        params = {"NextToken": next_token} if next_token else {}
        res = await _spapi_get(f"/orders/v0/orders/{order_id}/orderItems", params)
        if not res["ok"]:
            break
        payload = res["data"].get("payload") or {}
        items.extend(payload.get("OrderItems") or [])
        next_token = payload.get("NextToken")
        if not next_token:
            break
    return items


def _amz_addr(node: dict, fallback_name: str, email: str) -> dict:
    """Amazon ShippingAddress -> Meluni adres bloğu. Ad 'Name' tek alandır → ilk/soyad ayrılır."""
    node = node or {}
    full_name = (node.get("Name") or fallback_name or "").strip()
    # SON kelime = soyad, öncekiler = ad ("Mehmet Ali Kaya" → ad="Mehmet Ali", soyad="Kaya").
    # İsim hiç yoksa (PII kapalı/gelmedi) placeholder.
    if full_name:
        parts = full_name.rsplit(" ", 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
    else:
        first, last = "Amazon", "Müşterisi"
    addr_line = " ".join([x for x in [node.get("AddressLine1"), node.get("AddressLine2"),
                                      node.get("AddressLine3")] if x]).strip()
    return {
        "first_name": first,
        "last_name": last,
        "phone": node.get("Phone", "") or "",
        "email": email or "",
        "address": addr_line,
        "city": node.get("City", "") or "",
        "district": node.get("County") or node.get("District") or node.get("StateOrRegion", "") or "",
        "country": node.get("CountryCode", "") or "",
        "postal_code": node.get("PostalCode", "") or "",
    }


def map_amazon_order(o: dict, items: list) -> dict:
    """Amazon Orders API sipariş + kalemlerini Meluni db.orders şemasına eşler
    (map_trendyol_order deseniyle hizalı)."""
    from datetime import datetime, timezone
    order_id = o.get("AmazonOrderId")
    total_node = o.get("OrderTotal") or {}
    buyer = o.get("BuyerInfo") or {}
    ship = o.get("ShippingAddress") or {}
    buyer_email = buyer.get("BuyerEmail", "") or ""
    buyer_name = buyer.get("BuyerName", "") or ""

    mapped_items, subtotal = [], 0.0
    for it in (items or []):
        try:
            qty = int(it.get("QuantityOrdered") or 1) or 1
        except Exception:
            qty = 1
        try:
            line_amt = float((it.get("ItemPrice") or {}).get("Amount") or 0)
        except Exception:
            line_amt = 0.0
        unit = round(line_amt / qty, 2) if qty else line_amt
        sku = it.get("SellerSKU") or ""
        mapped_items.append({
            "product_id": sku,
            "product_name": it.get("Title", "") or "",
            "quantity": qty,
            "unit_price": unit,
            "discount_amount": 0,
            "price": unit,
            "size": "",
            "color": "",
            "barcode": sku,   # Meluni stok eşlemesi variants.barcode ile yapılır
            "sku": sku,
            "asin": it.get("ASIN", "") or "",
            "currency": (it.get("ItemPrice") or {}).get("CurrencyCode") or "TRY",
        })
        subtotal += line_amt

    try:
        total_amt = float(total_node.get("Amount") or 0) or round(subtotal, 2)
    except Exception:
        total_amt = round(subtotal, 2)

    # Teslimat adı = kargo adresindeki ALICI adı (ShippingAddress.Name). Fatura adı = SİPARİŞ
    # VEREN (BuyerInfo.BuyerName) — ikisi farklı olabilir (hediye/başkası adına gönderim).
    ship_addr = _amz_addr(ship, buyer_name, buyer_email)
    bill_addr = dict(ship_addr)
    if buyer_name:
        _bp = buyer_name.rsplit(" ", 1)
        bill_addr["first_name"] = _bp[0]
        bill_addr["last_name"] = (_bp[1] if len(_bp) > 1 else "")
    if buyer_email:
        bill_addr["email"] = buyer_email
    status_raw = o.get("OrderStatus") or ""
    _status, _payment_status = amazon_internal_status(status_raw)

    _doc = {
        "order_number": str(order_id),
        "platform": "amazon",
        "amazon_order_id": order_id,
        "user_id": None,
        "items": mapped_items,
        "shipping_address": ship_addr,
        "billing_address": {**bill_addr, "company_name": "", "tax_number": "",
                            "tax_office": "", "is_corporate": False},
        "billing_info": {"is_corporate": False, "company_name": "", "tax_number": "",
                         "tax_office": "", "e_invoice_user": False},
        "subtotal": round(subtotal, 2) if subtotal else total_amt,
        "shipping_cost": 0,
        "discount_amount": 0,
        "total": total_amt,
        "payment_method": "marketplace",
        "payment_status": _payment_status,
        "status": _status,
        "marketplace_status": status_raw,
        "fulfillment_channel": o.get("FulfillmentChannel", "") or "",  # AFN=FBA(Amazon kargolar) / MFN=satıcı
        "is_prime": bool(o.get("IsPrime")),
        "is_business_order": bool(o.get("IsBusinessOrder")),
        "sales_channel": o.get("SalesChannel", "") or "",
        "amazon_currency": total_node.get("CurrencyCode", "") or "",
        "marketplace_order_date": o.get("PurchaseDate", "") or "",
        "marketplace_last_modified": o.get("LastUpdateDate", "") or "",
        "cargo_tracking_number": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # KVKK: (bugün boş gelse de) fatura kimlik no alanları mühürlenerek yazılır — tek kural.
    from security.pii import seal_doc_tax_ids as _pii_seal
    return _pii_seal(_doc)


async def _amazon_barcodes_for_asin(asin: str) -> list:
    """ASIN → ürünün harici barkodları (EAN/UPC/GTIN) — Catalog Items API 2022-04-01.
    Amazon'a MANUEL listelenen ürünlerde SellerSKU keyfi olabilir; ama listelemede verilen
    harici ürün barkodu (EAN/UPC) Meluni variants.barcode ile eşleşir. Sonuç ASIN bazında
    cache'lenir (amazon_asin_cache) — tekrar çağrı yapılmaz."""
    asin = (asin or "").strip()
    if not asin:
        return []
    try:
        cached = await db.amazon_asin_cache.find_one({"asin": asin}, {"_id": 0, "barcodes": 1})
        if cached and isinstance(cached.get("barcodes"), list):
            return cached["barcodes"]
    except Exception:
        pass
    barcodes = []
    try:
        _, _, mp = await get_valid_access_token()
        res = await _spapi_get(f"/catalog/2022-04-01/items/{asin}",
                               {"marketplaceIds": mp, "includedData": "identifiers"})
        if res["ok"]:
            for block in ((res["data"] or {}).get("identifiers") or []):
                for idv in (block.get("identifiers") or []):
                    t = (idv.get("identifierType") or "").upper()
                    if t in ("EAN", "UPC", "GTIN", "GTIN13", "ISBN", "JAN", "MPN"):
                        v = idv.get("identifier")
                        if v:
                            barcodes.append(str(v).strip())
        barcodes = list(dict.fromkeys([b for b in barcodes if b]))
        await db.amazon_asin_cache.update_one(
            {"asin": asin},
            {"$set": {"asin": asin, "barcodes": barcodes, "updated_at": _now_iso()}},
            upsert=True)
    except Exception as _e:
        logger.error(f"[amazon] catalog barcode ({asin}): {_e}")
    return barcodes


async def _amazon_enrich_items(order_data: dict) -> dict:
    """Amazon sipariş kalemlerini Meluni ürünleriyle eşler. SIRA:
    1) SellerSKU (barcode/sku/product_id alanlarında) → Meluni kod eşleşmesi (stok_kodu/barkod).
    2) Eşleşmezse ASIN → harici barkod (EAN/UPC, Catalog Items) → Meluni variants.barcode.
    Eşleşen kalemde: görsel + gerçek product_id + Meluni barkodu (stok düşümü için) + beden/renk.
    Eşleşmeyen kalem 'matched:False' kalır (stok düşmez, log'da görünür)."""
    from .integrations_common import _meluni_match_for_codes, _meluni_product_image
    for it in (order_data.get("items") or []):
        codes = [it.get("barcode"), it.get("sku"), it.get("product_id")]
        m = await _meluni_match_for_codes([c for c in codes if c])
        if not m and it.get("asin"):
            bcs = await _amazon_barcodes_for_asin(it.get("asin"))
            if bcs:
                it["amazon_barcodes"] = bcs
                m = await _meluni_match_for_codes(bcs)
        if not m:
            it["matched"] = False
            continue
        prod, matched_code, how = m
        vbar = ""
        for v in (prod.get("variants") or []):
            if matched_code in (v.get("barcode"), v.get("stock_code")):
                vbar = v.get("barcode") or vbar
                if v.get("size") and not it.get("size"):
                    it["size"] = v.get("size")
                if v.get("color") and not it.get("color"):
                    it["color"] = v.get("color")
                break
        it["marketplace_sku"] = it.get("sku") or it.get("product_id")
        it["product_id"] = prod.get("id")
        it["meluni_product_id"] = prod.get("id")
        # KRİTİK: stok düşümü variants.barcode ile yapılır → eşleşen gerçek barkodu yaz.
        it["barcode"] = vbar or prod.get("barcode") or matched_code or it.get("barcode")
        img = _meluni_product_image(prod)
        if img:
            it["image"] = img
        if prod.get("name"):
            it["product_name"] = prod["name"]
        it["matched"] = True
        it["match_method"] = how
    return order_data


async def _amazon_push_stock_price(sku: str, quantity: int, price=None, currency: str = "TRY",
                                   product_type: str = None) -> dict:
    """Tek Amazon SKU'su için stok (+opsiyonel fiyat) PATCH'ler — Listings Items 2021-08-01,
    tek çağrıda fulfillment_availability + purchasable_offer. AMAZON_ALLOW_WRITE=0 iken dry-run
    (Amazon'a GİTMEZ, would_send döner). Stok/fiyat senkron job'ı bunu kullanır.
    DENETİM (stok-sync #3): productType ARTIK gerçek tipten gelir; sabit 'PRODUCT' apparel'de
    Amazon'ca INVALID dönüp PATCH uygulanmıyordu (sessiz oversell)."""
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    try:
        qty = max(0, int(quantity))
    except Exception:
        qty = 0
    patches = [{
        "op": "replace",
        "path": "/attributes/fulfillment_availability",
        "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": qty}],
    }]
    try:
        _pr = round(float(price), 2) if price is not None else 0
    except Exception:
        _pr = 0
    if _pr > 0:
        patches.append({
            "op": "replace",
            "path": "/attributes/purchasable_offer",
            "value": [{"marketplace_id": mp, "currency": (currency or "TRY").upper(),
                       "audience": "ALL",
                       "our_price": [{"schedule": [{"value_with_tax": _pr}]}]}],
        })
    body = {"productType": (product_type or "PRODUCT"), "patches": patches}
    res = await _spapi_send("PATCH", f"/listings/2021-08-01/items/{seller}/{sku}",
                            body=body, params={"marketplaceIds": mp})
    # DENETİM (stok-sync #2): Listings PATCH 200 dönse bile gövde status'u ACCEPTED değilse
    # (INVALID) BAŞARI SAYMA — aksi halde amazon_sku_state ilerleyip SKU sonsuza dek atlanır
    # (sessiz stok drifti/oversell). dry-run hariç.
    if res.get("ok") and not res.get("dry_run"):
        _st = str(((res.get("data") or {}).get("status") or "")).upper()
        if _st and _st not in ("ACCEPTED", "VALID"):
            res["ok"] = False
            res["reject_status"] = _st
    return res


# ============================== ÜRÜN LİSTELEME (Amazon'a aktarma) ==============================
# Amazon'da "kategori" yerine PRODUCT TYPE vardır (Product Type Definitions API). Meluni
# kategorisi → Amazon productType eşlemesi category_mappings (marketplace="amazon-tr") içinde
# default_mappings.product_type olarak saklanır. Listeleme: Listings Items 2021-08-01 PUT.
# ALLOW_WRITE=0 iken dry-run (would_send) — canlı yazma AMAZON_ALLOW_WRITE=1 ile.

async def _amazon_search_product_types(keywords: str = "") -> list:
    """Amazon productType arama (Definitions API). Kategori sayfasında 'kategori' seçimi için."""
    _, _, mp = await get_valid_access_token()
    params = {"marketplaceIds": mp}
    if keywords:
        params["keywords"] = keywords
    res = await _spapi_get("/definitions/2020-09-01/productTypes", params)
    out = []
    if res["ok"]:
        for pt in ((res["data"] or {}).get("productTypes") or []):
            out.append({"name": pt.get("name"), "displayName": pt.get("displayName") or pt.get("name")})
    return out


_AMZ_ENUM_EXCLUDE = {"language_tag", "marketplace_id", "currency", "audience",
                     "unit_of_measure", "region", "country_code"}


def _amz_enum_values(node: dict) -> list:
    """Bir attribute şema düğümünden izin verilen değerleri (enum + enumNames) çıkarır.
    Amazon şeması enum'u farklı derinliklerde tutabilir: items.properties.value.enum,
    doğrudan node.enum, node.properties.*.enum veya daha derinde. Döner [{value,label}].
    language_tag/marketplace_id gibi gerçek-değer OLMAYAN enum'lar atlanır."""
    def _pairs(sub):
        en = sub.get("enum") or []
        names = sub.get("enumNames") or en
        return [{"value": str(e), "label": str(n)} for e, n in zip(en, names)][:3000]

    def _from_props(props):
        if not isinstance(props, dict):
            return []
        for key in ("value", "unit", "type", "name"):  # 'value' asıl değer taşıyıcı
            sub = props.get(key)
            if isinstance(sub, dict) and sub.get("enum"):
                return _pairs(sub)
        for k, sub in props.items():
            if k in _AMZ_ENUM_EXCLUDE:
                continue
            if isinstance(sub, dict) and sub.get("enum"):
                return _pairs(sub)
        return []

    try:
        if not isinstance(node, dict):
            return []
        if node.get("enum"):
            return _pairs(node)
        items = node.get("items") or {}
        r = _from_props(items.get("properties") or {})
        if r:
            return r
        r = _from_props(node.get("properties") or {})
        if r:
            return r
        # Sınırlı derinlikte tarama — value taşıyan enum'u bul (exclude anahtarları atla).
        found = []

        def _scan(n, depth=0):
            if depth > 6 or found:
                return
            if isinstance(n, dict):
                if n.get("enum"):
                    found.extend(_pairs(n))
                    return
                for k, v in n.items():
                    if k in _AMZ_ENUM_EXCLUDE:
                        continue
                    _scan(v, depth + 1)
            elif isinstance(n, list):
                for x in n:
                    _scan(x, depth + 1)

        _scan(items or node)
        return found
    except Exception:
        return []


async def _amazon_product_type_schema(product_type: str) -> dict:
    """productType'ın LISTING zorunlu/opsiyonel attribute şemasını + izin verilen değerleri
    çeker + cache'ler (amazon_pt_schema). Kategori sayfası 'gelişmiş özellikler' için."""
    product_type = (product_type or "").strip()
    if not product_type:
        return {}
    try:
        cached = await db.amazon_pt_schema.find_one({"product_type": product_type}, {"_id": 0})
        if cached and cached.get("required") is not None and cached.get("_schema_v") == 4:
            return cached
    except Exception:
        pass
    _, _, mp = await get_valid_access_token()
    res = await _spapi_get(f"/definitions/2020-09-01/productTypes/{product_type}",
                           {"marketplaceIds": mp, "requirements": "LISTING", "locale": "tr_TR"})
    required, optional = [], []
    attributes_values = {}
    titles = {}
    if res["ok"]:
        schema_node = ((res["data"] or {}).get("schema") or {})
        props = schema_node.get("properties") or {}
        req_set = set(schema_node.get("required") or [])
        # Amazon şema çoğunlukla harici (imzalı) link'te döner — inline değilse onu çek.
        link = (schema_node.get("link") or {}).get("resource")
        if not props and link:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    r = await client.get(link)
                if r.status_code == 200:
                    js = r.json()
                    props = js.get("properties") or {}
                    req_set = set(js.get("required") or [])
            except Exception:
                pass
        for k in props.keys():
            (required if k in req_set else optional).append(k)
            _node = props.get(k) or {}
            _t = _node.get("title")
            if _t:
                titles[k] = _t
            _subp = ((_node.get("items") or {}).get("properties")) or _node.get("properties") or {}
            for _s, _sv in _subp.items():
                _st = (_sv or {}).get("title")
                if _st:
                    titles[f"{k}.{_s}"] = _st
        if not props:
            required = list(req_set)
        # Her attribute için izin verilen değerler (enum) — açılır liste için.
        for k, node in props.items():
            vals = _amz_enum_values(node)
            if vals:
                attributes_values[k] = vals
    doc = {"product_type": product_type, "required": required, "optional": optional,
           "values": attributes_values, "titles": titles if res["ok"] else {},
           "_schema_v": 4, "updated_at": _now_iso()}
    try:
        await db.amazon_pt_schema.update_one({"product_type": product_type},
                                             {"$set": doc}, upsert=True)
    except Exception:
        pass
    return doc


def _amazon_image_url(u: str) -> str:
    """Amazon WebP KABUL ETMEZ (yalnız JPEG/PNG/TIFF/GIF). Ürün görsellerimiz R2'de
    WebP → Amazon sessizce düşürüyordu. cdn.meluni.com.tr veya .webp uzantılı görselleri
    backend JPEG proxy'sine (/api/upload/to-jpeg) yönlendir; zaten JPEG/PNG olanlar kalır."""
    try:
        from urllib.parse import urlparse, quote
        pu = urlparse(u)
        host = pu.hostname or ""
        is_webp = pu.path.lower().endswith(".webp")
        if host == "cdn.meluni.com.tr" or is_webp:
            # Amazon ERROR 20015 ("dosya türü desteklenmiyor"): uzantısız ?src= URL'i reddediliyordu →
            # .jpg UZANTILI proxy yolu (/api/upload/jpeg/<b64>.jpg). İçerik yine JPEG'e çevrilir.
            import base64
            base = _public_base() or "https://api.meluni.com.tr"
            tok = base64.urlsafe_b64encode(u.encode("utf-8")).decode("ascii").rstrip("=")
            return f"{base}/api/upload/jpeg/{tok}.jpg"
    except Exception:
        pass
    return u


def _product_images(product: dict, variant: dict = None) -> list:
    # Varyant/rengin kendi gorseli varsa Amazon child listing'inde ana gorsel odur.
    # Urun gorselleri kalan slotlari doldurur (Amazon: main + en fazla 8 other).
    return [_amazon_image_url(u) for u in amazon_image_candidates(product, variant)]


def _amazon_color_of(product: dict, variant: dict = None) -> str:
    """Tek kanonik renk cozucu: varyant/urun/attributes ve son olarak urun adi."""
    fallback = ""
    try:
        from .products import _resolve_prod_color
        fallback = _resolve_prod_color(product or {})
    except Exception:
        pass
    return canonical_amazon_color(product or {}, variant or {}, fallback)


def _pick_variation_theme(enum, has_color: bool, has_size: bool) -> str:
    """productType şemasındaki geçerli variation_theme enum'undan (renk+beden / renk / beden)
    uygun olanı seçer. Eskiden 'SIZE_NAME/COLOR_NAME' gibi GEÇERSİZ sabitler gönderiliyordu →
    Amazon varyasyon ailesini reddediyor, bedenler/renkler düşüyordu. Enum boşsa kanonik
    değerlere (SIZE/COLOR, COLOR, SIZE) düşer."""
    # _amazon_product_type_schema enumlari arayuz icin {value,label} cifti olarak tutar.
    # Dict'i str() yapmak Amazon'a "{'value': ...}" gonderiyordu; Amazon bunu 90244 ile
    # reddediyordu. Yalnizca ham `value` gonderilir.
    enum = [str(v.get("value") if isinstance(v, dict) else v) for v in (enum or []) if v]

    def _u(v):
        return v.upper()

    if has_color and has_size:
        # Sadece gercekte gonderdigimiz color + size alanlarina dayanan en yalın temayi sec.
        # BAND_COLOR gibi baska bir attribute isteyen temalar ilk sirada olsa da secilmez.
        for preferred in ("COLOR_NAME/SIZE_NAME", "SIZE_NAME/COLOR_NAME",
                          "COLOR/SIZE", "SIZE/COLOR"):
            for v in enum:
                if _u(v) == preferred:
                    return v
        for v in enum:
            vu = _u(v)
            if "COLOR" in vu and "SIZE" in vu and "BAND_COLOR" not in vu:
                return v
        return enum[0] if enum else "COLOR/SIZE"
    if has_color:
        # Önce TEK boyutlu tam eşleşme; "FLAVOR/COLOR" gibi başka alan isteyen temalar seçilmez.
        for preferred in ("COLOR_NAME", "COLOR"):
            for v in enum:
                if _u(v) == preferred:
                    return v
        for v in enum:
            if "COLOR" in _u(v) and "SIZE" not in _u(v) and "/" not in v:
                return v
        return "COLOR" if not enum else enum[0]
    # Yalnız beden: "FLAVOR/SIZE" (Aroma/Boyut) seçilirse Amazon 90244 + 'Aroma gerekli' veriyordu.
    for preferred in ("SIZE_NAME", "SIZE"):
        for v in enum:
            if _u(v) == preferred:
                return v
    for v in enum:
        if "SIZE" in _u(v) and "COLOR" not in _u(v) and "/" not in v:
            return v
    return "SIZE" if not enum else enum[0]


async def _amazon_markup() -> float:
    """Amazon kâr marjı (%) — Amazon config'ten (integration_settings.amazon_spapi.markup).
    Set edilmemişse 0 (marjsız). Trendyol'daki markup mantığının Amazon karşılığı."""
    cfg = await _get_config()
    try:
        return float((cfg or {}).get("markup") or 0)
    except Exception:
        return 0.0


def _amazon_price_of(product: dict, markup: float) -> float:
    """Amazon SATIŞ fiyatı = pazaryeri baz fiyatı × (1 + marj/100).
    Baz fiyat Trendyol ile AYNI: integrations_common._mp_base_price (member_price_1, yoksa price)."""
    try:
        from .integrations_common import _mp_base_price
        base = float(_mp_base_price(product) or 0)
    except Exception:
        base = float(product.get("price") or 0)
    try:
        return round(base * (1 + float(markup) / 100.0), 2)
    except Exception:
        return round(base, 2)


# Türkçe/serbest metin isteyen attribute'lar language_tag alır; kalanlar düz value.
_AMZ_LOCALIZED_ATTRS = {"fabric_type", "material", "style", "occasion_type",
                        "care_instructions", "special_feature", "pattern_type", "neck_style",
                        "fit_type", "collar_style", "age_range_description",
                        "water_resistance_level", "weave_type", "item_type_name",
                        "special_size_type"}
# Ölçü (measurement) tipli attribute'lar — {value, unit} formatı ister (cm varsayılan).
_AMZ_MEASUREMENT_ATTRS = {"leg_hem_opening_width", "waist", "inseam_length", "outseam_length",
                          "rise_measurement", "arm_length", "chest_size", "hip_size"}
# Evet/Hayır (boolean) attribute'lar — varsayılan metni ("false") gerçek boolean'a çevrilir.
_AMZ_BOOL_ATTRS = {"batteries_required", "batteries_included"}
# Üst/tek parça giyim tipleri: Amazon PUT'ta 'Dış Malzeme' + 'Kapanma Türü' (iç içe) istiyor (90220).
_AMZ_OUTER_CLOSURE_PTS = {"DRESS", "COAT", "BLAZER", "SUIT", "SUIT_JACKET", "SWEATER", "SWEATSHIRT",
                          "VEST", "ONE_PIECE_OUTFIT", "OVERALLS", "JUMPSUIT", "APPAREL_SET", "BLOUSE",
                          "BODYSUIT", "ROBE"}


# Açıklamadaki elyaf adı → Amazon TR 'Malzeme' seçenek etiketi (canlı şemadan doğrulandı).
_AMZ_FIBER_LABEL = {"polyester": "Polyester", "pamuk": "Pamuk", "viskon": "Rayon", "viskoz": "Rayon",
                    "rayon": "Rayon", "modal": "Rayon", "elastan": "Spandeks", "likra": "Spandeks",
                    "spandeks": "Spandeks", "keten": "Keten", "akrilik": "Akrilik", "naylon": "Naylon",
                    "poliamid": "Naylon", "polyamid": "Naylon", "ipek": "İpek", "kaşmir": "Kaşmir",
                    "deri": "Deri", "kadife": "Kadife", "denim": "Kot", "kot": "Kot", "yün": "Yün",
                    "yun": "Yün", "tencel": "Rayon", "lyocell": "Rayon", "liyosel": "Rayon"}
# PUT 90220 ile görülen productType → koşullu-zorunlu alanlar (yalnız bu tiplerde gönderilir).
_AMZ_INNER_PTS = {"SUIT", "SKIRT", "DRESS"}
_AMZ_MATERIAL_PTS = {"SHORTS", "COAT", "BLAZER", "DRESS"}
_AMZ_STYLE_BY_NAME = {
    "SHORTS": [("bermuda", "Bermuda Şort"), ("şortolon", "Bermuda Şort"), ("boyfriend", "Boyfriend Şort"),
               ("chino", "Chino Şort"), ("kargo", "Kargo Şortu"), ("mini", "Mini Şort")],
    "COAT": [("trençkot", "Trençkot"), ("trenchcoat", "Trençkot"), ("trench", "Trençkot"),
             ("parka", "Parka Mont"), ("pelerin", "Pelerin Kaban"), ("panço", "Panço Mont"),
             ("kimono", "Kimono Kaban"), ("kruvaze", "Kruvaze Kaban"), ("yağmurluk", "Yağmurluk"),
             ("bomber", "Pilot Kaban"), ("pilot", "Pilot Kaban"), ("duffle", "Duffle Kaban"),
             ("maksi", "Maksi Kaban"), ("maxi", "Maksi Kaban")],
}
_AMZ_STYLE_FALLBACK = {
    "SHORTS": [("", "Mini Şort")],
    "COAT": [("kaban", "A Kesim Kaban"), ("trenç", "Trençkot"), ("ceket", "Pilot Kaban"), ("", "Parka Mont")],
    "HANDBAG": [("", "Minimal")],
}
_AMZ_DRESS_LEN = [("maksi", "Maksi"), ("maxi", "Maksi"), ("uzun", "Maksi"), ("midi", "Midi"),
                  ("mini", "Mini"), ("kısa", "Mini")]


def _amz_dominant_fiber(fabric: str) -> str:
    pairs = re.findall(r"%\s*(\d{1,3})\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", fabric or "")
    if pairs:
        ranked = sorted(pairs, key=lambda x: -int(x[0]))
        # Baskın elyaf Amazon listesinde tanınmıyorsa sıradakine bak (ör. tanınmayan bir karışım adı).
        for _pc, fw in ranked:
            if fw.lower() in _AMZ_FIBER_LABEL:
                return fw
        return ranked[0][1]
    return (fabric or "").strip().split(" ")[0] if fabric else ""


def _amz_extra_required(attrs: dict, product: dict, pt: str, mp: str, fabric: str):
    """Amazon PUT 90220 ile istenen ek alanlar — YALNIZ ürün verisinden bulunan gerçek değerle ve
    yalnız eksikse. Değer bulunamazsa alan gönderilmez (Amazon hatası görünür kalır)."""
    name = str(product.get("name") or "")
    low = f" {name.lower()} "
    text = f"{name} {product.get('description') or ''}".lower()
    ty = product.get("_ty_attrs") or {}   # ürünün Trendyol ilanındaki özellikler {norm(ad): değer}

    def _ty(*keys):
        for k in keys:
            v = ty.get(k)
            if v not in (None, ""):
                return str(v)
        return ""
    dom = _amz_dominant_fiber(fabric)
    if not dom or dom.lower() not in _AMZ_FIBER_LABEL:
        _tm = _ty("materyal", "kumas tipi", "materyal bileseni", "kumas icerigi", "urun icerigi").lower()
        for fw in _AMZ_FIBER_LABEL:
            if fw in _tm:
                dom = fw
                break

    def _tx(v):
        return [{"language_tag": "tr_TR", "value": v}]
    if pt in _AMZ_INNER_PTS and dom:
        attrs.setdefault("inner", [{"marketplace_id": mp, "material": _tx(dom.capitalize())}])
    if pt in _AMZ_MATERIAL_PTS:
        lab = _AMZ_FIBER_LABEL.get(dom.lower()) if dom else None
        if lab:
            attrs.setdefault("material", [{"value": lab, "language_tag": "tr_TR", "marketplace_id": mp}])
    if pt == "SCARF" and dom:
        attrs.setdefault("outer", [{"marketplace_id": mp, "material": _tx(dom.capitalize())}])
    _tymodel = f" {_ty('model', 'tip', 'sort tipi', 'kaban tipi', 'mont tipi', 'stil').lower()} "
    for kw, lab in _AMZ_STYLE_BY_NAME.get(pt, []):
        if kw in low or kw in _tymodel:
            attrs.setdefault("style", [{"value": lab, "language_tag": "tr_TR", "marketplace_id": mp}])
            break
    # Kullanıcı kararı (2026-10-01): ad/Trendyol bir şey vermezse EN MANTIKLI seçenek.
    if "style" not in attrs and pt in _AMZ_STYLE_FALLBACK:
        fb = next((lab for kw, lab in _AMZ_STYLE_FALLBACK[pt] if kw and kw in low), None) \
            or _AMZ_STYLE_FALLBACK[pt][-1][1]
        attrs["style"] = [{"value": fb, "language_tag": "tr_TR", "marketplace_id": mp}]
    if pt == "HANDBAG":
        _desc = str(product.get("description") or "")
        _mo = re.search(r"Dış[^%<]*%\s*\d+\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", _desc)
        _mi = re.search(r"İç[^%<]*%\s*\d+\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", _desc)
        _hm = (_mo.group(1) if _mo else "") or _ty("materyal", "dis materyal", "malzeme") or "Suni Deri"
        attrs.setdefault("outer", [{"marketplace_id": mp, "material": _tx(_hm.capitalize())}])
        attrs.setdefault("inner", [{"marketplace_id": mp, "material": _tx(
            (_mi.group(1) if _mi else "") or _ty("ic materyal", "astar") or "Kumaş")}])
        # Kullanıcı onayı ("gönder"): ölçü bilgisi yok → orta boy omuz çantası için makul ölçü + 1 cep.
        attrs.setdefault("number_of_pockets", [{"value": 1, "marketplace_id": mp}])
        attrs.setdefault("size_info", [{"marketplace_id": mp, "display_name": _tx("Standart")}])
        attrs.setdefault("item_dimensions", [{"marketplace_id": mp,
                                              "length": {"value": 25, "unit": "centimeters"},
                                              "width": {"value": 8, "unit": "centimeters"},
                                              "height": {"value": 14, "unit": "centimeters"}}])
        _cl = _amz_closure_of(text)
        attrs.setdefault("closure", [{"marketplace_id": mp, "type": _tx("Fermuar" if _cl == "Kapamasız" else _cl)}])
        attrs.setdefault("strap_type", [{"value": _ty("aski tipi", "aski") or "Ayarlanabilir Askı",
                                         "language_tag": "tr_TR", "marketplace_id": mp}])
        attrs.setdefault("number_of_compartments", [{"value": 1, "marketplace_id": mp}])
        _cap = re.search(r"(\d+(?:[.,]\d+)?)", _ty("hacim", "kapasite") or "")
        attrs.setdefault("capacity", [{"value": float(_cap.group(1).replace(",", ".")) if _cap else 1.0,
                                       "unit": "liters", "marketplace_id": mp}])
    if pt == "DRESS":
        _low_len = re.sub(r"\b(uzun|kısa)\s+kol\w*", " ", low)  # "uzun kollu" kol boyudur, elbise boyu değil
        for kw, lab in _AMZ_DRESS_LEN:
            if f" {kw} " in _low_len:
                attrs.setdefault("item_length_description",
                                 [{"value": lab, "language_tag": "tr_TR", "marketplace_id": mp}])
                break
        _tb = _ty("boy", "elbise boyu", "boy uzunluk", "uzunluk", "boy / uzunluk").lower()
        if _tb and "item_length_description" not in attrs:
            for kw, lab in (("diz üstü", "Diz Üstü"), ("diz ustu", "Diz Üstü"), ("diz altı", "Diz Altı"),
                            ("diz alti", "Diz Altı"), ("midi", "Midi"), ("mini", "Mini"), ("maxi", "Maksi"),
                            ("maksi", "Maksi"), ("uzun", "Maksi"), ("kısa", "Mini"), ("kisa", "Mini"),
                            ("bilek", "Ayak Bileği Uzunluğu"), ("diz", "Diz Hizası")):
                if kw in _tb:
                    attrs["item_length_description"] = [{"value": lab, "language_tag": "tr_TR", "marketplace_id": mp}]
                    break
        _ny = _ty("yaka tipi", "yaka", "yaka stili", "yaka modeli")
        if _ny:
            attrs.setdefault("neck", [{"marketplace_id": mp, "neck_style": _tx(_ny)}])
        # Kullanıcı kararı: bilgi yoksa en mantıklı değer.
        attrs.setdefault("item_length_description", [{"value": "Midi", "language_tag": "tr_TR", "marketplace_id": mp}])
        attrs.setdefault("neck", [{"marketplace_id": mp, "neck_style": _tx("Yuvarlak Yaka")}])
        m = re.search(r"([a-zçğıöşü]+(?:\s[a-zçğıöşü]+)?)\s+yaka", text)
        if m:
            neck = (m.group(1).split()[-1] + " Yaka").capitalize()
            if m.group(1).split()[-1] in ("v",):
                neck = "V Yaka"
            attrs.setdefault("neck", [{"marketplace_id": mp, "neck_style": _tx(neck)}])


def _amz_closure_of(text: str) -> str:
    """Ürün adı+açıklamasından kapanma türü (yalnız metinde geçen); bulunamazsa 'Kapamasız'."""
    t = (text or "").lower()
    for kw, val in (("fermuar", "Fermuar"), ("düğme", "Düğme"), ("dugme", "Düğme"), ("çıtçıt", "Çıtçıt"),
                    ("kuşak", "Kuşak"), ("bağcık", "Bağcık"), ("bağlama", "Bağcık"), ("lastik", "Lastikli"),
                    ("kanca", "Kanca")):
        if kw in t:
            return val
    return "Kapamasız"


# Meluni ürün alanından OTOMATİK dolan attribute'lar — kullanıcı default'u bunları EZMEZ.
_AMZ_AUTO_FILLED = {"item_name", "brand", "product_description", "bullet_point",
                    "main_product_image_locator", "purchasable_offer", "fulfillment_availability",
                    "condition_type", "externally_assigned_product_identifier", "size", "color"}


def _amz_collect_local_values(product: dict, variant: dict) -> dict:
    """Ürün+varyant özelliklerini {lowername: value} toplar (Trendyol _collect_local_values ile
    aynı mantık) — attribute_mappings'te seçilen yerel alanın değerini bulmak için."""
    out = {}

    def _put(nm, vv):
        if not nm or vv in (None, ""):
            return
        out.setdefault(str(nm).lower().strip(), str(vv))

    def _walk(attrs):
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if isinstance(v, dict):
                    _put(v.get("label") or v.get("name") or k, v.get("value") or v.get("attribute_value"))
                elif v is not None:
                    _put(k, v)
        elif isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict):
                    _put(a.get("label") or a.get("name") or a.get("type") or a.get("attribute_name"),
                         a.get("value") or a.get("attribute_value"))
    _walk(product.get("attributes"))
    if variant:
        _walk(variant.get("attributes"))
        if variant.get("color"):
            _put("Renk", variant["color"]); _put("Color", variant["color"])
        if variant.get("size"):
            _put("Beden", variant["size"]); _put("Size", variant["size"])
    return out


def _amazon_listing_attributes(product, variant, product_type, mp, price, qty, brand_default,
                               default_attrs: dict = None, attr_mappings: list = None,
                               list_price: float = None) -> dict:
    """Meluni ürün+varyant → Amazon Listings attribute'ları (giyim odaklı ortak set).
    Eksik/zorunlu alanları Amazon PUT yanıtındaki issues[] söyler → çağıran gösterir."""
    name = (product.get("name") or "").strip()
    desc = clean_amazon_text(product.get("description") or name)
    imgs = _product_images(product, variant)
    barcode = str(variant.get("barcode") or "").strip()
    # Kullanıcı onayı (2026-10-01, "gönder"): 12 haneli TR (868/869) barkodda kontrol hanesi eksik kaydedilmiş
    # → Amazon'a giden değerde EAN-13 kontrol hanesi eklenir (ürün kaydı DEĞİŞMEZ).
    if barcode.isdigit() and len(barcode) == 12 and barcode[:3] in ("868", "869"):
        _cs = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(barcode))
        barcode = barcode + str((10 - _cs % 10) % 10)
    brand = (product.get("brand") or brand_default or "").strip() or "Meluni"
    bullets = []
    for b in (product.get("features") or product.get("bullet_points") or []):
        if isinstance(b, str) and b.strip():
            bullets.append(clean_amazon_text(b))
    if not bullets and desc:
        bullets = [desc[:200]]
    attrs = {
        "condition_type": [{"value": "new_new", "marketplace_id": mp}],
        "item_name": [{"value": name, "language_tag": "tr_TR", "marketplace_id": mp}],
        "brand": [{"value": brand, "marketplace_id": mp}],
        "product_description": [{"value": desc, "language_tag": "tr_TR", "marketplace_id": mp}],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": max(0, int(qty))}],
        "purchasable_offer": [{"marketplace_id": mp, "currency": "TRY", "audience": "ALL",
                               "our_price": [{"schedule": [{"value_with_tax": round(float(price or 0), 2)}]}]}],
        # Amazon TR'de Seller Central'ın "Varsayılan Şablon" seçimi bu kimlikle temsil
        # ediliyor. Eksik olduğunda ürün/katalog kabul edilse bile teklif "Teklif yok"
        # kalabiliyor. Ortama özel şablon varsa env ile değiştirilebilir.
        "merchant_shipping_group": [{
            "value": os.environ.get("AMAZON_MERCHANT_SHIPPING_GROUP", "legacy-template-id"),
            "marketplace_id": mp,
        }],
    }
    # Üstü çizili "liste fiyatı" (RRP) — satış fiyatından yüksekse gönder (indirim görünür).
    try:
        _lp = round(float(list_price), 2) if list_price else 0
    except Exception:
        _lp = 0
    if _lp > round(float(price or 0), 2):
        attrs["list_price"] = [{"value": _lp, "currency": "TRY", "marketplace_id": mp}]
    if bullets:
        attrs["bullet_point"] = [{"value": b, "language_tag": "tr_TR", "marketplace_id": mp} for b in bullets[:5]]
    if imgs:
        attrs["main_product_image_locator"] = [{"media_location": imgs[0], "marketplace_id": mp}]
        for i, u in enumerate(imgs[1:9]):  # other_product_image_locator_1..8
            attrs[f"other_product_image_locator_{i+1}"] = [{"media_location": u, "marketplace_id": mp}]
    if barcode:
        attrs["externally_assigned_product_identifier"] = [
            {"value": barcode, "type": "ean", "marketplace_id": mp}]
    if variant.get("size"):
        # DENETİM (Amazon P3): beden değeri (M / 38) LOKALİZE DEĞİL — birçok apparel productType
        # 'size' alanında language_tag KABUL ETMEZ, göndermek varyantı düşürüyordu. language_tag'siz.
        attrs["size"] = [{"value": str(variant["size"]), "marketplace_id": mp}]
    _clr = _amazon_color_of(product, variant)
    if _clr:
        attrs["color"] = [{"value": _clr, "language_tag": "tr_TR", "marketplace_id": mp}]
    # Site-alanı eşleştirmeleri (attribute_mappings): kullanıcı bir Amazon alanını Meluni
    # özelliğine eşlediyse, ürünün o özellik DEĞERİNİ gönder (varsayılandan ÖNCE gelir).
    if attr_mappings:
        _locals = _amz_collect_local_values(product, variant)
        for m in attr_mappings:
            amz_attr = str((m or {}).get("mp_attr_id") or "").strip()
            local = str((m or {}).get("local_attr") or "").strip()
            if not amz_attr or not local or amz_attr in _AMZ_AUTO_FILLED or amz_attr in attrs:
                continue
            val = _locals.get(local.lower())
            if not val:
                continue
            if amz_attr in _AMZ_LOCALIZED_ATTRS:
                attrs[amz_attr] = [{"value": str(val), "language_tag": "tr_TR", "marketplace_id": mp}]
            else:
                attrs[amz_attr] = [{"value": str(val), "marketplace_id": mp}]
    # Kullanıcı VARSAYILAN değerleri (fabric_type, country_of_origin, menşei vb.) —
    # kategori default_mappings'ten gelir. product_type + otomatik-dolan alanlar ATLANIR;
    # zaten set edilmiş bir attribute EZİLMEZ.
    for k, v in (default_attrs or {}).items():
        key = str(k).strip()
        if not key or key == "product_type" or key in _AMZ_AUTO_FILLED or key in attrs:
            continue
        if v is None or str(v).strip() == "":
            continue
        val = str(v).strip()
        if key in _AMZ_BOOL_ATTRS:
            attrs[key] = [{"value": val.lower() in ("true", "1", "evet", "yes"), "marketplace_id": mp}]
            continue
        if key in _AMZ_MEASUREMENT_ATTRS:
            try:
                _num = float(str(val).replace(",", ".").split()[0])
                attrs[key] = [{"value": _num, "unit": "centimeters", "marketplace_id": mp}]
            except Exception:
                pass
        elif key in _AMZ_LOCALIZED_ATTRS:
            attrs[key] = [{"value": val, "language_tag": "tr_TR", "marketplace_id": mp}]
        else:
            attrs[key] = [{"value": val, "marketplace_id": mp}]

    # Ürünün yapılandırılmış kumaş özelliği yoksa açıklamadaki gerçek kumaş
    # bilgisini kullan. Kategori geneline "Pamuk" gibi hatalı bir sabit uygulanmaz.
    inferred_fabric = infer_amazon_fabric(product.get("description"))
    if inferred_fabric:
        attrs.setdefault("fabric_type", [{"value": inferred_fabric,
                                           "language_tag": "tr_TR",
                                           "marketplace_id": mp}])

    # ── AUTO zorunlular (Amazon giyimde ister) — kullanıcı/­default ezmemişse doldur ──
    attrs.setdefault("manufacturer", [{"value": brand, "marketplace_id": mp}])
    _model = (name or "")[:60] or (variant.get("stock_code") or "")
    if _model:
        attrs.setdefault("model_name", [{"value": _model, "marketplace_id": mp}])
    # Bazı eski kategori varsayımları güncel PANTS şemasında alan değil. Amazon bunları
    # sadece uyarı olarak görse de her gönderimde gereksiz hata üretip gerçek sorunu
    # maskeleyebiliyordu; geçersiz alanları payload'a hiç koyma.
    for _obsolete in ("lens", "item_display_dimensions", "gift_options",
                      "supplemental_condition_information"):
        attrs.pop(_obsolete, None)
    # Paket boyutları (zorunlu) — giyim için makul varsayılan (cm). Ürün verisi varsa onu kullan.
    # Paket boyutu/ağırlığı KULLANICI mapping'inden hatalı gelirse Amazon 99022 veriyor —
    # doğru format garantisi yoksa hiç gönderme (zorunlu değil; gerekirse 90220 ile görürüz).
    for _pk in ("item_package_dimensions", "item_package_weight", "leg_hem_opening_width"):
        attrs.pop(_pk, None)
    # ── Kadın-giyim apparel universal güvenli varsayılanlar (kategori default'u EZEBİLİR) ──
    # Alan adları Amazon şema title'larından DOĞRULANDI.
    attrs.setdefault("department", [{"value": "Kadın", "marketplace_id": mp}])
    attrs.setdefault("target_gender", [{"value": "female", "marketplace_id": mp}])
    attrs.setdefault("care_instructions", [{"value": "Makinede Yıkama", "language_tag": "tr_TR", "marketplace_id": mp}])
    _pt = (product_type or "").upper()
    # Amazon TR SHIRT semasi bu alanlari PUT sirasinda kosullu-zorunlu sayiyor; Definitions
    # API'nin ust-seviye required listesinde gorunmedikleri icin yalniz on-dogrulama bunlari
    # yakalayamiyordu. Secilen degerlerin tamami canli SHIRT enum listesinden alinmistir.
    if _pt == "SHIRT":
        def _loc(value):
            return [{"value": value, "language_tag": "tr_TR", "marketplace_id": mp}]
        attrs.setdefault("fit_type", _loc("Rahat Kesim"))
        attrs.setdefault("collar_style", _loc("Yuvarlak Yaka"))
        attrs.setdefault("age_range_description", _loc("Yetişkin"))
        attrs.setdefault("style", _loc("Tişört"))
        # Canlı Amazon TR şeması etiketi Türkçeleştiriyor, ancak API bu alan için
        # görünen etiketi değil enum kodunu kabul ediyor.
        attrs.setdefault("water_resistance_level", _loc("not_water_resistant"))
        attrs.setdefault("special_size_type", _loc("Standart"))
        attrs.setdefault("weave_type", _loc("Sade"))
        attrs.setdefault("item_type_name", _loc("Tişört"))
        _outer_material = "Pamuk" if "pamuk" in (inferred_fabric or "").lower() else (inferred_fabric or "Kumaş")
        attrs.setdefault("outer", [{"marketplace_id": mp,
                                    "material": [{"language_tag": "tr_TR", "value": _outer_material}]}])
    # Pantolon/alt-giyim özel zorunluları — İÇ İÇE (nested) yapı, şema title'larından DOĞRULANDI:
    # leg.style / outer.material / closure.type / rise.style+height. Yalnız ilgili productType'ta.
    if any(x in _pt for x in ("PANT", "TROUSER", "BOTTOM", "JEAN", "SHORT", "LEGGING", "SKIRT")):
        attrs.setdefault("weave_type", [{"value": "Düz", "marketplace_id": mp}])              # Dokuma Türü
        # Yanlış/geçersiz eski denemeleri temizle
        for _bad in ("compliance_outer_surface_material", "closure_type", "leg_style",
                     "outer_material_type", "front_style", "apparel_closure_orientation", "pants_form_type"):
            attrs.pop(_bad, None)
        # İÇ İÇE composite zorunlular — HAM ŞEMADAN doğrulanmış çift-nested format:
        # parent:[{marketplace_id, child:[{language_tag,value}]}]  (ölçüde {unit,value}).
        def _txt(v):
            return [{"language_tag": "tr_TR", "value": v}]
        attrs["leg"] = [{"marketplace_id": mp, "style": _txt("Düz Paça")}]              # Bacak Stili
        attrs["outer"] = [{"marketplace_id": mp, "material": _txt("Pamuk")}]            # Dış Malzeme
        attrs["closure"] = [{"marketplace_id": mp, "type": _txt("Fermuar")}]            # İlikleme/Kapanma
        attrs["rise"] = [{"marketplace_id": mp,                                         # Yükselti Stili + Mesafesi
                          "style": _txt("Yüksek Bel"),
                          "height": [{"unit": "centimeters", "value": 28}]}]
    # Diğer üst/tek parça giyim tipleri (elbise, mont, ceket, kazak…): Amazon 90220 ile Dış Malzeme +
    # Kapanma Türü istiyor. YALNIZ eksikse; kumaş açıklamadan, kapanma ad/açıklamada geçen kelimeden.
    if _pt in _AMZ_OUTER_CLOSURE_PTS:
        _pairs = re.findall(r"%\s*(\d{1,3})\s*([A-Za-zÇĞİÖŞÜçğıöşü]+)", inferred_fabric or "")
        _om = (max(_pairs, key=lambda x: int(x[0]))[1].capitalize() if _pairs
               else (inferred_fabric or "Kumaş"))
        attrs.setdefault("outer", [{"marketplace_id": mp, "material": [{"language_tag": "tr_TR", "value": _om}]}])
        attrs.setdefault("closure", [{"marketplace_id": mp, "type": [
            {"language_tag": "tr_TR", "value": _amz_closure_of(f"{name} {product.get('description') or ''}")}]}])
    _amz_extra_required(attrs, product, _pt, mp, inferred_fabric)
    # Ölçü tipli ama kullanıcı düz sayı girdiyse (ör. leg_hem_opening_width) → {value,unit}'e çevir.
    for _mk in list(attrs.keys()):
        if _mk in _AMZ_MEASUREMENT_ATTRS:
            _cur = attrs[_mk]
            if isinstance(_cur, list) and _cur and "unit" not in _cur[0] and "value" in _cur[0]:
                try:
                    _n = float(str(_cur[0]["value"]).replace(",", ".").split()[0])
                    attrs[_mk] = [{"value": _n, "unit": "centimeters", "marketplace_id": mp}]
                except Exception:
                    attrs.pop(_mk, None)  # geçersiz → hiç gönderme (Amazon 4000001 vermesin)
    return attrs


async def _resolve_amazon_pt_and_defaults(product: dict) -> tuple:
    """Ürün için (Amazon productType, default_mappings dict) döndürür.
    productType: (1) product.amazon_product_type, (2) kategori eşlemesi. default_mappings:
    kategori belgesindeki kullanıcı varsayılanları (fabric_type, country_of_origin vb.)."""
    pt = (product.get("amazon_product_type") or "").strip()
    defaults, attr_mappings = {}, []
    cat_id = product.get("category_id") or product.get("category")
    if cat_id:
        m = await db.category_mappings.find_one(
            {"marketplace": "amazon-tr", "category_id": str(cat_id)}, {"_id": 0})
        if m:
            defaults = dict(m.get("default_mappings") or {})
            attr_mappings = list(m.get("attribute_mappings") or [])
            if not pt:
                # productType kategori eşlemesinde marketplace_category_id alanında saklanır
                # (ör. "PANTS"). default_mappings.product_type / product_type yedek.
                pt = (str(m.get("marketplace_category_id") or "").strip()
                      or str(defaults.get("product_type") or "").strip()
                      or str(m.get("product_type") or "").strip())
    return pt, defaults, attr_mappings


async def _resolve_amazon_product_type(product: dict) -> str:
    pt, _, _ = await _resolve_amazon_pt_and_defaults(product)
    return pt


def _amazon_seller_sku(variant: dict, product: dict = None) -> str:
    """Varyant için BENZERSİZ Amazon SellerSKU. Renkler AYNI stok kodunu paylaşabildiğinden
    (Siyah/Acı Kahve → FCSS1800001) SKU'ya RENK de girer: stok_kodu[-renk][-beden].
    Renk/beden yoksa o parça atlanır; hiçbiri yoksa barkod."""
    product = product or {}
    return build_amazon_seller_sku(variant, product, _amazon_color_of(product, variant))


def _amz_code_match(product: dict, variant: dict, bset: set, sset: set, pset: set) -> bool:
    """Filtre eşleşmesi — varyant VE ürün seviyesindeki tüm kod adaylarını (barkod/stok_kodu/
    sku/urun_id/id) verilen kümelerle karşılaştırır. TAM eşleşme yoksa, kullanıcı SKU'nun bir
    PARÇASINI (ör. model no '2909') ya da ürün ADININ bir kısmını girmiş olabilir → substring
    ve isim eşleşmesi de denenir. 'Stok Kodu/Barkod' alanı bset+sset'e gider."""
    codes = [str(c or "").strip() for c in (
        variant.get("barcode"), variant.get("stock_code"), variant.get("sku"),
        variant.get("urun_id"), product.get("stock_code"), product.get("barcode"),
        product.get("sku"), product.get("id"))]
    codes = [c for c in codes if c]
    tokens = (bset | sset | pset)
    # 1) TAM eşleşme
    for c in codes:
        if c in tokens:
            return True
    # 2) SUBSTRING (SKU parçası) veya İSİM parçası
    name = str(product.get("name") or "").lower()
    for t in tokens:
        tl = str(t).strip().lower()
        if len(tl) < 3:  # çok kısa token yanlış eşleşir
            continue
        for c in codes:
            if tl in c.lower():
                return True
        if tl in name:
            return True
    return False


async def _amazon_put_listing(seller: str, mp: str, pt: str, sku: str, attrs: dict) -> tuple:
    """Tek SKU listeleme PUT + sonuç. Döner (result_dict, ok_bool, dry_bool)."""
    body = {"productType": pt, "requirements": "LISTING", "attributes": attrs}
    try:
        res = await _spapi_send("PUT", f"/listings/2021-08-01/items/{seller}/{sku}",
                                body=body, params={"marketplaceIds": mp})
    except Exception as _e:
        return {"sku": sku, "ok": False, "error": str(_e)}, False, False
    if res.get("dry_run"):
        return {"sku": sku, "product_type": pt, "dry_run": True,
                "attributes_preview": sorted(attrs.keys())}, True, True
    data = res.get("data") or {}
    issues = data.get("issues") or []
    _ok = res.get("ok") and (data.get("status") in ("ACCEPTED", "VALID", None) or not issues)
    return ({"sku": sku, "product_type": pt, "ok": bool(_ok), "status": data.get("status"),
             "http": res.get("status"), "submissionId": data.get("submissionId"),
             "raw_errors": (data.get("errors") if not issues else None),
             "issues": [{"code": i.get("code"), "message": i.get("message"),
                         "severity": i.get("severity"),
                         "attributeNames": i.get("attributeNames") or []} for i in issues]}, bool(_ok), False)


async def sync_products_to_amazon(payload: dict, current_user: dict) -> dict:
    """Meluni ürünlerini Amazon'a LİSTELER (Listings Items PUT). payload:
      { barcodes?, stock_codes?, product_ids?, default_product_type?, limit? }
    Güvenlik: filtre VERİLMEDEN tüm katalog listelenmez (kaza önleme) — limit zorunlu değilse
    yalnız filtreli çalışır. ALLOW_WRITE=0 iken dry-run (Amazon'a gitmez, would_send + attribute
    önizleme döner). Her SKU için Amazon issues[] toplanır (zorunlu alan eksikleri görünür)."""
    payload = payload or {}
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()

    _bset = {str(x).strip() for x in (payload.get("barcodes") or []) if str(x).strip()}
    _sset = {str(x).strip() for x in (payload.get("stock_codes") or []) if str(x).strip()}
    _pset = {str(x).strip() for x in (payload.get("product_ids") or []) if str(x).strip()}
    default_pt = (payload.get("default_product_type") or "").strip()
    limit = int(payload.get("limit") or 0)
    _filtered = bool(_bset or _sset or _pset)
    if not _filtered and limit <= 0:
        raise HTTPException(status_code=400,
                            detail="Güvenlik: filtre (barcodes/stock_codes/product_ids) veya limit verin. "
                                   "Tüm katalog tek seferde listelenmez.")

    # Pasif/kopya ürünler filtreyle açıkça aranmış olsa bile Amazon'a listelenmez.
    # Aynı stok kodundaki pasif bir kopya, aktif ürünün SellerSKU'sunu ikinci kez
    # yazarak child'ı yanlış parent ailesine taşıyabiliyordu.
    q = {"is_active": True}
    if _pset:
        q["id"] = {"$in": list(_pset)}
    products = await db.products.find(q, {"_id": 0}).to_list(length=(limit or None))
    if _filtered:
        # Mongo sorgusunu geniş tutuyoruz çünkü kod ürün veya varyant seviyesinde olabilir;
        # fakat gerçek PUT kapsamını burada mutlaka daralt. Bu süzme yokken panelde tek
        # stok kodu yazılsa dahi tüm aktif katalog Amazon'a gönderilmeye çalışılıyordu.
        products = [
            p for p in products
            if any(_amz_code_match(p, v, _bset, _sset, _pset)
                   for v in (p.get("variants") or [{}]))
        ]

    # Amazon'da daha once baska bir SKU kalibiyla acilmis fakat EAN ile ayni Meluni
    # varyantina eslenmis kaydi koru. Yeni SKU uretmek ayni EAN icin ikinci teklif ve
    # ikinci varyant ailesi olusturup 8541/13013 hatalarina yol aciyordu.
    _mapped_rows = {}
    _product_ids = [p.get("id") for p in products if p.get("id")]
    if _product_ids:
        async for _m in db.amazon_sku_map.find(
            {"matched": True, "product_id": {"$in": _product_ids}},
            {"_id": 0, "sku": 1, "asin": 1, "amazon_status": 1,
             "listing_status": 1, "product_id": 1, "variant_barcode": 1},
        ):
            _key = (str(_m.get("product_id") or ""), str(_m.get("variant_barcode") or ""))
            _mapped_rows.setdefault(_key, []).append(_m)

    def _listing_sku(pp: dict, v: dict) -> str:
        generated = _amazon_seller_sku(v, pp)
        key = (str(pp.get("id") or ""), str(v.get("barcode") or ""))
        existing = pick_existing_amazon_sku(_mapped_rows.get(key) or [], generated)
        return str((existing or {}).get("sku") or generated)

    def _uses_existing_catalog_sku(pp: dict, v: dict) -> bool:
        generated = _amazon_seller_sku(v, pp)
        key = (str(pp.get("id") or ""), str(v.get("barcode") or ""))
        return bool(pick_existing_amazon_sku(_mapped_rows.get(key) or [], generated))

    # Aynı EAN ile Amazon kataloğunda zaten var olan SKU'larda model_name zorunlu;
    # fakat Meluni adıyla tekrar yazılırsa 8541 katalog çakışması oluşur. Listings API
    # kaydındaki gerçek katalog değerini alıp yalnız bu alanda koruyoruz. Okuma başarısız
    # olursa yeni ürün model adını bırakmak, alanı boş göndermekten güvenlidir.
    _catalog_model_cache: dict[str, str] = {}

    async def _catalog_model_name(sku: str) -> str:
        sku = str(sku or "").strip()
        if not sku:
            return ""
        if sku in _catalog_model_cache:
            return _catalog_model_cache[sku]
        value = ""
        try:
            got = await _spapi_get(
                f"/listings/2021-08-01/items/{seller}/{sku}",
                {"marketplaceIds": mp, "includedData": "attributes"},
            )
            attrs = ((got.get("data") or {}).get("attributes") or {}) if got.get("ok") else {}
            raw = attrs.get("model_name") or []
            if isinstance(raw, list) and raw:
                value = str((raw[0] or {}).get("value") or "").strip()
        except Exception as exc:
            logger.warning("[amazon] mevcut SKU model adı okunamadı (%s): %s", sku, exc)
        _catalog_model_cache[sku] = value
        return value

    results = []
    _reserved_child_skus = set()
    pushed = failed = skipped = 0
    markup = await _amazon_markup()
    def _tally(r, ok, dry):
        nonlocal pushed, failed
        results.append(r)
        if not dry:
            if ok:
                pushed += 1
            else:
                failed += 1

    async def _mark_offer_sync_required(sku: str, ok: bool, dry: bool):
        """Yeni/yeniden oluşturulan listing için ilk stok-fiyat PATCH'ini zorunlu kıl.

        Amazon catalog PUT'u ACCEPTED dönebildiği halde teklif birkaç saniye sonra oluşur.
        Önceki bir denemeden kalan aynı qty/price state'i cron'un PATCH'i atlamasına yol
        açıyordu; Seller Central'da ürün ailesi ve görseller var, teklif ise 0/boş kalıyordu.
        Başarılı child PUT sonrası yalnız değişiklik-imzasını temizle; hata geçmişi ve denetim
        kaydı korunur, bir sonraki dakikalık tur gerçek teklifi yeniden yollar.
        """
        if not sku or not ok or dry:
            return
        await db.amazon_sku_state.update_one(
            {"sku": sku},
            {"$set": {"listing_synced_at": datetime.now(timezone.utc).isoformat()},
             "$unset": {"qty": "", "price": "", "next_retry_at": "", "quarantined": ""}},
            upsert=True,
        )

    async def _list_family(colors_products, group_key):
        """colors_products: AYNI stok kodunu paylaşan renk ürünleri (her biri bir renk).
        Beden×Renk children → TEK Amazon listesi (variation)."""
        # Ürünün Trendyol ilanındaki özellikleri (yaka/boy/materyal) — yalnız Amazon'un ek zorunlu alanları
        # için okunur; ürün kaydı DEĞİŞMEZ (kopya üzerinde, '_ty_attrs').
        _cp = []
        for _pp in colors_products:
            try:
                from .integrations_hepsiburada import _hb_ty_live_attrs
                _ta = await asyncio.wait_for(_hb_ty_live_attrs(_pp, {}), timeout=12)
            except Exception:
                _ta = {}
            _cp.append({**_pp, "_ty_attrs": _ta})
        colors_products = _cp
        p0 = colors_products[0]
        pt, cat_defaults, cat_mappings = await _resolve_amazon_pt_and_defaults(p0)
        pt = pt or default_pt
        specs = []
        for pp in colors_products:
            for v in (pp.get("variants") or []):
                child_sku = _listing_sku(pp, v)
                if not child_sku or child_sku in _reserved_child_skus:
                    continue
                _reserved_child_skus.add(child_sku)
                specs.append((pp, v))
        if not specs:
            return
        if not pt:
            for pp, v in specs:
                _tally({"sku": _listing_sku(pp, v), "product": pp.get("name"),
                        "ok": False, "error": "Amazon productType yok (kategori eşleştir)."}, False, False)
            return
        n_color = len({_amazon_color_of(pp, v) for pp, v in specs if _amazon_color_of(pp, v)})
        _has_size = any(str(v.get("size") or "").strip() for _, v in specs)
        # DENETİM (Amazon P3): variation_theme'i productType şemasının GEÇERLİ enum'undan seç
        # (sabit 'SIZE_NAME/COLOR_NAME' Amazon'ca reddediliyordu → beden/renk düşüyordu).
        try:
            _sch = await _amazon_product_type_schema(pt)
            _theme_enum = (_sch.get("values") or {}).get("variation_theme") or []
        except Exception:
            _theme_enum = []
        theme = _pick_variation_theme(_theme_enum, n_color > 1, _has_size)
        if len(specs) > 1:
            parent_sku = (group_key or f"{p0.get('id')}-P")
            pattrs = _amazon_listing_attributes(p0, {}, pt, mp, _amazon_price_of(p0, markup), 0,
                                                p0.get("brand"), default_attrs=cat_defaults,
                                                attr_mappings=cat_mappings,
                                                list_price=round(float(p0.get("price") or 0), 2))
            for _k in ("purchasable_offer", "fulfillment_availability", "merchant_shipping_group", "size", "color",
                       "externally_assigned_product_identifier", "list_price"):
                pattrs.pop(_k, None)
            pattrs["parentage_level"] = [{"marketplace_id": mp, "value": "parent"}]
            pattrs["variation_theme"] = [{"marketplace_id": mp, "name": theme}]
            _r, _ok, _dry = await _amazon_put_listing(seller, mp, pt, parent_sku, pattrs)
            _r["product"] = f"{p0.get('name')} (ana ürün)"
            _tally(_r, _ok, _dry)
            _done_skus = set()
            for pp, v in specs:
                _csku = _listing_sku(pp, v)
                if _csku in _done_skus:   # mükerrer (Kopya) ürün → aynı SKU'yu tekrar yazma
                    continue
                _done_skus.add(_csku)
                cattrs = _amazon_listing_attributes(pp, v, pt, mp, _amazon_price_of(pp, markup),
                                                    int(v.get("stock") or 0), pp.get("brand"),
                                                    default_attrs=cat_defaults, attr_mappings=cat_mappings,
                                                    list_price=round(float(pp.get("price") or 0), 2))
                # Mevcut ASIN'in katalog model adını koru. Alanı çıkarmak 90220
                # (zorunlu model adı eksik) veriyordu; gerçek Amazon değeri alınamazsa
                # yeni ürünün model adını olduğu gibi bırakırız.
                if _uses_existing_catalog_sku(pp, v):
                    _catalog_model = await _catalog_model_name(_csku)
                    if _catalog_model:
                        cattrs["model_name"] = [{"value": _catalog_model, "marketplace_id": mp}]
                cattrs["parentage_level"] = [{"marketplace_id": mp, "value": "child"}]
                cattrs["variation_theme"] = [{"marketplace_id": mp, "name": theme}]
                cattrs["child_parent_sku_relationship"] = [
                    {"marketplace_id": mp, "child_relationship_type": "variation", "parent_sku": parent_sku}]
                _r, _ok, _dry = await _amazon_put_listing(seller, mp, pt, _csku, cattrs)
                _r["product"] = pp.get("name")
                _tally(_r, _ok, _dry)
                await _mark_offer_sync_required(_csku, _ok, _dry)
        else:
            pp, v = specs[0]
            attrs = _amazon_listing_attributes(pp, v, pt, mp, _amazon_price_of(pp, markup),
                                               int(v.get("stock") or 0), pp.get("brand"),
                                               default_attrs=cat_defaults, attr_mappings=cat_mappings,
                                               list_price=round(float(pp.get("price") or 0), 2))
            _sku = _listing_sku(pp, v)
            if _uses_existing_catalog_sku(pp, v):
                _catalog_model = await _catalog_model_name(_sku)
                if _catalog_model:
                    attrs["model_name"] = [{"value": _catalog_model, "marketplace_id": mp}]
            _r, _ok, _dry = await _amazon_put_listing(seller, mp, pt, _sku, attrs)
            _r["product"] = pp.get("name")
            _tally(_r, _ok, _dry)
            await _mark_offer_sync_required(_sku, _ok, _dry)

    # Ürünleri STOK KODUNA göre grupla (renk grubu) — aynı stok kodundaki TÜM renkler tek listede.
    # DENETİM (Amazon P2): grup anahtarı eskiden yalnız p.get("stock_code")'du → stok kodu
    # `sku`/varyant içinde olan ürünler gruplanamayıp tekil/parent'sız listeleniyordu.
    # Trendyol ile AYNI çözücüyü (_resolve_stock_code = productMainId) kullan. İSİM GUARD:
    # aynı stok kodunu FARKLI modeller paylaşabildiğinden (_model_key) model bazında ayır →
    # over-merge önlenir; parent_sku benzersizliği için ikinci+ modele -N eklenir.
    from collections import defaultdict as _dd
    from .integrations_common import _resolve_stock_code as _rsc
    try:
        from .products import _model_key as _mk
    except Exception:
        def _mk(_p):
            return str((_p or {}).get("name") or "")
    _scodes = {_rsc(p) for p in products if _rsc(p)}
    _sibs = _dd(list)
    if _scodes:
        _codes = list(_scodes)
        _sq = amazon_sibling_query(_codes)
        _q2 = {"$and": [_sq, {"is_active": True}]}
        async for sp in db.products.find(_q2, {"_id": 0}):
            _sk = _rsc(sp)
            if _sk:
                _sibs[_sk].append(sp)
    _groups, _gk_sku, _sc_n, _singles, _seen = {}, {}, _dd(int), [], set()
    for p in products:
        sc = _rsc(p)
        if not sc:
            pid = str(p.get("id"))
            if pid not in _seen:
                _seen.add(pid)
                _singles.append(p)
            continue
        model = _mk(p)
        gkey = f"{sc}||{model}"
        if gkey not in _groups:
            _groups[gkey] = []
            _n = _sc_n[sc]
            _sc_n[sc] += 1
            _gk_sku[gkey] = sc if _n == 0 else f"{sc}-{_n + 1}"
        for sp in (_sibs.get(sc) or [p]):
            if _mk(sp) != model:      # aynı SKU ama farklı model → ayrı aile
                continue
            pid = str(sp.get("id"))
            if pid not in _seen:
                _seen.add(pid)
                _groups[gkey].append(sp)

    # Filtreli tekil gönderimde kapsam beklenmedik biçimde büyürse Amazon'a hiçbir PUT
    # yapmadan dur. Bu, yanlış/gevşek bir eşleşmenin tüm kataloğu yeniden yazmasını önleyen
    # son emniyet kemeridir. Büyük toplu işler ayrıca açık onay bayrağı gerektirir.
    if _filtered and not payload.get("confirm_large_batch"):
        _planned_children = {
            _amazon_seller_sku(v, p)
            for gps in _groups.values()
            for p in gps
            for v in (p.get("variants") or [])
            if _amazon_seller_sku(v, p)
        }
        _planned_children.update(
            _amazon_seller_sku(v, p)
            for p in _singles
            for v in (p.get("variants") or [])
            if _amazon_seller_sku(v, p)
        )
        if len(_planned_children) > 100:
            raise HTTPException(
                status_code=400,
                detail=(f"Güvenlik: filtre {len(_planned_children)} Amazon varyantıyla eşleşti. "
                        "Toplu aktarım için kapsamı daraltın veya confirm_large_batch=true verin."),
            )
    for gkey, gps in _groups.items():
        if gps:
            await _list_family(gps, _gk_sku[gkey])
    for sp in _singles:
        await _list_family([sp], "")
    return {"success": True, "dry_run": not ALLOW_WRITE, "pushed": pushed, "successful": pushed,
            "failed": failed, "skipped": skipped, "count": len(results), "results": results[:200],
            "message": (f"{'DRY-RUN — ' if not ALLOW_WRITE else ''}{pushed} gönderildi / {failed} hata"
                        + (f" / {skipped} atlandı" if skipped else ""))}


async def validate_products_for_amazon(payload: dict, current_user: dict) -> dict:
    """Aktarım ÖNCESİ doğrulama: filtreye uyan her ürün/varyant için Amazon'a gidecek attrs'ı
    kur, productType + zorunlu şema alanları + temel alanları (barkod/fiyat) kontrol et, eksikleri
    raporla. Canlıya YAZMAZ. FilteredPushPanel bunu 'Doğrula'da çağırır."""
    payload = payload or {}
    _, _, mp = await get_valid_access_token()
    _bset = {str(x).strip() for x in (payload.get("barcodes") or []) if str(x).strip()}
    _sset = {str(x).strip() for x in (payload.get("stock_codes") or []) if str(x).strip()}
    _pset = {str(x).strip() for x in (payload.get("product_ids") or []) if str(x).strip()}
    _filtered = bool(_bset or _sset or _pset)
    # Doğrulama ve gerçek aktarım aynı aktif ürün kapsamını kullanmalı; aksi halde
    # kullanıcı ekranda pasif "(Kopya)" varyantları hazır sanıp gönderebilir.
    q = {"is_active": True}
    if _pset:
        q["id"] = {"$in": list(_pset)}
    products = await db.products.find(q, {"_id": 0}).to_list(length=None)
    markup = await _amazon_markup()
    results, valid, invalid, top_missing = [], 0, 0, {}
    seen_skus = set()
    for p in products:
        pt, defaults, mappings = await _resolve_amazon_pt_and_defaults(p)
        req = []
        if pt:
            try:
                sch = await _amazon_product_type_schema(pt)
                req = sch.get("required") or []
            except Exception:
                req = []
        price = _amazon_price_of(p, markup)
        list_price = round(float(p.get("price") or 0), 2)
        for v in (p.get("variants") or []):
            bc = str(v.get("barcode") or "").strip()
            sc = str(v.get("stock_code") or "").strip()
            if _filtered and not _amz_code_match(p, v, _bset, _sset, _pset):
                continue
            sku = _amazon_seller_sku(v, p)
            if not sku or sku in seen_skus:
                continue
            seen_skus.add(sku)
            attrs = _amazon_listing_attributes(p, v, pt or "PRODUCT", mp, price,
                                               int(v.get("stock") or 0), p.get("brand"),
                                               default_attrs=defaults, attr_mappings=mappings,
                                               list_price=list_price)
            missing = []
            if not pt:
                missing.append("productType (kategori eşleştir)")
            for r in req:
                if r not in attrs:
                    missing.append(r)
            if not bc:
                missing.append("barkod (EAN)")
            if price <= 0:
                missing.append("fiyat")
            ok = len(missing) == 0
            # CategoryMapping ortak doğrulama tablosunun kontratını kullan. Eski
            # product/sku/valid adları yüzünden Amazon satırları boş ve eksik görünüyordu.
            results.append({
                "id": f"{p.get('id') or sku}:{sku}",
                "product_id": p.get("id"),
                "name": p.get("name") or "-",
                "category_name": p.get("category_name") or p.get("category") or "",
                "stock_code": sku,
                "is_valid": ok,
                "errors": [],
                "missing_required_attrs": [{"name": item} for item in missing],
            })
            if ok:
                valid += 1
            else:
                invalid += 1
                for m0 in missing:
                    top_missing[m0] = top_missing.get(m0, 0) + 1
    top = sorted(top_missing.items(), key=lambda kv: -kv[1])[:12]
    return {"success": True, "total": valid + invalid,
            "valid_count": valid, "invalid_count": invalid,
            "results": results[:300],
            "top_missing_attrs": [{"name": a, "count": c} for a, c in top]}


# ============================== ENDPOINTS ==============================

@router.get("/status")
async def spapi_status(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    cfg = await _get_config()
    if not cfg:
        return {"configured": False, "connected": False}
    return {
        "configured": True,
        "connected": bool(cfg.get("refresh_token_enc")),
        "has_client_secret": bool(cfg.get("client_secret_enc")),
        "client_id": cfg.get("client_id"),
        "marketplace_id": cfg.get("marketplace_id") or DEFAULT_MARKETPLACE_ID,
        "region": cfg.get("region") or DEFAULT_REGION,
        "app_id": cfg.get("app_id"),
        "markup": cfg.get("markup") or 0,
        "selling_partner_id": cfg.get("selling_partner_id") or "",
        "last_test": cfg.get("last_test"),
        "updated_at": cfg.get("updated_at"),
    }


@router.post("/config")
async def spapi_save_config(payload: dict, current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Kimlik bilgilerini kaydeder. Secret'lar AES vault formatında şifrelenir.
    payload: { client_id, client_secret?, refresh_token?, app_id?, marketplace_id?, region? }
    """
    from security.crypto import encrypt
    existing = await _get_config() or {}
    update = {
        "key": CONFIG_KEY,
        "client_id": (payload.get("client_id") or existing.get("client_id") or "").strip(),
        "app_id": (payload.get("app_id") or existing.get("app_id") or "").strip(),
        "marketplace_id": (payload.get("marketplace_id") or existing.get("marketplace_id") or DEFAULT_MARKETPLACE_ID).strip(),
        "region": (payload.get("region") or existing.get("region") or DEFAULT_REGION).strip(),
        "updated_at": _now_iso(),
        "updated_by": current_user.get("email"),
    }
    # Amazon kâr marjı (%) — marjlı fiyat için (listeleme + stok/fiyat push).
    if payload.get("markup") is not None and str(payload.get("markup")).strip() != "":
        try:
            update["markup"] = float(payload.get("markup"))
        except Exception:
            pass
    # Merchant Token / Selling Partner ID — listeleme + stok/fiyat push için gerekli.
    # Normalde OAuth consent akışında gelir; elle de girilebilir (Seller Central → Ayarlar →
    # Hesap Bilgileri → Merchant Token, ör. A2XXXXXXXX).
    if payload.get("selling_partner_id"):
        update["selling_partner_id"] = str(payload.get("selling_partner_id")).strip()
    if payload.get("client_secret"):
        update["client_secret_enc"] = encrypt(payload["client_secret"].strip())
    if payload.get("refresh_token"):
        update["refresh_token_enc"] = encrypt(payload["refresh_token"].strip())
        # token değişti -> cache temizle
        update["access_token"] = None
        update["access_expires_at"] = 0
    await db.integration_settings.update_one(
        {"key": CONFIG_KEY},
        {"$set": update, "$setOnInsert": {"created_at": _now_iso()}},
        upsert=True,
    )
    # Denetim: kimlik bilgisi değişikliği loglanır (secret DEĞERİ loglanmaz).
    await _log_spapi_call(
        "config_save", ok=True, actor=current_user.get("email", ""),
    )
    return {"success": True}


@router.post("/test")
async def spapi_test(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Token üretip getMarketplaceParticipations ile bağlantıyı doğrular."""
    res = await _spapi_get("/sellers/v1/marketplaceParticipations")
    ok = res["ok"]
    marketplaces = []
    if ok:
        for p in (res["data"].get("payload") or []):
            mp = p.get("marketplace") or {}
            marketplaces.append({
                "id": mp.get("id"),
                "name": mp.get("name"),
                "country": mp.get("countryCode"),
                "currency": mp.get("defaultCurrencyCode"),
            })
    await db.integration_settings.update_one(
        {"key": CONFIG_KEY},
        {"$set": {"last_test": {"ok": ok, "at": _now_iso(), "status": res["status"]}}},
    )
    if not ok:
        return {"success": False, "status": res["status"], "error": res["data"]}
    return {"success": True, "marketplaces": marketplaces}


@router.get("/orders")
async def spapi_orders(
    days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_permission("integrations.view", any_in_area=True)),
):
    """Son N gün siparişlerini Amazon Orders API'den çeker (örnek/önizleme)."""
    _, _, marketplace_id = await get_valid_access_token()
    created_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = await _spapi_get("/orders/v0/orders", {
        "MarketplaceIds": marketplace_id,
        "CreatedAfter": created_after,
    })
    if not res["ok"]:
        return {"success": False, "status": res["status"], "error": res["data"]}
    payload = res["data"].get("payload") or {}
    orders = payload.get("Orders") or []
    return {
        "success": True,
        "count": len(orders),
        "orders": [{
            "amazon_order_id": o.get("AmazonOrderId"),
            "status": o.get("OrderStatus"),
            "purchase_date": o.get("PurchaseDate"),
            "total": (o.get("OrderTotal") or {}).get("Amount"),
            "currency": (o.get("OrderTotal") or {}).get("CurrencyCode"),
            "items_shipped": o.get("NumberOfItemsShipped"),
            "fulfillment": o.get("FulfillmentChannel"),
        } for o in orders],
    }


@router.post("/orders/pull")
async def spapi_pull_orders_now(
    days: int = Query(3, ge=1, le=30),
    current_user: dict = Depends(require_permission("integrations.amazon")),
):
    """Amazon siparişlerini ŞİMDİ panele çeker (manuel tetik). Otomatik cron ile AYNI
    mantığı kullanır: son `days` gün LastUpdatedAfter penceresi → yeni sipariş insert +
    iptal/kargo durum güncelle. Yeni siparişte (MFN) stok düşülür, FBA'da düşülmez."""
    from scheduler import _run_amazon_auto_orders_pull  # yerel import — döngü önleme
    res = await _run_amazon_auto_orders_pull(lookback_days=days)
    return {"success": True, **(res or {})}


@router.get("/orders/{order_number}/diagnose")
async def spapi_diagnose_order(order_number: str, current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """TEŞHİS — tek Amazon siparişini uçtan uca açar: (1) config bayrakları, (2) panelde kayıtlı
    sipariş (isim/kalem/eşleşme/resim), (3) Amazon'un DÖNDÜRDÜĞÜ ham isim + SellerSKU/ASIN,
    (4) her SellerSKU için Meluni ürün eşleşme denemesi (neden resim/stok gelmiyor GÖRÜLÜR).
    'Trendyol'da nasıl oluyorsa' problemini kesin teşhis için."""
    from .integrations_common import _meluni_match_for_codes, _meluni_product_image

    out = {
        "order_number": order_number,
        "flags": {
            "restricted_allowed": RESTRICTED_ALLOWED,
            "allow_write": ALLOW_WRITE,
        },
    }

    # (2) Panelde kayıtlı sipariş
    stored = await db.orders.find_one({"order_number": order_number, "platform": "amazon"}, {"_id": 0})
    if stored:
        sa = stored.get("shipping_address") or {}
        ba = stored.get("billing_address") or {}
        out["stored"] = {
            "found": True,
            "status": stored.get("status"),
            "shipping_name": f"{sa.get('first_name','')} {sa.get('last_name','')}".strip(),
            "billing_name": f"{ba.get('first_name','')} {ba.get('last_name','')}".strip(),
            "shipping_city": sa.get("city", ""),
            "needs_pii_refresh": stored.get("needs_pii_refresh"),
            "pii_attempts": stored.get("pii_attempts"),
            "items_enriched": stored.get("items_enriched"),
            "items": [{
                "product_name": it.get("product_name"),
                "sku": it.get("sku") or it.get("marketplace_sku"),
                "barcode": it.get("barcode"),
                "product_id": it.get("product_id"),
                "matched": it.get("matched"),
                "has_image": bool(it.get("image")),
                "quantity": it.get("quantity"),
            } for it in (stored.get("items") or [])],
        }
    else:
        out["stored"] = {"found": False}

    # (3)+(4) Amazon'dan canlı çek + eşleşme denemesi
    try:
        full = await _fetch_amazon_order_full(order_number)
        items = await _fetch_amazon_order_items(order_number)
        buyer = (full or {}).get("BuyerInfo") or {}
        ship = (full or {}).get("ShippingAddress") or {}
        out["amazon_live"] = {
            "order_status": (full or {}).get("OrderStatus"),
            "fulfillment_channel": (full or {}).get("FulfillmentChannel"),
            "buyer_name_raw": buyer.get("BuyerName", ""),
            "buyer_email_present": bool(buyer.get("BuyerEmail")),
            "ship_name_raw": ship.get("Name", ""),
            "ship_city": ship.get("City", ""),
            "pii_returned": bool(ship or buyer),  # RDT gerçekten PII getirdi mi?
            "items": [{
                "SellerSKU": it.get("SellerSKU"),
                "ASIN": it.get("ASIN"),
                "Title": it.get("Title"),
                "QuantityOrdered": it.get("QuantityOrdered"),
            } for it in (items or [])],
        }
        # Her SellerSKU için Meluni eşleşme denemesi (import ile AYNI aday kodlar)
        match_report = []
        for it in (items or []):
            sku = it.get("SellerSKU") or ""
            asin = it.get("ASIN") or ""
            m = await _meluni_match_for_codes([sku])
            via = "sku"
            asin_barcodes = []
            if not m and asin:
                asin_barcodes = await _amazon_barcodes_for_asin(asin)
                if asin_barcodes:
                    m = await _meluni_match_for_codes(asin_barcodes)
                    if m:
                        via = "asin_barcode"
            if m:
                prod, code, how = m
                match_report.append({
                    "SellerSKU": sku, "ASIN": asin, "matched": True,
                    "matched_via": via, "method": how, "matched_code": code,
                    "asin_barcodes": asin_barcodes,
                    "meluni_product_id": prod.get("id"),
                    "meluni_name": prod.get("name"),
                    "has_image": bool(_meluni_product_image(prod)),
                })
            else:
                # Eşleşmedi → Meluni'te bu koda benzer ne var? (ilk 3 örnek barkod/stok_kodu)
                sample = []
                async for p in db.products.find(
                    {"is_active": True}, {"_id": 0, "name": 1, "variants.barcode": 1, "variants.stock_code": 1}
                ).limit(3):
                    for v in (p.get("variants") or [])[:2]:
                        sample.append({"barcode": v.get("barcode"), "stock_code": v.get("stock_code")})
                match_report.append({
                    "SellerSKU": sku, "ASIN": asin, "matched": False,
                    "asin_barcodes": asin_barcodes,
                    "hint": ("Ne SellerSKU ne de ASIN barkodu (EAN/UPC) Meluni variants.barcode/"
                             "stock_code ile eşleşti. asin_barcodes boşsa Amazon katalogda barkod yok."),
                    "meluni_ornek_kodlar": sample[:4],
                })
        out["match_report"] = match_report
    except Exception as e:
        out["amazon_live_error"] = str(e)

    return out


def _parse_amazon_packing_slip(text: str) -> dict:
    """Amazon sevk irsaliyesi (packing slip) PDF metnini ayrıştırır. PII rolü GEREKTİRMEZ —
    irsaliye satıcının kendi belgesidir. Çıkarır: sipariş no, alıcı adı, adres, il/ilçe/posta, tel."""
    import re as _re
    lines = [l.strip() for l in (text or "").splitlines()]
    joined = "\n".join(lines)
    out = {"order_number": "", "name": "", "address": "", "city": "", "district": "",
           "postal_code": "", "phone": ""}
    m = (_re.search(r"Sipari[şs]\s*No\.?\s*:?\s*([0-9]{3}-[0-9]{7}-[0-9]{7})", joined)
         or _re.search(r"orderId=([0-9]{3}-[0-9]{7}-[0-9]{7})", joined)
         or _re.search(r"\b([0-9]{3}-[0-9]{7}-[0-9]{7})\b", joined))
    out["order_number"] = m.group(1) if m else ""
    mp = _re.search(r"Phone\s*:?\s*([0-9 ]{7,})", joined)
    out["phone"] = (mp.group(1).strip().replace(" ", "") if mp else "")
    # "Alıcı:" bloğu — en temiz isim+adres kaynağı.
    name, addr_lines = "", []
    for i, l in enumerate(lines):
        if l.replace(" ", "").lower().startswith("alıcı:") or l.replace(" ", "").lower() == "alıcı:":
            block, j = [], i + 1
            while j < len(lines):
                lj = lines[j]
                if lj.startswith("Phone") or lj.startswith("Sipariş No") or lj.startswith("Amazon Pazar"):
                    break
                if lj:
                    block.append(lj)
                j += 1
            if block:
                name, addr_lines = block[0], block[1:]
            break
    out["name"] = name
    out["address"] = " ".join(addr_lines).strip()
    # "Mahalle Mh., İl, İlçe, 34738" → il/ilçe/posta.
    for l in addr_lines:
        mm = _re.search(r",\s*([^,]+?),\s*([^,]+?),\s*(\d{5})", l)
        if mm:
            out["city"], out["district"], out["postal_code"] = (
                mm.group(1).strip(), mm.group(2).strip(), mm.group(3).strip())
            break
    if not out["postal_code"]:
        mz = _re.search(r"\b(\d{5})\b", out["address"])
        out["postal_code"] = mz.group(1) if mz else ""
    return out


@router.post("/orders/parse-packing-slip")
async def spapi_parse_packing_slip(file: UploadFile = File(...),
                                   current_user: dict = Depends(require_permission("orders.view"))):
    """Amazon sevk irsaliyesi (PDF) yükle → alıcı adı/adres/il/ilçe/posta/tel çıkar ve
    irsaliyedeki SİPARİŞ NO ile eşleşen paneldeki Amazon siparişini doldur. PII rolü GEREKMEZ."""
    import io as _io
    from security.uploads import read_upload_capped
    raw = await read_upload_capped(file, 15 * 1024 * 1024, "PDF çok büyük (en fazla 15 MB)")
    try:
        from pypdf import PdfReader
        reader = PdfReader(_io.BytesIO(raw))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF okunamadı: {e}")
    parsed = _parse_amazon_packing_slip(text)
    oid = parsed.get("order_number")
    if not oid:
        return {"success": False, "parsed": parsed,
                "error": "İrsaliyede sipariş no bulunamadı (farklı formatta olabilir)."}
    existing = await db.orders.find_one({"order_number": oid, "platform": "amazon"}, {"_id": 1})
    if not existing:
        return {"success": False, "parsed": parsed, "order_found": False,
                "error": f"{oid} panelde Amazon siparişi olarak bulunamadı."}
    nm = parsed.get("name") or ""
    if nm:
        _p = nm.rsplit(" ", 1)
        first, last = _p[0], (_p[1] if len(_p) > 1 else "")
    else:
        first, last = "Amazon", "Müşterisi"
    _set = {
        "shipping_address.first_name": first or "Amazon",
        "shipping_address.last_name": last,
        "shipping_address.address": parsed.get("address", ""),
        "shipping_address.city": parsed.get("city", ""),
        "shipping_address.district": parsed.get("district", ""),
        "shipping_address.postal_code": parsed.get("postal_code", ""),
        "shipping_address.phone": parsed.get("phone", ""),
        "shipping_address.country": "TR",
        "billing_address.first_name": first or "Amazon",
        "billing_address.last_name": last,
        "billing_address.address": parsed.get("address", ""),
        "billing_address.city": parsed.get("city", ""),
        "billing_address.district": parsed.get("district", ""),
        "needs_pii_refresh": False,
        "pii_source": "packing_slip",
        "updated_at": _now_iso(),
    }
    r = await db.orders.update_one({"_id": existing["_id"]}, {"$set": _set})
    return {"success": True, "order_found": True, "updated": r.modified_count > 0,
            "order_number": oid, "parsed": parsed,
            "message": f"{oid}: {first} {last} — adres/telefon dolduruldu."}


@router.get("/product-types")
async def spapi_product_types(keywords: str = Query(""), current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Amazon productType arama (kategori sayfası 'kategori' seçimi için)."""
    return {"success": True, "product_types": await _amazon_search_product_types(keywords)}


@router.get("/product-types/{product_type}/raw-attr")
async def spapi_raw_attr(product_type: str, name: str = Query(...),
                         current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """TEŞHİS — bir attribute'ın HAM Amazon JSON şemasını döndürür (nested composite alt-yapıyı
    görmek için). Örn. rise/leg/outer/closure gerçek alt-alan formatı."""
    _, _, mp = await get_valid_access_token()
    res = await _spapi_get(f"/definitions/2020-09-01/productTypes/{product_type}",
                           {"marketplaceIds": mp, "requirements": "LISTING", "locale": "tr_TR"})
    schema_node = ((res["data"] or {}).get("schema") or {})
    props = schema_node.get("properties") or {}
    link = (schema_node.get("link") or {}).get("resource")
    if not props and link:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(link)
            if r.status_code == 200:
                props = (r.json() or {}).get("properties") or {}
        except Exception:
            pass
    return {"attr": name, "schema": props.get(name)}


@router.get("/product-types/{product_type}/schema")
async def spapi_product_type_schema(product_type: str, current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """productType LISTING attribute şeması (zorunlu/opsiyonel)."""
    return {"success": True, **(await _amazon_product_type_schema(product_type))}


@router.post("/products/sync")
async def spapi_products_sync(payload: dict = Body(default={}),
                              current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Meluni ürünlerini Amazon'a listeler (dry-run/canlı — AMAZON_ALLOW_WRITE)."""
    return await sync_products_to_amazon(payload or {}, current_user)


@router.get("/products/preview")
async def spapi_products_preview(q: str = Query(...), current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """TEK ÜRÜN DRY-RUN ÖNİZLEME — ürünü ada/barkoda/stok koduna göre bul, Amazon'a GİDECEK
    tam payload'u (marjlı fiyat, list_price, görseller, tüm attribute'lar) + eksik zorunlu
    alanları döndür. Canlıya YAZMAZ. Aktarımdan önce 'ne gidecek' görmek için."""
    import re as _re
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Arama (q) gerekli")
    query = {"$or": [
        {"name": {"$regex": _re.escape(q), "$options": "i"}},
        {"variants.barcode": q}, {"variants.stock_code": q},
        {"barcode": q}, {"stock_code": q},
    ]}
    prod = await db.products.find_one(query, {"_id": 0})
    if not prod:
        return {"found": False, "error": f"'{q}' ile aktif ürün bulunamadı"}
    _, _, mp = await get_valid_access_token()
    pt, defaults, mappings = await _resolve_amazon_pt_and_defaults(prod)
    markup = await _amazon_markup()
    price = _amazon_price_of(prod, markup)
    list_price = round(float(prod.get("price") or 0), 2)
    variants = prod.get("variants") or [{}]
    v0 = variants[0]
    attrs = _amazon_listing_attributes(prod, v0, pt or "PRODUCT", mp, price,
                                       int(v0.get("stock") or 0), prod.get("brand"),
                                       default_attrs=defaults, attr_mappings=mappings,
                                       list_price=list_price)
    missing = []
    if pt:
        sch = await _amazon_product_type_schema(pt)
        for r in (sch.get("required") or []):
            if r not in attrs:
                missing.append(r)
    imgs = _product_images(prod, v0)
    return {
        "found": True, "product": prod.get("name"), "product_id": prod.get("id"),
        "product_type": pt or None,
        "product_type_warning": None if pt else "Kategori→productType eşlemesi YOK — kategori eşleştir.",
        "markup_pct": markup, "our_price": price, "list_price": list_price,
        "variant_count": len(variants),
        "variant_sku": (v0.get("stock_code") or v0.get("barcode") or ""),
        "image_count": len(imgs), "images": imgs,
        "missing_required": missing,
        "attribute_keys": sorted(attrs.keys()),
        "attributes": attrs,
        "note": ("DRY-RUN — Amazon'a YAZILMADI. Eksik zorunlu alan varsa kategori Özellik/Değer "
                 "ekranından doldurup tekrar önizle; sağlamsa Ürün Aktar."),
    }


# ============================== STOK / FİYAT / LISTING (yazma) ==============================
# Hepsi Listings Items API 2021-08-01 (PATCH) — Product Listing rolü yeterli.
# Fiyatı YAZMAK için Pricing rolü GEREKMEZ (Pricing rolü sadece rakip/Buy Box fiyatı OKUMAK için).
# İlk fazda ALLOW_WRITE=0 → dry-run; canlı yazma env ile açılır.

@router.get("/listing-report")
async def amazon_listing_report(product_id: str = Query(None), barcode: str = Query(None),
                                sku: str = Query(None), asin: str = Query(None),
                                current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """TEŞHİS: Amazon'daki CANLI listeleme durumu — ürünün (ya da SKU / ASIN'in) her SKU'su için
    Amazon'un döndürdüğü durum, ASIN, issues[] (neden görsel/beden/varyant düşmüş), gönderilen
    görsel/beden/renk/varyasyon alanları + görsel URL'lerinin Amazon botu tarafından çekilebilirliği
    (Cloudflare 403 tespiti). Salt-okunur."""
    import asyncio as _aio
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    inc = "summaries,issues,attributes"
    out = {"seller": seller, "marketplace_id": mp, "listings": [], "image_fetch_check": [], "notes": []}

    # ── Ürün(ler)i çöz ──
    products = []
    if product_id:
        p = await db.products.find_one({"id": product_id}, {"_id": 0})
        if p:
            products = [p]
    elif barcode:
        p = await db.products.find_one({"$or": [{"barcode": barcode}, {"variants.barcode": barcode}]}, {"_id": 0})
        if p:
            products = [p]
    skus = []
    if sku:
        skus.append(("manual", sku))
    if asin and not products and not sku:
        # ASIN → SKU araması (searchListingsItems)
        r = await _spapi_get(f"/listings/2021-08-01/items/{seller}",
                             {"marketplaceIds": mp, "identifiers": asin, "identifiersType": "ASIN",
                              "includedData": inc, "pageSize": 20})
        if r["ok"]:
            for it in ((r["data"] or {}).get("items") or []):
                skus.append(("asin-search", it.get("sku")))
                out["listings"].append(_summarize_listing(it, mp))
            out["notes"].append(f"ASIN aramasıyla {len(skus)} SKU bulundu")
        else:
            out["notes"].append(f"ASIN araması başarısız (HTTP {r['status']}): {str(r['data'])[:200]}")
    if products:
        from .integrations_common import _resolve_stock_code as _rsc
        p0 = products[0]
        code = _rsc(p0)
        if code:
            sibs = await db.products.find(amazon_sibling_query([code]), {"_id": 0}).to_list(50)
            if sibs:
                products = sibs
        parent_cands = {code, f"{p0.get('id')}-P"} - {None, ""}
        for pc in sorted(parent_cands):
            skus.append(("parent?", pc))
        current = set()
        for pp in products:
            for v in (pp.get("variants") or []):
                sk = _amazon_seller_sku(v, pp)
                if sk:
                    current.add(sk)
                    skus.append((f"child:{pp.get('name')}|{v.get('size')}|{v.get('color') or ''}", sk))
        # ESKİ FORMAT (mükerrer) SKU adayları: renk parçası olmadan stok_kodu-beden (WARNING 8801 —
        # "başka bir SKU aynı varyasyon özelliklerine sahip" → bedenler ana ürüne bağlanamıyor).
        for lg in _amazon_legacy_sku_candidates(products, code, current, parent_cands):
            skus.append(("legacy-duplicate?", lg))
        # Görsel çekilebilirlik testi (Amazon görselleri kendi botuyla indirir; Cloudflare bot
        # kuralı 403 verirse görsel HİÇ aktarılmaz) — ilk 3 görsel.
        imgs = _product_images(p0, (p0.get("variants") or [{}])[0])[:3]
        # GÜVENLİK (T11/CHAIN11 SSRF): görsel URL'leri panelde saklanan değerlerdir → yalnız public
        # IP'ye pinli bağlantı, her yönlendirme hop'unda yeniden denetim, bayt sınırı (security_fetch).
        from security_fetch import safe_fetch as _safe_fetch, UnsafeURLError as _UnsafeURL, FetchTooLarge as _TooLarge
        for u in imgs:
            row = {"url": u}
            for label, ua in (("amazonbot", "Amazonbot/0.1 (+https://developer.amazon.com/support/amazonbot)"),
                              ("generic", "Mozilla/5.0 (compatible; ImageFetcher/1.0)")):
                try:
                    rr = await _safe_fetch(u, max_bytes=15 * 1024 * 1024, timeout=15.0, allow_redirects=3,
                                           headers={"User-Agent": ua})
                    row[label] = {"status": rr.status_code, "content_type": rr.content_type,
                                  "bytes": len(rr.content)}
                except _UnsafeURL as e:
                    row[label] = {"error": f"engellendi (SSRF koruması): {str(e)[:100]}"}
                except _TooLarge:
                    row[label] = {"error": "görsel 15 MB sınırını aşıyor"}
                except Exception as e:
                    row[label] = {"error": str(e)[:120]}
            out["image_fetch_check"].append(row)
        if any((r.get("amazonbot") or {}).get("status") == 403 for r in out["image_fetch_check"]):
            out["notes"].append("GÖRSEL ENGELİ: Amazon'un görsel botu görsel URL'lerinden 403 alıyor (Cloudflare bot kuralı). "
                                "Cloudflare → Security → WAF'ta cdn.meluni.com.tr ve api.meluni.com.tr/api/upload/to-jpeg için "
                                "'Skip' kuralı (User-Agent contains 'Amazon') gerekir; aksi halde görseller aktarılmaz.")
    # ── Her SKU için canlı listeleme ──
    seen = set()
    for label, sk in skus:
        if not sk or sk in seen:
            continue
        seen.add(sk)
        if label == "asin-search":
            continue  # zaten eklendi
        r = await _spapi_get(f"/listings/2021-08-01/items/{seller}/{sk}", {"marketplaceIds": mp, "includedData": inc})
        if r["ok"]:
            row = _summarize_listing(r["data"] or {}, mp)
            row["role"] = label
            out["listings"].append(row)
        else:
            out["listings"].append({"sku": sk, "role": label, "found": False, "http": r["status"],
                                    "error": str((r["data"] or {}).get("errors") or r["data"])[:200]})
        await _aio.sleep(0.25)
    n_found = sum(1 for l in out["listings"] if l.get("found", True))
    out["summary"] = {"skus_checked": len(seen), "found_on_amazon": n_found,
                      "with_issues": sum(1 for l in out["listings"] if l.get("issues")),
                      "with_images": sum(1 for l in out["listings"] if l.get("image_count"))}
    return out


def _amazon_legacy_sku_candidates(products: list, code: str, current: set, parent_cands: set) -> list:
    """Eski SKU biçimleri: '<stokkodu>-<beden>' ve '<barkod>' (renk parçasız). Güncel SKU setinde ve
    ana ürün adaylarında OLMAYANLAR döner — Amazon'da bulunursa mükerrerdir (silinmeli)."""
    out = []
    for pp in products:
        for v in (pp.get("variants") or []):
            size = str(v.get("size") or "").strip()
            cands = []
            if code and size:
                cands.append(f"{code}-{size}")
            vcode = str(v.get("stock_code") or "").strip()
            if vcode and size and vcode != code:
                cands.append(f"{vcode}-{size}")
            if v.get("barcode"):
                cands.append(str(v.get("barcode")).strip())
            for c in cands:
                if c and c not in current and c not in parent_cands and c not in out:
                    out.append(c)
    return out[:80]


@router.post("/listing-cleanup")
async def amazon_listing_cleanup(payload: dict = Body(default={}), current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Amazon'daki ESKİ/MÜKERRER çocuk SKU'ları (renk parçasız 'stokkodu-beden' vb.) tespit eder ve
    confirm=true ile SİLER (DELETE listing). Ana ürün SKU'su ve güncel SKU'lar ASLA silinmez.
    body: {"barcode": "...", | "product_id": "...", "confirm": false}"""
    import asyncio as _aio
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    p = None
    if payload.get("product_id"):
        p = await db.products.find_one({"id": payload["product_id"]}, {"_id": 0})
    elif payload.get("barcode"):
        bc = str(payload["barcode"]).strip()
        p = await db.products.find_one({"$or": [{"barcode": bc}, {"variants.barcode": bc}]}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı (barkod / product_id)")
    from .integrations_common import _resolve_stock_code as _rsc
    code = _rsc(p)
    products = [p]
    if code:
        sibs = await db.products.find(amazon_sibling_query([code]), {"_id": 0}).to_list(50)
        if sibs:
            products = sibs
    parent_cands = {code, f"{p.get('id')}-P"} - {None, ""}
    current = {_amazon_seller_sku(v, pp) for pp in products for v in (pp.get("variants") or []) if _amazon_seller_sku(v, pp)}
    found, deleted, errors = [], [], []
    for sk in _amazon_legacy_sku_candidates(products, code, current, parent_cands):
        r = await _spapi_get(f"/listings/2021-08-01/items/{seller}/{sk}", {"marketplaceIds": mp, "includedData": "summaries"})
        if not r["ok"]:
            continue
        s0 = ((r["data"] or {}).get("summaries") or [{}])[0]
        found.append({"sku": sk, "asin": s0.get("asin"), "status": s0.get("status")})
        if payload.get("confirm"):
            d = await _spapi_send("DELETE", f"/listings/2021-08-01/items/{seller}/{sk}", None, {"marketplaceIds": mp})
            if d.get("ok"):
                deleted.append(sk)
            else:
                errors.append({"sku": sk, "http": d.get("status"), "error": str(d.get("data"))[:200]})
        await _aio.sleep(0.3)
    return {"stock_code": code, "current_skus": sorted(current), "parent_skus": sorted(parent_cands),
            "legacy_found": found, "deleted": deleted, "errors": errors,
            "dry_run": not payload.get("confirm"), "write_enabled": bool(ALLOW_WRITE),
            "note": ("Silme için confirm=true gönderin." if not payload.get("confirm") else
                     "Silinen SKU'lar Amazon'da birkaç dakika içinde kalkar; ardından 'Ürün Aktar' ile yeniden senkron yapın.")}


def _summarize_listing(item: dict, mp: str) -> dict:
    """Listings Items API yanıtını teşhis satırına indirger."""
    sums = item.get("summaries") or []
    s0 = sums[0] if sums else {}
    attrs = item.get("attributes") or {}
    def _val(k):
        v = attrs.get(k) or []
        return (v[0] or {}).get("value") if v and isinstance(v[0], dict) else None
    imgs = [k for k in attrs.keys() if k == "main_product_image_locator" or k.startswith("other_product_image_locator")]
    rel = (attrs.get("child_parent_sku_relationship") or [{}])[0]
    return {
        "sku": item.get("sku"), "found": True,
        "asin": s0.get("asin"), "status": s0.get("status"), "item_name": s0.get("itemName"),
        "product_type": s0.get("productType"),
        "parentage": _val("parentage_level"), "variation_theme": ((attrs.get("variation_theme") or [{}])[0]).get("name"),
        "parent_sku": rel.get("parent_sku"),
        "size": _val("size"), "color": _val("color"),
        "image_count": len(imgs), "main_image": (attrs.get("main_product_image_locator") or [{}])[0].get("media_location"),
        "issues": [{"code": i.get("code"), "severity": i.get("severity"), "message": i.get("message"),
                    "attributes": i.get("attributeNames")} for i in (item.get("issues") or [])],
    }


@router.get("/write-status")
async def spapi_write_status(current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Yazma modu açık mı (canlı) yoksa dry-run mu — panelde göstermek için."""
    return {"allow_write": ALLOW_WRITE, "mode": "live" if ALLOW_WRITE else "dry_run"}


@router.post("/inventory/{sku}")
async def spapi_set_inventory(sku: str, payload: dict, current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Stok gönder: Listings Items PATCH fulfillment_availability.
    Body: { quantity:int, product_type?:str, fulfillment_channel_code?:str }"""
    try:
        qty = max(0, int((payload or {}).get("quantity")))
    except Exception:
        raise HTTPException(status_code=400, detail="quantity (tam sayı) gerekli")
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    body = {
        "productType": (payload or {}).get("product_type") or "PRODUCT",
        "patches": [{
            "op": "replace",
            "path": "/attributes/fulfillment_availability",
            "value": [{
                "fulfillment_channel_code": (payload or {}).get("fulfillment_channel_code") or "DEFAULT",
                "quantity": qty,
            }],
        }],
    }
    res = await _spapi_send("PATCH", f"/listings/2021-08-01/items/{seller}/{sku}",
                            body=body, params={"marketplaceIds": mp})
    return {"success": res.get("ok"), "sku": sku, "quantity": qty, **res}


@router.post("/price/{sku}")
async def spapi_set_price(sku: str, payload: dict, current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Fiyat gönder (KENDİ fiyatımız): Listings Items PATCH purchasable_offer.
    Body: { price:float, currency?:str=TRY, product_type?:str }"""
    try:
        price = round(float((payload or {}).get("price")), 2)
        if price <= 0:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="price (>0) gerekli")
    currency = ((payload or {}).get("currency") or "TRY").upper()
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    body = {
        "productType": (payload or {}).get("product_type") or "PRODUCT",
        "patches": [{
            "op": "replace",
            "path": "/attributes/purchasable_offer",
            "value": [{
                "marketplace_id": mp,
                "currency": currency,
                "audience": "ALL",
                "our_price": [{"schedule": [{"value_with_tax": price}]}],
            }],
        }],
    }
    res = await _spapi_send("PATCH", f"/listings/2021-08-01/items/{seller}/{sku}",
                            body=body, params={"marketplaceIds": mp})
    return {"success": res.get("ok"), "sku": sku, "price": price, "currency": currency, **res}


@router.patch("/listing/{sku}")
async def spapi_patch_listing(sku: str, payload: dict, current_user: dict = Depends(require_permission("integrations.amazon"))):
    """Genel listing güncelle: çağıran JSON-patch listesi verir.
    Body: { patches:[{op,path,value}], product_type?:str }. Yeni SKU için PUT gerekiyorsa
    ayrı ele alınır — ilk faz mevcut SKU güncelleme odaklı."""
    patches = (payload or {}).get("patches")
    if not isinstance(patches, list) or not patches:
        raise HTTPException(status_code=400, detail="patches (liste) gerekli")
    seller = await _require_seller_id()
    _, _, mp = await get_valid_access_token()
    body = {"productType": (payload or {}).get("product_type") or "PRODUCT", "patches": patches}
    res = await _spapi_send("PATCH", f"/listings/2021-08-01/items/{seller}/{sku}",
                            body=body, params={"marketplaceIds": mp})
    return {"success": res.get("ok"), "sku": sku, **res}


# ============================== FİNANS + FİYAT OKUMA (read) ==============================

@router.get("/finances")
async def spapi_finances(days: int = Query(30, ge=1, le=180),
                         current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Finansal olaylar (Finances API v0) — PII'siz (tutar/komisyon/ücret; alıcı bilgisi YOK).
    Finance and Accounting rolü gerekir."""
    posted_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = await _spapi_get("/finances/v0/financialEvents",
                           {"PostedAfter": posted_after, "MaxResultsPerPage": 100})
    if not res["ok"]:
        return {"success": False, "status": res["status"], "error": res["data"]}
    groups = (res["data"].get("payload") or {}).get("FinancialEvents") or {}
    # PII'siz özet: yalnız event tipleri + adet (ham finansal detay Amazon'da; burada özet).
    summary = {k: len(v) for k, v in groups.items() if isinstance(v, list)}
    return {"success": True, "event_types": summary, "raw_keys": list(groups.keys())}


@router.get("/pricing/{sku}")
async def spapi_read_pricing(sku: str, current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Amazon fiyatı / offer OKUMA (Product Pricing API v0). Pricing rolü gerekir.
    NOT: Kendi fiyatımızı YAZMAK için bu rol GEREKMEZ; bu yalnız OKUMA içindir."""
    _, _, mp = await get_valid_access_token()
    res = await _spapi_get("/products/pricing/v0/price",
                           {"MarketplaceId": mp, "ItemType": "Sku", "Skus": sku})
    return {"success": res["ok"], "status": res["status"], "data": res.get("data")}


@router.get("/offers/{sku}")
async def spapi_read_offers(sku: str, condition: str = Query("New"),
                            current_user: dict = Depends(require_permission("integrations.view", any_in_area=True))):
    """Offer / Buy Box OKUMA (getListingOffers). Pricing rolü gerekir. PII'siz."""
    _, _, mp = await get_valid_access_token()
    res = await _spapi_get(f"/products/pricing/v0/listings/{sku}/offers",
                           {"MarketplaceId": mp, "ItemCondition": condition})
    return {"success": res["ok"], "status": res["status"], "data": res.get("data")}


def _public_base() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")


@router.get("/authorize-url")
async def spapi_authorize_url(current_user: dict = Depends(require_permission("integrations.amazon"))):
    """OAuth consent URL (website workflow). App ID (solution id) gerektirir."""
    cfg = await _get_config()
    if not cfg or not cfg.get("app_id"):
        raise HTTPException(status_code=400, detail="App ID (Solution ID, amzn1.sp.solution.xxx) kaydedilmemiş")
    if not cfg.get("client_secret_enc"):
        raise HTTPException(status_code=400, detail="Önce Client Secret kaydedin")
    region = cfg.get("region") or DEFAULT_REGION
    base = SELLERCENTRAL_CONSENT.get(region, SELLERCENTRAL_CONSENT["eu"])
    redirect_uri = f"{_public_base()}/api/amazon/spapi/oauth/callback"
    state = _secrets.token_urlsafe(24)
    await db.integration_settings.update_one(
        {"key": CONFIG_KEY}, {"$set": {"oauth_state": state, "oauth_redirect": redirect_uri}}
    )
    url = (f"{base}/apps/authorize/consent?application_id={cfg['app_id']}"
           f"&state={state}&redirect_uri={redirect_uri}&version=beta")
    return {"url": url, "redirect_uri": redirect_uri}


@router.get("/oauth/callback")
async def spapi_oauth_callback(request: Request, spapi_oauth_code: str = None,
                               state: str = None, selling_partner_id: str = None):
    """Amazon OAuth dönüşü — spapi_oauth_code -> refresh_token (vault'a kaydedilir)."""
    from security.crypto import encrypt
    cfg = await _get_config(include_secrets=True)
    frontend = _public_base()
    if not spapi_oauth_code or not cfg:
        return RedirectResponse(url=f"{frontend}/admin/amazon?status=error")
    # GÜVENLİK (CSRF): gelen state MUTLAKA verilmeli VE panelde (authorize-url, require_admin) üretilmiş
    # state ile eşleşmeli. Eskiden `and state` kısa devresi yüzünden state="" gönderilince kontrol
    # ATLANIYORDU. IAM-01: kayıtlı state YOKSA da (panelden yetkilendirme başlatılmamış) RED — aksi hâlde
    # kimliksiz biri kendi Amazon kodunu göndererek mağazanın refresh_token'ını kendi satıcı hesabıyla
    # değiştirebiliyordu (fail-open). State tek kullanımlıktır.
    import hmac as _hmac
    _expected_state = str(cfg.get("oauth_state") or "")
    if not _expected_state or not state or not _hmac.compare_digest(str(state), _expected_state):
        return RedirectResponse(url=f"{frontend}/admin/amazon?status=state_mismatch")
    await db.integration_settings.update_one({"key": CONFIG_KEY}, {"$unset": {"oauth_state": ""}})
    redirect_uri = cfg.get("oauth_redirect") or f"{_public_base()}/api/amazon/spapi/oauth/callback"
    try:
        td = await _exchange_code_for_refresh(cfg["client_id"], cfg["client_secret"], spapi_oauth_code, redirect_uri)
    except HTTPException:
        return RedirectResponse(url=f"{frontend}/admin/amazon?status=exchange_failed")
    refresh_token = td.get("refresh_token")
    if not refresh_token:
        return RedirectResponse(url=f"{frontend}/admin/amazon?status=no_refresh_token")
    await db.integration_settings.update_one(
        {"key": CONFIG_KEY},
        {"$set": {
            "refresh_token_enc": encrypt(refresh_token),
            "selling_partner_id": selling_partner_id,
            "access_token": None, "access_expires_at": 0,
            "updated_at": _now_iso(),
        }},
    )
    return RedirectResponse(url=f"{frontend}/admin/amazon?status=connected")
