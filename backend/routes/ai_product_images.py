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
import os
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

# ── Kalite iyileştirme (Gemini) ─────────────────────────────────────────
# Tedarikçi görselleri 630x840 ve sıkıştırma izleri taşıyor; vitrinde
# büyütülünce dağılıyor. Gemini görseli yeniden çizerek netleştiriyor, sonra
# tam hedef ölçüye Lanczos ile oturtuluyor.
_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "{model}:generateContent")
_DEFAULT_ENHANCE_MODEL = "gemini-2.5-flash-image"
ENHANCE_TARGET = (1200, 1800)         # 2:3 dikey — moda kadrajı
_DEFAULT_ENHANCE_PROMPT = (
    "Bu ürün fotoğrafının ÇÖZÜNÜRLÜĞÜNÜ ve netliğini artır. Görüntüyü YENİDEN "
    "YORUMLAMA: kadraj, kompozisyon, duruş, arka plan, ışık ve renkler birebir "
    "aynı kalsın. Yalnız sıkıştırma bozulmalarını, gürültüyü ve bulanıklığı "
    "temizle; kumaş dokusunu, dikiş ve düğme detaylarını, desen çizgilerini ve "
    "yazıları netleştir. Ürüne hiçbir öğe ekleme, mevcut hiçbir öğeyi çıkarma, "
    "deseni veya rengi değiştirme. Ten rengini ve model yüzünü değiştirme. "
    "Yazı, logo, filigran veya çerçeve ekleme. Çıktı dikey 2:3 oranında olsun."
)
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


async def _store(raw: bytes, product_id: str, pose: str,
                 content_type: str = "image/png") -> str:
    """Üretilen görseli kalıcı depoya koyar ve public adresini döndürür.

    R2 kapalıysa ÜRETİM DÜŞMEZ: sistemin geri kalanı (routes/upload.py) zaten
    disk + veritabanı yedeğine düşüyor, burada da aynı yol izleniyor. Önceden
    503 atılıyordu ve R2 kurulmadan tek bir görsel bile üretilemiyordu — üstelik
    hata beş pozda birden çıkıyor, üretim ücreti boşa gidiyordu.
    """
    uzanti = "jpg" if content_type == "image/jpeg" else "png"
    filename = f"{product_id}-{pose}-{uuid.uuid4().hex[:8]}.{uzanti}"
    kayit = {
        "id": str(uuid.uuid4()),
        "storage_path": filename,
        "content_type": content_type,
        "size": len(raw),
        "source": "ai_generated",
        "product_id": product_id,
        "pose": pose,
        "is_deleted": False,
        "created_at": _now_iso(),
    }

    if r2.is_enabled():
        key = f"ai-products/{filename}"
        url = r2.put_object(key, raw, content_type)
        kayit.update({"r2_key": key, "r2_url": url})
    else:
        # upload.py ile AYNI yol ve aynı servis ucu: /api/upload/files/<ad>.
        # Diske yazamasak bile base64 kopya veritabanında durur, görsel yine servis edilir.
        try:
            from .upload import UPLOAD_DIR
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as fh:
                fh.write(raw)
        except Exception as e:
            logger.warning(f"[ai-images] diske yazilamadi ({e}); base64 kopyayla devam")
        kayit["data_b64"] = base64.b64encode(raw).decode("ascii")
        url = f"{await _public_base()}/api/upload/files/{filename}"

    await db.files.insert_one(kayit)
    return url


