/**
 * MusicUniverseMap — Zoom-driven hierarchical music taste map
 *
 * Key design decisions:
 *   - Collision radius = node r + half estimated label width → no label overlap
 *   - Genre bubble sized to fit artists + their labels (not just dots)
 *   - Labels only render when apparent screen gap to nearest neighbor >= threshold
 *   - Clicking an artist temporarily expands its collision radius → neighbors scatter
 *   - Three zoom tiers, initial view always fits all genres on screen
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import * as d3 from 'd3'
import { apiFetch } from '../lib/api'

const API = '/api'
const ZOOM_SOLAR = 0.65  // artists become visible
const ZOOM_STAR  = 2.4   // track orbits visible + node shrink triggers

// Pixels of screen-space gap between node edges needed before a label shows
const LABEL_GAP_THRESHOLD = 22

const GENRE_COLORS = [
  '#7c3aed','#0891b2','#059669','#d97706','#dc2626',
  '#1d4ed8','#be185d','#065f46','#7c2d12','#0c4a6e',
  '#4a1942','#92400e','#312e81','#064e3b','#7f1d1d',
  '#1e3a5f','#14532d','#78350f','#4c1d95','#1c1917',
]
const pickColor = i => GENRE_COLORS[i % GENRE_COLORS.length]

// Genre color: base hue from palette, brightness/saturation driven by affinity.
// High affinity (100): vivid, bright.  Low affinity (0): muted, dark.
function genreAffinityColor(index, affinity) {
  const base = d3.hsl(pickColor(index))
  const t = Math.max(0, Math.min(100, affinity || 0)) / 100
  base.l = 0.22 + t * 0.33   // luminance: 0.22 dark → 0.55 bright
  base.s = 0.35 + t * 0.65   // saturation: 0.35 grey-ish → 1.0 vivid
  return base.formatHex()
}

function affinityColor(a) {
  const t = Math.max(0, Math.min(100, a)) / 100
  if (t < 0.33) return d3.interpolateRgb('#4b5563','#0891b2')(t / 0.33)
  if (t < 0.66) return d3.interpolateRgb('#0891b2','#7c3aed')((t - 0.33) / 0.33)
  return d3.interpolateRgb('#7c3aed','#f59e0b')((t - 0.66) / 0.34)
}

// Taste drift: blend affinity color with recency & trend signals
function driftColor(baseHex, daysSince, trend) {
  if (daysSince == null) return baseHex
  const c = d3.color(baseHex)
  if (!c) return baseHex
  const base = d3.hsl(c)
  if (daysSince > 180) {
    const stale = Math.min(1, (daysSince - 180) / 365)
    base.s = Math.max(0.08, base.s * (1 - stale * 0.65))
    base.l = Math.max(0.14, base.l * (1 - stale * 0.35))
    return base.formatHex()
  }
  if (daysSince < 30 && trend === 'rising') {
    base.s = Math.min(1.0, base.s * 1.18)
    base.l = Math.min(0.72, base.l * 1.14)
    return base.formatHex()
  }
  return baseHex
}

// Estimate label half-width (pixels) from artist name length
function estLabelHalfW(name, fontSize) {
  return Math.ceil((name || '').length * fontSize * 0.32)
}

function artistR(affinity, plays) {
  const ps = Math.min(100, (Math.log1p(plays || 0) / Math.log1p(1000)) * 100)
  return 6 + ((affinity * 0.7 + ps * 0.3) / 100) * 14
}

// ── Track detail panel ────────────────────────────────────────────────────
function TrackPanel({ selectedTrack, onClose }) {
  if (!selectedTrack) return null
  const { track, artistColor } = selectedTrack
  const fav = track.is_favorite
  const cd  = !!track.cooldown_until
  const score = track.final_score != null ? Math.round(Number(track.final_score)) : null

  const fmtDate = iso => {
    if (!iso) return null
    try { return new Date(iso.endsWith('Z') ? iso : iso+'Z').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}) }
    catch { return null }
  }

  return (
    <div className="absolute bottom-16 right-4 w-64 z-30 rounded-xl overflow-hidden shadow-2xl"
         style={{
           background: 'rgba(7,9,15,0.97)',
           border: `1px solid ${artistColor}44`,
           backdropFilter: 'blur(16px)',
           boxShadow: `0 0 0 1px rgba(255,255,255,0.04), 0 20px 40px rgba(0,0,0,0.8), 0 0 20px ${artistColor}22`,
         }}>
      <div className="relative px-4 pt-3 pb-2.5"
           style={{ background: `linear-gradient(135deg, rgba(7,9,15,0.9) 0%, ${artistColor}18 100%)`, borderBottom: `1px solid ${artistColor}22` }}>
        <button onClick={onClose}
                className="absolute top-2.5 right-3 w-5 h-5 rounded-full flex items-center justify-center
                           text-slate-500 hover:text-white hover:bg-white/10 transition-colors text-xs leading-none">
          ×
        </button>
        <div className="flex items-start gap-2 pr-5">
          {fav && <span className="text-[#f59e0b] text-sm mt-0.5">★</span>}
          <div className="min-w-0">
            <div className="text-[12px] font-semibold text-white leading-tight truncate">{track.track_name}</div>
            {track.album_name && (
              <div className="text-[10px] text-slate-400 mt-0.5 truncate">{track.album_name}</div>
            )}
          </div>
        </div>
      </div>
      <div className="px-4 py-3 space-y-2.5" style={{ background: 'rgba(7,9,15,0.95)' }}>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <div className="text-[9px] uppercase tracking-widest text-slate-500 mb-0.5">Plays</div>
            <div className="text-[13px] font-bold text-white">{(track.play_count||0).toLocaleString()}</div>
          </div>
          {score != null && (
            <div>
              <div className="text-[9px] uppercase tracking-widest text-slate-500 mb-0.5">Score</div>
              <div className="text-[13px] font-bold" style={{ color: score>=70?'#4ade80':score>=40?artistColor:'#64748b' }}>{score}</div>
            </div>
          )}
          <div>
            <div className="text-[9px] uppercase tracking-widest text-slate-500 mb-0.5">Status</div>
            <div className="text-[11px] font-medium" style={{ color: cd?'#f97316':fav?'#f59e0b':'#94a3b8' }}>
              {cd ? '⏸ Cooldown' : fav ? '★ Fav' : '▶ Active'}
            </div>
          </div>
        </div>
        {cd && (
          <div className="text-[10px] text-orange-400/70 bg-orange-900/20 rounded px-2 py-1">
            Cooldown until {fmtDate(track.cooldown_until) || '…'}
          </div>
        )}
        {/* Score bar */}
        {score != null && (
          <div>
            <div className="h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.08)' }}>
              <div className="h-full rounded-full transition-all"
                   style={{ width: `${score}%`, background: score>=70?'#4ade80':score>=40?artistColor:'#475569' }} />
            </div>
            <div className="text-[9px] text-slate-600 mt-0.5">Recommendation score</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Artist detail panel ────────────────────────────────────────────────────
function ArtistPanel({ detail, onClose }) {
  if (!detail) return null
  return (
    <div className="absolute top-4 right-4 w-72 z-30 rounded-xl overflow-hidden shadow-2xl"
         style={{
           background: 'rgba(7, 9, 15, 0.97)',
           border: '1px solid rgba(255,255,255,0.12)',
           backdropFilter: 'blur(16px)',
           boxShadow: '0 0 0 1px rgba(255,255,255,0.05), 0 24px 48px rgba(0,0,0,0.8)',
         }}>
      <div className="relative px-4 pt-4 pb-3"
           style={{ background: 'linear-gradient(135deg, rgba(15,23,42,0.95) 0%, rgba(30,27,75,0.95) 100%)' }}>
        <button onClick={onClose}
                className="absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center
                           text-slate-400 hover:text-white hover:bg-white/10 transition-colors text-sm leading-none">
          ×
        </button>
        <div className="text-sm font-bold text-white pr-6 truncate">{detail.artist_name}</div>
        <div className="text-[11px] mt-0.5 text-slate-400">{detail.primary_genre || 'Unknown genre'}</div>
        <div className="flex gap-3 mt-3 flex-wrap">
          {[
            ['Affinity', detail.affinity_score?.toFixed(0), 'var(--accent)'],
            ['Plays', detail.total_plays?.toLocaleString(), 'white'],
            detail.popularity_score != null ? ['Global', detail.popularity_score?.toFixed(0), 'white'] : null,
            detail.skip_rate > 0.15 ? ['Skip %', (detail.skip_rate*100).toFixed(0)+'%', '#f87171'] : null,
          ].filter(Boolean).map(([label, val, color]) => (
            <div key={label}>
              <div className="text-[9px] uppercase tracking-widest text-slate-500">{label}</div>
              <div className="text-base font-bold" style={{ color }}>{val}</div>
            </div>
          ))}
        </div>
        {/* Recency + trend row */}
        <div className="flex items-center gap-2 mt-2 flex-wrap">
          {detail.trend_direction && detail.trend_direction !== 'stable' && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
                  style={{
                    background: detail.trend_direction==='rising' ? 'rgba(74,222,128,0.15)' : 'rgba(248,113,113,0.15)',
                    color: detail.trend_direction==='rising' ? '#4ade80' : '#f87171',
                  }}>
              {detail.trend_direction==='rising' ? '↑ Rising' : '↓ Falling'} on Last.fm
              {detail.trend_pct ? ` ${detail.trend_pct>0?'+':''}${detail.trend_pct.toFixed(0)}%` : ''}
            </span>
          )}
          {detail.replay_boost > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                  style={{background:'rgba(167,139,250,0.15)',color:'#a78bfa'}}>
              ⚡ Replay boost +{detail.replay_boost.toFixed(1)}
            </span>
          )}
        </div>
      </div>
      <div className="px-4 py-3 space-y-3 max-h-80 overflow-y-auto"
           style={{ background: 'rgba(7,9,15,0.95)' }}>
        {detail.tags?.length > 0 && (
          <div>
            <div className="text-[9px] uppercase tracking-widest mb-1.5 text-slate-500">Tags</div>
            <div className="flex flex-wrap gap-1">
              {detail.tags.map(t => (
                <span key={t} className="text-[10px] px-2 py-0.5 rounded-full"
                      style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)' }}>{t}</span>
              ))}
            </div>
          </div>
        )}
        {detail.biography && (
          <div>
            <div className="text-[9px] uppercase tracking-widest mb-1 text-slate-500">About</div>
            <p className="text-[11px] leading-relaxed line-clamp-4 text-slate-400">
              {detail.biography.replace(/<[^>]*>/g, '')}
            </p>
          </div>
        )}
        {detail.top_tracks?.length > 0 && (
          <div>
            <div className="text-[9px] uppercase tracking-widest mb-2 text-slate-500">Top Tracks</div>
            <div className="space-y-1.5">
              {detail.top_tracks.slice(0, 8).map((t, i) => (
                <div key={i} className="flex items-center gap-2">
                  <div className="w-4 text-[9px] tabular-nums text-right shrink-0 text-slate-600">{i + 1}</div>
                  <div className="flex-1 min-w-0">
                    <div className={`text-[11px] truncate ${t.cooldown_until ? 'line-through opacity-40' : ''}`}
                         style={{ color: t.is_favorite ? '#f59e0b' : '#e2e8f0' }}>
                      {t.is_favorite && <span className="mr-1">★</span>}{t.track_name}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <span className="text-[10px] tabular-nums text-slate-500">×{t.play_count}</span>
                    {(t.skip_penalty||0) > 0.2 && (
                      <span className="text-[9px] text-red-400 opacity-70" title="High skip rate">⚡</span>
                    )}
                    {(t.replay_boost||0) > 0 && (
                      <span className="text-[9px] text-purple-400 opacity-70" title="Replay boost: you've been actively returning to this artist recently">⚡</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {detail.similar_artists?.length > 0 && (
          <div>
            <div className="text-[9px] uppercase tracking-widest mb-1.5 text-slate-500">Similar Artists</div>
            <div className="flex flex-wrap gap-1">
              {detail.similar_artists.map(s => (
                <span key={s.name} className="text-[10px] px-2 py-0.5 rounded-full"
                      style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--accent)' }}>{s.name}</span>
              ))}
            </div>
          </div>
        )}
        {detail.lastfm_url && (
          <a href={detail.lastfm_url} target="_blank" rel="noopener noreferrer"
             className="block text-center text-[10px] py-2 rounded-lg hover:opacity-80 transition-opacity mt-1"
             style={{ background: 'rgba(255,255,255,0.06)', color: 'var(--accent)' }}>
            View on Last.fm →
          </a>
        )}
      </div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────
export default function MusicUniverseMap({ userId }) {
  const svgRef        = useRef(null)
  const zoomRef       = useRef(null)
  const simRef        = useRef(null)
  const trackLayerRef = useRef(null)
  const applyZoomRef  = useRef(null)
  const currentKRef   = useRef(0.5)   // live zoom scale for tick-time label culling
  const selectedRef   = useRef(null)  // mirror of selected state for collision force
  const connectedRef  = useRef(new Set())  // IDs of artists connected to selected
  const highlightRef  = useRef(false)     // true while an artist is selected
  const hoveredRef    = useRef(null)      // ID of currently hovered artist

  const [graphData,     setGraphData]     = useState(null)
  const [loading,       setLoading]       = useState(true)
  const [error,         setError]         = useState(null)
  const [zoomLevel,     setZoomLevel]     = useState('galaxy')
  const [selected,      setSelected]      = useState(null)
  const [detail,        setDetail]        = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [trackCache,    setTrackCache]    = useState({})
  const [selectedTrack, setSelectedTrack] = useState(null)  // {track, x, y} for tooltip
  const [searchQuery,   setSearchQuery]   = useState('')
  const [minAffinity,   setMinAffinity]   = useState(0)
  const [nodeLimit,     setNodeLimit]     = useState(100)
  const [fullscreen,    setFullscreen]    = useState(false)
  const savedTransformRef = useRef(null)  // preserve pan/zoom across filter changes

  // fetchData does the actual network call. resetView=true only on explicit Refresh.
  const fetchData = useCallback((resetView = false) => {
    if (!userId) return
    setLoading(true); setError(null)
    if (resetView) {
      // Explicit refresh: clear selection and reset view to fit-to-screen
      setSelected(null); setDetail(null)
      selectedRef.current = null
      connectedRef.current = new Set()
      highlightRef.current = false
      savedTransformRef.current = null
    }
    apiFetch(`${API}/graph/network?user_id=${userId}&limit=${nodeLimit}&min_affinity=${minAffinity}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => { setGraphData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [userId, nodeLimit, minAffinity])

  // Slider/filter changes trigger a refetch but preserve pan/zoom
  useEffect(() => { fetchData(false) }, [fetchData])

  // Explicit refresh button — resets view
  const fetchGraph = useCallback(() => fetchData(true), [fetchData])

  const fetchArtistDetail = useCallback((artistName) => {
    if (!userId) return
    setDetailLoading(true)
    apiFetch(`${API}/graph/artist/${encodeURIComponent(artistName)}?user_id=${userId}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => {
        setDetail(d); setDetailLoading(false)
        setTrackCache(prev => ({ ...prev, [artistName]: d.top_tracks || [] }))
      })
      .catch(() => setDetailLoading(false))
  }, [userId])

  // ── Build D3 scene ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!graphData || !svgRef.current) return
    const { nodes: rawNodes, edges: rawEdges } = graphData
    if (!rawNodes?.length || !rawNodes.some(n => n.type === 'artist')) return

    if (simRef.current) { simRef.current.stop(); simRef.current = null }

    const container = svgRef.current.parentElement
    const W = container.clientWidth  || 900
    const H = container.clientHeight || 650

    d3.select(svgRef.current).selectAll('*').remove()
    trackLayerRef.current = null

    const svg = d3.select(svgRef.current).attr('width', W).attr('height', H)

    // Starfield
    const starsG = svg.append('g').attr('pointer-events','none')
    for (let i = 0; i < 80; i++) {
      starsG.append('circle')
        .attr('cx', Math.random()*W).attr('cy', Math.random()*H)
        .attr('r',  Math.random()*0.9)
        .attr('fill','white').attr('opacity', Math.random()*0.22+0.04)
    }

    const g = svg.append('g').attr('class','universe')

    // ── Prepare nodes ────────────────────────────────────────────────────
    const apiGenres   = rawNodes.filter(n => n.type === 'genre')
    const artistNodes = rawNodes.filter(n => n.type === 'artist')

    // Assign artist sizes first — label width needed for genre sizing
    const LABEL_FONT = 10
    artistNodes.forEach(an => {
      an.r       = artistR(an.affinity, an.plays)
      an.color   = driftColor(affinityColor(an.affinity), an.days_since_played, an.trend)
      an.labelHW = estLabelHalfW(an.label, LABEL_FONT)  // half-width of label in px
      // Effective collision radius includes the label so artists space out for text
      an.collR   = an.r + Math.max(an.labelHW, 14) + 10
    })

    let genreNodes = apiGenres.length > 0 ? apiGenres : (() => {
      const gmap = {}
      for (const an of artistNodes) {
        const label = an.genre || 'Unknown'
        if (!gmap[label]) gmap[label] = { label, aff:0, plays:0, count:0, idx:Object.keys(gmap).length }
        gmap[label].aff += an.affinity||0; gmap[label].plays += an.plays||0; gmap[label].count++
      }
      return Object.values(gmap).map(gm => ({
        id:`genre:${gm.label}`, label:gm.label, type:'genre',
        affinity: gm.aff/Math.max(1,gm.count), plays:gm.plays, _idx:gm.idx,
      }))
    })()

    // Size genre bubbles to contain artists + their labels + breathing room
    genreNodes.forEach((gn, i) => {
      gn.color = genreAffinityColor(gn._idx !== undefined ? gn._idx : i, gn.affinity)
      const members = artistNodes.filter(an => (an.genre||'Unknown') === gn.label)
      const n = members.length
      if (n === 0) {
        gn.r = 35
      } else {
        // Pack using effective collision radii (includes label space)
        const avgCollR = members.reduce((s,a) => s + a.collR, 0) / n
        // Area-based packing: A_container = π*R² ≥ n * π*r_eff² * packFactor
        // → R ≥ sqrt(n) * r_eff * sqrt(packFactor)
        const packR = Math.sqrt(n) * avgCollR * 2.0
        gn.r = Math.max(50, packR)
      }
    })

    // ── Genre radial layout ──────────────────────────────────────────────
    const gc = genreNodes.length
    genreNodes.sort((a,b) => {
      const na = artistNodes.filter(an=>(an.genre||'Unknown')===a.label).length
      const nb = artistNodes.filter(an=>(an.genre||'Unknown')===b.label).length
      return nb - na
    })

    // Ring radius: ensures no two adjacent bubbles touch
    // Place centers at distance ringR; chord between adjacent = 2*ringR*sin(π/gc)
    // We need chord ≥ r_a + r_b + gap for each adjacent pair
    // Simple conservative: ringR = (sumR + gc*gap) / (2*sin(π/gc)*gc) * gc
    const GAP_BETWEEN_GENRES = 60
    const sumR = genreNodes.reduce((s,gn)=>s+gn.r, 0)
    const ringR = Math.max(
      300,
      (sumR + gc * GAP_BETWEEN_GENRES) / (Math.PI * 1.6)
    )

    genreNodes.forEach((gn, i) => {
      const angle = (i / gc) * 2 * Math.PI - Math.PI / 2
      gn.x = Math.cos(angle) * ringR
      gn.y = Math.sin(angle) * ringR
      gn.fx = gn.x
      gn.fy = gn.y
    })

    const genreByLabel = Object.fromEntries(genreNodes.map(gn => [gn.label, gn]))
    const fallback = genreNodes[0] || { x:0, y:0, r:80 }

    // Initial artist scatter — spread out inside genre bubble
    artistNodes.forEach(an => {
      const p = genreByLabel[an.genre||'Unknown'] || fallback
      const safeR = Math.max(4, p.r - an.collR - 8)
      const angle = Math.random() * 2 * Math.PI
      const dist  = Math.random() * safeR
      an.x = p.x + Math.cos(angle) * dist
      an.y = p.y + Math.sin(angle) * dist
    })

    const simEdges = (rawEdges||[]).filter(e => e.type === 'similar')

    // ── Tag cross-genre bridge artists ───────────────────────────────────
    const artistGenreById = {}
    artistNodes.forEach(an => { artistGenreById[an.id] = an.genre || 'Unknown' })
    const bridgeIds = new Set()
    simEdges.forEach(e => {
      const s = e.source?.id || e.source, t = e.target?.id || e.target
      if (artistGenreById[s] && artistGenreById[t] && artistGenreById[s] !== artistGenreById[t]) {
        bridgeIds.add(s); bridgeIds.add(t)
      }
    })
    artistNodes.forEach(an => { an.isBridge = bridgeIds.has(an.id) })

    // ── Force simulation ─────────────────────────────────────────────────
    const allNodes = [...genreNodes, ...artistNodes]

    const sim = d3.forceSimulation(allNodes)
      // Similarity edges: shorter distance for strong similarity
      .force('link', d3.forceLink(simEdges.filter(e=>(e.weight||0)>=0.15))
        .id(d => d.id)
        .distance(d => {
          const wa = allNodes.find(n=>n.id===d.source?.id||n.id===d.source)
          const wb = allNodes.find(n=>n.id===d.target?.id||n.id===d.target)
          const ra = wa?.collR || 20, rb = wb?.collR || 20
          // Min distance = sum of effective radii so labels don't overlap
          return (ra + rb) + (1 - (d.weight||0)) * 40
        })
        .strength(d => (d.weight||0.1) * 0.5)
      )
      // Cluster: pull artists toward genre center with distance-scaled strength
      .force('cluster', () => {
        for (const an of artistNodes) {
          const p = genreByLabel[an.genre||'Unknown'] || fallback
          const dx = p.x - an.x, dy = p.y - an.y
          const dist = Math.sqrt(dx*dx + dy*dy) || 1
          const limit = p.r - an.collR - 4
          // Always attract toward center
          const pull = 0.015 + (dist > limit ? 0.2 * Math.min(1, (dist-limit)/p.r) : 0)
          an.vx = (an.vx||0) + (dx/dist) * dist * pull
          an.vy = (an.vy||0) + (dy/dist) * dist * pull
        }
      })
      // Collision: use effective radius (includes label space)
      // When an artist is selected, expand its radius to push neighbors away
      .force('collision', d3.forceCollide(d => {
        if (d.type === 'genre') return 0
        const isSel = d.id === selectedRef.current
        const base  = d.collR || (d.r + 14)
        // When selected: collision covers the full orbit zone (nodeR + 52 orbit + 8 pad + label space)
        return isSel ? (d.r + 52 + 8 + 10) : base
      }).strength(0.9).iterations(3))
      .velocityDecay(0.4)
      .alphaDecay(0.015)

    simRef.current = sim

    // ── Draw: similarity edges ───────────────────────────────────────────
    const visEdges = simEdges.filter(e => (e.weight||0) >= 0.18)
    const linkSel = g.append('g').attr('class','links')
      .selectAll('line').data(visEdges).join('line')
      .attr('stroke','#fff').attr('stroke-opacity',0)
      .attr('stroke-width', d => Math.max(0.5, (d.weight||0)*1.8))

    // ── Draw: genre bubbles ──────────────────────────────────────────────
    const genreSel = g.append('g').attr('class','genres')
      .selectAll('g').data(genreNodes).join('g').attr('class','genre-node')

    genreSel.append('circle')   // atmospheric glow
      .attr('r', d=>d.r+20).attr('fill',d=>d.color).attr('fill-opacity',0.025)
      .attr('pointer-events','none')
    genreSel.append('circle')   // main bubble
      .attr('r', d=>d.r).attr('fill',d=>d.color).attr('fill-opacity',0.06)
      .attr('stroke',d=>d.color).attr('stroke-opacity',0.28).attr('stroke-width',1.5)
    genreSel.each(function(d) { // affinity arc
      d3.select(this).append('path')
        .attr('d', d3.arc().innerRadius(d.r+1).outerRadius(d.r+4)
          .startAngle(-Math.PI/2)
          .endAngle(-Math.PI/2 + Math.min(0.98,(d.affinity||0)/100)*2*Math.PI)())
        .attr('fill',d.color).attr('fill-opacity',0.65).attr('pointer-events','none')
    })
    genreSel.append('text').attr('class','genre-label')
      .attr('text-anchor','middle').attr('dominant-baseline','middle')
      .attr('font-size', d=>Math.max(11, Math.min(22, d.r*0.2)))
      .attr('font-weight','700').attr('letter-spacing','0.06em')
      .attr('fill',d=>d.color).attr('fill-opacity',0.92).attr('pointer-events','none')
      .text(d=>d.label)
    genreSel.each(function(d) {
      const n = artistNodes.filter(an=>(an.genre||'Unknown')===d.label).length
      if (!n) return
      d3.select(this).append('text').attr('class','genre-count')
        .attr('text-anchor','middle').attr('y',d.r*0.28)
        .attr('font-size',Math.max(9,Math.min(13,d.r*0.14)))
        .attr('fill',d.color).attr('fill-opacity',0.45).attr('pointer-events','none')
        .text(`${n} artist${n!==1?'s':''}`)
    })

    // ── Draw: artist nodes ───────────────────────────────────────────────
    const artistSel = g.append('g').attr('class','artists')
      .selectAll('g').data(artistNodes).join('g')
      .attr('class','artist-node').attr('cursor','pointer').attr('opacity',0)
      .call(d3.drag()
        .on('start',(e,d)=>{ if(!e.active) sim.alphaTarget(0.15).restart(); d.fx=d.x; d.fy=d.y })
        .on('drag', (e,d)=>{ d.fx=e.x; d.fy=e.y })
        .on('end',  (e,d)=>{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null }))
      .on('click',(e,d)=>{
        e.stopPropagation()
        setSelected(prev => {
          const isDeselect = prev?.id === d.id
          selectedRef.current = isDeselect ? null : d.id
          highlightRef.current  = !isDeselect
          // Build set of directly connected artist IDs
          if (isDeselect) {
            connectedRef.current = new Set()
          } else {
            const connected = new Set()
            for (const edge of visEdges) {
              const srcId = edge.source?.id || edge.source
              const tgtId = edge.target?.id || edge.target
              if (srcId === d.id) connected.add(tgtId)
              if (tgtId === d.id) connected.add(srcId)
            }
            connectedRef.current = connected
          }
          // Gently nudge only the immediate neighbors outward — no full restart
          // so the rest of the graph stays still
          if (!isDeselect) {
            const cx_ = d.x || 0, cy_ = d.y || 0
            const pushR = (d.r || 10) + 52 + 12  // orbit zone + padding
            for (const node of simRef.current?.nodes?.() || []) {
              if (node.id === d.id || node.type === 'genre') continue
              const dx = (node.x || 0) - cx_
              const dy = (node.y || 0) - cy_
              const dist = Math.sqrt(dx*dx + dy*dy) || 1
              if (dist < pushR) {
                // Push outward proportional to how deep inside the zone it is
                const overlap = (pushR - dist) / pushR
                node.vx = (node.vx || 0) + (dx/dist) * overlap * 3
                node.vy = (node.vy || 0) + (dy/dist) * overlap * 3
              }
            }
            // Tick at very low alpha so only the nudged nodes move
            if (simRef.current) simRef.current.alpha(0.05).restart()
          }
          if (isDeselect) { setDetail(null); return null }
          fetchArtistDetail(d.label)
          return d
        })
      })

    // ── Hover: subtle highlight of connected edges + neighbors ─────────────
    artistSel
      .on('mouseover', (e, d) => {
        if (highlightRef.current) return  // click selection takes priority
        hoveredRef.current = d.id

        // Build hover connected set inline (same logic as click)
        const hoverConn = new Set()
        for (const edge of visEdges) {
          const srcId = edge.source?.id || edge.source
          const tgtId = edge.target?.id || edge.target
          if (srcId === d.id) hoverConn.add(tgtId)
          if (tgtId === d.id) hoverConn.add(srcId)
        }

        // Dim unconnected artists via group opacity, strokes for connected
        artistSel
          .attr('opacity', n => n.id===d.id||hoverConn.has(n.id) ? 1 : 0.15)
        artistSel.selectAll('.artist-circle')
          .attr('stroke',        n => hoverConn.has(n.id) ? '#ffffff' : '#ffffff')
          .attr('stroke-width',  n => hoverConn.has(n.id) ? 2 : 1)
          .attr('stroke-opacity',n => hoverConn.has(n.id) ? 0.65 : 0.1)

        // Edges: highlight connected in bright white, dim rest
        linkSel
          .attr('stroke',        edge => {
            const s = edge.source?.id||edge.source, t = edge.target?.id||edge.target
            return (s===d.id||t===d.id) ? '#ffffff' : '#ffffff'
          })
          .attr('stroke-opacity', edge => {
            const s = edge.source?.id||edge.source, t = edge.target?.id||edge.target
            return (s===d.id||t===d.id)
              ? Math.max(0.55, edge.weight||0.55)
              : 0.03
          })
          .attr('stroke-width', edge => {
            const s = edge.source?.id||edge.source, t = edge.target?.id||edge.target
            return (s===d.id||t===d.id)
              ? Math.max(1.2, (edge.weight||0)*2.5)
              : Math.max(0.5,(edge.weight||0)*1.8)
          })
      })
      .on('mouseout', () => {
        if (highlightRef.current) return  // click selection takes priority
        hoveredRef.current = null

        // Restore group opacity (applyZoom will refine on next scroll)
        const kNow = currentKRef.current
        const fadeNow = Math.min(1, Math.max(0, (kNow - ZOOM_SOLAR*0.55) / (ZOOM_SOLAR*0.5)))
        artistSel.attr('opacity', fadeNow)
        artistSel.selectAll('.artist-circle')
          .attr('stroke', '#ffffff')
          .attr('stroke-width', 1)
          .attr('stroke-opacity', 0.15)

        // Restore edges to zoom-appropriate opacity (applyZoom will handle next zoom event,
        // but we need to restore immediately — read the current k)
        const k = currentKRef.current
        const inSolar = k >= ZOOM_SOLAR
        linkSel
          .attr('stroke', '#ffffff')
          .attr('stroke-width', d => Math.max(0.5,(d.weight||0)*1.8))
          .attr('stroke-opacity', !inSolar ? 0
            : d => Math.max(0.04, Math.min(0.45, (d.weight||0)*(k>=ZOOM_STAR?0.55:0.25))))
      })

    artistSel.append('circle')  // glow halo
      .attr('class','artist-halo')
      .attr('r',d=>d.r+5).attr('fill',d=>d.color).attr('fill-opacity',0.07)
      .attr('pointer-events','none')
    artistSel.append('circle').attr('class','artist-circle')  // body
      .attr('r',d=>d.r).attr('fill',d=>d.color).attr('fill-opacity',0.88)
      .attr('stroke','#fff').attr('stroke-opacity',0.15).attr('stroke-width',1)
    artistSel.filter(d=>d.has_favorite)
      .append('text').attr('text-anchor','middle').attr('dominant-baseline','central')
      .attr('font-size',d=>Math.max(6,d.r*0.5)).attr('fill','#fbbf24')
      .attr('pointer-events','none').text('★')

    // ── Skip rate arc (red arc, length = skip rate severity) ────────────────
    artistSel.filter(d => (d.skip_rate||0) > 0.15)
      .each(function(d) {
        const skipPct = Math.min(1, (d.skip_rate - 0.15) / 0.6)
        const arcR = d.r + 3
        const arcPath = d3.arc()
          .innerRadius(arcR).outerRadius(arcR + Math.max(1.5, skipPct * 3.5))
          .startAngle(Math.PI * 0.2)
          .endAngle(Math.PI * 0.2 + skipPct * 2 * Math.PI * 0.85)
        d3.select(this).append('path').attr('class','skip-arc')
          .attr('d', arcPath())
          .attr('fill','#ef4444').attr('fill-opacity', 0.55 + skipPct * 0.35)
          .attr('pointer-events','none')
      })

    // ── Bridge ring (cross-genre connector: dashed white ring) ───────────────
    artistSel.filter(d => d.isBridge)
      .append('circle').attr('class','bridge-ring')
      .attr('r', d => d.r + 6).attr('fill','none')
      .attr('stroke','#f8fafc').attr('stroke-opacity',0.35)
      .attr('stroke-width',1.2).attr('stroke-dasharray','3 2')
      .attr('pointer-events','none')

    // ── Trend arrow ──────────────────────────────────────────────────────────
    artistSel.filter(d => d.trend === 'rising' || d.trend === 'falling')
      .append('text').attr('class','trend-arrow')
      .attr('text-anchor','middle')
      .attr('x', d => d.r * 0.55).attr('y', d => -d.r * 0.55)
      .attr('font-size', d => Math.max(7, d.r * 0.55))
      .attr('fill', d => d.trend === 'rising' ? '#4ade80' : '#f87171')
      .attr('fill-opacity', 0.9).attr('pointer-events','none')
      .text(d => d.trend === 'rising' ? '↑' : '↓')

    // ── Replay boost ring (purple pulse ring) ────────────────────────────────
    artistSel.filter(d => (d.replay_boost||0) > 0)
      .append('circle').attr('class','replay-ring')
      .attr('r', d => d.r + 9).attr('fill','none')
      .attr('stroke','#a78bfa')
      .attr('stroke-opacity', d => Math.min(0.7, (d.replay_boost||0) / 12 * 0.7))
      .attr('stroke-width', d => Math.min(3, (d.replay_boost||0) / 4))
      .attr('pointer-events','none')

    // Label group: background pill + text
    // Positioned BELOW the node so it doesn't fight with the circle
    const labelG = artistSel.append('g').attr('class','artist-label-g').attr('opacity',0)
    labelG.append('rect').attr('class','artist-label-bg')
      .attr('rx',3)
      .attr('fill','#07090f').attr('fill-opacity',0.82)
      .attr('stroke','rgba(255,255,255,0.06)').attr('stroke-width',0.5)
    labelG.append('text').attr('class','artist-label-text')
      .attr('text-anchor','middle').attr('dominant-baseline','middle')
      .attr('font-size', LABEL_FONT)
      .attr('fill','#d1d5db').attr('pointer-events','none')
      .text(d=>d.label)

    // Track orbit layer
    const trackLayer = g.append('g').attr('class','tracks').attr('opacity',0)
    trackLayerRef.current = trackLayer

    // ── Simulation tick ──────────────────────────────────────────────────
    sim.on('tick', () => {
      genreSel.attr('transform', d=>`translate(${d.x},${d.y})`)
      artistSel.attr('transform', d=>`translate(${d.x},${d.y})`)
      linkSel
        .attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)
        .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y)

      const k = currentKRef.current
      const inSolar = k >= ZOOM_SOLAR

      // Per-tick label visibility: show label only if this node's nearest
      // neighbor is far enough away in screen space that labels won't collide
      artistSel.each(function(d) {
        // Position label below the node
        const labelYOffset = d.r + 12
        const textEl = d3.select(this).select('.artist-label-text')
        const bgEl   = d3.select(this).select('.artist-label-bg')
        textEl.attr('y', labelYOffset)
        bgEl.attr('y', labelYOffset - 7)

        // Measure text width for background rect
        const textNode = textEl.node()
        if (textNode) {
          try {
            const bb = textNode.getBBox()
            bgEl.attr('x', bb.x - 4).attr('y', bb.y - 3)
               .attr('width', bb.width + 8).attr('height', bb.height + 6)
          } catch(_) {}
        }

        if (!inSolar) {
          // Not zoomed in enough — keep labels hidden
          d3.select(this).select('.artist-label-g').attr('opacity', 0)
          return
        }

        // Find nearest neighbor in same genre for crowding check
        const sameGenre = artistNodes.filter(a => a !== d && (a.genre||'Unknown') === (d.genre||'Unknown'))
        let minGap = Infinity
        for (const n of sameGenre) {
          const dx = n.x - d.x, dy = n.y - d.y
          const dist = Math.sqrt(dx*dx + dy*dy)
          const gap = (dist - d.r - n.r) * k
          if (gap < minGap) minGap = gap
        }
        // Selected artist always shows its label regardless of crowding
        const isSel = d.id === selectedRef.current
        const showLabel = isSel || (minGap > LABEL_GAP_THRESHOLD)

        d3.select(this).select('.artist-label-g')
          .attr('opacity', showLabel ? Math.min(1, (minGap - LABEL_GAP_THRESHOLD/2) / 15) : 0)
      })
    })

    // ── Zoom visibility ──────────────────────────────────────────────────
    function applyZoom(k) {
      currentKRef.current = k
      const inSolar = k >= ZOOM_SOLAR
      const inStar  = k >= ZOOM_STAR

      // Artists: fade in entering solar. When zoomed to star AND an artist is
      // selected, dim other nodes heavily so the orbit/tracks are the focus.
      const baseFade = Math.min(1, Math.max(0, (k - ZOOM_SOLAR*0.55) / (ZOOM_SOLAR*0.5)))
      if (inStar && selectedRef.current) {
        const conn = connectedRef.current
        artistSel.transition().duration(150)
          .attr('opacity', d => {
            if (d.id === selectedRef.current) return 1
            if (conn.has(d.id)) return 0.55   // direct connections — visible
            return 0.06                        // everyone else — nearly invisible
          })
      } else {
        artistSel.transition().duration(80).attr('opacity', baseFade)
      }

      // Genre label fades as you zoom in past solar
      genreSel.selectAll('.genre-label')
        .transition().duration(80)
        .attr('fill-opacity', Math.max(0.1, 1.0 - Math.max(0, k - 0.35) * 0.65))
      genreSel.selectAll('.genre-count')
        .transition().duration(80)
        .attr('fill-opacity', Math.max(0, 0.45 - Math.max(0, k - 0.35) * 0.9))

      // Edges: only update opacity when NOT in highlight mode
      // (highlight mode uses its own gold colouring set in the selection effect)
      if (!highlightRef.current) {
        linkSel.transition().duration(120)
          .attr('stroke-opacity', !inSolar ? 0
            : d => Math.max(0.04, Math.min(0.45, (d.weight||0) * (inStar ? 0.55 : 0.25))))
      }

      // Tracks
      if (trackLayerRef.current)
        trackLayerRef.current.transition().duration(120).attr('opacity', inStar ? 1 : 0)
    }
    applyZoomRef.current = applyZoom

    // ── Zoom ─────────────────────────────────────────────────────────────
    const zoom = d3.zoom()
      .scaleExtent([0.1, 8])
      .on('zoom', event => {
        g.attr('transform', event.transform)
        const k = event.transform.k
        savedTransformRef.current = event.transform
        applyZoomRef.current?.(k)
        setZoomLevel(k < ZOOM_SOLAR ? 'galaxy' : k < ZOOM_STAR ? 'solar' : 'star')
      })
    zoomRef.current = zoom
    svg.call(zoom)

    // Restore previous transform if the user changed a filter, otherwise fit to screen
    const pad    = 70
    const extent = ringR + Math.max(...genreNodes.map(gn=>gn.r)) + pad
    const fitK   = Math.min(W, H) / (2 * extent) * 0.9
    const initT  = savedTransformRef.current
      || d3.zoomIdentity.translate(W/2, H/2).scale(fitK)
    svg.call(zoom.transform, initT)
    applyZoom(initT.k)

    svg.on('click', () => {
      selectedRef.current = null
      connectedRef.current = new Set()
      highlightRef.current = false
      hoveredRef.current = null
      setSelected(null); setDetail(null); setSelectedTrack(null)
    })

    return () => {
      sim.stop()
      simRef.current=null; trackLayerRef.current=null; applyZoomRef.current=null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, fullscreen])

  // ── Track orbit ──────────────────────────────────────────────────────────
  useEffect(() => {
    const tl = trackLayerRef.current
    if (!tl) return
    tl.selectAll('*').remove()

    // Always restore any previously shrunken node first (handles zoom-out case)
    if (svgRef.current) {
      d3.select(svgRef.current).selectAll('.artist-node')
        .each(function(d) {
          // Unpin so simulation can move it freely again
          d.fx = null; d.fy = null
          const ng = d3.select(this)
          ng.select('.artist-halo')
            .transition().duration(300)
            .attr('r', d.r + 5)
          ng.select('.artist-circle')
            .transition().duration(300)
            .attr('r', d.r)
          ng.select('.artist-label-g')
            .transition().duration(300)
            .attr('transform', null)
          ng.select('.artist-label-text')
            .transition().duration(300)
            .attr('font-size', Math.min(11, Math.max(8, d.r*0.65)))
            .attr('fill', '#d1d5db')
            .attr('font-weight', 'normal')
        })
    }

    if (!selected) return
    // Only show track orbit when zoomed in past ZOOM_STAR
    if (currentKRef.current < ZOOM_STAR) return
    const tracks = trackCache[selected.label] || []
    if (!tracks.length) return

    const cx = selected.x||0, cy = selected.y||0
    const n = tracks.length
    const nodeR  = selected.r||10

    // Scale orbit radius so moons are never closer than ~MIN_ARC_PX px of arc apart.
    // Each moon needs room for its dot (r=10) + label (~60px wide) on both sides.
    // arcSpacing = 2π * orbitR / n  →  orbitR = n * minArc / (2π)
    const MIN_ARC_PX = 72          // minimum arc-length between moon centers
    const minOrbitR  = (n * MIN_ARC_PX) / (2 * Math.PI)
    const orbitR     = Math.max(nodeR + 52, minOrbitR)

    // How much arc space each moon actually gets (in pixels)
    const arcPerMoon = (2 * Math.PI * orbitR) / n
    // Scale font-size: generous when there's room, tiny when crowded
    // arcPerMoon ~72px → font 8.5, ~130px+ → font 10, <50px → font 7
    const labelFont  = Math.max(6.5, Math.min(10, arcPerMoon / 9))
    // Max label chars: scale with arc space (roughly how many chars fit)
    const maxChars   = Math.max(8, Math.floor(arcPerMoon / (labelFont * 0.62)))

    // Pin the selected node at its current position so the simulation
    // can't push it away while the track orbit is displayed
    if (selected.__simNode) {
      selected.__simNode.fx = cx
      selected.__simNode.fy = cy
    }

    // Shrink the selected artist node to a small anchor dot at the center
    // and move its nameplate to sit just above the dot
    if (svgRef.current) {
      d3.select(svgRef.current).selectAll('.artist-node')
        .filter(d => d.id === selected.id)
        .each(function(d) {
          // Pin via the live simulation node object
          d.fx = cx; d.fy = cy
          const ng = d3.select(this)
          const miniR = 5
          ng.select('.artist-halo')
            .transition().duration(350).ease(d3.easeCubicOut)
            .attr('r', miniR + 3)
          ng.select('.artist-circle')
            .transition().duration(350).ease(d3.easeCubicOut)
            .attr('r', miniR)
          ng.select('.artist-label-g')
            .transition().duration(350).ease(d3.easeCubicOut)
            .attr('transform', `translate(0, ${-miniR - 10})`)
          ng.select('.artist-label-text')
            .transition().duration(350).ease(d3.easeCubicOut)
            .attr('font-size', 9)
            .attr('fill', '#ffffff')
            .attr('font-weight', 'bold')
        })
    }

    // Dark exclusion disc — covers from center out to just inside the orbit ring
    // Gradient-ish effect: inner disc is more opaque, fades toward orbit
    tl.append('circle').attr('cx',cx).attr('cy',cy)
      .attr('r', orbitR - 4)  // just inside the orbit ring
      .attr('fill','#07090f').attr('fill-opacity',0.45)
      .attr('stroke','none').attr('pointer-events','none')
    // Slightly more opaque inner zone around the node itself
    tl.append('circle').attr('cx',cx).attr('cy',cy)
      .attr('r', nodeR + 10)
      .attr('fill','#07090f').attr('fill-opacity',0.35)
      .attr('stroke','none').attr('pointer-events','none')

    // Orbit ring
    tl.append('circle').attr('cx',cx).attr('cy',cy).attr('r',orbitR)
      .attr('fill','none').attr('stroke',selected.color||'#7c3aed')
      .attr('stroke-opacity',0.2).attr('stroke-width',1).attr('stroke-dasharray','4 3')

    tracks.forEach((track, i) => {
      const angle = (i/n)*2*Math.PI - Math.PI/2
      const tx = cx+Math.cos(angle)*orbitR, ty = cy+Math.sin(angle)*orbitR
      const fav = !!track.is_favorite, cd = !!track.cooldown_until
      const trackColor = cd ? '#374151' : fav ? '#f59e0b' : selected.color||'#7c3aed'

      tl.append('line')
        .attr('x1',cx).attr('y1',cy).attr('x2',tx).attr('y2',ty)
        .attr('stroke',selected.color||'#7c3aed').attr('stroke-opacity',0.08).attr('stroke-width',0.7)

      const tg = tl.append('g')
        .attr('transform',`translate(${tx},${ty})`)
        .attr('cursor','pointer')
        .on('click', (e) => {
          e.stopPropagation()
          setSelectedTrack(st => st?.track===track ? null : { track, x: tx, y: ty, artistColor: selected.color||'#7c3aed' })
        })
        .on('mouseover', function() {
          d3.select(this).select('circle:nth-child(2)').attr('r', 7).attr('fill-opacity', cd ? 0.4 : 1)
        })
        .on('mouseout', function() {
          d3.select(this).select('circle:nth-child(2)').attr('r', 5.5).attr('fill-opacity', cd ? 0.25 : 0.88)
        })

      tg.append('circle').attr('r',10).attr('fill',trackColor).attr('fill-opacity',0.12)  // hit area
      tg.append('circle').attr('r',5.5)
        .attr('fill', trackColor).attr('fill-opacity',cd?0.25:0.88)
        .attr('stroke','#fff').attr('stroke-opacity',0.25).attr('stroke-width',0.8)
      // Skip penalty arc around the moon
      const skipPen = track.skip_penalty || 0
      if (skipPen > 0.1) {
        const skipArc = d3.arc()
          .innerRadius(6.5).outerRadius(8)
          .startAngle(-Math.PI/2)
          .endAngle(-Math.PI/2 + Math.min(0.9,skipPen)*2*Math.PI)
        tg.append('path').attr('d',skipArc())
          .attr('fill','#ef4444')
          .attr('fill-opacity', Math.min(0.85, 0.3 + skipPen*0.7))
          .attr('pointer-events','none')
      }
      // Replay boost marker — ⚡ means this artist has an active replay boost
      // (you voluntarily returned to listen to them within the last 7 days)
      if ((track.replay_boost||0) > 0)
        tg.append('text').attr('x',6).attr('y',-5)
          .attr('font-size',7).attr('fill','#a78bfa').attr('fill-opacity',0.85)
          .attr('pointer-events','none').text('⚡')

      // Label — always above the moon (negative y = up in SVG)
      const nm = track.track_name||''
      const label = nm.length > maxChars ? nm.slice(0, maxChars-1)+'…' : nm
      const lHalfW = label.length * labelFont * 0.32 + 4
      const lH     = labelFont + 4
      const moonEdge = 6    // moon radius
      const gap      = 3    // gap between moon top and label bottom

      const labelG = tg.append('g')
        .attr('transform', `translate(0, ${-(moonEdge + gap + lH/2)})`)
        .attr('pointer-events','none')
      labelG.append('rect')
        .attr('x', -lHalfW).attr('y', -lH/2)
        .attr('width', lHalfW*2).attr('height', lH)
        .attr('rx', 2).attr('fill','#07090f').attr('fill-opacity',0.88)
        .attr('stroke','rgba(255,255,255,0.06)').attr('stroke-width',0.5)
      labelG.append('text')
        .attr('text-anchor','middle').attr('dominant-baseline','middle')
        .attr('font-size', labelFont)
        .attr('fill', cd?'#4b5563':fav?'#f59e0b':'#b0b8c8')
        .text(label)
      if (track.play_count>0) {
        // Play count sits below the moon (opposite side from the label)
        tg.append('text')
          .attr('text-anchor','middle')
          .attr('y', moonEdge + gap + labelFont * 0.85)
          .attr('font-size', Math.max(5.5, labelFont * 0.75))
          .attr('fill','#64748b')
          .attr('pointer-events','none')
          .text(`×${track.play_count}`)
      }

      if (fav)
        tg.append('text').attr('dy',-10).attr('text-anchor','middle').attr('font-size',8)
          .attr('fill','#f59e0b').attr('pointer-events','none').text('★')
    })
  }, [selected, trackCache, zoomLevel])  // zoomLevel re-gates the orbit draw

  // ── Search highlight ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return
    const q = searchQuery.toLowerCase().trim()
    d3.select(svgRef.current).selectAll('.artist-node .artist-circle')
      .attr('fill-opacity', d => !q||d.label?.toLowerCase().includes(q) ? 0.88 : 0.12)
      .attr('stroke-width', d => q&&d.label?.toLowerCase().includes(q) ? 2.5 : 1)
    if (q) {
      d3.select(svgRef.current).selectAll('.artist-node .artist-label-g')
        .attr('opacity', d => d.label?.toLowerCase().includes(q) ? 1 : 0)
    }
  }, [searchQuery, graphData])

  // ── Selection + connection highlight ────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return
    const selId     = selected?.id
    const connected = connectedRef.current
    const hasSel    = !!selId

    // Build second-degree set FIRST — used by circle strokes below
    const secondDegree = new Set()
    if (hasSel) {
      d3.select(svgRef.current).selectAll('.links line')
        .each(function(edge) {
          const s = edge.source?.id||edge.source, t = edge.target?.id||edge.target
          if (connected.has(s)) secondDegree.add(t)
          if (connected.has(t)) secondDegree.add(s)
        })
      secondDegree.delete(selId)
      connected.forEach(id => secondDegree.delete(id))
    }

    // Artist circles: stroke ring for selected/connected/2nd-degree
    d3.select(svgRef.current).selectAll('.artist-node .artist-circle')
      .attr('stroke',        d => connected.has(d.id) ? d.color : '#ffffff')
      .attr('stroke-width',  d => d.id===selId ? 3 : (connected.has(d.id) ? 2 : (secondDegree.has(d.id) ? 1.5 : 1)))
      .attr('stroke-opacity',d => d.id===selId ? 1 : (connected.has(d.id) ? 0.9 : (secondDegree.has(d.id) ? 0.4 : (hasSel ? 0.15 : 0.15))))
      .attr('fill-opacity',  0.88)

    // Labels: when selected — force show selected+connected, hide others.
    // When deselected — reset to null so the tick handler takes back control.
    d3.select(svgRef.current).selectAll('.artist-node')
      .each(function(d) {
        const isSel  = d.id === selId
        const isConn = connected.has(d.id)
        if (!hasSel) {
          d3.select(this).select('.artist-label-g').attr('opacity', null)
          d3.select(this).select('.artist-label-text')
            .attr('fill', '#d1d5db').attr('font-weight', 'normal')
          d3.select(this).select('.artist-label-bg').attr('fill-opacity', 0.82)
          return
        }
        d3.select(this).select('.artist-label-g')
          .attr('opacity', isSel || isConn ? 1 : 0)
        d3.select(this).select('.artist-label-text')
          .attr('fill',        isSel ? '#ffffff' : (isConn ? d.color : '#d1d5db'))
          .attr('font-weight', isSel || isConn ? 'bold' : 'normal')
        d3.select(this).select('.artist-label-bg')
          .attr('fill-opacity', isSel || isConn ? 0.92 : 0.72)
      })

    // Edges: gold for direct, silver for 2nd-degree, invisible for rest
    d3.select(svgRef.current).selectAll('.links line')
      .each(function(edge) {
        const srcId  = edge.source?.id || edge.source
        const tgtId  = edge.target?.id || edge.target
        const isDirect = hasSel && (srcId===selId || tgtId===selId)
        const is2nd    = !isDirect && hasSel && (
          (connected.has(srcId) && secondDegree.has(tgtId)) ||
          (connected.has(tgtId) && secondDegree.has(srcId)) ||
          (secondDegree.has(srcId) && secondDegree.has(tgtId))
        )
        const el = d3.select(this)
        if (isDirect) {
          el.attr('stroke', '#f59e0b')
            .attr('stroke-width', Math.max(2, (edge.weight||0)*4))
            .attr('stroke-opacity', Math.max(0.65, edge.weight||0.65))
            .attr('filter', 'drop-shadow(0 0 4px #f59e0b88)')
        } else if (is2nd) {
          el.attr('stroke', '#94a3b8')
            .attr('stroke-width', Math.max(0.6, (edge.weight||0)*1.4))
            .attr('stroke-opacity', Math.max(0.14, (edge.weight||0)*0.28))
            .attr('filter', null)
        } else {
          el.attr('stroke', '#fff')
            .attr('stroke-width', Math.max(0.5,(edge.weight||0)*1.8))
            .attr('stroke-opacity', hasSel ? 0.02 : null)
            .attr('filter', null)
        }
      })
  }, [selected])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── Re-apply zoom visibility when selection changes ─────────────────────
  // This ensures the artist fade-out/in triggers immediately on click,
  // not just on the next scroll event.
  useEffect(() => {
    const k = currentKRef.current
    applyZoomRef.current?.(k)
  }, [selected])

  // ── No forced zoom on selection — user controls their own view ─────────────

  // ── Escape key exits fullscreen ─────────────────────────────────────────
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen])

  // ── Resize SVG when fullscreen toggles ───────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return
    // In fullscreen the SVG fills the whole viewport
    const W = fullscreen ? window.innerWidth  : (svgRef.current.parentElement?.clientWidth  || 900)
    const H = fullscreen ? window.innerHeight : (svgRef.current.parentElement?.clientHeight || 650)
    d3.select(svgRef.current).attr('width', W).attr('height', H)
    // Re-fit if this is the first time going fullscreen and no saved transform
    if (fullscreen && !savedTransformRef.current && zoomRef.current) {
      d3.select(svgRef.current)
        .call(zoomRef.current.transform,
          d3.zoomIdentity.translate(W / 2, H / 2).scale(0.5))
    }
  }, [fullscreen])

  // ── Render ───────────────────────────────────────────────────────────────
  const mapContent = (
    <div
      className="relative"
      style={{
        width: fullscreen ? '100vw' : '100%',
        height: fullscreen ? '100vh' : '100%',
        minHeight: fullscreen ? undefined : 560,
        background: '#07090f',
        overflow: 'hidden',
      }}
    >

      <div className="flex flex-col gap-2" style={{position:'absolute',top:12,left:12,zIndex:20}}>
        <input type="text" placeholder="Search artists…"
               value={searchQuery} onChange={e=>setSearchQuery(e.target.value)}
               className="rounded-lg px-3 py-1.5 text-xs w-44 focus:outline-none"
               style={{background:'rgba(7,9,15,0.92)',border:'1px solid rgba(255,255,255,0.12)',
                       color:'var(--text-primary)',backdropFilter:'blur(8px)'}}/>
        <div className="rounded-lg px-3 py-2"
             style={{background:'rgba(7,9,15,0.92)',border:'1px solid rgba(255,255,255,0.1)',backdropFilter:'blur(8px)'}}>
          <div className="text-[10px] mb-1 text-slate-400">Min affinity: {minAffinity}</div>
          <input type="range" min={0} max={80} step={5} value={minAffinity}
                 onChange={e=>setMinAffinity(Number(e.target.value))} className="w-32 accent-[var(--accent)]"/>
        </div>
        <div className="rounded-lg px-3 py-2"
             style={{background:'rgba(7,9,15,0.92)',border:'1px solid rgba(255,255,255,0.1)',backdropFilter:'blur(8px)'}}>
          <div className="text-[10px] mb-1 text-slate-400">Artists: {nodeLimit}</div>
          <input type="range" min={20} max={200} step={10} value={nodeLimit}
                 onChange={e=>setNodeLimit(Number(e.target.value))} className="w-32 accent-[var(--accent)]"/>
        </div>
      </div>

      <div className="flex flex-col gap-1.5" style={{position:'absolute',bottom:12,left:12,zIndex:20}}>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
             style={{background:'rgba(7,9,15,0.88)',border:'1px solid rgba(255,255,255,0.08)',backdropFilter:'blur(8px)'}}>
          <div className="flex gap-1.5 items-center">
            {['galaxy','solar','star'].map(lvl=>(
              <div key={lvl} className="rounded-full transition-all duration-300"
                   style={{width:zoomLevel===lvl?7:5,height:zoomLevel===lvl?7:5,
                           background:zoomLevel===lvl?'var(--accent)':'rgba(255,255,255,0.2)'}}/>
            ))}
          </div>
          <span className="text-[10px] font-mono tracking-widest text-slate-500 uppercase">{zoomLevel}</span>
          <span className="text-[10px] text-slate-600 mx-0.5">·</span>
          <span className="text-[10px] text-slate-500">
            {zoomLevel==='galaxy'&&'Scroll in to see artists'}
            {zoomLevel==='solar' &&'Click artist to expand · scroll for tracks'}
            {zoomLevel==='star'  &&'Tracks orbit the selected artist'}
          </span>
        </div>
        <div className="px-3 py-2 rounded-lg space-y-1"
             style={{background:'rgba(7,9,15,0.88)',border:'1px solid rgba(255,255,255,0.08)',backdropFilter:'blur(8px)'}}>
          {[
            ['#f59e0b','High affinity'],
            ['#7c3aed','Mid affinity'],
            ['#4b5563','Low / stale listening'],
          ].map(([c,l])=>(
            <div key={l} className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full shrink-0" style={{background:c}}/>
              <span className="text-[10px] text-slate-500">{l}</span>
            </div>
          ))}
          <div className="flex items-center gap-2">
            <span style={{color:'#f59e0b',fontSize:10,lineHeight:1}}>★</span>
            <span className="text-[10px] text-slate-500">Has a favourite</span>
          </div>
          <div className="flex items-center gap-2">
            <span style={{color:'#4ade80',fontSize:11,lineHeight:1,fontWeight:'bold'}}>↑</span>
            <span className="text-[10px] text-slate-500">Trending on Last.fm*</span>
          </div>
          <div className="flex items-center gap-2">
            <span style={{color:'#ef4444',fontSize:9,lineHeight:1}}>▬</span>
            <span className="text-[10px] text-slate-500">High skip rate</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full shrink-0 border border-dashed" style={{borderColor:'rgba(248,250,252,0.35)'}}/>
            <span className="text-[10px] text-slate-500">Cross-genre bridge</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="h-px w-4 shrink-0" style={{background:'#94a3b8',opacity:0.6}}/>
            <span className="text-[10px] text-slate-500">2nd-degree connection</span>
          </div>
          <div className="text-[9px] text-slate-600 mt-0.5 leading-tight">
            * needs 2 enrichment runs
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full shrink-0 border" style={{borderColor:'#a78bfa'}}/>
            <span className="text-[10px] text-slate-500">Replay boost (returned within 7d)</span>
          </div>
          <div className="flex items-center gap-2">
            <span style={{color:'#a78bfa',fontSize:9,lineHeight:1}}>⚡</span>
            <span className="text-[10px] text-slate-500">Track: artist replay boost</span>
          </div>
        </div>
      </div>

      <div className="flex flex-col items-end gap-1.5" style={{position:'absolute',bottom:12,right:12,zIndex:20}}>
        {graphData?.meta && (
          <div className="text-[10px] px-2 py-1 rounded"
               style={{color:'var(--text-secondary)',background:'rgba(7,9,15,0.7)'}}>
            {graphData.meta.shown_artists} artists · {graphData.meta.total_edges} connections
          </div>
        )}
        <div className="flex gap-1.5">
          <button onClick={()=>setFullscreen(f=>!f)}
                  className="text-[10px] px-2.5 py-1 rounded transition-opacity hover:opacity-80"
                  title={fullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}
                  style={{background:'rgba(7,9,15,0.88)',color:'var(--text-secondary)',border:'1px solid rgba(255,255,255,0.08)'}}>
            {fullscreen ? '✕ Exit' : '⛶ Full'}
          </button>
          <button onClick={fetchGraph} className="text-[10px] px-2.5 py-1 rounded transition-opacity hover:opacity-80"
                  style={{background:'rgba(7,9,15,0.88)',color:'var(--text-secondary)',border:'1px solid rgba(255,255,255,0.08)'}}>
            ↺ Refresh
          </button>
        </div>
      </div>

      {detailLoading&&(
        <div className="px-3 py-1.5 rounded-lg text-xs text-slate-400"
             style={{position:'absolute',top:12,right:12,zIndex:30,background:'rgba(7,9,15,0.95)',border:'1px solid rgba(255,255,255,0.1)'}}>
          Loading…
        </div>
      )}

      {loading&&(
        <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
          <div className="flex gap-1.5 mb-3">
            {[0,1,2].map(i=>(
              <div key={i} className="w-1.5 h-1.5 rounded-full animate-bounce"
                   style={{background:'var(--accent)',animationDelay:`${i*0.15}s`}}/>
            ))}
          </div>
          <div className="text-sm text-slate-400">Mapping your music universe…</div>
        </div>
      )}

      {error&&!loading&&(
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <div className="text-center">
            <div className="text-sm text-red-400 mb-2">Could not load graph data</div>
            <div className="text-xs text-slate-500 mb-3">{error}</div>
            <button onClick={fetchGraph} className="text-xs px-3 py-1.5 rounded-lg"
                    style={{background:'var(--bg-overlay)',color:'var(--accent)'}}>Try again</button>
          </div>
        </div>
      )}

      {!loading&&!error&&graphData?.nodes?.length===0&&(
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <div className="text-center">
            <div className="text-3xl mb-3">🌌</div>
            <div className="text-sm text-slate-400 mb-1">No universe data yet</div>
            <div className="text-xs text-slate-600">Run a full index from the Dashboard to populate the map</div>
          </div>
        </div>
      )}

      <svg ref={svgRef} className="w-full h-full" style={{display:'block'}}/>

      {detail&&!detailLoading&&(
        <ArtistPanel detail={detail} onClose={()=>{ selectedRef.current=null; connectedRef.current=new Set(); highlightRef.current=false; setSelected(null); setDetail(null); setSelectedTrack(null) }}/>
      )}
      {selectedTrack&&(
        <TrackPanel selectedTrack={selectedTrack} onClose={()=>setSelectedTrack(null)}/>
      )}
    </div>
  )

  if (fullscreen) {
    return createPortal(
      <div style={{ position: 'fixed', inset: 0, zIndex: 9999, overflow: 'hidden' }}>
        {mapContent}
      </div>,
      document.body
    )
  }

  return mapContent
}
