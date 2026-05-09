"""
Tests for services/library_reconcile.py — post-Jellyfin-migration ID remap.

Scenario being modelled: a Jellyfin server migration mints brand-new item IDs
for every track. The library scanner soft-deletes the old IDs and inserts the
new ones, leaving every dependent table (plays, scores, enrichments, etc.)
referencing IDs Jellyfin no longer recognises.

A bug in this module would:
  - Remap the wrong tracks together (mismatched name/artist/album lookup)
  - Lose play history (UPDATE silently skipped)
  - Crash on UNIQUE collisions in track_enrichments
  - Leave LibraryTrack permanently doubled in size

Run with: docker exec jellydj-backend python -m pytest tests/test_library_reconcile.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # registers all ORM models with Base.metadata
from models import (
    LibraryTrack, Play, TrackEnrichment, TrackScore,
    SkipPenalty, PlaylistBackupTrack, ImportedPlaylistTrack,
)
from services.library_reconcile import build_remap, reconcile


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _add_track(db, jid, name, artist, album="Album X", missing=False):
    db.add(LibraryTrack(
        jellyfin_item_id=jid,
        track_name=name, artist_name=artist, album_name=album,
        missing_since=datetime.utcnow() if missing else None,
    ))


def _seed_migration(db):
    """Old IDs old1/old2/old3 (missing) → new IDs new1/new2/new3 (active)."""
    # Old, soft-deleted
    _add_track(db, "old1", "Song One", "Artist A", missing=True)
    _add_track(db, "old2", "Song Two", "Artist B", missing=True)
    _add_track(db, "old3", "Song Three", "Artist C", missing=True)
    _add_track(db, "old4", "Removed Song", "Artist D", missing=True)  # truly gone
    # New, active — same metadata except old4 is absent
    _add_track(db, "new1", "Song One", "Artist A")
    _add_track(db, "new2", "Song Two", "Artist B")
    _add_track(db, "new3", "Song Three", "Artist C")
    db.commit()


def test_build_remap_matches_by_metadata(db):
    _seed_migration(db)
    remap, details, orphans = build_remap(db)
    assert remap == {"old1": "new1", "old2": "new2", "old3": "new3"}
    assert len(details) == 3
    assert {o["old_id"] for o in orphans} == {"old4"}


def test_build_remap_is_case_insensitive(db):
    _add_track(db, "old", "Hello WORLD", "ARTIST", missing=True)
    _add_track(db, "new", "hello world", "artist")
    db.commit()
    remap, _, _ = build_remap(db)
    assert remap == {"old": "new"}


def test_dry_run_makes_no_writes(db):
    _seed_migration(db)
    db.add(Play(user_id="u1", jellyfin_item_id="old1", play_count=3, last_played=datetime.utcnow()))
    db.commit()
    summary = reconcile(db, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["remap_count"] == 3
    assert summary["orphan_count"] == 1
    # Play row untouched
    assert db.query(Play).filter_by(jellyfin_item_id="old1").count() == 1
    # Both old + new LibraryTracks still present
    assert db.query(LibraryTrack).count() == 7


def test_apply_remaps_dependent_tables(db):
    _seed_migration(db)
    db.add(Play(user_id="u1", jellyfin_item_id="old1", play_count=2, last_played=datetime.utcnow()))
    db.add(Play(user_id="u1", jellyfin_item_id="old2", play_count=5, last_played=datetime.utcnow()))
    db.add(SkipPenalty(user_id="u1", jellyfin_item_id="old1"))
    db.add(TrackScore(user_id="u1", jellyfin_item_id="old3", final_score="42.0"))
    db.commit()

    summary = reconcile(db, dry_run=False)

    assert summary["remap_count"] == 3
    assert db.query(Play).filter_by(jellyfin_item_id="new1").count() == 1
    assert db.query(Play).filter_by(jellyfin_item_id="new2").count() == 1
    assert db.query(Play).filter_by(jellyfin_item_id="old1").count() == 0
    assert db.query(SkipPenalty).filter_by(jellyfin_item_id="new1").count() == 1
    assert db.query(TrackScore).filter_by(jellyfin_item_id="new3").count() == 1


def test_remapped_library_rows_are_deleted(db):
    _seed_migration(db)
    summary = reconcile(db, dry_run=False)

    # Active rows preserved; remapped missing rows removed; unremapped orphan kept
    remaining_ids = {r.jellyfin_item_id for r in db.query(LibraryTrack).all()}
    assert remaining_ids == {"new1", "new2", "new3", "old4"}
    assert summary["library_active_after"] == 3
    assert summary["library_missing_after"] == 1


def test_delete_orphans_clears_unmatched_missing_rows(db):
    _seed_migration(db)
    summary = reconcile(db, dry_run=False, delete_orphans=True)

    remaining_ids = {r.jellyfin_item_id for r in db.query(LibraryTrack).all()}
    assert remaining_ids == {"new1", "new2", "new3"}
    assert summary["library_missing_after"] == 0


def test_unique_constraint_table_uses_delete_not_update(db):
    """track_enrichments has UNIQUE(jellyfin_item_id) — old row must be DELETEd
    so it doesn't collide with whatever the next enrichment run will write
    against the new ID."""
    _seed_migration(db)
    # An enrichment row exists for the OLD ID (left over from before the migration)
    db.add(TrackEnrichment(jellyfin_item_id="old1", tags='["rock"]'))
    db.commit()

    reconcile(db, dry_run=False)

    # Old enrichment row deleted; nothing crashed, no collision.
    assert db.query(TrackEnrichment).filter_by(jellyfin_item_id="old1").count() == 0


def test_playlist_backup_tracks_are_not_remapped(db):
    """Backup snapshots are point-in-time records — they MUST NOT be rewritten,
    or the user loses the audit trail of what the playlist looked like before."""
    _seed_migration(db)
    db.add(PlaylistBackupTrack(
        revision_id=1, backup_id=1, position=0,
        jellyfin_item_id="old1", track_name="Song One",
        artist_name="Artist A", album_name="Album X",
    ))
    db.commit()

    reconcile(db, dry_run=False)

    # Backup snapshot still references the old ID — preserved as historical record.
    assert db.query(PlaylistBackupTrack).filter_by(jellyfin_item_id="old1").count() == 1


def test_imported_playlist_tracks_remap_via_matched_item_id(db):
    """ImportedPlaylistTrack uses the column name `matched_item_id`, not
    `jellyfin_item_id`. Without explicit handling, reconcile would silently
    skip it and imported playlists would push stale IDs to Jellyfin (which
    silently drops them, producing an empty playlist on the Jellyfin side)."""
    _seed_migration(db)
    db.add(ImportedPlaylistTrack(
        playlist_id=1, position=0,
        track_name="Song One", artist_name="Artist A", album_name="Album X",
        match_status="matched", matched_item_id="old1",
    ))
    db.add(ImportedPlaylistTrack(
        playlist_id=1, position=1,
        track_name="Song Two", artist_name="Artist B", album_name="Album X",
        match_status="matched", matched_item_id="old2",
    ))
    db.commit()

    reconcile(db, dry_run=False)

    assert db.query(ImportedPlaylistTrack).filter_by(matched_item_id="new1").count() == 1
    assert db.query(ImportedPlaylistTrack).filter_by(matched_item_id="new2").count() == 1
    assert db.query(ImportedPlaylistTrack).filter_by(matched_item_id="old1").count() == 0
    assert db.query(ImportedPlaylistTrack).filter_by(matched_item_id="old2").count() == 0


def test_idempotent_when_no_missing_rows(db):
    _add_track(db, "x1", "A", "B")
    db.commit()
    summary = reconcile(db, dry_run=False)
    assert summary["remap_count"] == 0
    assert summary["orphan_count"] == 0
    assert db.query(LibraryTrack).count() == 1
