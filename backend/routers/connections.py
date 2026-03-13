
"""
JellyDJ Connections router

Includes a 60-second in-memory cache for GET /jellyfin and GET /lidarr so that
navigating back to the Dashboard doesn't fire live HTTP checks every time.
The cache is invalidated when credentials are saved or a /test endpoint is called.
"""
import ipaddress
import socket
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import httpx
import time

from auth import require_admin, get_current_user, UserContext
from database import get_db
from models import ConnectionSettings, ManagedUser
from crypto import encrypt, decrypt

router = APIRouter(prefix="/api/connections", tags=["connections"])

# ── Connection status cache ───────────────────────────────────────────────────
# Keyed by service name. Avoids live HTTP on every dashboard load.
_conn_cache: dict = {}   # service -> {"ok": bool, "tested_at": float, "last_tested": datetime}
_CONN_CACHE_TTL = 60     # seconds


def _cache_put(service: str, ok: bool, last_tested: datetime):
    _conn_cache[service] = {
        "ok": ok,
        "last_tested": last_tested,
        "cached_at": time.monotonic(),
    }


def _cache_get(service: str) -> Optional[dict]:
    entry = _conn_cache.get(service)
    if not entry:
        return None
    if time.monotonic() - entry["cached_at"] > _CONN_CACHE_TTL:
        return None
    return entry


def _cache_invalidate(service: str):
    _conn_cache.pop(service, None)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConnectionPayload(BaseModel):
    base_url: str
    api_key: str


class ConnectionResponse(BaseModel):
    service: str
    base_url: str
    is_connected: bool
    last_tested: Optional[datetime]
    has_api_key: bool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create(db: Session, service: str) -> ConnectionSettings:
    obj = db.query(ConnectionSettings).filter_by(service=service).first()
    if not obj:
        obj = ConnectionSettings(service=service)
        db.add(obj)
        db.commit()
        db.refresh(obj)
    return obj


def _safe_url(url: str) -> str:
    return url.rstrip("/")


# ── SSRF protection ────────────────────────────────────────────────────────────
# IP networks that must never be the target of a server-side HTTP request.
# Covers loopback, RFC-1918 private ranges, link-local (IMDS on all major
# cloud providers lives at 169.254.169.254), and unspecified addresses.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),    # IPv4 loopback
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),      # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),   # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC-1918
    ipaddress.ip_network("fc00::/7"),        # IPv6 unique-local
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / cloud IMDS
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),       # Unspecified
    ipaddress.ip_network("::/128"),          # IPv6 unspecified
]


def _ip_is_blocked(addr: str) -> bool:
    """Return True if addr falls in any blocked network."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # unparseable → block
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _validate_service_url(url: str, field_name: str = "URL") -> str:
    """
    Validate a user-supplied service base URL for SSRF safety.

    Raises HTTP 422 if:
      - The URL is empty or has no scheme
      - The scheme is not http or https
      - The hostname resolves to any blocked IP range (loopback, RFC-1918,
        link-local, cloud metadata endpoint at 169.254.169.254, etc.)
      - The hostname cannot be resolved at all

    Returns the URL with trailing slash stripped, ready to store.

    The test endpoints (POST /jellyfin/test, POST /lidarr/test) read the URL
    from the database, so they are automatically covered — only a URL that
    passed this check on save can ever be used in an outbound request.

    DNS rebinding note: resolution happens at save time under admin auth.
    This stops the common case (typing a private IP directly) and raises the
    bar for an attacker who would need to control the DNS server for the
    target hostname to bypass the check. Defence-in-depth, not a complete
    guarantee against a targeted DNS rebinding attack.
    """
    url = url.strip().rstrip("/")
    if not url:
        raise HTTPException(422, f"{field_name} must not be empty.")

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise HTTPException(422, f"{field_name} is not a valid URL.")

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise HTTPException(
            422,
            f"{field_name} must start with http:// or https:// "
            f"(received scheme: {scheme!r}).",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(422, f"{field_name} is missing a hostname.")

    # Resolve hostname — getaddrinfo gives both A and AAAA records and handles
    # bracket-wrapped IPv6 literals correctly.
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(
            422,
            f"{field_name} hostname {hostname!r} could not be resolved ({exc}). "
            "Check that the address is correct and reachable from this server.",
        )

    for r in results:
        ip = r[4][0]
        if _ip_is_blocked(ip):
            raise HTTPException(
                422,
                f"{field_name} resolves to a private or reserved address ({ip}). "
                "Only publicly routable addresses are allowed here.",
            )

    return url


# ── Jellyfin endpoints ────────────────────────────────────────────────────────

@router.get("/jellyfin", response_model=ConnectionResponse)
def get_jellyfin(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")

    # Return cached status if fresh
    cached = _cache_get("jellyfin")
    is_connected = cached["ok"] if cached else obj.is_connected
    last_tested = cached["last_tested"] if cached else obj.last_tested

    return ConnectionResponse(
        service="jellyfin",
        base_url=obj.base_url,
        is_connected=is_connected,
        last_tested=last_tested,
        has_api_key=bool(obj.api_key_encrypted),
    )


@router.post("/jellyfin")
def save_jellyfin(payload: ConnectionPayload, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    obj.base_url = _validate_service_url(payload.base_url, "Jellyfin URL")
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False  # reset until next test
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("jellyfin")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/jellyfin/test")
async def test_jellyfin(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin URL and API key must be saved first.")

    api_key = decrypt(obj.api_key_encrypted)
    url = f"{obj.base_url}/Users"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"X-Emby-Token": api_key})
        ok = resp.status_code == 200
    except Exception:
        ok = False

    obj.is_connected = ok
    obj.last_tested = datetime.utcnow()
    db.commit()

    # Update cache with fresh result
    _cache_put("jellyfin", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Jellyfin. Check the URL and API key.")
    return {"ok": True, "message": "Jellyfin connected successfully."}


@router.get("/jellyfin/users/tracked")
def get_tracked_users(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Return all users who have activated JellyDJ (pushed at least one playlist).
    Used by the admin Connections page to show who has data and offer a delete option.
    """
    users = db.query(ManagedUser).filter_by(has_activated=True).all()
    return [
        {
            "jellyfin_user_id": u.jellyfin_user_id,
            "username": u.username,
            "is_admin": u.is_admin,
            "last_login_at": u.last_login_at,
        }
        for u in users
    ]


