"""
=============================================================================
email_marketing.py — Brevo birincil, AWS SES yedek E-POSTA PAZARLAMA
=============================================================================
YALNIZ PAZARLAMA. İşlemsel e-posta (sipariş/şifre) Zoho ZeptoMail'den gider.
Brevo Marketing Campaigns birincil kanaldır; mevcut SES ayarları silinmez ve
ayar açıkken yalnız yapılandırma/yedek kanal olarak kullanılabilir.

Alıcılar: db.newsletter_subscribers içinde `active!=False` VE `consent==True` olanlar
(KVKK/İYS: yalnız açık rıza verenlere ticari e-posta). Her maile abonelikten-çık linki
eklenir; çıkanlara İYS'ye RET bildirilir.

ENDPOINTS:
  GET/PUT /api/admin/email-marketing/settings     — Brevo + yedek SES ayarları
  POST    /api/admin/email-marketing/test         — tek test maili
  GET     /api/admin/email-marketing/audience     — izinli kitle (bülten + İYS + profil) sayısı/kırılımı
  GET     /api/admin/email-marketing/diagnose?email= — bir adrese neden mail gitti/gitmedi
  GET/POST /api/admin/email-marketing/campaigns/{id}/missing | /send-missing — almayanlara gönder
  POST    /api/admin/email-marketing/campaigns     — kampanya oluştur + arka planda gönder
  GET     /api/admin/email-marketing/campaigns     — kampanya geçmişi
  GET     /api/admin/email-marketing/suppressions — kara liste (bounce/şikâyet)
  POST    /api/email-marketing/ses-webhook        — Public: AWS SNS bounce/şikâyet bildirimi
  GET     /api/email-marketing/unsubscribe        — Public: abonelikten çık ONAY sayfası (değişiklik yapmaz)
  POST    /api/email-marketing/unsubscribe        — Public: abonelikten çıkışı uygular (form + RFC 8058 one-click)

GÖNDERİM SAĞLIĞI (SES hesabını korur): AWS, bounce oranı %5'i veya şikâyet oranı
%0,1'i aşan hesapların gönderimini ASKIYA ALIR. Bu yüzden SNS'ten gelen kalıcı
bounce/şikâyet adresleri `email_suppressions` kara listesine yazılır ve bir daha
ASLA maillenmez; abone kaydı da pasifleştirilir.
=============================================================================
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone

from .deps import db, generate_id, logger, require_permission

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from email_ses import get_ses_config, is_configured, send_ses_email  # noqa: E402
from email_brevo import (  # noqa: E402
    create_and_send_campaign,
    get_brevo_config,
    is_configured as brevo_is_configured,
    send_test_email as send_brevo_test,
    sync_contacts as sync_brevo_contacts,
    validate_config as validate_brevo_config,
)
from security.sns import validate_subscribe_url, verify_sns_message  # noqa: E402

admin_router = APIRouter(prefix="/admin/email-marketing", tags=["email-marketing-admin"])
public_router = APIRouter(prefix="/email-marketing", tags=["email-marketing-public"])

_SECRET_MASK = "********"


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── SUPPRESSION (kara liste) ──────────────────────────────────────────────────
# NEDEN ZORUNLU: AWS SES, gönderen hesabın BOUNCE oranı %5'i veya ŞİKÂYET (spam
# işaretleme) oranı %0,1'i aşarsa önce uyarır, sonra gönderim yetkisini ASKIYA ALIR.
# Kalıcı bounce veren (kapanmış/yanlış) adreslere göndermeye devam etmek hesabı yakar.
# Bu yüzden bounce/şikâyet gelen adres bir daha ASLA maillenmez.
async def _suppress(email: str, reason: str, detail: str = "") -> None:
    email = (email or "").strip().lower()
    if not email:
        return
    await db.email_suppressions.update_one(
        {"email": email},
        {"$set": {"email": email, "reason": reason, "detail": (detail or "")[:300],
                  "updated_at": _now()},
         "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    # Aboneyi de pasifleştir: kitle sayısı ve gelecekteki kampanyalar doğru olsun.
    try:
        await db.newsletter_subscribers.update_one(
            {"email": email}, {"$set": {"active": False, "suppressed_reason": reason,
                                        "suppressed_at": _now()}})
    except Exception:
        pass


async def _record_ret(email: str) -> None:
    """Abonelikten çıkış / spam şikâyeti = izin geri çekildi → İYS'ye RET (günlük toplu bildirilir)."""
    try:
        from .iys import record_consent
        await record_consent(recipient_email=email, recipient_phone="", channels=["EPOSTA"], status="RET", source="HS_WEB")
    except Exception as e:
        logger.warning(f"[email-marketing] İYS RET kaydı yazılamadı: {e}")


async def _suppressed_set() -> set:
    """Kampanya başında kara listeyi TEK sorguda çeker (alıcı başına sorgu atmamak için —
    hem hız hem MongoDB veri transferi maliyeti)."""
    try:
        emails = set()
        async for row in db.email_suppressions.find({}, {"_id": 0, "email": 1}):
            if row.get("email"):
                emails.add(row["email"].strip().lower())
        return emails
    except Exception:
        logger.error("[email-marketing] suppression listesi okunamadı; gönderim engellendi")
        raise HTTPException(status_code=503, detail="Gönderim engelleme listesi okunamadı; işlem durduruldu.") from None


def _unsub_token(email: str) -> str:
    """Abone kaydı OLMAYAN alıcılar (sipariş/üyelik izni) için abonelikten-çık jetonu:
    e-postaya bağlı HMAC (JWT_SECRET) — link tahmin edilemez, adres dışarıdan çıkarılamaz."""
    import hashlib, hmac
    try:
        from .deps import JWT_SECRET as _sec
    except Exception:
        _sec = "meluni"
    return "u" + hmac.new(str(_sec).encode(), (email or "").strip().lower().encode(), hashlib.sha256).hexdigest()[:28]


async def _campaign_audience() -> dict:
    """KAMPANYA KİTLESİ — e-posta ticari ileti izni olan HERKES (tek yerde hesaplanır):
      • db.newsletter_subscribers  aktif + consent=True  (site altı bülten)
      • db.iys_consents            EPOSTA / ONAY (ödeme sayfası, üyelik, hesabım) — alıcı başına EN SON karar
      • db.users.accepts_marketing (profil tercihi; sipariş izniyle de True olur)
    Çıkarılanlar: kara liste (bounce/şikâyet) + en son kararı RET olanlar (abonelikten çıkış / İYS RET).
    Eskiden YALNIZ bülten aboneleri alınıyordu; ödeme sayfasında izin veren müşterilere mail gitmiyordu.
    Dönüş: {"rows": [ {email,id,name,origin} ... e-posta sırasında ], "breakdown": {...}, "suppressed": n}"""
    from .consents import _collect as _consents_latest
    latest = await _consents_latest()
    suppressed = await _suppressed_set()
    rows: dict = {}
    breakdown = {"newsletter": 0, "iys": 0, "profile": 0, "ret": 0, "suppressed": 0}

    def _decision(email: str) -> str:
        rec = latest.get(("email", email))
        if not rec:
            return ""
        return "ret" if rec.get("suppressed") else (rec.get("status") or "")

    async for s in db.newsletter_subscribers.find({"active": {"$ne": False}, "consent": True},
                                                  {"_id": 0, "email": 1, "id": 1, "name": 1, "first_name": 1, "full_name": 1}):
        em = (s.get("email") or "").strip().lower()
        if em and em not in rows:
            rows[em] = {"email": em, "id": s.get("id") or "", "origin": "bülten",
                        "name": (s.get("name") or s.get("first_name") or s.get("full_name") or "").strip()}
    for (ch, em), rec in latest.items():
        if ch != "email" or rec.get("status") != "onay" or em in rows:
            continue
        rows[em] = {"email": em, "id": "", "origin": "iys", "name": ""}
    async for u in db.users.find({"accepts_marketing": True, "email": {"$exists": True, "$ne": ""}},
                                 {"_id": 0, "email": 1, "first_name": 1, "name": 1}):
        em = (u.get("email") or "").strip().lower()
        if em and em not in rows:
            rows[em] = {"email": em, "id": "", "origin": "profil",
                        "name": (u.get("first_name") or u.get("name") or "").strip()}
    out = []
    for em in sorted(rows):
        r = rows[em]
        if em in suppressed:
            breakdown["suppressed"] += 1
            continue
        if _decision(em) == "ret":
            breakdown["ret"] += 1
            continue
        if not r["id"]:
            r["id"] = _unsub_token(em)
        breakdown["newsletter" if r["origin"] == "bülten" else ("iys" if r["origin"] == "iys" else "profile")] += 1
        out.append(r)
    return {"rows": out, "breakdown": breakdown, "suppressed": len(suppressed)}


async def _audience_count() -> int:
    return len((await _campaign_audience())["rows"])


async def _sent_emails_of(campaign_ids: list) -> set:
    """Verilen kampanyalarda fiilen gönderilmiş (SES 'sent' / Brevo 'submitted') adresler —
    'almayanlara gönder' için hariç tutma kümesi."""
    out = set()
    ids = [c for c in (campaign_ids or []) if c]
    if not ids:
        return out
    try:
        async for x in db.email_campaign_sends.find({"campaign_id": {"$in": ids}, "status": {"$in": ["sent", "submitted"]}},
                                                    {"_id": 0, "email": 1}):
            if x.get("email"):
                out.add(x["email"].strip().lower())
    except Exception:
        pass
    return out


# ── GÖNDERİM SIRASINDA İZİN YENİDEN KONTROLÜ ───────────────────────────────────
# Kitle kampanya BAŞINDA hesaplanır; binlerce alıcılı SES gönderimi dakikalar sürer. Bu sürede
# gelen abonelikten çıkış / İYS RET / bounce-şikâyet eskiden YOK SAYILIYORDU (anlık görüntü).
# Artık her _OPTOUT_RECHECK_BATCH alıcıda bir, sıradaki partinin adresleri için güncel
# kara liste + anlık görüntüden SONRA gelen ret olayları tek seferde sorgulanır.
_OPTOUT_RECHECK_BATCH = 50


async def _optouts_since(rows: list, since_iso: str) -> set:
    """`rows` içindeki adreslerden ŞU AN gönderilmemesi gerekenler (küçük harf e-posta kümesi).
    Kontrol okunamazsa gönderim DURUR (503) — izin doğrulanamadan ticari ileti gitmez."""
    emails = sorted({(r.get("email") or "").strip().lower() for r in rows if (r.get("email") or "").strip()})
    if not emails:
        return set()
    drop: set = set()

    def _add(row):
        em = (row.get("email") or "").strip().lower()
        if em:
            drop.add(em)
    try:
        # 1) Kara liste (bounce/şikâyet) — her zaman güncel hâli
        async for x in db.email_suppressions.find({"email": {"$in": emails}}, {"_id": 0, "email": 1}):
            _add(x)
        # 2) Anlık görüntüden SONRA gelen İYS RET (abonelikten çık, şikâyet, panel/İYS iptali)
        async for x in db.iys_consents.find(
                {"email": {"$in": emails}, "channels": "EPOSTA", "status": {"$in": ["RET", "ret", "Ret"]},
                 "created_at": {"$gte": since_iso}}, {"_id": 0, "email": 1}):
            _add(x)
        # 3) Bülten aboneliğinden çıkış / pasifleştirme (anlık görüntüden sonra)
        async for x in db.newsletter_subscribers.find(
                {"email": {"$in": emails},
                 "$or": [{"unsubscribed_at": {"$gte": since_iso}}, {"suppressed_at": {"$gte": since_iso}}]},
                {"_id": 0, "email": 1}):
            _add(x)
        # 4) Üye profilinden pazarlama izni kapatıldı (zaman damgalı çıkış)
        async for x in db.users.find(
                {"email": {"$in": emails}, "marketing_unsubscribed_at": {"$gte": since_iso}},
                {"_id": 0, "email": 1}):
            _add(x)
        # 5) YALNIZ profil izniyle kitlede olanlar: izin artık açık değilse (zaman damgasız
        #    profil değişikliği dahil) gönderme.
        _profil = [(r.get("email") or "").strip().lower() for r in rows if r.get("origin") == "profil"]
        if _profil:
            async for x in db.users.find({"email": {"$in": _profil}, "accepts_marketing": {"$ne": True}},
                                         {"_id": 0, "email": 1}):
                _add(x)
    except Exception:
        logger.error("[email-marketing] gönderim sırasında izin kontrolü okunamadı; gönderim durduruldu")
        raise HTTPException(status_code=503, detail="İzin/kara liste kontrolü okunamadı; gönderim durduruldu.") from None
    return drop


