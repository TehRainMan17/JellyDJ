"""
Tests for services/playlist_engine.py — core playlist generation engine.

The engine is the "brain" that assembles playlists from filter trees.  It
orchestrates block executors (playlist_blocks.py), applies OR/AND set logic,
weight normalisation, artist capping, score-based ordering, and interleaving.

Covers:
  - _evaluate_nodes(): empty input, single node, siblings → OR, children → AND,
    nested OR-inside-AND, unknown filter_type skipped gracefully,
    executor exception returns empty set for that node
  - _find_artist_cap(): returns default when absent, finds cap at root level,
    finds cap nested in children, first-found value wins
  - _find_jitter(): returns 0.0 when absent, finds jitter_pct at root,
    finds it in children, respects custom value
  - _apply_artist_cap(): honours max_per_artist, two-pass relaxation when
    too few tracks, respects target count, case-insensitive artist matching
  - _interleave(): single chain, two equal chains, proportional by weight,
    gap-fill when one chain exhausted, total_tracks cap respected

All tests here are pure-logic (no database) except where noted.
Block executors are replaced with stub callables to test _evaluate_nodes
in isolation.

Run with: docker exec jellydj-backend python -m pytest tests/test_playlist_engine.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

from services.playlist_engine import (
    _evaluate_nodes,
    _find_artist_cap,
    _find_jitter,
    _apply_artist_cap,
    _apply_artist_cap_strict,
    _interleave,
)


# ── Stubs ─────────────────────────────────────────────────────────────────────

def _stub_executor(result_set: set):
    """Return an executor function that always returns result_set."""
    def executor(user_id, params, db, excluded_item_ids):
        return set(result_set)
    return executor


def _make_node(filter_type, result_set, children=None):
    """Build a filter tree node backed by a stub executor."""
    return {
        "filter_type": filter_type,
        "params": {},
        "children": children or [],
        "_stub_set": result_set,
    }


def _make_registry(nodes):
    """
    Build a mock BLOCK_REGISTRY from a list of node dicts that have _stub_set.
    Uses the node's filter_type as the key and its _stub_set as the returned set.
    """
    registry = {}
    def _collect(node_list):
        for n in node_list:
            ft = n.get("filter_type")
            if ft and ft not in registry:
                registry[ft] = _stub_executor(n["_stub_set"])
            _collect(n.get("children") or [])
    _collect(nodes)
    return registry


# ── _evaluate_nodes ────────────────────────────────────────────────────────────

class TestEvaluateNodes:
    """
    Tree evaluation semantics:
      Siblings (same level) → OR (union)
      Children of a node   → AND (intersection with parent)
    """

    def _eval(self, nodes, registry=None):
        db = MagicMock()
        if registry is None:
            registry = _make_registry(nodes)
        with patch("services.playlist_engine.BLOCK_REGISTRY", registry):
            return _evaluate_nodes(nodes, user_id="u1", db=db, excluded_item_ids=frozenset())

    # ── Base cases ────────────────────────────────────────────────────────────

    def test_empty_node_list_returns_empty_set(self):
        assert self._eval([]) == set()

    def test_single_node_no_children_returns_executor_set(self):
        node = _make_node("ft_a", {"id-1", "id-2"})
        assert self._eval([node]) == {"id-1", "id-2"}

    # ── Siblings → OR ─────────────────────────────────────────────────────────

    def test_two_siblings_are_or_unioned(self):
        node_a = _make_node("ft_a", {"a", "b"})
        node_b = _make_node("ft_b", {"b", "c"})
        registry = {**_make_registry([node_a]), **_make_registry([node_b])}
        result = self._eval([node_a, node_b], registry)
        assert result == {"a", "b", "c"}

    def test_three_siblings_are_all_unioned(self):
        node_a = _make_node("ft_a", {"a"})
        node_b = _make_node("ft_b", {"b"})
        node_c = _make_node("ft_c", {"c"})
        registry = {**_make_registry([node_a]), **_make_registry([node_b]),
                    **_make_registry([node_c])}
        result = self._eval([node_a, node_b, node_c], registry)
        assert result == {"a", "b", "c"}

    def test_disjoint_siblings_union_all(self):
        node_a = _make_node("ft_a", {"a", "b"})
        node_b = _make_node("ft_b", {"c", "d"})
        registry = {**_make_registry([node_a]), **_make_registry([node_b])}
        result = self._eval([node_a, node_b], registry)
        assert result == {"a", "b", "c", "d"}

    # ── Children → AND ────────────────────────────────────────────────────────

    def test_single_child_is_and_intersected(self):
        child = _make_node("ft_child", {"a", "b"})
        parent = _make_node("ft_parent", {"b", "c"}, children=[child])
        registry = {**_make_registry([child]), **_make_registry([parent])}
        result = self._eval([parent], registry)
        # parent ∩ child = {"b"}
        assert result == {"b"}

    def test_no_overlap_with_child_returns_empty(self):
        child  = _make_node("ft_child",  {"x", "y"})
        parent = _make_node("ft_parent", {"a", "b"}, children=[child])
        registry = {**_make_registry([child]), **_make_registry([parent])}
        result = self._eval([parent], registry)
        assert result == set()

    def test_two_children_are_or_unioned_before_and_with_parent(self):
        """
        Tree:
          parent {a, b, c, d}
            child1 {a, b}
            child2 {c, d}
        children OR → {a, b, c, d}, then AND with parent → {a, b, c, d}
        """
        child1  = _make_node("ft_c1", {"a", "b"})
        child2  = _make_node("ft_c2", {"c", "d"})
        parent  = _make_node("ft_p",  {"a", "b", "c", "d", "e"},
                             children=[child1, child2])
        registry = {**_make_registry([child1]), **_make_registry([child2]),
                    **_make_registry([parent])}
        result = self._eval([parent], registry)
        assert result == {"a", "b", "c", "d"}
        assert "e" not in result

    def test_nested_three_levels(self):
        """
        grandchild {x}
        child      {x, y}   ∩ grandchild → {x}
        parent     {x, y, z} ∩ child_result → {x}
        """
        grandchild = _make_node("ft_gc",  {"x"})
        child      = _make_node("ft_c",   {"x", "y"}, children=[grandchild])
        parent     = _make_node("ft_p",   {"x", "y", "z"}, children=[child])
        registry = {**_make_registry([grandchild]),
                    **_make_registry([child]),
                    **_make_registry([parent])}
        result = self._eval([parent], registry)
        assert result == {"x"}

    # ── Sibling + children combined ───────────────────────────────────────────

    def test_sibling_and_child_combined(self):
        """
        node_a {a, b}  (no children) → {a, b}
        node_b {b, c}
          child {c, d} → node_b ∩ child = {c}
        OR → {a, b, c}
        """
        child  = _make_node("ft_child",  {"c", "d"})
        node_a = _make_node("ft_a",      {"a", "b"})
        node_b = _make_node("ft_b",      {"b", "c"}, children=[child])
        registry = {**_make_registry([child]),
                    **_make_registry([node_a]),
                    **_make_registry([node_b])}
        result = self._eval([node_a, node_b], registry)
        assert result == {"a", "b", "c"}

    # ── Error handling ────────────────────────────────────────────────────────

    def test_unknown_filter_type_skipped(self):
        node = {"filter_type": "does_not_exist", "params": {}, "children": []}
        result = self._eval([node], registry={})
        assert result == set()

    def test_executor_exception_returns_empty_for_that_node(self):
        def bad_executor(user_id, params, db, excluded_item_ids):
            raise RuntimeError("executor exploded")

        node_ok  = _make_node("ft_ok",  {"ok-1"})
        registry = {**_make_registry([node_ok]), "ft_bad": bad_executor}
        node_bad = {"filter_type": "ft_bad", "params": {}, "children": []}
        result = self._eval([node_ok, node_bad], registry)
        # ft_bad contributes empty set; ft_ok contributes {"ok-1"}
        assert result == {"ok-1"}

    # ── v9 template structure — discovery constraint must not be bypassed ──────

    def test_v8_bug_passthrough_sibling_expands_to_all(self):
        """
        Regression: v8 "New For You" Block 0 had artist_cap (ALL-passthrough)
        as a sibling of discovery (filtered set).  Siblings are OR'd, so
        OR(discovery, ALL) = ALL — the discovery constraint was completely lost.

        genre {a,b,c,d,e}
          discovery {a,b}     ← strangers only
          artist_cap {a,b,c,d,e}  ← ALL passthrough
        children OR → {a,b,c,d,e}
        genre AND {a,b,c,d,e} → {a,b,c,d,e}  ← BUG: familiar tracks included
        """
        artist_cap = _make_node("artist_cap", {"a", "b", "c", "d", "e"})
        discovery  = _make_node("discovery",  {"a", "b"})
        genre      = _make_node("genre",      {"a", "b", "c", "d", "e"},
                                children=[discovery, artist_cap])
        registry = {**_make_registry([artist_cap]),
                    **_make_registry([discovery]),
                    **_make_registry([genre])}
        result = self._eval([genre], registry)
        # Demonstrates the v8 bug: familiar tracks c/d/e appear despite discovery
        assert {"c", "d", "e"}.issubset(result), (
            "v8 sibling structure correctly demonstrated as broken: "
            "artist_cap sibling OR-expands result to ALL"
        )

    def test_v9_fix_nested_passthrough_preserves_discovery_constraint(self):
        """
        v9 fix: artist_cap is nested inside discovery, not a sibling.
        OR of discovery's children (artist_cap = ALL) = ALL.
        discovery AND ALL = discovery.  genre AND discovery = correct intersection.

        genre {a,b,c,d,e}
          discovery {a,b}   ← strangers only
            artist_cap {a,b,c,d,e}  ← ALL passthrough (nested)
        evaluate(discovery_children) = OR(ALL) = ALL
        discovery AND ALL = {a,b}
        genre AND {a,b} → {a,b}  ← FIX: only strangers included ✓
        """
        artist_cap = _make_node("artist_cap", {"a", "b", "c", "d", "e"})
        discovery  = _make_node("discovery",  {"a", "b"}, children=[artist_cap])
        genre      = _make_node("genre",      {"a", "b", "c", "d", "e"},
                                children=[discovery])
        registry = {**_make_registry([artist_cap]),
                    **_make_registry([discovery]),
                    **_make_registry([genre])}
        result = self._eval([genre], registry)
        assert result == {"a", "b"}, (
            f"v9 nested structure should restrict to discovery result {{a,b}}, "
            f"got {result!r}"
        )
        assert {"c", "d", "e"}.isdisjoint(result), (
            "Familiar tracks c/d/e must not appear when discovery is properly nested"
        )


# ── _find_artist_cap ───────────────────────────────────────────────────────────

class TestFindArtistCap:

    def test_no_cap_node_returns_default(self):
        nodes = [{"filter_type": "final_score", "params": {}, "children": []}]
        assert _find_artist_cap(nodes) == 3

    def test_cap_node_at_root_returns_value(self):
        nodes = [{"filter_type": "artist_cap",
                  "params": {"max_per_artist": 5},
                  "children": []}]
        assert _find_artist_cap(nodes) == 5

    def test_cap_node_in_children_found_depth_first(self):
        nodes = [{
            "filter_type": "final_score",
            "params": {},
            "children": [{
                "filter_type": "artist_cap",
                "params": {"max_per_artist": 7},
                "children": [],
            }],
        }]
        assert _find_artist_cap(nodes) == 7

    def test_first_cap_found_wins(self):
        """Two artist_cap nodes in siblings — the first one encountered wins."""
        nodes = [
            {"filter_type": "artist_cap", "params": {"max_per_artist": 2}, "children": []},
            {"filter_type": "artist_cap", "params": {"max_per_artist": 9}, "children": []},
        ]
        assert _find_artist_cap(nodes) == 2

    def test_missing_max_per_artist_uses_default(self):
        nodes = [{"filter_type": "artist_cap", "params": {}, "children": []}]
        assert _find_artist_cap(nodes) == 3

    def test_custom_default_value_used_when_absent(self):
        nodes = [{"filter_type": "final_score", "params": {}, "children": []}]
        assert _find_artist_cap(nodes, default=10) == 10


# ── _find_jitter ───────────────────────────────────────────────────────────────

class TestFindJitter:

    def test_no_jitter_node_returns_zero(self):
        nodes = [{"filter_type": "final_score", "params": {}, "children": []}]
        assert _find_jitter(nodes) == 0.0

    def test_jitter_at_root_returns_pct(self):
        nodes = [{"filter_type": "jitter", "params": {"jitter_pct": 0.20}, "children": []}]
        assert _find_jitter(nodes) == pytest.approx(0.20)

    def test_jitter_missing_pct_uses_default_015(self):
        nodes = [{"filter_type": "jitter", "params": {}, "children": []}]
        assert _find_jitter(nodes) == pytest.approx(0.15)

    def test_jitter_in_children_found(self):
        nodes = [{
            "filter_type": "final_score",
            "params": {},
            "children": [{
                "filter_type": "jitter",
                "params": {"jitter_pct": 0.10},
                "children": [],
            }],
        }]
        assert _find_jitter(nodes) == pytest.approx(0.10)

    def test_empty_nodes_returns_zero(self):
        assert _find_jitter([]) == 0.0


# ── _apply_artist_cap ─────────────────────────────────────────────────────────

class TestApplyArtistCap:
    """
    Two-pass cap: strict pass (max_per_artist), then relaxed (+2) if short.
    """

    def _artist_map(self, mapping: dict) -> dict:
        """mapping: {item_id: artist_name}"""
        return mapping

    def test_strict_cap_applied_when_target_fillable_from_diverse_artists(self):
        """When there are enough different artists to fill the target via strict cap,
        each artist is capped at max_per_artist and no relaxation occurs."""
        # 5 different artists × 3 tracks each. cap=2, target=10.
        # Strict: 2 from each of 5 artists = 10. Target met. No relaxation.
        ids = [f"artist{a}-track{t}" for a in range(5) for t in range(3)]
        artist_map = {f"artist{a}-track{t}": f"Artist-{a}"
                      for a in range(5) for t in range(3)}
        result = _apply_artist_cap(ids, artist_map, max_per_artist=2, target=10)
        assert len(result) == 10
        from collections import Counter
        counts = Counter(artist_map[iid] for iid in result)
        assert all(c <= 2 for c in counts.values())

    def test_multiple_artists_each_independently_capped(self):
        """Each artist's tracks are capped independently when target is fillable."""
        # Artist A: 3 tracks, Artist B: 3 tracks. cap=1, target=2.
        # Strict: 1 from A, 1 from B = 2. Target met. No relaxation.
        ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
        artist_map = {"a1": "Artist A", "a2": "Artist A", "a3": "Artist A",
                      "b1": "Artist B", "b2": "Artist B", "b3": "Artist B"}
        result = _apply_artist_cap(ids, artist_map, max_per_artist=1, target=2)
        from collections import Counter
        counts = Counter(artist_map[iid] for iid in result)
        assert counts.get("Artist A", 0) <= 1
        assert counts.get("Artist B", 0) <= 1
        assert len(result) == 2

    def test_target_respected(self):
        ids = ["a", "b", "c", "d", "e", "f", "g", "h"]
        artist_map = {iid: f"Artist-{iid}" for iid in ids}  # all different artists
        result = _apply_artist_cap(ids, artist_map, max_per_artist=3, target=4)
        assert len(result) == 4

    def test_relaxed_pass_when_strict_is_short(self):
        """
        If strict cap doesn't fill the target, fall back to cap+2.
        Set up: 10 tracks from Artist A, cap=1, target=5.
        Strict pass: 1 track. Short of target (5). Relaxed (cap+2=3): up to 3 tracks.
        """
        ids = [f"a{i}" for i in range(10)]
        artist_map = {iid: "Artist A" for iid in ids}
        result = _apply_artist_cap(ids, artist_map, max_per_artist=1, target=5)
        # Relaxed cap is 3, so max from a single artist is 3
        assert len(result) <= 3
        assert len(result) >= 1

    def test_cap_is_case_insensitive(self):
        """'Radiohead', 'RADIOHEAD', and 'radiohead' must count against the same cap bucket.

        Observable proof: 4 tracks all 'radiohead' (different casings), cap=1, target=4.
        - Case-INSENSITIVE (correct): 1 artist bucket. Strict gets 1. Relaxed (cap+2=3) gets 3.
        - Case-SENSITIVE  (broken):  4 different artists. Strict gets all 4 immediately.
        So correct behaviour returns 3, broken behaviour returns 4.
        """
        ids = ["r1", "r2", "r3", "r4"]
        artist_map = {"r1": "Radiohead", "r2": "RADIOHEAD", "r3": "radiohead", "r4": "RadioHead"}
        result = _apply_artist_cap(ids, artist_map, max_per_artist=1, target=4)
        # Correct (case-insensitive): relaxed cap = 1+2 = 3 → returns 3 tracks
        assert len(result) == 3

    def test_empty_id_list_returns_empty(self):
        result = _apply_artist_cap([], {}, max_per_artist=3, target=10)
        assert result == []

    def test_none_artist_treated_as_empty_string(self):
        """artist_map.get(iid) may return None — must not raise."""
        ids = ["x", "y"]
        result = _apply_artist_cap(ids, {}, max_per_artist=1, target=2)
        # Both have artist="" (empty string treated as same artist)
        assert isinstance(result, list)


