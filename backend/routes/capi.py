"""
CAPI Public Event Endpoint.
React frontend bu endpoint'e (tüm) tracking event'lerini POST eder.
Backend, event'i tüm aktif CAPI provider'larına paralel olarak fan-out eder.

Dedup: Frontend `event_id` (uuid) üretir; aynı id ile browser pixel'i ile
server-side gönderim aynı event olarak sayılır (Meta/TikTok/Pinterest native
support).
"""
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel, Field

from .deps import db
from services.capi.orchestrator import dispatch_event
from services.capi.hash_utils import build_user_data


router = APIRouter(prefix="/capi", tags=["capi"])


class CapiItem(BaseModel):
    item_id: Optional[str] = None
    item_name: Optional[str] = None
    item_brand: Optional[str] = None
    item_category: Optional[str] = None
    item_category2: Optional[str] = None
    item_category3: Optional[str] = None
    item_category4: Optional[str] = None
    item_category5: Optional[str] = None
    item_variant: Optional[str] = None
    item_list_id: Optional[str] = None
    item_list_name: Optional[str] = None
    index: Optional[int] = None
    affiliation: Optional[str] = None
    # Fiyat
    price: Optional[float] = 0.0          # ödenen (sale) unit price
    list_price: Optional[float] = 0.0     # liste fiyatı (indirimsiz)
    sale_price: Optional[float] = 0.0
    discount: Optional[float] = 0.0       # kalem bazlı pozitif indirim
    currency: Optional[str] = "TRY"
    quantity: Optional[int] = 1
    # Varyant
    sku: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    barcode: Optional[str] = None
    # Promosyon
    coupon: Optional[str] = None
    promotion_id: Optional[str] = None
    promotion_name: Optional[str] = None


class CapiEventReq(BaseModel):
    event_name: str = Field(..., description="view_item|view_item_list|add_to_cart|remove_from_cart|begin_checkout|add_payment_info|purchase|refund|lead|search")
    event_id: Optional[str] = None
    event_time: Optional[int] = None
    # User PII (raw — backend hashes before sending)
    email: Optional[str] = None
    phone: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = "TR"
    zipcode: Optional[str] = None
    street: Optional[str] = None
    date_of_birth: Optional[str] = None             # YYYY-MM-DD
    gender: Optional[str] = None                    # m/f/erkek/kadın
    external_id: Optional[str] = None
    subscription_id: Optional[str] = None
    fb_login_id: Optional[str] = None
    lead_id: Optional[str] = None
    madid: Optional[str] = None
    idfa: Optional[str] = None
    idfv: Optional[str] = None
    locale: Optional[str] = None
    # Click IDs (cookies)
    fbp: Optional[str] = None
    fbc: Optional[str] = None
    gclid: Optional[str] = None
    wbraid: Optional[str] = None
    gbraid: Optional[str] = None
    ttclid: Optional[str] = None
    ttp: Optional[str] = None
    epik: Optional[str] = None
    sc_click_id: Optional[str] = None
    sc_cookie1: Optional[str] = None
    # Event payload (GA4 e-commerce Enhanced schema)
    currency: Optional[str] = "TRY"
    value: Optional[float] = 0.0
    items: Optional[List[CapiItem]] = None
    order_id: Optional[str] = None
    coupon: Optional[str] = None
    category: Optional[str] = None
    # Promosyon & İndirim
    discount: Optional[float] = 0.0
    original_value: Optional[float] = 0.0
    tax: Optional[float] = 0.0
    shipping: Optional[float] = 0.0
    # Ödeme & Kargo
    payment_type: Optional[str] = None
    shipping_tier: Optional[str] = None
    # Liste / atıf
    affiliation: Optional[str] = None
    list_id: Optional[str] = None
    list_name: Optional[str] = None
    promotion_id: Optional[str] = None
    promotion_name: Optional[str] = None
    # Context
    event_source_url: Optional[str] = None
    tenant_id: Optional[str] = None
    providers: Optional[List[str]] = None


@router.post("/event")
async def capi_event(req: CapiEventReq, request: Request,
                     background_tasks: BackgroundTasks):
    """Track event from React. Returns immediately; dispatch runs in background.

    For checkout/purchase flows we want the response to be NON-BLOCKING so the
    user UX is unaffected. We schedule the dispatch via BackgroundTasks.
    """
    # Gerçek son-kullanıcı IP'si — cf-connecting-ip (spoof-korumalı, edge doğrulanırsa) → XFF → peer.
    # Ham XFF[0] yerine ortak güvenli helper (deps.client_ip_from_request) kullanılır.
    try:
        from .deps import client_ip_from_request
        client_ip = client_ip_from_request(request) or (request.client.host if request.client else None)
    except Exception:
        client_ip = (request.client.host if request.client else None)
        fwd = request.headers.get("x-forwarded-for") or ""
        if fwd:
            client_ip = fwd.split(",")[0].strip()
    user_agent = request.headers.get("user-agent")

    user_data = build_user_data(
        email=req.email, phone=req.phone,
        first_name=req.first_name, last_name=req.last_name,
        city=req.city, state=req.state, country=req.country,
        zipcode=req.zipcode, street=req.street,
        date_of_birth=req.date_of_birth, gender=req.gender,
        external_id=req.external_id,
        subscription_id=req.subscription_id, fb_login_id=req.fb_login_id,
        lead_id=req.lead_id,
        madid=req.madid, idfa=req.idfa, idfv=req.idfv,
        client_ip=client_ip, user_agent=user_agent,
        locale=req.locale,
        fbp=req.fbp, fbc=req.fbc,
        gclid=req.gclid, wbraid=req.wbraid, gbraid=req.gbraid,
        ttclid=req.ttclid, ttp=req.ttp,
        epik=req.epik,
        sc_click_id=req.sc_click_id, sc_cookie1=req.sc_cookie1,
    )

    event_payload = {
        "currency": req.currency,
        "value": req.value,
        "items": [i.dict() for i in (req.items or [])],
        "order_id": req.order_id,
        "coupon": req.coupon,
        "category": req.category,
        # Enhanced e-commerce ek alanları
        "discount": req.discount,
        "original_value": req.original_value,
        "tax": req.tax,
        "shipping": req.shipping,
        "payment_type": req.payment_type,
        "shipping_tier": req.shipping_tier,
        "affiliation": req.affiliation,
        "list_id": req.list_id,
        "list_name": req.list_name,
        "promotion_id": req.promotion_id,
        "promotion_name": req.promotion_name,
    }

    event_id = req.event_id  # may be None — orchestrator will gen

    async def _run():
        await dispatch_event(
            db,
            event_name=req.event_name,
            event_id=event_id,
            event_time=req.event_time,
            user_data=user_data,
            event_payload=event_payload,
            event_source_url=req.event_source_url,
            tenant_id=req.tenant_id,
            providers=req.providers,
        )

    background_tasks.add_task(_run)
    # Return acknowledgment immediately
    return {
        "ok": True,
        "event_id": event_id,
        "queued": True,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health")
async def capi_health():
    """Public health check — list providers/pixel count without leaking tokens."""
    active = await db.marketing_pixels.count_documents(
        {"is_active": True, "capi_enabled": True})
    return {"ok": True, "capi_active_pixels": active}