async def _site_base() -> str:
    """Abonelikten-çık linki için PUBLIC site adresi (beyaz etiket: firma ayarından)."""
    try:
        s = await db.settings.find_one({"id": "main"}, {"_id": 0, "company_info": 1}) or {}
        url = ((s.get("company_info") or {}).get("site_url") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    return (os.environ.get("SITE_URL") or "https://meluni.com.tr").rstrip("/")


# ── Sağlayıcı ayarları ──────────────────────────────────────────────────────
@admin_router.get("/settings")
async def get_settings(current_user: dict = Depends(require_permission("settings.emails"))):
    brevo = await db.settings.find_one({"id": "email_brevo"}, {"_id": 0}) or {}
    ses = await db.settings.find_one({"id": "email_ses"}, {"_id": 0}) or {}
    routing = await db.settings.find_one({"id": "email_marketing_provider"}, {"_id": 0}) or {}
    return {
        "provider": routing.get("provider") or "brevo",
        "fallback_enabled": bool(routing.get("fallback_enabled", True)),
        "brevo_enabled": bool(brevo.get("enabled")),
        "brevo_api_key": _SECRET_MASK if brevo.get("api_key") else "",
        "brevo_from_email": brevo.get("from_email", ""),
        "brevo_from_name": brevo.get("from_name", ""),
        "brevo_reply_to": brevo.get("reply_to", ""),
        "brevo_list_id": str(brevo.get("list_id") or ""),
        "brevo_webhook_secret": _SECRET_MASK if brevo.get("webhook_secret") else "",
        "ses_enabled": bool(ses.get("enabled")),
        "ses_region": ses.get("region", ""),
        "ses_access_key": ses.get("access_key", ""),
        "ses_secret_key": _SECRET_MASK if ses.get("secret_key") else "",
        "ses_from_email": ses.get("from_email", ""),
        "ses_from_name": ses.get("from_name", ""),
        "ses_configuration_set": ses.get("configuration_set", ""),
        "ses_reply_to": ses.get("reply_to", ""),
        "ses_unsubscribe_mailto": ses.get("unsubscribe_mailto", ""),
        "brevo_configured": brevo_is_configured(await get_brevo_config(db)),
        "ses_configured": is_configured(await get_ses_config(db)),
        "configured": brevo_is_configured(await get_brevo_config(db)) or (
            bool(routing.get("fallback_enabled", True)) and is_configured(await get_ses_config(db))
        ),
    }


@admin_router.put("/settings")
async def save_settings(payload: dict, current_user: dict = Depends(require_permission("settings.emails"))):
    payload = payload or {}
    provider = payload.get("provider") if payload.get("provider") in ("brevo", "ses") else "brevo"
    routing = {"provider": provider, "fallback_enabled": bool(payload.get("fallback_enabled", True)), "updated_at": _now()}

    brevo = {
        "enabled": bool(payload.get("brevo_enabled")),
        "from_email": str(payload.get("brevo_from_email") or "").strip(),
        "from_name": str(payload.get("brevo_from_name") or "").strip(),
        "reply_to": str(payload.get("brevo_reply_to") or "").strip(),
        "updated_at": _now(),
    }
    try:
        brevo["list_id"] = int(payload.get("brevo_list_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Brevo liste kimliği sayı olmalıdır.")
    api_key = str(payload.get("brevo_api_key") or "").strip()
    if api_key and api_key != _SECRET_MASK:
        try:
            from security.crypto import encrypt as _enc
            brevo["api_key"] = _enc(api_key)
        except Exception:
            raise HTTPException(status_code=503, detail="Anahtar şifrelenemedi; hiçbir ayar kaydedilmedi.") from None
    webhook_secret = str(payload.get("brevo_webhook_secret") or "").strip()
    if webhook_secret and webhook_secret != _SECRET_MASK:
        try:
            from security.crypto import encrypt as _enc
            brevo["webhook_secret"] = _enc(webhook_secret)
        except Exception:
            raise HTTPException(status_code=503, detail="Anahtar şifrelenemedi; hiçbir ayar kaydedilmedi.") from None

    ses_map = {
        "ses_enabled": "enabled", "ses_region": "region", "ses_access_key": "access_key",
        "ses_from_email": "from_email", "ses_from_name": "from_name",
        "ses_configuration_set": "configuration_set", "ses_reply_to": "reply_to",
        "ses_unsubscribe_mailto": "unsubscribe_mailto",
    }
    ses = {dst: payload.get(src) for src, dst in ses_map.items() if src in payload}
    ses["updated_at"] = _now()
    secret = str(payload.get("ses_secret_key") or "").strip()
    if secret and secret != _SECRET_MASK:
        try:
            from security.crypto import encrypt as _enc
            ses["secret_key"] = _enc(secret)
        except Exception:
            raise HTTPException(status_code=503, detail="Anahtar şifrelenemedi; hiçbir ayar kaydedilmedi.") from None

    await db.settings.update_one({"id": "email_marketing_provider"}, {"$set": routing, "$setOnInsert": {"id": "email_marketing_provider"}}, upsert=True)
    await db.settings.update_one({"id": "email_brevo"}, {"$set": brevo, "$setOnInsert": {"id": "email_brevo"}}, upsert=True)
    await db.settings.update_one({"id": "email_ses"}, {"$set": ses, "$setOnInsert": {"id": "email_ses"}}, upsert=True)
    return {"success": True, "provider": provider}


async def _selected_provider() -> tuple[str, dict]:
    routing = await db.settings.find_one({"id": "email_marketing_provider"}, {"_id": 0}) or {}
    primary = routing.get("provider") or "brevo"
    brevo = await get_brevo_config(db)
    ses = await get_ses_config(db)
    if primary == "brevo" and brevo_is_configured(brevo):
        return "brevo", brevo
    if primary == "ses" and is_configured(ses):
        return "ses", ses
    if routing.get("fallback_enabled", True):
        if primary != "brevo" and brevo_is_configured(brevo):
            return "brevo", brevo
        if primary != "ses" and is_configured(ses):
            return "ses", ses
    return primary, {}


@admin_router.get("/provider-status")
async def provider_status(current_user: dict = Depends(require_permission("tasarim.email"))):
    """Marketing staff can see readiness, never provider credentials/settings."""
    provider, cfg = await _selected_provider()
    return {"provider": provider, "configured": bool(cfg)}


@admin_router.post("/validate-provider")
async def validate_provider(current_user: dict = Depends(require_permission("settings.emails"))):
    """Birincil sağlayıcının kimlik bilgilerini gönderim yapmadan doğrular."""
    provider, cfg = await _selected_provider()
    if not cfg:
        raise HTTPException(status_code=400, detail="Kullanılabilir e-posta pazarlama sağlayıcısı yok.")
    if provider == "brevo":
        result = await validate_brevo_config(cfg)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=f"Brevo doğrulanamadı: {result.get('error') or 'bilinmeyen hata'}")
    return {"success": True, "provider": provider}


@admin_router.post("/test")
async def send_test(payload: dict, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Test e-postası gönderir. `subject`+`html` VERİLİRSE kullanıcının KAMPANYA İÇERİĞİNİ
    (markalı kabukla) gönderir — composer'dan 'Test olarak gönder'. Verilmezse SES Ayarları'nın
    sabit "SES Test" davranışı korunur. Placeholder'lar ({customer_name}/{kod}/{indirim}/URUN_LINKI)
    testte HAM gider (kişiselleştirme gerçek kampanyada uygulanır). `to` zorunlu."""
    to = (payload or {}).get("to", "").strip()
    if not to:
        raise HTTPException(status_code=400, detail="Test için e-posta adresi gerekli.")
    provider, cfg = await _selected_provider()
    if not cfg:
        raise HTTPException(status_code=400, detail="Brevo ayarları eksik/pasif ve kullanılabilir SES yedeği yok.")
    _subject = str((payload or {}).get("subject") or "").strip()
    _body = str((payload or {}).get("html") or "")
    if _subject and _body.strip():
        # Test gönderiminde de {customer_name} makul bir örnekle dolsun (ham görünmesin):
        # alıcının kayıtlı adı → yoksa "Değerli Üyemiz". {kod}/{indirim}/link ham kalır (kampanya-seviyesi).
        _cn = "Değerli Üyemiz"
        try:
            _u = await db.users.find_one({"email": to}, {"_id": 0, "first_name": 1, "name": 1})
            _cn = ((_u or {}).get("first_name") or (_u or {}).get("name") or "").strip() or "Değerli Üyemiz"
        except Exception:
            pass
        try:
            from notification_service import render_template as _render_tpl
            subject = _render_tpl(_subject, {"customer_name": _cn})
            _body = _render_tpl(_body, {"customer_name": _cn})
        except Exception:
            subject = _subject
        html = _wrap(subject, _body, unsub_url="")
    else:
        subject = f"Meluni — {provider.upper()} Test"
        html = _wrap(subject, f"<p>Bu bir <b>{provider.upper()}</b> test e-postasıdır. Bu mail size ulaştıysa pazarlama kanalı çalışıyor. 🎉</p>", unsub_url="")
    if provider == "brevo":
        r = await send_brevo_test(cfg, to, subject, html, cfg.get("reply_to") or "")
    else:
        r = await send_ses_email(cfg, to, subject, html, cfg.get("reply_to") or "")
    if not r.get("success"):
        raise HTTPException(status_code=400, detail=f"Gönderilemedi: {r.get('error') or r.get('response') or 'bilinmeyen hata'}")
    return {"success": True, "provider": provider, "message_id": r.get("message_id", "")}


@admin_router.get("/audience")
async def audience(current_user: dict = Depends(require_permission("tasarim.email"))):
    """Ticari e-posta gönderilebilecek kitle = bülten + sipariş/üyelik (İYS) + profil izni − kara liste/RET."""
    total = await db.newsletter_subscribers.count_documents({})
    a = await _campaign_audience()
    return {"total": total, "eligible": len(a["rows"]), "suppressed": a["suppressed"], "breakdown": a["breakdown"]}


@admin_router.get("/diagnose")
async def diagnose_recipient(email: str = "", current_user: dict = Depends(require_permission("tasarim.email"))):
    """Bir adrese NEDEN mail gitti/gitmedi: bülten kaydı, İYS izinleri, profil tercihi, kara liste,
    kampanya kitlesinde olup olmadığı ve son kampanya gönderim kayıtları."""
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        raise HTTPException(status_code=400, detail="Geçerli bir e-posta girin.")
    sub = await db.newsletter_subscribers.find_one({"email": em}, {"_id": 0})
    iys = await db.iys_consents.find({"email": em, "channels": "EPOSTA"},
                                     {"_id": 0, "status": 1, "source": 1, "created_at": 1, "consent_date": 1, "order_id": 1, "reported": 1}
                                     ).sort("created_at", -1).to_list(20)
    user = await db.users.find_one({"email": em}, {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "accepts_marketing": 1, "marketing_consent_at": 1})
    supp = await db.email_suppressions.find_one({"email": em}, {"_id": 0})
    a = await _campaign_audience()
    in_aud = next((r for r in a["rows"] if r["email"] == em), None)
    sends = await db.email_campaign_sends.find({"email": em}, {"_id": 0}).sort("at", -1).to_list(20)
    camp_ids = sorted({x.get("campaign_id") for x in sends if x.get("campaign_id")})
    camps = {}
    if camp_ids:
        async for c in db.email_campaigns.find({"id": {"$in": camp_ids}}, {"_id": 0, "id": 1, "subject": 1, "started_at": 1, "provider": 1}):
            camps[c["id"]] = c
    for x in sends:
        c = camps.get(x.get("campaign_id")) or {}
        x["subject"] = c.get("subject") or ""
        x["provider"] = c.get("provider") or ""
    # Alıcı logu olmayan (eski) kampanyalar — o dönemde kitle YALNIZ bülten aboneleriydi
    older = await db.email_campaigns.find({"id": {"$nin": camp_ids}, "status": {"$nin": ["queued", "sending", "syncing"]}},
                                          {"_id": 0, "id": 1, "subject": 1, "started_at": 1, "status": 1, "sent": 1, "failed": 1}
                                          ).sort("created_at", -1).to_list(10)
    if supp:
        reason = f"Kara listede ({supp.get('reason') or 'bounce/şikâyet'}) — bu adrese hiçbir kampanya gönderilmez."
    elif in_aud:
        reason = f"Kampanya kitlesinde ({in_aud['origin']} izni). Bundan sonraki kampanyalar bu adrese gider."
    elif sub and (sub.get("active") is False or not sub.get("consent")):
        reason = "Bülten aboneliği pasif / rıza yok (abonelikten çıkmış olabilir)."
    elif iys and str(iys[0].get("status") or "").upper() == "RET":
        reason = "En son İYS kararı RET — ticari e-posta gönderilmez."
    else:
        reason = "Bu adres için e-posta izni kaydı yok (bülten / ödeme sayfası / profil). Mail gönderilemez."
    return {"email": em, "in_audience": bool(in_aud), "origin": (in_aud or {}).get("origin"), "reason": reason,
            "newsletter": sub, "iys": iys, "user": user, "suppressed": supp, "sends": sends,
            "older_campaigns_without_log": older}


def _is_full_html_document(html: str) -> bool:
    """İçerik TAM BİR HTML BELGESİ mi? (kendi <head>/logo/footer'ı olan, komple sayfa).
    Büyük/küçük harf duyarsız `<!doctype` VEYA `<html` içeriyorsa tam belge sayılır."""
    low = (html or "").lower()
    return ("<!doctype" in low) or ("<html" in low)


def _inject_unsub_into_full(html: str, unsub_html: str) -> str:
    """Tam-HTML belgesine abonelikten-çık satırını `</body>`'den hemen ÖNCE enjekte eder;
    `</body>` yoksa gövde sonuna ekler. (İYS/KVKK: ticari e-postada zorunlu.)"""
    if not unsub_html:
        return html
    import re as _re
    m = _re.search(r"</body\s*>", html or "", _re.IGNORECASE)
    if m:
        return (html[:m.start()] + unsub_html + html[m.start():])
    return (html or "") + unsub_html


def _wrap(subject: str, body_html: str, unsub_url: str) -> str:
    """Kampanya HTML'ini MELUNI marka kabuğuna sarar (işlemsel maillerle aynı görünüm) +
    abonelikten-çık satırı ekler (İYS/KVKK gereği ticari e-postada zorunlu).

    ÇİFT-MELUNI FİKSİ: İçerik TAM BİR HTML BELGESİ ise (kullanıcı kendi MELUNI logo/footer'ı
    olan komple sayfayı yapıştırmış) panel kabuğu (render_email = ikinci MELUNI header+footer)
    EKLENMEZ; HTML olduğu gibi gönderilir, YALNIZ abonelikten-çık satırı </body> öncesine enjekte
    edilir. İç-gövde/parça ise eski davranış: render_email ile MELUNI kabuğuna sarılır."""
    unsub = ""
    if unsub_url:
        unsub = (f'<p style="text-align:center;font-size:11px;color:#9a9a9a;margin-top:24px">'
                 f'Bu e-postayı Meluni Kulübü üyesi olduğunuz için aldınız. '
                 f'<a href="{unsub_url}" style="color:#9a9a9a;text-decoration:underline">Abonelikten çık</a></p>')
    body = body_html or ""
    if _is_full_html_document(body):
        # Tam belge → kabuk YOK (çift MELUNI önlenir), abonelikten-çık VAR.
        return _inject_unsub_into_full(body, unsub)
    content = body + unsub
    try:
        from email_layout import render_email
        return render_email(subject, content)
    except Exception:
        return content


def _add_utm(html: str, campaign_id: str) -> str:
    """Kampanya HTML'indeki meluni.com.tr linklerine utm_source=email&utm_medium=newsletter&
    utm_campaign=<kampanya id> ekler → müşteri tıklayıp sipariş verirse order.attribution.campaign
    bu id'yi taşır (dönüşüm raporu). Abonelikten-çık / api linklerine dokunmaz."""
    import re as _re
    from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse
    if not html or not campaign_id:
        return html

    def _fix(m):
        url = m.group(2)
        try:
            u = urlparse(url)
        except Exception:
            return m.group(0)
        host = (u.hostname or "").lower()
        if not host.endswith("meluni.com.tr") or host.startswith("api.") or "/api/" in (u.path or ""):
            return m.group(0)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        if "utm_campaign" in q:
            return m.group(0)
        q.update({"utm_source": "email", "utm_medium": "newsletter", "utm_campaign": campaign_id})
        return f'{m.group(1)}{urlunparse(u._replace(query=urlencode(q)))}{m.group(3)}'
    return _re.sub(r'(href\s*=\s*["\'])(https?://[^"\']+)(["\'])', _fix, html, flags=_re.IGNORECASE)


async def _log_send(campaign_id: str, email: str, subscriber_id: str, status: str, error: str = "") -> None:
    """Alıcı başına gönderim kaydı (db.email_campaign_sends) — 'kimlere gitti' listesi + dönüşüm eşlemesi."""
    try:
        await db.email_campaign_sends.insert_one({
            "campaign_id": campaign_id, "email": (email or "").strip().lower(), "subscriber_id": subscriber_id or "",
            "status": status, "error": (error or "")[:300], "at": _now()})
    except Exception:
        pass


# ── Kampanyalar ───────────────────────────────────────────────────────────────
async def _run_ses_campaign(campaign_id: str):
    """Arka plan: rıza vermiş aktif abonelere SES ile gönderir; sayaçları günceller."""
    camp = await db.email_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        return
    cfg = await get_ses_config(db)
    if not is_configured(cfg):
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {"status": "failed", "error": "SES yapılandırılmadı", "finished_at": _now()}})
        return
    base = await _site_base()
    # Abonelikten-çık ucu BACKEND'de; vitrin alan adındaki /api yolu SPA'ya düşüyordu (link çalışmıyordu).
    from urllib.parse import quote as _q
    try:
        from abandoned_cart_mail import api_base as _api_base
        unsub_base = (await _api_base(db)) or base
    except Exception:
        unsub_base = base
    subject = camp.get("subject") or ""
    body = camp.get("html") or ""
    # MÜKERRER GÖNDERİM KORUMASI: kampanya yarıda kesilirse (deploy/yeniden başlatma)
    # baştan başlayıp HERKESE tekrar mail atılıyordu. Artık ilerleme `cursor` ile
    # (son işlenen abone id'si) saklanır; devam eden çalışma kaldığı yerden sürer.
    _resume = camp.get("cursor") or ""
    sent = int(camp.get("sent") or 0)
    failed = int(camp.get("failed") or 0)
    error_sample = camp.get("error_sample") or ""  # ilk hata mesajı (panelde 'neden' göstermek için)
    n = int(camp.get("total_processed") or 0)
    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
        "status": "sending", "started_at": camp.get("started_at") or _now()}})

    suppressed = await _suppressed_set()
    skipped = int(camp.get("skipped") or 0)

    # KİŞİSELLEŞTİRME: bildirim sistemiyle AYNI motor (render_template) → {customer_name}
    # her aboneye gerçek adıyla dolar. {kod}/{indirim}/URUN_LINKI/GORSEL_URL KAMPANYA-seviyesi
    # (admin ne yazdıysa aynen gider — bilinmeyen placeholder render_template'te KORUNUR).
    try:
        from notification_service import render_template as _render_tpl
    except Exception:
        _render_tpl = None

    # KİTLE: bülten + İYS (ödeme/üyelik) + profil izni — tek helper (_campaign_audience).
    # _aud_at: anlık görüntü zamanı — bundan SONRA gelen ret olayları parti başında yakalanır.
    _aud_at = _now()
    _aud = (await _campaign_audience())["rows"]
    # Eski biçim cursor (abone id) → e-postaya çevir; cursor artık e-posta (deterministik sıra).
    if _resume and "@" not in _resume:
        _old = await db.newsletter_subscribers.find_one({"id": _resume}, {"_id": 0, "email": 1})
        _resume = ((_old or {}).get("email") or "").strip().lower()
    # Yarıda kesilme güvencesi: bu kampanyada zaten gönderilmiş adreslere ikinci kez gitmez.
    _already = set()
    try:
        async for _x in db.email_campaign_sends.find({"campaign_id": campaign_id, "status": "sent"}, {"_id": 0, "email": 1}):
            if _x.get("email"):
                _already.add(_x["email"])
    except Exception:
        pass
    # "ALMAYANLARA GÖNDER": önceki kampanya(lar)da mail almış adresler bu kampanyada hariç.
    _excl = await _sent_emails_of(camp.get("exclude_campaign_ids") or [])
    _already |= _excl
    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
        "total": sum(1 for r in _aud if (r.get("email") or "").strip().lower() not in _excl)}})
    _pending = [r for r in _aud if (r.get("email") or "").strip()
                and not (_resume and (r.get("email") or "").strip().lower() <= _resume)]
    _late_optouts: set = set()
    for _idx, s in enumerate(_pending):
        email = (s.get("email") or "").strip()
        # PARTİ BAŞI İZİN YENİDEN KONTROLÜ: gönderim sürerken gelen çıkış/RET/bounce uygulanır.
        if _idx % _OPTOUT_RECHECK_BATCH == 0:
            _late_optouts = await _optouts_since(_pending[_idx:_idx + _OPTOUT_RECHECK_BATCH], _aud_at)
            # PRIV11: kimlik bilgisi parti başında YENİDEN okunur — gönderim sürerken anahtar
            # döndürüldüyse yenisi kullanılır; SES kapatıldı/anahtar silindiyse kampanya DURUR.
            if _idx:
                cfg = await get_ses_config(db)
                if not is_configured(cfg):
                    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
                        "status": "failed", "sent": sent, "failed": failed, "skipped": skipped,
                        "total_processed": n, "finished_at": _now(), "aborted": True,
                        "error": "SES kimlik bilgisi gönderim sırasında kapatıldı/değişti — kampanya durduruldu "
                                 "(kaldığı yerden yeniden başlatılabilir)."}})
                    logger.warning(f"[email-marketing] kampanya {campaign_id}: SES yapılandırması kalktı → durduruldu")
                    return
        # Kara listedeki adrese ASLA gönderme (SES itibarını korur).
        if email.lower() in suppressed or email.lower() in _already or email.lower() in _late_optouts:
            skipped += 1
            continue
        n += 1
        # customer_name: abone adı → yoksa users.first_name (e-posta ile) → yoksa nötr fallback.
        _name = (s.get("name") or s.get("first_name") or s.get("full_name") or "").strip()
        if not _name:
            try:
                _u = await db.users.find_one({"email": email}, {"_id": 0, "first_name": 1, "name": 1})
                _name = ((_u or {}).get("first_name") or (_u or {}).get("name") or "").strip()
            except Exception:
                _name = ""
        _cn = _name or "değerli müşterimiz"
        _subj = _render_tpl(subject, {"customer_name": _cn}) if _render_tpl else subject
        _body = _render_tpl(body, {"customer_name": _cn}) if _render_tpl else body
        unsub_url = f"{unsub_base}/api/email-marketing/unsubscribe?e={_q(email)}&t={s.get('id','')}"
        html = _add_utm(_wrap(_subj, _body, unsub_url), campaign_id)
        try:
            # SHOP10/ADM-13: alıcıya özel tek-tık çıkış (RFC 8058) → List-Unsubscribe(+Post) başlıkları.
            r = await send_ses_email(cfg, email, _subj, html, cfg.get("reply_to") or "",
                                     list_unsubscribe_url=unsub_url)
            if r.get("success"):
                sent += 1
                await _log_send(campaign_id, email, s.get("id"), "sent")
            else:
                failed += 1
                await _log_send(campaign_id, email, s.get("id"), "failed", str(r.get("error") or ""))
                if not error_sample:
                    error_sample = str(r.get("error") or "bilinmeyen hata")[:400]
        except Exception as e:
            failed += 1
            await _log_send(campaign_id, email, s.get("id"), "failed", str(e))
            if not error_sample:
                error_sample = str(e)[:400]
            logger.warning(f"[email-marketing] gönderim hata {email}: {e}")

        # ERKEN DURDURMA: ilk denemelerin TAMAMI başarısızsa (0 gönderim) config/SES sandbox
        # bozuk demektir → 679 aboneyi boşa deneyip SES itibarını yakmadan kampanyayı DURDUR ve
        # NET sebep yaz (panelde 'sending' sonsuza kadar takılı kalmasın).
        if n >= 12 and sent == 0 and failed >= n:
            _err = (error_sample or "Tüm gönderimler reddedildi")
            _lo = _err.lower()
            _sandbox = ("not verified" in _lo or "sandbox" in _lo or "messagerejected" in _lo)
            if _sandbox:
                _reason = ("AWS SES sandbox modunda: mail YALNIZ doğrulanmış adreslere gider, "
                           "doğrulanmamış aboneler reddedilir. Toplu gönderim için AWS'den SES "
                           f"production access (sandbox çıkışı) alınmalı. Örnek: {_err[:160]}")
            else:
                _reason = f"İlk {n} gönderimin tümü başarısız — gönderim durduruldu. Örnek: {_err[:160]}"
            await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
                "status": "failed", "sent": sent, "failed": failed, "skipped": skipped,
                "total_processed": n, "cursor": email.lower(), "finished_at": _now(),
                "error_sample": error_sample, "error": _reason, "aborted": True}})
            logger.warning(f"[email-marketing] kampanya {campaign_id} ERKEN DURDURULDU: {_reason}")
            return

        # cursor HER kalemde yazılır ki yarıda kesilme en fazla 1 mükerrer mail versin.
        if n % 20 == 0:
            await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
                "sent": sent, "failed": failed, "skipped": skipped, "error_sample": error_sample,
                "total_processed": n, "cursor": email.lower()}})
        else:
            await db.email_campaigns.update_one({"id": campaign_id},
                                                {"$set": {"cursor": email.lower()}})
        await asyncio.sleep(0.05)  # SES kota dostu nazik hız
    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
        "status": "sent", "sent": sent, "failed": failed, "skipped": skipped,
        "total": n, "total_processed": n, "finished_at": _now(), "error_sample": error_sample,
    }})
    logger.info(f"[email-marketing] kampanya {campaign_id} bitti: {sent} gönderildi, {failed} hata / {n}")


