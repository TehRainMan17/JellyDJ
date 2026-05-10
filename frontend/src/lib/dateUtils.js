/**
 * dateUtils.js — shared date/time formatting helpers.
 *
 * Backend returns ISO timestamps that are usually bare (no timezone). Treat
 * those as UTC. If the string already carries a TZ offset or Z, leave it.
 */

// Normalise an ISO string to one that JS will parse as UTC.
// Preserves existing TZ info (offset or Z); adds 'Z' only if absent.
export const toUtcIso = s => {
  if (!s) return s
  if (/([+-]\d{2}:\d{2}|Z)$/.test(s)) return s
  return s + 'Z'
}

// "Mar 5, 2026, 02:14 PM" — date + hours/minutes
export function formatDate(iso, fallback = '—') {
  if (!iso) return fallback
  return new Date(toUtcIso(iso)).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// "Mar 5, 02:14 PM" — short, no year
export function formatDateShort(iso, fallback = '—') {
  if (!iso) return fallback
  return new Date(toUtcIso(iso)).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// "Mar 5, 2026" — date only, no time
export function formatDateOnly(iso, fallback = '—') {
  if (!iso) return fallback
  return new Date(toUtcIso(iso)).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

// "just now" / "5m ago" / "3h ago" / "2d ago"
export function formatTimeAgo(iso) {
  if (!iso) return ''
  const diff = (Date.now() - new Date(toUtcIso(iso))) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// Days from now until iso. Returns null for past dates or missing input.
export function daysUntil(iso) {
  if (!iso) return null
  const ms = new Date(toUtcIso(iso)) - Date.now()
  if (ms <= 0) return null
  return Math.ceil(ms / 86400000)
}

// Format a timestamp in a specific timezone (used by AutomationPanel).
export function formatInTz(iso, tz) {
  if (!iso) return null
  try {
    return new Date(toUtcIso(iso)).toLocaleString(undefined, tz ? { timeZone: tz } : undefined)
  } catch {
    return new Date(toUtcIso(iso)).toLocaleString()
  }
}
