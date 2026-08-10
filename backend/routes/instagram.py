"""
=============================================================================
instagram.py — @facette Instagram akışı (etiketli/gönderi feed'i)
=============================================================================
Instagram Graph API token'ı ile @facette hesabının son gönderilerini VEYA
hesabın etiketlendiği gönderileri (tagged) çeker, db.instagram_posts'a yazar;
anasayfadaki InstaShop bileşeni bunları gösterir.

Token yoksa admin gönderileri ELLE de ekleyebilir (görsel URL + gönderi linki).

ENDPOINTS:
  GET  /api/instagram/feed                 — Public (InstaShop için)
  GET  /api/admin/instagram/settings       — Admin (durum)
  PUT  /api/admin/instagram/settings       — Admin (token/ig_user_id/source/auto_sync)
  POST /api/admin/instagram/sync           — Admin (Graph API'den çek)
  POST /api/admin/instagram/posts          — Admin (elle gönderi ekle)
  DELETE /api/admin/instagram/posts/{id}   — Admin (gönderi sil)
=============================================================================
"""
import logging
import os
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone

import httpx

from .deps import db, require_admin, generate_id

try:
    from security.crypto import encrypt, decrypt
except Exception:  # pragma: no cover — kripto yoksa düz metin
    def encrypt(x): return x
    def decrypt(x): return x

logger = logging.getLogger("instagram")

public_router = APIRouter(prefix="/instagram", tags=["instagram-public"])
admin_router = APIRouter(prefix="/admin/instagram", tags=["instagram-admin"])

_GRAPH = "https://graph.facebook.com/v23.0"
# OAuth (tek tık bağlantı) — kullanıcı token'ı Graph Explorer'dan almak zorunda kalmaz:
# panel "Facebook ile Bağlan" der → Facebook onay ekranı → bu callback'e döner →
# code'u token'a çeviren, IG hesabını bulan ve senkronu başlatan sunucudur.
_API_PUBLIC_BASE = (os.environ.get("PUBLIC_API_BASE") or "https://api.facette.com.tr/api").rstrip("/")
_OAUTH_REDIRECT_URI = f"{_API_PUBLIC_BASE}/instagram/oauth/callback"
_ADMIN_RETURN_URL = (os.environ.get("SITE_URL") or "https://www.facette.com.tr").rstrip("/") + "/admin/instagram"
_OAUTH_SCOPE = "instagram_basic,pages_show_list"


def _now():
    return datetime.now(timezone.utc)


async def _get_settings():
    return await db.settings.find_one({"id": "instagram"}, {"_id": 0}) or {"id": "instagram"}


def _media_image(item: dict) -> str:
    """Video ise thumbnail, değilse media_url."""
    if (item.get("media_type") or "").upper() == "VIDEO":
        return item.get("thumbnail_url") or item.get("media_url") or ""
    return item.get("media_url") or item.get("thumbnail_url") or ""


async def _fetch_from_graph(token: str, ig_user_id: str, source: str, limit: int) -> list:
    """Graph API'den media (kendi gönderiler) veya tags (etiketli) çeker.
    SAYFALAMA: paging.next takip edilerek limit'e kadar birden çok sayfa toplanır (>30)."""
    edge = "tags" if source == "tags" else "media"
    fields = "id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,username"
    url = f"{_GRAPH}/{ig_user_id}/{edge}"
    params = {"fields": fields, "limit": 50, "access_token": token}
    out = []
    async with httpx.AsyncClient(timeout=25) as client:
        guard = 0
        while url and len(out) < limit and guard < 20:
            guard += 1
            r = await client.get(url, params=params)
            if r.status_code != 200:
                detail = ""
                try:
                    detail = (r.json().get("error") or {}).get("message") or r.text
                except Exception:
                    detail = r.text
                # İlk sayfada hata → yükselt; sonraki sayfalarda hata → eldekiyle dön.
                if not out:
                    raise HTTPException(status_code=502, detail=f"Instagram API hatası ({r.status_code}): {detail}")
                break
            j = r.json() or {}
            out.extend(j.get("data", []) or [])
            url = ((j.get("paging") or {}).get("next")) or ""
            params = None  # 'next' URL tüm parametreleri zaten içerir
    return out[:limit]


