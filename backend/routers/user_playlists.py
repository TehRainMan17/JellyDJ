
"""
JellyDJ — UserPlaylist CRUD + push router  (Phase 6)

Endpoints
─────────
GET    /api/user-playlists
POST   /api/user-playlists
GET    /api/user-playlists/{id}
PUT    /api/user-playlists/{id}
DELETE /api/user-playlists/{id}
POST   /api/user-playlists/{id}/push
POST   /api/user-playlists/{id}/preview
GET    /api/user-playlists/{id}/history
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import UserContext, assert_owns_playlist, get_current_user
from database import get_db
from models import ManagedUser, PlaylistRunItem, PlaylistTemplate, UserPlaylist

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-playlists", tags=["user-playlists"])


# ── Pydantic request / response models ───────────────────────────────────────

class UserPlaylistCreateIn(BaseModel):
    template_id: int
    base_name: str
    schedule_enabled: bool = False
    schedule_interval_h: int = 24


class UserPlaylistUpdateIn(BaseModel):
    base_name: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_interval_h: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_username(user_id: str, db: Session) -> str:
    row = db.query(ManagedUser.username).filter(ManagedUser.jellyfin_user_id == user_id).first()
    return row.username if row else user_id


def _jellyfin_name(base_name: str, username: str) -> str:
    """Computed playlist name — never stored."""
    return f"{base_name} - {username}"


def _playlist_out(playlist: UserPlaylist, template_name: Optional[str], username: str) -> dict:
    return {
        "id": playlist.id,
        "owner_user_id": playlist.owner_user_id,
        "template_id": playlist.template_id,
        "template_name": template_name,
        "base_name": playlist.base_name,
        "jellyfin_name": _jellyfin_name(playlist.base_name, username),
        "schedule_enabled": playlist.schedule_enabled,
        "schedule_interval_h": playlist.schedule_interval_h,
        "last_generated_at": playlist.last_generated_at,
        "last_track_count": playlist.last_track_count,
        "created_at": playlist.created_at,
        "updated_at": playlist.updated_at,
    }


def _get_owned_playlist(playlist_id: int, user: UserContext, db: Session) -> UserPlaylist:
    playlist = db.query(UserPlaylist).filter(UserPlaylist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="UserPlaylist not found")
    assert_owns_playlist(playlist, user)
    return playlist


def _get_template_name(template_id: Optional[int], db: Session) -> Optional[str]:
    if template_id is None:
        return None
    row = db.query(PlaylistTemplate.name).filter(PlaylistTemplate.id == template_id).first()
    return row.name if row else None


def _visible_template(template_id: int, user: UserContext, db: Session) -> PlaylistTemplate:
    """Load template and verify it is visible to the user. Raises 404 if not."""
    template = db.query(PlaylistTemplate).filter(PlaylistTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if user.is_admin or template.is_public or template.owner_user_id == user.user_id:
        return template
    raise HTTPException(status_code=404, detail="Template not found")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def list_user_playlists(
    user_id: Optional[str] = Query(default=None),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns all UserPlaylists owned by the requesting user.
    Admins can pass ?user_id= to see any user's playlists.
    """
    if user_id is not None and user_id != user.user_id:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required to view another user's playlists.")
        effective_user_id = user_id
    else:
        effective_user_id = user.user_id

    playlists = (
        db.query(UserPlaylist)
        .filter(UserPlaylist.owner_user_id == effective_user_id)
        .all()
    )

    username = _get_username(effective_user_id, db)
    return [
        _playlist_out(p, _get_template_name(p.template_id, db), username)
        for p in playlists
    ]


