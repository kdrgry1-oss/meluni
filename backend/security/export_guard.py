"""
Akan dışa aktarım koruması (CHAIN09 / ADM-02) — "güncel erişim iptali veri okumayı ve indirmeyi durdurur".

Sorun: toplu dışa aktarım (Excel/CSV) tek bir istekte üretilip akış olarak döner. Personel o sırada
pasifleştirilirse, oturumları iptal edilirse ya da freeze_exports açılırsa, yanıt yine de sonuna kadar
gönderiliyordu (yetki yalnız istek başında denetleniyordu).

Çözüm (tek merkez, uç başına kod yok):
  * Her toplu dışa aktarım ucu zaten `security_switches.require_switch_off("freeze_exports")` kapısından
    geçer. Kapı geçilince `register_export()` bu isteği "dışa aktarım" olarak işaretler ve isteği yapan
    personelin kimliğini + token'ının iptal-ilgili taleplerini (tv / ep / iat; sır yok) yakalar.
  * `ExportGuardMiddleware` (saf ASGI, en dışta) yalnız işaretli isteklerde:
      1. yanıt başlığı gönderilmeden HEMEN ÖNCE (bellekte üretilen dışa aktarımlar için "döndürmeden
         hemen önce") yeniden denetler; başarısızsa dosya yerine 401/423/503 JSON hata döner;
      2. gövdeyi ≤64 KB parçalara bölerek gönderir ve parçalar ARASINDA her ~5 sn'de (ya da her N
         parçada) bir yeniden denetler; başarısızsa akış bir hata işaret satırıyla KESİLİR, kalan
         baytlar gönderilmez (dosya eksik/bozuk kalır, Content-Length kaldırıldığı için istemci
         "tamamlandı" sanmaz) ve güvenlik olayı yazılır (denetim zinciri + kritik uyarı).
  * Denetim UCUZ: aralık başına TEK küçük DB okuması (users, dar projeksiyon); küresel oturum dönemi
    ve anahtarlar zaten süreç-içi ≤10 sn önbellekten okunur (aynı süreçteki iptal/anahtar değişikliği
    önbelleği anında günceller). Satır başına okuma YOK.
  * Fail-closed: personel durumu okunamazsa (DB hatası) akış durdurulur ("güncel personel durumu
    doğrulanamıyorsa export bekletilir").

Denetlenenler: hesap aktif mi · hâlâ personel mi (is_admin + rol/süper admin) · rolü/süper admin
yetkisi değişti mi · token_version arttı mı (şifre değişimi / logout-all / pasifleştirme) · küresel
oturum iptali (security epoch) · freeze_exports açıldı mı.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# İstek başına değiştirilebilir sözlük: middleware koyar, freeze_exports kapısı "export" anahtarını yazar.
EXPORT_CTX: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("export_guard_ctx", default=None)

RECHECK_INTERVAL_SEC = 5.0     # akış sırasında en geç bu aralıkla yeniden denetle
RECHECK_EVERY_CHUNKS = 64      # … ya da bu kadar parçada bir (64 × 64 KB = 4 MB)
CHUNK_BYTES = 64 * 1024
ABORT_MARKER = (b"\n#DISA_AKTARIM_DURDURULDU: erisim iptal edildi veya disa aktarimlar donduruldu; "
                b"bu dosya EKSIKTIR ve kullanilmamalidir.\n")

_REASON_HTTP = {
    "user_deactivated": (401, "Hesabınız devre dışı bırakıldığı için dışa aktarım iptal edildi."),
    "staff_role_removed": (403, "Personel yetkiniz kaldırıldığı için dışa aktarım iptal edildi."),
    "staff_role_changed": (403, "Rolünüz değiştirildiği için dışa aktarım iptal edildi; yeniden deneyin."),
    "token_revoked": (401, "Oturumunuz sonlandırıldığı için dışa aktarım iptal edildi. Tekrar giriş yapın."),
    "sessions_revoked": (401, "Tüm oturumlar güvenlik nedeniyle sonlandırıldı; dışa aktarım iptal edildi."),
    "exports_frozen": (423, None),   # mesaj: security_switches.switch_message("freeze_exports")
    "status_unverifiable": (503, "Personel durumu doğrulanamadığı için dışa aktarım güvenlik gereği "
                                 "durduruldu. Lütfen tekrar deneyin."),
}


def register_export() -> None:
    """freeze_exports kapısı geçildiğinde çağrılır: bu istek bir toplu dışa aktarımdır."""
    holder = EXPORT_CTX.get()
    if holder is None:      # middleware dışı çağrı (ör. doğrudan Python çağrısı) → izlenecek akış yok
        return
    from routes.deps import CURRENT_STAFF, CURRENT_STAFF_TOKEN
    u = CURRENT_STAFF.get() or {}
    t = CURRENT_STAFF_TOKEN.get() or {}
    holder["export"] = {
        "user_id": u.get("id"), "email": u.get("email"),
        "role_id": u.get("role_id"), "super": u.get("is_super_admin") is True,
        "tv": t.get("tv"), "ep": t.get("ep"), "iat": t.get("iat"),
    }


async def export_block_reason(ex: dict) -> Optional[str]:
    """Dışa aktarım sürebilir mi? None = evet; aksi halde neden kodu. Aralık başına tek DB okuması."""
    from routes.deps import db, get_security_epoch, _epoch_revoked
    uid = (ex or {}).get("user_id")
    if uid:
        try:
            u = await db.users.find_one(
                {"id": uid}, {"_id": 0, "is_active": 1, "is_admin": 1, "is_super_admin": 1, "role_id": 1,
                              "role": 1, "token_version": 1})
        except Exception as e:  # noqa: BLE001 — fail-closed
            logger.warning("[export_guard] personel durumu okunamadı: %s", type(e).__name__)
            return "status_unverifiable"
        if not u or u.get("is_active") is False:
            return "user_deactivated"
        is_super = u.get("is_super_admin") is True
        if u.get("is_admin") is not True or u.get("role") == "customer" or \
                not (is_super or u.get("role_id") not in (None, "")):
            return "staff_role_removed"
        if bool(ex.get("super")) and not is_super:
            return "staff_role_changed"
        if not ex.get("super") and str(u.get("role_id") or "") != str(ex.get("role_id") or ""):
            return "staff_role_changed"
        try:
            if int(u.get("token_version") or 0) > int(ex.get("tv") or 0):
                return "token_revoked"
        except (TypeError, ValueError):
            return "token_revoked"
        try:
            ep, bumped = await get_security_epoch()
        except Exception:  # noqa: BLE001
            return "status_unverifiable"
        if _epoch_revoked({"ep": ex.get("ep"), "iat": ex.get("iat")}, ep, bumped):
            return "sessions_revoked"
    try:
        from security_switches import switch_on
        if await switch_on("freeze_exports"):
            return "exports_frozen"
    except Exception:  # noqa: BLE001
        return "status_unverifiable"
    return None


def _deny_payload(reason: str) -> tuple:
    status, msg = _REASON_HTTP.get(reason, (403, "Dışa aktarım güvenlik nedeniyle durduruldu."))
    if msg is None:
        try:
            from security_switches import switch_message
            msg = switch_message("freeze_exports")
        except Exception:  # pragma: no cover
            msg = "Toplu veri dışa aktarımı güvenlik nedeniyle geçici olarak kapatıldı."
    return status, msg


async def _record(ex: dict, scope: dict, reason: str, phase: str, bytes_sent: int) -> None:
    """Güvenlik olayı: denetim zinciri (security.export_aborted_access_revoked) + kritik uyarı."""
    try:
        from security_switches import emit_security_event
        await emit_security_event(
            "export_aborted_access_revoked",
            {"id": ex.get("user_id"), "email": ex.get("email")},
            {"path": str(scope.get("path") or "")[:160], "reason": reason, "phase": phase,
             "bytes_sent": int(bytes_sent), "fp": f"{reason}:{scope.get('path')}"})
    except Exception as e:  # noqa: BLE001
        logger.warning("[export_guard] güvenlik olayı yazılamadı: %s", type(e).__name__)
    logger.warning("[export_guard] dışa aktarım durduruldu path=%s reason=%s phase=%s bytes=%s",
                   scope.get("path"), reason, phase, bytes_sent)


_DROP_ON_DENY = {b"content-type", b"content-length", b"content-disposition", b"content-encoding"}


class ExportGuardMiddleware:
    """Saf ASGI ara katmanı — yalnız register_export() ile işaretlenmiş yanıtları denetler."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        holder: dict = {}
        token = EXPORT_CTX.set(holder)
        st = {"aborted": False, "last": 0.0, "since": 0, "bytes": 0}

        async def _check(phase: str) -> Optional[str]:
            ex = holder.get("export") or {}
            reason = await export_block_reason(ex)
            st["last"] = time.monotonic()
            st["since"] = 0
            if reason:
                st["aborted"] = True
                await _record(ex, scope, reason, phase, st["bytes"])
            return reason

        async def _send(message):
            if holder.get("export") is None:
                await send(message)
                return
            if st["aborted"]:
                return      # akış kesildi: uygulamanın kalan baytları YUTULUR (istemciye gitmez)
            mtype = message.get("type")
            if mtype == "http.response.start":
                reason = await _check("before_send")
                if reason:
                    import json as _json
                    status, msg = _deny_payload(reason)
                    body = _json.dumps({"detail": msg, "code": f"export_aborted:{reason}"},
                                       ensure_ascii=False).encode("utf-8")
                    hdrs = [(k, v) for (k, v) in (message.get("headers") or [])
                            if k.lower() not in _DROP_ON_DENY]
                    hdrs += [(b"content-type", b"application/json; charset=utf-8"),
                             (b"content-length", str(len(body)).encode())]
                    await send({"type": "http.response.start", "status": status, "headers": hdrs})
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    return
                # Akış kesilebilsin (hata işaret satırı eklenebilsin) diye Content-Length kaldırılır →
                # parçalı aktarım; istemci kesilen dosyayı "tamamlandı" sanmaz.
                message = dict(message)
                message["headers"] = [(k, v) for (k, v) in (message.get("headers") or [])
                                      if k.lower() != b"content-length"]
                await send(message)
                return
            if mtype == "http.response.body":
                body = message.get("body") or b""
                more = bool(message.get("more_body"))
                if not body:
                    await send(message)
                    return
                n = len(body)
                step = max(1, int(CHUNK_BYTES))
                for i in range(0, n, step):
                    if st["bytes"] > 0 and (
                            (time.monotonic() - st["last"]) >= RECHECK_INTERVAL_SEC
                            or st["since"] >= RECHECK_EVERY_CHUNKS):
                        if await _check("mid_stream"):
                            await send({"type": "http.response.body", "body": ABORT_MARKER, "more_body": False})
                            return
                    piece = body[i:i + step]
                    last_piece = (i + step) >= n
                    await send({"type": "http.response.body", "body": piece,
                                "more_body": more or not last_piece})
                    st["bytes"] += len(piece)
                    st["since"] += 1
                return
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            try:
                EXPORT_CTX.reset(token)
            except Exception:  # pragma: no cover — farklı bağlam (nadir); değer zaten istek-yerel
                pass
