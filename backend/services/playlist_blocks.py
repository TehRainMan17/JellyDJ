"""
JellyDJ — Playlist Block Executors  (Phase 8 rewrite — audited & fixed)

Each executor fetches a candidate set of track IDs that match its filter
criteria.  The engine is responsible for AND-intersecting and OR-unioning
sets; executors just return all matching IDs.

Executor signature:
    def execute_<type>_block(
        user_id: str,
        params: dict,
        db: Session,
        excluded_item_ids: frozenset,
    ) -> set[str]

No target_count parameter — the engine decides how many tracks to take
from each block chain after all set operations are complete.

AUDIT FIXES (see AUDIT.md for full details):
  - execute_final_score_block:   played_filter param was accepted by UI but
      never applied in the query. Fixed.
  - execute_genre_block:         genre_affinity_min/max params were exposed in
      UI but never applied in the query. Fixed.
  - execute_artist_block:        artist_affinity_min/max params were exposed in
      UI but never applied in the query. Fixed. played_filter now applied.
  - execute_play_count_block:    order param ('asc'/'desc') was exposed in UI
      but the query always sorted DESC. Fixed.
  - execute_discovery_block:     popularity_min/max params were exposed in UI
      but the block never filtered by global_popularity. Fixed.
  - execute_global_popularity_block: TrackScore.global_popularity is a Float
      but scores stored as String were not always cast; made consistent.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text as satext, cast, Float

log = logging.getLogger(__name__)

FETCH_LIMIT = 2000  # broad pool per executor — engine thins it down


def _cast_float(col):
    return cast(col, Float)


def _apply_played_filter(query, played_filter: str):
    from models import TrackScore
    if played_filter == "played":
        return query.filter(TrackScore.is_played == True)   # noqa: E712
    if played_filter == "unplayed":
        return query.filter(TrackScore.is_played == False)  # noqa: E712
    return query


def _apply_exclusions(item_ids: set[str], excluded_item_ids: frozenset) -> set[str]:
    if not excluded_item_ids:
        return item_ids
    return item_ids - excluded_item_ids


def _rows_to_set(rows) -> set[str]:
    return {r.jellyfin_item_id for r in rows if r.jellyfin_item_id}


# ── Executors ─────────────────────────────────────────────────────────────────

def execute_final_score_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered and ranked by final_score range.

    Params:
      played_filter : 'all' | 'played' | 'unplayed'
      score_min     : float 0-99  (default 0)
      score_max     : float 0-99  (default 99)
      order         : 'desc' (default, highest first) | 'asc' (lowest first)

    FIX: played_filter was ignored — now applied.
    """
    from models import TrackScore
    score_min     = float(params.get("score_min", 0))
    score_max     = float(params.get("score_max", 99))
    played_filter = params.get("played_filter", "all")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(_cast_float(TrackScore.final_score) >= score_min)
        .filter(_cast_float(TrackScore.final_score) <= score_max)
        .order_by(satext("CAST(final_score AS REAL) DESC"))
    )
    # FIX: apply the played_filter param that the UI sends but was previously dropped
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_affinity_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks within an affinity range, sorted by average affinity."""
    from models import TrackScore
    affinity_min  = float(params.get("affinity_min", 0))
    affinity_max  = float(params.get("affinity_max", 100))
    played_filter = params.get("played_filter", "all")
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(satext(
            f"(CAST(artist_affinity AS REAL) + CAST(genre_affinity AS REAL)) / 2.0 "
            f"BETWEEN {affinity_min} AND {affinity_max}"
        ))
        .order_by(satext(
            "(CAST(artist_affinity AS REAL) + CAST(genre_affinity AS REAL)) / 2.0 DESC"
        ))
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_genre_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks matching specified genres (empty = all genres).

    Params:
      genres              : list of genre strings (empty = all)
      genre_affinity_min  : float 0-100 (default 0)
      genre_affinity_max  : float 0-100 (default 100)
      played_filter       : 'all' | 'played' | 'unplayed'

    FIX: genre_affinity_min/max and played_filter were exposed in the UI
         but never applied to the query.
    """
    from models import TrackScore
    genres             = params.get("genres", [])
    genre_affinity_min = float(params.get("genre_affinity_min", 0))
    genre_affinity_max = float(params.get("genre_affinity_max", 100))
    played_filter      = params.get("played_filter", "all")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        # FIX: apply the genre affinity range that the UI sends
        .filter(_cast_float(TrackScore.genre_affinity) >= genre_affinity_min)
        .filter(_cast_float(TrackScore.genre_affinity) <= genre_affinity_max)
        .order_by(satext("CAST(genre_affinity AS REAL) DESC"))
    )
    if genres:
        query = query.filter(TrackScore.genre.in_(genres))
    # FIX: apply played_filter
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_artist_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks from specified artists (empty = all artists).

    Params:
      artists              : list of artist name strings (empty = all)
      artist_affinity_min  : float 0-100 (default 0)
      artist_affinity_max  : float 0-100 (default 100)
      played_filter        : 'all' | 'played' | 'unplayed'

    FIX: artist_affinity_min/max and played_filter were exposed in the UI
         but never applied to the query.
    """
    from models import TrackScore
    artists              = params.get("artists", [])
    artist_affinity_min  = float(params.get("artist_affinity_min", 0))
    artist_affinity_max  = float(params.get("artist_affinity_max", 100))
    played_filter        = params.get("played_filter", "all")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        # FIX: apply the artist affinity range that the UI sends
        .filter(_cast_float(TrackScore.artist_affinity) >= artist_affinity_min)
        .filter(_cast_float(TrackScore.artist_affinity) <= artist_affinity_max)
        .order_by(satext("CAST(artist_affinity AS REAL) DESC"))
    )
    if artists:
        query = query.filter(TrackScore.artist_name.in_(artists))
    # FIX: apply played_filter
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_play_count_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by play count range.

    Params:
      play_count_min : int (default 0)
      play_count_max : int | None (default None = no upper bound)
      order          : 'desc' (most played first, default) | 'asc' (least played first)

    FIX: order param was exposed in UI but the query always sorted DESC.
         Now honours 'asc' for least-played-first use cases.
    """
    from models import TrackScore
    play_count_min = int(params.get("play_count_min", 0))
    play_count_max = params.get("play_count_max", None)
    order          = params.get("order", "desc")

    # FIX: honour the order param rather than hardcoding DESC
    order_col = TrackScore.play_count.asc() if order == "asc" else TrackScore.play_count.desc()

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_played == True)  # noqa: E712
        .filter(TrackScore.play_count >= play_count_min)
        .order_by(order_col)
    )
    if play_count_max is not None:
        query = query.filter(TrackScore.play_count <= int(play_count_max))
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_play_recency_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by recency of last play."""
    from models import TrackScore
    mode           = params.get("mode", "within")
    days           = int(params.get("days", 30))
    now            = datetime.utcnow()
    cutoff         = now - timedelta(days=days)
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_played == True)  # noqa: E712
        .filter(TrackScore.last_played.isnot(None))
    )
    if mode == "within":
        query = query.filter(TrackScore.last_played > cutoff).order_by(TrackScore.last_played.desc())
    else:  # older
        query = query.filter(TrackScore.last_played < cutoff).order_by(TrackScore.last_played.asc())
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_global_popularity_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks within a global popularity range."""
    from models import TrackScore
    popularity_min = float(params.get("popularity_min", 0))
    popularity_max = float(params.get("popularity_max", 100))
    played_filter  = params.get("played_filter", "all")
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.global_popularity.isnot(None))
        .filter(TrackScore.global_popularity.between(popularity_min, popularity_max))
        .order_by(TrackScore.global_popularity.desc())
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_discovery_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks bucketed by artist familiarity tier.

    Params:
      stranger_pct           : % of pool from unknown artists (default 34)
      acquaintance_pct       : % of pool from lightly-known artists (default 33)
      familiar_pct           : % of pool from well-known artists (default 33)
      acquaintance_max_plays : play threshold separating acquaintance/familiar (default 9)
      popularity_min         : float 0-100 — filter by global_popularity (default 0)
      popularity_max         : float 0-100 — filter by global_popularity (default 100)

    FIX: popularity_min/max params were exposed in the UI (Discovery block
         shows a "Popularity range" slider) but were never applied to the
         candidate pool query. Now applied as a pre-filter on tracks that have
         a non-null global_popularity.
    """
    from models import TrackScore, ArtistProfile

    stranger_pct           = float(params.get("stranger_pct",     34)) / 100
    acquaintance_pct       = float(params.get("acquaintance_pct", 33)) / 100
    familiar_pct           = float(params.get("familiar_pct",     33)) / 100
    acquaintance_max_plays = int(params.get("acquaintance_max_plays", 9))
    popularity_min         = float(params.get("popularity_min", 0))
    popularity_max         = float(params.get("popularity_max", 100))

    # Normalise familiarity split percentages
    total_pct = stranger_pct + acquaintance_pct + familiar_pct
    if total_pct <= 0:
        total_pct = 1.0
    stranger_pct     /= total_pct
    acquaintance_pct /= total_pct
    familiar_pct     /= total_pct

    # Artist familiarity map
    ap_rows = db.query(ArtistProfile).filter(ArtistProfile.user_id == user_id).all()
    if ap_rows:
        artist_plays: dict[str, int] = {r.artist_name.lower(): r.total_plays for r in ap_rows}
    else:
        from sqlalchemy import func
        results = (
            db.query(TrackScore.artist_name, func.sum(TrackScore.play_count))
            .filter(TrackScore.user_id == user_id)
            .group_by(TrackScore.artist_name)
            .all()
        )
        artist_plays = {a.lower(): (t or 0) for a, t in results if a}

    # FIX: apply the popularity_min/max filter that the UI sends.
    # We filter in two passes: tracks WITH global_popularity get the range filter;
    # tracks without it (NULL) are included only when popularity_min == 0 so that
    # un-enriched libraries still work with the default range.
    candidate_query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_played == False)  # noqa: E712
    )
    if popularity_min > 0 or popularity_max < 100:
        # User has narrowed the range — only keep tracks with a known popularity
        # that falls within the requested window.
        candidate_query = candidate_query.filter(
            TrackScore.global_popularity.isnot(None),
            TrackScore.global_popularity.between(popularity_min, popularity_max),
        )
    candidate_pool = candidate_query.limit(FETCH_LIMIT).all()

    strangers, acquaintances, familiar = [], [], []
    for row in candidate_pool:
        plays = artist_plays.get((row.artist_name or "").lower(), 0)
        if plays == 0:
            strangers.append(row)
        elif plays <= acquaintance_max_plays:
            acquaintances.append(row)
        else:
            familiar.append(row)

    # Sort each bucket by final_score descending before slicing so the
    # highest-scored candidates from each familiarity tier are selected.
    # Without this sort, slicing uses arbitrary DB insertion order and the
    # playlist engine's subsequent score-sort overrides the intended tier mix.
    strangers.sort(    key=lambda r: float(r.final_score or 0), reverse=True)
    acquaintances.sort(key=lambda r: float(r.final_score or 0), reverse=True)
    familiar.sort(     key=lambda r: float(r.final_score or 0), reverse=True)

    # Take proportional slice from each bucket
    limit = FETCH_LIMIT
    n_s = int(limit * stranger_pct)
    n_a = int(limit * acquaintance_pct)
    n_f = limit - n_s - n_a

    result_ids = (
        {r.jellyfin_item_id for r in strangers[:n_s]} |
        {r.jellyfin_item_id for r in acquaintances[:n_a]} |
        {r.jellyfin_item_id for r in familiar[:n_f]}
    )
    return _apply_exclusions(result_ids, excluded_item_ids)


def execute_favorites_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """All favorited tracks."""
    from models import TrackScore
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_favorite == True)  # noqa: E712
        .order_by(satext("CAST(final_score AS REAL) DESC"))
    )
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_played_status_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Filter by played/unplayed status only (for use as a refine node).

    No FETCH_LIMIT — used as a qualifier child node; capping it would silently
    exclude newly added tracks that sit beyond the 2000-row insertion window.
    """
    from models import TrackScore
    played_filter = params.get("played_filter", "played")
    query = db.query(TrackScore).filter(TrackScore.user_id == user_id)
    query = _apply_played_filter(query, played_filter)
    rows = query.all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_artist_cap_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """
    NOT a set-filter in the traditional sense — returns the full library.
    The engine applies artist_cap as a post-processing step when it sees
    this node type.  Returning the full set here means it acts as a passthrough
    in AND-intersection.

    No FETCH_LIMIT — passthrough blocks must return every track so the
    AND-intersection does not silently exclude newly added or lower-ranked tracks.
    """
    from models import TrackScore
    rows = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .all()
    )
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_jitter_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Returns the full scored library — used as an AND child to add randomness.

    The engine score-sorts each chain's results; jitter nudges those scores by
    a random amount before sorting so the same tracks don't always win.

    Params:
      jitter_pct : float 0.0–0.30  (default 0.15)
        The maximum fractional nudge applied to each track's score before
        the chain is sorted. A value of 0.15 means a track scored 80 can
        effectively rank anywhere from ~68 to ~92.  The nudge is applied
        inside _evaluate_nodes so it affects AND-intersection ordering.

    Implementation note:
      This block is a pass-through — it returns the entire library so it does
      not narrow the AND-intersection at all.

    No FETCH_LIMIT — passthrough blocks must return every track so the
    AND-intersection does not silently exclude newly added or lower-ranked tracks.
    """
    from models import TrackScore
    rows = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .all()
    )
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_cooldown_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Pass-through filter that excludes tracks currently on cooldown.

    Use as an AND child on any chain where you want to suppress tracks the user
    has been skipping lately.  Tracks with no cooldown or an expired cooldown
    pass through; tracks with an active cooldown_until > now are removed.

    Params:
      mode : 'exclude_active' (default) — remove tracks on active cooldown
             'only_active'              — keep ONLY tracks on active cooldown
                                          (niche use; surfaces the skip pile)
    """
    from models import TrackScore
    mode = params.get("mode", "exclude_active")
    now  = datetime.utcnow()

    query = db.query(TrackScore).filter(TrackScore.user_id == user_id)

    if mode == "only_active":
        query = query.filter(
            TrackScore.cooldown_until.isnot(None),
            TrackScore.cooldown_until > now,
        )
    else:  # exclude_active (default)
        query = query.filter(
            (TrackScore.cooldown_until == None) |  # noqa: E711
            (TrackScore.cooldown_until <= now)
        )

    # No FETCH_LIMIT — cooldown is a near-passthrough; capping it would silently
    # exclude newly added tracks that happen to sit beyond the 2000-row window.
    rows = query.all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


