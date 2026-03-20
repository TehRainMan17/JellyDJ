"""
JellyDJ Holiday Tagger — v1

Detects whether a LibraryTrack belongs to a seasonal/holiday collection and
stamps it with a holiday tag.  Tracks flagged this way are excluded from all
playlist and discovery output outside of a configurable seasonal window.

Supported holidays and their windows
─────────────────────────────────────
  christmas      Nov 25 – Jan 5
  hanukkah       Dec 1  – Jan 5   (window is intentionally generous; actual
                                   dates shift each year)
  halloween      Oct 1  – Nov 5
  thanksgiving   Nov 1  – Nov 30  (US)
  easter         Mar 15 – Apr 30  (window covers possible date range)
  valentines     Feb 1  – Feb 20
  new_year       Dec 26 – Jan 10

Detection strategy (in priority order)
────────────────────────────────────────
  1. Genre field contains a holiday keyword  (fastest, data from Jellyfin)
  2. Album name contains holiday keywords
  3. Track name contains holiday keywords
  4. LibraryTrack.tags (Last.fm / MusicBrainz) contains holiday keywords

A track that matches multiple holidays gets the first match in HOLIDAY_RULES
order (Christmas takes priority since it has the most overlap with other
winter holidays).

Public API
──────────────────────────────────────────────────────────────────────────────
  tag_track(track)           → str | None   (holiday slug or None)
  is_in_season(holiday_slug) → bool
  should_exclude(track)      → bool         (True = keep out of playlists now)
  tag_library(db)            → dict         (stats)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ── Holiday definitions ───────────────────────────────────────────────────────

# Each entry: (slug, keywords, season_start (month, day), season_end (month, day))
# Season windows are intentionally generous — we'd rather include one extra
# week of Christmas music availability than block it too early.
#
# Months/days use (month, day) tuples.  Ranges that cross the year boundary
# (e.g. Christmas starting Nov 25 and ending Jan 5) are handled by
# _in_window() which checks wrapping automatically.

HOLIDAY_RULES: list[tuple[str, list[str], tuple[int,int], tuple[int,int]]] = [
    (
        "christmas",
        [
            "christmas", "xmas", "x-mas", "noel", "noël", "navidad", "weihnacht",
            "jingle", "santa", "rudolph", "frosty", "sleigh", "reindeer",
            "yuletide", "yule", "carol", "caroling", "carolling",
            "deck the halls", "silent night", "holy night", "winter wonderland",
            "chestnuts roasting", "12 days", "twelve days",
            "away in a manger", "o come", "we wish you",
        ],
        (11, 25),   # Nov 25
        (1,  5),    # Jan 5
    ),
    (
        "hanukkah",
        [
            "hanukkah", "chanukah", "hannukah", "dreidel", "menorah",
            "festival of lights", "latke",
        ],
        (12, 1),    # Dec 1
        (1,  5),    # Jan 5
    ),
    (
        "halloween",
        [
            "halloween", "hallow", "spooky", "haunted", "haunting",
            "monster mash", "thriller", "ghost", "witch", "pumpkin",
            "trick or treat", "trick-or-treat", "skeleton", "vampire",
            "werewolf", "zombie", "nightmare", "horror",
        ],
        (10, 1),    # Oct 1
        (11, 5),    # Nov 5
    ),
    (
        "thanksgiving",
        [
            "thanksgiving", "turkey day", "harvest feast",
            "pilgrim", "giving thanks",
        ],
        (11, 1),    # Nov 1
        (11, 30),   # Nov 30
    ),
    (
        "easter",
        [
            "easter", "resurrection sunday", "passover",
            "easter parade", "here comes peter cottontail",
        ],
        (3, 15),    # Mar 15
        (4, 30),    # Apr 30
    ),
    (
        "valentines",
        [
            "valentine", "valentines", "st. valentine", "saint valentine",
        ],
        (2, 1),     # Feb 1
        (2, 20),    # Feb 20
    ),
    (
        "new_year",
        [
            "new year", "new years", "auld lang syne",
            "happy new year", "new year's eve", "new year's day",
            "countdown to midnight",
        ],
        (12, 26),   # Dec 26
        (1, 10),    # Jan 10
    ),
]

# Build a quick lookup: slug → (start, end)
_SEASON_WINDOWS: dict[str, tuple[tuple[int,int], tuple[int,int]]] = {
    slug: (start, end) for slug, _kw, start, end in HOLIDAY_RULES
}


# ── Season window arithmetic ──────────────────────────────────────────────────

def _in_window(today: date, start: tuple[int,int], end: tuple[int,int]) -> bool:
    """
    Return True if today falls within [start, end] (inclusive), correctly
    handling ranges that wrap across the year boundary (e.g. Nov 25 → Jan 5).
    """
    sm, sd = start
    em, ed = end
    m, d = today.month, today.day

    start_ord = sm * 100 + sd
    end_ord   = em * 100 + ed
    today_ord = m  * 100 + d

    if start_ord <= end_ord:
        # Normal range (does not cross year boundary)
        return start_ord <= today_ord <= end_ord
    else:
        # Wrapping range (e.g. Nov 25 → Jan 5)
        return today_ord >= start_ord or today_ord <= end_ord


def is_in_season(holiday_slug: str, today: Optional[date] = None) -> bool:
    """Return True if the named holiday's season window is currently active."""
    if holiday_slug not in _SEASON_WINDOWS:
        return False
    if today is None:
        today = datetime.utcnow().date()
    start, end = _SEASON_WINDOWS[holiday_slug]
    return _in_window(today, start, end)


