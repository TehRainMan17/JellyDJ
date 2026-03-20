"""
JellyDJ Automation router — settings and manual triggers for all scheduled tasks,
plus the activity feed endpoint.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from auth import get_current_user, require_admin, UserContext
from database import get_db, SessionLocal
from models import AutomationSettings, SystemEvent, ManagedUser, JobState

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])

# ── DB-backed job state — shared across all uvicorn workers ─────────────────
#
# Previously these were module-level dicts. With multiple workers each process
# has its own copy, so status polls would hit a different worker than the one
# running the job and see empty/stale state.
#
# Now state is written to the JobState table so any worker can read it.
# Writes use their own short-lived session so they don't interfere with the
# caller's transaction.

import json as _json

_JOB_DEFAULTS = {
    "enrichment": {
        "running": False, "phase": "", "current_item": "",
        "tracks_done": 0, "tracks_total": 0, "tracks_enriched": 0, "tracks_failed": 0,
        "artists_done": 0, "artists_total": 0, "artists_enriched": 0, "artists_failed": 0,
        "error": None,
    },
    "discovery": {
        "running": False, "phase": "", "detail": "",
        "users_done": 0, "users_total": 0, "items_added": 0, "error": None,
    },
    "download": {
        "running": False, "phase": "", "detail": "",
        "sent": 0, "total": 0, "error": None,
    },
}

def _get_job_state(job_id: str) -> dict:
    """Read job state from DB. Falls back to defaults if row doesn't exist yet."""
    try:
        db = SessionLocal()
        try:
            row = db.query(JobState).filter_by(job_id=job_id).first()
            if not row:
                return dict(_JOB_DEFAULTS.get(job_id, {}))
            state = _json.loads(row.payload) if row.payload else {}
            state["running"]     = bool(row.running)
            state["phase"]       = row.phase or ""
            state["started_at"]  = row.started_at.isoformat() if row.started_at else None
            state["finished_at"] = row.finished_at.isoformat() if row.finished_at else None
            return state
        finally:
            db.close()
    except Exception as e:
        log.warning(f"_get_job_state({job_id}) failed: {e}")
        return dict(_JOB_DEFAULTS.get(job_id, {}))

def _set_job_state(job_id: str, **kwargs):
    """
    Write job state to DB. Uses its own session so it's safe from any thread.

    For non-critical progress updates (tracks_done, current_item etc.), if the
    DB is locked by the enrichment thread's open transaction, we skip silently —
    the next progress callback fires in ~0.22s and will write the updated values.
    Critical state changes (running, phase, error) still log a warning if they fail.
    """
    from datetime import datetime as _dt
    is_critical = "running" in kwargs or "phase" in kwargs or "error" in kwargs
    try:
        db = SessionLocal()
        try:
            row = db.query(JobState).filter_by(job_id=job_id).first()
            if not row:
                row = JobState(job_id=job_id)
                db.add(row)
            # running / phase go on the row directly for fast queries
            if "running" in kwargs:
                row.running = bool(kwargs["running"])
                if kwargs["running"] and not row.started_at:
                    row.started_at  = _dt.utcnow()
                    row.finished_at = None
                if not kwargs["running"]:
                    row.finished_at = _dt.utcnow()
                    row.started_at  = None
            if "phase" in kwargs:
                row.phase = kwargs["phase"]
            # everything else goes into payload JSON
            existing = _json.loads(row.payload) if row.payload else {}
            existing.update({k: v for k, v in kwargs.items() if k not in ("running", "phase")})
            row.payload = _json.dumps(existing)
            row.updated_at = _dt.utcnow()
            db.commit()
        finally:
            db.close()
    except Exception as e:
        if is_critical:
            log.warning(f"_set_job_state({job_id}) failed (critical): {e}")
        else:
            log.debug(f"_set_job_state({job_id}) skipped (db busy): {e}")

# Convenience wrappers that match the old function signatures
def _set_enrichment_state(**kwargs): _set_job_state("enrichment", **kwargs)
def _set_discovery_state(**kwargs):  _set_job_state("discovery",  **kwargs)
def _set_download_state(**kwargs):   _set_job_state("download",   **kwargs)


# ── Schemas ───────────────────────────────────────────────────────────────────

