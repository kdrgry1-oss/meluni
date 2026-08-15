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
import re
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
# ÇIKTI ÖLÇÜSÜ — VİTRİNLE AYNI ORAN OLMAK ZORUNDA.
# Önce 1200x1800 (2:3) üretiliyordu. Oysa vitrindeki görsel kutularının hepsi
# 3:4 (`aspect-[3/4]`, on ayrı yerde) ve `object-contain` kullanıyor: 2:3 bir
# görsel 3:4 kutuya sığdırılınca yanlarda boşluk kalıyor, ürün sayfası çerçeveli
# duruyordu.
#
# Oranı VİTRİNE eşitliyoruz, tersini değil: tedarikçi görselleri de zaten 3:4
# (630x840). Böylece iyileştirme sırasında dolgu/gerdirme HİÇ gerekmiyor —
# görsel yalnız büyütülüyor, en temiz sonuç bu. Vitrini 2:3'e çevirmek ise
# iyileştirilmemiş binlerce ürünü boşluklu gösterirdi.
ENHANCE_TARGET = (1200, 1600)         # 3:4 dikey — vitrindeki kutularla aynı
_DEFAULT_ENHANCE_PROMPT = (
    "Bu ürün fotoğrafının ÇÖZÜNÜRLÜĞÜNÜ ve netliğini artır. Görüntüyü YENİDEN "
    "YORUMLAMA: kadraj, kompozisyon, duruş, arka plan, ışık ve renkler birebir "
    "aynı kalsın. Yalnız sıkıştırma bozulmalarını, gürültüyü ve bulanıklığı "
    "temizle; kumaş dokusunu, dikiş ve düğme detaylarını, desen çizgilerini ve "
    "yazıları netleştir. Ürüne hiçbir öğe ekleme, mevcut hiçbir öğeyi çıkarma, "
    "deseni veya rengi değiştirme. Ten rengini ve model yüzünü değiştirme. "
    "Yazı, logo, filigran veya çerçeve ekleme. Çıktı dikey 2:3 oranında olsun."
)

# ── Ölçü tablosu ─────────────────────────────────────────────────────────
# Tedarikçi görsellerinin arasında beden/ölçü tablosu da geliyor ve çoğu
# İngilizce. Ürün fotoğrafı gibi "netleştir" demek yanlış: bu bir belge, metin
# taşıyor. Ayrı bir metinle işlenir — çevrilir, yeniden dizilir.
_DEFAULT_CLASSIFY_MODEL = "gemini-2.5-flash"
_DEFAULT_SIZE_CHART_PROMPT = (
    "Bu bir BEDEN/ÖLÇÜ TABLOSU görseli. Aynı tabloyu TÜRKÇE olarak, temiz ve "
    "yüksek çözünürlüklü biçimde yeniden çiz.\n"
    "SAYILAR KUTSALDIR: tablodaki her ölçü, beden adı ve rakam BİREBİR aynı "
    "kalmalı. Hiçbir sayıyı yuvarlama, düzeltme, tahmin etme veya ekleme; "
    "okuyamadığın bir hücreyi olduğu gibi bırak.\n"
    "Yalnız BAŞLIKLARI Türkçeye çevir: Size→Beden, Bust/Chest→Göğüs, "
    "Waist→Bel, Hip/Hips→Kalça, Length→Boy, Shoulder→Omuz, Sleeve→Kol Boyu, "
    "Inseam→İç Bacak, Thigh→Uykuk, Height→Boy, Weight→Kilo, cm→cm.\n"
    "Düzen: beyaz zemin, koyu gri metin, ince ızgara çizgileri, satırlar rahat "
    "okunacak aralıkta. Marka adı, logo, filigran veya süsleme EKLEME. "
    "Çıktı dikey 2:3 oranında, tablo ortalanmış olsun."
)
_CLASSIFY_PROMPT = (
    "Bu görsel bir giyim ürününün fotoğrafı mı, yoksa beden/ölçü tablosu "
    "(ölçü çizelgesi, size chart) mı? Yalnız tek kelime yanıtla: "
    "'urun' veya 'olcu'."
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


def _gorsel_url(x) -> str:
    """Görsel listesindeki bir kaydın adresi.

    Liste TEK BİÇİMDE DEĞİL: bazı görseller düz adres, ölçü tablosu görselleri
    ise {url, is_size_table} sözlüğü olarak duruyor (size_tables.py böyle
    yazıyor). Sözlüğü adres sanmak indirmeyi ve kıyaslamayı bozuyordu.
    """
    if isinstance(x, dict):
        return str(x.get("url") or "")
    return str(x or "")


def _gorsel_degistir(eski, yeni_url: str):
    """Adresi değiştirir, sözlükteki işaretleri (is_size_table, alt) KORUR.

    Düz adresle üzerine yazmak ölçü tablosu işaretini siliyor, tablo müşteri
    galerisine sızıyordu.
    """
    if isinstance(eski, dict):
        return {**eski, "url": yeni_url}
    return yeni_url


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
             "$set": {"ai_images_generated_at": _now_iso(), "images_custom": True}})

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
# ── Ölçü tablosunu KENDİMİZ çizeriz ──────────────────────────────────────
# Modele "bu tabloyu Türkçe yeniden çiz" demek iki riski birden taşıyor:
# başlıkları serbestçe çeviriyor (hatalı karşılıklar) ve SAYILARI yeniden
# yazıyor — bir rakam kayarsa müşteri yanlış beden alır. Bu yüzden modelin
# işi yalnız OKUMAK; çizimi Pillow ile biz yapıyoruz. Böylece başlık çevirisi
# sabit bir sözlükten gelir, sayılar okunduğu gibi basılır, tedarikçinin
# manken silueti ve kalabalık düzeni de tabloya girmez.
_OLCU_BASLIK_TR = {
    "size": "Beden", "beden": "Beden", "bust": "Göğüs", "chest": "Göğüs",
    "gogus": "Göğüs", "göğüs": "Göğüs", "waist": "Bel", "bel": "Bel",
    "hip": "Kalça", "hips": "Kalça", "kalca": "Kalça", "kalça": "Kalça",
    "length": "Boy", "boy": "Boy", "shoulder": "Omuz", "omuz": "Omuz",
    "sleeve": "Kol Boyu", "sleeve length": "Kol Boyu", "kol": "Kol Boyu",
    "kol boyu": "Kol Boyu", "inseam": "İç Bacak", "thigh": "Uyluk",
    "height": "Boy", "weight": "Kilo", "arm": "Kol", "neck": "Boyun",
    "bottom length": "Alt Boy", "top length": "Üst Boy",
}


def _baslik_tr(ad: str) -> str:
    """Ölçü başlığını Türkçeleştirir; tanımadığını olduğu gibi bırakır."""
    t = re.sub(r"\s*\(.*?\)\s*", " ", str(ad or "")).strip().lower()
    t = t.replace("ı", "i")
    return _OLCU_BASLIK_TR.get(t, str(ad or "").strip().title())


