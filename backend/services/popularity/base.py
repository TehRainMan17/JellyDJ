"""
Base interface for all external popularity/metadata adapters.
Every adapter must implement these four methods.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ArtistInfo:
    name: str
    listeners: Optional[int] = None
    play_count: Optional[int] = None
    bio: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    similar_artists: list[str] = field(default_factory=list)
    image_url: Optional[str] = None
    source: str = ""


@dataclass
class AlbumPopularity:
    artist: str
    album: str
    score: float = 0.0          # 0–100 normalised
    listeners: Optional[int] = None
    play_count: Optional[int] = None
    release_year: Optional[int] = None
    image_url: Optional[str] = None
    source: str = ""


@dataclass
class TrendingTrack:
    title: str
    artist: str
    rank: int = 0
    score: float = 0.0
    source: str = ""


class BasePopularityAdapter(ABC):

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this adapter has the credentials it needs."""

    @abstractmethod
    def get_artist_info(self, name: str) -> Optional[ArtistInfo]:
        """Return metadata + similarity list for an artist."""

    @abstractmethod
    def get_album_popularity(self, artist: str, album: str) -> Optional[AlbumPopularity]:
        """Return a popularity score + metadata for a specific album."""

    @abstractmethod
    def get_trending_tracks(self, limit: int = 50) -> list[TrendingTrack]:
        """Return globally trending tracks right now."""

    @abstractmethod
    def get_similar_artists(self, artist_name: str) -> list[str]:
        """Return a list of similar artist name strings."""