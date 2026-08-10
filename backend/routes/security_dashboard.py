"""
Security Dashboard — admin paneli için /api/admin/security/* endpoint'leri.

Backend'in `auth_audit_logs` koleksiyonu + `users.locked_until` field'ları
üzerinde özet/grafik/forensic sorgular sağlar. Iteration 33'te oluşturulan
audit log altyapısının üstüne kuruludur.
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from datetime import datetime, timezone, timedelta
from typing import Optional

from .deps import db, require_admin, require_super_admin

router = APIRouter(prefix="/admin/security", tags=["admin-security"])


def _iso_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@router.get("/summary")
async def security_summary(window_hours: int = Query(24, ge=1, le=720),
                           current_user: dict = Depends(require_admin)):
    """Top-level KPI'lar — son N saatte event sayıları + aktif lockout'lar."""
    since = _iso_ago(window_hours)
    base = {"created_at": {"$gte": since}}

    # Sayımlar
    total = await db.auth_audit_logs.count_documents(base)
    failed_logins = await db.auth_audit_logs.count_documents(
        {**base, "event": "login", "success": False}
    )
    successful_logins = await db.auth_audit_logs.count_documents(
        {**base, "event": "login", "success": True}
    )
    registrations = await db.auth_audit_logs.count_documents(
        {**base, "event": "register"}
    )
    pwd_changes_fail = await db.auth_audit_logs.count_documents(
        {**base, "event": "password_change", "success": False}
    )

    # Aktif kilitli hesaplar
    now_iso = datetime.now(timezone.utc).isoformat()
    locked_users_cur = db.users.find(
        {"locked_until": {"$gt": now_iso}},
        {"_id": 0, "email": 1, "locked_until": 1, "failed_attempts": 1}
    ).limit(50)
    locked_users = await locked_users_cur.to_list(50)

    # NoSQL injection denemeleri
    nosql_attempts = await db.auth_audit_logs.count_documents({
        **base, "event": "login", "success": False,
        "meta.reason": "invalid_email_format"
    })

    # Rate limit ile blocked attempts
    locked_attempts = await db.auth_audit_logs.count_documents({
        **base, "event": "login", "success": False,
        "meta.reason": "locked"
    })

    return {
        "window_hours": window_hours,
        "total_events": total,
        "successful_logins": successful_logins,
        "failed_logins": failed_logins,
        "registrations": registrations,
        "password_change_failures": pwd_changes_fail,
        "active_lockouts": len(locked_users),
        "locked_users": locked_users,
        "nosql_injection_attempts": nosql_attempts,
        "lockout_blocked_attempts": locked_attempts,
    }


@router.get("/top-failed-emails")
async def top_failed_emails(window_hours: int = Query(24, ge=1, le=720),
                            limit: int = Query(20, ge=1, le=100),
                            current_user: dict = Depends(require_admin)):
    """En çok başarısız login olan e-posta adresleri (brute force hedefleri)."""
    since = _iso_ago(window_hours)
    pipeline = [
        {"$match": {"event": "login", "success": False, "created_at": {"$gte": since}, "email": {"$ne": None}}},
        {"$group": {"_id": "$email", "count": {"$sum": 1},
                    "last_seen": {"$max": "$created_at"},
                    "ips": {"$addToSet": "$ip"}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
        {"$project": {"_id": 0, "email": "$_id", "count": 1, "last_seen": 1,
                      "distinct_ips": {"$size": "$ips"}}},
    ]
    rows = await db.auth_audit_logs.aggregate(pipeline).to_list(limit)
    return {"items": rows}


@router.get("/top-failed-ips")
async def top_failed_ips(window_hours: int = Query(24, ge=1, le=720),
                         limit: int = Query(20, ge=1, le=100),
                         current_user: dict = Depends(require_admin)):
    """En çok başarısız login deneyen IP'ler (botnet/saldırı tespiti)."""
    since = _iso_ago(window_hours)
    pipeline = [
        {"$match": {"event": "login", "success": False, "created_at": {"$gte": since},
                    "ip": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$ip", "count": {"$sum": 1},
                    "last_seen": {"$max": "$created_at"},
                    "emails": {"$addToSet": "$email"}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
        {"$project": {"_id": 0, "ip": "$_id", "count": 1, "last_seen": 1,
                      "distinct_emails": {"$size": "$emails"}}},
    ]
    rows = await db.auth_audit_logs.aggregate(pipeline).to_list(limit)
    return {"items": rows}


@router.get("/timeline")
async def login_timeline(window_hours: int = Query(24, ge=1, le=720),
                         current_user: dict = Depends(require_admin)):
    """Saat bazlı (success vs fail) login grafiği için zaman serisi."""
    since_dt = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    since = since_dt.isoformat()
    pipeline = [
        {"$match": {"event": "login", "created_at": {"$gte": since}}},
        {"$project": {
            "success": 1,
            "hour": {"$substr": ["$created_at", 0, 13]},  # YYYY-MM-DDTHH
        }},
        {"$group": {
            "_id": {"hour": "$hour", "success": "$success"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.hour": 1}},
    ]
    rows = await db.auth_audit_logs.aggregate(pipeline).to_list(2000)
    # Dict lookup -> grafik dostu liste
    buckets = {}
    for r in rows:
        h = r["_id"]["hour"]
        if h not in buckets:
            buckets[h] = {"hour": h, "success": 0, "fail": 0}
        if r["_id"]["success"]:
            buckets[h]["success"] += r["count"]
        else:
            buckets[h]["fail"] += r["count"]
    return {"items": sorted(buckets.values(), key=lambda x: x["hour"])}


@router.get("/recent-events")
async def recent_events(limit: int = Query(100, ge=1, le=500),
                        event: Optional[str] = Query(None),
                        success: Optional[bool] = Query(None),
                        email: Optional[str] = Query(None),
                        ip: Optional[str] = Query(None),
                        current_user: dict = Depends(require_admin)):
    """Son N audit log girişi (canlı tablo). Filtrelenebilir."""
    q = {}
    if event:
        q["event"] = event
    if success is not None:
        q["success"] = success
    if email:
        q["email"] = email.lower().strip()
    if ip:
        q["ip"] = ip.strip()
    cur = db.auth_audit_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cur.to_list(limit)
    return {"items": items, "total": await db.auth_audit_logs.count_documents(q)}


@router.post("/unlock-user")
async def unlock_user(payload: dict, current_user: dict = Depends(require_super_admin)):
    """Admin manuel olarak kilitli hesabın kilidini açar."""
    email = (payload or {}).get("email", "").lower().strip()
    if not email:
        return {"success": False, "message": "email zorunlu"}
    res = await db.users.update_one(
        {"email": email},
        {"$unset": {"failed_attempts": "", "first_failed_at": "", "locked_until": ""}}
    )
    # Audit
    try:
        await db.auth_audit_logs.insert_one({
            "id": __import__("uuid").uuid4().hex,
            "event": "admin_unlock",
            "email": email,
            "user_id": current_user.get("id"),
            "success": True,
            "meta": {"by_admin": current_user.get("email")},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass
    return {"success": True, "matched": res.matched_count, "modified": res.modified_count}


# ---------------------------------------------------------------------------
# IP BLOCKLIST MANAGEMENT
# ---------------------------------------------------------------------------

@router.get("/ip-blocklist")
async def list_ip_blocklist(current_user: dict = Depends(require_admin)):
    """Aktif IP ban'larını listeler. Otomatik (50+ failed/saat) ve manuel."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = db.ip_blocklist.find({
        "$or": [
            {"permanent": True},
            {"blocked_until": {"$gt": now_iso}},
        ]
    }, {"_id": 0}).sort("blocked_at", -1).limit(500)
    items = await cur.to_list(500)
    return {"items": items, "total": len(items)}


@router.post("/ip-blocklist")
async def block_ip(payload: dict, current_user: dict = Depends(require_super_admin)):
    """Manuel IP ban — admin'in ilettiği IP'yi blocklist'e ekler.
    Body: {ip, hours? (default=24), permanent? (default=False), reason?}
    """
    ip = (payload or {}).get("ip", "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="ip zorunlu")
    hours = int((payload or {}).get("hours") or 24)
    permanent = bool((payload or {}).get("permanent") or False)
    reason = (payload or {}).get("reason") or f"manuel ban by {current_user.get('email')}"

    until = (datetime.now(timezone.utc) + timedelta(hours=max(1, hours))).isoformat()
    update = {
        "ip": ip,
        "blocked_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "auto_blocked": False,
        "blocked_by": current_user.get("email"),
    }
    if permanent:
        update["permanent"] = True
        update.pop("blocked_until", None)
    else:
        update["blocked_until"] = until
        update["permanent"] = False

    await db.ip_blocklist.update_one(
        {"ip": ip},
        {"$set": update, "$setOnInsert": {"id": __import__("uuid").uuid4().hex}},
        upsert=True,
    )
    await db.auth_audit_logs.insert_one({
        "id": __import__("uuid").uuid4().hex,
        "event": "admin_ip_block",
        "ip": ip,
        "user_id": current_user.get("id"),
        "email": current_user.get("email"),
        "success": True,
        "meta": {"hours": hours, "permanent": permanent, "reason": reason},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "ip": ip, "permanent": permanent, "blocked_until": update.get("blocked_until")}


@router.delete("/ip-blocklist/{ip}")
async def unblock_ip(ip: str, current_user: dict = Depends(require_super_admin)):
    """IP ban'ı kaldır."""
    res = await db.ip_blocklist.delete_one({"ip": ip})
    await db.auth_audit_logs.insert_one({
        "id": __import__("uuid").uuid4().hex,
        "event": "admin_ip_unblock",
        "ip": ip,
        "user_id": current_user.get("id"),
        "email": current_user.get("email"),
        "success": True,
        "meta": {"by_admin": current_user.get("email")},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "deleted": res.deleted_count}