def _render_size_table(adlar: List[str], satirlar: List[Dict[str, Any]],
                       baslik: str = "BEDEN ÖLÇÜLERİ",
                       hedef=ENHANCE_TARGET) -> bytes:
    """Okunan ölçülerden temiz bir tablo görseli üretir (1200x1800)."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    W, H = hedef
    BEYAZ, KOYU, GRI, CIZGI = (255, 255, 255), (26, 26, 26), (110, 110, 110), (222, 222, 222)
    im = Image.new("RGB", (W, H), BEYAZ)
    d = ImageDraw.Draw(im)

    yol = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    kalin_yol = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def font(p, kalin=False):
        try:
            return ImageFont.truetype(kalin_yol if kalin else yol, p)
        except Exception:
            return ImageFont.load_default()

    kolonlar = ["Beden"] + [_baslik_tr(a) for a in adlar]
    n_kol = len(kolonlar)
    n_sat = len(satirlar)
    if n_kol < 2 or n_sat < 1:
        raise HTTPException(status_code=400, detail="Tabloda gösterilecek veri yok.")

    kenar = 90
    tablo_g = W - 2 * kenar
    kol_g = tablo_g / n_kol
    # Satır yüksekliği okunur sınırlar içinde; sonra tüm blok DİKEY ORTALANIR.
    # Sabit bir üst boşluk bırakmak tabloyu sayfanın tepesine yığıyor, altta
    # yarım sayfa boşluk kalıyordu.
    sat_y = max(52, min(104, int((H - 620) / (n_sat + 1))))
    blok_y = 150 + (n_sat + 1) * sat_y             # başlık + tablo
    tepe = max(90, int((H - blok_y) / 2))

    d.text((kenar, tepe), baslik, font=font(52, True), fill=KOYU)
    d.text((kenar, tepe + 72), "Ölçüler santimetre (cm) cinsindendir.",
           font=font(28), fill=GRI)
    ust = tepe + 150

    # Başlık satırı
    d.rectangle([kenar, ust, W - kenar, ust + sat_y], fill=(244, 244, 244))
    bf = font(min(30, int(sat_y * 0.42)), True)
    for i, ad in enumerate(kolonlar):
        x = kenar + i * kol_g + kol_g / 2
        kutu = d.textbbox((0, 0), ad, font=bf)
        d.text((x - (kutu[2] - kutu[0]) / 2, ust + sat_y / 2 - (kutu[3] - kutu[1]) / 2 - kutu[1]),
               ad, font=bf, fill=KOYU)

    sf = font(min(30, int(sat_y * 0.40)))
    y = ust + sat_y
    for r in satirlar:
        hucreler = [str(r.get("beden") or "")] + [
            str(x or "—") for x in (r.get("olculer") or [])]
        hucreler = (hucreler + ["—"] * n_kol)[:n_kol]
        for i, hucre in enumerate(hucreler):
            x = kenar + i * kol_g + kol_g / 2
            kutu = d.textbbox((0, 0), hucre, font=sf)
            d.text((x - (kutu[2] - kutu[0]) / 2, y + sat_y / 2 - (kutu[3] - kutu[1]) / 2 - kutu[1]),
                   hucre, font=sf, fill=KOYU if i == 0 else (60, 60, 60))
        y += sat_y
        d.line([kenar, y, W - kenar, y], fill=CIZGI, width=1)

    # Dış çerçeve ve dikey ayraçlar
    d.rectangle([kenar, ust, W - kenar, y], outline=CIZGI, width=2)
    for i in range(1, n_kol):
        x = kenar + i * kol_g
        d.line([x, ust, x, y], fill=CIZGI, width=1)

    out = io.BytesIO()
    im.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# ÖLÇÜ VERİSİ — ÖNCE KREDİSİZ KAYNAKLAR
#
# Tabloyu ÇİZMEK zaten tamamen bizde (Pillow). Kredi yalnız görseldeki sayıları
# OKUMAK için harcanıyor. Oysa o sayılar çoğu üründe sistemde HAZIR duruyor:
#
#   1) ürünün kendi ölçü tablosu            (size_tables)
#   2) aynı stok kodlu renk kardeşinin tablosu  (aynı ürün, başka renk)
#   3) tedarikçinin ürün açıklamasındaki tablo  (HTML tablo ya da düz metin)
#
# Üçü de model çağırmaz. Sıra güven sırasıdır: elle girilmiş/onaylanmış veri
# önce, tedarikçi metni en sonda. Hiçbiri bulunamazsa OCR'a (Gemini) düşülür;
# o da yoksa elle giriş. Emin olunamayan hiçbir sayı uydurulmaz — yanlış ölçü
# müşteriye yanlış beden aldırır.
# ---------------------------------------------------------------------------

_HTML_ETIKET = re.compile(r"<[^>]+>")
_TR_SATIR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TD_HUCRE_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
_TABLO_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.I | re.S)
_BEDEN_RE = re.compile(
    r"^(xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]\s*x\s*l|\d{1,3}(?:[-/]\d{1,3})?|"
    r"std|standart|tek\s*ebat|one\s*size|onesize|universal)$", re.I)
_SAYI_RE = re.compile(r"^\d{1,3}(?:[.,]\d{1,2})?(?:\s*[-–/]\s*\d{1,3}(?:[.,]\d{1,2})?)?$")


def _hucre_temiz(s: Any) -> str:
    import html as _html
    t = _HTML_ETIKET.sub(" ", str(s or ""))
    t = _html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _olcu_basligi_mi(s: str) -> bool:
    """Sözlükte karşılığı olan bir ölçü adı mı? (Göğüs, Bel, Kalça, Boy…)"""
    t = re.sub(r"\s*\(.*?\)\s*", " ", str(s or "")).strip().lower().replace("ı", "i")
    return bool(t) and t in _OLCU_BASLIK_TR


def _beden_mi(s: str) -> bool:
    return bool(_BEDEN_RE.fullmatch(str(s or "").strip()))


def _sayisal_mi(s: str) -> bool:
    return bool(_SAYI_RE.fullmatch(str(s or "").strip()))


def _matristen_olcu(matris: List[List[str]]) -> tuple:
    """Hücre matrisinden (adlar, satirlar) çıkarır. İki yerleşim de desteklenir:

      A) satır = beden   →  ilk sütun S/M/L, başlık satırı ölçü adları
      B) satır = ölçü    →  ilk sütun Göğüs/Bel, başlık satırı S/M/L

    Emin olunamayan matris için BOŞ döner: yanlış eşleşmiş bir tablo,
    tablo olmamasından beterdir.
    """
    matris = [r for r in matris if any(str(c).strip() for c in r)]
    if len(matris) < 2:
        return [], []
    genislik = max(len(r) for r in matris)
    matris = [(list(r) + [""] * genislik)[:genislik] for r in matris]
    basliklar = [str(c).strip() for c in matris[0]]
    govde = matris[1:]

    # A) başlık satırında ölçü adları, ilk sütunda bedenler
    if sum(_olcu_basligi_mi(c) for c in basliklar[1:]) >= 1 and \
            sum(_beden_mi(r[0]) for r in govde) >= 1:
        adlar = basliklar[1:]
        satirlar = [{"beden": str(r[0]).strip(),
                     "olculer": [str(c).strip() for c in r[1:]]}
                    for r in govde if _beden_mi(r[0])]
    # B) başlık satırında bedenler, ilk sütunda ölçü adları → devriğini al
    elif sum(_beden_mi(c) for c in basliklar[1:]) >= 2 and \
            sum(_olcu_basligi_mi(r[0]) for r in govde) >= 1:
        adlar = [str(r[0]).strip() for r in govde if _olcu_basligi_mi(r[0])]
        kaynak = [r for r in govde if _olcu_basligi_mi(r[0])]
        satirlar = []
        for i, beden in enumerate(basliklar[1:], start=1):
            if not _beden_mi(beden):
                continue
            satirlar.append({"beden": str(beden).strip(),
                             "olculer": [str(r[i]).strip() for r in kaynak]})
    else:
        return [], []

    # Değer hücrelerinin çoğu sayı olmalı; değilse bu bir ölçü tablosu değildir.
    hucreler = [c for r in satirlar for c in r["olculer"] if str(c).strip()]
    if not hucreler or not adlar:
        return [], []
    if sum(_sayisal_mi(c) for c in hucreler) < len(hucreler) * 0.6:
        return [], []
    satirlar = [r for r in satirlar if any(str(c).strip() for c in r["olculer"])]
    return ([a for a in adlar if str(a).strip()], satirlar) if satirlar else ([], [])


def _metinden_olcu(metin: str) -> tuple:
    """Düz metin açıklamadan ölçü çıkarır. İki yaygın yazım:

        Beden: S M L        /  Göğüs: 88 92 96        (etiket + değer dizisi)
        S: Göğüs 88 Bel 70  /  M: Göğüs 92 Bel 74     (beden başına satır)
    """
    satir_metin = [_hucre_temiz(x) for x in re.split(r"<br\s*/?>|\n|\r", str(metin or ""))]
    satir_metin = [s for s in satir_metin if s]

    # Yerleşim 1: "Etiket: değer değer değer"
    bedenler: List[str] = []
    olcu_satir: List[tuple] = []
    for s in satir_metin:
        m = re.match(r"^([^:]{2,20})\s*[:：]\s*(.+)$", s)
        if not m:
            continue
        etiket, kalan = m.group(1).strip(), m.group(2).strip()
        parcalar = [p for p in re.split(r"[\s/|,]+", kalan) if p]
        if re.fullmatch(r"(beden|bedenler|size|sizes)", etiket, re.I):
            if len(parcalar) >= 2 and all(_beden_mi(p) for p in parcalar):
                bedenler = parcalar
        elif _olcu_basligi_mi(etiket) and len(parcalar) >= 2 and \
                all(_sayisal_mi(p) for p in parcalar):
            olcu_satir.append((etiket, parcalar))
    if bedenler and olcu_satir:
        adlar = [e for e, _ in olcu_satir]
        satirlar = []
        for i, beden in enumerate(bedenler):
            olculer = [(v[i] if i < len(v) else "") for _, v in olcu_satir]
            if any(olculer):
                satirlar.append({"beden": beden, "olculer": olculer})
        if satirlar:
            return adlar, satirlar

    # Yerleşim 2: "S: Göğüs 88 Bel 70 Boy 120"
    adlar, satirlar = [], []
    for s in satir_metin:
        m = re.match(r"^([A-Za-zÇĞİÖŞÜçğıöşü0-9]{1,10})\s*[:：\-]\s*(.+)$", s)
        if not m or not _beden_mi(m.group(1)):
            continue
        ciftler = re.findall(r"([A-Za-zÇĞİÖŞÜçğıöşü ]{2,14}?)\s*[:：]?\s*"
                             r"(\d{1,3}(?:[.,]\d{1,2})?)", m.group(2))
        olcu = {}
        for etiket, deger in ciftler:
            etiket = etiket.strip()
            if _olcu_basligi_mi(etiket):
                olcu[etiket] = deger
                if etiket not in adlar:
                    adlar.append(etiket)
        if olcu:
            satirlar.append({"beden": m.group(1).strip(), "_map": olcu})
    if adlar and satirlar:
        return adlar, [{"beden": r["beden"],
                        "olculer": [r["_map"].get(a, "") for a in adlar]}
                       for r in satirlar]
    return [], []


def _aciklamadan_olcu(aciklama: str) -> tuple:
    """Tedarikçi açıklamasındaki ölçü tablosunu ayrıştırır (model çağırmaz)."""
    ham = str(aciklama or "")
    if not ham.strip():
        return [], []
    for govde in _TABLO_RE.findall(ham):
        matris = [[_hucre_temiz(c) for c in _TD_HUCRE_RE.findall(satir)]
                  for satir in _TR_SATIR_RE.findall(govde)]
        adlar, satirlar = _matristen_olcu([r for r in matris if r])
        if satirlar:
            return adlar, satirlar
    return _metinden_olcu(ham)


def _st_satirlara(st: Optional[Dict[str, Any]]) -> tuple:
    """`size_tables` kaydını (adlar, satirlar) biçimine çevirir.

    Kanonik biçim panelin/vitrinin kullandığı biçimdir:
    sizes=["S","M"], columns=["Göğüs"], values={"S":{"Göğüs":"88"}}.

    AMA CANLI VERİ TEK BİÇİMDE DEĞİL. Bu koleksiyon yıllar içinde birkaç
    farklı yoldan yazılmış; `values` sözlük yerine liste, satır değerleri
    sözlük yerine sıralı liste olabiliyor. Beklenmedik bir biçimde
    `'list' object has no attribute 'get'` ile patlıyordu ve ölçü tablosu
    düğmesi hiç çalışmıyordu. Ne gelirse gelsin okunur, okunamıyorsa BOŞ
    döner — patlamaz.
    """
    if not isinstance(st, dict) or not st:
        return [], []
    ham = st.get("sizes") or []
    if not isinstance(ham, list):
        return [], []
    if ham and isinstance(ham[0], dict):
        adlar = [str(a) for a in (st.get("olcu_adlari") or st.get("columns") or [])]
        satirlar = [{"beden": str(r.get("beden") or "").strip(),
                     "olculer": [str(x or "").strip() for x in (r.get("olculer") or [])]}
                    for r in ham
                    if isinstance(r, dict) and str(r.get("beden") or "").strip()]
        return adlar, [r for r in satirlar if any(r["olculer"])]
    adlar = [str(c).strip() for c in (st.get("columns") or []) if str(c).strip()]
    _ham_degerler = st.get("values")
    # `values` sözlük beklenir; liste geldiyse beden sırasına göre eşle.
    if isinstance(_ham_degerler, list):
        degerler = {str(ham[i]).strip(): v for i, v in enumerate(_ham_degerler)
                    if i < len(ham)}
    elif isinstance(_ham_degerler, dict):
        degerler = _ham_degerler
    else:
        degerler = {}
    satirlar = []
    for beden in ham:
        b = str(beden).strip()
        if not b:
            continue
        hucre = degerler.get(b) or degerler.get(str(beden)) or {}
        if isinstance(hucre, dict):
            olculer = [str(hucre.get(a, "") or "").strip() for a in adlar]
        elif isinstance(hucre, (list, tuple)):
            # Sıralı liste: kolonlarla konum konum eşleşir.
            olculer = [str(hucre[i] if i < len(hucre) else "").strip()
                       for i in range(len(adlar))]
        else:
            continue
        if any(olculer):
            satirlar.append({"beden": b, "olculer": olculer})
    return adlar, satirlar


async def _kredisiz_olcu_verisi(product_id: str) -> Dict[str, Any]:
    """Model çağırmadan ölçü verisi bulur. Bulamazsa satirlar boş döner."""
    st = await db.size_tables.find_one({"product_id": product_id}, {"_id": 0})
    adlar, satirlar = _st_satirlara(st)
    if satirlar:
        return {"olcu_adlari": adlar, "satirlar": satirlar,
                "kaynak": "kayitli", "kaynak_adi": "ürünün kayıtlı ölçü tablosu"}

    product = await db.products.find_one(
        {"id": product_id}, {"_id": 0, "stock_code": 1, "description": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")

    kod = str(product.get("stock_code") or "").strip()
    if kod:
        kardes_ids = [p["id"] async for p in db.products.find(
            {"stock_code": kod, "id": {"$ne": product_id}, "is_deleted": {"$ne": True}},
            {"_id": 0, "id": 1}) if p.get("id")]
        if kardes_ids:
            kst = await db.size_tables.find_one(
                {"product_id": {"$in": kardes_ids}}, {"_id": 0},
                sort=[("updated_at", -1)])
            adlar, satirlar = _st_satirlara(kst)
            if satirlar:
                return {"olcu_adlari": adlar, "satirlar": satirlar,
                        "kaynak": "kardes",
                        "kaynak_adi": "aynı stok kodlu renk kardeşinin tablosu"}

    adlar, satirlar = _aciklamadan_olcu(product.get("description") or "")
    if satirlar:
        return {"olcu_adlari": adlar, "satirlar": satirlar,
                "kaynak": "aciklama", "kaynak_adi": "tedarikçi ürün açıklaması"}

    return {"olcu_adlari": [], "satirlar": [], "kaynak": "", "kaynak_adi": ""}


async def _olcu_tablosu_yaz(product_id: str, adlar: List[str],
                            satirlar: List[Dict[str, Any]], kaynak: str) -> int:
    """Ölçüleri PANELİN ve VİTRİNİN okuduğu kanonik biçimde yazar.

    `size_tables` koleksiyonunu vitrin (`/size-tables-public`) ve beden önerisi
    de okuyor; oraya kendi biçimimizi yazmak tabloyu müşteri tarafında görünmez
    yapardı. Tek koleksiyon, tek şema.

    Ölçü tablosu ÜRÜN düzeyindedir (renk değiştirince beden ölçüsü değişmez),
    bu yüzden aynı stok kodlu renk kardeşlerine de yansıtılır — manken bilgisi
    (`model_info`) renk özeldir, ona DOKUNULMAZ.
    """
    columns = [_baslik_tr(a) for a in adlar]
    sizes = [r["beden"] for r in satirlar]
    values = {
        r["beden"]: {columns[i]: (r["olculer"][i] if i < len(r["olculer"]) else "")
                     for i in range(len(columns))}
        for r in satirlar
    }
    ortak = {"sizes": sizes, "columns": columns, "values": values,
             "source": kaynak, "updated_at": _now_iso()}
    await db.size_tables.update_one(
        {"product_id": product_id},
        {"$set": {"product_id": product_id, **ortak},
         "$unset": {"olcu_adlari": ""}},
        upsert=True)

    senkron = 0
    try:
        p = await db.products.find_one({"id": product_id}, {"_id": 0, "stock_code": 1})
        kod = str((p or {}).get("stock_code") or "").strip()
        if kod:
            async for s in db.products.find(
                    {"stock_code": kod, "id": {"$ne": product_id},
                     "is_deleted": {"$ne": True}}, {"_id": 0, "id": 1}):
                await db.size_tables.update_one(
                    {"product_id": s["id"]},
                    {"$set": {"product_id": s["id"], **ortak,
                              "synced_from": product_id}},
                    upsert=True)
                senkron += 1
    except Exception as e:
        logger.error(f"[ai-images] olcu tablosu kardes senkron ({product_id}): {e}")
    return senkron


async def _gemini_key() -> str:
    cfg = await get_settings()
    enc = cfg.get("gemini_key_enc")
    if not enc:
        raise HTTPException(
            status_code=400,
            detail="Gemini API anahtarı tanımlı değil. Ayarlar > Görsel Üretimi'nden girin.")
    return decrypt(enc)


def _fit_target(raw: bytes, target=ENHANCE_TARGET) -> bytes:
    """Görseli hedef ölçüye getirir — HİÇBİR ŞEYİ KIRPMADAN.

    Kaynak 3:4, hedef 2:3: oranlar tutmuyor, birini seçmek zorundayız.
    Denenen iki yol da yanlıştı:
      • contain + düz gri dolgu → vitrinde üstte/altta gri bant.
      • cover + kırpma → görselin bir kısmı gidiyor. Ürün fotoğrafında bu
        kabul edilemez: yönetim ürünün tamamını görmek istiyor, hem de
        kırpılan yer bazen ürünün eteği ya da modelin başı oluyor.

    Doğrusu: oranı koruyup TAMAMINI sığdırmak, kalan boşluğu da görselin
    KENDİ KENARINI gererek doldurmak. Stüdyo fotoğrafında zemin düz olduğu
    için dikiş görünmüyor; kırpma olmadığı için de ürünün tek pikseli bile
    kaybolmuyor.
    """
    from PIL import Image
    import io

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    tw, th = target
    if (im.width, im.height) == (tw, th):
        out = io.BytesIO()
        im.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
        return out.getvalue()

    # TAMAMI SIĞSIN: küçük olan ölçek seçilir, taşma olmaz.
    sigdir = min(tw / im.width, th / im.height)
    nw = max(1, round(im.width * sigdir))
    nh = max(1, round(im.height * sigdir))
    im = im.resize((nw, nh), Image.LANCZOS)
    x, y = (tw - nw) // 2, (th - nh) // 2

    # Taban renk: dört kenarın ortalaması. Köşelerde şeritler buluşmadığı
    # için altta düz bir renk kalmalı, siyah kalmasın.
    kenar = im.resize((3, 3), Image.LANCZOS)
    piksel = [kenar.getpixel((i, j)) for i in range(3) for j in range(3)
              if (i, j) != (1, 1)]
    taban = tuple(sum(p[k] for p in piksel) // len(piksel) for k in range(3))
    tuval = Image.new("RGB", (tw, th), taban)

    serit = max(1, min(nh, nw) // 50)          # kenardan alınacak şerit kalınlığı
    if y > 0:                                   # üst ve alt boşluk
        tuval.paste(im.crop((0, 0, nw, serit)).resize((nw, y), Image.LANCZOS), (x, 0))
        alt = th - (y + nh)
        if alt > 0:
            tuval.paste(im.crop((0, nh - serit, nw, nh)).resize((nw, alt), Image.LANCZOS),
                        (x, y + nh))
    if x > 0:                                   # sol ve sağ boşluk
        tuval.paste(im.crop((0, 0, serit, nh)).resize((x, nh), Image.LANCZOS), (0, y))
        sag = tw - (x + nw)
        if sag > 0:
            tuval.paste(im.crop((nw - serit, 0, nw, nh)).resize((sag, nh), Image.LANCZOS),
                        (x + nw, y))

    tuval.paste(im, (x, y))                     # görselin TAMAMI, kırpılmadan

    out = io.BytesIO()
    tuval.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
    return out.getvalue()


# ── Model seçimi: sabit ad yazma, Google'a sor ──────────────────────────
# Koda gömülü model adı ergeç 404 verir. Bugün olan tam olarak bu:
# "models/gemini-2.5-flash is no longer available to new users" — kota değil,
# modelin kendisi kalkmış. Bu yüzden model adı artık bir VARSAYIM: çalışmazsa
# anahtarın erişebildiği modeller Google'dan sorulup uygunu seçiliyor ve
# ayarlara yazılıyor, bir sonraki çağrı doğrudan onunla gidiyor.
_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_MODEL_CACHE: Dict[str, Any] = {"liste": [], "anahtar": ""}


async def _model_listesi(client: httpx.AsyncClient, api_key: str,
                         tazele: bool = False) -> List[Dict[str, Any]]:
    """Anahtarın erişebildiği modeller. Süreç boyunca bir kez çekilir."""
    if not tazele and _MODEL_CACHE["liste"] and _MODEL_CACHE["anahtar"] == api_key[:8]:
        return _MODEL_CACHE["liste"]
    resp = await client.get(_MODELS_URL, params={"pageSize": 200},
                            headers={"x-goog-api-key": api_key}, timeout=60.0)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=_gemini_hata(resp))
    liste = (resp.json() or {}).get("models") or []
    _MODEL_CACHE.update({"liste": liste, "anahtar": api_key[:8]})
    return liste


def _model_puani(ad: str) -> tuple:
    """Sürüm numarasına göre sıralama anahtarı — yenisi öne gelsin."""
    s = re.sub(r"^models/", "", ad or "")
    surum = re.search(r"(\d+)(?:\.(\d+))?", s)
    ana = int(surum.group(1)) if surum else 0
    alt = int(surum.group(2) or 0) if surum else 0
    # Deneysel/önizleme sürümleri kararsız olabiliyor; eşitlikte kararlıyı seç.
    kararli = 0 if re.search(r"preview|exp|experimental", s) else 1
    return (ana, alt, kararli)


def _model_sec(modeller: List[Dict[str, Any]], amac: str) -> str:
    """amac='metin' → sınıflandırma/okuma, amac='gorsel' → görsel üretimi."""
    adaylar = []
    for m in modeller:
        ad = str(m.get("name") or "")
        kisa = re.sub(r"^models/", "", ad)
        yontemler = m.get("supportedGenerationMethods") or []
        if "generateContent" not in yontemler:
            continue
        if not kisa.startswith("gemini"):
            continue
        # Bunlar bu iş için uygun değil: gömme, konuşma, canlı oturum.
        if re.search(r"embedding|embed|tts|audio|live|native-audio", kisa):
            continue
        gorsel_uretir = "image" in kisa
        if amac == "gorsel" and not gorsel_uretir:
            continue
        if amac == "metin" and gorsel_uretir:
            continue
        adaylar.append(kisa)
    if not adaylar:
        return ""
    # Sınıflandırma/okuma binlerce görselde dönüyor: orada "flash" ucuz ve
    # yeterli. Görsel ÜRETİMİNDE ise kalite önemli — orada ucuzu seçmiyoruz,
    # eldeki en yeni sürümü alıyoruz.
    if amac == "metin":
        adaylar = [a for a in adaylar if "flash" in a] or adaylar
    return sorted(adaylar, key=_model_puani, reverse=True)[0]


def _model_yok_hatasi(resp) -> bool:
    """Yanıt 'bu model yok/erişilemiyor' mu diyor? (404 ya da 400 varyantı)"""
    if resp.status_code not in (400, 403, 404):
        return False
    try:
        msg = ((resp.json() or {}).get("error") or {}).get("message") or ""
    except Exception:
        msg = resp.text or ""
    m = msg.lower()
    return ("not found" in m or "no longer available" in m
            or "is not supported" in m or "not supported for" in m
            or "does not exist" in m)


async def _gemini_post(client: httpx.AsyncClient, api_key: str, model: str,
                       payload: dict, amac: str, ayar_alani: str):
    """generateContent çağrısı; model kalkmışsa uygununu bulup TEKRAR dener.

    Döner: (resp, kullanilan_model). Yeni bir model seçildiyse ayarlara yazılır
    ki bir dahaki çağrı doğrudan çalışsın ve her seferinde liste çekilmesin.
    """
    govde = payload

    async def _cagir(m):
        return await client.post(
            _GEMINI_URL.format(model=m),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=govde, timeout=_TIMEOUT)

    resp = await _cagir(model)
    # thinkingConfig'i her model tanımıyor; tanımayan 400 "Unknown name" der.
    # Alanı düşürüp bir kez daha deniyoruz — yoksa desteği olmayan modelde
    # işlem tamamen ölür.
    if resp.status_code == 400 and "thinking" in (resp.text or "").lower():
        govde = {**payload, "generationConfig": {
            k: v for k, v in (payload.get("generationConfig") or {}).items()
            if k != "thinkingConfig"}}
        resp = await _cagir(model)
    if not _model_yok_hatasi(resp):
        return resp, model

    logger.warning(f"[ai-images] model '{model}' kullanilamiyor, liste sorulacak")
    yeni = _model_sec(await _model_listesi(client, api_key, tazele=True), amac)
    if not yeni or yeni == model:
        return resp, model            # elde uygun başka model yok — asıl hata dönsün
    logger.info(f"[ai-images] model degistirildi: {model} → {yeni}")
    resp2 = await _cagir(yeni)
    if not _model_yok_hatasi(resp2):
        await db.settings.update_one(
            {"id": SETTINGS_ID},
            {"$set": {ayar_alani: yeni, f"{ayar_alani}_auto_at": _now_iso()}},
            upsert=True)
    return resp2, yeni


def _yanit_metni(resp) -> tuple:
    """(metin, bitis_sebebi) — yanıttaki tüm metin parçalarını birleştirir."""
    try:
        data = resp.json() or {}
    except Exception:
        return "", ""
    metin, bitis = "", ""
    for cand in (data.get("candidates") or []):
        bitis = bitis or str(cand.get("finishReason") or "")
        for part in ((cand.get("content") or {}).get("parts") or []):
            metin += part.get("text") or ""
    return metin, bitis


def _bos_yanit_sebebi(metin: str, bitis: str) -> str:
    """Boş yanıtın SEBEBİNİ söyler. '(boş)' yazmak hiçbir şey anlatmıyordu."""
    if metin.strip():
        return f"Model anlaşılır yanıt vermedi: {metin[:80]}"
    b = (bitis or "").upper()
    if b == "MAX_TOKENS":
        return ("Model çıktı bütçesini düşünmeye harcayıp cevap yazamadı "
                "(finishReason=MAX_TOKENS).")
    if b in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        return f"Model yanıtı güvenlik gerekçesiyle engelledi ({b})."
    if b == "RECITATION":
        return "Model yanıtı alıntı kontrolüne takıldı (RECITATION)."
    return f"Model boş yanıt döndürdü{f' ({b})' if b else ''}."


def _gemini_hata(resp) -> str:
    """Sağlayıcı hatasını okunur hâle getirir.

    Ham JSON'u ekrana basmak beş kutuyu aynı İngilizce yığınla dolduruyor ve
    asıl mesele (kota mı, anahtar mı, içerik reddi mi) kayboluyordu.
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
    if _model_yok_hatasi(resp):
        return ("Seçili Gemini modeli artık kullanılamıyor ve anahtarınızın "
                "erişebildiği uygun bir yedek de bulunamadı. Ayarlar > Görsel "
                "Üretimi'nden model seçin. (Google: " + msg[:140] + ")")
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
    resp, _ = await _gemini_post(client, api_key, model, payload,
                                 "gorsel", "enhance_model")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=_gemini_hata(resp))
    data = resp.json()
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


