"""
Library ID reconciliation — repairs the dependent tables after a Jellyfin
server migration.

When Jellyfin's host changes (or the server DB is rebuilt), every audio item
gets a brand-new Jellyfin item ID. The library scanner already handles this
gracefully on its own — old IDs are soft-deleted (missing_since=now) and the
new IDs are inserted as fresh LibraryTrack rows. The library effectively
doubles in size: 3,600 active rows + 3,600 missing rows.

The problem is that *every* dependent table (plays, scores, enrichments,
skip penalties, replay signals, cooldowns, billboard hits) still references
the old IDs. So:

  - Playback history points at IDs Jellyfin no longer recognises.
  - Track scores belong to ghost tracks — recommendations don't surface them.
  - Restoring a backup with id_only resolution writes IDs Jellyfin silently
    drops, producing an empty playlist.

This module performs a one-shot remap:

  1. Build (track_name, artist_name, album_name) → new_id from the active
     LibraryTrack rows.
  2. For each soft-deleted (missing) LibraryTrack row, look up its new ID
     by metadata.
  3. UPDATE every dependent table to swap old_id for new_id. For tables with
     a UNIQUE constraint on jellyfin_item_id (track_enrichments) the old row
     is DELETEd instead — enrichment for the new ID will be recreated by the
     next enrichment job.
  4. Delete the now-orphaned missing LibraryTrack rows.

A `dry_run` flag lets the caller inspect the planned remap before writing.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from models import LibraryTrack

log = logging.getLogger(__name__)


# Tables that hold a Jellyfin item ID pointing at LibraryTrack.
# (table_name, column_name, has_unique_on_column)
#
# The column is usually named jellyfin_item_id, but a few tables use a
# domain-specific name (e.g. imported_playlist_tracks.matched_item_id) and
# would be silently skipped if we only matched on the standard name —
# imported playlists then push to Jellyfin with stale IDs and no tracks
# appear.
DEPENDENT_TABLES: list[tuple[str, str, bool]] = [
    ("plays",                     "jellyfin_item_id", False),
    ("playback_events",           "jellyfin_item_id", False),
    ("skip_penalties",            "jellyfin_item_id", False),
    ("track_scores",              "jellyfin_item_id", False),
    ("track_enrichments",         "jellyfin_item_id", True),   # UNIQUE on jellyfin_item_id
    ("user_replay_signals",       "jellyfin_item_id", False),
    ("track_cooldowns",           "jellyfin_item_id", False),
    ("billboard_chart_entries",   "jellyfin_item_id", False),
    ("imported_playlist_tracks",  "matched_item_id",  False),
]


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def build_remap(db: Session) -> tuple[dict[str, str], list[dict], list[dict]]:
    """
    Build a mapping from old (missing) Jellyfin item ID → new Jellyfin item ID
    by matching (track_name, artist_name, album_name) case-insensitively.

    Returns:
      remap            — {old_id: new_id}
      remapped_details — list of {old_id, new_id, track_name, artist_name, album_name}
      orphans          — list of missing rows that could not be matched
                         (truly removed from the library, not just re-IDed)
    """
    # Active rows — current IDs
    active = (
        db.query(
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.track_name,
            LibraryTrack.artist_name,
            LibraryTrack.album_name,
        )
        .filter(LibraryTrack.missing_since.is_(None))
        .all()
    )

    # Build lookup: (name, artist, album) → new_id, with looser fallbacks
    by_name_artist_album: dict[tuple[str, str, str], str] = {}
    by_name_artist:       dict[tuple[str, str], str]      = {}
    by_name_album:        dict[tuple[str, str], str]      = {}
    by_name_only:         dict[str, str]                  = {}

    for jid, name, artist, album in active:
        if not jid:
            continue
        n, a, al = _norm(name), _norm(artist), _norm(album)
        if not n:
            continue
        by_name_artist_album.setdefault((n, a, al), jid)
        by_name_artist.setdefault((n, a), jid)
        by_name_album.setdefault((n, al), jid)
        by_name_only.setdefault(n, jid)

    # Missing rows — old IDs we'd like to remap
    missing = (
        db.query(
            LibraryTrack.id,
            LibraryTrack.jellyfin_item_id,
            LibraryTrack.track_name,
            LibraryTrack.artist_name,
            LibraryTrack.album_name,
        )
        .filter(LibraryTrack.missing_since.isnot(None))
        .all()
    )

    remap: dict[str, str] = {}
    remapped_details: list[dict] = []
    orphans: list[dict] = []

    for lib_id, jid, name, artist, album in missing:
        if not jid:
            continue
        n, a, al = _norm(name), _norm(artist), _norm(album)
        new_id = (
            by_name_artist_album.get((n, a, al))
            or by_name_artist.get((n, a))
            or by_name_album.get((n, al))
            or by_name_only.get(n)
        )
        if new_id and new_id != jid:
            remap[jid] = new_id
            remapped_details.append({
                "library_track_id": lib_id,
                "old_id": jid,
                "new_id": new_id,
                "track_name": name,
                "artist_name": artist,
                "album_name": album,
            })
        else:
            orphans.append({
                "library_track_id": lib_id,
                "old_id": jid,
                "track_name": name,
                "artist_name": artist,
                "album_name": album,
            })

    return remap, remapped_details, orphans


def apply_remap(db: Session, remap: dict[str, str]) -> dict:
    """
    Apply the (old_id → new_id) remap to every dependent table.

    For each row in DEPENDENT_TABLES:
      - If the table has a UNIQUE constraint on jellyfin_item_id, DELETE the
        old-id row (the new-id row already exists or will be recreated).
      - Otherwise UPDATE the old id to the new id.

    Returns per-table row counts of writes performed.
    """
    if not remap:
        return {"updated_by_table": {}, "deleted_by_table": {}}

    updated_by_table: dict[str, int] = {}
    deleted_by_table: dict[str, int] = {}

    # SQLite parameter limit is 999 (SQLITE_MAX_VARIABLE_NUMBER) — chunk to
    # stay well under that even when both old + new IDs are bound per row.
    items = list(remap.items())
    chunk_size = 200

    for table, column, has_unique in DEPENDENT_TABLES:
        u_total = 0
        d_total = 0
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            if has_unique:
                # Drop the stale row entirely; a fresh one for the new ID
                # will be regenerated by the next enrichment / scoring run.
                old_ids = [old for old, _ in chunk]
                placeholders = ", ".join(f":id{j}" for j in range(len(old_ids)))
                params = {f"id{j}": old for j, old in enumerate(old_ids)}
                res = db.execute(
                    text(f"DELETE FROM {table} WHERE {column} IN ({placeholders})"),
                    params,
                )
                d_total += res.rowcount or 0
            else:
                for old, new in chunk:
                    res = db.execute(
                        text(
                            f"UPDATE {table} SET {column} = :new "
                            f"WHERE {column} = :old"
                        ),
                        {"old": old, "new": new},
                    )
                    u_total += res.rowcount or 0
        if u_total:
            updated_by_table[table] = u_total
        if d_total:
            deleted_by_table[table] = d_total

    return {
        "updated_by_table": updated_by_table,
        "deleted_by_table": deleted_by_table,
    }


def reconcile(
    db: Session,
    dry_run: bool = True,
    delete_orphans: bool = False,
) -> dict:
    """
    Top-level reconciliation entry point.

    dry_run=True   → report the planned remap without writing anything.
    dry_run=False  → apply the remap to all dependent tables and delete
                     the soft-deleted LibraryTrack rows that were remapped.
    delete_orphans → if True, also hard-delete the missing LibraryTrack rows
                     that could NOT be matched (truly removed tracks).
                     Their dependent rows are left alone (they'll just refer
                     to nothing — the library scanner already considers them
                     gone).
    """
    active_count = (
        db.query(func.count(LibraryTrack.id))
        .filter(LibraryTrack.missing_since.is_(None))
        .scalar() or 0
    )
    missing_count = (
        db.query(func.count(LibraryTrack.id))
        .filter(LibraryTrack.missing_since.isnot(None))
        .scalar() or 0
    )

    remap, remapped_details, orphans = build_remap(db)

    summary = {
        "dry_run": dry_run,
        "library_active_before": active_count,
        "library_missing_before": missing_count,
        "remap_count": len(remap),
        "orphan_count": len(orphans),
        "delete_orphans": delete_orphans,
        # Sample for the UI — full lists can be huge
        "remap_sample": remapped_details[:25],
        "orphan_sample": orphans[:25],
    }

    if dry_run:
        return summary

    write_stats = apply_remap(db, remap)
    summary.update(write_stats)

    # Delete the now-orphaned missing LibraryTrack rows that we successfully
    # remapped. Their old_id no longer appears anywhere in dependent tables.
    remapped_lib_ids = [d["library_track_id"] for d in remapped_details]
    deleted_library_rows = 0
    chunk_size = 500
    for i in range(0, len(remapped_lib_ids), chunk_size):
        chunk = remapped_lib_ids[i : i + chunk_size]
        deleted_library_rows += (
            db.query(LibraryTrack)
            .filter(LibraryTrack.id.in_(chunk))
            .delete(synchronize_session=False)
        )

    if delete_orphans and orphans:
        orphan_ids = [o["library_track_id"] for o in orphans]
        for i in range(0, len(orphan_ids), chunk_size):
            chunk = orphan_ids[i : i + chunk_size]
            deleted_library_rows += (
                db.query(LibraryTrack)
                .filter(LibraryTrack.id.in_(chunk))
                .delete(synchronize_session=False)
            )

    db.commit()

    summary["deleted_library_rows"] = deleted_library_rows
    summary["library_active_after"] = (
        db.query(func.count(LibraryTrack.id))
        .filter(LibraryTrack.missing_since.is_(None))
        .scalar() or 0
    )
    summary["library_missing_after"] = (
        db.query(func.count(LibraryTrack.id))
        .filter(LibraryTrack.missing_since.isnot(None))
        .scalar() or 0
    )

    log.info(
        "Library reconcile applied: remapped=%d, orphans=%d (deleted=%s), "
        "library rows deleted=%d, table writes=%s",
        len(remap), len(orphans), delete_orphans,
        deleted_library_rows, write_stats,
    )
    return summary
