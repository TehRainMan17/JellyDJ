"""
JellyDJ Webhook receiver — v2

Changes from v1:
  - SkipPenalty now tracks consecutive_skips (streak) and resets on completion
  - After every skip event, check_and_apply_cooldown() is called — if the streak
    reaches COOLDOWN_SKIP_STREAK_THRESHOLD (3), the track enters cooldown
  - PlaybackEvent now stores source_context and session_id
  - Session ID is derived from (user_id + day) so we can group a listening session
    without requiring Jellyfin to report session info

Skip detection strategy — designed to work across ALL Jellyfin clients:

  STANDARD clients (Android, web, Jellyfin iOS, Swiftfin):
    Send PlaybackStart → PlaybackProgress → PlaybackStop.
    Completion measured via server-side wall-clock timing (most accurate).

  PROGRESS-ONLY clients (Manet on iOS, some others):
    Send only PlaybackProgress heartbeats (~1s interval, IsAutomated=True).
    No PlaybackStart or PlaybackStop.
    Track transition detected when ItemId changes for the same user.
    Completion measured via max position reached on previous track.

  SKIP CONFIRMATION (all clients):
    A stop that looks like a skip is held PENDING for SKIP_CONFIRM_SECS (10s).
    If the user's next PlaybackStart arrives within that window, it's confirmed.
    If silence follows (session ended), it's discarded — not penalized.

  MAX-POSITION tracking:
    Both paths track the PEAK position reached, not the last reported position.
    Protects against false skips when users seek backward.

Cooldown rules (v2):
  - consecutive_skips tracks skips in a row WITHOUT a completion between them
  - A completion resets consecutive_skips to 0
  - When consecutive_skips >= 3: check_and_apply_cooldown() in enrichment.py
  - Cooldown durations: 1st=7d, 2nd=14d, 3rd=30d, 4th+=permanent penalty
  - Favorites are shielded: consecutive_skips capped at 2 for favorited tracks
    (one or two bad-day skips shouldn't cooldown a beloved song)
"""
from __future__ import annotations

import hashlib
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

SKIP_THRESHOLD = 0.80
MAX_PENALTY = 0.60
MIN_EVENTS_FOR_PENALTY = 1
DEDUP_WINDOW_SECONDS = 10
SKIP_CONFIRM_SECS = 10
SESSION_END_SECS = 60
MIN_POSITION_TICKS = 5_000_000  # 0.5 seconds

# Favorites shield: a favorited track's consecutive skip count is capped at this
# value so it can never trigger cooldown from casual skips alone
FAVORITE_CONSECUTIVE_SKIP_CAP = 2


# ── In-memory state ───────────────────────────────────────────────────────────

_playback_starts: dict = {}
_playback_progress: dict = {}
_active_item: dict = {}
_pending_skips: dict = {}
_recent_stops: dict = {}
_recent_errors: list = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc_penalty(skip_count: int, total_events: int) -> float:
    import math
    if total_events < MIN_EVENTS_FOR_PENALTY:
        return 0.0
    skip_rate = skip_count / total_events
    return round(min(MAX_PENALTY, MAX_PENALTY * (1 - math.exp(-3 * skip_rate))), 4)


def _session_id(user_id: str) -> str:
    """
    Derive a session identifier from user + current UTC date.
    A "session" = one calendar day of listening for a user.
    This groups plays without requiring Jellyfin to report session data.
    Fine-grained enough to detect same-session artist returns.
    """
    day = datetime.utcnow().strftime("%Y-%m-%d")
    raw = f"{user_id}:{day}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _parse_body(body: dict) -> Optional[dict]:
    item    = body.get("Item") or {}
    session = body.get("Session") or {}

    item_id = item.get("Id") or body.get("ItemId") or body.get("Id")
    user_id = session.get("UserId") or body.get("UserId")
    if not item_id or not user_id:
        return None

    user_id = user_id.replace("-", "").lower()

    runtime = int(item.get("RunTimeTicks") or body.get("RunTimeTicks") or 0)
    position = int(
        body.get("PlaybackPositionTicks") or
        session.get("PlayState", {}).get("PositionTicks") or 0
    )

    artists = item.get("Artists") or []
    artist = (
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
        "is_automated": bool(body.get("IsAutomated") or False),
        "is_favorite": False,   # filled in by _enrich_favorite flag below
    }


