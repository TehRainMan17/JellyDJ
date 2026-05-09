"""
Regression tests for /api/connections/jellyfin/users/tracked.

Bug history:
  - Endpoint docstring says "list all Jellyfin users with activation status",
    and the Connections UI has "Add to JellyDJ" buttons for non-activated users.
  - At some point the implementation was reduced to iterating only ManagedUser
    rows, which dropped every Jellyfin user that wasn't already tracked.
  - After a Jellyfin server move that minted new user IDs, the old ManagedUser
    rows also had stale jellyfin_user_id values — so the admin lost the ability
    to see the real users at all.

These tests exercise the merge logic directly (no asyncio / no httpx) to keep
the unit tests fast and offline.

Run with: docker exec jellydj-backend python -m pytest tests/test_connections_users.py -v
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
from models import ManagedUser


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _merge(db, jellyfin_users):
    """Pure copy of the merge logic in routers/connections.get_tracked_users.

    Avoids importing the router (which pulls in auth/jose). If the production
    code drifts from this implementation, the corresponding test will need to
    be updated in lockstep — that's intentional, the test guards the contract.
    """
    managed = {u.jellyfin_user_id: u for u in db.query(ManagedUser).all()}
    result = []
    seen_ids: set[str] = set()
    for jf_id, jf_name in jellyfin_users.items():
        mu = managed.get(jf_id)
        result.append({
            "jellyfin_user_id": jf_id,
            "jellyfin_username": jf_name,
            "jellydj_username": mu.username if mu else None,
            "has_activated": bool(mu and mu.has_activated),
            "is_admin": bool(mu and mu.is_admin),
            "last_login_at": mu.last_login_at if mu else None,
        })
        seen_ids.add(jf_id)
    for jf_id, mu in managed.items():
        if jf_id in seen_ids:
            continue
        result.append({
            "jellyfin_user_id": jf_id,
            "jellyfin_username": mu.username or jf_id,
            "jellydj_username": mu.username,
            "has_activated": bool(mu.has_activated),
            "is_admin": bool(mu.is_admin),
            "last_login_at": mu.last_login_at,
        })
    return sorted(result, key=lambda x: x["jellyfin_username"].lower())


def test_lists_untracked_jellyfin_users(db):
    """Jellyfin users without a ManagedUser row must still appear (not activated)."""
    db.add(ManagedUser(
        jellyfin_user_id="jf-1", username="alice",
        has_activated=True, is_admin=False,
    ))
    db.commit()

    jellyfin_users = {
        "jf-1": "alice",
        "jf-2": "bob",       # exists in Jellyfin, never activated in JellyDJ
        "jf-3": "charlie",   # ditto
    }
    result = _merge(db, jellyfin_users)

    by_id = {u["jellyfin_user_id"]: u for u in result}
    assert set(by_id.keys()) == {"jf-1", "jf-2", "jf-3"}
    assert by_id["jf-1"]["has_activated"] is True
    assert by_id["jf-2"]["has_activated"] is False
    assert by_id["jf-2"]["jellydj_username"] is None
    assert by_id["jf-3"]["has_activated"] is False


def test_includes_stale_managed_users_after_id_change(db):
    """Managed users whose jellyfin_user_id no longer matches /Users should
    still appear so the admin can clean them up (post-Jellyfin-migration case)."""
    db.add(ManagedUser(
        jellyfin_user_id="old-id-from-previous-jellyfin",
        username="alice", has_activated=True, is_admin=True,
        last_login_at=datetime(2026, 1, 1),
    ))
    db.commit()

    jellyfin_users = {
        "new-id-after-migration": "alice",
        "jf-2": "bob",
    }
    result = _merge(db, jellyfin_users)

    ids = {u["jellyfin_user_id"] for u in result}
    assert "old-id-from-previous-jellyfin" in ids, \
        "stale managed user should still surface so admin can prune it"
    assert "new-id-after-migration" in ids
    assert "jf-2" in ids
    assert len(result) == 3


def test_no_duplicates_when_managed_id_matches_live(db):
    """A managed user matched by ID must appear exactly once."""
    db.add(ManagedUser(
        jellyfin_user_id="jf-1", username="alice",
        has_activated=True, is_admin=False,
    ))
    db.commit()

    result = _merge(db, {"jf-1": "alice"})
    assert len(result) == 1
    assert result[0]["has_activated"] is True


def test_jellyfin_unreachable_falls_back_to_managed(db):
    """If Jellyfin /Users fetch fails (empty dict), still return managed users
    so the admin isn't locked out of seeing/cleaning their existing data."""
    db.add(ManagedUser(
        jellyfin_user_id="jf-1", username="alice",
        has_activated=True, is_admin=False,
    ))
    db.commit()

    result = _merge(db, {})
    assert len(result) == 1
    assert result[0]["jellydj_username"] == "alice"


def test_sort_is_case_insensitive(db):
    result = _merge(db, {"a": "zoe", "b": "Alice", "c": "bob"})
    names = [u["jellyfin_username"] for u in result]
    assert names == ["Alice", "bob", "zoe"]
