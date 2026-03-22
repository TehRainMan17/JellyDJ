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
        )

        admin_user_id = await _get_admin_user_id(base_url, api_key)
        playlists = await _fetch_jellyfin_playlists(base_url, api_key, admin_user_id)

        backed_up = 0
        skipped = 0
        for p in playlists:
            existing = db.query(PlaylistBackup).filter_by(jellyfin_playlist_id=p["id"]).first()
            if existing and existing.exclude_from_auto:
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
    Called by PUT /api/playlist-backups/settings.
    """
    from models import PlaylistBackupSettings
    from apscheduler.triggers.interval import IntervalTrigger
    from scheduler import scheduler
    from database import SessionLocal

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

    scheduler.add_job(
        _run_auto_backup,
        trigger=IntervalTrigger(hours=settings.auto_backup_interval_hours),
        id=PLAYLIST_BACKUP_JOB_ID,
        args=[SessionLocal],
        replace_existing=True,
        name="Playlist auto-backup",
    )
    log.info(
        "Playlist backup job rescheduled — every %d hour(s)",
        settings.auto_backup_interval_hours,
    )
