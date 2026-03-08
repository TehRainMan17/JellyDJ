import { useState, useEffect } from 'react'
import { Outlet, NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard, ListMusic, Telescope, Settings,
  Plug, BarChart2, Menu, X, ChevronRight, Ban, LogOut, User,
} from 'lucide-react'
import logoUrl from '/logo-64.png'
import { useAuth } from '../contexts/AuthContext.jsx'

// Nav items — mark admin-only ones
const NAV = [
  { to: '/dashboard',   icon: LayoutDashboard, label: 'Dashboard'   },
  { to: '/playlists',   icon: ListMusic,        label: 'Playlists'   },
  { to: '/discovery',   icon: Telescope,        label: 'Discovery'   },
  { to: '/insights',    icon: BarChart2,        label: 'Insights'    },
  { to: '/exclusions',  icon: Ban,              label: 'Exclusions',  adminOnly: true },
  { to: '/connections', icon: Plug,             label: 'Connections', adminOnly: true },
  { to: '/settings',    icon: Settings,         label: 'Settings',    adminOnly: true },
]

const PAGE_LABELS = {
  dashboard: 'Dashboard', playlists: 'Playlists',
  discovery: 'Discovery', insights: 'Insights',
  connections: 'Connections', settings: 'Settings', exclusions: 'Exclusions',
}

function SidebarContent({ onClose }) {
  const { isAdmin, user, logout } = useAuth()
  const navigate = useNavigate()

  const visibleNav = NAV.filter(item => !item.adminOnly || isAdmin)

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="h-16 flex items-center px-4 gap-3 flex-shrink-0"
           style={{ borderBottom: '1px solid var(--border)' }}>
        <img
          src={logoUrl}
          alt="JellyDJ"
          width={52}
          height={52}
          className="flex-shrink-0 anim-glow"
          style={{ borderRadius: '50%' }}
        />
        <span style={{ fontFamily:'Syne', fontWeight:800, fontSize:18, letterSpacing:'-0.02em' }}>
          <span style={{ background: 'linear-gradient(90deg, #5be6f5 0%, #9b5de5 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>Jelly</span><span style={{ background: 'linear-gradient(90deg, #9b5de5 0%, #b44fff 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>DJ</span>
        </span>
        {onClose && (
          <button onClick={onClose}
            className="ml-auto p-1.5 rounded-lg transition-colors hover:bg-white/5"
            style={{ color: 'var(--text-muted)' }}>
            <X size={16} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        <div className="section-label px-3 mb-3">Navigation</div>
        {visibleNav.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} onClick={onClose}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Icon size={16} strokeWidth={1.75} className="flex-shrink-0" />
            <span className="flex-1">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer — user + logout */}
      <div className="px-3 py-3 flex-shrink-0 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
        {/* Username display */}
        {user && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
               style={{ background: 'rgba(255,255,255,0.03)' }}>
            <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0"
                 style={{ background: 'var(--accent-soft)', border: '1px solid rgba(83,236,252,0.2)' }}>
              <User size={11} style={{ color: 'var(--accent)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                {user.username}
              </div>
              {isAdmin && (
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Admin</div>
              )}
            </div>
          </div>
        )}

        {/* Logout button */}
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition-all"
          style={{ color: 'var(--text-secondary)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(248,113,113,0.08)'; e.currentTarget.style.color = 'var(--danger)' }}
          onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.style.color = 'var(--text-secondary)' }}
        >
          <LogOut size={14} strokeWidth={1.75} />
          <span className="text-xs font-medium">Sign out</span>
        </button>

        {/* Version */}
        <div className="flex items-center gap-2 px-3 pt-1">
          <div className="w-2 h-2 rounded-full anim-glow" style={{ background: 'var(--accent)' }} />
          <span style={{ fontSize:11, color:'var(--text-muted)', fontFamily:'JetBrains Mono,monospace' }}>v0.1.0</span>
        </div>
      </div>
    </div>
  )
}

function Breadcrumb() {
  const { pathname } = useLocation()
  const seg = pathname.split('/').filter(Boolean)
  return (
    <div className="flex items-center gap-1.5 text-sm overflow-hidden">
      <span style={{ fontFamily:'Syne', fontWeight:700, fontSize:14, color:'var(--text-muted)' }}>JellyDJ</span>
      {seg.map((s, i) => (
        <span key={s} className="flex items-center gap-1.5 min-w-0">
          <ChevronRight size={12} style={{ color:'var(--border-mid)', flexShrink:0 }} />
          <span className="truncate" style={{ color: i===seg.length-1 ? 'var(--text-primary)' : 'var(--text-secondary)', fontWeight: i===seg.length-1 ? 600 : 400 }}>
            {PAGE_LABELS[s] || s}
          </span>
        </span>
      ))}
    </div>
  )
}

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { pathname } = useLocation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  // Close mobile nav on route change
  useEffect(() => { setMobileOpen(false) }, [pathname])

  // Lock body scroll when mobile nav open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [mobileOpen])

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>

      {/* ── Desktop sidebar ─────────────────────────────────────────────── */}
      <aside className="hidden lg:flex flex-col flex-shrink-0"
             style={{ width: 'var(--sidebar-w)', borderRight: '1px solid var(--border)', background: 'var(--bg-surface)' }}>
        <SidebarContent />
      </aside>

      {/* ── Mobile sidebar overlay ──────────────────────────────────────── */}
      {mobileOpen && (
        <div className="lg:hidden fixed inset-0 z-50 flex">
          {/* Backdrop */}
          <div className="absolute inset-0" style={{ background:'rgba(0,0,0,0.6)', backdropFilter:'blur(4px)' }}
               onClick={() => setMobileOpen(false)} />
          {/* Drawer */}
          <aside className="relative z-10 flex flex-col anim-slide-l"
                 style={{ width:280, background:'var(--bg-surface)', borderRight:'1px solid var(--border)' }}>
            <SidebarContent onClose={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">

        {/* Topbar */}
        <header className="flex-shrink-0 flex items-center gap-3 px-4 lg:px-6"
                style={{ height:'var(--header-h)', borderBottom:'1px solid var(--border)', background:'var(--bg-surface)' }}>
          {/* Mobile menu button */}
          <button onClick={() => setMobileOpen(true)}
                  className="lg:hidden p-2 rounded-xl transition-colors hover:bg-white/5 flex-shrink-0"
                  style={{ color:'var(--text-secondary)' }}>
            <Menu size={18} />
          </button>

          <Breadcrumb />

          {/* Right slot */}
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg"
                 style={{ background:'var(--accent-soft)', border:'1px solid rgba(83,236,252,0.15)' }}>
              <div className="w-1.5 h-1.5 rounded-full anim-glow" style={{ background:'var(--accent)' }} />
              <span style={{ fontSize:11, color:'var(--accent)', fontWeight:600 }}>Live</span>
            </div>

            {/* Topbar user + logout (desktop only — sidebar handles this on desktop via the footer) */}
            {user && (
              <button
                onClick={handleLogout}
                title={`Sign out (${user.username})`}
                className="hidden lg:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-all"
                style={{ color: 'var(--text-secondary)', border: '1px solid transparent' }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(248,113,113,0.25)'; e.currentTarget.style.color = 'var(--danger)' }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' }}
              >
                <LogOut size={12} />
                <span className="font-medium">{user.username}</span>
              </button>
            )}
          </div>
        </header>

        {/* Page */}
        <main className="flex-1 overflow-y-auto" style={{ background:'var(--bg)' }}>
          <div className="p-4 lg:p-6 page-enter">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