async def _run_brevo_campaign(campaign_id: str):
    """Rızalı yerel kitleyi Brevo listesiyle eşitler ve Marketing Campaigns API'sine yollar."""
    camp = await db.email_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        return
    cfg = await get_brevo_config(db)
    if not brevo_is_configured(cfg):
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
            "status": "failed", "error": "Brevo yapılandırılmadı", "finished_at": _now(),
        }})
        return
    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
        "status": "syncing", "started_at": camp.get("started_at") or _now(),
    }})

    suppressed = await _suppressed_set()
    rows = (await _campaign_audience())["rows"]   # bülten + İYS + profil izni (kara liste/RET hariç)
    _excl = await _sent_emails_of(camp.get("exclude_campaign_ids") or [])   # almayanlara gönder
    contacts = [row for row in rows if (row.get("email") or "").strip().lower() not in suppressed
                and (row.get("email") or "").strip().lower() not in _excl]
    sync = await sync_brevo_contacts(cfg, contacts)
    if not sync.get("success"):
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
            "status": "failed", "failed": 0, "skipped": len(rows) - len(contacts),
            "error": sync.get("error") or "Brevo kitle eşitleme başarısız",
            "error_sample": sync.get("error") or "", "finished_at": _now(),
        }})
        return

    # Eski panel placeholder'ını Brevo'nun kişi alanına dönüştür. Brevo, boş FIRSTNAME
    # için boş değer kullanır; şablonlarda nötr hitap tercih edilmesi önerilir.
    subject = str(camp.get("subject") or "").replace("{customer_name}", "{{ contact.FIRSTNAME }}")
    body = str(camp.get("html") or "").replace("{customer_name}", "{{ contact.FIRSTNAME }}")
    html = _add_utm(_wrap(subject, body, "{{ unsubscribe }}"), campaign_id)
    # Alıcı listesi (Brevo'ya iletilen kitle) — 'kimlere gitti' için yerel kayıt
    try:
        _docs = [{"campaign_id": campaign_id, "email": (c.get("email") or "").strip().lower(),
                  "subscriber_id": c.get("id") or "", "status": "submitted", "error": "", "at": _now()}
                 for c in contacts if (c.get("email") or "").strip()]
        if _docs:
            await db.email_campaign_sends.delete_many({"campaign_id": campaign_id})
            await db.email_campaign_sends.insert_many(_docs)
    except Exception:
        pass
    result = await create_and_send_campaign(
        cfg,
        name=f"Meluni · {camp.get('subject') or campaign_id} · {campaign_id[:8]}",
        subject=subject,
        html=html,
        tag=f"meluni-{campaign_id[:12]}",
    )
    if not result.get("success"):
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
            "status": "failed", "error": result.get("error") or "Brevo kampanyası başlatılamadı",
            "error_sample": result.get("error") or "", "external_campaign_id": result.get("campaign_id"),
            "synced": sync.get("synced", 0), "finished_at": _now(),
        }})
        return

    # Brevo isteği kabul edip kampanyayı kendi kuyruğuna aldığında teslimat henüz kesinleşmez.
    # Bu yüzden `sent` uydurulmaz; hedef toplam ve dış kampanya kimliği ayrı tutulur.
    await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
        "status": "submitted", "sent": 0, "failed": 0, "total": len(contacts),
        "skipped": len(rows) - len(contacts), "synced": sync.get("synced", 0),
        "external_campaign_id": result.get("campaign_id"), "finished_at": _now(),
    }})
    logger.info(f"[email-marketing] Brevo kampanya {campaign_id}, hedef {len(contacts)}, dış id {result.get('campaign_id')}")


