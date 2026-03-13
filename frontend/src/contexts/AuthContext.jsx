
import { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react'

const AuthContext = createContext(null)

const REFRESH_KEY = 'jellydj_refresh_token'

function decodeJwtPayload(token) {
  try {
    const [, payloadB64] = token.split('.')
    const padded = payloadB64.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(padded)
    return JSON.parse(json)
  } catch {
    return null
  }
}

export function AuthProvider({ children }) {
  const [accessToken, setAccessToken] = useState(null)
  const [user, setUser] = useState(null)        // { user_id, username, is_admin }
  const [loading, setLoading] = useState(true)  // true during initial silent refresh

  const refreshTimerRef = useRef(null)

  // ── Silent refresh timer ──────────────────────────────────────────────────
  const scheduleRefresh = useCallback((token) => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    const payload = decodeJwtPayload(token)
    if (!payload?.exp) return
    const msUntilExpiry = payload.exp * 1000 - Date.now()
    const msUntilRefresh = msUntilExpiry - 2 * 60 * 1000 // 2 min before expiry
    if (msUntilRefresh > 0) {
      refreshTimerRef.current = setTimeout(() => { refresh() }, msUntilRefresh)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Store new access token + schedule refresh ────────────────────────────
  const applyAccessToken = useCallback((token) => {
    const payload = decodeJwtPayload(token)
    setAccessToken(token)
    setUser(payload ? {
      user_id:  payload.sub ?? payload.user_id,
      username: payload.username,
      is_admin: payload.is_admin ?? false,
    } : null)
    scheduleRefresh(token)
  }, [scheduleRefresh])

  // ── Refresh ───────────────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    const storedRefresh = sessionStorage.getItem(REFRESH_KEY)
    if (!storedRefresh) throw new Error('No refresh token')

    const resp = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: storedRefresh }),
    })

    if (!resp.ok) throw new Error('Refresh failed')

    const data = await resp.json()
    applyAccessToken(data.access_token)
    if (data.refresh_token) {
      sessionStorage.setItem(REFRESH_KEY, data.refresh_token)
    }
    return data.access_token
  }, [applyAccessToken])

  // ── Setup Login (no refresh token — setup sessions are short-lived only) ──
  const setupLogin = useCallback(async (username, password) => {
    const resp = await fetch('/api/auth/setup-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      const detail = err.detail
      const msg = typeof detail === 'string' ? detail : (err.error || 'Setup login failed')
      throw new Error(msg)
    }
    const data = await resp.json()
    // No refresh token for setup sessions — access token only
    applyAccessToken(data.access_token)
    // Do NOT store a refresh token
  }, [applyAccessToken])
  const login = useCallback(async (username, password) => {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      const detail = err.detail
      const msg = typeof detail === 'string' ? detail : (err.error || 'Login failed')
      throw new Error(msg)
    }
    const data = await resp.json()
    applyAccessToken(data.access_token)
    sessionStorage.setItem(REFRESH_KEY, data.refresh_token)
    const payload = decodeJwtPayload(data.access_token)
    return payload ? {
      user_id:  payload.sub ?? payload.user_id,
      username: payload.username,
      is_admin: payload.is_admin ?? false,
    } : null
  }, [applyAccessToken])

  // ── Logout ────────────────────────────────────────────────────────────────
  const logout = useCallback(async () => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      })
    } catch { /* best-effort */ }
    setAccessToken(null)
    setUser(null)
    sessionStorage.removeItem(REFRESH_KEY)
  }, [accessToken])

  // ── On mount: attempt silent refresh to restore session ──────────────────
  useEffect(() => {
    async function init() {
      const stored = sessionStorage.getItem(REFRESH_KEY)
      if (stored) {
        try {
          await refresh()
        } catch {
          sessionStorage.removeItem(REFRESH_KEY)
          setAccessToken(null)
          setUser(null)
        }
      }
      setLoading(false)
    }
    init()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cleanup timer on unmount ──────────────────────────────────────────────
  useEffect(() => {
    return () => { if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current) }
  }, [])

  const value = {
    user,
    isAdmin:         user?.is_admin ?? false,
    isAuthenticated: !!accessToken,
    accessToken,
    login,
    setupLogin,
    logout,
    refresh,
    loading,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
