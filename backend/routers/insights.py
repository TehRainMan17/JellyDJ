
"""
JellyDJ Insights router — Module 8c

Exposes pre-computed TrackScore, ArtistProfile, and GenreProfile data
for auditing and exploration. Answers questions like:
  - What does JellyDJ think are my top 50 tracks?
  - Which artists am I ranked highest on?
  - What are my most-skipped tracks?
  - Show me all unplayed tracks sorted by score
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func
from typing import Optional

from database import get_db
from models import TrackScore, ArtistProfile, GenreProfile, ManagedUser, Play, SkipPenalty

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _resolve_user(user_id: Optional[str], username: Optional[str], db: Session) -> str:
    """Resolve either user_id or username to a jellyfin_user_id."""
    if user_id:
        return user_id
    if username:
        u = db.query(ManagedUser).filter(
            ManagedUser.username.ilike(username)
        ).first()
        if not u:
            raise HTTPException(404, f"User '{username}' not found")
        return u.jellyfin_user_id
    raise HTTPException(400, "Provide user_id or username")


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """All managed users — for populating the user picker in the UI."""
    users = db.query(ManagedUser).filter_by(is_enabled=True).all()
    return [
        {"jellyfin_user_id": u.jellyfin_user_id, "username": u.username}
        for u in users
    ]


@router.get("/tracks")
def get_tracks(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    sort_by: str = Query("final_score", description="final_score|play_count|last_played|artist_name|track_name|skip_penalty"),
    order: str = Query("desc", description="asc|desc"),
    played_filter: str = Query("all", description="all|played|unplayed"),
    artist_filter: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """
    Browse all track scores for a user with sorting and filtering.
    The primary audit view — lets you see exactly how JellyDJ ranks every track.
    Now includes skip_count and total_events from SkipPenalty for verification.
    """
    uid = _resolve_user(user_id, username, db)

    q = db.query(TrackScore).filter_by(user_id=uid)

    # Played filter
    if played_filter == "played":
        q = q.filter(TrackScore.is_played == True)
    elif played_filter == "unplayed":
        q = q.filter(TrackScore.is_played == False)

    # Artist filter
    if artist_filter:
        q = q.filter(TrackScore.artist_name.ilike(f"%{artist_filter}%"))

    # Sorting — scores are stored as TEXT in SQLite.
    # SQLite ignores CAST() in ORDER BY when a text index exists on the column,
    # causing lexicographic ordering ("100" < "20" < "9"). Fix: use raw SQL
    # expressions that SQLite cannot satisfy with the text index.
    from sqlalchemy import text as satext

    def _order_expr(raw_sql):
        return satext(f"{raw_sql} DESC" if order == "desc" else f"{raw_sql} ASC")

    if sort_by == "skip_count":
        # skip_count is in SkipPenalty, not TrackScore — load all rows, sort in Python
        total = q.count()
        all_skip_rows = db.query(SkipPenalty).filter(SkipPenalty.user_id == uid).all()
        pre_skip_map = {sk.jellyfin_item_id: sk.skip_count or 0 for sk in all_skip_rows}
        all_rows = q.order_by(satext("CAST(final_score AS REAL) DESC")).all()
        all_rows.sort(
            key=lambda r: pre_skip_map.get(r.jellyfin_item_id, 0),
            reverse=(order == "desc")
        )
        rows = all_rows[(page - 1) * page_size: page * page_size]
    else:
        sql_sort_map = {
            "final_score":     "CAST(final_score AS REAL)",
            "play_count":      "play_count",
            "last_played":     "last_played",
            "artist_name":     "artist_name",
            "track_name":      "track_name",
            "skip_penalty":    "CAST(skip_penalty AS REAL)",
            "artist_affinity": "CAST(artist_affinity AS REAL)",
        }
        raw = sql_sort_map.get(sort_by, "CAST(final_score AS REAL)")
        q = q.order_by(_order_expr(raw))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

    # Batch-load SkipPenalty rows for this page to get skip_count / total_events
    item_ids = [r.jellyfin_item_id for r in rows]
    skip_map: dict[str, SkipPenalty] = {}
    if item_ids:
        skip_rows = (
            db.query(SkipPenalty)
            .filter(
                SkipPenalty.user_id == uid,
                SkipPenalty.jellyfin_item_id.in_(item_ids)
            )
            .all()
        )
        skip_map = {sr.jellyfin_item_id: sr for sr in skip_rows}

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "tracks": [
            {
                "jellyfin_item_id": r.jellyfin_item_id,
                "track_name": r.track_name,
                "artist_name": r.artist_name,
                "album_name": r.album_name,
                "genre": r.genre,
                "play_count": r.play_count,
                "last_played": r.last_played,
                "is_played": r.is_played,
                "is_favorite": r.is_favorite,
                "final_score": float(r.final_score),
                "play_score": float(r.play_score),
                "recency_score": float(r.recency_score),
                "artist_affinity": float(r.artist_affinity),
                "genre_affinity": float(r.genre_affinity),
                "skip_penalty": float(r.skip_penalty),
                "novelty_bonus": float(r.novelty_bonus),
                # Live skip tracking data from SkipPenalty table
                "skip_count": skip_map[r.jellyfin_item_id].skip_count
                    if r.jellyfin_item_id in skip_map else 0,
                "total_events": skip_map[r.jellyfin_item_id].total_events
                    if r.jellyfin_item_id in skip_map else 0,
                "skip_rate": float(skip_map[r.jellyfin_item_id].skip_rate)
                    if r.jellyfin_item_id in skip_map else 0.0,
            }
            for r in rows
        ],
    }


@router.get("/artists")
def get_artists(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    sort_by: str = Query("affinity_score", description="affinity_score|total_plays|skip_rate|artist_name"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """Artist profiles — see affinity score, play counts, skip rates per artist."""
    uid = _resolve_user(user_id, username, db)

    q = db.query(ArtistProfile).filter_by(user_id=uid)

    from sqlalchemy import cast, Float as SAFloat, func as sqlfunc

    # Always load live skip data first — used for sorting AND display
    live_skips_q = (
        db.query(
            SkipPenalty.artist_name,
            sqlfunc.sum(SkipPenalty.skip_count).label("skip_count"),
            sqlfunc.sum(SkipPenalty.total_events).label("total_events"),
        )
        .filter(SkipPenalty.user_id == uid)
        .filter(SkipPenalty.artist_name.isnot(None))
        .group_by(SkipPenalty.artist_name)
        .all()
    )
    live_skip_map = {
        r.artist_name: {
            "skip_count": int(r.skip_count or 0),
            "total_events": int(r.total_events or 0),
            "skip_rate": (r.skip_count / r.total_events) if r.total_events else 0.0,
        }
        for r in live_skips_q
    }

    # Sort — use raw SQL to bypass SQLite text index on TEXT-stored numeric columns
    from sqlalchemy import text as satext

    def _aord(raw_sql):
        return satext(f"{raw_sql} DESC" if order == "desc" else f"{raw_sql} ASC")

    if sort_by == "skip_rate":
        # Sort on live skip data from SkipPenalty (post-query Python sort)
        total = q.count()
        all_rows = q.order_by(satext("CAST(affinity_score AS REAL) DESC")).all()
        all_rows.sort(
            key=lambda r: live_skip_map.get(r.artist_name, {}).get("skip_rate", 0.0),
            reverse=(order == "desc")
        )
        rows = all_rows[(page - 1) * page_size: page * page_size]
    else:
        sql_map = {
            "affinity_score": "CAST(affinity_score AS REAL)",
            "total_plays":    "total_plays",
            "artist_name":    "artist_name",
        }
        raw = sql_map.get(sort_by, "CAST(affinity_score AS REAL)")
        q = q.order_by(_aord(raw))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "artists": [
            {
                "artist_name": r.artist_name,
                "affinity_score": float(r.affinity_score),
                "total_plays": r.total_plays,
                "total_tracks_played": r.total_tracks_played,
                "total_skips": live_skip_map.get(r.artist_name, {}).get("skip_count", 0),
                "total_events": live_skip_map.get(r.artist_name, {}).get("total_events", 0),
                "skip_rate": live_skip_map.get(r.artist_name, {}).get("skip_rate", 0.0),
                "has_favorite": r.has_favorite,
                "primary_genre": r.primary_genre,
            }
            for r in rows
        ],
    }


@router.get("/genres")
def get_genres(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Genre profiles sorted by affinity — see what genres JellyDJ thinks you love."""
    uid = _resolve_user(user_id, username, db)
    rows = db.query(GenreProfile).filter_by(user_id=uid)\
             .order_by(desc(GenreProfile.affinity_score)).all()
    return [
        {
            "genre": r.genre,
            "affinity_score": float(r.affinity_score),
            "total_plays": r.total_plays,
            "total_skips": r.total_skips,
            "skip_rate": float(r.skip_rate),
            "has_favorite": r.has_favorite,
        }
        for r in rows
    ]


