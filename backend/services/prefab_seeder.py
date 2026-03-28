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

v13 — catalog popularity bias + jitter removal:
  Jitter deprecated: playlist generation now uses random selection globally,
  so per-block jitter nodes are redundant and were acting as spurious filters
  (passthrough = ALL, but their presence in sibling lists caused unexpected
  OR-expansion).  Removed from all four system templates.

v13 — catalog popularity bias ("For You" + "New For You"):
  Root cause identified: Block 1 of "For You" had global_popularity,
  play_recency, and cooldown as OR-siblings of artist_cap/jitter (passthrough
  = ALL).  OR-union with ALL resolves to ALL, so those filters were completely
  inert — Block 1 was effectively just affinity >= 70 with random jitter.

  "For You" fix: replace inert global_popularity sibling with a properly
  chained catalog_pop filter:
    affinity → catalog_pop(>=25) → play_recency → cooldown → [cap, jitter]
  Effect: Block 1 now delivers the artist's signature hits rather than an
  unfiltered affinity pool.

  "New For You" upgrade: catalog_pop added as a third gate in Blocks 0/1
  (genre → discovery → catalog_pop → [cap, jitter]) so first-listen tracks
  are an artist's known songs, not deep cuts.  Added Block 3 (catalog_pop
  >= 60, discovery) as a dedicated "best unheard track per new artist" path.
  Block 3 (was global_popularity catch-all) moves to Block 4.

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

def _cooldown() -> dict:
    return _node("cooldown", {"mode": "exclude_active"})

def _artist_cap(n: int = 3) -> dict:
    return _node("artist_cap", {"max_per_artist": n})

def _catalog_pop(min_pct: float = 30.0, played_filter: str = "all") -> dict:
    """Filter to tracks whose artist_catalog_popularity >= min_pct.
    100 = artist's #1 Last.fm hit; values are proportional by listener count.
    Tracks from unenriched artists (NULL) are excluded by the executor.
    """
    return _node("artist_catalog_popularity", {
        "catalog_min": min_pct,
        "catalog_max": 100.0,
        "played_filter": played_filter,
    })


# ── Canonical block builders ──────────────────────────────────────────────────

def _blocks_for_you() -> list[dict]:
    """65% recently played + high score | 35% affinity-matched, catalog-biased.

    v12: raised quality floors now that random selection is in effect.
      Block 0: score_min 63 → 75
      Block 1: affinity_min 60 → 70

    v13: catalog popularity bias in Block 1.
      Previously Block 1 used global_popularity >= 40 as an AND child, but
      global_popularity was OR-sibling to artist_cap/jitter (both passthrough =
      ALL), so the union resolved to ALL and the popularity filter was never
      applied — Block 1 was just affinity >= 70 with no secondary gate.

      Fix + upgrade: replace global_popularity with artist_catalog_popularity
      and properly chain each filter so AND semantics actually hold:

        affinity → catalog_pop → play_recency → cooldown → [cap, jitter]

      Each filter is the SOLE child of the prior, so the AND-intersection is
      never widened by a passthrough sibling.  cap and jitter are still found
      depth-first by the engine's tree walker and applied post-processing.

      Effect: Block 1 now delivers tracks from artists you love that are also
      that artist's signature songs on Last.fm — not just globally popular
      tracks, which were often mega-hits from artists you barely listen to.
      Tracks whose artist has no Last.fm enrichment (catalog_pop = NULL) fall
      back to Block 0's high-final-score pool rather than appearing here.
    """
    return [
        # Block 0: high-scoring recently played tracks — the "comfort food" slice.
        dict(block_type="final_score", weight=65, position=0,
             params=_tree([
                 _node("final_score", {"score_min": 75, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}),
                     _cooldown(),
                     _artist_cap(3),
                 ]),
             ])),

        # Block 1: affinity-matched tracks biased toward each artist's catalog hits.
        # Two OR chains: played (not recently) and unplayed.  Each is properly chained
        # so catalog_pop, recency, and cooldown all AND-narrow the pool in sequence.
        # Chaining rule: each filter is the SOLE meaningful child of the prior so the
        # AND-intersection is never widened by a passthrough (cap/jitter = ALL) sibling.
        dict(block_type="affinity", weight=35, position=1,
             params=_tree([
                 # Chain A: played tracks not heard recently, narrowed to catalog hits.
                 # affinity → catalog_pop → play_recency → cooldown → [cap, jitter]
                 _node("affinity", {"affinity_min": 70, "affinity_max": 100, "played_filter": "played"}, children=[
                     _node("artist_catalog_popularity", {"catalog_min": 25.0, "catalog_max": 100.0, "played_filter": "played"}, children=[
                         _node("play_recency", {"mode": "older", "days": 60}, children=[
                             _node("cooldown", {"mode": "exclude_active"}, children=[
                                 _artist_cap(2),
                             ]),
                         ]),
                     ]),
                 ]),
                 # Chain B: unplayed tracks from liked artists, narrowed to catalog hits.
                 # affinity → catalog_pop → [cap, jitter]
                 _node("affinity", {"affinity_min": 70, "affinity_max": 100, "played_filter": "unplayed"}, children=[
                     _node("artist_catalog_popularity", {"catalog_min": 25.0, "catalog_max": 100.0, "played_filter": "unplayed"}, children=[
                         _artist_cap(2),
                     ]),
                 ]),
             ])),
    ]


