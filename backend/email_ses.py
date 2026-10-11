"""
email_ses.py — E-POSTA PAZARLAMA gönderimi Amazon SES HTTPS API'si (boto3) üzerinden.
============================================================================================
Railway giden SMTP portlarını (25/465/587) engellediğinden SES'in SMTP arayüzü DEĞİL,
HTTPS API'si (boto3 ses:SendEmail — 443) kullanılır (Zoho ZeptoMail ile aynı mantık).

ÖNEMLİ: Bu KANAL YALNIZ PAZARLAMA (toplu bülten/kampanya) içindir. İşlemsel e-postalar
(sipariş onayı, şifre vb.) ESKİSİ GİBİ Zoho ZeptoMail'den (email_smtp.py) gider — buraya
DOKUNULMAZ. Böylece pazarlama itibarı işlemsel gönderimi etkilemez (ayrı alt alan adı önerilir).

settings.id="email_ses" alanları (ayar sayfasıyla uyumlu):
  enabled            : bool -> pazarlama gönderimi aktif mi
  region             : str  -> AWS bölgesi (ör. "eu-central-1", "eu-west-1")
  access_key         : str  -> IAM access key id
  secret_key         : str  -> IAM secret (at-rest ŞİFRELİ saklanır — security.crypto)
  from_email         : str  -> doğrulanmış gönderen (ör. "bulten@news.meluni.com.tr")
  from_name          : str  -> gönderen adı (ör. "Meluni")
  configuration_set  : str  -> (ops.) SES configuration set (açılma/bounce takibi)
  unsubscribe_mailto : str  -> (ops.) List-Unsubscribe mailto adresi (ör. "unsubscribe@news.meluni.com.tr")
"""
import asyncio


async def get_ses_config(db) -> dict:
    cfg = await db.settings.find_one({"id": "email_ses"}, {"_id": 0}) or {}
    if cfg.get("secret_key"):
        try:
            from security.crypto import decrypt as _dec
            cfg = {**cfg, "secret_key": _dec(cfg["secret_key"])}
        except Exception:
            cfg = {**cfg, "secret_key": None}
    return cfg


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("enabled") and cfg.get("region") and cfg.get("access_key")
               and cfg.get("secret_key") and cfg.get("from_email"))


def _client(cfg: dict):
    import boto3
    return boto3.client(
        "ses",
        region_name=cfg.get("region"),
        aws_access_key_id=cfg.get("access_key"),
        aws_secret_access_key=cfg.get("secret_key"),
    )


def _html_to_text(html: str) -> str:
    """MIME text/plain alternatifi için kaba HTML → düz metin (spam skorunu düşürür)."""
    import re
    import html as _h
    t = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>|</li>", "\n", t)
    t = re.sub(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 (\1)", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = _h.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def _hdr_safe(v: str) -> str:
    """Başlık enjeksiyonu engeli: CR/LF ve açılı ayraç temizliği."""
    return str(v or "").replace("\r", "").replace("\n", "").replace("<", "").replace(">", "").strip()


def build_marketing_mime(cfg: dict, to: str, subject: str, html: str, reply_to: str = "",
                         list_unsubscribe_url: str = "", text: str = "") -> bytes:
    """Pazarlama e-postası için RFC 2369 + RFC 8058 başlıklı MIME (multipart/alternative: text+html).
      List-Unsubscribe: <https://…/unsubscribe?e=…&t=…>[, <mailto:…>]
      List-Unsubscribe-Post: List-Unsubscribe=One-Click
    mailto yalnız settings.email_ses.unsubscribe_mailto tanımlıysa eklenir."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr, make_msgid
    source = cfg.get("from_email") or ""
    name = cfg.get("from_name") or "Meluni"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject or "", "utf-8")
    msg["From"] = formataddr((str(Header(name, "utf-8")), source)) if name else source
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = _hdr_safe(reply_to)
    try:
        msg["Message-ID"] = make_msgid(domain=(source.split("@", 1)[1] if "@" in source else None))
    except Exception:
        pass
    parts = []
    url = _hdr_safe(list_unsubscribe_url)
    if url.lower().startswith("https://") or url.lower().startswith("http://"):
        parts.append(f"<{url}>")
    mailto = _hdr_safe(cfg.get("unsubscribe_mailto") or "")
    if mailto:
        if not mailto.lower().startswith("mailto:"):
            mailto = "mailto:" + mailto
        if "@" in mailto:
            parts.append(f"<{mailto}>")
    if parts:
        msg["List-Unsubscribe"] = ", ".join(parts)
        if url:
            # RFC 8058: tek tıkla çıkış POST'u (POST /email-marketing/unsubscribe bunu kabul eder)
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.attach(MIMEText(text or _html_to_text(html), "plain", "utf-8"))
    msg.attach(MIMEText(html or "", "html", "utf-8"))
    return msg.as_bytes()


def _send_raw_sync(cfg: dict, to: str, subject: str, html: str, reply_to: str = "",
                   list_unsubscribe_url: str = "", text: str = "") -> dict:
    try:
        client = _client(cfg)
        raw = build_marketing_mime(cfg, to, subject, html, reply_to, list_unsubscribe_url, text)
        kwargs = {"RawMessage": {"Data": raw}, "Destinations": [to]}
        if cfg.get("from_email"):
            kwargs["Source"] = cfg["from_email"]
        if cfg.get("configuration_set"):
            kwargs["ConfigurationSetName"] = cfg["configuration_set"]
        resp = client.send_raw_email(**kwargs)
        return {"success": True, "message_id": resp.get("MessageId", "")}
    except Exception as e:
        return {"success": False, "error": str(e)[:400]}


def _send_sync(cfg: dict, to: str, subject: str, html: str, reply_to: str = "") -> dict:
    try:
        client = _client(cfg)
        source = cfg.get("from_email")
        name = cfg.get("from_name") or "Meluni"
        src = f"{name} <{source}>" if name else source
        kwargs = {
            "Source": src,
            "Destination": {"ToAddresses": [to]},
            "Message": {
                "Subject": {"Data": subject or "", "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html or "", "Charset": "UTF-8"}},
            },
        }
        if cfg.get("configuration_set"):
            kwargs["ConfigurationSetName"] = cfg["configuration_set"]
        if reply_to:
            kwargs["ReplyToAddresses"] = [reply_to]
        resp = client.send_email(**kwargs)
        return {"success": True, "message_id": resp.get("MessageId", "")}
    except Exception as e:
        # botocore ClientError mesajını kısa döndür (kota/doğrulama/sandbox hataları burada görülür)
        return {"success": False, "error": str(e)[:400]}


async def send_ses_email(cfg: dict, to: str, subject: str, html: str, reply_to: str = "",
                         *, list_unsubscribe_url: str = "", text: str = "") -> dict:
    """Tek alıcıya SES ile mail (boto3 senkron → thread'e alınır, event loop bloklanmaz).

    list_unsubscribe_url verilirse (PAZARLAMA kampanyası) SES v1 send_raw_email ile MIME
    multipart/alternative (text+html) + `List-Unsubscribe` / `List-Unsubscribe-Post: One-Click`
    başlıkları gönderilir (Gmail/Yahoo toplu gönderici kuralı, RFC 8058). Verilmezse eski yol
    (send_email) — diğer çağıranlar DEĞİŞMEZ."""
    if list_unsubscribe_url:
        return await asyncio.to_thread(_send_raw_sync, cfg, to, subject, html, reply_to,
                                       list_unsubscribe_url, text)
    return await asyncio.to_thread(_send_sync, cfg, to, subject, html, reply_to)
