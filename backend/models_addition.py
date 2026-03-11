"""
backend/models.py — append this class before the final `if __name__ == "__main__":` block
or after the last existing model class.
"""

class DefaultPlaylistConfig(Base):
    """
    Admin-configured default playlists provisioned to every user automatically:
    on first login, on first push, or on-demand via the admin Connections panel.

    One row per default playlist slot. When a user is provisioned, a UserPlaylist
    is created for each active row the user doesn't already have (deduped by
    template_id). Users can rename, reschedule, or delete their provisioned
    playlists without affecting this config table.
    """
    __tablename__ = "default_playlist_configs"
    id                  = Column(Integer, primary_key=True, index=True)
    template_id         = Column(Integer, nullable=False)      # references PlaylistTemplate.id
    base_name           = Column(Text, nullable=False)         # playlist display name for users
    schedule_enabled    = Column(Boolean, default=True,  nullable=False)
    schedule_interval_h = Column(Integer, default=24,    nullable=False)
    position            = Column(Integer, default=0,     nullable=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# backend/main.py — THREE changes needed
# ─────────────────────────────────────────────────────────────────────────────

# CHANGE 1: Add to imports at the top of main.py (with other router imports):
#
#   from routers.admin_defaults import router as admin_defaults_router
#
# CHANGE 2: Register the router (after existing app.include_router calls):
#
#   app.include_router(admin_defaults_router)
#
# CHANGE 3: Auto-provision on first login.
#   Find the auth router login success handler (routers/auth.py) and add this
#   call after the user is authenticated and their ManagedUser row confirmed:
#
#   from routers.admin_defaults import provision_user_defaults
#   provision_user_defaults(user.jellyfin_user_id, db)
#
#   This is a no-op when no defaults are configured or the user already has
#   all default playlists, so it's safe to call on every login.
#
# NOTE: The default_playlist_configs table is brand-new and created by
# Base.metadata.create_all(bind=engine) in the lifespan handler.
# No ALTER TABLE migrations are needed for new tables.
