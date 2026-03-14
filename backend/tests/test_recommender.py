"""
Unit tests for the recommendation engine scoring logic.
Run with: docker exec jellydj-backend python -m pytest tests/ -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

from services.recommender import (
    _recency_score,
    recommend_library_tracks,
    WEIGHT_PRESETS,
    TrackResult,
)


# ── _recency_score ────────────────────────────────────────────────────────────

class TestRecencyScore:
    def test_never_played_returns_one(self):
        assert _recency_score(None) == 1.0

    def test_played_today_returns_zero(self):
        assert _recency_score(datetime.utcnow()) == 0.0

    def test_played_yesterday_returns_zero(self):
        assert _recency_score(datetime.utcnow() - timedelta(days=1)) == 0.0

    def test_played_29_days_ago_returns_zero(self):
        assert _recency_score(datetime.utcnow() - timedelta(days=29)) == 0.0

    def test_played_30_days_ago_returns_zero(self):
        # Exactly at the stale boundary — still 0
        assert _recency_score(datetime.utcnow() - timedelta(days=30)) == 0.0

    def test_played_180_days_ago_returns_partial(self):
        score = _recency_score(datetime.utcnow() - timedelta(days=180))
        assert 0.0 < score < 1.0

    def test_played_over_year_ago_returns_one(self):
        score = _recency_score(datetime.utcnow() - timedelta(days=400))
        assert score == 1.0

    def test_recency_increases_with_staleness(self):
        s60  = _recency_score(datetime.utcnow() - timedelta(days=60))
        s120 = _recency_score(datetime.utcnow() - timedelta(days=120))
        s240 = _recency_score(datetime.utcnow() - timedelta(days=240))
        assert s60 < s120 < s240


# ── Weight presets ────────────────────────────────────────────────────────────

class TestWeightPresets:
    def test_all_presets_sum_to_one(self):
        for name, weights in WEIGHT_PRESETS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-9, f"Preset '{name}' weights sum to {total}, not 1.0"

    def test_for_you_affinity_dominant(self):
        w = WEIGHT_PRESETS["for_you"]
        assert w["affinity"] == max(w.values()), "for_you should weight affinity highest"

    def test_discover_novelty_dominant(self):
        w = WEIGHT_PRESETS["discover"]
        assert w["novelty"] == max(w.values()), "discover should weight novelty highest"

    def test_required_keys_present(self):
        required = {"affinity", "popularity", "recency_inv", "novelty"}
        for name, weights in WEIGHT_PRESETS.items():
            assert set(weights.keys()) == required, f"Preset '{name}' missing keys"


# ── recommend_library_tracks ──────────────────────────────────────────────────

def _make_play(
    item_id="item1",
    track="Track",
    artist="Artist",
    album="Album",
    genre="Rock",
    play_count=5,
    last_played=None,
    is_favorite=False,
    user_id="user1",
):
    m = MagicMock()
    m.jellyfin_item_id = item_id
    m.track_name = track
    m.artist_name = artist
    m.album_name = album
    m.genre = genre
    m.play_count = play_count
    m.last_played = last_played or (datetime.utcnow() - timedelta(days=60))
    m.is_favorite = is_favorite
    m.user_id = user_id
    return m


def _make_db(plays, artist_affinity=None, genre_affinity=None):
    """Return a mock DB session with the given plays and taste profile."""
    db = MagicMock()

    # _fetch_all_library_tracks
    play_query = MagicMock()
    play_query.filter_by.return_value.all.return_value = plays

    # _affinity_map — taste profile rows
    profile_rows = []
    for artist, score in (artist_affinity or {}).items():
        r = MagicMock()
        r.artist_name = artist
        r.genre = None
        r.affinity_score = str(score)
        profile_rows.append(r)
    for genre, score in (genre_affinity or {}).items():
        r = MagicMock()
        r.artist_name = None
        r.genre = genre
        r.affinity_score = str(score)
        profile_rows.append(r)

    profile_query = MagicMock()
    profile_query.filter_by.return_value.all.return_value = profile_rows

    # PopularityCache — return None (neutral 0.5 fallback)
    cache_query = MagicMock()
    cache_query.filter_by.return_value.first.return_value = None

    def _query_dispatch(model):
        from models import Play, UserTasteProfile, PopularityCache, SkipPenalty
        if model is Play:
            return play_query
        if model is UserTasteProfile:
            return profile_query
        if model is PopularityCache:
            return cache_query
        if model is SkipPenalty:
            # No skip penalties in tests — return None so penalty = 0.0
            skip_query = MagicMock()
            skip_query.filter_by.return_value.first.return_value = None
            return skip_query
        return MagicMock()

    db.query.side_effect = _query_dispatch
    return db


class TestRecommendLibraryTracks:

    def test_returns_correct_count(self):
        plays = [_make_play(item_id=f"id{i}", track=f"Track {i}") for i in range(20)]
        db = _make_db(plays, artist_affinity={"Artist": 80.0})
        results = recommend_library_tracks("user1", "for_you", 10, db)
        assert len(results) == 10

    def test_returns_all_when_fewer_than_limit(self):
        plays = [_make_play(item_id=f"id{i}") for i in range(5)]
        db = _make_db(plays)
        results = recommend_library_tracks("user1", "for_you", 20, db)
        assert len(results) == 5

    def test_empty_library_returns_empty(self):
        db = _make_db([])
        results = recommend_library_tracks("user1", "for_you", 10, db)
        assert results == []

    def test_scores_are_between_zero_and_one(self):
        plays = [_make_play(item_id=f"id{i}") for i in range(10)]
        db = _make_db(plays, artist_affinity={"Artist": 100.0})
        results = recommend_library_tracks("user1", "for_you", 10, db)
        for r in results:
            assert 0.0 <= r.score <= 1.05, f"Score out of range: {r.score}"

    def test_favorite_gets_bonus(self):
        fav   = _make_play(item_id="fav",    artist="X", play_count=1, is_favorite=True)
        plain = _make_play(item_id="plain",  artist="X", play_count=1, is_favorite=False)
        db = _make_db([fav, plain], artist_affinity={"X": 50.0})
        results = recommend_library_tracks("user1", "for_you", 10, db)
        fav_result   = next(r for r in results if r.jellyfin_item_id == "fav")
        plain_result = next(r for r in results if r.jellyfin_item_id == "plain")
        assert fav_result.score > plain_result.score

    def test_unplayed_track_scores_higher_in_discover_mode(self):
        unplayed = _make_play(item_id="new",  artist="Y", play_count=0,  last_played=None)
        played   = _make_play(item_id="old",  artist="Y", play_count=20,
                              last_played=datetime.utcnow() - timedelta(days=5))
        db = _make_db([unplayed, played], artist_affinity={"Y": 50.0})
        results = recommend_library_tracks("user1", "discover", 10, db)
        unplayed_r = next(r for r in results if r.jellyfin_item_id == "new")
        played_r   = next(r for r in results if r.jellyfin_item_id == "old")
        assert unplayed_r.score > played_r.score

    def test_high_affinity_artist_scores_higher_in_for_you(self):
        loved   = _make_play(item_id="loved",   artist="Loved Artist",   play_count=5)
        unknown = _make_play(item_id="unknown", artist="Unknown Artist",  play_count=5)
        db = _make_db(
            [loved, unknown],
            artist_affinity={"Loved Artist": 100.0, "Unknown Artist": 0.0}
        )
        results = recommend_library_tracks("user1", "for_you", 10, db)
        loved_r   = next(r for r in results if r.jellyfin_item_id == "loved")
        unknown_r = next(r for r in results if r.jellyfin_item_id == "unknown")
        assert loved_r.score > unknown_r.score

    def test_score_breakdown_has_all_components(self):
        plays = [_make_play()]
        db = _make_db(plays)
        results = recommend_library_tracks("user1", "for_you", 5, db)
        assert len(results) > 0
        bd = results[0].score_breakdown
        assert {"affinity", "popularity", "recency_inv", "novelty", "skip_penalty"} == set(bd.keys())

    def test_results_are_instances_of_trackresult(self):
        plays = [_make_play(item_id=f"id{i}") for i in range(5)]
        db = _make_db(plays)
        results = recommend_library_tracks("user1", "for_you", 5, db)
        for r in results:
            assert isinstance(r, TrackResult)
