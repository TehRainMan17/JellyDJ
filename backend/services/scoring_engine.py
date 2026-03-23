"""
JellyDJ Scoring Engine — v7

Changes from v6:
  Artist affinity overhaul.

  Root-cause analysis of v6 artist scoring bugs:

    1. CEILING BUG — scores capped at ~65/100 for all users.
       The formula was:
         raw_score = W_PLAY * avg_play + W_RECENCY * avg_recency   (+ fav_boost)
       W_PLAY=0.45 and W_RECENCY=0.25 sum to only 0.70, so even at perfect
       avg_play=100 and avg_recency=100 the maximum raw_score before the
       fav_boost is 70.0.  FAVORITE_ARTIST_BOOST=15 can push it to 85, but only
       for favorited artists.  Non-favorited artists were hard-capped at 70.

    2. BREADTH PUNISHMENT — artists with many songs penalised vs. one-hit wonders.
       avg_play was calculated per-track: each track's play_score was computed
       relative to the single most-played track in the ENTIRE library
       (max_plays), then averaged across all of the artist's tracks.

       Example (from real user data):
         Radiohead: 1 track (Creep) played 60× → play_score = 100 → avg = 100
         George Ezra: 30 tracks, each played 15× → each play_score ≈ 67 → avg ≈ 67

       Radiohead's avg_play is 49% higher than George Ezra's, so Radiohead
       scores dramatically higher even though George Ezra represents
       30× the total listening engagement.

       This is the opposite of the desired behaviour.

  v7 fixes:

    1. TOTAL-PLAYS normalisation (mirrors the genre-profile fix in v5).
       Artist affinity now uses the artist's TOTAL plays normalised against
       the most-played artist (by total plays), not the per-track average.
       An artist with 30 tracks × 15 plays = 450 total plays correctly scores
       higher than an artist with 1 track × 60 plays.

    2. BREADTH BONUS — reward catalogue depth.
       A log-scaled bonus (0–ARTIST_BREADTH_BONUS_MAX pts) rewards artists
       where the user has played many distinct tracks.  This captures "I enjoy
       their whole back-catalogue" vs. "I heard one song".
       Formula: log(tracks_played+1) / log(ARTIST_BREADTH_MAX_TRACKS+1)
                × ARTIST_BREADTH_BONUS_MAX
       (capped at ARTIST_BREADTH_BONUS_MAX regardless of track count)

    3. BEST-RECENCY instead of AVERAGE-RECENCY.
       Recency now uses the most-recently-played track for the artist, not
       the average across all tracks.  This mirrors the genre fix: if you
       played any George Ezra track this week, George Ezra is "current" —
       averaging in 29 older track dates should not penalise him.

    4. WEIGHTS adjusted so the full 0–100 range is reachable.
       New formula (before skip penalty):
         raw = W_PLAY * total_play_score
             + W_RECENCY * best_recency_score
             + breadth_bonus
             [+ FAVORITE_ARTIST_BOOST if any track is favorited]
       W_PLAY(0.45) + W_RECENCY(0.25) = 0.70 → 70 pts at perfect play+recency
       ARTIST_BREADTH_BONUS_MAX = 15 pts → up to 85 without favorite
       FAVORITE_ARTIST_BOOST    = 15 pts → up to 100 with favorite
       Scores now span the full 0–100 range naturally.

  Resulting approximate artist affinity tiers (no favorite):
    0         never played (not stored in ArtistProfile)
    5–15      heard one track once, long ago
    25–45     casual listener — some plays, moderate catalogue
    55–75     regular listener — many plays or wide catalogue
    80–90     heavy listener — deep catalogue + high play count
    90–100    heavy listener with favorited tracks

Changes from v5/v6 (unplayed scoring) are preserved unchanged.

Changes in v8 (skip penalty overhaul):

  5. ARTIST SKIP PENALTY DOUBLE-HALVING BUG — non-favorited artists had their
     skip penalty halved twice.  The formula was:
       effective = skip_rate * 1.0  (non-favorited)
       affinity  = affinity * (1.0 - effective * 0.5)
     The extra * 0.5 meant a 100% skip rate only reduced affinity by 50%.
     Fixed: removed the * 0.5 multiplier on the penalty application.
     A floor of 1.0 prevents affinity from dropping to absolute zero.

  6. UNPLAYED TRACKS IGNORE ARTIST SKIP HISTORY — an unplayed track scored at
     full novelty bonus even when its artist had a 90% skip rate across all
     their other tracks.  Fixed by building an artist-level skip rate map and
     applying a proportional discount to unplayed scores when skip_rate > 0.2.
     This ensures that new tracks by a heavily-skipped artist start at a lower
     score rather than appearing as equally attractive as a loved artist.
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
SCORE_RESCALE_MAX     = 100.0
COMPRESSION_EXP       = 0.85

UNPLAYED_BASE         = 35.0
# v6: raised from 0.20 → 0.35 so artist affinity drives more of the unplayed score
UNPLAYED_ARTIST_W     = 0.35
# v6: raised from 0.14 → 0.20
UNPLAYED_GENRE_W      = 0.20
# v6: novelty_bonus is now a *dynamic* range; these two constants define it:
#   novelty_bonus = UNPLAYED_NOVELTY_BASE + UNPLAYED_NOVELTY_ARTIST_SCALE * (a_aff/100)
# At zero affinity:  2.0 pts  (same as the old flat constant)
# At full affinity: 17.0 pts  (meaningful lift for beloved unheard artists)
UNPLAYED_NOVELTY_BASE         = 2.0
UNPLAYED_NOVELTY_ARTIST_SCALE = 15.0
# v6: raised from 65 → 78 so high-affinity unplayed tracks can reach discovery thresholds
UNPLAYED_CAP          = 78.0

RECENCY_GRACE_DAYS    = 30
RECENCY_DECAY_DAYS    = 365

SKIP_MIN_EVENTS       = 1

FAVORITE_FLOOR        = 67.0
FAVORITE_BONUS        =  6.0
FAVORITE_SKIP_SHIELD  = 0.35
FAVORITE_ARTIST_BOOST = 15.0

REPLAY_BOOST_CAP      = 12.0

POPULARITY_PLAYED_MAX   = 5.0
POPULARITY_UNPLAYED_MAX = 10.0

# v7: artist breadth bonus constants
# Rewards users who listen to many distinct tracks from an artist.
# log-scaled so the bonus grows quickly for the first ~10 tracks then plateaus.
ARTIST_BREADTH_BONUS_MAX  = 15.0   # max pts awarded for catalogue depth
ARTIST_BREADTH_MAX_TRACKS = 50     # track count at which bonus is fully awarded


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


def _breadth_bonus(tracks_played: int) -> float:
    """
    Log-scaled bonus for playing many distinct tracks from an artist.
    Returns 0–ARTIST_BREADTH_BONUS_MAX points.
    """
    if tracks_played <= 0:
        return 0.0
    return round(
        min(
            (math.log1p(tracks_played) / math.log1p(ARTIST_BREADTH_MAX_TRACKS))
            * ARTIST_BREADTH_BONUS_MAX,
            ARTIST_BREADTH_BONUS_MAX,
        ),
        2,
    )


# ── Phase 1: Build ArtistProfile ─────────────────────────────────────────────

def rebuild_artist_profiles(db: Session, user_id: str) -> dict[str, float]:
    """
    Build ArtistProfile for every artist the user has played.

    v2: also reads replay boosts and enrichment data to populate
    ArtistProfile.replay_boost, .related_artists, .tags.

    v7: artist affinity overhaul — see module docstring.
      - Normalises against total artist plays (not per-track average).
      - Uses best (most recent) recency, not average.
      - Adds a breadth bonus for catalogue depth.
      - Scores now span the full 0–100 range.

    Returns dict of artist_name → affinity_score for use in TrackScore phase.
    """
    now = datetime.utcnow()
    plays = db.query(Play).filter_by(user_id=user_id).all()

    # v8: group by lowercase key so "Cage the Elephant" and "Cage The Elephant"
    # (different capitalisation in Jellyfin metadata) merge into one profile.
    # The canonical display name is the form with the highest play count.
    artist_agg: dict[str, dict] = {}  # key = artist_name.lower()
    for p in plays:
        raw_name = p.artist_name
        if not raw_name:
            continue
        key = raw_name.lower().strip()
        if key not in artist_agg:
            artist_agg[key] = {
                "canonical_name": raw_name,
                "canonical_play_count": p.play_count,
                "total_plays": 0,
                "tracks_played": 0,
                "best_last_played": None,   # v7: best recency instead of average
                "has_favorite": False,
                "genres": {},
            }
        agg = artist_agg[key]
        # Keep the name form with the most plays as the display name
        if p.play_count > agg["canonical_play_count"]:
            agg["canonical_name"] = raw_name
            agg["canonical_play_count"] = p.play_count

        agg["total_plays"] += p.play_count
        agg["tracks_played"] += 1

        # v7: track the most-recently-played date across all artist tracks
        if p.last_played:
            if (agg["best_last_played"] is None
                    or p.last_played > agg["best_last_played"]):
                agg["best_last_played"] = p.last_played

        if p.is_favorite:
            agg["has_favorite"] = True
        if p.genre:
            agg["genres"][p.genre] = agg["genres"].get(p.genre, 0) + p.play_count

    # v7: normalise against the most-played ARTIST (by total plays),
    #     not the most-played single track.  This prevents one-hit wonders
    #     from dominating the normalisation denominator.
    max_artist_plays = max(
        (a["total_plays"] for a in artist_agg.values()),
        default=1,
    ) or 1

    skip_rows = db.query(SkipPenalty).filter_by(user_id=user_id).all()
    # v8: also key artist_skips by lowercase so they match the normalised artist_agg keys
    artist_skips: dict[str, dict] = {}
    for sk in skip_rows:
        a = sk.artist_name
        if not a:
            continue
        key = a.lower().strip()
        if key not in artist_skips:
            artist_skips[key] = {"total_events": 0, "skip_count": 0, "display_name": a}
        artist_skips[key]["total_events"] += sk.total_events
        artist_skips[key]["skip_count"] += sk.skip_count

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

    for artist_key, agg in artist_agg.items():
        # v8: use the canonical display name (most-played form) rather than
        # the raw key, so the profile reflects the correct capitalisation.
        artist = agg["canonical_name"]

        # v7: total-play score — how much has the user played this artist overall?
        total_play_score = _play_score(agg["total_plays"], max_artist_plays)

        # v7: best-recency — is *any* of this artist's tracks fresh?
        best_recency = _recency_score(agg["best_last_played"])

        # v7: breadth bonus — reward catalogue depth (many distinct tracks)
        breadth = _breadth_bonus(agg["tracks_played"])

        fav_boost = FAVORITE_ARTIST_BOOST if agg["has_favorite"] else 0.0

        raw_score = (
            W_PLAY    * total_play_score
            + W_RECENCY * best_recency
            + breadth
        )
        affinity = round(min(100.0, raw_score + fav_boost), 2)

        # Skip penalty — reduces affinity proportionally.
        # v8 fix: previously the formula was (1.0 - effective_skip_rate * 0.5),
        # which halved the penalty a second time after already halving it for
        # favorites — meaning a 100% skip rate on a non-favorited artist only
        # reduced affinity by 50%.  Now uses the full effective rate so heavily-
        # skipped artists fall proportionally further.  A floor of 1.0 ensures
        # the artist still surfaces if the user changes their mind later.
        # v8: look up by lowercase key since artist_skips is now normalised.
        sk_data = artist_skips.get(artist_key, {})
        total_ev = sk_data.get("total_events", 0)
        skip_ct = sk_data.get("skip_count", 0)
        skip_rate = round(skip_ct / total_ev, 4) if total_ev >= SKIP_MIN_EVENTS else 0.0
        if skip_rate > 0:
            effective_skip_rate = skip_rate * (0.5 if agg["has_favorite"] else 1.0)
            affinity = round(max(1.0, affinity * (1.0 - effective_skip_rate)), 2)

        primary_genre = (
            max(agg["genres"].items(), key=lambda x: x[1])[0]
            if agg["genres"] else ""
        )
        # Store both the canonical name and the lowercase key so downstream
        # lookups (e.g. rebuild_track_scores) find the right affinity regardless
        # of capitalisation differences between LibraryTrack and Play rows.
        affinity_map[artist] = affinity
        if artist_key != artist.lower():
            affinity_map[artist_key] = affinity  # lowercase alias

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

    # v8: create stub profiles for skip-only artists — artists the user has
    # skipped but never completed a full listen of (so they have SkipPenalty
    # rows but no Play rows).  These stubs make them visible in insights and
    # ensure their skip signal is applied during track scoring.
    for skip_key, sk_data in artist_skips.items():
        if skip_key in artist_agg:
            continue  # Already handled above
        total_ev = sk_data.get("total_events", 0)
        skip_ct = sk_data.get("skip_count", 0)
        if total_ev < 3:
            continue  # Not enough data to be meaningful
        skip_rate = round(skip_ct / total_ev, 4)
        # Stub affinity: very low (0–5), inversely proportional to skip rate
        stub_affinity = round(max(1.0, 5.0 * (1.0 - skip_rate)), 2)
        display_name = sk_data.get("display_name", skip_key)
        affinity_map[display_name] = stub_affinity
        affinity_map[skip_key] = stub_affinity  # lowercase alias

        db.add(ArtistProfile(
            user_id=user_id,
            artist_name=display_name,
            total_plays=0,
            total_tracks_played=0,
            total_skips=skip_ct,
            skip_rate=str(skip_rate),
            has_favorite=False,
            primary_genre="",
            affinity_score=str(stub_affinity),
            updated_at=now,
            replay_boost=0.0,
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

    v6 additions (unplayed scoring overhaul):
      - novelty_bonus is now dynamic: scales with artist_affinity so tracks
        from loved artists receive a meaningful lift (up to +15 pts) rather
        than a flat +2. Stored in TrackScore.novelty_bonus for transparency.
      - UNPLAYED_ARTIST_W and UNPLAYED_GENRE_W increased so affinity drives
        more of the unplayed base score.
      - UNPLAYED_CAP raised 65 → 78 so high-affinity unplayed tracks can
        reach the thresholds used by "New For You" discovery filters.

    v7 note:
      - artist_affinity values coming in from rebuild_artist_profiles() now
        use the v7 total-play / breadth-bonus formula, so a_aff correctly
        reflects genuine engagement.  No changes needed in this phase.
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

    # Build per-artist aggregated skip rate for penalising unplayed tracks.
    # Unplayed tracks normally inherit only the artist's affinity score; this
    # map lets us also apply the artist's skip signal so that tracks by a
    # heavily-skipped artist score lower even before they've been heard.
    _artist_skip_agg: dict[str, dict] = {}
    for sk in skip_rows:
        a = sk.artist_name
        if not a:
            continue
        key = a.lower().strip()  # normalise so lookups are case-insensitive
        if key not in _artist_skip_agg:
            _artist_skip_agg[key] = {"total_events": 0, "skip_count": 0}
        _artist_skip_agg[key]["total_events"] += sk.total_events
        _artist_skip_agg[key]["skip_count"] += sk.skip_count
    artist_skip_rate_map: dict[str, float] = {
        a: d["skip_count"] / d["total_events"]
        for a, d in _artist_skip_agg.items()
        if d["total_events"] >= SKIP_MIN_EVENTS
    }

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

        # v8: fall back to lowercase key in case LibraryTrack and Play rows
        # have different capitalisations for the same artist name.
        raw_artist = (
            artist_affinity.get(track.artist_name)
            or artist_affinity.get(track.artist_name.lower(), 0.0)
        )
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
            # v6: novelty_bonus is now dynamic — scales with artist_affinity so
            # unplayed tracks from loved artists receive a meaningful lift.
            # At a_aff=0:   novelty_bonus = 2.0  (same as old flat constant)
            # At a_aff=100: novelty_bonus = 17.0 (significant lift for loved artists)
            novelty_bonus = round(
                UNPLAYED_NOVELTY_BASE + UNPLAYED_NOVELTY_ARTIST_SCALE * (a_aff / 100.0),
                2,
            )

            unplayed_score = (
                UNPLAYED_BASE
                + UNPLAYED_ARTIST_W * (a_aff * AFFINITY_SCALE)
                + UNPLAYED_GENRE_W  * (g_aff * AFFINITY_SCALE)
                + novelty_bonus
            )

            if skip_pen > 0.3:
                unplayed_score *= (1.0 - skip_pen * 0.3)

            # v8: apply artist-level skip rate to unplayed tracks.
            # Prevents new unheard tracks by a heavily-skipped artist from
            # scoring at full novelty just because they haven't been heard yet.
            # v8: case-insensitive fallback for capitalisation mismatches.
            artist_skip_rate = (
                artist_skip_rate_map.get(track.artist_name)
                or artist_skip_rate_map.get(track.artist_name.lower(), 0.0)
            )
            if artist_skip_rate > 0.2:
                unplayed_score *= (1.0 - artist_skip_rate * 0.5)

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
                novelty_bonus=str(novelty_bonus),
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
