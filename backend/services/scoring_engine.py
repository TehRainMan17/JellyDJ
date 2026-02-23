
"""
JellyDJ Scoring Engine — Module 8a

Replaces the old UserTasteProfile-based scoring with a three-layer system:

Layer 1 — ArtistProfile (per user, per artist)
  Aggregates all play/skip data for an artist into a single affinity score.

Layer 2 — GenreProfile (per user, per genre)
  Same but at genre level. Drives scoring for unplayed tracks in liked genres.

Layer 3 — TrackScore (per user, per track in full library)
  Pre-computed final score for every track. Playlist generation queries this
  table directly — no on-the-fly scoring.

Scoring philosophy:
  - Played tracks: play frequency + recency + skip penalty + artist/genre pull
  - Unplayed tracks: neutral base + artist/genre affinity (capped below played max)
  - Skip-heavy artists get suppressed even on unplayed tracks
  - Favorites get a meaningful but not dominant bonus
  - No echo chamber: unplayed can score up to ~75, played loved tracks score 85-100
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    LibraryTrack, Play, SkipPenalty,
    ArtistProfile, GenreProfile, TrackScore,
)

log = logging.getLogger(__name__)

# ── Scoring constants ─────────────────────────────────────────────────────────
#
# These weights and thresholds were tuned empirically. If you want to adjust
# the "feel" of recommendations, these are the primary levers:
#
#   W_PLAY      — raise to make heavily-played tracks dominate
#   W_RECENCY   — raise to favour recently-heard music over old favourites
#   UNPLAYED_CAP — raise to let discovery tracks compete more with played ones
#   FAVORITE_FLOOR — raise to push liked songs even higher in all playlists

# Played track formula weights (must sum to 1.0)
# These combine into a 0–100 base score before skip penalty is applied.
W_PLAY      = 0.45   # raw play frequency (log-scaled to reduce dominance of single obsessive plays)
W_RECENCY   = 0.25   # how recently played (decays linearly from 30 → 365 days)
W_ARTIST    = 0.20   # artist-level affinity pulls tracks from loved artists up
W_GENRE     = 0.10   # genre affinity as a softer signal

# Unplayed track scoring
# Unplayed tracks use a separate formula so they can appear in playlists
# without crowding out tracks the user genuinely loves.
# The score range (~38–72) sits deliberately below the played-track mean (~60–85)
# so "for you" playlists stay anchored to known favourites while still
# introducing new music in the minority discovery slots.
UNPLAYED_BASE         = 38.0   # floor for any unplayed track from any artist
UNPLAYED_ARTIST_W     = 0.20   # max +20pts from artist affinity (beloved artist = 58 base)
UNPLAYED_GENRE_W      = 0.14   # max +14pts from genre affinity
UNPLAYED_NOVELTY      = 2.0    # small constant bump to prefer genuinely new tracks over stale ones
UNPLAYED_CAP          = 72.0   # hard ceiling — keeps unplayed below a well-loved played track

# Recency decay parameters
# A track played within GRACE days gets maximum recency score (100).
# Score decays linearly to 0 at DECAY days. Tracks older than a year
# contribute nothing from recency but still score via play count + affinity.
RECENCY_GRACE_DAYS    = 30
RECENCY_DECAY_DAYS    = 365

# Minimum skip events before the penalty is applied.
# Set to 1 so that any recorded skip contributes, but the penalty itself
# scales gradually (calc in _calc_penalty in webhooks.py) so a single
# accidental skip doesn't crater a track's score.
SKIP_MIN_EVENTS       = 1

# Favorite / heart / like signal
# This is the strongest explicit signal a user can give. Design decisions:
#
#   FAVORITE_FLOOR:
#     A hearted track scores at LEAST 82/100 regardless of play count, recency,
#     or skip history. This ensures liked songs always appear near the top of
#     every playlist type. 82 was chosen to sit above the UNPLAYED_CAP (72)
#     and just above the typical ceiling for a heavily-played non-favourite (~81).
#
#   FAVORITE_BONUS:
#     Additive bonus applied before the floor check. For a frequently-played
#     and recently-heard favourite this pushes the score into the 95–100 range.
#
#   FAVORITE_SKIP_SHIELD:
#     Caps the effective skip penalty for a liked track at 0.25 (25%).
#     Rationale: if you hearted a song you probably still like it even if you
#     sometimes skip it (wrong mood, phone in pocket, etc.). Without the shield,
#     a 0.8 skip rate would multiply the score by 0.2 and drop it to ~16/100
#     even on a hearted track. With the shield, the penalty is capped at 0.25.
#
#   FAVORITE_ARTIST_BOOST:
#     When any track by an artist is favourited, the artist's overall affinity
#     score gets +20. This elevates the artist's OTHER tracks in discovery and
#     playlists — the "I love this artist" signal matters beyond the single track.
FAVORITE_FLOOR        = 82.0
FAVORITE_BONUS        = 18.0
FAVORITE_SKIP_SHIELD  = 0.25
FAVORITE_ARTIST_BOOST = 20.0


# ── Helper functions ──────────────────────────────────────────────────────────

def _play_score(play_count: int, max_plays: int) -> float:
    """Log-scaled play count normalised to 0–100."""
    if play_count <= 0 or max_plays <= 0:
        return 0.0
    return min(100.0, (math.log1p(play_count) / math.log1p(max_plays)) * 100)


def _recency_score(last_played: Optional[datetime]) -> float:
    """1.0 (100) within grace period, linear decay to 0 at RECENCY_DECAY_DAYS."""
    if not last_played:
        return 0.0
    days = (datetime.utcnow() - last_played).days
    if days <= RECENCY_GRACE_DAYS:
        return 100.0
    if days >= RECENCY_DECAY_DAYS:
        return 0.0
    decay_range = RECENCY_DECAY_DAYS - RECENCY_GRACE_DAYS
    return round(100.0 * (1.0 - (days - RECENCY_GRACE_DAYS) / decay_range), 2)


def _skip_multiplier(penalty: float) -> float:
    """Convert 0–1 skip penalty into a score multiplier (1.0 = no penalty)."""
    return max(0.1, 1.0 - float(penalty))


# ── Phase 1: Build ArtistProfile ─────────────────────────────────────────────

def rebuild_artist_profiles(db: Session, user_id: str) -> dict[str, float]:
    """
    Build ArtistProfile for every artist the user has played.
    Returns dict of artist_name → affinity_score for use in TrackScore phase.
    """
    now = datetime.utcnow()
    plays = db.query(Play).filter_by(user_id=user_id).all()
    if not plays:
        return {}

    max_plays = max((p.play_count for p in plays if p.play_count), default=1) or 1

    # Aggregate play signals by artist
    artist_agg: dict[str, dict] = {}
    for p in plays:
        key = p.artist_name
        if not key:
            continue
        if key not in artist_agg:
            artist_agg[key] = {
                "total_plays": 0,
                "tracks_played": 0,
                "play_scores": [],
                "recency_scores": [],
                "has_favorite": False,
                "genres": {},
            }
        agg = artist_agg[key]
        agg["total_plays"] += p.play_count
        agg["tracks_played"] += 1
        if p.play_count > 0:
            agg["play_scores"].append(_play_score(p.play_count, max_plays))
            agg["recency_scores"].append(_recency_score(p.last_played))
        if p.is_favorite:
            agg["has_favorite"] = True
        if p.genre:
            agg["genres"][p.genre] = agg["genres"].get(p.genre, 0) + p.play_count

    # Pull skip data per artist
    skip_rows = db.query(SkipPenalty).filter_by(user_id=user_id).all()
    artist_skips: dict[str, dict] = {}
    for sk in skip_rows:
        a = sk.artist_name
        if not a:
            continue
        if a not in artist_skips:
            artist_skips[a] = {"total_events": 0, "skip_count": 0}
        artist_skips[a]["total_events"] += sk.total_events
        artist_skips[a]["skip_count"] += sk.skip_count

    # Delete and rebuild artist profiles for this user
    db.query(ArtistProfile).filter_by(user_id=user_id).delete()

    affinity_map: dict[str, float] = {}

    for artist, agg in artist_agg.items():
        ps = agg["play_scores"]
        rs = agg["recency_scores"]
        avg_play = sum(ps) / len(ps) if ps else 0.0
        avg_recency = sum(rs) / len(rs) if rs else 0.0
        fav_boost = FAVORITE_ARTIST_BOOST if agg["has_favorite"] else 0.0
        raw_score = W_PLAY * avg_play + W_RECENCY * avg_recency
        affinity = round(min(100.0, raw_score + fav_boost), 2)

        # Skip penalty at artist level — but if the user has favorited a track
        # by this artist, dampen the skip suppression (one bad skip ≠ dislike)
        sk_data = artist_skips.get(artist, {})
        total_ev = sk_data.get("total_events", 0)
        skip_ct = sk_data.get("skip_count", 0)
        skip_rate = round(skip_ct / total_ev, 4) if total_ev >= SKIP_MIN_EVENTS else 0.0
        if skip_rate > 0:
            # Favorites shield: cap effective skip rate at 50% of normal for fav artists
            effective_skip_rate = skip_rate * (0.5 if agg["has_favorite"] else 1.0)
            affinity = round(affinity * (1.0 - effective_skip_rate * 0.5), 2)

        primary_genre = max(agg["genres"].items(), key=lambda x: x[1])[0] if agg["genres"] else ""
        affinity_map[artist] = affinity

        db.add(ArtistProfile(
            user_id=user_id,
            artist_name=artist,
            total_plays=agg["total_plays"],
            total_tracks_played=agg["tracks_played"],
            total_skips=skip_ct,
            skip_rate=str(skip_rate),
            has_favorite=agg["has_favorite"],
            primary_genre=primary_genre,
            affinity_score=str(affinity),
            updated_at=now,
        ))

    db.flush()
    return affinity_map


# ── Phase 2: Build GenreProfile ───────────────────────────────────────────────

def rebuild_genre_profiles(db: Session, user_id: str) -> dict[str, float]:
    """
    Build GenreProfile for every genre the user has played.
    Returns dict of genre → affinity_score.
    """
    now = datetime.utcnow()
    plays = db.query(Play).filter_by(user_id=user_id).all()
    if not plays:
        return {}

    max_plays = max((p.play_count for p in plays if p.play_count), default=1) or 1

    genre_agg: dict[str, dict] = {}
    for p in plays:
        key = p.genre
        if not key:
            continue
        if key not in genre_agg:
            genre_agg[key] = {
                "total_plays": 0,
                "tracks_played": 0,
                "play_scores": [],
                "recency_scores": [],
                "has_favorite": False,
            }
        agg = genre_agg[key]
        agg["total_plays"] += p.play_count
        agg["tracks_played"] += 1
        if p.play_count > 0:
            agg["play_scores"].append(_play_score(p.play_count, max_plays))
            agg["recency_scores"].append(_recency_score(p.last_played))
        if p.is_favorite:
            agg["has_favorite"] = True

    # Skip data per genre
    skip_rows = db.query(SkipPenalty).filter_by(user_id=user_id).all()
    genre_skips: dict[str, dict] = {}
    for sk in skip_rows:
        g = sk.genre
        if not g:
            continue
        if g not in genre_skips:
            genre_skips[g] = {"total_events": 0, "skip_count": 0}
        genre_skips[g]["total_events"] += sk.total_events
        genre_skips[g]["skip_count"] += sk.skip_count

    db.query(GenreProfile).filter_by(user_id=user_id).delete()

    affinity_map: dict[str, float] = {}

    for genre, agg in genre_agg.items():
        ps = agg["play_scores"]
        rs = agg["recency_scores"]
        avg_play = sum(ps) / len(ps) if ps else 0.0
        avg_recency = sum(rs) / len(rs) if rs else 0.0
        fav_boost = FAVORITE_ARTIST_BOOST if agg["has_favorite"] else 0.0
        raw_score = W_PLAY * avg_play + W_RECENCY * avg_recency + fav_boost
        affinity = round(min(100.0, raw_score), 2)

        sk_data = genre_skips.get(genre, {})
        total_ev = sk_data.get("total_events", 0)
        skip_ct = sk_data.get("skip_count", 0)
        skip_rate = round(skip_ct / total_ev, 4) if total_ev >= SKIP_MIN_EVENTS else 0.0
        if skip_rate > 0:
            affinity = round(affinity * (1.0 - skip_rate * 0.4), 2)

        affinity_map[genre] = affinity

        db.add(GenreProfile(
            user_id=user_id,
            genre=genre,
            total_plays=agg["total_plays"],
            total_tracks_played=agg["tracks_played"],
            total_skips=skip_ct,
            skip_rate=str(skip_rate),
            has_favorite=agg["has_favorite"],
            affinity_score=str(affinity),
            updated_at=now,
        ))

    db.flush()
    return affinity_map


# ── Phase 3: Build TrackScore ─────────────────────────────────────────────────

def rebuild_track_scores(
    db: Session,
    user_id: str,
    artist_affinity: dict[str, float],
    genre_affinity: dict[str, float],
) -> int:
    """
    Build TrackScore for every track in the library for this user.
    Played tracks get engagement-based scoring.
    Unplayed tracks get affinity-based scoring with a cap.
    Returns count of scores written.
    """
    now = datetime.utcnow()

    # Pull all library tracks (not soft-deleted)
    library = db.query(LibraryTrack).filter(
        LibraryTrack.missing_since.is_(None)
    ).all()

    if not library:
        log.warning(f"  No library tracks found for scoring — run library scan first")
        return 0

    # Build play lookup — keyed by jellyfin_item_id
    plays = {p.jellyfin_item_id: p for p in db.query(Play).filter_by(user_id=user_id).all()}

    # Build skip penalty lookup
    skip_map = {
        sk.jellyfin_item_id: float(sk.penalty)
        for sk in db.query(SkipPenalty).filter_by(user_id=user_id).all()
        if sk.total_events >= SKIP_MIN_EVENTS
    }

    max_plays = max((p.play_count for p in plays.values() if p.play_count), default=1) or 1

    # Normalise affinity maps to 0–1 for formula use
    max_artist_aff = max(artist_affinity.values(), default=1.0) or 1.0
    max_genre_aff = max(genre_affinity.values(), default=1.0) or 1.0

    # Delete existing scores for this user
    db.query(TrackScore).filter_by(user_id=user_id).delete()

    count = 0
    for track in library:
        jid = track.jellyfin_item_id
        play = plays.get(jid)
        skip_pen = skip_map.get(jid, 0.0)

        # Artist + genre affinity (0–100, normalised)
        raw_artist = artist_affinity.get(track.artist_name, 0.0)
        raw_genre = genre_affinity.get(track.genre, 0.0)
        a_aff = round((raw_artist / max_artist_aff) * 100, 2)
        g_aff = round((raw_genre / max_genre_aff) * 100, 2)

        if play and play.play_count > 0:
            # ── Played track scoring ──────────────────────────────────────────
            ps = _play_score(play.play_count, max_plays)
            rs = _recency_score(play.last_played)

            base = (
                W_PLAY    * ps +
                W_RECENCY * rs +
                W_ARTIST  * a_aff +
                W_GENRE   * g_aff
            )

            # Apply skip multiplier — but favorites are shielded:
            # an actively liked track's skip penalty is capped so accidental
            # or mood-driven skips can't crater a song the user has hearted.
            if play.is_favorite:
                shielded_pen = min(skip_pen, FAVORITE_SKIP_SHIELD)
                multiplier = _skip_multiplier(shielded_pen)
            else:
                multiplier = _skip_multiplier(skip_pen)

            final = round(min(100.0, base * multiplier), 2)

            # Favorites: additive bonus AND a floor so liked tracks always
            # surface near the top regardless of play count or recency.
            if play.is_favorite:
                final = round(min(100.0, final + FAVORITE_BONUS), 2)
                final = max(final, FAVORITE_FLOOR)  # floor: liked = always near top

            db.add(TrackScore(
                user_id=user_id,
                jellyfin_item_id=jid,
                track_name=track.track_name,
                artist_name=track.artist_name,
                album_name=track.album_name,
                genre=track.genre,
                play_count=play.play_count,
                last_played=play.last_played,
                is_favorite=play.is_favorite,
                is_played=True,
                play_score=str(round(ps, 2)),
                recency_score=str(round(rs, 2)),
                artist_affinity=str(a_aff),
                genre_affinity=str(g_aff),
                skip_penalty=str(round(skip_pen, 4)),
                novelty_bonus="0.0",
                final_score=str(final),
                updated_at=now,
            ))
        else:
            # ── Unplayed track scoring ────────────────────────────────────────
            # Base + affinity pulls, capped at UNPLAYED_CAP
            unplayed_score = (
                UNPLAYED_BASE
                + UNPLAYED_ARTIST_W * a_aff
                + UNPLAYED_GENRE_W  * g_aff
                + UNPLAYED_NOVELTY
            )

            # Artist skip suppression carries over to unplayed tracks
            # (if you always skip an artist, don't surface their unplayed stuff)
            if skip_pen > 0.3:
                unplayed_score *= (1.0 - skip_pen * 0.3)

            final = round(min(UNPLAYED_CAP, unplayed_score), 2)

            db.add(TrackScore(
                user_id=user_id,
                jellyfin_item_id=jid,
                track_name=track.track_name,
                artist_name=track.artist_name,
                album_name=track.album_name,
                genre=track.genre,
                play_count=0,
                last_played=None,
                is_favorite=False,
                is_played=False,
                play_score="0.0",
                recency_score="0.0",
                artist_affinity=str(a_aff),
                genre_affinity=str(g_aff),
                skip_penalty=str(round(skip_pen, 4)),
                novelty_bonus=str(UNPLAYED_NOVELTY),
                final_score=str(final),
                updated_at=now,
            ))

        count += 1

        # Commit in batches to avoid large transactions
        if count % 500 == 0:
            db.flush()
            log.debug(f"  Scored {count} tracks...")

    db.commit()
    log.info(f"  Built {count} track scores for user {user_id}")
    return count


# ── Main entry point ──────────────────────────────────────────────────────────

def rebuild_all_scores(db: Session, user_id: str) -> dict:
    """
    Full scoring rebuild for one user. Runs all three phases in sequence.
    Called by the indexer after play data is synced and library scan is done.
    """
    log.info(f"  Rebuilding scores for user {user_id}...")

    artist_aff = rebuild_artist_profiles(db, user_id)
    log.info(f"  Built {len(artist_aff)} artist profiles")

    genre_aff = rebuild_genre_profiles(db, user_id)
    log.info(f"  Built {len(genre_aff)} genre profiles")

    track_count = rebuild_track_scores(db, user_id, artist_aff, genre_aff)

    return {
        "artist_profiles": len(artist_aff),
        "genre_profiles": len(genre_aff),
        "track_scores": track_count,
    }


def get_score_distribution(db: Session, user_id: str) -> dict:
    """
    Return score distribution stats for diagnostics.
    Useful for verifying the scoring is working as expected.
    """
    from sqlalchemy import func

    scores = db.query(TrackScore).filter_by(user_id=user_id).all()
    if not scores:
        return {}

    final_scores = [float(s.final_score) for s in scores]
    played = [s for s in scores if s.is_played]
    unplayed = [s for s in scores if not s.is_played]

    def _stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        return {
            "count": len(vals),
            "min": round(min(vals), 1),
            "max": round(max(vals), 1),
            "mean": round(sum(vals) / len(vals), 1),
            "p25": round(sorted(vals)[len(vals) // 4], 1),
            "p75": round(sorted(vals)[3 * len(vals) // 4], 1),
        }

    return {
        "all": _stats(final_scores),
        "played": _stats([float(s.final_score) for s in played]),
        "unplayed": _stats([float(s.final_score) for s in unplayed]),
    }
