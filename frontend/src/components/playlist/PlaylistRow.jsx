/**
 * PlaylistRow.jsx — Single row in the My Playlists panel.
 * Handles: push, preview modal, schedule toggle, rename, delete.
 * + Jellyfin deep-link button when the playlist has been pushed at least once.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Play, Eye, Trash2, Loader2, CheckCircle2, XCircle,
  Clock, ChevronDown, Calendar, Edit2,
} from 'lucide-react'
import { api } from '../../lib/api.js'
import { useJellyfinUrl } from '../../hooks/useJellyfinUrl.js'
import JellyfinIcon from '../JellyfinIcon.jsx'

const SCHEDULE_OPTIONS = [
  { value: 6,    label: '6 hours' },
  { value: 12,   label: '12 hours' },
  { value: 24,   label: '24 hours' },
  { value: 48,   label: '48 hours' },
  { value: 168,  label: '7 days' },
]

/**
 * Compute the next scheduled push time from last_generated_at (or created_at).
 */
function nextRunTime(playlist, intervalH) {
  if (!intervalH) return null
  const baseStr = playlist.last_generated_at ?? playlist.created_at
  if (!baseStr) return null
  const base = new Date(
    baseStr.endsWith('Z') || baseStr.includes('+') ? baseStr : baseStr + 'Z'
  )
  return new Date(base.getTime() + intervalH * 3600 * 1000)
}