class AutomationSettingsUpdate(BaseModel):
    index_interval_hours: Optional[int] = None
    discovery_refresh_enabled: Optional[bool] = None
    discovery_refresh_interval_hours: Optional[int] = None
    discovery_items_per_run: Optional[int] = None

    auto_download_enabled: Optional[bool] = None        # was missing — caused 422 on every save
    auto_download_max_per_run: Optional[int] = None
    auto_download_cooldown_days: Optional[int] = None
    popularity_cache_refresh_interval_hours: Optional[int] = None
    billboard_refresh_enabled: Optional[bool] = None    # was missing — caused 422 on every save
    billboard_refresh_interval_hours: Optional[int] = None  # was missing — caused 422 on every save


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_or_create_settings(db: Session) -> AutomationSettings:
    row = db.query(AutomationSettings).first()
    if not row:
        row = AutomationSettings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# ── Settings endpoints ────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    s = _get_or_create_settings(db)
    return {
        "index_interval_hours": s.index_interval_hours,
        "discovery_refresh_enabled": s.discovery_refresh_enabled,
        "discovery_refresh_interval_hours": s.discovery_refresh_interval_hours,
        "discovery_items_per_run": s.discovery_items_per_run,
        "auto_download_enabled": bool(s.auto_download_enabled),
        "auto_download_max_per_run": s.auto_download_max_per_run,
        "auto_download_cooldown_days": s.auto_download_cooldown_days,
        "popularity_cache_refresh_interval_hours": s.popularity_cache_refresh_interval_hours,
        "billboard_refresh_enabled": bool(s.billboard_refresh_enabled) if s.billboard_refresh_enabled is not None else True,
        "billboard_refresh_interval_hours": s.billboard_refresh_interval_hours or 168,
        "last_auto_download": s.last_auto_download,
        "last_index": s.last_index,
        "last_discovery_refresh": s.last_discovery_refresh,
        "last_popularity_cache_refresh": s.last_popularity_cache_refresh,
    }


