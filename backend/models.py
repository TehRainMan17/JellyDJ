
from database import Base
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime


class SystemEvent(Base):
    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConnectionSettings(Base):
    """Stores encrypted connection credentials for Jellyfin and Lidarr."""
    __tablename__ = "connection_settings"

    id = Column(Integer, primary_key=True, index=True)
    service = Column(String, unique=True, nullable=False)   # "jellyfin" | "lidarr"
    base_url = Column(String, nullable=False, default="")
    # API key is stored encrypted via Fernet
    api_key_encrypted = Column(String, nullable=False, default="")
    is_connected = Column(Boolean, default=False)
    last_tested = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ManagedUser(Base):
    """Jellyfin users that JellyDJ actively tracks."""
    __tablename__ = "managed_users"

    id = Column(Integer, primary_key=True, index=True)
    jellyfin_user_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    is_enabled = Column(Boolean, default=False)
    added_at = Column(DateTime, default=datetime.utcnow)


class ExternalApiSettings(Base):
    """Key-value store for external API credentials (encrypted)."""
    __tablename__ = "external_api_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)   # e.g. "spotify_client_id"
    value_encrypted = Column(String, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PopularityCache(Base):
    """24-hour cache for external API responses."""
    __tablename__ = "popularity_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, nullable=False, index=True)
    payload = Column(String, nullable=False)            # JSON blob
    expires_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Play(Base):
    """Individual track play record per user, synced from Jellyfin."""
    __tablename__ = "plays"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    album_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    play_count = Column(Integer, nullable=False, default=0)
    last_played = Column(DateTime, nullable=True)
    is_favorite = Column(Boolean, default=False)
    synced_at = Column(DateTime, default=datetime.utcnow)


class UserTasteProfile(Base):
    """Per-user affinity scores for artists and genres. Rebuilt on every index run."""
    __tablename__ = "user_taste_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=True)
    genre = Column(String, nullable=True)
    # Stored as float-compatible string to avoid SQLite decimal issues
    affinity_score = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)


class IndexerSettings(Base):
    """Stores configurable scheduler settings."""
    __tablename__ = "indexer_settings"

    id = Column(Integer, primary_key=True)
    index_interval_hours = Column(Integer, nullable=False, default=6)
    last_full_index = Column(DateTime, nullable=True)


class UserSyncStatus(Base):
    """Last successful sync time per user for the dashboard widget."""
    __tablename__ = "user_sync_status"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False, default="")
    last_synced = Column(DateTime, nullable=True)
    tracks_indexed = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="never")


class PlaybackEvent(Base):
    """
    Raw playback stop events received from Jellyfin webhooks.
    Stores position/runtime to calculate completion percentage.
    """
    __tablename__ = "playback_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    album_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    position_ticks = Column(Integer, nullable=False, default=0)   # where they stopped
    runtime_ticks = Column(Integer, nullable=False, default=0)    # total length
    completion_pct = Column(String, nullable=False, default="0.0")  # 0.0–1.0
    was_skip = Column(Boolean, nullable=False, default=False)     # completion < threshold
    received_at = Column(DateTime, default=datetime.utcnow, index=True)


class SkipPenalty(Base):
    """
    Aggregated skip penalty per user+item, updated on every webhook event.
    Used by the recommender to down-score frequently skipped tracks/artists/genres.
    """
    __tablename__ = "skip_penalties"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    total_events = Column(Integer, nullable=False, default=0)
    skip_count = Column(Integer, nullable=False, default=0)
    skip_rate = Column(String, nullable=False, default="0.0")  # skip_count/total_events
    penalty = Column(String, nullable=False, default="0.0")    # 0.0–1.0 penalty multiplier
    updated_at = Column(DateTime, default=datetime.utcnow)


class DiscoveryQueueItem(Base):
    """
    A recommended artist/album pending user approval before sending to Lidarr.
    Status flow: pending → approved | rejected | snoozed
    """
    __tablename__ = "discovery_queue"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False)
    album_name = Column(String, nullable=False, default="")
    release_year = Column(Integer, nullable=True)
    popularity_score = Column(String, nullable=False, default="0.0")
    image_url = Column(String, nullable=True)
    why = Column(String, nullable=False, default="")
    source_artist = Column(String, nullable=False, default="")
    source_affinity = Column(String, nullable=False, default="0.0")
    status = Column(String, nullable=False, default="pending", index=True)
    lidarr_sent = Column(Boolean, default=False)
    lidarr_response = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, index=True)
    actioned_at = Column(DateTime, nullable=True)
    # Auto-download targeting: user explicitly picked this as "getting this next"
    # or system selected it as top candidate. Can be cleared with "not that one".
    auto_queued = Column(Boolean, default=False, nullable=False)
    auto_skip = Column(Boolean, default=False, nullable=False)  # user said "not that one"


class PlaylistRun(Base):
    """One full generation run — all playlist types for all users."""
    __tablename__ = "playlist_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")   # running|ok|error
    playlist_types = Column(String, nullable=False, default="")  # comma-separated
    user_count = Column(Integer, nullable=False, default=0)
    playlists_written = Column(Integer, nullable=False, default=0)


