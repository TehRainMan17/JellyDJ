"""
JellyDJ Network Graph API — v2

Provides data for the interactive music taste network diagram on the Insights page.

Nodes:
  - Artists  (with user affinity, play count, genre, popularity)
  - Genres   (aggregated from artist tags)

Edges:
  - Artist → Artist  (from ArtistRelation, weighted by Last.fm similarity × user affinity)
  - Artist → Genre   (from ArtistEnrichment.tags)

The graph is user-specific: node sizes and colours reflect the user's personal
affinity, not global popularity. An artist the user loves deeply will have a
large, brightly-coloured node even if they're obscure globally.

Edge weights factor in BOTH Last.fm similarity (objective musical closeness)
AND the user's affinity for the source artist (subjective importance to them).
This means "Radiohead → Portishead" is a heavy edge for a user who plays a lot
of Radiohead, but a light edge for someone who played Radiohead once.

Endpoints:
  GET /api/graph/network?user_id=&username=&limit=50&min_affinity=0
    Returns nodes + edges for the force-directed graph.

  GET /api/graph/artist/{artist_name}
    Returns a single artist's full enrichment detail (for click-to-expand).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user, UserContext
from database import get_db
from models import ManagedUser, ArtistProfile, ArtistEnrichment, ArtistRelation, GenreProfile, TrackScore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])


def _resolve_user(user_id: Optional[str], username: Optional[str], db: Session) -> str:
    if user_id:
        return user_id
    if username:
        u = db.query(ManagedUser).filter(ManagedUser.username.ilike(username)).first()
        if not u:
            raise HTTPException(404, f"User '{username}' not found")
        return u.jellyfin_user_id
    raise HTTPException(400, "Provide user_id or username")


def _affinity_to_color(affinity: float) -> str:
    """
    Map 0-100 affinity to a hex color from cool grey (low) through
    teal (medium) to bright accent (high). Gives the graph an intuitive
    heat-map feel without requiring explicit color config.
    """
    a = max(0.0, min(100.0, affinity)) / 100.0
    if a < 0.33:
        # Grey → teal
        r = int(100 + (0 - 100) * (a / 0.33))
        g = int(100 + (180 - 100) * (a / 0.33))
        b = int(110 + (170 - 110) * (a / 0.33))
    elif a < 0.66:
        # Teal → blue-purple
        t = (a - 0.33) / 0.33
        r = int(0 + (80 - 0) * t)
        g = int(180 + (100 - 180) * t)
        b = int(170 + (220 - 170) * t)
    else:
        # Blue-purple → bright accent (gold/amber)
        t = (a - 0.66) / 0.34
        r = int(80 + (255 - 80) * t)
        g = int(100 + (200 - 100) * t)
        b = int(220 + (50 - 220) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _popularity_to_size(affinity: float, plays: int) -> float:
    """
    Node size: blends user affinity (primary) with raw play count (secondary).
    Range: 8 (minimum visible) to 40 (maximum, for your absolute top artists).
    """
    # Log-scale plays: 1 play = 0, 100 plays ≈ 50, 1000 plays ≈ 75
    import math
    play_score = min(100.0, (math.log1p(plays) / math.log1p(1000)) * 100)
    combined = affinity * 0.7 + play_score * 0.3
    return round(8 + (combined / 100) * 32, 1)


@router.get("/network")
def get_network_graph(
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    limit: int = Query(80, ge=10, le=200, description="Max artist nodes to include"),
    min_affinity: float = Query(0.0, ge=0.0, le=100.0, description="Minimum affinity to include an artist"),
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return a network graph of artists and genres for the given user.

    Response shape:
    {
      "nodes": [
        {
          "id": "artist:Radiohead",
          "label": "Radiohead",
          "type": "artist",          // "artist" | "genre"
          "affinity": 92.5,
          "plays": 340,
          "genre": "Alternative Rock",
          "popularity": 88.0,        // Last.fm global popularity 0-100
          "tags": ["alternative rock", "indie"],
          "trend": "stable",         // "rising"|"falling"|"stable"
          "color": "#ffcc33",
          "size": 36.5,
          "has_favorite": true,
        },
        ...
      ],
      "edges": [
        {
          "source": "artist:Radiohead",
          "target": "artist:Portishead",
          "weight": 0.72,           // match_score × source_affinity_normalized
          "match_score": 0.85,      // raw Last.fm similarity
          "type": "similar",        // "similar" | "genre"
        },
        ...
      ],
      "meta": {
        "total_artists": 120,
        "shown_artists": 80,
        "total_edges": 215,
      }
    }
    """
    uid = _resolve_user(user_id, username, db)

    # Load user's artist profiles ordered by affinity
    artist_profiles = (
        db.query(ArtistProfile)
        .filter_by(user_id=uid)
        .filter(ArtistProfile.affinity_score > str(min_affinity))
        .order_by(ArtistProfile.affinity_score.desc())
        .limit(limit)
        .all()
    )

    if not artist_profiles:
        return {"nodes": [], "edges": [], "meta": {"total_artists": 0}}

    total_artists = db.query(ArtistProfile).filter_by(user_id=uid).count()
    artist_names_in_graph = {ap.artist_name for ap in artist_profiles}
    artist_names_lower = {name.lower() for name in artist_names_in_graph}

    max_affinity = max(float(ap.affinity_score) for ap in artist_profiles) or 1.0

    # Load enrichment data for these artists
    enrichment_map: dict[str, ArtistEnrichment] = {
        row.artist_name_lower: row
        for row in db.query(ArtistEnrichment)
        .filter(ArtistEnrichment.artist_name_lower.in_(artist_names_lower))
        .all()
    }

    # Load last-played recency per artist (most recent track play date)
    from sqlalchemy import func as sqlfunc
    recency_rows = (
        db.query(
            TrackScore.artist_name,
            sqlfunc.max(TrackScore.last_played).label('last_played'),
        )
        .filter(
            TrackScore.user_id == uid,
            TrackScore.artist_name.in_(artist_names_in_graph),
            TrackScore.is_played == True,
        )
        .group_by(TrackScore.artist_name)
        .all()
    )
    from datetime import datetime as _dt
    _now = _dt.utcnow()
    recency_map: dict[str, float] = {}   # artist_name → days_since_last_play
    for row in recency_rows:
        if row.last_played:
            recency_map[row.artist_name] = max(0.0, (_now - row.last_played).days)

    # Load per-artist skip rate from ArtistProfile (already on ap)
    # Load genre profiles for genre nodes
    genre_profiles = (
        db.query(GenreProfile)
        .filter_by(user_id=uid)
        .order_by(GenreProfile.affinity_score.desc())
        .limit(20)
        .all()
    )
    genre_names_in_graph = {gp.genre for gp in genre_profiles}

    # ── Build nodes ───────────────────────────────────────────────────────────

    nodes = []

    for ap in artist_profiles:
        affinity = float(ap.affinity_score)
        enc = enrichment_map.get(ap.artist_name.lower())

        tags = []
        trend = "stable"
        popularity = None
        if enc:
            try:
                tags = json.loads(enc.tags) if enc.tags else []
            except Exception:
                tags = []
            trend = enc.trend_direction or "stable"
            popularity = enc.popularity_score

        # Parse related artists from ArtistProfile (copied from enrichment on last index)
        related_artists = []
        if ap.related_artists:
            try:
                raw = json.loads(ap.related_artists)
                related_artists = [r.get("name", "") for r in raw if r.get("name")]
            except Exception:
                pass

        nodes.append({
            "id": f"artist:{ap.artist_name}",
            "label": ap.artist_name,
            "type": "artist",
            "affinity": affinity,
            "plays": ap.total_plays,
            "genre": ap.primary_genre,
            "popularity": popularity,
            "tags": tags[:5],
            "trend": trend,
            "color": _affinity_to_color(affinity),
            "size": _popularity_to_size(affinity, ap.total_plays),
            "has_favorite": ap.has_favorite,
            "replay_boost": round(ap.replay_boost or 0.0, 1),
            # New fields for visualisation
            "skip_rate": round(float(ap.skip_rate), 3),
            "total_skips": ap.total_skips,
            "days_since_played": recency_map.get(ap.artist_name),
            "total_tracks_played": ap.total_tracks_played,
        })

    for gp in genre_profiles:
        affinity = float(gp.affinity_score)
        nodes.append({
            "id": f"genre:{gp.genre}",
            "label": gp.genre,
            "type": "genre",
            "affinity": affinity,
            "plays": gp.total_plays,
            "color": "#6b7280",   # neutral grey for genre nodes
            "size": round(6 + (affinity / 100) * 18, 1),
        })

    # ── Build edges ───────────────────────────────────────────────────────────

    edges = []
    seen_edges: set[tuple] = set()

    # Artist→Artist edges from ArtistRelation
    relations = (
        db.query(ArtistRelation)
        .filter(ArtistRelation.artist_a.in_(artist_names_in_graph))
        .all()
    )

    for rel in relations:
        target_in_graph = rel.artist_b in artist_names_in_graph
        if not target_in_graph:
            continue
        edge_key = tuple(sorted([rel.artist_a, rel.artist_b]))
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        # Source artist affinity (normalized 0-1) scales the edge weight
        source_profile = next(
            (ap for ap in artist_profiles if ap.artist_name == rel.artist_a), None
        )
        if not source_profile:
            continue
        source_affinity_norm = float(source_profile.affinity_score) / max_affinity

        weight = round(rel.match_score * source_affinity_norm, 4)
        if weight < 0.05:
            continue  # prune very weak edges to keep graph readable

        edges.append({
            "source": f"artist:{rel.artist_a}",
            "target": f"artist:{rel.artist_b}",
            "weight": weight,
            "match_score": rel.match_score,
            "type": "similar",
        })

    # Artist→Genre edges from ArtistProfile.primary_genre
    for ap in artist_profiles:
        if ap.primary_genre and ap.primary_genre in genre_names_in_graph:
            affinity_norm = float(ap.affinity_score) / max_affinity
            edges.append({
                "source": f"artist:{ap.artist_name}",
                "target": f"genre:{ap.primary_genre}",
                "weight": round(affinity_norm * 0.5, 4),  # lighter than similarity edges
                "type": "genre",
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "total_artists": total_artists,
            "shown_artists": len(artist_profiles),
            "total_edges": len(edges),
            "min_affinity": min_affinity,
        },
    }


