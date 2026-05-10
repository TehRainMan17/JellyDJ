/**
 * Button — unified button component to replace ad-hoc ActionButton / ActionBtn /
 * Btn implementations across Connections, PlaylistBackups, AdminUsers, BlockEditor.
 *
 * Variants: primary | secondary | danger | ghost
 * Sizes:    sm | md | lg
 *
 * Pass `loading` to show a spinner and disable interaction.
 */

import { Loader2 } from 'lucide-react'

const VARIANTS = {
  primary:   'bg-[var(--accent)] text-black hover:brightness-110',
  secondary: 'bg-white/5 text-[var(--text-primary)] hover:bg-white/10 border border-white/10',
  danger:    'bg-[var(--danger)]/10 text-[var(--danger)] hover:bg-[var(--danger)]/20 border border-[var(--danger)]/30',
  ghost:     'bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-white/5',
}

const SIZES = {
  sm: 'text-[11px] px-2.5 py-1 rounded-md',
  md: 'text-xs px-3 py-1.5 rounded-md',
  lg: 'text-sm px-4 py-2 rounded-lg',
}

export default function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled,
  className = '',
  children,
  ...rest
}) {
  const isDisabled = disabled || loading
  return (
    <button
      disabled={isDisabled}
      className={`inline-flex items-center justify-center gap-1.5 font-semibold transition-all ${VARIANTS[variant]} ${SIZES[size]} ${isDisabled ? 'opacity-50 cursor-not-allowed' : ''} ${className}`}
      {...rest}
    >
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}
