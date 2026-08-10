"""
=============================================================================
integrations_temu.py — Temu Open Platform Entegrasyonu
=============================================================================

KAPSAM (7 modül):
  1. Ürün — create / list / get / update / images / variants
  2. Stok — get / update (tek + toplu)
  3. Fiyat — update (tek + toplu)
  4. Sipariş — list / get / confirm / ship / cancel
  5. Müşteri Soruları — list / thread / reply / resolve
  6. İade — list / approve / reject / refund
  7. Webhook — order.created / order.updated / payment.confirmed (verify)

Auth: HMAC-SHA256 signed request (app_key + app_secret + nonce + timestamp).
Base URL `marketplace_accounts.temu.base_url` veritabanından okunur.
Bu sayede kullanıcı Temu'dan satıcı onayı alınca aynı kodla canlıya geçebilir.

Not: Temu'nun Türkiye satıcılarına public API'si henüz açık değil. Endpoint
isimleri ve imzalama şeması Temu Merchant Portal beta dokümanlarına dayanır
ve gerçek credential gelince sadece base_url + endpoint path değişebilir.
=============================================================================
"""
import hmac
import hashlib
import secrets
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .deps import db, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/temu", tags=["Integrations-Temu"])


# -----------------------------------------------------------------------------
# Auth + helper
# -----------------------------------------------------------------------------

async def _get_temu_config() -> Dict[str, Any]:
    """marketplace_accounts.temu kaydından config çek.

    Pazaryeri Hub kimlik bilgilerini `credentials` alt-objesinde ve Temu panel
    isimleriyle (app_key/app_secret/access_token/env) saklar. Bu yüzden önce
    `credentials` okunur; eski düz (top-level) kayıtlar için geriye dönük fallback
    bırakıldı. Önceki sürüm top-level `api_key`/`api_secret` aradığı için Hub'dan
    girilen bilgiler hiç görünmüyordu (her zaman "App Key/App Secret eksik")."""
    acc = await db.marketplace_accounts.find_one({"key": "temu"}, {"_id": 0})
    if not acc:
        raise HTTPException(status_code=400, detail="Temu hesabı tanımlı değil. Önce Pazaryeri Hub'da credential girin.")
    if not acc.get("enabled"):
        raise HTTPException(status_code=400, detail="Temu hesabı aktif değil")
    creds = acc.get("credentials") or {}
    app_key = creds.get("app_key") or creds.get("api_key") or acc.get("api_key") or acc.get("app_key") or ""
    app_secret = creds.get("app_secret") or creds.get("api_secret") or acc.get("api_secret") or acc.get("app_secret") or ""
    access_token = creds.get("access_token") or acc.get("access_token") or ""
    if not app_key or not app_secret:
        raise HTTPException(status_code=400, detail="Temu App Key/App Secret eksik")
    env = str(creds.get("env") or acc.get("env") or acc.get("mode") or "prod").lower()
    is_sandbox = env in ("test", "sandbox")
    base = (creds.get("base_url") or acc.get("base_url")
            or ("https://api-sandbox.temu.com" if is_sandbox else "https://api.temu.com"))
    mall_id = (creds.get("partner_account") or creds.get("mall_id")
               or acc.get("supplier_id") or acc.get("mall_id") or "")
    # Temu Açık Platform gateway router — bölgeye göre. TR dahil "diğer tüm" durumlar EU.
    #   EU:           https://openapi-b-eu.temu.com/openapi/router
    #   US:           https://openapi-b-us.temu.com/openapi/router
    #   Meksika/Japonya: https://openapi-b-global.temu.com/openapi/router
    region = str(creds.get("region") or acc.get("region") or "eu").lower()
    if region in ("us", "usa", "united states"):
        gateway = "https://openapi-b-us.temu.com/openapi/router"
    elif region in ("global", "mx", "mexico", "jp", "japan"):
        gateway = "https://openapi-b-global.temu.com/openapi/router"
    else:  # eu + tr + diğer tüm durumlar
        gateway = "https://openapi-b-eu.temu.com/openapi/router"
    gateway = creds.get("gateway") or acc.get("gateway") or gateway
    # GÜVENLİK: app_secret / access_token at-rest şifreli (v1:...) olabilir → çöz.
    try:
        from security.crypto import decrypt as _dec_secret
        app_secret = _dec_secret(app_secret) or app_secret
        access_token = _dec_secret(access_token) or access_token
    except Exception:
        pass
    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "mall_id": mall_id,
        "access_token": access_token,
        "base_url": base.rstrip("/"),
        "gateway": gateway,
    }


