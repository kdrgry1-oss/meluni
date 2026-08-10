"""
integrations_hepsiburada.py — Hepsiburada (HB) pazaryeri entegrasyonu
(katalog/kategori attribute/ürün-stok-fiyat push/sipariş/iade).
2026-07-01 refactor: integrations.py'den ayrıştırıldı (bkz. integrations_common.py başlığı).
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Response, BackgroundTasks, Request, Body, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import os
import base64
import uuid
import re
import xml.etree.ElementTree as ET
import httpx
import hashlib

from .deps import db, logger, get_current_user, require_admin, generate_id, generate_short_id
from facette_defaults import facette_fixed_value_for  # tüm-pazaryeri sabit varsayılan (gap-fill)

router = APIRouter(tags=["Integrations-Hepsiburada"])

from .integrations_common import (
    _build_product_query_from_payload,
    _decrement_stock_for_imported_order,
    _dedupe_products_by_stock_code,
    _facette_match_for_codes,
    _facette_product_image,
    _hb_norm,
    _resolve_stock_code,
    _to_float_tr,
    log_integration_event,
)

# ---- Request/response modelleri ----
class HbOrderPreviewReq(BaseModel):
    begin_date: Optional[str] = None   # ISO: 2026-06-01T00:00:00
    end_date: Optional[str] = None
    order_number: Optional[str] = None

class HbOrderImportReq(BaseModel):
    orders: List[dict]

class HbBulkListingReq(BaseModel):
    items: List[dict]                 # [{merchantSku?, hepsiburadaSku?, price?, availableStock?}]
    update_stock: bool = True
    update_price: bool = True

class HbPackageReq(BaseModel):
    line_items: List[dict]            # [{id, quantity}]
    parcel_quantity: int = 1
    deci: Optional[int] = None

class HbInvoiceReq(BaseModel):
    invoice_link: str

class HbCargoReq(BaseModel):
    cargo_company_short_name: str

class HbCancelReq(BaseModel):
    reason_id: str = "83"

class HbClaimRejectReq(BaseModel):
    reason: int
    merchant_statement: Optional[str] = ""

class HbProductSyncReq(BaseModel):
    product_ids: Optional[List[str]] = None
    category_id: Optional[str] = None


@router.get("/hepsiburada/categories/{category_id}/attributes")
async def get_hepsiburada_category_attributes(category_id: str, current_user: dict = Depends(require_admin)):
    """Bir HB kategorisinin (canli) ozelliklerini doner — urun editorunun HB bolumu icin.
    Once cache, yoksa canli cekip cache'ler."""
    key = int(category_id) if str(category_id).isdigit() else str(category_id)
    cached = await db.hepsiburada_category_attributes.find_one({"category_id": key}, {"_id": 0})
    if cached and cached.get("_v") == 10 and cached.get("attributes") is not None:
        return {"success": True, "attributes": cached.get("attributes", []),
                "media_attributes": cached.get("media_attributes", []),
                "base_attributes": cached.get("base_attributes", []),
                "raw_structure": cached.get("raw_structure", {})}
    from .category_mapping import _fetch_hb_category_attributes
    attrs, err = await _fetch_hb_category_attributes(category_id)
    if err:
        return {"success": False, "attributes": [], "message": err}
    fresh = await db.hepsiburada_category_attributes.find_one({"category_id": key}, {"_id": 0}) or {}
    return {"success": True, "attributes": attrs,
            "media_attributes": fresh.get("media_attributes", []),
            "base_attributes": fresh.get("base_attributes", []),
            "raw_structure": fresh.get("raw_structure", {})}
HB_COMMON_ATTRS = [
    {"key": "cinsiyet", "label": "Cinsiyet"},
]
async def _hb_common_attr_values(norm_name: str):
    """Önbellekteki HB kategorilerinden, adı norm_name olan özelliğin TÜM değerlerini (uniq) toplar.
    Örn 'cinsiyet' -> ['Erkek','Kadın','Unisex',...] (kategoriler arası birleşik liste)."""
    out, seen = [], set()
    try:
        cursor = db.hepsiburada_category_attributes.find({}, {"_id": 0, "attributes": 1})
        async for doc in cursor:
            for a in (doc.get("attributes") or []):
                if _hb_norm(a.get("name")) == norm_name:
                    for v in (a.get("attributeValues") or []):
                        nm = v.get("name")
                        k = _hb_norm(nm)
                        if nm and k not in seen:
                            seen.add(k)
                            out.append(nm)
    except Exception:
        pass
    return sorted(out)
@router.get("/hepsiburada/base-field-mappings")
async def hb_get_base_field_mappings(current_user: dict = Depends(require_admin)):
    """HB temel/sistem alanlarının (Satıcı Stok Kodu / Ürün Adı / Barkod / Marka / KDV / Desi /
    Görsel ...) ürün-kartı kaynağına ya da sabit değere GLOBAL eşleştirmesi. Üst panel için
    alan listesi + kaynak seçenekleri + kayıtlı config döner."""
    from .category_mapping import HB_BASE_FIELDS, HB_PRODUCT_SOURCES
    s = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    saved = s.get("base_field_mappings") or {}
    fields = []
    for f in HB_BASE_FIELDS:
        cfg = saved.get(f["key"]) or {}
        fields.append({
            "key": f["key"], "label": f["label"],
            "default_source": f.get("default_source"),
            "source": cfg.get("source") or f.get("default_source"),
            "default": cfg.get("default", f.get("default_value", "")),
        })
    try:
        markup = float(s.get("default_markup", 0) or 0)
    except Exception:
        markup = 0.0
    price_source = s.get("price_source") or "price"
    if price_source not in _HB_PRICE_SOURCE_KEYS:
        price_source = "price"
    gad = s.get("global_attr_defaults") or {}
    common_attrs = []
    for c in HB_COMMON_ATTRS:
        nk = _hb_norm(c["key"])
        vals = await _hb_common_attr_values(nk)
        common_attrs.append({"key": nk, "label": c["label"], "values": vals,
                             "selected": gad.get(nk) or gad.get(c["key"]) or ""})
    return {"success": True, "fields": fields, "sources": HB_PRODUCT_SOURCES, "saved": saved,
            "markup": markup, "price_source": price_source, "price_sources": HB_PRICE_SOURCES,
            "common_attrs": common_attrs, "global_attr_defaults": gad}
@router.post("/hepsiburada/base-field-mappings")
async def hb_save_base_field_mappings(request: Request, current_user: dict = Depends(require_admin)):
    """{mappings: {key: {source, default}}} kaydeder (db.settings id=hepsiburada)."""
    from .category_mapping import _HB_BASE_BY_KEY
    payload = await request.json()
    mappings = payload.get("mappings") if isinstance(payload, dict) and "mappings" in payload else payload
    clean = {}
    for k, v in (mappings or {}).items():
        if k not in _HB_BASE_BY_KEY or not isinstance(v, dict):
            continue
        clean[k] = {"source": v.get("source") or "", "default": str(v.get("default") or "")}
    set_doc = {"id": "hepsiburada", "base_field_mappings": clean}
    if isinstance(payload, dict) and "markup" in payload:
        try:
            set_doc["default_markup"] = max(0.0, float(payload.get("markup") or 0))
        except Exception:
            pass
    if isinstance(payload, dict) and "price_source" in payload:
        ps = str(payload.get("price_source") or "price")
        set_doc["price_source"] = ps if ps in _HB_PRICE_SOURCE_KEYS else "price"
    if isinstance(payload, dict) and "global_attr_defaults" in payload:
        gin = payload.get("global_attr_defaults") or {}
        valid = {_hb_norm(c["key"]) for c in HB_COMMON_ATTRS}
        gclean = {}
        if isinstance(gin, dict):
            for k, v in gin.items():
                nk = _hb_norm(k)
                if nk in valid and v not in (None, ""):
                    gclean[nk] = str(v)
        set_doc["global_attr_defaults"] = gclean
    await db.settings.update_one({"id": "hepsiburada"}, {"$set": set_doc}, upsert=True)
    return {"success": True, "saved": clean, "count": len(clean),
            "markup": set_doc.get("default_markup"), "price_source": set_doc.get("price_source"),
            "global_attr_defaults": set_doc.get("global_attr_defaults")}
def _hb_g(d: dict, *keys, default=""):
    for k in keys:
        v = (d or {}).get(k)
        if v not in (None, ""):
            return v
    return default
def _hb_normalize_lines(resp):
    """OMS yanıtını kalem listesine indirger (list | {items|data|orders|content}...)."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("items", "data", "orders", "content", "lineItems", "details"):
            v = resp.get(k)
            if isinstance(v, list):
                return v
        if resp.get("orderNumber") or resp.get("orderId"):
            return [resp]
    return []
def _hb_group_orders(lines):
    """Kalemleri sipariş numarasına göre gruplar -> [{orderNumber, ...ortak..., lines:[...]}]."""
    groups = {}
    for ln in lines:
        no = str(_hb_g(ln, "orderNumber", "orderId", "id", default="?"))
        g = groups.get(no)
        if not g:
            g = {k: ln.get(k) for k in ("orderNumber", "orderId", "orderDate", "status",
                 "customerName", "email", "customerEmail", "totalPrice", "shippingAddress",
                 "shippingAddressDetail", "invoiceAddress", "customer") if k in ln}
            g["orderNumber"] = no
            g["lines"] = []
            groups[no] = g
        g["lines"].append(ln)
    return list(groups.values())
def _hb_is_full_order(d):
    """Tam sipariş objesi mi? (orderNumber/orderId VE içinde kalem dizisi olan)."""
    if not isinstance(d, dict):
        return False
    if not (d.get("orderNumber") or d.get("orderId")):
        return False
    return any(isinstance(d.get(k), list) and d.get(k)
               for k in ("items", "lineItems", "details", "lines", "orderLines"))
def _hb_order_group_from_full(o):
    """Tam sipariş objesinden (by_number / nested) grup üretir:
    order-level alanları (customer, invoice, shippingAddress, totalPrice...) KORUR,
    içindeki kalem dizisini lines olarak çıkarır."""
    if not isinstance(o, dict):
        return {"orderNumber": "?", "lines": []}
    lines = (o.get("items") or o.get("lineItems") or o.get("details")
             or o.get("lines") or o.get("orderLines") or [])
    g = dict(o)
    g["lines"] = lines if isinstance(lines, list) else []
    g["orderNumber"] = str(o.get("orderNumber") or o.get("orderId") or o.get("id") or "?")
    return g
def _hb_orders_from_response(resp):
    """OMS yanıtını sipariş-grubu listesine çevirir; İKİ şekli de ele alır:
      - by_number : tek tam sipariş objesi (kalemler nested) -> order-level KORUNUR
      - get_orders: düz kalem listesi {items:[lineItem,...]} -> orderNumber'a göre gruplanır
    Böylece numarayla çekilen sipariş, müşteri/fatura/kalem bilgisiyle birlikte içe aktarılır."""
    # 1) Tek tam sipariş objesi
    if _hb_is_full_order(resp):
        return [_hb_order_group_from_full(resp)]
    # 2) Liste: tam siparişler mi yoksa düz kalemler mi?
    if isinstance(resp, list):
        if resp and _hb_is_full_order(resp[0]):
            return [_hb_order_group_from_full(o) for o in resp if isinstance(o, dict)]
        return _hb_group_orders(_hb_normalize_lines(resp))
    # 3) Sarmalayıcı dict {items|data|orders|content:[...]}
    if isinstance(resp, dict):
        for k in ("items", "data", "orders", "content"):
            v = resp.get(k)
            if isinstance(v, list) and v:
                if _hb_is_full_order(v[0]):
                    return [_hb_order_group_from_full(o) for o in v if isinstance(o, dict)]
                return _hb_group_orders(v)
        # 4) orderNumber var ama nested kalem yok/boş -> yine de tam sipariş gibi al
        if resp.get("orderNumber") or resp.get("orderId"):
            return [_hb_order_group_from_full(resp)]
    # 5) Fallback (eski davranış)
    return _hb_group_orders(_hb_normalize_lines(resp))
def _hb_money(v, default=0.0):
    """HB OMS fiyatı düz sayı VEYA {amount/value/grossAmount/...} dict olabilir → güvenle float'a indirger."""
    if isinstance(v, dict):
        for k in ("amount", "value", "grossAmount", "totalPrice", "unitPrice", "price", "paidPrice"):
            iv = v.get(k)
            if iv is not None and not isinstance(iv, dict):
                try:
                    return float(iv)
                except Exception:
                    pass
        return float(default)
    try:
        return float(v if v not in (None, "") else default)
    except Exception:
        return float(default)
