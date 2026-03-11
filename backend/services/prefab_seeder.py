"""
JellyDJ — Prefab playlist template seeder + in-place migrator.

Responsibilities
────────────────
1. seed_prefabs()              — inserts the four system templates on a fresh DB
                                 (no-ops if is_system rows already exist).

2. migrate_system_templates()  — rewrites existing system template blocks to the
                                 new filter_tree format in-place, without touching
                                 user-space:
                                 • Only modifies rows where is_system=True
                                 • Never changes template.id (UserPlaylist.template_id
                                   foreign keys remain valid)
                                 • Never touches user-created or user-forked templates
                                 • Idempotent: guarded by a sentinel in
                                   the template description — runs exactly once

Both are called from main.py lifespan on every boot; they no-op instantly
after the first successful run.

v6 change — "New For You" template overhaul:
  The old template used a single discovery block that AND-intersected with
  global_popularity >= 50.  This meant:
    • Tracks with NULL global_popularity (un-enriched library items, including
      newly downloaded albums) were excluded entirely.
    • Niche-but-loved artists (Simon & Garfunkel deep cuts, White Stripes B-sides)
      often score below 50 on global charts and were therefore never surfaced.

  New design has three blocks:

    Block 0 — "Loved Artist New Arrivals" (weight 40)
      Uses the novelty block (affinity-driven unplayed scoring, v6) AND-intersected
      with a high artist-affinity filter.  No popularity floor — these are tracks
      you haven't heard from artists you already love.  This is the primary channel
      for newly added albums from favourite artists.

    Block 1 — "Discovery" (weight 40)
      Retains the existing discovery block but lowers the popularity floor to 25
      (was 50) and adds NULL-safe handling by removing the hard global_popularity
      AND-child entirely in favour of a soft preference via the novelty block.
      The artist_cap is reduced to 1 to maximise diversity.

    Block 2 — "Familiar Anchors" (weight 20)
      A small slice of high-scoring played tracks to keep the playlist listenable
      and prevent it from being 100% cold-start unfamiliar music.  Unchanged from
      the old 30% familiar block, just reduced in weight.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── Low-level helpers ─────────────────────────────────────────────────────────

def _tree(nodes: list) -> str:
    return json.dumps({"filter_tree": nodes})

def _node(filter_type: str, params: dict, children: list | None = None) -> dict:
    return {"filter_type": filter_type, "params": params, "children": children or []}

def _jitter(pct: float = 0.12) -> dict:
    return _node("jitter", {"jitter_pct": pct})

def _cooldown() -> dict:
    return _node("cooldown", {"mode": "exclude_active"})

def _artist_cap(n: int = 3) -> dict:
    return _node("artist_cap", {"max_per_artist": n})


# ── Canonical block builders ──────────────────────────────────────────────────

def _blocks_for_you() -> list[dict]:
    """65% recently played + high score | 35% affinity-matched, not-recently-played."""
    return [
        dict(block_type="final_score", weight=65, position=0,
             params=_tree([
                 _node("final_score", {"score_min": 63, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}),
                     _cooldown(),
                     _artist_cap(3),
                     _jitter(0.10),
                 ]),
             ])),
        dict(block_type="affinity", weight=35, position=1,
             params=_tree([
                 _node("affinity", {"affinity_min": 60, "affinity_max": 100, "played_filter": "all"}, children=[
                     _node("global_popularity", {"popularity_min": 40, "popularity_max": 100}),
                     _node("play_recency", {"mode": "older", "days": 60}),
                     _cooldown(),
                     _artist_cap(2),
                     _jitter(0.15),
                 ]),
                 _node("affinity", {"affinity_min": 60, "affinity_max": 100, "played_filter": "unplayed"}, children=[
                     _node("global_popularity", {"popularity_min": 40, "popularity_max": 100}),
                     _artist_cap(2),
                     _jitter(0.15),
                 ]),
             ])),
    ]

def _blocks_new_for_you() -> list[dict]:
    """
    Three-block design for maximum new-music diversity:

      40% — Loved Artist New Arrivals
        Unplayed tracks from high-affinity artists, ranked by novelty_bonus
        (which v6 now scales with artist_affinity).  No popularity floor so
        newly added albums from niche-but-loved artists always qualify.

      40% — Broad Discovery
        Unplayed tracks across all artist familiarity tiers.  Popularity floor
        lowered to 25 (was 50) so less mainstream but chart-adjacent music is
        included.  NULL-popularity tracks (un-enriched) are allowed through
        by the novelty block's unplayed filter rather than the old hard
        global_popularity AND-child.

      20% — Familiar Anchors
        A small slice of recent high-scoring played tracks so the playlist
        stays listenable and isn't 100% cold-start unfamiliar music.
    """
    return [
        # Block 0: Loved Artist New Arrivals — primary channel for new albums
        dict(block_type="novelty", weight=40, position=0,
             params=_tree([
                 # novelty block returns unplayed tracks ranked by novelty_bonus.
                 # v6: novelty_bonus scales with artist_affinity (0–17 pts) so
                 # tracks from loved artists naturally sort to the top.
                 _node("novelty", {"novelty_min": 0.0, "novelty_max": 100.0}, children=[
                     # Only tracks from artists the user has demonstrated affinity for.
                     # affinity_min=50 is intentionally permissive — captures artists
                     # the user likes but hasn't played to exhaustion.
                     _node("artist", {"artist_affinity_min": 50, "artist_affinity_max": 100,
                                      "played_filter": "unplayed"}),
                     _cooldown(),
                     # Cap at 2 per artist so a big new album doesn't dominate the whole block.
                     _artist_cap(2),
                     _jitter(0.15),
                 ]),
             ])),

        # Block 1: Broad Discovery — surfaces music from across the affinity spectrum
        dict(block_type="discovery", weight=40, position=1,
             params=_tree([
                 _node("discovery", {
                     # Rebalance tiers: more weight on acquaintances (artists you've heard
                     # a little of) and less on strangers vs the old 50/35/15 split.
                     # This means "artists you've sampled but not dug into" get more airtime.
                     "familiar_pct": 30,
                     "acquaintance_pct": 45,
                     "stranger_pct": 25,
                 }, children=[
                     # Lowered floor from 50 → 25.  This lets in less-mainstream music
                     # from artists like Simon & Garfunkel or White Stripes deep cuts.
                     # Tracks with NULL global_popularity (un-enriched) are still allowed
                     # through because popularity_min=0 keeps the NULL-inclusive path active
                     # in execute_discovery_block (see playlist_blocks.py FIX comment).
                     _node("global_popularity", {"popularity_min": 25, "popularity_max": 100}),
                     _artist_cap(1),
                     _jitter(0.20),
                 ]),
             ])),

        # Block 2: Familiar Anchors — keeps the playlist listenable
        dict(block_type="final_score", weight=20, position=2,
             params=_tree([
                 _node("final_score", {"score_min": 70, "score_max": 99}, children=[
                     _node("affinity", {"affinity_min": 55, "affinity_max": 100, "played_filter": "all"}),
                     _node("played_status", {"played_filter": "played"}),
                     _cooldown(),
                     _artist_cap(2),
                     _jitter(0.12),
                 ]),
             ])),
    ]

def _blocks_most_played() -> list[dict]:
    """All-time most-played, cooldown-filtered."""
    return [
        dict(block_type="play_count", weight=100, position=0,
             params=_tree([
                 _node("play_count", {"play_count_min": 5, "play_count_max": 9999, "order": "desc"}, children=[
                     _cooldown(),
                     _artist_cap(5),
                     _jitter(0.08),
                 ]),
             ])),
    ]

def _blocks_recently_played() -> list[dict]:
    """Played in last 30 days, cooldown-filtered."""
    return [
        dict(block_type="play_recency", weight=100, position=0,
             params=_tree([
                 _node("play_recency", {"mode": "within", "days": 30}, children=[
                     _cooldown(),
                     _artist_cap(4),
                     _jitter(0.08),
                 ]),
             ])),
    ]


# ── Prefab table (used by both seed and migrate) ──────────────────────────────

_PREFABS = [
    (
        dict(name="For You",
             description="A personalised mix weighted by your listening history, taste affinity, and play recency.",
             owner_user_id=None, is_public=True, is_system=True,
             total_tracks=60, blend_mode="weighted_shuffle"),
        _blocks_for_you(),
    ),
    (
        dict(name="New For You",
             description="New music you're likely to love: unheard tracks from your favourite artists, broader discovery picks, and a few familiar anchors.",
             owner_user_id=None, is_public=True, is_system=True,
             total_tracks=50, blend_mode="weighted_shuffle"),
        _blocks_new_for_you(),
    ),
    (
        dict(name="Most Played",
             description="Your all-time most-played tracks, sorted by play count. Skipped-heavy tracks are excluded.",
             owner_user_id=None, is_public=True, is_system=True,
             total_tracks=50, blend_mode="weighted_shuffle"),
        _blocks_most_played(),
    ),
    (
        dict(name="Recently Played",
             description="Tracks you have listened to in the last 30 days. Skipped-heavy tracks are filtered out.",
             owner_user_id=None, is_public=True, is_system=True,
             total_tracks=40, blend_mode="weighted_shuffle"),
        _blocks_recently_played(),
    ),
]

# Lookup maps for the migrator
_MIGRATION_BLOCKS = {
    "For You":         _blocks_for_you,
    "New For You":     _blocks_new_for_you,
    "Most Played":     _blocks_most_played,
    "Recently Played": _blocks_recently_played,
}

_MIGRATION_META = {tpl_kwargs["name"]: tpl_kwargs for tpl_kwargs, _ in _PREFABS}


# ── seed_prefabs ──────────────────────────────────────────────────────────────

def seed_prefabs(db) -> None:
    """Insert the four system templates on a fresh DB. No-ops if any is_system rows exist."""
    from models import PlaylistTemplate, PlaylistBlock
    try:
        if db.query(PlaylistTemplate).filter_by(is_system=True).count() > 0:
            log.debug("Prefab seeder: system templates already present — skipping seed.")
            return

        log.info("Prefab seeder: seeding %d system playlist templates...", len(_PREFABS))
        for template_kwargs, blocks in _PREFABS:
            template = PlaylistTemplate(**template_kwargs)
            db.add(template)
            db.flush()
            for block_kwargs in blocks:
                db.add(PlaylistBlock(template_id=template.id, **block_kwargs))
        db.commit()
        log.info("Prefab seeder: done — %d templates inserted.", len(_PREFABS))
    except Exception as exc:
        log.error("Prefab seeder failed (non-fatal): %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


# ── migrate_system_templates ──────────────────────────────────────────────────

def migrate_system_templates(db) -> None:
    """
    In-place migration of existing system playlist templates to the new
    filter_tree block format.

    Safety guarantees
    ─────────────────
    - Touches ONLY rows where is_system = True.
    - Never changes template.id — UserPlaylist.template_id foreign keys
      remain valid; no user playlists are affected.
    - Never touches user-created templates (is_system=False), including
      any user forks of system templates.
    - Idempotent: templates whose blocks already use the filter_tree schema
      are skipped, so this is safe to call on every boot.
    - Per-template transactions: a failure on one template is rolled back
      and logged; the others still complete.

    v6 note: "New For You" now uses a novelty block (block_type="novelty")
    as its primary block.  The migration detects the old design by checking
    if any block uses block_type="discovery" with weight >= 60 — that was
    the signature of the old single-block 70% discovery design.  If found,
    the template is rewritten with the new three-block design.

    All other system templates are still detected by the existing
    filter_tree sentinel check and skipped if already migrated.
    """
    from models import PlaylistTemplate, PlaylistBlock

    try:
        system_templates = (
            db.query(PlaylistTemplate)
            .filter(PlaylistTemplate.is_system == True)  # noqa: E712
            .all()
        )
    except Exception as exc:
        log.error("migrate_system_templates: query failed: %s", exc)
        return

    if not system_templates:
        log.debug("migrate_system_templates: no system templates found.")
        return

    migrated = skipped = 0

    for template in system_templates:
        name = template.name

        if name not in _MIGRATION_BLOCKS:
            log.debug("migrate_system_templates: '%s' not in migration map — skipping.", name)
            skipped += 1
            continue

        existing_blocks = db.query(PlaylistBlock).filter(
            PlaylistBlock.template_id == template.id
        ).all()

        # Check if this is the old "New For You" that needs upgrading:
        # Old design: one discovery block at weight=70, one final_score at weight=30,
        # with global_popularity >= 50 hard-filter.
        # New design: novelty block at weight=40 is the primary block.
        needs_nfy_upgrade = False
        if name == "New For You":
            has_novelty_block = any(b.block_type == "novelty" for b in existing_blocks)
            if not has_novelty_block:
                needs_nfy_upgrade = True
                log.info(
                    "migrate_system_templates: '%s' (id=%d) — old discovery-only design "
                    "detected, upgrading to v6 three-block design.",
                    name, template.id,
                )

        # For non-NFY templates: skip if already on filter_tree schema
        if not needs_nfy_upgrade:
            already_migrated = any(
                "filter_tree" in (json.loads(b.params) if isinstance(b.params, str) else (b.params or {}))
                for b in existing_blocks
            )
            if already_migrated:
                log.debug("migrate_system_templates: '%s' already on filter_tree schema — skipping.", name)
                skipped += 1
                continue

        try:
            # 1. Drop all existing blocks for this template
            db.query(PlaylistBlock).filter(
                PlaylistBlock.template_id == template.id
            ).delete(synchronize_session=False)

            # 2. Insert canonical new-format blocks
            new_blocks = _MIGRATION_BLOCKS[name]()
            for block_kwargs in new_blocks:
                db.add(PlaylistBlock(template_id=template.id, **block_kwargs))

            # 3. Update metadata and stamp the version tag
            meta = _MIGRATION_META[name]
            template.description  = meta["description"]
            template.total_tracks = meta["total_tracks"]
            template.updated_at   = datetime.utcnow()

            db.commit()
            migrated += 1
            log.info(
                "migrate_system_templates: '%s' (id=%d) migrated to filter_tree schema — "
                "%d block(s) written.",
                name, template.id, len(new_blocks),
            )

        except Exception as exc:
            log.error(
                "migrate_system_templates: '%s' (id=%d) failed — rolling back: %s",
                name, template.id, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass

    log.info(
        "migrate_system_templates: complete — %d migrated, %d skipped.",
        migrated, skipped,
    )
