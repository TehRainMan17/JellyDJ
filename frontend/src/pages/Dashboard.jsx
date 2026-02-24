import { useState, useEffect, useCallback } from 'react'
import {
  Activity, Music2, Telescope, CheckCircle2, XCircle, Loader2,
  RefreshCw, Clock, Database, SkipForward, Heart, Disc3, Download,
  Users, TrendingUp, Radio,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { useJobStatus } from '../hooks/useJobStatus.js'
import JobProgress from '../components/JobProgress.jsx'

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

// ── Connection status dot ─────────────────────────────────────────────────────
function ConnectionStatus({ service, label }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    // lastfm lives under external-apis, not connections
    const url = service === 'lastfm'
      ? '/api/external-apis/status'
      : `/api/connections/${service}`
    fetch(url)
      .then(r => r.json())
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
              <div className="text-xs truncate" style={{ color:'var(--text-primary)' }}>{e.message}</div>
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

  const fetchAll = useCallback(() => {
    fetch('/api/indexer/status').then(r => r.json()).then(setUsers).catch(() => {})
    fetch('/api/indexer/library-stats').then(r => r.json()).then(setLib).catch(() => {})
    fetch('/api/indexer/scheduler').then(r => r.json()).then(setSched).catch(() => {})
    fetch('/api/automation/activity?limit=20').then(r => r.json()).then(setActivity).catch(() => {})
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const { jobStatus, startPolling } = useJobStatus((finalState) => {
    setIndexing(false)
    fetchAll()
  })

  const handleIndex = async () => {
    setIndexing(true)
    try {
      await fetch('/api/indexer/full-scan', { method: 'POST' })
      startPolling()
    } catch {
      setIndexing(false)
    }
  }

  const nextIndex = schedulerStatus?.play_history_index?.next_run
  const totalTracks = Array.isArray(users) ? users.reduce((s, u) => s + (u.tracks_indexed || 0), 0) : 0

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
        <button onClick={handleIndex} disabled={indexing}
                className="btn-primary">
          {indexing
            ? <><Loader2 size={14} className="animate-spin" />Indexing…</>
            : <><RefreshCw size={14} />Index Now</>
          }
        </button>
      </div>

      {/* Live index progress */}
      {(indexing || (jobStatus && (jobStatus.running || jobStatus.finished_at))) && (
        <JobProgress job={jobStatus} label="Index" />
      )}

      {/* Top stat row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger">
        <StatCard icon={Music2}    label="Total tracks"   value={totalTracks > 0 ? totalTracks.toLocaleString() : '—'} color="var(--accent)"   delay={0} />
        <StatCard icon={Users}     label="Active users"   value={users.filter(u=>u.status==='ok').length || '—'}        color="#60a5fa"           delay={50} />
        <StatCard icon={TrendingUp} label="Library tracks" value={libraryStats?.total_tracks?.toLocaleString() ?? '—'} color="var(--purple)"    delay={100} />
        <StatCard icon={Disc3}     label="Artists tracked" value={libraryStats?.total_artists?.toLocaleString() ?? '—'} color="#f78166"          delay={150} />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Users */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <div className="section-label flex items-center gap-2">
              <Users size={12} />  Users
            </div>
          </div>
          {users.length === 0
            ? <div className="card flex items-center justify-center py-12" style={{ color:'var(--text-muted)' }}>
                <Loader2 size={20} className="animate-spin" />
              </div>
            : <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger">
                {users.map(u => <UserCard key={u.jellyfin_user_id} user={u} />)}
              </div>
          }

          {/* Next index */}
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

        {/* Right column */}
        <div className="space-y-4">
          {/* Connections */}
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

          {/* Activity feed */}
          <div className="card anim-fade-up" style={{ animationDelay:'150ms' }}>
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