def _sign(params: Dict[str, Any], secret: str) -> str:
    """HMAC-SHA256 imza — alfabetik sıralı k=v&k=v string'i secret ile imzalar."""
    sorted_items = sorted({k: v for k, v in params.items() if k != "signature"}.items())
    canonical = "&".join(f"{k}={v}" for k, v in sorted_items)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


async def _temu_request(method: str, path: str, *, params: Optional[Dict] = None, json_body: Optional[Dict] = None) -> Dict:
    """Imzalı Temu API çağrısı."""
    cfg = await _get_temu_config()
    p = dict(params or {})
    if json_body:
        p.update(json_body)
    p.setdefault("app_key", cfg["app_key"])
    p.setdefault("timestamp", int(time.time()))
    p.setdefault("nonce", secrets.token_hex(8))
    if cfg.get("access_token"):
        p["access_token"] = cfg["access_token"]
    p["signature"] = _sign(p, cfg["app_secret"])

    url = f"{cfg['base_url']}{path}"
    async with httpx.AsyncClient(timeout=30) as c:
        if method.upper() == "GET":
            r = await c.get(url, params=p)
        else:
            body = dict(json_body or {})
            body.update({k: p[k] for k in ("app_key", "timestamp", "nonce", "signature", "access_token") if k in p})
            r = await c.request(method.upper(), url, json=body, params=params or None,
                                 headers={"Content-Type": "application/json"})
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}
    ok = r.status_code in (200, 201) and (data.get("success") or data.get("code") in (0, "0"))

    # log_integration_event integrations.py'ye bağlı — geç import
    try:
        from .integrations import log_integration_event
        await log_integration_event(
            "temu", f"{method} {path}", "api", "",
            "success" if ok else "error",
            f"HTTP {r.status_code} → {str(data)[:300]}",
            {"request": {"params": params, "body": json_body}, "response": data},
        )
    except Exception:
        pass

    if not ok:
        raise HTTPException(status_code=502, detail=f"Temu API: {data.get('message') or data.get('error') or r.text[:300]}")
    return data.get("data", data)


# -----------------------------------------------------------------------------
# GERÇEK Temu Açık Platform çağrısı (gateway router + MD5 imza)
# Yukarıdaki _temu_request/_sign varsayımsal REST scaffold'dur; aşağıdaki katman
# Temu'nun gerçek açık API'sini kullanır: tek gateway router URL'ine POST + `type`
# alanı (örn. bg.local.goods.cats.get) + MD5 imza (params sıralı, app_secret
# sandviç, hex UPPERCASE). Mevcut scaffold endpoint'lerine dokunulmadı.
# -----------------------------------------------------------------------------

def _sign_openapi(params: Dict[str, Any], secret: str) -> str:
    """Temu açık API MD5 imzası.

    Algoritma: `sign` hariç tüm parametreler key'e göre alfabetik sıralanır;
    her biri `key+value` (value dict/list ise compact JSON) olarak birleştirilir;
    başına ve sonuna app_secret eklenir; MD5 hex UPPERCASE.
    """
    parts = []
    for k in sorted(params.keys()):
        if k == "sign":
            continue
        v = params[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        parts.append(f"{k}{v}")
    pre = f"{secret}{''.join(parts)}{secret}"
    return hashlib.md5(pre.encode("utf-8")).hexdigest().upper()


async def temu_openapi_call(api_type: str, business_params: Optional[Dict] = None) -> Dict:
    """İmzalı Temu açık API çağrısı (gateway router POST). Ham JSON yanıt döner."""
    cfg = await _get_temu_config()
    if not cfg.get("access_token"):
        raise HTTPException(status_code=400, detail="Temu access_token yok. Önce Temu uygulamasını mağazaya yetkilendirin.")
    payload: Dict[str, Any] = dict(business_params or {})
    payload["type"] = api_type
    payload["app_key"] = cfg["app_key"]
    payload["access_token"] = cfg["access_token"]
    payload["data_type"] = "JSON"
    payload["timestamp"] = str(int(time.time()))  # Temu: STRING, 10 haneli UNIX saniye
    payload["sign"] = _sign_openapi(payload, cfg["app_secret"])
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(cfg["gateway"], json=payload, headers={"Content-Type": "application/json"})
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:800], "http_status": r.status_code}
    return data