def _is_managed(user_id: str, db: Session) -> bool:
    normalised = user_id.replace("-", "").lower()
    result = db.query(ManagedUser).filter_by(
        jellyfin_user_id=normalised, is_enabled=True
    ).first()
    if not result:
        all_enabled = db.query(ManagedUser).filter_by(is_enabled=True).all()
        result = next(
            (u for u in all_enabled
             if u.jellyfin_user_id.replace("-", "").lower() == normalised),
            None,
        )
        if result:
            log.warning(
                f"Webhook user_id='{user_id}' matched '{result.username}' via normalisation. "
                f"Consider updating the stored ID."
            )
    if not result:
        all_users = db.query(ManagedUser).all()
        exists = next(
            (u for u in all_users
             if u.jellyfin_user_id.replace("-", "").lower() == normalised),
            None,
        )
        if exists:
            log.warning(f"Webhook from known user '{exists.username}' but is_enabled=False — skipping")
        else:
            log.warning(
                f"Webhook from UNKNOWN user_id='{user_id}'. "
                f"Stored IDs: {[u.jellyfin_user_id for u in db.query(ManagedUser).all()]}"
            )
        return False
    return True


def _prune(d: dict, max_age: float = 3600):
    cutoff = time.time() - max_age
    dead = [k for k, v in d.items()
            if isinstance(v, tuple) and isinstance(v[0], float) and v[0] < cutoff]
    for k in dead:
        del d[k]


def _write_event(db: Session, p: dict, completion: float, method: str, is_skip: bool):
    """
    Persist a PlaybackEvent and update the SkipPenalty row.
    v2: also updates consecutive_skips and triggers cooldown if threshold reached.
    """
    import traceback as _tb
    try:
        sid = _session_id(p["user_id"])

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
            source_context=p.get("source_context"),  # set externally if known
            session_id=sid,
        ))

        row = db.query(SkipPenalty).filter_by(
            user_id=p["user_id"], jellyfin_item_id=p["item_id"]
        ).first()
        if not row:
            row = SkipPenalty(
                user_id=p["user_id"], jellyfin_item_id=p["item_id"],
                artist_name=p["artist_name"], genre=p["genre"],
                total_events=0, skip_count=0,
                consecutive_skips=0, skip_streak_peak=0,
            )
            db.add(row)
            db.flush()

        row.total_events = (row.total_events or 0) + 1
        now = datetime.utcnow()

        if is_skip:
            row.skip_count = (row.skip_count or 0) + 1
            row.consecutive_skips = (row.consecutive_skips or 0) + 1
            row.last_skip_at = now

            # Favorites shield: cap consecutive skips so beloved songs can't
            # be accidentally cooled down by a couple of bad-day skips
            if p.get("is_favorite") and row.consecutive_skips > FAVORITE_CONSECUTIVE_SKIP_CAP:
                row.consecutive_skips = FAVORITE_CONSECUTIVE_SKIP_CAP

            row.skip_streak_peak = max(
                row.skip_streak_peak or 0,
                row.consecutive_skips,
            )
        else:
            # Completed — reset the consecutive skip streak
            row.consecutive_skips = 0
            row.last_completed_at = now

        skip_rate = (row.skip_count or 0) / row.total_events
        row.skip_rate = str(round(skip_rate, 4))
        row.penalty   = str(_calc_penalty(row.skip_count, row.total_events))
        row.updated_at = now
        db.flush()

        # v2: check if this track should enter cooldown
        cooldown_result = None
        if is_skip:
            try:
                from services.enrichment import check_and_apply_cooldown
                cooldown_result = check_and_apply_cooldown(
                    db=db,
                    user_id=p["user_id"],
                    jellyfin_item_id=p["item_id"],
                    artist_name=p["artist_name"],
                    track_name=p["track_name"],
                    consecutive_skips=row.consecutive_skips,
                )
            except Exception as ce:
                log.warning(f"  Cooldown check failed: {ce}")

        db.commit()

        outcome = "SKIP"
        if not is_skip:
            outcome = "PLAYED"
        if cooldown_result == "triggered":
            outcome += " → COOLDOWN"
        elif cooldown_result == "permanent":
            outcome += " → PERMANENT DISLIKE"

        log.info(
            f"  ✓ Recorded  user={p['user_id'][:8]}  '{p['track_name']}'  "
            f"{completion:.0%} via {method}  → {outcome}  "
            f"(streak={row.consecutive_skips})"
        )

    except Exception as exc:
        err_msg = (
            f"user={p['user_id'][:8]} track='{p['track_name']}' "
            f"{type(exc).__name__}: {exc}"
        )
        log.error(f"  ✗ DB WRITE FAILED  {err_msg}")
        import traceback
        log.error(traceback.format_exc())
        _recent_errors.append({
            "at": datetime.utcnow().isoformat(),
            "user_id": p["user_id"],
            "track": p["track_name"],
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        })
        if len(_recent_errors) > 20:
            _recent_errors.pop(0)
        try:
            db.rollback()
        except Exception:
            pass


