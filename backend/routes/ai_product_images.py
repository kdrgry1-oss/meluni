"""
ai_product_images.py — Ürün görsellerini yapay zekâ ile yeniden üretir.

Neden: tedarikçi görselleri onlarca bayi tarafından aynı anda kullanılıyor.
Aynı fotoğrafla piyasada yer almak markayı görünmez kılıyor. Bu modül, ürün
kartındaki mevcut görsellerden ve verilen referans görselden beslenerek
ürünün BEŞ farklı açıdan kendi fotoğrafını üretir:

    ön · sol · sağ · arka · detay

Tek istek beş görseli birden üretir (tek tuş). Pozlar eşzamanlı çalışır.

Prompt'lar KODA GÖMÜLÜ DEĞİL: veritabanında tutulur, panelden düzenlenir.
Sonuç beğenilmezse prompt değiştirilip yeniden üretilir, kod elden geçmez.
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .deps import db, logger, require_admin
from security.crypto import encrypt, decrypt
from services import r2_storage as r2

router = APIRouter(prefix="/ai-images", tags=["ai-product-images"])

SETTINGS_ID = "ai_images"
_OPENAI_EDIT_URL = "https://api.openai.com/v1/images/edits"
_DEFAULT_MODEL = "gpt-image-1"
_DEFAULT_SIZE = "1024x1536"          # moda ürünlerinde dikey kadraj
# Sağlayıcının kabul ettiği boyutlar. Serbest metin bırakmak, panele yazılan
# her değeri üretim anında 400 ile geri çeviriyordu ("Invalid size '1200x1800'")
# — hata beş pozda birden çıkıyor ve sebebi ekranda anlaşılmıyordu.
ALLOWED_SIZES = ("1024x1024", "1024x1536", "1536x1024", "auto")
ALLOWED_MODELS = ("gpt-image-1", "gpt-image-1-mini")
_MAX_REF_IMAGES = 6                   # API'ye gönderilecek referans üst sınırı
_TIMEOUT = 300.0                      # görsel üretimi yavaştır

# Ortak kural: ürünün KENDİSİ değişmemeli. Model bunu her pozda tekrar görmeli,
# yoksa deseni/rengi "iyileştirmeye" çalışıp ürünü başka bir ürüne çevirir.
_COMMON_RULES = (
    "Ürünün kimliğini birebir koru: kesim, dikiş yerleri, desen yönü ve tekrarı, "
    "renk tonu, kumaş dokusu, düğme/fermuar/aksesuar detayları ve etiket konumu "
    "aynı kalmalı. Ürüne yeni öğe ekleme, mevcut öğeyi çıkarma, deseni yeniden "
    "yorumlama. Referans görseldeki stüdyo ortamını, ışığı ve zemin rengini koru. "
    "Gerçekçi tekstil dokusu, doğal gölge, yüksek çözünürlük. Yazı, logo, filigran "
    "veya çerçeve ekleme."
)

DEFAULT_PROMPTS: Dict[str, str] = {
    "front": (
        "Ürünün TAM ÖNDEN çekilmiş fotoğrafını üret. Kamera göğüs hizasında, "
        "ürün kadrajın merkezinde ve tamamen görünür. Duruş simetrik ve dik. "
        + _COMMON_RULES
    ),
    "left": (
        "Ürünün SOL YANDAN (90 derece sol profil) çekilmiş fotoğrafını üret. "
        "Ürünün sol dış hattı, kol/yan dikişi ve düşüşü net görünsün. "
        "Aynı stüdyo ortamı ve aynı mesafe. " + _COMMON_RULES
    ),
    "right": (
        "Ürünün SAĞ YANDAN (90 derece sağ profil) çekilmiş fotoğrafını üret. "
        "Sol profille simetrik kadraj ve aynı ışık kullan. " + _COMMON_RULES
    ),
    "back": (
        "Ürünün TAM ARKADAN çekilmiş fotoğrafını üret. Arka kesim, sırt dikişi, "
        "varsa arka fermuar/bağcık ve etek/paça bitişi net görünsün. Ön yüzdeki "
        "desen mantığıyla tutarlı olsun. " + _COMMON_RULES
    ),
    "detail": (
        "Ürünün YAKIN PLAN DETAY fotoğrafını üret. Kumaş dokusu, örgü/dokuma yapısı, "
        "dikiş kalitesi ve varsa düğme/fermuar/işleme detayı belirgin olsun. "
        "Sığ alan derinliği, makro benzeri netlik. " + _COMMON_RULES
    ),
}

POSE_ORDER = ["front", "left", "right", "back", "detail"]
POSE_LABELS = {
    "front": "Ön", "left": "Sol", "right": "Sağ",
    "back": "Arka", "detail": "Detay",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_settings() -> Dict[str, Any]:
    return await db.settings.find_one({"id": SETTINGS_ID}, {"_id": 0}) or {}


async def _api_key() -> str:
    cfg = await get_settings()
    enc = cfg.get("api_key_enc")
    if not enc:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API anahtarı tanımlı değil. Ayarlar > Görsel Üretimi'nden girin.")
    return decrypt(enc)


async def get_prompts() -> Dict[str, str]:
    """Panelden düzenlenmiş prompt'lar; eksik poz varsayılana düşer."""
    saved = (await get_settings()).get("prompts") or {}
    return {pose: (saved.get(pose) or DEFAULT_PROMPTS[pose]) for pose in POSE_ORDER}


