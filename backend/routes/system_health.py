"""
System Health & Monitoring admin endpoints.

  GET  /api/admin/system/health          — health snapshot
  GET  /api/admin/system/errors          — recent error logs (paginated)
  GET  /api/admin/system/alerts          — recent alerts (paginated)
  POST /api/admin/system/alerts/{id}/read — mark alert read
  POST /api/admin/system/alerts/test     — fire a test alert (super_admin)
  GET  /api/admin/system/circuits        — circuit breaker states
  GET  /api/admin/system/cache           — cache stats
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .deps import db, require_admin
from security.monitoring import compute_health
from security.alerts import send_alert
from security.circuit_breaker import all_states
from cache import cache_stats

router = APIRouter(prefix="/admin/system", tags=["system-health"])


def _is_super(user: dict) -> bool:
    if not user:
        return False
    return bool(user.get("is_super_admin")) or user.get("email") == "admin@facette.com"


@router.get("/health")
async def health(_=Depends(require_admin)):
    return await compute_health()


@router.get("/errors")
async def errors(
    level: Optional[str] = Query(None, regex="^(info|warning|critical)$"),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(50, ge=1, le=200),
    _=Depends(require_admin),
):
    q = {}
    if level:
        q["level"] = level
    skip = (page - 1) * limit
    cursor = db.error_logs.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    total = await db.error_logs.count_documents(q)
    return {"items": items, "total": total, "page": page, "limit": limit}


@router.get("/alerts")
async def alerts(
    only_unread: bool = Query(False),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(50, ge=1, le=200),
    _=Depends(require_admin),
):
    q = {}
    if only_unread:
        q["read"] = False
    skip = (page - 1) * limit
    cursor = db.alerts.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    total = await db.alerts.count_documents(q)
    unread = await db.alerts.count_documents({"read": False})
    return {"items": items, "total": total, "unread": unread, "page": page, "limit": limit}


@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: str, _=Depends(require_admin)):
    res = await db.alerts.update_one({"id": alert_id}, {"$set": {"read": True, "read_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": res.modified_count > 0}


@router.post("/alerts/test")
async def fire_test_alert(admin=Depends(require_admin)):
    if not _is_super(admin):
        raise HTTPException(status_code=403, detail="Sadece süper admin test alarmı tetikleyebilir")
    # Soft rate-limit: 1 test alert per 30s per super_admin
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    recent = await db.alerts.count_documents({
        "kind": "manual_test",
        "meta.triggered_by": admin.get("email"),
        "created_at": {"$gte": cutoff},
    })
    if recent >= 1:
        raise HTTPException(status_code=429, detail="Çok hızlı — 30 saniye bekleyin")
    result = await send_alert(
        kind="manual_test",
        level="warning",
        title="Facette Monitoring Test",
        body=f"Bu bir test alarmıdır. Tetikleyen: {admin.get('email')}",
        fingerprint=f"manual_test_{admin.get('id')}",
        meta={"triggered_by": admin.get("email")},
    )
    return result


@router.get("/circuits")
async def circuits(_=Depends(require_admin)):
    return {"breakers": all_states()}


@router.get("/cache")
async def cache(_=Depends(require_admin)):
    return await cache_stats()
