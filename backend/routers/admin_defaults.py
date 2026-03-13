
"""
routers/admin_defaults.py

Admin-only endpoints for configuring and provisioning default playlists.

A DefaultPlaylistConfig row represents one playlist slot the admin wants
every user to have.  When a user is provisioned, one UserPlaylist row is
created per config entry — but only if the user doesn't already have a
playlist backed by that template (deduplication by template_id).

Routes
──────
  GET  /api/admin/default-playlists            List all config rows + available templates
  POST /api/admin/default-playlists            Add a config row
  PUT  /api/admin/default-playlists/{id}       Update a config row
  DELETE /api/admin/default-playlists/{id}     Remove a config row

  POST /api/admin/default-playlists/provision/{user_id}
       Provision defaults to a single user (idempotent)

  POST /api/admin/default-playlists/provision-all
       Provision defaults to every managed user (idempotent sweep)

All routes require admin authentication.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    DefaultPlaylistConfig,
    ManagedUser,
    PlaylistTemplate,
    UserPlaylist,
)
from auth import require_admin, UserContext

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/default-playlists", tags=["admin-defaults"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class DefaultConfigIn(BaseModel):
    template_id:         int
    base_name:           str
    schedule_enabled:    bool = True
    schedule_interval_h: int  = 24
    position:            int  = 0


class DefaultConfigUpdate(BaseModel):
    base_name:           Optional[str]  = None
    schedule_enabled:    Optional[bool] = None
    schedule_interval_h: Optional[int]  = None
    position:            Optional[int]  = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config_out(cfg: DefaultPlaylistConfig, template_name: Optional[str] = None) -> dict:
    return {
        "id":                   cfg.id,
        "template_id":          cfg.template_id,
        "template_name":        template_name,
        "base_name":            cfg.base_name,
        "schedule_enabled":     cfg.schedule_enabled,
        "schedule_interval_h":  cfg.schedule_interval_h,
        "position":             cfg.position,
        "created_at":           cfg.created_at.isoformat() if cfg.created_at else None,
    }


def _template_name(template_id: int, db: Session) -> Optional[str]:
    t = db.query(PlaylistTemplate.name).filter(PlaylistTemplate.id == template_id).first()
    return t.name if t else None


def provision_user_defaults(user_id: str, db: Session) -> list[int]:
    """
    Idempotent: creates UserPlaylist rows for any DefaultPlaylistConfig
    that the user doesn't already have (matched by template_id).

    Returns list of newly created UserPlaylist IDs.
    Can be called from any code path (auth login, admin panel, scheduler).
    """
    configs = db.query(DefaultPlaylistConfig).order_by(DefaultPlaylistConfig.position).all()
    if not configs:
        return []

    # Collect template_ids the user already has a playlist for
    existing_template_ids = {
        row.template_id
        for row in db.query(UserPlaylist.template_id)
        .filter(UserPlaylist.owner_user_id == user_id)
        .all()
        if row.template_id is not None
    }

    created_ids: list[int] = []
    now = datetime.utcnow()

    for cfg in configs:
        if cfg.template_id in existing_template_ids:
            continue  # already has this one

        # Verify the template still exists
        template_exists = db.query(PlaylistTemplate.id).filter(
            PlaylistTemplate.id == cfg.template_id
        ).first()
        if not template_exists:
            log.warning(
                "DefaultPlaylistConfig id=%d references non-existent template_id=%d — skipping",
                cfg.id, cfg.template_id,
            )
            continue

        playlist = UserPlaylist(
            owner_user_id=user_id,
            template_id=cfg.template_id,
            base_name=cfg.base_name,
            schedule_enabled=cfg.schedule_enabled,
            schedule_interval_h=cfg.schedule_interval_h,
            created_at=now,
            updated_at=now,
        )
        db.add(playlist)
        db.flush()  # get the id before commit
        created_ids.append(playlist.id)
        log.info(
            "Provisioned default playlist '%s' (template=%d) for user=%s",
            cfg.base_name, cfg.template_id, user_id,
        )

    if created_ids:
        db.commit()

    return created_ids


# ── CRUD endpoints ────────────────────────────────────────────────────────────

@router.get("")
def list_configs(
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    List all default playlist config rows, enriched with template names.
    Also returns the full list of available templates so the frontend can
    render a picker without a second request.
    """
    configs = db.query(DefaultPlaylistConfig).order_by(DefaultPlaylistConfig.position).all()

    # Build a template name map in one query
    template_ids = list({c.template_id for c in configs})
    tmap: dict[int, str] = {}
    if template_ids:
        rows = db.query(PlaylistTemplate.id, PlaylistTemplate.name).filter(
            PlaylistTemplate.id.in_(template_ids)
        ).all()
        tmap = {r.id: r.name for r in rows}

    # All public + system templates available for selection
    available = db.query(PlaylistTemplate).filter(
        (PlaylistTemplate.is_public == True) | (PlaylistTemplate.is_system == True)  # noqa: E712
    ).order_by(PlaylistTemplate.name).all()

    return {
        "configs":    [_config_out(c, tmap.get(c.template_id)) for c in configs],
        "templates":  [
            {"id": t.id, "name": t.name, "is_system": t.is_system}
            for t in available
        ],
    }


