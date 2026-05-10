/**
 * PlatformBadge — colored pill labelling the source platform of a playlist.
 *
 * Shared across PlaylistImport, PlaylistImportDetail, and Playlists pages.
 */

export const PLATFORM_LABELS = {
  spotify:       { label: 'Spotify',       color: '#1db954' },
  tidal:         { label: 'Tidal',         color: '#00ffff' },
  youtube_music: { label: 'YouTube Music', color: '#ff0000' },
  unknown:       { label: 'Unknown',       color: '#888' },
}

export default function PlatformBadge({ platform, size = 'md' }) {
  const { label, color } = PLATFORM_LABELS[platform] || PLATFORM_LABELS.unknown
  const sizing = size === 'sm'
    ? 'text-[9px] px-1.5 py-0.5'
    : 'text-[10px] px-2 py-0.5'
  return (
    <span
      className={`${sizing} font-semibold rounded-full`}
      style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  )
}
