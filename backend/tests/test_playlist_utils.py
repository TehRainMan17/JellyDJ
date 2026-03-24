"""
Tests for services/playlist_utils.py — shared playlist exclusion helpers.

These three functions build the combined exclusion frozenset passed to every
block executor during playlist generation.  A bug here means excluded albums,
out-of-season holiday tracks, or artist cooldowns fail to suppress their
tracks — producing playlists with content the user explicitly blacklisted.

Covers:
  - get_excluded_item_ids(): empty, pass-1 (album_id exact match), pass-2
    (album_name case-insensitive match via LibraryTrack and TrackScore),
    missing tracks not included, union of both passes, DB exception → frozenset()
  - get_artist_cooled_down_ids(): no cooldowns, active future cooldown,
    expired cooldown, wrong-status cooldown, missing tracks not included,
    DB exception → frozenset()
  - get_holiday_excluded_ids(): no holiday tracks, tag set but flag False,
    tag set and flag True (excluded), no tag at all, DB exception → frozenset()

Uses an in-memory SQLite database — real SQL, not mocks — so that actual
WHERE clause correctness is verified.

Run with: docker exec jellydj-backend python -m pytest tests/test_playlist_utils.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

from database import Base
import models  # ensures all ORM models register with Base.metadata before create_all() runs


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """Fresh in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    # No drop_all — the in-memory SQLite engine is discarded when it goes out of
    # scope. Calling Base.metadata.drop_all() can leave SQLAlchemy's shared
    # metadata in a state that breaks subsequent tests in the same session.


# ── Model constructors ────────────────────────────────────────────────────────

def _library_track(item_id, album_id=None, album_name="", artist_name="Artist",
                   missing=False, holiday_tag=None, holiday_exclude=False):
    from models import LibraryTrack
    t = LibraryTrack()
    t.jellyfin_item_id = item_id
    t.jellyfin_album_id = album_id
    t.album_name = album_name
    t.artist_name = artist_name
    t.track_name = "Track"
    t.album_artist = artist_name
    t.genre = ""
    t.missing_since = datetime.utcnow() if missing else None
    t.holiday_tag = holiday_tag
    t.holiday_exclude = holiday_exclude
    return t


def _excluded_album(album_id="excluded-album-id", album_name=""):
    from models import ExcludedAlbum
    e = ExcludedAlbum()
    e.jellyfin_album_id = album_id
    e.album_name = album_name
    e.artist_name = "Artist"
    return e


def _track_score(item_id, user_id="u1", album_name="", artist_name="Artist"):
    from models import TrackScore
    ts = TrackScore()
    ts.jellyfin_item_id = item_id
    ts.user_id = user_id
    ts.artist_name = artist_name
    ts.album_name = album_name
    ts.track_name = "Track"
    ts.genre = ""
    ts.final_score = "0.0"
    ts.play_score = "0.0"
    ts.recency_score = "0.0"
    ts.artist_affinity = "0.0"
    ts.genre_affinity = "0.0"
    ts.skip_penalty = "0.0"
    ts.novelty_bonus = "0.0"
    return ts


def _artist_cooldown(user_id, artist_name, status="active", days_ahead=7):
    from models import ArtistCooldown
    c = ArtistCooldown()
    c.user_id = user_id
    c.artist_name = artist_name
    c.status = status
    c.cooldown_until = datetime.utcnow() + timedelta(days=days_ahead)
    return c


# ── get_excluded_item_ids ─────────────────────────────────────────────────────

