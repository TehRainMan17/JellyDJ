/**
 * JobProgress — inline progress bar + phase label shown during background jobs.
 * Used by Dashboard (indexer), Playlists (generate), Discovery (populate).
 */
export default function JobProgress({ job, label = 'Working…' }) {
  if (!job) return null

  const isRunning = job.running
  const isError   = !!job.error
  const isDone    = !isRunning && !isError && job.finished_at
  const pct       = Math.max(0, Math.min(100, job.percent || 0))

  if (!isRunning && !isDone && !isError) return null

  const barColor  = isError ? 'var(--danger)' : isDone ? 'var(--accent)' : 'var(--accent)'
  const textColor = isError ? 'var(--danger)' : isDone ? 'var(--accent)' : 'var(--text-secondary)'

  return (
    <div className="rounded-xl px-4 py-3 space-y-2 anim-scale-in"
         style={{ background: isError ? 'rgba(248,113,113,0.06)' : 'rgba(0,212,170,0.05)',
                  border: `1px solid ${isError ? 'rgba(248,113,113,0.2)' : 'rgba(0,212,170,0.15)'}` }}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {isRunning && (
            <div className="w-2 h-2 rounded-full flex-shrink-0"
                 style={{ background:'var(--accent)', animation:'pulse 1s ease-in-out infinite' }} />
          )}
          <span className="text-xs font-medium truncate" style={{ color: textColor }}>
            {isError ? `Error: ${job.error}` : isDone ? `${label} complete` : job.phase || label}
          </span>
        </div>
        {isRunning && (
          <span className="text-[10px] font-mono flex-shrink-0" style={{ color:'var(--text-muted)' }}>
            {pct}%
          </span>
        )}
        {isDone && (
          <span className="text-[10px] flex-shrink-0" style={{ color:'var(--accent)' }}>✓ Done</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1 rounded-full overflow-hidden" style={{ background:'var(--bg-overlay)' }}>
        <div className="h-full rounded-full transition-all duration-500"
             style={{ width: isDone ? '100%' : `${pct}%`, background: barColor,
                      boxShadow: isRunning ? `0 0 8px ${barColor}` : 'none' }} />
      </div>

      {/* Detail line */}
      {job.detail && !isError && (
        <div className="text-[10px] truncate" style={{ color:'var(--text-muted)' }}>{job.detail}</div>
      )}
    </div>
  )
}
