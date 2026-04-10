"""
JellyDJ — YouTube Rip Router

Downloads audio from a YouTube video, converts it to MP3, and saves it into
the user-configured YOUTUBE_RIPS_PATH volume so Jellyfin can index it.

NOTE ON AUDIO QUALITY
  YouTube serves audio at 128–160 kbps (opus/m4a).  yt-dlp downloads the
  best available stream; ffmpeg re-encodes it to 320 kbps MP3 CBR.  This is
  the standard library-friendly format, but it does NOT recover quality that
  wasn't in the source — it is a re-encode, not a lossless upgrade.

Endpoints
─────────
  POST  /api/import/youtube-rip                    Submit a YouTube video URL
  GET   /api/import/youtube-rip/status/{job_id}    Poll job progress
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import UserContext
from database import get_db
# Re-use the dual-auth dependency (X-JellyDJ-Key API key OR Bearer JWT) that
# the playlist import router already exposes.  This lets the browser extension
# authenticate with its API key while the dashboard UI can use its JWT.
from routers.playlist_import import get_user_from_api_key_or_jwt

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/import", tags=["youtube-rip"])

# Container-internal mount point — always fixed; host path comes from env via
# the docker-compose volume binding.
_CONTAINER_RIP_DIR = Path("/music/youtube-rips")

# In-memory job store (lives for the process lifetime; survives requests but
# not container restarts).  Keys are UUID job IDs.
_jobs: dict[str, dict] = {}

# Maximum number of concurrent yt-dlp/ffmpeg workers.  Each rip is CPU- and
# memory-intensive; allowing unbounded parallelism can OOM the container or
# overload Jellyfin with back-to-back library refresh calls.
_MAX_CONCURRENT_RIPS = 2
_rip_semaphore = threading.Semaphore(_MAX_CONCURRENT_RIPS)

# Maps a clean video URL → job_id for any job that is still in-flight.
# Prevents the same video from being queued multiple times simultaneously.
_active_urls: dict[str, str] = {}
_active_urls_lock = threading.Lock()

# ── URL validation ────────────────────────────────────────────────────────────

_YOUTUBE_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/watch|youtu\.be/|music\.youtube\.com/watch)",
    re.IGNORECASE,
)


def _strip_to_video_url(url: str) -> str:
    """
    Return a clean single-video URL, stripping any playlist/index params.
    Prevents yt-dlp from accidentally downloading an entire playlist when the
    user is on a video that's part of one.
    """
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    video_id = (qs.get("v") or [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    # youtu.be short links — drop all query params
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


# ── Title / filename cleaning ─────────────────────────────────────────────────

# Bracketed noise common in YouTube titles: "(Official Video)", "[Lyrics]", etc.
_BRACKET_NOISE = re.compile(
    r"\s*[\(\[]\s*"
    r"(?:official\s*(?:music\s*)?(?:lyric\s*)?(?:audio\s*)?(?:video)?|"
    r"lyric(?:s)?\s*(?:video)?|lyrics?|hd|4k|explicit|clean\s*version|"
    r"remaster(?:ed)?(?:\s*\d{4})?|visuali[sz]er|audio|video|mv|m\/v|"
    r"full\s*(?:video|song)|vevo|amv)"
    r"\s*[\)\]]\s*",
    re.IGNORECASE,
)
# Trailing " - Official Video" / "| Official Audio" forms
_SUFFIX_NOISE = re.compile(
    r"\s*[-|]\s*(?:official\s*(?:music\s*)?(?:lyric\s*)?(?:audio\s*)?(?:video)?|"
    r"lyrics?)\s*$",
    re.IGNORECASE,
)


def _clean_title(raw: str) -> str:
    cleaned = _BRACKET_NOISE.sub(" ", raw)
    cleaned = _SUFFIX_NOISE.sub("", cleaned)
    return " ".join(cleaned.split())  # collapse internal whitespace


def _safe_name(name: str) -> str:
    """Strip characters that are invalid in file / directory names on any OS."""
    # Replace Windows-illegal chars and path separators
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Collapse consecutive underscores and trim
    name = re.sub(r"__+", "_", name).strip("_ ")
    return name or "Unknown"


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RipRequest(BaseModel):
    url: str
    # Optional user-provided metadata (from the browser extension's pre-rip modal).
    # When supplied, these override the YouTube video title / channel name for the
    # output filename, folder structure, and ID3 tags.
    user_title:     str | None = None
    user_artist:    str | None = None
    user_album:     str | None = None
    user_year:      str | None = None
    recording_mbid: str | None = None   # MusicBrainz recording MBID
    artist_mbid:    str | None = None   # MusicBrainz artist MBID
    release_mbid:   str | None = None   # MusicBrainz release MBID (for Cover Art Archive)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/youtube-rip")
def start_rip(
    payload: RipRequest,
    user: UserContext = Depends(get_user_from_api_key_or_jwt),
    db: Session = Depends(get_db),
):
    """
    Accept a YouTube video URL, queue a background download job, and return a
    job ID the caller can use to poll /status/{job_id}.
    """
    url = payload.url.strip()
    if not _YOUTUBE_RE.match(url):
        raise HTTPException(400, "URL must be a YouTube video URL (youtube.com/watch or youtu.be)")

    clean_url = _strip_to_video_url(url)

    # Verify the mount point exists — indicates YOUTUBE_RIPS_PATH was set and
    # the docker-compose volume binding is active.
    if not _CONTAINER_RIP_DIR.exists():
        raise HTTPException(
            500,
            "YouTube rips directory not found. "
            "Set YOUTUBE_RIPS_PATH in your .env and restart the stack — "
            f"expected container path: {_CONTAINER_RIP_DIR}",
        )

    # Deduplicate: if this exact video is already being ripped, return the
    # existing job so the caller can poll it instead of spawning a duplicate.
    with _active_urls_lock:
        existing_job_id = _active_urls.get(clean_url)
        if existing_job_id and existing_job_id in _jobs:
            log.info("YouTube rip already in progress: job=%s url=%s", existing_job_id, clean_url)
            return {"job_id": existing_job_id, "status": _jobs[existing_job_id]["status"]}

    # Enforce concurrency cap — reject rather than queue unboundedly.
    if not _rip_semaphore.acquire(blocking=False):
        raise HTTPException(
            429,
            f"Too many rips in progress (max {_MAX_CONCURRENT_RIPS}). "
            "Wait for a current job to finish before starting another.",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "queued",
        "artist": None,
        "title":  None,
        "path":   None,
        "error":  None,
    }

    with _active_urls_lock:
        _active_urls[clean_url] = job_id

    t = threading.Thread(
        target=_run_rip,
        args=(job_id, clean_url, payload),
        daemon=True,
        name=f"yt-rip-{job_id[:8]}",
    )
    t.start()
    log.info("YouTube rip queued: job=%s url=%s", job_id, clean_url)
    return {"job_id": job_id, "status": "queued"}


@router.get("/youtube-rip/status/{job_id}")
def rip_status(job_id: str, _: UserContext = Depends(get_user_from_api_key_or_jwt)):
    """Poll the status of a previously submitted rip job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found — it may have expired (server restarted)")
    return job


