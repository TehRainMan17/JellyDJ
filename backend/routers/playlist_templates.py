
"""
JellyDJ — Playlist Template & Block CRUD router  (Phase 8)

Endpoints
─────────
GET    /api/playlist-templates
POST   /api/playlist-templates
GET    /api/playlist-templates/{id}
PUT    /api/playlist-templates/{id}
DELETE /api/playlist-templates/{id}
POST   /api/playlist-templates/{id}/fork
GET    /api/playlist-templates/{id}/preview
POST   /api/playlist-templates/{id}/blocks
PUT    /api/playlist-templates/{id}/blocks/{block_id}
DELETE /api/playlist-templates/{id}/blocks/{block_id}
POST   /api/playlist-templates/{id}/blocks/reorder

Block params shape (Phase 8):
  Each block's `params` JSON is now a "block chain" descriptor:

  {
    "filter_tree": [          # list of OR-sibling root nodes
      {
        "filter_type": "play_recency",
        "params": {"mode": "within", "days": 30},
        "children": [         # AND-children
          {"filter_type": "global_popularity", "params": {...}, "children": []},
          {"filter_type": "favorites",          "params": {},    "children": []}
        ]
      }
    ]
  }

  Legacy flat params (no filter_tree key) are still supported by the engine
  for backward compatibility.

Weight validation: weights are normalised at runtime rather than hard-blocked.
The API warns when weights don't sum to 100 but always saves.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import UserContext, assert_owns_template, get_current_user
from database import get_db
from models import ManagedUser, PlaylistBlock, PlaylistTemplate

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlist-templates", tags=["playlist-templates"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class BlockIn(BaseModel):
    block_type: str       # top-level type label (display use; engine reads filter_tree)
    weight: int
    position: int
    params: dict = {}     # should contain "filter_tree" key for nested blocks


class TemplateCreateIn(BaseModel):
    name: str
    description: Optional[str] = None
    total_tracks: int = 50
    is_public: bool = True
    blocks: list[BlockIn] = []


class TemplateUpdateIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    total_tracks: Optional[int] = None
    is_public: Optional[bool] = None


class BlockUpdateIn(BaseModel):
    weight: Optional[int] = None
    position: Optional[int] = None
    params: Optional[dict] = None


class BlockReorderIn(BaseModel):
    order: list[int]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _username_map(db: Session) -> dict[str, str]:
    rows = db.query(ManagedUser.jellyfin_user_id, ManagedUser.username).all()
    return {r.jellyfin_user_id: r.username for r in rows}


def _block_out(block: PlaylistBlock) -> dict:
    params = block.params
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}
    return {
        "id": block.id,
        "template_id": block.template_id,
        "block_type": block.block_type,
        "weight": block.weight,
        "position": block.position,
        "params": params,
        "created_at": block.created_at,
        "updated_at": block.updated_at,
    }


def _template_list_item(template: PlaylistTemplate, block_count: int, username: Optional[str], summary: str = "") -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "owner_user_id": template.owner_user_id,
        "owner_username": username,
        "is_public": template.is_public,
        "is_system": template.is_system,
        "forked_from_id": template.forked_from_id,
        "total_tracks": template.total_tracks,
        "block_count": block_count,
        "summary": summary,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _template_detail(template: PlaylistTemplate, blocks: list[PlaylistBlock], username: Optional[str]) -> dict:
    d = _template_list_item(template, len(blocks), username, _summarise_template(blocks))
    d["blocks"] = [_block_out(b) for b in blocks]
    return d


def _get_visible_template(template_id: int, user: UserContext, db: Session) -> PlaylistTemplate:
    template = db.query(PlaylistTemplate).filter(PlaylistTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if user.is_admin:
        return template
    if template.is_public or template.owner_user_id == user.user_id:
        return template
    raise HTTPException(status_code=404, detail="Template not found")


def _blocks_for(template_id: int, db: Session) -> list[PlaylistBlock]:
    return (
        db.query(PlaylistBlock)
        .filter(PlaylistBlock.template_id == template_id)
        .order_by(PlaylistBlock.position)
        .all()
    )


def _weight_warning(blocks: list) -> Optional[str]:
    """Return a warning string if weights don't sum to 100, else None."""
    if not blocks:
        return None
    total = sum(b.weight for b in blocks)
    if abs(total - 100) > 1:
        if total < 100:
            return f"Block weights sum to {total} — remaining {100 - total}% will be distributed proportionally."
        else:
            return f"Block weights sum to {total} — blocks will be scaled down proportionally."
    return None



