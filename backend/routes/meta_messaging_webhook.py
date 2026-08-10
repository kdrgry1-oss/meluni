"""
Meta Messenger + Instagram AI müşteri temsilcisi — Messenger Platform webhook.

WhatsApp'tan AYRI: Messenger (Facebook Page) ve Instagram DM'leri Messenger
Platform formatını kullanır (entry[].messaging[]) ve Send API farklıdır
(POST /me/messages, Page Access Token). Aynı AI beyni (ai_chatbot) yeniden
kullanılır; yanıt "gerçek bir kıdemli müşteri temsilcisi" tonundadır.

Kimlik: settings.notification_providers.providers.meta_messaging
  { page_access_token, verify_token, app_secret[ops], ai_autoreply }
Aç/Kapa: ai_chatbot.enabled + meta_messaging.ai_autoreply (varsayılan KAPALI).
Guardrail: idempotent (mid), echo/own-message yoksay, düşük güven→insan devri,
imza doğrulama (app_secret varsa), arka planda işlem + hızlı 200.

Not: Messenger/IG'de müşterinin telefonu YOK (PSID/IGSID var) → sipariş bağlamı
telefonla eşleşemez; yanıt bilgi bankası + ürün + firma bilgisiyle temellenir.
Grup/echo mesajları ve statü olayları yok sayılır.
"""
import os
import hmac
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse

from .deps import db, logger
from .ai_chatbot import (
    get_ai_settings, _api_key_for, llm_chat, DEFAULT_PERSONA,
    _gather_kb_context, _gather_product_context,
)

router = APIRouter(prefix="/meta-messaging", tags=["meta-messaging-ai"])

_OPT_OUT = {"durdur", "stop", "iptal"}
_GRAPH_VER = "v23.0"


async def _cfg() -> dict:
    s = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
    return ((s.get("providers") or {}).get("meta_messaging") or {})


@router.get("/webhook")
async def verify_webhook(request: Request):
    p = request.query_params
    cfg = await _cfg()
    expected = cfg.get("verify_token") or os.environ.get("META_MSG_VERIFY_TOKEN", "")
    if p.get("hub.mode") == "subscribe" and expected and p.get("hub.verify_token") == expected:
        return PlainTextResponse(p.get("hub.challenge") or "")
    return PlainTextResponse("forbidden", status_code=403)


