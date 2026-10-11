"""Canonical, append-only stock audit records.

Historical ``stock_movements`` documents have several shapes. New admin stock
mutations use schema v2 so product detail can show the actor and exact per-SKU
before/after values without reconstructing or inventing historical balances.
"""
from datetime import datetime, timezone
import hashlib
import logging
from typing import Optional


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _variant_key(variant: dict) -> tuple:
    for field in ("id", "urun_id", "barcode", "stock_code", "sku"):
        value = str((variant or {}).get(field) or "").strip()
        if value:
            return field, value
    return "size_color", f"{(variant or {}).get('size', '')}|{(variant or {}).get('color', '')}"


def stock_changes(product_id: str, before: dict, after: dict) -> list:
    """Return exact changed SKU rows. Unknown historical values are never inferred."""
    old_variants = before.get("variants") or []
    new_variants = after.get("variants") or []
    if old_variants or new_variants:
        old_map = {_variant_key(v): v for v in old_variants}
        new_map = {_variant_key(v): v for v in new_variants}
        rows = []
        for key in sorted(set(old_map) | set(new_map), key=str):
            old_v, new_v = old_map.get(key), new_map.get(key)
            old_stock = _integer(old_v.get("stock"), 0) if old_v is not None else 0
            new_stock = _integer(new_v.get("stock"), 0) if new_v is not None else 0
            if old_stock == new_stock:
                continue
            sample = new_v or old_v or {}
            rows.append({
                "product_id": product_id,
                "variant_id": str(sample.get("id") or sample.get("urun_id") or ""),
                "sku": str(sample.get("stock_code") or sample.get("sku") or sample.get("barcode") or ""),
                "barcode": str(sample.get("barcode") or ""),
                "size": str(sample.get("size") or ""),
                "color": str(sample.get("color") or ""),
                "old": old_stock,
                "new": new_stock,
                "delta": new_stock - old_stock,
            })
        return rows

    old_stock = _integer(before.get("stock"), 0)
    new_stock = _integer(after.get("stock"), 0)
    if old_stock == new_stock:
        return []
    return [{
        "product_id": product_id,
        "variant_id": "",
        "sku": str(after.get("stock_code") or after.get("sku") or after.get("barcode") or before.get("stock_code") or ""),
        "barcode": str(after.get("barcode") or before.get("barcode") or ""),
        "size": "",
        "color": str(after.get("color") or before.get("color") or ""),
        "old": old_stock,
        "new": new_stock,
        "delta": new_stock - old_stock,
    }]


def actor_context(current_user: Optional[dict], request=None) -> tuple[dict, dict]:
    user = current_user or {}
    actor = {
        "id": str(user.get("id") or user.get("user_id") or ""),
        "email": str(user.get("email") or user.get("username") or ""),
        "role": str(user.get("role") or ("admin" if user.get("is_admin") else "")),
        "login_method": str(user.get("login_method") or user.get("auth_provider") or user.get("provider") or ""),
    }
    authorization = ""
    user_agent = ""
    ip = ""
    if request is not None:
        authorization = str(request.headers.get("authorization") or "")
        user_agent = str(request.headers.get("user-agent") or "")[:300]
        forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = forwarded or str(getattr(getattr(request, "client", None), "host", "") or "")
    context = {
        # Never persist the bearer token itself. Twelve hex chars are enough to
        # correlate actions in one login session without making it reusable.
        "session_hash": hashlib.sha256(authorization.encode()).hexdigest()[:12] if authorization else "",
        "ip": ip[:64],
        "user_agent": user_agent,
    }
    return actor, context


async def record_stock_audit(db, *, product_id: str, product_name: str, before: dict,
                             after: dict, source: str, current_user: Optional[dict] = None,
                             request=None, action: str = "stock_adjusted",
                             sync_result: Optional[dict] = None,
                             metadata: Optional[dict] = None) -> int:
    items = stock_changes(product_id, before or {}, after or {})
    if not items:
        return 0
    actor, context = actor_context(current_user, request)
    now = datetime.now(timezone.utc).isoformat()
    digest_seed = f"{product_id}|{now}|{actor.get('id')}|{source}"
    try:
        await db.stock_movements.insert_one({
            "id": hashlib.sha256(digest_seed.encode()).hexdigest()[:32],
            "schema_version": 2, "type": action, "action": action,
            "product_id": product_id, "product_name": product_name or "", "items": items,
            "source": source, "actor": actor, "context": context,
            "sync_result": sync_result or {"status": "not_applicable"},
            "metadata": metadata or {},
            "created_by": actor.get("email", ""), "created_at": now,
        })
    except Exception:
        # Auditing must never turn an already-applied inventory write into a
        # misleading API failure that an operator may retry.
        logging.getLogger(__name__).exception("stock audit insert failed product=%s", product_id)
        return 0
    return len(items)


async def record_stock_sync_audit(db, *, product: dict, platform: str, status: str,
                                  current_user: Optional[dict] = None, request=None,
                                  batch_id: str = "", message: str = "") -> int:
    """Record an explicit per-SKU outbound sync result without changing stock.

    ``old == new`` communicates that this is an outbound observation, not a
    local inventory mutation. Callers should use it for user-triggered syncs;
    high-frequency scheduled jobs keep their aggregate integration logs.
    """
    product = product or {}
    variants = product.get("variants") or []
    candidates = variants or [product]
    items = []
    for variant in candidates:
        quantity = _integer(variant.get("stock"), 0)
        items.append({
            "product_id": str(product.get("id") or ""),
            "variant_id": str(variant.get("id") or variant.get("urun_id") or ""),
            "sku": str(variant.get("stock_code") or variant.get("sku") or variant.get("barcode") or product.get("stock_code") or ""),
            "barcode": str(variant.get("barcode") or product.get("barcode") or ""),
            "size": str(variant.get("size") or ""), "color": str(variant.get("color") or product.get("color") or ""),
            "old": quantity, "new": quantity, "delta": 0,
        })
    actor, context = actor_context(current_user, request)
    now = datetime.now(timezone.utc).isoformat()
    seed = f"sync|{product.get('id')}|{platform}|{now}|{actor.get('id')}"
    try:
        await db.stock_movements.insert_one({
            "id": hashlib.sha256(seed.encode()).hexdigest()[:32], "schema_version": 2,
            "type": "marketplace_stock_sync", "action": "marketplace_stock_sync",
            "product_id": str(product.get("id") or ""), "product_name": str(product.get("name") or ""),
            "items": items, "source": platform, "actor": actor, "context": context,
            "sync_result": {"status": status, "platform": platform, "batch_id": str(batch_id or ""), "message": str(message or "")[:500]},
            "created_by": actor.get("email", ""), "created_at": now,
        })
    except Exception:
        logging.getLogger(__name__).exception("stock sync audit insert failed product=%s platform=%s", product.get("id"), platform)
        return 0
    return len(items)
