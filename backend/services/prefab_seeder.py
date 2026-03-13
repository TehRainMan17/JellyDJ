
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

v7 change — "New For You" philosophy inversion:
  Previous designs (v5, v6) both had a large block that pulled from
  high-affinity artists (artists you already listen to a lot).  The result
  was a playlist dominated by deep cuts from your heavy-rotation artists —
  exactly the opposite of what "New For You" should do.

  The correct intent is:
    - Prioritise globally popular unheard tracks from artists you don't
      already know well (strangers + acquaintances ranked by global_popularity).
    - Allow a familiar artist's song in only if it is overwhelmingly globally
      popular (popularity >= 80) — i.e. you'd be embarrassed not to know it.
    - Keep a small buffer of high-score played tracks as anchors so the
      playlist isn't 100% unfamiliar.

  New three-block design:

    Block 0 — "Popular Strangers & Acquaintances" (weight 60)
      Unplayed tracks from artists you have NOT listened to much
      (stranger + acquaintance tiers only, familiar_pct = 0), hard-filtered
      to global_popularity >= 60.  This surfaces the best new-to-you music
      with a real quality signal behind it.

    Block 1 — "Mega-Hits from Familiar Artists" (weight 25)
      Unplayed tracks from artists you DO know well, but only those with
      global_popularity >= 80.  The high floor is intentional — if you
      listen to Imagine Dragons constantly and there's a song you haven't
      heard, it should only surface here if it's genuinely one of their
      biggest tracks, not a B-side.

    Block 2 — "Familiar Anchors" (weight 15)
      A small slice of recently played, high-scoring tracks.  Keeps the
      playlist listenable and prevents cold-start overwhelm.  This is the
      only place where your heavy-rotation artists appear without restriction.
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
    Popularity-first discovery: best globally popular unheard tracks,
    familiar artists only when overwhelmingly popular.

      60% — Popular Strangers & Acquaintances
        Unplayed tracks from artists you don't know well yet, filtered to
        global_popularity >= 60.  Uses the discovery block with familiar_pct=0
        so your heavy-rotation artists are completely excluded from this slot.
        Ranked by popularity (jitter keeps it from being identical every run).

      25% — Mega-Hits from Familiar Artists
        Unplayed tracks from well-known artists (artists you play often), but
        only those scoring >= 80 global popularity.  This is the "you love
        Imagine Dragons but somehow missed this massive song" slot.  The high
        floor prevents deep cuts from heavy-rotation artists leaking in.

      15% — Familiar Anchors
        Small slice of recently played high-scoring tracks so the playlist
        stays listenable.
    """
    return [
        # Block 0: Popular strangers & acquaintances — the main discovery engine
        dict(block_type="discovery", weight=60, position=0,
             params=_tree([
                 _node("discovery", {
                     # familiar_pct = 0: completely exclude artists you listen to a lot.
                     # All slots go to strangers and acquaintances.
                     "familiar_pct": 0,
                     "acquaintance_pct": 55,
                     "stranger_pct": 45,
                 }, children=[
                     # Hard floor of 60 so only genuinely popular tracks surface.
                     # The discovery block's NULL-safe path keeps un-enriched tracks
                     # out when a floor > 0 is set — intentional; we want a real
                     # quality signal here, not a random un-scored track.
                     _node("global_popularity", {"popularity_min": 60, "popularity_max": 100}),
                     # 1 per artist max — maximise breadth of new artists heard.
                     _artist_cap(1),
                     _jitter(0.18),
                 ]),
             ])),

        # Block 1: Mega-hits from familiar artists — high bar, tight filter
        dict(block_type="global_popularity", weight=25, position=1,
             params=_tree([
                 _node("global_popularity", {"popularity_min": 80, "popularity_max": 100,
                                             "played_filter": "unplayed"}, children=[
                     # affinity_min=70 targets artists you genuinely listen to a lot.
                     # Combined with popularity >= 80 this is a very tight filter:
                     # only the biggest songs from your most-played artists.
                     _node("affinity", {"affinity_min": 70, "affinity_max": 100,
                                        "played_filter": "unplayed"}),
                     _cooldown(),
                     # 1 per artist so one beloved artist can't dominate.
                     _artist_cap(1),
                     _jitter(0.10),
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
             description="Globally popular tracks from artists you don't know yet, plus the biggest hits from artists you love that you've somehow missed.",
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

        # v7 sentinel: the new "New For You" design is identified by having a
        # global_popularity block at position 1.  Any prior design used
        # discovery (v5 original), novelty (v6), or final_score at position 1.
        if name == "New For You":
            has_v7_design = any(
                b.block_type == "global_popularity" and b.position == 1
                for b in existing_blocks
            )
            if has_v7_design:
                log.debug(
                    "migrate_system_templates: '%s' already on v7 design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v7 "
                "popularity-first design.", name, template.id,
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
