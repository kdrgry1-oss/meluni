"""
Admin Tasks (Görevler) & Weekly Checklist.

Görevler her tamamlandığında sıradaki `due_at` otomatik hesaplanır (daily/weekly/
biweekly/monthly/quarterly/custom_days).

Seed ile 12 standart çalışma görevi eklenir (ilk çağrıda).
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Optional
import uuid

from .deps import db, require_admin


router = APIRouter(prefix="/admin/tasks", tags=["admin-tasks"])


FREQUENCY_DAYS = {
    "daily": 1,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compute_next_due(frequency: str, custom_days: Optional[int] = None, base: Optional[datetime] = None) -> str:
    base = base or _now()
    if frequency == "once":
        return base.isoformat()
    if frequency == "custom" and custom_days and custom_days > 0:
        return (base + timedelta(days=custom_days)).isoformat()
    days = FREQUENCY_DAYS.get(frequency, 7)
    return (base + timedelta(days=days)).isoformat()


SEED_TASKS = [
    {"title": "Müşteri sorularını kontrol et", "category": "customer_support", "frequency": "daily", "priority": "high", "action_path": "/admin/sorular", "icon": "MessageSquare"},
    {"title": "Destek talepleri (ticket) yanıtla", "category": "customer_support", "frequency": "daily", "priority": "high", "action_path": "/admin/tickets", "icon": "MessageSquare"},
    {"title": "Yeni ürün yorumlarını moderasyondan geçir", "category": "customer_support", "frequency": "daily", "priority": "normal", "action_path": "/admin/yorumlar", "icon": "Star"},
    {"title": "Havale/EFT bildirimlerini onayla", "category": "orders", "frequency": "daily", "priority": "high", "action_path": "/admin/havale-bildirimleri", "icon": "Banknote"},
    {"title": "Bekleyen siparişleri kargoya ver", "category": "orders", "frequency": "daily", "priority": "high", "action_path": "/admin/siparisler", "icon": "Truck"},
    {"title": "Stok/fiyat alarm kayıtlarını incele", "category": "stock", "frequency": "weekly", "priority": "normal", "action_path": "/admin/stok-alarm", "icon": "BellRing"},
    {"title": "Terkedilmiş sepet sahiplerine hatırlatma maili at", "category": "marketing", "frequency": "weekly", "priority": "high", "action_path": "/admin/terkedilmis-sepet", "icon": "ShoppingCart"},
    {"title": "Haftalık bülten (toplu mail) gönder", "category": "marketing", "frequency": "weekly", "priority": "normal", "action_path": "/admin/toplu-mail", "icon": "Mail"},
    {"title": "Bu haftanın kampanyasını kurgula", "category": "marketing", "frequency": "weekly", "priority": "high", "action_path": "/admin/kampanyalar", "icon": "Megaphone"},
    {"title": "Satış raporunu incele, karar al", "category": "reporting", "frequency": "weekly", "priority": "normal", "action_path": "/admin/raporlar/satis", "icon": "TrendingUp"},
    {"title": "Stok biten ürünleri yeniden sipariş et", "category": "stock", "frequency": "weekly", "priority": "high", "action_path": "/admin/raporlar/stok", "icon": "Package"},
    {"title": "Anasayfa banner/slider güncelle", "category": "content", "frequency": "biweekly", "priority": "normal", "action_path": "/admin/bannerlar", "icon": "Image"},
    {"title": "SEO meta eksik sayfaları doldur", "category": "seo", "frequency": "monthly", "priority": "low", "action_path": "/admin/seo/meta", "icon": "FileText"},
    {"title": "Döviz kurlarını yenile", "category": "settings", "frequency": "weekly", "priority": "low", "action_path": "/admin/doviz", "icon": "DollarSign"},
    {"title": "Ürün karlılık raporunu gözden geçir", "category": "reporting", "frequency": "monthly", "priority": "normal", "action_path": "/admin/raporlar/gelismis", "icon": "DollarSign"},
    {"title": "Pazaryeri bağlantılarını (Trendyol/HB/Temu) test et", "category": "integrations", "frequency": "monthly", "priority": "normal", "action_path": "/admin/entegrasyonlar", "icon": "Cable"},
]


@router.post("/seed-defaults")
async def seed_defaults(current_user: dict = Depends(require_admin)):
    """Ilk kurulumda 16 standart görevi ekler. Tekrar çağrılırsa, `is_default=True`
    olan görevler silinip yeniden oluşturulur — custom görevler dokunulmaz."""
    await db.admin_tasks.delete_many({"is_default": True})
    now = _now().isoformat()
    docs = []
    for t in SEED_TASKS:
        docs.append({
            "id": str(uuid.uuid4()),
            "title": t["title"],
            "category": t["category"],
            "frequency": t["frequency"],
            "priority": t["priority"],
            "action_path": t.get("action_path"),
            "icon": t.get("icon"),
            "is_default": True,
            "is_active": True,
            "due_at": now,  # Hemen görünsün
            "last_completed_at": None,
            "completion_count": 0,
            "created_at": now,
        })
    if docs:
        await db.admin_tasks.insert_many(docs)
    return {"success": True, "inserted": len(docs)}


@router.get("")
async def list_tasks(
    include_future: bool = Query(True),
    category: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    q: dict = {"is_active": True}
    if category:
        q["category"] = category
    rows = await db.admin_tasks.find(q, {"_id": 0}).sort("due_at", 1).to_list(500)
    now = _now().isoformat()
    due_now = [r for r in rows if (r.get("due_at") or "") <= now]
    upcoming = [r for r in rows if (r.get("due_at") or "") > now]
    # Group counts by priority among due
    by_priority = {"urgent": 0, "high": 0, "normal": 0, "low": 0}
    for r in due_now:
        by_priority[r.get("priority", "normal")] = by_priority.get(r.get("priority", "normal"), 0) + 1
    return {
        "due_now": due_now,
        "upcoming": upcoming if include_future else [],
        "totals": {"due_now": len(due_now), "upcoming": len(upcoming), "by_priority": by_priority},
    }


@router.post("")
async def create_task(payload: dict, current_user: dict = Depends(require_admin)):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Başlık zorunlu")
    freq = payload.get("frequency", "weekly")
    if freq not in {"once", "daily", "weekly", "biweekly", "monthly", "quarterly", "yearly", "custom"}:
        freq = "weekly"
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": payload.get("description", ""),
        "category": payload.get("category", "other"),
        "frequency": freq,
        "custom_days": int(payload.get("custom_days") or 0) or None,
        "priority": payload.get("priority", "normal"),
        "action_path": payload.get("action_path"),
        "icon": payload.get("icon"),
        "is_default": False,
        "is_active": True,
        "due_at": payload.get("due_at") or now.isoformat(),
        "last_completed_at": None,
        "completion_count": 0,
        "created_at": now.isoformat(),
        "created_by": current_user.get("email", ""),
    }
    await db.admin_tasks.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "task": doc}


@router.put("/{tid}")
async def update_task(tid: str, payload: dict, current_user: dict = Depends(require_admin)):
    allowed = ("title", "description", "category", "frequency", "custom_days", "priority", "action_path", "icon", "is_active", "due_at")
    update = {k: v for k, v in payload.items() if k in allowed}
    update["updated_at"] = _now().isoformat()
    res = await db.admin_tasks.update_one({"id": tid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    return {"success": True}


@router.post("/{tid}/complete")
async def complete_task(tid: str, payload: Optional[dict] = None, current_user: dict = Depends(require_admin)):
    doc = await db.admin_tasks.find_one({"id": tid})
    if not doc:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")

    now = _now()
    # Log completion
    log_doc = {
        "id": str(uuid.uuid4()),
        "task_id": tid,
        "task_title": doc.get("title"),
        "completed_at": now.isoformat(),
        "completed_by": current_user.get("email", ""),
        "note": (payload or {}).get("note", ""),
    }
    await db.admin_task_logs.insert_one(log_doc)

    # Recurring → next due
    freq = doc.get("frequency", "weekly")
    update = {
        "last_completed_at": now.isoformat(),
        "$inc_skip": 0,  # placeholder
    }
    next_due = _compute_next_due(freq, doc.get("custom_days"), now) if freq != "once" else None
    mongo_update: dict = {"$set": {"last_completed_at": now.isoformat()}, "$inc": {"completion_count": 1}}
    if freq == "once":
        mongo_update["$set"]["is_active"] = False
    else:
        mongo_update["$set"]["due_at"] = next_due

    await db.admin_tasks.update_one({"id": tid}, mongo_update)
    return {"success": True, "next_due_at": next_due}


@router.post("/{tid}/snooze")
async def snooze_task(tid: str, payload: dict, current_user: dict = Depends(require_admin)):
    hours = int(payload.get("hours") or 24)
    new_due = (_now() + timedelta(hours=hours)).isoformat()
    res = await db.admin_tasks.update_one({"id": tid}, {"$set": {"due_at": new_due, "snoozed_at": _now().isoformat()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    return {"success": True, "new_due_at": new_due}


@router.delete("/{tid}")
async def delete_task(tid: str, current_user: dict = Depends(require_admin)):
    res = await db.admin_tasks.delete_one({"id": tid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Görev bulunamadı")
    return {"success": True}


@router.get("/history")
async def task_history(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(require_admin),
):
    cutoff = (_now() - timedelta(days=days)).isoformat()
    rows = await db.admin_task_logs.find({"completed_at": {"$gte": cutoff}}, {"_id": 0}).sort("completed_at", -1).to_list(500)
    # Group by day for streak chart
    from collections import Counter
    counter: Counter = Counter()
    for r in rows:
        day = (r.get("completed_at") or "")[:10]
        counter[day] += 1
    streak = [{"date": d, "completed": c} for d, c in sorted(counter.items())]
    return {"logs": rows, "total": len(rows), "streak": streak}


@router.get("/summary")
async def summary(current_user: dict = Depends(require_admin)):
    """Dashboard widget: bugün için kaç görev bekliyor + bu hafta tamamlanma sayısı."""
    now = _now().isoformat()
    week_ago = (_now() - timedelta(days=7)).isoformat()
    due_now = await db.admin_tasks.count_documents({"is_active": True, "due_at": {"$lte": now}})
    overdue = await db.admin_tasks.count_documents({"is_active": True, "due_at": {"$lt": (_now() - timedelta(days=1)).isoformat()}})
    completed_week = await db.admin_task_logs.count_documents({"completed_at": {"$gte": week_ago}})
    return {"due_now": due_now, "overdue": overdue, "completed_this_week": completed_week}
