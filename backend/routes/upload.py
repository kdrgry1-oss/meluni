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

APP_NAME = "facette"
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

    # 2) Fallback: MongoDB (kalıcı) + disk (önbellek)
    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(data)
    except Exception as e:
        logger.warning(f"upload disk write failed: {e}")

    await db.files.insert_one({
        "id": str(uuid.uuid4()),
        "storage_path": filename,
        "original_filename": file.filename,
        "content_type": content_type,
        "size": len(data),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

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

    # 2) Fallback: disk + MongoDB
    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(data)
    except Exception as e:
        logger.warning(f"video disk write failed: {e}")
    await db.files.insert_one({
        "id": str(uuid.uuid4()), "storage_path": filename,
        "original_filename": file.filename, "content_type": ct, "size": len(data),
        "data_b64": base64.b64encode(data).decode("ascii"), "is_video": True,
        "is_deleted": False, "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "path": filename, "url": f"/api/upload/files/{filename}", "is_video": True}


async def _serve(path: str, w: int = 0, q: int = 90):
    record = await db.files.find_one({"storage_path": path, "is_deleted": False})

    # 0) R2'ye taşınmış/yüklenmişse → R2 public URL'ine yönlendir (CDN servis eder).
    #    SADECE mutlak http(s) URL'e yönlendir; eski/bozuk relatif r2_url ("/uploads/..")
    #    varsa GÖZ ARDI et ve DB/disk içeriğinden servis et (görsel kaybolmasın).
    _r2 = (record or {}).get("r2_url") or ""
    if _r2 and str(_r2).lower().startswith("http"):
        return RedirectResponse(url=_r2, status_code=302)

    content = None
    ctype = (record or {}).get("content_type", "image/jpeg")
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
        raise HTTPException(status_code=404, detail="File not found")

    # İçerik-adresli (uuid) dosyalar değişmez → 1 yıl immutable cache (PageSpeed + tekrar ziyaret)
    headers = {"Cache-Control": "public, max-age=31536000, immutable",
               "X-Content-Type-Options": "nosniff"}
    # SVG XSS KORUMASI: SVG top-level açılırsa içindeki <script> çalışır. attachment ile
    # top-level render engellenir (<img> ile gömme etkilenmez, orada script zaten çalışmaz).
    if "svg" in (ctype or "").lower():
        headers["Content-Disposition"] = "attachment"
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"

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
async def get_file(path: str, w: int = 0, q: int = 90):
    return await _serve(path, w, q)


@files_router.get("/{path:path}")
async def get_file_legacy(path: str, w: int = 0, q: int = 90):
    """Eski kayıtlardaki /api/files/... URL'lerini de servis et (resize destekli)."""
    return await _serve(path, w, q)