@router.get("/summary")
def get_summary(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Quick summary card for the top of the Insights page.
    Top 5 artists, top 5 genres, most skipped artist, total counts.
    """
    uid = _resolve_user(user_id, username, db)

    total_tracks = db.query(TrackScore).filter_by(user_id=uid).count()
    played_tracks = db.query(TrackScore).filter_by(user_id=uid, is_played=True).count()
    total_artists = db.query(ArtistProfile).filter_by(user_id=uid).count()

    top_artists = db.query(ArtistProfile).filter_by(user_id=uid)\
                    .order_by(desc(ArtistProfile.affinity_score)).limit(5).all()

    top_genres = db.query(GenreProfile).filter_by(user_id=uid)\
                   .order_by(desc(GenreProfile.affinity_score)).limit(5).all()

    # Most skipped artist — read live from SkipPenalty (always current, not index-stale)
    from sqlalchemy import func as sqlfunc
    live_skip_agg = (
        db.query(
            SkipPenalty.artist_name,
            sqlfunc.sum(SkipPenalty.skip_count).label("total_skips"),
            sqlfunc.sum(SkipPenalty.total_events).label("total_events"),
        )
        .filter(SkipPenalty.user_id == uid)
        .filter(SkipPenalty.artist_name.isnot(None))
        .group_by(SkipPenalty.artist_name)
        .having(sqlfunc.sum(SkipPenalty.total_events) >= 3)
        .all()
    )
    most_skipped = None
    if live_skip_agg:
        best = max(
            live_skip_agg,
            key=lambda r: (r.total_skips / r.total_events) if r.total_events else 0
        )
        if best.total_events and best.total_skips:
            most_skipped = type("MS", (), {
                "artist_name": best.artist_name,
                "skip_rate":   best.total_skips / best.total_events,
                "total_skips": best.total_skips,
            })()

    # Highest scored track
    top_track = db.query(TrackScore).filter_by(user_id=uid)\
                  .order_by(desc(TrackScore.final_score)).first()

    # Most played track
    most_played = db.query(TrackScore).filter_by(user_id=uid)\
                    .filter(TrackScore.is_played == True)\
                    .order_by(desc(TrackScore.play_count)).first()

    # Live skip summary from SkipPenalty (reflects webhook updates immediately)
    total_skip_events = db.query(SkipPenalty).filter_by(user_id=uid).count()
    total_skips_recorded = db.query(
        func.sum(SkipPenalty.skip_count)
    ).filter_by(user_id=uid).scalar() or 0

    return {
        "total_tracks_in_library": total_tracks,
        "played_tracks": played_tracks,
        "unplayed_tracks": total_tracks - played_tracks,
        "total_artists": total_artists,
        "top_artists": [
            {"artist_name": r.artist_name, "affinity_score": float(r.affinity_score),
             "total_plays": r.total_plays}
            for r in top_artists
        ],
        "top_genres": [
            {"genre": r.genre, "affinity_score": float(r.affinity_score),
             "total_plays": r.total_plays}
            for r in top_genres
        ],
        "most_skipped_artist": {
            "artist_name": most_skipped.artist_name,
            "skip_rate": float(most_skipped.skip_rate),
            "total_skips": most_skipped.total_skips,
        } if most_skipped else None,
        "top_track": {
            "track_name": top_track.track_name,
            "artist_name": top_track.artist_name,
            "final_score": float(top_track.final_score),
            "play_count": top_track.play_count,
        } if top_track else None,
        "most_played_track": {
            "track_name": most_played.track_name,
            "artist_name": most_played.artist_name,
            "play_count": most_played.play_count,
            "final_score": float(most_played.final_score),
        } if most_played else None,
        # Live skip tracking stats (from SkipPenalty, updated by webhooks in real-time)
        "skip_tracking": {
            "tracks_with_events": total_skip_events,
            "total_skips_recorded": int(total_skips_recorded),
        },
    }
