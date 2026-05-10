# JellyDJ Architecture

A contributor-oriented reference for the JellyDJ codebase. For day-to-day commands, see `README.md`. For known refactor opportunities, see `AUDIT_FINDINGS.md`. For Claude Code navigation hints, see `CLAUDE.md`.

---

## What JellyDJ is

A self-hosted music recommendation engine that extends Jellyfin. It ingests Jellyfin playback history, builds per-user taste profiles, and generates curated playlists which it pushes back to Jellyfin. It also imports external playlists (Spotify/Tidal/YouTube Music), matches them to your library, and recommends albums for Lidarr to download.

Two containers: FastAPI backend (`:8000`) and React+Nginx frontend (host `:7879` → container `:3000`). SQLite by default at `/config/jellydj.db`.

---

## Backend layout

```
backend/
├── main.py             # FastAPI app, lifespan, _run_migrations(), router mounts
├── scheduler.py        # APScheduler — all cron jobs registered here
├── models.py           # All ORM tables (40+) — single file, comments mark v1–v13
├── database.py         # Engine, SQLite WAL config, get_db dependency
├── crypto.py           # Fernet encrypt/decrypt for stored credentials
├── routers/            # FastAPI routers — one per feature domain
└── services/           # Business logic — DB sessions injected, never created here
    └── popularity/     # Pluggable popularity adapters (Spotify, Last.fm, MB, Billboard)
```

### Lifespan (main.py:507–641)

On startup, in order:
1. Security checks (warn if setup backdoor still active)
2. `Base.metadata.create_all()`
3. Reset stale `JobState` rows (jobs that were running when the app died)
4. `_run_migrations()` — additive ALTER TABLE statements (lines 46–281)
5. `_seed_env_credentials()` — Jellyfin/Lidarr creds from env vars
6. Prefab playlist templates seeded
7. Scheduler starts and reads `automation_settings` for intervals

On shutdown: scheduler.shutdown(wait=True).

### Data flow — playback to scoring

```
Jellyfin webhook
  → routers/webhooks.py                  (validates X-Jellyfin-Token)
  → buffered into playback_events table
  → APScheduler job (`play_history_index`, every 6h)
  → services/indexer.py
      ├── flushes playback_events into Play rows
      ├── pulls full play history from Jellyfin
      ├── calls services/library_scanner.py if needed
      └── calls services/scoring_engine.py
              ├── builds artist_profiles
              ├── builds genre_profiles  (from canonical Last.fm tags, not file genres)
              └── writes track_scores per user
```

### Data flow — playlist generation

```
User clicks "Push" on a UserPlaylist
  → routers/user_playlists.py
  → services/playlist_engine.py
      └── walks playlist_blocks tree (template's filter_tree)
          └── services/playlist_blocks.py — block executors per type
                  └── reads track_scores + library_tracks
  → services/playlist_writer.py — pushes to Jellyfin via httpx
  → records PlaylistRun + PlaylistRunItems
```

Block tree semantics: **siblings = OR, children = AND with parent**. Passthrough blocks (`artist_cap`, `jitter`) must be nested inside a filtering node, otherwise they expand to the entire library.

### Data flow — external playlist import

```
User pastes URL or browser extension calls /api/import/playlists
  → routers/playlist_import.py
  → services/external_playlist_fetcher.py  (Spotify embed scrape, yt-dlp for Tidal/YT)
  → services/playlist_import.py — 3-pass fuzzy match:
        1. exact match
        2. fuzzy track match
        3. fuzzy artist + album match
  → unmatched tracks get album suggestions via services/recommender.py popularity data
  → ImportedPlaylist + ImportedPlaylistTrack rows created
```

### Scoring engine summary

`scoring_engine.py` produces a `TrackScore` per (user, track) row:

```
artist_affinity = W_PLAY(0.45) * total_play_score
                + W_RECENCY(0.25) * best_recency_score
                + breadth_bonus
                ± skip_penalty
                ± favorite_boost (+15)

track_final    = play_score
                + recency_score
                + artist_affinity
                + genre_affinity
                + novelty_bonus
                − skip_penalty
                ± cooldown
                ± replay_boost
```

Skip cascade: per-track cooldown 7d → 14d → 30d on consecutive skips; artist cooldown triggers at 5+ skips in 2 days.

### Genre system (v9 canonical)

Two distinct sources of "genre" exist. **Always use the canonical source for features.**

| Column | Status | Source |
|---|---|---|
| `LibraryTrack.genre` | historical only | Jellyfin file tag (often inaccurate) |
| `Play.genre` | historical only | Jellyfin file tag at playback time |
| `ArtistProfile.primary_genre` | **canonical** | derived from Last.fm tags (top weighted) |
| `ArtistProfile.canonical_genres` | **canonical** | weighted JSON, decay 50/25/14/7/4% |
| `GenreProfile` | **canonical** | derived per-user from artist canonical genres |

### Authentication

- Jellyfin is the only credential store. JellyDJ never stores user passwords.
- JWT access tokens (60 min). Refresh tokens stored as SHA-256 hashes (8h TTL).
- Setup-mode account (env `SETUP_USERNAME`/`SETUP_PASSWORD`) is auto-disabled once Jellyfin connection is configured.
- Browser extension uses `X-JellyDJ-Key` header (per-user API key) instead of JWT.

### APScheduler jobs

All registered in `scheduler.py`. Intervals stored in `automation_settings` and re-applied at runtime via `reschedule_automation_jobs()` (`scheduler.py:679`).

| Job ID | Default | Function |
|---|---|---|
| `play_history_index` | 6h | `_job_run_index` |
| `discovery_refresh` | 24h | `_job_discovery_refresh` |
| `auto_download` | 24h | `_job_auto_download` |
| `enrichment` | 48h | `_job_enrichment` (daemon thread) |
| `popularity_cache_refresh` | 24h | `_job_popularity_cache_refresh` |
| `user_playlist_autopush` | 15m | `_run_user_playlist_autopush` |
| `playlist_backup` | 24h | (in `playlist_backups` router) |
| `billboard_refresh` | 168h | `_job_billboard_refresh` |
| `audio_analysis` | nightly | `_job_audio_analysis` (daemon + watchdog) |

Daemon-thread jobs use `_job_stale_watchdog()` (line 261) to reset `JobState` rows that have been running >4h.

### Database

- SQLite default, PostgreSQL via `DATABASE_URL` env var.
- WAL mode + 30s busy timeout (handles concurrent writes from scheduler + API).
- **Never drop and recreate tables.** All schema changes go into `_run_migrations()` as additive `ALTER TABLE` statements.
- `get_db()` is a generator-style FastAPI dependency. Background jobs use bare `SessionLocal()` (see AUDIT_FINDINGS B4 — this is a known footgun).

### External services

| Service | Used for |
|---|---|
| Jellyfin | Library + playback history (source of truth) |
| Last.fm | Tags, similar artists, listener counts (primary enrichment) |
| Spotify | Popularity scores 0–100 |
| MusicBrainz | MBIDs, fallback metadata |
| Lidarr | Sends approved discovery albums for download |
| Billboard | Hot 100 scraper (`services/popularity/billboard_adapter.py`) |
| yt-dlp | YouTube ripping + Tidal/YT Music playlist fetching |

---

## Frontend layout

```
frontend/src/
├── App.jsx                  # Router with RequireAuth / RequireAdmin guards
├── main.jsx                 # Entry, wraps AuthProvider
├── index.css                # Tailwind tokens + design system component classes
├── contexts/
│   └── AuthContext.jsx      # JWT state + silent refresh timer
├── lib/
│   └── api.js               # Fetch wrapper with auto-401-retry
├── hooks/
│   ├── useJellyfinUrl.js    # Cached Jellyfin base URL for client-side links
│   └── useJobStatus.js      # 5-job adaptive poller (2s/5s/30s)
├── pages/                   # Top-level routes
└── components/              # Shared UI
    └── playlist/            # BlockEditor, BlockCard, BlockChainEditor, etc.
```

