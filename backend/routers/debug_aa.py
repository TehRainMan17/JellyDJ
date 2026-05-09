"""
Android Auto telemetry sink.

The mobile app's MediaLibraryService cannot be debugged live in a car. This router
accepts structured lifecycle events from the phone (fire-and-forget POSTs) and
appends them to a JSONL file that can be tailed afterward to diagnose AA bind
failures.

POST /api/debug/aa-event is intentionally unauthenticated — AA bind happens
before/around the JWT refresh path and we cannot afford to lose events when the
token is expired or missing. The endpoint accepts only well-formed JSON of a
known shape and writes a single line per event, capped at 64 KB per body to
prevent abuse.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import UserContext, require_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug"])

LOG_DIR = Path(os.getenv("JELLYDJ_LOG_DIR", "/config/logs"))
LOG_FILE = LOG_DIR / "aa-events.jsonl"
MAX_BODY_BYTES = 64 * 1024
MAX_TAIL_LINES = 2000


class AaEvent(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    ts: str = Field(min_length=1, max_length=64)  # client-side ISO-ish timestamp
    event: str = Field(min_length=1, max_length=64)
    fields: dict[str, Any] = Field(default_factory=dict)


def _ensure_log_dir() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("aa-event log dir unavailable: %s", e)


@router.post("/aa-event")
async def post_aa_event(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "event too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "invalid json")

    # Accept either a single event or a small batch — the client buffers events
    # offline, so the flush path needs to send many in one call.
    events = payload if isinstance(payload, list) else [payload]
    if len(events) > 500:
        raise HTTPException(413, "batch too large")

    _ensure_log_dir()
    received_at = datetime.now(timezone.utc).isoformat()
    client_ip = request.client.host if request.client else "?"
    lines: list[str] = []
    for raw_event in events:
        try:
            ev = AaEvent.model_validate(raw_event)
        except Exception as e:
            log.warning("aa-event rejected: %s", e)
            continue
        record = {
            "received_at": received_at,
            "client_ip": client_ip,
            "session_id": ev.session_id,
            "ts": ev.ts,
            "event": ev.event,
            "fields": ev.fields,
        }
        line = json.dumps(record, separators=(",", ":"), default=str)
        lines.append(line)
        # Mirror to standard logger so `docker compose logs` shows them in real time.
        log.info("AA[%s] %s %s", ev.session_id[:8], ev.event, ev.fields)

    if lines:
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            log.error("aa-event write failed: %s", e)
            # Do not 500 — the client treats failure as "buffer and retry",
            # which would loop forever if the disk is genuinely broken.
    return {"ok": True, "written": len(lines)}


class AaTailLine(BaseModel):
    received_at: str
    client_ip: str
    session_id: str
    ts: str
    event: str
    fields: dict[str, Any]


@router.get("/aa-events/tail", response_model=list[AaTailLine])
def tail_aa_events(
    n: int = Query(200, ge=1, le=MAX_TAIL_LINES),
    session_id: Optional[str] = Query(None),
    _: UserContext = Depends(require_admin),
) -> list[AaTailLine]:
    if not LOG_FILE.exists():
        return []
    # Cheap tail: read whole file (we cap retention by line count below). For a
    # diagnostic file we expect to clear regularly this is fine.
    try:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except Exception as e:
        raise HTTPException(500, f"cannot read log: {e}")

    parsed: list[AaTailLine] = []
    for raw in raw_lines[-MAX_TAIL_LINES:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if session_id and obj.get("session_id") != session_id:
                continue
            parsed.append(AaTailLine(**obj))
        except Exception:
            continue
    return parsed[-n:]


@router.delete("/aa-events")
def clear_aa_events(_: UserContext = Depends(require_admin)):
    try:
        if LOG_FILE.exists():
            LOG_FILE.unlink()
    except Exception as e:
        raise HTTPException(500, f"cannot clear log: {e}")
    return {"ok": True}
