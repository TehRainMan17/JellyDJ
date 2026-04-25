# JellyDJ Mobile Companion (Android)

Android app scaffold with real JellyDJ/Jellyfin integration and Android Auto media browsing.

## Implemented in this pass

- Real sign-in through existing JellyDJ auth (`/api/auth/login` + JWT)
- Real library data from backend mobile API (`/api/mobile/*`)
- Real streaming URLs to Jellyfin per authenticated user session
- Search tracks/artists and play playlists
- Android Auto-compatible `MediaLibraryService` browse tree:
  - Recently Played
  - Playlists
- Playback resume persistence to support in-car continuation
- JellyDJ color direction carried into Material theme

## Backend changes required (already added in repo)

- New router: `backend/routers/mobile.py`
- Included in app: `backend/main.py`

## Local run notes

- Default mobile API base URL is emulator-friendly: `http://10.0.2.2:8000/`
- If testing on a physical phone, change `MOBILE_API_BASE_URL` in:
  - `app/build.gradle.kts`

## Current scope

This is now runnable for login, browse, search, playlist playback, and Android Auto library browse.
Social/gamification and vibe-generated playlists are left as next milestones.