def _sanitize_products(raw) -> list:
    """InstaShop: gönderiye bağlı ürün kartlarını normalize et (id, title, image, price, url)."""
    out = []
    for p in (raw or [])[:12]:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or p.get("product_id") or "").strip()
        url = str(p.get("url") or p.get("slug") or "").strip()
        if not (pid or url):
            continue
        out.append({
            "id": pid,
            "title": str(p.get("title") or p.get("name") or "")[:160],
            "image": str(p.get("image") or "")[:500],
            "price": p.get("price"),
            "old_price": p.get("old_price"),
            "url": url,
        })
    return out


async def _mirror_image(post_id: str, ig_url: str) -> str:
    """Instagram görselini kendi depomuza (R2/CDN) kopyalar ve KALICI adresi döndürür.

    NEDEN: Instagram CDN adresleri imzalı + süreli (oe= ~2 gün). Saklanan adres bayatlayınca
    403 döner ve vitrindeki akış boş kutulara düşer. Kopya bizde olunca akış, senkron dursa
    bile çalışmaya devam eder.

    Idempotent: aynı gönderi daha önce aynalandıysa tekrar indirilmez.
    Güvenli: R2 kapalıysa veya indirme/yükleme başarısızsa ORİJİNAL adres döndürülür
    (regresyon yok — en kötü ihtimalle bugünkü davranış korunur).
    """
    try:
        from services import r2_storage
        if not r2_storage.is_enabled():
            return ig_url
        key = f"instagram/{post_id}.jpg"
        # Zaten aynalanmışsa (kayıtlı adres bizim CDN'imizi gösteriyorsa) tekrar indirme.
        prev = await db.instagram_posts.find_one({"id": post_id}, {"_id": 0, "image": 1, "image_mirrored": 1})
        if prev and prev.get("image_mirrored") and str(prev.get("image") or "").endswith(key):
            return prev["image"]
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
            r = await c.get(ig_url)
        if r.status_code != 200 or not r.content:
            logger.warning("[instagram] görsel indirilemedi (%s) post=%s", r.status_code, post_id)
            return ig_url
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
        url = await run_in_threadpool(r2_storage.put_object, key, r.content, ctype)
        await db.instagram_posts.update_one({"id": post_id}, {"$set": {"image_mirrored": True}})
        return url or ig_url
    except Exception as e:
        logger.warning("[instagram] görsel aynalanamadı post=%s: %s", post_id, e)
        return ig_url


async def _do_sync(token: str, ig_user_id: str, max_each: int = 100) -> tuple:
    """Kendi gönderiler (media) + etiketli (tags) çekilir; kind ile ayrı ayrı upsert edilir.
    active/products KORUNUR (yalnız ilk eklemede default atanır) → admin seçimleri bozulmaz.
    media default GÖSTER (active), tagged default GİZLİ (admin seçer)."""
    per = {}
    total = 0
    tags_error = ""
    for kind, src in (("media", "media"), ("tagged", "tags")):
        try:
            items = await _fetch_from_graph(token, ig_user_id, src, max_each)
        except HTTPException as e:
            if kind == "media":
                raise            # kendi gönderiler çekilemiyorsa gerçek hata
            per[kind] = 0        # tags edge izin/erişim isteyebilir → media başarılıysa yut ama SEBEBİ sakla
            tags_error = str(getattr(e, "detail", e))[:300]
            continue
        c = 0
        for it in items:
            img = _media_image(it)
            if not img:
                continue
            _pid = it.get("id") or generate_id()
            # DAYANIKLILIK: Instagram'ın CDN adresleri İMZALI ve SÜRELİDİR (~2 gün, oe=
            # parametresi). Bu adresi olduğu gibi saklarsak, senkron herhangi bir sebeple
            # dursa (token süresi dolar, API değişir) görseller 2 gün içinde 403 verip
            # anasayfadaki "Get The Look" bölümü boş kutulara döner — nitekim döndü.
            # Çözüm: görseli BİR KEZ kendi depomuza (R2/CDN) kopyala ve kalıcı adresi sakla.
            # Idempotent: daha önce aynayan gönderi tekrar indirilmez.
            img = await _mirror_image(_pid, img)
            base = {
                "id": _pid,
                "image": img,
                "permalink": it.get("permalink") or "",
                "caption": (it.get("caption") or "")[:500],
                "media_type": it.get("media_type") or "IMAGE",
                "username": it.get("username") or "",
                "timestamp": it.get("timestamp") or _now().isoformat(),
                "kind": kind, "source": src,
                "synced_at": _now().isoformat(),
            }
            await db.instagram_posts.update_one(
                {"id": base["id"]},
                {"$set": base,
                 "$setOnInsert": {"product_link": "", "products": [], "active": (kind == "media")}},
                upsert=True)
            c += 1
        per[kind] = c
        total += c
    return total, per, tags_error


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
@public_router.get("/feed")
async def instagram_feed(limit: int = 12):
    """Anasayfa InstaShop için: kayıtlı gönderiler (en yeni önce)."""
    limit = max(1, min(limit, 60))
    rows = await db.instagram_posts.find(
        {"active": {"$ne": False}},
        {"_id": 0, "id": 1, "image": 1, "permalink": 1, "caption": 1,
         "product_link": 1, "products": 1, "kind": 1, "timestamp": 1},
    ).sort("timestamp", -1).to_list(length=limit)
    return {"posts": rows}


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@admin_router.get("/settings")
async def get_settings(current_user: dict = Depends(require_admin)):
    s = await _get_settings()
    count = await db.instagram_posts.count_documents({})
    return {
        "connected": bool(s.get("access_token")),
        "token_set": bool(s.get("access_token")),
        "app_id": s.get("app_id", ""),
        "app_secret_set": bool(s.get("app_secret")),
        "oauth_redirect_uri": _OAUTH_REDIRECT_URI,
        "ig_user_id": s.get("ig_user_id", ""),
        "source": s.get("source", "media"),
        "auto_sync": bool(s.get("auto_sync", False)),
        "last_sync": s.get("last_sync"),
        "last_error": s.get("last_error", ""),
        "tags_note": s.get("tags_note", ""),
        "post_count": count,
    }


