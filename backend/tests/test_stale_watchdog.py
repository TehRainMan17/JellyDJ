"""
Tests for the stale-job watchdog (_job_stale_watchdog in scheduler.py).

The watchdog must:
1. Reset any JobState row that has been running=True longer than the threshold.
2. Leave jobs that are still within the threshold alone.
3. Leave jobs that are not running at all alone.
4. Set running=False, update phase with an explanatory message, and set finished_at.
5. No-op (no commit, no crash) when no stale rows exist.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job_state(job_id: str, running: bool, started_at: datetime | None):
    row = MagicMock()
    row.job_id = job_id
    row.running = running
    row.started_at = started_at
    row.phase = "In progress"
    row.finished_at = None
    return row


def _make_db(rows: list):
    """Return a mock session that the watchdog can query."""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stale_job_is_reset():
    """A job running for longer than the threshold must be reset."""
    import scheduler as sched

    now = datetime.now(timezone.utc)
    old_started = now - timedelta(hours=sched._STALE_JOB_THRESHOLD_HOURS + 1)

    stale_row = _make_job_state("cache", running=True, started_at=old_started)
    db = _make_db([stale_row])

    # SessionLocal is imported inside the function — patch it at the source module
    with patch("database.SessionLocal", return_value=db):
        sched._job_stale_watchdog()

    assert stale_row.running is False
    assert "watchdog" in stale_row.phase.lower()
    assert stale_row.finished_at is not None
    db.commit.assert_called_once()


def test_fresh_job_is_not_reset():
    """A job within the threshold must not be touched (DB filter excludes it)."""
    import scheduler as sched

    # Simulate the DB filter correctly excluding fresh rows
    db = _make_db([])

    with patch("database.SessionLocal", return_value=db):
        sched._job_stale_watchdog()

    db.commit.assert_not_called()


def test_no_stale_jobs_no_commit():
    """When there are no stale jobs the watchdog must not commit anything."""
    import scheduler as sched

    db = _make_db([])

    with patch("database.SessionLocal", return_value=db):
        sched._job_stale_watchdog()

    db.commit.assert_not_called()


def test_multiple_stale_jobs_all_reset():
    """All stale jobs in the batch are reset in a single commit."""
    import scheduler as sched

    now = datetime.now(timezone.utc)
    old_started = now - timedelta(hours=sched._STALE_JOB_THRESHOLD_HOURS + 2)

    rows = [
        _make_job_state("enrichment", running=True, started_at=old_started),
        _make_job_state("discovery",  running=True, started_at=old_started),
    ]
    db = _make_db(rows)

    with patch("database.SessionLocal", return_value=db):
        sched._job_stale_watchdog()

    for row in rows:
        assert row.running is False
    db.commit.assert_called_once()


def test_db_error_does_not_crash():
    """If the DB query raises, the watchdog must swallow the error gracefully."""
    import scheduler as sched

    db = MagicMock()
    db.query.side_effect = Exception("SQLite locked")

    with patch("database.SessionLocal", return_value=db):
        # Must not raise
        sched._job_stale_watchdog()

    db.commit.assert_not_called()
    db.close.assert_called()


def test_db_session_always_closed():
    """The DB session must be closed even when the watchdog finds stale jobs."""
    import scheduler as sched

    now = datetime.now(timezone.utc)
    old_started = now - timedelta(hours=sched._STALE_JOB_THRESHOLD_HOURS + 1)
    db = _make_db([_make_job_state("cache", running=True, started_at=old_started)])

    with patch("database.SessionLocal", return_value=db):
        sched._job_stale_watchdog()

    db.close.assert_called()
