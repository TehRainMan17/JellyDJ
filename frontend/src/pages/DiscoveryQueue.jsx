import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api.js'
import { useAuth } from '../contexts/AuthContext.jsx'
import {
  Telescope, RefreshCw, Check, X, Clock, Send, Loader2, Music2,
  ChevronDown, ChevronUp, Trash2, Download, Pin, PinOff, ShieldAlert,
  Sparkles, Filter, History,
} from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────
const utc = s => { if (!s) return s; const bare = s.replace(/([+-]\d{2}:\d{2}|Z)$/, ''); return bare + 'Z' }

const STATUS_TABS = [
  { key:'pending',       label:'Pending',        color:'#fbbf24' },
  { key:'approved',      label:'Approved',       color:'var(--accent)' },
  { key:'rejected',      label:'Rejected',       color:'var(--danger)' },
  { key:'snoozed',       label:'Snoozed',        color:'var(--text-secondary)' },
  { key:'auto_downloaded', label:'Auto-Downloaded', color:'#d29922' },
]

function ScoreBadge({ score }) {
  const n = Number(score)
  const color = n >= 70 ? 'var(--accent)' : n >= 45 ? '#fbbf24' : 'var(--text-muted)'
  return (
    <div className="flex items-center justify-center w-10 h-10 rounded-xl text-xs font-bold font-mono flex-shrink-0"
         style={{ background:`${color}12`, border:`1px solid ${color}28`, color }}>
      {Math.round(n)}
    </div>
  )
}

function StatusPill({ status, lidarrSent }) {
  const cfg = {
    pending:  { color:'#fbbf24',            label:'Pending'  },
    approved: { color:'var(--accent)',       label: lidarrSent ? 'Sent ✓' : 'Approved' },
    rejected: { color:'var(--danger)',       label:'Rejected' },
    snoozed:  { color:'var(--text-muted)',   label:'Snoozed'  },
  }[status] || { color:'var(--text-muted)', label: status }
  return (
    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
          style={{ background:`${cfg.color}15`, color:cfg.color, border:`1px solid ${cfg.color}30` }}>
      {cfg.label}
    </span>
  )
}

