/**
 * BlockEditor.jsx
 *
 * Full-screen dedicated window for creating/editing a playlist template.
 * Renders as a fixed overlay covering the entire viewport.
 *
 * Fixes applied:
 *  1. Genre editor: /api/insights/genres returns [{genre, ...}] objects — extract .genre string
 *     and pass user_id so the endpoint doesn't 400.
 *  2. Artist editor: pass user_id, show scrollable image/name cards in addition to search,
 *     fetch all pages up to 500 artists.
 *  3. Template open/fork: consumer (TemplateCard/Playlists) now fetches full detail before
 *     opening editor — BlockEditor chainFromBlock rehydration path is preserved.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  X, Plus, Save, Eye, Loader2, CheckCircle2, Search,
  Sparkles, Radio, TrendingUp, Clock, Globe, Star, Users, Tag,
  ChevronUp, ChevronDown, Trash2, Music2, Shuffle, AlertCircle
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import { api } from '../../lib/api'

// ── Filter type catalogue ─────────────────────────────────────────────────────

export const FILTER_TYPES = {
  final_score: {
    label: 'Final Score', icon: Sparkles, color: 'var(--accent)',
    oneliner: 'Your personal blended score for every track (0–99)',
    desc: [
      'Every track has a personal score combining: how often you play it, how recently you played it, how much you skip it, whether you\'ve favourited it, and its global streaming popularity.',
      'Use the range slider to target any band you want — your absolute best tracks, your guilty pleasures, tracks you\'ve never touched, or even the ones you keep skipping.',
      { '95–99': 'Favourites / max-played top-artist tracks' },
      { '87–94': 'Heavily played / top-affinity artists' },
      { '77–86': 'Frequently played' },
      { '63–76': 'Regularly played' },
      { '46–62': 'Occasionally played' },
      { '38–45': 'Barely liked' },
      { '0–37':  'Unplayed, buried, or heavily skipped' },
    ],
  },
  play_recency: {
    label: 'Play Recency', icon: Clock, color: '#fbbf24',
    oneliner: 'Filter by how long ago you last played a track',
    desc: [
      '"Within last N days" keeps the pool to tracks you\'ve played recently — great for a Fresh Listens playlist. "More than N days ago" surfaces tracks you\'ve been neglecting — great for rediscovery.',
      'Pairs naturally with a Final Score AND child to get recently-played tracks that are also highly rated.',
    ],
  },
  genre: {
    label: 'Genre', icon: Tag, color: '#34d399',
    oneliner: 'Match tracks by genre — leave empty for all genres',
    desc: [
      'Filters to tracks whose genre tag matches your selected list. Leave the list empty to pass all genres through (useful as a base when you only want AND children like a score range).',
      'Genres come from your Jellyfin library metadata. Tracks with no genre tag won\'t match any genre filter.',
    ],
  },
  artist: {
    label: 'Artist', icon: Users, color: '#fb923c',
    oneliner: 'Match tracks by artist — leave empty for all artists',
    desc: [
      'Filters to tracks from your selected artists. Leave empty to include everyone — useful when combining with AND children like Play Recency or Final Score.',
      'Select multiple artists to build a "best of several artists" chain, or combine with an Artist Cap AND child to keep any one artist from dominating.',
    ],
  },
  play_count: {
    label: 'Play Count', icon: TrendingUp, color: '#f87171',
    oneliner: 'Filter by lifetime play count',
    desc: [
      'Matches tracks whose total play count is within your min–max range. Set max low (e.g. 0–3) to surface rarely-played tracks. Set min high (e.g. 20+) to get your most-played songs.',
      'Play count is the raw lifetime total, not plays per week. A track played 50 times years ago still counts as 50.',
    ],
  },
  discovery: {
    label: 'Discovery', icon: Radio, color: '#f472b6',
    oneliner: 'Mix unheard tracks by how familiar you are with the artist',
    desc: [
      'Buckets unplayed tracks by artist familiarity: Strangers (artists you\'ve never played), Acquaintances (played a handful of times), and Familiar (regular listens). Then takes a proportional slice from each.',
      'Crank up Stranger % for maximum exploration. Lean on Familiar % for safe "sounds like what I already love" discovery. The acquaintance threshold is how many total plays makes an artist familiar.',
    ],
  },
  global_popularity: {
    label: 'Global Popularity', icon: Globe, color: '#60a5fa',
    oneliner: 'Filter by worldwide streaming popularity (0 = obscure, 100 = massive)',
    desc: [
      'Scores tracks 0–100 based on aggregated listener and play data from Last.fm and Spotify. 100 = globally massive chart hit. 0 = nearly undiscovered.',
      'Combine with a high Final Score range to find hidden gems your taste profile loves that the world hasn\'t caught onto yet. Or use a high popularity range to build a crowd-pleasing mainstream playlist.',
    ],
  },
  affinity: {
    label: 'Affinity Range', icon: Star, color: '#a78bfa',
    oneliner: 'Filter by your artist + genre taste alignment score',
    desc: [
      'Affinity (0–100) measures how well a track matches your taste at the artist and genre level — independent of how many times you\'ve played that specific track. A track you\'ve never heard from a beloved artist in a beloved genre can still score high affinity.',
      'Most useful for finding unplayed tracks that fit your taste perfectly, or building a playlist from artists you love without repeating over-played favourites.',
    ],
  },
  favorites: {
    label: 'Favorites Only', icon: Star, color: '#fde68a',
    oneliner: 'Only tracks you\'ve explicitly marked as favourites',
    desc: [
      'A pure pass-through filter — only tracks with a Jellyfin favourite flag pass through. No parameters needed.',
      'Stack with AND children to do things like "my favourite tracks I haven\'t played in over a year" or "my favourite tracks from a specific genre".',
    ],
  },
  played_status: {
    label: 'Played Status', icon: TrendingUp, color: '#94a3b8',
    oneliner: 'Narrow to played or unplayed tracks only',
    desc: [
      'A simple pass-through filter. "Played" keeps only tracks you\'ve heard at least once. "Unplayed" keeps only tracks you\'ve never played.',
      'Add as an AND child to any other block to apply the constraint to just that block\'s results. For example: Final Score (score 70–99) AND Played Status (unplayed) = high-affinity tracks you haven\'t explored yet.',
    ],
  },
  cooldown: {
    label: 'Cooldown Filter', icon: Clock, color: '#f87171',
    oneliner: "Exclude tracks you've been skipping (active skip-cooldown)",
    desc: [
      "Removes tracks that are currently on a skip-cooldown — songs you've skipped multiple times recently and the system has temporarily suppressed.",
      'Add as an AND child to any block to keep punished tracks out of that chain. Without this, a play-count or recency block might still surface songs you keep skipping.',
      'The "exclude_active" mode (default) hides cooled-down tracks. "only_active" does the opposite and surfaces only the skip pile — useful for auditing.',
    ],
  },
  artist_cap: {
    label: 'Artist Cap', icon: Users, color: '#94a3b8',
    oneliner: 'Limit how many tracks per artist appear in this chain',
    desc: [
      'After all other filters run, caps how many tracks from any one artist can appear in this chain\'s share of the playlist. Without this, a chain heavy in one artist\'s catalogue will naturally pull a lot of them.',
      'Add as an AND child. The cap applies to this chain only — other chains in the same playlist are unaffected.',
    ],
  },
  jitter: {
    label: 'Jitter', icon: Shuffle, color: '#c084fc',
    oneliner: 'Randomise track ordering so every generation feels different',
    desc: [
      'Nudges each track\'s score by a small random amount before sorting. Without jitter, the same filters always produce the same tracks in the same order every time you generate.',
      'Higher jitter = more chaos. At 30% a track scored 80 can rank anywhere from ~56 to ~99. At 5% the top tracks stay near the top but the exact order varies. Add as an AND child to whichever block you want to randomise.',
    ],
  },
}

const DEFAULT_PARAMS = {
  final_score:       { score_min: 0, score_max: 99 },
  play_recency:      { mode: 'within', days: 30 },
  genre:             { genres: [] },
  artist:            { artists: [] },
  play_count:        { play_count_min: 0, play_count_max: 500 },
  discovery:         { stranger_pct: 34, acquaintance_pct: 33, familiar_pct: 33 },
  global_popularity: { popularity_min: 0, popularity_max: 100 },
  affinity:          { affinity_min: 0, affinity_max: 100 },
  favorites:         {},
  played_status:     { played_filter: 'unplayed' },
  artist_cap:        { max_per_artist: 3 },
  jitter:            { jitter_pct: 0.15 },
  cooldown:          { mode: 'exclude_active' },
}

let _uid = 5000
function uid() { return String(++_uid) }
function makeNode(filter_type) {
  return { _id: uid(), filter_type, params: { ...(DEFAULT_PARAMS[filter_type] ?? {}) }, children: [] }
}
let _cid = 9000
function makeChain(filter_type = 'final_score') {
  return { _id: String(++_cid), weight: 50, filter_tree: [makeNode(filter_type)], dbId: null }
}
function chainFromBlock(block) {
  let filter_tree
  if (block.params?.filter_tree) {
    filter_tree = _rehydrate(block.params.filter_tree)
  } else {
    // Legacy flat-params block (pre-filter_tree format).
    // Merge DEFAULT_PARAMS so score_min/score_max (and all other typed keys)
    // are always present in the node params. Without this, the backend falls
    // back to full-range defaults and any range the user set is silently ignored.
    const defaults = DEFAULT_PARAMS[block.block_type] ?? {}
    const mergedParams = { ...defaults, ...(block.params ?? {}) }
    filter_tree = [{ _id: uid(), filter_type: block.block_type, params: mergedParams, children: [] }]
  }
  return { _id: `db_${block.id}`, weight: block.weight, filter_tree, dbId: block.id }
}
function _rehydrate(nodes) {
  return (nodes || []).map(n => ({ ...n, _id: n._id || uid(), children: _rehydrate(n.children) }))
}
function chainLabel(chain) {
  const tree = chain.filter_tree || []
  if (tree.length === 0) return 'Empty chain'
  return tree.map(n => FILTER_TYPES[n.filter_type]?.label ?? n.filter_type).join(' / ')
}
function chainColor(chain) {
  return FILTER_TYPES[chain.filter_tree?.[0]?.filter_type]?.color ?? 'var(--text-muted)'
}
function chainBlockType(chain) {
  return chain.filter_tree?.[0]?.filter_type ?? 'final_score'
}

// ── FilterPickerModal — portal, never clipped ─────────────────────────────────

function FilterPickerModal({ title, onPick, onClose }) {
  const [q, setQ] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 40)
    const esc = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const entries = Object.entries(FILTER_TYPES).filter(([, c]) =>
    !q.trim() || c.label.toLowerCase().includes(q.toLowerCase()) || (c.oneliner ?? '').toLowerCase().includes(q.toLowerCase())
  )

  return createPortal(
    <div className="fixed inset-0 flex items-center justify-center"
      style={{ zIndex: 99999, background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}>
      <div className="flex flex-col rounded-2xl shadow-2xl"
        style={{ width: 580, maxWidth: '96vw', maxHeight: '82vh', background: 'var(--bg-elevated)', border: '1px solid var(--border-mid)' }}
        onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex-1">
            <div className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{title}</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>Hover for description · click to add</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
            style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)' }}>
            <Search size={13} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            <input ref={inputRef} placeholder="Search filter types…" value={q}
              onChange={e => setQ(e.target.value)}
              className="flex-1 bg-transparent border-0 text-xs focus:outline-none"
              style={{ color: 'var(--text-primary)' }} />
          </div>
        </div>

        {/* Grid */}
        <div className="overflow-y-auto p-4 grid grid-cols-2 gap-2">
          {entries.map(([type, cfg]) => {
            const Icon = cfg.icon
            return (
              <button key={type}
                onClick={() => { onPick(type); onClose() }}
                className="flex items-start gap-3 p-3.5 rounded-xl text-left group transition-all duration-150"
                style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
                onMouseEnter={e => {
                  e.currentTarget.style.borderColor = `${cfg.color}70`
                  e.currentTarget.style.background = `color-mix(in srgb, ${cfg.color} 7%, var(--bg-surface))`
                  e.currentTarget.style.transform = 'translateY(-1px)'
                  e.currentTarget.style.boxShadow = `0 4px 16px ${cfg.color}20`
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.borderColor = 'var(--border)'
                  e.currentTarget.style.background = 'var(--bg-surface)'
                  e.currentTarget.style.transform = ''
                  e.currentTarget.style.boxShadow = ''
                }}>
                <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
                  style={{ background: `${cfg.color}18`, border: `1px solid ${cfg.color}35` }}>
                  <Icon size={16} style={{ color: cfg.color }} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>{cfg.label}</div>
                  <div className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{cfg.oneliner}</div>
                </div>
              </button>
            )
          })}
          {entries.length === 0 && (
            <div className="col-span-2 text-center py-10" style={{ color: 'var(--text-muted)' }}>
              No filters match "{q}"
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

// ── Shared UI primitives ──────────────────────────────────────────────────────

function RangeInputs({ label, lo, hi, min = 0, max = 100, step = 1, onLo, onHi, unit = '' }) {
  const trackRef = useRef(null)
  const dragging = useRef(null) // 'lo' | 'hi' | null
  const span = max - min || 1
  const lp = ((lo - min) / span) * 100
  const rp = ((hi - min) / span) * 100
  const snap = v => Math.round(v / step) * step
  const clLo = v => snap(Math.max(min, Math.min(v, hi)))
  const clHi = v => snap(Math.min(max, Math.max(v, lo)))

  function valueFromEvent(e) {
    const rect = trackRef.current.getBoundingClientRect()
    const clientX = e.touches ? e.touches[0].clientX : e.clientX
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return min + pct * span
  }

  function onTrackDown(e) {
    e.preventDefault()
    const val = valueFromEvent(e)
    const distLo = Math.abs(val - lo)
    const distHi = Math.abs(val - hi)
    dragging.current = (distLo <= distHi) ? 'lo' : 'hi'
    moveTo(val)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onUp)
  }

  function onMove(e) {
    if (!dragging.current) return
    e.preventDefault()
    moveTo(valueFromEvent(e))
  }

  function onUp() {
    dragging.current = null
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    window.removeEventListener('touchmove', onMove)
    window.removeEventListener('touchend', onUp)
  }

  function moveTo(val) {
    if (dragging.current === 'lo') onLo(clLo(val))
    else onHi(clHi(val))
  }

  return (
    <div>
      {label && <div className="section-label mb-2">{label}</div>}
      <div ref={trackRef} className="relative mb-3 select-none" style={{ height: 20, cursor: 'pointer' }}
        onMouseDown={onTrackDown} onTouchStart={onTrackDown}>
        <div className="absolute rounded-full pointer-events-none"
          style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
        <div className="absolute rounded-full pointer-events-none"
          style={{ top: 8, height: 4, left: `${lp}%`, width: `${Math.max(0, rp - lp)}%`, background: 'var(--accent)', opacity: 0.8 }} />
        <div className="absolute pointer-events-none rounded-full border-2"
          style={{ top: 2, width: 16, height: 16, left: `calc(${lp}% - 8px)`, background: 'var(--accent)', borderColor: 'var(--bg)', boxShadow: '0 0 0 3px rgba(83,236,252,0.2)', zIndex: 2, transition: dragging.current ? 'none' : 'left 0.05s' }} />
        <div className="absolute pointer-events-none rounded-full border-2"
          style={{ top: 2, width: 16, height: 16, left: `calc(${rp}% - 8px)`, background: 'var(--accent)', borderColor: 'var(--bg)', boxShadow: '0 0 0 3px rgba(83,236,252,0.2)', zIndex: 2, transition: dragging.current ? 'none' : 'left 0.05s' }} />
      </div>
      <div className="flex items-center gap-2">
        <input type="number" min={min} max={max} step={step} value={lo}
          onChange={e => onLo(clLo(Number(e.target.value)))} className="input w-16 text-center text-xs" />
        <div className="flex-1 text-center text-xs" style={{ color: 'var(--text-muted)' }}>to</div>
        <input type="number" min={min} max={max} step={step} value={hi}
          onChange={e => onHi(clHi(Number(e.target.value)))} className="input w-16 text-center text-xs" />
        {unit && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </div>
    </div>
  )
}

function Slider({ label, value, min, max, step = 1, onChange, unit = '', hint = '' }) {
  const v = value ?? min
  const pct = ((v - min) / (max - min || 1)) * 100
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="section-label">{label}</span>
        <span className="text-xs font-mono font-semibold" style={{ color: 'var(--accent)' }}>
          {v}{unit}
          {hint && <span className="font-normal ml-1.5" style={{ color: 'var(--text-muted)', fontFamily: 'inherit' }}>{hint}</span>}
        </span>
      </div>
      <div className="relative mb-2" style={{ height: 20 }}>
        <div className="absolute rounded-full" style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
        <div className="absolute rounded-full" style={{ top: 8, height: 4, left: 0, width: `${pct}%`, background: 'var(--accent)', opacity: 0.7 }} />
        <input type="range" min={min} max={max} step={step} value={v} onChange={e => onChange(Number(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%', zIndex: 3 }} />
        <div className="absolute pointer-events-none rounded-full border-2"
          style={{ top: 2, width: 16, height: 16, left: `calc(${pct}% - 8px)`, background: 'var(--accent)', borderColor: 'var(--bg)', zIndex: 4 }} />
      </div>
      <input type="number" min={min} max={max} step={step} value={v}
        onChange={e => onChange(Math.max(min, Math.min(max, Number(e.target.value))))}
        className="input w-20 text-center text-xs" />
    </div>
  )
}

function Chips({ value, options, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(({ v, label }) => (
        <button key={v} onClick={() => onChange(v)}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
          style={{
            background: value === v ? 'var(--accent-soft)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${value === v ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
            color: value === v ? 'var(--accent)' : 'var(--text-secondary)',
          }}>
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Param editors ─────────────────────────────────────────────────────────────

const Editors = {

  final_score: ({ p, set }) => {
    const lo = p.score_min ?? 0
    const hi = p.score_max ?? 99
    const tier =
      lo === 0 && hi === 99 ? 'Full range' :
      hi <= 20  ? 'Buried / permanent dislikes' :
      hi <= 45  ? 'Barely liked' :
      lo >= 95  ? 'Favourites & top tracks' :
      lo >= 87  ? 'Heavily played' :
      lo >= 77  ? 'Frequently played' :
      lo >= 63  ? 'Regularly played' :
      hi <= 62  ? 'Occasionally played or below' :
                  'Custom range'
    return (
      <RangeInputs
        label={`Score range · ${tier}`}
        lo={lo} hi={hi} min={0} max={99} step={1}
        onLo={v => set({ ...p, score_min: v })}
        onHi={v => set({ ...p, score_max: v })}
      />
    )
  },

  play_recency: ({ p, set }) => (
    <div className="space-y-4">
      <div>
        <div className="section-label mb-1.5">Mode</div>
        <Chips value={p.mode ?? 'within'} onChange={v => set({ ...p, mode: v })}
          options={[{ v: 'within', label: 'Played within last…' }, { v: 'older', label: 'Not played in last…' }]} />
      </div>
      <Slider label="Days" value={p.days ?? 30} min={1} max={365} step={1}
        onChange={v => set({ ...p, days: v })} unit=" days" />
    </div>
  ),

  genre: ({ p, set, genres }) => {
    const [q, setQ] = useState('')
    const sel = p.genres ?? []
    const filtered = q.trim() ? genres.filter(g => g.toLowerCase().includes(q.toLowerCase())) : genres
    return (
      <div className="space-y-2">
        <div className="section-label mb-1">Genres <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(empty = all)</span></div>
        {sel.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {sel.map(g => (
              <button key={g} onClick={() => set({ ...p, genres: sel.filter(x => x !== g) })}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.3)', color: '#34d399' }}>
                {g} ×
              </button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
          style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)' }}>
          <Search size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          <input placeholder="Search genres…" value={q} onChange={e => setQ(e.target.value)}
            className="flex-1 bg-transparent border-0 text-xs focus:outline-none" style={{ color: 'var(--text-primary)' }} />
        </div>
        {genres.length === 0 ? (
          <div className="flex items-center gap-2 py-3 px-3 rounded-lg text-xs"
            style={{ background: 'var(--bg-overlay)', color: 'var(--text-muted)' }}>
            <Tag size={12} /> No genres found — index your library first.
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto pr-1">
            {filtered.map(g => {
              const active = sel.includes(g)
              return (
                <button key={g}
                  onClick={() => set({ ...p, genres: active ? sel.filter(x => x !== g) : [...sel, g] })}
                  className="px-2 py-0.5 rounded-full text-xs font-medium transition-all"
                  style={{
                    background: active ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.04)',
                    border: `1px solid ${active ? 'rgba(52,211,153,0.4)' : 'var(--border)'}`,
                    color: active ? '#34d399' : 'var(--text-secondary)',
                  }}>
                  {g}
                </button>
              )
            })}
            {filtered.length === 0 && q.trim() && (
              <span className="text-xs px-1" style={{ color: 'var(--text-muted)' }}>No genres match "{q}"</span>
            )}
          </div>
        )}
      </div>
    )
  },

  artist: ({ p, set, artists }) => {
    const [q, setQ] = useState('')
    const sel = p.artists ?? []
    const artistObjects = artists.map(a =>
      typeof a === 'string' ? { name: a } : { name: a.name ?? a.artist_name ?? '', ...a }
    ).filter(a => a.name)
    const filtered = q.trim() ? artistObjects.filter(a => a.name.toLowerCase().includes(q.toLowerCase())) : artistObjects
    const toggle = name => set({ ...p, artists: sel.includes(name) ? sel.filter(x => x !== name) : [...sel, name] })
    return (
      <div className="space-y-2">
        <div className="section-label mb-1">Artists <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(empty = all)</span></div>
        {sel.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {sel.map(a => (
              <button key={a} onClick={() => toggle(a)}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ background: 'rgba(251,146,60,0.12)', border: '1px solid rgba(251,146,60,0.3)', color: '#fb923c' }}>
                {a} ×
              </button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
          style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)' }}>
          <Search size={11} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          <input placeholder="Search artists…" value={q} onChange={e => setQ(e.target.value)}
            className="flex-1 bg-transparent border-0 text-xs focus:outline-none" style={{ color: 'var(--text-primary)' }} />
          {q && <button onClick={() => setQ('')} style={{ color: 'var(--text-muted)' }}><X size={10} /></button>}
        </div>
        {artistObjects.length === 0 ? (
          <div className="flex items-center gap-2 py-3 px-3 rounded-lg text-xs"
            style={{ background: 'var(--bg-overlay)', color: 'var(--text-muted)' }}>
            <Users size={12} /> No artists found — index your library first.
          </div>
        ) : (
          <div className="overflow-y-auto rounded-xl" style={{ maxHeight: 220, border: '1px solid var(--border)' }}>
            <div className="grid grid-cols-2 gap-1 p-1.5">
              {filtered.slice(0, 80).map(a => {
                const active = sel.includes(a.name)
                const hue = a.name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360
                return (
                  <button key={a.name} onClick={() => toggle(a.name)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-all"
                    style={{ background: active ? 'rgba(251,146,60,0.12)' : 'transparent', border: `1px solid ${active ? 'rgba(251,146,60,0.35)' : 'transparent'}` }}
                    onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                    onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}>
                    <div className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold"
                      style={{ background: `hsla(${hue},55%,45%,0.25)`, border: `1px solid hsla(${hue},55%,55%,0.4)`, color: `hsl(${hue},70%,70%)` }}>
                      {a.name.charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-semibold truncate" style={{ color: active ? '#fb923c' : 'var(--text-primary)' }}>{a.name}</div>
                      {a.primary_genre && <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{a.primary_genre}</div>}
                    </div>
                    {a.has_favorite && <Star size={9} style={{ color: '#fde68a', flexShrink: 0 }} />}
                  </button>
                )
              })}
            </div>
            {filtered.length > 80 && (
              <div className="text-center py-2 text-[10px]" style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}>
                Showing 80 of {filtered.length} — search to narrow down
              </div>
            )}
            {filtered.length === 0 && q.trim() && (
              <div className="text-xs px-3 py-3 text-center" style={{ color: 'var(--text-muted)' }}>No artists match "{q}"</div>
            )}
          </div>
        )}
      </div>
    )
  },

  play_count: ({ p, set }) => (
    <RangeInputs label="Lifetime play count range"
      lo={p.play_count_min ?? 0} hi={p.play_count_max ?? 500} min={0} max={500}
      onLo={v => set({ ...p, play_count_min: v })} onHi={v => set({ ...p, play_count_max: v })} unit="plays" />
  ),

  discovery: ({ p, set }) => {
    const s = p.stranger_pct ?? 34, a = p.acquaintance_pct ?? 33, f = p.familiar_pct ?? 33
    const total = s + a + f
    const warn = Math.abs(total - 100) > 1
    const tiers = [
      { key: 'stranger_pct',     label: 'Stranger',     color: '#f472b6', hint: 'Never heard of them' },
      { key: 'acquaintance_pct', label: 'Acquaintance', color: '#fb923c', hint: 'Played a handful of times' },
      { key: 'familiar_pct',     label: 'Familiar',     color: '#34d399', hint: 'Regular listens' },
    ]
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="section-label">Familiarity mix</span>
          <span className="text-xs font-mono" style={{ color: warn ? 'var(--danger)' : 'var(--text-muted)' }}>
            {total}/100{warn && ' ← should equal 100'}
          </span>
        </div>
        <div className="flex h-2 rounded-full overflow-hidden gap-px">
          {tiers.map(({ key, color }) => (
            <div key={key} style={{ width: `${p[key] ?? 33}%`, background: color, transition: 'width 0.15s' }} />
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {tiers.map(({ key, label, color, hint }) => {
            const val = p[key] ?? 33
            return (
              <div key={key}>
                <div className="flex items-center gap-1 mb-0.5">
                  <div className="w-2 h-2 rounded-full" style={{ background: color }} />
                  <span className="text-[10px] font-semibold" style={{ color }}>{label}</span>
                </div>
                <div className="text-[9px] mb-1.5" style={{ color: 'var(--text-muted)' }}>{hint}</div>
                <div className="relative mb-1" style={{ height: 16 }}>
                  <div className="absolute rounded-full" style={{ top: 6, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
                  <div className="absolute rounded-full" style={{ top: 6, height: 4, left: 0, width: `${val}%`, background: color, opacity: 0.7 }} />
                  <input type="range" min={0} max={100} step={1} value={val}
                    onChange={e => set({ ...p, [key]: Number(e.target.value) })}
                    className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%' }} />
                  <div className="absolute pointer-events-none rounded-full border-2"
                    style={{ top: 0, width: 16, height: 16, left: `calc(${val}% - 8px)`, background: color, borderColor: 'var(--bg)' }} />
                </div>
                <div className="flex items-center gap-0.5">
                  <input type="number" min={0} max={100} value={val}
                    onChange={e => set({ ...p, [key]: Math.max(0, Math.min(100, Number(e.target.value))) })}
                    className="input w-full text-center text-xs" style={warn ? { borderColor: 'rgba(248,113,113,0.4)' } : {}} />
                  <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>%</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    )
  },

  global_popularity: ({ p, set }) => (
    <RangeInputs label="Popularity range  (0 = obscure · 100 = globally massive)"
      lo={p.popularity_min ?? 0} hi={p.popularity_max ?? 100} min={0} max={100}
      onLo={v => set({ ...p, popularity_min: v })} onHi={v => set({ ...p, popularity_max: v })} />
  ),

  affinity: ({ p, set }) => (
    <RangeInputs label="Affinity range  (0 = no fit · 100 = perfect taste match)"
      lo={p.affinity_min ?? 0} hi={p.affinity_max ?? 100} min={0} max={100}
      onLo={v => set({ ...p, affinity_min: v })} onHi={v => set({ ...p, affinity_max: v })} />
  ),

  favorites: () => (
    <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
      No settings needed — passes only tracks you've explicitly favourited in Jellyfin.
    </p>
  ),

  played_status: ({ p, set }) => (
    <Chips value={p.played_filter ?? 'unplayed'} onChange={v => set({ ...p, played_filter: v })}
      options={[{ v: 'played', label: 'Played before' }, { v: 'unplayed', label: 'Never played' }]} />
  ),

  artist_cap: ({ p, set }) => (
    <Slider label="Max tracks per artist" value={p.max_per_artist ?? 3} min={1} max={20} step={1}
      onChange={v => set({ ...p, max_per_artist: v })} />
  ),

  cooldown: ({ p, set }) => (
    <div className="space-y-2">
      <div className="section-label mb-1.5">Mode</div>
      <Chips value={p.mode ?? 'exclude_active'} onChange={v => set({ ...p, mode: v })}
        options={[
          { v: 'exclude_active', label: 'Exclude cooldown tracks' },
          { v: 'only_active',    label: 'Only cooldown tracks' },
        ]} />
      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
        "Exclude" removes tracks currently on a skip-cooldown. Use as an AND child to keep skip-punished songs out of any chain.
      </p>
    </div>
  ),

  jitter: ({ p, set }) => {
    const pct = p.jitter_pct ?? 0.15
    const hint =
      pct === 0   ? '— off (strict ordering)' :
      pct <= 0.05 ? '— tiny nudge' :
      pct <= 0.15 ? '— gentle shuffle' :
      pct <= 0.25 ? '— noticeable variety' :
                    '— high chaos'
    return (
      <Slider label="Randomness" value={Math.round(pct * 100)} min={0} max={30} step={1}
        onChange={v => set({ ...p, jitter_pct: v / 100 })} unit="%" hint={hint} />
    )
  },

}

function AddFilterButton({ label, accentColor, onAdd }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg transition-all"
        style={{ color: accentColor, background: `${accentColor}12`, border: `1px dashed ${accentColor}55` }}
        onMouseEnter={e => e.currentTarget.style.background = `${accentColor}22`}
        onMouseLeave={e => e.currentTarget.style.background = `${accentColor}12`}>
        <Plus size={12} /> {label}
      </button>
      {open && <FilterPickerModal title={label} onPick={onAdd} onClose={() => setOpen(false)} />}
    </>
  )
}

// ── BlockDesc — collapsible descriptor shown inside every node ────────────────

function BlockDesc({ cfg }) {
  const [open, setOpen] = useState(false)
  const desc = cfg.desc ?? []
  if (desc.length === 0) return null

  const paragraphs = desc.filter(item => typeof item === 'string')
  const tableRows  = desc.filter(item => typeof item === 'object' && !Array.isArray(item))

  return (
    <div>
      <button onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 text-[10px] font-semibold transition-colors"
        style={{ color: open ? cfg.color : 'var(--text-muted)' }}
        onMouseEnter={e => e.currentTarget.style.color = cfg.color}
        onMouseLeave={e => e.currentTarget.style.color = open ? cfg.color : 'var(--text-muted)'}>
        {open ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        {open ? 'Hide info' : 'How does this work?'}
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {paragraphs.map((t, i) => <p key={i}>{t}</p>)}
          {tableRows.length > 0 && (
            <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
              {tableRows.map((row, i) => {
                const [range, label] = Object.entries(row)[0]
                return (
                  <div key={i} className="flex items-center gap-3 px-3 py-1.5"
                    style={{ background: 'var(--bg-overlay)', borderBottom: i < tableRows.length - 1 ? '1px solid var(--border)' : 'none' }}>
                    <span className="font-mono text-[10px] w-12 flex-shrink-0 text-right"
                      style={{ color: cfg.color, opacity: 0.85 }}>{range}</span>
                    <span style={{ color: 'var(--text-muted)' }}>{label}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── FilterNode ─────────────────────────────────────────────────────────────────

function FilterNode({ node, isFirst, depth = 0, onUpdate, onDelete, genres, artists }) {
  const [collapsed, setCollapsed] = useState(false)
  const cfg    = FILTER_TYPES[node.filter_type] ?? { label: node.filter_type, icon: Tag, color: 'var(--text-muted)', oneliner: '', desc: [] }
  const Icon   = cfg.icon
  const Editor = Editors[node.filter_type]
  const children = node.children ?? []

  function setParams(params) { onUpdate({ ...node, params }) }
  function addChild(ft) { onUpdate({ ...node, children: [...children, makeNode(ft)] }) }
  function updateChild(i, u) { const c = [...children]; c[i] = u; onUpdate({ ...node, children: c }) }
  function deleteChild(i) { onUpdate({ ...node, children: children.filter((_, j) => j !== i) }) }

  return (
    <div>
      {/* OR badge between siblings */}
      {!isFirst && (
        <div className="flex items-center gap-3 my-2.5">
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span className="text-[9px] font-black px-3 py-1 rounded-full tracking-widest"
            style={{ background: 'rgba(162,143,251,0.15)', color: 'var(--purple)', border: '1px solid rgba(162,143,251,0.35)' }}>
            OR
          </span>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        </div>
      )}

      <div className="rounded-xl" style={{
        border: `1px solid ${cfg.color}40`,
        background: depth === 0 ? 'var(--bg-surface)' : 'rgba(0,0,0,0.15)',
        marginLeft: depth > 0 ? 16 : 0,
      }}>
        {/* Node header */}
        <div className="flex items-center gap-2.5 px-3 py-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: `${cfg.color}18`, border: `1px solid ${cfg.color}35` }}>
            <Icon size={14} style={{ color: cfg.color }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-bold" style={{ color: cfg.color }}>{cfg.label}</div>
            {collapsed && cfg.oneliner && <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{cfg.oneliner}</div>}
            {!collapsed && cfg.oneliner && <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{cfg.oneliner}</div>}
          </div>
          <div className="flex items-center gap-0.5 flex-shrink-0">
            <button onClick={() => setCollapsed(v => !v)} className="p-1 rounded transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
              {collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
            </button>
            {onDelete && (
              <button onClick={onDelete} className="p-1 rounded transition-colors"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
                <Trash2 size={13} />
              </button>
            )}
          </div>
        </div>

        {/* Params + descriptor */}
        {!collapsed && (
          <div className="px-3 pb-3 pt-2 space-y-3" style={{ borderTop: `1px solid ${cfg.color}20` }}>
            {Editor && <Editor p={node.params ?? {}} set={setParams} genres={genres} artists={artists} />}
            <BlockDesc cfg={cfg} />
          </div>
        )}

        {/* AND children */}
        {!collapsed && children.length > 0 && (
          <div className="mx-3 mb-3 p-3 rounded-xl" style={{ background: 'rgba(83,236,252,0.03)', border: '1px solid rgba(83,236,252,0.15)' }}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-[9px] font-black px-3 py-1 rounded-full tracking-widest"
                style={{ background: 'rgba(83,236,252,0.1)', color: 'var(--accent)', border: '1px solid rgba(83,236,252,0.3)' }}>
                AND
              </span>
              <div style={{ flex: 1, height: 1, background: 'rgba(83,236,252,0.15)' }} />
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>must also match</span>
            </div>
            {children.map((child, idx) => (
              <FilterNode key={child._id} node={child} depth={depth + 1} isFirst={idx === 0}
                onUpdate={u => updateChild(idx, u)}
                onDelete={() => deleteChild(idx)}
                genres={genres} artists={artists} />
            ))}
            <div className="mt-2.5">
              <AddFilterButton label="+ OR filter" accentColor="var(--purple)" onAdd={addChild} />
            </div>
          </div>
        )}

        {/* Add AND child */}
        {!collapsed && (
          <div className="px-3 pb-3" style={{ borderTop: children.length > 0 ? 'none' : `1px solid ${cfg.color}15`, paddingTop: children.length > 0 ? 0 : 10 }}>
            <AddFilterButton label="+ AND filter" accentColor="var(--accent)" onAdd={addChild} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── BlockEditor — full-window ─────────────────────────────────────────────────

export default function BlockEditor({ template, onClose, onSaved }) {
  const { user } = useAuth()
  const isNew = !template?.id

  const [name,     setName]   = useState(template?.name ?? '')
  const [desc,     setDesc]   = useState(template?.description ?? '')
  const [total,    setTotal]  = useState(template?.total_tracks ?? 50)
  const [isPublic, setPub]    = useState(template?.is_public ?? true)

  const [chains,     setChains]    = useState(() => {
    if (!isNew && template?.blocks?.length > 0) {
      return [...template.blocks].sort((a, b) => a.position - b.position).map(chainFromBlock)
    }
    return [{ ...makeChain('final_score'), weight: 100 }]
  })
  const [activeIdx,  setActive]    = useState(0)
  const [deletedIds, setDeleted]   = useState([])
  const userAdjustedWeights = useRef(false)
  const previewDebounceRef  = useRef(null)
  // Tracks the live { chainId -> dbBlockId } mapping so saveBlocksSilently
  // doesn't rely on the stale template.blocks prop after a silent save creates
  // a new block.
  const savedBlockIdsRef = useRef(
    Object.fromEntries(
      (template?.blocks ?? []).map(b => [`db_${b.id}`, b.id])
    )
  )

  // FIX: genres stored as plain strings; artists stored as enriched objects
  const [genres,  setGenres]  = useState([])
  const [artists, setArtists] = useState([])

  const [saving,    setSaving]   = useState(false)
  const [saved,     setSaved]    = useState(false)
  const [saveErr,   setSaveErr]  = useState(null)
  const [prevLoad,  setPrevLoad] = useState(false)
  const [preview,   setPreview]  = useState(null)

  useEffect(() => {
    const userId = user?.user_id
    if (!userId) return

    // FIX: pass user_id so _resolve_user doesn't 400; extract .genre string from each object
    api.get(`/api/insights/genres?user_id=${userId}`)
      .then(r => {
        const raw = Array.isArray(r) ? r : (r?.genres ?? [])
        // Each item is {genre, affinity_score, ...} — extract the string
        const names = raw.map(item => (typeof item === 'string' ? item : item?.genre)).filter(Boolean)
        setGenres(names)
      })
      .catch(() => {})

    // FIX: pass user_id; fetch up to 500 artists; store full objects for the card UI
    api.get(`/api/insights/artists?user_id=${userId}&page_size=200&page=1`)
      .then(r => {
        const list = r?.artists ?? []
        setArtists(list)
        // If there are more pages, fetch them (up to 500)
        const pages = r?.pages ?? 1
        if (pages > 1) {
          const extra = Array.from({ length: Math.min(pages - 1, 2) }, (_, i) =>
            api.get(`/api/insights/artists?user_id=${userId}&page_size=200&page=${i + 2}`)
              .then(p2 => p2?.artists ?? [])
              .catch(() => [])
          )
          Promise.all(extra).then(results => {
            setArtists(prev => [...prev, ...results.flat()])
          })
        }
      })
      .catch(() => {})
  }, [user?.user_id])

  // Close on Escape
  useEffect(() => {
    const esc = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const totalWeight = chains.reduce((s, c) => s + (c.weight || 0), 0)
  const weightOk    = Math.abs(totalWeight - 100) <= 1

  const activeChain = chains[activeIdx] ?? chains[0]
  const safeActive  = Math.min(activeIdx, chains.length - 1)

  function evenWeights(cs) {
    if (cs.length === 0) return cs
    const each = Math.floor(100 / cs.length)
    const rem  = 100 - each * cs.length
    return cs.map((c, i) => ({ ...c, weight: each + (i === 0 ? rem : 0) }))
  }
  function addChain(ft = 'final_score') {
    const newChain = makeChain(ft)
    setChains(cs => {
      const next = [...cs, newChain]
      return userAdjustedWeights.current ? next : evenWeights(next)
    })
    setActive(chains.length)
  }
  function updateChainWeight(idx, w) {
    userAdjustedWeights.current = true
    setChains(cs => cs.map((c, i) => i === idx ? { ...c, weight: w } : c))
  }
  function updateChainTree(idx, tree) {
    setChains(cs => cs.map((c, i) => i === idx ? { ...c, filter_tree: tree } : c))
  }
  function moveChain(idx, dir) {
    setChains(cs => {
      const a = [...cs], s = idx + dir
      if (s < 0 || s >= a.length) return a
      ;[a[idx], a[s]] = [a[s], a[idx]]
      return a
    })
    setActive(idx + dir)
  }
  function deleteChain(idx) {
    const c = chains[idx]
    if (c.dbId) setDeleted(d => [...d, c.dbId])
    setChains(cs => {
      const next = cs.filter((_, i) => i !== idx)
      return userAdjustedWeights.current ? next : evenWeights(next)
    })
    setActive(Math.max(0, idx - 1))
  }
  function updateActiveTree(tree) {
    updateChainTree(safeActive, tree)
  }

  // ── Silent block-save: persists current chains to the DB without touching
  //    the main save UI state (no redirect, no "Saved" flash).
  //    Returns true on success so the caller can proceed to preview.
  const saveBlocksSilently = useCallback(async (currentChains, currentDeletedIds) => {
    if (!template?.id) return false
    try {
      const totalW = currentChains.reduce((s, c) => s + (c.weight || 0), 0) || 100
      // Delete removed blocks
      for (const id of currentDeletedIds) {
        await api.delete(`/api/playlist-templates/${template.id}/blocks/${id}`)
      }
      // Upsert each chain — use the live savedBlockIdsRef, NOT template.blocks,
      // so we don't re-POST blocks that were already created by a prior silent save.
      for (const c of currentChains) {
        const payload = {
          block_type: chainBlockType(c),
          weight: Math.round((c.weight / totalW) * 100),
          position: currentChains.indexOf(c),
          params: { filter_tree: c.filter_tree },
        }
        const knownDbId = savedBlockIdsRef.current[c._id] ?? c.dbId
        if (knownDbId) {
          await api.put(`/api/playlist-templates/${template.id}/blocks/${knownDbId}`, payload)
        } else {
          const created = await api.post(`/api/playlist-templates/${template.id}/blocks`, payload)
          // Track the new DB id so subsequent saves PUT instead of POST
          if (created?.id) savedBlockIdsRef.current[c._id] = created.id
        }
      }
      return true
    } catch (err) {
      console.error('saveBlocksSilently failed:', err)
      return false
    }
  }, [template?.id])

  async function handlePreview() {
    if (!template?.id) return
    setPrevLoad(true); setPreview(null)
    try {
      // Always sync current (possibly unsaved) chains to the DB first so the
      // preview endpoint sees the latest filter configuration.
      const saved = await saveBlocksSilently(chains, deletedIds)
      if (!saved) {
        setPreview({ error: 'Could not sync filters to server before preview. Check you own this template and try again.', error_code: 'save_failed' })
        return
      }
      const result = await api.get(`/api/playlist-templates/${template.id}/preview`)
      setPreview(result)
    } catch (e) {
      const msg = e.message || 'Unknown error'
      setPreview({
        error: msg.includes('500')
          ? "The server returned an error (500). Check your library is indexed, scores are built, and your block filters are not mutually exclusive."
          : msg,
        error_code: 'http_error',
      })
    } finally {
      setPrevLoad(false)
    }
  }

  // ── Auto-refresh preview whenever chains change (debounced 800 ms) ─────────
  //    Only runs for existing (saved) templates — new templates have no id yet.
  useEffect(() => {
    if (!template?.id) return
    if (previewDebounceRef.current) clearTimeout(previewDebounceRef.current)
    previewDebounceRef.current = setTimeout(() => {
      handlePreview()
    }, 800)
    return () => {
      if (previewDebounceRef.current) clearTimeout(previewDebounceRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chains])

  async function handleSave() {
    if (!name.trim()) { setSaveErr('Template name is required.'); return }
    setSaving(true); setSaveErr(null)
    const totalW = chains.reduce((s, c) => s + (c.weight || 0), 0) || 100
    const blocksPayload = chains.map((c, i) => ({
      block_type: chainBlockType(c),
      weight: Math.round((c.weight / totalW) * 100),
      position: i,
      params: { filter_tree: c.filter_tree },
    }))
    try {
      let savedTpl
      if (isNew) {
        savedTpl = await api.post('/api/playlist-templates', {
          name: name.trim(), description: desc.trim() || null,
          total_tracks: total, is_public: isPublic, blocks: blocksPayload,
        })
      } else {
        savedTpl = await api.put(`/api/playlist-templates/${template.id}`, {
          name: name.trim(), description: desc.trim() || null,
          total_tracks: total, is_public: isPublic,
        })
        for (const id of deletedIds) {
          await api.delete(`/api/playlist-templates/${template.id}/blocks/${id}`)
        }
        const existingIds = new Set((template.blocks ?? []).map(b => b.id))
        for (const c of chains) {
          const payload = { block_type: chainBlockType(c), weight: c.weight, position: chains.indexOf(c), params: { filter_tree: c.filter_tree } }
          if (c.dbId && existingIds.has(c.dbId)) await api.put(`/api/playlist-templates/${template.id}/blocks/${c.dbId}`, payload)
          else await api.post(`/api/playlist-templates/${template.id}/blocks`, payload)
        }
      }
      setSaved(true)
      setTimeout(() => { onSaved(savedTpl); onClose() }, 600)
    } catch (e) { setSaveErr(e.message) }
    finally { setSaving(false) }
  }

  const ac = chains[safeActive]

  return createPortal(
    <div className="fixed inset-0 flex flex-col" style={{ zIndex: 9000, background: 'var(--bg)' }}>

      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-4 px-6 py-3 flex-shrink-0"
        style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)', minHeight: 60 }}>
        <div className="flex-1 min-w-0 flex items-center gap-4">
          <div className="min-w-0 flex-1">
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="Template name…"
              className="bg-transparent border-0 text-base font-bold focus:outline-none w-full"
              style={{ color: 'var(--text-primary)' }} />
            <input value={desc} onChange={e => setDesc(e.target.value)}
              placeholder="Description (optional)…"
              className="bg-transparent border-0 text-xs focus:outline-none w-full mt-0.5"
              style={{ color: 'var(--text-secondary)' }} />
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="flex items-center gap-1.5">
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Tracks</span>
              <input type="number" min={5} max={500} value={total}
                onChange={e => setTotal(parseInt(e.target.value) || 50)}
                className="w-16 text-center rounded-lg px-2 py-1 text-xs"
                style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </div>
            <button onClick={() => setPub(v => !v)}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
              style={{ background: isPublic ? 'rgba(83,236,252,0.1)' : 'rgba(255,255,255,0.05)', border: `1px solid ${isPublic ? 'rgba(83,236,252,0.3)' : 'var(--border)'}`, color: isPublic ? 'var(--accent)' : 'var(--text-secondary)' }}>
              {isPublic ? 'Public' : 'Private'}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {saveErr && <span className="text-xs" style={{ color: 'var(--danger)' }}>{saveErr}</span>}
          {!isNew && (
            <button onClick={handlePreview} disabled={prevLoad} className="btn-secondary flex items-center gap-1.5">
              {prevLoad ? <Loader2 size={13} className="animate-spin" /> : <Eye size={13} />} Preview
            </button>
          )}
          <button onClick={handleSave} disabled={saving || saved || !name.trim()} className="btn-primary flex items-center gap-1.5">
            {saved ? <><CheckCircle2 size={13} /> Saved</>
              : saving ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
              : <><Save size={13} /> {isNew ? 'Create' : 'Save'}</>}
          </button>
          <button onClick={onClose} className="p-2 rounded-lg transition-colors ml-1"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <X size={18} />
          </button>
        </div>
      </div>

      {/* ── Chain tabs bar ───────────────────────────────────────────────── */}
      {chains.length > 0 && (
        <div className="flex items-center gap-2 px-6 py-2 flex-shrink-0 overflow-x-auto"
          style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 flex-1 min-w-0">
            {chains.map((c, idx) => {
              const color = chainColor(c)
              const active = idx === safeActive
              return (
                <button key={c._id} onClick={() => setActive(idx)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg flex-shrink-0 transition-all"
                  style={{
                    background: active ? `${color}18` : 'rgba(255,255,255,0.03)',
                    border: `1px solid ${active ? `${color}50` : 'var(--border)'}`,
                  }}>
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
                  <span className="text-xs font-semibold truncate max-w-28" style={{ color: active ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                    {chainLabel(c)}
                  </span>
                  <div className="flex items-center gap-0.5" onClick={e => e.stopPropagation()}>
                    <input type="number" min={1} max={999} value={c.weight}
                      onChange={e => updateChainWeight(idx, parseInt(e.target.value) || 0)}
                      className="w-9 text-center rounded px-1 py-0.5 text-[10px] font-mono"
                      style={{ background: 'rgba(0,0,0,0.2)', border: 'none', color: color }} />
                    <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>%</span>
                  </div>
                  {chains.length > 1 && (
                    <button onClick={e => { e.stopPropagation(); deleteChain(idx) }}
                      className="p-0.5 rounded ml-0.5 flex-shrink-0 transition-colors"
                      style={{ color: 'var(--text-muted)' }}
                      onMouseEnter={e => { e.stopPropagation(); e.currentTarget.style.color = 'var(--danger)' }}
                      onMouseLeave={e => { e.stopPropagation(); e.currentTarget.style.color = 'var(--text-muted)' }}>
                      <X size={10} />
                    </button>
                  )}
                </button>
              )
            })}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 ml-2">
            <div className="h-1.5 rounded-full overflow-hidden" style={{ width: 80, background: 'var(--bg-overlay)' }}>
              <div className="h-full flex gap-px" style={{ width: `${Math.min((totalWeight / 100) * 100, 100)}%`, transition: 'width 0.2s' }}>
                {chains.map(c => (
                  <div key={c._id} style={{ flex: c.weight, background: chainColor(c), minWidth: 1 }} />
                ))}
              </div>
            </div>
            <span className="text-[10px] font-mono w-12 text-right"
              style={{ color: totalWeight === 100 ? 'var(--accent)' : totalWeight > 100 ? 'var(--danger)' : 'var(--text-muted)' }}>
              {totalWeight}%
              {totalWeight !== 100 && <span style={{ color: 'var(--text-muted)' }}> →100</span>}
            </span>
            <AddFilterButton label="+ Chain" accentColor="var(--accent)" onAdd={addChain} />
          </div>
        </div>
      )}

      {/* ── Main scroll area ─────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {chains.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-4" style={{ color: 'var(--text-muted)' }}>
            <div className="text-sm">No block chains yet</div>
            <AddFilterButton label="+ Add First Chain" accentColor="var(--accent)" onAdd={addChain} />
          </div>
        ) : !ac ? null : (
          <div className="max-w-2xl mx-auto px-6 py-6">
            <div className="space-y-0">
              {(ac.filter_tree || []).map((node, idx) => (
                <FilterNode key={node._id} node={node} depth={0} isFirst={idx === 0}
                  onUpdate={u => { const t = [...(ac.filter_tree || [])]; t[idx] = u; updateActiveTree(t) }}
                  onDelete={(ac.filter_tree || []).length > 1 ? () => updateActiveTree((ac.filter_tree || []).filter((_, j) => j !== idx)) : undefined}
                  genres={genres} artists={artists} />
              ))}
            </div>

            <div className="mt-4">
              <AddFilterButton
                label={(ac.filter_tree || []).length === 0 ? '+ Add filter' : '+ OR filter'}
                accentColor="var(--purple)"
                onAdd={ft => updateActiveTree([...(ac.filter_tree || []), makeNode(ft)])} />
            </div>

            {/* Live preview — auto-refreshes whenever filters change */}
            {(preview || prevLoad) && (
              <div className="mt-6 rounded-xl overflow-hidden" style={{
                border: `1px solid ${preview?.error ? 'rgba(248,113,113,0.35)' : 'var(--border)'}`,
                background: preview?.error ? 'rgba(248,113,113,0.05)' : 'var(--bg-surface)',
                opacity: prevLoad ? 0.6 : 1,
                transition: 'opacity 0.2s',
              }}>
                {prevLoad && !preview && (
                  <div className="flex items-center gap-2 p-4">
                    <Loader2 size={13} className="animate-spin" style={{ color: 'var(--accent)' }} />
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Running preview…</span>
                  </div>
                )}
                {preview?.error ? (
                  <div className="flex gap-3 p-4">
                    <AlertCircle size={16} style={{ color: 'var(--danger)', flexShrink: 0, marginTop: 1 }} />
                    <div>
                      <div className="text-xs font-semibold mb-1" style={{ color: 'var(--danger)' }}>
                        Preview couldn't run
                      </div>
                      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        {preview.error}
                      </p>
                    </div>
                  </div>
                ) : preview ? (
                  <div className="p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <CheckCircle2 size={13} style={{ color: 'var(--accent)' }} />
                      <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
                        ~{preview.estimated_tracks} tracks matched
                      </span>
                      <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>· random sample below</span>
                      <span className="ml-auto text-[10px]" style={{ color: prevLoad ? 'var(--accent)' : 'var(--text-muted)' }}>
                        {prevLoad ? '↻ updating…' : '● live'}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {(preview.sample || []).map((t, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                          <span className="text-[10px] w-4 text-right flex-shrink-0 font-mono"
                            style={{ color: 'var(--text-muted)' }}>{i + 1}</span>
                          <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{t.track}</span>
                          <span style={{ color: 'var(--text-muted)' }}>—</span>
                          <span style={{ color: 'var(--text-secondary)' }}>{t.artist}</span>
                        </div>
                      ))}
                    </div>
                    {preview.sample?.length === 0 && (
                      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No sample tracks available.</p>
                    )}
                  </div>
                ) : null}
                {/* Dev: show traceback when present */}
                {preview?.traceback && (
                  <details style={{ borderTop: '1px solid var(--border)' }}>
                    <summary className="px-4 py-2 text-[10px] cursor-pointer" style={{ color: 'var(--text-muted)' }}>
                      Server traceback (dev)
                    </summary>
                    <pre className="px-4 pb-3 text-[10px] overflow-x-auto whitespace-pre-wrap"
                      style={{ color: 'var(--danger)', opacity: 0.8 }}>{preview.traceback}</pre>
                  </details>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}
