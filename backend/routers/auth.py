"""
JellyDJ — Authentication router.

Endpoints
─────────
  POST /api/auth/login          Authenticate via Jellyfin, issue JWT + refresh token
  POST /api/auth/setup-login    First-time setup login (env-var credentials, no Jellyfin needed)
  GET  /api/auth/setup-status   Returns whether setup mode is active
  POST /api/auth/refresh        Rotate refresh token, re-validate Jellyfin session
  POST /api/auth/logout         Revoke refresh token server-side
  GET  /api/auth/me             Return current user from JWT (no DB/Jellyfin call)

Design notes
────────────
- Jellyfin is the only credential store; JellyDJ never stores passwords.
- The Jellyfin access token is encrypted at rest with crypto.encrypt().
- Refresh tokens are stored as SHA-256 hashes; the plaintext is only returned
  once (at issuance) and never stored.
- All Jellyfin HTTP calls use the same X-Emby-Authorization header format and
  ConnectionSettings lookup pattern as the rest of the codebase.

Setup Mode
──────────
- Enabled when SETUP_USERNAME and SETUP_PASSWORD are both set in the environment.
- Setup login is ONLY accepted when Jellyfin is not yet configured, preventing
  the setup account from being used as a backdoor once production is running.
  (Operators who intentionally want persistent setup access can set
  SETUP_ALLOW_AFTER_CONFIGURE=true, but this is not recommended.)
- The setup session is issued as a short-lived JWT (same 15-min expiry) with
  is_admin=True and a synthetic user_id of "jellydj-setup".
- Refresh tokens are NOT issued for setup sessions — the setup user must
  re-authenticate each time, preventing long-lived setup credentials.

Rate limiting
─────────────
- IP resolution prefers X-Forwarded-For (set by nginx/Traefik/Caddy in front
  of the container) over request.client.host, which is always the proxy's
  internal address in a docker-compose deployment.
- Controlled by TRUSTED_PROXY_DEPTH (default 1): the number of upstream
  proxies whose X-Forwarded-For entries to trust. Set to 0 to ignore the
  header entirely (direct-bind deployments). Set to 2+ for multi-hop proxy
  chains. Never trust a depth larger than your actual proxy chain.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from collections import defaultdict
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

# ── Simple in-process rate limiter (no external dependencies) ─────────────────
# Tracks login attempts per IP: {ip: [timestamp, ...]}.
# Max 10 attempts per 60-second window. Automatically clears old entries.
_login_attempts: dict = defaultdict(list)
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW = 60  # seconds

# How many proxy hops to trust in X-Forwarded-For.
# In a standard docker-compose deployment there is exactly one nginx proxy
# between the internet and the backend container, so the default of 1 is
# correct. Override with TRUSTED_PROXY_DEPTH=0 for direct-bind deployments
# (no reverse proxy in front) or a higher value for multi-hop chains.
_TRUSTED_PROXY_DEPTH = int(os.getenv("TRUSTED_PROXY_DEPTH", "1"))


def _real_ip(request: Request) -> str:
    """
    Return the best-guess real client IP for rate-limiting purposes.

    When running behind a reverse proxy (nginx, Caddy, Traefik) the
    direct TCP peer is always the proxy's internal docker-network address.
    Reading request.client.host in that configuration means every client
    shares the same "IP", collapsing the rate-limit bucket to a single
    global counter that either allows everything or locks everyone out.

    X-Forwarded-For is a comma-separated list built left-to-right as the
    request travels through proxies:
        X-Forwarded-For: <client>, <proxy1>, <proxy2>

    With TRUSTED_PROXY_DEPTH=1 (one trusted nginx in front) we read the
    rightmost entry minus one hop, which is the address the outermost
    trusted proxy saw as its client. An attacker cannot spoof this entry
    by injecting their own X-Forwarded-For header because our trusted
    proxy appends its own observed IP to the right of whatever the client
    sent — the injected value ends up further left and is ignored.

    If the header is absent or malformed, fall back to the transport-layer
    peer address (correct for direct-bind deployments with depth=0).
    """
    if _TRUSTED_PROXY_DEPTH > 0:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            # Split and strip whitespace from each hop address.
            hops = [h.strip() for h in forwarded_for.split(",")]
            # The genuine client IP is at index -(TRUSTED_PROXY_DEPTH).
            # Example with depth=1 and chain "client, nginx":
            #   hops[-1] = "nginx" (the proxy we trust appended this)
            #   hops[-2] = "client" (what nginx saw — the real client)
            idx = -(min(_TRUSTED_PROXY_DEPTH, len(hops)))
            candidate = hops[idx]
            if candidate:
                return candidate

    # Fallback: transport-layer peer (correct when no proxy is in front)
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    import time
    ip = _real_ip(request)
    now = time.time()
    # Purge attempts outside the window
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _RATE_LIMIT_WINDOW]
    if len(_login_attempts[ip]) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please wait a minute and try again.",
        )
    _login_attempts[ip].append(now)

# ── Jellyfin header ───────────────────────────────────────────────────────────

_EMBY_AUTH = (
    'MediaBrowser Client="JellyDJ", Device="Server", '
    'DeviceId="jellydj-server", Version="1.0.0"'
)

REFRESH_TOKEN_EXPIRE_HOURS = 8

# ── Setup mode ────────────────────────────────────────────────────────────────

SETUP_USER_ID = "jellydj-setup"

def _setup_credentials() -> tuple[str, str] | None:
    """
    Return (username, password) from env vars if setup mode is configured,
    or None if the vars are absent/empty.
    Both SETUP_USERNAME and SETUP_PASSWORD must be set for setup mode to activate.
    """
    u = os.getenv("SETUP_USERNAME", "").strip()
    p = os.getenv("SETUP_PASSWORD", "").strip()
    if u and p:
        return u, p
    return None


def _jellyfin_is_configured(db: Session) -> bool:
    """Return True if Jellyfin URL has been saved to the database."""
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    return bool(row and row.base_url)


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

    - Any Jellyfin user can log in; new rows are created with has_activated=False.
    - Activation happens automatically when the user pushes their first playlist.
    - is_admin and last_login_at are always refreshed on each login.
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
            is_enabled=False,       # legacy column — left False for new rows
            has_activated=False,    # flipped to True on first playlist push
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


class SetupLoginRequest(BaseModel):
    username: str
    password: str


class SetupLoginResponse(BaseModel):
    access_token: str
    username: str
    is_admin: bool


class SetupStatusResponse(BaseModel):
    setup_available: bool   # True when env vars are set AND Jellyfin is not yet configured
    jellyfin_configured: bool


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

@router.get("/setup-status", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)):
    """
    Returns whether setup mode is currently available.
    The frontend uses this to decide whether to show the setup-login UI.
    """
    creds = _setup_credentials()
    configured = _jellyfin_is_configured(db)
    allow_after = os.getenv("SETUP_ALLOW_AFTER_CONFIGURE", "").lower() in ("1", "true", "yes")

    setup_available = (
        creds is not None
        and (not configured or allow_after)
    )
    return SetupStatusResponse(
        setup_available=setup_available,
        jellyfin_configured=configured,
    )


@router.post("/setup-login", response_model=SetupLoginResponse)
def setup_login(request: Request, body: SetupLoginRequest, db: Session = Depends(get_db)):
    """
    First-time setup login using SETUP_USERNAME / SETUP_PASSWORD env vars.

    Security properties:
    - Only accepted when Jellyfin is not yet configured (bootstrap window).
    - Credentials compared with secrets.compare_digest to prevent timing attacks.
    - Issues a short-lived access JWT only — no refresh token is stored.
    - The synthetic user_id "jellydj-setup" is never written to managed_users.
    - Once Jellyfin URL is saved, this endpoint returns 403 (unless
      SETUP_ALLOW_AFTER_CONFIGURE=true, which is not recommended for
      internet-facing instances).
    """
    _check_rate_limit(request)
    creds = _setup_credentials()
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup mode is not enabled. Set SETUP_USERNAME and SETUP_PASSWORD in your .env to activate it.",
        )

    configured = _jellyfin_is_configured(db)
    allow_after = os.getenv("SETUP_ALLOW_AFTER_CONFIGURE", "").lower() in ("1", "true", "yes")
    if configured and not allow_after:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup login is disabled once Jellyfin is configured. Log in with your Jellyfin account.",
        )

    env_username, env_password = creds

    # Constant-time comparison to prevent timing attacks
    username_ok = secrets.compare_digest(body.username.encode(), env_username.encode())
    password_ok = secrets.compare_digest(body.password.encode(), env_password.encode())

    if not (username_ok and password_ok):
        log.warning("Failed setup login attempt for username=%r", body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid setup credentials",
        )

    log.info("Setup login successful — issuing short-lived admin token")
    access_token = create_access_token(
        {"user_id": SETUP_USER_ID, "username": env_username, "is_admin": True}
    )

    return SetupLoginResponse(
        access_token=access_token,
        username=env_username,
        is_admin=True,
    )

@router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate against Jellyfin and issue access + refresh tokens.

    Flow:
      1. POST /Users/AuthenticateByName to Jellyfin
      2. Extract User.Id, Policy.IsAdministrator, AccessToken
      3. Upsert ManagedUser row
      4. Persist RefreshToken (hashed) with encrypted Jellyfin token
      5. Return access JWT + opaque refresh token
    """
    _check_rate_limit(request)
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

    # Auto-provision default playlists for this user on every login.
    # provision_user_defaults() is idempotent — already-covered templates are skipped.
    try:
        from routers.admin_defaults import provision_user_defaults
        provision_user_defaults(jellyfin_user_id, db)
    except Exception as _prov_err:
        log.warning("Auto-provision on login failed for %s: %s", jellyfin_user_id, _prov_err)

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