async def _run_campaign(campaign_id: str):
    try:
        camp = await db.email_campaigns.find_one({"id": campaign_id}, {"_id": 0, "provider": 1})
        if not camp:
            return
        if camp.get("provider") == "brevo":
            await _run_brevo_campaign(campaign_id)
        else:
            await _run_ses_campaign(campaign_id)
    except HTTPException as exc:
        # In particular, never continue a send when the suppression check fails.
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
            "status": "failed", "error": str(exc.detail), "finished_at": _now(),
        }})
    except Exception:
        # A provider may have accepted a request before a timeout/DB failure.
        # Do not automatically resend through SES; that could duplicate mail.
        logger.error("[email-marketing] kampanya sonucu doğrulanamadı: %s", campaign_id)
        await db.email_campaigns.update_one({"id": campaign_id}, {"$set": {
            "status": "needs_review", "error": "Gönderim sonucu doğrulanamadı. Tekrar göndermeden önce sağlayıcı kayıtlarını kontrol edin.",
            "finished_at": _now(),
        }})


@admin_router.post("/campaigns")
async def create_campaign(payload: dict, current_user: dict = Depends(require_permission("tasarim.email"))):
    subject = (payload or {}).get("subject", "").strip()
    html = (payload or {}).get("html", "").strip()
    if not subject or not html:
        raise HTTPException(status_code=400, detail="Konu ve içerik zorunlu.")
    provider, cfg = await _selected_provider()
    if not cfg:
        raise HTTPException(status_code=400, detail="Brevo ayarları eksik/pasif ve kullanılabilir SES yedeği yok.")
    eligible = await _audience_count()
    if eligible == 0:
        raise HTTPException(status_code=400, detail="E-posta izni olan alıcı yok.")
    doc = {
        "id": generate_id(), "subject": subject, "html": html,
        "status": "queued", "provider": provider, "total": eligible, "sent": 0, "failed": 0,
        "created_by": current_user.get("email", ""), "created_at": _now(),
    }
    await db.email_campaigns.insert_one(doc)
    doc.pop("_id", None)
    # Arka planda gönder (istek beklemez)
    asyncio.create_task(_run_campaign(doc["id"]))
    return {"success": True, "campaign": doc, "eligible": eligible}


@admin_router.get("/campaigns")
async def list_campaigns(limit: int = 50, current_user: dict = Depends(require_permission("tasarim.email"))):
    limit = max(1, min(limit, 200))
    rows = await db.email_campaigns.find({}, {"_id": 0, "html": 0}).sort("created_at", -1).to_list(limit)
    return {"campaigns": rows}


async def _missing_for(campaign_id: str) -> dict:
    camp = await db.email_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    if camp.get("status") in ("queued", "sending", "syncing"):
        raise HTTPException(status_code=400, detail="Kampanya hâlâ gönderiliyor; bitince tekrar deneyin.")
    # Aynı içeriğin daha önceki 'almayanlara gönder' turları da hariç tutulur (zincir).
    chain = [campaign_id] + [c.get("id") for c in await db.email_campaigns.find(
        {"$or": [{"resend_of": campaign_id}, {"exclude_campaign_ids": campaign_id}]}, {"_id": 0, "id": 1}).to_list(50)]
    got = await _sent_emails_of(chain)
    aud = (await _campaign_audience())["rows"]
    missing = [r for r in aud if r["email"] not in got]
    return {"campaign": camp, "chain": chain, "received": len(got), "audience": len(aud), "missing": missing}