# ── Plain-English template summary ───────────────────────────────────────────

def _describe_node(node: dict, depth: int = 0) -> str:
    """Recursively describe a filter_tree node in plain English."""
    ft      = node.get("filter_type", "unknown")
    params  = node.get("params", {})
    children = node.get("children", [])

    # Root-level description of this node type
    descriptions = {
        "final_score":       lambda p: (
            f"tracks scoring {int(p.get('score_min', 0))}–{int(p.get('score_max', 99))}"
        ),
        "affinity":          lambda p: (
            f"tracks with {int(p.get('affinity_min', 0))}–{int(p.get('affinity_max', 100))}% affinity"
            + (f" ({p['played_filter']})" if p.get("played_filter", "all") != "all" else "")
        ),
        "play_recency":      lambda p: (
            f"played in the last {p.get('days', 30)} days"
            if p.get("mode", "within") == "within"
            else f"not played in {p.get('days', 30)}+ days"
        ),
        "play_count":        lambda p: (
            f"your most-played tracks (played ≥{p.get('play_count_min', 1)}×)"
        ),
        "global_popularity": lambda p: (
            f"globally popular tracks ({int(p.get('popularity_min', 0))}–{int(p.get('popularity_max', 100))}%)"
        ),
        "discovery":         lambda p: (
            f"unheard music — {int(p.get('familiar_pct', 33))}% from artists you know, "
            f"{int(p.get('acquaintance_pct', 33))}% from ones you've sampled, "
            f"{int(p.get('stranger_pct', 34))}% new artists"
        ),
        "favorites":         lambda p: "your favourited tracks",
        "played_status":     lambda p: (
            "played tracks" if p.get("played_filter") == "played" else "unplayed tracks"
        ),
        "genre":             lambda p: (
            f"genre: {', '.join(p['genres'])}" if p.get("genres") else "any genre"
        ),
        "artist":            lambda p: (
            f"artist: {', '.join(p['artists'])}" if p.get("artists") else "any artist"
        ),
        "artist_cap":        lambda p: f"max {p.get('max_per_artist', 3)} per artist",
        "jitter":            lambda p: f"±{int(p.get('jitter_pct', 0.15)*100)}% shuffle",
        "cooldown":          lambda p: "skip-cooled tracks excluded",
    }

    try:
        base = descriptions.get(ft, lambda p: ft)(params)
    except Exception:
        base = ft

    # Fold meaningful AND-children into the description (skip structural ones)
    structural = {"artist_cap", "jitter", "cooldown"}
    meaningful_children = [c for c in children if c.get("filter_type") not in structural]
    structural_children = [c for c in children if c.get("filter_type") in structural]

    # Structural modifiers as a parenthetical
    mods = []
    for c in structural_children:
        try:
            mods.append(descriptions[c["filter_type"]](c.get("params", {})))
        except Exception:
            pass

    if meaningful_children:
        child_parts = " AND ".join(_describe_node(c) for c in meaningful_children)
        base = f"{base}, filtered to {child_parts}"

    if mods:
        base = f"{base} ({', '.join(mods)})"

    return base


