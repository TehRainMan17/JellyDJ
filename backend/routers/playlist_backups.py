"""
JellyDJ — Playlist Backup router (revision edition).

Schema overview
───────────────
  PlaylistBackup          — one row per tracked playlist (metadata + prefs)
  PlaylistBackupRevision  — one row per snapshot; up to max_revisions per backup
  PlaylistBackupTrack     — one row per track per revision

Each backup/re-backup call creates a NEW revision rather than overwriting the
previous one. When the revision count exceeds max_revisions, the oldest
unlabeled revision is pruned (labeled revisions are always kept as permanent
snapshots regardless of the rotation limit).

Endpoints
─────────
GET  /api/playlist-backups/jellyfin-playlists
GET  /api/playlist-backups
GET  /api/playlist-backups/settings
PUT  /api/playlist-backups/settings
POST /api/playlist-backups/backup
POST /api/playlist-backups/backup-all
GET  /api/playlist-backups/{backup_id}/revisions
GET  /api/playlist-backups/{backup_id}/revisions/{revision_id}/tracks
GET  /api/playlist-backups/{backup_id}/revisions/{revision_id}/resolve-preview
POST /api/playlist-backups/{backup_id}/revisions/{revision_id}/restore
POST /api/playlist-backups/{backup_id}/revisions/{revision_id}/label
DELETE /api/playlist-backups/{backup_id}/revisions/{revision_id}
PATCH /api/playlist-backups/{backup_id}
DELETE /api/playlist-backups/{backup_id}

Jellyfin API notes
───────────────────
- List playlists: GET /Users/{userId}/Items?IncludeItemTypes=Playlist (not /Playlists)
- Playlist items: GET /Playlists/{id}/Items?UserId={userId}  (UserId required with API key)
- Create:         POST /Playlists  with UserId in JSON body
- Clear/add:      DELETE/POST /Playlists/{id}/Items with UserId param
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import func

from auth import require_admin, UserContext
from database import get_db
from models import (
    ConnectionSettings,
    LibraryTrack,
    ManagedUser,
    PlaylistBackup,
    PlaylistBackupRevision,
    PlaylistBackupTrack,
    PlaylistBackupSettings,
    UserPlaylist,
)
from crypto import decrypt

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/playlist-backups", tags=["playlist-backups"])

# Maximum revisions kept per playlist when no explicit max_revisions is set
DEFAULT_MAX_REVISIONS = 6


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BackupRequestBody(BaseModel):
    jellyfin_playlist_ids: List[str]


class BackupPatchBody(BaseModel):
    display_name: Optional[str] = None
    exclude_from_auto: Optional[bool] = None
    max_revisions: Optional[int] = None


class BackupSettingsBody(BaseModel):
    auto_backup_enabled: Optional[bool] = None
    auto_backup_interval_hours: Optional[int] = None


class RevisionLabelBody(BaseModel):
    label: Optional[str] = None   # None or "" clears the label


# ── Managed playlist detection ────────────────────────────────────────────────

def _build_managed_set(db: Session) -> tuple[set[str], set[str]]:
    managed_ids: set[str] = set()
    managed_names: set[str] = set()
    rows = db.query(UserPlaylist).all()
    user_cache: dict[str, str] = {}
    for row in rows:
        jf_id = getattr(row, "jellyfin_playlist_id", "") or ""
        if jf_id:
            managed_ids.add(jf_id)
        uid = row.owner_user_id
        if uid not in user_cache:
            u = db.query(ManagedUser).filter_by(jellyfin_user_id=uid).first()
            user_cache[uid] = u.username if u else uid
        managed_names.add(f"{row.base_name} - {user_cache[uid]}".lower())
    return managed_ids, managed_names


def _is_managed(pid: str, name: str, ids: set[str], names: set[str]) -> bool:
    return pid in ids or name.lower() in names


# ── Jellyfin helpers ──────────────────────────────────────────────────────────

def _jellyfin_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise HTTPException(400, "Jellyfin is not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


def _h(api_key: str) -> dict:
    return {"X-Emby-Token": api_key}


async def _get_admin_user_id(base_url: str, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{base_url}/Users", headers=_h(api_key))
    if resp.status_code != 200:
        raise HTTPException(502, f"Jellyfin /Users returned {resp.status_code}")
    users = resp.json()
    if not users:
        raise HTTPException(502, "Jellyfin returned no users")
    admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
    return (admin or users[0])["Id"]


async def _fetch_jellyfin_playlists(base_url: str, api_key: str, uid: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base_url}/Users/{uid}/Items",
            headers=_h(api_key),
            params={"IncludeItemTypes": "Playlist", "Recursive": "true",
                    "Fields": "ChildCount", "Limit": 10000},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"Jellyfin returned {resp.status_code} listing playlists")
    return [
        {"id": i["Id"], "name": i.get("Name", ""), "track_count": i.get("ChildCount", 0)}
        for i in (resp.json().get("Items") or [])
    ]


async def _fetch_playlist_tracks(base_url: str, api_key: str, pid: str, uid: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/Playlists/{pid}/Items",
            headers=_h(api_key),
            params={"UserId": uid, "Fields": "Name,Album", "Limit": 10000},
        )
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise HTTPException(502, f"Jellyfin returned {resp.status_code} fetching tracks for {pid}")
    tracks = []
    for i, item in enumerate(resp.json().get("Items") or []):
        artists = item.get("ArtistItems") or item.get("Artists") or []
        artist = (artists[0].get("Name", "") if isinstance(artists[0], dict)
                  else str(artists[0])) if artists else item.get("AlbumArtist", "")
        tracks.append({
            "position": i,
            "jellyfin_item_id": item.get("Id", ""),
            "track_name": item.get("Name", ""),
            "artist_name": artist,
            "album_name": item.get("Album", ""),
        })
    return tracks


async def _create_jellyfin_playlist(base_url, api_key, name, track_ids, uid) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/Playlists",
            headers={**_h(api_key), "Content-Type": "application/json"},
            json={"Name": name, "Ids": track_ids, "UserId": uid, "MediaType": "Audio"},
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Jellyfin returned {resp.status_code} creating '{name}'")
    return resp.json().get("Id", "")


async def _overwrite_jellyfin_playlist(base_url, api_key, pid, track_ids, uid) -> None:
    headers = _h(api_key)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{base_url}/Playlists/{pid}/Items",
                             headers=headers, params={"UserId": uid, "Limit": 10000})
        if r.status_code == 200:
            current = r.json().get("Items") or []
            if current:
                eids = [str(x.get("PlaylistItemId") or x.get("Id", ""))
                        for x in current if x.get("PlaylistItemId") or x.get("Id")]
                if eids:
                    await client.delete(f"{base_url}/Playlists/{pid}/Items",
                                        headers=headers, params={"EntryIds": ",".join(eids)})
        for i in range(0, len(track_ids), 100):
            batch = track_ids[i:i + 100]
            await client.post(f"{base_url}/Playlists/{pid}/Items",
                              headers=headers, params={"Ids": ",".join(batch), "UserId": uid})


# ── Revision helpers ──────────────────────────────────────────────────────────

def _next_revision_number(db: Session, backup_id: int) -> int:
    """Return the next revision_number for a backup (max existing + 1, or 1)."""
    from sqlalchemy import func
    result = db.query(func.max(PlaylistBackupRevision.revision_number)) \
               .filter_by(backup_id=backup_id).scalar()
    return (result or 0) + 1


def _prune_old_revisions(db: Session, backup_id: int, max_revisions: int) -> None:
    """
    Delete the oldest unlabeled revisions if the total count exceeds max_revisions.
    Labeled revisions are always preserved — they represent deliberate snapshots.

    Uses ORM-level object deletes (not bulk DELETE) so that rows added in the
    current transaction are visible and the operation is safe within an open session.
    """
    # Defensive: treat None/0 as the default limit so a missing migration
    # column never causes a TypeError or unexpectedly prunes all revisions.
    limit = max_revisions if (max_revisions and max_revisions > 0) else DEFAULT_MAX_REVISIONS

    all_revs = (
        db.query(PlaylistBackupRevision)
        .filter_by(backup_id=backup_id)
        .order_by(PlaylistBackupRevision.revision_number.asc())
        .all()
    )
    unlabeled = [r for r in all_revs if not r.label]
    total = len(all_revs)

    while total > limit and unlabeled:
        oldest = unlabeled.pop(0)
        for track in db.query(PlaylistBackupTrack).filter_by(revision_id=oldest.id).all():
            db.delete(track)
        db.delete(oldest)
        total -= 1


def _write_revision(
    db: Session,
    backup: PlaylistBackup,
    tracks: list[dict],
    now: datetime,
    label: Optional[str] = None,
) -> PlaylistBackupRevision:
    """
    Create a new revision row and its track rows, then prune old revisions.

    Prune order:
      1. Flush the new revision row so it gets an id.
      2. Insert all track rows.
      3. Flush again so everything is visible within the session.
      4. Prune — by this point the new revision is counted, so the oldest
         unlabeled revision (not the one we just created) is removed.
    The caller is responsible for the final db.commit().
    """
    rev_num = _next_revision_number(db, backup.id)
    rev = PlaylistBackupRevision(
        backup_id=backup.id,
        revision_number=rev_num,
        track_count=len(tracks),
        backed_up_at=now,
        label=label,
    )
    db.add(rev)
    db.flush()  # get rev.id before inserting tracks

    for t in tracks:
        db.add(PlaylistBackupTrack(
            revision_id=rev.id,
            backup_id=backup.id,  # kept populated for NOT NULL compat with old schema
            position=t["position"],
            jellyfin_item_id=t["jellyfin_item_id"],
            track_name=t["track_name"],
            artist_name=t["artist_name"],
            album_name=t["album_name"],
        ))

    db.flush()  # make tracks visible to prune query
    # Guard: max_revisions may be None on rows that existed before the column was added
    max_rev = backup.max_revisions if (backup.max_revisions and backup.max_revisions > 0) else DEFAULT_MAX_REVISIONS
    _prune_old_revisions(db, backup.id, max_rev)
    return rev


# ── Track ID resolution (post-migration restore) ──────────────────────────────
#
# After a Jellyfin server migration the stored jellyfin_item_ids in a backup
# revision are stale — Jellyfin's POST /Playlists/{id}/Items endpoint silently
# drops unknown IDs and returns 200/204, which is why a "successful" restore
# could end up producing an empty playlist.
#
# We always validate stored IDs against the local LibraryTrack table (rebuilt
# by the indexer with current Jellyfin IDs). Misses are re-resolved by name +
# artist, then name + album, then name only. This keeps restores correct
# whether or not the Jellyfin item IDs changed.

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _resolve_track_ids(
    db: Session,
    tracks: list[PlaylistBackupTrack],
    strategy: str = "auto",
) -> tuple[list[str], list[dict], list[dict]]:
    """
    Resolve backup track rows to currently-valid Jellyfin item IDs.

    strategy:
      - "auto"      → use stored ID if it still exists in LibraryTrack,
                      otherwise fall back to name/artist/album matching
      - "id_only"   → only use the stored IDs that still exist in LibraryTrack
                      (safe variant of the legacy behaviour)
      - "name_only" → ignore stored IDs entirely; always re-match by metadata

    Returns (ordered_resolved_ids, matched_details, unmatched_details).
    matched_details items: {position, track_name, artist_name, jellyfin_item_id, source}
    unmatched_details items: {position, track_name, artist_name, album_name}
    """
    if strategy not in ("auto", "id_only", "name_only"):
        raise HTTPException(400, f"Invalid match_strategy '{strategy}'")

    # Pre-load the set of currently-valid Jellyfin IDs so we can validate
    # stored IDs without a per-track query.
    #
    # CRITICAL: filter out soft-deleted rows (missing_since IS NOT NULL).
    # After a Jellyfin server migration, the indexer marks every old ID as
    # missing and inserts new rows for the new IDs. If we don't filter here,
    # we happily "validate" stored backup IDs against stale rows and Jellyfin
    # silently drops them on the restore POST.
    valid_ids: set[str] = set()
    if strategy in ("auto", "id_only"):
        valid_ids = {
            row[0] for row in db.query(LibraryTrack.jellyfin_item_id)
            .filter(LibraryTrack.missing_since.is_(None))
            .all() if row[0]
        }

    resolved: list[str] = []
    matched: list[dict] = []
    unmatched: list[dict] = []

    for t in tracks:
        new_id: Optional[str] = None
        source = ""

        if strategy != "name_only" and t.jellyfin_item_id and t.jellyfin_item_id in valid_ids:
            new_id = t.jellyfin_item_id
            source = "stored_id"

        if not new_id and strategy != "id_only":
            name = _norm(t.track_name)
            artist = _norm(t.artist_name)
            album = _norm(t.album_name)
            if name:
                row = None
                if artist:
                    row = (
                        db.query(LibraryTrack.jellyfin_item_id)
                        .filter(LibraryTrack.missing_since.is_(None))
                        .filter(func.lower(LibraryTrack.track_name) == name)
                        .filter(func.lower(LibraryTrack.artist_name) == artist)
                        .first()
                    )
                    if row:
                        source = "name_artist"
                if not row and album:
                    row = (
                        db.query(LibraryTrack.jellyfin_item_id)
                        .filter(LibraryTrack.missing_since.is_(None))
                        .filter(func.lower(LibraryTrack.track_name) == name)
                        .filter(func.lower(LibraryTrack.album_name) == album)
                        .first()
                    )
                    if row:
                        source = "name_album"
                if not row:
                    row = (
                        db.query(LibraryTrack.jellyfin_item_id)
                        .filter(LibraryTrack.missing_since.is_(None))
                        .filter(func.lower(LibraryTrack.track_name) == name)
                        .first()
                    )
                    if row:
                        source = "name_only"
                if row and row[0]:
                    new_id = row[0]

        if new_id:
            resolved.append(new_id)
            matched.append({
                "position": t.position,
                "track_name": t.track_name,
                "artist_name": t.artist_name,
                "jellyfin_item_id": new_id,
                "source": source,
            })
        else:
            unmatched.append({
                "position": t.position,
                "track_name": t.track_name,
                "artist_name": t.artist_name,
                "album_name": t.album_name,
            })

    return resolved, matched, unmatched


# ── Backup write ──────────────────────────────────────────────────────────────

async def _do_backup_playlist(
    db: Session,
    base_url: str,
    api_key: str,
    admin_user_id: str,
    jellyfin_playlist_id: str,
    jellyfin_playlist_name: str,
    force: bool = False,
    label: Optional[str] = None,
) -> tuple[PlaylistBackup, PlaylistBackupRevision]:
    """
    Fetch tracks from Jellyfin and write a new revision.

    force=False — skips if exclude_from_auto=True (automatic job)
    force=True  — always writes (manual press), respects snapshot flag meaning
                  only that auto is excluded, not that manual is blocked

    Returns (backup, new_revision).
    """
    existing = db.query(PlaylistBackup).filter_by(
        jellyfin_playlist_id=jellyfin_playlist_id
    ).first()

    if existing and existing.exclude_from_auto and not force:
        # Return the backup and its most recent revision without writing
        latest = (
            db.query(PlaylistBackupRevision)
            .filter_by(backup_id=existing.id)
            .order_by(PlaylistBackupRevision.revision_number.desc())
            .first()
        )
        return existing, latest

    tracks = await _fetch_playlist_tracks(
        base_url, api_key, jellyfin_playlist_id, admin_user_id
    )
    now = datetime.now(timezone.utc)

    if not existing:
        existing = PlaylistBackup(
            jellyfin_playlist_id=jellyfin_playlist_id,
            jellyfin_playlist_name=jellyfin_playlist_name,
            display_name=None,
            exclude_from_auto=False,
            max_revisions=DEFAULT_MAX_REVISIONS,
            created_at=now,
        )
        db.add(existing)
        db.flush()

    # Always update the name to reflect what Jellyfin currently calls it
    existing.jellyfin_playlist_name = jellyfin_playlist_name

    rev = _write_revision(db, existing, tracks, now, label=label)
    db.commit()
    db.refresh(existing)
    db.refresh(rev)
    return existing, rev


# ── Serializers ───────────────────────────────────────────────────────────────

def _serialize_revision(r: PlaylistBackupRevision) -> dict:
    return {
        "id": r.id,
        "backup_id": r.backup_id,
        "revision_number": r.revision_number,
        "track_count": r.track_count,
        "backed_up_at": r.backed_up_at,
        "label": r.label,
        "is_labeled": bool(r.label),
    }


def _serialize_backup(b: PlaylistBackup, latest_rev: Optional[PlaylistBackupRevision]) -> dict:
    return {
        "id": b.id,
        "jellyfin_playlist_id": b.jellyfin_playlist_id,
        "jellyfin_playlist_name": b.jellyfin_playlist_name,
        "display_name": b.display_name,
        "effective_name": b.display_name or b.jellyfin_playlist_name,
        "exclude_from_auto": b.exclude_from_auto,
        "max_revisions": b.max_revisions,
        "created_at": b.created_at,
        # Denormalised from the latest revision for convenience
        "track_count": latest_rev.track_count if latest_rev else 0,
        "last_backed_up_at": latest_rev.backed_up_at if latest_rev else None,
        "latest_revision_id": latest_rev.id if latest_rev else None,
    }


def _get_latest_revision(db: Session, backup_id: int) -> Optional[PlaylistBackupRevision]:
    return (
        db.query(PlaylistBackupRevision)
        .filter_by(backup_id=backup_id)
        .order_by(PlaylistBackupRevision.revision_number.desc())
        .first()
    )


def _get_backup_settings(db: Session) -> PlaylistBackupSettings:
    row = db.query(PlaylistBackupSettings).first()
    if not row:
        row = PlaylistBackupSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/jellyfin-playlists")
async def list_jellyfin_playlists(
    include_managed: bool = Query(default=False),
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return Jellyfin playlists filtered to user-created ones by default."""
    base_url, api_key = _jellyfin_creds(db)
    uid = await _get_admin_user_id(base_url, api_key)
    playlists = await _fetch_jellyfin_playlists(base_url, api_key, uid)

    managed_ids, managed_names = _build_managed_set(db)
    backup_map = {b.jellyfin_playlist_id: b for b in db.query(PlaylistBackup).all()}

    result = []
    for p in playlists:
        is_managed = _is_managed(p["id"], p["name"], managed_ids, managed_names)
        if is_managed and not include_managed:
            continue
        b = backup_map.get(p["id"])
        latest = _get_latest_revision(db, b.id) if b else None
        result.append({
            **p,
            "is_managed": is_managed,
            "has_backup": b is not None,
            "last_backed_up_at": latest.backed_up_at if latest else None,
            "exclude_from_auto": b.exclude_from_auto if b else False,
            "revision_count": (
                db.query(PlaylistBackupRevision).filter_by(backup_id=b.id).count()
                if b else 0
            ),
        })
    return result


