"""
=============================================================================
ai_assistant.py — Akıllı Müşteri Yanıtlayıcı (Iter38)
=============================================================================
ai_chatbot.py'nin üstünde:
- Direct chat ile bot eğitimi (admin doğrudan bota öğretir)
- Toplu eğitim (geçmiş ANSWERED Trendyol sorularından KB üret)
- Otomatik yanıtlama batch (confidence > threshold → auto-send)
- Yetersiz cevap dedektörü (AI quality check)

Endpoints:
- POST /api/ai-assistant/chat — admin direkt bot ile sohbet
- POST /api/ai-assistant/bulk-train — geçmiş Q&A'dan KB üret
- GET  /api/ai-assistant/bulk-train-status — son toplu eğitim raporu
- POST /api/ai-assistant/auto-answer-batch — bekleyen soruları toplu yanıtla
- GET  /api/ai-assistant/auto-answer-stats — son batch istatistikleri
=============================================================================
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from typing import Optional
import uuid
import re

# Emergent kaldırıldı → ai_chatbot.llm_chat kullanılıyor

from .deps import db, require_admin, logger
from .ai_chatbot import (
    get_ai_settings, _api_key_for, llm_chat, DEFAULT_PERSONA, MARKETPLACE_TO_COLL,
    _gather_kb_context, _gather_product_context,
)

router = APIRouter(prefix="/ai-assistant", tags=["ai-assistant"])


# ---------------------------------------------------------------------------
# 1) DIRECT CHAT (admin öğretiyor)
# ---------------------------------------------------------------------------
# Admin bota şu formatlarda yazabilir:
# - "S: kargo kaç günde gelir? C: 2-3 iş gününde teslim edilir."  → KB'ye otomatik ekle
# - "Müşteriye nazik ol" / "Beden konusunda XL=42 deniyor"        → persona/talimat olarak kaydet
# - Düz soru: bot yanıtlar (test için)
#
# Niyet algılama bot'a delege edilir; LLM kararını JSON olarak verir.

CHAT_INTENT_SYSTEM = """Sen Facette mağazasının AI asistan eğitim arabirimisin.
Admin sana 3 niyetten birini iletir:
1) TEACH_QA  — Soru-Cevap çifti veriyor, KB'ye eklenecek.
2) INSTRUCT  — Persona/davranış talimatı (örn: "müşteriye nazik ol", "beden tablosu: XL=42")
3) ASK       — Düz bir soru soruyor; sen normal cevap ver.

Her cevabını ZORUNLU olarak şu formatla bitirmen gerekiyor:
---META---
INTENT: <TEACH_QA|INSTRUCT|ASK>
KB_QUESTION: <TEACH_QA ise soru metni, değilse boş>
KB_ANSWER:   <TEACH_QA ise cevap metni, değilse boş>
INSTRUCTION: <INSTRUCT ise tek satır talimat, değilse boş>

