# JellyDJ

A self-hosted music recommendation engine for Jellyfin.

JellyDJ analyzes your Jellyfin play history, builds personalized taste
profiles per user, generates smart playlists directly in Jellyfin, and
surfaces new album recommendations that can optionally be sent to Lidarr
for download.

------------------------------------------------------------------------

## Features

-   **Smart playlists** --- "For You", "Discover Weekly", and more
    generated automatically
-   **Skip-aware scoring** --- playback webhooks improve recommendation
    accuracy
-   **Discovery queue** --- find new artists and albums based on
    listening habits
-   **Auto-download** --- optionally send approved discoveries to Lidarr
-   **Insights** --- listening stats, genre breakdowns, and user taste
    profiles

------------------------------------------------------------------------

# Quick Start (Recommended)

The primary deployment method is using the **pre-built Docker images**
with the included `docker-compose.yml`.

No building required.

## 1. Clone the repository

``` bash
git clone https://github.com/YOUR_USERNAME/jellydj.git
cd jellydj
```

## 2. Create your environment file

``` bash
cp .env.example .env
```

Generate a secret key:

``` bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Add it to `.env`:

    SECRET_KEY=your_generated_key_here

Optional settings:

    JELLYDJ_PORT=7879
    TZ=UTC

Everything else can be configured in the web UI after launch.

------------------------------------------------------------------------

## 3. Start JellyDJ

``` bash
docker compose up -d
```

Docker will automatically pull the latest JellyDJ images.

Startup usually takes **30--90 seconds**.

------------------------------------------------------------------------

## 4. Open the Web UI

Open:

    http://localhost:7879

Then complete setup in the **Connections** page.

------------------------------------------------------------------------

# Docker Compose (Sanitized Reference)

``` yaml
version: "3.9"

services:
  backend:
    image: 562ray/jellydj-backend:latest
    restart: unless-stopped
    env_file:
      - .env
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

------------------------------------------------------------------------

# Updating

``` bash
docker compose pull
docker compose up -d
```

Your data is stored in the Docker volume and will persist.

------------------------------------------------------------------------

# Troubleshooting

### View logs

``` bash
docker compose logs -f
```

### Reset all data

``` bash
docker compose down -v
```

------------------------------------------------------------------------

# License

GNU Affero General Public License v3.0 (AGPL-3.0)
