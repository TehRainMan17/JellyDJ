
/**
 * api.js — Centralized fetch wrapper for JellyDJ.
 *
 * Attaches Authorization: Bearer {accessToken} to every request.
 * On 401: attempts one silent token refresh, retries the request.
 * If refresh also fails: calls logout() and throws.
 *
 * Usage:
 *   import { api } from '../lib/api.js'
 *   const data = await api.get('/api/user-playlists')
 *   const data = await api.post('/api/auth/login', { username, password })
 */

// We need access to the auth context from outside React.
// We do this via a small module-level store that AuthProvider populates.
let _getToken = () => null
let _refresh = async () => { throw new Error('no refresh') }
let _logout = async () => {}

/** Called once by AuthProvider to wire up the auth callbacks. */
export function _wireAuth({ getToken, refresh, logout }) {
  _getToken = getToken
  _refresh = refresh
  _logout = logout
}

async function request(method, path, body) {
  const doFetch = (token) => {
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    return fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  let token = _getToken()
  let resp = await doFetch(token)

  // 401 → try one refresh + retry
  if (resp.status === 401) {
    try {
      token = await _refresh()
      resp = await doFetch(token)
    } catch {
      await _logout()
      throw new Error('Session expired — please log in again.')
    }
  }

  if (!resp.ok) {
    let errMsg = `Request failed: ${resp.status}`
    try {
      const errBody = await resp.json()
      errMsg = errBody.detail || errBody.message || errMsg
    } catch { /* ignore */ }
    throw new Error(errMsg)
  }

  // Some endpoints return 204 No Content
  if (resp.status === 204) return null

  return resp.json()
}

export const api = {
  get:    (path)               => request('GET',    path),
  post:   (path, body)         => request('POST',   path, body),
  put:    (path, body)         => request('PUT',    path, body),
  delete: (path)               => request('DELETE', path),
}