@router.delete("/jellyfin/users/{jellyfin_user_id}")
def delete_user_data(jellyfin_user_id: str, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Wipe all JellyDJ data for a user and de-activate them.

    Deletes: plays, track_scores, artist_profiles, genre_profiles,
             discovery_queue, playlist_run_items, user_playlists, refresh_tokens.
    Sets has_activated=False so the user won't be indexed until they push
    another playlist.
    """
    from models import (
        Play, TrackScore, ArtistProfile, GenreProfile,
        DiscoveryQueueItem, PlaylistRunItem, UserPlaylist, RefreshToken,
    )

    uid = jellyfin_user_id

    deleted = {}

    def _del(model, label):
        n = db.query(model).filter_by(user_id=uid).delete(synchronize_session=False)
        deleted[label] = n

    _del(Play,               "plays")
    _del(TrackScore,         "track_scores")
    _del(ArtistProfile,      "artist_profiles")
    _del(GenreProfile,       "genre_profiles")
    _del(DiscoveryQueueItem, "discovery_queue")
    _del(PlaylistRunItem,    "playlist_run_items")

    # UserPlaylist uses owner_user_id, not user_id
    n = db.query(UserPlaylist).filter_by(owner_user_id=uid).delete(synchronize_session=False)
    deleted["user_playlists"] = n

    # RefreshTokens use user_id
    n = db.query(RefreshToken).filter_by(user_id=uid).delete(synchronize_session=False)
    deleted["refresh_tokens"] = n

    # Also clear SkipPenalty and UserTasteProfile if they exist
    try:
        from models import SkipPenalty
        n = db.query(SkipPenalty).filter_by(user_id=uid).delete(synchronize_session=False)
        deleted["skip_penalties"] = n
    except Exception:
        pass
    try:
        from models import UserTasteProfile
        n = db.query(UserTasteProfile).filter_by(user_id=uid).delete(synchronize_session=False)
        deleted["taste_profiles"] = n
    except Exception:
        pass
    try:
        from models import UserSyncStatus
        n = db.query(UserSyncStatus).filter_by(user_id=uid).delete(synchronize_session=False)
        deleted["sync_status"] = n
    except Exception:
        pass

    # De-activate the user — they won't be indexed until they push another playlist
    user = db.query(ManagedUser).filter_by(jellyfin_user_id=uid).first()
    if user:
        user.has_activated = False
        user.is_enabled = False

    db.commit()

    import logging
    logging.getLogger(__name__).info(
        "Admin wiped data for user %s: %s", uid, deleted
    )
    return {"ok": True, "deleted": deleted}


@router.post("/jellyfin/users/sync")
async def sync_managed_user_names(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Pull fresh usernames from Jellyfin and update local records."""
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin not configured.")

    api_key = decrypt(obj.api_key_encrypted)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{obj.base_url}/Users",
                headers={"X-Emby-Token": api_key}
            )
        resp.raise_for_status()
        jf_users = {u["Id"]: u["Name"] for u in resp.json()}
    except Exception:
        raise HTTPException(502, "Failed to fetch users from Jellyfin.")

    for managed in db.query(ManagedUser).all():
        if managed.jellyfin_user_id in jf_users:
            managed.username = jf_users[managed.jellyfin_user_id]
    db.commit()
    return {"ok": True}


# ── Lidarr endpoints ──────────────────────────────────────────────────────────

@router.get("/lidarr", response_model=ConnectionResponse)
def get_lidarr(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")

    # Return cached status if fresh
    cached = _cache_get("lidarr")
    is_connected = cached["ok"] if cached else obj.is_connected
    last_tested = cached["last_tested"] if cached else obj.last_tested

    return ConnectionResponse(
        service="lidarr",
        base_url=obj.base_url,
        is_connected=is_connected,
        last_tested=last_tested,
        has_api_key=bool(obj.api_key_encrypted),
    )


@router.post("/lidarr")
def save_lidarr(payload: ConnectionPayload, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    obj.base_url = _validate_service_url(payload.base_url, "Lidarr URL")
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("lidarr")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/lidarr/test")
async def test_lidarr(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Lidarr URL and API key must be saved first.")

    api_key = decrypt(obj.api_key_encrypted)
    url = f"{obj.base_url}/api/v1/system/status"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"X-Api-Key": api_key})
        ok = resp.status_code == 200
    except Exception:
        ok = False

    obj.is_connected = ok
    obj.last_tested = datetime.utcnow()
    db.commit()

    # Update cache with fresh result
    _cache_put("lidarr", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Lidarr. Check the URL and API key.")
    return {"ok": True, "message": "Lidarr connected successfully."}
