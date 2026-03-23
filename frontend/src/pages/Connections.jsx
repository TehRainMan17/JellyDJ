import { useState, useEffect, useCallback } from 'react'
import WebhookSetupPanel from '../components/WebhookSetupPanel.jsx'
import { api } from '../lib/api.js'
import { invalidateJellyfinUrlCache } from '../hooks/useJellyfinUrl.js'
import {
  Plug, CheckCircle2, XCircle, Loader2, Eye, EyeOff,
  RefreshCw, Users, ChevronDown, ChevronUp, Save, Trash2,
  AlertCircle, ShieldCheck, Globe,
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

// ── Tracked Users Panel ───────────────────────────────────────────────────────

function TrackedUsersPanel() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState({})
  const [confirmId, setConfirmId] = useState(null)
  const [error, setError] = useState('')

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.get('/api/connections/jellyfin/users/tracked')
      setUsers(data)
    } catch {
      setError('Could not load tracked users.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const handleAdd = async (userId, username) => {
    setDeleting(p => ({ ...p, [userId]: true }))
    try {
      await api.post(`/api/connections/jellyfin/users/add-test-user?jellyfin_user_id=${userId}&username=${username}`, {})
      await fetchUsers()
    } catch (err) {
      setError(`Failed to add user: ${err.message}`)
    } finally {
      setDeleting(p => ({ ...p, [userId]: false }))
    }
  }

  const handleDelete = async (userId) => {
    setDeleting(p => ({ ...p, [userId]: true }))
    try {
      await api.delete(`/api/connections/jellyfin/users/${userId}`)
      await fetchUsers()
    } finally {
      setDeleting(p => ({ ...p, [userId]: false }))
      setConfirmId(null)
    }
  }

  if (loading)
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] mt-4">
        <Loader2 size={13} className="animate-spin" /> Loading tracked users…
      </div>
    )

  if (error)
    return <p className="text-xs text-[var(--danger)] mt-4">{error}</p>

  return (
    <div className="mt-4 space-y-1">
      <p className="text-xs text-[var(--text-secondary)] mb-3">
        <strong>Jellyfin Users:</strong> Below are all your Jellyfin users.
        Green checkmarks indicate active JellyDJ users. Click "Add" to manually activate a user,
        or "Remove" to delete their JellyDJ data.
      </p>

      {users.length === 0 ? (
        <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] py-2">
          <AlertCircle size={13} className="text-[var(--text-secondary)]" />
          No Jellyfin users found. Check your Jellyfin connection.
        </div>
      ) : (
        users.map(user => (
          <div
            key={user.jellyfin_user_id}
            className={`flex items-center justify-between py-2 px-3 rounded-lg transition-colors ${
              user.has_activated
                ? 'bg-[rgba(74,222,128,0.08)] border border-[rgba(74,222,128,0.3)]'
                : 'hover:bg-[var(--bg-overlay)]'
            }`}
          >
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-full bg-[var(--bg-overlay)] border border-[var(--border)] flex items-center justify-center text-xs font-semibold text-[var(--text-secondary)]">
                {user.jellyfin_username[0]?.toUpperCase()}
              </div>
              <div>
                <div className="flex items-center gap-1.5">
                  <span className="text-sm text-[var(--text-primary)]">{user.jellyfin_username}</span>
                  {user.has_activated && (
                    <span className="flex items-center gap-0.5 text-[10px] text-[#4ade80] font-semibold">
                      <ShieldCheck size={10} /> Active
                    </span>
                  )}
                  {user.is_admin && (
                    <span className="flex items-center gap-0.5 text-[10px] text-[var(--accent)] font-semibold">
                      <ShieldCheck size={10} /> Admin
                    </span>
                  )}
                </div>
                {user.last_login_at && (
                  <div className="text-[10px] text-[var(--text-secondary)]">
                    Last login: {new Date(user.last_login_at).toLocaleDateString()}
                  </div>
                )}
              </div>
            </div>

            {confirmId === user.jellyfin_user_id ? (
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-[var(--danger)]">Delete all data?</span>
                <button
                  onClick={() => handleDelete(user.jellyfin_user_id)}
                  disabled={!!deleting[user.jellyfin_user_id]}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold
                             bg-[var(--danger)]/10 border border-[var(--danger)]/25
                             text-[var(--danger)] hover:bg-[var(--danger)]/20 transition-colors disabled:opacity-40"
                >
                  {deleting[user.jellyfin_user_id] ? <Loader2 size={10} className="animate-spin" /> : null}
                  Confirm
                </button>
                <button
                  onClick={() => setConfirmId(null)}
                  className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                {user.has_activated ? (
                  <button
                    onClick={() => setConfirmId(user.jellyfin_user_id)}
                    title="Delete all JellyDJ data for this user"
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium
                               text-[var(--text-secondary)] hover:text-[var(--danger)]
                               hover:bg-[var(--danger)]/8 border border-transparent
                               hover:border-[var(--danger)]/20 transition-all"
                  >
                    <Trash2 size={12} />
                    Remove
                  </button>
                ) : (
                  <button
                    onClick={() => handleAdd(user.jellyfin_user_id, user.jellyfin_username)}
                    disabled={!!deleting[user.jellyfin_user_id]}
                    title="Manually activate this user for JellyDJ"
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium
                               bg-[#6366f1]/10 border border-[#6366f1]/25
                               text-[#6366f1] hover:bg-[#6366f1]/20 transition-all disabled:opacity-40"
                  >
                    {deleting[user.jellyfin_user_id] ? <Loader2 size={10} className="animate-spin" /> : null}
                    Add to JellyDJ
                  </button>
                )}
              </div>
            )}
          </div>
        ))
      )}

      <div className="pt-2">
        <button
          onClick={fetchUsers}
          className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
        >
          <RefreshCw size={11} /> Refresh
        </button>
      </div>
    </div>
  )
}

