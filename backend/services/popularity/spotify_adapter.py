"""
Spotify adapter — uses spotipy (Client Credentials flow, no user auth needed).
"""
import logging
from typing import Optional

from .base import BasePopularityAdapter, ArtistInfo, AlbumPopularity, TrendingTrack

log = logging.getLogger(__name__)


class SpotifyAdapter(BasePopularityAdapter):

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._sp = None
        self._broken = False   # set True on 403/auth failure to skip all future calls

    def _client(self):
        if self._broken:
            return None
        if self._sp is not None:
            return self._sp
        if not self._client_id or not self._client_secret:
            return None
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            auth = SpotifyClientCredentials(
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            self._sp = spotipy.Spotify(auth_manager=auth, requests_timeout=8)
            return self._sp
        except Exception as e:
            log.warning(f"Spotify init failed: {e}")
            self._broken = True
            return None

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def get_artist_info(self, name: str) -> Optional[ArtistInfo]:
        sp = self._client()
        if not sp:
            return None
        try:
            results = sp.search(q=f"artist:{name}", type="artist", limit=1)
            items = results.get("artists", {}).get("items", [])
            if not items:
                return None
            a = items[0]
            # Get related artists
            related = []
            try:
                ra = sp.artist_related_artists(a["id"])
                related = [r["name"] for r in ra.get("artists", [])[:10]]
            except Exception:
                pass
            image_url = a["images"][0]["url"] if a.get("images") else None
            return ArtistInfo(
                name=a["name"],
                listeners=a.get("followers", {}).get("total"),
                tags=a.get("genres", []),
                similar_artists=related,
                image_url=image_url,
                source="spotify",
            )
        except Exception as e:
            msg = str(e)
            if "403" in msg or "401" in msg:
                log.warning(f"Spotify auth error — disabling adapter: {e}")
                self._broken = True
                self._sp = None
            else:
                log.warning(f"Spotify get_artist_info({name}): {e}")
            return None

    def get_album_popularity(self, artist: str, album: str) -> Optional[AlbumPopularity]:
        sp = self._client()
        if not sp:
            return None
        try:
            results = sp.search(q=f"artist:{artist} album:{album}", type="album", limit=1)
            items = results.get("albums", {}).get("items", [])
            if not items:
                return None
            a = items[0]
            # Get full album details for popularity
            full = sp.album(a["id"])
            raw_pop = full.get("popularity", 0)  # Spotify: 0-100
            release_date = full.get("release_date", "")
            year = int(release_date[:4]) if release_date else None
            image_url = full["images"][0]["url"] if full.get("images") else None
            return AlbumPopularity(
                artist=artist,
                album=album,
                score=float(raw_pop),
                release_year=year,
                image_url=image_url,
                source="spotify",
            )
        except Exception as e:
            log.warning(f"Spotify get_album_popularity({artist}, {album}): {e}")
            return None

    def get_trending_tracks(self, limit: int = 50) -> list[TrendingTrack]:
        sp = self._client()
        if not sp:
            return []
        try:
            # Use Spotify's Global Top 50 playlist
            playlist_id = "37i9dQZEVXbMDoHDwVN2tF"
            results = sp.playlist_tracks(playlist_id, limit=min(limit, 50))
            tracks = []
            for i, item in enumerate(results.get("items", []), 1):
                t = item.get("track")
                if not t:
                    continue
                tracks.append(TrendingTrack(
                    title=t["name"],
                    artist=t["artists"][0]["name"] if t.get("artists") else "Unknown",
                    rank=i,
                    score=float(t.get("popularity", 0)),
                    source="spotify",
                ))
            return tracks
        except Exception as e:
            log.warning(f"Spotify get_trending_tracks: {e}")
            return []

    def get_similar_artists(self, artist_name: str) -> list[str]:
        info = self.get_artist_info(artist_name)
        return info.similar_artists if info else []
