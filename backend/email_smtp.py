"""
email_smtp.py — E-posta gonderimi Zoho ZeptoMail HTTPS API'si (port 443) uzerinden.
Railway giden SMTP portlarini (25/465/587) engelledigi icin SMTP yerine HTTPS API kullanilir.
Fonksiyon adlari korunmustur; cagiran tum moduller degismeden calisir.

settings.id="email_smtp" alanlari (ayar sayfasiyla uyumlu):
  enabled   : bool  -> gonderim aktif mi
  username  : str   -> GONDEREN e-posta adresi (or. info@facette.com.tr)
  password  : str   -> ZeptoMail "Send Mail Token" (API anahtari)
  from_name : str   -> gonderen adi (or. FACETTE)
  host      : str   -> 'eu' iceriyorsa api.zeptomail.eu, aksi halde api.zeptomail.com
"""
import httpx


async def get_smtp_config(db) -> dict:
    cfg = await db.settings.find_one({"id": "email_smtp"}, {"_id": 0}) or {}
    # A1.1: şifre at-rest şifreli saklanır; kullanım için çöz (eski düz metin olduğu gibi geçer).
    if cfg.get("password"):
        try:
            from security.crypto import decrypt as _dec
            cfg = {**cfg, "password": _dec(cfg["password"])}
        except Exception:
            pass
    return cfg


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("enabled") and cfg.get("username") and cfg.get("password"))


def _endpoint(cfg: dict) -> str:
    # Facette hesabi Zoho EU (Avrupa) veri merkezinde → varsayilan EU endpoint.
    # Yalnizca host ACIKCA ".com" (US) ise US endpoint kullanilir. Boylece host bos/eksik
    # olsa bile EU'ya gider (US endpoint EU token'ini reddedip "mail gitmiyor"a yol aciyordu).
    host = (cfg.get("host") or "").lower()
    base = "https://api.zeptomail.com" if "com" in host else "https://api.zeptomail.eu"
    return base + "/v1.1/email"


def _auth_header(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("zoho-enczapikey"):
        return token
    return "Zoho-enczapikey " + token


async def send_smtp_email(db, to: str, subject: str, html: str,
                          from_name=None, reply_to=None, text=None, wrap=True,
                          from_email=None) -> dict:
    """Tek aliciya ZeptoMail API ile mail. Doner: {success, response}.
    wrap=True (varsayilan): HTML markali FACETTE kabuguna (logo + sosyal footer) sarilir.
    Zaten markaliysa tekrar sarilmaz (idempotent). Boylece sistemden cikan TUM mailler
    -- uyelik, sifre, s3 durum bildirimi, toplu, test -- istisnasiz ayni tasarimda gider.
    Markasiz gitmesi gereken ozel durumlar icin wrap=False gecilebilir."""
    cfg = await get_smtp_config(db)
    if not is_configured(cfg):
        return {"success": False, "response": "email_not_configured"}

    if wrap:
        try:
            # BEYAZ ETİKET: e-posta markasını (logo/site/sosyal) ayardaki firma bilgisinden uygula.
            try:
                from company import get_company
                from email_layout import set_brand as _set_brand
                _set_brand(await get_company(db))
            except Exception:
                pass  # marka uygulanamazsa FACETTE varsayılanıyla devam
            from email_layout import render_email
            html = render_email(subject, html or "")
        except Exception:
            pass  # sarma basarisiz olursa ham HTML ile devam (mail asla bloklanmaz)

    # from_email: çağrı bazında gönderen adresini geçersiz kılar (ör. sipariş mailleri
    # siparis@... adresinden gitsin diye). Boşsa varsayılan yapılandırılmış gönderen
    # (cfg.username) kullanılır → mevcut davranış korunur. ZeptoMail'de gönderilen adres
    # DOĞRULANMIŞ domaine ait olmalıdır (facette.com.tr doğrulandığı için @facette.com.tr
    # adresleri çalışır).
    sender = (str(from_email).strip() if from_email else "") or cfg.get("username")
    name = from_name or cfg.get("from_name") or "FACETTE"
    payload = {
        "from": {"address": sender, "name": name},
        "to": [{"email_address": {"address": to}}],
        "subject": subject or "",
        "htmlbody": html or "",
    }
    if text:
        payload["textbody"] = text
    if reply_to:
        payload["reply_to"] = [{"address": reply_to}]

    headers = {
        "Authorization": _auth_header(cfg.get("password")),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(_endpoint(cfg), headers=headers, json=payload)
        ok = r.status_code in (200, 201, 202)
        return {"success": ok, "response": (r.text or "")[:400]}
    except Exception as e:
        return {"success": False, "response": str(e)[:300]}
