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

v11 fix — "New For You" genre_affinity_min lowered 40→25:
  Root cause: genre_affinity_min=40 is a RELATIVE threshold — it means "genres
  where you've listened at least 40% as much as your top genre."  A user with
  500 pop plays and 150 classic rock plays has classic rock at g_aff=30, which
  FAILS the 40 threshold → classic rock is completely excluded from Block 0/1.
  Secondary genres the user genuinely likes end up invisible.

  Fix: lowered to 25, meaning "at least 25% of your top genre's engagement."
  With the example above, classic rock (g_aff=30) now qualifies.  The threshold
  still excludes truly peripheral genres (g_aff<25) while including any genre
  where the user has meaningful but not dominant listening history.

v11 fix — "New For You" cross-block artist dedup (playlist_engine.py):
  Root cause: per-block artist_cap=1 prevents an artist from taking more than
  1 slot WITHIN a single block, but the same artist can appear once in Block 0
  (liked genre), once in Block 1 (adjacent genre), and once in Block 3
  (popularity) — potentially 3+ tracks from Kelsea Ballerini in one playlist.
  The fix is in playlist_engine.py: after interleaving, a strict global artist
  cap (minimum per-block cap across all blocks) removes duplicates.

v10 fix — "New For You" popular discoveries block added:
  Root cause: highly popular unplayed tracks by stranger artists (e.g. Aretha
  Franklin "Respect", Rick Springfield "Jessie's Girl") were invisible because:
    1. Their Last.fm genre ("soul", "pop rock") sat outside or one extra hop from
       the user's genre affinity path — no direct route through Block 0/1.
    2. POPULARITY_UNPLAYED_MAX was only 10 pts (12.8% of the 78-pt cap), too weak
       to lift zero-affinity strangers above higher-affinity tracks in the 2000-row
       pool, so they were cut before the engine ever saw them.

  Fix: adds Block 3 "Popular Discoveries" (weight 15%) — unplayed tracks with
  global_popularity >= 65, restricted to stranger/acquaintance artists, 1 per
  artist with high jitter.  This is a direct "bangers you haven't heard" path
  that bypasses genre filtering entirely.

  Weights rebalanced: Block 0 55→50, Block 1 30→25, Block 2 15→10, Block 3 new 15.

v9 fix — "New For You" discovery constraint was silently bypassed:
  Root cause: artist_cap and jitter nodes were siblings of discovery inside the
  genre node's children list.  The tree evaluator OR-unions siblings before
  AND-ing with the parent:

    evaluate([discovery, artist_cap, jitter])
      = OR(stranger+acq tracks, ALL tracks, ALL tracks)
      = ALL tracks           ← familiar artists' unplayed tracks included!

    genre_result AND ALL = genre_result   ← discovery constraint vanishes

  Fix: artist_cap and jitter are now children of discovery, not siblings:

    evaluate([artist_cap, jitter])         = OR(ALL, ALL) = ALL
    discovery_result AND ALL               = discovery_result  (stranger+acq only)
    genre_result AND discovery_result      = correct intersection ✓

  Before: familiar artists with 10+ plays could fill Block 0/1 with their
  high-scoring (UNPLAYED_CAP = 78) unplayed tracks, starving true strangers
  (final_score ≈ 37–50) of any playlist slots.

  After: only stranger (0 plays) and acquaintance (1–9 plays) artists are
  eligible for Blocks 0/1; familiar artists are reserved for Block 2 (anchors).
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
    """65% recently played + high score | 35% affinity-matched, not-recently-played.

    v12: raised quality floors now that random selection is in effect.
      Block 0: score_min 63 → 75 — filters out mediocre played tracks that
               previously only appeared through score-sort luck.
      Block 1: affinity_min 60 → 70 — tightens the taste signal without
               meaningfully shrinking the large unplayed candidate pool.
    """
    return [
        dict(block_type="final_score", weight=65, position=0,
             params=_tree([
                 _node("final_score", {"score_min": 75, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}),
                     _cooldown(),
                     _artist_cap(3),
                     _jitter(0.10),
                 ]),
             ])),
        dict(block_type="affinity", weight=35, position=1,
             params=_tree([
                 _node("affinity", {"affinity_min": 70, "affinity_max": 100, "played_filter": "all"}, children=[
                     _node("global_popularity", {"popularity_min": 40, "popularity_max": 100}),
                     _node("play_recency", {"mode": "older", "days": 60}),
                     _cooldown(),
                     _artist_cap(2),
                     _jitter(0.15),
                 ]),
                 _node("affinity", {"affinity_min": 70, "affinity_max": 100, "played_filter": "unplayed"}, children=[
                     _node("global_popularity", {"popularity_min": 40, "popularity_max": 100}),
                     _artist_cap(2),
                     _jitter(0.15),
                 ]),
             ])),
    ]


