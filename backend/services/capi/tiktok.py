"""
TikTok Events API adapter.
Doc: https://business-api.tiktok.com/portal/docs?id=1771101303285761

Endpoint:
  POST https://business-api.tiktok.com/open_api/v1.3/event/track/

Header:
  Access-Token: <ACCESS_TOKEN>
"""
import httpx
from typing import Optional
from datetime import datetime, timezone


TIKTOK_BASE = "https://business-api.tiktok.com/open_api/v1.3/event/track/"

EVENT_MAP = {
    "view_item":        "ViewContent",
    "view_item_list":   "ViewContent",
    "add_to_cart":      "AddToCart",
    "remove_from_cart": "RemoveFromCart",
    "begin_checkout":   "InitiateCheckout",
    "add_payment_info": "AddPaymentInfo",
    "add_to_wishlist":  "AddToWishlist",
    "purchase":         "CompletePayment",
    "refund":           "PlaceAnOrder",   # closest fallback
    "lead":             "SubmitForm",
    "search":           "Search",
}


# Feature flag adı → gate'lenen TikTok user alanı (rollback). Flag AÇIKÇA False ise gönderilmez.
# None/eksik → mevcut davranış (gönderilir).
_FLAG_TO_TT_FIELD = {
    "email": "email", "phone": "phone_number", "external_id": "external_id",
    "ttclid": "ttclid", "ttp": "ttp",
}


def _tt_flag_off(field_flags: Optional[dict], tt_field: str) -> bool:
    if not field_flags:
        return False
    for flag, fld in _FLAG_TO_TT_FIELD.items():
        if fld == tt_field and field_flags.get(flag) is False:
            return True
    return False


def _build_user(ud: dict, field_flags: Optional[dict] = None) -> dict:
    """TikTok Events API v1.3 'user' fields (advanced matching).
    Doc: https://business-api.tiktok.com/portal/docs?id=1771101303285761
    field_flags: yalnız AÇIKÇA False olan alanlar gönderilmez (rollback). None → mevcut davranış.
    """
    out = {}
    # Hashed identifiers
    for k_in, k_out in [
        ("em", "email"), ("ph", "phone_number"),
        ("external_id", "external_id"),
        ("fn", "first_name"), ("ln", "last_name"),
        ("ct", "city"), ("st", "state"), ("zp", "zip_code"), ("country", "country"),
        ("madid", "mobile_advertising_id"),
        ("idfa", "idfa"), ("idfv", "idfv"),
        ("ge", "gender"), ("db", "date_of_birth"),
    ]:
        if _tt_flag_off(field_flags, k_out):
            continue
        v = ud.get(k_in)
        if v: out[k_out] = v
    # Raw context
    if ud.get("ttclid") and not _tt_flag_off(field_flags, "ttclid"):  out["ttclid"] = ud["ttclid"]
    if ud.get("ttp") and not _tt_flag_off(field_flags, "ttp"):        out["ttp"] = ud["ttp"]
    if ud.get("client_ip_address"): out["ip"] = ud["client_ip_address"]
    if ud.get("client_user_agent"): out["user_agent"] = ud["client_user_agent"]
    if ud.get("locale"):       out["locale"] = ud["locale"]
    return out


def _build_properties(event: dict) -> dict:
    """TikTok properties (custom_data) — tüm desteklenen alanlar."""
    contents = []
    for it in (event.get("items") or []):
        c = {
            "content_id": str(it.get("item_id") or it.get("id") or ""),
            "content_name": it.get("item_name") or it.get("name") or "",
            "content_category": it.get("item_category") or "",
            "content_type": "product",
            "brand": it.get("item_brand") or "",
            "quantity": int(it.get("quantity") or 1),
            "price": float(it.get("price") or 0),
        }
        if it.get("sku"):
            c["sku"] = it["sku"]
        contents.append(c)
    props = {
        "currency": event.get("currency") or "TRY",
        "value": float(event.get("value") or 0.0),
        "contents": contents,
        "content_type": "product",
        "order_id": str(event.get("order_id") or ""),
    }
    if event.get("category"):
        props["content_category"] = event["category"]
    if event.get("coupon"):
        props["coupon_code"] = event["coupon"]
    if event.get("description"):
        props["description"] = event["description"]
    if event.get("search_string") or event.get("query"):
        props["query"] = event.get("search_string") or event.get("query")
    # Tax / shipping / discount (TikTok'da resmi std olmadığı için custom field olarak gönderilir)
    if (event.get("discount") or 0) > 0: props["discount"] = float(event["discount"])
    if (event.get("shipping") or 0) > 0: props["shipping"] = float(event["shipping"])
    if (event.get("tax") or 0) > 0:      props["tax"] = float(event["tax"])
    if event.get("payment_type"):        props["payment_method"] = event["payment_type"]
    return props


async def send(
    *,
    pixel_id: str,
    access_token: str,
    event_name: str,
    event_id: str,
    event_time: Optional[int],
    user_data: dict,
    event_payload: dict,
    event_source_url: Optional[str] = None,
    test_event_code: Optional[str] = None,
    timeout: float = 8.0,
    field_flags: Optional[dict] = None,
) -> dict:
    tt_event = EVENT_MAP.get(event_name, event_name)
    if not event_time:
        event_time = int(datetime.now(timezone.utc).timestamp())

    payload = {
        "event_source": "web",
        "event_source_id": pixel_id,
        "data": [{
            "event": tt_event,
            "event_time": int(event_time),
            "event_id": event_id,
            "user": _build_user(user_data, field_flags),
            "properties": _build_properties(event_payload),
            "page": {"url": event_source_url or "https://www.facette.com.tr"},
        }],
    }
    if test_event_code:
        payload["test_event_code"] = test_event_code

    headers = {"Access-Token": access_token, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(TIKTOK_BASE, json=payload, headers=headers)
            try:
                resp_json = r.json()
            except Exception:
                resp_json = {"text": r.text[:500]}
            ok = (r.status_code == 200 and (resp_json or {}).get("code") == 0)
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
        user_data={"client_ip_address": "8.8.8.8",
                   "client_user_agent": "Mozilla/5.0 FacetteTest"},
        event_payload={"value": 0, "currency": "TRY"},
        test_event_code=test_event_code,
    )
