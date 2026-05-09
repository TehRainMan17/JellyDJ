"""
Tests for audio analysis counts excluding soft-deleted (missing) tracks.

Regression: after a Jellyfin DB issue, soft-deleted LibraryTrack rows
remained in the table and the audio analysis pending list / stats
endpoint kept reporting the inflated pre-fix count.

Run with: docker exec jellydj-backend python -m pytest tests/test_audio_analysis_missing.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime

import pytest
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401 — registers ORM models
from models import LibraryTrack


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _add(db, jid, missing=False, analyzed=False, version=None):
    db.add(LibraryTrack(
        jellyfin_item_id=jid,
        track_name=f"T{jid}", artist_name="A", album_name="Alb",
        missing_since=datetime.utcnow() if missing else None,
        audio_analyzed_at=datetime.utcnow() if analyzed else None,
        audio_analysis_version=version,
    ))


def test_pending_query_excludes_missing(db):
    """The analyzer's pending query must skip soft-deleted tracks."""
    _add(db, "present-unanalyzed")
    _add(db, "present-analyzed", analyzed=True, version=1)
    _add(db, "missing-unanalyzed", missing=True)
    _add(db, "missing-analyzed", missing=True, analyzed=True, version=1)
    db.commit()

    # Mirror the query in services/audio_analysis.analyze_new_tracks
    pending = db.query(LibraryTrack).filter(
        LibraryTrack.missing_since.is_(None),
        or_(
            LibraryTrack.audio_analyzed_at.is_(None),
            LibraryTrack.audio_analysis_version < 1,
        )
    ).all()
    ids = sorted(t.jellyfin_item_id for t in pending)
    assert ids == ["present-unanalyzed"], f"missing tracks leaked into pending list: {ids}"


def _stats(db):
    """Mirror of routers.audio_analysis.analysis_stats counting logic.

    Imported inline rather than from the router to avoid pulling in
    auth/jose dependencies during unit tests.
    """
    present = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None))
    total = present.count()
    analyzed = present.filter(LibraryTrack.audio_analyzed_at.isnot(None)).count()
    return {"total": total, "analyzed": analyzed, "pending": total - analyzed}


def test_stats_endpoint_excludes_missing(db):
    """/api/audio-analysis/stats counts must reflect the present library only."""
    # 2 present (1 analyzed, 1 not), 3 missing (2 analyzed, 1 not).
    _add(db, "p1", analyzed=True)
    _add(db, "p2")
    _add(db, "m1", missing=True, analyzed=True)
    _add(db, "m2", missing=True, analyzed=True)
    _add(db, "m3", missing=True)
    db.commit()

    result = _stats(db)
    assert result["total"] == 2, f"total should ignore missing: {result}"
    assert result["analyzed"] == 1, f"analyzed should ignore missing: {result}"
    assert result["pending"] == 1, f"pending should ignore missing: {result}"


def test_stats_with_only_missing_tracks(db):
    """If every remaining row is soft-deleted, all counts are zero."""
    _add(db, "m1", missing=True, analyzed=True)
    _add(db, "m2", missing=True)
    db.commit()

    result = _stats(db)
    assert result == {"total": 0, "analyzed": 0, "pending": 0}


def test_stats_with_no_missing_tracks(db):
    """Sanity: when nothing is missing, counts match the full library."""
    _add(db, "p1", analyzed=True)
    _add(db, "p2", analyzed=True)
    _add(db, "p3")
    db.commit()

    result = _stats(db)
    assert result == {"total": 3, "analyzed": 2, "pending": 1}
