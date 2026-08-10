"""
AI Chatbot (Customer Service Assistant) module.

Architecture:
  - One orchestrator agent (LlmChat) driven by persona + RAG-style knowledge.
  - Tools are not delegated to the LLM via function-calling here (keeps it simple
    and reliable); instead we pre-fetch product/order/policy data and inject as
    context before the model generates a Turkish reply.
  - Draft generation returns the suggested answer + confidence score.
  - If confidence < threshold, we hint the panel to hand off to a human.
  - Each approved Q&A pair is added to `ai_knowledge_base` as a reusable FAQ.

Settings live in `db.settings` under id="ai_chatbot".
Conversations per marketplace live in their own collections; `ai_suggestions`
tracks draft answers attached to a specific question_id.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
import os
import uuid
import re

# Emergent kaldırıldı → sağlayıcı-bağımsız doğrudan SDK çağrısı: llm_chat() (aşağıda)

from .deps import db, require_admin, logger

router = APIRouter(prefix="/ai", tags=["ai-chatbot"])

DEFAULT_PERSONA = (
    "Sen Facette marka moda mağazasının kıdemli müşteri temsilcisisin. "
    "Kısa, sıcak, samimi ve profesyonel bir tonla Türkçe konuşursun. "
    "Emin olmadığın bilgiyi kesinlikle uydurmazsın; 'kontrol edip yazayım' dersin. "
    "Gereksiz emoji kullanma, müşterinin adını uygunsa bir kez kullan, tekrara düşme. "
    "Stok/fiyat/sipariş bilgisini sadece sana sağlanan verilerden cevapla. "
    "Gerekirse müşteriyi hafif bir CTA ile satışa yönlendir."
)


async def get_ai_settings() -> dict:
    s = await db.settings.find_one({"id": "ai_chatbot"}, {"_id": 0})
    if not s:
        return {
            "id": "ai_chatbot",
            "enabled": True,
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "fast_model": "claude-haiku-4-5",
            "persona": DEFAULT_PERSONA,
            "confidence_threshold": 0.7,
            "use_emergent_key": False,
            "custom_api_key": "",
            "channels": {
                "trendyol": True, "hepsiburada": True, "temu": True,
                "whatsapp": False, "instagram": False, "messenger": False, "site": True,
            },
        }
    return s


def _api_key_for(settings: dict) -> str:
    """AI anahtarını çözer (öncelik sırası):
      1) Ayarlardaki kendi anahtarın (custom_api_key)
      2) Sağlayıcıya göre ortam değişkeni (OPENAI/ANTHROPIC/GEMINI_API_KEY)
      3) Geriye dönük: Emergent (EMERGENT_LLM_KEY) — Emergent'ten çıkınca devre dışı."""
    # 1) Kendi anahtarın — şifreli (custom_api_key_enc) öncelikli, eski düz metin yedek
    enc = settings.get("custom_api_key_enc")
    if enc:
        try:
            from security.crypto import decrypt
            dec = decrypt(enc)
            if dec:
                return dec
        except Exception:
            pass
    if settings.get("custom_api_key"):
        return settings["custom_api_key"]
    prov = (settings.get("provider") or "openai").strip().lower()
    env_name = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY", "claude": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY", "google": "GEMINI_API_KEY",
    }.get(prov, "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    if settings.get("use_emergent_key"):
        return os.environ.get("EMERGENT_LLM_KEY", "")
    return ""


async def llm_chat(api_key: str, provider: str, model: str,
                   system_message: str, user_text: str,
                   max_tokens: int = 1200) -> str:
    """Sağlayıcı-bağımsız tek-tur sohbet (Emergent yerine DOĞRUDAN resmi SDK).
    provider: 'openai' | 'anthropic'/'claude' | 'gemini'/'google'. Düz metin döner."""
    prov = (provider or "openai").strip().lower()

    if prov in ("anthropic", "claude"):
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model=model or "claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_message or "",
            messages=[{"role": "user", "content": user_text}],
        )
        parts = []
        for b in (msg.content or []):
            t = getattr(b, "text", None)
            if t:
                parts.append(t)
        return "".join(parts).strip()

    if prov in ("gemini", "google", "google-gemini"):
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=model or "gemini-3.1-flash",
            contents=user_text,
            config={"system_instruction": system_message or "",
                    "max_output_tokens": max_tokens},
        )
        return (getattr(resp, "text", None) or "").strip()

    # varsayılan: OpenAI (gpt-5.x → max_completion_tokens)
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model or "gpt-5.4-mini",
        messages=[{"role": "system", "content": system_message or ""},
                  {"role": "user", "content": user_text}],
        max_completion_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# -------------------- Settings endpoints --------------------

