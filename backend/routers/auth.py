"""
JellyDJ — Authentication router.

Endpoints
─────────
  POST /api/auth/login    Authenticate via Jellyfin, issue JWT + refresh token
  POST /api/auth/refresh  Rotate refresh token, re-validate Jellyfin session
  POST /api/auth/logout   Revoke refresh token server-side
  GET  /api/auth/me       Return current user from JWT (no DB/Jellyfin call)

Design notes
────────────
- Jellyfin is the only credential store; JellyDJ never stores passwords.
- The Jellyfin access token is encrypted at rest with crypto.encrypt().
- Refresh tokens are stored as SHA-256 hashes; the plaintext is only returned
  once (at issuance) and never stored.
- All Jellyfin HTTP calls use the same X-Emby-Authorization header format and
  ConnectionSettings lookup pattern as the rest of the codebase.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import (
    UserContext,
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_token,
)
from crypto import decrypt, encrypt
from database import get_db
from models import ConnectionSettings, ManagedUser, RefreshToken

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Jellyfin header ───────────────────────────────────────────────────────────

_EMBY_AUTH = (
    'MediaBrowser Client="JellyDJ", Device="Server", '
    'DeviceId="jellydj-server", Version="1.0.0"'
)

REFRESH_TOKEN_EXPIRE_HOURS = 8


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jellyfin_url(db: Session) -> str:
    """
    Return the configured Jellyfin base URL from ConnectionSettings.
    Raises HTTP 503 if not yet configured.
    """
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Jellyfin connection not configured",
        )
    return row.base_url.rstrip("/")


def _upsert_managed_user(
    db: Session,
    jellyfin_user_id: str,
    username: str,
    is_admin: bool,
) -> ManagedUser:
    """
    Auto-create or update the ManagedUser row for a Jellyfin user.

    - New users are created with is_enabled=False (admin enables them separately).
    - is_admin and last_login_at are always refreshed.
    """
    user = (
        db.query(ManagedUser)
        .filter_by(jellyfin_user_id=jellyfin_user_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if user is None:
        user = ManagedUser(
            jellyfin_user_id=jellyfin_user_id,
            username=username,
            is_enabled=False,
            is_admin=is_admin,
            last_login_at=now,
        )
        db.add(user)
        log.info("Auto-created ManagedUser for Jellyfin user %s (%s)", jellyfin_user_id, username)
    else:
        user.username = username
        user.is_admin = is_admin
        user.last_login_at = now
    db.commit()
    db.refresh(user)
    return user


def _issue_tokens(
    db: Session,
    jellyfin_user_id: str,
    username: str,
    is_admin: bool,
    jellyfin_token: str,
) -> tuple[str, str]:
    """
    Issue a new access JWT and refresh token.
    Stores the refresh token hash + encrypted Jellyfin token in RefreshToken.
    Returns (access_token, refresh_token_plaintext).
    """
    access_token = create_access_token(
        {"user_id": jellyfin_user_id, "username": username, "is_admin": is_admin}
    )
    refresh_plaintext = create_refresh_token()
    token_hash = hash_token(refresh_plaintext)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)

    rt = RefreshToken(
        token_hash=token_hash,
        user_id=jellyfin_user_id,
        jellyfin_token=encrypt(jellyfin_token),
        expires_at=expires_at,
    )
    db.add(rt)
    db.commit()

    return access_token, refresh_plaintext


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    username: str
    is_admin: bool


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    user_id: str
    username: str
    is_admin: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate against Jellyfin and issue access + refresh tokens.

    Flow:
      1. POST /Users/AuthenticateByName to Jellyfin
      2. Extract User.Id, Policy.IsAdministrator, AccessToken
      3. Upsert ManagedUser row
      4. Persist RefreshToken (hashed) with encrypted Jellyfin token
      5. Return access JWT + opaque refresh token
    """
    base_url = _jellyfin_url(db)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/Users/AuthenticateByName",
                json={"Username": body.username, "Pw": body.password},
                headers={"X-Emby-Authorization": _EMBY_AUTH},
            )
    except httpx.RequestError as exc:
        log.error("Jellyfin login request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Jellyfin server",
        )

    if resp.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Jellyfin username or password",
        )
    if resp.status_code != 200:
        log.error("Jellyfin auth returned %s: %s", resp.status_code, resp.text[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jellyfin returned unexpected status {resp.status_code}",
        )

    data = resp.json()
    jellyfin_user_id: str = data["User"]["Id"]
    jellyfin_username: str = data["User"]["Name"]
    is_admin: bool = bool(data["User"].get("Policy", {}).get("IsAdministrator", False))
    jellyfin_token: str = data["AccessToken"]

    _upsert_managed_user(db, jellyfin_user_id, jellyfin_username, is_admin)
    access_token, refresh_token = _issue_tokens(
        db, jellyfin_user_id, jellyfin_username, is_admin, jellyfin_token
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        username=jellyfin_username,
        is_admin=is_admin,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """
    Rotate a refresh token and issue a new access JWT.

    Flow:
      1. Look up RefreshToken row by hash
      2. Verify not expired
      3. Decrypt stored Jellyfin token and re-validate via GET /Users/{id}
      4. Delete old RefreshToken row, issue new pair
      5. Return new access JWT + new refresh token

    Returns HTTP 401 if the token is unknown, expired, or Jellyfin rejects
    the stored session (e.g. user changed their Jellyfin password).
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )

    token_hash = hash_token(body.refresh_token)
    rt = db.query(RefreshToken).filter_by(token_hash=token_hash).first()

    if rt is None:
        raise invalid_exc

    now = datetime.now(timezone.utc)
    # Handle both tz-aware and tz-naive datetimes stored in SQLite
    expires = rt.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        db.delete(rt)
        db.commit()
        raise invalid_exc

    # Re-validate the stored Jellyfin session token
    try:
        jellyfin_token = decrypt(rt.jellyfin_token)
    except Exception:
        log.error("Failed to decrypt stored Jellyfin token for user %s", rt.user_id)
        raise invalid_exc

    base_url = _jellyfin_url(db)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/Users/{rt.user_id}",
                headers={
                    "X-Emby-Authorization": _EMBY_AUTH,
                    "X-MediaBrowser-Token": jellyfin_token,
                },
            )
    except httpx.RequestError as exc:
        log.error("Jellyfin re-validation request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach Jellyfin server",
        )

    if resp.status_code == 401:
        # Jellyfin rejected our token — password changed or session revoked
        db.delete(rt)
        db.commit()
        raise invalid_exc

    if resp.status_code != 200:
        log.error("Jellyfin /Users/%s returned %s", rt.user_id, resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jellyfin returned unexpected status {resp.status_code}",
        )

    user_data = resp.json()
    username: str = user_data.get("Name", "")
    is_admin: bool = bool(user_data.get("Policy", {}).get("IsAdministrator", False))

    # Update last_used_at before deleting
    rt.last_used_at = now
    db.commit()

    # Rotate: delete old token row, issue fresh pair
    db.delete(rt)
    db.commit()

    new_access, new_refresh = _issue_tokens(
        db, rt.user_id, username, is_admin, jellyfin_token
    )

    return RefreshResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    """
    Revoke a refresh token server-side.
    Silently succeeds even if the token is unknown (already expired/deleted).
    """
    token_hash = hash_token(body.refresh_token)
    rt = db.query(RefreshToken).filter_by(token_hash=token_hash).first()
    if rt:
        db.delete(rt)
        db.commit()


@router.get("/me", response_model=MeResponse)
def me(user: UserContext = Depends(get_current_user)):
    """
    Return the current authenticated user from the JWT.
    No database or Jellyfin calls are made.
    """
    return MeResponse(
        user_id=user.user_id,
        username=user.username,
        is_admin=user.is_admin,
    )
