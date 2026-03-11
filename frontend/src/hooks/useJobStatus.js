
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
 *   - startPolling() resets and restarts polling (call after triggering a job).
 *   - onComplete fires once when the INDEX job transitions running → false.
 */
import { useState, useRef, useCallback, useEffect } from 'react'

const URLS = {
  index:    '/api/indexer/job-status',
  cache:    '/api/automation/trigger/popularity-cache/status',
  enrich:   '/api/automation/trigger/enrichment/status',
  discover: '/api/automation/trigger/discovery/status',
  download: '/api/automation/trigger/auto-download/status',
}
const INTERVAL_MS = 2000

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

  const stopPolling = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }, [])

  const poll = useCallback(async () => {
    try {
      const results = await Promise.allSettled(
        Object.values(URLS).map(url => fetch(url).then(r => r.ok ? r.json() : null))
      )
      const [ir, cr, er, dr, dlr] = results.map(r =>
        r.status === 'fulfilled' ? r.value : null
      )

      if (ir)  setIndexStatus(ir)
      if (cr)  setCacheStatus(cr)
      if (er)  setEnrichStatus(er)
      if (dr)  setDiscoverStatus(dr)
      if (dlr) setDownloadStatus(dlr)

      // Fire onComplete when index transitions running → idle
      if (ir?.running) {
        indexWasRunning.current = true
      } else if (indexWasRunning.current && ir && !ir.running) {
        indexWasRunning.current = false
        onCompleteRef.current?.(ir)
      }
    } catch { /* network blip */ }
  }, [])

  // Start polling immediately on mount
  useEffect(() => {
    poll()
    timerRef.current = setInterval(poll, INTERVAL_MS)
    return () => stopPolling()
  }, [poll, stopPolling])

  const startPolling = useCallback(() => {
    indexWasRunning.current = false
    stopPolling()
    poll()
    timerRef.current = setInterval(poll, INTERVAL_MS)
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
