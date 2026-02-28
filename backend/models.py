"""
JellyDJ — SQLAlchemy models (cumulative v1 + v2 + v3).

All tables and columns from every version are defined here.
New tables are created by create_all() on startup.
New columns on existing tables are added by _run_migrations() in main.py.
"""

from database import Base
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, Text, Index
from datetime import datetime


# ── Original v1 tables ────────────────────────────────────────────────────────

class SystemEvent(Base):
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConnectionSettings(Base):
    __tablename__ = "connection_settings"
    id = Column(Integer, primary_key=True, index=True)
    service = Column(String, unique=True, nullable=False)
    base_url = Column(String, nullable=False, default="")
    api_key_encrypted = Column(String, nullable=False, default="")
    is_connected = Column(Boolean, default=False)
    last_tested = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ManagedUser(Base):
    __tablename__ = "managed_users"
    id = Column(Integer, primary_key=True, index=True)
    jellyfin_user_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    is_enabled = Column(Boolean, default=False)
    added_at = Column(DateTime, default=datetime.utcnow)


class ExternalApiSettings(Base):
    __tablename__ = "external_api_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value_encrypted = Column(String, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PopularityCache(Base):
    __tablename__ = "popularity_cache"
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, nullable=False, index=True)
    payload = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Play(Base):
    """
    One row per (user, track). Updated in-place on every index run.

    v3: prev_played_1/2/3 track the last 3 play dates before last_played,
    giving a 4-point frequency window. Rotated by _upsert_play() in indexer.py
    whenever last_played advances.
    """
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
    # v3: play history window
    prev_played_1 = Column(DateTime, nullable=True)
    prev_played_2 = Column(DateTime, nullable=True)
    prev_played_3 = Column(DateTime, nullable=True)
    # v3: skip/cooldown signals
    total_skips = Column(Integer, nullable=False, default=0)
    consecutive_skips = Column(Integer, nullable=False, default=0)
    voluntary_play_count = Column(Integer, nullable=False, default=0)
    cooldown_until = Column(DateTime, nullable=True)
    cooldown_count = Column(Integer, nullable=False, default=0)


class UserTasteProfile(Base):
    __tablename__ = "user_taste_profile"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=True)
    genre = Column(String, nullable=True)
    affinity_score = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)


class IndexerSettings(Base):
    __tablename__ = "indexer_settings"
    id = Column(Integer, primary_key=True)
    index_interval_hours = Column(Integer, nullable=False, default=6)
    last_full_index = Column(DateTime, nullable=True)


class UserSyncStatus(Base):
    __tablename__ = "user_sync_status"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False, default="")
    last_synced = Column(DateTime, nullable=True)
    tracks_indexed = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="never")


class PlaybackEvent(Base):
    """
    Raw webhook events. Short-lived buffer — flushed each index cycle.
    v2: added source_context and session_id.
    """
    __tablename__ = "playback_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    album_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    position_ticks = Column(Integer, nullable=False, default=0)
    runtime_ticks = Column(Integer, nullable=False, default=0)
    completion_pct = Column(String, nullable=False, default="0.0")
    was_skip = Column(Boolean, nullable=False, default=False)
    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    # v2
    source_context = Column(String, nullable=True)
    session_id = Column(String, nullable=True, index=True)


class SkipPenalty(Base):
    """
    Aggregated skip penalty per user+item.
    v2: added consecutive_skips, skip_streak_peak, last_skip_at, last_completed_at.
    """
    __tablename__ = "skip_penalties"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    total_events = Column(Integer, nullable=False, default=0)
    skip_count = Column(Integer, nullable=False, default=0)
    skip_rate = Column(String, nullable=False, default="0.0")
    penalty = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)
    # v2
    consecutive_skips = Column(Integer, nullable=False, default=0)
    skip_streak_peak = Column(Integer, nullable=False, default=0)
    last_skip_at = Column(DateTime, nullable=True)
    last_completed_at = Column(DateTime, nullable=True)


class DiscoveryQueueItem(Base):
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
    auto_queued = Column(Boolean, default=False, nullable=False)
    auto_skip = Column(Boolean, default=False, nullable=False)


