"""
JellyDJ — Playlist Engine  (Phase 8 rewrite)

Template data shape
───────────────────
Each PlaylistBlock row stores a *block chain* in its params JSON:

{
  "weight": 50,              # % of playlist this chain contributes
  "filter_tree": [           # list of OR-nodes at the top level
    {
      "filter_type": "play_recency",
      "params": {"mode": "within", "days": 30},
      "children": [          # AND-children: intersected with parent result
        {
          "filter_type": "global_popularity",
          "params": {"popularity_min": 30, "popularity_max": 80},
          "children": []
        },
        {                    # sibling of above → OR with it THEN AND with parent
          "filter_type": "favorites",
          "params": {},
          "children": []
        }
      ]
    }
  ]
}

Evaluation semantics
────────────────────
Given a list of nodes at the same level (siblings):

  evaluate(nodes) → set[str]

  1. For each node:
       a. Get the node's own ID set from its executor.
       b. If it has children: AND-intersect the node's set with evaluate(children).
       c. Collect the resulting sets from all sibling nodes.
  2. OR-union all collected sets → return.

So siblings are OR, children are AND.  This mirrors the lexical indentation
model the user described.

Block chain weighting
──────────────────────
Each PlaylistBlock.weight gives that chain's % share of the final playlist.
Weights are normalised if they don't sum to 100.  The engine pulls
`round(total_tracks * normalised_weight)` tracks from each chain's resolved
set, then applies a per-artist cap (from any artist_cap node in the tree,
defaulting to 3) before merging.

Public API
──────────
  generate_from_template(template_id, user_id, db) -> list[str]
  preview_template(template_id, user_id, db) -> dict
"""
from __future__ import annotations

import json
import logging
import random
from typing import Optional

from sqlalchemy.orm import Session

from services.playlist_blocks import BLOCK_REGISTRY
from services.playlist_utils import (
    get_excluded_item_ids,
    get_holiday_excluded_ids,
    get_artist_cooled_down_ids,
)

log = logging.getLogger(__name__)


# ── Tree evaluator ────────────────────────────────────────────────────────────

