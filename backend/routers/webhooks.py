
"""
JellyDJ Webhook receiver

Skip detection strategy:
  1. SERVER-SIDE TIMING via PlaybackStart (most accurate)
  2. PlaybackProgress heartbeats — Jellyfin sends these every ~10s while playing.
     Last progress position before stop = where user actually was.
  3. PlayedToCompletion flag (fallback, unreliable on Android ~50%)
  4. Ambiguous — no data, don't penalise

Jellyfin sends Content-Type: text/plain with JSON body.
PlaybackStop fires 2-3x per action (one per connected session) — deduped.

SETUP: Enable PlaybackStart, PlaybackProgress, AND PlaybackStop in webhook config.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import PlaybackEvent, SkipPenalty, ManagedUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# ── Skip detection thresholds ────────────────────────────────────────────────
#
# SKIP_THRESHOLD: a track is counted as "completed" if the user listened to
# at least this fraction of it. Below 80% = skip. This is deliberately high
# because Jellyfin users often let tracks play to near the end before skipping.
# Lower this (e.g. 0.60) if you want to be more aggressive about skip detection.
SKIP_THRESHOLD = 0.80

# MAX_PENALTY: the highest penalty multiplier a track can accumulate.
# 0.60 means a heavily-skipped track scores at most 40% of its base score.
# Kept below 1.0 so even a much-skipped track still has a chance to appear
# if the user's taste profile strongly suggests it.
MAX_PENALTY    = 0.60

# A single skip event is enough to start accumulating penalty, but the penalty
# itself scales gradually via _calc_penalty() so one accidental skip doesn't
# permanently suppress a track.
MIN_EVENTS_FOR_PENALTY = 1

# Jellyfin sometimes fires PlaybackStop 2-3 times for a single stop action
# (one per connected client session). We deduplicate within this window.
DEDUP_WINDOW_SECONDS   = 10

# ── In-memory state ───────────────────────────────────────────────────────────
# These dicts are keyed by "user_id::jellyfin_item_id".
# They live in memory (not the DB) because they're working state for a single
# playback session — they're only persisted once PlaybackStop fires.

# Populated by PlaybackStart: stores when playback began and the total runtime
# so we can calculate elapsed time even if position_ticks is missing in Stop.
# Format: { key: (wall_time_float, runtime_ticks, track_name) }
_playback_starts: dict = {}

# Populated by PlaybackProgress heartbeats (~every 10s while playing).
# The LAST entry before PlaybackStop gives us the actual position, which is
# more reliable than the position_ticks in the Stop event itself on some clients.
# Format: { key: (last_wall_time_float, last_position_ticks, runtime_ticks, track_name) }
_playback_progress: dict = {}

# Tracks the last PlaybackStop wall time per key for deduplication
_recent_stops: dict = {}

# Circular buffer of the last 20 DB write errors — surfaced by the /diagnostic endpoint
# so admins can see if webhooks are being received but failing to persist
_recent_errors: list = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_penalty(skip_count: int, total_events: int) -> float:
    import math
    if total_events < MIN_EVENTS_FOR_PENALTY:
        return 0.0
    skip_rate = skip_count / total_events
    return round(min(MAX_PENALTY, MAX_PENALTY * (1 - math.exp(-3 * skip_rate))), 4)


def _parse_body(body: dict) -> Optional[dict]:
    item    = body.get("Item") or {}
    session = body.get("Session") or {}

    item_id = item.get("Id") or body.get("ItemId") or body.get("Id")
    user_id = session.get("UserId") or body.get("UserId")
    if not item_id or not user_id:
        return None
    # Normalise user_id: strip hyphens, lowercase — Jellyfin clients send UUIDs
    # in inconsistent formats (with/without hyphens, mixed case).
    user_id = user_id.replace("-", "").lower()

    runtime = int(item.get("RunTimeTicks") or body.get("RunTimeTicks") or 0)

    # Position — present in Progress events, always 0 in Stop events
    position = int(
        body.get("PlaybackPositionTicks") or
        session.get("PlayState", {}).get("PositionTicks") or 0
    )

    artists = item.get("Artists") or []
    artist  = (
        body.get("Artist") or
        item.get("AlbumArtist") or
        (artists[0] if artists else "") or ""
    )

    genre_raw = body.get("Genres") or item.get("Genres") or ""
    if isinstance(genre_raw, str):
        genre = genre_raw.split(",")[0].strip()
    elif isinstance(genre_raw, list):
        genre = genre_raw[0] if genre_raw else ""
    else:
        genre = ""

    return {
        "item_id":    item_id,
        "user_id":    user_id,
        "track_name": item.get("Name") or body.get("Name") or "",
        "artist_name": artist,
        "album_name": item.get("Album") or body.get("Album") or "",
        "genre":      genre,
        "runtime_ticks":  runtime,
        "position_ticks": position,
        "played_to_completion": bool(body.get("PlayedToCompletion") or False),
    }


def _is_managed(user_id: str, db: Session) -> bool:
    # Normalise: strip hyphens and lowercase so UUID format differences
    # (e.g. "16c9f81b-15e5-4486-be11-b2a4bb4f9290" vs "16c9f81b15e54486be11b2a4bb4f9290")
    # and casing differences between Jellyfin clients don't cause silent drops.
    normalised = user_id.replace("-", "").lower()

    # Try exact match first, then normalised match
    result = db.query(ManagedUser).filter_by(
        jellyfin_user_id=user_id, is_enabled=True
    ).first()

    if not result:
        # Normalised fallback — catches hyphenated or differently-cased UUIDs
        all_enabled = db.query(ManagedUser).filter_by(is_enabled=True).all()
        result = next(
            (u for u in all_enabled
             if u.jellyfin_user_id.replace("-", "").lower() == normalised),
            None
        )
        if result:
            log.warning(
                f"Webhook user_id='{user_id}' matched '{result.username}' via normalisation "                f"(stored: '{result.jellyfin_user_id}'). Consider updating the stored ID to match."
            )

    if not result:
        # Check if user exists but is disabled
        all_users = db.query(ManagedUser).all()
        exists = next(
            (u for u in all_users
             if u.jellyfin_user_id.replace("-", "").lower() == normalised),
            None
        )
        if exists:
            log.warning(f"Webhook from known user '{exists.username}' (id={user_id}) but is_enabled=False — skipping")
        else:
            log.warning(
                f"Webhook from UNKNOWN user_id='{user_id}' (normalised: '{normalised}') "                f"— not in ManagedUser table. "                f"Stored IDs: {[u.jellyfin_user_id for u in db.query(ManagedUser).all()]}"
            )
        return False
    return True


def _prune(d: dict, max_age: float = 3600):
    cutoff = time.time() - max_age
    dead = []
    for k, v in d.items():
        ts = v[0] if isinstance(v, tuple) else v
        if isinstance(ts, (int, float)) and ts < cutoff:
            dead.append(k)
    for k in dead:
        del d[k]


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_start(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return
    key = f"{p['user_id']}::{p['item_id']}"
    _playback_starts[key]   = (time.time(), p["runtime_ticks"], p["track_name"])
    _playback_progress[key] = (time.time(), 0, p["runtime_ticks"], p["track_name"])
    _prune(_playback_starts)
    _prune(_playback_progress)
    log.info(f"Playback START  user={p['user_id'][:8]}  track='{p['track_name']}'  runtime={p['runtime_ticks']//10_000_000}s")


def handle_progress(body: dict, db: Session):
    """
    PlaybackProgress fires every ~10s with actual PositionTicks.
    We store the last known position so Stop can use it.
    """
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return
    if p["position_ticks"] <= 0:
        return  # ignore zero-position progress (shouldn't happen but guard it)
    key = f"{p['user_id']}::{p['item_id']}"
    _playback_progress[key] = (time.time(), p["position_ticks"], p["runtime_ticks"], p["track_name"])
    _prune(_playback_progress)
    log.debug(
        f"Progress: '{p['track_name']}' "
        f"{p['position_ticks']//10_000_000}s / {p['runtime_ticks']//10_000_000}s"
    )


def handle_stop(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return

    # Dedup — Jellyfin fires stop once per connected session
    key = f"{p['user_id']}::{p['item_id']}"
    now = time.time()
    if now - _recent_stops.get(key, 0) < DEDUP_WINDOW_SECONDS:
        log.debug(f"Dedup: dropping duplicate stop  user={p['user_id'][:8]}  track='{p['track_name']}'")
        return
    _recent_stops[key] = now
    _prune(_recent_stops)

    # ── Determine completion ──────────────────────────────────────────────────
    completion: float
    method: str

    start_entry    = _playback_starts.pop(key, None)
    progress_entry = _playback_progress.pop(key, None)

    # Use runtime from Stop event if present, else fall back to what Start/Progress stored.
    # Mobile clients (Android, iOS) often omit RunTimeTicks from PlaybackStop payloads,
    # which caused all completion branches to be skipped → method="ambiguous" → skips never
    # recorded for users on those clients. Start and Progress always include RunTimeTicks.
    runtime_ticks = (
        p["runtime_ticks"]
        or (start_entry[1] if start_entry else 0)
        or (progress_entry[2] if progress_entry else 0)
    )

    if start_entry and runtime_ticks > 0:
        # Best: wall-clock elapsed since start
        start_wall, _rt, _ = start_entry
        elapsed_ticks = (now - start_wall) * 10_000_000
        completion = min(1.0, elapsed_ticks / runtime_ticks)
        method = "server_timing"

    elif progress_entry and runtime_ticks > 0:
        # Good: last known position from Progress heartbeat
        _wall, last_pos, _rt, _ = progress_entry
        # Add time since last heartbeat (song kept playing after it)
        extra_ticks = (now - _wall) * 10_000_000
        effective_pos = min(runtime_ticks, last_pos + extra_ticks)
        completion = effective_pos / runtime_ticks
        method = "progress_heartbeat"

    elif p["played_to_completion"]:
        # Fallback: Jellyfin flag (unreliable on Android but better than nothing)
        completion = 1.0
        method = "played_to_completion_flag"

    else:
        # No data — ambiguous, don't penalise
        completion = 0.0
        method = "ambiguous"

    is_skip = (method != "ambiguous") and (completion < SKIP_THRESHOLD)

    log.info(
        f"Playback STOP  user={p['user_id'][:8]}  track='{p['track_name']}' by '{p['artist_name']}' "
        f"— {completion:.0%} via {method} → {'SKIP' if is_skip else 'PLAYED'}"
    )

    # Record event
    import traceback as _tb
    try:
        db.add(PlaybackEvent(
            user_id=p["user_id"],
            jellyfin_item_id=p["item_id"],
            track_name=p["track_name"],
            artist_name=p["artist_name"],
            album_name=p["album_name"],
            genre=p["genre"],
            position_ticks=int(completion * p["runtime_ticks"]),
            runtime_ticks=p["runtime_ticks"],
            completion_pct=str(round(completion, 4)),
            was_skip=is_skip,
            received_at=datetime.utcnow(),
        ))

        # Update penalty
        row = db.query(SkipPenalty).filter_by(
            user_id=p["user_id"], jellyfin_item_id=p["item_id"]
        ).first()
        if not row:
            row = SkipPenalty(
                user_id=p["user_id"], jellyfin_item_id=p["item_id"],
                artist_name=p["artist_name"], genre=p["genre"],
                total_events=0, skip_count=0,
            )
            db.add(row)
            db.flush()

        row.total_events = (row.total_events or 0) + 1
        if is_skip:
            row.skip_count = (row.skip_count or 0) + 1
        skip_rate     = (row.skip_count or 0) / row.total_events
        row.skip_rate = str(round(skip_rate, 4))
        row.penalty   = str(_calc_penalty(row.skip_count, row.total_events))
        row.updated_at = datetime.utcnow()
        db.commit()
        log.info(f"  ✓ DB commit OK  user={p['user_id'][:8]}  skip={is_skip}")
    except Exception as exc:
        err_msg = (
            f"user={p['user_id'][:8]} track='{p['track_name']}' "
            f"{type(exc).__name__}: {exc}"
        )
        log.error(f"  ✗ DB WRITE FAILED  {err_msg}")
        log.error(_tb.format_exc())
        _recent_errors.append({
            "at": datetime.utcnow().isoformat(),
            "user_id": p["user_id"],
            "track": p["track_name"],
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": _tb.format_exc(),
        })
        if len(_recent_errors) > 20:
            _recent_errors.pop(0)
        try:
            db.rollback()
        except Exception:
            pass


# ── API endpoint ──────────────────────────────────────────────────────────────

@router.post("/jellyfin")
async def jellyfin_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = json.loads(await request.body())
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    raw_type = (
        body.get("NotificationType") or
        body.get("Event") or
        body.get("Type") or ""
    ).lower().replace(".", "").replace("_", "").replace(" ", "")

    log.debug(f"Webhook event: {raw_type} — {body.get('Name')}")

    try:
        if "playbackstart" in raw_type:
            handle_start(body, db)
            return {"ok": True, "event": "start"}
        elif "playbackprogress" in raw_type or "playbacktick" in raw_type:
            handle_progress(body, db)
            return {"ok": True, "event": "progress"}
        elif "playbackstop" in raw_type:
            handle_stop(body, db)
            return {"ok": True, "event": "stop"}
        else:
            return {"ok": True, "processed": False, "reason": f"'{raw_type}' ignored"}
    except Exception as e:
        log.error(f"Webhook error ({raw_type}): {e}")
        return {"ok": True, "processed": False, "error": str(e)}


# ── Diagnostic / utility endpoints ───────────────────────────────────────────

_debug_captures: list = []

@router.post("/jellyfin/debug")
async def debug_capture(request: Request):
    raw  = await request.body()
    body = {}
    try:
        body = json.loads(raw)
    except Exception:
        pass
    session = body.get("Session") or {}
    item    = body.get("Item") or {}
    capture = {
        "NotificationType":      body.get("NotificationType"),
        "Name":                  body.get("Name"),
        "Artist":                body.get("Artist"),
        # All the places Jellyfin might send a user ID
        "UserId_body":           body.get("UserId"),
        "UserId_session":        session.get("UserId"),
        "Username_session":      session.get("UserName") or session.get("Username"),
        "ItemId_item":           item.get("Id"),
        "ItemId_body":           body.get("ItemId"),
        "PlaybackPositionTicks": body.get("PlaybackPositionTicks"),
        "RunTimeTicks":          body.get("RunTimeTicks"),
        "PlayedToCompletion":    body.get("PlayedToCompletion"),
        "raw_text": raw.decode("utf-8", errors="replace")[:2000],
    }
    _debug_captures.append(capture)
    if len(_debug_captures) > 20:
        _debug_captures.pop(0)
    print(f"DEBUG: type={body.get('NotificationType')} track={body.get('Name')} pos={body.get('PlaybackPositionTicks')}", flush=True)
    return {"received": True, "capture": capture}


@router.get("/jellyfin/debug")
async def get_debug():
    return {"count": len(_debug_captures), "captures": _debug_captures}


@router.get("/pending-starts")
def pending_starts():
    """Show what's currently registered as started — for debugging."""
    now = time.time()
    return {
        k: {
            "track_name": v[2],
            "started_ago_seconds": round(now - v[0], 1),
            "runtime_seconds": round(v[1] / 10_000_000, 0),
        }
        for k, v in _playback_starts.items()
    }