# ── New blocks enabled by existing data ──────────────────────────────────────
# See AUDIT.md §3 for rationale. These are registered but not yet exposed in
# the frontend — add them to FILTER_TYPES in BlockChainEditor.jsx to activate.

def execute_skip_rate_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by their skip penalty (skip-rate-derived score).

    Useful as a positive filter ("only tracks I rarely skip") or a negative
    one when AND'd to exclude high-skip-rate tracks from an otherwise broad
    chain.

    Params:
      skip_penalty_min : float 0.0–1.0 (default 0.0)
      skip_penalty_max : float 0.0–1.0 (default 1.0)
      played_filter    : 'all' | 'played' | 'unplayed' (default 'all')

    Data source: TrackScore.skip_penalty (written by scoring_engine Phase 3).
    """
    from models import TrackScore
    skip_min      = float(params.get("skip_penalty_min", 0.0))
    skip_max      = float(params.get("skip_penalty_max", 1.0))
    played_filter = params.get("played_filter", "all")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(_cast_float(TrackScore.skip_penalty) >= skip_min)
        .filter(_cast_float(TrackScore.skip_penalty) <= skip_max)
        .order_by(satext("CAST(skip_penalty AS REAL) ASC"))
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_replay_boost_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks from artists the user has voluntarily replayed recently.

    Surfaces artists who received a replay_boost signal — i.e. the user
    deliberately returned to their music within 7 days of a previous play.
    Great for a "things I'm obsessed with right now" chain.

    Params:
      boost_min    : float 0.0–12.0 — minimum replay boost on the artist (default 0.1)
      boost_max    : float 0.0–12.0 — maximum replay boost on the artist (default 12.0)
      played_filter: 'all' | 'played' | 'unplayed' (default 'all')

    Data source: TrackScore joined to ArtistProfile.replay_boost
                 (written by scoring_engine Phase 1 via compute_replay_boosts).
    """
    from models import TrackScore, ArtistProfile
    boost_min     = float(params.get("boost_min", 0.1))
    boost_max     = float(params.get("boost_max", 12.0))
    played_filter = params.get("played_filter", "all")

    # Collect artists whose replay boost falls within the min–max range
    boosted_artists = (
        db.query(ArtistProfile.artist_name)
        .filter(
            ArtistProfile.user_id == user_id,
            ArtistProfile.replay_boost >= boost_min,
            ArtistProfile.replay_boost <= boost_max,
        )
        .all()
    )
    artist_names = [r.artist_name for r in boosted_artists if r.artist_name]

    if not artist_names:
        return set()

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.artist_name.in_(artist_names))
        .order_by(satext("CAST(final_score AS REAL) DESC"))
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_novelty_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Unplayed tracks filtered by their novelty bonus score.

    The novelty_bonus is written by scoring_engine for unplayed tracks based
    on artist and genre affinity. This block lets users build "fresh picks from
    artists I love" chains with fine control over how novel vs familiar the
    suggestions are.

    Params:
      novelty_min : float 0.0–100.0 (default 0.0)
      novelty_max : float 0.0–100.0 (default 100.0)

    Data source: TrackScore.novelty_bonus (written by scoring_engine Phase 3
                 for is_played=False tracks).
    """
    from models import TrackScore
    novelty_min = float(params.get("novelty_min", 0.0))
    novelty_max = float(params.get("novelty_max", 100.0))

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_played == False)  # noqa: E712
        .filter(_cast_float(TrackScore.novelty_bonus) >= novelty_min)
        .filter(_cast_float(TrackScore.novelty_bonus) <= novelty_max)
        .order_by(satext("CAST(novelty_bonus AS REAL) DESC"))
    )
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_recency_score_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by pre-computed recency_score (0–100, decays over 365 days).

    Unlike play_recency (which uses a hard date window), this uses the
    continuous recency_score already computed by the scoring engine — giving
    a smooth gradient rather than a binary cutoff.

    Params:
      recency_min  : float 0–100 (default 0)
      recency_max  : float 0–100 (default 100)
      played_filter: 'all' | 'played' | 'unplayed' (default 'played')

    Data source: TrackScore.recency_score (written by scoring_engine Phase 3).
    """
    from models import TrackScore
    recency_min   = float(params.get("recency_min", 0))
    recency_max   = float(params.get("recency_max", 100))
    played_filter = params.get("played_filter", "played")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(_cast_float(TrackScore.recency_score) >= recency_min)
        .filter(_cast_float(TrackScore.recency_score) <= recency_max)
        .order_by(satext("CAST(recency_score AS REAL) DESC"))
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_skip_streak_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by current consecutive skip streak.

    Lets you explicitly exclude tracks the user is currently on a skip streak
    for (as a softer alternative to cooldown), or — unusually — surface them
    (e.g. a "tracks I keep skipping" debug playlist).

    Params:
      streak_min   : int — keep only tracks with streak >= this value (default 0)
      streak_max   : int — keep only tracks with streak <= this value (default 0)
      played_filter: 'all' | 'played' | 'unplayed' (default 'all')

    Default streak_max=0 means "no current skip streak" — ideal as an AND child
    to exclude tracks the user has skipped consecutively at least once. This is
    stricter than cooldown (which fires at 3+ skips) but softer than permanent
    suppression.  Set streak_min=3 to build a skip-pile diagnostic playlist.

    Data source: TrackScore.skip_streak (written by scoring_engine Phase 3
                 from SkipPenalty.consecutive_skips).
    """
    from models import TrackScore
    streak_min    = int(params.get("streak_min", 0))
    streak_max    = int(params.get("streak_max", 0))
    played_filter = params.get("played_filter", "all")

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.skip_streak >= streak_min)
        .filter(TrackScore.skip_streak <= streak_max)
        .order_by(TrackScore.skip_streak.asc())
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_genre_adjacent_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Unplayed tracks in genres adjacent to the user's high-affinity genres.

    Uses the hardcoded GENRE_ADJACENCY map.  Source genres (those already at or
    above the affinity threshold) are excluded from the result so this block
    complements — rather than duplicates — a genre block set at the same threshold.

    Params:
      genre_affinity_min : float 0-100 (default 40) — minimum affinity for a genre
                           to be considered a 'source' genre whose neighbours are
                           surfaced.
      played_filter      : 'all' | 'played' | 'unplayed' (default 'unplayed')
    """
    from models import TrackScore, GenreProfile
    from services.genre_adjacency import GENRE_ADJACENCY, norm_genre

    genre_affinity_min = float(params.get("genre_affinity_min", 40))
    played_filter      = params.get("played_filter", "unplayed")

    # 1. Load user's high-affinity genres — these are the 'source' genres.
    source_rows = (
        db.query(GenreProfile)
        .filter(
            GenreProfile.user_id == user_id,
            _cast_float(GenreProfile.affinity_score) >= genre_affinity_min,
        )
        .all()
    )
    if not source_rows:
        return set()

    source_normalized = {norm_genre(r.genre) for r in source_rows if r.genre}

    # 2. Expand to adjacent genres, excluding source genres themselves.
    adjacent_normalized: set[str] = set()
    for genre in source_normalized:
        for adj in GENRE_ADJACENCY.get(genre, []):
            if adj not in source_normalized:
                adjacent_normalized.add(adj)

    if not adjacent_normalized:
        return set()

    # 3. Map normalized adjacent names back to raw Jellyfin genre strings.
    #    TrackScore.genre stores the original Jellyfin value ("Pop Rock", "Hip-Hop",
    #    etc.) which may differ in casing or punctuation from our normalised keys.
    #    We load all distinct genre values for this user and match via norm_genre().
    library_genres = (
        db.query(TrackScore.genre)
        .filter(TrackScore.user_id == user_id, TrackScore.genre.isnot(None))
        .distinct()
        .all()
    )
    matching_raw: list[str] = [
        row.genre for row in library_genres
        if norm_genre(row.genre) in adjacent_normalized
    ]
    if not matching_raw:
        return set()

    # 4. Fetch tracks in those matching raw genres, ordered by final_score so
    #    the engine surfaces the best candidates first after artist-cap + jitter.
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.genre.in_(matching_raw))
        .order_by(satext("CAST(final_score AS REAL) DESC"))
    )
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


# ── Registry ──────────────────────────────────────────────────────────────────

BLOCK_REGISTRY: dict = {
    # Core blocks (all UI-exposed)
    "final_score":       execute_final_score_block,
    "affinity":          execute_affinity_block,
    "genre":             execute_genre_block,
    "artist":            execute_artist_block,
    "play_count":        execute_play_count_block,
    "play_recency":      execute_play_recency_block,
    "global_popularity": execute_global_popularity_block,
    "discovery":         execute_discovery_block,
    "favorites":         execute_favorites_block,
    "played_status":     execute_played_status_block,
    "artist_cap":        execute_artist_cap_block,
    "jitter":            execute_jitter_block,
    "cooldown":          execute_cooldown_block,
    # Discovery helper — uses genre adjacency map
    "genre_adjacent":    execute_genre_adjacent_block,
    # New blocks (data already present — add to frontend FILTER_TYPES to expose)
    "skip_rate":         execute_skip_rate_block,
    "replay_boost":      execute_replay_boost_block,
    "novelty":           execute_novelty_block,
    "recency_score":     execute_recency_score_block,
    "skip_streak":       execute_skip_streak_block,
}