import { useState, useEffect } from 'react'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, ListMusic, Telescope, Settings,
  Plug, Radio, BarChart2, Menu, X, ChevronRight,
} from 'lucide-react'

const NAV = [
  { to: '/dashboard',   icon: LayoutDashboard, label: 'Dashboard'       },
  { to: '/playlists',   icon: ListMusic,        label: 'Playlists'       },
  { to: '/discovery',   icon: Telescope,        label: 'Discovery'       },
  { to: '/insights',    icon: BarChart2,        label: 'Insights'        },
  { to: '/connections', icon: Plug,             label: 'Connections'     },
  { to: '/settings',    icon: Settings,         label: 'Settings'        },
]

const PAGE_LABELS = {
  dashboard: 'Dashboard', playlists: 'Playlists',
  discovery: 'Discovery', insights: 'Insights',
  connections: 'Connections', settings: 'Settings',
}

function SidebarContent({ onClose }) {
  return (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 gap-3 flex-shrink-0"
           style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 anim-glow"
             style={{ background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)' }}>
          <Radio size={15} color="#0a0e17" strokeWidth={2.5} />
        </div>
        <span style={{ fontFamily:'Syne', fontWeight:800, fontSize:18, letterSpacing:'-0.02em', color:'var(--text-primary)' }}>
          JellyDJ
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
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} onClick={onClose}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Icon size={16} strokeWidth={1.75} className="flex-shrink-0" />
            <span className="flex-1">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 flex-shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-[var(--accent)] anim-glow" />
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

  // Close mobile nav on route change
  useEffect(() => { setMobileOpen(false) }, [pathname])

  // Lock body scroll when mobile nav open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [mobileOpen])

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
          <aside className="relative z-10 flex flex-col anim-slide-r"
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

          {/* Right slot — could add notifications, user avatar, etc later */}
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg"
                 style={{ background:'rgba(0,212,170,0.06)', border:'1px solid rgba(0,212,170,0.12)' }}>
              <div className="w-1.5 h-1.5 rounded-full anim-glow" style={{ background:'var(--accent)' }} />
              <span style={{ fontSize:11, color:'var(--accent)', fontWeight:600 }}>Live</span>
            </div>
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
