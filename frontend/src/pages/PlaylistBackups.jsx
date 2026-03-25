/**
 * PlaylistBackups — Admin-only page for backing up and restoring Jellyfin playlists.
 *
 * Each backup now keeps up to max_revisions rolling snapshots. You can restore
 * any revision, label it to pin it permanently, or delete individual revisions.
 */

import { useState, useEffect, useCallback } from 'react'
import {
  DatabaseBackup, RefreshCw, RotateCcw, RotateCw, Trash2, Save,
  ChevronDown, ChevronUp, Check, Shield, ShieldOff, Clock,
  Settings2, AlertCircle, Loader2, Eye, EyeOff, Pencil, X,
  CheckCircle2, History, Tag, ChevronRight,
} from 'lucide-react'
import { api } from '../lib/api.js'

// ── Date helpers ──────────────────────────────────────────────────────────────
const utc = s => {
  if (!s) return s
  if (/([+-]\d{2}:\d{2}|Z)$/.test(s)) return s
  return s + 'Z'
}
function fmt(dt) {
  if (!dt) return '—'
  return new Date(utc(dt)).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
function fmtShort(dt) {
  if (!dt) return '—'
  return new Date(utc(dt)).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── Primitives ────────────────────────────────────────────────────────────────

function Badge({ children, variant = 'default' }) {
  const colors = {
    default: 'bg-white/10 text-[var(--text-secondary)]',
    success: 'bg-emerald-500/20 text-emerald-400',
    warning: 'bg-amber-500/20 text-amber-400',
    info:    'bg-sky-500/20 text-sky-400',
    muted:   'bg-white/5 text-[var(--text-muted)]',
    pinned:  'bg-purple-500/20 text-purple-300',
  }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${colors[variant]}`}>
      {children}
    </span>
  )
}

function Spinner({ size = 14 }) {
  return <Loader2 size={size} className="animate-spin flex-shrink-0" />
}

function Notice({ type = 'info', children }) {
  const styles = {
    info:    'bg-sky-500/10 border-sky-500/30 text-sky-300',
    success: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300',
    error:   'bg-red-500/10 border-red-500/30 text-red-300',
    warning: 'bg-amber-500/10 border-amber-500/30 text-amber-300',
  }
  return (
    <div className={`flex items-start gap-2 px-4 py-3 rounded-lg border text-sm ${styles[type]}`}>
      <AlertCircle size={15} className="mt-0.5 flex-shrink-0" />
      <span>{children}</span>
    </div>
  )
}

function ActionBtn({ onClick, disabled, loading, icon: Icon, label, variant = 'default', title }) {
  const variants = {
    default: 'hover:bg-white/10 text-[var(--text-secondary)] border border-transparent hover:border-white/10',
    primary: 'text-white hover:opacity-90',
    danger:  'hover:bg-red-500/15 text-[var(--text-muted)] hover:text-red-400 border border-transparent hover:border-red-500/20',
    warning: 'bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25',
    ghost:   'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-white/5',
  }
  return (
    <button onClick={onClick} disabled={disabled || loading} title={title}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed ${variants[variant]}`}
      style={variant === 'primary' ? { background: 'var(--accent)' } : undefined}>
      {loading ? <Spinner size={13} /> : <Icon size={13} />}
      {label}
    </button>
  )
}

// ── Settings panel ────────────────────────────────────────────────────────────

