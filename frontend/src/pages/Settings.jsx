
import { useState, useEffect } from 'react'
import AutomationPanel from '../components/AutomationPanel.jsx'
import WebhookSetupPanel from '../components/WebhookSetupPanel.jsx'
import {
  CheckCircle2, XCircle, Loader2, Eye, EyeOff,
  Save, Plug, Trash2, Database,
} from 'lucide-react'

// ── Shared ────────────────────────────────────────────────────────────────────

function FieldLabel({ children }) {
  return (
    <label className="block text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-1.5">
      {children}
    </label>
  )
}

function SecretInput({ value, onChange, placeholder, disabled }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full var(--bg) border border-[var(--border)] rounded-lg px-3 py-2 pr-10
                   text-sm text-[var(--text-primary)] placeholder-[#484f58] font-mono
                   focus:outline-none focus:border-[var(--accent)]/60 focus:ring-1 focus:ring-[#00d4aa]/20
                   disabled:opacity-40 transition-colors"
      />
      <button
        type="button"
        onClick={() => setShow(v => !v)}
        disabled={disabled}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors disabled:opacity-40"
        tabIndex={-1}
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  )
}

function StatusPill({ configured }) {
  if (configured === null)
    return <span className="text-xs text-[var(--text-secondary)]">—</span>
  return configured
    ? <span className="flex items-center gap-1 text-xs text-[var(--accent)]"><CheckCircle2 size={12} /> Configured</span>
    : <span className="flex items-center gap-1 text-xs text-[var(--text-secondary)]"><XCircle size={12} /> Not configured</span>
}

function ServiceCard({ title, color, icon, configured, lastUpdated, children, noKey }) {
  return (
    <div className="card space-y-4 anim-fade-up">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div
            className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 text-lg"
            style={{ background: `${color}18`, border: `1px solid ${color}30` }}
          >
            {icon}
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">{title}</div>
            {noKey
              ? <div className="text-xs text-[var(--text-secondary)] mt-0.5">No API key required</div>
              : lastUpdated
                ? <div className="text-xs text-[var(--text-secondary)] mt-0.5">Updated {new Date(lastUpdated).toLocaleDateString()}</div>
                : <div className="text-xs text-[var(--text-secondary)] mt-0.5">Not yet configured</div>
            }
          </div>
        </div>
        <StatusPill configured={noKey ? true : configured} />
      </div>
      {children}
    </div>
  )
}

function TestResult({ result }) {
  if (!result) return null
  return (
    <div className={`flex items-center gap-2 text-xs rounded-lg px-3 py-2 mt-1
      ${result.ok
        ? 'bg-[#00d4aa]/8 border border-[var(--accent)]/20 text-[var(--accent)]'
        : 'bg-[#f85149]/8 border border-[#f85149]/20 text-[var(--danger)]'}`}
    >
      {result.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
      {result.message}
    </div>
  )
}

// ── Spotify Card ──────────────────────────────────────────────────────────────

function SpotifyCard({ status, onStatusChange }) {
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)

  const handleSave = async () => {
    if (!clientId || !clientSecret) return
    setSaving(true)
    try {
      const r = await fetch('/api/external-apis/spotify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
      })
      if (r.ok) {
        setResult({ ok: true, message: 'Credentials saved.' })
        onStatusChange()
      } else {
        setResult({ ok: false, message: 'Save failed.' })
      }
    } finally {
      setSaving(false)
      setTimeout(() => setResult(null), 10000)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await fetch('/api/external-apis/spotify/test', { method: 'POST' })
      const data = await r.json()
      setResult({ ok: r.ok, message: r.ok ? data.message : (data.detail || 'Test failed.') })
    } catch {
      setResult({ ok: false, message: 'Network error.' })
    } finally {
      setTesting(false)
      setTimeout(() => setResult(null), 10000)
    }
  }

  return (
    <ServiceCard
      title="Spotify"
      color="#1db954"
      icon="🎵"
      configured={status?.spotify?.configured}
      lastUpdated={status?.spotify?.last_updated}
    >
      <div className="space-y-3">
        <div>
          <FieldLabel>Client ID</FieldLabel>
          <SecretInput
            value={clientId}
            onChange={setClientId}
            placeholder={status?.spotify?.has_client_id ? 'Stored — paste new to update' : 'From Spotify Developer Dashboard'}
          />
        </div>
        <div>
          <FieldLabel>Client Secret</FieldLabel>
          <SecretInput
            value={clientSecret}
            onChange={setClientSecret}
            placeholder={status?.spotify?.has_client_secret ? 'Stored — paste new to update' : 'From Spotify Developer Dashboard'}
          />
        </div>
        <p className="text-[11px] text-[var(--text-secondary)]">
          Create an app at{' '}
          <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer"
             className="text-[var(--accent)] hover:underline">
            developer.spotify.com/dashboard
          </a>
          . No special scopes needed — Client Credentials flow only.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || (!clientId && !clientSecret)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     var(--bg-elevated) hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]
                     disabled:opacity-40 transition-all"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save
        </button>
        <button
          onClick={handleTest}
          disabled={testing || !status?.spotify?.configured}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     bg-[#1db954]/10 hover:bg-[#1db954]/20 border border-[#1db954]/30 text-[#1db954]
                     disabled:opacity-40 transition-all"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Plug size={12} />}
          Test
        </button>
      </div>
      <TestResult result={result} />
    </ServiceCard>
  )
}

