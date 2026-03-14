from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from auth import require_admin, UserContext
from database import get_db
from models import ExternalApiSettings
from crypto import encrypt, decrypt
from services.popularity import get_aggregator

router = APIRouter(prefix="/api/external-apis", tags=["external-apis"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_key(db: Session, key: str, value: str):
    row = db.query(ExternalApiSettings).filter_by(key=key).first()
    if not row:
        row = ExternalApiSettings(key=key)
        db.add(row)
    row.value_encrypted = encrypt(value) if value else ""
    row.updated_at = datetime.utcnow()
    db.commit()


def _get_key(db: Session, key: str) -> str:
    row = db.query(ExternalApiSettings).filter_by(key=key).first()
    if not row or not row.value_encrypted:
        return ""
    try:
        return decrypt(row.value_encrypted)
    except Exception:
        return ""


def _has_key(db: Session, key: str) -> bool:
    row = db.query(ExternalApiSettings).filter_by(key=key).first()
    return bool(row and row.value_encrypted)


def _last_updated(db: Session, key: str) -> Optional[datetime]:
    row = db.query(ExternalApiSettings).filter_by(key=key).first()
    return row.updated_at if row else None


# ── Schemas ───────────────────────────────────────────────────────────────────

class SpotifyCredentials(BaseModel):
    client_id: str
    client_secret: str


class LastFmCredentials(BaseModel):
    api_key: str
    api_secret: str = ""


# ── Status endpoint ───────────────────────────────────────────────────────────

@router.get("/status")
def get_status(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    """Return configuration status for all external services."""
    aggregator = get_aggregator(db)
    statuses = aggregator.adapter_status()

    return {
        "spotify": {
            "configured": statuses.get("spotify", False),
            "has_client_id": _has_key(db, "spotify_client_id"),
            "has_client_secret": _has_key(db, "spotify_client_secret"),
            "last_updated": _last_updated(db, "spotify_client_id"),
        },
        "lastfm": {
            "configured": statuses.get("lastfm", False),
            "has_api_key": _has_key(db, "lastfm_api_key"),
            "last_updated": _last_updated(db, "lastfm_api_key"),
        },
        "musicbrainz": {
            "configured": True,
            "requires_key": False,
        },
        "billboard": {
            "configured": True,
            "requires_key": False,
        },
    }


# ── Spotify ───────────────────────────────────────────────────────────────────

@router.post("/spotify")
def save_spotify(creds: SpotifyCredentials, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    _set_key(db, "spotify_client_id", creds.client_id)
    _set_key(db, "spotify_client_secret", creds.client_secret)
    return {"ok": True}


@router.post("/spotify/test")
def test_spotify(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    client_id = _get_key(db, "spotify_client_id")
    client_secret = _get_key(db, "spotify_client_secret")
    if not client_id or not client_secret:
        raise HTTPException(400, "Spotify credentials not saved.")
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
        )
        # A cheap call to verify auth works
        sp.search(q="test", type="track", limit=1)
        return {"ok": True, "message": "Spotify connected successfully."}
    except Exception as e:
        raise HTTPException(502, f"Spotify authentication failed: {str(e)}")


# ── Last.fm ───────────────────────────────────────────────────────────────────

@router.post("/lastfm")
def save_lastfm(creds: LastFmCredentials, _: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    _set_key(db, "lastfm_api_key", creds.api_key)
    _set_key(db, "lastfm_api_secret", creds.api_secret)
    return {"ok": True}


@router.post("/lastfm/test")
def test_lastfm(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    api_key = _get_key(db, "lastfm_api_key")
    if not api_key:
        raise HTTPException(400, "Last.fm API key not saved.")
    try:
        import pylast
        network = pylast.LastFMNetwork(api_key=api_key)
        # Cheap validation call
        network.get_top_artists(limit=1)
        return {"ok": True, "message": "Last.fm connected successfully."}
    except Exception as e:
        raise HTTPException(502, f"Last.fm authentication failed: {str(e)}")


# ── MusicBrainz (no key) ──────────────────────────────────────────────────────

@router.post("/musicbrainz/test")
def test_musicbrainz(_: UserContext = Depends(require_admin)):
    try:
        import musicbrainzngs
        musicbrainzngs.set_useragent("JellyDJ", "0.1", "https://github.com/jellydj")
        musicbrainzngs.search_artists(artist="Radiohead", limit=1)
        return {"ok": True, "message": "MusicBrainz reachable."}
    except Exception as e:
        raise HTTPException(502, f"MusicBrainz unreachable: {str(e)}")


# ── Billboard (no key) ────────────────────────────────────────────────────────

@router.post("/billboard/test")
def test_billboard(_: UserContext = Depends(require_admin)):
    try:
        import billboard
        chart = billboard.ChartData("hot-100")
        if not chart:
            raise Exception("Empty chart returned")
        return {"ok": True, "message": f"Billboard Hot 100 fetched — #{1}: {chart[0].title} by {chart[0].artist}"}
    except Exception as e:
        raise HTTPException(502, f"Billboard fetch failed: {str(e)}")


# ── Cache management ──────────────────────────────────────────────────────────

@router.delete("/cache")
def clear_cache(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    from models import PopularityCache
    count = db.query(PopularityCache).delete()
    db.commit()
    return {"ok": True, "cleared": count}


@router.get("/cache/stats")
def cache_stats(_: UserContext = Depends(require_admin), db: Session = Depends(get_db)):
    from models import PopularityCache
    from datetime import datetime
    total = db.query(PopularityCache).count()
    expired = db.query(PopularityCache).filter(
        PopularityCache.expires_at < datetime.utcnow()
    ).count()
    return {"total_entries": total, "expired_entries": expired, "live_entries": total - expired}
