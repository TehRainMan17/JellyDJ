
# Contributing to JellyDJ

Thanks for your interest in contributing! This document covers how the project is structured, how to run it locally for development, and the conventions used throughout.

---

## Architecture overview

```
jellydj/
├── backend/                FastAPI Python backend
│   ├── main.py             App entry point, startup, CORS
│   ├── models.py           SQLAlchemy ORM models (all tables)
│   ├── database.py         SQLAlchemy engine + session factory
│   ├── scheduler.py        APScheduler background jobs
│   ├── crypto.py           Fernet encryption for stored credentials
│   ├── routers/            One file per API prefix group
│   │   ├── connections.py  /api/connections — Jellyfin + Lidarr
│   │   ├── indexer.py      /api/indexer — sync + job status
│   │   ├── automation.py   /api/automation — scheduler settings + triggers
│   │   ├── discovery.py    /api/discovery — recommendation queue
│   │   ├── playlists.py    /api/playlists — generate + history
│   │   ├── webhooks.py     /api/webhooks — Jellyfin playback events
│   │   ├── insights.py     /api/insights — listening stats
│   │   └── external_apis.py /api/external-apis — Spotify/Last.fm keys
│   └── services/           Business logic, no HTTP concerns
│       ├── indexer.py      Play history sync, taste profile rebuild
│       ├── scoring_engine.py  Three-phase track scoring (artist → genre → track)
│       ├── recommender.py  Playlist + new-album recommendation
│       ├── playlist_writer.py Jellyfin playlist creation/overwrite
│       ├── library_scanner.py Full library snapshot into LibraryTrack
│       ├── library_dedup.py   Fuzzy album/track dedup before download
│       └── popularity/     External API adapters (Last.fm, Spotify, MusicBrainz)
│           ├── aggregator.py  Unified interface + 24h caching
│           ├── lastfm_adapter.py
│           ├── spotify_adapter.py
│           ├── musicbrainz_adapter.py
│           └── billboard_adapter.py
└── frontend/               React + Tailwind + Vite
    ├── src/
    │   ├── App.jsx          Root router
    │   ├── index.css        Global styles + CSS custom properties (theme tokens)
    │   ├── components/      Shared UI components
    │   │   ├── Layout.jsx   Sidebar + topbar shell
    │   │   ├── AutomationPanel.jsx
    │   │   ├── JobProgress.jsx    Live progress bar for background jobs
    │   │   └── ...
    │   ├── hooks/
    │   │   └── useJobStatus.js    Polling hook for background job state
    │   └── pages/           One file per route
    └── index.html
```

## Data flow

1. **Indexer** (`/api/indexer/full-scan`) → `services/indexer.py`
   - Runs library scan → fetches per-user play history from Jellyfin → upserts `Play` rows
   - Calls `services/scoring_engine.py` which rebuilds `ArtistProfile`, `GenreProfile`, `TrackScore`

2. **Webhooks** (`/api/webhooks/jellyfin`) → `routers/webhooks.py`
   - Receives `PlaybackStart` / `PlaybackProgress` / `PlaybackStop` from Jellyfin
   - Calculates completion percentage → updates `SkipPenalty` table
   - Skip penalties are read back by the scoring engine on next index run

3. **Playlists** (`/api/playlists/generate`) → `services/playlist_writer.py`
   - Queries `TrackScore` (pre-computed by scoring engine) for each user
   - Applies per-artist diversity cap and score jitter
   - Creates/overwrites named playlists in Jellyfin via the API

4. **Discovery queue** (`/api/discovery/populate`) → `services/recommender.py`
   - Takes user taste profile → finds similar artists via popularity adapters
   - Deduplicates against `LibraryTrack` via `library_dedup.py`
   - Writes pending items to `DiscoveryQueueItem`; user approves → sends to Lidarr

## Scoring system

The scoring engine runs in three phases after each index:

1. **ArtistProfile** — aggregate all plays + skips for each artist → affinity score
2. **GenreProfile** — same at genre level
3. **TrackScore** — pre-compute final score for every track in the library

Key constants to tune are in `services/scoring_engine.py` at the top of the file, all documented with rationale.

## Running locally (development)

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

Set `DATABASE_URL=sqlite:///./dev.db` in a local `.env` for development.

## Adding a new popularity adapter

1. Create `backend/services/popularity/myadapter.py` inheriting from `BasePopularityAdapter`
2. Implement `is_configured()`, `get_artist_info()`, `get_album_popularity()`, `get_trending_tracks()`
3. Import and add it to the adapter list in `popularity/aggregator.py`
4. If it needs credentials, add them to `ExternalApiSettings` in `models.py` and wire up the UI in `pages/Settings.jsx`

## Code style

- Python: PEP 8, type hints on all function signatures, docstrings on all public functions
- JavaScript/JSX: functional components, hooks only (no class components), CSS custom properties for all colours (no hardcoded hex in components)
- Commits: conventional commits style preferred (`feat:`, `fix:`, `docs:`, `refactor:`)

## Pull requests

- Keep changes focused — one feature or fix per PR
- Update the relevant docstrings/comments if you change logic
- If you change scoring constants, document the rationale in the PR description