@admin_router.put("/settings")
async def update_settings(payload: dict, current_user: dict = Depends(require_admin)):
    upd = {"updated_at": _now().isoformat()}
    # Token yalnızca doluysa güncellenir (boş gönderim mevcut token'ı silmez).
    tok = (payload or {}).get("access_token")
    if isinstance(tok, str) and tok.strip():
        tok = tok.strip()
        # SINIRSIZ: app_id (sayısal) + app_secret kayıtlıysa, yapıştırılan KISA token'ı 60 günlük
        # uzun ömürlüye çevir + damga bas → scheduler 45 günde bir otomatik yeniler.
        try:
            _s0 = await _get_settings()
            _aid = str(_s0.get("app_id") or "").strip()
            _asec = decrypt(_s0.get("app_secret")) if _s0.get("app_secret") else ""
            if _aid.isdigit() and _asec:
                async with httpx.AsyncClient(timeout=30) as _c:
                    _r = await _c.get(f"{_GRAPH}/oauth/access_token", params={
                        "grant_type": "fb_exchange_token", "client_id": _aid,
                        "client_secret": _asec, "fb_exchange_token": tok})
                if _r.status_code == 200 and (_r.json() or {}).get("access_token"):
                    tok = _r.json()["access_token"]
                    upd["token_obtained_at"] = _now().isoformat()
        except Exception:
            pass  # çevrilemezse kısa token'la devam (sync yine çalışır, sadece süresi kısa olur)
        upd["access_token"] = encrypt(tok)
    if "ig_user_id" in (payload or {}):
        upd["ig_user_id"] = str(payload.get("ig_user_id") or "").strip()
    if "source" in (payload or {}):
        upd["source"] = "tags" if payload.get("source") == "tags" else "media"
    if "auto_sync" in (payload or {}):
        upd["auto_sync"] = bool(payload.get("auto_sync"))
    await db.settings.update_one(
        {"id": "instagram"}, {"$set": upd, "$setOnInsert": {"id": "instagram"}}, upsert=True)
    return {"success": True}


