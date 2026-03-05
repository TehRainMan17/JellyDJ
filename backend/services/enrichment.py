
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
import requests
import re
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
LASTFM_DELAY   = 0.22   # seconds between calls (rate limit: ~4 rps safe)
LASTFM_BASE    = "https://ws.audioscrobbler.com/2.0/"  # direct REST — 1 call/track
MB_DELAY       = 1.1    # seconds between MusicBrainz calls

# ── Enrichment TTL ────────────────────────────────────────────────────────────
TRACK_TTL_DAYS  = 30    # re-enrich tracks after this many days
ARTIST_TTL_DAYS = 14    # re-enrich artists more frequently (trends change)

# ── Batch sizes ──────────────────────────────────────────────────────────────
# Maintenance runs (scheduler, every 6h): process only TTL-expired items.
# 500 tracks/run keeps any library size fresh within a 30-day TTL:
#   - 10,000-track library expires ~83 tracks/6h → 500/run gives 6x headroom.
# Catchup runs (first launch, or manual "Run Now"): no limit, full library pass.
#   - 10,000 tracks takes ~37min at 0.22s/track. Runs in background.
TRACKS_PER_RUN  = 500   # tracks per maintenance run
ARTISTS_PER_RUN = 200   # artists per maintenance run

# If >10% of library is unenriched, auto-switch to catchup (no batch limit).
CATCHUP_THRESHOLD = 0.10

# ── Popularity scoring ────────────────────────────────────────────────────────
# Log-scale Last.fm listeners → 0-100. Reference points:
#   10M listeners → ~95,  1M → ~80,  100K → ~65,  10K → ~50,  1K → ~35
# Popularity scoring — linear spread in log space.
#
# Track listeners and artist listeners live on very different scales:
#   - Artists: 1K (tiny) to 80M (Taylor Swift). Reference ceiling: 10M.
#   - Tracks:  <500 (obscure) to 3M+ (Bohemian Rhapsody). Reference ceiling: 3M.
#
# Using the same formula for both meant tracks always looked low and artists
# always looked high. Separate functions with calibrated floors/ceilings fix this.

# Artist-level calibration: 1K listeners → 0, 10M → 100
ARTIST_LISTENER_FLOOR   = 1_000
ARTIST_LISTENER_CEILING = 10_000_000

# Track-level calibration: 500 listeners → 0, 3M → 100
# This spreads the realistic track range properly:
#   600K → ~82 (popular hit), 60K → ~55 (solid track), 12K → ~37 (deep cut)
TRACK_LISTENER_FLOOR   = 500
TRACK_LISTENER_CEILING = 3_000_000

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


def _listeners_to_score(listeners: Optional[int], floor: int = ARTIST_LISTENER_FLOOR,
                        ceiling: int = ARTIST_LISTENER_CEILING) -> float:
    """Linear-in-log-space listener count → 0-100 popularity score.

    Call with default args for artist-level scoring (floor=1K, ceiling=10M).
    Pass floor=TRACK_LISTENER_FLOOR, ceiling=TRACK_LISTENER_CEILING for tracks.
    """
    if not listeners or listeners <= 0:
        return 0.0
    log_l = math.log(max(listeners, 1))
    log_floor = math.log(floor)
    log_ceil  = math.log(ceiling)
    return round(min(100.0, max(0.0, (log_l - log_floor) / (log_ceil - log_floor) * 100)), 1)


def _track_listeners_to_score(listeners: Optional[int]) -> float:
    """Track-level listener count → 0-100. Calibrated for per-song Last.fm counts."""
    return _listeners_to_score(listeners, floor=TRACK_LISTENER_FLOOR, ceiling=TRACK_LISTENER_CEILING)


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

