from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import Response, RedirectResponse
from datetime import datetime, timezone
import asyncio
from collections import OrderedDict
import base64
import uuid
import os

from .deps import db, get_current_user, require_auth, require_admin, logger, require_permission
from services import r2_storage as r2

# DECOMPRESSION-BOMB KORUMASI (modül düzeyi): bu modülün TÜM PIL yolları (yükleme optimize,
# ?w= yeniden boyutlandırma, JPEG proxy) için geçerli. Eskiden yalnız JPEG proxy'nin içinde,
# ilk çağrıda set ediliyordu → ondan önceki yükleme/resize çağrıları korumasızdı.
# PIL: >MAX → uyarı, >2×MAX (80MP) → DecompressionBombError.
from image_worker import MAX_IMAGE_PIXELS as _MAX_IMAGE_PIXELS  # tek kaynak (40 MP)
try:
    from PIL import Image as _PILImage
    _PILImage.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
except Exception:  # noqa: BLE001 — Pillow yoksa resize yolları zaten try/except içinde
    _PILImage = None

# ── GÖRSEL DÖNÜŞTÜRME İZOLASYONU (ADM-07 / T18 / SHOP02) ─────────────────────────────────
# Tüm PIL dönüşümleri (yükleme optimize, ?w= yeniden boyutlandırma, JPEG proxy, Excel görselleri,
# ölçü tablosu kaynak görseli) AYRI SÜREÇTE koşar (image_worker.py): sınırlı iş parçacığı havuzu
# eşzamanlılığı sınırlar, her iş parçacığı kendi tek-işçili süreç havuzunu kullanır. Pillow'da
# yerel bir çökme / bellek taşması yalnız o çocuk süreci öldürür (API süreci ve checkout etkilenmez;
# havuz yeniden kurulur, çağırana temiz bir hata döner). Süre sınırı aşılırsa çocuk süreç
# ÖLDÜRÜLÜR — iş arka planda sürmez. Piksel tavanı ÇÖZÜMLEMEDEN ÖNCE başlıktan kontrol edilir.
import image_worker as _iw  # noqa: E402
from image_worker import (  # noqa: E402,F401 — geriye dönük uyumlu adlar (testler/çağıranlar)
    IMAGE_WORKERS, IMAGE_TASK_TIMEOUT_S, ImageTaskTimeout, ImageTooLarge, ImageWorkerCrashed,
    check_image_pixels, open_image_guarded, run_image_job, run_image_job_isolated,
)
from image_worker import _POOL as _IMAGE_POOL  # noqa: E402,F401
from image_worker import InvalidImage as _InvalidImage  # noqa: E402


router = APIRouter(prefix="/upload", tags=["upload"])
# Geriye dönük uyumluluk: bazı kayıtlarda görsel URL'si /api/files/... olarak saklandı
files_router = APIRouter(prefix="/files", tags=["upload"])

APP_NAME = "meluni"
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Yeni yüklemeler Cloudflare R2'ye (S3 uyumlu) gider ve doğrudan R2 CDN'den
# sunulur. Bu, MongoDB'yi base64 görsel yükünden kurtarır ve yüklemeyi hızlandırır.
# R2 yapılandırılmamışsa eski davranışa (MongoDB + disk) düşer.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — DSLR çıkışı hero görselleri 10MB'ı aşıyor; Pillow zaten resize+WebP ile küçültüyor


# Yüklemede maksimum uzun kenar (px) — ürün görsel ızgarası + PDP zoom için yeterli,
# eskiden olduğu gibi 1700-2500px ham görselleri (yavaş decode/paint → "boş çıkıyor"
# şikayeti) önler. PNG/JPG → WebP'e çevrilir, format başına ortalama %60-80 küçülür.
MAX_UPLOAD_LONG_EDGE = 2000
UPLOAD_WEBP_QUALITY = 88


