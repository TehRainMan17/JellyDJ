const PLATFORM_ICONS = {
  spotify:       '/icons/spotify.png',
  tidal:         '/icons/tidal.png',
  youtube_music: '/icons/ytmusic.png',
}

export default function PlatformIcon({ platform, size = 18, className = '' }) {
  const src = PLATFORM_ICONS[platform]
  if (!src) return null
  return (
    <img
      src={src}
      alt={platform}
      width={size}
      height={size}
      className={`flex-shrink-0 ${className}`}
      style={{ borderRadius: 4 }}
    />
  )
}
