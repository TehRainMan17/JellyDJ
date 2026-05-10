
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
import logging
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
from database import engine, Base, SessionLocal
import models  # noqa: F401 — imported so SQLAlchemy registers all table definitions
from routers import (
    connections, external_apis, indexer, recommender,
    webhooks, discovery, playlists, insights, automation, exclusions,
)
from routers.graph import router as graph_router
from routers.auth import router as auth_router
from routers.mobile import router as mobile_router
from routers.playlist_templates import router as playlist_templates_router
from routers.user_playlists import router as user_playlists_router
from routers.admin_defaults import router as admin_defaults_router
from routers.playlist_backups import router as playlist_backups_router
from routers.playlist_import import router as playlist_import_router
from routers.youtube_rip import router as youtube_rip_router
from routers.audio_analysis import router as audio_analysis_router
from routers.debug_aa import router as debug_aa_router

from services.migrations import run_migrations as _run_migrations


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


def _seed_env_credentials():
    """
    Seed ConnectionSettings and ExternalApiSettings from .env variables.

    Idempotent — only creates rows if they don't already exist.
    Allows users to pre-configure everything via .env without manual UI setup.
    """
    import logging
    from crypto import encrypt
    from models import ConnectionSettings, ExternalApiSettings

    _log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        # Jellyfin
        jellyfin_url = os.getenv("JELLYFIN_URL", "").strip()
        jellyfin_key = os.getenv("JELLYFIN_API_KEY", "").strip()
        if jellyfin_url and jellyfin_key:
            existing = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
            if not existing:
                db.add(ConnectionSettings(
                    service="jellyfin",
                    base_url=jellyfin_url,
                    api_key_encrypted=encrypt(jellyfin_key),
                    is_connected=False,
                ))
                db.commit()
                _log.info("Seeded Jellyfin connection from .env")

        # Lidarr
        lidarr_url = os.getenv("LIDARR_URL", "").strip()
        lidarr_key = os.getenv("LIDARR_API_KEY", "").strip()
        if lidarr_url and lidarr_key:
            existing = db.query(ConnectionSettings).filter_by(service="lidarr").first()
            if not existing:
                db.add(ConnectionSettings(
                    service="lidarr",
                    base_url=lidarr_url,
                    api_key_encrypted=encrypt(lidarr_key),
                    is_connected=False,
                ))
                db.commit()
                _log.info("Seeded Lidarr connection from .env")

        # Spotify
        spotify_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        if spotify_id and spotify_secret:
            existing = db.query(ExternalApiSettings).filter_by(key="spotify_client_id").first()
            if not existing:
                db.add(ExternalApiSettings(key="spotify_client_id", value_encrypted=encrypt(spotify_id)))
                db.add(ExternalApiSettings(key="spotify_client_secret", value_encrypted=encrypt(spotify_secret)))
                db.commit()
                _log.info("Seeded Spotify credentials from .env")

        # Last.fm
        lastfm_key = os.getenv("LASTFM_API_KEY", "").strip()
        lastfm_secret = os.getenv("LASTFM_API_SECRET", "").strip()
        if lastfm_key and lastfm_secret:
            existing = db.query(ExternalApiSettings).filter_by(key="lastfm_api_key").first()
            if not existing:
                db.add(ExternalApiSettings(key="lastfm_api_key", value_encrypted=encrypt(lastfm_key)))
                db.add(ExternalApiSettings(key="lastfm_api_secret", value_encrypted=encrypt(lastfm_secret)))
                db.commit()
                _log.info("Seeded Last.fm credentials from .env")

    except Exception as exc:
        _log.warning("Failed to seed credentials from .env: %s", exc)
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
    # ── Startup security checks ───────────────────────────────────────────────
    # Validate critical secrets before accepting any requests.  A missing or
    # insecure key must crash at boot with a clear diagnostic rather than
    # failing silently on the first authenticated request (which could be hard
    # to trace back to a misconfigured .env).
    import auth as _auth_module
    import crypto as _crypto_module
    _sec_log = logging.getLogger("jellydj.startup")

    try:
        _auth_module._secret_key()
    except RuntimeError as _exc:
        _sec_log.critical(
            "Startup aborted — JWT signing key is missing or insecure: %s\n"
            "Generate a strong key: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then set JWT_SECRET_KEY=<value> (or SECRET_KEY) in your .env and restart.",
            _exc,
        )
        raise SystemExit(1) from _exc

    try:
        _crypto_module._get_fernet()
    except RuntimeError as _exc:
        _sec_log.critical(
            "Startup aborted — SECRET_KEY is missing or insecure: %s\n"
            "Generate a strong key: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then set SECRET_KEY=<value> in your .env and restart.",
            _exc,
        )
        raise SystemExit(1) from _exc

    # Warn if the webhook endpoint has been opened without a shared secret.
    # This is safe on a fully private LAN, but dangerous on internet-facing
    # deployments — any host can inject playback events.
    if os.getenv("WEBHOOK_SECRET_REQUIRED", "true").strip().lower() in ("false", "0", "no"):
        _sec_log.warning(
            "SECURITY: WEBHOOK_SECRET_REQUIRED=false — the /api/webhooks/jellyfin "
            "endpoint accepts unauthenticated requests.  Any reachable host can "
            "inject playback events and manipulate listening history.  Set "
            "WEBHOOK_SECRET in .env to enable authentication."
        )

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

    # Seed .env credentials into the database (idempotent, only on first setup)
    _seed_env_credentials()

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
    version="1.2.0",
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
app.include_router(mobile_router)         # /api/mobile         — Android/iOS companion endpoints
app.include_router(playlist_templates_router)  # /api/playlist-templates — template + block CRUD
app.include_router(user_playlists_router)      # /api/user-playlists     — user playlist CRUD + push
app.include_router(admin_defaults_router)      # /api/admin/default-playlists — admin default playlist config
app.include_router(playlist_backups_router)    # /api/playlist-backups   — playlist backup + restore
app.include_router(playlist_import_router)     # /api/import             — external playlist import
app.include_router(youtube_rip_router)         # /api/import/youtube-rip — YouTube audio → MP3 → Jellyfin
app.include_router(audio_analysis_router)      # /api/audio-analysis    — media paths, stats, key list
app.include_router(debug_aa_router)            # /api/debug             — Android Auto telemetry sink


@app.get("/api/health")
async def health_check():
    """
    Lightweight health check polled by the Docker Compose healthcheck.
    Returns 200 as soon as the app is running — no DB or external calls.
    The frontend container waits for this to pass before starting.
    """
    return {"status": "ok", "service": "JellyDJ", "version": "1.2.0"}


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

