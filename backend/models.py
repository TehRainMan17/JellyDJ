"""
JellyDJ — SQLAlchemy models (cumulative v1 + v2 + v3 + Auth Phase 1 + Phase 3).

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
    # v8: optional public URL returned to the browser for deep-links only.
    # Never used for server-side requests — no SSRF validation needed.
    public_url = Column(String, nullable=True, default="")


class ManagedUser(Base):
    __tablename__ = "managed_users"
    id = Column(Integer, primary_key=True, index=True)
    jellyfin_user_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    is_enabled = Column(Boolean, default=False)   # legacy — kept for zero-downtime; use has_activated
    has_activated = Column(Boolean, default=False, nullable=False)  # True once first playlist pushed
    added_at = Column(DateTime, default=datetime.utcnow)
    # Auth Phase 1: Jellyfin login integration
    is_admin = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime, nullable=True)


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
    # Jellyfin file-tag genre (Genres[0] from Jellyfin API). Kept as a historical
    # record. DO NOT use for genre analytics or feature implementation — it reflects
    # whatever was tagged in the music file (often inaccurate, e.g., Ludacris as "Pop").
    # Use ArtistProfile.primary_genre or ArtistProfile.canonical_genres instead.
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
    # Jellyfin file-tag genre — inherited from Play.genre at skip time. Historical only.
    # Skip signals are spread across canonical genres during GenreProfile rebuild (v9).
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
    # Phase 3: playlist template system
    user_playlist_id = Column(Integer, nullable=True)


class LibraryTrack(Base):
    __tablename__ = "library_tracks"
    id = Column(Integer, primary_key=True, index=True)
    jellyfin_item_id = Column(String, unique=True, nullable=False, index=True)
    track_name = Column(String, nullable=False, default="")
    artist_name = Column(String, nullable=False, default="", index=True)
    album_name = Column(String, nullable=False, default="", index=True)
    album_artist = Column(String, nullable=False, default="")
    # Jellyfin file-tag genre (Genres[0] from Jellyfin API). Kept as a historical
    # record of what the music file declared. DO NOT use for genre analytics or feature
    # implementation — accuracy is poor (file taggers frequently default to "Pop" or "Rock").
    # Use ArtistProfile.primary_genre or ArtistProfile.canonical_genres instead.
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
    # v4: holiday tagging
    holiday_tag     = Column(String,  nullable=True)
    holiday_exclude = Column(Boolean, nullable=False, default=False)
    # v5: Jellyfin album container ID
    jellyfin_album_id = Column(String, nullable=True, index=True)
    # v6: Jellyfin artist item ID
    jellyfin_artist_id = Column(String, nullable=True, index=True)

    # Aliases used by playlist_import service
    @property
    def name(self):
        return self.track_name

    @property
    def artist(self):
        return self.artist_name


class ArtistProfile(Base):
    """
    Per-user per-artist signals. Rebuilt on every index.
    v2: added replay_boost, related_artists, tags.
    v9: canonical genre system.

    GENRE DATA GUIDANCE — which columns to use for feature implementation:

      USE FOR GENRE FEATURES:
        primary_genre   — dominant canonical genre, normalized (lowercase, spaces).
                          Sourced from Last.fm artist tags as of v9. This is the
                          authoritative genre signal for playlist blocks, scoring,
                          discovery, and insights. Falls back to Jellyfin file-tag
                          genre only when the artist has no Last.fm enrichment.
        canonical_genres — weighted multi-genre JSON: [{"genre": str, "weight": float}, ...]
                          Weights decay by Last.fm tag position (50/25/14/7/4%).
                          Use this when you need the full genre blend (e.g., Ludacris:
                          hip-hop 50%, r&b 25%, rap 14%).  GenreProfile is built from
                          these fractional weights, so users accumulate genre affinity
                          proportionally across all an artist's genres.

      DO NOT USE FOR GENRE FEATURES:
        tags            — raw Last.fm tags JSON (string list, not weighted, not
                          filtered for junk). Used to derive canonical_genres but
                          not authoritative on its own. Includes non-genre strings
                          like "seen live", "favorites", "2000s".
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
    # v9: repurposed — now stores the dominant CANONICAL genre (normalized, Last.fm-sourced).
    # Previously stored the most-played Jellyfin file-tag genre (unreliable).
    # Always use this column, never Play.genre or LibraryTrack.genre, for genre analytics.
    primary_genre = Column(String, nullable=False, default="")
    affinity_score = Column(String, nullable=False, default="0.0")
    updated_at = Column(DateTime, default=datetime.utcnow)
    # v2
    replay_boost = Column(Float, nullable=True, default=0.0)
    related_artists = Column(Text, nullable=True)
    # Raw Last.fm tags (unweighted, unfiltered). See docstring — prefer canonical_genres.
    tags = Column(Text, nullable=True)
    # v9: weighted canonical genre list — JSON [{genre, weight}, ...].
    # The authoritative multi-genre representation for this artist. Built from Last.fm
    # tags with position-decay weighting, junk-tag filtering, and GENRE_ADJACENCY
    # validation. All genre-sensitive features should derive from this column.
    canonical_genres = Column(Text, nullable=True)


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
    # v9: canonical genre (normalized, Last.fm-sourced) for this track.
    # Populated from ArtistProfile.primary_genre during rebuild_track_scores().
    # This is the genre used by all playlist blocks, insights, and the galaxy.
    # Safe to filter and query against — it aligns with GenreProfile.genre.
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
    # v4: holiday tagging
    holiday_tag     = Column(String,  nullable=True)
    holiday_exclude = Column(Boolean, nullable=False, default=False)
    # v12: per-artist catalog popularity (0–100).
    # 100 = this track is the artist's #1 most-listened song on Last.fm.
    # Score is proportional: (track_listeners / artist_top_track_listeners) * 100.
    # NULL = artist has no Last.fm enrichment or track not found in artist top-10.
    artist_catalog_popularity = Column(Float, nullable=True)


