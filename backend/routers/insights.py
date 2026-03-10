"""
JellyDJ Insights router — v3

New in v3 (UI column expansion):
  - /tracks: exposes all score components as sortable fields:
      play_score, recency_score, genre_affinity, novelty_bonus,
      last_played, global_popularity, replay_boost, skip_streak,
      cooldown_until (on_cooldown), holiday_tag
  - /artists: adds popularity_score sort (from ArtistEnrichment),
      trend_direction now always returned, related_artists parsed
      with match scores
  - Both endpoints: cooldown_filter (all|active|clear) already wired
    in /tracks
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func
from typing import Optional
from datetime import datetime

from auth import UserContext, get_current_user
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


def _assert_can_view_user(requested_user_id: str, current_user: UserContext) -> None:
    """Raise 403 if a non-admin tries to view another user's data."""
    if not current_user.is_admin and requested_user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own insights.",
        )


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    """Return enabled users. Non-admins only see themselves."""
    if not current_user.is_admin:
        # Return just the current user — no cross-user visibility
        u = db.query(ManagedUser).filter_by(jellyfin_user_id=current_user.user_id, has_activated=True).first()
        if not u:
            return []
        return [{"jellyfin_user_id": u.jellyfin_user_id, "username": u.username}]

    users = db.query(ManagedUser).filter_by(has_activated=True).all()
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
    artist_filter: Optional[str] = Query(None),   # kept for back-compat
    search_filter: Optional[str] = Query(None),    # artist OR track OR album
    holiday_filter: str = Query("all", description="all|holiday|excluded|normal"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
):
    uid = _resolve_user(user_id, username, db)
    _assert_can_view_user(uid, current_user)

    q = db.query(TrackScore).filter_by(user_id=uid)

    if played_filter == "played":
        q = q.filter(TrackScore.is_played == True)
    elif played_filter == "unplayed":
        q = q.filter(TrackScore.is_played == False)

    now = datetime.utcnow()
    if cooldown_filter == "active":
        q = q.filter(TrackScore.cooldown_until > now)
    elif cooldown_filter == "clear":
        q = q.filter(
            (TrackScore.cooldown_until == None) |
            (TrackScore.cooldown_until <= now)
        )

    _search = search_filter or artist_filter
    if _search:
        from sqlalchemy import or_
        q = q.filter(or_(
            TrackScore.artist_name.ilike(f"%{_search}%"),
            TrackScore.track_name.ilike(f"%{_search}%"),
            TrackScore.album_name.ilike(f"%{_search}%"),
        ))

    if holiday_filter == "holiday":
        q = q.filter(TrackScore.holiday_tag.isnot(None))
    elif holiday_filter == "excluded":
        q = q.filter(TrackScore.holiday_tag.isnot(None), TrackScore.holiday_exclude == True)
    elif holiday_filter == "normal":
        q = q.filter(TrackScore.holiday_tag.is_(None))

    from sqlalchemy import text as satext

    def _order_expr(raw_sql):
        return satext(f"{raw_sql} DESC" if order == "desc" else f"{raw_sql} ASC")

    # All sortable fields — covers every column exposed in the UI
    sql_sort_map = {
        "final_score":        "CAST(final_score AS REAL)",
        "play_score":         "CAST(play_score AS REAL)",
        "recency_score":      "CAST(recency_score AS REAL)",
        "artist_affinity":    "CAST(artist_affinity AS REAL)",
        "genre_affinity":     "CAST(genre_affinity AS REAL)",
        "skip_penalty":       "CAST(skip_penalty AS REAL)",
        "novelty_bonus":      "CAST(novelty_bonus AS REAL)",
        "play_count":         "play_count",
        "last_played":        "last_played",
        "artist_name":        "artist_name",
        "track_name":         "track_name",
        "skip_streak":        "skip_streak",
        "replay_boost":       "replay_boost",
        "global_popularity":  "global_popularity",
        "cooldown_until":     "cooldown_until",
        "holiday_tag":        "holiday_tag",
    }

    if sort_by == "skip_count":
        # skip_count lives in SkipPenalty — sort in Python
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

    # ── Popularity resolution (three-tier fallback) ───────────────────────────
    # Tier 1: TrackScore.global_popularity  — written by scoring_engine after enrichment
    # Tier 2: TrackEnrichment.popularity_score — written by enrich_tracks()
    # Tier 3: PopularityCache artist:{name}  — written by popularity cache refresh
    #
    # Tiers 2 and 3 handle the common case where the user has run a popularity
    # cache refresh but not yet a full enrichment + index cycle, so TrackScore
    # rows still have global_popularity=NULL.

    # Build tier-2 map from TrackEnrichment for just this page's item IDs
    te_pop_map: dict[str, float] = {}
    te_listeners_map: dict[str, int] = {}
    te_playcount_map: dict[str, int] = {}
    if item_ids:
        try:
            from models import TrackEnrichment
            te_rows = (
                db.query(
                    TrackEnrichment.jellyfin_item_id,
                    TrackEnrichment.popularity_score,
                    TrackEnrichment.global_listeners,
                    TrackEnrichment.global_playcount,
                )
                .filter(TrackEnrichment.jellyfin_item_id.in_(item_ids))
                .all()
            )
            for row in te_rows:
                if row.popularity_score is not None:
                    te_pop_map[row.jellyfin_item_id] = row.popularity_score
                if row.global_listeners is not None:
                    te_listeners_map[row.jellyfin_item_id] = row.global_listeners
                if row.global_playcount is not None:
                    te_playcount_map[row.jellyfin_item_id] = row.global_playcount
        except Exception:
            pass

    # Build tier-3 map from PopularityCache for distinct artist names on this page
    artist_pop_map: dict[str, float] = {}
    try:
        import json as _json
        from models import PopularityCache
        artist_names = list({r.artist_name.lower() for r in rows if r.artist_name})
        cache_keys = [f"artist:{a}" for a in artist_names]
        if cache_keys:
            cache_rows = (
                db.query(PopularityCache)
                .filter(PopularityCache.cache_key.in_(cache_keys))
                .all()
            )
            for cr in cache_rows:
                try:
                    payload = _json.loads(cr.payload)
                    score = payload.get("popularity_score")
                    if score is not None:
                        # strip "artist:" prefix to get bare lowercase name
                        artist_pop_map[cr.cache_key[7:]] = float(score)
                except Exception:
                    pass
    except Exception:
        pass

    def _resolve_track_popularity(r) -> float | None:
        """Best available per-song popularity: TrackScore → TrackEnrichment → None."""
        if r.global_popularity is not None:
            return r.global_popularity
        if r.jellyfin_item_id in te_pop_map:
            return te_pop_map[r.jellyfin_item_id]
        return None  # no track-level data yet

    def _resolve_artist_popularity(r) -> float | None:
        """Artist-level popularity from PopularityCache."""
        if r.artist_name:
            return artist_pop_map.get(r.artist_name.lower())
        return None

    def _resolve_popularity(r) -> float | None:
        """Track-level popularity only — no artist fallback.
        Returns None if enrichment hasn't run yet for this track.
        Callers can check artist_popularity separately for context.
        """
        return _resolve_track_popularity(r)

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
                "last_played":       r.last_played.isoformat() if r.last_played else None,
                "is_played":         r.is_played,
                "is_favorite":       r.is_favorite,
                # Score components
                "final_score":       float(r.final_score),
                "play_score":        float(r.play_score),
                "recency_score":     float(r.recency_score),
                "artist_affinity":   float(r.artist_affinity),
                "genre_affinity":    float(r.genre_affinity),
                "skip_penalty":      float(r.skip_penalty),
                "novelty_bonus":     float(r.novelty_bonus),
                # Popularity — track-level (song-specific Last.fm listeners)
                # and artist-level (from popularity cache) kept separate so the
                # UI can show which source was used and avoid confusing the two.
                "track_popularity":       _resolve_track_popularity(r),
                "artist_popularity":      _resolve_artist_popularity(r),
                "global_popularity":      _resolve_popularity(r),   # best available (back-compat)
                "track_listeners":        te_listeners_map.get(r.jellyfin_item_id),
                "track_playcount":        te_playcount_map.get(r.jellyfin_item_id),
                # Replay signal
                "replay_boost":      round(r.replay_boost or 0.0, 2),
                # Skip / cooldown signals
                "skip_streak":       r.skip_streak or 0,
                "cooldown_until":    r.cooldown_until.isoformat() if r.cooldown_until else None,
                "on_cooldown":       bool(r.cooldown_until and r.cooldown_until > now),
                # Live skip events (from SkipPenalty)
                "skip_count": skip_map[r.jellyfin_item_id].skip_count
                    if r.jellyfin_item_id in skip_map else 0,
                "total_events": skip_map[r.jellyfin_item_id].total_events
                    if r.jellyfin_item_id in skip_map else 0,
                "skip_rate": float(skip_map[r.jellyfin_item_id].skip_rate)
                    if r.jellyfin_item_id in skip_map else 0.0,
                # Holiday
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
    current_user: UserContext = Depends(get_current_user),
):
    uid = _resolve_user(user_id, username, db)
    _assert_can_view_user(uid, current_user)

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

    # Load enrichment data (ArtistEnrichment)
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
    elif sort_by == "popularity_score":
        # popularity lives in ArtistEnrichment — sort in Python
        total = q.count()
        all_rows = q.order_by(satext("CAST(affinity_score AS REAL) DESC")).all()
        all_rows.sort(
            key=lambda r: (enc_map.get(r.artist_name.lower()) and enc_map[r.artist_name.lower()].popularity_score) or 0.0,
            reverse=(order == "desc")
        )
        rows = all_rows[(page - 1) * page_size: page * page_size]
    else:
        sql_map = {
            "affinity_score": "CAST(affinity_score AS REAL)",
            "total_plays":    "total_plays",
            "artist_name":    "artist_name",
            "replay_boost":   "replay_boost",
        }
        raw = sql_map.get(sort_by, "CAST(affinity_score AS REAL)")
        q = q.order_by(_aord(raw))
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

    import json as _json

    def _parse_related(raw):
        """Return list of {name, match} dicts regardless of stored format."""
        if not raw:
            return []
        try:
            parsed = _json.loads(raw)
            if not parsed:
                return []
            # Already list of dicts
            if isinstance(parsed[0], dict):
                return [{"name": item.get("name", ""), "match": item.get("match")} for item in parsed[:15]]
            # List of strings
            return [{"name": str(item), "match": None} for item in parsed[:15]]
        except Exception:
            return []

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "artists": [
            {
                "artist_name":          r.artist_name,
                "affinity_score":       float(r.affinity_score),
                "total_plays":          r.total_plays,
                "total_tracks_played":  r.total_tracks_played,
                "total_skips":          live_skip_map.get(r.artist_name, {}).get("skip_count", 0),
                "total_events":         live_skip_map.get(r.artist_name, {}).get("total_events", 0),
                "skip_rate":            live_skip_map.get(r.artist_name, {}).get("skip_rate", 0.0),
                "has_favorite":         r.has_favorite,
                "primary_genre":        r.primary_genre,
                # Replay signal
                "replay_boost":         round(r.replay_boost or 0.0, 2),
                # Enrichment
                "related_artists":      _parse_related(r.related_artists),
                "tags":                 _json.loads(r.tags) if r.tags else [],
                "popularity_score":     (enc_map.get(r.artist_name.lower()) and
                                         enc_map[r.artist_name.lower()].popularity_score),
                "trend_direction":      (enc_map.get(r.artist_name.lower()) and
                                         enc_map[r.artist_name.lower()].trend_direction),
                "global_listeners":     (enc_map.get(r.artist_name.lower()) and
                                         enc_map[r.artist_name.lower()].global_listeners),
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
    """Library-wide enrichment progress."""
    from models import LibraryTrack, TrackEnrichment, ArtistEnrichment, ArtistRelation
    from datetime import datetime

    total_tracks = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).count()
    enriched_tracks = db.query(TrackEnrichment).count()
    enriched_artists = db.query(ArtistEnrichment).count()
    total_relations = db.query(ArtistRelation).count()

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
    current_user: UserContext = Depends(get_current_user),
):
    uid = _resolve_user(user_id, username, db)
    _assert_can_view_user(uid, current_user)

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


