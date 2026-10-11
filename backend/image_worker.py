"""
image_worker.py — Görsel çözümleme/dönüştürme SÜREÇ İZOLASYONU (ADM-07 / T18 / SHOP02).

NEDEN: Yüklenen ya da dışarıdan indirilen görsellerin Pillow ile çözümlenmesi (decode) yerel (C)
koddur. Bozuk/kötü niyetli bir dosya Pillow'da yerel bir çökmeye (segfault) ya da bellek taşmasına
yol açarsa, dönüştürme API süreci İÇİNDE çalıştığında tüm backend (checkout dahil) düşerdi; süre
sınırını aşan iş parçacığı da arka planda sürmeye devam ederdi.

TASARIM:
  • Sınırlı iş parçacığı havuzu (`_POOL`, IMAGE_WORKERS adet, "img-convert" adlı) eşzamanlılığı
    sınırlar — eskisi gibi. Her iş parçacığı KENDİ tek-işçili `ProcessPoolExecutor`'ına (spawn
    bağlamı, `max_tasks_per_child`) sahiptir → en fazla IMAGE_WORKERS çocuk süreç.
  • Asıl çözümleme/dönüştürme çocuk süreçte koşar (bu modüldeki üst düzey iş fonksiyonları;
    çocuk yalnız bu hafif modülü + Pillow'u içe aktarır — uygulama/DB içe aktarılmaz).
  • Süre sınırı aşılırsa O işin çocuk süreci ÖLDÜRÜLÜR (iş arka planda sürmez) ve havuz bir
    sonraki işte yeniden kurulur → çağırana ImageTaskTimeout.
  • Çocuk süreç çökerse (segfault, os._exit, OOM-killer) `BrokenProcessPool` yakalanır, havuz
    atılır/yeniden kurulur → çağırana ImageWorkerCrashed (çağıran temiz bir 4xx/5xx döner).
    API süreci etkilenmez; sıradaki dönüşüm yeni bir çocukta çalışır.
  • Çocuk sürecin sanal belleği sınırlıdır (RLIMIT_AS, IMAGE_WORKER_MAX_MEM_MB, varsayılan 1536):
    taşma çocukta MemoryError olur; ayrıca çocuk OOM-killer'ın tercihli kurbanıdır
    (oom_score_adj=1000) → konteyner belleği daralırsa API süreci değil dönüştürücü ölür.
  • Piksel tavanı ve boyut sınırları AYNEN korunur (başlıktan, çözümlemeden önce).
  • Çocuk süreç "spawn" ile başlar (çok iş parçacıklı API sürecini fork etmez). Üretimde
    `python -m uvicorn server:app` (Procfile) ile ana modül yeniden içe aktarılmaz.
  • Süreç oluşturulamazsa (platform sem_open desteklemiyor, izin yok…) ya da ortam değişkeni
    IMAGE_PROCESS_ISOLATION=0 ise iş, eskisi gibi sınırlı iş parçacığında çalışır (temiz geri
    dönüş); durum `isolation_status()` ile görülür (Güvenlik öz-testi raporlar).

Çağıranlar:  await run_image_job_isolated(<bu modüldeki iş>, *args, timeout=...)
Genel (izolasyonsuz) iş parçacığı işi:  await run_image_job(fn, *args, timeout=...)
"""
from __future__ import annotations

import asyncio
import functools
import io
import logging
import os
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FuturesTimeout

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 40_000_000
IMAGE_WORKERS = 2
IMAGE_TASK_TIMEOUT_S = 20.0
# Çocuk süreç bu kadar işten sonra yenilenir (Pillow/libwebp bellek parçalanması birikmesin).
MAX_TASKS_PER_CHILD = 200
# Çocuk süreç sanal bellek tavanı (MB; env IMAGE_WORKER_MAX_MEM_MB, 0 = sınırsız). 40 MP RGBA ≈ 160 MB
# çözülmüş + yeniden boyutlandırma ara tamponları için bol pay.
DEFAULT_CHILD_MAX_MEM_MB = 1536


class ImageTaskTimeout(Exception):
    """Görsel dönüştürme işi süre sınırını aştı (izolasyonlu işte çocuk süreç öldürüldü)."""


class ImageTooLarge(Exception):
    """Görselin piksel sayısı tavanı aşıyor (başlıktan, çözümlemeden önce tespit edildi)."""


class InvalidImage(Exception):
    """Pillow içeriği gerçek bir görsel olarak AÇAMADI → yükleme reddedilir."""