// ── Jellyfin Card ─────────────────────────────────────────────────────────────

function JellyfinCard() {
  const [url, setUrl]               = useState('')
  const [publicUrl, setPublicUrl]   = useState('')
  const [apiKey, setApiKey]         = useState('')
  const [hasStoredKey, setHasStoredKey] = useState(false)
  const [status, setStatus]         = useState('idle')
  const [lastTested, setLastTested] = useState(null)
  const [saving, setSaving]         = useState(false)
  const [msg, setMsg]               = useState({ text: '', isError: false })
  const [showUsers, setShowUsers]   = useState(false)

  useEffect(() => {
    api.get('/api/connections/jellyfin')
      .then(data => {
        setUrl(data.base_url || '')
        setPublicUrl(data.public_url || '')
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
      await api.post('/api/connections/jellyfin', {
        base_url: url,
        api_key: apiKey,
        public_url: publicUrl,
      })
      showMsg('Credentials saved.')
      setHasStoredKey(true)
      setStatus('idle')
      // Bust the deep-link URL cache so Insights/Playlists pick up the change
      invalidateJellyfinUrlCache()
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setStatus('testing')
    setMsg({ text: '', isError: false })
    try {
      const data = await api.post('/api/connections/jellyfin/test')
      if (data) {
        setStatus('connected')
        showMsg(data.message || 'Connected successfully.')
        setLastTested(new Date())
      }
    } catch (err) {
      setStatus('error')
      showMsg(err.message || 'Connection failed.', true)
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
        {/* Internal / LAN base URL — used by the backend for API calls */}
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
          <p className="text-[11px] text-[var(--text-secondary)] mt-1.5">
            Your LAN or Docker address — used by the JellyDJ backend for all API calls.
          </p>
        </div>

        {/* Public / DNS URL — optional, browser-only deep-links */}
        <div>
          <FieldLabel>
            <span className="flex items-center gap-1.5">
              <Globe size={11} />
              Public URL
              <span className="normal-case font-normal text-[var(--text-muted)]">(optional)</span>
            </span>
          </FieldLabel>
          <input
            type="url"
            value={publicUrl}
            onChange={e => setPublicUrl(e.target.value)}
            placeholder="https://jellyfin.yourdomain.com"
            className="w-full bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2
                       text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
                       focus:outline-none focus:border-[var(--accent)]/60 focus:ring-1 focus:ring-[var(--accent)]/20
                       transition-colors"
          />
          <p className="text-[11px] text-[var(--text-secondary)] mt-1.5">
            Your internet-facing DNS address. When set, deep-links in Insights and Playlists
            will use this URL instead of the Base URL above — so clicking "Open in Jellyfin"
            works from outside your LAN. The backend never sends requests to this address.
          </p>
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
            JellyDJ Users
            {showUsers
              ? <ChevronUp size={13} className="ml-auto" />
              : <ChevronDown size={13} className="ml-auto" />}
          </button>
          {showUsers && <TrackedUsersPanel />}
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
    api.get('/api/connections/lidarr')
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
      await api.post('/api/connections/lidarr', { base_url: url, api_key: apiKey })
      showMsg('Credentials saved.')
      setHasStoredKey(true)
      setStatus('idle')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setStatus('testing')
    setMsg({ text: '', isError: false })
    try {
      const data = await api.post('/api/connections/lidarr/test')
      if (data) {
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

// ── External API shared helpers ───────────────────────────────────────────────

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
      await api.post('/api/external-apis/spotify', { client_id: clientId, client_secret: clientSecret })
      if (true) {
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
      const data = await api.post('/api/external-apis/spotify/test')
      setResult({ ok: true, message: data.message || 'Test passed.' })
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
      await api.post('/api/external-apis/lastfm', { api_key: apiKey, api_secret: apiSecret })
      if (true) {
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
      const data = await api.post('/api/external-apis/lastfm/test')
      setResult({ ok: true, message: data.message || 'Test passed.' })
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
      const data = await api.post(testUrl)
      setResult({ ok: true, message: data.message || 'Test passed.' })
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

// ── Page root ─────────────────────────────────────────────────────────────────

export default function Connections() {
  const [extStatus, setExtStatus] = useState(null)

  const fetchExtStatus = () => {
    api.get('/api/external-apis/status')
      .then(setExtStatus)
      .catch(() => {})
  }

  useEffect(() => { fetchExtStatus() }, [])

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>
          Connections
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Configure your Jellyfin, Lidarr, and external API connections. Credentials are encrypted at rest.
        </p>
      </div>

      <JellyfinCard />
      <LidarrCard />

      <SpotifyCard status={extStatus} onStatusChange={fetchExtStatus} />
      <LastFmCard status={extStatus} onStatusChange={fetchExtStatus} />

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
    </div>
  )
}