// ── Last.fm Card ──────────────────────────────────────────────────────────────

function LastFmCard({ status, onStatusChange }) {
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)

  const handleSave = async () => {
    if (!apiKey) return
    setSaving(true)
    try {
      const r = await fetch('/api/external-apis/lastfm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }),
      })
      if (r.ok) {
        setResult({ ok: true, message: 'Credentials saved.' })
        onStatusChange()
      } else {
        setResult({ ok: false, message: 'Save failed.' })
      }
    } finally {
      setSaving(false)
      setTimeout(() => setResult(null), 10000)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await fetch('/api/external-apis/lastfm/test', { method: 'POST' })
      const data = await r.json()
      setResult({ ok: r.ok, message: r.ok ? data.message : (data.detail || 'Test failed.') })
    } catch {
      setResult({ ok: false, message: 'Network error.' })
    } finally {
      setTesting(false)
      setTimeout(() => setResult(null), 10000)
    }
  }

  return (
    <ServiceCard
      title="Last.fm"
      color="#d51007"
      icon="📻"
      configured={status?.lastfm?.configured}
      lastUpdated={status?.lastfm?.last_updated}
    >
      <div className="space-y-3">
        <div>
          <FieldLabel>API Key</FieldLabel>
          <SecretInput
            value={apiKey}
            onChange={setApiKey}
            placeholder={status?.lastfm?.has_api_key ? 'Stored — paste new to update' : 'From last.fm/api/account/create'}
          />
        </div>
        <div>
          <FieldLabel>Shared Secret <span className="normal-case text-[var(--text-muted)] font-normal">(optional)</span></FieldLabel>
          <SecretInput
            value={apiSecret}
            onChange={setApiSecret}
            placeholder="Optional — only needed for write operations"
          />
        </div>
        <p className="text-[11px] text-[var(--text-secondary)]">
          Get a free API key at{' '}
          <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer"
             className="text-[var(--accent)] hover:underline">
            last.fm/api/account/create
          </a>.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !apiKey}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     var(--bg-elevated) hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]
                     disabled:opacity-40 transition-all"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save
        </button>
        <button
          onClick={handleTest}
          disabled={testing || !status?.lastfm?.configured}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     bg-[#d51007]/10 hover:bg-[#d51007]/20 border border-[#d51007]/30 text-[#d51007]
                     disabled:opacity-40 transition-all"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Plug size={12} />}
          Test
        </button>
      </div>
      <TestResult result={result} />
    </ServiceCard>
  )
}

// ── No-key service card (MusicBrainz / Billboard) ─────────────────────────────

