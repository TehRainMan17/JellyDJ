"""
JellyDJ — Album Exclusions router

Lets users manually exclude entire albums from all playlist generation.
Excluded albums are stored in ExcludedAlbum and filtered by playlist_writer.py.

Endpoints:
  GET    /api/exclusions/albums      — list all excluded albums
  POST   /api/exclusions/albums      — add an exclusion
  DELETE /api/exclusions/albums/{id} — remove an exclusion
  GET    /api/exclusions/search?q=   — live search Jellyfin for albums
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import require_admin, UserContext
from crypto import decrypt
from database import get_db
from models import ConnectionSettings, ExcludedAlbum

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exclusions", tags=["exclusions"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jellyfin_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


async def _get_admin_id(base_url: str, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{base_url}/Users", headers={"X-Emby-Token": api_key})
        r.raise_for_status()
        users = r.json()
    admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
    if not admin and users:
        admin = users[0]
    if not admin:
        raise HTTPException(503, "No Jellyfin users found")
    return admin["Id"]


# ── Schemas ───────────────────────────────────────────────────────────────────

class AddExclusionRequest(BaseModel):
    jellyfin_album_id: str
    album_name: str
    artist_name: str
    reason: Optional[str] = ""
    cover_image_url: Optional[str] = None


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/albums")
def list_exclusions(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(ExcludedAlbum).order_by(ExcludedAlbum.excluded_at.desc()).all()
    return [
        {
            "id":                row.id,
            "jellyfin_album_id": row.jellyfin_album_id,
            "album_name":        row.album_name,
            "artist_name":       row.artist_name,
            "reason":            row.reason or "",
            "cover_image_url":   row.cover_image_url,
            "excluded_at":       row.excluded_at.isoformat() if row.excluded_at else None,
            "track_count":       row.track_count,
        }
        for row in rows
    ]


# ── Add ───────────────────────────────────────────────────────────────────────

@router.post("/albums")
def add_exclusion(req: AddExclusionRequest, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(ExcludedAlbum).filter_by(
        jellyfin_album_id=req.jellyfin_album_id
    ).first()
    if existing:
        return {"ok": True, "already_excluded": True, "id": existing.id,
                "track_count": existing.track_count}

    from models import LibraryTrack
    track_count = db.query(LibraryTrack).filter(
        LibraryTrack.album_name == req.album_name,
        LibraryTrack.missing_since.is_(None),
    ).count()

    row = ExcludedAlbum(
        jellyfin_album_id=req.jellyfin_album_id,
        album_name=req.album_name,
        artist_name=req.artist_name,
        reason=req.reason or "",
        cover_image_url=req.cover_image_url,
        excluded_at=datetime.utcnow(),
        track_count=track_count,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(f"Album excluded: '{req.album_name}' by {req.artist_name} ({track_count} tracks)")
    return {"ok": True, "already_excluded": False, "id": row.id, "track_count": track_count}


# ── Remove ────────────────────────────────────────────────────────────────────

@router.delete("/albums/{exclusion_id}")
def remove_exclusion(exclusion_id: int, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(ExcludedAlbum).filter_by(id=exclusion_id).first()
    if not row:
        raise HTTPException(404, "Exclusion not found")
    name = row.album_name
    db.delete(row)
    db.commit()
    log.info(f"Album exclusion removed: '{name}'")
    return {"ok": True}


# ── Jellyfin album search ─────────────────────────────────────────────────────

@router.get("/search")
async def search_albums(
    q: str = Query(..., min_length=1),
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Search your Jellyfin library for albums by name or artist."""
    try:
        base_url, api_key = _jellyfin_creds(db)
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    try:
        admin_id = await _get_admin_id(base_url, api_key)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Jellyfin unreachable: {e}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{base_url}/Users/{admin_id}/Items",
                headers={"X-Emby-Token": api_key},
                params={
                    "SearchTerm":       q,
                    "IncludeItemTypes": "MusicAlbum",
                    "Recursive":        "true",
                    "Fields":           "AlbumArtist,ChildCount,ProductionYear",
                    "Limit":            "30",
                },
            )
            r.raise_for_status()
            items = r.json().get("Items", [])
    except Exception as e:
        raise HTTPException(503, f"Jellyfin search failed: {e}")

    excluded_ids = {
        row.jellyfin_album_id
        for row in db.query(ExcludedAlbum.jellyfin_album_id).all()
    }

    results = []
    for item in items:
        jid = item.get("Id", "")
        cover = None
        if item.get("ImageTags", {}).get("Primary"):
            cover = (
                f"{base_url}/Items/{jid}/Images/Primary"
                f"?maxWidth=120&quality=80&api_key={api_key}"
            )
        results.append({
            "jellyfin_album_id": jid,
            "album_name":        item.get("Name", ""),
            "artist_name":       item.get("AlbumArtist", ""),
            "year":              item.get("ProductionYear"),
            "track_count":       item.get("ChildCount", 0),
            "cover_image_url":   cover,
            "already_excluded":  jid in excluded_ids,
        })

    return {"results": results}
