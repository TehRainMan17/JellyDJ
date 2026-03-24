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
                                 • Idempotent: guarded by block-type sentinel check —
                                   runs exactly once per design revision

Both are called from main.py lifespan on every boot; they no-op instantly
after the first successful run.

v8 change — "New For You" affinity-first rework:
  v7 used global_popularity as the primary filter, which caused rap and mainstream
  pop to dominate discovery regardless of the user's actual taste.  Globally
  popular ≠ personally relevant.

  The correct intent is:
    - Build a candidate pool from genres and artists the user actually likes
      (affinity-first), then rank by popularity within that pool.
    - Surface unknown artists whose best songs fall inside the user's taste
      footprint — this is the most desirable discovery outcome.
    - Branch into adjacent genres (e.g. Pop Rock → Rock → Alt Rock) to gently
      expand the user's taste without jumping to unrelated genres.
    - Keep a small buffer of high-score played tracks as anchors so the
      playlist stays listenable.

  New three-block design (v8):

    Block 0 — "Liked Genre Discovery" (weight 55)
      Unplayed tracks in genres where the user has affinity >= 40, restricted
      to stranger and acquaintance artists only (familiar_pct = 0).  Ranked by
      final_score (which folds in genre/artist affinity + popularity nudge),
      1 track per artist for maximum breadth.

    Block 1 — "Adjacent Genre Discovery" (weight 30)
      Unplayed tracks in genres adjacent to the user's high-affinity genres,
      again restricted to strangers and acquaintances.  Uses the hardcoded
      GENRE_ADJACENCY map to branch into related genres (e.g. if you like
      Folk, this surfaces Americana, Singer-Songwriter, Indie Folk).  Slightly
      higher jitter than Block 0 so genre branching stays varied.

    Block 2 — "Familiar Anchors" (weight 15)
      Unchanged from v7: a small slice of recently played, high-scoring tracks
      to keep the playlist listenable and prevent cold-start overwhelm.
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
    Affinity-first discovery: pool from genres/artists the user likes,
    ranked by score within that pool, with a genre-branching slot.

      55% — Liked Genre Discovery
        Unplayed tracks in genres the user has affinity >= 40 for, from
        artists they don't know well yet (stranger + acquaintance tiers,
        familiar_pct=0).  Ranked by final_score (genre/artist affinity +
        popularity nudge baked in), 1 track per artist for breadth.

      30% — Adjacent Genre Discovery
        Unplayed tracks in genres adjacent to the user's high-affinity
        genres, sourced via the GENRE_ADJACENCY map.  Same familiarity
        constraint as Block 0.  Slightly higher jitter for variety.
        Source genres (already liked) are excluded so this block branches
        outward, not backward.

      15% — Familiar Anchors
        Small slice of recently played, high-scoring tracks so the
        playlist stays listenable.
    """
    return [
        # Block 0: Liked genre discovery — affinity-first main engine
        dict(block_type="genre", weight=55, position=0,
             params=_tree([
                 _node("genre", {
                     # No genre list = all genres; affinity_min narrows to only
                     # genres the user has meaningfully engaged with.
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     # Familiarity gate: strangers and acquaintances only.
                     # familiar_pct=0 keeps heavy-rotation artists out entirely.
                     _node("discovery", {
                         "stranger_pct": 55,
                         "acquaintance_pct": 45,
                         "familiar_pct": 0,
                     }),
                     _artist_cap(1),
                     _jitter(0.15),
                 ]),
             ])),

        # Block 1: Adjacent genre discovery — genre branching slot
        dict(block_type="genre_adjacent", weight=30, position=1,
             params=_tree([
                 _node("genre_adjacent", {
                     # Finds genres adjacent to those with affinity >= 40,
                     # automatically excluding the source genres themselves.
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 60,
                         "acquaintance_pct": 40,
                         "familiar_pct": 0,
                     }),
                     _artist_cap(1),
                     _jitter(0.18),
                 ]),
             ])),

        # Block 2: Familiar anchors — keeps the playlist listenable
        dict(block_type="final_score", weight=15, position=2,
             params=_tree([
                 _node("final_score", {"score_min": 70, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}),
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
             description="Unheard tracks in genres you love, plus a branch into adjacent genres you might not have explored yet.",
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
    In-place migration of existing system playlist templates.

    Safety guarantees
    ─────────────────
    - Touches ONLY rows where is_system = True.
    - Never changes template.id — UserPlaylist.template_id foreign keys
      remain valid; no user playlists are affected.
    - Never touches user-created templates (is_system=False).
    - Idempotent: "New For You" migration is detected by checking whether
      any block uses block_type="global_popularity" at position=1 (the v7
      mega-hits slot).  If already present, the template is skipped.
    - Per-template transactions: failure on one rolls back; others complete.
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

        # v8 sentinel: the new "New For You" design is identified by having a
        # genre_adjacent block at position 1.  Prior designs used:
        #   discovery (v5), novelty (v6), global_popularity (v7).
        if name == "New For You":
            has_v8_design = any(
                b.block_type == "genre_adjacent" and b.position == 1
                for b in existing_blocks
            )
            if has_v8_design:
                log.debug(
                    "migrate_system_templates: '%s' already on v8 design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v8 "
                "affinity-first design.", name, template.id,
            )
        else:
            already_migrated = any(
                "filter_tree" in (
                    json.loads(b.params) if isinstance(b.params, str) else (b.params or {})
                )
                for b in existing_blocks
            )
            if already_migrated:
                log.debug(
                    "migrate_system_templates: '%s' already on filter_tree schema — skipping.",
                    name,
                )
                skipped += 1
                continue

        try:
            db.query(PlaylistBlock).filter(
                PlaylistBlock.template_id == template.id
            ).delete(synchronize_session=False)

            new_blocks = _MIGRATION_BLOCKS[name]()
            for block_kwargs in new_blocks:
                db.add(PlaylistBlock(template_id=template.id, **block_kwargs))

            meta = _MIGRATION_META[name]
            template.description  = meta["description"]
            template.total_tracks = meta["total_tracks"]
            template.updated_at   = datetime.utcnow()

            db.commit()
            migrated += 1
            log.info(
                "migrate_system_templates: '%s' (id=%d) migrated — %d block(s) written.",
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