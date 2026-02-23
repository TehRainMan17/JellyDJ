
"""
JellyDJ Playlist Writer — Module 7

Generates playlists from the recommender engine and writes them to Jellyfin.
Each managed user gets their own named playlist visible to all Jellyfin users.

Playlist types:
  for_you         — "For You - Alice"          affinity-weighted
  discover        — "Discover Weekly - Alice"  novelty-heavy
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

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from models import (
    ConnectionSettings, ManagedUser, Play,
    PlaylistRun, PlaylistRunItem, TrackScore,
)
from services.events import log_event
from crypto import decrypt

log = logging.getLogger(__name__)

# Track counts per playlist type.
# Kept intentionally different so each playlist feels sized appropriately —
# "Most Played" at 50 gives a decent listening session; "Discover Weekly"
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
    "discover":         "Discover Weekly",
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


def _get_tracks_for_playlist(
    playlist_type: str,
    user_id: str,
    username: str,
    db: Session,
) -> list[str]:
    """
    Return a list of Jellyfin item IDs for the given playlist type.

    Variety mechanisms (moderate randomness):
    - for_you:   20% reserved discovery slots (unplayed from loved artists) +
                 10% deep cuts (high-affinity tracks not heard in 6+ months) +
                 mid-tier score jitter on the remaining 70%
    - discover:  55% unplayed + 25% deep cuts + 20% recently played stale tracks
                 all with score jitter in mid-tier
    - most_played / recently_played: stable (these are intentionally deterministic)
    """
    import random
    from datetime import timedelta
    from models import TrackScore, Play
    from sqlalchemy import text as satext, cast, Float as SAFloat

    limit = PLAYLIST_SIZES.get(playlist_type, 50)
    score_count = db.query(TrackScore).filter_by(user_id=user_id).count()

    if score_count == 0:
        log.warning(f"  No TrackScores for {username} — falling back to plays table")
        rows = (
            db.query(Play.jellyfin_item_id)
            .filter_by(user_id=user_id)
            .filter(Play.play_count > 0)
            .order_by(Play.play_count.desc())
            .limit(limit)
            .all()
        )
        return [r.jellyfin_item_id for r in rows]

    # ── Deterministic types — no jitter ──────────────────────────────────────

    if playlist_type == "most_played":
        rows = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(TrackScore.is_played == True)
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
            # Temporarily store jittered score for sorting; don't persist
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
            .order_by(satext("CAST(final_score AS REAL) DESC"))
            .limit(n_discovery * 8)
            .all()
        )
        discovery_pool = [r for r in discovery_pool if r.jellyfin_item_id not in core_set]
        discovery_pool = _jitter(discovery_pool)
        discovery_ids = _diversify(discovery_pool, n_discovery)
        combined_set = core_set | set(discovery_ids)

        # Deep cuts: high affinity, played, not heard in 6+ months
        cutoff = datetime.utcnow() - timedelta(days=180)
        deep_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(
                TrackScore.is_played == True,
                TrackScore.last_played < cutoff,
            )
            .order_by(satext("CAST(artist_affinity AS REAL) DESC"))
            .limit(n_deep_cuts * 8)
            .all()
        )
        deep_pool = [r for r in deep_pool if r.jellyfin_item_id not in combined_set]
        random.shuffle(deep_pool)   # true shuffle for deep cuts — surprise factor
        deep_ids = _diversify(deep_pool, n_deep_cuts)

        result = core_ids + discovery_ids + deep_ids
        random.shuffle(result)   # interleave all three pools so playlist doesn't feel sectioned
        return result

    # ── discover: unplayed-heavy + deep cuts + stale played ──────────────────

    if playlist_type == "discover":
        n_unplayed   = int(limit * 0.55)   # 55% never heard
        n_deep_cuts  = int(limit * 0.25)   # 25% deep cuts (6+ months unheard)
        n_stale      = limit - n_unplayed - n_deep_cuts  # 20% oldest plays

        unplayed_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(TrackScore.is_played == False)
            .order_by(satext("CAST(final_score AS REAL) DESC"))
            .limit(n_unplayed * 6)
            .all()
        )
        unplayed_pool = _jitter(unplayed_pool)
        unplayed_ids = _diversify(unplayed_pool, n_unplayed)
        used = set(unplayed_ids)

        cutoff = datetime.utcnow() - timedelta(days=180)
        deep_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(
                TrackScore.is_played == True,
                TrackScore.last_played < cutoff,
            )
            .order_by(satext("CAST(artist_affinity AS REAL) DESC"))
            .limit(n_deep_cuts * 6)
            .all()
        )
        deep_pool = [r for r in deep_pool if r.jellyfin_item_id not in used]
        random.shuffle(deep_pool)
        deep_ids = _diversify(deep_pool, n_deep_cuts)
        used |= set(deep_ids)

        stale_pool = (
            db.query(TrackScore)
            .filter_by(user_id=user_id)
            .filter(
                TrackScore.is_played == True,
                TrackScore.last_played.isnot(None),
            )
            .order_by(TrackScore.last_played)   # oldest first
            .limit(n_stale * 6)
            .all()
        )
        stale_pool = [r for r in stale_pool if r.jellyfin_item_id not in used]
        stale_ids = _diversify(stale_pool, n_stale)

        result = unplayed_ids + deep_ids + stale_ids
        random.shuffle(result)
        return result

    # Fallback
    rows = (
        db.query(TrackScore)
        .filter_by(user_id=user_id)
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
