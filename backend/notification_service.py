"""
=============================================================================
notification_service.py — Çok kanallı (SMS + Mail + WhatsApp) bildirim servisi
=============================================================================

AMAÇ:
  Siparişin durum değişiklikleri, şifre sıfırlama OTP, kargo güncellemeleri,
  sepette ürün kaldı vb. event'leri tek bir yerden, ayarlanabilir şablonlarla
  müşteriye ulaştıran ortak servis.

Kanallar:
  - SMS  : Netgsm, İletiMerkezi, Twilio, Vatansms, Verimor, Mutlucep,
           Mobildev, Postagüvercini (kargo gibi çoklu — sadece birisi aktif)
  - WhatsApp : Meta Cloud API (Business phone number)
  - E-posta : Resend (mevcut altyapıyı kullanır)

Events (template keys):
  order_confirmed, order_packed, order_shipped, order_delivered,
  order_undelivered, order_cancelled, password_reset_otp,
  abandoned_cart, stock_alert, admin_new_order

Şablon içinde değişkenler {variable} ile replace edilir:
  {customer_name}, {order_number}, {amount}, {tracking_number}, {otp_code}

Her provider için credential'lar MongoDB `settings` koleksiyonunda:
  settings.id = "notification_providers"  → {sms_active, whatsapp_active, providers: {...}}

Template'ler MongoDB `notification_templates` koleksiyonunda.
=============================================================================
"""
from __future__ import annotations
import asyncio
import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

# ---------- Sabit liste: SMS sağlayıcıları ----------
SMS_PROVIDERS = [
    {"key": "netgsm", "name": "Netgsm"},
    {"key": "iletimerkezi", "name": "İleti Merkezi"},
    {"key": "twilio", "name": "Twilio"},
    {"key": "vatansms", "name": "VatanSMS"},
    {"key": "verimor", "name": "Verimor"},
    {"key": "mutlucep", "name": "MutluCep"},
    {"key": "mobildev", "name": "Mobildev"},
    {"key": "postagüvercini", "name": "Posta Güvercini"},
]

DEFAULT_EVENTS = [
    {"key": "order_confirmed", "name": "Sipariş Onaylandı"},
    {"key": "order_packed", "name": "Sipariş Paketleniyor"},
    {"key": "order_shipped", "name": "Sipariş Kargoya Verildi"},
    {"key": "order_delivered", "name": "Sipariş Teslim Edildi"},
    {"key": "order_undelivered", "name": "Teslim Edilemedi (Şubede Bekliyor)"},
    {"key": "order_cancelled", "name": "Sipariş İptal Edildi"},
    {"key": "order_awaiting_payment", "name": "Siparişiniz Alındı · Ödeme Bekleniyor (Havale)"},
    {"key": "order_payment_reminder", "name": "Ödeme Hatırlatma (Havale · elle gönder)"},
    {"key": "order_payment_notified", "name": "Ödeme Bildirimi Alındı"},
    {"key": "order_pending", "name": "Sipariş Alındı (Onay Bekliyor)"},
    {"key": "order_preparing", "name": "Sipariş Hazırlanıyor"},
    {"key": "order_ready_to_ship", "name": "Kargoya Hazır"},
    {"key": "order_in_transit", "name": "Kargo Taşınıyor"},
    {"key": "order_out_for_delivery", "name": "Dağıtımda"},
    {"key": "order_return_requested", "name": "İade Talebi Oluşturuldu"},
    {"key": "order_return_in_transit", "name": "İade Kargoda"},
    {"key": "order_returned", "name": "İade Tamamlandı"},
    {"key": "order_refunded", "name": "İade Bedeli Ödendi"},
    {"key": "password_reset_otp", "name": "Şifre Sıfırlama Kodu"},
    {"key": "abandoned_cart", "name": "Sepette Ürün Kaldı"},
    {"key": "wishlist_back_in_stock", "name": "Favori Ürün Tekrar Stokta"},
    {"key": "welcome", "name": "Hoş Geldin (Üyelik)"},
    {"key": "stock_alert", "name": "Stok Uyarısı (Admin)"},
    {"key": "admin_new_order", "name": "Yeni Sipariş (Admin)"},
]