@admin_router.get("/campaigns/{campaign_id}/missing")
async def campaign_missing(campaign_id: str, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Bu kampanyayı ALMAMIŞ izinli alıcı sayısı (kitle − gönderim logu)."""
    m = await _missing_for(campaign_id)
    return {"campaign_id": campaign_id, "subject": m["campaign"].get("subject"), "audience": m["audience"],
            "received": m["received"], "missing": len(m["missing"]), "sample": [r["email"] for r in m["missing"][:10]]}


@admin_router.post("/campaigns/{campaign_id}/send-missing")
async def campaign_send_missing(campaign_id: str, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Aynı konu/içeriği YALNIZ daha önce almayan izinli alıcılara gönderir (yeni kampanya kaydı;
    önceki alıcılar gönderim loguna göre hariç). Örn. 842'ye gitti, kitle 1250 → kalan 408'e gider."""
    m = await _missing_for(campaign_id)
    if not m["missing"]:
        raise HTTPException(status_code=400, detail="Bu kampanyayı almamış izinli alıcı yok.")
    src = m["campaign"]
    if not (src.get("html") or "").strip():
        raise HTTPException(status_code=400, detail="Kaynak kampanyanın içeriği yok.")
    provider, cfg = await _selected_provider()
    if not cfg:
        raise HTTPException(status_code=400, detail="Brevo ayarları eksik/pasif ve kullanılabilir SES yedeği yok.")
    doc = {
        "id": generate_id(), "subject": src.get("subject") or "", "html": src.get("html") or "",
        "status": "queued", "provider": provider, "total": len(m["missing"]), "sent": 0, "failed": 0,
        "resend_of": campaign_id, "exclude_campaign_ids": m["chain"],
        "created_by": current_user.get("email", ""), "created_at": _now(),
    }
    await db.email_campaigns.insert_one(doc)
    doc.pop("_id", None); doc.pop("html", None)
    asyncio.create_task(_run_campaign(doc["id"]))
    return {"success": True, "campaign": doc, "missing": len(m["missing"]), "excluded": m["received"]}


# ── HAZIR KAMPANYA ŞABLONLARI (email_templates) + ÖNİZLEME ─────────────────────
# Şablonlar YALNIZ iç gövde HTML'i tutar; gönderim/önizlemede _wrap → render_email
# ile MELUNI marka kabuğuna (logo + sosyal footer + abonelikten-çık) sarılır.
# Placeholder'lar: {customer_name} (bildirim sistemiyle AYNI — per-alıcı otomatik dolar),
# {kod}/{indirim}/URUN_LINKI/GORSEL_URL (kampanya-seviyesi, admin editörde doldurur).
_BTN_STYLE = ("display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;"
              "padding:15px 44px;font-size:13px;font-weight:500;letter-spacing:1.5px;"
              "text-transform:uppercase;border-radius:2px;")
_P_STYLE = "font-size:15px;line-height:1.75;color:#4a4a4a;margin:0 0 22px;"
_HINT_STYLE = "font-size:12px;line-height:1.6;color:#9a9a9a;margin:24px 0 0;"


def _seed_body(intro_html: str, cta_text: str, extra_html: str = "") -> str:
    """Ortak iç-gövde iskeleti: kısa metin + CTA buton (link placeholder) + ipucu satırı."""
    return (
        f'<p style="{_P_STYLE}">{intro_html}</p>'
        f'{extra_html}'
        f'<a href="URUN_LINKI" style="{_BTN_STYLE}">{cta_text}</a>'
        f'<p style="{_HINT_STYLE}">↑ <b>URUN_LINKI</b> yazan yeri kendi kampanya/koleksiyon '
        f'bağlantınızla değiştirin. {{customer_name}} her aboneye adıyla OTOMATİK dolar; '
        f'{{kod}}/{{indirim}} ve GORSEL_URL kendi bilginizle doldurulur (tüm alıcılarda aynı).</p>'
    )


_IMG_PLACEHOLDER = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    'style="margin:0 0 22px;"><tr><td align="center">'
    '<img src="GORSEL_URL" alt="Görsel" width="512" '
    'style="display:block;width:100%;max-width:512px;height:auto;border:0;border-radius:2px;" />'
    '<div style="font-size:11px;color:#b8b8b8;margin-top:6px;">GORSEL_URL → kendi görsel bağlantınız</div>'
    '</td></tr></table>'
)


# Hazır (builtin) şablonlar — kadın giyim, sade/şık MELUNI sesi. builtin_key idempotent anahtar.
_SEED_EMAIL_TEMPLATES = [
    {
        # İLK KAMPANYA — markanın ilk e-posta gönderimi için önerilen (listede EN ÜSTTE).
        "builtin_key": "ilk-kampanya", "name": "İlk Kampanya / Kulübe Merhaba",
        "category": "Tanıtım", "recommended": True, "subject": "Meluni Kulübü'ne Hoş Geldiniz",
        "html": _seed_body(
            "Merhaba {customer_name},<br><br>Biz MELUNI — zamansız kesimler ve yumuşak dokularla, "
            "günlük şıklığı sade bir dille anlatan bir kadın giyim markasıyız. "
            "Meluni Kulübü'ne katıldığınız için çok mutluyuz.<br><br>"
            "Yeni koleksiyonlar, özel kampanyalar ve size özel fırsatlar ilk olarak burada. "
            "Tanışmamıza özel <b>{kod}</b> koduyla ilk alışverişinizde küçük bir hoş geldin "
            "hediyesi sizi bekliyor.",
            "Koleksiyonu Keşfet", _IMG_PLACEHOLDER),
    },
    {
        "builtin_key": "yeni-sezon", "name": "Yeni Sezon / Koleksiyon Lansmanı",
        "category": "Koleksiyon", "subject": "Yeni Sezon Geldi",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Yeni sezon parçalarımız MELUNI'de. Zamansız kesimler, "
            "yumuşak dokular ve sezonun en sevilen tonları seni bekliyor.",
            "Koleksiyonu Keşfet", _IMG_PLACEHOLDER),
    },
    {
        "builtin_key": "indirim", "name": "İndirim Kampanyası",
        "category": "İndirim", "subject": "%{indirim} İndirim Başladı",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Seçili ürünlerde <b>%{indirim} indirim</b> başladı. "
            "Sepette <b>{kod}</b> kodunu kullan, favori parçalarına şimdi sahip ol.",
            "Alışverişe Başla"),
    },
    {
        "builtin_key": "hosgeldin", "name": "Hoş Geldin / Yeni Üye",
        "category": "Üyelik", "subject": "MELUNI'ye Hoş Geldin",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Aramıza hoş geldin. İlk siparişine özel <b>{kod}</b> "
            "(HOSGELDIN) koduyla tanışma indirimini kullanabilirsin.",
            "İlk Siparişini Ver"),
    },
    {
        "builtin_key": "tekrar-stokta", "name": "Tekrar Stokta",
        "category": "Ürün", "subject": "Favorilerin Tekrar Stokta",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Beklediğin parçalar yeniden stokta. En sevilenler hızla "
            "tükeniyor — kaçırmadan incele.",
            "Şimdi İncele", _IMG_PLACEHOLDER),
    },
    {
        "builtin_key": "ozel-gun", "name": "Özel Gün (Bayram / Yılbaşı)",
        "category": "Özel Gün", "subject": "Sana Özel Kutlama İndirimi",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Bu özel günü birlikte kutlayalım. Sana özel <b>%{indirim}</b> "
            "hediye — <b>{kod}</b> koduyla kendine ya da sevdiklerine şık bir seçim yap.",
            "Kutlamaya Katıl"),
    },
    {
        "builtin_key": "vip-erken-erisim", "name": "VIP / Kulüp Erken Erişim",
        "category": "VIP", "subject": "Sana Özel Erken Erişim",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Meluni Kulübü üyelerine özel: yeni koleksiyona <b>herkesten "
            "önce</b> eriş. Sınırlı sayıda — senin için ayrı tuttuk.",
            "Erken Erişimi Aç"),
    },
    {
        "builtin_key": "sepette-unutulanlar", "name": "Sepette Unutulanlar",
        "category": "Hatırlatma", "subject": "Sepetinde Bir Şey Unuttun",
        "html": _seed_body(
            "Merhaba {customer_name},<br>Beğendiğin parçalar sepetinde seni bekliyor. Stoklar "
            "sınırlı; dilersen alışverişini şimdi tamamlayabilirsin.",
            "Sepete Dön"),
    },
]


async def seed_email_templates() -> int:
    """Hazır şablonları İDEMPOTENT ekler/günceller (builtin_key anahtar). İlk GET'te çağrılır
    → ayrı startup hook gerektirmez. BUILTIN'ler KOD-SAHİPLİ: içerik (name/category/subject/html/
    recommended) koddaki güncel seed'e EŞİTLENİR (ör. {ad}→{customer_name} güncellemesi yayılsın).
    KULLANICI kopyaları AYRI dokümanlardır (builtin!=True) — onlara DOKUNULMAZ."""
    added = 0
    for t in _SEED_EMAIL_TEMPLATES:
        key = t["builtin_key"]
        _fields = {
            "builtin": True,
            "recommended": bool(t.get("recommended")),
            "name": t["name"], "category": t.get("category", "Genel"),
            "subject": t["subject"], "html": t["html"],
            "updated_at": _now(),
        }
        existing = await db.email_templates.find_one({"builtin_key": key}, {"_id": 0, "id": 1, "html": 1, "name": 1, "subject": 1, "category": 1})
        if existing:
            # İçerik değiştiyse builtin'i güncel seed'e eşitle (kod otorite).
            if any(existing.get(k) != _fields.get(k) for k in ("html", "name", "subject", "category")):
                try:
                    await db.email_templates.update_one({"builtin_key": key}, {"$set": _fields})
                except Exception as e:
                    logger.warning(f"[email-marketing] seed şablon güncellenemedi {key}: {e}")
            continue
        doc = {"id": f"builtin:{key}", "builtin_key": key, "created_at": _now(), **_fields}
        try:
            await db.email_templates.insert_one(doc)
            added += 1
        except Exception as e:
            logger.warning(f"[email-marketing] seed şablon eklenemedi {key}: {e}")
    return added


_CONV_EXCLUDED = ["cancelled", "cancel_refunded", "payment_failed", "failed", "awaiting_payment", "pending",
                  "returned", "refunded"]


async def _campaign_report(campaign_id: str, days: int = 14) -> dict:
    """Kampanya alıcıları + dönüşüm: (a) UTM ile gelen siparişler (attribution.campaign = kampanya id),
    (b) alıcıların gönderimden sonra N gün içinde verdiği siparişler (e-posta eşleşmesi)."""
    camp = await db.email_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        raise HTTPException(status_code=404, detail="Kampanya bulunamadı")
    sends = await db.email_campaign_sends.find({"campaign_id": campaign_id}, {"_id": 0}).sort("at", 1).to_list(None)
    emails = sorted({x["email"] for x in sends if x.get("email")})
    start = camp.get("started_at") or camp.get("created_at") or ""
    end_dt = None
    try:
        from datetime import datetime as _dt, timedelta as _td
        end_dt = (_dt.fromisoformat(str(start).replace("Z", "+00:00")) + _td(days=days)).isoformat()
    except Exception:
        end_dt = ""
    utm_orders = await db.orders.find({"attribution.campaign": campaign_id, "status": {"$nin": _CONV_EXCLUDED}},
                                      {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "total": 1, "status": 1,
                                       "shipping_address.email": 1, "email": 1, "customer_email": 1}).to_list(2000)
    by_email_orders: dict = {}
    if emails and start:
        q = {"created_at": {"$gte": start}, "status": {"$nin": _CONV_EXCLUDED},
             "platform": {"$nin": ["trendyol", "hepsiburada", "amazon"]},
             "$or": [{"shipping_address.email": {"$in": emails}}, {"email": {"$in": emails}}, {"customer_email": {"$in": emails}}]}
        if end_dt:
            q["created_at"]["$lte"] = end_dt
        async for o in db.orders.find(q, {"_id": 0, "id": 1, "order_number": 1, "created_at": 1, "total": 1, "status": 1,
                                          "shipping_address.email": 1, "email": 1, "customer_email": 1, "attribution.campaign": 1}):
            em = ((o.get("shipping_address") or {}).get("email") or o.get("email") or o.get("customer_email") or "").strip().lower()
            by_email_orders.setdefault(em, []).append(o)
    rows = []
    buyers = 0
    rev = 0.0
    for x in sends:
        os_ = by_email_orders.get(x.get("email"), [])
        if os_:
            buyers += 1
            rev += sum(float(o.get("total") or 0) for o in os_)
        rows.append({"email": x.get("email"), "status": x.get("status"), "at": x.get("at"), "error": x.get("error") or "",
                     "purchased": bool(os_), "orders": [{"order_number": o.get("order_number"), "total": o.get("total"),
                                                         "created_at": o.get("created_at"), "status": o.get("status"),
                                                         "via_link": (o.get("attribution") or {}).get("campaign") == campaign_id} for o in os_]})
    utm_rev = sum(float(o.get("total") or 0) for o in utm_orders)
    n_sent = sum(1 for x in sends if x.get("status") in ("sent", "submitted"))
    return {
        "campaign": {"id": campaign_id, "subject": camp.get("subject"), "status": camp.get("status"),
                     "provider": camp.get("provider"), "created_at": camp.get("created_at"), "sent": camp.get("sent"),
                     "total": camp.get("total")},
        "window_days": days,
        "summary": {"recipients": len(sends), "delivered_or_submitted": n_sent,
                    "buyers": buyers, "buyer_orders": sum(len(v) for v in by_email_orders.values()),
                    "buyer_revenue": round(rev, 2),
                    "conversion_rate": round(100.0 * buyers / n_sent, 2) if n_sent else 0.0,
                    "via_link_orders": len(utm_orders), "via_link_revenue": round(utm_rev, 2)},
        "via_link_orders": [{"order_number": o.get("order_number"), "total": o.get("total"), "created_at": o.get("created_at"),
                             "status": o.get("status")} for o in utm_orders],
        "recipients": rows,
        "note": ("Bu kampanya için alıcı kaydı yok (alıcı başına kayıt bu tarihten sonraki kampanyalarda tutulur); "
                 "yalnız link (UTM) dönüşümleri gösterilir." if not sends else ""),
    }


