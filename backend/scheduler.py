"""
JellyDJ — Central background job scheduler.

Uses APScheduler's AsyncIOScheduler so that scheduled jobs can be async
coroutines and share the same event loop as FastAPI.

Registered jobs
───────────────
  play_history_index      Run library scan + per-user play history sync + score rebuild.
                          Default: every 6 hours. Configurable in Automation settings.

  discovery_refresh       Populate the discovery queue with new album recommendations
                          for all enabled users. Default: every 24 hours.

  user_playlist_autopush  Check all UserPlaylist rows with schedule_enabled=True and
                          push any that are past their next scheduled run time.
                          Runs every 15 minutes (fine-grained poll; actual pushes are
                          gated by each playlist's own interval).

  auto_download           Send top-scored pending discovery items to Lidarr.
                          Starts paused; only runs when auto_download_enabled=True.
                          Interval matches the cooldown_days setting.

  popularity_cache_refresh  Refresh the artist-level popularity cache (listener counts,
                          tags, similar artists) from Last.fm and other adapters.
                          Default: every 24 hours. Configurable in Automation settings.

  playlist_backup         Automatically back up all non-excluded Jellyfin playlists.
                          Default: every 24 hours. Configurable in Playlist Backups settings.

All intervals are re-read from the AutomationSettings database row on startup
via reschedule_automation_jobs(), so changes made in the UI take effect after
the next container restart (or immediately if the user saves settings, which
calls reschedule_automation_jobs() again).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Stable job IDs — used to reschedule or pause individual jobs
INDEX_JOB_ID               = "play_history_index"
DISCOVERY_JOB_ID           = "discovery_refresh"
USER_PLAYLIST_AUTOPUSH_ID  = "user_playlist_autopush"
AUTO_DOWNLOAD_JOB_ID       = "auto_download"
BILLBOARD_JOB_ID           = "billboard_refresh"
ENRICHMENT_JOB_ID          = "enrichment"
POPULARITY_CACHE_JOB_ID    = "popularity_cache_refresh"
PLAYLIST_BACKUP_JOB_ID     = "playlist_backup"


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
        auto_download_enabled             = False
        auto_download_cooldown_days       = 7
    return Defaults()


def _sync_wrap(async_fn, *args, **kwargs):
    """
    Run an async job function synchronously in a fresh event loop.

    APScheduler's AsyncIOScheduler dispatches async jobs via
    asyncio.ensure_future() on its internal event loop reference.
    When that reference is None or mismatched with FastAPI's loop,
    the coroutine is silently never awaited and the job appears permanently
    overdue (next_run stays in the past).

    Wrapping each scheduled job in a plain synchronous function that calls
    asyncio.run() sidesteps this entirely — each job gets a clean, isolated
    event loop that runs to completion and is then discarded.
    This is safe because none of the job functions share state with
    FastAPI's request-handling loop.
    """
    import asyncio
    asyncio.run(async_fn(*args, **kwargs))


def _job_run_index():
    import asyncio
    from services.indexer import run_full_index, _index_lock
    import logging as _log
    # Acquire the lock in the sync wrapper — before creating an event loop —
    # so two scheduler threads can never both enter run_full_index concurrently.
    if not _index_lock.acquire(blocking=False):
        _log.getLogger(__name__).warning(
            "Index already running (lock held) — skipping duplicate scheduler trigger."
        )
        return
    try:
        asyncio.run(run_full_index(_lock_already_held=True))
    finally:
        _index_lock.release()


def _job_discovery_refresh():
    from routers.automation import _run_discovery_refresh
    _sync_wrap(_run_discovery_refresh)


def _job_auto_download():
    from routers.automation import _run_auto_download
    _sync_wrap(_run_auto_download)


def _job_billboard_refresh():
    """Fetch Billboard Hot 100 and update the chart entries table."""
    from database import SessionLocal
    from services.indexer import sync_billboard_chart
    db = SessionLocal()
    try:
        sync_billboard_chart(db)
    except Exception as e:
        log.error(f"Billboard refresh job failed: {e}")
    finally:
        db.close()


def _job_enrichment():
    """
    Periodic job: fetch per-song and per-artist Last.fm data (listeners, tags, similar).
    Runs in a daemon thread so the scheduler thread is never blocked — enrichment
    makes hundreds of sequential HTTP calls and holds the DB for the full run,
    which was causing 499s on every other request while it ran.
    """
    import threading

    # ── Duplicate-run guard ───────────────────────────────────────────────────
    # Enrichment can run for 37+ minutes on a large library. If the scheduler
    # interval is shorter than the actual run time, or if a manual trigger fires
    # while the scheduled run is still in progress, we would spin up a second
    # thread writing to the same TrackEnrichment/ArtistEnrichment rows concurrently.
    # Reading from the DB state (not an in-memory flag) means all 4 workers agree.
    try:
        from routers.automation import _get_job_state
        if _get_job_state("enrichment").get("running"):
            log.warning("Enrichment already running — skipping scheduled trigger")
            return
    except Exception as e:
        log.warning(f"Enrichment guard check failed ({e}) — proceeding anyway")

    def _run():
        from database import SessionLocal
        from services.enrichment import run_enrichment
        db = SessionLocal()
        try:
            result = run_enrichment(db)
            log.info(f"Enrichment job complete: {result}")
        except Exception as e:
            log.error(f"Enrichment job failed: {e}", exc_info=True)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True, name="scheduled-enrichment").start()


def _job_popularity_cache_refresh():
    """Periodic job: refresh artist-level popularity cache from Last.fm and other adapters."""
    from routers.automation import _run_popularity_cache_refresh
    _sync_wrap(_run_popularity_cache_refresh)


def _job_holiday_flags():
    """Nightly job: flip holiday_exclude flags as seasons open and close."""
    from database import SessionLocal
    from services.holiday import refresh_exclude_flags
    db = SessionLocal()
    try:
        result = refresh_exclude_flags(db)
        log.info(f"Holiday flags refreshed: {result}")
    except Exception as e:
        log.error(f"Holiday flags refresh failed: {e}")
    finally:
        db.close()


def _job_playlist_backup():
    """Periodic job: back up all non-excluded Jellyfin playlists."""
    from playlist_backup_scheduler import _run_auto_backup
    from database import SessionLocal
    asyncio.run(_run_auto_backup(SessionLocal))


async def _run_user_playlist_autopush():
    """
    Poll all UserPlaylist rows with schedule_enabled=True and push any whose
    next scheduled run time has passed.

    Next run = last_generated_at + schedule_interval_h.
    If the playlist has never been pushed (last_generated_at is None), we
    treat created_at as the base so it fires one interval after creation
    rather than immediately on first boot.

    This job runs every 15 minutes so the maximum push latency is 15 minutes
    past the user's chosen interval.
    """
    from datetime import timedelta
    from database import SessionLocal
    from models import UserPlaylist

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due_playlists = (
            db.query(UserPlaylist)
            .filter(UserPlaylist.schedule_enabled == True)  # noqa: E712
            .all()
        )

        if not due_playlists:
            return

        # Import push logic — reuse the same code path as the manual push endpoint
        from services.playlist_engine import generate_from_template
        from services.playlist_writer import (
            _add_to_playlist,
            _clear_playlist,
            _create_playlist,
            _find_playlist,
            _jellyfin_creds,
        )

        try:
            base_url, api_key = _jellyfin_creds(db)
        except RuntimeError as e:
            log.warning(f"UserPlaylist autopush skipped — Jellyfin not configured: {e}")
            return

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/Users",
                headers={"X-Emby-Token": api_key},
            )
            if resp.status_code != 200:
                log.warning("UserPlaylist autopush: could not reach Jellyfin to get admin user ID")
                return
            users_json = resp.json()
            admin = next((u for u in users_json if u.get("Policy", {}).get("IsAdministrator")), None)
            jf_admin_id = (admin or (users_json[0] if users_json else None) or {}).get("Id")

        if not jf_admin_id:
            log.warning("UserPlaylist autopush: could not determine Jellyfin admin user ID")
            return

        from models import ManagedUser, PlaylistRunItem

        # Build a set of active user IDs once for the whole run so we can
        # cheaply skip orphaned playlists without a per-row DB query.
        active_user_ids: set[str] = {
            row.jellyfin_user_id
            for row in db.query(ManagedUser).filter_by(has_activated=True).all()
        }

        pushed = 0
        skipped = 0

        for playlist in due_playlists:
            try:
                # ── Ghost-user guard ───────────────────────────────────────
                # A playlist whose owner is no longer an active managed user
                # (deleted via the admin panel, or de-activated) must never
                # be pushed.  Without this check, de-activated users get
                # empty playlists created in Jellyfin on every autopush tick.
                if playlist.owner_user_id not in active_user_ids:
                    log.warning(
                        "UserPlaylist id=%d belongs to inactive/deleted user %s "
                        "— skipping autopush and disabling schedule",
                        playlist.id, playlist.owner_user_id,
                    )
                    # Disable the schedule so this warning only fires once
                    # rather than every 15 minutes until someone cleans up.
                    playlist.schedule_enabled = False
                    db.commit()
                    skipped += 1
                    continue

                interval = timedelta(hours=playlist.schedule_interval_h or 24)

                # Determine the base time for computing next_due
                if playlist.last_generated_at is not None:
                    base = playlist.last_generated_at
                    if base.tzinfo is None:
                        base = base.replace(tzinfo=timezone.utc)
                else:
                    # Never pushed — use created_at so it doesn't fire immediately
                    # on every boot before the user has ever manually pushed
                    base = playlist.created_at
                    if base is None:
                        base = now
                    if base.tzinfo is None:
                        base = base.replace(tzinfo=timezone.utc)

                next_due = base + interval

                if next_due > now:
                    skipped += 1
                    continue  # not yet due

                if playlist.template_id is None:
                    log.warning(
                        "UserPlaylist id=%d has no template, skipping autopush", playlist.id
                    )
                    skipped += 1
                    continue

                # Look up the owner's username
                user_row = db.query(ManagedUser).filter_by(
                    jellyfin_user_id=playlist.owner_user_id
                ).first()
                username = user_row.username if user_row else playlist.owner_user_id
                jf_name  = f"{playlist.base_name} - {username}"

                # Generate track list
                track_ids = await generate_from_template(
                    playlist.template_id, playlist.owner_user_id, db
                )

                # Push to Jellyfin
                existing_id = await _find_playlist(base_url, api_key, jf_name, jf_admin_id)
                if existing_id:
                    await _clear_playlist(base_url, api_key, existing_id, jf_admin_id)
                    await _add_to_playlist(base_url, api_key, existing_id, track_ids, jf_admin_id)
                    action = "updated"
                    jf_playlist_id = existing_id
                else:
                    jf_playlist_id = await _create_playlist(
                        base_url, api_key, jf_name, jf_admin_id, track_ids
                    )
                    action = "created"

                if not jf_playlist_id:
                    log.error(
                        "UserPlaylist autopush: Jellyfin op failed for playlist id=%d", playlist.id
                    )
                    skipped += 1
                    continue

                # Stamp last_generated_at — this is the clock that drives the next push
                push_time = datetime.utcnow()
                playlist.last_generated_at = push_time
                playlist.last_track_count  = len(track_ids)
                # Do NOT touch updated_at — that's the user's edit timestamp,
                # not the push timestamp, and the frontend uses updated_at for
                # optimistic display.  last_generated_at is the correct field.

                db.add(PlaylistRunItem(
                    run_id=0,
                    user_id=playlist.owner_user_id,
                    username=username,
                    playlist_type="template",
                    playlist_name=jf_name,
                    jellyfin_playlist_id=jf_playlist_id or "",
                    tracks_added=len(track_ids),
                    action=action,
                    status="ok",
                    created_at=push_time,
                    user_playlist_id=playlist.id,
                ))

                db.commit()
                pushed += 1
                log.info(
                    "UserPlaylist autopush: id=%d (%s) → %d tracks, action=%s",
                    playlist.id, jf_name, len(track_ids), action,
                )

            except Exception as exc:
                log.error(
                    "UserPlaylist autopush failed for id=%d: %s", playlist.id, exc, exc_info=True
                )
                db.rollback()

        if pushed or skipped:
            log.info(
                "UserPlaylist autopush complete: %d pushed, %d not yet due", pushed, skipped
            )

    except Exception as exc:
        log.error("UserPlaylist autopush job crashed: %s", exc, exc_info=True)
    finally:
        db.close()


def _job_user_playlist_autopush():
    """Sync wrapper for the async autopush job — called by APScheduler."""
    asyncio.run(_run_user_playlist_autopush())


def _run_billboard_if_empty():
    """
    Run a billboard sync immediately if the table has never been populated.
    This ensures the dashboard shows chart data on first launch without waiting
    a full week for the scheduler to fire.
    """
    import threading
    from database import SessionLocal
    from models import BillboardChartEntry

    def _check_and_run():
        db = SessionLocal()
        try:
            count = db.query(BillboardChartEntry).count()
            if count == 0:
                log.info("Billboard table empty — running initial chart fetch...")
                from services.indexer import sync_billboard_chart
                sync_billboard_chart(db)
        except Exception as e:
            log.warning(f"Initial billboard fetch failed (non-fatal): {e}")
        finally:
            db.close()

    t = threading.Thread(target=_check_and_run, daemon=True, name="billboard-init")
    t.start()


def start_scheduler(db_session_factory):
    """
    Register all jobs and start the scheduler.
    Called once from the FastAPI lifespan in main.py.

    Jobs are registered with their default intervals first, then
    reschedule_automation_jobs() immediately corrects them from the database.
    The two-step approach ensures the scheduler is running before any
    database access, which avoids startup order issues.

    All scheduled functions are plain synchronous wrappers around their async
    implementations — see _sync_wrap(). This avoids the APScheduler/asyncio
    event loop mismatch that causes jobs to appear perpetually overdue.
    """

    # Register jobs with conservative defaults — reschedule() below will override
    scheduler.add_job(
        _job_run_index,
        trigger=IntervalTrigger(hours=6),
        id=INDEX_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _job_discovery_refresh,
        trigger=IntervalTrigger(hours=24),
        id=DISCOVERY_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,
    )

    # UserPlaylist per-row auto-push — polls every 15 minutes
    # Actual push frequency is gated by each playlist's schedule_interval_h
    scheduler.add_job(
        _job_user_playlist_autopush,
        trigger=IntervalTrigger(minutes=15),
        id=USER_PLAYLIST_AUTOPUSH_ID,
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        _job_auto_download,
        trigger=IntervalTrigger(days=1),
        id=AUTO_DOWNLOAD_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        _job_billboard_refresh,
        trigger=IntervalTrigger(hours=168),  # weekly default
        id=BILLBOARD_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _job_enrichment,
        trigger=IntervalTrigger(hours=6),
        id=ENRICHMENT_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _job_popularity_cache_refresh,
        trigger=IntervalTrigger(hours=24),  # default; reschedule() below corrects from DB
        id=POPULARITY_CACHE_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _job_holiday_flags,
        trigger=IntervalTrigger(hours=24, start_date=datetime.now(timezone.utc)),
        id="holiday_flags",
        name="Holiday Flag Refresh",
        replace_existing=True,
    )

    # Playlist backup job — registered last; interval corrected from DB below
    scheduler.add_job(
        _job_playlist_backup,
        trigger=IntervalTrigger(hours=24),
        id=PLAYLIST_BACKUP_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
        name="Playlist auto-backup",
    )

    scheduler.start()

    # Auto-download starts paused — reschedule_automation_jobs will enable it
    # only if auto_download_enabled=True is saved in the database
    scheduler.pause_job(AUTO_DOWNLOAD_JOB_ID)
    log.info("Scheduler started (9 jobs registered, including user_playlist_autopush and playlist_backup).")

    # Run billboard sync immediately on first start if table is empty
    _run_billboard_if_empty()

    # Immediately apply stored settings so intervals are correct from the first run
    from database import SessionLocal
    db = SessionLocal()
    try:
        reschedule_automation_jobs(db)
        # Apply playlist backup settings from DB
        from playlist_backup_scheduler import reschedule_backup_job
        reschedule_backup_job(db)
    finally:
        db.close()


def reschedule_index_job(db):
    """
    Update only the indexer job interval.
    Called when the user changes index_interval_hours in the Automation settings.
    """
    s = _get_settings(db)
    from datetime import timedelta as _td
    _ix_interval = _td(hours=s.index_interval_hours)
    _ix_last     = getattr(s, "last_index", None)
    _now         = datetime.now(timezone.utc)
    if _ix_last is None:
        _ix_next = _now + _td(minutes=2)   # never run — fire soon after startup
    else:
        if _ix_last.tzinfo is None:
            _ix_last = _ix_last.replace(tzinfo=timezone.utc)
        _ix_next = _ix_last + _ix_interval
        if _ix_next < _now:
            _ix_next = _now + _td(minutes=2)  # overdue — run soon but not instantly
    scheduler.reschedule_job(
        INDEX_JOB_ID,
        trigger=IntervalTrigger(
            hours=s.index_interval_hours,
            start_date=_ix_next,
        ),
    )
    log.info(f"Index job rescheduled: every {s.index_interval_hours}h, next run {_ix_next.strftime('%H:%M UTC')}")


def reschedule_automation_jobs(db):
    """
    Apply current AutomationSettings to all scheduler jobs.

    Called:
      - On startup (after registering jobs with defaults)
      - When the user saves Automation settings in the UI
      - After any manual trigger that might change settings

    Disabled jobs are paused rather than removed so they can be re-enabled
    without re-registering.
    """
    s = _get_settings(db)

    # Indexer always runs — only the interval changes.
    # Use last_index to compute start_date so container restarts and settings saves
    # don't reset the clock and fire the index immediately every time.
    from datetime import timedelta as _td
    _ix_interval = _td(hours=s.index_interval_hours)
    _ix_last     = getattr(s, "last_index", None)
    _now_ix      = datetime.now(timezone.utc)
    if _ix_last is None:
        _ix_next = _now_ix + _td(minutes=2)
    else:
        if _ix_last.tzinfo is None:
            _ix_last = _ix_last.replace(tzinfo=timezone.utc)
        _ix_next = _ix_last + _ix_interval
        if _ix_next < _now_ix:
            _ix_next = _now_ix + _td(minutes=2)  # overdue — run soon but not instantly
    scheduler.reschedule_job(
        INDEX_JOB_ID,
        trigger=IntervalTrigger(
            hours=s.index_interval_hours,
            start_date=_ix_next,
        ),
    )

    # Discovery refresh — can be fully disabled
    if s.discovery_refresh_enabled:
        from datetime import timedelta as _td
        _disc_interval = _td(hours=s.discovery_refresh_interval_hours)
        _disc_last     = getattr(s, "last_discovery_refresh", None)
        _now           = datetime.now(timezone.utc)
        if _disc_last is None:
            _disc_next = _now + _disc_interval
        else:
            if _disc_last.tzinfo is None:
                _disc_last = _disc_last.replace(tzinfo=timezone.utc)
            _disc_next = _disc_last + _disc_interval
            if _disc_next < _now:
                _disc_next = _now + _td(minutes=2)
        scheduler.reschedule_job(
            DISCOVERY_JOB_ID,
            trigger=IntervalTrigger(
                hours=s.discovery_refresh_interval_hours,
                start_date=_disc_next,
            ),
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

    # Auto-download — disabled by default; interval = cooldown_days
    # The job itself also enforces the cooldown internally as a safety net.
    auto_dl_enabled = getattr(s, "auto_download_enabled", False)
    cooldown_days   = getattr(s, "auto_download_cooldown_days", 7) or 1
    if auto_dl_enabled:
        from datetime import timedelta as _td
        interval = _td(days=cooldown_days)
        now      = datetime.now(timezone.utc)
        last_run = getattr(s, "last_auto_download", None)

        if last_run is None:
            # Never run — schedule one interval from now so it doesn't fire immediately
            next_run = now + interval
        else:
            # Base next run on last_run + interval, not on now + interval.
            # This way container restarts and settings saves don't reset the clock —
            # if the job ran 3 days ago with a 7-day cooldown it stays due in 4 days,
            # not pushed back to 7 days from now.
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            next_run = last_run + interval
            # If overdue (next_run in the past), run shortly after startup
            if next_run < now:
                next_run = now + _td(minutes=5)

        scheduler.reschedule_job(
            AUTO_DOWNLOAD_JOB_ID,
            trigger=IntervalTrigger(days=cooldown_days, start_date=next_run),
        )
        try:
            scheduler.resume_job(AUTO_DOWNLOAD_JOB_ID)
        except Exception:
            pass
        log.info(
            f"Auto-download scheduled: every {cooldown_days}d, "
            f"next run {next_run.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:
        try:
            scheduler.pause_job(AUTO_DOWNLOAD_JOB_ID)
        except Exception:
            pass

    # Billboard chart refresh — weekly by default
    billboard_enabled  = getattr(s, "billboard_refresh_enabled", True)
    billboard_interval = getattr(s, "billboard_refresh_interval_hours", 168) or 168
    if billboard_enabled:
        from datetime import timedelta as _td
        _bb_interval = _td(hours=billboard_interval)
        _bb_last     = getattr(s, "last_billboard_refresh", None)
        _now         = datetime.now(timezone.utc)
        if _bb_last is None:
            _bb_next = _now + _td(minutes=1)   # run soon if never run
        else:
            if _bb_last.tzinfo is None:
                _bb_last = _bb_last.replace(tzinfo=timezone.utc)
            _bb_next = _bb_last + _bb_interval
            if _bb_next < _now:
                _bb_next = _now + _td(minutes=1)
        try:
            scheduler.reschedule_job(
                BILLBOARD_JOB_ID,
                trigger=IntervalTrigger(hours=billboard_interval, start_date=_bb_next),
            )
            scheduler.resume_job(BILLBOARD_JOB_ID)
        except Exception:
            pass
    else:
        try:
            scheduler.pause_job(BILLBOARD_JOB_ID)
        except Exception:
            pass

    # Enrichment — always enabled, interval from AutomationSettings
    enrich_interval = getattr(s, "enrichment_interval_hours", 48) or 48
    try:
        scheduler.reschedule_job(
            ENRICHMENT_JOB_ID,
            trigger=IntervalTrigger(hours=enrich_interval),
        )
        scheduler.resume_job(ENRICHMENT_JOB_ID)
    except Exception:
        pass

    # Popularity cache refresh — always enabled, last-run-based scheduling
    pop_cache_interval = getattr(s, "popularity_cache_refresh_interval_hours", 24) or 24
    _pc_last = getattr(s, "last_popularity_cache_refresh", None)
    _now = datetime.now(timezone.utc)
    from datetime import timedelta as _td
    if _pc_last is None:
        # Never run — fire shortly after startup so new installs populate quickly
        _pc_next = _now + _td(minutes=10)
    else:
        if _pc_last.tzinfo is None:
            _pc_last = _pc_last.replace(tzinfo=timezone.utc)
        _pc_next = _pc_last + _td(hours=pop_cache_interval)
        if _pc_next < _now:
            _pc_next = _now + _td(minutes=10)
    try:
        scheduler.reschedule_job(
            POPULARITY_CACHE_JOB_ID,
            trigger=IntervalTrigger(hours=pop_cache_interval, start_date=_pc_next),
        )
        scheduler.resume_job(POPULARITY_CACHE_JOB_ID)
    except Exception:
        pass

    log.info(
        f"Automation rescheduled: index={s.index_interval_hours}h | "
        f"discovery={'on' if s.discovery_refresh_enabled else 'off'} "
        f"({s.discovery_refresh_interval_hours}h) | "
        f"user_playlist_autopush=on (poll every 15m) | "
        f"auto_download={'on' if auto_dl_enabled else 'off'} "
        f"(every {cooldown_days}d) | "
        f"billboard={'on' if billboard_enabled else 'off'} "
        f"(every {billboard_interval}h) | "
        f"enrichment=on (every {enrich_interval}h) | "
        f"popularity_cache=on (every {pop_cache_interval}h)"
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