@router.get("/managed-users")
def managed_users_diagnostic(db: Session = Depends(get_db)):
    """
    Full webhook diagnostic — shows registered users AND what's actually in the DB.
    This tells you immediately whether events are arriving and being recorded.
    """
    from models import PlaybackEvent, SkipPenalty
    from sqlalchemy import func

    users = db.query(ManagedUser).all()

    result = []
    for u in users:
        uid = u.jellyfin_user_id

        total_events = db.query(PlaybackEvent).filter_by(user_id=uid).count()
        skip_events  = db.query(PlaybackEvent).filter_by(user_id=uid, was_skip=True).count()
        ambiguous    = db.query(PlaybackEvent).filter_by(user_id=uid).filter(
            PlaybackEvent.completion_pct == "0.0"
        ).count()

        # Most recent event
        last_event = (
            db.query(PlaybackEvent)
            .filter_by(user_id=uid)
            .order_by(PlaybackEvent.received_at.desc())
            .first()
        )

        # Check in-memory state (what's currently tracked)
        pending_starts = [k for k in _playback_starts if k.startswith(uid)]
        pending_progress = [k for k in _playback_progress if k.startswith(uid)]

        result.append({
            "username":           u.username,
            "jellyfin_user_id":   uid,
            "is_enabled":         u.is_enabled,
            "db_total_events":    total_events,
            "db_skip_events":     skip_events,
            "db_ambiguous_events": ambiguous,
            "db_completion_events": total_events - ambiguous,
            "last_event_track":   last_event.track_name if last_event else None,
            "last_event_was_skip": last_event.was_skip if last_event else None,
            "last_event_completion": last_event.completion_pct if last_event else None,
            "last_event_at":      last_event.received_at.isoformat() if last_event else None,
            "in_memory_pending_starts":   len(pending_starts),
            "in_memory_pending_progress": len(pending_progress),
            "diagnosis": (
                "No events in DB — webhook user_id mismatch or events not reaching server"
                if total_events == 0 else
                f"{total_events} events, {skip_events} skips. "
                f"{ambiguous} ambiguous (runtime missing in Stop — mobile client likely). "
                "Skips may be under-counted before this fix."
                if ambiguous > total_events * 0.3 and skip_events == 0 else
                f"{total_events} events in DB, {skip_events} skips recorded — OK"
            ),
        })

    return {
        "managed_users": result,
        "in_memory_active_starts": len(_playback_starts),
        "recent_db_errors": _recent_errors[-5:],   # last 5 errors with full traceback
        "hint": (
            "If db_total_events=0: webhook user_id not matching. "
            "If db_total_events>0 but db_skip_events=0: PlaybackStart events missing — "
            "check Jellyfin webhook config has PlaybackStart enabled. "
            "If db_ambiguous_events is high: container may have restarted losing in-memory state. "
            "Check recent_db_errors for exact failure reason."
        )
    }