@router.get("/settings")
async def get_ai_chatbot_settings(current_user: dict = Depends(require_admin)):
    s = await get_ai_settings()
    # Hassas anahtar ASLA düz dönmez: şifreli blob gizlenir; sadece "tanımlı mı" bilgisi maske olarak verilir.
    has_key = bool(s.get("custom_api_key_enc") or s.get("custom_api_key"))
    s.pop("custom_api_key_enc", None)
    s["custom_api_key"] = "********" if has_key else ""
    s["has_api_key"] = has_key
    return s


@router.post("/settings")
async def save_ai_chatbot_settings(payload: dict, current_user: dict = Depends(require_admin)):
    update = {
        "id": "ai_chatbot",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for f in (
        "enabled", "provider", "model", "fast_model", "persona",
        "confidence_threshold", "use_emergent_key", "channels",
        "wa_extra_rules",   # WhatsApp/sosyal AI'a panelden eklenebilen ek kurallar (düzenlenebilir)
    ):
        if f in payload:
            update[f] = payload[f]
    newk = (payload.get("custom_api_key") or "").strip()
    set_ops = {"$set": update}
    if newk and newk != "********":
        # Yeni anahtar — AES-256-GCM ile şifrele; eski düz metin alanını temizle.
        from security.crypto import encrypt
        update["custom_api_key_enc"] = encrypt(newk)
        set_ops["$unset"] = {"custom_api_key": ""}
    await db.settings.update_one({"id": "ai_chatbot"}, set_ops, upsert=True)
    return {"success": True}


# -------------------- Knowledge Base endpoints --------------------

@router.get("/kb")
async def list_kb_entries(
    q: Optional[str] = None,
    current_user: dict = Depends(require_admin),
):
    query = {}
    if q:
        query["$or"] = [
            {"question": {"$regex": q, "$options": "i"}},
            {"answer": {"$regex": q, "$options": "i"}},
        ]
    items = await db.ai_knowledge_base.find(query, {"_id": 0}).sort("usage_count", -1).limit(500).to_list(500)
    total = await db.ai_knowledge_base.count_documents({})
    return {"items": items, "total": total}


@router.post("/kb")
async def add_kb_entry(payload: dict, current_user: dict = Depends(require_admin)):
    question = (payload or {}).get("question", "").strip()
    answer = (payload or {}).get("answer", "").strip()
    if not question or not answer:
        raise HTTPException(status_code=400, detail="Soru ve cevap boş olamaz")
    doc = {
        "id": str(uuid.uuid4()),
        "question": question,
        "answer": answer,
        "tags": payload.get("tags", []),
        "channel": payload.get("channel", "all"),
        "source_question_id": payload.get("source_question_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
        "usage_count": 0,
    }
    await db.ai_knowledge_base.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "entry": doc}


@router.delete("/kb/{entry_id}")
async def delete_kb_entry(entry_id: str, current_user: dict = Depends(require_admin)):
    res = await db.ai_knowledge_base.delete_one({"id": entry_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"success": True}


# -------------------- Draft generation --------------------

MARKETPLACE_TO_COLL = {
    "trendyol": "trendyol_questions",
    "hepsiburada": "hepsiburada_questions",
    "temu": "temu_questions",
    "whatsapp": "whatsapp_messages",
    "instagram": "instagram_messages",
    "messenger": "messenger_messages",
    "site": "site_messages",
}


async def _gather_kb_context(question_text: str) -> str:
    """Return top 5 KB entries loosely matching the question (keyword-based MVP)."""
    words = [w for w in re.split(r"\W+", question_text.lower()) if len(w) > 3]
    if not words:
        return ""
    or_clauses = [{"question": {"$regex": w, "$options": "i"}} for w in words[:5]]
    items = await db.ai_knowledge_base.find(
        {"$or": or_clauses}, {"_id": 0, "question": 1, "answer": 1}
    ).limit(5).to_list(5)
    if not items:
        return ""
    return "\n".join(f"- S: {i['question']}\n  C: {i['answer']}" for i in items)


async def _gather_product_qa_rules(product_name: str) -> str:
    """Ürüne özel cevap kuralları — AI Asistan → 'Ürüne Özel Yanıt' sekmesinden girilir.
    Soru product_name taşıdığından eşleştirme isim bazlıdır. EN YÜKSEK ÖNCELİK."""
    if not product_name:
        return ""
    try:
        rdoc = await db.product_qa_rules.find_one(
            {"product_name": {"$regex": re.escape(product_name[:32]), "$options": "i"}},
            {"_id": 0, "instructions": 1, "rules": 1},
        )
    except Exception:
        rdoc = None
    if not rdoc:
        return ""
    parts = []
    if (rdoc.get("instructions") or "").strip():
        parts.append(rdoc["instructions"].strip())
    for r in (rdoc.get("rules") or [])[:20]:
        rq = (r.get("q") or "").strip()
        ra = (r.get("a") or "").strip()
        if ra:
            parts.append(f"Soru '{rq}' benzeri ise: {ra}" if rq else ra)
    if not parts:
        return ""
    return ("[ÜRÜNE ÖZEL KURALLAR — EN YÜKSEK ÖNCELİK; ÇELİŞKİDE BUNLARI UYGULA]\n"
            + "\n".join(f"• {p}" for p in parts))


async def _gather_product_context(product_name: str) -> str:
    rule_txt = await _gather_product_qa_rules(product_name)
    if not product_name:
        return rule_txt
    prods = await db.products.find(
        {"name": {"$regex": re.escape(product_name[:40]), "$options": "i"}},
        {"_id": 0, "name": 1, "price": 1, "stock": 1, "variants": 1, "description": 1, "attributes": 1, "category_name": 1}
    ).limit(3).to_list(3)
    lines = []
    for p in prods:
        sv = [f"{v.get('size')}:{v.get('stock', 0)}" for v in (p.get("variants") or [])][:10]
        attrs = []
        for a in (p.get("attributes") or [])[:18]:
            if isinstance(a, dict):
                an = (a.get("name") or "").strip()
                av = (a.get("value") or "").strip()
                if an and av:
                    attrs.append(f"{an}: {av}")
        line = (
            f"- Ürün: {p.get('name')} | Kategori: {p.get('category_name', '—')} | "
            f"Fiyat: {p.get('price', '—')} TL | Toplam Stok: {p.get('stock', 0)} | "
            f"Bedenler: {', '.join(sv) if sv else 'tek beden'}"
        )
        if attrs:
            line += f"\n  Özellikler: {', '.join(attrs)}"
        lines.append(line)
    prod_txt = "\n".join(lines)
    if rule_txt and prod_txt:
        return rule_txt + "\n\n" + prod_txt
    return rule_txt or prod_txt


@router.post("/draft/{marketplace}/{question_id}")
async def generate_draft_answer(
    marketplace: str,
    question_id: str,
    payload: Optional[dict] = None,
    current_user: dict = Depends(require_admin),
):
    if marketplace not in MARKETPLACE_TO_COLL:
        raise HTTPException(status_code=404, detail="Bilinmeyen kanal")
    coll = db[MARKETPLACE_TO_COLL[marketplace]]
    q = await coll.find_one({"question_id": question_id}, {"_id": 0})
    if not q:
        raise HTTPException(status_code=404, detail="Soru bulunamadı")

    settings = await get_ai_settings()
    if not settings.get("enabled", True):
        raise HTTPException(status_code=400, detail="AI Chatbot devre dışı")

    api_key = _api_key_for(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="AI anahtarı yapılandırılmamış")

    kb_ctx = await _gather_kb_context(q.get("question_text", ""))
    prod_ctx = await _gather_product_context(q.get("product_name", ""))

    system = settings.get("persona") or DEFAULT_PERSONA
    system += (
        "\n\n--- BİLGİ KAYNAĞI ---\n"
        "Aşağıdaki bilgileri kullan. Bilgi yoksa uydurma.\n"
    )
    if prod_ctx:
        system += f"\n[Ürün Bilgisi]\n{prod_ctx}\n"
    if kb_ctx:
        system += f"\n[Bilgi Bankası - Önceki Onaylı Yanıtlar]\n{kb_ctx}\n"
    system += (
        "\nCevabın sonuna yeni bir satıra ÖZEL bir blok ekle:\n"
        "---META---\n"
        "CONFIDENCE: <0.0-1.0 arası bir değer>\n"
        "HANDOFF: <yes veya no – insan temsilciye devredilmeli mi?>\n"
    )

    user_text = (
        f"[{marketplace.upper()} Kanalı]\n"
        f"Ürün: {q.get('product_name', '—')}\n"
        f"Müşteri: {q.get('customer_name', '—')}\n"
        f"Soru: {q.get('question_text', '')}"
    )

    try:
        response = await llm_chat(
            api_key=api_key,
            provider=settings.get("provider", "anthropic"),
            model=settings.get("model", "claude-sonnet-4-6"),
            system_message=system,
            user_text=user_text,
            max_tokens=1200,
        )
    except Exception as e:
        logger.exception("AI draft failed")
        raise HTTPException(status_code=500, detail=f"AI cevap üretemedi: {e}")

    text = str(response or "").strip()
    confidence = 0.75
    handoff = False
    # Parse META block
    m = re.search(r"---META---\s*CONFIDENCE:\s*([0-9.]+)\s*HANDOFF:\s*(\w+)", text, re.IGNORECASE)
    draft_text = text
    if m:
        try:
            confidence = float(m.group(1))
        except Exception:
            pass
        handoff = m.group(2).lower().startswith("y")
        draft_text = text[: m.start()].strip()
    # DENETİM FIX: regex tutmasa bile '---META---' sonrası HİÇBİR şey müşteriye gitmesin
    # (model biçimi bozarsa 'CONFIDENCE/HANDOFF' bloğu cevaba sızıyordu). Savunma amaçlı kes.
    draft_text = re.split(r"-{2,}\s*META", draft_text, 1)[0].strip()
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except Exception:
        confidence = 0.75

    threshold = float(settings.get("confidence_threshold", 0.7) or 0.7)
    if confidence < threshold:
        handoff = True

    suggestion_doc = {
        "id": str(uuid.uuid4()),
        "marketplace": marketplace,
        "question_id": question_id,
        "draft": draft_text,
        "confidence": confidence,
        "handoff": handoff,
        "model": settings.get("model", "gpt-5.2"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
    }
    await db.ai_suggestions.insert_one(suggestion_doc)

    return {
        "success": True,
        "draft": draft_text,
        "confidence": confidence,
        "handoff": handoff,
        "threshold": threshold,
    }


@router.post("/train-from-question")
async def train_from_question(payload: dict, current_user: dict = Depends(require_admin)):
    """Add an approved Q+A to the knowledge base (called from 'AI'yı Eğit' button)."""
    question = (payload or {}).get("question", "").strip()
    answer = (payload or {}).get("answer", "").strip()
    if not question or not answer:
        raise HTTPException(status_code=400, detail="Soru ve cevap gerekli")
    doc = {
        "id": str(uuid.uuid4()),
        "question": question,
        "answer": answer,
        "channel": payload.get("channel", "all"),
        "source_question_id": payload.get("question_id"),
        "tags": payload.get("tags", []),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user.get("email", ""),
        "usage_count": 0,
    }
    await db.ai_knowledge_base.insert_one(doc)
    return {"success": True}


# ──────────────────────────────────────────────────────────────────────────
# Ürüne Özel Yanıt Kuralları (AI Asistan → 'Ürüne Özel Yanıt' sekmesi)
# Soru product_name taşıdığından eşleşme isim bazlıdır. EN YÜKSEK ÖNCELİK.
# ──────────────────────────────────────────────────────────────────────────
@router.get("/product-qa-rules")
async def list_product_qa_rules(current_user: dict = Depends(require_admin)):
    """Tanımlı tüm ürüne özel kuralları listeler (sekme için)."""
    rows = await db.product_qa_rules.find({}, {"_id": 0}).sort("updated_at", -1).limit(1000).to_list(1000)
    return {"items": rows, "total": len(rows)}


@router.get("/product-qa-rules/{product_id}")
async def get_product_qa_rule(product_id: str, current_user: dict = Depends(require_admin)):
    doc = await db.product_qa_rules.find_one({"product_id": str(product_id)}, {"_id": 0})
    return doc or {"product_id": str(product_id), "instructions": "", "rules": []}


@router.post("/product-qa-rules")
async def upsert_product_qa_rule(payload: dict, current_user: dict = Depends(require_admin)):
    """Bir ürüne özel cevap kuralını kaydeder/günceller.
    Body: {product_id, product_name, instructions?, rules?: [{q, a}]}"""
    pid = str((payload or {}).get("product_id", "")).strip()
    pname = ((payload or {}).get("product_name") or "").strip()
    if not pid or not pname:
        raise HTTPException(400, "product_id ve product_name gerekli")
    rules = []
    for r in (payload.get("rules") or [])[:50]:
        if isinstance(r, dict):
            q = (r.get("q") or "").strip()
            a = (r.get("a") or "").strip()
            if a:
                rules.append({"q": q, "a": a})
    doc = {
        "product_id": pid,
        "product_name": pname,
        "instructions": (payload.get("instructions") or "").strip()[:4000],
        "rules": rules,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user.get("email", ""),
    }
    await db.product_qa_rules.update_one({"product_id": pid}, {"$set": doc}, upsert=True)
    return {"success": True, "product_id": pid, "rules_count": len(rules)}


@router.delete("/product-qa-rules/{product_id}")
async def delete_product_qa_rule(product_id: str, current_user: dict = Depends(require_admin)):
    res = await db.product_qa_rules.delete_one({"product_id": str(product_id)})
    return {"success": True, "deleted": res.deleted_count}


@router.post("/kb/backfill-marketplace")
async def backfill_kb_from_marketplace(payload: Optional[dict] = None, current_user: dict = Depends(require_admin)):
    """Geçmişteki TÜM cevaplanmış TY/HB/Temu sorularını Bilgi Bankası'na aktarır (tek tıkla eğit).
    Aynı soru-cevap zaten varsa atlar (idempotent)."""
    colls = {"trendyol": "trendyol_questions", "hepsiburada": "hepsiburada_questions", "temu": "temu_questions"}
    added = 0
    scanned = 0
    skipped = 0
    for mp, cname in colls.items():
        try:
            cur = db[cname].find(
                {"answer": {"$nin": ["", None]}},
                {"_id": 0, "question_text": 1, "question": 1, "answer": 1, "product_name": 1, "question_id": 1},
            )
            async for q in cur:
                scanned += 1
                qt = (q.get("question_text") or q.get("question") or "").strip()
                at = (q.get("answer") or "").strip()
                if not qt or not at:
                    skipped += 1
                    continue
                exists = await db.ai_knowledge_base.find_one(
                    {"question": qt, "answer": at}, {"_id": 0, "id": 1}
                )
                if exists:
                    skipped += 1
                    continue
                await db.ai_knowledge_base.insert_one({
                    "id": str(uuid.uuid4()),
                    "question": qt,
                    "answer": at,
                    "channel": mp,
                    "source_question_id": q.get("question_id"),
                    "tags": ["backfill", mp],
                    "product_name": q.get("product_name", ""),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": current_user.get("email", "backfill"),
                    "usage_count": 0,
                })
                added += 1
        except Exception as e:
            logger.warning(f"[kb-backfill] {mp} atlandı: {e}")
    return {"success": True, "scanned": scanned, "added": added, "skipped": skipped}