@router.get("/artist/{artist_name}")
def get_artist_detail(
    artist_name: str,
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    _: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Full enrichment detail for a single artist — shown in the graph's
    click-to-expand panel.
    """
    uid = _resolve_user(user_id, username, db)

    ap = db.query(ArtistProfile).filter_by(
        user_id=uid, artist_name=artist_name
    ).first()
    if not ap:
        raise HTTPException(404, f"Artist '{artist_name}' not in user's profile")

    enc = db.query(ArtistEnrichment).filter_by(
        artist_name_lower=artist_name.lower()
    ).first()

    # Tracks by this artist that the user has played (top 10 by play count)
    from models import TrackScore
    from sqlalchemy import text as satext
    top_tracks = (
        db.query(TrackScore)
        .filter_by(user_id=uid, artist_name=artist_name, is_played=True)
        .order_by(satext("play_count DESC"))
        .limit(10)
        .all()
    )

    similar = []
    tags = []
    if enc:
        try:
            similar = json.loads(enc.similar_artists) if enc.similar_artists else []
        except Exception:
            pass
        try:
            tags = json.loads(enc.tags) if enc.tags else []
        except Exception:
            pass

    return {
        "artist_name": artist_name,
        "affinity_score": float(ap.affinity_score),
        "total_plays": ap.total_plays,
        "total_tracks_played": ap.total_tracks_played,
        "total_skips": ap.total_skips,
        "skip_rate": float(ap.skip_rate),
        "has_favorite": ap.has_favorite,
        "primary_genre": ap.primary_genre,
        "replay_boost": round(ap.replay_boost or 0.0, 1),
        # Enrichment data
        "biography": enc.biography if enc else None,
        "image_url": enc.image_url if enc else None,
        "lastfm_url": enc.lastfm_url if enc else None,
        "global_listeners": enc.global_listeners if enc else None,
        "global_playcount": enc.global_playcount if enc else None,
        "popularity_score": enc.popularity_score if enc else None,
        "trend_direction": enc.trend_direction if enc else None,
        "trend_pct": enc.trend_pct if enc else None,
        "tags": tags,
        "similar_artists": similar[:5],
        "enriched_at": enc.enriched_at.isoformat() if enc and enc.enriched_at else None,
        # User's top tracks by this artist
        "top_tracks": [
            {
                "track_name": t.track_name,
                "album_name": t.album_name,
                "play_count": t.play_count,
                "final_score": float(t.final_score),
                "is_favorite": t.is_favorite,
                "cooldown_until": t.cooldown_until.isoformat() if t.cooldown_until else None,
                "skip_penalty": float(t.skip_penalty) if t.skip_penalty else 0.0,
                "last_played": t.last_played.isoformat() if t.last_played else None,
                "recency_score": float(t.recency_score) if t.recency_score else 0.0,
                "replay_boost": float(t.replay_boost) if t.replay_boost else 0.0,
            }
            for t in top_tracks
        ],
    }