async def _gemini_classify(client: httpx.AsyncClient, api_key: str, model: str,
                           raw: bytes) -> tuple:
    """Görsel ürün fotoğrafı mı ölçü tablosu mu? 'olcu' | 'urun'.

    Ayrı ve UCUZ bir metin modeliyle soruluyor: görsel modeline "bu neyse ona
    göre davran" demek güvenilir değil, çünkü o metin döndürmüyor.

    (tür, sebep) döner. Sebep ESKİDEN YUTULUYORDU: kota mı bitmiş, model adı mı
    yanlış, anahtarın o modele yetkisi mi yok — hiçbiri görünmüyor, ekranda
    yalnız "belirsiz" yazıyordu. Sorunu çözmek için sebep şart.
    """
    payload = {
        "contents": [{
            "parts": [
                {"text": _CLASSIFY_PROMPT},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(raw).decode()}},
            ]
        }],
        # maxOutputTokens BURADA 10'DU ve model BOŞ dönüyordu: yeni modeller
        # yanıtı yazmadan önce "düşünüyor" ve o düşünme de çıktı bütçesinden
        # harcanıyor — 10 jeton düşünmeye gidip cevaba sıra gelmiyordu.
        # Düşünmeyi kapatıyoruz (tek kelimelik iş) ve bütçeyi rahat bırakıyoruz.
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    try:
        resp, _ = await _gemini_post(client, api_key, model, payload,
                                     "metin", "classify_model")
        if resp.status_code >= 400:
            # Sessizce "ürün" demek apaçık bir tabloyu kaçırıyordu (kota
            # bitince her görsel ürün sayılıyordu). Kararsız kaldığımızı
            # söylüyoruz; panel elle işaretlemeye yönlendiriyor.
            sebep = _gemini_hata(resp)
            logger.warning(f"[ai-images] siniflandirma dustu: {sebep}")
            return "belirsiz", sebep
        metin, bitis = _yanit_metni(resp)
        temiz = metin.strip().lower().replace("ö", "o").replace("ç", "c")
        if "olcu" in temiz:
            return "olcu", ""
        if "urun" in temiz:
            return "urun", ""
        return "belirsiz", _bos_yanit_sebebi(metin, bitis)
    except Exception as e:
        logger.warning(f"[ai-images] siniflandirma hatasi ({e})")
        return "belirsiz", f"Bağlantı hatası: {e}"


