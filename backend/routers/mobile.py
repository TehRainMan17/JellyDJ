from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Float, cast, desc, func
from sqlalchemy.orm import Session

from auth import UserContext, get_current_user
from crypto import decrypt
from database import get_db
from models import ArtistEnrichment, ArtistProfile, ConnectionSettings, GenreProfile, LibraryTrack, RefreshToken, TrackScore

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


class MobileLibraryTrack(BaseModel):
    id: str
    title: str
    artist: str
    album: str
    duration_ms: int
    stream_url: str
    image_url: Optional[str] = None
    artist_affinity: float = 0.0
    global_popularity: Optional[float] = None
    play_count: int = 0
    bpm: Optional[float] = None
    energy: Optional[float] = None


class MobileLibraryArtist(BaseModel):
    id: str
    name: str
    image_url: Optional[str] = None
    affinity_score: float
    global_popularity: Optional[float] = None
    track_count: int


class MobileLibraryAlbum(BaseModel):
    id: str
    name: str
    artist: str
    image_url: Optional[str] = None
    affinity_score: float
    global_popularity: Optional[float] = None
    track_count: int


class MobileLibraryGenre(BaseModel):
    id: str
    name: str
    affinity_score: float
    track_count: int


class MobileRelatedArtist(BaseModel):
    name: str
    match_score: float


class MobileGenreWeight(BaseModel):
    genre: str
    weight: float


class MobileArtistDetail(BaseModel):
    name: str
    image_url: Optional[str] = None
    affinity_score: float = 0.0
    global_popularity: Optional[float] = None
    trend_direction: Optional[str] = None
    biography: Optional[str] = None
    canonical_genres: list[MobileGenreWeight] = []
    related_artists: list[MobileRelatedArtist] = []


class MobileLibraryYear(BaseModel):
    year: int
    track_count: int


class MobileSmartCollection(BaseModel):
    key: str
    label: str
    description: str
    icon_hint: str


_SMART_COLLECTIONS: list[MobileSmartCollection] = [
    MobileSmartCollection(key="top_played", label="Top Played", description="Your most-played tracks of all time", icon_hint="play_arrow"),
    MobileSmartCollection(key="hidden_gems", label="Hidden Gems", description="High affinity, low global popularity", icon_hint="diamond"),
    MobileSmartCollection(key="high_energy", label="High Energy", description="Maximum energy tracks", icon_hint="bolt"),
    MobileSmartCollection(key="acoustic_chill", label="Acoustic Chill", description="Warm, acoustic, mellow tracks", icon_hint="spa"),
    MobileSmartCollection(key="fast_tempo", label="Fast Tempo", description="Tracks with the highest BPM", icon_hint="speed"),
    MobileSmartCollection(key="rising_artists", label="Rising Artists", description="Tracks by globally trending artists", icon_hint="trending_up"),
]


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


def _try_jellyfin_connection(db: Session) -> Optional[str]:
    try:
        return _jellyfin_connection(db)
    except HTTPException as exc:
        if exc.status_code == 503:
            log.info("Mobile library: Jellyfin base URL not configured; returning DB-only browse data")
            return None
        raise


def _try_active_jellyfin_user_token(user_id: str, db: Session) -> Optional[str]:
    try:
        return _active_jellyfin_user_token(user_id, db)
    except HTTPException as exc:
        if exc.status_code == 401:
            log.info("Mobile library: no active Jellyfin user token for user_id=%s; returning DB-only browse data", user_id)
            return None
        raise


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


def _stream_url_for_item(base_url: str, user_id: str, jellyfin_token: str, item_id: str) -> str:
    return (
        f"{base_url}/Audio/{item_id}/universal"
        f"?UserId={user_id}&DeviceId=JellyDJMobileAndroid&api_key={jellyfin_token}"
        "&Container=opus,webm,mp3,aac,m4a,m4b,flac,wav,ogg,webma"
        "&TranscodingContainer=mp3"
        "&TranscodingProtocol=http"
        "&AudioCodec=mp3"
        "&MaxStreamingBitrate=320000"
    )


