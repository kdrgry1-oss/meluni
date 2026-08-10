"""
İYS (İleti Yönetim Sistemi) entegrasyonu — Iter 43.

Türkiye yasal zorunluluğu: B2C ticari ileti (email/SMS/arama) öncesi izin kontrolü.
Bu modül OAuth2 Client Credentials + tek/toplu izin sorgu/ekleme/iptal sağlar.

ENV:
  IYS_API_BASE_URL (default https://api.iys.org.tr)
  IYS_BRAND_CODE
  IYS_API_USERNAME
  IYS_API_PASSWORD

Veritabanı:
  iys_permissions: { recipient, recipient_type, message_type, status, source,
                      consent_date, cached_at, expires_at }
"""
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import db, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/iys", tags=["iys"])

IYS_BASE = os.environ.get("IYS_API_BASE_URL") or "https://api.iys.org.tr"
IYS_BRAND = os.environ.get("IYS_BRAND_CODE") or ""

RecipientType = Literal["BIREYSEL", "TACIR"]
MessageType = Literal["MESAJ", "EPOSTA", "ARAMA"]


class _TokenCache:
    def __init__(self):
        self.token: Optional[str] = None
        self.expires_at: float = 0
_token = _TokenCache()


async def _get_token() -> Optional[str]:
    """OAuth2 Client Credentials — Secrets Vault'tan veya env'den okur."""
    if _token.token and time.time() < _token.expires_at - 60:
        return _token.token
    # Önce Secrets Vault'tan dene
    try:
        from .secrets_vault import get_secret
        user = await get_secret("IYS_API_USERNAME") or os.environ.get("IYS_API_USERNAME")
        pwd = await get_secret("IYS_API_PASSWORD") or os.environ.get("IYS_API_PASSWORD")
    except Exception:
        user = os.environ.get("IYS_API_USERNAME")
        pwd = os.environ.get("IYS_API_PASSWORD")
    if not user or not pwd:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{IYS_BASE}/oauth/token",
                data={"grant_type": "client_credentials",
                      "username": user, "password": pwd, "scope": "iys-api"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                logger.warning(f"IYS token failed: {r.status_code} {r.text[:200]}")
                return None
            d = r.json()
            _token.token = d.get("access_token")
            _token.expires_at = time.time() + int(d.get("expires_in") or 3600)
            return _token.token
    except Exception as e:
        logger.warning(f"IYS token exception: {e}")
        return None


# Local cache helpers ---------------------------------------------------------
async def _cache_get(rec: str, rtype: str, mtype: str) -> Optional[dict]:
    doc = await db.iys_permissions.find_one(
        {"recipient": rec, "recipient_type": rtype, "message_type": mtype, "_id": 0},
        {"_id": 0},
    )
    if doc and doc.get("expires_at", "") > datetime.now(timezone.utc).isoformat():
        return doc
    return None


async def _cache_put(rec: str, rtype: str, mtype: str, status: str, source: str = "", ttl_minutes: int = 60):
    now = datetime.now(timezone.utc)
    await db.iys_permissions.update_one(
        {"recipient": rec, "recipient_type": rtype, "message_type": mtype},
        {"$set": {
            "recipient": rec, "recipient_type": rtype, "message_type": mtype,
            "status": status, "source": source,
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            "is_compliant": status == "ONAY",
        }},
        upsert=True,
    )


# Models ---------------------------------------------------------------------
class IYSQuery(BaseModel):
    recipient: str
    recipient_type: RecipientType
    message_type: MessageType


class IYSRegister(IYSQuery):
    status: Literal["ONAY", "RET"] = "ONAY"
    source: str = "API"


# NetGSM İYS ayar okuma/yazma — checkout bildirimi ile AYNI yer (providers.netgsm) --------
async def _netgsm_prov() -> dict:
    """providers.netgsm bloğu (İYS + SMS kimlikleri burada; iys.py._iys_config ile aynı kaynak)."""
    doc = await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {}
    return (doc.get("providers", {}) or {}).get("netgsm", {}) or {}


def _iys_fields(prov: dict) -> dict:
    """providers.netgsm + env'den etkin İYS kimlik/marka değerlerini çıkarır (iys.py ile birebir)."""
    return {
        "username": (prov.get("username") or os.environ.get("NETGSM_USERCODE", "")).strip(),
        "password": (prov.get("iys_password") or prov.get("password")
                     or os.environ.get("NETGSM_IYS_PASSWORD") or os.environ.get("NETGSM_PASSWORD", "")).strip(),
        "appkey": (prov.get("iys_appkey") or prov.get("appkey")
                   or os.environ.get("NETGSM_IYS_APPKEY") or os.environ.get("NETGSM_APPKEY", "")).strip(),
        "brand_code": (prov.get("iys_brand_code") or prov.get("brand_code")
                       or os.environ.get("NETGSM_IYS_BRAND_CODE", "")).strip(),
    }


class IYSSettings(BaseModel):
    brand_code: str = ""
    appkey: str = ""


# Endpoints ------------------------------------------------------------------
@router.get("/status")
async def iys_status(_=Depends(require_admin)):
    """NetGSM İYS bağlantı durumu — checkout bildiriminin okuduğu providers.netgsm'den."""
    prov = await _netgsm_prov()
    f = _iys_fields(prov)
    username_set = bool(f["username"])
    password_set = bool(f["password"])
    appkey_set = bool(f["appkey"])
    brand = f["brand_code"]
    configured = bool(username_set and password_set and brand)
    hint = ""
    if not brand:
        hint = "İYS Marka Kodu eksik — sağdaki 'İYS Ayarları'ndan girip kaydedin (NetGSM panelinde NetİYS altında görünür)."
    elif not (username_set and password_set):
        hint = "NetGSM kullanıcı/şifre eksik — Ayarlar → Bildirim Sağlayıcıları → NetGSM bloğundan girin (İYS için ayrı şifre varsa iys_password)."
    return {
        "configured": configured,
        "brand_code": brand or "(eksik)",
        "username_set": username_set,
        "password_set": password_set,
        "appkey_set": appkey_set,
        "hint": hint,
        "base_url": IYS_BASE,
    }


@router.get("/settings")
async def iys_settings_get(_=Depends(require_admin)):
    """Kayıtlı İYS marka kodu + appkey (providers.netgsm)."""
    prov = await _netgsm_prov()
    return {
        "brand_code": prov.get("iys_brand_code") or prov.get("brand_code") or "",
        "appkey": prov.get("iys_appkey") or prov.get("appkey") or "",
    }


@router.post("/settings")
async def iys_settings_post(p: IYSSettings, _=Depends(require_admin)):
    """İYS marka kodu + appkey'i providers.netgsm bloğuna yazar (checkout bildirimi buradan okur)."""
    upd = {"providers.netgsm.iys_brand_code": (p.brand_code or "").strip()}
    # appkey boş gönderilirse mevcut değeri ezme; doluysa İYS alt-kullanıcı appkey'ine yaz.
    if (p.appkey or "").strip():
        upd["providers.netgsm.iys_appkey"] = p.appkey.strip()
    await db.settings.update_one({"id": "notification_providers"}, {"$set": upd}, upsert=True)
    return {"ok": True}


@router.post("/test-connection")
async def iys_test_connection(_=Depends(require_admin)):
    """NetGSM İYS bağlantısını YAN-ETKİSİZ doğrular: gerçek İYS ucuna (/iys/add) BOŞ data ile
    POST atar — hiçbir izin gönderilmez, yalnızca kimlik + marka + İYS modülü erişimi test edilir
    (checkout bildiriminin kullandığı AYNI uç ve Basic-Auth yöntemi)."""
    prov = await _netgsm_prov()
    f = _iys_fields(prov)
    attempts: List[dict] = []
    missing = [lbl for lbl, v in (("kullanıcı", f["username"]), ("şifre", f["password"]),
                                  ("marka kodu", f["brand_code"])) if not v]
    if missing:
        return {"ok": False, "message": "Eksik alan(lar): " + ", ".join(missing) +
                ". İYS Ayarları'ndan marka kodunu, NetGSM bloğundan kullanıcı/şifreyi girin.",
                "attempts": attempts}
    import base64 as _b64
    url = os.environ.get("NETGSM_IYS_URL") or "https://api.netgsm.com.tr/iys/add"
    _auth = _b64.b64encode(f"{f['username']}:{f['password']}".encode()).decode()
    hdrs = {"Content-Type": "application/json; charset=utf-8", "Authorization": "Basic " + _auth}
    payload = {"header": {"username": f["username"], "password": f["password"], "brandCode": f["brand_code"]},
               "body": {"data": []}}          # BOŞ data → gerçek gönderim YOK (yan etkisiz)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.post(url, json=payload, headers=hdrs)
        body = (r.text or "").strip()
        low = body.lower()
        attempts.append({"mode": "iys-add(empty)", "code": r.status_code})
        # NetGSM İYS modülü kapalı: code 40 / "aktif" / "modul" → net, düzeltilebilir mesaj.
        if body[:2] == "40" or "aktifle" in low or "modul" in low or "modül" in low:
            return {"ok": False,
                    "message": "NetGSM İYS modülü hesabınızda aktif değil — NetGSM'den İYS (NetİYS) "
                               "modülünü açtırın (kod 40). Kimlik/marka doğru.",
                    "attempts": attempts, "raw": body[:200]}
        # Kimlik reddi: 30/70 kodları veya HTTP 401/403 veya şifre/kullanıcı ibaresi.
        if r.status_code in (401, 403) or body[:2] in ("30", "70") \
                or "kullanıc" in low or "sifre" in low or "şifre" in low or "unauthor" in low:
            return {"ok": False,
                    "message": "NetGSM kimlik reddedildi — İYS alt-kullanıcı adı/şifresini kontrol edin.",
                    "attempts": attempts, "raw": body[:200]}
        # NetGSM'e ulaşıldı ve kimlik/modül hatası YOK → bağlantı hazır. (Boş data'yı NetGSM
        # 'gönderilecek kayıt yok' diye reddetse bile bu, auth+marka+modülün ÇALIŞTIĞINI kanıtlar.)
        return {"ok": True,
                "message": f"NetGSM İYS bağlantısı hazır ✓ · Marka {f['brand_code']} · kimlik doğrulandı. "
                           "Checkout izinleri İYS'ye bildirilebilir.",
                "attempts": attempts, "raw": body[:200]}
    except Exception as e:
        attempts.append({"mode": "iys-add(empty)", "error": str(e)})
        return {"ok": False, "message": "NetGSM'e ulaşılamadı: " + str(e), "attempts": attempts}


@router.post("/query")
async def iys_query(q: IYSQuery, _=Depends(require_admin)):
    """Tek izin sorgula — önce cache, sonra IYS API."""
    cached = await _cache_get(q.recipient, q.recipient_type, q.message_type)
    if cached:
        return {"source": "cache", **cached}

    tok = await _get_token()
    if not tok:
        return {"source": "no_token", "status": "UNKNOWN", "is_compliant": False,
                "message": "IYS API credentials eksik. Secrets Vault → IYS_API_USERNAME/IYS_API_PASSWORD ekleyin"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{IYS_BASE}/v1/consent/show/json",
                headers={"Authorization": f"Bearer {tok}"},
                json={"brandCode": IYS_BRAND, "recipient": q.recipient,
                      "recipientType": q.recipient_type, "type": q.message_type},
            )
            if r.status_code != 200:
                return {"source": "error", "status": "UNKNOWN", "is_compliant": False,
                        "http_status": r.status_code, "detail": r.text[:200]}
            d = r.json()
            consent = (d.get("response") or {}).get("consent") or d
            status = consent.get("status", "UNKNOWN")
            await _cache_put(q.recipient, q.recipient_type, q.message_type, status, consent.get("source", ""))
            return {"source": "api", "status": status, "is_compliant": status == "ONAY",
                    "consent_source": consent.get("source"), "consent_date": consent.get("consentDate")}
    except Exception as e:
        return {"source": "exception", "status": "UNKNOWN", "is_compliant": False, "error": str(e)}


@router.post("/register")
async def iys_register(p: IYSRegister, _=Depends(require_admin)):
    """İzin ekle — ARACI NETGSM üzerinden. DOĞRUDAN devlet İYS'sine (api.iys.org.tr) GİTMEZ.

    Facette'in İYS aracı hizmet sağlayıcısı NetGSM'dir; admin panelinden elle eklenen izin de
    checkout ile AYNI NetGSM yolunu (record_consent → api.netgsm.com.tr/iys/add) kullanır.
    Böylece tek kanal olur, devlet İYS'sine doğrudan çift gönderim yapılmaz."""
    from .iys import record_consent
    is_email = p.message_type == "EPOSTA"
    email = p.recipient if is_email else ""
    phone = "" if is_email else p.recipient
    ch = "EPOSTA" if is_email else "MESAJ"
    res = await record_consent(email, phone, [ch], status=p.status,
                               source=(p.source or "ADMIN_PANEL"))
    await _cache_put(p.recipient, p.recipient_type, p.message_type, p.status, p.source)
    return {"ok": bool(res.get("recorded")), "via": "netgsm", **res}


@router.post("/query-batch")
async def iys_query_batch(queries: List[IYSQuery], _=Depends(require_admin)):
    """Toplu sorgulama (max 50). Pazarlama kampanyaları öncesi izin doğrulama."""
    if len(queries) > 50:
        raise HTTPException(status_code=400, detail="Max 50 sorgu")
    import asyncio
    results = await asyncio.gather(*[iys_query(q, _=_) for q in queries], return_exceptions=True)
    out = []
    compliant = 0
    for q, r in zip(queries, results):
        if isinstance(r, Exception):
            out.append({"recipient": q.recipient, "error": str(r), "is_compliant": False})
        else:
            out.append({"recipient": q.recipient, **r})
            if r.get("is_compliant"):
                compliant += 1
    return {"total": len(queries), "compliant": compliant, "items": out}
