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
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# Bump this tag whenever _PREFABS changes in a way that needs pushing to
# existing installs. migrate_system_templates() skips templates already
# carrying this tag in their description field.
# Migration is detected by inspecting block params for "filter_tree" key


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
    """30% familiar liked music | 70% unheard globally popular discovery."""
    return [
        dict(block_type="final_score", weight=30, position=0,
             params=_tree([
                 _node("final_score", {"score_min": 70, "score_max": 99}, children=[
                     _node("affinity", {"affinity_min": 55, "affinity_max": 100, "played_filter": "all"}),
                     _node("played_status", {"played_filter": "played"}),
                     _cooldown(),
                     _artist_cap(2),
                     _jitter(0.12),
                 ]),
             ])),
        dict(block_type="discovery", weight=70, position=1,
             params=_tree([
                 _node("discovery", {"familiar_pct": 50, "acquaintance_pct": 35, "stranger_pct": 15}, children=[
                     _node("global_popularity", {"popularity_min": 50, "popularity_max": 100}),
                     _artist_cap(1),
                     _jitter(0.18),
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
             description="30 % familiar favourites, 70 % unheard music you are very likely to love.",
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

        # Already migrated: at least one block uses filter_tree params
        existing_blocks = db.query(PlaylistBlock).filter(
            PlaylistBlock.template_id == template.id
        ).all()
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
