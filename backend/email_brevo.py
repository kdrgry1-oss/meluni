"""Brevo e-posta pazarlama istemcisi.

Toplu kampanyalar Brevo'nun Marketing Campaigns API'siyle gönderilir. Test
e-postası için Brevo'nun tek-alıcı SMTP/transactional HTTPS API'si kullanılır.
API anahtarı ``settings.id=email_brevo`` belgesinde şifreli saklanır.
"""
from __future__ import annotations

from typing import Any

import httpx


API_BASE = "https://api.brevo.com/v3"


async def get_brevo_config(db) -> dict:
    cfg = await db.settings.find_one({"id": "email_brevo"}, {"_id": 0}) or {}
    for field in ("api_key", "webhook_secret"):
        if cfg.get(field):
            try:
                from security.crypto import decrypt
                cfg = {**cfg, field: decrypt(cfg[field])}
            except Exception:
                cfg = {**cfg, field: None}
    return cfg


def is_configured(cfg: dict) -> bool:
    try:
        list_id = int(cfg.get("list_id") or 0)
    except (TypeError, ValueError):
        list_id = 0
    return bool(
        cfg.get("enabled") and cfg.get("api_key") and cfg.get("from_email") and list_id > 0
    )


async def _request(cfg: dict, method: str, path: str, *, json: Any = None,
                   params: dict | None = None, timeout: float = 30) -> dict:
    headers = {
        "api-key": str(cfg.get("api_key") or "").strip(),
        "accept": "application/json",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, API_BASE + path, headers=headers, json=json, params=params)
        if response.status_code not in (200, 201, 202, 204):
            detail = response.text or f"HTTP {response.status_code}"
            return {"success": False, "status": response.status_code, "error": detail[:500]}
        data = response.json() if response.content else {}
        return {"success": True, "status": response.status_code, "data": data}
    except Exception as exc:
        return {"success": False, "status": 0, "error": str(exc)[:500]}


async def validate_config(cfg: dict) -> dict:
    """API anahtarını hesap uç noktasında doğrular; sır saklanmadan önce çağrılabilir."""
    return await _request(cfg, "GET", "/account")


async def send_test_email(cfg: dict, to: str, subject: str, html: str,
                          reply_to: str = "", tags: list | None = None) -> dict:
    payload: dict[str, Any] = {
        "sender": {"name": cfg.get("from_name") or "Meluni", "email": cfg.get("from_email")},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
        "tags": list(tags or ["meluni-email-marketing-test"]),
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    result = await _request(cfg, "POST", "/smtp/email", json=payload)
    if result.get("success"):
        result["message_id"] = (result.get("data") or {}).get("messageId", "")
    return result


async def _list_contacts(cfg: dict) -> set[str] | None:
    """Pazarlama listesindeki mevcut adresleri getirir; hata halinde None döner."""
    list_id = int(cfg["list_id"])
    emails: set[str] = set()
    offset = 0
    while True:
        result = await _request(
            cfg, "GET", f"/contacts/lists/{list_id}/contacts",
            params={"limit": 500, "offset": offset, "sort": "asc"},
        )
        if not result.get("success"):
            return None
        rows = (result.get("data") or {}).get("contacts") or []
        for row in rows:
            email = str(row.get("email") or "").strip().lower()
            if email:
                emails.add(email)
        if len(rows) < 500:
            break
        offset += len(rows)
    return emails


async def sync_contacts(cfg: dict, contacts: list[dict]) -> dict:
    """Yerel rızalı kitleyi Brevo listesinin birebir güncel hali yapar.

    Brevo'da daha önce abonelikten çıkan bir kişiyi yeniden aktifleştirmez;
    yalnız liste üyeliği ve ad alanları güncellenir. Yerel kitleden çıkan eski
    adresler listeden kaldırılır ki sonraki kampanyaya yanlışlıkla dahil olmasın.
    """
    list_id = int(cfg["list_id"])
    wanted: dict[str, dict] = {}
    for item in contacts:
        email = str(item.get("email") or "").strip().lower()
        if not email:
            continue
        wanted[email] = item

    current = await _list_contacts(cfg)
    if current is None:
        return {"success": False, "error": "Brevo kişi listesi okunamadı; kampanya güvenlik için gönderilmedi."}

    removed = 0
    stale = sorted(current - set(wanted))
    for start in range(0, len(stale), 150):
        result = await _request(
            cfg, "POST", f"/contacts/lists/{list_id}/contacts/remove",
            json={"emails": stale[start:start + 150]},
        )
        if not result.get("success"):
            return {"success": False, "error": f"Eski Brevo alıcıları listeden çıkarılamadı: {result.get('error', '')}"}
        removed += len(stale[start:start + 150])

    synced = 0
    for email, item in wanted.items():
        full_name = str(item.get("name") or item.get("first_name") or item.get("full_name") or "").strip()
        first_name = full_name.split()[0] if full_name else ""
        payload: dict[str, Any] = {
            "email": email,
            "listIds": [list_id],
            "updateEnabled": True,
        }
        if first_name:
            payload["attributes"] = {"FIRSTNAME": first_name}
        result = await _request(cfg, "POST", "/contacts", json=payload)
        if not result.get("success"):
            return {"success": False, "error": f"{email} Brevo'ya eşitlenemedi: {result.get('error', '')}"}
        synced += 1
    return {"success": True, "synced": synced, "removed": removed}


async def create_and_send_campaign(cfg: dict, *, name: str, subject: str,
                                   html: str, tag: str = "") -> dict:
    payload: dict[str, Any] = {
        "name": name,
        "sender": {"name": cfg.get("from_name") or "Meluni", "email": cfg.get("from_email")},
        "subject": subject,
        "htmlContent": html,
        "recipients": {"listIds": [int(cfg["list_id"])]},
        "mirrorActive": True,
        "tag": tag or "meluni-email-marketing",
    }
    if cfg.get("reply_to"):
        payload["replyTo"] = cfg["reply_to"]
    created = await _request(cfg, "POST", "/emailCampaigns", json=payload)
    if not created.get("success"):
        return created
    campaign_id = (created.get("data") or {}).get("id")
    if not campaign_id:
        return {"success": False, "error": "Brevo kampanya kimliği döndürmedi."}
    sent = await _request(cfg, "POST", f"/emailCampaigns/{campaign_id}/sendNow", json={})
    if not sent.get("success"):
        return {**sent, "campaign_id": campaign_id}
    return {"success": True, "campaign_id": campaign_id}
