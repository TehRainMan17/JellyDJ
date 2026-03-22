/**
 * PlaylistImport.jsx — Playlist Import page
 *
 * Route: /import  (add to App.jsx and Layout.jsx)
 *
 * Sections:
 *  1. URL paste form (alternative to browser extension)
 *  2. List of imported playlists with match progress
 *  3. Detail drawer: track list + album suggestions per playlist
 */

import { useState, useEffect, useCallback } from 'react'
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
      <div style={{ flex: 1, height: 6, background: 'var(--color-border-tertiary)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3, transition: 'width 0.4s' }} />
      </div>
      <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', minWidth: 60 }}>
        {matched}/{total} ({pct}%)
      </span>
    </div>
  )
}

// ── Album suggestion row ────────────────────────────────────────────────────

const STATUS_COLORS = {
  pending:     'var(--color-text-secondary)',
  approved:    '#4ade80',
  rejected:    '#f87171',
  downloading: '#facc15',
  complete:    '#4ade80',
}

function SuggestionRow({ suggestion, playlistId, onUpdate }) {
  const [loading, setLoading] = useState(false)

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

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 0',
      borderBottom: '1px solid var(--color-border-tertiary)',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, fontSize: 13 }}>{suggestion.artist_name}</div>
        <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
          {suggestion.album_name}
        </div>
      </div>
      <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', whiteSpace: 'nowrap' }}>
        fills {suggestion.coverage_count} track{suggestion.coverage_count !== 1 ? 's' : ''}
      </div>
      <div style={{ fontSize: 12, color: STATUS_COLORS[suggestion.lidarr_status] || '#888', minWidth: 80, textAlign: 'right' }}>
        {suggestion.lidarr_status}
      </div>
      {isPending && (
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            onClick={approve}
            disabled={loading}
            style={{ padding: '4px 12px', borderRadius: 6, border: 'none', background: '#6366f1', color: 'white', fontSize: 12, cursor: 'pointer' }}
          >
            {loading ? '…' : 'Fetch'}
          </button>
          <button
            onClick={reject}
            disabled={loading}
            style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 12, cursor: 'pointer' }}
          >
            Skip
          </button>
        </div>
      )}
    </div>
  )
}

// ── Playlist detail panel ───────────────────────────────────────────────────

