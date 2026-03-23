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
 *   - Adaptive interval: 2s while any job is running, 10s after any job just
 *     finished (keep showing the completion state), 30s when all fully idle.
 *   - Once a job transitions to finished, we KEEP that finished state in memory
 *     so JobProgress can run its 10-20s hide timer — we only reset it when the
 *     backend confirms the job has been cleared (running=false, finished_at=null).
 *   - startPolling() snaps back to fast polling immediately (call after
 *     manually triggering a job).
 *   - onComplete fires once when the INDEX job transitions running → finished.
 *
 * BUG FIXES vs previous version:
 *   - State is never overwritten with null/empty from a failed fetch — stale
 *     state is preserved so the progress bar doesn't disappear on a blip.
 *   - After a job finishes we switch to a 10s "cool-down" interval so the
 *     completed state stays visible long enough for the 15-20s hide timer
 *     in JobProgress to fire before we slow-poll back to idle state.
 *   - All five status keys always receive a value on every successful poll
 *     so there are no "stuck running" states from partial responses.
 */
import { useState, useRef, useCallback, useEffect } from 'react'
import { apiFetch, hasToken } from '../lib/api'

const URLS = {
  index:    '/api/indexer/job-status',
  cache:    '/api/automation/trigger/popularity-cache/status',
  enrich:   '/api/automation/trigger/enrichment/status',
  discover: '/api/automation/trigger/discovery/status',
  download: '/api/automation/trigger/auto-download/status',
}

const INTERVAL_ACTIVE_MS   = 2000   // a job is running — keep progress bar snappy
const INTERVAL_COOLING_MS  = 5000   // a job just finished — keep polling so the bar stays visible
const INTERVAL_IDLE_MS     = 30000  // nothing happening — don't hammer the server

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

  // Track when each job last finished so we can stay in COOLING interval
  const finishedAtRef = useRef({})

  // Keep a stable ref to the latest poll function
  const pollRef = useRef(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    let anyRunning = false
    let anyRecentlyFinished = false

    // Skip the poll entirely when there's no auth token — avoids 403 log
    // spam on the backend from unauthenticated requests.
    if (!hasToken()) {
      timerRef.current = setTimeout(() => pollRef.current?.(), INTERVAL_IDLE_MS)
      return
    }

    try {
      const results = await Promise.allSettled(
        Object.values(URLS).map(url => apiFetch(url).then(r => r.ok ? r.json() : null))
      )
      const [ir, cr, er, dr, dlr] = results.map(r =>
        r.status === 'fulfilled' ? r.value : null
      )

      // Only update state when we got a valid (non-null) response — never
      // clobber existing state with null from a transient network error.
      if (ir  != null) setIndexStatus(ir)
      if (cr  != null) setCacheStatus(cr)
      if (er  != null) setEnrichStatus(er)
      if (dr  != null) setDiscoverStatus(dr)
      if (dlr != null) setDownloadStatus(dlr)

      const statuses = { index: ir, cache: cr, enrich: er, discover: dr, download: dlr }

      // Determine scheduling cadence
      for (const [key, s] of Object.entries(statuses)) {
        if (!s) continue
        if (s.running) {
          anyRunning = true
          finishedAtRef.current[key] = null  // reset finish tracker while running
        } else if (s.finished_at) {
          // Job is done — record when we first saw it finished
          if (!finishedAtRef.current[key]) {
            finishedAtRef.current[key] = Date.now()
          }
          // Stay in cooling interval for 25s after finish (longer than the 20s hide timer)
          const age = Date.now() - finishedAtRef.current[key]
          if (age < 25_000) {
            anyRecentlyFinished = true
          }
        }
      }

      // Fire onComplete when index transitions running → idle
      if (ir?.running) {
        indexWasRunning.current = true
      } else if (indexWasRunning.current && ir && !ir.running) {
        indexWasRunning.current = false
        onCompleteRef.current?.(ir)
      }
    } catch { /* network blip — reschedule anyway */ }

    // Pick the tightest interval that applies
    const ms = anyRunning
      ? INTERVAL_ACTIVE_MS
      : anyRecentlyFinished
        ? INTERVAL_COOLING_MS
        : INTERVAL_IDLE_MS

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
    finishedAtRef.current = {}
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
