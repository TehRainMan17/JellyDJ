# Changelog

All notable changes to JellyDJ are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

_Nothing pending._

---

## [1.2.0] — 2026-03-23

### Added
- **Artist timeout system** — skip 5+ distinct tracks by the same artist within a 2-day rolling window to trigger a playlist exclusion. Escalates: 7 days → 14 days → 30 days on repeat offences. Active timeouts visible on the Artists insights page with expiry date and cycle count.
- **Artists insights page shows all library artists** — previously only showed artists with completed play history (ArtistProfile rows). Now uses LibraryTrack as the base so every artist in the library appears. Untracked artists render at the bottom with a "not tracked" badge; their profile builds as you listen.
- **Playlist Import** — import any Spotify, Tidal, or YouTube Music playlist by URL. Matches tracks to your local library, creates the playlist in Jellyfin, and flags missing tracks for Lidarr.
- **Playlist Backups** — rolling revision history for every JellyDJ-managed playlist. Browse, inspect, and restore any prior snapshot from the PL Backups page.
- **Browser Extension** — JellyDJ Chrome extension clips playlist URLs from Spotify or YouTube Music and sends them directly to your self-hosted instance for import. Configured with server URL + personal API key.
- **ArtistCooldown model** — new `artist_cooldowns` DB table (auto-created on startup, no migration needed).
- **`check_and_apply_artist_cooldown()`** in `enrichment.py` — called on every skip webhook event; handles escalation logic.
- **`get_artist_cooled_down_ids()`** in `playlist_utils.py` — returns frozenset of item IDs belonging to timed-out artists, merged into playlist exclusion set.
- **Skip-only artist stubs** — artists with SkipPenalty data but no ArtistProfile now appear immediately in insights search results without waiting for a re-index.
- README screenshots and feature detail sections for Playlist Import, Playlist Backups, and Browser Extension.

### Fixed
- **Artist skip penalty double-application** — skip rate was being multiplied in twice, halving its actual effect. Now applies in a single full-strength pass.
- **Unplayed tracks ignoring artist skip signal** — tracks by artists the user has only ever skipped (never completed) now inherit the artist skip rate during scoring, suppressing them in playlists before a re-index runs.
- **Artist profile deduplication** — `rebuild_artist_profiles()` now groups by `artist_name.lower()`, collapsing capitalisation variants (e.g. `Cage the Elephant` / `cage the elephant`) into one canonical profile.
- **Collaboration string detection in `_resolve_track_artist()`** — `AlbumArtist` values like `Lady Gaga & Bradley Cooper` are now detected as collab strings and resolved to the primary artist (`Lady Gaga`), preventing duplicate artist entries per collab track.
- **Breadth bonus cap** — `_breadth_bonus()` was not capping at `ARTIST_BREADTH_BONUS_MAX` for artists with more than 50 tracks played; now uses `min()` to enforce the ceiling.
- **Case-insensitive affinity lookup** in `rebuild_track_scores()` — artist affinity map now includes lowercase aliases so capitalisation mismatches between LibraryTrack and ArtistProfile don't silently zero out the affinity contribution.

### Changed
- Artists insights header now reads "X artists in library" instead of "X artists tracked".
- `_skip_only` flag renamed to `_no_profile` in artist row response — covers both skip-only and genuinely unplayed artists.
- Artist cooldown check now runs inline in the webhook handler after every skip event (with silent failure so it never breaks playback event recording).

---

## [1.1.0] — 2026-03-22

### Added
- **Playlist Backups** — initial implementation with rolling revision history.
- **API Key Management** for JellyDJ Browser Extension — generate and revoke personal API keys from Settings.

### Fixed
- Added Jellyfin artist ID to database so artist links open the correct Jellyfin artist page. Requires a re-index after update.

---

## [1.0.0] — 2026-03-21

### Added
- Initial public release.
- Per-user taste profiles (affinity scores from play counts, recency, skips, favorites, replay signals).
- Smart playlists auto-generated in Jellyfin (*For You*, *New For You*, *Most Played*, *Recently Played*).
- Block-based playlist editor with AND/OR filter chaining.
- Discovery Queue with album recommendations ranked by affinity + novelty.
- Lidarr integration for auto-download of approved discoveries.
- Billboard Hot 100 chart data cross-referenced with library.
- Jellyfin webhook scoring — playback events update taste profiles in real time.
- Insights dashboard with score breakdowns, genre affinities, top artists, skip analysis.
- Music Universe Map — interactive force-directed graph of taste (genre → artist → track drill-down).
- Multi-source enrichment (Spotify, Last.fm, MusicBrainz, Billboard).
- Direct links to songs, artists, and playlists in Jellyfin (requires public domain name for external access).
- Full multi-user support with per-user profiles and admin controls.
