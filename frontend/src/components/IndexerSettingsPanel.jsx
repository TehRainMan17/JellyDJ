
import { useState, useEffect } from 'react'
import { apiFetch } from '../lib/api'
import { Clock, RefreshCw, Loader2, Save } from 'lucide-react'

export default function IndexerSettingsPanel() {
  const [hours, setHours] = useState(6)
  const [lastIndex, setLastIndex] = useState(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState({ text: '', ok: true })

  const showMsg = (text, ok = true) => {
    setMsg({ text, ok })
    setTimeout(() => setMsg({ text: '', ok: true }), 10000)
  }

  useEffect(() => {
    apiFetch('/api/indexer/settings')
      .then(r => r.json())
      .then(data => {
        setHours(data.index_interval_hours ?? 6)
        setLastIndex(data.last_full_index ? new Date(data.last_full_index + 'Z') : null)
      })
      .catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const r = await apiFetch('/api/indexer/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index_interval_hours: hours }),
      })
      showMsg(r.ok ? `Interval set to every ${hours}h.` : 'Save failed.', r.ok)
    } finally {
      setSaving(false)
    }
  }

  const handleRunNow = async () => {
    setRunning(true)
    try {
      const r = await apiFetch('/api/indexer/run-now', { method: 'POST' })
      const data = await r.json()
      showMsg(data.message || 'Index started in background.', true)
    } catch {
      showMsg('Failed to trigger index.', false)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-[var(--accent)]/10 border border-[var(--accent)]/20 flex items-center justify-center flex-shrink-0">
          <Clock size={16} className="text-[var(--accent)]" />
        </div>
        <div>
          <div className="text-sm font-semibold text-[var(--text-primary)]">Play History Indexer</div>
          <div className="text-xs text-[var(--text-secondary)] mt-0.5">
            {lastIndex ? `Last run: ${lastIndex.toLocaleString()}` : 'Never run'}
          </div>
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">
          Index every
        </label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={1}
            max={24}
            step={1}
            value={hours}
            onChange={e => setHours(Number(e.target.value))}
            className="flex-1 accent-[var(--accent)]"
          />
          <span className="text-sm font-mono text-[var(--text-primary)] w-16 text-right">
            {hours}h
          </span>
        </div>
        <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mt-1">
          <span>1h</span>
          <span>6h (default)</span>
          <span>24h</span>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     bg-[var(--bg-elevated)] hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]
                     disabled:opacity-40 transition-all"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save Interval
        </button>
        <button
          onClick={handleRunNow}
          disabled={running}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     bg-[var(--accent)]/10 hover:bg-[var(--accent)]/20 border border-[var(--accent)]/30 text-[var(--accent)]
                     disabled:opacity-40 transition-all"
        >
          {running ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Run Index Now
        </button>
        {msg.text && (
          <span className={`text-xs ${msg.ok ? 'text-[var(--accent)]' : 'text-[var(--danger)]'}`}>
            {msg.text}
          </span>
        )}
      </div>
    </div>
  )
}
