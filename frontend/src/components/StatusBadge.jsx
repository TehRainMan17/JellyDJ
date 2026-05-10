/**
 * StatusBadge — unified status pill to replace per-page Badge / StatusPill /
 * StatusBadge variants in Connections, DiscoveryQueue, PlaylistBackups.
 *
 * Variants: default | success | warning | danger | info | accent
 * Sizes:    sm | md
 */

const VARIANTS = {
  default: 'bg-white/10 text-[var(--text-secondary)]',
  success: 'bg-emerald-500/20 text-emerald-400',
  warning: 'bg-amber-500/20 text-amber-400',
  danger:  'bg-red-500/20 text-red-400',
  info:    'bg-blue-500/20 text-blue-400',
  accent:  'bg-[var(--accent)]/20 text-[var(--accent)]',
}

const SIZES = {
  sm: 'text-[9px] px-1.5 py-0.5',
  md: 'text-[10px] px-2 py-0.5',
}

export default function StatusBadge({
  variant = 'default',
  size = 'md',
  icon: Icon = null,
  children,
  className = '',
}) {
  return (
    <span className={`inline-flex items-center gap-1 font-semibold rounded-full ${VARIANTS[variant]} ${SIZES[size]} ${className}`}>
      {Icon && <Icon size={size === 'sm' ? 9 : 11} />}
      {children}
    </span>
  )
}