class PlaylistRun(Base):
    __tablename__ = "playlist_runs"
    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")
    playlist_types = Column(String, nullable=False, default="")
    user_count = Column(Integer, nullable=False, default=0)
    playlists_written = Column(Integer, nullable=False, default=0)


class PlaylistRunItem(Base):
    __tablename__ = "playlist_run_items"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    username = Column(String, nullable=False, default="")
    playlist_type = Column(String, nullable=False)
    playlist_name = Column(String, nullable=False)
    jellyfin_playlist_id = Column(String, nullable=False, default="")
    tracks_added = Column(Integer, nullable=False, default=0)
    action = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="ok")
    created_at = Column(DateTime, default=datetime.utcnow)


class LibraryTrack(Base):
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
    date_added = Column(DateTime, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    missing_since = Column(DateTime, nullable=True)
    # v2: enrichment
    mbid = Column(String, nullable=True)
    lastfm_url = Column(String, nullable=True)
    global_playcount = Column(Integer, nullable=True)
    global_listeners = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    enrichment_source = Column(String, nullable=True)


class ArtistProfile(Base):
    """
    Per-user per-artist signals. Rebuilt on every index.
    v2: added replay_boost, related_artists, tags.
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
    affinity_score = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)
    # v2
    replay_boost = Column(Float, nullable=True, default=0.0)
    related_artists = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)


class GenreProfile(Base):
    __tablename__ = "genre_profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    genre = Column(String, nullable=False, index=True)
    total_plays = Column(Integer, nullable=False, default=0)
    total_tracks_played = Column(Integer, nullable=False, default=0)
    total_skips = Column(Integer, nullable=False, default=0)
    skip_rate = Column(String, nullable=False, default="0.0")
    has_favorite = Column(Boolean, default=False)
    affinity_score = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)


class TrackScore(Base):
    """
    Pre-computed per-user per-track composite score.
    v2: added cooldown_until, replay_boost, global_popularity, skip_streak.
    """
    __tablename__ = "track_scores"
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
    is_played = Column(Boolean, default=False)
    play_score = Column(String, nullable=False, default="0.0")
    recency_score = Column(String, nullable=False, default="0.0")
    artist_affinity = Column(String, nullable=False, default="0.0")
    genre_affinity = Column(String, nullable=False, default="0.0")
    skip_penalty = Column(String, nullable=False, default="0.0")
    novelty_bonus = Column(String, nullable=False, default="0.0")
    final_score = Column(String, nullable=False, default="0.0", index=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
    # v2
    cooldown_until = Column(DateTime, nullable=True)
    replay_boost = Column(Float, nullable=True, default=0.0)
    global_popularity = Column(Float, nullable=True)
    skip_streak = Column(Integer, nullable=True, default=0)


class AutomationSettings(Base):
    __tablename__ = "automation_settings"
    id = Column(Integer, primary_key=True)
    index_interval_hours = Column(Integer, nullable=False, default=6)
    discovery_refresh_enabled = Column(Boolean, default=True)
    discovery_refresh_interval_hours = Column(Integer, nullable=False, default=24)
    discovery_items_per_run = Column(Integer, nullable=False, default=10)
    playlist_regen_enabled = Column(Boolean, default=True)
    playlist_regen_interval_hours = Column(Integer, nullable=False, default=24)
    auto_download_enabled = Column(Boolean, default=False)
    auto_download_max_per_run = Column(Integer, nullable=False, default=1)
    auto_download_cooldown_days = Column(Integer, nullable=False, default=7)
    last_auto_download = Column(DateTime, nullable=True)
    last_index = Column(DateTime, nullable=True)
    last_discovery_refresh = Column(DateTime, nullable=True)
    last_playlist_regen = Column(DateTime, nullable=True)
    # v2
    enrichment_enabled = Column(Boolean, default=True)
    enrichment_interval_hours = Column(Integer, nullable=False, default=48)
    last_enrichment = Column(DateTime, nullable=True)
    # v3
    billboard_refresh_enabled = Column(Boolean, default=True)
    billboard_refresh_interval_hours = Column(Integer, nullable=False, default=168)
    last_billboard_refresh = Column(DateTime, nullable=True)


# ── v2: new tables (created by create_all, never existed before) ──────────────

class TrackEnrichment(Base):
    """Per-track Last.fm / MusicBrainz metadata. One row per library track."""
    __tablename__ = "track_enrichments"
    id = Column(Integer, primary_key=True, index=True)
    jellyfin_item_id = Column(String, unique=True, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    mbid = Column(String, nullable=True)
    lastfm_url = Column(String, nullable=True)
    global_playcount = Column(Integer, nullable=True)
    global_listeners = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)            # JSON list of tag strings
    similar_tracks = Column(Text, nullable=True)  # JSON list of {title, artist}
    popularity_score = Column(Float, nullable=True)  # 0–100 log-normalised
    enriched_at = Column(DateTime, nullable=True)
    enrichment_source = Column(String, nullable=True)


class ArtistEnrichment(Base):
    """Per-artist Last.fm / MusicBrainz metadata. One row per unique artist."""
    __tablename__ = "artist_enrichments"
    id = Column(Integer, primary_key=True, index=True)
    artist_name = Column(String, nullable=False, index=True)
    artist_name_lower = Column(String, nullable=False, unique=True, index=True)
    mbid = Column(String, nullable=True)
    lastfm_url = Column(String, nullable=True)
    biography = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    global_listeners = Column(Integer, nullable=True)
    global_playcount = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)            # JSON list
    similar_artists = Column(Text, nullable=True) # JSON list of {name, match}
    popularity_score = Column(Float, nullable=True)
    trend_direction = Column(String, nullable=True)  # "rising"|"falling"|"stable"
    trend_pct = Column(Float, nullable=True)
    enriched_at = Column(DateTime, nullable=True)


class ArtistRelation(Base):
    """
    Edge table for the artist similarity network graph.
    One row per (artist_a → artist_b) pair from Last.fm similar-artists.
    """
    __tablename__ = "artist_relations"
    id = Column(Integer, primary_key=True, index=True)
    artist_a = Column(String, nullable=False, index=True)
    artist_b = Column(String, nullable=False, index=True)
    match_score = Column(Float, nullable=False, default=0.0)  # 0–1 Last.fm similarity
    source = Column(String, nullable=False, default="lastfm")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_artist_relations_pair", "artist_a", "artist_b", unique=True),
    )


class UserReplaySignal(Base):
    """
    Materialized voluntary replays within 7 days — high-value preference signal.
    One row per detected replay event. Small table: only fires when a user
    genuinely seeks out a track or artist within a week of a previous play.
    """
    __tablename__ = "user_replay_signals"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, default="")
    signal_type = Column(String, nullable=False)  # "track_return"|"artist_return"|"session_return"
    days_between = Column(Float, nullable=True)
    seed_was_playlist = Column(Boolean, default=False)
    boost_applied = Column(Float, nullable=False, default=0.0)
    replay_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrackCooldown(Base):
    """
    Per-user per-track cooldown state. Created when a skip streak exceeds 3.
    Status: "active" | "expired" | "permanent"
    """
    __tablename__ = "track_cooldowns"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="active", index=True)
    cooldown_until = Column(DateTime, nullable=True)
    cooldown_count = Column(Integer, nullable=False, default=1)
    skip_streak_at_trigger = Column(Integer, nullable=False, default=0)
    cooldown_started_at = Column(DateTime, default=datetime.utcnow)
    expired_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_track_cooldowns_user_item_status", "user_id", "jellyfin_item_id", "status"),
    )


# ── v3: new tables ────────────────────────────────────────────────────────────

class BillboardChartEntry(Base):
    """
    Snapshot of the Billboard Hot 100. Always exactly 100 rows — replaced
    wholesale on each weekly refresh. No historical rows accumulate.
    Shared across all users (the chart is global).
    """
    __tablename__ = "billboard_chart_entries"
    id = Column(Integer, primary_key=True, index=True)
    rank = Column(Integer, nullable=False, index=True)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    chart_score = Column(Float, nullable=False, default=0.0)  # rank 1→100, rank 100→1
    weeks_on_chart = Column(Integer, nullable=True)
    peak_position = Column(Integer, nullable=True)
    last_week_position = Column(Integer, nullable=True)
    jellyfin_item_id = Column(String, nullable=True, index=True)  # null if not in library
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    chart_date = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_billboard_rank_fetched", "rank", "fetched_at"),
    )
