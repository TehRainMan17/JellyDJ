
"""
JellyDJ Webhook receiver — v2.1

Changes from v2:
  - BUG FIX: item_id is now normalised (dashes stripped, lowercased) the same
    way user_id is.  Some 3rd-party clients (e.g. Manet on iOS) inconsistently
    include or omit dashes in the UUID they report, causing every heartbeat to
    look like a track transition and write a false skip.  This was the primary
    cause of wildly inflated skip counts (e.g. 173 skips for 2 actual plays).

  - BUG FIX: _finalize_progress_track now requires a minimum accumulated
    wall-clock listen time (MIN_LISTEN_SECS, default 8 s) before it will
    write *any* event.  This prevents stale in-memory entries left over from
    a server restart from producing phantom skips on the next track transition.

  - BUG FIX: Progress-only (Manet-style) skips now go through the same
    SKIP_CONFIRM_SECS pending window as standard-client skips.  Previously
    they were written immediately, so pausing then abandoning a song always
    counted as a skip.

  - BUG FIX: _playback_progress wall-clock timestamp is no longer updated on
    every heartbeat.  It now preserves the *start* wall time so that elapsed-
    time calculations remain accurate across the full listen session.

  - IMPROVEMENT: _active_item is now cleaned up after a confirmed transition
    so that a second identical "transition" heartbeat from a slow client can't
    re-trigger finalization for the same old item.

Changes from v1 (retained from v2):
  - SkipPenalty tracks consecutive_skips (streak) and resets on completion
  - check_and_apply_cooldown() called on skip; cooldown at streak >= 3
  - PlaybackEvent stores source_context and session_id
  - Session ID derived from (user_id + day)

Skip detection strategy:

  STANDARD clients (Android, web, Swiftfin …):
    PlaybackStart → PlaybackProgress → PlaybackStop.
    Completion measured via server-side wall-clock timing (most accurate).

  PROGRESS-ONLY clients (Manet on iOS, some others):
    Only PlaybackProgress heartbeats (~1 s interval, IsAutomated=True).
    Track transition detected when ItemId changes for the same user.
    Completion measured via max position reached on previous track.
    Transitions that look like skips enter the same SKIP_CONFIRM_SECS
    pending window as standard-client stops.

  SKIP CONFIRMATION (all clients):
    A stop/transition that looks like a skip is held PENDING for
    SKIP_CONFIRM_SECS (10 s).  Confirmed by the user's next playback
    event; discarded (not penalised) if silence follows.

  MAX-POSITION tracking:
    Both paths track the PEAK position reached, not the last reported
    position, to protect against false skips on backward seeks.

Cooldown rules (v2, unchanged):
  - consecutive_skips tracks skips in a row without a completion
  - Completion resets consecutive_skips to 0
  - When consecutive_skips >= 3: check_and_apply_cooldown() fires
  - Cooldown durations: 1st=7 d, 2nd=14 d, 3rd=30 d, 4th+=permanent
  - Favourites shielded: consecutive_skips capped at 2
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from auth import require_admin, UserContext
from database import get_db
from models import PlaybackEvent, SkipPenalty, ManagedUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _verify_webhook_secret(request: Request) -> None:
    """
    Enforce the WEBHOOK_SECRET shared secret on every inbound webhook request.

    Default behaviour — WEBHOOK_SECRET is not set:
      Requests are REJECTED with HTTP 401.  An unauthenticated webhook endpoint
      allows any internet host to inject playback events, corrupt listening
      history, and manipulate skip counts and cooldowns.

    To configure authentication (recommended for all deployments):
      1. Generate a secret:
           python -c "import secrets; print(secrets.token_urlsafe(32))"
      2. Add to .env:
           WEBHOOK_SECRET=<value>
      3. In Jellyfin → Webhook plugin → add a header:
           X-Jellyfin-Token: <same value>

    To opt out for a fully private LAN deployment where you have decided no
    secret is needed, set this in .env:
      WEBHOOK_SECRET_REQUIRED=false

    Token lookup (for compatibility across Jellyfin plugin versions):
      1. X-Jellyfin-Token request header  (preferred)
      2. ?token= query parameter           (fallback for older plugin versions)
    """
    expected = os.getenv("WEBHOOK_SECRET", "").strip()

    if not expected:
        # No secret set — block unless the operator has explicitly opted out.
        required_env = os.getenv("WEBHOOK_SECRET_REQUIRED", "true").strip().lower()
        if required_env not in ("false", "0", "no"):
            log.warning(
                "Webhook request blocked: WEBHOOK_SECRET is not configured. "
                "Set WEBHOOK_SECRET in .env, or set WEBHOOK_SECRET_REQUIRED=false "
                "to allow unauthenticated webhooks on a private LAN."
            )
            raise HTTPException(
                status_code=401,
                detail=(
                    "Webhook authentication is not configured. "
                    "Set WEBHOOK_SECRET in your .env, then add the same value "
                    "as the X-Jellyfin-Token header in the Jellyfin Webhook plugin. "
                    "For private LAN installs without a secret, set "
                    "WEBHOOK_SECRET_REQUIRED=false."
                ),
            )
        log.debug("Webhook received; secret check skipped (WEBHOOK_SECRET_REQUIRED=false)")
        return

    provided = (
        request.headers.get("X-Jellyfin-Token", "")
        or request.query_params.get("token", "")
    )
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        log.warning(
            "Webhook rejected — bad or missing secret from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

# ── Skip detection thresholds ────────────────────────────────────────────────

SKIP_THRESHOLD          = 0.80
MAX_PENALTY             = 0.60
MIN_EVENTS_FOR_PENALTY  = 1
DEDUP_WINDOW_SECONDS    = 10
SKIP_CONFIRM_SECS       = 10
SESSION_END_SECS        = 60
MIN_POSITION_TICKS      = 5_000_000   # 0.5 s — ignore sub-half-second pings

# Minimum wall-clock seconds that must have elapsed (since we first saw the
# item) before we will write ANY event for it.  Prevents stale in-memory
# entries (e.g. from a server restart mid-song) producing phantom skips.
MIN_LISTEN_SECS         = 8

# Favourites shield: cap consecutive skips for a favourited track so a couple
# of bad-day skips can never trigger cooldown.
FAVORITE_CONSECUTIVE_SKIP_CAP = 2


# ── In-memory state ───────────────────────────────────────────────────────────

_playback_starts:   dict = {}   # key -> (wall_start, runtime_ticks, track_name)
_playback_progress: dict = {}   # key -> (wall_start, max_pos, runtime_ticks, p)
_active_item:       dict = {}   # uid  -> normalised item_id
_pending_skips:     dict = {}   # uid  -> {p, completion, method, wall_time}
_recent_stops:      dict = {}   # key  -> wall_time  (dedup)
_recent_errors:     list = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_id(raw: str) -> str:
    """Normalise a Jellyfin UUID: strip dashes, lowercase."""
    return raw.replace("-", "").lower()


def _calc_penalty(skip_count: int, total_events: int) -> float:
    import math
    if total_events < MIN_EVENTS_FOR_PENALTY:
        return 0.0
    skip_rate = skip_count / total_events
    return round(min(MAX_PENALTY, MAX_PENALTY * (1 - math.exp(-3 * skip_rate))), 4)


def _session_id(user_id: str) -> str:
    day = datetime.utcnow().strftime("%Y-%m-%d")
    return hashlib.md5(f"{user_id}:{day}".encode()).hexdigest()[:16]


def _parse_body(body: dict) -> Optional[dict]:
    item    = body.get("Item") or {}
    session = body.get("Session") or {}

    raw_item_id = item.get("Id") or body.get("ItemId") or body.get("Id")
    raw_user_id = session.get("UserId") or body.get("UserId")
    if not raw_item_id or not raw_user_id:
        return None

    # ── FIX: normalise *both* IDs consistently ────────────────────────────
    item_id = _norm_id(str(raw_item_id))
    user_id = _norm_id(str(raw_user_id))

    runtime  = int(item.get("RunTimeTicks")  or body.get("RunTimeTicks")  or 0)
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
        "item_id":      item_id,
        "user_id":      user_id,
        "track_name":   item.get("Name")  or body.get("Name")  or "",
        "artist_name":  artist,
        "album_name":   item.get("Album") or body.get("Album") or "",
        "genre":        genre,
        "runtime_ticks":          runtime,
        "position_ticks":         position,
        "played_to_completion":   bool(body.get("PlayedToCompletion") or False),
        "is_automated":           bool(body.get("IsAutomated") or False),
        "is_favorite":            False,   # filled in by callers if needed
    }


def _is_managed(user_id: str, db: Session) -> bool:
    normalised = _norm_id(user_id)
    result = db.query(ManagedUser).filter_by(
        jellyfin_user_id=normalised, has_activated=True
    ).first()
    if not result:
        all_activated = db.query(ManagedUser).filter_by(has_activated=True).all()
        result = next(
            (u for u in all_activated
             if _norm_id(u.jellyfin_user_id) == normalised),
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
            (u for u in all_users if _norm_id(u.jellyfin_user_id) == normalised),
            None,
        )
        if exists:
            log.warning(f"Webhook from known user '{exists.username}' but not yet activated — skipping")
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
            source_context=p.get("source_context"),
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
            row.skip_count        = (row.skip_count or 0) + 1
            row.consecutive_skips = (row.consecutive_skips or 0) + 1
            row.last_skip_at      = now

            if p.get("is_favorite") and row.consecutive_skips > FAVORITE_CONSECUTIVE_SKIP_CAP:
                row.consecutive_skips = FAVORITE_CONSECUTIVE_SKIP_CAP

            row.skip_streak_peak = max(row.skip_streak_peak or 0, row.consecutive_skips)
        else:
            row.consecutive_skips  = 0
            row.last_completed_at  = now

        skip_rate  = (row.skip_count or 0) / row.total_events
        row.skip_rate  = str(round(skip_rate, 4))
        row.penalty    = str(_calc_penalty(row.skip_count, row.total_events))
        row.updated_at = now
        db.flush()

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

            # Artist-level timeout: if the user skips enough distinct tracks
            # by the same artist within a short window, mute the whole artist.
            try:
                from services.enrichment import check_and_apply_artist_cooldown
                check_and_apply_artist_cooldown(
                    db=db,
                    user_id=p["user_id"],
                    artist_name=p["artist_name"],
                )
            except Exception as ae:
                log.warning(f"  Artist cooldown check failed: {ae}")

        db.commit()

        outcome = "SKIP" if is_skip else "PLAYED"
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
            "at":       datetime.utcnow().isoformat(),
            "user_id":  p["user_id"],
            "track":    p["track_name"],
            "error":    f"{type(exc).__name__}: {exc}",
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
            f"NOT penalising  user={uid[:8]}  '{entry['p']['track_name']}'"
        )


def _finalize_progress_track(user_id: str, old_item_id: str, db: Session):
    """
    Called when a progress-only client moves to a new track.

    FIX v2.1:
      - Requires MIN_LISTEN_SECS of accumulated wall-clock time before writing
        anything.  This discards stale entries left in memory after a server
        restart (which would have no real position data).
      - Transitions that look like skips now enter _pending_skips instead of
        being written immediately.  This gives the same confirmation window
        that standard clients get, so pausing then abandoning a song is NOT
        penalised as a skip.
    """
    key   = f"{user_id}::{old_item_id}"
    entry = _playback_progress.pop(key, None)
    if not entry:
        return

    wall_start, max_pos, runtime, p = entry

    # Guard: ignore entries with insufficient listen data
    elapsed_wall = time.time() - wall_start
    if elapsed_wall < MIN_LISTEN_SECS or max_pos < MIN_POSITION_TICKS:
        log.debug(
            f"  Progress finalize: discarding '{p['track_name']}' — "
            f"only {elapsed_wall:.1f}s wall / {max_pos//10_000_000}s ticks accumulated "
            f"(server restart guard or genuine sub-{MIN_LISTEN_SECS}s play)"
        )
        return

    if runtime <= 0:
        log.debug(f"  Progress finalize: skipping '{p['track_name']}' — no runtime info")
        return

    completion = min(1.0, max_pos / runtime)
    is_skip    = completion < SKIP_THRESHOLD

    log.info(
        f"Playback TRANSITION (progress-only)  user={user_id[:8]}  "
        f"'{p['track_name']}'  max_pos={max_pos//10_000_000}s / {runtime//10_000_000}s "
        f"= {completion:.0%}  → {'pending skip confirmation' if is_skip else 'PLAYED'}"
    )

    if not is_skip:
        _write_event(db, p, completion, "progress_transition", is_skip=False)
        return

    # ── FIX: route potential skips through the same confirmation window ───
    # Only commit the skip if another track starts within SKIP_CONFIRM_SECS.
    # This prevents paused/abandoned sessions from always counting as skips.
    _pending_skips[user_id] = {
        "p":          p,
        "completion": completion,
        "method":     "progress_transition",
        "wall_time":  time.time(),
    }
    log.info(
        f"  Skip PENDING confirmation  user={user_id[:8]}  '{p['track_name']}'  "
        f"{completion:.0%} — waiting {SKIP_CONFIRM_SECS}s for next playback event"
    )


# ── Event handlers ────────────────────────────────────────────────────────────

def handle_start(body: dict, db: Session):
    p = _parse_body(body)
    if not p or not _is_managed(p["user_id"], db):
        return

    _expire_pending_skips(db)

    uid = p["user_id"]
    key = f"{uid}::{p['item_id']}"

    # Confirm any pending skip for this user (they started a new track)
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

    # ── FIX: preserve wall_start — do NOT reset it on every progress tick ─
    # Initialise the progress entry only if it doesn't already exist for this key.
    if key not in _playback_progress:
        _playback_progress[key] = (time.time(), 0, p["runtime_ticks"], p)

    _playback_starts[key] = (time.time(), p["runtime_ticks"], p["track_name"])
    _active_item[uid]     = p["item_id"]

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
        # ── FIX: clear _active_item immediately after finalization so a
        #    second heartbeat with the old item_id can't re-trigger it ──────
        if _active_item.get(uid) == prev_item_id:
            del _active_item[uid]

    _active_item[uid] = p["item_id"]

    existing    = _playback_progress.get(key)
    current_max = existing[1] if existing else 0
    new_max     = max(current_max, p["position_ticks"]) if p["position_ticks"] >= MIN_POSITION_TICKS else current_max

    # ── FIX: preserve the original wall_start from when we first saw this
    #    item; only update max_pos and (if missing) runtime_ticks ───────────
    if existing:
        wall_start, _old_max, old_runtime, _old_p = existing
        runtime = p["runtime_ticks"] or old_runtime
        _playback_progress[key] = (wall_start, new_max, runtime, p)
    else:
        _playback_progress[key] = (now, new_max, p["runtime_ticks"], p)

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
        or (start_entry[1]    if start_entry    else 0)
        or (progress_entry[2] if progress_entry else 0)
    )

    if p["played_to_completion"]:
        log.info(f"Playback STOP  user={uid[:8]}  '{p['track_name']}' — 100% (completion flag) → PLAYED")
        _write_event(db, p, 1.0, "played_to_completion_flag", is_skip=False)
        return

    if start_entry and runtime_ticks > 0:
        wall_start, _rt, _ = start_entry
        elapsed_ticks = (now - wall_start) * 10_000_000
        completion    = min(1.0, elapsed_ticks / runtime_ticks)
        method        = "server_timing"

    elif progress_entry and runtime_ticks > 0:
        # Use max_pos directly — don't add extra wall-clock time here as
        # it can inflate completion on paused tracks.
        _wall_start, max_pos, _rt, _ = progress_entry
        completion = min(1.0, max_pos / runtime_ticks)
        method     = "progress_heartbeat"

    else:
        completion = 0.0
        method     = "ambiguous"

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
    _verify_webhook_secret(request)
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
        elif "item" in raw_type and ("added" in raw_type or "new" in raw_type):
            # New item indexed in Jellyfin — check if any imported playlists need updating
            item_id = (
                body.get("ItemId") or
                body.get("Id") or
                (body.get("Item") or {}).get("Id")
            )
            if item_id:
                try:
                    from services.playlist_import import on_jellyfin_item_added
                    updated = await on_jellyfin_item_added(item_id, db)
                    if updated:
                        log.info("item.added: updated %d imported playlist(s) with new item %s", updated, item_id[:8])
                except Exception as imp_exc:
                    log.debug("item.added import handler skipped: %s", imp_exc)
            return {"ok": True, "processed": True, "event": "item_added"}
        else:
            return {"ok": True, "processed": False, "reason": f"'{raw_type}' ignored"}
    except Exception as e:
        log.error(f"Webhook error ({raw_type}): {e}")
        return {"ok": True, "processed": False, "error": str(e)}


# ── Diagnostic / utility endpoints ───────────────────────────────────────────

_debug_captures: list = []

@router.post("/jellyfin/debug")
async def debug_capture(request: Request, _: UserContext = Depends(require_admin)):
    raw  = await request.body()
    body = {}
    try:
        body = json.loads(raw)
    except Exception:
        pass
    session = body.get("Session") or {}
    item    = body.get("Item")    or {}
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
    print(
        f"DEBUG: type={body.get('NotificationType')} "
        f"track={body.get('Name')} "
        f"pos={body.get('PlaybackPositionTicks')}",
        flush=True,
    )
    return {"received": True, "capture": capture}


@router.get("/jellyfin/debug")
async def get_debug(_: UserContext = Depends(require_admin)):
    return {"count": len(_debug_captures), "captures": _debug_captures}


@router.get("/pending-starts")
def pending_starts(_: UserContext = Depends(require_admin)):
    now = time.time()
    return {
        k: {
            "track_name":          v[2],
            "started_ago_seconds": round(now - v[0], 1),
            "runtime_seconds":     round(v[1] / 10_000_000, 0),
        }
        for k, v in _playback_starts.items()
    }


@router.get("/pending-skips")
def pending_skips_diagnostic(_: UserContext = Depends(require_admin)):
    now = time.time()
    return {
        uid: {
            "track_name":      entry["p"]["track_name"],
            "completion":      f"{entry['completion']:.0%}",
            "method":          entry["method"],
            "waiting_secs":    round(now - entry["wall_time"], 1),
            "expires_in_secs": round(SKIP_CONFIRM_SECS - (now - entry["wall_time"]), 1),
        }
        for uid, entry in _pending_skips.items()
    }


@router.get("/managed-users")
def managed_users_diagnostic(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    from models import PlaybackEvent, SkipPenalty
    from sqlalchemy import desc

    users  = db.query(ManagedUser).all()
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

        from models import TrackCooldown
        active_cooldowns   = db.query(TrackCooldown).filter_by(user_id=uid, status="active").count()
        permanent_dislikes = db.query(TrackCooldown).filter_by(user_id=uid, status="permanent").count()

        pending_start_keys    = [k for k in _playback_starts    if k.startswith(uid)]
        pending_progress_keys = [k for k in _playback_progress  if k.startswith(uid)]
        pending_skip          = _pending_skips.get(uid)

        if total_events == 0:
            diagnosis = "No events in DB — webhook user_id mismatch or events not reaching server"
        elif skip_events == 0 and total_events > 5:
            diagnosis = f"{total_events} events but 0 skips — progress-only client (Manet)"
        else:
            diagnosis = (
                f"{total_events} events, {skip_events} skips "
                f"({100 * skip_events // total_events if total_events else 0}%) — OK"
            )

        result.append({
            "username":            u.username,
            "jellyfin_user_id":    uid,
            "has_activated":       u.has_activated,
            "db_total_events":     total_events,
            "db_skip_events":      skip_events,
            "db_ambiguous_events": ambiguous,
            "last_event_track":    last_event.track_name    if last_event else None,
            "last_event_was_skip": last_event.was_skip      if last_event else None,
            "last_event_at":       last_event.received_at.isoformat() if last_event else None,
            "in_memory_pending_starts":   len(pending_start_keys),
            "in_memory_pending_progress": len(pending_progress_keys),
            "in_memory_pending_skip":     bool(pending_skip),
            "active_cooldowns":    active_cooldowns,
            "permanent_dislikes":  permanent_dislikes,
            "diagnosis":           diagnosis,
        })

    return {
        "managed_users":            result,
        "in_memory_active_starts":  len(_playback_starts),
        "in_memory_pending_skips":  len(_pending_skips),
        "recent_db_errors":         _recent_errors[-5:],
    }


@router.get("/cooldowns/{user_id}")
def get_cooldowns(user_id: str, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
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
            "track_name":     r.track_name,
            "artist_name":    r.artist_name,
            "status":         r.status,
            "cooldown_count": r.cooldown_count,
            "skip_streak":    r.skip_streak_at_trigger,
            "cooldown_until": r.cooldown_until.isoformat() if r.cooldown_until else None,
            "days_remaining": max(0, round((r.cooldown_until - now).total_seconds() / 86400, 1))
                              if r.cooldown_until and r.status == "active" else 0,
            "started_at":     r.cooldown_started_at.isoformat() if r.cooldown_started_at else None,
        }
        for r in rows
    ]


@router.get("/setup-guide")
def setup_guide(request: Request, _: UserContext = Depends(require_admin)):
    """
    Return webhook configuration instructions for the admin setup UI.
    Requires admin auth — the webhook URL and secret status are internal details.
    """
    host = request.headers.get("host", "localhost:7879")
    url  = f"http://{host}/api/webhooks/jellyfin"
    secret_configured = bool(os.getenv("WEBHOOK_SECRET", "").strip())
    secret_required   = os.getenv("WEBHOOK_SECRET_REQUIRED", "true").strip().lower()                         not in ("false", "0", "no")
    step7 = (
        "7. Add a header — Name: X-Jellyfin-Token  Value: <your WEBHOOK_SECRET>"
        if secret_configured
        else (
            "7. WARNING: WEBHOOK_SECRET is not set — webhooks are currently BLOCKED. "
            "Set WEBHOOK_SECRET in .env and add it as the X-Jellyfin-Token header."
            if secret_required
            else
            "7. No secret configured (WEBHOOK_SECRET_REQUIRED=false) — "
            "webhooks accepted from any source. Recommended for private LAN only."
        )
    )
    return {
        "webhook_url": url,
        "secret_configured": secret_configured,
        "secret_required": secret_required,
        "instructions": [
            "1. Jellyfin → Dashboard → Plugins → Webhook",
            "2. Click 'Add Generic Destination'",
            f"3. URL: {url}",
            "4. Request Type: POST",
            "5. Notification Types: enable 'Playback Start', 'Playback Progress', AND 'Playback Stop'",
            "6. Item Type: Songs",
            step7,
        ],
        "skip_threshold":           f"{SKIP_THRESHOLD:.0%}",
        "skip_confirm_window_secs": SKIP_CONFIRM_SECS,
        "cooldown_threshold_skips": 3,
    }


@router.get("/stats/{user_id}")
def skip_stats(user_id: str, limit: int = 50, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(SkipPenalty)
        .filter_by(user_id=user_id)
        .filter(SkipPenalty.total_events >= MIN_EVENTS_FOR_PENALTY)
        .order_by(SkipPenalty.skip_rate.desc())
        .limit(limit).all()
    )
    return {
        "user_id":       user_id,
        "total_tracked": db.query(SkipPenalty).filter_by(user_id=user_id).count(),
        "items": [
            {
                "jellyfin_item_id":   r.jellyfin_item_id,
                "artist_name":        r.artist_name,
                "total_events":       r.total_events,
                "skip_count":         r.skip_count,
                "skip_rate":          float(r.skip_rate),
                "penalty":            float(r.penalty),
                "consecutive_skips":  r.consecutive_skips,
                "skip_streak_peak":   r.skip_streak_peak,
                "last_skip_at":       r.last_skip_at.isoformat() if r.last_skip_at else None,
                "last_completed_at":  r.last_completed_at.isoformat() if r.last_completed_at else None,
            }
            for r in rows
        ],
    }


@router.get("/recent/{user_id}")
def recent_events(user_id: str, limit: int = 20, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
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
def clear_penalties(user_id: str, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    pc = db.query(SkipPenalty).filter_by(user_id=user_id).delete()
    ec = db.query(PlaybackEvent).filter_by(user_id=user_id).delete()
    db.commit()
    return {"ok": True, "penalties_cleared": pc, "events_cleared": ec}