def _evaluate_nodes(
    nodes: list[dict],
    user_id: str,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """
    Recursively evaluate a list of sibling filter nodes.

    Siblings → OR (union).
    Children of a node → AND (intersection with the node's own set).

    Returns the union of all sibling results.
    """
    if not nodes:
        return set()

    sibling_sets: list[set[str]] = []

    for node in nodes:
        filter_type = node.get("filter_type")
        params      = node.get("params") or {}
        children    = node.get("children") or []

        executor = BLOCK_REGISTRY.get(filter_type)
        if executor is None:
            log.warning("Unknown filter_type '%s' — skipping node", filter_type)
            continue

        # Get this node's own candidate set
        try:
            node_set = executor(
                user_id=user_id,
                params=params,
                db=db,
                excluded_item_ids=excluded_item_ids,
            )
        except Exception as exc:
            log.error("Executor '%s' raised: %s", filter_type, exc, exc_info=True)
            node_set = set()

        # AND-intersect with evaluated children
        if children:
            children_set = _evaluate_nodes(children, user_id, db, excluded_item_ids)
            node_set = node_set & children_set

        sibling_sets.append(node_set)

    # OR-union all sibling results
    result: set[str] = set()
    for s in sibling_sets:
        result |= s
    return result


def _find_artist_cap(nodes: list[dict], default: int = 3) -> int:
    """
    Walk the filter tree and return the max_per_artist value from the first
    artist_cap node found (depth-first).  Falls back to default.
    """
    for node in nodes:
        if node.get("filter_type") == "artist_cap":
            return int((node.get("params") or {}).get("max_per_artist", default))
        cap = _find_artist_cap(node.get("children") or [], default)
        if cap != default:
            return cap
    return default


def _find_jitter(nodes: list[dict]) -> float:
    """
    Walk the filter tree and return the jitter_pct from the first jitter node
    found (depth-first).  Returns 0.0 if no jitter node exists in this chain.
    """
    for node in nodes:
        if node.get("filter_type") == "jitter":
            return float((node.get("params") or {}).get("jitter_pct", 0.15))
        found = _find_jitter(node.get("children") or [])
        if found > 0:
            return found
    return 0.0


def _apply_artist_cap(
    id_list: list[str],
    artist_map: dict[str, str],  # item_id → artist_name
    max_per_artist: int,
    target: int,
) -> list[str]:
    """
    Pick up to `target` tracks from id_list honouring per-artist cap.
    Two-pass: strict cap, then relaxed cap (+2) if short.
    """
    def _pick(ids, cap):
        counts: dict[str, int] = {}
        picked = []
        for iid in ids:
            artist = (artist_map.get(iid) or "").lower()
            if counts.get(artist, 0) < cap:
                picked.append(iid)
                counts[artist] = counts.get(artist, 0) + 1
            if len(picked) >= target:
                break
        return picked

    result = _pick(id_list, max_per_artist)
    if len(result) < target:
        result = _pick(id_list, max_per_artist + 2)
    return result


def _build_artist_map(item_ids: set[str], user_id: str, db: Session) -> dict[str, str]:
    """Bulk-load item_id → artist_name for a set of IDs."""
    from models import TrackScore
    if not item_ids:
        return {}
    rows = (
        db.query(TrackScore.jellyfin_item_id, TrackScore.artist_name)
        .filter(TrackScore.user_id == user_id, TrackScore.jellyfin_item_id.in_(item_ids))
        .all()
    )
    return {r.jellyfin_item_id: (r.artist_name or "") for r in rows}


def _build_score_map(item_ids: set[str], user_id: str, db: Session, jitter_pct: float = 0.0) -> dict[str, float]:
    """Bulk-load item_id → final_score for ordering within a resolved set.

    When jitter_pct > 0, each score is nudged by a random amount up to
    ±(score * jitter_pct) before returning, so that repeated playlist
    generations with the same filter don't always produce the same ordering.
    """
    from models import TrackScore
    if not item_ids:
        return {}
    rows = (
        db.query(TrackScore.jellyfin_item_id, TrackScore.final_score)
        .filter(TrackScore.user_id == user_id, TrackScore.jellyfin_item_id.in_(item_ids))
        .all()
    )
    if jitter_pct <= 0:
        return {r.jellyfin_item_id: float(r.final_score or 0) for r in rows}
    result = {}
    for r in rows:
        base = float(r.final_score or 0)
        nudge = base * jitter_pct * (2 * random.random() - 1)
        result[r.jellyfin_item_id] = max(0.0, base + nudge)
    return result


# ── Core engine ───────────────────────────────────────────────────────────────

async def generate_from_template(
    template_id: int,
    user_id: str,
    db: Session,
) -> list[str]:
    """
    Execute a PlaylistTemplate and return a list of Jellyfin item IDs.

    Each PlaylistBlock is a "block chain" with its own filter tree and weight.
    Block chains contribute a proportional slice of the final playlist.
    Tracks are deduplicated across chains; the highest-weight chain wins on clash.
    """
    from models import PlaylistTemplate, PlaylistBlock

    template = db.query(PlaylistTemplate).filter(PlaylistTemplate.id == template_id).first()
    if not template:
        raise ValueError(f"PlaylistTemplate id={template_id} not found")

    blocks = (
        db.query(PlaylistBlock)
        .filter(PlaylistBlock.template_id == template_id)
        .order_by(PlaylistBlock.position)
        .all()
    )
    if not blocks:
        log.warning("Template id=%d has no blocks — returning empty list", template_id)
        return []

    total_tracks = template.total_tracks or 50

    # ── Normalise weights ─────────────────────────────────────────────────────
    weight_sum = sum(b.weight for b in blocks) or 100
    if abs(weight_sum - 100) > 1:
        log.warning(
            "Template id=%d block weights sum to %d — normalising proportionally",
            template_id, weight_sum,
        )

    # ── Exclusions ────────────────────────────────────────────────────────────
    # Combines: manually excluded albums, out-of-season holiday tracks, and
    # any artist currently on a skip-triggered timeout for this user.
    excluded_item_ids: frozenset = (
        get_excluded_item_ids(db)
        | get_holiday_excluded_ids(db)
        | get_artist_cooled_down_ids(db, user_id)
    )

    # ── Evaluate each block chain ─────────────────────────────────────────────
    # Process blocks in weight order (heaviest first) so dedup favours the
    # most important chains.
    chain_results: list[tuple[int, list[str]]] = []  # (weight, ordered_ids)

    seen_ids: set[str] = set()

    sorted_blocks = sorted(blocks, key=lambda b: b.weight, reverse=True)

    for block in sorted_blocks:
        try:
            raw = block.params
            chain_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            log.warning("Block id=%d has invalid JSON params — skipping", block.id)
            continue

        # Support both old flat format and new tree format
        filter_tree = chain_data.get("filter_tree")
        if filter_tree is None:
            # Legacy: treat the block_type + params as a single root node
            filter_tree = [{
                "filter_type": block.block_type,
                "params": chain_data,
                "children": [],
            }]

        # Walk tree to find artist_cap
        max_per_artist = _find_artist_cap(filter_tree, default=3)

        # Evaluate the tree → set of matching IDs
        try:
            matched_ids: set[str] = _evaluate_nodes(
                filter_tree, user_id, db, excluded_item_ids
            )
        except Exception as exc:
            log.error("Block id=%d tree evaluation failed: %s", block.id, exc, exc_info=True)
            matched_ids = set()

        # Exclude already-claimed IDs (dedup across chains)
        fresh_ids = matched_ids - seen_ids

        # Score-sort the remaining IDs so best tracks come first within this chain
        # Apply jitter if a jitter node exists anywhere in this chain's tree
        chain_jitter = _find_jitter(filter_tree)
        score_map = _build_score_map(fresh_ids, user_id, db, jitter_pct=chain_jitter)
        sorted_ids = sorted(fresh_ids, key=lambda iid: score_map.get(iid, 0), reverse=True)

        # Compute target count for this chain proportional to its weight
        normalised_w  = block.weight / weight_sum
        chain_target  = max(1, round(total_tracks * normalised_w))

        # Apply artist cap
        artist_map = _build_artist_map(fresh_ids, user_id, db)
        capped_ids = _apply_artist_cap(sorted_ids, artist_map, max_per_artist, chain_target)

        seen_ids.update(capped_ids)
        chain_results.append((block.weight, capped_ids))

        log.debug(
            "Block id=%d weight=%d matched=%d after_dedup=%d capped=%d target=%d",
            block.id, block.weight,
            len(matched_ids), len(fresh_ids), len(capped_ids), chain_target,
        )

    # ── Merge chains in weighted interleaved order ────────────────────────────
    # Sort back to position order for interleaving
    chain_results.sort(key=lambda t: t[0], reverse=True)
    result = _interleave(chain_results, total_tracks)

    log.info(
        "Template id=%d user=%s: %d/%d tracks generated",
        template_id, user_id, len(result), total_tracks,
    )
    return result


def _interleave(chain_results: list[tuple[int, list[str]]], total_tracks: int) -> list[str]:
    """
    Interleave results from multiple chains proportionally by weight.
    Fills gaps from any chain that still has tracks if others run dry.
    """
    if not chain_results:
        return []

    total_weight = sum(w for w, _ in chain_results) or 1
    quotas = [max(1, round(total_tracks * (w / total_weight))) for w, _ in chain_results]

    result: list[str] = []
    ptrs   = [0] * len(chain_results)
    used   = [0] * len(chain_results)
    order  = sorted(range(len(chain_results)), key=lambda i: chain_results[i][0], reverse=True)

    while len(result) < total_tracks:
        added_any = False
        for i in order:
            if len(result) >= total_tracks:
                break
            if used[i] >= quotas[i]:
                continue
            ids = chain_results[i][1]
            if ptrs[i] >= len(ids):
                continue
            result.append(ids[ptrs[i]])
            ptrs[i] += 1
            used[i] += 1
            added_any = True

        if not added_any:
            # Quotas exhausted — drain any remaining pool
            for i in order:
                ids = chain_results[i][1]
                while len(result) < total_tracks and ptrs[i] < len(ids):
                    result.append(ids[ptrs[i]])
                    ptrs[i] += 1
            break

    return result[:total_tracks]


# ── Preview error ─────────────────────────────────────────────────────────────

class PlaylistPreviewError(Exception):
    """Raised when preview can diagnose a clear user-facing reason for failure."""
    def __init__(self, message: str, code: str = "preview_error"):
        super().__init__(message)
        self.code = code


# ── Preview ───────────────────────────────────────────────────────────────────

async def preview_template(
    template_id: int,
    user_id: str,
    db: Session,
) -> dict:
    """
    Dry-run generate_from_template and return a lightweight preview dict.
    Raises PlaylistPreviewError with a plain-English message when a diagnosable
    failure is detected so the frontend can show it directly to the user.
    """
    from models import TrackScore, PlaylistTemplate, PlaylistBlock

    # ── Pre-flight checks ─────────────────────────────────────────────────────

    # 1. Template must have at least one block
    template = db.query(PlaylistTemplate).filter(PlaylistTemplate.id == template_id).first()
    if not template:
        raise PlaylistPreviewError(
            f"Template {template_id} not found.",
            code="template_not_found",
        )

    blocks = db.query(PlaylistBlock).filter(PlaylistBlock.template_id == template_id).all()
    if not blocks:
        raise PlaylistPreviewError(
            "This template has no blocks yet. Add at least one filter block to generate a preview.",
            code="no_blocks",
        )

    # 2. User must have scored tracks
    track_count = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .limit(1)
        .count()
    )
    if track_count == 0:
        raise PlaylistPreviewError(
            "No scored tracks found for your account. Make sure your Jellyfin library has been "
            "scanned and scores have been built (Settings → Rebuild Scores).",
            code="no_track_scores",
        )

    # ── Validate block filter trees ───────────────────────────────────────────
    # Walk every block's filter_tree and flag any unknown filter_type before
    # actually running — gives a clearer error than a mid-generation crash.
    unknown_types = set()
    for block in blocks:
        try:
            raw = block.params
            chain_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            filter_tree = chain_data.get("filter_tree") or [{"filter_type": block.block_type, "params": chain_data}]
            _collect_unknown_types(filter_tree, unknown_types)
        except (json.JSONDecodeError, TypeError):
            raise PlaylistPreviewError(
                f"Block #{block.id} has invalid JSON params. Try removing and re-adding it.",
                code="invalid_block_params",
            )

    if unknown_types:
        raise PlaylistPreviewError(
            f"Unknown filter type(s) in this template: {', '.join(sorted(unknown_types))}. "
            "These blocks are not recognised by the engine. Remove them and re-save.",
            code="unknown_filter_types",
        )

    # ── Run generation ────────────────────────────────────────────────────────
    try:
        ids = await generate_from_template(template_id, user_id, db)
    except ValueError as e:
        raise PlaylistPreviewError(str(e), code="generation_error")
    except Exception as e:
        # Surface a readable version of any unexpected engine error
        msg = str(e)
        hint = _diagnose_engine_error(msg)
        raise PlaylistPreviewError(hint or f"Generation failed: {msg}", code="generation_error")

    if not ids:
        # Successful run but zero results — explain the most likely cause
        hint = _diagnose_empty_result(blocks, user_id, db)
        raise PlaylistPreviewError(hint, code="empty_result")

    # ── Build sample ──────────────────────────────────────────────────────────
    sample_ids = random.sample(ids, min(5, len(ids)))
    name_map: dict[str, tuple[str, str]] = {}
    if sample_ids:
        rows = (
            db.query(TrackScore.jellyfin_item_id, TrackScore.track_name, TrackScore.artist_name)
            .filter(TrackScore.user_id == user_id, TrackScore.jellyfin_item_id.in_(sample_ids))
            .all()
        )
        for row in rows:
            name_map[row.jellyfin_item_id] = (row.track_name or "", row.artist_name or "")

    sample = [
        {"track": name_map.get(iid, ("", ""))[0], "artist": name_map.get(iid, ("", ""))[1]}
        for iid in sample_ids
    ]
    return {"estimated_tracks": len(ids), "sample": sample}