def map_hepsiburada_order(o: dict) -> dict:
    from datetime import datetime, timezone
    raw_no = str(_hb_g(o, "orderNumber", "orderId", "id"))
    order_number = raw_no if raw_no.upper().startswith("HB") else f"HB{raw_no}"
    lines = o.get("lines") or o.get("items") or []
    items, subtotal = [], 0.0
    for ln in lines:
        try:
            qty = int(_hb_money(_hb_g(ln, "quantity", "qty", default=1), 1)) or 1
        except Exception:
            qty = 1
        unit = _hb_money(_hb_g(ln, "price", "unitPrice", "totalPrice", "amount", default=0))
        # HB alan adları: merchantSKU (SKU büyük!) = bizim varyant urun_id'miz;
        # productBarcode = GERÇEK ürün barkodu; 'barcode' ise KARGO barkodu —
        # eşleşme için asla kullanılmamalı. properties[] Beden/Renk taşır,
        # productImageUrlFormat HB görselidir ({size} yer tutucusu).
        m_sku = _hb_g(ln, "merchantSKU", "merchantSku", "MerchantSku", "merchantsku")
        p_bar = _hb_g(ln, "productBarcode", "ProductBarcode")
        _size = _color = ""
        for pr in (ln.get("properties") or []):
            nm = str((pr or {}).get("name") or "").strip().lower()
            if nm == "beden":
                _size = (pr or {}).get("value") or _size
            elif nm == "renk":
                _color = (pr or {}).get("value") or _color
        img_fmt = _hb_g(ln, "productImageUrlFormat")
        hb_img = ""
        if img_fmt and "{size}" in str(img_fmt):
            hb_img = str(img_fmt).replace("{size}", "424")
        elif img_fmt:
            hb_img = str(img_fmt)
        _pname = _hb_g(ln, "productName", "name", "lineItemName")
        items.append({
            "product_id": m_sku or _hb_g(ln, "sku", "hbSku") or p_bar,
            "merchant_sku": m_sku,
            "hb_sku": _hb_g(ln, "sku", "hbSku"),
            "product_name": _pname,
            "name": _pname,
            "quantity": qty, "unit_price": unit, "price": unit,
            "barcode": p_bar or _hb_g(ln, "barcode"),
            "cargo_barcode": _hb_g(ln, "barcode"),
            "size": _size or _hb_g(ln, "size", "variantValue"),
            "color": _color or _hb_g(ln, "color"),
            "image": hb_img or None,
            "currency": "TRY",
        })
        subtotal += unit * qty
    total = _hb_money(_hb_g(o, "totalPrice", "totalAmount", default=subtotal), subtotal)
    ship = (o.get("shippingAddress") or o.get("shippingAddressDetail")
            or o.get("deliveryAddress") or {})
    inv = o.get("invoiceAddress") or o.get("invoice") or {}
    cust = o.get("customer") or {}
    cust_name = (_hb_g(o, "customerName") or _hb_g(cust, "name")
                 or _hb_g(ship, "name", "firstName") or "Hepsiburada Müşterisi")
    parts = str(cust_name).split(" ", 1)
    # KARGO (Trendyol'daki gibi çekilir): kargo firması + takip no + takip linki. HB bunları
    # sipariş/paket seviyesinde farklı adlarla verebilir; yoksa ilk kargo kaleminden yedeklenir.
    # NOT: shipping_cost yine 0 kalır — pazaryeri kargoyu üstlenir (TY ile aynı kural).
    _l0 = lines[0] if lines else {}
    # HB kargoyu sipariş üstünde, nested paket dizisinde (packages/shipmentPackages) ya da
    # kalem üstünde verebilir — üçünü de tara (ilk dolu olan kazanır).
    _pkgs = (o.get("packages") or o.get("shipmentPackages") or o.get("shipmentPackage")
             or o.get("cargoPackages") or [])
    if isinstance(_pkgs, dict):
        _pkgs = [_pkgs]
    _pk0 = _pkgs[0] if (isinstance(_pkgs, list) and _pkgs) else {}
    def _cg(*keys):
        for src in (o, _pk0, _l0):
            v = _hb_g(src or {}, *keys)
            if v:
                return str(v)
        return ""
    cargo_no = _cg("cargoTrackingNumber", "trackingNumber", "cargoTrackingCode",
                   "shipmentTrackingNumber", "packageTrackingNumber")
    cargo_name = _cg("cargoCompany", "cargoProviderName", "cargoCompanyName",
                     "shippingCompany", "cargoCompanyModelCode", "cargoCompanyShortName")
    cargo_link = _cg("cargoTrackingUrl", "cargoTrackingLink", "trackingUrl")
    return {
        "order_number": order_number, "platform": "hepsiburada", "marketplace": "hepsiburada",
        "hepsiburada_order_number": raw_no, "user_id": None, "items": items,
        "shipping_address": {
            "first_name": parts[0] if parts else "Hepsiburada",
            "last_name": parts[1] if len(parts) > 1 else "Müşterisi",
            "phone": _hb_g(ship, "phoneNumber", "phone", "gsm"),
            "email": _hb_g(o, "email", "customerEmail"),
            "address": _hb_g(ship, "address", "addressDetail", "fullAddress", "detail"),
            "city": _hb_g(ship, "city"), "district": _hb_g(ship, "district", "town"),
            "country": _hb_g(ship, "countryCode", "country", default="TR"),
        },
        "billing_address": {
            "first_name": _hb_g(inv, "name", "firstName") or (parts[0] if parts else ""),
            "last_name": _hb_g(inv, "lastName") or (parts[1] if len(parts) > 1 else ""),
            "phone": _hb_g(inv, "phoneNumber", "phone"),
            "address": _hb_g(inv, "address", "addressDetail", "fullAddress"),
            "city": _hb_g(inv, "city"), "district": _hb_g(inv, "district", "town"),
            "country": _hb_g(inv, "countryCode", "country", default="TR"),
            "company_name": _hb_g(inv, "companyName", "company"),
            "tax_number": _hb_g(inv, "taxNumber", "vkn"), "tax_office": _hb_g(inv, "taxOffice"),
        },
        "subtotal": subtotal, "shipping_cost": 0, "discount_amount": 0, "total": total,
        "payment_method": "marketplace", "payment_status": "paid", "status": "confirmed",
        "marketplace_status": _hb_g(o, "status"), "hb_order_date": _hb_g(o, "orderDate"),
        # Kargo (TY ile aynı alan adları — panel/liste/etiket bu alanları okur)
        "cargo_tracking_number": cargo_no,
        "cargo_provider_name": cargo_name,
        "cargo_tracking_link": cargo_link,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
def _hb_created_at(o):
    from datetime import datetime, timezone
    d = o.get("hb_order_date")
    if isinstance(d, (int, float)) and d > 0:
        try:
            return datetime.fromtimestamp(d / 1000, tz=timezone.utc).isoformat()
        except Exception:
            pass
    if isinstance(d, str) and len(d) >= 10 and "-" in d:
        return d
    return datetime.now(timezone.utc).isoformat()
async def _hb_enrich_items(order_data):
    """HB kalemlerini FACETTE ürünleriyle eşler: görsel + FACETTE product_id + barkod (stok düşümü için) + ad/varyant.
    Eşleşmeyen kalem dokunulmadan kalır (matched=False işaretlenir)."""
    for it in (order_data.get("items") or []):
        codes = [it.get("barcode"), it.get("sku"), it.get("product_id"),
                 it.get("merchant_sku"), it.get("merchantSku"),
                 it.get("hb_sku"), it.get("productBarcode")]
        m = await _facette_match_for_codes(codes)
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
        it["marketplace_sku"] = it.get("product_id")
        it["product_id"] = prod.get("id")
        it["facette_product_id"] = prod.get("id")
        it["barcode"] = vbar or prod.get("barcode") or it.get("barcode")
        img = _facette_product_image(prod)
        if img:
            it["image"] = img
        if prod.get("name"):
            it["product_name"] = prod["name"]
        it["matched"] = True
        it["match_method"] = how
    return order_data
@router.post("/hepsiburada/orders/preview")
async def preview_hepsiburada_orders(req: HbOrderPreviewReq, current_user: dict = Depends(require_admin)):
    """Hepsiburada OMS'ten geçmiş siparişleri tarih aralığı veya sipariş no ile listeler (içe aktarmadan).
    Hata durumunda 200 + {success:False, error, attempted_url} döner ki proxy/timeout mesajı maskelemesin."""
    import asyncio
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        return {"success": False, "error": err}
    try:
        oms_base = client._oms_base()
        mid = client.merchant_id
    except Exception:
        oms_base, mid = "?", "?"
    attempted = f"{oms_base}/orders/merchantid/{mid}"
    try:
        if req.order_number and req.order_number.strip():
            on = req.order_number.strip()
            attempted = f"{oms_base}/orders/merchantid/{mid}/ordernumber/{on}"
            resp = await asyncio.wait_for(asyncio.to_thread(client.get_order_by_number, on), timeout=15)
        else:
            if req.begin_date or req.end_date:
                attempted += f"?beginDate={req.begin_date}&endDate={req.end_date}&offset=0&limit=50"
            else:
                attempted += "?offset=0&limit=50"  # tarihsiz: ödemesi tamamlanmış (Open) listesi
            resp = await asyncio.wait_for(
                asyncio.to_thread(client.get_orders, req.begin_date, req.end_date, 0, 50), timeout=15)
    except asyncio.TimeoutError:
        return {"success": False,
                "error": "HB OMS 15 sn icinde yanit vermedi (oms-external-sit Railway agindan yavas/erisilemez "
                         "gorunuyor). Bu HB SIT tarafi kaynakli; 1-2 dk sonra tekrar deneyin, surekli olursa "
                         "ticket acariz.",
                "attempted_url": attempted}
    except Exception as e:
        return {"success": False, "error": str(e), "attempted_url": attempted}
    grouped = _hb_orders_from_response(resp)
    preview = []
    for g in grouped:
        preview.append(await _hb_enrich_items(map_hepsiburada_order(g)))
    nums = [p["order_number"] for p in preview]
    existing = set()
    if nums:
        async for o in db.orders.find({"order_number": {"$in": nums}, "platform": "hepsiburada"},
                                       {"_id": 0, "order_number": 1}):
            existing.add(o["order_number"])
    for p in preview:
        p["_already_imported"] = p["order_number"] in existing
    return {"success": True, "count": len(preview), "orders": grouped, "preview": preview,
            "attempted_url": attempted,
            "raw_sample": (grouped[:2] if isinstance(grouped, list) else grouped)}
@router.get("/hepsiburada/oms-diag")
async def hepsiburada_oms_diag(on: str = "", key: str = "", current_user: dict = Depends(require_admin)):
    # DENETİM FIX: require_admin eklendi — sabit gömülü anahtar (repo'da açık) tek başına
    # koruma değildi; teşhis ucu efektif olarak public'ti.
    """GEÇİCİ TEŞHİS UCU: Railway backend'inden HB OMS-SIT'e bağlantıyı ölçer. HER ZAMAN 200 döner.
    Tarayıcı adres çubuğundan aç:  /api/integrations/hepsiburada/oms-diag?key=facette_oms_diag&on=<sipariş_no>
    Yorum: list_1.ok=true+düşük ms => OMS erişilebilir (sorun frontend/cache);
           error 'timeout' => oms-external-sit Railway ağından erişilemez/yavaş (HB SIT tarafı, ticket);
           '401/403' => OMS auth; '400' => erişim var, istek formatı."""
    if key != "facette_oms_diag":
        return {"ok": False, "error": "?key=facette_oms_diag gerekli (gecici teshis ucu)"}
    import asyncio, time
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        return {"ok": False, "stage": "client", "error": err}
    info = {"test_mode": getattr(client, "test", None)}
    try:
        info["base"] = client._oms_base(); info["mid"] = client.merchant_id
    except Exception as e:
        info["base_err"] = str(e)

    async def timed(fn, timeout):
        t0 = time.time()
        try:
            r = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
            return {"ok": True, "ms": int((time.time() - t0) * 1000), "sample": str(r)[:300]}
        except asyncio.TimeoutError:
            return {"ok": False, "ms": int((time.time() - t0) * 1000),
                    "error": f"timeout>{timeout}s (OMS yanit vermedi — Railway agindan erisilemez/yavas)"}
        except Exception as e:
            return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": str(e)[:300]}

    results = {"list_1": await timed(lambda: client.get_orders(None, None, 0, 1, read_timeout=10), 11)}
    try:
        from datetime import datetime as _dt, timedelta as _td
        _bd = (_dt.now() - _td(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
        _ed = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
        results["list_dated_14d"] = await timed(lambda: client.get_orders(_bd, _ed, 0, 5, read_timeout=10), 11)
    except Exception as _de:
        results["list_dated_14d"] = {"ok": False, "error": str(_de)[:200]}
    on = (on or "").strip()
    if on:
        results["by_number"] = await timed(lambda: client.get_order_by_number(on), 10)
    return {"ok": True, "info": info, "results": results}
@router.get("/hepsiburada/claims/{claim_number}/media-probe")
async def hb_claim_media_probe(claim_number: str, current_user: dict = Depends(require_admin)):
    """TANI: HB müşteri iade görselleri OMS'te hangi uçtan geliyor — adayları dener (yalnız GET)."""
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    import urllib.parse as _up
    cn = _up.quote(str(claim_number), safe="")
    mid = client.merchant_id
    candidates = [
        f"/claims/number/{cn}",
        f"/claims/number/{cn}/documents",
        f"/claims/number/{cn}/images",
        f"/claims/number/{cn}/attachments",
        f"/claims/number/{cn}/detail",
        f"/claims/merchantid/{mid}/number/{cn}",
    ]
    out = []
    import asyncio as _aio
    for ep in candidates:
        try:
            r = await _aio.wait_for(_aio.to_thread(client._oms_get, ep, None), timeout=12)
            out.append({"endpoint": ep, "status": 200, "body": str(r)[:800]})
        except Exception as e:
            out.append({"endpoint": ep, "status": "err", "body": str(e)[:300]})
    return {"claim_number": claim_number, "results": out}


@router.get("/hepsiburada/orders/import-by-number")
async def hepsiburada_import_by_number(on: str = "", current_user: dict = Depends(require_admin)):
    """Siparişi numarayla OMS'ten çekip doğrudan panele (db.orders) aktarır.
    GÜVENLİK: Artık admin auth zorunlu (eskiden sabit ?key ile kimliksiz stok
    değiştirilebiliyordu — kaldırıldı)."""
    on = (on or "").strip()
    if not on:
        return {"ok": False, "error": "on (siparis no) gerekli"}
    import asyncio, traceback
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        return {"ok": False, "error": err}
    try:
        resp = await asyncio.wait_for(asyncio.to_thread(client.get_order_by_number, on), timeout=12)
        grouped = _hb_orders_from_response(resp)
        if not grouped:
            return {"ok": False, "error": "siparis bulunamadi/bos", "raw_full": resp, "raw": str(resp)[:400]}
        imported = updated = 0
        out = []
        for g in grouped:
            order_data = await _hb_enrich_items(map_hepsiburada_order(g))
            onum = order_data["order_number"]
            existing = await db.orders.find_one({"order_number": onum, "platform": "hepsiburada"})
            if existing:
                _upd = {k: v for k, v in order_data.items() if k != "status"}
                # Kargo alanları BOŞ geldiyse mevcut değeri EZME (henüz kargolanmamış tekrar-senkron).
                for _ck in ("cargo_tracking_number", "cargo_provider_name", "cargo_tracking_link"):
                    if not _upd.get(_ck):
                        _upd.pop(_ck, None)
                # KISMİ İPTAL TESPİTİ: müşteri kalemlerden birini iptal edince HB yanıtındaki
                # toplam adet DÜŞER. Sipariş tamamen iptal DEĞİLKEN adet azaldıysa işaretle →
                # panelde "iptal edilen ürün var" kırmızı noktası yanar.
                def _qsum(o):
                    return sum(int(it.get("quantity") or 1) for it in (o.get("items") or []))
                _old_q, _new_q = _qsum(existing), _qsum(order_data)
                if 0 < _new_q < _old_q and not existing.get("partial_cancelled"):
                    _upd["partial_cancelled"] = True
                    _upd["partial_cancel_at"] = datetime.now(timezone.utc).isoformat()
                await db.orders.update_one({"_id": existing["_id"]}, {"$set": _upd})
                updated += 1
            else:
                order_data["id"] = generate_id()
                order_data["created_at"] = _hb_created_at(order_data)
                await db.orders.insert_one(order_data)
                imported += 1
                try:
                    await _decrement_stock_for_imported_order(order_data, "hepsiburada")
                except Exception:
                    pass  # ürün FACETTE'de yoksa stok düşmez — sipariş yine kaydedildi
            matched = sum(1 for it in order_data.get("items", []) if it.get("matched"))
            out.append({"order_number": onum, "items": len(order_data.get("items", [])), "eslesen": matched})
        return {"ok": True, "imported": imported, "updated": updated, "orders": out,
                "_raw": resp,
                "mesaj": "Siparis(ler) panele aktarildi — Siparisler sayfasinda gorunur."}
    except Exception as e:
        return {"ok": False, "stage": "import", "error": str(e)[:400],
                "trace": traceback.format_exc()[-900:]}
@router.post("/hepsiburada/orders/create-test")
async def create_hepsiburada_test_order(payload: dict = None, current_user: dict = Depends(require_admin)):
    """SADECE TEST (SIT): oms-stub üzerinden bir test siparişi oluşturur; sonra 'Çek' ile panele alınır.
    SKU olarak senin gerçek SIT listing'lerinden bir HBSKU otomatik kullanılır (stub listing arar).
    Override: {sku:"HBV..."} / {skus:[...]} / {body:{...tam gövde...}}."""
    import asyncio
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        return {"success": False, "error": err}
    if not getattr(client, "test", True):
        return {"success": False,
                "error": "Test siparişi yalnızca SANDBOX/TEST modunda oluşturulur. Hepsiburada modunu 'Sandbox' yapın."}
    payload = payload or {}
    body = payload.get("body")
    skus = payload.get("skus") or ([payload["sku"]] if payload.get("sku") else [])

    # Tam gövde de SKU da verilmediyse: gerçek listing'lerden HBSKU çek
    if not body and not skus:
        try:
            lst = await asyncio.to_thread(client.get_listings, 0, 50)
        except Exception as e:
            return {"success": False, "error": f"Listing çekilemedi: {e}"}
        rows = lst.get("listings") if isinstance(lst, dict) else (lst if isinstance(lst, list) else [])

        def _hbsku(r):
            for k in ("hepsiburadaSku", "HepsiburadaSku", "hbSku", "hepsiburada_sku", "sku", "Sku"):
                v = (r or {}).get(k)
                if v:
                    return str(v)
            return None
        for r in (rows or []):
            s = _hbsku(r)
            if s and s not in skus:
                skus.append(s)
            if len(skus) >= 2:
                break
        if not skus:
            return {"success": False,
                    "error": "SIT kataloğunda HBSKU'lu listing bulunamadı. Önce ürünü gönderip listing oluştur "
                             "(gerekirse satışa aç) ya da gövdeye gerçek HBSKU gir.",
                    "listings_sample": (rows or [])[:1]}

    if not body:
        def _li(sku):
            return {"Sku": sku, "Quantity": 1, "Price": {"Amount": 301.4, "Currency": "TRY"},
                    "Vat": 0, "TotalPrice": {"Amount": 301.4, "Currency": "TRY"},
                    "CargoCompanyId": 1, "DeliveryOptionId": 1}
        body = {
            "Customer": {"CustomerId": "dfc8a27f-faae-4cb2-859c-8a7d50ee77be", "Name": "Test User"},
            "DeliveryAddress": {
                "AddressId": "e66765b3-d37d-488c-ae15-47051245dc9b", "Name": "Hepsiburada Office",
                "AddressDetail": "Trump Towers", "Email": "customer@hepsiburada.com.tr",
                "CountryCode": "TR", "PhoneNumber": "902822613231", "AlternatePhoneNumber": "045321538212",
                "Town": "Sisli", "District": "Kustepe", "City": "İstanbul"},
            "LineItems": [_li(s) for s in skus[:2]] or [_li(skus[0])],
        }
    attempted = f"{client.OMS_STUB_SANDBOX}/orders/merchantId/{client.merchant_id}"
    try:
        resp = await asyncio.to_thread(client.create_test_order, body)
    except Exception as e:
        return {"success": False, "error": str(e), "attempted_url": attempted, "used_skus": skus}
    return {"success": True, "order_number": (resp or {}).get("_orderNumber"),
            "used_skus": skus, "attempted_url": attempted, "response": resp}
@router.post("/hepsiburada/orders/import-selected")
async def import_selected_hepsiburada_orders(req: HbOrderImportReq, current_user: dict = Depends(require_admin)):
    """Önizlemeden seçilen Hepsiburada siparişlerini sisteme aktarır (Trendyol akışıyla aynı: stok düşümü + log)."""
    imported = updated = 0
    errors = []
    for raw in req.orders:
        order_data = await _hb_enrich_items(map_hepsiburada_order(raw))
        on = order_data["order_number"]
        try:
            existing = await db.orders.find_one({"order_number": on, "platform": "hepsiburada"})
            if existing:
                _upd = {k: v for k, v in order_data.items() if k != "status"}
                for _ck in ("cargo_tracking_number", "cargo_provider_name", "cargo_tracking_link"):
                    if not _upd.get(_ck):
                        _upd.pop(_ck, None)
                _old_q = sum(int(it.get("quantity") or 1) for it in (existing.get("items") or []))
                _new_q = sum(int(it.get("quantity") or 1) for it in (order_data.get("items") or []))
                if 0 < _new_q < _old_q and not existing.get("partial_cancelled"):
                    _upd["partial_cancelled"] = True
                    _upd["partial_cancel_at"] = datetime.now(timezone.utc).isoformat()
                await db.orders.update_one({"_id": existing["_id"]}, {"$set": _upd})
                updated += 1
            else:
                order_data["id"] = generate_id()
                order_data["created_at"] = _hb_created_at(order_data)
                await db.orders.insert_one(order_data)
                imported += 1
                await _decrement_stock_for_imported_order(order_data, "hepsiburada")
            await log_integration_event("hepsiburada", "import_order", "order", on, "success", "Sipariş aktarıldı.")
        except Exception as e:
            errors.append({"orderNumber": on, "error": str(e)})
            await log_integration_event("hepsiburada", "import_order", "order", on, "error", f"Aktarım hatası: {e}", {"raw": raw})
    return {"success": True, "imported": imported, "updated": updated, "errors": errors}
async def _hb_markup() -> float:
    s = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    try:
        return float(s.get("default_markup", 0) or 0)
    except Exception:
        return 0.0
HB_PRICE_SOURCES = [
    {"value": "price", "label": "Satış Fiyatı (price) — varsayılan"},
    {"value": "auto", "label": "Otomatik (İndirimli varsa onu kullan, yoksa Satış Fiyatı)"},
    {"value": "sale_price", "label": "İndirimli Fiyat (sale_price)"},
]
_HB_PRICE_SOURCE_KEYS = {x["value"] for x in HB_PRICE_SOURCES}
async def _hb_price_source() -> str:
    s = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    ps = (s.get("price_source") or "price")
    return ps if ps in _HB_PRICE_SOURCE_KEYS else "price"
def _hb_pick_base_price(obj: dict, price_source: str = "auto") -> float:
    """Ürün/varyanttan, seçili kaynağa göre temel fiyatı çöz.
    #4: Önce Üye Tipi 1 fiyatı (member_price_1) baz alınır; boş/0 ise eski mantığa düşer.
    Sayılar _to_float_tr ile çözülür → '2100,99' gibi virgüllü fiyatlar 0'a DÜŞMEZ."""
    member = _to_float_tr(obj.get("member_price_1"))
    if member > 0:
        return member
    price = _to_float_tr(obj.get("price"))
    sale = _to_float_tr(obj.get("sale_price"))
    if price_source == "price":
        return price
    if price_source == "sale_price":
        return sale if sale > 0 else price
    # auto: indirimli (>0) varsa onu kullan, yoksa satış fiyatı
    return sale if sale > 0 else price
def _hb_merchant_sku(obj: dict) -> str:
    return str(obj.get("stock_code") or obj.get("barcode") or obj.get("sku") or "").strip()
def _hb_card_id(product: dict) -> str:
    """Ürün Kart ID — urun_karti_id (asıl) veya csv_card_id (yedek)."""
    return str(product.get("urun_karti_id") or product.get("csv_card_id") or "").strip()
def _hb_sku_base_from_source(product: dict, source: str) -> str:
    """'Satıcı Stok Kodu' kaynağına göre ürün-seviyesi temel SKU değeri."""
    src = source or "stock_code"
    if src in ("id", "variant_urun_id", "urun_id"):
        return str(product.get("urun_id") or product.get("id") or "").strip()
    if src == "card_id":
        return _hb_card_id(product) or str(_hb_merchant_sku(product) or product.get("id") or "").strip()
    if src == "barcode":
        return str(product.get("barcode") or "").strip()
    if src == "name":
        return str(product.get("name") or "").strip()
    if src == "brand":
        return str(product.get("brand") or product.get("brand_name") or "").strip()
    if src == "category_name":
        return str(product.get("category_name") or "").strip()
    # stock_code (varsayılan) ve bilinmeyenler
    return str(_hb_merchant_sku(product) or product.get("stock_code") or product.get("id") or "").strip()
def _hb_variant_sku(product: dict, variant, vi: int, source: str = "stock_code", used: set = None) -> str:
    """Configured 'Satıcı Stok Kodu' kaynağına göre VARYANT BAŞINA BENZERSIZ merchantSku.
    Hem ürün oluşturmada hem stok/fiyat güncellemede AYNI sonucu üretir (HB join anahtarı).
    Varsayılan (stock_code) davranış birebir korunur; kullanıcı bir kaynak seçtiyse (ör.
    Ürün Kart ID) o değer baz alınır, varyantlarda benzersizlik için beden/renk soneki eklenir."""
    src = source or "stock_code"
    local = _hb_collect_local(product, variant)

    def _var_token():
        # Y10: STABİL varyant kimliği — dizi konumundan (vi) BAĞIMSIZ. merchantSku HB'nin
        # stok/fiyat join anahtarıdır; sonek konuma bağlı olursa (örn. Excel re-import ile
        # variants yeniden sıralanınca) beden 38'in stoğu 40'ın ilanına gidiyordu. Artık sonek
        # varyantın kendi barkod/sku/urun_id'sinden ya da beden-renk kombinasyonundan türetilir.
        raw = str((variant or {}).get("barcode") or (variant or {}).get("sku")
                  or (variant or {}).get("urun_id") or (variant or {}).get("id") or "").strip()
        if raw:
            return raw.replace(" ", "").upper()[-10:]
        sx = (local.get("beden") or local.get("size") or local.get("numara") or "")
        cx = (local.get("renk") or local.get("color") or "")
        combo = "-".join([p for p in [sx, cx] if p])
        return _hb_norm(combo).replace(" ", "").upper() if combo else f"V{vi + 1}"

    def _suffix():
        sx = (local.get("beden") or local.get("size") or local.get("numara") or "")
        cx = (local.get("renk") or local.get("color") or "")
        combo = "-".join([p for p in [sx, cx] if p])
        return _hb_norm(combo).replace(" ", "").upper() if combo else _var_token()

    # KULLANICI "Ürün ID" seçtiyse: her bedenin KENDİ urun_id'si varyantta (variants[].urun_id)
    # duruyor → doğrudan onu merchantSku yap. Uydurma sonek YOK; her beden zaten benzersiz.
    if src in ("id", "variant_urun_id", "urun_id") and variant is not None:
        vid = str((variant or {}).get("urun_id") or "").strip()
        if vid:
            if used is not None:
                if vid in used:
                    vid = f"{vid}-{_var_token()}"
                used.add(vid)
            return vid

    if src == "stock_code":
        # Varsayılan: varyantın kendi stok kodu/barkodu öncelikli (mevcut davranış)
        sku = (str((variant or {}).get("stock_code") or "").strip()
               or str((variant or {}).get("barcode") or "").strip())
        if not sku:
            base = _hb_sku_base_from_source(product, "stock_code")
            sku = (f"{base}-{_suffix()}" if base else _suffix()) if variant is not None else base
    else:
        # Kullanıcı kaynağı (ör. Ürün Kart ID): base bu kaynaktan; varyantta benzersizlik için sonek
        base = _hb_sku_base_from_source(product, src) or _hb_sku_base_from_source(product, "stock_code")
        sku = (f"{base}-{_suffix()}" if base else _suffix()) if variant is not None else base

    sku = (sku or "").strip()
    if used is not None and sku:
        if sku in used:
            sku = f"{sku}-{_var_token()}"  # Y10: konumsal değil, stabil sonek
        used.add(sku)
    return sku
async def _hb_sku_source() -> str:
    """Kayıtlı 'Satıcı Stok Kodu' gönderim kaynağı (base_field_mappings.merchantSku.source)."""
    s = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    cfg = (s.get("base_field_mappings") or {}).get("merchantSku") or {}
    return cfg.get("source") or "stock_code"
def _hb_listing_items_from_product(product: dict, markup: float = 0.0, price_source: str = "auto",
                                   sku_source: str = "stock_code"):
    """Yerel ürün -> HB listing kalemleri [{merchantSku, price, availableStock}].
    Fiyat, price_source'a göre (auto/price/sale_price) seçilir; markup uygulanır.
    merchantSku, gönderim kaynağına (sku_source) göre _hb_variant_sku ile üretilir →
    ürün oluşturma ile stok/fiyat güncellemesi AYNI SKU'yu kullanır."""
    items = []
    base_price = _hb_pick_base_price(product, price_source)
    if markup > 0:
        base_price = base_price * (1 + markup / 100)
    variants = product.get("variants", []) or []
    used: set = set()
    if variants:
        for vi, v in enumerate(variants):
            sku = _hb_variant_sku(product, v, vi, sku_source, used)
            if not sku:
                continue
            # Varyantın kendi fiyatı varsa onu (kaynağa göre) baz al, yoksa ürün fiyatı + price_diff
            v_price = _hb_pick_base_price(v, price_source) if (v.get("price") or v.get("sale_price")) else 0.0
            if v_price > 0:
                p = v_price * (1 + markup / 100) if markup > 0 else v_price
            else:
                p = base_price + _to_float_tr(v.get("price_diff", 0))
            # Fiyat 0/negatif ise HB'ye GÖNDERME (None → push filtresi düşürür). Stok yine gider.
            items.append({"merchantSku": sku,
                          "price": (round(p, 2) if (p and p > 0) else None),
                          "availableStock": int(_to_float_tr(v.get("stock", 0)))})
    else:
        sku = _hb_variant_sku(product, None, 0, sku_source)
        if sku:
            items.append({"merchantSku": sku,
                          "price": (round(base_price, 2) if (base_price and base_price > 0) else None),
                          "availableStock": int(_to_float_tr(product.get("stock", 0)))})
    return items
async def _hb_push_stock_price(client, items, do_price=True, do_stock=True):
    """price-uploads / stock-uploads çağrılarını yapar, upload id'lerini döner."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    out = {"price_upload_id": None, "stock_upload_id": None, "errors": []}
    price_items = [{k: it[k] for k in ("merchantSku", "hepsiburadaSku", "price") if k in it and it.get(k) not in (None, "")}
                   for it in items if it.get("price") is not None]
    stock_items = [{k: it[k] for k in ("merchantSku", "hepsiburadaSku", "availableStock") if k in it and it.get(k) not in (None, "")}
                   for it in items if it.get("availableStock") is not None]
    if do_price and price_items:
        try:
            r = await asyncio.to_thread(client.update_prices, price_items)
            out["price_upload_id"] = (r or {}).get("id") if isinstance(r, dict) else None
        except HepsiburadaError as e:
            out["errors"].append(f"Fiyat: {e}")
    if do_stock and stock_items:
        try:
            r = await asyncio.to_thread(client.update_stocks, stock_items)
            out["stock_upload_id"] = (r or {}).get("id") if isinstance(r, dict) else None
        except HepsiburadaError as e:
            out["errors"].append(f"Stok: {e}")
    return out
@router.post("/hepsiburada/products/{product_id}/update-stock-price")
async def hb_update_product_stock_price(product_id: str, body: HbBulkListingReq = None,
                                        current_user: dict = Depends(require_admin)):
    """Tek ürünün stok ve fiyatını Hepsiburada listing'ine gönderir (price/stock-uploads)."""
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    markup = await _hb_markup()
    price_source = await _hb_price_source()
    sku_source = await _hb_sku_source()
    items = _hb_listing_items_from_product(product, markup, price_source, sku_source)
    if not items:
        raise HTTPException(status_code=400, detail="Ürünün stok kodu/barkodu bulunamadı")
    do_price = True if (body is None) else body.update_price
    do_stock = True if (body is None) else body.update_stock
    res = await _hb_push_stock_price(client, items, do_price, do_stock)
    await db.products.update_one({"id": product_id}, {"$set": {
        "hb_listing_updated": datetime.now(timezone.utc).isoformat(),
        "hb_price_upload_id": res.get("price_upload_id"),
        "hb_stock_upload_id": res.get("stock_upload_id"),
    }})
    status = "success" if not res["errors"] else "error"
    await log_integration_event("hepsiburada", "update_stock_price", "product", product_id, status,
                                f"{len(items)} kalem gönderildi" + (f" — {'; '.join(res['errors'])}" if res["errors"] else ""))
    return {"success": not res["errors"], "items_count": len(items), **res}
@router.post("/hepsiburada/categories/{category_id}/update-stock-price")
async def hb_update_category_stock_price(category_id: str, body: HbBulkListingReq = None,
                                         current_user: dict = Depends(require_admin)):
    """Bir kategorideki tüm ürünlerin stok/fiyatını Hepsiburada'ya gönderir."""
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    category = await db.categories.find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    products = await db.products.find({"category_name": category.get("name"), "is_active": True}, {"_id": 0}).to_list(1000)
    if not products:
        products = await db.products.find({"category_id": category_id, "is_active": True}, {"_id": 0}).to_list(1000)
    if not products:
        raise HTTPException(status_code=404, detail="Bu kategoride ürün bulunamadı")
    markup = await _hb_markup()
    price_source = await _hb_price_source()
    sku_source = await _hb_sku_source()
    items = []
    for p in products:
        items.extend(_hb_listing_items_from_product(p, markup, price_source, sku_source))
    if not items:
        raise HTTPException(status_code=400, detail="Ürünlerin stok kodu/barkodu bulunamadı")
    do_price = True if (body is None) else body.update_price
    do_stock = True if (body is None) else body.update_stock
    # HB tek istekte max 4000 sku — parça parça gönder
    all_res = {"price_upload_ids": [], "stock_upload_ids": [], "errors": []}
    for i in range(0, len(items), 4000):
        chunk = items[i:i + 4000]
        r = await _hb_push_stock_price(client, chunk, do_price, do_stock)
        if r.get("price_upload_id"):
            all_res["price_upload_ids"].append(r["price_upload_id"])
        if r.get("stock_upload_id"):
            all_res["stock_upload_ids"].append(r["stock_upload_id"])
        all_res["errors"].extend(r.get("errors", []))
    await log_integration_event("hepsiburada", "update_stock_price", "category", category_id,
                                "success" if not all_res["errors"] else "error",
                                f"{category.get('name')}: {len(items)} kalem")
    return {"success": not all_res["errors"], "items_count": len(items), **all_res}
@router.post("/hepsiburada/listings/update")
async def hb_update_listings_bulk(req: HbBulkListingReq, current_user: dict = Depends(require_admin)):
    """Serbest kalem listesiyle toplu fiyat/stok güncelleme.
    items: [{merchantSku?, hepsiburadaSku?, price?, availableStock?}]"""
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not req.items:
        raise HTTPException(status_code=400, detail="items boş")
    res = await _hb_push_stock_price(client, req.items, req.update_price, req.update_stock)
    await log_integration_event("hepsiburada", "update_listings", "bulk", str(len(req.items)),
                                "success" if not res["errors"] else "error", f"{len(req.items)} kalem")
    return {"success": not res["errors"], "items_count": len(req.items), **res}
@router.get("/hepsiburada/listings/status/{kind}/{upload_id}")
async def hb_listing_upload_status(kind: str, upload_id: str, current_user: dict = Depends(require_admin)):
    """Fiyat/stok güncelleme işlem kontrolü. kind: price | stock | inventory."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.get_upload_status, kind, upload_id)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.get("/hepsiburada/listings")
async def hb_get_listings(offset: int = 0, limit: int = 100, merchant_sku: Optional[str] = None,
                          current_user: dict = Depends(require_admin)):
    """Satıcı listing bilgilerini çeker."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        skus = [merchant_sku] if merchant_sku else None
        data = await asyncio.to_thread(client.get_listings, offset, limit, skus)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/listings/{hbsku}/activate")
async def hb_activate_listing(hbsku: str, current_user: dict = Depends(require_admin)):
    """Listingi satışa açar (stok ve fiyat > 0 olmalı)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.activate_listing, hbsku)
        await log_integration_event("hepsiburada", "activate_listing", "listing", hbsku, "success", "Satışa açıldı")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/listings/{hbsku}/deactivate")
async def hb_deactivate_listing(hbsku: str, current_user: dict = Depends(require_admin)):
    """Listingi satışa kapatır."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.deactivate_listing, hbsku)
        await log_integration_event("hepsiburada", "deactivate_listing", "listing", hbsku, "success", "Satışa kapatıldı")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
_HB_CLOTHING_SYNONYMS = {
    "yaka stili": ["Yaka", "Yaka Tipi", "Yaka Modeli", "Yaka Şekli"],
    "kol tipi": ["Kol", "Kol Modeli", "Kol Şekli"],
    "kumas tipi": ["Kumaş", "Kumaş Cinsi", "Materyal", "Malzeme", "Kumaş Türü"],
}
def _hb_collect_local(product: dict, variant: dict | None = None) -> dict:
    """Ürün (+varsa varyant) verisinden {normalize(özellik_adı): değer} toplar.
    Tamamen yerel — hiçbir pazaryerine bağlı değil."""
    out: dict = {}

    def put(nm, vv):
        if not nm or vv in (None, ""):
            return
        k = _hb_norm(nm)
        if k and k not in out:
            out[k] = str(vv).strip()

    def walk(attrs):
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if isinstance(v, dict):
                    put(v.get("label") or v.get("name") or k, v.get("value") or v.get("attribute_value"))
                else:
                    put(k, v)
        elif isinstance(attrs, list):
            for a in attrs:
                if isinstance(a, dict):
                    put(a.get("label") or a.get("name") or a.get("type") or a.get("attribute_name"),
                        a.get("value") or a.get("attribute_value"))

    walk(product.get("attributes"))
    walk(product.get("hepsiburada_attributes"))
    if product.get("brand") or product.get("brand_name"):
        put("marka", product.get("brand") or product.get("brand_name"))
    if product.get("gender"):
        put("cinsiyet", product.get("gender"))
    if variant:
        walk(variant.get("attributes"))
        if variant.get("color"):
            put("renk", variant["color"]); put("color", variant["color"])
        if variant.get("size"):
            put("beden", variant["size"]); put("numara", variant["size"])
    return out
def _hb_local_for_attr(attr_name: str, local: dict) -> str | None:
    """HB özellik adına göre yerel değeri semantik olarak bulur (Renk/Beden/Cinsiyet/Materyal/Marka)."""
    n = _hb_norm(attr_name)
    if local.get(n):
        return local[n]
    if any(w in n for w in ("renk", "color")):
        return local.get("renk") or local.get("color") or local.get("web color")
    if any(w in n for w in ("beden", "size", "numara", "olcu", "olcusu")):
        return local.get("beden") or local.get("size") or local.get("numara")
    if "cinsiyet" in n or "gender" in n:
        return local.get("cinsiyet") or local.get("gender")
    if any(w in n for w in ("materyal", "kumas", "icerik", "material", "kumas tipi", "kumas bilgisi")):
        return (local.get("materyal") or local.get("materyal bilesimi") or local.get("kumas bilgisi")
                or local.get("kumas icerigi") or local.get("urun icerik bilgisi") or local.get("urun icerigi")
                or local.get("kumas tipi"))
    if any(w in n for w in ("ilikleme", "kapama")):
        # HB "İlikleme Türü" ↔ bizim "Kapama Şekli" (Düğmeli/Fermuarlı/Çıtçıtlı...)
        return (local.get("ilikleme turu") or local.get("ilikleme")
                or local.get("kapama sekli") or local.get("kapama"))
    if "marka" in n or "brand" in n:
        return local.get("marka")
    return None
def _hb_value_from_name(product_name, attr: dict):
    """Ürün adında geçen bir DEĞER, HB özelliğinin enum değerlerinden biriyle eşleşirse onu döner.
    TEK kelime  : tam-kelime eşleşmesi ('Ekru' ∈ 'Mira Mini Etek Ekru').
    ÇOK kelime  : değer adı, üründe geçen bitişik kelime dizisiyle eşleşir
                  ('V Yaka' ∈ 'Alba V Yaka Uzun Triko Elbise'). Eski kod yalnız tek
                  kelimeye baktığından 'V Yaka' gibi boşluklu değerleri KAÇIRIYORDU →
                  HB 'Yaka Stili zorunlu' diye reddediyordu. En UZUN eşleşen değer seçilir."""
    vals = attr.get("attributeValues") or []
    if not vals or not product_name:
        return None
    toks = [_hb_norm(w) for w in re.split(r"[\s/,\.\-_()\[\]]+", str(product_name)) if w]
    toks = [t for t in toks if t]
    if not toks:
        return None
    word_set = set(toks)
    name_padded = " " + " ".join(toks) + " "   # sınır-korumalı bitişik dizi araması için
    best = None  # (uzunluk, ad) — en uzun/özel eşleşme kazanır ('V Yaka' > 'Yaka')
    for v in vals:
        nm = v.get("name")
        vn = _hb_norm(nm)
        if not vn or len(vn) < 2:
            continue
        hit = False
        if " " in vn:
            # çok kelimeli değer: ada bitişik dizi olarak gömülü mü? (' v yaka ' ⊂ ' ... ')
            if (" " + vn + " ") in name_padded:
                hit = True
        elif vn in word_set:
            hit = True
        if hit and (best is None or len(vn) > best[0]):
            best = (len(vn), nm)
    return best[1] if best else None
_HB_DIRTY_POOL_MIN = 200
def _hb_is_junk_value(raw) -> bool:
    """HB'ye ASLA gönderilmemesi gereken çöp değerler.
    Ekranda görülen 'Yaka Stili = 2|belirtilmemis|1' gibi pipe-artefaktlar ve
    'belirtilmemiş/diğer/yok' türü sahte-değerler. Bunlar kayıtlı hepsiburada_attributes
    veya default_mappings'ten faithfully taşınıp HB ürününe çöp basıyordu."""
    if raw in (None, ""):
        return True
    s = str(raw).strip()
    if not s:
        return True
    if "|" in s:                      # "2|belirtilmemis|1" → kayıt artefaktı, asla geçerli değil
        return True
    n = _hb_norm(s)
    if n in ("belirtilmemis", "diger", "other", "yok", "na", "none", "null", "bos", "seciniz", "seciniz"):
        return True
    return False
_HB_ID_SHAPE_RE = re.compile(r'^[0-9A-Za-z]{3,14}$')
def _hb_looks_like_leaked_id(raw) -> bool:
    """vals (enum) BOS geldiginde (HB deger listesi cache'te eksik/cekilememis) ham
    deger cop bir HB ic value-ID'sine mi benziyor -- gercek bir insan tarafindan girilmis
    serbest metne mi? Sezgisel: cok kisa (<=2 karakter, ornek 'r') VEYA harf+rakam karisik,
    bosluksuz, Turkce karaktersiz, 3-14 karakter (ornek '0004TYN', '11287', '0002ULK',
    '0000O5LW') -- bunlar SATICI PANELINDE 'ozellik degeri reddedildi/tanimli degil' diye
    geri donen TAM OLARAK bu sekildeki degerler. HB'ye GONDERME (bos birak) -- cop degerden
    HER ZAMAN iyidir: zorunlu degilse sorun yaratmaz, zorunluysa 'eksik' diye raporlanir."""
    s = str(raw or "").strip()
    if not s:
        return False
    if len(s) <= 2:
        return True
    if " " in s or any(ch in "çğıöşüÇĞİÖŞÜ" for ch in s):
        return False  # gercek Turkce metin (boslukla veya Turkce harfle) -- dokunma
    if _HB_ID_SHAPE_RE.match(s) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s):
        return True  # harf+rakam karisik kod -- ID gorunumlu
    if s.isdigit() and len(s) >= 3:
        return True  # saf sayisal ID gorunumlu (11287, 15212, 4939, 20503 gibi)
    return False
def _hb_resolve_value(attr: dict, raw):
    """raw değeri HB özelliğinin izin verdiği değerlerden (enum) birine çözer.
    raw bir değer ADı veya değer ID'si olabilir (Özel Değer dropdown'u id kaydediyor).
    enum değilse serbest metin döner. Uyuşmayan ve allowCustom kapalıysa None."""
    if _hb_is_junk_value(raw):
        return None  # çöp/sahte değer → hiç gönderme (HB zorunlu alanı boş ister, çöp değil)
    vals = attr.get("attributeValues") or []
    if not vals:
        # ⚠️ HB değer listesi BOŞ (cache'te eksik/çekilememiş) — bu durumda eskiden raw
        # OLDUĞU GİBİ gönderiliyordu. Eğer raw aslında başka bir kategoriden/eski bir
        # cache'ten sızmış bir HB iç value-ID'siyse ("0004TYN", "11287", "r" gibi —
        # satıcı panelinde "özellik değer öneriniz reddedilmiştir/tanımlı değildir" diye
        # dönen TAM OLARAK bu görünüm), HB onu reddediyordu. Artık ID-görünümlü/anlamsız
        # değerler HİÇ GÖNDERİLMEZ (boş = HB zorunlu değilse sorunsuz; zorunluysa "eksik"
        # diye raporlanır) — çöp değer her zaman reddedilen değerden iyidir.
        if _hb_looks_like_leaked_id(raw):
            return None
        return str(raw)  # serbest metin / varchar
    nr = _hb_norm(raw)
    if not nr:
        return None
    # 1) Değer ID eşleşmesi (Özel Değer/Listeden Seçin dropdown'u value.id kaydeder)
    for v in vals:
        vid = v.get("id")
        if vid is not None and _hb_norm(str(vid)) == nr:
            return v.get("name")
    # 2) Tam ad eşleşmesi
    for v in vals:
        if _hb_norm(v.get("name")) == nr:
            return v.get("name")
    # 3) En iyi parçalı/kelime eşleşmesi — YALNIZCA küçük/temiz enum'lar için.
    #    Renk/Beden/Materyal gibi yüksek-kardinaliteli "satıcı havuzu" enum'larında (yüzlerce-binlerce
    #    kirli serbest-girdi: "0-Gri", "690Ekru", "Altın - Ekru"...) parçalı eşleşme YANLIŞ sonuç verir
    #    ("Ekru" → "Açık Ekru"). Bu alanlar pratikte serbest değer kabul eder (havuzdaki satıcı-custom
    #    değerler bunun kanıtı); tam eşleşme yoksa raw'ı AYNEN göndeririz, HB yeni değeri kabul eder.
    if len(vals) <= _HB_DIRTY_POOL_MIN:
        nr_words = set(nr.split())
        nr2 = " ".join(nr.replace("-", " ").split())
        best = None  # (skor, uzunluk, ad) — küçük skor + kısa ad daha iyi
        for v in vals:
            nm = v.get("name")
            vn = _hb_norm(nm)
            if not vn or len(vn) < 2:
                continue
            vn_words = set(vn.split())
            vn2 = " ".join(vn.replace("-", " ").split())
            score = None
            if nr in vn_words:               # raw, değerin bir kelimesi: "ekru" ∈ "Altın - Ekru"
                score = 10
            elif vn in nr_words:             # değer, raw'ın bir kelimesi: "Ekru" ∈ "Altın Ekru"
                score = 20
            elif len(nr2) >= 3 and (nr2 in vn2 or vn2 in nr2):  # gevşek substring (son çare)
                score = 100
            if score is not None:
                cand = (score, len(vn), nm)
                if best is None or cand < best:
                    best = cand
        if best:
            return best[2]
    if attr.get("allowCustom") or len(vals) > _HB_DIRTY_POOL_MIN:
        return str(raw)
    return None
def _hb_resolve_with_fallback(a, raw, orig=None):
    """raw'ı HB değerine çöz; enum'da ham/sayısal bir ID gibi ÇÖZÜLMEDEN kalırsa
    (örn. başka kategoriden gelmiş value_mapping ID'si → Cinsiyet'in '17530' basması)
    orijinal etikete ('Kadın') geri düşer. SALT-ADDİTİF: sağlam isim eşleşmesi varsa
    sonucu hiç değiştirmez; yalnız 'çözülemeyen sayısal ID' durumunu düzeltir."""
    rv = _hb_resolve_value(a, raw)
    vals = a.get("attributeValues") or []

    def _unresolved(val):
        if val in (None, ""):
            return True
        if not vals:
            return False  # serbest metin alanı → her şey geçerli
        nv = _hb_norm(val)
        if any(_hb_norm(v.get("name")) == nv for v in vals):
            return False  # gerçek bir değer ADı → çözülmüş
        return str(val).strip().isdigit()  # isim değil + saf sayı → çözülmemiş ID

    if orig not in (None, "") and str(orig) != str(raw) and _unresolved(rv):
        rv2 = _hb_resolve_value(a, orig)
        if not _unresolved(rv2):
            return rv2
    return rv
def _hb_fabric_tokens(text):
    """'%97 Pamuk %3 Elasthan' → {'97pamuk','3elastan'} (sıra-bağımsız, yazım-normalize).
    HB 'Kumaş Tipi' kapalı bir kompozisyon enum'u (292 seçenek); düz 'Pamuk' eşleşmez.
    Ürün AÇIKLAMASINDAKİ gerçek kompozisyonu (yüzde+lif) çıkarıp HB seçeneğiyle
    BİREBİR eşlemek için kullanılır. 'elasthan/elastane' → 'elastan' birleştirilir."""
    import re as _re
    t = _hb_norm(text).replace("elasthan", "elastan").replace("elastane", "elastan")
    toks = set()
    for m in _re.finditer(r"%\s*(\d{1,3})\s*([a-z]+)", t):
        toks.add(m.group(1) + m.group(2))
    return toks
def _hb_match_fabric_from_desc(attr, desc):
    """Büyük 'Kumaş Tipi' enum'unda, açıklamadaki gerçek kompozisyonu HB seçeneğine eşler.
    Yalnız HB seçeneğinin TÜM yüzde+lif token'ları açıklamada AYNEN geçiyorsa döner
    (uydurma/tahmin YOK). Birden çok aday varsa en SPESİFİK (en çok token'lı, açıklamada
    tam desteklenen) seçenek seçilir. Küçük enum'lara dokunmaz (zaten güvenli eşleşiyor)."""
    vals = attr.get("attributeValues") or []
    if len(vals) <= _HB_DIRTY_POOL_MIN:
        return None
    want = _hb_fabric_tokens(desc)
    if not want:
        return None
    best = None  # (token_sayısı, ad)
    for v in vals:
        ot = _hb_fabric_tokens(v.get("name"))
        if ot and ot <= want:
            cand = (len(ot), v.get("name"))
            if best is None or cand > best:
                best = cand
    return best[1] if best else None
async def _hb_category_attributes_for(hb_cat):
    """HB kategori özelliklerini (cache → yoksa canlı) getirir. (attrs_list, error).
    ⚠️ KISMİ CACHE BOŞLUĞU KORUMASI: eskiden yalnız '_v==10 ve attributes dolu mu' bakılırdı —
    bazı özelliklerin (ör. Ortam/Stil/Koleksiyon) attributeValues'u BOŞ olsa bile cache
    'taze' sayılıyordu. Bu, gönderimde o özelliklerin HAM/ID değerinin (çözülemeden) HB'ye
    gidip reddedilmesine yol açıyordu (satıcı panelinde "özellik değer öneriniz
    reddedilmiştir/tanımlı değildir"). Artık böyle bir boşluk varsa hedefli onarım denenir
    (yalnız boş kalan özellikler canlı çekilir — dolu olanlara dokunulmaz, hızlı); aynı
    kategori için 30 dakikada bir denenir (bulk gönderimde her ürün için tekrar tekrar
    HB'ye gitmesin diye). Onarım denemesi SERT 8sn sınırlıdır — HB yavaş/yanıtsız kalırsa
    isteği (ve gateway'i) asla bloklamaz, elindeki (boşluklu da olsa) cache ile devam eder."""
    key = int(hb_cat) if str(hb_cat).isdigit() else str(hb_cat)
    cad = await db.hepsiburada_category_attributes.find_one({"category_id": key}, {"_id": 0})
    from .category_mapping import _fetch_hb_category_attributes, _hb_schema_has_gaps
    import asyncio
    if cad and cad.get("_v") == 10 and cad.get("attributes"):
        attrs = cad.get("attributes") or []
        if not _hb_schema_has_gaps(attrs):
            return attrs, None
        last_try = cad.get("_gap_refreshed_at")
        try:
            stale = (not last_try) or (
                (datetime.now(timezone.utc) - datetime.fromisoformat(last_try)).total_seconds() > 1800)
        except Exception:
            stale = True
        if not stale:
            return attrs, None  # yakın zamanda denendi, hâlâ boşluk var (HB tarafında gerçekten yok olabilir) — bekleme
        # Denemeyi ÖNCE damgala (sonucu beklemeden) — onarım zaman aşımına uğrasa/başarısız
        # olsa bile aynı 30dk içinde tekrar tekrar denenip her seferinde yavaşlatmasın.
        await db.hepsiburada_category_attributes.update_one(
            {"category_id": key}, {"$set": {"_gap_refreshed_at": datetime.now(timezone.utc).isoformat()}})
        try:
            fresh, ferr = await asyncio.wait_for(_fetch_hb_category_attributes(hb_cat), timeout=8)
        except (asyncio.TimeoutError, Exception):
            return attrs, None  # yavaş/başarısız → elimizdekiyle devam (eski davranış)
        if fresh:
            await db.hepsiburada_category_attributes.update_one(
                {"category_id": key}, {"$set": {"attributes": fresh}})
            return fresh, None
        return attrs, None
    attrs, ferr = await _fetch_hb_category_attributes(hb_cat)
    if not attrs and ferr:
        return [], ferr
    return attrs or [], None
async def _hb_base_attributes_for(hb_cat) -> list:
    """HB'nin bu kategori için döndürdüğü ZORUNLU temel/sistem alanlarını (baseAttributes)
    cache'ten getirir. _fetch_hb_category_attributes bunları `base_attributes` altında saklar.
    Boş dönerse (cache yok / kategoride base yok) çağıran taraf eski sabit davranışa düşer."""
    key = int(hb_cat) if str(hb_cat).isdigit() else str(hb_cat)
    cad = await db.hepsiburada_category_attributes.find_one(
        {"category_id": key}, {"_id": 0, "base_attributes": 1, "media_attributes": 1})
    return (cad or {}).get("base_attributes") or []
async def _build_hb_product_item(product: dict, merchant_id: str):
    """Yerel ürün -> HB import kalem(ler)i. Liste döner (varyant başına bir kalem).

    HB'ye TAMAMEN BAĞIMSIZ: kategorinin HB API özelliklerini (zorunlu/opsiyonel + izin
    verilen değerler) alır ve her özelliği ÜRÜN VERİSİNDEN otomatik türetir; enum değerleri
    HB'nin kabul ettiği değere çözer. Kaydedilmiş attribute_mappings/value_mappings/
    default_mappings override olarak kullanılır. Çözülemeyen ZORUNLU özellik varsa kalem
    atlanır ve sebebi (hangi özellikler eksik) raporlanır → kullanıcı yalnız onları doldurur.

    Döner: (items_list, error). items_list boşsa error doludur.
    """
    cm = await db.category_mappings.find_one(
        {"marketplace": "hepsiburada", "category_id": product.get("category_id")}, {"_id": 0})
    if not cm:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": product.get("category_name")}, {"_id": 0})
    hb_cat = (cm or {}).get("marketplace_category_id")
    if not hb_cat:
        return [], "HB kategori eşleşmesi yok (Kategori Eşleştirme ekranından eşleyin)"

    hb_attrs_list, ferr = await _hb_category_attributes_for(hb_cat)
    if not hb_attrs_list:
        return [], f"HB kategori özellikleri çekilemedi: {ferr or 'boş'}"

    saved_maps = (cm or {}).get("attribute_mappings") or []
    map_by_attr_id = {str(m.get("mp_attr_id") or m.get("trendyol_attr_id")): m
                      for m in saved_maps if (m.get("mp_attr_id") or m.get("trendyol_attr_id"))}
    vmaps = (cm or {}).get("value_mappings") or {}
    defaults = (cm or {}).get("default_mappings") or {}
    # default_mappings anahtarları üç biçimde gelebilir: tam HB attribute ADı ("Yaka Stili"),
    # HB attribute ID'si ("000002C") veya küçük-harf alias ("yaka_stili"). Ad/ID eşleşmesini
    # ana döngü zaten yakalar; alias'ları yakalamak için NORMALİZE anahtar haritası kurarız →
    # böylece her default DOĞRU özelliğe (resolve edilerek) gider, payload'a ham çöp ANAHTAR basılmaz.
    defaults_norm = {_hb_norm(k): v for k, v in defaults.items() if v not in (None, "")}
    # Global ortak-özellik default'ları (panel: "Ortak Özellikler", ör. Cinsiyet=Kadın).
    # Her kategoride geçerli; o kategorinin enum'una ada göre çözülür.
    _hbset = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    gad = {_hb_norm(k): v for k, v in (_hbset.get("global_attr_defaults") or {}).items()
           if v not in (None, "")}

    brand = product.get("brand") or product.get("brand_name")
    desc = re.sub(r"<[^>]+>", " ", product.get("description") or "").strip()
    # KULLANICI KARARI: ölçü görseli artık HB'ye de SON görsel olarak gönderilir
    # (eski 'gönderme' kuralı kaldırıldı). Normal görseller önce; data: URL'ler atlanır.
    imgs, _size_imgs = [], []
    for img in (product.get("images") or []):
        u = img.get("url") if isinstance(img, dict) else img
        if not u or str(u).startswith("data:"):
            continue
        if isinstance(img, dict) and img.get("is_size_table"):
            _size_imgs.append(u)
        else:
            imgs.append(u)
    imgs = (imgs[:4] + _size_imgs[:1]) if _size_imgs else imgs[:5]
    if not imgs and product.get("image"):
        imgs.append(product["image"])

    variants = product.get("variants") or []
    targets = variants if variants else [None]
    vgroup = _hb_merchant_sku(product) or str(product.get("id") or "")
    cat_val = int(hb_cat) if str(hb_cat).isdigit() else hb_cat

    # Global "Varsayılan Alan Eşleştirme" — temel HB alanlarının ürün-kartı kaynağı / sabit değeri
    from .category_mapping import _HB_BASE_BY_KEY, _HB_BASE_ALIAS, _hb_sysnorm
    bfm = (await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}).get("base_field_mappings") or {}
    sku_source = ((bfm.get("merchantSku") or {}).get("source") or "stock_code")

    # 🎯 HB'nin BU kategori için zorunlu kıldığı TEMEL alanlar (baseAttributes).
    # Eski davranış yalnız kategori-özelliklerini zorunlu sayıyordu; HB bir temel alanı
    # (ör. Garanti Süresi) zorunlu kılıp biz göndermezsek import SESSİZCE reddediliyordu.
    # Artık bunları da doğruluyoruz → eksikse kullanıcıya net hata, HB'de sessiz red yok.
    _HB_BASE_PAYLOAD_KEY = {
        "merchantSku": "merchantSku", "Barcode": "Barcode", "UrunAdi": "UrunAdi",
        "UrunAciklamasi": "UrunAciklamasi", "Marka": "Marka", "GarantiSuresi": "GarantiSuresi",
        "kg": "kg", "kdv": "tax", "Image": "Image1", "VaryantGroupID": "VaryantGroupID",
    }
    base_required = []  # [(payload_key, label)]
    try:
        for ba in (await _hb_base_attributes_for(hb_cat)):
            if not isinstance(ba, dict):
                continue
            if not (ba.get("mandatory") or ba.get("required") or ba.get("mandatoryVariant")):
                continue
            our_key = _HB_BASE_ALIAS.get(_hb_sysnorm(ba.get("name") or ""))
            pk = _HB_BASE_PAYLOAD_KEY.get(our_key or "")
            if pk:
                label = (_HB_BASE_BY_KEY.get(our_key, {}) or {}).get("label") or ba.get("name") or pk
                base_required.append((pk, label))
    except Exception:
        base_required = []

    def _src_val(src, variant):
        if src == "name":
            return product.get("name")
        if src == "description":
            return desc
        if src == "stock_code":
            return ((variant or {}).get("stock_code") or (variant or {}).get("barcode")
                    or product.get("stock_code") or _hb_merchant_sku(product))
        if src == "card_id":
            return _hb_card_id(product)
        if src == "id":
            return product.get("id")
        if src == "barcode":
            return (variant or {}).get("barcode") or product.get("barcode")
        if src == "brand":
            return brand
        if src == "category_name":
            return product.get("category_name")
        if src == "price":
            return product.get("price")
        if src == "weight":
            return product.get("weight") or product.get("kg")
        if src == "images":
            return imgs
        return None

    def _base_val(key, variant):
        meta = _HB_BASE_BY_KEY.get(key, {})
        cfg = bfm.get(key) or {}
        src = cfg.get("source") or meta.get("default_source") or "__default"
        dflt = cfg.get("default") if cfg.get("default") not in (None, "") else meta.get("default_value", "")
        if src in ("__default", "__auto"):
            return dflt
        v = _src_val(src, variant)
        if v in (None, "", []):
            v = dflt
        return v

    items, errors = [], set()
    used_skus: set = set()
    hb_attrs_for_product = dict(product.get("hepsiburada_attributes") or {})

    for vi, variant in enumerate(targets):
        local = _hb_collect_local(product, variant)
        v_hb = dict((variant or {}).get("hepsiburada_attributes") or {})
        attrs: dict = {}
        missing_req = []

        for a in hb_attrs_list:
            aid = str(a.get("id"))
            aname = a.get("name")
            if not aname:
                continue
            # ── YENİ MODEL: TEK ve GÖRÜNÜR kaynak. Ad-kazıma YOK, gizli arka-plan türetme YOK. ──
            # Bir özellik ya VARYANTTAN gelir (ürün içinde değişen: Renk/Beden), ya da
            # AÇIK BİR KAYNAKTAN: ürün kartındaki HB değeri · kategori sabiti · ortak default ·
            # açık alan-eşleştirmesi. Hiçbiri yoksa boştur (zorunluysa "eksik" der, çöp basmaz).
            anorm = _hb_norm(aname)
            is_variant_axis = bool(a.get("variant")) or any(
                w in anorm for w in ("renk", "color", "beden", "size", "numara"))
            if is_variant_axis:
                # KATMAN 1 — VARYANT EKSENİ: önce varyant/ürün alanından (Renk/Beden) — otomatik
                # tespit çoğu üründe doğru çalışır. Bulamazsa, kullanıcının Kategori Eşleştirme
                # ekranındaki "Özellik Eşleştirme"de bu HB özelliği için AÇIKÇA seçtiği yerel
                # özelliğe düşülür (eskiden bu adım YALNIZ varyant-dışı özelliklerde çalışıyordu —
                # Renk/Beden gibi en sık eşleştirilen alanlarda kullanıcının kaydettiği eşleştirme
                # sessizce YOK SAYILIYORDU). Manuel eşleştirme her zaman otomatik tespitten SONRA
                # denenir → doğru otomatik eşleşmeyi asla ezmez, yalnız boşluğu doldurur.
                raw = _hb_local_for_attr(aname, local) or v_hb.get(aname) or hb_attrs_for_product.get(aname)
                if not raw:
                    m = map_by_attr_id.get(aid)
                    if m and m.get("local_attr"):
                        raw = local.get(_hb_norm(m["local_attr"]))
            else:
                # KATMAN 2 — BİREBİR eşleşen açık kaynak (ad-kazıma / fuzzy tahmin YOK):
                #   kart(HB) → TRENDYOL aynı-adlı özellik → kategori sabiti → ortak default →
                #   FACETTE sabit varsayılan (Menşei/Cinsiyet/Yaş Grubu/…, Üretici/İthalatçı) → alan-eşleştirme
                raw = (v_hb.get(aname) or hb_attrs_for_product.get(aname)
                       or local.get(anorm)
                       or defaults.get(aname) or defaults.get(aid) or defaults_norm.get(anorm)
                       or gad.get(anorm)
                       or facette_fixed_value_for(aname))
                if not raw:
                    m = map_by_attr_id.get(aid)
                    if m and m.get("local_attr"):
                        raw = local.get(_hb_norm(m["local_attr"]))
                if not raw:
                    # EŞANLAMLI YEREL-AD KÖPRÜSÜ: HB "Yaka Stili" ↔ ürün "Yaka"/"Yaka Tipi" gibi
                    # ad farkları yüzünden zorunlu alan boş kalmasın. Yalnız birebir kaynak boşsa
                    # devreye girer; bulunan değer yine HB enum'una çözülür (geçersizse gitmez).
                    for _syn in _HB_CLOTHING_SYNONYMS.get(anorm, ()):
                        raw = (v_hb.get(_syn) or hb_attrs_for_product.get(_syn)
                               or local.get(_hb_norm(_syn)))
                        if raw:
                            break
            # value_mapping çevirisi (Kırmızı↔Red gibi). Orijinal etiket saklanır →
            # map'lenen ID bu kategoride çözülemezse etikete geri döneriz (Cinsiyet=17530 fix).
            orig_raw = raw
            if raw:
                mapped = vmaps.get(f"{aid}|{raw}")
                if not mapped and isinstance(vmaps.get(aid), dict):
                    mapped = vmaps[aid].get(str(raw))
                if mapped:
                    # Kullanıcının KAYDETTİĞİ value_mapping bağı aynen onurlandırılır (kirli havuz
                    # dahil) — kullanıcı ne map'lediyse o gider. Çözülemeyen sayısal ID (başka
                    # kategoriden sızmış) durumu _hb_resolve_with_fallback'te orijinal etikete döner.
                    raw = mapped
            # enum'a/serbest metne çöz (çözülemeyen sayısal ID → orijinal etikete düş, çöp filtrelenir)
            if raw not in (None, ""):
                rv = _hb_resolve_with_fallback(a, raw, orig_raw)
                if rv not in (None, ""):
                    attrs[aname] = rv
            # KUMAŞ TİPİ (büyük kapalı kompozisyon enum'u, ~292 seçenek): ürün kartında düz
            # "Pamuk" var → HB listesinde yok → HB "belirtilmemis"e düşürüyor. Ürünün
            # AÇIKLAMASINDAKİ gerçek kompozisyonu ("%97 Pamuk %3 Elastan") HB seçeneğine
            # BİREBİR eşle. Yalnız mevcut değer geçerli bir HB seçeneği DEĞİLSE devreye girer
            # (salt-additif: doğru eşleşeni bozmaz); açıklamada karşılığı yoksa olduğu gibi bırakır.
            if "kumas" in anorm and (a.get("attributeValues") or []):
                _cur = attrs.get(aname)
                _isopt = bool(_cur) and any(
                    _hb_norm(o.get("name")) == _hb_norm(_cur) for o in (a.get("attributeValues") or []))
                if not _isopt:
                    _comp = _hb_match_fabric_from_desc(a, desc)
                    if _comp:
                        attrs[aname] = _comp
            # MENŞEİ (ülke menüsü ~285 seçenek): HB listesinde 7 farklı "Türkiye" varyantı var
            # ("TR - ( Türkiye )", "TR - (Türkiye)", "TR - Türkiye", "TR (Türkiye)" ...). Kartta
            # tutulan boşluklu çöp varyant ("TR - ( Türkiye )") HB tarafında güvenilir eşleşmiyor
            # → yanlış ülkeye (Avustralya) düşüyor. Türkiye niyetini, listenin geri kalanıyla aynı
            # STANDART formatlı seçeneğe ("TR - (Türkiye)") deterministik sabitle (kart verisinden
            # bağımsız, her push'ta doğru gider). KKTC hariç tutulur.
            if ("mense" in anorm) and (a.get("attributeValues") or []):
                _curm = _hb_norm(attrs.get(aname) or raw)
                if _curm and ("turkiye" in _curm) and ("kibris" not in _curm):
                    _tropts = [o.get("name") for o in (a.get("attributeValues") or [])
                               if "turkiye" in _hb_norm(o.get("name"))
                               and "kibris" not in _hb_norm(o.get("name"))]
                    if _tropts:
                        def _tr_std_score(nm):
                            n = nm or ""
                            return ((1 if ("(" in n and ")" in n) else 0)
                                    + (1 if " - (" in n else 0)
                                    + (1 if ("( " not in n and " )" not in n) else 0))
                        attrs[aname] = sorted(_tropts, key=lambda n: (-_tr_std_score(n), len(n or "")))[0]
            if a.get("required") and aname not in attrs:
                missing_req.append(aname)


        # Taban alanlar — global "Varsayılan Alan Eşleştirme" panelinden çözülür.
        # merchantSku VARYANT BAŞINA BENZERSIZ olmalı; yoksa HB tüm bedenleri tek ürüne indirger.
        # Gönderim kaynağı "Ürün Kart ID" ise kart id baz alınır (+ varyant soneki ile benzersizleşir).
        sku = _hb_variant_sku(product, variant, vi, sku_source, used_skus)
        if not sku:
            errors.add("stok kodu/barkod yok")
            continue
        bc = (str(_base_val("Barcode", variant) or "").strip()
              or (variant or {}).get("barcode") or product.get("barcode") or sku)
        attrs.setdefault("merchantSku", sku)
        attrs.setdefault("Barcode", bc)
        if variants:
            attrs.setdefault("VaryantGroupID", str(vgroup))
        attrs.setdefault("UrunAdi", str(_base_val("UrunAdi", variant) or product.get("name") or ""))
        attrs.setdefault("UrunAciklamasi",
                         str(_base_val("UrunAciklamasi", variant) or desc or product.get("name") or ""))
        mk = _base_val("Marka", variant)
        if mk not in (None, ""):
            attrs.setdefault("Marka", str(mk))
        gar = _base_val("GarantiSuresi", variant)
        gar_s = str(gar or "").strip()
        # HB tam sayı (ay) ister. Geçerli sayı VARSA onu gönder; YOKSA HİÇ GÖNDERME.
        # (Eski davranış sabit "24" basıyordu → kullanıcının girmediği hayalet garanti.)
        # Garanti zorunlu olan kategoride: "Varsayılan Alan Eşleştirme"den GarantiSuresi default'u gir.
        if gar_s.isdigit() and 1 <= int(gar_s) <= 99:
            attrs.setdefault("GarantiSuresi", str(int(gar_s)))
        # KDV (zorunlu) -> HB anahtarı "tax", tam sayı.
        # ÖNCE ürünün KENDİ KDV'si (product.vat_rate — Trendyol ile AYNI kaynak),
        # yoksa panel/temel "Varsayılan Alan Eşleştirme" default'u (10). Böylece HB ile
        # Trendyol aynı KDV'yi gönderir; kadın hazır giyim %10 sapması olmaz.
        _pv = product.get("vat_rate")
        try:
            _pv_ok = float(str(_pv).replace("%", "").replace(",", ".")) > 0 if _pv not in (None, "") else False
        except Exception:
            _pv_ok = False
        kdv_raw = _pv if _pv_ok else _base_val("kdv", variant)
        kdv_s = str(kdv_raw or "").strip().replace("%", "").replace(",", ".")
        if kdv_s:
            try:
                kdv_i = int(round(float(kdv_s)))
            except Exception:
                kdv_i = None
            if kdv_i and kdv_i > 0:
                attrs.setdefault("tax", str(kdv_i))
        img_src = (bfm.get("Image") or {}).get("source") or "images"
        if img_src == "images":
            for i, u in enumerate(imgs, 1):
                attrs.setdefault(f"Image{i}", u)
        attrs.setdefault("kg", str(_base_val("kg", variant) or "1"))
        # Şirket default'ları artık YALNIZ ana döngüde, DOĞRU HB özellik adına (resolve edilerek)
        # uygulanır. Eskiden buradaki dökme döngüsü, gerçek özellik adı olmayan default
        # anahtarlarını (HB attribute-ID "000002C" ve küçük-harf alias "yaka_stili") da payload'a
        # basıyordu → HB tanımadığı bu anahtarlar yüzünden ürünün ÖZELLİK BLOĞUNU reddedip
        # adıyla doğru gönderdiklerimizi de boş bırakıyordu. Güvenlik için yalnız GERÇEK HB
        # özellik adına denk gelen ve henüz set edilmemiş default'ları, attribute üzerinden
        # RESOLVE ederek ekleriz; ham ID/alias anahtar ASLA payload'a girmez.
        _valid_attr = {a.get("name"): a for a in hb_attrs_list if a.get("name")}
        for k, v in defaults.items():
            if v in (None, "") or k in attrs:
                continue
            a = _valid_attr.get(k)
            if not a:
                continue  # gerçek HB özellik adı değil (ID/alias) → payload'a basma
            rv = _hb_resolve_with_fallback(a, v, v)
            if rv not in (None, ""):
                attrs[k] = rv

        if missing_req:
            errors.add("zorunlu HB özellikleri eksik: " + ", ".join(sorted(set(missing_req))))
            continue
        # HB'nin zorunlu kıldığı temel alanlar payload'da dolu mu? (Garanti/Desi/KDV vb.)
        miss_base = [lbl for (pk, lbl) in base_required
                     if str(attrs.get(pk) or "").strip() == ""]
        if miss_base:
            errors.add("zorunlu HB temel alanı eksik: " + ", ".join(sorted(set(miss_base)))
                       + " (Varsayılan Alan Eşleştirme ekranından doldurun)")
            continue
        items.append({"categoryId": cat_val, "merchant": merchant_id, "attributes": attrs})

    if not items:
        return [], ("; ".join(sorted(errors)) if errors else "Gönderilebilir varyant yok")
    return items, None
def _hb_summarize_import_status(data):
    """HB içe-aktarım (import) durum yanıtını YAPI-BAĞIMSIZ özetler.
    HB sürüm/kategoriye göre farklı anahtarlar döndürebildiğinden sabit isme güvenmeyiz.
    Doner: {done, status, items:[{sku, ok(True/False/None), status, reason}], success, failed, processing}"""
    status, rows = "", None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        status = str(data.get("status") or data.get("trackingStatus") or data.get("state") or "").upper()
        for k in ("data", "items", "lineItems", "products", "productStatuses",
                  "validations", "results", "lines"):
            v = data.get(k)
            if isinstance(v, list):
                rows = v
                break
            if isinstance(v, dict):
                if not status:
                    status = str(v.get("status") or "").upper()
                for k2 in ("items", "lineItems", "products", "results", "data"):
                    if isinstance(v.get(k2), list):
                        rows = v[k2]
                        break
                if rows is not None:
                    break
    items, success, failed, processing = [], 0, 0, 0
    OK = {"SUCCESS", "DONE", "CREATED", "MATCHED", "COMPLETED", "OK", "APPROVED", "ACTIVE"}
    PROC = {"PROCESSING", "WAITING", "CONTINUE", "INPROGRESS", "IN_PROGRESS", "PENDING",
            "QUEUED", "WAITING_FOR_APPROVAL", "TRANSFERRING", "RECEIVED"}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        sku = (r.get("productMerchantSku") or r.get("merchantSku") or r.get("sku")
               or r.get("merchantSkuId") or r.get("stockCode") or r.get("productName") or "")
        st = str(r.get("status") or r.get("state") or r.get("result") or r.get("importStatus") or "").upper()
        reasons = []
        for ek in ("validationResults", "errors", "messages", "validationErrors",
                   "errorList", "failureReasons", "rejectReasons"):
            ev = r.get(ek)
            if isinstance(ev, list):
                for e in ev:
                    if isinstance(e, dict):
                        m = (e.get("message") or e.get("description") or e.get("reason")
                             or e.get("error") or e.get("detail"))
                        if m:
                            reasons.append(str(m))
                    elif isinstance(e, str) and e:
                        reasons.append(e)
            elif isinstance(ev, str) and ev:
                reasons.append(ev)
        for sk in ("reason", "errorMessage", "rejectReason"):
            if r.get(sk):
                reasons.append(str(r.get(sk)))
        reason = "; ".join(dict.fromkeys([x for x in reasons if x])) or None
        is_ok = (st in OK) or (r.get("isSuccess") is True) or (r.get("success") is True)
        if reason and not is_ok:
            failed += 1
            ok = False
        elif is_ok:
            success += 1
            ok = True
        else:
            processing += 1
            ok = None
        items.append({"sku": str(sku), "ok": ok, "status": st, "reason": reason})
    done = (status in {"DONE", "COMPLETED", "FINISHED", "SUCCESS", "FAILED", "ERROR"}) \
        or (bool(items) and processing == 0)
    return {"done": done, "status": status, "items": items,
            "success": success, "failed": failed, "processing": processing}
async def _hb_poll_import(client, tracking_id, attempts=8, delay=2.0):
    """create_products sonrası import durumunu SINIRLI süre yoklar (ingress-güvenli).
    Sonuçlandıysa (done + success/failed) erken döner; aksi halde son özeti döner."""
    import asyncio
    last = None
    for _ in range(max(1, attempts)):
        try:
            data = await asyncio.to_thread(client.get_product_tracking, tracking_id)
            last = _hb_summarize_import_status(data)
            last["raw"] = data
        except Exception as e:
            last = {"done": False, "status": "ERROR", "items": [],
                    "success": 0, "failed": 0, "processing": 0, "error": str(e)}
        if last.get("done") and (last.get("success") or last.get("failed")):
            break
        await asyncio.sleep(delay)
    return last or {"done": False, "items": [], "success": 0, "failed": 0, "processing": 0}
async def _hb_build_sku_to_hbsku_map(client, merchant_skus, chunk=100):
    """SADECE gonderilen merchantSku'lari HB'de arar (Listing sorgusu, scoped) ve
    merchantSku -> hepsiburadaSku haritasi doner. SALT-OKUNUR.
    ONEMLI -- performans: tum katalogu sayfalayip taramak YERINE (eski/yavas yaklasim,
    25 sayfa x 1000 urune kadar HTTP) yalniz ilgilenilen SKU'lari sorgular -> tek urun
    'tekrar aktar'inda bile tum magaza taranmasin diye.
    Amac: 'tekrar aktar' edilen bir urun HB'de ZATEN var mi (-> OZELLIK GUNCELLE) yoksa
    YENI mi (-> KATALOG GIRISI) ayrimini yapmak."""
    import asyncio
    out = {}
    uniq = sorted({str(s).strip().upper() for s in (merchant_skus or []) if s})
    if not uniq:
        return out
    for i in range(0, len(uniq), chunk):
        batch = uniq[i:i + chunk]
        try:
            d = await asyncio.to_thread(client.get_listings, 0, len(batch), batch, None)
            rows = (d.get("listings") if isinstance(d, dict) else d) or []
            if isinstance(d, dict) and not rows:
                rows = d.get("data") or []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                ms = str(r.get("merchantSku") or r.get("MerchantSku") or r.get("merchantSKU") or "").strip().upper()
                hb = r.get("hepsiburadaSku") or r.get("hbSku") or r.get("HepsiburadaSku") or r.get("hepsiburadaSKU")
                if ms and hb:
                    out[ms] = str(hb)
        except Exception:
            continue  # bu batch cozulemezse o kalemler 'yeni' kabul edilip create_products'a duser
    return out
def _hb_split_ticket_item(attrs: dict) -> dict:
    """create-shape 'attributes' (UrunAdi/UrunAciklamasi/Image1../tax/kg/...) sözlüğünü
    Ürün Güncelleme Servisi şemasına (productName/productDescription/image1../attributes)
    çevirir. Yapısal/katalog-only alanlar (merchantSku, Barcode, VaryantGroupID, tax, kg,
    GarantiSuresi, Marka) ticket'a YAZILMAZ — bu servis yalnız ad/açıklama/görsel/video/
    kategori-özelliği günceller; fiyat/stok/KDV zaten ayrı (Listing) kanaldan gidiyor."""
    a = dict(attrs)
    t: dict = {}
    pn = a.pop("UrunAdi", None)
    pd = a.pop("UrunAciklamasi", None)
    if pn:
        t["productName"] = pn
    if pd:
        t["productDescription"] = pd
    for k in list(a.keys()):
        if k.startswith("Image"):
            idx = k[5:]
            v = a.pop(k)
            if idx.isdigit() and 1 <= int(idx) <= 10 and v:
                t[f"image{idx}"] = v
    for sk in ("merchantSku", "Barcode", "VaryantGroupID", "tax", "kg", "GarantiSuresi", "Marka"):
        a.pop(sk, None)
    if a:
        t["attributes"] = a
    return t
async def _hb_poll_ticket(client, tracking_id, attempts=8, delay=2.0):
    """update_products sonrası ticket durumunu SINIRLI süre yoklar. Status endpoint'i
    tahmini olduğundan (bkz. get_update_ticket_status) tek denemede hata alırsa
    sessizce 'UNKNOWN' döner — ana akışı (ticket zaten HB'ye iletildi) bozmaz."""
    import asyncio
    last = None
    for _ in range(max(1, attempts)):
        try:
            data = await asyncio.to_thread(client.get_update_ticket_status, tracking_id)
            last = _hb_summarize_import_status(data)
            last["raw"] = data
        except Exception as e:
            last = {"done": False, "status": "UNKNOWN", "items": [],
                    "success": 0, "failed": 0, "processing": 0, "error": str(e)}
            break
        if last.get("done") and (last.get("success") or last.get("failed")):
            break
        await asyncio.sleep(delay)
    return last or {"done": False, "items": [], "success": 0, "failed": 0, "processing": 0}
@router.post("/hepsiburada/products/sync")
async def hb_sync_products(request: Request, current_user: dict = Depends(require_admin)):
    """Seçili ürünleri Hepsiburada kataloğuna gönderir (import). FilteredPushPanel sözleşmesi:
    body {stock_codes, barcodes, date_from, date_to, category_filters} → {successful, failed, ...}.
    NOT: Kategori-özellik eşleşmesi eksikse ilgili ürün atlanır; HB sandbox ile doğrulanmalıdır."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    payload = await request.json()
    query = await _build_product_query_from_payload(payload)
    products = await db.products.find(query, {"_id": 0}).to_list(length=None)
    products = _dedupe_products_by_stock_code(products)
    if not products:
        return {"successful": 0, "failed": 0, "message": "Filtreye uyan ürün bulunamadı"}
    items, skipped = [], []
    for p in products:
        built, e = await _build_hb_product_item(p, client.merchant_id)
        if e:
            skipped.append({"product_id": p.get("id"), "name": p.get("name"), "reason": e})
        else:
            items.extend(built)
    if not items:
        return {"successful": 0, "failed": len(skipped),
                "message": "Hiçbir ürün gönderilemedi (kategori/özellik eşleşmesi eksik).",
                "skipped": skipped}

    # 🔁 YENİ vs ZATEN-LİSTELİ ayrımı. "Tekrar Aktar" — yani daha önce HB'ye gönderilmiş
    # bir ürünü ikinci kez göndermek — create_products (/product/api/products/import) ile
    # YAPILAMAZ: o uç yalnız YENİ ürün girişi içindir, var olan ürünün ad/görsel/özellik
    # değişikliklerini HB'ye yansıtmayı GARANTİ ETMEZ. HB'nin resmi "Ürün Güncelleme
    # Servisi" (ticket-api, hbSku ile) bunun için var. Burada her kalemin merchantSku'sunu
    # HB katalogunda arar: zaten varsa GÜNCELLE (update_products), yoksa YENİ GİRİŞ (create_products).
    _send_skus = [str((it.get("attributes") or {}).get("merchantSku") or "") for it in items]
    try:
        sku_to_hb = await asyncio.wait_for(_hb_build_sku_to_hbsku_map(client, _send_skus), timeout=12)
    except asyncio.TimeoutError:
        # HB Listing sorgusu 12sn'de yanıt vermedi — Railway/Cloudflare gateway timeout'a
        # düşüp tarayıcıda "Network Error" görünmesindense, hepsini "yeni" kabul edip
        # eski (create_products) davranışa düş. Önceki HB'de var olan bir ürün bu durumda
        # create_products'a gider; HB import sırasında merchantSku zaten varsa kendi
        # tarafında reddeder/atlar (veri kaybı yok, yalnız o turda özellik güncellenmez).
        sku_to_hb = {}
    new_items, update_items, update_src = [], [], []
    for it in items:
        ms = str((it.get("attributes") or {}).get("merchantSku") or "").strip().upper()
        hb_sku = sku_to_hb.get(ms) if ms else None
        if hb_sku:
            t = {"hbSku": hb_sku}
            t.update(_hb_split_ticket_item(it.get("attributes") or {}))
            update_items.append(t)
            update_src.append(it)
        else:
            new_items.append(it)

    create_res, create_tracking_id = None, None
    if new_items:
        try:
            create_res = await asyncio.to_thread(client.create_products, new_items)
        except HepsiburadaError as e:
            await log_integration_event("hepsiburada", "product_import", "bulk", str(len(new_items)), "error", str(e))
            raise HTTPException(status_code=502, detail=str(e))
        _crd = (create_res or {}).get("data") or {}
        create_tracking_id = ((create_res or {}).get("trackingId") or (create_res or {}).get("tracking_id")
                              or (create_res or {}).get("id") or _crd.get("trackingId") or _crd.get("tracking_id"))

    update_res, update_tracking_id = None, None
    if update_items:
        try:
            update_res = await asyncio.to_thread(client.update_products, update_items)
            _urd = (update_res or {}).get("data") or {}
            update_tracking_id = ((update_res or {}).get("trackingId") or (update_res or {}).get("tracking_id")
                                  or (update_res or {}).get("id") or _urd.get("trackingId") or _urd.get("tracking_id"))
        except HepsiburadaError as e:
            await log_integration_event("hepsiburada", "product_update", "bulk", str(len(update_items)), "error", str(e))
            # Güncelleme başarısız olsa bile YENİ ürünler zaten gitmiş olabilir → akışı kesme,
            # hatayı mesajda raporla.
            update_res = {"error": str(e)}

    # 💰 KATALOG ≠ FİYAT/STOK. HB ürün importu fiyat/stok TAŞIMAZ (ayrı price/stock-uploads
    # kapısı). Eski davranış: "aktar" sadece katalog gönderiyordu → "fiyat stok aktarmadı".
    # Artık aynı SKU kaynağıyla fiyat/stok'u da gönderiyoruz (best-effort). Daha önce
    # aktarılmış/eşleşmiş ürünlerde anında uygulanır; HB henüz eşleştirmediği yeni
    # ürünlerde geçici olarak reddedebilir (uyarı olarak raporlanır, katalog yine gitti).
    price_stock_msg = ""
    price_stock = {"price_upload_id": None, "stock_upload_id": None, "errors": []}
    try:
        markup = await _hb_markup()
        price_source = await _hb_price_source()
        sku_source = await _hb_sku_source()
        listing_items = []
        for p in products:
            listing_items.extend(_hb_listing_items_from_product(p, markup, price_source, sku_source))
        if listing_items:
            no_price = sum(1 for it in listing_items if it.get("price") is None)
            price_stock = await _hb_push_stock_price(client, listing_items, True, True)
            if price_stock.get("errors"):
                price_stock_msg = " · ⚠️ fiyat/stok: " + "; ".join(price_stock["errors"])
            else:
                price_stock_msg = f" · {len(listing_items)} kalem fiyat/stok da gönderildi"
            if no_price:
                price_stock_msg += (f" · ⚠️ {no_price} kalemde fiyat 0/boş → fiyat GÖNDERİLMEDİ "
                                    f"(ürün fiyatını gir; stok yine gönderildi)")
    except Exception as ps_err:
        price_stock_msg = f" · ⚠️ fiyat/stok gönderilemedi: {ps_err}"

    tracking_id = create_tracking_id  # geriye-uyum: eski FE alanı = YENİ ürün takip id'si
    is_test = bool(getattr(client, "test", False))
    env_code = "sandbox" if is_test else "production"
    env_label = "SANDBOX (TEST — ürünler gerçek mağazada görünmez!)" if is_test else "CANLI (production)"

    # 🔎 İçe-aktarımı DOĞRULA. HB importu ASENKRON: create_products/update_products yalnız
    # "istek alındı" demektir, gerçekten işlenip işlenmediği (ya da hangi sebeple reddedildiği)
    # durum sorgusunda görünür. Hedefli/küçük gönderimlerde (≤60 kalem) kısa süre yoklayıp
    # GERÇEK sonucu mesaja koyuyoruz; büyük gönderimlerde hız için atlanır.
    import_result = None
    verify_msg = ""
    if create_tracking_id and len(new_items) <= 60:
        try:
            import_result = await asyncio.wait_for(_hb_poll_import(client, str(create_tracking_id)), timeout=10)
        except asyncio.TimeoutError:
            import_result = {"done": False, "items": [], "success": 0, "failed": 0, "processing": 0}
        if not import_result.get("done"):
            verify_msg += " · ⏳ YENİ ürünler hâlâ işleniyor — “İçe Aktarım Durumu”ndan kontrol edin"
        else:
            fail_rows = [it for it in import_result.get("items", []) if it.get("ok") is False]
            ok_n = import_result.get("success", 0)
            if fail_rows:
                detail = "; ".join(f"{it['sku']}: {it['reason'] or 'reddedildi'}" for it in fail_rows[:8])
                more = f" (+{len(fail_rows) - 8} daha)" if len(fail_rows) > 8 else ""
                verify_msg += f" · ❌ HB {len(fail_rows)} YENİ ürünü REDDETTİ → {detail}{more}"
            elif ok_n:
                verify_msg += f" · ✅ HB doğruladı: {ok_n} yeni ürün oluşturuldu"

    update_result = None
    update_verify_msg = ""
    if update_tracking_id and len(update_items) <= 60:
        try:
            update_result = await asyncio.wait_for(_hb_poll_ticket(client, str(update_tracking_id)), timeout=10)
        except asyncio.TimeoutError:
            update_result = {"done": False, "status": "UNKNOWN", "items": [], "success": 0, "failed": 0, "processing": 0}
        if update_result.get("status") == "UNKNOWN":
            update_verify_msg = (f" · ℹ️ {len(update_items)} ürünün özellikleri güncelleme talebiyle "
                                 f"HB'ye iletildi (durum sorgusu desteklenmiyor — “Ürün Güncelleme "
                                 f"Geçmişi”nden kontrol edin)")
        elif not update_result.get("done"):
            update_verify_msg = " · ⏳ özellik güncellemeleri hâlâ işleniyor"
        else:
            ufail = [it for it in update_result.get("items", []) if it.get("ok") is False]
            uok = update_result.get("success", 0)
            if ufail:
                udetail = "; ".join(f"{it['sku']}: {it['reason'] or 'reddedildi'}" for it in ufail[:8])
                update_verify_msg = f" · ❌ HB {len(ufail)} özellik güncellemesini REDDETTİ → {udetail}"
            elif uok:
                update_verify_msg = f" · ✅ HB doğruladı: {uok} ürünün özellikleri güncellendi"
    elif update_items:
        update_verify_msg = f" · {len(update_items)} ürünün özellikleri güncelleme talebiyle HB'ye iletildi"
    if isinstance(update_res, dict) and update_res.get("error"):
        update_verify_msg = f" · ❌ özellik güncelleme isteği HB'ye iletilemedi: {update_res['error']}"

    total_sent = len(new_items) + len(update_items)
    await log_integration_event(
        "hepsiburada", "product_import", "bulk", str(create_tracking_id or update_tracking_id or total_sent),
        "success" if not (import_result and [x for x in import_result.get("items", []) if x.get("ok") is False]) else "error",
        f"{len(new_items)} yeni ürün + {len(update_items)} özellik güncellemesi → {env_code.upper()} "
        f"({getattr(client,'base','')}), {len(skipped)} atlandı · Takip: {create_tracking_id}{verify_msg}{update_verify_msg}")
    return {"successful": total_sent, "failed": len(skipped), "tracking_id": tracking_id,
            "created": len(new_items), "updated": len(update_items),
            "update_tracking_id": update_tracking_id,
            "environment": env_code, "environment_label": env_label,
            "host": getattr(client, "base", ""), "is_test": is_test,
            "price_stock": price_stock,
            "import_result": import_result,
            "update_result": update_result,
            "message": f"{total_sent} ürün {('SANDBOX' if is_test else 'CANLI')} ortamına gönderildi"
                       + (f" ({len(new_items)} yeni, {len(update_items)} özellik güncellemesi)" if new_items and update_items
                          else (" (özellik güncellemesi)" if update_items and not new_items else ""))
                       + (f", {len(skipped)} atlandı (eşleşme eksik)" if skipped else "")
                       + price_stock_msg
                       + verify_msg
                       + update_verify_msg
                       + (f" · Takip: {create_tracking_id}" if create_tracking_id else "")
                       + (" — ⚠️ SANDBOX! Gerçek mağazada görünmez." if is_test else ""),
            "skipped": skipped, "raw": create_res, "update_raw": update_res}
@router.post("/hepsiburada/products/validate")
async def hb_validate_products(request: Request, current_user: dict = Depends(require_admin)):
    """Aktarım öncesi DOĞRULAMA — ürünlerin HB kategori eşleşmesi + stok kodunu kontrol eder.
    Body sync ile aynı. Dönüş: {valid_count, invalid_count, results}."""
    payload = await request.json()
    query = await _build_product_query_from_payload(payload)
    products = await db.products.find(query, {"_id": 0}).to_list(length=None)
    products = _dedupe_products_by_stock_code(products)
    results = []
    valid_count = invalid_count = 0
    sku_source = await _hb_sku_source()  # gönderimde kullanılan merchantSku kaynağı (Ürün ID vb.)

    def _display_sku(p):
        """Validate ekranındaki 'Stok Kodu' sütunu — GERÇEKTEN gidecek merchantSku'yu gösterir
        (stok kodu DEĞİL; kullanıcı 'Ürün ID' seçtiyse beden urun_id'leri)."""
        vs = p.get("variants") or []
        if vs:
            seen = set()
            skus = [_hb_variant_sku(p, v, i, sku_source, seen) for i, v in enumerate(vs)]
            skus = [s for s in skus if s]
            if skus:
                return skus[0] + (f"  +{len(skus) - 1}" if len(skus) > 1 else "")
        return _hb_variant_sku(p, None, 0, sku_source) or _resolve_stock_code(p) or p.get("barcode") or ""

    # Kategori eşleşmelerini bir kez yükle (eksik zorunluların HB geçerli-değerlerini eklemek için).
    cm_list = await db.category_mappings.find(
        {"marketplace": "hepsiburada", "marketplace_category_id": {"$nin": [None, ""]}}, {"_id": 0}
    ).to_list(length=5000)
    cm_by_id = {str(c.get("category_id")): c for c in cm_list}
    cm_by_name = {(c.get("category_name") or "").strip(): c for c in cm_list}
    valvals_cache: dict = {}  # hb_cat -> {norm(attr_name): [değer adları]}

    async def _hb_valid_values_map(hb_cat):
        ck = str(hb_cat)
        if ck in valvals_cache:
            return valvals_cache[ck]
        out = {}
        try:
            calist, _ = await _hb_category_attributes_for(hb_cat)  # cache'ten (build ısıttı)
            for a in (calist or []):
                nm = a.get("name")
                vals = [v.get("name") for v in (a.get("attributeValues") or []) if v.get("name")]
                if nm:
                    out[_hb_norm(nm)] = vals
        except Exception:
            pass
        valvals_cache[ck] = out
        return out

    def _parse_missing(err: str) -> list[str]:
        out, seen = [], set()
        for seg in (err or "").split(";"):
            seg = seg.strip()
            for key in ("zorunlu HB özellikleri eksik:", "zorunlu HB temel alanı eksik:"):
                if seg.startswith(key):
                    rest = seg[len(key):].split(" (")[0]
                    for nm in rest.split(","):
                        nm = nm.strip()
                        if nm and nm not in seen:
                            seen.add(nm)
                            out.append(nm)
        return out

    for p in products:
        # Gerçek gönderim mantığıyla doğrula: motor zorunlu HB özelliklerini ürün
        # verisinden türetir; türetemediği zorunluları sebep olarak raporlar.
        built, e = await _build_hb_product_item(p, "")
        # HB stok kodu = gönderimde kullanılan merchantSku (Ürün ID seçiliyse beden urun_id).
        sc = _display_sku(p)
        if e:
            invalid_count += 1
            names = _parse_missing(e)
            # HB'nin kabul ettiği geçerli değerleri + ürünün Trendyol verisinde BİREBİR bulunanları ekle.
            cm = (cm_by_id.get(str(p.get("category_id")))
                  or cm_by_name.get((p.get("category_name") or "").strip()))
            vmap = await _hb_valid_values_map(cm.get("marketplace_category_id")) if cm else {}
            local_p = _hb_collect_local(p, None)
            local_vals_norm = {_hb_norm(v): v for v in local_p.values() if v not in (None, "")}
            miss = []
            for nm in names:
                vals = vmap.get(_hb_norm(nm)) or []
                # Ürünün TY verisinde, bu HB özelliğinin geçerli bir değerine BİREBİR uyan var mı?
                ty_found = [vv for vv in vals if _hb_norm(vv) in local_vals_norm]
                miss.append({"name": nm,
                             "valid_values": (vals[:12] if 0 < len(vals) <= 80 else []),
                             "value_count": len(vals),
                             "ty_found": ty_found[:6]})
            results.append({"product_id": p.get("id"), "name": p.get("name"),
                            "category_name": p.get("category_name"),
                            "stock_code": sc, "barcode": p.get("barcode"),
                            "is_valid": False, "errors": ([e] if not miss else []),
                            "missing_required_attrs": miss, "unmatched_values": [],
                            "variant_count": 0})
        else:
            valid_count += 1
            results.append({"product_id": p.get("id"), "name": p.get("name"),
                            "category_name": p.get("category_name"),
                            "stock_code": sc, "barcode": p.get("barcode"),
                            "is_valid": True, "errors": [],
                            "missing_required_attrs": [], "unmatched_values": [],
                            "variant_count": len(built)})
    return {"valid_count": valid_count, "invalid_count": invalid_count, "results": results}
@router.post("/hepsiburada/products/{product_id}/set-category-default")
async def hb_set_category_default(product_id: str, request: Request,
                                  current_user: dict = Depends(require_admin)):
    """Validate kırmızı kutusundan TEK TIK: eksik bir zorunlu HB özelliğini, ürünün ait olduğu
    HB kategori eşleşmesine KATEGORİ SABİTİ (default_mappings) olarak kaydeder.
    Etki kategori geneli — o kategorideki TÜM ürünler bu değeri alır (bir kez gir, herkes alsın)."""
    body = await request.json()
    attr = (body.get("attr") or "").strip()
    value = (body.get("value") or "").strip()
    if not attr or not value:
        raise HTTPException(status_code=400, detail="attr ve value zorunlu")
    p = await db.products.find_one({"id": product_id}, {"_id": 0, "category_id": 1, "category_name": 1})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    cm = await db.category_mappings.find_one(
        {"marketplace": "hepsiburada", "category_id": p.get("category_id")}, {"_id": 0})
    if not cm:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": p.get("category_name")}, {"_id": 0})
    if not cm:
        raise HTTPException(status_code=404, detail="Bu ürün için HB kategori eşleşmesi yok")
    defaults = dict(cm.get("default_mappings") or {})
    defaults[attr] = value
    await db.category_mappings.update_one(
        {"category_id": cm.get("category_id"), "marketplace": "hepsiburada"},
        {"$set": {"default_mappings": defaults,
                  "updated_at": datetime.now(timezone.utc).isoformat()}})
    return {"success": True, "category_name": cm.get("category_name"),
            "message": f"'{attr} = {value}' → {cm.get('category_name')} kategorisine sabit eklendi"}
@router.post("/hepsiburada/products/{product_id}/set-product-attribute")
async def hb_set_product_attribute(product_id: str, request: Request,
                                   current_user: dict = Depends(require_admin)):
    """Validate kırmızı kutusundan TEK TIK (yalnız bu ürün): bir zorunlu HB özelliğini
    SADECE bu ürünün `hepsiburada_attributes` alanına yazar. Kategoriye dokunmaz —
    üründen ürüne değişen alanlar (Yaka Stili vb.) için doğru yol budur."""
    body = await request.json()
    attr = (body.get("attr") or "").strip()
    value = (body.get("value") or "").strip()
    if not attr or not value:
        raise HTTPException(status_code=400, detail="attr ve value zorunlu")
    p = await db.products.find_one({"id": product_id},
                                   {"_id": 0, "hepsiburada_attributes": 1, "name": 1})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    hb = dict(p.get("hepsiburada_attributes") or {})
    hb[attr] = value
    await db.products.update_one({"id": product_id},
                                 {"$set": {"hepsiburada_attributes": hb}})
    return {"success": True, "message": f"'{attr} = {value}' → bu ürüne uygulandı"}
@router.get("/hepsiburada/products/{product_id}/category-attributes")
async def hb_product_category_attributes(product_id: str, current_user: dict = Depends(require_admin)):
    """Ürün modalındaki 'Hepsiburada için Özellikler' bölümünü besler.
    HB kategorisi ÜRÜNDE durmaz; category_mappings'te (yerel kategori → HB kategori) durur.
    Burada onu çözüp HB'nin GERÇEK kategori özelliklerini (zorunlu + enum değerleri) döneriz →
    modaldeki 'Zorunlu - Boş' kırmızı bölüm otomatik dolar."""
    p = await db.products.find_one(
        {"id": product_id}, {"_id": 0, "category_id": 1, "category_name": 1})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    cm = await db.category_mappings.find_one(
        {"marketplace": "hepsiburada", "category_id": p.get("category_id")}, {"_id": 0})
    if not cm and p.get("category_name"):
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": p.get("category_name")}, {"_id": 0})
    hb_cat = (cm or {}).get("marketplace_category_id") or (cm or {}).get("hepsiburada_category_id")
    if not hb_cat:
        return {"attributes": [], "hb_category_id": None, "has_mapping": False}
    attrs, err = await _hb_category_attributes_for(hb_cat)
    return {"attributes": attrs or [], "hb_category_id": hb_cat, "has_mapping": True, "error": err}
@router.get("/hepsiburada/category-attributes/by-local")
async def hb_category_attributes_by_local(
    category_id: str = "", category_name: str = "",
    current_user: dict = Depends(require_admin)):
    """Ürün modalının 'Hepsiburada için Özellikler' bölümünü KAYDEDİLMEMİŞ üründe de besler.
    HB kategorisi üründe durmaz (category_mappings'te durur); burada onu YEREL kategoriden
    (id veya ad) çözeriz — push çekirdeği (_build_hb_product_item) ile AYNI kaynak. Böylece
    yeni üründe de HB'nin GERÇEK tüm kategori özellikleri (zorunlu + enum değerleri) gelir,
    yalnız 9 sabite düşmez. product.id gerektirmez."""
    cm = None
    if category_id:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_id": category_id}, {"_id": 0})
    if not cm and category_name:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": category_name}, {"_id": 0})
    hb_cat = (cm or {}).get("marketplace_category_id") or (cm or {}).get("hepsiburada_category_id")
    if not hb_cat:
        return {"attributes": [], "hb_category_id": None, "has_mapping": False}
    attrs, err = await _hb_category_attributes_for(hb_cat)
    return {"attributes": attrs or [], "hb_category_id": hb_cat, "has_mapping": True, "error": err}
async def _hb_resolve_local_to_hb(category_id, category_name):
    """Yerel kategoriyi (id veya ad) category_mappings üzerinden HB kategori id'sine çözer
    — by-product/by-local/push ile AYNI kaynak."""
    cm = None
    if category_id:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_id": category_id}, {"_id": 0})
    if not cm and category_name:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": category_name}, {"_id": 0})
    return (cm or {}).get("marketplace_category_id") or (cm or {}).get("hepsiburada_category_id"), cm
async def _hb_numeric_id_findings():
    """Tüm ürünlerin hepsiburada_attributes'ında DEĞER ADI yerine sızmış HB value-id'lerini
    bulur: kategori şemasında value-id'ye karşılık gelen ama geçerli değer ADI OLMAYAN
    herhangi bir değer — yalnız SAYISAL değil, "0004TYN"/"0002ULK" gibi ALFANÜMERİK HB
    value-id'leri de dahil (satıcı panelinde "özellik değer öneriniz reddedilmiştir/tanımlı
    değildir" diye dönen değerler tam olarak bunlardır). Beden '36' gibi gerçekten sayısal
    değer adlarına DOKUNMAZ (id_map'te karşılığı yoksa fix önerilmez). Salt-okunur."""
    findings = []
    schema_cache = {}
    map_cache = {}

    async def attrs_for(hb_cat):
        if hb_cat in schema_cache:
            return schema_cache[hb_cat]
        a, _ = await _hb_category_attributes_for(hb_cat)
        schema_cache[hb_cat] = a or []
        return schema_cache[hb_cat]

    async def hb_for(cid, cname):
        key = (cid, cname)
        if key in map_cache:
            return map_cache[key]
        hb, _cm = await _hb_resolve_local_to_hb(cid, cname)
        map_cache[key] = hb
        return hb

    cursor = db.products.find(
        {"hepsiburada_attributes": {"$exists": True, "$ne": {}}},
        {"_id": 0, "id": 1, "name": 1, "category_id": 1, "category_name": 1, "hepsiburada_attributes": 1})
    async for p in cursor:
        ha = p.get("hepsiburada_attributes")
        if not isinstance(ha, dict) or not ha:
            continue
        hb_cat = await hb_for(p.get("category_id"), p.get("category_name"))
        if not hb_cat:
            continue
        schema = await attrs_for(hb_cat)
        by_attr = {}
        for a in schema:
            an = _hb_norm(a.get("name"))
            id_map, name_set = {}, set()
            for v in (a.get("attributeValues") or []):
                vid, vn = v.get("id"), v.get("name")
                if vid is not None:
                    id_map[str(vid).strip().upper()] = vn
                if vn:
                    name_set.add(_hb_norm(vn))
            by_attr[an] = (id_map, name_set)
        prod_fix, prod_clear = {}, []
        for aname, val in ha.items():
            if val is None or val == "":
                continue
            sval = str(val).strip()
            info = by_attr.get(_hb_norm(aname))
            if not info:
                continue
            id_map, name_set = info
            if _hb_norm(sval) in name_set:
                continue  # zaten geçerli değer adı → dokunma
            sval_key = sval.upper()
            if sval_key in id_map and id_map[sval_key]:
                prod_fix[aname] = {"old": sval, "new": id_map[sval_key]}
            elif _hb_looks_like_leaked_id(sval):
                # ID-görünümlü ama BU şemada karşılığı yok (yanlış kategoriden sızmış/eski
                # cache) → isim'e çeviremeyiz, ama HB'ye ÇÖP olarak da gitmemeli. "clear"
                # listesine alınır (fix ile None'a çekilir, bir daha gönderilmez).
                prod_clear.append(aname)
        if prod_fix or prod_clear:
            findings.append({
                "product_id": p.get("id"),
                "name": (p.get("name") or "")[:60],
                "category": p.get("category_name"),
                "fixes": prod_fix,
                "clear": prod_clear})
    return findings
@router.get("/hepsiburada/attributes/numeric-id-scan")
async def hb_numeric_id_scan(current_user: dict = Depends(require_admin)):
    """SALT-OKUNUR. hepsiburada_attributes'ta değer adı yerine sızmış HB value-id'lerini
    raporlar — hem isme çevrilebilenler (fixes) hem de şemada karşılığı bulunamayıp
    temizlenmesi gerekenler (clear, ör. 'Renk: r' gibi anlamsız kalıntılar)."""
    f = await _hb_numeric_id_findings()
    total_fix = sum(len(x["fixes"]) for x in f)
    total_clear = sum(len(x["clear"]) for x in f)
    return {"products_affected": len(f), "values_to_fix": total_fix,
            "values_to_clear": total_clear, "findings": f}
@router.post("/hepsiburada/attributes/numeric-id-fix")
async def hb_numeric_id_fix(current_user: dict = Depends(require_admin)):
    """Sızmış HB value-id'lerini doğru değer ADINA çevirir; şemada karşılığı bulunamayan
    ID-görünümlü/anlamsız değerleri (ör. 'r', '0002ULK') TEMİZLER (None'a çeker — bir daha
    HB'ye çöp olarak gitmesinler). Scan ile AYNI tespit. Yazar."""
    f = await _hb_numeric_id_findings()
    fixed_products = fixed_values = cleared_values = 0
    for x in f:
        pid = x["product_id"]
        prod = await db.products.find_one({"id": pid}, {"_id": 0, "hepsiburada_attributes": 1})
        if not prod:
            continue
        ha = dict(prod.get("hepsiburada_attributes") or {})
        changed = False
        for aname, fx in x["fixes"].items():
            if str(ha.get(aname)).strip() == fx["old"]:
                ha[aname] = fx["new"]
                changed = True
                fixed_values += 1
        for aname in x.get("clear", []):
            if aname in ha and ha.get(aname) not in (None, ""):
                ha.pop(aname, None)
                changed = True
                cleared_values += 1
        if changed:
            await db.products.update_one({"id": pid}, {"$set": {"hepsiburada_attributes": ha}})
            fixed_products += 1
    return {"fixed_products": fixed_products, "fixed_values": fixed_values,
            "cleared_values": cleared_values}
@router.get("/hepsiburada/category-mapping-audit")
async def hb_category_mapping_audit(current_user: dict = Depends(require_admin)):
    """Hangi yerel kategori HB'ye eşli/eşsiz + her birinde kaç ürün. Eşsiz kategorideki
    ürünler HB'ye gidemez (kart 9 sabite düşer). Salt-okunur."""
    counts = {}
    async for p in db.products.find({}, {"_id": 0, "category_name": 1}):
        cn = p.get("category_name") or "(boş)"
        counts[cn] = counts.get(cn, 0) + 1
    cats = {}
    async for c in db.categories.find({}, {"_id": 0, "id": 1, "name": 1}):
        if c.get("name"):
            cats[c["name"]] = c.get("id")
    names = set(list(counts.keys()) + list(cats.keys()))
    mapped, unmapped = [], []
    for name in names:
        if name == "(boş)":
            continue
        hb, cm = await _hb_resolve_local_to_hb(cats.get(name), name)
        row = {
            "category": name,
            "product_count": counts.get(name, 0),
            "mapped": bool(hb),
            "hb_category_id": hb,
            "hb_category_name": (cm or {}).get("marketplace_category_name")
                                or (cm or {}).get("hepsiburada_category_name")}
        (mapped if hb else unmapped).append(row)
    mapped.sort(key=lambda r: -r["product_count"])
    unmapped.sort(key=lambda r: -r["product_count"])
    return {
        "total_products": sum(counts.values()),
        "category_count": len(names),
        "mapped_count": len(mapped),
        "unmapped_count": len(unmapped),
        "unmapped_product_total": sum(r["product_count"] for r in unmapped),
        "mapped": mapped,
        "unmapped": unmapped}
@router.get("/hepsiburada/products/{product_id}/debug-payload")
async def hb_debug_payload(product_id: str, current_user: dict = Depends(require_admin)):
    """SALT-OKUNUR teşhis. HB'ye HİÇBİR ŞEY göndermez. Bir ürün için:
      • import_items  : create_products'a gidecek TAM kalem(ler) {categoryId, merchant, attributes}
      • build_error   : kalem üretilemediyse sebebi (eksik zorunlu vb.)
      • listing_items : price/stock-uploads'a gidecek {merchantSku, price, availableStock}
      • hb_category_attributes : HB'nin bu kategori için döndürdüğü GERÇEK özellik adları+zorunluluk
      • hb_base_attributes     : HB'nin GERÇEK temel alan adları (KDV/Garanti/Desi vb. gerçek anahtar)
      • config        : merchantSku kaynağı, fiyat kaynağı, markup
    Amaç: 'HB neyi reddediyor' tahminini bitirmek — gönderdiğimiz anahtarlarla HB'nin
    beklediği anahtarları yan yana görüp KDV/Garanti/Fiyat anahtarını kesin düzeltmek."""
    from .category_mapping import _get_hb_client  # noqa: F401 (kimlik kontrolü gerekmez, salt okuma)
    p = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    cm = await db.category_mappings.find_one(
        {"marketplace": "hepsiburada", "category_id": p.get("category_id")}, {"_id": 0})
    if not cm:
        cm = await db.category_mappings.find_one(
            {"marketplace": "hepsiburada", "category_name": p.get("category_name")}, {"_id": 0})
    hb_cat = (cm or {}).get("marketplace_category_id")

    items, build_err = await _build_hb_product_item(p, "DEBUG")
    try:
        markup = await _hb_markup()
        price_source = await _hb_price_source()
        sku_source = await _hb_sku_source()
        listing_items = _hb_listing_items_from_product(p, markup, price_source, sku_source)
    except Exception as e:
        markup, price_source, sku_source, listing_items = 0.0, "auto", "stock_code", [f"hata: {e}"]

    cat_attrs, base_attrs = [], []
    if hb_cat:
        try:
            cat_attrs, _ = await _hb_category_attributes_for(hb_cat)
            base_attrs = await _hb_base_attributes_for(hb_cat)
        except Exception:
            pass

    return {
        "product": {"id": p.get("id"), "name": p.get("name"),
                    "category_id": p.get("category_id"), "category_name": p.get("category_name"),
                    "price": p.get("price"), "sale_price": p.get("sale_price"),
                    "member_price_1": p.get("member_price_1"), "stock": p.get("stock"),
                    "variant_count": len(p.get("variants") or [])},
        "hb_category_id": hb_cat,
        "config": {"merchantSku_source": sku_source, "price_source": price_source, "markup_pct": markup},
        "import_items": items, "build_error": build_err,
        "listing_items": listing_items,
        "hb_required_attributes": [a for a in (cat_attrs or []) if a.get("required")],
        "hb_base_attributes": [{"name": b.get("name"),
                                "mandatory": bool(b.get("mandatory") or b.get("required")
                                                  or b.get("mandatoryVariant")),
                                "type": b.get("type")}
                               for b in (base_attrs or []) if isinstance(b, dict)],
    }
@router.post("/hepsiburada/products/inventory-sync")
async def hb_inventory_sync(current_user: dict = Depends(require_admin)):
    """Tüm aktif ürünlerin güncel stok+fiyatını Hepsiburada listing'ine gönderir (StockPriceUpdatePanel)."""
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    products = await db.products.find({"is_active": True}, {"_id": 0}).to_list(length=None)
    markup = await _hb_markup()
    price_source = await _hb_price_source()
    sku_source = await _hb_sku_source()
    items = []
    for p in products:
        items.extend(_hb_listing_items_from_product(p, markup, price_source, sku_source))
    if not items:
        return {"message": "Gönderilecek stok/fiyat kalemi bulunamadı", "items_count": 0}
    res = await _hb_push_stock_price(client, items, True, True)
    status = "success" if not res["errors"] else "error"
    await log_integration_event("hepsiburada", "inventory_sync", "bulk", str(len(items)), status,
                                f"{len(items)} kalem" + (f" — {'; '.join(res['errors'])}" if res["errors"] else ""))
    return {"message": f"{len(items)} kalem stok/fiyat gönderildi"
                       + (f" — uyarı: {'; '.join(res['errors'])}" if res["errors"] else ""),
            "items_count": len(items), **res}
@router.post("/hepsiburada/products/autofill-attributes")
async def hb_autofill_attributes(request: Request, current_user: dict = Depends(require_admin)):
    """Filtreye uyan (boş = tüm HB-eşleşmiş kategori) ürünlerin `hepsiburada_attributes`
    alanını, HB kategori özelliklerinden + ürün verisinden OTOMATİK türetip kalıcı doldurur.
    Renk/Beden varyant-bazlıdır → ürün-alanına yazılmaz (gönderimde varyanttan türetilir);
    ürün-seviyesi özellikler (Cinsiyet, Materyal, Marka, Kalıp vb.) doldurulur.
    Mevcut (manuel) değerler KORUNUR — yalnız boş alanlar doldurulur."""
    payload = await request.json()
    query = await _build_product_query_from_payload(payload)
    products = await db.products.find(query, {"_id": 0}).to_list(length=None)
    cm_list = await db.category_mappings.find(
        {"marketplace": "hepsiburada", "marketplace_category_id": {"$nin": [None, ""]}}, {"_id": 0}
    ).to_list(length=3000)
    cm_by_id = {str(c.get("category_id")): c for c in cm_list}
    cm_by_name = {(c.get("category_name") or "").strip(): c for c in cm_list}
    attr_cache: dict = {}
    skip_norm = ("renk", "color", "beden", "size", "numara")
    updated = filled = scanned = 0
    for p in products:
        cm = (cm_by_id.get(str(p.get("category_id")))
              or cm_by_name.get((p.get("category_name") or "").strip()))
        if not cm or not cm.get("marketplace_category_id"):
            continue
        scanned += 1
        hb_cat = cm["marketplace_category_id"]
        ck = str(hb_cat)
        if ck not in attr_cache:
            alist, _e = await _hb_category_attributes_for(hb_cat)
            attr_cache[ck] = alist or []
        attrs_list = attr_cache[ck]
        if not attrs_list:
            continue
        local = _hb_collect_local(p, None)
        defaults = cm.get("default_mappings") or {}
        vmaps = cm.get("value_mappings") or {}
        cur = dict(p.get("hepsiburada_attributes") or {})
        changed = False
        for a in attrs_list:
            aname = a.get("name")
            if not aname or cur.get(aname):
                continue
            if any(w in _hb_norm(aname) for w in skip_norm):
                continue  # varyant-bazlı → gönderimde türetilir
            raw = _hb_local_for_attr(aname, local)
            if not raw:
                # AÇIK türetme — yalnız autofill'de: ürün adındaki kelime HB enum'una TAM eşleşirse
                # öneri olarak yaz. Kaydedilir, ürün kartında gözden geçirilebilir. (Aktarım addan
                # KAZIMAZ; burada bir kez, görünür ve onaylanabilir biçimde dolar.)
                raw = _hb_value_from_name(p.get("name"), a)
            orig_raw = raw
            if raw:
                aid = str(a.get("id"))
                mapped = vmaps.get(f"{aid}|{raw}")
                if not mapped and isinstance(vmaps.get(aid), dict):
                    mapped = vmaps[aid].get(str(raw))
                if mapped:
                    raw = mapped
            if not raw:
                raw = defaults.get(aname) or defaults.get(str(a.get("id")))
            if not raw:
                continue
            rv = _hb_resolve_with_fallback(a, raw, orig_raw)
            if rv not in (None, ""):
                cur[aname] = rv
                changed = True
                filled += 1
        if changed:
            await db.products.update_one({"id": p["id"]}, {"$set": {"hepsiburada_attributes": cur}})
            updated += 1
    return {"success": True, "scanned": scanned, "updated_products": updated, "filled_values": filled,
            "message": f"{updated} üründe {filled} HB özelliği otomatik dolduruldu"
                       + (" · Renk/Beden gönderimde varyanttan gelir" if updated else "")}
@router.get("/hepsiburada/products/tracking/{tracking_id}")
async def hb_product_tracking(tracking_id: str, current_user: dict = Depends(require_admin)):
    """Ürün import (tracking) durumunu döner."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.get_product_tracking, tracking_id)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.get("/hepsiburada/products/by-status")
async def hb_products_by_status(product_status: str = "WAITING", task_status: bool = False,
                                page: int = 0, size: int = 100,
                                current_user: dict = Depends(require_admin)):
    """Statü bazlı ürün listesi (WAITING, MATCHED, REJECTED, CREATED ...)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.get_products_by_status, product_status, task_status, page, size)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.get("/hepsiburada/env-status")
async def hb_env_status(current_user: dict = Depends(require_admin)):
    """HB ortamının NEREDEN çözüldüğünü gösterir (sandbox tuzağı teşhisi).
    Kural: marketplace_accounts veya db.settings kaynaklarından biri 'canlı/production'
    ise CANLI; ikisi de değilse sandbox."""
    from .category_mapping import _get_hb_client
    acc = await db.marketplace_accounts.find_one({"key": "hepsiburada"}, {"_id": 0}) or {}
    cr = acc.get("credentials") or {}
    s = await db.settings.find_one({"id": "hepsiburada"}, {"_id": 0}) or {}
    env_acc = (cr.get("env") or cr.get("mode") or "").strip().lower()
    env_set = (s.get("mode") or s.get("env") or "").strip().lower()
    acc_has_creds = bool((cr.get("merchant_id") or "").strip() and (cr.get("secret_key") or cr.get("password") or "").strip() and (cr.get("dev_username") or "").strip())
    set_has_creds = bool((s.get("merchant_id") or "").strip() and (s.get("secret_key") or s.get("password") or "").strip() and (s.get("dev_username") or "").strip())
    client, err = await _get_hb_client()
    resolved = host = cred_source = None
    if client is not None:
        resolved = "sandbox" if getattr(client, "test", False) else "production"
        host = getattr(client, "base", "")
        cred_source = getattr(client, "_cred_source", None)
    return {
        "resolved_environment": resolved,
        "host": host,
        "cred_source": cred_source,
        "marketplace_accounts": {"has_creds": acc_has_creds, "env": env_acc or "(boş)"},
        "settings": {"has_creds": set_has_creds, "env": env_set or "(boş)"},
        "error": err,
        "note": "Kimlik + ortam AYNI kaynaktan alınır. cred_source hangi ekranı gösteriyorsa "
                "ortamı O ekranda ayarla VE o ekrandaki kimlikler o ortama ait olsun "
                "(SIT/sandbox kimliği ≠ canlı kimliği). Uyuşmazlık = HB 403.",
    }
@router.get("/hepsiburada/reconcile/preview")
async def hb_reconcile_preview(markup: float = 25.0, active_only: bool = True,
                               max_pages: int = 25, page_size: int = 1000,
                               current_user: dict = Depends(require_admin)):
    """SALT-OKUNUR mutabakat önizlemesi — hiçbir şey yazmaz/silmez.
    Site ürünleri ile HB kataloğunu merchantSku + barkod üzerinden karşılaştırır:
      - missing_on_hb: sitede olup HB'de olmayan (→ aktarılacaklar; fiyat = taban × (1+markup/100))
      - orphan_on_hb : HB'de olup site SKU'su/barkodu ile eşleşmeyen (→ silinecek/listelenecek aday)
    """
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    is_test = bool(getattr(client, "test", False))
    price_source = await _hb_price_source()
    sku_source = await _hb_sku_source()

    # 1) Site ürünleri -> beklenen HB kalemleri (merchantSku -> {price, stock, ürün})
    q = {"is_active": True} if active_only else {}
    products = await db.products.find(q, {"_id": 0}).to_list(length=None)
    products = _dedupe_products_by_stock_code(products)
    site_sku_map: dict = {}
    site_barcodes: set = set()
    for p in products:
        for b in ([p.get("barcode")] + [v.get("barcode") for v in (p.get("variants") or [])]):
            b = str(b or "").strip()
            if b:
                site_barcodes.add(b)
        for it in _hb_listing_items_from_product(p, markup, price_source, sku_source):
            sk = str(it.get("merchantSku") or "").strip()
            if sk and sk not in site_sku_map:
                site_sku_map[sk] = {"product_id": p.get("id"), "name": p.get("name"),
                                    "merchantSku": sk, "price": it.get("price"),
                                    "stock": it.get("availableStock")}

    # 2) HB kataloğu (sayfalı, salt-okunur)
    hb_sku_set: set = set()
    hb_items: list = []
    pages = 0
    truncated = False
    try:
        for page in range(max_pages):
            d = await asyncio.to_thread(client.get_all_products, page, page_size)
            rows = (d.get("data") if isinstance(d, dict) else d) or []
            if not rows:
                break
            for r in rows:
                if not isinstance(r, dict):
                    continue
                msku = str(r.get("merchantSku") or r.get("merchantSKU") or r.get("sku") or "").strip()
                bc = str(r.get("barcode") or "").strip()
                hbsku = str(r.get("hepsiburadaSku") or r.get("hbSku") or r.get("hepsiburadaSKU") or "").strip()
                nm = r.get("productName") or r.get("name") or r.get("title") or ""
                st = r.get("status") or r.get("productStatus") or ""
                if msku:
                    hb_sku_set.add(msku)
                hb_items.append({"merchantSku": msku, "hepsiburadaSku": hbsku, "barcode": bc,
                                 "name": nm, "status": st})
            pages += 1
            if isinstance(d, dict) and d.get("last") is True:
                break
        else:
            truncated = True
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=f"HB ürün listesi alınamadı: {e}")

    # 3) Karşılaştırma
    missing = [v for k, v in site_sku_map.items() if k not in hb_sku_set]
    matched = sum(1 for k in site_sku_map if k in hb_sku_set)
    orphans = [r for r in hb_items
               if r["merchantSku"] and r["merchantSku"] not in site_sku_map
               and (not r["barcode"] or r["barcode"] not in site_barcodes)]

    CAP = 1000
    return {
        "environment": "sandbox" if is_test else "production",
        "is_test": is_test, "host": getattr(client, "base", ""),
        "markup": markup, "price_source": price_source, "sku_source": sku_source,
        "site_product_count": len(products), "site_sku_count": len(site_sku_map),
        "hb_catalog_count": len(hb_items), "hb_pages_scanned": pages, "hb_truncated": truncated,
        "matched_count": matched,
        "missing_on_hb": {"count": len(missing), "items": missing[:CAP], "capped": len(missing) > CAP},
        "orphan_on_hb": {"count": len(orphans), "items": orphans[:CAP], "capped": len(orphans) > CAP},
        "note": "SALT-OKUNUR önizleme. Hiçbir ürün gönderilmedi/güncellenmedi/silinmedi.",
    }
