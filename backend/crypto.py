
"""
JellyDJ — Credential encryption helpers.

All sensitive values stored in the database (Jellyfin API key, Lidarr API key,
Spotify/Last.fm credentials) are encrypted at rest using Fernet symmetric
encryption from the cryptography library.

Key derivation:
  The SECRET_KEY environment variable is passed through PBKDF2-HMAC-SHA256
  with 100,000 iterations to produce a 32-byte key, which is then base64-
  encoded for use with Fernet.

  A static salt is used intentionally — this isn't a password hash; the
  purpose is to derive a consistent encryption key from the secret. The
  security relies entirely on SECRET_KEY being secret, not on the salt.

  Generate a good SECRET_KEY:
    python -c "import secrets; print(secrets.token_hex(32))"

Warning:
  If you change SECRET_KEY after storing credentials, all encrypted values
  become unreadable and you will need to re-enter your API keys in the UI.
  Keep a backup of your SECRET_KEY value.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Module-level singleton — key derivation runs once, not on every call
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """
    Build (or return cached) Fernet instance from SECRET_KEY.
    Called lazily on first encrypt/decrypt call.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    # Read the secret key; fall back to a clearly-insecure dev default
    # that will work out of the box but should never be used in production
    secret = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")

    # Static salt is acceptable here — we're deriving an encryption key, not
    # hashing a password. The security comes from SECRET_KEY being kept secret.
    salt = b"jellydj-static-salt-v1"

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    """
    Encrypt a plaintext string and return a URL-safe base64 Fernet token.
    Store the returned token in the database; never store the plaintext.
    """
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """
    Decrypt a Fernet token back to plaintext.
    Raises cryptography.fernet.InvalidToken if the token is malformed or
    was encrypted with a different key (e.g. after a SECRET_KEY change).
    """
    return _get_fernet().decrypt(token.encode()).decode()