def _collect_unknown_types(nodes: list[dict], result: set) -> None:
    """Walk a filter tree and collect any filter_type not in BLOCK_REGISTRY."""
    for node in nodes:
        ft = node.get("filter_type")
        if ft and ft not in BLOCK_REGISTRY:
            result.add(ft)
        _collect_unknown_types(node.get("children") or [], result)


def _diagnose_engine_error(msg: str) -> str:
    """Map common engine exception messages to plain-English hints."""
    m = msg.lower()
    if "cast" in m and "real" in m:
        return (
            "A scoring value couldn't be read as a number. Try rebuilding scores "
            "(Settings → Rebuild Scores) — some tracks may have corrupt score data."
        )
    if "no such table" in m or "operationalerror" in m:
        return (
            "A database table is missing. The app may need a migration or restart."
        )
    if "user_id" in m or "user" in m:
        return "User account data is missing or corrupt. Try logging out and back in."
    return ""


def _diagnose_empty_result(blocks, user_id: str, db) -> str:
    """
    When generation succeeds but returns zero tracks, try to explain why.
    Checks the most common culprits in order.
    """
    from models import TrackScore

    # Check each block's filter_tree for likely over-constraints
    hints = []
    for block in blocks:
        try:
            raw = block.params
            chain_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            filter_tree = chain_data.get("filter_tree") or [{"filter_type": block.block_type, "params": chain_data}]
            hints.extend(_check_tree_for_empty(filter_tree, user_id, db))
        except Exception:
            pass

    if hints:
        return (
            "No tracks matched your filters. "
            + " ".join(dict.fromkeys(hints))  # dedupe while preserving order
        )

    return (
        "No tracks matched this template. The filter combination may be too narrow — "
        "try widening a range, removing an AND filter, or checking that your library has play history."
    )