class TestGetExcludedItemIds:

    def test_no_excluded_albums_returns_empty(self, db):
        from services.playlist_utils import get_excluded_item_ids
        assert get_excluded_item_ids(db) == frozenset()

    # Pass 1 — jellyfin_album_id exact match

    def test_pass1_matching_album_id_excludes_track(self, db):
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="album-abc"))
        db.add(_library_track("item-1", album_id="album-abc"))
        db.commit()
        assert "item-1" in get_excluded_item_ids(db)

    def test_pass1_non_matching_album_id_not_excluded(self, db):
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="album-abc"))
        db.add(_library_track("item-1", album_id="album-xyz"))
        db.commit()
        assert "item-1" not in get_excluded_item_ids(db)

    def test_pass1_missing_track_not_included(self, db):
        """Tracks with missing_since set (soft-deleted) must not be excluded — they
        are no longer in the library so their IDs have no effect anyway."""
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="album-abc"))
        db.add(_library_track("item-missing", album_id="album-abc", missing=True))
        db.commit()
        result = get_excluded_item_ids(db)
        assert "item-missing" not in result

    def test_pass1_excludes_multiple_tracks_from_same_album(self, db):
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="album-abc"))
        db.add(_library_track("item-1", album_id="album-abc"))
        db.add(_library_track("item-2", album_id="album-abc"))
        db.add(_library_track("item-3", album_id="album-other"))  # not excluded
        db.commit()
        result = get_excluded_item_ids(db)
        assert {"item-1", "item-2"} <= result
        assert "item-3" not in result

    # Pass 2 — album_name case-insensitive match via LibraryTrack

    def test_pass2_name_match_excludes_library_track(self, db):
        """Track with matching album_name (but different album_id) caught by name pass."""
        from services.playlist_utils import get_excluded_item_ids
        # ExcludedAlbum with a jellyfin_album_id that does NOT match the track
        db.add(_excluded_album(album_id="excl-id-99", album_name="Dark Side of the Moon"))
        db.add(_library_track("item-name-match", album_id="different-album-id",
                               album_name="Dark Side of the Moon"))
        db.commit()
        result = get_excluded_item_ids(db)
        assert "item-name-match" in result

    def test_pass2_name_match_is_case_insensitive(self, db):
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="excl-id-01", album_name="Abbey Road"))
        # Track has different casing
        db.add(_library_track("item-case", album_id="diff-id", album_name="ABBEY ROAD"))
        db.commit()
        # SQLite lower() is ASCII-only but this test uses ASCII album names
        result = get_excluded_item_ids(db)
        assert "item-case" in result

    def test_pass2_name_match_excludes_track_score_row(self, db):
        """Pass 2 also scans TrackScore.album_name — catches tracks present in
        track_scores but with a different jellyfin_album_id from the library."""
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="excl-id-02", album_name="Thriller"))
        ts = _track_score("item-ts-only", album_name="Thriller")
        db.add(ts)
        db.commit()
        result = get_excluded_item_ids(db)
        assert "item-ts-only" in result

    def test_pass2_name_mismatch_not_excluded(self, db):
        from services.playlist_utils import get_excluded_item_ids
        db.add(_excluded_album(album_id="excl-id-03", album_name="Abbey Road"))
        db.add(_library_track("item-other", album_id="diff-id", album_name="Led Zeppelin IV"))
        db.commit()
        result = get_excluded_item_ids(db)
        assert "item-other" not in result

    # Union of both passes

    def test_union_of_both_passes(self, db):
        from services.playlist_utils import get_excluded_item_ids
        # Pass 1 hit
        db.add(_excluded_album(album_id="album-p1", album_name=""))
        db.add(_library_track("item-p1", album_id="album-p1"))
        # Pass 2 hit (album_id doesn't match, album_name does)
        db.add(_excluded_album(album_id="album-p2", album_name="Thriller"))
        db.add(_library_track("item-p2", album_id="unrelated-id", album_name="Thriller"))
        db.commit()
        result = get_excluded_item_ids(db)
        assert "item-p1" in result
        assert "item-p2" in result

    # Exception safety

    def test_db_exception_returns_empty_frozenset(self):
        """A DB error must not propagate — returns empty frozenset gracefully."""
        from services.playlist_utils import get_excluded_item_ids
        bad_db = MagicMock()
        bad_db.query.side_effect = Exception("DB is on fire")
        result = get_excluded_item_ids(bad_db)
        assert result == frozenset()


# ── get_artist_cooled_down_ids ────────────────────────────────────────────────

