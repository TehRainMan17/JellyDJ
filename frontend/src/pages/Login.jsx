
import { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Loader2, AlertCircle, Wrench, ShieldCheck } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import logoUrl from '/logo-64.png'

export default function Login() {
  const { login, setupLogin } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [username, setUsername]     = useState('')
  const [password, setPassword]     = useState('')
  const [error, setError]           = useState('')
  const [loading, setLoading]       = useState(false)

  // Setup mode state
  const [setupStatus, setSetupStatus]       = useState(null)   // null = loading, then {setup_available, jellyfin_configured}
  const [setupMode, setSetupMode]           = useState(false)   // true = showing setup login form

  // Where to go after successful login (preserved redirect state)
  const from = location.state?.from?.pathname || '/'

  // Check setup status on mount
  useEffect(() => {
    fetch('/api/auth/setup-status')
      .then(r => r.json())
      .then(data => setSetupStatus(data))
      .catch(() => setSetupStatus({ setup_available: false, jellyfin_configured: false }))
  }, [])

  const handleSubmit = async () => {
    if (!username || !password) {
      setError('Please enter your username and password.')
      return
    }
    setLoading(true)
    setError('')
    try {
      if (setupMode) {
        await setupLogin(username, password)
      } else {
        await login(username, password)
      }
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.message || 'Login failed. Check your credentials.')
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSubmit()
  }

  const switchMode = (toSetup) => {
    setSetupMode(toSetup)
    setUsername('')
    setPassword('')
    setError('')
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--bg)' }}
    >
      {/* Glow backdrop */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 60% 40% at 50% 30%, rgba(83,236,252,0.06) 0%, transparent 70%)',
        }}
      />

      <div className="relative w-full max-w-sm">
        {/* Logo + brand */}
        <div className="flex flex-col items-center mb-8 gap-3">
          <div className="relative">
            <img
              src={logoUrl}
              alt="JellyDJ"
              width={72}
              height={72}
              className="anim-glow"
              style={{ borderRadius: '50%' }}
            />
          </div>
          <div style={{ textAlign: 'center' }}>
            <span style={{
              fontFamily: 'Syne', fontWeight: 800, fontSize: 28, letterSpacing: '-0.02em',
              display: 'block',
            }}>
              <span style={{
                background: 'linear-gradient(90deg, #5be6f5 0%, #9b5de5 100%)',
                WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
              }}>Jelly</span>
              <span style={{
                background: 'linear-gradient(90deg, #9b5de5 0%, #b44fff 100%)',
                WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
              }}>DJ</span>
            </span>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
              {setupMode ? 'First-time setup' : 'Sign in to continue'}
            </p>
          </div>
        </div>

        {/* Setup mode banner */}
        {setupMode && (
          <div
            className="flex items-start gap-2.5 px-3 py-3 rounded-xl text-sm mb-4 anim-scale-in"
            style={{
              background: 'rgba(91,230,245,0.07)',
              border: '1px solid rgba(91,230,245,0.25)',
              color: 'var(--text-primary)',
            }}
          >
            <ShieldCheck size={15} style={{ flexShrink: 0, marginTop: 1, color: '#5be6f5' }} />
            <span style={{ color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5 }}>
              You're using the <strong style={{ color: 'var(--text-primary)' }}>setup account</strong> to
              configure JellyDJ for the first time. Connect Jellyfin on the{' '}
              <strong style={{ color: 'var(--text-primary)' }}>Connections</strong> page, then sign in with
              your Jellyfin account. Remove <code style={{ fontSize: 11 }}>SETUP_USERNAME</code> and{' '}
              <code style={{ fontSize: 11 }}>SETUP_PASSWORD</code> from your <code style={{ fontSize: 11 }}>.env</code>{' '}
              when done.
            </span>
          </div>
        )}

        {/* Card */}
        <div
          className="card space-y-4"
          style={{ padding: '1.75rem' }}
        >
          {/* Username */}
          <div className="space-y-1.5">
            <label
              htmlFor="username"
              className="section-label"
            >
              {setupMode ? 'Setup Username' : 'Username'}
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={setupMode ? 'admin' : 'Jellyfin username'}
              className="input"
            />
          </div>

          {/* Password */}
          <div className="space-y-1.5">
            <label
              htmlFor="password"
              className="section-label"
            >
              {setupMode ? 'Setup Password' : 'Password'}
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="••••••••"
              className="input"
            />
          </div>

          {/* Error */}
          {error && (
            <div
              className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl text-sm anim-scale-in"
              style={{
                background: 'rgba(248,113,113,0.08)',
                border: '1px solid rgba(248,113,113,0.25)',
                color: 'var(--danger)',
              }}
            >
              <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{error}</span>
            </div>
          )}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="btn-primary w-full mt-2"
            style={{ height: 42 }}
          >
            {loading
              ? <><Loader2 size={15} className="animate-spin" /> Signing in…</>
              : setupMode ? 'Enter Setup' : 'Sign in'
            }
          </button>
        </div>

        {/* Setup mode toggle — only shown when setup is available */}
        {setupStatus?.setup_available && (
          <div className="mt-4 text-center">
            {setupMode ? (
              <button
                onClick={() => switchMode(false)}
                className="text-xs"
                style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
              >
                ← Back to Jellyfin login
              </button>
            ) : (
              <button
                onClick={() => switchMode(true)}
                className="flex items-center gap-1.5 mx-auto text-xs"
                style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
              >
                <Wrench size={12} />
                First time setup? Use setup account
              </button>
            )}
          </div>
        )}

        <p
          className="text-center mt-6 text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          Self-hosted · All data stays local
        </p>
      </div>
    </div>
  )
}
