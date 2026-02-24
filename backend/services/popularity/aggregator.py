
"""
PopularityAggregator — unified interface over all adapters.
Results are cached in SQLite for 24 hours to avoid hammering external APIs.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Optional

# Each adapter call runs in a ThreadPoolExecutor with this timeout.
# 12 seconds is intentionally generous — Last.fm can be slow under load.
# If an adapter times out, we log a warning and continue with other adapters
# rather than failing the whole request. External API failures should never
# block playlist generation.
ADAPTER_TIMEOUT_SECS = 12

from sqlalchemy.orm import Session

from .base import ArtistInfo, AlbumPopularity, TrendingTrack
from .spotify_adapter import SpotifyAdapter
from .lastfm_adapter import LastFmAdapter
from .musicbrainz_adapter import MusicBrainzAdapter
from .billboard_adapter import BillboardAdapter

log = logging.getLogger(__name__)

# Results from all adapters are cached in the popularity_cache table for this
# many hours. This prevents hammering external APIs during index runs and means
# the same artist info is consistent across all users on the same server.
# Increase this if you hit Last.fm or Spotify rate limits.
CACHE_TTL_HOURS = 24


class PopularityAggregator:

    def __init__(
        self,
        spotify_client_id: str = "",
        spotify_client_secret: str = "",
        lastfm_api_key: str = "",
        lastfm_api_secret: str = "",
    ):
        self.adapters = {
            "spotify":      SpotifyAdapter(spotify_client_id, spotify_client_secret),
            "lastfm":       LastFmAdapter(lastfm_api_key, lastfm_api_secret),
            "musicbrainz":  MusicBrainzAdapter(),
            "billboard":    BillboardAdapter(),
        }

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cache_get(self, db: Session, key: str) -> Optional[dict]:
        from models import PopularityCache
        row = db.query(PopularityCache).filter_by(cache_key=key).first()
        if not row:
            return None
        if datetime.utcnow() > row.expires_at:
            db.delete(row)
            db.commit()
            return None
        return json.loads(row.payload)

    def _cache_set(self, db: Session, key: str, data: dict):
        from models import PopularityCache
        row = db.query(PopularityCache).filter_by(cache_key=key).first()
        if not row:
            row = PopularityCache(cache_key=key)
            db.add(row)
        row.payload = json.dumps(data)
        row.expires_at = datetime.utcnow() + timedelta(hours=CACHE_TTL_HOURS)
        row.updated_at = datetime.utcnow()
        db.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_artist_info(self, name: str, db: Optional[Session] = None) -> dict:
        """
        Merge artist info from all configured adapters.
        Returns a unified dict with tags, similar_artists, image_url, and a
        normalised popularity score 0–100.
        """
        cache_key = f"artist:{name.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached

        results: list[ArtistInfo] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(adapter.get_artist_info, name): adapter_name
                for adapter_name, adapter in self.adapters.items()
                if adapter.is_configured()
            }
            for future, adapter_name in futures.items():
                try:
                    info = future.result(timeout=ADAPTER_TIMEOUT_SECS)
                    if info:
                        results.append(info)
                except FuturesTimeoutError:
                    log.warning(f"{adapter_name} get_artist_info timed out after {ADAPTER_TIMEOUT_SECS}s")
                except Exception as e:
                    log.warning(f"{adapter_name} get_artist_info failed: {e}")

        merged = self._merge_artist_info(name, results)
        if db:
            self._cache_set(db, cache_key, merged)
        return merged

    def get_album_popularity(
        self, artist: str, album: str, db: Optional[Session] = None
    ) -> dict:
        cache_key = f"album:{artist.lower()}:{album.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached

        results: list[AlbumPopularity] = []
        for name_, adapter in self.adapters.items():
            if not adapter.is_configured():
                continue
            try:
                pop = adapter.get_album_popularity(artist, album)
                if pop:
                    results.append(pop)
            except Exception as e:
                log.warning(f"{name_} get_album_popularity failed: {e}")

        merged = self._merge_album_popularity(artist, album, results)
        if db:
            self._cache_set(db, cache_key, merged)
        return merged

    def get_trending_tracks(
        self, limit: int = 50, db: Optional[Session] = None
    ) -> list[dict]:
        cache_key = f"trending:{limit}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached.get("tracks", [])

        all_tracks: list[TrendingTrack] = []
        for name_, adapter in self.adapters.items():
            if not adapter.is_configured():
                continue
            try:
                tracks = adapter.get_trending_tracks(limit=limit)
                all_tracks.extend(tracks)
            except Exception as e:
                log.warning(f"{name_} get_trending_tracks failed: {e}")

        merged = self._merge_trending(all_tracks, limit)
        if db:
            self._cache_set(db, cache_key, {"tracks": merged})
        return merged

    def get_similar_artists(
        self, artist_name: str, db: Optional[Session] = None
    ) -> list[str]:
        cache_key = f"similar:{artist_name.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached.get("artists", [])

        seen: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(adapter.get_similar_artists, artist_name): adapter_name
                for adapter_name, adapter in self.adapters.items()
                if adapter.is_configured()
            }
            for future, adapter_name in futures.items():
                try:
                    similar = future.result(timeout=ADAPTER_TIMEOUT_SECS)
                    for s in similar:
                        seen[s] = seen.get(s, 0) + 1
                except FuturesTimeoutError:
                    log.warning(f"{adapter_name} get_similar_artists timed out after {ADAPTER_TIMEOUT_SECS}s")
                except Exception as e:
                    log.warning(f"{adapter_name} get_similar_artists failed: {e}")

        # Sort by how many sources agree
        ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
        result = [name for name, _ in ranked[:20]]
        if db:
            self._cache_set(db, cache_key, {"artists": result})
        return result

    # ── Merge helpers ─────────────────────────────────────────────────────────

    def _merge_artist_info(self, name: str, results: list[ArtistInfo]) -> dict:
        if not results:
            return {"name": name, "popularity_score": 0.0, "tags": [], "similar_artists": [], "image_url": None, "sources": []}

        # Popularity: average non-zero listener scores, normalised
        scores = []
        for r in results:
            if r.listeners and r.listeners > 0:
                import math
                s = min(100.0, (math.log1p(r.listeners) / math.log1p(10_000_000)) * 100)
                scores.append(s)
        pop = sum(scores) / len(scores) if scores else 0.0

        # Merge tags + similar (deduplicated, frequency-ranked)
        tag_counts: dict[str, int] = {}
        similar_counts: dict[str, int] = {}
        image_url = None
        for r in results:
            for t in r.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            for s in r.similar_artists:
                similar_counts[s] = similar_counts.get(s, 0) + 1
            if not image_url and r.image_url:
                image_url = r.image_url

        tags = [t for t, _ in sorted(tag_counts.items(), key=lambda x: -x[1])[:10]]
        similar = [s for s, _ in sorted(similar_counts.items(), key=lambda x: -x[1])[:15]]

        # Store raw max listener count so recommender can compute log-scale fame
        max_listeners = max((r.listeners or 0) for r in results)

        return {
            "name": results[0].name,
            "popularity_score": round(pop, 1),
            "listener_count": max_listeners,
            "tags": tags,
            "similar_artists": similar,
            "image_url": image_url,
            "sources": list({r.source for r in results}),
        }

    def _merge_album_popularity(
        self, artist: str, album: str, results: list[AlbumPopularity]
    ) -> dict:
        scores = [r.score for r in results if r.score > 0]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        image_url = next((r.image_url for r in results if r.image_url), None)
        release_year = next((r.release_year for r in results if r.release_year), None)

        return {
            "artist": artist,
            "album": album,
            "popularity_score": round(avg_score, 1),
            "release_year": release_year,
            "image_url": image_url,
            "sources": list({r.source for r in results}),
        }

    def _merge_trending(self, tracks: list[TrendingTrack], limit: int) -> list[dict]:
        # Group by (title, artist), average scores
        seen: dict[tuple, list[float]] = {}
        meta: dict[tuple, TrendingTrack] = {}
        for t in tracks:
            key = (t.title.lower(), t.artist.lower())
            seen.setdefault(key, []).append(t.score)
            meta[key] = t

        merged = []
        for key, scores in seen.items():
            t = meta[key]
            merged.append({
                "title": t.title,
                "artist": t.artist,
                "score": round(sum(scores) / len(scores), 1),
                "sources": [],
            })

        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:limit]

    # ── Config management ─────────────────────────────────────────────────────

    def update_credentials(
        self,
        spotify_client_id: str = "",
        spotify_client_secret: str = "",
        lastfm_api_key: str = "",
        lastfm_api_secret: str = "",
    ):
        self.adapters["spotify"] = SpotifyAdapter(spotify_client_id, spotify_client_secret)
        self.adapters["lastfm"] = LastFmAdapter(lastfm_api_key, lastfm_api_secret)
        # Reset spotipy client cache
        self.adapters["spotify"]._sp = None

    def adapter_status(self) -> dict:
        return {
            name: adapter.is_configured()
            for name, adapter in self.adapters.items()
        }

    def get_tag_top_artists(self, tag: str, limit: int = 50, db: Optional[Session] = None) -> list[dict]:
        """Get top artists for a genre/mood tag. Cached 48h (tags change slowly)."""
        cache_key = f"tag_artists:{tag.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached.get("artists", [])

        adapter = self.adapters.get("lastfm")
        if not adapter or not adapter.is_configured():
            return []

        results = adapter.get_tag_top_artists(tag, limit=limit)
        if db and results:
            # Cache for 48h — tag charts change slowly
            from models import PopularityCache
            from datetime import timedelta
            row = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
            if not row:
                row = PopularityCache(cache_key=cache_key)
                db.add(row)
            row.payload = __import__("json").dumps({"artists": results})
            row.expires_at = __import__("datetime").datetime.utcnow() + timedelta(hours=48)
            row.updated_at = __import__("datetime").datetime.utcnow()
            db.commit()
        return results

    def get_similar_tags(self, tag: str, limit: int = 10, db: Optional[Session] = None) -> list[str]:
        """Get tags adjacent to this genre. Cached 72h."""
        cache_key = f"tag_similar:{tag.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached.get("tags", [])

        adapter = self.adapters.get("lastfm")
        if not adapter or not adapter.is_configured():
            return []

        results = adapter.get_similar_tags(tag, limit=limit)
        if db and results:
            from models import PopularityCache
            from datetime import timedelta
            row = db.query(PopularityCache).filter_by(cache_key=cache_key).first()
            if not row:
                row = PopularityCache(cache_key=cache_key)
                db.add(row)
            row.payload = __import__("json").dumps({"tags": results})
            row.expires_at = __import__("datetime").datetime.utcnow() + timedelta(hours=72)
            row.updated_at = __import__("datetime").datetime.utcnow()
            db.commit()
        return results

    def get_artist_top_album(self, artist_name: str, db: Optional[Session] = None) -> Optional[dict]:
        """Get an artist's top non-compilation album. Cached 48h."""
        cache_key = f"top_album:{artist_name.lower()}"
        if db:
            cached = self._cache_get(db, cache_key)
            if cached:
                return cached

        adapter = self.adapters.get("lastfm")
        if not adapter or not adapter.is_configured():
            return None

        result = adapter.get_artist_top_album(artist_name)
        if db and result:
            # Normalise to always store under "album" key so readers are consistent.
            # Last.fm adapter uses "name"; we add "album" as a canonical alias.
            normalised = dict(result)
            if "name" in normalised and "album" not in normalised:
                normalised["album"] = normalised["name"]
            self._cache_set(db, cache_key, normalised)
            result = normalised
        return result
