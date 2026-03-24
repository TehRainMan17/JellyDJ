import { useState, useEffect, useRef, useCallback } from 'react'
import { Ban, Search, X, Plus, Trash2, Loader2, AlertCircle, CheckCircle2, Music2 } from 'lucide-react'
import { api } from '../lib/api.js'

// ── Debounce hook ─────────────────────────────────────────────────────────────
function useDebounce(value, ms) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function Toast({ toast }) {
  if (!toast) return null
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2.5 px-4 py-3 rounded-2xl text-sm font-medium shadow-2xl"
      style={{
        background: 'var(--bg-elevated)',
        border: `1px solid ${toast.ok ? 'var(--border-mid)' : 'rgba(248,113,113,0.35)'}`,
        color: toast.ok ? 'var(--text-primary)' : 'var(--danger)',
        backdropFilter: 'blur(12px)',
        minWidth: 280,
      }}>
      {toast.ok
        ? <CheckCircle2 size={15} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        : <AlertCircle size={15} style={{ flexShrink: 0 }} />}
      {toast.msg}
    </div>
  )
}

// ── Album art placeholder ─────────────────────────────────────────────────────
function AlbumArt({ src, size = 48, radius = 10 }) {
  const [err, setErr] = useState(false)
  return (
    <div style={{
      width: size, height: size, borderRadius: radius, flexShrink: 0,
      background: 'var(--bg-overlay)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      overflow: 'hidden',
      border: '1px solid var(--border)',
    }}>
      {src && !err
        ? <img src={src} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }}
               onError={() => setErr(true)} />
        : <Music2 size={size * 0.35} style={{ color: 'var(--text-muted)' }} />}
    </div>
  )
}

// ── Search result row ─────────────────────────────────────────────────────────
function SearchRow({ item, onAdd, adding }) {
  const [reason, setReason] = useState('')
  const [showReason, setShowReason] = useState(false)
  const busy = adding === item.jellyfin_album_id

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      padding: '10px 14px',
      borderBottom: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <AlbumArt src={item.cover_image_url} size={44} radius={8} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 13, fontWeight: 600, color: 'var(--text-primary)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {item.album_name}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 1 }}>
            {item.artist_name}
            {item.year ? <span style={{ color: 'var(--text-muted)' }}> · {item.year}</span> : null}
            <span style={{ color: 'var(--text-muted)' }}> · {item.track_count} tracks</span>
          </div>
        </div>

        {item.already_excluded ? (
          <span style={{
            fontSize: 11, fontWeight: 600, color: 'var(--accent)',
            display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0,
          }}>
            <CheckCircle2 size={13} /> Excluded
          </span>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            <button
              onClick={() => setShowReason(s => !s)}
              style={{
                fontSize: 10, padding: '3px 8px', borderRadius: 6,
                background: 'var(--bg-overlay)', color: 'var(--text-secondary)',
                border: '1px solid var(--border)', cursor: 'pointer',
              }}>
              {showReason ? 'hide' : '+ reason'}
            </button>
            <button
              onClick={() => onAdd(item, reason)}
              disabled={busy}
              className="btn-primary"
              style={{ fontSize: 12, padding: '5px 12px', borderRadius: 8, minHeight: 0, display: 'flex', alignItems: 'center', gap: 5 }}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Ban size={12} />}
              Exclude
            </button>
          </div>
        )}
      </div>

      {showReason && !item.already_excluded && (
        <input
          value={reason}
          onChange={e => setReason(e.target.value)}
          placeholder="Reason (optional) — e.g. 'religious content', 'seasonal only'"
          maxLength={120}
          style={{
            marginLeft: 56,
            fontSize: 12, padding: '6px 10px', borderRadius: 8,
            background: 'var(--bg-overlay)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            outline: 'none',
            width: 'calc(100% - 56px)',
          }}
        />
      )}
    </div>
  )
}