@router.post("", status_code=201)
def add_config(
    body: DefaultConfigIn,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Add a new default playlist config row."""
    # Validate template exists
    if not db.query(PlaylistTemplate.id).filter(PlaylistTemplate.id == body.template_id).first():
        raise HTTPException(status_code=404, detail=f"Template {body.template_id} not found.")

    cfg = DefaultPlaylistConfig(
        template_id=body.template_id,
        base_name=body.base_name.strip() or "My Playlist",
        schedule_enabled=body.schedule_enabled,
        schedule_interval_h=max(1, body.schedule_interval_h),
        position=body.position,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    log.info("Admin added DefaultPlaylistConfig id=%d template=%d", cfg.id, cfg.template_id)
    return _config_out(cfg, _template_name(cfg.template_id, db))


@router.put("/{config_id}")
def update_config(
    config_id: int,
    body: DefaultConfigUpdate,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a default playlist config row."""
    cfg = db.query(DefaultPlaylistConfig).filter(DefaultPlaylistConfig.id == config_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found.")

    if body.base_name is not None:
        cfg.base_name = body.base_name.strip() or cfg.base_name
    if body.schedule_enabled is not None:
        cfg.schedule_enabled = body.schedule_enabled
    if body.schedule_interval_h is not None:
        cfg.schedule_interval_h = max(1, body.schedule_interval_h)
    if body.position is not None:
        cfg.position = body.position

    cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    return _config_out(cfg, _template_name(cfg.template_id, db))


@router.delete("/{config_id}", status_code=204)
def delete_config(
    config_id: int,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Remove a default playlist config row.
    Does NOT delete existing UserPlaylist rows — users keep what they have.
    """
    cfg = db.query(DefaultPlaylistConfig).filter(DefaultPlaylistConfig.id == config_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found.")
    db.delete(cfg)
    db.commit()
    log.info("Admin removed DefaultPlaylistConfig id=%d", config_id)


# ── Provisioning endpoints ────────────────────────────────────────────────────

@router.post("/provision/{user_id}")
def provision_user(
    user_id: str,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Provision default playlists to a single user.
    Idempotent — safe to call multiple times; already-existing playlists
    (matched by template_id) are skipped.
    """
    # Verify user exists
    managed = db.query(ManagedUser).filter(ManagedUser.jellyfin_user_id == user_id).first()
    if not managed:
        raise HTTPException(status_code=404, detail="User not found in managed_users.")

    created = provision_user_defaults(user_id, db)
    return {
        "ok": True,
        "user_id": user_id,
        "username": managed.username,
        "playlists_created": len(created),
        "playlist_ids": created,
    }


@router.post("/provision-all")
def provision_all_users(
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Provision default playlists to all managed users.
    Idempotent sweep — skips users who already have each template covered.
    Returns a summary per user.
    """
    all_managed = db.query(ManagedUser).all()
    results = []
    total_created = 0

    for managed in all_managed:
        created = provision_user_defaults(managed.jellyfin_user_id, db)
        total_created += len(created)
        results.append({
            "user_id":          managed.jellyfin_user_id,
            "username":         managed.username,
            "playlists_created": len(created),
        })

    log.info(
        "provision-all: swept %d users, created %d new playlists total",
        len(all_managed), total_created,
    )
    return {
        "ok":            True,
        "users_swept":   len(all_managed),
        "total_created": total_created,
        "results":       results,
    }
