"""
JellyDJ Playlist Writer — Module 7

Generates playlists from the recommender engine and writes them to Jellyfin.
Each managed user gets their own named playlist visible to all Jellyfin users.

Playlist types:
  for_you         — "For You - Alice"          affinity-weighted
  discover        — "New For You - Alice"  novelty-heavy, NEW-artist-first
  most_played     — "Most Played - Alice"      sorted by play count
  recently_played — "Recently Played - Alice"  sorted by last_played desc

Flow per playlist:
  1. Generate track list from recommender (or direct DB query for most/recently played)
  2. Look up existing Jellyfin playlist by name
  3. If exists → clear all items, re-add new list (overwrite)
  4. If not exists → create new playlist, add items
  5. Record in PlaylistRun table for history/dashboard
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from models import (
    ConnectionSettings, ManagedUser, Play,
    PlaylistRun, PlaylistRunItem, TrackScore, ExcludedAlbum, LibraryTrack,
)
from services.events import log_event
from crypto import decrypt

from sqlalchemy import or_ as _sa_or_


def _holiday_ok():
    """
    SQLAlchemy filter: exclude tracks that are definitively marked as
    out-of-season holiday content.

    IMPORTANT: We query LibraryTrack.holiday_exclude directly via a subquery
    rather than relying on the denormalized TrackScore.holiday_exclude field.
    The reason: holiday season windows are re-evaluated daily by
    holiday.refresh_exclude_flags(), which updates LibraryTrack but does NOT
    update TrackScore (that only happens on a full score rebuild, which runs
    every 6 hours).  Relying on TrackScore means holiday tracks can leak into
    playlists for up to 6 hours after a season ends/begins.

    Reading from LibraryTrack guarantees we always use the current season
    state with zero stale-data risk.
    """
    from sqlalchemy import and_ as _sa_and_

    # Subquery: IDs of tracks that are currently out-of-season holiday content
    holiday_excluded_ids = (
        LibraryTrack.__table__.select()
        .with_only_columns(LibraryTrack.__table__.c.jellyfin_item_id)
        .where(
            _sa_and_(
                LibraryTrack.__table__.c.holiday_tag.isnot(None),
                LibraryTrack.__table__.c.holiday_exclude == True,  # noqa: E712
            )
        )
    )
    return TrackScore.jellyfin_item_id.notin_(holiday_excluded_ids)


def _get_excluded_item_ids(db) -> frozenset:
    """
    Return a frozenset of jellyfin_item_ids whose album has been manually excluded.

    Matching strategy (two passes, unioned):

    Pass 1 — jellyfin_album_id (exact, reliable):
      LibraryTrack.jellyfin_album_id == ExcludedAlbum.jellyfin_album_id
      This is the canonical match. Jellyfin's AlbumId is a stable UUID that
      never changes regardless of how the album_name tag is written in the
      audio file. Works for virtual albums like "Other" where every track has
      a different album_name tag but the same AlbumId container.
      Requires library_scanner v5+ (AlbumId stored during scan).

    Pass 2 — album_name LOWER() match (fallback for pre-v5 rows):
      Matches ExcludedAlbum.album_name → LibraryTrack.album_name case-insensitively.
      Catches tracks scanned before jellyfin_album_id was introduced (i.e. before
      the user runs their first library scan after this update).
      Also catches TrackScore rows via a second sub-pass.

    The union of both passes is returned. After the first post-update library
    scan, Pass 1 alone will handle everything correctly.
    """
    try:
        from sqlalchemy import func as _func

        excl_rows = db.query(ExcludedAlbum).all()
        if not excl_rows:
            return frozenset()

        excl_album_ids = [r.jellyfin_album_id for r in excl_rows if r.jellyfin_album_id]
        excl_names_lower = [r.album_name.lower() for r in excl_rows if r.album_name]

        result: set[str] = set()

        # Pass 1: match via jellyfin_album_id (exact, most reliable)
        if excl_album_ids:
            id_rows = db.query(LibraryTrack.jellyfin_item_id).filter(
                LibraryTrack.jellyfin_album_id.in_(excl_album_ids),
                LibraryTrack.missing_since.is_(None),
            ).all()
            for r in id_rows:
                result.add(r.jellyfin_item_id)

        # Pass 2: match via album_name (fallback for tracks not yet rescanned)
        if excl_names_lower:
            name_rows = db.query(LibraryTrack.jellyfin_item_id).filter(
                _func.lower(LibraryTrack.album_name).in_(excl_names_lower),
                LibraryTrack.missing_since.is_(None),
            ).all()
            for r in name_rows:
                result.add(r.jellyfin_item_id)

            # Also catch via TrackScore.album_name for any score rows
            # whose LibraryTrack row may have been updated with a different name
            ts_rows = db.query(TrackScore.jellyfin_item_id).filter(
                _func.lower(TrackScore.album_name).in_(excl_names_lower),
            ).all()
            for r in ts_rows:
                result.add(r.jellyfin_item_id)

        frozen = frozenset(result)
        log.debug(
            f"Excluded album filter: {len(excl_rows)} excluded album(s) → "
            f"{len(frozen)} track IDs blocked"
        )
        if excl_rows and not frozen:
            log.warning(
                f"Excluded album filter matched 0 track IDs! "
                f"Excluded album IDs: {excl_album_ids} | "
                f"Excluded album names: {excl_names_lower[:5]} — "
                f"Run a library scan to populate jellyfin_album_id on existing tracks."
            )
        return frozen
    except Exception as _e:
        log.warning(f"Failed to load excluded album item IDs: {_e}")
        return frozenset()


log = logging.getLogger(__name__)

# Track counts per playlist type.
# Kept intentionally different so each playlist feels sized appropriately —
# "Most Played" at 50 gives a decent listening session; "New For You"
# at 40 keeps the unfamiliar content digestible.
# Playlist names in Jellyfin follow the pattern "<Label> - <Username>",
# e.g. "For You - Alice". Jellyfin shows these to all users on the server
# so family members can see each other's personalised playlists.
PLAYLIST_SIZES = {
    "for_you":          50,
    "discover":         40,
    "most_played":      50,
    "recently_played":  40,
}

PLAYLIST_LABELS = {
    "for_you":          "For You",
    "discover":         "New For You",
    "most_played":      "Most Played",
    "recently_played":  "Recently Played",
}


def _jellyfin_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


def _playlist_name(playlist_type: str, username: str) -> str:
    label = PLAYLIST_LABELS.get(playlist_type, playlist_type.replace("_", " ").title())
    return f"{label} - {username}"


# ── Track selection ───────────────────────────────────────────────────────────

def _diversify(
    rows: list,
    limit: int,
    id_field: str = "jellyfin_item_id",
    artist_field: str = "artist_name",
    max_per_artist: int = 3,
    relax_to: int = 5,
) -> list[str]:
    """
    Apply a per-artist track cap to prevent any single artist from dominating
    a playlist. Works in two passes:

    Pass 1: Allow at most max_per_artist tracks per artist. If this fills
            the playlist, we're done.
    Pass 2: If Pass 1 couldn't fill the list (user has a very narrow library),
            relax the cap to relax_to and try again.

    This is intentionally a greedy algorithm — it takes the highest-scored
    tracks first and only worries about the cap per artist, not global
    diversity. More sophisticated approaches (e.g. MMR) weren't necessary
    in testing because the score jitter in _jitter() already creates variety.

    Returns a list of jellyfin_item_ids in score-descending order.
    """
    def _pick(rows, cap):
        counts: dict[str, int] = {}
        picked = []
        for row in rows:
            artist = getattr(row, artist_field, "") or ""
            key = artist.lower()
            if counts.get(key, 0) < cap:
                picked.append(getattr(row, id_field))
                counts[key] = counts.get(key, 0) + 1
            if len(picked) >= limit:
                break
        return picked

    result = _pick(rows, max_per_artist)
    if len(result) < limit:
        result = _pick(rows, relax_to)
    return result


def _build_artist_play_totals(user_id: str, db: Session) -> dict[str, int]:
    """
    Return a dict of {artist_name_lower: total_play_count} for the user.

    This is the key signal for New For You: we need to know which artists
    the user has NEVER played (strangers), barely played (acquaintances), and
    plays heavily (familiar). We derive this from the ArtistProfile table which
    the scoring engine builds on every index run.

    Falls back to summing TrackScore.play_count per artist if ArtistProfile
    is empty (e.g. first run before scoring).
    """
    from models import ArtistProfile
    rows = db.query(ArtistProfile).filter_by(user_id=user_id).all()
    if rows:
        return {r.artist_name.lower(): r.total_plays for r in rows}

    # Fallback: aggregate from TrackScore
    from sqlalchemy import func
    results = (
        db.query(TrackScore.artist_name, func.sum(TrackScore.play_count))
        .filter_by(user_id=user_id)
        .group_by(TrackScore.artist_name)
        .all()
    )
    return {artist.lower(): total for artist, total in results if artist}


def _get_artist_popularity(artist_name: str, db: Session) -> float:
    """
    Pull cached popularity score for an artist (0–100).
    Uses the PopularityCache keyed as 'artist:{name_lower}'.
    Returns a neutral 50.0 if nothing is cached — never blocks.
    """
    from models import PopularityCache
    key = f"artist:{artist_name.lower()}"
    row = db.query(PopularityCache).filter_by(cache_key=key).first()
    if not row:
        return 50.0
    try:
        data = json.loads(row.payload)
        return float(data.get("popularity_score", 50.0))
    except Exception:
        return 50.0


def _get_tracks_for_playlist(
    playlist_type: str,
    user_id: str,
    username: str,
    db: Session,
) -> list[str]:
    """
    Return a list of Jellyfin item IDs for the given playlist type.

    Variety mechanisms:
    - for_you:   20% reserved discovery slots (unplayed from loved artists) +
                 10% deep cuts (high-affinity tracks not heard in 1+ month) +
                 mid-tier score jitter on the remaining 70%
    - discover:  NEW-ARTIST-FIRST algorithm (see below)
    - most_played / recently_played: stable (intentionally deterministic)

    New For You algorithm (completely reworked):
    ─────────────────────────────────────────────────
    Goal: Surface the absolute best songs from artists the user has never
    or barely played, filtered by genre/taste compatibility, sorted by
    global popularity so we always lead with genuine hits — not deep cuts.

    Artist familiarity tiers (based on ArtistProfile.total_plays):
      Stranger    — 0 total plays  → 60% of playlist
      Acquaintance — 1–9 plays     → 25% of playlist
      Familiar     — 10+ plays, unplayed tracks → 15% of playlist (safety net)

    Sorting within each tier:
      Stranger/Acquaintance: genre_affinity (taste match) × artist_popularity
        This finds "sounds like what you love" artists ranked by how well-known
        they are, so we serve their biggest hits rather than obscure B-sides.
      Familiar: genre_affinity DESC (still unplayed, just from known artists)

    Per-artist cap:
      New For You uses max_per_artist=1 (one song per artist, period).
      This forces maximum breadth. If the pool runs dry we relax to 2.

    Popularity bonus:
      Each track gets a discover_score = genre_affinity * 0.4 + popularity * 0.6
      (popularity from PopularityCache for the artist, 0–100).
      This means a genre-matched artist with 80 popularity scores above a
      perfect-genre-match artist with 20 popularity — we always lead with hits.
    """
    from sqlalchemy import text as satext

    limit = PLAYLIST_SIZES.get(playlist_type, 50)

    # v4: load excluded album item IDs once — frozenset of jellyfin_item_ids
    _excl_item_ids = _get_excluded_item_ids(db)

    def _album_ok():
        """Exclude tracks belonging to manually excluded albums.
        Uses a pre-loaded frozenset of item IDs — no subquery, no case issues."""
        from sqlalchemy import literal
        if not _excl_item_ids:
            return literal(True)   # nothing excluded — pass everything through
        return TrackScore.jellyfin_item_id.notin_(_excl_item_ids)

    score_count = db.query(TrackScore).filter_by(user_id=user_id).count()

    if score_count == 0:
        log.warning(f"  No TrackScores for {username} — falling back to plays table")
        # Exclude out-of-season holiday tracks even in the fallback path
        excluded_ids = {
            r.jellyfin_item_id
            for r in db.query(LibraryTrack.jellyfin_item_id)
            .filter(
                LibraryTrack.holiday_tag.isnot(None),
                LibraryTrack.holiday_exclude == True,
            )
            .all()
        }
        rows = (
            db.query(Play.jellyfin_item_id)
            .filter_by(user_id=user_id)
            .filter(Play.play_count > 0)
            .order_by(Play.play_count.desc())
            .limit(limit * 4)
            .all()
        )
        all_excluded = excluded_ids | _excl_item_ids
        return [r.jellyfin_item_id for r in rows if r.jellyfin_item_id not in all_excluded][:limit]


    # ── Deterministic types — no jitter ──────────────────────────────────────

    if playlist_type == "most_played":
        rows = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(TrackScore.is_played == True)
            .filter(_holiday_ok())
            .filter(_album_ok())
            .order_by(TrackScore.play_count.desc())
            .limit(limit * 6)
            .all()
        )
        return _diversify(rows, limit, max_per_artist=5, relax_to=8)

    if playlist_type == "recently_played":
        rows = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(
                TrackScore.is_played == True,
                TrackScore.last_played.isnot(None),
            )
            .filter(_holiday_ok())
            .filter(_album_ok())
            .order_by(TrackScore.last_played.desc())
            .limit(limit * 4)
            .all()
        )
        return _diversify(rows, limit, max_per_artist=4, relax_to=6)

    # ── Helper: apply mid-tier score jitter ──────────────────────────────────
    def _jitter(rows: list, top_threshold: float = 75.0, jitter_pct: float = 0.15) -> list:
        """
        Jitter scores for mid-tier tracks (below top_threshold) by ±jitter_pct.
        Top-tier tracks stay sorted stably. Returns re-sorted list.
        """
        top = [r for r in rows if float(r.final_score) >= top_threshold]
        mid = [r for r in rows if float(r.final_score) < top_threshold]
        for r in mid:
            score = float(r.final_score)
            jitter = random.uniform(-jitter_pct, jitter_pct) * score
            r._jittered = score + jitter
        mid.sort(key=lambda r: getattr(r, "_jittered", float(r.final_score)), reverse=True)
        return top + mid

    # ── for_you: stable top 20% + discovery slots + deep cuts + jittered mid ─

    if playlist_type == "for_you":
        n_discovery = int(limit * 0.20)   # 20% unplayed from loved artists
        n_deep_cuts = int(limit * 0.10)   # 10% forgotten favorites (6+ months)
        n_core      = limit - n_discovery - n_deep_cuts  # 70% core scored tracks

        # Core: all tracks, jittered
        core_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(_holiday_ok())
            .filter(_album_ok())
            .order_by(satext("CAST(final_score AS REAL) DESC"))
            .limit(n_core * 8)
            .all()
        )
        core_pool = _jitter(core_pool)
        core_ids = _diversify(core_pool, n_core)
        core_set = set(core_ids)

        # Discovery: unplayed tracks not already in core
        discovery_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(TrackScore.is_played == False)
            .filter(_holiday_ok())
            .filter(_album_ok())
            .order_by(satext("CAST(final_score AS REAL) DESC"))
            .limit(n_discovery * 8)
            .all()
        )
        discovery_pool = [r for r in discovery_pool if r.jellyfin_item_id not in core_set]
        discovery_pool = _jitter(discovery_pool)
        discovery_ids = _diversify(discovery_pool, n_discovery)
        combined_set = core_set | set(discovery_ids)

        # Deep cuts: high affinity, played, not heard in 1+ month
        cutoff = datetime.utcnow() - timedelta(days=30)
        deep_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(
                TrackScore.is_played == True,
                TrackScore.last_played < cutoff,
            )
            .filter(_holiday_ok())
            .filter(_album_ok())
            .order_by(satext("CAST(artist_affinity AS REAL) DESC"))
            .limit(n_deep_cuts * 8)
            .all()
        )
        deep_pool = [r for r in deep_pool if r.jellyfin_item_id not in combined_set]
        random.shuffle(deep_pool)
        deep_ids = _diversify(deep_pool, n_deep_cuts)

        result = core_ids + discovery_ids + deep_ids
        random.shuffle(result)
        return result

    # ── discover: new-artist-first, popularity-sorted ────────────────────────

    if playlist_type == "discover":
        #
        # DISCOVER WEEKLY — complete rework
        #
        # The old algorithm queried unplayed tracks sorted by final_score, which
        # bakes in artist_affinity. That meant "most loved artist's unplayed B-sides"
        # won every time — the opposite of discovery.
        #
        # New approach:
        #   1. Pull ALL unplayed tracks + their genre_affinity
        #   2. Bucket by artist familiarity (stranger / acquaintance / familiar)
        #   3. Within each bucket, rank by discover_score:
        #        discover_score = genre_affinity * 0.4 + artist_popularity * 0.6
        #      Genre affinity keeps it on-taste; popularity ensures we serve
        #      genuine hits rather than obscure album cuts.
        #   4. Blend pools: 60% strangers, 25% acquaintances, 15% familiar-unplayed
        #   5. max_per_artist=1 for maximum breadth (relax to 2 if pool is thin)
        #

        # Familiarity thresholds
        STRANGER_MAX_PLAYS    = 0    # never played a single track by this artist
        ACQUAINTANCE_MAX_PLAYS = 9   # played 1–9 tracks total by this artist

        # Pool size targets
        n_stranger      = int(limit * 0.60)   # 60%  — pure new artists
        n_acquaintance  = int(limit * 0.25)   # 25%  — lightly heard artists
        n_familiar      = limit - n_stranger - n_acquaintance  # 15% safety net

        # Build artist familiarity map for this user
        artist_plays = _build_artist_play_totals(user_id, db)

        # Fetch all unplayed tracks (broad pool — we'll sort in Python)
        # We need genre_affinity to compute discover_score so we can't
        # just ORDER BY in SQL without also sorting by popularity.
        fetch_limit = max(limit * 20, 400)
        all_unplayed = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(_holiday_ok())
            .filter(_album_ok())
            .filter(
                TrackScore.is_played == False,
                TrackScore.auto_skip == False if hasattr(TrackScore, 'auto_skip') else True,
            )
            .limit(fetch_limit)
            .all()
        )

        # Also pull tracks from acquaintance artists that haven't been played recently
        # (a different slice: played artist, but not this specific track)
        all_played_artist_unplayed_tracks = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(_holiday_ok())
            .filter(_album_ok())
            .filter(
                TrackScore.is_played == False,
            )
            .limit(fetch_limit)
            .all()
        )
        # Merge and deduplicate (all_unplayed is a subset if is_played covers it)
        seen_ids: set[str] = set()
        candidate_pool: list = []
        for row in all_unplayed + all_played_artist_unplayed_tracks:
            if row.jellyfin_item_id not in seen_ids:
                seen_ids.add(row.jellyfin_item_id)
                candidate_pool.append(row)

        # Build popularity cache lookup (batch: avoid N+1 queries)
        artist_names_needed = {(r.artist_name or "").lower() for r in candidate_pool}
        popularity_cache: dict[str, float] = {}
        for aname in artist_names_needed:
            popularity_cache[aname] = _get_artist_popularity(aname, db)

        # Score each track for discover purposes
        def _discover_score(row) -> float:
            """
            discover_score = genre_affinity * 0.4 + artist_popularity * 0.6

            We use genre_affinity (not artist_affinity) because:
            - genre_affinity tells us "does this fit your taste in music"
            - artist_affinity tells us "do you already love this artist" — wrong
              for a playlist trying to introduce NEW artists

            Artist popularity (0–100 from PopularityCache) is weighted at 60%
            so we surface genuine hits first, not deep cuts from unknown artists.
            A track with genre_affinity=80 and popularity=20 scores 44.
            A track with genre_affinity=60 and popularity=80 scores 72.
            The hit wins — intentionally.
            """
            genre_aff = float(row.genre_affinity or 0)
            artist_key = (row.artist_name or "").lower()
            pop = popularity_cache.get(artist_key, 50.0)
            return genre_aff * 0.4 + pop * 0.6

        # Bucket by artist familiarity
        strangers: list      = []   # artist total plays == 0
        acquaintances: list  = []   # artist total plays 1–9
        familiar: list       = []   # artist total plays 10+, track unplayed

        for row in candidate_pool:
            artist_key = (row.artist_name or "").lower()
            total = artist_plays.get(artist_key, 0)
            row._discover_score = _discover_score(row)

            if total <= STRANGER_MAX_PLAYS:
                strangers.append(row)
            elif total <= ACQUAINTANCE_MAX_PLAYS:
                acquaintances.append(row)
            else:
                familiar.append(row)

        # Sort each bucket by discover_score DESC — hits first
        strangers.sort(key=lambda r: r._discover_score, reverse=True)
        acquaintances.sort(key=lambda r: r._discover_score, reverse=True)
        familiar.sort(key=lambda r: r._discover_score, reverse=True)

        log.info(
            f"  New For You [{username}]: "
            f"{len(strangers)} stranger tracks, "
            f"{len(acquaintances)} acquaintance tracks, "
            f"{len(familiar)} familiar-unplayed tracks"
        )

        # Pick from each bucket with tight per-artist cap (max 1 song per artist)
        # This maximises breadth — one hit from each new artist rather than
        # 3 songs from the same "new to you" artist.
        used_ids: set[str] = set()

        def _pick_bucket(rows: list, n: int, max_pa: int = 1) -> list[str]:
            counts: dict[str, int] = {}
            picked = []
            for row in rows:
                if row.jellyfin_item_id in used_ids:
                    continue
                key = (row.artist_name or "").lower()
                if counts.get(key, 0) < max_pa:
                    picked.append(row.jellyfin_item_id)
                    counts[key] = counts.get(key, 0) + 1
                    used_ids.add(row.jellyfin_item_id)
                if len(picked) >= n:
                    break
            return picked

        stranger_ids     = _pick_bucket(strangers,     n_stranger,     max_pa=1)
        acquaintance_ids = _pick_bucket(acquaintances, n_acquaintance, max_pa=1)
        familiar_ids     = _pick_bucket(familiar,      n_familiar,     max_pa=2)

        # If a bucket ran dry, backfill from the others (still strict cap)
        shortage = limit - len(stranger_ids) - len(acquaintance_ids) - len(familiar_ids)
        if shortage > 0:
            log.info(f"  New For You [{username}]: backfilling {shortage} slots from remaining pools")
            remaining = [
                r for r in (strangers + acquaintances + familiar)
                if r.jellyfin_item_id not in used_ids
            ]
            remaining.sort(key=lambda r: r._discover_score, reverse=True)
            backfill = _pick_bucket(remaining, shortage, max_pa=2)
            familiar_ids.extend(backfill)

        result = stranger_ids + acquaintance_ids + familiar_ids
        log.info(
            f"  New For You [{username}]: "
            f"{len(stranger_ids)} strangers + {len(acquaintance_ids)} acquaintances + "
            f"{len(familiar_ids)} familiar = {len(result)} total"
        )
        # Shuffle within tiers so the playlist doesn't open with a block of one style
        random.shuffle(result)
        return result

    # Fallback — applies both holiday and user-exclusion filters
    rows = (
        db.query(TrackScore)
        .filter_by(user_id=user_id)
        .filter(_holiday_ok())
        .filter(_album_ok())
        .order_by(satext("CAST(final_score AS REAL) DESC"))
        .limit(limit)
        .all()
    )
    return [r.jellyfin_item_id for r in rows]


# ── Jellyfin API helpers ──────────────────────────────────────────────────────

async def _find_playlist(
    base_url: str, api_key: str, name: str, admin_user_id: str
) -> Optional[str]:
    """Return the Jellyfin playlist ID if it exists, else None."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{admin_user_id}/Items",
            headers=headers,
            params={
                "IncludeItemTypes": "Playlist",
                "Recursive": "true",
                "SearchTerm": name,
            },
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("Items", [])
        # Exact name match
        match = next((i for i in items if i.get("Name") == name), None)
        return match["Id"] if match else None


