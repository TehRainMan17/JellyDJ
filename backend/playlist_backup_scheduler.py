"""
JellyDJ — Playlist Backup scheduler helpers.

Registers and reschedules the automatic playlist-backup job.
Called from start_scheduler() in scheduler.py.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PLAYLIST_BACKUP_JOB_ID = "playlist_backup"


def _run_auto_backup(SessionLocal):
    """Synchronous APScheduler entry point."""
    asyncio.run(_async_auto_backup(SessionLocal))


async def _async_auto_backup(SessionLocal):
    """
    Automatic backup: backs up all playlists that are NOT excluded from auto-backup.
    Excluded (snapshot) playlists are only updated via manual "Backup now" in the UI.
    """
    db = SessionLocal()
    try:
        from models import PlaylistBackupSettings, PlaylistBackup, ConnectionSettings
        from crypto import decrypt

        settings = db.query(PlaylistBackupSettings).first()
        if not settings or not settings.auto_backup_enabled:
            log.debug("Playlist auto-backup is disabled — skipping")
            return

        jf_row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
        if not jf_row or not jf_row.base_url or not jf_row.api_key_encrypted:
            log.warning("Playlist auto-backup: Jellyfin not configured — skipping")
            return

        base_url = jf_row.base_url.rstrip("/")
        api_key = decrypt(jf_row.api_key_encrypted)

        from routers.playlist_backups import (
            _get_admin_user_id,
            _fetch_jellyfin_playlists,
            _do_backup_playlist,
            _build_managed_set,
            _is_managed,
        )

        admin_user_id = await _get_admin_user_id(base_url, api_key)
        playlists = await _fetch_jellyfin_playlists(base_url, api_key, admin_user_id)
        managed_ids, managed_names = _build_managed_set(db)

        backed_up = 0
        skipped = 0
        for p in playlists:
            # Never auto-backup JellyDJ-managed playlists
            if _is_managed(p["id"], p["name"], managed_ids, managed_names):
                skipped += 1
                continue
            existing = db.query(PlaylistBackup).filter_by(jellyfin_playlist_id=p["id"]).first()
            # Only back up playlists explicitly enrolled by the user (existing record)
            if not existing:
                skipped += 1
                continue
            if existing.exclude_from_auto:
                skipped += 1
                continue
            await _do_backup_playlist(
                db, base_url, api_key, admin_user_id, p["id"], p["name"], force=False
            )
            backed_up += 1

        settings.last_auto_backup_at = datetime.now(timezone.utc)
        db.commit()

        log.info(
            "Playlist auto-backup complete: %d backed up, %d excluded/skipped",
            backed_up, skipped,
        )

    except Exception as exc:
        log.error("Playlist auto-backup failed: %s", exc, exc_info=True)
    finally:
        db.close()


def register_playlist_backup_job(SessionLocal):
    """Register (or replace) the playlist backup job. Called from start_scheduler()."""
    from apscheduler.triggers.interval import IntervalTrigger

    db = SessionLocal()
    try:
        from models import PlaylistBackupSettings
        settings = db.query(PlaylistBackupSettings).first()
        if not settings:
            settings = PlaylistBackupSettings(id=1)
            db.add(settings)
            db.commit()
            db.refresh(settings)
        interval_hours = settings.auto_backup_interval_hours
        enabled = settings.auto_backup_enabled
    except Exception:
        interval_hours = 24
        enabled = True
    finally:
        db.close()

    from scheduler import scheduler

    if enabled:
        scheduler.add_job(
            _run_auto_backup,
            trigger=IntervalTrigger(hours=interval_hours),
            id=PLAYLIST_BACKUP_JOB_ID,
            args=[SessionLocal],
            replace_existing=True,
            name="Playlist auto-backup",
        )
        log.info("Playlist backup job registered — every %d hour(s)", interval_hours)
    else:
        try:
            scheduler.remove_job(PLAYLIST_BACKUP_JOB_ID)
        except Exception:
            pass
        log.info("Playlist backup job disabled — not registered")


def reschedule_backup_job(db):
    """
    Re-read settings from DB and update the scheduler job.
    Called by PUT /api/playlist-backups/settings and on startup.

    Uses last_auto_backup_at to compute start_date so container restarts
    and settings saves don't reset the interval clock (same pattern as
    reschedule_index_job in scheduler.py).
    """
    from models import PlaylistBackupSettings
    from apscheduler.triggers.interval import IntervalTrigger
    from scheduler import scheduler
    from database import SessionLocal
    from datetime import timedelta as _td

    settings = db.query(PlaylistBackupSettings).first()
    if not settings:
        return

    if not settings.auto_backup_enabled:
        try:
            scheduler.remove_job(PLAYLIST_BACKUP_JOB_ID)
            log.info("Playlist backup job removed (disabled by settings)")
        except Exception:
            pass
        return

    _interval = _td(hours=settings.auto_backup_interval_hours)
    _last = settings.last_auto_backup_at
    _now = datetime.now(timezone.utc)
    if _last is None:
        _next = _now + _td(minutes=2)   # never run yet — fire soon after startup
    else:
        if _last.tzinfo is None:
            _last = _last.replace(tzinfo=timezone.utc)
        _next = _last + _interval
        if _next < _now:
            _next = _now + _td(minutes=2)  # overdue — run soon but not instantly

    scheduler.add_job(
        _run_auto_backup,
        trigger=IntervalTrigger(hours=settings.auto_backup_interval_hours, start_date=_next),
        id=PLAYLIST_BACKUP_JOB_ID,
        args=[SessionLocal],
        replace_existing=True,
        name="Playlist auto-backup",
    )
    log.info(
        "Playlist backup job rescheduled — every %d hour(s), next run %s",
        settings.auto_backup_interval_hours,
        _next.strftime("%Y-%m-%d %H:%M UTC"),
    )
