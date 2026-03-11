
import { useState, useEffect, useCallback } from 'react'
import {
  Activity, Music2, Telescope, CheckCircle2, XCircle, Loader2,
  RefreshCw, Clock, Database, SkipForward, Heart, Disc3, Download,
  Users, TrendingUp, TrendingDown, Radio, Flame, Library,
  ExternalLink, ChevronRight, AlertCircle, ArrowUp, ArrowDown, Minus,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { useJobStatus } from '../hooks/useJobStatus.js'
import JobProgress from '../components/JobProgress.jsx'
import { api } from '../lib/api.js'
import { useAuth } from '../contexts/AuthContext.jsx'

// ── Helpers ───────────────────────────────────────────────────────────────────
const utc = s => { if (!s) return s; const bare = s.replace(/([+-]\d{2}:\d{2}|Z)$/, ''); return bare + 'Z' }
function timeAgo(dateStr) {
  if (!dateStr) return ''
  const diff = (Date.now() - new Date(utc(dateStr))) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`
  return `${Math.floor(diff/86400)}d ago`
}

const EVENT_META = {
  index_complete:      { icon: Database,     color: '#00d4aa', label: 'Index complete' },
  index_error:         { icon: XCircle,      color: '#f87171', label: 'Index error' },
  playlist_generated:  { icon: Music2,       color: '#f78166', label: 'Playlists regenerated' },
  discovery_refreshed: { icon: Telescope,    color: '#60a5fa', label: 'Discovery refreshed' },
  track_approved:      { icon: CheckCircle2, color: '#00d4aa', label: 'Approved' },
  track_rejected:      { icon: XCircle,      color: '#8899b5', label: 'Rejected' },
  track_snoozed:       { icon: Clock,        color: '#8899b5', label: 'Snoozed' },
  skip_recorded:       { icon: SkipForward,  color: '#fbbf24', label: 'Skip recorded' },
  auto_download:       { icon: Download,     color: '#d29922', label: 'Auto-downloaded' },
}

// ── Billboard download modal ──────────────────────────────────────────────────
function BillboardDownloadModal({ entry, onClose, onSuccess }) {
  const [status, setStatus] = useState('idle') // idle | loading | ok | error
  const [message, setMessage] = useState('')

  const handleDownload = async () => {
    setStatus('loading')
    try {
      const d = await api.post('/api/indexer/billboard/download', {
          artist: entry.artist,
          title: entry.title,
          album_name: '',
        })
      if (d.ok) {
        setStatus('ok')
        setMessage(d.message || 'Sent to Lidarr!')
        onSuccess?.()
      } else {
        setStatus('error')
        setMessage(d.detail || d.message || 'Failed to send to Lidarr')
      }
    } catch (e) {
      setStatus('error')
      setMessage('Network error — is Lidarr configured?')
    }
  }

  return (
    // Backdrop
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(6px)' }}
      onPointerDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-sm rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', boxShadow: '0 32px 80px rgba(0,0,0,0.6)' }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {/* Album art header */}
        <div className="relative h-40 overflow-hidden">
          {entry.image_url ? (
            <img
              src={entry.image_url}
              alt=""
              className="w-full h-full object-cover"
              style={{ filter: 'brightness(0.55)' }}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center"
                 style={{ background: 'linear-gradient(135deg, var(--bg-overlay) 0%, var(--bg-card) 100%)' }}>
              <Music2 size={40} style={{ color: 'var(--text-muted)', opacity: 0.4 }} />
            </div>
          )}
          {/* Rank badge */}
          <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1 rounded-full"
               style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.1)' }}>
            <Flame size={11} style={{ color: '#f97316' }} />
            <span className="text-xs font-bold" style={{ color: 'white' }}>#{entry.rank}</span>
          </div>
          {/* In library badge */}
          {entry.in_library && (
            <div className="absolute top-3 right-3 flex items-center gap-1.5 px-2.5 py-1 rounded-full"
                 style={{ background: 'rgba(0,212,170,0.2)', backdropFilter: 'blur(8px)', border: '1px solid rgba(0,212,170,0.3)' }}>
              <Library size={11} style={{ color: '#00d4aa' }} />
              <span className="text-xs font-medium" style={{ color: '#00d4aa' }}>In library</span>
            </div>
          )}
          {/* Text overlay */}
          <div className="absolute bottom-0 left-0 right-0 p-4"
               style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 100%)' }}>
            <div className="text-base font-bold leading-tight" style={{ color: 'white' }}>{entry.title}</div>
            <div className="text-sm mt-0.5" style={{ color: 'rgba(255,255,255,0.7)' }}>{entry.artist}</div>
          </div>
        </div>

        {/* Body */}
        <div className="p-4 space-y-3">
          {/* Chart stats */}
          <div className="flex gap-3">
            {entry.weeks_on_chart && (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'var(--bg-overlay)' }}>
                <div className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{entry.weeks_on_chart}</div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Weeks on chart</div>
              </div>
            )}
            {entry.peak_position && (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'var(--bg-overlay)' }}>
                <div className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>#{entry.peak_position}</div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Peak</div>
              </div>
            )}
            {/* Trend tile */}
            {entry.position_change === null || entry.position_change === undefined ? (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'rgba(96,165,250,0.1)' }}>
                <div className="text-lg font-bold" style={{ color: '#60a5fa' }}>NEW</div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>This week</div>
              </div>
            ) : entry.position_change > 0 ? (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'rgba(0,212,170,0.08)' }}>
                <div className="flex items-center justify-center gap-1">
                  <ArrowUp size={14} style={{ color: '#00d4aa' }} />
                  <span className="text-lg font-bold" style={{ color: '#00d4aa' }}>{entry.position_change}</span>
                </div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Up from #{entry.last_week_position}</div>
              </div>
            ) : entry.position_change < 0 ? (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'rgba(248,113,113,0.08)' }}>
                <div className="flex items-center justify-center gap-1">
                  <ArrowDown size={14} style={{ color: '#f87171' }} />
                  <span className="text-lg font-bold" style={{ color: '#f87171' }}>{Math.abs(entry.position_change)}</span>
                </div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Down from #{entry.last_week_position}</div>
              </div>
            ) : (
              <div className="flex-1 rounded-xl px-3 py-2 text-center"
                   style={{ background: 'var(--bg-overlay)' }}>
                <div className="text-lg font-bold" style={{ color: 'var(--text-muted)' }}>—</div>
                <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Holding</div>
              </div>
            )}
          </div>

          {entry.in_library ? (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl"
                 style={{ background: 'rgba(0,212,170,0.08)', border: '1px solid rgba(0,212,170,0.2)' }}>
              <CheckCircle2 size={14} style={{ color: '#00d4aa', flexShrink: 0 }} />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                This track is already in your Jellyfin library.
              </span>
            </div>
          ) : (
            <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl"
                 style={{ background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.15)' }}>
              <AlertCircle size={14} style={{ color: '#fbbf24', flexShrink: 0, marginTop: 1 }} />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                Not in your library yet. Lidarr will find and download the album.
              </span>
            </div>
          )}

          {/* Status message */}
          {status === 'ok' && (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl"
                 style={{ background: 'rgba(0,212,170,0.08)', border: '1px solid rgba(0,212,170,0.2)' }}>
              <CheckCircle2 size={14} style={{ color: '#00d4aa' }} />
              <span className="text-xs" style={{ color: '#00d4aa' }}>{message}</span>
            </div>
          )}
          {status === 'error' && (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl"
                 style={{ background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)' }}>
              <XCircle size={14} style={{ color: '#f87171' }} />
              <span className="text-xs" style={{ color: '#f87171' }}>{message}</span>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-1">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-xl text-sm font-medium transition-all"
              style={{ background: 'var(--bg-overlay)', color: 'var(--text-secondary)', border: '1px solid var(--border)', touchAction: 'manipulation', WebkitTapHighlightColor: 'transparent' }}
            >
              {status === 'ok' ? 'Close' : 'Cancel'}
            </button>
            {status !== 'ok' && (
              <button
                onClick={handleDownload}
                disabled={status === 'loading'}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all btn-primary"
                style={{ opacity: status === 'loading' ? 0.7 : 1, touchAction: 'manipulation', WebkitTapHighlightColor: 'transparent' }}
              >
                {status === 'loading'
                  ? <><Loader2 size={13} className="animate-spin" />Sending…</>
                  : <><Download size={13} />Send to Lidarr</>
                }
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Billboard card ────────────────────────────────────────────────────────────
function BillboardCard({ entry, rank, onClick }) {
  const [imgError, setImgError] = useState(false)
  const inLibrary = entry.in_library

  return (
    <button
      onClick={inLibrary ? undefined : onClick}
      className={`group relative flex flex-col rounded-xl overflow-hidden text-left transition-all duration-200 anim-fade-up ${inLibrary ? '' : 'cursor-pointer'}`}
      style={{
        animationDelay: `${rank * 60}ms`,
        background: 'var(--bg-card)',
        border: `1px solid ${inLibrary ? 'rgba(83,236,252,0.2)' : 'var(--border)'}`,
        cursor: inLibrary ? 'default' : 'pointer',
        touchAction: 'manipulation',
        WebkitTapHighlightColor: 'transparent',
      }}
      onMouseEnter={e => {
        if (inLibrary) return
        e.currentTarget.style.transform = 'translateY(-2px)'
        e.currentTarget.style.borderColor = 'var(--accent)'
        e.currentTarget.style.boxShadow = '0 8px 32px rgba(83,236,252,0.12)'
      }}
      onMouseLeave={e => {
        if (inLibrary) return
        e.currentTarget.style.transform = ''
        e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.boxShadow = ''
      }}
    >
      {/* Album art */}
      <div className="relative w-full aspect-square overflow-hidden"
           style={{ background: 'var(--bg-overlay)' }}>
        {entry.image_url && !imgError ? (
          <img
            src={entry.image_url}
            alt=""
            className="w-full h-full object-cover transition-transform duration-300"
            onError={() => setImgError(true)}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center"
               style={{ background: 'linear-gradient(135deg, var(--bg-overlay) 0%, rgba(0,0,0,0.3) 100%)' }}>
            <Music2 size={28} style={{ color: 'var(--text-muted)', opacity: 0.35 }} />
          </div>
        )}

        {/* Rank badge */}
        <div className="absolute top-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md"
             style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(6px)' }}>
          <span className="text-[11px] font-black" style={{ color: 'white', letterSpacing: '-0.02em' }}>
            #{entry.rank}
          </span>
        </div>

        {/* In library badge */}
        {entry.in_library && (
          <div className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center"
               style={{ background: 'rgba(0,212,170,0.25)', border: '1px solid rgba(0,212,170,0.5)' }}>
            <Library size={10} style={{ color: '#00d4aa' }} />
          </div>
        )}

        {/* Hover overlay — only show download CTA if not already in library */}
        <div className="absolute inset-0 flex items-center justify-center billboard-card-overlay transition-opacity duration-200"
             style={{ background: 'rgba(0,0,0,0.45)', pointerEvents: 'none' }}>
          {entry.in_library ? (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
                 style={{ background: 'rgba(0,212,170,0.85)', color: '#fff' }}>
              <CheckCircle2 size={11} />
              Have it
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
                 style={{ background: 'var(--accent)', color: 'var(--bg-card)' }}>
              <Download size={11} />
              Get album
            </div>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="p-2.5">
        <div className="text-xs font-semibold leading-tight truncate" style={{ color: 'var(--text-primary)' }}>
          {entry.title}
        </div>
        <div className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--text-secondary)' }}>
          {entry.artist}
        </div>
        <div className="flex items-center justify-between mt-1.5 gap-1">
          {/* Weeks on chart */}
          {entry.weeks_on_chart ? (
            <div className="flex items-center gap-1">
              <Flame size={9} style={{ color: '#f97316', opacity: entry.weeks_on_chart > 4 ? 1 : 0.5 }} />
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {entry.weeks_on_chart}w
              </span>
            </div>
          ) : <span />}

          {/* Trend badge */}
          {entry.position_change === null || entry.position_change === undefined ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded-md font-semibold"
                  style={{ background: 'rgba(96,165,250,0.15)', color: '#60a5fa' }}>
              NEW
            </span>
          ) : entry.position_change > 0 ? (
            <div className="flex items-center gap-0.5">
              <ArrowUp size={9} style={{ color: '#00d4aa' }} />
              <span className="text-[10px] font-semibold" style={{ color: '#00d4aa' }}>
                {entry.position_change}
              </span>
            </div>
          ) : entry.position_change < 0 ? (
            <div className="flex items-center gap-0.5">
              <ArrowDown size={9} style={{ color: '#f87171' }} />
              <span className="text-[10px] font-semibold" style={{ color: '#f87171' }}>
                {Math.abs(entry.position_change)}
              </span>
            </div>
          ) : (
            <Minus size={9} style={{ color: 'var(--text-muted)' }} />
          )}
        </div>
      </div>
    </button>
  )
}

// ── Billboard strip ───────────────────────────────────────────────────────────
function BillboardStrip() {
  const [entries, setEntries] = useState([])
  const [state, setState] = useState('loading') // loading | fetching | ready | error
  const [selected, setSelected] = useState(null)
  const [downloadedIds, setDownloadedIds] = useState(new Set())
  const LIMIT = 5

  const loadEntries = useCallback(async (triggerRefreshIfEmpty = false) => {
    try {
      const d = await api.get(`/api/indexer/billboard?limit=${LIMIT}`)
      const list = Array.isArray(d) ? d : []

      if (list.length > 0) {
        setEntries(list)
        setState('ready')
      } else if (triggerRefreshIfEmpty) {
        // Table is empty — trigger a background fetch and poll for results
        setState('fetching')
        await api.post('/api/indexer/billboard/refresh')
        // Poll every 3s for up to 60s waiting for billboard.py to return data
        let attempts = 0
        const poll = setInterval(async () => {
          attempts++
          try {
            const r2 = await api.get(`/api/indexer/billboard?limit=${LIMIT}`)
            const d2 = await r2.json()
            const list2 = Array.isArray(d2) ? d2 : []
            if (list2.length > 0) {
              clearInterval(poll)
              setEntries(list2)
              setState('ready')
            } else if (attempts >= 20) {
              clearInterval(poll)
              setState('error')
            }
          } catch {
            clearInterval(poll)
            setState('error')
          }
        }, 3000)
      } else {
        setState('ready') // empty but don't re-trigger
      }
    } catch {
      setState('error')
    }
  }, [])

  useEffect(() => { loadEntries(true) }, [loadEntries])

  if (state === 'loading') return (
    <div className="anim-fade-up rounded-xl px-4 py-3 flex items-center gap-3"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <Loader2 size={14} className="animate-spin flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading Billboard Hot 100…</span>
    </div>
  )

  if (state === 'fetching') return (
    <div className="anim-fade-up rounded-xl px-4 py-3 flex items-center gap-3"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <Loader2 size={14} className="animate-spin flex-shrink-0" style={{ color: '#f97316' }} />
      <div>
        <div className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>Fetching Billboard Hot 100…</div>
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>First load takes ~10 seconds</div>
      </div>
    </div>
  )

  if (state === 'error') return (
    <div className="anim-fade-up rounded-xl px-4 py-3 flex items-center justify-between gap-3"
         style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2" style={{ color: 'var(--text-muted)' }}>
        <XCircle size={14} style={{ color: '#f87171', flexShrink: 0 }} />
        <span className="text-xs">Billboard chart unavailable — check network access</span>
      </div>
      <button onClick={() => { setState('loading'); loadEntries(true) }}
              className="text-xs px-3 py-1 rounded-lg flex-shrink-0"
              style={{ background: 'var(--bg-overlay)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
        Retry
      </button>
    </div>
  )

  if (!entries.length) return null

  return (
    <>
      <div className="space-y-2 anim-fade-up">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="section-label flex items-center gap-2">
            <Flame size={12} style={{ color: '#f97316' }} />
            Billboard Hot 100
            {entries[0]?.chart_date && (
              <span className="normal-case font-normal" style={{ color: 'var(--text-muted)' }}>
                · {entries[0].chart_date}
              </span>
            )}
          </div>
          <Link
            to="/discovery"
            className="flex items-center gap-1 text-[11px] transition-colors hover:opacity-80"
            style={{ color: 'var(--accent)' }}
          >
            Discovery queue <ChevronRight size={11} />
          </Link>
        </div>

        {/* Cards */}
        <div className={`grid gap-3`}
             style={{ gridTemplateColumns: `repeat(${Math.min(entries.length, LIMIT)}, minmax(0, 1fr))` }}>
          {entries.map((entry, i) => (
            <BillboardCard
              key={entry.rank}
              entry={downloadedIds.has(entry.rank)
                ? { ...entry, in_library: true }
                : entry
              }
              rank={i}
              onClick={entry.in_library ? undefined : () => setSelected(entry)}
            />
          ))}
        </div>
      </div>

      {selected && (
        <BillboardDownloadModal
          entry={selected}
          onClose={() => setSelected(null)}
          onSuccess={() => {
            setDownloadedIds(prev => new Set([...prev, selected.rank]))
            setTimeout(() => setSelected(null), 1800)
          }}
        />
      )}
    </>
  )
}

// ── Connection status dot ─────────────────────────────────────────────────────
function ConnectionStatus({ service, label }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    const url = service === 'lastfm'
      ? '/api/external-apis/status'
      : `/api/connections/${service}`
    api.get(url)
      
      .then(d => {
        if (service === 'lastfm') {
          setData({ is_connected: d?.lastfm?.configured === true })
        } else {
          setData(d)
        }
      })
      .catch(() => setData({ is_connected: false }))
  }, [service])
  const ok = data?.is_connected
  return (
    <div className="flex items-center gap-2.5 py-2">
      <div className={`w-2 h-2 rounded-full flex-shrink-0 transition-all ${
        data === null ? 'bg-[var(--text-muted)]'
        : ok ? 'bg-[var(--accent)]' : 'bg-[var(--danger)]'
      } ${ok ? 'anim-glow' : ''}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium" style={{ color:'var(--text-primary)' }}>{label}</div>
        {data === null
          ? <div className="text-xs" style={{ color:'var(--text-muted)' }}>Checking…</div>
          : ok
          ? <div className="text-xs" style={{ color:'var(--accent)' }}>Connected</div>
          : <div className="text-xs" style={{ color:'var(--text-muted)' }}>
              Not connected — <Link to={service === 'lastfm' ? '/settings' : '/connections'} style={{ color:'var(--accent)' }} className="hover:underline">configure</Link>
            </div>
        }
      </div>
      {data === null
        ? <Loader2 size={13} className="animate-spin flex-shrink-0" style={{ color:'var(--text-muted)' }} />
        : ok
        ? <CheckCircle2 size={13} style={{ color:'var(--accent)' }} />
        : <XCircle size={13} style={{ color:'var(--danger)' }} />
      }
    </div>
  )
}

