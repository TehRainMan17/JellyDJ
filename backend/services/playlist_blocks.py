"""
JellyDJ — Playlist Block Executors  (Phase 8 rewrite)

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
    """
    from models import TrackScore
    score_min = float(params.get("score_min", 0))
    score_max = float(params.get("score_max", 99))

    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(_cast_float(TrackScore.final_score) >= score_min)
        .filter(_cast_float(TrackScore.final_score) <= score_max)
        .order_by(satext("CAST(final_score AS REAL) DESC"))
    )
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
    """Tracks matching specified genres (empty = all genres)."""
    from models import TrackScore
    genres = params.get("genres", [])
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .order_by(satext("CAST(genre_affinity AS REAL) DESC"))
    )
    if genres:
        query = query.filter(TrackScore.genre.in_(genres))
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_artist_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks from specified artists (empty = all artists)."""
    from models import TrackScore
    artists = params.get("artists", [])
    query = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .order_by(satext("CAST(artist_affinity AS REAL) DESC"))
    )
    if artists:
        query = query.filter(TrackScore.artist_name.in_(artists))
    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)


def execute_play_count_block(
    user_id: str,
    params: dict,
    db: Session,
    excluded_item_ids: frozenset,
) -> set[str]:
    """Tracks filtered by play count range."""
    from models import TrackScore
    play_count_min = int(params.get("play_count_min", 0))
    play_count_max = params.get("play_count_max", None)
    order_col = TrackScore.play_count.desc()
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
    """Tracks bucketed by artist familiarity tier."""
    from models import TrackScore, ArtistProfile

    stranger_pct           = float(params.get("stranger_pct",     34)) / 100
    acquaintance_pct       = float(params.get("acquaintance_pct", 33)) / 100
    familiar_pct           = float(params.get("familiar_pct",     33)) / 100
    acquaintance_max_plays = int(params.get("acquaintance_max_plays", 9))

    # Normalise
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

    candidate_pool = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .filter(TrackScore.is_played == False)  # noqa: E712
        .limit(FETCH_LIMIT)
        .all()
    )

    strangers, acquaintances, familiar = [], [], []
    for row in candidate_pool:
        plays = artist_plays.get((row.artist_name or "").lower(), 0)
        if plays == 0:
            strangers.append(row)
        elif plays <= acquaintance_max_plays:
            acquaintances.append(row)
        else:
            familiar.append(row)

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
    """Filter by played/unplayed status only (for use as a refine node)."""
    from models import TrackScore
    played_filter = params.get("played_filter", "played")
    query = db.query(TrackScore).filter(TrackScore.user_id == user_id)
    query = _apply_played_filter(query, played_filter)
    rows = query.limit(FETCH_LIMIT).all()
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
    """
    from models import TrackScore
    rows = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .limit(FETCH_LIMIT)
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
      not narrow the AND-intersection at all. Its effect is purely in ordering:
      the engine will find this node in the tree and apply score jitter when
      building score_map for this chain.
    """
    from models import TrackScore
    rows = (
        db.query(TrackScore)
        .filter(TrackScore.user_id == user_id)
        .limit(FETCH_LIMIT)
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

    rows = query.limit(FETCH_LIMIT).all()
    return _apply_exclusions(_rows_to_set(rows), excluded_item_ids)

# ── Registry ──────────────────────────────────────────────────────────────────

BLOCK_REGISTRY: dict = {
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
}

