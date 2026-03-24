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

import pytest
from unittest.mock import patch, MagicMock

from services.playlist_engine import (
    _evaluate_nodes,
    _find_artist_cap,
    _find_jitter,
    _apply_artist_cap,
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