def _clean_track_name(name: str) -> str:
    """Strip Jellyfin suffixes that prevent Last.fm matching.

    Handles: (Remastered 2011), - 2014 Remaster, (Explicit Version),
    (feat. X), [2024 Mix], (MTV Unplugged), (Live at ...), [Bonus Track], etc.
    Returns the raw name unchanged if it contains no known suffixes.
    """
    # (Remastered YYYY) / (YYYY Remaster) / (Remastered)
    name = re.sub(r'\s*\(\s*(?:\d{4}\s+)?[Rr]emaster(?:ed)?\s*(?:\d{4})?\s*\)', '', name)
    # "- 2014 Remaster" / "- Remastered 2011" / "- Remastered" at end of string
    name = re.sub(r'\s*[-–]\s+(?:\d{4}\s+)?[Rr]emaster(?:ed)?(?:\s+\d{4})?$', '', name)
    # (Live ...) / [Live ...] / "- Live" at end
    name = re.sub(r'\s*\(\s*[Ll]ive[^)]*\)', '', name)
    name = re.sub(r'\s*\[\s*[Ll]ive[^\]]*\]', '', name)
    name = re.sub(r'\s*[-–]\s+[Ll]ive(?:\s+\w+)*$', '', name)
    # [Bonus Track] / (Bonus)
    name = re.sub(r'\s*[\(\[]\s*[Bb]onus[^\)\]]*[\)\]]', '', name)
    # (Explicit Version) / (Clean) / (Radio Edit) / (Single/Album Version) / (Acoustic)
    name = re.sub(r'\s*\(\s*(?:Explicit|Clean|Radio Edit|Single Version|Album Version|Acoustic)(?:\s+Version)?\s*\)', '', name, flags=re.IGNORECASE)
    # (feat. ...) / (ft. ...) / (featuring ...)
    name = re.sub(r'\s*\(\s*(?:feat(?:uring)?|ft)\.?\s[^)]+\)', '', name, flags=re.IGNORECASE)
    # [Mix / Version / Edit / Remix / Acoustic / Demo / Remaster / Unplugged in brackets]
    name = re.sub(r'\s*\[\s*(?:\d{4}\s+)?(?:Mix|Version|Edit|Remix|Acoustic|Demo|Remaster|Unplugged)[^\]]*\]', '', name, flags=re.IGNORECASE)
    # (MTV Unplugged) / (Unplugged ...)
    name = re.sub(r'\s*\(\s*(?:MTV\s+)?Unplugged[^)]*\)', '', name, flags=re.IGNORECASE)
    return name.strip()


def _clean_artist_for_lastfm(artist: str) -> str:
    """
    Strip featured/collaborator suffixes so Last.fm can match the primary artist.

    "Ed Sheeran feat. Khalid"  → "Ed Sheeran"
    "Ed Sheeran & Rudimental"  → "Ed Sheeran"
    "Eminem ft. Ed Sheeran"    → "Eminem"
    "Simon & Garfunkel"        → "Simon & Garfunkel"  (single word before &, preserved)
    "Florence + the Machine"   → "Florence + the Machine"  (single word before +, preserved)
    """
    # feat. / ft. / featuring — most unambiguous collab marker
    artist = re.sub(r'\s+(?:feat(?:uring)?\.?|ft\.?)\s+.+$', '', artist, flags=re.IGNORECASE)
    # parenthetical collab: (feat. X), (ft. X), (with X)
    artist = re.sub(r'\s*\(\s*(?:feat(?:uring)?\.?|ft\.?|with)\s+[^)]+\)', '', artist, flags=re.IGNORECASE)
    # "& X" or "+ X" — only strip when there are already 2+ words before it
    # (preserves "Simon & Garfunkel", "Florence + the Machine")
    parts = re.split(r'\s+[&+]\s+', artist)
    if len(parts) > 1 and len(parts[0].split()) >= 2:
        artist = parts[0]
    return artist.strip()