@router.get("/hepsiburada/orders/{order_number}")
async def hb_order_detail(order_number: str, current_user: dict = Depends(require_admin)):
    """Sipariş detayını OMS'ten getirir."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    raw_no = order_number[2:] if order_number.upper().startswith("HB") else order_number
    try:
        data = await asyncio.to_thread(client.get_order_detail, raw_no)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.get("/hepsiburada/packages")
async def hb_packages(offset: int = 0, limit: int = 100, current_user: dict = Depends(require_admin)):
    """Paket listesini döner."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.get_packages, offset, limit)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/packages")
async def hb_create_package(req: HbPackageReq, current_user: dict = Depends(require_admin)):
    """Kalemleri paketler (kargoya hazırlar)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not req.line_items:
        raise HTTPException(status_code=400, detail="line_items boş")
    try:
        data = await asyncio.to_thread(client.package_items, req.line_items, req.parcel_quantity, req.deci)
        await log_integration_event("hepsiburada", "package", "order", str(len(req.line_items)), "success", "Paketlendi")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.put("/hepsiburada/packages/{package_number}/invoice")
async def hb_send_invoice(package_number: str, req: HbInvoiceReq, current_user: dict = Depends(require_admin)):
    """Pakete fatura linki iletir."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.send_invoice, package_number, req.invoice_link)
        await log_integration_event("hepsiburada", "send_invoice", "package", package_number, "success", "Fatura iletildi")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        # 5xx DÖNDÜRME: Cloudflare origin 5xx'i kendi HTML sayfasıyla değiştirip HB'nin gerçek
        # hata metnini gizliyordu. 200 + gövdede gerçek hata ile ilet (panelde görünür olsun).
        _err = str(e)
        await log_integration_event("hepsiburada", "send_invoice", "package", package_number, "error", _err[:500])
        return {"success": False, "error": _err}
