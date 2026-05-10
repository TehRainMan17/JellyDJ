"""
migrations — additive ALTER TABLE migrations for SQLite (and Postgres).

This module owns ALL post-`create_all()` schema changes. Adding a new
nullable/defaulted column? Append a row to `NEW_COLUMNS` below.

We don't use Alembic — the JellyDJ deploy model is a single-container Docker
app shared across hobbyist installs, so we lean on idempotent ALTER TABLE
statements that silently no-op if the column already exists.

Called from main.py's lifespan after Base.metadata.create_all().
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from database import engine

log = logging.getLogger(__name__)


# (table_name, column_name, sql_type, default)
NEW_COLUMNS: list[tuple[str, str, str, str]] = [
    ("automation_settings", "auto_download_enabled",       "BOOLEAN",  "0"),
    ("automation_settings", "auto_download_max_per_run",   "INTEGER",  "1"),
    ("automation_settings", "auto_download_cooldown_days", "INTEGER",  "7"),
    ("automation_settings", "last_auto_download",          "DATETIME", "NULL"),
    ("discovery_queue",     "auto_queued",                 "BOOLEAN",  "0"),
    ("discovery_queue",     "auto_skip",                   "BOOLEAN",  "0"),
    # v3: play history window — last 3 play dates before last_played
    ("plays", "prev_played_1",        "DATETIME", "NULL"),
    ("plays", "prev_played_2",        "DATETIME", "NULL"),
    ("plays", "prev_played_3",        "DATETIME", "NULL"),
    # v3: skip + cooldown signals denormalised onto Play
    ("plays", "total_skips",          "INTEGER",  "0"),
    ("plays", "consecutive_skips",    "INTEGER",  "0"),
    ("plays", "voluntary_play_count", "INTEGER",  "0"),
    ("plays", "cooldown_until",       "DATETIME", "NULL"),
    ("plays", "cooldown_count",       "INTEGER",  "0"),
    # v3: billboard chart refresh schedule on AutomationSettings
    ("automation_settings", "billboard_refresh_enabled",        "BOOLEAN",  "1"),
    ("automation_settings", "billboard_refresh_interval_hours", "INTEGER",  "168"),
    ("automation_settings", "last_billboard_refresh",           "DATETIME", "NULL"),
    # v2: enrichment + scoring columns (silently skipped if already present)
    ("artist_profiles",  "replay_boost",      "REAL",     "0.0"),
    ("artist_profiles",  "related_artists",   "TEXT",     "NULL"),
    ("artist_profiles",  "tags",              "TEXT",     "NULL"),
    ("track_scores",     "cooldown_until",    "DATETIME", "NULL"),
    ("track_scores",     "replay_boost",      "REAL",     "0.0"),
    ("track_scores",     "global_popularity", "REAL",     "NULL"),
    ("track_scores",     "skip_streak",       "INTEGER",  "0"),
    ("skip_penalties",   "consecutive_skips",   "INTEGER",  "0"),
    ("skip_penalties",   "skip_streak_peak",    "INTEGER",  "0"),
    ("skip_penalties",   "last_skip_at",        "DATETIME", "NULL"),
    ("skip_penalties",   "last_completed_at",   "DATETIME", "NULL"),
    ("playback_events",  "source_context",  "TEXT",    "NULL"),
    ("playback_events",  "session_id",      "TEXT",    "NULL"),
    ("automation_settings", "enrichment_enabled",        "BOOLEAN",  "1"),
    ("automation_settings", "enrichment_interval_hours", "INTEGER",  "48"),
    ("automation_settings", "last_enrichment",           "DATETIME", "NULL"),
    # v2: enrichment columns on library_tracks
    ("library_tracks", "mbid",              "TEXT",     "NULL"),
    ("library_tracks", "lastfm_url",        "TEXT",     "NULL"),
    ("library_tracks", "global_playcount",  "INTEGER",  "NULL"),
    ("library_tracks", "global_listeners",  "INTEGER",  "NULL"),
    ("library_tracks", "tags",              "TEXT",     "NULL"),
    ("library_tracks", "enriched_at",       "DATETIME", "NULL"),
    ("library_tracks", "enrichment_source", "TEXT",     "NULL"),
    # v4: holiday tagging
    ("library_tracks", "holiday_tag",     "TEXT",    "NULL"),
    ("library_tracks", "holiday_exclude", "BOOLEAN", "0"),
    ("track_scores",   "holiday_tag",     "TEXT",    "NULL"),
    ("track_scores",   "holiday_exclude", "BOOLEAN", "0"),
    # v5: Jellyfin album container ID for reliable album exclusion matching
    ("library_tracks", "jellyfin_album_id",  "TEXT", "NULL"),
    # v6a: Jellyfin artist item ID for direct artist profile deep-links
    ("library_tracks", "jellyfin_artist_id", "TEXT", "NULL"),
    # v6: fix missing columns on track_enrichments — their absence caused
    # enrich_tracks() to crash on the expires_at staleness-check query,
    # preventing popularity_score from ever reaching TrackScore.global_popularity.
    ("track_enrichments",  "album_name",          "TEXT",     "NULL"),
    ("track_enrichments",  "source",              "TEXT",     "NULL"),
    ("track_enrichments",  "expires_at",          "DATETIME", "NULL"),
    ("track_enrichments",  "enriched_at",         "DATETIME", "NULL"),
    ("track_enrichments",  "enrichment_source",   "TEXT",     "NULL"),
    ("artist_enrichments", "top_tracks",          "TEXT",     "NULL"),
    # v6b: same bug in artist_enrichments — expires_at missing caused
    # enrich_artists() to crash, leaving artist popularity always empty.
    ("artist_enrichments", "expires_at",          "DATETIME", "NULL"),
    ("artist_enrichments", "source",              "TEXT",     "NULL"),
    ("artist_enrichments", "listeners_previous",  "INTEGER",  "NULL"),
    # bugfix: first_play_at was missing from user_replay_signals, causing every
    # db.add(UserReplaySignal(..., first_play_at=...)) to throw an
    # InvalidRequestError that was silently swallowed — no replay signals were
    # ever written, so replay_boost was always null/0 for every track.
    ("user_replay_signals", "first_play_at", "DATETIME", "NULL"),
    # v7: popularity cache refresh schedule on AutomationSettings
    ("automation_settings", "popularity_cache_refresh_interval_hours", "INTEGER",  "24"),
    ("automation_settings", "last_popularity_cache_refresh",           "DATETIME", "NULL"),
    # Auth Phase 1: Jellyfin login integration on managed_users
    ("managed_users", "is_admin",        "BOOLEAN",  "0"),
    ("managed_users", "last_login_at",   "DATETIME", "NULL"),
    # Activation model: user activates automatically on first playlist push
    ("managed_users", "has_activated",   "BOOLEAN",  "0"),
    # Phase 3: playlist template system — new column on playlist_run_items
    ("playlist_run_items", "user_playlist_id", "INTEGER", "NULL"),
    # v8: public_url on connection_settings — browser-only Jellyfin deep-link base URL
    ("connection_settings", "public_url", "TEXT", "NULL"),
    # v8: jellyfin_playlist_id on user_playlists — already in DB for most installs
    # but missing from the ORM model declaration; adding here for clean new installs
    ("user_playlists", "jellyfin_playlist_id", "TEXT", "''"),
    # Playlist backup feature — new tables are created by create_all() automatically;
    # no ALTER TABLE migrations needed for brand-new tables.
    # Revision schema upgrade: add max_revisions to playlist_backups and
    # revision_id to playlist_backup_tracks (the old backup_id column is kept
    # so existing rows aren't broken; it's just no longer written by new code).
    ("playlist_backups",       "max_revisions", "INTEGER", "6"),
    ("playlist_backup_tracks", "revision_id",   "INTEGER", "NULL"),
    # v9: canonical genre system — weighted Last.fm multi-genre profile per artist.
    # primary_genre is repurposed (still the same column) to store the dominant
    # canonical genre (normalized, Last.fm-sourced) instead of the Jellyfin file-tag.
    # canonical_genres stores the full weighted list as JSON.
    ("artist_profiles", "canonical_genres", "TEXT", "NULL"),
    # Playlist Import feature — new tables created by create_all(); no ALTER needed.
    # New nullable columns on existing tables listed below:
    ("imported_playlists",        "description",         "TEXT",     "NULL"),
    ("imported_playlists",        "last_sync_at",        "DATETIME", "NULL"),
    ("imported_playlist_tracks",  "match_score",         "REAL",     "NULL"),
    ("imported_playlist_tracks",  "suggested_album",     "TEXT",     "NULL"),
    ("imported_playlist_tracks",  "suggested_artist",    "TEXT",     "NULL"),
    ("imported_playlist_tracks",  "resolved_at",         "DATETIME", "NULL"),
    ("import_album_suggestions",  "lidarr_queued_at",    "DATETIME", "NULL"),
    # v2: enriched album suggestion metadata
    ("import_album_suggestions",  "artist_mbid",         "TEXT",     "NULL"),
    ("import_album_suggestions",  "album_mbid",          "TEXT",     "NULL"),
    ("import_album_suggestions",  "image_url",           "TEXT",     "NULL"),
    ("import_album_suggestions",  "missing_tracks",      "TEXT",     "NULL"),
    # v12: per-artist catalog popularity — track's listeners relative to artist's #1 hit
    ("track_scores", "artist_catalog_popularity", "REAL", "NULL"),
    # audio analysis: waveform-derived properties on library_tracks
    ("library_tracks", "bpm",                    "INTEGER",  "NULL"),
    ("library_tracks", "musical_key",             "TEXT",     "NULL"),
    ("library_tracks", "key_confidence",          "REAL",     "NULL"),
    ("library_tracks", "energy",                  "REAL",     "NULL"),
    ("library_tracks", "loudness_db",             "REAL",     "NULL"),
    ("library_tracks", "beat_strength",           "REAL",     "NULL"),
    ("library_tracks", "time_signature",          "INTEGER",  "NULL"),
    ("library_tracks", "acousticness",            "REAL",     "NULL"),
    ("library_tracks", "audio_analyzed_at",       "DATETIME", "NULL"),
    ("library_tracks", "audio_analysis_version",  "INTEGER",  "NULL"),
    # audio analysis schedule on automation_settings
    ("automation_settings", "audio_analysis_enabled",        "BOOLEAN", "1"),
    ("automation_settings", "audio_analysis_interval_hours", "INTEGER", "24"),
    ("automation_settings", "last_audio_analysis",           "DATETIME", "NULL"),
    # remember-me: long-lived refresh tokens (30 days vs 8 hours)
    ("refresh_tokens", "long_session", "BOOLEAN", "0"),
]


def _add_columns() -> None:
    with engine.connect() as conn:
        for table, col, typ, default in NEW_COLUMNS:
            try:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}"
                ))
                conn.commit()
            except Exception:
                pass  # column already exists — expected on every startup after the first


def _backfill_managed_users_activation() -> None:
    """Any user previously is_enabled=True (old manual-enable model) is treated
    as activated under the new self-activation model."""
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "UPDATE managed_users SET has_activated = 1 "
                "WHERE is_enabled = 1 AND has_activated = 0"
            ))
            conn.commit()
        except Exception:
            pass


def _migrate_playlist_backup_revisions() -> None:
    """Earlier versions stored tracks directly against playlist_backups via
    playlist_backup_tracks.backup_id. The new schema uses an intermediate
    playlist_backup_revisions table so we can keep multiple snapshots.

    Migration (idempotent): for every PlaylistBackup row that has no child
    PlaylistBackupRevision yet, create revision #1 from the existing track rows
    and stamp them with the new revision_id.
    """
    with engine.connect() as conn:
        try:
            orphan_backups = conn.execute(text(
                "SELECT DISTINCT pbt.backup_id FROM playlist_backup_tracks pbt "
                "WHERE pbt.backup_id IS NOT NULL "
                "  AND pbt.revision_id IS NULL "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM playlist_backup_revisions pbr "
                "    WHERE pbr.backup_id = pbt.backup_id"
                "  )"
            )).fetchall()

            for (backup_id,) in orphan_backups:
                backup_row = conn.execute(text(
                    "SELECT jellyfin_playlist_name, created_at FROM playlist_backups WHERE id = :id"
                ), {"id": backup_id}).fetchone()
                if not backup_row:
                    continue

                track_count = conn.execute(text(
                    "SELECT COUNT(*) FROM playlist_backup_tracks WHERE backup_id = :bid"
                ), {"bid": backup_id}).scalar()

                backed_up_at = backup_row[1] or "1970-01-01 00:00:00"

                conn.execute(text(
                    "INSERT INTO playlist_backup_revisions "
                    "(backup_id, revision_number, track_count, backed_up_at, label) "
                    "VALUES (:bid, 1, :tc, :bat, 'Migrated from previous version')"
                ), {"bid": backup_id, "tc": track_count, "bat": backed_up_at})
                conn.commit()

                rev_id = conn.execute(text(
                    "SELECT id FROM playlist_backup_revisions "
                    "WHERE backup_id = :bid AND revision_number = 1"
                ), {"bid": backup_id}).scalar()

                if rev_id:
                    conn.execute(text(
                        "UPDATE playlist_backup_tracks SET revision_id = :rid "
                        "WHERE backup_id = :bid AND revision_id IS NULL"
                    ), {"rid": rev_id, "bid": backup_id})
                    conn.commit()

        except Exception as exc:
            log.warning(
                "Playlist backup revision migration skipped (may not be needed): %s", exc
            )


def run_migrations() -> None:
    """Top-level migration entry point. Called from main.py during lifespan startup.

    SQLite does not support IF NOT EXISTS on ALTER TABLE, so we attempt each
    ALTER and silently swallow the error when the column already exists. This
    lets the app upgrade cleanly without requiring users to drop and recreate
    their database.

    To add a new column, append a row to NEW_COLUMNS at the top of this module.
    """
    _add_columns()
    _backfill_managed_users_activation()
    _migrate_playlist_backup_revisions()