# ── Keyword matching ──────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    """Lowercase and collapse punctuation for matching."""
    return re.sub(r"[^\w\s]", " ", s.lower())


def _match_holiday(text: str) -> Optional[str]:
    """
    Return the slug of the first matching holiday, or None.
    Checks the normalised text against each holiday's keyword list in order.
    """
    norm = _normalise(text)
    for slug, keywords, _start, _end in HOLIDAY_RULES:
        for kw in keywords:
            # Use word-boundary matching so "easter" doesn't match "northeast"
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, norm):
                return slug
    return None


def tag_track(track) -> Optional[str]:
    """
    Inspect a LibraryTrack ORM object and return a holiday slug if detected,
    or None if the track has no holiday association.

    Detection priority:
      1. Genre
      2. Album name
      3. Track name
      4. Last.fm tags (stored as JSON list on LibraryTrack.tags)
    """
    # 1. Genre
    if track.genre:
        hit = _match_holiday(track.genre)
        if hit:
            return hit

    # 2. Album name
    if track.album_name:
        hit = _match_holiday(track.album_name)
        if hit:
            return hit

    # 3. Track name
    if track.track_name:
        hit = _match_holiday(track.track_name)
        if hit:
            return hit

    # 4. Enrichment tags (JSON list of strings)
    if track.tags:
        try:
            tags = json.loads(track.tags)
            joined = " ".join(tags)
            hit = _match_holiday(joined)
            if hit:
                return hit
        except Exception:
            pass

    return None


def should_exclude(track, today: Optional[date] = None) -> bool:
    """
    Return True if this track should be excluded from playlists/suggestions
    right now.  A track is excluded when:
      - It has a holiday_tag, AND
      - That holiday's season window is not currently active.
    Tracks with no holiday tag are never excluded by this function.
    """
    holiday = getattr(track, "holiday_tag", None)
    if not holiday:
        return False
    return not is_in_season(holiday, today)


# ── Bulk library tagger ───────────────────────────────────────────────────────

def tag_library(db: Session) -> dict:
    """
    Scan all active LibraryTrack rows and stamp holiday_tag / holiday_exclude.

    holiday_tag     — the slug of the detected holiday (or None)
    holiday_exclude — True if the track should be excluded right now

    Called by the library scanner after each scan run, and can be triggered
    manually via the API.  Only writes rows that actually need to change to
    avoid unnecessary DB churn.

    Returns stats dict with tagged/untagged/updated counts.
    """
    from models import LibraryTrack

    today = datetime.utcnow().date()
    tracks = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).all()

    tagged   = 0
    untagged = 0
    updated  = 0

    for track in tracks:
        new_tag     = tag_track(track)
        new_exclude = bool(new_tag and not is_in_season(new_tag, today))

        old_tag     = track.holiday_tag
        old_exclude = track.holiday_exclude

        if new_tag != old_tag or new_exclude != old_exclude:
            track.holiday_tag     = new_tag
            track.holiday_exclude = new_exclude
            updated += 1

        if new_tag:
            tagged += 1
        else:
            untagged += 1

    db.commit()

    log.info(
        f"Holiday tagger: {tagged} holiday tracks ({updated} rows updated), "
        f"{untagged} non-holiday tracks"
    )

    # Log a breakdown by holiday for visibility
    breakdown: dict[str, int] = {}
    for track in tracks:
        if track.holiday_tag:
            breakdown[track.holiday_tag] = breakdown.get(track.holiday_tag, 0) + 1
    if breakdown:
        log.info(f"  Holiday breakdown: {breakdown}")

    return {
        "total_scanned": len(tracks),
        "tagged": tagged,
        "untagged": untagged,
        "updated": updated,
        "breakdown": breakdown,
        "season_active": {
            slug: is_in_season(slug, today) for slug in _SEASON_WINDOWS
        },
    }


def refresh_exclude_flags(db: Session) -> dict:
    """
    Re-evaluate holiday_exclude for all already-tagged tracks based on today's
    date.  Much cheaper than a full re-tag — only touches holiday tracks.

    Called by the scheduler at midnight (or on any config change) so the
    exclusion flags flip automatically as seasons open/close.
    """
    from models import LibraryTrack

    today = datetime.utcnow().date()
    holiday_tracks = db.query(LibraryTrack).filter(
        LibraryTrack.holiday_tag.isnot(None),
        LibraryTrack.missing_since.is_(None),
    ).all()

    flipped = 0
    for track in holiday_tracks:
        new_exclude = not is_in_season(track.holiday_tag, today)
        if track.holiday_exclude != new_exclude:
            track.holiday_exclude = new_exclude
            flipped += 1

    db.commit()

    log.info(
        f"Holiday flags refreshed: {len(holiday_tracks)} holiday tracks, "
        f"{flipped} exclusion flags flipped"
    )
    return {"holiday_tracks": len(holiday_tracks), "flags_flipped": flipped}