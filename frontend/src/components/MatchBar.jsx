/**
 * MatchBar — progress bar showing matched-vs-total ratio for imported playlists.
 *
 * Color thresholds: ≥80% accent, ≥50% amber, <50% danger.
 */

export default function MatchBar({ matched, total, showPercent = false }) {
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0
  const color = pct >= 80 ? 'var(--accent)' : pct >= 50 ? '#fbbf24' : 'var(--danger)'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.08)' }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[10px] font-mono flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
        {matched}/{total}{showPercent ? ` (${pct}%)` : ''}
      </span>
    </div>
  )
}