def _ftyp_brands(data: bytes):
    """ISO-BMFF (MP4/MOV/AVIF/HEIC) 'ftyp' kutusundan (major, {compatible}) markaları."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return None, set()
    try:
        box_len = int.from_bytes(data[0:4], "big")
    except Exception:  # noqa: BLE001
        box_len = 0
    box_len = max(16, min(box_len or 16, 256, len(data)))
    major = data[8:12]
    compat = {data[i:i + 4] for i in range(16, box_len - 3, 4)}
    return major, compat


_AVIF_BRANDS = {b"avif", b"avis"}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def _sniff_image(data: bytes):
    """Sihirli baytlardan (magic bytes) görsel türünü belirle → (mime, ext) ya da None.
    Sunucu türe İÇERİKTEN karar verir; istemcinin Content-Type başlığına/dosya adına güvenilmez."""
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    if data[:2] == b"BM":
        return "image/bmp", "bmp"
    major, compat = _ftyp_brands(data)
    if major is not None:
        brands = {major} | compat
        if brands & _AVIF_BRANDS:
            return "image/avif", "avif"
        if brands & _HEIC_BRANDS:
            return "image/heic", "heic"
    return None


def _sniff_video(data: bytes):
    """Video türü: MP4/M4V (ISO-BMFF), MOV (QuickTime 'qt  ' ya da eski 'moov'/'mdat'/'wide'
    atomu), WEBM (EBML + 'webm' doctype) → (mime, ext) ya da None."""
    if not data or len(data) < 12:
        return None
    major, compat = _ftyp_brands(data)
    if major is not None:
        brands = {major} | compat
        if brands & (_AVIF_BRANDS | _HEIC_BRANDS) and not (brands & {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"qt  ", b"M4V "}):
            return None  # durağan görsel konteyneri, video değil
        if major == b"qt  ":
            return "video/quicktime", "mov"
        if major in (b"M4V ", b"M4VH", b"M4VP"):
            return "video/mp4", "m4v"
        return "video/mp4", "mp4"
    if data[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"):
        return "video/quicktime", "mov"   # ftyp'siz eski QuickTime
    if data[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in data[:64]:
        return "video/webm", "webm"
    return None


def _optimize_for_upload(data: bytes, content_type: str):
    """Yüklenen görseli WebP'e çevirip gerekirse küçültür (iş gövdesi: image_worker.optimize_for_upload).

    - PIL görseli AÇAMAZSA (bozuk/sahte/bomba) → `_InvalidImage` (çağıran 400 döner; ham bayt
      ASLA yüklenmez).
    - Animasyonlu GIF/WebP → (None, None, None): çağıran (doğrulanmış) orijinal bytes'ı
      İÇERİKTEN tespit edilen tür/uzantıyla yükler.
    Bu sarmalayıcı aynı süreçte çalışır; yükleme ucu işi `run_image_job_isolated` ile AYRI
    süreçte çalıştırır."""
    return _iw.optimize_for_upload(data, MAX_UPLOAD_LONG_EDGE, UPLOAD_WEBP_QUALITY)


# DENETİM (injection-redteam): image/svg+xml KALDIRILDI. SVG optimize edilmeden R2'ye
# yazılıp cdn.meluni.com.tr'den inline servis ediliyordu → herhangi bir üye çalıştırılabilir
# SVG (stored-XSS) yükleyebiliyordu. Raster formatlar güvenli (PIL ile yeniden kodlanır).
_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "image/avif", "image/heic", "image/heif", "image/bmp",
}


@router.post("/image")
async def upload_image(file: UploadFile = File(...), user=Depends(require_admin)):  # yalnız personel (denetim 2026-09-29)
    """Görseli optimize edip (resize+WebP) Cloudflare R2'ye yükler; R2 kapalıysa MongoDB+disk'e düşer."""
    # GÜVENLİK: tür İÇERİKTEN (magic bytes) belirlenir; istemcinin Content-Type başlığı ve
    # dosya adı uzantısı yalnız bilgi amaçlıdır. Uzantıyı da sunucu seçer.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Dosya çok büyük (maks 25MB)")

    sniffed = _sniff_image(data)
    if not sniffed or sniffed[0] not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Sadece resim dosyaları yüklenebilir (jpeg/png/webp/gif/avif)")
    content_type, ext = sniffed
    # PIL CPU-yoğun ve yerel kod → AYRI SÜREÇTE (ADM-07): çökme/bellek taşması API'yi düşürmez.
    try:
        opt_data, opt_type, opt_ext = await run_image_job_isolated(
            _iw.optimize_for_upload, data, MAX_UPLOAD_LONG_EDGE, UPLOAD_WEBP_QUALITY)
    except ImageTaskTimeout:
        logger.warning(f"upload rejected: dönüştürme süre sınırını aştı ({content_type})")
        raise HTTPException(status_code=422, detail="Görsel işlenemedi (süre sınırı aşıldı); daha küçük bir görsel yükleyin")
    except ImageWorkerCrashed:
        logger.error(f"upload rejected: dönüştürme süreci çöktü ({content_type}); API etkilenmedi")
        raise HTTPException(status_code=422, detail="Görsel işlenemedi (dosya dönüştürücüyü çökertti); farklı bir görsel yükleyin")
    except _InvalidImage as e:
        logger.warning(f"upload rejected: PIL açamadı ({content_type}): {e}")
        detail = ("HEIC görseller desteklenmiyor; lütfen JPEG/PNG olarak yükleyin"
                  if content_type == "image/heic" else "Görsel okunamadı (bozuk ya da desteklenmeyen dosya)")
        raise HTTPException(status_code=400, detail=detail)
    if opt_data is not None:
        data, content_type, ext = opt_data, opt_type, opt_ext
    filename = f"{uuid.uuid4()}.{ext}"

    # 1) Cloudflare R2 (tercih edilen)
    if r2.is_enabled():
        key = f"uploads/{filename}"
        try:
            public_url = await asyncio.to_thread(r2.put_object, key, data, content_type)
            await db.files.insert_one({
                "id": str(uuid.uuid4()),
                "storage_path": filename,
                "r2_key": key,
                "r2_url": public_url,
                "original_filename": file.filename,
                "content_type": content_type,
                "size": len(data),
                "is_deleted": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return {"success": True, "path": filename, "url": public_url}
        except Exception as e:
            logger.error(f"R2 upload failed, falling back to db/disk: {e}")

    # 2) Fallback: disk (asıl) + MongoDB (yalnız diske yazılamadıysa)
    #
    # BASE64 KOPYA HER ZAMAN yazılıyordu — diske yazma başarılı olsa bile.
    # Her görsel veritabanında ~%33 şişmiş hâliyle yer kapladı ve Atlas'ın
    # 512 MB kotası doldu: "Writes are blocked", yani SİPARİŞ dahil her yazma
    # durdu. Görsel dosyadır; veritabanı yalnız gerçek çare kalmadığında.
    diske_yazildi = False
    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(data)
        diske_yazildi = True
    except Exception as e:
        logger.warning(f"upload disk write failed: {e}")

    _kayit = {
        "id": str(uuid.uuid4()),
        "storage_path": filename,
        "original_filename": file.filename,
        "content_type": content_type,
        "size": len(data),
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not diske_yazildi:
        _kayit["data_b64"] = base64.b64encode(data).decode("ascii")
    await db.files.insert_one(_kayit)

    return {
        "success": True,
        "path": filename,
        "url": f"/api/upload/files/{filename}",
    }


MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100 MB — hero slider videoları


@router.post("/video")
async def upload_video(file: UploadFile = File(...), user=Depends(require_admin)):  # yalnız personel (denetim 2026-09-29)
    """Video yükler (hero slider slaytı için) → Cloudflare R2 CDN'e; optimize edilmez,
    olduğu gibi CDN'den servis edilir (anasayfayı yormaz). R2 kapalıysa MongoDB+disk'e düşer."""
    # GÜVENLİK: tür İÇERİKTEN (magic bytes) belirlenir — istemci Content-Type'ı/dosya adı
    # güvenilmez; keyfi dosya (HTML/SVG/exe) public CDN'e "video" diye yazılamaz.
    data = await file.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=400, detail="Video çok büyük (maks 100MB)")
    sniffed = _sniff_video(data)
    if not sniffed:
        raise HTTPException(status_code=400, detail="Sadece video dosyaları yüklenebilir (mp4/webm/mov)")
    ct, ext = sniffed
    filename = f"{uuid.uuid4()}.{ext}"

    # 1) Cloudflare R2 (tercih edilen — CDN'den doğrudan servis)
    if r2.is_enabled():
        key = f"uploads/{filename}"
        try:
            public_url = await asyncio.to_thread(r2.put_object, key, data, ct)
            await db.files.insert_one({
                "id": str(uuid.uuid4()), "storage_path": filename, "r2_key": key,
                "r2_url": public_url, "original_filename": file.filename,
                "content_type": ct, "size": len(data), "is_video": True,
                "is_deleted": False, "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return {"success": True, "path": filename, "url": public_url, "is_video": True}
        except Exception as e:
            logger.error(f"R2 video upload failed, falling back to db/disk: {e}")

    # 2) Fallback: disk (asıl) + MongoDB (yalnız diske yazılamadıysa).
    # Videoda bu daha da kritik: tek dosya onlarca MB, kotayı tek başına yer.
    diske_yazildi = False
    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(data)
        diske_yazildi = True
    except Exception as e:
        logger.warning(f"video disk write failed: {e}")
    _kayit = {
        "id": str(uuid.uuid4()), "storage_path": filename,
        "original_filename": file.filename, "content_type": ct, "size": len(data),
        "is_video": True,
        "is_deleted": False, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not diske_yazildi:
        _kayit["data_b64"] = base64.b64encode(data).decode("ascii")
    await db.files.insert_one(_kayit)
    return {"success": True, "path": filename, "url": f"/api/upload/files/{filename}", "is_video": True}


# ?w= genişliği sabit kovalara yuvarlanır: rastgele w değerleriyle (w=1001,1002,…) her istekte
# yeni bir PIL dönüşümü + yeni CDN önbellek girdisi üretilemesin (CPU/önbellek DoS).
_W_BUCKETS = (160, 320, 480, 640, 800, 1080, 1440, 1920)


def _bucket_w(w, top: int = 1920) -> int:
    try:
        w = int(w or 0)
    except (TypeError, ValueError):
        return 0
    if w <= 0:
        return 0
    for b in _W_BUCKETS:
        if w <= b:
            return b
    return top


def _bucket_q(q) -> int:
    try:
        q = int(q)
    except (TypeError, ValueError):
        q = 90
    q = max(40, min(q, 90))
    return int(round(q / 10.0) * 10)


async def _serve(path: str, w: int = 0, q: int = 90):
    w = _bucket_w(w)
    q = _bucket_q(q)
    record = await db.files.find_one({"storage_path": path, "is_deleted": False})

    # 0) R2'ye taşınmış/yüklenmişse → R2 public URL'ine yönlendir (CDN servis eder).
    #    SADECE mutlak http(s) URL'e yönlendir; eski/bozuk relatif r2_url ("/uploads/..")
    #    varsa GÖZ ARDI et ve DB/disk içeriğinden servis et (görsel kaybolmasın).
    _r2 = (record or {}).get("r2_url") or ""
    if _r2 and str(_r2).lower().startswith("http"):
        return RedirectResponse(url=_r2, status_code=302)

    content = None
    # İÇERİK TÜRÜ kayıttan gelir; kayıt yoksa DOSYA UZANTISINDAN bulunur.
    # Sabit "image/jpeg" varsaymak, kaydı olmayan bir PNG/WEBP'i yanlış türle
    # servis ediyordu ve `nosniff` başlığı yüzünden tarayıcı görseli hiç
    # göstermiyordu. (Kayıtsız dosya artık normal: kota temizliğinde üst veri
    # kaydı silinip görsel diskte bırakılabiliyor.)
    ctype = (record or {}).get("content_type") or ""
    if not ctype:
        import mimetypes as _mt
        ctype = _mt.guess_type(path)[0] or "application/octet-stream"
    # 1) MongoDB içeriği (eski yüklemeler için güvenilir kaynak)
    if record and record.get("data_b64"):
        try:
            content = base64.b64decode(record["data_b64"])
        except Exception as e:
            logger.warning(f"serve db decode failed for {path}: {e}")
    # 2) Disk önbelleği (fallback) — PATH TRAVERSAL KORUMASI:
    #    `path` ham URL segmentidir. `..`, mutlak yol veya sembolik kaçışla
    #    UPLOAD_DIR dışına çıkılamamalı. realpath ile taban dizin içinde
    #    kaldığını doğrula; aksi halde 404 (bilgi sızdırma) ver.
    if content is None:
        base_dir = os.path.realpath(UPLOAD_DIR)
        candidate = os.path.realpath(os.path.join(base_dir, path))
        if (candidate == base_dir or candidate.startswith(base_dir + os.sep)) and os.path.isfile(candidate):
            with open(candidate, "rb") as f:
                content = f.read()
    # 2b) Gizli arşive taşınmış (manuel fatura PDF vb.) → backend üzerinden arşivden oku
    if content is None and (record or {}).get("archive_key"):
        try:
            from services import r2_storage as _r2a
            if _r2a.archive_enabled():
                content, _ct2 = await asyncio.to_thread(_r2a.archive_get, record["archive_key"])
        except Exception as e:
            logger.warning(f"archive serve failed {path}: {e}")
    if content is None:
        # UZANTILI ADRESİ DE ÇÖZ. Dosyalar uzantısız adlarla saklanıyor
        # ("...-hd0-06b3") ama Trendyol görsel adresinin .jpg/.png ile
        # bitmesini şart koşuyor — uzantısız adresi hiç indirmiyor. Adresin
        # sonundaki uzantıyı atıp bir daha deniyoruz; böylece aynı dosya iki
        # adresten de servis edilir ve eski bağlantılar kırılmaz.
        _kok, _uzanti = os.path.splitext(path)
        # "-mp" SONEKİ: pazaryeri tuvalini SORGU PARAMETRESİYLE değil YOLDA
        # taşıyoruz. Trendyol görsel adresinin uzantıyla bitmesini istiyor;
        # "...jpg?mp=1" biçimini kabul etmiyor. "...-mp.jpg" hem uzantıyla
        # bitiyor hem tuvali seçiyor.
        if _kok.endswith("-mp"):
            return await _serve(_kok[:-3] + _uzanti, w, q, 1)
        if _uzanti.lower() in (".jpg", ".jpeg", ".png", ".webp") and _kok != path:
            return await _serve(_kok, w, q, mp)
        raise HTTPException(status_code=404, detail="File not found")

    # İçerik-adresli (uuid) dosyalar değişmez → 1 yıl immutable cache (PageSpeed + tekrar ziyaret)
    headers = {"Cache-Control": "public, max-age=31536000, immutable",
               "X-Content-Type-Options": "nosniff"}
    # SVG XSS KORUMASI: SVG top-level açılırsa içindeki <script> çalışır. attachment ile
    # top-level render engellenir (<img> ile gömme etkilenmez, orada script zaten çalışmaz).
    if "svg" in (ctype or "").lower():
        headers["Content-Disposition"] = "attachment"
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"

    # Pazaryeri tuvali — ?mp=1 verildiyse (Trendyol 1200x1800 şartı).
    if mp and ctype.startswith("image/") and "svg" not in ctype:
        try:
            return Response(content=_pazaryeri_tuvali(content),
                            media_type="image/jpeg", headers=headers)
        except Exception as e:
            logger.warning(f"pazaryeri tuvali başarısız {path}: {e}")

    # On-the-fly optimize/resize — yalnızca ?w= verildiyse ve raster görselse.
    if w and w > 0 and ctype.startswith("image/") and "svg" not in ctype:
        try:
            # PIL CPU-yoğun ve yerel kod → AYRI SÜREÇTE (ADM-07); eşzamanlı dönüşüm sayısı JPEG
            # proxy ile ORTAK semaforla sınırlı (işlemci doyup site beklemesin).
            async with _JPEG_SEM:
                _webp = await run_image_job_isolated(_iw.resize_webp, content, w, q)
            return Response(content=_webp, media_type="image/webp", headers=headers)
        except Exception as e:
            logger.warning(f"resize failed {path}: {e}")
    return Response(content=content, media_type=ctype, headers=headers)


# =============================================================================
# JPEG proxy — pazaryeri (Amazon) görsel aktarımı için. Amazon WebP KABUL ETMEZ
# (yalnız JPEG/PNG/TIFF/GIF); ürün görsellerimiz R2'de WebP saklanıyor → Amazon
# webp'leri sessizce düşürüyordu ("resimler aktarılmıyor"). Bu uç origin görseli
# çekip tam çözünürlükte (≤2000px) JPEG'e çevirir. Cloudflare cdn-cgi transform
# kotasına (ERROR 9422) bağlı DEĞİL → güvenilir. Amazon URL'i bir kez çekip cache'ler.
# =============================================================================
_JPEG_ALLOWED_HOSTS = {"cdn.meluni.com.tr", "static.ticimax.cloud"}
# Pazaryeri (HB/Amazon) yüzlerce görseli AYNI ANDA ilk kez çektiğinde her biri ayrı PIL
# dönüşümü yapıyordu → işlemci doyup panel + vitrin saniyelerce bekliyordu. Aynı anda en
# fazla 2 dönüşüm; sonuçlar küçük bir bellek önbelleğinde (≈64 MB) tutulur.
_JPEG_SEM = asyncio.Semaphore(2)
_JPEG_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_JPEG_CACHE_MAX = 64 * 1024 * 1024
_jpeg_cache_bytes = 0


def _jpeg_cache_put(key: str, data: bytes):
    global _jpeg_cache_bytes
    if len(data) > 8 * 1024 * 1024:
        return
    if key in _JPEG_CACHE:
        return
    _JPEG_CACHE[key] = data
    _jpeg_cache_bytes += len(data)
    while _jpeg_cache_bytes > _JPEG_CACHE_MAX and _JPEG_CACHE:
        _, old = _JPEG_CACHE.popitem(last=False)
        _jpeg_cache_bytes -= len(old)


@router.get("/jpeg/{token}.jpg")
async def image_to_jpeg_ext(token: str, w: int = 2000):
    """Amazon media_location için .jpg UZANTILI proxy: /api/upload/jpeg/<urlsafe-b64(src)>.jpg.
    Amazon (ERROR 20015) dosya türünü URL/uzantı + içerikten doğrular; uzantısız
    ?src= biçimi reddediliyordu. Aynı SSRF/boyut korumalarıyla to-jpeg'e delege eder."""
    import base64
    try:
        pad = "=" * (-len(token) % 4)
        src = base64.urlsafe_b64decode((token + pad).encode()).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz görsel anahtarı")
    return await image_to_jpeg(src, w)


