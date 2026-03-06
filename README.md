<p align="center">
  <img src=".github/images/banner.png" alt="JellyDJ" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/TehRainMan17/JellyDJ/stargazers"><img src="https://img.shields.io/github/stars/TehRainMan17/JellyDJ?style=for-the-badge&logo=github&color=5be6f5&labelColor=090b22&logoColor=5be6f5" alt="Stars" /></a>
  <a href="https://github.com/TehRainMan17/JellyDJ/network/members"><img src="https://img.shields.io/github/forks/TehRainMan17/JellyDJ?style=for-the-badge&logo=github&color=a28ffb&labelColor=090b22&logoColor=a28ffb" alt="Forks" /></a>
  <a href="https://github.com/TehRainMan17/JellyDJ/issues"><img src="https://img.shields.io/github/issues/TehRainMan17/JellyDJ?style=for-the-badge&logo=github&color=f87171&labelColor=090b22&logoColor=f87171" alt="Issues" /></a>
  <a href="https://github.com/TehRainMan17/JellyDJ/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-a28ffb?style=for-the-badge&labelColor=090b22" alt="License" /></a>
  <a href="https://hub.docker.com/r/562ray/jellydj-backend"><img src="https://img.shields.io/docker/pulls/562ray/jellydj-backend?style=for-the-badge&logo=docker&color=5be6f5&labelColor=090b22&logoColor=5be6f5" alt="Docker Pulls" /></a>
</p>

<p align="center">
  <strong>A self-hosted music recommendation engine that turns your static Jellyfin library into a living, breathing music ecosystem.</strong><br/>
  Taste profiles &nbsp;·&nbsp; Smart playlists &nbsp;·&nbsp; Album discovery &nbsp;·&nbsp; Lidarr integration
</p>

<br/>

---

## 🪼 Why I Built This

My family moved off Spotify to take back control of our music. No subscriptions, no algorithms selling our data, no content disappearing overnight — just our library, our way.

But my girls missed something real: **the magic of discovery**. That moment when a service just *knows* you well enough to surface an artist you've never heard but somehow immediately love. Spotify and YouTube Music are genuinely great at this, and plain Jellyfin has no answer for it.

So I built JellyDJ to fill that void.

It watches what everyone in the house listens to, builds taste profiles per person, and quietly surfaces new artists and albums they're likely to love — sending approved ones straight to Lidarr for download. My kids wake up and there's new music in their library that they didn't have to search for. My wife's playlists update themselves. Nobody has to touch a thing.

**JellyDJ is what Jellyfin's music experience should have been all along.**

<br/>

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 🧠 | **Per-User Taste Profiles** | Affinity scores built from play counts, recency, skips, favorites, and replay signals — per person |
| 📋 | **Smart Playlists** | *For You*, *Discover Weekly*, *Most Played*, *Recently Played* — auto-generated directly in Jellyfin |
| 🔭 | **Discovery Queue** | New artist and album recommendations ranked by affinity + novelty, ready to approve or reject |
| 📥 | **Auto-Download** | Approved discoveries go straight to Lidarr — your library grows while you sleep |
| 🔥 | **Billboard Hot 100** | Weekly chart data cross-referenced with your library so you never miss a trending track |
| 📡 | **Webhook Scoring** | Jellyfin playback events update taste profiles in real time — skips count against bad recs |
| 📊 | **Insights** | Full score breakdowns, genre affinities, top artists, skip analysis, and listening stats per user |
| 🎸 | **Multi-Source Enrichment** | Spotify, Last.fm, MusicBrainz, Billboard — layered signals, no single point of failure |
| 🏠 | **Truly Self-Hosted** | No cloud, no accounts, no tracking. Your data stays on your hardware |

---

## 📸 Screenshots

### Dashboard
*Billboard Hot 100, system stats, per-user sync status, and live activity feed*

<p align="center">
  <img src=".github/images/shot-dashboard.png" alt="JellyDJ Dashboard" width="100%" />
</p>

### Discovery Queue &amp; Insights
*Review album recommendations with one tap &nbsp;·&nbsp; Deep dive into your taste profile with full score breakdowns*

<p align="center">
  <img src=".github/images/shot-discovery-insights.png" alt="Discovery Queue and Insights" width="100%" />
</p>

### Automation Settings
*Control every scheduler interval, enable auto-download, and tune enrichment — all from the UI*

<p align="center">
  <img src=".github/images/shot-settings.png" alt="Settings" width="50%" />
</p>

---

## 🏗️ How It Works

<p align="center">
  <img src=".github/images/architecture.png" alt="Architecture" width="100%" />
</p>

JellyDJ runs as two Docker containers (FastAPI backend + React frontend) alongside your existing Jellyfin and Lidarr setup. It **never touches your media files** — it only reads play history via the Jellyfin API and writes back playlist metadata.

