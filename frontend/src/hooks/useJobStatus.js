/**
 * useJobStatus — polls all background jobs and exposes their live state.
 *
 * Five background processes on the server, each with its own status endpoint:
 *   1. Indexer          → /api/indexer/job-status
 *   2. Popularity cache → /api/automation/trigger/popularity-cache/status
 *   3. Enrichment       → /api/automation/trigger/enrichment/status
 *   4. Discovery        → /api/automation/trigger/discovery/status
 *   5. Auto-download    → /api/automation/trigger/auto-download/status
 *
 * Key behaviours:
 *   - Polling starts immediately on mount — any already-running job appears
 *     without a button click.
 *   - Adaptive interval: 2s while any job is running, 30s when all idle.
 *     This prevents a continuous burst of HTTP requests against the backend
 *     (and Jellyfin, which shares the same host) when the user just has a
 *     tab open and nothing is happening.
 *   - startPolling() snaps back to fast polling immediately (call after
 *     manually triggering a job).
 *   - onComplete fires once when the INDEX job transitions running → false.
 */
import { useState, useRef, useCallback, useEffect } from 'react'
import { apiFetch } from '../lib/api'

const URLS = {
  index:    '/api/indexer/job-status',
  cache:    '/api/automation/trigger/popularity-cache/status',
  enrich:   '/api/automation/trigger/enrichment/status',
  discover: '/api/automation/trigger/discovery/status',
  download: '/api/automation/trigger/auto-download/status',
}

const INTERVAL_ACTIVE_MS = 2000   // a job is running — keep the progress bar snappy
const INTERVAL_IDLE_MS   = 30000  // nothing running — no need to hammer the server

export function useJobStatus(onComplete) {
  const [indexStatus,    setIndexStatus]    = useState(null)
  const [cacheStatus,    setCacheStatus]    = useState(null)
  const [enrichStatus,   setEnrichStatus]   = useState(null)
  const [discoverStatus, setDiscoverStatus] = useState(null)
  const [downloadStatus, setDownloadStatus] = useState(null)

  const timerRef        = useRef(null)
  const indexWasRunning = useRef(false)
  const onCompleteRef   = useRef(onComplete)
  onCompleteRef.current = onComplete

  // Keep a stable ref to the poll function so the setTimeout callback always
  // calls the latest version without triggering effect re-runs.
  const pollRef = useRef(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    let anyRunning = false
    try {
      const results = await Promise.allSettled(
        Object.values(URLS).map(url => apiFetch(url).then(r => r.ok ? r.json() : null))
      )
      const [ir, cr, er, dr, dlr] = results.map(r =>
        r.status === 'fulfilled' ? r.value : null
      )

      if (ir)  setIndexStatus(ir)
      if (cr)  setCacheStatus(cr)
      if (er)  setEnrichStatus(er)
      if (dr)  setDiscoverStatus(dr)
      if (dlr) setDownloadStatus(dlr)

      anyRunning = !!(ir?.running || cr?.running || er?.running || dr?.running || dlr?.running)

      // Fire onComplete when index transitions running → idle
      if (ir?.running) {
        indexWasRunning.current = true
      } else if (indexWasRunning.current && ir && !ir.running) {
        indexWasRunning.current = false
        onCompleteRef.current?.(ir)
      }
    } catch { /* network blip — reschedule anyway */ }

    // Schedule the next poll at the appropriate cadence
    const ms = anyRunning ? INTERVAL_ACTIVE_MS : INTERVAL_IDLE_MS
    timerRef.current = setTimeout(() => pollRef.current?.(), ms)
  }, []) // no deps — state setters and refs are stable

  pollRef.current = poll

  // Start on mount; adaptive scheduling takes over from the first poll result
  useEffect(() => {
    poll()
    return () => stopPolling()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Snap back to fast polling immediately after manually triggering a job
  const startPolling = useCallback(() => {
    indexWasRunning.current = false
    stopPolling()
    poll()
  }, [poll, stopPolling])

  return {
    indexStatus,
    cacheStatus,
    enrichStatus,
    discoverStatus,
    downloadStatus,
    startPolling,
    stopPolling,
  }
}
