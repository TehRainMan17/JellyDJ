/**
 * TemplateCard.jsx — Template gallery card component.
 *
 * "Recipe" dropdown: description, plain-English summary sentence, and
 * block-type chips are all hidden by default behind a single toggle.
 * Block chips lazy-fetch the full template detail on first expand.
 */
import { useState } from 'react'
import { GitFork, Edit2, Trash2, Loader2, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react'
import { FILTER_TYPES } from './BlockEditor.jsx'
import { api } from '../../lib/api.js'

export default function TemplateCard({
  template,
  currentUserId,
  isAdmin,
  onUse,
  onEdit,
  onForkSuccess,
  onDeleteSuccess,
  highlighted,
}) {
  const [forking, setForking]             = useState(false)
  const [forkDone, setForkDone]           = useState(false)
  const [deleting, setDeleting]           = useState(false)
  const [confirmDel, setConfirm]          = useState(false)
  const [loadingEdit, setLoadEdit]        = useState(false)
  const [recipeOpen, setRecipeOpen]       = useState(false)
  const [detailBlocks, setDetailBlocks]   = useState(null)
  const [loadingBlocks, setLoadingBlocks] = useState(false)

  const isOwner  = template.owner_user_id === currentUserId
  const isSystem = template.is_system

  // Badge
  let badgeLabel, badgeColor, badgeBg
  if (isSystem) {
    badgeLabel = 'System'; badgeColor = 'var(--text-secondary)'; badgeBg = 'rgba(255,255,255,0.06)'
  } else if (isOwner) {
    badgeLabel = 'You'; badgeColor = 'var(--accent)'; badgeBg = 'var(--accent-soft)'
  } else {
    const hue = (template.owner_username || '').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360
    badgeLabel = template.owner_username || 'Unknown'
    badgeColor = `hsl(${hue},55%,60%)`
    badgeBg    = `hsla(${hue},55%,60%,0.1)`
  }

  const handleEdit = async () => {
    setLoadEdit(true)
    try {
      const full = await api.get(`/api/playlist-templates/${template.id}`)
      onEdit(full)
    } catch {
      onEdit(template)
    } finally {
      setLoadEdit(false)
    }
  }

  const handleFork = async () => {
    setForking(true)
    try {
      const forked = await api.post(`/api/playlist-templates/${template.id}/fork`)
      setForkDone(true)
      setTimeout(() => setForkDone(false), 3000)
      onForkSuccess(forked)
    } catch (e) {
      alert(`Fork failed: ${e.message}`)
    } finally {
      setForking(false)
    }
  }

  const handleDelete = async () => {
    if (!confirmDel) { setConfirm(true); return }
    setDeleting(true)
    try {
      await api.delete(`/api/playlist-templates/${template.id}`)
      onDeleteSuccess(template.id)
    } catch (e) {
      alert(`Delete failed: ${e.message}`)
      setDeleting(false)
      setConfirm(false)
    }
  }

  const handleRecipeToggle = async () => {
    const next = !recipeOpen
    setRecipeOpen(next)
    if (next && !detailBlocks && !loadingBlocks && template.block_count > 0) {
      setLoadingBlocks(true)
      try {
        const full = await api.get(`/api/playlist-templates/${template.id}`)
        setDetailBlocks(full.blocks || [])
      } catch {
        setDetailBlocks([])
      } finally {
        setLoadingBlocks(false)
      }
    }
  }

  const canEdit   = (isOwner || isAdmin) && !isSystem
  const canDelete = (isOwner || isAdmin) && !isSystem
  const hasRecipe = template.summary || template.block_count > 0
  // description is always shown above the toggle, not inside it

  return (
    <div
      className="card flex flex-col gap-3 anim-fade-up transition-all"
      style={highlighted ? { borderColor: 'rgba(83,236,252,0.4)', boxShadow: '0 0 0 2px rgba(83,236,252,0.12)' } : {}}
    >
      {/* Top row: name + badge */}
      <div className="flex items-start gap-2">
        <div className="text-sm font-semibold truncate flex-1 min-w-0" style={{ color: 'var(--text-primary)' }}>
          {template.name}
        </div>
        <span
          className="flex-shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full"
          style={{ background: badgeBg, color: badgeColor, border: `1px solid ${badgeColor}30` }}
        >
          {isSystem ? 'System' : badgeLabel}
        </span>
      </div>

      {/* Description — always visible */}
      {template.description && (
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {template.description}
        </div>
      )}

      {/* Stats row */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          <span style={{ color: 'var(--text-secondary)' }}>{template.block_count}</span> block{template.block_count !== 1 ? 's' : ''}
          {' · '}
          <span style={{ color: 'var(--text-secondary)' }}>{template.total_tracks}</span> tracks
        </span>
        {template.forked_from_id && (
          <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--purple)' }}>
            <GitFork size={9} /> forked
          </span>
        )}
      </div>

      {/* Recipe toggle */}
      {hasRecipe && (
        <div>
          <button
            onClick={handleRecipeToggle}
            className="flex items-center gap-1 text-[10px] transition-colors"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-secondary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          >
            {loadingBlocks
              ? <Loader2 size={10} className="animate-spin" />
              : recipeOpen ? <ChevronUp size={10} /> : <ChevronDown size={10} />
            }
            {recipeOpen ? 'Hide' : 'Show'} recipe
          </button>

          {recipeOpen && (
            <div className="mt-2 space-y-2 anim-fade-in">
              {template.summary && (
                <div
                  className="text-[11px] leading-relaxed px-2.5 py-2 rounded-lg"
                  style={{ background: 'var(--bg-overlay)', color: 'var(--text-secondary)', borderLeft: '2px solid var(--border-mid)' }}
                >
                  {template.summary}
                </div>
              )}

              {detailBlocks && detailBlocks.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {detailBlocks.map((b, i) => {
                    const cfg = FILTER_TYPES[b.block_type] || { label: b.block_type, color: 'var(--text-muted)' }
                    return (
                      <span
                        key={i}
                        className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                        style={{ background: `${cfg.color}12`, color: cfg.color, border: `1px solid ${cfg.color}20` }}
                      >
                        {cfg.label} {b.weight}%
                      </span>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-1.5 mt-auto pt-1">
        <button onClick={() => onUse(template)} className="btn-primary text-xs py-1.5 flex-1">
          Use Template
        </button>

        {canEdit && (
          <button
            onClick={handleEdit}
            disabled={loadingEdit}
            className="btn-secondary text-xs py-1.5 px-2.5"
            title="Edit template"
          >
            {loadingEdit ? <Loader2 size={11} className="animate-spin" /> : <Edit2 size={11} />}
          </button>
        )}

        <button
          onClick={handleFork}
          disabled={forking || forkDone}
          className="btn-secondary text-xs py-1.5 px-2.5"
          title="Fork template"
        >
          {forkDone
            ? <CheckCircle2 size={11} style={{ color: 'var(--accent)' }} />
            : forking
            ? <Loader2 size={11} className="animate-spin" />
            : <GitFork size={11} />
          }
        </button>

        {canDelete && (
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="btn-secondary text-xs py-1.5 px-2.5"
            title={confirmDel ? 'Click again to confirm' : 'Delete template'}
            style={confirmDel ? { borderColor: 'rgba(248,113,113,0.4)', color: 'var(--danger)' } : {}}
          >
            {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
          </button>
        )}
      </div>

      {confirmDel && !deleting && (
        <div
          className="text-xs px-3 py-2 rounded-lg anim-scale-in flex items-center justify-between gap-2"
          style={{ background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.2)' }}
        >
          <span style={{ color: 'var(--danger)' }}>Permanently delete this template?</span>
          <button
            className="text-[10px] font-medium"
            onClick={() => setConfirm(false)}
            style={{ color: 'var(--text-muted)' }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