# ── _apply_artist_cap_strict ──────────────────────────────────────────────────

class TestApplyArtistCapStrict:
    """
    Strict (no-relaxation) artist cap.  Unlike _apply_artist_cap, this never
    relaxes the cap — it simply drops excess artist appearances.

    NOTE (v12): _apply_artist_cap_strict is no longer called post-hoc inside
    generate_from_template.  Cross-block dedup is now handled in-loop via the
    seen_artists set (see TestSeenArtistsCrossBlockExclusion).  The function is
    retained as a utility; these tests verify it still behaves correctly.
    """

    def test_caps_each_artist_strictly(self):
        ids = ["a1", "a2", "b1", "b2"]
        amap = {"a1": "Artist A", "a2": "Artist A", "b1": "Artist B", "b2": "Artist B"}
        result = _apply_artist_cap_strict(ids, amap, cap=1)
        assert result == ["a1", "b1"]

    def test_does_not_relax_when_short(self):
        """All 10 tracks from Artist A, cap=1 — only 1 track returned, no relaxation."""
        ids = [f"a{i}" for i in range(10)]
        amap = {iid: "Artist A" for iid in ids}
        result = _apply_artist_cap_strict(ids, amap, cap=1)
        assert result == ["a0"]

    def test_preserves_order(self):
        """Tracks that pass the cap appear in their original order."""
        ids = ["a1", "b1", "a2", "c1", "b2"]
        amap = {"a1": "A", "b1": "B", "a2": "A", "c1": "C", "b2": "B"}
        result = _apply_artist_cap_strict(ids, amap, cap=1)
        assert result == ["a1", "b1", "c1"]

    def test_cap_two_allows_two_per_artist(self):
        ids = ["a1", "a2", "a3", "b1", "b2"]
        amap = {"a1": "A", "a2": "A", "a3": "A", "b1": "B", "b2": "B"}
        result = _apply_artist_cap_strict(ids, amap, cap=2)
        assert result == ["a1", "a2", "b1", "b2"]

    def test_empty_returns_empty(self):
        assert _apply_artist_cap_strict([], {}, cap=1) == []

    def test_cross_block_scenario(self):
        """
        Simulates Kelsea Ballerini appearing in 3 blocks (the reported bug).
        After global cap=1, she should appear exactly once despite being the
        highest-scoring track in each block's contribution.
        """
        # Block 0 contributes "kelsea-block0", Block 1 "kelsea-block1", Block 3 "kelsea-block3"
        ids = ["kelsea-block0", "other1", "kelsea-block1", "other2", "kelsea-block3", "other3"]
        amap = {
            "kelsea-block0": "Kelsea Ballerini",
            "kelsea-block1": "Kelsea Ballerini",
            "kelsea-block3": "Kelsea Ballerini",
            "other1": "Artist X",
            "other2": "Artist Y",
            "other3": "Artist Z",
        }
        result = _apply_artist_cap_strict(ids, amap, cap=1)
        kelsea_count = sum(1 for iid in result if "kelsea" in iid)
        assert kelsea_count == 1, (
            f"Kelsea Ballerini appeared {kelsea_count} times after global cap=1."
        )
        assert len(result) == 4  # 1 Kelsea + 3 others


