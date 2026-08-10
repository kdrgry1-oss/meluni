"""
Cloudflare Email Routing -> gelen mail webhook alıcısı.
info@facettedijital.com'a gelen mailler buraya düşer, db.inbound_mails'e yazılır.
"""
import os
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Header, HTTPException

from routes.deps import db

router = APIRouter()

WEBHOOK_SECRET = os.environ.get("MAIL_WEBHOOK_SECRET", "")


@router.post("/inbound-mail")
async def inbound_mail(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
):
    # Sadece Cloudflare Worker'dan gelen, doğru secret'lı istekleri kabul et
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

    # Aynı mail iki kez gelse de mükerrer kayıt olmaz (message_id'ye göre)
    if doc["message_id"]:
        await db.inbound_mails.update_one(
            {"message_id": doc["message_id"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
    else:
        await db.inbound_mails.insert_one(doc)

    return {"ok": True}
