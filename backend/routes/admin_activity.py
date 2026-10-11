"""Unified, read-only admin activity view over existing append-only logs."""
from collections import Counter
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from typing import Optional

from activity_audit import redact_value
from .deps import db, require_permission, tr_day_start_utc, tr_day_end_utc


router = APIRouter(prefix="/admin/activity", tags=["admin-activity"])
_SCAN_LIMIT = 2000


def _actor(doc: dict):
    raw = doc.get("actor")
    if isinstance(raw, dict):
        return {
            "id": str(raw.get("id") or raw.get("user_id") or ""),
            "email": str(raw.get("email") or raw.get("username") or ""),
            "email_hash": str(raw.get("email_hash") or ""),
            "role": str(raw.get("role") or ""),
            "login_method": str(raw.get("login_method") or raw.get("auth_provider") or ""),
        }
    return {
        "id": str(doc.get("user_id") or ""),
        "email": str(raw or doc.get("email") or doc.get("created_by") or ""),
        "email_hash": "", "role": "", "login_method": str(doc.get("login_method") or ""),
    }


def normalize_activity(collection: str, doc: dict) -> dict:
    """Normalize legacy documents without inventing unavailable fields."""
    actor = _actor(doc)
    context = doc.get("context") if isinstance(doc.get("context"), dict) else {}
    if collection == "auth_audit_logs":
        auth_meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        actor["login_method"] = str(doc.get("login_method") or auth_meta.get("provider") or "")
        event = str(doc.get("event") or "auth_event")
        source = "auth"
        entity = {"type": "user", "id": str(doc.get("user_id") or "")}
        success = doc.get("success") if isinstance(doc.get("success"), bool) else None
        summary = event
        context = {"ip": doc.get("ip") or "", "user_agent": doc.get("user_agent") or "",
                   "session_hash": ""}
        details = {}
    elif collection == "order_events":
        event = str(doc.get("event_type") or "order_event")
        source = "order_events"
        entity = {"type": "order", "id": str(doc.get("order_id") or "")}
        success = doc.get("success") if isinstance(doc.get("success"), bool) else None
        # Legacy descriptions/meta can contain free-form customer notes. Do not
        # expose them in the cross-module view; the event type is the safe label.
        summary = event
        details = {}
    elif collection == "stock_movements":
        event = str(doc.get("action") or doc.get("type") or "stock_movement")
        source = str(doc.get("source") or "stock_movements")
        pid = doc.get("product_id")
        if not pid:
            entries = doc.get("items") or doc.get("moves") or []
            pid = next((i.get("product_id") for i in entries if isinstance(i, dict) and i.get("product_id")), "")
        entity = {"type": "product", "id": str(pid or "")}
        status = (doc.get("sync_result") or {}).get("status") if isinstance(doc.get("sync_result"), dict) else ""
        success = True if status in {"success", "submitted"} else False if status in {"failed", "error"} else None
        summary = event
        stock_items = doc.get("items") or doc.get("moves") or []
        details = {"item_count": len(stock_items), "items": redact_value(stock_items)}
    else:
        event = str(doc.get("action") or doc.get("event") or "audit_event")
        source = str(doc.get("source") or doc.get("category") or "audit_logs")
        raw_entity = doc.get("entity") if isinstance(doc.get("entity"), dict) else {}
        entity = {"type": str(raw_entity.get("type") or doc.get("category") or "system"),
                  "id": str(raw_entity.get("id") or doc.get("entity_id") or "")}
        success = doc.get("success") if isinstance(doc.get("success"), bool) else None
        summary = event
        details = ({"diff": redact_value(doc.get("diff") or {}),
                    "metadata": redact_value(doc.get("metadata") or {})}
                   if int(doc.get("schema_version") or 1) >= 2 else {})
    return {
        "id": str(doc.get("id") or ""), "date": str(doc.get("created_at") or doc.get("at") or ""),
        "event": event, "source": source, "success": success, "actor": actor,
        "context": {"session_hash": str(context.get("session_hash") or ""),
                    "ip": str(context.get("ip") or ""),
                    "user_agent": str(context.get("user_agent") or "")[:300]},
        "entity": entity, "summary": redact_value(str(summary), key="summary"),
        "details": details, "origin_collection": collection,
        "legacy_incomplete": int(doc.get("schema_version") or 1) < 2,
    }


def _contains(value, needle: str) -> bool:
    return needle.casefold() in str(value or "").casefold()


@router.get("")
async def activity_feed(
    user: Optional[str] = None, event: Optional[str] = None, source: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    success: Optional[bool] = None, page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_permission("audit.read")),
):
    date_query = {}
    if date_from:
        date_query["$gte"] = tr_day_start_utc(date_from[:10])
    if date_to:
        date_query["$lte"] = tr_day_end_utc(date_to[:10])
    base = {"created_at": date_query} if date_query else {}
    collections = ("auth_audit_logs", "order_events", "stock_movements", "audit_logs")
    items, capped = [], []
    for name in collections:
        query = dict(base)
        if name == "auth_audit_logs" and success is not None:
            query["success"] = success
        rows = await db[name].find(query, {"_id": 0}).sort("created_at", -1).limit(_SCAN_LIMIT).to_list(_SCAN_LIMIT)
        if len(rows) == _SCAN_LIMIT:
            capped.append(name)
        items.extend(normalize_activity(name, row) for row in rows)

    # New audit records retain only internal actor id + email hash. Resolve the
    # display email at authorized read time instead of duplicating PII in logs.
    actor_ids = list({x["actor"].get("id") for x in items
                      if x["actor"].get("id") and not x["actor"].get("email")})
    users_by_id = {}
    for i in range(0, len(actor_ids), 5000):
        async for u in db.users.find({"id": {"$in": actor_ids[i:i + 5000]}}, {"_id": 0, "id": 1, "email": 1}):
            users_by_id[str(u.get("id") or "")] = str(u.get("email") or "")
    for item in items:
        if not item["actor"].get("email"):
            item["actor"]["email"] = users_by_id.get(item["actor"].get("id"), "")

    if user:
        items = [x for x in items if _contains(x["actor"].get("email"), user)
                 or _contains(x["actor"].get("id"), user)]
    if event:
        items = [x for x in items if _contains(x.get("event"), event)]
    if source:
        items = [x for x in items if _contains(x.get("source"), source)
                 or _contains(x.get("origin_collection"), source)]
    if success is not None:
        items = [x for x in items if x.get("success") is success]
    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    counts = Counter(x["source"] for x in items)
    total = len(items)
    start = (page - 1) * limit
    return {
        "items": items[start:start + limit], "total": total, "page": page, "limit": limit,
        "source_counts": dict(counts), "scan_capped_sources": capped,
        "coverage": "Kaynak kayıtlar değiştirilmez. Geçmiş kayıtlarda bulunmayan kullanıcı, başarı veya bağlam alanları tahmin edilmez.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