def _image_url_for_item(base_url: str, jellyfin_token: str, item_id: str) -> str:
    return f"{base_url}/Items/{item_id}/Images/Primary?api_key={jellyfin_token}&maxWidth=512&quality=80"


def _ensure_jellyfin_ok(resp: httpx.Response, action: str) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Jellyfin session expired. Please sign in again.")
    if resp.status_code == 404:
        raise HTTPException(status_code=502, detail=f"Jellyfin resource not found while trying to {action}")
    raise HTTPException(status_code=502, detail=f"Could not {action} from Jellyfin")


def _build_library_track(r, base_url: Optional[str], user_id: str, jellyfin_token: Optional[str]) -> MobileLibraryTrack:
    return MobileLibraryTrack(
        id=r.jellyfin_item_id,
        title=r.track_name or "Unknown Track",
        artist=r.artist_name or "Unknown Artist",
        album=r.album_name or "Unknown Album",
        duration_ms=int((r.duration_ticks or 0) / 10_000),
        stream_url=(
            _stream_url_for_item(base_url, user_id, jellyfin_token, r.jellyfin_item_id)
            if (base_url and jellyfin_token)
            else ""
        ),
        image_url=(
            _image_url_for_item(base_url, jellyfin_token, r.jellyfin_item_id)
            if (base_url and jellyfin_token)
            else None
        ),
        artist_affinity=float(r.artist_affinity or 0.0),
        global_popularity=float(r.global_popularity) if r.global_popularity is not None else None,
        play_count=int(r.play_count or 0),
        bpm=float(r.bpm) if r.bpm is not None else None,
        energy=float(r.energy) if r.energy is not None else None,
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

    _ensure_jellyfin_ok(resp, "load Jellyfin library")

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

    _ensure_jellyfin_ok(resp, "search Jellyfin library")

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

    _ensure_jellyfin_ok(resp, "load playlists")

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

    _ensure_jellyfin_ok(resp, "load top tracks")

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

    _ensure_jellyfin_ok(resp, "load playlist tracks")

    items = resp.json().get("Items", [])
    return [_track_from_jellyfin(item, base_url, user.user_id, jellyfin_token) for item in items]


@router.get("/library/artists", response_model=list[MobileLibraryArtist])
def library_artists(
    q: Optional[str] = Query(default=None, min_length=1, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _try_jellyfin_connection(db)
    jellyfin_token = _try_active_jellyfin_user_token(user.user_id, db)

    lib_q = db.query(
        LibraryTrack.artist_name.label("artist_name"),
        func.max(LibraryTrack.jellyfin_artist_id).label("jellyfin_artist_id"),
        func.max(LibraryTrack.jellyfin_item_id).label("fallback_item_id"),
        func.count(LibraryTrack.jellyfin_item_id).label("lib_track_count"),
    ).filter(
        LibraryTrack.artist_name.isnot(None),
        LibraryTrack.artist_name != "",
        LibraryTrack.missing_since.is_(None),
    )
    if q:
        lib_q = lib_q.filter(LibraryTrack.artist_name.ilike(f"%{q}%"))
    lib_rows = lib_q.group_by(LibraryTrack.artist_name).all()
    if not lib_rows:
        return []

    artist_names = [r.artist_name for r in lib_rows]
    profile_rows = db.query(
        ArtistProfile.artist_name,
        cast(ArtistProfile.affinity_score, Float).label("affinity_score"),
    ).filter(
        ArtistProfile.user_id == user.user_id,
        ArtistProfile.artist_name.in_(artist_names),
    ).all()
    affinity_map = {r.artist_name: float(r.affinity_score or 0.0) for r in profile_rows}

    counts_rows = (
        db.query(
            TrackScore.artist_name,
            func.count(TrackScore.jellyfin_item_id).label("track_count"),
            func.avg(TrackScore.global_popularity).label("global_popularity"),
        )
        .filter(
            TrackScore.user_id == user.user_id,
            TrackScore.artist_name.in_(artist_names),
        )
        .group_by(TrackScore.artist_name)
        .all()
    )
    counts_map = {
        r.artist_name: {"track_count": int(r.track_count or 0), "global_popularity": r.global_popularity}
        for r in counts_rows
    }

    results = [
        MobileLibraryArtist(
            id=lr.artist_name,
            name=lr.artist_name,
            image_url=(
                _image_url_for_item(base_url, jellyfin_token, lr.jellyfin_artist_id)
                if (base_url and jellyfin_token and lr.jellyfin_artist_id)
                else (
                    _image_url_for_item(base_url, jellyfin_token, lr.fallback_item_id)
                    if (base_url and jellyfin_token and lr.fallback_item_id)
                    else None
                )
            ),
            affinity_score=affinity_map.get(lr.artist_name, 0.0),
            global_popularity=(
                float(counts_map[lr.artist_name]["global_popularity"])
                if counts_map.get(lr.artist_name, {}).get("global_popularity") is not None
                else None
            ),
            track_count=max(
                int(lr.lib_track_count or 0),
                counts_map.get(lr.artist_name, {}).get("track_count", 0),
            ),
        )
        for lr in lib_rows
    ]
    results.sort(key=lambda x: x.affinity_score, reverse=True)
    return results[:limit]


@router.get("/library/albums", response_model=list[MobileLibraryAlbum])
def library_albums(
    q: Optional[str] = Query(default=None, min_length=1, max_length=120),
    artist: Optional[str] = Query(default=None),
    sort: str = Query(default="affinity"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _try_jellyfin_connection(db)
    jellyfin_token = _try_active_jellyfin_user_token(user.user_id, db)

    lib_q = db.query(
        LibraryTrack.album_name.label("album_name"),
        LibraryTrack.jellyfin_album_id.label("jellyfin_album_id"),
        func.min(LibraryTrack.artist_name).label("primary_artist"),
        func.count(func.distinct(LibraryTrack.artist_name)).label("distinct_artists"),
        func.count(LibraryTrack.jellyfin_item_id).label("lib_track_count"),
    ).filter(
        LibraryTrack.album_name.isnot(None),
        LibraryTrack.album_name != "",
        LibraryTrack.artist_name.isnot(None),
        LibraryTrack.artist_name != "",
        LibraryTrack.missing_since.is_(None),
    )
    if q:
        lib_q = lib_q.filter(LibraryTrack.album_name.ilike(f"%{q}%"))
    if artist:
        lib_q = lib_q.filter(LibraryTrack.artist_name == artist)
    lib_rows = lib_q.group_by(LibraryTrack.album_name, LibraryTrack.jellyfin_album_id).all()
    if not lib_rows:
        return []

    album_names = [r.album_name for r in lib_rows]
    score_rows = (
        db.query(
            TrackScore.album_name,
            func.avg(cast(TrackScore.artist_affinity, Float)).label("affinity_score"),
            func.avg(TrackScore.global_popularity).label("global_popularity"),
            func.count(TrackScore.jellyfin_item_id).label("score_track_count"),
            func.max(TrackScore.last_played).label("max_last_played"),
        )
        .filter(
            TrackScore.user_id == user.user_id,
            TrackScore.album_name.in_(album_names),
        )
        .group_by(TrackScore.album_name)
        .all()
    )
    score_map = {
        r.album_name: {
            "affinity_score": float(r.affinity_score or 0.0),
            "global_popularity": float(r.global_popularity) if r.global_popularity is not None else None,
            "score_track_count": int(r.score_track_count or 0),
            "max_last_played": r.max_last_played,
        }
        for r in score_rows
    }

    albums = [
        MobileLibraryAlbum(
            id=lr.jellyfin_album_id or f"__{lr.album_name}",
            name=lr.album_name,
            artist="Various Artists" if (lr.distinct_artists or 1) > 1 else (lr.primary_artist or ""),
            image_url=(
                _image_url_for_item(base_url, jellyfin_token, lr.jellyfin_album_id)
                if (base_url and jellyfin_token and lr.jellyfin_album_id)
                else None
            ),
            affinity_score=score_map.get(lr.album_name, {}).get("affinity_score", 0.0),
            global_popularity=score_map.get(lr.album_name, {}).get("global_popularity"),
            track_count=max(
                int(lr.lib_track_count or 0),
                score_map.get(lr.album_name, {}).get("score_track_count", 0),
            ),
        )
        for lr in lib_rows
    ]

    if sort == "recent":
        albums.sort(key=lambda a: score_map.get(a.name, {}).get("max_last_played") or "", reverse=True)
    elif sort == "popular":
        albums.sort(key=lambda a: a.global_popularity or 0.0, reverse=True)
    else:
        albums.sort(key=lambda a: a.affinity_score, reverse=True)
    return albums[:limit]


@router.get("/library/genres", response_model=list[MobileLibraryGenre])
def library_genres(
    q: Optional[str] = Query(default=None, min_length=1, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    score_genres_q = db.query(
        TrackScore.genre,
        func.count(TrackScore.jellyfin_item_id).label("track_count"),
    ).filter(
        TrackScore.user_id == user.user_id,
        TrackScore.genre.isnot(None),
        TrackScore.genre != "",
    )
    if q:
        score_genres_q = score_genres_q.filter(TrackScore.genre.ilike(f"%{q}%"))
    score_counts = score_genres_q.group_by(TrackScore.genre).all()
    count_map = {r.genre: int(r.track_count or 0) for r in score_counts}

    profile_q = db.query(
        GenreProfile.genre,
        cast(GenreProfile.affinity_score, Float).label("affinity_score"),
    ).filter(GenreProfile.user_id == user.user_id)
    if q:
        profile_q = profile_q.filter(GenreProfile.genre.ilike(f"%{q}%"))
    profile_rows = profile_q.all()
    affinity_map = {r.genre: float(r.affinity_score or 0.0) for r in profile_rows}

    all_genres = set(count_map.keys()) | set(affinity_map.keys())
    if not all_genres:
        return []

    result = [
        MobileLibraryGenre(
            id=g,
            name=g,
            affinity_score=affinity_map.get(g, 0.0),
            track_count=count_map.get(g, 0),
        )
        for g in all_genres
    ]
    result.sort(key=lambda x: (x.affinity_score, x.track_count), reverse=True)
    return result[:limit]


@router.get("/library/tracks", response_model=list[MobileLibraryTrack])
def library_tracks(
    q: Optional[str] = Query(default=None, min_length=1, max_length=120),
    artist: Optional[str] = Query(default=None),
    album: Optional[str] = Query(default=None),
    genre: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
    sort: str = Query(default="personal"),
    limit: int = Query(default=250, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _try_jellyfin_connection(db)
    jellyfin_token = _try_active_jellyfin_user_token(user.user_id, db)

    query = (
        db.query(
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.track_name,
            LibraryTrack.artist_name,
            LibraryTrack.album_name,
            LibraryTrack.duration_ticks,
            LibraryTrack.bpm,
            LibraryTrack.energy,
            TrackScore.play_count,
            cast(TrackScore.artist_affinity, Float).label("artist_affinity"),
            TrackScore.global_popularity,
            cast(TrackScore.final_score, Float).label("final_score"),
        )
        .outerjoin(
            TrackScore,
            (TrackScore.jellyfin_item_id == LibraryTrack.jellyfin_item_id) &
            (TrackScore.user_id == user.user_id)
        )
        .filter(
            LibraryTrack.jellyfin_item_id.isnot(None),
            LibraryTrack.missing_since.is_(None),
        )
    )

    if q:
        query = query.filter(
            (LibraryTrack.track_name.ilike(f"%{q}%")) |
            (LibraryTrack.artist_name.ilike(f"%{q}%")) |
            (LibraryTrack.album_name.ilike(f"%{q}%"))
        )
    if artist:
        query = query.filter(LibraryTrack.artist_name == artist)
    if album:
        query = query.filter(LibraryTrack.album_name == album)
    if genre:
        query = query.filter(TrackScore.genre == genre)
    if year is not None:
        query = query.filter(LibraryTrack.year == year)

    if sort == "global":
        query = query.order_by(desc(func.coalesce(TrackScore.global_popularity, 0.0)), desc(func.coalesce(TrackScore.play_count, 0)))
    elif sort == "affinity":
        query = query.order_by(desc(func.coalesce(cast(TrackScore.artist_affinity, Float), 0.0)), desc(func.coalesce(TrackScore.play_count, 0)))
    elif sort == "plays":
        query = query.order_by(desc(func.coalesce(TrackScore.play_count, 0)))
    elif sort == "bpm":
        query = query.filter(LibraryTrack.bpm.isnot(None)).order_by(desc(LibraryTrack.bpm))
    elif sort == "energy":
        query = query.filter(LibraryTrack.energy.isnot(None)).order_by(desc(LibraryTrack.energy))
    elif sort == "acousticness":
        query = query.filter(LibraryTrack.acousticness.isnot(None)).order_by(desc(LibraryTrack.acousticness))
    elif sort == "hidden_gems":
        query = query.filter(
            (TrackScore.global_popularity.is_(None)) | (TrackScore.global_popularity < 40)
        ).order_by(desc(func.coalesce(cast(TrackScore.final_score, Float), 0.0)))
    else:
        query = query.order_by(desc(func.coalesce(TrackScore.play_count, 0)), desc(func.coalesce(cast(TrackScore.final_score, Float), 0.0)))

    rows = query.limit(limit).all()
    return [
        _build_library_track(r, base_url, user.user_id, jellyfin_token)
        for r in rows
        if r.jellyfin_item_id
    ]


@router.get("/library/artists/{artist_name}/tracks", response_model=list[MobileLibraryTrack])
def library_artist_tracks(
    artist_name: str,
    sort: str = Query(default="personal"),
    q: Optional[str] = Query(default=None, min_length=1, max_length=120),
    limit: int = Query(default=250, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return library_tracks(
        q=q,
        artist=artist_name,
        album=None,
        genre=None,
        year=None,
        sort=sort,
        limit=limit,
        user=user,
        db=db,
    )


@router.get("/library/artists/{artist_name}/detail", response_model=MobileArtistDetail)
def library_artist_detail(
    artist_name: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base_url = _try_jellyfin_connection(db)
    jellyfin_token = _try_active_jellyfin_user_token(user.user_id, db)

    lib_row = (
        db.query(
            func.max(LibraryTrack.jellyfin_artist_id).label("jellyfin_artist_id"),
            func.max(LibraryTrack.jellyfin_item_id).label("fallback_item_id"),
        )
        .filter(
            LibraryTrack.artist_name == artist_name,
            LibraryTrack.missing_since.is_(None),
        )
        .first()
    )

    image_url: Optional[str] = None
    if base_url and jellyfin_token and lib_row:
        if lib_row.jellyfin_artist_id:
            image_url = _image_url_for_item(base_url, jellyfin_token, lib_row.jellyfin_artist_id)
        elif lib_row.fallback_item_id:
            image_url = _image_url_for_item(base_url, jellyfin_token, lib_row.fallback_item_id)

    profile = db.query(ArtistProfile).filter_by(user_id=user.user_id, artist_name=artist_name).first()
    enrichment = db.query(ArtistEnrichment).filter_by(artist_name=artist_name).first()

    canonical_genres: list[MobileGenreWeight] = []
    if profile and profile.canonical_genres:
        try:
            raw = json.loads(profile.canonical_genres) if isinstance(profile.canonical_genres, str) else profile.canonical_genres
            if isinstance(raw, list):
                canonical_genres = [
                    MobileGenreWeight(genre=g["genre"], weight=float(g.get("weight", 0)))
                    for g in raw
                    if isinstance(g, dict) and "genre" in g
                ]
        except Exception:
            pass

    related_artists: list[MobileRelatedArtist] = []
    if profile and profile.related_artists:
        try:
            raw = json.loads(profile.related_artists) if isinstance(profile.related_artists, str) else profile.related_artists
            if isinstance(raw, list):
                for r in raw[:5]:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("name") or r.get("artist")
                    score = r.get("match", r.get("match_score", r.get("score", 0)))
                    if name:
                        related_artists.append(MobileRelatedArtist(name=str(name), match_score=float(score or 0)))
        except Exception:
            pass

    global_popularity: Optional[float] = None
    if enrichment and enrichment.popularity_score is not None:
        global_popularity = float(enrichment.popularity_score)
    else:
        avg = db.query(func.avg(TrackScore.global_popularity)).filter(
            TrackScore.user_id == user.user_id,
            TrackScore.artist_name == artist_name,
            TrackScore.global_popularity.isnot(None),
        ).scalar()
        if avg is not None:
            global_popularity = float(avg)

    return MobileArtistDetail(
        name=artist_name,
        image_url=image_url,
        affinity_score=float(profile.affinity_score) if profile else 0.0,
        global_popularity=global_popularity,
        trend_direction=enrichment.trend_direction if enrichment else None,
        biography=enrichment.biography if enrichment else None,
        canonical_genres=canonical_genres,
        related_artists=related_artists,
    )


@router.get("/library/years", response_model=list[MobileLibraryYear])
def library_years(
    limit: int = Query(default=200, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            LibraryTrack.year,
            func.count(LibraryTrack.jellyfin_item_id).label("track_count"),
        )
        .filter(
            LibraryTrack.year.isnot(None),
            LibraryTrack.year > 0,
            LibraryTrack.missing_since.is_(None),
        )
        .group_by(LibraryTrack.year)
        .order_by(LibraryTrack.year.desc())
        .limit(limit)
        .all()
    )
    return [MobileLibraryYear(year=r.year, track_count=int(r.track_count)) for r in rows]


@router.get("/library/years/{year}/tracks", response_model=list[MobileLibraryTrack])
def library_year_tracks(
    year: int,
    sort: str = Query(default="personal"),
    limit: int = Query(default=500, ge=1, le=1000),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return library_tracks(
        q=None, artist=None, album=None, genre=None,
        year=year, sort=sort, limit=limit,
        user=user, db=db,
    )


@router.get("/library/smart", response_model=list[MobileSmartCollection])
def library_smart_collections(
    user: UserContext = Depends(get_current_user),
):
    return _SMART_COLLECTIONS


@router.get("/library/smart/{collection_key}/tracks", response_model=list[MobileLibraryTrack])
def library_smart_tracks(
    collection_key: str,
    limit: int = Query(default=100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sort_map = {
        "top_played": "plays",
        "hidden_gems": "hidden_gems",
        "high_energy": "energy",
        "acoustic_chill": "acousticness",
        "fast_tempo": "bpm",
    }

    if collection_key in sort_map:
        return library_tracks(
            q=None, artist=None, album=None, genre=None, year=None,
            sort=sort_map[collection_key], limit=limit,
            user=user, db=db,
        )

    if collection_key == "rising_artists":
        return _library_rising_artist_tracks(limit=limit, user=user, db=db)

    raise HTTPException(status_code=404, detail=f"Unknown smart collection: {collection_key}")


def _library_rising_artist_tracks(
    limit: int,
    user: UserContext,
    db: Session,
) -> list[MobileLibraryTrack]:
    base_url = _try_jellyfin_connection(db)
    jellyfin_token = _try_active_jellyfin_user_token(user.user_id, db)

    rows = (
        db.query(
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.track_name,
            LibraryTrack.artist_name,
            LibraryTrack.album_name,
            LibraryTrack.duration_ticks,
            LibraryTrack.bpm,
            LibraryTrack.energy,
            TrackScore.play_count,
            cast(TrackScore.artist_affinity, Float).label("artist_affinity"),
            TrackScore.global_popularity,
            cast(TrackScore.final_score, Float).label("final_score"),
        )
        .outerjoin(
            TrackScore,
            (TrackScore.jellyfin_item_id == LibraryTrack.jellyfin_item_id) &
            (TrackScore.user_id == user.user_id)
        )
        .join(ArtistEnrichment, ArtistEnrichment.artist_name == LibraryTrack.artist_name)
        .filter(
            LibraryTrack.jellyfin_item_id.isnot(None),
            LibraryTrack.missing_since.is_(None),
            ArtistEnrichment.trend_direction == "rising",
        )
        .order_by(desc(func.coalesce(cast(TrackScore.artist_affinity, Float), 0.0)))
        .limit(limit)
        .all()
    )

    return [
        _build_library_track(r, base_url, user.user_id, jellyfin_token)
        for r in rows
        if r.jellyfin_item_id
    ]
