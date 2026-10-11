"""
Multi-channel alert dispatcher.

Channels:
  - Email (SMTP) — if ALERT_SMTP_HOST/USER/PASSWORD are set
  - Resend API — if RESEND_API_KEY is set (used when SMTP is missing or fails)
  - In-app  — always; persisted to MongoDB `alerts` collection so the
              admin dashboard can render unread alerts

Recipients (when the caller gives no ``to_email``), in order:
  1. İşletme Kuralı ``security.alert_emails`` (comma / newline separated) if non-empty
  2. active super admins' e-mails (users.is_super_admin=True, is_active≠False)
  3. env ALERT_TO_EMAIL (comma separated allowed)
  4. company contact e-mail (company.get_company → tenant config)
No address is hard-coded (white-label).

Sender (Resend): env RESEND_FROM → İşletme Kuralı ``security.alert_from`` →
"<StoreName> Güvenlik <guvenlik@<store domain>>" where the store domain comes from company
settings (storefront URL), falling back to the domain of the transactional sender
(settings.email_smtp.username). Company/sender values are cached for 5 minutes.

Throttling: each fingerprint is rate-limited to once per ALERT_THROTTLE_SECONDS
(per recipient) to prevent storms.

SOC-01 (security/alert_ack.py): every delivered WARNING/CRITICAL alert is stored with
``ack_required=True`` and each recipient's e-mail carries a signed, expiring, single-use
"Gördüm / İnceliyorum" link; unacknowledged CRITICAL alerts are escalated once ("[YANIT YOK]")
by the security_alert_escalation job.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

import httpx

from routes.deps import db, generate_id

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("ALERT_SMTP_HOST")
SMTP_PORT = int(os.environ.get("ALERT_SMTP_PORT") or "587")
SMTP_USER = os.environ.get("ALERT_SMTP_USER")
SMTP_PASS = os.environ.get("ALERT_SMTP_PASSWORD")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
THROTTLE_SECONDS = int(os.environ.get("ALERT_THROTTLE_SECONDS") or "300")  # 5 min default

LEVELS = {"info", "warning", "critical"}

_CACHE_TTL = 300.0
_cache: dict = {"at": 0.0, "company": None, "smtp_user": ""}


def _env_alert_to() -> list:
    return [e.strip() for e in (os.environ.get("ALERT_TO_EMAIL") or "").split(",") if e.strip() and "@" in e]


async def _company_cached() -> dict:
    now = time.monotonic()
    if _cache["company"] is not None and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["company"]
    comp = {}
    try:
        from company import get_company
        comp = await get_company(db) or {}
    except Exception as e:
        logger.warning("alerts: company settings unavailable: %s", type(e).__name__)
    smtp_user = ""
    try:
        cfg = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0, "username": 1}) or {}
        smtp_user = str(cfg.get("username") or "").strip()
    except Exception:
        pass
    _cache.update({"at": now, "company": comp, "smtp_user": smtp_user})
    return comp


def _store_domain(comp: dict) -> str:
    site = str((comp or {}).get("website") or (comp or {}).get("site_url") or "").strip().lower()
    site = site.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    if site.startswith("www."):
        site = site[4:]
    if site and "." in site:
        return site
    su = _cache.get("smtp_user") or ""
    if "@" in su:
        return su.split("@", 1)[1].strip().lower()
    return ""


def _split_emails(raw) -> list:
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or "").replace(";", ",").replace("\n", ",").split(",")
    out = []
    for e in items:
        e = e.strip().lower()
        if e and "@" in e and " " not in e and e not in out:
            out.append(e)
    return out[:25]


async def _rule(key: str, default=""):
    try:
        from business_rules import get_rule
        return await get_rule(db, key, default)
    except Exception:
        return default


async def resolve_sender() -> str:
    env_from = (os.environ.get("RESEND_FROM") or "").strip()
    if env_from:
        return env_from
    rule_from = str(await _rule("security.alert_from", "") or "").strip()
    if rule_from and "@" in rule_from:
        if "<" in rule_from:
            return rule_from
        comp = await _company_cached()
        name = str((comp or {}).get("store_name") or "").replace("<", "").replace(">", "").strip()
        return f"{name} Güvenlik <{rule_from}>" if name else rule_from
    comp = await _company_cached()
    dom = _store_domain(comp)
    name = str((comp or {}).get("store_name") or "Mağaza").replace("<", "").replace(">", "").strip()
    if dom:
        return f"{name} Güvenlik <guvenlik@{dom}>"
    smtp_from = (os.environ.get("ALERT_SMTP_FROM") or SMTP_USER or "").strip()
    return smtp_from


async def resolve_default_recipients() -> list:
    out: list = _split_emails(await _rule("security.alert_emails", ""))
    if out:
        return out
    try:
        async for u in db.users.find({"is_super_admin": True, "is_active": {"$ne": False}},
                                     {"_id": 0, "email": 1}).limit(25):
            e = str(u.get("email") or "").strip().lower()
            if e and e not in out:
                out.append(e)
    except Exception as e:
        logger.warning("alerts: super admin lookup failed: %s", type(e).__name__)
    if out:
        return out
    out = [e.lower() for e in _env_alert_to()]
    if out:
        return out
    comp = await _company_cached()
    ce = str((comp or {}).get("contact_email") or "").strip().lower()
    return [ce] if ce and "@" in ce else []


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
                       fingerprint: str, meta: dict, extra: Optional[dict] = None) -> str:
    aid = generate_id()
    doc = {
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
    }
    if extra:
        doc.update(extra)
    await db.alerts.insert_one(doc)
    return aid


def _send_smtp_blocking(subject: str, body: str, to_email: str) -> tuple:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        return False, "smtp_not_configured"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("ALERT_SMTP_FROM") or SMTP_USER
        msg["To"] = to_email
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=ctx)
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True, ""
    except Exception as e:
        logger.warning(f"SMTP alert failed: {type(e).__name__}")
        return False, f"smtp_error:{type(e).__name__}"


async def _send_resend(subject: str, body: str, to_email: str) -> tuple:
    if not RESEND_API_KEY:
        return False, "resend_not_configured"
    sender = await resolve_sender()
    if not sender:
        logger.error("Resend alert NOT sent: no sender (set RESEND_FROM or the storefront URL in company settings)")
        return False, "resend_no_sender"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": sender, "to": [to_email], "subject": subject, "text": body},
            )
            if r.status_code in (200, 201):
                return True, ""
            if 400 <= r.status_code < 500:
                # Çoğunlukla: gönderen alan adı Resend'de doğrulanmamış / anahtar geçersiz.
                logger.error("Resend alert REJECTED (HTTP %s) sender_domain=%s: %s — Resend panelinde gönderen "
                             "alan adını doğrulayın ya da RESEND_FROM'u doğrulanmış bir adrese ayarlayın.",
                             r.status_code, sender.split("@")[-1].rstrip(">"), r.text[:200])
            else:
                logger.warning(f"Resend alert failed: {r.status_code} {r.text[:200]}")
            _msg = ""
            try:
                _msg = str((r.json() or {}).get("message") or "")[:120]
            except Exception:
                _msg = r.text[:120]
            return False, f"resend_http_{r.status_code}:{_msg}" if _msg else f"resend_http_{r.status_code}"
    except Exception as e:
        logger.warning(f"Resend exception: {type(e).__name__}")
        return False, f"resend_error:{type(e).__name__}"


async def _send_store_mail(subject: str, text: str, target: str) -> tuple:
    """SON ÇARE: mağazanın sipariş/üyelik e-postalarını gönderen işlemsel mail altyapısı
    (email_smtp.send_smtp_email — ayarlardan). Resend alan adı doğrulanmamış ya da SMTP yoksa
    güvenlik uyarıları yine de ulaşsın."""
    try:
        import html as _html
        from email_smtp import send_smtp_email
        body = ('<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">'
                f'<pre style="white-space:pre-wrap;font-family:inherit">{_html.escape(text)}</pre></div>')
        res = await send_smtp_email(db, target, subject, body, text=text)
        if (res or {}).get("success"):
            return True, ""
        return False, f"store_mail:{str((res or {}).get('response'))[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"store_mail:{type(e).__name__}"


async def _deliver(subject: str, text: str, target: str) -> dict:
    smtp_ok, smtp_err = await asyncio.to_thread(_send_smtp_blocking, subject, text, target)
    resend_ok, resend_err = False, ""
    if not smtp_ok:
        resend_ok, resend_err = await _send_resend(subject, text, target)
    store_ok, store_err = False, ""
    if not (smtp_ok or resend_ok):
        store_ok, store_err = await _send_store_mail(subject, text, target)
    ok = bool(smtp_ok or resend_ok or store_ok)
    err = "" if ok else ";".join(x for x in (smtp_err, resend_err, store_err) if x)
    # Resend başarısız olup yedek kanal ulaştıysa nedeni yine kaydedilsin (teşhis için).
    warn = resend_err if (store_ok and resend_err) else ""
    return {"to": target, "smtp": smtp_ok, "resend": resend_ok, "store": store_ok, "ok": ok,
            "error": err, "warning": warn}


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
    within THROTTLE_SECONDS). Without ``to_email`` the default recipients are used
    (active super admins → ALERT_TO_EMAIL → company contact e-mail).
    Returns {alert_id, throttled, delivered:{smtp,resend}, recipients:[{to,ok,smtp,resend,error}]}.
    """
    if level not in LEVELS:
        level = "warning"
    fp = fingerprint or f"{kind}:{title[:80]}"
    # KVKK: güvenlik uyarı e-postası / kaydı müşteri/influencer telefonu ya da adresi TAŞIMAZ
    # (çağıran yanlışlıkla koysa bile tek noktada maskelenir).
    try:
        from security.contact_pii import scrub_contact_text as _sct, scrub_contact as _sc
        title = _sct(str(title or ""))
        body = _sct(str(body or ""))
        meta = _sc(meta or {})
    except Exception:  # noqa: BLE001 — süzgeç hatası uyarıyı DURDURMAZ
        pass

    throttled = await _was_recently_sent(fp)
    targets: list = []
    extra: dict = {}
    if not throttled:
        # Çağıran virgüllü liste verebilir ("a@x, b@y" — ör. stok uyarısı): her adres AYRI alıcı.
        # Tek dize olarak gönderilince Resend/ZeptoMail reddediyor, uyarı "teslim edilemedi"
        # sayılıyor ve güvenlik hattı sahte "kesinti" alarmı veriyordu. Geçerli adres yoksa varsayılan.
        targets = (_split_emails(to_email) if to_email else []) or await resolve_default_recipients()
        # SOC-01: uyarı/kritik uyarı insan teyidi bekler (imzalı "Gördüm" bağlantısı + panelde Onayla).
        if level in ("warning", "critical"):
            try:
                from security.alert_ack import new_ack_fields
                extra = new_ack_fields(targets)
            except Exception as e:  # noqa: BLE001 — onay alanı kurulamasa da uyarı GİTMELİ
                logger.warning("alert ack fields unavailable: %s", type(e).__name__)
    aid = await _store_alert(kind, level, title, body, fp, meta or {}, extra)

    if throttled:
        return {"alert_id": aid, "throttled": True, "delivered": {"smtp": False, "resend": False},
                "recipients": []}

    comp = await _company_cached()
    store = str((comp or {}).get("store_name") or "").strip()
    subject = f"[{store + ' ' if store else ''}{level.upper()}] {title}"[:200]
    text = (
        f"{title}\n\n"
        f"Level: {level}\nKind: {kind}\nTime: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"{body}\n\n"
        f"Meta: {meta or {}}\n"
    )
    results = []
    for i, t in enumerate(targets):
        t_text = text
        if extra.get("ack_required"):
            try:
                from security.alert_ack import ack_instructions
                t_text = text + await ack_instructions(aid, i, extra.get("ack_nonce") or "", level)
            except Exception as e:  # noqa: BLE001
                logger.warning("alert ack link unavailable: %s", type(e).__name__)
        results.append(await _deliver(subject, t_text, t))
    smtp_ok = any(r["smtp"] for r in results)
    resend_ok = any(r["resend"] for r in results)
    store_ok = any(r.get("store") for r in results)
    upd = {"subject": subject,
           "delivered.smtp": smtp_ok, "delivered.resend": resend_ok, "delivered.store": store_ok,
           "delivery": [{"ok": r["ok"], "error": r["error"], "warning": r.get("warning", "")} for r in results]}
    if not targets:
        upd["delivery_error"] = "no_recipients"
        logger.error("alert %s: no recipients (no active super admin, ALERT_TO_EMAIL or company e-mail)", kind)
    elif not (smtp_ok or resend_ok or store_ok):
        upd["delivery_error"] = ";".join(sorted({r["error"] for r in results if r["error"]}))[:300]
    try:
        await db.alerts.update_one({"id": aid}, {"$set": upd})
    except Exception:
        pass
    # Kritik uyarıda ANINDA SMS (İşletme Kuralları: security.alert_sms_phones / security.alert_sms_instant).
    if level == "critical":
        try:
            from security.alert_sms import instant_critical_sms
            await instant_critical_sms(aid, kind, title)
        except Exception as e:  # noqa: BLE001 — SMS olmasa da e-posta gitti
            logger.warning("instant critical sms failed: %s", type(e).__name__)
    return {"alert_id": aid, "throttled": False,
            "delivered": {"smtp": smtp_ok, "resend": resend_ok, "store": store_ok},
            "recipients": [{"to": r["to"], "ok": r["ok"], "smtp": r["smtp"], "resend": r["resend"],
                            "store": r.get("store", False), "error": r["error"],
                            "warning": r.get("warning", "")} for r in results]}


async def send_test_alert(actor: Optional[dict] = None) -> dict:
    """Super-admin test: sends a test alert to the current default recipients (unthrottled)."""
    recipients = await resolve_default_recipients()
    sender = await resolve_sender() if RESEND_API_KEY else (os.environ.get("ALERT_SMTP_FROM") or SMTP_USER or "")
    res = await send_alert(
        "manual_test", "Güvenlik uyarısı testi",
        f"Bu bir test uyarısıdır. Gönderen: {sender or '-'}\n"
        f"Tetikleyen: {(actor or {}).get('email') or '-'}",
        level="info", fingerprint=f"manual_test:{generate_id()}")
    return {"sender": sender, "channels": {"smtp": bool(SMTP_HOST and SMTP_USER and SMTP_PASS),
                                           "resend": bool(RESEND_API_KEY)},
            "recipients": res.get("recipients") or [], "default_recipient_count": len(recipients),
            "alert_id": res.get("alert_id")}
