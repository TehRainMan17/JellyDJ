"""
JellyDJ — Playlist Import Router

Endpoints
─────────
  POST   /api/import/playlists           Submit a URL or browser-extension payload
  GET    /api/import/playlists           List all imported playlists for current user
  GET    /api/import/playlists/{id}      Detail: tracks + album suggestions
  POST   /api/import/playlists/{id}/rematch    Re-run the match pass (after library update)
  DELETE /api/import/playlists/{id}      Remove import (leaves Jellyfin playlist intact)

  GET    /api/import/playlists/{id}/suggestions     Album suggestions for missing tracks
  POST   /api/import/playlists/{id}/suggestions/{sid}/approve    Send album to Lidarr
  POST   /api/import/playlists/{id}/suggestions/{sid}/reject

  POST   /api/import/webhook             Internal endpoint called by the webhook service
                                         when a new Jellyfin item is indexed.
                                         (Also called by services/events.py via import)

Register in main.py:
  from routers.playlist_import import router as playlist_import_router
  app.include_router(playlist_import_router)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional
import secrets

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, HttpUrl, validator
from sqlalchemy.orm import Session

from auth import UserContext, get_current_user, require_admin
from database import get_db
from models import (
    ConnectionSettings, ImportAlbumSuggestion,
    ImportedPlaylist, ImportedPlaylistTrack, ImportAPIKey, ManagedUser,
)
from crypto import decrypt
from services.external_playlist_fetcher import (
    FetchError, UnsupportedURLError, fetch_playlist_metadata, detect_platform,
)
from services.playlist_import import (
    build_album_suggestions, on_jellyfin_item_added,
    run_match_pass, write_jellyfin_playlist,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/import", tags=["playlist-import"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ImportRequest(BaseModel):
    """Submitted by the browser extension OR by the URL-paste form in the UI."""
    url: str

    # Pre-parsed track list — optionally sent by the browser extension to save
    # a yt-dlp round-trip when the extension already scraped the page DOM.
    # If omitted, the backend fetches metadata itself via yt-dlp.
    tracks: Optional[list[dict]] = None
    playlist_name: Optional[str] = None

    @validator("url")
    def url_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("URL must not be empty")
        return v


class SuggestionActionPayload(BaseModel):
    pass  # body intentionally empty; action is encoded in the endpoint path


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_user_from_api_key_or_jwt(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
) -> UserContext:
    """
    Authenticate via X-JellyDJ-Key header (API key) OR Authorization Bearer (JWT).

    Returns UserContext if either method succeeds. Raises 401 otherwise.
    Extension sends X-JellyDJ-Key; browser UI sends JWT Bearer.
    """
    # Try API key first
    api_key = request.headers.get("X-JellyDJ-Key", "").strip()
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        row = db.query(ImportAPIKey).filter_by(
            key_hash=key_hash,
            is_active=True,
        ).first()
        if not row:
            raise HTTPException(401, "Invalid or revoked API key")

        # Update last_used_at
        row.last_used_at = datetime.utcnow()
        db.commit()

        # Look up user metadata (username, is_admin) from managed_users
        user = db.query(ManagedUser).filter_by(jellyfin_user_id=row.user_id).first()
        return UserContext(
            user_id=row.user_id,
            username=user.username if user else row.user_id,
            is_admin=user.is_admin if user else False,
        )

    # Fall back to JWT Bearer
    if credentials:
        return get_current_user(credentials)

    raise HTTPException(401, "Authentication required (X-JellyDJ-Key header or Bearer token)")


def _get_lidarr_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="lidarr").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Lidarr not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


async def _send_album_to_lidarr(artist: str, album: str, db: Session) -> bool:
    """Fire-and-forget: send an artist/album search to Lidarr."""
    try:
        base_url, api_key = _get_lidarr_creds(db)
    except RuntimeError as exc:
        log.warning("Lidarr not configured: %s", exc)
        return False

    headers = {"X-Api-Key": api_key}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Search for artist in Lidarr
            search_resp = await client.get(
                f"{base_url}/api/v1/artist/lookup",
                headers=headers,
                params={"term": artist},
            )
            if search_resp.status_code != 200 or not search_resp.json():
                log.warning("Lidarr artist lookup failed for '%s'", artist)
                return False

            results = search_resp.json()
            lidarr_artist = results[0]

            # Check if already monitored
            existing_resp = await client.get(
                f"{base_url}/api/v1/artist",
                headers=headers,
            )
            existing_ids = {a["foreignArtistId"] for a in existing_resp.json()} if existing_resp.status_code == 200 else set()

            if lidarr_artist["foreignArtistId"] not in existing_ids:
                # Add artist to Lidarr
                add_payload = {
                    **lidarr_artist,
                    "qualityProfileId": 1,
                    "metadataProfileId": 1,
                    "monitored": True,
                    "addOptions": {"monitor": "none", "searchForMissingAlbums": False},
                    "rootFolderPath": lidarr_artist.get("rootFolderPath", "/music"),
                }
                await client.post(f"{base_url}/api/v1/artist", headers=headers, json=add_payload)

            # Trigger album search
            await client.post(
                f"{base_url}/api/v1/command",
                headers=headers,
                json={"name": "ArtistSearch", "artistId": lidarr_artist.get("id", 0)},
            )
            return True

    except Exception as exc:
        log.error("Error sending album to Lidarr: %s", exc)
        return False


def _format_playlist(pl: ImportedPlaylist) -> dict:
    return {
        "id":                    pl.id,
        "name":                  pl.name,
        "source_platform":       pl.source_platform,
        "source_url":            pl.source_url,
        "track_count":           pl.track_count,
        "matched_count":         pl.matched_count,
        "jellyfin_playlist_id":  pl.jellyfin_playlist_id,
        "status":                pl.status,
        "created_at":            pl.created_at,
        "last_sync_at":          pl.last_sync_at,
        "match_pct":             round(pl.matched_count / pl.track_count * 100, 1) if pl.track_count else 0,
    }


def _format_track(t: ImportedPlaylistTrack) -> dict:
    return {
        "id":                 t.id,
        "position":           t.position,
        "track_name":         t.track_name,
        "artist_name":        t.artist_name,
        "album_name":         t.album_name,
        "match_status":       t.match_status,
        "match_score":        t.match_score,
        "matched_item_id":    t.matched_item_id,
        "added_to_playlist":  t.added_to_playlist,
        "lidarr_requested":   t.lidarr_requested,
    }


# ── Background job ────────────────────────────────────────────────────────────

async def _run_full_import(playlist_id: int, owner_user_id: str):
    """
    Background task: match tracks, build suggestions, create Jellyfin playlist.
    Runs after the ImportedPlaylist and ImportedPlaylistTrack rows exist.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        run_match_pass(playlist_id, db)
        build_album_suggestions(playlist_id, db)
        await write_jellyfin_playlist(playlist_id, owner_user_id, db)

        # Mark playlist active
        pl = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
        if pl:
            pl.status = "active"
            db.commit()
    except Exception as exc:
        log.error("Import job failed for playlist %d: %s", playlist_id, exc)
        db = SessionLocal()  # fresh session for error update
        pl = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
        if pl:
            pl.status = "error"
            db.commit()
    finally:
        db.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/playlists", status_code=202)