CHANNELS = ["sms", "email", "whatsapp"]


def render_template(text: str, variables: Dict[str, Any]) -> str:
    """{variable} formatındaki placeholder'ları doldurur."""
    if not text:
        return ""
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{([a-zA-Z0-9_]+)\}", repl, text)


def normalize_phone_tr(phone: str) -> str:
    """TR telefon numaralarını uluslararası formata çevirir (905xxxxxxxxx).
    Giriş: '0 555 123 45 67' / '5551234567' / '+905551234567' / '905551234567'
    Çıkış: '905551234567'
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("90"):
        return digits
    if digits.startswith("0"):
        return "90" + digits[1:]
    if len(digits) == 10:
        return "90" + digits
    return digits


# =============================================================================
# SMS PROVIDERS
# =============================================================================

async def _sms_netgsm(cfg: Dict, to: str, message: str) -> Dict:
    """Netgsm HTTP GET API — https://api.netgsm.com.tr/sms/send/get

    Gerekli cfg alanları:
      - username : Netgsm abone numarası VEYA tanımlı API alt kullanıcı adı
      - password : API (alt) kullanıcı şifresi
      - header   : Netgsm panelinde ONAYLI gönderici adı (Originator)
    Opsiyonel cfg:
      - dil       : "TR" → Türkçe karakter destekli gönderim (varsayılan "TR")
      - appkey    : API alt kullanıcıya tanımlı uygulama anahtarı (varsa)
      - iysfilter : İYS filtresi (ticari ileti için; işlemsel SMS'te boş bırakılır)
    """
    gsmno = normalize_phone_tr(to)  # 905XXXXXXXXX
    url = "https://api.netgsm.com.tr/sms/send/get"
    params = {
        "usercode": cfg.get("username", ""),
        "password": cfg.get("password", ""),
        "gsmno": gsmno,
        "message": message,
        "msgheader": cfg.get("header", ""),
        "dil": (cfg.get("dil") or "TR"),  # Türkçe karakter desteği (ç,ş,ğ,ı,ö,ü)
    }
    if cfg.get("appkey"):
        params["appkey"] = cfg.get("appkey")
    if cfg.get("iysfilter"):
        params["iysfilter"] = cfg.get("iysfilter")

    err_map = {
        "20": "Mesaj metni hatalı ya da karakter sınırı aşıldı.",
        "30": "Geçersiz kullanıcı adı/şifre, API erişim izni yok veya IP kısıtlaması var.",
        "40": "Gönderici adı (başlık) sistemde tanımlı/onaylı değil.",
        "50": "Hesap İYS kontrollü; alıcının izni bulunmuyor.",
        "51": "Gönderici adı İYS marka eşleşmesi yapılmamış.",
        "70": "Hatalı veya eksik parametre.",
        "80": "Gönderim sınırı aşıldı.",
        "85": "Aynı numaraya 1 dakika içinde mükerrer gönderim sınırı.",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        text = (r.text or "").strip()
    # Netgsm GET yanıtı: başarı = "00 JOBID" (tek başarı kodu 00'dır)
    parts = text.split()
    code = parts[0] if parts else ""
    ok = code == "00"
    result = {"success": ok, "response": text}
    if ok:
        result["job_id"] = parts[1] if len(parts) > 1 else ""
    else:
        result["error"] = err_map.get(code, f"Beklenmeyen Netgsm yanıtı: {text or '(boş)'}")
    return result


async def _sms_iletimerkezi(cfg: Dict, to: str, message: str) -> Dict:
    """İletiMerkezi v1 JSON API."""
    url = "https://api.iletimerkezi.com/v1/send-sms/json"
    body = {
        "request": {
            "authentication": {"key": cfg.get("api_key", ""), "hash": cfg.get("api_hash", "")},
            "order": {
                "sender": cfg.get("header", ""),
                "message": {"text": message, "receipents": {"number": [to]}},
            },
        }
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=body)
        ok = r.status_code == 200 and "200" in r.text
        return {"success": ok, "response": r.text[:500]}


async def _sms_twilio(cfg: Dict, to: str, message: str) -> Dict:
    """Twilio REST API."""
    sid = cfg.get("account_sid", "")
    token = cfg.get("auth_token", "")
    from_ = cfg.get("from_number", "")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    auth = (sid, token)
    data = {"From": from_, "To": "+" + to if not to.startswith("+") else to, "Body": message}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=data, auth=auth)
        return {"success": r.status_code in (200, 201), "response": r.text[:500]}


async def _sms_vatansms(cfg: Dict, to: str, message: str) -> Dict:
    """VatanSMS JSON API."""
    url = "https://api.vatansms.net/api/v1/1toN"
    body = {
        "api_id": cfg.get("api_id", ""),
        "api_key": cfg.get("api_key", ""),
        "sender": cfg.get("header", ""),
        "message_type": "normal",
        "message": message,
        "phones": [to],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=body)
        ok = r.status_code == 200 and "status" in r.text.lower()
        return {"success": ok, "response": r.text[:500]}


async def _sms_generic(cfg: Dict, to: str, message: str) -> Dict:
    """Henüz bağlanmamış sağlayıcılar için placeholder (Verimor, MutluCep, Mobildev, PostaGüvercini).
    Y19: Önceden success=True dönüyordu → SMS hiç gitmediği halde notification_logs 'başarılı'
    kaydediyordu (sessiz kesinti). Artık success=False döner ki gerçek durum görünür olsun."""
    logger.warning(f"[SMS] Sağlayıcı henüz uygulanmadı — SMS GÖNDERİLMEDİ, to=***{str(to)[-4:]}")  # A3: telefon+OTP loglanmaz
    return {"success": False, "response": "provider-not-implemented", "mock": True,
            "error": "SMS sağlayıcısı entegre değil"}


SMS_IMPL = {
    "netgsm": _sms_netgsm,
    "iletimerkezi": _sms_iletimerkezi,
    "twilio": _sms_twilio,
    "vatansms": _sms_vatansms,
}


# =============================================================================
# WHATSAPP (META CLOUD API)
# =============================================================================

async def _whatsapp_send(cfg: Dict, to: str, message: str, template_name: Optional[str] = None,
                         template_params: Optional[List[str]] = None, lang: str = "tr") -> Dict:
    """Meta WhatsApp Cloud API — text (24-h window) veya template message.
    Credential: phone_number_id, access_token.
    """
    pnid = cfg.get("phone_number_id", "")
    token = cfg.get("access_token", "")
    api_ver = cfg.get("api_version", "v23.0")
    if not pnid or not token:
        return {"success": False, "response": "credentials_missing"}
    url = f"https://graph.facebook.com/{api_ver}/{pnid}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if template_name:
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang},
            },
        }
        if template_params:
            payload["template"]["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in template_params]
            }]
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        return {"success": r.status_code in (200, 201), "response": r.text[:700]}


# =============================================================================
# EMAIL (RESEND)
# =============================================================================

async def _email_send(db, to: str, subject: str, html: str,
                      from_email: Optional[str] = None,
                      from_name: Optional[str] = None,
                      reply_to: Optional[str] = None) -> Dict:
    """Kurumsal SMTP (Zoho) üzerinden tek alıcıya mail.
    from_email verilirse gönderen adresi geçersiz kılınır (ör. sipariş mailleri
    siparis@... adresinden). Boşsa varsayılan yapılandırılmış gönderen kullanılır."""
    from email_smtp import send_smtp_email
    return await send_smtp_email(db, to, subject, html,
                                 from_email=from_email, from_name=from_name,
                                 reply_to=reply_to)


async def _order_sender(db, event: str) -> Dict:
    """Sipariş yaşam-döngüsü maillerinin (order_*) gönderen kimliğini çözer.
    Panelden (Ayarlar → E-posta) ayarlanan `order_from_email` doluysa sipariş
    mailleri o adresten gider (ör. siparis@facette.com.tr); pazarlama/işlemsel
    itibar ayrımı korunur. Boşsa varsayılan gönderen kullanılır (davranış değişmez).
    Yalnız `order_` ile başlayan MÜŞTERİ olayları için; admin/şifre/pazarlama hariç."""
    if not str(event or "").startswith("order_"):
        return {}
    try:
        s = await db.settings.find_one(
            {"id": "email_smtp"},
            {"_id": 0, "order_from_email": 1, "order_from_name": 1, "order_reply_to": 1}) or {}
    except Exception:
        return {}
    oe = str(s.get("order_from_email") or "").strip()
    if not oe:
        return {}
    return {
        "from_email": oe,
        "from_name": (str(s.get("order_from_name") or "").strip() or None),
        "reply_to": (str(s.get("order_reply_to") or "").strip() or None),
    }


# =============================================================================
# ORCHESTRATION
# =============================================================================

async def _get_providers_config(db) -> Dict:
    return await db.settings.find_one({"id": "notification_providers"}, {"_id": 0}) or {
        "sms_active": None, "whatsapp_active": False, "email_active": True,
        "providers": {},
    }


async def sms_available(db) -> bool:
    """Gerçekten SMS gönderebiliyor muyuz?

    `sms_active` boşsa ya da bilinmeyen bir sağlayıcıysa `_sms_generic` devreye
    girer ve `{"success": False, "mock": True}` döner — yani çağrı İSTİSNA
    ATMAZ ama SMS asla gitmez. Bu sessiz mock, OTP akışlarında kullanıcıyı
    "kodu girin" ekranında sonsuza kadar bekletir.

    Çağıran taraf bu fonksiyonla durumu ÖNCEDEN görüp akışı değiştirebilir
    (örn. MFA'yı atlayıp normal girişe izin vermek).
    """
    try:
        cfg = await _get_providers_config(db)
    except Exception:
        return False
    active = cfg.get("sms_active")
    if not active or active not in SMS_IMPL:
        return False
    # Sağlayıcı seçili ama kimlik bilgileri girilmemişse de gönderemeyiz.
    return bool((cfg.get("providers") or {}).get(active))


async def _get_template(db, event_key: str, channel: str) -> Optional[Dict]:
    return await db.notification_templates.find_one(
        {"event": event_key, "channel": channel}, {"_id": 0}
    )


async def _log_event(db, *, event: str, channel: str, to: str, status: str,
                     response: str = "", variables: Optional[Dict] = None):
    try:
        await db.notification_logs.insert_one({
            "event": event,
            "channel": channel,
            "to": to,
            "status": status,
            "response": response[:1000] if response else "",
            "variables": variables or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"notification log insert failed: {e}")


async def send_notification(
    db,
    event: str,
    *,
    to_phone: Optional[str] = None,
    to_email: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    channels: Optional[List[str]] = None,
) -> Dict:
    """
    Tek çağrı → uygun kanallara event'i tetikler.
    channels verilmezse tüm aktif kanallar kullanılır.
    Template'ler DB'den çekilir. Bulunamazsa default kısa metin.
    """
    variables = dict(variables or {})
    results: Dict[str, Dict] = {}

    # Marka değişkenlerini her şablona otomatik ver. Böylece SMS/e-posta
    # metinlerinde marka adı SABİT yazılmak zorunda kalmaz; {store_name}
    # yazmak yeterli olur ve yeni markada kendiliğinden doğru gelir.
    # Çağıranın açıkça verdiği değer kazanır (setdefault).
    try:
        import company as _company
        _c = await _company.get_company(db)
        for _k in ("store_name", "contact_email", "contact_phone", "site_url", "website", "whatsapp"):
            if _c.get(_k):
                variables.setdefault(_k, _c[_k])
    except Exception:
        # Marka bilgisi okunamazsa bildirim yine de gitsin.
        pass

    cfg = await _get_providers_config(db)
    providers = cfg.get("providers", {})

    active_channels = channels or CHANNELS

    # GÜVENLİK-KRİTİK event'ler (OTP / MFA / şifre sıfırlama kodu) admin'in bildirim
    # AÇ/KAPA toggle'ından BAĞIMSIZ her zaman gönderilir. Aksi halde şablon yanlışlıkla
    # pasif bırakılınca giriş doğrulama kodu / şifre sıfırlama kodu HİÇ gitmiyordu
    # ("doğrulama kodu gitmiyor" kök nedeni). Şablon metni yine düzenlenebilir.
    _CRITICAL_EVENTS = {"password_reset_otp"}
    def _tpl_on(_tpl) -> bool:
        return bool(_tpl) and (_tpl.get("enabled", True) or event in _CRITICAL_EVENTS)

    # --- SMS ---
    if "sms" in active_channels and to_phone:
        to = normalize_phone_tr(to_phone)
        tpl = await _get_template(db, event, "sms")
        if _tpl_on(tpl):
            msg = render_template(tpl.get("body", ""), variables) or f"[{event}]"
            sms_active = cfg.get("sms_active")
            impl = SMS_IMPL.get(sms_active, _sms_generic)
            prov_cfg = providers.get(sms_active, {}) if sms_active else {}
            try:
                res = await impl(prov_cfg, to, msg)
                results["sms"] = res
                await _log_event(db, event=event, channel="sms", to=to,
                                 status="success" if res.get("success") else "failed",
                                 response=str(res.get("response", "")), variables=variables)
            except Exception as e:
                results["sms"] = {"success": False, "response": str(e)}
                await _log_event(db, event=event, channel="sms", to=to, status="error",
                                 response=str(e), variables=variables)
        else:
            # H1 fix: durum ayarı "SMS gönder" dedi ama bu event için ŞABLON YOK / PASİF →
            # eskiden iz bırakmadan atlanıyordu (admin gitti sanıyordu). Artık "skipped" loglanır.
            results["sms"] = {"success": False, "response": "sms-template-missing-or-disabled"}
            await _log_event(db, event=event, channel="sms", to=to, status="skipped",
                             response="şablon yok veya pasif (event=%s)" % event, variables=variables)

    # --- WhatsApp ---
    if "whatsapp" in active_channels and to_phone and cfg.get("whatsapp_active"):
        to = normalize_phone_tr(to_phone)
        tpl = await _get_template(db, event, "whatsapp")
        if _tpl_on(tpl):
            msg = render_template(tpl.get("body", ""), variables)
            wa_cfg = providers.get("whatsapp_meta", {})
            try:
                res = await _whatsapp_send(
                    wa_cfg, to, msg,
                    template_name=tpl.get("meta_template_name") or None,
                    template_params=[render_template(p, variables) for p in (tpl.get("meta_template_params") or [])],
                    lang=tpl.get("meta_template_lang", "tr"),
                )
                results["whatsapp"] = res
                await _log_event(db, event=event, channel="whatsapp", to=to,
                                 status="success" if res.get("success") else "failed",
                                 response=str(res.get("response", "")), variables=variables)
            except Exception as e:
                results["whatsapp"] = {"success": False, "response": str(e)}
                await _log_event(db, event=event, channel="whatsapp", to=to, status="error",
                                 response=str(e), variables=variables)

    # --- Email ---
    if "email" in active_channels and to_email and cfg.get("email_active", True):
        tpl = await _get_template(db, event, "email")
        if _tpl_on(tpl):
            subj = render_template(tpl.get("subject", ""), variables) or f"Bildirim: {event}"
            html = render_template(tpl.get("body", ""), variables) or f"<p>{event}</p>"
            try:
                _snd = await _order_sender(db, event)
                res = await _email_send(db, to_email, subj, html, **_snd)
                results["email"] = res
                await _log_event(db, event=event, channel="email", to=to_email,
                                 status="success" if res.get("success") else "failed",
                                 response=str(res.get("response", "")), variables=variables)
            except Exception as e:
                results["email"] = {"success": False, "response": str(e)}
                await _log_event(db, event=event, channel="email", to=to_email, status="error",
                                 response=str(e), variables=variables)

    return {"event": event, "results": results}


# =============================================================================
# TEST HELPERS
# =============================================================================

async def test_provider(db, channel: str, provider_key: Optional[str], to: str, message: str) -> Dict:
    cfg = await _get_providers_config(db)
    providers = cfg.get("providers", {})
    prov_cfg = providers.get(provider_key or "", {}) if provider_key else {}
    if channel == "sms":
        impl = SMS_IMPL.get(provider_key or "", _sms_generic)
        return await impl(prov_cfg, normalize_phone_tr(to), message)
    if channel == "whatsapp":
        return await _whatsapp_send(providers.get("whatsapp_meta", {}), normalize_phone_tr(to), message)
    if channel == "email":
        return await _email_send(db, to, "Test Bildirim", f"<p>{message}</p>")
    return {"success": False, "response": "unknown_channel"}
