/**
 * BlockCard.jsx — Single block display/edit card for the Block Editor.
 * All numeric ranges have both a drag slider AND a type-in number input.
 */
import { useState } from 'react'
import {
  Sparkles, Radio, TrendingUp, Clock, Globe, Star, Users,
  Tag, Layers, ChevronDown, ChevronUp, ArrowUp, ArrowDown, Trash2,
  Zap, RefreshCw, Compass, BarChart2, SkipForward, Activity, Music2, Wind
} from 'lucide-react'

export const BLOCK_TYPES = {
  final_score:       { label: 'Final Score',        icon: Sparkles,     color: 'var(--accent)',   desc: 'Affinity-weighted picks ranked by composite score' },
  affinity:          { label: 'Affinity',            icon: Star,         color: '#a78bfa',         desc: 'Tracks ranked by artist + genre affinity average' },
  genre:             { label: 'Genre',               icon: Tag,          color: '#34d399',         desc: 'Tracks filtered by genre with affinity ranking' },
  artist:            { label: 'Artist',              icon: Users,        color: '#fb923c',         desc: 'Tracks from specific artists ranked by affinity' },
  play_count:        { label: 'Play Count',          icon: TrendingUp,   color: '#f87171',         desc: 'Your most (or least) played tracks' },
  play_recency:      { label: 'Play Recency',        icon: Clock,        color: '#fbbf24',         desc: 'Tracks played recently or a while ago' },
  global_popularity: { label: 'Global Popularity',  icon: Globe,        color: '#60a5fa',         desc: 'Globally popular tracks from your library' },
  discovery:         { label: 'Discovery',           icon: Radio,        color: '#f472b6',         desc: 'Novel tracks by familiarity tier' },
  favorites:         { label: 'Favorites',           icon: Star,         color: '#fde68a',         desc: 'Your favorited tracks only' },
  // New blocks
  skip_rate:         { label: 'Skip Rate Filter',    icon: SkipForward,  color: '#f97316',         desc: 'Filter by skip penalty — 0 = never skipped, 100 = always skipped' },
  replay_boost:      { label: 'Replay Boost',        icon: RefreshCw,    color: '#22d3ee',         desc: "Tracks from artists you've been actively replaying lately" },
  novelty:           { label: 'Novelty Score',       icon: Compass,      color: '#a78bfa',         desc: 'Unplayed tracks ranked by taste-profile fit' },
  recency_score:     { label: 'Recency Score',       icon: BarChart2,    color: '#fb923c',         desc: 'Smooth recency gradient: 100 = last month, 0 = over a year ago' },
  skip_streak:       { label: 'Skip Streak',         icon: Zap,          color: '#f43f5e',         desc: 'Filter by consecutive skip count — great for zero-tolerance filtering' },
  // Audio analysis blocks
  bpm_range:         { label: 'BPM Range',           icon: Activity,     color: '#f87171',         desc: 'Filter tracks by tempo (beats per minute)' },
  musical_key:       { label: 'Musical Key',         icon: Music2,       color: '#a78bfa',         desc: 'Filter by musical key and mode — major, minor, or specific keys' },
  energy:            { label: 'Energy',              icon: Zap,          color: '#fbbf24',         desc: 'Filter by RMS audio energy 0–1 (0 = quiet, 1 = loud and dense)' },
  loudness_db:       { label: 'Loudness',            icon: BarChart2,    color: '#60a5fa',         desc: 'Filter by integrated loudness in dBFS (closer to 0 = louder)' },
  beat_strength:     { label: 'Beat Strength',       icon: Activity,     color: '#f97316',         desc: 'Filter by rhythmic pulse clarity 0–1 (0 = loose/ambient, 1 = strong beat)' },
  time_signature:    { label: 'Time Signature',      icon: Music2,       color: '#34d399',         desc: 'Filter by beats per bar — 3/4 (waltz) or 4/4 (common time)' },
  acousticness:      { label: 'Acousticness',        icon: Wind,         color: '#7ee787',         desc: 'Filter by acoustic vs electronic character (0 = electronic, 1 = acoustic)' },
}

// ── RangeWithInputs ───────────────────────────────────────────────────────────
// Dual-handle slider for min/max ranges, plus two number inputs below it.
// Both the slider thumbs AND the inputs stay in sync.

