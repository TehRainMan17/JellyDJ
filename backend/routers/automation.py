
"""
JellyDJ Automation router — settings and manual triggers for all scheduled tasks,
plus the activity feed endpoint.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db, SessionLocal
from models import AutomationSettings, SystemEvent, ManagedUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AutomationSettingsUpdate(BaseModel):
    index_interval_hours: Optional[int] = None
    discovery_refresh_enabled: Optional[bool] = None
    discovery_refresh_interval_hours: Optional[int] = None
    discovery_items_per_run: Optional[int] = None
    playlist_regen_enabled: Optional[bool] = None
    playlist_regen_interval_hours: Optional[int] = None
    auto_download_enabled: Optional[bool] = None
    auto_download_max_per_run: Optional[int] = None
    auto_download_cooldown_days: Optional[int] = None


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
def get_settings(db: Session = Depends(get_db)):
    s = _get_or_create_settings(db)
    return {
        "index_interval_hours": s.index_interval_hours,
        "discovery_refresh_enabled": s.discovery_refresh_enabled,
        "discovery_refresh_interval_hours": s.discovery_refresh_interval_hours,
        "discovery_items_per_run": s.discovery_items_per_run,
        "playlist_regen_enabled": s.playlist_regen_enabled,
        "playlist_regen_interval_hours": s.playlist_regen_interval_hours,
        "auto_download_enabled": bool(s.auto_download_enabled),
        "auto_download_max_per_run": s.auto_download_max_per_run,
        "auto_download_cooldown_days": s.auto_download_cooldown_days,
        "last_auto_download": s.last_auto_download,
        "last_index": s.last_index,
        "last_discovery_refresh": s.last_discovery_refresh,
        "last_playlist_regen": s.last_playlist_regen,
    }


@router.post("/settings")
def update_settings(payload: AutomationSettingsUpdate, db: Session = Depends(get_db)):
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

    if payload.playlist_regen_enabled is not None:
        s.playlist_regen_enabled = payload.playlist_regen_enabled

    if payload.playlist_regen_interval_hours is not None:
        if not (1 <= payload.playlist_regen_interval_hours <= 168):
            raise HTTPException(400, "Playlist interval must be 1–168 hours")
        s.playlist_regen_interval_hours = payload.playlist_regen_interval_hours

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
async def trigger_index(db: Session = Depends(get_db)):
    """Manually trigger a full index run immediately."""
    from scheduler import trigger_index_now
    import asyncio
    asyncio.ensure_future(trigger_index_now())
    return {"ok": True, "message": "Index started in background"}


@router.post("/trigger/discovery")
async def trigger_discovery(db: Session = Depends(get_db)):
    """Manually trigger a discovery queue refresh for all enabled users."""
    import asyncio
    asyncio.ensure_future(_run_discovery_refresh())
    return {"ok": True, "message": "Discovery refresh started in background"}


@router.post("/trigger/playlists")
async def trigger_playlists(db: Session = Depends(get_db)):
    """Manually trigger playlist regeneration for all enabled users."""
    import asyncio
    asyncio.ensure_future(_run_playlist_regen())
    return {"ok": True, "message": "Playlist regeneration started in background"}


@router.post("/trigger/auto-download")
async def trigger_auto_download(db: Session = Depends(get_db)):
    """Manually trigger an auto-download run (respects enabled flag but bypasses cooldown)."""
    s = _get_or_create_settings(db)
    if not s.auto_download_enabled:
        raise HTTPException(400, "Auto-download is disabled. Enable it in settings first.")
    import asyncio
    asyncio.ensure_future(_run_auto_download(bypass_cooldown=True))
    return {"ok": True, "message": "Auto-download check started"}


@router.get("/auto-download-preview")
def auto_download_preview(db: Session = Depends(get_db)):
    """
    Show what the auto-downloader would pick for each user right now,
    without actually sending anything. Useful for debugging pin behaviour.
    """
    from models import DiscoveryQueueItem
    from sqlalchemy import text as satext

    s = _get_or_create_settings(db)
    users = db.query(ManagedUser).filter_by(is_enabled=True).all()
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
    db = SessionLocal()
    try:
        from models import ManagedUser
        s = _get_or_create_settings(db)
        users = db.query(ManagedUser).filter_by(is_enabled=True).all()
        if not users:
            return

        from routers.discovery import _populate_queue_for_user
        total_added = 0
        for user in users:
            try:
                added = await _populate_queue_for_user(
                    user.jellyfin_user_id, db,
                    limit=s.discovery_items_per_run
                )
                total_added += added
                log.info(f"  Discovery refresh: +{added} items for {user.username}")
            except Exception as e:
                log.error(f"  Discovery refresh failed for {user.username}: {e}")

        s.last_discovery_refresh = datetime.utcnow()
        db.commit()
        log.info(f"Discovery refresh complete: +{total_added} items total")
    except Exception as e:
        log.error(f"Discovery refresh run failed: {e}")
    finally:
        db.close()


async def _run_playlist_regen():
    """Regenerate all playlists. Called by scheduler."""
    db = SessionLocal()
    try:
        from services.playlist_writer import run_playlist_generation
        log.info("Scheduled playlist regeneration starting...")
        result = await run_playlist_generation(db)
        log.info(f"Playlist regen complete: {result.get('playlists_written', 0)} written")
    except Exception as e:
        log.error(f"Playlist regen failed: {e}")
    finally:
        db.close()


async def _run_auto_download(bypass_cooldown: bool = False):
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
    db = SessionLocal()
    try:
        s = _get_or_create_settings(db)

        # Gate 1: master switch
        if not s.auto_download_enabled:
            log.info("Auto-download: skipping — disabled")
            return

        # Gate 2: cooldown check
        if not bypass_cooldown and s.last_auto_download:
            cooldown = timedelta(days=s.auto_download_cooldown_days)
            elapsed = datetime.utcnow() - s.last_auto_download
            if elapsed < cooldown:
                remaining = (cooldown - elapsed).total_seconds() / 3600
                log.info(f"Auto-download: cooldown active — {remaining:.1f}h remaining")
                return

        from models import DiscoveryQueueItem
        from routers.discovery import _send_to_lidarr, _get_lidarr_creds

        try:
            base_url, api_key = _get_lidarr_creds(db)
        except Exception as e:
            log.error(f"Auto-download: Lidarr not configured — {e}")
            return

        # Gate 3: two-pass candidate selection
        # Pass 1 — send pinned items for ALL users unconditionally.
        #   Pinned = explicit user request. The cap must never block these.
        # Pass 2 — fill remaining slots up to max_per_run with best unpinned
        #   candidates, skipping users who already got something in pass 1.
        from sqlalchemy import text as satext
        users = db.query(ManagedUser).filter_by(is_enabled=True).all()
        total_sent = 0
        max_total  = s.auto_download_max_per_run
        users_sent_this_run: set = set()

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
                    log.info(f"  ✓ {result['message']}")
                    from services.events import log_event
                    log_event(db, "auto_download",
                              f"Auto-downloaded: {candidate.artist_name} — {candidate.album_name or 'album'}")
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

        # Always stamp last_auto_download so the cooldown advances regardless of
        # whether anything was sent. Without this, an empty queue causes the job
        # to fire every interval forever (cooldown never starts).
        s.last_auto_download = datetime.utcnow()
        db.commit()
        if total_sent > 0:
            log.info(f"Auto-download complete: {total_sent} album(s) sent to Lidarr")
        else:
            log.info("Auto-download: ran, no albums sent this run (queue empty or all filtered)")


    except Exception as e:
        log.error(f"Auto-download run failed: {e}")
        import traceback
        log.error(traceback.format_exc())
    finally:
        db.close()


# ── Activity feed ─────────────────────────────────────────────────────────────

@router.get("/activity")
def get_activity(
    limit: int = 50,
    event_type: Optional[str] = None,
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
def scheduler_status(db: Session = Depends(get_db)):
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
            "playlist_regen_enabled": s.playlist_regen_enabled,
            "playlist_regen_interval_hours": s.playlist_regen_interval_hours,
        }
    }
