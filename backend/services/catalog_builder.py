"""
Album catalog builder — pre-computes a canonical album→track mapping.

Grouping strategy:
  PRIMARY: jellyfin_album_id — Jellyfin assigns one ID per folder/album container.
           This is the ground truth for album membership regardless of how messy
           individual track metadata is (different album_name, artist, etc.).
  FALLBACK: tracks with no jellyfin_album_id are grouped by normalised
            (album_artist_or_artist, album_name).

Display name for each group = most common album_name among its tracks.
Display artist = most common album_artist (or artist_name if album_artist absent).

The mobile app checks GET /api/mobile/catalog/version on startup and only
re-downloads the full catalog when the version changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import AlbumCatalogEntry, CatalogVersion, LibraryTrack, TrackScore

log = logging.getLogger(__name__)


def _normalise(s: str) -> str:
    from services.library_dedup import _normalise as _norm
    return _norm(s)


def build_catalog_hash(db: Session) -> str:
    """SHA-256 of every non-missing track's (item_id, jellyfin_album_id, album_name)."""
    rows = (
        db.query(
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.jellyfin_album_id,
            LibraryTrack.album_name,
        )
        .filter(LibraryTrack.missing_since.is_(None))
        .order_by(LibraryTrack.jellyfin_item_id)
        .all()
    )
    h = hashlib.sha256()
    for item_id, album_id, album_name in rows:
        h.update(f"{item_id}|{album_id or ''}|{album_name or ''}".encode())
    return h.hexdigest()


def build_catalog(db: Session) -> None:
    """
    Rebuild all AlbumCatalogEntry rows then bump CatalogVersion.

    One entry per jellyfin_album_id (= one Jellyfin folder = one album).
    Track metadata inconsistencies within the folder are ignored for grouping;
    the most-common album_name / album_artist is used for display.
    """
    now = datetime.now(timezone.utc)

    tracks = (
        db.query(
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.artist_name,
            LibraryTrack.album_name,
            LibraryTrack.album_artist,
            LibraryTrack.jellyfin_album_id,
        )
        .filter(LibraryTrack.missing_since.is_(None))
        .all()
    )

    # Global popularity (user-agnostic average across all users)
    pop_sum: dict[str, list[float]] = defaultdict(list)
    for item_id, pop in db.query(
        TrackScore.jellyfin_item_id, TrackScore.global_popularity
    ).filter(TrackScore.global_popularity.isnot(None)).all():
        if item_id and pop is not None:
            pop_sum[item_id].append(pop)
    popularity_map: dict[str, float] = {k: sum(v) / len(v) for k, v in pop_sum.items()}

    # ── Group tracks ──────────────────────────────────────────────────────────
    # key → accumulator dict
    groups: dict[str, dict] = {}

    for row in tracks:
        item_id = row.jellyfin_item_id
        if not item_id:
            continue

        if row.jellyfin_album_id:
            # PRIMARY: group by Jellyfin's own folder/album ID
            key = f"jid::{row.jellyfin_album_id}"
        else:
            # FALLBACK: no album ID — use canonical (artist, album_name)
            artist = (row.album_artist or row.artist_name or "").strip()
            album  = (row.album_name or "").strip()
            norm_artist = _normalise(artist)
            norm_album  = _normalise(album) or "__unknown__"
            key = f"name::{norm_artist}::{norm_album}"

        if key not in groups:
            groups[key] = {
                "jellyfin_album_id": row.jellyfin_album_id,  # None for fallback entries
                "album_name_counter":   Counter(),
                "artist_counter":       Counter(),
                "track_ids":            [],
                "popularities":         [],
            }

        g = groups[key]
        if row.album_name:
            g["album_name_counter"][row.album_name] += 1
        # Prefer album_artist; fall back to artist_name
        display_artist = (row.album_artist or row.artist_name or "").strip()
        if display_artist:
            g["artist_counter"][display_artist] += 1
        g["track_ids"].append(item_id)
        if item_id in popularity_map:
            g["popularities"].append(popularity_map[item_id])

    # ── Write entries ─────────────────────────────────────────────────────────
    db.query(AlbumCatalogEntry).delete(synchronize_session=False)

    entries = []
    for key, g in groups.items():
        display_album = (
            g["album_name_counter"].most_common(1)[0][0]
            if g["album_name_counter"] else ""
        )
        display_artist = (
            g["artist_counter"].most_common(1)[0][0]
            if g["artist_counter"] else ""
        )
        avg_pop = (
            sum(g["popularities"]) / len(g["popularities"])
            if g["popularities"] else None
        )
        jid = g["jellyfin_album_id"]
        entries.append(AlbumCatalogEntry(
            canonical_key=key,
            display_album=display_album,
            display_artist=display_artist,
            jellyfin_album_ids=json.dumps([jid] if jid else []),
            track_ids=json.dumps(g["track_ids"]),
            track_count=len(g["track_ids"]),
            avg_popularity=avg_pop,
            updated_at=now,
        ))

    db.bulk_save_objects(entries)

    new_hash = build_catalog_hash(db)
    version_row = db.query(CatalogVersion).filter_by(id=1).first()
    if version_row is None:
        version_row = CatalogVersion(
            id=1, version=1, content_hash=new_hash,
            updated_at=now, total_albums=len(entries), total_tracks=len(tracks),
        )
        db.add(version_row)
    else:
        version_row.version += 1
        version_row.content_hash = new_hash
        version_row.updated_at = now
        version_row.total_albums = len(entries)
        version_row.total_tracks = len(tracks)

    db.commit()
    log.info(
        "Catalog rebuilt: %d albums, %d tracks, version=%d",
        len(entries), len(tracks), version_row.version,
    )


def check_and_rebuild_catalog(db: Session) -> bool:
    """Hash the library; rebuild and return True if changed, False if unchanged."""
    try:
        current_hash = build_catalog_hash(db)
        version_row = db.query(CatalogVersion).filter_by(id=1).first()
        if current_hash == (version_row.content_hash if version_row else None):
            log.debug("Catalog hash unchanged — skipping rebuild")
            return False
        build_catalog(db)
        return True
    except Exception as exc:
        log.warning("Catalog rebuild failed (non-fatal): %s", exc)
        return False
