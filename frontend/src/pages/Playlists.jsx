/**
 * Playlists.jsx — Full rewrite for Phase 7.
 * Three tabs: My Playlists | Template Gallery | Run History
 *
 * Fix: When opening the BlockEditor for an existing template, we must fetch
 * the full template detail (GET /api/playlist-templates/:id) which includes
 * blocks[]. The list endpoint omits blocks to keep list payloads small.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ListMusic, Clock, Layers, Search, Plus, CheckCircle2, XCircle,
  ChevronDown, ChevronUp, Loader2, GitFork, History, Trash2, ExternalLink, RefreshCw,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'
import { api } from '../lib/api.js'
import PlaylistRow from '../components/playlist/PlaylistRow.jsx'
import TemplateCard from '../components/playlist/TemplateCard.jsx'
import BlockEditor from '../components/playlist/BlockEditor.jsx'
import { useJellyfinUrl } from '../hooks/useJellyfinUrl.js'
import JellyfinIcon from '../components/JellyfinIcon.jsx'
import PlatformIcon from '../components/PlatformIcon.jsx'

// ── Platform badge (matches PlaylistImport.jsx) ──────────────────────────────

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
      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
      style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  )
}

// ── Imported Playlist Row ────────────────────────────────────────────────────

function ImportedPlaylistRow({ playlist, onDelete, onRematched }) {
  const navigate = useNavigate()
  const { buildItemUrl } = useJellyfinUrl()
  const [confirmDel, setConfirm] = useState(false)
  const [deleting, setDeleting]  = useState(false)
  const [rematching, setRematching] = useState(false)

  const handleDelete = async () => {
    if (!confirmDel) { setConfirm(true); return }
    setDeleting(true)
    try {
      await api.delete(`/api/import/playlists/${playlist.id}`)
      onDelete(playlist.id)
    } catch (e) {
      alert(`Delete failed: ${e.message}`)
      setDeleting(false)
      setConfirm(false)
    }
  }

  const handleRematch = async () => {
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

  const jellyfinUrl = playlist.jellyfin_playlist_id
    ? buildItemUrl(playlist.jellyfin_playlist_id)
    : null

  const matchPct = playlist.match_pct ?? (
    playlist.track_count ? Math.round(playlist.matched_count / playlist.track_count * 100) : 0
  )
  const isBusy = playlist.status === 'pending' || playlist.status === 'matching'

  return (
    <div className="card space-y-3 anim-fade-up" style={{ padding: '0.875rem 1rem' }}>
      {/* Row 1: Name + source + actions */}
      <div className="flex items-start gap-3">
        <PlatformIcon platform={playlist.source_platform} size={24} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate(`/import/${playlist.id}`)}
              className="text-sm font-semibold truncate hover:underline text-left"
              style={{ color: 'var(--text-primary)' }}
            >
              {playlist.name}
            </button>
            {jellyfinUrl && (
              <a
                href={jellyfinUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="Open in Jellyfin"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold flex-shrink-0
                           border border-[var(--border)] bg-[var(--bg-overlay)]
                           hover:border-[var(--accent)]/50 hover:bg-[var(--accent)]/10
                           transition-all duration-150"
                onClick={e => e.stopPropagation()}
              >
                <JellyfinIcon size={14} />
                <span className="hidden sm:inline text-white">Jellyfin</span>
              </a>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <PlatformBadge platform={playlist.source_platform} />
            {isBusy && (
              <span className="flex items-center gap-1 text-[10px]" style={{ color: '#fbbf24' }}>
                <Loader2 size={9} className="animate-spin" />
                {playlist.status === 'matching' ? 'Re-matching…' : 'Matching…'}
              </span>
            )}
            {!isBusy && (
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Imported</span>
            )}
          </div>
        </div>

        {/* Rematch */}
        <button
          onClick={handleRematch}
          disabled={rematching || isBusy}
          className="btn-secondary text-xs py-1.5 px-2.5 flex-shrink-0"
          title="Re-check library & push to Jellyfin"
        >
          {rematching ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
        </button>

        {/* View detail */}
        <button
          onClick={() => navigate(`/import/${playlist.id}`)}
          className="btn-secondary text-xs py-1.5 px-2.5 flex-shrink-0"
          title="View import details"
        >
          <ExternalLink size={11} />
        </button>

        {/* Delete */}
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="btn-secondary text-xs py-1.5 px-2.5 flex-shrink-0"
          title={confirmDel ? 'Click again to confirm delete' : 'Delete imported playlist'}
          style={confirmDel ? { borderColor: 'rgba(248,113,113,0.4)', color: 'var(--danger)' } : {}}
        >
          {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
        </button>
      </div>

      {/* Delete confirm */}
      {confirmDel && !deleting && (
        <div
          className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg anim-scale-in text-xs"
          style={{ background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.2)' }}
        >
          <span style={{ color: 'var(--danger)' }}>Delete this imported playlist and its data?</span>
          <button onClick={() => setConfirm(false)} className="font-medium flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
            Cancel
          </button>
        </div>
      )}

      {/* Row 2: Stats */}
      <div className="flex items-center gap-4 flex-wrap">
        <div>
          <div className="section-label">Match rate</div>
          <div className="text-xs mt-0.5" style={{ color: matchPct >= 80 ? 'var(--accent)' : matchPct >= 50 ? '#fbbf24' : 'var(--danger)' }}>
            {playlist.matched_count}/{playlist.track_count} tracks ({matchPct}%)
          </div>
        </div>
        <div>
          <div className="section-label">Status</div>
          <div className="text-xs mt-0.5" style={{ color: playlist.status === 'active' ? 'var(--accent)' : 'var(--text-secondary)' }}>
            {playlist.status}
          </div>
        </div>
        {playlist.created_at && (
          <div>
            <div className="section-label">Imported</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-primary)' }}>
              {new Date(playlist.created_at.endsWith('Z') || playlist.created_at.includes('+')
                ? playlist.created_at : playlist.created_at + 'Z').toLocaleDateString()}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const utc = s => { if (!s) return s; const bare = s.replace(/([+-]\d{2}:\d{2}|Z)$/, ''); return bare + 'Z' }

// ── Run History components ────────────────────────────────────────────────────

function RunRow({ run, onExpand, expanded }) {
  const dur = run.duration_secs != null
    ? run.duration_secs < 60 ? `${run.duration_secs}s` : `${Math.round(run.duration_secs/60)}m` : '—'
  const sc = run.status === 'ok' ? 'var(--accent)' : run.status === 'running' ? '#fbbf24' : 'var(--danger)'
  return (
    <div className="rounded-xl overflow-hidden anim-fade-up" style={{ border:'1px solid var(--border)' }}>
      <button
        onClick={() => onExpand(run.id)}
        className="w-full flex items-center gap-3 px-4 py-3 transition-colors text-left"
        style={{ background:'var(--bg-surface)' }}
        onMouseEnter={e=>e.currentTarget.style.background='var(--bg-elevated)'}
        onMouseLeave={e=>e.currentTarget.style.background='var(--bg-surface)'}
      >
        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background:sc }} />
        <span className="text-xs flex-1" style={{ color:'var(--text-primary)' }}>
          {new Date(utc(run.finished_at || run.started_at)).toLocaleString()}
        </span>
        <span className="text-xs" style={{ color:'var(--text-muted)' }}>{run.playlists_written} playlists</span>
        <span className="text-xs font-mono" style={{ color:'var(--text-muted)' }}>{dur}</span>
        {expanded ? <ChevronUp size={12} style={{ color:'var(--text-muted)' }} /> : <ChevronDown size={12} style={{ color:'var(--text-muted)' }} />}
      </button>
      {expanded && run.items && (
        <div className="px-4 py-3 space-y-1.5" style={{ borderTop:'1px solid var(--border)', background:'var(--bg)' }}>
          {run.items.map((item, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              {item.status==='ok'
                ? <CheckCircle2 size={11} style={{ color:'var(--accent)', flexShrink:0 }} />
                : <XCircle size={11} style={{ color:'var(--danger)', flexShrink:0 }} />}
              <span className="flex-1 truncate" style={{ color:'var(--text-secondary)' }}>{item.playlist_name}</span>
              {item.status==='ok'
                ? <span className="font-mono" style={{ color:'var(--text-muted)' }}>{item.tracks_added}t</span>
                : <span style={{ color:'var(--danger)' }}>{item.status}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Template Picker Modal ─────────────────────────────────────────────────────

function TemplatePicker({ templates, initialTemplate, currentUsername, onClose, onCreated }) {
  const [step, setStep]           = useState(initialTemplate ? 'name' : 'pick')
  const [selectedTpl, setSelTpl]  = useState(initialTemplate ?? null)
  const [search, setSearch]       = useState('')
  const [baseName, setBaseName]   = useState('')
  const [saving, setSaving]       = useState(false)
  const [error, setError]         = useState(null)

  const filtered = templates.filter(t =>
    !search.trim() || t.name.toLowerCase().includes(search.toLowerCase())
  )

  const handleCreate = async () => {
    if (!baseName.trim()) { setError('Name is required'); return }
    setSaving(true); setError(null)
    try {
      const pl = await api.post('/api/user-playlists', {
        template_id: selectedTpl.id,
        base_name: baseName.trim(),
      })
      onCreated(pl)
      onClose()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center anim-fade-in"
      style={{ background: 'rgba(0,0,0,0.65)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-full max-w-md mx-4 rounded-2xl overflow-hidden anim-scale-in"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
      >
        {/* Header */}
        <div className="px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            {step === 'pick' ? 'Choose a Template' : 'Name Your Playlist'}
          </div>
          {step === 'name' && selectedTpl && (
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              Using: <span style={{ color: 'var(--purple)' }}>{selectedTpl.name}</span>
            </div>
          )}
        </div>

        {/* Body */}
        <div className="px-5 py-4 max-h-[60vh] overflow-y-auto">
          {step === 'pick' ? (
            <div className="space-y-3">
              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-muted)' }} />
                <input
                  type="text" placeholder="Search templates…" value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="input pl-8"
                />
              </div>
              <div className="space-y-1.5">
                {filtered.map(t => (
                  <button
                    key={t.id}
                    onClick={() => { setSelTpl(t); setStep('name') }}
                    className="w-full text-left px-3 py-2.5 rounded-xl transition-all"
                    style={{ border: '1px solid var(--border)', background: 'var(--bg-elevated)' }}
                    onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--border-mid)'}
                    onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>{t.name}</span>
                      {t.is_system && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded-full" style={{ background: 'rgba(255,255,255,0.07)', color: 'var(--text-muted)' }}>System</span>
                      )}
                    </div>
                    {t.description && (
                      <div className="text-[10px] mt-0.5 line-clamp-1" style={{ color: 'var(--text-secondary)' }}>{t.description}</div>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="section-label block mb-1.5">Playlist name</label>
                <input
                  type="text"
                  placeholder="My awesome playlist"
                  value={baseName}
                  onChange={e => setBaseName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleCreate()}
                  autoFocus
                  className="input"
                />
              </div>
              {baseName.trim() && (
                <div
                  className="px-3 py-2 rounded-lg text-xs"
                  style={{ background: 'var(--bg-overlay)', color: 'var(--text-secondary)' }}
                >
                  Jellyfin name: <span style={{ color: 'var(--text-primary)' }}>{baseName.trim()} - {currentUsername}</span>
                </div>
              )}
              {error && <div className="text-xs" style={{ color: 'var(--danger)' }}>{error}</div>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="px-5 py-4 flex items-center gap-2"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          {step === 'name' && !initialTemplate && (
            <button onClick={() => setStep('pick')} className="btn-secondary text-xs">
              ← Back
            </button>
          )}
          <div className="flex-1" />
          <button onClick={onClose} className="btn-secondary text-xs">Cancel</button>
          {step === 'name' && (
            <button
              onClick={handleCreate}
              disabled={saving || !baseName.trim()}
              className="btn-primary text-xs"
            >
              {saving ? <Loader2 size={11} className="animate-spin" /> : null}
              Create Playlist
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main Playlists page ───────────────────────────────────────────────────────

export default function Playlists() {
  const { user, isAdmin }         = useAuth()

  const [tab, setTab]             = useState('playlists')

  const [myPlaylists, setMyPls]   = useState([])
  const [importedPls, setImPls]   = useState([])
  const [plsLoading, setPlsLoad]  = useState(true)
  const [adminUsers, setAdminU]   = useState([])
  const [selectedUser, setSelU]   = useState(null)
  const [highlightedPlId, setHlPl]= useState(null)

  const [templates, setTemplates] = useState([])
  const [tplLoading, setTplLoad]  = useState(true)
  const [highlightedTplId, setHlTpl] = useState(null)

  const [pickerOpen, setPickerOpen]       = useState(false)
  const [pickerInitTpl, setPickerInitTpl] = useState(null)
  // undefined=closed, null=new, obj=edit (obj must have blocks[] populated)
  const [editorTemplate, setEditorTpl]    = useState(undefined)

  const [runs, setRuns]           = useState([])
  const [expandedRun, setExpRun]  = useState(null)
  const [runDetails, setRunDets]  = useState({})

  // ── Fetch my playlists ───────────────────────────────────────────────────
  const fetchPlaylists = useCallback(async (userId) => {
    setPlsLoad(true)
    try {
      const url = userId ? `/api/user-playlists?user_id=${userId}` : '/api/user-playlists'
      const [userPls, imported] = await Promise.all([
        api.get(url),
        api.get('/api/import/playlists'),
      ])
      setMyPls(userPls)
      setImPls(imported)
    } catch {}
    setPlsLoad(false)
  }, [])

  // ── Fetch templates ──────────────────────────────────────────────────────
  const fetchTemplates = useCallback(async () => {
    setTplLoad(true)
    try {
      const data = await api.get('/api/playlist-templates')
      setTemplates(data)
    } catch {}
    setTplLoad(false)
  }, [])

  const fetchRuns = useCallback(async () => {
    try {
      const data = await api.get('/api/playlists/runs')
      setRuns(data)
    } catch {}
  }, [])

  const fetchAdminUsers = useCallback(async () => {
    if (!isAdmin) return
    try {
      const data = await api.get('/api/playlists/users')
      setAdminU(data)
    } catch {}
  }, [isAdmin])

  useEffect(() => {
    fetchPlaylists(null)
    fetchTemplates()
    fetchRuns()
    fetchAdminUsers()
  }, [fetchPlaylists, fetchTemplates, fetchRuns, fetchAdminUsers])

  // ── Run expand ───────────────────────────────────────────────────────────
  const handleExpandRun = async (runId) => {
    if (expandedRun === runId) { setExpRun(null); return }
    setExpRun(runId)
    if (!runDetails[runId]) {
      try {
        const d = await api.get(`/api/playlists/runs/${runId}`)
        setRunDets(prev => ({ ...prev, [runId]: d }))
      } catch {}
    }
  }

  // ── Highlight helper (auto-clear after 4s) ───────────────────────────────
  const hlTimerRef = useRef(null)
  const highlightTpl = (id) => {
    setHlTpl(id)
    if (hlTimerRef.current) clearTimeout(hlTimerRef.current)
    hlTimerRef.current = setTimeout(() => setHlTpl(null), 4000)
  }
  useEffect(() => () => { if (hlTimerRef.current) clearTimeout(hlTimerRef.current) }, [])

  const handleTemplateLinkClick = (tplId) => {
    setTab('gallery')
    highlightTpl(tplId)
  }

  const openPickerNew = () => { setPickerInitTpl(null); setPickerOpen(true) }
  const openPickerWith = (tpl) => { setPickerInitTpl(tpl); setPickerOpen(true) }

  const handlePlaylistCreated = (pl) => {
    setMyPls(prev => [pl, ...prev])
    setTab('playlists')
    setHlPl(pl.id)
    setTimeout(() => setHlPl(null), 4000)
  }

  const handlePlaylistUpdate = useCallback((updated) => {
    setMyPls(prev => prev.map(p => p.id === updated.id ? updated : p))
  }, [])

  const handlePlaylistDelete = useCallback((id) => {
    setMyPls(prev => prev.filter(p => p.id !== id))
  }, [])

  const handleImportedDelete = useCallback((id) => {
    setImPls(prev => prev.filter(p => p.id !== id))
  }, [])

  const handleForkSuccess = (forked) => {
    // forked already contains blocks[] from the fork endpoint (_template_detail)
    setTemplates(prev => [forked, ...prev])
    highlightTpl(forked.id)
  }

  const handleDeleteTemplate = (id) => {
    setTemplates(prev => prev.filter(t => t.id !== id))
  }

  const handleEditorSaved = (saved) => {
    if (saved) {
      setTemplates(prev => {
        const idx = prev.findIndex(t => t.id === saved.id)
        if (idx >= 0) {
          const next = [...prev]; next[idx] = saved; return next
        }
        return [saved, ...prev]
      })
      highlightTpl(saved.id)
    }
    fetchTemplates()
  }

  // FIX: When opening editor from "New Template" button (editorTemplate = null)
  // there's nothing to fetch. When opening an existing template from Playlists.jsx
  // directly (e.g. future path), ensure blocks are loaded.
  // TemplateCard.handleEdit now handles fetching before calling onEdit, so
  // setEditorTpl receives a fully-hydrated object.
  const openNewTemplate = () => setEditorTpl(null)

  const systemTpls    = templates.filter(t => t.is_system)
  const communityTpls = templates.filter(t => !t.is_system)

  const handleAdminUserChange = (userId) => {
    setSelU(userId || null)
    fetchPlaylists(userId || null)
  }

  return (
    <div className="space-y-6 max-w-3xl">
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 flex-wrap anim-fade-up">
        <div>
          <h1 style={{ fontFamily:'Syne', fontWeight:800, fontSize:26, letterSpacing:'-0.02em', color:'var(--text-primary)' }}>Playlists</h1>
          <p className="text-sm mt-1" style={{ color:'var(--text-secondary)' }}>Build, manage and auto-push template-driven playlists to Jellyfin</p>
        </div>
      </div>

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <div className="tab-bar anim-fade-up" style={{ animationDelay: '50ms' }}>
        {[
          { key: 'playlists', label: 'My Playlists',     icon: ListMusic },
          { key: 'gallery',   label: 'Template Gallery',  icon: Layers },
          { key: 'history',   label: 'Run History',       icon: History },
        ].map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setTab(key)} className={`tab ${tab === key ? 'active' : ''}`}>
            <Icon size={12} />{label}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* MY PLAYLISTS TAB                                                   */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      {tab === 'playlists' && (
        <div className="space-y-3 anim-fade-up">
          <div className="flex items-center gap-3 flex-wrap">
            {isAdmin && adminUsers.length > 0 && (
              <div className="relative">
                <select
                  value={selectedUser ?? ''}
                  onChange={e => handleAdminUserChange(e.target.value)}
                  className="input pr-8 text-xs appearance-none cursor-pointer"
                  style={{ width: 'auto' }}
                >
                  <option value="">My playlists</option>
                  {adminUsers.map(u => (
                    <option key={u.jellyfin_user_id} value={u.jellyfin_user_id}>{u.username}</option>
                  ))}
                </select>
                <ChevronDown size={10} className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-muted)' }} />
              </div>
            )}
            <div className="flex-1" />
            <button onClick={openPickerNew} className="btn-primary text-xs">
              <Plus size={12} /> New Playlist
            </button>
          </div>

          {plsLoading ? (
            <div className="flex items-center gap-2 py-8 justify-center">
              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
              <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Loading playlists…</span>
            </div>
          ) : myPlaylists.length === 0 && importedPls.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-16 gap-4 text-center anim-scale-in">
              <ListMusic size={28} strokeWidth={1.25} style={{ color: 'var(--text-muted)' }} />
              <div>
                <div className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No playlists yet</div>
                <div className="text-xs mt-1 max-w-xs" style={{ color: 'var(--text-muted)' }}>Go to the Template Gallery to create your first one, or import a playlist from Spotify, Tidal, or YouTube Music.</div>
              </div>
              <button onClick={() => setTab('gallery')} className="btn-secondary text-xs">
                Browse Templates
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Template-driven playlists */}
              {myPlaylists.length > 0 && (
                <div className="space-y-2">
                  {importedPls.length > 0 && (
                    <div className="flex items-center gap-2 mb-2">
                      <span className="section-label">Template Playlists</span>
                      <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                    </div>
                  )}
                  <div className="space-y-2 stagger">
                    {myPlaylists.map(pl => (
                      <div
                        key={pl.id}
                        style={highlightedPlId === pl.id ? {
                          boxShadow: '0 0 0 2px rgba(83,236,252,0.3)',
                          borderRadius: 14,
                        } : {}}
                      >
                        <PlaylistRow
                          playlist={pl}
                          onUpdate={handlePlaylistUpdate}
                          onDelete={handlePlaylistDelete}
                          onTemplateClick={handleTemplateLinkClick}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Imported playlists */}
              {importedPls.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="section-label">Imported Playlists</span>
                    <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                  </div>
                  <div className="space-y-2 stagger">
                    {importedPls.map(pl => (
                      <ImportedPlaylistRow
                        key={`imp-${pl.id}`}
                        playlist={pl}
                        onDelete={handleImportedDelete}
                        onRematched={() => fetchPlaylists(selectedUser)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* TEMPLATE GALLERY TAB                                               */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      {tab === 'gallery' && (
        <div className="space-y-6 anim-fade-up">
          <div className="flex items-center justify-end">
            <button onClick={openNewTemplate} className="btn-primary text-xs">
              <Plus size={12} /> New Template
            </button>
          </div>

          {tplLoading ? (
            <div className="flex items-center gap-2 py-8 justify-center">
              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
              <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Loading templates…</span>
            </div>
          ) : (
            <>
              {systemTpls.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="section-label">System Templates</span>
                    <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger">
                    {systemTpls.map(t => (
                      <TemplateCard
                        key={t.id}
                        template={t}
                        currentUserId={user?.user_id}
                        isAdmin={isAdmin}
                        onUse={openPickerWith}
                        onEdit={tpl => setEditorTpl(tpl)}
                        onForkSuccess={handleForkSuccess}
                        onDeleteSuccess={handleDeleteTemplate}
                        highlighted={highlightedTplId === t.id}
                      />
                    ))}
                  </div>
                </div>
              )}

              {communityTpls.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="section-label">Community Templates</span>
                    <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger">
                    {communityTpls.map(t => (
                      <TemplateCard
                        key={t.id}
                        template={t}
                        currentUserId={user?.user_id}
                        isAdmin={isAdmin}
                        onUse={openPickerWith}
                        onEdit={tpl => setEditorTpl(tpl)}
                        onForkSuccess={handleForkSuccess}
                        onDeleteSuccess={handleDeleteTemplate}
                        highlighted={highlightedTplId === t.id}
                      />
                    ))}
                  </div>
                </div>
              )}

              {templates.length === 0 && (
                <div className="card flex flex-col items-center justify-center py-16 gap-3 text-center anim-scale-in">
                  <Layers size={28} strokeWidth={1.25} style={{ color: 'var(--text-muted)' }} />
                  <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No templates yet</div>
                  <button onClick={openNewTemplate} className="btn-primary text-xs">
                    <Plus size={11} /> Create First Template
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* RUN HISTORY TAB                                                    */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      {tab === 'history' && (
        <div className="space-y-2 anim-fade-up">
          {runs.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-12 gap-2 anim-scale-in">
              <Clock size={24} strokeWidth={1.25} style={{ color: 'var(--text-muted)' }} />
              <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No run history yet</div>
            </div>
          ) : runs.map(run => (
            <RunRow
              key={run.id}
              run={{ ...run, items: runDetails[run.id]?.items }}
              onExpand={handleExpandRun}
              expanded={expandedRun === run.id}
            />
          ))}
        </div>
      )}

      {/* ── Template Picker Modal ─────────────────────────────────────── */}
      {pickerOpen && (
        <TemplatePicker
          templates={templates}
          initialTemplate={pickerInitTpl}
          currentUsername={user?.username ?? ''}
          onClose={() => { setPickerOpen(false); setPickerInitTpl(null) }}
          onCreated={handlePlaylistCreated}
        />
      )}

      {/* ── Block Editor Overlay ──────────────────────────────────────── */}
      {editorTemplate !== undefined && (
        <BlockEditor
          template={editorTemplate}
          onClose={() => setEditorTpl(undefined)}
          onSaved={handleEditorSaved}
        />
      )}
    </div>
  )
}
