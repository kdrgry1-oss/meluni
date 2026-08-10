"""
Cloudflare Email Routing -> gelen mail webhook alıcısı (/api/inbound-mail)
+ Resend ile mail gönderme yardımcısı ve test ucu (/api/send-test).
"""
import os
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Header, HTTPException

from routes.deps import db

router = APIRouter()

WEBHOOK_SECRET = os.environ.get("MAIL_WEBHOOK_SECRET", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "Facette <info@facette.com.tr>")


# --- GELEN MAIL (Cloudflare Worker buraya POST eder) ---
@router.post("/inbound-mail")
async def inbound_mail(request: Request, x_webhook_secret: str | None = Header(default=None)):
    if not WEBHOOK_SECRET or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")

    p = await request.json()
    doc = {
        "message_id": p.get("messageId"),
        "from_address": p.get("from"),
        "from_name": p.get("fromName"),
        "to_address": p.get("to"),
        "subject": p.get("subject"),
        "text": p.get("text"),
        "html": p.get("html"),
        "attachments": p.get("attachments", []),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }
    if doc["message_id"]:
        await db.inbound_mails.update_one(
            {"message_id": doc["message_id"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
    else:
        await db.inbound_mails.insert_one(doc)
    return {"ok": True}


# --- MAIL GÖNDERME (Resend HTTPS API — SMTP yok, Railway'de çalışır) ---
async def send_email(to: str, subject: str, html: str,
                     text: str | None = None, reply_to: str | None = None):
    """Uygulamanın herhangi bir yerinden mail göndermek için bunu çağır."""
    from email_smtp import send_smtp_email
    res = await send_smtp_email(db, to, subject, html, reply_to=reply_to, text=text)
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=f"SMTP hata: {res.get('response')}")
    return {"ok": True, "id": "smtp"}


@router.post("/send-test")
async def send_test(request: Request, x_webhook_secret: str | None = Header(default=None)):
    if not WEBHOOK_SECRET or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")
    body = await request.json()
    to = body.get("to")
    if not to:
        raise HTTPException(status_code=400, detail="'to' alanı gerekli")
    subject = body.get("subject", "Facette test maili")
    html = body.get("html", "<p>Bu bir test mailidir. Resend + Railway çalışıyor 🎉</p>")
    result = await send_email(to=to, subject=subject, html=html)
    return {"ok": True, "id": result.get("id")}
