/**
 * AdminUsers.jsx — /admin/users
 *
 * Dedicated admin page for:
 *   1. Default Playlists — configure which playlists every user gets on login
 *   2. Managed Users — view, provision, and delete tracked users
 *
 * This replaces the buried panel inside Connections.jsx.
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Music2, Plus, Trash2, Loader2, CheckCircle2, AlertCircle,
  RefreshCw, Users, Zap, Clock, ChevronDown, ChevronUp,
  Settings2, Save, X, ShieldCheck, Shield,
} from 'lucide-react'
import { api } from '../lib/api'

// ── Interval options ──────────────────────────────────────────────────────────

const INTERVAL_OPTIONS = [
  { h: 6,   label: 'Every 6 h'  },
  { h: 12,  label: 'Every 12 h' },
  { h: 24,  label: 'Daily'      },
  { h: 48,  label: 'Every 2 d'  },
  { h: 168, label: 'Weekly'     },
]

function intervalLabel(h) {
  return INTERVAL_OPTIONS.find(o => o.h === h)?.label ?? `${h}h`
}

// ── Shared primitives ─────────────────────────────────────────────────────────

function SectionHeader({ title, subtitle }) {
  return (
    <div className="mb-5">
      <h2 className="text-base font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'Syne' }}>
        {title}
      </h2>
      {subtitle && (
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>{subtitle}</p>
      )}
    </div>
  )
}

function Card({ children, className = '' }) {
  return (
    <div className={`rounded-2xl p-5 ${className}`}
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      {children}
    </div>
  )
}

function Btn({ onClick, disabled, loading, variant = 'primary', size = 'md', children }) {
  const sizes = { sm: 'px-2.5 py-1 text-[11px]', md: 'px-3.5 py-2 text-xs' }
  const variants = {
    primary: { background: 'var(--accent)', color: 'var(--bg)', border: 'none' },
    ghost:   { background: 'rgba(255,255,255,0.05)', color: 'var(--text-secondary)', border: '1px solid var(--border)' },
    danger:  { background: 'rgba(248,113,113,0.1)', color: 'var(--danger)', border: '1px solid rgba(248,113,113,0.3)' },
    accent:  { background: 'rgba(83,236,252,0.08)', color: 'var(--accent)', border: '1px solid rgba(83,236,252,0.25)' },
  }
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      className={`flex items-center gap-1.5 rounded-lg font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed ${sizes[size]}`}
      style={variants[variant]}
    >
      {loading && <Loader2 size={11} className="animate-spin" />}
      {children}
    </button>
  )
}

function IntervalPicker({ value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {INTERVAL_OPTIONS.map(o => (
        <button key={o.h} onClick={() => onChange(o.h)}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
          style={{
            background: o.h === value ? 'rgba(83,236,252,0.12)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${o.h === value ? 'rgba(83,236,252,0.4)' : 'var(--border)'}`,
            color: o.h === value ? 'var(--accent)' : 'var(--text-secondary)',
          }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── DefaultPlaylistRow ────────────────────────────────────────────────────────

function DefaultPlaylistRow({ cfg, onDelete, onUpdate }) {
  const [editing,  setEditing]  = useState(false)
  const [saving,   setSaving]   = useState(false)
  const [confirm,  setConfirm]  = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [draft, setDraft] = useState({
    base_name:           cfg.base_name,
    schedule_enabled:    cfg.schedule_enabled,
    schedule_interval_h: cfg.schedule_interval_h,
  })

  const save = async () => {
    setSaving(true)
    try {
      const updated = await api.put(`/api/admin/default-playlists/${cfg.id}`, draft)
      onUpdate(updated)
      setEditing(false)
    } finally { setSaving(false) }
  }

  const remove = async () => {
    setDeleting(true)
    try {
      await api.delete(`/api/admin/default-playlists/${cfg.id}`)
      onDelete(cfg.id)
    } finally { setDeleting(false) }
  }

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ border: '1px solid var(--border)', background: 'var(--bg)' }}>

      {/* Main row */}
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgba(83,236,252,0.08)', border: '1px solid rgba(83,236,252,0.18)' }}>
          <Music2 size={14} style={{ color: 'var(--accent)' }} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {cfg.base_name}
          </div>
          <div className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
            {cfg.template_name ?? `Template #${cfg.template_id}`}
          </div>
        </div>

        {/* Schedule badge */}
        <span className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold flex-shrink-0"
          style={{
            background: cfg.schedule_enabled ? 'rgba(83,236,252,0.08)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${cfg.schedule_enabled ? 'rgba(83,236,252,0.25)' : 'var(--border)'}`,
            color: cfg.schedule_enabled ? 'var(--accent)' : 'var(--text-muted)',
          }}>
          <Clock size={10} />
          {cfg.schedule_enabled ? intervalLabel(cfg.schedule_interval_h) : 'Manual'}
        </span>

        {/* Actions */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <button onClick={() => { setEditing(v => !v); setConfirm(false) }}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: editing ? 'var(--accent)' : 'var(--text-muted)' }}
            title="Edit">
            <Settings2 size={14} />
          </button>
          {confirm ? (
            <div className="flex items-center gap-1.5 ml-1">
              <span className="text-[11px]" style={{ color: 'var(--danger)' }}>Remove?</span>
              <Btn size="sm" variant="danger" loading={deleting} onClick={remove}>Yes</Btn>
              <button onClick={() => setConfirm(false)} className="text-[11px]" style={{ color: 'var(--text-muted)' }}>No</button>
            </div>
          ) : (
            <button onClick={() => setConfirm(true)}
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
              title="Remove">
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Inline edit */}
      {editing && (
        <div className="px-4 pb-4 pt-2 space-y-3" style={{ borderTop: '1px solid var(--border)' }}>
          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-muted)' }}>
              Playlist name
            </label>
            <input value={draft.base_name} onChange={e => setDraft(d => ({ ...d, base_name: e.target.value }))}
              className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
          </div>
          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
              Auto-push schedule
            </label>
            <div className="flex items-center gap-2 mb-2">
              <button onClick={() => setDraft(d => ({ ...d, schedule_enabled: !d.schedule_enabled }))}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
                style={{
                  background: draft.schedule_enabled ? 'rgba(83,236,252,0.1)' : 'rgba(255,255,255,0.04)',
                  border: `1px solid ${draft.schedule_enabled ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
                  color: draft.schedule_enabled ? 'var(--accent)' : 'var(--text-secondary)',
                }}>
                {draft.schedule_enabled ? 'Scheduled' : 'Manual only'}
              </button>
            </div>
            {draft.schedule_enabled && (
              <IntervalPicker value={draft.schedule_interval_h}
                onChange={h => setDraft(d => ({ ...d, schedule_interval_h: h }))} />
            )}
          </div>
          <div className="flex gap-2 pt-1">
            <Btn variant="primary" loading={saving} onClick={save}><Save size={11} />Save</Btn>
            <Btn variant="ghost" onClick={() => setEditing(false)}><X size={11} />Cancel</Btn>
          </div>
        </div>
      )}
    </div>
  )
}

// ── AddDefaultForm ────────────────────────────────────────────────────────────

function AddDefaultForm({ templates, onAdded, onCancel }) {
  const [templateId,   setTemplateId]   = useState(templates[0]?.id ?? '')
  const [baseName,     setBaseName]     = useState('')
  const [schedEnabled, setSchedEnabled] = useState(true)
  const [intervalH,    setIntervalH]    = useState(24)
  const [saving,       setSaving]       = useState(false)
  const [err,          setErr]          = useState('')

  // Auto-fill name from selected template
  useEffect(() => {
    const t = templates.find(t => t.id === Number(templateId))
    if (t) setBaseName(t.name)
  }, [templateId]) // eslint-disable-line

  const submit = async () => {
    if (!templateId) { setErr('Select a template.'); return }
    if (!baseName.trim()) { setErr('Enter a playlist name.'); return }
    setSaving(true); setErr('')
    try {
      const created = await api.post('/api/admin/default-playlists', {
        template_id:         Number(templateId),
        base_name:           baseName.trim(),
        schedule_enabled:    schedEnabled,
        schedule_interval_h: intervalH,
      })
      onAdded(created)
    } catch (e) {
      setErr(e.message || 'Failed to add.')
    } finally { setSaving(false) }
  }

  return (
    <div className="rounded-xl p-4 space-y-4"
      style={{ background: 'var(--bg)', border: '1px solid rgba(83,236,252,0.3)' }}>

      <div className="text-xs font-bold" style={{ color: 'var(--accent)' }}>New default playlist</div>

      <div>
        <label className="block text-[11px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Template
        </label>
        <select value={templateId} onChange={e => setTemplateId(e.target.value)}
          className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none"
          style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
          {templates.map(t => (
            <option key={t.id} value={t.id}>{t.name}{t.is_system ? ' ✦' : ''}</option>
          ))}
        </select>
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>✦ = system template</p>
      </div>

      <div>
        <label className="block text-[11px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Playlist name shown to users
        </label>
        <input value={baseName} onChange={e => setBaseName(e.target.value)}
          placeholder="e.g. For You · Daily Mix"
          className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none"
          style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
      </div>

      <div>
        <label className="block text-[11px] font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
          Default auto-push schedule
        </label>
        <div className="flex items-center gap-2 mb-2">
          <button onClick={() => setSchedEnabled(v => !v)}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
            style={{
              background: schedEnabled ? 'rgba(83,236,252,0.1)' : 'rgba(255,255,255,0.04)',
              border: `1px solid ${schedEnabled ? 'rgba(83,236,252,0.35)' : 'var(--border)'}`,
              color: schedEnabled ? 'var(--accent)' : 'var(--text-secondary)',
            }}>
            {schedEnabled ? 'Scheduled' : 'Manual only'}
          </button>
        </div>
        {schedEnabled && <IntervalPicker value={intervalH} onChange={setIntervalH} />}
      </div>

      {err && <p className="text-xs" style={{ color: 'var(--danger)' }}>{err}</p>}

      <div className="flex gap-2">
        <Btn variant="primary" loading={saving} onClick={submit}><Plus size={11} />Add</Btn>
        <Btn variant="ghost" onClick={onCancel}><X size={11} />Cancel</Btn>
      </div>
    </div>
  )
}

// ── DefaultPlaylistsSection ───────────────────────────────────────────────────

function DefaultPlaylistsSection() {
  const [configs,   setConfigs]   = useState([])
  const [templates, setTemplates] = useState([])
  const [loading,   setLoading]   = useState(true)
  const [err,       setErr]       = useState('')
  const [showForm,  setShowForm]  = useState(false)
  const [provResult, setProvResult] = useState(null)
  const [provisioning, setProvisioning] = useState(false)
  const [confirmProvAll, setConfirmProvAll] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const data = await api.get('/api/admin/default-playlists')
      setConfigs(data.configs ?? [])
      setTemplates(data.templates ?? [])
    } catch { setErr('Could not load configuration.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const provisionAll = async () => {
    setProvisioning(true); setProvResult(null); setConfirmProvAll(false)
    try {
      const res = await api.post('/api/admin/default-playlists/provision-all')
      setProvResult(res)
    } finally { setProvisioning(false) }
  }

  return (
    <Card>
      <SectionHeader
        title="Default Playlists"
        subtitle="Every user gets these playlists automatically on first login. Existing users can be provisioned with the button below."
      />

      {loading && (
        <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--text-secondary)' }}>
          <Loader2 size={13} className="animate-spin" /> Loading…
        </div>
      )}

      {err && (
        <div className="flex items-center gap-2 text-xs py-2" style={{ color: 'var(--danger)' }}>
          <AlertCircle size={13} /> {err}
          <button onClick={load} className="underline ml-1">Retry</button>
        </div>
      )}

      {!loading && !err && (
        <div className="space-y-5">

          {/* Config list */}
          {configs.length === 0 && !showForm ? (
            <div className="flex items-center gap-3 py-4 px-4 rounded-xl text-sm"
              style={{ background: 'var(--bg)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
              <Music2 size={16} style={{ flexShrink: 0 }} />
              <span>No default playlists configured. Add one and every new user will have playlists waiting when they log in.</span>
            </div>
          ) : (
            <div className="space-y-2">
              {configs.map(cfg => (
                <DefaultPlaylistRow key={cfg.id} cfg={cfg}
                  onDelete={id => setConfigs(p => p.filter(c => c.id !== id))}
                  onUpdate={upd => setConfigs(p => p.map(c => c.id === upd.id ? upd : c))} />
              ))}
            </div>
          )}

          {/* Add form or add button */}
          {showForm ? (
            <AddDefaultForm
              templates={templates}
              onAdded={cfg => { setConfigs(p => [...p, cfg]); setShowForm(false) }}
              onCancel={() => setShowForm(false)}
            />
          ) : (
            <Btn variant="accent" onClick={() => setShowForm(true)}>
              <Plus size={12} /> Add default playlist
            </Btn>
          )}

          {/* Provision all existing users */}
          {configs.length > 0 && (
            <div className="pt-3" style={{ borderTop: '1px solid var(--border)' }}>
              <div className="text-xs font-semibold mb-2" style={{ color: 'var(--text-secondary)' }}>
                Push defaults to existing users
              </div>
              <p className="text-xs mb-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                New users get these playlists automatically on login. Use this to
                backfill users who registered before defaults were configured.
                Safe to run multiple times — already-covered playlists are skipped.
              </p>

              {provResult ? (
                <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--accent)' }}>
                  <CheckCircle2 size={13} />
                  Created {provResult.total_created} new playlist{provResult.total_created !== 1 ? 's' : ''} across {provResult.users_swept} user{provResult.users_swept !== 1 ? 's' : ''}
                  <button onClick={() => setProvResult(null)} className="ml-2" style={{ color: 'var(--text-muted)' }}>Dismiss</button>
                </div>
              ) : confirmProvAll ? (
                <div className="flex items-center gap-3">
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Provision all managed users?</span>
                  <Btn variant="accent" loading={provisioning} onClick={provisionAll}>
                    <Zap size={11} /> Yes, provision all
                  </Btn>
                  <button onClick={() => setConfirmProvAll(false)}
                    className="text-xs" style={{ color: 'var(--text-muted)' }}>Cancel</button>
                </div>
              ) : (
                <Btn variant="accent" onClick={() => setConfirmProvAll(true)}>
                  <Users size={12} /> Provision All Users
                </Btn>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

// ── UserRow ───────────────────────────────────────────────────────────────────

function UserRow({ user, onDeleted }) {
  const [provisioning, setProvisioning] = useState(false)
  const [provDone,     setProvDone]     = useState(null)
  const [confirm,      setConfirm]      = useState(false)
  const [deleting,     setDeleting]     = useState(false)

  const provision = async () => {
    setProvisioning(true); setProvDone(null)
    try {
      const res = await api.post(`/api/admin/default-playlists/provision/${user.jellyfin_user_id}`)
      setProvDone(res)
    } finally { setProvisioning(false) }
  }

  const remove = async () => {
    setDeleting(true)
    try {
      await api.delete(`/api/connections/jellyfin/users/${user.jellyfin_user_id}`)
      onDeleted(user.jellyfin_user_id)
    } finally { setDeleting(false); setConfirm(false) }
  }

  return (
    <div className="flex items-center gap-3 py-2.5 px-3 rounded-xl transition-colors"
      style={{ borderBottom: '1px solid var(--border)' }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>

      {/* Avatar */}
      <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
        style={{ background: 'var(--bg-overlay)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
        {(user.jellyfin_username || user.jellydj_username || '?')[0]?.toUpperCase()}
      </div>

      {/* Name + meta */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{user.jellyfin_username || user.jellydj_username || 'Unknown'}</span>
          {user.is_admin && (
            <span className="flex items-center gap-0.5 text-[10px] font-semibold" style={{ color: 'var(--accent)' }}>
              <ShieldCheck size={10} /> Admin
            </span>
          )}
        </div>
        {user.last_login_at && (
          <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
            Last login {new Date(user.last_login_at).toLocaleDateString()}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {/* Provision */}
        {provDone ? (
          <span className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--accent)' }}>
            <CheckCircle2 size={11} />
            {provDone.playlists_created === 0 ? 'Up to date' : `+${provDone.playlists_created} added`}
          </span>
        ) : (
          <Btn size="sm" variant="accent" loading={provisioning} onClick={provision}>
            <Zap size={10} /> Provision
          </Btn>
        )}

        {/* Delete */}
        {confirm ? (
          <div className="flex items-center gap-1.5">
            <span className="text-[11px]" style={{ color: 'var(--danger)' }}>Delete data?</span>
            <Btn size="sm" variant="danger" loading={deleting} onClick={remove}>Confirm</Btn>
            <button onClick={() => setConfirm(false)} className="text-[11px]" style={{ color: 'var(--text-muted)' }}>Cancel</button>
          </div>
        ) : (
          <button onClick={() => setConfirm(true)}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
            title="Delete all JellyDJ data for this user">
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  )
}

// ── ManagedUsersSection ───────────────────────────────────────────────────────

function ManagedUsersSection() {
  const [users,   setUsers]   = useState([])
  const [loading, setLoading] = useState(true)
  const [err,     setErr]     = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const data = await api.get('/api/connections/jellyfin/users/tracked')
      setUsers(data)
    } catch { setErr('Could not load users.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <Card>
      <div className="flex items-center justify-between mb-5">
        <SectionHeader
          title="Managed Users"
          subtitle="Users who have activated JellyDJ by pushing their first playlist."
        />
        <button onClick={load} className="flex items-center gap-1 text-xs transition-colors mt-1"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs py-4" style={{ color: 'var(--text-secondary)' }}>
          <Loader2 size={13} className="animate-spin" /> Loading users…
        </div>
      )}

      {err && (
        <p className="text-xs py-2" style={{ color: 'var(--danger)' }}>{err}</p>
      )}

      {!loading && !err && users.length === 0 && (
        <div className="flex items-center gap-3 py-4 px-4 rounded-xl text-sm"
          style={{ background: 'var(--bg)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
          <Users size={16} style={{ flexShrink: 0 }} />
          No users have activated JellyDJ yet. Users activate automatically when they push their first playlist.
        </div>
      )}

      {!loading && users.length > 0 && (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          {users.map(user => (
            <UserRow key={user.jellyfin_user_id} user={user}
              onDeleted={id => setUsers(p => p.filter(u => u.jellyfin_user_id !== id))} />
          ))}
        </div>
      )}
    </Card>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AdminUsers() {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ fontFamily: 'Syne', color: 'var(--text-primary)' }}>
          User Management
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          Configure default playlists for all users and manage who has access to JellyDJ.
        </p>
      </div>

      <DefaultPlaylistsSection />
      <ManagedUsersSection />
    </div>
  )
}
