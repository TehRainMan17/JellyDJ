
import { useState, useEffect, useCallback } from 'react'
import {
  Plug, CheckCircle2, XCircle, Loader2, Eye, EyeOff,
  RefreshCw, Users, ChevronDown, ChevronUp, Save,
} from 'lucide-react'

// ── Shared sub-components ─────────────────────────────────────────────────────

function StatusBadge({ status }) {
  if (status === 'connected')
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--accent)]">
        <CheckCircle2 size={13} /> Connected
      </span>
    )
  if (status === 'error')
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--danger)]">
        <XCircle size={13} /> Failed
      </span>
    )
  if (status === 'testing')
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--warning)]">
        <Loader2 size={13} className="animate-spin" /> Testing…
      </span>
    )
  return (
    <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
      <div className="w-2 h-2 rounded-full bg-[var(--border)]" /> Not tested
    </span>
  )
}

function ApiKeyInput({ value, onChange, placeholder }) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 pr-10
                   text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] font-mono
                   focus:outline-none focus:border-[var(--accent)]/60 focus:ring-1 focus:ring-[var(--accent)]/20
                   transition-colors"
      />
      <button
        type="button"
        onClick={() => setShow(v => !v)}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        tabIndex={-1}
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  )
}

function FieldLabel({ children }) {
  return (
    <label className="block text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-1.5">
      {children}
    </label>
  )
}

function ActionButton({ onClick, disabled, loading, variant = 'primary', children }) {
  const base = "flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-semibold transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
  const variants = {
    primary: "bg-[var(--accent)] hover:bg-[#00c49c] text-[var(--bg)]",
    ghost:   "bg-[var(--bg-overlay)] hover:bg-[#2d333b] border border-[var(--border)] text-[var(--text-primary)]",
  }
  return (
    <button onClick={onClick} disabled={disabled || loading} className={`${base} ${variants[variant]}`}>
      {loading && <Loader2 size={13} className="animate-spin" />}
      {children}
    </button>
  )
}

// ── Managed Users Panel ───────────────────────────────────────────────────────

