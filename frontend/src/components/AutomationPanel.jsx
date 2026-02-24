import { useState, useEffect } from 'react'
import { Clock, RefreshCw, Loader2, Save, Play, Music2, Telescope, Zap, Download, ShieldAlert, ToggleLeft, ToggleRight } from 'lucide-react'

// Normalise any ISO datetime string to UTC for reliable cross-browser parsing.
// Python's datetime.utcnow().isoformat() produces "2026-02-24T14:30:00" with no
// timezone suffix — new Date() treats that as local time in most browsers, which
// gives wrong results or "Invalid Date" on Safari. We strip any existing offset
// and always append Z so the string is unambiguously UTC.
const utc = s => {
  if (!s) return s
  // Remove any existing timezone offset (+HH:MM, -HH:MM, or Z)
  const bare = s.replace(/([+-]\d{2}:\d{2}|Z)$/, '')
  return bare + 'Z'
}

function Toggle({ enabled, onChange }) {
  return (
    <button onClick={() => onChange(!enabled)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold transition-all"
            style={{
              background: enabled ? 'rgba(0,212,170,0.12)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${enabled ? 'rgba(0,212,170,0.3)' : 'var(--border)'}`,
              color: enabled ? 'var(--accent)' : 'var(--text-muted)',
            }}>
      {enabled ? <ToggleRight size={13} /> : <ToggleLeft size={13} />}
      {enabled ? 'On' : 'Off'}
    </button>
  )
}

function Slider({ label, value, onChange, min=1, max=168, unit='h', markers }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs" style={{ color:'var(--text-secondary)' }}>{label}</span>
        <span className="text-xs font-mono font-semibold" style={{ color:'var(--text-primary)' }}>
          {value}{unit}
        </span>
      </div>
      <input type="range" min={min} max={max} step={1} value={value} onChange={e => onChange(Number(e.target.value))} />
      {markers && (
        <div className="flex justify-between text-[10px] mt-1" style={{ color:'var(--text-muted)' }}>
          {markers.map(m => <span key={m}>{m}</span>)}
        </div>
      )}
    </div>
  )
}