async def temu_fetch_child_categories(parent_cat_id: Any = 0) -> Dict:
    """bg.local.goods.cats.get — parentCatId verilirse o kategorinin altları döner.

    Temu spec: "parentCatId verilmezse tüm ANA kategoriler döner." Bu yüzden kök
    (0/None/boş) için parentCatId hiç gönderilmez; alt seviyeler için catId geçilir.
    """
    business: Dict[str, Any] = {}
    try:
        pid = int(parent_cat_id)
    except Exception:
        pid = parent_cat_id
    if pid not in (0, None, ""):
        business["parentCatId"] = pid
    return await temu_openapi_call("bg.local.goods.cats.get", business)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class ProductCreateReq(BaseModel):
    name: str
    description: str = ""
    category_id: str
    price: float
    currency: str = "USD"
    images: List[str] = []
    attributes: Dict[str, Any] = {}
    variants: List[Dict[str, Any]] = []


class StockUpdateReq(BaseModel):
    items: List[Dict[str, Any]]  # [{product_id, quantity, sku?}]


class PriceUpdateReq(BaseModel):
    items: List[Dict[str, Any]]  # [{product_id, price, sku?}]


class ShipReq(BaseModel):
    tracking_number: str
    carrier: str
    ship_date: Optional[str] = None


class ReplyReq(BaseModel):
    message: str
    attachments: List[str] = []


class ReturnDecisionReq(BaseModel):
    refund_amount: Optional[float] = 0
    reason: Optional[str] = ""
    instructions: Optional[str] = ""


# =============================================================================
# 0) TANI — Backend egress IP (Temu IP whitelist için)
# =============================================================================

@router.get("/egress-ip")
async def temu_egress_ip(current_user=Depends(require_admin)):
    """Backend'in dışarı çıkış (egress) IP'sini döner — Temu IP whitelist için.

    Temu uygulamasının IP whitelist alanına yazılacak adres BUDUR (kişisel IP'in
    değil). Railway dinamik egress kullanıyorsa bu değer deploy/restart'ta
    değişebilir; sabitlemek için Railway Static Outbound IP (Pro) gerekir.
    """
    sources = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/all.json",
        "https://checkip.amazonaws.com",
    ]
    results = []
    ip = None
    async with httpx.AsyncClient(timeout=8) as c:
        for url in sources:
            try:
                r = await c.get(url, headers={"Accept": "application/json"})
                got = None
                try:
                    j = r.json()
                    got = j.get("ip") or j.get("ip_addr") or j.get("address")
                except Exception:
                    got = r.text.strip()
                if got:
                    got = str(got).strip()
                results.append({"source": url, "ip": got})
                if got and not ip:
                    ip = got
            except Exception as e:
                results.append({"source": url, "error": str(e)[:120]})
    return {
        "egress_ip": ip,
        "detail": results,
        "note": ("Bu IP'yi Temu IP whitelist'e ekleyin. Railway dinamikse "
                 "deploy/restart'ta değişebilir; Railway Static Outbound IP (Pro) "
                 "ile sabitleyin."),
    }


# =============================================================================
# 1) ÜRÜN
# =============================================================================

@router.post("/products")
async def temu_create_product(req: ProductCreateReq, current_user=Depends(require_admin)):
    # 🎯 FACETTE SABİT VARSAYILANLAR (gap-fill): ürünün taşımadığı ortak özellikleri
    # (Menşei/Cinsiyet/Yaş Grubu/Ortam/Ek Özellik/Kutu Durumu/Persona/Performans +
    # Üretici/İthalatçı GPSR) Temu attribute'larına ekle. Mevcut değer DOKUNULMAZ.
    from facette_defaults import FACETTE_FIXED_ATTR_DEFAULTS, FACETTE_COMPANY, _norm as _fnorm
    attrs_in = dict(req.attributes or {})
    _have = {_fnorm(k) for k in attrs_in}
    for k, v in FACETTE_FIXED_ATTR_DEFAULTS.items():
        if _fnorm(k) not in _have:
            attrs_in[k] = v
    # BEYAZ ETİKET: GPSR üretici bilgisi merkezî Firma Bilgileri'nden (varsa) — koddan değil.
    try:
        from company import get_company as _get_company
        from routes.deps import db as _db
        _co = await _get_company(_db)
    except Exception:
        _co = {}
    for label, val in (("Üretici Adı", _co.get("company_name") or FACETTE_COMPANY["company_name"]),
                       ("Üretici Mail Adresi", _co.get("email") or FACETTE_COMPANY["email"]),
                       ("Üretici Adres Bilgisi", _co.get("address") or FACETTE_COMPANY["address"])):
        if _fnorm(label) not in _have:
            attrs_in[label] = val
    payload = {
        "product_name": req.name,
        "product_description": req.description,
        "category_id": req.category_id,
        "base_price": req.price,
        "currency": req.currency,
        "primary_image": req.images[0] if req.images else "",
        "additional_images": req.images[1:],
        "attributes": [{"attribute_name": k, "attribute_value": str(v)} for k, v in attrs_in.items()],
        "variants": req.variants,
    }
    return await _temu_request("POST", "/product/create", json_body=payload)


