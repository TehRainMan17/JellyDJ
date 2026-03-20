from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user, UserContext
from database import get_db
from models import PlaylistRun, PlaylistRunItem, ManagedUser, UserSyncStatus

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/playlists", tags=["playlists"])


# ── Run History ───────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(limit: int = Query(default=20, le=100), _: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return recent playlist push runs."""
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
def get_run_detail(run_id: int, _: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
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


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
def get_users_for_generation(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
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