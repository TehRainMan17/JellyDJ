# JellyDJ

A self-hosted music recommendation engine for [Jellyfin](https://jellyfin.org).

JellyDJ analyses your Jellyfin play history, builds personalised taste profiles per user, generates smart playlists directly in Jellyfin, and surfaces new album recommendations that can be automatically sent to [Lidarr](https://lidarr.audio) for download.

---

## What it does

- **Smart playlists** — "For You", "Discover Weekly", "Most Played", and "Recently Played" playlists regenerated on a schedule and written directly into Jellyfin
- **Skip-aware scoring** — listens to Jellyfin playback webhooks; songs you skip stop appearing, songs you favourite get boosted to the top
- **Discovery queue** — finds new artists and missing albums based on your listening habits; you review them before anything is downloaded
- **Auto-download** — optionally sends approved discovery items to Lidarr automatically on a configurable schedule
- **Insights** — per-user listening statistics, top artists, genre breakdowns, and skip rate analysis

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker + Docker Compose (Linux)
- A running [Jellyfin](https://jellyfin.org) server with an API key
- *(Optional)* A running [Lidarr](https://lidarr.audio) instance for download management

---

## Getting started

### 1. Get the files

```bash
git clone https://github.com/YOUR_USERNAME/jellydj.git
cd jellydj
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and set `SECRET_KEY` to a random string:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Everything else (Jellyfin URL, API keys) can be configured through the web UI after first launch.

### 3. Start JellyDJ

```bash
docker compose up -d
```

First run takes 2–5 minutes while Docker builds the images.

### 4. Open the UI

Navigate to **http://localhost:7879** and follow the setup steps in the Connections page.

---

## Configuration

### Connecting to Jellyfin

1. Open the **Connections** page in JellyDJ
2. Enter your Jellyfin URL (e.g. `http://192.168.1.100:8096`) and an API key
3. Click **Test** — JellyDJ needs read access to your library and write access to create playlists

### Setting up webhooks (enables skip detection)

JellyDJ improves its recommendations when it can see individual playback events:

1. Install the **Webhook** plugin in Jellyfin (Dashboard → Plugins → Catalogue)
2. Create a new webhook pointing to `http://jellydj-backend:8000/api/webhooks/jellyfin`
3. Enable: **PlaybackStart**, **PlaybackProgress**, and **PlaybackStop**
4. The setup panel in JellyDJ's Settings page walks through this step by step

### External APIs (optional, improves discovery)

- **Last.fm** — artist similarity data and listener counts (free API key at last.fm/api)
- **Spotify** — popularity scores (free Client Credentials app at developer.spotify.com)
- **MusicBrainz** — no key required, used automatically

Without external APIs, discovery quality is reduced but playlists still work.

---

## Updating

```bash
docker compose pull
docker compose up -d --build
```

Your data is stored in the `jellydj-config` Docker volume and persists across updates.

---

## Troubleshooting

**Page won't load after `docker compose up`**
Wait 30 seconds — the frontend waits for the backend health check to pass. Check status with `docker compose ps`.

**Port 7879 is already in use**
Change `JELLYDJ_PORT=7879` to another port in your `.env`, then restart.

**Playlists aren't appearing in Jellyfin**
Make sure your Jellyfin API key has write access. Check the Connections page — the connection test only verifies read access.

**Discovery queue is empty after "Refresh Recs"**
Run "Index Now" first to build your taste profile, then try refreshing. External API keys (Last.fm/Spotify) significantly improve discovery results.

**View logs**
```bash
docker compose logs -f
```

**Full reset** *(deletes all data)*
```bash
docker compose down -v
```

---

## Architecture

See [CONTRIBUTING.md](CONTRIBUTING.md) for a full walkthrough of the codebase.

```
jellydj/
├── backend/     FastAPI + SQLite — scoring engine, scheduler, Jellyfin/Lidarr API clients
├── frontend/    React + Tailwind — served via Nginx
├── .env.example
└── docker-compose.yml
```

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

---

## License

MIT