META üstündeki kısımda admin'e Türkçe, kısa, samimi yanıt ver:
- TEACH_QA → "Eklendi: 'soru' → KB'ye kaydettim ✓"
- INSTRUCT → "Talimat alındı: ... — sonraki cevaplarda uygulayacağım."
- ASK → Soruya kısa, profesyonel bir yanıt + "Bu cevap KB'ye otomatik eklenmedi; istersen 'Kaydet' diyebilirsin."
"""


@router.post("/chat")
async def chat_with_assistant(payload: dict, current_user: dict = Depends(require_admin)):
    """Admin AI asistanla doğrudan sohbet eder, bot eğitimi yapar.

    Body: {"text": "...", "session_id": "<opt>"}
    Response: {"reply": "...", "intent": "TEACH_QA|INSTRUCT|ASK",
               "kb_added": bool, "instruction_saved": bool}
    """
    text = (payload or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text boş olamaz")
    session_id = (payload or {}).get("session_id") or f"admin-{current_user.get('id', 'x')}"

    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI anahtarı yapılandırılmamış")

    try:
        resp_raw = await llm_chat(
            api_key=api_key,
            provider=settings.get("provider", "anthropic"),
            model=settings.get("fast_model", "claude-haiku-4-5"),
            system_message=CHAT_INTENT_SYSTEM,
            user_text=text,
            max_tokens=800,
        )
    except Exception as e:
        logger.exception("AI assistant chat failed")
        raise HTTPException(status_code=500, detail=f"AI yanıt veremedi: {e}")

    resp = str(resp_raw or "").strip()

    # Parse META
    intent = "ASK"
    kb_q = ""
    kb_a = ""
    instruction = ""
    m = re.search(
        r"---META---\s*INTENT:\s*(\w+)\s*"
        r"KB_QUESTION:\s*(.*?)\s*"
        r"KB_ANSWER:\s*(.*?)\s*"
        r"INSTRUCTION:\s*(.*?)$",
        resp, re.IGNORECASE | re.DOTALL
    )
    reply_text = resp
    if m:
        intent = m.group(1).upper()
        kb_q = m.group(2).strip().strip("\"'")
        kb_a = m.group(3).strip().strip("\"'")
        instruction = m.group(4).strip().strip("\"'")
        reply_text = resp[: m.start()].strip()

    kb_added = False
    instruction_saved = False

    if intent == "TEACH_QA" and kb_q and kb_a:
        await db.ai_knowledge_base.insert_one({
            "id": str(uuid.uuid4()),
            "question": kb_q,
            "answer": kb_a,
            "tags": ["chat-trained"],
            "channel": "all",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": current_user.get("email", ""),
            "usage_count": 0,
        })
        kb_added = True

    elif intent == "INSTRUCT" and instruction:
        # Persona'ya append et
        cur_persona = settings.get("persona") or DEFAULT_PERSONA
        new_persona = cur_persona + f"\n• {instruction}"
        await db.settings.update_one(
            {"id": "ai_chatbot"},
            {"$set": {"persona": new_persona,
                      "persona_updated_at": datetime.now(timezone.utc).isoformat(),
                      "persona_updated_by": current_user.get("email")}},
            upsert=True,
        )
        instruction_saved = True

    # Audit log
    await db.ai_chat_history.insert_one({
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "admin_email": current_user.get("email"),
        "input": text,
        "reply": reply_text,
        "intent": intent,
        "kb_added": kb_added,
        "instruction_saved": instruction_saved,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "reply": reply_text,
        "intent": intent,
        "kb_added": kb_added,
        "instruction_saved": instruction_saved,
        "kb_question": kb_q if kb_added else None,
        "kb_answer": kb_a if kb_added else None,
        "instruction": instruction if instruction_saved else None,
        "session_id": session_id,
    }


@router.get("/chat/history")
async def get_chat_history(limit: int = 100, current_user: dict = Depends(require_admin)):
    items = await db.ai_chat_history.find(
        {"admin_email": current_user.get("email")},
        {"_id": 0}
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"items": items}


# ---------------------------------------------------------------------------
# 2) TOPLU EĞİTİM (geçmiş Q&A'dan KB üret)
# ---------------------------------------------------------------------------

@router.post("/bulk-train")
async def bulk_train_from_history(
    payload: Optional[dict] = None,
    current_user: dict = Depends(require_admin),
):
    """Trendyol/HB/Temu collection'larındaki ANSWERED soruları KB'ye toplu aktar.

    Body: {"channel": "trendyol|all", "min_answer_length": 30,
           "skip_existing": true, "max_count": 1000}
    """
    cfg = payload or {}
    channel = cfg.get("channel", "all")
    min_len = int(cfg.get("min_answer_length") or 30)
    skip_existing = bool(cfg.get("skip_existing", True))
    max_count = int(cfg.get("max_count") or 1000)

    collections = []
    if channel == "all":
        collections = list(MARKETPLACE_TO_COLL.values())
    elif channel in MARKETPLACE_TO_COLL:
        collections = [MARKETPLACE_TO_COLL[channel]]
    else:
        raise HTTPException(status_code=400, detail="channel: trendyol|hepsiburada|temu|all")

    inserted = 0
    skipped_short = 0
    skipped_existing = 0
    scanned = 0

    for coll_name in collections:
        cur = db[coll_name].find(
            {"status": "ANSWERED", "answer": {"$nin": [None, ""]}},
            {"_id": 0, "question_text": 1, "answer": 1, "question_id": 1}
        ).limit(max_count - inserted)
        async for doc in cur:
            scanned += 1
            q_text = (doc.get("question_text") or "").strip()
            a_text = (doc.get("answer") or "").strip()
            if not q_text or not a_text:
                continue
            if len(a_text) < min_len:
                skipped_short += 1
                continue
            if skip_existing:
                exists = await db.ai_knowledge_base.find_one(
                    {"source_question_id": doc.get("question_id")},
                    {"_id": 1},
                )
                if exists:
                    skipped_existing += 1
                    continue
            await db.ai_knowledge_base.insert_one({
                "id": str(uuid.uuid4()),
                "question": q_text,
                "answer": a_text,
                "tags": ["bulk-train", coll_name.split("_")[0]],
                "channel": coll_name.split("_")[0],
                "source_question_id": doc.get("question_id"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": current_user.get("email", ""),
                "usage_count": 0,
            })
            inserted += 1
            if inserted >= max_count:
                break
        if inserted >= max_count:
            break

    # Last run kayıt
    await db.settings.update_one(
        {"id": "ai_assistant_last_train"},
        {"$set": {
            "id": "ai_assistant_last_train",
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "ran_by": current_user.get("email"),
            "channel": channel,
            "scanned": scanned,
            "inserted": inserted,
            "skipped_short": skipped_short,
            "skipped_existing": skipped_existing,
        }},
        upsert=True,
    )

    return {
        "success": True,
        "scanned": scanned,
        "inserted": inserted,
        "skipped_short": skipped_short,
        "skipped_existing": skipped_existing,
    }


@router.get("/bulk-train-status")
async def bulk_train_status(current_user: dict = Depends(require_admin)):
    last = await db.settings.find_one({"id": "ai_assistant_last_train"}, {"_id": 0})
    kb_total = await db.ai_knowledge_base.count_documents({})
    kb_chat_trained = await db.ai_knowledge_base.count_documents({"tags": "chat-trained"})
    kb_bulk_trained = await db.ai_knowledge_base.count_documents({"tags": "bulk-train"})
    return {
        "last_run": last,
        "kb_total": kb_total,
        "kb_chat_trained": kb_chat_trained,
        "kb_bulk_trained": kb_bulk_trained,
    }


# ---------------------------------------------------------------------------
# 3) AUTO-ANSWER BATCH
# ---------------------------------------------------------------------------

QUALITY_CHECK_SYSTEM = """Sen bir Türkçe müşteri hizmetleri kalite kontrol uzmanısın.
Verilen 'cevap' metnini değerlendir: müşterinin sorusunu YETERLİ ölçüde
karşılıyor mu? Çok kısa, kaçamak, alakasız ya da soru kaçıyorsa YETERSİZ say.

