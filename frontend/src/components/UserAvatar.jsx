import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext.jsx'

/**
 * Displays a Jellyfin user's profile image.
 * Falls back to a letter-initial circle if the user has no profile image.
 *
 * Props:
 *   jellyfinUserId — Jellyfin user ID (used to fetch the avatar)
 *   username       — Display name (used for the fallback initial)
 *   className      — Tailwind size + any other classes (e.g. "w-8 h-8")
 *   style          — Extra inline styles applied to both img and fallback div
 *   fallbackStyle  — Extra inline styles for the fallback div only
 */
export default function UserAvatar({
  jellyfinUserId,
  username,
  className = 'w-8 h-8',
  style = {},
  fallbackStyle = {},
}) {
  const { accessToken } = useAuth()
  const [blobUrl, setBlobUrl] = useState(null)
  const initial = (username || '?')[0]?.toUpperCase()

  useEffect(() => {
    if (!jellyfinUserId || !accessToken) return
    let objectUrl = null
    let cancelled = false

    fetch(`/api/auth/avatar/${jellyfinUserId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then(resp => {
        if (!resp.ok) throw new Error('no image')
        return resp.blob()
      })
      .then(blob => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setBlobUrl(objectUrl)
      })
      .catch(() => { /* leave blobUrl null → fallback initial shown */ })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [jellyfinUserId, accessToken])

  if (blobUrl) {
    return (
      <img
        src={blobUrl}
        alt={username || 'User'}
        className={`${className} rounded-full object-cover flex-shrink-0`}
        style={style}
      />
    )
  }

  // Fallback — also shown while loading (same appearance as before, no flash)
  return (
    <div
      className={`${className} rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0`}
      style={{ ...style, ...fallbackStyle }}
    >
      {initial}
    </div>
  )
}
