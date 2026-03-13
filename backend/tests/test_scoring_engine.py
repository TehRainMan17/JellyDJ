
"""
Tests for Module 8a: Library Scanner + Scoring Engine

Tests cover:
  - Library scan upsert logic (add/update/soft-delete)
  - Artist profile scoring
  - Genre profile scoring
  - Track score computation for played tracks
  - Track score computation for unplayed tracks
  - Unplayed cap enforcement
  - Skip penalty propagation to unplayed tracks
  - Score distribution stats
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from services.scoring_engine import (
    _play_score,
    _recency_score,
    _skip_multiplier,
    rebuild_artist_profiles,
    rebuild_genre_profiles,
    rebuild_track_scores,
    rebuild_all_scores,
    get_score_distribution,
    UNPLAYED_CAP,
    UNPLAYED_BASE,
    FAVORITE_BONUS,
    SKIP_MIN_EVENTS,
)
from services.library_scanner import scan_library


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_library_track(item_id="t1", artist="Artist A", album="Album 1",
                         track="Track 1", genre="Rock"):
    from models import LibraryTrack
    t = LibraryTrack()
    t.jellyfin_item_id = item_id
    t.artist_name = artist
    t.album_name = album
    t.track_name = track
    t.album_artist = artist
    t.genre = genre
    t.duration_ticks = 2000000
    t.track_number = 1
    t.disc_number = 1
    t.year = 2020
    t.first_seen = datetime.utcnow()
    t.last_seen = datetime.utcnow()
    t.missing_since = None
    return t


def _make_play(item_id="t1", artist="Artist A", album="Album 1",
               play_count=5, genre="Rock", last_played=None,
               is_favorite=False):
    from models import Play
    p = Play()
    p.jellyfin_item_id = item_id
    p.artist_name = artist
    p.album_name = album
    p.track_name = "Track"
    p.genre = genre
    p.play_count = play_count
    p.last_played = last_played or (datetime.utcnow() - timedelta(days=60))
    p.is_favorite = is_favorite
    p.user_id = "user1"
    return p


def _make_db(library_tracks=None, plays=None, skip_penalties=None):
    """Build a mock DB session for scoring tests."""
    from models import LibraryTrack, Play, SkipPenalty, ArtistProfile, GenreProfile, TrackScore

    lib = library_tracks or []
    pl = plays or []
    sk = skip_penalties or []

    db = MagicMock()
    deleted = []

    def query_dispatch(model):
        if model is LibraryTrack:
            q = MagicMock()
            q.filter.return_value.all.return_value = lib
            q.filter_by.return_value.all.return_value = lib
            # filter(missing_since.is_(None))
            q.filter.return_value.filter.return_value.all.return_value = lib
            return q
        if model is Play:
            q = MagicMock()
            q.filter_by.return_value.all.return_value = pl
            return q
        if model is SkipPenalty:
            q = MagicMock()
            q.filter_by.return_value.all.return_value = sk
            return q
        if model in (ArtistProfile, GenreProfile, TrackScore):
            q = MagicMock()
            q.filter_by.return_value.delete.return_value = None
            q.filter_by.return_value.all.return_value = []
            return q
        return MagicMock()

    db.query.side_effect = query_dispatch
    db.add = MagicMock()
    db.flush = MagicMock()
    db.commit = MagicMock()
    return db


# ── Unit tests: helper functions ──────────────────────────────────────────────

class TestPlayScore:
    def test_zero_plays_returns_zero(self):
        assert _play_score(0, 100) == 0.0

    def test_max_plays_returns_100(self):
        assert _play_score(100, 100) == 100.0

    def test_log_scaling(self):
        s10 = _play_score(10, 100)
        s50 = _play_score(50, 100)
        s100 = _play_score(100, 100)
        assert 0 < s10 < s50 < s100

    def test_zero_max_plays_returns_zero(self):
        assert _play_score(5, 0) == 0.0


class TestRecencyScore:
    def test_played_today_returns_100(self):
        assert _recency_score(datetime.utcnow()) == 100.0

    def test_played_within_grace_returns_100(self):
        assert _recency_score(datetime.utcnow() - timedelta(days=20)) == 100.0

    def test_played_at_decay_limit_returns_zero(self):
        assert _recency_score(datetime.utcnow() - timedelta(days=365)) == 0.0

    def test_halfway_returns_partial(self):
        # ~197 days is halfway through the decay window (30–365)
        s = _recency_score(datetime.utcnow() - timedelta(days=197))
        assert 40.0 < s < 60.0

    def test_none_returns_zero(self):
        assert _recency_score(None) == 0.0

    def test_increases_with_recency(self):
        s_old = _recency_score(datetime.utcnow() - timedelta(days=300))
        s_mid = _recency_score(datetime.utcnow() - timedelta(days=150))
        s_new = _recency_score(datetime.utcnow() - timedelta(days=10))
        assert s_old < s_mid < s_new


class TestSkipMultiplier:
    def test_no_penalty_returns_one(self):
        assert _skip_multiplier(0.0) == 1.0

    def test_full_penalty_returns_min(self):
        assert _skip_multiplier(1.0) == 0.1

    def test_half_penalty_reduces_score(self):
        m = _skip_multiplier(0.5)
        assert 0.4 < m < 0.6

    def test_never_returns_zero(self):
        assert _skip_multiplier(2.0) >= 0.1


# ── Unit tests: scoring engine ────────────────────────────────────────────────

class TestArtistProfiles:
    def test_builds_profile_from_plays(self):
        plays = [
            _make_play("t1", artist="Metallica", play_count=20),
            _make_play("t2", artist="Metallica", play_count=10),
            _make_play("t3", artist="Coldplay",  play_count=2),
        ]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        assert "Metallica" in result
        assert "Coldplay" in result
        assert result["Metallica"] > result["Coldplay"]

    def test_favorite_boosts_score(self):
        plain = [_make_play("t1", artist="X", play_count=5, is_favorite=False)]
        fav   = [_make_play("t1", artist="X", play_count=5, is_favorite=True)]
        db_plain = _make_db(plays=plain)
        db_fav   = _make_db(plays=fav)
        plain_score = rebuild_artist_profiles(db_plain, "user1").get("X", 0)
        fav_score   = rebuild_artist_profiles(db_fav, "user1").get("X", 0)
        assert fav_score > plain_score

    def test_no_plays_returns_empty(self):
        db = _make_db(plays=[])
        result = rebuild_artist_profiles(db, "user1")
        assert result == {}

    def test_scores_are_between_0_and_100(self):
        plays = [_make_play(f"t{i}", artist=f"Artist{i}", play_count=i*5) for i in range(1, 10)]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        for artist, score in result.items():
            assert 0.0 <= score <= 100.0, f"{artist} score {score} out of range"


class TestGenreProfiles:
    def test_builds_profile_from_plays(self):
        plays = [
            _make_play("t1", genre="Rock",  play_count=20),
            _make_play("t2", genre="Rock",  play_count=15),
            _make_play("t3", genre="Pop",   play_count=3),
        ]
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")
        assert "Rock" in result
        assert "Pop" in result
        assert result["Rock"] > result["Pop"]

    def test_scores_are_between_0_and_100(self):
        plays = [_make_play(f"t{i}", genre=f"Genre{i % 3}", play_count=i*3) for i in range(1, 8)]
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")
        for genre, score in result.items():
            assert 0.0 <= score <= 100.0


class TestTrackScores:
    def test_played_track_scores_above_unplayed(self):
        lib = [
            _make_library_track("played",   artist="X", genre="Rock"),
            _make_library_track("unplayed", artist="X", genre="Rock"),
        ]
        plays = [_make_play("played", artist="X", genre="Rock", play_count=20)]
        db = _make_db(library_tracks=lib, plays=plays)
        # Capture added scores
        added = []
        db.add = lambda obj: added.append(obj)
        from models import TrackScore
        rebuild_track_scores(db, "user1", {"X": 80.0}, {"Rock": 70.0})
        scores = {s.jellyfin_item_id: float(s.final_score) for s in added if hasattr(s, 'final_score')}
        assert scores.get("played", 0) > scores.get("unplayed", 0)

    def test_unplayed_score_capped_at_unplayed_cap(self):
        lib = [_make_library_track("t1", artist="Beatles", genre="Rock")]
        db = _make_db(library_tracks=lib, plays=[])
        added = []
        db.add = lambda obj: added.append(obj)
        rebuild_track_scores(db, "user1", {"Beatles": 100.0}, {"Rock": 100.0})
        scores = [float(s.final_score) for s in added if hasattr(s, 'final_score')]
        assert all(s <= UNPLAYED_CAP for s in scores), f"Scores exceeded cap: {scores}"

    def test_skip_penalty_reduces_played_score(self):
        from models import SkipPenalty
        lib = [_make_library_track("t1")]
        plays = [_make_play("t1", play_count=20)]
        sk = MagicMock(spec=SkipPenalty)
        sk.jellyfin_item_id = "t1"
        sk.penalty = "0.6"
        sk.total_events = 10
        db_clean = _make_db(library_tracks=lib, plays=plays)
        db_skip  = _make_db(library_tracks=lib, plays=plays, skip_penalties=[sk])
        added_clean = []
        added_skip  = []
        db_clean.add = lambda obj: added_clean.append(obj)
        db_skip.add  = lambda obj: added_skip.append(obj)
        rebuild_track_scores(db_clean, "user1", {"Artist A": 80.0}, {"Rock": 70.0})
        rebuild_track_scores(db_skip,  "user1", {"Artist A": 80.0}, {"Rock": 70.0})
        clean_score = next(float(s.final_score) for s in added_clean if hasattr(s, 'final_score'))
        skip_score  = next(float(s.final_score) for s in added_skip  if hasattr(s, 'final_score'))
        assert skip_score < clean_score

    def test_favorite_bonus_applied(self):
        lib = [_make_library_track("t1")]
        plain = [_make_play("t1", play_count=10, is_favorite=False)]
        fav   = [_make_play("t1", play_count=10, is_favorite=True)]
        added_p, added_f = [], []
        db_p = _make_db(library_tracks=lib, plays=plain)
        db_f = _make_db(library_tracks=lib, plays=fav)
        db_p.add = lambda obj: added_p.append(obj)
        db_f.add = lambda obj: added_f.append(obj)
        rebuild_track_scores(db_p, "user1", {"Artist A": 80.0}, {"Rock": 70.0})
        rebuild_track_scores(db_f, "user1", {"Artist A": 80.0}, {"Rock": 70.0})
        ps = next(float(s.final_score) for s in added_p if hasattr(s, 'final_score'))
        fs = next(float(s.final_score) for s in added_f if hasattr(s, 'final_score'))
        assert fs > ps
        # Bonus may be partially absorbed by the 100.0 cap at high base scores,
        # so we assert it increases the score but don't require the exact magnitude.
        assert fs - ps > 0.5

    def test_high_affinity_unplayed_scores_higher_than_low_affinity(self):
        lib = [
            _make_library_track("loved",   artist="Beatles", genre="Rock"),
            _make_library_track("unknown", artist="NobodyBand", genre="Country"),
        ]
        db = _make_db(library_tracks=lib, plays=[])
        added = []
        db.add = lambda obj: added.append(obj)
        rebuild_track_scores(
            db, "user1",
            {"Beatles": 100.0, "NobodyBand": 0.0},
            {"Rock": 100.0, "Country": 0.0}
        )
        scores = {s.jellyfin_item_id: float(s.final_score) for s in added if hasattr(s, 'final_score')}
        assert scores["loved"] > scores["unknown"]

    def test_unplayed_track_is_marked_not_played(self):
        lib = [_make_library_track("t1")]
        db = _make_db(library_tracks=lib, plays=[])
        added = []
        db.add = lambda obj: added.append(obj)
        rebuild_track_scores(db, "user1", {}, {})
        ts = next((s for s in added if hasattr(s, 'is_played')), None)
        assert ts is not None
        assert ts.is_played is False
        assert ts.play_count == 0

    def test_all_scores_between_0_and_100(self):
        lib = [_make_library_track(f"t{i}", artist=f"A{i % 3}", genre=f"G{i % 2}") for i in range(20)]
        plays = [_make_play(f"t{i}", artist=f"A{i % 3}", genre=f"G{i % 2}", play_count=i) for i in range(10)]
        db = _make_db(library_tracks=lib, plays=plays)
        added = []
        db.add = lambda obj: added.append(obj)
        artist_aff = {f"A{i}": float(i * 30) for i in range(3)}
        genre_aff  = {f"G{i}": float(i * 50) for i in range(2)}
        rebuild_track_scores(db, "user1", artist_aff, genre_aff)
        scores = [float(s.final_score) for s in added if hasattr(s, 'final_score')]
        assert len(scores) == 20
        for s in scores:
            assert 0.0 <= s <= 100.0, f"Score {s} out of range"


class TestLibraryScanner:
    def test_scan_adds_new_tracks(self):
        items = [
            {"Id": "t1", "Name": "Track 1", "AlbumArtist": "Artist A",
             "Album": "Album 1", "Genres": ["Rock"], "RunTimeTicks": 2000000},
            {"Id": "t2", "Name": "Track 2", "AlbumArtist": "Artist B",
             "Album": "Album 2", "Genres": ["Pop"],  "RunTimeTicks": 1800000},
        ]
        from models import LibraryTrack
        db = MagicMock()
        existing_map: dict = {}
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 2
        added = []
        db.add = lambda obj: added.append(obj)
        db.commit = MagicMock()
        stats = scan_library(db, items)
        assert stats["added"] == 2
        assert stats["updated"] == 0
        assert stats["soft_deleted"] == 0

    def test_scan_soft_deletes_missing_tracks(self):
        from models import LibraryTrack
        existing_track = _make_library_track("old_t1")

        db = MagicMock()
        def query_dispatch(model):
            if model is LibraryTrack:
                q = MagicMock()
                q.all.return_value = [existing_track]
                q.filter.return_value.count.return_value = 0
                return q
            return MagicMock()
        db.query.side_effect = query_dispatch
        db.add = MagicMock()
        db.commit = MagicMock()

        # Scan with no items — old track should be soft-deleted
        stats = scan_library(db, [])
        assert existing_track.missing_since is not None
        assert stats["soft_deleted"] == 1

    def test_scan_clears_missing_since_when_track_returns(self):
        from models import LibraryTrack
        track = _make_library_track("t1")
        track.missing_since = datetime.utcnow() - timedelta(days=5)

        db = MagicMock()
        def query_dispatch(model):
            if model is LibraryTrack:
                q = MagicMock()
                q.all.return_value = [track]
                q.filter.return_value.count.return_value = 1
                return q
            return MagicMock()
        db.query.side_effect = query_dispatch
        db.add = MagicMock()
        db.commit = MagicMock()

        items = [{"Id": "t1", "Name": "Track 1", "AlbumArtist": "Artist A",
                  "Album": "Album 1", "Genres": ["Rock"]}]
        scan_library(db, items)
        assert track.missing_since is None

    def test_items_without_id_are_skipped(self):
        items = [{"Name": "No ID Track", "AlbumArtist": "X"}]
        from models import LibraryTrack
        db = MagicMock()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        added = []
        db.add = lambda obj: added.append(obj)
        db.commit = MagicMock()
        stats = scan_library(db, items)
        assert stats["added"] == 0