@router.get("/products")
async def temu_list_products(page: int = 1, page_size: int = 50, status: Optional[str] = None,
                              search: Optional[str] = None, current_user=Depends(require_admin)):
    params = {"page": page, "page_size": min(page_size, 100)}
    if status: params["status"] = status
    if search: params["search_term"] = search
    return await _temu_request("GET", "/product/list", params=params)


@router.get("/products/{product_id}")
async def temu_get_product(product_id: str, current_user=Depends(require_admin)):
    return await _temu_request("GET", f"/product/{product_id}/details", params={"product_id": product_id})


@router.put("/products/{product_id}")
async def temu_update_product(product_id: str, payload: dict, current_user=Depends(require_admin)):
    return await _temu_request("PUT", f"/product/{product_id}/update", json_body={"product_id": product_id, "updates": payload})


# =============================================================================
# 2) STOK
# =============================================================================

@router.post("/stock/update")
async def temu_update_stock(req: StockUpdateReq, current_user=Depends(require_admin)):
    if len(req.items) == 1:
        it = req.items[0]
        return await _temu_request("POST", "/inventory/update-stock", json_body=it)
    return await _temu_request("POST", "/inventory/bulk-update-stock", json_body={"updates": req.items})


@router.get("/stock/{product_id}")
async def temu_get_stock(product_id: str, sku: Optional[str] = None, current_user=Depends(require_admin)):
    params = {"product_id": product_id}
    if sku: params["sku"] = sku
    return await _temu_request("GET", "/inventory/stock-status", params=params)


# =============================================================================
# 3) FİYAT
# =============================================================================

@router.post("/price/update")
async def temu_update_price(req: PriceUpdateReq, current_user=Depends(require_admin)):
    if len(req.items) == 1:
        return await _temu_request("POST", "/pricing/update-price", json_body=req.items[0])
    return await _temu_request("POST", "/pricing/bulk-update-prices", json_body={"updates": req.items})


# =============================================================================
# 4) SİPARİŞ
# =============================================================================

@router.get("/orders")
async def temu_list_orders(page: int = 1, page_size: int = 50, status: Optional[str] = None,
                            start_date: Optional[str] = None, end_date: Optional[str] = None,
                            current_user=Depends(require_admin)):
    params = {"page": page, "page_size": min(page_size, 100)}
    if status: params["status"] = status
    if start_date: params["start_date"] = start_date
    if end_date: params["end_date"] = end_date
    return await _temu_request("GET", "/orders/list", params=params)


@router.get("/orders/{order_id}")
async def temu_get_order(order_id: str, current_user=Depends(require_admin)):
    return await _temu_request("GET", f"/orders/{order_id}/details", params={"order_id": order_id})


@router.post("/orders/{order_id}/confirm")
async def temu_confirm_order(order_id: str, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/orders/{order_id}/status", json_body={"order_id": order_id, "status": "confirmed"})


@router.post("/orders/{order_id}/ship")
async def temu_ship_order(order_id: str, req: ShipReq, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/orders/{order_id}/ship", json_body={
        "order_id": order_id,
        "tracking_number": req.tracking_number,
        "carrier": req.carrier,
        "ship_date": req.ship_date or datetime.now(timezone.utc).isoformat(),
    })


@router.post("/orders/{order_id}/cancel")
async def temu_cancel_order(order_id: str, reason: str = Query("Stok yok"), current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/orders/{order_id}/status",
                                json_body={"order_id": order_id, "status": "cancelled", "reason": reason})


