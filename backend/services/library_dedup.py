"""
JellyDJ Library Dedup — Module 8b

Fuzzy matching utilities that check candidate albums/tracks against the
LibraryTrack table before they enter the discovery queue or trigger downloads.

Core problem solved:
  - User has "Shape of You" from an Ed Sheeran Greatest Hits compilation.
    LibraryTrack knows this as artist="Ed Sheeran", album="Greatest Hits".
    Recommender suggests album "÷ (Divide)" by Ed Sheeran.
    Before queuing ÷ (Divide), we check how many of its tracks we already
    own (by title fuzzy-match against LibraryTrack for the same artist).
    If the *unreleased* tracks have high enough quality signal, we still
    suggest it. If not, we skip.

  - User has no Ed Sheeran at all. Recommender suggests "÷ (Divide)".
    No library tracks match → suggest normally.

Fuzzy matching strategy:
  - Artist match: token-sort ratio >= 85 (handles "The Beatles" vs "Beatles")
  - Track match: token-sort ratio >= 80 (handles minor title variations,
    "(Acoustic)", "(Remastered)", trailing punctuation)
  - Album match: used for queue dedup, ratio >= 75

We avoid the difflib SequenceMatcher for this — we implement a simple
token-sort comparison that handles the most common cases without
external dependencies.
"""
from __future__ import annotations

import re
import logging
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from models import LibraryTrack

log = logging.getLogger(__name__)


# ── String normalisation ──────────────────────────────────────────────────────

# Tokens to strip before comparing (articles, common suffixes)
_STRIP_WORDS = {"the", "a", "an"}

# Phase 1: strip entire parenthetical/bracketed blocks that contain edition noise.
# Catches: (2015 Remaster), [Super Deluxe Edition], (20th Anniversary Edition),
#          (Explicit), [Bonus Track], (feat. X), (Live at Wembley), etc.
# The key insight: we strip the WHOLE bracket group if it contains any noise word,
# rather than trying to match exact patterns — this handles arbitrary combinations
# like "(2015 Super Deluxe Remastered Anniversary Edition)".
_STRIP_BRACKET_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b("
    r"remaster(?:ed)?|deluxe|expanded|bonus|anniversary|edition|explicit|clean"
    r"|acoustic|live|radio\s*edit|single\s*version|album\s*version|re-?issue"
    r"|collector|limited|special|super|feat\.?|ft\.?|with\s"
    r").*?[\)\]]",
    re.IGNORECASE,
)

# Phase 2: strip bare suffixes that appear without brackets at end of string.
# Catches: "Nevermind - Remastered", "Abbey Road (2019 Mix)" already handled above,
# but also "Something Remastered 2011" with no brackets.
_STRIP_BARE_SUFFIX = re.compile(
    r"\s*[-–]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|deluxe|re-?issue|re-?master)(?:\s+\d{4})?$",
    re.IGNORECASE,
)

# Phase 3: after all bracket/suffix stripping, remove any orphaned 4-digit years
# and common noise words that the above passes might leave behind.
_STRIP_ORPHAN_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_STRIP_NOISE_WORDS = re.compile(
    r"\b(remaster(?:ed)?|deluxe|expanded|bonus|anniversary|edition|explicit"
    r"|super|special|collector|limited|version|reissue|re-?issue)\b",
    re.IGNORECASE,
)


def _normalise(s: str) -> str:
    """
    Lowercase, strip all edition/remaster/year noise, remove punctuation, sort tokens.

    Three-phase approach:
      1. Strip whole bracket groups containing noise keywords
         → removes (2015 Remaster), [Super Deluxe Edition], (feat. X), etc.
      2. Strip bare end-of-string remaster suffixes without brackets
         → removes "- Remastered 2011", "- Deluxe"
      3. Strip orphaned years and leftover noise words
         → removes stray "2015", "Remastered" tokens left after phase 1/2

    This makes "Jagged Little Pill (2015 Remaster)" normalise to the same
    token-sorted string as "Jagged Little Pill".
    """
    if not s:
        return ""
    s = s.lower()
    s = _STRIP_BRACKET_NOISE.sub("", s)
    s = _STRIP_BARE_SUFFIX.sub("", s)
    s = _STRIP_ORPHAN_YEAR.sub("", s)
    s = _STRIP_NOISE_WORDS.sub("", s)
    # Remove punctuation except spaces
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = [t for t in s.split() if t not in _STRIP_WORDS]
    return " ".join(sorted(tokens))


