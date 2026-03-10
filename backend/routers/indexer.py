from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime

from database import get_db
from models import IndexerSettings, UserSyncStatus, Play, UserTasteProfile

router = APIRouter(prefix="/api/indexer", tags=["indexer"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class IndexerSettingsUpdate(BaseModel):
    index_interval_hours: int


# ── Settings ──────────────────────────────────────────────────────────────────

def _get_or_create_settings(db: Session) -> IndexerSettings:
    row = db.query(IndexerSettings).first()
    if not row:
        row = IndexerSettings(index_interval_hours=6)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    row = _get_or_create_settings(db)
    return {
        "index_interval_hours": row.index_interval_hours,
        "last_full_index": row.last_full_index,
    }


@router.post("/settings")
def update_settings(payload: IndexerSettingsUpdate, db: Session = Depends(get_db)):
    if not (1 <= payload.index_interval_hours <= 168):
        raise HTTPException(400, "Interval must be between 1 and 168 hours.")
    row = _get_or_create_settings(db)
    row.index_interval_hours = payload.index_interval_hours
    db.commit()
    # Reschedule the live job
    try:
        from scheduler import reschedule_index_job
        reschedule_index_job(db)
    except Exception:
        pass  # Scheduler may not be running in test mode
    return {"ok": True}


# ── Manual trigger ────────────────────────────────────────────────────────────

@router.post("/run-now")
async def run_now(background_tasks: BackgroundTasks):
    """Trigger a full index run immediately (non-blocking)."""
    from scheduler import trigger_index_now
    background_tasks.add_task(trigger_index_now)
    return {"ok": True, "message": "Index started in background."}


# ── Status / dashboard data ───────────────────────────────────────────────────

@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    """
    Return per-user sync status for the dashboard widget.
    Always returns ALL enabled managed users — new users show as 'never'
    even before their first index run completes.
    """
    from models import ManagedUser
    # Build sync status lookup
    sync_map = {
        r.user_id: r
        for r in db.query(UserSyncStatus).all()
    }
    # All enabled managed users — source of truth
    users = db.query(ManagedUser).filter_by(has_activated=True).all()
    result = []
    for u in users:
        s = sync_map.get(u.jellyfin_user_id)
        result.append({
            "user_id": u.jellyfin_user_id,
            "username": u.username,
            "last_synced": s.last_synced if s else None,
            "tracks_indexed": s.tracks_indexed if s else 0,
            "status": s.status if s else "never",
        })
    return result


@router.get("/job-status")
def job_status():
    """
    Lightweight polling endpoint for the frontend.
    Returns current indexer run state: phase, detail, percent complete.
    Safe to call every 2s — reads from in-memory state, no DB hit.
    """
    from services.indexer import get_job_state
    return get_job_state()


@router.get("/scheduler")
def get_scheduler_status():
    """Return next-run times for all scheduled jobs."""
    try:
        from scheduler import get_job_status
        return get_job_status()
    except Exception as e:
        return {"error": str(e)}


# ── Taste profile inspection (useful for debugging Module 5) ──────────────────

@router.get("/taste-profile/{user_id}")
def get_taste_profile(user_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Return the top affinity scores for a user — artists and genres separately."""
    rows = db.query(UserTasteProfile).filter_by(user_id=user_id).all()

    artists = sorted(
        [r for r in rows if r.artist_name],
        key=lambda r: float(r.affinity_score),
        reverse=True,
    )[:limit]

    genres = sorted(
        [r for r in rows if r.genre],
        key=lambda r: float(r.affinity_score),
        reverse=True,
    )[:20]

    play_count = db.query(Play).filter_by(user_id=user_id).count()

    return {
        "user_id": user_id,
        "total_plays_indexed": play_count,
        "top_artists": [
            {"artist": r.artist_name, "score": float(r.affinity_score)}
            for r in artists
        ],
        "top_genres": [
            {"genre": r.genre, "score": float(r.affinity_score)}
            for r in genres
        ],
    }


# ── Module 8a: Library scan + scoring endpoints ───────────────────────────────

@router.post("/library-scan")
async def trigger_library_scan(db: Session = Depends(get_db)):
    """Trigger a full library scan (all Jellyfin audio, played or not)."""
    from services.library_scanner import run_library_scan
    result = await run_library_scan(db)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "Library scan failed"))
    return result


@router.get("/library-stats")
def library_stats(db: Session = Depends(get_db)):
    """Return library size stats for the dashboard."""
    from services.library_scanner import get_library_stats
    return get_library_stats(db)


@router.post("/full-scan")
async def trigger_full_scan(db: Session = Depends(get_db)):
    """
    Trigger a complete rescan: library scan + play history + scoring rebuild.
    Runs in a daemon thread so the ASGI event loop is never blocked and the
    dashboard stays responsive throughout. Same execution model as the scheduler.
    """
    import threading
    from services.indexer import run_full_index, get_job_state
    import asyncio

    # Don't start a second run if one is already in progress
    state = get_job_state()
    if state.get("running"):
        return {"ok": True, "message": "Index already running."}

    def _run():
        asyncio.run(run_full_index())

    t = threading.Thread(target=_run, daemon=True, name="manual-index")
    t.start()
    return {"ok": True, "message": "Full scan started in background. Poll /api/indexer/job-status for progress."}


@router.get("/score-distribution/{user_id}")
def score_distribution(user_id: str, db: Session = Depends(get_db)):
    """Return score distribution stats for a user — useful for tuning/debugging."""
    from services.scoring_engine import get_score_distribution
    dist = get_score_distribution(db, user_id)
    if not dist:
        raise HTTPException(404, "No scores found for this user. Run a full scan first.")
    return dist


@router.get("/score-distribution/by-username/{username}")
def score_distribution_by_username(username: str, db: Session = Depends(get_db)):
    """Score distribution by username for convenience."""
    from models import ManagedUser
    from services.scoring_engine import get_score_distribution
    user = db.query(ManagedUser).filter(
        ManagedUser.username.ilike(username)
    ).first()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    dist = get_score_distribution(db, user.jellyfin_user_id)
    if not dist:
        raise HTTPException(404, "No scores found. Run a full scan first.")
    return {"username": user.username, "user_id": user.jellyfin_user_id, **dist}

# ── Billboard Hot 100 endpoints ───────────────────────────────────────────────

@router.get("/billboard")
def get_billboard(limit: int = 5, db: Session = Depends(get_db)):
    """
    Return the top N Billboard Hot 100 entries with album art and trend data.

    Artwork strategy (tried in order):
      1. iTunes Search API — free, no key, returns high-res artwork for current
         chart hits. Results cached in popularity_cache for 7 days so repeated
         loads are instant and we don't hammer Apple's servers.
      2. Popularity cache artist image_url — falls back to whatever Last.fm/
         Spotify stored for the artist if iTunes comes up empty.

    Trend fields:
      last_week_position  — chart position last week (None = new entry)
      position_change     — positive = moved up, negative = moved down, 0 = same,
                            None = new entry this week
    """
    from models import BillboardChartEntry, PopularityCache
    import json, httpx
    from datetime import timedelta

    rows = (
        db.query(BillboardChartEntry)
        .order_by(BillboardChartEntry.rank.asc())
        .limit(limit)
        .all()
    )

    if not rows:
        return []

    # ── Artwork: iTunes Search API, cached 7 days ─────────────────────────────
    def _itunes_artwork(artist: str, title: str) -> str | None:
        """Query iTunes Search API for artwork. Returns URL or None."""
        cache_key = f"itunes_art:{artist.lower()}::{title.lower()}"
        cached = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
        if cached:
            try:
                return json.loads(cached.payload).get("url")
            except Exception:
                pass

        try:
            import urllib.parse
            query = urllib.parse.quote(f"{artist} {title}")
            resp = httpx.get(
                f"https://itunes.apple.com/search?term={query}&media=music&limit=5&entity=song",
                timeout=6.0,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                url = None
                for r in results:
                    raw = r.get("artworkUrl100", "")
                    if raw:
                        # Upgrade to 600px artwork
                        url = raw.replace("100x100bb", "600x600bb")
                        break

                # Cache result (even None) for 7 days so we don't repeat failed lookups
                payload = json.dumps({"url": url})
                row = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
                if not row:
                    row = PopularityCache(cache_key=cache_key)
                    db.add(row)
                row.payload = payload
                row.expires_at = datetime.utcnow() + timedelta(days=7)
                row.updated_at = datetime.utcnow()
                db.commit()
                return url
        except Exception:
            pass
        return None

    # Fallback: artist image from popularity cache
    artist_names = list({r.artist.lower() for r in rows})
    artist_image_cache: dict[str, str | None] = {}
    for name_lower in artist_names:
        cache_row = db.query(PopularityCache).filter_by(
            cache_key=f"artist:{name_lower}"
        ).first()
        if cache_row:
            try:
                artist_image_cache[name_lower] = json.loads(cache_row.payload).get("image_url")
            except Exception:
                pass

    # ── Live library check ────────────────────────────────────────────────────
    from models import LibraryTrack
    from services.indexer import _normalise_for_match

    def _artist_in_library(artist: str) -> bool:
        norm = _normalise_for_match(artist)
        # Direct match first
        match = db.query(LibraryTrack.id).filter(
            LibraryTrack.missing_since.is_(None),
            LibraryTrack.artist_name.ilike(f"%{artist}%"),
        ).first()
        if match:
            return True
        # Also check album_artist field
        match2 = db.query(LibraryTrack.id).filter(
            LibraryTrack.missing_since.is_(None),
            LibraryTrack.album_artist.ilike(f"%{artist}%"),
        ).first()
        return bool(match2)

    result = []
    for r in rows:
        # Try iTunes first, fall back to artist cache
        image_url = _itunes_artwork(r.artist, r.title)
        if not image_url:
            image_url = artist_image_cache.get(r.artist.lower())

        # Trend: positive = moved up the chart (lower rank number = better)
        position_change = None
        if r.last_week_position is not None:
            position_change = r.last_week_position - r.rank  # +ve = improved

        # Live library check — more accurate than the weekly sync match
        in_library = bool(r.jellyfin_item_id) or _artist_in_library(r.artist)

        result.append({
            "rank":               r.rank,
            "title":              r.title,
            "artist":             r.artist,
            "chart_score":        r.chart_score,
            "weeks_on_chart":     r.weeks_on_chart,
            "peak_position":      r.peak_position,
            "last_week_position": r.last_week_position,
            "position_change":    position_change,
            "in_library":         in_library,
            "jellyfin_item_id":   r.jellyfin_item_id,
            "image_url":          image_url,
            "chart_date":         r.chart_date,
        })

    return result


@router.get("/billboard/status")
def get_billboard_status(db: Session = Depends(get_db)):
    """Return chart metadata and when it was last refreshed."""
    from models import BillboardChartEntry, AutomationSettings
    count = db.query(BillboardChartEntry).count()
    latest = (
        db.query(BillboardChartEntry)
        .order_by(BillboardChartEntry.fetched_at.desc())
        .first()
    )
    settings = db.query(AutomationSettings).first()
    return {
        "entries": count,
        "chart_date": latest.chart_date if latest else None,
        "fetched_at": latest.fetched_at.isoformat() if latest else None,
        "last_refresh": settings.last_billboard_refresh.isoformat()
            if settings and settings.last_billboard_refresh else None,
    }


@router.post("/billboard/refresh")
async def refresh_billboard(db: Session = Depends(get_db)):
    """Manually trigger a Billboard chart refresh."""
    import threading, asyncio
    from services.indexer import sync_billboard_chart

    def _run():
        from database import SessionLocal
        rdb = SessionLocal()
        try:
            sync_billboard_chart(rdb)
        finally:
            rdb.close()

    t = threading.Thread(target=_run, daemon=True, name="billboard-refresh")
    t.start()
    return {"ok": True, "message": "Billboard refresh started in background"}


class BillboardDownloadRequest(BaseModel):
    artist: str
    title: str
    album_name: str = ""


@router.post("/billboard/download")
async def billboard_download(payload: BillboardDownloadRequest, db: Session = Depends(get_db)):
    """
    Send a Billboard chart track directly to Lidarr without needing a
    discovery queue item. Creates a temporary queue entry, sends it, then
    leaves it in the queue as 'approved' so the user can see it was actioned.
    """
    from models import DiscoveryQueueItem
    from routers.discovery import _send_to_lidarr, _get_lidarr_creds

    try:
        base_url, api_key = _get_lidarr_creds(db)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    # Use album_name if provided, otherwise fall back to track title
    # (Lidarr works on albums not tracks, so we find the artist's top album)
    album_to_send = payload.album_name or ""

    try:
        result = await _send_to_lidarr(payload.artist, album_to_send, base_url, api_key)
    except Exception as e:
        raise HTTPException(502, str(e))

    # Record in discovery queue so the user can see what was downloaded
    entry = DiscoveryQueueItem(
        user_id="billboard",  # system-sourced, not user-specific
        artist_name=payload.artist,
        album_name=result.get("album_name") or album_to_send or payload.title,
        why=f"Billboard Hot 100 — manually requested from dashboard",
        source_artist="billboard",
        source_affinity="100.0",
        status="approved",
        lidarr_sent=result["ok"],
        lidarr_response=result["message"],
        actioned_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()

    if not result["ok"]:
        raise HTTPException(502, result["message"])

    return {"ok": True, "message": result["message"]}