"""
JellyDJ — Playlist Import Router

Endpoints
─────────
  POST   /api/import/playlists                 Submit a URL or browser-extension payload
  GET    /api/import/playlists                 List all imported playlists for current user
  GET    /api/import/playlists/{id}            Detail: tracks + album suggestions
  PATCH  /api/import/playlists/{id}/rename     Rename an imported playlist (DB + Jellyfin)
  POST   /api/import/playlists/{id}/rematch    Re-run the match pass (after library update)
  DELETE /api/import/playlists/{id}            Remove import + delete Jellyfin playlist

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
    run_match_pass, write_jellyfin_playlist, _normalise as _norm_album,
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


class RenameRequest(BaseModel):
    name: str

    @validator("name")
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
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


async def _send_album_to_lidarr(
    artist: str,
    album: str,
    db: Session,
    artist_mbid: str | None = None,
) -> bool:
    """
    Add artist to Lidarr (if not present), find the specific album,
    monitor it, and trigger an AlbumSearch.

    Uses the same pattern as discovery.py: dynamic root folder / quality
    profile / metadata profile, monitor: "future" for new artists, then
    album-specific search.
    """
    import asyncio

    try:
        base_url, api_key = _get_lidarr_creds(db)
    except RuntimeError as exc:
        log.warning("Lidarr not configured: %s", exc)
        return False

    headers = {"X-Api-Key": api_key}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # ── Step 1: look up artist ────────────────────────────────
            # Prefer mbid for precise lookup when available
            search_term = f"lidarr:{artist_mbid}" if artist_mbid else artist
            search_resp = await client.get(
                f"{base_url}/api/v1/artist/lookup",
                headers=headers,
                params={"term": search_term},
            )
            if search_resp.status_code != 200 or not search_resp.json():
                # Retry with plain name if mbid lookup returned nothing
                if artist_mbid:
                    search_resp = await client.get(
                        f"{base_url}/api/v1/artist/lookup",
                        headers=headers,
                        params={"term": artist},
                    )
                if search_resp.status_code != 200 or not search_resp.json():
                    log.warning("Lidarr artist lookup failed for '%s'", artist)
                    return False

            lidarr_artist = search_resp.json()[0]
            foreign_artist_id = lidarr_artist.get("foreignArtistId", "")

            # ── Step 2: check if artist already exists ────────────────
            existing_resp = await client.get(
                f"{base_url}/api/v1/artist", headers=headers
            )
            existing_map = {}
            if existing_resp.status_code == 200:
                existing_map = {
                    a["foreignArtistId"]: a for a in existing_resp.json()
                }

            artist_already_exists = foreign_artist_id in existing_map
            lidarr_artist_id = None

            if artist_already_exists:
                lidarr_artist_id = existing_map[foreign_artist_id].get("id")
                log.info("Import: '%s' already in Lidarr (id=%s)", artist, lidarr_artist_id)
            else:
                # ── Step 3-6: add artist with dynamic profiles ────────
                root_resp = await client.get(f"{base_url}/api/v1/rootfolder", headers=headers)
                root_resp.raise_for_status()
                roots = root_resp.json()
                root_path = roots[0]["path"] if roots else "/music"

                qp_resp = await client.get(f"{base_url}/api/v1/qualityprofile", headers=headers)
                qp_resp.raise_for_status()
                profiles = qp_resp.json()
                quality_profile_id = next(
                    (p["id"] for p in profiles if p.get("name", "").lower() == "jellydj"),
                    profiles[0]["id"] if profiles else 1,
                )

                mp_resp = await client.get(f"{base_url}/api/v1/metadataprofile", headers=headers)
                mp_resp.raise_for_status()
                meta_profiles = mp_resp.json()
                metadata_profile_id = meta_profiles[0]["id"] if meta_profiles else 1

                # Strip all album monitoring from the lookup payload
                safe_artist = {**lidarr_artist}
                if "albums" in safe_artist:
                    safe_artist["albums"] = [
                        {**alb, "monitored": False}
                        for alb in safe_artist["albums"]
                    ]

                add_payload = {
                    **safe_artist,
                    "qualityProfileId": quality_profile_id,
                    "metadataProfileId": metadata_profile_id,
                    "rootFolderPath": root_path,
                    "monitored": True,
                    "monitor": "none",
                    "addOptions": {
                        "monitor": "none",
                        "searchForMissingAlbums": False,
                    },
                }
                add_resp = await client.post(
                    f"{base_url}/api/v1/artist", headers=headers, json=add_payload
                )
                add_resp.raise_for_status()
                added_artist = add_resp.json()
                lidarr_artist_id = added_artist.get("id")
                log.info("Import: added '%s' to Lidarr (id=%s)", artist, lidarr_artist_id)

            if not lidarr_artist_id:
                log.warning("Import: no Lidarr artist ID for '%s'", artist)
                return False

            # ── Step 7: refresh artist so Lidarr scans albums ─────────
            await client.post(
                f"{base_url}/api/v1/command",
                headers=headers,
                json={"name": "RefreshArtist", "artistId": lidarr_artist_id},
            )

            # ── Step 8: fetch albums with retry/backoff ───────────────
            albums = []
            max_attempts = 5 if not artist_already_exists else 2
            for attempt in range(max_attempts):
                if attempt > 0:
                    await asyncio.sleep(4)
                albums_resp = await client.get(
                    f"{base_url}/api/v1/album",
                    headers=headers,
                    params={"artistId": lidarr_artist_id},
                )
                if albums_resp.status_code == 200:
                    albums = albums_resp.json()
                    if albums:
                        break
                log.info("Import: waiting for albums (attempt %d/%d)…", attempt + 1, max_attempts)

            if not albums:
                log.warning("Import: no albums found for '%s' — skipping (will not search entire artist)", artist)
                return False

            # ── Step 9: score albums to find best match ───────────────
            # Strip placeholder values used when no album metadata was found
            clean_album = album or ""
            if clean_album in ("Unknown Album", "Artist not in Lidarr") or clean_album.endswith("(artist search)"):
                clean_album = ""
            target = clean_album.lower().strip()

            def _album_score(alb: dict) -> float:
                raw_title  = alb.get("title", "").lower().strip()
                norm_title = _norm_album(alb.get("title", ""))
                norm_tgt   = _norm_album(clean_album)
                if not target:
                    return 0.0
                # Exact match on raw or normalised forms
                if raw_title == target or norm_title == norm_tgt:
                    return 1.0
                # Normalised substring containment (handles "(1st album)", "(Remastered)", etc.)
                if norm_tgt and norm_title and (norm_tgt in norm_title or norm_title in norm_tgt):
                    # Prefer titles that are closer in length after normalisation
                    return 0.95 - abs(len(norm_title) - len(norm_tgt)) * 0.01
                # Raw substring containment (fallback for titles that survive normalisation)
                if target in raw_title:
                    return 0.85 - (len(raw_title) - len(target)) * 0.01
                if raw_title in target:
                    return 0.75
                # Word-overlap on normalised tokens
                t_words = set(norm_tgt.split())
                a_words = set(norm_title.split())
                overlap = len(t_words & a_words)
                if t_words and a_words and overlap:
                    return 0.5 + (overlap / max(len(t_words), len(a_words))) * 0.3
                return 0.0

            match = None
            if target:
                scored = [(alb, _album_score(alb)) for alb in albums]
                scored.sort(key=lambda x: x[1], reverse=True)
                best_score = scored[0][1]
                match = scored[0][0] if best_score > 0.3 else None
                if match:
                    log.info("Import: album match '%s' (score=%.2f) for target '%s'",
                             match.get("title"), best_score, album)
                else:
                    log.info("Import: no album matched for '%s' (best score=%.2f), available: %s",
                             album, best_score,
                             [a.get("title") for a in albums[:5]])

            if not match:
                log.warning("Import: no specific album match for '%s' by '%s' — skipping (will not download random album)", album, artist)
                return False

            # ── Step 10: monitor the specific album ───────────────────
            album_id = match["id"]
            match["monitored"] = True
            put_resp = await client.put(
                f"{base_url}/api/v1/album/{album_id}",
                headers=headers,
                json=match,
            )
            log.info("Import: monitor album PUT '%s' (id=%s): HTTP %d",
                      match.get("title"), album_id, put_resp.status_code)

            if put_resp.status_code not in (200, 202):
                # If PUT failed, try updating just the monitored flag via the
                # simpler monitor endpoint that some Lidarr versions support
                log.warning("Import: album PUT failed, trying PATCH approach")
                await client.put(
                    f"{base_url}/api/v1/album/monitor",
                    headers=headers,
                    json={"albumIds": [album_id], "monitored": True},
                )

            # ── Step 11: trigger album-specific search ────────────────
            cmd_resp = await client.post(
                f"{base_url}/api/v1/command",
                headers=headers,
                json={"name": "AlbumSearch", "albumIds": [album_id]},
            )
            log.info("Import: AlbumSearch for '%s' (id=%s): HTTP %d",
                      match.get("title"), album_id, cmd_resp.status_code)

            if cmd_resp.status_code not in (200, 201):
                log.warning("Import: AlbumSearch failed for '%s' (HTTP %d) — will not fall back to artist search",
                            match.get("title"), cmd_resp.status_code)

            log.info("Import: queued album '%s' by '%s' in Lidarr", match.get("title"), artist)
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


async def _rename_jellyfin_playlist(
    pl: ImportedPlaylist,
    new_name: str,
    db: Session,
) -> tuple[bool, str]:
    """
    Rename a Jellyfin playlist by recreating it with the new name.

    Jellyfin playlist names are tied to the folder/file Jellyfin created on
    disk; ``POST /Items/{id}`` is silently ignored for playlists.  The only
    reliable rename is: create a new playlist with the correct name and all
    current matched tracks, update our DB record with the new Jellyfin ID,
    then delete the old playlist.

    Returns ``(True, "")`` on success, ``(False, detail)`` on failure.
    Never raises.
    """
    conn = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not conn or not conn.base_url:
        return False, "Jellyfin not configured"

    base_url   = conn.base_url.rstrip("/")
    api_key    = decrypt(conn.api_key_encrypted)
    headers    = {"X-Emby-Token": api_key, "Content-Type": "application/json"}
    old_jf_id  = pl.jellyfin_playlist_id

    # Collect matched track IDs in playlist order
    matched = (
        db.query(ImportedPlaylistTrack)
        .filter_by(playlist_id=pl.id, match_status="matched")
        .order_by(ImportedPlaylistTrack.position)
        .all()
    )
    item_ids = [t.matched_item_id for t in matched if t.matched_item_id]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1 — create new playlist with the new name
            create_resp = await client.post(
                f"{base_url}/Playlists",
                headers=headers,
                json={
                    "Name":      new_name,
                    "Ids":       ",".join(item_ids) if item_ids else "",
                    "UserId":    pl.owner_user_id,
                    "MediaType": "Audio",
                },
            )
            if create_resp.status_code not in (200, 201):
                log.error(
                    "Jellyfin create-for-rename failed (%s): %s",
                    create_resp.status_code, create_resp.text[:300],
                )
                return False, f"Jellyfin create failed: HTTP {create_resp.status_code}"

            new_jf_id = create_resp.json().get("Id")
            if not new_jf_id:
                return False, "Jellyfin returned no playlist ID after create"

            # Step 2 — persist the new Jellyfin ID before attempting delete
            pl.jellyfin_playlist_id = new_jf_id
            db.commit()

            # Step 3 — delete old playlist (best-effort; failure is non-fatal)
            if old_jf_id:
                del_resp = await client.delete(
                    f"{base_url}/Items/{old_jf_id}", headers=headers
                )
                if del_resp.status_code not in (200, 204):
                    log.warning(
                        "Jellyfin delete of old playlist %s returned %s — ignored",
                        old_jf_id, del_resp.status_code,
                    )

    except Exception as exc:
        log.error("Jellyfin rename-by-recreate raised: %s", exc)
        return False, str(exc)

    return True, ""


# ── Background job ────────────────────────────────────────────────────────────

async def _run_full_import(playlist_id: int, owner_user_id: str):
    """
    Background task: match tracks, build suggestions, create Jellyfin playlist.
    Runs after the ImportedPlaylist and ImportedPlaylistTrack rows exist.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        log.info("Import job starting for playlist %d", playlist_id)
        run_match_pass(playlist_id, db)
        await build_album_suggestions(playlist_id, db)
        await write_jellyfin_playlist(playlist_id, owner_user_id, db)

        # Mark playlist active
        pl = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
        if pl:
            pl.status = "active"
            db.commit()
        log.info("Import job completed for playlist %d", playlist_id)
    except Exception as exc:
        log.exception("Import job failed for playlist %d: %s", playlist_id, exc)
        try:
            db2 = SessionLocal()
            pl = db2.query(ImportedPlaylist).filter_by(id=playlist_id).first()
            if pl:
                pl.status = "error"
                db2.commit()
            db2.close()
        except Exception:
            pass
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
        playlist_name = (payload.playlist_name or "Imported Playlist") + f" - {current_user.username}"
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
        playlist_name = meta["name"] + f" - {current_user.username}"
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