@router.get("")
def list_backups(
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return all stored playlist backups with their latest revision summary."""
    backups = db.query(PlaylistBackup).order_by(PlaylistBackup.jellyfin_playlist_name).all()
    result = []
    for b in backups:
        latest = _get_latest_revision(db, b.id)
        rev_count = db.query(PlaylistBackupRevision).filter_by(backup_id=b.id).count()
        entry = _serialize_backup(b, latest)
        entry["revision_count"] = rev_count
        result.append(entry)
    return result


@router.get("/settings")
def get_settings(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    s = _get_backup_settings(db)
    return {
        "auto_backup_enabled": s.auto_backup_enabled,
        "auto_backup_interval_hours": s.auto_backup_interval_hours,
        "last_auto_backup_at": s.last_auto_backup_at,
    }


@router.put("/settings")
def update_settings(
    body: BackupSettingsBody,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = _get_backup_settings(db)
    if body.auto_backup_enabled is not None:
        s.auto_backup_enabled = body.auto_backup_enabled
    if body.auto_backup_interval_hours is not None:
        if body.auto_backup_interval_hours < 1:
            raise HTTPException(400, "Interval must be at least 1 hour")
        s.auto_backup_interval_hours = body.auto_backup_interval_hours
    db.commit()
    try:
        from playlist_backup_scheduler import reschedule_backup_job
        reschedule_backup_job(db)
    except Exception as exc:
        log.warning("Could not reschedule backup job: %s", exc)
    db.refresh(s)
    return {
        "auto_backup_enabled": s.auto_backup_enabled,
        "auto_backup_interval_hours": s.auto_backup_interval_hours,
        "last_auto_backup_at": s.last_auto_backup_at,
    }


@router.post("/backup")
async def backup_playlists(
    body: BackupRequestBody,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Manual backup of specific playlists — always forces a write and creates
    a new revision even for snapshot (exclude_from_auto) playlists.
    """
    if not body.jellyfin_playlist_ids:
        raise HTTPException(400, "No playlist IDs provided")

    base_url, api_key = _jellyfin_creds(db)
    uid = await _get_admin_user_id(base_url, api_key)
    all_playlists = await _fetch_jellyfin_playlists(base_url, api_key, uid)
    name_map = {p["id"]: p["name"] for p in all_playlists}

    missing = [pid for pid in body.jellyfin_playlist_ids if pid not in name_map]
    if missing:
        raise HTTPException(
            404,
            f"Playlist ID(s) not found in Jellyfin: {missing}. No backups written."
        )

    results = []
    for pid in body.jellyfin_playlist_ids:
        try:
            backup, rev = await _do_backup_playlist(
                db, base_url, api_key, uid, pid, name_map[pid], force=True
            )
        except HTTPException:
            raise
        except Exception as exc:
            import traceback
            log.error(
                "backup_playlists failed for playlist %s ('%s'): %s\n%s",
                pid, name_map.get(pid, "?"), exc, traceback.format_exc(),
            )
            try:
                db.rollback()
            except Exception:
                pass
            raise HTTPException(500, f"Backup failed for '{name_map.get(pid, pid)}': {exc}") from exc

        latest = _get_latest_revision(db, backup.id)
        rev_count = db.query(PlaylistBackupRevision).filter_by(backup_id=backup.id).count()
        entry = _serialize_backup(backup, latest)
        entry["revision_count"] = rev_count
        entry["new_revision"] = _serialize_revision(rev)
        results.append(entry)
    return {"backed_up": results}


@router.post("/backup-all")
async def backup_all_playlists(
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Back up all user-created playlists, creating a new revision for each.
    Skips JellyDJ-managed playlists and exclude_from_auto (snapshot) playlists.
    """
    base_url, api_key = _jellyfin_creds(db)
    uid = await _get_admin_user_id(base_url, api_key)
    all_playlists = await _fetch_jellyfin_playlists(base_url, api_key, uid)
    managed_ids, managed_names = _build_managed_set(db)

    results, skipped_managed, skipped_snapshot = [], [], []
    for p in all_playlists:
        if _is_managed(p["id"], p["name"], managed_ids, managed_names):
            skipped_managed.append(p["name"])
            continue
        existing = db.query(PlaylistBackup).filter_by(jellyfin_playlist_id=p["id"]).first()
        if existing and existing.exclude_from_auto:
            skipped_snapshot.append(p["name"])
            continue
        backup, rev = await _do_backup_playlist(
            db, base_url, api_key, uid, p["id"], p["name"], force=False
        )
        latest = _get_latest_revision(db, backup.id)
        rev_count = db.query(PlaylistBackupRevision).filter_by(backup_id=backup.id).count()
        entry = _serialize_backup(backup, latest)
        entry["revision_count"] = rev_count
        results.append(entry)

    s = _get_backup_settings(db)
    s.last_auto_backup_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "backed_up": results,
        "skipped_managed": skipped_managed,
        "skipped_snapshots": skipped_snapshot,
    }


@router.get("/{backup_id}/revisions")
def list_revisions(
    backup_id: int,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return all revisions for a backup, newest first."""
    b = db.query(PlaylistBackup).filter_by(id=backup_id).first()
    if not b:
        raise HTTPException(404, "Backup not found")
    revisions = (
        db.query(PlaylistBackupRevision)
        .filter_by(backup_id=backup_id)
        .order_by(PlaylistBackupRevision.revision_number.desc())
        .all()
    )
    return {
        "backup_id": backup_id,
        "backup_name": b.display_name or b.jellyfin_playlist_name,
        "max_revisions": b.max_revisions,
        "revisions": [_serialize_revision(r) for r in revisions],
    }


@router.get("/{backup_id}/revisions/{revision_id}/tracks")
def get_revision_tracks(
    backup_id: int,
    revision_id: int,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return the track list for a specific revision."""
    b = db.query(PlaylistBackup).filter_by(id=backup_id).first()
    if not b:
        raise HTTPException(404, "Backup not found")
    rev = db.query(PlaylistBackupRevision).filter_by(id=revision_id, backup_id=backup_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found")
    tracks = (
        db.query(PlaylistBackupTrack)
        .filter_by(revision_id=revision_id)
        .order_by(PlaylistBackupTrack.position)
        .all()
    )
    return {
        "revision": _serialize_revision(rev),
        "tracks": [
            {
                "position": t.position,
                "jellyfin_item_id": t.jellyfin_item_id,
                "track_name": t.track_name,
                "artist_name": t.artist_name,
                "album_name": t.album_name,
            }
            for t in tracks
        ],
    }


def _load_revision_tracks(db: Session, backup_id: int, revision_id: int) -> tuple[PlaylistBackup, PlaylistBackupRevision, list[PlaylistBackupTrack]]:
    b = db.query(PlaylistBackup).filter_by(id=backup_id).first()
    if not b:
        raise HTTPException(404, "Backup not found")
    rev = db.query(PlaylistBackupRevision).filter_by(id=revision_id, backup_id=backup_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found")
    tracks = (
        db.query(PlaylistBackupTrack)
        .filter_by(revision_id=revision_id)
        .order_by(PlaylistBackupTrack.position)
        .all()
    )
    return b, rev, tracks


@router.get("/{backup_id}/revisions/{revision_id}/resolve-preview")
def resolve_preview(
    backup_id: int,
    revision_id: int,
    match_strategy: str = Query(default="auto"),
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Dry-run a restore: report which tracks would resolve to a current
    Jellyfin item ID under the given strategy, and which would not.

    Useful after a Jellyfin migration to see how many tracks of a backup
    can be name-matched before actually writing the playlist.
    """
    _, rev, tracks = _load_revision_tracks(db, backup_id, revision_id)
    if not tracks:
        raise HTTPException(409, "This revision contains no tracks.")

    resolved, matched, unmatched = _resolve_track_ids(db, tracks, match_strategy)
    by_source: dict[str, int] = {}
    for m in matched:
        by_source[m["source"]] = by_source.get(m["source"], 0) + 1
    return {
        "revision_number": rev.revision_number,
        "match_strategy": match_strategy,
        "total": len(tracks),
        "matched_count": len(resolved),
        "unmatched_count": len(unmatched),
        "matched_by_source": by_source,
        "unmatched": unmatched,
    }


@router.post("/{backup_id}/revisions/{revision_id}/restore")
async def restore_revision(
    backup_id: int,
    revision_id: int,
    match_strategy: str = Query(
        default="auto",
        description="auto = stored ID with name-match fallback (post-migration safe); "
                    "id_only = stored IDs validated against LibraryTrack only; "
                    "name_only = always re-match by track/artist/album",
    ),
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Restore a specific revision to Jellyfin.
    Creates the playlist if it doesn't exist; overwrites if it does.

    Stored Jellyfin item IDs are always validated against the local
    LibraryTrack index before being sent to Jellyfin — this prevents the
    silent-drop failure mode where Jellyfin accepts a POST containing IDs
    that no longer exist (e.g. after a server migration) and produces an
    empty playlist with no error.
    """
    b, rev, tracks = _load_revision_tracks(db, backup_id, revision_id)
    if not tracks:
        raise HTTPException(409, "This revision contains no tracks — nothing to restore.")

    track_ids, matched, unmatched = _resolve_track_ids(db, tracks, match_strategy)
    if not track_ids:
        raise HTTPException(
            409,
            f"None of the {len(tracks)} backup tracks could be resolved to current "
            f"Jellyfin items (strategy={match_strategy}). Re-run the library indexer "
            f"to refresh LibraryTrack, or try match_strategy=name_only.",
        )

    target_name = b.display_name or b.jellyfin_playlist_name

    base_url, api_key = _jellyfin_creds(db)
    uid = await _get_admin_user_id(base_url, api_key)
    live = await _fetch_jellyfin_playlists(base_url, api_key, uid)
    existing_jf_id = next(
        (p["id"] for p in live if p["name"].strip().lower() == target_name.strip().lower()),
        None,
    )

    if existing_jf_id:
        await _overwrite_jellyfin_playlist(base_url, api_key, existing_jf_id, track_ids, uid)
        action = "overwritten"
        playlist_id = existing_jf_id
    else:
        playlist_id = await _create_jellyfin_playlist(base_url, api_key, target_name, track_ids, uid)
        action = "created"

    # Re-anchor the backup row to whichever Jellyfin playlist it now corresponds
    # to. Without this, a post-migration restore creates a brand-new Jellyfin
    # playlist (new ID) but PlaylistBackup.jellyfin_playlist_id still holds the
    # stale pre-migration ID — so:
    #   - the available-to-backup list never matches it again
    #   - the next backup fetches tracks for the dead ID, gets 404, and writes
    #     a 0-track revision over the previous good snapshot
    if playlist_id and playlist_id != b.jellyfin_playlist_id:
        log.info(
            "Re-anchoring backup %d ('%s'): jellyfin_playlist_id %s → %s",
            backup_id, target_name, b.jellyfin_playlist_id, playlist_id,
        )
        b.jellyfin_playlist_id = playlist_id
        db.commit()

    by_source: dict[str, int] = {}
    for m in matched:
        by_source[m["source"]] = by_source.get(m["source"], 0) + 1

    log.info(
        "Restored backup %d revision #%d ('%s') to Jellyfin: %d/%d tracks resolved "
        "(strategy=%s, by_source=%s, unmatched=%d), action=%s",
        backup_id, rev.revision_number, target_name,
        len(track_ids), len(tracks), match_strategy, by_source, len(unmatched), action,
    )
    return {
        "restored": True,
        "playlist_name": target_name,
        "jellyfin_playlist_id": playlist_id,
        "track_count": len(track_ids),
        "requested_count": len(tracks),
        "unmatched_count": len(unmatched),
        "matched_by_source": by_source,
        "unmatched": unmatched,
        "match_strategy": match_strategy,
        "revision_number": rev.revision_number,
        "action": action,
    }


@router.post("/{backup_id}/revisions/{revision_id}/label")
def label_revision(
    backup_id: int,
    revision_id: int,
    body: RevisionLabelBody,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Set or clear the label on a revision.
    Labeled revisions are never auto-pruned regardless of max_revisions.
    Clear the label (pass null or "") to allow it to be pruned normally.
    """
    rev = db.query(PlaylistBackupRevision).filter_by(id=revision_id, backup_id=backup_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found")
    rev.label = (body.label or "").strip() or None
    db.commit()
    db.refresh(rev)
    return _serialize_revision(rev)


@router.delete("/{backup_id}/revisions/{revision_id}")
def delete_revision(
    backup_id: int,
    revision_id: int,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a specific revision and its tracks. Cannot delete the only remaining revision."""
    rev = db.query(PlaylistBackupRevision).filter_by(id=revision_id, backup_id=backup_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found")
    count = db.query(PlaylistBackupRevision).filter_by(backup_id=backup_id).count()
    if count <= 1:
        raise HTTPException(
            409, "Cannot delete the only revision. Delete the entire backup record instead."
        )
    db.query(PlaylistBackupTrack).filter_by(revision_id=revision_id).delete()
    db.delete(rev)
    db.commit()
    return {"deleted_revision_id": revision_id}


@router.patch("/{backup_id}")
def patch_backup(
    backup_id: int,
    body: BackupPatchBody,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update display_name, exclude_from_auto, and/or max_revisions on a backup."""
    b = db.query(PlaylistBackup).filter_by(id=backup_id).first()
    if not b:
        raise HTTPException(404, "Backup not found")
    if body.display_name is not None:
        b.display_name = body.display_name.strip() or None
    if body.exclude_from_auto is not None:
        b.exclude_from_auto = body.exclude_from_auto
    if body.max_revisions is not None:
        if body.max_revisions < 1:
            raise HTTPException(400, "max_revisions must be at least 1")
        b.max_revisions = body.max_revisions
        db.flush()  # write new max before prune reads it
        _prune_old_revisions(db, backup_id, body.max_revisions)
    db.commit()
    db.refresh(b)
    latest = _get_latest_revision(db, b.id)
    rev_count = db.query(PlaylistBackupRevision).filter_by(backup_id=b.id).count()
    entry = _serialize_backup(b, latest)
    entry["revision_count"] = rev_count
    return entry


@router.delete("/{backup_id}")
def delete_backup(
    backup_id: int,
    _: UserContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete an entire backup record — all revisions and tracks."""
    b = db.query(PlaylistBackup).filter_by(id=backup_id).first()
    if not b:
        raise HTTPException(404, "Backup not found")
    revs = db.query(PlaylistBackupRevision).filter_by(backup_id=backup_id).all()
    for rev in revs:
        db.query(PlaylistBackupTrack).filter_by(revision_id=rev.id).delete()
        db.delete(rev)
    db.delete(b)
    db.commit()
    return {"deleted": backup_id}