@admin_router.post("/auto-setup")
async def auto_setup(payload: dict, current_user: dict = Depends(require_admin)):
    """OTOMATİK KURULUM: Graph Explorer'dan alınan KISA ömürlü token + App ID/Secret ile
    her şeyi backend halleder — uzun ömürlü token'a çevirir, bağlı sayfayı ve Instagram
    Business hesabını bulur, ayarları kaydeder, ilk senkronu çalıştırır.
    (Meta alan adları yalnız sunucudan erişilebilir olduğundan bu iş burada yapılır.)"""
    app_id = str((payload or {}).get("app_id") or "").strip()
    app_secret = str((payload or {}).get("app_secret") or "").strip()
    short_token = str((payload or {}).get("short_token") or "").strip()
    ig_user_id = str((payload or {}).get("ig_user_id") or "").strip()
    if not (app_id and app_secret and short_token):
        raise HTTPException(status_code=400, detail="App ID, App Secret ve kısa token zorunlu")
    return await _finalize_connect(app_id, app_secret, short_token, ig_user_id)


async def _finalize_connect(app_id: str, app_secret: str, user_token: str, ig_user_id: str = "") -> dict:
    """Ortak son adım (auto-setup + OAuth callback): kullanıcı token'ını uzun ömürlüye
    çevirir, bağlı Instagram Business hesabını bulur, şifreli kaydeder, ilk senkronu koşar."""
    async with httpx.AsyncClient(timeout=30) as client:
        # 1) Kısa token → uzun ömürlü (60 gün) kullanıcı token'ı
        r = await client.get(f"{_GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": user_token})
        if r.status_code != 200:
            _err = ((r.json() or {}).get("error") or {}).get("message", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            raise HTTPException(status_code=502, detail=f"Token uzatılamadı: {_err}")
        long_token = (r.json() or {}).get("access_token") or ""
        if not long_token:
            raise HTTPException(status_code=502, detail="Uzun ömürlü token alınamadı")

        # 2) Bağlı sayfalar → instagram_business_account
        r2 = await client.get(f"{_GRAPH}/me/accounts", params={
            "fields": "id,name,instagram_business_account{id,username}",
            "access_token": long_token})
        if r2.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Sayfalar okunamadı: {r2.text[:300]}")
        pages = (r2.json() or {}).get("data", []) or []
        ig = None
        for p in pages:
            iba = p.get("instagram_business_account")
            if iba and iba.get("id"):
                ig = {"id": iba["id"], "username": iba.get("username", ""), "page": p.get("name", "")}
                break
        # DOĞRUDAN IG ID YOLU: sayfa /me/accounts'ta görünmüyorsa (İşletme Yöneticisi / "Yeni Sayfa
        # Deneyimi"nde klasik rol dönmez) ama elde IG User ID varsa, doğrudan o hesabı doğrula ve kullan.
        # Feed zaten /{ig_user_id}/media ile çekiliyor; sayfaya ihtiyaç yok.
        if not ig and ig_user_id:
            rc = await client.get(f"{_GRAPH}/{ig_user_id}", params={
                "fields": "username,name", "access_token": long_token})
            if rc.status_code == 200 and (rc.json() or {}).get("id"):
                _j = rc.json() or {}
                ig = {"id": str(_j.get("id") or ig_user_id), "username": _j.get("username", ""),
                      "page": "(doğrudan IG)"}
            else:
                _e = ((rc.json() or {}).get("error") or {}).get("message", rc.text[:200]) if rc.headers.get("content-type", "").startswith("application/json") else rc.text[:200]
                raise HTTPException(status_code=400, detail=(
                    f"Verilen Instagram User ID ({ig_user_id}) bu token ile okunamadı: {_e} "
                    "ID'nin doğru ve token'da instagram_basic izninin olduğundan emin olun."))

        if not ig:
            # TEŞHİS: kaç sayfa görüldü + adları → "sayfa gelmiyor" mu yoksa "sayfa var IG boş" mu ayırt et.
            if not pages:
                _diag = ("Hiç Facebook Sayfası görülmedi → onay ekranında sayfa SEÇİLMEDİ veya "
                         "'pages_show_list' izni verilmedi. Tekrar bağlanıp sayfayı işaretleyin ve tüm izinleri onaylayın.")
            else:
                _names = ", ".join((p.get("name") or "?") for p in pages[:6])
                _diag = (f"{len(pages)} sayfa görüldü ({_names}) ama HİÇBİRİNDE bağlı Instagram İşletme hesabı yok. "
                         "Yani Instagram bu sayfaya Graph API'nin gördüğü 'profesyonel bağ' ile bağlı değil. "
                         "ÇÖZÜM: Meta Business Suite → Ayarlar → Instagram hesapları → Instagram'ı BU sayfaya bağlayın "
                         "(veya Sayfa → Ayarlar → Bağlı hesaplar → Instagram → Bağlan). Sadece Instagram uygulamasındaki "
                         "Accounts Center bağı YETMEZ; sayfa tarafından profesyonel bağlantı gerekir.")
            raise HTTPException(status_code=400, detail="Bağlı Instagram Business hesabı bulunamadı. " + _diag)

    # 3) Kaydet (token şifreli) + otomatik senkron aç
    await db.settings.update_one(
        {"id": "instagram"},
        {"$set": {"access_token": encrypt(long_token), "ig_user_id": ig["id"],
                  "auto_sync": True, "source": "media",
                  "app_id": app_id, "app_secret": encrypt(app_secret),
                  # SINIRSIZ token için: obtained_at damgası — scheduler yaklaşınca otomatik yeniler.
                  "token_obtained_at": _now().isoformat(),
                  "last_error": "", "updated_at": _now().isoformat()},
         "$setOnInsert": {"id": "instagram"}}, upsert=True)

    # 4) İlk senkron (hata olursa kurulum yine başarılı sayılır — mesajda belirtilir)
    synced, sync_err = 0, ""
    try:
        items = await _fetch_from_graph(long_token, ig["id"], "media", 30)
        for it in items:
            img = _media_image(it)
            if not img:
                continue
            await db.instagram_posts.update_one(
                {"ig_id": it.get("id")},
                {"$set": {"ig_id": it.get("id"), "image": img,
                          "permalink": it.get("permalink", ""),
                          "caption": (it.get("caption") or "")[:300],
                          "timestamp": it.get("timestamp", ""), "active": True,
                          "source": "graph", "updated_at": _now().isoformat()},
                 "$setOnInsert": {"id": generate_id()}}, upsert=True)
            synced += 1
        await db.settings.update_one({"id": "instagram"}, {"$set": {"last_sync": _now().isoformat()}})
    except HTTPException as e:
        sync_err = str(e.detail)
    except Exception as e:
        sync_err = str(e)[:200]

    return {"success": True, "ig_username": ig.get("username"), "ig_user_id": ig["id"],
            "page": ig.get("page"), "synced": synced,
            "message": f"@{ig.get('username') or 'hesap'} bağlandı · {synced} gönderi çekildi"
                       + (f" · ilk senkron uyarısı: {sync_err}" if sync_err else "")}


@admin_router.post("/oauth-start")
async def oauth_start(payload: dict, current_user: dict = Depends(require_admin)):
    """TEK TIK BAĞLANTI (adım 1): App ID+Secret'ı kaydeder, Facebook onay ekranı URL'ini döner.
    Panel bu URL'e yönlendirir; kullanıcı KENDİ tarayıcısında Facebook'a girip onay verir —
    şifre/token hiçbir zaman panele girilmez. Dönüş /instagram/oauth/callback'e olur."""
    s = await _get_settings()
    app_id = str((payload or {}).get("app_id") or "").strip() or str(s.get("app_id") or "").strip()
    app_secret = str((payload or {}).get("app_secret") or "").strip()
    if not app_secret and s.get("app_secret"):
        app_secret = decrypt(s.get("app_secret"))
    if not (app_id and app_secret):
        raise HTTPException(status_code=400, detail="App ID ve App Secret gerekli (bir kez girilir, sonra kayıtlıdır)")

    state = generate_id()
    await db.settings.update_one(
        {"id": "instagram"},
        {"$set": {"app_id": app_id, "app_secret": encrypt(app_secret),
                  "oauth_state": state, "oauth_state_at": _now().isoformat(),
                  "updated_at": _now().isoformat()},
         "$setOnInsert": {"id": "instagram"}}, upsert=True)

    auth_url = "https://www.facebook.com/v19.0/dialog/oauth?" + urlencode({
        "client_id": app_id,
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "state": state,
        "scope": _OAUTH_SCOPE,
        "response_type": "code",
    })
    return {"auth_url": auth_url, "redirect_uri": _OAUTH_REDIRECT_URI}


@public_router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """TEK TIK BAĞLANTI (adım 2): Facebook onayından dönen 'code'u sunucu token'a çevirir,
    IG hesabını bulur, kaydeder, senkronlar; tarayıcıyı panele geri yollar.
    Kimlik güvencesi 'state' parametresidir (oauth-start'ta admin oturumunda üretildi)."""
    def _back(params: dict):
        return RedirectResponse(url=f"{_ADMIN_RETURN_URL}?{urlencode(params)}", status_code=302)

    if error:
        return _back({"ig_error": (error_description or error)[:180]})
    s = await _get_settings()
    saved_state = s.get("oauth_state") or ""
    ok_state = bool(state and saved_state and state == saved_state)
    if ok_state:
        try:  # state en fazla 30 dk geçerli
            _age = (_now() - datetime.fromisoformat(s.get("oauth_state_at"))).total_seconds()
            ok_state = _age < 1800
        except Exception:
            ok_state = False
    if not ok_state or not code:
        return _back({"ig_error": "Oturum doğrulanamadı (state) — panelden tekrar 'Facebook ile Bağlan' deneyin"})
    # state tek kullanımlık
    await db.settings.update_one({"id": "instagram"}, {"$unset": {"oauth_state": "", "oauth_state_at": ""}})

    app_id = str(s.get("app_id") or "").strip()
    app_secret = decrypt(s.get("app_secret")) if s.get("app_secret") else ""
    if not (app_id and app_secret):
        return _back({"ig_error": "App bilgileri bulunamadı — App ID/Secret girip tekrar deneyin"})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{_GRAPH}/oauth/access_token", params={
                "client_id": app_id, "client_secret": app_secret,
                "redirect_uri": _OAUTH_REDIRECT_URI, "code": code})
        if r.status_code != 200:
            try:
                _err = ((r.json() or {}).get("error") or {}).get("message") or r.text
            except Exception:
                _err = r.text
            return _back({"ig_error": f"Token alınamadı: {str(_err)[:160]}"})
        user_token = (r.json() or {}).get("access_token") or ""
        if not user_token:
            return _back({"ig_error": "Facebook token dönmedi"})
        result = await _finalize_connect(app_id, app_secret, user_token)
        return _back({"ig_connected": "1", "ig_user": result.get("ig_username") or "",
                      "ig_synced": str(result.get("synced") or 0)})
    except HTTPException as e:
        return _back({"ig_error": str(e.detail)[:180]})
    except Exception as e:  # pragma: no cover
        logger.exception("instagram oauth callback")
        return _back({"ig_error": f"Beklenmeyen hata: {str(e)[:140]}"})


