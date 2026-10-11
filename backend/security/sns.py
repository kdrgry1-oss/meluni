"""Validation helpers for inbound Amazon SNS HTTP(S) messages.

SNS message fields are attacker-controlled until the RSA signature has been
verified.  In particular, never fetch ``SigningCertURL`` or ``SubscribeURL``
before validating that the URL is an HTTPS Amazon SNS endpoint.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding


_SNS_HOST_RE = re.compile(
    r"^sns(?:-fips)?(?:\.[a-z0-9-]+)?\.amazonaws\.com(?:\.cn)?$",
    re.IGNORECASE,
)
_SNS_CERT_PATH_RE = re.compile(
    r"^/SimpleNotificationService-[A-Za-z0-9_-]+\.pem$"
)


def is_allowed_sns_url(url: str, *, certificate: bool = False) -> bool:
    """Return True only for canonical HTTPS SNS endpoints.

    Userinfo, non-default ports, fragments and certificate query strings are
    rejected to keep both certificate retrieval and subscription confirmation
    out of the generic-SSRF category.
    """

    try:
        parsed = urlsplit(str(url or ""))
        host = (parsed.hostname or "").rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not host
            or not _SNS_HOST_RE.fullmatch(host)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            return False
        if certificate:
            return bool(
                _SNS_CERT_PATH_RE.fullmatch(parsed.path)
                and not parsed.query
            )
        return parsed.path in ("", "/")
    except (TypeError, ValueError):
        return False


def validate_subscribe_url(url: str, payload: dict) -> bool:
    """Validate an SNS confirmation URL and bind it to the signed payload."""

    if not is_allowed_sns_url(url):
        return False
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        query.get("Action") == ["ConfirmSubscription"]
        and query.get("TopicArn") == [str(payload.get("TopicArn") or "")]
        and query.get("Token") == [str(payload.get("Token") or "")]
    )


def canonical_sns_message(payload: dict) -> bytes:
    """Build the exact byte sequence signed by Amazon SNS."""

    message_type = str(payload.get("Type") or "")
    if message_type == "Notification":
        fields = ["Message", "MessageId"]
        if "Subject" in payload:
            fields.append("Subject")
        fields.extend(["Timestamp", "TopicArn", "Type"])
    elif message_type in {"SubscriptionConfirmation", "UnsubscribeConfirmation"}:
        fields = [
            "Message",
            "MessageId",
            "SubscribeURL",
            "Timestamp",
            "Token",
            "TopicArn",
            "Type",
        ]
    else:
        raise ValueError("unsupported SNS message type")

    if any(field not in payload for field in fields):
        raise ValueError("incomplete SNS message")
    return "".join(f"{field}\n{payload[field]}\n" for field in fields).encode("utf-8")


def verify_sns_signature_with_certificate(payload: dict, certificate_pem: bytes) -> None:
    """Raise ValueError when the certificate or SNS signature is invalid."""

    version = str(payload.get("SignatureVersion") or "")
    if version not in {"1", "2"}:
        raise ValueError("unsupported SNS signature version")
    try:
        signature = base64.b64decode(str(payload.get("Signature") or ""), validate=True)
        certificate = x509.load_pem_x509_certificate(certificate_pem)
        digest = hashes.SHA1() if version == "1" else hashes.SHA256()
        certificate.public_key().verify(
            signature,
            canonical_sns_message(payload),
            padding.PKCS1v15(),
            digest,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalid SNS signature") from exc


async def verify_sns_message(payload: dict, *, expected_topic_arn: str = "") -> None:
    """Fetch the allowlisted signing certificate and verify an SNS message."""

    topic_arn = str(payload.get("TopicArn") or "")
    if expected_topic_arn and topic_arn != expected_topic_arn:
        raise ValueError("unexpected SNS topic")

    certificate_url = str(payload.get("SigningCertURL") or "")
    if not is_allowed_sns_url(certificate_url, certificate=True):
        raise ValueError("invalid SNS signing certificate URL")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        follow_redirects=False,
    ) as client:
        response = await client.get(certificate_url)
        response.raise_for_status()
        if len(response.content) > 64 * 1024:
            raise ValueError("SNS signing certificate is too large")
        verify_sns_signature_with_certificate(payload, response.content)

