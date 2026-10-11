"""
Security Dashboard — admin paneli için /api/admin/security/* endpoint'leri.

Backend'in `auth_audit_logs` koleksiyonu + `users.locked_until` field'ları
üzerinde özet/grafik/forensic sorgular sağlar. Iteration 33'te oluşturulan
audit log altyapısının üstüne kuruludur.
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from datetime import datetime, timezone, timedelta
from typing import Optional

from .deps import db, require_admin, require_super_admin, require_permission
from activity_audit import run_audited

router = APIRouter(prefix="/admin/security", tags=["admin-security"])


def _iso_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@router.get("/summary")
async def security_summary(window_hours: int = Query(24, ge=1, le=720),
                           current_user: dict = Depends(require_permission("admin.logs"))):
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

    # IP+hesap kilitleri (login_ip_failures) — aktif olanlar
    try:
        ip_account_locks = await db.login_ip_failures.count_documents({"locked_until": {"$gt": now_iso}})
    except Exception:
        ip_account_locks = 0
    try:
        from security.approvals import pending_count
        pending_approvals = await pending_count()
    except Exception:
        pending_approvals = 0
    try:
        from security.audit_chain import chain_status
        _cs = await chain_status(db)
        audit_chain = {k: _cs.get(k) for k in ("head_seq", "exported_seq", "last_export_at", "last_export_error",
                                               "last_verify_at", "last_verify_ok", "archive_enabled")}
    except Exception:
        audit_chain = {}

    return {
        "ip_account_locks": ip_account_locks,
        "pending_approvals": pending_approvals,
        "audit_chain": audit_chain,
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
                            current_user: dict = Depends(require_permission("admin.logs"))):
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
                         current_user: dict = Depends(require_permission("admin.logs"))):
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
                         current_user: dict = Depends(require_permission("admin.logs"))):
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
                        current_user: dict = Depends(require_permission("admin.logs"))):
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
async def unlock_user(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    """Admin manuel olarak kilitli hesabın kilidini açar (fail-closed denetimli)."""
    email = str((payload or {}).get("email") or "").lower().strip()
    if not email:
        return {"success": False, "message": "email zorunlu"}
    target = await db.users.find_one({"email": email}, {"_id": 0, "id": 1, "locked_until": 1,
                                                        "failed_attempts": 1}) or {}
    out = {}

    async def _perform():
        out["res"] = await db.users.update_one(
            {"email": email},
            {"$unset": {"failed_attempts": "", "first_failed_at": "", "locked_until": ""}}
        )
        # Bu hesabın IP+hesap kilitleri de kalkar.
        await db.login_ip_failures.delete_many({"email": email})
    await run_audited(db, action="security.unlock_user", entity_type="user", entity_id=target.get("id") or "?",
                      perform=_perform, before={"locked_until": target.get("locked_until"),
                                                "failed_attempts": target.get("failed_attempts")},
                      after={"locked_until": None}, current_user=current_user, request=request,
                      source="admin.security")
    res = out["res"]
    # Audit (eski güvenlik olay akışı — panel listesinde görünür)
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


@router.get("/locks")
async def list_locks(current_user: dict = Depends(require_permission("admin.logs"))):
    """Aktif IP+hesap kilitleri (login_ip_failures) + hesap-geneli kilitler."""
    now_iso = datetime.now(timezone.utc).isoformat()
    ip_locks = await db.login_ip_failures.find(
        {"locked_until": {"$gt": now_iso}},
        {"_id": 0, "key": 0}).sort("locked_until", -1).limit(200).to_list(200)
    acct = await db.users.find({"locked_until": {"$gt": now_iso}},
                               {"_id": 0, "email": 1, "locked_until": 1, "failed_attempts": 1}) \
        .limit(100).to_list(100)
    return {"ip_account_locks": ip_locks, "account_locks": acct}


@router.post("/locks/clear")
async def clear_ip_lock(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
    """Tek bir IP+hesap kilidini kaldırır. Body: {email, ip}."""
    email = str((payload or {}).get("email") or "").lower().strip()
    ip = str((payload or {}).get("ip") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="email zorunlu")
    q = {"email": email}
    if ip:
        q["ip"] = ip
    out = {}

    async def _perform():
        out["res"] = await db.login_ip_failures.delete_many(q)
    await run_audited(db, action="security.clear_ip_lock", entity_type="login_lock", entity_id=ip or "*",
                      perform=_perform, current_user=current_user, request=request, source="admin.security",
                      metadata={"ip": ip or "*"})
    return {"success": True, "deleted": out["res"].deleted_count}


# ---------------------------------------------------------------------------
# IP BLOCKLIST MANAGEMENT
# ---------------------------------------------------------------------------

@router.get("/ip-blocklist")
async def list_ip_blocklist(current_user: dict = Depends(require_permission("admin.logs"))):
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
async def block_ip(payload: dict, request: Request, current_user: dict = Depends(require_super_admin)):
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

    _before = await db.ip_blocklist.find_one({"ip": ip}, {"_id": 0, "blocked_until": 1, "permanent": 1})

    async def _perform():
        await db.ip_blocklist.update_one(
            {"ip": ip},
            {"$set": update, "$setOnInsert": {"id": __import__("uuid").uuid4().hex}},
            upsert=True,
        )
    await run_audited(db, action="security.ip_block", entity_type="ip", entity_id=ip, perform=_perform,
                      before=_before or {}, after={"blocked_until": update.get("blocked_until"),
                                                   "permanent": permanent},
                      current_user=current_user, request=request, source="admin.security",
                      metadata={"hours": hours, "reason": str(reason)[:200]})
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
async def unblock_ip(ip: str, request: Request, current_user: dict = Depends(require_super_admin)):
    """IP ban'ı kaldır (fail-closed denetimli)."""
    _before = await db.ip_blocklist.find_one({"ip": ip}, {"_id": 0, "blocked_until": 1, "permanent": 1,
                                                         "auto_blocked": 1})
    _out = {}

    async def _perform():
        _out["res"] = await db.ip_blocklist.delete_one({"ip": ip})
    await run_audited(db, action="security.ip_unblock", entity_type="ip", entity_id=ip, perform=_perform,
                      before=_before or {}, after={}, current_user=current_user, request=request,
                      source="admin.security")
    res = _out["res"]
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