@router.get("/to-jpeg")
async def image_to_jpeg(src: str, w: int = 2000):
    """src (yalnız izinli CDN host) görselini tam çözünürlükte JPEG'e çevirip döndürür.
    SSRF koruması: host beyaz-listesi. Amazon media_location için kullanılır."""
    from urllib.parse import urlparse
    try:
        u = urlparse(src)
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz URL")
    if u.scheme not in ("http", "https") or (u.hostname or "") not in _JPEG_ALLOWED_HOSTS:
        raise HTTPException(status_code=400, detail="İzin verilmeyen kaynak")
    # w sabit kovalara yuvarlanır (önbellek anahtarı + dönüşüm AYNI değeri kullanır);
    # 1920 üstü → 2000 (Amazon tam çözünürlük varsayılanı korunur).
    w = _bucket_w(w, top=2000) or 2000
    _ck = f"{src}|{w}"
    _hdr = {"Cache-Control": "public, max-age=31536000, immutable"}
    if _ck in _JPEG_CACHE:
        _JPEG_CACHE.move_to_end(_ck)
        return Response(content=_JPEG_CACHE[_ck], media_type="image/jpeg", headers=_hdr)
    try:
        async with _JPEG_SEM:
            if _ck in _JPEG_CACHE:
                return Response(content=_JPEG_CACHE[_ck], media_type="image/jpeg", headers=_hdr)
            data = await _to_jpeg_bytes(src, w)
        _jpeg_cache_put(_ck, data)
        return Response(content=data, media_type="image/jpeg", headers=_hdr)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[to-jpeg] dönüştürme hatası src={src[:120]}: {e}")
        raise HTTPException(status_code=502, detail="Görsel dönüştürülemedi")


