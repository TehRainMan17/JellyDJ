/**
 * BlockChainEditor.jsx
 *
 * Renders one block chain (weight % + nested filter tree).
 *
 * Key design decisions:
 *  - Filter picker is a PORTAL modal (renders in document.body via createPortal)
 *    so it is never clipped by overflow:hidden on parent containers.
 *  - The modal shows all filter types as large cards with icon + description.
 *  - Siblings at the same tree level = OR (union)
 *  - Children of a node = AND (intersection with parent's set)
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  Sparkles, Radio, TrendingUp, Clock, Globe, Star, Users,
  Tag, ChevronDown, ChevronUp, Trash2, Plus, X, Search,
  Zap, RefreshCw, Compass, BarChart2, SkipForward
} from 'lucide-react'

// ── Filter catalogue ──────────────────────────────────────────────────────────

export const FILTER_TYPES = {
  final_score:       { label: 'Final Score',        icon: Sparkles,   color: 'var(--accent)',  desc: 'Tracks ranked by your personal composite score — the best all-round pick' },
  play_recency:      { label: 'Play Recency',        icon: Clock,      color: '#fbbf24',        desc: 'Tracks you played recently, or ones you haven\'t touched in a while' },
  genre:             { label: 'Genre',               icon: Tag,        color: '#34d399',        desc: 'Tracks belonging to specific genres in your library' },
  artist:            { label: 'Artist',              icon: Users,      color: '#fb923c',        desc: 'Tracks from specific artists — leave empty for all artists' },
  play_count:        { label: 'Play Count',          icon: TrendingUp, color: '#f87171',        desc: 'Filter by how many times you\'ve played a track' },
  discovery:         { label: 'Discovery',           icon: Radio,      color: '#f472b6',        desc: 'Tracks bucketed by artist familiarity: strangers, acquaintances, or familiar artists' },
  global_popularity: { label: 'Global Popularity',   icon: Globe,      color: '#60a5fa',        desc: 'Narrow by how popular a track is globally (Last.fm / Spotify data)' },
  affinity:          { label: 'Affinity Range',      icon: Star,       color: '#a78bfa',        desc: 'Tracks within a specific artist + genre affinity score range' },
  favorites:         { label: 'Favorites Only',      icon: Star,       color: '#fde68a',        desc: 'Only tracks you have explicitly marked as favorites' },
  played_status:     { label: 'Played Status',       icon: TrendingUp, color: '#94a3b8',        desc: 'Narrow to only played tracks, or only tracks you\'ve never played' },
  artist_cap:        { label: 'Artist Cap',          icon: Users,        color: '#94a3b8',        desc: 'Limit how many tracks from any single artist can appear in this chain' },
  // New blocks
  skip_rate:         { label: 'Skip Rate Filter',    icon: SkipForward,  color: '#f97316',        desc: 'Filter by how often you skip a track — 0 = never skip, 1 = always skip' },
  replay_boost:      { label: 'Replay Boost',        icon: RefreshCw,    color: '#22d3ee',        desc: "Tracks from artists you've been voluntarily seeking out recently" },
  novelty:           { label: 'Novelty Score',       icon: Compass,      color: '#a78bfa',        desc: 'Unplayed tracks ranked by how well they match your artist + genre taste profile' },
  recency_score:     { label: 'Recency Score',       icon: BarChart2,    color: '#fb923c',        desc: 'Smooth recency gradient — 100 = played last month, 0 = over a year ago' },
  skip_streak:       { label: 'Skip Streak',         icon: Zap,          color: '#f43f5e',        desc: 'Filter by consecutive skip count — great for zero-tolerance skip filtering' },
}

const DEFAULT_PARAMS = {
  final_score:       { played_filter: 'all', jitter_pct: 0.15 },
  play_recency:      { mode: 'within', days: 30 },
  genre:             { genres: [] },
  artist:            { artists: [] },
  play_count:        { play_count_min: 0, play_count_max: 500, order: 'desc' },
  discovery:         { stranger_pct: 34, acquaintance_pct: 33, familiar_pct: 33 },
  global_popularity: { popularity_min: 0, popularity_max: 100 },
  affinity:          { affinity_min: 0, affinity_max: 100, played_filter: 'all' },
  favorites:         {},
  played_status:     { played_filter: 'played' },
  artist_cap:        { max_per_artist: 3 },
  // New blocks
  skip_rate:         { skip_penalty_min: 0.0, skip_penalty_max: 0.3, played_filter: 'all' },
  replay_boost:      { boost_min: 0.1, boost_max: 12, played_filter: 'all' },
  novelty:           { novelty_min: 0, novelty_max: 100 },
  recency_score:     { recency_min: 0, recency_max: 100, played_filter: 'played' },
  skip_streak:       { streak_min: 0, streak_max: 2, played_filter: 'all' },
}

let _uid = 1000
export function makeNode(filter_type) {
  return { _id: String(++_uid), filter_type, params: { ...(DEFAULT_PARAMS[filter_type] || {}) }, children: [] }
}

// ── FilterPickerModal — portal-based, never clipped ───────────────────────────

function FilterPickerModal({ title, onPick, onClose }) {
  const [search, setSearch] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    // Focus search on open
    setTimeout(() => inputRef.current?.focus(), 50)
    // Close on Escape
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const entries = Object.entries(FILTER_TYPES).filter(([, cfg]) =>
    !search.trim() ||
    cfg.label.toLowerCase().includes(search.toLowerCase()) ||
    cfg.desc.toLowerCase().includes(search.toLowerCase())
  )

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="flex flex-col rounded-2xl shadow-2xl anim-scale-in"
        style={{ width: 560, maxWidth: '95vw', maxHeight: '80vh', background: 'var(--bg-elevated)', border: '1px solid var(--border-mid)' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className="flex items-center gap-3 px-5 py-4 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex-1">
            <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{title}</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>Choose a filter type to add</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg transition-colors"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)' }}>
            <Search size={13} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            <input ref={inputRef} type="text" placeholder="Search filters…" value={search}
              onChange={e => setSearch(e.target.value)}
              className="flex-1 bg-transparent border-0 text-xs focus:outline-none"
              style={{ color: 'var(--text-primary)' }} />
          </div>
        </div>

        {/* Filter grid */}
        <div className="overflow-y-auto p-4 grid grid-cols-2 gap-2">
          {entries.map(([type, cfg]) => {
            const Icon = cfg.icon
            return (
              <button
                key={type}
                onClick={() => { onPick(type); onClose() }}
                className="flex items-start gap-3 p-3 rounded-xl text-left transition-all"
                style={{ background: 'var(--bg-surface)', border: `1px solid var(--border)` }}
                onMouseEnter={e => {
                  e.currentTarget.style.borderColor = `${cfg.color}60`
                  e.currentTarget.style.background = `color-mix(in srgb, ${cfg.color} 6%, var(--bg-surface))`
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.borderColor = 'var(--border)'
                  e.currentTarget.style.background = 'var(--bg-surface)'
                }}
              >
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                  style={{ background: `${cfg.color}18`, border: `1px solid ${cfg.color}30` }}>
                  <Icon size={14} style={{ color: cfg.color }} />
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>{cfg.label}</div>
                  <div className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{cfg.desc}</div>
                </div>
              </button>
            )
          })}
          {entries.length === 0 && (
            <div className="col-span-2 text-center py-8 text-sm" style={{ color: 'var(--text-muted)' }}>
              No filters match "{search}"
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

// ── Shared slider/input primitives ────────────────────────────────────────────

function RangeInputs({ label, lo, hi, min = 0, max = 100, step = 1, onLo, onHi, unit = '' }) {
  const span     = max - min || 1
  const leftPct  = ((lo - min) / span) * 100
  const rightPct = ((hi - min) / span) * 100
  const clampLo  = v => Math.max(min, Math.min(Number(v), hi))
  const clampHi  = v => Math.min(max, Math.max(Number(v), lo))
  return (
    <div>
      {label && <div className="section-label mb-2">{label}</div>}
      <div className="relative mb-2" style={{ height: 20 }}>
        <div className="absolute rounded-full" style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
        <div className="absolute rounded-full pointer-events-none" style={{ top: 8, height: 4, left: `${leftPct}%`, width: `${Math.max(0, rightPct - leftPct)}%`, background: 'var(--accent)', opacity: 0.7 }} />
        <input type="range" min={min} max={max} step={step} value={lo} onChange={e => onLo(clampLo(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%', zIndex: lo >= hi - (span * 0.03) ? 5 : 3 }} />
        <input type="range" min={min} max={max} step={step} value={hi} onChange={e => onHi(clampHi(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%', zIndex: 4 }} />
        {[leftPct, rightPct].map((pct, i) => (
          <div key={i} className="absolute pointer-events-none rounded-full border-2"
            style={{ top: 2, width: 16, height: 16, left: `calc(${pct}% - 8px)`, background: 'var(--accent)', borderColor: 'var(--bg)', boxShadow: '0 0 0 2px rgba(83,236,252,0.2)', zIndex: 6, transition: 'left 0.04s' }} />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input type="number" min={min} max={max} step={step} value={lo} onChange={e => onLo(clampLo(e.target.value))} className="input w-16 text-center text-xs" />
        <div className="flex-1 text-center text-xs" style={{ color: 'var(--text-muted)' }}>to</div>
        <input type="number" min={min} max={max} step={step} value={hi} onChange={e => onHi(clampHi(e.target.value))} className="input w-16 text-center text-xs" />
        {unit && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </div>
    </div>
  )
}

function SingleSlider({ label, value, min, max, step = 1, onChange, unit = '' }) {
  const v   = value ?? min
  const pct = ((v - min) / (max - min || 1)) * 100
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="section-label">{label}</span>
        <span className="text-xs font-mono font-semibold" style={{ color: 'var(--accent)' }}>{v}{unit}</span>
      </div>
      <div className="relative mb-2" style={{ height: 20 }}>
        <div className="absolute rounded-full" style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
        <div className="absolute rounded-full pointer-events-none" style={{ top: 8, height: 4, left: 0, width: `${pct}%`, background: 'var(--accent)', opacity: 0.7 }} />
        <input type="range" min={min} max={max} step={step} value={v} onChange={e => onChange(Number(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%', zIndex: 3 }} />
        <div className="absolute pointer-events-none rounded-full border-2"
          style={{ top: 2, width: 16, height: 16, left: `calc(${pct}% - 8px)`, background: 'var(--accent)', borderColor: 'var(--bg)', boxShadow: '0 0 0 2px rgba(83,236,252,0.2)', zIndex: 4, transition: 'left 0.04s' }} />
      </div>
      <input type="number" min={min} max={max} step={step} value={v} onChange={e => onChange(Math.max(min, Math.min(max, Number(e.target.value))))} className="input w-20 text-center text-xs" />
    </div>
  )
}

function Chips({ value, options, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(({ v, label }) => (
        <button key={v} onClick={() => onChange(v)}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
          style={{ background: value === v ? 'var(--accent-soft)' : 'rgba(255,255,255,0.04)', border: `1px solid ${value === v ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`, color: value === v ? 'var(--accent)' : 'var(--text-secondary)' }}>
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Param editors per filter type ─────────────────────────────────────────────

function FinalScoreEditor({ p, set }) {
  return (
    <div className="space-y-4">
      <div>
        <div className="section-label mb-1.5">Played status</div>
        <Chips value={p.played_filter ?? 'all'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'all', label: 'All' }, { v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
      </div>
      <SingleSlider label="Jitter (randomness)" value={Math.round((p.jitter_pct ?? 0.15) * 100)}
        min={0} max={30} step={1} onChange={v => set({ ...p, jitter_pct: v / 100 })} unit="%" />
    </div>
  )
}

function PlayRecencyEditor({ p, set }) {
  const mode = p.mode ?? 'within'
  return (
    <div className="space-y-4">
      <div>
        <div className="section-label mb-1.5">Mode</div>
        <Chips value={mode} onChange={v => set({ ...p, mode: v })}
          options={[{ v: 'within', label: 'Played within' }, { v: 'older', label: 'More than … ago' }]} />
      </div>
      <SingleSlider label={mode === 'within' ? 'Within last N days' : 'More than N days ago'}
        value={p.days ?? 30} min={1} max={365} step={1} onChange={v => set({ ...p, days: v })} unit=" days" />
    </div>
  )
}

function GenreEditor({ p, set, genres }) {
  const selected = p.genres ?? []
  return (
    <div>
      <div className="section-label mb-1.5">Genres <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(empty = all)</span></div>
      <div className="flex flex-wrap gap-1.5 max-h-28 overflow-y-auto pr-1">
        {genres.map(g => (
          <button key={g} onClick={() => set({ ...p, genres: selected.includes(g) ? selected.filter(x => x !== g) : [...selected, g] })}
            className="px-2 py-0.5 rounded-full text-xs font-medium transition-all"
            style={{ background: selected.includes(g) ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.04)', border: `1px solid ${selected.includes(g) ? 'rgba(52,211,153,0.4)' : 'var(--border)'}`, color: selected.includes(g) ? '#34d399' : 'var(--text-secondary)' }}>
            {g}
          </button>
        ))}
        {genres.length === 0 && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>No genres loaded — index first</span>}
      </div>
    </div>
  )
}

function ArtistEditor({ p, set, artists }) {
  const [search, setSearch] = useState('')
  const selected = p.artists ?? []
  const filtered = search.trim() ? artists.filter(a => a.toLowerCase().includes(search.toLowerCase())) : artists
  return (
    <div className="space-y-2">
      <div className="section-label">Artists <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(empty = all)</span></div>
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map(a => (
            <button key={a} onClick={() => set({ ...p, artists: selected.filter(x => x !== a) })}
              className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
              style={{ background: 'rgba(251,146,60,0.12)', border: '1px solid rgba(251,146,60,0.3)', color: '#fb923c' }}>
              {a} ×
            </button>
          ))}
        </div>
      )}
      <input type="text" placeholder="Search artists…" value={search} onChange={e => setSearch(e.target.value)} className="input text-xs" />
      <div className="max-h-28 overflow-y-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
        {filtered.slice(0, 30).map(a => (
          <button key={a}
            onClick={() => set({ ...p, artists: selected.includes(a) ? selected.filter(x => x !== a) : [...selected, a] })}
            className="w-full text-left px-2.5 py-1.5 text-xs transition-colors"
            style={{ background: selected.includes(a) ? 'rgba(251,146,60,0.1)' : 'transparent', color: selected.includes(a) ? '#fb923c' : 'var(--text-secondary)' }}
            onMouseEnter={e => { if (!selected.includes(a)) e.currentTarget.style.background = 'rgba(255,255,255,0.03)' }}
            onMouseLeave={e => { if (!selected.includes(a)) e.currentTarget.style.background = 'transparent' }}>
            {a}
          </button>
        ))}
        {filtered.length === 0 && <div className="text-xs px-2.5 py-2" style={{ color: 'var(--text-muted)' }}>No matches</div>}
      </div>
    </div>
  )
}

function PlayCountEditor({ p, set }) {
  return (
    <div className="space-y-4">
      <RangeInputs label="Play count" lo={p.play_count_min ?? 0} hi={p.play_count_max ?? 500} min={0} max={500}
        onLo={v => set({ ...p, play_count_min: v })} onHi={v => set({ ...p, play_count_max: v })} unit="plays" />
      <div>
        <div className="section-label mb-1.5">Order</div>
        <Chips value={p.order ?? 'desc'} onChange={v => set({ ...p, order: v })}
          options={[{ v: 'desc', label: 'Most played first' }, { v: 'asc', label: 'Least played first' }]} />
      </div>
    </div>
  )
}

function DiscoveryEditor({ p, set }) {
  const s = p.stranger_pct ?? 34
  const a = p.acquaintance_pct ?? 33
  const f = p.familiar_pct ?? 33
  const total = s + a + f
  const warn = Math.abs(total - 100) > 1
  const tiers = [
    { key: 'stranger_pct', label: 'Stranger', color: '#f472b6' },
    { key: 'acquaintance_pct', label: 'Acquaintance', color: '#fb923c' },
    { key: 'familiar_pct', label: 'Familiar', color: '#34d399' },
  ]
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="section-label">Familiarity split</span>
        <span className="text-xs font-mono" style={{ color: warn ? 'var(--danger)' : 'var(--text-muted)' }}>{total}/100</span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden gap-px">
        {tiers.map(({ key, color }) => <div key={key} style={{ width: `${p[key] ?? 33}%`, background: color, transition: 'width 0.15s' }} />)}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {tiers.map(({ key, label, color }) => {
          const val = p[key] ?? 33
          return (
            <div key={key}>
              <div className="flex items-center gap-1 mb-1.5">
                <div className="w-2 h-2 rounded-full" style={{ background: color }} />
                <span className="text-[10px] font-semibold" style={{ color }}>{label}</span>
              </div>
              <div className="relative mb-1" style={{ height: 16 }}>
                <div className="absolute rounded-full" style={{ top: 6, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
                <div className="absolute rounded-full" style={{ top: 6, height: 4, left: 0, width: `${val}%`, background: color, opacity: 0.7 }} />
                <input type="range" min={0} max={100} step={1} value={val} onChange={e => set({ ...p, [key]: Number(e.target.value) })}
                  className="absolute w-full opacity-0 cursor-pointer" style={{ top: 0, height: '100%' }} />
                <div className="absolute pointer-events-none rounded-full border-2"
                  style={{ top: 0, width: 16, height: 16, left: `calc(${val}% - 8px)`, background: color, borderColor: 'var(--bg)' }} />
              </div>
              <div className="flex items-center gap-0.5">
                <input type="number" min={0} max={100} value={val} onChange={e => set({ ...p, [key]: Math.max(0, Math.min(100, Number(e.target.value))) })}
                  className="input w-full text-center text-xs" style={warn ? { borderColor: 'rgba(248,113,113,0.4)' } : {}} />
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>%</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function GlobalPopularityEditor({ p, set }) {
  return <RangeInputs label="Popularity range" lo={p.popularity_min ?? 0} hi={p.popularity_max ?? 100} min={0} max={100}
    onLo={v => set({ ...p, popularity_min: v })} onHi={v => set({ ...p, popularity_max: v })} />
}

function AffinityEditor({ p, set }) {
  return (
    <div className="space-y-4">
      <RangeInputs label="Affinity range" lo={p.affinity_min ?? 0} hi={p.affinity_max ?? 100} min={0} max={100}
        onLo={v => set({ ...p, affinity_min: v })} onHi={v => set({ ...p, affinity_max: v })} />
      <div>
        <div className="section-label mb-1.5">Played status</div>
        <Chips value={p.played_filter ?? 'all'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'all', label: 'All' }, { v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
      </div>
    </div>
  )
}

// ── New block param editors ────────────────────────────────────────────────────

function SkipRateEditor({ p, set }) {
  const lo = p.skip_penalty_min ?? 0.0
  const hi = p.skip_penalty_max ?? 0.3
  return (
    <div className="space-y-4">
      <RangeInputs
        label="Skip penalty (0 = never skip · 100 = always skip)"
        lo={Math.round(lo * 100)} hi={Math.round(hi * 100)}
        min={0} max={100} step={1}
        onLo={v => set({ ...p, skip_penalty_min: v / 100 })}
        onHi={v => set({ ...p, skip_penalty_max: v / 100 })}
        unit="%"
      />
      <div>
        <div className="section-label mb-1.5">Track status</div>
        <Chips value={p.played_filter ?? 'all'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'all', label: 'All' }, { v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
      </div>
    </div>
  )
}

function ReplayBoostEditor({ p, set }) {
  const lo = parseFloat((p.boost_min ?? 0.1).toFixed(1))
  const hi = parseFloat((p.boost_max ?? 12).toFixed(1))
  return (
    <div className="space-y-4">
      <RangeInputs
        label="Replay boost range (0.1 = any signal · 12 = max obsession)"
        lo={lo} hi={hi}
        min={0.1} max={12} step={0.1}
        onLo={v => set({ ...p, boost_min: v, boost_max: Math.max(hi, v) })}
        onHi={v => set({ ...p, boost_max: v, boost_min: Math.min(lo, v) })}
      />
      <div>
        <div className="section-label mb-1.5">Track status</div>
        <Chips value={p.played_filter ?? 'all'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'all', label: 'All' }, { v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
      </div>
    </div>
  )
}

function NoveltyEditor({ p, set }) {
  return (
    <RangeInputs
      label="Novelty score (0 = low taste fit · 100 = perfect match)"
      lo={p.novelty_min ?? 0} hi={p.novelty_max ?? 100}
      min={0} max={100} step={1}
      onLo={v => set({ ...p, novelty_min: v })}
      onHi={v => set({ ...p, novelty_max: v })}
    />
  )
}

function RecencyScoreEditor({ p, set }) {
  return (
    <div className="space-y-4">
      <RangeInputs
        label="Recency score (0 = over a year ago · 100 = last 30 days)"
        lo={p.recency_min ?? 0} hi={p.recency_max ?? 100}
        min={0} max={100} step={1}
        onLo={v => set({ ...p, recency_min: v })}
        onHi={v => set({ ...p, recency_max: v })}
      />
      <div>
        <div className="section-label mb-1.5">Track status</div>
        <Chips value={p.played_filter ?? 'played'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }, { v: 'all', label: 'All' }]} />
      </div>
    </div>
  )
}

function SkipStreakEditor({ p, set }) {
  const lo = p.streak_min ?? 0
  const hi = p.streak_max ?? 2
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="section-label mb-1">Min streak</div>
          <input type="number" min={0} max={20} value={lo}
            onChange={e => set({ ...p, streak_min: Math.max(0, Math.min(hi, Number(e.target.value))) })}
            className="input w-full text-center text-xs" />
        </div>
        <div className="text-xs pt-4" style={{ color: 'var(--text-muted)' }}>to</div>
        <div className="flex-1">
          <div className="section-label mb-1">Max streak</div>
          <input type="number" min={0} max={20} value={hi}
            onChange={e => set({ ...p, streak_max: Math.max(lo, Math.min(20, Number(e.target.value))) })}
            className="input w-full text-center text-xs" />
        </div>
      </div>
      <div>
        <div className="section-label mb-1.5">Track status</div>
        <Chips value={p.played_filter ?? 'all'} onChange={v => set({ ...p, played_filter: v })}
          options={[{ v: 'all', label: 'All' }, { v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
      </div>
    </div>
  )
}

const PARAM_EDITORS = {
  final_score:       FinalScoreEditor,
  play_recency:      PlayRecencyEditor,
  genre:             GenreEditor,
  artist:            ArtistEditor,
  play_count:        PlayCountEditor,
  discovery:         DiscoveryEditor,
  global_popularity: GlobalPopularityEditor,
  affinity:          AffinityEditor,
  favorites:         () => <p className="text-xs" style={{ color: 'var(--text-muted)' }}>All favorited tracks — no extra settings needed.</p>,
  played_status:     ({ p, set }) => (
    <div>
      <div className="section-label mb-1.5">Show</div>
      <Chips value={p.played_filter ?? 'played'} onChange={v => set({ ...p, played_filter: v })}
        options={[{ v: 'played', label: 'Played' }, { v: 'unplayed', label: 'Unplayed' }]} />
    </div>
  ),
  artist_cap: ({ p, set }) => (
    <SingleSlider label="Max tracks per artist" value={p.max_per_artist ?? 3} min={1} max={20} step={1}
      onChange={v => set({ ...p, max_per_artist: v })} />
  ),
  // New blocks
  skip_rate:     SkipRateEditor,
  replay_boost:  ReplayBoostEditor,
  novelty:       NoveltyEditor,
  recency_score: RecencyScoreEditor,
  skip_streak:   SkipStreakEditor,
}

// ── AddFilterButton ───────────────────────────────────────────────────────────

function AddFilterButton({ label, accentColor, onAdd }) {
  const [showPicker, setShowPicker] = useState(false)
  return (
    <>
      <button
        onClick={() => setShowPicker(true)}
        className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-all"
        style={{ color: accentColor, background: `${accentColor}10`, border: `1px dashed ${accentColor}50` }}
        onMouseEnter={e => e.currentTarget.style.background = `${accentColor}18`}
        onMouseLeave={e => e.currentTarget.style.background = `${accentColor}10`}
      >
        <Plus size={11} /> {label}
      </button>
      {showPicker && (
        <FilterPickerModal title={label} onPick={onAdd} onClose={() => setShowPicker(false)} />
      )}
    </>
  )
}

// ── FilterNode ────────────────────────────────────────────────────────────────

export function FilterNode({ node, isFirst, depth = 0, onUpdate, onDelete, genres, artists }) {
  const [collapsed, setCollapsed] = useState(false)
  const cfg    = FILTER_TYPES[node.filter_type] || { label: node.filter_type, icon: Tag, color: 'var(--text-muted)', desc: '' }
  const Icon   = cfg.icon
  const Editor = PARAM_EDITORS[node.filter_type]

  function updateChild(idx, updated) {
    const children = [...(node.children || [])]
    children[idx] = updated
    onUpdate({ ...node, children })
  }

  function deleteChild(idx) {
    onUpdate({ ...node, children: (node.children || []).filter((_, i) => i !== idx) })
  }

  function addAndChild(filter_type) {
    onUpdate({ ...node, children: [...(node.children || []), makeNode(filter_type)] })
  }

  const hasChildren = (node.children || []).length > 0

  return (
    <div className="flex flex-col gap-0">
      {/* OR separator between siblings */}
      {!isFirst && (
        <div className="flex items-center gap-3 my-2">
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
          <span className="text-[9px] font-black px-2.5 py-1 rounded-full tracking-[0.1em]"
            style={{ background: 'rgba(162,143,251,0.12)', color: 'var(--purple)', border: '1px solid rgba(162,143,251,0.3)' }}>
            OR
          </span>
          <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        </div>
      )}

      {/* Node card */}
      <div className="rounded-xl" style={{ border: `1px solid ${cfg.color}40`, background: depth === 0 ? 'var(--bg-surface)' : 'var(--bg)' }}>
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: `${cfg.color}15`, border: `1px solid ${cfg.color}30` }}>
            <Icon size={13} style={{ color: cfg.color }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold" style={{ color: cfg.color }}>{cfg.label}</div>
            {collapsed && <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{cfg.desc}</div>}
          </div>
          <div className="flex items-center gap-0.5 flex-shrink-0">
            <button onClick={() => setCollapsed(v => !v)} className="p-1 rounded transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
              {collapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
            </button>
            {onDelete && (
              <button onClick={onDelete} className="p-1 rounded transition-colors"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
                <Trash2 size={12} />
              </button>
            )}
          </div>
        </div>

        {/* Params editor */}
        {!collapsed && Editor && (
          <div className="px-3 pb-3 pt-2" style={{ borderTop: `1px solid ${cfg.color}20` }}>
            <Editor p={node.params || {}} set={params => onUpdate({ ...node, params })} genres={genres} artists={artists} />
          </div>
        )}

        {/* AND children */}
        {!collapsed && hasChildren && (
          <div className="mx-3 mb-3 rounded-xl p-3" style={{ background: 'rgba(83,236,252,0.03)', border: '1px solid rgba(83,236,252,0.12)' }}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-[9px] font-black px-2.5 py-1 rounded-full tracking-[0.1em]"
                style={{ background: 'rgba(83,236,252,0.1)', color: 'var(--accent)', border: '1px solid rgba(83,236,252,0.25)' }}>
                AND
              </span>
              <div style={{ flex: 1, height: 1, background: 'rgba(83,236,252,0.15)' }} />
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>must also match</span>
            </div>
            {(node.children || []).map((child, idx) => (
              <FilterNode key={child._id} node={child} depth={depth + 1} isFirst={idx === 0}
                onUpdate={u => updateChild(idx, u)}
                onDelete={() => deleteChild(idx)}
                genres={genres} artists={artists} />
            ))}
            <div className="mt-2">
              <AddFilterButton label="+ OR filter" accentColor="var(--purple)" onAdd={filter_type => addAndChild(filter_type)} />
            </div>
          </div>
        )}

        {/* Add AND child button */}
        {!collapsed && (
          <div className="px-3 pb-3" style={{ borderTop: hasChildren ? 'none' : `1px solid ${cfg.color}15`, paddingTop: hasChildren ? 0 : 10 }}>
            <AddFilterButton label="+ AND filter" accentColor="var(--accent)" onAdd={addAndChild} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── BlockChainEditor ──────────────────────────────────────────────────────────

export default function BlockChainEditor({
  chain,
  index,
  totalChains,
  totalWeight,
  onWeightChange,
  onTreeChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  genres,
  artists,
}) {
  const tree      = chain.filter_tree || []
  const rootColor = tree.length > 0 ? (FILTER_TYPES[tree[0].filter_type]?.color || 'var(--accent)') : 'var(--text-muted)'
  const rootLabel = tree.length === 0 ? 'Empty — add a filter below'
    : tree.map(n => FILTER_TYPES[n.filter_type]?.label || n.filter_type).join(' / ')

  function updateNode(idx, updated) {
    const next = [...tree]; next[idx] = updated; onTreeChange(next)
  }
  function deleteNode(idx) {
    onTreeChange(tree.filter((_, i) => i !== idx))
  }
  function addRootNode(filter_type) {
    onTreeChange([...tree, makeNode(filter_type)])
  }

  const widthPct = totalWeight > 0 ? (chain.weight / totalWeight) * 100 : 0

  return (
    <div className="rounded-2xl" style={{ border: `1px solid ${rootColor}30`, background: 'var(--bg-surface)' }}>

      {/* Chain header */}
      <div className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="w-2 h-8 rounded-full flex-shrink-0" style={{ background: rootColor, opacity: 0.8 }} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold truncate" style={{ color: 'var(--text-primary)' }}>{rootLabel}</div>
          <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {tree.length <= 1 ? 'Single filter' : `${tree.length} filters (OR)`}
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <input type="number" min={1} max={100} value={chain.weight}
            onChange={e => onWeightChange(parseInt(e.target.value) || 0)}
            onClick={e => e.stopPropagation()}
            className="w-14 text-center rounded-lg px-2 py-1 text-xs font-mono"
            style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', color: 'var(--accent)' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>%</span>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button onClick={onMoveUp} disabled={index === 0} className="p-1 rounded disabled:opacity-20" style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <ChevronUp size={13} />
          </button>
          <button onClick={onMoveDown} disabled={index === totalChains - 1} className="p-1 rounded disabled:opacity-20" style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <ChevronDown size={13} />
          </button>
          <button onClick={onDelete} className="p-1 rounded ml-0.5" style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* Weight bar */}
      <div style={{ height: 3, background: 'var(--bg-overlay)' }}>
        <div style={{ height: '100%', width: `${Math.min(100, widthPct)}%`, background: rootColor, transition: 'width 0.2s ease' }} />
      </div>

      {/* Filter tree */}
      <div className="p-4 space-y-0">
        {tree.map((node, idx) => (
          <FilterNode key={node._id} node={node} depth={0} isFirst={idx === 0}
            onUpdate={u => updateNode(idx, u)}
            onDelete={tree.length > 1 ? () => deleteNode(idx) : undefined}
            genres={genres} artists={artists} />
        ))}
        <div className={tree.length > 0 ? 'mt-3' : ''}>
          <AddFilterButton label={tree.length === 0 ? '+ Add filter' : '+ OR filter'} accentColor="var(--purple)" onAdd={addRootNode} />
        </div>
      </div>
    </div>
  )
}
