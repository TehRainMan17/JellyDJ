# backend/routers/

FastAPI routers. One file per feature domain. Routers should be **thin** — validate input, call a service, shape the response. Business logic lives in `../services/`.

All routers are mounted in `main.py:671–691` with the prefixes shown below.

## Routers

| File | LOC | Prefix | Domain |
|---|---|---|---|
| `auth.py` | 650 | `/api/auth` | Jellyfin login, JWT/refresh tokens, setup mode. |
| `connections.py` | 540 | `/api/connections` | Jellyfin + Lidarr credentials (60s in-process cache). |
| `external_apis.py` | 240 | `/api/external-apis` | Spotify / Last.fm / MusicBrainz keys + tests. |
| `automation.py` | 1,120 | `/api/automation` | Scheduler settings, manual job triggers, activity feed. |
| `indexer.py` | 615 | `/api/indexer` | Play-history sync, library scan, taste profile, billboard sync. |
| `webhooks.py` | 970 | `/api/webhooks` | Jellyfin playback event sink + skip tracking. |
| `recommender.py` | 166 | `/api/recommender` | Taste profile inspection + library/album recommendations. |
| `discovery.py` | 1,030 | `/api/discovery` | Album recommendation queue + Lidarr push. |
| `insights.py` | 1,200+ | `/api/insights` | Listening stats, charts, cooldowns, replay signals. |
| `graph.py` | 340 | `/api/graph` | Artist/genre network for the visualization. |
| `playlists.py` | 105 | `/api/playlists` | Read-only playlist run history. |
| `playlist_templates.py` | 650 | `/api/playlist-templates` | Template + block CRUD. |
| `user_playlists.py` | 450 | `/api/user-playlists` | User-created playlists + scheduled push. |
| `admin_defaults.py` | 310 | `/api/admin/default-playlists` | Default playlists for new users. |
| `playlist_backups.py` | 1,020 | `/api/playlist-backups` | Backup + revision restore. |
| `playlist_import.py` | 1,300+ | `/api/import` | External playlist import + Lidarr album matching. |
| `youtube_rip.py` | 160 | `/api/import/youtube-rip` | yt-dlp ripping. |
| `audio_analysis.py` | 90 | `/api/audio-analysis` | Audio analysis stats + reindex. |
| `exclusions.py` | 170 | `/api/exclusions` | Manual album exclusions. |
| `mobile.py` | 1,140+ | `/api/mobile` | Android/iOS companion app endpoints (streaming, library browse). |
| `debug_aa.py` | 150 | `/api/debug` | Android Auto telemetry sink. |

## Conventions

- All authenticated routes use `Depends(get_current_user)`. Admin-only routes also call `require_admin()`.
- Use `Depends(get_db)` for the DB session. Don't create sessions inside the route function.
- Error responses use `HTTPException(status_code=, detail=)` — don't return JSON error envelopes manually.
- For long-running work, push to a scheduler job and return immediately. Don't block the request thread.

## Known overlap

Three routers have a `GET /users` endpoint with overlapping shapes (`playlists.py`, `recommender.py`, `insights.py`). Consolidation is on the audit list (`AUDIT_FINDINGS.md` B6).
