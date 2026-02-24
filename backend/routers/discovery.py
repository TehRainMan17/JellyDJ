
"""
Discovery Queue router — Module 6

Manages the flow:
  1. Populate queue from recommender engine (run manually or on schedule)
  2. User approves / rejects / snoozes items in the UI
  3. Approved items get sent to Lidarr as album search requests
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import DiscoveryQueueItem, ConnectionSettings, ManagedUser
from services.events import log_event
from crypto import decrypt

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ActionPayload(BaseModel):
    status: str          # approved | rejected | snoozed


# ── Lidarr helpers ────────────────────────────────────────────────────────────

def _get_lidarr_creds(db: Session) -> tuple[str, str]:
    row = db.query(ConnectionSettings).filter_by(service="lidarr").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Lidarr not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


async def _send_to_lidarr(artist_name: str, album_name: str, base_url: str, api_key: str) -> dict:
    """
    Search Lidarr for the artist, then add them for monitoring and trigger album search.
    Returns a result dict with ok/message.

    Handles three cases:
    - Artist not in Lidarr: add them, then search for the specific album
    - Artist already in Lidarr: skip add, go straight to album search
    - No specific album: just ensure artist is monitored
    """
    headers = {"X-Api-Key": api_key}
    import asyncio

    # Longer timeout — RefreshArtist + retries can take 15-20s
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: search for artist in Lidarr
        search_resp = await client.get(
            f"{base_url}/api/v1/artist/lookup",
            headers=headers,
            params={"term": artist_name},
        )
        search_resp.raise_for_status()
        results = search_resp.json()

        if not results:
            return {"ok": False, "message": f"Artist '{artist_name}' not found in Lidarr lookup"}

        artist = results[0]
        foreign_artist_id = artist.get("foreignArtistId")
        artist_name_found = artist.get("artistName", artist_name)

        # Step 2: check if already added
        existing_resp = await client.get(
            f"{base_url}/api/v1/artist",
            headers=headers,
        )
        existing_resp.raise_for_status()
        existing = existing_resp.json()
        existing_map = {a.get("foreignArtistId"): a for a in existing}

        artist_already_exists = foreign_artist_id in existing_map
        if artist_already_exists:
            lidarr_artist_id = existing_map[foreign_artist_id].get("id")
            log.info(f"  '{artist_name_found}' already in Lidarr (id={lidarr_artist_id}), going straight to album search")
            # Fall through to album search below — do NOT return early

        # Steps 3-6: add artist only if not already in Lidarr
        if not artist_already_exists:
            # Step 3: get root folder
            root_resp = await client.get(f"{base_url}/api/v1/rootfolder", headers=headers)
            root_resp.raise_for_status()
            roots = root_resp.json()
            if not roots:
                return {"ok": False, "message": "No root folders configured in Lidarr"}
            root_path = roots[0]["path"]

            # Step 4: get quality profile
            qp_resp = await client.get(f"{base_url}/api/v1/qualityprofile", headers=headers)
            qp_resp.raise_for_status()
            profiles = qp_resp.json()
            quality_profile_id = profiles[0]["id"] if profiles else 1

            # Step 5: get metadata profile
            mp_resp = await client.get(f"{base_url}/api/v1/metadataprofile", headers=headers)
            mp_resp.raise_for_status()
            meta_profiles = mp_resp.json()
            metadata_profile_id = meta_profiles[0]["id"] if meta_profiles else 1

            # Step 6: add artist — monitored for FUTURE releases only
            add_payload = {
                **artist,
                "qualityProfileId": quality_profile_id,
                "metadataProfileId": metadata_profile_id,
                "rootFolderPath": root_path,
                "monitored": True,
                "monitor": "future",
                "addOptions": {
                    "monitor": "future",
                    "searchForMissingAlbums": False,
                },
            }
            add_resp = await client.post(
                f"{base_url}/api/v1/artist",
                headers=headers,
                json=add_payload,
            )
            add_resp.raise_for_status()
            added_artist = add_resp.json()
            lidarr_artist_id = added_artist.get("id")
            log.info(f"  Added '{artist_name_found}' to Lidarr (id={lidarr_artist_id})")

        # Step 7: find and trigger search for the specific album.
        # Runs for BOTH new and already-existing artists.
        album_searched = ""
        album_error = ""
        if lidarr_artist_id:
            # Trigger a refresh so Lidarr scans the artist's albums
            await client.post(
                f"{base_url}/api/v1/command",
                headers=headers,
                json={"name": "RefreshArtist", "artistId": lidarr_artist_id},
            )

            # Fetch albums — retry with backoff (new artists need scan time)
            albums = []
            max_attempts = 5 if not artist_already_exists else 2
            for attempt in range(max_attempts):
                if attempt > 0:
                    await asyncio.sleep(4)
                albums_resp = await client.get(
                    f"{base_url}/api/v1/album",
                    headers=headers,
                    params={"artistId": lidarr_artist_id},
                )
                if albums_resp.status_code == 200:
                    albums = albums_resp.json()
                    if albums:
                        break
                log.info(f"  Waiting for Lidarr albums (attempt {attempt+1}/{max_attempts})...")

            if not albums:
                album_error = f"Lidarr returned no albums for '{artist_name_found}' after retries"
                log.warning(f"  {album_error}")
            else:
                target = (album_name or "").lower().strip()

                def _album_score(alb: dict) -> float:
                    """Score how well a Lidarr album matches the requested name."""
                    title = alb.get("title", "").lower().strip()
                    if not target:
                        return 0.0
                    if title == target:
                        return 1.0
                    if target in title:
                        return 0.9 - (len(title) - len(target)) * 0.01
                    if title in target:
                        return 0.8
                    t_words = set(target.split())
                    a_words = set(title.split())
                    overlap = len(t_words & a_words)
                    if overlap:
                        return 0.5 + (overlap / max(len(t_words), len(a_words))) * 0.3
                    return 0.0

                if target:
                    scored = [(alb, _album_score(alb)) for alb in albums]
                    scored.sort(key=lambda x: x[1], reverse=True)
                    best_score = scored[0][1]
                    match = scored[0][0] if best_score > 0.3 else None
                    if match:
                        log.info(f"  Album match: '{match.get('title')}' (score={best_score:.2f}) for target='{album_name}'")
                    else:
                        log.info(f"  No match for '{album_name}', top: {[(a.get('title'), round(s,2)) for a,s in scored[:3]]}")
                else:
                    match = None

                if not match:
                    non_comp = [a for a in albums if a.get("albumType", "") == "Album"]
                    match = non_comp[0] if non_comp else albums[0]
                    log.info(f"  Fallback album: '{match.get('title')}'")

                if match:
                    album_id = match["id"]
                    album_searched = match.get("title", album_name or "")

                    # Ensure this album is monitored
                    match["monitored"] = True
                    put_resp = await client.put(
                        f"{base_url}/api/v1/album/{album_id}",
                        headers=headers,
                        json=match,
                    )
                    log.info(f"  Monitor album PUT: HTTP {put_resp.status_code} — '{album_searched}'")

                    # Trigger album search
                    cmd_resp = await client.post(
                        f"{base_url}/api/v1/command",
                        headers=headers,
                        json={"name": "AlbumSearch", "albumIds": [album_id]},
                    )
                    log.info(f"  AlbumSearch: HTTP {cmd_resp.status_code} — {cmd_resp.text[:200]}")

                    if cmd_resp.status_code not in (200, 201):
                        # Fallback: search entire artist
                        log.warning(f"  AlbumSearch failed (HTTP {cmd_resp.status_code}), falling back to ArtistSearch")
                        fb = await client.post(
                            f"{base_url}/api/v1/command",
                            headers=headers,
                            json={"name": "ArtistSearch", "artistId": lidarr_artist_id},
                        )
                        log.info(f"  ArtistSearch fallback: HTTP {fb.status_code}")

        action = "already in" if artist_already_exists else "added to"
        msg = f"'{artist_name_found}' {action} Lidarr"

        if album_searched:
            # Album was found and AlbumSearch command was sent — genuine success
            msg += f" — search triggered for '{album_searched}'"
            return {"ok": True, "message": msg}
        elif album_error:
            # Artist was added/found but Lidarr returned no albums at all.
            # Return ok=False so the item is NOT marked lidarr_sent and can
            # be retried on the next auto-download run.
            msg += f" — could not trigger download: {album_error}"
            return {"ok": False, "message": msg}
        elif album_name:
            # Artist exists in Lidarr but the specific album couldn't be matched.
            # Same — don't mark sent, let it retry.
            msg += f" — could not find album '{album_name}' in Lidarr"
            return {"ok": False, "message": msg}
        else:
            # No album name specified — artist-only add succeeded
            return {"ok": True, "message": msg}


# ── Queue population ──────────────────────────────────────────────────────────

async def _populate_queue_for_user(user_id: str, db: Session, limit: int = 20):
    """
    Run the recommender and add new items to the discovery queue.

    On each refresh:
    1. Wipe all non-pinned pending items using raw SQL (avoids SQLite 999-variable
       limit that breaks ORM bulk-delete on large queues).
    2. Build exclusion set from ONLY the items being kept (rejected/snoozed/
       approved + Lidarr live check). Deleted pending items must NOT be in the
       exclusion set or they block every new rec.
    3. Fill back up to exactly `limit` fresh recs.
    """
    import httpx
    from sqlalchemy import text as satext
    from services.recommender import recommend_new_albums
    from services.library_dedup import validate_album_in_lidarr, tracks_in_library_for_album

    # ── Lidarr creds ──────────────────────────────────────────────────────────
    lidarr_base_url = ""
    lidarr_api_key = ""
    try:
        lidarr_row = db.query(ConnectionSettings).filter_by(service="lidarr").first()
        if lidarr_row and lidarr_row.base_url and lidarr_row.api_key_encrypted:
            lidarr_base_url = lidarr_row.base_url.rstrip("/")
            lidarr_api_key = decrypt(lidarr_row.api_key_encrypted)
    except Exception:
        pass

    # ── Step 1: Wipe non-pinned pending items via raw SQL ─────────────────────
    # ORM .delete() translates to DELETE ... WHERE id IN (...all ids...) which
    # hits SQLite's 999-variable limit and silently deletes only ~300 rows.
    # Raw SQL WHERE clause avoids this entirely.
    db.execute(satext(
        "DELETE FROM discovery_queue "
        "WHERE user_id = :uid AND status = 'pending' AND auto_queued = 0"
    ), {"uid": user_id})

    # Also clean up already-sent approved items (downloaded, no longer needed).
    db.execute(satext(
        "DELETE FROM discovery_queue "
        "WHERE user_id = :uid AND status = 'approved' AND lidarr_sent = 1"
    ), {"uid": user_id})

    db.flush()
    log.info(f"  Wiped pending queue for user {user_id[:8]}, refilling to {limit}")

    # ── Step 2: Build exclusion set from KEPT items only ─────────────────────
    # Only include rejected/snoozed/approved items (user decisions) and
    # pinned items. Do NOT include the items we just deleted — they're gone
    # and should be fair game for fresh recs.
    excluded: set[str] = set()
    kept_rows = db.query(DiscoveryQueueItem.artist_name, DiscoveryQueueItem.album_name
                         ).filter(
        DiscoveryQueueItem.user_id == user_id,
        DiscoveryQueueItem.status.in_(["approved", "rejected", "snoozed"]),
    ).all()
    for row in kept_rows:
        excluded.add(f"{row.artist_name.lower()}::{(row.album_name or '').lower()}")

    # Also exclude pinned items (auto_queued=True) so they aren't duplicated
    for row in db.query(DiscoveryQueueItem.artist_name, DiscoveryQueueItem.album_name
                        ).filter_by(user_id=user_id, auto_queued=True).all():
        excluded.add(f"{row.artist_name.lower()}::{(row.album_name or '').lower()}")

    log.info(f"  Exclusion set: {len(excluded)} kept/actioned items")

    # `effective_limit` is how many new items we'll add this run
    effective_limit = limit

    # ── Step 3: Lidarr monitored albums — live exclusion ─────────────────────
    # Ask Lidarr what it's already monitoring. This catches albums that were
    # just downloaded but haven't shown up in LibraryTrack yet (pre-index).
    # Also catches albums that were manually added to Lidarr outside JellyDJ.
    lidarr_monitored_albums: set[str] = set()   # "artist_lower::album_lower"
    lidarr_monitored_artists: set[str] = set()  # artist names Lidarr already has

    if lidarr_base_url:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"X-Api-Key": lidarr_api_key}

                # Get all artists Lidarr is monitoring
                artists_resp = await client.get(
                    f"{lidarr_base_url}/api/v1/artist", headers=headers
                )
                if artists_resp.status_code == 200:
                    for a in artists_resp.json():
                        lidarr_monitored_artists.add(a.get("artistName", "").lower())
                        artist_id = a.get("id")
                        if not artist_id:
                            continue
                        # Get albums for each monitored artist
                        alb_resp = await client.get(
                            f"{lidarr_base_url}/api/v1/album",
                            headers=headers,
                            params={"artistId": artist_id},
                        )
                        if alb_resp.status_code == 200:
                            for alb in alb_resp.json():
                                aname = a.get("artistName", "").lower()
                                albname = alb.get("title", "").lower()
                                if aname and albname:
                                    lidarr_monitored_albums.add(f"{aname}::{albname}")

            log.info(
                f"  Lidarr: {len(lidarr_monitored_artists)} monitored artists, "
                f"{len(lidarr_monitored_albums)} monitored albums"
            )
        except Exception as e:
            log.warning(f"  Could not fetch Lidarr monitored albums: {e}")

    # ── Step 4: Get recommendations ──────────────────────────────────────────
    recs = recommend_new_albums(user_id, effective_limit * 4, db)

    # ── Step 5: Enforce discovery bias — 75% new artists, 25% known ──────────
    # Split recs by type, then interleave with the target ratio.
    new_artist_recs = [r for r in recs if r.rec_type != "missing_album"]
    known_artist_recs = [r for r in recs if r.rec_type == "missing_album"]

    n_new   = min(len(new_artist_recs),   int(effective_limit * 0.75))
    n_known = min(len(known_artist_recs), effective_limit - n_new)
    # Interleave: 3 new for every 1 known
    ordered: list = []
    ni, ki = 0, 0
    while len(ordered) < (n_new + n_known):
        for _ in range(3):
            if ni < n_new:
                ordered.append(new_artist_recs[ni]); ni += 1
        if ki < n_known:
            ordered.append(known_artist_recs[ki]); ki += 1
        if ni >= n_new and ki >= n_known:
            break

    log.info(
        f"  Rec mix: {n_new} new-artist / {n_known} known-artist "
        f"(from {len(new_artist_recs)} / {len(known_artist_recs)} available)"
    )

    added = 0
    for rec in ordered:
        if added >= effective_limit:
            break

        alow = rec.artist_name.lower()
        alblow = (rec.album_name or "").lower()
        dedup_key = f"{alow}::{alblow}"

        if dedup_key in excluded:
            continue

        # Check Lidarr monitored albums (live exclusion)
        if dedup_key in lidarr_monitored_albums:
            log.info(f"  Skipping '{rec.artist_name}' / '{rec.album_name}' — already monitored in Lidarr")
            excluded.add(dedup_key)
            continue

        # Also exclude by artist-level check from Lidarr if it's a missing_album rec:
        # if Lidarr already has this artist, we only skip missing_album recs that
        # Lidarr already monitors at album level (checked above). New-artist recs
        # are never excluded purely because Lidarr has the artist.

        album_name_to_use = rec.album_name or ""
        image_url = rec.image_url

        # ── Lidarr pre-validation ────────────────────────────────────────────
        if lidarr_base_url and rec.artist_name:
            try:
                validation = await validate_album_in_lidarr(
                    rec.artist_name, album_name_to_use,
                    lidarr_base_url, lidarr_api_key,
                )

                if validation["found"]:
                    if validation["is_compilation"]:
                        log.info(
                            f"  Skipping '{rec.artist_name}' / '{validation['lidarr_album_name']}'"
                            f" — Lidarr confirms compilation"
                        )
                        excluded.add(dedup_key)
                        continue

                    album_name_to_use = validation["lidarr_album_name"] or album_name_to_use

                    # Re-check dedup with canonical Lidarr album name
                    canonical_key = f"{alow}::{album_name_to_use.lower()}"
                    if canonical_key in excluded or canonical_key in lidarr_monitored_albums:
                        log.info(f"  Skipping '{rec.artist_name}' / '{album_name_to_use}' — canonical name already excluded")
                        excluded.add(dedup_key)
                        continue

                    if validation["track_names"]:
                        owned, total = tracks_in_library_for_album(
                            rec.artist_name, validation["track_names"], db
                        )
                        if total > 0:
                            overlap_pct = owned / total
                            log.info(
                                f"  '{rec.artist_name}' / '{album_name_to_use}': "
                                f"{owned}/{total} tracks owned ({overlap_pct:.0%})"
                            )
                            if overlap_pct >= 0.90:
                                log.info(f"  Skipping — >90% tracks already owned")
                                excluded.add(dedup_key)
                                continue
                else:
                    log.info(f"  '{rec.artist_name}' not in Lidarr yet — queuing anyway")

            except Exception as e:
                log.warning(f"  Lidarr validation error for '{rec.artist_name}': {e} — queuing anyway")

        db.add(DiscoveryQueueItem(
            user_id=user_id,
            artist_name=rec.artist_name,
            album_name=album_name_to_use,
            release_year=rec.release_year,
            popularity_score=str(rec.popularity_score),
            image_url=image_url,
            why=rec.why,
            source_artist=rec.source_artist,
            source_affinity=str(rec.source_affinity),
            status="pending",
        ))
        excluded.add(dedup_key)
        added += 1

    db.commit()

    if added:
        log_event(db, "discovery_refreshed",
                  f"Discovery refresh: +{added} new recommendation(s) added")
        try:
            from models import AutomationSettings
            s = db.query(AutomationSettings).first()
            if not s:
                s = AutomationSettings(); db.add(s)
            s.last_discovery_refresh = datetime.utcnow()
            db.commit()
        except Exception:
            pass
    return added


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def get_queue(
    status: str = Query(default="pending"),
    user_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return discovery queue items, optionally filtered by status and user."""
    q = db.query(DiscoveryQueueItem)
    if status != "all":
        q = q.filter_by(status=status)
    if user_id:
        q = q.filter_by(user_id=user_id)
    q = q.order_by(DiscoveryQueueItem.popularity_score.desc(), DiscoveryQueueItem.added_at.desc())
    items = q.all()

    # Attach username for display
    user_map = {
        u.jellyfin_user_id: u.username
        for u in db.query(ManagedUser).all()
    }

    return [
        {
            "id": item.id,
            "user_id": item.user_id,
            "username": user_map.get(item.user_id, item.user_id),
            "artist_name": item.artist_name,
            "album_name": item.album_name,
            "release_year": item.release_year,
            "popularity_score": float(item.popularity_score),
            "image_url": item.image_url,
            "why": item.why,
            "source_artist": item.source_artist,
            "source_affinity": float(item.source_affinity),
            "status": item.status,
            "lidarr_sent": item.lidarr_sent,
            "lidarr_response": item.lidarr_response,
            "auto_queued": bool(item.auto_queued),
            "auto_skip": bool(item.auto_skip),
            "added_at": item.added_at,
            "actioned_at": item.actioned_at,
        }
        for item in items
    ]


