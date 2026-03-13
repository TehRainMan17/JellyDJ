
/**
 * DefaultPlaylistsPanel.jsx
 *
 * Admin UI for configuring which playlists every new (and existing) user
 * gets provisioned with automatically.
 *
 * Shows:
 *   - The current default playlist config rows
 *   - An "Add default playlist" form (pick template, set name + schedule)
 *   - Per-config edit / delete controls
 *   - A "Provision All Users" button to sweep existing users
 *   - Per-user provision buttons on the tracked users list (via prop callback)
 *
 * Used inside Connections.jsx, rendered in the JellyfinCard users section.
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Plus, Trash2, Loader2, CheckCircle2, AlertCircle,
  RefreshCw, Users, Zap, Clock, ChevronDown, ChevronUp,
  Music2, Settings2, Save, X
} from 'lucide-react'
import { api } from '../lib/api'

// ── Tiny shared primitives (match Connections.jsx style) ─────────────────────

function FieldLabel({ children }) {
  return (
    <label className="block text-xs font-medium uppercase tracking-wider mb-1.5"
      style={{ color: 'var(--text-secondary)' }}>
      {children}
    </label>
  )
}

function SmallInput({ value, onChange, type = 'text', min, max, placeholder, className = '' }) {
  return (
    <input
      type={type} value={value} onChange={onChange}
      min={min} max={max} placeholder={placeholder}
      className={`w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-colors ${className}`}
      style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        color: 'var(--text-primary)',
      }}
      onFocus={e => e.currentTarget.style.borderColor = 'rgba(83,236,252,0.5)'}
      onBlur={e => e.currentTarget.style.borderColor = 'var(--border)'}
    />
  )
}

function ToggleChip({ value, onChange, labelOn = 'Enabled', labelOff = 'Disabled' }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className="px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
      style={{
        background: value ? 'rgba(83,236,252,0.1)' : 'rgba(255,255,255,0.04)',
        border: `1px solid ${value ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
        color: value ? 'var(--accent)' : 'var(--text-secondary)',
      }}>
      {value ? labelOn : labelOff}
    </button>
  )
}

// ── Interval selector — common schedule intervals ─────────────────────────────

const INTERVAL_OPTIONS = [
  { h: 6,    label: 'Every 6 hours' },
  { h: 12,   label: 'Every 12 hours' },
  { h: 24,   label: 'Every day' },
  { h: 48,   label: 'Every 2 days' },
  { h: 168,  label: 'Every week' },
]

function IntervalPicker({ value, onChange }) {
  const matched = INTERVAL_OPTIONS.find(o => o.h === value)
  return (
    <div className="flex flex-wrap gap-1.5">
      {INTERVAL_OPTIONS.map(o => (
        <button key={o.h} onClick={() => onChange(o.h)}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
          style={{
            background: o.h === value ? 'rgba(83,236,252,0.1)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${o.h === value ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
            color: o.h === value ? 'var(--accent)' : 'var(--text-secondary)',
          }}>
          {o.label}
        </button>
      ))}
      {!matched && (
        <span className="text-xs self-center" style={{ color: 'var(--text-muted)' }}>
          Custom: {value}h
        </span>
      )}
    </div>
  )
}

// ── ConfigRow — one editable default playlist entry ───────────────────────────

function ConfigRow({ cfg, onDelete, onUpdate }) {
  const [editing,  setEditing]  = useState(false)
  const [saving,   setSaving]   = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirm,  setConfirm]  = useState(false)
  const [draft,    setDraft]    = useState({
    base_name:           cfg.base_name,
    schedule_enabled:    cfg.schedule_enabled,
    schedule_interval_h: cfg.schedule_interval_h,
  })

  const handleSave = async () => {
    setSaving(true)
    try {
      const updated = await api.put(`/api/admin/default-playlists/${cfg.id}`, draft)
      onUpdate(updated)
      setEditing(false)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await api.delete(`/api/admin/default-playlists/${cfg.id}`)
      onDelete(cfg.id)
    } finally {
      setDeleting(false)
      setConfirm(false)
    }
  }

  return (
    <div className="rounded-xl p-3.5"
      style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)' }}>

      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(83,236,252,0.1)', border: '1px solid rgba(83,236,252,0.2)' }}>
            <Music2 size={14} style={{ color: 'var(--accent)' }} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
              {cfg.base_name}
            </div>
            <div className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
              {cfg.template_name ?? `Template #${cfg.template_id}`}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          {/* Schedule badge */}
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold"
            style={{
              background: cfg.schedule_enabled ? 'rgba(83,236,252,0.08)' : 'rgba(255,255,255,0.04)',
              border: `1px solid ${cfg.schedule_enabled ? 'rgba(83,236,252,0.25)' : 'var(--border)'}`,
              color: cfg.schedule_enabled ? 'var(--accent)' : 'var(--text-muted)',
            }}>
            <Clock size={9} />
            {cfg.schedule_enabled
              ? INTERVAL_OPTIONS.find(o => o.h === cfg.schedule_interval_h)?.label ?? `${cfg.schedule_interval_h}h`
              : 'Manual'}
          </span>

          {/* Edit toggle */}
          <button onClick={() => { setEditing(v => !v); setConfirm(false) }}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: editing ? 'var(--accent)' : 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = editing ? 'var(--accent)' : 'var(--text-muted)'}>
            <Settings2 size={13} />
          </button>

          {/* Delete */}
          {confirm ? (
            <div className="flex items-center gap-1.5">
              <span className="text-[10px]" style={{ color: 'var(--danger)' }}>Remove?</span>
              <button onClick={handleDelete} disabled={deleting}
                className="px-2 py-0.5 rounded text-[10px] font-semibold transition-colors"
                style={{ background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.3)', color: 'var(--danger)' }}>
                {deleting ? <Loader2 size={9} className="animate-spin" /> : 'Confirm'}
              </button>
              <button onClick={() => setConfirm(false)}
                className="text-[10px] transition-colors"
                style={{ color: 'var(--text-muted)' }}>Cancel</button>
            </div>
          ) : (
            <button onClick={() => setConfirm(true)}
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Inline edit form */}
      {editing && (
        <div className="mt-3 pt-3 space-y-3" style={{ borderTop: '1px solid var(--border)' }}>
          <div>
            <FieldLabel>Playlist name shown to users</FieldLabel>
            <SmallInput value={draft.base_name}
              onChange={e => setDraft(d => ({ ...d, base_name: e.target.value }))} />
          </div>
          <div>
            <FieldLabel>Auto-push schedule</FieldLabel>
            <div className="flex items-center gap-2 mb-2">
              <ToggleChip value={draft.schedule_enabled}
                onChange={v => setDraft(d => ({ ...d, schedule_enabled: v }))}
                labelOn="Schedule on" labelOff="Manual only" />
            </div>
            {draft.schedule_enabled && (
              <IntervalPicker value={draft.schedule_interval_h}
                onChange={h => setDraft(d => ({ ...d, schedule_interval_h: h }))} />
            )}
          </div>
          <div className="flex items-center gap-2 pt-1">
            <button onClick={handleSave} disabled={saving}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
              style={{ background: 'var(--accent)', color: 'var(--bg)' }}>
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
              Save
            </button>
            <button onClick={() => { setEditing(false); setDraft({ base_name: cfg.base_name, schedule_enabled: cfg.schedule_enabled, schedule_interval_h: cfg.schedule_interval_h }) }}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
              style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              <X size={11} /> Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── AddConfigForm — inline form for adding a new default ──────────────────────

function AddConfigForm({ templates, onAdded, onCancel }) {
  const [templateId,        setTemplateId]        = useState(templates[0]?.id ?? '')
  const [baseName,          setBaseName]          = useState('')
  const [scheduleEnabled,   setScheduleEnabled]   = useState(true)
  const [scheduleIntervalH, setScheduleIntervalH] = useState(24)
  const [saving,            setSaving]            = useState(false)
  const [err,               setErr]               = useState('')

  // Auto-fill the name from the selected template
  const selectedTemplate = templates.find(t => t.id === Number(templateId))
  useEffect(() => {
    if (selectedTemplate && !baseName) setBaseName(selectedTemplate.name)
  }, [templateId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleAdd = async () => {
    if (!templateId) { setErr('Select a template.'); return }
    if (!baseName.trim()) { setErr('Enter a playlist name.'); return }
    setSaving(true); setErr('')
    try {
      const created = await api.post('/api/admin/default-playlists', {
        template_id:         Number(templateId),
        base_name:           baseName.trim(),
        schedule_enabled:    scheduleEnabled,
        schedule_interval_h: scheduleIntervalH,
      })
      onAdded(created)
    } catch (e) {
      setErr(e.message || 'Failed to add.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl p-4 space-y-3"
      style={{ background: 'var(--bg-surface)', border: '1px solid rgba(83,236,252,0.25)' }}>
      <div className="text-xs font-bold" style={{ color: 'var(--accent)' }}>Add default playlist</div>

      <div>
        <FieldLabel>Template</FieldLabel>
        <select value={templateId} onChange={e => setTemplateId(e.target.value)}
          className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-colors"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
          {templates.map(t => (
            <option key={t.id} value={t.id}>{t.name}{t.is_system ? ' ✦' : ''}</option>
          ))}
        </select>
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>✦ = system template</p>
      </div>

      <div>
        <FieldLabel>Playlist name shown to users</FieldLabel>
        <SmallInput value={baseName} onChange={e => setBaseName(e.target.value)}
          placeholder="e.g. For You · Daily Mix" />
      </div>

      <div>
        <FieldLabel>Default auto-push schedule</FieldLabel>
        <div className="flex items-center gap-2 mb-2">
          <ToggleChip value={scheduleEnabled} onChange={setScheduleEnabled}
            labelOn="Schedule on" labelOff="Manual only" />
        </div>
        {scheduleEnabled && (
          <IntervalPicker value={scheduleIntervalH} onChange={setScheduleIntervalH} />
        )}
      </div>

      {err && (
        <p className="text-xs" style={{ color: 'var(--danger)' }}>{err}</p>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button onClick={handleAdd} disabled={saving}
          className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
          style={{ background: 'var(--accent)', color: 'var(--bg)' }}>
          {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
          Add
        </button>
        <button onClick={onCancel}
          className="flex items-center gap-1 px-3.5 py-2 rounded-lg text-xs font-semibold transition-all"
          style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
          <X size={11} /> Cancel
        </button>
      </div>
    </div>
  )
}

// ── ProvisionAllButton ────────────────────────────────────────────────────────

function ProvisionAllButton({ disabled }) {
  const [loading, setLoading]   = useState(false)
  const [result,  setResult]    = useState(null)
  const [err,     setErr]       = useState('')
  const [confirm, setConfirm]   = useState(false)

  const run = async () => {
    setLoading(true); setErr(''); setResult(null); setConfirm(false)
    try {
      const data = await api.post('/api/admin/default-playlists/provision-all')
      setResult(data)
    } catch (e) {
      setErr(e.message || 'Failed to provision.')
    } finally {
      setLoading(false)
    }
  }

  if (result) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--accent)' }}>
        <CheckCircle2 size={13} />
        Provisioned {result.total_created} new playlist{result.total_created !== 1 ? 's' : ''} across {result.users_swept} user{result.users_swept !== 1 ? 's' : ''}
        <button onClick={() => setResult(null)}
          className="ml-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>Dismiss</button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {confirm ? (
        <>
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            Provision defaults to all managed users?
          </span>
          <button onClick={run} disabled={loading || disabled}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
            style={{ background: 'rgba(83,236,252,0.12)', border: '1px solid rgba(83,236,252,0.3)', color: 'var(--accent)' }}>
            {loading ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
            Provision All
          </button>
          <button onClick={() => setConfirm(false)}
            className="text-xs" style={{ color: 'var(--text-muted)' }}>Cancel</button>
        </>
      ) : (
        <button onClick={() => setConfirm(true)} disabled={disabled || loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
          style={{ background: 'rgba(83,236,252,0.08)', border: '1px solid rgba(83,236,252,0.2)', color: 'var(--accent)' }}>
          <Users size={12} />
          Provision All Users
        </button>
      )}
      {err && <span className="text-xs" style={{ color: 'var(--danger)' }}>{err}</span>}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────────────────

export default function DefaultPlaylistsPanel() {
  const [configs,    setConfigs]    = useState([])
  const [templates,  setTemplates]  = useState([])
  const [loading,    setLoading]    = useState(true)
  const [err,        setErr]        = useState('')
  const [showForm,   setShowForm]   = useState(false)
  const [collapsed,  setCollapsed]  = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const data = await api.get('/api/admin/default-playlists')
      setConfigs(data.configs ?? [])
      setTemplates(data.templates ?? [])
    } catch {
      setErr('Could not load default playlist configuration.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const handleAdded = (cfg) => {
    setConfigs(prev => [...prev, cfg])
    setShowForm(false)
  }
  const handleDeleted = (id) => setConfigs(prev => prev.filter(c => c.id !== id))
  const handleUpdated = (cfg) => setConfigs(prev => prev.map(c => c.id === cfg.id ? cfg : c))

  return (
    <div className="mt-4 rounded-xl overflow-hidden"
      style={{ border: '1px solid var(--border)', background: 'var(--bg-surface)' }}>

      {/* Header */}
      <button
        onClick={() => setCollapsed(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 transition-colors"
        style={{ borderBottom: collapsed ? 'none' : '1px solid var(--border)' }}
        onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{ background: 'rgba(83,236,252,0.1)', border: '1px solid rgba(83,236,252,0.2)' }}>
            <Music2 size={13} style={{ color: 'var(--accent)' }} />
          </div>
          <div className="text-left">
            <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              Default Playlists
            </div>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              {configs.length === 0
                ? 'No defaults configured — new users start with no playlists'
                : `${configs.length} default${configs.length !== 1 ? 's' : ''} · auto-provisioned to all users`}
            </div>
          </div>
        </div>
        {collapsed ? <ChevronDown size={14} style={{ color: 'var(--text-muted)' }} />
                   : <ChevronUp   size={14} style={{ color: 'var(--text-muted)' }} />}
      </button>

      {!collapsed && (
        <div className="p-4 space-y-4">

          {loading && (
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <Loader2 size={13} className="animate-spin" /> Loading…
            </div>
          )}

          {err && (
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--danger)' }}>
              <AlertCircle size={13} /> {err}
            </div>
          )}

          {!loading && !err && (
            <>
              {/* Description */}
              <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                Playlists configured here are automatically created for every user — new users get them
                on first login, and existing users can be provisioned with the button below.
                Users can rename, reschedule, or delete their copies without affecting these defaults.
              </p>

              {/* Config list */}
              {configs.length > 0 ? (
                <div className="space-y-2">
                  {configs.map(cfg => (
                    <ConfigRow key={cfg.id} cfg={cfg}
                      onDelete={handleDeleted} onUpdate={handleUpdated} />
                  ))}
                </div>
              ) : (
                <div className="flex items-center gap-2 py-3 px-3 rounded-lg text-xs"
                  style={{ background: 'var(--bg-overlay)', color: 'var(--text-muted)', border: '1px dashed var(--border)' }}>
                  <Music2 size={13} />
                  No default playlists yet. Add one below to get started.
                </div>
              )}

              {/* Add form or button */}
              {showForm ? (
                <AddConfigForm
                  templates={templates}
                  onAdded={handleAdded}
                  onCancel={() => setShowForm(false)}
                />
              ) : (
                <button onClick={() => setShowForm(true)}
                  className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg transition-all"
                  style={{ color: 'var(--accent)', background: 'rgba(83,236,252,0.06)', border: '1px dashed rgba(83,236,252,0.35)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(83,236,252,0.12)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'rgba(83,236,252,0.06)'}>
                  <Plus size={12} /> Add default playlist
                </button>
              )}

              {/* Divider */}
              {configs.length > 0 && (
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                  <div className="text-[11px] font-semibold mb-2 uppercase tracking-wider"
                    style={{ color: 'var(--text-muted)' }}>
                    Existing users
                  </div>
                  <ProvisionAllButton disabled={configs.length === 0} />
                  <p className="text-[11px] mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                    Provision All is safe to run multiple times — it skips any playlist
                    the user already has for that template.
                  </p>
                </div>
              )}

              {/* Refresh */}
              <button onClick={fetchData}
                className="flex items-center gap-1.5 text-xs transition-colors"
                style={{ color: 'var(--text-secondary)' }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--text-secondary)'}>
                <RefreshCw size={11} /> Refresh
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * UserProvisionButton — inline provision button for a single user.
 * Used inside TrackedUsersPanel next to each user row.
 *
 * Usage:
 *   import { UserProvisionButton } from './DefaultPlaylistsPanel'
 *   <UserProvisionButton userId={user.jellyfin_user_id} />
 */
export function UserProvisionButton({ userId }) {
  const [loading, setLoading] = useState(false)
  const [done,    setDone]    = useState(null)   // null | { playlists_created: number }
  const [err,     setErr]     = useState('')

  const run = async () => {
    setLoading(true); setErr(''); setDone(null)
    try {
      const data = await api.post(`/api/admin/default-playlists/provision/${userId}`)
      setDone(data)
    } catch (e) {
      setErr(e.message || 'Failed.')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <span className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--accent)' }}>
        <CheckCircle2 size={10} />
        {done.playlists_created === 0 ? 'Already up to date' : `+${done.playlists_created} added`}
      </span>
    )
  }

  return (
    <button onClick={run} disabled={loading}
      className="flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium transition-all disabled:opacity-40"
      style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', background: 'transparent' }}
      onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'rgba(83,236,252,0.4)' }}
      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.borderColor = 'var(--border)' }}
      title="Provision default playlists to this user">
      {loading ? <Loader2 size={10} className="animate-spin" /> : <Zap size={10} />}
      {err || 'Provision'}
    </button>
  )
}
