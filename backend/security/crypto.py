"""
Authenticated symmetric encryption for sensitive fields (API keys, passwords,
tokens) stored in MongoDB.

Primitive: Fernet (cryptography library) — AES-128 in CBC mode for
confidentiality + HMAC-SHA256 for integrity/authentication, with per-message
random IVs. This is NOT AES-256-GCM; do not describe it as such in any
compliance/security statement. If AES-256-GCM is ever required, switch the
primitive to `cryptography.hazmat.primitives.ciphers.aead.AESGCM` and migrate
(re-encrypt) all existing rows — the two ciphertext formats are not
interchangeable.

Master key resolution order:
  1. SECRETS_MASTER_KEY env var (preferred — 32 bytes base64url, Fernet format)
  2. Derived from JWT_SECRET via HKDF (graceful fallback so previews keep working)

NOTE: the HKDF `info` label below is the literal string "aes256-gcm-master".
It is a legacy misnomer kept ONLY because it is an input to key derivation —
changing the bytes would change the derived key and make every already-encrypted
row undecryptable. It does not reflect the actual primitive (Fernet, above).

Rotate the master key by re-encrypting each row with `rotate_secret()`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def _derive_key_from_jwt(secret: str) -> bytes:
    """HKDF-SHA256 → 32 bytes → urlsafe base64 (Fernet input)."""
    salt = b"facette-secrets-vault-v1"
    info = b"aes256-gcm-master"
    # Simple HKDF (extract+expand)
    prk = hmac.new(salt, secret.encode("utf-8"), hashlib.sha256).digest()
    okm = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(okm)


def _load_master_key() -> bytes:
    raw = os.environ.get("SECRETS_MASTER_KEY")
    if raw:
        # Expect base64url 32-byte fernet key
        try:
            decoded = base64.urlsafe_b64decode(raw)
            if len(decoded) == 32:
                return raw.encode() if isinstance(raw, str) else raw
        except Exception:
            pass
        # Allow user-provided key padded to 32 bytes
        digest = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)
    # Y12: Gömülü sabit anahtar KULLANILMAZ. JWT_SECRET'i deps ile AYNI kaynaktan
    # (ortam ya da kalıcı backend/data/.jwt_secret dosyası) türetiriz — böylece vault
    # şifre çözümü restart/worker'lar arasında tutarlı kalır.
    try:
        from routes.deps import JWT_SECRET as jwt_secret
    except Exception:
        jwt_secret = (os.environ.get("JWT_SECRET") or "").strip()
        if len(jwt_secret) < 32:
            try:
                _p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", ".jwt_secret")
                if os.path.exists(_p):
                    with open(_p) as _fh:
                        jwt_secret = _fh.read().strip()
            except Exception:
                pass
    logger.warning(
        "SECRETS_MASTER_KEY missing. Deriving from JWT_SECRET (NOT recommended for production). "
        "Generate a strong key with: python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\""
    )
    return _derive_key_from_jwt(jwt_secret)


_MASTER_KEY = _load_master_key()
_fernet = Fernet(_MASTER_KEY)


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string. Returns prefixed ciphertext "v1:<token>" or None."""
    if plaintext is None or plaintext == "":
        return plaintext
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    token = _fernet.encrypt(plaintext.encode("utf-8")).decode()
    return f"v1:{token}"


def decrypt(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a string. If value is not encrypted (legacy plaintext), returns
    the input as-is for graceful migration."""
    if ciphertext is None or ciphertext == "":
        return ciphertext
    if not isinstance(ciphertext, str):
        return ciphertext
    if not ciphertext.startswith("v1:"):
        # Legacy plaintext value — flag once and return raw
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext[3:].encode()).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt secret — wrong master key or tampered ciphertext")
        return None


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith("v1:")


def rotate_secret(ciphertext: str) -> str:
    """Re-encrypt with the current master key (used during key rotation)."""
    plain = decrypt(ciphertext)
    if plain is None:
        raise ValueError("Cannot rotate — decryption failed")
    return encrypt(plain)
