/**
 * useJobStatus — React hook for polling a background job's progress.
 *
 * The indexer runs as a fire-and-forget background task on the server, so the
 * frontend has no way to know when it finishes without polling. This hook
 * handles that cleanly: call startPolling() immediately after triggering a job,
 * and the hook will poll /api/indexer/job-status every 2 seconds until the
 * server reports running=false, then call onComplete() once and stop itself.
 *
 * The endpoint is a pure in-memory read (no DB queries) so 2s polling is safe
 * even on low-powered hardware.
 *
 * Usage:
 *   const { jobStatus, startPolling } = useJobStatus((finalState) => {
 *     // called once when job finishes — refresh your data here
 *     refetchDashboardData()
 *   })
 *
 *   // After triggering a job:
 *   await fetch('/api/indexer/full-scan', { method: 'POST' })
 *   startPolling()
 */
import { useState, useRef, useCallback, useEffect } from 'react'

/**
 * Polls /api/indexer/job-status every 2s while a job appears to be running.
 * Call startPolling() after firing off a background job.
 * onComplete(jobState) is called once when running flips false.
 */
export function useJobStatus(onComplete) {
  const [jobStatus, setJobStatus] = useState(null)
  const timerRef       = useRef(null)
  const wasRunningRef  = useRef(false)
  const onCompleteRef  = useRef(onComplete)
  onCompleteRef.current = onComplete

  const stopPolling = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }, [])

  const poll = useCallback(async () => {
    try {
      const r = await fetch('/api/indexer/job-status')
      if (!r.ok) return
      const d = await r.json()
      setJobStatus(d)
      if (d.running) {
        wasRunningRef.current = true
      } else if (wasRunningRef.current) {
        wasRunningRef.current = false
        stopPolling()
        onCompleteRef.current?.(d)
      }
    } catch { /* network blip */ }
  }, [stopPolling])

  const startPolling = useCallback(() => {
    wasRunningRef.current = false
    stopPolling()
    poll()
    timerRef.current = setInterval(poll, 2000)
  }, [poll, stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  return { jobStatus, startPolling, stopPolling }
}
