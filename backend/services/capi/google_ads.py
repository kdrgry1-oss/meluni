"""
Google Ads Enhanced Conversions adapter (Measurement Protocol for GA4 + Ads).

Two endpoints supported:
  1. GA4 Measurement Protocol (server-side events to GA4 property)
     POST https://www.google-analytics.com/mp/collect?measurement_id={MID}&api_secret={SECRET}
     → Used for GA4 visibility (free).

  2. Google Ads Enhanced Conversions are typically done via the Google Ads API
     (uploadClickConversions / uploadConversionAdjustments). That requires
     OAuth2 + developer token. For MVP we send via GA4 MP which Ads consumes
     via auto-import when the Ads–GA4 link is configured.

Pixel ID format expected: "G-XXXX|api_secret"  (or stored in advanced JSON).
"""
import httpx
from typing import Optional
from datetime import datetime, timezone


GA4_BASE = "https://www.google-analytics.com/mp/collect"

EVENT_MAP = {
    "view_item":        "view_item",
    "view_item_list":   "view_item_list",
    "add_to_cart":      "add_to_cart",
    "remove_from_cart": "remove_from_cart",
    "begin_checkout":   "begin_checkout",
    "add_payment_info": "add_payment_info",
    "add_to_wishlist":  "add_to_wishlist",
    "purchase":         "purchase",
    "refund":           "refund",
    "lead":             "generate_lead",
    "search":           "search",
}


def _items_to_ga4(items: list) -> list:
    """GA4 spec'e göre item parametrelerini hazırla."""
    out = []
    for it in (items or []):
        item = {
            "item_id": str(it.get("item_id") or it.get("id") or ""),
            "item_name": it.get("item_name") or it.get("name") or "",
            "item_category": it.get("item_category") or "",
            "item_brand": it.get("item_brand") or "",
            "item_variant": it.get("item_variant") or "",
            "price": float(it.get("price") or 0),
            "quantity": int(it.get("quantity") or 1),
            "currency": it.get("currency") or "TRY",
            "discount": float(it.get("discount") or 0),
            "affiliation": it.get("affiliation") or "FACETTE Online",
        }
        for k in ("item_category2", "item_category3", "item_category4", "item_category5"):
            if it.get(k):
                item[k] = it[k]
        if it.get("item_list_id"):
            item["item_list_id"] = it["item_list_id"]
        if it.get("item_list_name"):
            item["item_list_name"] = it["item_list_name"]
        if it.get("index") is not None:
            item["index"] = it["index"]
        if it.get("coupon"):
            item["coupon"] = it["coupon"]
        if it.get("promotion_id"):
            item["promotion_id"] = it["promotion_id"]
        if it.get("promotion_name"):
            item["promotion_name"] = it["promotion_name"]
        out.append(item)
    return out