@router.patch("/playlists/{playlist_id}/rename")
async def rename_imported_playlist(
    playlist_id: int,
    payload: RenameRequest,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename an imported playlist in the DB and, if already in Jellyfin, there too."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    pl.name = payload.name
    db.commit()

    jf_ok = True
    jf_error = ""
    if pl.jellyfin_playlist_id:
        jf_ok, jf_error = await _rename_jellyfin_playlist(pl, payload.name, db)

    result = _format_playlist(pl)
    result["jellyfin_synced"] = jf_ok
    if jf_error:
        result["jellyfin_error"] = jf_error
    return result


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

    # Signal the frontend that work is in progress
    pl.status = "matching"
    db.commit()

    background_tasks.add_task(_run_full_import, playlist_id, current_user.user_id)
    return {"ok": True, "message": "Re-match started in background."}


# ── Manual match helpers ───────────────────────────────────────────────────────

class ManualMatchBody(BaseModel):
    track_name: str
    library_item_id: str


async def _rebuild_suggestions_only(playlist_id: int):
    """Background task: rebuild album suggestions after a manual match."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        await build_album_suggestions(playlist_id, db)
        pl = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
        if pl:
            await write_jellyfin_playlist(playlist_id, pl.owner_user_id, db)
    except Exception as exc:
        log.exception("Manual match rebuild failed for playlist %d: %s", playlist_id, exc)
    finally:
        db.close()


@router.get("/playlists/{playlist_id}/library-search")
def library_search(
    playlist_id: int,
    q: str = Query(""),
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search library tracks by name — used by the manual match modal."""
    from models import LibraryTrack
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")
    if not q.strip():
        return []
    results = (
        db.query(LibraryTrack)
        .filter(
            LibraryTrack.track_name.ilike(f"%{q}%"),
            LibraryTrack.missing_since.is_(None),
        )
        .order_by(LibraryTrack.track_name)
        .limit(25)
        .all()
    )
    return [
        {
            "item_id":    r.jellyfin_item_id,
            "track_name": r.track_name,
            "artist_name": r.artist_name,
            "album_name": r.album_name or "",
        }
        for r in results
    ]


@router.post("/playlists/{playlist_id}/tracks/manual-match")
async def manual_match_track(
    playlist_id: int,
    body: ManualMatchBody,
    background_tasks: BackgroundTasks,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually link an import track to a library track, bypassing fuzzy matching."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    tracks = (
        db.query(ImportedPlaylistTrack)
        .filter_by(playlist_id=playlist_id, track_name=body.track_name)
        .filter(ImportedPlaylistTrack.match_status != "matched")
        .all()
    )
    if not tracks:
        raise HTTPException(404, "Track not found or already matched")

    for t in tracks:
        t.match_status = "matched"
        t.matched_item_id = body.library_item_id
        t.match_score = 1.0
        t.resolved_at = datetime.utcnow()

    pl.matched_count = (
        db.query(ImportedPlaylistTrack)
        .filter_by(playlist_id=playlist_id, match_status="matched")
        .count()
        + len(tracks)  # optimistic — commits below confirm
    )
    db.commit()

    # Rebuild suggestions and re-write playlist in background
    background_tasks.add_task(_rebuild_suggestions_only, playlist_id)
    return {"matched": len(tracks)}


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_imported_playlist(
    playlist_id: int,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove import metadata AND delete the Jellyfin playlist."""
    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    # Delete the Jellyfin playlist if one was created
    if pl.jellyfin_playlist_id:
        try:
            conn = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
            if conn and conn.base_url and conn.api_key_encrypted:
                base_url = conn.base_url.rstrip("/")
                api_key = decrypt(conn.api_key_encrypted)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.delete(
                        f"{base_url}/Items/{pl.jellyfin_playlist_id}",
                        headers={"X-Emby-Token": api_key},
                    )
                    if resp.status_code in (200, 204):
                        log.info("Deleted Jellyfin playlist %s for import %d",
                                 pl.jellyfin_playlist_id, playlist_id)
                    else:
                        log.warning("Jellyfin DELETE /Items/%s returned %d",
                                    pl.jellyfin_playlist_id, resp.status_code)
        except Exception as exc:
            log.warning("Failed to delete Jellyfin playlist for import %d: %s",
                        playlist_id, exc)

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

    import json as _json

    result = []
    for s in suggestions:
        # Parse missing_tracks JSON
        try:
            tracks_list = _json.loads(s.missing_tracks) if s.missing_tracks else []
        except (ValueError, TypeError):
            tracks_list = []

        result.append({
            "id":             s.id,
            "artist_name":    s.artist_name,
            "album_name":     s.album_name,
            "coverage_count": s.coverage_count,
            "lidarr_status":  s.lidarr_status,
            "lidarr_queued_at": s.lidarr_queued_at,
            "artist_mbid":    s.artist_mbid,
            "image_url":      s.image_url,
            "missing_tracks": tracks_list,
        })
    return result


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
    ok = await _send_album_to_lidarr(
        suggestion.artist_name,
        suggestion.album_name,
        db,
        artist_mbid=suggestion.artist_mbid,
    )

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


# ── Add missing artists to Lidarr ─────────────────────────────────────────────

@router.post("/playlists/{playlist_id}/add-artists", status_code=202)
async def add_missing_artists(
    playlist_id: int,
    background_tasks: BackgroundTasks,
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Add all 'Artist not in Lidarr' artists to Lidarr (monitor: none),
    then re-run album suggestion builder so suggestions refresh with
    real Lidarr albums.
    """
    import asyncio

    pl = db.query(ImportedPlaylist).filter_by(
        id=playlist_id, owner_user_id=current_user.user_id
    ).first()
    if not pl:
        raise HTTPException(404, "Playlist not found")

    # Find all "Artist not in Lidarr" suggestions
    not_in_lidarr = (
        db.query(ImportAlbumSuggestion)
        .filter_by(playlist_id=playlist_id, album_name="Artist not in Lidarr")
        .all()
    )

    if not not_in_lidarr:
        return {"ok": True, "added": 0, "message": "All artists already in Lidarr."}

    artist_names = [(s.artist_name, s.artist_mbid) for s in not_in_lidarr]

    async def _add_artists_and_rebuild():
        from database import SessionLocal
        db2 = SessionLocal()
        try:
            base_url, api_key = _get_lidarr_creds(db2)
            headers = {"X-Api-Key": api_key}

            # Fetch profiles once
            async with httpx.AsyncClient(timeout=30.0) as client:
                root_resp = await client.get(f"{base_url}/api/v1/rootfolder", headers=headers)
                roots = root_resp.json() if root_resp.status_code == 200 else []
                root_path = roots[0]["path"] if roots else "/music"

                qp_resp = await client.get(f"{base_url}/api/v1/qualityprofile", headers=headers)
                profiles = qp_resp.json() if qp_resp.status_code == 200 else []
                quality_profile_id = next(
                    (p["id"] for p in profiles if p.get("name", "").lower() == "jellydj"),
                    profiles[0]["id"] if profiles else 1,
                )

                mp_resp = await client.get(f"{base_url}/api/v1/metadataprofile", headers=headers)
                meta_profiles = mp_resp.json() if mp_resp.status_code == 200 else []
                metadata_profile_id = meta_profiles[0]["id"] if meta_profiles else 1

                # Pre-fetch existing artists ONCE
                existing_resp = await client.get(f"{base_url}/api/v1/artist", headers=headers)
                existing_ids = set()
                if existing_resp.status_code == 200:
                    existing_ids = {a["foreignArtistId"] for a in existing_resp.json()}

                added = 0
                for artist_name, artist_mbid in artist_names:
                    try:
                        # Quick check: if we have mbid from enrichment, check directly
                        if artist_mbid and artist_mbid in existing_ids:
                            log.info("Import: '%s' already in Lidarr (mbid match), skipping", artist_name)
                            continue

                        # Look up artist in Lidarr's metadata
                        search_term = f"lidarr:{artist_mbid}" if artist_mbid else artist_name
                        search_resp = await client.get(
                            f"{base_url}/api/v1/artist/lookup",
                            headers=headers,
                            params={"term": search_term},
                        )
                        if search_resp.status_code != 200 or not search_resp.json():
                            if artist_mbid:
                                search_resp = await client.get(
                                    f"{base_url}/api/v1/artist/lookup",
                                    headers=headers,
                                    params={"term": artist_name},
                                )
                            if search_resp.status_code != 200 or not search_resp.json():
                                log.warning("Import: artist lookup failed for '%s'", artist_name)
                                continue

                        lidarr_artist = search_resp.json()[0]

                        # Check foreignArtistId against existing
                        if lidarr_artist.get("foreignArtistId") in existing_ids:
                            log.info("Import: '%s' already in Lidarr (foreignId match), skipping", artist_name)
                            continue

                        # Strip all album monitoring from the lookup payload
                        safe_artist = {**lidarr_artist}
                        if "albums" in safe_artist:
                            safe_artist["albums"] = [
                                {**alb, "monitored": False}
                                for alb in safe_artist["albums"]
                            ]

                        add_payload = {
                            **safe_artist,
                            "qualityProfileId": quality_profile_id,
                            "metadataProfileId": metadata_profile_id,
                            "rootFolderPath": root_path,
                            "monitored": True,
                            "monitor": "none",
                            "addOptions": {
                                "monitor": "none",
                                "searchForMissingAlbums": False,
                            },
                        }
                        add_resp = await client.post(
                            f"{base_url}/api/v1/artist", headers=headers, json=add_payload
                        )
                        if add_resp.status_code in (200, 201):
                            added += 1
                            log.info("Import: added '%s' to Lidarr (monitor: none)", artist_name)
                        else:
                            log.warning("Import: failed to add '%s': HTTP %d", artist_name, add_resp.status_code)

                        # Small delay to avoid hammering Lidarr
                        await asyncio.sleep(1)
                    except Exception as exc:
                        log.error("Import: error adding '%s' to Lidarr: %s", artist_name, exc)

            log.info("Import: added %d/%d artists to Lidarr, rebuilding suggestions…", added, len(artist_names))

            # Wait for Lidarr to index the new artists
            if added > 0:
                await asyncio.sleep(5)

            # Rebuild suggestions with the new artists now available
            await build_album_suggestions(playlist_id, db2)

        except Exception as exc:
            log.error("Import: add-artists job failed: %s", exc)
        finally:
            db2.close()

    background_tasks.add_task(_add_artists_and_rebuild)

    return {
        "ok": True,
        "adding": len(artist_names),
        "message": f"Adding {len(artist_names)} artist(s) to Lidarr and rebuilding suggestions…",
    }


# ── API Key Verification (used by browser extension) ─────────────────────────

@router.get("/verify")
def verify_api_key(
    current_user: UserContext = Depends(get_user_from_api_key_or_jwt),
):
    """Lightweight endpoint for the browser extension to validate its API key."""
    return {"ok": True, "username": current_user.username}


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