# ── Background worker ─────────────────────────────────────────────────────────

def _run_rip(job_id: str, url: str, payload: RipRequest) -> None:
    job = _jobs[job_id]

    try:
        _run_rip_inner(job_id, url, job, payload)
    finally:
        # Always release the concurrency slot and deregister the URL so future
        # requests for the same video are accepted again.
        _rip_semaphore.release()
        with _active_urls_lock:
            _active_urls.pop(url, None)


def _run_rip_inner(job_id: str, url: str, job: dict, payload: RipRequest) -> None:
    try:
        import yt_dlp  # already in requirements.txt
    except ImportError:
        job["status"] = "error"
        job["error"] = "yt-dlp is not installed — rebuild the container"
        return

    # ── Phase 1: extract metadata without downloading ─────────────────────────
    job["status"] = "fetching_info"
    try:
        info_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        log.warning("yt-dlp metadata extraction failed for %s: %s", url, exc)
        job["status"] = "error"
        job["error"] = f"Could not fetch video info: {exc}"
        return

    # Resolve artist and title: prefer user-supplied values from the extension's
    # pre-rip modal, fall back to YouTube metadata.
    if payload.user_artist:
        artist = _safe_name(payload.user_artist)
    else:
        raw_artist = (
            info.get("artist")
            or info.get("uploader")
            or info.get("channel")
            or "Unknown Artist"
        )
        artist = _safe_name(raw_artist)

    if payload.user_title:
        title = _safe_name(payload.user_title)
    else:
        raw_title = info.get("title") or "Unknown Title"
        title = _safe_name(_clean_title(raw_title))

    job["artist"] = artist
    job["title"]  = title

    # ── Phase 2: download + convert to a temp directory ──────────────────────
    # We intentionally write to /tmp first so that Jellyfin never sees a
    # partial or in-progress file.  The completed MP3 is moved atomically into
    # the bind-mounted library directory only after yt-dlp + ffmpeg finish.
    job["status"] = "downloading"

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"yt-rip-{job_id[:8]}-"))
    try:
        out_template = str(tmp_dir / f"{title}.%(ext)s")
        tmp_mp3      = tmp_dir / f"{title}.mp3"

        dl_opts: dict = {
            "format":      "bestaudio/best",
            "outtmpl":     out_template,
            "noplaylist":  True,
            "quiet":       True,
            "no_warnings": True,
            # postprocessors run in order:
            #   1. Extract audio and re-encode to 320 kbps MP3 CBR via ffmpeg
            #   2. Write ID3 tags (title, artist, uploader URL) into the MP3
            "postprocessors": [
                {
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   "mp3",
                    # "320" → fixed 320 kbps CBR.  Note: source audio from YouTube
                    # is typically 128–160 kbps opus; this is a re-encode at a higher
                    # container bitrate, not a quality gain from the source stream.
                    "preferredquality": "320",
                },
                {
                    "key":          "FFmpegMetadata",
                    "add_metadata": True,
                },
            ],
        }

        try:
            job["status"] = "converting"
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as exc:
            log.warning("yt-dlp download failed for %s: %s", url, exc)
            job["status"] = "error"
            job["error"] = str(exc)
            return
        except Exception as exc:
            log.exception("Unexpected error ripping %s", url)
            job["status"] = "error"
            job["error"] = f"Unexpected error: {exc}"
            return

        if not tmp_mp3.exists():
            # yt-dlp may have chosen a slightly different filename.
            candidates = list(tmp_dir.glob(f"{title}.*"))
            if candidates:
                tmp_mp3 = candidates[0]
            else:
                job["status"] = "error"
                job["error"] = "Download appeared to succeed but no output file found"
                return

        # ── Phase 3: move completed file into the Jellyfin-watched directory ──
        # Organise as Artist/Album/Title.mp3 when album is known, else Artist/Title.mp3.
        # Only after a successful encode do we touch the bind mount, so Jellyfin
        # will never scan an incomplete file.
        if payload.user_album:
            safe_album = _safe_name(payload.user_album)
            dest_dir   = _CONTAINER_RIP_DIR / artist / safe_album
        else:
            dest_dir = _CONTAINER_RIP_DIR / artist

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            job["status"] = "error"
            job["error"] = f"Cannot create output directory {dest_dir}: {exc}"
            return

        final_mp3 = dest_dir / tmp_mp3.name
        shutil.move(str(tmp_mp3), str(final_mp3))

        # ── Phase 4: cover art ───────────────────────────────────────────────
        # Fetch cover art now that we have the final file path and both
        # potential sources: the MusicBrainz release MBID (if the user matched
        # in the pre-rip modal) and the YouTube video thumbnail (fallback).
        # Embed into the MP3 as an ID3 APIC frame AND save cover.jpg in the
        # destination directory so Jellyfin finds art via both paths.
        cover_bytes = _fetch_cover_art(
            release_mbid  = payload.release_mbid,
            thumbnail_url = _best_thumbnail(info),
        )
        if cover_bytes:
            _embed_cover_art(final_mp3, cover_bytes)
            _save_cover_file(dest_dir, cover_bytes)

        # ── Phase 5: overwrite ID3 tags with correct metadata ────────────────
        # yt-dlp's FFmpegMetadata wrote the raw YouTube title/channel into the
        # tags; if the user confirmed different (MusicBrainz-matched) metadata,
        # rewrite those tags now so Jellyfin indexes the song correctly.
        has_user_meta = any([
            payload.user_title, payload.user_artist, payload.user_album,
            payload.recording_mbid,
        ])
        if has_user_meta:
            _write_id3_tags(
                final_mp3,
                title          = payload.user_title or title,
                artist         = payload.user_artist or artist,
                album          = payload.user_album or "",
                year           = payload.user_year or "",
                recording_mbid = payload.recording_mbid or "",
                artist_mbid    = payload.artist_mbid or "",
            )

    finally:
        # Always clean up the temp directory, even on error paths.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    job["path"] = str(final_mp3)

    job["status"] = "done"
    log.info("YouTube rip complete: %s / %s → %s", artist, title, final_mp3)


