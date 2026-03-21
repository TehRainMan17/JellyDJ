/**
 * JobProgress — universal progress panel for all background jobs.
 *
 * Renders a row per job that has something to show (running, finished, or errored).
 * Used on both Dashboard and Settings so progress is visible wherever you are.
 *
 * BUG FIXES vs previous version:
 *   - useEffect had no dependency array, causing it to re-run and re-register
 *     hide timers on every render. Timers are now tracked in a stable ref
 *     (timerIds) and only re-scheduled when the relevant job key's done state
 *     actually changes — preventing the "bar disappears immediately" issue.
 *   - Each job's completed-at timestamp is persisted in a ref so the 15-20s
 *     window is measured from the first time we saw it done, not reset on
 *     every re-render.
 *   - Verbose per-phase detail text added for every job type so the bar always
 *     shows something meaningful and users know it hasn't stalled.
 *   - Progress % is shown during indeterminate phases too (spinner + "Working…"
 *     replaced with explicit phase names and counters where available).
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { Database, Sparkles, Star, Telescope, Download, Users, TrendingUp } from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function elapsed(isoStr) {
  if (!isoStr) return null
  const secs = Math.floor((Date.now() - new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z')) / 1000)
  if (secs < 60)   return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

function ElapsedTicker({ startedAt, color }) {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  return (
    <span className="text-[10px] font-mono tabular-nums" style={{ color }}>
      {elapsed(startedAt)}
    </span>
  )
}

// ── Single job row ────────────────────────────────────────────────────────────

function JobRow({ icon: Icon, label, job, pct, phaseLabel, detail, accentColor = 'var(--accent)' }) {
  const isRunning = job?.running
  const isError   = !!job?.error
  const isDone    = !isRunning && !isError && !!job?.finished_at
  const barPct    = isDone ? 100 : Math.max(0, Math.min(100, pct ?? 0))
  const startedAt = job?.started_at
  const rowColor  = isError ? '#f87171' : accentColor

  // Indeterminate pulse when running but no % yet
  const isPulsing = isRunning && barPct === 0

  // Build a human status string
  const statusText = isError
    ? `Error: ${job.error}`
    : isDone
      ? `✓ Done`
      : phaseLabel || job?.phase || 'Starting…'

  return (
    <div className="space-y-1.5">
      {/* Label row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0"
            style={{ background: `${rowColor}18`, border: `1px solid ${rowColor}30` }}
          >
            <Icon size={11} style={{ color: rowColor }} />
          </div>
          <div className="min-w-0">
            <span className="text-xs font-medium" style={{ color: isError ? rowColor : 'var(--text-primary)' }}>
              {label}
            </span>
            {(isRunning || isDone || isError) && (
              <span className="text-xs ml-2 truncate" style={{ color: 'var(--text-muted)' }}>
                {statusText}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isRunning && startedAt && (
            <ElapsedTicker startedAt={startedAt} color="var(--text-muted)" />
          )}
          {isRunning && barPct > 0 && (
            <span
              className="text-[10px] font-mono tabular-nums"
              style={{ color: rowColor, minWidth: '2.5rem', textAlign: 'right' }}
            >
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

      {/* Detail sub-line — always show when running so users see activity */}
      {!isError && detail && (
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

// ── Per-job configuration ─────────────────────────────────────────────────────

/**
 * getPct   — returns 0..100
 * getPhase — short status string shown next to the label (replaces "Working…")
 * getDetail — tiny sub-line with counts / current item
 */
const JOB_ROWS = [
  {
    key: 'index',
    icon: Database,
    label: 'Library Index',
    color: 'var(--accent)',
    getPct: s => Math.max(0, Math.min(100, s?.percent ?? 0)),
    getPhase: s => {
      if (!s?.running) return null
      const pct = s.percent ?? 0
      if (pct === 0)  return 'Starting scan…'
      if (pct < 20)   return 'Scanning users…'
      if (pct < 50)   return 'Importing play history…'
      if (pct < 80)   return 'Building taste profiles…'
      if (pct < 100)  return 'Finalising scores…'
      return 'Wrapping up…'
    },
    getDetail: s => {
      if (!s) return null
      if (s.detail) return s.detail
      if (s.phase)  return s.phase
      return null
    },
  },
  {
    key: 'cache',
    icon: Sparkles,
    label: 'Popularity Cache',
    color: '#60a5fa',
    getPct: s => {
      if (!s) return 0
      const raw = s.progress_pct ?? (s.total > 0 ? Math.round(100 * (s.done ?? 0) / s.total) : 0)
      return Math.max(0, Math.min(100, raw))
    },
    getPhase: s => {
      if (!s?.running) return null
      const done  = s.done  ?? 0
      const total = s.total ?? 0
      if (total === 0) return 'Starting cache refresh…'
      return `Fetching artist data (${done.toLocaleString()} / ${total.toLocaleString()})`
    },
    getDetail: s => {
      if (!s) return null
      if (s.current_artist) return `↳ ${s.current_artist}`
      if (s.phase)           return s.phase
      return null
    },
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
        return Math.round(trackDone / trackTotal * 50)        // first half
      if (s.phase === 'Fetching artist data' && artTotal > 0)
        return 50 + Math.round(artDone / artTotal * 50)       // second half
      return s.running ? 5 : 0
    },
    getPhase: s => {
      if (!s?.running) return null
      if (s.phase === 'Fetching song data') {
        const done  = s.tracks_done  ?? 0
        const total = s.tracks_total ?? 0
        return total > 0
          ? `Songs: ${done.toLocaleString()} / ${total.toLocaleString()}`
          : 'Fetching song data…'
      }
      if (s.phase === 'Fetching artist data') {
        const done  = s.artists_done  ?? 0
        const total = s.artists_total ?? 0
        return total > 0
          ? `Artists: ${done.toLocaleString()} / ${total.toLocaleString()}`
          : 'Fetching artist data…'
      }
      return s.phase || 'Starting enrichment…'
    },
    getDetail: s => {
      if (!s) return null
      if (s.current_item) return `↳ ${s.current_item}`
      if (s.phase === 'Fetching song data' && (s.tracks_enriched ?? 0) > 0)
        return `${s.tracks_enriched} enriched so far${s.tracks_failed > 0 ? ` · ${s.tracks_failed} unmatched` : ''}`
      if (s.phase === 'Fetching artist data' && (s.artists_enriched ?? 0) > 0)
        return `${s.artists_enriched} artists done so far`
      if (s.phase === 'Complete')
        return `${s.tracks_enriched ?? 0} songs · ${s.artists_enriched ?? 0} artists enriched`
      return null
    },
  },
  {
    key: 'discover',
    icon: Telescope,
    label: 'Discovery Refresh',
    color: 'var(--accent)',
    getPct: s => Math.max(0, Math.min(100, s?.progress_pct ?? 0)),
    getPhase: s => {
      if (!s?.running) return null
      const done  = s.users_done  ?? 0
      const total = s.users_total ?? 0
      if (total > 0) return `Processing user ${done + 1} of ${total}`
      return s.phase || 'Refreshing discovery queue…'
    },
    getDetail: s => {
      if (!s) return null
      if (s.detail) return s.detail
      if ((s.items_added ?? 0) > 0) return `${s.items_added} items added so far`
      return null
    },
  },
  {
    key: 'download',
    icon: Download,
    label: 'Auto-Download',
    color: '#d29922',
    getPct: s => Math.max(0, Math.min(100, s?.progress_pct ?? 0)),
    getPhase: s => {
      if (!s?.running) return null
      const sent  = s.sent  ?? 0
      const total = s.total ?? 0
      if (total > 0) return `Sending ${sent + 1} of ${total} to Lidarr`
      return s.phase || 'Checking download queue…'
    },
    getDetail: s => {
      if (!s) return null
      if (s.detail) return s.detail
      if ((s.sent ?? 0) > 0) return `${s.sent} album${s.sent !== 1 ? 's' : ''} sent to Lidarr`
      return null
    },
  },
]

// How long a finished job stays visible before the row fades out
const HIDE_AFTER_MS = 15_000   // 15 s (well within the 10–20 s spec)

// ── Main export ───────────────────────────────────────────────────────────────

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

  // completedAt[key] = ms timestamp of when we first saw a job as done
  const completedAt = useRef({})
  // timer IDs per key so we can cancel and replace without pile-up
  const timerIds = useRef({})
  // hidden[key] = true once hide timer fires
  const [hidden, setHidden] = useState({})

  useEffect(() => {
    JOB_ROWS.forEach(({ key }) => {
      const s = statuses[key]
      const isDone = s && !s.running && !s.error && !!s.finished_at

      if (isDone) {
        // Record the first time we see this job as done
        if (!completedAt.current[key]) {
          completedAt.current[key] = Date.now()
        }

        // Only schedule a NEW timer if we don't already have one pending
        if (!timerIds.current[key] && !hidden[key]) {
          const age   = Date.now() - completedAt.current[key]
          const delay = Math.max(0, HIDE_AFTER_MS - age)
          timerIds.current[key] = setTimeout(() => {
            timerIds.current[key] = null
            setHidden(prev => ({ ...prev, [key]: true }))
          }, delay)
        }
      } else {
        // Job is running/errored or has no data — reset so it shows on next finish
        if (completedAt.current[key]) {
          delete completedAt.current[key]
        }
        // Cancel any pending hide timer
        if (timerIds.current[key]) {
          clearTimeout(timerIds.current[key])
          timerIds.current[key] = null
        }
        // Un-hide if it was hidden (job re-started)
        if (hidden[key]) {
          setHidden(prev => {
            const n = { ...prev }
            delete n[key]
            return n
          })
        }
      }
    })

    // Cleanup: cancel all timers when component unmounts
    return () => {
      Object.values(timerIds.current).forEach(id => id && clearTimeout(id))
    }
  // We intentionally list individual status objects so the effect re-runs
  // only when a status actually changes, not on every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indexStatus, cacheStatus, enrichStatus, discoverStatus, downloadStatus])

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
      {visible.map(({ key, icon, label, color, getPct, getPhase, getDetail }, i) => {
        const s = statuses[key]
        return (
          <div key={key}>
            {i > 0 && <div style={{ borderTop: '1px solid var(--border)', marginBottom: 12 }} />}
            <JobRow
              icon={icon}
              label={label}
              job={s}
              pct={getPct(s)}
              phaseLabel={getPhase(s)}
              detail={getDetail(s)}
              accentColor={color}
            />
          </div>
        )
      })}
    </div>
  )
}
