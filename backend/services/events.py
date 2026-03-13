
"""
JellyDJ event logger — writes structured events to the system_events table.
Used by all services so the Dashboard activity feed has real data.

Event types:
  index_complete      — play history + library scan finished
  index_error         — indexer failed for a user
  playlist_generated  — playlist write run completed
  discovery_refreshed — discovery queue populated with new recs
  skip_recorded       — webhook recorded a skip
  track_approved      — user approved a discovery queue item
  track_rejected      — user rejected a discovery queue item
"""
from __future__ import annotations
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def log_event(db, event_type: str, message: str):
    """Write a single event row. Silently no-ops on error so it never breaks callers."""
    try:
        from models import SystemEvent
        db.add(SystemEvent(
            event_type=event_type,
            message=message,
            created_at=datetime.utcnow(),
        ))
        db.commit()
    except Exception as e:
        log.warning(f"log_event failed ({event_type}): {e}")