function TaskCard({ icon: Icon, color, title, description, lastRun, nextRun, enabled, onToggle, triggerLabel, onTrigger, triggering, children }) {
  return (
    <div className="rounded-2xl p-5 space-y-4 transition-all anim-fade-up"
         style={{ background:'var(--bg-elevated)', border:'1px solid var(--border)' }}>
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
               style={{ background:`${color}15`, border:`1px solid ${color}28` }}>
            <Icon size={15} style={{ color }} />
          </div>
          <div>
            <div className="text-sm font-semibold" style={{ color:'var(--text-primary)' }}>{title}</div>
            <div className="text-[11px]" style={{ color:'var(--text-muted)' }}>{description}</div>
          </div>
        </div>
        {onToggle && <Toggle enabled={enabled} onChange={onToggle} />}
      </div>

      {children}

      {/* Footer row */}
      <div className="flex items-center justify-between pt-1 gap-3">
        <div className="space-y-0.5">
          {lastRun && (
            <div className="text-[10px]" style={{ color:'var(--text-muted)' }}>
              Last run: {new Date(utc(lastRun)).toLocaleString()}
            </div>
          )}
          {nextRun && !nextRun.paused && (
            <div className="text-[10px]" style={{ color:'var(--text-muted)' }}>
              Next: {new Date(utc(nextRun.next_run)).toLocaleString()}
            </div>
          )}
        </div>
        <button onClick={onTrigger} disabled={triggering}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all disabled:opacity-40"
                style={{ background:`${color}10`, border:`1px solid ${color}22`, color }}>
          {triggering ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
          {triggerLabel}
        </button>
      </div>
    </div>
  )
}

export default function AutomationPanel() {
  const [settings, setSettings]         = useState(null)
  const [jobStatus, setJobStatus]       = useState({})
  const [saving, setSaving]             = useState(false)
  const [saveMsg, setSaveMsg]           = useState('')

  // Index
  const [indexInterval, setIndexInterval] = useState(6)
  // Discovery
  const [discEnabled,  setDiscEnabled]    = useState(true)
  const [discInterval, setDiscInterval]   = useState(24)
  const [discItems,    setDiscItems]      = useState(10)
  // Playlists
  const [plEnabled,  setPlEnabled]        = useState(true)
  const [plInterval, setPlInterval]       = useState(24)
  // Auto-download
  const [autoEnabled,   setAutoEnabled]   = useState(false)
  const [autoMax,       setAutoMax]       = useState(1)
  const [autoCooldown,  setAutoCooldown]  = useState(7)

  // Trigger states
  const [trigIndex,  setTrigIndex]  = useState(false)
  const [trigDisc,   setTrigDisc]   = useState(false)
  const [trigPl,     setTrigPl]     = useState(false)
  const [trigAuto,   setTrigAuto]   = useState(false)

  useEffect(() => {
    fetch('/api/automation/settings').then(r=>r.json()).then(d => {
      setSettings(d)
      setIndexInterval(d.index_interval_hours ?? 6)
      setDiscEnabled(!!d.discovery_refresh_enabled)
      setDiscInterval(d.discovery_refresh_interval_hours ?? 24)
      setDiscItems(d.discovery_items_per_run ?? 10)
      setPlEnabled(!!d.playlist_regen_enabled)
      setPlInterval(d.playlist_regen_interval_hours ?? 24)
      setAutoEnabled(!!d.auto_download_enabled)
      setAutoMax(d.auto_download_max_per_run ?? 1)
      setAutoCooldown(d.auto_download_cooldown_days ?? 7)
    }).catch(() => {})
    fetch('/api/indexer/scheduler').then(r=>r.json()).then(setJobStatus).catch(() => {})
  }, [])

  const save = async () => {
    setSaving(true); setSaveMsg('')
    try {
      const r = await fetch('/api/automation/settings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          index_interval_hours: indexInterval,
          discovery_refresh_enabled: discEnabled,
          discovery_refresh_interval_hours: discInterval,
          discovery_items_per_run: discItems,
          playlist_regen_enabled: plEnabled,
          playlist_regen_interval_hours: plInterval,
          auto_download_enabled: autoEnabled,
          auto_download_max_per_run: autoMax,
          auto_download_cooldown_days: autoCooldown,
        }),
      })
      setSaveMsg(r.ok ? '✓ Saved' : '✗ Save failed')
    } catch { setSaveMsg('✗ Network error') }
    finally {
      setSaving(false)
      setTimeout(() => setSaveMsg(''), 4000)
      // Re-fetch scheduler state so next-run times reflect the new intervals
      fetch('/api/indexer/scheduler').then(r=>r.json()).then(setJobStatus).catch(() => {})
    }
  }

  const trigger = async (path, setLoading) => {
    setLoading(true)
    try { await fetch(path, { method:'POST' }) }
    finally { setLoading(false); setTimeout(() => fetch('/api/automation/settings').then(r=>r.json()).then(setSettings).catch(()=>{}), 2000) }
  }

  const s = settings

  return (
    <div className="space-y-4">

      {/* Index */}
      <TaskCard icon={RefreshCw} color="#60a5fa" title="Library Index"
                description="Scans Jellyfin, imports play history, rebuilds scores"
                lastRun={s?.last_index} nextRun={jobStatus.play_history_index}
                triggerLabel="Index Now" triggering={trigIndex}
                onTrigger={() => trigger('/api/indexer/full-scan', setTrigIndex)}>
        <Slider label="Run every" value={indexInterval} onChange={setIndexInterval}
                min={1} max={168} unit="h" markers={['1h','24h','1w']} />
      </TaskCard>

      {/* Discovery */}
      <TaskCard icon={Telescope} color="var(--accent)" title="Discovery Refresh"
                description="Generates new album recommendations per user"
                lastRun={s?.last_discovery_refresh} nextRun={jobStatus.discovery_refresh}
                enabled={discEnabled} onToggle={setDiscEnabled}
                triggerLabel="Refresh Now" triggering={trigDisc}
                onTrigger={() => trigger('/api/discovery/populate', setTrigDisc)}>
        <div className="space-y-3">
          <Slider label="Run every" value={discInterval} onChange={setDiscInterval}
                  min={1} max={168} unit="h" markers={['1h','24h','1w']} />
          <Slider label="Max new items per user per run" value={discItems} onChange={setDiscItems}
                  min={1} max={30} unit="" markers={['1','10','30']} />
        </div>
      </TaskCard>

      {/* Playlists */}
      <TaskCard icon={Music2} color="#f78166" title="Playlist Generation"
                description="Writes updated playlists to Jellyfin for each user"
                lastRun={s?.last_playlist_regen} nextRun={jobStatus.playlist_regen}
                enabled={plEnabled} onToggle={setPlEnabled}
                triggerLabel="Regenerate" triggering={trigPl}
                onTrigger={() => trigger('/api/playlists/generate', setTrigPl)}>
        <Slider label="Run every" value={plInterval} onChange={setPlInterval}
                min={1} max={168} unit="h" markers={['1h','24h','1w']} />
      </TaskCard>

      {/* Auto-download */}
      <TaskCard icon={Download} color="#d29922" title="Auto-Download"
                description="Automatically sends top-scored discoveries to Lidarr"
                lastRun={s?.last_auto_download}
                nextRun={autoEnabled ? jobStatus.auto_download : null}
                enabled={autoEnabled} onToggle={setAutoEnabled}
                triggerLabel="Run Now" triggering={trigAuto}
                onTrigger={() => trigger('/api/automation/trigger/auto-download', setTrigAuto)}>
        <div className="space-y-3">
          {!autoEnabled && (
            <div className="text-xs px-3 py-2 rounded-xl" style={{ background:'rgba(255,255,255,0.04)', color:'var(--text-muted)', border:'1px solid var(--border)' }}>
              Enable to automatically download top-scored pending items to Lidarr on a schedule.
            </div>
          )}
          {autoEnabled && (
            <>
              <Slider label="Max albums per run (per user)" value={autoMax} onChange={setAutoMax}
                      min={1} max={5} unit="" markers={['1 cautious','3','5 aggressive']} />
              <Slider label="Cooldown between runs" value={autoCooldown} onChange={setAutoCooldown}
                      min={1} max={30} unit=" days" markers={['1 day','7 days','30 days']} />
              {/* Next scheduled run — computed from last run + cooldown */}
              {(() => {
                const lastRan = s?.last_auto_download
                const nextJob = jobStatus.auto_download
                // Prefer the live scheduler next_run; fall back to computing from last_auto_download
                let nextRunDisplay = null
                if (nextJob && !nextJob.paused && nextJob.next_run) {
                  nextRunDisplay = new Date(utc(nextJob.next_run)).toLocaleString()
                } else if (lastRan) {
                  const next = new Date(new Date(utc(lastRan)).getTime() + autoCooldown * 86400000)
                  nextRunDisplay = next.toLocaleString()
                }
                return nextRunDisplay ? (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
                       style={{ background:'rgba(212,153,34,0.06)', border:'1px solid rgba(212,153,34,0.15)' }}>
                    <Clock size={11} style={{ color:'#d29922', flexShrink:0 }} />
                    <span className="text-[11px]" style={{ color:'var(--text-secondary)' }}>
                      Next auto-download: <strong style={{ color:'#d29922' }}>{nextRunDisplay}</strong>
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
                       style={{ background:'rgba(212,153,34,0.04)', border:'1px solid rgba(212,153,34,0.12)' }}>
                    <Clock size={11} style={{ color:'var(--text-muted)', flexShrink:0 }} />
                    <span className="text-[11px]" style={{ color:'var(--text-muted)' }}>
                      Next auto-download: will run within {autoCooldown} day{autoCooldown !== 1 ? 's' : ''} of first enabling
                    </span>
                  </div>
                )
              })()}
              <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl"
                   style={{ background:'rgba(212,153,34,0.06)', border:'1px solid rgba(212,153,34,0.2)' }}>
                <ShieldAlert size={13} style={{ color:'#d29922', flexShrink:0, marginTop:1 }} />
                <div className="text-[11px]" style={{ color:'var(--text-secondary)' }}>
                  Use <strong style={{ color:'#d29922' }}>Get next</strong> in the Discovery Queue to pin a specific album for priority download.
                  Use <strong>Not that one</strong> to exclude items. Turning this off stops all automatic downloads.
                </div>
              </div>
            </>
          )}
        </div>
      </TaskCard>

      {/* Save */}
      <div className="flex items-center justify-between pt-2">
        <div>
          {saveMsg && <span className={`text-sm anim-fade-in ${saveMsg.startsWith('✓') ? 'text-[var(--accent)]' : 'text-[var(--danger)]'}`}>{saveMsg}</span>}
        </div>
        <button onClick={save} disabled={saving} className="btn-primary">
          {saving ? <><Loader2 size={14} className="animate-spin" />Saving…</> : <><Save size={14} />Save Settings</>}
        </button>
      </div>
    </div>
  )
}
