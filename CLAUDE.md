# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Docker (primary workflow):**
```bash
docker compose up --build -d    # Build and start both containers
docker compose logs -f          # Stream live logs
docker compose down -v          # Stop and wipe volumes
```

**Backend (local dev):**
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --reload
pytest tests/                   # Run all tests
pytest tests/test_scoring.py    # Run a single test file
```

**Frontend (local dev):**
```bash
cd frontend
npm install
npm run dev       # Vite dev server
npm run build     # Production bundle
```

## Architecture

JellyDJ is a self-hosted music recommendation engine that extends Jellyfin. It is a two-container Docker app: a FastAPI backend (port 8000) and a React+Nginx frontend (port 7879→3000).

**Backend entry points:**
- `backend/main.py` — FastAPI app, lifespan startup, all DB migrations (`_run_migrations()`)
- `backend/models.py` — Every ORM table definition (40+ tables, single file, versioned in comments v1–v12)
- `backend/scheduler.py` — APScheduler AsyncIOScheduler; all cron jobs registered here
- `backend/routers/` — One file per feature domain; all mounted in `main.py`
- `backend/services/` — Business logic (no DB session creation; sessions passed in from routers)

**Frontend entry points:**
- `frontend/src/App.jsx` — Root router with `RequireAuth` / `RequireAdmin` guards
- `frontend/src/lib/api.js` — Centralized fetch wrapper; auto-refreshes JWT on 401 then retries
- `frontend/src/contexts/AuthContext.jsx` — JWT + refresh token state

**Data flow:**
1. Jellyfin webhooks → `routers/webhooks.py` → buffered in `playback_events` table
2. APScheduler index job → `services/indexer.py` flushes events, syncs plays, calls `scoring_engine.py`
3. Scoring engine writes `artist_profiles`, `genre_profiles`, `track_scores` per user
4. Playlist engine (`services/playlist_engine.py`) reads scores and applies a block filter tree

## Database

- Default: SQLite at `/config/jellydj.db` (Docker volume); switchable to PostgreSQL via `DATABASE_URL` env var
- WAL mode enabled; 30-second busy timeout for concurrent access
- **Never drop and recreate tables.** All schema changes must be additive `ALTER TABLE` migrations added to `_run_migrations()` in `main.py`
- Session factory in `backend/database.py`; always use dependency injection (`get_db`) in routers

## Genre System (v9 — important)

- `LibraryTrack.genre` and `Play.genre` are Jellyfin file-tag genres — **historically inaccurate, do not use for features**
- Canonical genres come from Last.fm tags: `ArtistProfile.primary_genre` (dominant) and `ArtistProfile.canonical_genres` (weighted JSON with decay weights 50/25/14/7/4%)
- `GenreProfile` is derived from fractional weights of artist canonical genres
- All playlist blocks, insights, and recommendations **must** derive genre from canonical sources

## Playlist Engine (Phase 8 block system)

- Templates (`playlist_templates`) contain ordered `playlist_blocks` with a `params` JSON and `filter_tree` JSON
- Block tree semantics: siblings at the same level are **OR**; child nodes are **AND** with their parent
- Passthrough blocks (`artist_cap`, `jitter`) must be **nested inside** a filtering node or they expand to all tracks
- Block executor registry is in `services/playlist_blocks.py`

## Scoring Engine

- Artist affinity = `W_PLAY(0.45) × total_play_score + W_RECENCY(0.25) × best_recency_score + breadth_bonus ± skip_penalty ± favorite_boost(+15)`
- Track final score = `play_score + recency_score + artist_affinity + genre_affinity + novelty_bonus − skip_penalty ± cooldown ± replay_boost`
- Skip cascade: track cooldown (7d→14d→30d) + artist cooldown triggered at 5+ skips in 2 days

## Authentication

- Jellyfin is the only credential store; JellyDJ never stores passwords
- JWT access tokens (60 min); refresh tokens stored as SHA-256 hashes (8h)
- Setup account (env vars `SETUP_USERNAME`/`SETUP_PASSWORD`) is auto-disabled after Jellyfin is configured
- Browser extension uses `X-JellyDJ-Key` header (API key) instead of JWT

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Fernet encryption for stored API keys (required) |
| `JWT_SECRET_KEY` | JWT signing; falls back to `SECRET_KEY` |
| `TZ` | Timezone for scheduler display and cron |
| `DATABASE_URL` | Default `sqlite:////config/jellydj.db` |
| `YOUTUBE_RIPS_PATH` | Host path for ripped MP3s (must be in Jellyfin library) |
| `YOUTUBE_COOKIES_FILE` | Netscape cookies.txt for geo-restricted YouTube content |
| `WEBHOOK_SECRET` | Validates `X-Jellyfin-Token` header on webhook receiver |

## APScheduler Jobs

All jobs are registered in `scheduler.py` and intervals are stored in the `automation_settings` table:

| Job | Default interval |
|---|---|
| `play_history_index` | 6h |
| `discovery_refresh` | 24h |
| `enrichment` | 48h |
| `popularity_cache_refresh` | 24h |
| `user_playlist_autopush` | 15m |
| `playlist_backup` | 24h |
| `billboard_refresh` | 168h (1 week) |

To reschedule a job at runtime, call the appropriate `reschedule_*` helper in `scheduler.py` — do not restart the app.

## External Services

- **Jellyfin** — Source of truth for library and playback history
- **Last.fm** — Tags, similar artists, listener counts (primary enrichment source)
- **Spotify** — Popularity scores (0–100)
- **MusicBrainz** — MBIDs, fallback metadata
- **Lidarr** — Sends approved discovery albums for download
- **Billboard** — Hot 100 scraper (`services/popularity/billboard_adapter.py`)
- **yt-dlp** — YouTube ripping and external playlist fetching