@router.post("", status_code=201)
def create_user_playlist(
    body: UserPlaylistCreateIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new UserPlaylist. Template must be visible to the requesting user."""
    _visible_template(body.template_id, user, db)

    playlist = UserPlaylist(
        owner_user_id=user.user_id,
        template_id=body.template_id,
        base_name=body.base_name,
        schedule_enabled=body.schedule_enabled,
        schedule_interval_h=body.schedule_interval_h,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    username = _get_username(user.user_id, db)
    template_name = _get_template_name(playlist.template_id, db)
    log.info("Created UserPlaylist id=%d for user=%s", playlist.id, user.user_id)
    return _playlist_out(playlist, template_name, username)


@router.get("/{playlist_id}")
def get_user_playlist(
    playlist_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return full detail for a UserPlaylist. Owner or admin only."""
    playlist = _get_owned_playlist(playlist_id, user, db)
    username = _get_username(playlist.owner_user_id, db)
    template_name = _get_template_name(playlist.template_id, db)
    return _playlist_out(playlist, template_name, username)


@router.put("/{playlist_id}")
def update_user_playlist(
    playlist_id: int,
    body: UserPlaylistUpdateIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a UserPlaylist. Owner or admin only."""
    playlist = _get_owned_playlist(playlist_id, user, db)

    if body.base_name is not None:
        playlist.base_name = body.base_name
    if body.schedule_enabled is not None:
        playlist.schedule_enabled = body.schedule_enabled
    if body.schedule_interval_h is not None:
        playlist.schedule_interval_h = body.schedule_interval_h
    playlist.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(playlist)

    username = _get_username(playlist.owner_user_id, db)
    template_name = _get_template_name(playlist.template_id, db)
    log.info("Updated UserPlaylist id=%d by user=%s", playlist_id, user.user_id)
    return _playlist_out(playlist, template_name, username)


@router.delete("/{playlist_id}", status_code=200)
async def delete_user_playlist(
    playlist_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a UserPlaylist from JellyDJ and attempt to remove it from Jellyfin.
    If Jellyfin deletion fails, logs a warning but still returns 200.
    """
    playlist = _get_owned_playlist(playlist_id, user, db)

    # Attempt Jellyfin deletion — best-effort
    try:
        from services.playlist_writer import (
            _find_playlist,
            _jellyfin_creds,
        )
        base_url, api_key = _jellyfin_creds(db)
        username = _get_username(playlist.owner_user_id, db)
        jf_name = _jellyfin_name(playlist.base_name, username)

        # We need an admin user ID to call _find_playlist
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/Users",
                headers={"X-Emby-Token": api_key},
            )
            jf_admin_id = None
            if resp.status_code == 200:
                users = resp.json()
                admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
                jf_admin_id = (admin or (users[0] if users else None) or {}).get("Id")

        if jf_admin_id:
            jf_playlist_id = await _find_playlist(base_url, api_key, jf_name, jf_admin_id)
            if jf_playlist_id:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    del_resp = await client.delete(
                        f"{base_url}/Items/{jf_playlist_id}",
                        headers={"X-Emby-Token": api_key},
                    )
                    if del_resp.status_code not in (200, 204):
                        log.warning(
                            "Jellyfin DELETE /Items/%s returned %d — JellyDJ record will still be deleted.",
                            jf_playlist_id, del_resp.status_code,
                        )
    except Exception as exc:
        log.warning("Failed to delete Jellyfin playlist for UserPlaylist id=%d: %s", playlist_id, exc)

    db.delete(playlist)
    db.commit()
    log.info("Deleted UserPlaylist id=%d by user=%s", playlist_id, user.user_id)
    return {"ok": True, "deleted_id": playlist_id}


@router.post("/{playlist_id}/push")
async def push_user_playlist(
    playlist_id: int,
    user_id: Optional[str] = Query(default=None),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate tracks from the template and push the playlist to Jellyfin.
    Owner or admin only. Admin can pass ?user_id= to push on behalf of another user.
    """
    playlist = _get_owned_playlist(playlist_id, user, db)

    if user_id is not None and user_id != user.user_id:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required to push on behalf of another user.")
        effective_user_id = user_id
    else:
        effective_user_id = playlist.owner_user_id

    if playlist.template_id is None:
        raise HTTPException(status_code=400, detail="Playlist has no associated template (detached).")

    from services.playlist_engine import generate_from_template
    from services.playlist_writer import (
        _add_to_playlist,
        _clear_playlist,
        _create_playlist,
        _find_playlist,
        _jellyfin_creds,
    )

    # Generate track list
    try:
        track_ids = await generate_from_template(playlist.template_id, effective_user_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Get Jellyfin credentials
    try:
        base_url, api_key = _jellyfin_creds(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Look up username for the effective user
    username = _get_username(effective_user_id, db)
    jf_name = _jellyfin_name(playlist.base_name, username)

    # Get Jellyfin admin user ID
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{base_url}/Users",
            headers={"X-Emby-Token": api_key},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=503, detail="Could not reach Jellyfin to get admin user ID.")
        users = resp.json()
        admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
        jf_admin_id = (admin or (users[0] if users else None) or {}).get("Id")

    if not jf_admin_id:
        raise HTTPException(status_code=503, detail="Could not determine Jellyfin admin user ID.")

    # Push strategy: find existing → clear + add, or create new
    action: str
    existing_id = await _find_playlist(base_url, api_key, jf_name, jf_admin_id)

    if existing_id:
        await _clear_playlist(base_url, api_key, existing_id, jf_admin_id)
        await _add_to_playlist(base_url, api_key, existing_id, track_ids, jf_admin_id)
        action = "updated"
        jf_playlist_id = existing_id
    else:
        jf_playlist_id = await _create_playlist(base_url, api_key, jf_name, jf_admin_id, track_ids)
        action = "created"

    if not jf_playlist_id:
        raise HTTPException(status_code=502, detail="Jellyfin playlist operation failed.")

    # Update UserPlaylist metadata
    now = datetime.utcnow()
    playlist.last_generated_at = now
    playlist.last_track_count = len(track_ids)
    playlist.updated_at = now

    # Activate the user on their first successful push — this is the signal that
    # they've set up JellyDJ and want to be indexed/tracked going forward.
    managed = db.query(ManagedUser).filter_by(jellyfin_user_id=effective_user_id).first()
    if managed and not managed.has_activated:
        managed.has_activated = True
        managed.is_enabled = True   # keep legacy column in sync
        log.info("User %s (%s) activated via first playlist push", effective_user_id,
                 managed.username if managed else effective_user_id)

    # Create a PlaylistRunItem record (run_id=0 for template-driven pushes — no PlaylistRun row)
    run_item = PlaylistRunItem(
        run_id=0,
        user_id=effective_user_id,
        username=username,
        playlist_type="template",
        playlist_name=jf_name,
        jellyfin_playlist_id=jf_playlist_id or "",
        tracks_added=len(track_ids),
        action=action,
        status="ok",
        created_at=now,
        user_playlist_id=playlist.id,
    )
    db.add(run_item)
    db.commit()

    log.info(
        "Pushed UserPlaylist id=%d (%s) to Jellyfin: %d tracks, action=%s",
        playlist_id, jf_name, len(track_ids), action,
    )
    return {
        "ok": True,
        "tracks_added": len(track_ids),
        "jellyfin_name": jf_name,
        "action": action,
    }


@router.post("/{playlist_id}/preview")
async def preview_user_playlist(
    playlist_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dry-run preview for this UserPlaylist's template. Owner or admin only."""
    playlist = _get_owned_playlist(playlist_id, user, db)

    if playlist.template_id is None:
        raise HTTPException(status_code=400, detail="Playlist has no associated template (detached).")

    from services.playlist_engine import preview_template
    result = await preview_template(playlist.template_id, playlist.owner_user_id, db)
    return result


@router.get("/{playlist_id}/history")
def get_playlist_history(
    playlist_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the 20 most recent PlaylistRunItem rows for this UserPlaylist. Owner or admin only."""
    _get_owned_playlist(playlist_id, user, db)

    items = (
        db.query(PlaylistRunItem)
        .filter(PlaylistRunItem.user_playlist_id == playlist_id)
        .order_by(PlaylistRunItem.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": item.id,
            "run_id": item.run_id,
            "user_id": item.user_id,
            "username": item.username,
            "playlist_type": item.playlist_type,
            "playlist_name": item.playlist_name,
            "jellyfin_playlist_id": item.jellyfin_playlist_id,
            "tracks_added": item.tracks_added,
            "action": item.action,
            "status": item.status,
            "created_at": item.created_at,
            "user_playlist_id": item.user_playlist_id,
        }
        for item in items
    ]
