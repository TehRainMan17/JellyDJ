import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Loader2, AlertCircle } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext.jsx'
import logoUrl from '/logo-64.png'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  // Where to go after successful login (preserved redirect state)
  const from = location.state?.from?.pathname || '/'

  const handleSubmit = async () => {
    if (!username || !password) {
      setError('Please enter your username and password.')
      return
    }
    setLoading(true)
    setError('')
    try {
      await login(username, password)
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
              Sign in to continue
            </p>
          </div>
        </div>

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
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="admin"
              className="input"
            />
          </div>

          {/* Password */}
          <div className="space-y-1.5">
            <label
              htmlFor="password"
              className="section-label"
            >
              Password
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
              : 'Sign in'
            }
          </button>
        </div>

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
