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
For all 'missing' tracks, group by artist.  For each artist, look up
ArtistEnrichment (Last.fm/MusicBrainz) to find which album each missing
track belongs to (via the top_tracks field).  Fall back to the scrape
metadata (suggested_album) when enrichment isn't available.  Score each
album by coverage_count and populate image_url, artist_mbid, and the
missing_tracks JSON list.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import httpx
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
    # Remove noise words that differ between platforms/library metadata
    # "Hall & Oates" (& stripped above) vs "Hall and Oates" — both → "hall oates"
    # "The Beatles" vs "Beatles" — both → "beatles"
    ascii_text = re.sub(r"\b(?:and|the|n)\b", "", ascii_text)
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

        # Pass 2: exact artist + fuzzy track (ratio ≥ 0.82)
        if ea == a_norm:
            r = _ratio(et, t_norm)
            if r >= 0.82 and r > best_score:
                best_id = entry["item_id"]
                best_score = r
            # Pass 2b: artist exact + one track name contains the other
            # Catches "Girls Just Want to Have Fun" vs
            #         "Girls Just Want to Have Fun acoustic version"
            elif not best_id and len(t_norm) >= 8 and (et.startswith(t_norm) or t_norm.startswith(et)):
                containment_score = min(len(t_norm), len(et)) / max(len(t_norm), len(et))
                if containment_score >= 0.5 and containment_score > best_score:
                    best_id = entry["item_id"]
                    best_score = containment_score * 0.95  # slight penalty
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
            # Pass 4: artist word containment — one name contains all words
            # of the other.  Catches "Hall Oates" vs "Daryl Hall John Oates"
            elif not best_id and t_r >= 0.92 and a_norm and ea:
                a_words = set(a_norm.split())
                ea_words = set(ea.split())
                if a_words and ea_words and (a_words <= ea_words or ea_words <= a_words):
                    score = t_r * 0.85  # penalty for artist uncertainty
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
        LibraryTrack.track_name,
        LibraryTrack.artist_name,
        LibraryTrack.album_artist,
    ).filter(LibraryTrack.missing_since.is_(None)).all()

    index = []
    for row in rows:
        artist_norm = _normalise(row.artist_name)
        album_artist_norm = _normalise(row.album_artist) if row.album_artist else ""
        entry = {
            "item_id":            row.jellyfin_item_id,
            "track_norm":         _normalise(row.track_name),
            "artist_norm":        artist_norm,
        }
        index.append(entry)
        # If album_artist differs from artist, add a second index entry
        # so we can match "Cyndi Lauper" whether stored as artist or album_artist
        if album_artist_norm and album_artist_norm != artist_norm:
            index.append({
                "item_id":     row.jellyfin_item_id,
                "track_norm":  _normalise(row.track_name),
                "artist_norm": album_artist_norm,
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
        # If primary artist didn't match, try suggested_artist (may differ)
        if not item_id and track.suggested_artist and track.suggested_artist != track.artist_name:
            item_id, score = match_track(track.track_name, track.suggested_artist, library_index)
        if item_id:
            track.match_status = "matched"
            track.match_score = score
            track.matched_item_id = item_id
            track.resolved_at = datetime.utcnow()
            newly_matched += 1
            log.info("  matched: '%s' by '%s' (score=%.2f)", track.track_name, track.artist_name, score)
        else:
            log.info("  MISS: '%s' by '%s' (norm: '%s' / '%s')",
                     track.track_name, track.artist_name,
                     _normalise(track.track_name), _normalise(track.artist_name))

    # Update playlist summary counts
    all_tracks = db.query(ImportedPlaylistTrack).filter_by(playlist_id=playlist_id).all()
    playlist.matched_count = sum(1 for t in all_tracks if t.match_status == "matched")
    playlist.last_sync_at = datetime.utcnow()

    db.commit()

    still_missing = len(tracks) - newly_matched
    log.info("Playlist %d: +%d matched, %d still missing", playlist_id, newly_matched, still_missing)
    return {"matched": newly_matched, "still_missing": still_missing}


# ── Album suggestion builder ───────────────────────────────────────────────────

async def build_album_suggestions(playlist_id: int, db: Session) -> int:
    """
    For all unresolved missing tracks, build/refresh ImportAlbumSuggestion rows.

    Strategy: for each unique artist with missing tracks, look them up in Lidarr,
    fetch the artist's ACTUAL album catalog + track listings from Lidarr, then
    match our missing tracks against those real albums. This guarantees we only
    ever suggest albums that Lidarr can actually download — no greatest-hits
    compilations or albums that don't exist in Lidarr's metadata sources.

    Returns count of suggestions created or updated.
    """
    from models import (
        ImportedPlaylistTrack, ImportAlbumSuggestion, ArtistEnrichment,
        ConnectionSettings,
    )
    from crypto import decrypt

    missing = db.query(ImportedPlaylistTrack).filter_by(
        playlist_id=playlist_id,
        match_status="missing",
    ).all()

    if not missing:
        return 0

    log.info("Playlist %d: building album suggestions for %d missing tracks…", playlist_id, len(missing))

    # ── Check if Lidarr is configured ─────────────────────────────────────
    conn = db.query(ConnectionSettings).filter_by(service="lidarr").first()
    has_lidarr = conn and conn.base_url and conn.api_key_encrypted
    lidarr_base = conn.base_url.rstrip("/") if has_lidarr else ""
    lidarr_key = decrypt(conn.api_key_encrypted) if has_lidarr else ""
    lidarr_headers = {"X-Api-Key": lidarr_key} if has_lidarr else {}

    # ── Load enrichment data for artist metadata ──────────────────────────
    artist_names_raw = {(t.suggested_artist or t.artist_name or "").strip() for t in missing}
    artist_names_raw.discard("")
    enrichment_map: dict[str, ArtistEnrichment] = {}
    for name in artist_names_raw:
        enrichment = db.query(ArtistEnrichment).filter_by(
            artist_name_lower=name.lower()
        ).first()
        if enrichment:
            enrichment_map[name.lower()] = enrichment

    # ── Group missing tracks by artist ────────────────────────────────────
    artist_tracks: dict[str, list[tuple[str, str]]] = {}  # artist_lower → [(track_name, display_artist)]
    for track in missing:
        artist = (track.suggested_artist or track.artist_name or "").strip()
        track_name = (track.track_name or "").strip()
        if not artist or not track_name:
            continue
        artist_tracks.setdefault(artist.lower(), []).append((track_name, artist))

    # ── For each artist, query Lidarr for real albums + tracks ────────────
    # Result: album_suggestions[key] = {artist, album_title, track_list, image_url, ...}
    album_suggestions: dict[tuple[str, str], dict] = {}

    if has_lidarr:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Pre-fetch all existing artists in Lidarr for fast lookup
            existing_resp = await client.get(
                f"{lidarr_base}/api/v1/artist", headers=lidarr_headers
            )
            existing_artists = {}       # lowered name → artist dict
            existing_by_foreign = {}    # foreignArtistId → artist dict
            existing_artists_norm = {}  # normalised name → artist dict
            if existing_resp.status_code == 200:
                for a in existing_resp.json():
                    name_lower = a.get("artistName", "").lower()
                    existing_artists[name_lower] = a
                    fid = a.get("foreignArtistId", "")
                    if fid:
                        existing_by_foreign[fid] = a
                    norm = _normalise(a.get("artistName", ""))
                    if norm:
                        existing_artists_norm[norm] = a

            for artist_lower, track_list in artist_tracks.items():
                display_artist = track_list[0][1]  # use first track's display name
                # Deduplicate track names (same track can appear multiple times)
                seen_tracks: set[str] = set()
                track_names: list[str] = []
                for t in track_list:
                    if t[0] not in seen_tracks:
                        seen_tracks.add(t[0])
                        track_names.append(t[0])

                # Find artist in Lidarr by multiple strategies
                lidarr_artist_id = None
                lidarr_artist = None

                # Strategy 1: exact lowered name
                lidarr_artist = existing_artists.get(artist_lower)
                if lidarr_artist:
                    lidarr_artist_id = lidarr_artist.get("id")

                # Strategy 2: normalised name (handles "The Beatles" vs "Beatles", punctuation)
                if not lidarr_artist_id:
                    norm = _normalise(display_artist)
                    if norm and norm in existing_artists_norm:
                        lidarr_artist = existing_artists_norm[norm]
                        lidarr_artist_id = lidarr_artist.get("id")
                        log.info("  '%s' matched Lidarr artist '%s' by normalised name",
                                 display_artist, lidarr_artist.get("artistName"))

                # Strategy 3: MusicBrainz ID from enrichment → Lidarr foreignArtistId
                if not lidarr_artist_id:
                    enrichment = enrichment_map.get(artist_lower)
                    if enrichment and enrichment.mbid and enrichment.mbid in existing_by_foreign:
                        lidarr_artist = existing_by_foreign[enrichment.mbid]
                        lidarr_artist_id = lidarr_artist.get("id")
                        log.info("  '%s' matched Lidarr artist '%s' by MusicBrainz ID %s",
                                 display_artist, lidarr_artist.get("artistName"), enrichment.mbid)

                # Strategy 4: fuzzy name match (catches minor spelling/spacing differences)
                if not lidarr_artist_id:
                    norm = _normalise(display_artist)
                    best_score = 0.0
                    best_match = None
                    for lidarr_norm, lidarr_a in existing_artists_norm.items():
                        score = _ratio(norm, lidarr_norm)
                        if score > best_score:
                            best_score = score
                            best_match = lidarr_a
                    if best_score >= 0.85 and best_match:
                        lidarr_artist = best_match
                        lidarr_artist_id = lidarr_artist.get("id")
                        log.info("  '%s' matched Lidarr artist '%s' by fuzzy name (%.2f)",
                                 display_artist, lidarr_artist.get("artistName"), best_score)

                if not lidarr_artist_id:
                    log.info("  '%s' NOT matched in Lidarr (norm='%s', mbid=%s)",
                             display_artist, _normalise(display_artist),
                             enrichment_map.get(artist_lower, None) and enrichment_map[artist_lower].mbid)
                    # Artist not in Lidarr — can't get album tracks, group by artist
                    key = (_normalise(display_artist), "")
                    album_suggestions[key] = {
                        "artist": display_artist,
                        "album": f"Artist not in Lidarr",
                        "tracks": track_names,
                        "image_url": enrichment_map.get(artist_lower, None) and enrichment_map[artist_lower].image_url,
                        "artist_mbid": enrichment_map.get(artist_lower, None) and enrichment_map[artist_lower].mbid,
                    }
                    continue

                # Fetch album list for this artist from Lidarr
                try:
                    albums_resp = await client.get(
                        f"{lidarr_base}/api/v1/album",
                        headers=lidarr_headers,
                        params={"artistId": lidarr_artist_id},
                    )
                    if albums_resp.status_code != 200:
                        continue
                    lidarr_albums = albums_resp.json()
                except Exception as exc:
                    log.debug("Lidarr album fetch failed for '%s': %s", display_artist, exc)
                    continue

                if not lidarr_albums:
                    continue

                # Fetch track listings for each album
                # Lidarr: GET /api/v1/track?artistId=X returns ALL tracks for the artist
                try:
                    tracks_resp = await client.get(
                        f"{lidarr_base}/api/v1/track",
                        headers=lidarr_headers,
                        params={"artistId": lidarr_artist_id},
                    )
                    if tracks_resp.status_code != 200:
                        continue
                    all_lidarr_tracks = tracks_resp.json()
                except Exception as exc:
                    log.debug("Lidarr track fetch failed for '%s': %s", display_artist, exc)
                    continue

                # Build album_id → [track_title_norm, ...] mapping
                album_track_map: dict[int, list[str]] = {}
                for lt in all_lidarr_tracks:
                    aid = lt.get("albumId")
                    title = lt.get("title", "")
                    if aid and title:
                        album_track_map.setdefault(aid, []).append(_normalise(title))

                # Build album_id → album metadata
                album_meta: dict[int, dict] = {}
                for alb in lidarr_albums:
                    album_meta[alb["id"]] = {
                        "title": alb.get("title", ""),
                        "albumType": alb.get("albumType", ""),
                        "images": alb.get("images", []),
                    }

                # Score each Lidarr album: how many of our missing tracks does it contain?
                for album_id, lidarr_track_norms in album_track_map.items():
                    meta = album_meta.get(album_id)
                    if not meta:
                        continue

                    matched_tracks = []
                    for our_track in track_names:
                        our_norm = _normalise(our_track)
                        # Exact match
                        if our_norm in lidarr_track_norms:
                            matched_tracks.append(our_track)
                            continue
                        # Fuzzy match
                        for lt_norm in lidarr_track_norms:
                            if _ratio(our_norm, lt_norm) >= 0.82:
                                matched_tracks.append(our_track)
                                break

                    if matched_tracks:
                        # Get album cover image
                        cover_url = None
                        for img in meta.get("images", []):
                            if img.get("coverType") == "cover" and img.get("remoteUrl"):
                                cover_url = img["remoteUrl"]
                                break

                        key = (_normalise(display_artist), _normalise(meta["title"]))
                        # If this album already has entries (from another artist variant), merge
                        if key in album_suggestions:
                            existing = album_suggestions[key]["tracks"]
                            for t in matched_tracks:
                                if t not in existing:
                                    existing.append(t)
                        else:
                            album_suggestions[key] = {
                                "artist": display_artist,
                                "album": meta["title"],
                                "tracks": matched_tracks,
                                "image_url": cover_url,
                                "artist_mbid": enrichment_map.get(artist_lower, None) and enrichment_map[artist_lower].mbid,
                                "album_type": meta.get("albumType", ""),
                            }

                # ── Minimum set cover: pick fewest albums that cover all tracks ──
                artist_norm = _normalise(display_artist)
                artist_candidates = {
                    k: v for k, v in album_suggestions.items()
                    if k[0] == artist_norm and v.get("album") != "Artist not in Lidarr"
                }

                log.info("  '%s': %d missing tracks, %d candidate albums before set cover",
                         display_artist, len(track_names), len(artist_candidates))
                for k, v in artist_candidates.items():
                    log.info("    candidate '%s': covers %s", v["album"], v["tracks"])

                if artist_candidates:
                    uncovered = set(track_names)
                    selected_keys: list[tuple] = []

                    while uncovered:
                        best_key = None
                        best_covered: set[str] = set()
                        for k, v in artist_candidates.items():
                            if k in selected_keys:
                                continue
                            covers = {t for t in v["tracks"] if t in uncovered}
                            if len(covers) > len(best_covered):
                                best_key = k
                                best_covered = covers
                        if not best_key:
                            break
                        selected_keys.append(best_key)
                        uncovered -= best_covered
                        log.info("    set-cover picked '%s' covering %d tracks, %d still uncovered",
                                 artist_candidates[best_key]["album"], len(best_covered), len(uncovered))

                    # Remove non-selected albums for this artist
                    for k in list(artist_candidates.keys()):
                        if k not in selected_keys:
                            log.info("    dropping album '%s' (not needed)", artist_candidates[k]["album"])
                            del album_suggestions[k]

                    # Update track lists: each album keeps only its responsible tracks
                    assigned: set[str] = set()
                    for k in selected_keys:
                        sug = album_suggestions[k]
                        unique_tracks = [t for t in sug["tracks"] if t not in assigned]
                        assigned.update(unique_tracks)
                        sug["tracks"] = unique_tracks
                        sug["coverage_count"] = len(unique_tracks)

                    # Remove any albums that ended up with 0 unique tracks
                    for k in list(selected_keys):
                        if not album_suggestions[k]["tracks"]:
                            log.info("    removing '%s' (0 unique tracks after dedup)",
                                     album_suggestions[k]["album"])
                            del album_suggestions[k]

                log.info("  '%s': final result = %d albums",
                         display_artist,
                         sum(1 for k in album_suggestions
                             if k[0] == artist_norm and album_suggestions[k].get("album") != "Artist not in Lidarr"))

    # ── For artists NOT resolved via Lidarr, fall back to simple grouping ─
    resolved_artists = set()
    for key, sug in album_suggestions.items():
        resolved_artists.add(key[0])

    for artist_lower, track_list in artist_tracks.items():
        display_artist = track_list[0][1]
        if _normalise(display_artist) in resolved_artists:
            continue
        track_names = [t[0] for t in track_list]
        enrichment = enrichment_map.get(artist_lower)
        key = (_normalise(display_artist), "")
        album_suggestions[key] = {
            "artist": display_artist,
            "album": "Unknown Album",
            "tracks": track_names,
            "image_url": enrichment.image_url if enrichment else None,
            "artist_mbid": enrichment.mbid if enrichment else None,
        }

    # ── Delete stale suggestions and re-insert ────────────────────────────
    db.query(ImportAlbumSuggestion).filter_by(playlist_id=playlist_id).delete()

    # Sort by coverage count descending
    sorted_suggestions = sorted(album_suggestions.items(), key=lambda x: len(x[1]["tracks"]), reverse=True)

    created = 0
    for key, sug in sorted_suggestions:
        suggestion = ImportAlbumSuggestion(
            playlist_id    = playlist_id,
            artist_name    = sug["artist"],
            album_name     = sug["album"],
            coverage_count = len(sug["tracks"]),
            lidarr_status  = "pending",
            artist_mbid    = sug.get("artist_mbid"),
            image_url      = sug.get("image_url"),
            missing_tracks = json.dumps(sug["tracks"]),
        )
        db.add(suggestion)
        created += 1

    db.commit()
    log.info("Playlist %d: built %d album suggestions from Lidarr catalog", playlist_id, created)
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

    track_norm  = _normalise(lib_track.track_name)
    artist_norm = _normalise(lib_track.artist_name)

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
