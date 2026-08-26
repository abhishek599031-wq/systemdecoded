"""Encryption for secrets stored at rest.

OAuth refresh tokens are the only durable credential this system holds, and a
refresh token is effectively a long-lived password for the connected YouTube
channel. They are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a key
that lives in the environment, never in the database — so a database dump alone
does not yield working credentials.

Nothing in this module ever logs plaintext, ciphertext or the key.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.core.errors import AppError

__all__ = ["DecryptionError", "SecretsKeyError", "decrypt", "encrypt", "generate_key"]


class SecretsKeyError(AppError):
    """SECRETS_KEY is missing or not a valid Fernet key."""

    status_code = 500
    code = "secrets_key_invalid"
    message = "SECRETS_KEY is missing or invalid; secrets cannot be encrypted."


class DecryptionError(AppError):
    """Stored ciphertext could not be decrypted.

    In practice this means SECRETS_KEY was rotated or replaced while encrypted
    rows still exist. Surfaced explicitly rather than as a generic error,
    because the fix (reconnect the account, or restore the old key) is
    specific.
    """

    status_code = 500
    code = "decryption_failed"
    message = "A stored secret could not be decrypted. Was SECRETS_KEY changed?"


def generate_key() -> str:
    """Generate a new Fernet key. Used by operators, not at runtime."""
    return Fernet.generate_key().decode()


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    key = settings.SECRETS_KEY
    if not key:
        raise SecretsKeyError(
            "SECRETS_KEY is not set. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise SecretsKeyError(
            "SECRETS_KEY is not a valid Fernet key (expected 32 url-safe "
            "base64-encoded bytes)."
        ) from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a secret for storage. Returns ciphertext bytes."""
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes | memoryview | None) -> str | None:
    """Decrypt stored ciphertext. Returns None if there was nothing stored."""
    if ciphertext is None:
        return None
    if isinstance(ciphertext, memoryview):
        ciphertext = ciphertext.tobytes()
    try:
        return _cipher().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError() from exc


def reset_cipher_cache() -> None:
    """Clear the cached cipher. For tests that swap SECRETS_KEY."""
    _cipher.cache_clear()
