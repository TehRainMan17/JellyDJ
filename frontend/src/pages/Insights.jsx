
import { useState, useEffect } from 'react'
import {
  BarChart2, Music2, Mic2, Tag, TrendingUp, TrendingDown,
  ChevronUp, ChevronDown, ChevronsUpDown, Star, Loader2,
  AlertCircle, Play, SkipForward, Heart
} from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

// Display timestamps in the browser's local timezone (UTC stored, local displayed)
function fmtDate(iso) {
  if (!iso) return '—'
  const d = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'
  return new Date(d).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
function fmtDateShort(iso) {
  if (!iso) return '—'
  const d2 = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'
  return new Date(d2).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function ScoreBar({ value, max = 100, color = 'var(--accent)' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className="relative h-1.5 bg-[var(--bg-overlay)] rounded-full overflow-hidden w-full">
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-all duration-300"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  )
}

function StatPill({ label, value, color = 'text-[var(--text-primary)]' }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">{label}</span>
      <span className={`text-xs font-semibold mt-0.5 ${color}`}>{value}</span>
    </div>
  )
}

function SortHeader({ label, field, currentSort, currentOrder, onSort }) {
  const active = currentSort === field
  return (
    <button
      onClick={() => onSort(field)}
      className={`flex items-center gap-1 text-[10px] uppercase tracking-wider font-semibold
                  hover:text-[var(--text-primary)] transition-colors whitespace-nowrap
                  ${active ? 'text-[var(--accent)]' : 'text-[var(--text-secondary)]'}`}
    >
      {label}
      {active
        ? currentOrder === 'desc' ? <ChevronDown size={11} /> : <ChevronUp size={11} />
        : <ChevronsUpDown size={11} className="opacity-40" />}
    </button>
  )
}

// ── Summary Cards ─────────────────────────────────────────────────────────────

function SummarySection({ summary }) {
  if (!summary) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
      {/* Totals */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Library</div>
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Total tracks</span>
            <span className="text-sm font-bold text-[var(--text-primary)]">{summary.total_tracks_in_library.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Played</span>
            <span className="text-sm font-semibold text-[var(--accent)]">{summary.played_tracks.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Unplayed</span>
            <span className="text-sm font-semibold text-[var(--text-secondary)]">{summary.unplayed_tracks.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Artists tracked</span>
            <span className="text-sm font-semibold text-[var(--text-primary)]">{summary.total_artists}</span>
          </div>
          {summary.skip_tracking && (
            <div className="flex justify-between items-center pt-2 border-t border-[var(--bg-overlay)]">
              <span className="text-xs text-[var(--text-secondary)]">Skips recorded</span>
              <span className="text-sm font-semibold text-[var(--danger)]">
                {summary.skip_tracking.total_skips_recorded.toLocaleString()}
                {summary.skip_tracking.tracks_with_events > 0 && (
                  <span className="text-[10px] text-[var(--text-secondary)] ml-1">
                    ({summary.skip_tracking.tracks_with_events} tracked)
                  </span>
                )}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Top track */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Highlights</div>
        {summary.top_track && (
          <div className="mb-3 pb-3 border-b border-[var(--bg-overlay)]">
            <div className="flex items-center gap-1.5 mb-1">
              <TrendingUp size={11} className="text-[var(--accent)]" />
              <span className="text-[10px] text-[var(--accent)] uppercase tracking-wider">Highest scored</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.top_track.track_name}</div>
            <div className="text-xs text-[var(--text-secondary)] truncate">{summary.top_track.artist_name}</div>
            <div className="text-xs text-[var(--accent)] mt-1">{summary.top_track.final_score.toFixed(1)} / 100</div>
          </div>
        )}
        {summary.most_played_track && (
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <Play size={11} className="text-[#7d8590]" />
              <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Most played</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.most_played_track.track_name}</div>
            <div className="text-xs text-[var(--text-secondary)] truncate">{summary.most_played_track.artist_name}</div>
            <div className="text-xs text-[var(--text-secondary)] mt-1">{summary.most_played_track.play_count} plays</div>
          </div>
        )}
      </div>

      {/* Top genres + skipped */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Top Genres</div>
        <div className="space-y-2 mb-3">
          {summary.top_genres.slice(0, 4).map(g => (
            <div key={g.genre}>
              <div className="flex justify-between items-center mb-0.5">
                <span className="text-xs text-[var(--text-primary)] truncate">{g.genre || 'Unknown'}</span>
                <span className="text-[10px] text-[var(--text-secondary)] ml-2 flex-shrink-0">{g.affinity_score.toFixed(0)}</span>
              </div>
              <ScoreBar value={g.affinity_score} />
            </div>
          ))}
        </div>
        {summary.most_skipped_artist && (
          <div className="pt-3 border-t border-[var(--bg-overlay)]">
            <div className="flex items-center gap-1.5 mb-1">
              <SkipForward size={11} className="text-[var(--danger)]" />
              <span className="text-[10px] text-[var(--danger)] uppercase tracking-wider">Most skipped artist</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.most_skipped_artist.artist_name}</div>
            <div className="text-xs text-[var(--text-secondary)]">{(summary.most_skipped_artist.skip_rate * 100).toFixed(0)}% skip rate · {summary.most_skipped_artist.total_skips} skips</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Track Table ───────────────────────────────────────────────────────────────

function TrackTable({ userId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState('final_score')
  const [order, setOrder] = useState('desc')
  const [playedFilter, setPlayedFilter] = useState('all')
  const [artistFilter, setArtistFilter] = useState('')
  const [artistInput, setArtistInput] = useState('')
  const [page, setPage] = useState(1)
  const [expandedRow, setExpandedRow] = useState(null)

  const doFetch = (uid, sb, ord, pf, af, pg) => {
    if (!uid) return
    setLoading(true)
    const params = new URLSearchParams({
      user_id: uid, sort_by: sb, order: ord,
      played_filter: pf, page: pg,
      page_size: 50,
      ...(af ? { artist_filter: af } : {}),
    })
    fetch(`/api/insights/tracks?${params}`)
      .then(r => r.json()).then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    doFetch(userId, sort, order, playedFilter, artistFilter, page)
  }, [userId, sort, order, playedFilter, artistFilter, page])

  const handleSort = (field) => {
    const newOrder = sort === field ? (order === 'desc' ? 'asc' : 'desc') : 'desc'
    const newSort = field
    setSort(newSort)
    setOrder(newOrder)
    setPage(1)
  }

  const handleArtistSearch = (e) => {
    e.preventDefault()
    setArtistFilter(artistInput)
    setPage(1)
  }

  const scoreColor = (s) => s >= 75 ? 'var(--accent)' : s >= 55 ? 'var(--text-primary)' : 'var(--text-secondary)'

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap gap-2 mb-4 items-center">
        <div className="flex bg-[var(--bg-overlay)] rounded-lg border border-[var(--border)] overflow-hidden text-xs">
          {['all','played','unplayed'].map(f => (
            <button key={f}
              onClick={() => { setPlayedFilter(f); setPage(1) }}
              className={`px-3 py-1.5 font-medium transition-colors capitalize
                ${playedFilter === f ? 'bg-[var(--accent)] text-[var(--bg)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
            >{f}</button>
          ))}
        </div>
        <form onSubmit={handleArtistSearch} className="flex gap-1">
          <input
            value={artistInput}
            onChange={e => setArtistInput(e.target.value)}
            placeholder="Filter by artist…"
            className="bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg px-3 py-1.5
                       text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] outline-none
                       focus:border-[var(--accent)] transition-colors w-40"
          />
          <button type="submit" className="px-2 py-1.5 bg-[var(--bg-overlay)] border border-[var(--border)]
                                           rounded-lg text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
            Go
          </button>
          {artistFilter && (
            <button type="button" onClick={() => { setArtistFilter(''); setArtistInput(''); setPage(1) }}
              className="px-2 py-1.5 text-xs text-[var(--danger)] hover:text-[#ff7b72] transition-colors">
              ✕
            </button>
          )}
        </form>
        {data && (
          <span className="text-xs text-[var(--text-secondary)] ml-auto">
            {data.total.toLocaleString()} tracks · page {data.page}/{data.pages}
          </span>
        )}
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--bg-overlay)]">
                <th className="px-4 py-2.5 w-8 text-[10px] text-[var(--text-secondary)]">#</th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Track" field="track_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden sm:table-cell">
                  <SortHeader label="Artist" field="artist_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Score" field="final_score" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden md:table-cell">
                  <SortHeader label="Plays" field="play_count" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden lg:table-cell">
                  <SortHeader label="Skip pen." field="skip_penalty" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden md:table-cell">
                  <SortHeader label="Skips" field="skip_count" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden lg:table-cell">
                  <SortHeader label="Artist aff." field="artist_affinity" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={8} className="px-4 py-8 text-center">
                  <Loader2 size={16} className="animate-spin text-[var(--text-secondary)] mx-auto" />
                </td></tr>
              )}
              {!loading && data?.tracks?.map((t, i) => (
                <>
                  <tr
                    key={t.jellyfin_item_id}
                    onClick={() => setExpandedRow(expandedRow === t.jellyfin_item_id ? null : t.jellyfin_item_id)}
                    className="border-b border-[var(--bg-overlay)] hover:bg-[var(--bg-surface)] cursor-pointer transition-colors group"
                  >
                    <td className="px-4 py-2.5 text-[10px] text-[var(--text-secondary)]">
                      {(page - 1) * 50 + i + 1}
                    </td>
                    <td className="px-2 py-2.5 max-w-[180px]">
                      <div className="flex items-center gap-1.5">
                        {t.is_favorite && <Heart size={10} className="text-[var(--danger)] flex-shrink-0" />}
                        {!t.is_played && <div className="w-1.5 h-1.5 rounded-full bg-[#388bfd] flex-shrink-0" title="Unplayed" />}
                        <span className="text-xs text-[var(--text-primary)] truncate">{t.track_name}</span>
                      </div>
                    </td>
                    <td className="px-2 py-2.5 hidden sm:table-cell max-w-[140px]">
                      <span className="text-xs text-[var(--text-secondary)] truncate block">{t.artist_name}</span>
                    </td>
                    <td className="px-2 py-2.5">
                      <div className="flex items-center gap-2 min-w-[70px]">
                        <span className="text-xs font-bold tabular-nums" style={{ color: scoreColor(t.final_score) }}>
                          {t.final_score.toFixed(1)}
                        </span>
                        <ScoreBar value={t.final_score} color={scoreColor(t.final_score)} />
                      </div>
                    </td>
                    <td className="px-2 py-2.5 hidden md:table-cell text-xs text-[var(--text-secondary)] tabular-nums">
                      {t.play_count || '—'}
                    </td>
                    <td className="px-2 py-2.5 hidden lg:table-cell text-xs tabular-nums"
                        style={{ color: t.skip_penalty > 0.3 ? 'var(--danger)' : t.skip_rate > 0.3 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                      {t.skip_penalty > 0
                        ? `${(t.skip_penalty * 100).toFixed(0)}%`
                        : t.skip_rate > 0 ? `${(t.skip_rate * 100).toFixed(0)}%*` : '—'}
                    </td>
                    <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                        title={t.total_events > 0 ? `${t.skip_count} skips out of ${t.total_events} plays tracked` : 'No webhook events yet'}>
                      {t.skip_count > 0
                        ? <span style={{ color: 'var(--danger)' }}>
                            {t.skip_count}<span className="text-[var(--text-secondary)] text-[10px]">/{t.total_events}</span>
                          </span>
                        : <span className="text-[var(--text-secondary)]">—</span>}
                    </td>
                    <td className="px-2 py-2.5 hidden lg:table-cell text-xs text-[var(--text-secondary)] tabular-nums">
                      {t.artist_affinity.toFixed(1)}
                    </td>
                  </tr>
                  {expandedRow === t.jellyfin_item_id && (
                    <tr key={`${t.jellyfin_item_id}-exp`} className="bg-[var(--bg-surface)] border-b border-[var(--bg-overlay)]">
                      <td colSpan={8} className="px-4 py-3">
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                          <StatPill label="Album" value={t.album_name || '—'} />
                          <StatPill label="Genre" value={t.genre || '—'} />
                          <StatPill label="Play score" value={t.play_score.toFixed(1)} />
                          <StatPill label="Recency score" value={t.recency_score.toFixed(1)} />
                          <StatPill label="Artist affinity" value={t.artist_affinity.toFixed(1)} />
                          <StatPill label="Genre affinity" value={t.genre_affinity.toFixed(1)} />
                          <StatPill label="Skip penalty" value={t.skip_penalty > 0 ? `${(t.skip_penalty*100).toFixed(0)}%` : 'None'}
                            color={t.skip_penalty > 0.3 ? 'text-[var(--danger)]' : 'text-[var(--text-primary)]'} />
                          <StatPill label="Last played" value={fmtDateShort(t.last_played) || 'Never'} />
                          <StatPill label="Skips / plays"
                            value={t.total_events > 0 ? `${t.skip_count} skips / ${t.total_events} events` : 'No data yet'}
                            color={t.skip_count > 0 ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'} />
                          <StatPill label="Live skip rate"
                            value={t.total_events > 0 ? `${(t.skip_rate * 100).toFixed(0)}%` : '—'}
                            color={t.skip_rate > 0.4 ? 'text-[var(--danger)]' : t.skip_rate > 0.2 ? 'text-[#d29922]' : 'text-[var(--text-secondary)]'} />
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {!loading && !data?.tracks?.length && (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-sm text-[var(--text-secondary)]">
                  No tracks found. Run a full scan first.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-center gap-2 p-3 border-t border-[var(--bg-overlay)]">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              ← Prev
            </button>
            <span className="text-xs text-[var(--text-secondary)]">{page} / {data.pages}</span>
            <button onClick={() => setPage(p => Math.min(data.pages, p + 1))} disabled={page === data.pages}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Artist Table ──────────────────────────────────────────────────────────────

function ArtistTable({ userId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState('affinity_score')
  const [order, setOrder] = useState('desc')
  const [page, setPage] = useState(1)

  const doFetch = (uid, sb, ord, pg) => {
    if (!uid) return
    setLoading(true)
    const params = new URLSearchParams({ user_id: uid, sort_by: sb, order: ord, page: pg, page_size: 50 })
    fetch(`/api/insights/artists?${params}`)
      .then(r => r.json()).then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    doFetch(userId, sort, order, page)
  }, [userId, sort, order, page])

  const handleSort = (field) => {
    const newOrder = sort === field ? (order === 'desc' ? 'asc' : 'desc') : 'desc'
    setSort(field)
    setOrder(newOrder)
    setPage(1)
  }

  return (
    <div>
      <div className="flex justify-end mb-4">
        {data && <span className="text-xs text-[var(--text-secondary)]">{data.total} artists tracked</span>}
      </div>
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--bg-overlay)]">
                <th className="px-4 py-2.5 w-8 text-[10px] text-[var(--text-secondary)]">#</th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Artist" field="artist_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Affinity" field="affinity_score" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden sm:table-cell">
                  <SortHeader label="Total plays" field="total_plays" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden md:table-cell">
                  <SortHeader label="Skip rate" field="skip_rate" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5 hidden md:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">
                  Skips
                </th>
                <th className="px-2 py-2.5 hidden lg:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Genre</th>
                <th className="px-2 py-2.5 hidden lg:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Fav</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={7} className="px-4 py-8 text-center">
                  <Loader2 size={16} className="animate-spin text-[var(--text-secondary)] mx-auto" />
                </td></tr>
              )}
              {!loading && data?.artists?.map((a, i) => (
                <tr key={a.artist_name} className="border-b border-[var(--bg-overlay)] hover:bg-[var(--bg-surface)] transition-colors">
                  <td className="px-4 py-2.5 text-[10px] text-[var(--text-secondary)]">{(page - 1) * 50 + i + 1}</td>
                  <td className="px-2 py-2.5">
                    <span className="text-xs font-medium text-[var(--text-primary)]">{a.artist_name}</span>
                  </td>
                  <td className="px-2 py-2.5">
                    <div className="flex items-center gap-2 min-w-[80px]">
                      <span className="text-xs font-bold tabular-nums"
                            style={{ color: a.affinity_score >= 60 ? 'var(--accent)' : a.affinity_score >= 30 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                        {a.affinity_score.toFixed(1)}
                      </span>
                      <ScoreBar value={a.affinity_score} color={a.affinity_score >= 60 ? 'var(--accent)' : 'var(--text-secondary)'} />
                    </div>
                  </td>
                  <td className="px-2 py-2.5 hidden sm:table-cell text-xs text-[var(--text-secondary)] tabular-nums">{a.total_plays}</td>
                  <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                      style={{ color: a.skip_rate > 0.3 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                    {a.skip_rate > 0 ? `${(a.skip_rate * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                      style={{ color: a.total_skips > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                    {a.total_skips > 0
                      ? <span title={`${a.total_skips} skips / ${a.total_events} events`}>
                          {a.total_skips}<span className="text-[var(--text-secondary)] text-[10px]">/{a.total_events}</span>
                        </span>
                      : '—'}
                  </td>
                  <td className="px-2 py-2.5 hidden lg:table-cell text-xs text-[var(--text-secondary)] truncate max-w-[120px]">
                    {a.primary_genre || '—'}
                  </td>
                  <td className="px-2 py-2.5 hidden lg:table-cell text-xs">
                    {a.has_favorite ? <Heart size={11} className="text-[var(--danger)]" /> : <span className="text-[var(--border)]">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.pages > 1 && (
          <div className="flex items-center justify-center gap-2 p-3 border-t border-[var(--bg-overlay)]">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              ← Prev
            </button>
            <span className="text-xs text-[var(--text-secondary)]">{page} / {data.pages}</span>
            <button onClick={() => setPage(p => Math.min(data.pages, p + 1))} disabled={page === data.pages}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Insights() {
  const [users, setUsers] = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [summary, setSummary] = useState(null)
  const [tab, setTab] = useState('tracks')

  useEffect(() => {
    fetch('/api/insights/users')
      .then(r => r.json())
      .then(data => {
        setUsers(data)
        if (data.length > 0) setSelectedUser(data[0].jellyfin_user_id)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!selectedUser) return
    setSummary(null)
    fetch(`/api/insights/summary?user_id=${selectedUser}`)
      .then(r => r.json()).then(setSummary).catch(() => {})
  }, [selectedUser])

  const selectedUsername = users.find(u => u.jellyfin_user_id === selectedUser)?.username || ''

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>Insights</h1>
          <p className="text-sm text-[var(--text-secondary)] mt-1">Audit what JellyDJ knows about your taste</p>
        </div>

        {/* User picker */}
        {users.length > 1 && (
          <div className="flex bg-[var(--bg-overlay)] rounded-lg border border-[var(--border)] overflow-hidden">
            {users.map(u => (
              <button key={u.jellyfin_user_id}
                onClick={() => setSelectedUser(u.jellyfin_user_id)}
                className={`px-4 py-2 text-xs font-medium transition-colors
                  ${selectedUser === u.jellyfin_user_id
                    ? 'bg-[var(--accent)] text-[var(--bg)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
                {u.username}
              </button>
            ))}
          </div>
        )}
      </div>

      {!selectedUser ? (
        <div className="card flex flex-col items-center justify-center py-12 text-center gap-2">
          <AlertCircle size={24} className="text-[var(--border)]" />
          <div className="text-sm text-[var(--text-secondary)]">No indexed users found</div>
          <div className="text-xs text-[var(--text-secondary)]/60">Run a full scan from the Dashboard first</div>
        </div>
      ) : (
        <>
          <SummarySection summary={summary} />

          {/* Tab switcher */}
          <div className="flex gap-1 bg-[var(--bg-surface)] rounded-lg p-1 w-fit border border-[var(--bg-overlay)]">
            {[
              { key: 'tracks', label: 'Tracks', icon: Music2 },
              { key: 'artists', label: 'Artists', icon: Mic2 },
            ].map(({ key, label, icon: Icon }) => (
              <button key={key} onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all
                  ${tab === key
                    ? 'bg-[var(--bg-overlay)] text-[var(--text-primary)] shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
                <Icon size={12} />
                {label}
              </button>
            ))}
          </div>

          {tab === 'tracks' && <TrackTable userId={selectedUser} key={selectedUser} />}
          {tab === 'artists' && <ArtistTable userId={selectedUser} key={selectedUser} />}
        </>
      )}
    </div>
  )
}