async def scan_size_charts(product_id: str) -> Dict[str, Any]:
    """Ürün görsellerini tarar, hangileri ölçü tablosu söyler."""
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")
    images = [_gorsel_url(u) for u in (product.get("images") or [])
              if _gorsel_url(u)]
    if not images:
        return {"images": []}

    cfg = await get_settings()
    api_key = await _gemini_key()
    model = cfg.get("classify_model") or _DEFAULT_CLASSIFY_MODEL

    async with httpx.AsyncClient() as client:
        async def tek(i: int):
            raw = await _fetch_image(client, images[i])
            if not raw:
                return {"index": i, "url": images[i], "tur": "urun", "hata": "indirilemedi"}
            tur, sebep = await _gemini_classify(client, api_key, model, raw)
            return {"index": i, "url": images[i], "tur": tur, "sebep": sebep}
        sonuc = list(await asyncio.gather(*[tek(i) for i in range(len(images))]))
    belirsiz = sum(1 for r in sonuc if r["tur"] == "belirsiz")
    return {"product_id": product_id, "images": sonuc,
            "size_chart_count": sum(1 for r in sonuc if r["tur"] == "olcu"),
            "belirsiz": belirsiz,
            "warning": ((next((r.get("sebep") for r in sonuc if r.get("sebep")), "")
                         or "Sınıflandırma yapılamadı.")
                        + " Tabloyu aşağıdan elle işaretleyebilirsiniz."
                        if belirsiz else "")}


