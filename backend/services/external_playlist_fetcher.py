"""
JellyDJ — External Playlist Fetcher

Fetches playlist metadata from Spotify, Tidal, and YouTube Music URLs.

Strategy
────────
- Spotify: Fetches the public embed page and extracts track data from the
  server-rendered HTML. No API keys or user auth required — works for any
  public playlist. Falls back to suggesting the browser extension if the
  embed page doesn't yield tracks.
- Tidal / YouTube Music: Uses yt-dlp to extract playlist metadata.
  yt-dlp is called with --flat-playlist --dump-single-json --no-download
  (metadata only, no audio, no ffmpeg dependency).

Security
────────
- URL is validated against an allowlist of known platform domains.
- yt-dlp is called as a subprocess with a strict argument list (no shell=True).
- We never pass user input into a shell command.
- Output is captured and parsed as JSON — never eval'd.
- Timeout is enforced at 90 seconds; after that the process is killed.

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


def _find_track_list(obj, depth=0):
    """
    Recursively search a nested dict/list for the first list of dicts
    that look like tracks (have a 'title' or 'name' key).
    """
    if depth > 10:
        return None
    if isinstance(obj, list) and len(obj) >= 2:
        # Check if this looks like a track list
        if all(isinstance(item, dict) and ("title" in item or "name" in item)
               for item in obj[:3]):
            return obj
    if isinstance(obj, dict):
        for value in obj.values():
            result = _find_track_list(value, depth + 1)
            if result:
                return result
    if isinstance(obj, list):
        for item in obj:
            result = _find_track_list(item, depth + 1)
            if result:
                return result
    return None


def _fetch_spotify_embed(url: str) -> dict:
    """
    Fetch Spotify playlist tracks from the public embed endpoint.

    Spotify's embed page at /embed/playlist/{id} returns HTML containing
    a <script id="__NEXT_DATA__"> tag with full track listings as JSON.
    No API key, no auth, no premium account needed — just a public playlist.
    """
    import httpx

    playlist_id = _extract_source_id(url, "spotify")
    if not playlist_id:
        raise FetchError("Could not extract Spotify playlist ID from URL")

    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    log.info("Fetching Spotify embed: %s", embed_url)

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(embed_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            })
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        raise FetchError(f"Failed to fetch Spotify embed page: {exc}")

    # Extract __NEXT_DATA__ JSON blob from the HTML
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*({.+?})\s*</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise FetchError(
            "Could not extract track data from Spotify embed page. "
            "The playlist may be private. Try using the browser extension instead."
        )

    try:
        next_data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FetchError(f"Failed to parse Spotify embed data: {exc}")

    # Navigate the __NEXT_DATA__ structure to find track list.
    # The embed structure has changed over time, so we try multiple paths.
    track_list = None
    playlist_name = "Spotify Playlist"

    # Known paths for the track list in __NEXT_DATA__
    _PATHS = [
        # 2024+ structure
        lambda d: d["props"]["pageProps"]["state"]["data"]["entity"],
        # Alternate structure
        lambda d: d["props"]["pageProps"],
    ]

    for path_fn in _PATHS:
        try:
            entity = path_fn(next_data)
            tl = entity.get("trackList") or entity.get("tracks") or entity.get("items")
            if tl and isinstance(tl, list) and len(tl) > 0:
                track_list = tl
                playlist_name = entity.get("name") or entity.get("title") or playlist_name
                break
        except (KeyError, TypeError, AttributeError):
            continue

    # Fallback: recursively search for any list of dicts containing "title" keys
    if not track_list:
        track_list = _find_track_list(next_data)

    if not track_list:
        raise FetchError(
            "No tracks found in Spotify embed. The playlist may be empty, "
            "private, or the embed format has changed. "
            "Please use the browser extension to import this playlist."
        )

    tracks = []
    for pos, item in enumerate(track_list, start=1):
        if not isinstance(item, dict):
            continue
        # Try multiple field names for track title
        track_name = (
            item.get("title") or item.get("name") or
            item.get("track_name") or ""
        )
        # Artist: could be "subtitle", "artists", "artist_name", or "artist"
        artist_name = item.get("subtitle") or item.get("artist_name") or ""
        if not artist_name:
            artists = item.get("artists")
            if isinstance(artists, list):
                names = [a.get("name", "") for a in artists if isinstance(a, dict)]
                artist_name = ", ".join(n for n in names if n)
            elif isinstance(artists, str):
                artist_name = artists
        if not artist_name:
            artist_name = item.get("artist") or ""
        album_name = item.get("album") or item.get("album_name") or ""
        if isinstance(album_name, dict):
            album_name = album_name.get("name", "")
        duration_ms = item.get("duration") or item.get("duration_ms") or None

        if not track_name:
            continue

        tracks.append({
            "position":    pos,
            "track_name":  track_name.strip(),
            "artist_name": artist_name.strip(),
            "album_name":  album_name.strip() if isinstance(album_name, str) else "",
            "duration_ms": duration_ms,
        })

    log.info("Spotify embed: found %d tracks in '%s'", len(tracks), playlist_name)

    return {
        "platform":    "spotify",
        "source_id":   playlist_id,
        "name":        playlist_name,
        "description": None,
        "tracks":      tracks,
    }


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

    # Spotify: use embed page scraper (yt-dlp doesn't support Spotify DRM)
    if platform == "spotify":
        return _fetch_spotify_embed(url)

    source_id = _extract_source_id(url, platform)

    log.info("Fetching %s playlist via yt-dlp: %s", platform, url)

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
