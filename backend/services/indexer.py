
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
import threading as _threading
from datetime import datetime as _dt

_job_lock = _threading.Lock()
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
    with _job_lock:
        return dict(_job_state)

def _set_job(running: bool, phase: str = "", detail: str = "",
             percent: int = 0, error: str = None):
    with _job_lock:
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
    # Guard: don't stack concurrent runs
    if get_job_state()["running"]:
        log.warning("Index already running — skipping duplicate trigger.")
        return

    _set_job(True, "Starting", "Connecting to Jellyfin…", 0)
    db = SessionLocal()
    try:
        base_url, api_key = _get_jellyfin_creds(db)
        users = db.query(ManagedUser).filter_by(is_enabled=True).all()

        if not users:
            log.info("No managed users enabled — skipping index.")
            _set_job(False, "Done", "No enabled users", 100)
            return

        n_users = len(users)

        # Step 1: Full library scan (all tracks, played or not)
        _set_job(True, "Library scan", "Scanning Jellyfin library…", 10)
        log.info("Step 1: Running full library scan...")
        scan_result = await run_library_scan(db)
        if scan_result.get("ok"):
            n_tracks = scan_result.get("total_in_db", 0)
            log.info(
                f"  Library scan: {n_tracks} tracks "
                f"(+{scan_result.get('added')} new)"
            )
            _set_job(True, "Library scan", f"{n_tracks:,} tracks found", 20)
        else:
            log.warning(f"  Library scan failed: {scan_result.get('error')} — continuing with play index")
            _set_job(True, "Library scan", "Scan failed — continuing", 20)

        # Step 1.5: Sync usernames from Jellyfin
        _set_job(True, "Syncing users", "Fetching Jellyfin usernames…", 25)
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
            pct = 30 + int((i / n_users) * 60)
            _set_job(True, "Play history", f"Indexing {uname} ({i+1}/{n_users})…", pct)
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

        _set_job(True, "Rebuilding scores", "Updating affinity scores…", 92)

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
        _set_job(False, "Complete", f"Indexed {n_users} user(s)", 100)
    except Exception as e:
        log.error(f"Full index run failed: {e}")
        _set_job(False, "Error", str(e)[:120], 0, error=str(e))
    finally:
        if db is not None:
            db.close()


async def warm_popularity_cache(user_id: str, db: Session, top_n: int = 20):
    """
    After building the taste profile, pre-warm the popularity cache for the
    user's top artists. Runs blocking pylast/HTTP calls in a thread pool
    so they don't block the FastAPI async event loop.
    """
    import asyncio
    from models import UserTasteProfile
    from services.popularity import get_aggregator

    top = (
        db.query(UserTasteProfile)
        .filter_by(user_id=user_id)
        .filter(UserTasteProfile.artist_name.isnot(None))
        .order_by(UserTasteProfile.affinity_score.desc())
        .limit(top_n)
        .all()
    )

    if not top:
        log.info(f"  No taste profile yet for {user_id} — skipping cache warm-up")
        return

    aggregator = get_aggregator(db)
    configured = [k for k, v in aggregator.adapter_status().items() if v]
    if not configured:
        log.info("  No external APIs configured — skipping cache warm-up")
        return

    log.info(f"  Warming popularity cache for {len(top)} artists (using: {', '.join(configured)})")

    loop = asyncio.get_event_loop()
    # Capture artist names before entering thread — avoids passing SQLAlchemy
    # objects across thread boundaries (sessions are not thread-safe)
    top_artist_names = [row.artist_name for row in top]

    def _do_warmup():
        """All blocking I/O in one function — runs in thread pool with its own session."""
        thread_db = SessionLocal()
        try:
            thread_aggregator = get_aggregator(thread_db)
            for artist in top_artist_names:
                try:
                    thread_aggregator.get_artist_info(artist, db=thread_db)
                    thread_aggregator.get_similar_artists(artist, db=thread_db)
                    _cache_artist_discography(artist, thread_db)
                    log.debug(f"    Cached: {artist}")
                except Exception as e:
                    log.warning(f"    Cache warm-up failed for '{artist}': {e}")
            _warm_similar_artist_top_albums_names(top_artist_names, thread_db)
        finally:
            thread_db.close()

    await loop.run_in_executor(None, _do_warmup)
    log.info(f"  Cache warm-up complete.")


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