async def _create_playlist(
    base_url: str, api_key: str, name: str, admin_user_id: str,
    item_ids: list[str],
) -> Optional[str]:
    """Create a new Jellyfin playlist and return its ID."""
    headers = {"X-Emby-Token": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base_url}/Playlists",
            headers=headers,
            json={
                "Name": name,
                "Ids": item_ids,
                "UserId": admin_user_id,
                "MediaType": "Audio",
            },
        )
        if resp.status_code not in (200, 201):
            log.error(f"Create playlist failed: {resp.status_code} — {resp.text[:300]}")
            return None
        return resp.json().get("Id")


async def _clear_playlist(
    base_url: str, api_key: str, playlist_id: str, admin_user_id: str = "",
) -> bool:
    """
    Remove all items from an existing playlist.
    Tries multiple strategies to handle different Jellyfin versions.
    """
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Strategy 1: fetch items with UserId param (required by some Jellyfin versions)
        params: dict = {}
        if admin_user_id:
            params["UserId"] = admin_user_id

        resp = await client.get(
            f"{base_url}/Playlists/{playlist_id}/Items",
            headers=headers,
            params=params,
        )
        log.info(f"  GET playlist items: HTTP {resp.status_code} playlist_id={playlist_id}")

        if resp.status_code != 200:
            log.warning(f"  GET playlist items failed: {resp.status_code} — {resp.text[:300]}")
            return False

        data = resp.json()
        items = data.get("Items", [])
        log.info(f"  {len(items)} items in playlist")
        if not items:
            return True  # already empty

        # PlaylistItemId is the entry ID needed for deletion.
        # Different Jellyfin versions may use different field names.
        def _entry_id(item: dict) -> Optional[str]:
            for key in ("PlaylistItemId", "Id"):
                val = item.get(key)
                if val:
                    return str(val)
            return None

        entry_ids = [_entry_id(i) for i in items]
        entry_ids = [e for e in entry_ids if e]
        log.info(f"  Entry IDs to delete: {entry_ids[:5]}{'...' if len(entry_ids) > 5 else ''}")

        if not entry_ids:
            log.warning(f"  No entry IDs found. Item keys: {list(items[0].keys()) if items else []}")
            return False

        # Delete in one request — comma-separated EntryIds query param
        del_resp = await client.delete(
            f"{base_url}/Playlists/{playlist_id}/Items",
            headers=headers,
            params={"EntryIds": ",".join(entry_ids)},
        )
        log.info(f"  DELETE playlist items: HTTP {del_resp.status_code} — {del_resp.text[:200]}")

        if del_resp.status_code in (200, 204):
            return True

        # Strategy 2: delete items one at a time (fallback for strict Jellyfin versions)
        log.info(f"  Bulk delete failed, trying one-by-one...")
        all_ok = True
        for eid in entry_ids:
            r = await client.delete(
                f"{base_url}/Playlists/{playlist_id}/Items",
                headers=headers,
                params={"EntryIds": eid},
            )
            if r.status_code not in (200, 204):
                log.warning(f"  Single delete failed for entry {eid}: HTTP {r.status_code}")
                all_ok = False
        return all_ok