class AutomationSettings(Base):
    __tablename__ = "automation_settings"
    id = Column(Integer, primary_key=True)
    index_interval_hours = Column(Integer, nullable=False, default=6)
    discovery_refresh_enabled = Column(Boolean, default=True)
    discovery_refresh_interval_hours = Column(Integer, nullable=False, default=24)
    discovery_items_per_run = Column(Integer, nullable=False, default=10)
    auto_download_enabled = Column(Boolean, default=False)
    auto_download_max_per_run = Column(Integer, nullable=False, default=1)
    auto_download_cooldown_days = Column(Integer, nullable=False, default=7)
    last_auto_download = Column(DateTime, nullable=True)
    last_index = Column(DateTime, nullable=True)
    last_discovery_refresh = Column(DateTime, nullable=True)
    # v2
    enrichment_enabled = Column(Boolean, default=True)
    enrichment_interval_hours = Column(Integer, nullable=False, default=48)
    last_enrichment = Column(DateTime, nullable=True)
    # v3
    billboard_refresh_enabled = Column(Boolean, default=True)
    billboard_refresh_interval_hours = Column(Integer, nullable=False, default=168)
    last_billboard_refresh = Column(DateTime, nullable=True)
    # v7: popularity cache refresh schedule
    popularity_cache_refresh_interval_hours = Column(Integer, nullable=False, default=24)
    last_popularity_cache_refresh = Column(DateTime, nullable=True)


# ── v2: new tables ────────────────────────────────────────────────────────────

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
    tags = Column(Text, nullable=True)
    similar_tracks = Column(Text, nullable=True)
    popularity_score = Column(Float, nullable=True)
    album_name = Column(String, nullable=True, default="")
    source = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
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
    tags = Column(Text, nullable=True)
    similar_artists = Column(Text, nullable=True)
    popularity_score = Column(Float, nullable=True)
    trend_direction = Column(String, nullable=True)
    trend_pct = Column(Float, nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    source = Column(String, nullable=True)
    listeners_previous = Column(Integer, nullable=True)
    top_tracks = Column(Text, nullable=True)


class ArtistRelation(Base):
    """Edge table for the artist similarity network graph."""
    __tablename__ = "artist_relations"
    id = Column(Integer, primary_key=True, index=True)
    artist_a = Column(String, nullable=False, index=True)
    artist_b = Column(String, nullable=False, index=True)
    match_score = Column(Float, nullable=False, default=0.0)
    source = Column(String, nullable=False, default="lastfm")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_artist_relations_pair", "artist_a", "artist_b", unique=True),
    )


class UserReplaySignal(Base):
    __tablename__ = "user_replay_signals"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    jellyfin_item_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, default="")
    signal_type = Column(String, nullable=False)
    first_play_at = Column(DateTime, nullable=True)
    days_between = Column(Float, nullable=True)
    seed_was_playlist = Column(Boolean, default=False)
    boost_applied = Column(Float, nullable=False, default=0.0)
    replay_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrackCooldown(Base):
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