function ManagedUsersPanel() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState({})
  const [error, setError] = useState('')

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/connections/jellyfin/users')
      if (!r.ok) throw new Error('Failed')
      setUsers(await r.json())
    } catch {
      setError('Could not load users. Is Jellyfin connected?')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const toggle = async (user) => {
    setToggling(p => ({ ...p, [user.jellyfin_user_id]: true }))
    try {
      await fetch('/api/connections/jellyfin/users/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jellyfin_user_id: user.jellyfin_user_id,
          username: user.username,
          is_enabled: !user.is_enabled,
        }),
      })
      setUsers(prev => prev.map(u =>
        u.jellyfin_user_id === user.jellyfin_user_id
          ? { ...u, is_enabled: !u.is_enabled }
          : u
      ))
    } finally {
      setToggling(p => ({ ...p, [user.jellyfin_user_id]: false }))
    }
  }

  if (loading)
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] mt-4">
        <Loader2 size={13} className="animate-spin" /> Fetching users…
      </div>
    )

  if (error)
    return <p className="text-xs text-[var(--danger)] mt-4">{error}</p>

  if (users.length === 0)
    return <p className="text-xs text-[var(--text-secondary)] mt-4">No users found in Jellyfin.</p>

  return (
    <div className="mt-4 space-y-1">
      <p className="text-xs text-[var(--text-secondary)] mb-3">
        Toggle which users JellyDJ should track and generate playlists for.
      </p>
      {users.map(user => (
        <div
          key={user.jellyfin_user_id}
          className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-[var(--bg-overlay)] transition-colors"
        >
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-[var(--bg-overlay)] border border-[var(--border)] flex items-center justify-center text-xs font-semibold text-[var(--text-secondary)]">
              {user.username[0]?.toUpperCase()}
            </div>
            <span className="text-sm text-[var(--text-primary)]">{user.username}</span>
          </div>
          <button
            onClick={() => toggle(user)}
            disabled={!!toggling[user.jellyfin_user_id]}
            className={`relative w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0
              ${user.is_enabled ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'}
              disabled:opacity-50`}
          >
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200
                ${user.is_enabled ? 'translate-x-5' : 'translate-x-0.5'}`}
            />
          </button>
        </div>
      ))}
      <div className="pt-2">
        <button
          onClick={fetchUsers}
          className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        >
          <RefreshCw size={11} /> Refresh users
        </button>
      </div>
    </div>
  )
}

// ── Jellyfin Card ─────────────────────────────────────────────────────────────

function JellyfinCard() {
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [hasStoredKey, setHasStoredKey] = useState(false)
  const [status, setStatus] = useState('idle')
  const [lastTested, setLastTested] = useState(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState({ text: '', isError: false })
  const [showUsers, setShowUsers] = useState(false)

  useEffect(() => {
    fetch('/api/connections/jellyfin')
      .then(r => r.json())
      .then(data => {
        setUrl(data.base_url || '')
        setHasStoredKey(data.has_api_key)
        setStatus(data.is_connected ? 'connected' : 'idle')
        if (data.last_tested) setLastTested(new Date(data.last_tested))
      })
      .catch(() => {})
  }, [])

  const showMsg = (text, isError = false) => {
    setMsg({ text, isError })
    setTimeout(() => setMsg({ text: '', isError: false }), 10000)
  }

  const handleSave = async () => {
    if (!url) { showMsg('Enter a Base URL.', true); return }
    if (!apiKey) { showMsg('Enter an API key.', true); return }
    setSaving(true)
    try {
      const r = await fetch('/api/connections/jellyfin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: url, api_key: apiKey }),
      })
      if (r.ok) {
        showMsg('Credentials saved.')
        setHasStoredKey(true)
        setStatus('idle')
      } else {
        showMsg('Save failed.', true)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setStatus('testing')
    setMsg({ text: '', isError: false })
    try {
      const r = await fetch('/api/connections/jellyfin/test', { method: 'POST' })
      const data = await r.json()
      if (r.ok) {
        setStatus('connected')
        showMsg(data.message || 'Connected successfully.')
        setLastTested(new Date())
      } else {
        setStatus('error')
        showMsg(data.detail || 'Connection failed.', true)
      }
    } catch {
      setStatus('error')
      showMsg('Network error — is the backend running?', true)
    }
  }

  return (
    <div className="card space-y-5">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-[#00a4dc]/10 border border-[#00a4dc]/20 flex items-center justify-center flex-shrink-0">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <ellipse cx="12" cy="8" rx="7" ry="6" fill="#00a4dc" opacity=".9"/>
              <path d="M5 10 Q6 18 8 20 Q10 22 12 22 Q14 22 16 20 Q18 18 19 10" fill="#00a4dc" opacity=".6"/>
              <path d="M8 14 Q9 19 10 21" stroke="var(--bg)" strokeWidth="1" strokeLinecap="round" opacity=".4"/>
              <path d="M16 14 Q15 19 14 21" stroke="var(--bg)" strokeWidth="1" strokeLinecap="round" opacity=".4"/>
            </svg>
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">Jellyfin</div>
            <div className="text-xs text-[var(--text-secondary)] mt-0.5">Media server connection</div>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="space-y-4">
        <div>
          <FieldLabel>Base URL</FieldLabel>
          <input
            type="url"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="http://192.168.1.100:8096"
            className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2
                       text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
                       focus:outline-none focus:border-[var(--accent)]/60 focus:ring-1 focus:ring-[var(--accent)]/20
                       transition-colors"
          />
        </div>
        <div>
          <FieldLabel>API Key</FieldLabel>
          <ApiKeyInput
            value={apiKey}
            onChange={setApiKey}
            placeholder={hasStoredKey ? 'Key stored — paste new key to change' : 'Paste your Jellyfin API key'}
          />
          <p className="text-[11px] text-[var(--text-secondary)] mt-1.5">
            Dashboard → Administration → API Keys → + (add new key)
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap pt-1">
        <ActionButton onClick={handleSave} loading={saving} variant="ghost">
          <Save size={13} />
          Save
        </ActionButton>
        <ActionButton
          onClick={handleTest}
          loading={status === 'testing'}
          disabled={!hasStoredKey && !apiKey}
          variant="primary"
        >
          <Plug size={13} />
          Test Connection
        </ActionButton>
        {msg.text && (
          <span className={`text-xs ${msg.isError ? 'text-[var(--danger)]' : 'text-[var(--accent)]'}`}>
            {msg.text}
          </span>
        )}
      </div>

      {lastTested && (
        <p className="text-[11px] text-[var(--text-secondary)]">
          Last tested: {lastTested.toLocaleString()}
        </p>
      )}

      {status === 'connected' && (
        <div className="border-t border-[var(--border)] pt-4">
          <button
            onClick={() => setShowUsers(v => !v)}
            className="flex items-center gap-2 text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors w-full"
          >
            <Users size={14} />
            Managed Users
            {showUsers
              ? <ChevronUp size={13} className="ml-auto" />
              : <ChevronDown size={13} className="ml-auto" />}
          </button>
          {showUsers && <ManagedUsersPanel />}
        </div>
      )}
    </div>
  )
}

// ── Lidarr Card ───────────────────────────────────────────────────────────────

function LidarrCard() {
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [hasStoredKey, setHasStoredKey] = useState(false)
  const [status, setStatus] = useState('idle')
  const [lastTested, setLastTested] = useState(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState({ text: '', isError: false })

  useEffect(() => {
    fetch('/api/connections/lidarr')
      .then(r => r.json())
      .then(data => {
        setUrl(data.base_url || '')
        setHasStoredKey(data.has_api_key)
        setStatus(data.is_connected ? 'connected' : 'idle')
        if (data.last_tested) setLastTested(new Date(data.last_tested))
      })
      .catch(() => {})
  }, [])

  const showMsg = (text, isError = false) => {
    setMsg({ text, isError })
    setTimeout(() => setMsg({ text: '', isError: false }), 10000)
  }

  const handleSave = async () => {
    if (!url) { showMsg('Enter a Base URL.', true); return }
    if (!apiKey) { showMsg('Enter an API key.', true); return }
    setSaving(true)
    try {
      const r = await fetch('/api/connections/lidarr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: url, api_key: apiKey }),
      })
      if (r.ok) {
        showMsg('Credentials saved.')
        setHasStoredKey(true)
        setStatus('idle')
      } else {
        showMsg('Save failed.', true)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setStatus('testing')
    setMsg({ text: '', isError: false })
    try {
      const r = await fetch('/api/connections/lidarr/test', { method: 'POST' })
      const data = await r.json()
      if (r.ok) {
        setStatus('connected')
        showMsg(data.message || 'Connected successfully.')
        setLastTested(new Date())
      } else {
        setStatus('error')
        showMsg(data.detail || 'Connection failed.', true)
      }
    } catch {
      setStatus('error')
      showMsg('Network error — is the backend running?', true)
    }
  }

  return (
    <div className="card space-y-5">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-[#f5a623]/10 border border-[#f5a623]/20 flex items-center justify-center flex-shrink-0">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" fill="#f5a623" opacity=".85"/>
              <circle cx="12" cy="12" r="4" fill="none" stroke="var(--bg)" strokeWidth="1.5"/>
              <circle cx="12" cy="12" r="1.5" fill="var(--bg)"/>
              <path d="M12 3 L12 7M12 17 L12 21M3 12 L7 12M17 12 L21 12" stroke="var(--bg)" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">Lidarr</div>
            <div className="text-xs text-[var(--text-secondary)] mt-0.5">Music download manager</div>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="space-y-4">
        <div>
          <FieldLabel>Base URL</FieldLabel>
          <input
            type="url"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="http://192.168.1.100:8686"
            className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2
                       text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
                       focus:outline-none focus:border-[var(--accent)]/60 focus:ring-1 focus:ring-[var(--accent)]/20
                       transition-colors"
          />
        </div>
        <div>
          <FieldLabel>API Key</FieldLabel>
          <ApiKeyInput
            value={apiKey}
            onChange={setApiKey}
            placeholder={hasStoredKey ? 'Key stored — paste new key to change' : 'Paste your Lidarr API key'}
          />
          <p className="text-[11px] text-[var(--text-secondary)] mt-1.5">
            Lidarr → Settings → General → Security → API Key
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap pt-1">
        <ActionButton onClick={handleSave} loading={saving} variant="ghost">
          <Save size={13} />
          Save
        </ActionButton>
        <ActionButton
          onClick={handleTest}
          loading={status === 'testing'}
          disabled={!hasStoredKey && !apiKey}
          variant="primary"
        >
          <Plug size={13} />
          Test Connection
        </ActionButton>
        {msg.text && (
          <span className={`text-xs ${msg.isError ? 'text-[var(--danger)]' : 'text-[var(--accent)]'}`}>
            {msg.text}
          </span>
        )}
      </div>

      {lastTested && (
        <p className="text-[11px] text-[var(--text-secondary)]">
          Last tested: {lastTested.toLocaleString()}
        </p>
      )}
    </div>
  )
}

// ── Page root ─────────────────────────────────────────────────────────────────

export default function Connections() {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>
          Connections
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Configure your Jellyfin and Lidarr connections. Credentials are encrypted at rest.
        </p>
      </div>

      <JellyfinCard />
      <LidarrCard />
    </div>
  )
}