// ── Stat card ─────────────────────────────────────────────────────────────────
function StatCard({ icon: Icon, label, value, sub, color = 'var(--accent)', delay = 0 }) {
  return (
    <div className="card anim-fade-up" style={{ animationDelay: `${delay}ms` }}>
      <div className="flex items-start justify-between mb-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center"
             style={{ background:`${color}15`, border:`1px solid ${color}25` }}>
          <Icon size={16} style={{ color }} />
        </div>
      </div>
      <div className="stat-val text-3xl mb-1">{value ?? '—'}</div>
      <div className="text-xs font-medium" style={{ color:'var(--text-secondary)' }}>{label}</div>
      {sub && <div className="text-xs mt-0.5" style={{ color:'var(--text-muted)' }}>{sub}</div>}
    </div>
  )
}

// ── User card ─────────────────────────────────────────────────────────────────
function UserCard({ user }) {
  const ok = user.status === 'ok'
  const initial = user.username?.[0]?.toUpperCase() || '?'
  const hue = user.username.split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360
  const avatarColor = `hsl(${hue},60%,55%)`
  return (
    <div className="card space-y-3 anim-fade-up">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
             style={{ background:`${avatarColor}20`, border:`1.5px solid ${avatarColor}40`, color:avatarColor }}>
          {initial}
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold truncate" style={{ color:'var(--text-primary)' }}>{user.username}</div>
          <div className={`text-xs ${ok ? 'text-[var(--accent)]' : user.status === 'error' ? 'text-[var(--danger)]' : 'text-[var(--text-muted)]'}`}>
            {ok ? 'Synced' : user.status === 'error' ? 'Sync error' : 'Not synced yet'}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 pt-1" style={{ borderTop:'1px solid var(--border)' }}>
        <div>
          <div className="section-label mb-1">Tracks</div>
          <div className="stat-val text-xl">{user.tracks_indexed > 0 ? user.tracks_indexed.toLocaleString() : '—'}</div>
        </div>
        <div>
          <div className="section-label mb-1">Last sync</div>
          <div className="text-xs" style={{ color:'var(--text-secondary)' }}>
            {user.last_synced ? timeAgo(user.last_synced) : '—'}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Activity feed ─────────────────────────────────────────────────────────────
function ActivityFeed({ events }) {
  if (!events.length) return (
    <div className="flex flex-col items-center justify-center py-12 gap-2" style={{ color:'var(--text-muted)' }}>
      <Activity size={24} strokeWidth={1.5} />
      <div className="text-sm">No activity yet — run an index to get started</div>
    </div>
  )
  return (
    <div className="space-y-0 stagger">
      {events.map((e, i) => {
        const meta = EVENT_META[e.event_type] || { icon: Activity, color:'var(--text-muted)', label: e.event_type }
        const Icon = meta.icon
        return (
          <div key={e.id} className="flex items-start gap-3 py-2.5 anim-fade-up"
               style={{ borderBottom: i < events.length - 1 ? '1px solid var(--border)' : 'none' }}>
            <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                 style={{ background:`${meta.color}12`, border:`1px solid ${meta.color}20` }}>
              <Icon size={12} style={{ color: meta.color }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs break-words" style={{ color:'var(--text-primary)' }}>{e.message}</div>
              <div className="text-[10px] mt-0.5" style={{ color:'var(--text-muted)' }}>{meta.label}</div>
            </div>
            <div className="text-[10px] flex-shrink-0 mt-0.5" style={{ color:'var(--text-muted)' }}>{timeAgo(e.created_at)}</div>
          </div>
        )
      })}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [users, setUsers]           = useState([])
  const [libraryStats, setLib]      = useState(null)
  const [schedulerStatus, setSched] = useState(null)
  const [activity, setActivity]     = useState([])
  const [indexing, setIndexing]     = useState(false)
  const { isAdmin } = useAuth()

  const { indexStatus, cacheStatus, enrichStatus, discoverStatus, downloadStatus, startPolling } = useJobStatus((finalState) => {
    setIndexing(false)
    fetchAll()
  })

  const fetchAll = useCallback(() => {
    api.get('/api/indexer/status').then(setUsers).catch(() => {})
    api.get('/api/indexer/library-stats').then(setLib).catch(() => {})
    api.get('/api/indexer/scheduler').then(setSched).catch(() => {})
    api.get('/api/automation/activity?limit=20').then(setActivity).catch(() => {})
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const handleIndex = async () => {
    setIndexing(true)
    try {
      await api.post('/api/indexer/full-scan')
      startPolling()
    } catch {
      setIndexing(false)
    }
  }

  const isIndexRunning = indexing || !!indexStatus?.running
  const nextIndex      = schedulerStatus?.play_history_index?.next_run
  const totalTracks    = Array.isArray(users) ? users.reduce((s, u) => s + (u.tracks_indexed || 0), 0) : 0

  return (
    <div className="space-y-6 max-w-6xl">

      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap anim-fade-up">
        <div>
          <h1 style={{ fontFamily:'Syne', fontWeight:800, fontSize:26, letterSpacing:'-0.02em', color:'var(--text-primary)', lineHeight:1.1 }}>
            Dashboard
          </h1>
          <p className="text-sm mt-1" style={{ color:'var(--text-secondary)' }}>
            System overview and recent activity
          </p>
        </div>
        {isAdmin && (
          <button onClick={handleIndex} disabled={isIndexRunning}
                  className="btn-primary">
            {isIndexRunning
              ? <><Loader2 size={14} className="animate-spin" />Indexing…</>
              : <><RefreshCw size={14} />Index Now</>
            }
          </button>
        )}
      </div>

      {/* Live progress */}
      <JobProgress
        indexStatus={indexStatus}
        cacheStatus={cacheStatus}
        enrichStatus={enrichStatus}
        discoverStatus={discoverStatus}
        downloadStatus={downloadStatus}
      />

      {/* ── Billboard Hot 100 ─────────────────────────────────────────────── */}
      <BillboardStrip />

      {/* Top stat row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger">
        <StatCard icon={Music2}     label="Total tracks"    value={totalTracks > 0 ? totalTracks.toLocaleString() : '—'} color="var(--accent)"  delay={0} />
        <StatCard icon={Users}      label="Active users"    value={users.filter(u=>u.status==='ok').length || '—'}        color="#60a5fa"         delay={50} />
        <StatCard icon={TrendingUp} label="Library tracks"  value={libraryStats?.total_tracks?.toLocaleString() ?? '—'}   color="var(--purple)"  delay={100} />
        <StatCard icon={Disc3}      label="Artists tracked" value={libraryStats?.total_artists?.toLocaleString() ?? '—'}  color="#f78166"         delay={150} />
      </div>

      {/* Main grid — Activity wide, Users+Services narrow */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Left column: Users + Services + next-index pill */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="section-label flex items-center gap-2">
              <Users size={12} /> Users
            </div>
          </div>
          {users.length === 0
            ? <div className="card flex items-center justify-center py-12" style={{ color:'var(--text-muted)' }}>
                <Loader2 size={20} className="animate-spin" />
              </div>
            : <div className="space-y-3 stagger">
                {users.map(u => <UserCard key={u.jellyfin_user_id} user={u} />)}
              </div>
          }

          <div className="card anim-fade-up" style={{ animationDelay:'100ms' }}>
            <div className="section-label flex items-center gap-2 mb-3">
              <Radio size={12} /> Services
            </div>
            <div style={{ borderTop:'1px solid var(--border)' }}>
              <ConnectionStatus service="jellyfin" label="Jellyfin" />
              <div style={{ borderTop:'1px solid var(--border)' }}>
                <ConnectionStatus service="lidarr" label="Lidarr" />
              </div>
              <div style={{ borderTop:'1px solid var(--border)' }}>
                <ConnectionStatus service="lastfm" label="Last.fm" />
              </div>
            </div>
          </div>

          {nextIndex && (
            <div className="flex items-center gap-3 px-4 py-3 rounded-xl anim-fade-up"
                 style={{ background:'rgba(96,165,250,0.06)', border:'1px solid rgba(96,165,250,0.15)' }}>
              <Clock size={13} style={{ color:'#60a5fa', flexShrink:0 }} />
              <span className="text-xs" style={{ color:'var(--text-secondary)' }}>
                Next index: <span style={{ color:'var(--text-primary)', fontWeight:600 }}>
                  {new Date(utc(nextIndex)).toLocaleTimeString()}
                </span>
              </span>
            </div>
          )}
        </div>

        {/* Right wide column: Recent Activity */}
        <div className="lg:col-span-2">
          <div className="card anim-fade-up h-full" style={{ animationDelay:'150ms' }}>
            <div className="section-label flex items-center gap-2 mb-3">
              <Activity size={12} /> Recent Activity
            </div>
            <ActivityFeed events={activity} />
          </div>
        </div>
      </div>
    </div>
  )
}