@admin_router.get("/campaigns/{campaign_id}/report")
async def campaign_report(campaign_id: str, days: int = 14,
                          current_user: dict = Depends(require_permission("tasarim.email"))):
    """Kampanya raporu: kimlere gitti + kimler alışveriş yaptı (UTM link ve e-posta eşleşmesi)."""
    return await _campaign_report(campaign_id, max(1, min(days, 90)))


@admin_router.get("/campaigns/{campaign_id}/report.xlsx")
async def campaign_report_xlsx(campaign_id: str, days: int = 14,
                               current_user: dict = Depends(require_permission("tasarim.email"))):
    # ACİL ANAHTAR: freeze_exports açıkken toplu veri dışa aktarımı yapılmaz (423).
    from security_switches import require_switch_off as _sw_off, emit_security_event as _sec_emit
    await _sw_off("freeze_exports")
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from fastapi.responses import StreamingResponse
    rep = await _campaign_report(campaign_id, max(1, min(days, 90)))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Alıcılar"
    ws.append(["E-posta", "Gönderim", "Tarih", "Alışveriş yaptı", "Sipariş no", "Tutar", "Sipariş tarihi", "Linkten geldi"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="111111")
    for r in rep["recipients"]:
        if r["orders"]:
            for o in r["orders"]:
                ws.append([r["email"], r["status"], str(r["at"] or "")[:16].replace("T", " "), "Evet", o["order_number"],
                           o["total"], str(o["created_at"] or "")[:16].replace("T", " "), "Evet" if o["via_link"] else ""])
        else:
            ws.append([r["email"], r["status"], str(r["at"] or "")[:16].replace("T", " "), "", "", "", "", ""])
    for i, w in enumerate([32, 12, 17, 14, 14, 12, 17, 12], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    s2 = wb.create_sheet("Özet")
    sm = rep["summary"]
    for k, v in [("Kampanya", rep["campaign"]["subject"]), ("Alıcı", sm["recipients"]), ("Gönderilen", sm["delivered_or_submitted"]),
                 (f"Alışveriş yapan alıcı ({rep['window_days']} gün)", sm["buyers"]), ("Sipariş adedi", sm["buyer_orders"]),
                 ("Ciro (TL)", sm["buyer_revenue"]), ("Dönüşüm oranı (%)", sm["conversion_rate"]),
                 ("Maildeki linkten gelen sipariş", sm["via_link_orders"]), ("Linkten gelen ciro (TL)", sm["via_link_revenue"])]:
        s2.append([k, v])
    s2.column_dimensions["A"].width = 38
    s2.column_dimensions["B"].width = 40
    # GÜVENLİK (INJ-01): alıcı e-postası herkese açık bülten formundan gelir ('=hyperlink(..)@a.co'
    # geçerli sayılır) → formül hücresi olarak yazılmaz.
    from utils.spreadsheet_safe import neutralize_workbook
    neutralize_workbook(wb)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # Güvenlik olayı: toplu dışa aktarım (denetim izi + süper admin uyarısı; asla istisna fırlatmaz).
    await _sec_emit("email_campaign_report_export", current_user, {"export": "kampanya-raporu.xlsx", "rows": len(rep.get("recipients") or [])})
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=kampanya-raporu-{campaign_id[:8]}.xlsx",
                                      "Cache-Control": "no-store"})


@admin_router.get("/templates")
async def list_templates(current_user: dict = Depends(require_permission("tasarim.email"))):
    """Hazır (builtin) + kullanıcının kaydettiği şablonlar. Builtin'ler önce, sonra en yeni."""
    await seed_email_templates()  # idempotent: yoksa ekler
    rows = await db.email_templates.find({}, {"_id": 0}).to_list(1000)
    # Builtin'ler: önerilen (İlk Kampanya) EN ÜSTTE, sonra seed sırası; sonra kullanıcı (en yeni üstte).
    _seed_order = {t["builtin_key"]: i for i, t in enumerate(_SEED_EMAIL_TEMPLATES)}
    builtin = sorted([r for r in rows if r.get("builtin")],
                     key=lambda r: (0 if r.get("recommended") else 1,
                                    _seed_order.get(r.get("builtin_key"), 999)))
    mine = sorted([r for r in rows if not r.get("builtin")],
                  key=lambda r: r.get("created_at") or "", reverse=True)
    return {"templates": builtin + mine}


@admin_router.post("/templates")
async def save_template(payload: dict, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Kullanıcı şablonu kaydet/güncelle. Builtin'ler DEĞİŞTİRİLEMEZ — builtin id gelirse
    ya da id yoksa YENİ kullanıcı şablonu oluşturulur (kopyala-düzenle). Yalnız mevcut
    bir KULLANICI şablonunun id'si güncellenir."""
    p = payload or {}
    name = str(p.get("name") or "").strip()
    subject = str(p.get("subject") or "").strip()
    html = str(p.get("html") or "").strip()
    category = str(p.get("category") or "Genel").strip() or "Genel"
    if not name or not html:
        raise HTTPException(status_code=400, detail="Şablon adı ve içerik (HTML) zorunlu.")
    tid = str(p.get("id") or "").strip()
    now = _now()
    # Güncelleme YALNIZ mevcut bir kullanıcı (builtin olmayan) şablonu için.
    if tid and not tid.startswith("builtin:"):
        existing = await db.email_templates.find_one({"id": tid, "builtin": {"$ne": True}}, {"_id": 0})
        if existing:
            await db.email_templates.update_one({"id": tid}, {"$set": {
                "name": name, "subject": subject, "html": html, "category": category, "updated_at": now}})
            doc = await db.email_templates.find_one({"id": tid}, {"_id": 0})
            return {"success": True, "template": doc, "updated": True}
    doc = {
        "id": generate_id(), "builtin": False, "name": name, "category": category,
        "subject": subject, "html": html,
        "created_by": current_user.get("email", ""), "created_at": now, "updated_at": now,
    }
    await db.email_templates.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "template": doc, "updated": False}


@admin_router.delete("/templates/{template_id}")
async def delete_template(template_id: str, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Kullanıcı şablonunu siler. Builtin (hazır) şablonlar SİLİNEMEZ."""
    if str(template_id).startswith("builtin:"):
        raise HTTPException(status_code=400, detail="Hazır şablonlar silinemez (kopyalayıp düzenleyin).")
    doc = await db.email_templates.find_one({"id": template_id}, {"_id": 0, "builtin": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Şablon bulunamadı.")
    if doc.get("builtin"):
        raise HTTPException(status_code=400, detail="Hazır şablonlar silinemez.")
    await db.email_templates.delete_one({"id": template_id})
    return {"success": True}


@admin_router.post("/preview")
async def preview_email(payload: dict, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Verilen {subject, html} için TAM markalı e-postayı (logo + footer + abonelikten-çık)
    döndürür — gönderim/secret YOK, sadece render. Kullanıcı göndermeden önce görür."""
    p = payload or {}
    subject = str(p.get("subject") or "").strip()
    html = str(p.get("html") or "")
    # Önizlemede örnek abonelikten-çık linki (gerçek gönderimde aboneye özel üretilir).
    full = _wrap(subject, html, "https://meluni.com.tr/ornek-abonelikten-cik")
    return {"html": full, "subject": subject}


def _abs_img_url(imgs, base: str) -> str:
    """Ürünün İLK geçerli görselini MUTLAK https URL'ye çevirir. Dict {url} veya string;
    data: (base64) atlanır; '//x'→https, '/x'→site kökü. Yoksa ''."""
    for im in (imgs or []):
        u = (im.get("url") if isinstance(im, dict) else im)
        u = str(u or "").strip()
        if not u or u.startswith("data:"):
            continue
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http://"):
            return "https://" + u[len("http://"):]
        if u.startswith("https://"):
            return u
        if u.startswith("/"):
            return base + u
        return f"{base}/{u.lstrip('/')}"
    return ""


@admin_router.get("/products")
async def picker_products(category: str = "", q: str = "", limit: int = 24,
                          current_user: dict = Depends(require_permission("tasarim.email"))):
    """E-posta composer ürün seçici için MİNİMAL ürün listesi. `category` = YEREL kategori id
    (category_ids/category_id ile eşleşir). Yalnız aktif+görselli ürünler; base64 YOK — ilk
    görsel MUTLAK https, ad, fiyat, satış fiyatı, slug, ürün URL'si döner (payload hafif)."""
    try:
        limit = max(1, min(int(limit or 24), 60))
    except Exception:
        limit = 24
    query: dict = {"is_active": True, "is_deleted": {"$ne": True}, "images.0": {"$exists": True}}
    ands: list = []
    _catq = str(category or "").strip()
    if _catq:
        ands.append({"$or": [{"category_ids": _catq}, {"category_id": _catq}]})
    if str(q or "").strip():
        import re as _re
        ands.append({"name": {"$regex": _re.escape(q.strip()), "$options": "i"}})
    if ands:
        query["$and"] = ands
    rows = await db.products.find(
        query, {"_id": 0, "id": 1, "name": 1, "images": 1, "slug": 1, "price": 1, "sale_price": 1},
    ).limit(limit).to_list(limit)
    base = await _site_base()
    out = []
    for p in rows:
        img = _abs_img_url(p.get("images") or [], base)
        if not img:
            continue
        slug = p.get("slug") or p.get("id")
        out.append({
            "id": p.get("id"), "name": p.get("name") or "", "image": img,
            "slug": slug, "url": f"{base}/{slug}",
            "price": p.get("price"), "sale_price": p.get("sale_price"),
        })
    return {"products": out, "total": len(out)}


# ── BREVO ABONELİKTEN ÇIKIŞ / BOUNCE WEBHOOK ─────────────────────────────────
@public_router.post("/brevo-webhook")
async def brevo_webhook(payload: dict, request: Request):
    """Brevo pazarlama olaylarını yerel rıza/suppression verisine yansıtır."""
    import hmac as _hmac
    configured = await get_brevo_config(db)
    expected = (os.environ.get("BREVO_WEBHOOK_SECRET") or configured.get("webhook_secret") or "").strip()
    given = (request.query_params.get("key") or request.headers.get("X-Webhook-Secret") or "").strip()
    if not expected or not given or not _hmac.compare_digest(expected, given):
        raise HTTPException(status_code=403, detail="forbidden")

    event = str((payload or {}).get("event") or (payload or {}).get("msg_status") or "").lower()
    email = str((payload or {}).get("email") or "").strip().lower()
    if email and event in {"unsubscribe", "unsubscribed"}:
        await db.newsletter_subscribers.update_one({"email": email}, {"$set": {
            "active": False, "unsubscribed_at": _now(), "unsubscribed_via": "brevo",
        }})
        try:
            await db.users.update_many({"email": email}, {"$set": {"accepts_marketing": False, "marketing_unsubscribed_at": _now()}})
        except Exception:
            pass
        await _record_ret(email)
        return {"ok": True, "handled": 1, "event": event}
    if email and event in {"hard_bounce", "hardbounce", "spam", "complaint", "invalid", "blocked"}:
        reason = "complaint" if event in {"spam", "complaint"} else "bounce"
        await _suppress(email, reason, f"brevo:{event}")
        if reason == "complaint":
            await _record_ret(email)
        return {"ok": True, "handled": 1, "event": event}
    return {"ok": True, "handled": 0, "event": event}


# (Aynı yolda ikinci bir "/brevo-webhook" tanımı vardı; FastAPI yalnız İLKİNİ kullandığından ölü
#  koddu — kaldırıldı. Abonelikten çıkış / spam / kalıcı bounce yukarıdaki işleyicide.)


# ── SES BOUNCE / ŞİKÂYET BİLDİRİMİ (AWS SNS webhook) ──────────────────────────
# AWS kurulumu: SES → Configuration set → Event destination → SNS topic →
#   Subscription: HTTPS → https://api.meluni.com.tr/api/email-marketing/ses-webhook?key=<SECRET>
# SECRET: ortam değişkeni SES_WEBHOOK_SECRET ya da settings.email_ses.webhook_secret.
# GÜVENLİK fail-closed: secret yoksa/yanlışsa 403. (Aksi halde herkes sahte bounce
# gönderip abonelerinizi kara listeye attırabilirdi.)
@public_router.post("/ses-webhook")
async def ses_webhook(payload: dict, request: Request):
    import os as _os, hmac as _hmac, json as _json
    _secret = (_os.environ.get("SES_WEBHOOK_SECRET", "") or "").strip()
    if not _secret:
        _cfg = await db.settings.find_one({"id": "email_ses"}, {"_id": 0, "webhook_secret": 1}) or {}
        _secret = str(_cfg.get("webhook_secret") or "").strip()
    _given = (request.query_params.get("key") or request.headers.get("X-Webhook-Secret") or "").strip()
    if not _secret or not _given or not _hmac.compare_digest(_given, _secret):
        raise HTTPException(status_code=403, detail="forbidden")

    # Paylaşılan webhook anahtarı tek başına yeterli değildir: AWS'nin önerdiği
    # şekilde SNS mesaj imzasını doğrula. SigningCertURL doğrulanmadan ağ isteği
    # yapılmaz; böylece sertifika alanı SSRF aracı olarak kullanılamaz.
    _expected_topic = (_os.environ.get("SES_SNS_TOPIC_ARN", "") or "").strip()
    try:
        await verify_sns_message(payload or {}, expected_topic_arn=_expected_topic)
    except Exception:
        logger.warning("[email-marketing] geçersiz SNS imzası veya topic reddedildi")
        raise HTTPException(status_code=403, detail="invalid SNS message")

    _type = (payload or {}).get("Type") or ""

    # 1) Abonelik onayı: SNS ilk kurulumda SubscribeURL gönderir; GET ile onaylanır.
    if _type == "SubscriptionConfirmation":
        _url = (payload or {}).get("SubscribeURL") or ""
        if not validate_subscribe_url(_url, payload or {}):
            raise HTTPException(status_code=403, detail="invalid SNS confirmation URL")
        try:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=20, follow_redirects=False) as _c:
                _response = await _c.get(_url)
                _response.raise_for_status()
            logger.info("[email-marketing] SNS aboneliği onaylandı")
        except Exception:
            logger.warning("[email-marketing] SNS abonelik onayı başarısız")
            raise HTTPException(status_code=502, detail="SNS confirmation failed")
        return {"ok": True}

    # 2) Bildirim: Message alanı JSON string'dir.
    msg = (payload or {}).get("Message")
    if isinstance(msg, str):
        try:
            msg = _json.loads(msg)
        except Exception:
            msg = {}
    msg = msg or {}
    kind = (msg.get("notificationType") or msg.get("eventType") or "").lower()
    handled = 0

    if kind == "bounce":
        b = msg.get("bounce") or {}
        # YALNIZ kalıcı (Permanent) bounce kara listeye alınır. Geçici (Transient — kutu
        # dolu, sunucu meşgul) adresler sağlıklıdır; onları silmek kitleyi boşuna eritir.
        _perm = (b.get("bounceType") or "").lower() == "permanent"
        for r in (b.get("bouncedRecipients") or []):
            _em = (r.get("emailAddress") or "").strip()
            if not _em:
                continue
            if _perm:
                await _suppress(_em, "bounce", f"{b.get('bounceType')}/{b.get('bounceSubType')}")
                handled += 1
    elif kind == "complaint":
        c = msg.get("complaint") or {}
        for r in (c.get("complainedRecipients") or []):
            _em = (r.get("emailAddress") or "").strip()
            if not _em:
                continue
            # Şikâyet = "spam" işareti. En ağır sinyal; şartsız kara liste.
            await _suppress(_em, "complaint", c.get("complaintFeedbackType") or "")
            # Brevo şikâyetiyle SİMETRİK: şikâyet = izin geri çekildi → İYS'ye RET de yazılır.
            await _record_ret(_em.lower())
            handled += 1

    if handled:
        logger.info(f"[email-marketing] SES {kind}: {handled} adres kara listeye alındı")
    return {"ok": True, "type": kind, "handled": handled}