def _verify_signature(app_secret: str, raw: bytes, header: str) -> bool:
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    mac = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, header.split("=", 1)[1])


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    cfg = await _cfg()
    if not _verify_signature(cfg.get("app_secret", ""), raw,
                             request.headers.get("x-hub-signature-256", "")):
        return PlainTextResponse("bad signature", status_code=403)
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return PlainTextResponse("EVENT_RECEIVED")

    obj = data.get("object")  # "page" (Messenger) | "instagram"
    channel = "instagram" if obj == "instagram" else "messenger"
    for entry in (data.get("entry") or []):
        for ev in (entry.get("messaging") or []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue  # kendi gönderdiğimiz mesajın yankısı
            sender = (ev.get("sender") or {}).get("id")
            text = msg.get("text")
            mid = msg.get("mid")
            if not sender or not text or not mid:
                continue
            background.add_task(_handle, channel=channel, sender=sender, mid=mid, text=text.strip())
    return PlainTextResponse("EVENT_RECEIVED")


async def _already(mid: str) -> bool:
    try:
        res = await db.meta_msg_processed.update_one(
            {"_id": mid}, {"$setOnInsert": {"at": _now()}}, upsert=True)
        return res.upserted_id is None
    except Exception:
        return False


async def _handle(channel: str, sender: str, mid: str, text: str):
    try:
        if await _already(mid):
            return
        cfg = await _cfg()
        low = text.lower().strip()
        if low in _OPT_OUT:
            await _send(cfg, sender, "Mesajlarımız durduruldu. Tekrar yazarak bize ulaşabilirsiniz.")
            await _log(channel, sender, text, "[opt-out]", handoff=False, confidence=1.0)
            return

        settings = await get_ai_settings()
        if not settings.get("enabled", True) or not cfg.get("ai_autoreply", False):
            await _log(channel, sender, text, "", handoff=True, confidence=0.0, note="ai_disabled")
            return
        api_key = _api_key_for(settings)
        if not api_key:
            await _log(channel, sender, text, "", handoff=True, confidence=0.0, note="no_api_key")
            return

        kb_ctx = await _gather_kb_context(text)
        prod_ctx = await _gather_product_context(text)
        store_name = await _store_name()

        system = settings.get("persona") or DEFAULT_PERSONA
        _er = (settings.get("wa_extra_rules") or "").strip()
        if _er:
            system += f"\n\n[MAĞAZA EK KURALLARI — bunlara MUTLAKA uy]\n{_er}\n"
        if store_name:
            system += f"\n\nMağaza adı: {store_name}. Kendini bu mağazanın temsilcisi olarak tanıt."
        chname = "Instagram" if channel == "instagram" else "Messenger"
        system += (
            f"\n\nKanal: {chname} DM. Kısa, sıcak, gerçek bir insan temsilci gibi yaz "
            "(1-4 cümle). Emin olmadığın sipariş/stok/fiyat/iade bilgisini UYDURMA; "
            "bilmiyorsan insana devret.\n--- BİLGİ KAYNAĞI (yalnız bunları kullan) ---\n"
        )
        if prod_ctx:
            system += f"\n[Ürün Bilgisi]\n{prod_ctx}\n"
        if kb_ctx:
            system += f"\n[Bilgi Bankası]\n{kb_ctx}\n"
        system += "\nCevabın SONUNA ayrı satırda ekle:\n---META---\nCONFIDENCE: <0.0-1.0>\nHANDOFF: <yes|no>\n"

        try:
            resp = await llm_chat(
                api_key=api_key,
                provider=settings.get("provider", "anthropic"),
                model=settings.get("fast_model") or settings.get("model", "claude-haiku-4-5"),
                system_message=system, user_text=f"Müşteri: {text}", max_tokens=500,
            )
        except Exception as e:
            logger.exception("Meta-msg AI reply failed")
            await _handoff(cfg, channel, sender, text)
            await _log(channel, sender, text, "", handoff=True, confidence=0.0, note=f"llm_error:{e}")
            return

        reply, confidence, handoff = _parse_meta(str(resp or ""))
        if confidence < float(settings.get("confidence_threshold", 0.7) or 0.7):
            handoff = True
        if handoff or not reply:
            await _handoff(cfg, channel, sender, text)
            await _log(channel, sender, text, reply, handoff=True, confidence=confidence)
            return
        await _send(cfg, sender, reply)
        await _log(channel, sender, text, reply, handoff=False, confidence=confidence)
    except Exception as e:
        logger.exception(f"Meta-msg handler error: {e}")


def _parse_meta(text: str):
    import re
    confidence, handoff, reply = 0.7, False, text.strip()
    m = re.search(r"---META---\s*CONFIDENCE:\s*([0-9.]+)\s*HANDOFF:\s*(\w+)", text, re.I)
    if m:
        try:
            confidence = max(0.0, min(1.0, float(m.group(1))))
        except Exception:
            pass
        handoff = m.group(2).lower().startswith("y")
        reply = text[:m.start()].strip()
    reply = re.split(r"-{2,}\s*META", reply, 1)[0].strip()
    return reply, confidence, handoff


async def _send(cfg: dict, recipient_id: str, message: str):
    """Messenger + Instagram ortak Send API: POST /me/messages (Page Access Token)."""
    import httpx
    token = cfg.get("page_access_token", "")
    if not token:
        logger.warning("meta_messaging: page_access_token yok")
        return
    url = f"https://graph.facebook.com/{_GRAPH_VER}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "messaging_type": "RESPONSE",
        "message": {"text": message[:1900]},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, params={"access_token": token}, json=payload)
            if r.status_code not in (200, 201):
                logger.warning(f"meta-msg send failed {r.status_code}: {r.text[:300]}")
    except Exception as e:
        logger.warning(f"meta-msg send error: {e}")


async def _handoff(cfg: dict, channel: str, sender: str, text: str):
    try:
        await db.meta_handoffs.insert_one({
            "channel": channel, "sender": sender, "question": text,
            "status": "open", "created_at": _now(),
        })
    except Exception:
        pass
    await _send(cfg, sender, "Talebinizi müşteri temsilcimize ilettim, en kısa sürede dönüş yapacağız. 🙏")


async def _log(channel: str, sender: str, inbound: str, outbound: str, *,
               handoff: bool, confidence: float, note: str = ""):
    try:
        await db.meta_conversations.insert_one({
            "channel": channel, "sender": sender, "inbound": inbound,
            "outbound": outbound, "handoff": handoff, "confidence": confidence,
            "note": note, "created_at": _now(),
        })
    except Exception:
        pass
    # ADMIN 'Müşteri Soruları' paneli — Instagram/Messenger sohbetleri de görünsün + eğitilebilsin.
    coll_name = {"instagram": "instagram_messages", "messenger": "messenger_messages"}.get(channel)
    if coll_name and inbound and inbound != "[opt-out]" and note not in ("history", "human_reply"):
        try:
            from .deps import generate_id
            await db[coll_name].insert_one({
                "question_id": generate_id(),
                "question_text": inbound,
                "answer": (outbound if (not handoff and outbound and outbound != "[opt-out]") else ""),
                "status": "WAITING_FOR_ANSWER" if handoff else "ANSWERED",
                "customer_name": f"{channel.capitalize()} • {str(sender)[-4:]}",
                "customer_phone": sender,
                "confidence": confidence,
                "channel": channel,
                "created_at": _now(),
                "created_date": _now(),
            })
        except Exception:
            pass


async def _store_name() -> str:
    try:
        import company
        c = await company.get_company(db)
        return c.get("store_name") or ""
    except Exception:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