def _enrich_track_lastfm(net, artist_name: str, track_name: str) -> dict:
    """
    Fetch track data from Last.fm using a single track.getInfo REST call.

    One HTTP request returns: listeners, playcount, url, mbid, toptags.
    This replaces the previous approach of 6 separate pylast lazy-eval calls
    (each triggering its own HTTP round-trip), cutting time from 3-5s to ~0.3s.
    Similar tracks are skipped — not worth an extra call for what we need.
    """
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
    cleaned_name = _clean_track_name(track_name)
    lookup_name = cleaned_name if cleaned_name else track_name
    if lookup_name != track_name:
        log.debug(f"  Cleaned track: {track_name!r} → {lookup_name!r}")

    # Clean the artist name too — "Ed Sheeran feat. Khalid" → "Ed Sheeran"
    lookup_artist = _clean_artist_for_lastfm(artist_name)
    if lookup_artist != artist_name:
        log.debug(f"  Cleaned artist: {artist_name!r} → {lookup_artist!r}")

    # Get the API key from the pylast network object
    try:
        api_key = net.api_key
    except AttributeError:
        api_key = getattr(net, "_api_key", None)
    if not api_key:
        result["source"] = "none"
        return result

    def _getinfo(artist: str, title: str) -> dict:
        try:
            r = requests.get(
                LASTFM_BASE,
                params={
                    "method":      "track.getInfo",
                    "api_key":     api_key,
                    "artist":      artist,
                    "track":       title,
                    "autocorrect": 1,
                    "format":      "json",
                },
                timeout=8,
            )
            if r.status_code == 200:
                return r.json().get("track", {})
        except Exception as e:
            log.debug(f"  track.getInfo request failed: {e}")
        return {}

    data = _getinfo(lookup_artist, lookup_name)

    # If lookup returned nothing, try fallback combinations
    if not data and lookup_name != track_name:
        log.debug(f"  Retrying with original track name: {track_name!r}")
        data = _getinfo(lookup_artist, track_name)
    if not data and lookup_artist != artist_name:
        log.debug(f"  Retrying with original artist: {artist_name!r}")
        data = _getinfo(artist_name, lookup_name)

    if data:
        try:
            # Use explicit None check — "0" is falsy but valid (genuinely 0 listeners)
            listeners_raw = data.get("listeners")
            if listeners_raw is None:
                listeners_raw = (data.get("stats") or {}).get("listeners")
            result["global_listeners"] = int(listeners_raw or 0)
        except Exception:
            pass
        try:
            pc_raw = data.get("playcount")
            if pc_raw is None:
                pc_raw = (data.get("stats") or {}).get("playcount")
            result["global_playcount"] = int(pc_raw or 0)
        except Exception:
            pass
        result["lastfm_url"] = data.get("url")
        result["mbid"]       = data.get("mbid") or None
        try:
            raw_tags = (data.get("toptags") or {}).get("tag", [])
            result["tags"] = json.dumps([
                {"name": t["name"], "count": int(t.get("count", 0))}
                for t in raw_tags[:5]
            ])
        except Exception:
            pass
        result["popularity_score"] = _track_listeners_to_score(result["global_listeners"])
    else:
        log.warning(
            f"  track.getInfo returned nothing for {lookup_artist!r} — {lookup_name!r}"
            + (f" (original artist: {artist_name!r})" if lookup_artist != artist_name else "")
            + (f" (original track: {track_name!r})" if lookup_name != track_name else "")
        )
        result["source"] = "none"

    return result