function RangeWithInputs({ label, minVal, maxVal, absMin = 0, absMax = 100, step = 1, onMinChange, onMaxChange, unit = '' }) {
  const lo   = minVal ?? absMin
  const hi   = maxVal ?? absMax
  const span = absMax - absMin || 1

  const leftPct  = ((lo - absMin) / span) * 100
  const rightPct = ((hi - absMin) / span) * 100
  const widthPct = rightPct - leftPct

  const clampMin = v => Math.max(absMin, Math.min(Number(v), hi))
  const clampMax = v => Math.min(absMax, Math.max(Number(v), lo))

  return (
    <div>
      <div className="section-label mb-3">{label}</div>

      {/* ── Slider track ── */}
      <div className="relative mb-3" style={{ height: 20 }}>
        {/* Track */}
        <div
          className="absolute rounded-full"
          style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }}
        />
        {/* Filled range */}
        <div
          className="absolute rounded-full pointer-events-none"
          style={{
            top: 8, height: 4,
            left: `${leftPct}%`,
            width: `${Math.max(0, widthPct)}%`,
            background: 'var(--accent)',
            opacity: 0.75,
            transition: 'left 0.04s, width 0.04s',
          }}
        />

        {/* Min range input — sits on top; z-index tricks let both be draggable */}
        <input
          type="range" min={absMin} max={absMax} step={step} value={lo}
          onChange={e => onMinChange(clampMin(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer"
          style={{ top: 0, height: '100%', zIndex: lo >= hi - (span * 0.03) ? 5 : 3 }}
        />
        {/* Max range input */}
        <input
          type="range" min={absMin} max={absMax} step={step} value={hi}
          onChange={e => onMaxChange(clampMax(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer"
          style={{ top: 0, height: '100%', zIndex: 4 }}
        />

        {/* Visual thumb — min */}
        <div
          className="absolute pointer-events-none rounded-full border-2"
          style={{
            top: 2, width: 16, height: 16,
            left: `calc(${leftPct}% - 8px)`,
            background: 'var(--accent)',
            borderColor: 'var(--bg)',
            boxShadow: '0 0 0 2px rgba(83,236,252,0.25)',
            zIndex: 6,
            transition: 'left 0.04s',
          }}
        />
        {/* Visual thumb — max */}
        <div
          className="absolute pointer-events-none rounded-full border-2"
          style={{
            top: 2, width: 16, height: 16,
            left: `calc(${rightPct}% - 8px)`,
            background: 'var(--accent)',
            borderColor: 'var(--bg)',
            boxShadow: '0 0 0 2px rgba(83,236,252,0.25)',
            zIndex: 6,
            transition: 'left 0.04s',
          }}
        />
      </div>

      {/* ── Number inputs ── */}
      <div className="flex items-center gap-2">
        <input
          type="number" min={absMin} max={absMax} step={step} value={lo}
          onChange={e => onMinChange(clampMin(e.target.value))}
          className="input w-16 text-center text-xs"
        />
        <div className="flex-1 text-center text-xs" style={{ color: 'var(--text-muted)' }}>to</div>
        <input
          type="number" min={absMin} max={absMax} step={step} value={hi}
          onChange={e => onMaxChange(clampMax(e.target.value))}
          className="input w-16 text-center text-xs"
        />
        {unit && <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </div>
    </div>
  )
}

// ── SingleSliderWithInput ─────────────────────────────────────────────────────
// A single draggable slider + a number input for scalar values.

function SingleSliderWithInput({ label, value, min, max, step = 1, onChange, unit = '' }) {
  const v   = value ?? min
  const pct = ((v - min) / (max - min || 1)) * 100

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="section-label">{label}</span>
        <span className="text-xs font-mono font-semibold" style={{ color: 'var(--accent)' }}>
          {v}{unit}
        </span>
      </div>

      {/* Slider */}
      <div className="relative mb-2" style={{ height: 20 }}>
        <div
          className="absolute rounded-full"
          style={{ top: 8, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }}
        />
        <div
          className="absolute rounded-full pointer-events-none"
          style={{ top: 8, height: 4, left: 0, width: `${pct}%`, background: 'var(--accent)', opacity: 0.75 }}
        />
        <input
          type="range" min={min} max={max} step={step} value={v}
          onChange={e => onChange(Number(e.target.value))}
          className="absolute w-full opacity-0 cursor-pointer"
          style={{ top: 0, height: '100%', zIndex: 3 }}
        />
        <div
          className="absolute pointer-events-none rounded-full border-2"
          style={{
            top: 2, width: 16, height: 16,
            left: `calc(${pct}% - 8px)`,
            background: 'var(--accent)',
            borderColor: 'var(--bg)',
            boxShadow: '0 0 0 2px rgba(83,236,252,0.25)',
            zIndex: 4,
            transition: 'left 0.04s',
          }}
        />
      </div>

      {/* Number input */}
      <input
        type="number" min={min} max={max} step={step} value={v}
        onChange={e => onChange(Math.max(min, Math.min(max, Number(e.target.value))))}
        className="input w-20 text-center text-xs"
      />
    </div>
  )
}

// ── Shared filter sub-components ──────────────────────────────────────────────

function PlayedFilter({ value, onChange }) {
  return (
    <div>
      <div className="section-label mb-1.5">Played filter</div>
      <div className="flex gap-1.5">
        {['all', 'played', 'unplayed'].map(v => (
          <button
            key={v}
            onClick={() => onChange(v)}
            className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all capitalize"
            style={{
              background: value === v ? 'var(--accent-soft)' : 'rgba(255,255,255,0.04)',
              border: `1px solid ${value === v ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
              color: value === v ? 'var(--accent)' : 'var(--text-secondary)',
            }}
          >
            {v}
          </button>
        ))}
      </div>
    </div>
  )
}

function MaxPerArtist({ value, onChange }) {
  return (
    <SingleSliderWithInput
      label="Max tracks per artist"
      value={value ?? 3}
      min={1} max={20} step={1}
      onChange={onChange}
    />
  )
}

// ── Filter controls per block type ────────────────────────────────────────────

function FinalScoreFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <SingleSliderWithInput
        label="Jitter (randomness)"
        value={Math.round((params.jitter_pct ?? 0.15) * 100)}
        min={0} max={30} step={1}
        onChange={v => onChange({ ...params, jitter_pct: v / 100 })}
        unit="%"
      />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function AffinityFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Affinity range"
        minVal={params.affinity_min ?? 0}
        maxVal={params.affinity_max ?? 100}
        absMin={0} absMax={100}
        onMinChange={v => onChange({ ...params, affinity_min: v })}
        onMaxChange={v => onChange({ ...params, affinity_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function GenreFilters({ params, onChange, genres }) {
  const selected = params.genres ?? []
  const toggle = g => onChange({ ...params, genres: selected.includes(g) ? selected.filter(x => x !== g) : [...selected, g] })
  return (
    <div className="space-y-5">
      <div>
        <div className="section-label mb-1.5">
          Genres <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(leave empty = all)</span>
        </div>
        <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
          {genres.map(g => (
            <button
              key={g}
              onClick={() => toggle(g)}
              className="px-2 py-0.5 rounded-full text-xs font-medium transition-all"
              style={{
                background: selected.includes(g) ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${selected.includes(g) ? 'rgba(52,211,153,0.4)' : 'var(--border)'}`,
                color: selected.includes(g) ? '#34d399' : 'var(--text-secondary)',
              }}
            >{g}</button>
          ))}
          {genres.length === 0 && (
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>No genres loaded — index first</span>
          )}
        </div>
      </div>
      <RangeWithInputs
        label="Genre affinity range"
        minVal={params.genre_affinity_min ?? 0}
        maxVal={params.genre_affinity_max ?? 100}
        absMin={0} absMax={100}
        onMinChange={v => onChange({ ...params, genre_affinity_min: v })}
        onMaxChange={v => onChange({ ...params, genre_affinity_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
      <SingleSliderWithInput
        label="Jitter (randomness)"
        value={Math.round((params.jitter_pct ?? 0) * 100)}
        min={0} max={30} step={1}
        onChange={v => onChange({ ...params, jitter_pct: v / 100 })}
        unit="%"
      />
    </div>
  )
}

function ArtistFilters({ params, onChange, artists }) {
  const [search, setSearch] = useState('')
  const selected = params.artists ?? []
  const filtered = search.trim()
    ? artists.filter(a => a.toLowerCase().includes(search.toLowerCase()))
    : artists
  return (
    <div className="space-y-5">
      <div>
        <div className="section-label mb-1.5">
          Artists <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(leave empty = all)</span>
        </div>
        {selected.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {selected.map(a => (
              <button
                key={a}
                onClick={() => onChange({ ...params, artists: selected.filter(x => x !== a) })}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition-all"
                style={{ background: 'rgba(251,146,60,0.12)', border: '1px solid rgba(251,146,60,0.3)', color: '#fb923c' }}
              >{a} ×</button>
            ))}
          </div>
        )}
        <input
          type="text" placeholder="Search artists…" value={search}
          onChange={e => setSearch(e.target.value)}
          className="input mb-2 text-xs"
        />
        <div className="max-h-32 overflow-y-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
          {filtered.slice(0, 30).map(a => (
            <button
              key={a}
              onClick={() => onChange({ ...params, artists: selected.includes(a) ? selected.filter(x => x !== a) : [...selected, a] })}
              className="w-full text-left px-2.5 py-1.5 text-xs transition-colors"
              style={{ background: selected.includes(a) ? 'rgba(251,146,60,0.1)' : 'transparent', color: selected.includes(a) ? '#fb923c' : 'var(--text-secondary)' }}
              onMouseEnter={e => { if (!selected.includes(a)) e.currentTarget.style.background = 'rgba(255,255,255,0.03)' }}
              onMouseLeave={e => { if (!selected.includes(a)) e.currentTarget.style.background = 'transparent' }}
            >{a}</button>
          ))}
          {filtered.length === 0 && <div className="text-xs px-2.5 py-2" style={{ color: 'var(--text-muted)' }}>No matches</div>}
        </div>
      </div>
      <RangeWithInputs
        label="Artist affinity range"
        minVal={params.artist_affinity_min ?? 0}
        maxVal={params.artist_affinity_max ?? 100}
        absMin={0} absMax={100}
        onMinChange={v => onChange({ ...params, artist_affinity_min: v })}
        onMaxChange={v => onChange({ ...params, artist_affinity_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function PlayCountFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Play count range"
        minVal={params.play_count_min ?? 0}
        maxVal={params.play_count_max ?? 500}
        absMin={0} absMax={500} step={1}
        onMinChange={v => onChange({ ...params, play_count_min: v })}
        onMaxChange={v => onChange({ ...params, play_count_max: v })}
        unit="plays"
      />
      <div>
        <div className="section-label mb-1.5">Order</div>
        <div className="flex gap-1.5">
          {[{ v: 'desc', label: 'Most played first' }, { v: 'asc', label: 'Least played first' }].map(({ v, label }) => (
            <button
              key={v}
              onClick={() => onChange({ ...params, order: v })}
              className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
              style={{
                background: (params.order ?? 'desc') === v ? 'var(--accent-soft)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${(params.order ?? 'desc') === v ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
                color: (params.order ?? 'desc') === v ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >{label}</button>
          ))}
        </div>
      </div>
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function PlayRecencyFilters({ params, onChange }) {
  const mode = params.mode ?? 'within'
  return (
    <div className="space-y-5">
      <div>
        <div className="section-label mb-1.5">Mode</div>
        <div className="flex gap-1.5">
          {[{ v: 'within', label: 'Played within' }, { v: 'older', label: 'Played more than' }].map(({ v, label }) => (
            <button
              key={v}
              onClick={() => onChange({ ...params, mode: v })}
              className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
              style={{
                background: mode === v ? 'var(--accent-soft)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${mode === v ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
                color: mode === v ? 'var(--accent)' : 'var(--text-secondary)',
              }}
            >{label}</button>
          ))}
        </div>
      </div>
      <SingleSliderWithInput
        label={mode === 'within' ? 'Within the last N days' : 'More than N days ago'}
        value={params.days ?? 30}
        min={1} max={365} step={1}
        onChange={v => onChange({ ...params, days: v })}
        unit=" days"
      />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function GlobalPopularityFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Popularity range"
        minVal={params.popularity_min ?? 0}
        maxVal={params.popularity_max ?? 100}
        absMin={0} absMax={100}
        onMinChange={v => onChange({ ...params, popularity_min: v })}
        onMaxChange={v => onChange({ ...params, popularity_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function DiscoveryFilters({ params, onChange }) {
  const stranger = params.stranger_pct    ?? 34
  const acquaint = params.acquaintance_pct ?? 33
  const familiar = params.familiar_pct    ?? 33
  const total    = stranger + acquaint + familiar
  const warn     = Math.abs(total - 100) > 1

  const tiers = [
    { key: 'stranger_pct',    label: 'Stranger',     color: '#f472b6', hint: "Artists you've never heard" },
    { key: 'acquaintance_pct',label: 'Acquaintance', color: '#fb923c', hint: "Artists you've barely heard" },
    { key: 'familiar_pct',    label: 'Familiar',     color: '#34d399', hint: "Artists you know but haven't overplayed" },
  ]

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="section-label">Familiarity split</span>
          <span className="text-xs font-mono font-semibold" style={{ color: warn ? 'var(--danger)' : 'var(--text-muted)' }}>
            {total}/100 {warn && '⚠ must equal 100'}
          </span>
        </div>
        {/* Visual proportion bar */}
        <div className="flex h-2 rounded-full overflow-hidden mb-4 gap-px">
          {tiers.map(({ key, color }) => {
            const val = params[key] ?? (key === 'stranger_pct' ? 34 : 33)
            return <div key={key} style={{ width: `${val}%`, background: color, transition: 'width 0.15s' }} title={`${val}%`} />
          })}
        </div>
        <div className="grid grid-cols-3 gap-3">
          {tiers.map(({ key, label, color, hint }) => {
            const val = params[key] ?? (key === 'stranger_pct' ? 34 : 33)
            const pct = val
            return (
              <div key={key}>
                <div className="flex items-center gap-1 mb-2">
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
                  <span className="text-[10px] font-semibold" style={{ color }} title={hint}>{label}</span>
                </div>
                {/* Slider */}
                <div className="relative mb-1.5" style={{ height: 16 }}>
                  <div className="absolute rounded-full" style={{ top: 6, left: 0, right: 0, height: 4, background: 'var(--bg-overlay)' }} />
                  <div className="absolute rounded-full" style={{ top: 6, height: 4, left: 0, width: `${pct}%`, background: color, opacity: 0.7 }} />
                  <input
                    type="range" min={0} max={100} step={1} value={val}
                    onChange={e => onChange({ ...params, [key]: Number(e.target.value) })}
                    className="absolute w-full opacity-0 cursor-pointer"
                    style={{ top: 0, height: '100%' }}
                  />
                  <div
                    className="absolute pointer-events-none rounded-full border-2"
                    style={{ top: 0, width: 16, height: 16, left: `calc(${pct}% - 8px)`, background: color, borderColor: 'var(--bg)' }}
                  />
                </div>
                <div className="flex items-center gap-1">
                  <input
                    type="number" min={0} max={100} value={val}
                    onChange={e => onChange({ ...params, [key]: Math.max(0, Math.min(100, Number(e.target.value))) })}
                    className="input w-full text-center text-xs"
                    style={warn ? { borderColor: 'rgba(248,113,113,0.4)' } : {}}
                  />
                  <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }}>%</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
      <RangeWithInputs
        label="Popularity range"
        minVal={params.popularity_min ?? 0}
        maxVal={params.popularity_max ?? 100}
        absMin={0} absMax={100}
        onMinChange={v => onChange({ ...params, popularity_min: v })}
        onMaxChange={v => onChange({ ...params, popularity_max: v })}
      />
    </div>
  )
}

function FavoritesFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function SkipRateFilters({ params, onChange }) {
  const lo = params.skip_penalty_min ?? 0.0
  const hi = params.skip_penalty_max ?? 0.3
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Skip penalty range (0 = never skipped · 100 = always)"
        minVal={Math.round(lo * 100)}
        maxVal={Math.round(hi * 100)}
        absMin={0} absMax={100} step={1}
        onMinChange={v => onChange({ ...params, skip_penalty_min: v / 100 })}
        onMaxChange={v => onChange({ ...params, skip_penalty_max: v / 100 })}
        unit="%"
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function ReplayBoostFilters({ params, onChange }) {
  const lo = parseFloat((params.boost_min ?? 0.1).toFixed(1))
  const hi = parseFloat((params.boost_max ?? 12).toFixed(1))
  return (
    <div className="space-y-5">
      <SingleSliderWithInput
        label="Minimum replay boost"
        value={lo}
        min={0.1} max={12} step={0.1}
        onChange={v => onChange({ ...params, boost_min: v, boost_max: Math.max(hi, v) })}
      />
      <SingleSliderWithInput
        label="Maximum replay boost"
        value={hi}
        min={0.1} max={12} step={0.1}
        onChange={v => onChange({ ...params, boost_max: v, boost_min: Math.min(lo, v) })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function NoveltyFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Novelty score range (0 = low fit · 100 = perfect match)"
        minVal={params.novelty_min ?? 0}
        maxVal={params.novelty_max ?? 100}
        absMin={0} absMax={100} step={1}
        onMinChange={v => onChange({ ...params, novelty_min: v })}
        onMaxChange={v => onChange({ ...params, novelty_max: v })}
      />
      <MaxPerArtist value={params.max_per_artist} onChange={v => onChange({ ...params, max_per_artist: v })} />
    </div>
  )
}

function RecencyScoreFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Recency score (0 = over a year ago · 100 = last 30 days)"
        minVal={params.recency_min ?? 0}
        maxVal={params.recency_max ?? 100}
        absMin={0} absMax={100} step={1}
        onMinChange={v => onChange({ ...params, recency_min: v })}
        onMaxChange={v => onChange({ ...params, recency_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'played'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function SkipStreakFilters({ params, onChange }) {
  const lo = params.streak_min ?? 0
  const hi = params.streak_max ?? 2
  return (
    <div className="space-y-5">
      <div>
        <div className="section-label mb-2">Consecutive skip streak range</div>
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-[10px] mb-1" style={{ color: 'var(--text-muted)' }}>Min</div>
            <input type="number" min={0} max={20} value={lo}
              onChange={e => onChange({ ...params, streak_min: Math.max(0, Math.min(hi, Number(e.target.value))) })}
              className="input w-full text-center text-xs" />
          </div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>to</div>
          <div className="flex-1">
            <div className="text-[10px] mb-1" style={{ color: 'var(--text-muted)' }}>Max</div>
            <input type="number" min={0} max={20} value={hi}
              onChange={e => onChange({ ...params, streak_max: Math.max(lo, Math.min(20, Number(e.target.value))) })}
              className="input w-full text-center text-xs" />
          </div>
        </div>
      </div>
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

// ── Audio analysis filter components ─────────────────────────────────────────

// Chromatic notes with enharmonic display names
const CHROMATIC_NOTES = [
  { root: 'C',  display: 'C' },
  { root: 'C#', display: 'C# / D♭' },
  { root: 'D',  display: 'D' },
  { root: 'D#', display: 'D# / E♭' },
  { root: 'E',  display: 'E' },
  { root: 'F',  display: 'F' },
  { root: 'F#', display: 'F# / G♭' },
  { root: 'G',  display: 'G' },
  { root: 'G#', display: 'G# / A♭' },
  { root: 'A',  display: 'A' },
  { root: 'A#', display: 'A# / B♭' },
  { root: 'B',  display: 'B' },
]

function BpmRangeFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Tempo range"
        minVal={params.bpm_min ?? 120}
        maxVal={params.bpm_max ?? 160}
        absMin={40} absMax={220} step={1}
        onMinChange={v => onChange({ ...params, bpm_min: v })}
        onMaxChange={v => onChange({ ...params, bpm_max: v })}
        unit=" BPM"
      />

      {/* Harmonic BPM toggle */}
      <div className="flex items-start gap-3 px-3 py-2.5 rounded-xl cursor-pointer select-none"
           style={{ background: params.harmonic ? 'rgba(248,113,113,0.08)' : 'rgba(255,255,255,0.03)', border: `1px solid ${params.harmonic ? 'rgba(248,113,113,0.25)' : 'var(--border)'}` }}
           onClick={() => onChange({ ...params, harmonic: !params.harmonic })}>
        <div className="w-4 h-4 rounded flex items-center justify-center flex-shrink-0 mt-0.5"
             style={{ background: params.harmonic ? '#f87171' : 'transparent', border: `2px solid ${params.harmonic ? '#f87171' : 'var(--border)'}` }}>
          {params.harmonic && <svg width="8" height="6" viewBox="0 0 8 6"><path d="M1 3l2 2 4-4" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round"/></svg>}
        </div>
        <div>
          <div className="text-xs font-medium" style={{ color: params.harmonic ? '#f87171' : 'var(--text-primary)' }}>Harmonic BPM</div>
          <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            Also include songs at half and double tempo — e.g. 120 BPM also matches 60 and 240 BPM since the beat feel is identical.
          </div>
        </div>
      </div>

      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function MusicalKeyFilters({ params, onChange }) {
  const mode     = params.mode  ?? 'all'
  const selected = params.notes ?? []

  const toggle = (root) =>
    onChange({ ...params, notes: selected.includes(root) ? selected.filter(n => n !== root) : [...selected, root] })

  const modes = [
    { v: 'all',   label: 'All modes' },
    { v: 'major', label: 'Major only' },
    { v: 'minor', label: 'Minor only' },
  ]

  return (
    <div className="space-y-5">
      {/* Mode */}
      <div>
        <div className="section-label mb-1.5">Mode</div>
        <div className="flex gap-1.5">
          {modes.map(({ v, label }) => (
            <button key={v} onClick={() => onChange({ ...params, mode: v })}
                    className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
                    style={{
                      background: mode === v ? 'rgba(167,139,250,0.15)' : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${mode === v ? 'rgba(167,139,250,0.4)' : 'var(--border)'}`,
                      color: mode === v ? '#a78bfa' : 'var(--text-secondary)',
                    }}>{label}</button>
          ))}
        </div>
      </div>

      {/* Note picker */}
      <div>
        <div className="section-label mb-1.5">
          Root note <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(leave empty = all)</span>
        </div>
        <div className="grid grid-cols-3 gap-1.5">
          {CHROMATIC_NOTES.map(({ root, display }) => (
            <button key={root} onClick={() => toggle(root)}
                    className="px-2 py-1.5 rounded-lg text-xs font-medium transition-all text-center"
                    style={{
                      background: selected.includes(root) ? 'rgba(167,139,250,0.12)' : 'rgba(255,255,255,0.03)',
                      border: `1px solid ${selected.includes(root) ? 'rgba(167,139,250,0.4)' : 'var(--border)'}`,
                      color: selected.includes(root) ? '#a78bfa' : 'var(--text-secondary)',
                    }}>
              {display}
            </button>
          ))}
        </div>
        {selected.length > 0 && (
          <button onClick={() => onChange({ ...params, notes: [] })}
                  className="mt-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Clear selection (show all keys)
          </button>
        )}
      </div>

      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function EnergyFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Energy range"
        minVal={params.energy_min ?? 0.4} maxVal={params.energy_max ?? 1.0}
        absMin={0} absMax={1} step={0.01}
        onMinChange={v => onChange({ ...params, energy_min: v })}
        onMaxChange={v => onChange({ ...params, energy_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function LoudnessFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Loudness range (dBFS)"
        minVal={params.loudness_min ?? -30} maxVal={params.loudness_max ?? 0}
        absMin={-60} absMax={0} step={1}
        onMinChange={v => onChange({ ...params, loudness_min: v })}
        onMaxChange={v => onChange({ ...params, loudness_max: v })}
        unit=" dB"
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function BeatStrengthFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Beat strength range"
        minVal={params.beat_min ?? 0.4} maxVal={params.beat_max ?? 1.0}
        absMin={0} absMax={1} step={0.01}
        onMinChange={v => onChange({ ...params, beat_min: v })}
        onMaxChange={v => onChange({ ...params, beat_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function TimeSignatureFilters({ params, onChange }) {
  const sigs = params.time_sigs ?? [4]
  const toggle = v => onChange({ ...params, time_sigs: sigs.includes(v) ? sigs.filter(x => x !== v) : [...sigs, v] })
  return (
    <div className="space-y-5">
      <div>
        <div className="section-label mb-3">Beats per bar</div>
        <div className="flex gap-3">
          {[3, 4].map(v => (
            <button key={v} onClick={() => toggle(v)}
              className="flex-1 py-2.5 rounded-xl text-sm font-semibold transition-all"
              style={{ background: sigs.includes(v) ? 'rgba(52,211,153,0.1)' : 'rgba(255,255,255,0.03)', border: `1px solid ${sigs.includes(v) ? 'rgba(52,211,153,0.4)' : 'var(--border)'}`, color: sigs.includes(v) ? '#34d399' : 'var(--text-secondary)' }}>
              {v}/4
              <div className="text-[10px] font-normal mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {v === 3 ? 'Waltz / triple' : 'Common time'}
              </div>
            </button>
          ))}
        </div>
      </div>
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

function AcousticnessFilters({ params, onChange }) {
  return (
    <div className="space-y-5">
      <RangeWithInputs
        label="Acousticness range"
        minVal={params.acousticness_min ?? 0.0} maxVal={params.acousticness_max ?? 1.0}
        absMin={0} absMax={1} step={0.01}
        onMinChange={v => onChange({ ...params, acousticness_min: v })}
        onMaxChange={v => onChange({ ...params, acousticness_max: v })}
      />
      <PlayedFilter value={params.played_filter ?? 'all'} onChange={v => onChange({ ...params, played_filter: v })} />
    </div>
  )
}

const FILTER_COMPONENTS = {
  final_score:       FinalScoreFilters,
  affinity:          AffinityFilters,
  genre:             GenreFilters,
  artist:            ArtistFilters,
  play_count:        PlayCountFilters,
  play_recency:      PlayRecencyFilters,
  global_popularity: GlobalPopularityFilters,
  discovery:         DiscoveryFilters,
  favorites:         FavoritesFilters,
  // New blocks
  skip_rate:         SkipRateFilters,
  replay_boost:      ReplayBoostFilters,
  novelty:           NoveltyFilters,
  recency_score:     RecencyScoreFilters,
  skip_streak:       SkipStreakFilters,
  // Audio analysis blocks
  bpm_range:         BpmRangeFilters,
  musical_key:       MusicalKeyFilters,
  energy:            EnergyFilters,
  loudness_db:       LoudnessFilters,
  beat_strength:     BeatStrengthFilters,
  time_signature:    TimeSignatureFilters,
  acousticness:      AcousticnessFilters,
}

// ── BlockCard ─────────────────────────────────────────────────────────────────

export default function BlockCard({
  block,
  index,
  totalBlocks,
  totalWeight,
  onWeightChange,
  onParamsChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  genres,
  artists,
}) {
  const [expanded, setExpanded] = useState(false)
  const cfg = BLOCK_TYPES[block.block_type] || { label: block.block_type, icon: Layers, color: 'var(--text-muted)' }
  const Icon = cfg.icon
  const widthPct = totalWeight > 0 ? (block.weight / totalWeight) * 100 : 0
  const FilterComp = FILTER_COMPONENTS[block.block_type]

  return (
    <div
      className="rounded-xl overflow-hidden anim-fade-up"
      style={{ border: '1px solid var(--border)', background: 'var(--bg-surface)' }}
    >
      {/* Header row */}
      <div className="flex items-center gap-3 px-3 py-2.5">
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: `${cfg.color}18`, border: `1px solid ${cfg.color}28` }}
        >
          <Icon size={12} style={{ color: cfg.color }} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{cfg.label}</div>
        </div>

        {/* Weight input */}
        <div className="flex items-center gap-1.5">
          <input
            type="number" min={1} max={100} value={block.weight}
            onChange={e => onWeightChange(parseInt(e.target.value) || 0)}
            onClick={e => e.stopPropagation()}
            className="w-14 text-center rounded-lg px-2 py-1 text-xs font-mono transition-all"
            style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', color: 'var(--accent)' }}
          />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>%</span>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-0.5">
          <button onClick={onMoveUp} disabled={index === 0}
            className="p-1 rounded transition-colors disabled:opacity-20"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          ><ArrowUp size={11} /></button>
          <button onClick={onMoveDown} disabled={index === totalBlocks - 1}
            className="p-1 rounded transition-colors disabled:opacity-20"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          ><ArrowDown size={11} /></button>
          <button onClick={onDelete}
            className="p-1 rounded transition-colors ml-0.5"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          ><Trash2 size={11} /></button>
          <button onClick={() => setExpanded(v => !v)}
            className="p-1 rounded transition-colors ml-1"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          >{expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}</button>
        </div>
      </div>

      {/* Weight strip */}
      <div style={{ height: 3, background: 'var(--bg-overlay)' }}>
        <div style={{ height: '100%', width: `${Math.min(100, widthPct)}%`, background: cfg.color, transition: 'width 0.2s ease' }} />
      </div>

      {/* Expanded filters */}
      {expanded && FilterComp && (
        <div className="px-4 py-4" style={{ borderTop: '1px solid var(--border)', background: 'var(--bg)' }}>
          <FilterComp
            params={block.params || {}}
            onChange={onParamsChange}
            genres={genres || []}
            artists={artists || []}
          />
        </div>
      )}
    </div>
  )
}