async def _add_to_playlist(
    base_url: str, api_key: str, playlist_id: str,
    item_ids: list[str], admin_user_id: str,
) -> bool:
    """Add items to an existing playlist in batches of 100."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(item_ids), 100):
            batch = item_ids[i:i + 100]
            resp = await client.post(
                f"{base_url}/Playlists/{playlist_id}/Items",
                headers=headers,
                params={
                    "Ids": ",".join(batch),
                    "UserId": admin_user_id,
                },
            )
            if resp.status_code not in (200, 204):
                log.warning(f"Add to playlist batch failed: {resp.status_code} — {resp.text[:200]}")
                return False
    return True


async def _get_admin_user_id(base_url: str, api_key: str) -> Optional[str]:
    """Get the first admin user ID from Jellyfin (needed for playlist operations)."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base_url}/Users", headers=headers)
        if resp.status_code != 200:
            return None
        users = resp.json()
        # Prefer admin users, fall back to first user
        admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
        return (admin or users[0])["Id"] if users else None


# ── Main entry points ─────────────────────────────────────────────────────────

async def write_playlist(
    user: ManagedUser,
    playlist_type: str,
    db: Session,
    base_url: str,
    api_key: str,
    admin_user_id: str,
) -> dict:
    """
    Write a single playlist for a single user.
    Returns a result dict with ok/tracks_added/playlist_id.
    """
    name = _playlist_name(playlist_type, user.username)
    log.info(f"  Writing playlist: '{name}'")

    # Get track IDs
    item_ids = _get_tracks_for_playlist(playlist_type, user.jellyfin_user_id, user.username, db)
    if not item_ids:
        log.warning(f"  No tracks generated for '{name}'")
        return {"ok": False, "name": name, "reason": "no_tracks", "tracks_added": 0}

    # Check if playlist already exists
    playlist_id = await _find_playlist(base_url, api_key, name, admin_user_id)

    if playlist_id:
        # Overwrite: clear then re-add
        log.info(f"  Playlist exists (id={playlist_id}), attempting clear...")
        cleared = False
        try:
            cleared = await _clear_playlist(base_url, api_key, playlist_id, admin_user_id)
        except Exception as e:
            import traceback
            log.error(f"  _clear_playlist raised exception: {type(e).__name__}: {e}")
            log.error(traceback.format_exc())
        if not cleared:
            log.warning(f"  Could not clear playlist '{name}' — will try to add anyway")
        added = await _add_to_playlist(base_url, api_key, playlist_id, item_ids, admin_user_id)
        action = "overwritten"
    else:
        # Create new
        playlist_id = await _create_playlist(base_url, api_key, name, admin_user_id, item_ids)
        added = playlist_id is not None
        action = "created"

    if not added or not playlist_id:
        return {"ok": False, "name": name, "reason": "jellyfin_error", "tracks_added": 0}

    log.info(f"  ✓ '{name}' {action} — {len(item_ids)} tracks")
    return {
        "ok": True,
        "name": name,
        "playlist_id": playlist_id,
        "tracks_added": len(item_ids),
        "action": action,
        "playlist_type": playlist_type,
        "user_id": user.jellyfin_user_id,
        "username": user.username,
    }


