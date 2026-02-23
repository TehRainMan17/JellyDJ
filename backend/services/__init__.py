
"""
Module-level aggregator singleton with credential-keyed caching.

Why: Without caching, get_aggregator() creates a fresh PopularityAggregator on
every call, which resets the Spotify adapter's _broken=True flag after a 403.
This means Spotify gets hammered with repeated failed auth attempts — one per
artist during an index run.

With caching:
  - Same credentials → same instance → _broken state persists between calls
  - Credentials change → cache invalidated → fresh instance built
  - Cache expires after 5 min as a safety valve (picks up credential changes)
"""
from __future__ import annotations

import time
from sqlalchemy.orm import Session
from .popularity.aggregator import PopularityAggregator

_cached_agg: PopularityAggregator | None = None
_cached_key: tuple = ()   # (spotify_id, spotify_secret, lastfm_key, lastfm_secret)
_cached_at: float = 0.0
_CACHE_TTL_SECS = 300     # 5 minutes


def get_aggregator(db: Session) -> PopularityAggregator:
    """
    Return a PopularityAggregator loaded with current DB credentials.
    Instance is cached so _broken=True (Spotify 403) survives between calls.
    Cache is invalidated when credentials change or after 5 minutes.
    """
    global _cached_agg, _cached_key, _cached_at

    from models import ExternalApiSettings
    from crypto import decrypt

    def _get(key: str) -> str:
        row = db.query(ExternalApiSettings).filter_by(key=key).first()
        if not row or not row.value_encrypted:
            return ""
        try:
            return decrypt(row.value_encrypted)
        except Exception:
            return ""

    def _clean(val: str) -> str:
        v = (val or "").strip()
        return v if len(v) > 8 else ""

    spotify_id     = _clean(_get("spotify_client_id"))
    spotify_secret = _clean(_get("spotify_client_secret"))
    lastfm_key     = _clean(_get("lastfm_api_key"))
    lastfm_secret  = _clean(_get("lastfm_api_secret"))

    cred_key = (spotify_id, spotify_secret, lastfm_key, lastfm_secret)
    now = time.monotonic()

    # Return cached instance if credentials haven't changed and TTL is fresh
    if (
        _cached_agg is not None
        and _cached_key == cred_key
        and (now - _cached_at) < _CACHE_TTL_SECS
    ):
        return _cached_agg

    # Build new instance
    _cached_agg = PopularityAggregator(
        spotify_client_id=spotify_id,
        spotify_client_secret=spotify_secret,
        lastfm_api_key=lastfm_key,
        lastfm_api_secret=lastfm_secret,
    )
    _cached_key = cred_key
    _cached_at  = now
    return _cached_agg


def invalidate_aggregator_cache() -> None:
    """Call this when credentials are saved/changed in Settings."""
    global _cached_agg, _cached_key, _cached_at
    _cached_agg = None
    _cached_key = ()
    _cached_at  = 0.0