@router.get("/hepsiburada/packages/{package_number}/label")
async def hb_cargo_label(package_number: str, fmt: str = "base64zpl", current_user: dict = Depends(require_admin)):
    """Hepsiburada kargo etiketini döner (zpl | base64zpl | png)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.get_cargo_label, package_number, fmt)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.put("/hepsiburada/packages/{package_number}/cargo")
async def hb_change_cargo(package_number: str, req: HbCargoReq, current_user: dict = Depends(require_admin)):
    """Paketin kargo firmasını değiştirir."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.change_package_cargo, package_number, req.cargo_company_short_name)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/lineitems/{line_item_id}/cancel")
async def hb_cancel_line(line_item_id: str, req: HbCancelReq = None, current_user: dict = Depends(require_admin)):
    """Sipariş kalemini iptal eder (para cezasına tabidir)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    reason = (req.reason_id if req else "83")
    try:
        data = await asyncio.to_thread(client.cancel_line_item, line_item_id, reason)
        await log_integration_event("hepsiburada", "cancel_line", "lineitem", line_item_id, "success", f"İptal (sebep {reason})")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/packages/{package_number}/deliver")
async def hb_mark_delivered(package_number: str, body: dict = Body(default={}),
                            current_user: dict = Depends(require_admin)):
    """Teslim edildi bilgisi gönderir."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.send_delivered, package_number,
                                       body.get("received_by"), body.get("received_date"),
                                       body.get("digital_codes"))
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.get("/hepsiburada/claims")
async def hb_claims(status: Optional[str] = None, offset: int = 0, limit: int = 100,
                    current_user: dict = Depends(require_admin)):
    """Talep (iade) listesini döner. status verilirse statü bazlı."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        if status:
            data = await asyncio.to_thread(client.get_claims_by_status, status, offset, limit)
        else:
            data = await asyncio.to_thread(client.get_claims, offset, limit)
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/claims/{claim_number}/accept")
async def hb_accept_claim(claim_number: str, current_user: dict = Depends(require_admin)):
    """Talebi (iadeyi) kabul eder."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.accept_claim, claim_number)
        await log_integration_event("hepsiburada", "accept_claim", "claim", claim_number, "success", "İade kabul")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))
