"""
JellyDJ Library Scanner — Module 8a

Scans the full Jellyfin audio library (played AND unplayed) into LibraryTrack.
This is the foundation layer — every other system queries LibraryTrack as the
source of truth for "what music do we have".

Key differences from the play history indexer:
  - No user_id — library tracks are global
  - Fetches ALL audio items, not just played ones
  - Soft-deletes items no longer in Jellyfin (missing_since)
  - Batches in 500s for scalability
  - Runs independently of per-user indexing but is triggered alongside it
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from database import SessionLocal
from models import ConnectionSettings, LibraryTrack
from crypto import decrypt

log = logging.getLogger(__name__)

BATCH_SIZE = 500

# Brief pause between Jellyfin page requests to avoid saturating it while
# playback is happening.  At 500 items/page a 10,000-track library takes
# ~20 pages; 0.25 s/page adds only ~5 seconds total but gives Jellyfin's
# HTTP thread pool time to breathe between bursts.
_PAGE_SLEEP_SECS = 0.25


def _get_jellyfin_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


async def _fetch_all_audio_items(base_url: str, api_key: str) -> list[dict]:
    """
    Fetch every audio item from Jellyfin regardless of played status.
    Pages through results in batches of BATCH_SIZE.
    """
    headers = {"X-Emby-Token": api_key}
    all_items: list[dict] = []
    start_index = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            params = {
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "Fields": "DateCreated,Genres,Album,AlbumArtist,Artists,"
                          "IndexNumber,ParentIndexNumber,RunTimeTicks,ProductionYear,AlbumId",
                "StartIndex": start_index,
                "Limit": BATCH_SIZE,
                "SortBy": "AlbumArtist,Album,IndexNumber",
                "SortOrder": "Ascending",
            }
            try:
                resp = await client.get(
                    f"{base_url}/Items",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.error(f"Library scan fetch error at offset {start_index}: {e}")
                break

            items = data.get("Items", [])
            all_items.extend(items)
            total = data.get("TotalRecordCount", 0)
            start_index += BATCH_SIZE

            log.debug(f"  Library scan: fetched {len(all_items)}/{total}")

            if start_index >= total or not items:
                break

            # Yield to Jellyfin between pages so playback requests aren't
            # starved.  Small libraries (≤1 page) never hit this.
            await asyncio.sleep(_PAGE_SLEEP_SECS)

    return all_items


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.rstrip("Z"))
    except Exception:
        return None


def _extract_genre(item: dict) -> str:
    genres = item.get("Genres", [])
    return genres[0] if genres else ""


_VARIOUS_ARTISTS = {
    "various artists", "various", "va", "v.a.", "v/a",
    "multiple artists", "assorted artists", "unknown artist", "unknown",
}

def _extract_artist(item: dict) -> str:
    """
    Return the best artist name for a track.
    Prefers AlbumArtist unless it's a compilation catch-all (e.g. "Various Artists"),
    in which case falls back to the track-specific Artists[0].
    """
    album_artist = (item.get("AlbumArtist") or "").strip()
    track_artists = item.get("Artists") or []

    if album_artist and album_artist.lower() not in _VARIOUS_ARTISTS:
        return album_artist

    # AlbumArtist is a catch-all — use the track-level artist instead
    real = [a for a in track_artists if a.strip().lower() not in _VARIOUS_ARTISTS]
    if real:
        return real[0]
    return track_artists[0] if track_artists else album_artist


def scan_library(db: Session, items: list[dict]) -> dict:
    """
    Upsert all Jellyfin items into LibraryTrack.
    Soft-deletes anything no longer present in Jellyfin.
    Returns stats dict.
    """
    now = datetime.utcnow()
    seen_ids: set[str] = set()
    added = 0
    updated = 0

    # Build lookup of existing tracks
    existing: dict[str, LibraryTrack] = {
        row.jellyfin_item_id: row
        for row in db.query(LibraryTrack).all()
    }

    for item in items:
        jid = item.get("Id")
        if not jid:
            continue
        seen_ids.add(jid)

        artist = _extract_artist(item)
        album = item.get("Album", "")
        track_name = item.get("Name", "")
        genre = _extract_genre(item)
        album_artist = item.get("AlbumArtist", "")

        if jid in existing:
            row = existing[jid]
            # Update mutable fields — metadata can change in Jellyfin
            row.track_name = track_name
            row.artist_name = artist
            row.album_name = album
            row.album_artist = album_artist
            row.genre = genre
            row.duration_ticks = item.get("RunTimeTicks")
            row.track_number = item.get("IndexNumber")
            row.disc_number = item.get("ParentIndexNumber")
            row.year = item.get("ProductionYear")
            row.last_seen = now
            row.missing_since = None   # clear any soft-delete
            row.jellyfin_album_id = item.get("AlbumId") or None
            updated += 1
        else:
            db.add(LibraryTrack(
                jellyfin_item_id=jid,
                track_name=track_name,
                artist_name=artist,
                album_name=album,
                album_artist=album_artist,
                genre=genre,
                duration_ticks=item.get("RunTimeTicks"),
                track_number=item.get("IndexNumber"),
                disc_number=item.get("ParentIndexNumber"),
                year=item.get("ProductionYear"),
                date_added=_parse_date(item.get("DateCreated")),
                first_seen=now,
                last_seen=now,
                missing_since=None,
                jellyfin_album_id=item.get("AlbumId") or None,
            ))
            added += 1

    # Soft-delete anything not seen in this scan
    missing_count = 0
    for jid, row in existing.items():
        if jid not in seen_ids and row.missing_since is None:
            row.missing_since = now
            missing_count += 1

    db.commit()

    stats = {
        "total_in_jellyfin": len(items),
        "added": added,
        "updated": updated,
        "soft_deleted": missing_count,
        "total_in_db": db.query(LibraryTrack).filter(
            LibraryTrack.missing_since.is_(None)
        ).count(),
    }
    log.info(
        f"  Library scan complete: {stats['total_in_db']} tracks "
        f"(+{added} new, {updated} updated, {missing_count} missing)"
    )

    # v4: stamp holiday tags on every active library track.
    # Run AFTER the db.commit() above so all new/updated LibraryTrack rows
    # are visible. This must succeed — a silent failure here leaves
    # holiday_exclude=NULL on LibraryTrack, which scoring_engine will read
    # as False, causing excluded holiday tracks to slip into playlists.
    from services.holiday import tag_library
    holiday_stats = tag_library(db)
    stats["holiday_tagged"]    = holiday_stats["tagged"]
    stats["holiday_breakdown"] = holiday_stats["breakdown"]
    log.info(f"  Holiday tagger: {holiday_stats['tagged']} holiday tracks tagged")

    return stats


async def run_library_scan(db: Optional[Session] = None) -> dict:
    """
    Entry point for a full library scan.
    Can be called with an existing session or will create its own.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        base_url, api_key = _get_jellyfin_creds(db)
        log.info("Starting full library scan...")
        items = await _fetch_all_audio_items(base_url, api_key)
        log.info(f"  Fetched {len(items)} audio items from Jellyfin")
        stats = scan_library(db, items)
        return {"ok": True, **stats}
    except Exception as e:
        log.error(f"Library scan failed: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        if own_session:
            db.close()


def get_library_stats(db: Session) -> dict:
    """Quick stats for the dashboard."""
    total = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).count()
    missing = db.query(LibraryTrack).filter(LibraryTrack.missing_since.isnot(None)).count()

    # Artist + album counts
    from sqlalchemy import func
    artist_count = db.query(
        func.count(func.distinct(LibraryTrack.artist_name))
    ).filter(LibraryTrack.missing_since.is_(None)).scalar() or 0

    album_count = db.query(
        func.count(func.distinct(LibraryTrack.album_name))
    ).filter(LibraryTrack.missing_since.is_(None)).scalar() or 0

    last_scan = db.query(func.max(LibraryTrack.last_seen)).scalar()

    return {
        "total_tracks": total,
        "missing_tracks": missing,
        "total_artists": artist_count,
        "total_albums": album_count,
        "last_scan": last_scan,
    }