def _blocks_new_for_you() -> list[dict]:
    """
    Affinity-first discovery biased toward each artist's catalog hits.

    v13: catalog popularity integrated throughout so discovery surfaces an
    artist's best-known songs rather than arbitrary or obscure tracks.

      40% — Liked Genre Discovery (catalog-biased)
        Unplayed tracks in genres the user has affinity >= 40 for, from
        stranger/acquaintance artists, AND in the artist's top catalog
        (catalog_pop >= 30 = roughly top 5 songs on Last.fm for that artist).
        Chain: genre → discovery → catalog_pop(>=30) → [cap, jitter].

      20% — Adjacent Genre Discovery (catalog-biased)
        Same shape as Block 0 but branching into adjacent genres the user
        hasn't directly engaged with yet.  catalog_pop(>=30) ensures the
        first impression of a new genre comes from proven songs, not deep cuts.
        Chain: genre_adjacent → discovery → catalog_pop(>=30) → [cap, jitter].

      10% — Familiar Anchors
        Small slice of recently played, high-scoring tracks so the
        playlist stays listenable.  Unchanged from prior versions.

      15% — Artist Catalog Top Songs (new)
        Directly targets an artist's #1–3 hits (catalog_pop >= 60) from
        stranger/acquaintance artists, regardless of genre.  This is the
        "hear an artist's best work before exploring their catalog" path —
        especially useful for new artists entering the library.
        Chain: catalog_pop(>=60, unplayed) → discovery → [cap, jitter].

      15% — Global Popular Discoveries (fallback)
        Highly popular unplayed tracks regardless of catalog data.  Catches
        artists without Last.fm enrichment who are excluded from Blocks 0–1
        and Block 3.  Unchanged from v12 (popularity_min=75).

    Chaining note (v9 / v13):
      In every block, cap and jitter are the DEEPEST leaves of the chain.
      Passthrough blocks (artist_cap, jitter) return ALL tracks; if they
      were siblings of real filters the OR union would resolve to ALL and
      negate the filter.  Making each real filter the sole parent of the
      next ensures AND semantics hold at every level.
    """
    return [
        # Block 0: Liked genre discovery — catalog-biased main engine.
        # genre → discovery → catalog_pop → [cap, jitter]
        # All three gates are properly chained so each AND-narrows the prior result.
        dict(block_type="genre", weight=40, position=0,
             params=_tree([
                 _node("genre", {
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 55,
                         "acquaintance_pct": 45,
                         "familiar_pct": 0,
                     }, children=[
                         # v13: catalog_pop is the sole child of discovery so the
                         # AND-intersection keeps only catalog-popular tracks from
                         # stranger/acquaintance artists in liked genres.
                         # cap and jitter are inside catalog_pop (deepest leaf).
                         _node("artist_catalog_popularity", {
                             "catalog_min": 30.0,
                             "catalog_max": 100.0,
                             "played_filter": "unplayed",
                         }, children=[
                             _artist_cap(1),
                         ]),
                     ]),
                 ]),
             ])),

        # Block 1: Adjacent genre discovery — catalog-biased genre branching slot.
        # genre_adjacent → discovery → catalog_pop → [cap]
        dict(block_type="genre_adjacent", weight=20, position=1,
             params=_tree([
                 _node("genre_adjacent", {
                     "genre_affinity_min": 40,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 60,
                         "acquaintance_pct": 40,
                         "familiar_pct": 0,
                     }, children=[
                         _node("artist_catalog_popularity", {
                             "catalog_min": 30.0,
                             "catalog_max": 100.0,
                             "played_filter": "unplayed",
                         }, children=[
                             _artist_cap(1),
                         ]),
                     ]),
                 ]),
             ])),

        # Block 2: Familiar anchors — keeps the playlist listenable.
        # final_score → play_recency → cooldown → [cap]
        dict(block_type="final_score", weight=10, position=2,
             params=_tree([
                 _node("final_score", {"score_min": 78, "score_max": 99}, children=[
                     _node("play_recency", {"mode": "within", "days": 60}, children=[
                         _node("cooldown", {"mode": "exclude_active"}, children=[
                             _artist_cap(2),
                         ]),
                     ]),
                 ]),
             ])),

        # Block 3: Artist catalog top songs — "best unheard track per new artist".
        # v13: directly surfaces catalog_pop >= 60 (roughly an artist's #1–2 hits)
        # from stranger/acquaintance artists regardless of genre.
        # catalog_pop → discovery → [cap]
        dict(block_type="artist_catalog_popularity", weight=15, position=3,
             params=_tree([
                 _node("artist_catalog_popularity", {
                     "catalog_min": 60.0,
                     "catalog_max": 100.0,
                     "played_filter": "unplayed",
                 }, children=[
                     _node("discovery", {
                         "stranger_pct": 70,
                         "acquaintance_pct": 30,
                         "familiar_pct": 0,
                     }, children=[
                         _artist_cap(1),
                     ]),
                 ]),
             ])),

        # Block 4: Global popular discoveries — fallback for unenriched artists.
        # Catches tracks whose artist has no Last.fm catalog data (catalog_pop=NULL).
        # global_popularity → discovery → [cap]
        dict(block_type="global_popularity", weight=15, position=4,
             params=_tree([
                 _node("global_popularity", {
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
            def _nfy_has_catalog_bias(blocks) -> bool:
                """Return True if any block uses artist_catalog_popularity (v13 marker)."""
                for b in blocks:
                    try:
                        chain_str = b.params if isinstance(b.params, str) else json.dumps(b.params or {})
                        if "artist_catalog_popularity" in chain_str:
                            return True
                    except Exception:
                        pass
                return False

            def _has_jitter(blocks) -> bool:
                for b in blocks:
                    try:
                        chain_str = b.params if isinstance(b.params, str) else json.dumps(b.params or {})
                        if '"jitter"' in chain_str:
                            return True
                    except Exception:
                        pass
                return False

            # v13: catalog bias present AND jitter removed.
            is_latest = _nfy_has_catalog_bias(existing_blocks) and not _has_jitter(existing_blocks)
            if is_latest:
                log.debug(
                    "migrate_system_templates: '%s' already on latest design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v13 "
                "(catalog_pop bias in Blocks 0/1, new catalog top-songs Block 3, "
                "global_popularity fallback becomes Block 4).",
                name, template.id,
            )

        elif name == "For You":
            def _fy_has_catalog_bias(blocks) -> bool:
                """Return True if Block 1 (affinity block) uses artist_catalog_popularity."""
                for b in blocks:
                    if b.block_type == "affinity":
                        try:
                            chain_str = b.params if isinstance(b.params, str) else json.dumps(b.params or {})
                            if "artist_catalog_popularity" in chain_str:
                                return True
                        except Exception:
                            pass
                return False

            def _fy_has_jitter(blocks) -> bool:
                for b in blocks:
                    try:
                        chain_str = b.params if isinstance(b.params, str) else json.dumps(b.params or {})
                        if '"jitter"' in chain_str:
                            return True
                    except Exception:
                        pass
                return False

            # v13: catalog bias present AND jitter removed.
            is_latest = _fy_has_catalog_bias(existing_blocks) and not _fy_has_jitter(existing_blocks)
            if is_latest:
                log.debug(
                    "migrate_system_templates: '%s' already on latest design — skipping.", name
                )
                skipped += 1
                continue
            log.info(
                "migrate_system_templates: '%s' (id=%d) — upgrading to v13 "
                "(Block 1: affinity → catalog_pop chain, replaces global_popularity sibling).",
                name, template.id,
            )

        else:
            def _has_jitter_generic(blocks) -> bool:
                for b in blocks:
                    try:
                        chain_str = b.params if isinstance(b.params, str) else json.dumps(b.params or {})
                        if '"jitter"' in chain_str:
                            return True
                    except Exception:
                        pass
                return False

            has_filter_tree = any(
                "filter_tree" in (
                    json.loads(b.params) if isinstance(b.params, str) else (b.params or {})
                )
                for b in existing_blocks
            )
            # Up-to-date = filter_tree schema present AND jitter removed.
            already_migrated = has_filter_tree and not _has_jitter_generic(existing_blocks)
            if already_migrated:
                log.debug(
                    "migrate_system_templates: '%s' already on latest schema — skipping.",
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