function BackupSettingsPanel({ onSaved }) {
  const [settings, setSettings] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api.get('/api/playlist-backups/settings').then(setSettings).catch(e => setError(e.message))
  }, [])

  async function handleSave() {
    setSaving(true); setError(null)
    try {
      const updated = await api.put('/api/playlist-backups/settings', {
        auto_backup_enabled: settings.auto_backup_enabled,
        auto_backup_interval_hours: parseInt(settings.auto_backup_interval_hours, 10),
      })
      setSettings(updated); setSaved(true); setTimeout(() => setSaved(false), 2500)
      onSaved?.()
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  return (
    <div className="rounded-xl border overflow-hidden"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-white/5 transition-colors">
        <div className="flex items-center gap-3">
          <Settings2 size={17} style={{ color: 'var(--accent)' }} />
          <span className="font-semibold text-[var(--text-primary)]">Auto-backup Settings</span>
          {settings && (
            <Badge variant={settings.auto_backup_enabled ? 'success' : 'default'}>
              {settings.auto_backup_enabled ? `Every ${settings.auto_backup_interval_hours}h` : 'Disabled'}
            </Badge>
          )}
        </div>
        {open ? <ChevronUp size={15} style={{ color: 'var(--text-muted)' }} />
               : <ChevronDown size={15} style={{ color: 'var(--text-muted)' }} />}
      </button>

      {open && settings && (
        <div className="px-5 pb-5 space-y-4 border-t" style={{ borderColor: 'var(--border)' }}>
          <p className="text-sm text-[var(--text-secondary)] pt-4">
            Each auto-backup run creates a new revision. Older unlabeled revisions are
            pruned automatically once the per-playlist limit is reached. Labeled
            (pinned) revisions are never pruned.
          </p>
          <div className="flex items-center gap-3">
            <label className="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" checked={settings.auto_backup_enabled}
                onChange={e => setSettings(s => ({ ...s, auto_backup_enabled: e.target.checked }))}
                className="sr-only peer" />
              <div className="w-10 h-5 rounded-full peer bg-white/10 peer-checked:bg-[var(--accent)] transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-5" />
            </label>
            <span className="text-sm text-[var(--text-primary)]">Enable automatic backups</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-sm text-[var(--text-secondary)] whitespace-nowrap">Check every</span>
            <input type="number" min={1} max={168} value={settings.auto_backup_interval_hours}
              onChange={e => setSettings(s => ({ ...s, auto_backup_interval_hours: e.target.value }))}
              className="w-20 px-3 py-1.5 rounded-lg text-sm text-[var(--text-primary)] border"
              style={{ background: 'var(--bg)', borderColor: 'var(--border)' }} />
            <span className="text-sm text-[var(--text-secondary)]">hours</span>
          </div>
          {settings.last_auto_backup_at && (
            <p className="text-xs text-[var(--text-muted)] flex items-center gap-1.5">
              <Clock size={11} /> Last auto-backup: {fmt(settings.last_auto_backup_at)}
            </p>
          )}
          {error && <Notice type="error">{error}</Notice>}
          <ActionBtn onClick={handleSave} loading={saving}
            icon={saved ? Check : Save} label={saved ? 'Saved!' : 'Save settings'} variant="primary" />
        </div>
      )}
    </div>
  )
}

// ── Name editor ───────────────────────────────────────────────────────────────

function NameEditor({ backup, onUpdated }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(backup.display_name || '')
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      const updated = await api.patch(`/api/playlist-backups/${backup.id}`, {
        display_name: value.trim() || null,
      })
      onUpdated(updated); setEditing(false)
    } catch { /* keep open */ }
    finally { setSaving(false) }
  }

  if (!editing) return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="font-semibold text-[var(--text-primary)] truncate">
        {backup.display_name || backup.jellyfin_playlist_name}
      </span>
      {backup.display_name && backup.display_name !== backup.jellyfin_playlist_name && (
        <span className="text-xs text-[var(--text-muted)] truncate hidden sm:block">
          (Jellyfin: {backup.jellyfin_playlist_name})
        </span>
      )}
      <button onClick={() => setEditing(true)} title="Edit restore name"
        className="flex-shrink-0 p-1 rounded opacity-40 hover:opacity-100 hover:bg-white/10 transition-all">
        <Pencil size={11} style={{ color: 'var(--text-muted)' }} />
      </button>
    </div>
  )

  return (
    <div className="flex items-center gap-2">
      <input autoFocus value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
        placeholder={backup.jellyfin_playlist_name}
        className="px-2 py-1 rounded border text-sm text-[var(--text-primary)] w-52"
        style={{ background: 'var(--bg)', borderColor: 'var(--border)' }} />
      <button onClick={save} disabled={saving}
        className="p-1.5 rounded text-emerald-400 hover:bg-white/10 disabled:opacity-50">
        {saving ? <Spinner size={13} /> : <Check size={13} />}
      </button>
      <button onClick={() => setEditing(false)}
        className="p-1.5 rounded hover:bg-white/10" style={{ color: 'var(--text-muted)' }}>
        <X size={13} />
      </button>
    </div>
  )
}

// ── Single revision row ───────────────────────────────────────────────────────

