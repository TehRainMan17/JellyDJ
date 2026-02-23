/**
 * App — root router component.
 *
 * All routes are nested under <Layout />, which renders the sidebar nav
 * and the topbar. The Outlet inside Layout renders the active page.
 *
 * Route map:
 *   /               → redirect to /dashboard
 *   /dashboard      → system overview, user sync status, activity feed
 *   /playlists      → playlist generation controls and run history
 *   /discovery      → new album recommendation queue
 *   /settings       → indexer intervals, external API keys, webhook setup
 *   /connections    → Jellyfin + Lidarr connection credentials
 *   /insights       → listening statistics and charts
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Playlists from './pages/Playlists.jsx'
import DiscoveryQueue from './pages/DiscoveryQueue.jsx'
import Settings from './pages/Settings.jsx'
import Connections from './pages/Connections.jsx'
import Insights from './pages/Insights.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"   element={<Dashboard />} />
          <Route path="playlists"   element={<Playlists />} />
          <Route path="discovery"   element={<DiscoveryQueue />} />
          <Route path="settings"    element={<Settings />} />
          <Route path="connections" element={<Connections />} />
          <Route path="insights"    element={<Insights />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