@router.post("/settings")
def update_settings(payload: AutomationSettingsUpdate, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    s = _get_or_create_settings(db)

    if payload.index_interval_hours is not None:
        if not (1 <= payload.index_interval_hours <= 168):
            raise HTTPException(400, "Index interval must be 1–168 hours")
        s.index_interval_hours = payload.index_interval_hours
        # Reschedule live job
        try:
            from scheduler import reschedule_index_job
            reschedule_index_job(db)
        except Exception:
            pass

    if payload.discovery_refresh_enabled is not None:
        s.discovery_refresh_enabled = payload.discovery_refresh_enabled

    if payload.discovery_refresh_interval_hours is not None:
        if not (1 <= payload.discovery_refresh_interval_hours <= 168):
            raise HTTPException(400, "Discovery interval must be 1–168 hours")
        s.discovery_refresh_interval_hours = payload.discovery_refresh_interval_hours

    if payload.discovery_items_per_run is not None:
        if not (1 <= payload.discovery_items_per_run <= 50):
            raise HTTPException(400, "Items per run must be 1–50")
        s.discovery_items_per_run = payload.discovery_items_per_run

    # ── Auto-download controls ────────────────────────────────────────────────
    if payload.auto_download_enabled is not None:
        s.auto_download_enabled = payload.auto_download_enabled
        log.info(f"Auto-download {'ENABLED' if payload.auto_download_enabled else 'DISABLED'}")

    if payload.auto_download_max_per_run is not None:
        if not (1 <= payload.auto_download_max_per_run <= 5):
            raise HTTPException(400, "Auto-download max per run must be 1–5")
        s.auto_download_max_per_run = payload.auto_download_max_per_run

    if payload.auto_download_cooldown_days is not None:
        if not (1 <= payload.auto_download_cooldown_days <= 30):
            raise HTTPException(400, "Cooldown must be 1–30 days")
        s.auto_download_cooldown_days = payload.auto_download_cooldown_days

    if payload.popularity_cache_refresh_interval_hours is not None:
        if not (1 <= payload.popularity_cache_refresh_interval_hours <= 168):
            raise HTTPException(400, "Popularity cache interval must be 1–168 hours")
        s.popularity_cache_refresh_interval_hours = payload.popularity_cache_refresh_interval_hours

    if payload.billboard_refresh_enabled is not None:
        s.billboard_refresh_enabled = payload.billboard_refresh_enabled

    if payload.billboard_refresh_interval_hours is not None:
        if not (24 <= payload.billboard_refresh_interval_hours <= 168):
            raise HTTPException(400, "Billboard interval must be 24–168 hours")
        s.billboard_refresh_interval_hours = payload.billboard_refresh_interval_hours

    db.commit()

    # Reschedule automation jobs with new intervals
    try:
        from scheduler import reschedule_automation_jobs
        reschedule_automation_jobs(db)
    except Exception as e:
        log.warning(f"Reschedule failed: {e}")

    return {"ok": True}


# ── Manual trigger endpoints ──────────────────────────────────────────────────

@router.post("/trigger/index")
async def trigger_index(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Manually trigger a full index run immediately."""
    import threading
    from services.indexer import run_full_index, get_job_state
    state = get_job_state()
    if state.get("running"):
        return {"ok": True, "message": "Index already running."}
    def _run():
        asyncio.run(run_full_index())
    threading.Thread(target=_run, daemon=True, name="manual-index").start()
    return {"ok": True, "message": "Index started in background"}


@router.post("/trigger/enrichment")
async def trigger_enrichment(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Manually trigger a full enrichment run (tracks + artists) immediately.
    Fetches per-song and per-artist Last.fm data: listener counts, tags, similar artists.
    This populates the song popularity and artist popularity columns in Insights.
    Runs in a background thread — poll /trigger/enrichment/status for progress.
    """
    import threading
    state = _get_job_state("enrichment")
    if state.get("running"):
        return {"ok": False, "message": "Enrichment already running", "state": state}

    def _run():
        import time as _time
        _set_enrichment_state(
            running=True, phase="Fetching song data",
            current_item="",
            tracks_done=0, tracks_total=0, tracks_enriched=0, tracks_failed=0,
            artists_done=0, artists_total=0, artists_enriched=0, artists_failed=0,
            error=None,
        )
        db2 = SessionLocal()
        try:
            from services.enrichment import enrich_tracks, enrich_artists

            # Throttle DB writes to once every 5 seconds — enrichment fires
            # the callback after every track (0.22s delay between calls), so
            # without throttling we'd write ~4-5 times/second and saturate
            # the SQLite write lock for the entire run.
            _last_progress_write = [0.0]
            PROGRESS_WRITE_INTERVAL = 5.0

            def track_progress(done, total, track, artist, enriched, failed):
                now = _time.monotonic()
                if now - _last_progress_write[0] < PROGRESS_WRITE_INTERVAL:
                    return
                _last_progress_write[0] = now
                _set_enrichment_state(
                    tracks_done=done,
                    tracks_total=total,
                    tracks_enriched=enriched,
                    tracks_failed=failed,
                    current_item=f"{track}" + (f" — {artist}" if artist else ""),
                )

            def artist_progress(done, total, artist, enriched, failed):
                now = _time.monotonic()
                if now - _last_progress_write[0] < PROGRESS_WRITE_INTERVAL:
                    return
                _last_progress_write[0] = now
                _set_enrichment_state(
                    artists_done=done,
                    artists_total=total,
                    artists_enriched=enriched,
                    artists_failed=failed,
                    current_item=artist,
                )

            # Manual trigger = catchup mode: no limit, process full library.
            # run_enrichment()'s smart dispatcher also auto-detects catchup,
            # but passing limit=None directly ensures manual runs are always full.
            track_result = enrich_tracks(db2, force=False, limit=None, progress_callback=track_progress)

            _set_enrichment_state(
                running=True, phase="Fetching artist data",
                current_item="",
                tracks_enriched=track_result.get("enriched", 0),
                tracks_failed=track_result.get("failed", 0),
            )

            artist_result = enrich_artists(db2, force=False, limit=None, progress_callback=artist_progress)

            try:
                from models import AutomationSettings
                from datetime import datetime as _dt
                s = db2.query(AutomationSettings).first()
                if s:
                    s.last_enrichment = _dt.utcnow()
                    db2.commit()
            except Exception:
                pass

            _set_enrichment_state(
                running=False, phase="Complete",
                current_item="",
                tracks_enriched=track_result.get("enriched", 0),
                tracks_failed=track_result.get("failed", 0),
                artists_enriched=artist_result.get("enriched", 0),
                artists_failed=artist_result.get("failed", 0),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Manual enrichment failed: {e}", exc_info=True)
            _set_enrichment_state(running=False, phase="Error", error=str(e))
        finally:
            db2.close()

    threading.Thread(target=_run, daemon=True, name="manual-enrichment").start()
    return {"ok": True, "message": "Enrichment started in background — poll /trigger/enrichment/status"}


def _sanitize_state(state: dict, user: UserContext) -> dict:
    """
    Strip the internal 'error' field from job-state dicts before returning
    them to non-admin users.

    The error field is populated with raw str(e) from Python exceptions, which
    can contain internal hostnames, file paths, database schema details, or
    other information that should not be disclosed to ordinary users.  Admin
    users see the full error string so they can diagnose problems in the UI
    without needing to read server logs.
    """
    out = dict(state)
    if not user.is_admin:
        # Replace exception detail with a generic signal.
        # The boolean 'had_error' lets the frontend show a generic error badge
        # without leaking the underlying message to non-admin users.
        had_error = bool(out.get("error"))
        out["error"] = None
        out["had_error"] = had_error
    return out


@router.get("/trigger/enrichment/status")
def enrichment_trigger_status(user: UserContext = Depends(get_current_user)):
    """Poll this to get live progress of the enrichment run."""
    return _sanitize_state(_get_job_state("enrichment"), user)


@router.post("/trigger/popularity-cache")
async def trigger_popularity_cache(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Trigger a full library popularity cache refresh.
    Runs in a background task — returns immediately, dashboard stays responsive.
    Poll GET /trigger/popularity-cache/status for progress.
    """
    from services.indexer import get_cache_refresh_state, refresh_library_popularity_cache
    state = get_cache_refresh_state()
    if state.get("running"):
        return {"ok": False, "message": "Cache refresh already running", "state": state}

    async def _run_and_stamp():
        await refresh_library_popularity_cache(db)
        try:
            db2 = SessionLocal()
            s = db2.query(AutomationSettings).first()
            if s:
                s.last_popularity_cache_refresh = datetime.utcnow()
                db2.commit()
            db2.close()
        except Exception as exc:
            log.warning(f"Could not stamp last_popularity_cache_refresh: {exc}")

    asyncio.create_task(_run_and_stamp())
    return {"ok": True, "message": "Cache refresh started in background — poll /status for progress"}


@router.get("/trigger/popularity-cache/status")
def cache_refresh_status(user: UserContext = Depends(get_current_user)):
    """Poll this to get live progress of the cache refresh."""
    from services.indexer import get_cache_refresh_state
    state = get_cache_refresh_state()
    done = state.get("done", 0)
    total = state.get("total", 0)
    pct = round(100 * done / total) if total > 0 else 0
    return {**_sanitize_state(state, user), "progress_pct": pct}


@router.get("/trigger/discovery/status")
def discovery_trigger_status(user: UserContext = Depends(get_current_user)):
    """Poll this to get live progress of the discovery refresh."""
    state = _get_job_state("discovery")
    done = state.get("users_done", 0)
    total = state.get("users_total", 0)
    pct = round(100 * done / total) if total > 0 else (50 if state.get("running") else 0)
    return {**_sanitize_state(state, user), "progress_pct": pct}


@router.get("/trigger/auto-download/status")
def download_trigger_status(user: UserContext = Depends(get_current_user)):
    """Poll this to get live progress of the auto-download run."""
    state = _get_job_state("download")
    sent = state.get("sent", 0)
    total = state.get("total", 0)
    pct = round(100 * sent / total) if total > 0 else (50 if state.get("running") else 0)
    return {**_sanitize_state(state, user), "progress_pct": pct}


@router.post("/trigger/discovery")
async def trigger_discovery(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Manually trigger a discovery queue refresh for all enabled users."""
    asyncio.create_task(_run_discovery_refresh())
    return {"ok": True, "message": "Discovery refresh started in background"}


@router.post("/trigger/auto-download")
async def trigger_auto_download(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Manually trigger an auto-download run — bypasses cooldown and schedule.

    The manual Run Now button always fires regardless of enabled state,
    so users can test configuration. It does NOT update last_auto_download,
    so the scheduled timer is unaffected.
    """
    s = _get_or_create_settings(db)
    if not s.auto_download_enabled:
        raise HTTPException(400, "Auto-download is disabled. Enable it in settings first.")
    # bypass_cooldown=True  — skip the cooldown gate
    # update_timestamp=False — do NOT stamp last_auto_download so the scheduled
    #                          timer is not reset by a manual run
    asyncio.create_task(_run_auto_download(bypass_cooldown=True, update_timestamp=False))
    return {"ok": True, "message": "Auto-download started (manual run — schedule timer unchanged)"}


@router.get("/auto-download-preview")
def auto_download_preview(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Show what the auto-downloader would pick for each user right now,
    without actually sending anything. Useful for debugging pin behaviour.
    """
    from models import DiscoveryQueueItem
    from sqlalchemy import text as satext

    s = _get_or_create_settings(db)
    users = db.query(ManagedUser).filter_by(has_activated=True).all()
    result = []

    for user in users:
        uid = user.jellyfin_user_id

        all_pinned = (
            db.query(DiscoveryQueueItem)
            .filter(
                DiscoveryQueueItem.user_id == uid,
                DiscoveryQueueItem.auto_queued == True,
            )
            .all()
        )

        pinned = next(
            (i for i in all_pinned
             if i.status == "pending" and not i.lidarr_sent and not i.auto_skip),
            None
        )

        fallback = None
        if not pinned:
            fallback = (
                db.query(DiscoveryQueueItem)
                .filter(
                    DiscoveryQueueItem.user_id == uid,
                    DiscoveryQueueItem.status == "pending",
                    DiscoveryQueueItem.lidarr_sent == False,
                    DiscoveryQueueItem.auto_skip == False,
                    DiscoveryQueueItem.auto_queued == False,
                )
                .order_by(satext("CAST(popularity_score AS REAL) DESC"))
                .first()
            )

        candidate = pinned or fallback

        result.append({
            "username": user.username,
            "user_id": uid,
            "all_pinned_in_db": [
                {
                    "id": i.id,
                    "artist": i.artist_name,
                    "album": i.album_name,
                    "status": i.status,
                    "lidarr_sent": i.lidarr_sent,
                    "auto_skip": bool(i.auto_skip),
                    "auto_queued": bool(i.auto_queued),
                }
                for i in all_pinned
            ],
            "would_send": {
                "id": candidate.id,
                "artist": candidate.artist_name,
                "album": candidate.album_name,
                "is_pinned": bool(pinned),
                "auto_queued_value": candidate.auto_queued,
            } if candidate else None,
        })

    return {
        "enabled": bool(s.auto_download_enabled),
        "cooldown_days": s.auto_download_cooldown_days,
        "max_per_run": s.auto_download_max_per_run,
        "users": result,
    }


# ── Scheduled job functions ───────────────────────────────────────────────────

async def _run_discovery_refresh():
    """Refresh discovery queue for all enabled users. Called by scheduler."""
    import asyncio
    from database import SessionLocal as _SL

    # Read settings and user list with a short-lived session
    db = _SL()
    try:
        from models import ManagedUser
        s = _get_or_create_settings(db)
        users = db.query(ManagedUser).filter_by(has_activated=True).all()
        limit = s.discovery_items_per_run
    finally:
        db.close()

    if not users:
        _set_discovery_state(running=False, phase="No enabled users", detail="",
                             users_done=0, users_total=0, items_added=0, error=None)
        return

    _set_discovery_state(
        running=True, phase="Starting discovery refresh",
        detail=f"Found {len(users)} user(s)", users_done=0,
        users_total=len(users), items_added=0, error=None,
    )

    path_e_global_seen: set[str] = set()
    total_added = 0

    def _run_user_sync(user_id: str, username: str) -> int:
        """Runs in a thread — keeps the event loop free."""
        import asyncio as _aio
        from routers.discovery import _populate_queue_for_user
        db2 = _SL()
        try:
            loop = _aio.new_event_loop()
            try:
                return loop.run_until_complete(
                    _populate_queue_for_user(
                        user_id, db2, limit,
                        path_e_global_seen=path_e_global_seen,
                    )
                )
            finally:
                loop.close()
        except Exception as e:
            log.error(f"  Discovery refresh failed for {username}: {e}")
            return 0
        finally:
            db2.close()

    try:
        for idx, user in enumerate(users):
            _set_discovery_state(
                phase=f"Refreshing queue for {user.username}",
                detail=f"User {idx + 1} of {len(users)}",
                users_done=idx,
            )
            added = await asyncio.to_thread(_run_user_sync, user.jellyfin_user_id, user.username)
            total_added += added
            log.info(f"  Discovery refresh: +{added} items for {user.username}")
            await asyncio.sleep(0)

        # Stamp last_discovery_refresh
        db3 = _SL()
        try:
            s2 = _get_or_create_settings(db3)
            s2.last_discovery_refresh = datetime.utcnow()
            db3.commit()
        finally:
            db3.close()

        log.info(f"Discovery refresh complete: +{total_added} items total")
        _set_discovery_state(
            running=False, phase="Complete",
            detail=f"+{total_added} new items across {len(users)} user(s)",
            users_done=len(users), items_added=total_added, error=None,
        )
    except Exception as e:
        log.error(f"Discovery refresh run failed: {e}")
        _set_discovery_state(running=False, phase="Error", error=str(e))


async def _run_auto_download(bypass_cooldown: bool = False, update_timestamp: bool = True):
    """
    Auto-download job: sends the top-scored pending discovery items to Lidarr
    automatically, subject to the user's rate limit controls.

    Safety gates (all must pass before ANY download happens):
    1. auto_download_enabled must be True
    2. Cooldown: last_auto_download must be > cooldown_days ago (unless bypass_cooldown)
    3. Max per run: never sends more than auto_download_max_per_run albums in one run
    4. Never sends an item marked auto_skip=True ("not that one")
    5. Never sends an item already sent (lidarr_sent=True)

    Preference order:
    - If any item has auto_queued=True (user said "getting this next"), send that first
    - Otherwise pick the highest-scored pending item not marked auto_skip
    """
    _set_download_state(running=True, phase="Starting", detail="Checking gates…", sent=0, total=0, error=None)
    db = SessionLocal()
    try:
        s = _get_or_create_settings(db)

        # Gate 1: master switch
        if not s.auto_download_enabled:
            log.info("Auto-download: skipping — disabled")
            _set_download_state(running=False, phase="Skipped — auto-download is disabled", error=None)
            return

        # Gate 2: cooldown check
        if not bypass_cooldown and s.last_auto_download:
            cooldown = timedelta(days=s.auto_download_cooldown_days)
            elapsed = datetime.utcnow() - s.last_auto_download
            if elapsed < cooldown:
                remaining = (cooldown - elapsed).total_seconds() / 3600
                log.info(f"Auto-download: cooldown active — {remaining:.1f}h remaining")
                _set_download_state(running=False, phase=f"Cooldown active — {remaining:.1f}h remaining", error=None)
                return

        from models import DiscoveryQueueItem
        from routers.discovery import _send_to_lidarr, _get_lidarr_creds

        try:
            base_url, api_key = _get_lidarr_creds(db)
        except Exception as e:
            log.error(f"Auto-download: Lidarr not configured — {e}")
            _set_download_state(running=False, phase="Lidarr not configured", error=str(e))
            return

        # Gate 2b: refresh discovery queue before picking candidates.
        # Auto-download should always work from fresh recommendations so it
        # doesn't re-attempt items that were recently rejected or already sent.
        # Skip the refresh if discovery ran within the last 6 hours to avoid
        # hammering external APIs unnecessarily.
        refresh_stale_after_hours = 6
        needs_refresh = True
        if s.last_discovery_refresh:
            hours_since = (datetime.utcnow() - s.last_discovery_refresh).total_seconds() / 3600
            if hours_since < refresh_stale_after_hours:
                needs_refresh = False
                log.info(f"Auto-download: discovery refreshed {hours_since:.1f}h ago, skipping pre-refresh")

        if needs_refresh:
            log.info("Auto-download: running discovery refresh before picking candidates...")
            _set_download_state(phase="Refreshing discovery queue…", detail="Pre-run queue update")
            try:
                users_for_refresh = db.query(ManagedUser).filter_by(has_activated=True).all()
                from routers.discovery import _populate_queue_for_user
                for u in users_for_refresh:
                    try:
                        added = await _populate_queue_for_user(u.jellyfin_user_id, db, limit=s.discovery_items_per_run)
                        log.info(f"  Pre-refresh: +{added} items for {u.username}")
                    except Exception as e:
                        log.warning(f"  Pre-refresh failed for {u.username}: {e}")
                s.last_discovery_refresh = datetime.utcnow()
                db.commit()
            except Exception as e:
                log.warning(f"Auto-download: pre-refresh failed, proceeding with existing queue — {e}")

        # Gate 3: two-pass candidate selection
        # Pass 1 — send pinned items for ALL users unconditionally.
        #   Pinned = explicit user request. The cap must never block these.
        # Pass 2 — fill remaining slots up to max_per_run with best unpinned
        #   candidates, skipping users who already got something in pass 1.
        from sqlalchemy import text as satext
        users = db.query(ManagedUser).filter_by(has_activated=True).all()
        total_sent = 0
        max_total  = s.auto_download_max_per_run
        users_sent_this_run: set = set()

        _set_download_state(
            phase="Selecting candidates",
            detail=f"Up to {max_total} album(s) across {len(users)} user(s)",
            total=max_total,
        )

        async def _send_candidate(candidate, user, is_pinned: bool):
            nonlocal total_sent
            log.info(
                f"Auto-download [{user.username}]: sending "
                f"'{'PINNED ' if is_pinned else ''}{candidate.artist_name} — {candidate.album_name}'"
            )
            try:
                result = await _send_to_lidarr(
                    candidate.artist_name, candidate.album_name, base_url, api_key
                )
                candidate.lidarr_sent   = result["ok"]
                candidate.lidarr_response = result["message"]
                if result["ok"]:
                    candidate.status      = "approved"
                    candidate.actioned_at = datetime.utcnow()
                    candidate.auto_queued = False
                    total_sent += 1
                    users_sent_this_run.add(user.jellyfin_user_id)
                    _set_download_state(
                        phase=f"Sending to Lidarr",
                        detail=f"{candidate.artist_name} — {candidate.album_name or 'album'}",
                        sent=total_sent,
                    )
                    log.info(f"  ✓ {result['message']}")
                    from services.events import log_event
                    # Use the album name Lidarr actually searched for (from result["message"]),
                    # not candidate.album_name which may be blank for artist-only recommendations.
                    display_album = candidate.album_name or ""
                    if not display_album:
                        # Parse from result message: "'Artist' added to Lidarr — search triggered for 'Album'"
                        import re as _re
                        m = _re.search(r"search triggered for '([^']+)'", result["message"])
                        if m:
                            display_album = m.group(1)
                    log_event(db, "auto_download",
                              f"Auto-downloaded: {candidate.artist_name} — {display_album or 'unknown album'}")
                    db.flush()  # ensure SystemEvent row lands in the same commit as the queue item update
                else:
                    log.warning(f"  ✗ Failed: {result['message']}")
                db.commit()
            except Exception as e:
                log.error(f"  ✗ Exception for '{candidate.artist_name}': {e}")

        # ── Pass 1: pinned items — one per user, no cap ───────────────────────
        for user in users:
            uid = user.jellyfin_user_id
            pinned = (
                db.query(DiscoveryQueueItem)
                .filter(
                    DiscoveryQueueItem.user_id      == uid,
                    DiscoveryQueueItem.status        == "pending",
                    DiscoveryQueueItem.lidarr_sent   == False,
                    DiscoveryQueueItem.auto_queued   == True,
                    DiscoveryQueueItem.auto_skip     == False,
                )
                .first()
            )
            if pinned:
                await _send_candidate(pinned, user, is_pinned=True)
            else:
                log.info(f"Auto-download [{user.username}]: no pinned item")

        # ── Pass 2: unpinned fallback, up to max_total total sends ────────────
        for user in users:
            if total_sent >= max_total:
                break
            uid = user.jellyfin_user_id
            if uid in users_sent_this_run:
                continue   # already got their pinned item
            fallback = (
                db.query(DiscoveryQueueItem)
                .filter(
                    DiscoveryQueueItem.user_id      == uid,
                    DiscoveryQueueItem.status        == "pending",
                    DiscoveryQueueItem.lidarr_sent   == False,
                    DiscoveryQueueItem.auto_skip     == False,
                    DiscoveryQueueItem.auto_queued   == False,
                )
                .order_by(satext("CAST(popularity_score AS REAL) DESC"))
                .first()
            )
            if fallback:
                await _send_candidate(fallback, user, is_pinned=False)
            else:
                log.info(f"Auto-download [{user.username}]: no fallback candidate")

        # Stamp last_auto_download so the cooldown advances — but NOT on manual
        # runs (update_timestamp=False), so the scheduled timer is unaffected.
        if update_timestamp:
            s.last_auto_download = datetime.utcnow()
            db.commit()
        if total_sent > 0:
            log.info(f"Auto-download complete: {total_sent} album(s) sent to Lidarr")
            _set_download_state(running=False, phase="Complete", detail=f"{total_sent} album(s) sent to Lidarr", sent=total_sent, error=None)
        else:
            log.info("Auto-download: ran, no albums sent this run (queue empty or all filtered)")
            _set_download_state(running=False, phase="Complete — nothing to send", detail="Queue empty or all candidates filtered", sent=0, error=None)


    except Exception as e:
        log.error(f"Auto-download run failed: {e}")
        import traceback
        log.error(traceback.format_exc())
        _set_download_state(running=False, phase="Error", error=str(e))
    finally:
        db.close()


async def _run_popularity_cache_refresh():
    """
    Scheduled job: refresh the artist-level popularity cache.
    Runs the blocking HTTP+DB work in a thread so the event loop stays free.
    """
    import asyncio

    def _run_sync():
        from services.indexer import refresh_library_popularity_cache
        import asyncio as _aio
        db = SessionLocal()
        try:
            log.info("Scheduled popularity cache refresh starting…")
            loop = _aio.new_event_loop()
            try:
                loop.run_until_complete(refresh_library_popularity_cache(db))
            finally:
                loop.close()
            s = db.query(AutomationSettings).first()
            if s:
                s.last_popularity_cache_refresh = datetime.utcnow()
                db.commit()
            log.info("Scheduled popularity cache refresh complete.")
        except Exception as e:
            log.error(f"Popularity cache refresh job failed: {e}")
        finally:
            db.close()

    await asyncio.to_thread(_run_sync)


# ── Auto-download history ──────────────────────────────────────────────────────

@router.get("/auto-download/history")
def get_auto_download_history(limit: int = 200, _: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Return the most recent auto-download events from the SystemEvent log,
    newest first. Used by Settings > Auto-Download (last requested display)
    and the Discovery Queue > Auto-Downloaded history tab.
    """
    rows = (
        db.query(SystemEvent)
        .filter(SystemEvent.event_type == "auto_download")
        .order_by(SystemEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":         r.id,
            "message":    r.message,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ── Activity feed ─────────────────────────────────────────────────────────────

@router.get("/activity")
def get_activity(
    limit: int = 50,
    event_type: Optional[str] = None,
    _: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Recent system activity feed. Sorted newest-first.
    Used by the Dashboard activity section.
    """
    q = db.query(SystemEvent).order_by(SystemEvent.created_at.desc())
    if event_type:
        q = q.filter(SystemEvent.event_type == event_type)
    rows = q.limit(limit).all()

    return [
        {
            "id": r.id,
            "event_type": r.event_type,
            "message": r.message,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/scheduler-status")
def scheduler_status(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return next-run times for all jobs plus current settings."""
    from scheduler import get_job_status
    jobs = get_job_status()
    s = _get_or_create_settings(db)
    return {
        "jobs": jobs,
        "settings": {
            "index_interval_hours": s.index_interval_hours,
            "discovery_refresh_enabled": s.discovery_refresh_enabled,
            "discovery_refresh_interval_hours": s.discovery_refresh_interval_hours,
        }
    }


@router.post("/reset-job-state/{job_id}")
def reset_job_state(
    job_id: str,
    _: UserContext = Depends(require_admin),
):
    """
    Manually clear a stuck 'running' job state. Admin only.

    Use this when a job crashed without cleaning up its state and the trigger
    endpoint is refusing to start because it thinks the job is still running.

    Valid job_ids: enrichment, discovery, download, index, popularity_cache
    """
    valid_ids = {"enrichment", "discovery", "download", "index", "popularity_cache"}
    if job_id not in valid_ids:
        raise HTTPException(400, f"Unknown job_id '{job_id}'. Valid: {sorted(valid_ids)}")

    _set_job_state(job_id, running=False, phase="Reset by admin", error=None)
    log.warning("Job state '%s' manually reset by admin", job_id)
    return {"ok": True, "job_id": job_id, "message": f"'{job_id}' state cleared — you can now trigger it again"}


@router.post("/reset-all-job-states")
def reset_all_job_states(_: UserContext = Depends(require_admin)):
    """
    Clear all stuck 'running' job states in one call. Admin only.
    """
    from models import JobState
    db = SessionLocal()
    try:
        stale = db.query(JobState).filter_by(running=True).all()
        reset = []
        for row in stale:
            row.running = False
            row.phase = "Reset by admin"
            reset.append(row.job_id)
        db.commit()
    finally:
        db.close()

    if reset:
        log.warning("All running job states reset by admin: %s", reset)
    return {"ok": True, "reset": reset, "message": f"Cleared {len(reset)} job state(s)"}