def enrich_tracks(db: Session, force: bool = False, limit=TRACKS_PER_RUN,
                  progress_callback=None) -> dict:
    """
    Enrich unenriched/stale tracks with Last.fm data.

    limit=None means no limit (catchup mode — process entire library).
    limit=N processes only the next N expired tracks (maintenance mode).

    progress_callback(done, total, current_track, current_artist, enriched, failed)
    is called after each track if provided — use this for live UI progress.
    """
    from models import LibraryTrack, TrackEnrichment
    from sqlalchemy import text as _satext

    net = _get_lastfm_net(db)
    if not net:
        log.info("Enrichment: Last.fm not configured — skipping track enrichment")
        return {"skipped": True, "reason": "lastfm_not_configured"}

    now = datetime.utcnow()

    # Use LEFT JOIN instead of notin_() to avoid SQLite's large IN-clause limit.
    # Selects LibraryTracks that have no fresh TrackEnrichment row (expires_at > now).
    if force:
        q = (
            db.query(LibraryTrack)
            .filter(LibraryTrack.missing_since.is_(None))
            .order_by(LibraryTrack.first_seen.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        tracks = q.all()
    else:
        q = (
            db.query(LibraryTrack)
            .outerjoin(
                TrackEnrichment,
                (LibraryTrack.jellyfin_item_id == TrackEnrichment.jellyfin_item_id) &
                (TrackEnrichment.expires_at.isnot(None)) &
                (TrackEnrichment.expires_at > now)
            )
            .filter(LibraryTrack.missing_since.is_(None))
            .filter(TrackEnrichment.jellyfin_item_id.is_(None))
            .order_by(LibraryTrack.first_seen.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        tracks = q.all()

    if not tracks:
        log.info("Enrichment: all tracks are fresh — nothing to enrich")
        if progress_callback:
            progress_callback(0, 0, "", "", 0, 0)
        return {"enriched": 0, "skipped": 0, "failed": 0}

    total = len(tracks)
    log.info(f"Enrichment: enriching {total} tracks from Last.fm...")
    enriched = failed = 0

    for i, lt in enumerate(tracks):
        if not lt.artist_name or not lt.track_name:
            failed += 1
            if progress_callback:
                progress_callback(i + 1, total, lt.track_name or "?", lt.artist_name or "?", enriched, failed)
            continue

        # Resolve the real track artist — compilation albums store "Various Artists"
        # as artist_name in the DB (from old index runs before the fix). Use the
        # track's own artist field if available, otherwise skip — Last.fm will
        # return nothing for "Various Artists" and we'd score 0 incorrectly.
        _VA = {"various artists", "various", "va", "v.a.", "v/a",
               "multiple artists", "assorted artists", "unknown artist", "unknown"}
        lookup_artist = lt.artist_name
        if lookup_artist.strip().lower() in _VA:
            # Try the album_artist field as a fallback (some tracks store real artist there)
            real = getattr(lt, "album_artist", None) or ""
            if real and real.strip().lower() not in _VA:
                lookup_artist = real.strip()
            else:
                log.debug(f"  Skipping '{lt.track_name}' — artist is '{lt.artist_name}' (compilation catch-all)")
                failed += 1
                if progress_callback:
                    progress_callback(i + 1, total, lt.track_name, lt.artist_name, enriched, failed)
                continue

        if progress_callback:
            progress_callback(i, total, lt.track_name, lookup_artist, enriched, failed)

        data = _enrich_track_lastfm(net, lookup_artist, lt.track_name)
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
            else:
                # Always update artist_name in case it was stored as "Various Artists"
                # before the library_scanner fix corrected LibraryTrack.artist_name
                existing.artist_name = lt.artist_name
                existing.track_name  = lt.track_name

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

            lt.global_playcount = data["global_playcount"]
            lt.global_listeners = data["global_listeners"]
            lt.tags = data["tags"]
            lt.mbid = data["mbid"]
            lt.enriched_at = now
            lt.enrichment_source = data["source"]

            db.flush()   # stage to transaction but don't fsync yet
            enriched += 1

        except Exception as e:
            log.warning(f"  Track enrichment DB write failed for '{lt.track_name}': {e}")
            db.rollback()
            failed += 1

        # Commit every 25 tracks — one fsync per 25 writes instead of per 1.
        # Reduces SSD wear ~25× with no meaningful data-loss risk
        # (worst case: lose the last <25 enrichment rows on crash, re-enriched next run).
        if (enriched + failed) % 25 == 0:
            try:
                db.commit()
            except Exception as e:
                log.warning(f"  Batch commit failed: {e}")
                db.rollback()

        if progress_callback:
            progress_callback(i + 1, total, lt.track_name, lt.artist_name, enriched, failed)

    # Final commit for the last partial batch
    try:
        db.commit()
    except Exception as e:
        log.warning(f"  Final commit failed: {e}")
        db.rollback()

    log.info(f"Track enrichment complete: {enriched} enriched, {failed} failed")
    return {"enriched": enriched, "failed": failed, "total": total}


# ── Artist enrichment ─────────────────────────────────────────────────────────

def _enrich_artist_lastfm(net, artist_name: str, previous_listeners: Optional[int]) -> dict:
    """
    Fetch artist data from Last.fm using a single artist.getInfo REST call.

    artist.getInfo returns in one response: listeners, playcount, url, mbid,
    biography, image, tags, and up to 5 similar artists.
    Replaces the previous 7-call pylast approach.
    """
    result = {
        "mbid": None,
        "lastfm_url": None,
        "image_url": None,
        "global_listeners": None,
        "global_playcount": None,
        "biography": None,
        "tags": None,
        "similar_artists": None,
        "top_tracks": None,
        "popularity_score": None,
        "trend_direction": "stable",
        "trend_pct": 0.0,
        "source": "lastfm",
    }

    try:
        api_key = net.api_key
    except AttributeError:
        api_key = getattr(net, "_api_key", None)
    if not api_key:
        result["source"] = "none"
        return result

    def _lastfm_get(method: str, params: dict) -> dict:
        try:
            r = requests.get(
                LASTFM_BASE,
                params={"method": method, "api_key": api_key, "format": "json", **params},
                timeout=8,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log.debug(f"  {method} request failed: {e}")
        return {}

    info_data = _lastfm_get("artist.getInfo", {"artist": artist_name, "autocorrect": 1})
    data = info_data.get("artist", {})
    if not data:
        log.warning(f"  artist.getInfo returned nothing for {artist_name!r}")
        result["source"] = "none"
        return result

    # Fetch top tracks + resolve which album each belongs to.
    # artist.getTopTracks gives name+listeners but no album field.
    # We call track.getInfo for the top 3 tracks to get their album names —
    # this lets us recommend "the album containing their biggest hit" rather
    # than "their album with the most total plays" (which favours long albums).
    time.sleep(LASTFM_DELAY)
    tracks_data = _lastfm_get("artist.getTopTracks", {"artist": artist_name, "autocorrect": 1, "limit": 10})
    try:
        raw_tracks = tracks_data.get("toptracks", {}).get("track", [])
        enriched_tracks = []
        for i, t in enumerate(raw_tracks[:10]):
            if not t.get("name"):
                continue
            track_entry = {
                "name":      t["name"],
                "listeners": int(t.get("listeners", 0) or 0),
                "rank":      int((t.get("@attr") or {}).get("rank", i + 1)),
                "album":     None,
            }
            # Resolve album for top 3 tracks via track.getInfo
            if i < 3:
                time.sleep(LASTFM_DELAY)
                ti = _lastfm_get("track.getInfo", {
                    "artist": artist_name,
                    "track":  t["name"],
                    "autocorrect": 1,
                })
                track_data = ti.get("track", {})
                album_title = (track_data.get("album") or {}).get("title")
                if album_title:
                    track_entry["album"] = album_title
            enriched_tracks.append(track_entry)
        result["top_tracks"] = json.dumps(enriched_tracks)
    except Exception as e:
        log.debug(f"  top tracks fetch failed for {artist_name!r}: {e}")

    if not data:
        log.warning(f"  artist.getInfo returned nothing for {artist_name!r}")
        result["source"] = "none"
        return result

    # Core stats
    stats = data.get("stats", {})
    try:
        result["global_listeners"] = int(stats.get("listeners") or 0)
    except Exception:
        pass
    try:
        result["global_playcount"] = int(stats.get("playcount") or 0)
    except Exception:
        pass

    result["lastfm_url"] = data.get("url")
    result["mbid"]       = data.get("mbid") or None

    # Biography (strip trailing "Read more on Last.fm" link noise)
    bio_raw = (data.get("bio") or {}).get("summary", "") or ""
    bio_clean = bio_raw.split('<a href="https://www.last.fm')[0].strip()
    result["biography"] = bio_clean[:500] if bio_clean else None

    # Image — take the largest available
    images = data.get("image", [])
    for img in reversed(images):
        url = img.get("#text", "")
        if url and not url.endswith("2a96cbd8b46e442fc41c2b86b821562f.png"):  # skip default placeholder
            result["image_url"] = url
            break

    # Tags (up to 5)
    try:
        raw_tags = (data.get("tags") or {}).get("tag", [])
        result["tags"] = json.dumps([t["name"] for t in raw_tags[:5]])
    except Exception:
        pass

    # Similar artists embedded in getInfo (up to 5 — enough for recommendations)
    try:
        similar_raw = (data.get("similar") or {}).get("artist", [])
        result["similar_artists"] = json.dumps([
            {"name": s["name"], "match": 1.0}   # getInfo doesn't include match score
            for s in similar_raw[:5]
            if s.get("name")
        ])
    except Exception:
        pass

    # Popularity + trend
    result["popularity_score"] = _listeners_to_score(result["global_listeners"])
    cur = result["global_listeners"]
    if cur and previous_listeners and previous_listeners > 0:
        pct_change = (cur - previous_listeners) / previous_listeners * 100
        result["trend_pct"] = round(pct_change, 1)
        result["trend_direction"] = (
            "rising"  if pct_change >  5 else
            "falling" if pct_change < -5 else
            "stable"
        )

    return result


def enrich_artists(db: Session, force: bool = False, limit=ARTISTS_PER_RUN,
                   progress_callback=None) -> dict:
    """
    Enrich the next batch of unenriched/stale artists with Last.fm data.
    Also populates/updates ArtistRelation rows from similar_artists.

    progress_callback(done, total, current_artist, enriched, failed) is called
    after each artist if provided.
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
    ]
    if limit is not None:
        to_enrich = to_enrich[:limit]

    if not to_enrich:
        log.info("Enrichment: all artists are fresh")
        return {"enriched": 0, "failed": 0}

    total_artists = len(to_enrich)
    log.info(f"Enrichment: enriching {total_artists} artists from Last.fm...")
    enriched = failed = relations_added = 0

    for i, artist_name in enumerate(to_enrich):
        if progress_callback:
            progress_callback(i, total_artists, artist_name, enriched, failed)
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
            existing.top_tracks = data.get("top_tracks")
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

            db.flush()
            enriched += 1

        except Exception as e:
            log.warning(f"  Artist enrichment DB write failed for '{artist_name}': {e}")
            db.rollback()
            failed += 1

        if (enriched + failed) % 25 == 0:
            try:
                db.commit()
            except Exception as e:
                log.warning(f"  Artist batch commit failed: {e}")
                db.rollback()

        if progress_callback:
            progress_callback(i + 1, total_artists, artist_name, enriched, failed)

    try:
        db.commit()
    except Exception as e:
        log.warning(f"  Artist final commit failed: {e}")
        db.rollback()

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
                # Dedup: check for a signal already written for this exact replay
                # timestamp (within 60 seconds). Use a tight window so rapid
                # consecutive plays (e.g. same song on repeat) each get their own
                # signal rather than being eaten by the previous one.
                exists = db.query(UserReplaySignal).filter_by(
                    user_id=user_id,
                    jellyfin_item_id=item_id,
                    signal_type="track_replay",
                ).filter(
                    UserReplaySignal.replay_at >= play_times[i] - timedelta(seconds=60),
                    UserReplaySignal.replay_at <= play_times[i] + timedelta(seconds=60),
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
                    UserReplaySignal.replay_at >= t2_time - timedelta(seconds=60),
                    UserReplaySignal.replay_at <= t2_time + timedelta(seconds=60),
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

    Multiple signals for the same track are summed (not maxed) so that
    genuinely obsessive repeat-plays accumulate a stronger boost. The
    scoring engine caps the final contribution at REPLAY_BOOST_CAP.
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
            # Sum signals: 3× replays should feel stronger than 1×.
            # The scoring engine caps the final contribution at REPLAY_BOOST_CAP.
            boosts[key] = boosts.get(key, 0.0) + effective_boost
        elif sig.signal_type in ("artist_return", "same_session_return"):
            key = f"artist:{sig.artist_name.lower()}"
            boosts[key] = boosts.get(key, 0.0) + effective_boost

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
    Smart enrichment dispatcher: auto-detects first-launch catchup vs maintenance.

    Catchup mode  — triggered when >CATCHUP_THRESHOLD of library is unenriched.
      No batch limit. Processes the entire library in one background pass.
      A 10,000-track library takes ~37min at 0.22s/track.

    Maintenance mode — normal scheduler runs.
      Processes up to TRACKS_PER_RUN expired tracks and ARTISTS_PER_RUN expired
      artists. 500 tracks every 6h keeps any library size fully current.
    """
    from models import LibraryTrack, TrackEnrichment

    now = datetime.utcnow()

    # Count how many library tracks have no fresh enrichment
    total_tracks = (
        db.query(LibraryTrack)
        .filter(LibraryTrack.missing_since.is_(None))
        .count()
    )
    fresh_count = (
        db.query(TrackEnrichment)
        .filter(TrackEnrichment.expires_at > now)
        .count()
    )
    unenriched_fraction = 1.0 - (fresh_count / total_tracks) if total_tracks > 0 else 1.0

    if force or unenriched_fraction > CATCHUP_THRESHOLD:
        mode = "catchup"
        track_limit = None   # no limit — process everything
        artist_limit = None
        log.info(
            f"Enrichment: CATCHUP mode "
            f"({fresh_count}/{total_tracks} tracks fresh, {unenriched_fraction*100:.0f}% unenriched)"
        )
    else:
        mode = "maintenance"
        track_limit = TRACKS_PER_RUN
        artist_limit = ARTISTS_PER_RUN
        log.info(
            f"Enrichment: MAINTENANCE mode "
            f"({fresh_count}/{total_tracks} tracks fresh — processing up to "
            f"{track_limit} expired tracks)"
        )

    track_result  = enrich_tracks(db, force=force, limit=track_limit)
    artist_result = enrich_artists(db, force=force, limit=artist_limit)

    return {
        "mode": mode,
        "tracks":  track_result,
        "artists": artist_result,
    }


def run_enrichment_legacy(db: Session, force: bool = False) -> dict:
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
