
"""
JellyDJ Scoring Engine — v5

Changes from v4:
  Scoring normalisation overhaul to fix top-tier compression.

  After popularity enrichment was improved, many tracks started scoring 93-99
  because additive bonuses (popularity +5, replay +12, favorite +6) stacked on
  top of a raw score that was already near-ceilinged by the old constants:

    - SCORE_RESCALE_MAX was 91: any raw_base ≥ 91 clamped to compressed=100
      before bonuses, collapsing all high-scoring tracks to a flat ceiling.
    - COMPRESSION_EXP was 0.75: too aggressive — pulled moderate scores toward
      the top and compressed the gap between "good" and "great".
    - Popularity and replay bonuses were flat-additive, so a 93 + 5 pop = 98
      regardless of how little headroom was left.

  v5 fixes:
    1. SCORE_RESCALE_MAX raised 91 → 100: eliminates the premature ceiling so
       the full raw_base range (0–100) maps cleanly to the compressed range.
    2. COMPRESSION_EXP raised 0.75 → 0.85: closer to linear in the upper range,
       giving meaningful point-gap between "frequently played" and "top track".
    3. Popularity and replay bonuses are now proportional: each bonus is scaled
       by the remaining headroom (1 - score/99) so a track at 90 receives less
       absolute benefit than a track at 70. This prevents stacking bonuses from
       pushing large swaths of the catalogue to 98-99.

  Resulting tiers (approximate, no bonuses):
    38–45  barely liked
    46–62  occasionally played
    63–76  regularly played
    77–86  frequently played
    87–94  heavily played / top-affinity artist
    95–99  favorites or max-played top-artist tracks (requires stacking)

Changes from v3 (kept from v4):
  Phase 1 (ArtistProfile): now pulls replay_boost from UserReplaySignal and
    stamps it onto ArtistProfile.replay_boost. Also copies related_artists and
    tags from ArtistEnrichment for use by Discover Weekly.

  Phase 2 (GenreProfile): unchanged logic, kept for compatibility.

  Phase 3 (TrackScore): factors in replay_boost, global_popularity,
    cooldown_until, skip_streak (all unchanged from v4).
"""
from __future__ import annotations

import json
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

W_PLAY      = 0.45
W_RECENCY   = 0.25
W_ARTIST    = 0.20
W_GENRE     = 0.10

AFFINITY_SCALE        = 0.70
# v5: raised from 91→100 so raw scores are no longer pre-clamped before compression.
# The old value of 91 meant any raw_base ≥ 91 all mapped to compressed=100 before
# bonuses were added, collapsing the top tier into a flat ceiling.
SCORE_RESCALE_MAX     = 100.0
# v5: raised from 0.75→0.85 (closer to linear) for more spread in the 70-95 range.
# 0.75 was too aggressive — it pulled moderate scores up toward the ceiling and
# compressed the distance between "good" and "great" tracks.
COMPRESSION_EXP       = 0.85

UNPLAYED_BASE         = 35.0
UNPLAYED_ARTIST_W     = 0.20
UNPLAYED_GENRE_W      = 0.14
UNPLAYED_NOVELTY      = 2.0
UNPLAYED_CAP          = 65.0

RECENCY_GRACE_DAYS    = 30
RECENCY_DECAY_DAYS    = 365

SKIP_MIN_EVENTS       = 1

FAVORITE_FLOOR        = 67.0
FAVORITE_BONUS        =  6.0
FAVORITE_SKIP_SHIELD  = 0.35
FAVORITE_ARTIST_BOOST = 15.0

# v2: replay boost cap — a voluntary replay can add at most this many points
# to a track's final_score. Keeps replayed tracks from vaulting above true
# 90+ favourites purely from one enthusiastic week of replaying.
REPLAY_BOOST_CAP      = 12.0

# v5: popularity bonuses are now applied proportionally (scaled by remaining headroom)
# so that already-high-scoring tracks receive a smaller absolute nudge than
# lower-scoring tracks. A flat +5 / +10 was enough to vault a 90→99 or 93→99 when
# stacked with replay and favorite bonuses.
# These values are the *maximum* bonus at maximum popularity with full headroom.
POPULARITY_PLAYED_MAX   = 5.0   # max pts for a played track (at pop=100, score=0)
POPULARITY_UNPLAYED_MAX = 10.0  # max pts for an unplayed track


# ── Helper functions ──────────────────────────────────────────────────────────

