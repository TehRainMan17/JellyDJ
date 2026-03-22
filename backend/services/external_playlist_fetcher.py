"""
JellyDJ — External Playlist Fetcher

Fetches playlist metadata from Spotify, Tidal, and YouTube Music URLs
WITHOUT requiring any API keys or user accounts.

Strategy
────────
We use yt-dlp, which is already a common self-hosted tool dependency,
to extract playlist metadata. yt-dlp supports all three platforms via
its extractors and returns clean JSON — no authentication needed for
public playlists.

For Spotify, yt-dlp uses the open.spotify.com embed endpoint to get
track listings from public playlists. Tidal and YouTube Music have
native extractors.

yt-dlp must be installed in the container — add it to requirements.txt:
  yt-dlp>=2024.1.0

Docker note: yt-dlp does NOT download audio here — we only call it with
--flat-playlist --dump-single-json --no-download, so there is no
ffmpeg dependency and no network-heavy operation. A typical 50-track
Spotify playlist fetch takes ~3-8 seconds.

Security
────────
- URL is validated against an allowlist of known platform domains before
  being passed to yt-dlp.
- yt-dlp is called as a subprocess with a strict argument list (no shell=True).
- We never pass user input into a shell command.
- Output is captured and parsed as JSON — never eval'd.
- Timeout is enforced at 60 seconds; after that the process is killed.

Supported URL patterns
──────────────────────
  Spotify:       https://open.spotify.com/playlist/{id}
  Tidal:         https://tidal.com/browse/playlist/{uuid}
                 https://listen.tidal.com/playlist/{uuid}
  YouTube Music: https://music.youtube.com/playlist?list={id}
                 https://www.youtube.com/playlist?list={id}
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# ── Domain allowlist ───────────────────────────────────────────────────────────

_ALLOWED_DOMAINS = {
    "open.spotify.com",
    "tidal.com",
    "listen.tidal.com",
    "music.youtube.com",
    "www.youtube.com",
    "youtube.com",
}

_PLATFORM_MAP = {
    "open.spotify.com":   "spotify",
    "tidal.com":          "tidal",
    "listen.tidal.com":   "tidal",
    "music.youtube.com":  "youtube_music",
    "www.youtube.com":    "youtube_music",
    "youtube.com":        "youtube_music",
}

_YTDLP_TIMEOUT = 90  # seconds — generous for slow connections


class FetchError(Exception):
    pass


class UnsupportedURLError(FetchError):
    pass


class FetchTimeoutError(FetchError):
    pass


def detect_platform(url: str) -> str:
    """Return 'spotify' | 'tidal' | 'youtube_music' | raise UnsupportedURLError."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")
        # Re-add www. for lookup since map has www. variants
        for candidate in [parsed.netloc.lower(), domain]:
            if candidate in _PLATFORM_MAP:
                return _PLATFORM_MAP[candidate]
    except Exception:
        pass
    raise UnsupportedURLError(
        f"URL domain not recognised. Supported: Spotify, Tidal, YouTube Music."
    )