def _similarity(a: str, b: str) -> float:
    """
    Token-sort similarity ratio (0.0–1.0) using longest common subsequence.

    Why not difflib.SequenceMatcher?
      - SequenceMatcher is character-level and doesn't handle token reordering
        well (e.g. "Abbey Road Remastered" vs "Remastered Abbey Road").
      - This implementation normalises then sorts tokens first, so word-order
        differences don't reduce the score.

    Why not a third-party library like rapidfuzz?
      - Keeping dependencies minimal for a self-hosted project.
      - The LCS approach is accurate enough for artist/album names.

    Performance: O(m*n) DP where m,n are normalised string lengths.
    Typical album names are < 50 chars, so this is fine at < 5000 comparisons.
    For very large libraries (100k+ tracks) consider adding a prefix index.
    """
    na, nb = _normalise(a), _normalise(b)
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    # LCS-based ratio
    la, lb = len(na), len(nb)
    # Quick prefix check
    shorter, longer = (na, nb) if la <= lb else (nb, na)
    if shorter in longer:
        return len(shorter) / len(longer)
    # DP LCS
    prev = [0] * (len(shorter) + 1)
    for ch in longer:
        curr = [0] * (len(shorter) + 1)
        for i, c in enumerate(shorter):
            curr[i + 1] = prev[i] + 1 if c == ch else max(curr[i], prev[i + 1])
        prev = curr
    lcs = prev[-1]
    return (2 * lcs) / (la + lb)


def artist_matches(a: str, b: str, threshold: float = 0.85) -> bool:
    return _similarity(a, b) >= threshold


def album_matches(a: str, b: str, threshold: float = 0.75) -> bool:
    return _similarity(a, b) >= threshold


def track_matches(a: str, b: str, threshold: float = 0.80) -> bool:
    return _similarity(a, b) >= threshold


# ── Library lookup ────────────────────────────────────────────────────────────

def get_artist_tracks_in_library(
    artist_name: str,
    db: Session,
) -> list[LibraryTrack]:
    """
    Return all LibraryTrack rows for a given artist (fuzzy name match).
    Checks both artist_name and album_artist fields.
    """
    # Exact match first (fast path)
    exact = (
        db.query(LibraryTrack)
        .filter(
            LibraryTrack.missing_since.is_(None),
            func.lower(LibraryTrack.artist_name) == artist_name.lower(),
        )
        .all()
    )
    if exact:
        return exact

    # Fuzzy match — fetch all and filter in Python
    # This is fine at <5000 tracks; would need an index for 100k+
    all_tracks = (
        db.query(LibraryTrack)
        .filter(LibraryTrack.missing_since.is_(None))
        .all()
    )
    return [
        t for t in all_tracks
        if artist_matches(t.artist_name, artist_name)
        or artist_matches(t.album_artist, artist_name)
    ]


def artist_in_library(artist_name: str, db: Session) -> bool:
    """Quick check — does this artist exist anywhere in the library?"""
    return len(get_artist_tracks_in_library(artist_name, db)) > 0


def album_in_library(artist_name: str, album_name: str, db: Session) -> bool:
    """
    Check if we already have this album (by artist+album fuzzy match).
    Used for quick queue dedup.
    """
    artist_tracks = get_artist_tracks_in_library(artist_name, db)
    return any(album_matches(t.album_name, album_name) for t in artist_tracks)


def tracks_in_library_for_album(
    artist_name: str,
    track_names: list[str],
    db: Session,
) -> tuple[int, int]:
    """
    Given a list of track names from an album, count how many we already own
    by this artist (regardless of which album they're tagged under).
    Returns (owned_count, total_count).
    """
    if not track_names:
        return 0, 0

    artist_tracks = get_artist_tracks_in_library(artist_name, db)
    owned_names = {_normalise(t.track_name) for t in artist_tracks}

    owned = sum(
        1 for name in track_names
        if _normalise(name) in owned_names
        or any(track_matches(t.track_name, name) for t in artist_tracks)
    )
    return owned, len(track_names)


# ── Lidarr pre-validation ─────────────────────────────────────────────────────

