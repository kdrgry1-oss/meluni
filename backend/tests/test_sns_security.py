import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from security.sns import (
    canonical_sns_message,
    is_allowed_sns_url,
    validate_subscribe_url,
    verify_sns_signature_with_certificate,
)


def _confirmation_payload():
    return {
        "Type": "SubscriptionConfirmation",
        "MessageId": "message-id",
        "Token": "token-value",
        "TopicArn": "arn:aws:sns:eu-central-1:123456789012:ses-events",
        "Message": "Confirm this subscription",
        "SubscribeURL": (
            "https://sns.eu-central-1.amazonaws.com/"
            "?Action=ConfirmSubscription"
            "&TopicArn=arn%3Aaws%3Asns%3Aeu-central-1%3A123456789012%3Ases-events"
            "&Token=token-value"
        ),
        "Timestamp": "2026-09-07T12:00:00.000Z",
        "SignatureVersion": "2",
        "SigningCertURL": (
            "https://sns.eu-central-1.amazonaws.com/"
            "SimpleNotificationService-test.pem"
        ),
    }


@pytest.mark.parametrize(
    "url,certificate,expected",
    [
        ("https://sns.eu-central-1.amazonaws.com/", False, True),
        ("https://sns.us-gov-west-1.amazonaws.com/", False, True),
        ("https://sns.cn-north-1.amazonaws.com.cn/", False, True),
        ("https://sns.eu-central-1.amazonaws.com/SimpleNotificationService-a.pem", True, True),
        ("http://sns.eu-central-1.amazonaws.com/", False, False),
        ("https://sns.eu-central-1.amazonaws.com.evil.test/", False, False),
        ("https://sns.eu-central-1.amazonaws.com:444/", False, False),
        ("https://sns.eu-central-1.amazonaws.com@evil.test/", False, False),
        ("https://127.0.0.1/", False, False),
        ("https://sns.eu-central-1.amazonaws.com/other.pem", True, False),
        ("https://sns.eu-central-1.amazonaws.com/SimpleNotificationService-a.pem?x=1", True, False),
    ],
)
def test_sns_url_allowlist(url, certificate, expected):
    assert is_allowed_sns_url(url, certificate=certificate) is expected


def test_subscribe_url_is_bound_to_signed_topic_and_token():
    payload = _confirmation_payload()
    assert validate_subscribe_url(payload["SubscribeURL"], payload)
    assert not validate_subscribe_url(
        payload["SubscribeURL"].replace("Token=token-value", "Token=attacker"),
        payload,
    )


def test_signature_verification_accepts_valid_and_rejects_tampered_message():
    payload = _confirmation_payload()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    payload["Signature"] = base64.b64encode(
        key.sign(canonical_sns_message(payload), padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")
    certificate_pem = cert.public_bytes(serialization.Encoding.PEM)

    verify_sns_signature_with_certificate(payload, certificate_pem)
    payload["Message"] = "tampered"
    with pytest.raises(ValueError, match="invalid SNS signature"):
        verify_sns_signature_with_certificate(payload, certificate_pem)