@admin_router.post("/disconnect")
async def disconnect(current_user: dict = Depends(require_admin)):
    """Token'ı temizle (bağlantıyı kes). Gönderiler silinmez."""
    await db.settings.update_one(
        {"id": "instagram"}, {"$unset": {"access_token": ""}, "$set": {"auto_sync": False}})
    return {"success": True}


@admin_router.post("/sync")
async def sync_now(current_user: dict = Depends(require_admin)):
    s = await _get_settings()
    token = decrypt(s.get("access_token")) if s.get("access_token") else ""
    ig_user_id = s.get("ig_user_id", "")
    source = s.get("source", "media")
    if not token or not ig_user_id:
        raise HTTPException(status_code=400, detail="Önce Access Token ve Instagram User ID girin.")
    try:
        total, per, tags_error = await _do_sync(token, ig_user_id, 100)
    except HTTPException as e:
        await db.settings.update_one({"id": "instagram"}, {"$set": {"last_error": str(e.detail)}})
        raise
    await db.settings.update_one(
        {"id": "instagram"},
        {"$set": {"last_sync": _now().isoformat(), "last_error": "", "tags_note": tags_error}})
    return {"success": True, "saved": total,
            "media": per.get("media", 0), "tagged": per.get("tagged", 0),
            "tags_note": tags_error}


