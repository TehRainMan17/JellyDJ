from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import UserContext, get_current_user
from crypto import decrypt
from database import get_db
from models import ConnectionSettings, RefreshToken, TrackScore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mobile", tags=["mobile"])


class MobileTrack(BaseModel):
    id: str
    title: str
    artist: str
    album: str
    duration_ms: int
    stream_url: str
    image_url: Optional[str] = None


class MobilePlaylist(BaseModel):
    id: str
    name: str
    track_count: int
    cover_image_url: Optional[str] = None


class MobileSearchResponse(BaseModel):
    tracks: list[MobileTrack]


def _jellyfin_connection(db: Session) -> str:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url:
        raise HTTPException(status_code=503, detail="Jellyfin connection not configured")
    return row.base_url.rstrip("/")


def _active_jellyfin_user_token(user_id: str, db: Session) -> str:
    now = datetime.now(timezone.utc)

    rows = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.last_used_at.desc(), RefreshToken.created_at.desc())
        .all()
    )

    for row in rows:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now > expires:
            continue
        try:
            return decrypt(row.jellyfin_token)
        except Exception:
            continue

    raise HTTPException(status_code=401, detail="No active Jellyfin session found. Please sign in again.")


def _track_from_jellyfin(item: dict, base_url: str, user_id: str, jellyfin_token: str) -> MobileTrack:
    item_id = str(item.get("Id") or "")
    title = item.get("Name") or "Unknown Track"
    artists = item.get("Artists") or []
    artist = artists[0] if artists else (item.get("ArtistItems") or [{"Name": "Unknown Artist"}])[0].get("Name", "Unknown Artist")
    album = item.get("Album") or "Unknown Album"
    runtime_ticks = int(item.get("RunTimeTicks") or 0)
    duration_ms = int(runtime_ticks / 10_000)

    stream_url = (
        f"{base_url}/Audio/{item_id}/universal"
        f"?UserId={user_id}&DeviceId=JellyDJMobileAndroid&api_key={jellyfin_token}"
        "&Container=opus,webm,mp3,aac,m4a,m4b,flac,wav,ogg,webma"
        "&TranscodingContainer=mp3"
        "&TranscodingProtocol=http"
        "&AudioCodec=mp3"
        "&MaxStreamingBitrate=320000"
    )

    image_url = f"{base_url}/Items/{item_id}/Images/Primary?api_key={jellyfin_token}&maxWidth=512&quality=80"

    return MobileTrack(
        id=item_id,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        stream_url=stream_url,
        image_url=image_url,
    )


@router.get("/library/recent", response_model=list[MobileTrack])
async def recent_tracks(
    limit: int = Query(default=100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _jellyfin_connection(db)
    jellyfin_token = _active_jellyfin_user_token(user.user_id, db)

    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "SortBy": "DatePlayed",
        "SortOrder": "Descending",
        "Fields": "RunTimeTicks,Album,Artists",
        "Limit": str(limit),
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{user.user_id}/Items",
            headers={"X-Emby-Token": jellyfin_token},
            params=params,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not load Jellyfin library")

    items = resp.json().get("Items", [])
    return [_track_from_jellyfin(item, base_url, user.user_id, jellyfin_token) for item in items]


@router.get("/search", response_model=MobileSearchResponse)
async def search_tracks(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _jellyfin_connection(db)
    jellyfin_token = _active_jellyfin_user_token(user.user_id, db)

    params = {
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "SearchTerm": q,
        "Fields": "RunTimeTicks,Album,Artists",
        "Limit": str(limit),
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{user.user_id}/Items",
            headers={"X-Emby-Token": jellyfin_token},
            params=params,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Search failed")

    items = resp.json().get("Items", [])
    tracks = [_track_from_jellyfin(item, base_url, user.user_id, jellyfin_token) for item in items]
    return MobileSearchResponse(tracks=tracks)


@router.get("/playlists", response_model=list[MobilePlaylist])
async def playlists(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _jellyfin_connection(db)
    jellyfin_token = _active_jellyfin_user_token(user.user_id, db)

    params = {
        "IncludeItemTypes": "Playlist",
        "Recursive": "true",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{user.user_id}/Items",
            headers={"X-Emby-Token": jellyfin_token},
            params=params,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not load playlists")

    out: list[MobilePlaylist] = []
    for item in resp.json().get("Items", []):
        item_id = str(item.get("Id") or "")
        has_image = bool(item.get("ImageTags", {}).get("Primary"))
        cover_url = (
            f"{base_url}/Items/{item_id}/Images/Primary?api_key={jellyfin_token}&maxWidth=512&quality=80"
            if has_image else None
        )
        out.append(
            MobilePlaylist(
                id=item_id,
                name=item.get("Name") or "Playlist",
                track_count=int(item.get("ChildCount") or 0),
                cover_image_url=cover_url,
            )
        )
    return out


@router.get("/top-tracks", response_model=list[MobileTrack])
async def top_tracks(
    limit: int = Query(default=20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _jellyfin_connection(db)
    jellyfin_token = _active_jellyfin_user_token(user.user_id, db)

    scores = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user.user_id, TrackScore.play_count > 0)
        .order_by(TrackScore.play_count.desc())
        .limit(limit)
        .all()
    )

    if not scores:
        return []

    item_ids = ",".join(s.jellyfin_item_id for s in scores)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{user.user_id}/Items",
            headers={"X-Emby-Token": jellyfin_token},
            params={"Ids": item_ids, "Fields": "RunTimeTicks,Album,Artists"},
        )

    if resp.status_code != 200:
        return []

    items_by_id = {str(i.get("Id")): i for i in resp.json().get("Items", [])}
    result = []
    for score in scores:
        item = items_by_id.get(score.jellyfin_item_id)
        if item:
            result.append(_track_from_jellyfin(item, base_url, user.user_id, jellyfin_token))
    return result


@router.get("/playlists/{playlist_id}/tracks", response_model=list[MobileTrack])
async def playlist_tracks(
    playlist_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _jellyfin_connection(db)
    jellyfin_token = _active_jellyfin_user_token(user.user_id, db)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Playlists/{playlist_id}/Items",
            headers={"X-Emby-Token": jellyfin_token},
            params={"UserId": user.user_id, "Fields": "RunTimeTicks,Album,Artists"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not load playlist tracks")

    items = resp.json().get("Items", [])
    return [_track_from_jellyfin(item, base_url, user.user_id, jellyfin_token) for item in items]
