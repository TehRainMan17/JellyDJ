/**
 * useJellyfinUrl.js
 *
 * Fetches the Jellyfin connection settings once per session and exposes
 * helpers for building browser deep-links into the Jellyfin web client.
 *
 * URL resolution order for links:
 *   1. public_url  — if set, use this (your internet-facing DNS address)
 *   2. base_url    — fallback to the LAN/internal address
 *
 * The backend always uses base_url for its own API calls; public_url is
 * only ever returned to the browser and never used server-side.
 *
 * Deep-link format:  {resolvedBase}/web/index.html#!/details?id={itemId}
 */
import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'

// Module-level cache — one fetch shared across all component instances.
let _cachedBase = null    // resolved URL string (never null after first fetch)
let _fetching   = false
let _listeners  = []

function subscribe(fn) {
  _listeners.push(fn)
  return () => { _listeners = _listeners.filter(l => l !== fn) }
}

function notify(url) {
  _cachedBase = url
  _fetching   = false
  _listeners.forEach(fn => fn(url))
}

function ensureFetch() {
  if (_cachedBase !== null || _fetching) return
  _fetching = true
  api.get('/api/connections/jellyfin')
    .then(data => {
      // Prefer the public URL when it has been configured; fall back to the
      // internal base URL that the backend uses for its own API calls.
      const pub      = (data.public_url || '').trim().replace(/\/$/, '')
      const internal = (data.base_url   || '').trim().replace(/\/$/, '')
      notify(pub || internal)
    })
    .catch(() => notify(''))
}

/**
 * Hook — returns { jellyfinUrl, buildItemUrl, buildSearchUrl }
 *
 * jellyfinUrl      — the resolved base URL (public if configured, else internal)
 * buildItemUrl(id) — deep-link to a specific Jellyfin item by ID
 * buildSearchUrl(q)— opens the Jellyfin web search for a query string
 */
export function useJellyfinUrl() {
  const [baseUrl, setBaseUrl] = useState(_cachedBase ?? '')

  useEffect(() => {
    if (_cachedBase !== null) return   // already resolved
    ensureFetch()
    const unsub = subscribe(setBaseUrl)
    return unsub
  }, [])

  const buildItemUrl = (itemId) => {
    if (!baseUrl || !itemId) return null
    return `${baseUrl}/web/index.html#!/details?id=${itemId}`
  }

  const buildSearchUrl = (query) => {
    if (!baseUrl || !query) return null
    return `${baseUrl}/web/index.html#!/search?query=${encodeURIComponent(query)}`
  }

  return { jellyfinUrl: baseUrl, buildItemUrl, buildSearchUrl }
}

/**
 * Call this from any component that knows the user just saved new connection
 * settings, so the next useJellyfinUrl() call re-fetches rather than serving
 * a stale cached value.
 */
export function invalidateJellyfinUrlCache() {
  _cachedBase = null
  _fetching   = false
}