function RevisionRow({ backupId, revision, isLatest, totalRevisions, onRestored, onDeleted, onLabeled }) {
  const [restoring, setRestoring] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [editingLabel, setEditingLabel] = useState(false)
  const [labelValue, setLabelValue] = useState(revision.label || '')
  const [savingLabel, setSavingLabel] = useState(false)
  const [restoreResult, setRestoreResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleRestore() {
    const name = revision.label ? `"${revision.label}"` : `revision #${revision.revision_number}`
    if (!window.confirm(
      `Restore ${name} to Jellyfin?\n\n` +
      `This will write ${revision.track_count} track(s) into the playlist.\n` +
      `If the playlist already exists in Jellyfin it will be overwritten.`
    )) return
    setRestoring(true); setError(null); setRestoreResult(null)
    try {
      const res = await api.post(
        `/api/playlist-backups/${backupId}/revisions/${revision.id}/restore`
      )
      setRestoreResult(res); onRestored?.()
    } catch (e) { setError(e.message) }
    finally { setRestoring(false) }
  }

  async function handleDelete() {
    if (!window.confirm(
      `Delete revision #${revision.revision_number}?\n\n` +
      `This revision's ${revision.track_count} stored tracks will be permanently removed.`
    )) return
    setDeleting(true)
    try {
      await api.delete(`/api/playlist-backups/${backupId}/revisions/${revision.id}`)
      onDeleted(revision.id)
    } catch (e) { setError(e.message); setDeleting(false) }
  }

  async function saveLabel() {
    setSavingLabel(true)
    try {
      const updated = await api.post(
        `/api/playlist-backups/${backupId}/revisions/${revision.id}/label`,
        { label: labelValue.trim() || null }
      )
      onLabeled(updated); setEditingLabel(false)
    } catch (e) { setError(e.message) }
    finally { setSavingLabel(false) }
  }

  const canDelete = totalRevisions > 1

  return (
    <div className={`rounded-lg border px-3 py-2.5 space-y-2 ${
      isLatest ? 'border-[var(--accent)]/30 bg-[var(--accent)]/5' : ''
    }`} style={!isLatest ? { borderColor: 'var(--border)', background: 'var(--bg)' } : {}}>

      <div className="flex items-center gap-2 flex-wrap">
        {/* Revision number + latest badge */}
        <span className="text-xs font-mono font-semibold" style={{ color: 'var(--text-muted)' }}>
          #{revision.revision_number}
        </span>
        {isLatest && <Badge variant="success">Latest</Badge>}
        {revision.is_labeled && (
          <Badge variant="pinned"><Tag size={9} /> {revision.label}</Badge>
        )}

        {/* Track count + timestamp */}
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {revision.track_count} tracks
        </span>
        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--text-muted)' }}>
          <Clock size={10} /> {fmtShort(revision.backed_up_at)}
        </span>

        {/* Actions */}
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          {/* Label/pin toggle */}
          {!editingLabel ? (
            <button onClick={() => { setLabelValue(revision.label || ''); setEditingLabel(true) }}
              title={revision.label ? 'Edit label (pinned — won\'t be pruned)' : 'Pin this revision with a label'}
              className={`p-1.5 rounded-lg text-xs transition-colors ${
                revision.is_labeled
                  ? 'bg-purple-500/20 text-purple-300'
                  : 'hover:bg-white/10 text-[var(--text-muted)]'
              }`}>
              <Tag size={12} />
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <input autoFocus value={labelValue}
                onChange={e => setLabelValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') saveLabel(); if (e.key === 'Escape') setEditingLabel(false) }}
                placeholder="Label to pin (blank to unpin)"
                className="px-2 py-1 rounded border text-xs w-36"
                style={{ background: 'var(--bg)', borderColor: 'var(--border)', color: 'var(--text-primary)' }} />
              <button onClick={saveLabel} disabled={savingLabel}
                className="p-1 rounded text-emerald-400 hover:bg-white/10">
                {savingLabel ? <Spinner size={11} /> : <Check size={11} />}
              </button>
              <button onClick={() => setEditingLabel(false)}
                className="p-1 rounded hover:bg-white/10" style={{ color: 'var(--text-muted)' }}>
                <X size={11} />
              </button>
            </div>
          )}

          {/* Restore */}
          <button onClick={handleRestore} disabled={restoring}
            title="Restore this revision to Jellyfin"
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium text-white transition-opacity disabled:opacity-50"
            style={{ background: 'var(--accent)' }}>
            {restoring ? <Spinner size={11} /> : <RotateCcw size={11} />}
            Restore
          </button>

          {/* Delete */}
          {canDelete && (
            <button onClick={handleDelete} disabled={deleting}
              title="Delete this revision"
              className="p-1.5 rounded-lg hover:bg-red-500/15 text-[var(--text-muted)] hover:text-red-400 transition-colors disabled:opacity-50">
              {deleting ? <Spinner size={12} /> : <Trash2 size={12} />}
            </button>
          )}
        </div>
      </div>

      {restoreResult && (
        <Notice type="success">
          Restored to Jellyfin — "{restoreResult.playlist_name}" {restoreResult.action}
          with {restoreResult.track_count} tracks.
        </Notice>
      )}
      {error && <Notice type="error">{error}</Notice>}
    </div>
  )
}

// ── Revision history panel ────────────────────────────────────────────────────

function RevisionHistory({ backup, onUpdated }) {
  const [open, setOpen] = useState(false)
  const [revisions, setRevisions] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    setLoading(true); setError(null)
    try {
      const data = await api.get(`/api/playlist-backups/${backup.id}/revisions`)
      setRevisions(data.revisions)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  // Reload the revision list whenever a new backup is created for this playlist
  // (detected via revision_count changing). Only fires if the panel is already
  // open and has previously loaded — avoids a double-fetch on initial expand.
  useEffect(() => {
    if (open && revisions !== null) load()
  }, [backup.revision_count]) // eslint-disable-line react-hooks/exhaustive-deps

  function handleToggle() {
    const next = !open
    setOpen(next)
    if (next && !revisions) load()
  }

  function handleDeleted(revId) {
    setRevisions(prev => prev.filter(r => r.id !== revId))
    onUpdated?.()
  }

  function handleLabeled(updated) {
    setRevisions(prev => prev.map(r => r.id === updated.id ? updated : r))
  }

  const revCount = backup.revision_count ?? 0

  return (
    <div>
      <button onClick={handleToggle}
        className="flex items-center gap-2 text-xs hover:text-[var(--text-secondary)] transition-colors"
        style={{ color: 'var(--text-muted)' }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <History size={13} />
        <span>
          {revCount} revision{revCount !== 1 ? 's' : ''} stored
          {backup.max_revisions ? ` (max ${backup.max_revisions})` : ''}
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-1.5 pl-4 border-l" style={{ borderColor: 'var(--border)' }}>
          {loading && (
            <div className="flex items-center gap-2 text-xs py-2" style={{ color: 'var(--text-muted)' }}>
              <Spinner size={12} /> Loading revisions…
            </div>
          )}
          {error && <Notice type="error">{error}</Notice>}
          {revisions && revisions.length === 0 && (
            <p className="text-xs py-1" style={{ color: 'var(--text-muted)' }}>No revisions yet.</p>
          )}
          {revisions && revisions.map((rev, idx) => (
            <RevisionRow
              key={rev.id}
              backupId={backup.id}
              revision={rev}
              isLatest={idx === 0}
              totalRevisions={revisions.length}
              onRestored={() => onUpdated?.()}
              onDeleted={handleDeleted}
              onLabeled={handleLabeled}
            />
          ))}

          {/* Max revisions editor */}
          <MaxRevisionsEditor backup={backup} onUpdated={onUpdated} />
        </div>
      )}
    </div>
  )
}

function MaxRevisionsEditor({ backup, onUpdated }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(backup.max_revisions)
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      const updated = await api.patch(`/api/playlist-backups/${backup.id}`, {
        max_revisions: parseInt(value, 10),
      })
      onUpdated?.(updated); setEditing(false)
    } catch { /* keep open */ }
    finally { setSaving(false) }
  }

  if (!editing) return (
    <button onClick={() => setEditing(true)}
      className="text-xs hover:underline" style={{ color: 'var(--text-muted)' }}>
      Change revision limit ({backup.max_revisions} kept)
    </button>
  )

  return (
    <div className="flex items-center gap-2 pt-1">
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Keep</span>
      <input type="number" min={1} max={20} value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
        className="w-14 px-2 py-1 rounded border text-xs text-[var(--text-primary)]"
        style={{ background: 'var(--bg)', borderColor: 'var(--border)' }} />
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>revisions</span>
      <button onClick={save} disabled={saving}
        className="p-1 rounded text-emerald-400 hover:bg-white/10 disabled:opacity-50">
        {saving ? <Spinner size={12} /> : <Check size={12} />}
      </button>
      <button onClick={() => setEditing(false)}
        className="p-1 rounded hover:bg-white/10" style={{ color: 'var(--text-muted)' }}>
        <X size={12} />
      </button>
    </div>
  )
}

