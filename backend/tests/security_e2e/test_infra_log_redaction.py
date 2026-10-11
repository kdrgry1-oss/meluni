"""Log redaction (BL-019) through the REAL logging handlers set up by importing `server`.

A subprocess is used so that logging is configured exactly like production (`python -m uvicorn`
configures uvicorn's handlers, then imports server.py) and not polluted by pytest's capture handler.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiMTIzNDU2Nzg5In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
PAN_VISA = "4111111111111111"          # Luhn-valid test PAN
PAN_SPACED = "5555 5555 5555 4444"     # Luhn-valid, grouped
PAN_AMEX = "378282246310005"           # Luhn-valid 15 digits
NOT_PAN = "1234567812345678"           # 16 digits, Luhn-INVALID (order no / barcode-like)
assert sum(int(d) for d in NOT_PAN) >= 0

SCRIPT = textwrap.dedent(f"""
    import os, sys, logging, logging.config
    os.environ.update(MONGO_URL="mongodb://kv-log.invalid:1", DB_NAME="kvlog", JWT_SECRET="k" * 64)
    import motor.motor_asyncio, mongomock_motor
    motor.motor_asyncio.AsyncIOMotorClient = mongomock_motor.AsyncMongoMockClient
    sys.path.insert(0, {str(BACKEND)!r})
    MODE = sys.argv[1]
    if MODE == "uvicorn":
        import uvicorn.config
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    import server  # noqa: F401
    print("---BEGIN---", file=sys.stderr, flush=True)
    log = logging.getLogger("routes.kvtest")
    log.warning("jwt in message %s", {JWT!r})
    log.warning("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345")
    log.warning("login attempt password=Hunter2Secret! user=x")
    log.warning("cfg {{'api_key': 'sk_live_0123456789'}}")
    log.warning("card %s paid", {PAN_VISA!r})
    log.warning("card %s paid", {PAN_SPACED!r})
    log.warning("amex %s", {PAN_AMEX!r})
    log.warning("order %s barcode", {NOT_PAN!r})
    log.warning("plain line without secrets 42")
    if MODE == "uvicorn":
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET",
            "/api/auth/verify-email?token=SuperSecretTok3n99&x=1", "1.1", 200)
        logging.getLogger("uvicorn.error").error("Exception: Authorization: Bearer %s", "zyxwvutsrqponmlkjih987")
    print("---END---", file=sys.stderr, flush=True)
""")


def _run(mode: str) -> str:
    if mode == "uvicorn":
        pytest.importorskip("uvicorn")
    p = subprocess.run([sys.executable, "-c", SCRIPT, mode], capture_output=True, text=True, timeout=180,
                       cwd=str(BACKEND))
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-3000:]
    assert "---BEGIN---" in out and "---END---" in out, out[-3000:]
    return out.split("---BEGIN---", 1)[1].split("---END---", 1)[0] + p.stdout


@pytest.fixture(scope="module")
def root_out():
    return _run("root")


@pytest.fixture(scope="module")
def uvicorn_out():
    return _run("uvicorn")


@pytest.mark.parametrize("fixture", ["root_out", "uvicorn_out"])
def test_secrets_masked_on_root_handler(request, fixture):
    out = request.getfixturevalue(fixture)
    assert JWT not in out and "[JWT]" in out
    assert "abcdefghijklmnopqrstuvwxyz012345" not in out and "Bearer [REDACTED]" in out
    assert "Hunter2Secret!" not in out and "password=[REDACTED]" in out
    assert "sk_live_0123456789" not in out
    for pan in (PAN_VISA, PAN_SPACED, PAN_AMEX):
        assert pan not in out
    assert out.count("[PAN]") >= 3
    assert "Logging error" not in out


@pytest.mark.parametrize("fixture", ["root_out", "uvicorn_out"])
def test_non_luhn_16_digits_and_plain_lines_untouched(request, fixture):
    out = request.getfixturevalue(fixture)
    assert f"order {NOT_PAN} barcode" in out
    assert "plain line without secrets 42" in out


def test_uvicorn_access_and_error_lines_masked_and_still_formatted(uvicorn_out):
    out = uvicorn_out
    assert "SuperSecretTok3n99" not in out
    assert "zyxwvutsrqponmlkjih987" not in out
    # access line still rendered by uvicorn's AccessFormatter (structure kept, status text present)
    assert '"GET /api/auth/verify-email?token=[REDACTED]&x=1 HTTP/1.1" 200' in out
    assert "Logging error" not in out


def test_filter_unit_keeps_format_args_when_possible():
    import server
    f = server._SecretRedactFilter()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/x?token=abcdefgh", "1.1", 200), None)
    assert f.filter(rec) is True
    assert isinstance(rec.args, tuple) and rec.args[2] == "/x?token=[REDACTED]"
    rec2 = logging.LogRecord("x", logging.INFO, __file__, 1, "password=%s", ("Hunter2Secret!",), None)
    f.filter(rec2)
    assert "Hunter2Secret!" not in rec2.getMessage() and rec2.args == ()