class ArtistCooldown(Base):
    """
    Artist-level skip timeout.

    Triggered when a user skips ARTIST_COOLDOWN_SKIP_THRESHOLD or more distinct
    tracks by the same artist within a rolling ARTIST_COOLDOWN_WINDOW_DAYS window.

    While status='active' and cooldown_until is in the future, all tracks by
    this artist are excluded from playlist generation for this user.

    Cooldown durations escalate across cycles: 7d → 14d → 30d (then stays at 30d).
    """
    __tablename__ = "artist_cooldowns"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="active", index=True)  # active | expired
    cooldown_until = Column(DateTime, nullable=True)
    cooldown_count = Column(Integer, nullable=False, default=1)
    skip_count_at_trigger = Column(Integer, nullable=False, default=0)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    expired_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_artist_cooldowns_user_artist_status", "user_id", "artist_name", "status"),
    )


# ── v3: new tables ────────────────────────────────────────────────────────────

class BillboardChartEntry(Base):
    __tablename__ = "billboard_chart_entries"
    id = Column(Integer, primary_key=True, index=True)
    rank = Column(Integer, nullable=False, index=True)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    chart_score = Column(Float, nullable=False, default=0.0)
    weeks_on_chart = Column(Integer, nullable=True)
    peak_position = Column(Integer, nullable=True)
    last_week_position = Column(Integer, nullable=True)
    jellyfin_item_id = Column(String, nullable=True, index=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    chart_date = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_billboard_rank_fetched", "rank", "fetched_at"),
    )


# ── Auth Phase 1: refresh token store ────────────────────────────────────────

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id             = Column(Integer, primary_key=True, index=True)
    token_hash     = Column(String, unique=True, nullable=False, index=True)
    user_id        = Column(String, nullable=False, index=True)
    jellyfin_token = Column(Text, nullable=False)
    expires_at     = Column(DateTime, nullable=False)
    created_at     = Column(DateTime, default=datetime.utcnow)
    last_used_at   = Column(DateTime, nullable=True)


# ── v4: manual album exclusions ───────────────────────────────────────────────

class ExcludedAlbum(Base):
    __tablename__ = "excluded_albums"
    id                = Column(Integer, primary_key=True, index=True)
    jellyfin_album_id = Column(String, unique=True, nullable=False, index=True)
    album_name        = Column(String, nullable=False, default="")
    artist_name       = Column(String, nullable=False, default="")
    reason            = Column(String, nullable=True)
    cover_image_url   = Column(String, nullable=True)
    excluded_at       = Column(DateTime, default=datetime.utcnow)
    track_count       = Column(Integer, nullable=False, default=0)


# ── Phase 3: playlist template system ────────────────────────────────────────

class PlaylistTemplate(Base):
    __tablename__ = "playlist_templates"
    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(Text, nullable=False)
    description    = Column(Text, nullable=True)
    owner_user_id  = Column(Text, nullable=True)
    is_public      = Column(Boolean, default=True)
    is_system      = Column(Boolean, default=False)
    forked_from_id = Column(Integer, nullable=True)
    total_tracks   = Column(Integer, default=50)
    blend_mode     = Column(Text, default="weighted_shuffle")
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlaylistBlock(Base):
    __tablename__ = "playlist_blocks"
    id          = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, nullable=False)
    block_type  = Column(Text, nullable=False)
    weight      = Column(Integer, nullable=False)
    position    = Column(Integer, nullable=False)
    params      = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserPlaylist(Base):
    __tablename__ = "user_playlists"
    id                   = Column(Integer, primary_key=True, index=True)
    owner_user_id        = Column(Text, nullable=False)
    template_id          = Column(Integer, nullable=True)
    base_name            = Column(Text, nullable=False)
    schedule_enabled     = Column(Boolean, default=False)
    schedule_interval_h  = Column(Integer, default=24)
    last_generated_at    = Column(DateTime, nullable=True)
    last_track_count     = Column(Integer, nullable=True)
    jellyfin_playlist_id = Column(String, nullable=False, default="")
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LoginRateLimit(Base):
    __tablename__ = "login_rate_limits"
    ip            = Column(Text, primary_key=True)
    window_start  = Column(DateTime, nullable=False, default=datetime.utcnow)
    attempt_count = Column(Integer,  nullable=False, default=1)


