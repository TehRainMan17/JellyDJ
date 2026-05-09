"""
Regression tests: audio-feature playlist blocks must exclude soft-deleted
(missing_since IS NOT NULL) tracks.

A prior Jellyfin DB issue left orphaned LibraryTrack rows behind. The audio
block executors (BPM, key, energy, loudness, beat strength, time signature,
acousticness) queried LibraryTrack without filtering missing_since, so
playlists built from these blocks could contain tracks that no longer exist
in Jellyfin.

Run with: docker exec jellydj-backend python -m pytest tests/test_audio_block_missing.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401
from models import LibraryTrack
from services.playlist_blocks import (
    execute_bpm_range_block,
    execute_musical_key_block,
    execute_energy_block,
    execute_loudness_db_block,
    execute_beat_strength_block,
    execute_time_signature_block,
    execute_acousticness_block,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _add(db, jid, missing=False, **fields):
    """Add a LibraryTrack with all audio features populated by default."""
    defaults = dict(
        track_name=f"T{jid}", artist_name="A", album_name="Alb",
        bpm=120,
        musical_key="C Major",
        energy=0.5,
        loudness_db=-14.0,
        beat_strength=0.5,
        time_signature=4,
        acousticness=0.3,
        audio_analyzed_at=datetime.utcnow(),
        audio_analysis_version=1,
    )
    defaults.update(fields)
    db.add(LibraryTrack(
        jellyfin_item_id=jid,
        missing_since=datetime.utcnow() if missing else None,
        **defaults,
    ))


@pytest.fixture
def populated(db):
    """One present track and one missing track that match every audio filter."""
    _add(db, "present")
    _add(db, "missing", missing=True)
    db.commit()
    return db


EMPTY_EXCL = frozenset()


def test_bpm_block_excludes_missing(populated):
    result = execute_bpm_range_block(
        "u1", {"bpm_min": 100, "bpm_max": 140, "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_musical_key_block_excludes_missing(populated):
    result = execute_musical_key_block(
        "u1", {"mode": "major", "notes": [], "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_energy_block_excludes_missing(populated):
    result = execute_energy_block(
        "u1", {"energy_min": 0.0, "energy_max": 1.0, "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_loudness_block_excludes_missing(populated):
    result = execute_loudness_db_block(
        "u1", {"loudness_min": -60, "loudness_max": 0, "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_beat_strength_block_excludes_missing(populated):
    result = execute_beat_strength_block(
        "u1", {"beat_min": 0.0, "beat_max": 1.0, "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_time_signature_block_excludes_missing(populated):
    result = execute_time_signature_block(
        "u1", {"time_sigs": [4], "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}


def test_acousticness_block_excludes_missing(populated):
    result = execute_acousticness_block(
        "u1", {"acousticness_min": 0.0, "acousticness_max": 1.0, "played_filter": "all"},
        populated, EMPTY_EXCL,
    )
    assert result == {"present"}
