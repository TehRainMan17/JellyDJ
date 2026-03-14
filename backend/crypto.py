"""
JellyDJ — Credential encryption helpers.

All sensitive values stored in the database (Jellyfin API key, Lidarr API key,
Spotify/Last.fm credentials) are encrypted at rest using Fernet symmetric
encryption from the cryptography library.

Key derivation:
  The SECRET_KEY environment variable is passed through PBKDF2-HMAC-SHA256
  with 100,000 iterations to produce a 32-byte key, which is then base64-
  encoded for use with Fernet.

  The PBKDF2 salt is derived from SECRET_KEY itself via HMAC-SHA256, making
  it unique per installation without needing a separate stored value.  This
  is safe — the only thing the salt needs to be is unpredictable to an
  attacker, which it is when SECRET_KEY is strong and secret.  Unlike a
  static public salt in the repository, a SECRET_KEY-derived salt cannot be
  used to precompute attack tables across installations.

  Generate a good SECRET_KEY:
    python -c "import secrets; print(secrets.token_hex(32))"

Migration note — upgrading from the static-salt version:
  The key derivation function changed (static public salt → per-installation
  derived salt).  After upgrading, you must re-enter all API keys in the UI
  (Connections page for Jellyfin/Lidarr, Settings for Spotify/Last.fm).  The
  old encrypted values in the database will fail to decrypt and the app will
  prompt you to re-configure the affected connections.

Warning:
  If you change SECRET_KEY after storing credentials, all encrypted values
  become unreadable and you will need to re-enter your API keys in the UI.
  Keep a backup of your SECRET_KEY value.
"""

import hashlib
import hmac
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Module-level singleton — key derivation runs once, not on every call
_fernet: Fernet | None = None


# Known-insecure default values — used only for local dev, never production.
# Listed here so _get_fernet() can detect and reject them at runtime.
_INSECURE_DEFAULTS = frozenset({
    "dev-insecure-secret-change-me",
    "change-me-generate-a-real-secret",
    "",
})


def _get_fernet() -> Fernet:
    """
    Build (or return cached) Fernet instance from SECRET_KEY.
    Called lazily on first encrypt/decrypt call.

    Raises RuntimeError if SECRET_KEY is unset or matches a known insecure
    default — the application must not start with a default encryption key
    because all stored API credentials would be encrypted with a publicly
    known key value.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    secret = os.getenv("SECRET_KEY", "").strip()

    if not secret or secret in _INSECURE_DEFAULTS:
        raise RuntimeError(
            "SECRET_KEY is not set or uses an insecure default value. "
            "All stored credentials (Jellyfin, Lidarr, Spotify, Last.fm API keys) "
            "are encrypted with this key — using a default makes them trivially "
            "decryptable by anyone who has read the source code. "
            "Generate a strong key and set it in .env:\n"
            "  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then set: SECRET_KEY=<generated value>"
        )

    # Derive a per-installation salt from the secret key itself.
    # This replaces the previous static public salt (b"jellydj-static-salt-v1").
    #
    # A static salt in a public repository is a significant weakness: an attacker
    # who obtains the database can precompute a rainbow table for all plausible
    # SECRET_KEY values against the known salt, then test the table against every
    # JellyDJ installation at once.  A secret-derived salt prevents this — the
    # salt is unique per installation and unknown to an attacker who does not
    # already know SECRET_KEY (at which point the game is already over).
    #
    # Using HMAC-SHA256 with a fixed label produces a deterministic, stable salt
    # from the secret, so the derived Fernet key is the same on every startup
    # for a given SECRET_KEY — existing encrypted values continue to decrypt.
    salt = hmac.new(
        secret.encode(),
        b"jellydj-fernet-salt-v2",
        hashlib.sha256,
    ).digest()  # 32 bytes — full SHA-256 output

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
