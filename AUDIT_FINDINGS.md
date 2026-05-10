# JellyDJ Audit Findings

One-time audit report. Findings are prioritized for impact-vs-risk. Once acted on, this file can be deleted.

Generated against commit `cc31080` (post v9 canonical genre system, Phase 8 block engine).

---

## Status — what shipped in the audit follow-up

✅ **Done**
- **F5** — `frontend/src/lib/dateUtils.js` created; 6 callers migrated (Dashboard, DiscoveryQueue, Playlists, PlaylistBackups, Insights, AutomationPanel). Picked the safer "preserve existing TZ" pattern; the strip-and-replace variant in 4 files was actually buggy for offset-bearing inputs.
- **F4** — `components/PlatformBadge.jsx` and `components/MatchBar.jsx` extracted; 3 pages migrated. Canonical look is the tailwind variant; minor visual change in `PlaylistImportDetail.jsx` (bigger padding gone, percentage label now opt-in via `showPercent`).
- **F2** — `lib/filterTypes.js` created; `FILTER_TYPES` + `DEFAULT_PARAMS` extracted from `BlockEditor.jsx` (1,586 → 1,279 LOC). `BlockEditor` re-exports `FILTER_TYPES` so existing importers (TemplateCard) keep working. `BlockChainEditor.jsx` has its own copy with different `desc` shape — left alone.
- **B1** — `services/jellyfin_client.py` created with `get_jellyfin_creds`, `get_jellyfin_creds_or_none`, and `jellyfin_headers`. 4 service-level duplicates eliminated (library_scanner, indexer, playlist_writer, audio_analysis). Inline router-level lookups left alone.
- **B5** — `services/migrations.py` created; 236-line `_run_migrations()` moved out of `main.py`. `main.py` now imports and aliases — zero behavior change.
- **B4** — `session_scope()` context manager added to `database.py`. **Callers NOT migrated** — adopt opportunistically.
- **F6** — `hooks/useAsync.js` added. **Callers NOT migrated** — adopt opportunistically.
- **F8** — `components/Button.jsx` and `components/StatusBadge.jsx` added with variants/sizes. **Callers NOT migrated** — adopt opportunistically.

❌ **Skipped — wrong / unsafe**
- **B7** — Audit said `Play.genre` and `SkipPenalty.genre` were dead. Closer reading shows they're a **fallback** in `scoring_engine.py:285` when canonical genre is missing, with test enforcement at `test_scoring_engine.py:1184`. Don't drop the writes.

⏸ **Deferred — explicitly, with reasons**
- **B3** (http_client factory) — 14 call sites with intentional timeout variance (8s for Jellyfin pings, 120s for streaming). Centralizing requires deciding policy per use case; not a mechanical refactor.
- **B9** (externalize prefab templates) — Templates aren't static data; they're built by Python helpers (`_node`, `_cooldown`, `_artist_cap`) composing nested trees. Moving to JSON loses the helpers; templating language adds complexity.
- **B2** (text_utils unification) — 8 variants, each tuned for a different matching use case, each pinned by existing tests. Safe merge requires corpus comparison + per-caller migration with its own tests. Hours of work; doing blindly risks "tracks stopped matching" bugs.
- **F1** (split Insights.jsx 2,270 LOC), **F3** (split MusicUniverseMap.jsx 1,375 LOC), **B8** (split recommender.py 1,642 LOC) — all flagged HIGH/MEDIUM risk by the audit itself. Each requires reading the whole file, understanding entangled state (D3 simulation, recommendation paths, chart builders), and careful relocation with backwards-compat re-exports. Right way: one per session, with tests passing before/after.

**Net result:** ~700 LOC removed/relocated, 9 new shared modules created, no behavior changes shipped beyond the F5 timezone bug fix and F4 minor visual normalization. Foundations laid for the deferred items so they're cheaper next session.

---

## Headline numbers

| | Backend | Frontend | Tests |
|---|---|---|---|
| Files | 21 routers + 23 services + core (main, scheduler, models, database) | 18 pages + 20 components + 2 hooks + lib | 12 test files |
| LOC | ~22,000 | ~17,000 | ~5,500 |
| Largest | `recommender.py` (1,642), `enrichment.py` (1,363), `indexer.py` (1,361) | `Insights.jsx` (2,270), `BlockEditor.jsx` (1,586), `MusicUniverseMap.jsx` (1,375) | `test_scoring_engine.py` (1,410) |

