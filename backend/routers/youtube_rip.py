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
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required to rip YouTube audio")

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
        args=(job_id, clean_url),
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

def _run_rip(job_id: str, url: str) -> None:
    job = _jobs[job_id]

    try:
        _run_rip_inner(job_id, url, job)
    finally:
        # Always release the concurrency slot and deregister the URL so future
        # requests for the same video are accepted again.
        _rip_semaphore.release()
        with _active_urls_lock:
            _active_urls.pop(url, None)


def _run_rip_inner(job_id: str, url: str, job: dict) -> None:
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

    # Resolve artist: YouTube Music videos may carry an 'artist' tag; regular
    # videos fall back to the uploader / channel name.
    raw_artist = (
        info.get("artist")
        or info.get("uploader")
        or info.get("channel")
        or "Unknown Artist"
    )
    raw_title = info.get("title") or "Unknown Title"

    artist = _safe_name(raw_artist)
    title  = _safe_name(_clean_title(raw_title))

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
        # Only after a successful encode do we touch the bind mount, so Jellyfin
        # will never scan an incomplete file.
        artist_dir = _CONTAINER_RIP_DIR / artist
        try:
            artist_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            job["status"] = "error"
            job["error"] = f"Cannot create output directory {artist_dir}: {exc}"
            return

        final_mp3 = artist_dir / tmp_mp3.name
        shutil.move(str(tmp_mp3), str(final_mp3))

    finally:
        # Always clean up the temp directory, even on error paths.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    job["path"] = str(final_mp3)

    job["status"] = "done"
    log.info("YouTube rip complete: %s / %s → %s", artist, title, final_mp3)


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