function PlaylistDetail({ playlist, onClose, onRematch }) {
  const [detail, setDetail]           = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [tab, setTab]                 = useState('missing') // 'missing' | 'matched' | 'suggestions'
  const [loading, setLoading]         = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const [det, sugg] = await Promise.all([
      api.get(`/api/import/playlists/${playlist.id}`),
      api.get(`/api/import/playlists/${playlist.id}/suggestions`),
    ])
    setDetail(det)
    setSuggestions(sugg)
    setLoading(false)
  }, [playlist.id])

  useEffect(() => { load() }, [load])

  if (loading) return (
    <div style={{ padding: 24, color: 'var(--color-text-secondary)' }}>Loading…</div>
  )

  const tracks         = detail?.tracks || []
  const matched        = tracks.filter(t => t.match_status === 'matched')
  const missing        = tracks.filter(t => t.match_status === 'missing')
  const hasSuggestions = suggestions.length > 0

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, width: 480, height: '100vh',
      background: 'var(--color-background-primary)',
      borderLeft: '1px solid var(--color-border-tertiary)',
      overflowY: 'auto', zIndex: 50, display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--color-border-tertiary)', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PlatformBadge platform={playlist.source_platform} />
          <h2 style={{ fontSize: 16, fontWeight: 600, marginTop: 6, marginBottom: 4 }}>{playlist.name}</h2>
          <MatchBar matched={playlist.matched_count} total={playlist.track_count} />
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--color-text-secondary)', cursor: 'pointer', fontSize: 18, lineHeight: 1 }}>✕</button>
      </div>

      {/* Actions */}
      <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--color-border-tertiary)', display: 'flex', gap: 8 }}>
        {playlist.jellyfin_playlist_id && (
          <span style={{ fontSize: 12, color: '#4ade80', alignSelf: 'center' }}>✓ Jellyfin playlist active</span>
        )}
        <button
          onClick={onRematch}
          style={{ marginLeft: 'auto', padding: '5px 12px', borderRadius: 6, border: '1px solid var(--color-border-secondary)', background: 'transparent', color: 'var(--color-text-secondary)', fontSize: 12, cursor: 'pointer' }}
        >
          Re-match library
        </button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--color-border-tertiary)' }}>
        {[
          ['missing',     `Missing (${missing.length})`],
          ['matched',     `Matched (${matched.length})`],
          ['suggestions', `Fetch gaps${hasSuggestions ? ` (${suggestions.length})` : ''}`],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            style={{
              flex: 1, padding: '10px 8px', border: 'none', borderBottom: tab === key ? '2px solid #6366f1' : '2px solid transparent',
              background: 'transparent', color: tab === key ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
              fontSize: 12, fontWeight: tab === key ? 600 : 400, cursor: 'pointer',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px' }}>
        {tab === 'suggestions' && (
          <div style={{ paddingTop: 4 }}>
            {suggestions.length === 0 ? (
              <p style={{ padding: '20px 0', color: 'var(--color-text-secondary)', fontSize: 13 }}>
                {missing.length === 0 ? '🎉 All tracks matched!' : 'No album suggestions available yet.'}
              </p>
            ) : (
              <>
                <p style={{ padding: '10px 0', fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
                  Each row is an album that, when downloaded by Lidarr, fills one or more missing tracks.
                  Click <strong>Fetch</strong> to queue the album in Lidarr — tracks will be added to your playlist automatically once indexed.
                </p>
                {suggestions.map(s => (
                  <SuggestionRow key={s.id} suggestion={s} playlistId={playlist.id} onUpdate={load} />
                ))}
              </>
            )}
          </div>
        )}

        {tab === 'missing' && (
          <div>
            {missing.length === 0 ? (
              <p style={{ padding: '20px 0', color: 'var(--color-text-secondary)', fontSize: 13 }}>No missing tracks.</p>
            ) : missing.map(t => (
              <div key={t.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--color-border-tertiary)' }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{t.track_name}</div>
                <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{t.artist_name}{t.album_name ? ` · ${t.album_name}` : ''}</div>
                {t.lidarr_requested && <span style={{ fontSize: 11, color: '#facc15' }}>⏳ Lidarr queued</span>}
              </div>
            ))}
          </div>
        )}

        {tab === 'matched' && (
          <div>
            {matched.length === 0 ? (
              <p style={{ padding: '20px 0', color: 'var(--color-text-secondary)', fontSize: 13 }}>No matched tracks yet.</p>
            ) : matched.map(t => (
              <div key={t.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--color-border-tertiary)', display: 'flex', gap: 8, alignItems: 'center' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{t.track_name}</div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{t.artist_name}</div>
                </div>
                <span style={{ fontSize: 11, color: t.added_to_playlist ? '#4ade80' : 'var(--color-text-tertiary)' }}>
                  {t.added_to_playlist ? '✓ in playlist' : '+ pending'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── API Key Management ──────────────────────────────────────────────────────

function ApiKeySection() {
  const [keyData, setKeyData]     = useState(null)      // { id, prefix, created_at, last_used_at }
  const [newKey, setNewKey]       = useState(null)      // full key after generate/reroll
  const [showNewKey, setShowNewKey] = useState(false)  // banner visibility
  const [copied, setCopied]       = useState(false)
  const [loading, setLoading]     = useState(true)
  const [generating, setGenerating] = useState(false)
  const [revoking, setRevoking]   = useState(false)

  const loadKeys = useCallback(async () => {
    setLoading(true)
    try {
      const keys = await api.get('/api/import/api-keys')
      setKeyData(keys.length > 0 ? keys[0] : null)
      setShowNewKey(false)
    } catch (err) {
      console.error('Failed to load API keys:', err)
    }
    setLoading(false)
  }, [])

  useEffect(() => { loadKeys() }, [loadKeys])

  async function handleGenerate() {
    setGenerating(true)
    try {
      const result = await api.post('/api/import/api-keys', {})
      setNewKey(result.key)
      setShowNewKey(true)
      await loadKeys()
    } catch (err) {
      alert('Failed to generate key: ' + err.message)
    }
    setGenerating(false)
  }

  async function handleReroll() {
    if (!keyData || !confirm('Old key will be immediately invalid. Continue?')) return
    setGenerating(true)
    try {
      const result = await api.post(`/api/import/api-keys/${keyData.id}/reroll`, {})
      setNewKey(result.key)
      setShowNewKey(true)
      await loadKeys()
    } catch (err) {
      alert('Failed to reroll key: ' + err.message)
    }
    setGenerating(false)
  }

  async function handleRevoke() {
    if (!keyData || !confirm('This key will be permanently invalid. Continue?')) return
    setRevoking(true)
    try {
      await api.delete(`/api/import/api-keys/${keyData.id}`)
      setKeyData(null)
      setShowNewKey(false)
    } catch (err) {
      alert('Failed to revoke key: ' + err.message)
    }
    setRevoking(false)
  }

  function copyKey() {
    navigator.clipboard.writeText(newKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const dateFormatter = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })

  return (
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '1px solid var(--color-border-tertiary)',
      borderRadius: 12,
      padding: '20px 24px',
      marginBottom: 24,
    }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Browser Extension API Key</h2>
      <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 16, lineHeight: 1.5 }}>
        Generate an API key to use with the JellyDJ browser extension. This key gives access to your import endpoints.
        <br />
        <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>Keep it private and secure.</span>
      </p>

      {showNewKey && newKey && (
        <div style={{
          background: '#facc1520',
          border: '1px solid #facc15',
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 16,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}>
          <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', flex: 1 }}>
            Copy your key now — <strong>it will not be shown again</strong>
          </span>
          <code style={{
            background: 'rgba(0,0,0,0.2)',
            padding: '6px 10px',
            borderRadius: 4,
            fontFamily: 'monospace',
            fontSize: 11,
            maxWidth: 200,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            color: 'var(--color-text-primary)',
          }}>
            {newKey}
          </code>
          <button
            onClick={copyKey}
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              border: 'none',
              background: '#facc15',
              color: '#000',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      )}

      {loading ? (
        <p style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>Loading…</p>
      ) : keyData ? (
        <div>
          <div style={{
            background: 'var(--color-background-primary)',
            border: '1px solid var(--color-border-tertiary)',
            borderRadius: 8,
            padding: '12px 16px',
            marginBottom: 12,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <code style={{
                background: 'rgba(0,0,0,0.1)',
                padding: '4px 8px',
                borderRadius: 4,
                fontFamily: 'monospace',
                fontSize: 13,
                fontWeight: 600,
                color: '#6366f1',
              }}>
                {keyData.prefix}…
              </code>
              <span style={{ fontSize: 12, color: '#4ade80' }}>✓ Active</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', lineHeight: 1.6 }}>
              {keyData.created_at && (
                <div>Created: {dateFormatter.format(new Date(keyData.created_at))}</div>
              )}
              {keyData.last_used_at && (
                <div>Last used: {dateFormatter.format(new Date(keyData.last_used_at))}</div>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleReroll}
              disabled={generating}
              style={{
                padding: '8px 14px',
                borderRadius: 6,
                border: '1px solid var(--color-border-secondary)',
                background: 'transparent',
                color: 'var(--color-text-secondary)',
                fontSize: 12,
                fontWeight: 600,
                cursor: generating ? 'not-allowed' : 'pointer',
              }}
            >
              {generating ? '…' : 'Reroll'}
            </button>
            <button
              onClick={handleRevoke}
              disabled={revoking}
              style={{
                padding: '8px 14px',
                borderRadius: 6,
                border: '1px solid #f87171',
                background: 'transparent',
                color: '#f87171',
                fontSize: 12,
                fontWeight: 600,
                cursor: revoking ? 'not-allowed' : 'pointer',
              }}
            >
              {revoking ? '…' : 'Revoke'}
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={handleGenerate}
          disabled={generating}
          style={{
            padding: '10px 16px',
            borderRadius: 8,
            border: 'none',
            background: generating ? 'var(--color-border-secondary)' : '#6366f1',
            color: 'white',
            fontSize: 13,
            fontWeight: 600,
            cursor: generating ? 'not-allowed' : 'pointer',
          }}
        >
          {generating ? 'Generating…' : 'Generate Key'}
        </button>
      )}
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
    <div style={{
      background: 'var(--color-background-secondary)',
      border: '1px solid var(--color-border-tertiary)',
      borderRadius: 12,
      padding: '20px 24px',
      marginBottom: 24,
    }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Import a playlist</h2>
      <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 16, lineHeight: 1.5 }}>
        Paste a public playlist URL from Spotify, Tidal, or YouTube Music.
        JellyDJ will match tracks against your library and suggest albums to fill the gaps.
        <br />
        <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>
          Or install the JellyDJ browser extension to import with one click from any playlist page.
        </span>
      </p>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 10 }}>
        <input
          type="url"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://open.spotify.com/playlist/…"
          style={{
            flex: 1,
            padding: '9px 14px',
            borderRadius: 8,
            border: '1px solid var(--color-border-secondary)',
            background: 'var(--color-background-primary)',
            color: 'var(--color-text-primary)',
            fontSize: 13,
            outline: 'none',
          }}
        />
        <button
          type="submit"
          disabled={loading || !url.trim()}
          style={{
            padding: '9px 20px', borderRadius: 8, border: 'none',
            background: loading ? 'var(--color-border-secondary)' : '#6366f1',
            color: 'white', fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? 'Importing…' : 'Import'}
        </button>
      </form>
      {error && <p style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-danger)' }}>{error}</p>}
    </div>
  )
}

// ── Playlist card ───────────────────────────────────────────────────────────

function PlaylistCard({ playlist, onClick }) {
  const isPending = playlist.status === 'pending'
  return (
    <div
      onClick={() => !isPending && onClick(playlist)}
      style={{
        background: 'var(--color-background-secondary)',
        border: '1px solid var(--color-border-tertiary)',
        borderRadius: 10,
        padding: '14px 18px',
        cursor: isPending ? 'default' : 'pointer',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={e => { if (!isPending) e.currentTarget.style.borderColor = 'var(--color-border-primary)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--color-border-tertiary)' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <PlatformBadge platform={playlist.source_platform} />
            {isPending && <span style={{ fontSize: 11, color: '#facc15' }}>⏳ matching…</span>}
          </div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{playlist.name}</div>
        </div>
        {playlist.jellyfin_playlist_id && (
          <span style={{ fontSize: 11, color: '#4ade80', flexShrink: 0 }}>✓ In Jellyfin</span>
        )}
      </div>
      <MatchBar matched={playlist.matched_count} total={playlist.track_count} />
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11, color: 'var(--color-text-tertiary)' }}>
        <span>{playlist.track_count} tracks</span>
        <span>{playlist.track_count - playlist.matched_count} missing</span>
      </div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function PlaylistImport() {
  const [playlists, setPlaylists] = useState([])
  const [selected, setSelected]   = useState(null)
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
    // Poll every 5s while any playlist is pending
    const interval = setInterval(() => {
      if (playlists.some(p => p.status === 'pending')) loadPlaylists()
    }, 5000)
    return () => clearInterval(interval)
  }, [loadPlaylists, playlists])

  async function handleRematch() {
    if (!selected) return
    await api.post(`/api/import/playlists/${selected.id}/rematch`)
    await loadPlaylists()
    // Reload selected with fresh data
    const fresh = await api.get(`/api/import/playlists/${selected.id}`)
    setSelected({ ...selected, matched_count: fresh.matched_count, track_count: fresh.track_count })
  }

  return (
    <div style={{ padding: '24px', maxWidth: 780, position: 'relative' }}>
      <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 6 }}>Playlist Import</h1>
      <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 24 }}>
        Bring your playlists from Spotify, Tidal, or YouTube Music into Jellyfin.
        Missing tracks are automatically filled as you download albums through Lidarr.
      </p>

      <ApiKeySection />

      <ImportForm onImported={loadPlaylists} />

      {loading ? (
        <p style={{ color: 'var(--color-text-secondary)', fontSize: 13 }}>Loading…</p>
      ) : playlists.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '48px 24px',
          border: '1px dashed var(--color-border-secondary)', borderRadius: 12,
          color: 'var(--color-text-tertiary)',
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>🎵</div>
          <p style={{ fontSize: 14 }}>No imported playlists yet.</p>
          <p style={{ fontSize: 13, marginTop: 4 }}>Paste a URL above or use the browser extension.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 14 }}>
          {playlists.map(pl => (
            <PlaylistCard key={pl.id} playlist={pl} onClick={setSelected} />
          ))}
        </div>
      )}

      {selected && (
        <>
          {/* Backdrop */}
          <div
            onClick={() => setSelected(null)}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 40 }}
          />
          <PlaylistDetail
            playlist={selected}
            onClose={() => setSelected(null)}
            onRematch={handleRematch}
          />
        </>
      )}
    </div>
  )
}
