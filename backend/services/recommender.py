"""
JellyDJ Recommendation Engine — Module 5 (v8)

v8 changes — four bugs fixed:

  BUG 1  PATH E never ran (NameError crash, silently swallowed).
    Three ordering mistakes inside the `if all_relation_rows:` block caused
    an immediate NameError on the first PATH E candidate that reached the
    genre gate:
      a) `pop_cache_map` was used in the `holiday_artist_names` loop but
         defined ~60 lines later (after `if N > 1:`).
      b) `_holiday_re` was used in `_is_holiday_artist` at graph-build time
         but compiled ~60 lines later.
      c) `listeners_score_e` was referenced in the PATH E genre gate but
         was never assigned anywhere.
    Fix: move `_holiday_re` and `pop_cache_map` to the top of the PATH E
    block (before any use), and assign `listeners_score_e` from the
    PopularityCache payload alongside the other per-candidate fields.

  BUG 2  PATH D bypassed genre filter for any artist with ≥2M listeners.
    The genre gate reads: "allow if no tags, OR has tag overlap, OR
    listeners_score >= 90." The threshold for listeners_score=90 is only
    ~2M Last.fm listeners — reached by hundreds of artists including many
    the user actively dislikes (2Pac, Ice Cube, etc. sit at 97–99).
    These artists sail through with the full +15 never_heard boost and
    dominate the final sort.
    Fix: raise bypass threshold from 90 → 98 (~50M listeners — genuine
    global phenomena like The Beatles, Michael Jackson). Add tag-string
    normalisation so "Hip-Hop" matches "hip hop" etc.

  BUG 3  PATH B (taste-driven similarity) was consistently outscored by
    PATH D (global popularity).
    `_add_candidate` applies a +15 never_heard boost to any artist not in
    the library regardless of path. PATH D artists are by definition never
    in the library, so they *always* get the boost. PATH B artists with
    moderated listener scores therefore usually lose in the final sort.
    Additionally, PATH B's target was only 30% of limit but competed for
    slots with three other paths.
    Fixes:
      a) Increase PATH B target 30% → 45% of limit.
      b) Increase per_seed_cap from 20% → 30% of limit (floor 2).
      c) Reduce `never_heard` boost for PATH D from +15 to +8, and cap
         the PATH D target at 20% of limit (down from 25%) to leave more
         headroom for taste-driven paths.
      d) In the final sort, use a slot-reservation system: PATH A and
         PATH B results are placed first up to their targets, then PATH D
         and PATH E fill remaining slots. This prevents raw listener scores
         from displacing every affinity-driven recommendation.

  BUG 4  Genre normalisation mismatch between Jellyfin and Last.fm.
    `user_top_genres` is built from `GenreProfile.genre`, which mirrors
    Jellyfin's genre field (e.g. "Classic Rock", "R&B", "Hip-Hop").
    Last.fm tags use different casing/hyphenation ("classic rock", "rnb",
    "hip hop"). The overlap test `t in user_top_genres` was case-sensitive
    and token-exact, causing many valid genre matches to be missed.
    Fix: normalise both sides — lowercase, replace hyphens/underscores with
    spaces, strip punctuation — before comparison. Build a `_norm_genre`
    helper used consistently in `_genre_ok`, `_genre_affinity_score`, and
    `user_top_genres`.

Two public functions:
  recommend_library_tracks(user_id, playlist_type, limit, db) -> list[TrackResult]
  recommend_new_albums(user_id, limit, db)                    -> list[AlbumResult]

Scoring philosophy
------------------
For recommend_library_tracks, every track in the user's Jellyfin library is
scored using a weighted formula. The playlist_type controls the weights:

  "for_you"     — heavy affinity weighting (you already love this stuff)
  "discover"    — heavy novelty + popularity (push you toward new things)
  custom types can override weights via the `weights` parameter

Score components (all normalised 0–1 before weighting):
  affinity     — artist + genre affinity from UserTasteProfile
  popularity   — external popularity score (Spotify/Last.fm/etc), cached
  recency_inv  — inverse recency: bonus for tracks NOT played recently
  novelty      — bonus for tracks NEVER played (play_count == 0)

For recommend_new_albums, we:
  1. Take the user's top artists from their taste profile
  2. Find similar artists via the PopularityAggregator (cached)
  3. For each similar artist, ask Jellyfin if any of their albums exist in library
  4. Filter out albums already present
  5. Score by popularity + artist affinity of the source artist
  6. Return ranked list with "why recommended" reasoning text
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import httpx
import numpy as np
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Result dataclasses ------------------------------------------------------──

@dataclass
class TrackResult:
    jellyfin_item_id: str
    track_name: str
    artist_name: str
    album_name: str
    genre: str
    score: float
    play_count: int
    last_played: Optional[datetime]
    is_favorite: bool
    score_breakdown: dict = field(default_factory=dict)


@dataclass
class AlbumResult:
    artist_name: str
    album_name: str
    release_year: Optional[int]
    popularity_score: float       # final blended score 0–100
    image_url: Optional[str]
    why: str                      # human-readable reasoning
    source_artist: str            # the user's artist that led to this rec
    source_affinity: float        # how much the user likes source_artist (0–100)
    lastfm_listeners: float = 0.0 # raw Last.fm listener score 0–100
    rec_type: str = ""            # "missing_album" | "new_artist" | "hub_artist"


# ── Weight presets ------------------------------------------------------──────

# Playlist type weight presets.
# Each preset controls the relative importance of four scoring dimensions:
#
#   affinity    — how much the user has historically liked this artist/genre
#   popularity  — external popularity signal (Last.fm listener count etc.)
#   recency_inv — inverse recency: bonus for tracks NOT heard recently
#                 (high value = surfaces forgotten music; low = sticks to current faves)
#   novelty     — 1.0 for never-played tracks, 0.0 for played
#
# These presets are intentionally well-separated so each playlist type
# feels meaningfully different. Add your own presets here and they'll
# automatically appear in the API and UI.
WEIGHT_PRESETS = {
    # for_you: anchored in taste, but rotates through catalogue via recency_inv
    "for_you": {
        "affinity":    0.55,
        "popularity":  0.15,
        "recency_inv": 0.20,
        "novelty":     0.10,
    },
    # discover: novelty-first; affinity still filters out irrelevant genres
    "discover": {
        "affinity":    0.15,
        "popularity":  0.30,
        "recency_inv": 0.15,
        "novelty":     0.40,
    },
    # favourites: maximises affinity so only beloved artists appear
    "favourites": {
        "affinity":    0.70,
        "popularity":  0.10,
        "recency_inv": 0.10,
        "novelty":     0.10,
    },
    # popular: ignore personal taste, surface globally popular music
    "popular": {
        "affinity":    0.20,
        "popularity":  0.60,
        "recency_inv": 0.10,
        "novelty":     0.10,
    },
}

RECENCY_STALE_DAYS   = 30    # tracks not played in this many days get a recency bonus
RECENCY_WINDOW_DAYS  = 365   # tracks played longer ago than this get max recency bonus


# ── Genre normalisation ------------------------------------------------------─

def _norm_genre(s: str) -> str:
    """
    Normalise a genre/tag string for comparison.
    Handles mismatches between Jellyfin genre fields and Last.fm tags:
      "Classic Rock" → "classic rock"
      "Hip-Hop"      → "hip hop"
      "R&B"          → "r&b"
      "hip_hop"      → "hip hop"
    """
    import re as _re
    s = s.lower().strip()
    s = _re.sub(r"[-_]", " ", s)   # hyphens/underscores → space
    s = _re.sub(r"\s+", " ", s)    # collapse whitespace
    return s


def _norm_track(s: str) -> str:
    """
    Normalise a track name for fuzzy matching between Last.fm and Jellyfin.
    Strips punctuation, lowercases, collapses whitespace so that
    'Good Luck, Babe!' matches 'Good Luck Babe' and
    'HOT TO GO!' matches 'Hot to Go!'.
    """
    import re as _re
    s = s.lower().strip()
    s = _re.sub(r"[^\w\s]", "", s)   # strip all punctuation
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _norm_album_title(s: str) -> str:
    """
    Lightweight unicode transliteration for album title key comparison.
    Handles album titles like Ed Sheeran's "× (Deluxe Edition)" where the
    × symbol (U+00D7) is stripped as punctuation, leaving an empty string
    that matches nothing in known_albums set lookups.
    """
    _MAP = {"×": "x", "÷": "/", "–": "-", "—": "-"}
    for u, a in _MAP.items():
        s = s.replace(u, a)
    return s.lower()


# ── Helpers ------------------------------------------------------─────────────

def _affinity_map(db: Session, user_id: str) -> tuple[dict[str, float], dict[str, float]]:
    """Return (artist_affinity, genre_affinity) dicts normalised to 0–1."""
    from models import UserTasteProfile

    rows = db.query(UserTasteProfile).filter_by(user_id=user_id).all()
    artists: dict[str, float] = {}
    genres:  dict[str, float] = {}

    for r in rows:
        score = float(r.affinity_score)
        if r.artist_name:
            artists[r.artist_name.lower()] = score
        elif r.genre:
            genres[r.genre.lower()] = score

    def _norm(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return d
        mx = max(d.values()) or 1.0
        return {k: v / mx for k, v in d.items()}

    return _norm(artists), _norm(genres)


def _recency_score(last_played: Optional[datetime]) -> float:
    """
    Returns 0.0 if played very recently, 1.0 if never played or very stale.
    """
    if last_played is None:
        return 1.0
    days = (datetime.utcnow() - last_played).days
    if days < RECENCY_STALE_DAYS:
        return 0.0
    return min(1.0, (days - RECENCY_STALE_DAYS) / (RECENCY_WINDOW_DAYS - RECENCY_STALE_DAYS))


def _fetch_all_library_tracks(db: Session, user_id: str) -> list:
    """
    Return all Play rows for this user — these represent the full Jellyfin
    music library as indexed (played + unplayed items from the indexer).
    """
    from models import Play
    return db.query(Play).filter_by(user_id=user_id).all()


def _get_popularity(artist: str, db: Session) -> float:
    """
    Pull cached artist-level popularity score (0–100, normalised to 0–1).
    Used as fallback when no track-level enrichment exists.
    """
    from models import PopularityCache
    import json
    key = f"artist:{artist.lower()}"
    row = db.query(PopularityCache).filter_by(cache_key=key).first()
    if not row:
        return 0.5
    try:
        data = json.loads(row.payload)
        return min(1.0, float(data.get("popularity_score", 50)) / 100.0)
    except Exception:
        return 0.5


def _get_track_popularity(jellyfin_item_id: str, artist: str, db: Session) -> float:
    """
    Per-track popularity score (0–1), sourced from TrackEnrichment.

    Uses the actual Last.fm listener count for this specific song — so
    'Creep' by Radiohead scores far higher than an obscure B-side by the
    same artist, even though _get_popularity() would give them equal scores.

    Falls back to artist-level PopularityCache if no track enrichment exists
    (e.g. enrichment hasn't run yet, or the track wasn't found on Last.fm).
    """
    from models import TrackEnrichment
    row = db.query(TrackEnrichment).filter_by(
        jellyfin_item_id=jellyfin_item_id
    ).first()
    if row and row.popularity_score is not None:
        return min(1.0, float(row.popularity_score) / 100.0)
    # Fallback: artist-level score — better than nothing
    return _get_popularity(artist, db)


def _get_skip_penalty(jellyfin_item_id: str, user_id: str, db: Session) -> float:
    """
    Return the skip penalty for a track (0.0 = no penalty, up to MAX_PENALTY).
    Returns 0.0 if not enough data yet.
    """
    from models import SkipPenalty
    row = db.query(SkipPenalty).filter_by(
        user_id=user_id,
        jellyfin_item_id=jellyfin_item_id,
    ).first()
    if not row:
        return 0.0
    return float(row.penalty)


# ── Main functions ------------------------------------------------------──────

def recommend_library_tracks(
    user_id: str,
    playlist_type: str,
    limit: int,
    db: Session,
    custom_weights: Optional[dict] = None,
) -> list[TrackResult]:
    """
    Score every track in the user's Jellyfin library and return the top `limit`.

    Randomness strategy (moderate):
    - Top tier  (score >= 0.75): stable — your genuine favorites always appear
    - Mid tier  (score 0.40–0.74): ±15% random jitter before sort → varies each run
    - Deep cuts: tracks with high affinity but not played in 6+ months get a bonus
    - The mid-tier jitter means each generation feels noticeably different
      while your top ~20% stays anchored
    """
    import random
    weights = custom_weights or WEIGHT_PRESETS.get(playlist_type, WEIGHT_PRESETS["for_you"])
    artist_aff, genre_aff = _affinity_map(db, user_id)

    tracks = _fetch_all_library_tracks(db, user_id)
    if not tracks:
        log.warning(f"No tracks indexed for user {user_id}")
        return []

    # Bulk-load all TrackEnrichment popularity scores in one query to avoid
    # N+1 DB calls (one per track) inside the scoring loop.
    # Falls back to artist-level PopularityCache for any un-enriched tracks.
    from models import TrackEnrichment
    track_ids = [t.jellyfin_item_id for t in tracks]
    enrichment_scores: dict[str, float] = {}
    for row in db.query(
        TrackEnrichment.jellyfin_item_id,
        TrackEnrichment.popularity_score
    ).filter(TrackEnrichment.jellyfin_item_id.in_(track_ids)).all():
        if row.popularity_score is not None:
            enrichment_scores[row.jellyfin_item_id] = min(1.0, float(row.popularity_score) / 100.0)

    now = datetime.utcnow()
    results: list[TrackResult] = []

    for t in tracks:
        # ── Affinity component ------------------------------------────────
        a_score = artist_aff.get(t.artist_name.lower(), 0.0)
        g_score = genre_aff.get(t.genre.lower(), 0.0)
        affinity = max(a_score, g_score * 0.7)

        # ── Popularity component — per-track from bulk-loaded enrichment ──
        # Uses actual Last.fm listener count for this specific song so that
        # popular hits score higher than B-sides from the same artist.
        # Falls back to artist-level cache for un-enriched tracks.
        if t.jellyfin_item_id in enrichment_scores:
            popularity = enrichment_scores[t.jellyfin_item_id]
        else:
            popularity = _get_popularity(t.artist_name, db)

        # ── Recency inverse ------------------------------------───────────
        recency_inv = _recency_score(t.last_played)

        # ── Novelty ------------------------------------------------------─
        # Gradual decay rather than binary — a track played once or twice
        # still gets meaningful novelty credit. Fully decays at 10+ plays.
        # This prevents played-once tracks from immediately competing on
        # pure affinity (which re-anchors to familiar artists).
        novelty = max(0.0, 1.0 - (t.play_count or 0) / 10.0)

        # ── Weighted sum ------------------------------------──────────────
        score = (
            weights["affinity"]    * affinity    +
            weights["popularity"]  * popularity  +
            weights["recency_inv"] * recency_inv +
            weights["novelty"]     * novelty
        )

        if t.is_favorite:
            score = min(1.0, score + 0.05)

        skip_penalty = _get_skip_penalty(t.jellyfin_item_id, user_id, db)
        if skip_penalty > 0:
            score = score * (1.0 - skip_penalty)

        # ── Deep cut bonus: loved artist, not heard in 6+ months ─────────
        # Gives forgotten favorites a chance to resurface
        if (
            affinity >= 0.65 and
            t.play_count and t.play_count > 0 and
            t.last_played and (now - t.last_played).days >= 180
        ):
            score = min(1.0, score + 0.12)

        results.append(TrackResult(
            jellyfin_item_id=t.jellyfin_item_id,
            track_name=t.track_name,
            artist_name=t.artist_name,
            album_name=t.album_name,
            genre=t.genre,
            score=round(score, 4),
            play_count=t.play_count,
            last_played=t.last_played,
            is_favorite=t.is_favorite,
            score_breakdown={
                "affinity":    round(affinity, 3),
                "popularity":  round(popularity, 3),
                "recency_inv": round(recency_inv, 3),
                "novelty":     round(novelty, 3),
                "skip_penalty": round(skip_penalty, 3),
            },
        ))

    results.sort(key=lambda r: r.score, reverse=True)

    # ── Tiered randomness ------------------------------------─────────────────
    # Top tier (score >= 0.75): stable — sort-stable, always included first
    # Mid tier (0.40–0.74): jitter score by ±15% before re-sorting
    # Bottom tier (< 0.40): excluded unless we need to fill
    TOP_THRESHOLD  = 0.75
    MID_THRESHOLD  = 0.40
    JITTER_RANGE   = 0.15   # ±15% of the score value

    top_tier  = [r for r in results if r.score >= TOP_THRESHOLD]
    mid_tier  = [r for r in results if MID_THRESHOLD <= r.score < TOP_THRESHOLD]
    low_tier  = [r for r in results if r.score < MID_THRESHOLD]

    # Jitter the mid tier — each track gets a different random bonus/penalty
    for r in mid_tier:
        jitter = random.uniform(-JITTER_RANGE, JITTER_RANGE) * r.score
        r.score = round(max(0.0, min(1.0, r.score + jitter)), 4)
    mid_tier.sort(key=lambda r: r.score, reverse=True)

    # Combine: top first (stable), then jittered mid, then low as fallback
    combined = top_tier + mid_tier + low_tier

    # ── Per-artist cap for discover playlist ------------------───────────────
    # Prevents the feedback loop where high-affinity artists (Adele, Cher)
    # dominate every generated playlist just because they have the most tracks.
    # Cap: max 3 tracks per artist in a discover playlist, 5 for for_you/favourites.
    if playlist_type in ("discover", "popular"):
        per_artist_cap = 3
    else:
        per_artist_cap = 5

    artist_counts: dict[str, int] = {}
    capped: list[TrackResult] = []
    for r in combined:
        key = r.artist_name.lower()
        if artist_counts.get(key, 0) < per_artist_cap:
            capped.append(r)
            artist_counts[key] = artist_counts.get(key, 0) + 1
    combined = capped

    return combined[:limit]


def _score_album_from_top_tracks(top_tracks: list[dict]) -> dict[str, float]:
    """
    Given an artist's top-tracks list (each entry has name, listeners, rank, album),
    score each album by the positional-weighted sum of its songs that appear in the list.

    Positional weights (rank 1 is worth the most):
      rank 1 → 5 pts, rank 2 → 4 pts, rank 3 → 3 pts, rank 4 → 2 pts, rank 5+ → 1 pt

    Each pt is then multiplied by a listener-count factor so a massive hit at #2
    can still beat a moderate track at #1 if the listener gap is large.

    Returns dict of {album_name: weighted_score}, sorted descending.
    Albums with no attributed tracks are not included.

    Tie-breaking: when two albums share the same rounded score, the one containing
    the single highest-ranked song wins (lowest rank number = earlier = better).
    """
    POSITIONAL_WEIGHTS = {1: 5, 2: 4, 3: 3, 4: 2}
    DEFAULT_WEIGHT = 1

    # Normalise listener counts across the track list so they act as a multiplier
    # rather than completely dominating the positional signal.
    max_listeners = max((t.get("listeners", 1) or 1) for t in top_tracks) or 1

    album_scores: dict[str, float] = {}
    # Track the best (lowest) rank for each album for tie-breaking
    album_best_rank: dict[str, int] = {}

    # Normalise album names for comparison (strip "Deluxe", "Remastered", etc.)
    _ALBUM_NOISE = re.compile(
        r'\s*[\(\[](deluxe|expanded|remaster(?:ed)?|anniversary|special'
        r'|edition|version|bonus|explicit)[^\)\]]*[\)\]]',
        re.IGNORECASE,
    )

    def _norm_album(name: str) -> str:
        return _ALBUM_NOISE.sub("", name).strip().lower()

    # Build a canonical → display-name map so we return the original name
    canonical_to_display: dict[str, str] = {}

    for i, track in enumerate(top_tracks):
        album = (track.get("album") or "").strip()
        if not album:
            continue  # no album attribution — skip, don't guess
        canonical = _norm_album(album)
        canonical_to_display.setdefault(canonical, album)

        rank = track.get("rank") or (i + 1)
        pos_weight = POSITIONAL_WEIGHTS.get(rank, DEFAULT_WEIGHT)
        listeners = track.get("listeners") or 0
        listener_factor = 1.0 + (listeners / max_listeners)  # 1.0–2.0
        score = pos_weight * listener_factor

        album_scores[canonical] = album_scores.get(canonical, 0.0) + score
        # Keep track of the best (lowest-numbered) rank for this album
        if canonical not in album_best_rank or rank < album_best_rank[canonical]:
            album_best_rank[canonical] = rank

    # Re-key back to display names, sort by (score desc, best_rank asc)
    result = {}
    for canonical, score in album_scores.items():
        display = canonical_to_display.get(canonical, canonical)
        result[display] = score

    return dict(
        sorted(
            result.items(),
            key=lambda kv: (-kv[1], album_best_rank.get(_norm_album(kv[0]), 99))
        )
    )


def _get_best_album_for_artist(
    artist_name: str,
    db,
    known_track_names: set | None = None,
) -> tuple[str, Optional[int], Optional[str]]:
    """
    Return the album most worth recommending for a given artist, chosen by
    scoring each album against the artist's top-5 songs on Last.fm.

    Algorithm:
      1. Load ArtistEnrichment.top_tracks (pre-fetched by enrichment service,
         already includes album attribution for top-5 tracks via track.getInfo).
      2. Score albums using positional-weighted song counts (_score_album_from_top_tracks).
         Weighted sum: rank-1 song = 5pts, rank-2 = 4pts, … rank-5+ = 1pt,
         each multiplied by a listener-count factor so monster hits carry extra weight.
         Tie-break: album containing the single highest-ranked song wins.
      3. Skip obvious compilations from the winner.
      4. Skip any album whose hit tracks are already in the user's library
         (>= 50% of the album's attributed top tracks already owned).
         This catches deluxe/edition variants like "Midwest Princess (Deluxe)"
         when the user already owns "Midwest Princess" with the same songs.
      5. Fall back to PopularityCache top_album only if no track→album data exists.

    known_track_names: set of "artist_lower::track_name_lower" keys from the
    user's library. Pass None to skip the overlap check.

    Returns (album_name, release_year, image_url).
    """
    import json
    import re as _re
    from models import ArtistEnrichment, PopularityCache

    _COMPILATION_WORDS = [
        "greatest hits", "best of", "collection", "essential", "platinum",
        "gold", "anthology", "singles", "ultimate", "the very best",
        "definitive", "complete collection",
    ]

    _ALBUM_NOISE = _re.compile(
        r'\s*[\(\[](deluxe|expanded|remaster(?:ed)?|anniversary|special'
        r'|edition|version|bonus|explicit)[^\)\]]*[\)\]]',
        _re.IGNORECASE,
    )
    # Strip "Track By Track", "Super Deluxe", "Commentary" etc. that appear
    # as bare suffixes (no brackets) — e.g. "Overexposed Track By Track"
    _ALBUM_SUFFIX_NOISE = _re.compile(
        r'\s+(track\s+by\s+track|commentary|super\s+deluxe|'
        r'\d+th\s+anniversary|anniversary\s+edition|deluxe\s+edition|'
        r'expanded\s+edition|special\s+edition|bonus\s+tracks?)\s*$',
        _re.IGNORECASE,
    )

    def _norm_alb(name: str) -> str:
        s = _ALBUM_NOISE.sub("", name).strip()
        s = _ALBUM_SUFFIX_NOISE.sub("", s).strip()
        return s.lower()



    # ── Step 1+2: top songs → scored album map ------------------──────────────
    ae = db.query(ArtistEnrichment).filter_by(
        artist_name_lower=artist_name.lower()
    ).first()

    if ae and ae.top_tracks:
        try:
            top_tracks = json.loads(ae.top_tracks)
            scored = _score_album_from_top_tracks(top_tracks)

            # Walk the ranked list, skip compilations and already-owned hits
            for album_name, _score in scored.items():
                if any(w in album_name.lower() for w in _COMPILATION_WORDS):
                    log.debug(
                        "  Album picker: skipping '%s' for '%s' (compilation)",
                        album_name, artist_name,
                    )
                    continue

                # ── Hit-track overlap check ───────────────────────────────────
                # Find which top tracks are attributed to this album (normalised
                # to strip deluxe/remaster suffixes so "Midwest Princess (Deluxe)"
                # matches tracks attributed to "Midwest Princess").
                if known_track_names is not None:
                    album_tracks = [
                        t for t in top_tracks
                        if t.get("album") and _norm_alb(t["album"]) == _norm_alb(album_name)
                    ]
                    if not album_tracks:
                        # No album attribution for this specific album title.
                        # Only fall back to tracks that DO have any album
                        # attribution (top 5) — using all 10 dilutes the owned
                        # ratio because tracks 6-10 never have album data and
                        # may not be in the user's library, dropping 3/3 owned
                        # to 3/10 (30%) and bypassing the 50% threshold.
                        album_tracks = [t for t in top_tracks if t.get("album")]
                        if not album_tracks:
                            # Truly no attribution at all — use top 5 by rank
                            album_tracks = top_tracks[:5]

                    if album_tracks:
                        artist_lower = artist_name.lower()
                        owned = sum(
                            1 for t in album_tracks
                            if t.get("name") and
                            f"{artist_lower}::{_norm_track(t['name'])}" in known_track_names
                        )
                        overlap_pct = owned / len(album_tracks)
                        if overlap_pct >= 0.50:
                            log.info(
                                "  Album picker: skipping '%s' for '%s' — "
                                "%d/%d hit tracks already in library (%.0f%%)",
                                album_name, artist_name,
                                owned, len(album_tracks), overlap_pct * 100,
                            )
                            continue

                image_url = _get_album_image(artist_name, album_name, db)
                log.debug(
                    "  Album picker: chose '%s' for '%s' (score=%.1f, ranked albums=%s)",
                    album_name, artist_name, _score,
                    {k: round(v, 1) for k, v in list(scored.items())[:4]},
                )
                return album_name, None, image_url

        except Exception as exc:
            log.debug("  Album picker: top_tracks parse failed for '%s': %s", artist_name, exc)

    # ── Fallback: top_album from popularity cache ------------------───────────
    key = f"top_album:{artist_name.lower()}"
    row = db.query(PopularityCache).filter_by(cache_key=key).first()
    if row:
        try:
            d = json.loads(row.payload)
            name = d.get("name") or d.get("album", "")
            return name, d.get("year"), d.get("image_url")
        except Exception:
            pass

    return "", None, None


def _get_album_image(artist_name: str, album_name: str, db) -> Optional[str]:
    """Try to find an image URL for a specific album from the discography cache."""
    import json
    from models import PopularityCache
    try:
        key = f"discography:{artist_name.lower()}"
        row = db.query(PopularityCache).filter_by(cache_key=key).first()
        if row:
            albums = json.loads(row.payload).get("albums", [])
            for alb in albums:
                if alb.get("name", "").lower() == album_name.lower():
                    return alb.get("image_url")
    except Exception:
        pass
    from models import ArtistEnrichment
    ae = db.query(ArtistEnrichment).filter_by(artist_name_lower=artist_name.lower()).first()
    return ae.image_url if ae else None


# Keep old name as alias so any other callers don't break
def _get_top_album_from_cache(artist_name: str, db) -> tuple[str, Optional[int], Optional[str]]:
    return _get_best_album_for_artist(artist_name, db)


# ── Holiday keyword filter (module-level so all paths can use it) ─────────────

def _build_holiday_re():
    """
    Build a compiled regex matching holiday/seasonal keywords from HOLIDAY_RULES
    plus common Last.fm holiday tags. Called once at import time.
    """
    import re as _re
    keywords: set[str] = set()
    try:
        from services.holiday import HOLIDAY_RULES
        for _slug, _keywords, _start, _end in HOLIDAY_RULES:
            for _kw in _keywords:
                keywords.add(_kw.lower())
    except Exception:
        pass
    keywords.update({
        "christmas music", "holiday", "holiday music", "seasonal",
        "winter holiday", "christmas songs", "christmas carols",
        "halloween", "thanksgiving", "christmas", "xmas",
    })
    return _re.compile(
        r'\b(' + '|'.join(_re.escape(kw) for kw in sorted(keywords)) + r')\b',
        _re.IGNORECASE,
    )

_HOLIDAY_RE = _build_holiday_re()


def _is_holiday_artist(name_lower: str, tags: list[str]) -> bool:
    """
    Return True if this artist is primarily a holiday act.
    - Artist name contains a holiday keyword → exclude
    - >= 50% of their Last.fm tags are holiday keywords → exclude
    - A single holiday tag among many real-genre tags → keep (handled by hard block in paths)
    """
    if _HOLIDAY_RE.search(name_lower):
        return True
    if tags:
        holiday_count = sum(1 for t in tags if _HOLIDAY_RE.search(t.lower()))
        if holiday_count >= len(tags) / 2:
            return True
    return False


def _has_holiday_tag(tags: list[str]) -> bool:
    """Return True if ANY tag is a holiday keyword. Used as a hard block in all paths."""
    return any(_HOLIDAY_RE.search(t.lower()) for t in tags)


def recommend_new_albums(
    user_id: str,
    limit: int,
    db,
    path_e_global_seen: set = None,
) -> list[AlbumResult]:
    """
    Album-first discovery engine — song-quality driven, taste-aware.

    Core philosophy:
      Albums are suggested because (a) the artist is globally popular and new
      to your library, OR (b) an existing artist has a highly popular album you
      don't own.  In BOTH cases, the album is chosen by finding the artist's
      most globally popular songs on Last.fm and picking the album that
      contains the highest-scoring cluster of those songs.

    Scoring formula (inside _add_candidate):
      blended = (lastfm_listeners * 0.50) + (pop_score * 0.25) + (genre_affinity * 0.25)

      genre_affinity is computed per-candidate from tag overlap with the user's
      top genres — it replaces the old hardcoded source_affinity tiebreaker so
      taste is a genuine 25% signal rather than a footnote.

    Genre filtering (PATH D — medium strictness):
      - Artists with ≥1 tag overlapping user's top genres: allowed + genre affinity bonus
      - Artists with no tag overlap: allowed only if listeners_score >= 90 (global phenomena)
      - Artists with no tags at all: treated as neutral, full score
      - Genre cap is taste-aware: user's own genres cap at 5, others at 2

    Diversity controls:
      - Per-seed-artist cap: floor(limit * 0.20), min 1.
      - Taste-aware genre cap: liked genres 5, neutral genres 2.
      - Seed artist selection: structured tiered sampling.
      - 90-day artist suppression + 180-day rejection suppression.

    Four recommendation paths:
      PATH A — Missing albums from known artists (complete their collection).
                Capped at 25% of limit. Uses top-songs→album logic.
      PATH B — New artists via similarity chains from sampled seed artists.
                Capped per seed artist at floor(limit * 0.20).
      PATH D — Globally popular artists (>=1M listeners) not in library.
                Targets 40% of output. Genre-filtered (medium strictness).
      PATH E — Genre-centrality / "hub artist" discovery (PageRank-style).
                Builds a directed artist-similarity graph from ArtistRelation
                rows, runs simplified PageRank (10 iters, damping=0.85), and
                surfaces artists with high in-degree — i.e. many other artists
                list them as similar — who are not yet in the user's library.
                Requires >=2 library artists to vote for a candidate.
                Targets 20% of output. Score blends listener reach (40%),
                popularity (25%), PageRank centrality (25%), genre affinity
                (10%) plus a per-voter bonus (up to +15 pts).
    """
    import random
    import json
    import math
    import re as _re
    from models import UserTasteProfile, Play, PopularityCache, DiscoveryQueueItem

    # ── Per-seed-artist diversity cap ------------------------------------────
    # Hard cap of 3 recs per seed artist regardless of limit.
    #
    # Previously round(limit * 0.30) sounded reasonable, but discovery.py calls
    # recommend_new_albums(user_id, items_per_run * 4, db) to build an oversized
    # candidate pool.  For items_per_run=10 that makes limit=40, giving
    # per_seed_cap=12 — one artist like Maroon 5 could fill 12 "because you like X"
    # slots in a run that only shows 10 items to the user.
    # A hard cap of 3 gives meaningful representation per seed while ensuring
    # ~4+ different seed artists contribute to every pool.
    per_seed_cap = 3

    # ── Taste-aware genre caps ------------------------------------────────────
    # Genres the user actively listens to get more headroom than unknown genres.
    # This lets familiar genres produce more recs while still blocking any single
    # genre from completely dominating the run.
    #   User's liked genres  → cap 5  (room to go deep on what you love)
    #   Everything else      → cap 2  (sample, don't saturate)
    GENRE_CAP_LIKED   = 5
    GENRE_CAP_NEUTRAL = 2
    genre_counts: dict[str, int] = {}

    # ── Load taste profile ------------------------------------───────────────
    top_artists_rows = (
        db.query(UserTasteProfile)
        .filter_by(user_id=user_id)
        .filter(UserTasteProfile.artist_name.isnot(None))
        .order_by(UserTasteProfile.affinity_score.desc())
        .limit(30)
        .all()
    )

    if not top_artists_rows:
        log.warning(f"No taste profile for user {user_id} — run indexer first")
        return []

    max_affinity = max(float(r.affinity_score) for r in top_artists_rows) or 1.0
    affinity_map = {
        r.artist_name: min(100.0, float(r.affinity_score) / max_affinity * 100)
        for r in top_artists_rows
    }

    # ── Structured seed selection ------------------------------------────────
    # Only seed from artists the user genuinely likes.
    #
    # The old approach sampled from three tiers including positions 15-30.
    # This caused low-affinity artists (e.g. Radiohead at #24 with one liked
    # song) to occasionally seed a full 3-rec chain, producing suggestions
    # the user would find baffling.
    #
    # New approach:
    #   - Hard affinity floor: only artists whose normalised affinity >= 30
    #     are eligible as seeds. A #24 artist with weak engagement is excluded.
    #   - Two tiers only: top-5 (always sampled) + positions 5-15 (sampled).
    #   - Tier 1: sample all (up to 5) — these are your real favourites.
    #   - Tier 2: sample up to 5 from positions 5-14, weighted by affinity.
    #   - No tier3: positions 15+ are too weak to drive good discovery.
    SEED_AFFINITY_FLOOR = 30.0   # normalised 0-100; artists below this aren't seeded

    eligible = [r for r in top_artists_rows
                if (float(r.affinity_score) / max_affinity * 100) >= SEED_AFFINITY_FLOOR]

    tier1 = eligible[:5]
    tier2 = eligible[5:15]

    def _weighted_sample(rows, n):
        if not rows: return []
        n = min(n, len(rows))
        weights = [float(r.affinity_score) ** 0.5 for r in rows]
        total = sum(weights) or 1.0
        probs = [w / total for w in weights]
        indices = list(np.random.choice(len(rows), size=n, replace=False, p=probs))
        return [rows[i] for i in indices]

    seed_rows = (
        _weighted_sample(tier1, min(5, len(tier1))) +
        _weighted_sample(tier2, min(5, len(tier2)))
    )
    seen_seeds: set[str] = set()
    seed_rows = [r for r in seed_rows if not (r.artist_name in seen_seeds or seen_seeds.add(r.artist_name))]

    log.info(f"  Discovery seeds ({len(seed_rows)}): {[r.artist_name for r in seed_rows[:6]]}...")

    # ── Build known library sets ------------------------------------─────────
    from models import LibraryTrack
    from services.library_dedup import artist_in_library, album_in_library

    known_albums: set[str] = set()
    known_artists: set[str] = set()

    lib_rows = db.query(
        LibraryTrack.artist_name, LibraryTrack.album_name, LibraryTrack.album_artist
    ).filter(LibraryTrack.missing_since.is_(None)).all()

    for row in lib_rows:
        for name in (row.artist_name, row.album_artist):
            if name:
                known_artists.add(name.lower())
        if row.artist_name and row.album_name:
            known_albums.add(f"{row.artist_name.lower()}::{_norm_album_title(row.album_name)}")
        if row.album_artist and row.album_name:
            known_albums.add(f"{row.album_artist.lower()}::{_norm_album_title(row.album_name)}")

    for row in db.query(Play.artist_name, Play.album_name).filter_by(user_id=user_id).distinct():
        if row.artist_name:
            known_artists.add(row.artist_name.lower())
        if row.artist_name and row.album_name:
            known_albums.add(f"{row.artist_name.lower()}::{_norm_album_title(row.album_name)}")

    log.info(f"  Dedup sets: {len(known_artists)} artists, {len(known_albums)} albums in library")

    # ── Load exclusions list ------------------------------------─────────────
    # ExcludedAlbum rows are user-managed blacklists — never recommend these.
    from models import ExcludedAlbum
    excluded_artists: set[str] = set()
    excluded_album_keys: set[str] = set()
    for row in db.query(ExcludedAlbum).all():
        if row.artist_name:
            excluded_artists.add(row.artist_name.lower())
        if row.artist_name and row.album_name:
            excluded_album_keys.add(f"{row.artist_name.lower()}::{row.album_name.lower()}")
    log.info(f"  Exclusions: {len(excluded_artists)} excluded artists, {len(excluded_album_keys)} excluded albums")

    known_track_names: set[str] = set()
    for row in db.query(LibraryTrack.track_name, LibraryTrack.artist_name).filter(
        LibraryTrack.missing_since.is_(None)
    ).all():
        if row.track_name and row.artist_name:
            # Store normalised form so Last.fm punctuation variants
            # ("Good Luck, Babe!" vs "Good Luck Babe") still match.
            known_track_names.add(f"{row.artist_name.lower()}::{_norm_track(row.track_name)}")

    # ── Already-queued suppression ------------------------------------───────
    from datetime import timedelta
    cutoff_90d  = datetime.utcnow() - timedelta(days=90)
    cutoff_180d = datetime.utcnow() - timedelta(days=180)

    queued: set[str] = set()
    recently_queued_artists: set[str] = set()
    rejected_artists: set[str] = set()

    for row in db.query(
        DiscoveryQueueItem.artist_name,
        DiscoveryQueueItem.album_name,
        DiscoveryQueueItem.added_at,
        DiscoveryQueueItem.status,
    ).filter_by(user_id=user_id).all():
        key = f"{row.artist_name.lower()}::{(row.album_name or '').lower()}"
        queued.add(key)
        if row.status == "rejected" and row.added_at and row.added_at >= cutoff_180d:
            rejected_artists.add(row.artist_name.lower())
        elif row.added_at and row.added_at >= cutoff_90d:
            recently_queued_artists.add(row.artist_name.lower())

    # ── Top genres for taste-aware filtering and scoring ------------------───
    from models import GenreProfile
    top_genre_rows = (
        db.query(GenreProfile)
        .filter_by(user_id=user_id)
        .order_by(GenreProfile.affinity_score.desc())
        .limit(15)   # wider window so niche genres aren't missed
        .all()
    )
    # Set for fast membership tests — normalised for tag comparison
    user_top_genres = {_norm_genre(r.genre) for r in top_genre_rows if r.genre}
    # Normalised affinity scores 0–100 for scoring (tag → score)
    _max_genre_aff = max((float(r.affinity_score) for r in top_genre_rows), default=1.0) or 1.0
    user_genre_affinity: dict[str, float] = {
        _norm_genre(r.genre): min(100.0, float(r.affinity_score) / _max_genre_aff * 100)
        for r in top_genre_rows if r.genre
    }

    seen: set[str] = set()
    candidates: list[AlbumResult] = []

    # Track how many recs each seed artist has produced (for diversity cap)
    seed_artist_counts: dict[str, int] = {}

    def _build_why_text(
        artist: str,
        album_name: str,
        pop_score: float,
        source_context: str,
        top_tracks: list[dict],
        genre_hint: str = "",
    ) -> str:
        """
        Build a human-readable 'why recommended' string that highlights
        the specific popular songs that led to this album being chosen.

        Only mentions songs NOT already in the user's library — avoids
        saying "Features: Thinking Out Loud" when the user already owns it.
        Falls back to no song list if all hits are already owned.
        """
        import re as _re
        _NOISE = _re.compile(
            r'\s*[\(\[](deluxe|expanded|remaster(?:ed)?|anniversary|special'
            r'|edition|version|bonus|explicit)[^\)\]]*[\)\]]',
            _re.IGNORECASE,
        )
        _SUFFIX = _re.compile(
            r'\s+(track\s+by\s+track|commentary|super\s+deluxe|'
            r'\d+th\s+anniversary|anniversary\s+edition|deluxe\s+edition|'
            r'expanded\s+edition|special\s+edition|bonus\s+tracks?)\s*$',
            _re.IGNORECASE,
        )
        def _norm(s):
            s = _NOISE.sub("", s).strip()
            s = _SUFFIX.sub("", s).strip()
            return s.lower()

        # Tracks attributed to this specific album (normalised match)
        album_tracks = [
            t for t in top_tracks
            if t.get("album") and _norm(t["album"]) == _norm(album_name)
        ]
        # Fall back to tracks that have ANY attribution — never unattributed ones
        if not album_tracks:
            album_tracks = [t for t in top_tracks if t.get("album")][:3]

        # Filter to songs NOT already in the user's library so we never
        # advertise "features Thinking Out Loud" when the user owns it
        artist_lower = artist.lower()
        unowned = [
            t for t in album_tracks
            if t.get("name") and
            f"{artist_lower}::{_norm_track(t['name'])}" not in known_track_names
        ]

        hit_names = [t["name"] for t in unowned[:3] if t.get("name")]
        hits_str = ""
        if hit_names:
            if len(hit_names) == 1:
                hits_str = f" Features their hit '{hit_names[0]}'."
            else:
                hits_str = f" Features hits: {', '.join(repr(h) for h in hit_names)}."

        return (
            f"{source_context}{genre_hint}.{hits_str} "
            f"{'Recommended album: ' + album_name + '. ' if album_name else ''}"
            f"Popularity: {pop_score:.0f}/100."
        )

    def _genre_affinity_score(tags: list[str]) -> float:
        """
        Compute a 0–100 genre affinity score for an artist based on tag overlap
        with the user's genre profile.

        - No tags at all → 50.0 (neutral, not penalised per spec)
        - Tags present but none overlap → 0.0
        - Tags overlap → average affinity of matching tags, weighted by overlap count

        This replaces the old hardcoded source_affinity=0.0 for PATH D so
        taste is a real 25% signal in the blended score.
        """
        if not tags:
            return 50.0  # neutral — no data, don't penalise
        tag_lowers = [_norm_genre(t) for t in tags[:5]]
        matched_scores = [
            user_genre_affinity[tl]
            for tl in tag_lowers
            if tl in user_genre_affinity
        ]
        if not matched_scores:
            return 0.0
        # Weight by how many tags matched — more overlap = stronger signal
        overlap_bonus = min(20.0, (len(matched_scores) - 1) * 10.0)
        return min(100.0, (sum(matched_scores) / len(matched_scores)) + overlap_bonus)

    def _add_candidate(
        artist: str,
        album: str,
        release_year,
        pop_score: float,
        image_url,
        source_artist: str,
        source_affinity: float,
        why: str,
        rec_type: str,
        lastfm_listeners: float = 0.0,
        tags: list = None,
        is_wildcard: bool = False,
    ):
        # Require a specific album — artist-only recs with no album produce
        # queue items that show "album unknown" and add the artist to Lidarr
        # with monitor=future only, which downloads nothing and confuses users.
        if not album or not album.strip():
            log.debug("  Skipping '%s' — no album identified, would produce useless queue item", artist)
            return

        dedup = f"{artist.lower()}::{album.lower()}"
        if dedup in seen or dedup in queued:
            return

        if artist.lower() in rejected_artists:
            return
        if artist.lower() in recently_queued_artists:
            return
        if artist.lower() in excluded_artists:
            return
        if album and f"{artist.lower()}::{album.lower()}" in excluded_album_keys:
            return

        if album:
            exact_key = f"{artist.lower()}::{_norm_album_title(album)}"
            if exact_key in known_albums:
                return
            if album_in_library(artist, album, db):
                log.info(f"  Dedup (fuzzy album): '{album}' by '{artist}' — already in library")
                return
            # Check if the album's own name appears as a track key — this was
            # previously broken (compared album name against track-name keys)
            # and is now superseded by the hit-track overlap check below.
            # Left intentionally empty; overlap check in _add_candidate's caller
            # handles this case properly via known_track_names.

        seen.add(dedup)

        # ── Genre affinity signal ------------------------------------─────────
        # Compute from artist tags rather than source chain so PATH D candidates
        # get a real taste score instead of a hardcoded 0.
        # PATH A/B pass source_affinity explicitly; we take the max so seed-chain
        # affinity still counts when it's stronger than the genre signal.
        genre_aff = _genre_affinity_score(tags or [])
        effective_affinity = max(source_affinity, genre_aff)

        # ── Scoring ------------------------------------------------------─────
        # 50% global listener reach (was 60% — reduced to make room for taste)
        # 25% artist/album popularity signal
        # 25% taste affinity (was 15% — now a genuine signal, not a tiebreaker)
        blended = (
            (lastfm_listeners    * 0.50) +
            (pop_score           * 0.25) +
            (effective_affinity  * 0.25)
        )

        # "New but popular" boost: artist not in library + strong listener score.
        # Reduced from +15 to +8 so PATH D artists don't systematically outscore
        # taste-driven PATH B results which have moderated listener scores.
        never_heard = artist.lower() not in known_artists
        if never_heard and lastfm_listeners >= 60:
            blended = min(100.0, blended + 8.0)

        if is_wildcard:
            blended = min(100.0, blended + 5.0)

        candidates.append(AlbumResult(
            artist_name=artist,
            album_name=album,
            release_year=release_year,
            popularity_score=round(blended, 1),
            image_url=image_url,
            why=why,
            source_artist=source_artist,
            source_affinity=round(effective_affinity, 1),
            lastfm_listeners=round(lastfm_listeners, 1),
            rec_type=rec_type,
        ))

    # ── Taste-aware genre cap helpers ------------------------------------─────
    def _genre_cap_for(tag: str) -> int:
        """Return the cap for a given tag: higher for genres the user likes."""
        return GENRE_CAP_LIKED if _norm_genre(tag) in user_top_genres else GENRE_CAP_NEUTRAL

    def _genre_ok(tags: list[str]) -> bool:
        """
        Return True if this artist's top tags still have headroom.
        A tag is exhausted when its count reaches its cap.
        An artist with no tags is always OK (neutral, not penalised).
        """
        if not tags:
            return True
        for tag in tags[:3]:
            tl = _norm_genre(tag)
            if genre_counts.get(tl, 0) >= _genre_cap_for(tl):
                return False
        return True

    def _charge_genre(tags: list[str]):
        """Increment genre counters after a candidate is accepted."""
        for tag in tags[:3]:
            tl = _norm_genre(tag)
            genre_counts[tl] = genre_counts.get(tl, 0) + 1

    # ── PATH A: Missing albums from known artists ------------------───────────
    # Uses top-songs→album logic, same as PATH B/D — no special-casing for
    # known artists. Capped at 25% of limit.
    path_a_cap = max(1, round(limit * 0.25))
    path_a_count = 0

    # PATH B total cap — similarity chains get 45% of limit.
    # Increased from 30% to ensure taste-driven recs aren't crowded out by
    # raw global-popularity results from PATH D.
    # Per-seed cap ensures diversity across seeds.
    path_b_target = max(1, round(limit * 0.45))
    path_b_total  = 0

    for artist_name, aff_score in affinity_map.items():
        if path_a_count >= path_a_cap:
            break

        disco_key = f"discography:{artist_name.lower()}"
        disco_row = db.query(PopularityCache).filter_by(cache_key=disco_key).first()
        if not disco_row:
            continue
        try:
            albums_data = json.loads(disco_row.payload).get("albums", [])
        except Exception:
            continue

        # Get artist-level listener score for blending
        pop_key = f"artist:{artist_name.lower()}"
        pop_row = db.query(PopularityCache).filter_by(cache_key=pop_key).first()
        artist_listeners_score = 0.0
        artist_tags: list[str] = []
        if pop_row:
            try:
                pd = json.loads(pop_row.payload)
                raw_l = float(pd.get("listener_count", 0))
                if raw_l > 0:
                    artist_listeners_score = min(100.0, (math.log1p(raw_l) / math.log1p(10_000_000)) * 100)
                artist_tags = pd.get("tags", [])
            except Exception:
                pass

        # ── Whole-artist top-track coverage check ────────────────────────────
        # Before spending time picking an album, check whether the user already
        # owns most of this artist's popular catalogue (e.g. via compilation
        # albums). If ≥60% of the artist's top tracks (across ALL albums) are
        # already in the library, there's no point recommending any album —
        # the user already has the songs that would make them want to buy it.
        from models import ArtistEnrichment
        ae = db.query(ArtistEnrichment).filter_by(artist_name_lower=artist_name.lower()).first()
        if ae and ae.top_tracks:
            try:
                all_top = json.loads(ae.top_tracks)
                artist_lower_cov = artist_name.lower()
                owned_top = sum(
                    1 for t in all_top
                    if t.get("name") and
                    f"{artist_lower_cov}::{_norm_track(t['name'])}" in known_track_names
                )
                coverage = owned_top / len(all_top) if all_top else 0.0
                if coverage >= 0.60:
                    log.info(
                        "  PATH A: skipping '%s' — %.0f%% of top tracks already owned "
                        "(%d/%d across all albums)",
                        artist_name, coverage * 100, owned_top, len(all_top),
                    )
                    continue
            except Exception:
                pass

        # Choose the best missing album using top-songs logic
        best_album, best_year, best_image = _get_best_album_for_artist(artist_name, db, known_track_names)

        if not best_album:
            continue
        lib_key = f"{artist_name.lower()}::{_norm_album_title(best_album)}"
        if lib_key in known_albums or album_in_library(artist_name, best_album, db):
            # Top album already owned — try the next best from discography,
            # applying track-overlap check to each candidate.
            best_album = ""
            for alb in albums_data:
                alb_name = alb.get("name", "")
                if not alb_name:
                    continue
                if f"{artist_name.lower()}::{_norm_album_title(alb_name)}" in known_albums:
                    continue
                if album_in_library(artist_name, alb_name, db):
                    continue
                # Track-overlap check: skip if user already owns ≥50%
                # of this artist's top tracks (they likely have the key songs
                # already via compilations, so this album adds no new value).
                # We use the artist's global top tracks as proxy since the
                # discography cache doesn't have per-album track lists.
                from services.library_dedup import tracks_in_library_for_album
                alb_top_tracks = [
                    t.get("name") for t in (json.loads(ae.top_tracks) if ae and ae.top_tracks else [])
                    if t.get("name")
                ]
                if alb_top_tracks:
                    owned_c, total_c = tracks_in_library_for_album(artist_name, alb_top_tracks, db)
                    if total_c > 0 and owned_c / total_c >= 0.50:
                        continue
                best_album = alb_name
                best_year = alb.get("release_year")
                best_image = alb.get("image_url")
                break
            if not best_album:
                continue

        if not _genre_ok(artist_tags):
            continue

        # Get top tracks for why-text (ae already loaded above)
        top_tracks_list: list[dict] = []
        if ae and ae.top_tracks:
            try:
                top_tracks_list = json.loads(ae.top_tracks)
            except Exception:
                pass

        why = _build_why_text(
            artist_name, best_album, artist_listeners_score,
            f"You love {artist_name} but don't have this album",
            top_tracks_list,
        )

        before = len(candidates)
        _add_candidate(
            artist=artist_name,
            album=best_album,
            release_year=best_year,
            pop_score=max(0.0, artist_listeners_score - 15.0),  # familiarity penalty
            image_url=best_image,
            source_artist=artist_name,
            source_affinity=aff_score,
            lastfm_listeners=aff_score,  # use affinity as listener proxy for known artists
            why=why,
            rec_type="missing_album",
            tags=artist_tags,
        )
        if len(candidates) > before:
            _charge_genre(artist_tags)
            path_a_count += 1

    # ── PATH B: New artists via similarity ------------------------------------
    for seed_row in seed_rows:
        if path_b_total >= path_b_target:
            break

        seed_name = seed_row.artist_name
        source_affinity = affinity_map.get(seed_name, 50.0)

        # Enforce per-seed-artist diversity cap
        if seed_artist_counts.get(seed_name, 0) >= per_seed_cap:
            continue

        similar_key = f"similar:{seed_name.lower()}"
        sim_row = db.query(PopularityCache).filter_by(cache_key=similar_key).first()
        if not sim_row:
            continue
        try:
            similar_artists: list[str] = json.loads(sim_row.payload).get("artists", [])
        except Exception:
            continue

        similar_shuffled = list(similar_artists)
        random.shuffle(similar_shuffled)

        for similar in similar_shuffled:
            if seed_artist_counts.get(seed_name, 0) >= per_seed_cap:
                break
            if similar.lower() in known_artists:
                continue

            pop_key = f"artist:{similar.lower()}"
            pop_row = db.query(PopularityCache).filter_by(cache_key=pop_key).first()
            pop_score = 40.0
            lastfm_listeners_score = 40.0
            image_url = None
            tags: list[str] = []

            if pop_row:
                try:
                    pd = json.loads(pop_row.payload)
                    pop_score = float(pd.get("popularity_score", 40))
                    image_url = pd.get("image_url")
                    tags = pd.get("tags", [])
                    raw_listeners = float(pd.get("listener_count", 0))
                    if raw_listeners > 0:
                        lastfm_listeners_score = min(100.0, (math.log1p(raw_listeners) / math.log1p(10_000_000)) * 100)
                    else:
                        lastfm_listeners_score = pop_score
                except Exception:
                    pass

            if not _genre_ok(tags):
                continue

            # Hard holiday block — skip any artist with a holiday tag regardless
            # of whether a non-holiday tag passed the genre gate above
            if any(_HOLIDAY_RE.search(t.lower()) for t in tags):
                log.debug("  PATH B: skipping '%s' — has holiday tag in %s", similar, tags[:3])
                continue

            # ── Core change: pick album via top-songs logic ------------------─
            album_name, release_year, album_image = _get_best_album_for_artist(similar, db, known_track_names)
            if not album_name:
                log.debug("  PATH B: skipping '%s' — no recommendable album found", similar)
                continue
            use_image = album_image or image_url

            # Load top tracks for why-text
            from models import ArtistEnrichment
            ae = db.query(ArtistEnrichment).filter_by(artist_name_lower=similar.lower()).first()
            top_tracks_list: list[dict] = []
            if ae and ae.top_tracks:
                try:
                    top_tracks_list = json.loads(ae.top_tracks)
                except Exception:
                    pass

            genre_hint = f" ({', '.join(tags[:2])})" if tags else ""
            why = _build_why_text(
                similar, album_name, lastfm_listeners_score,
                f"Fans of {seed_name} also love this artist{genre_hint}",
                top_tracks_list,
            )

            # Moderate the listener score by source affinity so PATH B results
            # aren't scored identically to PATH D global-popular results.
            # A globally popular artist recommended via a high-affinity seed
            # still scores well; one via a weak-affinity seed scores lower.
            affinity_factor = 0.5 + (source_affinity / 200.0)  # 0.5–1.0
            moderated_listeners = lastfm_listeners_score * affinity_factor

            before = len(candidates)
            _add_candidate(
                artist=similar,
                album=album_name,
                release_year=release_year,
                pop_score=pop_score,
                image_url=use_image,
                source_artist=seed_name,
                source_affinity=source_affinity,
                lastfm_listeners=moderated_listeners,
                why=why,
                rec_type="new_artist",
                tags=tags,
            )
            if len(candidates) > before:
                seed_artist_counts[seed_name] = seed_artist_counts.get(seed_name, 0) + 1
                path_b_total += 1
                _charge_genre(tags)

    # ── PATH D: Globally popular (≥1M listeners), not in library ─────────────
    # Reduced from 25% to 20% to leave more headroom for taste-driven paths.
    path_d_target = max(1, round(limit * 0.20))
    path_d_added = 0

    try:
        from models import PopularityCache as PC
        all_artist_cache = (
            db.query(PC)
            .filter(PC.cache_key.like("artist:%"))
            .all()
        )
        scored_global = []
        for row in all_artist_cache:
            try:
                pd = json.loads(row.payload)
                raw_listeners = float(pd.get("listener_count", 0))
                if raw_listeners < 1_000_000:
                    continue
                artist_name_g = pd.get("name") or row.cache_key.replace("artist:", "")
                if not artist_name_g:
                    continue
                if artist_name_g.lower() in known_artists:
                    continue
                if artist_name_g.lower() in recently_queued_artists:
                    continue
                if artist_name_g.lower() in rejected_artists:
                    continue
                listeners_score = min(100.0, (math.log1p(raw_listeners) / math.log1p(10_000_000)) * 100)
                pop_score_g = float(pd.get("popularity_score", 40))
                scored_global.append((artist_name_g, listeners_score, pop_score_g, pd))
            except Exception:
                continue

        scored_global.sort(key=lambda x: x[1], reverse=True)

        for artist_name_g, listeners_score, pop_score_g, pd in scored_global:
            if path_d_added >= path_d_target:
                break

            tags_g = pd.get("tags", [])

            # ── Strict genre gate (v8) ------------------------------------────
            # Bypass only for genuine global phenomena (listeners_score >= 98,
            # roughly 50M+ listeners — The Beatles, Michael Jackson tier).
            # Previously 90 (~2M listeners), which let 2Pac, Ice Cube etc.
            # bypass the filter even for users who dislike rap/hip-hop.
            # Tag normalisation via _norm_genre handles "Hip-Hop" vs "hip hop".
            if tags_g and listeners_score < 98.0:
                tag_lowers_g = [_norm_genre(t) for t in tags_g[:5]]
                has_genre_overlap = any(t in user_top_genres for t in tag_lowers_g)
                if not has_genre_overlap:
                    log.debug(
                        "  PATH D skip (genre mismatch): '%s' tags=%s — not in user genres",
                        artist_name_g, tags_g[:3],
                    )
                    continue

            if not _genre_ok(tags_g):
                continue

            # Hard holiday block
            if any(_HOLIDAY_RE.search(t.lower()) for t in tags_g):
                log.debug("  PATH D: skipping '%s' — has holiday tag in %s", artist_name_g, tags_g[:3])
                continue

            image_url_g = pd.get("image_url")
            # Top-songs → album logic for globally popular artists
            album_name_g, release_year_g, album_image_g = _get_best_album_for_artist(artist_name_g, db, known_track_names)
            if not album_name_g:
                log.debug("  PATH D: skipping '%s' — no recommendable album found", artist_name_g)
                continue
            use_image_g = album_image_g or image_url_g
            genre_hint_g = f" ({', '.join(tags_g[:2])})" if tags_g else ""

            from models import ArtistEnrichment
            ae_g = db.query(ArtistEnrichment).filter_by(
                artist_name_lower=artist_name_g.lower()
            ).first()
            top_tracks_g: list[dict] = []
            if ae_g and ae_g.top_tracks:
                try:
                    top_tracks_g = json.loads(ae_g.top_tracks)
                except Exception:
                    pass

            why_g = _build_why_text(
                artist_name_g, album_name_g, listeners_score,
                f"Popular pick{genre_hint_g}: highly listened to worldwide but not yet in your library",
                top_tracks_g,
            )

            before = len(candidates)
            _add_candidate(
                artist=artist_name_g,
                album=album_name_g,
                release_year=release_year_g,
                pop_score=pop_score_g,
                image_url=use_image_g,
                source_artist="global_popular",
                source_affinity=0.0,   # genre_affinity_score computed inside _add_candidate from tags
                lastfm_listeners=listeners_score,
                why=why_g,
                rec_type="new_artist",
                tags=tags_g,
            )
            if len(candidates) > before:
                path_d_added += 1
                _charge_genre(tags_g)

    except Exception as e:
        log.warning(f"  PATH D (global popular) failed: {e}")

    log.info(f"  PATH D: {path_d_added} globally popular artists added")

    # ── PATH E: Genre-centrality discovery (PageRank-style) ------------------
    #
    # Philosophy: within the artist similarity graph we already have in
    # ArtistRelation, some artists act as "hubs" — many other artists point to
    # them via their similar-artist lists, even though they may not appear in
    # the user's own top-30 seeds.  These hub artists are structurally
    # important in a genre (analogous to a highly-linked page in Google's
    # original PageRank model).  If the user hasn't heard them, they are
    # premium discovery targets.
    #
    # Algorithm:
    #   1. Load every ArtistRelation row whose artist_a is a user-library
    #      artist OR appears in the PopularityCache (i.e. enriched globally).
    #      This gives us a directed similarity graph: A → B means "A lists B
    #      as similar".
    #   2. Run a simplified PageRank (10 iterations, damping=0.85) on the
    #      *in-degree* of each artist — how many other artists point at them.
    #      Artists the user already owns are still included in the graph (they
    #      act as "voters") but are excluded from the candidate output.
    #   3. Take the top N by PageRank score who are not in the user's library,
    #      not already queued, and pass the genre filter.
    #   4. Add them as rec_type="hub_artist" with a why string that names the
    #      genre and centrality rank.
    #
    # Target: up to 20% of the discovery limit (floor 2).
    # This path runs after PATH D so it only fills remaining headroom.

    path_e_target = max(1, round(limit * 0.20))
    path_e_added  = 0

    try:
        from models import ArtistRelation, PopularityCache as PC2
        import json as _json
        from services.holiday import HOLIDAY_RULES

        # Build a flat set of all holiday keywords for fast tag/name matching
        # _holiday_re and _is_holiday_artist are now module-level (defined below recommend_new_albums)

        # ── Step 1: build the graph ------------------------------------───────
        # We want all relations where artist_a is known (enriched or in library).
        # Use a set of "known" names from PopularityCache (artist:* keys) so the
        # graph isn't limited to just the user's listened artists.
        all_relation_rows = db.query(ArtistRelation).all()

        if all_relation_rows:
            # Build in-degree map: how many unique artists point to each artist.
            # Use a dict of sets so duplicate edges don't inflate the count.
            in_edges: dict[str, set[str]] = {}   # target → {source, ...}
            out_edges: dict[str, set[str]] = {}  # source → {target, ...}

            # Build a set of known holiday-primary artist names from the
            # cache so we can prune them from graph edges efficiently.
            holiday_artist_names: set[str] = set()
            for name_l, (_disp, _pd) in pop_cache_map.items():
                _tags = _pd.get("tags", [])
                _hcount = sum(1 for t in _tags if _HOLIDAY_RE.search(t.lower()))
                if _HOLIDAY_RE.search(name_l) or (_tags and _hcount >= len(_tags) / 2):
                    holiday_artist_names.add(name_l)
            log.info(f"  PATH E: pruning {len(holiday_artist_names)} holiday-primary artists from graph")

            for rel in all_relation_rows:
                a = rel.artist_a.lower()
                b = rel.artist_b.lower()
                # Only exclude artists whose primary identity is holiday music
                if a in holiday_artist_names or b in holiday_artist_names:
                    continue
                in_edges.setdefault(b, set()).add(a)
                out_edges.setdefault(a, set()).add(b)

            all_nodes = set(in_edges.keys()) | set(out_edges.keys())
            N = len(all_nodes)

            if N > 1:
                # ── Step 2: simplified PageRank ------------------─────────────
                DAMPING     = 0.85
                ITERATIONS  = 10
                rank: dict[str, float] = {node: 1.0 / N for node in all_nodes}

                for _ in range(ITERATIONS):
                    new_rank: dict[str, float] = {}
                    for node in all_nodes:
                        # Sum of (rank[src] / out_degree[src]) for all srcs → node
                        in_flow = 0.0
                        for src in in_edges.get(node, set()):
                            out_d = len(out_edges.get(src, set())) or 1
                            in_flow += rank[src] / out_d
                        new_rank[node] = (1.0 - DAMPING) / N + DAMPING * in_flow
                    rank = new_rank

                # Normalise to 0–100
                max_rank = max(rank.values()) or 1.0
                rank_score: dict[str, float] = {
                    node: (r / max_rank) * 100.0 for node, r in rank.items()
                }

                # ── Step 3: pick top candidates not in user's library ─────────
                # Sort by PageRank descending, filter to unknowns
                sorted_by_rank = sorted(
                    rank_score.items(), key=lambda kv: kv[1], reverse=True
                )

                log.info(
                    f"  PATH E: graph has {N} nodes, {len(all_relation_rows)} edges, "
                    f"{len(pop_cache_map)} enriched artists in cache"
                )

                for artist_lower, pr_score in sorted_by_rank:
                    if path_e_added >= path_e_target:
                        break

                    if artist_lower in known_artists:
                        continue
                    if artist_lower in recently_queued_artists:
                        continue
                    if artist_lower in rejected_artists:
                        continue

                    # How many library artists reference this one (in-degree from library)
                    lib_voters = [
                        src for src in in_edges.get(artist_lower, set())
                        if src in known_artists
                    ]
                    n_lib_voters = len(lib_voters)

                    # Lower threshold to 1 — even a single strong library pointer
                    # is meaningful if PageRank is high. Log but don't skip.
                    if n_lib_voters < 1:
                        continue

                    # Look up enrichment via the pre-built lowercase map
                    cache_entry = pop_cache_map.get(artist_lower)
                    if not cache_entry:
                        log.debug(f"  PATH E: no cache entry for '{artist_lower}' — skipping")
                        continue

                    artist_display, pd_e = cache_entry
                    tags_e: list[str] = pd_e.get("tags", [])

                    # Compute listeners_score_e from cache payload (was never assigned — v8 fix)
                    raw_listeners_e = float(pd_e.get("listener_count", 0))
                    listeners_score_e = (
                        min(100.0, (math.log1p(raw_listeners_e) / math.log1p(10_000_000)) * 100)
                        if raw_listeners_e > 0 else 0.0
                    )
                    pop_score_e = float(pd_e.get("popularity_score", 40))
                    image_url_e = pd_e.get("image_url")

                    # Skip holiday/seasonal artists
                    if _is_holiday_artist(artist_lower, tags_e):
                        log.debug(f"  PATH E: skipping holiday artist '{artist_display}'")
                        continue

                    # Hard holiday block — if ANY tag is a holiday keyword, skip.
                    # This runs before the genre gate so that an artist with tags
                    # like ['novelty', 'christmas'] cannot slip through because
                    # 'novelty' happens to overlap the user's genre profile.
                    if any(_HOLIDAY_RE.search(t.lower()) for t in tags_e):
                        log.debug(
                            "  PATH E: '%s' skipped — has holiday tag in %s",
                            artist_display, tags_e[:3],
                        )
                        continue

                    # Genre gate — same strict rule as PATH D (v8: threshold 98, _norm_genre)
                    if tags_e and listeners_score_e < 98.0:
                        tag_lowers_e = [_norm_genre(t) for t in tags_e[:5]]
                        if not any(t in user_top_genres for t in tag_lowers_e):
                            log.debug(
                                f"  PATH E: '{artist_display}' skipped — genre mismatch "
                                f"tags={tags_e[:3]} not in user genres"
                            )
                            continue

                    if not _genre_ok(tags_e):
                        continue

                    album_name_e, release_year_e, album_image_e = _get_best_album_for_artist(
                        artist_display, db, known_track_names
                    )
                    if not album_name_e:
                        log.debug("  PATH E: skipping '%s' — no recommendable album found", artist_display)
                        continue
                    use_image_e = album_image_e or image_url_e

                    # Build why string — name-drop up to 3 user-library voters,
                    # sorted by their own affinity score so the most-loved names appear first
                    sample_voters = sorted(
                        lib_voters, key=lambda s: rank_score.get(s, 0), reverse=True
                    )[:3]
                    voter_names = [v.title() for v in sample_voters]
                    voter_str = (
                        f"{voter_names[0]}" if len(voter_names) == 1
                        else f"{', '.join(voter_names[:-1])} and {voter_names[-1]}"
                    )
                    genre_hint_e = f" ({', '.join(tags_e[:2])})" if tags_e else ""
                    why_e = (
                        f"Genre cornerstone{genre_hint_e}: {voter_str} "
                        f"{'all ' if n_lib_voters > 1 else ''}"
                        f"consider{'s' if n_lib_voters == 1 else ''} them essential — "
                        f"{n_lib_voters} artist{'s' if n_lib_voters != 1 else ''} "
                        f"in your library point here. "
                        f"Popularity: {listeners_score_e:.0f}/100."
                    )

                    # Blend: 40% listener reach, 25% pop score, 25% PageRank, 10% genre affinity
                    genre_aff_e = _genre_affinity_score(tags_e)
                    pr_normalised = min(100.0, pr_score)   # already 0–100
                    blended_e = (
                        listeners_score_e * 0.40 +
                        pop_score_e       * 0.25 +
                        pr_normalised     * 0.25 +
                        genre_aff_e       * 0.10
                    )

                    # Voter bonus: extra lift for being referenced by many library artists
                    voter_bonus = min(15.0, n_lib_voters * 2.5)
                    blended_e = min(100.0, blended_e + voter_bonus)

                    # Skip if this hub artist was already queued for another
                    # user this run (prevents identical recs across all users)
                    if path_e_global_seen is not None and artist_lower in path_e_global_seen:
                        continue

                    before = len(candidates)
                    _add_candidate(
                        artist=artist_display,
                        album=album_name_e,
                        release_year=release_year_e,
                        pop_score=pop_score_e,
                        image_url=use_image_e,
                        source_artist="genre_centrality",
                        source_affinity=genre_aff_e,
                        lastfm_listeners=listeners_score_e,
                        why=why_e,
                        rec_type="hub_artist",
                        tags=tags_e,
                    )
                    # Override the blended score set by _add_candidate so our
                    # PageRank-boosted value is used instead
                    if len(candidates) > before:
                        candidates[-1].popularity_score = round(blended_e, 1)
                        path_e_added += 1
                        _charge_genre(tags_e)
                        if path_e_global_seen is not None:
                            path_e_global_seen.add(artist_lower)

        log.info(f"  PATH E: {path_e_added} genre-hub artists added (PageRank)")

    except Exception as e:
        log.warning(f"  PATH E (genre centrality) failed: {e}", exc_info=True)

    # ── Final assembly with slot reservation ------------------───────────────
    # Apply jitter first (same as before), then assemble with reserved slots
    # so PATH A (taste-known artists) and PATH B (similarity chains) are
    # always represented before PATH D/E fill remaining headroom.
    # Without this, PATH D's high raw listener scores crowd out every
    # affinity-driven recommendation in the top-N.
    for c in candidates:
        jitter_range = 3.0 if c.popularity_score >= 70 else 8.0
        c.popularity_score = round(
            max(0.0, min(100.0, c.popularity_score + random.uniform(-jitter_range, jitter_range))),
            1
        )

    # Split by path type
    path_a_results  = [c for c in candidates if c.rec_type == "missing_album"]
    path_b_results  = [c for c in candidates if c.rec_type == "new_artist" and c.source_artist != "global_popular"]
    path_d_results  = [c for c in candidates if c.rec_type == "new_artist" and c.source_artist == "global_popular"]
    path_e_results  = [c for c in candidates if c.rec_type == "hub_artist"]

    # Sort each bucket by score descending
    for bucket in (path_a_results, path_b_results, path_d_results, path_e_results):
        bucket.sort(key=lambda c: c.popularity_score, reverse=True)

    # Reserved slot counts (guaranteed minimums, not hard caps)
    reserved_a = min(len(path_a_results), max(1, round(limit * 0.20)))
    reserved_b = min(len(path_b_results), max(1, round(limit * 0.35)))
    reserved_e = min(len(path_e_results), max(0, round(limit * 0.15)))

    reserved: list = (
        path_a_results[:reserved_a] +
        path_b_results[:reserved_b] +
        path_e_results[:reserved_e]
    )
    reserved_set = {id(c) for c in reserved}

    # Remaining candidates sorted by score fill up to limit
    remainder = sorted(
        [c for c in candidates if id(c) not in reserved_set],
        key=lambda c: c.popularity_score,
        reverse=True,
    )

    final = reserved + remainder

    log.info(
        f"  Discovery: {len(candidates)} candidates total → "
        f"reserved A={reserved_a} B={reserved_b} E={reserved_e}, "
        f"remainder={len(remainder)} "
        f"(seed cap={per_seed_cap}/artist, genre caps: liked={GENRE_CAP_LIKED} neutral={GENRE_CAP_NEUTRAL})"
    )
    return final[:limit]


def get_weight_presets() -> dict:
    """Expose presets to the API for the UI dropdowns in Module 7."""
    return {name: dict(w) for name, w in WEIGHT_PRESETS.items()}