async def convert_size_chart(product_id: str, index: int, *, replace: bool = False) -> Dict[str, Any]:
    """Ölçü tablosunu SIFIRDAN, temiz ve Türkçe olarak yeniden üretir.

    Önce modele "bu tabloyu Türkçe yeniden çiz" deniyordu; başlıkları serbestçe
    çeviriyor (hatalı karşılıklar) ve sayıları yeniden yazıyordu — bir rakam
    kayarsa müşteri yanlış beden alır. Artık modelin işi yalnız OKUMAK:
    değerler JSON olarak alınıyor, tablo Pillow ile burada çiziliyor. Başlık
    çevirisi sabit sözlükten geliyor, sayılar okunduğu gibi basılıyor,
    tedarikçinin manken silueti ve kalabalık düzeni tabloya hiç girmiyor.

    ÖNCE KREDİSİZ: ölçüler zaten sistemdeyse (kayıtlı tablo, renk kardeşi ya da
    tedarikçi açıklaması) modele hiç gidilmez — çizim zaten bizde. Yalnız
    hiçbir kaynakta yoksa görsel OCR ile okunur.
    """
    okunan = await _kredisiz_olcu_verisi(product_id)
    if not okunan.get("satirlar"):
        okunan = await read_size_table(product_id, index, kaydet=False)
        okunan["kaynak"], okunan["kaynak_adi"] = "ocr", "görselden okundu (Gemini)"
    raw = _render_size_table(okunan.get("olcu_adlari") or [],
                             okunan.get("satirlar") or [])
    url = await _store(raw, product_id, f"olcu{index}", "image/jpeg")

    product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
    ham_liste = [u for u in ((product or {}).get("images") or []) if _gorsel_url(u)]
    images = [_gorsel_url(u) for u in ham_liste]
    if replace and 0 <= index < len(images):
        yeni_liste = list(ham_liste)
        yeni_liste[index] = _gorsel_degistir(ham_liste[index], url)
        await db.products.update_one({"id": product_id}, {"$set": {
            "images": yeni_liste,
            "images_custom": True,
            "size_chart_localized_at": _now_iso(),
        }})

    return {"product_id": product_id, "index": index, "url": url,
            "source": images[index] if 0 <= index < len(images) else "",
            "replaced": bool(replace),
            "kaynak": okunan.get("kaynak") or "",
            "kaynak_adi": okunan.get("kaynak_adi") or "",
            "kredi_harcandi": okunan.get("kaynak") == "ocr",
            "olcu_adlari": okunan.get("olcu_adlari") or [],
            "satirlar": okunan.get("satirlar") or [],
            "warning": "Tablo okunan değerlerden yeniden çizildi — "
                       "sayıları kaynak görselle karşılaştırın."}


