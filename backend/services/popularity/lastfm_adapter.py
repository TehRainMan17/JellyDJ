"""
Last.fm adapter — uses pylast.
"""
import logging
from typing import Optional

from .base import BasePopularityAdapter, ArtistInfo, AlbumPopularity, TrendingTrack

log = logging.getLogger(__name__)

# Last.fm listener ceiling used for normalisation (top artists have ~10M)
_LASTFM_MAX_LISTENERS = 10_000_000


class LastFmAdapter(BasePopularityAdapter):

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self._api_key = api_key
        self._api_secret = api_secret
        self._network = None

    def _net(self):
        if self._network is not None:
            return self._network
        if not self._api_key:
            return None
        try:
            import pylast
            self._network = pylast.LastFMNetwork(
                api_key=self._api_key,
                api_secret=self._api_secret or "",
            )
            return self._network
        except Exception as e:
            log.warning(f"LastFm init failed: {e}")
            return None

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def get_artist_info(self, name: str) -> Optional[ArtistInfo]:
        net = self._net()
        if not net:
            return None
        try:
            artist = net.get_artist(name)
            listeners = None
            play_count = None
            try:
                listeners = int(artist.get_listener_count())
                play_count = int(artist.get_playcount())
            except Exception:
                pass

            tags = []
            try:
                tags = [t.item.get_name() for t in artist.get_top_tags(limit=5)]
            except Exception:
                pass

            similar = []
            try:
                similar = [s.item.get_name() for s in artist.get_similar(limit=10)]
            except Exception:
                pass

            bio = None
            try:
                bio = artist.get_bio_summary()
            except Exception:
                pass

            return ArtistInfo(
                name=name,
                listeners=listeners,
                play_count=play_count,
                bio=bio,
                tags=tags,
                similar_artists=similar,
                source="lastfm",
            )
        except Exception as e:
            log.warning(f"LastFm get_artist_info({name}): {e}")
            return None

    def get_album_popularity(self, artist: str, album: str) -> Optional[AlbumPopularity]:
        net = self._net()
        if not net:
            return None
        try:
            alb = net.get_album(artist, album)
            play_count = None
            listeners = None
            try:
                play_count = int(alb.get_playcount())
                listeners = int(alb.get_listener_count())
            except Exception:
                pass

            # Normalise: use log scale on play_count vs ceiling
            score = 0.0
            if play_count:
                import math
                score = min(100.0, (math.log1p(play_count) / math.log1p(5_000_000)) * 100)

            image_url = None
            try:
                image_url = alb.get_cover_image()
            except Exception:
                pass

            return AlbumPopularity(
                artist=artist,
                album=album,
                score=score,
                listeners=listeners,
                play_count=play_count,
                image_url=image_url,
                source="lastfm",
            )
        except Exception as e:
            log.warning(f"LastFm get_album_popularity({artist}, {album}): {e}")
            return None

    def get_trending_tracks(self, limit: int = 50) -> list[TrendingTrack]:
        net = self._net()
        if not net:
            return []
        try:
            top = net.get_top_tracks(limit=limit)
            tracks = []
            for i, item in enumerate(top, 1):
                t = item.item
                try:
                    name = t.get_name()
                    artist = t.get_artist().get_name()
                    pc = int(t.get_playcount() or 0)
                    import math
                    score = min(100.0, (math.log1p(pc) / math.log1p(5_000_000)) * 100)
                    tracks.append(TrendingTrack(
                        title=name, artist=artist, rank=i, score=score, source="lastfm"
                    ))
                except Exception:
                    continue
            return tracks
        except Exception as e:
            log.warning(f"LastFm get_trending_tracks: {e}")
            return []

    def get_similar_artists(self, artist_name: str) -> list[str]:
        info = self.get_artist_info(artist_name)
        return info.similar_artists if info else []

    def get_tag_top_artists(self, tag: str, limit: int = 50) -> list[dict]:
        """
        Get top artists for a Last.fm tag/genre.
        Returns list of dicts with 'name' and 'listeners'.
        This is the key call for tag-based discovery.
        """
        net = self._net()
        if not net:
            return []
        try:
            tag_obj = net.get_tag(tag)
            top = tag_obj.get_top_artists(limit=limit)
            results = []
            for item in top:
                try:
                    name = item.item.get_name()
                    # Weight is relative rank score from Last.fm
                    weight = int(item.weight) if hasattr(item, 'weight') else 50
                    results.append({"name": name, "weight": weight})
                except Exception:
                    continue
            return results
        except Exception as e:
            log.warning(f"LastFm get_tag_top_artists({tag}): {e}")
            return []

    def get_similar_tags(self, tag: str, limit: int = 10) -> list[str]:
        """
        Get tags similar to a given tag — used for adjacent genre exploration.
        Returns [] silently if the pylast version doesn't support get_similar()
        on Tag objects (this varies by pylast version — not a critical feature).
        """
        net = self._net()
        if not net:
            return []
        try:
            tag_obj = net.get_tag(tag)
            # pylast's Tag.get_similar() was added in newer versions — guard it
            if not hasattr(tag_obj, "get_similar"):
                return []
            similar = tag_obj.get_similar()
            return [t.item.get_name() for t in similar[:limit]]
        except Exception:
            # Best-effort enhancement — fail silently, don't spam logs
            return []

    def get_artist_top_album(self, artist_name: str) -> Optional[dict]:
        """Get an artist's most popular album from Last.fm."""
        net = self._net()
        if not net:
            return None
        try:
            artist = net.get_artist(artist_name)
            top_albums = artist.get_top_albums(limit=5)
            for item in top_albums:
                try:
                    alb = item.item
                    name = alb.get_name()
                    # Skip obvious compilations/hits collections
                    skip_words = ['greatest hits', 'best of', 'collection', 'essential',
                                  'platinum', 'gold', 'anthology', 'singles']
                    if any(w in name.lower() for w in skip_words):
                        continue
                    image = None
                    try:
                        image = alb.get_cover_image()
                    except Exception:
                        pass
                    playcount = 0
                    try:
                        playcount = int(alb.get_playcount() or 0)
                    except Exception:
                        pass
                    return {"name": name, "image_url": image, "playcount": playcount}
                except Exception:
                    continue
            # Fallback: return first album even if it's a hits collection
            if top_albums:
                try:
                    alb = top_albums[0].item
                    return {"name": alb.get_name(), "image_url": None, "playcount": 0}
                except Exception:
                    pass
            return None
        except Exception as e:
            log.warning(f"LastFm get_artist_top_album({artist_name}): {e}")
            return None