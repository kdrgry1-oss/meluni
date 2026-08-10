"""
Pinterest Conversions API adapter.
Doc: https://developers.pinterest.com/docs/conversions/conversions/

Endpoint:
  POST https://api.pinterest.com/v5/ad_accounts/{ad_account_id}/events
Header:
  Authorization: Bearer {access_token}

NOTE: Pinterest requires ad_account_id (not the pixel/tag id). We store both
in the marketing_pixels doc:
  - tag_id = ad_account_id
  - advanced.pixel_tag = "TAG-ID"  (for browser pixel)
"""
import httpx
from typing import Optional
from datetime import datetime, timezone


PINTEREST_BASE = "https://api.pinterest.com/v5"

EVENT_MAP = {
    "view_item":        "page_visit",
    "view_item_list":   "page_visit",
    "add_to_cart":      "add_to_cart",
    "remove_from_cart": "custom",
    "begin_checkout":   "checkout",
    "add_payment_info": "checkout",
    "add_to_wishlist":  "custom",
    "purchase":         "checkout",
    "refund":           "custom",
    "lead":             "lead",
    "search":           "search",
}


def _build_user(ud: dict) -> dict:
    """Pinterest Conversions API v5 — TÜM hashed fields list olarak.
    Doc: https://developers.pinterest.com/docs/conversions/conversions/
    """
    out = {}
    # Hashed arrays
    for k in ("em", "ph", "fn", "ln", "ct", "st", "zp", "country",
              "external_id", "ge", "db", "hashed_maids"):
        v = ud.get(k)
        if v:
            out[k] = [v] if not isinstance(v, list) else v
    # Madid mapped to hashed_maids
    if ud.get("madid"):
        out.setdefault("hashed_maids", []).append(ud["madid"])
    # Raw context
    if ud.get("client_ip_address"): out["client_ip_address"] = ud["client_ip_address"]
    if ud.get("client_user_agent"): out["client_user_agent"] = ud["client_user_agent"]
    if ud.get("epik"):       out["click_id"] = ud["epik"]
    return out


def _build_custom_data(event: dict) -> dict:
    contents = []
    for it in (event.get("items") or []):
        c = {
            "id": str(it.get("item_id") or it.get("id") or ""),
            "item_name": it.get("item_name") or "",
            "item_brand": it.get("item_brand") or "",
            "item_category": it.get("item_category") or "",
            "item_price": str(float(it.get("price") or 0)),
            "quantity": int(it.get("quantity") or 1),
        }
        if it.get("sku"): c["sku"] = it["sku"]
        contents.append(c)
    cd = {
        "currency": event.get("currency") or "TRY",
        "value": str(float(event.get("value") or 0.0)),
        "content_ids": [c["id"] for c in contents],
        "contents": contents,
        "num_items": sum(c["quantity"] for c in contents),
        "order_id": str(event.get("order_id") or ""),
        "content_category": event.get("category") or "",
        "content_name": event.get("content_name") or "",
        "content_brand": event.get("content_brand") or "FACETTE",
    }
    if event.get("search_string"):
        cd["search_string"] = event["search_string"]
    return cd


async def send(
    *,
    pixel_id: str,                # ad_account_id
    access_token: str,
    event_name: str,
    event_id: str,
    event_time: Optional[int],
    user_data: dict,
    event_payload: dict,
    event_source_url: Optional[str] = None,
    test_event_code: Optional[str] = None,
    timeout: float = 8.0,
) -> dict:
    pin_event = EVENT_MAP.get(event_name, "custom")
    if not event_time:
        event_time = int(datetime.now(timezone.utc).timestamp())

    payload = {
        "data": [{
            "event_name": pin_event,
            "action_source": "web",
            "event_time": int(event_time),
            "event_id": event_id,
            "event_source_url": event_source_url or "https://www.facette.com.tr",
            "user_data": _build_user(user_data),
            "custom_data": _build_custom_data(event_payload),
        }],
    }
    if test_event_code:
        payload["test"] = True

    url = f"{PINTEREST_BASE}/ad_accounts/{pixel_id}/events"
    headers = {"Authorization": f"Bearer {access_token}",
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload, headers=headers)
            try:
                resp_json = r.json()
            except Exception:
                resp_json = {"text": r.text[:300]}
            ok = 200 <= r.status_code < 300
            return {"ok": ok, "status": r.status_code,
                    "response": resp_json,
                    "error": None if ok else resp_json}
    except Exception as e:
        return {"ok": False, "status": 0, "response": None, "error": str(e)}


async def test_connection(pixel_id: str, access_token: str,
                          test_event_code: Optional[str] = None) -> dict:
    return await send(
        pixel_id=pixel_id, access_token=access_token,
        event_name="lead", event_id=f"test-{int(datetime.now().timestamp())}",
        event_time=None,
        user_data={"em": "abc123",
                   "client_ip_address": "8.8.8.8",
                   "client_user_agent": "Mozilla/5.0 FacetteTest"},
        event_payload={"value": 0, "currency": "TRY"},
        test_event_code="1",
    )
