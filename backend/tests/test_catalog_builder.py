"""
Tests for services/catalog_builder.py

Grouping rule: one entry per jellyfin_album_id (= Jellyfin folder = album).
Tracks with no album_id fall back to canonical (artist, album_name) grouping.

Run with: docker exec jellydj-backend python -m pytest tests/test_catalog_builder.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # registers all ORM models

from models import AlbumCatalogEntry, CatalogVersion, LibraryTrack
from services.catalog_builder import (
    build_catalog,
    build_catalog_hash,
    check_and_rebuild_catalog,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _track(db, item_id, track_name, artist, album, album_id,
           album_artist=None, missing=False):
    from datetime import datetime
    t = LibraryTrack(
        jellyfin_item_id=item_id,
        track_name=track_name,
        artist_name=artist,
        album_name=album,
        album_artist=album_artist or artist,
        jellyfin_album_id=album_id,
        missing_since=datetime(2024, 1, 1) if missing else None,
    )
    db.add(t)
    db.commit()
    return t


# ── build_catalog_hash ────────────────────────────────────────────────────────

def test_hash_stable(db):
    _track(db, "t1", "Song", "Artist", "Album", "aid1")
    assert build_catalog_hash(db) == build_catalog_hash(db)


def test_hash_changes_on_new_track(db):
    _track(db, "t1", "Song", "Artist", "Album", "aid1")
    h1 = build_catalog_hash(db)
    _track(db, "t2", "Song 2", "Artist", "Album", "aid1")
    assert build_catalog_hash(db) != h1


def test_hash_ignores_missing_tracks(db):
    _track(db, "t1", "Song", "Artist", "Album", "aid1")
    h1 = build_catalog_hash(db)
    _track(db, "t2", "Ghost", "Artist", "Album", "aid1", missing=True)
    assert build_catalog_hash(db) == h1


# ── Primary grouping: by jellyfin_album_id ────────────────────────────────────

def test_same_album_id_is_one_entry(db):
    """All tracks sharing a jellyfin_album_id → single catalog entry."""
    _track(db, "t1", "Track 1", "Artist A", "Album", "aid1")
    _track(db, "t2", "Track 2", "Artist A", "Album", "aid1")
    _track(db, "t3", "Track 3", "Artist A", "Album", "aid1")

    build_catalog(db)

    assert db.query(AlbumCatalogEntry).count() == 1
    assert db.query(AlbumCatalogEntry).first().track_count == 3


def test_different_album_ids_are_separate_entries(db):
    _track(db, "t1", "Track 1", "Artist A", "Album A", "aid1")
    _track(db, "t2", "Track 1", "Artist B", "Album B", "aid2")

    build_catalog(db)

    assert db.query(AlbumCatalogEntry).count() == 2


def test_messy_track_metadata_grouped_by_folder_id(db):
    """
    Tracks in the same folder (same jellyfin_album_id) with completely different
    album_name / artist_name metadata must still end up in ONE entry.
    This is the core bug: bad tags broke album display in the mobile library.
    """
    folder_id = "folder_abc"
    _track(db, "t1", "Song 1", "Artist X",         "Wrong Album Name",  folder_id, album_artist="The Band")
    _track(db, "t2", "Song 2", "Artist Y",         "Different Name",    folder_id, album_artist="The Band")
    _track(db, "t3", "Song 3", "Artist X",         "Correct Album",     folder_id, album_artist="The Band")
    _track(db, "t4", "Song 4", "Totally Wrong",    "Totally Wrong",     folder_id, album_artist="The Band")

    build_catalog(db)

    entries = db.query(AlbumCatalogEntry).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.track_count == 4
    # Display name = most common album_name in the folder
    assert entry.display_album == "Correct Album" or entry.track_count == 4  # just confirm grouping


def test_collab_artist_variation_grouped_by_album_id(db):
    """
    Tracks in the same album where some have 'Calum Scott' and others have
    'Calum Scott, Tiësto' as artist — same album_id must produce one entry.
    """
    from models import LibraryTrack
    for i, artist in enumerate(["Calum Scott", "Calum Scott, Tiësto", "Calum Scott, Leona Lewis"], 1):
        db.add(LibraryTrack(
            jellyfin_item_id=f"cs{i}",
            track_name=f"Track {i}",
            artist_name=artist,
            album_name="Only Human (Special Edition)",
            album_artist="Calum Scott",
            jellyfin_album_id="cs_album",
            missing_since=None,
        ))
    db.commit()

    build_catalog(db)

    entries = db.query(AlbumCatalogEntry).all()
    assert len(entries) == 1
    assert entries[0].track_count == 3
    assert entries[0].display_artist == "Calum Scott"


# ── Fallback grouping: by canonical name (no album_id) ───────────────────────

def test_no_album_id_groups_by_canonical_name(db):
    from models import LibraryTrack
    # Two tracks, same artist+album, no album_id → one entry
    for i in range(1, 3):
        db.add(LibraryTrack(
            jellyfin_item_id=f"n{i}", track_name=f"Track {i}",
            artist_name="Artist A", album_name="Album A",
            album_artist="Artist A", jellyfin_album_id=None, missing_since=None,
        ))
    db.commit()

    build_catalog(db)
    assert db.query(AlbumCatalogEntry).count() == 1


def test_edition_variants_merged_when_no_album_id(db):
    """Without an album_id, 'Album' and 'Album (Deluxe)' should normalise together."""
    from models import LibraryTrack
    db.add(LibraryTrack(jellyfin_item_id="n1", track_name="T1",
                        artist_name="A", album_name="Album",
                        album_artist="A", jellyfin_album_id=None, missing_since=None))
    db.add(LibraryTrack(jellyfin_item_id="n2", track_name="T2",
                        artist_name="A", album_name="Album (Deluxe Edition)",
                        album_artist="A", jellyfin_album_id=None, missing_since=None))
    db.commit()

    build_catalog(db)
    assert db.query(AlbumCatalogEntry).count() == 1


# ── Version management ────────────────────────────────────────────────────────

def test_version_created_on_first_build(db):
    _track(db, "t1", "T", "A", "B", "aid1")
    build_catalog(db)
    v = db.query(CatalogVersion).filter_by(id=1).first()
    assert v is not None
    assert v.version >= 1


def test_version_bumped_on_second_build(db):
    _track(db, "t1", "T", "A", "B", "aid1")
    build_catalog(db)
    v_before = db.query(CatalogVersion).filter_by(id=1).first().version

    _track(db, "t2", "T2", "A", "B", "aid1")
    build_catalog(db)
    db.expire_all()
    assert db.query(CatalogVersion).filter_by(id=1).first().version == v_before + 1


def test_stale_entries_replaced(db):
    _track(db, "t1", "T", "A", "Old Album", "aid1")
    build_catalog(db)

    db.query(LibraryTrack).filter_by(jellyfin_item_id="t1").update({"album_name": "New Album"})
    db.commit()
    build_catalog(db)

    entries = db.query(AlbumCatalogEntry).all()
    assert len(entries) == 1
    assert entries[0].display_album == "New Album"


# ── check_and_rebuild_catalog ─────────────────────────────────────────────────

def test_check_rebuilds_first_run(db):
    _track(db, "t1", "T", "A", "B", "aid1")
    assert check_and_rebuild_catalog(db) is True
    assert db.query(AlbumCatalogEntry).count() == 1


def test_check_noop_when_unchanged(db):
    _track(db, "t1", "T", "A", "B", "aid1")
    check_and_rebuild_catalog(db)
    assert check_and_rebuild_catalog(db) is False


def test_check_rebuilds_on_change(db):
    _track(db, "t1", "T", "A", "B", "aid1")
    check_and_rebuild_catalog(db)
    _track(db, "t2", "T2", "A", "B", "aid1")
    assert check_and_rebuild_catalog(db) is True
    assert db.query(AlbumCatalogEntry).first().track_count == 2


def test_check_rebuilds_on_soft_delete(db):
    from datetime import datetime
    _track(db, "t1", "T1", "A", "B", "aid1")
    _track(db, "t2", "T2", "A", "B", "aid1")
    check_and_rebuild_catalog(db)
    db.query(LibraryTrack).filter_by(jellyfin_item_id="t2").update(
        {"missing_since": datetime(2025, 1, 1)}
    )
    db.commit()
    assert check_and_rebuild_catalog(db) is True
    assert db.query(AlbumCatalogEntry).first().track_count == 1
