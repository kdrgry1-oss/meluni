"""Shipping eligibility is based on merchandise after discounts, never charges.

Gift cards/store credit are payment instruments, not merchandise discounts.
The threshold is supplied by settings/campaigns; no tenant amount is hardcoded.
"""
from decimal import Decimal, ROUND_HALF_UP


def shipping_quote(subtotal, discounts, threshold, fee, free_shipping_promotion=False):
    money = lambda value: Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    basis = max(Decimal(0), money(subtotal) - sum((money(d) for d in discounts), Decimal(0)))
    # A promotion flag must not bypass a configured minimum after payment/points discounts.
    free = basis >= money(threshold) if threshold is not None else bool(free_shipping_promotion)
    return {"basis": float(basis), "free": free, "cost": 0.0 if free else float(money(fee))}


def campaign_live(c, now=None):
    """Is a campaign within its start/end window right now? Same rule as the
    coupon evaluator (routes/coupons.py): a date-only end_at is valid until the
    end of that day. The free-shipping threshold resolvers must use this — an
    `is_active` campaign past its end_at must NOT keep lowering the threshold."""
    from datetime import datetime, timezone
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    start = c.get("start_at")
    if start and str(start) > now_iso:
        return False
    end = c.get("end_at")
    if end:
        end = str(end)
        if len(end) == 10 and "T" not in end:
            end = f"{end}T23:59:59+00:00"
        if end < now_iso:
            return False
    return True
