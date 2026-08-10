"""
Multi-channel alert dispatcher.

Channels:
  - Email (SMTP) — primary; recipient: ALERT_TO_EMAIL env var
  - Resend API — fallback if RESEND_API_KEY is set and SMTP missing
  - In-app  — always; persisted to MongoDB `alerts` collection so the
              admin dashboard can render unread alerts

Throttling: each (kind, fingerprint) is rate-limited to once per
ALERT_THROTTLE_SECONDS to prevent storms.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

import httpx

from routes.deps import db, generate_id

logger = logging.getLogger(__name__)

ALERT_TO_EMAIL = os.environ.get("ALERT_TO_EMAIL") or "kdrgry@gmail.com"
SMTP_HOST = os.environ.get("ALERT_SMTP_HOST")
SMTP_PORT = int(os.environ.get("ALERT_SMTP_PORT") or "587")
SMTP_USER = os.environ.get("ALERT_SMTP_USER")
SMTP_PASS = os.environ.get("ALERT_SMTP_PASSWORD")
SMTP_FROM = os.environ.get("ALERT_SMTP_FROM") or SMTP_USER or "alerts@facette.com"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM = os.environ.get("RESEND_FROM") or SMTP_FROM
THROTTLE_SECONDS = int(os.environ.get("ALERT_THROTTLE_SECONDS") or "300")  # 5 min default

LEVELS = {"info", "warning", "critical"}


async def _was_recently_sent(fingerprint: str) -> bool:
    if not fingerprint:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=THROTTLE_SECONDS)).isoformat()
    doc = await db.alerts.find_one(
        {"fingerprint": fingerprint, "created_at": {"$gte": cutoff}},
        {"_id": 1},
    )
    return doc is not None


async def _store_alert(kind: str, level: str, title: str, body: str,
                       fingerprint: str, meta: dict) -> str:
    aid = generate_id()
    await db.alerts.insert_one({
        "id": aid,
        "kind": kind,
        "level": level,
        "title": title[:300],
        "body": body[:8000],
        "fingerprint": fingerprint or "",
        "meta": meta or {},
        "delivered": {"smtp": False, "resend": False, "in_app": True},
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return aid


def _send_smtp_blocking(subject: str, body: str, to_email: str) -> bool:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=ctx)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"SMTP alert failed: {e}")
        return False


async def _send_resend(subject: str, body: str, to_email: str) -> bool:
    if not RESEND_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": RESEND_FROM,
                    "to": [to_email],
                    "subject": subject,
                    "text": body,
                },
            )
            if r.status_code in (200, 201):
                return True
            logger.warning(f"Resend alert failed: {r.status_code} {r.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Resend exception: {e}")
        return False


async def send_alert(
    kind: str,
    title: str,
    body: str = "",
    *,
    level: str = "warning",
    fingerprint: Optional[str] = None,
    meta: Optional[dict] = None,
    to_email: Optional[str] = None,
) -> dict:
    """Single entry point for sending alerts.

    `kind` examples: 'error_spike', 'integration_down', 'auth_brute_force',
                     'db_slow', 'manual_test'.
    `fingerprint` enables throttling (same fingerprint won't email twice
    within THROTTLE_SECONDS).
    """
    if level not in LEVELS:
        level = "warning"
    fp = fingerprint or f"{kind}:{title[:80]}"

    throttled = await _was_recently_sent(fp)
    aid = await _store_alert(kind, level, title, body, fp, meta or {})

    if throttled:
        return {"alert_id": aid, "throttled": True, "delivered": {"smtp": False, "resend": False}}

    target = to_email or ALERT_TO_EMAIL
    subject = f"[Facette {level.upper()}] {title}"[:200]
    text = (
        f"{title}\n\n"
        f"Level: {level}\nKind: {kind}\nTime: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"{body}\n\n"
        f"Meta: {meta or {}}\n"
    )

    # Try SMTP first (in thread to avoid blocking the loop)
    smtp_ok = await asyncio.to_thread(_send_smtp_blocking, subject, text, target)
    resend_ok = False
    if not smtp_ok:
        resend_ok = await _send_resend(subject, text, target)

    await db.alerts.update_one(
        {"id": aid},
        {"$set": {"delivered.smtp": smtp_ok, "delivered.resend": resend_ok}},
    )
    return {"alert_id": aid, "throttled": False,
            "delivered": {"smtp": smtp_ok, "resend": resend_ok}}