async def _public_base() -> str:
    """Görsel adreslerinin taban adresi. BEYAZ ETİKET: koda gömülmez."""
    base = (os.environ.get("PUBLIC_API_URL") or os.environ.get("API_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    try:
        from company import get_site_url
        site = await get_site_url(db)
        # storefront meluni.com.tr ise API api.meluni.com.tr
        return site.replace("://www.", "://api.").replace("://", "://api.", 1) \
            if "://api." not in site else site
    except Exception:
        return ""


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
                        f"'{_DEFAULT_SIZE}' kullanıldı ve ayar düzeltildi.")
        logger.warning(f"[ai-images] gecersiz boyut ayarı duzeltildi: {size}")
        size = _DEFAULT_SIZE
        # Ayarı da düzeltiyoruz: aksi hâlde uyarı her üretimde tekrar çıkar ve
        # kullanıcı panele girip elle düzeltene kadar gürültü olarak kalır.
        await db.settings.update_one({"id": SETTINGS_ID},
                                     {"$set": {"size": _DEFAULT_SIZE}}, upsert=True)
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


# ─────────────────────── kalite iyileştirme ───────────────────────
async def _gemini_key() -> str:
    cfg = await get_settings()
    enc = cfg.get("gemini_key_enc")
    if not enc:
        raise HTTPException(
            status_code=400,
            detail="Gemini API anahtarı tanımlı değil. Ayarlar > Görsel Üretimi'nden girin.")
    return decrypt(enc)


def _fit_target(raw: bytes, target=ENHANCE_TARGET) -> bytes:
    """Görseli tam hedef ölçüye oturtur.

    Kaynak oran hedeften farklıysa (tedarikçi görselleri 3:4, hedef 2:3) KIRPMAK
    ürünün eteğini/paçasını kesebilir. Bu yüzden oran korunarak sığdırılıp
    boşluk, görselin kenar renginden alınan düz zeminle doldurulur — böylece
    ekleme yapılmadan kadraj tamamlanır.
    """
    from PIL import Image
    import io

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    tw, th = target
    if (im.width, im.height) == (tw, th):
        out = io.BytesIO()
        im.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
        return out.getvalue()

    scale = min(tw / im.width, th / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((nw, nh), Image.LANCZOS)

    # Zemin rengi: üst kenarın ortalaması — stüdyo fotoğraflarında arka plan.
    kenar = im.crop((0, 0, im.width, max(1, im.height // 40))).resize((1, 1), Image.LANCZOS)
    zemin = kenar.getpixel((0, 0))

    tuval = Image.new("RGB", (tw, th), zemin)
    tuval.paste(im, ((tw - nw) // 2, (th - nh) // 2))
    out = io.BytesIO()
    tuval.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
    return out.getvalue()


def _gemini_hata(resp) -> str:
    """Sağlayıcı hatasını okunur hâle getirir.

    Ham JSON'u ekrana basmak beş kutuyu aynı İngilizce yığınla dolduruyor ve
    asıl mesele (kota mı, anahtar mı, içerik reddi mi) kaybolyordu.
    """
    try:
        msg = ((resp.json() or {}).get("error") or {}).get("message") or ""
    except Exception:
        msg = (resp.text or "")[:200]
    kod = resp.status_code
    if kod == 429:
        return ("Gemini kotası/kredisi tükenmiş. AI Studio'da projenin "
                "faturalandırmasını açın veya kredi yükleyin. (Google: " + msg[:120] + ")")
    if kod in (401, 403):
        return "Gemini API anahtarı geçersiz veya bu modele yetkisi yok."
    if kod == 400 and "safety" in msg.lower():
        return "Gemini görseli güvenlik gerekçesiyle işlemedi."
    return f"Gemini hatası ({kod}): {msg[:200] or 'ayrıntı yok'}"


async def _gemini_enhance(client: httpx.AsyncClient, api_key: str, model: str,
                          prompt: str, raw: bytes) -> Optional[bytes]:
    """Görseli Gemini'ye gönderip iyileştirilmiş sürümü döndürür.

    Model görsel döndürmezse None döner — çağıran taraf o zaman yalnız yeniden
    ölçekleme yapar. Sessizce başarısız olmuyoruz; sonuçta hangi görselin
    modelden geçtiği bildiriliyor.
    """
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(raw).decode()}},
            ]
        }],
    }
    resp = await client.post(
        _GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=_gemini_hata(resp))
    data = resp.json()
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


async def enhance_product_images(product_id: str, *, indexes: Optional[List[int]] = None,
                                 replace: bool = False) -> Dict[str, Any]:
    """Ürün görsellerini iyileştirip 1200x1800 olarak yeniden üretir."""
    product = await db.products.find_one({"id": product_id},
                                         {"_id": 0, "id": 1, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")
    images = [u for u in (product.get("images") or []) if u]
    if not images:
        raise HTTPException(status_code=400, detail="Üründe görsel yok.")

    secili = [i for i in (indexes or range(len(images))) if 0 <= i < len(images)]
    if not secili:
        raise HTTPException(status_code=400, detail="Geçerli görsel seçilmedi.")

    cfg = await get_settings()
    api_key = await _gemini_key()
    model = cfg.get("enhance_model") or _DEFAULT_ENHANCE_MODEL
    prompt = cfg.get("enhance_prompt") or _DEFAULT_ENHANCE_PROMPT

    sonuc: List[Dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        async def tek(i: int):
            raw = None
            try:
                raw = await _fetch_image(client, images[i])
                if not raw:
                    return {"index": i, "error": "kaynak görsel indirilemedi"}
                iyilestirilmis = None
                uyari = ""
                try:
                    iyilestirilmis = await _gemini_enhance(
                        client, api_key, model, prompt, raw)
                except HTTPException as e:
                    # Model erişilemiyor (kota/kredi/yetki). Bu, işi TAMAMEN
                    # düşürmek için sebep değil: görseli yine de hedef ölçüye
                    # büyütüyoruz. Kullanıcı en azından 1200x1800 alır ve
                    # kutuda "yalnız ölçek" etiketiyle sebebi görür.
                    uyari = str(e.detail)
                son = _fit_target(iyilestirilmis or raw)
                url = await _store(son, product_id, f"hd{i}", "image/jpeg")
                return {"index": i, "url": url, "ai": bool(iyilestirilmis),
                        "note": uyari, "source": images[i]}
            except Exception as e:
                return {"index": i, "error": str(e)}

        sonuc = list(await asyncio.gather(*[tek(i) for i in secili]))

    basarili = [r for r in sonuc if r.get("url")]
    if replace and basarili:
        yeni = list(images)
        for r in basarili:
            yeni[r["index"]] = r["url"]
        await db.products.update_one({"id": product_id}, {"$set": {
            "images": yeni,
            "images_enhanced_at": _now_iso(),
        }})

    return {
        "product_id": product_id,
        "target": f"{ENHANCE_TARGET[0]}x{ENHANCE_TARGET[1]}",
        "results": sonuc,
        "replaced": bool(replace and basarili),
        "ai_count": sum(1 for r in basarili if r.get("ai")),
        "warning": ("" if all(r.get("ai") for r in basarili) else
                    (next((r["note"] for r in basarili if r.get("note")), "")
                     or "Bazı görseller modelden geçmedi; yalnız yeniden ölçeklendi.")),
    }


# ─────────────────────────── uçlar ───────────────────────────
@router.get("/settings")
async def read_settings(_admin=Depends(require_admin)):
    cfg = await get_settings()
    return {
        "configured": bool(cfg.get("api_key_enc")),
        "gemini_configured": bool(cfg.get("gemini_key_enc")),
        "enhance_model": cfg.get("enhance_model") or _DEFAULT_ENHANCE_MODEL,
        "enhance_prompt": cfg.get("enhance_prompt") or _DEFAULT_ENHANCE_PROMPT,
        "enhance_target": f"{ENHANCE_TARGET[0]}x{ENHANCE_TARGET[1]}",
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
    if payload.get("gemini_key"):
        update["gemini_key_enc"] = encrypt(str(payload["gemini_key"]).strip())
    if payload.get("enhance_model"):
        update["enhance_model"] = str(payload["enhance_model"]).strip()
    if payload.get("enhance_prompt") is not None:
        update["enhance_prompt"] = str(payload["enhance_prompt"])
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


@router.post("/products/{product_id}/enhance")
async def enhance(product_id: str, payload: dict | None = None,
                  _admin=Depends(require_admin)):
    """Ürün görsellerini Gemini ile netleştirip 1200x1800 üretir.

    body: {indexes?: [0,2], replace?: bool}
    `replace` verilmezse üretilenler yalnız döndürülür, ürün değişmez.
    """
    p = payload or {}
    idx = p.get("indexes")
    return await enhance_product_images(
        product_id,
        indexes=[int(i) for i in idx] if isinstance(idx, list) else None,
        replace=bool(p.get("replace")),
    )
