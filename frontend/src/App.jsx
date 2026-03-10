/**
 * App — root router component.
 *
 * All protected routes are nested under <Layout />, which renders the sidebar nav
 * and the topbar. The Outlet inside Layout renders the active page.
 *
 * Route map:
 *   /login          → Login page (public)
 *   /               → redirect to /dashboard
 *   /dashboard      → system overview, user sync status, activity feed
 *   /playlists      → playlist generation controls and run history
 *   /discovery      → new album recommendation queue
 *   /settings       → indexer intervals, external API keys, webhook setup
 *   /connections    → Jellyfin + Lidarr connection credentials
 *   /insights       → listening statistics and charts
 *   /exclusions     → manual album exclusions
 */

import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Playlists from './pages/Playlists.jsx'
import DiscoveryQueue from './pages/DiscoveryQueue.jsx'
import Settings from './pages/Settings.jsx'
import Connections from './pages/Connections.jsx'
import Insights from './pages/Insights.jsx'
import AlbumExclusions from './pages/AlbumExclusions.jsx'
import Login from './pages/Login.jsx'
import { useAuth } from './contexts/AuthContext.jsx'
import { _wireAuth } from './lib/api.js'

// ── Wire api.js to auth context ───────────────────────────────────────────────
function ApiWire() {
  const { accessToken, refresh, logout } = useAuth()
  useEffect(() => {
    _wireAuth({
      getToken: () => accessToken,
      refresh,
      logout,
    })
  }, [accessToken, refresh, logout])
  return null
}

// ── Route guard ───────────────────────────────────────────────────────────────
function RequireAuth({ children }) {
  const { isAuthenticated, loading } = useAuth()
  const location = useLocation()

  // Don't redirect until the initial silent refresh completes
  if (loading) return null

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return children
}

// ── Admin-only route guard ────────────────────────────────────────────────────
function RequireAdmin({ children }) {
  const { isAdmin, loading } = useAuth()
  if (loading) return null
  if (!isAdmin) return <Navigate to="/dashboard" replace />
  return children
}

export default function App() {
  return (
    <BrowserRouter>
      <ApiWire />
      <Routes>
        {/* Public route */}
        <Route path="/login" element={<Login />} />

        {/* Protected routes */}
        <Route
          path="/"
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"   element={<Dashboard />} />
          <Route path="playlists"   element={<Playlists />} />
          <Route path="discovery"   element={<DiscoveryQueue />} />
          <Route path="insights"    element={<Insights />} />
          <Route path="exclusions"  element={<RequireAdmin><AlbumExclusions /></RequireAdmin>} />
          <Route path="connections" element={<RequireAdmin><Connections /></RequireAdmin>} />
          <Route path="settings"    element={<RequireAdmin><Settings /></RequireAdmin>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}