_SIZE_TABLE_READ_PROMPT = (
    "Bu bir beden/ölçü tablosu görseli. İçindeki veriyi JSON olarak çıkar.\n"
    'Biçim: {"olcu_adlari": ["Göğüs","Bel","Kalça"], '
    '"satirlar": [{"beden": "S", "olculer": ["88","70","96"]}]}\n'
    "Kurallar: sayıları BİREBİR oku, yuvarlama veya tahmin etme; okuyamadığın "
    "hücreye boş dize koy. Ölçü adlarını Türkçeye çevir (Bust/Chest→Göğüs, "
    "Waist→Bel, Hip→Kalça, Length→Boy, Shoulder→Omuz, Sleeve→Kol Boyu). "
    "Yalnız JSON döndür, açıklama yazma."
)


async def read_size_table(product_id: str, index: int, *, kaydet: bool = False) -> Dict[str, Any]:
    """Ölçü tablosu görselindeki değerleri okuyup ürünün ölçü tablosuna yazar.

    Tablo görsel olarak duruyor ama site onu VERİ olarak kullanamıyor: beden
    rehberi, filtreler ve pazaryeri aktarımı sayısal ölçü ister. Burada görsel
    bir kez okunup `size_tables` koleksiyonuna yazılıyor.

    Sayılar birebir okunur; model bir hücreyi seçemezse boş bırakır. Yine de
    SONUÇ ELLE DOĞRULANMALI — yanlış ölçü müşteriye yanlış beden aldırır.
    """
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")
    images = [_gorsel_url(u) for u in (product.get("images") or [])
              if _gorsel_url(u)]
    if not (0 <= index < len(images)):
        raise HTTPException(status_code=400, detail="Geçersiz görsel sırası.")

    cfg = await get_settings()
    api_key = await _gemini_key()
    model = cfg.get("classify_model") or _DEFAULT_CLASSIFY_MODEL

    async with httpx.AsyncClient() as client:
        raw = await _fetch_image(client, images[index])
        if not raw:
            raise HTTPException(status_code=502, detail="Görsel indirilemedi.")
        payload = {
            "contents": [{"parts": [
                {"text": _SIZE_TABLE_READ_PROMPT},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(raw).decode()}},
            ]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        resp, _ = await _gemini_post(client, api_key, model, payload,
                                     "metin", "classify_model")
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=_gemini_hata(resp))
        metin = ""
        for cand in (resp.json().get("candidates") or []):
            for part in ((cand.get("content") or {}).get("parts") or []):
                metin += part.get("text") or ""

    import json as _json
    try:
        veri = _json.loads(metin.strip().strip("`").replace("json\n", "", 1))
    except Exception:
        raise HTTPException(status_code=502,
                            detail="Tablo okunamadı; model geçerli veri döndürmedi.")

    adlar = [str(a) for a in (veri.get("olcu_adlari") or []) if str(a).strip()]
    satirlar = []
    for r in (veri.get("satirlar") or []):
        beden = str(r.get("beden") or "").strip()
        olculer = [str(x).strip() for x in (r.get("olculer") or [])]
        if beden and any(olculer):
            satirlar.append({"beden": beden, "olculer": olculer})
    if not satirlar:
        raise HTTPException(status_code=502, detail="Tabloda okunabilir satır bulunamadı.")

    if kaydet:
        await _olcu_tablosu_yaz(product_id, adlar, satirlar, "ai_ocr")
        await db.size_tables.update_one(
            {"product_id": product_id},
            {"$set": {"source_image": images[index]}})

    return {"product_id": product_id, "index": index,
            "olcu_adlari": adlar, "satirlar": satirlar, "kaydedildi": bool(kaydet),
            "warning": "Okunan ölçüleri görselle karşılaştırın — "
                       "yanlış sayı müşteriye yanlış beden aldırır."}


