"""
app_version_policy.py — ADM-15: asgari mobil uygulama sürümü politikası.

AMAÇ:
  Eski (güvenlik düzeltmesi almamış) bir native uygulama sürümü yönetim/vitrin API'sini
  kullanmaya devam edemesin. Asgari sürüm kod içinde DEĞİL, İşletme Kuralları'ndan gelir
  (beyaz etiket — her firma kendi değerini girer):
    - app.min_version_admin     → yönetim uygulaması (+ native istemcinin /api/admin/* çağrıları)
    - app.min_version_customer  → müşteri uygulaması
  Boş değer = zorlama yok (varsayılan). Geçersiz yazım da zorlama yapmaz (yanlış bir değer
  personeli kilitlemesin); kaydederken ayrıca biçim doğrulanır (business_rules_api).

KİM ETKİLENİR:
  Yalnız `X-App-Platform: ios|android` başlığı gönderen native istemciler. Web istemcileri (başlıksız)
  ETKİLENMEZ. Native istemci sürüm başlığı (`X-App-Version`) göndermez/bozuk gönderirse
  "en eski sürüm" sayılır (politika açıkken 426 — eski sürüm kapıyı atlayamaz).

NOT (sınır): Başlıklar istemci beyanıdır; politika bir HİJYEN kapısıdır, yetki sınırı değildir.
  Asıl güvenlik kapıları (yetki, MFA, onay, iptal) sunucuda HER istemciye aynen uygulanır.

YANIT: 426 Upgrade Required + {"detail": "Uygulamanın güncellenmesi gerekiyor",
  "code": "app_update_required", "min_version", "platform", "store_url"}.
"""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

PLATFORM_HEADER = "x-app-platform"
VERSION_HEADER = "x-app-version"
TARGET_HEADER = "x-app-target"
NATIVE_PLATFORMS = ("ios", "android")

RULE_ADMIN = "app.min_version_admin"
RULE_CUSTOMER = "app.min_version_customer"
RULE_KEYS = (RULE_ADMIN, RULE_CUSTOMER)

UPDATE_REQUIRED_MSG = "Uygulamanın güncellenmesi gerekiyor"
UPDATE_REQUIRED_CODE = "app_update_required"
UPDATE_REQUIRED_HEADER = "X-App-Update-Required"

# Sürüm denetimi uygulanmayan uçlar: güncelleme ekranının kendisinin ihtiyaç duyduğu sürüm sorgusu
# ve sağlık kontrolü.
_EXEMPT_PREFIXES = ("/api/app/version-check", "/api/health")

_VERSION_RE = re.compile(r"^v?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?$", re.IGNORECASE)


def parse_version(value) -> tuple | None:
    """'1.2.3' → (1, 2, 3, 0). Boş/geçersiz → None. En çok 4 bileşen; 'v' öneki kabul."""
    s = str(value or "").strip()
    if not s or len(s) > 40:
        return None
    m = _VERSION_RE.match(s)
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def is_valid_min_version(value) -> bool:
    """İşletme Kuralı kaydı için: boş (zorlama yok) ya da geçerli bir sürüm."""
    return str(value or "").strip() == "" or parse_version(value) is not None


def policy_rule_key(path: str, target: str) -> str:
    """Yönetim uçları ya da yönetim uygulaması → admin kuralı; diğerleri → müşteri kuralı."""
    if str(path or "").startswith("/api/admin") or str(target or "").strip().lower() == "admin":
        return RULE_ADMIN
    return RULE_CUSTOMER


def is_below_minimum(client_version, minimum) -> bool:
    """Asgari sürüm boş/geçersizse False (zorlama yok). İstemci sürümü yok/bozuksa True."""
    min_t = parse_version(minimum)
    if min_t is None:
        return False
    cur_t = parse_version(client_version)
    return cur_t is None or cur_t < min_t


async def evaluate(db, method: str, path: str, headers) -> dict | None:
    """İstek politikaya takılıyorsa 426 gövdesini, aksi halde None döner."""
    platform = str(headers.get(PLATFORM_HEADER) or "").strip().lower()
    if platform not in NATIVE_PLATFORMS:
        return None  # web istemcisi — etkilenmez
    if str(method or "").upper() == "OPTIONS":
        return None
    path = str(path or "")
    if not path.startswith("/api/") or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return None
    target = str(headers.get(TARGET_HEADER) or "").strip().lower()
    key = policy_rule_key(path, target)
    from business_rules import get_rule
    minimum = str(await get_rule(db, key, "") or "").strip()
    if not is_below_minimum(headers.get(VERSION_HEADER), minimum):
        return None
    store_url = ""
    if key == RULE_CUSTOMER:
        # Müşteri uygulamasının mağaza bağlantısı (Mobil Uygulama ayarları). Yönetim uygulaması
        # personel dağıtım kanalından (TestFlight/MDM) güncellenir — bağlantı verilmez.
        try:
            cfg = await db.settings.find_one({"id": "app_versions"}, {"_id": 0, "versions": 1}) or {}
            url = str(((cfg.get("versions") or {}).get(platform) or {}).get("store_url") or "")
            if url.startswith("https://"):
                store_url = url[:500]
        except Exception:
            store_url = ""
    return {"detail": UPDATE_REQUIRED_MSG, "code": UPDATE_REQUIRED_CODE, "min_version": minimum,
            "platform": platform, "app": "admin" if key == RULE_ADMIN else "customer",
            "store_url": store_url}


class AppVersionPolicyMiddleware(BaseHTTPMiddleware):
    """CORS'un İÇİNDE çalışmalı (server.py'de CORS'tan ÖNCE eklenir) ki 426 yanıtı da CORS
    başlıklarını alsın ve native WebView okuyabilsin."""

    async def dispatch(self, request, call_next):
        res = None
        try:
            if str(request.headers.get(PLATFORM_HEADER) or "").strip().lower() in NATIVE_PLATFORMS:
                from routes.deps import db
                res = await evaluate(db, request.method, request.url.path, request.headers)
        except Exception:
            res = None  # politika okunamadı → kimse kilitlenmez (sunucu yetki kapıları zaten geçerli)
        if res:
            return JSONResponse(status_code=426, content=res,
                                headers={UPDATE_REQUIRED_HEADER: "1", "Cache-Control": "no-store"})
        return await call_next(request)
