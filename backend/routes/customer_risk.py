"""
=============================================================================
customer_risk.py — Müşteri risk skoru + blok yönetimi (FAZ 6)
=============================================================================

Endpoint'ler:
  GET  /api/customer-risk/users/{user_id}         → iade oranı, sipariş adedi, risk level
  GET  /api/customer-risk/by-email?email=         → aynı bilgiyi e-posta ile (pazaryeri siparişlerinde user_id yok)
  GET  /api/customer-risk/bulk?user_ids=a,b,c     → toplu risk skoru (Orders listesi için)
  POST /api/customer-risk/block                   → kullanıcı/IP bloklama
  GET  /api/customer-risk/blocked                 → bloklu liste
  DELETE /api/customer-risk/blocked/{bid}         → blok kaldır

Formül:
  return_rate = iade_edilen_sipariş / tamamlanmış_sipariş (cancelled hariç)
  risk_level:
    low       → < 20%
    medium    → 20–49%
    high      → ≥ 50% (şüpheli — UI'da kırmızı)

Auto-block opsiyonu: POST /block endpoint'ine auto=true ile çağırarak kural ekle.
=============================================================================
"""
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .deps import db, require_admin, generate_id

router = APIRouter(prefix="/customer-risk", tags=["customer-risk"])


async def _compute_risk(user_id: Optional[str] = None, email: Optional[str] = None) -> dict:
    q = {}
    if user_id:
        q["user_id"] = user_id
    elif email:
        # Sipariş shipping_address.email veya root email alanında tutulabilir
        q["$or"] = [
            {"shipping_address.email": email},
            {"email": email},
        ]
    else:
        return {"total_orders": 0, "returned": 0, "return_rate": 0.0, "return_rate_pct": 0.0, "risk_level": "low", "suspicious": False}

    total_orders = await db.orders.count_documents({**q, "status": {"$ne": "cancelled"}})
    if total_orders == 0:
        return {"total_orders": 0, "returned": 0, "return_rate": 0.0, "return_rate_pct": 0.0, "risk_level": "low", "suspicious": False}

    # İade sayısı — `returns` koleksiyonu varsa oradan, yoksa orders.has_return bayrağı
    try:
        if user_id:
            returned = await db.customer_returns.count_documents({"user_id": user_id, "status": {"$ne": "rejected"}})
        elif email:
            returned = await db.customer_returns.count_documents({"customer_email": email, "status": {"$ne": "rejected"}})
        else:
            returned = 0
    except Exception:
        returned = 0

    # Fallback: orders.has_return alanı (eski kayıtlar için)
    if returned == 0:
        try:
            returned = await db.orders.count_documents({**q, "has_return": True})
        except Exception:
            returned = 0

    rate = (returned / total_orders) if total_orders else 0.0

    if rate >= 0.5:
        level = "high"
    elif rate >= 0.2:
        level = "medium"
    else:
        level = "low"

    return {
        "total_orders": total_orders,
        "returned": returned,
        "return_rate": round(rate, 4),
        "return_rate_pct": round(rate * 100, 1),
        "risk_level": level,
        "suspicious": (level == "high"),
    }


@router.get("/users/{user_id}")
async def risk_by_user(user_id: str, current_user: dict = Depends(require_admin)):
    return await _compute_risk(user_id=user_id)


@router.get("/by-email")
async def risk_by_email(email: str, current_user: dict = Depends(require_admin)):
    return await _compute_risk(email=email.lower().strip())


@router.get("/bulk")
async def risk_bulk(user_ids: str = Query("", description="virgülle ayrılmış user_id listesi"),
                    emails: str = Query("", description="virgülle ayrılmış email listesi"),
                    current_user: dict = Depends(require_admin)):
    uids = [u.strip() for u in user_ids.split(",") if u.strip()]
    mails = [e.strip().lower() for e in emails.split(",") if e.strip()]
    out = {}
    for u in uids[:200]:
        out[u] = await _compute_risk(user_id=u)
    for m in mails[:200]:
        out[m] = await _compute_risk(email=m)
    return {"risks": out}


# -----------------------------------------------------------------------------
# Block management
# -----------------------------------------------------------------------------

class BlockReq(BaseModel):
    user_id: Optional[str] = None
    ip: Optional[str] = None
    email: Optional[str] = None
    reason: str = "Yüksek iade oranı"
    expires_at: Optional[str] = None  # ISO date; None → kalıcı


@router.post("/block")
async def block_customer(req: BlockReq, current_user: dict = Depends(require_admin)):
    if not any([req.user_id, req.ip, req.email]):
        raise HTTPException(status_code=400, detail="user_id / ip / email en az biri gerekli")
    doc = {
        "id": generate_id(),
        "user_id": req.user_id,
        "ip": req.ip,
        "email": (req.email or "").lower() or None,
        "reason": req.reason,
        "expires_at": req.expires_at,
        "active": True,
        "blocked_by": current_user.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.blocked_customers.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "block": doc}


@router.get("/blocked")
async def list_blocked(active_only: bool = True, current_user: dict = Depends(require_admin)):
    q = {"active": True} if active_only else {}
    rows = await db.blocked_customers.find(q, {"_id": 0}).sort("created_at", -1).to_list(length=500)
    return {"items": rows, "total": len(rows)}


@router.delete("/blocked/{bid}")
async def unblock(bid: str, current_user: dict = Depends(require_admin)):
    res = await db.blocked_customers.update_one({"id": bid}, {"$set": {"active": False, "unblocked_at": datetime.now(timezone.utc).isoformat(), "unblocked_by": current_user.get("email", "")}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"success": True}