class ImageWorkerCrashed(Exception):
    """Dönüştürme çocuk süreci çöktü (yerel çökme / bellek taşması). API süreci etkilenmedi."""


def _pil_image():
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    return Image


# ═══════════════════════════ Başlık denetimi (çözümlemeden önce) ═══════════════════════════

def check_image_pixels(im, max_pixels: int = None) -> None:
    """Açılmış (henüz çözümlenmemiş) PIL görselinin başlık boyutlarını tavana göre denetler."""
    cap = int(max_pixels or MAX_IMAGE_PIXELS)
    try:
        w, h = im.size
    except Exception:  # noqa: BLE001
        raise ImageTooLarge("boyut okunamadı")
    if w <= 0 or h <= 0 or w * h > cap:
        raise ImageTooLarge(f"{w}x{h} piksel tavanı ({cap}) aşıyor")


def open_image_guarded(data: bytes, max_pixels: int = None):
    """Görseli yalnız BAŞLIĞIYLA açar ve piksel tavanını denetler (çözümleme yapmaz)."""
    Image = _pil_image()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # >MAX uyarısı: kendi tavanımız aşağıda karar verir
        im = Image.open(io.BytesIO(data))  # DecompressionBombError (>2×MAX) burada fırlar
    check_image_pixels(im, max_pixels)
    return im


# ═══════════════════════ Çocuk süreçte koşan işler (üst düzey, pickle'lanabilir) ═══════════════════════

def optimize_for_upload(data: bytes, max_long_edge: int = 2000, quality: int = 88):
    """Yüklenen görseli WebP'e çevirip gerekirse küçültür → (bytes, mime, ext).

    - Açılamazsa (bozuk/sahte/bomba/piksel tavanı) → InvalidImage.
    - Animasyonlu GIF/WebP ya da açılıp dönüştürülemeyen görsel → (None, None, None): çağıran
      doğrulanmış orijinali yükler (eski davranış)."""
    try:
        im = open_image_guarded(data)   # piksel tavanı BAŞLIKTAN (çözümlemeden önce) denetlenir
        im.load()  # gerçekten çözülebiliyor mu (yalnız başlık değil)
    except Exception as e:  # noqa: BLE001 — DecompressionBombError / ImageTooLarge dahil
        raise InvalidImage(str(e)[:200])
    try:
        from PIL import ImageOps
        Image = _pil_image()
        if getattr(im, "is_animated", False):
            return None, None, None
        im = ImageOps.exif_transpose(im)  # telefon fotoğraflarında yanlış dönüşü düzelt
        if max(im.width, im.height) > max_long_edge:
            ratio = max_long_edge / float(max(im.width, im.height))
            im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS)
        # Şeffaflığı koru (RGBA/LA/transparan P), yoksa RGB'ye düş
        if im.mode == "P" and "transparency" in im.info:
            im = im.convert("RGBA")
        elif im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="WEBP", quality=int(quality), method=6)
        return out.getvalue(), "image/webp", "webp"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"upload optimize failed, orijinal yüklenecek: {e}")
        return None, None, None


def resize_webp(content: bytes, w: int, q: int = 90) -> bytes:
    """?w= yeniden boyutlandırma → WebP baytları."""
    Image = _pil_image()
    im = open_image_guarded(content)   # piksel tavanı çözümlemeden önce
    if im.width > w:
        ratio = w / float(im.width)
        im = im.resize((w, max(1, int(im.height * ratio))), Image.LANCZOS)
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=max(40, min(int(q), 95)), method=4)
    return out.getvalue()


