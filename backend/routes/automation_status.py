"""
=============================================================================
automation_status.py — Otomasyon Durumu / Cron Görünürlük Paneli
=============================================================================
Admin'in arka planda hangi işlerin çalıştığını / ne zaman çalıştığını /
son loglarını görebilmesi için tek noktadan birleştirilmiş özet endpoint.

Birleştirir:
  • APScheduler aktif job listesi (id, interval, next_run, last_run)
  • Marketplace auto_sync ayarları (Trendyol/HB/Temu vs.) interval ve son tetikleme
  • integration_logs koleksiyonundan son N event
  • Bekleyen / başarısız işler özeti

ENDPOINT:
  GET /api/admin/automation/status
=============================================================================
"""
from fastapi import APIRouter, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from .deps import db, require_admin

router = APIRouter(prefix="/admin/automation", tags=["admin-automation"])


def _to_iso(dt) -> str:
    if not dt:
        return None
    if isinstance(dt, str):
        return dt
    if hasattr(dt, "isoformat"):
        try:
            return dt.isoformat()
        except Exception:
            return str(dt)
    return str(dt)


def _interval_label(seconds: int) -> str:
    if not seconds:
        return "-"
    if seconds < 60:
        return f"{seconds} sn"
    if seconds < 3600:
        return f"{seconds // 60} dk"
    if seconds < 86400:
        return f"{seconds // 3600} sa"
    return f"{seconds // 86400} gün"


@router.get("/status")
async def get_automation_status(
    log_limit: int = Query(50, ge=10, le=500),
    current_user: dict = Depends(require_admin),
):
    """Tüm otomatik işlerin tek seferde özet durumu."""

    # 1) APScheduler job'ları
    jobs_out: List[Dict[str, Any]] = []
    try:
        from scheduler import _scheduler  # type: ignore
        if _scheduler:
            for job in _scheduler.get_jobs():
                interval_sec = None
                try:
                    if hasattr(job.trigger, "interval"):
                        interval_sec = int(job.trigger.interval.total_seconds())
                except Exception:
                    pass
                jobs_out.append({
                    "id": job.id,
                    "name": job.name or job.id,
                    "next_run": _to_iso(job.next_run_time),
                    "interval_sec": interval_sec,
                    "interval_label": _interval_label(interval_sec) if interval_sec else None,
                    "trigger": str(job.trigger),
                })
    except Exception as e:
        jobs_out.append({"error": f"Scheduler okunamadı: {e}"})

    # 2) Marketplace auto_sync ayarları (Trendyol/HB/Temu vb.)
    marketplaces: List[Dict[str, Any]] = []
    async for acc in db.marketplace_accounts.find({}, {"_id": 0}):
        auto = acc.get("auto_sync") or {}
        marketplaces.append({
            "key": acc.get("key"),
            "name": acc.get("name") or acc.get("key"),
            "enabled": bool(acc.get("enabled")),
            "products_enabled": bool(auto.get("products_enabled")),
            "products_interval_min": auto.get("products_interval_min"),
            "orders_enabled": bool(auto.get("orders_enabled")),
            "orders_interval_min": auto.get("orders_interval_min"),
            "last_orders_sync": auto.get("_last_orders_sync"),
            "last_products_sync": auto.get("_last_products_sync"),
        })

    # 3) Son N integration log
    log_cursor = db.integration_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(log_limit)
    logs = await log_cursor.to_list(length=log_limit)
    # Hızlı özet: marketplace + status grupla
    summary: Dict[str, Dict[str, int]] = {}
    for log_doc in logs:
        mk = (log_doc.get("marketplace") or "diger").lower()
        st = (log_doc.get("status") or "info").lower()
        summary.setdefault(mk, {"success": 0, "error": 0, "info": 0})
        summary[mk][st if st in ("success", "error") else "info"] = summary[mk].get(
            st if st in ("success", "error") else "info", 0
        ) + 1

    # 4) Webhook konfigürasyonu (frontend için kolay erişim)
    settings = await db.settings.find_one({"id": "ticimax"}) or {}
    has_dogan = bool((await db.settings.find_one({"id": "dogan_edonusum"}) or {}).get("api_key"))
    import os
    has_resend = bool(os.environ.get("RESEND_API_KEY", "").strip())

    return {
        "now": _to_iso(datetime.now(timezone.utc)),
        "jobs": jobs_out,
        "marketplaces": marketplaces,
        "logs": logs,
        "log_summary": summary,
        "integrations": {
            "ticimax_configured": bool(settings.get("api_key")),
            "dogan_configured": has_dogan,
            "resend_configured": has_resend,
        },
    }