def _expire_pending_skips(db: Session):
    now = time.time()
    expired = [
        uid for uid, entry in _pending_skips.items()
        if now - entry["wall_time"] > SKIP_CONFIRM_SECS
    ]
    for uid in expired:
        entry = _pending_skips.pop(uid)
        log.info(
            f"  Session end (no next track within {SKIP_CONFIRM_SECS}s) — "
            f"NOT penalizing  user={uid[:8]}  '{entry['p']['track_name']}'"
        )


def _finalize_progress_track(user_id: str, old_item_id: str, db: Session):
    key = f"{user_id}::{old_item_id}"
    entry = _playback_progress.pop(key, None)
    if not entry:
        return

    _wall, max_pos, runtime, p = entry
    if runtime <= 0 or max_pos < MIN_POSITION_TICKS:
        log.debug(f"  Progress finalize: skipping '{p['track_name']}' — insufficient data")
        return

    completion = min(1.0, max_pos / runtime)
    is_skip = completion < SKIP_THRESHOLD

    log.info(
        f"Playback TRANSITION (progress-only)  user={user_id[:8]}  "
        f"'{p['track_name']}'  max_pos={max_pos//10_000_000}s / {runtime//10_000_000}s "
        f"= {completion:.0%}  → {'SKIP' if is_skip else 'PLAYED'}"
    )
    _write_event(db, p, completion, "progress_transition", is_skip)


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_start(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return

    _expire_pending_skips(db)

    uid = p["user_id"]
    key = f"{uid}::{p['item_id']}"

    if uid in _pending_skips:
        entry = _pending_skips.pop(uid)
        log.info(
            f"  Skip CONFIRMED by next PlaybackStart  user={uid[:8]}  "
            f"'{entry['p']['track_name']}'"
        )
        _write_event(db, entry["p"], entry["completion"], entry["method"], is_skip=True)

    prev_item_id = _active_item.get(uid)
    if prev_item_id and prev_item_id != p["item_id"]:
        log.debug(f"  Start-triggered transition: {prev_item_id[:8]} → {p['item_id'][:8]}  user={uid[:8]}")
        _finalize_progress_track(uid, prev_item_id, db)

    _playback_starts[key]   = (time.time(), p["runtime_ticks"], p["track_name"])
    _playback_progress[key] = (time.time(), 0, p["runtime_ticks"], p)
    _active_item[uid]       = p["item_id"]

    _prune(_playback_starts)
    _prune(_playback_progress)
    log.info(
        f"Playback START  user={uid[:8]}  '{p['track_name']}'  "
        f"runtime={p['runtime_ticks']//10_000_000}s"
    )


def handle_progress(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return

    _expire_pending_skips(db)

    uid = p["user_id"]
    key = f"{uid}::{p['item_id']}"
    now = time.time()

    prev_item_id = _active_item.get(uid)
    if prev_item_id and prev_item_id != p["item_id"]:
        log.debug(f"  Track transition: {prev_item_id[:8]} → {p['item_id'][:8]}  user={uid[:8]}")
        _finalize_progress_track(uid, prev_item_id, db)

    _active_item[uid] = p["item_id"]

    existing = _playback_progress.get(key)
    current_max = existing[1] if existing else 0
    new_max = max(current_max, p["position_ticks"]) if p["position_ticks"] >= MIN_POSITION_TICKS else current_max
    _playback_progress[key] = (now, new_max, p["runtime_ticks"] or (existing[2] if existing else 0), p)
    _prune(_playback_progress)

    log.debug(
        f"Progress: '{p['track_name']}'  "
        f"{p['position_ticks']//10_000_000}s / {p['runtime_ticks']//10_000_000}s  "
        f"max={new_max//10_000_000}s"
    )


def handle_stop(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return

    _expire_pending_skips(db)

    uid = p["user_id"]
    key = f"{uid}::{p['item_id']}"
    now = time.time()

    if now - _recent_stops.get(key, 0) < DEDUP_WINDOW_SECONDS:
        log.debug(f"Dedup: dropping duplicate stop  user={uid[:8]}  '{p['track_name']}'")
        return
    _recent_stops[key] = now
    _prune(_recent_stops)

    if _active_item.get(uid) == p["item_id"]:
        del _active_item[uid]

    start_entry    = _playback_starts.pop(key, None)
    progress_entry = _playback_progress.pop(key, None)

    runtime_ticks = (
        p["runtime_ticks"]
        or (start_entry[1] if start_entry else 0)
        or (progress_entry[2] if progress_entry else 0)
    )

    if p["played_to_completion"]:
        completion = 1.0
        method = "played_to_completion_flag"
        is_skip = False
        log.info(f"Playback STOP  user={uid[:8]}  '{p['track_name']}' — 100% (completion flag) → PLAYED")
        _write_event(db, p, completion, method, is_skip)
        return

    if start_entry and runtime_ticks > 0:
        start_wall, _rt, _ = start_entry
        elapsed_ticks = (now - start_wall) * 10_000_000
        completion = min(1.0, elapsed_ticks / runtime_ticks)
        method = "server_timing"

    elif progress_entry and runtime_ticks > 0:
        _wall, max_pos, _rt, _ = progress_entry
        extra_ticks = (now - _wall) * 10_000_000
        effective_pos = min(runtime_ticks, max_pos + extra_ticks)
        completion = effective_pos / runtime_ticks
        method = "progress_heartbeat"

    else:
        completion = 0.0
        method = "ambiguous"

    is_skip = (method != "ambiguous") and (completion < SKIP_THRESHOLD)

    log.info(
        f"Playback STOP  user={uid[:8]}  '{p['track_name']}'  "
        f"{completion:.0%} via {method}  → {'pending skip confirmation' if is_skip else 'PLAYED'}"
    )

    if not is_skip or method == "ambiguous":
        _write_event(db, p, completion, method, is_skip=False if method == "ambiguous" else is_skip)
        return

    _pending_skips[uid] = {
        "p":          p,
        "completion": completion,
        "method":     method,
        "wall_time":  now,
    }
    log.info(
        f"  Skip PENDING confirmation  user={uid[:8]}  '{p['track_name']}'  "
        f"{completion:.0%} — waiting {SKIP_CONFIRM_SECS}s for next PlaybackStart"
    )


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
        "UserId_body":           body.get("UserId"),
        "UserId_session":        session.get("UserId"),
        "Username_session":      session.get("UserName") or session.get("Username"),
        "ItemId_item":           item.get("Id"),
        "ItemId_body":           body.get("ItemId"),
        "PlaybackPositionTicks": body.get("PlaybackPositionTicks"),
        "RunTimeTicks":          body.get("RunTimeTicks"),
        "PlayedToCompletion":    body.get("PlayedToCompletion"),
        "IsAutomated":           body.get("IsAutomated"),
        "ClientName":            body.get("ClientName") or body.get("Client"),
        "DeviceName":            body.get("DeviceName"),
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
    now = time.time()
    return {
        k: {
            "track_name": v[2],
            "started_ago_seconds": round(now - v[0], 1),
            "runtime_seconds": round(v[1] / 10_000_000, 0),
        }
        for k, v in _playback_starts.items()
    }


@router.get("/pending-skips")
def pending_skips_diagnostic():
    now = time.time()
    return {
        uid: {
            "track_name": entry["p"]["track_name"],
            "completion": f"{entry['completion']:.0%}",
            "method": entry["method"],
            "waiting_secs": round(now - entry["wall_time"], 1),
            "expires_in_secs": round(SKIP_CONFIRM_SECS - (now - entry["wall_time"]), 1),
        }
        for uid, entry in _pending_skips.items()
    }


@router.get("/managed-users")
def managed_users_diagnostic(db: Session = Depends(get_db)):
    from models import PlaybackEvent, SkipPenalty
    from sqlalchemy import desc

    users = db.query(ManagedUser).all()
    result = []

    for u in users:
        uid = u.jellyfin_user_id

        total_events = db.query(PlaybackEvent).filter_by(user_id=uid).count()
        skip_events  = db.query(PlaybackEvent).filter_by(user_id=uid, was_skip=True).count()
        ambiguous    = db.query(PlaybackEvent).filter_by(user_id=uid).filter(
            PlaybackEvent.completion_pct == "0.0"
        ).count()

        last_event = (
            db.query(PlaybackEvent)
            .filter_by(user_id=uid)
            .order_by(PlaybackEvent.received_at.desc())
            .first()
        )

        # v2: cooldown stats
        from models import TrackCooldown
        active_cooldowns = db.query(TrackCooldown).filter_by(
            user_id=uid, status="active"
        ).count()
        permanent_dislikes = db.query(TrackCooldown).filter_by(
            user_id=uid, status="permanent"
        ).count()

        pending_start_keys    = [k for k in _playback_starts    if k.startswith(uid)]
        pending_progress_keys = [k for k in _playback_progress if k.startswith(uid)]
        pending_skip          = _pending_skips.get(uid)

        if total_events == 0:
            diagnosis = "No events in DB — webhook user_id mismatch or events not reaching server"
        elif skip_events == 0 and total_events > 5:
            diagnosis = f"{total_events} events but 0 skips — progress-only client (Manet)"
        else:
            diagnosis = f"{total_events} events, {skip_events} skips ({100*skip_events//total_events if total_events else 0}%) — OK"

        result.append({
            "username":            u.username,
            "jellyfin_user_id":    uid,
            "is_enabled":          u.is_enabled,
            "db_total_events":     total_events,
            "db_skip_events":      skip_events,
            "db_ambiguous_events": ambiguous,
            "last_event_track":    last_event.track_name if last_event else None,
            "last_event_was_skip": last_event.was_skip if last_event else None,
            "last_event_at":       last_event.received_at.isoformat() if last_event else None,
            "in_memory_pending_starts":   len(pending_start_keys),
            "in_memory_pending_progress": len(pending_progress_keys),
            "in_memory_pending_skip":     bool(pending_skip),
            # v2 additions
            "active_cooldowns":    active_cooldowns,
            "permanent_dislikes":  permanent_dislikes,
            "diagnosis": diagnosis,
        })

    return {
        "managed_users": result,
        "in_memory_active_starts":   len(_playback_starts),
        "in_memory_pending_skips":   len(_pending_skips),
        "recent_db_errors": _recent_errors[-5:],
    }


@router.get("/cooldowns/{user_id}")
def get_cooldowns(user_id: str, db: Session = Depends(get_db)):
    """Show all active and historical cooldowns for a user."""
    from models import TrackCooldown
    rows = (
        db.query(TrackCooldown)
        .filter_by(user_id=user_id)
        .order_by(TrackCooldown.cooldown_started_at.desc())
        .limit(50)
        .all()
    )
    now = datetime.utcnow()
    return [
        {
            "track_name":         r.track_name,
            "artist_name":        r.artist_name,
            "status":             r.status,
            "cooldown_count":     r.cooldown_count,
            "skip_streak":        r.skip_streak_at_trigger,
            "cooldown_until":     r.cooldown_until.isoformat() if r.cooldown_until else None,
            "days_remaining":     max(0, round((r.cooldown_until - now).total_seconds() / 86400, 1))
                                  if r.cooldown_until and r.status == "active" else 0,
            "started_at":         r.cooldown_started_at.isoformat() if r.cooldown_started_at else None,
        }
        for r in rows
    ]


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
        ],
        "skip_threshold": f"{SKIP_THRESHOLD:.0%}",
        "skip_confirm_window_secs": SKIP_CONFIRM_SECS,
        "cooldown_threshold_skips": 3,
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
                "artist_name":      r.artist_name,
                "total_events":     r.total_events,
                "skip_count":       r.skip_count,
                "skip_rate":        float(r.skip_rate),
                "penalty":          float(r.penalty),
                # v2
                "consecutive_skips": r.consecutive_skips,
                "skip_streak_peak":  r.skip_streak_peak,
                "last_skip_at":      r.last_skip_at.isoformat() if r.last_skip_at else None,
                "last_completed_at": r.last_completed_at.isoformat() if r.last_completed_at else None,
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
            "completion_pct": float(r.completion_pct),
            "was_skip":       r.was_skip,
            "received_at":    r.received_at,
            "source_context": r.source_context,
            "session_id":     r.session_id,
        }
        for r in rows
    ]


@router.delete("/penalties/{user_id}")
def clear_penalties(user_id: str, db: Session = Depends(get_db)):
    pc = db.query(SkipPenalty).filter_by(user_id=user_id).delete()
    ec = db.query(PlaybackEvent).filter_by(user_id=user_id).delete()
    db.commit()
    return {"ok": True, "penalties_cleared": pc, "events_cleared": ec}