class DefaultPlaylistConfig(Base):
    __tablename__ = "default_playlist_configs"
    id                  = Column(Integer, primary_key=True, index=True)
    template_id         = Column(Integer, nullable=False)
    base_name           = Column(Text, nullable=False)
    schedule_enabled    = Column(Boolean, default=True,  nullable=False)
    schedule_interval_h = Column(Integer, default=24,    nullable=False)
    position            = Column(Integer, default=0,     nullable=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class JobState(Base):
    __tablename__ = "job_state"
    id         = Column(Integer, primary_key=True, index=True)
    job_id     = Column(String, unique=True, nullable=False, index=True)
    running    = Column(Boolean, default=False, nullable=False)
    phase      = Column(String, nullable=True)
    payload    = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at= Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Playlist Backup feature ───────────────────────────────────────────────────

class PlaylistBackup(Base):
    """
    One row per tracked playlist — the stable parent record.

    Stores metadata about the playlist and user preferences (display_name,
    exclude_from_auto). The actual track data lives in PlaylistBackupRevision
    and PlaylistBackupTrack, keeping up to max_revisions rolling snapshots.

    Fields
    ──────
    jellyfin_playlist_id   — Jellyfin item ID of the source playlist
    jellyfin_playlist_name — most recent name seen in Jellyfin
    display_name           — optional override name used on restore;
                             defaults to jellyfin_playlist_name when None
    exclude_from_auto      — when True, the scheduler skips this playlist;
                             only a manual "Re-backup now" press updates it
    max_revisions          — how many rolling revisions to keep (default 6)
    created_at             — when this backup record was first created
    """
    __tablename__ = "playlist_backups"
    id                     = Column(Integer, primary_key=True, index=True)
    jellyfin_playlist_id   = Column(String, unique=True, nullable=False, index=True)
    jellyfin_playlist_name = Column(String, nullable=False, default="")
    display_name           = Column(String, nullable=True)
    exclude_from_auto      = Column(Boolean, nullable=False, default=False)
    max_revisions          = Column(Integer, nullable=False, default=6)
    created_at             = Column(DateTime, default=datetime.utcnow)


class PlaylistBackupRevision(Base):
    """
    One snapshot of a playlist's track list at a point in time.

    Multiple revisions exist per PlaylistBackup, up to backup.max_revisions.
    When a new backup is written and the limit is exceeded, the oldest
    revision (lowest revision_number) is deleted along with its tracks.

    Fields
    ──────
    backup_id       — parent PlaylistBackup.id
    revision_number — 1-based counter, increments with each new snapshot;
                      the highest number is always the most recent
    track_count     — cached count of tracks in this revision
    backed_up_at    — UTC timestamp when this snapshot was taken
    label           — optional human label (e.g. "before holiday shuffle");
                      labeled revisions are never auto-pruned
    """
    __tablename__ = "playlist_backup_revisions"
    id              = Column(Integer, primary_key=True, index=True)
    backup_id       = Column(Integer, nullable=False, index=True)   # → PlaylistBackup.id
    revision_number = Column(Integer, nullable=False)
    track_count     = Column(Integer, nullable=False, default=0)
    backed_up_at    = Column(DateTime, nullable=False, default=datetime.utcnow)
    label           = Column(String, nullable=True)   # None = auto-generated label

    __table_args__ = (
        Index("ix_backup_revisions_backup_rev", "backup_id", "revision_number"),
    )


class PlaylistBackupTrack(Base):
    """
    One row per track within a PlaylistBackupRevision.

    Indexed by revision_id (not backup_id) so each revision's track list
    is fully independent — restoring an old revision doesn't touch newer ones.
    """
    __tablename__ = "playlist_backup_tracks"
    id               = Column(Integer, primary_key=True, index=True)
    # backup_id is the original column (NOT NULL in old installs). We keep writing
    # it so the old constraint is always satisfied regardless of install age.
    # On fresh installs the migration adds it nullable; old installs have NOT NULL.
    backup_id        = Column(Integer, nullable=True, index=True)   # → PlaylistBackup.id (legacy compat)
    revision_id      = Column(Integer, nullable=True, index=True)   # → PlaylistBackupRevision.id
    position         = Column(Integer, nullable=False, default=0)
    jellyfin_item_id = Column(String, nullable=False)
    track_name       = Column(String, nullable=False, default="")
    artist_name      = Column(String, nullable=False, default="")
    album_name       = Column(String, nullable=False, default="")


class PlaylistBackupSettings(Base):
    """
    Singleton settings row (id=1) for the playlist backup scheduler.

    auto_backup_enabled          — master switch for the scheduled job
    auto_backup_interval_hours   — how often the job runs (default: 24 h)
    last_auto_backup_at          — UTC timestamp of the most recent auto-run
    """
    __tablename__ = "playlist_backup_settings"
    id                         = Column(Integer, primary_key=True)
    auto_backup_enabled        = Column(Boolean, nullable=False, default=True)
    auto_backup_interval_hours = Column(Integer, nullable=False, default=24)
    last_auto_backup_at        = Column(DateTime, nullable=True)


# ── Playlist Import feature ───────────────────────────────────────────────────

class ImportedPlaylist(Base):
    """
    One row per playlist imported from an external platform (Spotify/Tidal/YT Music).

    source_platform:  'spotify' | 'tidal' | 'youtube_music' | 'unknown'
    source_url:       original URL stored for display; never used for outbound requests
    name:             playlist title as returned by the scrape / yt-dlp
    track_count:      total tracks in the source playlist
    matched_count:    tracks found in local Jellyfin library
    jellyfin_playlist_id: the Jellyfin playlist this maps to (set once created)
    status:           'pending' | 'active' | 'error' | 'archived'
    """
    __tablename__ = "imported_playlists"
    id                    = Column(Integer, primary_key=True, index=True)
    owner_user_id         = Column(String, nullable=False, index=True)
    source_platform       = Column(String, nullable=False, default="unknown")
    source_url            = Column(String, nullable=False, default="")
    source_id             = Column(String, nullable=False, default="", index=True)
    name                  = Column(String, nullable=False, default="Imported Playlist")
    description           = Column(Text, nullable=True)
    track_count           = Column(Integer, nullable=False, default=0)
    matched_count         = Column(Integer, nullable=False, default=0)
    jellyfin_playlist_id  = Column(String, nullable=True)
    status                = Column(String, nullable=False, default="pending")
    created_at            = Column(DateTime, default=datetime.utcnow)
    last_sync_at          = Column(DateTime, nullable=True)


class ImportedPlaylistTrack(Base):
    """
    One row per track in an imported playlist.

    match_status: 'matched' | 'missing' | 'skipped'
    matched_item_id: LibraryTrack.jellyfin_item_id when matched
    suggested_album / suggested_artist: best guess for Lidarr fetch
    lidarr_requested: True once user has sent the album to Lidarr
    added_to_playlist: True once the track exists in the Jellyfin playlist
    """
    __tablename__ = "imported_playlist_tracks"
    id                  = Column(Integer, primary_key=True, index=True)
    playlist_id         = Column(Integer, nullable=False, index=True)
    position            = Column(Integer, nullable=False, default=0)
    track_name          = Column(String, nullable=False, default="")
    artist_name         = Column(String, nullable=False, default="")
    album_name          = Column(String, nullable=False, default="")
    duration_ms         = Column(Integer, nullable=True)
    match_status        = Column(String, nullable=False, default="missing")
    match_score         = Column(Float, nullable=True)
    matched_item_id     = Column(String, nullable=True)
    suggested_album     = Column(String, nullable=True)
    suggested_artist    = Column(String, nullable=True)
    lidarr_requested    = Column(Boolean, default=False)
    added_to_playlist   = Column(Boolean, default=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    resolved_at         = Column(DateTime, nullable=True)


class ImportAlbumSuggestion(Base):
    """
    Groups missing ImportedPlaylistTrack rows by the best single album to fetch
    so that one Lidarr request fills as many gaps as possible.

    coverage_count: how many tracks this album would resolve
    lidarr_status: 'pending' | 'approved' | 'rejected' | 'downloading' | 'complete'
    """
    __tablename__ = "import_album_suggestions"
    id               = Column(Integer, primary_key=True, index=True)
    playlist_id      = Column(Integer, nullable=False, index=True)
    artist_name      = Column(String, nullable=False, default="")
    album_name       = Column(String, nullable=False, default="")
    coverage_count   = Column(Integer, nullable=False, default=1)
    lidarr_status    = Column(String, nullable=False, default="pending")
    lidarr_queued_at = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    # v2: enriched album suggestion metadata
    artist_mbid      = Column(String, nullable=True)
    album_mbid       = Column(String, nullable=True)
    image_url        = Column(String, nullable=True)
    missing_tracks   = Column(Text, nullable=True)   # JSON list of track names


class ImportAPIKey(Base):
    """
    Per-user API keys for the browser extension.

    The full key is never stored — only a SHA-256 hash.
    key_prefix stores the first 8 chars for masked display ("jdj_a3f2…").
    One active key per user is the expected UX; the table allows multiple
    so that reroll is atomic (new key created before old one is deactivated).
    """
    __tablename__ = "import_api_keys"
    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(String, nullable=False, index=True)
    key_hash     = Column(String, unique=True, nullable=False, index=True)
    key_prefix   = Column(String, nullable=False, default="")
    label        = Column(String, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    is_active    = Column(Boolean, nullable=False, default=True)
