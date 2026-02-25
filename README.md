JellyDJ
A self-hosted music recommendation engine for Jellyfin.

JellyDJ analyses your Jellyfin play history, builds personalised taste profiles per user, generates smart playlists directly in Jellyfin, and surfaces new album recommendations that can be automatically sent to Lidarr for download.

What it does
Smart playlists — "For You", "Discover Weekly", "Most Played", and "Recently Played" playlists regenerated on a schedule and written directly into Jellyfin.

Skip-aware scoring — Listens to Jellyfin playback webhooks; songs you skip stop appearing, songs you favorite get boosted.

Discovery queue — Finds new artists and missing albums based on your listening habits for review before downloading.

Auto-download — Optionally sends approved discovery items to Lidarr automatically on a configurable schedule.

Insights — Per-user listening statistics, top artists, genre breakdowns, and skip rate analysis.

Quick Start (Recommended)
The fastest way to get JellyDJ running is to use the pre-built images from Docker Hub.

1. Create a docker-compose.yml
Create a new directory and save the following as docker-compose.yml:

YAML
version: "3.9"

services:
  backend:
    image: 562ray/jellydj-backend:latest
    container_name: jellydj-backend
    restart: unless-stopped
    environment:
      - SECRET_KEY=change-me-generate-a-real-secret
      - TZ=UTC
      - DATABASE_URL=sqlite:////config/jellydj.db
    volumes:
      - ./jellydj-config:/config
    networks:
      - jellydj-net
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  frontend:
    image: 562ray/jellydj-frontend:latest
    container_name: jellydj-frontend
    restart: unless-stopped
    ports:
      - "7879:3000"
    depends_on:
      backend:
        condition: service_healthy
    networks:
      - jellydj-net

volumes:
  jellydj-config:
    name: jellydj-config

networks:
  jellydj-net:
    name: jellydj-net
    driver: bridge
2. Start JellyDJ
Run the following command in your terminal:

Bash
docker compose up -d
3. Open the UI
Navigate to http://localhost:7879 and follow the setup steps in the Connections page.

Advanced: Build from Source
Use this method if you want to modify the code or contribute to the project.

1. Clone the repository
Bash
git clone https://github.com/YOUR_USERNAME/jellydj.git
cd jellydj
2. Create your .env file
Bash
cp .env.example .env
Generate a SECRET_KEY and add it to your .env:

Bash
python -c "import secrets; print(secrets.token_hex(32))"
3. Build and Start
This will build the local containers from the /backend and /frontend directories:

Bash
docker compose up -d --build
Configuration
Connecting to Jellyfin
Open the Connections page in JellyDJ.

Enter your Jellyfin URL and an API key.

Click Test — JellyDJ needs read access to your library and write access to create playlists.

Setting up webhooks (enables skip detection)
Install the Webhook plugin in Jellyfin (Dashboard → Plugins → Catalogue).

Create a new webhook pointing to http://jellydj-backend:8000/api/webhooks/jellyfin.

Enable: PlaybackStart, PlaybackProgress, and PlaybackStop.

External APIs (Optional)
Adding Last.fm or Spotify keys in the UI significantly improves discovery quality by providing artist similarity and popularity scores.

Troubleshooting
Page won't load: Wait 30 seconds for the backend health check to pass.

Port in use: Change JELLYDJ_PORT in your .env or the host port in your docker-compose.yml.

Empty Discovery: Run "Index Now" first to build your taste profile.

Logs: Run docker compose logs -f to see what's happening under the hood.

License
GNU Affero General Public License v3.0 (AGPL-3.0)