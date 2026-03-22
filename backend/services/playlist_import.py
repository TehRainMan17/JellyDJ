"""
JellyDJ — Playlist Import Service

Responsible for:
  1. Fuzzy-matching imported tracks against LibraryTrack rows
  2. Building ImportAlbumSuggestion rows (best album per gap cluster)
  3. Writing/updating the Jellyfin playlist as tracks become available
  4. Being called by the webhook handler when new items are indexed

Match algorithm
───────────────
We use a lightweight three-pass approach that avoids any external dependency:

  Pass 1 — Exact normalised match on (track_name, artist_name).
            Normalise = lower, strip punctuation, collapse whitespace.
            Confidence: 1.0

  Pass 2 — Artist exact + track fuzzy (SequenceMatcher ratio ≥ 0.82).
            Catches minor title differences ("remastered", "(feat. ...)", etc.)
            Confidence: ratio value

  Pass 3 — Track exact + artist fuzzy (ratio ≥ 0.75).
            Catches "The Beatles" vs "Beatles".
            Confidence: ratio × 0.9 (slight penalty for artist uncertainty)

First match that exceeds its threshold wins.  Anything below Pass 3's floor
is left as 'missing'.

Album suggestion algorithm
──────────────────────────
For all 'missing' tracks, group by (suggested_artist, suggested_album) from the
track metadata provided by the scrape.  Score each album by coverage_count (how
many missing tracks it satisfies).  Create one ImportAlbumSuggestion per unique
album, ordered descending by coverage_count so the UI surfaces highest-value
albums first.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ── Text normalisation ─────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lower-case, strip accents, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    # Decompose accented characters then strip non-ASCII
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Lower
    ascii_text = ascii_text.lower()
    # Remove parenthetical suffixes common in streaming metadata
    # e.g. "(feat. X)", "(Remastered)", "[Radio Edit]"
    ascii_text = re.sub(r"[\(\[][^\)\]]{0,60}[\)\]]", "", ascii_text)
    # Strip all non-alphanumeric (keep spaces)
    ascii_text = re.sub(r"[^a-z0-9 ]", "", ascii_text)
    # Collapse whitespace
    return " ".join(ascii_text.split())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ── Core match function ────────────────────────────────────────────────────────

def match_track(
    track_name: str,
    artist_name: str,
    library_index: list[dict],  # list of {item_id, track_norm, artist_norm}
) -> tuple[Optional[str], float]:
    """
    Returns (jellyfin_item_id, confidence) or (None, 0.0) if no match.

    library_index should be built once per import run — see build_library_index().
    """
    t_norm = _normalise(track_name)
    a_norm = _normalise(artist_name)

    if not t_norm:
        return None, 0.0

    best_id: Optional[str] = None
    best_score: float = 0.0

    for entry in library_index:
        et = entry["track_norm"]
        ea = entry["artist_norm"]

        # Pass 1: exact both
        if et == t_norm and ea == a_norm:
            return entry["item_id"], 1.0

        # Pass 2: exact artist + fuzzy track
        if ea == a_norm:
            r = _ratio(et, t_norm)
            if r >= 0.82 and r > best_score:
                best_id = entry["item_id"]
                best_score = r
            continue  # don't also check pass 3 for same entry

        # Pass 3: fuzzy both, artist a bit looser
        t_r = _ratio(et, t_norm)
        if t_r >= 0.90:  # track must be very close if artist is fuzzy
            a_r = _ratio(ea, a_norm)
            if a_r >= 0.75:
                score = t_r * a_r * 0.9
                if score > best_score:
                    best_id = entry["item_id"]
                    best_score = score

    return best_id, best_score


def build_library_index(db: Session) -> list[dict]:
    """
    Pull all LibraryTrack rows into a lightweight in-memory list.
    At 100k tracks this is ~25MB — fine for a self-hosted single-user app.
    """
    from models import LibraryTrack

    rows = db.query(
        LibraryTrack.jellyfin_item_id,
        LibraryTrack.name,
        LibraryTrack.artist,
    ).filter(LibraryTrack.missing_since.is_(None)).all()

    index = []
    for row in rows:
        index.append({
            "item_id":     row.jellyfin_item_id,
            "track_norm":  _normalise(row.name),
            "artist_norm": _normalise(row.artist),
        })
    return index


# ── Import matching orchestrator ───────────────────────────────────────────────

def run_match_pass(playlist_id: int, db: Session) -> dict:
    """
    Match all 'missing' tracks in the playlist against the library.
    Updates ImportedPlaylistTrack rows in place.
    Returns summary stats.
    """
    from models import ImportedPlaylistTrack, ImportedPlaylist

    playlist = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        raise ValueError(f"ImportedPlaylist {playlist_id} not found")

    tracks = db.query(ImportedPlaylistTrack).filter_by(
        playlist_id=playlist_id,
        match_status="missing",
    ).all()

    if not tracks:
        log.info("Playlist %d: no missing tracks to match", playlist_id)
        return {"matched": 0, "still_missing": 0}

    log.info("Playlist %d: matching %d missing tracks against library…", playlist_id, len(tracks))
    library_index = build_library_index(db)

    newly_matched = 0
    for track in tracks:
        item_id, score = match_track(track.track_name, track.artist_name, library_index)
        if item_id:
            track.match_status = "matched"
            track.match_score = score
            track.matched_item_id = item_id
            track.resolved_at = datetime.utcnow()
            newly_matched += 1

    # Update playlist summary counts
    all_tracks = db.query(ImportedPlaylistTrack).filter_by(playlist_id=playlist_id).all()
    playlist.matched_count = sum(1 for t in all_tracks if t.match_status == "matched")
    playlist.last_sync_at = datetime.utcnow()

    db.commit()

    still_missing = len(tracks) - newly_matched
    log.info("Playlist %d: +%d matched, %d still missing", playlist_id, newly_matched, still_missing)
    return {"matched": newly_matched, "still_missing": still_missing}


# ── Album suggestion builder ───────────────────────────────────────────────────

def build_album_suggestions(playlist_id: int, db: Session) -> int:
    """
    For all unresolved missing tracks, build/refresh ImportAlbumSuggestion rows.
    Returns count of suggestions created or updated.
    """
    from models import ImportedPlaylistTrack, ImportAlbumSuggestion

    missing = db.query(ImportedPlaylistTrack).filter_by(
        playlist_id=playlist_id,
        match_status="missing",
    ).all()

    if not missing:
        return 0

    # Group by (artist, album) — prefer the track's own album metadata
    album_map: dict[tuple[str, str], int] = {}
    for track in missing:
        artist = (track.suggested_artist or track.artist_name or "").strip()
        album  = (track.suggested_album  or track.album_name  or "").strip()
        if not artist and not album:
            continue
        key = (_normalise(artist), _normalise(album))
        album_map[key] = album_map.get(key, 0) + 1

    # Upsert suggestions — delete stale ones first
    db.query(ImportAlbumSuggestion).filter_by(playlist_id=playlist_id).delete()

    # Re-insert sorted by coverage descending
    sorted_albums = sorted(album_map.items(), key=lambda x: x[1], reverse=True)

    # Reverse-map normalised keys back to display names (use first track's values)
    display: dict[tuple[str, str], tuple[str, str]] = {}
    for track in missing:
        artist = (track.suggested_artist or track.artist_name or "").strip()
        album  = (track.suggested_album  or track.album_name  or "").strip()
        key    = (_normalise(artist), _normalise(album))
        if key not in display:
            display[key] = (artist, album)

    created = 0
    for key, count in sorted_albums:
        artist_disp, album_disp = display.get(key, ("", ""))
        suggestion = ImportAlbumSuggestion(
            playlist_id    = playlist_id,
            artist_name    = artist_disp,
            album_name     = album_disp,
            coverage_count = count,
            lidarr_status  = "pending",
        )
        db.add(suggestion)
        created += 1

    db.commit()
    log.info("Playlist %d: built %d album suggestions", playlist_id, created)
    return created


# ── Jellyfin playlist writer ───────────────────────────────────────────────────

async def write_jellyfin_playlist(
    playlist_id: int,
    owner_jellyfin_user_id: str,
    db: Session,
) -> Optional[str]:
    """
    Create or update the Jellyfin playlist for an ImportedPlaylist.
    Adds all matched tracks in position order.
    Returns the Jellyfin playlist ID.
    """
    import httpx
    from models import ImportedPlaylist, ImportedPlaylistTrack, ConnectionSettings
    from crypto import decrypt

    playlist = db.query(ImportedPlaylist).filter_by(id=playlist_id).first()
    if not playlist:
        return None

    conn = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not conn or not conn.base_url:
        raise RuntimeError("Jellyfin not configured")

    base_url = conn.base_url.rstrip("/")
    api_key  = decrypt(conn.api_key_encrypted)
    headers  = {"X-Emby-Token": api_key, "Content-Type": "application/json"}

    matched_tracks = (
        db.query(ImportedPlaylistTrack)
        .filter_by(playlist_id=playlist_id, match_status="matched")
        .order_by(ImportedPlaylistTrack.position)
        .all()
    )

    item_ids = [t.matched_item_id for t in matched_tracks if t.matched_item_id]

    async with httpx.AsyncClient(timeout=30.0) as client:
        if not playlist.jellyfin_playlist_id:
            # Create new playlist
            payload = {
                "Name":   playlist.name,
                "Ids":    ",".join(item_ids) if item_ids else "",
                "UserId": owner_jellyfin_user_id,
                "MediaType": "Audio",
            }
            resp = await client.post(f"{base_url}/Playlists", headers=headers, json=payload)
            if resp.status_code not in (200, 201):
                log.error("Failed to create Jellyfin playlist: %s", resp.text)
                return None

            jf_id = resp.json().get("Id")
            playlist.jellyfin_playlist_id = jf_id
            db.commit()
            log.info("Created Jellyfin playlist %s for import %d", jf_id, playlist_id)

            # Mark matched tracks as added
            for track in matched_tracks:
                track.added_to_playlist = True
            db.commit()
            return jf_id

        else:
            # Playlist exists — add only tracks not yet added
            not_added = [t for t in matched_tracks if not t.added_to_playlist]
            if not not_added:
                return playlist.jellyfin_playlist_id

            add_ids = [t.matched_item_id for t in not_added if t.matched_item_id]
            if add_ids:
                resp = await client.post(
                    f"{base_url}/Playlists/{playlist.jellyfin_playlist_id}/Items",
                    headers=headers,
                    params={"Ids": ",".join(add_ids), "UserId": owner_jellyfin_user_id},
                )
                if resp.status_code in (200, 204):
                    for track in not_added:
                        track.added_to_playlist = True
                    db.commit()
                    log.info(
                        "Added %d tracks to Jellyfin playlist %s",
                        len(add_ids), playlist.jellyfin_playlist_id,
                    )
                else:
                    log.error("Failed to add tracks to playlist: %s", resp.text)

            return playlist.jellyfin_playlist_id


# ── Webhook handler: called when Jellyfin indexes a new item ──────────────────

async def on_jellyfin_item_added(jellyfin_item_id: str, db: Session) -> int:
    """
    Called by the webhook router when a new audio item is indexed in Jellyfin.

    Checks all 'missing' ImportedPlaylistTrack rows across all active playlists
    to see if any of them match this new item.  If so, updates the track and
    triggers a playlist update.

    Returns the number of playlists updated.
    """
    from models import ImportedPlaylistTrack, ImportedPlaylist, LibraryTrack

    # Get the new track's metadata from LibraryTrack (should already be scanned)
    lib_track = db.query(LibraryTrack).filter_by(
        jellyfin_item_id=jellyfin_item_id
    ).first()
    if not lib_track:
        return 0

    track_norm  = _normalise(lib_track.name)
    artist_norm = _normalise(lib_track.artist)

    # Find any missing imported tracks that match this item
    candidates = db.query(ImportedPlaylistTrack).filter_by(match_status="missing").all()
    playlists_to_update: set[int] = set()

    for cand in candidates:
        ct = _normalise(cand.track_name)
        ca = _normalise(cand.artist_name)

        # Use same three-pass logic
        matched = False
        if ct == track_norm and ca == artist_norm:
            matched = True
        elif ca == artist_norm and _ratio(ct, track_norm) >= 0.82:
            matched = True
        elif _ratio(ct, track_norm) >= 0.90 and _ratio(ca, artist_norm) >= 0.75:
            matched = True

        if matched:
            cand.match_status    = "matched"
            cand.matched_item_id = jellyfin_item_id
            cand.match_score     = 1.0
            cand.resolved_at     = datetime.utcnow()
            playlists_to_update.add(cand.playlist_id)

    if not playlists_to_update:
        return 0

    db.commit()

    # Trigger playlist update for each affected playlist
    updated = 0
    for pid in playlists_to_update:
        pl = db.query(ImportedPlaylist).filter_by(id=pid, status="active").first()
        if pl and pl.jellyfin_playlist_id:
            try:
                await write_jellyfin_playlist(pid, pl.owner_user_id, db)
                updated += 1
            except Exception as exc:
                log.error("Failed to update playlist %d after new item: %s", pid, exc)

    return updated
