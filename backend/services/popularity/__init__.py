"""
Module-level aggregator singleton.
Call get_aggregator(db) to get an instance loaded with current DB credentials.
"""
from __future__ import annotations
from sqlalchemy.orm import Session
from .aggregator import PopularityAggregator


def get_aggregator(db: Session) -> PopularityAggregator:
    """
    Build a PopularityAggregator from credentials stored in the DB.
    Intentionally not cached at module level so credential changes take effect immediately.
    """
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
        """Strip whitespace; treat placeholder/test values as empty."""
        v = (val or "").strip()
        return v if len(v) > 8 else ""   # real credentials are always >8 chars

    return PopularityAggregator(
        spotify_client_id=_clean(_get("spotify_client_id")),
        spotify_client_secret=_clean(_get("spotify_client_secret")),
        lastfm_api_key=_clean(_get("lastfm_api_key")),
        lastfm_api_secret=_clean(_get("lastfm_api_secret")),
    )