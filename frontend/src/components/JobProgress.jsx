
/**
 * JobProgress — universal progress panel for all background jobs.
 *
 * Renders a row per job that has something to show (running, finished, or errored).
 * Used on both Dashboard and Settings so progress is visible wherever you are.
 *
 * Props (all optional — pass what the parent has from useJobStatus):
 *   indexStatus    enrichStatus   discoverStatus
 *   cacheStatus    enrichStatus discoverStatus downloadStatus
 */
import { useState, useEffect, useRef } from 'react'
import { Database, Sparkles, Star, Telescope, Download } from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function elapsed(isoStr) {
  if (!isoStr) return null
  const secs = Math.floor((Date.now() - new Date(isoStr + (isoStr.endsWith('Z') ? '' : 'Z'))) / 1000)
  if (secs < 60)   return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

function ElapsedTicker({ startedAt, color }) {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="text-[10px] font-mono tabular-nums" style={{ color }}>
      {elapsed(startedAt)}
    </span>
  )
}

// ── Single job row ────────────────────────────────────────────────────────────

function JobRow({ icon: Icon, label, job, pct, detail, accentColor = 'var(--accent)' }) {
  const isRunning = job?.running
  const isError   = !!job?.error
  const isDone    = !isRunning && !isError && !!job?.finished_at
  const barPct    = isDone ? 100 : Math.max(0, Math.min(100, pct ?? 0))
  const startedAt = job?.started_at
  const rowColor  = isError ? '#f87171' : accentColor

  // Pulse animation when running with indeterminate progress (pct === 0 or unknown)
  const isPulsing = isRunning && barPct === 0

  return (
    <div className="space-y-1.5">
      {/* Label row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0"
               style={{ background: `${rowColor}18`, border: `1px solid ${rowColor}30` }}>
            <Icon size={11} style={{ color: rowColor }} />
          </div>
          <div className="min-w-0">
            <span className="text-xs font-medium" style={{ color: isError ? rowColor : 'var(--text-primary)' }}>
              {label}
            </span>
            {(isRunning || isDone || isError) && (
              <span className="text-xs ml-2 truncate" style={{ color: 'var(--text-muted)' }}>
                {isError
                  ? `Error: ${job.error}`
                  : isDone
                    ? `✓ ${job.phase || 'Complete'}`
                    : (job?.phase || 'Working…')}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isRunning && startedAt && (
            <ElapsedTicker startedAt={startedAt} color="var(--text-muted)" />
          )}
          {isRunning && barPct > 0 && (
            <span className="text-[10px] font-mono tabular-nums"
                  style={{ color: rowColor, minWidth: '2.5rem', textAlign: 'right' }}>
              {barPct}%
            </span>
          )}
          {isDone && (
            <span className="text-[10px]" style={{ color: rowColor }}>✓</span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1 rounded-full overflow-hidden" style={{ background: 'var(--bg-overlay)' }}>
        {isPulsing ? (
          // Indeterminate animated stripe
          <div
            className="h-full rounded-full"
            style={{
              width: '40%',
              background: `linear-gradient(90deg, transparent, ${rowColor}cc, ${rowColor}, ${rowColor}cc, transparent)`,
              animation: 'progressPulse 1.6s ease-in-out infinite',
            }}
          />
        ) : (
          <div
            className="h-full rounded-full transition-all duration-700 ease-out"
            style={{
              width: `${barPct}%`,
              background: isError
                ? '#f87171'
                : `linear-gradient(90deg, ${rowColor}cc, ${rowColor})`,
              boxShadow: isRunning ? `0 0 6px ${rowColor}80` : 'none',
            }}
          />
        )}
      </div>

      {/* Detail sub-line */}
      {detail && !isError && (
        <div className="text-[10px] truncate pl-7" style={{ color: 'var(--text-muted)' }}>
          {detail}
        </div>
      )}
    </div>
  )
}

// ── CSS for indeterminate pulse (injected once) ───────────────────────────────

let _styleInjected = false
function injectPulseStyle() {
  if (_styleInjected || typeof document === 'undefined') return
  _styleInjected = true
  const el = document.createElement('style')
  el.textContent = `
    @keyframes progressPulse {
      0%   { transform: translateX(-100%); }
      100% { transform: translateX(350%); }
    }
  `
  document.head.appendChild(el)
}

// ── Main export ───────────────────────────────────────────────────────────────

