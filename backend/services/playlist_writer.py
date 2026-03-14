"""
JellyDJ Playlist Writer

Low-level Jellyfin helpers used by the user-playlist autopush scheduler.

The old hardcoded playlist system (for_you / discover / most_played /
recently_played) has been removed.  All playlists are now driven by
user-defined or admin-defined PlaylistTemplate records via the modular
block engine.  The scheduler autopush job in scheduler.py calls
generate_from_template() (services/playlist_engine.py) and then uses
the _find_playlist / _create_playlist / _clear_playlist / _add_to_playlist
helpers below to write the result to Jellyfin.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from models import ConnectionSettings
from crypto import decrypt


log = logging.getLogger(__name__)


def _jellyfin_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)




# ── Jellyfin API helpers ──────────────────────────────────────────────────────

async def _find_playlist(
    base_url: str, api_key: str, name: str, admin_user_id: str
) -> Optional[str]:
    """Return the Jellyfin playlist ID if it exists, else None."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/Users/{admin_user_id}/Items",
            headers=headers,
            params={
                "IncludeItemTypes": "Playlist",
                "Recursive": "true",
                "SearchTerm": name,
            },
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("Items", [])
        # Exact name match
        match = next((i for i in items if i.get("Name") == name), None)
        return match["Id"] if match else None


async def _create_playlist(
    base_url: str, api_key: str, name: str, admin_user_id: str,
    item_ids: list[str],
) -> Optional[str]:
    """Create a new Jellyfin playlist and return its ID."""
    headers = {"X-Emby-Token": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base_url}/Playlists",
            headers=headers,
            json={
                "Name": name,
                "Ids": item_ids,
                "UserId": admin_user_id,
                "MediaType": "Audio",
            },
        )
        if resp.status_code not in (200, 201):
            log.error(f"Create playlist failed: {resp.status_code} — {resp.text[:300]}")
            return None
        return resp.json().get("Id")


async def _clear_playlist(
    base_url: str, api_key: str, playlist_id: str, admin_user_id: str = "",
) -> bool:
    """
    Remove all items from an existing playlist.
    Tries multiple strategies to handle different Jellyfin versions.
    """
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Strategy 1: fetch items with UserId param (required by some Jellyfin versions)
        params: dict = {}
        if admin_user_id:
            params["UserId"] = admin_user_id

        resp = await client.get(
            f"{base_url}/Playlists/{playlist_id}/Items",
            headers=headers,
            params=params,
        )
        log.info(f"  GET playlist items: HTTP {resp.status_code} playlist_id={playlist_id}")

        if resp.status_code != 200:
            log.warning(f"  GET playlist items failed: {resp.status_code} — {resp.text[:300]}")
            return False

        data = resp.json()
        items = data.get("Items", [])
        log.info(f"  {len(items)} items in playlist")
        if not items:
            return True  # already empty

        # PlaylistItemId is the entry ID needed for deletion.
        # Different Jellyfin versions may use different field names.
        def _entry_id(item: dict) -> Optional[str]:
            for key in ("PlaylistItemId", "Id"):
                val = item.get(key)
                if val:
                    return str(val)
            return None

        entry_ids = [_entry_id(i) for i in items]
        entry_ids = [e for e in entry_ids if e]
        log.info(f"  Entry IDs to delete: {entry_ids[:5]}{'...' if len(entry_ids) > 5 else ''}")

        if not entry_ids:
            log.warning(f"  No entry IDs found. Item keys: {list(items[0].keys()) if items else []}")
            return False

        # Delete in one request — comma-separated EntryIds query param
        del_resp = await client.delete(
            f"{base_url}/Playlists/{playlist_id}/Items",
            headers=headers,
            params={"EntryIds": ",".join(entry_ids)},
        )
        log.info(f"  DELETE playlist items: HTTP {del_resp.status_code} — {del_resp.text[:200]}")

        if del_resp.status_code in (200, 204):
            return True

        # Strategy 2: delete items one at a time (fallback for strict Jellyfin versions)
        log.info(f"  Bulk delete failed, trying one-by-one...")
        all_ok = True
        for eid in entry_ids:
            r = await client.delete(
                f"{base_url}/Playlists/{playlist_id}/Items",
                headers=headers,
                params={"EntryIds": eid},
            )
            if r.status_code not in (200, 204):
                log.warning(f"  Single delete failed for entry {eid}: HTTP {r.status_code}")
                all_ok = False
        return all_ok


async def _add_to_playlist(
    base_url: str, api_key: str, playlist_id: str,
    item_ids: list[str], admin_user_id: str,
) -> bool:
    """Add items to an existing playlist in batches of 100."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(item_ids), 100):
            batch = item_ids[i:i + 100]
            resp = await client.post(
                f"{base_url}/Playlists/{playlist_id}/Items",
                headers=headers,
                params={
                    "Ids": ",".join(batch),
                    "UserId": admin_user_id,
                },
            )
            if resp.status_code not in (200, 204):
                log.warning(f"Add to playlist batch failed: {resp.status_code} — {resp.text[:200]}")
                return False
    return True


async def _get_admin_user_id(base_url: str, api_key: str) -> Optional[str]:
    """Get the first admin user ID from Jellyfin (needed for playlist operations)."""
    headers = {"X-Emby-Token": api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base_url}/Users", headers=headers)
        if resp.status_code != 200:
            return None
        users = resp.json()
        # Prefer admin users, fall back to first user
        admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
        return (admin or users[0])["Id"] if users else None