def _summarise_template(blocks: list) -> str:
    """
    Generate a plain-English one-liner describing what a template does.
    Works from the block list returned by _blocks_for().
    """
    if not blocks:
        return "No blocks configured."

    chain_parts = []
    for block in sorted(blocks, key=lambda b: b.position):
        try:
            params = json.loads(block.params) if isinstance(block.params, str) else (block.params or {})
        except Exception:
            params = {}

        filter_tree = params.get("filter_tree", [])
        weight = block.weight

        if filter_tree:
            # OR-siblings described with " OR "
            or_parts = [_describe_node(node) for node in filter_tree]
            chain_desc = " OR ".join(or_parts)
        else:
            # Legacy flat-params fallback — describe by block_type only
            legacy_names = {
                "final_score": "high-scoring tracks",
                "affinity": "high-affinity tracks",
                "play_recency": "recently played tracks",
                "play_count": "most-played tracks",
                "global_popularity": "globally popular tracks",
                "discovery": "unheard discovery tracks",
                "favorites": "favourited tracks",
            }
            chain_desc = legacy_names.get(block.block_type, block.block_type)

        chain_parts.append(f"{weight}% {chain_desc}")

    if len(chain_parts) == 1:
        return chain_parts[0].capitalize() + "."
    else:
        joined = "; ".join(chain_parts[:-1]) + f"; and {chain_parts[-1]}"
        return joined.capitalize() + "."


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def list_templates(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.is_admin:
        templates = db.query(PlaylistTemplate).all()
    else:
        templates = (
            db.query(PlaylistTemplate)
            .filter(
                (PlaylistTemplate.is_public == True) |  # noqa: E712
                (PlaylistTemplate.owner_user_id == user.user_id)
            )
            .all()
        )
    umap = _username_map(db)
    from sqlalchemy import func
    block_counts_rows = (
        db.query(PlaylistBlock.template_id, func.count(PlaylistBlock.id))
        .group_by(PlaylistBlock.template_id)
        .all()
    )
    block_counts: dict[int, int] = {tid: cnt for tid, cnt in block_counts_rows}

    # Fetch all blocks in one query so we can generate summaries without N+1
    template_ids = [t.id for t in templates]
    all_blocks_rows = (
        db.query(PlaylistBlock)
        .filter(PlaylistBlock.template_id.in_(template_ids))
        .order_by(PlaylistBlock.template_id, PlaylistBlock.position)
        .all()
    ) if template_ids else []
    blocks_by_template: dict[int, list] = {}
    for b in all_blocks_rows:
        blocks_by_template.setdefault(b.template_id, []).append(b)

    return [
        _template_list_item(
            t,
            block_counts.get(t.id, 0),
            umap.get(t.owner_user_id or ""),
            _summarise_template(blocks_by_template.get(t.id, [])),
        )
        for t in templates
    ]


@router.post("", status_code=201)
def create_template(
    body: TemplateCreateIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = PlaylistTemplate(
        name=body.name,
        description=body.description,
        owner_user_id=user.user_id,
        is_public=body.is_public,
        is_system=False,
        total_tracks=body.total_tracks,
        blend_mode="weighted_shuffle",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(template)
    db.flush()

    blocks = []
    for i, b in enumerate(body.blocks):
        block = PlaylistBlock(
            template_id=template.id,
            block_type=b.block_type,
            weight=b.weight,
            position=b.position,
            params=json.dumps(b.params),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(block)
        blocks.append(block)

    db.commit()
    db.refresh(template)
    for blk in blocks:
        db.refresh(blk)

    umap = _username_map(db)
    warn = _weight_warning(body.blocks)
    result = _template_detail(template, blocks, umap.get(user.user_id))
    if warn:
        result["weight_warning"] = warn
    log.info("Created template id=%d name=%r owner=%s", template.id, template.name, user.user_id)
    return result


@router.get("/{template_id}")
def get_template(
    template_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    blocks = _blocks_for(template_id, db)
    umap = _username_map(db)
    return _template_detail(template, blocks, umap.get(template.owner_user_id or ""))


@router.put("/{template_id}")
def update_template(
    template_id: int,
    body: TemplateUpdateIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    if body.name is not None:
        template.name = body.name
    if body.description is not None:
        template.description = body.description
    if body.total_tracks is not None:
        template.total_tracks = body.total_tracks
    if body.is_public is not None:
        template.is_public = body.is_public
    template.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(template)
    blocks = _blocks_for(template_id, db)
    umap = _username_map(db)
    log.info("Updated template id=%d by user=%s", template_id, user.user_id)
    return _template_detail(template, blocks, umap.get(template.owner_user_id or ""))


@router.delete("/{template_id}", status_code=200)
def delete_template(
    template_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    if template.is_system:
        raise HTTPException(status_code=400, detail="System templates cannot be deleted.")

    from models import UserPlaylist
    db.query(UserPlaylist).filter(UserPlaylist.template_id == template_id).update(
        {"template_id": None}, synchronize_session=False
    )
    db.query(PlaylistBlock).filter(PlaylistBlock.template_id == template_id).delete(
        synchronize_session=False
    )
    db.delete(template)
    db.commit()
    log.info("Deleted template id=%d by user=%s", template_id, user.user_id)
    return {"ok": True, "deleted_id": template_id}


@router.post("/{template_id}/fork", status_code=201)
def fork_template(
    template_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _get_visible_template(template_id, user, db)
    source_blocks = _blocks_for(template_id, db)

    new_template = PlaylistTemplate(
        name=f"{source.name} (fork)",
        description=source.description,
        owner_user_id=user.user_id,
        is_public=False,
        is_system=False,
        forked_from_id=source.id,
        total_tracks=source.total_tracks,
        blend_mode=source.blend_mode or "weighted_shuffle",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(new_template)
    db.flush()

    new_blocks = []
    for b in source_blocks:
        new_block = PlaylistBlock(
            template_id=new_template.id,
            block_type=b.block_type,
            weight=b.weight,
            position=b.position,
            params=b.params,  # already JSON string — deep copy
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(new_block)
        new_blocks.append(new_block)

    db.commit()
    db.refresh(new_template)
    for blk in new_blocks:
        db.refresh(blk)

    umap = _username_map(db)
    log.info("Forked template id=%d → new id=%d by user=%s", template_id, new_template.id, user.user_id)
    return _template_detail(new_template, new_blocks, umap.get(user.user_id))


@router.get("/{template_id}/preview")
async def preview_template_endpoint(
    template_id: int,
    user_id: Optional[str] = Query(default=None),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_visible_template(template_id, user, db)
    if user_id is not None and user_id != user.user_id:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required to preview for another user.")
        effective_user_id = user_id
    else:
        effective_user_id = user.user_id

    # Always return 200 with either results or a plain-English error object.
    # Never let a 500 reach the client — the frontend's api.js throws on any
    # non-2xx before it can even read the body, making errors invisible.
    import traceback
    from services.playlist_engine import preview_template, PlaylistPreviewError
    try:
        return await preview_template(template_id, effective_user_id, db)
    except PlaylistPreviewError as e:
        return {"error": str(e), "error_code": e.code, "estimated_tracks": 0, "sample": []}
    except Exception as e:
        tb = traceback.format_exc()
        log.error("preview_template id=%d user=%s failed: %s", template_id, effective_user_id, tb)
        # Return the actual exception message + type so the UI can show something useful.
        # In production you may want to strip the traceback from the response.
        return {
            "error": f"{type(e).__name__}: {e}",
            "error_code": "unexpected_error",
            "estimated_tracks": 0,
            "sample": [],
            "traceback": tb,
        }


# ── Block sub-endpoints ───────────────────────────────────────────────────────

@router.post("/{template_id}/blocks", status_code=201)
def add_block(
    template_id: int,
    body: BlockIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    block = PlaylistBlock(
        template_id=template_id,
        block_type=body.block_type,
        weight=body.weight,
        position=body.position,
        params=json.dumps(body.params),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(block)
    db.commit()
    db.refresh(block)

    all_blocks = _blocks_for(template_id, db)
    warn = _weight_warning(all_blocks)
    result = _block_out(block)
    if warn:
        result["weight_warning"] = warn
    log.info("Added block id=%d to template id=%d", block.id, template_id)
    return result


@router.put("/{template_id}/blocks/{block_id}")
def update_block(
    template_id: int,
    block_id: int,
    body: BlockUpdateIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    block = db.query(PlaylistBlock).filter(
        PlaylistBlock.id == block_id,
        PlaylistBlock.template_id == template_id,
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    if body.weight is not None:
        block.weight = body.weight
    if body.position is not None:
        block.position = body.position
    if body.params is not None:
        block.params = json.dumps(body.params)
    block.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(block)

    all_blocks = _blocks_for(template_id, db)
    warn = _weight_warning(all_blocks)
    result = _block_out(block)
    if warn:
        result["weight_warning"] = warn
    return result


@router.delete("/{template_id}/blocks/{block_id}", status_code=200)
def delete_block(
    template_id: int,
    block_id: int,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    block = db.query(PlaylistBlock).filter(
        PlaylistBlock.id == block_id,
        PlaylistBlock.template_id == template_id,
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    db.delete(block)
    db.commit()
    log.info("Deleted block id=%d from template id=%d", block_id, template_id)
    return {"ok": True, "deleted_id": block_id}


@router.post("/{template_id}/blocks/reorder", status_code=200)
def reorder_blocks(
    template_id: int,
    body: BlockReorderIn,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = _get_visible_template(template_id, user, db)
    assert_owns_template(template, user)

    blocks = {b.id: b for b in _blocks_for(template_id, db)}
    for new_pos, block_id in enumerate(body.order):
        if block_id in blocks:
            blocks[block_id].position = new_pos
            blocks[block_id].updated_at = datetime.utcnow()

    db.commit()
    updated = _blocks_for(template_id, db)
