"""
Centralized error & request monitoring.

- ErrorTrackingMiddleware: catches unhandled exceptions, persists to
  `error_logs`, and emits a `critical` alert when error rate spikes.
- record_event(): manual instrumentation hook (used by integrations).
- compute_health(): fast snapshot for the admin dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from routes.deps import db, generate_id, client_ip_from_request
from security.alerts import send_alert

logger = logging.getLogger(__name__)


# Error-rate burst detection — if more than N errors hit in WINDOW seconds we
# fire a critical alert.
_ERR_BURST_WINDOW_SEC = 60
_ERR_BURST_THRESHOLD = 10
_recent_errors: list[float] = []  # in-process timestamps
_burst_lock = asyncio.Lock()


async def _maybe_alert_on_burst() -> None:
    async with _burst_lock:
        now = time.time()
        cutoff = now - _ERR_BURST_WINDOW_SEC
        # prune
        while _recent_errors and _recent_errors[0] < cutoff:
            _recent_errors.pop(0)
        _recent_errors.append(now)
        if len(_recent_errors) >= _ERR_BURST_THRESHOLD:
            await send_alert(
                kind="error_spike",
                level="critical",
                title=f"Hata Patlaması: {len(_recent_errors)} hata / {_ERR_BURST_WINDOW_SEC}s",
                body="Backend son 60 saniyede yüksek miktarda 5xx üretti. "
                     "/admin/system-health üzerinden detayları inceleyin.",
                fingerprint="error_spike_60s",
                meta={"count": len(_recent_errors), "window_sec": _ERR_BURST_WINDOW_SEC},
            )
            _recent_errors.clear()


async def record_event(kind: str, *, level: str = "info", message: str = "",
                       path: str = "", meta: Optional[dict] = None) -> None:
    """Persist a structured event row (visible in /admin/system-health)."""
    try:
        await db.error_logs.insert_one({
            "id": generate_id(),
            "kind": kind,
            "level": level,
            "message": (message or "")[:2000],
            "path": (path or "")[:500],
            "meta": meta or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"record_event failed: {e}")


class ErrorTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        path = request.url.path
        method = request.method
        try:
            response = await call_next(request)
        except Exception as e:
            # Starlette emits RuntimeError("No response returned.") when the
            # client disconnects mid-stream. This is benign noise, not a server
            # bug — drop it so the dashboard isn't flooded with false positives.
            err_str = str(e)
            if isinstance(e, RuntimeError) and "No response returned" in err_str:
                return JSONResponse(status_code=499, content={"detail": "client disconnected"})
            tb = traceback.format_exc()
            await record_event(
                kind="exception",
                level="critical",
                message=f"{type(e).__name__}: {e}",
                path=path,
                meta={
                    "method": method,
                    "ip": client_ip_from_request(request),
                    "trace": tb[-2000:],
                },
            )
            await _maybe_alert_on_burst()
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error", "error_id": "see /admin/system-health"},
            )

        # Track 5xx as errors and slow responses (>3s) as warnings
        elapsed_ms = int((time.time() - start) * 1000)
        if response.status_code >= 500:
            await record_event(
                kind="http_5xx", level="critical",
                message=f"HTTP {response.status_code} {method} {path}",
                path=path,
                meta={"duration_ms": elapsed_ms, "status": response.status_code,
                      "ip": client_ip_from_request(request)},
            )
            await _maybe_alert_on_burst()
        elif elapsed_ms > 3000 and path.startswith("/api/"):
            await record_event(
                kind="slow_response", level="warning",
                message=f"Slow {elapsed_ms}ms {method} {path}",
                path=path, meta={"duration_ms": elapsed_ms},
            )
        return response


async def compute_health() -> dict:
    """Quick health snapshot for the admin dashboard."""
    now = datetime.now(timezone.utc)
    last_24h = (now - timedelta(hours=24)).isoformat()
    last_1h = (now - timedelta(hours=1)).isoformat()
    last_5m = (now - timedelta(minutes=5)).isoformat()

    pipeline_24h = [
        {"$match": {"created_at": {"$gte": last_24h}}},
        {"$group": {"_id": "$level", "count": {"$sum": 1}}},
    ]
    counts_24h = {"critical": 0, "warning": 0, "info": 0}
    async for row in db.error_logs.aggregate(pipeline_24h):
        counts_24h[row["_id"]] = row["count"]

    err_5m = await db.error_logs.count_documents({"level": "critical", "created_at": {"$gte": last_5m}})
    err_1h = await db.error_logs.count_documents({"level": "critical", "created_at": {"$gte": last_1h}})
    unread_alerts = await db.alerts.count_documents({"read": False})

    # Mongo ping latency
    mongo_latency_ms = None
    try:
        t0 = time.time()
        await db.command("ping")
        mongo_latency_ms = int((time.time() - t0) * 1000)
    except Exception:
        mongo_latency_ms = -1

    # Status banner
    status = "healthy"
    if err_5m >= _ERR_BURST_THRESHOLD or mongo_latency_ms == -1:
        status = "down"
    elif err_5m >= 3 or (mongo_latency_ms and mongo_latency_ms > 500):
        status = "degraded"

    return {
        "status": status,
        "mongo_latency_ms": mongo_latency_ms,
        "errors": {"last_5m": err_5m, "last_1h": err_1h, **counts_24h},
        "unread_alerts": unread_alerts,
        "checked_at": now.isoformat(),
    }
