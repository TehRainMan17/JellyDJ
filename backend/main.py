"""
JellyDJ — FastAPI application entry point.

Startup sequence (managed by the FastAPI lifespan context):
  1. SQLAlchemy creates all tables that don't exist yet (create_all)
  2. _run_migrations() safely adds new columns to existing tables so upgrades
     don't require wiping the database
  3. The APScheduler background scheduler starts and registers the four
     periodic jobs: index, discovery refresh, playlist regen, auto-download

All API routes are split across routers in the /routers directory.
The CORS middleware allows all origins so the React frontend (served by
a separate Nginx container) can communicate with the backend on any port.
"""

import os
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database import engine, Base, SessionLocal
import models  # noqa: F401 — imported so SQLAlchemy registers all table definitions
from routers import (
    connections, external_apis, indexer, recommender,
    webhooks, discovery, playlists, insights, automation, exclusions,
)
from routers.graph import router as graph_router
from routers.auth import router as auth_router
from routers.playlist_templates import router as playlist_templates_router
from routers.user_playlists import router as user_playlists_router
from routers.admin_defaults import router as admin_defaults_router


def _run_migrations():
    """
    Safe column-level migrations for SQLite.

    SQLite does not support IF NOT EXISTS on ALTER TABLE, so we attempt
    each ALTER and silently swallow the error when the column already
    exists. This lets the app upgrade cleanly without requiring users
    to drop and recreate their database.

    Add a new row here whenever you introduce a nullable/defaulted column
    to an existing model. Columns defined in the initial schema are handled
    by create_all() above and do not need to be listed here.
    """
    from sqlalchemy import text
    new_columns = [
        # (table_name,          column_name,                   sql_type,   default)
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
        ("library_tracks", "jellyfin_album_id", "TEXT", "NULL"),
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
    ]
    with engine.connect() as conn:
        for table, col, typ, default in new_columns:
            try:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}"
                ))
                conn.commit()
            except Exception:
                pass  # column already exists — expected on every startup after the first

    # Backfill: any user previously is_enabled=True (old manual-enable model)
    # is considered activated under the new self-activation model.
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "UPDATE managed_users SET has_activated = 1 "
                "WHERE is_enabled = 1 AND has_activated = 0"
            ))
            conn.commit()
        except Exception:
            pass


def _fix_various_artists_enrichment():
    """
    One-time (idempotent) data fix: nulls out popularity_score and expires_at
    on TrackEnrichment rows whose artist_name is a compilation catch-all.
    On the next enrichment run, _resolve_track_artist() will use the correct
    per-track artist (from LibraryTrack.album_artist or a re-index) and
    fetch real scores from Last.fm.
    Safe to run on every startup — only touches rows that still have the problem.
    """
    db = SessionLocal()
    try:
        va_names = [
            "various artists", "various", "va", "v.a.", "v/a",
            "multiple artists", "assorted artists", "unknown artist", "unknown",
        ]
        from models import TrackEnrichment
        import sqlalchemy as _sa
        # Expire rows still named "Various Artists" (library_scanner fix may not
        # have run yet on their index pass)
        updated = (
            db.query(TrackEnrichment)
            .filter(
                _sa.func.lower(TrackEnrichment.artist_name).in_(va_names)
            )
            .update(
                {"expires_at": None, "popularity_score": None, "source": "expired"},
                synchronize_session=False,
            )
        )
        # Also expire rows that were "enriched" but came back with null scores
        # (source="none" means Last.fm returned nothing — retry after artist fix)
        updated_nulls = (
            db.query(TrackEnrichment)
            .filter(
                TrackEnrichment.source == "none",
                TrackEnrichment.popularity_score.is_(None),
            )
            .update(
                {"expires_at": None},
                synchronize_session=False,
            )
        )
        updated += updated_nulls
        if updated:
            db.commit()
            import logging
            logging.getLogger(__name__).info(
                f"VA fix: expired {updated} 'Various Artists' track enrichment rows "
                f"— they will be re-enriched with correct artist on next run"
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"VA enrichment fix skipped: {e}")
    finally:
        db.close()