Tek satır JSON döndür:
{"sufficient": true|false, "reason": "<kısa Türkçe gerekçe>"}
"""


@router.post("/evaluate-answer")
async def evaluate_answer(payload: dict, current_user: dict = Depends(require_admin)):
    question = (payload or {}).get("question", "").strip()
    answer = (payload or {}).get("answer", "").strip()
    if not question or not answer:
        raise HTTPException(status_code=400, detail="question ve answer gerekli")

    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI anahtarı yok")

    try:
        resp = await llm_chat(
            api_key=api_key,
            provider=settings.get("provider", "anthropic"),
            model=settings.get("fast_model", "claude-haiku-4-5"),
            system_message=QUALITY_CHECK_SYSTEM,
            user_text=f"Soru: {question}\nCevap: {answer}",
            max_tokens=600,
        )
    except Exception as e:
        return {"sufficient": True, "reason": f"QC çağrı hatası, varsayılan yeterli ({e})"}

    text = str(resp or "").strip()
    sufficient = True
    reason = ""
    m = re.search(r'"sufficient"\s*:\s*(true|false)', text, re.IGNORECASE)
    if m:
        sufficient = m.group(1).lower() == "true"
    m2 = re.search(r'"reason"\s*:\s*"([^"]+)"', text)
    if m2:
        reason = m2.group(1)

    return {"sufficient": sufficient, "reason": reason or text[:200]}


@router.post("/auto-answer-batch")
async def auto_answer_batch(
    payload: Optional[dict] = None,
    current_user: dict = Depends(require_admin),
):
    """Bekleyen WAITING_FOR_ANSWER Trendyol soruları için draft + auto-send.

    Body: {"channel":"trendyol", "max_count":10, "min_confidence":0.85,
           "dry_run": false, "send": false}
      - dry_run: sadece draft üret, gönderme
      - send: confidence>=min_confidence olanları gerçekten Trendyol'a gönder
    """
    cfg = payload or {}
    channel = cfg.get("channel", "trendyol")
    max_count = min(int(cfg.get("max_count") or 10), 50)   # A2.2: sunucu-tarafı cap (denial-of-wallet)
    min_conf = float(cfg.get("min_confidence") or 0.85)
    dry_run = bool(cfg.get("dry_run", False))
    # A1.4 (GÜVENLİK): AI cevabı ARTIK OTOMATİK GÖNDERİLMEZ. Prompt-injection ile
    # güven eşiği/handoff modelin serbest metninden sahtelenip satıcı adına kamuya
    # zararlı içerik yayınlanabildiğinden, gönderim İNSAN ONAYI gerektirir: taslaklar
    # ai_suggestions'a kuyruklanır; admin QnA panelinden manuel gönderir
    # (POST /trendyol/questions/{id}/answer). 'send' parametresi yok sayılır.
    do_send = False

    if channel not in MARKETPLACE_TO_COLL:
        raise HTTPException(status_code=400, detail="channel desteklenmiyor")

    coll = db[MARKETPLACE_TO_COLL[channel]]
    waiting = await coll.find(
        {"status": "WAITING_FOR_ANSWER"},
        {"_id": 0}
    ).sort("created_at", -1).limit(max_count).to_list(max_count)

    settings = await get_ai_settings()
    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI anahtarı yok")

    results = []
    sent_count = 0
    queued_count = 0

    # Lazy import — circular import önle
    if channel == "trendyol":
        from .integrations import get_trendyol_config, get_trendyol_headers
        ty_config = await get_trendyol_config()
        ty_headers = await get_trendyol_headers()

    import httpx

    for q in waiting:
        q_id = q.get("question_id")
        q_text = q.get("question_text", "")

        # 1) Draft üret (KB + persona ile)
        kb_ctx = await _gather_kb_context(q_text)
        prod_ctx = await _gather_product_context(q.get("product_name", ""))
        sys = (settings.get("persona") or DEFAULT_PERSONA) + "\n\n--- BİLGİ KAYNAĞI ---\n"
        if prod_ctx:
            sys += f"\n[Ürün]\n{prod_ctx}\n"
        if kb_ctx:
            sys += f"\n[Önceki Onaylı Yanıtlar]\n{kb_ctx}\n"
        sys += (
            "\nÖnemli: SADECE müşterinin sorusuna doğrudan cevap ver. Selamlama gereksiz değilse koy ama uzatma. "
            "Cevabın sonuna ZORUNLU şu metayı ekle:\n"
            "---META---\nCONFIDENCE: <0.0-1.0>\nHANDOFF: <yes|no>\n"
        )
        try:
            r = await llm_chat(
                api_key=api_key,
                provider=settings.get("provider", "anthropic"),
                model=settings.get("model", "claude-sonnet-4-6"),
                system_message=sys,
                user_text=f"Soru: {q_text}",
                max_tokens=1200,
            )
        except Exception as e:
            results.append({"question_id": q_id, "ok": False, "error": str(e)})
            continue

        text = str(r or "").strip()
        confidence = 0.7
        handoff = False
        m = re.search(r"---META---\s*CONFIDENCE:\s*([0-9.]+)\s*HANDOFF:\s*(\w+)", text, re.IGNORECASE)
        draft = text
        if m:
            try:
                confidence = float(m.group(1))
            except Exception:
                pass
            handoff = m.group(2).lower().startswith("y")
            draft = text[: m.start()].strip()
        # A2.1: META bloğu regex tutmazsa da draft'a SIZMASIN (ortak temizlik).
        draft = re.split(r"-{2,}\s*META", draft, 1)[0].strip()

        # 2) Yetersiz cevap dedektörü (kısa cevaplar için)
        is_sufficient = True
        if len(draft) < 20:
            is_sufficient = False

        action = "queued"
        send_resp = None
        if not dry_run and do_send and confidence >= min_conf and not handoff and is_sufficient:
            # Trendyol'a gönder
            if channel == "trendyol" and ty_config and ty_config.get("is_active") and ty_headers:
                base_url = "https://apigw.trendyol.com" if ty_config.get("mode") == "live" else "https://stageapigw.trendyol.com"
                url = f"{base_url}/integration/qna/sellers/{ty_config['supplier_id']}/questions/{q_id}/answers"
                try:
                    async with httpx.AsyncClient(timeout=15) as cli:
                        resp_send = await cli.post(url, headers=ty_headers, json={"text": draft})
                        if resp_send.status_code in (200, 201):
                            await coll.update_one(
                                {"question_id": q_id},
                                {"$set": {"answer": draft, "status": "ANSWERED",
                                          "answered_at": datetime.now(timezone.utc).isoformat(),
                                          "auto_answered_by_ai": True,
                                          "ai_confidence": confidence}}
                            )
                            action = "sent"
                            sent_count += 1
                        else:
                            action = "send_failed"
                            send_resp = f"HTTP {resp_send.status_code}: {resp_send.text[:120]}"
                except Exception as e:
                    action = "send_error"
                    send_resp = str(e)
        else:
            queued_count += 1

        # AI suggestion her durumda kaydet
        await db.ai_suggestions.insert_one({
            "id": str(uuid.uuid4()),
            "marketplace": channel,
            "question_id": q_id,
            "draft": draft,
            "confidence": confidence,
            "handoff": handoff,
            "is_sufficient": is_sufficient,
            "action": action,
            "auto_batch": True,
            "send_response": send_resp,
            "model": settings.get("model"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": current_user.get("email", ""),
        })

        results.append({
            "question_id": q_id,
            "question": q_text[:80],
            "draft": draft[:200],
            "confidence": confidence,
            "handoff": handoff,
            "is_sufficient": is_sufficient,
            "action": action,
            "send_response": send_resp,
        })

    # Stats kaydet
    await db.settings.update_one(
        {"id": "ai_assistant_last_auto_answer"},
        {"$set": {
            "id": "ai_assistant_last_auto_answer",
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "ran_by": current_user.get("email"),
            "channel": channel,
            "min_confidence": min_conf,
            "dry_run": dry_run,
            "send": do_send,
            "processed": len(results),
            "sent": sent_count,
            "queued": queued_count,
        }},
        upsert=True,
    )

    return {
        "success": True,
        "processed": len(results),
        "sent": sent_count,
        "queued": queued_count,
        "results": results,
    }


@router.get("/auto-answer-stats")
async def auto_answer_stats(current_user: dict = Depends(require_admin)):
    last = await db.settings.find_one({"id": "ai_assistant_last_auto_answer"}, {"_id": 0})
    pending_trendyol = await db.trendyol_questions.count_documents({"status": "WAITING_FOR_ANSWER"})
    auto_answered_today = await db.trendyol_questions.count_documents({
        "auto_answered_by_ai": True,
        "answered_at": {"$gte": (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()},
    })
    suggestions_pending = await db.ai_suggestions.count_documents({"action": "queued"})
    return {
        "last_run": last,
        "pending_trendyol": pending_trendyol,
        "auto_answered_today": auto_answered_today,
        "suggestions_pending": suggestions_pending,
    }
