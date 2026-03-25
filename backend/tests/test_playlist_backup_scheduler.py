"""
Tests for playlist_backup_scheduler:
- reschedule_backup_job: start_date derived from last_auto_backup_at so
  container restarts don't reset the interval clock.
- _async_auto_backup: only backs up enrolled (existing PlaylistBackup record),
  non-excluded, non-managed playlists.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(
    *,
    enabled: bool = True,
    interval_hours: int = 6,
    last_backup: datetime | None = None,
):
    s = MagicMock()
    s.auto_backup_enabled = enabled
    s.auto_backup_interval_hours = interval_hours
    s.last_auto_backup_at = last_backup
    return s


def _make_db(settings):
    db = MagicMock()
    db.query.return_value.first.return_value = settings
    return db


# ---------------------------------------------------------------------------
# reschedule_backup_job
# ---------------------------------------------------------------------------

class TestRescheduleBackupJob:
    """reschedule_backup_job must preserve the backup cadence across restarts."""

    def _run(self, db, fake_scheduler, fake_session_local, fake_now):
        """Import and call reschedule_backup_job with all external deps mocked."""
        # Build a minimal fake module environment so the function's imports work
        fake_models = types.ModuleType("models")
        fake_models.PlaylistBackupSettings = MagicMock

        fake_apscheduler_triggers_interval = types.ModuleType(
            "apscheduler.triggers.interval"
        )
        captured = {}

        class CapturingIntervalTrigger:
            def __init__(self, hours, start_date=None):
                captured["hours"] = hours
                captured["start_date"] = start_date

        fake_apscheduler_triggers_interval.IntervalTrigger = CapturingIntervalTrigger

        fake_scheduler_mod = types.ModuleType("scheduler")
        fake_scheduler_mod.scheduler = fake_scheduler

        fake_database_mod = types.ModuleType("database")
        fake_database_mod.SessionLocal = fake_session_local

        modules = {
            "models": fake_models,
            "apscheduler.triggers.interval": fake_apscheduler_triggers_interval,
            "apscheduler": types.ModuleType("apscheduler"),
            "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
            "scheduler": fake_scheduler_mod,
            "database": fake_database_mod,
        }

        import importlib, sys

        # Temporarily inject fakes
        originals = {k: sys.modules.get(k) for k in modules}
        sys.modules.update(modules)
        try:
            # Re-import to pick up fakes
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "playlist_backup_scheduler",
                "playlist_backup_scheduler.py",
            )
            mod = importlib.util.module_from_spec(spec)
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                spec.loader.exec_module(mod)
                mod.reschedule_backup_job(db)
        finally:
            for k, v in originals.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        return captured

    # -- simpler approach: patch inside the already-loaded module ------------

    def _call(self, db, fake_now):
        """
        Call the already-loaded reschedule_backup_job with datetime.now patched.
        """
        import sys, types

        # Provide stub modules that the function imports at call-time
        fake_models = types.ModuleType("models")
        fake_models.PlaylistBackupSettings = object  # just needs to exist

        captured_trigger = {}

        class CapturingIntervalTrigger:
            def __init__(self, hours, start_date=None):
                captured_trigger["hours"] = hours
                captured_trigger["start_date"] = start_date
                self._hours = hours
                self._start_date = start_date

        fake_apscheduler_pkg = types.ModuleType("apscheduler")
        fake_apscheduler_triggers = types.ModuleType("apscheduler.triggers")
        fake_apscheduler_interval = types.ModuleType("apscheduler.triggers.interval")
        fake_apscheduler_interval.IntervalTrigger = CapturingIntervalTrigger

        fake_scheduler_obj = MagicMock()
        fake_scheduler_mod = types.ModuleType("scheduler")
        fake_scheduler_mod.scheduler = fake_scheduler_obj

        fake_session_local = MagicMock()
        fake_database_mod = types.ModuleType("database")
        fake_database_mod.SessionLocal = fake_session_local

        module_map = {
            "models": fake_models,
            "apscheduler": fake_apscheduler_pkg,
            "apscheduler.triggers": fake_apscheduler_triggers,
            "apscheduler.triggers.interval": fake_apscheduler_interval,
            "scheduler": fake_scheduler_mod,
            "database": fake_database_mod,
        }

        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "_pbs_test",
            os.path.join(os.path.dirname(__file__), "..", "playlist_backup_scheduler.py"),
        )
        mod = importlib.util.module_from_spec(spec)

        originals = {k: sys.modules.get(k) for k in module_map}
        sys.modules.update(module_map)
        try:
            with patch(
                "playlist_backup_scheduler.datetime",
                wraps=datetime,
            ):
                # Patch datetime inside the freshly loaded module
                spec.loader.exec_module(mod)
                with patch.object(mod, "datetime") as mock_dt_cls:
                    mock_dt_cls.now.return_value = fake_now
                    # Allow construction of real datetime objects for tzinfo checks
                    mock_dt_cls.side_effect = lambda *a, **kw: datetime(*a, **kw)
                    mod.reschedule_backup_job(db)
        finally:
            for k, v in originals.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        return captured_trigger, fake_scheduler_obj

    # -----------------------------------------------------------------------
    # Tests
    # -----------------------------------------------------------------------

    def test_disabled_removes_job(self):
        """When auto_backup_enabled=False, the scheduler job is removed."""
        settings = _make_settings(enabled=False)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

        _, fake_scheduler = self._call(db, now)
        fake_scheduler.remove_job.assert_called_once()
        fake_scheduler.add_job.assert_not_called()

    def test_no_last_backup_fires_soon(self):
        """With no prior backup, next run should be ~2 min from now."""
        settings = _make_settings(enabled=True, interval_hours=6, last_backup=None)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

        captured, fake_scheduler = self._call(db, now)
        fake_scheduler.add_job.assert_called_once()
        start_date = captured["start_date"]
        assert start_date is not None
        delta = (start_date - now).total_seconds()
        assert 0 < delta <= 180, f"Expected ~2 min, got {delta}s"

    def test_recent_backup_preserves_cadence(self):
        """A backup 3h ago with 6h interval → next run in ~3h, clock not reset."""
        last = datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)
        settings = _make_settings(enabled=True, interval_hours=6, last_backup=last)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)  # 3h later

        captured, fake_scheduler = self._call(db, now)
        fake_scheduler.add_job.assert_called_once()
        start_date = captured["start_date"]
        # Expected: last + 6h = 15:00 UTC
        expected = datetime(2026, 3, 24, 15, 0, tzinfo=timezone.utc)
        assert abs((start_date - expected).total_seconds()) < 5, (
            f"Expected next run ~{expected}, got {start_date}"
        )

    def test_overdue_backup_fires_soon(self):
        """A backup overdue by 2 days → next run in ~2 min, not 6h from now."""
        last = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)  # 2 days ago
        settings = _make_settings(enabled=True, interval_hours=6, last_backup=last)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

        captured, fake_scheduler = self._call(db, now)
        fake_scheduler.add_job.assert_called_once()
        start_date = captured["start_date"]
        # Must be soon (≤3 min), NOT 6h in the future
        delta = (start_date - now).total_seconds()
        assert 0 < delta <= 180, (
            f"Overdue backup should fire in ~2 min, got {delta}s. "
            "This is the regression: interval clock was being reset on restart."
        )

    def test_naive_last_backup_handled(self):
        """A naive (no tzinfo) last_auto_backup_at is treated as UTC."""
        last = datetime(2026, 3, 24, 10, 0)  # naive
        settings = _make_settings(enabled=True, interval_hours=6, last_backup=last)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

        captured, fake_scheduler = self._call(db, now)
        # Should not raise, start_date should be computed (16:00 UTC)
        start_date = captured["start_date"]
        expected = datetime(2026, 3, 24, 16, 0, tzinfo=timezone.utc)
        assert abs((start_date - expected).total_seconds()) < 5

    def test_interval_hours_passed_to_trigger(self):
        """The interval_hours value from settings is forwarded to IntervalTrigger."""
        settings = _make_settings(enabled=True, interval_hours=12, last_backup=None)
        db = _make_db(settings)
        now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)

        captured, _ = self._call(db, now)
        assert captured["hours"] == 12


# ---------------------------------------------------------------------------
# _async_auto_backup — enrollment / filtering
# ---------------------------------------------------------------------------

class TestAsyncAutoBackup:
    """
    _async_auto_backup must only back up playlists that are:
      - enrolled  (existing PlaylistBackup row)
      - not excluded (exclude_from_auto=False)
      - not managed by JellyDJ
    """

    def _run(self, playlists_from_jellyfin, backup_rows, managed_ids=None, managed_names=None):
        """
        Run _async_auto_backup with mocked DB and helpers.
        Returns the list of playlist IDs that _do_backup_playlist was called with.
        """
        import asyncio, sys, types, importlib.util, os

        managed_ids = managed_ids or set()
        managed_names = managed_names or set()

        # --- fake settings row (enabled) ---
        fake_settings = MagicMock()
        fake_settings.auto_backup_enabled = True
        fake_settings.last_auto_backup_at = None

        # --- fake connection row ---
        fake_conn = MagicMock()
        fake_conn.base_url = "http://jf"
        fake_conn.api_key_encrypted = b"enc"

        # --- DB mock ---
        backed_up_ids = []

        def query_side_effect(model_cls):
            q = MagicMock()
            name = getattr(model_cls, "__name__", str(model_cls))
            if "PlaylistBackupSettings" in name:
                q.first.return_value = fake_settings
            elif "ConnectionSettings" in name:
                q.filter_by.return_value.first.return_value = fake_conn
            elif "PlaylistBackup" in name:
                def filter_by_side(**kw):
                    pid = kw.get("jellyfin_playlist_id")
                    row = backup_rows.get(pid)
                    r = MagicMock()
                    r.first.return_value = row
                    return r
                q.filter_by.side_effect = filter_by_side
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        # --- track calls to _do_backup_playlist ---
        async def fake_do_backup(db_, bu, ak, uid, pid, name, force=False):
            backed_up_ids.append(pid)

        # --- stub modules ---
        fake_models = types.ModuleType("models")
        fake_models.PlaylistBackupSettings = type("PlaylistBackupSettings", (), {})
        fake_models.PlaylistBackup = type("PlaylistBackup", (), {})
        fake_models.ConnectionSettings = type("ConnectionSettings", (), {})

        fake_crypto = types.ModuleType("crypto")
        fake_crypto.decrypt = lambda x: "apikey"

        async def _admin(*a): return "admin"
        async def _fetch(*a): return playlists_from_jellyfin

        fake_pb_router = types.ModuleType("routers.playlist_backups")
        fake_pb_router._get_admin_user_id = _admin
        fake_pb_router._fetch_jellyfin_playlists = _fetch
        fake_pb_router._do_backup_playlist = fake_do_backup
        fake_pb_router._build_managed_set = lambda db_: (managed_ids, managed_names)
        fake_pb_router._is_managed = lambda pid, name, ids, names: (
            pid in ids or name.lower() in names
        )

        fake_routers = types.ModuleType("routers")
        fake_routers.playlist_backups = fake_pb_router

        module_map = {
            "models": fake_models,
            "crypto": fake_crypto,
            "routers": fake_routers,
            "routers.playlist_backups": fake_pb_router,
        }

        originals = {k: sys.modules.get(k) for k in module_map}
        sys.modules.update(module_map)
        try:
            spec = importlib.util.spec_from_file_location(
                "_pbs_async_test",
                os.path.join(os.path.dirname(__file__), "..", "playlist_backup_scheduler.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            asyncio.run(mod._async_auto_backup(lambda: db))
        finally:
            for k, v in originals.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        return backed_up_ids

    def _make_backup_row(self, exclude=False):
        row = MagicMock()
        row.exclude_from_auto = exclude
        return row

    # -----------------------------------------------------------------------

    def test_skips_unenrolled_playlist(self):
        """A playlist with no PlaylistBackup row is NOT backed up."""
        playlists = [{"id": "p1", "name": "My Mix"}]
        backed_up = self._run(playlists, backup_rows={})  # no enrollment
        assert "p1" not in backed_up

    def test_backs_up_enrolled_playlist(self):
        """A playlist with an existing non-excluded PlaylistBackup row IS backed up."""
        playlists = [{"id": "p1", "name": "My Mix"}]
        backed_up = self._run(
            playlists,
            backup_rows={"p1": self._make_backup_row(exclude=False)},
        )
        assert "p1" in backed_up

    def test_skips_excluded_playlist(self):
        """A playlist with exclude_from_auto=True is skipped even if enrolled."""
        playlists = [{"id": "p1", "name": "Snapshot"}]
        backed_up = self._run(
            playlists,
            backup_rows={"p1": self._make_backup_row(exclude=True)},
        )
        assert "p1" not in backed_up

    def test_skips_managed_playlist_by_id(self):
        """A JellyDJ-managed playlist (matched by ID) is never auto-backed up."""
        playlists = [{"id": "managed1", "name": "Chill Vibes - Alice"}]
        backed_up = self._run(
            playlists,
            backup_rows={"managed1": self._make_backup_row(exclude=False)},
            managed_ids={"managed1"},
        )
        assert "managed1" not in backed_up

    def test_skips_managed_playlist_by_name(self):
        """A JellyDJ-managed playlist (matched by name) is never auto-backed up."""
        playlists = [{"id": "p2", "name": "Rock Hits - Bob"}]
        backed_up = self._run(
            playlists,
            backup_rows={"p2": self._make_backup_row(exclude=False)},
            managed_names={"rock hits - bob"},
        )
        assert "p2" not in backed_up

    def test_mixed_playlist_set(self):
        """Only enrolled, non-excluded, non-managed playlists are backed up."""
        playlists = [
            {"id": "enrolled", "name": "User Faves"},
            {"id": "unenrolled", "name": "New Playlist"},
            {"id": "excluded", "name": "Snapshot"},
            {"id": "managed", "name": "Auto Mix - Alice"},
        ]
        backed_up = self._run(
            playlists,
            backup_rows={
                "enrolled": self._make_backup_row(exclude=False),
                "excluded": self._make_backup_row(exclude=True),
                "managed": self._make_backup_row(exclude=False),
            },
            managed_ids={"managed"},
        )
        assert backed_up == ["enrolled"]
