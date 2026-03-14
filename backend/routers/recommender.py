from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from auth import get_current_user, UserContext
from database import get_db
from services.recommender import (
    recommend_library_tracks,
    recommend_new_albums,
    get_weight_presets,
    WEIGHT_PRESETS,
)

router = APIRouter(prefix="/api/recommender", tags=["recommender"])


@router.get("/presets")
def list_presets(_: UserContext = Depends(get_current_user)):
    """Return all available playlist weight presets."""
    return get_weight_presets()


@router.get("/library/{user_id}")
def preview_library_recommendations(
    user_id: str,
    playlist_type: str = Query(default="for_you"),
    limit: int = Query(default=30, ge=5, le=100),
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Preview the top scored library tracks for a user.
    Useful for testing the engine before Module 7 writes playlists to Jellyfin.
    """
    if not current_user.is_admin and user_id != current_user.user_id:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(403, "You can only view your own data.")
    if playlist_type not in WEIGHT_PRESETS:
        raise HTTPException(400, f"Unknown playlist_type '{playlist_type}'. Valid: {list(WEIGHT_PRESETS.keys())}")

    tracks = recommend_library_tracks(user_id, playlist_type, limit, db)
    if not tracks:
        raise HTTPException(404, "No tracks found. Has the indexer run for this user?")

    return {
        "user_id": user_id,
        "playlist_type": playlist_type,
        "count": len(tracks),
        "tracks": [
            {
                "jellyfin_item_id": t.jellyfin_item_id,
                "track_name": t.track_name,
                "artist_name": t.artist_name,
                "album_name": t.album_name,
                "genre": t.genre,
                "score": t.score,
                "play_count": t.play_count,
                "last_played": t.last_played,
                "is_favorite": t.is_favorite,
                "score_breakdown": t.score_breakdown,
            }
            for t in tracks
        ],
    }


@router.get("/new-albums/{user_id}")
def preview_new_albums(
    user_id: str,
    limit: int = Query(default=20, ge=5, le=100),
    current_user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Preview new album recommendations for a user.
    These feed the Discovery Queue in Module 6.
    """
    if not current_user.is_admin and user_id != current_user.user_id:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(403, "You can only view your own data.")
    albums = recommend_new_albums(user_id, limit, db)
    if not albums:
        raise HTTPException(
            404,
            "No recommendations generated. Make sure the indexer has run and "
            "external API caches are populated (run a Spotify/Last.fm lookup first)."
        )

    return {
        "user_id": user_id,
        "count": len(albums),
        "albums": [
            {
                "artist_name": a.artist_name,
                "album_name": a.album_name,
                "release_year": a.release_year,
                "popularity_score": a.popularity_score,
                "image_url": a.image_url,
                "why": a.why,
                "source_artist": a.source_artist,
                "source_affinity": a.source_affinity,
            }
            for a in albums
        ],
    }


@router.get("/users")
def list_recommendable_users(_: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all enabled users that have indexed data — for UI dropdowns."""
    from models import ManagedUser, UserSyncStatus
    users = (
        db.query(ManagedUser, UserSyncStatus)
        .join(UserSyncStatus, ManagedUser.jellyfin_user_id == UserSyncStatus.user_id, isouter=True)
        .filter(ManagedUser.has_activated == True)
        .all()
    )
    return [
        {
            "jellyfin_user_id": u.jellyfin_user_id,
            "username": u.username,
            "tracks_indexed": s.tracks_indexed if s else 0,
            "last_synced": s.last_synced if s else None,
            "ready": (s is not None and s.tracks_indexed > 0),
        }
        for u, s in users
    ]


@router.get("/new-albums/by-username/{username}")
def preview_new_albums_by_username(
    username: str,
    limit: int = Query(default=20, ge=5, le=100),
    _: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Convenience endpoint — look up user_id by Jellyfin username first.
    Useful for testing in the browser without knowing the UUID.
    """
    from models import ManagedUser
    user = db.query(ManagedUser).filter(
        ManagedUser.username.ilike(username)
    ).first()
    if not user:
        raise HTTPException(404, f"No managed user found with username '{username}'. "
                                 "Check /api/recommender/users for valid usernames.")
    return preview_new_albums(user.jellyfin_user_id, limit, db)


@router.get("/library/by-username/{username}")
def preview_library_by_username(
    username: str,
    playlist_type: str = Query(default="for_you"),
    limit: int = Query(default=30, ge=5, le=100),
    _: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Convenience endpoint — look up by username instead of UUID."""
    from models import ManagedUser
    user = db.query(ManagedUser).filter(
        ManagedUser.username.ilike(username)
    ).first()
    if not user:
        raise HTTPException(404, f"No managed user found with username '{username}'.")
    return preview_library_recommendations(user.jellyfin_user_id, playlist_type, limit, db)
