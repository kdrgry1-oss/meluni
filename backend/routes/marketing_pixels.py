"""
=============================================================================
marketing_pixels.py — Pazarlama Pixel & Etiket Yönetimi (FAZ 9)
=============================================================================

Admin panelden sadece kod yapıştırıp aktifleştirerek sitenin her sayfasında
otomatik çalışan pixel'ler:
  - Google Analytics 4 (GA4)
  - Meta Pixel (Facebook)
  - Google Ads Conversion
  - TikTok Pixel
  - Yandex Metrica
  - Özel HTML snippet'ler

Endpoint'ler:
  GET    /api/marketing-pixels               → admin tüm listesi
  POST   /api/marketing-pixels               → ekle/güncelle
  DELETE /api/marketing-pixels/{id}          → sil
  GET    /api/marketing-pixels/active-public → PUBLIC (auth yok) — aktif pixel head+body snippet'leri

Güvenlik: active-public sadece AKTİF pixel'lerin KAYITLI snippet'lerini döner.
Admin dışında kimse yeni kod ekleyemez.
=============================================================================
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from .deps import db, require_admin, generate_id

router = APIRouter(prefix="/marketing-pixels", tags=["marketing-pixels"])


PIXEL_PROVIDERS = [
    {"key": "ga4", "name": "Google Analytics 4", "supports_capi": True},
    {"key": "meta", "name": "Meta Pixel (Facebook) + CAPI", "supports_capi": True},
    {"key": "google_ads", "name": "Google Ads (Enhanced Conv)", "supports_capi": True},
    {"key": "tiktok", "name": "TikTok Pixel + Events API", "supports_capi": True},
    {"key": "pinterest", "name": "Pinterest + Conversions API", "supports_capi": True},
    {"key": "snapchat", "name": "Snapchat + Conversions API", "supports_capi": True},
    {"key": "gtm", "name": "Google Tag Manager (container)", "supports_capi": False},
    {"key": "yandex", "name": "Yandex Metrica", "supports_capi": False},
    {"key": "hotjar", "name": "Hotjar", "supports_capi": False},
    {"key": "clarity", "name": "Microsoft Clarity", "supports_capi": False},
    {"key": "custom", "name": "Özel Kod", "supports_capi": False},
]


def _template_snippet(provider: str, tag_id: str) -> dict:
    """Hızlı tag-id tabanlı snippet oluşturur (GA4, Meta gibi)."""
    head = ""
    body = ""
    if provider == "ga4" and tag_id:
        head = f"""
<!-- Google Analytics 4 -->
<script async src="https://www.googletagmanager.com/gtag/js?id={tag_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{tag_id}');
</script>
""".strip()
    elif provider == "meta" and tag_id:
        head = f"""
<!-- Meta Pixel -->
<script>
!function(f,b,e,v,n,t,s)
{{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{tag_id}');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id={tag_id}&ev=PageView&noscript=1"/></noscript>
""".strip()
    elif provider == "google_ads" and tag_id:
        head = f"""
<!-- Google Ads -->
<script async src="https://www.googletagmanager.com/gtag/js?id={tag_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{tag_id}');
</script>
""".strip()
    elif provider == "tiktok" and tag_id:
        head = f"""
<!-- TikTok Pixel -->
<script>
!function (w, d, t) {{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}},ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};
  ttq.load('{tag_id}');
  ttq.page();
}}(window, document, 'ttq');
</script>
""".strip()
    elif provider == "pinterest" and tag_id:
        # tag_id = Pinterest TAG ID for browser pixel (PT-XXXX)
        head = f"""
<!-- Pinterest Tag -->
<script>
!function(e){{if(!window.pintrk){{window.pintrk = function () {{
window.pintrk.queue.push(Array.prototype.slice.call(arguments))}};var
n=window.pintrk;n.queue=[],n.version="3.0";var
t=document.createElement("script");t.async=!0,t.src=e;var
r=document.getElementsByTagName("script")[0];
r.parentNode.insertBefore(t,r)}}}}("https://s.pinimg.com/ct/core.js");
pintrk('load', '{tag_id}');
pintrk('page');
</script>
<noscript><img height="1" width="1" style="display:none;" alt="" src="https://ct.pinterest.com/v3/?event=init&tid={tag_id}&noscript=1" /></noscript>
""".strip()
    elif provider == "snapchat" and tag_id:
        head = f"""