async def import_playlist(
    payload: ImportRequest,
    background_tasks: BackgroundTasks,
    current_user: UserContext = Depends(get_user_from_api_key_or_jwt),
    db: Session = Depends(get_db),
):
    """
    Submit a playlist URL for import.

    If payload.tracks is provided (sent by browser extension), we skip yt-dlp.
    Otherwise, we fetch metadata from the URL using yt-dlp.
    """
    url = payload.url.strip()

    # Detect platform (also validates domain)
    try:
        platform = detect_platform(url)
    except UnsupportedURLError as exc:
        raise HTTPException(400, str(exc))

    # Fetch or use pre-parsed track list
    if payload.tracks is not None:
        # Browser extension path — trust the pre-parsed data
        tracks_raw = payload.tracks
        playlist_name = payload.playlist_name or "Imported Playlist"
        source_id = ""  # extension doesn't always provide this
    else:
        # URL paste path — use yt-dlp
        try:
            meta = fetch_playlist_metadata(url)
        except UnsupportedURLError as exc:
            raise HTTPException(400, str(exc))
        except FetchError as exc:
            raise HTTPException(502, str(exc))

        tracks_raw = meta["tracks"]
        playlist_name = meta["name"]
        source_id = meta.get("source_id", "")

    # Create ImportedPlaylist row
    pl = ImportedPlaylist(
        owner_user_id    = current_user.user_id,
        source_platform  = platform,
        source_url       = url,
        source_id        = source_id,
        name             = playlist_name,
        track_count      = len(tracks_raw),
        matched_count    = 0,
        status           = "pending",
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)

    # Bulk-insert track rows
    track_objs = []
    for t in tracks_raw:
        track_objs.append(ImportedPlaylistTrack(
            playlist_id   = pl.id,
            position      = t.get("position", 0),
            track_name    = t.get("track_name", ""),
            artist_name   = t.get("artist_name", ""),
            album_name    = t.get("album_name", ""),
            duration_ms   = t.get("duration_ms"),
            match_status  = "missing",
            suggested_album  = t.get("album_name", ""),
            suggested_artist = t.get("artist_name", ""),
        ))

    db.bulk_save_objects(track_objs)
    db.commit()

    # Kick off match + Jellyfin write in background
    background_tasks.add_task(_run_full_import, pl.id, current_user.user_id)

    return {
        "id":             pl.id,
        "name":           pl.name,
        "track_count":    pl.track_count,
        "status":         pl.status,
        "message":        "Import started — matching tracks in the background.",
    }