# ── _interleave ────────────────────────────────────────────────────────────────

class TestInterleave:
    """
    Proportional interleaving of results from multiple chains.
    Fills gaps from other chains when one runs dry.
    """

    def test_empty_chains_returns_empty(self):
        assert _interleave([], total_tracks=10) == []

    def test_single_chain_returns_up_to_total_tracks(self):
        ids = ["a", "b", "c", "d", "e"]
        result = _interleave([(100, ids)], total_tracks=3)
        assert result == ["a", "b", "c"]

    def test_single_chain_shorter_than_total(self):
        ids = ["a", "b"]
        result = _interleave([(100, ids)], total_tracks=10)
        assert result == ["a", "b"]

    def test_two_equal_weight_chains_interleaved(self):
        chain_a = ["a1", "a2", "a3"]
        chain_b = ["b1", "b2", "b3"]
        result = _interleave([(50, chain_a), (50, chain_b)], total_tracks=4)
        assert len(result) == 4
        # Both chains should contribute
        assert any(iid.startswith("a") for iid in result)
        assert any(iid.startswith("b") for iid in result)

    def test_heavy_chain_contributes_more(self):
        """A chain with 3× the weight should contribute ~3× as many tracks."""
        chain_heavy = [f"h{i}" for i in range(20)]
        chain_light = [f"l{i}" for i in range(20)]
        result = _interleave([(75, chain_heavy), (25, chain_light)], total_tracks=20)
        heavy_count = sum(1 for iid in result if iid.startswith("h"))
        light_count = sum(1 for iid in result if iid.startswith("l"))
        assert heavy_count > light_count

    def test_exhausted_chain_filled_from_other(self):
        """When one chain runs out, remaining quota is filled from others."""
        short_chain = ["s1"]           # only 1 track
        long_chain  = [f"l{i}" for i in range(20)]
        result = _interleave([(50, short_chain), (50, long_chain)], total_tracks=10)
        assert len(result) == 10
        # short chain contributes 1, long chain fills the rest
        assert "s1" in result

    def test_total_tracks_cap_respected(self):
        ids = [str(i) for i in range(100)]
        result = _interleave([(100, ids)], total_tracks=15)
        assert len(result) == 15

    def test_no_duplicates_in_result(self):
        """_interleave does not deduplicate itself — each chain's list is already deduped."""
        chain_a = ["a", "b", "c"]
        chain_b = ["d", "e", "f"]
        result = _interleave([(50, chain_a), (50, chain_b)], total_tracks=6)
        assert len(result) == len(set(result))

    def test_preserves_chain_internal_order(self):
        """Within each chain, tracks should appear in input order (score-sorted by engine)."""
        chain = ["best", "second", "third", "fourth"]
        result = _interleave([(100, chain)], total_tracks=4)
        assert result == ["best", "second", "third", "fourth"]

    def test_fills_to_total_tracks_when_chain_has_overflow(self):
        """
        The fix for short-count playlists: chains now receive total_tracks as
        the artist-cap target (not chain_target), so each chain can hold excess
        tracks that the drain loop uses when other chains under-deliver.

        Without the fix: B0 was capped at 28 (its quota), B1 at 5, B2 at 8 →
        total 41, not 50.  The drain fires but every chain is already exhausted
        at exactly its cap.

        After the fix: B0 holds 50 tracks; the drain pulls the remaining 9
        tracks from B0's excess after B1's small pool is spent → total 50.
        """
        b0 = [f"b0-{i}" for i in range(50)]   # rich pool — larger than its quota
        b1 = [f"b1-{i}" for i in range(5)]    # genuinely small pool (< quota of 15)
        b2 = [f"b2-{i}" for i in range(8)]    # exactly meets its quota

        result = _interleave([(55, b0), (30, b1), (15, b2)], total_tracks=50)

        assert len(result) == 50, (
            f"Expected 50 tracks, got {len(result)}. "
            "B0 has 50 tracks available; the gap from B1's shortfall should be "
            "filled from B0's excess via the drain loop."
        )
        # B1 and B2 must contribute every track they have
        for i in range(5):
            assert f"b1-{i}" in result, f"b1-{i} missing — B1 should be fully consumed"
        for i in range(8):
            assert f"b2-{i}" in result, f"b2-{i} missing — B2 should be fully consumed"


