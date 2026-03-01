"""
JellyDJ Insights router — v2

New in v2:
  - /tracks now includes cooldown_until, skip_streak, replay_boost, global_popularity
  - /artists now includes related_artists, tags, replay_boost
  - /cooldowns/{user_id}  — browse all active/historical cooldowns
  - /enrichment/status    — see enrichment progress across the library
  - /summary              — adds cooldown counts, replay signal counts
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func
from typing import Optional
from datetime import datetime

from database import get_db
from models import TrackScore, ArtistProfile, GenreProfile, ManagedUser, Play, SkipPenalty

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _resolve_user(user_id: Optional[str], username: Optional[str], db: Session) -> str:
    if user_id:
        return user_id
    if username:
        u = db.query(ManagedUser).filter(ManagedUser.username.ilike(username)).first()
        if not u:
            raise HTTPException(404, f"User '{username}' not found")
        return u.jellyfin_user_id
    raise HTTPException(400, "Provide user_id or username")


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(ManagedUser).filter_by(is_enabled=True).all()
    return [
        {"jellyfin_user_id": u.jellyfin_user_id, "username": u.username}
        for u in users
    ]


@router.get("/tracks")
def get_tracks(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    sort_by: str = Query("final_score"),
    order: str = Query("desc"),
    played_filter: str = Query("all", description="all|played|unplayed"),
    cooldown_filter: str = Query("all", description="all|active|clear"),
    artist_filter: Optional[str] = Query(None),
    holiday_filter: str = Query("all", description="all|holiday|excluded|normal"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    uid = _resolve_user(user_id, username, db)

    q = db.query(TrackScore).filter_by(user_id=uid)

    if played_filter == "played":
        q = q.filter(TrackScore.is_played == True)
    elif played_filter == "unplayed":
        q = q.filter(TrackScore.is_played == False)

    # v2: cooldown filter
    now = datetime.utcnow()
    if cooldown_filter == "active":
        q = q.filter(TrackScore.cooldown_until > now)
    elif cooldown_filter == "clear":
        q = q.filter(
            (TrackScore.cooldown_until == None) |
            (TrackScore.cooldown_until <= now)
        )

    if artist_filter:
        q = q.filter(TrackScore.artist_name.ilike(f"%{artist_filter}%"))

    # v4: holiday filter
    if holiday_filter == "holiday":
        q = q.filter(TrackScore.holiday_tag.isnot(None))
    elif holiday_filter == "excluded":
        q = q.filter(TrackScore.holiday_tag.isnot(None), TrackScore.holiday_exclude == True)
    elif holiday_filter == "normal":
        q = q.filter(TrackScore.holiday_tag.is_(None))

    from sqlalchemy import text as satext

    def _order_expr(raw_sql):
        return satext(f"{raw_sql} DESC" if order == "desc" else f"{raw_sql} ASC")

    if sort_by == "skip_count":
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
            "final_score":        "CAST(final_score AS REAL)",
            "play_count":         "play_count",
            "last_played":        "last_played",
            "artist_name":        "artist_name",
            "track_name":         "track_name",
            "skip_penalty":       "CAST(skip_penalty AS REAL)",
            "artist_affinity":    "CAST(artist_affinity AS REAL)",
            "skip_streak":        "skip_streak",          # v2
            "replay_boost":       "replay_boost",          # v2
            "global_popularity":  "global_popularity",     # v2
        }
        raw = sql_sort_map.get(sort_by, "CAST(final_score AS REAL)")
        q = q.order_by(_order_expr(raw))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

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
                "jellyfin_item_id":  r.jellyfin_item_id,
                "track_name":        r.track_name,
                "artist_name":       r.artist_name,
                "album_name":        r.album_name,
                "genre":             r.genre,
                "play_count":        r.play_count,
                "last_played":       r.last_played,
                "is_played":         r.is_played,
                "is_favorite":       r.is_favorite,
                "final_score":       float(r.final_score),
                "play_score":        float(r.play_score),
                "recency_score":     float(r.recency_score),
                "artist_affinity":   float(r.artist_affinity),
                "genre_affinity":    float(r.genre_affinity),
                "skip_penalty":      float(r.skip_penalty),
                "novelty_bonus":     float(r.novelty_bonus),
                "skip_count": skip_map[r.jellyfin_item_id].skip_count
                    if r.jellyfin_item_id in skip_map else 0,
                "total_events": skip_map[r.jellyfin_item_id].total_events
                    if r.jellyfin_item_id in skip_map else 0,
                "skip_rate": float(skip_map[r.jellyfin_item_id].skip_rate)
                    if r.jellyfin_item_id in skip_map else 0.0,
                # v2 fields
                "skip_streak":       r.skip_streak or 0,
                "replay_boost":      round(r.replay_boost or 0.0, 2),
                "global_popularity": r.global_popularity,
                "cooldown_until":    r.cooldown_until.isoformat() if r.cooldown_until else None,
                "on_cooldown":       bool(r.cooldown_until and r.cooldown_until > now),
                "holiday_tag":       getattr(r, "holiday_tag", None),
                "holiday_exclude":   bool(getattr(r, "holiday_exclude", False)),
            }
            for r in rows
        ],
    }


@router.get("/artists")
def get_artists(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    sort_by: str = Query("affinity_score"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    uid = _resolve_user(user_id, username, db)

    q = db.query(ArtistProfile).filter_by(user_id=uid)

    from sqlalchemy import cast, Float as SAFloat, func as sqlfunc, text as satext

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

    # Load enrichment data
    try:
        from models import ArtistEnrichment
        import json as _json
        enc_map = {
            row.artist_name_lower: row
            for row in db.query(ArtistEnrichment).all()
        }
    except Exception:
        enc_map = {}

    def _aord(raw_sql):
        return satext(f"{raw_sql} DESC" if order == "desc" else f"{raw_sql} ASC")

    if sort_by == "skip_rate":
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
            "replay_boost":   "replay_boost",   # v2
        }
        raw = sql_map.get(sort_by, "CAST(affinity_score AS REAL)")
        q = q.order_by(_aord(raw))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

    import json as _json
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "artists": [
            {
                "artist_name":       r.artist_name,
                "affinity_score":    float(r.affinity_score),
                "total_plays":       r.total_plays,
                "total_tracks_played": r.total_tracks_played,
                "total_skips":       live_skip_map.get(r.artist_name, {}).get("skip_count", 0),
                "total_events":      live_skip_map.get(r.artist_name, {}).get("total_events", 0),
                "skip_rate":         live_skip_map.get(r.artist_name, {}).get("skip_rate", 0.0),
                "has_favorite":      r.has_favorite,
                "primary_genre":     r.primary_genre,
                # v2
                "replay_boost":      round(r.replay_boost or 0.0, 2),
                "related_artists":   _json.loads(r.related_artists)
                    if r.related_artists else [],
                "tags":              _json.loads(r.tags) if r.tags else [],
                "popularity_score":  enc_map.get(r.artist_name.lower(), None) and
                    enc_map[r.artist_name.lower()].popularity_score,
                "trend_direction":   enc_map.get(r.artist_name.lower(), None) and
                    enc_map[r.artist_name.lower()].trend_direction,
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
    uid = _resolve_user(user_id, username, db)
    rows = db.query(GenreProfile).filter_by(user_id=uid)\
             .order_by(desc(GenreProfile.affinity_score)).all()
    return [
        {
            "genre":         r.genre,
            "affinity_score": float(r.affinity_score),
            "total_plays":   r.total_plays,
            "total_skips":   r.total_skips,
            "skip_rate":     float(r.skip_rate),
            "has_favorite":  r.has_favorite,
        }
        for r in rows
    ]


@router.get("/cooldowns")
def get_cooldowns(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    status: str = Query("all", description="all|active|expired|permanent"),
    db: Session = Depends(get_db),
):
    """Browse all cooldowns for a user — active, historical, and permanent dislikes."""
    from models import TrackCooldown

    uid = _resolve_user(user_id, username, db)
    now = datetime.utcnow()

    q = db.query(TrackCooldown).filter_by(user_id=uid)
    if status != "all":
        q = q.filter(TrackCooldown.status == status)

    rows = q.order_by(TrackCooldown.cooldown_started_at.desc()).limit(100).all()

    return {
        "cooldowns": [
            {
                "track_name":         r.track_name,
                "artist_name":        r.artist_name,
                "jellyfin_item_id":   r.jellyfin_item_id,
                "status":             r.status,
                "cooldown_count":     r.cooldown_count,
                "skip_streak":        r.skip_streak_at_trigger,
                "cooldown_until":     r.cooldown_until.isoformat() if r.cooldown_until else None,
                "days_remaining":     max(0, round((r.cooldown_until - now).total_seconds() / 86400, 1))
                    if r.cooldown_until and r.status == "active" else 0,
                "cooldown_started_at": r.cooldown_started_at.isoformat()
                    if r.cooldown_started_at else None,
                "expired_at":         r.expired_at.isoformat() if r.expired_at else None,
            }
            for r in rows
        ],
        "summary": {
            "active":    db.query(TrackCooldown).filter_by(user_id=uid, status="active").count(),
            "expired":   db.query(TrackCooldown).filter_by(user_id=uid, status="expired").count(),
            "permanent": db.query(TrackCooldown).filter_by(user_id=uid, status="permanent").count(),
        }
    }


@router.get("/replay-signals")
def get_replay_signals(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    limit: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """Show recent voluntary replay signals — the high-value preference indicators."""
    from models import UserReplaySignal

    uid = _resolve_user(user_id, username, db)
    rows = (
        db.query(UserReplaySignal)
        .filter_by(user_id=uid)
        .order_by(UserReplaySignal.replay_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "jellyfin_item_id": r.jellyfin_item_id,
            "artist_name":      r.artist_name,
            "signal_type":      r.signal_type,
            "days_between":     r.days_between,
            "seed_was_playlist": r.seed_was_playlist,
            "boost_applied":    r.boost_applied,
            "replay_at":        r.replay_at.isoformat() if r.replay_at else None,
        }
        for r in rows
    ]


@router.get("/enrichment/status")
def get_enrichment_status(db: Session = Depends(get_db)):
    """Library-wide enrichment progress — useful for checking how much has been indexed."""
    from models import LibraryTrack, TrackEnrichment, ArtistEnrichment, ArtistRelation
    from datetime import datetime

    total_tracks = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).count()
    enriched_tracks = db.query(TrackEnrichment).count()
    enriched_artists = db.query(ArtistEnrichment).count()
    total_relations = db.query(ArtistRelation).count()

    # Most recently enriched
    latest_track = (
        db.query(TrackEnrichment)
        .order_by(TrackEnrichment.enriched_at.desc())
        .first()
    )
    latest_artist = (
        db.query(ArtistEnrichment)
        .order_by(ArtistEnrichment.enriched_at.desc())
        .first()
    )

    # Next enrichment time from AutomationSettings
    from models import AutomationSettings
    settings = db.query(AutomationSettings).first()
    next_run = None
    if settings and settings.last_enrichment and settings.enrichment_interval_hours:
        from datetime import timedelta
        next_run = (
            settings.last_enrichment +
            timedelta(hours=settings.enrichment_interval_hours)
        ).isoformat()

    return {
        "tracks": {
            "total_in_library": total_tracks,
            "enriched": enriched_tracks,
            "pending": max(0, total_tracks - enriched_tracks),
            "pct_complete": round(enriched_tracks / total_tracks * 100, 1) if total_tracks else 0,
            "last_enriched_track": latest_track.track_name if latest_track else None,
            "last_enriched_at": latest_track.enriched_at.isoformat()
                if latest_track and latest_track.enriched_at else None,
        },
        "artists": {
            "enriched": enriched_artists,
            "total_relations": total_relations,
            "last_enriched_artist": latest_artist.artist_name if latest_artist else None,
            "last_enriched_at": latest_artist.enriched_at.isoformat()
                if latest_artist and latest_artist.enriched_at else None,
        },
        "schedule": {
            "enabled": settings.enrichment_enabled if settings else False,
            "interval_hours": settings.enrichment_interval_hours if settings else 48,
            "last_run": settings.last_enrichment.isoformat()
                if settings and settings.last_enrichment else None,
            "next_run": next_run,
        },
    }


@router.get("/summary")
def get_summary(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    uid = _resolve_user(user_id, username, db)

    total_tracks = db.query(TrackScore).filter_by(user_id=uid).count()
    played_tracks = db.query(TrackScore).filter_by(user_id=uid, is_played=True).count()
    total_artists = db.query(ArtistProfile).filter_by(user_id=uid).count()

    top_artists = db.query(ArtistProfile).filter_by(user_id=uid)\
                    .order_by(desc(ArtistProfile.affinity_score)).limit(5).all()

    top_genres = db.query(GenreProfile).filter_by(user_id=uid)\
                   .order_by(desc(GenreProfile.affinity_score)).limit(5).all()

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
            most_skipped = {
                "artist_name": best.artist_name,
                "skip_rate":   round(best.total_skips / best.total_events, 3),
                "total_skips": best.total_skips,
            }

    top_track = db.query(TrackScore).filter_by(user_id=uid)\
                  .order_by(desc(TrackScore.final_score)).first()

    most_played = db.query(TrackScore).filter_by(user_id=uid, is_played=True)\
                    .order_by(desc(TrackScore.play_count)).first()

    total_skip_events = db.query(SkipPenalty).filter_by(user_id=uid).count()
    total_skips_recorded = db.query(
        func.sum(SkipPenalty.skip_count)
    ).filter_by(user_id=uid).scalar() or 0

    # v2: cooldown counts
    now = datetime.utcnow()
    try:
        from models import TrackCooldown
        active_cooldowns = db.query(TrackCooldown).filter_by(
            user_id=uid, status="active"
        ).count()
        permanent_dislikes = db.query(TrackCooldown).filter_by(
            user_id=uid, status="permanent"
        ).count()
    except Exception:
        active_cooldowns = 0
        permanent_dislikes = 0

    # v2: replay signal count
    try:
        from models import UserReplaySignal
        from datetime import timedelta
        recent_replays = db.query(UserReplaySignal).filter_by(user_id=uid)\
                           .filter(UserReplaySignal.replay_at >= now - timedelta(days=7))\
                           .count()
    except Exception:
        recent_replays = 0

    return {
        "total_tracks_in_library": total_tracks,
        "played_tracks": played_tracks,
        "unplayed_tracks": total_tracks - played_tracks,
        "total_artists": total_artists,
        "top_artists": [
            {"artist_name": r.artist_name, "affinity_score": float(r.affinity_score),
             "total_plays": r.total_plays, "replay_boost": round(r.replay_boost or 0.0, 1)}
            for r in top_artists
        ],
        "top_genres": [
            {"genre": r.genre, "affinity_score": float(r.affinity_score),
             "total_plays": r.total_plays}
            for r in top_genres
        ],
        "most_skipped_artist": most_skipped,
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
        "skip_tracking": {
            "tracks_with_events": total_skip_events,
            "total_skips_recorded": int(total_skips_recorded),
        },
        # v2
        "cooldowns": {
            "active": active_cooldowns,
            "permanent_dislikes": permanent_dislikes,
        },
        "replay_signals": {
            "last_7_days": recent_replays,
        },
    }


@router.get("/holiday")
def get_holiday_summary(db: Session = Depends(get_db)):
    """
    v4: Holiday detection summary.
    Returns per-holiday track counts, season windows, and the full tagged
    track list so the UI can show exactly which songs were identified.
    """
    from models import LibraryTrack
    from services.holiday import is_in_season, HOLIDAY_RULES
    from datetime import date

    today = date.today()

    tagged = db.query(LibraryTrack).filter(
        LibraryTrack.holiday_tag.isnot(None),
        LibraryTrack.missing_since.is_(None),
    ).order_by(LibraryTrack.holiday_tag, LibraryTrack.artist_name, LibraryTrack.track_name).all()

    total_tagged       = len(tagged)
    currently_excluded = sum(1 for t in tagged if t.holiday_exclude)
    currently_included = total_tagged - currently_excluded

    by_holiday: dict[str, int] = {}
    for t in tagged:
        by_holiday[t.holiday_tag] = by_holiday.get(t.holiday_tag, 0) + 1

    season_status = {
        slug: is_in_season(slug, today)
        for slug, _kw, _s, _e in HOLIDAY_RULES
    }

    return {
        "summary": {
            "total_tagged":       total_tagged,
            "currently_excluded": currently_excluded,
            "currently_included": currently_included,
            "by_holiday":         by_holiday,
        },
        "season_status": season_status,
        "tracks": [
            {
                "jellyfin_item_id": t.jellyfin_item_id,
                "track_name":       t.track_name,
                "artist_name":      t.artist_name,
                "album_name":       t.album_name,
                "holiday_tag":      t.holiday_tag,
                "holiday_exclude":  bool(t.holiday_exclude),
            }
            for t in tagged
        ],
    }