@router.get("/playlists")
def list_imported_playlists(
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all imported playlists owned by the current user."""
    playlists = (
        db.query(ImportedPlaylist)
        .filter_by(owner_user_id=current_user.user_id)
        .order_by(ImportedPlaylist.created_at.desc())
        .all()
    )
    return [_format_playlist(pl) for pl in playlists]


@router.get("/playlists/{playlist_id}")
def get_imported_playlist(
    playlist_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return full detail: playlist metadata + all tracks."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    tracks = (
        db.query(ImportedPlaylistTrack)
        .filter_by(playlist_id=playlist_id)
        .order_by(ImportedPlaylistTrack.position)
        .all()
    )

    return {
        **_format_playlist(pl),
        "tracks": [_format_track(t) for t in tracks],
    }


@router.post("/playlists/{playlist_id}/rematch", status_code=202)
async def rematch_playlist(
    playlist_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run match pass + rebuild Jellyfin playlist. Useful after a library scan."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    background_tasks.add_task(_run_full_import, playlist_id, current_user.user_id)
    return {"ok": True, "message": "Re-match started in background."}


@router.delete("/playlists/{playlist_id}", status_code=204)
def delete_imported_playlist(
    playlist_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Remove import metadata. Does NOT delete the Jellyfin playlist — that stays
    as a normal user playlist.
    """
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    db.query(ImportedPlaylistTrack).filter_by(playlist_id=playlist_id).delete()
    db.query(ImportAlbumSuggestion).filter_by(playlist_id=playlist_id).delete()
    db.delete(pl)
    db.commit()
    return None


# ── Album suggestions ─────────────────────────────────────────────────────────

@router.get("/playlists/{playlist_id}/suggestions")
def get_suggestions(
    playlist_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return album suggestions for missing tracks in this playlist."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    suggestions = (
        db.query(ImportAlbumSuggestion)
        .filter_by(playlist_id=playlist_id)
        .order_by(ImportAlbumSuggestion.coverage_count.desc())
        .all()
    )

    return [
        {
            "id":             s.id,
            "artist_name":    s.artist_name,
            "album_name":     s.album_name,
            "coverage_count": s.coverage_count,
            "lidarr_status":  s.lidarr_status,
            "lidarr_queued_at": s.lidarr_queued_at,
        }
        for s in suggestions
    ]


@router.post("/playlists/{playlist_id}/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    playlist_id: int,
    suggestion_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send an album suggestion to Lidarr for download."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    suggestion = db.query(ImportAlbumSuggestion).filter_by(
        id=suggestion_id, playlist_id=playlist_id
    ).first()
    if not suggestion:
        raise HTTPException(404, "Suggestion not found")

    if suggestion.lidarr_status not in ("pending", "rejected"):
        return {"ok": True, "message": "Already queued or complete."}

    # Send to Lidarr
    ok = await _send_album_to_lidarr(suggestion.artist_name, suggestion.album_name, db)

    suggestion.lidarr_status   = "approved" if ok else "pending"
    suggestion.lidarr_queued_at = datetime.utcnow() if ok else None
    db.commit()

    # Mark affected track rows as lidarr_requested
    db.query(ImportedPlaylistTrack).filter(
        ImportedPlaylistTrack.playlist_id == playlist_id,
        ImportedPlaylistTrack.match_status == "missing",
        ImportedPlaylistTrack.suggested_artist == suggestion.artist_name,
        ImportedPlaylistTrack.suggested_album  == suggestion.album_name,
    ).update({"lidarr_requested": True})
    db.commit()

    return {
        "ok":     ok,
        "status": suggestion.lidarr_status,
        "message": "Sent to Lidarr." if ok else "Lidarr not reachable — will retry.",
    }


@router.post("/playlists/{playlist_id}/suggestions/{suggestion_id}/reject", status_code=204)
def reject_suggestion(
    playlist_id: int,
    suggestion_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a suggestion as rejected (won't be sent to Lidarr)."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    suggestion = db.query(ImportAlbumSuggestion).filter_by(
        id=suggestion_id, playlist_id=playlist_id
    ).first()
    if not suggestion:
        raise HTTPException(404, "Suggestion not found")

    suggestion.lidarr_status = "rejected"
    db.commit()
    return None


# ── API Key Management ────────────────────────────────────────────────────────

@router.get("/api-keys")
def list_api_keys(
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all active API keys for the current user (masked)."""
    keys = (
        db.query(ImportAPIKey)
        .filter_by(user_id=current_user.user_id, is_active=True)
        .order_by(ImportAPIKey.created_at.desc())
        .all()
    )
    return [
        {
            "id": k.id,
            "label": k.label,
            "prefix": k.key_prefix,
            "created_at": k.created_at,
            "last_used_at": k.last_used_at,
        }
        for k in keys
    ]


@router.post("/api-keys", status_code=201)
def create_api_key(
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate a new API key for the extension.

    One active key per user — creating a new one deactivates the old.
    Returns the full key ONCE. Store it securely — it will never be shown again.
    """
    # Deactivate any existing active key
    db.query(ImportAPIKey).filter_by(
        user_id=current_user.user_id,
        is_active=True,
    ).update({"is_active": False})
    db.commit()

    # Generate new key: 64-char hex with jdj_ prefix = 68 chars total
    raw_key = secrets.token_hex(32)  # 64 hex chars
    full_key = f"jdj_{raw_key}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:8]

    # Store hashed key
    key_row = ImportAPIKey(
        user_id=current_user.user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        label=None,
    )
    db.add(key_row)
    db.commit()
    db.refresh(key_row)

    return {
        "id": key_row.id,
        "key": full_key,  # Returned ONCE only
        "prefix": key_prefix,
        "message": "Copy this key now — it will not be shown again. Store it securely.",
        "created_at": key_row.created_at,
    }


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke an API key (deactivate). Keep record for audit purposes."""
    key = db.query(ImportAPIKey).filter_by(
        id=key_id,
        user_id=current_user.user_id,
    ).first()
    if not key:
        raise HTTPException(404, "API key not found")

    key.is_active = False
    db.commit()
    return None


@router.post("/api-keys/{key_id}/reroll", status_code=201)
def reroll_api_key(
    key_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Revoke old key and generate a new one.

    Returns the new full key ONCE. The old key is immediately invalid.
    """
    key = db.query(ImportAPIKey).filter_by(
        id=key_id,
        user_id=current_user.user_id,
    ).first()
    if not key:
        raise HTTPException(404, "API key not found")

    # Deactivate old key (keep for audit)
    key.is_active = False
    db.commit()

    # Generate new key
    raw_key = secrets.token_hex(32)
    full_key = f"jdj_{raw_key}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:8]

    new_key = ImportAPIKey(
        user_id=current_user.user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        label=None,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return {
        "id": new_key.id,
        "key": full_key,  # Returned ONCE only
        "prefix": key_prefix,
        "message": "Old key revoked. Copy this new key now — it will not be shown again.",
        "created_at": new_key.created_at,
    }


# ── Internal webhook endpoint ─────────────────────────────────────────────────

@router.post("/internal/item-added")
async def item_added_hook(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Called internally by the webhook handler (services/events.py) when
    Jellyfin fires an item.added event. Not exposed publicly — called
    programmatically, no auth needed since it's internal-only.

    Payload: {"jellyfin_item_id": "..."}

    To wire this up, add to services/events.py → handle_jellyfin_webhook():
        if event_type in ("item.added", "library.new"):
            item_id = payload.get("ItemId") or payload.get("item_id")
            if item_id:
                from services.playlist_import import on_jellyfin_item_added
                await on_jellyfin_item_added(item_id, db)
    """
    item_id = payload.get("jellyfin_item_id")
    if not item_id:
        return {"ok": False, "message": "Missing jellyfin_item_id"}

    updated = await on_jellyfin_item_added(item_id, db)
    return {"ok": True, "playlists_updated": updated}