@admin_router.get("/suppressions")
async def list_suppressions(limit: int = 200, current_user: dict = Depends(require_permission("tasarim.email"))):
    """Kara liste (bounce/şikâyet) — hangi adrese neden gönderilmiyor."""
    limit = max(1, min(limit, 1000))
    rows = await db.email_suppressions.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    total = await db.email_suppressions.count_documents({})
    return {"total": total, "items": rows}


# ── Public: abonelikten çık ────────────────────────────────────────────────────
# GET yalnız ONAY sayfası gösterir (DEĞİŞİKLİK YAPMAZ): e-posta güvenlik tarayıcıları / link
# ön-izleyicileri bağlantıyı GET ile açtığında aboneyi kendiliğinden çıkarıyordu. Çıkış POST ile
# uygulanır: (a) sayfadaki "Abonelikten çık" butonu, (b) RFC 8058 one-click — posta istemcisi
# `List-Unsubscribe-Post: List-Unsubscribe=One-Click` başlığıyla aynı URL'e POST eder.
async def _unsub_authorized(email: str, t: str) -> bool:
    # DENETİM SEC-4 F17: token (t) ZORUNLU — eskiden yalnız e-posta ile herkes başkasını
    # abonelikten çıkarabiliyordu. Kampanya linkleri zaten &t=<id> içeriyor.
    if not (email and t):
        return False
    import hmac as _hmac
    if _hmac.compare_digest(str(t).encode("utf-8"), _unsub_token(email).encode("utf-8")):
        # Abone kaydı olmayan alıcılar (sipariş/üyelik izni) e-postaya bağlı HMAC jetonla çıkar.
        return True
    sub = await db.newsletter_subscribers.find_one({"email": email, "id": t}, {"_id": 0, "id": 1})
    return bool(sub)


async def _apply_unsubscribe(email: str) -> None:
    await db.newsletter_subscribers.update_many({"email": email}, {"$set": {
        "active": False, "unsubscribed_at": _now(),
    }})
    try:
        await db.users.update_many({"email": email}, {"$set": {"accepts_marketing": False, "marketing_unsubscribed_at": _now()}})
    except Exception:
        pass
    # İYS'ye RET bildir (best-effort) — kitle hesabında "en son karar" RET sayılır
    try:
        from .iys import record_consent
        await record_consent(recipient_email=email, recipient_phone="", channels=["EPOSTA"], status="RET", source="HS_WEB")
    except Exception:
        pass


async def _unsub_page(inner_html: str) -> HTMLResponse:
    """Beyaz etiket: logo GÖRSEL olarak (firma logo_url → site /logo.png), ad firma ayarından."""
    import html as _h
    store, logo = "", ""
    try:
        from company import get_company
        c = await get_company(db)
        store = c.get("store_name") or ""
        logo = (c.get("logo_url") or "").strip()
        if not logo and c.get("site_url"):
            logo = c["site_url"].rstrip("/") + "/logo.png"
    except Exception:
        pass
    logo_html = (f'<img src="{_h.escape(logo, quote=True)}" alt="{_h.escape(store, quote=True)}" '
                 f'style="display:block;margin:0 auto;max-height:40px;max-width:180px">') if logo else ""
    page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Abonelik</title></head>