Every 6 hours (configurable), JellyDJ:
1. Pulls play history from Jellyfin for each user
2. Rebuilds artist + genre affinity profiles per person
3. Scores every track in the library
4. Regenerates smart playlists in Jellyfin
5. Refreshes the discovery queue with new album recommendations

Approved discoveries are automatically sent to Lidarr for download.

---

## 🚀 Quick Start

### Prerequisites

- Docker + Docker Compose
- A running [Jellyfin](https://jellyfin.org) instance
- *(Optional)* [Lidarr](https://lidarr.audio) for auto-download

### 1. Clone

```bash
git clone https://github.com/TehRainMan17/JellyDJ.git
cd JellyDJ
```

### 2. Configure

```bash
cp .env.example .env
```

Generate a secret key and add it to `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

```env
SECRET_KEY=your_generated_key_here
JELLYDJ_PORT=7879   # change if needed
TZ=America/New_York
```

Everything else — Jellyfin URL, API keys, Lidarr — can be configured from the web UI after launch.

### 3. Launch

```bash
docker compose up -d
```

> First startup takes 30–90 seconds. Docker pulls the images automatically — no building required.

### 4. Open

```
http://localhost:7879
```

Complete setup on the **Connections** page, then hit **Index Now** to run your first library scan.

---

## 🐳 Docker Compose Reference

```yaml
version: "3.9"

services:
  backend:
    image: 562ray/jellydj-backend:latest
    restart: unless-stopped
    env_file: .env
    environment:
      - DATABASE_URL=sqlite:////config/jellydj.db
      - TZ=${TZ:-UTC}
    volumes:
      - jellydj-config:/config
    networks:
      - jellydj

  frontend:
    image: 562ray/jellydj-frontend:latest
    restart: unless-stopped
    ports:
      - "${JELLYDJ_PORT:-7879}:3000"
    depends_on:
      - backend
    networks:
      - jellydj

volumes:
  jellydj-config:

networks:
  jellydj:
```

---

## ⚙️ Configuration

All settings are managed from the web UI. The `.env` file only needs the secret key and port.

| Setting | Default | Description |
|---|---|---|
| `JELLYDJ_PORT` | `7879` | Host port for the web UI |
| `SECRET_KEY` | *(required)* | Encrypts stored credentials — generate once, don't change |
| `TZ` | `UTC` | Timezone for scheduled jobs and display |
| `DATABASE_URL` | `sqlite:////config/jellydj.db` | SQLite (default) or PostgreSQL for larger libraries |

> ⚠️ Changing `SECRET_KEY` after setup will invalidate all stored API keys. You'll need to re-enter them in the UI.

### External API Keys *(all optional)*

| Service | Used For | Required? |
|---|---|---|
| **Jellyfin** | Play history, playlist write-back | ✅ Core |
| **Lidarr** | Auto-download approved albums | Optional |
| **Spotify** | Popularity scores, album metadata | Optional |
| **Last.fm** | Artist similarity, tags, enrichment | Optional |
| **Billboard** | Hot 100 chart data | ✅ Free, no key needed |

---

## 🔄 Updating

```bash
docker compose pull
docker compose up -d
```

Your library data and settings live in the `jellydj-config` Docker volume and persist across updates.

---

## 🛠️ Troubleshooting

**View live logs**
```bash
docker compose logs -f
```

**Reset everything** *(destructive — deletes all data)*
```bash
docker compose down -v
```

**Billboard chart not loading**
The first load scrapes Billboard's website and takes ~10 seconds. If it fails, check that your Docker host has outbound internet access.

**Playlists not appearing in Jellyfin**
Make sure the Jellyfin API key has write permissions and that at least one library scan has completed successfully.

**Discovery queue is empty**
Run a full index first (Dashboard → Index Now), then trigger a Discovery Refresh from the Settings page.

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

- 🐛 **Bug reports** → [open an issue](https://github.com/TehRainMan17/JellyDJ/issues)
- 💡 **Feature requests** → [open a discussion](https://github.com/TehRainMan17/JellyDJ/discussions)
- 🔧 **Pull requests** → fork, branch off `main`, and submit

---

## 📄 License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE) for details.

AGPL means: if you run a modified version of JellyDJ as a network service, you must make your modifications available under the same license.

---

<p align="center">
  Built with 🪼 by <a href="https://github.com/TehRainMan17">TehRainMan17</a>
  &nbsp;—&nbsp; because my girls deserve great music discovery without giving up their privacy.
  <br/><br/>
  <a href="https://github.com/TehRainMan17/JellyDJ/stargazers">⭐ If JellyDJ fills the same void for your family, a star means a lot.</a>
</p>
