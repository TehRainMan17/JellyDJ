# Changelog

All notable changes to JellyDJ are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

### Change tiers
Each entry is prefixed with a tier marker used to generate release notes:
- `!!!` **Major** — headline feature or breaking change; gets a full callout in release notes
- `~~` **Minor** — meaningful improvement or non-trivial fix; one-liner in release notes
- *(no prefix)* **Patch** — small fix, housekeeping, copy/label change; grouped or omitted in release notes

---

## [Unreleased]

_Nothing pending._

---

## [1.3.0] — 2026-03-26

### Added
- `!!` **Startup security checks** — backend validates JWT signing key and Fernet encryption key on boot. Logs a clear error and exits if either is missing or malformed, preventing silent misconfiguration.
- `~~` **Backdoor detection in setup endpoint** — `GET /setup-status` now returns `backdoor_active: true` when `SETUP_ALLOW_AFTER_CONFIGURE=true` and Jellyfin is already configured, making persistent admin backdoors visible to health checks and monitoring.
- `~~` **Webhook secret warning** — startup logs a warning when `WEBHOOK_SECRET_REQUIRED=false`, surfacing unauthenticated webhook exposure before it becomes a problem.
- `~~` **Stale job watchdog** — hourly scheduler job that resets any job stuck at `running=True` for longer than 4 hours, preventing permanent job deadlock in the daemon-thread fire-and-forget pattern.
- Comprehensive tests for startup security checks (`test_security.py`) and stale job watchdog (`test_stale_watchdog.py`).

### Changed
- Uvicorn reduced from 4 workers to 1 — CPU compute is not the bottleneck; multiple workers introduced race conditions on shared in-process state. Single-worker removes the hazard with no throughput loss.

---

## [1.2.0] — 2026-03-23

### Added
- `!!!` **Artist timeout system** — skip 5+ distinct tracks by the same artist within a 2-day rolling window to trigger a playlist exclusion. Escalates: 7 days → 14 days → 30 days on repeat offences. Active timeouts visible on the Artists insights page with expiry date and cycle count.
- `!!!` **Artists insights page shows all library artists** — previously only showed artists with completed play history. Now uses LibraryTrack as the base so every artist in the library appears. Untracked artists render at the bottom with a "not tracked" badge; profile builds as you listen.
- `!!!` **Playlist Import** — import any Spotify, Tidal, or YouTube Music playlist by URL. Matches tracks to your local library, creates the playlist in Jellyfin, and flags missing tracks for Lidarr.
- `!!!` **Playlist Backups** — rolling revision history for every JellyDJ-managed playlist. Browse, inspect, and restore any prior snapshot from the PL Backups page.
- `!!!` **Browser Extension** — JellyDJ Chrome extension clips playlist URLs from Spotify or YouTube Music and sends them directly to your self-hosted instance for import.
- `~~` **Skip-only artist stubs** — artists with skip data but no play history now appear immediately in insights search without waiting for a re-index.
- New `artist_cooldowns` DB table (auto-created on startup, no migration needed).
- README screenshots and feature detail sections for Playlist Import, Playlist Backups, and Browser Extension.

### Fixed
- `!!!` **Artist skip penalty double-application** — skip rate was being multiplied in twice, halving its actual effect. Now applies in a single full-strength pass.
- `~~` **Unplayed tracks ignoring artist skip signal** — tracks by artists the user has only ever skipped now inherit the artist skip rate during scoring, suppressing them in playlists before a re-index runs.
- `~~` **Artist profile deduplication** — `rebuild_artist_profiles()` now collapses capitalisation variants (e.g. `Cage the Elephant` / `cage the elephant`) into one canonical profile.
- `~~` **Collaboration string detection** — `AlbumArtist` values like `Lady Gaga & Bradley Cooper` now resolve to the primary artist (`Lady Gaga`), preventing duplicate artist entries per collab track.
- `~~` **Case-insensitive affinity lookup** in `rebuild_track_scores()` — capitalisation mismatches between LibraryTrack and ArtistProfile no longer silently zero out affinity.
- Breadth bonus cap — `_breadth_bonus()` now correctly enforces `ARTIST_BREADTH_BONUS_MAX` for artists with 50+ tracks played.

### Changed
- Artists insights header now reads "X artists in library" instead of "X artists tracked".
- `_skip_only` flag renamed to `_no_profile` in artist row response.
- Artist cooldown check runs inline in the webhook handler after every skip event.

---

## [1.1.0] — 2026-03-22

### Added
- `!!!` **Playlist Backups** — initial implementation with rolling revision history.
- `~~` **API Key Management** for JellyDJ Browser Extension — generate and revoke personal API keys from Settings.

### Fixed
- `~~` Added Jellyfin artist ID to database so artist links open the correct Jellyfin artist page. Requires a re-index after update.

---

## [1.0.0] — 2026-03-21

### Added
- `!!!` Initial public release.
- `!!!` Per-user taste profiles (affinity scores from play counts, recency, skips, favorites, replay signals).
- `!!!` Smart playlists auto-generated in Jellyfin (*For You*, *New For You*, *Most Played*, *Recently Played*).
- `!!!` Block-based playlist editor with AND/OR filter chaining.
- `!!!` Discovery Queue with album recommendations ranked by affinity + novelty.
- `!!!` Lidarr integration for auto-download of approved discoveries.
- `~~` Billboard Hot 100 chart data cross-referenced with library.
- `~~` Jellyfin webhook scoring — playback events update taste profiles in real time.
- `~~` Insights dashboard with score breakdowns, genre affinities, top artists, skip analysis.
- `~~` Music Universe Map — interactive force-directed graph of taste (genre → artist → track drill-down).
- `~~` Multi-source enrichment (Spotify, Last.fm, MusicBrainz, Billboard).
- `~~` Direct links to songs, artists, and playlists in Jellyfin.
- Full multi-user support with per-user profiles and admin controls.