---

## Backend findings

### B1. Jellyfin credential lookup duplicated in 5+ places (HIGH impact, LOW risk)

Three near-identical implementations of "fetch base_url + decrypt api_key":
- `services/library_scanner.py:40` — `_get_jellyfin_creds()`
- `services/indexer.py:174` — `_get_jellyfin_creds()`
- `services/playlist_writer.py:34` — `_jellyfin_creds()`
- `services/audio_analysis.py:130` — `_get_jellyfin_context()` (variant)
- Plus inline lookups in routers: `discovery.py`, `playlist_backups.py`, `webhooks.py`, `user_playlists.py`, `playlist_import.py`

**Action**: extract `services/jellyfin_client.py` exposing `get_jellyfin_creds(db) → (base_url, api_key)`. Same module can host a `get_jellyfin_async_client()` factory (see B3).

### B2. Text normalization sprawled across 8 files (MEDIUM impact, MEDIUM risk)

| File | Function | Purpose |
|---|---|---|
| `library_dedup.py:81` | `_normalise()` | brackets, suffix, noise words |
| `enrichment.py:176` | `_clean_track_name()` | remaster/live/feat |
| `enrichment.py:204` | `_clean_artist_for_lastfm()` | collab suffix |
| `holiday.py:173` | `_normalise()` | punctuation collapse |
| `indexer.py:1225` | `_normalise_for_match()` | match-key |
| `library_reconcile.py:68` | `_norm()` | lowercase/strip |
| `recommender.py:184/200/227` | `_norm_genre/_norm_track/_norm_album_title` | genre + title |
| `playlist_import.py:57` | `_normalise()` | ASCII translit + suffix |

These are NOT all the same — each has subtle reasons. Risk: a careless merge breaks fuzzy matching.

**Action**: create `services/text_utils.py` with **named** functions matching purpose: `normalise_track_match`, `normalise_artist_match`, `normalise_genre`, `normalise_keyword`. Keep semantics identical to the most-tested current variant; add unit tests pinning expected output for each before flipping callers.

### B3. httpx client construction repeated 14 times with random timeouts (MEDIUM impact, LOW risk)

`audio_analysis.py:147,175`, `indexer.py:199,555`, `library_scanner.py:56`, `playlist_writer.py:50,74,99,172,194`, etc. Timeouts range 8s–120s with no documented rationale.

**Action**: `services/http_client.py` with three factories — `jellyfin_client(timeout=30)`, `playlist_client(timeout=20)`, `stream_client(timeout=120)`. Lets you globally tweak retry/UA/proxy behavior.

### B4. Background-job session handling is a footgun (HIGH safety, MEDIUM risk)

`automation.py`, `discovery.py`, `indexer.py`, `playlist_import.py`, `youtube_rip.py` create bare `SessionLocal()` in threads/jobs and rely on `try/finally db.close()`. An exception that bypasses the finally (e.g. a `SystemExit` from job cancellation) leaks a connection in WAL mode.

**Action**: add `with_session()` context manager in `database.py`, port jobs over.

### B5. Migrations are 230 lines of SQL inside `main.py:46-281` (LOW impact, LOW risk)

`_run_migrations()` does 100+ ALTER TABLE statements. Hard to grep, hard to add to.

