"""Cloudflare R2 (S3-compatible) object storage helper.

Görseller artık MongoDB base64 yerine R2'ye yüklenir ve doğrudan R2 public
CDN URL'i üzerinden sunulur. Bu hem DB yükünü azaltır hem de yüklemeyi hızlandırır.
"""
import os
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("r2")

_client = None


def is_enabled() -> bool:
    """R2 yapılandırılmış mı?

    R2_PUBLIC_URL ŞART: yoksa public_url() relatif/bozuk bir URL üretir
    (ör. "/uploads/x.jpg") ve görsel hiçbir yerde açılmaz. Eksikse R2'yi KAPALI
    say → güvenilir DB/disk fallback'ine düş (görsel her zaman servis edilir).
    """
    return bool(
        os.environ.get("R2_ENDPOINT")
        and os.environ.get("R2_ACCESS_KEY_ID")
        and os.environ.get("R2_SECRET_ACCESS_KEY")
        and os.environ.get("R2_BUCKET")
        and (os.environ.get("R2_PUBLIC_URL") or "").strip().lower().startswith("http")
    )


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _client


def public_url(key: str) -> str:
    """Bir nesne anahtarı (key) için public CDN URL'i döndürür."""
    base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    return f"{base}/{key.lstrip('/')}"


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Veriyi R2'ye yükler, public URL döndürür."""
    client = _get_client()
    client.put_object(
        Bucket=os.environ["R2_BUCKET"],
        Key=key.lstrip("/"),
        Body=data,
        ContentType=content_type or "application/octet-stream",
        CacheControl="public, max-age=31536000, immutable",
    )
    return public_url(key)


def get_object(key: str):
    """R2'den nesneyi indirir → (bytes, content_type). Yoksa (None, None)."""
    client = _get_client()
    try:
        resp = client.get_object(Bucket=os.environ["R2_BUCKET"], Key=key.lstrip("/"))
        return resp["Body"].read(), resp.get("ContentType", "application/octet-stream")
    except (BotoCoreError, ClientError):
        return None, None


def health_check() -> dict:
    """R2 bağlantısını doğrular (head_bucket)."""
    try:
        _get_client().head_bucket(Bucket=os.environ["R2_BUCKET"])
        return {"ok": True, "bucket": os.environ["R2_BUCKET"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