// ── Excluded album row ────────────────────────────────────────────────────────
function ExcludedRow({ album, onRemove, removing }) {
  const busy = removing === album.id
  const date = album.excluded_at
    ? new Date(album.excluded_at.endsWith('Z') ? album.excluded_at : album.excluded_at + 'Z')
        .toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    : null

  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px' }}>
      <AlbumArt src={album.cover_image_url} size={52} radius={10} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: 600, color: 'var(--text-primary)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {album.album_name}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
          {album.artist_name}
          <span style={{ color: 'var(--text-muted)' }}> · {album.track_count ?? '?'} tracks</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 5,
            background: 'rgba(248,113,113,0.12)', color: 'var(--danger)',
            border: '1px solid rgba(248,113,113,0.2)',
          }}>
            excluded
          </span>
          {album.reason && (
            <span style={{ fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic' }}>
              "{album.reason}"
            </span>
          )}
          {date && (
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>since {date}</span>
          )}
        </div>
      </div>
      <button
        onClick={() => onRemove(album.id)}
        disabled={busy}
        title="Remove exclusion — album will appear in playlists again"
        style={{
          flexShrink: 0, padding: 8, borderRadius: 10,
          background: 'transparent', border: '1px solid transparent',
          color: 'var(--danger)', cursor: busy ? 'not-allowed' : 'pointer',
          opacity: busy ? 0.5 : 1,
          transition: 'background 150ms, border-color 150ms',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(248,113,113,0.1)'; e.currentTarget.style.borderColor = 'rgba(248,113,113,0.25)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'transparent' }}>
        {busy ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
      </button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function AlbumExclusions() {
  const [exclusions, setExclusions]   = useState([])
  const [loadingList, setLoadingList] = useState(true)
  const [listErr, setListErr]         = useState(null)
  const [removing, setRemoving]       = useState(null)

  const [query, setQuery]         = useState('')
  const [results, setResults]     = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchErr, setSearchErr] = useState(null)
  const debouncedQ = useDebounce(query, 380)

  const [adding, setAdding] = useState(null)
  const [toast, setToast]   = useState(null)
  const toastTimer = useRef(null)

  function showToast(msg, ok = true) {
    setToast({ msg, ok })
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }

  // ── Load list ────────────────────────────────────────────────────────────
  const loadList = useCallback(() => {
    setLoadingList(true)
    api.get('/api/exclusions/albums')
      
      .then(d => { setExclusions(d); setLoadingList(false) })
      .catch(e => { setListErr(e.message); setLoadingList(false) })
  }, [])

  useEffect(loadList, [loadList])

  // ── Search ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!debouncedQ.trim()) { setResults(null); setSearchErr(null); return }
    setSearching(true)
    setSearchErr(null)
    api.get(`/api/exclusions/search?q=${encodeURIComponent(debouncedQ.trim())}`)
      .then(d => { setResults(d.results || []); setSearching(false) })
      .catch(e => { setSearchErr(e.message); setSearching(false) })
  }, [debouncedQ])

  // ── Add ──────────────────────────────────────────────────────────────────
  async function handleAdd(item, reason) {
    setAdding(item.jellyfin_album_id)
    try {
      const data = await api.post('/api/exclusions/albums', {
          jellyfin_album_id: item.jellyfin_album_id,
          album_name:        item.album_name,
          artist_name:       item.artist_name,
          reason:            reason || '',
          cover_image_url:   item.cover_image_url || null,
        })
      if (!data) throw new Error('Failed')
      const n = data.track_count ?? item.track_count ?? 0
      showToast(`"${item.album_name}" excluded — ${n} track${n === 1 ? '' : 's'} will be skipped in playlists`)
      loadList()
      setResults(prev => prev?.map(x =>
        x.jellyfin_album_id === item.jellyfin_album_id ? { ...x, already_excluded: true } : x
      ))
    } catch (e) {
      showToast(e.message, false)
    } finally {
      setAdding(null)
    }
  }

  // ── Remove ───────────────────────────────────────────────────────────────
  async function handleRemove(id) {
    setRemoving(id)
    const album = exclusions.find(e => e.id === id)
    try {
      await api.delete(`/api/exclusions/albums/${id}`)
      showToast(`"${album?.album_name ?? 'Album'}" removed — will appear in playlists again`)
      loadList()
      if (album && results) {
        setResults(prev => prev?.map(x =>
          x.jellyfin_album_id === album.jellyfin_album_id ? { ...x, already_excluded: false } : x
        ))
      }
    } catch (e) {
      showToast(e.message, false)
    } finally {
      setRemoving(null)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ maxWidth: 680, margin: '0 auto' }}>

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontFamily: 'Syne, sans-serif', fontWeight: 800, fontSize: 22, color: 'var(--text-primary)', margin: 0 }}>
          Album Exclusions
        </h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
          Excluded albums are permanently removed from all playlist generation, regardless of play count or score.
          Changes take effect immediately on the next playlist run — no re-index needed.
        </p>
      </div>

      {/* Search card */}
      <div style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 14,
        marginBottom: 24,
        overflow: 'hidden',
      }}>
        {/* Search input */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 16px',
          borderBottom: results !== null ? '1px solid var(--border)' : 'none',
        }}>
          {searching
            ? <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            : <Search size={16} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search your Jellyfin library for an album to exclude…"
            autoComplete="off"
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              fontSize: 13, color: 'var(--text-primary)',
            }}
          />
          {query && (
            <button onClick={() => { setQuery(''); setResults(null) }}
              style={{ background: 'none', border: 'none', cursor: 'pointer',
                       color: 'var(--text-muted)', padding: 2, borderRadius: 4 }}>
              <X size={14} />
            </button>
          )}
        </div>

        {/* Results */}
        {results !== null && (
          <div style={{ maxHeight: 400, overflowY: 'auto' }}>
            {searchErr ? (
              <div style={{ padding: '14px 16px', color: 'var(--danger)', fontSize: 13,
                            display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertCircle size={14} /> {searchErr}
              </div>
            ) : results.length === 0 ? (
              <div style={{ padding: '14px 16px', color: 'var(--text-muted)', fontSize: 13 }}>
                No albums found for "{query}"
              </div>
            ) : results.map(item => (
              <SearchRow
                key={item.jellyfin_album_id}
                item={item}
                onAdd={handleAdd}
                adding={adding}
              />
            ))}
          </div>
        )}
      </div>

      {/* Currently excluded */}
      <div>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 12,
        }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em',
                         textTransform: 'uppercase', color: 'var(--text-muted)' }}>
            Currently excluded
          </span>
          {exclusions.length > 0 && (
            <span style={{
              fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 20,
              background: 'rgba(248,113,113,0.1)', color: 'var(--danger)',
              border: '1px solid rgba(248,113,113,0.2)',
            }}>
              {exclusions.length} album{exclusions.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {loadingList ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '32px 0',
                        color: 'var(--text-muted)', justifyContent: 'center', fontSize: 13 }}>
            <Loader2 size={15} className="animate-spin" /> Loading…
          </div>
        ) : listErr ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 16, borderRadius: 12,
                        background: 'rgba(248,113,113,0.07)', color: 'var(--danger)', fontSize: 13 }}>
            <AlertCircle size={14} /> {listErr}
          </div>
        ) : exclusions.length === 0 ? (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            padding: '48px 0', textAlign: 'center',
          }}>
            <div style={{
              width: 52, height: 52, borderRadius: 16, background: 'var(--bg-overlay)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 14,
            }}>
              <Ban size={22} style={{ color: 'var(--text-muted)' }} />
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
              No albums excluded yet
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
              Use the search above to find albums and exclude them from playlists
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {exclusions.map(album => (
              <ExcludedRow
                key={album.id}
                album={album}
                onRemove={handleRemove}
                removing={removing}
              />
            ))}
          </div>
        )}
      </div>

      <Toast toast={toast} />
    </div>
  )
}
