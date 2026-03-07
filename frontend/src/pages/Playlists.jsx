import { useState, useEffect, useCallback } from 'react'
import { ListMusic, RefreshCw, CheckCircle2, XCircle, Loader2, Clock, ChevronDown, ChevronUp, Sparkles, TrendingUp, History, Radio } from 'lucide-react'
import { useJobStatus } from '../hooks/useJobStatus.js'
import JobProgress from '../components/JobProgress.jsx'

const TYPE_CFG = {
  for_you:         { label:'For You',         icon:Sparkles,   color:'var(--accent)', desc:'Affinity-weighted picks from your history' },
  discover:        { label:'New For You',     icon:Radio,      color:'#fbbf24',        desc:"Novel picks — artists and tracks you haven't heard yet, ranked by how well they fit your taste" },
  most_played:     { label:'Most Played',     icon:TrendingUp, color:'#f78166',        desc:'Your all-time top tracks' },
  recently_played: { label:'Recently Played', icon:History,    color:'#8899b5',        desc:"What you've been listening to lately" },
}

const utc = s => { if (!s) return s; const bare = s.replace(/([+-]\d{2}:\d{2}|Z)$/, ''); return bare + 'Z' }

function UserCard({ username, playlists }) {
  const [open, setOpen] = useState(true)
  const initial = username?.[0]?.toUpperCase() || '?'
  const hue = username.split('').reduce((a,c)=>a+c.charCodeAt(0),0) % 360
  const col = `hsl(${hue},55%,55%)`
  return (
    <div className="card space-y-3 anim-fade-up">
      <button onClick={() => setOpen(v=>!v)} className="w-full flex items-center gap-3">
        <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
             style={{ background:`${col}18`, border:`1.5px solid ${col}35`, color:col }}>{initial}</div>
        <div className="flex-1 text-left">
          <div className="text-sm font-semibold" style={{ color:'var(--text-primary)' }}>{username}</div>
          <div className="text-xs" style={{ color:'var(--text-muted)' }}>{playlists.length} playlist{playlists.length!==1?'s':''}</div>
        </div>
        {open ? <ChevronUp size={14} style={{ color:'var(--text-muted)' }} /> : <ChevronDown size={14} style={{ color:'var(--text-muted)' }} />}
      </button>
      {open && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1 stagger">
          {playlists.map(p => {
            const cfg = TYPE_CFG[p.playlist_type] || { label:p.label, color:'var(--text-muted)', icon:ListMusic }
            const Icon = cfg.icon
            return (
              <div key={p.playlist_type}
                   className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors anim-fade-up"
                   style={{ background:'var(--bg-elevated)', border:'1px solid var(--border)' }}>
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                     style={{ background:`${cfg.color}12`, border:`1px solid ${cfg.color}22` }}>
                  <Icon size={13} style={{ color:cfg.color }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate" style={{ color:'var(--text-primary)' }}>{p.playlist_name}</div>
                  <div className="text-[10px] mt-0.5" style={{ color:'var(--text-muted)' }}>{p.tracks_added} tracks · {p.action}</div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function RunRow({ run, onExpand, expanded }) {
  const dur = run.duration_secs != null
    ? run.duration_secs < 60 ? `${run.duration_secs}s` : `${Math.round(run.duration_secs/60)}m` : '—'
  const sc = run.status === 'ok' ? 'var(--accent)' : run.status === 'running' ? '#fbbf24' : 'var(--danger)'
  return (
    <div className="rounded-xl overflow-hidden anim-fade-up" style={{ border:'1px solid var(--border)' }}>
      <button onClick={() => onExpand(run.id)}
              className="w-full flex items-center gap-3 px-4 py-3 transition-colors text-left"
              style={{ background:'var(--bg-surface)' }}
              onMouseEnter={e=>e.currentTarget.style.background='var(--bg-elevated)'}
              onMouseLeave={e=>e.currentTarget.style.background='var(--bg-surface)'}>
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
          {run.items.map((item,i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              {item.status==='ok' ? <CheckCircle2 size={11} style={{ color:'var(--accent)', flexShrink:0 }} /> : <XCircle size={11} style={{ color:'var(--danger)', flexShrink:0 }} />}
              <span className="flex-1 truncate" style={{ color:'var(--text-secondary)' }}>{item.playlist_name}</span>
              {item.status==='ok' ? <span className="font-mono" style={{ color:'var(--text-muted)' }}>{item.tracks_added}t</span>
                : <span style={{ color:'var(--danger)' }}>{item.status}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Playlists() {
  const [current, setCurrent]       = useState(null)
  const [runs, setRuns]             = useState([])
  const [users, setUsers]           = useState([])
  const [generating, setGenerating] = useState(false)
  const [genMsg, setGenMsg]         = useState(null)
  const [expandedRun, setExpandedRun] = useState(null)
  const [runDetails, setRunDetails] = useState({})
  const [selected, setSelected]     = useState(Object.keys(TYPE_CFG))
  const [tab, setTab]               = useState('playlists')

  const fetchAll = useCallback(() => {
    fetch('/api/playlists/current').then(r=>r.json()).then(setCurrent).catch(()=>{})
    fetch('/api/playlists/runs').then(r=>r.json()).then(setRuns).catch(()=>{})
    fetch('/api/playlists/users').then(r=>r.json()).then(setUsers).catch(()=>{})
  }, [])
  useEffect(() => { fetchAll() }, [fetchAll])

  const [genPhase, setGenPhase] = useState('')

  const handleGenerate = async () => {
    setGenerating(true); setGenMsg(null); setGenPhase('Writing playlists to Jellyfin\u2026')
    try {
      const r = await fetch('/api/playlists/generate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ playlist_types: selected }),
      })
      const d = await r.json()
      setGenPhase('')
      if (r.ok && d.ok) {
        setGenMsg({ text:`${d.playlists_written} playlist${d.playlists_written!==1?'s':''} written to Jellyfin`, ok:true })
        fetchAll(); setTab('playlists')
      } else setGenMsg({ text: d.error || d.detail || 'Generation failed', ok:false })
    } catch { setGenPhase(''); setGenMsg({ text:'Network error', ok:false }) }
    finally { setGenerating(false); setTimeout(() => setGenMsg(null), 10000) }
  }

  const handleExpand = async (runId) => {
    if (expandedRun === runId) { setExpandedRun(null); return }
    setExpandedRun(runId)
    if (!runDetails[runId]) {
      const r = await fetch(`/api/playlists/runs/${runId}`)
      const d = await r.json()
      setRunDetails(prev => ({ ...prev, [runId]: d }))
    }
  }

  const byUser = {}
  for (const p of current?.playlists || []) {
    if (!byUser[p.username]) byUser[p.username] = []
    byUser[p.username].push(p)
  }
  const ready    = users.filter(u => u.ready)
  const notReady = users.filter(u => !u.ready)

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-start justify-between gap-4 flex-wrap anim-fade-up">
        <div>
          <h1 style={{ fontFamily:'Syne', fontWeight:800, fontSize:26, letterSpacing:'-0.02em', color:'var(--text-primary)' }}>Playlists</h1>
          <p className="text-sm mt-1" style={{ color:'var(--text-secondary)' }}>Auto-generated playlists written to Jellyfin for each user</p>
        </div>
        {current?.last_run && (
          <div className="text-right flex-shrink-0">
            <div className="section-label">Last generated</div>
            <div className="text-xs mt-1" style={{ color:'var(--text-primary)' }}>
              {new Date(utc(current.last_run.finished_at)).toLocaleString()}
            </div>
          </div>
        )}
      </div>

      {/* Generate card */}
      <div className="card space-y-4 anim-fade-up" style={{ animationDelay:'50ms' }}>
        <div className="flex items-center gap-2">
          <ListMusic size={15} style={{ color:'var(--accent)' }} />
          <span className="text-sm font-semibold" style={{ color:'var(--text-primary)' }}>Generate Playlists</span>
        </div>

        <div>
          <div className="section-label mb-2">Playlist types</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(TYPE_CFG).map(([type, cfg]) => {
              const Icon = cfg.icon
              const sel = selected.includes(type)
              return (
                <button key={type} onClick={() => setSelected(prev => sel ? prev.filter(x=>x!==type) : [...prev,type])}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all"
                        style={{ background: sel ? `${cfg.color}15` : 'rgba(255,255,255,0.04)', borderColor: sel ? `${cfg.color}40` : 'var(--border)', border:'1px solid', color: sel ? cfg.color : 'var(--text-muted)' }}>
                  <Icon size={11} />{cfg.label}
                </button>
              )
            })}
          </div>
        </div>

        {users.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {ready.map(u => (
              <span key={u.jellyfin_user_id} className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full"
                    style={{ background:'rgba(0,212,170,0.08)', border:'1px solid rgba(0,212,170,0.2)', color:'var(--accent)' }}>
                <CheckCircle2 size={10} />{u.username} · {u.tracks_indexed.toLocaleString()} tracks
              </span>
            ))}
            {notReady.map(u => (
              <span key={u.jellyfin_user_id} className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full"
                    style={{ background:'rgba(248,113,113,0.08)', border:'1px solid rgba(248,113,113,0.18)', color:'var(--danger)' }}>
                <XCircle size={10} />{u.username} · not indexed
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={handleGenerate} disabled={generating || selected.length===0 || ready.length===0} className="btn-primary">
            {generating ? <><Loader2 size={14} className="animate-spin" />Generating…</> : <><RefreshCw size={14} />Generate Now</>}
          </button>
        </div>

        {generating && (
          <div className="rounded-xl px-4 py-3 space-y-2 anim-scale-in"
               style={{ background:'rgba(0,212,170,0.05)', border:'1px solid rgba(0,212,170,0.15)' }}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full" style={{ background:'var(--accent)', animation:'pulse 1s ease-in-out infinite' }} />
                <span className="text-xs font-medium" style={{ color:'var(--accent)' }}>Generating playlists</span>
              </div>
            </div>
            <div className="h-1 w-full rounded-full overflow-hidden" style={{ background:'var(--bg-overlay)' }}>
              <div className="h-full rounded-full anim-progress-bar" style={{ width:'100%', background:'var(--accent)', opacity:0.7 }} />
            </div>
            <div className="text-[10px]" style={{ color:'var(--text-muted)' }}>Writing to Jellyfin… this may take 15–30 seconds</div>
          </div>
        )}
        {genMsg && !generating && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl anim-scale-in"
               style={{ background: genMsg.ok ? 'rgba(0,212,170,0.06)' : 'rgba(248,113,113,0.06)',
                        border: `1px solid ${genMsg.ok ? 'rgba(0,212,170,0.2)' : 'rgba(248,113,113,0.2)'}` }}>
            {genMsg.ok ? <CheckCircle2 size={13} style={{ color:'var(--accent)', flexShrink:0 }} />
                       : <XCircle size={13} style={{ color:'var(--danger)', flexShrink:0 }} />}
            <span className="text-xs font-medium" style={{ color: genMsg.ok ? 'var(--accent)' : 'var(--danger)' }}>
              {genMsg.text}
            </span>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="tab-bar anim-fade-up" style={{ animationDelay:'100ms' }}>
        {[{ key:'playlists', label:'Current Playlists', icon:ListMusic }, { key:'history', label:'Run History', icon:Clock }].map(t => {
          const Icon = t.icon
          return (
            <button key={t.key} onClick={() => setTab(t.key)} className={`tab ${tab===t.key?'active':''}`}>
              <Icon size={12} />{t.label}
            </button>
          )
        })}
      </div>

      {tab === 'playlists' && (
        <div className="space-y-3">
          {Object.keys(byUser).length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-16 gap-3 text-center anim-scale-in">
              <ListMusic size={28} strokeWidth={1.25} style={{ color:'var(--text-muted)' }} />
              <div className="text-sm" style={{ color:'var(--text-secondary)' }}>No playlists generated yet</div>
              <div className="text-xs max-w-xs" style={{ color:'var(--text-muted)' }}>Click Generate Now to create playlists in Jellyfin for all indexed users.</div>
            </div>
          ) : Object.entries(byUser).map(([username, pls]) => (
            <UserCard key={username} username={username} playlists={pls} />
          ))}
        </div>
      )}

      {tab === 'history' && (
        <div className="space-y-2">
          {runs.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-12 gap-2 anim-scale-in">
              <Clock size={24} strokeWidth={1.25} style={{ color:'var(--text-muted)' }} />
              <div className="text-sm" style={{ color:'var(--text-secondary)' }}>No run history yet</div>
            </div>
          ) : runs.map(run => (
            <RunRow key={run.id} run={{ ...run, items:runDetails[run.id]?.items }}
                    onExpand={handleExpand} expanded={expandedRun===run.id} />
          ))}
        </div>
      )}
    </div>
  )
}
