"""
JellyDJ Enrichment Service — v2

Fetches metadata from Last.fm (primary) and MusicBrainz (fallback) for every
track and artist in the library. Results are stored in:
  - TrackEnrichment   (per-track: playcount, listeners, tags, similar tracks)
  - ArtistEnrichment  (per-artist: bio, tags, similar artists, trend direction)
  - ArtistRelation    (similarity edges, powers the network graph)
  - LibraryTrack      (denormalised fast columns: global_playcount, tags, enriched_at)

Rate limits:
  Last.fm:      5 req/s  → we use 0.22s delay between calls (4.5 req/s, safe margin)
  MusicBrainz:  1 req/s  → we use 1.1s delay

Enrichment is idempotent: rows are upserted, not duplicated.
Tracks enriched within expires_at are skipped unless force=True.

The enrichment job is registered in the scheduler with a 48h default interval.
It processes tracks in batches of 50 per run to stay within rate limits without
hogging the server for hours. A full library enrichment may take several runs.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
LASTFM_DELAY   = 0.22   # seconds between Last.fm calls
MB_DELAY       = 1.1    # seconds between MusicBrainz calls

# ── Enrichment TTL ────────────────────────────────────────────────────────────
TRACK_TTL_DAYS  = 30    # re-enrich tracks after this many days
ARTIST_TTL_DAYS = 14    # re-enrich artists more frequently (trends change)

# ── Batch sizes per scheduler run ────────────────────────────────────────────
TRACKS_PER_RUN  = 100   # tracks to enrich per scheduler invocation
ARTISTS_PER_RUN = 50    # artists to enrich per scheduler invocation

# ── Popularity scoring ────────────────────────────────────────────────────────
# Log-scale Last.fm listeners → 0-100. Reference points:
#   10M listeners → ~95,  1M → ~80,  100K → ~65,  10K → ~50,  1K → ~35
LISTENER_SCALE = 10_000_000   # listeners at which score approaches 100

# ── Cooldown skip threshold ───────────────────────────────────────────────────
COOLDOWN_SKIP_STREAK_THRESHOLD = 3   # consecutive skips to trigger cooldown
COOLDOWN_DAYS_FIRST  = 7            # first cooldown: 1 week
COOLDOWN_DAYS_SECOND = 14           # second cooldown: 2 weeks
COOLDOWN_DAYS_THIRD  = 30           # third cooldown: 1 month before permanent
COOLDOWN_CYCLES_PERMANENT = 3       # after this many cycles → permanent penalty

# ── Replay signal ─────────────────────────────────────────────────────────────
REPLAY_WINDOW_DAYS   = 7    # voluntary replay within this window → high signal
REPLAY_BOOST_TRACK   = 8.0  # score pts added for same-track replay
REPLAY_BOOST_ARTIST  = 4.0  # score pts for same-artist return (different track)
REPLAY_BOOST_SESSION = 6.0  # score pts for same-session artist return


def _listeners_to_score(listeners: Optional[int]) -> float:
    """Log-scale Last.fm listener count → 0-100 popularity score."""
    if not listeners or listeners <= 0:
        return 0.0
    return round(min(100.0, (math.log1p(listeners) / math.log1p(LISTENER_SCALE)) * 100), 1)


def _get_lastfm_net(db: Session):
    """Build a pylast.LastFMNetwork from stored credentials. Returns None if unconfigured."""
    try:
        import pylast
        from models import ExternalApiSettings
        from crypto import decrypt

        key_row = db.query(ExternalApiSettings).filter_by(key="lastfm_api_key").first()
        sec_row = db.query(ExternalApiSettings).filter_by(key="lastfm_api_secret").first()
        if not key_row or not sec_row:
            return None
        api_key = decrypt(key_row.value_encrypted)
        api_secret = decrypt(sec_row.value_encrypted)
        if not api_key or not api_secret:
            return None
        return pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
    except Exception:
        return None


# ── Track enrichment ──────────────────────────────────────────────────────────

def _enrich_track_lastfm(net, artist_name: str, track_name: str) -> dict:
    """Fetch track data from Last.fm. Returns dict of enrichment fields."""
    result = {
        "mbid": None,
        "lastfm_url": None,
        "global_playcount": None,
        "global_listeners": None,
        "tags": None,
        "similar_tracks": None,
        "popularity_score": None,
        "source": "lastfm",
    }
    try:
        track = net.get_track(artist_name, track_name)

        try:
            result["global_playcount"] = int(track.get_playcount() or 0)
        except Exception:
            pass
        try:
            result["global_listeners"] = int(track.get_listener_count() or 0)
        except Exception:
            pass
        try:
            result["lastfm_url"] = track.get_url()
        except Exception:
            pass
        try:
            result["mbid"] = track.get_mbid()
        except Exception:
            pass

        # Tags (top 5)
        try:
            raw_tags = track.get_top_tags(limit=5)
            result["tags"] = json.dumps([
                {"name": t.item.get_name(), "count": int(t.weight or 0)}
                for t in raw_tags
            ])
        except Exception:
            pass

        # Similar tracks (top 5)
        try:
            similar = track.get_similar(limit=5)
            result["similar_tracks"] = json.dumps([
                {
                    "name": s.item.get_name(),
                    "artist": s.item.get_artist().get_name(),
                    "match": float(s.match or 0),
                }
                for s in similar
            ])
        except Exception:
            pass

        result["popularity_score"] = _listeners_to_score(result["global_listeners"])

    except Exception as e:
        log.debug(f"  Last.fm track lookup failed for '{artist_name}' — '{track_name}': {e}")
        result["source"] = "none"

    return result


def enrich_tracks(db: Session, force: bool = False, limit: int = TRACKS_PER_RUN) -> dict:
    """
    Enrich the next batch of unenriched (or stale) tracks with Last.fm data.
    Returns stats dict.
    """
    from models import LibraryTrack, TrackEnrichment

    net = _get_lastfm_net(db)
    if not net:
        log.info("Enrichment: Last.fm not configured — skipping track enrichment")
        return {"skipped": True, "reason": "lastfm_not_configured"}

    now = datetime.utcnow()
    expires_threshold = now  # anything with expires_at < now is stale

    # Select tracks needing enrichment: no enrichment row, or expired
    existing_enriched_ids = {
        row.jellyfin_item_id
        for row in db.query(TrackEnrichment.jellyfin_item_id)
        .filter(TrackEnrichment.expires_at > expires_threshold)
        .all()
    }

    if force:
        existing_enriched_ids = set()

    tracks = (
        db.query(LibraryTrack)
        .filter(LibraryTrack.missing_since.is_(None))
        .filter(LibraryTrack.jellyfin_item_id.notin_(existing_enriched_ids))
        .order_by(LibraryTrack.first_seen.asc())
        .limit(limit)
        .all()
    )

    if not tracks:
        log.info("Enrichment: all tracks are fresh — nothing to enrich")
        return {"enriched": 0, "skipped": 0, "failed": 0}

    log.info(f"Enrichment: enriching {len(tracks)} tracks from Last.fm...")
    enriched = failed = 0

    for lt in tracks:
        if not lt.artist_name or not lt.track_name:
            failed += 1
            continue

        data = _enrich_track_lastfm(net, lt.artist_name, lt.track_name)
        time.sleep(LASTFM_DELAY)

        try:
            existing = db.query(TrackEnrichment).filter_by(
                jellyfin_item_id=lt.jellyfin_item_id
            ).first()

            if not existing:
                existing = TrackEnrichment(
                    jellyfin_item_id=lt.jellyfin_item_id,
                    artist_name=lt.artist_name,
                    track_name=lt.track_name,
                    album_name=lt.album_name,
                )
                db.add(existing)

            existing.mbid = data["mbid"]
            existing.lastfm_url = data["lastfm_url"]
            existing.global_playcount = data["global_playcount"]
            existing.global_listeners = data["global_listeners"]
            existing.tags = data["tags"]
            existing.similar_tracks = data["similar_tracks"]
            existing.popularity_score = data["popularity_score"]
            existing.source = data["source"]
            existing.enriched_at = now
            existing.expires_at = now + timedelta(days=TRACK_TTL_DAYS)

            # Denormalise key fields onto LibraryTrack for fast SQL queries
            lt.global_playcount = data["global_playcount"]
            lt.global_listeners = data["global_listeners"]
            lt.tags = data["tags"]
            lt.mbid = data["mbid"]
            lt.enriched_at = now
            lt.enrichment_source = data["source"]

            db.commit()
            enriched += 1

        except Exception as e:
            log.warning(f"  Track enrichment DB write failed for '{lt.track_name}': {e}")
            db.rollback()
            failed += 1

    log.info(f"Track enrichment complete: {enriched} enriched, {failed} failed")
    return {"enriched": enriched, "failed": failed}


# ── Artist enrichment ─────────────────────────────────────────────────────────

def _enrich_artist_lastfm(net, artist_name: str, previous_listeners: Optional[int]) -> dict:
    """Fetch artist data from Last.fm including similar artists and tags."""
    result = {
        "mbid": None,
        "lastfm_url": None,
        "image_url": None,
        "global_listeners": None,
        "global_playcount": None,
        "biography": None,
        "tags": None,
        "similar_artists": None,
        "popularity_score": None,
        "trend_direction": "stable",
        "trend_pct": 0.0,
        "source": "lastfm",
    }
    try:
        artist = net.get_artist(artist_name)

        try:
            result["global_listeners"] = int(artist.get_listener_count() or 0)
        except Exception:
            pass
        try:
            result["global_playcount"] = int(artist.get_playcount() or 0)
        except Exception:
            pass
        try:
            result["lastfm_url"] = artist.get_url()
        except Exception:
            pass
        try:
            result["mbid"] = artist.get_mbid()
        except Exception:
            pass

        # Biography (trimmed)
        try:
            bio = artist.get_bio_summary(language="en") or ""
            result["biography"] = bio[:500] if bio else None
        except Exception:
            pass

        # Image
        try:
            import pylast
            result["image_url"] = artist.get_cover_image(pylast.SIZE_LARGE)
        except Exception:
            pass

        # Tags (top 5 names only for compact storage)
        try:
            raw_tags = artist.get_top_tags(limit=5)
            result["tags"] = json.dumps([t.item.get_name() for t in raw_tags])
        except Exception:
            pass

        # Similar artists (up to 10, with match score)
        try:
            similar = artist.get_similar(limit=10)
            result["similar_artists"] = json.dumps([
                {
                    "name": s.item.get_name(),
                    "match": round(float(s.match or 0), 4),
                }
                for s in similar
            ])
        except Exception:
            pass

        # Popularity score
        result["popularity_score"] = _listeners_to_score(result["global_listeners"])

        # Trend direction
        cur = result["global_listeners"]
        if cur and previous_listeners and previous_listeners > 0:
            pct_change = (cur - previous_listeners) / previous_listeners * 100
            result["trend_pct"] = round(pct_change, 1)
            if pct_change > 5:
                result["trend_direction"] = "rising"
            elif pct_change < -5:
                result["trend_direction"] = "falling"
            else:
                result["trend_direction"] = "stable"

    except Exception as e:
        log.debug(f"  Last.fm artist lookup failed for '{artist_name}': {e}")
        result["source"] = "none"

    return result


def enrich_artists(db: Session, force: bool = False, limit: int = ARTISTS_PER_RUN) -> dict:
    """
    Enrich the next batch of unenriched/stale artists with Last.fm data.
    Also populates/updates ArtistRelation rows from similar_artists.
    """
    from models import LibraryTrack, ArtistEnrichment, ArtistRelation

    net = _get_lastfm_net(db)
    if not net:
        log.info("Enrichment: Last.fm not configured — skipping artist enrichment")
        return {"skipped": True, "reason": "lastfm_not_configured"}

    now = datetime.utcnow()

    # Find all unique artist names in the library
    all_artists = (
        db.query(LibraryTrack.artist_name)
        .filter(LibraryTrack.missing_since.is_(None))
        .filter(LibraryTrack.artist_name != "")
        .distinct()
        .all()
    )
    all_artist_names = {row.artist_name for row in all_artists}

    # Which artists need enrichment?
    fresh_artists = {
        row.artist_name_lower
        for row in db.query(ArtistEnrichment.artist_name_lower)
        .filter(ArtistEnrichment.expires_at > now)
        .all()
    }
    if force:
        fresh_artists = set()

    to_enrich = [
        name for name in all_artist_names
        if name.lower() not in fresh_artists
    ][:limit]

    if not to_enrich:
        log.info("Enrichment: all artists are fresh")
        return {"enriched": 0, "failed": 0}

    log.info(f"Enrichment: enriching {len(to_enrich)} artists from Last.fm...")
    enriched = failed = relations_added = 0

    for artist_name in to_enrich:
        existing = db.query(ArtistEnrichment).filter_by(
            artist_name_lower=artist_name.lower()
        ).first()
        prev_listeners = existing.global_listeners if existing else None

        data = _enrich_artist_lastfm(net, artist_name, prev_listeners)
        time.sleep(LASTFM_DELAY)

        try:
            if not existing:
                existing = ArtistEnrichment(
                    artist_name=artist_name,
                    artist_name_lower=artist_name.lower(),
                )
                db.add(existing)

            existing.mbid = data["mbid"]
            existing.lastfm_url = data["lastfm_url"]
            existing.image_url = data["image_url"]
            existing.global_listeners = data["global_listeners"]
            existing.global_playcount = data["global_playcount"]
            existing.biography = data["biography"]
            existing.tags = data["tags"]
            existing.similar_artists = data["similar_artists"]
            existing.popularity_score = data["popularity_score"]
            existing.listeners_previous = prev_listeners
            existing.trend_direction = data["trend_direction"]
            existing.trend_pct = data["trend_pct"]
            existing.source = data["source"]
            existing.enriched_at = now
            existing.expires_at = now + timedelta(days=ARTIST_TTL_DAYS)

            db.flush()

            # Populate ArtistRelation edges
            if data["similar_artists"]:
                try:
                    similar_list = json.loads(data["similar_artists"])
                    for sim in similar_list:
                        b_name = sim.get("name", "")
                        match = sim.get("match", 0.0)
                        if not b_name:
                            continue
                        rel = db.query(ArtistRelation).filter_by(
                            artist_a=artist_name, artist_b=b_name
                        ).first()
                        if not rel:
                            rel = ArtistRelation(
                                artist_a=artist_name,
                                artist_b=b_name,
                            )
                            db.add(rel)
                            relations_added += 1
                        rel.match_score = match
                        rel.source = "lastfm"
                        rel.updated_at = now
                except Exception as e:
                    log.debug(f"  ArtistRelation upsert failed for {artist_name}: {e}")

            db.commit()
            enriched += 1

        except Exception as e:
            log.warning(f"  Artist enrichment DB write failed for '{artist_name}': {e}")
            db.rollback()
            failed += 1

    log.info(
        f"Artist enrichment complete: {enriched} enriched, {failed} failed, "
        f"{relations_added} new relations"
    )
    return {"enriched": enriched, "failed": failed, "relations_added": relations_added}


# ── Cooldown management ───────────────────────────────────────────────────────

def _cooldown_duration_days(cooldown_count: int) -> int:
    """Return cooldown duration based on how many times this track has been cooled down."""
    if cooldown_count == 1:
        return COOLDOWN_DAYS_FIRST
    elif cooldown_count == 2:
        return COOLDOWN_DAYS_SECOND
    else:
        return COOLDOWN_DAYS_THIRD


def check_and_apply_cooldown(
    db: Session,
    user_id: str,
    jellyfin_item_id: str,
    artist_name: str,
    track_name: str,
    consecutive_skips: int,
) -> Optional[str]:
    """
    Check if a track should enter cooldown after a skip event.
    Called by the webhook handler after updating SkipPenalty.

    Returns:
      "triggered"  — new cooldown applied
      "extended"   — existing cooldown extended (already on cooldown)
      "permanent"  — track marked as permanent dislike
      None         — no cooldown action taken
    """
    from models import TrackCooldown

    if consecutive_skips < COOLDOWN_SKIP_STREAK_THRESHOLD:
        return None

    now = datetime.utcnow()

    # Check for existing active cooldown
    existing = db.query(TrackCooldown).filter_by(
        user_id=user_id,
        jellyfin_item_id=jellyfin_item_id,
    ).filter(
        TrackCooldown.status.in_(["active", "permanent"])
    ).first()

    if existing and existing.status == "permanent":
        return "permanent"   # already perma-penalised, nothing to do

    if existing and existing.status == "active":
        # Already on cooldown — don't reset the clock, but log it
        log.debug(
            f"  Cooldown: '{track_name}' already on cooldown for user "
            f"{user_id[:8]} until {existing.cooldown_until}"
        )
        return "extended"

    # Find previous cooldown count for this track
    all_prev = db.query(TrackCooldown).filter_by(
        user_id=user_id,
        jellyfin_item_id=jellyfin_item_id,
    ).all()
    cooldown_count = len(all_prev) + 1

    if cooldown_count > COOLDOWN_CYCLES_PERMANENT:
        # Too many cooldown cycles — mark as permanent dislike
        if all_prev:
            latest = sorted(all_prev, key=lambda r: r.created_at)[-1]
            latest.status = "permanent"
            latest.expired_at = now
            latest.updated_at = now
        else:
            db.add(TrackCooldown(
                user_id=user_id,
                jellyfin_item_id=jellyfin_item_id,
                artist_name=artist_name,
                track_name=track_name,
                status="permanent",
                cooldown_count=cooldown_count,
                skip_streak_at_trigger=consecutive_skips,
                cooldown_started_at=now,
                cooldown_until=now + timedelta(days=3650),  # effectively forever
                expired_at=now,
            ))
        db.commit()
        log.info(
            f"  Cooldown: '{track_name}' [{user_id[:8]}] → PERMANENT DISLIKE "
            f"after {cooldown_count - 1} cooldown cycles"
        )
        return "permanent"

    # Apply new cooldown
    duration = _cooldown_duration_days(cooldown_count)
    cooldown_until = now + timedelta(days=duration)

    db.add(TrackCooldown(
        user_id=user_id,
        jellyfin_item_id=jellyfin_item_id,
        artist_name=artist_name,
        track_name=track_name,
        status="active",
        cooldown_count=cooldown_count,
        skip_streak_at_trigger=consecutive_skips,
        cooldown_started_at=now,
        cooldown_until=cooldown_until,
    ))

    # Also stamp TrackScore.cooldown_until for fast playlist queries
    from models import TrackScore
    ts = db.query(TrackScore).filter_by(
        user_id=user_id, jellyfin_item_id=jellyfin_item_id
    ).first()
    if ts:
        ts.cooldown_until = cooldown_until
        ts.skip_streak = consecutive_skips

    db.commit()
    log.info(
        f"  Cooldown: '{track_name}' [{user_id[:8]}] → COOLDOWN #{cooldown_count} "
        f"for {duration} days (until {cooldown_until.strftime('%Y-%m-%d')})"
    )
    return "triggered"


def expire_cooldowns(db: Session) -> int:
    """
    Mark expired cooldowns as 'expired' and clear TrackScore.cooldown_until.
    Called at the start of each index run so cooldowns don't linger.
    Returns number of cooldowns expired.
    """
    from models import TrackCooldown, TrackScore

    now = datetime.utcnow()
    active_expired = (
        db.query(TrackCooldown)
        .filter_by(status="active")
        .filter(TrackCooldown.cooldown_until <= now)
        .all()
    )

    count = 0
    for cd in active_expired:
        cd.status = "expired"
        cd.expired_at = now
        cd.updated_at = now

        # Clear the cooldown from TrackScore too
        ts = db.query(TrackScore).filter_by(
            user_id=cd.user_id,
            jellyfin_item_id=cd.jellyfin_item_id,
        ).first()
        if ts:
            ts.cooldown_until = None

        # Reset consecutive_skips so the track gets a fresh start
        from models import SkipPenalty
        sp = db.query(SkipPenalty).filter_by(
            user_id=cd.user_id,
            jellyfin_item_id=cd.jellyfin_item_id,
        ).first()
        if sp:
            sp.consecutive_skips = 0
            sp.updated_at = now

        count += 1

    if count:
        db.commit()
        log.info(f"Cooldowns: expired {count} active cooldowns")

    return count


# ── Replay signal detection ───────────────────────────────────────────────────

def detect_replay_signals(db: Session, user_id: str) -> dict:
    """
    Scan recent PlaybackEvent rows to find voluntary replays.
    A replay is when the user plays a track (or same-artist track) again
    within REPLAY_WINDOW_DAYS of a prior play.

    Called by the indexer after syncing play history for a user.
    Returns stats dict.
    """
    from models import PlaybackEvent, UserReplaySignal, ArtistProfile
    from sqlalchemy import func

    now = datetime.utcnow()
    window_start = now - timedelta(days=REPLAY_WINDOW_DAYS * 2)  # look back 2 windows

    # Load recent play events for this user, ordered by time
    events = (
        db.query(PlaybackEvent)
        .filter_by(user_id=user_id)
        .filter(PlaybackEvent.received_at >= window_start)
        .filter(PlaybackEvent.was_skip == False)
        .order_by(PlaybackEvent.received_at.asc())
        .all()
    )

    if len(events) < 2:
        return {"signals_found": 0}

    signals_found = 0

    # Build index: item_id → list of (received_at, source_context)
    item_plays: dict[str, list] = {}
    artist_plays: dict[str, list] = {}

    for ev in events:
        item_plays.setdefault(ev.jellyfin_item_id, []).append(ev.received_at)
        if ev.artist_name:
            artist_plays.setdefault(ev.artist_name.lower(), []).append(
                (ev.received_at, ev.jellyfin_item_id, ev.artist_name)
            )

    # Check for track replays within window
    for item_id, play_times in item_plays.items():
        if len(play_times) < 2:
            continue
        for i in range(1, len(play_times)):
            days_diff = (play_times[i] - play_times[i - 1]).total_seconds() / 86400
            if 0 < days_diff <= REPLAY_WINDOW_DAYS:
                # Check we haven't already recorded this signal
                exists = db.query(UserReplaySignal).filter_by(
                    user_id=user_id,
                    jellyfin_item_id=item_id,
                    signal_type="track_replay",
                ).filter(
                    UserReplaySignal.replay_at >= play_times[i] - timedelta(minutes=5)
                ).first()
                if not exists:
                    # Find artist name from the event
                    artist_ev = next(
                        (e for e in events if e.jellyfin_item_id == item_id), None
                    )
                    db.add(UserReplaySignal(
                        user_id=user_id,
                        jellyfin_item_id=item_id,
                        artist_name=artist_ev.artist_name if artist_ev else "",
                        signal_type="track_replay",
                        first_play_at=play_times[i - 1],
                        replay_at=play_times[i],
                        days_between=round(days_diff, 2),
                        seed_was_playlist=bool(
                            artist_ev and artist_ev.source_context and
                            "jellydj" in (artist_ev.source_context or "")
                        ),
                        boost_applied=REPLAY_BOOST_TRACK,
                    ))
                    signals_found += 1

    # Check for artist return signals (different track, same artist, within window)
    for artist_key, plays in artist_plays.items():
        if len(plays) < 2:
            continue
        for i in range(1, len(plays)):
            t1_time, t1_id, t1_artist = plays[i - 1]
            t2_time, t2_id, _ = plays[i]
            if t1_id == t2_id:
                continue   # same track — already handled above
            days_diff = (t2_time - t1_time).total_seconds() / 86400
            if 0 < days_diff <= REPLAY_WINDOW_DAYS:
                exists = db.query(UserReplaySignal).filter_by(
                    user_id=user_id,
                    jellyfin_item_id=t2_id,
                    signal_type="artist_return",
                ).filter(
                    UserReplaySignal.replay_at >= t2_time - timedelta(minutes=5)
                ).first()
                if not exists:
                    db.add(UserReplaySignal(
                        user_id=user_id,
                        jellyfin_item_id=t2_id,
                        artist_name=t1_artist,
                        signal_type="artist_return",
                        first_play_at=t1_time,
                        replay_at=t2_time,
                        days_between=round(days_diff, 2),
                        seed_was_playlist=False,
                        boost_applied=REPLAY_BOOST_ARTIST,
                    ))
                    signals_found += 1

    if signals_found:
        db.commit()
        log.info(f"Replay signals: found {signals_found} new signals for user {user_id[:8]}")

    return {"signals_found": signals_found}


def compute_replay_boosts(db: Session, user_id: str) -> dict[str, float]:
    """
    Compute per-track and per-artist replay boost scores for use in TrackScore.

    Returns dict of {jellyfin_item_id: boost_pts} for tracks that have
    active replay signals within the last REPLAY_WINDOW_DAYS.
    Also returns {f"artist:{artist_name_lower}": boost_pts} for artist-level boosts.
    """
    from models import UserReplaySignal

    now = datetime.utcnow()
    cutoff = now - timedelta(days=REPLAY_WINDOW_DAYS)

    recent = (
        db.query(UserReplaySignal)
        .filter_by(user_id=user_id)
        .filter(UserReplaySignal.replay_at >= cutoff)
        .all()
    )

    boosts: dict[str, float] = {}
    for sig in recent:
        # Decay: boost is full at replay, fades over the window
        days_ago = (now - sig.replay_at).total_seconds() / 86400
        decay = max(0.0, 1.0 - (days_ago / REPLAY_WINDOW_DAYS))
        effective_boost = sig.boost_applied * decay

        # Extra boost if the seed was a playlist play (we introduced them to it)
        if sig.seed_was_playlist:
            effective_boost *= 1.5

        if sig.signal_type == "track_replay":
            key = sig.jellyfin_item_id
            boosts[key] = max(boosts.get(key, 0.0), effective_boost)
        elif sig.signal_type in ("artist_return", "same_session_return"):
            key = f"artist:{sig.artist_name.lower()}"
            boosts[key] = max(boosts.get(key, 0.0), effective_boost)

    return boosts


# ── Archival / housekeeping ───────────────────────────────────────────────────

def archive_old_play_events(db: Session) -> int:
    """
    Roll up PlaybackEvent rows older than 90 days into PlayEventSummary.
    Returns number of events archived.
    """
    from models import PlaybackEvent, PlayEventSummary
    from sqlalchemy import func

    cutoff = datetime.utcnow() - timedelta(days=90)

    old_events = (
        db.query(PlaybackEvent)
        .filter(PlaybackEvent.received_at < cutoff)
        .all()
    )

    if not old_events:
        return 0

    # Group by user + item + month
    buckets: dict[tuple, dict] = {}
    for ev in old_events:
        month_key = ev.received_at.strftime("%Y-%m")
        key = (ev.user_id, ev.jellyfin_item_id, month_key)
        if key not in buckets:
            buckets[key] = {
                "artist_name": ev.artist_name,
                "total_plays": 0,
                "total_skips": 0,
                "manual_plays": 0,
                "playlist_plays": 0,
            }
        b = buckets[key]
        b["total_plays"] += 1
        if ev.was_skip:
            b["total_skips"] += 1
        ctx = ev.source_context or ""
        if "jellydj" in ctx:
            b["playlist_plays"] += 1
        else:
            b["manual_plays"] += 1

    # Upsert summaries
    for (uid, jid, month), data in buckets.items():
        existing = db.query(PlayEventSummary).filter_by(
            user_id=uid, jellyfin_item_id=jid, month=month
        ).first()
        if existing:
            existing.total_plays += data["total_plays"]
            existing.total_skips += data["total_skips"]
            existing.manual_plays += data["manual_plays"]
            existing.playlist_plays += data["playlist_plays"]
            existing.updated_at = datetime.utcnow()
        else:
            db.add(PlayEventSummary(
                user_id=uid,
                jellyfin_item_id=jid,
                artist_name=data["artist_name"],
                month=month,
                total_plays=data["total_plays"],
                total_skips=data["total_skips"],
                manual_plays=data["manual_plays"],
                playlist_plays=data["playlist_plays"],
            ))

    # Delete the archived events
    archived = len(old_events)
    for ev in old_events:
        db.delete(ev)

    db.commit()
    log.info(f"Archival: archived {archived} play events into {len(buckets)} summary buckets")
    return archived


# ── Top-level enrichment job ──────────────────────────────────────────────────

def run_enrichment(db: Session, force: bool = False) -> dict:
    """
    Full enrichment pass: tracks + artists + archival.
    Called by the scheduler every 48 hours (configurable).
    """
    log.info("=== Enrichment job starting ===")

    track_result = enrich_tracks(db, force=force)
    time.sleep(1.0)
    artist_result = enrich_artists(db, force=force)
    archived = archive_old_play_events(db)

    # Update AutomationSettings.last_enrichment
    try:
        from models import AutomationSettings
        s = db.query(AutomationSettings).first()
        if s:
            s.last_enrichment = datetime.utcnow()
            db.commit()
    except Exception:
        pass

    log.info("=== Enrichment job complete ===")
    return {
        "tracks": track_result,
        "artists": artist_result,
        "events_archived": archived,
    }
