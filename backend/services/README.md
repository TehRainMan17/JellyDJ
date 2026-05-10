# backend/services/

Business logic. Routers call into here. **Services never create DB sessions** — the session is passed in.

## Files

| File | LOC | Purpose |
|---|---|---|
| `audio_analysis.py` | 351 | Librosa-based audio feature extraction (BPM, key, energy, acousticness). Public: `analyze_new_tracks()`. |
| `catalog_builder.py` | 163 | Pre-computes album→track mappings + version hash for the mobile-app cache. Called by `library_scanner`. |
| `enrichment.py` | 1,363 | Last.fm + MusicBrainz fetcher (artist/track playcount, tags, similar artists, replay/cooldown signals). |
| `events.py` | 28 | One-line event logger for the Dashboard activity feed. |
| `external_playlist_fetcher.py` | 374 | Spotify embed scraper + Tidal/YouTube via yt-dlp. Returns normalized track lists. |
| `genre_adjacency.py` | 247 | Hardcoded bidirectional genre graph + `norm_genre()`. Used by playlist_blocks, recommender, scoring_engine. |
| `holiday.py` | 290 | Seasonal track tagger + in-season check. Called by `library_scanner`. |
| `indexer.py` | 1,361 | Per-user play history + taste profile builder. Flushes `playback_events` and calls `scoring_engine`. |
| `library_dedup.py` | 341 | Fuzzy matching for imported playlists / recommendations. Public: `artist_in_library`, `album_in_library`, `tracks_in_library_for_album`. |
| `library_reconcile.py` | 268 | Post-migration ID remap when Jellyfin server is rebuilt. |
| `library_scanner.py` | 365 | Scans full Jellyfin library → `LibraryTrack`. Triggers holiday tagging + catalog rebuild. |
| `playlist_blocks.py` | 1,033 | Individual block executors (score range, genre, artist, etc.). Exports `BLOCK_REGISTRY`. |
| `playlist_engine.py` | 712 | Filter-tree evaluator (AND/OR semantics). Public: `generate_from_template`, `preview_template`. |
| `playlist_import.py` | 974 | 3-pass fuzzy match (exact → fuzzy track → fuzzy artist) + album-suggestion logic. |
| `playlist_utils.py` | 154 | Shared filters: excluded items, cooled-down artists, holiday exclusions. |
| `playlist_writer.py` | 169 | Async httpx wrappers around Jellyfin playlist API. |
| `prefab_seeder.py` | 584 | Seeds + migrates the four hardcoded system templates (v7/v8/v11/v13). |
| `recommender.py` | 1,642 | Main recommendation engine. Four paths (A–D) for library + new-album recommendations. |
| `scoring_engine.py` | 1,297 | Per-user `TrackScore` computation (affinity + popularity + recency + novelty − cooldowns). |
| `popularity/` | — | Pluggable popularity adapters. |

## popularity/

Cached, pluggable popularity sources. 24h SQLite cache.

| File | Purpose |
|---|---|
| `__init__.py` | Module singleton: `get_aggregator(db)`. |
| `base.py` | Abstract adapter interface + `ArtistInfo` / `AlbumPopularity` / `TrendingTrack` dataclasses. |
| `aggregator.py` | Unified orchestrator. Merges results from all adapters. |
| `spotify_adapter.py` | Spotify popularity scores (0–100). |
| `lastfm_adapter.py` | Last.fm listener counts + tags. |
| `musicbrainz_adapter.py` | MBIDs + fallback metadata. |
| `billboard_adapter.py` | Hot 100 scraper. |

## Conventions

- DB session is always a parameter, never created here. Services that *do* call `SessionLocal()` are background-job entry points and that pattern is on the audit list (see `AUDIT_FINDINGS.md` B4).
- Each service file owns a domain. Cross-cutting helpers (text normalization, Jellyfin creds, HTTP clients) currently live duplicated across several services — see `AUDIT_FINDINGS.md` B1, B2, B3.
- New external services go in `popularity/` if they fit the adapter shape; otherwise create a new top-level service file.
