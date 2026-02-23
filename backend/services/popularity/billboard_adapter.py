"""
Billboard adapter — uses billboard.py. No API key required.
Scrapes Billboard chart data.
"""
import logging
from typing import Optional

from .base import BasePopularityAdapter, ArtistInfo, AlbumPopularity, TrendingTrack

log = logging.getLogger(__name__)


class BillboardAdapter(BasePopularityAdapter):

    def is_configured(self) -> bool:
        return True  # No key needed

    def get_artist_info(self, name: str) -> Optional[ArtistInfo]:
        # Billboard doesn't have artist lookup — return None gracefully
        return None

    def get_album_popularity(self, artist: str, album: str) -> Optional[AlbumPopularity]:
        # Billboard doesn't have per-album lookup by name in the library
        return None

    def get_trending_tracks(self, limit: int = 50) -> list[TrendingTrack]:
        try:
            import billboard
            chart = billboard.ChartData("hot-100")
            tracks = []
            for entry in chart[:limit]:
                # Rank 1 = score 100, rank 100 = score 1 (linear)
                score = max(1.0, 100.0 - (entry.rank - 1))
                tracks.append(TrendingTrack(
                    title=entry.title,
                    artist=entry.artist,
                    rank=entry.rank,
                    score=score,
                    source="billboard",
                ))
            return tracks
        except Exception as e:
            log.warning(f"Billboard get_trending_tracks: {e}")
            return []

    def get_similar_artists(self, artist_name: str) -> list[str]:
        # Billboard can't provide similarity — return empty
        return []

    def get_artist_chart_presence(self, artist_name: str) -> float:
        """
        Bonus: scan Hot-100 for any tracks by this artist.
        Returns a 0–100 score based on how many entries and their ranks.
        """
        try:
            import billboard
            chart = billboard.ChartData("hot-100")
            score = 0.0
            for entry in chart:
                if artist_name.lower() in entry.artist.lower():
                    score += max(1.0, 100.0 - (entry.rank - 1))
            return min(100.0, score)
        except Exception as e:
            log.warning(f"Billboard get_artist_chart_presence({artist_name}): {e}")
            return 0.0
