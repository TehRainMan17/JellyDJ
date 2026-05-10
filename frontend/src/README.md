# frontend/src/

React + Vite + Tailwind. JSX. No TypeScript.

## Layout

```
src/
├── App.jsx                # Router with RequireAuth / RequireAdmin guards
├── main.jsx               # Entry, wraps AuthProvider
├── index.css              # Tailwind tokens + .nav-item / .card / .btn / .tab classes
├── contexts/AuthContext.jsx
├── lib/api.js             # Fetch wrapper with auto-401-retry
├── hooks/                 # useJellyfinUrl, useJobStatus
├── pages/                 # Top-level routes
└── components/            # Shared UI + components/playlist/ subdir
```

## Pages

| File | LOC | Route | Purpose |
|---|---|---|---|
| `Login.jsx` | 257 | `/login` | Setup-mode toggle + regular login. |
| `Dashboard.jsx` | 773 | `/` | Sync status, activity feed, trending, billboard download modal. |
| `Insights.jsx` | 2,270 | `/insights` | Charts, listening stats, album ratings, cooldowns. **Largest file — split candidate.** |
| `Playlists.jsx` | 810 | `/playlists` | My Playlists / Template Gallery / Run History tabs. |
| `Connections.jsx` | 966 | `/connections` | Jellyfin + Lidarr creds, tracked users, webhook setup. |
| `DiscoveryQueue.jsx` | 552 | `/discovery` | Album recommendation cards. |
| `PlaylistImport.jsx` | 673 | `/import` | URL paste + YouTube rip + grid of imported playlists. |
| `PlaylistImportDetail.jsx` | 841 | `/import/:id` | All / Matched / Missing / Album Suggestions tabs. |
| `PlaylistBackups.jsx` | 956 | `/backups` | Backup list with revisions. |
| `AlbumExclusions.jsx` | 423 | `/exclusions` | Manual album exclusion table. |
| `AdminUsers.jsx` | 614 | `/admin/users` | Default playlists + managed users. Admin-only. |
| `ImportSetup.jsx` | 314 | `/import/setup` | Browser extension instructions. |
| `Settings.jsx` | 42 | `/settings` | Settings shell. |

## Components

### Top level

| File | LOC | Purpose |
|---|---|---|
| `Layout.jsx` | 239 | Sidebar + topbar + breadcrumb. |
| `JobProgress.jsx` | 417 | All 5 job progress bars. Per-job config in `JOB_ROWS`. |
| `MusicUniverseMap.jsx` | 1,375 | D3 zoomable hierarchical music map (split candidate). |
| `NetworkGraph.jsx` | 459 | D3 force-directed artist/genre network. |
| `AutomationPanel.jsx` | 597 | Per-job task cards (toggle / interval / trigger). |
| `DefaultPlaylistsPanel.jsx` | 592 | Choose which playlists auto-assign to new users. |
| `IndexerSettingsPanel.jsx` | 122 | Indexer schedule + intervals. |
| `WebhookSetupPanel.jsx` | 110 | Jellyfin webhook test/copy. |
| `JellyfinIcon.jsx`, `PlatformIcon.jsx`, `UserAvatar.jsx` | small | Icon + avatar primitives. |

### components/playlist/

| File | LOC | Purpose |
|---|---|---|
| `BlockEditor.jsx` | 1,586 | Full template editor — `FILTER_TYPES` catalog + `Editors` map. **Split candidate.** |
| `BlockCard.jsx` | 972 | Single filter block card. Visual variant per filter type. |
| `BlockChainEditor.jsx` | 775 | Block tree editor (AND/OR chains, drag-drop reorder). |
| `PlaylistRow.jsx` | 514 | Single playlist list row with run count + actions. |
| `TemplateCard.jsx` | 257 | Gallery card for template preview. |

## Hooks

| File | Purpose |
|---|---|
| `useJellyfinUrl.js` | Module-level cached Jellyfin base URL. Use this for any Jellyfin-side link. |
| `useJobStatus.js` | Adaptive 2s/5s/30s polling for the 5 named jobs. |

## Conventions

1. **Routes are protected via guards in `App.jsx`.** Don't add auth checks inside pages.
2. **All API calls go through `lib/api.js`** — `api.get`, `api.post`, etc. Don't use raw `fetch`. The wrapper handles tokens and 401 retry.
3. **Tailwind first.** Inline styles in `PlaylistImportDetail.jsx` predate the convention.
4. **`useJellyfinUrl()` for any Jellyfin link.** Don't hand-build URLs.
5. **Inline sub-components are tolerated for now** in pages > 700 LOC. See `AUDIT_FINDINGS.md` for split candidates.

## Known issues

See `AUDIT_FINDINGS.md` for:
- F1 — `Insights.jsx` split
- F2 — `BlockEditor.jsx` split (start with `FILTER_TYPES` extraction)
- F4 — `PlatformBadge` / `MatchBar` drifted across 3 pages, ripe for consolidation
- F5 — date formatters in 5 places
- F6 — missing `useAsync` hook