async def send(
    *,
    pixel_id: str,                # "G-XXXX|api_secret" or just "G-XXXX"
    access_token: str,            # api_secret (GA4 MP) — kept here for compat
    event_name: str,
    event_id: str,
    event_time: Optional[int],
    user_data: dict,
    event_payload: dict,
    event_source_url: Optional[str] = None,
    test_event_code: Optional[str] = None,
    timeout: float = 8.0,
) -> dict:
    # Parse measurement_id + api_secret
    if "|" in pixel_id:
        mid, embedded_secret = pixel_id.split("|", 1)
        api_secret = embedded_secret.strip() or access_token
    else:
        mid = pixel_id
        api_secret = access_token

    if not api_secret:
        return {"ok": False, "status": 0, "response": None,
                "error": "Google Ads/GA4 için api_secret eksik."}

    ga_event = EVENT_MAP.get(event_name, event_name)

    # Build the payload — GA4 e-commerce schema (Enhanced)
    params_body = {
        "currency": event_payload.get("currency") or "TRY",
        "value": float(event_payload.get("value") or 0.0),
        "transaction_id": str(event_payload.get("order_id") or event_id),
        "coupon": event_payload.get("coupon") or "",
        "items": _items_to_ga4(event_payload.get("items") or []),
        "engagement_time_msec": 100,
        "session_id": user_data.get("external_id") or event_id,
        # Enhanced parametreleri
        "discount": float(event_payload.get("discount") or 0.0),
        "tax": float(event_payload.get("tax") or 0.0),
        "shipping": float(event_payload.get("shipping") or 0.0),
        "payment_type": event_payload.get("payment_type") or "",
        "shipping_tier": event_payload.get("shipping_tier") or "",
        "affiliation": event_payload.get("affiliation") or "FACETTE Online",
        "item_list_id": event_payload.get("list_id") or "",
        "item_list_name": event_payload.get("list_name") or "",
    }
    # Promosyon (varsa)
    if event_payload.get("promotion_id"):
        params_body["promotion_id"] = event_payload["promotion_id"]
    if event_payload.get("promotion_name"):
        params_body["promotion_name"] = event_payload["promotion_name"]

    payload = {
        "client_id": user_data.get("external_id") or user_data.get("em") or event_id,
        "events": [{"name": ga_event, "params": params_body}],
    }
    # Optional user_id (CRM identifier - cross-device tracking)
    if user_data.get("external_id"):
        payload["user_id"] = user_data["external_id"]
    # Y24: Enhanced Conversions doğru yapı: PII `user_data` altında olmalı (user_properties DEĞİL);
    # e-posta/telefon/ad/soyad/sokak SHA-256 HASH'Lİ, şehir/bölge/posta/ülke ise HASH'SİZ (ham).
    # Önceki kod hepsini user_properties'e koyup adres bileşenlerini hash'li gönderdiği için
    # Google eşleştirme oranı sıfırdı.
    ud = {}
    if user_data.get("em"): ud["sha256_email_address"] = user_data["em"]
    if user_data.get("ph"): ud["sha256_phone_number"] = user_data["ph"]
    address = {}
    if user_data.get("fn"): address["sha256_first_name"] = user_data["fn"]
    if user_data.get("ln"): address["sha256_last_name"] = user_data["ln"]
    if user_data.get("street"): address["sha256_street"] = user_data["street"]
    if user_data.get("ct_raw"): address["city"] = user_data["ct_raw"]
    if user_data.get("st_raw"): address["region"] = user_data["st_raw"]
    if user_data.get("zp_raw"): address["postal_code"] = user_data["zp_raw"]
    if user_data.get("country_raw"): address["country"] = user_data["country_raw"]
    if address:
        ud["address"] = [address]
    if ud:
        payload["user_data"] = ud
    # Google Ads click ID propagation (GA4 ↔ Ads attribution)
    if user_data.get("gclid") or user_data.get("wbraid") or user_data.get("gbraid"):
        params_body["gclid"] = user_data.get("gclid") or ""
        if user_data.get("wbraid"): params_body["wbraid"] = user_data["wbraid"]
        if user_data.get("gbraid"): params_body["gbraid"] = user_data["gbraid"]

    params = {"measurement_id": mid, "api_secret": api_secret}
    if test_event_code:
        params["debug_mode"] = "true"

    # O11: Doğru validation ucu `/debug/mp/collect`'tir (`/mp/collect/debug` DEĞİL).
    url = ("https://www.google-analytics.com/debug/mp/collect" if test_event_code else GA4_BASE)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, params=params, json=payload)
            # MP returns 204 No Content on success
            ok = r.status_code in (200, 204)
            try:
                resp_json = r.json() if r.text else {"status": "no_content"}
            except Exception:
                resp_json = {"text": r.text[:300]}
            return {"ok": ok, "status": r.status_code,
                    "response": resp_json,
                    "error": None if ok else resp_json}
    except Exception as e:
        return {"ok": False, "status": 0, "response": None, "error": str(e)}


async def test_connection(pixel_id: str, access_token: str,
                          test_event_code: Optional[str] = "1") -> dict:
    return await send(
        pixel_id=pixel_id, access_token=access_token,
        event_name="lead", event_id=f"test-{int(datetime.now().timestamp())}",
        event_time=None,
        user_data={"em": "test@example.com"},
        event_payload={"value": 0, "currency": "TRY"},
        test_event_code=test_event_code,
    )
