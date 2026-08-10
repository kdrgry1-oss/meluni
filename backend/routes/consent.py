"""
consent.py — Çerez onayı kayıt ucu (KVKK/GDPR kanıt/hesap verebilirlik).
=====================================================================
Ziyaretçinin çerez tercihini (kabul/ret/özel) SUNUCUDA denetlenebilir biçimde
saklar. Önceden yalnız localStorage'da tutuluyordu → "kim, ne zaman, neye rıza
gösterdi" kaydı yoktu (KVKK kayıt yükümlülüğü boşluğu). Public uç (anonim ziyaretçi).
"""
from fastapi import APIRouter, Request
from datetime import datetime, timezone

from .deps import db, logger, client_ip_from_request

router = APIRouter(tags=["consent"])


@router.post("/consent/log")
async def log_cookie_consent(payload: dict, request: Request):
    """Çerez onayı kararını kalıcı kaydet. Best-effort — hata sipariş/site akışını bozmaz."""
    try:
        p = payload if isinstance(payload, dict) else {}
        doc = {
            "necessary": True,  # zorunlu çerezler her zaman
            "analytics": bool(p.get("analytics")),
            "marketing": bool(p.get("marketing")),
            "decision": str(p.get("decision") or p.get("action") or "")[:32],  # accept_all/reject/custom
            "path": str(p.get("path") or "")[:256],
            "ua": str(p.get("ua") or "")[:512],
            "ip": client_ip_from_request(request),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.cookie_consents.insert_one(doc)
        return {"success": True}
    except Exception as e:
        logger.warning(f"consent log failed: {e}")
        return {"success": False}