def _blocks_new_for_you() -> list[dict]:
    """
    Affinity-first discovery: pool from genres/artists the user likes,
    ranked by score within that pool, with a genre-branching slot and a
    popularity-first catch-all for globally popular unheard tracks.

      50% — Liked Genre Discovery
        Unplayed tracks in genres the user has affinity >= 40 for, from
        artists they don't know well yet (stranger + acquaintance tiers,
        familiar_pct=0).  Ranked by final_score (genre/artist affinity +
        popularity nudge baked in), 1 track per artist for breadth.

      25% — Adjacent Genre Discovery
        Unplayed tracks in genres adjacent to the user's high-affinity
        genres, sourced via the GENRE_ADJACENCY map.  Same familiarity
        constraint as Block 0.  Slightly higher jitter for variety.
        Source genres (already liked) are excluded so this block branches
        outward, not backward.

      10% — Familiar Anchors
        Small slice of recently played, high-scoring tracks so the
        playlist stays listenable.

      15% — Popular Discoveries
        Highly popular (global_popularity >= 65) unplayed tracks from
        stranger/acquaintance artists, regardless of genre.  Directly
        surfaces "bangers you haven't heard" that might be invisible to
        the affinity path because their genre is an extra hop away or
        their artist has no play history.

    v9 node structure note:
      artist_cap and jitter are children of discovery, not siblings.
      Siblings are OR'd before AND-ing with their parent; if artist_cap/jitter
      (which return ALL tracks) were siblings of discovery, the OR would
      expand the set to ALL and completely negate the familiarity constraint.
      Nesting them inside discovery ensures only stranger+acquaintance tracks
      survive the AND-intersection with the parent genre block.
    """
    return [
        # Block 0: Liked genre discovery — affinity-first main engine
        # Chain: genre (unplayed, affinity≥40) → discovery (stranger+acq) → [cap, jitter]
        dict(block_type="genre", weight=50, position=0,
             params=_tree([
                 _node("genre", {
                     # No genre list = all genres; affinity_min narrows to only
                     # genres the user has meaningfully engaged with.
                     # v12: raised 25→40. Distribution is bimodal (tracks either
                     # match well or not at all), so pool size is nearly identical
                     # but the intent is cleaner: require real genre affinity.
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     # v9: discovery is the SOLE child so the AND-intersection
                     # with genre enforces the familiarity gate properly.
                     # artist_cap and jitter are nested inside so their ALL-track
                     # returns don't OR-expand the sibling set.
                     _node("discovery", {
                         "stranger_pct": 55,
                         "acquaintance_pct": 45,
                         "familiar_pct": 0,
                     }, children=[
                         _artist_cap(1),
                         _jitter(0.15),
                     ]),
                 ]),
             ])),

        # Block 1: Adjacent genre discovery — genre branching slot
        # Chain: genre_adjacent (unplayed, affinity≥40) → discovery → [cap, jitter]
        dict(block_type="genre_adjacent", weight=25, position=1,
             params=_tree([
                 _node("genre_adjacent", {
                     # Finds genres adjacent to those with affinity >= 40,
                     # automatically excluding the source genres themselves.
                     # v12: raised 25→40 to match Block 0.
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 60,
                         "acquaintance_pct": 40,
                         "familiar_pct": 0,
                     }, children=[
                         _artist_cap(1),
                         _jitter(0.18),
                     ]),
                 ]),
             ])),

        # Block 2: Familiar anchors — keeps the playlist listenable
        # Chain: final_score (70-99) → play_recency → cooldown → [cap, jitter]
        # Each filter is a child of the previous so all three AND-narrow the result.
        dict(block_type="final_score", weight=10, position=2,
             params=_tree([
                 _node("final_score", {"score_min": 78, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}, children=[
                         _node("cooldown", {"mode": "exclude_active"}, children=[
                             _artist_cap(2),
                             _jitter(0.12),
                         ]),
                     ]),
                 ]),
             ])),

        # Block 3: Popular discoveries — "bangers you haven't heard" catch-all
        # v10: directly surfaces globally popular unplayed tracks that may be invisible
        # to Blocks 0/1 because their genre sits an extra hop from the user's affinity
        # path (e.g. "soul" when user likes "pop", or "pop rock" when user likes
        # "classic rock").  Popularity >= 65 ensures only well-regarded tracks surface.
        # High jitter keeps the selection varied across playlist generations.
        dict(block_type="global_popularity", weight=15, position=3,
             params=_tree([
                 _node("global_popularity", {
                     # v12: raised 65→75 — slightly higher bar for "bangers you
                     # haven't heard" so the random pick pulls from better tracks.
                     "popularity_min": 75,
                     "popularity_max": 100,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 70,
                         "acquaintance_pct": 30,
                         "familiar_pct": 0,
                     }, children=[
                         _artist_cap(1),
                         _jitter(0.22),
                     ]),
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
    - Idempotent: "New For You" v10 migration checks whether any block has
      block_type == "global_popularity".  If present, already v10 — skipped.
      Upgrades v7/v8/v9 installations to v10 in one pass.
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

        # ── Per-template latest-design sentinels ─────────────────────────────
        # Each sentinel checks a value that changes with every design revision so
        # the migrator runs exactly once per version bump and no-ops thereafter.
        if name == "New For You":
            def _genre_aff_min(blocks) -> float:
                """Return genre_affinity_min from the first genre block, or 0 if absent."""
                for b in blocks:
                    if b.block_type == "genre":
                        try:
                            chain_data = (json.loads(b.params) if isinstance(b.params, str)
                                          else (b.params or {}))
                            ft = chain_data.get("filter_tree") or []
                            if ft:
                                return float(ft[0].get("params", {}).get("genre_affinity_min", 0))
                        except Exception:
                            pass
                return 0.0

            def _nfy_anchor_floor(blocks) -> float:
                """Return score_min from the final_score anchor block (Block 2), or 0."""
                for b in blocks:
                    if b.block_type == "final_score":
                        try:
                            chain_data = (json.loads(b.params) if isinstance(b.params, str)
                                          else (b.params or {}))
                            ft = chain_data.get("filter_tree") or []
                            if ft:
                                return float(ft[0].get("params", {}).get("score_min", 0))
                        except Exception:
                            pass
                return 0.0

            # v12: genre_affinity_min raised back to 40 (distribution is bimodal
            # so pool size is unchanged), anchor floor raised 70→78.
            # Composite sentinel: both must match to be considered current.
            is_latest = (
                _genre_aff_min(existing_blocks) >= 40
                and _nfy_anchor_floor(existing_blocks) >= 78
            )
            if is_latest:
                log.debug(
                    "migrate_system_templates: '%s' already on latest design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v12 "
                "(genre_affinity_min→40, anchor floor→78, pop discoveries→75).",
                name, template.id,
            )

        elif name == "For You":
            def _fy_score_min(blocks) -> float:
                """Return score_min from the final_score block (Block 0), or 0."""
                for b in blocks:
                    if b.block_type == "final_score":
                        try:
                            chain_data = (json.loads(b.params) if isinstance(b.params, str)
                                          else (b.params or {}))
                            ft = chain_data.get("filter_tree") or []
                            if ft:
                                return float(ft[0].get("params", {}).get("score_min", 0))
                        except Exception:
                            pass
                return 0.0

            # v12: score_min raised 63→75, affinity_min raised 60→70.
            is_latest = _fy_score_min(existing_blocks) >= 75
            if is_latest:
                log.debug(
                    "migrate_system_templates: '%s' already on latest design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v12 "
                "(score_min 63→75, affinity_min 60→70).",
                name, template.id,
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