const JOB_ROWS = [
  {
    key: 'index',
    icon: Database,
    label: 'Library Index',
    color: 'var(--accent)',
    getPct: s => Math.max(0, Math.min(100, s?.percent ?? 0)),
    getDetail: s => s?.detail || null,
  },
  {
    key: 'cache',
    icon: Sparkles,
    label: 'Popularity Cache',
    color: '#60a5fa',
    getPct: s => {
      const raw = s?.progress_pct ?? (s?.total > 0 ? Math.round(100 * (s.done ?? 0) / s.total) : 0)
      return Math.max(0, Math.min(100, raw))
    },
    getDetail: s => s?.total > 0 ? `${(s.done ?? 0).toLocaleString()} / ${s.total.toLocaleString()} artists` : (s?.phase || null),
  },
  {
    key: 'enrich',
    icon: Star,
    label: 'Track & Artist Enrichment',
    color: '#a78bfa',
    getPct: s => {
      if (!s) return 0
      const trackDone  = s.tracks_done  ?? 0
      const trackTotal = s.tracks_total ?? 0
      const artDone    = s.artists_done  ?? 0
      const artTotal   = s.artists_total ?? 0
      if (s.phase === 'Fetching song data' && trackTotal > 0)
        return Math.round(trackDone / trackTotal * 50)  // first half
      if (s.phase === 'Fetching artist data' && artTotal > 0)
        return 50 + Math.round(artDone / artTotal * 50) // second half
      return s.running ? 5 : 0
    },
    getDetail: s => {
      if (!s) return null
      if (s.phase === 'Fetching song data')
        return `Songs: ${s.tracks_done ?? 0} / ${s.tracks_total ?? '…'}${s.current_item ? ` — ${s.current_item}` : ''}`
      if (s.phase === 'Fetching artist data')
        return `Artists: ${s.artists_done ?? 0} / ${s.artists_total ?? '…'}${s.current_item ? ` — ${s.current_item}` : ''}`
      if (s.phase === 'Complete')
        return `${s.tracks_enriched ?? 0} songs · ${s.artists_enriched ?? 0} artists enriched`
      return s.phase || null
    },
  },
  {
    key: 'discover',
    icon: Telescope,
    label: 'Discovery Refresh',
    color: 'var(--accent)',
    getPct: s => Math.max(0, Math.min(100, s?.progress_pct ?? 0)),
    getDetail: s => s?.detail || s?.phase || null,
  },
  {
    key: 'download',
    icon: Download,
    label: 'Auto-Download',
    color: '#d29922',
    getPct: s => Math.max(0, Math.min(100, s?.progress_pct ?? 0)),
    getDetail: s => s?.detail || s?.phase || null,
  },
]

const HIDE_AFTER_MS = 20_000  // hide completed rows after 20 s

export default function JobProgress({
  indexStatus,
  cacheStatus,
  enrichStatus,
  discoverStatus,
  downloadStatus,
}) {
  injectPulseStyle()

  const statuses = {
    index:    indexStatus,
    cache:    cacheStatus,
    enrich:   enrichStatus,
    discover: discoverStatus,
    download: downloadStatus,
  }

  // completedAt[key] = timestamp (ms) when a job first entered "done" state
  const completedAt = useRef({})
  // hidden[key] = true once the 20-s timer has fired
  const [hidden, setHidden] = useState({})

  useEffect(() => {
    const timers = []

    JOB_ROWS.forEach(({ key }) => {
      const s = statuses[key]
      const isDone = s && !s.running && !s.error && !!s.finished_at

      if (isDone) {
        // Record the first time we see this job as done
        if (!completedAt.current[key]) {
          completedAt.current[key] = Date.now()
        }
        // If not already hidden, schedule (or fire immediately if overdue)
        if (!hidden[key]) {
          const elapsed = Date.now() - completedAt.current[key]
          const delay = Math.max(0, HIDE_AFTER_MS - elapsed)
          const id = setTimeout(() => {
            setHidden(prev => ({ ...prev, [key]: true }))
          }, delay)
          timers.push(id)
        }
      } else {
        // Job is running or errored — reset so it shows again on next completion
        if (completedAt.current[key]) {
          delete completedAt.current[key]
        }
        if (hidden[key]) {
          setHidden(prev => { const n = { ...prev }; delete n[key]; return n })
        }
      }
    })

    return () => timers.forEach(clearTimeout)
  })  // run every render so timers stay accurate as statuses update

  const visible = JOB_ROWS.filter(({ key }) => {
    if (hidden[key]) return false
    const s = statuses[key]
    return s && (s.running || s.finished_at || s.error)
  })

  if (visible.length === 0) return null

  const hasError    = visible.some(({ key }) => statuses[key]?.error)
  const borderColor = hasError ? 'rgba(248,113,113,0.2)' : 'rgba(0,212,170,0.15)'
  const bgColor     = hasError ? 'rgba(248,113,113,0.04)' : 'rgba(0,212,170,0.04)'

  return (
    <div
      className="rounded-xl px-4 py-3 space-y-3 anim-scale-in"
      style={{ background: bgColor, border: `1px solid ${borderColor}` }}
    >
      {visible.map(({ key, icon, label, color, getPct, getDetail }, i) => {
        const s = statuses[key]
        return (
          <div key={key}>
            {i > 0 && <div style={{ borderTop: '1px solid var(--border)', marginBottom: 12 }} />}
            <JobRow
              icon={icon}
              label={label}
              job={s}
              pct={getPct(s)}
              detail={getDetail(s)}
              accentColor={color}
            />
          </div>
        )
      })}
    </div>
  )
}