async def validate_album_in_lidarr(
    artist_name: str,
    album_name: str,
    base_url: str,
    api_key: str,
) -> dict:
    """
    Check whether Lidarr can find this artist+album before we add it to the queue.
    Returns:
      {
        "found": bool,
        "lidarr_artist_name": str,
        "lidarr_album_name": str,
        "album_type": str,       # "Album" | "Single" | "EP" | "Compilation" | ...
        "is_compilation": bool,
        "foreign_artist_id": str,
        "track_count": int,
        "track_names": list[str],
      }
    """
    import httpx

    headers = {"X-Api-Key": api_key}
    result = {
        "found": False,
        "lidarr_artist_name": "",
        "lidarr_album_name": "",
        "album_type": "",
        "is_compilation": False,
        "foreign_artist_id": "",
        "track_count": 0,
        "track_names": [],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Step 1: look up artist
            r = await client.get(
                f"{base_url}/api/v1/artist/lookup",
                headers=headers,
                params={"term": artist_name},
            )
            if r.status_code != 200 or not r.json():
                return result

            candidates = r.json()
            # Find best artist match
            best_artist = None
            best_score = 0.0
            for c in candidates[:5]:
                score = _similarity(c.get("artistName", ""), artist_name)
                if score > best_score:
                    best_score = score
                    best_artist = c

            if not best_artist or best_score < 0.7:
                log.debug(f"  Lidarr validation: no good artist match for '{artist_name}'")
                return result

            result["lidarr_artist_name"] = best_artist.get("artistName", "")
            result["foreign_artist_id"] = best_artist.get("foreignArtistId", "")

            # Step 2: look up albums for this artist via MusicBrainz ID
            foreign_id = best_artist.get("foreignArtistId", "")
            if not foreign_id:
                return result

            alb_r = await client.get(
                f"{base_url}/api/v1/album/lookup",
                headers=headers,
                params={"term": f"lidarr:{foreign_id}"},
            )
            if alb_r.status_code != 200:
                return result

            albums = alb_r.json()
            if not albums:
                return result

            # Find best album match — filter out compilations first
            COMPILATION_TYPES = {"compilation", "soundtrack", "mixtape/street"}
            COMPILATION_TITLE_PATTERNS = re.compile(
                r"\b(greatest hits?|best of|essential|collection|anthology"
                r"|platinum|gold|singles|hits|complete|definitive)\b",
                re.IGNORECASE,
            )

            studio_albums = [
                a for a in albums
                if a.get("albumType", "").lower() not in COMPILATION_TYPES
                and not COMPILATION_TITLE_PATTERNS.search(a.get("title", ""))
            ]

            search_pool = studio_albums if studio_albums else albums

            # Score all albums against requested album name
            if album_name:
                scored = [(a, _similarity(a.get("title", ""), album_name)) for a in search_pool]
                scored.sort(key=lambda x: x[1], reverse=True)
                best_score = scored[0][1] if scored else 0.0
                best_album = scored[0][0] if best_score > 0.55 else None
            else:
                # No album name specified — don't silently pick a random album.
                # Return found=False so the caller queues the artist without
                # a specific album target. _send_to_lidarr handles artist-only adds.
                log.debug(f"  Lidarr validation: no album_name for '{artist_name}', skipping album match")
                return result

            if not best_album:
                log.debug(f"  Lidarr validation: no album match for '{album_name}' by '{artist_name}'")
                return result

            album_type = best_album.get("albumType", "")
            is_comp = (
                album_type.lower() in COMPILATION_TYPES
                or bool(COMPILATION_TITLE_PATTERNS.search(best_album.get("title", "")))
            )

            # Extract track names from media/tracks
            track_names: list[str] = []
            for medium in best_album.get("media", []):
                for track in medium.get("tracks", []):
                    name = track.get("trackName") or track.get("title") or ""
                    if name:
                        track_names.append(name)

            result.update({
                "found": True,
                "lidarr_album_name": best_album.get("title", ""),
                "album_type": album_type,
                "is_compilation": is_comp,
                "track_count": len(track_names),
                "track_names": track_names,
            })

    except Exception as e:
        log.warning(f"  Lidarr validation error for '{artist_name}'/'{album_name}': {e}")

    return result