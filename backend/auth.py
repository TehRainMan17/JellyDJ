"""
JellyDJ — Shared authentication utilities.

Provides:
  - JWT access token creation/decoding (python-jose)
  - Refresh token generation and hashing
  - FastAPI dependencies: get_current_user, require_admin
  - Permission helpers: assert_owns_template, assert_owns_playlist

JWT signing key is the existing SECRET_KEY env var — the same key used by
crypto.py for Fernet credential encryption. No new env vars are introduced.
"""

import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ALGORITHM = "HS256"
JWT_ACCESS_MINUTES = int(os.getenv("JWT_ACCESS_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_HOURS = int(os.getenv("REFRESH_TOKEN_EXPIRE_HOURS", "8"))

_bearer = HTTPBearer(auto_error=True)


def _secret_key() -> str:
    """Return the app secret key. Fails loudly if unset in production."""
    return os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")


# ── Token dataclass ───────────────────────────────────────────────────────────

@dataclass
class UserContext:
    user_id: str
    username: str
    is_admin: bool


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(payload: dict) -> str:
    """
    Sign and return a JWT with a 15-minute (default) expiry.
    `payload` should include at minimum: user_id, username, is_admin.
    """
    to_encode = payload.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_MINUTES)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, _secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT.  Raises jose.JWTError on any failure
    (expired, bad signature, malformed).
    """
    return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])


# ── Refresh token helpers ─────────────────────────────────────────────────────

def create_refresh_token() -> str:
    """Return a cryptographically random 64-byte hex opaque refresh token."""
    return secrets.token_hex(64)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of `token`."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> UserContext:
    """
    FastAPI dependency — extracts and validates the Bearer JWT.
    Returns a UserContext on success; raises HTTP 401 on any failure.
    No database or Jellyfin calls are made.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str = payload.get("user_id")
        username: str = payload.get("username")
        is_admin: bool = bool(payload.get("is_admin", False))
        if user_id is None or username is None:
            raise credentials_exception
        return UserContext(user_id=user_id, username=username, is_admin=is_admin)
    except JWTError:
        raise credentials_exception


def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    """
    FastAPI dependency — like get_current_user but also enforces is_admin.
    Raises HTTP 403 if the authenticated user is not an administrator.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return user


# ── Permission helpers ────────────────────────────────────────────────────────

def assert_owns_template(template, user: UserContext) -> None:
    """Raises 403 if user is not admin and does not own the template."""
    if user.is_admin:
        return
    if template.owner_user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this template.",
        )


def assert_owns_playlist(playlist, user: UserContext) -> None:
    """Raises 403 if user is not admin and does not own the playlist."""
    if user.is_admin:
        return
    if playlist.owner_user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this playlist.",
        )
