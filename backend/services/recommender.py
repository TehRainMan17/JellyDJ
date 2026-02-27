
"""
JellyDJ Recommendation Engine — Module 5

Two public functions:
  recommend_library_tracks(user_id, playlist_type, limit, db) -> list[TrackResult]
  recommend_new_albums(user_id, limit, db)                    -> list[AlbumResult]

Scoring philosophy
──────────────────
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import httpx
import numpy as np
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Result dataclasses ────────────────────────────────────────────────────────

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
    rec_type: str = ""            # "missing_album" | "new_artist"


# ── Weight presets ────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    Pull cached popularity score for an artist (0–100, normalised to 0–1).
    Doesn't trigger live API calls — uses only what's already cached.
    """
    from models import PopularityCache
    import json
    key = f"artist:{artist.lower()}"
    row = db.query(PopularityCache).filter_by(cache_key=key).first()
    if not row:
        return 0.5   # neutral fallback when no cache data
    try:
        data = json.loads(row.payload)
        return min(1.0, float(data.get("popularity_score", 50)) / 100.0)
    except Exception:
        return 0.5


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


# ── Main functions ────────────────────────────────────────────────────────────

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

    now = datetime.utcnow()
    results: list[TrackResult] = []

    for t in tracks:
        # ── Affinity component ────────────────────────────────────────────
        a_score = artist_aff.get(t.artist_name.lower(), 0.0)
        g_score = genre_aff.get(t.genre.lower(), 0.0)
        affinity = max(a_score, g_score * 0.7)

        # ── Popularity component ──────────────────────────────────────────
        popularity = _get_popularity(t.artist_name, db)

        # ── Recency inverse ───────────────────────────────────────────────
        recency_inv = _recency_score(t.last_played)

        # ── Novelty ───────────────────────────────────────────────────────
        # Gradual decay rather than binary — a track played once or twice
        # still gets meaningful novelty credit. Fully decays at 10+ plays.
        # This prevents played-once tracks from immediately competing on
        # pure affinity (which re-anchors to familiar artists).
        novelty = max(0.0, 1.0 - (t.play_count or 0) / 10.0)

        # ── Weighted sum ──────────────────────────────────────────────────
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

    # ── Tiered randomness ─────────────────────────────────────────────────────
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

    # ── Per-artist cap for discover playlist ─────────────────────────────────
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


def _get_top_album_from_cache(artist_name: str, db) -> tuple[str, Optional[int], Optional[str]]:
    """
    Look up the most popular album for an artist from the popularity cache.
    Returns (album_name, release_year, image_url).
    Falls back to empty strings if not cached.
    """
    from models import PopularityCache
    import json
    # Check for a cached top-album key first
    key = f"top_album:{artist_name.lower()}"
    row = db.query(PopularityCache).filter_by(cache_key=key).first()
    if row:
        try:
            d = json.loads(row.payload)
            # Last.fm adapter stores the album name under "name", not "album"
            return d.get("name") or d.get("album", ""), d.get("year"), d.get("image_url")
        except Exception:
            pass
    return "", None, None