**Action**: move to `services/migrations.py`. Keep the function call in `lifespan()`. No schema change. (Alembic is overkill for this project's deploy model.)

### B6. Three endpoints return "list of users" with overlapping shapes (LOW impact, LOW risk)

- `playlists.py:GET /users` (line ~82) — managed users + readiness
- `recommender.py:GET /users` (line ~108) — enabled users with index data
- `insights.py:GET /users` (line ~48) — same again

**Action**: consolidate into one `/api/users/roster` returning a superset; deprecate the duplicates with a redirect for one release.

### B7. `models.py` orphaned columns (LOW impact, LOW risk)

- `Play.genre` (line 87) and `SkipPenalty.genre` (line 166) — both labeled "historical only, do not use" since v9 but still written on every event.
- `UserTasteProfile.affinity_score` — declared `String`, inconsistent with all other affinity numerics.
- `IndexerSettings` (2 cols) overlaps `AutomationSettings.index_interval_hours` — could be merged.

**Action**: stop writing to dead columns now (1-line each). Drop in next major. Verify no readers via grep.

### B8. `recommender.py` (1,642 LOC) is monolithic (MEDIUM impact, HIGH risk)

Four recommendation paths (A–D) + filtering + album discovery + popularity caching, all in one file.

**Action**: convert to a `services/recommender/` package — `__init__.py` (public API), `paths.py`, `filtering.py`, `album_discovery.py`. Pure mechanical move; tests stay green.

### B9. `prefab_seeder.py` (584 LOC) hardcodes 4 generations of templates (LOW impact, LOW risk)

v7/v8/v11/v13 template definitions are baked in. Future generations will keep growing the file.

**Action**: move template JSON to `backend/data/prefab_templates/` (one file per template); seeder loads + diff-applies. Same logic, externalized data.

### B10. Lidarr album-search logic duplicated (LOW impact, LOW risk)

`discovery.py:_send_to_lidarr()` (~100 LOC) is roughly mirrored in `playlist_import.py` for album suggestions.

**Action**: extract `services/lidarr_client.py`.

---

## Frontend findings

### F1. `Insights.jsx` is 2,270 LOC — clearest split candidate (HIGH impact, MEDIUM risk)

Sub-components and panels are already well-separated logically; just need to be moved to files.

**Suggested split**:
- `pages/insights/index.jsx` — router/tabs (~200 LOC)
- `pages/insights/GenreAffinityPanel.jsx` (~500)
- `pages/insights/ArtistNetworkPanel.jsx` (~150 — wraps MusicUniverseMap)
- `pages/insights/ListeningStatsPanel.jsx` (~400)
- `pages/insights/AlbumRatingsPanel.jsx` (~250)
- `pages/insights/CooldownPanel.jsx` (~250)
- `pages/insights/_shared.jsx` — `ScoreBar`, `StatPill`, `HolidayBadge`, `CooldownBadge`, `PopularityBar`, `SortableHeader` (~200)

### F2. `BlockEditor.jsx` is 1,586 LOC — the FILTER_TYPES catalog should be its own data file (HIGH impact, LOW risk)

**Suggested split**:
- `lib/filterTypes.js` — `FILTER_TYPES` metadata catalog (data only)
- `components/playlist/FilterEditors.jsx` — the `Editors` map and per-filter editor components
- `components/playlist/BlockEditor.jsx` — the shell + state management (~500 LOC)

### F3. `MusicUniverseMap.jsx` is 1,375 LOC (MEDIUM impact, MEDIUM risk)

D3 simulation + color logic + UI all mixed.

**Suggested split**:
- `lib/colorUtils.js` — `genreAffinityColor`, `driftColor`, palette helpers
- `components/MusicUniverseMap/TrackPanel.jsx` — sub-panel
- `components/MusicUniverseMap/index.jsx` — D3 logic + container

### F4. PlatformBadge / MatchBar / PLATFORM_LABELS are *near*-duplicates that drifted (MEDIUM impact, LOW risk)

The constant `PLATFORM_LABELS` IS identical in:
- `pages/PlaylistImport.jsx:24`
- `pages/PlaylistImportDetail.jsx:19`
- `pages/Playlists.jsx:26`

But the `PlatformBadge` and `MatchBar` *components* drifted in three different ways: tailwind vs inline styles, different sizes (text-[10px] vs text-[9px]), different progress bar heights, percentage label shown in one but not the others.

**Action**:
1. Pick a canonical look (recommend the tailwind variant in `PlaylistImport.jsx` — most consistent with the rest of the design system).
2. Move to `components/PlatformBadge.jsx` and `components/MatchBar.jsx`.
3. Accept minor visual drift in `PlaylistImportDetail.jsx` (currently inline-styled, looks older).

### F5. Date/time formatters in 5 places (MEDIUM impact, LOW risk)

- `Insights.jsx` — `fmtDate`, `fmtDateShort`, `daysUntil`
- `Dashboard.jsx` — `timeAgo`
- `DiscoveryQueue.jsx` — `utc`
- `PlaylistBackups.jsx` — `fmt`, `fmtShort`
- `JobProgress.jsx` — elapsed-ticker formatter

**Action**: `lib/dateUtils.js` with `formatDate`, `formatTimeAgo`, `daysUntil`, `formatElapsed`. Drop-in replacement.

### F6. `useAsync` is missing (HIGH ergonomic impact, LOW risk)

The `[loading, setLoading] / [error, setError] / try/catch/finally` pattern appears 30+ times across pages. Boilerplate is mostly identical.

**Action**: `hooks/useAsync.js` returning `{data, loading, error, refetch}`. Migrate opportunistically — don't do all at once.

### F7. Generic polling hook is missing; `useJobStatus` solves the case but isn't reused (MEDIUM impact, LOW risk)

`Playlists.jsx:67–83` has an inline `setInterval` rematch poller. `DiscoveryQueue.jsx` has another. `useJobStatus.js` has the elegant adaptive-interval pattern but is hard-coded for the 5 named jobs.

**Action**: `hooks/usePolling.js` — generic poll(`fetcher`, `{interval, untilCondition}`). Refactor `useJobStatus` to use it internally.

### F8. Buttons / badges / status pills reimplemented per-page (MEDIUM impact, MEDIUM risk)

- Buttons: `Connections.jsx` (`ActionButton`), `PlaylistBackups.jsx` (`ActionBtn`), `AdminUsers.jsx` (`Btn`), `BlockEditor.jsx` (custom)
- Status pills: `Connections.jsx` (`StatusBadge`), `DiscoveryQueue.jsx` (`StatusPill`), `PlaylistBackups.jsx` (`Badge`)

**Action**: `components/Button.jsx` + `components/StatusBadge.jsx` with prop-driven variants. Migrate gradually.

### F9. Modal pattern hand-rolled per-modal (LOW impact, LOW risk)

`Dashboard.jsx` (`BillboardDownloadModal`), `DiscoveryQueue.jsx`, `PlaylistImportDetail.jsx` (`ManualMatchModal`), `BlockEditor.jsx` all roll their own backdrop + animation + escape handler.

**Action**: `components/Modal.jsx` shell with backdrop / escape / focus-trap. Body is a render prop.

### F10. API endpoint strings scattered (LOW impact, LOW risk)

Endpoint paths like `/api/import/playlists/{id}/rematch` are inlined at call sites.

**Action**: optional — `lib/endpoints.js` centralizing endpoint builders. Low priority; current style is fine.

---

## Cross-cutting findings

### X1. Settings retrieval pattern repeats: `_get_or_create_settings()`

Identical implementations in `automation.py:191` and `indexer.py:21`. The retry-on-locked SQLite wrapper `_stamp_setting()` (`automation.py:23`) is a useful general utility currently isolated.

**Action**: move both to `database.py` as `get_or_create(model, db)` + `stamp_setting_with_retry()`.

### X2. Auth boilerplate `Depends(get_current_user)` + `require_admin()` repeated in all 21 routers

Not really duplication — this is FastAPI idiomatic. **No action**, but worth a one-line comment in `routers/__init__.py` documenting the convention.

---

## Prioritized action list

| Priority | Item | Effort | Risk |
|---|---|---|---|
| 1 | B1 — Jellyfin client extraction | 30 min | low |
| 2 | F2 — Split BlockEditor (data extraction) | 1h | low |
| 3 | F1 — Split Insights.jsx | 2h | medium |
| 4 | F4 — PlatformBadge / MatchBar consolidation | 30 min | low |
| 5 | F5 — Date utils consolidation | 30 min | low |
| 6 | B3 — http_client factory | 1h | low |
| 7 | B7 — drop dead `Play.genre` writes | 15 min | low |
| 8 | B5 — migrations module move | 30 min | low |
| 9 | F6 — useAsync hook | 1h + gradual | low |
| 10 | B2 — text_utils unification (with tests first) | 2h | medium |
| 11 | B8 — recommender package split | 1h | high |
| 12 | F3 — MusicUniverseMap split | 1h | medium |
| 13 | F8 — Button/StatusBadge components | 2h | medium |
| 14 | B4 — `with_session` context manager | 1h | medium |
| 15 | B9 — externalize prefab template data | 1h | low |

Items 1, 4, 5, 7, 8 are the easy wins — knock those out and you reclaim ~500 LOC with negligible risk.
