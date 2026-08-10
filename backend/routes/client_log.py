"""
İstemci (tarayıcı) yüklenme/hata teleметрisi — sitenin HANGİ cihaz/IP'de açılıp
açılmadığını ve açılmıyorsa NEDENİNİ (JS hatası) tespit etmek için.

Akış: index.html'deki küçük ES5 beacon, sayfa açılınca / hata olunca / uygulama
MOUNT olamayınca buraya POST atar. Sunucu IP + UA damgalar. Admin panelden
(GET /admin/client-load-events) cihaz/IP/hata dökümü görülür.
"""
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends
from fastapi.responses import PlainTextResponse

from .deps import db, logger, require_admin

try:
    from .deps import client_ip_from_request
except Exception:  # pragma: no cover
    client_ip_from_request = None

router = APIRouter(tags=["client-log"])

_ALLOWED_STATUS = {"ok", "mount_fail", "error", "unhandledrejection", "heal_reload", "slow"}


def _ip(request: Request) -> str:
    try:
        if client_ip_from_request:
            return client_ip_from_request(request) or ""
    except Exception:
        pass
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _classify_ua(ua: str) -> dict:
    u = (ua or "").lower()
    if "iphone" in u or "ipad" in u or "ios" in u:
        os_name = "iOS"
    elif "android" in u:
        os_name = "Android"
    elif "windows" in u:
        os_name = "Windows"
    elif "mac os" in u or "macintosh" in u:
        os_name = "macOS"
    elif "linux" in u:
        os_name = "Linux"
    else:
        os_name = "Diğer"
    if "edg/" in u:
        br = "Edge"
    elif "chrome" in u and "chromium" not in u:
        br = "Chrome"
    elif "crios" in u:
        br = "Chrome iOS"
    elif "firefox" in u or "fxios" in u:
        br = "Firefox"
    elif "safari" in u:
        br = "Safari"
    elif "instagram" in u:
        br = "Instagram içi"
    elif "fban" in u or "fbav" in u:
        br = "Facebook içi"
    else:
        br = "Diğer"
    in_app = any(k in u for k in ("instagram", "fban", "fbav", "wv)", "; wv"))
    return {"os": os_name, "browser": br, "in_app": in_app}


@router.post("/client-log")
async def client_log(request: Request):
    """Herkese açık beacon ucu — istemci yüklenme/hata olayı kaydeder (IP+UA sunucudan)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status = str(body.get("status") or "")[:40]
    if status not in _ALLOWED_STATUS:
        status = "error"
    ua = request.headers.get("user-agent", "")[:400]
    doc = {
        "ip": _ip(request),
        "ua": ua,
        **_classify_ua(ua),
        "status": status,
        "url": str(body.get("url") or "")[:500],
        "message": str(body.get("message") or "")[:800],
        "source": str(body.get("source") or "")[:300],
        "line": body.get("line") if isinstance(body.get("line"), (int, float)) else None,
        "col": body.get("col") if isinstance(body.get("col"), (int, float)) else None,
        "heal": bool(body.get("heal")),
        "screen": str(body.get("screen") or "")[:40],
        "conn": str(body.get("conn") or "")[:40],
        "referrer": str(body.get("referrer") or "")[:300],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.client_load_events.insert_one(doc)
    except Exception as e:
        logger.warning(f"client_log insert failed: {e}")
    return {"ok": True}


@router.get("/admin/client-load-events")
async def list_client_load_events(limit: int = 200, status: str = "",
                                  only_fail: bool = False,
                                  current_user: dict = Depends(require_admin)):
    """Admin: son istemci yüklenme/hata olayları + özet. only_fail=başarısızlar."""
    q = {}
    if status:
        q["status"] = status
    elif only_fail:
        q["status"] = {"$in": ["mount_fail", "error", "unhandledrejection"]}
    try:
        rows = await db.client_load_events.find(q, {"_id": 0}).sort("at", -1)\
            .limit(min(int(limit or 200), 1000)).to_list(1000)
    except Exception:
        rows = []
    async def _c(qq):
        try:
            return await db.client_load_events.count_documents(qq)
        except Exception:
            return -1
    summary = {
        "ok": await _c({"status": "ok"}),
        "mount_fail": await _c({"status": "mount_fail"}),
        "error": await _c({"status": "error"}),
        "unhandledrejection": await _c({"status": "unhandledrejection"}),
        "total": await _c({}),
    }
    return {"events": rows, "summary": summary}


@router.get("/client-load-events/peek")
async def peek_client_load_events(key: str = "", limit: int = 120, only_fail: bool = True):
    """Uygulama MOUNT olmasa da (admin panel açılmasa da) olayları görebilmek için token-gated
    okuma. Anahtar = WhatsApp verify_token (ayrı sır tutmadan hızlı teşhis)."""
    try:
        s = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
        vt = ((s.get("providers") or {}).get("whatsapp_meta") or {}).get("verify_token") or ""
    except Exception:
        vt = ""
    if not vt or not hmac.compare_digest(str(key), str(vt)):
        return PlainTextResponse("forbidden", status_code=403)
    q = {}
    if only_fail:
        q["status"] = {"$in": ["mount_fail", "error", "unhandledrejection"]}
    rows = await db.client_load_events.find(q, {"_id": 0}).sort("at", -1)\
        .limit(min(int(limit or 120), 400)).to_list(400)
    # Hata mesajlarını grupla (en sık çıkan hata neyse o kök sebep).
    buckets = {}
    for r in rows:
        msg = (r.get("message") or r.get("status") or "").strip()
        key2 = msg[:160]
        b = buckets.setdefault(key2, {"message": key2, "count": 0, "os": {}, "browser": {}, "sample": None})
        b["count"] += 1
        b["os"][r.get("os", "?")] = b["os"].get(r.get("os", "?"), 0) + 1
        b["browser"][r.get("browser", "?")] = b["browser"].get(r.get("browser", "?"), 0) + 1
        if not b["sample"]:
            b["sample"] = {"ua": r.get("ua", ""), "url": r.get("url", ""), "at": r.get("at"),
                           "source": r.get("source", ""), "line": r.get("line")}
    top = sorted(buckets.values(), key=lambda x: x["count"], reverse=True)[:12]
    async def _c(qq):
        try:
            return await db.client_load_events.count_documents(qq)
        except Exception:
            return -1
    return {
        "summary": {
            "ok": await _c({"status": "ok"}),
            "mount_fail": await _c({"status": "mount_fail"}),
            "error": await _c({"status": "error"}),
            "unhandledrejection": await _c({"status": "unhandledrejection"}),
            "heal_reload": await _c({"status": "heal_reload"}),
            "total": await _c({}),
        },
        "top_errors": top,
        "recent": rows[:40],
    }
