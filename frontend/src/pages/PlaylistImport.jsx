/**
 * PlaylistImport.jsx — Playlist Import list page
 *
 * Route: /import
 *
 * Sections:
 *  1. URL paste form (alternative to browser extension)
 *  2. Grid of imported playlists — click to open detail page
 *  3. Link to Extension Setup page
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Loader2, Trash2, ChevronRight, ArrowDownToLine, Settings2, RefreshCw, Pencil, Check, X,
} from 'lucide-react'
import { api } from '../lib/api.js'
import PlatformIcon from '../components/PlatformIcon.jsx'

// ── Platform badge ──────────────────────────────────────────────────────────

const PLATFORM_LABELS = {
  spotify:       { label: 'Spotify',       color: '#1db954' },
  tidal:         { label: 'Tidal',         color: '#00ffff' },
  youtube_music: { label: 'YouTube Music', color: '#ff0000' },
  unknown:       { label: 'Unknown',       color: '#888' },
}

function PlatformBadge({ platform }) {
  const { label, color } = PLATFORM_LABELS[platform] || PLATFORM_LABELS.unknown
  return (
    <span
      className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
      style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  )
}

// ── Progress bar ────────────────────────────────────────────────────────────

function MatchBar({ matched, total }) {
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0
  const color = pct >= 80 ? 'var(--accent)' : pct >= 50 ? '#fbbf24' : 'var(--danger)'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.08)' }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[10px] font-mono flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
        {matched}/{total}
      </span>
    </div>
  )
}

// ── URL paste form ──────────────────────────────────────────────────────────

function ImportForm({ onImported }) {
  const [url, setUrl]         = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!url.trim()) return
    setLoading(true)
    setError('')
    try {
      await api.post('/api/import/playlists', { url: url.trim() })
      setUrl('')
      onImported()
    } catch (err) {
      setError(err.message)
    }
    setLoading(false)
  }

  return (
    <div className="card" style={{ padding: '16px 20px' }}>
      <div className="text-xs font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>Import a playlist</div>
      <p className="text-[11px] mb-3" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
        Paste a public playlist URL from Spotify, Tidal, or YouTube Music.
      </p>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="url"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://open.spotify.com/playlist/…"
          className="input flex-1 text-xs"
        />
        <button
          type="submit"
          disabled={loading || !url.trim()}
          className="btn-primary text-xs flex-shrink-0"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : null}
          {loading ? 'Importing…' : 'Import'}
        </button>
      </form>
      {error && <p className="text-xs mt-2" style={{ color: 'var(--danger)' }}>{error}</p>}
    </div>
  )
}

// ── Playlist card ───────────────────────────────────────────────────────────

function PlaylistCard({ playlist, onOpen, onDelete, onRematched, onRenamed }) {
  const isPending = playlist.status === 'pending'
  const isMatching = playlist.status === 'matching'
  const isBusy = isPending || isMatching
  const [deleting, setDeleting]     = useState(false)
  const [confirmDel, setConfirm]    = useState(false)
  const [rematching, setRematching] = useState(false)
  const [renaming, setRenaming]     = useState(false)
  const [renameVal, setRenameVal]   = useState(playlist.name)
  const [renameSaving, setRenameSaving] = useState(false)
  const missingCount = playlist.track_count - playlist.matched_count
  const pct = playlist.track_count > 0
    ? Math.round((playlist.matched_count / playlist.track_count) * 100) : 0

  async function handleDelete(e) {
    e.stopPropagation()
    if (!confirmDel) { setConfirm(true); return }
    setDeleting(true)
    try {
      await api.delete(`/api/import/playlists/${playlist.id}`)
      onDelete()
    } catch (err) {
      alert('Failed to delete: ' + err.message)
      setDeleting(false)
      setConfirm(false)
    }
  }

  async function handleRematch(e) {
    e.stopPropagation()
    setRematching(true)
    try {
      await api.post(`/api/import/playlists/${playlist.id}/rematch`)
      const poll = setInterval(async () => {
        try {
          const det = await api.get(`/api/import/playlists/${playlist.id}`)
          if (det.status === 'active' || det.status === 'error') {
            clearInterval(poll)
            setRematching(false)
            onRematched()
          }
        } catch {
          clearInterval(poll)
          setRematching(false)
        }
      }, 2000)
    } catch (err) {
      alert('Re-match failed: ' + err.message)
      setRematching(false)
    }
  }

  async function handleRenameSave(e) {
    e.stopPropagation()
    const trimmed = renameVal.trim()
    if (!trimmed || trimmed === playlist.name) { setRenaming(false); return }
    setRenameSaving(true)
    try {
      const updated = await api.patch(`/api/import/playlists/${playlist.id}/rename`, { name: trimmed })
      setRenaming(false)
      onRenamed(updated)
      if (updated.jellyfin_error) {
        alert(`Renamed in JellyDJ, but Jellyfin sync failed:\n${updated.jellyfin_error}`)
      }
    } catch (err) {
      alert('Rename failed: ' + err.message)
    }
    setRenameSaving(false)
  }

  function handleRenameKeyDown(e) {
    if (e.key === 'Enter') handleRenameSave(e)
    if (e.key === 'Escape') { e.stopPropagation(); setRenaming(false); setRenameVal(playlist.name) }
  }

  return (
    <div
      className="card anim-fade-up overflow-hidden"
      style={{ padding: 0, cursor: isBusy ? 'default' : 'pointer' }}
      onClick={() => !isBusy && onOpen(playlist)}
    >
      {/* Main content */}
      <div className="p-4 space-y-3">
        {/* Row 1: Name + platform icon + actions */}
        <div className="flex items-start gap-3">
          <PlatformIcon platform={playlist.source_platform} size={28} />
          <div className="flex-1 min-w-0">
            {renaming ? (
              <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                <input
                  autoFocus
                  className="input text-xs flex-1 min-w-0"
                  value={renameVal}
                  onChange={e => setRenameVal(e.target.value)}
                  onKeyDown={handleRenameKeyDown}
                  style={{ padding: '3px 8px', height: 28 }}
                />
                <button
                  onClick={handleRenameSave}
                  disabled={renameSaving}
                  className="btn-secondary p-1 flex-shrink-0"
                  title="Save"
                >
                  {renameSaving ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} style={{ color: 'var(--accent)' }} />}
                </button>
                <button
                  onClick={e => { e.stopPropagation(); setRenaming(false); setRenameVal(playlist.name) }}
                  className="btn-secondary p-1 flex-shrink-0"
                  title="Cancel"
                >
                  <X size={10} />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1 group/name min-w-0">
                <div className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                  {playlist.name}
                </div>
                <button
                  onClick={e => { e.stopPropagation(); setRenaming(true); setRenameVal(playlist.name) }}
                  className="opacity-0 group-hover/name:opacity-100 transition-opacity btn-secondary p-0.5 flex-shrink-0"
                  title="Rename"
                >
                  <Pencil size={9} />
                </button>
              </div>
            )}
            <div className="flex items-center gap-2 mt-1">
              <PlatformBadge platform={playlist.source_platform} />
              {(isPending || isMatching) && (
                <span className="flex items-center gap-1 text-[10px]" style={{ color: '#fbbf24' }}>
                  <Loader2 size={9} className="animate-spin" /> {isMatching ? 'Re-matching…' : 'Matching…'}
                </span>
              )}
            </div>
          </div>

          {/* Rematch button */}
          <button
            onClick={handleRematch}
            disabled={rematching || isBusy}
            className="btn-secondary text-xs py-1.5 px-2 flex-shrink-0"
            title="Re-check library & push to Jellyfin"
          >
            {rematching ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
          </button>

          {/* Delete button */}
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="btn-secondary text-xs py-1.5 px-2 flex-shrink-0"
            title={confirmDel ? 'Click again to confirm' : 'Delete'}
            style={confirmDel ? { borderColor: 'rgba(248,113,113,0.4)', color: 'var(--danger)' } : {}}
          >
            {deleting ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
          </button>
        </div>

        {/* Delete confirm */}
        {confirmDel && !deleting && (
          <div
            className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg anim-scale-in text-xs"
            style={{ background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.2)' }}
            onClick={e => e.stopPropagation()}
          >
            <span style={{ color: 'var(--danger)' }}>Delete this import?</span>
            <button onClick={(e) => { e.stopPropagation(); setConfirm(false) }} className="font-medium" style={{ color: 'var(--text-muted)' }}>
              Cancel
            </button>
          </div>
        )}

        {/* Progress bar */}
        <MatchBar matched={playlist.matched_count} total={playlist.track_count} />

        {/* Stats row */}
        <div className="flex items-center gap-3">
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {playlist.track_count} tracks
          </span>
          {missingCount > 0 && !isBusy && (
            <span className="text-[10px] font-semibold" style={{ color: 'var(--danger)' }}>
              {missingCount} missing
            </span>
          )}
          {pct === 100 && (
            <span className="text-[10px] font-semibold" style={{ color: 'var(--accent)' }}>
              Complete
            </span>
          )}
        </div>
      </div>

      {/* Footer action — visible call-to-action */}
      {!isBusy && (
        <div
          className="flex items-center justify-between px-4 py-2.5 transition-colors"
          style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-elevated)' }}
          onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-overlay)'}
          onMouseLeave={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
        >
          <span className="text-[11px] font-semibold" style={{ color: missingCount > 0 ? 'var(--accent)' : 'var(--text-secondary)' }}>
            {missingCount > 0 ? 'View & fill missing tracks' : 'View playlist details'}
          </span>
          <ChevronRight size={12} style={{ color: 'var(--text-muted)' }} />
        </div>
      )}
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function PlaylistImport() {
  const navigate = useNavigate()
  const [playlists, setPlaylists] = useState([])
  const [loading, setLoading]     = useState(true)

  const loadPlaylists = useCallback(async () => {
    try {
      const data = await api.get('/api/import/playlists')
      setPlaylists(data)
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => {
    loadPlaylists()
    // Poll every 5s to pick up new playlists from the browser extension
    const interval = setInterval(() => {
      api.get('/api/import/playlists').then(data => setPlaylists(data)).catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [loadPlaylists])

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap anim-fade-up">
        <div>
          <h1 style={{ fontFamily: 'Syne', fontWeight: 800, fontSize: 26, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
            Playlist Import
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
            Bring your playlists from Spotify, Tidal, or YouTube Music into Jellyfin.
          </p>
        </div>
        <button
          onClick={() => navigate('/import/setup')}
          className="btn-secondary text-xs flex items-center gap-1.5"
        >
          <Settings2 size={11} /> Extension Setup
        </button>
      </div>

      {/* Import form */}
      <div className="anim-fade-up" style={{ animationDelay: '50ms' }}>
        <ImportForm onImported={loadPlaylists} />
      </div>

      {/* Playlist grid */}
      <div className="anim-fade-up" style={{ animationDelay: '100ms' }}>
        {loading ? (
          <div className="flex items-center gap-2 py-8 justify-center">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
            <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Loading playlists…</span>
          </div>
        ) : playlists.length === 0 ? (
          <div className="card flex flex-col items-center justify-center py-16 gap-3 text-center anim-scale-in">
            <ArrowDownToLine size={28} strokeWidth={1.25} style={{ color: 'var(--text-muted)' }} />
            <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No imported playlists yet</div>
            <div className="text-xs max-w-xs" style={{ color: 'var(--text-muted)' }}>
              Paste a URL above or use the browser extension to import a playlist.
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger">
            {playlists.map(pl => (
              <PlaylistCard
                key={pl.id}
                playlist={pl}
                onOpen={p => navigate(`/import/${p.id}`)}
                onDelete={loadPlaylists}
                onRematched={loadPlaylists}
                onRenamed={updated => setPlaylists(prev => prev.map(p => p.id === updated.id ? updated : p))}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