def _cleanup_expired_refresh_tokens():
    """
    Delete RefreshToken rows whose expires_at is in the past.

    Run at startup and daily via the scheduler so the table stays small.
    Safe to run concurrently — each DELETE is atomic.
    """
    import logging
    from datetime import datetime, timezone
    from models import RefreshToken
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        deleted = (
            db.query(RefreshToken)
            .filter(RefreshToken.expires_at < now)
            .delete(synchronize_session=False)
        )
        if deleted:
            db.commit()
            logging.getLogger(__name__).info(
                "Deleted %d expired refresh token(s) on startup", deleted
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("Refresh token cleanup failed: %s", exc)
    finally:
        db.close()


def _warn_if_setup_backdoor_active():
    """
    Emit a WARNING at startup if SETUP_ALLOW_AFTER_CONFIGURE=true is set
    while Jellyfin is already configured in the database.

    In that state, the setup credentials act as a permanent admin backdoor
    with no expiry and no per-user DB row.  Operators often set this flag
    during initial setup and forget to remove it.  The warning appears in
    `docker logs` on every restart so it cannot be silently forgotten.

    This is advisory only — the app still starts normally.  Forcing a hard
    exit here would lock operators out of a running instance if they forget
    to clean up the flag, which is worse than the risk being warned about.
    """
    import logging as _sl
    _log = _sl.getLogger("jellydj.setup")

    allow_after = os.getenv("SETUP_ALLOW_AFTER_CONFIGURE", "").lower() in ("1", "true", "yes")
    if not allow_after:
        return

    setup_user = os.getenv("SETUP_USERNAME", "").strip()
    setup_pass = os.getenv("SETUP_PASSWORD", "").strip()
    if not (setup_user and setup_pass):
        return  # flag is set but creds are absent — endpoint will reject anyway

    # Check whether Jellyfin is configured without pulling in the router module
    db = SessionLocal()
    try:
        from models import ConnectionSettings
        row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
        jellyfin_configured = bool(row and row.base_url)
    except Exception:
        jellyfin_configured = False
    finally:
        db.close()

    if jellyfin_configured:
        sep = "=" * 70
        _log.warning(
            "\n%s\n"
            "  SECURITY WARNING: setup backdoor is active post-configure\n"
            "\n"
            "  SETUP_ALLOW_AFTER_CONFIGURE=true is set and Jellyfin is already\n"
            "  configured. The setup credentials (SETUP_USERNAME / SETUP_PASSWORD)\n"
            "  can still be used to obtain an admin token at any time.\n"
            "\n"
            "  Recommended action:\n"
            "    1. Remove SETUP_ALLOW_AFTER_CONFIGURE from your .env\n"
            "    2. Remove SETUP_USERNAME and SETUP_PASSWORD from your .env\n"
            "    3. Restart the stack\n"
            "\n"
            "  Every use of the setup login while this flag is active is recorded\n"
            "  in the system_events table (event_type='setup_login').\n"
            "%s",
            sep, sep,
        )
    else:
        # Flag is set but Jellyfin isn't configured yet — normal bootstrap state.
        # Log at INFO only so first-time setup isn't noisy.
        _log.info(
            "Setup mode active (SETUP_ALLOW_AFTER_CONFIGURE=true, Jellyfin not yet "
            "configured). Remember to remove this flag once setup is complete."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan: runs setup before the first request, teardown on shutdown.

    Using lifespan instead of the older on_event("startup") / on_event("shutdown")
    pattern because it's the current FastAPI recommendation and gives us clean
    shutdown behaviour via the yield boundary.
    """
    # Create all tables defined in models.py (no-ops if they already exist)
    Base.metadata.create_all(bind=engine)

    # ── Reset stale job states from previous process ──────────────────────────
    # If the server crashed or was killed while a background job was running,
    # the JobState row stays running=True in the DB. On the next boot every
    # trigger endpoint sees "already running" and refuses to start — the job
    # is permanently locked until someone manually resets the DB.
    # Fix: on every startup, mark all running jobs as not-running so they can
    # be triggered fresh. Any job that was genuinely mid-run is dead anyway
    # (its thread died with the previous process).
    try:
        _boot_db = SessionLocal()
        try:
            from models import JobState
            stale = _boot_db.query(JobState).filter_by(running=True).all()
            if stale:
                import logging as _logging
                _boot_log = _logging.getLogger(__name__)
                for row in stale:
                    row.running = False
                    row.phase = f"Interrupted (server restarted)"
                _boot_db.commit()
                _boot_log.warning(
                    "Startup: reset %d stale running job(s): %s",
                    len(stale), [r.job_id for r in stale]
                )
        finally:
            _boot_db.close()
    except Exception:
        pass  # non-fatal — don't prevent startup

    # Add any new columns that appeared since the user's last install
    _run_migrations()

    # Backfill any stale popularity cache entries (one-time, safe to repeat)
    _backfill_top_album_cache()

    # Expire TrackEnrichment rows stored under "Various Artists" so they
    # get re-fetched with the real track artist on the next enrichment run.
    _fix_various_artists_enrichment()

    # Purge any expired refresh tokens left over from previous sessions
    _cleanup_expired_refresh_tokens()

    # Seed system prefab playlist templates on a fresh DB (no-op if rows exist),
    # then migrate any existing system templates to the new filter_tree block
    # format. Both operations are idempotent and touch only is_system=True rows.
    from services.prefab_seeder import seed_prefabs, migrate_system_templates
    _db = SessionLocal()
    try:
        seed_prefabs(_db)
        migrate_system_templates(_db)
    finally:
        _db.close()

    # Start the APScheduler background job scheduler
    from scheduler import start_scheduler
    start_scheduler(SessionLocal)

    # Register daily refresh token cleanup in the scheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from scheduler import scheduler
    scheduler.add_job(
        _cleanup_expired_refresh_tokens,
        trigger=IntervalTrigger(hours=24),
        id="refresh_token_cleanup",
        replace_existing=True,
        name="Refresh token expiry cleanup",
    )

    # Warn if the setup backdoor flag is active post-configure.
    # This runs after create_all + migrations so the DB is guaranteed to exist.
    _warn_if_setup_backdoor_active()

    yield  # application handles requests here

    # Graceful shutdown: stop the scheduler without blocking on running jobs
    from scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="JellyDJ",
    version="1.0.0",
    description="Self-hosted music recommendation engine for Jellyfin",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# JellyDJ uses Authorization: Bearer tokens, not cookies — allow_credentials
# must be False when allow_origins=["*"].  Combining credentials=True with a
# wildcard origin is a known exploit that lets any website make credentialed
# cross-origin requests as the logged-in user.
#
# To restrict to specific origins (recommended for internet-facing installs):
#   CORS_ORIGINS=https://jellydj.yourdomain.com   (comma-separated for multiple)
_raw = os.getenv("CORS_ORIGINS", "")
_origins = [o.strip() for o in _raw.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,   # safe: we use Bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers — each module owns its own URL prefix
app.include_router(connections.router)     # /api/connections   — Jellyfin + Lidarr credentials
app.include_router(external_apis.router)  # /api/external-apis — Spotify, Last.fm, MusicBrainz
app.include_router(indexer.router)        # /api/indexer        — play history sync + library scan
app.include_router(recommender.router)    # /api/recommender    — taste profile inspection
app.include_router(webhooks.router)       # /api/webhooks       — Jellyfin playback events
app.include_router(discovery.router)      # /api/discovery      — new album recommendation queue
app.include_router(playlists.router)      # /api/playlists      — playlist generation + history
app.include_router(insights.router)       # /api/insights       — listening stats + charts
app.include_router(graph_router)          # /api/graph          — artist/genre network map
app.include_router(automation.router)     # /api/automation     — scheduler settings + triggers
app.include_router(exclusions.router)     # /api/exclusions     — manual album exclusions
app.include_router(auth_router)           # /api/auth           — Jellyfin login + JWT tokens
app.include_router(playlist_templates_router)  # /api/playlist-templates — template + block CRUD
app.include_router(user_playlists_router)      # /api/user-playlists     — user playlist CRUD + push
app.include_router(admin_defaults_router)      # /api/admin/default-playlists — admin default playlist config


@app.get("/api/health")
async def health_check():
    """
    Lightweight health check polled by the Docker Compose healthcheck.
    Returns 200 as soon as the app is running — no DB or external calls.
    The frontend container waits for this to pass before starting.
    """
    return {"status": "ok", "service": "JellyDJ", "version": "1.0.0"}


def _backfill_top_album_cache():
    """
    One-time migration: add 'album' key to top_album cache entries that only
    have 'name' (written by the Last.fm adapter before the key was normalised).
    Safe to run on every startup — skips rows that already have 'album'.
    """
    import json
    db = SessionLocal()
    try:
        from models import PopularityCache
        rows = db.query(PopularityCache).filter(
            PopularityCache.cache_key.like("top_album:%")
        ).all()
        fixed = 0
        for row in rows:
            try:
                d = json.loads(row.payload)
                if "name" in d and "album" not in d:
                    d["album"] = d["name"]
                    row.payload = json.dumps(d)
                    fixed += 1
            except Exception:
                pass
        if fixed:
            db.commit()
            import logging
            logging.getLogger(__name__).info(
                f"Backfilled 'album' key in {fixed} top_album cache entries"
            )
    except Exception:
        pass
    finally:
        db.close()