export default function PlaylistRow({
  playlist,
  onUpdate,
  onDelete,
  onTemplateClick,
}) {
  // ── Push state ──────────────────────────────────────────────────────────
  const [pushing, setPushing]     = useState(false)
  const [pushMsg, setPushMsg]     = useState(null)
  const pushTimerRef              = useRef(null)

  // ── Preview state ───────────────────────────────────────────────────────
  const [previewOpen, setPreview] = useState(false)
  const [previewData, setPrevData]= useState(null)
  const [previewLoading, setPrevL]= useState(false)
  const [pushingFromPreview, setPFP] = useState(false)

  // ── Rename state ────────────────────────────────────────────────────────
  const [renaming, setRenaming]   = useState(false)
  const [nameInput, setNameInput] = useState(playlist.base_name)
  const renameRef                 = useRef(null)

  // ── Schedule state ──────────────────────────────────────────────────────
  const [schedEnabled, setSchedE] = useState(playlist.schedule_enabled)
  const [schedInterval, setSchedI]= useState(playlist.schedule_interval_h ?? 24)
  const [schedSaving, setSchedS]  = useState(false)

  // ── Delete state ────────────────────────────────────────────────────────
  const [confirmDel, setConfirm]  = useState(false)
  const [deleting, setDeleting]   = useState(false)

  // ── Jellyfin URL ─────────────────────────────────────────────────────────
  const { buildItemUrl } = useJellyfinUrl()

  useEffect(() => () => { if (pushTimerRef.current) clearTimeout(pushTimerRef.current) }, [])

  useEffect(() => {
    if (renaming && renameRef.current) renameRef.current.focus()
  }, [renaming])

  // ── Push ─────────────────────────────────────────────────────────────────
  const handlePush = useCallback(async () => {
    setPushing(true); setPushMsg(null)
    if (pushTimerRef.current) clearTimeout(pushTimerRef.current)
    try {
      const r = await api.post(`/api/user-playlists/${playlist.id}/push`)
      setPushMsg({ ok: true, text: `${r.tracks_added} tracks written to Jellyfin` })
      onUpdate({ ...playlist, last_generated_at: new Date().toISOString(), last_track_count: r.tracks_added })
    } catch (e) {
      setPushMsg({ ok: false, text: e.message })
    } finally {
      setPushing(false)
      pushTimerRef.current = setTimeout(() => setPushMsg(null), 6000)
    }
  }, [playlist, onUpdate])

  // ── Preview ───────────────────────────────────────────────────────────────
  const openPreview = async () => {
    setPreview(true); setPrevData(null); setPrevL(true)
    try {
      const r = await api.post(`/api/user-playlists/${playlist.id}/preview`)
      setPrevData(r)
    } catch (e) {
      setPrevData({ error: e.message })
    } finally {
      setPrevL(false)
    }
  }

  const pushFromPreview = async () => {
    setPFP(true)
    try {
      const r = await api.post(`/api/user-playlists/${playlist.id}/push`)
      setPushMsg({ ok: true, text: `${r.tracks_added} tracks written to Jellyfin` })
      onUpdate({ ...playlist, last_generated_at: new Date().toISOString(), last_track_count: r.tracks_added })
      setPreview(false)
      pushTimerRef.current = setTimeout(() => setPushMsg(null), 6000)
    } catch (e) {
      setPushMsg({ ok: false, text: e.message })
    } finally {
      setPFP(false)
    }
  }

  // ── Rename ────────────────────────────────────────────────────────────────
  const commitRename = async () => {
    const trimmed = nameInput.trim()
    if (!trimmed || trimmed === playlist.base_name) { setRenaming(false); setNameInput(playlist.base_name); return }
    try {
      const updated = await api.put(`/api/user-playlists/${playlist.id}`, { base_name: trimmed })
      onUpdate(updated)
    } catch {
      setNameInput(playlist.base_name)
    }
    setRenaming(false)
  }

  const handleNameKeyDown = (e) => {
    if (e.key === 'Enter') commitRename()
    if (e.key === 'Escape') { setRenaming(false); setNameInput(playlist.base_name) }
  }

  // ── Schedule ──────────────────────────────────────────────────────────────
  const saveSchedule = async (enabled, interval) => {
    setSchedS(true)
    try {
      const updated = await api.put(`/api/user-playlists/${playlist.id}`, {
        schedule_enabled: enabled,
        schedule_interval_h: interval,
      })
      onUpdate(updated)
    } catch {}
    setSchedS(false)
  }

  const toggleSchedule = (v) => {
    setSchedE(v)
    saveSchedule(v, schedInterval)
  }

  const changeInterval = (v) => {
    setSchedI(v)
    saveSchedule(schedEnabled, v)
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  const handleDelete = async () => {
    if (!confirmDel) { setConfirm(true); return }
    setDeleting(true)
    try {
      await api.delete(`/api/user-playlists/${playlist.id}`)
      onDelete(playlist.id)
    } catch (e) {
      alert(`Delete failed: ${e.message}`)
      setDeleting(false)
      setConfirm(false)
    }
  }

  const nextRun = schedEnabled ? nextRunTime(playlist, schedInterval) : null
  const isPastDue = nextRun && nextRun < new Date()

  const lastGenFmt = playlist.last_generated_at
    ? new Date(playlist.last_generated_at.endsWith('Z') || playlist.last_generated_at.includes('+')
        ? playlist.last_generated_at : playlist.last_generated_at + 'Z').toLocaleString()
    : null

  // Build the Jellyfin playlist deep-link (only available after at least one push)
  const jellyfinPlaylistUrl = playlist.jellyfin_playlist_id
    ? buildItemUrl(playlist.jellyfin_playlist_id)
    : null

  return (
    <>
      <div
        className="card space-y-3 anim-fade-up"
        style={{ padding: '0.875rem 1rem' }}
      >
        {/* Row 1: Name + template + quick actions */}
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            {/* Playlist name — clickable to rename */}
            {renaming ? (
              <input
                ref={renameRef}
                value={nameInput}
                onChange={e => setNameInput(e.target.value)}
                onBlur={commitRename}
                onKeyDown={handleNameKeyDown}
                className="input py-0.5 text-sm font-semibold w-full mb-0.5"
              />
            ) : (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { setRenaming(true); setNameInput(playlist.base_name) }}
                  className="flex items-center gap-1 group text-left"
                  title="Click to rename"
                >
                  <span className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                    {playlist.jellyfin_name}
                  </span>
                  <Edit2 size={10} className="flex-shrink-0 opacity-0 group-hover:opacity-60 transition-opacity" style={{ color: 'var(--text-muted)' }} />
                </button>

                {/* ── Jellyfin open link — shown once the playlist has been pushed ── */}
                {jellyfinPlaylistUrl && (
                  <a
                    href={jellyfinPlaylistUrl}
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
            )}

            {/* Template link */}
            {playlist.template_name ? (
              <button
                onClick={() => onTemplateClick(playlist.template_id)}
                className="flex items-center gap-1 text-[10px] mt-0.5 hover:underline"
                style={{ color: 'var(--purple)' }}
              >
                {playlist.template_name}
              </button>
            ) : (
              <span className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>No template</span>
            )}
          </div>

          {/* Push button */}
          <button
            onClick={handlePush}
            disabled={pushing}
            className="btn-primary text-xs py-1.5 px-3 flex-shrink-0"
          >
            {pushing ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
            Push
          </button>

          {/* Preview */}
          <button
            onClick={openPreview}
            className="btn-secondary text-xs py-1.5 px-2.5 flex-shrink-0"
            title="Preview tracks"
          >
            <Eye size={11} />
          </button>

          {/* Delete */}
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="btn-secondary text-xs py-1.5 px-2.5 flex-shrink-0"
            title={confirmDel ? 'Click again to confirm delete' : 'Delete playlist'}
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
            <span style={{ color: 'var(--danger)' }}>This will also delete the playlist from Jellyfin. Are you sure?</span>
            <button onClick={() => setConfirm(false)} className="font-medium flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
              Cancel
            </button>
          </div>
        )}

        {/* Row 2: Stats */}
        <div className="flex items-center gap-4 flex-wrap">
          <div>
            <div className="section-label">Last pushed</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-primary)' }}>
              {lastGenFmt ?? 'Never pushed'}
            </div>
          </div>
          <div>
            <div className="section-label">Tracks</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-primary)' }}>
              {playlist.last_track_count != null ? playlist.last_track_count : '—'}
            </div>
          </div>
        </div>

        {/* Push result */}
        {pushMsg && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-lg anim-scale-in"
            style={{
              background: pushMsg.ok ? 'rgba(83,236,252,0.06)' : 'rgba(248,113,113,0.06)',
              border: `1px solid ${pushMsg.ok ? 'rgba(83,236,252,0.2)' : 'rgba(248,113,113,0.2)'}`,
            }}
          >
            {pushMsg.ok
              ? <CheckCircle2 size={11} style={{ color: 'var(--accent)', flexShrink: 0 }} />
              : <XCircle size={11} style={{ color: 'var(--danger)', flexShrink: 0 }} />
            }
            <span className="text-xs" style={{ color: pushMsg.ok ? 'var(--accent)' : 'var(--danger)' }}>
              {pushMsg.text}
            </span>
            {/* Shortcut: open in Jellyfin right after a successful push */}
            {pushMsg.ok && jellyfinPlaylistUrl && (
              <a
                href={jellyfinPlaylistUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto flex items-center gap-1.5 text-[10px] font-medium flex-shrink-0
                           text-[var(--accent)]/70 hover:text-[var(--accent)] transition-colors"
                onClick={e => e.stopPropagation()}
              >
                <JellyfinIcon size={11} />
                Open in Jellyfin
              </a>
            )}
          </div>
        )}

        {/* Row 3: Schedule */}
        <div
          className="flex items-center gap-3 pt-2 flex-wrap"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <button
            onClick={() => toggleSchedule(!schedEnabled)}
            className="flex items-center gap-2 text-xs"
            disabled={schedSaving}
          >
            <div
              className="w-8 h-4 rounded-full transition-colors relative flex-shrink-0"
              style={{ background: schedEnabled ? 'var(--accent)' : 'rgba(255,255,255,0.1)' }}
            >
              <div
                className="absolute top-0.5 w-3 h-3 rounded-full transition-all"
                style={{
                  background: 'white',
                  left: schedEnabled ? '17px' : '2px',
                }}
              />
            </div>
            <span style={{ color: schedEnabled ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
              Auto-push
            </span>
          </button>

          {schedEnabled && (
            <>
              <div className="relative">
                <select
                  value={schedInterval}
                  onChange={e => changeInterval(parseInt(e.target.value))}
                  className="input py-0.5 pr-6 text-xs appearance-none cursor-pointer"
                  style={{ width: 'auto', paddingRight: '1.5rem' }}
                >
                  {SCHEDULE_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                <ChevronDown size={10} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-muted)' }} />
              </div>

              {nextRun && (
                <span
                  className="flex items-center gap-1 text-xs"
                  style={{ color: isPastDue ? 'var(--accent)' : 'var(--text-muted)' }}
                  title={isPastDue ? 'Push is due — will run within 15 minutes' : undefined}
                >
                  <Calendar size={9} />
                  {isPastDue ? 'Pushing soon…' : `Next: ${nextRun.toLocaleString()}`}
                </span>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Preview Modal ──────────────────────────────────────────────── */}
      {previewOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center anim-fade-in"
          style={{ background: 'rgba(0,0,0,0.65)' }}
          onClick={(e) => e.target === e.currentTarget && setPreview(false)}
        >
          <div
            className="w-full max-w-sm mx-4 rounded-2xl overflow-hidden anim-scale-in"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          >
            <div className="px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                  Preview: {playlist.jellyfin_name}
                </div>
                {jellyfinPlaylistUrl && (
                  <a
                    href={jellyfinPlaylistUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-[10px] font-medium
                               text-[var(--text-secondary)] hover:text-[var(--accent)] transition-colors"
                    onClick={e => e.stopPropagation()}
                    title="Open this playlist in Jellyfin"
                  >
                    <JellyfinIcon size={12} />
                    Open in Jellyfin
                  </a>
                )}
              </div>
            </div>
            <div className="px-5 py-4">
              {previewLoading ? (
                <div className="flex items-center gap-2 py-4">
                  <Loader2 size={14} className="animate-spin" style={{ color: 'var(--accent)' }} />
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Generating preview…</span>
                </div>
              ) : previewData?.error ? (
                <div className="text-sm" style={{ color: 'var(--danger)' }}>{previewData.error}</div>
              ) : previewData ? (() => {
                const count = previewData.estimated_tracks
                  ?? previewData.estimated_count
                  ?? previewData.track_count
                  ?? previewData.total
                  ?? '?'
                const rawSamples = previewData.sample
                  ?? previewData.sample_tracks
                  ?? previewData.tracks
                  ?? []
                const samples = rawSamples.slice(0, 5).map(t => ({
                  name:   t.track ?? t.track_name ?? t.name ?? '',
                  artist: t.artist ?? t.artist_name ?? '',
                }))
                return (
                  <div className="space-y-3">
                    <div className="text-2xl font-bold" style={{ color: 'var(--accent)', fontFamily: 'Syne' }}>
                      ~{count} tracks
                    </div>
                    {samples.length > 0 && (
                      <div className="space-y-1.5">
                        <div className="section-label">Sample tracks</div>
                        {samples.map((t, i) => (
                          <div key={i} className="flex items-center gap-2">
                            <span className="text-xs font-mono w-4 text-right flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{i+1}</span>
                            <div className="min-w-0">
                              <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{t.name}</div>
                              <div className="text-[10px] truncate" style={{ color: 'var(--text-secondary)' }}>{t.artist}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {samples.length === 0 && count !== '?' && Number(count) > 0 && (
                      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>No sample available</div>
                    )}
                    {count === '?' || Number(count) === 0 ? (
                      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                        No tracks matched — check your filter settings or make sure tracks are indexed.
                      </div>
                    ) : null}
                  </div>
                )
              })() : null}
            </div>
            <div
              className="px-5 py-4 flex items-center gap-2"
              style={{ borderTop: '1px solid var(--border)' }}
            >
              <button
                onClick={pushFromPreview}
                disabled={pushingFromPreview || previewLoading}
                className="btn-primary text-xs"
              >
                {pushingFromPreview ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                Push Now
              </button>
              <button
                onClick={() => setPreview(false)}
                className="btn-secondary text-xs"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
