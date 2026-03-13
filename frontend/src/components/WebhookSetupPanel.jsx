
import { useState, useEffect } from 'react'
import { apiFetch } from '../lib/api'
import { Zap, Copy, CheckCircle2, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'

export default function WebhookSetupPanel() {
  const [guide, setGuide] = useState(null)
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    apiFetch('/api/webhooks/setup-guide')
      .then(r => r.json())
      .then(setGuide)
      .catch(() => {})
  }, [])

  const handleCopy = () => {
    if (!guide?.webhook_url) return
    navigator.clipboard.writeText(guide.webhook_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-[var(--warning)]/10 border border-[var(--warning)]/20 flex items-center justify-center flex-shrink-0">
            <Zap size={16} className="text-[var(--warning)]" />
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">Playback Webhooks</div>
            <div className="text-xs text-[var(--text-secondary)] mt-0.5">
              Skip tracking via Jellyfin playback events
            </div>
          </div>
        </div>
        <button
          onClick={() => setOpen(v => !v)}
          className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        >
          Setup guide
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>

      <p className="text-xs text-[var(--text-secondary)]">
        When configured, JellyDJ tracks how much of each song you listen to.
        Tracks skipped before <strong className="text-[var(--text-primary)]">{guide?.skip_threshold || '80%'}</strong> completion
        accumulate a skip penalty that reduces their playlist score over time.
        Artist and genre affinity scores are also dampened for frequently skipped content.
      </p>

      {open && guide && (
        <div className="space-y-4 pt-1">
          {/* Webhook URL */}
          <div>
            <div className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">
              Webhook URL
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2
                              text-xs text-[var(--accent)] font-mono overflow-x-auto whitespace-nowrap">
                {guide.webhook_url}
              </code>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                           bg-[var(--bg-elevated)] hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]
                           transition-all flex-shrink-0"
              >
                {copied
                  ? <><CheckCircle2 size={12} className="text-[var(--accent)]" /> Copied</>
                  : <><Copy size={12} /> Copy</>
                }
              </button>
            </div>
          </div>

          {/* Step-by-step */}
          <div>
            <div className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">
              Jellyfin Setup Steps
            </div>
            <ol className="space-y-2">
              {guide.instructions.map((step, i) => (
                <li key={i} className="flex gap-3 text-xs text-[var(--text-secondary)]">
                  <span className="flex-shrink-0 w-5 h-5 rounded-full bg-[var(--bg-elevated)] border border-[var(--border)]
                                   flex items-center justify-center text-[10px] font-mono text-[var(--text-primary)]">
                    {i + 1}
                  </span>
                  <span className="pt-0.5">{step.replace(/^\d+\.\s*/, '')}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className="flex items-start gap-2 bg-[var(--warning)]/5 border border-[var(--warning)]/20 rounded-lg px-3 py-2.5">
            <Zap size={13} className="text-[var(--warning)] flex-shrink-0 mt-0.5" />
            <p className="text-xs text-[var(--text-secondary)]">
              After setup, verify it's working at{' '}
              <code className="text-[var(--text-primary)] font-mono">/api/webhooks/recent/YOUR_USER_ID</code>
              {' '}— play and skip a song in Jellyfin, then refresh.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