// ── Queue card ─────────────────────────────────────────────────────────────────
function QueueCard({ item, onAction, onSendToLidarr, onDelete, onPin, activeTab, isAdmin }) {
  const [expanded, setExpanded]   = useState(false)
  const [actioning, setActioning] = useState(null)
  const [sending, setSending]     = useState(false)
  const [pinning, setPinning]     = useState(null)
  const [msg, setMsg]             = useState('')

  const handlePin = async () => {
    setPinning('pin')
    try {
      await api.post(`/api/discovery/${item.id}/pin`)
      onPin(item.id, true)
    } finally { setPinning(null) }
  }
  const handleSkipAuto = async () => {
    setPinning('skip')
    try {
      await api.post(`/api/discovery/${item.id}/skip-auto`)
      onPin(item.id, false)
    } finally { setPinning(null) }
  }
  const handleAction = async (status) => {
    setActioning(status)
    try {
      await api.post(`/api/discovery/${item.id}/action`, { status })
      onAction(item.id, status)
    } finally { setActioning(null) }
  }
  const handleSend = async () => {
    setSending(true); setMsg('')
    try {
      const r = await api.post(`/api/discovery/${item.id}/send-to-lidarr`)
      const d = await r.json()
      setMsg(d.ok ? '✓ Sent' : `✗ ${d.message || 'Failed'}`)
      if (d.ok) onSendToLidarr(item.id)
    } catch { setMsg('✗ Network error') }
    finally { setSending(false); setTimeout(() => setMsg(''), 5000) }
  }
  const handleDelete = async () => {
    await api.delete(`/api/discovery/${item.id}`)
    onDelete(item.id)
  }

  const isPinned = item.auto_queued
  const isSkipped = item.auto_skip

  return (
    <div className={`card transition-all duration-200 anim-fade-up ${isPinned ? 'border-[rgba(251,191,36,0.35)]' : ''}`}
         style={isPinned ? { borderColor:'rgba(251,191,36,0.35)', background:'rgba(251,191,36,0.03)' } : {}}>

      <div className="flex items-start gap-3">
        {/* Album art / score */}
        <div className="flex-shrink-0">
          {item.image_url
            ? <img src={item.image_url} alt="" className="w-12 h-12 rounded-lg object-cover"
                   style={{ border:'1px solid var(--border)' }} />
            : <div className="w-12 h-12 rounded-lg flex items-center justify-center"
                   style={{ background:'var(--bg-elevated)', border:'1px solid var(--border)' }}>
                <Music2 size={18} style={{ color:'var(--text-muted)' }} />
              </div>
          }
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start gap-2 flex-wrap">
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-sm truncate" style={{ color:'var(--text-primary)' }}>
                {item.artist_name}
              </div>
              <div className="text-xs truncate mt-0.5" style={{ color:'var(--text-secondary)' }}>
                {item.album_name || <span style={{ color:'var(--text-muted)', fontStyle:'italic' }}>album unknown</span>}
                {item.release_year && <span style={{ color:'var(--text-muted)' }}> · {item.release_year}</span>}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <ScoreBadge score={item.popularity_score} />
              <StatusPill status={item.status} lidarrSent={item.lidarr_sent} />
            </div>
          </div>

          {/* Badges */}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {item.username && (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                    style={{ background:'rgba(96,165,250,0.1)', color:'#60a5fa', border:'1px solid rgba(96,165,250,0.2)' }}>
                {item.username}
              </span>
            )}
            {isPinned && (
              <span className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full font-semibold"
                    style={{ background:'rgba(251,191,36,0.12)', color:'#fbbf24', border:'1px solid rgba(251,191,36,0.25)' }}>
                <Pin size={8} /> Getting this next
              </span>
            )}
            {isSkipped && (
              <span className="text-[10px] px-2 py-0.5 rounded-full"
                    style={{ background:'rgba(255,255,255,0.05)', color:'var(--text-muted)', border:'1px solid var(--border)' }}>
                Auto-skipped
              </span>
            )}
          </div>

          {/* Expand toggle */}
          <button onClick={() => setExpanded(v => !v)}
                  className="flex items-center gap-1 text-[10px] mt-2 transition-colors hover:opacity-80"
                  style={{ color:'var(--text-muted)' }}>
            {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            {expanded ? 'Less' : 'Why this?'}
          </button>

          {expanded && (
            <div className="mt-2 text-xs rounded-lg px-3 py-2 anim-scale-in"
                 style={{ background:'var(--bg-elevated)', color:'var(--text-secondary)', border:'1px solid var(--border)' }}>
              {item.why || 'No reason provided.'}
              {item.source_artist && item.source_artist !== item.artist_name && (
                <div className="mt-1" style={{ color:'var(--text-muted)' }}>
                  Based on: <span style={{ color:'var(--accent)' }}>{item.source_artist}</span>
                </div>
              )}
              <div className="mt-1" style={{ color:'var(--text-muted)' }}>
                Added {new Date(utc(item.added_at)).toLocaleDateString()}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Action row */}
      {activeTab === 'pending' && (
        <div className="flex flex-wrap gap-2 mt-3 pt-3" style={{ borderTop:'1px solid var(--border)' }}>
          {/* Admin-only queue actions */}
          {isAdmin && (
            <>
              <button onClick={() => handleAction('approved')} disabled={!!actioning}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(0,212,170,0.12)', border:'1px solid rgba(0,212,170,0.25)', color:'var(--accent)' }}>
                {actioning==='approved' ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                Approve
              </button>
              <button onClick={() => handleAction('rejected')} disabled={!!actioning}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(248,113,113,0.1)', border:'1px solid rgba(248,113,113,0.2)', color:'var(--danger)' }}>
                {actioning==='rejected' ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />}
                Reject
              </button>
              <button onClick={() => handleAction('snoozed')} disabled={!!actioning}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(255,255,255,0.05)', border:'1px solid var(--border)', color:'var(--text-secondary)' }}>
                {actioning==='snoozed' ? <Loader2 size={11} className="animate-spin" /> : <Clock size={11} />}
                Snooze
              </button>
            </>
          )}

          {/* Auto-download pin — available to all users for their own items */}
          <div className={isAdmin ? 'ml-auto flex gap-1.5 flex-wrap' : 'flex gap-1.5 flex-wrap'}>
            {!isPinned && !isSkipped && (
              <button onClick={handlePin} disabled={pinning==='pin'}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.2)', color:'#fbbf24' }}>
                {pinning==='pin' ? <Loader2 size={10} className="animate-spin" /> : <Pin size={10} />}
                Get next
              </button>
            )}
            {isPinned && (
              <button onClick={handleSkipAuto} disabled={pinning==='skip'}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(255,255,255,0.05)', border:'1px solid var(--border)', color:'var(--text-secondary)' }}>
                {pinning==='skip' ? <Loader2 size={10} className="animate-spin" /> : <PinOff size={10} />}
                Unpin
              </button>
            )}
            {/* Admin-only: skip-auto / not-that-one */}
            {isAdmin && isSkipped && (
              <button onClick={handlePin} disabled={pinning==='pin'}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(0,212,170,0.08)', border:'1px solid rgba(0,212,170,0.2)', color:'var(--accent)' }}>
                {pinning==='pin' ? <Loader2 size={10} className="animate-spin" /> : <Pin size={10} />}
                Re-include
              </button>
            )}
            {isAdmin && !isPinned && !isSkipped && (
              <button onClick={handleSkipAuto} disabled={pinning==='skip'}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all disabled:opacity-40"
                      style={{ background:'rgba(255,255,255,0.04)', border:'1px solid var(--border)', color:'var(--text-muted)' }}>
                {pinning==='skip' ? <Loader2 size={10} className="animate-spin" /> : <ShieldAlert size={10} />}
                Not that one
              </button>
            )}
          </div>
        </div>
      )}

      {/* Approved tab actions */}
      {activeTab === 'approved' && (
        <div className="flex flex-wrap gap-2 mt-3 pt-3" style={{ borderTop:'1px solid var(--border)' }}>
          {!item.lidarr_sent && (
            <button onClick={handleSend} disabled={sending}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
                    style={{ background:'rgba(0,212,170,0.12)', border:'1px solid rgba(0,212,170,0.25)', color:'var(--accent)' }}>
              {sending ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
              Send to Lidarr
            </button>
          )}
          {msg && <span className={`text-xs self-center ${msg.startsWith('✓') ? 'text-[var(--accent)]' : 'text-[var(--danger)]'}`}>{msg}</span>}
          <button onClick={handleDelete} className="ml-auto btn-ghost text-[11px]" style={{ color:'var(--text-muted)' }}>
            <Trash2 size={11} /> Remove
          </button>
        </div>
      )}

      {/* Other tabs: just delete */}
      {(activeTab === 'rejected' || activeTab === 'snoozed') && (
        <div className="flex mt-3 pt-3" style={{ borderTop:'1px solid var(--border)' }}>
          {activeTab === 'snoozed' && (
            <button onClick={() => handleAction('pending')} disabled={!!actioning}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all disabled:opacity-40"
                    style={{ background:'rgba(0,212,170,0.08)', border:'1px solid rgba(0,212,170,0.2)', color:'var(--accent)' }}>
              {actioning ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Restore
            </button>
          )}
          <button onClick={handleDelete} className="ml-auto btn-ghost text-[11px]" style={{ color:'var(--text-muted)' }}>
            <Trash2 size={11} /> Remove
          </button>
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function DiscoveryQueue() {
  const { isAdmin, user } = useAuth()
  const [activeTab, setActiveTab]     = useState('pending')
  const [items, setItems]             = useState([])
  const [counts, setCounts]           = useState({})
  const [loading, setLoading]         = useState(true)
  const [populating, setPopulating]   = useState(false)
  const [popMsg, setPopMsg]           = useState('')
  const [dlHistory, setDlHistory]     = useState([])
  const [dlLoading, setDlLoading]     = useState(false)

  const fetchDlHistory = useCallback(() => {
    setDlLoading(true)
    api.get('/api/automation/auto-download/history?limit=200')
      .then(setDlHistory).catch(() => setDlHistory([]))
      .finally(() => setDlLoading(false))
  }, [])

  const fetchItems = useCallback((tab) => {
    if (tab === 'auto_downloaded') return
    setLoading(true)
    // Non-admins always scope to their own user_id
    const userParam = !isAdmin && user?.user_id ? `&user_id=${user.user_id}` : ''
    api.get(`/api/discovery?status=${tab}${userParam}`)
      .then(setItems).catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [isAdmin, user])

  const fetchCounts = useCallback(() => {
    // Non-admins get counts scoped to their own queue
    const userParam = !isAdmin && user?.user_id ? `?user_id=${user.user_id}` : ''
    api.get(`/api/discovery/counts${userParam}`).then(setCounts).catch(() => {})
  }, [isAdmin, user])

  useEffect(() => {
    if (activeTab === 'auto_downloaded') fetchDlHistory()
    else fetchItems(activeTab)
  }, [activeTab, fetchItems, fetchDlHistory])
  useEffect(() => { fetchCounts() }, [fetchCounts])

  const handleAction   = (id) => setItems(prev => prev.filter(i => i.id !== id))
  const handleSendToLidarr = (id) => setItems(prev => prev.map(i => i.id === id ? { ...i, lidarr_sent:true } : i))
  const handleDelete   = (id) => { setItems(prev => prev.filter(i => i.id !== id)); fetchCounts() }
  const handlePin      = (id, pinned) => setItems(prev => prev.map(i => {
    if (i.id === id) return { ...i, auto_queued: pinned, auto_skip: false }
    if (pinned && i.auto_queued) return { ...i, auto_queued: false }
    return i
  }))

  const [popResult, setPopResult] = useState(null)  // { ok, text }

  const handlePopulate = async () => {
    setPopulating(true); setPopMsg('Generating recommendations…'); setPopResult(null)
    try {
      const r = await api.post('/api/discovery/populate')
      const d = await r.json()
      setPopMsg('')
      setPopResult(d.ok
        ? { ok:true,  text:`Added ${d.added} new recommendation${d.added!==1?'s':''}` }
        : { ok:false, text: d.detail || 'Populate failed' })
      fetchItems(activeTab); fetchCounts()
    } catch { setPopMsg(''); setPopResult({ ok:false, text:'Network error' }) }
    finally { setPopulating(false); setTimeout(() => setPopResult(null), 10000) }
  }

  const tabCounts = STATUS_TABS.map(t => ({
    ...t,
    count: t.key === 'auto_downloaded' ? dlHistory.length : (counts[t.key] || 0),
  }))

  return (
    <div className="space-y-5 max-w-3xl">

      {/* Header */}
      <div className="flex items-start gap-4 flex-wrap anim-fade-up">
        <div className="flex-1 min-w-0">
          <h1 style={{ fontFamily:'Syne', fontWeight:800, fontSize:26, letterSpacing:'-0.02em', color:'var(--text-primary)', lineHeight:1.1 }}>
            Discovery Queue
          </h1>
          <p className="text-sm mt-1" style={{ color:'var(--text-secondary)' }}>
            {isAdmin ? 'Album recommendations ranked by affinity and novelty' : 'Your personal album recommendations'}
          </p>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2 flex-wrap flex-shrink-0">
            <button onClick={handlePopulate} disabled={populating} className="btn-primary">
              {populating ? <><Loader2 size={14} className="animate-spin" />Generating…</> : <><Sparkles size={14} />Refresh Recs</>}
            </button>
          </div>
        )}
      </div>

      {/* Progress / result */}
      {populating && (
        <div className="rounded-xl px-4 py-3 space-y-2 anim-scale-in"
             style={{ background:'rgba(0,212,170,0.05)', border:'1px solid rgba(0,212,170,0.15)' }}>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full" style={{ background:'var(--accent)', animation:'pulse 1s ease-in-out infinite' }} />
            <span className="text-xs font-medium" style={{ color:'var(--accent)' }}>Generating recommendations</span>
          </div>
          <div className="h-1 w-full rounded-full overflow-hidden" style={{ background:'var(--bg-overlay)' }}>
            <div className="h-full rounded-full" style={{ width:'100%', background:'var(--accent)', opacity:0.6 }} />
          </div>
          <div className="text-[10px]" style={{ color:'var(--text-muted)' }}>Analysing your taste profile and finding new music…</div>
        </div>
      )}
      {popResult && !populating && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-xl anim-scale-in"
             style={{ background: popResult.ok ? 'rgba(0,212,170,0.06)' : 'rgba(248,113,113,0.06)',
                      border: `1px solid ${popResult.ok ? 'rgba(0,212,170,0.2)' : 'rgba(248,113,113,0.2)'}` }}>
          {popResult.ok
            ? <Check size={13} style={{ color:'var(--accent)', flexShrink:0 }} />
            : <X size={13} style={{ color:'var(--danger)', flexShrink:0 }} />}
          <span className="text-xs font-medium" style={{ color: popResult.ok ? 'var(--accent)' : 'var(--danger)' }}>
            {popResult.text}
          </span>
        </div>
      )}

      {/* Tabs */}
      <div className="tab-bar anim-fade-up" style={{ animationDelay:'50ms' }}>
        {tabCounts.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
                  className={`tab ${activeTab === t.key ? 'active' : ''}`}>
            {t.label}
            {t.count > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold font-mono"
                    style={{
                      background: activeTab === t.key ? `${t.color}20` : 'rgba(255,255,255,0.06)',
                      color: activeTab === t.key ? t.color : 'var(--text-muted)',
                    }}>
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* List */}
      {activeTab === 'auto_downloaded' ? (
        dlLoading ? (
          <div className="space-y-3">
            {[1,2,3].map(i => <div key={i} className="skeleton h-14 rounded-xl" />)}
          </div>
        ) : dlHistory.length === 0 ? (
          <div className="card flex flex-col items-center justify-center py-20 gap-3 text-center anim-scale-in">
            <History size={32} strokeWidth={1.25} style={{ color:'var(--text-muted)' }} />
            <div className="text-sm font-medium" style={{ color:'var(--text-secondary)' }}>No auto-download history yet</div>
            <div className="text-xs max-w-xs" style={{ color:'var(--text-muted)' }}>
              Albums sent to Lidarr by the auto-downloader will appear here
            </div>
          </div>
        ) : (
          <div className="space-y-2 stagger">
            {dlHistory.map(entry => {
              const msg = entry.message || ''
              const artist = msg.replace(/^Auto-downloaded:\s*/, '').split(' — ')[0] || '—'
              const album  = msg.includes(' — ') ? msg.split(' — ').slice(1).join(' — ') : '—'
              const ts     = entry.created_at
                ? new Date(entry.created_at.replace(/([+-]\d{2}:\d{2}|Z)$/, '') + 'Z').toLocaleString()
                : ''
              return (
                <div key={entry.id} className="card flex items-center gap-3 py-3 px-4">
                  <Download size={14} style={{ color:'#d29922', flexShrink:0 }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate" style={{ color:'var(--text-primary)' }}>
                      {artist}
                    </div>
                    <div className="text-xs truncate" style={{ color:'var(--text-secondary)' }}>{album}</div>
                  </div>
                  <div className="text-[11px] flex-shrink-0" style={{ color:'var(--text-muted)' }}>{ts}</div>
                </div>
              )
            })}
          </div>
        )
      ) : loading ? (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="skeleton h-28 rounded-xl" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-20 gap-3 text-center anim-scale-in">
          <Telescope size={32} strokeWidth={1.25} style={{ color:'var(--text-muted)' }} />
          <div className="text-sm font-medium" style={{ color:'var(--text-secondary)' }}>
            {activeTab === 'pending' ? 'Queue is empty' : `No ${activeTab} items`}
          </div>
          {activeTab === 'pending' && (
            <div className="text-xs max-w-xs" style={{ color:'var(--text-muted)' }}>
              Click <span style={{ color:'var(--accent)', fontWeight:600 }}>Refresh Recs</span> to generate recommendations from your play history
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3 stagger">
          {items.map(item => (
            <QueueCard key={item.id} item={item} activeTab={activeTab} isAdmin={isAdmin}
                       onAction={handleAction} onSendToLidarr={handleSendToLidarr}
                       onDelete={handleDelete} onPin={handlePin} />
          ))}
        </div>
      )}
    </div>
  )
}
