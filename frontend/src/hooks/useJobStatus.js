/**
 * useJobStatus — polls both the indexer job and the popularity cache refresh.
 *
 * Two separate background processes run on the server:
 *   1. Indexer  → /api/indexer/job-status
 *      Fields: { running, phase, detail, percent, error, started_at, finished_at }
 *
 *   2. Cache refresh → /api/automation/trigger/popularity-cache/status
 *      Fields: { running, phase, done, total, progress_pct, error, started_at, finished_at }
 *
 * Key behaviours:
 *   - Polling starts immediately on mount so a job already running when you
 *     navigate to the dashboard is visible right away — no button click needed.
 *   - startPolling() resets and restarts (called after manually triggering).
 *   - onComplete fires once when the INDEX job transitions running→false.
 */
import { useState, useRef, useCallback, useEffect } from 'react'

const INDEX_URL  = '/api/indexer/job-status'
const CACHE_URL  = '/api/automation/trigger/popularity-cache/status'
const INTERVAL_MS = 2000

export function useJobStatus(onComplete) {
  const [indexStatus, setIndexStatus] = useState(null)
  const [cacheStatus, setCacheStatus] = useState(null)

  const timerRef        = useRef(null)
  const indexWasRunning = useRef(false)
  const onCompleteRef   = useRef(onComplete)
  onCompleteRef.current = onComplete

  const stopPolling = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }, [])

  const poll = useCallback(async () => {
    try {
      const [ir, cr] = await Promise.allSettled([
        fetch(INDEX_URL).then(r => r.ok ? r.json() : null),
        fetch(CACHE_URL).then(r => r.ok ? r.json() : null),
      ])

      const idx   = ir.status === 'fulfilled' ? ir.value : null
      const cache = cr.status === 'fulfilled' ? cr.value : null

      if (idx)   setIndexStatus(idx)
      if (cache) setCacheStatus(cache)

      // Fire onComplete when index transitions running → idle
      if (idx?.running) {
        indexWasRunning.current = true
      } else if (indexWasRunning.current && idx && !idx.running) {
        indexWasRunning.current = false
        onCompleteRef.current?.(idx)
      }
    } catch { /* network blip */ }
  }, [])

  // Start polling immediately on mount — catches jobs already running
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

  return { indexStatus, cacheStatus, startPolling, stopPolling }
}
