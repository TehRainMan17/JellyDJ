"""
jellyfin_client — shared helpers for talking to Jellyfin.

Centralises credential lookup, header construction, and (eventually) httpx
client factories. Replaces duplicated `_get_jellyfin_creds` / `_jellyfin_creds`
helpers that previously lived in ~5 service modules.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from crypto import decrypt
from models import ConnectionSettings


def get_jellyfin_creds(db: Session) -> tuple[str, str]:
    """Return (base_url, api_key) for the configured Jellyfin server.

    Raises RuntimeError if no Jellyfin connection is configured.
    """
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        raise RuntimeError("Jellyfin not configured")
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


def get_jellyfin_creds_or_none(db: Session) -> tuple[str, str] | None:
    """Return (base_url, api_key) or None if Jellyfin is not configured.

    Use this when the caller wants to gracefully no-op rather than fail —
    e.g. background jobs that should skip when Jellyfin is unavailable.
    """
    row = db.query(ConnectionSettings).filter_by(service="jellyfin").first()
    if not row or not row.base_url or not row.api_key_encrypted:
        return None
    return row.base_url.rstrip("/"), decrypt(row.api_key_encrypted)


def jellyfin_headers(api_key: str) -> dict[str, str]:
    """Standard Jellyfin auth header."""
    return {"X-Emby-Token": api_key}
