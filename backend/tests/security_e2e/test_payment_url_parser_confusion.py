"""Ödeme dönüş/callback adresinde ayrıştırıcı farkı saldırıları (\\, @, kontrol karakteri) reddedilir."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.payment import _safe_return_base, _safe_callback_url

REQ = SimpleNamespace(base_url="https://api.meluni.com.tr/")
BAD = [
    "https://evil.example\\@www.meluni.com.tr/odeme",
    "https://www.meluni.com.tr@evil.example/odeme",
    "https://evil.example\\.meluni.com.tr/odeme",
    "https://www.meluni.com.tr/odeme\r\nLocation: https://evil.example",
    "https://www.meluni.com.tr /odeme",
]


@pytest.mark.parametrize("url", BAD)
def test_return_url_parser_confusion_falls_back_to_default(url):
    assert _safe_return_base(url, REQ) == "https://api.meluni.com.tr/odeme"


@pytest.mark.parametrize("url", BAD)
def test_callback_url_parser_confusion_rejected(url):
    with pytest.raises(HTTPException):
        _safe_callback_url(url, REQ)


def test_legit_urls_still_accepted():
    assert _safe_return_base("https://www.meluni.com.tr/odeme", REQ) == "https://www.meluni.com.tr/odeme"
    assert _safe_callback_url("https://api.meluni.com.tr/api/payment/3ds/callback", REQ).endswith("/3ds/callback")