def _check_tree_for_empty(nodes: list[dict], user_id: str, db) -> list[str]:
    """Return plain-English hints for over-constrained nodes."""
    from models import TrackScore
    hints = []
    for node in nodes:
        ft  = node.get("filter_type")
        p   = node.get("params") or {}

        if ft == "final_score":
            lo, hi = float(p.get("score_min", 0)), float(p.get("score_max", 99))
            count = (
                db.query(TrackScore)
                .filter(TrackScore.user_id == user_id)
                .filter(TrackScore.final_score.isnot(None))
                .count()
            )
            if count == 0:
                hints.append("Scores haven't been built yet — run a score rebuild first.")
            elif lo > hi:
                hints.append(f"Final Score range is invalid: min ({lo}) is higher than max ({hi}).")
            elif hi - lo < 5:
                hints.append(
                    f"Final Score range {lo}–{hi} is very narrow. "
                    "Widen it to include more tracks."
                )

        elif ft == "play_count":
            lo = int(p.get("play_count_min", 0))
            hi = int(p.get("play_count_max", 500))
            if lo > hi:
                hints.append(f"Play Count range is invalid: min ({lo}) is higher than max ({hi}).")

        elif ft == "play_recency":
            days = int(p.get("days", 30))
            mode = p.get("mode", "within")
            count = (
                db.query(TrackScore)
                .filter(TrackScore.user_id == user_id, TrackScore.is_played == True)  # noqa
                .count()
            )
            if count == 0:
                hints.append("No play history found. Play Recency requires at least some played tracks.")
            elif mode == "within" and days < 3:
                hints.append(f"Play Recency window of {days} day(s) is very short — try a wider window.")

        elif ft == "genre":
            genres = p.get("genres", [])
            if genres:
                matched = (
                    db.query(TrackScore)
                    .filter(TrackScore.user_id == user_id, TrackScore.genre.in_(genres))
                    .limit(1).count()
                )
                if matched == 0:
                    hints.append(
                        f"Genre filter ({', '.join(genres[:3])}) matched no tracks. "
                        "Check that these genres exist in your library."
                    )

        elif ft == "favorites":
            count = (
                db.query(TrackScore)
                .filter(TrackScore.user_id == user_id, TrackScore.is_favorite == True)  # noqa
                .limit(1).count()
            )
            if count == 0:
                hints.append("No favourited tracks found. Mark some tracks as favourites in Jellyfin first.")

        elif ft == "played_status":
            played_filter = p.get("played_filter", "played")
            col_filter = (TrackScore.is_played == True) if played_filter == "played" else (TrackScore.is_played == False)  # noqa
            count = (
                db.query(TrackScore)
                .filter(TrackScore.user_id == user_id, col_filter)
                .limit(1).count()
            )
            if count == 0:
                label = "played" if played_filter == "played" else "unplayed"
                hints.append(f"No {label} tracks found for your account.")

        # Recurse into children
        hints.extend(_check_tree_for_empty(node.get("children") or [], user_id, db))

    return hints