async def _to_jpeg_bytes(src: str, w: int) -> bytes:
    import httpx
    # DENETİM (cost-redteam #6): indirilen boyutu ve piksel sayısını sınırla
    # (decompression-bomb RAM/CPU + CDN egress amplifikasyonu engeli).
    # Piksel tavanı modül düzeyinde (_MAX_IMAGE_PIXELS) set edilir.
    _max_src = 20 * 1024 * 1024  # 20MB kaynak üst sınırı
    # DENETİM (verify-redteam #1): follow_redirects=False — aksi halde izinli host bir
    # yönlendirmeyle iç ağ/özel IP'ye (SSRF) yönlendirebilirdi (allowlist yalnız ilk hop'ta).
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        async with client.stream("GET", src) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                raise HTTPException(status_code=400, detail="Yönlendirme kabul edilmez")
            r.raise_for_status()
            try:
                _cl = int(r.headers.get("content-length") or 0)
            except ValueError:
                _cl = 0
            if _cl > _max_src:
                raise HTTPException(status_code=413, detail="Kaynak görsel çok büyük")
            # AKIŞLA oku, tavanı aşınca HEMEN kes (önce hepsini belleğe alıp sonra bakma)
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _max_src:
                    raise HTTPException(status_code=413, detail="Kaynak görsel çok büyük")
            raw = bytes(buf)
    # PIL CPU-yoğun ve yerel kod → AYRI SÜREÇTE, süre sınırıyla (ADM-07).
    try:
        return await run_image_job_isolated(_iw.to_jpeg, raw, w)
    except ImageTooLarge:
        raise HTTPException(status_code=413, detail="Kaynak görsel çok büyük")
    except ImageTaskTimeout:
        raise HTTPException(status_code=503, detail="Görsel dönüştürme zaman aşımı")
    except ImageWorkerCrashed:
        raise HTTPException(status_code=502, detail="Görsel dönüştürülemedi")


@router.get("/files/{path:path}")
async def get_file(path: str, w: int = 0, q: int = 90, mp: int = 0):
    return await _serve(path, w, q, mp)


@files_router.get("/{path:path}")
async def get_file_legacy(path: str, w: int = 0, q: int = 90, mp: int = 0):
    """Eski kayıtlardaki /api/files/... URL'lerini de servis et (resize destekli)."""
    return await _serve(path, w, q, mp)