// ── Full backup row ───────────────────────────────────────────────────────────

function BackupRow({ backup, onUpdated, onDeleted }) {
  const [rebackingUp, setRebackingUp] = useState(false)
  const [rebackupStatus, setRebackupStatus] = useState(null) // null | 'ok' | 'error'
  const [togglingSnapshot, setTogglingSnapshot] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState(null)

  async function handleRebackup() {
    setRebackingUp(true); setError(null); setRebackupStatus(null)
    try {
      const res = await api.post('/api/playlist-backups/backup', {
        jellyfin_playlist_ids: [backup.jellyfin_playlist_id],
      })
      onUpdated(res.backed_up[0])
      setRebackupStatus('ok')
      setTimeout(() => setRebackupStatus(null), 4000)
    } catch (e) { setError(e.message); setRebackupStatus('error') }
    finally { setRebackingUp(false) }
  }

  async function handleSnapshotToggle() {
    setTogglingSnapshot(true); setError(null)
    try {
      const updated = await api.patch(`/api/playlist-backups/${backup.id}`, {
        exclude_from_auto: !backup.exclude_from_auto,
      })
      onUpdated(updated)
    } catch (e) { setError(e.message) }
    finally { setTogglingSnapshot(false) }
  }

  async function handleDelete() {
    if (!window.confirm(
      `Delete ALL backups for "${backup.effective_name}"?\n\n` +
      `This will remove all ${backup.revision_count} revision(s) and their stored tracks.\n` +
      `The actual playlist in Jellyfin is not affected.`
    )) return
    setDeleting(true)
    try {
      await api.delete(`/api/playlist-backups/${backup.id}`)
      onDeleted(backup.id)
    } catch (e) { setError(e.message); setDeleting(false) }
  }

  return (
    <div className="rounded-xl border overflow-hidden"
      style={{
        background: 'var(--surface)',
        borderColor: backup.exclude_from_auto ? 'rgba(245,158,11,0.25)' : 'var(--border)',
      }}>

      {/* Header */}
      <div className="px-4 pt-4 pb-3">
        <NameEditor backup={backup} onUpdated={onUpdated} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5">
          <Badge variant="info">{backup.track_count} tracks</Badge>
          {backup.exclude_from_auto && (
            <Badge variant="warning"><Shield size={9} /> Frozen — auto skipped</Badge>
          )}
          <span className="text-xs flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
            {rebackupStatus === 'ok'
              ? <CheckCircle2 size={13} className="text-emerald-400" />
              : <Clock size={11} />}
            <span className={rebackupStatus === 'ok' ? 'text-emerald-400' : ''}>
              {rebackupStatus === 'ok' ? 'Backed up ' : ''}
              {fmt(backup.last_backed_up_at)}
            </span>
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="px-4 pb-3 pt-1 flex flex-wrap items-center gap-2 border-t"
        style={{ borderColor: 'var(--border)' }}>
        <ActionBtn onClick={handleRebackup} loading={rebackingUp}
          icon={DatabaseBackup} label="Re-backup now" variant="default"
          title="Pull current playlist from Jellyfin and create a new revision" />

        <ActionBtn onClick={handleSnapshotToggle} loading={togglingSnapshot}
          icon={backup.exclude_from_auto ? ShieldOff : Shield}
          label={backup.exclude_from_auto ? 'Unfreeze' : 'Freeze (skip auto)'}
          variant={backup.exclude_from_auto ? 'warning' : 'default'}
          title={backup.exclude_from_auto
            ? 'Allow auto-backup to update this playlist again'
            : 'Stop auto-backup from updating this playlist'} />

        <ActionBtn onClick={handleDelete} loading={deleting}
          icon={Trash2} label="Delete all backups" variant="danger"
          title="Delete all revisions for this playlist — does NOT touch Jellyfin" />
      </div>

      {error && <div className="px-4 pb-3"><Notice type="error">{error}</Notice></div>}

      {/* Revision history */}
      <div className="px-4 pb-4 border-t" style={{ borderColor: 'var(--border)', paddingTop: 10 }}>
        <RevisionHistory backup={backup} onUpdated={onUpdated} />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PlaylistBackups() {
  const [availablePlaylists, setAvailablePlaylists] = useState([])
  const [backups, setBackups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showManaged, setShowManaged] = useState(false)

  const [selected, setSelected] = useState(new Set())
  const [backingUpSelected, setBackingUpSelected] = useState(false)
  const [backingUpAll, setBackingUpAll] = useState(false)
  const [actionResult, setActionResult] = useState(null)

  const load = useCallback(async (includeManagedOverride) => {
    const inc = includeManagedOverride !== undefined ? includeManagedOverride : showManaged
    setLoading(true); setError(null)
    try {
      const [jf, stored] = await Promise.all([
        api.get(`/api/playlist-backups/jellyfin-playlists?include_managed=${inc}`),
        api.get('/api/playlist-backups'),
      ])
      const backedUpIds = new Set(stored.map(b => b.jellyfin_playlist_id))
      setAvailablePlaylists(jf.filter(p => !backedUpIds.has(p.id)))
      setBackups(stored)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }, [showManaged])

  useEffect(() => { load() }, [load])

  async function handleToggleManaged() {
    const next = !showManaged; setShowManaged(next); setSelected(new Set())
    await load(next)
  }

  function toggleSelect(id) {
    setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  function toggleAll() {
    const ids = availablePlaylists.map(p => p.id)
    setSelected(ids.length > 0 && ids.every(id => selected.has(id)) ? new Set() : new Set(ids))
  }

  async function handleBackupSelected() {
    if (!selected.size) return
    setBackingUpSelected(true); setActionResult(null)
    try {
      const res = await api.post('/api/playlist-backups/backup', {
        jellyfin_playlist_ids: [...selected],
      })
      const newIds = new Set(res.backed_up.map(b => b.jellyfin_playlist_id))
      setAvailablePlaylists(prev => prev.filter(p => !newIds.has(p.id)))
      setBackups(prev => {
        const map = Object.fromEntries(prev.map(b => [b.jellyfin_playlist_id, b]))
        for (const b of res.backed_up) map[b.jellyfin_playlist_id] = b
        return Object.values(map)
      })
      setSelected(new Set())
      setActionResult({ type: 'success', msg: `Backed up ${res.backed_up.length} playlist(s).` })
    } catch (e) { setActionResult({ type: 'error', msg: e.message }) }
    finally { setBackingUpSelected(false) }
  }

  async function handleBackupAll() {
    setBackingUpAll(true); setActionResult(null)
    try {
      const res = await api.post('/api/playlist-backups/backup-all')
      await load()
      const parts = [`Backed up ${res.backed_up.length} playlist(s).`]
      if (res.skipped_managed?.length) parts.push(`Skipped ${res.skipped_managed.length} JellyDJ-managed.`)
      if (res.skipped_snapshots?.length) parts.push(`Skipped ${res.skipped_snapshots.length} frozen.`)
      setActionResult({ type: 'success', msg: parts.join(' ') })
    } catch (e) { setActionResult({ type: 'error', msg: e.message }) }
    finally { setBackingUpAll(false) }
  }

  function handleBackupUpdated(updated) {
    setBackups(prev => prev.map(b => b.id === updated.id ? updated : b))
  }

  function handleBackupDeleted(id) {
    setBackups(prev => prev.filter(b => b.id !== id)); load()
  }

  const allSelected = availablePlaylists.length > 0 &&
    availablePlaylists.every(p => selected.has(p.id))

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>
          Playlist Backups
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Each backup keeps up to 6 rolling revisions so you can revert if tracks go missing.
          Pin a revision with a label to keep it permanently regardless of the rotation limit.
        </p>
      </div>

      <BackupSettingsPanel onSaved={load} />

      {loading && (
        <div className="flex items-center gap-3 text-[var(--text-secondary)] text-sm py-4">
          <Spinner size={16} /> Loading playlists…
        </div>
      )}
      {error && <Notice type="error">{error}</Notice>}

      {/* ── Available to back up ── */}
      {!loading && !error && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-[var(--text-primary)]">
                Available to Back Up
              </h2>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                User-created Jellyfin playlists that don't have a backup yet.
              </p>
            </div>
            <button onClick={handleToggleManaged}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors hover:bg-white/10 flex-shrink-0"
              style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
              {showManaged ? <EyeOff size={12} /> : <Eye size={12} />}
              {showManaged ? 'Hide managed' : 'Show managed'}
            </button>
          </div>

          {availablePlaylists.length === 0 ? (
            <div className="rounded-xl border px-5 py-8 text-center"
              style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
              <Check size={24} className="mx-auto mb-2 text-emerald-400 opacity-70" />
              <p className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>
                All playlists are backed up
              </p>
              {!showManaged && (
                <button onClick={handleToggleManaged}
                  className="mt-2 text-xs underline opacity-50 hover:opacity-80">
                  Show JellyDJ-managed playlists
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 flex-wrap">
                <ActionBtn onClick={handleBackupSelected} loading={backingUpSelected}
                  disabled={!selected.size} icon={DatabaseBackup}
                  label={`Back up selected (${selected.size})`} variant="primary" />
                <ActionBtn onClick={handleBackupAll} loading={backingUpAll}
                  icon={DatabaseBackup} label="Back up all" variant="default" />
                <button onClick={() => load()} disabled={loading}
                  className="ml-auto p-2 rounded-lg hover:bg-white/10 transition-colors">
                  <RotateCw size={14} style={{ color: 'var(--text-muted)' }} />
                </button>
              </div>

              {actionResult && <Notice type={actionResult.type}>{actionResult.msg}</Notice>}

              <div className="rounded-xl border overflow-hidden"
                style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
                <div className="flex items-center gap-3 px-4 py-2.5 border-b"
                  style={{ borderColor: 'var(--border)' }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} className="rounded" />
                  <span className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                    {availablePlaylists.length} playlist{availablePlaylists.length !== 1 ? 's' : ''}
                  </span>
                </div>
                <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
                  {availablePlaylists.map(p => (
                    <label key={p.id}
                      className={`flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors ${
                        p.is_managed ? 'opacity-50' : 'hover:bg-white/5'
                      }`}>
                      <input type="checkbox" checked={selected.has(p.id)}
                        onChange={() => toggleSelect(p.id)} className="rounded flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <span className="text-sm text-[var(--text-primary)] truncate block">{p.name}</span>
                        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                          {p.track_count} tracks
                        </span>
                      </div>
                      {p.is_managed && <Badge variant="muted">JellyDJ managed</Badge>}
                    </label>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {actionResult && availablePlaylists.length === 0 && (
        <Notice type={actionResult.type}>{actionResult.msg}</Notice>
      )}

      {/* ── Stored backups ── */}
      {!loading && !error && (
        <div className="space-y-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--text-primary)]">
              Stored Backups
              {backups.length > 0 && (
                <span className="ml-2 text-sm font-normal" style={{ color: 'var(--text-muted)' }}>
                  ({backups.length})
                </span>
              )}
            </h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Expand the revision history to restore an older version or pin it permanently.
            </p>
          </div>

          {backups.length === 0 ? (
            <div className="rounded-xl border px-5 py-10 text-center"
              style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
              <DatabaseBackup size={30} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">No backups yet. Select playlists above and back them up.</p>
            </div>
          ) : (
            backups
              .slice()
              .sort((a, b) => a.effective_name.localeCompare(b.effective_name))
              .map(b => (
                <BackupRow key={b.id} backup={b}
                  onUpdated={handleBackupUpdated}
                  onDeleted={handleBackupDeleted} />
              ))
          )}
        </div>
      )}

      {/* Legend */}
      {!loading && !error && (
        <div className="rounded-xl border px-4 py-3 text-xs space-y-2"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
          <p className="font-medium" style={{ color: 'var(--text-secondary)' }}>How revisions work</p>
          <p>Each backup or auto-backup creates a new revision. The oldest unlabeled revisions are
            automatically pruned once the limit is reached (default 6 per playlist).</p>
          <p className="flex items-start gap-2">
            <Tag size={12} className="flex-shrink-0 mt-0.5 text-purple-300" />
            <span><strong className="text-purple-300">Pin a revision</strong> by giving it a label —
              pinned revisions are never pruned regardless of the limit. Use this to permanently
              preserve a known-good state before making changes.</span>
          </p>
          <p className="flex items-start gap-2">
            <RotateCcw size={12} className="flex-shrink-0 mt-0.5" style={{ color: 'var(--accent)' }} />
            <span><strong>Restore</strong> writes any revision back to Jellyfin. Creates or overwrites
              the playlist — existing Jellyfin tracks are replaced with the revision's stored tracks.</span>
          </p>
        </div>
      )}
    </div>
  )
}
