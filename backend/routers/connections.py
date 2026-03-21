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
_conn_cache: dict = {}
_CONN_CACHE_TTL = 60


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


class JellyfinConnectionPayload(BaseModel):
    base_url: str
    api_key: str
    # Optional public-facing URL used only by the browser for deep-links.
    # Never passed to any server-side HTTP request — no SSRF validation needed.
    public_url: Optional[str] = ""


class ConnectionResponse(BaseModel):
    service: str
    base_url: str
    is_connected: bool
    last_tested: Optional[datetime]
    has_api_key: bool


class JellyfinConnectionResponse(BaseModel):
    service: str
    base_url: str
    public_url: Optional[str]
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
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]


def _ip_is_blocked(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _validate_service_url(url: str, field_name: str = "URL") -> str:
    """
    Validate a user-supplied service base URL for SSRF safety.
    Used for base_url (server-side requests) only — NOT for public_url.
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


def _validate_public_url(url: str) -> str:
    """
    Light validation for the public_url field.
    This URL is returned to the browser only — never used for server-side requests.
    We just normalise it; no SSRF resolution required.
    """
    url = url.strip().rstrip("/")
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            422,
            "Public URL must start with http:// or https://",
        )
    if not parsed.hostname:
        raise HTTPException(422, "Public URL is missing a hostname.")
    return url


# ── Jellyfin endpoints ────────────────────────────────────────────────────────

@router.get("/jellyfin", response_model=JellyfinConnectionResponse)
def get_jellyfin(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")

    cached = _cache_get("jellyfin")
    is_connected = cached["ok"] if cached else obj.is_connected
    last_tested = cached["last_tested"] if cached else obj.last_tested

    return JellyfinConnectionResponse(
        service="jellyfin",
        base_url=obj.base_url,
        public_url=obj.public_url or "",
        is_connected=is_connected,
        last_tested=last_tested,
        has_api_key=bool(obj.api_key_encrypted),
    )


@router.post("/jellyfin")
def save_jellyfin(
    payload: JellyfinConnectionPayload,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    obj = _get_or_create(db, "jellyfin")
    obj.base_url = _validate_service_url(payload.base_url, "Jellyfin URL")
    obj.api_key_encrypted = encrypt(payload.api_key)
    # public_url is optional — empty string means "use base_url for links"
    obj.public_url = _validate_public_url(payload.public_url or "")
    obj.is_connected = False
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("jellyfin")
    return {"ok": True}


@router.post("/jellyfin/test")
async def test_jellyfin(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin URL and API key must be saved first.")

    try:
        api_key = decrypt(obj.api_key_encrypted)
    except Exception:
        raise HTTPException(
            400,
            "Stored Jellyfin API key could not be decrypted — SECRET_KEY changed. "
            "Re-enter your API key on the Connections page.",
        )
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

    _cache_put("jellyfin", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Jellyfin. Check the URL and API key.")
    return {"ok": True, "message": "Jellyfin connected successfully."}


@router.get("/jellyfin/users/tracked")
def get_tracked_users(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
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

    n = db.query(UserPlaylist).filter_by(owner_user_id=uid).delete(synchronize_session=False)
    deleted["user_playlists"] = n

    n = db.query(RefreshToken).filter_by(user_id=uid).delete(synchronize_session=False)
    deleted["refresh_tokens"] = n

    for model_name in ("SkipPenalty", "UserTasteProfile", "UserSyncStatus"):
        try:
            from models import __dict__ as mdict
            model = mdict.get(model_name)
            if model:
                n = db.query(model).filter_by(user_id=uid).delete(synchronize_session=False)
                deleted[model_name.lower()] = n
        except Exception:
            pass

    user = db.query(ManagedUser).filter_by(jellyfin_user_id=uid).first()
    if user:
        user.has_activated = False
        user.is_enabled = False

    db.commit()

    import logging
    logging.getLogger(__name__).info("Admin wiped data for user %s: %s", uid, deleted)
    return {"ok": True, "deleted": deleted}


@router.post("/jellyfin/users/sync")
async def sync_managed_user_names(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
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
    _cache_invalidate("lidarr")
    return {"ok": True}


@router.post("/lidarr/test")
async def test_lidarr(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Lidarr URL and API key must be saved first.")

    try:
        api_key = decrypt(obj.api_key_encrypted)
    except Exception:
        raise HTTPException(
            400,
            "Stored Lidarr API key could not be decrypted — SECRET_KEY changed. "
            "Re-enter your API key on the Connections page.",
        )
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

    _cache_put("lidarr", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Lidarr. Check the URL and API key.")
    return {"ok": True, "message": "Lidarr connected successfully."}