def recommend_new_albums(
    user_id: str,
    limit: int,
    db,
) -> list[AlbumResult]:
    """
    Album-first recommendation engine with smart variety.

    Two recommendation paths:

    PATH A — Missing albums from known artists (complete their collection)
    PATH B — New artists via similarity (expand their world)

    Variety mechanisms:
    - Seed artists are sampled from top 30 (weighted by affinity) rather than
      always using the same top 5 — different artists seed each run
    - Recent repeat suppression: artists queued in last 45 days are skipped
    - Wildcard genre injection: ~20% of PATH B recs come from genres
      adjacent to the user's favorites (one genre step out)
    - "New but popular" boost: unknown artists with high listener counts
      get a bonus — surfaces rising/popular artists you haven't found yet
    - Never-heard-artist preference: artists with zero library presence
      are weighted higher than artists you partly know
    """
    import random
    import json
    import math
    from models import UserTasteProfile, Play, PopularityCache, DiscoveryQueueItem

    # ── Load taste profile — use top 30 as the candidate seed pool ───────────
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

    # Normalise affinity scores to 0–100
    max_affinity = max(float(r.affinity_score) for r in top_artists_rows) or 1.0
    affinity_map = {
        r.artist_name: min(100.0, float(r.affinity_score) / max_affinity * 100)
        for r in top_artists_rows
    }

    # ── Structured seed selection — break top-artist dominance ───────────────
    # Pure affinity-weighted sampling always picks Adele/Cher as seeds, which
    # means PATH B candidates are always "similar to Adele/Cher". Instead,
    # divide seeds into three tiers with guaranteed minimum slots per tier.
    #
    #   Tier 1 (rank 1-5):   max 4 seeds  — your absolute favorites, always present
    #   Tier 2 (rank 6-15):  min 6 seeds  — mid-range artists, generate variety
    #   Tier 3 (rank 16-30): min 4 seeds  — long-tail artists, unexpected finds
    #
    tier1 = top_artists_rows[:5]
    tier2 = top_artists_rows[5:15]
    tier3 = top_artists_rows[15:30]

    def _weighted_sample(rows, n):
        if not rows: return []
        n = min(n, len(rows))
        weights = [float(r.affinity_score) ** 0.5 for r in rows]
        total = sum(weights) or 1.0
        probs = [w / total for w in weights]
        indices = list(np.random.choice(len(rows), size=n, replace=False, p=probs))
        return [rows[i] for i in indices]

    seed_rows = (
        _weighted_sample(tier1, min(4, len(tier1))) +
        _weighted_sample(tier2, min(6, len(tier2))) +
        _weighted_sample(tier3, min(4, len(tier3)))
    )
    # Deduplicate (weighted_sample per tier, no overlap possible, but guard anyway)
    seen_seeds = set()
    seed_rows = [r for r in seed_rows if not (r.artist_name in seen_seeds or seen_seeds.add(r.artist_name))]

    log.info(f"  Discovery seeds ({len(seed_rows)}): {[r.artist_name for r in seed_rows[:6]]}...")

    # ── Build known library sets ─────────────────────────────────────────────
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
            known_albums.add(f"{row.artist_name.lower()}::{row.album_name.lower()}")
        if row.album_artist and row.album_name:
            known_albums.add(f"{row.album_artist.lower()}::{row.album_name.lower()}")

    for row in db.query(Play.artist_name, Play.album_name).filter_by(user_id=user_id).distinct():
        if row.artist_name:
            known_artists.add(row.artist_name.lower())
        if row.artist_name and row.album_name:
            known_albums.add(f"{row.artist_name.lower()}::{row.album_name.lower()}")

    log.info(f"  Dedup sets: {len(known_artists)} artists, {len(known_albums)} albums in library")

    known_track_names: set[str] = set()
    for row in db.query(LibraryTrack.track_name, LibraryTrack.artist_name).filter(
        LibraryTrack.missing_since.is_(None)
    ).all():
        if row.track_name and row.artist_name:
            known_track_names.add(f"{row.artist_name.lower()}::{row.track_name.lower()}")

    # ── Already-queued suppression ────────────────────────────────────────────
    # Albums: deduplicated for all time (never re-recommend the same album)
    # Artists: suppressed for 90 days after any queue appearance
    # Rejected artists: suppressed for 180 days — user explicitly said no
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

    # ── Collect user's top genres for wildcard adjacency ─────────────────────
    from models import GenreProfile
    top_genre_rows = (
        db.query(GenreProfile)
        .filter_by(user_id=user_id)
        .order_by(GenreProfile.affinity_score.desc())
        .limit(8)
        .all()
    )
    user_top_genres = {r.genre.lower() for r in top_genre_rows if r.genre}

    seen: set[str] = set()
    candidates: list[AlbumResult] = []

    def _add_candidate(
        artist: str, album: str, release_year, pop_score: float,
        image_url, source_artist: str, source_affinity: float,
        why: str, rec_type: str,
        lastfm_listeners: float = 0.0,
        is_wildcard: bool = False,
    ):
        dedup = f"{artist.lower()}::{album.lower()}"
        if dedup in seen or dedup in queued:
            return

        # Suppress artists queued recently (90-day window) or rejected (180-day)
        if artist.lower() in rejected_artists:
            log.debug(f"  Suppressing '{artist}' — rejected within last 180 days")
            return
        if artist.lower() in recently_queued_artists:
            log.debug(f"  Suppressing '{artist}' — queued within last 90 days")
            return

        if album:
            exact_key = f"{artist.lower()}::{album.lower()}"
            if exact_key in known_albums:
                log.debug(f"  Dedup (exact): '{album}' by '{artist}'")
                return
            if album_in_library(artist, album, db):
                log.info(f"  Dedup (fuzzy album): '{album}' by '{artist}' — already in library")
                return
            track_key = f"{artist.lower()}::{album.lower()}"
            if track_key in known_track_names:
                log.info(f"  Dedup (single/EP): '{album}' by '{artist}' — title matches owned track")
                return

        seen.add(dedup)

        # ── Scoring ───────────────────────────────────────────────────────────
        # For discovery, POPULARITY leads — we want to surface music the world
        # loves, not just music adjacent to what you already love.
        #
        #   50% lastfm_listeners  — global reach / how many people actually listen
        #   30% pop_score         — album/artist-level popularity signal
        #   20% source_affinity   — tiebreaker: prefer recs from artists you love
        #
        # Previously this was 40/25/35 (affinity-led), which caused familiar
        # artist similarities to always outrank genuinely popular new artists.
        blended = (
            (lastfm_listeners * 0.60) +  # global reach dominates discovery
            (pop_score        * 0.25) +  # album/artist-level signal
            (source_affinity  * 0.15)    # tiebreaker only
        )

        # "New but popular" boost: artist not in library at all + high listeners.
        # Boosted by 15pts (was 8) so genuinely popular unknowns can compete with
        # mid-tier similarity results.
        never_heard = artist.lower() not in known_artists
        if never_heard and lastfm_listeners >= 60:
            blended = min(100.0, blended + 20.0)

        # Wildcard bonus: pure popularity ranking, no affinity component
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
            source_affinity=round(source_affinity, 1),
            lastfm_listeners=round(lastfm_listeners, 1),
            rec_type=rec_type,
        ))

    # ── PATH A: Missing albums from known artists ─────────────────────────────
    # Capped at 25% of the total limit — this keeps "complete your collection"
    # suggestions from drowning out genuine new-artist discovery.
    # PATH A recs get a familiarity penalty so they score BELOW new artists
    # of comparable popularity. The user already knows these artists; the point
    # of the discovery queue is to find artists they DON'T know.
    path_a_cap = max(2, limit // 4)
    path_a_count = 0
    for artist_name, aff_score in affinity_map.items():
        if path_a_count >= path_a_cap:
            break
        # Look for cached discography  
        disco_key = f"discography:{artist_name.lower()}"
        disco_row = db.query(PopularityCache).filter_by(cache_key=disco_key).first()
        if not disco_row:
            continue
        try:
            albums_data = json.loads(disco_row.payload).get("albums", [])
        except Exception:
            continue

        for alb in albums_data:
            alb_name = alb.get("name", "")
            if not alb_name:
                continue
            lib_key = f"{artist_name.lower()}::{alb_name.lower()}"
            if lib_key in known_albums:
                log.debug(f"  Path A skip (exact): {artist_name} / {alb_name}")
                continue   # already have it
            # Fuzzy check — catches "Greatest Hits 2003" vs "Greatest Hits: Platinum Collection"
            if album_in_library(artist_name, alb_name, db):
                log.debug(f"  Path A skip (fuzzy): {artist_name} / {alb_name}")
                continue

            pop_score = float(alb.get("popularity_score", 40))
            # For known artists: use their own affinity as the listener proxy
            # (we already know user loves them, so fame matters less here)
            before_len = len(candidates)
            # Familiarity penalty: known artists score 15pts lower than new ones
            # so genuine discovery always wins ties with "get more Adele" recs
            _add_candidate(
                artist=artist_name,
                album=alb_name,
                release_year=alb.get("release_year"),
                pop_score=max(0.0, pop_score - 15.0),
                image_url=alb.get("image_url"),
                source_artist=artist_name,
                source_affinity=aff_score,
                lastfm_listeners=aff_score,
                why=(
                    f"You listen to {artist_name} a lot but don't have "
                    f"'{alb_name}' (popularity {pop_score:.0f}/100)."
                ),
                rec_type="missing_album",
            )
            if len(candidates) > before_len:
                path_a_count += 1

    # ── PATH B: New artists via similarity (from sampled seed artists) ─────────
    for seed_row in seed_rows:
        seed_name = seed_row.artist_name
        source_affinity = affinity_map.get(seed_name, 50.0)

        similar_key = f"similar:{seed_name.lower()}"
        sim_row = db.query(PopularityCache).filter_by(cache_key=similar_key).first()
        if not sim_row:
            continue
        try:
            similar_artists: list[str] = json.loads(sim_row.payload).get("artists", [])
        except Exception:
            continue

        # Shuffle similar artists so we don't always recommend the same #1 similar
        similar_shuffled = list(similar_artists)
        random.shuffle(similar_shuffled)

        for similar in similar_shuffled:
            if similar.lower() in known_artists:
                continue

            pop_key = f"artist:{similar.lower()}"
            pop_row = db.query(PopularityCache).filter_by(cache_key=pop_key).first()
            pop_score = 40.0
            lastfm_listeners_score = 40.0
            image_url = None
            tags = []

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

            album_name, release_year, album_image = _get_top_album_from_cache(similar, db)
            use_image = album_image or image_url

            genre_hint = f" ({', '.join(tags[:2])})" if tags else ""
            why = (
                f"Similar to {seed_name}{genre_hint}, an artist you love. "
                f"{'Album: ' + album_name + '. ' if album_name else ''}"
                f"Popularity: {pop_score:.0f}/100."
            )

            _add_candidate(
                artist=similar,
                album=album_name,
                release_year=release_year,
                pop_score=pop_score,
                image_url=use_image,
                source_artist=seed_name,
                source_affinity=source_affinity,
                lastfm_listeners=lastfm_listeners_score,
                why=why,
                rec_type="new_artist",
            )

    # ── PATH D: Globally popular — no seed required ───────────────────────────
    # Pull the most-listened-to artists from the full popularity cache regardless
    # of similarity to any seed. This is the "what's huge right now that I haven't
    # heard" path. It breaks the similarity-chain feedback loop entirely.
    # Target: 25% of the final output comes from this path.
    path_d_target = max(4, int(limit * 0.40))
    path_d_added = 0
    # PATH D: pull genuinely popular artists not in library
    # 1M listener floor keeps this tier high-quality

    try:
        from models import PopularityCache as PC
        # Fetch all artist cache entries, sort by listener_count descending
        all_artist_cache = (
            db.query(PC)
            .filter(PC.cache_key.like("artist:%"))
            .all()
        )
        # Parse and sort by raw listener count
        scored_global = []
        for row in all_artist_cache:
            try:
                pd = json.loads(row.payload)
                raw_listeners = float(pd.get("listener_count", 0))
                if raw_listeners < 1_000_000:  # floor: only artists with genuine global reach
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

            tags = pd.get("tags", [])
            image_url_g = pd.get("image_url")
            album_name_g, release_year_g, album_image_g = _get_top_album_from_cache(artist_name_g, db)
            use_image_g = album_image_g or image_url_g
            genre_hint_g = f" ({', '.join(tags[:2])})" if tags else ""

            why_g = (
                f"Globally popular{genre_hint_g}: {listeners_score:.0f}/100 listener score. "
                f"{'Album: ' + album_name_g + '. ' if album_name_g else ''}"
                f"Not yet in your library."
            )

            before = len(candidates)
            _add_candidate(
                artist=artist_name_g,
                album=album_name_g,
                release_year=release_year_g,
                pop_score=pop_score_g,
                image_url=use_image_g,
                source_artist="global_popular",
                source_affinity=0.0,    # no affinity component — pure popularity
                lastfm_listeners=listeners_score,
                why=why_g,
                rec_type="new_artist",
            )
            if len(candidates) > before:
                path_d_added += 1

    except Exception as e:
        log.warning(f"  PATH D (global popular) failed: {e}")

    log.info(f"  PATH D: {path_d_added} globally popular artists added")
    # Find artists that share tags with your top genres but aren't similar to
    # any of your known artists. This is the "one genre step out" expansion.
    # We scan the popularity cache for artists tagged with adjacent genres.
    wildcard_target = max(3, limit // 5)
    wildcard_added = 0

    if user_top_genres:
        # Sample from ALL cached artist entries looking for genre-adjacent tags
        from models import PopularityCache as PC
        # Fetch a sample of artist cache entries to scan for genre adjacency
        artist_cache_rows = (
            db.query(PC)
            .filter(PC.cache_key.like("artist:%"))
            .limit(500)
            .all()
        )
        # Shuffle so we don't always look at the same 500
        random.shuffle(artist_cache_rows)

        for cache_row in artist_cache_rows:
            if wildcard_added >= wildcard_target:
                break
            try:
                pd = json.loads(cache_row.payload)
                tags = [t.lower() for t in pd.get("tags", [])]
                if not tags:
                    continue

                # Adjacent: shares at least one tag with user's genres
                # but that tag is NOT the user's #1 genre (truly adjacent, not core)
                shared = set(tags) & user_top_genres
                if not shared:
                    continue

                artist_name = pd.get("name") or cache_row.cache_key.replace("artist:", "")
                if not artist_name or artist_name.lower() in known_artists:
                    continue
                if artist_name.lower() in recently_queued_artists:
                    continue

                pop_score = float(pd.get("popularity_score", 40))
                raw_listeners = float(pd.get("listener_count", 0))
                if raw_listeners > 0:
                    listeners_score = min(100.0, (math.log1p(raw_listeners) / math.log1p(10_000_000)) * 100)
                else:
                    listeners_score = pop_score

                # Only surface reasonably popular wildcard artists
                if listeners_score < 35:
                    continue

                album_name, release_year, album_image = _get_top_album_from_cache(artist_name, db)
                image_url = album_image or pd.get("image_url")
                genre_label = ", ".join(list(shared)[:2])

                why = (
                    f"Genre discovery: {artist_name} matches your taste in {genre_label}. "
                    f"{'Album: ' + album_name + '. ' if album_name else ''}"
                    f"Popularity: {pop_score:.0f}/100."
                )

                before = len(candidates)
                _add_candidate(
                    artist=artist_name,
                    album=album_name,
                    release_year=release_year,
                    pop_score=pop_score,
                    image_url=image_url,
                    source_artist=f"genre:{genre_label}",
                    source_affinity=50.0,   # neutral — not from a known artist
                    lastfm_listeners=listeners_score,
                    why=why,
                    rec_type="new_artist",
                    is_wildcard=True,
                )
                if len(candidates) > before:
                    wildcard_added += 1

            except Exception:
                continue

    log.info(f"  Discovery: {len(candidates)} candidates ({wildcard_added} wildcards)")

    # ── Final sort with light randomness ─────────────────────────────────────
    # Jitter scores slightly so the exact same ranking doesn't repeat every run.
    # Top candidates (score >= 70) stay near the top; lower ones shuffle more.
    for c in candidates:
        jitter_range = 3.0 if c.popularity_score >= 70 else 8.0
        c.popularity_score = round(
            max(0.0, min(100.0, c.popularity_score + random.uniform(-jitter_range, jitter_range))),
            1
        )

    candidates.sort(key=lambda a: a.popularity_score, reverse=True)
    return candidates[:limit]


def get_weight_presets() -> dict:
    """Expose presets to the API for the UI dropdowns in Module 7."""
    return {name: dict(w) for name, w in WEIGHT_PRESETS.items()}