def to_jpeg(raw: bytes, w: int = 2000) -> bytes:
    """Pazaryeri JPEG proxy'si: şeffaflık beyaz zemine, ≤2000 px JPEG."""
    from PIL import ImageOps
    Image = _pil_image()
    img = open_image_guarded(raw)   # piksel tavanı çözümlemeden önce (decompression-bomb)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    _w = max(200, min(int(w or 2000), 2000))
    if img.width > _w:
        img.thumbnail((_w, _w * 4), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def normalize_rgb_jpeg(raw: bytes, max_w: int = 1200, max_h: int = 1800):
    """Güvenilmeyen görseli çocuk süreçte çözüp oranı koruyarak küçültür ve KENDİ ürettiğimiz
    küçük bir JPEG'e çevirir (ölçü tablosu şablonu gibi ana süreçte çizim yapan yollar için).
    Açılamazsa None."""
    try:
        Image = _pil_image()
        im = open_image_guarded(raw).convert("RGB")
        r = min(1.0, max_w / float(im.width), max_h / float(im.height))
        if r < 1.0:
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def chart_like(content: bytes) -> bool:
    """Ölçü tablosu görseli mi? 1200×1800 ve dört kenar şeridi saf beyaz."""
    from PIL import ImageStat
    im = open_image_guarded(content)
    if im.size != (1200, 1800):
        return False
    g = im.convert("L")
    w, h = g.size
    b = 25
    for box in [(0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h)]:
        if ImageStat.Stat(g.crop(box)).mean[0] < 254.0:
            return False
    return True


def _child_init(max_mem_mb: int) -> None:
    """Çocuk süreç başlangıcı: bellek tavanı (RLIMIT_AS → taşmada MemoryError, konteyner OOM'u
    değil) ve çekirdek OOM-killer'ı için TERCİHLİ KURBAN işareti (oom_score_adj=1000) — bellek
    yetmezse önce dönüştürücü ölür, API süreci değil. Desteklenmeyen platformda sessizce atlanır."""
    try:
        import resource
        if max_mem_mb and max_mem_mb > 0:
            lim = int(max_mem_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except Exception:  # noqa: BLE001
        pass


def _child_call(fn, args, kwargs):
    """Çocuk süreçte: işi çalıştır, sonucu çocuk PID'iyle döndür (izolasyon kanıtı için)."""
    return os.getpid(), fn(*args, **kwargs)


# ═══════════════════════════════ Ana süreç tarafı (havuzlar) ═══════════════════════════════

_POOL = ThreadPoolExecutor(max_workers=IMAGE_WORKERS, thread_name_prefix="img-convert")
_TLS = threading.local()
_LOCK = threading.Lock()
_STATE = {"mode": "process", "spawned": 0, "crashes": 0, "timeouts_killed": 0, "jobs": 0,
          "fallback_jobs": 0, "last_pid": 0, "last_error": "", "last_crash_at": ""}
_OFF = ("0", "off", "false", "no", "disable")


def isolation_status() -> dict:
    """Süreç izolasyonu durumu (öz-test / tanı). Kişisel veri içermez."""
    with _LOCK:
        st = dict(_STATE)
    if (os.environ.get("IMAGE_PROCESS_ISOLATION") or "").strip().lower() in _OFF:
        st["mode"] = "disabled"
    st["workers"] = IMAGE_WORKERS
    st["parent_pid"] = os.getpid()
    return st


def _isolation_on() -> bool:
    if (os.environ.get("IMAGE_PROCESS_ISOLATION") or "").strip().lower() in _OFF:
        return False
    return _STATE["mode"] == "process"


def _bump(key: str, n: int = 1) -> None:
    with _LOCK:
        _STATE[key] = int(_STATE.get(key) or 0) + n


def _max_mem_mb() -> int:
    try:
        return max(0, int((os.environ.get("IMAGE_WORKER_MAX_MEM_MB") or str(DEFAULT_CHILD_MAX_MEM_MB)).strip()))
    except ValueError:
        return DEFAULT_CHILD_MAX_MEM_MB


def _new_executor():
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    kw = {"max_workers": 1, "mp_context": mp.get_context("spawn"),
          "initializer": _child_init, "initargs": (_max_mem_mb(),)}
    if sys.version_info >= (3, 11):
        kw["max_tasks_per_child"] = MAX_TASKS_PER_CHILD
    ex = ProcessPoolExecutor(**kw)
    _bump("spawned")
    return ex


def _discard_executor(ex, *, kill: bool) -> None:
    """Havuzu at; `kill` ise çocuk süreçleri hemen öldür (süre aşımı / çökme sonrası)."""
    if ex is None:
        return
    if kill:
        for p in list((getattr(ex, "_processes", None) or {}).values()):
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001
        pass
    if getattr(_TLS, "ex", None) is ex:
        _TLS.ex = None


def _fallback(err: Exception) -> None:
    with _LOCK:
        first = _STATE["mode"] == "process"
        _STATE["mode"] = "thread-fallback"
        _STATE["last_error"] = f"{type(err).__name__}: {str(err)[:160]}"
    if first:
        logger.error("[image-worker] süreç izolasyonu kurulamadı, iş parçacığına düşüldü: %s: %s",
                     type(err).__name__, err)


def _submit(fn, args, kwargs):
    """İş parçacığının kendi süreç havuzuna gönder; bozuk havuzu bir kez yeniden kurar."""
    from concurrent.futures.process import BrokenProcessPool
    for attempt in (1, 2):
        ex = getattr(_TLS, "ex", None)
        if ex is None:
            ex = _new_executor()
            _TLS.ex = ex
        try:
            return ex, ex.submit(_child_call, fn, args, kwargs)
        except (BrokenProcessPool, RuntimeError):
            # Önceki çökmeden kalmış/kapanmış havuz → at ve bir kez yeniden kur.
            _discard_executor(ex, kill=True)
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def _run_isolated_blocking(fn, args, kwargs, deadline: float):
    """`_POOL` iş parçacığında çalışır: işi çocuk süreçte koştur, süre/çökme durumunu yönet."""
    from concurrent.futures.process import BrokenProcessPool
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ImageTaskTimeout()          # kuyrukta beklerken süre doldu → hiç çalıştırma
    if not _isolation_on():
        _bump("fallback_jobs")
        return fn(*args, **kwargs)
    try:
        ex, fut = _submit(fn, args, kwargs)
    except BrokenProcessPool:
        _bump("crashes")
        raise ImageWorkerCrashed()
    except Exception as e:  # noqa: BLE001 — süreç oluşturulamadı (platform/izin) → temiz geri dönüş
        _fallback(e)
        _bump("fallback_jobs")
        return fn(*args, **kwargs)
    _bump("jobs")
    try:
        pid, result = fut.result(timeout=max(0.001, deadline - time.monotonic()))
    except _FuturesTimeout:
        _discard_executor(ex, kill=True)   # iş arka planda SÜRMEZ: çocuk süreç öldürülür
        _bump("timeouts_killed")
        raise ImageTaskTimeout()
    except BrokenProcessPool:
        _discard_executor(ex, kill=True)
        with _LOCK:
            _STATE["crashes"] += 1
            _STATE["last_crash_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.error("[image-worker] dönüştürme çocuk süreci çöktü (%s); havuz yeniden kurulacak",
                     getattr(fn, "__name__", "?"))
        raise ImageWorkerCrashed()
    with _LOCK:
        _STATE["last_pid"] = int(pid or 0)
    return result


async def run_image_job_isolated(fn, *args, timeout: float = None, **kwargs):
    """`fn(*args, **kwargs)`'ı AYRI bir süreçte (sınırlı havuz + süre sınırı) çalıştırır.
    `fn` üst düzey (pickle'lanabilir) bir fonksiyon olmalı ve hafif bir modülde durmalı
    (çocuk süreç modülü içe aktarır). Hatalar: ImageTaskTimeout, ImageWorkerCrashed ya da
    işin kendi istisnası (ör. ImageTooLarge, InvalidImage)."""
    t = float(timeout or IMAGE_TASK_TIMEOUT_S)
    deadline = time.monotonic() + t
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(_POOL, functools.partial(_run_isolated_blocking, fn, args, kwargs, deadline))
    try:
        return await asyncio.wait_for(fut, timeout=t)
    except asyncio.TimeoutError:
        raise ImageTaskTimeout()


async def run_image_job(fn, *args, timeout: float = None, **kwargs):
    """`fn(*args, **kwargs)`'ı sınırlı görsel İŞ PARÇACIĞI havuzunda süre sınırıyla çalıştırır
    (izolasyonsuz; güvenilir/kendi ürettiğimiz içerik ya da pickle'lanamayan işler için)."""
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(_POOL, functools.partial(fn, *args, **kwargs))
    try:
        return await asyncio.wait_for(fut, timeout=float(timeout or IMAGE_TASK_TIMEOUT_S))
    except asyncio.TimeoutError:
        raise ImageTaskTimeout()


def prewarm() -> None:
    """Açılışta çocuk süreçleri önceden başlat (ilk yüklemede spawn gecikmesi olmasın). Asla fırlatmaz."""
    if not _isolation_on():
        return

    def _warm():
        try:
            _run_isolated_blocking(os.getpid, (), {}, time.monotonic() + 60)
        except Exception as e:  # noqa: BLE001
            logger.warning("[image-worker] ön ısıtma başarısız: %s", type(e).__name__)
    for _ in range(IMAGE_WORKERS):
        try:
            _POOL.submit(_warm)
        except Exception:  # noqa: BLE001
            return