def _play_score(play_count: int, max_plays: int) -> float:
    if play_count <= 0 or max_plays <= 0:
        return 0.0
    return min(100.0, (math.log1p(play_count) / math.log1p(max_plays)) * 100)


def _recency_score(last_played: Optional[datetime]) -> float:
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
    return max(0.1, 1.0 - float(penalty))


def _compress(raw: float) -> float:
    if raw <= 0:
        return 0.0
    scaled = min(100.0, (raw / SCORE_RESCALE_MAX) * 100.0)
    return round(100.0 * ((scaled / 100.0) ** COMPRESSION_EXP), 2)


# ── Phase 1: Build ArtistProfile ─────────────────────────────────────────────

def rebuild_artist_profiles(db: Session, user_id: str) -> dict[str, float]:
    """
    Build ArtistProfile for every artist the user has played.

    v2: also reads replay boosts and enrichment data to populate
    ArtistProfile.replay_boost, .related_artists, .tags.

    Returns dict of artist_name → affinity_score for use in TrackScore phase.
    """
    now = datetime.utcnow()
    plays = db.query(Play).filter_by(user_id=user_id).all()
    if not plays:
        return {}

    max_plays = max((p.play_count for p in plays if p.play_count), default=1) or 1

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

    # v2: load replay boosts for this user
    from services.enrichment import compute_replay_boosts
    replay_boosts = compute_replay_boosts(db, user_id)

    # v2: load artist enrichment data for related_artists and tags
    try:
        from models import ArtistEnrichment
        enrichment_map = {
            row.artist_name_lower: row
            for row in db.query(ArtistEnrichment).all()
        }
    except Exception:
        enrichment_map = {}

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

        sk_data = artist_skips.get(artist, {})
        total_ev = sk_data.get("total_events", 0)
        skip_ct = sk_data.get("skip_count", 0)
        skip_rate = round(skip_ct / total_ev, 4) if total_ev >= SKIP_MIN_EVENTS else 0.0
        if skip_rate > 0:
            effective_skip_rate = skip_rate * (0.5 if agg["has_favorite"] else 1.0)
            affinity = round(affinity * (1.0 - effective_skip_rate * 0.5), 2)

        primary_genre = max(agg["genres"].items(), key=lambda x: x[1])[0] if agg["genres"] else ""
        affinity_map[artist] = affinity

        # v2: replay boost at artist level
        artist_replay_boost = replay_boosts.get(f"artist:{artist.lower()}", 0.0)

        # v2: enrichment metadata
        enc = enrichment_map.get(artist.lower())
        related = enc.similar_artists if enc else None
        tags = enc.tags if enc else None

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
            replay_boost=artist_replay_boost,
            related_artists=related,
            tags=tags,
        ))

    db.flush()
    return affinity_map


# ── Phase 2: Build GenreProfile ───────────────────────────────────────────────

