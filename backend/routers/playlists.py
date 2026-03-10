from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import PlaylistRun, PlaylistRunItem, ManagedUser, UserSyncStatus
from services.playlist_writer import (
    run_playlist_generation,
    PLAYLIST_SIZES,
    PLAYLIST_LABELS,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/playlists", tags=["playlists"])


class GenerateRequest(BaseModel):
    playlist_types: Optional[list[str]] = None   # None = all types
    user_ids: Optional[list[str]] = None         # None = all enabled users


# ── Generation ────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate_playlists(
    req: GenerateRequest,
    db: Session = Depends(get_db),
):
    """
    Generate and write playlists to Jellyfin.
    Runs synchronously so the caller gets full results.
    For large libraries this may take 10–30s.
    """
    valid_types = list(PLAYLIST_SIZES.keys())
    types = req.playlist_types or valid_types
    invalid = [t for t in types if t not in valid_types]
    if invalid:
        raise HTTPException(400, f"Unknown playlist types: {invalid}. Valid: {valid_types}")

    result = await run_playlist_generation(db, types, req.user_ids)
    if not result["ok"] and "error" in result:
        raise HTTPException(500, result["error"])
    return result


@router.get("/types")
def list_playlist_types():
    """Return available playlist types with labels and sizes."""
    return [
        {"type": t, "label": PLAYLIST_LABELS[t], "size": PLAYLIST_SIZES[t]}
        for t in PLAYLIST_SIZES
    ]


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    """Return recent playlist generation runs."""
    runs = (
        db.query(PlaylistRun)
        .order_by(PlaylistRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "status": r.status,
            "playlist_types": r.playlist_types.split(",") if r.playlist_types else [],
            "user_count": r.user_count,
            "playlists_written": r.playlists_written,
            "duration_secs": (
                round((r.finished_at - r.started_at).total_seconds(), 1)
                if r.finished_at else None
            ),
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run_detail(run_id: int, db: Session = Depends(get_db)):
    """Return full detail for a single run including per-playlist results."""
    run = db.query(PlaylistRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")

    items = (
        db.query(PlaylistRunItem)
        .filter_by(run_id=run_id)
        .order_by(PlaylistRunItem.username, PlaylistRunItem.playlist_type)
        .all()
    )
    return {
        "id": run.id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "playlists_written": run.playlists_written,
        "items": [
            {
                "username": i.username,
                "playlist_type": i.playlist_type,
                "playlist_name": i.playlist_name,
                "tracks_added": i.tracks_added,
                "action": i.action,
                "status": i.status,
                "jellyfin_playlist_id": i.jellyfin_playlist_id,
            }
            for i in items
        ],
    }


@router.get("/current")
def current_playlists(db: Session = Depends(get_db)):
    """
    Return the most recently written playlist per user+type.
    Used by the Playlists page to show current state.
    """
    # Get the latest run
    latest_run = (
        db.query(PlaylistRun)
        .filter_by(status="ok")
        .order_by(PlaylistRun.finished_at.desc())
        .first()
    )
    if not latest_run:
        return {"last_run": None, "playlists": []}

    items = (
        db.query(PlaylistRunItem)
        .filter_by(run_id=latest_run.id, status="ok")
        .order_by(PlaylistRunItem.username, PlaylistRunItem.playlist_type)
        .all()
    )

    user_map = {
        u.jellyfin_user_id: u
        for u in db.query(ManagedUser).all()
    }

    return {
        "last_run": {
            "id": latest_run.id,
            "finished_at": latest_run.finished_at,
            "playlists_written": latest_run.playlists_written,
        },
        "playlists": [
            {
                "username": i.username,
                "playlist_type": i.playlist_type,
                "label": PLAYLIST_LABELS.get(i.playlist_type, i.playlist_type),
                "playlist_name": i.playlist_name,
                "tracks_added": i.tracks_added,
                "action": i.action,
                "jellyfin_playlist_id": i.jellyfin_playlist_id,
            }
            for i in items
        ],
    }


@router.get("/users")
def get_users_for_generation(db: Session = Depends(get_db)):
    """
    Return all enabled managed users with readiness status.
    New users (never indexed) appear with ready=False instead of being hidden.
    """
    users = db.query(ManagedUser).filter_by(has_activated=True).all()
    sync_map = {
        s.user_id: s
        for s in db.query(UserSyncStatus).all()
    }
    return [
        {
            "jellyfin_user_id": u.jellyfin_user_id,
            "username": u.username,
            "tracks_indexed": sync_map[u.jellyfin_user_id].tracks_indexed
                              if u.jellyfin_user_id in sync_map else 0,
            "status": sync_map[u.jellyfin_user_id].status
                      if u.jellyfin_user_id in sync_map else "never",
            "ready": u.jellyfin_user_id in sync_map and
                     sync_map[u.jellyfin_user_id].tracks_indexed > 0,
        }
        for u in users
    ]