from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import Response, RedirectResponse
from datetime import datetime, timezone
import base64
import uuid
import os

from .deps import db, get_current_user, require_auth, logger
from services import r2_storage as r2

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


def _optimize_for_upload(data: bytes, content_type: str):
    """Yüklenen görseli WebP'e çevirip gerekirse küçültür.

    Animasyonlu GIF / SVG gibi optimize edilemeyecek türlerde (None, None, None)
    döner → çağıran orijinal bytes'ı olduğu gibi yükler (davranış bozulmaz).
    """
    if not content_type or "svg" in content_type:
        return None, None, None
    try:
        from PIL import Image, ImageOps
        import io

        im = Image.open(io.BytesIO(data))

        # Animasyonlu GIF/WebP → dokunma (kareleri kaybetmemek için)
        if getattr(im, "is_animated", False):
            return None, None, None

        im = ImageOps.exif_transpose(im)  # telefon fotoğraflarında yanlış dönüşü düzelt

        if max(im.width, im.height) > MAX_UPLOAD_LONG_EDGE:
            ratio = MAX_UPLOAD_LONG_EDGE / float(max(im.width, im.height))
            im = im.resize(
                (max(1, int(im.width * ratio)), max(1, int(im.height * ratio))),
                Image.LANCZOS,
            )

        # Şeffaflığı koru (RGBA/LA/transparan P), yoksa RGB'ye düş
        if im.mode == "P" and "transparency" in im.info:
            im = im.convert("RGBA")
        elif im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")

        out = io.BytesIO()
        im.save(out, format="WEBP", quality=UPLOAD_WEBP_QUALITY, method=6)
        return out.getvalue(), "image/webp", "webp"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"upload optimize failed, orijinal yüklenecek: {e}")
        return None, None, None


_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "image/svg+xml", "image/avif", "image/heic", "image/heif", "image/bmp",
}


@router.post("/image")
async def upload_image(file: UploadFile = File(...), user=Depends(require_auth)):
    """Görseli optimize edip (resize+WebP) Cloudflare R2'ye yükler; R2 kapalıysa MongoDB+disk'e düşer."""
    # GÜVENLİK: geniş "image/*" yerine açık allowlist — sahte/garip content-type reddedilir.
    if not file.content_type or file.content_type.split(";")[0].strip().lower() not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Sadece resim dosyaları yüklenebilir (jpeg/png/webp/gif/svg)")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Dosya çok büyük (maks 25MB)")

    content_type = file.content_type
    opt_data, opt_type, opt_ext = _optimize_for_upload(data, content_type)
    if opt_data is not None:
        data, content_type = opt_data, opt_type
        ext = opt_ext
    else:
        ext = file.filename.split(".")[-1].lower() if "." in (file.filename or "") else "jpg"
    filename = f"{uuid.uuid4()}.{ext}"

    # 1) Cloudflare R2 (tercih edilen)
    if r2.is_enabled():
        key = f"uploads/{filename}"
        try:
            public_url = r2.put_object(key, data, content_type)
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
async def upload_video(file: UploadFile = File(...), user=Depends(require_auth)):
    """Video yükler (hero slider slaytı için) → Cloudflare R2 CDN'e; optimize edilmez,
    olduğu gibi CDN'den servis edilir (anasayfayı yormaz). R2 kapalıysa MongoDB+disk'e düşer."""
    ct = (file.content_type or "").split(";")[0].strip().lower()
    # GÜVENLİK: açık video allowlist — sahte content-type ile keyfi dosya deposu engeli.
    _ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime",
                            "video/x-msvideo", "video/mpeg", "video/ogg"}
    if ct not in _ALLOWED_VIDEO_TYPES:
        raise HTTPException(status_code=400, detail="Sadece video dosyaları yüklenebilir (mp4/webm/mov)")
    data = await file.read()
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=400, detail="Video çok büyük (maks 100MB)")
    ext = (file.filename.split(".")[-1].lower() if "." in (file.filename or "") else "mp4")
    if ext not in ("mp4", "webm", "mov", "m4v", "ogg"):
        ext = "mp4"
    filename = f"{uuid.uuid4()}.{ext}"

    # 1) Cloudflare R2 (tercih edilen — CDN'den doğrudan servis)
    if r2.is_enabled():
        key = f"uploads/{filename}"
        try:
            public_url = r2.put_object(key, data, ct)
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


# Pazaryeri görsel ölçüsü: Trendyol giyimde en az 1200x1800 (2:3) istiyor.
# Bizim iyileştirilmiş görsellerimiz 1200x1600 (3:4) — vitrin oranı bu. Küçük
# oranlı görseli Trendyol ürünü reddetmeden ALMIYOR: ürün açılıyor, görseller
# boş kalıyor (canlıda yaşandı). Vitrini bozmamak için dosyayı DEĞİŞTİRMİYORUZ;
# yalnız `?mp=1` ile istendiğinde tuval 2:3'e tamamlanıp servis ediliyor.
MP_TARGET = (1200, 1800)


def _pazaryeri_tuvali(icerik: bytes) -> bytes:
    """Görseli KIRPMADAN 1200x1800 tuvale oturtur (beyaz zemin)."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(icerik))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    hedef_g, hedef_y = MP_TARGET
    oran = min(hedef_g / im.width, hedef_y / im.height)
    yeni = im.resize((max(1, int(im.width * oran)), max(1, int(im.height * oran))),
                     Image.LANCZOS)
    tuval = Image.new("RGB", MP_TARGET, (255, 255, 255))
    tuval.paste(yeni, ((hedef_g - yeni.width) // 2, (hedef_y - yeni.height) // 2))
    cikti = io.BytesIO()
    tuval.save(cikti, format="JPEG", quality=92, optimize=True)
    return cikti.getvalue()


async def _serve(path: str, w: int = 0, q: int = 90, mp: int = 0):
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
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(content))
            if im.width > w:
                ratio = w / float(im.width)
                im = im.resize((w, max(1, int(im.height * ratio))), Image.LANCZOS)
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=max(40, min(int(q), 95)), method=4)
            return Response(content=out.getvalue(), media_type="image/webp", headers=headers)
        except Exception as e:
            logger.warning(f"resize failed {path}: {e}")
    return Response(content=content, media_type=ctype, headers=headers)


@router.get("/files/{path:path}")
async def get_file(path: str, w: int = 0, q: int = 90, mp: int = 0):
    return await _serve(path, w, q, mp)


@files_router.get("/{path:path}")
async def get_file_legacy(path: str, w: int = 0, q: int = 90, mp: int = 0):
    """Eski kayıtlardaki /api/files/... URL'lerini de servis et (resize destekli)."""
    return await _serve(path, w, q, mp)