async def _fetch_image(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    try:
        resp = await client.get(url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > 20 * 1024 * 1024:
            logger.warning(f"[ai-images] referans görsel çok büyük, atlandı: {url}")
            return None
        return resp.content
    except Exception as e:
        logger.warning(f"[ai-images] referans görsel indirilemedi ({url}): {e}")
        return None


async def _generate_one(
    client: httpx.AsyncClient, api_key: str, model: str, size: str,
    prompt: str, images: List[bytes],
) -> bytes:
    """Tek poz üretir ve ham görsel baytlarını döndürür."""
    files = []
    for idx, raw in enumerate(images):
        # gpt-image-1 birden fazla referans görseli kabul eder; hepsi image[] altında.
        files.append(("image[]", (f"ref{idx}.png", raw, "image/png")))
    data = {"model": model, "prompt": prompt, "n": "1", "size": size}

    resp = await client.post(
        _OPENAI_EDIT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        data=data, files=files, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        # Sağlayıcı hatasını olduğu gibi yukarı taşı — "üretilemedi" demek yerine
        # nedenini göster (kota, içerik reddi, geçersiz anahtar).
        detail = resp.text[:400]
        raise HTTPException(status_code=502, detail=f"Görsel üretilemedi ({resp.status_code}): {detail}")

    payload = resp.json()
    items = payload.get("data") or []
    if not items:
        raise HTTPException(status_code=502, detail="Sağlayıcı boş yanıt döndü.")
    b64 = items[0].get("b64_json")
    if b64:
        return base64.b64decode(b64)
    url = items[0].get("url")
    if url:
        got = await _fetch_image(client, url)
        if got:
            return got
    raise HTTPException(status_code=502, detail="Yanıtta görsel verisi yok.")


async def _store(raw: bytes, product_id: str, pose: str) -> str:
    """Üretilen görseli kalıcı depoya koyar ve public adresini döndürür."""
    filename = f"{product_id}-{pose}-{uuid.uuid4().hex[:8]}.png"
    key = f"ai-products/{filename}"
    if r2.is_enabled():
        url = r2.put_object(key, raw, "image/png")
    else:
        raise HTTPException(
            status_code=503,
            detail="Görsel depolama (R2) kapalı; üretilen görsel kaydedilemedi.")
    await db.files.insert_one({
        "id": str(uuid.uuid4()),
        "storage_path": filename,
        "r2_key": key,
        "r2_url": url,
        "content_type": "image/png",
        "size": len(raw),
        "source": "ai_generated",
        "product_id": product_id,
        "pose": pose,
        "is_deleted": False,
        "created_at": _now_iso(),
    })
    return url


# ─────────────────────────── üretim ───────────────────────────
async def regenerate_for_product(
    product_id: str, *, reference_url: str = "", poses: Optional[List[str]] = None,
    attach: bool = False,
) -> Dict[str, Any]:
    """Ürün için istenen pozları üretir.

    Referans olarak: verilen reference_url + ürün kartındaki mevcut görseller
    birlikte gönderilir; model böylece ürünü her açıdan tanır.
    """
    poses = [p for p in (poses or POSE_ORDER) if p in DEFAULT_PROMPTS] or POSE_ORDER

    product = await db.products.find_one({"id": product_id}, {"_id": 0, "id": 1, "name": 1, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")

    cfg = await get_settings()
    api_key = await _api_key()
    model = cfg.get("model") or _DEFAULT_MODEL
    size = cfg.get("size") or _DEFAULT_SIZE
    # Doğrulama eklenmeden önce kaydedilmiş geçersiz bir boyut, üretimi beş
    # pozda birden düşürüyor. Sessizce varsayılana düşüp uyarıyı sonuca koyuyoruz.
    size_warning = ""
    if size not in ALLOWED_SIZES:
        size_warning = (f"Kayıtlı boyut '{size}' sağlayıcı tarafından kabul edilmiyor; "
                        f"'{_DEFAULT_SIZE}' kullanıldı. Ayarlardan düzeltin.")
        logger.warning(f"[ai-images] gecersiz boyut ayarı: {size}")
        size = _DEFAULT_SIZE
    prompts = await get_prompts()

    # Referans görselleri topla: önce kullanıcının verdiği, sonra ürünün kendi
    # görselleri. Sıra önemli — ilk görsel modele en güçlü sinyali verir.
    source_urls: List[str] = []
    if reference_url:
        source_urls.append(reference_url)
    source_urls += [u for u in (product.get("images") or []) if u and u not in source_urls]
    source_urls = source_urls[:_MAX_REF_IMAGES]
    if not source_urls:
        raise HTTPException(status_code=400,
                            detail="Referans görsel yok. Ürüne görsel ekleyin veya referans adresi verin.")

    async with httpx.AsyncClient() as client:
        fetched = await asyncio.gather(*[_fetch_image(client, u) for u in source_urls])
        images = [b for b in fetched if b]
        if not images:
            raise HTTPException(status_code=502, detail="Referans görsellerin hiçbiri indirilemedi.")

        async def one(pose: str):
            try:
                raw = await _generate_one(client, api_key, model, size, prompts[pose], images)
                return pose, await _store(raw, product_id, pose), None
            except HTTPException as e:
                return pose, None, str(e.detail)
            except Exception as e:
                return pose, None, str(e)

        # Pozlar eşzamanlı — beş görsel sırayla değil paralel üretilir.
        results = await asyncio.gather(*[one(p) for p in poses])

    generated: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for pose, url, err in results:
        if url:
            generated[pose] = url
        else:
            errors[pose] = err or "bilinmeyen hata"

    if attach and generated:
        # Mevcut görselleri SİLMEDEN sona ekle — beğenilmezse geri dönülebilsin.
        await db.products.update_one(
            {"id": product_id},
            {"$push": {"images": {"$each": [generated[p] for p in POSE_ORDER if p in generated]}},
             "$set": {"ai_images_generated_at": _now_iso()}})

    await db.ai_image_jobs.insert_one({
        "id": str(uuid.uuid4()), "product_id": product_id,
        "poses": poses, "generated": generated, "errors": errors,
        "model": model, "size": size, "created_at": _now_iso(),
    })

    return {"product_id": product_id, "generated": generated,
            "errors": errors, "attached": bool(attach and generated),
            "warning": size_warning,
            "labels": {p: POSE_LABELS[p] for p in poses}}


# ─────────────────────────── uçlar ───────────────────────────
@router.get("/settings")
async def read_settings(_admin=Depends(require_admin)):
    cfg = await get_settings()
    return {
        "configured": bool(cfg.get("api_key_enc")),
        "model": cfg.get("model") or _DEFAULT_MODEL,
        "size": cfg.get("size") or _DEFAULT_SIZE,
        "prompts": await get_prompts(),
        "pose_order": POSE_ORDER,
        "pose_labels": POSE_LABELS,
        "allowed_sizes": list(ALLOWED_SIZES),
        "allowed_models": list(ALLOWED_MODELS),
    }


@router.put("/settings")
async def write_settings(payload: dict, _admin=Depends(require_admin)):
    """API anahtarı, model, boyut ve poz prompt'ları."""
    update: Dict[str, Any] = {}
    if payload.get("api_key"):
        update["api_key_enc"] = encrypt(str(payload["api_key"]).strip())
    if payload.get("model"):
        _m = str(payload["model"]).strip()
        if _m not in ALLOWED_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Geçersiz model: {_m}. Kabul edilenler: {', '.join(ALLOWED_MODELS)}")
        update["model"] = _m
    if payload.get("size"):
        _sz = str(payload["size"]).strip()
        if _sz not in ALLOWED_SIZES:
            raise HTTPException(
                status_code=400,
                detail=f"Geçersiz boyut: {_sz}. Kabul edilenler: {', '.join(ALLOWED_SIZES)}")
        update["size"] = _sz
    if isinstance(payload.get("prompts"), dict):
        prompts = {}
        for pose, text in payload["prompts"].items():
            if pose not in DEFAULT_PROMPTS:
                raise HTTPException(status_code=400, detail=f"Bilinmeyen poz: {pose}")
            prompts[pose] = str(text)
        update["prompts"] = {**((await get_settings()).get("prompts") or {}), **prompts}
    if not update:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok.")
    update["updated_at"] = _now_iso()
    await db.settings.update_one({"id": SETTINGS_ID}, {"$set": update}, upsert=True)
    return {"success": True}


@router.post("/prompts/reset")
async def reset_prompts(_admin=Depends(require_admin)):
    """Prompt'ları fabrika varsayılanına döndürür."""
    await db.settings.update_one({"id": SETTINGS_ID},
                                 {"$set": {"prompts": dict(DEFAULT_PROMPTS),
                                           "updated_at": _now_iso()}}, upsert=True)
    return {"success": True, "prompts": DEFAULT_PROMPTS}


@router.post("/products/{product_id}/regenerate")
async def regenerate(product_id: str, payload: dict | None = None, _admin=Depends(require_admin)):
    """Tek istekle beş görseli üretir.

    body: {reference_url?, poses?: ["front","left",...], attach?: bool}
    """
    p = payload or {}
    return await regenerate_for_product(
        product_id,
        reference_url=str(p.get("reference_url") or "").strip(),
        poses=p.get("poses"),
        attach=bool(p.get("attach")),
    )
