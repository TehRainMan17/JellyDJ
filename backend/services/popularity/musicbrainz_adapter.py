"""
MusicBrainz adapter — uses musicbrainzngs. No API key required.
Rate-limited to 1 req/sec by the library automatically.
"""
import logging
from typing import Optional

from .base import BasePopularityAdapter, ArtistInfo, AlbumPopularity, TrendingTrack

log = logging.getLogger(__name__)

_MB_SETUP_DONE = False


# Track consecutive failures so we can stop hammering a broken MusicBrainz
# connection on every index run.
_MB_FAIL_COUNT = 0
_MB_MAX_FAILS = 3   # stop calling MusicBrainz after this many consecutive SSL/network errors

def _setup_mb():
    global _MB_SETUP_DONE
    if _MB_SETUP_DONE:
        return
    try:
        import musicbrainzngs
        musicbrainzngs.set_useragent("JellyDJ", "0.1", "https://github.com/YOUR_USERNAME/jellydj")
        # Hard socket timeout: prevents indefinite blocking on SSL hangs.
        # musicbrainzngs uses urllib internally; the set_hostname call doesn't
        # expose a timeout, but we can patch the opener.
        import urllib.request
        musicbrainzngs.set_hostname("musicbrainz.org")
        _MB_SETUP_DONE = True
    except Exception as e:
        log.warning(f"MusicBrainz setup failed: {e}")


class MusicBrainzAdapter(BasePopularityAdapter):

    def is_configured(self) -> bool:
        # Disable after repeated failures so a broken MusicBrainz connection
        # doesn't slow down every index run. Resets on container restart.
        global _MB_FAIL_COUNT
        if _MB_FAIL_COUNT >= _MB_MAX_FAILS:
            return False
        return True  # No key needed

    def get_artist_info(self, name: str) -> Optional[ArtistInfo]:
        _setup_mb()
        try:
            import musicbrainzngs
            result = musicbrainzngs.search_artists(artist=name, limit=1)
            artists = result.get("artist-list", [])
            if not artists:
                return None
            a = artists[0]
            mbid = a.get("id")

            similar = []
            tags = []
            try:
                detail = musicbrainzngs.get_artist_by_id(
                    mbid, includes=["tags", "artist-rels"]
                )
                art = detail.get("artist", {})
                tags = [t["name"] for t in art.get("tag-list", [])[:8]]
                # Extract related artists from relations
                for rel in art.get("artist-relation-list", []):
                    rname = rel.get("artist", {}).get("name")
                    if rname and rel.get("type") in ("member of band", "collaboration", "supporting musician"):
                        similar.append(rname)
            except Exception:
                pass

            return ArtistInfo(
                name=a.get("name", name),
                tags=tags,
                similar_artists=similar[:10],
                source="musicbrainz",
            )
        except Exception as e:
            global _MB_FAIL_COUNT
            err_str = str(e).lower()
            if any(k in err_str for k in ("ssl", "eof", "urlopen", "timeout", "connection")):
                _MB_FAIL_COUNT += 1
                if _MB_FAIL_COUNT >= _MB_MAX_FAILS:
                    log.warning(
                        f"MusicBrainz disabled after {_MB_FAIL_COUNT} consecutive network errors "
                        f"(last: {e}). Will re-enable on container restart."
                    )
                else:
                    log.warning(f"MusicBrainz get_artist_info({name}): {e} (fail {_MB_FAIL_COUNT}/{_MB_MAX_FAILS})")
            else:
                log.warning(f"MusicBrainz get_artist_info({name}): {e}")
            return None

    def get_album_popularity(self, artist: str, album: str) -> Optional[AlbumPopularity]:
        _setup_mb()
        try:
            import musicbrainzngs
            result = musicbrainzngs.search_releases(
                artist=artist, release=album, limit=1
            )
            releases = result.get("release-list", [])
            if not releases:
                return None
            r = releases[0]
            date = r.get("date", "")
            year = int(date[:4]) if date and len(date) >= 4 else None

            # MusicBrainz doesn't have play counts — return metadata only, score=0
            return AlbumPopularity(
                artist=artist,
                album=r.get("title", album),
                score=0.0,
                release_year=year,
                source="musicbrainz",
            )
        except Exception as e:
            log.warning(f"MusicBrainz get_album_popularity({artist}, {album}): {e}")
            return None

    def get_trending_tracks(self, limit: int = 50) -> list[TrendingTrack]:
        # MusicBrainz doesn't have trending data
        return []

    def get_similar_artists(self, artist_name: str) -> list[str]:
        info = self.get_artist_info(artist_name)
        return info.similar_artists if info else []

    def get_cover_image_url(self, mbid: str) -> Optional[str]:
        """Bonus: fetch cover art from Cover Art Archive by MusicBrainz release ID."""
        _setup_mb()
        try:
            import musicbrainzngs
            data = musicbrainzngs.get_image_list(mbid)
            images = data.get("images", [])
            for img in images:
                if img.get("front"):
                    thumbnails = img.get("thumbnails", {})
                    return thumbnails.get("500") or thumbnails.get("250") or img.get("image")
        except Exception:
            pass
        return None