@router.post("/hepsiburada/claims/{claim_number}/reject")
async def hb_reject_claim(claim_number: str, req: HbClaimRejectReq, current_user: dict = Depends(require_admin)):
    """Talebi (iadeyi) reddeder. reason: HB ret sebep kodu (int)."""
    import asyncio
    from hepsiburada_client import HepsiburadaError
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        data = await asyncio.to_thread(client.reject_claim, claim_number, req.reason, req.merchant_statement)
        await log_integration_event("hepsiburada", "reject_claim", "claim", claim_number, "success", f"İade ret (sebep {req.reason})")
        return {"success": True, "data": data}
    except HepsiburadaError as e:
        raise HTTPException(status_code=502, detail=str(e))


# =====================================================================
#  HB CLAIMS (İADE) DB SENKRONU + GİDER PUSULASI KÖPRÜSÜ + 30 GÜN BACKFILL
#
#  TASARIM: HB iade talepleri Trendyol'la AYNI iade/GP ekranını kullanır.
#  Kayıtlar db.trendyol_claims koleksiyonuna platform="hepsiburada" ile
#  upsert edilir; böylece GP üretimi (/trendyol/claims/{id}/gider-pusulasi),
#  onay/ret, restock_claim_once ve Excel export uçları DEĞİŞİKSİZ çalışır.
#  Liste ucu (GET /trendyol/claims) platform paramına göre süzer.
# =====================================================================

