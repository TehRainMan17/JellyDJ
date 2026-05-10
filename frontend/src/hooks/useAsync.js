/**
 * useAsync — drop-in replacement for the [loading, error, try/catch/finally]
 * boilerplate that appears 30+ times across pages.
 *
 * Usage:
 *
 *   const { data, loading, error, refetch } = useAsync(
 *     () => api.get('/api/something'),
 *     [dep1, dep2],   // optional dependency array; refetches when changed
 *   )
 *
 * If you want manual control (don't fetch on mount), pass `manual: true`:
 *
 *   const { data, loading, error, run } = useAsync(fn, [], { manual: true })
 *   // ...later: run() to trigger
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export default function useAsync(asyncFn, deps = [], { manual = false } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(!manual)
  const [error, setError] = useState(null)
  const aliveRef = useRef(true)

  useEffect(() => () => { aliveRef.current = false }, [])

  const run = useCallback(async (...args) => {
    setLoading(true)
    setError(null)
    try {
      const result = await asyncFn(...args)
      if (aliveRef.current) setData(result)
      return result
    } catch (e) {
      if (aliveRef.current) setError(e)
      throw e
    } finally {
      if (aliveRef.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    if (manual) return
    run().catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, loading, error, run, refetch: run, setData }
}