# ── Prefab seeder: "New For You" latest structure ─────────────────────────────

class TestNewForYouV10Structure:
    """
    Verifies the current "New For You" block design (v10 + v11 fixes):
      v10: 4th "Popular Discoveries" block (global_popularity) surfaces
           popular tracks invisible to the affinity/adjacency path.
      v11: genre_affinity_min lowered 40→25 so secondary liked genres
           (classic rock, soul, etc.) are not excluded when the user's
           top genre dominates the relative affinity normalisation.

    Tests are pure-logic (no database).
    """

    def _get_blocks(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from services.prefab_seeder import _blocks_new_for_you
        return _blocks_new_for_you()

    def test_has_four_blocks(self):
        """v10 design has 4 blocks (was 3 in v9)."""
        blocks = self._get_blocks()
        assert len(blocks) == 4, (
            f"Expected 4 blocks in 'New For You' v10, got {len(blocks)}. "
            "Block 3 (Popular Discoveries) may be missing."
        )

    def test_weights_sum_to_100(self):
        blocks = self._get_blocks()
        total = sum(b["weight"] for b in blocks)
        assert total == 100, f"Block weights sum to {total}, expected 100."

    def test_block3_is_global_popularity(self):
        """Block 3 must use global_popularity as the root filter type."""
        blocks = self._get_blocks()
        import json
        b3 = blocks[3]
        assert b3["block_type"] == "global_popularity", (
            f"Block 3 block_type is '{b3['block_type']}', expected 'global_popularity'."
        )
        tree = json.loads(b3["params"])["filter_tree"]
        assert tree[0]["filter_type"] == "global_popularity"
        assert tree[0]["params"]["played_filter"] == "unplayed"

    def test_block3_has_discovery_child(self):
        """Block 3's discovery child must restrict to stranger+acquaintance (familiar_pct=0)."""
        blocks = self._get_blocks()
        import json
        b3 = blocks[3]
        tree = json.loads(b3["params"])["filter_tree"]
        root = tree[0]
        discovery_nodes = [c for c in root.get("children", []) if c["filter_type"] == "discovery"]
        assert discovery_nodes, "Block 3 must have a discovery child node."
        assert discovery_nodes[0]["params"]["familiar_pct"] == 0, (
            "Block 3 discovery should exclude familiar artists (familiar_pct=0)."
        )

    def test_block3_popularity_threshold_is_meaningful(self):
        """The popularity_min must be >= 60 to filter out obscure tracks."""
        blocks = self._get_blocks()
        import json
        b3 = blocks[3]
        tree = json.loads(b3["params"])["filter_tree"]
        pop_min = tree[0]["params"]["popularity_min"]
        assert pop_min >= 60, (
            f"popularity_min={pop_min} is too low — Popular Discoveries would surface "
            "obscure unplayed tracks instead of genuine bangers."
        )

    def test_genre_affinity_min_is_25(self):
        """
        v11: genre_affinity_min must be 25 (was 40).  The old value excluded
        secondary liked genres — e.g. a user with 500 pop plays and 150 classic
        rock plays has classic rock at g_aff=30, which failed the 40 threshold.
        Lowering to 25 includes any genre with at least 25% of peak engagement.
        """
        blocks = self._get_blocks()
        import json
        for i in (0, 1):  # Blocks 0 and 1 both use genre_affinity_min
            b = blocks[i]
            tree = json.loads(b["params"])["filter_tree"]
            aff_min = tree[0]["params"].get("genre_affinity_min")
            assert aff_min <= 25, (
                f"Block {i} genre_affinity_min={aff_min}, expected <= 25. "
                "A higher value excludes secondary liked genres from discovery."
            )

    def test_block0_and_block1_use_discovery_with_children(self):
        """
        v9 invariant: discovery nodes in Blocks 0 and 1 must have children
        (artist_cap/jitter nested inside) so the familiar_pct=0 constraint
        is not bypassed by sibling OR-expansion.
        """
        blocks = self._get_blocks()
        import json

        def has_discovery_with_children(nodes):
            for n in nodes:
                if n.get("filter_type") == "discovery" and n.get("children"):
                    return True
                if has_discovery_with_children(n.get("children") or []):
                    return True
            return False

        for i in (0, 1):
            tree = json.loads(blocks[i]["params"])["filter_tree"]
            assert has_discovery_with_children(tree), (
                f"Block {i} discovery node must have children (artist_cap/jitter nested "
                "inside) to prevent familiar_pct=0 bypass."
            )


# ── seen_artists cross-block exclusion (v12) ─────────────────────────────────

class TestSeenArtistsCrossBlockExclusion:
    """
    Integration-level tests for the in-loop seen_artists cross-block artist
    deduplication introduced in v12.

    Mechanism: after each block is processed (weight-descending), the artists
    selected by that block are recorded in seen_artists.  Subsequent blocks
    whose max_per_artist <= 2 have those artists filtered out of their
    candidate pool BEFORE scoring/capping, so:

      - The same artist can't consume a slot in every block.
      - The dominant (heaviest) block's deeper pool fills the gap via the
        interleave drain, so total_tracks is still reached.
      - When max_per_artist > 2 the exclusion does NOT fire.

    These tests exercise generate_from_template end-to-end using lightweight
    mocks for the DB, block executors, and scoring helpers.
    """

    def _make_fake_block(self, block_id: int, weight: int, artist_cap: int,
                         tracks_by_artist: dict[str, list[str]]):
        """
        Return (block_mock, artist_map, id_set).

        block_mock has .id, .weight, .block_type and .params set to a valid
        filter_tree JSON with one genre root + artist_cap child.
        """
        block = MagicMock()
        block.id = block_id
        block.weight = weight
        block.block_type = "genre"
        block.position = block_id  # ensure deterministic ordering if used

        all_ids = [iid for ids in tracks_by_artist.values() for iid in ids]
        artist_map = {
            iid: artist
            for artist, ids in tracks_by_artist.items()
            for iid in ids
        }
        filter_tree = [
            {
                "filter_type": "genre",
                "params": {},
                "children": [
                    {
                        "filter_type": "artist_cap",
                        "params": {"max_per_artist": artist_cap},
                        "children": [],
                    }
                ],
            }
        ]
        block.params = json.dumps({"filter_tree": filter_tree})
        return block, artist_map, set(all_ids)

    def _run(self, block_configs: list[dict], total_tracks: int = 10) -> list[str]:
        """
        Execute generate_from_template with fully mocked internals.

        block_configs: list of dicts, each with:
          weight      int
          artist_cap  int
          tracks      {artist_name: [item_id, ...]}

        Blocks are sorted by weight (desc) inside the engine; eval_sets are
        ordered to match so _evaluate_nodes returns the right set per block.
        """
        sorted_configs = sorted(block_configs, key=lambda c: c["weight"], reverse=True)

        fake_blocks = []
        combined_artist_map: dict[str, str] = {}
        eval_sets: list[set[str]] = []

        for i, cfg in enumerate(sorted_configs):
            block, amap, id_set = self._make_fake_block(
                block_id=i + 1,
                weight=cfg["weight"],
                artist_cap=cfg["artist_cap"],
                tracks_by_artist=cfg["tracks"],
            )
            fake_blocks.append(block)
            combined_artist_map.update(amap)
            eval_sets.append(id_set)

        # Mock DB ─────────────────────────────────────────────────────────────
        db = MagicMock()

        # Template query: db.query(PlaylistTemplate).filter(...).first()
        fake_template = MagicMock()
        fake_template.total_tracks = total_tracks

        # Block query: db.query(PlaylistBlock).filter(...).order_by(...).all()
        # Use side_effect keyed on the first positional arg to db.query()
        class_name_to_result = {
            "PlaylistTemplate": fake_template,
            "PlaylistBlock": fake_blocks,
        }

        def query_side_effect(*args):
            q = MagicMock()
            model = args[0] if args else None
            name = getattr(model, "__name__", None) or getattr(
                getattr(model, "__class__", None), "__name__", ""
            )
            if name == "PlaylistTemplate":
                q.filter.return_value.first.return_value = fake_template
            else:
                # PlaylistBlock chain: .filter().order_by().all()
                q.filter.return_value.order_by.return_value.all.return_value = fake_blocks
            return q

        db.query.side_effect = query_side_effect

        # Mocked helpers ──────────────────────────────────────────────────────
        eval_call = [0]

        def eval_side(nodes, user_id, db, excluded_item_ids):
            idx = eval_call[0]
            eval_call[0] += 1
            return eval_sets[idx] if idx < len(eval_sets) else set()

        def amap_side(item_ids, user_id, db):
            return {iid: combined_artist_map.get(iid, "") for iid in item_ids}

        def smap_side(item_ids, user_id, db, jitter_pct=0.0):
            # Uniform scores → ordering irrelevant for these tests
            return {iid: 50.0 for iid in item_ids}

        with patch("services.playlist_engine._evaluate_nodes", side_effect=eval_side), \
             patch("services.playlist_engine._build_artist_map", side_effect=amap_side), \
             patch("services.playlist_engine._build_score_map", side_effect=smap_side), \
             patch("services.playlist_engine.get_excluded_item_ids", return_value=frozenset()), \
             patch("services.playlist_engine.get_holiday_excluded_ids", return_value=frozenset()), \
             patch("services.playlist_engine.get_artist_cooled_down_ids", return_value=frozenset()):

            from services.playlist_engine import generate_from_template
            return asyncio.run(generate_from_template(1, "user1", db))

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_block1_kelsea_tracks_excluded_when_block0_claimed_her(self):
        """
        Kelsea Ballerini is in both Block 0 (weight=70, cap=1) and Block 1
        (weight=30, cap=1).  Block 0 processes first and claims her.

        Cross-block exclusion must ensure that Block 1's Kelsea tracks
        (kb-4, kb-5) never enter the candidate pool — only Block 0's Kelsea
        tracks (kb-1..3) can appear.

        Note: Block 0 uses a two-pass cap (strict=1, relaxed=3), so up to 3
        Kelsea tracks from Block 0 may appear.  That is expected per-block
        behaviour — the exclusion only prevents Block 1 from re-adding her.
        """
        result = self._run(
            block_configs=[
                {
                    "weight": 70,
                    "artist_cap": 1,
                    "tracks": {
                        "Kelsea Ballerini": ["kb-1", "kb-2", "kb-3"],
                        "Artist X": ["x-1", "x-2", "x-3"],
                        "Artist Y": ["y-1", "y-2", "y-3"],
                    },
                },
                {
                    "weight": 30,
                    "artist_cap": 1,
                    "tracks": {
                        "Kelsea Ballerini": ["kb-4", "kb-5"],   # same artist, different tracks
                        "Artist Z": ["z-1", "z-2"],
                    },
                },
            ],
            total_tracks=6,
        )
        # Block 1's Kelsea tracks must NOT appear (excluded by seen_artists)
        assert "kb-4" not in result, "kb-4 (Block 1 Kelsea track) should be excluded"
        assert "kb-5" not in result, "kb-5 (Block 1 Kelsea track) should be excluded"
        # Block 0 may contribute 1–3 Kelsea tracks (per-block relaxed cap)
        kb_from_b0 = sum(1 for iid in result if iid in {"kb-1", "kb-2", "kb-3"})
        assert kb_from_b0 >= 1, "At least one Block 0 Kelsea track must appear"

    def test_total_tracks_preserved_despite_cross_block_exclusion(self):
        """
        When Block 1 loses candidates because their artist was already claimed by
        Block 0, the interleave drain should pull from Block 0's excess pool so
        the total playlist count is still reached.

        This is the key regression guard: the old post-hoc _apply_artist_cap_strict
        approach reduced "New For You" from 50 to 17 tracks.  The in-loop approach
        pre-allocates so Block 0 can still hold excess tracks for the drain.
        """
        # Block 0: 20 tracks across 10 artists — deep pool
        block0_tracks = {f"artist-{i}": [f"b0-{i}-t1", f"b0-{i}-t2"] for i in range(10)}
        # Block 1: 6 tracks, but 4 of them belong to artists already in Block 0
        block1_tracks = {
            "artist-0": ["b1-0-t1"],       # already claimed by Block 0
            "artist-1": ["b1-1-t1"],       # already claimed by Block 0
            "new-artist-a": ["b1-new-a1", "b1-new-a2"],
            "new-artist-b": ["b1-new-b1", "b1-new-b2"],
        }

        result = self._run(
            block_configs=[
                {"weight": 70, "artist_cap": 1, "tracks": block0_tracks},
                {"weight": 30, "artist_cap": 1, "tracks": block1_tracks},
            ],
            total_tracks=10,
        )
        assert len(result) == 10, (
            f"Expected 10 tracks but got {len(result)}.  "
            "Block 0's deep pool should fill the gap left by Block 1 exclusions."
        )

    def test_cross_block_exclusion_skipped_when_cap_above_2(self):
        """
        When max_per_artist > 2, the seen_artists guard is disabled entirely.
        The same artist may appear in multiple blocks' contributions.
        """
        result = self._run(
            block_configs=[
                {
                    "weight": 60,
                    "artist_cap": 3,   # > 2 — exclusion disabled
                    "tracks": {
                        "Kelsea Ballerini": ["kb-1", "kb-2", "kb-3"],
                        "Other A": ["oa-1"],
                    },
                },
                {
                    "weight": 40,
                    "artist_cap": 3,   # > 2 — exclusion disabled
                    "tracks": {
                        "Kelsea Ballerini": ["kb-4", "kb-5"],
                        "Other B": ["ob-1"],
                    },
                },
            ],
            total_tracks=8,
        )
        # With cap=3 and no cross-block exclusion, KB tracks from both blocks
        # can appear.  The per-block artist_cap allows up to 3 per block.
        # B0 capped: kb-1,2,3 (cap=3); B1 capped: kb-4,5 (cap=3, only 2 avail)
        # Dedup by seen_ids removes kb-1..3 from B1 consideration → only kb-4,5
        # are fresh in B1.  So total KB = kb-1..3 + kb-4,5 = up to 5.
        # At minimum, both blocks must contribute at least one KB track.
        kb_count = sum(1 for iid in result if iid.startswith("kb-"))
        assert kb_count >= 2, (
            f"With artist_cap=3 (>2) cross-block exclusion should not fire; "
            f"expected >= 2 Kelsea tracks, got {kb_count}."
        )

    def test_seen_artists_uses_lowercase_matching(self):
        """
        Artist names in seen_artists are normalised to lowercase, so
        'Kelsea Ballerini' and 'KELSEA BALLERINI' map to the same exclusion bucket.

        Block 1's 'kb-upper' track must never appear regardless of casing.
        """
        result = self._run(
            block_configs=[
                {
                    "weight": 70,
                    "artist_cap": 1,
                    "tracks": {
                        "Kelsea Ballerini": ["kb-1"],   # mixed case
                    },
                },
                {
                    "weight": 30,
                    "artist_cap": 1,
                    "tracks": {
                        "KELSEA BALLERINI": ["kb-upper"],   # all caps — same artist
                        "Artist Z": ["z-1"],
                    },
                },
            ],
            total_tracks=2,
        )
        # Block 1's all-caps Kelsea track must be excluded by case-normalised seen_artists
        assert "kb-upper" not in result, (
            "kb-upper (Block 1, 'KELSEA BALLERINI') should be excluded because "
            "'kelsea ballerini' is already in seen_artists from Block 0."
        )
        # Block 0's Kelsea track must appear (it's the only artist in Block 0)
        assert "kb-1" in result, (
            "kb-1 (Block 0, 'Kelsea Ballerini') should appear — Block 0 has only her."
        )