@router.get("/counts")
def get_counts(db: Session = Depends(get_db)):
    """Return item counts per status — used by the dashboard badge."""
    from sqlalchemy import func
    rows = (
        db.query(DiscoveryQueueItem.status, func.count(DiscoveryQueueItem.id))
        .group_by(DiscoveryQueueItem.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "snoozed": counts.get("snoozed", 0),
        "total": sum(counts.values()),
    }


@router.post("/populate")
async def populate_queue(
    db: Session = Depends(get_db),
):
    """
    Populate queue for all enabled users.
    Runs synchronously so the client gets the real added count and can
    immediately refresh the list. Limit is always read from AutomationSettings.
    """
    from models import AutomationSettings
    users = db.query(ManagedUser).filter_by(is_enabled=True).all()
    if not users:
        raise HTTPException(400, "No enabled managed users found.")

    s = db.query(AutomationSettings).first()
    effective_limit = s.discovery_items_per_run if s else 10
    log.info(f"Discovery populate: limit={effective_limit} (from AutomationSettings)")

    total_added = 0
    for user in users:
        try:
            added = await _populate_queue_for_user(user.jellyfin_user_id, db, effective_limit)
            total_added += added
            log.info(f"Discovery queue: +{added} items for {user.username}")
        except Exception as e:
            log.warning(f"Discovery populate failed for {user.username}: {e}")

    return {"ok": True, "added": total_added,
            "message": f"Added {total_added} new recommendation(s)"}


async def _populate_all_users(users, limit: int = 0):
    """
    Background task: populate discovery queue for all users.
    Always reads the limit from AutomationSettings so the user's control is respected.
    The limit parameter is ignored — it exists only for backwards compatibility.
    """
    from database import SessionLocal
    from models import AutomationSettings
    db = SessionLocal()
    try:
        s = db.query(AutomationSettings).first()
        effective_limit = s.discovery_items_per_run if s else 10
        log.info(f"Discovery populate: effective_limit={effective_limit} (from AutomationSettings)")

        for user in users:
            try:
                added = await _populate_queue_for_user(
                    user.jellyfin_user_id, db, effective_limit
                )
                log.info(f"Discovery queue: +{added} items for {user.username}")
            except Exception as e:
                log.warning(f"Discovery populate failed for {user.username}: {e}")
    finally:
        db.close()


@router.post("/{item_id}/action")
def action_item(
    item_id: int,
    payload: ActionPayload,
    db: Session = Depends(get_db),
):
    """Approve, reject, or snooze a queue item."""
    if payload.status not in ("approved", "rejected", "snoozed", "pending"):
        raise HTTPException(400, f"Invalid status '{payload.status}'")

    item = db.query(DiscoveryQueueItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")

    item.status = payload.status
    item.actioned_at = datetime.utcnow()
    db.commit()
    log_event(db, f"track_{payload.status}",
              f"{payload.status.capitalize()}: {item.artist_name} — {item.album_name or 'unknown album'}")
    return {"ok": True, "id": item_id, "status": item.status}


@router.post("/{item_id}/send-to-lidarr")
async def send_to_lidarr(item_id: int, db: Session = Depends(get_db)):
    """Send an approved item to Lidarr."""
    item = db.query(DiscoveryQueueItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")

    try:
        base_url, api_key = _get_lidarr_creds(db)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    try:
        result = await _send_to_lidarr(item.artist_name, item.album_name, base_url, api_key)
    except Exception as e:
        result = {"ok": False, "message": str(e)}

    item.lidarr_sent = result["ok"]
    item.lidarr_response = result["message"]
    if result["ok"]:
        item.status = "approved"
        item.actioned_at = datetime.utcnow()
    db.commit()

    if not result["ok"]:
        raise HTTPException(502, result["message"])

    return {"ok": True, "message": result["message"]}


@router.post("/{item_id}/pin")
def pin_item(item_id: int, db: Session = Depends(get_db)):
    """
    Mark an item as 'getting this next' for auto-download.
    Clears the pin from any other item for this user first (only one can be pinned).
    """
    item = db.query(DiscoveryQueueItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    if item.lidarr_sent:
        raise HTTPException(400, "Item already sent to Lidarr")

    # Clear existing pin for this user
    db.query(DiscoveryQueueItem).filter_by(
        user_id=item.user_id, auto_queued=True
    ).update({"auto_queued": False})

    item.auto_queued = True
    item.auto_skip = False  # unmark skip if they're re-pinning
    db.commit()
    return {"ok": True, "id": item_id, "pinned": True}


@router.post("/{item_id}/skip-auto")
def skip_auto_item(item_id: int, db: Session = Depends(get_db)):
    """
    Mark 'not that one' — exclude this item from auto-download selection.
    Does not reject the item from the queue; it stays visible for manual approval.
    """
    item = db.query(DiscoveryQueueItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")

    item.auto_skip = True
    item.auto_queued = False  # remove pin if it was pinned
    db.commit()
    return {"ok": True, "id": item_id, "auto_skip": True}


@router.get("/auto-status")
def auto_download_status(user_id: str, db: Session = Depends(get_db)):
    """
    Returns the current auto-download candidate for a user:
    - pinned item (user said 'getting this next'), if any
    - otherwise top-scored pending item not marked auto_skip
    Also returns cooldown/enabled status.
    """
    from models import AutomationSettings
    from sqlalchemy import text as satext

    s = db.query(AutomationSettings).first()
    enabled = bool(s.auto_download_enabled) if s else False
    cooldown_days = s.auto_download_cooldown_days if s else 7
    last_dl = s.last_auto_download if s else None

    from datetime import timedelta
    cooldown_remaining_hours = None
    if last_dl:
        elapsed = datetime.utcnow() - last_dl
        remaining = timedelta(days=cooldown_days) - elapsed
        if remaining.total_seconds() > 0:
            cooldown_remaining_hours = round(remaining.total_seconds() / 3600, 1)

    pinned = (
        db.query(DiscoveryQueueItem)
        .filter_by(user_id=user_id, status="pending", lidarr_sent=False, auto_queued=True)
        .filter(DiscoveryQueueItem.auto_skip == False)
        .first()
    )

    next_candidate = pinned
    if not next_candidate:
        next_candidate = (
            db.query(DiscoveryQueueItem)
            .filter_by(user_id=user_id, status="pending", lidarr_sent=False)
            .filter(DiscoveryQueueItem.auto_skip == False)
            .filter(DiscoveryQueueItem.auto_queued == False)
            .order_by(satext("CAST(popularity_score AS REAL) DESC"))
            .first()
        )

    return {
        "enabled": enabled,
        "cooldown_days": cooldown_days,
        "cooldown_remaining_hours": cooldown_remaining_hours,
        "last_auto_download": last_dl,
        "next_candidate": {
            "id": next_candidate.id,
            "artist_name": next_candidate.artist_name,
            "album_name": next_candidate.album_name,
            "popularity_score": next_candidate.popularity_score,
            "image_url": next_candidate.image_url,
            "why": next_candidate.why,
            "auto_queued": bool(next_candidate.auto_queued),
        } if next_candidate else None,
    }


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Permanently remove an item from the queue."""
    item = db.query(DiscoveryQueueItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    db.delete(item)
    db.commit()
    return {"ok": True}



@router.post("/purge-duplicates")
def purge_duplicate_queue_items(
    user_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Scan all pending queue items and remove any that are already in the library.
    Run this after updating the dedup logic to clean up stale entries.
    """
    from services.library_dedup import album_in_library
    from models import LibraryTrack

    # Build known_albums set same as recommender
    known_albums = set()
    lib_rows = db.query(
        LibraryTrack.artist_name, LibraryTrack.album_name, LibraryTrack.album_artist
    ).filter(LibraryTrack.missing_since.is_(None)).all()
    for row in lib_rows:
        for name in (row.artist_name, row.album_artist):
            if name and row.album_name:
                known_albums.add(f"{name.lower()}::{row.album_name.lower()}")

    q = db.query(DiscoveryQueueItem).filter_by(status="pending")
    if user_id:
        q = q.filter_by(user_id=user_id)
    items = q.all()

    removed = []
    for item in items:
        exact_key = f"{item.artist_name.lower()}::{(item.album_name or '').lower()}"
        in_exact = exact_key in known_albums
        in_fuzzy = bool(item.album_name and album_in_library(item.artist_name, item.album_name, db))
        if in_exact or in_fuzzy:
            removed.append({"artist": item.artist_name, "album": item.album_name,
                            "match": "exact" if in_exact else "fuzzy"})
            db.delete(item)

    db.commit()
    return {"purged": len(removed), "items": removed}

@router.get("/debug/library/{user_id}")
def debug_library_dedup(user_id: str, db: Session = Depends(get_db)):
    """
    Shows exactly what albums are in the library dedup sets for a user.
    Use this to diagnose why recommendations contain albums you already own.
    """
    from models import LibraryTrack, Play
    from services.library_dedup import album_in_library

    # Build same sets as recommender
    known_albums = set()
    known_artists = set()

    lib_rows = db.query(
        LibraryTrack.artist_name, LibraryTrack.album_name, LibraryTrack.album_artist
    ).filter(LibraryTrack.missing_since.is_(None)).all()

    for row in lib_rows:
        for name in (row.artist_name, row.album_artist):
            if name:
                known_artists.add(name.lower())
        if row.artist_name and row.album_name:
            known_albums.add(f"{row.artist_name.lower()}::{row.album_name.lower()}")
        if row.album_artist and row.album_name:
            known_albums.add(f"{row.album_artist.lower()}::{row.album_name.lower()}")

    # Current queue items
    queue_items = db.query(DiscoveryQueueItem).filter_by(user_id=user_id).all()
    flagged = []
    for item in queue_items:
        exact_key = f"{item.artist_name.lower()}::{(item.album_name or '').lower()}"
        in_exact = exact_key in known_albums
        in_fuzzy = bool(item.album_name and album_in_library(item.artist_name, item.album_name, db))
        if in_exact or in_fuzzy:
            flagged.append({
                "id": item.id,
                "artist": item.artist_name,
                "album": item.album_name,
                "in_exact_set": in_exact,
                "in_fuzzy_match": in_fuzzy,
                "status": item.status,
            })

    return {
        "library_stats": {
            "total_library_tracks": len(lib_rows),
            "unique_albums_in_dedup_set": len(known_albums),
            "unique_artists_in_dedup_set": len(known_artists),
        },
        "queue_items_that_are_already_in_library": flagged,
        "sample_known_albums": sorted(list(known_albums))[:30],
    }


@router.get("/debug/recommend/{user_id}")
async def debug_recommend(user_id: str, db: Session = Depends(get_db)):
    """
    Runs the recommender with verbose output — shows raw candidates before
    dedup/filtering, so you can see exactly what's being suggested and why
    each item is kept or dropped.
    """
    import logging
    # Temporarily set recommender logger to DEBUG
    rec_logger = logging.getLogger("services.recommender")
    old_level = rec_logger.level
    rec_logger.setLevel(logging.DEBUG)

    from services.recommender import recommend_new_albums
    from models import LibraryTrack

    lib_count = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).count()

    try:
        recs = recommend_new_albums(user_id, limit=20, db=db)
    finally:
        rec_logger.setLevel(old_level)

    return {
        "library_tracks": lib_count,
        "recommendations_returned": len(recs),
        "recommendations": [
            {
                "artist": r.artist_name,
                "album": r.album_name,
                "score": r.popularity_score,
                "why": r.why,
                "source_artist": r.source_artist,
                "rec_type": r.rec_type,
            }
            for r in recs
        ],
    }
