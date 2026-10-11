"""Pure, side-effect-free marketplace order mapping helpers."""


def canonical_hb_order_number(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return f"HB{raw[2:]}" if raw.upper().startswith("HB") else f"HB{raw}"


def hb_internal_status(raw_status) -> tuple[str, str]:
    key = str(raw_status or "").replace(" ", "").replace("_", "").replace("-", "").lower()
    cancelled = {
        "cancelled", "canceled", "cancel", "unfulfilled", "unsupplied",
        "cancelledbycustomer", "canceledbycustomer",
        "cancelledbymerchant", "canceledbymerchant",
    }
    returned = {"returned", "returncompleted", "refunded", "refund"}
    shipped = {"shipped", "intransit", "handedtocargo"}
    delivered = {"delivered", "completed"}
    # HB OMS açık sipariş uçları özellikle Open/Unpacked/Packed döndürür.
    # Bunlar ödeme alınmış, operasyonel olarak aktif pazaryeri siparişleridir.
    confirmed = {
        "open", "unpacked", "packed", "approved", "confirmed", "processing",
        "picking", "packaged", "invoiced", "readytoship",
    }
    unpaid = {"pending", "created", "unpaid", "awaitingpayment", "paymentpending"}
    if key in cancelled:
        return "cancelled", "paid"
    if key in returned:
        return ("refunded" if key in {"refunded", "refund"} else "returned"), "paid"
    if key in shipped:
        return "shipped", "paid"
    if key in delivered:
        return "delivered", "paid"
    if key in confirmed:
        return "confirmed", "paid"
    if key in unpaid:
        return "pending", "pending"
    # Unknown states must not silently become paid revenue.
    return "pending", "pending"


def amazon_internal_status(raw_status) -> tuple[str, str]:
    key = str(raw_status or "").strip()
    if key in {"Canceled", "Cancelled", "Unfulfillable"}:
        return "cancelled", "paid"
    if key in {"Shipped", "PartiallyShipped"}:
        return "shipped", "paid"
    if key == "Unshipped":
        return "confirmed", "paid"
    if key in {"Pending", "PendingAvailability", "InvoiceUnconfirmed"}:
        return "pending", "pending"
    return "pending", "pending"


def temu_order_fields(data: dict) -> dict:
    """Map only values actually present in a Temu webhook payload."""
    data = data or {}
    status_key = str(data.get("status") or "").lower().strip()
    status_map = {
        "pending": "pending", "created": "pending", "unpaid": "awaiting_payment",
        "paid": "confirmed", "confirmed": "confirmed", "processing": "confirmed",
        "shipped": "shipped", "in_transit": "shipped", "delivered": "delivered",
        "completed": "delivered", "cancelled": "cancelled", "canceled": "cancelled",
        "refunded": "refunded", "returned": "returned",
    }
    out = {"status": status_map.get(status_key, "pending"),
           "marketplace_status_raw": status_key}
    raw_items = data.get("items") or data.get("lines")
    items = None
    if isinstance(raw_items, list):
        items = []
        for raw in raw_items:
            raw = raw or {}
            try:
                qty = max(1, int(raw.get("quantity") or raw.get("qty") or 1))
            except (TypeError, ValueError):
                qty = 1
            price = raw.get("price")
            if price is None:
                price = raw.get("unit_price") or raw.get("unitPrice") or 0
            try:
                price = float(price)
            except (TypeError, ValueError):
                price = 0.0
            items.append({
                "product_id": raw.get("product_id") or raw.get("productId") or raw.get("sku"),
                "product_name": raw.get("product_name") or raw.get("productName") or raw.get("name") or "",
                "name": raw.get("name") or raw.get("product_name") or raw.get("productName") or "",
                "barcode": raw.get("barcode") or raw.get("sku") or "",
                "quantity": qty, "price": price, "unit_price": price,
                "size": raw.get("size") or "", "color": raw.get("color") or "",
            })
    total = data.get("total")
    if total is None:
        total = data.get("total_amount")
    order_date = data.get("order_date") or data.get("created_at") or data.get("create_time")
    if items is not None:
        out["items"] = items
    if total not in (None, ""):
        try:
            out["total"] = float(total)
        except (TypeError, ValueError):
            pass
    if order_date:
        out["marketplace_order_date"] = order_date
    if out["status"] not in {"pending", "awaiting_payment"}:
        out["payment_status"] = "paid"
        out["payment_method"] = "marketplace"
    out["integration_incomplete"] = not (
        isinstance(out.get("items"), list) and "total" in out and bool(out.get("marketplace_order_date"))
    )
    return out
