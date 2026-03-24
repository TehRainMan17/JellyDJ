"""
Tests for services/library_scanner.py — pure helpers and scan_library() upsert.

library_scanner.py is the foundation layer that every other system relies on
for "what music do we have".  A bug in scan_library() means:
  - New tracks silently never appear in recommendations
  - Changed metadata (artist name, album) persists incorrectly
  - Removed tracks are never soft-deleted, polluting playlists with dead items
  - Deleted-then-re-added tracks stay permanently missing

Also covers the pure helper functions that parse Jellyfin API payloads:
  - _parse_date(): ISO timestamp handling
  - _extract_genre(): first genre extraction
  - _extract_artist(): Various-Artists-aware artist selection
  - _extract_artist_id(): AlbumArtists→ArtistItems fallback

Note: scan_library() calls services.holiday.tag_library() at the end.
We patch that out to keep these tests focused on scan logic, not holiday tagging.

Uses an in-memory SQLite database for scan_library() tests.

Run with: docker exec jellydj-backend python -m pytest tests/test_library_scanner.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # registers all ORM models with Base.metadata

from services.library_scanner import (
    _parse_date,
    _extract_genre,
    _extract_artist,
    _extract_artist_id,
    scan_library,
)


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── _parse_date ───────────────────────────────────────────────────────────────

class TestParseDate:

    def test_valid_iso_returns_datetime(self):
        dt = _parse_date("2023-06-15T12:00:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2023
        assert dt.month == 6

    def test_trailing_z_stripped(self):
        dt = _parse_date("2023-06-15T12:00:00Z")
        assert dt is not None
        assert dt.year == 2023

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_date("") is None

    def test_garbage_string_returns_none(self):
        assert _parse_date("not-a-date") is None


# ── _extract_genre ────────────────────────────────────────────────────────────

class TestExtractGenre:

    def test_returns_first_genre(self):
        item = {"Genres": ["Rock", "Pop"]}
        assert _extract_genre(item) == "Rock"

    def test_single_genre(self):
        item = {"Genres": ["Jazz"]}
        assert _extract_genre(item) == "Jazz"

    def test_empty_genres_returns_empty_string(self):
        item = {"Genres": []}
        assert _extract_genre(item) == ""

    def test_missing_genres_key_returns_empty_string(self):
        item = {}
        assert _extract_genre(item) == ""


# ── _extract_artist ───────────────────────────────────────────────────────────

class TestExtractArtist:

    def test_normal_album_artist_returned(self):
        item = {"AlbumArtist": "Led Zeppelin", "Artists": ["Led Zeppelin"]}
        assert _extract_artist(item) == "Led Zeppelin"

    def test_various_artists_falls_back_to_track_artist(self):
        item = {"AlbumArtist": "Various Artists", "Artists": ["David Bowie"]}
        assert _extract_artist(item) == "David Bowie"

    def test_various_artists_lowercase_variant(self):
        item = {"AlbumArtist": "various artists", "Artists": ["Nina Simone"]}
        assert _extract_artist(item) == "Nina Simone"

    def test_va_abbreviation(self):
        item = {"AlbumArtist": "V.A.", "Artists": ["Radiohead"]}
        assert _extract_artist(item) == "Radiohead"

    def test_va_slash_variant(self):
        item = {"AlbumArtist": "V/A", "Artists": ["Portishead"]}
        assert _extract_artist(item) == "Portishead"

    def test_unknown_artist_fallback(self):
        item = {"AlbumArtist": "Unknown Artist", "Artists": ["Massive Attack"]}
        assert _extract_artist(item) == "Massive Attack"

    def test_missing_album_artist_uses_track_artist(self):
        item = {"AlbumArtist": None, "Artists": ["Björk"]}
        assert _extract_artist(item) == "Björk"

    def test_all_various_falls_back_to_album_artist(self):
        """When both AlbumArtist and all Artists are various-artist variants,
        falls back to AlbumArtist."""
        item = {"AlbumArtist": "various", "Artists": ["Various Artists"]}
        # After filtering real artists = [] → falls to track_artists[0]
        result = _extract_artist(item)
        # Various Artists is the only option
        assert result in ("various", "Various Artists")

    def test_empty_album_artist_uses_track_artist(self):
        item = {"AlbumArtist": "", "Artists": ["Tom Waits"]}
        assert _extract_artist(item) == "Tom Waits"

    def test_no_artists_at_all_returns_empty_string(self):
        item = {"AlbumArtist": "", "Artists": []}
        assert _extract_artist(item) == ""


# ── _extract_artist_id ────────────────────────────────────────────────────────

class TestExtractArtistId:

    def test_real_album_artist_id_returned(self):
        item = {
            "AlbumArtists": [{"Id": "artist-id-1", "Name": "Led Zeppelin"}],
            "ArtistItems": [],
        }
        assert _extract_artist_id(item) == "artist-id-1"

    def test_various_artists_skipped_falls_to_artist_items(self):
        item = {
            "AlbumArtists": [{"Id": "va-id", "Name": "Various Artists"}],
            "ArtistItems": [{"Id": "real-id", "Name": "David Bowie"}],
        }
        assert _extract_artist_id(item) == "real-id"

    def test_missing_album_artists_key_returns_none(self):
        item = {}
        assert _extract_artist_id(item) is None

    def test_empty_album_artists_returns_none(self):
        item = {"AlbumArtists": [], "ArtistItems": []}
        assert _extract_artist_id(item) is None

    def test_last_resort_various_artist_id(self):
        """If all entries are various-artists, returns first AlbumArtists Id anyway."""
        item = {
            "AlbumArtists": [{"Id": "va-id", "Name": "Various Artists"}],
            "ArtistItems": [{"Id": "va-id-2", "Name": "various"}],
        }
        # Both are various — last resort returns album_artists[0].get("Id")
        result = _extract_artist_id(item)
        assert result == "va-id"

    def test_id_missing_from_album_artist_dict_returns_none(self):
        item = {
            "AlbumArtists": [{"Name": "Led Zeppelin"}],  # no "Id" key
            "ArtistItems": [],
        }
        assert _extract_artist_id(item) is None

    def test_non_dict_entries_skipped(self):
        item = {
            "AlbumArtists": ["not-a-dict"],
            "ArtistItems": [{"Id": "real", "Name": "Radiohead"}],
        }
        result = _extract_artist_id(item)
        assert result == "real"


# ── scan_library ──────────────────────────────────────────────────────────────

# scan_library() does `from services.holiday import tag_library` inside the
# function body, so we patch it on the holiday module directly.
PATCH_TAG_LIBRARY = patch(
    "services.holiday.tag_library",
    return_value={"tagged": 0, "breakdown": {}},
)


class TestScanLibrary:

    def _item(self, jid, name="Track", album="Album", artist="Artist",
              album_artist="Artist", genres=None):
        return {
            "Id": jid,
            "Name": name,
            "Album": album,
            "AlbumArtist": album_artist,
            "Artists": [artist],
            "AlbumArtists": [{"Id": f"{jid}-artist", "Name": album_artist}],
            "ArtistItems": [],
            "Genres": genres or [],
            "RunTimeTicks": None,
            "IndexNumber": None,
            "ParentIndexNumber": None,
            "ProductionYear": None,
            "DateCreated": None,
            "AlbumId": f"{jid}-album",
        }

    @PATCH_TAG_LIBRARY
    def test_new_track_added_to_db(self, mock_tag, db):
        from models import LibraryTrack
        items = [self._item("id-1", name="Yesterday", artist="The Beatles")]
        scan_library(db, items)
        row = db.query(LibraryTrack).filter_by(jellyfin_item_id="id-1").first()
        assert row is not None
        assert row.track_name == "Yesterday"

    @PATCH_TAG_LIBRARY
    def test_added_count_correct(self, mock_tag, db):
        items = [self._item("id-1"), self._item("id-2")]
        stats = scan_library(db, items)
        assert stats["added"] == 2

    @PATCH_TAG_LIBRARY
    def test_existing_track_updated(self, mock_tag, db):
        from models import LibraryTrack
        # First scan: add track
        scan_library(db, [self._item("id-1", name="Old Name")])
        # Second scan: same ID, different name
        stats = scan_library(db, [self._item("id-1", name="New Name")])
        row = db.query(LibraryTrack).filter_by(jellyfin_item_id="id-1").first()
        assert row.track_name == "New Name"
        assert stats["updated"] == 1

    @PATCH_TAG_LIBRARY
    def test_missing_track_soft_deleted(self, mock_tag, db):
        from models import LibraryTrack
        # Add track in first scan
        scan_library(db, [self._item("id-1"), self._item("id-2")])
        # Second scan: id-2 gone
        stats = scan_library(db, [self._item("id-1")])
        row = db.query(LibraryTrack).filter_by(jellyfin_item_id="id-2").first()
        assert row.missing_since is not None
        assert stats["soft_deleted"] == 1

    @PATCH_TAG_LIBRARY
    def test_restored_track_clears_missing_since(self, mock_tag, db):
        from models import LibraryTrack
        # Add, then soft-delete
        scan_library(db, [self._item("id-1")])
        scan_library(db, [])  # disappears
        row = db.query(LibraryTrack).filter_by(jellyfin_item_id="id-1").first()
        assert row.missing_since is not None
        # Re-appears in next scan
        scan_library(db, [self._item("id-1")])
        db.refresh(row)
        assert row.missing_since is None

    @PATCH_TAG_LIBRARY
    def test_track_without_id_skipped(self, mock_tag, db):
        from models import LibraryTrack
        items = [{"Name": "No ID Track", "Genres": [], "Artists": []}]
        scan_library(db, items)
        assert db.query(LibraryTrack).count() == 0

    @PATCH_TAG_LIBRARY
    def test_stats_total_in_db_excludes_missing(self, mock_tag, db):
        # Scan two tracks, then remove one
        scan_library(db, [self._item("id-1"), self._item("id-2")])
        stats = scan_library(db, [self._item("id-1")])
        assert stats["total_in_db"] == 1

    @PATCH_TAG_LIBRARY
    def test_already_missing_track_not_double_counted(self, mock_tag, db):
        """A track that was already soft-deleted should not increment soft_deleted again."""
        scan_library(db, [self._item("id-1")])
        # First disappearance
        stats1 = scan_library(db, [])
        assert stats1["soft_deleted"] == 1
        # Second scan with id-1 still absent — already has missing_since set
        stats2 = scan_library(db, [])
        assert stats2["soft_deleted"] == 0

    @PATCH_TAG_LIBRARY
    def test_artist_extracted_from_item(self, mock_tag, db):
        from models import LibraryTrack
        items = [self._item("id-1", artist="David Bowie", album_artist="David Bowie")]
        scan_library(db, items)
        row = db.query(LibraryTrack).filter_by(jellyfin_item_id="id-1").first()
        assert row.artist_name == "David Bowie"

    @PATCH_TAG_LIBRARY
    def test_empty_items_list_soft_deletes_all(self, mock_tag, db):
        from models import LibraryTrack
        scan_library(db, [self._item("id-1"), self._item("id-2")])
        stats = scan_library(db, [])
        assert stats["soft_deleted"] == 2
        assert db.query(LibraryTrack).filter(
            LibraryTrack.missing_since.isnot(None)
        ).count() == 2