<body style="font-family:system-ui,Arial,sans-serif;background:#fafafa;margin:0;padding:0">
<div style="max-width:460px;margin:12vh auto;background:#fff;border:1px solid #eee;border-radius:14px;padding:40px 32px;text-align:center">
{logo_html}
{inner_html}
</div></body></html>"""
    return HTMLResponse(content=page, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@public_router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(e: str = "", t: str = ""):
    """ONAY sayfası — burada hiçbir kayıt DEĞİŞMEZ (link tarayıcıları aboneyi çıkaramaz)."""
    import html as _h
    email = (e or "").strip().lower()
    if not await _unsub_authorized(email, t):
        return await _unsub_page('<p style="color:#444;margin-top:20px;line-height:1.6">'
                                 'Bağlantı geçersiz veya süresi dolmuş.</p>')
    # Boş action → form AYNI URL'e (e,t sorgu parametreleriyle) POST eder; gizli alanlar yedek.
    inner = (
        '<p style="color:#444;margin-top:20px;line-height:1.6">'
        f'<strong>{_h.escape(email)}</strong> adresine pazarlama e-postası gönderimini durdurmak '
        'istediğinizi onaylayın.</p>'
        '<form method="post" style="margin-top:24px">'
        f'<input type="hidden" name="e" value="{_h.escape(email, quote=True)}">'
        f'<input type="hidden" name="t" value="{_h.escape(t, quote=True)}">'
        '<button type="submit" style="display:inline-flex;align-items:center;justify-content:center;'
        'background:#111;color:#fff;border:0;border-radius:8px;padding:12px 28px;font-size:14px;'
        'cursor:pointer">Abonelikten çık</button></form>'
    )
    return await _unsub_page(inner)


@public_router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm(request: Request, e: str = "", t: str = ""):
    """Çıkışı UYGULAR. Kaynaklar: onay formu (e,t gövde/sorgu) veya RFC 8058 one-click POST
    (gövde `List-Unsubscribe=One-Click`, e,t URL sorgusunda)."""
    if not (e and t):
        try:
            form = await request.form()
            e = e or str(form.get("e") or "")
            t = t or str(form.get("t") or "")
        except Exception:
            pass
    email = (e or "").strip().lower()
    ok = await _unsub_authorized(email, t)
    if ok:
        await _apply_unsubscribe(email)
    msg = "Abonelikten çıkarıldınız. Artık pazarlama e-postası almayacaksınız." if ok \
        else "Kayıt bulunamadı veya bağlantı geçersiz."
    return await _unsub_page(f'<p style="color:#444;margin-top:20px;line-height:1.6">{msg}</p>')


# ═══════════════ ADM-13 / PRIV04: PAZARLAMA REDDİ YEDEKTEN DÖNÜŞTE CANLANMAZ ═══════════════
# Silinmemiş müşterinin pazarlama reddi (abonelikten çıkış, İYS RET, bounce/şikâyet kara listesi,
# profil tercihi) her gece gizli R2 arşivine yazılır: `kvkk-suppression/<YYYY-MM-DD>.json`.
# Arşivde DÜZ e-posta YOKTUR — yalnız KVKK mezar taşı anahtarlarıyla (routes.kvkk._tombstone_keys)
# alan-ayrımlı HMAC + ret zamanı + tür. Veritabanı eski bir yedekten dönerse restore-finalize
# `reapply_marketing_suppressions()`'ı çağırır: arşivdeki rette olup DB'de yeniden "izinli" görünen
# alıcı tekrar reddedilir. KULLANICI DEĞİŞİKLİĞİ KALICIDIR: ret zamanından SONRA verilmiş açık
# rıza (bülten consent_at / profil marketing_consent_at / İYS ONAY) varsa kişiye dokunulmaz.
SUPPRESSION_ARCHIVE_PREFIX = "kvkk-suppression/"
_OPTOUT_HMAC_DOMAIN = "meluni:marketing-optout:email:v1|"
_CONSENT_OPTOUT_KINDS = {"iys_ret", "newsletter_unsub", "profile_unsub"}


def _optout_hmacs(email: str) -> dict:
    import hashlib as _hl
    import hmac as _hm
    from .kvkk import _tombstone_keys
    em = (email or "").strip().lower()
    if not em:
        return {}
    msg = (_OPTOUT_HMAC_DOMAIN + em).encode()
    return {kid: _hm.new(k, msg, _hl.sha256).hexdigest() for kid, k in _tombstone_keys()}


async def _latest_iys_email() -> dict:
    """{email: (status_upper, at)} — EPOSTA kanalında alıcı başına EN SON İYS kararı."""
    latest: dict = {}
    async for r in db.iys_consents.find({"channels": "EPOSTA"},
                                        {"_id": 0, "email": 1, "status": 1, "consent_date": 1, "created_at": 1}):
        em = (r.get("email") or "").strip().lower()
        if not em:
            continue
        at = str(r.get("consent_date") or r.get("created_at") or "")
        cur = latest.get(em)
        if cur is None or at >= cur[1]:
            latest[em] = (str(r.get("status") or "ONAY").upper(), at)
    return latest


async def collect_marketing_optouts() -> dict:
    """{email: {"at": iso, "kinds": set, "reason": str}} — şu an pazarlama reddinde olan alıcılar
    (KVKK ile silinmiş üyeler hariç; onları mezar taşları korur)."""
    out: dict = {}

    def _add(em, at, kind, reason=""):
        em = (em or "").strip().lower()
        if not em or "@" not in em:
            return
        e = out.setdefault(em, {"at": "", "kinds": set(), "reason": ""})
        e["kinds"].add(kind)
        if at and (not e["at"] or str(at) > e["at"]):
            e["at"] = str(at)
        if reason and not e["reason"]:
            e["reason"] = str(reason)[:40]

    async for s in db.email_suppressions.find({}, {"_id": 0, "email": 1, "reason": 1, "created_at": 1, "updated_at": 1}):
        _add(s.get("email"), s.get("created_at") or s.get("updated_at"), "suppression", s.get("reason") or "")
    for em, (st, at) in (await _latest_iys_email()).items():
        if st == "RET":
            _add(em, at, "iys_ret")
    async for n in db.newsletter_subscribers.find(
            {"active": False, "$or": [{"unsubscribed_at": {"$nin": [None, ""]}},
                                      {"suppressed_at": {"$nin": [None, ""]}}]},
            {"_id": 0, "email": 1, "unsubscribed_at": 1, "suppressed_at": 1}):
        if n.get("unsubscribed_at"):
            _add(n.get("email"), n.get("unsubscribed_at"), "newsletter_unsub")
        else:   # yalnız bounce/şikâyet bastırması → teknik (rıza kararı değil)
            _add(n.get("email"), n.get("suppressed_at"), "newsletter_suppressed")
    async for u in db.users.find(
            {"accepts_marketing": {"$ne": True}, "marketing_unsubscribed_at": {"$nin": [None, ""]},
             "kvkk_deleted": {"$ne": True}},
            {"_id": 0, "email": 1, "marketing_unsubscribed_at": 1}):
        _add(u.get("email"), u.get("marketing_unsubscribed_at"), "profile_unsub")
    return out


def _suppression_archive_put(key: str, data: bytes) -> None:
    from services import r2_storage as r2
    r2.archive_put(key, data, "application/json")


def _suppression_archive_latest(key: str = None):
    """(key, payload_dict) — verilen ya da en yeni arşiv dosyası; yoksa (None, None)."""
    import json as _json
    from services import r2_storage as r2
    if not r2.archive_enabled():
        return None, None
    if not key:
        client = r2._get_client()
        token, keys = None, []
        while True:
            kw = {"Bucket": r2.archive_bucket(), "Prefix": SUPPRESSION_ARCHIVE_PREFIX, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**kw)
            keys += [o["Key"] for o in (resp.get("Contents") or []) if str(o.get("Key", "")).endswith(".json")]
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        if not keys:
            return None, None
        key = sorted(keys)[-1]
    data, _ = r2.archive_get(key)
    if not data:
        return key, None
    return key, _json.loads(data.decode("utf-8"))


async def archive_marketing_suppressions() -> dict:
    """GECE İŞİ: pazarlama ret listesini (HMAC'li, düz e-posta yok) gizli R2 arşivine yazar."""
    import json as _json
    from services import r2_storage as r2
    if not r2.archive_enabled():
        logger.warning("[kvkk-suppression] gizli arşiv kapalı — ret listesi yedeklenemedi")
        return {"ok": False, "error": "archive_disabled"}
    try:
        optouts = await collect_marketing_optouts()
        entries = []
        for em in sorted(optouts):
            e = optouts[em]
            h = _optout_hmacs(em)
            if not h:
                continue
            entries.append({"h": h, "at": e["at"], "k": sorted(e["kinds"]), "r": e["reason"]})
        now = datetime.now(timezone.utc)
        key = f"{SUPPRESSION_ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}.json"
        body = _json.dumps({"version": 1, "created_at": now.isoformat(), "count": len(entries),
                            "entries": entries}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await asyncio.to_thread(_suppression_archive_put, key, body)
        back, _ = await asyncio.to_thread(lambda: r2.archive_get(key))
        ok = back == body
        await db.settings.update_one({"id": "kvkk_suppression_archive"},
                                     {"$set": {"id": "kvkk_suppression_archive", "last_key": key,
                                               "last_count": len(entries), "last_at": now.isoformat(),
                                               "last_ok": ok}}, upsert=True)
        if not ok:
            logger.error("[kvkk-suppression] arşiv yazıldı ama doğrulanamadı")
        return {"ok": ok, "key": key, "count": len(entries)}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[kvkk-suppression] arşivleme hatası: {type(e).__name__}")
        return {"ok": False, "error": type(e).__name__}


async def reapply_marketing_suppressions(archive_key: str = None) -> dict:
    """YEDEKTEN DÖNÜŞ SONRASI (restore-finalize kancası): arşivdeki pazarlama retlerini yeniden uygular.

    Arşivde rette olan ama DB'de yeniden izinli görünen alıcı → profil izni kapatılır, bülten pasife
    alınır, (bounce/şikâyet ise) kara listeye geri yazılır, İYS'de en son karar ONAY görünüyorsa
    yeniden RET kaydı düşülür (günlük İYS bildirimiyle gider). Ret zamanından SONRA verilmiş açık rıza
    korunur. İdempotent; asla istisna fırlatmaz. Dönüş: sayaçlar (kişisel veri yok)."""
    stats = {"archive_key": None, "entries": 0, "matched": 0, "users_optout_reapplied": 0,
             "newsletter_reapplied": 0, "suppressions_restored": 0, "iys_ret_reapplied": 0,
             "kept_newer_optin": 0, "ok": True}
    try:
        key, payload = await asyncio.to_thread(_suppression_archive_latest, archive_key)
        stats["archive_key"] = key
        if not payload:
            stats.update(ok=False, error="archive_missing")
            return stats
        entries = list(payload.get("entries") or [])
        stats["entries"] = len(entries)
        index: dict = {}
        for e in entries:
            for kid, hx in (e.get("h") or {}).items():
                index[(kid, hx)] = e
        if not index:
            return stats
        emails: set = set()
        async for u in db.users.find({"email": {"$nin": [None, ""]}, "kvkk_deleted": {"$ne": True}},
                                     {"_id": 0, "email": 1}):
            emails.add(str(u["email"]).strip().lower())
        async for n in db.newsletter_subscribers.find({}, {"_id": 0, "email": 1}):
            if n.get("email"):
                emails.add(str(n["email"]).strip().lower())
        iys_latest = await _latest_iys_email()
        emails |= set(iys_latest)
        suppressed = {str(s.get("email") or "").strip().lower()
                      async for s in db.email_suppressions.find({}, {"_id": 0, "email": 1})}
        now_iso = datetime.now(timezone.utc).isoformat()
        for em in sorted(emails):
            entry = None
            for kid, hx in _optout_hmacs(em).items():
                entry = index.get((kid, hx))
                if entry:
                    break
            if not entry:
                continue
            stats["matched"] += 1
            opt_at = str(entry.get("at") or "")
            kinds = set(entry.get("k") or [])
            # Ret zamanından SONRA verilmiş açık rıza var mı? (kullanıcının son kararı kazanır)
            fresh = ""
            async for u in db.users.find({"email": em, "accepts_marketing": True},
                                         {"_id": 0, "marketing_consent_at": 1}):
                fresh = max(fresh, str(u.get("marketing_consent_at") or ""))
            async for n in db.newsletter_subscribers.find({"email": em, "active": {"$ne": False}, "consent": True},
                                                          {"_id": 0, "consent_at": 1}):
                fresh = max(fresh, str(n.get("consent_at") or ""))
            _st, _at = iys_latest.get(em, ("", ""))
            if _st == "ONAY":
                fresh = max(fresh, str(_at or ""))
            if fresh and opt_at and fresh > opt_at:
                stats["kept_newer_optin"] += 1
                continue
            if not (kinds & _CONSENT_OPTOUT_KINDS):
                r = None   # yalnız teknik kara liste (bounce) → rıza alanlarına dokunulmaz
            else:
                r = await db.users.update_many(
                    {"email": em, "accepts_marketing": True, "kvkk_deleted": {"$ne": True}},
                    {"$set": {"accepts_marketing": False, "marketing_unsubscribed_at": opt_at or now_iso,
                              "marketing_optout_reapplied_at": now_iso}})
                stats["users_optout_reapplied"] += int(getattr(r, "modified_count", 0) or 0)
            if kinds & _CONSENT_OPTOUT_KINDS or "suppression" in kinds:
                r = await db.newsletter_subscribers.update_many(
                    {"email": em, "active": {"$ne": False}},
                    {"$set": {"active": False, "unsubscribed_at": opt_at or now_iso,
                              "optout_reapplied_at": now_iso}})
                stats["newsletter_reapplied"] += int(getattr(r, "modified_count", 0) or 0)
            if "suppression" in kinds and em not in suppressed:
                await db.email_suppressions.update_one(
                    {"email": em},
                    {"$setOnInsert": {"email": em, "reason": entry.get("r") or "restore_reapply",
                                      "detail": "restore_reapply", "created_at": opt_at or now_iso,
                                      "updated_at": now_iso}},
                    upsert=True)
                stats["suppressions_restored"] += 1
            if (kinds & _CONSENT_OPTOUT_KINDS) and _st != "RET":
                try:
                    from .iys import record_consent
                    rec = await record_consent(recipient_email=em, recipient_phone="", channels=["EPOSTA"],
                                               status="RET", source="HS_WEB")
                    if rec.get("id"):
                        await db.iys_consents.update_one({"id": rec["id"]}, {"$set": {"restore_reapply": True}})
                    stats["iys_ret_reapplied"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[kvkk-suppression] İYS RET yeniden yazılamadı: {type(e).__name__}")
        changed = sum(stats[k] for k in ("users_optout_reapplied", "newsletter_reapplied",
                                         "suppressions_restored", "iys_ret_reapplied"))
        try:
            from .integrations_common import log_integration_event
            await log_integration_event("security", "marketing_suppression_reapply", "system", "kvkk",
                                        "success", f"Pazarlama retleri yeniden uygulandı: {changed} değişiklik",
                                        {k: v for k, v in stats.items() if k != "archive_key"})
        except Exception:
            pass
        if changed:
            logger.warning(f"[kvkk-suppression] restore sonrası yeniden uygulandı: {stats}")
        return stats
    except Exception as e:  # noqa: BLE001
        logger.error(f"[kvkk-suppression] reapply hatası: {type(e).__name__}")
        stats.update(ok=False, error=type(e).__name__)
        return stats
