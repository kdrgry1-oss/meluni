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
  from_email         : str  -> doğrulanmış gönderen (ör. "bulten@news.facette.com.tr")
  from_name          : str  -> gönderen adı (ör. "Facette")
  configuration_set  : str  -> (ops.) SES configuration set (açılma/bounce takibi)
"""
import asyncio


async def get_ses_config(db) -> dict:
    cfg = await db.settings.find_one({"id": "email_ses"}, {"_id": 0}) or {}
    if cfg.get("secret_key"):
        try:
            from security.crypto import decrypt as _dec
            cfg = {**cfg, "secret_key": _dec(cfg["secret_key"])}
        except Exception:
            pass
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


def _send_sync(cfg: dict, to: str, subject: str, html: str, reply_to: str = "") -> dict:
    try:
        client = _client(cfg)
        source = cfg.get("from_email")
        name = cfg.get("from_name") or "Facette"
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


async def send_ses_email(cfg: dict, to: str, subject: str, html: str, reply_to: str = "") -> dict:
    """Tek alıcıya SES ile mail (boto3 senkron → thread'e alınır, event loop bloklanmaz)."""
    return await asyncio.to_thread(_send_sync, cfg, to, subject, html, reply_to)