# ---------------------------------------------------------------------------
# DENETİM ZİNCİRİ (tamper-evident) + ARŞİV
# ---------------------------------------------------------------------------

@router.get("/audit-chain")
async def audit_chain_status(current_user: dict = Depends(require_super_admin)):
    """Zincir başı, son doğrulama sonucu, son arşiv (R2) aktarım zamanı."""
    from security.audit_chain import chain_status
    return await chain_status(db)


@router.post("/audit-chain/verify")
async def audit_chain_verify(request: Request, payload: dict = None,
                             current_user: dict = Depends(require_super_admin)):
    """Body: {full?: bool, archive_key?: str}. full=True tüm zinciri baştan doğrular;
    archive_key verilirse o arşiv nesnesi hem kendi içinde hem DB ile karşılaştırılır."""
    from activity_audit import verify_audit_chain, record_admin_audit
    from security.audit_chain import verify_archive_object
    payload = payload or {}
    res = await verify_audit_chain(full=bool(payload.get("full")))
    out = {"chain": res}
    key = str(payload.get("archive_key") or "").strip()
    if key:
        out["archive"] = await verify_archive_object(db, key)
        if not out["archive"].get("ok") and out["archive"].get("problems"):
            try:
                from security.events import security_event
                await security_event("audit_chain_broken", current_user,
                                     {"archive_key": key, "problems": len(out["archive"]["problems"]),
                                      "fp": key}, request=request, audit=False)
            except Exception:
                pass
    await record_admin_audit(db, action="security.audit_chain_verify", entity_type="audit_chain",
                             entity_id="audit_chain", current_user=current_user, request=request,
                             source="admin.security",
                             metadata={"full": bool(payload.get("full")), "ok": bool(res.get("ok")),
                                       "archive_key": key[:120]})
    return out


@router.post("/audit-chain/export")
async def audit_chain_export_now(request: Request, current_user: dict = Depends(require_super_admin)):
    """Yeni denetim kayıtlarını hemen gizli arşive (R2) aktar (zamanlayıcı 15 dk'da bir yapar)."""
    from activity_audit import export_audit_to_archive
    return await export_audit_to_archive()


# ---------------------------------------------------------------------------
# ACİL DURUM: TÜM OTURUMLARI SONLANDIR (security epoch)
# ---------------------------------------------------------------------------

@router.post("/revoke-all-sessions")
async def revoke_all_sessions(request: Request, current_user: dict = Depends(require_super_admin)):
    """TÜM kullanıcıların (personel + müşteri) oturumlarını anında geçersiz kılar. Acil durum
    işlemi olduğu için iki-kişi onayı İSTEMEZ; fail-closed denetlenir ve süper adminlere uyarı
    gider. Çağıran süper admin için TAZE token döner (paneli açık kalır)."""
    from .deps import bump_security_epoch, create_token, token_auth_time
    out = {}

    async def _perform():
        out["epoch"] = await bump_security_epoch()
    await run_audited(db, action="security.revoke_all_sessions", entity_type="security_epoch",
                      entity_id="security_epoch", perform=_perform, current_user=current_user,
                      request=request, source="admin.security")
    try:
        from security.events import security_event
        await security_event("sessions_revoked_all", current_user, {"epoch": out.get("epoch"), "fp": out.get("epoch")},
                             request=request, audit=False)
    except Exception:
        pass
    _hdr = request.headers.get("authorization") or ""
    _raw = _hdr.split(" ", 1)[1].strip() if _hdr.lower().startswith("bearer ") else ""
    fresh = create_token(current_user["id"], True, token_version=int(current_user.get("token_version") or 0),
                         auth_time=token_auth_time(_raw) or None)
    return {"success": True, "epoch": out.get("epoch"), "token": fresh,
            "message": "Tüm oturumlar sonlandırıldı. Herkes yeniden giriş yapmalı."}


# ---------------------------------------------------------------------------
# UYARI TESLİMAT TESTİ
# ---------------------------------------------------------------------------

@router.post("/alerts/test")
async def alerts_test(request: Request, current_user: dict = Depends(require_super_admin)):
    """Mevcut varsayılan alıcılara (aktif süper adminler → ALERT_TO_EMAIL → firma iletişim e-postası)
    test uyarısı gönderir; alıcı başına teslim sonucunu döndürür (Resend alan adı doğrulaması vb.)."""
    from security.alerts import send_test_alert
    from activity_audit import record_admin_audit
    res = await send_test_alert(current_user)
    await record_admin_audit(db, action="security.alerts_test", entity_type="alerts", entity_id="test",
                             current_user=current_user, request=request, source="admin.security",
                             metadata={"recipients": len(res.get("recipients") or []),
                                       "ok": sum(1 for r in (res.get("recipients") or []) if r.get("ok"))})
    return res