### State + auth

`AuthContext` holds `user`, `accessToken`, `refreshToken`. `lib/api.js` reads the token from context, attaches `Authorization: Bearer …`, and on 401 it transparently calls `/api/auth/refresh` and retries the original request once.

### Where features live

| Feature | Page | Key components |
|---|---|---|
| Dashboard / activity feed | `pages/Dashboard.jsx` | `JobProgress`, inline `BillboardDownloadModal` |
| Listening insights / charts | `pages/Insights.jsx` (large) | `MusicUniverseMap`, `NetworkGraph` |
| Playlist management | `pages/Playlists.jsx` | `PlaylistRow`, `TemplateCard`, `BlockEditor` |
| Template editing | inside `BlockEditor.jsx` | `BlockCard`, `BlockChainEditor` |
| Playlist import | `pages/PlaylistImport.jsx` + `PlaylistImportDetail.jsx` | platform badges (drifted) |
| Discovery queue | `pages/DiscoveryQueue.jsx` | inline `QueueCard` |
| Backups | `pages/PlaylistBackups.jsx` | inline `BackupSettingsPanel` |
| Connections setup | `pages/Connections.jsx` | `WebhookSetupPanel` |
| Admin / users | `pages/AdminUsers.jsx` | `DefaultPlaylistsPanel`, `IndexerSettingsPanel` |
| Automation tasks | inside `AdminUsers.jsx` | `AutomationPanel` |
| Browser extension setup | `pages/ImportSetup.jsx` | — |

### Polling

Jobs that change state without user input are polled. The canonical pattern is `useJobStatus.js`, which adapts (2s / 5s / 30s) based on activity. A few pages roll their own (`Playlists.jsx` rematch poll, `DiscoveryQueue.jsx`) — see AUDIT_FINDINGS F7.

---

## Conventions for contributors

### Backend

1. **Routers are thin.** They validate input, call a service, and shape the response. Business logic lives in `services/`.
2. **Services do not create DB sessions.** Session is passed in (from `Depends(get_db)` for routers, from job wrappers for scheduler).
3. **Schema changes are additive.** Add an `ALTER TABLE` in `_run_migrations()`. Never drop or rename a column without a deprecation cycle.
4. **Genre features must use canonical sources** — see the table above.
5. **Long-running jobs use `JobState`** so the watchdog can reset stale rows.

### Frontend

1. **Routes are protected via `RequireAuth` / `RequireAdmin`** in `App.jsx`. Don't add auth checks inside pages.
2. **Use `api.{get,post,put,patch,delete}` from `lib/api.js`** — it handles tokens and 401 retry. Don't use raw `fetch`.
3. **Tailwind first, inline styles second** — most of the codebase uses Tailwind classes. Inline styles in `PlaylistImportDetail.jsx` predate the convention and will be migrated.
4. **`useJellyfinUrl()` for any Jellyfin-side link** — it builds the right base URL.
5. **Bigger pages keep sub-components inline** for now (Dashboard, Insights). Extracting them is on the audit list.

### Testing

- All backend tests live in `backend/tests/`.
- Run all: `pytest tests/`. Run one file: `pytest tests/test_scoring.py`.
- The scoring + playlist engine + import code paths have the deepest coverage (`test_scoring_engine.py` 1,410 LOC, `test_playlist_engine.py` 999, `test_playlist_import.py` 1,123).
- New features should ship with tests — recurring build errors in this repo trace back to missing coverage.

### Commits

Follow the workflow noted in the project memory: update CHANGELOG, update README if user-visible, bump version, run tests, then commit. Releases trigger the Docker action.