@router.get("/holiday-debug")
def holiday_debug(
    sample_artist: Optional[str] = Query(None, description="Artist name to check, e.g. 'Mariah Carey'"),
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    from models import LibraryTrack
    from sqlalchemy import text, and_, func

    lt_total   = db.query(LibraryTrack).filter(LibraryTrack.missing_since.is_(None)).count()
    lt_tagged  = db.query(LibraryTrack).filter(
        LibraryTrack.holiday_tag.isnot(None),
        LibraryTrack.missing_since.is_(None),
    ).count()
    lt_excluded = db.query(LibraryTrack).filter(
        LibraryTrack.holiday_tag.isnot(None),
        LibraryTrack.holiday_exclude == True,
        LibraryTrack.missing_since.is_(None),
    ).count()
    lt_null_exclude = db.query(LibraryTrack).filter(
        LibraryTrack.holiday_tag.isnot(None),
        LibraryTrack.holiday_exclude.is_(None),
        LibraryTrack.missing_since.is_(None),
    ).count()

    ts_total    = db.query(TrackScore).count()
    ts_tagged   = db.query(TrackScore).filter(TrackScore.holiday_tag.isnot(None)).count()
    ts_excluded = db.query(TrackScore).filter(
        TrackScore.holiday_tag.isnot(None),
        TrackScore.holiday_exclude == True,
    ).count()
    ts_null_tag = db.query(TrackScore).filter(TrackScore.holiday_tag.is_(None)).count()
    ts_null_exclude = db.query(TrackScore).filter(
        TrackScore.holiday_tag.isnot(None),
        TrackScore.holiday_exclude.is_(None),
    ).count()

    col_check = {}
    for table in ["library_tracks", "track_scores"]:
        try:
            db.execute(text(f"SELECT holiday_tag, holiday_exclude FROM {table} LIMIT 1")).fetchall()
            col_check[table] = "columns exist"
        except Exception as e:
            col_check[table] = f"ERROR: {e}"

    lt_christmas = db.query(LibraryTrack.jellyfin_item_id).filter(
        LibraryTrack.holiday_tag == "christmas",
        LibraryTrack.holiday_exclude == True,
        LibraryTrack.missing_since.is_(None),
    ).limit(5).all()
    lt_ids = [r.jellyfin_item_id for r in lt_christmas]

    ts_mismatch = []
    for jid in lt_ids:
        ts_row = db.query(TrackScore).filter_by(jellyfin_item_id=jid).first()
        ts_mismatch.append({
            "jellyfin_item_id": jid,
            "ts_found": ts_row is not None,
            "ts_holiday_tag": ts_row.holiday_tag if ts_row else "NO ROW",
            "ts_holiday_exclude": ts_row.holiday_exclude if ts_row else "NO ROW",
        })

    artist_sample = []
    if sample_artist:
        lt_rows = db.query(LibraryTrack).filter(
            LibraryTrack.artist_name.ilike(f"%{sample_artist}%"),
            LibraryTrack.missing_since.is_(None),
        ).limit(10).all()
        for lt in lt_rows:
            ts = db.query(TrackScore).filter_by(jellyfin_item_id=lt.jellyfin_item_id).first()
            artist_sample.append({
                "track_name":        lt.track_name,
                "album_name":        lt.album_name,
                "lt_holiday_tag":    lt.holiday_tag,
                "lt_holiday_exclude": lt.holiday_exclude,
                "ts_holiday_tag":    ts.holiday_tag if ts else "NO SCORE ROW",
                "ts_holiday_exclude": ts.holiday_exclude if ts else "NO SCORE ROW",
                "would_be_excluded": (
                    ts is not None and
                    ts.holiday_tag is not None and
                    ts.holiday_exclude == True
                ),
            })

    user_check = None
    if user_id or username:
        try:
            uid = _resolve_user(user_id, username, db)
            total_for_user = db.query(TrackScore).filter_by(user_id=uid).count()
            holiday_for_user = db.query(TrackScore).filter_by(user_id=uid).filter(
                TrackScore.holiday_tag.isnot(None)
            ).count()
            excluded_for_user = db.query(TrackScore).filter_by(user_id=uid).filter(
                TrackScore.holiday_tag.isnot(None),
                TrackScore.holiday_exclude == True,
            ).count()
            leaking = db.query(TrackScore).filter_by(user_id=uid).filter(
                TrackScore.holiday_tag.isnot(None),
                TrackScore.holiday_exclude == True,
            ).limit(5).all()

            user_check = {
                "user_id": uid,
                "total_track_scores": total_for_user,
                "holiday_tagged_scores": holiday_for_user,
                "excluded_scores": excluded_for_user,
                "should_be_blocked_count": excluded_for_user,
                "sample_should_be_blocked": [
                    {
                        "track_name": r.track_name,
                        "artist_name": r.artist_name,
                        "holiday_tag": r.holiday_tag,
                        "holiday_exclude": r.holiday_exclude,
                    }
                    for r in leaking
                ],
            }
        except Exception as e:
            user_check = {"error": str(e)}

    return {
        "column_check": col_check,
        "library_tracks": {
            "total": lt_total,
            "holiday_tagged": lt_tagged,
            "holiday_excluded": lt_excluded,
            "tagged_but_null_exclude": lt_null_exclude,
            "diagnosis": (
                "OK" if lt_null_exclude == 0
                else f"PROBLEM: {lt_null_exclude} tracks have a holiday_tag but holiday_exclude=NULL"
            ),
        },
        "track_scores": {
            "total": ts_total,
            "holiday_tagged": ts_tagged,
            "holiday_excluded": ts_excluded,
            "tagged_but_null_exclude": ts_null_exclude,
            "diagnosis": (
                "OK" if ts_null_exclude == 0 and (ts_tagged >= lt_tagged or lt_tagged == 0)
                else f"PROBLEM: ts_tagged={ts_tagged} vs lt_tagged={lt_tagged}, null_exclude={ts_null_exclude}"
            ),
        },
        "christmas_track_sample_in_track_scores": ts_mismatch,
        "artist_sample": artist_sample,
        "user_check": user_check,
        "instructions": {
            "step1": "Check column_check — both tables must say 'columns exist'",
            "step2": "Check library_tracks.tagged_but_null_exclude — must be 0",
            "step3": "Check track_scores.holiday_tagged — must equal library_tracks.holiday_tagged",
            "step4": "Check christmas_track_sample — ts_holiday_exclude must be true for all",
            "step5": "Pass ?user_id=XXX to check per-user scores",
            "step6": "Pass ?sample_artist=Mariah+Carey to check a specific artist",
        },
    }


@router.get("/debug/jellyfin-track")
async def debug_jellyfin_track(
    track_name: str,
    db: Session = Depends(get_db),
):
    """
    Fetch raw Jellyfin metadata for a track by name so you can see exactly
    what fields Jellyfin is returning (AlbumArtist, Artists, etc.).
    Usage: /api/debug/jellyfin-track?track_name=Castle+on+the+Hill
    """
    import httpx
    from models import ConnectionSettings, ManagedUser, LibraryTrack
    from crypto import decrypt

    conn = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not conn or not conn.base_url:
        return {"error": "Jellyfin not configured"}

    base_url = conn.base_url.rstrip("/")
    api_key  = decrypt(conn.api_key_encrypted)
    headers  = {"X-Emby-Token": api_key}

    # Also show what's in the local DB for this track
    db_rows = (
        db.query(LibraryTrack)
        .filter(LibraryTrack.track_name.ilike(f"%{track_name}%"))
        .limit(5)
        .all()
    )
    db_info = [
        {
            "track_name":    r.track_name,
            "artist_name":   r.artist_name,
            "album_name":    r.album_name,
            "jellyfin_item_id": r.jellyfin_item_id,
        }
        for r in db_rows
    ]

    # Fetch live from Jellyfin with ALL fields including Artists
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/Items",
                headers=headers,
                params={
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "SearchTerm": track_name,
                    "Fields": "DateCreated,Genres,UserData,AlbumArtist,Artists,Album,ParentId,MediaSources",
                    "Limit": 5,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return {"error": str(e), "db_rows": db_info}

    items = data.get("Items", [])
    simplified = [
        {
            "Name":        item.get("Name"),
            "Album":       item.get("Album"),
            "AlbumArtist": item.get("AlbumArtist"),
            "Artists":     item.get("Artists"),
            "AlbumArtists": item.get("AlbumArtists"),
            "Id":          item.get("Id"),
            # Raw keys present — helps spot unexpected field names
            "all_keys":    sorted(item.keys()),
        }
        for item in items
    ]

    return {
        "db_rows":       db_info,
        "jellyfin_live": simplified,
    }