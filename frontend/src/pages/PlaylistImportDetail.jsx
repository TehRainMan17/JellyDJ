/**
 * PlaylistImportDetail.jsx — Detail page for a single imported playlist
 *
 * Route: /import/:id
 *
 * Tabs:
 *  1. All Tracks — full track listing with match status
 *  2. Matched — tracks found in your Jellyfin library
 *  3. Missing — tracks not yet in your library
 *  4. Album Suggestions — albums to fetch via Lidarr to fill gaps
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../lib/api.js'

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
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      border: `1px solid ${color}40`,
      color,
      background: `${color}18`,
    }}>
      {label}
    </span>
  )
}

// ── Progress bar ────────────────────────────────────────────────────────────

function MatchBar({ matched, total }) {
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0
  const color = pct >= 80 ? '#4ade80' : pct >= 50 ? '#facc15' : '#f87171'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 8, background: 'var(--color-border-tertiary)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width 0.4s' }} />
      </div>
      <span style={{ fontSize: 13, color: 'var(--color-text-secondary)', minWidth: 80 }}>
        {matched}/{total} ({pct}%)
      </span>
    </div>
  )
}

// ── Status colors ───────────────────────────────────────────────────────────

const STATUS_COLORS = {
  pending:     'var(--color-text-secondary)',
  approved:    '#4ade80',
  rejected:    '#f87171',
  downloading: '#facc15',
  complete:    '#4ade80',
}

// ── Manual match modal ──────────────────────────────────────────────────────

function ManualMatchModal({ trackName, playlistId, onClose, onMatched }) {
  const [query, setQuery] = useState(trackName)
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [matching, setMatching] = useState(false)

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    const t = setTimeout(async () => {
      setSearching(true)
      try {
        const data = await api.get(`/api/import/playlists/${playlistId}/library-search?q=${encodeURIComponent(query.trim())}`)
        setResults(data || [])
      } catch { setResults([]) }
      setSearching(false)
    }, 300)
    return () => clearTimeout(t)
  }, [query, playlistId])

  async function selectTrack(item) {
    setMatching(true)
    try {
      await api.post(`/api/import/playlists/${playlistId}/tracks/manual-match`, {
        track_name: trackName,
        library_item_id: item.item_id,
      })
      onMatched()
    } catch (e) { alert(e.message); setMatching(false) }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(9, 11, 34, 0.80)',
        backdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        className="anim-scale-in"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-mid)',
          borderRadius: 18,
          padding: '24px 24px 20px',
          width: 500,
          maxWidth: '92vw',
          maxHeight: '74vh',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          boxShadow: '0 32px 72px rgba(0,0,0,0.55), 0 0 0 1px rgba(162,143,251,0.08)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, color: 'var(--text-primary)', letterSpacing: '-0.2px' }}>
              Find in Library
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.4 }}>
              Matching{' '}
              <span style={{ color: 'var(--purple)', fontWeight: 600 }}>"{trackName}"</span>
              {' '}to a track you already have
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              width: 28, height: 28, borderRadius: 8,
              border: '1px solid var(--border)',
              background: 'rgba(255,255,255,0.04)',
              color: 'var(--text-muted)',
              fontSize: 14, cursor: 'pointer', flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {/* Search input */}
        <input
          autoFocus
          className="input"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search by track name…"
        />

        {/* Results */}
        <div style={{ overflowY: 'auto', flex: 1, minHeight: 120, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {searching && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '10px 2px' }}>Searching…</div>
          )}
          {!searching && results.length === 0 && query.trim() && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '10px 2px' }}>No matches in your library</div>
          )}
          {!searching && !query.trim() && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', padding: '10px 2px' }}>Type to search your library</div>
          )}
          {results.map(r => (
            <div
              key={r.item_id}
              onClick={() => !matching && selectTrack(r)}
              style={{
                padding: '10px 14px',
                borderRadius: 12,
                border: '1px solid var(--border)',
                background: 'var(--bg-surface)',
                cursor: matching ? 'wait' : 'pointer',
                display: 'flex', flexDirection: 'column', gap: 3,
                transition: 'border-color 120ms ease, background 120ms ease',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.borderColor = 'rgba(83, 236, 252, 0.45)'
                e.currentTarget.style.background = 'var(--bg-overlay)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.background = 'var(--bg-surface)'
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>{r.track_name}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {r.artist_name}{r.album_name ? <span style={{ color: 'var(--border-mid)' }}> — </span> : ''}{r.album_name}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 4, borderTop: '1px solid var(--border)' }}>
          <button className="btn-secondary" onClick={onClose} style={{ fontSize: 12, padding: '6px 16px' }}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Suggestion row ──────────────────────────────────────────────────────────

function SuggestionRow({ suggestion, playlistId, onUpdate }) {
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [matchingTrack, setMatchingTrack] = useState(null)

  async function approve() {
    setLoading(true)
    try {
      await api.post(`/api/import/playlists/${playlistId}/suggestions/${suggestion.id}/approve`)
      onUpdate()
    } catch (e) { alert(e.message) }
    setLoading(false)
  }

  async function reject() {
    setLoading(true)
    try {
      await api.post(`/api/import/playlists/${playlistId}/suggestions/${suggestion.id}/reject`)
      onUpdate()
    } catch (e) { alert(e.message) }
    setLoading(false)
  }

  const isPending = suggestion.lidarr_status === 'pending' || suggestion.lidarr_status === 'rejected'
  const isRequested = suggestion.lidarr_status === 'approved'
  const isPlaceholder = suggestion.album_name === 'Artist not in Lidarr' || suggestion.album_name === 'Unknown Album' || suggestion.album_name === 'No albums tracked in Lidarr'
  const canFetch = (isPending || isRequested) && !isPlaceholder
  const trackList = suggestion.missing_tracks || []

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '1px solid var(--color-border-tertiary)',
      borderRadius: 8,
      marginBottom: 10,
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '14px 16px',
        cursor: trackList.length > 0 ? 'pointer' : 'default',
      }} onClick={() => trackList.length > 0 && setExpanded(!expanded)}>
        {/* Album art or placeholder */}
        {suggestion.image_url ? (
          <img
            src={suggestion.image_url}
            alt=""
            style={{ width: 48, height: 48, borderRadius: 6, objectFit: 'cover', flexShrink: 0 }}
          />
        ) : (
          <div style={{
            width: 48, height: 48, borderRadius: 6, flexShrink: 0,
            background: 'var(--color-border-tertiary)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, color: 'var(--color-text-tertiary)',
          }}>
            ♪
          </div>
        )}

        {/* Artist + album info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{suggestion.artist_name}</div>
          <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginTop: 2 }}>
            {suggestion.album_name || 'Unknown album'}
          </div>
        </div>

        {/* Coverage badge */}
        <div style={{
          fontSize: 12, whiteSpace: 'nowrap',
          padding: '3px 10px', borderRadius: 12,
          background: '#6366f118', color: '#6366f1',
          border: '1px solid #6366f130',
          fontWeight: 600,
        }}>
          {suggestion.coverage_count} track{suggestion.coverage_count !== 1 ? 's' : ''}
        </div>

        {/* Status — only show for terminal states (downloading / complete) */}
        {!isPending && !isRequested && (
          <div style={{
            fontSize: 12, minWidth: 80, textAlign: 'right',
            color: STATUS_COLORS[suggestion.lidarr_status] || '#888',
            fontWeight: 500,
          }}>
            {suggestion.lidarr_status}
          </div>
        )}

        {/* Action buttons */}
        {canFetch && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            {isRequested && (
              <span style={{
                fontSize: 10, fontWeight: 600, letterSpacing: '0.3px',
                color: '#facc15', display: 'flex', alignItems: 'center', gap: 3,
              }}>
                ✓ Requested
              </span>
            )}
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={(e) => { e.stopPropagation(); approve() }}
                disabled={loading}
                style={{ padding: '6px 16px', borderRadius: 6, border: 'none', background: '#6366f1', color: 'white', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
              >
                {loading ? '…' : 'Fetch'}
              </button>
              {!isRequested && (
                <button
                  onClick={(e) => { e.stopPropagation(); reject() }}
                  disabled={loading}
                  style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 12, cursor: 'pointer' }}
                >
                  Skip
                </button>
              )}
            </div>
          </div>
        )}
        {suggestion.album_name === 'Artist not in Lidarr' && isPending && (
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', fontStyle: 'italic', whiteSpace: 'nowrap' }}>
            Add artist to Lidarr first
          </span>
        )}
        {suggestion.album_name === 'No albums tracked in Lidarr' && isPending && (
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', fontStyle: 'italic', whiteSpace: 'nowrap' }}>
            Artist in Lidarr — add an album to track
          </span>
        )}

        {/* Expand indicator */}
        {trackList.length > 0 && (
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', flexShrink: 0 }}>
            {expanded ? '▾' : '▸'}
          </span>
        )}
      </div>

      {/* Expandable track list */}
      {expanded && trackList.length > 0 && (
        <div style={{
          padding: '0 16px 14px 78px',
          borderTop: '1px solid var(--color-border-tertiary)',
          paddingTop: 10,
        }}>
          <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Missing tracks this album would fill
          </div>
          {trackList.map((name, i) => (
            <div key={i} style={{
              fontSize: 12, color: 'var(--text-secondary)',
              padding: '4px 0',
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <span style={{ color: 'var(--danger)', fontSize: 9, flexShrink: 0 }}>●</span>
              <span style={{ flex: 1, color: 'var(--text-secondary)' }}>{name}</span>
              <button
                onClick={e => { e.stopPropagation(); setMatchingTrack(name) }}
                style={{
                  padding: '3px 10px',
                  borderRadius: 20,
                  fontSize: 11,
                  fontWeight: 600,
                  border: '1px solid rgba(83, 236, 252, 0.28)',
                  background: 'rgba(83, 236, 252, 0.07)',
                  color: 'var(--accent)',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                  letterSpacing: '0.1px',
                  transition: 'background 120ms ease, border-color 120ms ease',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = 'rgba(83, 236, 252, 0.14)'
                  e.currentTarget.style.borderColor = 'rgba(83, 236, 252, 0.5)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'rgba(83, 236, 252, 0.07)'
                  e.currentTarget.style.borderColor = 'rgba(83, 236, 252, 0.28)'
                }}
              >
                Match
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Manual match modal */}
      {matchingTrack && (
        <ManualMatchModal
          trackName={matchingTrack}
          playlistId={playlistId}
          onClose={() => setMatchingTrack(null)}
          onMatched={() => { setMatchingTrack(null); onUpdate() }}
        />
      )}
    </div>
  )
}

// ── Track row ───────────────────────────────────────────────────────────────

function TrackRow({ track, showStatus }) {
  const isMatched = track.match_status === 'matched'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 16px',
      background: 'var(--color-background-secondary)',
      border: '1px solid var(--color-border-tertiary)',
      borderRadius: 8,
      marginBottom: 6,
    }}>
      <div style={{
        width: 28, height: 28, borderRadius: '50%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 11, fontWeight: 600,
        background: isMatched ? '#4ade8020' : 'var(--color-border-tertiary)',
        color: isMatched ? '#4ade80' : 'var(--color-text-tertiary)',
        flexShrink: 0,
      }}>
        {track.position || '–'}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{track.track_name}</div>
        <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
          {track.artist_name}{track.album_name ? ` · ${track.album_name}` : ''}
        </div>
      </div>
      {showStatus && (
        <span style={{
          fontSize: 11, flexShrink: 0, padding: '2px 8px', borderRadius: 4,
          background: isMatched ? '#4ade8018' : '#f8717118',
          color: isMatched ? '#4ade80' : '#f87171',
          border: `1px solid ${isMatched ? '#4ade8030' : '#f8717130'}`,
        }}>
          {isMatched
            ? (track.added_to_playlist ? 'In playlist' : 'Matched')
            : (track.lidarr_requested ? 'Lidarr queued' : 'Missing')}
        </span>
      )}
    </div>
  )
}

// ── Tab button ──────────────────────────────────────────────────────────────

function TabButton({ active, label, count, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '10px 16px',
        border: 'none',
        borderBottom: active ? '2px solid #6366f1' : '2px solid transparent',
        background: 'transparent',
        color: active ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
        fontSize: 13,
        fontWeight: active ? 600 : 400,
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {label}{count != null ? ` (${count})` : ''}
    </button>
  )
}

// ── Main detail page ────────────────────────────────────────────────────────

export default function PlaylistImportDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail]           = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [tab, setTab]                 = useState('all')
  const [loading, setLoading]         = useState(true)
  const [deleting, setDeleting]       = useState(false)
  const [rematching, setRematching]   = useState(false)
  const [addingArtists, setAddingArtists] = useState(false)
  const [renaming, setRenaming]       = useState(false)
  const [renameVal, setRenameVal]     = useState('')
  const [renameSaving, setRenameSaving] = useState(false)
  const renameInputRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const [det, sugg] = await Promise.all([
        api.get(`/api/import/playlists/${id}`),
        api.get(`/api/import/playlists/${id}/suggestions`),
      ])
      setDetail(det)
      setSuggestions(sugg)
    } catch {
      navigate('/import', { replace: true })
    }
    setLoading(false)
  }, [id, navigate])

  useEffect(() => { load() }, [load])

  async function handleDelete() {
    if (!confirm(`Delete "${detail?.name}"? This removes the import record but leaves any Jellyfin playlist intact.`)) return
    setDeleting(true)
    try {
      await api.delete(`/api/import/playlists/${id}`)
      navigate('/import', { replace: true })
    } catch (err) {
      alert('Failed to delete: ' + err.message)
      setDeleting(false)
    }
  }

  async function handleRematch() {
    setRematching(true)
    try {
      await api.post(`/api/import/playlists/${id}/rematch`)
      // Poll until backend finishes (status goes back to "active")
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        try {
          const [det, sugg] = await Promise.all([
            api.get(`/api/import/playlists/${id}`),
            api.get(`/api/import/playlists/${id}/suggestions`),
          ])
          setDetail(det)
          setSuggestions(sugg)
          if (det.status === 'active' || det.status === 'error' || attempts >= 90) {
            clearInterval(poll)
            setRematching(false)
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

  async function handleAddArtists() {
    setAddingArtists(true)
    try {
      await api.post(`/api/import/playlists/${id}/add-artists`)
      // Poll for rebuilt suggestions
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        try {
          const sugg = await api.get(`/api/import/playlists/${id}/suggestions`)
          setSuggestions(sugg)
          // Stop when no more "Artist not in Lidarr" or after 90s
          const stillMissing = sugg.some(s => s.album_name === 'Artist not in Lidarr')
          if (!stillMissing || attempts >= 45) {
            clearInterval(poll)
            setAddingArtists(false)
            // Also refresh detail
            try {
              const det = await api.get(`/api/import/playlists/${id}`)
              setDetail(det)
            } catch {}
          }
        } catch {
          clearInterval(poll)
          setAddingArtists(false)
        }
      }, 2000)
    } catch (err) {
      alert('Failed to add artists: ' + err.message)
      setAddingArtists(false)
    }
  }

  function startRename() {
    setRenameVal(detail?.name || '')
    setRenaming(true)
    setTimeout(() => renameInputRef.current?.focus(), 0)
  }

  async function saveRename() {
    const trimmed = renameVal.trim()
    if (!trimmed || trimmed === detail?.name) { setRenaming(false); return }
    setRenameSaving(true)
    try {
      const updated = await api.patch(`/api/import/playlists/${id}/rename`, { name: trimmed })
      setDetail(prev => ({ ...prev, name: updated.name }))
      setRenaming(false)
      if (updated.jellyfin_error) {
        alert(`Renamed in JellyDJ, but Jellyfin sync failed:\n${updated.jellyfin_error}`)
      }
    } catch (err) {
      alert('Rename failed: ' + err.message)
    }
    setRenameSaving(false)
  }

  function handleRenameKeyDown(e) {
    if (e.key === 'Enter') saveRename()
    if (e.key === 'Escape') { setRenaming(false); setRenameVal(detail?.name || '') }
  }

  if (loading) return (
    <div style={{ padding: 24, color: 'var(--color-text-secondary)' }}>Loading…</div>
  )

  if (!detail) return null

  const tracks    = detail.tracks || []
  const matched   = tracks.filter(t => t.match_status === 'matched')
  const missing   = tracks.filter(t => t.match_status === 'missing')

  const tabList = tab === 'all' ? tracks
    : tab === 'matched' ? matched
    : tab === 'missing' ? missing
    : []

  return (
    <div style={{ padding: '24px', maxWidth: 860 }}>
      {/* Back link */}
      <button
        onClick={() => navigate('/import')}
        style={{
          background: 'none', border: 'none', color: 'var(--color-text-secondary)',
          cursor: 'pointer', fontSize: 13, padding: 0, marginBottom: 16,
          display: 'flex', alignItems: 'center', gap: 4,
        }}
      >
        ← Back to imports
      </button>

      {/* Header */}
      <div style={{
        background: 'var(--color-background-secondary)',
        border: '1px solid var(--color-border-tertiary)',
        borderRadius: 12,
        padding: '20px 24px',
        marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <PlatformBadge platform={detail.source_platform} />
              {detail.jellyfin_playlist_id && (
                <span style={{ fontSize: 11, color: '#4ade80' }}>In Jellyfin</span>
              )}
              {detail.status === 'pending' && (
                <span style={{ fontSize: 11, color: '#facc15' }}>Matching…</span>
              )}
            </div>
            {renaming ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <input
                  ref={renameInputRef}
                  value={renameVal}
                  onChange={e => setRenameVal(e.target.value)}
                  onKeyDown={handleRenameKeyDown}
                  className="input"
                  style={{ fontSize: 18, fontWeight: 600, flex: 1 }}
                />
                <button
                  onClick={saveRename}
                  disabled={renameSaving}
                  style={{ padding: '6px 14px', borderRadius: 6, border: '1px solid var(--accent)', background: 'transparent', color: 'var(--accent)', fontSize: 12, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}
                >
                  {renameSaving ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={() => { setRenaming(false); setRenameVal(detail?.name || '') }}
                  style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 12, cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, cursor: 'default' }}>
                <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>{detail.name}</h1>
                <button
                  onClick={startRename}
                  title="Rename playlist"
                  style={{ padding: '3px 8px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 11, cursor: 'pointer', opacity: 0.6 }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >
                  Rename
                </button>
              </div>
            )}
            <MatchBar matched={detail.matched_count} total={detail.track_count} />
          </div>
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            onClick={handleRematch}
            disabled={rematching}
            style={{
              padding: '7px 14px', borderRadius: 6,
              border: '1px solid var(--color-border-secondary)',
              background: 'transparent',
              color: 'var(--color-text-secondary)',
              fontSize: 12, fontWeight: 600,
              cursor: rematching ? 'not-allowed' : 'pointer',
            }}
          >
            {rematching ? 'Re-matching…' : 'Re-match library'}
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            style={{
              padding: '7px 14px', borderRadius: 6,
              border: '1px solid #f87171',
              background: 'transparent',
              color: '#f87171',
              fontSize: 12, fontWeight: 600,
              cursor: deleting ? 'not-allowed' : 'pointer',
              marginLeft: 'auto',
            }}
          >
            {deleting ? 'Deleting…' : 'Delete import'}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{
        display: 'flex', gap: 0,
        borderBottom: '1px solid var(--color-border-tertiary)',
        marginBottom: 16,
      }}>
        <TabButton active={tab === 'all'}         label="All Tracks"        count={tracks.length}      onClick={() => setTab('all')} />
        <TabButton active={tab === 'matched'}     label="Matched"           count={matched.length}     onClick={() => setTab('matched')} />
        <TabButton active={tab === 'missing'}     label="Missing"           count={missing.length}     onClick={() => setTab('missing')} />
        <TabButton active={tab === 'suggestions'} label="Album Suggestions" count={suggestions.length} onClick={() => setTab('suggestions')} />
      </div>

      {/* Tab content */}
      {tab === 'suggestions' ? (
        <div>
          {suggestions.length === 0 ? (
            <div style={{
              textAlign: 'center', padding: '40px 24px',
              color: 'var(--color-text-tertiary)',
            }}>
              {missing.length === 0
                ? 'All tracks matched — no suggestions needed!'
                : 'No album suggestions available yet.'}
            </div>
          ) : (
            <>
              {/* Add all new artists button */}
              {(() => {
                const notInLidarr = suggestions.filter(s => s.album_name === 'Artist not in Lidarr')
                if (notInLidarr.length === 0) return null
                return (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '12px 16px',
                    background: '#6366f10c',
                    border: '1px solid #6366f125',
                    borderRadius: 8,
                    marginBottom: 14,
                  }}>
                    <div style={{ flex: 1, fontSize: 13, color: 'var(--color-text-secondary)' }}>
                      <strong>{notInLidarr.length}</strong> artist{notInLidarr.length !== 1 ? 's' : ''} not yet in Lidarr.
                      Add them to see which albums are available.
                    </div>
                    <button
                      onClick={handleAddArtists}
                      disabled={addingArtists}
                      style={{
                        padding: '7px 18px', borderRadius: 6, border: 'none',
                        background: '#6366f1', color: 'white',
                        fontSize: 12, fontWeight: 600,
                        cursor: addingArtists ? 'not-allowed' : 'pointer',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {addingArtists ? 'Adding…' : `Add all ${notInLidarr.length} to Lidarr`}
                    </button>
                  </div>
                )
              })()}
              <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 12, lineHeight: 1.5 }}>
                Each album below fills one or more missing tracks when downloaded via Lidarr.
                Click <strong>Fetch</strong> to queue it.
              </p>
              {suggestions.map(s => (
                <SuggestionRow key={s.id} suggestion={s} playlistId={detail.id} onUpdate={load} />
              ))}
            </>
          )}
        </div>
      ) : (
        <div>
          {tabList.length === 0 ? (
            <div style={{
              textAlign: 'center', padding: '40px 24px',
              color: 'var(--color-text-tertiary)',
            }}>
              {tab === 'matched' ? 'No matched tracks yet.' : 'No missing tracks — everything is matched!'}
            </div>
          ) : (
            tabList.map(t => (
              <TrackRow key={t.id} track={t} showStatus={tab === 'all'} />
            ))
          )}
        </div>
      )}
    </div>
  )
}