async def enhance_product_images(product_id: str, *, indexes: Optional[List[int]] = None,
                                 replace: bool = False) -> Dict[str, Any]:
    """Ürün görsellerini iyileştirip 1200x1800 olarak yeniden üretir."""
    product = await db.products.find_one({"id": product_id},
                                         {"_id": 0, "id": 1, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")
    ham_liste = [u for u in (product.get("images") or []) if _gorsel_url(u)]
    images = [_gorsel_url(u) for u in ham_liste]
    if not images:
        raise HTTPException(status_code=400, detail="Üründe görsel yok.")

    secili = [i for i in (indexes or range(len(images))) if 0 <= i < len(images)]
    if not secili:
        raise HTTPException(status_code=400, detail="Geçerli görsel seçilmedi.")

    cfg = await get_settings()
    api_key = await _gemini_key()
    model = cfg.get("enhance_model") or _DEFAULT_ENHANCE_MODEL
    prompt = cfg.get("enhance_prompt") or _DEFAULT_ENHANCE_PROMPT
    chart_prompt = cfg.get("size_chart_prompt") or _DEFAULT_SIZE_CHART_PROMPT
    classify_model = cfg.get("classify_model") or _DEFAULT_CLASSIFY_MODEL

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
                # Ölçü tablosu bir BELGE: "netleştir" demek yetmiyor, metni
                # Türkçeleştirip yeniden dizmek gerekiyor. Önce ne olduğunu
                # soruyoruz, sonra ona göre metin seçiyoruz.
                tur, _sebep = await _gemini_classify(client, api_key, classify_model, raw)
                try:
                    iyilestirilmis = await _gemini_enhance(
                        client, api_key, model,
                        chart_prompt if tur == "olcu" else prompt, raw)
                except HTTPException as e:
                    # Model erişilemiyor (kota/kredi/yetki). Bu, işi TAMAMEN
                    # düşürmek için sebep değil: görseli yine de hedef ölçüye
                    # büyütüyoruz. Kullanıcı en azından 1200x1800 alır ve
                    # kutuda "yalnız ölçek" etiketiyle sebebi görür.
                    uyari = str(e.detail)
                son = _fit_target(iyilestirilmis or raw)
                url = await _store(son, product_id, f"hd{i}", "image/jpeg")
                return {"index": i, "url": url, "ai": bool(iyilestirilmis),
                        "tur": tur, "note": uyari, "source": images[i]}
            except Exception as e:
                return {"index": i, "error": str(e)}

        sonuc = list(await asyncio.gather(*[tek(i) for i in secili]))

    basarili = [r for r in sonuc if r.get("url")]
    if replace and basarili:
        yeni = list(ham_liste)
        for r in basarili:
            yeni[r["index"]] = _gorsel_degistir(ham_liste[r["index"]], r["url"])
        await db.products.update_one({"id": product_id}, {"$set": {
            "images": yeni,
            # KİLİT: bundan sonra tedarikçi aktarımı bu ürünün görsellerine
            # dokunmaz. Olmadığında yeni görseller ertesi günkü aktarımda
            # siliniyor, ürün tedarikçinin düşük çözünürlüklü fotoğraflarına
            # geri dönüyordu.
            "images_custom": True,
            "images_enhanced_at": _now_iso(),
            # Değiştirilen tedarikçi adresleri: geri almak isteyen için kayıt.
            "images_supplier_backup": [images[r["index"]] for r in basarili],
        }})

    return {
        "product_id": product_id,
        "target": f"{ENHANCE_TARGET[0]}x{ENHANCE_TARGET[1]}",
        "results": sonuc,
        "replaced": bool(replace and basarili),
        "images_locked": bool(replace and basarili),
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
        "size_chart_prompt": cfg.get("size_chart_prompt") or _DEFAULT_SIZE_CHART_PROMPT,
        "classify_model": cfg.get("classify_model") or _DEFAULT_CLASSIFY_MODEL,
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
    if payload.get("size_chart_prompt") is not None:
        update["size_chart_prompt"] = str(payload["size_chart_prompt"])
    if payload.get("classify_model"):
        update["classify_model"] = str(payload["classify_model"]).strip()
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

    ARKA PLANDA çalışır (vekilin 100 saniyelik sınırı); iş kimliği döner.
    `bekle: true` verilirse sonucu bekler.

    body: {reference_url?, poses?: ["front","left",...], attach?: bool, bekle?}
    """
    p = payload or {}
    ref = str(p.get("reference_url") or "").strip()
    poses = p.get("poses")
    attach = bool(p.get("attach"))

    def _isi():
        return regenerate_for_product(product_id, reference_url=ref,
                                      poses=poses, attach=attach)

    if p.get("bekle"):
        return await _isi()
    return {"job_id": await _is_baslat(product_id, "gorsel-uretimi", _isi),
            "durum": "calisiyor"}


@router.post("/products/{product_id}/enhance")
async def enhance(product_id: str, payload: dict | None = None,
                  _admin=Depends(require_admin)):
    """Ürün görsellerini Gemini ile netleştirip 1200x1800 üretir.

    ARKA PLANDA çalışır (vekilin 100 saniyelik sınırı); iş kimliği döner.
    `bekle: true` verilirse sonucu bekler.

    body: {indexes?: [0,2], replace?: bool, bekle?: bool}
    `replace` verilmezse üretilenler yalnız döndürülür, ürün değişmez.
    """
    p = payload or {}
    idx = p.get("indexes")
    indexes = [int(i) for i in idx] if isinstance(idx, list) else None
    replace = bool(p.get("replace"))

    def _isi():
        return enhance_product_images(product_id, indexes=indexes, replace=replace)

    if p.get("bekle"):
        return await _isi()
    return {"job_id": await _is_baslat(product_id, "kalite-iyilestirme", _isi),
            "durum": "calisiyor"}


@router.get("/products/{product_id}/scan")
async def scan(product_id: str, _admin=Depends(require_admin)):
    """Ürün görsellerini tarar: hangisi ürün fotoğrafı, hangisi ölçü tablosu."""
    return await scan_size_charts(product_id)


@router.post("/products/{product_id}/size-chart")
async def size_chart(product_id: str, payload: dict, _admin=Depends(require_admin)):
    """Bir ölçü tablosunu Türkçeleştirip aynı ölçüde yeniden üretir.

    body: {index: 4, replace?: bool}
    """
    if "index" not in (payload or {}):
        raise HTTPException(status_code=400, detail="Görsel sırası (index) gerekli.")
    return await convert_size_chart(
        product_id, int(payload["index"]), replace=bool(payload.get("replace")))


@router.post("/products/{product_id}/size-table-read")
async def size_table_read(product_id: str, payload: dict, _admin=Depends(require_admin)):
    """Ölçü tablosundaki değerleri okur; kaydet=true ise ürünün tablosuna yazar.

    body: {index: 3, kaydet?: bool}
    """
    if "index" not in (payload or {}):
        raise HTTPException(status_code=400, detail="Görsel sırası (index) gerekli.")
    return await read_size_table(product_id, int(payload["index"]),
                                 kaydet=bool(payload.get("kaydet")))


@router.post("/products/{product_id}/size-table-build")
async def size_table_build(product_id: str, payload: dict, _admin=Depends(require_admin)):
    """Verilen ölçülerden tablo görselini üretir — MODEL GEREKTİRMEZ.

    Okuma adımı Gemini'ye bağlı; kota bitince ölçü tablosu hiç üretilemiyordu.
    Oysa çizim tamamen bizde. Ölçüler elle girildiğinde (ya da daha önce
    okunmuş veriden geldiğinde) tablo krediye ihtiyaç duymadan oluşuyor.

    body: {olcu_adlari: ["Göğüs","Bel"],
           satirlar: [{"beden":"S","olculer":["88","70"]}],
           index?: 3, replace?: bool, kaydet?: bool}
    """
    adlar = [str(a).strip() for a in ((payload or {}).get("olcu_adlari") or []) if str(a).strip()]
    ham = (payload or {}).get("satirlar") or []
    satirlar = []
    for r in ham:
        beden = str((r or {}).get("beden") or "").strip()
        olculer = [str(x).strip() for x in ((r or {}).get("olculer") or [])]
        if beden and any(olculer):
            satirlar.append({"beden": beden, "olculer": olculer})
    if not adlar or not satirlar:
        raise HTTPException(status_code=400,
                            detail="En az bir ölçü adı ve bir beden satırı gerekli.")

    raw = _render_size_table(adlar, satirlar)
    url = await _store(raw, product_id, "olcu-elle", "image/jpeg")

    index = (payload or {}).get("index")
    if (payload or {}).get("replace") and index is not None:
        product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
        ham_liste = [u for u in ((product or {}).get("images") or []) if _gorsel_url(u)]
        images = list(ham_liste)
        i = int(index)
        if 0 <= i < len(images):
            images[i] = _gorsel_degistir(ham_liste[i], url)
            await db.products.update_one({"id": product_id}, {"$set": {
                "images": images, "images_custom": True,
                "size_chart_localized_at": _now_iso()}})

    senkron = 0
    if (payload or {}).get("kaydet"):
        senkron = await _olcu_tablosu_yaz(product_id, adlar, satirlar, "manuel")

    return {"product_id": product_id, "url": url,
            "olcu_adlari": adlar, "satirlar": satirlar, "senkron_kardes": senkron,
            "replaced": bool((payload or {}).get("replace") and index is not None)}


# ── Uzun işler: arka plana al, panel sorsun ─────────────────────────────
# Cloudflare vekili 100 SANİYEDE bağlantıyı kesiyor (524). Görsel üretimi ve
# tablo okuma bundan uzun sürebiliyor; bağlantı koptuğu için iş sunucuda bitse
# bile sonuç panele ulaşmıyor, kullanıcı "olmuyor" görüyordu. İşi hızlandırmak
# çözüm değil — sınır isteğin SÜRESİNDE, işin süresinde değil. Bu yüzden iş
# arka planda çalışıyor, panel iş kimliğiyle durumu soruyor.
#
# Durum MongoDB'de tutuluyor: uvicorn birden çok işçiyle çalışırsa sorgu başka
# işçiye düşebilir, bellekte tutulan durum orada görünmezdi.

async def _is_baslat(product_id: str, tur: str, isi) -> str:
    """İşi arka planda başlatır, iş kimliğini döndürür."""
    job_id = str(uuid.uuid4())
    await db.ai_jobs.insert_one({
        "id": job_id, "product_id": product_id, "tur": tur,
        "durum": "calisiyor", "created_at": _now_iso(),
    })

    async def _sar():
        try:
            sonuc = await isi()
            await db.ai_jobs.update_one({"id": job_id}, {"$set": {
                "durum": "bitti", "sonuc": sonuc, "finished_at": _now_iso()}})
        except HTTPException as e:
            await db.ai_jobs.update_one({"id": job_id}, {"$set": {
                "durum": "hata", "hata": str(e.detail), "finished_at": _now_iso()}})
        except Exception as e:
            logger.exception(f"[ai-images] arka plan isi dustu ({tur}): {e}")
            await db.ai_jobs.update_one({"id": job_id}, {"$set": {
                "durum": "hata", "hata": f"Beklenmeyen hata: {e}",
                "finished_at": _now_iso()}})

    asyncio.create_task(_sar())
    return job_id


@router.get("/jobs/{job_id}")
async def is_durumu(job_id: str, _admin=Depends(require_admin)):
    """Arka plan işinin durumu: calisiyor | bitti | hata."""
    job = await db.ai_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="İş bulunamadı.")
    return job


@router.post("/products/{product_id}/size-table-fix")
async def size_table_fix(product_id: str, payload: dict | None = None,
                         _admin=Depends(require_admin)):
    """Ölçü tablosu düzeltmesini ARKA PLANDA başlatır, iş kimliği döndürür.

    `bekle: true` gönderilirse eskisi gibi sonucu bekler (betikler için).
    """
    p = payload or {}
    if p.get("bekle"):
        return await _size_table_fix(product_id, p.get("index"))
    index = p.get("index")
    job_id = await _is_baslat(product_id, "olcu-tablosu",
                              lambda: _size_table_fix(product_id, index))
    return {"job_id": job_id, "durum": "calisiyor"}


async def _size_table_fix(product_id: str, index=None) -> Dict[str, Any]:
    """TEK DÜĞME: ölçü tablosunu bul, Türkçeleştir, görseli değiştir, tabloya yaz.

    Panelde beş ayrı düğme vardı (ara → çevir → oku → elle işaretle → elle gir)
    ve her biri ayrı ayrı başarısız olabiliyordu; kullanıcı hangi sırayla ne
    yapacağını takip etmek zorunda kalıyordu. İş tek: "şu tabloyu Türkçe yap".
    Sıra artık burada:

      1. Görsel seçilmediyse hangisinin tablo olduğunu bul (model).
      2. Ölçüleri KREDİSİZ kaynaklardan al; yoksa görselden okut (OCR).
      3. Tabloyu burada çiz, ürünün o görselinin yerine yaz.
      4. Ölçüleri ürünün beden tablosuna (ve renk kardeşlerine) kaydet.

    Bir adım tıkanırsa NE OLDUĞUNU söyler; sessizce yarım bırakmaz.
    `index` verilmezse tablo görseli aranır.
    """
    product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")
    ham_gorseller = [u for u in (product.get("images") or []) if _gorsel_url(u)]
    images = [_gorsel_url(u) for u in ham_gorseller]
    if not images:
        raise HTTPException(status_code=400, detail="Üründe görsel yok.")

    adimlar: List[str] = []

    # 1) Hangi görsel tablo?
    if index is None:
        try:
            tarama = await scan_size_charts(product_id)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=(
                f"Tablo görselini bulamadım: {e.detail} "
                "Tablo görselini kendiniz seçip tekrar deneyin."))
        olcular = [r["index"] for r in (tarama.get("images") or []) if r["tur"] == "olcu"]
        if not olcular:
            raise HTTPException(status_code=422, detail=(
                (tarama.get("warning") or "Görsellerin hiçbiri ölçü tablosu değil.")
                + " Tablo görselini kendiniz seçip tekrar deneyin."))
        index = olcular[0]
        adimlar.append(f"{index + 1}. görsel ölçü tablosu olarak bulundu")
    else:
        index = int(index)
        adimlar.append(f"{index + 1}. görsel sizin seçiminiz")
    if not (0 <= index < len(images)):
        raise HTTPException(status_code=400, detail="Geçersiz görsel sırası.")

    # 2) Ölçüler — önce kredisiz, sonra OCR.
    veri = await _kredisiz_olcu_verisi(product_id)
    if veri.get("satirlar"):
        adimlar.append(f"ölçüler {veri['kaynak_adi']} içinden alındı (kredi harcanmadı)")
    else:
        veri = await read_size_table(product_id, index, kaydet=False)
        veri["kaynak"], veri["kaynak_adi"] = "ocr", "görselden okundu"
        adimlar.append("ölçüler görselden okundu (Gemini)")

    adlar = veri.get("olcu_adlari") or []
    satirlar = veri.get("satirlar") or []

    # 3) Çiz ve görselin yerine yaz.
    raw = _render_size_table(adlar, satirlar)
    url = await _store(raw, product_id, f"olcu{index}", "image/jpeg")
    yeni = list(ham_gorseller)
    yeni[index] = _gorsel_degistir(ham_gorseller[index], url)
    await db.products.update_one({"id": product_id}, {"$set": {
        "images": yeni, "images_custom": True,
        "size_chart_localized_at": _now_iso()}})
    adimlar.append("Türkçe tablo çizildi ve görselin yerine yazıldı")

    # 4) Beden tablosuna kaydet (vitrin ve pazaryeri VERİ olarak kullanıyor).
    senkron = await _olcu_tablosu_yaz(product_id, adlar, satirlar,
                                      veri.get("kaynak") or "otomatik")
    adimlar.append("ölçüler beden tablosuna yazıldı"
                   + (f", {senkron} renk kardeşine yansıtıldı" if senkron else ""))

    return {"product_id": product_id, "index": index, "url": url,
            "olcu_adlari": adlar, "satirlar": satirlar,
            "kaynak": veri.get("kaynak"), "adimlar": adimlar,
            "kredi_harcandi": veri.get("kaynak") == "ocr",
            "warning": "Sayıları kaynak görselle bir kez karşılaştırın — "
                       "yanlış ölçü müşteriye yanlış beden aldırır."}


@router.get("/gemini-models")
async def gemini_models(_admin=Depends(require_admin)):
    """Anahtarın ERİŞEBİLDİĞİ Gemini modellerini listeler.

    Model adı koda gömülmüyor; hangi modelin var olduğunu tahmin etmek yerine
    Google'a soruyoruz. Panel bunu açılır listede gösterir, önerilen alanı da
    otomatik seçimin ne bulduğunu söyler.
    """
    api_key = await _gemini_key()
    cfg = await get_settings()
    async with httpx.AsyncClient() as client:
        modeller = await _model_listesi(client, api_key, tazele=True)
    def _sade(m):
        return {"ad": re.sub(r"^models/", "", str(m.get("name") or "")),
                "baslik": m.get("displayName") or "",
                "yontemler": m.get("supportedGenerationMethods") or []}
    hepsi = [_sade(m) for m in modeller]
    return {
        "toplam": len(hepsi),
        "metin_onerilen": _model_sec(modeller, "metin"),
        "gorsel_onerilen": _model_sec(modeller, "gorsel"),
        "secili_metin": cfg.get("classify_model") or _DEFAULT_CLASSIFY_MODEL,
        "secili_gorsel": cfg.get("enhance_model") or _DEFAULT_ENHANCE_MODEL,
        "modeller": sorted(hepsi, key=lambda x: x["ad"]),
    }


@router.get("/products/{product_id}/size-table-source")
async def size_table_source(product_id: str, _admin=Depends(require_admin)):
    """Ölçüler KREDİ HARCAMADAN bulunabiliyor mu? Bulunduysa değerleri döndürür.

    Panel bunu tablo bölümünü açarken sorar: veri hazırsa "Kredisiz oluştur"
    düğmesi çıkar, Gemini'ye hiç gidilmez.
    """
    return await _kredisiz_olcu_verisi(product_id)


@router.post("/products/{product_id}/size-table-auto")
async def size_table_auto(product_id: str, payload: dict | None = None,
                          _admin=Depends(require_admin)):
    """Ölçü tablosunu KREDİ HARCAMADAN üretir.

    Ölçüler kayıtlı tablodan, renk kardeşinden ya da tedarikçi açıklamasından
    okunur; tablo Pillow ile burada çizilir. Hiçbir model çağrısı yapılmaz.

    body: {index?: 3, replace?: bool, kaydet?: bool}
    """
    p = payload or {}
    veri = await _kredisiz_olcu_verisi(product_id)
    satirlar = veri.get("satirlar") or []
    if not satirlar:
        raise HTTPException(
            status_code=404,
            detail="Kredisiz kaynaklarda ölçü bulunamadı (kayıtlı tablo, renk "
                   "kardeşi, tedarikçi açıklaması). Ölçüleri elle girin ya da "
                   "görselden okutun.")

    adlar = veri.get("olcu_adlari") or []
    raw = _render_size_table(adlar, satirlar)
    url = await _store(raw, product_id, "olcu-kredisiz", "image/jpeg")

    index = p.get("index")
    degisti = False
    if p.get("replace") and index is not None:
        product = await db.products.find_one({"id": product_id}, {"_id": 0, "images": 1})
        ham_liste = [u for u in ((product or {}).get("images") or []) if _gorsel_url(u)]
        images = list(ham_liste)
        i = int(index)
        if 0 <= i < len(images):
            images[i] = _gorsel_degistir(ham_liste[i], url)
            await db.products.update_one({"id": product_id}, {"$set": {
                "images": images, "images_custom": True,
                "size_chart_localized_at": _now_iso()}})
            degisti = True

    senkron = 0
    if p.get("kaydet"):
        senkron = await _olcu_tablosu_yaz(product_id, adlar, satirlar,
                                          veri.get("kaynak") or "kredisiz")

    return {"product_id": product_id, "url": url, "replaced": degisti,
            "kaynak": veri.get("kaynak"), "kaynak_adi": veri.get("kaynak_adi"),
            "olcu_adlari": adlar, "satirlar": satirlar, "senkron_kardes": senkron,
            "kredi_harcandi": False,
            "warning": "Ölçüler " + (veri.get("kaynak_adi") or "sistemden") +
                       " alındı — sayıları bir kez gözden geçirin."}