_HB_CLAIM_STATUS_TO_TY = {
    # HB claim statüsü -> ekran kovalarının beklediği Trendyol statü seti
    "accepted": "Accepted", "refunded": "Accepted", "refund": "Accepted",
    "rejected": "Rejected",
    "cancelled": "Cancelled", "canceled": "Cancelled",
    "awaitingaction": "WaitingInAction", "indispute": "WaitingInAction",
    "waitingaction": "WaitingInAction", "inreview": "WaitingInAction",
}


def _hb_claim_status(raw: str) -> str:
    key = str(raw or "").replace(" ", "").replace("_", "").lower()
    return _HB_CLAIM_STATUS_TO_TY.get(key, "Created")


def _hb_claim_items_raw(claim: dict) -> list:
    for k in ("items", "claimItems", "lineItems", "details", "claimLineItems"):
        v = claim.get(k)
        if isinstance(v, list) and v:
            return v
    return [claim]  # tek kalemlik talep: alanlar claim kökünde


async def _hb_claim_norm(claim: dict) -> dict | None:
    """HB claim JSON'unu Trendyol claim şemasına normalize eder.
    Alan adları HB tarafında değişkenlik gösterebildiği için savunmacıdır;
    ham yanıt her zaman raw_data'da saklanır (gerekirse map buradan inceltilir)."""
    cid = str(_hb_g(claim, "claimNumber", "claimId", "number", "id") or "").strip()
    if not cid:
        return None
    onum = str(_hb_g(claim, "orderNumber", "orderId") or "").strip()
    st = _hb_claim_status(_hb_g(claim, "status", "claimStatus", "state"))
    ctype_raw = str(_hb_g(claim, "type", "claimType", "requestType") or "").lower()
    ctype = "CANCEL" if ("cancel" in ctype_raw or "iptal" in ctype_raw) else "RETURN"

    # Sipariş kaydı (2 dk cron'la panelde) — müşteri adı + kalem barkod/fiyat zenginleştirme.
    # DİKKAT: claim'deki orderNumber HB PREFIXSİZ gelir ("4600095398"), yerel kayıt ise
    # map_hepsiburada_order ile "HB4600095398" yazılır — her iki biçim (ve baştaki sıfırlar
    # atılmış hali) denenir, yoksa join hiçbir zaman tutmaz.
    order = {}
    if onum:
        cands = [onum, f"HB{onum}"]
        stripped = onum.lstrip("0")
        if stripped and stripped != onum:
            cands += [stripped, f"HB{stripped}"]
        order = await db.orders.find_one(
            {"order_number": {"$in": cands}, "platform": "hepsiburada"}, {"_id": 0}) or {}
    o_items = order.get("items") or []

    def _order_match(sku: str, name: str) -> dict:
        s = str(sku or "").strip()
        for it in o_items:
            for k in ("barcode", "sku", "merchant_sku", "product_id", "urun_id", "hb_sku", "hbSku"):
                if s and str(it.get(k) or "").strip() == s:
                    return it
        if name:
            n = str(name).strip().lower()
            for it in o_items:
                if n and n in str(it.get("name") or "").lower():
                    return it
        return {}

    items, refund = [], 0.0
    reason_txt = str(_hb_g(claim, "reason", "claimReason", "customerReason", "Reason", "explanation") or "")
    if isinstance(claim.get("reason"), dict):
        reason_txt = str(_hb_g(claim["reason"], "name", "description", "text") or reason_txt)
    for ci in _hb_claim_items_raw(claim):
        if not isinstance(ci, dict):
            continue
        # HB claim'i DÜZ yapıdadır: 'sku' = HB katalog kodu (HBCV...), 'MerchantSku' (büyük M)
        # = bizim varyant urun_id'miz; fiyat 'priceAmount'/'totalPriceAmount' anahtarındadır
        # ve ürün ADI alanı hiç yoktur — ad/barkod yerel katalogdan çözülür.
        hb_sku = str(_hb_g(ci, "sku", "hbSku", "listingId") or "")
        ms = str(_hb_g(ci, "MerchantSku", "merchantSku", "merchantSKU") or "")
        sku = ms or hb_sku
        name = str(_hb_g(ci, "productName", "name", "product") or "")
        qty = int(_hb_g(ci, "quantity", "qty", default=1) or 1)
        price = _hb_money(_hb_g(ci, "price", "unitPrice", "priceAmount",
                                "totalPriceAmount", "totalPrice", "amount", default=0))
        r = _hb_g(ci, "reason", "claimReason", "Reason", "explanation")
        if isinstance(r, dict):
            r = _hb_g(r, "name", "description", "text")
        oi = _order_match(ms, name) or _order_match(hb_sku, name)
        barcode = str(_hb_g(ci, "barcode", "productBarcode") or oi.get("barcode") or "")
        if not price:
            price = float(oi.get("price") or oi.get("unit_price") or 0)
        if not name:
            name = str(oi.get("name") or "")
        # Sipariş yerelde yoksa: MerchantSku (varyant urun_id) üzerinden katalogdan çöz —
        # ad + barkod + beden siparişten bağımsız gelir.
        v_size = str(oi.get("size") or "")
        if ms and (not name or not barcode):
            _mk = [ms]
            _msr = ms.lstrip("0")
            if _msr and _msr != ms:
                _mk.append(_msr)
            prod = await db.products.find_one(
                {"variants.urun_id": {"$in": _mk}}, {"_id": 0, "name": 1, "variants": 1})
            if not prod:
                try:
                    prod = await db.products.find_one(
                        {"variants.urun_id": int(ms)}, {"_id": 0, "name": 1, "variants": 1})
                except Exception:
                    prod = None
            if prod:
                name = name or str(prod.get("name") or "")
                for vv in (prod.get("variants") or []):
                    if str(vv.get("urun_id") or "") in (_mk + ([str(int(ms))] if ms.isdigit() else [])):
                        barcode = barcode or str(vv.get("barcode") or "")
                        v_size = v_size or str(vv.get("size") or vv.get("beden") or "")
                        break
        items.append({
            "claim_item_id": str(_hb_g(ci, "id", "lineItemId", "claimItemId") or ""),
            "productName": name, "barcode": barcode, "merchantSku": sku,
            "hb_sku": hb_sku, "size": v_size,
            "unit_price": price, "discount_amount": 0, "price": price,
            "quantity": qty, "reason": str(r or reason_txt or ""),
        })
        refund += price * qty
    if not reason_txt and items:
        reason_txt = items[0].get("reason", "")

    created_raw = _hb_g(claim, "createdDate", "createdAt", "claimDate", "date")
    created_iso = ""
    if created_raw:
        try:
            if isinstance(created_raw, (int, float)):
                created_iso = datetime.fromtimestamp(
                    (created_raw / 1000 if created_raw > 10**11 else created_raw),
                    tz=timezone.utc).isoformat()
            else:
                created_iso = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")).isoformat()
        except Exception:
            created_iso = str(created_raw)

    ship = order.get("shipping_address") or {}
    cust = str(_hb_g(claim, "customerName", "customer") or "").strip()
    if isinstance(claim.get("customer"), dict):
        cust = str(_hb_g(claim["customer"], "name", "fullName") or cust)
    if not cust:
        cust = f"{ship.get('first_name', '')} {ship.get('last_name', '')}".strip() \
               or str(order.get("customer_name") or "")

    # Kargo: HB claim'de takip bilgisi kökte değil Deliveries[] altındadır
    dels = claim.get("Deliveries") or claim.get("deliveries") or []
    d0 = dels[0] if isinstance(dels, list) and dels and isinstance(dels[0], dict) else {}
    cc = d0.get("CargoCompany") if isinstance(d0.get("CargoCompany"), dict) else {}
    cargo_no = str(_hb_g(claim, "cargoTrackingNumber", "trackingNumber")
                   or _hb_g(d0, "TrackingNumber", "DeliveryCode") or "")
    cargo_name = str(_hb_g(claim, "cargoCompany", "cargoProviderName")
                     or _hb_g(cc, "Name", "Alias") or "Hepsiburada Marketplace")
    # İade tutarı: HB gerçek refundAmount verirse onu kullan (kalem toplamı yedek)
    raw_refund = _hb_money(_hb_g(claim, "refundAmount", "refundedAmount", default=0))

    return {
        "claim_id": f"HB-{cid}",          # TY claim id'leriyle çakışmasın
        "hb_claim_number": cid,           # HB accept/reject uçları bu numarayı kullanır
        "platform": "hepsiburada",
        "order_number": onum,
        "claim_type": ctype,
        "claim_reason": reason_txt,
        "claim_status": st,
        "customer_name": cust,
        "created_date": created_iso or datetime.now(timezone.utc).isoformat(),
        "items": items,
        "refund_amount": round(raw_refund or refund, 2),
        "invoice_number": str(order.get("invoice_number") or ""),
        "cargo_tracking_number": cargo_no,
        "cargo_provider_name": cargo_name,
        "raw_data": claim,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _sync_hepsiburada_claims_core(days_back: int = 60) -> dict:
    """HB OMS claims -> db.trendyol_claims (platform=hepsiburada) upsert.
    Endpoint + scheduler ortak çekirdeği. Durum geçişleri her turda tazelenir;
    admin manuel kilit (manual_locked) koyduysa durum EZİLMEZ (TY ile aynı kural)."""
    import asyncio as _aio
    import uuid as _uuid
    from .category_mapping import _get_hb_client
    client, err = await _get_hb_client()
    if err:
        return {"success": False, "error": err, "synced": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days_back or 60)))
    synced, offset, limit = 0, 0, 50
    for _page in range(20):  # emniyet: en çok 1000 kayıt/tur
        try:
            resp = await _aio.to_thread(client.get_claims, offset, limit)
        except Exception as e:
            return {"success": False, "error": str(e), "synced": synced}
        rows = _hb_normalize_lines(resp)
        if not isinstance(rows, list) or not rows:
            break
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            doc = await _hb_claim_norm(raw)
            if not doc:
                continue
            # days_back süzgeci (tarih parse edilebildiyse)
            try:
                cd = datetime.fromisoformat(doc["created_date"].replace("Z", "+00:00"))
                if cd.tzinfo is None:
                    cd = cd.replace(tzinfo=timezone.utc)
                if cd < cutoff:
                    continue
            except Exception:
                pass
            existing = await db.trendyol_claims.find_one(
                {"claim_id": doc["claim_id"]},
                {"_id": 0, "manual_locked": 1, "amount_overridden": 1,
                 "return_approved_at": 1, "return_rejected_at": 1})
            # MANUEL TUTAR KORUMASI (Trendyol ile SİMETRİK): bir HB iadesi elle düzeltildiyse
            # (set-amount → amount_overridden) periyodik senkron tutarı/kalemleri HB değeriyle
            # EZMESİN — yoksa 30 dk'da bir "düzelttim ama yine bozuldu" olur. Durum/kargo tazelenir.
            if existing and existing.get("amount_overridden"):
                doc.pop("refund_amount", None)
                doc.pop("items", None)
            if existing and existing.get("manual_locked"):
                doc.pop("claim_status", None)  # manuel durum korunur; kalan alanlar tazelenir
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                if doc.get("claim_status") == "Accepted" and not (existing or {}).get("return_approved_at"):
                    doc["return_approved_at"] = now_iso
                if doc.get("claim_status") in ("Rejected", "Cancelled") and not (existing or {}).get("return_rejected_at"):
                    doc["return_rejected_at"] = now_iso
            await db.trendyol_claims.update_one(
                {"claim_id": doc["claim_id"]},
                {"$set": doc, "$setOnInsert": {"id": str(_uuid.uuid4()),
                                               "created_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True)
            synced += 1
        if len(rows) < limit:
            break
        offset += limit
    # DENETİM HATA-1 (HB ayağı): onaylanmış HB iadelerini sipariş durumuna yansıt
    # (yalnız statü + iz alanları; stok/kupon mantığına dokunulmaz).
    try:
        from .integrations_common import apply_accepted_claims_to_orders
        await apply_accepted_claims_to_orders(platforms=["hepsiburada"], dry_run=False)
    except Exception as _ae:
        logger.error(f"[hb claims->orders return] {_ae}")
    return {"success": True, "synced": synced}


@router.get("/hepsiburada/claims/sync")
async def hb_claims_sync(days_back: int = 60, current_user: dict = Depends(require_admin)):
    """HB iade taleplerini yerel iade ekranına (Trendyol ile ortak) senkronlar."""
    res = await _sync_hepsiburada_claims_core(days_back=days_back)
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=res.get("error") or "HB claims senkron hatası")
    await log_integration_event("hepsiburada", "claims_sync", "claim", "", "success",
                                f"HB iade senkronu: {res['synced']} kayıt")
    return {"success": True, "message": f"{res['synced']} HB iade kaydı senkronlandı", **res}


# ---------------------------------------------------------------------
#  TEK SEFERLİK BACKFILL — son N günün HB siparişleri + iptalleri + iadeleri
# ---------------------------------------------------------------------
async def _hb_backfill_run(days: int, decrement_stock: bool):
    """Arka planda çalışır; ilerleme integration_logs'a düşer.
    Kaynaklar: (1) açık siparişler (tarihsiz open/unpacked listesi),
    (2) paketler — 24 saatlik dilimlerle (paketlenmiş/kargolanmış geçmiş siparişler),
    (3) iptaller — 24 saatlik dilimlerle (HB yalnız son 1 aylık iptali verir),
    (4) iadeler (claims senkronu)."""
    import asyncio as _aio
    from .category_mapping import _get_hb_client
    days = max(1, min(int(days or 30), 30))
    client, err = await _get_hb_client()
    if err:
        await log_integration_event("hepsiburada", "backfill", "job", "", "failed", f"HB kimliği yok: {err}")
        return
    # Önceki koşulardan sızmış bozuk kayıtlar (sipariş no çözülemeyen "HB?") temizlenir
    try:
        await db.orders.delete_many({"order_number": {"$in": ["HB?", "?", "HB"]},
                                     "platform": "hepsiburada"})
    except Exception:
        pass

    def _deep_find(d, names, depth=0):
        """Sözlükte (nested dahil, 4 seviye) adı names kümesine uyan ilk dolu değeri döner.
        HB'nin shipped/delivered paket şemaları alan adlarını farklı/nested taşıyabiliyor."""
        if depth > 4 or not isinstance(d, dict):
            return ""
        for k, v in d.items():
            kl = str(k).lower().replace("_", "").replace("-", "")
            if kl in names and v not in (None, "", []) and not isinstance(v, (dict, list)):
                return v
        for v in d.values():
            if isinstance(v, dict):
                r = _deep_find(v, names, depth + 1)
                if r not in (None, ""):
                    return r
            elif isinstance(v, list):
                for it in v[:5]:
                    if isinstance(it, dict):
                        r = _deep_find(it, names, depth + 1)
                        if r not in (None, ""):
                            return r
        return ""

    _ORDNO_KEYS = {"ordernumber", "orderno", "ordernum"}
    _CUST_KEYS = {"customername", "recipientname", "receivername", "fullname"}
    _ODATE_KEYS = {"orderdate", "ordercreatedate", "ordercreatedat"}

    async def _upsert_groups(grouped, forced_status=None, allow_decrement=False):
        imported = updated = 0
        for g in grouped:
            if not isinstance(g, dict):
                continue  # OMS bazen düz string satırlar sızdırıyor ('str' has no attribute 'get')
            try:
                data = await _hb_enrich_items(map_hepsiburada_order(g))
                number = data.get("order_number")
                if not number or str(number).rstrip("?") in ("", "HB"):
                    continue  # sipariş no çözülemedi — çöp kayıt üretme
                # Koruma: içeriği boş (0 TL / adsız kalem) gelen satır, dolu mevcut kaydı ezmesin
                try:
                    if float(data.get("total") or 0) <= 0:
                        _ex = await db.orders.find_one(
                            {"order_number": number, "platform": "hepsiburada"},
                            {"_id": 0, "total": 1})
                        if _ex and float(_ex.get("total") or 0) > 0:
                            continue
                except Exception:
                    pass
                if forced_status:
                    data["status"] = forced_status
                existing = await db.orders.find_one({"order_number": number, "platform": "hepsiburada"})
                if existing:
                    upd = {k: v for k, v in data.items() if k != "status"}
                    if forced_status and existing.get("status") not in ("cancelled", "returned"):
                        upd["status"] = forced_status
                        if forced_status == "cancelled":
                            upd.setdefault("cancelled_at", datetime.now(timezone.utc).isoformat())
                            upd.setdefault("cancel_source", "hepsiburada_backfill")
                    await db.orders.update_one({"_id": existing["_id"]}, {"$set": upd})
                    updated += 1
                else:
                    data["id"] = generate_id()
                    data["created_at"] = _hb_created_at(data)
                    if forced_status == "cancelled":
                        data.setdefault("cancelled_at", datetime.now(timezone.utc).isoformat())
                        data.setdefault("cancel_source", "hepsiburada_backfill")
                    await db.orders.insert_one(data)
                    imported += 1
                    if allow_decrement and data.get("status") not in ("cancelled", "returned"):
                        await _decrement_stock_for_imported_order(data, "hepsiburada")
            except Exception as e:
                await log_integration_event("hepsiburada", "backfill", "order",
                                            str(g)[:40], "error", f"Backfill kalem hatası: {e}")
        return imported, updated

    tot_imp = tot_upd = 0
    # (1) Açık siparişler — tarihsiz liste, offset sayfalama
    try:
        offset = 0
        for _p in range(10):
            resp = await _aio.to_thread(client.get_orders, None, None, offset, 100)
            grouped = _hb_orders_from_response(resp)
            if not grouped:
                break
            i, u = await _upsert_groups(grouped, allow_decrement=decrement_stock)
            tot_imp += i; tot_upd += u
            if len(_hb_normalize_lines(resp)) < 100:
                break
            offset += 100
    except Exception as e:
        await log_integration_event("hepsiburada", "backfill", "open_orders", "", "error", f"Açık sipariş taraması: {e}")

    # (2) Paketler + (3) İptaller — 24 saatlik dilimler (bugünden geriye)
    now_ = datetime.now(timezone.utc)
    pkg_imp = pkg_upd = cn_imp = cn_upd = cargo_upd = 0
    detail_numbers: set = set()  # shipped/delivered künyelerinden toplanan sipariş noları

    def _flatten_pkg_lines(rows):
        """Paket objeleri kalemleri nested taşıyabilir ({items|lineItems:[...]}) —
        sipariş gruplayıcı düz kalem listesi beklediğinden nested kalemler paket
        üstbilgisiyle (orderNumber/müşteri/kargo) zenginleştirilerek düzleştirilir."""
        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            nested = None
            for k in ("items", "lineItems", "details", "orderItems"):
                v = r.get(k)
                if isinstance(v, list) and v:
                    nested = v
                    break
            if nested:
                for ln in nested:
                    if not isinstance(ln, dict):
                        continue
                    m = dict(ln)
                    for ck in ("orderNumber", "orderId", "orderDate", "customerName", "customer",
                               "shippingAddress", "deliveryAddress", "recipientName",
                               "packageNumber", "cargoCompany", "trackingNumber", "barcode"):
                        if m.get(ck) in (None, "") and r.get(ck) not in (None, ""):
                            m[ck] = r.get(ck)
                    # Alan adları paket şemasında farklı/nested olabilir — derin tarayıcı tamamlar
                    if m.get("orderNumber") in (None, ""):
                        v = _deep_find(ln, _ORDNO_KEYS) or _deep_find(r, _ORDNO_KEYS)
                        if v:
                            m["orderNumber"] = str(v)
                    if m.get("customerName") in (None, ""):
                        v = _deep_find(ln, _CUST_KEYS) or _deep_find(r, _CUST_KEYS)
                        if v:
                            m["customerName"] = str(v)
                    if m.get("orderDate") in (None, ""):
                        v = _deep_find(ln, _ODATE_KEYS) or _deep_find(r, _ODATE_KEYS)
                        if v:
                            m["orderDate"] = v
                    out.append(m)
            else:
                m = dict(r)
                if m.get("orderNumber") in (None, ""):
                    v = _deep_find(r, _ORDNO_KEYS)
                    if v:
                        m["orderNumber"] = str(v)
                if m.get("customerName") in (None, ""):
                    v = _deep_find(r, _CUST_KEYS)
                    if v:
                        m["customerName"] = str(v)
                out.append(m)
        return out

    # Paket kaynak adayları: güncel liste kargolananları DÜŞÜRDÜĞÜ için geçmiş siparişler
    # teslim/kargo uçlarından gelir. Uçlar hesaba göre değişebildiğinden bir kez problanır.
    pkg_sources = [("packages", client.get_packages)]
    _pb = (now_ - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    _pe = now_.strftime("%Y-%m-%d %H:%M")
    for label, fn in (
        ("shipped", client.get_packages_shipped),      # resmî uç: kargoya verilenler
        ("delivered", client.get_packages_delivered),  # resmî uç: teslim edilenler
    ):
        try:
            r = await _aio.to_thread(fn, 0, 5, _pb, _pe)
            if isinstance(r, dict) and str(r.get("success", "")).lower() == "false":
                raise RuntimeError(str(r.get("message") or "success=false"))
            _rows = _hb_normalize_lines(r) or []
            pkg_sources.append((label, fn))
            _sample = {}
            try:
                import json as _json
                _sample = {"sample_row": _json.loads(_json.dumps(_rows[0], default=str))} if _rows else {}
            except Exception:
                _sample = {}
            await log_integration_event("hepsiburada", "backfill", "probe", label, "success",
                                        f"Paket ucu kullanılabilir: {label} ({len(_rows)} örnek satır)",
                                        details=_sample)
        except Exception as pe_:
            await log_integration_event("hepsiburada", "backfill", "probe", label, "info",
                                        f"Paket ucu yok/erişilemedi: {label}: {str(pe_)[:140]}")

    for d in range(days):
        end = now_ - timedelta(days=d)
        begin = end - timedelta(days=1)
        b, e = begin.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
        for label, fn in pkg_sources:
            try:
                off = 0
                for _pg in range(4):  # dilim başına en çok 200 paket (HB limit tavanı 50)
                    resp = await _aio.to_thread(fn, off, 50, b, e)
                    rows_ = _hb_normalize_lines(resp) or []
                    if not rows_:
                        break
                    if label in ("shipped", "delivered"):
                        # Bu uçlar KÜNYE döner (OrderNumber/paket no/tarih — kalem-tutar yok).
                        # Stub'ı upsert etmek dolu kaydı ezer; numaraları toplayıp detay
                        # aşağıda ordernumber ucundan tam çekilir.
                        for _r in rows_:
                            if not isinstance(_r, dict):
                                continue
                            _ns = _r.get("OrderNumbers") if isinstance(_r.get("OrderNumbers"), list) else []
                            _n1 = _deep_find(_r, _ORDNO_KEYS)
                            _all_ns = [str(x or "").strip() for x in ([_n1] + _ns) if str(x or "").strip()]
                            for _n in _all_ns:
                                detail_numbers.add(_n)
                            # KARGO (geçmiş siparişlerde de göster): shipped/delivered paket satırı
                            # kargo takip barkodu (Barcode) + paket no taşır → eşleşen siparişlere yaz.
                            # (HB bu uçta kargo FİRMA adını vermez; takip barkodu = izlenebilir kargo.)
                            _cg_no = str(_hb_g(_r, "Barcode", "barcode", "cargoTrackingNumber", "trackingNumber") or "")
                            _cg_pkg = str(_hb_g(_r, "PackageNumber", "packageNumber") or "")
                            _cg_nm = str(_hb_g(_r, "CargoCompany", "cargoCompany", "cargoProviderName", "cargoCompanyName") or "")
                            if (_cg_no or _cg_pkg) and _all_ns:
                                _cset = {}
                                if _cg_no:
                                    _cset["cargo_tracking_number"] = _cg_no
                                if _cg_nm:
                                    _cset["cargo_provider_name"] = _cg_nm
                                if _cg_pkg:
                                    _cset["hb_package_number"] = _cg_pkg
                                _hbnums = list({*(_all_ns), *[(x if x.upper().startswith("HB") else f"HB{x}") for x in _all_ns]})
                                try:
                                    await db.orders.update_many(
                                        {"order_number": {"$in": _hbnums}, "platform": "hepsiburada"},
                                        {"$set": _cset})
                                    cargo_upd += 1
                                except Exception:
                                    pass
                    else:
                        lines = _flatten_pkg_lines(rows_)
                        i, u = await _upsert_groups(_hb_orders_from_response(lines))
                        pkg_imp += i; pkg_upd += u
                    if len(rows_) < 50:
                        break
                    off += 50
            except Exception as ex:
                await log_integration_event("hepsiburada", "backfill", "packages", f"{label} {b}", "error",
                                            f"Paket dilimi {label} {b}: {ex}")
        try:
            resp = await _aio.to_thread(client.get_cancelled_orders, 0, 50, b, e)
            i, u = await _upsert_groups(_hb_orders_from_response(resp), forced_status="cancelled")
            cn_imp += i; cn_upd += u
        except Exception as ex:
            await log_integration_event("hepsiburada", "backfill", "cancelled", b, "error", f"İptal dilimi {b}: {ex}")
        await _aio.sleep(0.3)  # OMS rate limit nezaketi

    # (3.5) Künyelerden toplanan numaraların TAM detayı (kalem/tutar/müşteri)
    if detail_numbers:
        for _on in sorted(detail_numbers):
            try:
                resp = await _aio.to_thread(client.get_order_by_number, _on)
                i, u = await _upsert_groups(_hb_orders_from_response(resp))
                pkg_imp += i; pkg_upd += u
            except Exception as ex:
                await log_integration_event("hepsiburada", "backfill", "order_detail", _on, "error",
                                            f"Sipariş detayı çekilemedi {_on}: {ex}")
            await _aio.sleep(0.15)

    # (4) İadeler
    claims = await _sync_hepsiburada_claims_core(days_back=days)

    await log_integration_event(
        "hepsiburada", "backfill", "job", "", "success",
        f"HB {days} gün backfill bitti — açık: +{tot_imp}/{tot_upd} · paket: +{pkg_imp}/{pkg_upd} · "
        f"iptal: +{cn_imp}/{cn_upd} · iade: {claims.get('synced', 0)} · kargo yazılan: {cargo_upd}"
        + (" · stok düşümü: AÇIK" if decrement_stock else " · stok düşümü: kapalı"))


@router.post("/hepsiburada/backfill")
async def hb_backfill(payload: Optional[dict] = Body(default=None),
                      current_user: dict = Depends(require_admin)):
    """Tek seferlik: son N günün HB siparişleri + iptalleri + iadelerini sisteme çeker.
    Arka planda çalışır; ilerleme/sonuç Entegrasyon Logları'na (hepsiburada/backfill) düşer.
    body: {"days": 30, "decrement_stock": false}
    NOT: Geçmiş siparişlerde stok düşümü VARSAYILAN KAPALI (çift düşüm riski —
    bu siparişler büyük olasılıkla fiilen işlendi)."""
    import asyncio as _aio
    p = payload or {}
    days = int(p.get("days") or 30)
    dec = bool(p.get("decrement_stock") or False)
    # Çıplak create_task GC'ye gidebilir (bilinen weakref davranışı) — güçlü referansla tut
    global _HB_BACKFILL_TASKS
    try:
        _HB_BACKFILL_TASKS
    except NameError:
        _HB_BACKFILL_TASKS = set()
    _t = _aio.create_task(_hb_backfill_run(days, dec))
    _HB_BACKFILL_TASKS.add(_t)
    _t.add_done_callback(_HB_BACKFILL_TASKS.discard)
    await log_integration_event("hepsiburada", "backfill", "job", "", "queued",
                                f"HB {days} gün backfill başlatıldı (stok düşümü: {'açık' if dec else 'kapalı'})")
    return {"success": True, "started": True, "days": days, "decrement_stock": dec,
            "message": f"Son {days} günün HB siparişleri/iptalleri/iadeleri arka planda çekiliyor — sonuç Entegrasyon Logları'nda."}
