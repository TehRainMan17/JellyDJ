"""
JellyDJ — Central background job scheduler.

Uses APScheduler's AsyncIOScheduler so that scheduled jobs can be async
coroutines and share the same event loop as FastAPI.

Registered jobs
───────────────
  play_history_index   Run library scan + per-user play history sync + score rebuild.
                       Default: every 6 hours. Configurable in Automation settings.

  discovery_refresh    Populate the discovery queue with new album recommendations
                       for all enabled users. Default: every 24 hours.

  playlist_regen       Regenerate all Jellyfin playlists from current scores.
                       Default: every 24 hours.

  auto_download        Send top-scored pending discovery items to Lidarr.
                       Starts paused; only runs when auto_download_enabled=True.
                       Interval matches the cooldown_days setting.

All intervals are re-read from the AutomationSettings database row on startup
via reschedule_automation_jobs(), so changes made in the UI take effect after
the next container restart (or immediately if the user saves settings, which
calls reschedule_automation_jobs() again).
"""
from __future__ import annotations

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Stable job IDs — used to reschedule or pause individual jobs
INDEX_JOB_ID          = "play_history_index"
DISCOVERY_JOB_ID      = "discovery_refresh"
PLAYLIST_JOB_ID       = "playlist_regen"
AUTO_DOWNLOAD_JOB_ID  = "auto_download"


def _get_settings(db):
    """
    Read AutomationSettings from the database.
    Returns a Defaults fallback object if the row doesn't exist yet
    (i.e. on a fresh install before the user has visited the Automation page).
    """
    try:
        from models import AutomationSettings
        row = db.query(AutomationSettings).first()
        if row:
            return row
    except Exception:
        pass

    # Inline defaults — mirrors the column defaults in models.py
    class Defaults:
        index_interval_hours              = 6
        discovery_refresh_enabled         = True
        discovery_refresh_interval_hours  = 24
        discovery_items_per_run           = 10
        playlist_regen_enabled            = True
        playlist_regen_interval_hours     = 24
        auto_download_enabled             = False
        auto_download_cooldown_days       = 7
    return Defaults()


def start_scheduler(db_session_factory):
    """
    Register all four jobs and start the scheduler.
    Called once from the FastAPI lifespan in main.py.

    Jobs are registered with their default intervals first, then
    reschedule_automation_jobs() immediately corrects them from the database.
    The two-step approach ensures the scheduler is running before any
    database access, which avoids startup order issues.
    """
    from services.indexer import run_full_index
    from routers.automation import _run_discovery_refresh, _run_playlist_regen, _run_auto_download

    # Register jobs with conservative defaults — reschedule() below will override
    scheduler.add_job(
        run_full_index,
        trigger=IntervalTrigger(hours=6),
        id=INDEX_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,   # if the job fires 5+ minutes late, still run it
    )
    scheduler.add_job(
        _run_discovery_refresh,
        trigger=IntervalTrigger(hours=24),
        id=DISCOVERY_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _run_playlist_regen,
        trigger=IntervalTrigger(hours=24),
        id=PLAYLIST_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _run_auto_download,
        trigger=IntervalTrigger(days=1),
        id=AUTO_DOWNLOAD_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.start()

    # Auto-download starts paused — reschedule_automation_jobs will enable it
    # only if auto_download_enabled=True is saved in the database
    scheduler.pause_job(AUTO_DOWNLOAD_JOB_ID)
    log.info("Scheduler started (4 jobs registered).")

    # Immediately apply stored settings so intervals are correct from the first run
    from database import SessionLocal
    db = SessionLocal()
    try:
        reschedule_automation_jobs(db)
    finally:
        db.close()


def reschedule_index_job(db):
    """
    Update only the indexer job interval.
    Called when the user changes index_interval_hours in the Automation settings.
    """
    s = _get_settings(db)
    scheduler.reschedule_job(
        INDEX_JOB_ID,
        trigger=IntervalTrigger(hours=s.index_interval_hours),
    )
    log.info(f"Index job rescheduled: every {s.index_interval_hours}h")


def reschedule_automation_jobs(db):
    """
    Apply current AutomationSettings to all four scheduler jobs.

    Called:
      - On startup (after registering jobs with defaults)
      - When the user saves Automation settings in the UI
      - After any manual trigger that might change settings

    Disabled jobs are paused rather than removed so they can be re-enabled
    without re-registering.
    """
    s = _get_settings(db)

    # Indexer always runs — only the interval changes
    scheduler.reschedule_job(
        INDEX_JOB_ID,
        trigger=IntervalTrigger(hours=s.index_interval_hours),
    )

    # Discovery refresh — can be fully disabled
    if s.discovery_refresh_enabled:
        scheduler.reschedule_job(
            DISCOVERY_JOB_ID,
            trigger=IntervalTrigger(hours=s.discovery_refresh_interval_hours),
        )
        try:
            scheduler.resume_job(DISCOVERY_JOB_ID)
        except Exception:
            pass
    else:
        try:
            scheduler.pause_job(DISCOVERY_JOB_ID)
        except Exception:
            pass

    # Playlist regen — can be fully disabled
    if s.playlist_regen_enabled:
        scheduler.reschedule_job(
            PLAYLIST_JOB_ID,
            trigger=IntervalTrigger(hours=s.playlist_regen_interval_hours),
        )
        try:
            scheduler.resume_job(PLAYLIST_JOB_ID)
        except Exception:
            pass
    else:
        try:
            scheduler.pause_job(PLAYLIST_JOB_ID)
        except Exception:
            pass

    # Auto-download — disabled by default; interval = cooldown_days
    # The job itself also enforces the cooldown internally as a safety net
    auto_dl_enabled = getattr(s, "auto_download_enabled", False)
    cooldown_days   = getattr(s, "auto_download_cooldown_days", 7) or 1
    if auto_dl_enabled:
        scheduler.reschedule_job(
            AUTO_DOWNLOAD_JOB_ID,
            trigger=IntervalTrigger(days=cooldown_days),
        )
        try:
            scheduler.resume_job(AUTO_DOWNLOAD_JOB_ID)
        except Exception:
            pass
    else:
        try:
            scheduler.pause_job(AUTO_DOWNLOAD_JOB_ID)
        except Exception:
            pass

    log.info(
        f"Automation rescheduled: index={s.index_interval_hours}h | "
        f"discovery={'on' if s.discovery_refresh_enabled else 'off'} "
        f"({s.discovery_refresh_interval_hours}h) | "
        f"playlists={'on' if s.playlist_regen_enabled else 'off'} "
        f"({s.playlist_regen_interval_hours}h) | "
        f"auto_download={'on' if auto_dl_enabled else 'off'} "
        f"(every {cooldown_days}d)"
    )


async def trigger_index_now():
    """
    Immediately fire the indexer outside of its normal schedule.
    Called by the manual "Index Now" button in the UI via the automation router.
    """
    from services.indexer import run_full_index
    log.info("Manual index triggered.")
    await run_full_index()


def get_job_status() -> dict:
    """
    Return the next scheduled run time for all registered jobs.
    next_run is None when a job is paused.
    Used by the Dashboard and the Automation page to show upcoming run times.
    """
    statuses = {}
    for job in scheduler.get_jobs():
        statuses[job.id] = {
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "paused":   job.next_run_time is None,
            "name":     job.name or job.id,
        }
    return statuses