class TestGetArtistCooledDownIds:

    def test_no_cooldowns_returns_empty(self, db):
        from services.playlist_utils import get_artist_cooled_down_ids
        assert get_artist_cooled_down_ids(db, "u1") == frozenset()

    def test_active_future_cooldown_excludes_artist_tracks(self, db):
        from services.playlist_utils import get_artist_cooled_down_ids
        db.add(_artist_cooldown("u1", "The Beatles", status="active", days_ahead=7))
        db.add(_library_track("item-beatles-1", artist_name="The Beatles"))
        db.add(_library_track("item-beatles-2", artist_name="The Beatles"))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-beatles-1" in result
        assert "item-beatles-2" in result

    def test_expired_cooldown_not_excluded(self, db):
        """cooldown_until in the past → user is over the timeout → tracks appear normally."""
        from services.playlist_utils import get_artist_cooled_down_ids
        from models import ArtistCooldown
        c = ArtistCooldown()
        c.user_id = "u1"
        c.artist_name = "Rolling Stones"
        c.status = "active"
        c.cooldown_until = datetime.utcnow() - timedelta(days=1)  # expired
        db.add(c)
        db.add(_library_track("item-stones", artist_name="Rolling Stones"))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-stones" not in result

    def test_inactive_status_not_excluded(self, db):
        """status='expired' (or anything other than 'active') must be ignored."""
        from services.playlist_utils import get_artist_cooled_down_ids
        db.add(_artist_cooldown("u1", "Pink Floyd", status="expired", days_ahead=7))
        db.add(_library_track("item-floyd", artist_name="Pink Floyd"))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-floyd" not in result

    def test_cooldown_for_different_user_not_excluded(self, db):
        """Cooldowns are per-user — user-2's cooldown must not affect user-1."""
        from services.playlist_utils import get_artist_cooled_down_ids
        db.add(_artist_cooldown("u2", "Radiohead", status="active", days_ahead=7))
        db.add(_library_track("item-radiohead", artist_name="Radiohead"))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-radiohead" not in result

    def test_missing_tracks_not_included(self, db):
        """Soft-deleted tracks (missing_since set) must not appear in the exclusion set."""
        from services.playlist_utils import get_artist_cooled_down_ids
        db.add(_artist_cooldown("u1", "Oasis", status="active", days_ahead=7))
        db.add(_library_track("item-oasis-missing", artist_name="Oasis", missing=True))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-oasis-missing" not in result

    def test_non_cooled_artist_not_excluded(self, db):
        from services.playlist_utils import get_artist_cooled_down_ids
        db.add(_artist_cooldown("u1", "The Beatles", status="active", days_ahead=7))
        db.add(_library_track("item-zeppelin", artist_name="Led Zeppelin"))
        db.commit()
        result = get_artist_cooled_down_ids(db, "u1")
        assert "item-zeppelin" not in result

    def test_db_exception_returns_empty_frozenset(self):
        from services.playlist_utils import get_artist_cooled_down_ids
        bad_db = MagicMock()
        bad_db.query.side_effect = Exception("DB blown up")
        result = get_artist_cooled_down_ids(bad_db, "u1")
        assert result == frozenset()


# ── get_holiday_excluded_ids ──────────────────────────────────────────────────

class TestGetHolidayExcludedIds:

    def test_no_tracks_returns_empty(self, db):
        from services.playlist_utils import get_holiday_excluded_ids
        assert get_holiday_excluded_ids(db) == frozenset()

    def test_holiday_tag_and_exclude_true_is_excluded(self, db):
        from services.playlist_utils import get_holiday_excluded_ids
        db.add(_library_track("item-xmas", holiday_tag="christmas", holiday_exclude=True))
        db.commit()
        result = get_holiday_excluded_ids(db)
        assert "item-xmas" in result

    def test_holiday_tag_but_exclude_false_not_excluded(self, db):
        """In-season holiday track (exclude=False) should pass through."""
        from services.playlist_utils import get_holiday_excluded_ids
        db.add(_library_track("item-halloween", holiday_tag="halloween", holiday_exclude=False))
        db.commit()
        result = get_holiday_excluded_ids(db)
        assert "item-halloween" not in result

    def test_no_holiday_tag_not_excluded(self, db):
        """A non-holiday track with holiday_exclude=False is never in the set."""
        from services.playlist_utils import get_holiday_excluded_ids
        db.add(_library_track("item-regular", holiday_tag=None, holiday_exclude=False))
        db.commit()
        result = get_holiday_excluded_ids(db)
        assert "item-regular" not in result

    def test_multiple_excluded_and_non_excluded(self, db):
        from services.playlist_utils import get_holiday_excluded_ids
        db.add(_library_track("item-xmas",     holiday_tag="christmas", holiday_exclude=True))
        db.add(_library_track("item-easter",   holiday_tag="easter",    holiday_exclude=True))
        db.add(_library_track("item-summer",   holiday_tag="summer",    holiday_exclude=False))
        db.add(_library_track("item-regular",  holiday_tag=None,        holiday_exclude=False))
        db.commit()
        result = get_holiday_excluded_ids(db)
        assert "item-xmas"   in result
        assert "item-easter" in result
        assert "item-summer"   not in result
        assert "item-regular"  not in result

    def test_db_exception_returns_empty_frozenset(self):
        from services.playlist_utils import get_holiday_excluded_ids
        bad_db = MagicMock()
        bad_db.query.side_effect = Exception("DB on fire")
        result = get_holiday_excluded_ids(bad_db)
        assert result == frozenset()