def _best_thumbnail(info: dict) -> str | None:
    """Return the highest-resolution thumbnail URL from a yt-dlp info dict."""
    thumbnails = info.get("thumbnails") or []
    ranked = sorted(
        [t for t in thumbnails if t.get("url")],
        key=lambda t: t.get("width", 0) * t.get("height", 0),
        reverse=True,
    )
    if ranked:
        return ranked[0]["url"]
    return info.get("thumbnail")


def _crop_to_square(img_bytes: bytes) -> bytes:
    """
    Center-crop image bytes to a square JPEG.
    YouTube thumbnails are 16:9 — we crop the sides to make them square so
    Jellyfin doesn't letterbox or distort the album art.
    Returns original bytes unchanged if Pillow is unavailable or crop fails.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        size = min(w, h)
        left = (w - size) // 2
        top  = (h - size) // 2
        cropped = img.crop((left, top, left + size, top + size))
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as exc:
        log.debug("Image crop to square failed (using original): %s", exc)
        return img_bytes


def _fetch_cover_art(release_mbid: str | None, thumbnail_url: str | None) -> bytes | None:
    """
    Fetch cover art for the ripped track.

    Resolution order
    ────────────────
    1. MusicBrainz Cover Art Archive (front image for the matched release).
       Requires a valid release_mbid from the user's MusicBrainz selection.
    2. YouTube video thumbnail, center-cropped from 16:9 to square via Pillow.

    Returns JPEG bytes, or None if both sources fail.
    """
    import httpx

    # ── Option 1: Cover Art Archive ───────────────────────────────────────────
    if release_mbid:
        try:
            resp = httpx.get(
                f"https://coverartarchive.org/release/{release_mbid}/front",
                follow_redirects=True,
                timeout=20.0,
                headers={"User-Agent": "JellyDJ/1.0 (self-hosted; github.com/TehRainMan17)"},
            )
            if resp.status_code == 200 and resp.content:
                log.info("Cover art fetched from Cover Art Archive for release %s", release_mbid)
                return resp.content
            log.debug("Cover Art Archive returned HTTP %s for release %s", resp.status_code, release_mbid)
        except Exception as exc:
            log.debug("Cover Art Archive request failed for %s: %s", release_mbid, exc)

    # ── Option 2: YouTube thumbnail (cropped to square) ───────────────────────
    if thumbnail_url:
        try:
            resp = httpx.get(thumbnail_url, follow_redirects=True, timeout=15.0)
            if resp.status_code == 200 and resp.content:
                log.info("Cover art fetched from YouTube thumbnail, cropping to square")
                return _crop_to_square(resp.content)
            log.debug("YouTube thumbnail fetch returned HTTP %s", resp.status_code)
        except Exception as exc:
            log.debug("YouTube thumbnail fetch failed: %s", exc)

    log.debug("No cover art source succeeded for release_mbid=%s thumbnail=%s", release_mbid, thumbnail_url)
    return None


def _embed_cover_art(path: Path, cover_bytes: bytes) -> None:
    """Embed JPEG cover art as an ID3 APIC (Attached Picture) frame in an MP3."""
    try:
        from mutagen.id3 import ID3, APIC
        from mutagen.mp3 import MP3
        audio = MP3(str(path), ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(
            encoding=3,          # UTF-8
            mime="image/jpeg",
            type=3,              # Front cover
            desc="Cover",
            data=cover_bytes,
        ))
        audio.save()
        log.info("Cover art embedded in ID3 tags: %s", path)
    except Exception as exc:
        log.warning("Failed to embed cover art in %s: %s", path, exc)


def _save_cover_file(dest_dir: Path, cover_bytes: bytes) -> None:
    """
    Write cover.jpg alongside the MP3 so Jellyfin's file-based art scanner
    also picks it up (useful when ID3 embedding is not read for some reason).
    Only writes if no cover.jpg already exists in the directory.
    """
    cover_path = dest_dir / "cover.jpg"
    if cover_path.exists():
        return  # don't overwrite art from a previous rip in the same album dir
    try:
        cover_path.write_bytes(cover_bytes)
        log.info("cover.jpg saved: %s", cover_path)
    except Exception as exc:
        log.warning("Failed to write cover.jpg to %s: %s", dest_dir, exc)


def _write_id3_tags(
    path: Path,
    title: str,
    artist: str,
    album: str = "",
    year: str = "",
    recording_mbid: str = "",
    artist_mbid: str = "",
) -> None:
    """Overwrite ID3 tags on a completed MP3 with user-verified metadata."""
    try:
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TXXX
        from mutagen.mp3 import MP3
    except ImportError:
        log.warning("mutagen not installed — ID3 tags not updated; add mutagen to requirements.txt")
        return

    try:
        audio = MP3(str(path), ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags

        tags.delall("TIT2")
        tags.add(TIT2(encoding=3, text=title))

        tags.delall("TPE1")
        tags.add(TPE1(encoding=3, text=artist))

        if album:
            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=album))

        if year:
            tags.delall("TDRC")
            tags.add(TDRC(encoding=3, text=str(year)))

        if recording_mbid:
            for key in list(tags.keys()):
                if key.startswith("TXXX:MusicBrainz Recording"):
                    del tags[key]
            tags.add(TXXX(encoding=3, desc="MusicBrainz Recording Id", text=recording_mbid))

        if artist_mbid:
            for key in list(tags.keys()):
                if key.startswith("TXXX:MusicBrainz Artist"):
                    del tags[key]
            tags.add(TXXX(encoding=3, desc="MusicBrainz Artist Id", text=artist_mbid))

        audio.save()
        log.info(
            "ID3 tags written: title=%r artist=%r album=%r mbid=%s",
            title, artist, album, recording_mbid,
        )
    except Exception as exc:
        log.warning("Failed to write ID3 tags for %s: %s", path, exc)


# ── Jellyfin library refresh ──────────────────────────────────────────────────

def _trigger_jellyfin_refresh() -> None:
    """
    Ask Jellyfin to rescan its music libraries so the new MP3 is indexed.
    Best-effort — logs a warning on failure but never raises.
    """
    try:
        import httpx
        from database import SessionLocal
        from models import ConnectionSettings
        from crypto import decrypt

        db = SessionLocal()
        try:
            row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
            if not row or not row.base_url:
                log.warning("Jellyfin not configured — skipping library refresh")
                return
            base = row.base_url.rstrip("/")
            api_key = decrypt(row.api_key_encrypted)
        finally:
            db.close()

        resp = httpx.post(
            f"{base}/Library/Refresh",
            headers={"X-Emby-Token": api_key},
            timeout=10.0,
        )
        log.info("Jellyfin library refresh triggered: HTTP %s", resp.status_code)
    except Exception as exc:
        log.warning("Jellyfin library refresh failed (non-fatal): %s", exc)
