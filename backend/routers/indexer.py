
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
    users = db.query(ManagedUser).filter_by(is_enabled=True).all()
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