@router.get("/setup-guide")
def setup_guide(request: Request):
    host = request.headers.get("host", "localhost:7879")
    url  = f"http://{host}/api/webhooks/jellyfin"
    return {
        "webhook_url": url,
        "instructions": [
            "1. Jellyfin → Dashboard → Plugins → Webhook",
            "2. Click 'Add Generic Destination'",
            f"3. URL: {url}",
            "4. Request Type: POST",
            "5. Notification Types: enable 'Playback Start', 'Playback Progress', AND 'Playback Stop'",
            "6. Item Type: Songs",
            "7. Send all fields — Save",
            "NOTE: All three event types are required for accurate skip detection.",
            "Start + Progress track position; Stop calculates how much was played.",
        ],
        "skip_threshold": f"{SKIP_THRESHOLD:.0%}",
    }


@router.get("/stats/{user_id}")
def skip_stats(user_id: str, limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(SkipPenalty)
        .filter_by(user_id=user_id)
        .filter(SkipPenalty.total_events >= MIN_EVENTS_FOR_PENALTY)
        .order_by(SkipPenalty.skip_rate.desc())
        .limit(limit).all()
    )
    return {
        "user_id": user_id,
        "total_tracked": db.query(SkipPenalty).filter_by(user_id=user_id).count(),
        "items": [
            {
                "jellyfin_item_id": r.jellyfin_item_id,
                "artist_name":  r.artist_name,
                "total_events": r.total_events,
                "skip_count":   r.skip_count,
                "skip_rate":    float(r.skip_rate),
                "penalty":      float(r.penalty),
            }
            for r in rows
        ],
    }


@router.get("/recent/{user_id}")
def recent_events(user_id: str, limit: int = 20, db: Session = Depends(get_db)):
    rows = (
        db.query(PlaybackEvent)
        .filter_by(user_id=user_id)
        .order_by(PlaybackEvent.received_at.desc())
        .limit(limit).all()
    )
    return [
        {
            "track_name":     r.track_name,
            "artist_name":    r.artist_name,
            "position_ticks": r.position_ticks,
            "runtime_ticks":  r.runtime_ticks,
            "completion_pct": float(r.completion_pct),
            "was_skip":       r.was_skip,
            "received_at":    r.received_at,
        }
        for r in rows
    ]


@router.delete("/penalties/{user_id}")
def clear_penalties(user_id: str, db: Session = Depends(get_db)):
    pc = db.query(SkipPenalty).filter_by(user_id=user_id).delete()
    ec = db.query(PlaybackEvent).filter_by(user_id=user_id).delete()
    db.commit()
    return {"ok": True, "penalties_cleared": pc, "events_cleared": ec}
