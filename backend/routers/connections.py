"""
JellyDJ Connections router

Includes a 60-second in-memory cache for GET /jellyfin and GET /lidarr so that
navigating back to the Dashboard doesn't fire live HTTP checks every time.
The cache is invalidated when credentials are saved or a /test endpoint is called.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import httpx
import time

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


class ManagedUserToggle(BaseModel):
    """Kept for API backward-compat only — no longer used by the UI."""
    jellyfin_user_id: str
    is_enabled: bool
    username: str = ""


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


# ── Jellyfin endpoints ────────────────────────────────────────────────────────

@router.get("/jellyfin", response_model=ConnectionResponse)
def get_jellyfin(db: Session = Depends(get_db)):
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
def save_jellyfin(payload: ConnectionPayload, db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    obj.base_url = _safe_url(payload.base_url)
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False  # reset until next test
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("jellyfin")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/jellyfin/test")
async def test_jellyfin(db: Session = Depends(get_db)):
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
def get_tracked_users(db: Session = Depends(get_db)):
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
def delete_user_data(jellyfin_user_id: str, db: Session = Depends(get_db)):
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


# ── Legacy toggle — kept so any existing integrations don't 500 ───────────────

@router.post("/jellyfin/users/toggle")
async def toggle_managed_user(payload: ManagedUserToggle, db: Session = Depends(get_db)):
    """Deprecated. Kept for backward-compat only. Has no effect on activation."""
    return {"ok": True, "deprecated": True}


# ── Legacy list — kept so any existing integrations don't 500 ────────────────

@router.get("/jellyfin/users")
async def get_jellyfin_users(db: Session = Depends(get_db)):
    """Deprecated list endpoint — returns tracked users only."""
    return await get_tracked_users.__wrapped__(db) if hasattr(get_tracked_users, '__wrapped__') else get_tracked_users(db)


@router.post("/jellyfin/users/sync")
async def sync_managed_user_names(db: Session = Depends(get_db)):
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
def get_lidarr(db: Session = Depends(get_db)):
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
def save_lidarr(payload: ConnectionPayload, db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    obj.base_url = _safe_url(payload.base_url)
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("lidarr")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/lidarr/test")
async def test_lidarr(db: Session = Depends(get_db)):
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