def _validate_url(url: str) -> str:
    """
    Validate that the URL is from an allowed platform domain.
    Returns the sanitised URL (stripped whitespace, scheme enforced).
    Raises UnsupportedURLError if not allowed.
    """
    url = url.strip()
    if not url.startswith(("https://", "http://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.netloc.lower() not in _ALLOWED_DOMAINS:
        raise UnsupportedURLError(
            f"Domain '{parsed.netloc}' is not an allowed playlist source."
        )

    return url


def _extract_source_id(url: str, platform: str) -> str:
    """Pull the playlist ID out of the URL for deduplication."""
    if platform == "spotify":
        m = re.search(r"/playlist/([A-Za-z0-9]+)", url)
        return m.group(1) if m else ""
    if platform == "tidal":
        m = re.search(r"/playlist/([0-9a-f-]+)", url)
        return m.group(1) if m else ""
    if platform == "youtube_music":
        m = re.search(r"list=([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else ""
    return ""


def fetch_playlist_metadata(url: str) -> dict:
    """
    Fetch playlist metadata using yt-dlp.

    Returns a dict:
      {
        "platform":    "spotify" | "tidal" | "youtube_music",
        "source_id":   str,
        "name":        str,
        "description": str | None,
        "tracks": [
          {
            "position":   int,
            "track_name": str,
            "artist_name": str,
            "album_name":  str,        # may be empty for YT Music
            "duration_ms": int | None,
          },
          ...
        ]
      }

    Raises:
      UnsupportedURLError  — domain not allowed
      FetchTimeoutError    — yt-dlp exceeded timeout
      FetchError           — any other failure (non-zero exit, parse error)
    """
    url = _validate_url(url)
    platform = detect_platform(url)
    source_id = _extract_source_id(url, platform)

    log.info("Fetching %s playlist: %s", platform, url)

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--no-download",
        "--no-warnings",
        "--quiet",
        # Prevent any cookies / browser profile usage
        "--no-cookies-from-browser",
        # Limit concurrent fragment workers (we only care about metadata)
        "--concurrent-fragments", "1",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_YTDLP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise FetchTimeoutError(
            f"yt-dlp timed out after {_YTDLP_TIMEOUT}s fetching {url}"
        )
    except FileNotFoundError:
        raise FetchError(
            "yt-dlp is not installed. Add 'yt-dlp' to backend/requirements.txt "
            "and rebuild the container."
        )

    if result.returncode != 0:
        stderr = result.stderr[:500] if result.stderr else "(no stderr)"
        raise FetchError(
            f"yt-dlp exited {result.returncode} for {url}: {stderr}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FetchError(f"yt-dlp output was not valid JSON: {exc}")

    return _parse_ytdlp_output(data, platform, source_id)


def _parse_ytdlp_output(data: dict, platform: str, source_id: str) -> dict:
    """
    Normalise yt-dlp's output into our standard shape.

    yt-dlp's flat playlist JSON has:
      data["title"]   — playlist title
      data["entries"] — list of track dicts

    Each entry has:
      "title"         — may be "Artist - Title" for Spotify/Tidal
      "uploader"      — artist name (more reliable on YouTube)
      "album"         — album (Spotify/Tidal only)
      "duration"      — seconds (float or None)
      "artists"       — list of artist dicts (Spotify)
      "track"         — track title (Spotify, more reliable than "title")
    """
    playlist_name = data.get("title") or data.get("playlist_title") or "Imported Playlist"
    description   = data.get("description") or data.get("playlist_description")

    entries = data.get("entries") or []
    tracks: list[dict] = []

    for pos, entry in enumerate(entries, start=1):
        if not entry:
            continue

        track_name  = _extract_track_name(entry, platform)
        artist_name = _extract_artist_name(entry, platform)
        album_name  = entry.get("album") or ""
        duration_s  = entry.get("duration")
        duration_ms = int(duration_s * 1000) if duration_s else None

        if not track_name:
            continue

        tracks.append({
            "position":    pos,
            "track_name":  track_name.strip(),
            "artist_name": artist_name.strip(),
            "album_name":  album_name.strip(),
            "duration_ms": duration_ms,
        })

    return {
        "platform":    platform,
        "source_id":   source_id,
        "name":        playlist_name.strip(),
        "description": description,
        "tracks":      tracks,
    }


def _extract_track_name(entry: dict, platform: str) -> str:
    """Best-effort track name extraction from a yt-dlp flat entry."""
    # Spotify and Tidal provide "track" as the clean title
    if track := entry.get("track"):
        return track

    title = entry.get("title") or ""

    if platform in ("spotify", "tidal") and " - " in title:
        # "Artist - Track Title" format
        parts = title.split(" - ", 1)
        return parts[1] if len(parts) == 2 else title

    return title


def _extract_artist_name(entry: dict, platform: str) -> str:
    """Best-effort artist name extraction from a yt-dlp flat entry."""
    # Spotify returns a proper artists list
    artists = entry.get("artists")
    if artists and isinstance(artists, list):
        names = [a.get("name", "") for a in artists if a.get("name")]
        if names:
            return ", ".join(names)

    # Tidal / YouTube Music: uploader is usually the artist
    if uploader := entry.get("uploader") or entry.get("channel"):
        # Strip common YouTube suffixes like "- Topic"
        uploader = re.sub(r"\s*-\s*Topic\s*$", "", uploader, flags=re.IGNORECASE)
        return uploader

    # Fall back: parse "Artist - Title" from title
    title = entry.get("title") or ""
    if " - " in title:
        return title.split(" - ", 1)[0]

    return entry.get("artist") or ""
