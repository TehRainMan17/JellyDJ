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

import { useState, useEffect, useCallback } from 'react'
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

// ── Suggestion row ──────────────────────────────────────────────────────────

function SuggestionRow({ suggestion, playlistId, onUpdate }) {
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)

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
  const isPlaceholder = suggestion.album_name === 'Artist not in Lidarr' || suggestion.album_name === 'Unknown Album'
  const canFetch = isPending && !isPlaceholder
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

        {/* Status — only show when NOT pending (pending shows Fetch/Skip buttons instead) */}
        {!isPending && (
          <div style={{
            fontSize: 12, minWidth: 80, textAlign: 'right',
            color: STATUS_COLORS[suggestion.lidarr_status] || '#888',
            fontWeight: 500,
          }}>
            {suggestion.lidarr_status === 'approved' ? 'Sent to Lidarr' : suggestion.lidarr_status}
          </div>
        )}

        {/* Action buttons */}
        {canFetch && (
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={(e) => { e.stopPropagation(); approve() }}
              disabled={loading}
              style={{ padding: '6px 16px', borderRadius: 6, border: 'none', background: '#6366f1', color: 'white', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
            >
              {loading ? '…' : 'Fetch'}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); reject() }}
              disabled={loading}
              style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 12, cursor: 'pointer' }}
            >
              Skip
            </button>
          </div>
        )}
        {isPlaceholder && isPending && (
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', fontStyle: 'italic', whiteSpace: 'nowrap' }}>
            Add artist to Lidarr first
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
              fontSize: 12, color: 'var(--color-text-secondary)',
              padding: '3px 0',
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <span style={{ color: '#f87171', fontSize: 10 }}>●</span>
              {name}
            </div>
          ))}
        </div>
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
            <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>{detail.name}</h1>
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
