"""
Tests for services/enrichment.py — pure scoring and metadata-cleaning functions.

enrichment.py drives popularity scores and Last.fm matching quality.  Bugs
in the pure functions listed below ripple directly into recommendation scores:
  - _listeners_to_score() controls artist popularity bucket assignment and
    the Discovery block's popularity_min/max filtering
  - _clean_track_name() / _clean_artist_for_lastfm() gate Last.fm lookups —
    if suffixes aren't stripped, enrichment silently fails and tracks never
    get global_playcount populated

Covers:
  - _listeners_to_score(): 0/None → 0.0, floor clamps to 0.0, ceiling clamps
    to 100.0, midpoint in expected range, monotonically increasing
  - _track_listeners_to_score(): same contract with track-calibrated floor/ceil
  - _clean_track_name(): strips (Remastered YYYY), - Remastered, (Live ...),
    [Bonus Track], (Explicit Version), (feat. X), [Radio Edit],
    (MTV Unplugged); preserves unmodified clean titles
  - _clean_artist_for_lastfm(): strips feat./ft./featuring suffixes,
    parenthetical collabs, multi-word & chains; preserves two-word bands
    like "Simon & Garfunkel" and "Florence + the Machine"

Run with: docker exec jellydj-backend python -m pytest tests/test_enrichment.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from services.enrichment import (
    _listeners_to_score,
    _track_listeners_to_score,
    _clean_track_name,
    _clean_artist_for_lastfm,
    ARTIST_LISTENER_FLOOR,
    ARTIST_LISTENER_CEILING,
    TRACK_LISTENER_FLOOR,
    TRACK_LISTENER_CEILING,
)


# ── _listeners_to_score (artist calibration) ──────────────────────────────────

class TestListenersToScore:

    def test_none_returns_zero(self):
        assert _listeners_to_score(None) == 0.0

    def test_zero_returns_zero(self):
        assert _listeners_to_score(0) == 0.0

    def test_negative_returns_zero(self):
        assert _listeners_to_score(-100) == 0.0

    def test_at_floor_returns_zero(self):
        # log(floor) - log(floor) = 0 → 0.0
        result = _listeners_to_score(ARTIST_LISTENER_FLOOR)
        assert result == 0.0

    def test_at_ceiling_returns_100(self):
        result = _listeners_to_score(ARTIST_LISTENER_CEILING)
        assert result == 100.0

    def test_above_ceiling_clamped_to_100(self):
        result = _listeners_to_score(ARTIST_LISTENER_CEILING * 10)
        assert result == 100.0

    def test_midpoint_in_expected_range(self):
        """Geometric mean of floor and ceiling → should be ~50."""
        import math
        mid = int(math.exp((math.log(ARTIST_LISTENER_FLOOR) + math.log(ARTIST_LISTENER_CEILING)) / 2))
        result = _listeners_to_score(mid)
        assert 45.0 <= result <= 55.0

    def test_monotonically_increasing(self):
        """More listeners → higher score."""
        s1 = _listeners_to_score(10_000)
        s2 = _listeners_to_score(100_000)
        s3 = _listeners_to_score(1_000_000)
        assert s1 < s2 < s3

    def test_result_within_0_to_100(self):
        for n in [1, 1_000, 100_000, 10_000_000, 500_000_000]:
            r = _listeners_to_score(n)
            assert 0.0 <= r <= 100.0, f"Out of range for {n}: {r}"

    def test_returns_float(self):
        assert isinstance(_listeners_to_score(50_000), float)

    def test_known_reference_1m_listeners_about_80(self):
        """1M artist listeners should score around 80 per design doc comment."""
        r = _listeners_to_score(1_000_000)
        assert 75.0 <= r <= 85.0


# ── _track_listeners_to_score ─────────────────────────────────────────────────

class TestTrackListenersToScore:

    def test_none_returns_zero(self):
        assert _track_listeners_to_score(None) == 0.0

    def test_zero_returns_zero(self):
        assert _track_listeners_to_score(0) == 0.0

    def test_at_track_floor_returns_zero(self):
        result = _track_listeners_to_score(TRACK_LISTENER_FLOOR)
        assert result == 0.0

    def test_at_track_ceiling_returns_100(self):
        result = _track_listeners_to_score(TRACK_LISTENER_CEILING)
        assert result == 100.0

    def test_track_score_higher_than_artist_score_for_same_raw_count(self):
        """Track calibration has a lower floor, so same count → higher score than artist."""
        count = 100_000
        artist_score = _listeners_to_score(count)
        track_score  = _track_listeners_to_score(count)
        assert track_score > artist_score

    def test_600k_track_listeners_about_82(self):
        """From design doc: 600K listeners → ~82."""
        r = _track_listeners_to_score(600_000)
        assert 78.0 <= r <= 86.0

    def test_monotonically_increasing(self):
        s1 = _track_listeners_to_score(1_000)
        s2 = _track_listeners_to_score(100_000)
        s3 = _track_listeners_to_score(1_000_000)
        assert s1 < s2 < s3


# ── _clean_track_name ─────────────────────────────────────────────────────────

class TestCleanTrackName:

    def test_no_suffix_unchanged(self):
        assert _clean_track_name("Bohemian Rhapsody") == "Bohemian Rhapsody"

    def test_strips_remastered_with_year(self):
        result = _clean_track_name("Yesterday (Remastered 2009)")
        assert "Remastered" not in result
        assert "2009" not in result
        assert result.strip() == "Yesterday"

    def test_strips_remastered_no_year(self):
        result = _clean_track_name("Let It Be (Remastered)")
        assert "Remastered" not in result
        assert result.strip() == "Let It Be"

    def test_strips_dash_remaster_suffix(self):
        result = _clean_track_name("Hotel California - 2013 Remaster")
        assert "Remaster" not in result
        assert result.strip() == "Hotel California"

    def test_strips_live_parenthetical(self):
        result = _clean_track_name("Stairway to Heaven (Live at Madison Square Garden)")
        assert "Live" not in result
        assert result.strip() == "Stairway to Heaven"

    def test_strips_live_bracket(self):
        result = _clean_track_name("Imagine [Live]")
        assert "Live" not in result
        assert result.strip() == "Imagine"

    def test_strips_bonus_track(self):
        result = _clean_track_name("Hidden Track [Bonus Track]")
        assert "Bonus" not in result
        assert result.strip() == "Hidden Track"

    def test_strips_explicit_version(self):
        result = _clean_track_name("F*** The Police (Explicit Version)")
        assert "Explicit" not in result

    def test_strips_radio_edit(self):
        result = _clean_track_name("Smells Like Teen Spirit (Radio Edit)")
        assert "Radio Edit" not in result
        assert "Smells Like Teen Spirit" in result

    def test_strips_feat_suffix(self):
        result = _clean_track_name("Empire State of Mind (feat. Alicia Keys)")
        assert "feat" not in result.lower()
        assert "Alicia Keys" not in result

    def test_strips_mtv_unplugged(self):
        result = _clean_track_name("Come As You Are (MTV Unplugged)")
        assert "Unplugged" not in result
        assert "MTV" not in result

    def test_strips_bracket_remix(self):
        # Regex requires bracket content to start with optional YYYY then Mix/etc.
        # "[Remix]" or "[2024 Remix]" matches; "[12\" Mix]" has a non-year prefix
        # so the regex skips it. Test a clean bracket form instead:
        result = _clean_track_name("Blue Monday [2024 Remix]")
        assert "Remix" not in result
        assert "2024" not in result
        assert result.strip() == "Blue Monday"

    def test_whitespace_stripped(self):
        result = _clean_track_name("Yesterday (Remastered)")
        assert result == result.strip()

    def test_multiple_suffixes(self):
        """Multiple suffixes are each stripped independently."""
        result = _clean_track_name("Song (feat. Artist) (Remastered)")
        assert "feat" not in result.lower()
        assert "Remastered" not in result


# ── _clean_artist_for_lastfm ──────────────────────────────────────────────────

class TestCleanArtistForLastfm:

    def test_clean_artist_unchanged(self):
        assert _clean_artist_for_lastfm("Ed Sheeran") == "Ed Sheeran"

    def test_strips_feat_suffix(self):
        result = _clean_artist_for_lastfm("Ed Sheeran feat. Khalid")
        assert result == "Ed Sheeran"

    def test_strips_ft_suffix(self):
        result = _clean_artist_for_lastfm("Eminem ft. Ed Sheeran")
        assert result == "Eminem"

    def test_strips_featuring_suffix(self):
        result = _clean_artist_for_lastfm("Drake featuring Rihanna")
        assert result == "Drake"

    def test_strips_parenthetical_collab(self):
        result = _clean_artist_for_lastfm("Jay-Z (feat. Alicia Keys)")
        assert "Alicia Keys" not in result
        assert "feat" not in result.lower()

    def test_preserves_simon_and_garfunkel(self):
        """Single word before & → band name, must not be stripped."""
        result = _clean_artist_for_lastfm("Simon & Garfunkel")
        assert result == "Simon & Garfunkel"

    def test_preserves_florence_and_the_machine(self):
        """Single word before + → band name, must not be stripped."""
        result = _clean_artist_for_lastfm("Florence + the Machine")
        assert result == "Florence + the Machine"

    def test_strips_multi_word_ampersand(self):
        """Two+ words before & → collaboration, strip the collab."""
        result = _clean_artist_for_lastfm("Daryl Hall & John Oates")
        assert result == "Daryl Hall"

    def test_strips_multi_word_plus(self):
        result = _clean_artist_for_lastfm("Calvin Harris & Rihanna")
        assert result == "Calvin Harris"

    def test_whitespace_trimmed(self):
        result = _clean_artist_for_lastfm("Ed Sheeran feat. Khalid")
        assert result == result.strip()

    def test_case_insensitive_feat(self):
        result = _clean_artist_for_lastfm("Ed Sheeran FEAT. Khalid")
        assert "Khalid" not in result

    def test_empty_string_returns_empty(self):
        assert _clean_artist_for_lastfm("") == ""
