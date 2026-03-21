/**
 * JellyfinIcon.jsx
 * Inline SVG recreation of the Jellyfin logo — two concentric rounded
 * triangles with the brand purple-to-cyan gradient.
 * Accepts a `size` prop (default 16) and className.
 */
import { useId } from 'react'

export default function JellyfinIcon({ size = 16, className = '' }) {
  const uid = useId().replace(/:/g, '')
  const gradId = `jfg-${uid}`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 256 256"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%"   stopColor="#aa5cc3" />
          <stop offset="100%" stopColor="#00a4dc" />
        </linearGradient>
      </defs>
      {/* Outer rounded triangle */}
      <path
        d="M128 18 C118 18 109 23 104 32 L18 186 C13 195 13 206 18 215 C23 224 33 230 44 230 L212 230 C223 230 233 224 238 215 C243 206 243 195 238 186 L152 32 C147 23 138 18 128 18 Z"
        fill={`url(#${gradId})`}
      />
      {/* Inner cutout */}
      <path
        d="M128 52 C123 52 118 55 115 60 L46 186 C43 191 43 197 46 202 C49 207 55 210 61 210 L195 210 C201 210 207 207 210 202 C213 197 213 191 210 186 L141 60 C138 55 133 52 128 52 Z"
        fill="#1c1c1c"
      />
      {/* Inner filled triangle */}
      <path
        d="M128 98 C125 98 122 100 120 103 L88 158 C86 161 86 165 88 168 C90 171 93 173 97 173 L159 173 C163 173 166 171 168 168 C170 165 170 161 168 158 L136 103 C134 100 131 98 128 98 Z"
        fill={`url(#${gradId})`}
      />
    </svg>
  )
}