class PlaylistRunItem(Base):
    """One playlist written in a run."""
    __tablename__ = "playlist_run_items"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    username = Column(String, nullable=False, default="")
    playlist_type = Column(String, nullable=False)
    playlist_name = Column(String, nullable=False)
    jellyfin_playlist_id = Column(String, nullable=False, default="")
    tracks_added = Column(Integer, nullable=False, default=0)
    action = Column(String, nullable=False, default="")   # created|overwritten
    status = Column(String, nullable=False, default="ok") # ok|error|no_tracks
    created_at = Column(DateTime, default=datetime.utcnow)


class LibraryTrack(Base):
    """
    Full Jellyfin library snapshot — every audio item, played or not.
    Library-wide (no user_id) — rescanned daily.
    Soft-deletes with missing_since when a track disappears from Jellyfin.
    """
    __tablename__ = "library_tracks"

    id = Column(Integer, primary_key=True, index=True)
    jellyfin_item_id = Column(String, unique=True, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="", index=True)
    album_name = Column(String, nullable=False, default="", index=True)
    album_artist = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    duration_ticks = Column(Integer, nullable=True)
    track_number = Column(Integer, nullable=True)
    disc_number = Column(Integer, nullable=True)
    year = Column(Integer, nullable=True)
    date_added = Column(DateTime, nullable=True)   # when Jellyfin got it
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    missing_since = Column(DateTime, nullable=True)   # soft-delete flag


class ArtistProfile(Base):
    """
    Per-user per-artist macro signals. Rebuilt on every index run.
    Aggregates plays + skips across all tracks by this artist.
    """
    __tablename__ = "artist_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, index=True)
    total_plays = Column(Integer, nullable=False, default=0)
    total_tracks_played = Column(Integer, nullable=False, default=0)
    total_skips = Column(Integer, nullable=False, default=0)
    skip_rate = Column(String, nullable=False, default="0.0")
    has_favorite = Column(Boolean, default=False)
    primary_genre = Column(String, nullable=False, default="")
    affinity_score = Column(String, nullable=False, default="0.0")   # 0–100
    updated_at = Column(DateTime, default=datetime.utcnow)


class GenreProfile(Base):
    """
    Per-user per-genre macro signals. Rebuilt on every index run.
    """
    __tablename__ = "genre_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    genre = Column(String, nullable=False, index=True)
    total_plays = Column(Integer, nullable=False, default=0)
    total_tracks_played = Column(Integer, nullable=False, default=0)
    total_skips = Column(Integer, nullable=False, default=0)
    skip_rate = Column(String, nullable=False, default="0.0")
    has_favorite = Column(Boolean, default=False)
    affinity_score = Column(String, nullable=False, default="0.0")   # 0–100
    updated_at = Column(DateTime, default=datetime.utcnow)


class TrackScore(Base):
    """
    Pre-computed per-user per-track composite score.
    Rebuilt on every index run. Playlist generation queries this directly
    instead of computing scores on the fly.
    """
    __tablename__ = "track_scores"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)

    # Denormalised for fast querying without joins
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    album_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")

    # Engagement signals (null = never played)
    play_count = Column(Integer, nullable=False, default=0)
    last_played = Column(DateTime, nullable=True)
    is_favorite = Column(Boolean, default=False)
    is_played = Column(Boolean, default=False)    # ever played at all

    # Component scores (all 0–100)
    play_score = Column(String, nullable=False, default="0.0")
    recency_score = Column(String, nullable=False, default="0.0")
    artist_affinity = Column(String, nullable=False, default="0.0")
    genre_affinity = Column(String, nullable=False, default="0.0")
    skip_penalty = Column(String, nullable=False, default="0.0")    # 0–1 multiplier
    novelty_bonus = Column(String, nullable=False, default="0.0")

    # Final pre-computed score
    final_score = Column(String, nullable=False, default="0.0", index=True)

    updated_at = Column(DateTime, default=datetime.utcnow)


class AutomationSettings(Base):
    """
    Configurable schedule for all automated tasks.
    Single row — one set of settings for the whole system.
    """
    __tablename__ = "automation_settings"

    id = Column(Integer, primary_key=True)

    # Play history + library scan + score rebuild
    index_interval_hours = Column(Integer, nullable=False, default=6)

    # Discovery queue: how often to run recommend_new_albums + populate queue
    discovery_refresh_enabled = Column(Boolean, default=True)
    discovery_refresh_interval_hours = Column(Integer, nullable=False, default=24)
    # Max items to add per run (rate-limits how aggressively it expands the queue)
    discovery_items_per_run = Column(Integer, nullable=False, default=10)

    # Playlist regeneration
    playlist_regen_enabled = Column(Boolean, default=True)
    playlist_regen_interval_hours = Column(Integer, nullable=False, default=24)

    # Auto-download: automatically send top-scored pending items to Lidarr
    # Master switch — when False, nothing is ever auto-sent regardless of other settings
    auto_download_enabled    = Column(Boolean, default=False)
    # Hard cap: max albums auto-sent per run (1–5, default 1 to be conservative)
    auto_download_max_per_run = Column(Integer, nullable=False, default=1)
    # Cooldown: minimum days between any two auto-downloads (1–30, default 7)
    auto_download_cooldown_days = Column(Integer, nullable=False, default=7)
    # Last time an auto-download was triggered (used to enforce cooldown)
    last_auto_download = Column(DateTime, nullable=True)

    # Last run times (informational)
    last_index = Column(DateTime, nullable=True)
    last_discovery_refresh = Column(DateTime, nullable=True)
    last_playlist_regen = Column(DateTime, nullable=True)
