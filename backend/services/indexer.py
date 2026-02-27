
"""
Jellyfin play history indexer.

For each managed user:
1. Fetches all played music items from Jellyfin (/Users/{id}/Items)
2. Upserts into the `plays` table
3. Rebuilds the `user_taste_profile` table with affinity scores
4. Updates `user_sync_status` for the dashboard widget
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    ConnectionSettings, ManagedUser, Play,
    UserTasteProfile, UserSyncStatus,
)
# Module 8a: new scoring foundation
from services.library_scanner import run_library_scan
from services.scoring_engine import rebuild_all_scores
from services.events import log_event
from crypto import decrypt

log = logging.getLogger(__name__)

# Weighting constants for affinity score calculation
# Legacy taste-profile weights (used by _rebuild_taste_profile below).
# The primary scoring path is scoring_engine.py → rebuild_track_scores();
# this path is kept as a fallback for users who haven't run a full index yet.
W_PLAY_COUNT   = 0.50   # raw play frequency
W_RECENCY      = 0.30   # how recently played
W_FAVORITE     = 0.20   # explicit favorite flag (legacy path — scoring_engine.py has full logic)

# ── In-memory job state tracker ──────────────────────────────────────────────
# Lightweight: no DB writes, just lets the frontend poll while a run is in progress.
#
# Why asyncio.Lock instead of threading.Lock?
# run_full_index() is an async coroutine running on the asyncio event loop.
# asyncio.ensure_future() (used by the API endpoints) queues multiple coroutines
# on the same event loop. Between the guard check and the state update, the
# event loop can yield and let another queued coroutine past the guard — a
# classic TOCTOU race. asyncio.Lock prevents this: the lock is held across
# both the check AND the set, so only one coroutine can enter at a time.
# (threading.Lock would deadlock here because async code can't block threads.)
import asyncio as _asyncio
from datetime import datetime as _dt

# Module-level lock — created once, reused for every run_full_index call
_index_lock: _asyncio.Lock | None = None

def _get_index_lock() -> _asyncio.Lock:
    """Lazily create the lock on first access (must be on the event loop)."""
    global _index_lock
    if _index_lock is None:
        _index_lock = _asyncio.Lock()
    return _index_lock

_job_state: dict = {
    "running":    False,
    "phase":      "",
    "detail":     "",
    "percent":    0,
    "started_at": None,
    "finished_at": None,
    "error":      None,
}

def get_job_state() -> dict:
    """Safe to call from any context — just a dict copy, no lock needed."""
    return dict(_job_state)

def _set_job(running: bool, phase: str = "", detail: str = "",
             percent: int = 0, error: str = None):
    _job_state["running"]  = running
    _job_state["phase"]    = phase
    _job_state["detail"]   = detail
    _job_state["percent"]  = percent
    _job_state["error"]    = error
    if running and not _job_state["started_at"]:
        _job_state["started_at"]  = _dt.utcnow().isoformat()
        _job_state["finished_at"] = None
    if not running:
        _job_state["finished_at"] = _dt.utcnow().isoformat()
        _job_state["started_at"]  = None



def _get_jellyfin_creds(db: Session) -> tuple[str, str]:
    """Return (base_url, api_key) or raise if not configured."""
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


def _jellyfin_headers(api_key: str) -> dict:
    return {"X-Emby-Token": api_key}


async def _fetch_played_items(
    base_url: str, api_key: str, user_id: str
) -> list[dict]:
    """
    Fetch all played music items for a user.
    Uses IsPlayed=true filter, sorted by DatePlayed descending.
    Pages through results in batches of 500.
    """
    headers = _jellyfin_headers(api_key)
    all_items: list[dict] = []
    start_index = 0
    limit = 500

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {
                "IncludeItemTypes": "Audio",
                "IsPlayed": "true",
                "Recursive": "true",
                "SortBy": "DatePlayed",
                "SortOrder": "Descending",
                # UserData includes PlayCount, IsFavorite, LastPlayedDate per user
            "Fields": "DateCreated,Genres,UserData,AlbumArtist,Album,ParentId",
                "StartIndex": start_index,
                "Limit": limit,
            }
            try:
                resp = await client.get(
                    f"{base_url}/Users/{user_id}/Items",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warning(f"Jellyfin fetch error for user {user_id}: {e}")
                break

            items = data.get("Items", [])
            all_items.extend(items)

            total = data.get("TotalRecordCount", 0)
            start_index += limit
            if start_index >= total or not items:
                break

    return all_items


def _parse_last_played(item: dict) -> Optional[datetime]:
    user_data = item.get("UserData", {})
    raw = user_data.get("LastPlayedDate")
    if not raw:
        return None
    try:
        # Jellyfin returns ISO 8601 with Z suffix
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _upsert_play(db: Session, user_id: str, item: dict):
    """Insert or update a single play record."""
    jellyfin_id = item.get("Id", "")
    if not jellyfin_id:
        return

    user_data = item.get("UserData", {})
    play_count = user_data.get("PlayCount", 0) or 0
    is_favorite = user_data.get("IsFavorite", False)
    last_played = _parse_last_played(item)

    # Primary genre
    genres = item.get("Genres", [])
    genre = genres[0] if genres else ""

    existing = db.query(Play).filter_by(
        user_id=user_id, jellyfin_item_id=jellyfin_id
    ).first()

    if existing:
        existing.play_count = play_count
        existing.last_played = last_played
        existing.is_favorite = is_favorite
        existing.genre = genre
        existing.track_name = item.get("Name", existing.track_name)
        existing.artist_name = item.get("AlbumArtist", "") or item.get("Artists", [""])[0] if item.get("Artists") else existing.artist_name
        existing.album_name = item.get("Album", existing.album_name)
        existing.synced_at = datetime.utcnow()
    else:
        artist = item.get("AlbumArtist", "")
        if not artist and item.get("Artists"):
            artist = item["Artists"][0]
        db.add(Play(
            user_id=user_id,
            jellyfin_item_id=jellyfin_id,
            track_name=item.get("Name", ""),
            artist_name=artist,
            album_name=item.get("Album", ""),
            genre=genre,
            play_count=play_count,
            last_played=last_played,
            is_favorite=is_favorite,
        ))


def _rebuild_taste_profile(db: Session, user_id: str):
    """
    Rebuild affinity scores for this user from their play data.

    Affinity score formula (0.0–100.0):
      - play_score:    log-scaled play count normalised to max plays
      - recency_score: 1.0 if played in last 30d, decaying to 0 at 365d
      - favorite_score: 1.0 if any track by this artist/genre is favorited

    Final = (W_PLAY_COUNT * play_score +
             W_RECENCY    * recency_score +
             W_FAVORITE   * favorite_score) * 100
    """
    plays = db.query(Play).filter_by(user_id=user_id).all()
    if not plays:
        return

    now = datetime.utcnow()
    max_plays = max((p.play_count for p in plays), default=1) or 1

    # Aggregate by artist
    artist_data: dict[str, dict] = {}
    genre_data: dict[str, dict] = {}

    for p in plays:
        if not p.play_count:
            continue

        play_score = math.log1p(p.play_count) / math.log1p(max_plays)

        days_ago = (now - p.last_played).days if p.last_played else 365
        recency_score = max(0.0, 1.0 - (days_ago / 365.0))

        fav = 1.0 if p.is_favorite else 0.0

        def _merge(bucket: dict, key: str):
            if not key:
                return
            if key not in bucket:
                bucket[key] = {"play": 0.0, "recency": 0.0, "fav": 0.0, "count": 0}
            b = bucket[key]
            # Running average for play/recency, max for fav
            b["count"] += 1
            b["play"] += play_score
            b["recency"] += recency_score
            b["fav"] = max(b["fav"], fav)

        _merge(artist_data, p.artist_name)
        _merge(genre_data, p.genre)

    def _score(b: dict) -> float:
        n = b["count"]
        avg_play = b["play"] / n
        avg_recency = b["recency"] / n
        raw = (W_PLAY_COUNT * avg_play + W_RECENCY * avg_recency) * 100
        if b["fav"]:
            # Favorites: additive boost + floor — mirrors scoring_engine.py logic
            raw = min(100.0, raw + 18.0)
            raw = max(raw, 82.0)
        return round(raw, 3)

    # Delete existing profile for this user
    db.query(UserTasteProfile).filter_by(user_id=user_id).delete()

    for artist, b in artist_data.items():
        db.add(UserTasteProfile(
            user_id=user_id,
            artist_name=artist,
            genre=None,
            affinity_score=str(_score(b)),
            updated_at=now,
        ))

    for genre, b in genre_data.items():
        db.add(UserTasteProfile(
            user_id=user_id,
            artist_name=None,
            genre=genre,
            affinity_score=str(_score(b)),
            updated_at=now,
        ))

    # Apply skip penalties to taste profile scores
    # Artists/genres with high skip rates get their affinity score dampened
    from models import SkipPenalty
    penalties = db.query(SkipPenalty).filter_by(user_id=user_id).all()

    # Build per-artist and per-genre aggregate skip penalties
    artist_penalties: dict[str, list[float]] = {}
    genre_penalties:  dict[str, list[float]] = {}
    for p in penalties:
        pen = float(p.penalty)
        if pen > 0:
            if p.artist_name:
                artist_penalties.setdefault(p.artist_name, []).append(pen)
            if p.genre:
                genre_penalties.setdefault(p.genre, []).append(pen)

    # Apply average penalty to matching taste profile rows
    profile_rows = db.query(UserTasteProfile).filter_by(user_id=user_id).all()
    # Build set of artists/genres with at least one favorited track
    fav_artists = {p.artist_name for p in plays if p.is_favorite and p.artist_name}
    fav_genres  = {p.genre for p in plays if p.is_favorite and p.genre}

    for row in profile_rows:
        if row.artist_name and row.artist_name in artist_penalties:
            avg_pen = sum(artist_penalties[row.artist_name]) / len(artist_penalties[row.artist_name])
            # Shield favorited artists: cap effective penalty
            if row.artist_name in fav_artists:
                avg_pen = min(avg_pen, 0.25)
            original = float(row.affinity_score)
            row.affinity_score = str(round(original * (1.0 - avg_pen), 4))
        elif row.genre and row.genre in genre_penalties:
            avg_pen = sum(genre_penalties[row.genre]) / len(genre_penalties[row.genre])
            if row.genre in fav_genres:
                avg_pen = min(avg_pen, 0.25)
            original = float(row.affinity_score)
            row.affinity_score = str(round(original * (1.0 - avg_pen), 4))

    db.commit()


async def index_user(base_url: str, api_key: str, user: ManagedUser, db: Session):
    """Full index run for a single user."""
    log.info(f"Indexing play history for user: {user.username}")
    try:
        items = await _fetch_played_items(base_url, api_key, user.jellyfin_user_id)
        log.info(f"  Fetched {len(items)} played items for {user.username}")

        for item in items:
            _upsert_play(db, user.jellyfin_user_id, item)
        db.commit()

        # Legacy taste profile (kept for discovery queue compatibility until Module 8b)
        _rebuild_taste_profile(db, user.jellyfin_user_id)

        # Module 8a: rebuild artist/genre profiles and pre-computed track scores
        rebuild_all_scores(db, user.jellyfin_user_id)

        # Pre-warm popularity + similarity cache for the recommendation engine
        await warm_popularity_cache(user.jellyfin_user_id, db)

        # Update sync status
        status_row = db.query(UserSyncStatus).filter_by(
            user_id=user.jellyfin_user_id
        ).first()
        if not status_row:
            status_row = UserSyncStatus(user_id=user.jellyfin_user_id)
            db.add(status_row)
        status_row.username = user.username
        status_row.last_synced = datetime.utcnow()
        status_row.tracks_indexed = db.query(Play).filter_by(
            user_id=user.jellyfin_user_id
        ).count()
        status_row.status = "ok"
        db.commit()

        log.info(f"  Done indexing {user.username}: {status_row.tracks_indexed} tracks")
        log_event(db, "index_complete",
                  f"Indexed {user.username}: {status_row.tracks_indexed} tracks in library")

    except Exception as e:
        log.error(f"  Index failed for {user.username}: {e}")
        log_event(db, "index_error", f"Index failed for {user.username}: {e}")
        status_row = db.query(UserSyncStatus).filter_by(
            user_id=user.jellyfin_user_id
        ).first()
        if not status_row:
            status_row = UserSyncStatus(user_id=user.jellyfin_user_id)
            db.add(status_row)
        status_row.username = user.username
        status_row.status = "error"
        db.commit()
        raise


async def _sync_usernames(base_url: str, api_key: str, db: Session):
    """
    Fetch all Jellyfin users and update stored usernames.
    Fixes cases where username was stored as jellyfin_user_id (UUID) by mistake.
    """
    import re
    UUID_RE = re.compile(r'^[0-9a-f]{32}$', re.IGNORECASE)
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{base_url}/Users", headers=_jellyfin_headers(api_key))
        resp.raise_for_status()
        jf_users = {u["Id"]: u["Name"] for u in resp.json()}
    updated = 0
    for user in db.query(ManagedUser).all():
        real_name = jf_users.get(user.jellyfin_user_id)
        if real_name and (user.username != real_name):
            log.info(f"  Correcting username: '{user.username}' -> '{real_name}'")
            user.username = real_name
            updated += 1
    if updated:
        db.commit()
        log.info(f"  Updated {updated} username(s)")


async def run_full_index():
    """
    Entry point called by APScheduler.
    Opens its own DB session (scheduler runs in a thread pool).
    Runs library scan first, then per-user play history + scoring.
    """
    # Atomic acquire — only one coroutine can pass this at a time.
    # asyncio.Lock.acquire() is a coroutine: it yields to the event loop
    # while waiting, so other requests stay responsive. Once acquired,
    # no other call to run_full_index() can enter until we release below.
    lock = _get_index_lock()
    if lock.locked():
        log.warning("Index already running — skipping duplicate trigger.")
        return
    await lock.acquire()
    db = None
    try:
        _set_job(True, "Connecting", "Reaching Jellyfin server…", 2)
        db = SessionLocal()
        base_url, api_key = _get_jellyfin_creds(db)
        users = db.query(ManagedUser).filter_by(is_enabled=True).all()

        if not users:
            log.info("No managed users enabled — skipping index.")
            _set_job(False, "Done", "No enabled users configured", 100)
            return  # finally block will release the lock

        n_users = len(users)

        # Step 1: Full library scan (all tracks, played or not)
        _set_job(True, "Scanning library", "Fetching all tracks from Jellyfin…", 8)
        log.info("Step 1: Running full library scan...")
        scan_result = await run_library_scan(db)
        if scan_result.get("ok"):
            n_tracks  = scan_result.get("total_in_db", 0)
            n_added   = scan_result.get("added", 0)
            n_missing = scan_result.get("marked_missing", 0)
            log.info(f"  Library scan: {n_tracks} tracks (+{n_added} new, {n_missing} missing)")
            _set_job(True, "Library scanned",
                     f"{n_tracks:,} tracks (+{n_added} new{f', {n_missing} removed' if n_missing else ''})",
                     18)
        else:
            log.warning(f"  Library scan failed: {scan_result.get('error')} — continuing")
            _set_job(True, "Library scan failed", "Continuing with play history…", 18)

        # Step 1.5: Sync usernames from Jellyfin
        _set_job(True, "Syncing users", f"Checking {n_users} user account(s)…", 22)
        try:
            await _sync_usernames(base_url, api_key, db)
        except Exception as e:
            log.warning(f"  Username sync failed (non-fatal): {e}")

        # Step 2: Per-user play history + scoring
        log.info(f"Step 2: Indexing play history for {n_users} user(s).")
        user_ids = [(u.jellyfin_user_id, u.username, u) for u in users]
        db.close()
        db = None

        for i, (uid, uname, user) in enumerate(user_ids):
            pct = 28 + int((i / n_users) * 40)
            _set_job(True, f"Play history — {uname}",
                     f"Fetching listened tracks ({i+1} of {n_users} users)…", pct)
            user_db = SessionLocal()
            try:
                from models import ManagedUser as MU
                fresh_user = user_db.query(MU).filter_by(jellyfin_user_id=uid).first()
                if fresh_user:
                    await index_user(base_url, api_key, fresh_user, user_db)
            except Exception as e:
                log.error(f"  User {uname} index failed (continuing): {e}")
            finally:
                user_db.close()

        _set_job(True, "Rebuilding taste profiles",
                 "Calculating affinity scores for all users…", 72)

        # Update last_index timestamp on AutomationSettings
        ts_db = SessionLocal()
        try:
            from models import AutomationSettings
            s = ts_db.query(AutomationSettings).first()
            if not s:
                s = AutomationSettings()
                ts_db.add(s)
            s.last_index = datetime.utcnow()
            ts_db.commit()
        except Exception:
            pass
        finally:
            ts_db.close()

        log.info("Full index complete.")
        _set_job(False, "Complete", f"Indexed {n_users} user(s) · popularity cache refreshing in background", 100)
    except Exception as e:
        log.error(f"Full index run failed: {e}")
        _set_job(False, "Error", str(e)[:120], 0, error=str(e))
    finally:
        if db is not None:
            db.close()
        lock.release()  # always release so the next run can proceed


# ── Global cache refresh state (for progress polling) ─────────────────────────
_cache_refresh_state: dict = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}

def get_cache_refresh_state() -> dict:
    return dict(_cache_refresh_state)

def _set_cache_state(**kwargs):
    _cache_refresh_state.update(kwargs)


async def warm_popularity_cache(user_id: str, db: Session, top_n: int = 20):
    """
    Shim kept for call-site compatibility.
    Fires the full library cache refresh as a non-blocking background thread.
    Returns immediately — index continues, dashboard stays responsive.
    """
    if _cache_refresh_state.get("running"):
        log.info("  Cache refresh already running — skipping duplicate trigger")
        return
    # Fire and forget — don't await, don't block
    import threading
    t = threading.Thread(
        target=_run_cache_refresh_sync,
        args=(db,),
        daemon=True,
        name="popularity-cache-refresh",
    )
    t.start()
    log.info("  Cache refresh started in background thread — index continuing immediately")


async def refresh_library_popularity_cache(db: Session):
    """
    Async entry point for the manual trigger endpoint.
    Fires the refresh in a background thread and returns immediately.
    """
    if _cache_refresh_state.get("running"):
        log.info("  Cache refresh already running")
        return
    import threading
    t = threading.Thread(
        target=_run_cache_refresh_sync,
        args=(db,),
        daemon=True,
        name="popularity-cache-refresh",
    )
    t.start()


def _run_cache_refresh_sync(caller_db: Session):
    """
    Blocking cache refresh — runs entirely in its own daemon thread.
    Opens its own DB session; never touches the caller's session.

    Design decisions:
    - 5 concurrent workers with a shared token-bucket rate limiter
      → 5× throughput while staying under Last.fm's 5 req/s limit
    - _cache_artist_discography replaced by direct REST call (1 API call,
      not the 32 that pylast lazy-evaluation makes per artist)
    - Pass 2 capped at top 5 similar per library artist, deduplicated
      → bounds the total to ~1,000-2,000 similar artists max
    - Progress state written to _cache_refresh_state for UI polling
    """
    import math
    import time
    import threading
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timedelta
    from models import LibraryTrack, PopularityCache

    _set_cache_state(
        running=True,
        phase="Starting",
        done=0,
        total=0,
        started_at=datetime.utcnow().isoformat(),
        finished_at=None,
        error=None,
    )

    thread_db = SessionLocal()
    try:
        from services.popularity import get_aggregator
        agg = get_aggregator(thread_db)
        lfm = agg.adapters.get("lastfm")
        if not lfm or not lfm.is_configured():
            _set_cache_state(running=False, phase="Skipped — Last.fm not configured", error="No Last.fm API key")
            return

        api_key = lfm._api_key

        # ── Rate limiter: 4.5 req/s across all workers ───────────────────────
        # Token bucket: refill 4.5 tokens/sec, max burst of 5
        _rate_lock = threading.Lock()
        _tokens = [4.5]
        _last_refill = [time.monotonic()]

        def _rate_wait():
            while True:
                with _rate_lock:
                    now = time.monotonic()
                    elapsed = now - _last_refill[0]
                    _tokens[0] = min(5.0, _tokens[0] + elapsed * 4.5)
                    _last_refill[0] = now
                    if _tokens[0] >= 1.0:
                        _tokens[0] -= 1.0
                        return
                time.sleep(0.05)

        # ── Direct Last.fm REST helper (bypasses pylast lazy evaluation) ─────
        LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

        def _lastfm_get(method: str, params: dict) -> dict:
            _rate_wait()
            try:
                r = requests.get(
                    LASTFM_BASE,
                    params={"method": method, "api_key": api_key, "format": "json", **params},
                    timeout=10,
                )
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            return {}

        def _fetch_artist(artist: str) -> dict | None:
            """Fetch artist info + similar + top albums in 3 REST calls."""
            # Call 1: artist.getInfo
            data = _lastfm_get("artist.getInfo", {"artist": artist, "autocorrect": 1})
            artist_data = data.get("artist", {})
            if not artist_data:
                return None

            stats = artist_data.get("stats", {})
            listeners = int(stats.get("listeners", 0) or 0)
            pop_score = (
                min(100.0, (math.log1p(listeners) / math.log1p(10_000_000)) * 100)
                if listeners > 0 else 0.0
            )
            tags = [t["name"] for t in (artist_data.get("tags") or {}).get("tag", [])[:10]]
            similar_raw = artist_data.get("similar", {}).get("artist", [])
            similar = [s["name"] for s in similar_raw[:15]]
            image_url = next(
                (img["#text"] for img in reversed(artist_data.get("image", []))
                 if img.get("#text")),
                None,
            )
            canonical_name = artist_data.get("name", artist)

            # Call 2: artist.getTopAlbums (replaces _cache_artist_discography's 32 calls)
            alb_data = _lastfm_get("artist.getTopAlbums", {"artist": artist, "autocorrect": 1, "limit": 10})
            albums = []
            for alb in (alb_data.get("topalbums", {}).get("album", []) or []):
                name = alb.get("name", "")
                if not name or name.lower() in ("(null)", ""):
                    continue
                pc = int(alb.get("playcount", 0) or 0)
                score = min(100.0, (math.log1p(pc) / math.log1p(5_000_000)) * 100)
                img = next(
                    (i["#text"] for i in reversed(alb.get("image", []))
                     if i.get("#text")), None
                )
                albums.append({
                    "name": name,
                    "popularity_score": round(score, 1),
                    "release_year": None,
                    "image_url": img,
                })
            top_album = albums[0] if albums else None

            # Call 3: artist.getSimilar (more complete than getInfo's similar field)
            sim_data = _lastfm_get("artist.getSimilar", {"artist": artist, "autocorrect": 1, "limit": 20})
            similar_full = [
                s["name"]
                for s in (sim_data.get("similarartists", {}).get("artist", []) or [])[:20]
            ]
            if similar_full:
                similar = similar_full

            return {
                "name": canonical_name,
                "popularity_score": round(pop_score, 1),
                "listener_count": listeners,
                "tags": tags,
                "similar_artists": similar,
                "image_url": image_url,
                "albums": albums,
                "top_album": top_album,
            }

        def _cache_artist_result(result: dict, artist: str):
            """Write all fetched data to the DB cache."""
            agg._cache_set(thread_db, f"artist:{artist.lower()}", {
                "name": result["name"],
                "tags": result["tags"],
                "similar_artists": result["similar_artists"],
                "image_url": result["image_url"],
                "popularity_score": result["popularity_score"],
                "listener_count": result["listener_count"],
            })
            if result["similar_artists"]:
                agg._cache_set(thread_db, f"similar:{artist.lower()}", {
                    "artists": result["similar_artists"]
                })
            if result["albums"]:
                agg._cache_set(thread_db, f"discography:{artist.lower()}", {
                    "albums": result["albums"]
                })
            if result["top_album"]:
                top = dict(result["top_album"])
                top["album"] = top.get("name", "")
                agg._cache_set(thread_db, f"top_album:{artist.lower()}", top)

        # ── Collect library artists ───────────────────────────────────────────
        raw: set[str] = set()
        for row in thread_db.query(LibraryTrack.artist_name).filter(
            LibraryTrack.missing_since.is_(None),
            LibraryTrack.artist_name.isnot(None),
            LibraryTrack.artist_name != "",
        ).distinct().all():
            if row[0] and row[0].strip():
                raw.add(row[0].strip())

        for row in thread_db.query(LibraryTrack.album_artist).filter(
            LibraryTrack.missing_since.is_(None),
            LibraryTrack.album_artist.isnot(None),
            LibraryTrack.album_artist != "",
        ).distinct().all():
            if row[0] and row[0].strip():
                raw.add(row[0].strip())

        all_library_artists = list(raw)
        if not all_library_artists:
            _set_cache_state(running=False, phase="Done — no library artists found")
            return

        # Determine which are stale
        stale_cutoff = datetime.utcnow() - timedelta(hours=20)
        fresh = {
            row[0].replace("artist:", "")
            for row in thread_db.query(PopularityCache.cache_key)
            .filter(PopularityCache.cache_key.like("artist:%"))
            .filter(PopularityCache.updated_at > stale_cutoff)
            .all()
        }
        stale_library = [a for a in all_library_artists if a.lower() not in fresh]

        log.info(
            f"  Cache refresh: {len(all_library_artists)} library artists, "
            f"{len(stale_library)} stale, {len(fresh)} fresh"
        )

        if not stale_library:
            _set_cache_state(running=False, phase="Done — all artists fresh")
            return

        # ── Pass 1: library artists, 5 concurrent workers ────────────────────
        _set_cache_state(
            phase=f"Fetching your {len(stale_library)} library artists from Last.fm…",
            total=len(stale_library), done=0,
        )
        similar_collected: dict[str, list[str]] = {}  # artist → their similar list
        done_count = [0]
        write_lock = threading.Lock()

        def _process_library_artist(artist: str):
            result = _fetch_artist(artist)
            with write_lock:
                done_count[0] += 1
                _set_cache_state(
                    done=done_count[0],
                    phase=f"Library artists: {done_count[0]} / {len(stale_library)} fetched",
                )
                if done_count[0] % 25 == 0:
                    log.info(f"    Pass 1: {done_count[0]}/{len(stale_library)}")
            if result:
                _cache_artist_result(result, artist)
                # Cap at top 5 similar per artist to bound Pass 2 size
                return artist, result["similar_artists"][:5]
            return artist, []

        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="lfm") as pool:
            futures = {pool.submit(_process_library_artist, a): a for a in stale_library}
            for future in as_completed(futures):
                try:
                    artist, similars = future.result()
                    similar_collected[artist] = similars
                except Exception as e:
                    log.warning(f"    Worker error: {e}")

        # ── Pass 2: similar artists (discovery candidates) ────────────────────
        # Collect unique similar artists, skip any already in the library
        library_lower = {a.lower() for a in all_library_artists}
        similar_all: set[str] = set()
        for similars in similar_collected.values():
            similar_all.update(s for s in similars if s.lower() not in library_lower)

        # Filter to only stale ones
        stale_similar = [
            s for s in similar_all
            if s.lower() not in fresh
        ]

        log.info(
            f"  Pass 2: {len(similar_all)} unique similar artists, "
            f"{len(stale_similar)} stale"
        )
        _set_cache_state(
            phase=f"Fetching {len(stale_similar)} related artists (discovery candidates)…",
            total=len(stale_similar), done=0,
        )
        done_count[0] = 0

        def _process_similar_artist(artist: str):
            result = _fetch_artist(artist)
            with write_lock:
                done_count[0] += 1
                _set_cache_state(
                    done=done_count[0],
                    phase=f"Related artists: {done_count[0]} / {len(stale_similar)} fetched",
                )
                if done_count[0] % 50 == 0:
                    log.info(f"    Pass 2: {done_count[0]}/{len(stale_similar)}")
            if result:
                _cache_artist_result(result, artist)

        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="lfm") as pool:
            futures = {pool.submit(_process_similar_artist, a): a for a in stale_similar}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log.warning(f"    Worker error: {e}")

        log.info(
            f"  Cache refresh complete: {len(stale_library)} library + "
            f"{len(stale_similar)} similar artists cached"
        )
        _set_cache_state(
            running=False,
            phase="Complete",
            finished_at=datetime.utcnow().isoformat(),
        )

    except Exception as e:
        log.error(f"Cache refresh failed: {e}")
        import traceback
        log.error(traceback.format_exc())
        _set_cache_state(running=False, phase="Error", error=str(e))
    finally:
        thread_db.close()


def _cache_artist_discography(artist_name: str, db):
    """
    Fetch top albums for an artist from Last.fm and store as
    `discography:{artist}` in the popularity cache.
    Each album entry: {name, popularity_score, release_year, image_url}
    """
    import json
    from datetime import datetime, timedelta
    from models import PopularityCache

    cache_key = f"discography:{artist_name.lower()}"
    # Skip if already cached and not expired
    existing = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
    if existing and existing.expires_at > datetime.utcnow():
        return

    try:
        import pylast
        import math
        from services.popularity import get_aggregator
        agg = get_aggregator(db)
        lastfm = agg.adapters.get("lastfm")
        if not lastfm or not lastfm.is_configured():
            return

        net = lastfm._net()
        if not net:
            return

        artist = net.get_artist(artist_name)
        top_albums = artist.get_top_albums(limit=10)
        albums = []
        for item in top_albums:
            alb = item.item
            try:
                alb_name = alb.get_name()
                pc = int(alb.get_playcount() or 0)
                score = min(100.0, (math.log1p(pc) / math.log1p(5_000_000)) * 100)
                release_year = None
                image_url = None
                try:
                    image_url = alb.get_cover_image()
                except Exception:
                    pass
                albums.append({
                    "name": alb_name,
                    "popularity_score": round(score, 1),
                    "release_year": release_year,
                    "image_url": image_url,
                })
            except Exception:
                continue

        if not albums:
            return

        payload = json.dumps({"albums": albums})
        if not existing:
            existing = PopularityCache(cache_key=cache_key)
            db.add(existing)
        existing.payload = payload
        existing.expires_at = datetime.utcnow() + timedelta(hours=24)
        existing.updated_at = datetime.utcnow()
        db.commit()
        log.debug(f"    Cached {len(albums)} albums for {artist_name}")

    except Exception as e:
        log.warning(f"    Discography cache failed for '{artist_name}': {e}")


def _warm_similar_artist_top_albums_names(top_artist_names: list, db):
    """
    Wrapper that accepts a list of artist name strings instead of ORM rows.
    Used by the thread-pool warmup to avoid cross-thread session sharing.
    """
    class _FakeRow:
        def __init__(self, name): self.artist_name = name
    _warm_similar_artist_top_albums([_FakeRow(n) for n in top_artist_names], db)


def _warm_similar_artist_top_albums(top_artist_rows, db):
    """
    For each similar artist found via the similarity cache,
    fetch and store their top album as `top_album:{artist}`.
    """
    import json
    from datetime import datetime, timedelta
    from models import PopularityCache
    import math

    try:
        from services.popularity import get_aggregator
        agg = get_aggregator(db)
        lastfm = agg.adapters.get("lastfm")
        if not lastfm or not lastfm.is_configured():
            return
        net = lastfm._net()
        if not net:
            return
    except Exception:
        return

    # Collect all similar artists from cache
    similar_set: set[str] = set()
    for row in top_artist_rows:
        sim_key = f"similar:{row.artist_name.lower()}"
        sim_row = db.query(PopularityCache).filter_by(cache_key=sim_key).first()
        if not sim_row:
            continue
        try:
            artists = json.loads(sim_row.payload).get("artists", [])
            similar_set.update(artists[:5])  # top 5 similar per seed artist
        except Exception:
            continue

    for artist_name in similar_set:
        cache_key = f"top_album:{artist_name.lower()}"
        existing = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
        if existing and existing.expires_at > datetime.utcnow():
            continue
        try:
            artist = net.get_artist(artist_name)
            top_albums = artist.get_top_albums(limit=1)
            if not top_albums:
                continue
            alb = top_albums[0].item
            alb_name = alb.get_name()
            pc = int(alb.get_playcount() or 0)
            score = min(100.0, (math.log1p(pc) / math.log1p(5_000_000)) * 100)
            image_url = None
            try:
                image_url = alb.get_cover_image()
            except Exception:
                pass

            payload = json.dumps({
                "album": alb_name,
                "popularity_score": round(score, 1),
                "year": None,
                "image_url": image_url,
            })
            if not existing:
                existing = PopularityCache(cache_key=cache_key)
                db.add(existing)
            existing.payload = payload
            existing.expires_at = datetime.utcnow() + timedelta(hours=24)
            existing.updated_at = datetime.utcnow()
            db.commit()
            log.debug(f"    Top album cached for {artist_name}: {alb_name}")
        except Exception as e:
            log.warning(f"    Top album cache failed for '{artist_name}': {e}")