async def run_playlist_generation(
    db: Session,
    playlist_types: Optional[list[str]] = None,
    user_ids: Optional[list[str]] = None,
) -> dict:
    """
    Generate all requested playlist types for all (or specified) managed users.
    Records results in PlaylistRun + PlaylistRunItem tables.
    """
    types = playlist_types or list(PLAYLIST_SIZES.keys())

    try:
        base_url, api_key = _jellyfin_creds(db)
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "results": []}

    admin_user_id = await _get_admin_user_id(base_url, api_key)
    if not admin_user_id:
        return {"ok": False, "error": "Could not get Jellyfin admin user ID", "results": []}

    # Get users to process
    q = db.query(ManagedUser).filter_by(is_enabled=True)
    if user_ids:
        q = q.filter(ManagedUser.jellyfin_user_id.in_(user_ids))
    users = q.all()

    if not users:
        return {"ok": False, "error": "No enabled managed users", "results": []}

    # Create a run record
    run = PlaylistRun(
        started_at=datetime.utcnow(),
        status="running",
        playlist_types=",".join(types),
        user_count=len(users),
    )
    db.add(run)
    db.commit()

    results = []
    total_ok = 0

    for user in users:
        for ptype in types:
            try:
                result = await write_playlist(user, ptype, db, base_url, api_key, admin_user_id)
                results.append(result)
                if result["ok"]:
                    total_ok += 1
                    # Record successful playlist
                    db.add(PlaylistRunItem(
                        run_id=run.id,
                        user_id=user.jellyfin_user_id,
                        username=user.username,
                        playlist_type=ptype,
                        playlist_name=result["name"],
                        jellyfin_playlist_id=result.get("playlist_id", ""),
                        tracks_added=result["tracks_added"],
                        action=result.get("action", ""),
                        status="ok",
                    ))
                else:
                    db.add(PlaylistRunItem(
                        run_id=run.id,
                        user_id=user.jellyfin_user_id,
                        username=user.username,
                        playlist_type=ptype,
                        playlist_name=result["name"],
                        jellyfin_playlist_id="",
                        tracks_added=0,
                        action="",
                        status=result.get("reason", "error"),
                    ))
            except Exception as e:
                log.error(f"Playlist write failed for {user.username}/{ptype}: {e}")
                results.append({
                    "ok": False, "name": _playlist_name(ptype, user.username),
                    "reason": str(e), "tracks_added": 0,
                })

    run.status = "ok" if total_ok > 0 else "error"
    run.finished_at = datetime.utcnow()
    run.playlists_written = total_ok
    db.commit()

    log_event(db, "playlist_generated",
              f"Generated {total_ok} playlist(s) for {len(users)} user(s)")
    try:
        from models import AutomationSettings
        s = db.query(AutomationSettings).first()
        if not s:
            s = AutomationSettings(); db.add(s)
        s.last_playlist_regen = datetime.utcnow()
        db.commit()
    except Exception:
        pass

    return {
        "ok": True,
        "run_id": run.id,
        "playlists_written": total_ok,
        "total_attempted": len(users) * len(types),
        "results": results,
    }
