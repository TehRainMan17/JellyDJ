/**
 * NetworkGraph — force-directed artist/genre taste map
 *
 * Nodes:
 *   Artists — sized by affinity+plays, colored by affinity heat (grey→teal→gold)
 *   Genres  — smaller neutral grey nodes
 *
 * Edges:
 *   Artist→Artist  — Last.fm similarity × user affinity weight
 *   Artist→Genre   — lighter dashed lines
 *
 * Interactions:
 *   Drag nodes to reposition
 *   Click artist node → detail panel (bio, top tracks, similar artists)
 *   Scroll / pinch → zoom
 *   Search box → highlight matching nodes
 *   Affinity slider → filter out low-affinity artists
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import * as d3 from 'd3'

const API = '/api'

function TrendBadge({ direction }) {
  if (!direction || direction === 'stable') return null
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
      direction === 'rising'
        ? 'bg-green-500/20 text-green-400'
        : 'bg-red-500/20 text-red-400'
    }`}>
      {direction === 'rising' ? '↑ Rising' : '↓ Falling'}
    </span>
  )
}

function ArtistDetailPanel({ artist, userId, onClose }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!artist || !userId) return
    setLoading(true)
    const name = encodeURIComponent(artist.label)
    fetch(`${API}/graph/artist/${name}?user_id=${userId}`)
      .then(r => r.json())
      .then(d => { setDetail(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [artist, userId])

  if (!artist) return null

  return (
    <div className="absolute top-4 right-4 w-72 card z-20 overflow-y-auto max-h-[85%] shadow-2xl">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="text-sm font-bold text-[var(--text-primary)]">{artist.label}</div>
          <div className="text-[11px] text-[var(--text-secondary)]">{artist.genre || 'Unknown genre'}</div>
        </div>
        <button
          onClick={onClose}
          className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-lg leading-none ml-2"
        >
          ×
        </button>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="bg-[var(--bg-overlay)] rounded-lg p-2">
          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Your Affinity</div>
          <div className="text-base font-bold text-[var(--accent)]">{artist.affinity?.toFixed(0)}</div>
        </div>
        <div className="bg-[var(--bg-overlay)] rounded-lg p-2">
          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Your Plays</div>
          <div className="text-base font-bold text-[var(--text-primary)]">{artist.plays?.toLocaleString()}</div>
        </div>
        {artist.popularity != null && (
          <div className="bg-[var(--bg-overlay)] rounded-lg p-2">
            <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Global Pop.</div>
            <div className="text-base font-bold text-[var(--text-primary)]">{artist.popularity?.toFixed(0)}</div>
          </div>
        )}
        {artist.replay_boost > 0 && (
          <div className="bg-[var(--bg-overlay)] rounded-lg p-2">
            <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Replay ↑</div>
            <div className="text-base font-bold text-green-400">+{artist.replay_boost}</div>
          </div>
        )}
      </div>

      {/* Trend */}
      {artist.trend && artist.trend !== 'stable' && (
        <div className="flex items-center gap-2 mb-3">
          <TrendBadge direction={artist.trend} />
          <span className="text-[11px] text-[var(--text-secondary)]">on Last.fm</span>
        </div>
      )}

      {/* Tags */}
      {artist.tags?.length > 0 && (
        <div className="mb-3">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Tags</div>
          <div className="flex flex-wrap gap-1">
            {artist.tags.map(tag => (
              <span key={tag} className="text-[10px] bg-[var(--bg-overlay)] text-[var(--text-secondary)] px-2 py-0.5 rounded-full">
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {loading && (
        <div className="text-[11px] text-[var(--text-secondary)] py-4 text-center">Loading details…</div>
      )}

      {detail && !loading && (
        <>
          {/* Bio */}
          {detail.biography && (
            <div className="mb-3">
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-1">About</div>
              <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed line-clamp-4">
                {detail.biography.replace(/<[^>]*>/g, '')}
              </p>
            </div>
          )}

          {/* Top tracks */}
          {detail.top_tracks?.length > 0 && (
            <div className="mb-3">
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                Your Top Tracks
              </div>
              <div className="space-y-1">
                {detail.top_tracks.slice(0, 5).map((t, i) => (
                  <div key={i} className="flex items-center justify-between text-[11px]">
                    <span className={`truncate max-w-[70%] ${t.cooldown_until ? 'text-orange-400/70 line-through' : 'text-[var(--text-primary)]'}`}>
                      {t.track_name}
                      {t.cooldown_until && <span className="ml-1 text-[9px] no-underline text-orange-400">cooldown</span>}
                    </span>
                    <span className="text-[var(--text-secondary)] ml-1 shrink-0">×{t.play_count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Similar artists */}
          {detail.similar_artists?.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">
                Similar Artists
              </div>
              <div className="flex flex-wrap gap-1">
                {detail.similar_artists.map(s => (
                  <span key={s.name} className="text-[10px] bg-[var(--bg-overlay)] text-[var(--accent)] px-2 py-0.5 rounded-full">
                    {s.name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {detail.lastfm_url && (
            <a
              href={detail.lastfm_url}
              target="_blank"
              rel="noopener noreferrer"
              className="block mt-3 text-center text-[10px] text-[var(--accent)] hover:underline"
            >
              View on Last.fm →
            </a>
          )}
        </>
      )}
    </div>
  )
}

export default function NetworkGraph({ userId }) {
  const svgRef = useRef(null)
  const simRef = useRef(null)

  const [graphData, setGraphData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [minAffinity, setMinAffinity] = useState(0)
  const [nodeLimit, setNodeLimit] = useState(80)

  // Fetch graph data
  const fetchGraph = useCallback(() => {
    if (!userId) return
    setLoading(true)
    setError(null)
    fetch(`${API}/graph/network?user_id=${userId}&limit=${nodeLimit}&min_affinity=${minAffinity}`)
      .then(r => r.json())
      .then(d => {
        setGraphData(d)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }, [userId, nodeLimit, minAffinity])

  useEffect(() => { fetchGraph() }, [fetchGraph])

  // Build D3 graph
  useEffect(() => {
    if (!graphData || !svgRef.current) return

    const { nodes, edges } = graphData
    if (!nodes.length) return

    const container = svgRef.current.parentElement
    const W = container.clientWidth || 800
    const H = container.clientHeight || 600

    // Clear previous
    d3.select(svgRef.current).selectAll('*').remove()

    const svg = d3.select(svgRef.current)
      .attr('width', W)
      .attr('height', H)

    // Zoom + pan
    const g = svg.append('g')
    svg.call(
      d3.zoom()
        .scaleExtent([0.2, 4])
        .on('zoom', e => g.attr('transform', e.transform))
    )

    // D3 simulation
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges)
        .id(d => d.id)
        .distance(d => d.type === 'genre' ? 120 : 60 + (1 - (d.weight || 0)) * 80)
        .strength(d => d.type === 'genre' ? 0.3 : (d.weight || 0.3) * 0.7)
      )
      .force('charge', d3.forceManyBody()
        .strength(d => d.type === 'genre' ? -80 : -120)
      )
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collision', d3.forceCollide(d => (d.size || 10) + 4))

    simRef.current = sim

    // Defs: arrowhead marker
    svg.append('defs').append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 18)
      .attr('refY', 0)
      .attr('markerWidth', 4)
      .attr('markerHeight', 4)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#4b5563')

    // Edges
    const link = g.append('g').attr('class', 'links')
      .selectAll('line')
      .data(edges)
      .join('line')
      .attr('stroke', d => d.type === 'genre' ? '#374151' : '#4b5563')
      .attr('stroke-opacity', d => d.type === 'genre' ? 0.3 : Math.max(0.15, d.weight * 0.8))
      .attr('stroke-width', d => d.type === 'genre' ? 0.5 : Math.max(0.5, d.weight * 3))
      .attr('stroke-dasharray', d => d.type === 'genre' ? '3,3' : null)

    // Node groups
    const node = g.append('g').attr('class', 'nodes')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .attr('class', 'node')
      .style('cursor', d => d.type === 'artist' ? 'pointer' : 'default')
      .call(
        d3.drag()
          .on('start', (e, d) => {
            if (!e.active) sim.alphaTarget(0.3).restart()
            d.fx = d.x; d.fy = d.y
          })
          .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y })
          .on('end', (e, d) => {
            if (!e.active) sim.alphaTarget(0)
            d.fx = null; d.fy = null
          })
      )
      .on('click', (e, d) => {
        if (d.type === 'artist') {
          setSelectedNode(prev => prev?.id === d.id ? null : d)
        }
      })

    // Node circles
    node.append('circle')
      .attr('r', d => d.size || 10)
      .attr('fill', d => d.color || '#6b7280')
      .attr('fill-opacity', 0.85)
      .attr('stroke', d => d.type === 'artist' ? '#ffffff22' : 'none')
      .attr('stroke-width', 1)

    // Favorite star
    node.filter(d => d.has_favorite)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .attr('font-size', d => Math.max(8, (d.size || 10) * 0.6))
      .attr('fill', '#fbbf24')
      .attr('pointer-events', 'none')
      .text('★')

    // Labels
    node.filter(d => (d.size || 10) > 12)
      .append('text')
      .attr('dy', d => (d.size || 10) + 12)
      .attr('text-anchor', 'middle')
      .attr('font-size', d => d.type === 'genre' ? 9 : Math.min(13, Math.max(9, (d.size || 10) * 0.55)))
      .attr('fill', d => d.type === 'genre' ? '#6b7280' : '#e5e7eb')
      .attr('pointer-events', 'none')
      .text(d => d.label)

    // Tick
    sim.on('tick', () => {
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y)

      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })

    return () => { sim.stop() }
  }, [graphData])

  // Search highlight effect
  useEffect(() => {
    if (!svgRef.current) return
    const q = searchQuery.toLowerCase().trim()
    d3.select(svgRef.current).selectAll('.node circle')
      .attr('stroke', d => {
        if (!q) return d.type === 'artist' ? '#ffffff22' : 'none'
        return d.label.toLowerCase().includes(q) ? '#ffffff' : (d.type === 'artist' ? '#ffffff11' : 'none')
      })
      .attr('stroke-width', d => {
        if (!q) return 1
        return d.label.toLowerCase().includes(q) ? 2.5 : 1
      })
      .attr('fill-opacity', d => {
        if (!q) return 0.85
        return d.label.toLowerCase().includes(q) ? 1.0 : 0.3
      })
  }, [searchQuery, graphData])

  return (
    <div className="relative w-full h-full" style={{ minHeight: 500 }}>
      {/* Controls */}
      <div className="absolute top-3 left-3 z-10 flex flex-col gap-2">
        <input
          type="text"
          placeholder="Search artists…"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          className="bg-[var(--bg-card)] border border-[var(--bg-overlay)] text-[var(--text-primary)]
                     rounded-lg px-3 py-1.5 text-xs w-48 focus:outline-none focus:border-[var(--accent)]"
        />
        <div className="bg-[var(--bg-card)] border border-[var(--bg-overlay)] rounded-lg px-3 py-2">
          <div className="text-[10px] text-[var(--text-secondary)] mb-1">Min affinity: {minAffinity}</div>
          <input
            type="range"
            min={0} max={80} step={5}
            value={minAffinity}
            onChange={e => setMinAffinity(Number(e.target.value))}
            className="w-32 accent-[var(--accent)]"
          />
        </div>
        <div className="bg-[var(--bg-card)] border border-[var(--bg-overlay)] rounded-lg px-3 py-2">
          <div className="text-[10px] text-[var(--text-secondary)] mb-1">Artists shown: {nodeLimit}</div>
          <input
            type="range"
            min={20} max={200} step={10}
            value={nodeLimit}
            onChange={e => setNodeLimit(Number(e.target.value))}
            className="w-32 accent-[var(--accent)]"
          />
        </div>
      </div>

      {/* Legend */}
      <div className="absolute bottom-3 left-3 z-10 bg-[var(--bg-card)] border border-[var(--bg-overlay)]
                      rounded-lg px-3 py-2 text-[10px] text-[var(--text-secondary)] space-y-1">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-[#ffcc33]" />
          <span>High affinity</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-[#00b4d8]" />
          <span>Medium affinity</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-[#6b7280]" />
          <span>Genre node</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[#fbbf24]">★</span>
          <span>Has a favorite</span>
        </div>
      </div>

      {/* Meta */}
      {graphData?.meta && (
        <div className="absolute bottom-3 right-3 z-10 text-[10px] text-[var(--text-secondary)]">
          {graphData.meta.shown_artists} artists · {graphData.meta.total_edges} connections
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-sm text-[var(--text-secondary)]">Building your music graph…</div>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-sm text-red-400">Graph error: {error}</div>
        </div>
      )}

      {!loading && graphData?.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <div className="text-sm text-[var(--text-secondary)] mb-1">No graph data yet</div>
            <div className="text-xs text-[var(--text-secondary)]">
              Run a full index + enrichment pass to populate the network
            </div>
          </div>
        </div>
      )}

      <svg ref={svgRef} className="w-full h-full" />

      {selectedNode && (
        <ArtistDetailPanel
          artist={selectedNode}
          userId={userId}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  )
}
