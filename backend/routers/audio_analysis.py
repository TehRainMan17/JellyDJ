"""
Audio analysis router — library stats and musical key index.

Endpoints:
  GET /api/audio-analysis/keys   — distinct musical_key values in the library
  GET /api/audio-analysis/stats  — analyzed/pending counts and BPM distribution
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin, UserContext
from database import get_db
from models import LibraryTrack

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio-analysis", tags=["audio-analysis"])


@router.get("/keys")
def list_keys(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all distinct musical_key values present in the analyzed library."""
    rows = (
        db.query(LibraryTrack.musical_key)
        .filter(LibraryTrack.musical_key.isnot(None))
        .distinct()
        .order_by(LibraryTrack.musical_key)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/stats")
def analysis_stats(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return analysis coverage stats and a BPM histogram."""
    total = db.query(LibraryTrack).count()
    analyzed = db.query(LibraryTrack).filter(LibraryTrack.audio_analyzed_at.isnot(None)).count()
    pending = total - analyzed

    # BPM histogram (10-BPM buckets)
    bpm_rows = (
        db.query(LibraryTrack.bpm)
        .filter(LibraryTrack.bpm.isnot(None))
        .all()
    )
    histogram: dict[str, int] = {}
    for (bpm,) in bpm_rows:
        bucket = f"{(bpm // 10) * 10}-{(bpm // 10) * 10 + 9}"
        histogram[bucket] = histogram.get(bucket, 0) + 1

    # Key distribution
    key_rows = (
        db.query(LibraryTrack.musical_key, func.count(LibraryTrack.id))
        .filter(LibraryTrack.musical_key.isnot(None))
        .group_by(LibraryTrack.musical_key)
        .order_by(func.count(LibraryTrack.id).desc())
        .all()
    )
    key_distribution = {k: c for k, c in key_rows}

    return {
        "total": total,
        "analyzed": analyzed,
        "pending": pending,
        "bpm_histogram": histogram,
        "key_distribution": key_distribution,
    }


@router.post("/reindex-all")
def reindex_all_tracks(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Reset audio analysis data on every track so the next analysis run reprocesses
    the entire library with the current algorithm version.  Does not start the job —
    trigger it separately via POST /api/automation/trigger/audio-analysis.
    """
    count = db.query(LibraryTrack).filter(LibraryTrack.audio_analyzed_at.isnot(None)).count()
    db.query(LibraryTrack).update(
        {"audio_analyzed_at": None, "audio_analysis_version": None},
        synchronize_session=False,
    )
    db.commit()
    log.info("Reindex-all: reset audio analysis data for %d tracks", count)
    return {"ok": True, "reset": count}