function NoKeyServiceCard({ title, color, icon, testUrl, description, docsUrl, docsLabel }) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)

  const handleTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await fetch(testUrl, { method: 'POST' })
      const data = await r.json()
      setResult({ ok: r.ok, message: r.ok ? data.message : (data.detail || 'Test failed.') })
    } catch {
      setResult({ ok: false, message: 'Network error.' })
    } finally {
      setTesting(false)
      setTimeout(() => setResult(null), 10000)
    }
  }

  return (
    <ServiceCard title={title} color={color} icon={icon} noKey>
      <p className="text-xs text-[var(--text-secondary)]">{description}</p>
      {docsUrl && (
        <p className="text-[11px] text-[var(--text-secondary)]">
          <a href={docsUrl} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
            {docsLabel}
          </a>
        </p>
      )}
      <div>
        <button
          onClick={handleTest}
          disabled={testing}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                     var(--bg-elevated) hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]
                     disabled:opacity-40 transition-all"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Plug size={12} />}
          Test Connection
        </button>
      </div>
      <TestResult result={result} />
    </ServiceCard>
  )
}

// ── Cache panel ───────────────────────────────────────────────────────────────

function CachePanel() {
  const [stats, setStats] = useState(null)
  const [clearing, setClearing] = useState(false)

  const fetchStats = () => {
    fetch('/api/external-apis/cache/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(() => {})
  }

  useEffect(() => { fetchStats() }, [])

  const handleClear = async () => {
    setClearing(true)
    await fetch('/api/external-apis/cache', { method: 'DELETE' })
    setClearing(false)
    fetchStats()
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-3">
        <Database size={14} className="text-[var(--text-secondary)]" />
        <span className="text-sm font-semibold text-[var(--text-primary)]">Popularity Cache</span>
      </div>
      <p className="text-xs text-[var(--text-secondary)] mb-4">
        External API responses are cached for 24 hours to avoid rate limiting.
      </p>
      {stats && (
        <div className="flex gap-6 mb-4">
          <div>
            <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider">Live entries</div>
            <div className="text-2xl font-bold text-[var(--text-primary)] mt-0.5" style={{ fontFamily: 'Syne' }}>
              {stats.live_entries}
            </div>
          </div>
          <div>
            <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider">Expired</div>
            <div className="text-2xl font-bold text-[var(--text-secondary)] mt-0.5" style={{ fontFamily: 'Syne' }}>
              {stats.expired_entries}
            </div>
          </div>
        </div>
      )}
      <button
        onClick={handleClear}
        disabled={clearing}
        className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold
                   bg-[#f85149]/10 hover:bg-[#f85149]/20 border border-[#f85149]/30 text-[var(--danger)]
                   disabled:opacity-40 transition-all"
      >
        {clearing ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
        Clear Cache
      </button>
    </div>
  )
}

// ── Page root ─────────────────────────────────────────────────────────────────

export default function Settings() {
  const [status, setStatus] = useState(null)

  const fetchStatus = () => {
    fetch('/api/external-apis/status')
      .then(r => r.json())
      .then(setStatus)
      .catch(() => {})
  }

  useEffect(() => { fetchStatus() }, [])

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>
          Settings
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          External API credentials and scheduler configuration.
        </p>
      </div>

      <SpotifyCard status={status} onStatusChange={fetchStatus} />
      <LastFmCard status={status} onStatusChange={fetchStatus} />

      <NoKeyServiceCard
        title="MusicBrainz"
        color="#ba478f"
        icon="🎸"
        testUrl="/api/external-apis/musicbrainz/test"
        description="Open music encyclopedia. Used for artist metadata, genre tags, and release year data. No key required — rate limited to 1 request/sec."
        docsUrl="https://musicbrainz.org/doc/MusicBrainz_API"
        docsLabel="MusicBrainz API docs →"
      />

      <NoKeyServiceCard
        title="Billboard"
        color="#f5a623"
        icon="📊"
        testUrl="/api/external-apis/billboard/test"
        description="Billboard Hot 100 chart data. Used to boost popularity scores for trending mainstream tracks. No key required."
        docsUrl="https://www.billboard.com/charts/hot-100"
        docsLabel="Billboard Hot 100 →"
      />

      <WebhookSetupPanel />

      <AutomationPanel />

      <CachePanel />
    </div>
  )
}
