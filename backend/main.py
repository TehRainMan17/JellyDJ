
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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database import engine, Base, SessionLocal
import models  # noqa: F401 — imported so SQLAlchemy registers all table definitions
from routers import (
    connections, external_apis, indexer, recommender,
    webhooks, discovery, playlists, insights, automation, exclusions,
)
from routers.auth import router as auth_router


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
        ("managed_users", "is_admin",      "BOOLEAN",  "0"),
        ("managed_users", "last_login_at", "DATETIME", "NULL"),
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
                {"expires_at": None, "popularity_score": None, "source": None},
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

    # Add any new columns that appeared since the user's last install
    _run_migrations()

    # Backfill any stale popularity cache entries (one-time, safe to repeat)
    _backfill_top_album_cache()

    # Expire TrackEnrichment rows stored under "Various Artists" so they
    # get re-fetched with the real track artist on the next enrichment run.
    _fix_various_artists_enrichment()

    # Purge any expired refresh tokens left over from previous sessions
    _cleanup_expired_refresh_tokens()

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

    yield  # application handles requests here

    # Graceful shutdown: stop the scheduler without blocking on running jobs
    from scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="JellyDJ",
    version="0.1.0",
    description="Self-hosted music recommendation engine for Jellyfin",
    lifespan=lifespan,
)

# Open CORS policy — the React frontend is served by a separate Nginx container
# and may run on a different port. For self-hosted deployments this is safe.
# If you expose JellyDJ to the internet, restrict allow_origins to your domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
app.include_router(automation.router)     # /api/automation     — scheduler settings + triggers
app.include_router(exclusions.router)     # /api/exclusions     — manual album exclusions
app.include_router(auth_router)           # /api/auth           — Jellyfin login + JWT tokens


@app.get("/api/health")
async def health_check():
    """
    Lightweight health check polled by the Docker Compose healthcheck.
    Returns 200 as soon as the app is running — no DB or external calls.
    The frontend container waits for this to pass before starting.
    """
    return {"status": "ok", "service": "JellyDJ", "version": "0.1.0"}


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