# =============================================================================
# 5) MÜŞTERİ SORULARI
# =============================================================================

@router.get("/messages")
async def temu_list_messages(page: int = 1, page_size: int = 20, status: Optional[str] = None,
                              order_id: Optional[str] = None, current_user=Depends(require_admin)):
    params = {"page": page, "page_size": min(page_size, 50)}
    if status: params["status"] = status
    if order_id: params["order_id"] = order_id
    return await _temu_request("GET", "/messages/list", params=params)


@router.get("/messages/{thread_id}")
async def temu_get_thread(thread_id: str, current_user=Depends(require_admin)):
    return await _temu_request("GET", f"/messages/thread/{thread_id}", params={"thread_id": thread_id})


@router.post("/messages/{thread_id}/reply")
async def temu_reply(thread_id: str, req: ReplyReq, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/messages/thread/{thread_id}/reply", json_body={
        "thread_id": thread_id, "message": req.message, "attachments": req.attachments,
    })


@router.post("/messages/{thread_id}/resolve")
async def temu_resolve(thread_id: str, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/messages/thread/{thread_id}/resolve",
                                json_body={"thread_id": thread_id, "status": "resolved"})


# =============================================================================
# 6) İADE
# =============================================================================

@router.get("/returns")
async def temu_list_returns(page: int = 1, page_size: int = 20, status: Optional[str] = None,
                             current_user=Depends(require_admin)):
    params = {"page": page, "page_size": min(page_size, 50)}
    if status: params["status"] = status
    return await _temu_request("GET", "/returns/list", params=params)


@router.post("/returns/{return_id}/approve")
async def temu_approve_return(return_id: str, req: ReturnDecisionReq, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/returns/{return_id}/approve", json_body={
        "return_id": return_id, "action": "approve",
        "refund_amount": req.refund_amount, "return_instructions": req.instructions,
    })


@router.post("/returns/{return_id}/reject")
async def temu_reject_return(return_id: str, req: ReturnDecisionReq, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/returns/{return_id}/reject",
                                json_body={"return_id": return_id, "action": "reject", "reason": req.reason or "Rejected"})


@router.post("/returns/{return_id}/refund")
async def temu_refund(return_id: str, req: ReturnDecisionReq, current_user=Depends(require_admin)):
    return await _temu_request("POST", f"/returns/{return_id}/refund", json_body={
        "return_id": return_id, "refund_amount": req.refund_amount, "reason": req.reason or "Customer return",
    })


# =============================================================================
# 7) WEBHOOK
# =============================================================================

@router.post("/webhook")
async def temu_webhook(request: Request):
    """Temu push notifications.
    Signature header X-Temu-Signature ile verify edilir (app_secret tabanlı HMAC-SHA256).
    """
    cfg = await _get_temu_config()
    body = await request.body()
    sig = request.headers.get("X-Temu-Signature", "")
    expected = hmac.new(cfg["app_secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = data.get("event_type", "unknown")
    await db.temu_webhook_events.insert_one({
        "event_type": event,
        "data": data,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "processed": False,
    })

    # Sipariş webhook'u → ana orders koleksiyonuna düş
    if event in ("order.created", "order.updated"):
        oid = data.get("order_id")
        if oid:
            # O5: Pipeline kanalı `platform` alanında tutar (marketplace değil) ve durumlar
            # dahili katalogla eşlenmeli — aksi halde Temu siparişleri platform filtrelerinde
            # görünmez ve tanınmayan ham durum stringi taşırdı.
            _raw_status = str(data.get("status") or "").lower().strip()
            _status_map = {
                "pending": "pending", "created": "pending", "unpaid": "awaiting_payment",
                "paid": "confirmed", "confirmed": "confirmed", "processing": "confirmed",
                "shipped": "shipped", "in_transit": "shipped", "delivered": "delivered",
                "completed": "delivered", "cancelled": "cancelled", "canceled": "cancelled",
                "refunded": "refunded", "returned": "returned",
            }
            _internal = _status_map.get(_raw_status, "pending")
            await db.orders.update_one(
                {"platform": "temu", "marketplace_order_id": oid},
                {"$set": {"platform": "temu", "marketplace": "temu",
                          "marketplace_order_id": oid, "raw_data": data,
                          "status": _internal, "marketplace_status_raw": _raw_status,
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
    return {"status": "received", "event_type": event}
