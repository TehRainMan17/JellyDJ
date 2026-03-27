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

v7 additions:
  - Artist breadth: deep-catalogue artists score higher than one-hit wonders
    with the same total plays
  - Ceiling fix: heavy non-favorited artists can score above 70
  - Recency: best-track recency is used (not average), so a recently played
    track lifts the whole artist even if other tracks are old
  - Ordering: correctly ranked real-world analogs
    (many-track heavy listener > one-song listener)

v9 additions:
  - _derive_canonical_genres(): Last.fm tags → weighted multi-genre list
  - _dominant_genre(): picks best GENRE_ADJACENCY-validated genre
  - Canonical genre pipeline: ArtistProfile.canonical_genres drives
    GenreProfile and TrackScore.genre instead of Jellyfin file-tag genres
  - Genre profile keys are normalized (lowercase, spaces)
  - Fracture fix: GenreProfile keys align with Last.fm tag normalization
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from services.scoring_engine import (
    _play_score,
    _recency_score,
    _skip_multiplier,
    _breadth_bonus,
    _derive_canonical_genres,
    _dominant_genre,
    rebuild_artist_profiles,
    rebuild_genre_profiles,
    rebuild_track_scores,
    rebuild_all_scores,
    get_score_distribution,
    UNPLAYED_CAP,
    UNPLAYED_BASE,
    FAVORITE_BONUS,
    SKIP_MIN_EVENTS,
    ARTIST_BREADTH_BONUS_MAX,
    ARTIST_BREADTH_MAX_TRACKS,
    FAVORITE_ARTIST_BOOST,
    RECENTLY_ADDED_BOOST_MAX,
    RECENTLY_ADDED_BOOST_DAYS,
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


def _make_artist_profile(artist_name, primary_genre="", canonical_genres=None):
    """
    Build a mock ArtistProfile for use in _make_db(artist_profiles=[...]).

    canonical_genres should be a list of {"genre": str, "weight": float} dicts
    (the same format stored in ArtistProfile.canonical_genres as JSON).
    If omitted, canonical_genres is None and the scoring engine falls back
    to norm_genre(LibraryTrack.genre).
    """
    import json as _json
    ap = MagicMock()
    ap.artist_name = artist_name
    ap.primary_genre = primary_genre
    ap.canonical_genres = _json.dumps(canonical_genres) if canonical_genres is not None else None
    return ap


def _make_db(library_tracks=None, plays=None, skip_penalties=None, artist_profiles=None):
    """
    Build a mock DB session for scoring tests.

    artist_profiles: list of mock ArtistProfile objects (from _make_artist_profile).
      Injected so rebuild_genre_profiles() and rebuild_track_scores() can test
      the canonical genre path.  When omitted (None → []), both functions fall
      back to the Jellyfin genre (norm_genre(Play.genre / LibraryTrack.genre)).
    """
    from models import LibraryTrack, Play, SkipPenalty, ArtistProfile, GenreProfile, TrackScore

    lib = library_tracks or []
    pl  = plays or []
    sk  = skip_penalties or []
    aps = artist_profiles or []

    db = MagicMock()

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
        if model is ArtistProfile:
            q = MagicMock()
            q.filter_by.return_value.delete.return_value = None
            q.filter_by.return_value.all.return_value = aps
            return q
        if model in (GenreProfile, TrackScore):
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


class TestBreadthBonus:
    """v7: new helper — rewards catalogue depth."""

    def test_zero_tracks_returns_zero(self):
        assert _breadth_bonus(0) == 0.0

    def test_one_track_returns_small_value(self):
        b = _breadth_bonus(1)
        assert 0 < b < ARTIST_BREADTH_BONUS_MAX * 0.25

    def test_increases_with_track_count(self):
        b1 = _breadth_bonus(1)
        b5 = _breadth_bonus(5)
        b20 = _breadth_bonus(20)
        assert b1 < b5 < b20

    def test_saturates_at_max(self):
        # At or beyond ARTIST_BREADTH_MAX_TRACKS the bonus should be at max
        b_max = _breadth_bonus(ARTIST_BREADTH_MAX_TRACKS)
        b_over = _breadth_bonus(ARTIST_BREADTH_MAX_TRACKS * 3)
        assert b_max == pytest.approx(ARTIST_BREADTH_BONUS_MAX, abs=0.1)
        assert b_over == pytest.approx(ARTIST_BREADTH_BONUS_MAX, abs=0.1)

    def test_never_exceeds_max(self):
        for n in [1, 5, 10, 50, 100, 500]:
            assert _breadth_bonus(n) <= ARTIST_BREADTH_BONUS_MAX


# ── Unit tests: artist profiles ───────────────────────────────────────────────

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
        fav_score   = rebuild_artist_profiles(db_fav,   "user1").get("X", 0)
        assert fav_score > plain_score
        assert fav_score - plain_score == pytest.approx(FAVORITE_ARTIST_BOOST, abs=2.0)

    def test_no_plays_returns_empty(self):
        db = _make_db(plays=[])
        result = rebuild_artist_profiles(db, "user1")
        assert result == {}

    def test_scores_are_between_0_and_100(self):
        plays = [_make_play(f"t{i}", artist=f"Artist{i}", play_count=i * 5) for i in range(1, 10)]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        for artist, score in result.items():
            assert 0.0 <= score <= 100.0, f"{artist} score {score} out of range"

    # ── v7 regression tests ───────────────────────────────────────────────────

    def test_ceiling_above_70_for_heavy_non_favorited_artist(self):
        """
        v7 fix: prior formula capped non-favorited artists at 70.
        A user's most-listened artist (all plays, recent) must be able to
        score above 70 without needing a favorite flag.
        """
        # Single artist, 20 recent tracks — they ARE the max, so total_play_score=100
        plays = [
            _make_play(f"t{i}", artist="HeavyListener",
                       play_count=10,
                       last_played=datetime.utcnow() - timedelta(days=5))
            for i in range(20)
        ]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        score = result.get("HeavyListener", 0)
        assert score > 70.0, (
            f"Non-favorited heavy artist scored {score}, expected > 70. "
            "v7 ceiling fix may be broken."
        )

    def test_broad_catalogue_beats_one_hit_wonder_with_same_total_plays(self):
        """
        v7 fix: per-track average previously rewarded one-hit wonders.
        An artist with N tracks × k plays should score HIGHER than an artist
        with 1 track × (N*k) plays, because the user clearly enjoys their
        broader catalogue.
        """
        # George: 20 tracks × 10 plays = 200 total plays
        george_plays = [
            _make_play(f"george_{i}", artist="GeorgeEzra",
                       play_count=10,
                       last_played=datetime.utcnow() - timedelta(days=10))
            for i in range(20)
        ]
        # Radiohead: 1 track × 200 plays — same total engagement
        radiohead_plays = [
            _make_play("creep", artist="Radiohead",
                       play_count=200,
                       last_played=datetime.utcnow() - timedelta(days=10))
        ]

        db = _make_db(plays=george_plays + radiohead_plays)
        result = rebuild_artist_profiles(db, "user1")

        george_score    = result.get("GeorgeEzra", 0)
        radiohead_score = result.get("Radiohead", 0)

        assert george_score > radiohead_score, (
            f"GeorgeEzra ({george_score:.1f}) should outscore Radiohead "
            f"({radiohead_score:.1f}) when total plays are equal but "
            f"catalogue breadth is much wider."
        )

    def test_breadth_bonus_differentiates_equal_play_counts(self):
        """
        Two artists with identical total plays: the one with more distinct
        tracks played should score higher due to the breadth bonus.
        """
        # Artist A: 1 track × 50 plays
        plays_narrow = [_make_play("a1", artist="Narrow", play_count=50,
                                   last_played=datetime.utcnow() - timedelta(days=10))]
        # Artist B: 10 tracks × 5 plays = 50 total plays
        plays_broad = [
            _make_play(f"b{i}", artist="Broad", play_count=5,
                       last_played=datetime.utcnow() - timedelta(days=10))
            for i in range(10)
        ]

        db = _make_db(plays=plays_narrow + plays_broad)
        result = rebuild_artist_profiles(db, "user1")

        assert result["Broad"] > result["Narrow"], (
            f"Broad ({result['Broad']:.1f}) should outscore "
            f"Narrow ({result['Narrow']:.1f}) at equal total plays."
        )

    def test_best_recency_not_average_recency(self):
        """
        v7 fix: recency uses the most-recently-played track.
        An artist with one very recent track + many old tracks should score
        the same on recency as if all tracks were recent, not a diluted average.
        """
        # Artist with 1 recent track + 9 very old tracks
        plays_mixed = (
            [_make_play("recent", artist="MixedRecency",
                        play_count=5,
                        last_played=datetime.utcnow() - timedelta(days=5))]
            + [_make_play(f"old{i}", artist="MixedRecency",
                          play_count=5,
                          last_played=datetime.utcnow() - timedelta(days=360))
               for i in range(9)]
        )
        # Artist where ALL tracks are recent
        plays_all_recent = [
            _make_play(f"r{i}", artist="AllRecent",
                       play_count=5,
                       last_played=datetime.utcnow() - timedelta(days=5))
            for i in range(10)
        ]

        # Both have same total plays and same track count — only recency differs
        # in naive average, but best-recency should make them equal on that axis.
        db_mixed  = _make_db(plays=plays_mixed)
        db_recent = _make_db(plays=plays_all_recent)

        score_mixed  = rebuild_artist_profiles(db_mixed,  "user1").get("MixedRecency", 0)
        score_recent = rebuild_artist_profiles(db_recent, "user1").get("AllRecent",    0)

        # With best-recency they should be equal (same best date, same plays, same breadth).
        # Allow a small tolerance for floating-point arithmetic.
        assert abs(score_mixed - score_recent) < 1.0, (
            f"MixedRecency ({score_mixed:.2f}) should ≈ AllRecent ({score_recent:.2f}) "
            "because best-recency is used. If this fails, average recency may be leaking back in."
        )

    def test_real_world_ordering_george_over_radiohead(self):
        """
        Regression against the user's exact reported bug:
          - Radiohead: 1 song (Creep) played many times
          - George Ezra: many songs each played many times
        George Ezra must rank higher.
        """
        radiohead = [
            _make_play("creep", artist="Radiohead",
                       play_count=60,
                       last_played=datetime.utcnow() - timedelta(days=3))
        ]
        george = [
            _make_play(f"ge_{i}", artist="GeorgeEzra",
                       play_count=15,
                       last_played=datetime.utcnow() - timedelta(days=3))
            for i in range(30)
        ]

        db = _make_db(plays=radiohead + george)
        result = rebuild_artist_profiles(db, "user1")

        assert result["GeorgeEzra"] > result["Radiohead"], (
            f"GeorgeEzra ({result['GeorgeEzra']:.1f}) should outscore "
            f"Radiohead ({result['Radiohead']:.1f}). "
            "This is the core v7 regression."
        )

    def test_loved_artist_can_reach_near_100(self):
        """
        An artist that dominates the user's listening (most total plays),
        has a wide catalogue, is listened to recently, and is favorited
        should be able to score ≥ 95.
        """
        plays = [
            _make_play(f"t{i}", artist="Beloved",
                       play_count=20,
                       last_played=datetime.utcnow() - timedelta(days=2),
                       is_favorite=True)
            for i in range(40)
        ]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        assert result["Beloved"] >= 95.0, (
            f"Beloved artist scored {result['Beloved']:.1f}, expected ≥ 95. "
            "Full-range scoring may be broken."
        )

    def test_skip_penalty_reduces_affinity(self):
        from models import SkipPenalty
        plays = [_make_play("t1", artist="SkippyArtist", play_count=20)]

        sk = MagicMock(spec=SkipPenalty)
        sk.artist_name = "SkippyArtist"
        sk.genre = None
        sk.jellyfin_item_id = "t1"
        sk.total_events = 10
        sk.skip_count = 8
        sk.consecutive_skips = 0

        db_clean = _make_db(plays=plays)
        db_skip  = _make_db(plays=plays, skip_penalties=[sk])

        clean_score = rebuild_artist_profiles(db_clean, "user1").get("SkippyArtist", 0)
        skip_score  = rebuild_artist_profiles(db_skip,  "user1").get("SkippyArtist", 0)

        assert skip_score < clean_score

    def test_new_library_artist_not_in_results(self):
        """
        An artist with no plays should have zero affinity and should not
        appear in the artist_affinity map at all (clean cold-start).
        """
        # Only plays for Artist A — Artist B is in the library but never played
        plays = [_make_play("t1", artist="ArtistA", play_count=5)]
        db = _make_db(plays=plays)
        result = rebuild_artist_profiles(db, "user1")
        assert "ArtistB" not in result, (
            "Unplayed artists should not appear in artist_affinity. "
            "They will correctly receive 0 affinity in the TrackScore phase."
        )


# ── Unit tests: genre profiles ────────────────────────────────────────────────

class TestGenreProfiles:
    def test_builds_profile_from_plays_fallback(self):
        """
        When no ArtistProfile enrichment is available, rebuild_genre_profiles()
        falls back to norm_genre(Play.genre).  Keys are always normalized
        (lowercase, hyphens→spaces) — never raw Jellyfin strings.

        v9: this tests the Jellyfin-fallback path.  The primary path (Last.fm
        canonical genres) is tested in TestCanonicalGenrePipeline.
        """
        plays = [
            _make_play("t1", genre="Rock",  play_count=20),
            _make_play("t2", genre="Rock",  play_count=15),
            _make_play("t3", genre="Pop",   play_count=3),
        ]
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")
        # Keys are normalized to lowercase (v9: norm_genre applied to Jellyfin fallback)
        assert "rock" in result, f"Expected 'rock' in {list(result.keys())}"
        assert "pop"  in result, f"Expected 'pop' in {list(result.keys())}"
        assert result["rock"] > result["pop"]

    def test_genre_keys_are_always_normalized(self):
        """
        GenreProfile.genre is always lowercase with spaces (normalized).
        No raw Jellyfin strings like 'Classic Rock' or 'Hip-Hop' should appear.
        """
        plays = [
            _make_play("t1", genre="Classic Rock", play_count=10),
            _make_play("t2", genre="Hip-Hop",      play_count=5),
        ]
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")
        for key in result.keys():
            assert key == key.lower(),   f"Genre key '{key}' is not lowercase"
            assert "-" not in key,        f"Genre key '{key}' contains a hyphen"

    def test_scores_are_between_0_and_100(self):
        plays = [_make_play(f"t{i}", genre=f"Genre{i % 3}", play_count=i * 3) for i in range(1, 8)]
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")
        for genre, score in result.items():
            assert 0.0 <= score <= 100.0


# ── Unit tests: track scores ──────────────────────────────────────────────────

class TestTrackScores:
    def test_played_track_scores_above_unplayed(self):
        lib = [
            _make_library_track("played",   artist="X", genre="Rock"),
            _make_library_track("unplayed", artist="X", genre="Rock"),
        ]
        plays = [_make_play("played", artist="X", genre="Rock", play_count=20)]
        db = _make_db(library_tracks=lib, plays=plays)
        added = []
        db.add = lambda obj: added.append(obj)
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
        sk.consecutive_skips = 0
        db_clean = _make_db(library_tracks=lib, plays=plays)
        db_skip  = _make_db(library_tracks=lib, plays=plays, skip_penalties=[sk])
        added_clean, added_skip = [], []
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
        assert fs - ps > 0.5

    def test_high_affinity_unplayed_scores_higher_than_low_affinity(self):
        lib = [
            _make_library_track("loved",   artist="Beatles",    genre="Rock"),
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

    def test_high_affinity_artist_unplayed_benefits_from_v7_affinity(self):
        """
        Because v7 artist affinity now correctly reaches high values,
        unplayed tracks from a heavily-listened artist should get a
        meaningfully higher score than the UNPLAYED_BASE floor.
        """
        lib = [_make_library_track("new_album_track", artist="GeorgeEzra", genre="Pop")]
        db = _make_db(library_tracks=lib, plays=[])
        added = []
        db.add = lambda obj: added.append(obj)
        # George Ezra now correctly has high affinity (e.g. 83)
        rebuild_track_scores(db, "user1", {"GeorgeEzra": 83.0}, {"Pop": 60.0})
        scores = [float(s.final_score) for s in added if hasattr(s, 'final_score')]
        assert scores[0] > UNPLAYED_BASE + 10, (
            f"Unplayed track from high-affinity artist scored {scores[0]:.1f}, "
            f"expected > {UNPLAYED_BASE + 10:.1f}. "
            "This tests that v7 affinity improvements feed correctly into discovery."
        )


# ── Unit tests: recently-added boost (v10) ────────────────────────────────────

class TestRecentlyAddedBoost:
    """
    v10: unplayed tracks added to the library within RECENTLY_ADDED_BOOST_DAYS
    receive a linearly-decaying score lift so fresh library additions surface
    above stale catalog in the same genre.
    """

    def test_recently_added_scores_higher_than_old(self):
        """A track added yesterday scores above an identical track added 200 days ago."""
        lib_new = [_make_library_track("new", artist="Eagles", genre="Rock")]
        lib_new[0].date_added = datetime.utcnow() - timedelta(days=1)

        lib_old = [_make_library_track("old", artist="Eagles", genre="Rock")]
        lib_old[0].date_added = datetime.utcnow() - timedelta(days=200)

        added_new, added_old = [], []
        db_new = _make_db(library_tracks=lib_new, plays=[])
        db_old = _make_db(library_tracks=lib_old, plays=[])
        db_new.add = lambda obj: added_new.append(obj)
        db_old.add = lambda obj: added_old.append(obj)

        rebuild_track_scores(db_new, "user1", {"Eagles": 80.0}, {"Rock": 70.0})
        rebuild_track_scores(db_old, "user1", {"Eagles": 80.0}, {"Rock": 70.0})

        score_new = next(float(s.final_score) for s in added_new if hasattr(s, "final_score"))
        score_old = next(float(s.final_score) for s in added_old if hasattr(s, "final_score"))
        assert score_new > score_old, (
            f"Recently-added track scored {score_new:.2f}, older track scored {score_old:.2f}. "
            "Recently-added track should score higher."
        )

    def test_no_boost_after_window(self):
        """A track added beyond RECENTLY_ADDED_BOOST_DAYS gets no lift over one with no date."""
        lib_past = [_make_library_track("past", artist="Eagles", genre="Rock")]
        lib_past[0].date_added = datetime.utcnow() - timedelta(days=RECENTLY_ADDED_BOOST_DAYS + 1)

        lib_none = [_make_library_track("nodate", artist="Eagles", genre="Rock")]
        lib_none[0].date_added = None

        added_past, added_none = [], []
        db_past = _make_db(library_tracks=lib_past, plays=[])
        db_none = _make_db(library_tracks=lib_none, plays=[])
        db_past.add = lambda obj: added_past.append(obj)
        db_none.add = lambda obj: added_none.append(obj)

        rebuild_track_scores(db_past, "user1", {"Eagles": 80.0}, {"Rock": 70.0})
        rebuild_track_scores(db_none, "user1", {"Eagles": 80.0}, {"Rock": 70.0})

        score_past = next(float(s.final_score) for s in added_past if hasattr(s, "final_score"))
        score_none = next(float(s.final_score) for s in added_none if hasattr(s, "final_score"))
        assert score_past == score_none, (
            f"Track added {RECENTLY_ADDED_BOOST_DAYS + 1} days ago scored {score_past:.2f}, "
            f"track with no date scored {score_none:.2f}. Both should be equal (no boost)."
        )

    def test_boost_decays_with_age(self):
        """A track added 10 days ago scores higher than one added 60 days ago."""
        lib_10 = [_make_library_track("t10", artist="Eagles", genre="Rock")]
        lib_10[0].date_added = datetime.utcnow() - timedelta(days=10)

        lib_60 = [_make_library_track("t60", artist="Eagles", genre="Rock")]
        lib_60[0].date_added = datetime.utcnow() - timedelta(days=60)

        added_10, added_60 = [], []
        db_10 = _make_db(library_tracks=lib_10, plays=[])
        db_60 = _make_db(library_tracks=lib_60, plays=[])
        db_10.add = lambda obj: added_10.append(obj)
        db_60.add = lambda obj: added_60.append(obj)

        rebuild_track_scores(db_10, "user1", {"Eagles": 80.0}, {"Rock": 70.0})
        rebuild_track_scores(db_60, "user1", {"Eagles": 80.0}, {"Rock": 70.0})

        score_10 = next(float(s.final_score) for s in added_10 if hasattr(s, "final_score"))
        score_60 = next(float(s.final_score) for s in added_60 if hasattr(s, "final_score"))
        assert score_10 > score_60, (
            f"10-day-old track scored {score_10:.2f}, 60-day-old scored {score_60:.2f}. "
            "Boost should decay with age."
        )

    def test_boost_never_exceeds_unplayed_cap(self):
        """The recently-added boost must not push the final score above UNPLAYED_CAP."""
        lib = [_make_library_track("t1", artist="Beatles", genre="Rock")]
        lib[0].date_added = datetime.utcnow()  # added today — maximum boost

        added = []
        db = _make_db(library_tracks=lib, plays=[])
        db.add = lambda obj: added.append(obj)
        rebuild_track_scores(db, "user1", {"Beatles": 100.0}, {"Rock": 100.0})
        scores = [float(s.final_score) for s in added if hasattr(s, "final_score")]
        assert all(s <= UNPLAYED_CAP for s in scores), (
            f"Score(s) exceeded UNPLAYED_CAP={UNPLAYED_CAP}: {scores}"
        )


# ── Unit tests: library scanner ───────────────────────────────────────────────

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


# ── Unit tests: _derive_canonical_genres (v9) ────────────────────────────────

class TestDeriveCanonicalGenres:
    """
    Tests for the canonical genre derivation helper.
    This function is the single point where Last.fm tags are converted into
    the authoritative weighted genre list for an artist.
    """

    def test_valid_lastfm_tags_preferred_over_jellyfin(self):
        """Last.fm tags should override the Jellyfin file-tag fallback."""
        result = _derive_canonical_genres('["hip hop", "rap"]', "Pop")
        genres = [g["genre"] for g in result]
        assert "hip hop" in genres
        assert "pop" not in genres, "Jellyfin 'Pop' should not appear when Last.fm tags exist"

    def test_position_decay_weighting(self):
        """tag[0] gets 50% weight; tag[1] gets 25%; weights renormalize to sum=1."""
        result = _derive_canonical_genres('["hip hop", "rap"]', "")
        assert len(result) == 2
        # After renormalization: 0.50/(0.50+0.25) ≈ 0.667, 0.25/0.75 ≈ 0.333
        assert result[0]["genre"] == "hip hop"
        assert result[0]["weight"] > result[1]["weight"]

    def test_weights_sum_to_one(self):
        result = _derive_canonical_genres('["hip hop", "rap", "southern hip hop", "r&b", "crunk"]', "")
        total = sum(g["weight"] for g in result)
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_junk_tags_filtered_out(self):
        """Non-genre social tags must be excluded before weighting."""
        tags = '["seen live", "favorites", "hip hop", "2000s"]'
        result = _derive_canonical_genres(tags, "")
        genres = [g["genre"] for g in result]
        assert "seen live" not in genres
        assert "favorites" not in genres
        assert "2000s" not in genres
        assert "hip hop" in genres

    def test_all_junk_falls_back_to_jellyfin(self):
        """When every Last.fm tag is junk, use the Jellyfin genre as fallback."""
        tags = '["seen live", "favorites", "2000s"]'
        result = _derive_canonical_genres(tags, "Rock")
        assert len(result) == 1
        assert result[0]["genre"] == "rock"  # norm_genre("Rock")

    def test_fallback_to_jellyfin_when_no_lastfm(self):
        """No Last.fm data → fall back to Jellyfin genre (normalized)."""
        result = _derive_canonical_genres(None, "Classic Rock")
        assert len(result) == 1
        assert result[0]["genre"] == "classic rock"
        assert result[0]["weight"] == 1.0

    def test_empty_returns_empty(self):
        """No tags, no Jellyfin genre → return []."""
        assert _derive_canonical_genres(None, "") == []
        assert _derive_canonical_genres("[]", "") == []

    def test_tags_normalized_to_lowercase_and_spaces(self):
        """Tags like 'Hip-Hop' should be normalized to 'hip hop'."""
        result = _derive_canonical_genres('["Hip-Hop", "R&B"]', "")
        genres = [g["genre"] for g in result]
        assert "hip hop" in genres
        assert "r&b" in genres
        assert "Hip-Hop" not in genres

    def test_invalid_json_falls_back_gracefully(self):
        """Bad JSON in tags field should not crash — fall back to Jellyfin."""
        result = _derive_canonical_genres("not-valid-json", "Jazz")
        assert result[0]["genre"] == "jazz"

    def test_up_to_five_tags_accepted(self):
        """Only the first 5 tags are considered (Last.fm max)."""
        tags = '["rock", "pop", "jazz", "blues", "folk", "country", "metal"]'
        result = _derive_canonical_genres(tags, "")
        # All 7 non-junk tags exist, but only first 5 should be used
        assert len(result) <= 5

    def test_ludacris_real_world(self):
        """
        Real-world regression: Ludacris was previously classified as 'Pop'
        by Jellyfin.  With Last.fm tags, he should be 'hip hop' dominant.
        """
        tags = '["hip hop", "rap", "southern hip hop", "crunk", "r&b"]'
        result = _derive_canonical_genres(tags, "Pop")
        assert result[0]["genre"] == "hip hop", (
            f"Ludacris dominant genre should be 'hip hop', got '{result[0]['genre']}'"
        )
        genres = [g["genre"] for g in result]
        assert "pop" not in genres, "Jellyfin 'Pop' should not appear with valid Last.fm tags"


# ── Unit tests: _dominant_genre (v9) ─────────────────────────────────────────

class TestDominantGenre:

    def test_empty_list_returns_empty_string(self):
        assert _dominant_genre([]) == ""

    def test_prefers_genre_adjacency_validated_entry(self):
        """
        If only the second entry is in GENRE_ADJACENCY, it should be preferred
        over the first entry (which may be a Last.fm subgenre not in the map).
        """
        # "southern hip hop" might not be in GENRE_ADJACENCY; "hip hop" is.
        genres = [
            {"genre": "southern hip hop", "weight": 0.50},
            {"genre": "hip hop",          "weight": 0.25},
        ]
        result = _dominant_genre(genres)
        assert result == "hip hop", (
            "Should prefer the GENRE_ADJACENCY-validated entry even if it's not first"
        )

    def test_falls_back_to_first_when_none_in_adjacency(self):
        """If no entry is in GENRE_ADJACENCY, return the first entry's genre."""
        genres = [
            {"genre": "obscure subgenre xyz", "weight": 0.60},
            {"genre": "another unknown",       "weight": 0.40},
        ]
        result = _dominant_genre(genres)
        assert result == "obscure subgenre xyz"

    def test_returns_first_adjacency_match_not_highest_weight(self):
        """Validation against GENRE_ADJACENCY takes priority over weight order."""
        genres = [
            {"genre": "jazz",     "weight": 0.90},  # in GENRE_ADJACENCY
            {"genre": "blues",    "weight": 0.10},  # also in GENRE_ADJACENCY
        ]
        # jazz is first AND highest weight — should win
        assert _dominant_genre(genres) == "jazz"


# ── Integration tests: canonical genre pipeline (v9) ────────────────────────

class TestCanonicalGenrePipeline:
    """
    End-to-end tests verifying that Last.fm-sourced canonical genres flow
    correctly through rebuild_genre_profiles() and rebuild_track_scores().

    These tests are the primary regression guard for the v9 genre overhaul.
    They verify the fix for the core data quality problem:
      - Before: Jellyfin file-tags (e.g., "Pop" for Ludacris) drove everything
      - After:  Last.fm artist tags drive genre profiles, track scores, and
                all downstream playlist/discovery/insights features
    """

    def test_genre_profile_uses_canonical_genres_not_jellyfin(self):
        """
        When ArtistProfile has Last.fm canonical genres, GenreProfile should
        aggregate by those genres — NOT by Play.genre (Jellyfin file-tag).

        Simulates: Ludacris filed as 'Pop' by Jellyfin but tagged 'hip hop'
        on Last.fm.  GenreProfile must show 'hip hop', not 'pop'.
        """
        plays = [
            _make_play("t1", artist="Ludacris", genre="Pop", play_count=10),
            _make_play("t2", artist="Ludacris", genre="Pop", play_count=5),
        ]
        # Inject ArtistProfile with Last.fm canonical genres
        aps = [
            _make_artist_profile(
                "Ludacris",
                primary_genre="hip hop",
                canonical_genres=[
                    {"genre": "hip hop", "weight": 0.67},
                    {"genre": "rap",     "weight": 0.33},
                ],
            )
        ]
        db = _make_db(plays=plays, artist_profiles=aps)
        result = rebuild_genre_profiles(db, "user1")

        assert "hip hop" in result, (
            f"Expected 'hip hop' in genre profiles, got: {list(result.keys())}. "
            "Jellyfin 'Pop' tag is poisoning the genre profiles."
        )
        assert "pop" not in result, (
            "Jellyfin 'Pop' file-tag must not appear in genre profiles "
            "when artist has Last.fm canonical genres."
        )

    def test_fractional_play_credit_across_genres(self):
        """
        A blended artist (e.g., Ludacris: hip-hop 50%, r&b 25%, rap 25%)
        should credit plays fractionally.  After 10 plays:
          hip hop gets 5.0 credits, r&b gets 2.5, rap gets 2.5.
        Both genres should appear in GenreProfile, with hip-hop having
        higher affinity than r&b (more credit).
        """
        plays = [_make_play("t1", artist="Ludacris", genre="Pop", play_count=10)]
        aps = [
            _make_artist_profile(
                "Ludacris",
                primary_genre="hip hop",
                canonical_genres=[
                    {"genre": "hip hop", "weight": 0.50},
                    {"genre": "r&b",     "weight": 0.25},
                    {"genre": "rap",     "weight": 0.25},
                ],
            )
        ]
        db = _make_db(plays=plays, artist_profiles=aps)
        result = rebuild_genre_profiles(db, "user1")

        assert "hip hop" in result
        assert "r&b"     in result
        assert "rap"     in result
        assert result["hip hop"] > result["r&b"], (
            "hip hop (50% weight) should have higher affinity than r&b (25% weight)"
        )

    def test_track_score_genre_uses_artist_primary_genre(self):
        """
        TrackScore.genre must be the canonical primary genre from ArtistProfile,
        NOT the Jellyfin file-tag from LibraryTrack.genre.

        This is the core fix for the genre fracture: Ludacris tracks must be
        labelled 'hip hop' in TrackScore so they surface in hip-hop playlist
        blocks and genre-adjacent discovery — not in 'pop' blocks.
        """
        lib = [_make_library_track("t1", artist="Ludacris", genre="Pop")]
        plays = [_make_play("t1", artist="Ludacris", genre="Pop", play_count=5)]
        aps = [
            _make_artist_profile(
                "Ludacris",
                primary_genre="hip hop",
                canonical_genres=[{"genre": "hip hop", "weight": 1.0}],
            )
        ]
        db = _make_db(library_tracks=lib, plays=plays, artist_profiles=aps)
        added = []
        db.add = lambda obj: added.append(obj)

        rebuild_track_scores(db, "user1", {"Ludacris": 80.0}, {"hip hop": 75.0})

        track_scores = [s for s in added if hasattr(s, "genre")]
        assert len(track_scores) > 0
        assert track_scores[0].genre == "hip hop", (
            f"TrackScore.genre should be 'hip hop' (canonical), got '{track_scores[0].genre}'. "
            "Jellyfin 'Pop' file-tag is leaking into TrackScore."
        )

    def test_genre_affinity_lookup_consistent_with_genre_profile(self):
        """
        The fracture fix: genre_affinity dict keys (from rebuild_genre_profiles)
        and TrackScore.genre (from rebuild_track_scores) must use the same
        normalized canonical strings so the lookup doesn't always miss.

        Before v9: GenreProfile had 'Pop' (Jellyfin) but recommender compared
        Last.fm tags like 'hip hop' against it — constant misses → neutral 50.0.
        After v9: both sides use normalized canonical genres → consistent matches.
        """
        plays = [_make_play("t1", artist="Ludacris", genre="Pop", play_count=10)]
        aps = [
            _make_artist_profile(
                "Ludacris",
                primary_genre="hip hop",
                canonical_genres=[{"genre": "hip hop", "weight": 1.0}],
            )
        ]
        db = _make_db(plays=plays, artist_profiles=aps)

        # Step 1: build genre profiles (returns canonical genre keys)
        genre_aff = rebuild_genre_profiles(db, "user1")

        # Step 2: genre_aff should have 'hip hop', not 'pop'
        assert "hip hop" in genre_aff, (
            f"genre_affinity should have 'hip hop', got {list(genre_aff.keys())}"
        )

        # Step 3: track scores should look up 'hip hop' and find it
        lib = [_make_library_track("t1", artist="Ludacris", genre="Pop")]
        db2 = _make_db(library_tracks=lib, plays=plays, artist_profiles=aps)
        added = []
        db2.add = lambda obj: added.append(obj)
        rebuild_track_scores(db2, "user1", {"Ludacris": 80.0}, genre_aff)

        track_scores = [s for s in added if hasattr(s, "genre_affinity")]
        assert len(track_scores) > 0
        # genre_affinity should be > 0 (lookup succeeded) rather than 0.0 (miss)
        assert float(track_scores[0].genre_affinity) > 0.0, (
            "genre_affinity is 0.0 — the canonical genre lookup is missing. "
            "This indicates the v9 fracture fix is not working correctly."
        )

    def test_unenriched_artist_falls_back_to_jellyfin_genre(self):
        """
        Artists with no Last.fm enrichment (canonical_genres=None) must fall
        back to norm_genre(Play.genre) rather than producing no genre at all.
        This ensures the system still works for artists that haven't been enriched.
        """
        plays = [_make_play("t1", artist="UnknownArtist", genre="Country", play_count=5)]
        # No artist_profiles → empty → triggers fallback path
        db = _make_db(plays=plays)
        result = rebuild_genre_profiles(db, "user1")

        assert "country" in result, (
            f"Unenriched artist should fall back to norm_genre('Country')='country'. "
            f"Got: {list(result.keys())}"
        )

    def test_mixed_enriched_and_unenriched_artists(self):
        """
        A realistic scenario: some artists have Last.fm data, some don't.
        Both enriched canonical genres and Jellyfin fallback genres should
        appear in GenreProfile without interfering with each other.
        """
        plays = [
            _make_play("t1", artist="Ludacris",       genre="Pop",     play_count=10),
            _make_play("t2", artist="UnknownCountry",  genre="Country", play_count=5),
        ]
        aps = [
            _make_artist_profile(
                "Ludacris",
                primary_genre="hip hop",
                canonical_genres=[{"genre": "hip hop", "weight": 1.0}],
            )
            # UnknownCountry has no profile → falls back to Jellyfin "Country"
        ]
        db = _make_db(plays=plays, artist_profiles=aps)
        result = rebuild_genre_profiles(db, "user1")

        assert "hip hop" in result, "Enriched artist should contribute canonical genre"
        assert "country" in result, "Unenriched artist should fall back to Jellyfin genre"
        assert "pop"     not in result, "Jellyfin 'Pop' tag must not appear for enriched artist"
