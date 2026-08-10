"""
CAPI Orchestrator — dispatches events to all configured providers.
Reads active pixel configs from `marketing_pixels` (per tenant), decrypts
access tokens from Vault if needed, fans out the event to each provider
in parallel via asyncio.gather.

Failed providers go to `capi_event_queue` for retry.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List
from uuid import uuid4

from . import meta, tiktok, google_ads, pinterest, snapchat


logger = logging.getLogger(__name__)

PROVIDERS = {
    "meta": meta,
    "facebook": meta,         # alias
    "tiktok": tiktok,
    "google_ads": google_ads,
    "ga4": google_ads,        # GA4 + Ads use same MP endpoint
    "pinterest": pinterest,
    "snapchat": snapchat,
}

MAX_ATTEMPTS = 5
BACKOFF_MINUTES = [1, 5, 15, 60, 240]   # exponential backoff


async def _resolve_access_token(px: dict) -> Optional[str]:
    """Returns access token. Order: env override → vault → plain stored."""
    import os
    # Env override (per provider, e.g. META_CAPI_TOKEN)
    env_key = (px.get("env_token_key") or "").strip()
    if env_key:
        v = os.environ.get(env_key)
        if v:
            return v
    # Vault reference
    vault_key = (px.get("vault_key") or "").strip()
    if vault_key:
        try:
            from routes.secrets_vault import get_secret as vault_get_value
            v = await vault_get_value(vault_key)
            if v:
                return v
        except Exception as e:
            logger.warning(f"Vault read failed for {vault_key}: {e}")
    # Plain text fallback (less secure)
    return (px.get("access_token") or "").strip() or None


async def _send_one(db, px: dict, *, event_name: str, event_id: str,
                    event_time: Optional[int], user_data: dict,
                    event_payload: dict, event_source_url: Optional[str]) -> dict:
    """Send to one provider; on failure, enqueue for retry."""
    provider_key = (px.get("provider") or "").lower()
    mod = PROVIDERS.get(provider_key)
    if not mod:
        return {"provider": provider_key, "ok": False, "skipped": True,
                "reason": "unknown provider"}

    if not px.get("capi_enabled"):
        return {"provider": provider_key, "ok": True, "skipped": True,
                "reason": "capi disabled"}

    pixel_id = (px.get("tag_id") or "").strip()
    access_token = await _resolve_access_token(px)
    test_event_code = (px.get("test_event_code") or "").strip() or None

    if not pixel_id or not access_token:
        return {"provider": provider_key, "ok": False, "skipped": True,
                "reason": "missing pixel_id or access_token"}

    # Meta + TikTok alan-bazlı feature flag geçir (rollback); diğer sağlayıcılar etkilenmez.
    _extra = {"field_flags": px.get("field_flags")} if provider_key in ("meta", "tiktok") else {}
    res = await mod.send(
        pixel_id=pixel_id,
        access_token=access_token,
        event_name=event_name,
        event_id=event_id,
        event_time=event_time,
        user_data=user_data,
        event_payload=event_payload,
        event_source_url=event_source_url,
        test_event_code=test_event_code,
        **_extra,
    )
    res["provider"] = provider_key
    res["pixel_doc_id"] = px.get("id")

    # Persist log — PII'siz eşleşme sinyalleri (brief §13 observability): hangi Purchase/event'te
    # email/telefon/ttclid/fbc vb. GİTTİ, panelden/loglardan görülebilsin (EMQ teşhisi).
    _ud = user_data or {}
    await db.capi_event_logs.insert_one({
        "id": str(uuid4()),
        "provider": provider_key,
        "pixel_doc_id": px.get("id"),
        "event_name": event_name,
        "event_id": event_id,
        "tenant_id": px.get("tenant_id"),
        "ok": bool(res.get("ok")),
        "status": res.get("status"),
        "response": (res.get("response") or {}),
        "error": res.get("error"),
        "match_signals": {
            "email": bool(_ud.get("em")), "phone": bool(_ud.get("ph")),
            "external_id": bool(_ud.get("external_id")),
            "fbp": bool(_ud.get("fbp")), "fbc": bool(_ud.get("fbc")),
            "ttclid": bool(_ud.get("ttclid")), "ttp": bool(_ud.get("ttp")),
            "ip": bool(_ud.get("client_ip_address")), "ua": bool(_ud.get("client_user_agent")),
            # ip_version: Meta IPv6/IPv4 uyuşmazlığı teşhisi (§ Meta plan). Ham IP YAZILMAZ, yalnız sürüm.
            "ip_version": (6 if ":" in (_ud.get("client_ip_address") or "")
                           else (4 if (_ud.get("client_ip_address") or "") else None)),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Enqueue on failure
    if not res.get("ok") and not res.get("skipped"):
        await db.capi_event_queue.insert_one({
            "id": str(uuid4()),
            "provider": provider_key,
            "pixel_doc_id": px.get("id"),
            "tenant_id": px.get("tenant_id"),
            "event_name": event_name,
            "event_id": event_id,
            "event_time": event_time,
            "user_data": user_data,
            "event_payload": event_payload,
            "event_source_url": event_source_url,
            "attempts": 0,
            "next_try_at": datetime.now(timezone.utc).isoformat(),
            "last_error": res.get("error"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return res


async def dispatch_event(
    db,
    *,
    event_name: str,                    # internal name (view_item, purchase, …)
    event_id: Optional[str] = None,     # for dedup; auto-generated if missing
    event_time: Optional[int] = None,
    user_data: dict,
    event_payload: dict,
    event_source_url: Optional[str] = None,
    tenant_id: Optional[str] = None,    # multi-tenant filter
    providers: Optional[List[str]] = None,  # filter providers (None = all active)
) -> dict:
    """Dispatch a single event to all matching active pixel configs in parallel."""
    if not event_id:
        event_id = str(uuid4())
    if not event_time:
        event_time = int(datetime.now(timezone.utc).timestamp())

    # Fetch active pixel configs
    query = {"is_active": True, "capi_enabled": True}
    if tenant_id:
        query["tenant_id"] = tenant_id
    if providers:
        query["provider"] = {"$in": [p.lower() for p in providers]}

    pixels = await db.marketing_pixels.find(query, {"_id": 0}).to_list(50)
    if not pixels:
        return {"ok": True, "event_id": event_id, "dispatched": 0, "results": []}

    coros = [_send_one(db, px,
                       event_name=event_name, event_id=event_id,
                       event_time=event_time, user_data=user_data,
                       event_payload=event_payload,
                       event_source_url=event_source_url) for px in pixels]
    results = await asyncio.gather(*coros, return_exceptions=True)
    safe_results = []
    for r in results:
        if isinstance(r, Exception):
            safe_results.append({"ok": False, "error": str(r)})
        else:
            safe_results.append(r)
    return {
        "ok": True,
        "event_id": event_id,
        "dispatched": len(safe_results),
        "results": safe_results,
    }


async def retry_queue_once(db, batch_size: int = 100) -> dict:
    """Process pending queue items. Called periodically by background loop."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cursor = db.capi_event_queue.find(
        {"next_try_at": {"$lte": now.isoformat()}, "attempts": {"$lt": MAX_ATTEMPTS}},
        {"_id": 0},
    ).limit(batch_size)
    items = await cursor.to_list(batch_size)
    processed, ok_count, fail_count = 0, 0, 0
    for it in items:
        processed += 1
        # Re-fetch latest pixel config (token might have rotated)
        px = await db.marketing_pixels.find_one({"id": it.get("pixel_doc_id")}, {"_id": 0})
        if not px or not px.get("is_active") or not px.get("capi_enabled"):
            await db.capi_event_queue.delete_one({"id": it["id"]})
            continue
        # Try sending again
        provider_key = (it.get("provider") or "").lower()
        mod = PROVIDERS.get(provider_key)
        if not mod:
            await db.capi_event_queue.delete_one({"id": it["id"]})
            continue
        access_token = await _resolve_access_token(px)
        if not access_token:
            # O4: Token yoksa eskiden attempts artmadan/next_try_at ilerlemeden `continue`
            # ediliyordu → bu öğe HER 30 dk'da SONSUZA dek yeniden işleniyordu. Artık başarısız
            # gönderim gibi attempts artırılır, backoff uygulanır ve MAX_ATTEMPTS'te dead-letter olur.
            fail_count += 1
            attempt = int(it.get("attempts") or 0) + 1
            if attempt >= MAX_ATTEMPTS:
                await db.capi_event_queue.update_one(
                    {"id": it["id"]},
                    {"$set": {"attempts": attempt, "dead": True,
                              "last_error": "access_token yok",
                              "updated_at": now.isoformat()}},
                )
            else:
                backoff = BACKOFF_MINUTES[min(attempt - 1, len(BACKOFF_MINUTES) - 1)]
                await db.capi_event_queue.update_one(
                    {"id": it["id"]},
                    {"$set": {"attempts": attempt, "last_error": "access_token yok",
                              "next_try_at": (now + timedelta(minutes=backoff)).isoformat(),
                              "updated_at": now.isoformat()}},
                )
            continue
        _extra = {"field_flags": px.get("field_flags")} if provider_key in ("meta", "tiktok") else {}
        res = await mod.send(
            pixel_id=(px.get("tag_id") or "").strip(),
            access_token=access_token,
            event_name=it.get("event_name"),
            event_id=it.get("event_id"),
            event_time=it.get("event_time"),
            user_data=it.get("user_data") or {},
            event_payload=it.get("event_payload") or {},
            event_source_url=it.get("event_source_url"),
            test_event_code=(px.get("test_event_code") or "").strip() or None,
            **_extra,
        )
        if res.get("ok"):
            ok_count += 1
            await db.capi_event_queue.delete_one({"id": it["id"]})
            await db.capi_event_logs.insert_one({
                "id": str(uuid4()),
                "provider": provider_key,
                "pixel_doc_id": px.get("id"),
                "event_name": it.get("event_name"),
                "event_id": it.get("event_id"),
                "tenant_id": px.get("tenant_id"),
                "ok": True, "status": res.get("status"),
                "response": res.get("response") or {},
                "from_retry": True,
                "created_at": now.isoformat(),
            })
        else:
            fail_count += 1
            attempt = int(it.get("attempts") or 0) + 1
            if attempt >= MAX_ATTEMPTS:
                await db.capi_event_queue.update_one(
                    {"id": it["id"]},
                    {"$set": {"attempts": attempt, "dead": True,
                              "last_error": res.get("error"),
                              "updated_at": now.isoformat()}},
                )
            else:
                backoff = BACKOFF_MINUTES[min(attempt - 1, len(BACKOFF_MINUTES) - 1)]
                await db.capi_event_queue.update_one(
                    {"id": it["id"]},
                    {"$set": {
                        "attempts": attempt,
                        "last_error": res.get("error"),
                        "next_try_at": (now + timedelta(minutes=backoff)).isoformat(),
                        "updated_at": now.isoformat(),
                    }},
                )
    return {"processed": processed, "ok": ok_count, "failed": fail_count}


async def background_retry_loop(get_db, interval_seconds: int = 1800):
    """Run retry_queue_once every 30 minutes."""
    while True:
        try:
            db = get_db()
            res = await retry_queue_once(db)
            if res.get("processed"):
                logger.info(f"[CAPI Retry] {res}")
        except Exception as e:
            logger.error(f"[CAPI Retry] loop error: {e}")
        await asyncio.sleep(interval_seconds)
