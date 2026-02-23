"""
JellyDJ Connections router

Includes a 60-second in-memory cache for GET /jellyfin and GET /lidarr so that
navigating back to the Dashboard doesn't fire live HTTP checks every time.
The cache is invalidated when credentials are saved or a /test endpoint is called.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import httpx
import time

from database import get_db
from models import ConnectionSettings, ManagedUser
from crypto import encrypt, decrypt

router = APIRouter(prefix="/api/connections", tags=["connections"])

# ── Connection status cache ───────────────────────────────────────────────────
# Keyed by service name. Avoids live HTTP on every dashboard load.
_conn_cache: dict = {}   # service -> {"ok": bool, "tested_at": float, "last_tested": datetime}
_CONN_CACHE_TTL = 60     # seconds


def _cache_put(service: str, ok: bool, last_tested: datetime):
    _conn_cache[service] = {
        "ok": ok,
        "last_tested": last_tested,
        "cached_at": time.monotonic(),
    }


def _cache_get(service: str) -> Optional[dict]:
    entry = _conn_cache.get(service)
    if not entry:
        return None
    if time.monotonic() - entry["cached_at"] > _CONN_CACHE_TTL:
        return None
    return entry


def _cache_invalidate(service: str):
    _conn_cache.pop(service, None)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConnectionPayload(BaseModel):
    base_url: str
    api_key: str


class ConnectionResponse(BaseModel):
    service: str
    base_url: str
    is_connected: bool
    last_tested: Optional[datetime]
    has_api_key: bool


class ManagedUserToggle(BaseModel):
    jellyfin_user_id: str
    is_enabled: bool
    username: str = ""   # optional — frontend sends the known display name


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create(db: Session, service: str) -> ConnectionSettings:
    obj = db.query(ConnectionSettings).filter_by(service=service).first()
    if not obj:
        obj = ConnectionSettings(service=service)
        db.add(obj)
        db.commit()
        db.refresh(obj)
    return obj


def _safe_url(url: str) -> str:
    return url.rstrip("/")


# ── Jellyfin endpoints ────────────────────────────────────────────────────────

@router.get("/jellyfin", response_model=ConnectionResponse)
def get_jellyfin(db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")

    # Return cached status if fresh
    cached = _cache_get("jellyfin")
    is_connected = cached["ok"] if cached else obj.is_connected
    last_tested = cached["last_tested"] if cached else obj.last_tested

    return ConnectionResponse(
        service="jellyfin",
        base_url=obj.base_url,
        is_connected=is_connected,
        last_tested=last_tested,
        has_api_key=bool(obj.api_key_encrypted),
    )


@router.post("/jellyfin")
def save_jellyfin(payload: ConnectionPayload, db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    obj.base_url = _safe_url(payload.base_url)
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False  # reset until next test
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("jellyfin")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/jellyfin/test")
async def test_jellyfin(db: Session = Depends(get_db)):
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin URL and API key must be saved first.")

    api_key = decrypt(obj.api_key_encrypted)
    url = f"{obj.base_url}/Users"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"X-Emby-Token": api_key})
        ok = resp.status_code == 200
    except Exception:
        ok = False

    obj.is_connected = ok
    obj.last_tested = datetime.utcnow()
    db.commit()

    # Update cache with fresh result
    _cache_put("jellyfin", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Jellyfin. Check the URL and API key.")
    return {"ok": True, "message": "Jellyfin connected successfully."}


@router.get("/jellyfin/users")
async def get_jellyfin_users(db: Session = Depends(get_db)):
    """Fetch all users from Jellyfin and return them with their managed status."""
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin not configured.")

    api_key = decrypt(obj.api_key_encrypted)
    url = f"{obj.base_url}/Users"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"X-Emby-Token": api_key})
        resp.raise_for_status()
        jf_users = resp.json()
    except Exception:
        raise HTTPException(502, "Failed to fetch users from Jellyfin.")

    # Get currently managed users
    managed = {u.jellyfin_user_id: u.is_enabled
               for u in db.query(ManagedUser).all()}

    return [
        {
            "jellyfin_user_id": u["Id"],
            "username": u["Name"],
            "is_enabled": managed.get(u["Id"], False),
        }
        for u in jf_users
    ]


@router.post("/jellyfin/users/toggle")
async def toggle_managed_user(payload: ManagedUserToggle, db: Session = Depends(get_db)):
    user = db.query(ManagedUser).filter_by(
        jellyfin_user_id=payload.jellyfin_user_id
    ).first()
    if not user:
        # Fetch the real username from Jellyfin immediately — never use ID as placeholder
        username = payload.username or payload.jellyfin_user_id  # frontend-provided name, fallback to ID
        try:
            obj = _get_or_create(db, "jellyfin")
            if obj.base_url and obj.api_key_encrypted:
                api_key = decrypt(obj.api_key_encrypted)
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(
                        f"{obj.base_url}/Users",
                        headers={"X-Emby-Token": api_key}
                    )
                if resp.status_code == 200:
                    jf_users = {u["Id"]: u["Name"] for u in resp.json()}
                    username = jf_users.get(payload.jellyfin_user_id, payload.jellyfin_user_id)
        except Exception:
            pass  # use fallback
        user = ManagedUser(
            jellyfin_user_id=payload.jellyfin_user_id,
            username=username,
        )
        db.add(user)
    user.is_enabled = payload.is_enabled
    db.commit()
    return {"ok": True}


@router.post("/jellyfin/users/sync")
async def sync_managed_user_names(db: Session = Depends(get_db)):
    """Pull fresh usernames from Jellyfin and update local records."""
    obj = _get_or_create(db, "jellyfin")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Jellyfin not configured.")

    api_key = decrypt(obj.api_key_encrypted)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{obj.base_url}/Users",
                headers={"X-Emby-Token": api_key}
            )
        resp.raise_for_status()
        jf_users = {u["Id"]: u["Name"] for u in resp.json()}
    except Exception:
        raise HTTPException(502, "Failed to fetch users from Jellyfin.")

    for managed in db.query(ManagedUser).all():
        if managed.jellyfin_user_id in jf_users:
            managed.username = jf_users[managed.jellyfin_user_id]
    db.commit()
    return {"ok": True}


# ── Lidarr endpoints ──────────────────────────────────────────────────────────

@router.get("/lidarr", response_model=ConnectionResponse)
def get_lidarr(db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")

    # Return cached status if fresh
    cached = _cache_get("lidarr")
    is_connected = cached["ok"] if cached else obj.is_connected
    last_tested = cached["last_tested"] if cached else obj.last_tested

    return ConnectionResponse(
        service="lidarr",
        base_url=obj.base_url,
        is_connected=is_connected,
        last_tested=last_tested,
        has_api_key=bool(obj.api_key_encrypted),
    )


@router.post("/lidarr")
def save_lidarr(payload: ConnectionPayload, db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    obj.base_url = _safe_url(payload.base_url)
    obj.api_key_encrypted = encrypt(payload.api_key)
    obj.is_connected = False
    obj.updated_at = datetime.utcnow()
    db.commit()
    _cache_invalidate("lidarr")   # credentials changed → flush cache
    return {"ok": True}


@router.post("/lidarr/test")
async def test_lidarr(db: Session = Depends(get_db)):
    obj = _get_or_create(db, "lidarr")
    if not obj.base_url or not obj.api_key_encrypted:
        raise HTTPException(400, "Lidarr URL and API key must be saved first.")

    api_key = decrypt(obj.api_key_encrypted)
    url = f"{obj.base_url}/api/v1/system/status"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"X-Api-Key": api_key})
        ok = resp.status_code == 200
    except Exception:
        ok = False

    obj.is_connected = ok
    obj.last_tested = datetime.utcnow()
    db.commit()

    # Update cache with fresh result
    _cache_put("lidarr", ok, obj.last_tested)

    if not ok:
        raise HTTPException(502, "Could not reach Lidarr. Check the URL and API key.")
    return {"ok": True, "message": "Lidarr connected successfully."}
