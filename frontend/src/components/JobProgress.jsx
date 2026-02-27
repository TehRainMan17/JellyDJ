/**
 * JobProgress — shows one or two live progress rows for background jobs.
 *
 * Renders a row for the indexer and/or the popularity cache refresh,
 * whichever has something to show. Each row has its own bar and phase label
 * so both can be visible at the same time (they run concurrently after an index).
 *
 * Props:
 *   indexStatus  — from useJobStatus().indexStatus
 *   cacheStatus  — from useJobStatus().cacheStatus
 */
import { useState, useEffect } from 'react'
import { Database, Sparkles } from 'lucide-react'

// ── Elapsed time helper ───────────────────────────────────────────────────────
function elapsed(isoStr) {
  if (!isoStr) return null
  const secs = Math.floor((Date.now() - new Date(isoStr + (isoStr.endsWith('Z') ? '' : 'Z'))) / 1000)
  if (secs < 60)  return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

// ── Ticking elapsed time that re-renders every second ────────────────────────
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
                {isError ? `Error: ${job.error}` : isDone ? '✓ Complete' : (job?.phase || 'Working…')}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isRunning && startedAt && (
            <ElapsedTicker startedAt={startedAt} color="var(--text-muted)" />
          )}
          {isRunning && (
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

// ── Main export ───────────────────────────────────────────────────────────────
export default function JobProgress({ indexStatus, cacheStatus }) {
  const showIndex = indexStatus && (indexStatus.running || indexStatus.finished_at || indexStatus.error)
  const showCache = cacheStatus && (cacheStatus.running || cacheStatus.finished_at || cacheStatus.error)

  if (!showIndex && !showCache) return null

  const indexPct = Math.max(0, Math.min(100, indexStatus?.percent ?? 0))

  const rawCachePct = cacheStatus?.progress_pct
    ?? (cacheStatus?.total > 0 ? Math.round(100 * (cacheStatus.done ?? 0) / cacheStatus.total) : 0)
  const cachePct = Math.max(0, Math.min(100, rawCachePct))

  const cacheDetail = cacheStatus?.total > 0
    ? `${(cacheStatus.done ?? 0).toLocaleString()} / ${cacheStatus.total.toLocaleString()} artists`
    : null

  const hasError  = indexStatus?.error || cacheStatus?.error
  const borderColor = hasError ? 'rgba(248,113,113,0.2)' : 'rgba(0,212,170,0.15)'
  const bgColor     = hasError ? 'rgba(248,113,113,0.04)' : 'rgba(0,212,170,0.04)'

  return (
    <div
      className="rounded-xl px-4 py-3 space-y-3 anim-scale-in"
      style={{ background: bgColor, border: `1px solid ${borderColor}` }}
    >
      {showIndex && (
        <JobRow
          icon={Database}
          label="Index"
          job={indexStatus}
          pct={indexPct}
          detail={indexStatus?.detail}
          accentColor="var(--accent)"
        />
      )}

      {showIndex && showCache && (
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 2 }} />
      )}

      {showCache && (
        <JobRow
          icon={Sparkles}
          label="Popularity cache"
          job={cacheStatus}
          pct={cachePct}
          detail={cacheDetail}
          accentColor="#60a5fa"
        />
      )}
    </div>
  )
}