def rebuild_genre_profiles(db: Session, user_id: str) -> dict[str, float]:
    """
    Build GenreProfile for every genre the user has played.
    Returns dict of genre → affinity_score.

    Fixed: previously averaged per-track play_score and recency_score across
    all tracks in the genre. This compressed everything into a ~13-point range
    (typically 48-61) because:
      1. play_score was normalised against the single most-played track in the
         whole library, so per-track averages for any genre were always modest.
      2. Averaging recency across all genre tracks heavily diluted large genres —
         200 older rock tracks pulled the average recency way down even if the
         user played rock yesterday.

    Now uses:
      - play_score: log(total_genre_plays) / log(max_genre_total_plays) * 100
        Captures how much you listen to this genre as a whole.
      - recency_score: score of the most-recently-played track in the genre.
        If you played any rock song yesterday, rock feels current — averaging
        in hundreds of old tracks shouldn't penalise it.
    """
    now = datetime.utcnow()
    plays = db.query(Play).filter_by(user_id=user_id).all()
    if not plays:
        return {}

    genre_agg: dict[str, dict] = {}
    for p in plays:
        key = p.genre
        if not key:
            continue
        if key not in genre_agg:
            genre_agg[key] = {
                "total_plays": 0,
                "tracks_played": 0,
                "best_last_played": None,
                "has_favorite": False,
            }
        agg = genre_agg[key]
        agg["total_plays"] += p.play_count
        agg["tracks_played"] += 1
        if p.last_played:
            if agg["best_last_played"] is None or p.last_played > agg["best_last_played"]:
                agg["best_last_played"] = p.last_played
        if p.is_favorite:
            agg["has_favorite"] = True

    # Normalise against the most-played genre, not the most-played single track
    max_genre_plays = max((a["total_plays"] for a in genre_agg.values()), default=1) or 1

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
        genre_play_score = _play_score(agg["total_plays"], max_genre_plays)
        genre_recency_score = _recency_score(agg["best_last_played"])
        fav_boost = FAVORITE_ARTIST_BOOST if agg["has_favorite"] else 0.0
        raw_score = W_PLAY * genre_play_score + W_RECENCY * genre_recency_score + fav_boost
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

    v2 additions:
      - replay_boost  added to final_score (capped at REPLAY_BOOST_CAP)
      - global_popularity  copied from TrackEnrichment
      - cooldown_until  stamped from active TrackCooldown rows
      - skip_streak  copied from SkipPenalty.consecutive_skips
    """
    now = datetime.utcnow()

    library = db.query(LibraryTrack).filter(
        LibraryTrack.missing_since.is_(None)
    ).all()

    if not library:
        log.warning("  No library tracks found for scoring — run library scan first")
        return 0

    plays = {p.jellyfin_item_id: p for p in db.query(Play).filter_by(user_id=user_id).all()}

    # Build skip penalty lookup (penalty float + consecutive_skips)
    skip_rows = db.query(SkipPenalty).filter_by(user_id=user_id).all()
    skip_map: dict[str, float] = {}
    streak_map: dict[str, int] = {}
    for sk in skip_rows:
        if sk.total_events >= SKIP_MIN_EVENTS:
            skip_map[sk.jellyfin_item_id] = float(sk.penalty)
        streak_map[sk.jellyfin_item_id] = sk.consecutive_skips or 0

    max_plays = max((p.play_count for p in plays.values() if p.play_count), default=1) or 1

    max_artist_aff = max(artist_affinity.values(), default=1.0) or 1.0
    max_genre_aff = max(genre_affinity.values(), default=1.0) or 1.0

    # v2: load per-track replay boosts
    from services.enrichment import compute_replay_boosts
    replay_boosts = compute_replay_boosts(db, user_id)

    # v2: load TrackEnrichment for global_popularity
    try:
        from models import TrackEnrichment
        enrichment_pop: dict[str, Optional[float]] = {
            row.jellyfin_item_id: row.popularity_score
            for row in db.query(TrackEnrichment.jellyfin_item_id, TrackEnrichment.popularity_score).all()
        }
    except Exception:
        enrichment_pop = {}

    # v2: load active cooldowns for this user
    try:
        from models import TrackCooldown
        active_cooldowns: dict[str, datetime] = {
            row.jellyfin_item_id: row.cooldown_until
            for row in db.query(TrackCooldown).filter_by(user_id=user_id, status="active").all()
        }
        permanent_penalty_ids: set[str] = {
            row.jellyfin_item_id
            for row in db.query(TrackCooldown).filter_by(user_id=user_id, status="permanent").all()
        }
    except Exception:
        active_cooldowns = {}
        permanent_penalty_ids = set()

    db.query(TrackScore).filter_by(user_id=user_id).delete()

    count = 0
    for track in library:
        jid = track.jellyfin_item_id
        play = plays.get(jid)
        skip_pen = skip_map.get(jid, 0.0)
        skip_streak = streak_map.get(jid, 0)

        raw_artist = artist_affinity.get(track.artist_name, 0.0)
        raw_genre = genre_affinity.get(track.genre, 0.0)
        a_aff = round((raw_artist / max_artist_aff) * 100, 2)
        g_aff = round((raw_genre / max_genre_aff) * 100, 2)

        # v2: replay boost — per-track first, fall back to per-artist
        track_boost = replay_boosts.get(jid, 0.0)
        artist_boost = replay_boosts.get(f"artist:{track.artist_name.lower()}", 0.0)
        replay_boost = min(REPLAY_BOOST_CAP, max(track_boost, artist_boost))

        # v2: global popularity (may be None for unenriched tracks)
        global_pop = enrichment_pop.get(jid)

        # v2: cooldown
        cooldown_until = active_cooldowns.get(jid)

        # v2: permanent dislike — heavy penalty on final score
        is_permanent_dislike = jid in permanent_penalty_ids

        if play and play.play_count > 0:
            # ── Played track scoring ──────────────────────────────────────────
            ps = _play_score(play.play_count, max_plays)
            rs = _recency_score(play.last_played)

            a_scaled = a_aff * AFFINITY_SCALE
            g_scaled = g_aff * AFFINITY_SCALE

            raw_base = (
                W_PLAY    * ps +
                W_RECENCY * rs +
                W_ARTIST  * a_scaled +
                W_GENRE   * g_scaled
            )

            compressed = _compress(raw_base)

            if play.is_favorite:
                shielded_pen = min(skip_pen, FAVORITE_SKIP_SHIELD)
                multiplier = _skip_multiplier(shielded_pen)
            else:
                multiplier = _skip_multiplier(skip_pen)

            final = round(compressed * multiplier, 2)

            if play.is_favorite:
                final = round(min(99.0, final + FAVORITE_BONUS), 2)
                final = max(final, FAVORITE_FLOOR)

            # v2: add replay boost (additive, capped)
            # v5: scale by remaining headroom so high-scoring tracks receive a
            # smaller absolute bump — prevents stacking from vaulting 93→99.
            if replay_boost > 0:
                headroom = max(0.0, (99.0 - final) / 99.0)
                effective_replay = replay_boost * (0.4 + 0.6 * headroom)
                final = round(min(99.0, final + effective_replay), 2)

            # v3: song popularity nudge — tie-breaker for played tracks
            # v5: proportional — high-scoring tracks get less benefit
            if global_pop is not None:
                headroom = max(0.0, (99.0 - final) / 99.0)
                pop_nudge = round((global_pop / 100.0) * POPULARITY_PLAYED_MAX * (0.5 + 0.5 * headroom), 2)
                final = round(min(99.0, final + pop_nudge), 2)

            # v2: permanent dislike — score capped at 20 so it essentially
            # never surfaces in any playlist
            if is_permanent_dislike:
                final = min(final, 20.0)

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
                # v2
                replay_boost=replay_boost,
                global_popularity=global_pop,
                cooldown_until=cooldown_until,
                skip_streak=skip_streak,
                # v4: holiday
                holiday_tag=track.holiday_tag,
                holiday_exclude=(True if track.holiday_exclude else False),
            ))

        else:
            # ── Unplayed track scoring ────────────────────────────────────────
            unplayed_score = (
                UNPLAYED_BASE
                + UNPLAYED_ARTIST_W * (a_aff * AFFINITY_SCALE)
                + UNPLAYED_GENRE_W  * (g_aff * AFFINITY_SCALE)
                + UNPLAYED_NOVELTY
            )

            if skip_pen > 0.3:
                unplayed_score *= (1.0 - skip_pen * 0.3)

            final = round(min(UNPLAYED_CAP, unplayed_score), 2)

            # v2: replay boost applies to unplayed tracks too — user might have
            # heard a track in a playlist, sought out more by the artist (artist_return
            # signal fires), and we haven't fully indexed that track as played yet.
            # v5: proportional — scales by remaining headroom below the unplayed cap.
            if replay_boost > 0:
                headroom = max(0.0, (UNPLAYED_CAP - final) / UNPLAYED_CAP)
                effective_replay = replay_boost * (0.4 + 0.6 * headroom)
                final = round(min(UNPLAYED_CAP, final + effective_replay), 2)

            # v3: song popularity is the primary external quality signal for
            # unplayed tracks — best hint that a song is worth surfacing.
            # v5: proportional within the unplayed cap.
            if global_pop is not None:
                headroom = max(0.0, (UNPLAYED_CAP - final) / UNPLAYED_CAP)
                pop_nudge = round((global_pop / 100.0) * POPULARITY_UNPLAYED_MAX * (0.5 + 0.5 * headroom), 2)
                final = round(min(UNPLAYED_CAP, final + pop_nudge), 2)

            if is_permanent_dislike:
                final = min(final, 20.0)

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
                # v2
                replay_boost=replay_boost,
                global_popularity=global_pop,
                cooldown_until=cooldown_until,
                skip_streak=skip_streak,
                # v4: holiday
                holiday_tag=track.holiday_tag,
                holiday_exclude=(True if track.holiday_exclude else False),
            ))

        count += 1
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
    v2: also runs expire_cooldowns() at the start so scores reflect current state.
    """
    log.info(f"  Rebuilding scores for user {user_id}...")

    # v2: expire any cooldowns that have timed out before scoring
    try:
        from services.enrichment import expire_cooldowns, detect_replay_signals
        expired = expire_cooldowns(db)
        if expired:
            log.info(f"  Expired {expired} cooldowns before score rebuild")
        detect_replay_signals(db, user_id)
    except Exception as e:
        log.warning(f"  Pre-score enrichment steps failed (non-fatal): {e}")

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