<!-- Snap Pixel Code -->
<script type='text/javascript'>
(function(e,t,n){{if(e.snaptr)return;var a=e.snaptr=function(){{
a.handleRequest?a.handleRequest.apply(a,arguments):a.queue.push(arguments)}};
a.queue=[];var s='script';r=t.createElement(s);r.async=!0;
r.src=n;var u=t.getElementsByTagName(s)[0];
u.parentNode.insertBefore(r,u);}})(window,document,'https://sc-static.net/scevent.min.js');
snaptr('init', '{tag_id}', {{}});
snaptr('track', 'PAGE_VIEW');
</script>
""".strip()
    elif provider == "gtm" and tag_id:
        # tag_id = GTM-XXXX container id
        head = f"""
<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{tag_id}');</script>
""".strip()
        body = f"""
<!-- Google Tag Manager (noscript) -->
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={tag_id}"
height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
""".strip()
    elif provider == "yandex" and tag_id:
        head = f"""
<!-- Yandex Metrica -->
<script type="text/javascript">
(function(m,e,t,r,i,k,a){{m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};
m[i].l=1*new Date();for (var j = 0; j < document.scripts.length; j++) {{if (document.scripts[j].src === r) {{ return; }}}}
k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)}})
(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");
ym({tag_id}, "init", {{clickmap:true,trackLinks:true,accurateTrackBounce:true}});
</script>
""".strip()
    elif provider == "hotjar" and tag_id:
        head = f"""
<!-- Hotjar -->
<script>
(function(h,o,t,j,a,r){{
  h.hj=h.hj||function(){{(h.hj.q=h.hj.q||[]).push(arguments)}};
  h._hjSettings={{hjid:{tag_id},hjsv:6}};
  a=o.getElementsByTagName('head')[0];
  r=o.createElement('script');r.async=1;
  r.src=t+h._hjSettings.hjid+j+h._hjSettings.hjsv;
  a.appendChild(r);
}})(window,document,'https://static.hotjar.com/c/hotjar-','.js?sv=');
</script>
""".strip()
    elif provider == "clarity" and tag_id:
        head = f"""
<!-- Microsoft Clarity -->
<script>
(function(c,l,a,r,i,t,y){{
  c[a]=c[a]||function(){{(c[a].q=c[a].q||[]).push(arguments)}};
  t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
  y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
}})(window, document, "clarity", "script", "{tag_id}");
</script>
""".strip()
    return {"head": head, "body": body}


class PixelReq(BaseModel):
    id: Optional[str] = None
    provider: str  # bkz PIXEL_PROVIDERS
    name: Optional[str] = ""
    tag_id: Optional[str] = ""  # GA4 ID, Pixel ID, vb.
    head_snippet: Optional[str] = ""  # özel kod / override
    body_snippet: Optional[str] = ""
    is_active: bool = True
    # ---- CAPI (Server-Side Conversions API) fields ----
    capi_enabled: Optional[bool] = False
    access_token: Optional[str] = ""        # plain (taşıma için; vault'ta saklamak öneriliyor)
    vault_key: Optional[str] = ""           # secrets_vault collection key
    env_token_key: Optional[str] = ""       # env override (örn META_CAPI_TOKEN)
    test_event_code: Optional[str] = ""     # Meta TEST00001, GA4 debug, …
    tenant_id: Optional[str] = None         # multi-tenant (SaaS)
    # Meta CAPI alan-bazlı gönderim bayrakları (rollback). None/eksik → hepsi açık (mevcut davranış).
    # Örn: {"fbp": true, "fbc": true, "email": true, "phone": true, "external_id": false}
    field_flags: Optional[dict] = None
    # Provider'a özel ekstra (Pinterest ad_account_id, Snapchat conversion_event vs)
    extra: Optional[dict] = None


@router.get("/active-public")
async def list_active_public(response: Response):
    """Frontend'in <head> ve <body> sonuna enjekte edeceği kodlar.
    Auth yok — sadece aktif pixel'lerin önceden kaydedilmiş snippet'leri döner.
    60 saniye cache edilir (her sayfa yüklemesinde DB yorma).

    ÖNEMLİ: /{pid} dynamic route'undan ÖNCE tanımlı olmalı, aksi halde
    'active-public' string'i pid parametresi olarak yakalanıp 401 dönülür.
    """
    rows = await db.marketing_pixels.find(
        {"is_active": True}, {"_id": 0, "provider": 1, "name": 1, "head_snippet": 1, "body_snippet": 1}
    ).to_list(length=50)
    # KVKK gate bayrağı (varsayılan KAPALI) — frontend bu değere göre Meta/CAPI'yi onaya bağlar.
    try:
        from business_rules import get_rule as _get_rule
        consent_gate = bool(await _get_rule(db, "marketing.capi_consent_gate", False))
    except Exception:
        consent_gate = False
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return {
        "head": "\n".join([r.get("head_snippet") or "" for r in rows if r.get("head_snippet")]),
        "body": "\n".join([r.get("body_snippet") or "" for r in rows if r.get("body_snippet")]),
        "count": len(rows),
        "consent_gate": consent_gate,
    }


@router.get("/providers")
async def get_providers(current_user: dict = Depends(require_admin)):
    return {"providers": PIXEL_PROVIDERS}


@router.get("")
async def list_pixels(current_user: dict = Depends(require_admin)):
    rows = await db.marketing_pixels.find({}, {"_id": 0}).sort("created_at", -1).to_list(length=200)
    # Token'ları maskele
    for r in rows:
        if r.get("access_token"):
            r["_has_token"] = True
            r["access_token"] = "***"
        else:
            r["_has_token"] = bool(r.get("vault_key") or r.get("env_token_key"))
    return {"items": rows}


@router.post("")
async def upsert_pixel(req: PixelReq, current_user: dict = Depends(require_admin)):
    valid = {p["key"] for p in PIXEL_PROVIDERS}
    if req.provider not in valid:
        raise HTTPException(status_code=400, detail=f"Geçersiz sağlayıcı. Geçerli: {sorted(valid)}")

    # Kullanıcı head_snippet yazmadıysa veya bir önceki otomatik template'i koruyorsa,
    # tag_id'ye göre snippet'i regenerate et (tag_id değiştirildiğinde stale kalmasın diye).
    head = (req.head_snippet or "").strip()
    body = (req.body_snippet or "").strip()

    # Sistem template'ı (header comment'le başlar). Bu pattern'ler için otomatik regenerate.
    AUTO_TEMPLATE_MARKERS = (
        "<!-- Google Analytics 4 -->", "<!-- Meta Pixel -->",
        "<!-- Google Ads", "<!-- TikTok Pixel -->",
        "<!-- Pinterest Tag -->", "<!-- Snap Pixel Code -->",
        "<!-- Google Tag Manager -->", "<!-- Yandex.Metrika counter -->",
        "<!-- Hotjar Tracking Code -->", "<!-- Microsoft Clarity -->",
    )
    is_auto_template = head and any(m in head for m in AUTO_TEMPLATE_MARKERS)
    if req.tag_id and (not head or is_auto_template):
        tpl = _template_snippet(req.provider, req.tag_id.strip())
        head = tpl.get("head") or ""
        body = tpl.get("body") or body

    now_iso = datetime.now(timezone.utc).isoformat()
    data = {
        "provider": req.provider,
        "name": req.name or next((p["name"] for p in PIXEL_PROVIDERS if p["key"] == req.provider), req.provider),
        "tag_id": (req.tag_id or "").strip(),
        "head_snippet": head,
        "body_snippet": body,
        "is_active": req.is_active,
        # CAPI fields
        "capi_enabled": bool(req.capi_enabled),
        "vault_key": (req.vault_key or "").strip() or None,
        "env_token_key": (req.env_token_key or "").strip() or None,
        "test_event_code": (req.test_event_code or "").strip() or None,
        "tenant_id": req.tenant_id,
        "field_flags": req.field_flags if isinstance(req.field_flags, dict) else None,
        "extra": req.extra or {},
        "updated_at": now_iso,
        "updated_by": current_user.get("email", ""),
    }
    # Access token: vault'a şifreli olarak yazılır; plain MongoDB'de tutulmaz
    if req.access_token:
        # Eğer vault_key belirtilmemişse otomatik üret (SaaS-safe)
        if not (req.vault_key or req.env_token_key):
            from hashlib import sha1
            sig = sha1(f"{req.provider}-{(req.tag_id or '').strip()}-{req.tenant_id or 'default'}".encode()).hexdigest()[:10]
            req.vault_key = f"capi_{req.provider}_{sig}"
            data["vault_key"] = req.vault_key
        # Vault'a yaz (env_token_key yoksa)
        if req.vault_key and not req.env_token_key:
            try:
                from security.crypto import encrypt as _vault_encrypt
                enc = _vault_encrypt(req.access_token.strip())
                await db.vault_secrets.update_one(
                    {"key": req.vault_key.strip()},
                    {"$set": {
                        "key": req.vault_key.strip(),
                        "value_enc": enc,
                        "description": f"CAPI {req.provider} token — {req.name or req.tag_id}",
                        "scope": "capi",
                        "updated_by": current_user.get("email"),
                        "updated_at": now_iso,
                    }, "$setOnInsert": {"created_at": now_iso}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"Vault upsert failed: {e}")
        # PLAIN text token'ı MongoDB'de tutma
        data["access_token"] = ""

    if req.id:
        existing = await db.marketing_pixels.find_one({"id": req.id}, {"_id": 0, "id": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
        await db.marketing_pixels.update_one({"id": req.id}, {"$set": data})
        data["id"] = req.id
    else:
        data["id"] = generate_id()
        data["created_at"] = now_iso
        await db.marketing_pixels.insert_one(data)
    data.pop("_id", None)
    return {"success": True, "pixel": data}


@router.post("/test-connection")
async def test_capi_connection(req: PixelReq, current_user: dict = Depends(require_admin)):
    """CAPI bağlantısını test event göndererek dener.

    Frontend'den ya kayıtlı pixel id'yi referans edebilir ya da form verisini
    doğrudan yollayabilir. test_event_code önerilir (canlı raporu kirletmemek için).
    """
    from datetime import datetime
    from services.capi.orchestrator import _resolve_access_token, PROVIDERS

    provider = req.provider.lower()
    mod = PROVIDERS.get(provider)
    if not mod:
        raise HTTPException(status_code=400,
                            detail=f"Bu sağlayıcı için CAPI desteklenmiyor: {provider}")

    # Resolve token: from form, else from existing record's vault/env
    token = (req.access_token or "").strip()
    if not token and req.id:
        existing = await db.marketing_pixels.find_one({"id": req.id}, {"_id": 0})
        if existing:
            token = await _resolve_access_token(existing) or ""

    pixel_id = (req.tag_id or "").strip()
    if not pixel_id or not token:
        raise HTTPException(status_code=400,
                            detail="Test için pixel_id ve access_token gerekli.")

    res = await mod.test_connection(
        pixel_id=pixel_id, access_token=token,
        test_event_code=(req.test_event_code or "").strip() or None,
    )
    # Persist tester log
    await db.capi_event_logs.insert_one({
        "id": generate_id(),
        "provider": provider, "event_name": "test_connection",
        "tenant_id": req.tenant_id, "ok": bool(res.get("ok")),
        "status": res.get("status"), "response": res.get("response") or {},
        "error": res.get("error"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_test": True,
    })
    return res


@router.get("/{pid}")
async def get_pixel(pid: str, current_user: dict = Depends(require_admin)):
    px = await db.marketing_pixels.find_one({"id": pid}, {"_id": 0})
    if not px:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    # Token'ı maskele
    if px.get("access_token"):
        px["access_token"] = "***"
        px["_has_token"] = True
    return {"pixel": px}


@router.get("/capi/queue/status")
async def capi_queue_status(current_user: dict = Depends(require_admin)):
    """Stuck CAPI eventlerin özet durumu."""
    pending = await db.capi_event_queue.count_documents({"dead": {"$ne": True}})
    dead = await db.capi_event_queue.count_documents({"dead": True})
    total_logs = await db.capi_event_logs.count_documents({})
    last_failed = await db.capi_event_logs.find(
        {"ok": False}, {"_id": 0}
    ).sort("created_at", -1).limit(10).to_list(10)
    return {"pending": pending, "dead": dead,
            "total_logs": total_logs, "recent_failures": last_failed}


@router.post("/capi/queue/run-now")
async def capi_queue_run_now(current_user: dict = Depends(require_admin)):
    """Tüm bekleyen kuyruğu hemen tetikle (cron'u beklemeden)."""
    from services.capi.orchestrator import retry_queue_once
    res = await retry_queue_once(db)
    return res


def _capi_logs_query(provider=None, event_name=None, ok=None, date_from=None, date_to=None) -> dict:
    """CAPI log filtre sorgusu. date_from/date_to = 'YYYY-MM-DD' (gün bazlı, dahil).
    created_at ISO string olduğundan aralık string karşılaştırmasıyla çözülür."""
    q = {}
    if provider: q["provider"] = provider
    if event_name: q["event_name"] = event_name
    if ok is not None: q["ok"] = ok
    rng = {}
    if date_from:
        rng["$gte"] = f"{str(date_from)[:10]}T00:00:00"
    if date_to:
        # Bitiş gününü DAHİL etmek için ertesi günün başlangıcından küçük (< to+1 00:00).
        try:
            _d = datetime.strptime(str(date_to)[:10], "%Y-%m-%d") + timedelta(days=1)
            rng["$lt"] = _d.strftime("%Y-%m-%dT00:00:00")
        except Exception:
            pass
    if rng:
        q["created_at"] = rng
    return q


@router.get("/capi/logs")
async def capi_logs(
    current_user: dict = Depends(require_admin),
    limit: int = 100,
    skip: int = 0,
    provider: Optional[str] = None,
    event_name: Optional[str] = None,
    ok: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Son CAPI gönderim loglarını filtreyle getir (tarih aralığı destekli)."""
    q = _capi_logs_query(provider, event_name, ok, date_from, date_to)
    items = await db.capi_event_logs.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(min(limit, 500)).to_list(limit)
    total = await db.capi_event_logs.count_documents(q)
    return {"items": items, "total": total}


@router.get("/capi/logs/export")
async def capi_logs_export(
    current_user: dict = Depends(require_admin),
    provider: Optional[str] = None,
    event_name: Optional[str] = None,
    ok: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50000,
):
    """Filtrelenmiş CAPI loglarını CSV olarak indir (PII'siz). match_signals düzleştirilir."""
    import csv as _csv
    import io as _io
    q = _capi_logs_query(provider, event_name, ok, date_from, date_to)
    rows = await db.capi_event_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(min(int(limit or 50000), 200000)).to_list(None)
    buf = _io.StringIO()
    cols = ["created_at", "provider", "event_name", "event_id", "ok", "status", "from_retry", "is_test",
            "s_email", "s_phone", "s_external_id", "s_fbp", "s_fbc", "s_ttclid", "s_ttp", "s_ip", "s_ua", "ip_version",
            "message"]
    w = _csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        ms = r.get("match_signals") or {}
        msg = r.get("error") or r.get("response") or ""
        msg = str(msg).replace("\n", " ")[:300]
        w.writerow([
            r.get("created_at", ""), r.get("provider", ""), r.get("event_name", ""), r.get("event_id", ""),
            r.get("ok", ""), r.get("status", ""), r.get("from_retry", ""), r.get("is_test", ""),
            ms.get("email", ""), ms.get("phone", ""), ms.get("external_id", ""), ms.get("fbp", ""),
            ms.get("fbc", ""), ms.get("ttclid", ""), ms.get("ttp", ""), ms.get("ip", ""), ms.get("ua", ""),
            ms.get("ip_version", ""), msg,
        ])
    fname = f"capi_logs_{(date_from or 'all')}_{(date_to or 'all')}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/capi/queue")
async def capi_queue(
    current_user: dict = Depends(require_admin),
    limit: int = 100,
    dead: Optional[bool] = None,
):
    """Bekleyen / ölü kuyruktaki event'leri getir."""
    q = {}
    if dead is not None:
        q["dead"] = dead if dead else {"$ne": True}
    items = await db.capi_event_queue.find(q, {"_id": 0}).sort("next_try_at", 1).limit(min(limit, 500)).to_list(limit)
    return {"items": items, "count": len(items)}


@router.post("/capi/queue/{qid}/retry")
async def capi_retry_one(qid: str, current_user: dict = Depends(require_admin)):
    """Tek bir kuyruk öğesini hemen denemek için işaretle (next_try_at=now)."""
    res = await db.capi_event_queue.update_one(
        {"id": qid},
        {"$set": {"next_try_at": datetime.now(timezone.utc).isoformat(),
                  "dead": False}},
    )
    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Kuyruk öğesi bulunamadı")
    return {"ok": True, "id": qid}


def _pct(n: int, d: int) -> float:
    """Yüzde (0 bölmeye dayanıklı, 1 ondalık)."""
    return round((n * 100.0 / d), 1) if d else 0.0


@router.get("/capi/audit")
async def capi_audit(
    current_user: dict = Depends(require_admin),
    provider: str = "meta",
    sample: int = 100,
    window_hours: int = 72,
):
    """Post-deploy DOĞRULAMA denetimi — SALT-OKUNUR, PII'siz.

    Meta CAPI'nin son N server Purchase eventini ve `window_hours` içindeki tüm
    event'lerini `capi_event_logs` + `capi_event_queue` üzerinden özetler.
    Ham e-posta/telefon/IP/fbp DÖNMEZ — yalnız var/yok sayıları, oranlar, kaynak
    dağılımı. Event akışına HİÇ dokunmaz (yeni event üretmez/göndermez)."""
    from datetime import timedelta
    sample = max(1, min(int(sample or 100), 500))
    window_hours = max(1, min(int(window_hours or 72), 24 * 30))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=window_hours)).isoformat()

    # ── 1) Son N server Purchase — PII'siz coverage audit (§2) ────────────────
    # İlk gönderim satırlarında match_signals bulunur (retry-başarı satırlarında yok).
    purchases = await db.capi_event_logs.find(
        {"provider": provider, "event_name": "purchase",
         "match_signals": {"$exists": True}},
        {"_id": 0, "event_id": 1, "ok": 1, "status": 1, "match_signals": 1, "created_at": 1},
    ).sort("created_at", -1).limit(sample).to_list(sample)

    def _sig(rows, key):
        return sum(1 for r in rows if (r.get("match_signals") or {}).get(key))

    total_p = len(purchases)
    coverage = {
        "total_server_purchase": total_p,
        "has_email":        {"n": _sig(purchases, "email"),        "pct": _pct(_sig(purchases, "email"), total_p)},
        "has_phone":        {"n": _sig(purchases, "phone"),        "pct": _pct(_sig(purchases, "phone"), total_p)},
        "has_fbp":          {"n": _sig(purchases, "fbp"),          "pct": _pct(_sig(purchases, "fbp"), total_p)},
        "has_fbc":          {"n": _sig(purchases, "fbc"),          "pct": _pct(_sig(purchases, "fbc"), total_p)},
        "has_ttclid":       {"n": _sig(purchases, "ttclid"),       "pct": _pct(_sig(purchases, "ttclid"), total_p)},
        "has_ttp":          {"n": _sig(purchases, "ttp"),          "pct": _pct(_sig(purchases, "ttp"), total_p)},
        "has_external_id":  {"n": _sig(purchases, "external_id"),  "pct": _pct(_sig(purchases, "external_id"), total_p)},
        "has_ip":           {"n": _sig(purchases, "ip"),           "pct": _pct(_sig(purchases, "ip"), total_p)},
        "has_user_agent":   {"n": _sig(purchases, "ua"),           "pct": _pct(_sig(purchases, "ua"), total_p)},
        "meta_api_ok":      {"n": sum(1 for r in purchases if r.get("ok")), "pct": _pct(sum(1 for r in purchases if r.get("ok")), total_p)},
    }
    _ipv4 = sum(1 for r in purchases if (r.get("match_signals") or {}).get("ip_version") == 4)
    _ipv6 = sum(1 for r in purchases if (r.get("match_signals") or {}).get("ip_version") == 6)
    coverage["ip_version_4"] = {"n": _ipv4, "pct": _pct(_ipv4, total_p)}
    coverage["ip_version_6"] = {"n": _ipv6, "pct": _pct(_ipv6, total_p)}

    # ── 2) _fbp KAYNAK dağılımı + source-path (§2/§3) — orders JOIN ────────────
    order_nums = [str(r.get("event_id")) for r in purchases if r.get("event_id")]
    omap = {}
    if order_nums:
        async for o in db.orders.find(
            {"order_number": {"$in": order_nums}},
            {"_id": 0, "order_number": 1, "capi_purchase_source": 1,
             "click_ids": 1, "attribution": 1},
        ):
            omap[str(o.get("order_number"))] = o

    fbp_src = {"order_snapshot": 0, "attribution_fallback": 0, "none": 0, "unknown_no_order": 0}
    src_path = {}
    for r in purchases:
        on = str(r.get("event_id") or "")
        o = omap.get(on)
        has_fbp = bool((r.get("match_signals") or {}).get("fbp"))
        # _fbp kaynağı
        if not o:
            fbp_src["unknown_no_order"] += 1
        elif not has_fbp:
            fbp_src["none"] += 1
        elif (o.get("click_ids") or {}).get("fbp"):
            fbp_src["order_snapshot"] += 1
        else:
            fbp_src["attribution_fallback"] += 1
        # source-path (hangi akıştan gönderildi)
        sp = ((o or {}).get("capi_purchase_source") or "unknown") if o is not None else "unknown_no_order"
        b = src_path.setdefault(sp, {"n": 0, "email": 0, "phone": 0, "fbp": 0, "fbc": 0, "ttclid": 0, "ttp": 0, "external_id": 0})
        b["n"] += 1
        for k in ("email", "phone", "fbp", "fbc", "ttclid", "ttp", "external_id"):
            if (r.get("match_signals") or {}).get(k):
                b[k] += 1

    # ── 3) Production sağlık kontrolü (§4) — window_hours penceresi ────────────
    base = {"provider": provider, "created_at": {"$gte": since}}
    total_w = await db.capi_event_logs.count_documents(base)
    ok_w = await db.capi_event_logs.count_documents({**base, "ok": True})
    err_w = total_w - ok_w
    retry_success = await db.capi_event_logs.count_documents({**base, "from_retry": True})
    viewcontent_vol = await db.capi_event_logs.count_documents({**base, "event_name": "view_item"})
    pending_q = await db.capi_event_queue.count_documents({"provider": provider, "dead": {"$ne": True}})
    dead_q = await db.capi_event_queue.count_documents({"provider": provider, "dead": True})

    by_event = {}
    async for row in db.capi_event_logs.aggregate([
        {"$match": base},
        {"$group": {"_id": "$event_name",
                    "n": {"$sum": 1},
                    "ok": {"$sum": {"$cond": ["$ok", 1, 0]}}}},
    ]):
        by_event[row["_id"] or "?"] = {"n": row["n"], "ok": row["ok"],
                                       "err": row["n"] - row["ok"],
                                       "ok_pct": _pct(row["ok"], row["n"])}
    status_dist = {}
    async for row in db.capi_event_logs.aggregate([
        {"$match": {**base, "ok": False}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        status_dist[str(row["_id"])] = row["n"]

    health = {
        "window_hours": window_hours,
        "total_events": total_w,
        "ok": ok_w, "error": err_w, "error_rate_pct": _pct(err_w, total_w),
        "retry_success": retry_success,
        "queue_pending": pending_q, "dead_letter": dead_q,
        "viewcontent_server_volume": viewcontent_vol,
        "by_event": by_event,
        "error_status_distribution": status_dist,
        "send_latency_ms": None,  # KAYIT YOK — per-event süre ölçülmüyor (event akışını değiştirmemek için eklenmedi)
    }

    # ── Sağlayıcı CONFIG durumu + ham log sayıları (neden 0? teşhisi) — secret DÖNMEZ ──
    _pix = await db.marketing_pixels.find(
        {"provider": provider},
        {"_id": 0, "id": 1, "name": 1, "is_active": 1, "capi_enabled": 1,
         "tag_id": 1, "access_token": 1, "vault_key": 1, "env_token_key": 1},
    ).to_list(50)
    provider_status = [{
        "name": p.get("name") or p.get("id"),
        "is_active": bool(p.get("is_active")),
        "capi_enabled": bool(p.get("capi_enabled")),
        "has_tag_id": bool(str(p.get("tag_id") or "").strip()),
        "has_token": bool(str(p.get("access_token") or "").strip()
                          or str(p.get("vault_key") or "").strip()
                          or str(p.get("env_token_key") or "").strip()),
    } for p in _pix]
    raw_provider_total = await db.capi_event_logs.count_documents({"provider": provider})
    raw_all_total = await db.capi_event_logs.count_documents({})
    prov_dist = {}
    async for _row in db.capi_event_logs.aggregate([{"$group": {"_id": "$provider", "n": {"$sum": 1}}}]):
        prov_dist[_row["_id"] or "?"] = _row["n"]
    # HAM event_name dağılımı (bu sağlayıcı) — TikTok kayıtları BAŞKA bir event_name altında
    # (ör. mapped 'CompletePayment') tutuluyorsa audit 'purchase' filtresi 0 gösterirdi; bu dağılım
    # onu ham veriden ORTAYA ÇIKARIR. (Kod: log internal 'purchase' yazar → burada 'purchase' beklenir.)
    evname_dist = {}
    async for _row in db.capi_event_logs.aggregate([
        {"$match": {"provider": provider}},
        {"$group": {"_id": "$event_name", "n": {"$sum": 1}}},
    ]):
        evname_dist[_row["_id"] or "?"] = _row["n"]

    _diag = None
    if total_p == 0:
        if raw_provider_total == 0:
            if not provider_status:
                _diag = (f"'{provider}' için hiç pixel kaydı yok — panelden CAPI pixel ekleyin "
                         f"(provider={provider}).")
            else:
                _bad = [s for s in provider_status
                        if not (s["is_active"] and s["capi_enabled"] and s["has_tag_id"] and s["has_token"])]
                if _bad:
                    _diag = (f"'{provider}' için HİÇ ham log yok VE config eksik "
                             f"(is_active/capi_enabled/tag_id/token). Server event GÖNDERİLMİYOR → "
                             f"bu bir CONFIG sorunu (audit sorgusu değil). Eksikleri tamamlayın.")
                else:
                    _diag = (f"'{provider}' config tam görünüyor ama hiç ham log yok → gerçek bir "
                             f"Purchase henüz bu sağlayıcıya dispatch edilmemiş ya da her denemede "
                             f"skip/exception oluyor olabilir.")
        else:
            _diag = (f"'{provider}' ham log VAR ({raw_provider_total}) ama Purchase coverage 0 → "
                     f"event_name/filtre uyuşmazlığı olabilir (Logs sekmesinden ham kayda bakın).")

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "provider": provider,
        "sample_size": total_p,
        "window_since": since,
        "provider_status": provider_status,
        "raw_counts": {
            "this_provider_all_time": raw_provider_total,
            "all_providers_all_time": raw_all_total,
            "provider_distribution": prov_dist,
            "event_name_distribution": evname_dist,  # bu sağlayıcının ham event_name kırılımı (§6 teşhisi)
        },
        "diagnosis": _diag,
        "purchase_coverage": coverage,
        "fbp_source": fbp_src,
        "purchase_source_path": src_path,
        "health": health,
        "notes": [
            "PII'siz: ham email/telefon/IP/fbp değeri DÖNMEZ; yalnız var/yok sayısı ve oran.",
            "coverage örneği = son N ilk-gönderim Purchase logu (retry-başarı satırları match_signals taşımaz, hariç).",
            "source-path & _fbp kaynağı orders JOIN ile (event_id=order_number → orders.capi_purchase_source / click_ids).",
            "send_latency_ms=None: per-event gönderim süresi loglanmıyor (ölçüm eklemek event akışını değiştirir — freeze gereği yapılmadı).",
        ],
    }


@router.delete("/capi/queue/{qid}")
async def capi_delete_one(qid: str, current_user: dict = Depends(require_admin)):
    res = await db.capi_event_queue.delete_one({"id": qid})
    return {"ok": True, "deleted": res.deleted_count}


@router.delete("/capi/logs/clear-old")
async def capi_logs_clear_old(
    current_user: dict = Depends(require_admin),
    days: int = 30,
):
    """X günden eski logları temizle."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = await db.capi_event_logs.delete_many({"created_at": {"$lt": cutoff}})
    return {"ok": True, "deleted": res.deleted_count, "older_than_days": days}


@router.delete("/{pid}")
async def delete_pixel(pid: str, current_user: dict = Depends(require_admin)):
    res = await db.marketing_pixels.delete_one({"id": pid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    return {"success": True}