@admin_router.get("/posts")
async def list_posts(limit: int = 60, current_user: dict = Depends(require_admin)):
    limit = max(1, min(limit, 200))
    rows = await db.instagram_posts.find({}, {"_id": 0}).sort("timestamp", -1).to_list(length=limit)
    return {"posts": rows}


@admin_router.post("/posts")
async def add_post(payload: dict, current_user: dict = Depends(require_admin)):
    """Elle gönderi ekle (token yokken). image zorunlu."""
    image = (payload or {}).get("image", "").strip()
    if not image:
        raise HTTPException(status_code=400, detail="Görsel URL'si zorunlu.")
    doc = {
        "id": generate_id(),
        "image": image,
        "permalink": (payload.get("permalink") or "").strip(),
        "product_link": (payload.get("product_link") or "").strip(),
        "caption": (payload.get("caption") or "")[:500],
        "media_type": "IMAGE",
        "source": "manual",
        "active": True,
        "timestamp": _now().isoformat(),
        "created_at": _now().isoformat(),
    }
    await db.instagram_posts.insert_one({**doc})
    return {"success": True, "id": doc["id"]}


@admin_router.put("/posts/{post_id}")
async def update_post(post_id: str, payload: dict, current_user: dict = Depends(require_admin)):
    allowed = {"image", "permalink", "product_link", "caption", "active", "products"}
    upd = {k: v for k, v in (payload or {}).items() if k in allowed}
    if "products" in upd:
        upd["products"] = _sanitize_products(upd["products"])
    if not upd:
        raise HTTPException(status_code=400, detail="Güncellenecek alan yok")
    res = await db.instagram_posts.update_one({"id": post_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Gönderi bulunamadı")
    return {"success": True}


@admin_router.delete("/posts/{post_id}")
async def delete_post(post_id: str, current_user: dict = Depends(require_admin)):
    await db.instagram_posts.delete_one({"id": post_id})
    return {"success": True}


# --------------------------------------------------------------------------- #
# Scheduler'ın çağırdığı otomatik senkron (auto_sync açıksa)
# --------------------------------------------------------------------------- #
async def auto_sync_instagram():
    """Scheduler: auto_sync açık + token varsa gönderileri tazeler."""
    try:
        s = await _get_settings()
        if not s.get("auto_sync") or not s.get("access_token") or not s.get("ig_user_id"):
            return
        token = decrypt(s.get("access_token"))
        total, per, _tags_err = await _do_sync(token, s.get("ig_user_id"), 100)
        await db.settings.update_one(
            {"id": "instagram"}, {"$set": {"last_sync": _now().isoformat(), "last_error": "",
                                           "fail_since": None, "alerted_at": None}})
        logger.info("[instagram] auto-sync ok — media=%d tagged=%d", per.get("media", 0), per.get("tagged", 0))
    except Exception as e:
        logger.warning("[instagram] auto-sync hata: %s", e)
        try:
            # SESSİZ ÖLÜM KORUMASI: 28 Tem'de token süresi dolduğunda senkron 9 GÜN boyunca
            # sessizce başarısız oldu; kimse fark etmedi ve vitrindeki akış boş kutulara döndü.
            # Artık ilk hatada zaman damgası tutulur; arıza 3 saati aşarsa admin'e PUSH gider.
            # Tekrar bildirim en fazla GÜNDE BİR (spam yok). Başarılı senkronda sayaç sıfırlanır.
            _now_dt = _now()
            _prev = await db.settings.find_one({"id": "instagram"},
                                               {"_id": 0, "fail_since": 1, "alerted_at": 1}) or {}
            _fail_since = _prev.get("fail_since") or _now_dt.isoformat()
            await db.settings.update_one({"id": "instagram"},
                                         {"$set": {"last_error": str(e), "fail_since": _fail_since}})

            def _age_h(iso):
                try:
                    d = datetime.fromisoformat(str(iso))
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=timezone.utc)
                    return (_now_dt - d).total_seconds() / 3600.0
                except Exception:
                    return 0.0

            _alerted = _prev.get("alerted_at")
            if _age_h(_fail_since) >= 3 and (not _alerted or _age_h(_alerted) >= 24):
                try:
                    from .push import send_push_to_admins
                    await send_push_to_admins(
                        "⚠️ Instagram akışı durdu",
                        "Bağlantı yenilenmeli — anasayfadaki akış güncellenmiyor. "
                        f"Hata: {str(e)[:90]}",
                        {"type": "instagram_sync_failed"},
                    )
                    await db.settings.update_one({"id": "instagram"},
                                                 {"$set": {"alerted_at": _now_dt.isoformat()}})
                except Exception as _pe:
                    logger.warning("[instagram] uyarı push'u gönderilemedi: %s", _pe)
        except Exception:
            pass
