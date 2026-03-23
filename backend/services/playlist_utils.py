"""
JellyDJ — Playlist utilities shared across playlist_writer.py and the new
block engine (playlist_blocks.py / playlist_engine.py).

We extract _get_excluded_item_ids and _holiday_ok from playlist_writer.py
here ONLY if circular-import resolution requires it.  Both files can then
import from this module instead of from each other.

Because the spec says "modify playlist_writer.py only if needed to resolve
circular imports", and playlist_engine.py needs to import these helpers while
playlist_writer.py also defines them, we move them here and re-export them
from this module so the existing playlist_writer.py can optionally delegate
to us without a source change (it keeps its own internal copies which are
functionally identical).

The block engine imports exclusively from here.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_ as _sa_and_

log = logging.getLogger(__name__)


def get_excluded_item_ids(db: Session) -> frozenset:
    """
    Return a frozenset of jellyfin_item_ids whose album has been manually excluded.

    Matching strategy (two passes, unioned):

    Pass 1 — jellyfin_album_id (exact, reliable):
      LibraryTrack.jellyfin_album_id == ExcludedAlbum.jellyfin_album_id
    Pass 2 — album_name LOWER() match (fallback for pre-v5 rows).

    Mirrors _get_excluded_item_ids() in playlist_writer.py exactly.
    """
    try:
        from models import ExcludedAlbum, LibraryTrack, TrackScore
        from sqlalchemy import func as _func

        excl_rows = db.query(ExcludedAlbum).all()
        if not excl_rows:
            return frozenset()

        excl_album_ids  = [r.jellyfin_album_id for r in excl_rows if r.jellyfin_album_id]
        excl_names_lower = [r.album_name.lower() for r in excl_rows if r.album_name]

        result: set[str] = set()

        if excl_album_ids:
            id_rows = db.query(LibraryTrack.jellyfin_item_id).filter(
                LibraryTrack.jellyfin_album_id.in_(excl_album_ids),
                LibraryTrack.missing_since.is_(None),
            ).all()
            for r in id_rows:
                result.add(r.jellyfin_item_id)

        if excl_names_lower:
            name_rows = db.query(LibraryTrack.jellyfin_item_id).filter(
                _func.lower(LibraryTrack.album_name).in_(excl_names_lower),
                LibraryTrack.missing_since.is_(None),
            ).all()
            for r in name_rows:
                result.add(r.jellyfin_item_id)

            ts_rows = db.query(TrackScore.jellyfin_item_id).filter(
                _func.lower(TrackScore.album_name).in_(excl_names_lower),
            ).all()
            for r in ts_rows:
                result.add(r.jellyfin_item_id)

        frozen = frozenset(result)
        log.debug(
            f"Excluded album filter: {len(excl_rows)} excluded album(s) → "
            f"{len(frozen)} track IDs blocked"
        )
        return frozen
    except Exception as _e:
        log.warning(f"Failed to load excluded album item IDs: {_e}")
        return frozenset()


def get_artist_cooled_down_ids(db: Session, user_id: str) -> frozenset:
    """
    Return a frozenset of jellyfin_item_ids whose artist has an active
    artist-level cooldown for this user.

    Called once per playlist generation so that all block executors share the
    same pre-computed exclusion set, avoiding repeated queries per block.
    """
    try:
        from models import ArtistCooldown, LibraryTrack
        from datetime import datetime as _dt

        now = _dt.utcnow()
        cooled = (
            db.query(ArtistCooldown.artist_name)
            .filter(
                ArtistCooldown.user_id == user_id,
                ArtistCooldown.status == "active",
                ArtistCooldown.cooldown_until > now,
            )
            .all()
        )
        if not cooled:
            return frozenset()

        artist_names = [r.artist_name for r in cooled]
        rows = (
            db.query(LibraryTrack.jellyfin_item_id)
            .filter(
                LibraryTrack.artist_name.in_(artist_names),
                LibraryTrack.missing_since.is_(None),
            )
            .all()
        )
        frozen = frozenset(r.jellyfin_item_id for r in rows)
        log.debug(
            f"Artist cooldown filter: {len(artist_names)} artist(s) on timeout → "
            f"{len(frozen)} track IDs blocked for user {user_id[:8]}"
        )
        return frozen
    except Exception as _e:
        log.warning(f"Failed to load artist-cooldown item IDs: {_e}")
        return frozenset()


def get_holiday_excluded_ids(db: Session) -> frozenset:
    """
    Return the frozenset of jellyfin_item_ids that are currently out-of-season
    holiday content.  We query LibraryTrack directly (not the denormalised
    TrackScore.holiday_exclude field) so that holiday.refresh_exclude_flags()
    changes are immediately visible.

    Mirrors the subquery inside _holiday_ok() in playlist_writer.py, but
    returns a Python frozenset so block executors can apply it as a post-filter
    alongside the album-excluded frozenset — no SQLAlchemy expression needed.
    """
    try:
        from models import LibraryTrack

        rows = db.query(LibraryTrack.jellyfin_item_id).filter(
            LibraryTrack.holiday_tag.isnot(None),
            LibraryTrack.holiday_exclude == True,  # noqa: E712
        ).all()
        return frozenset(r.jellyfin_item_id for r in rows)
    except Exception as _e:
        log.warning(f"Failed to load holiday-excluded IDs: {_e}")
        return frozenset()