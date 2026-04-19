import { useState, useEffect } from 'react'
import { api } from '../lib/api.js'
import { useAuth } from '../contexts/AuthContext.jsx'
import {
  BarChart2, Music2, Mic2, Tag, TrendingUp, TrendingDown,
  ChevronUp, ChevronDown, ChevronsUpDown, Star, Loader2,
  AlertCircle, Play, SkipForward, Heart, Snowflake, Zap,
  Clock, Globe, RefreshCw, ThumbsDown, Info, Flame, Activity,
  ExternalLink, Search, X,
} from 'lucide-react'
import MusicUniverseMap from '../components/MusicUniverseMap.jsx'
import { useJellyfinUrl } from '../hooks/useJellyfinUrl.js'
import JellyfinIcon from '../components/JellyfinIcon.jsx'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso) {
  if (!iso) return '—'
  const d = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'
  return new Date(d).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
function fmtDateShort(iso) {
  if (!iso) return '—'
  const d2 = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'
  return new Date(d2).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function daysUntil(iso) {
  if (!iso) return null
  const d = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'
  const ms = new Date(d) - Date.now()
  if (ms <= 0) return null
  return Math.ceil(ms / 86400000)
}

function ScoreBar({ value, max = 100, color = 'var(--accent)' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className="relative h-1.5 bg-[var(--bg-overlay)] rounded-full overflow-hidden w-full">
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-all duration-300"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  )
}

function StatPill({ label, value, color = 'text-[var(--text-primary)]', hint }) {
  return (
    <div className="flex flex-col" title={hint}>
      <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">{label}</span>
      <span className={`text-xs font-semibold mt-0.5 ${color}`}>{value}</span>
    </div>
  )
}

const HOLIDAY_COLORS = {
  christmas:    { bg: 'bg-red-900/40',    text: 'text-red-300',    label: '🎄 Christmas'   },
  hanukkah:     { bg: 'bg-blue-900/40',   text: 'text-blue-300',   label: '🕎 Hanukkah'    },
  halloween:    { bg: 'bg-orange-900/40', text: 'text-orange-300', label: '🎃 Halloween'   },
  thanksgiving: { bg: 'bg-amber-900/40',  text: 'text-amber-300',  label: '🦃 Thanksgiving'},
  easter:       { bg: 'bg-pink-900/40',   text: 'text-pink-300',   label: '🐣 Easter'      },
  valentines:   { bg: 'bg-rose-900/40',   text: 'text-rose-300',   label: "💝 Valentine's" },
  new_year:     { bg: 'bg-purple-900/40', text: 'text-purple-300', label: '🎆 New Year'    },
}

function HolidayBadge({ tag, exclude }) {
  if (!tag) return null
  const c = HOLIDAY_COLORS[tag] || { bg: 'bg-[var(--bg-overlay)]', text: 'text-[var(--text-secondary)]', label: tag }
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${c.bg} ${c.text}`}
          title={exclude ? 'Out of season — excluded from playlists' : 'In season — included in playlists'}>
      {c.label}{exclude && <span className="opacity-60"> ✗</span>}
    </span>
  )
}

function CooldownBadge({ cooldown_until, on_cooldown }) {
  if (!on_cooldown) return <span className="text-[var(--text-secondary)] text-[10px]">—</span>
  const days = daysUntil(cooldown_until)
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-orange-900/40 text-orange-300"
          title={`Cooldown until ${fmtDate(cooldown_until)}`}>
      <Clock size={9} />
      {days != null ? `${days}d` : 'active'}
    </span>
  )
}

function PopularityBar({ value }) {
  if (value == null) return <span className="text-[var(--text-secondary)] text-[10px]">—</span>
  const color = value >= 70 ? '#3fb950' : value >= 40 ? 'var(--accent)' : 'var(--text-secondary)'
  return (
    <div className="flex items-center gap-1.5 min-w-[60px]">
      <span className="text-xs tabular-nums" style={{ color }}>{value.toFixed(0)}</span>
      <ScoreBar value={value} color={color} />
    </div>
  )
}

function SortHeader({ label, field, currentSort, currentOrder, onSort, hint }) {
  const active = currentSort === field
  return (
    <button
      onClick={() => onSort(field)}
      title={hint}
      className={`flex items-center gap-1 text-[10px] uppercase tracking-wider font-semibold
                  hover:text-[var(--text-primary)] transition-colors whitespace-nowrap
                  ${active ? 'text-[var(--accent)]' : 'text-[var(--text-secondary)]'}`}
    >
      {label}
      {active
        ? currentOrder === 'desc' ? <ChevronDown size={11} /> : <ChevronUp size={11} />
        : <ChevronsUpDown size={11} className="opacity-40" />}
    </button>
  )
}

// ── Jellyfin deep-link button strip ──────────────────────────────────────────
// Shown in expanded track rows. Opens Jellyfin in a new tab for
// the artist (search), album (item detail), or track (item detail).

function JellyfinLinkStrip({ track, buildItemUrl, buildSearchUrl }) {
  // Prefer a direct artist profile link if we have the Jellyfin artist item ID;
  // fall back to a name search so the button is never silently broken.
  const artistUrl = track.jellyfin_artist_id
    ? buildItemUrl(track.jellyfin_artist_id)
    : buildSearchUrl(track.artist_name)
  const albumUrl  = track.jellyfin_album_id ? buildItemUrl(track.jellyfin_album_id) : null
  const trackUrl  = track.jellyfin_item_id  ? buildItemUrl(track.jellyfin_item_id)  : null

  // Only render if we have at least one valid URL
  if (!artistUrl && !trackUrl) return null

  return (
    <div className="flex items-center gap-2 pt-3 border-t border-[var(--bg-overlay)]">
      {/* Logo mark */}
      <div className="flex items-center gap-1.5 mr-1">
        <JellyfinIcon size={14} />
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] font-semibold">
          Play on Jellyfin
        </span>
      </div>

      {artistUrl && (
        <a
          href={artistUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold
                     bg-[var(--bg-overlay)] border border-[var(--border)]
                     text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                     hover:border-[var(--accent)]/40 hover:bg-[var(--accent)]/8
                     transition-all duration-150"
          title={track.jellyfin_artist_id
            ? `Open artist "${track.artist_name}" in Jellyfin`
            : `Search for "${track.artist_name}" in Jellyfin`}
        >
          <Mic2 size={10} />
          Artist
          <ExternalLink size={9} className="opacity-50 ml-0.5" />
        </a>
      )}

      {albumUrl && (
        <a
          href={albumUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold
                     bg-[var(--bg-overlay)] border border-[var(--border)]
                     text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                     hover:border-[var(--accent)]/40 hover:bg-[var(--accent)]/8
                     transition-all duration-150"
          title={`Open album "${track.album_name}" in Jellyfin`}
        >
          <Tag size={10} />
          Album
          <ExternalLink size={9} className="opacity-50 ml-0.5" />
        </a>
      )}

      {trackUrl && (
        <a
          href={trackUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold
                     bg-[var(--bg-overlay)] border border-[var(--border)]
                     text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                     hover:border-[var(--accent)]/40 hover:bg-[var(--accent)]/8
                     transition-all duration-150"
          title={`Open "${track.track_name}" in Jellyfin`}
        >
          <Music2 size={10} />
          Track
          <ExternalLink size={9} className="opacity-50 ml-0.5" />
        </a>
      )}
    </div>
  )
}

// ── Jellyfin artist link button ───────────────────────────────────────────────
// Shown in expanded artist rows. Links directly to the Jellyfin artist profile
// if we have the item ID; falls back to a name search otherwise.

function JellyfinArtistLink({ artistName, jellyfinArtistId, buildItemUrl, buildSearchUrl }) {
  const url = jellyfinArtistId
    ? buildItemUrl(jellyfinArtistId)
    : buildSearchUrl(artistName)
  if (!url) return null

  return (
    <div className="flex items-center gap-2 pt-3 border-t border-[var(--bg-overlay)]">
      <div className="flex items-center gap-1.5 mr-1">
        <JellyfinIcon size={14} />
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] font-semibold">
          Jellyfin
        </span>
      </div>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={e => e.stopPropagation()}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold
                   bg-[var(--bg-overlay)] border border-[var(--border)]
                   text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                   hover:border-[var(--accent)]/40 hover:bg-[var(--accent)]/8
                   transition-all duration-150"
        title={jellyfinArtistId
          ? `Open artist "${artistName}" in Jellyfin`
          : `Search for "${artistName}" in Jellyfin`}
      >
        <Mic2 size={10} />
        View Artist in Jellyfin
        <ExternalLink size={9} className="opacity-50 ml-0.5" />
      </a>
    </div>
  )
}

// ── Column definitions ────────────────────────────────────────────────────────

const ALL_COLUMNS = [
  { key: 'track_name',        label: 'Track',           always: true,  hint: 'Track title' },
  { key: 'artist_name',       label: 'Artist',          always: true,  hint: 'Primary artist' },
  { key: 'genre',             label: 'Genre',           default: true,  hint: 'Genre tag stored for this track in Jellyfin.' },
  { key: 'final_score',       label: 'Score',           always: true,  hint: 'Composite recommendation score (0–100). Higher = more likely to appear in playlists.' },
  { key: 'play_count',        label: 'Plays',           default: true,  hint: 'Total play count in Jellyfin' },
  { key: 'last_played',       label: 'Last played',     default: true,  hint: 'Most recent play date' },
  { key: 'play_score',        label: 'Play score',      default: false, hint: 'Score component from play frequency (log-normalised against your most-played track).' },
  { key: 'recency_score',     label: 'Recency',         default: false, hint: 'Score component for how recently you played this. Full marks within 30 days, decays to 0 after 1 year.' },
  { key: 'artist_affinity',   label: 'Artist aff.',     default: true,  hint: 'How much the engine thinks you like this artist (0–100), based on your overall play history for them.' },
  { key: 'genre_affinity',    label: 'Genre affinity',  default: true,  hint: 'How much you like this track\'s genre overall (0–100). Drives 10% of the final score.' },
  { key: 'global_popularity',         label: 'Song Popularity',  default: true,  hint: 'Per-song Last.fm popularity (0–100). Based on how many people globally have listened to this specific track. Requires enrichment to populate — blank until enrichment has run.' },
  { key: 'artist_catalog_popularity', label: 'Catalog rank',     default: true,  hint: "How popular this track is within its own artist's catalog (0–100). 100 = the artist's most-listened song on Last.fm. Used by the 'Artist's Top Tracks' playlist block." },
  { key: 'replay_boost',      label: 'Replay ↑',        default: true,  hint: 'Bonus score from voluntary replays within 7 days of a previous play — a strong signal that you really like this track.' },
  { key: 'novelty_bonus',     label: 'Novelty',         default: false, hint: 'Small bonus for unplayed tracks to give them a chance. 0 on played tracks.' },
  { key: 'skip_penalty',      label: 'Skip pen.',       default: true,  hint: 'Skip penalty multiplier applied to the final score. High values suppress the track in playlists.' },
  { key: 'skip_count',        label: 'Skips',           default: true,  hint: 'Raw skip count vs total events tracked by webhook.' },
  { key: 'skip_streak',       label: 'Skip streak',     default: true,  hint: 'Consecutive skips without a full listen. ≥3 triggers a cooldown.' },
  { key: 'on_cooldown',       label: 'Cooldown',        default: true,  hint: 'Whether this track is in a skip-streak cooldown and excluded from playlists until the timer expires.' },
  { key: 'holiday',           label: 'Holiday',         default: false, hint: 'Auto-detected holiday tag and whether it\'s currently in-season.' },
  // Audio waveform analysis
  { key: 'bpm',            label: 'BPM',          default: false, hint: 'Tempo in beats per minute (librosa waveform analysis).' },
  { key: 'musical_key',    label: 'Key',          default: false, hint: 'Detected tonal center and mode, e.g. "C Major" or "F# Minor".' },
  { key: 'energy',         label: 'Energy',       default: false, hint: 'RMS loudness normalized 0–1. 1 = very loud / energetic.' },
  { key: 'loudness_db',    label: 'Loudness',     default: false, hint: 'Integrated loudness in dBFS. Closer to 0 = louder.' },
  { key: 'beat_strength',  label: 'Beat',         default: false, hint: 'Rhythmic pulse clarity 0–1. 1 = very clear beat, low = diffuse or rubato.' },
  { key: 'time_signature', label: 'Time sig.',    default: false, hint: 'Estimated beats per bar. 3 = waltz/triple, 4 = common time.' },
  { key: 'acousticness',   label: 'Acoustic',     default: false, hint: 'Heuristic acousticness 0–1. 1 = purely acoustic, 0 = fully electronic.' },
]

const DEFAULT_VISIBLE = new Set(
  ALL_COLUMNS.filter(c => c.always || c.default).map(c => c.key)
)

const COLS_STORAGE_KEY = 'jellydj_insights_cols_v1'

function loadSavedCols() {
  try {
    const raw = localStorage.getItem(COLS_STORAGE_KEY)
    if (!raw) return DEFAULT_VISIBLE
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) return DEFAULT_VISIBLE
    const valid = new Set(ALL_COLUMNS.map(c => c.key))
    const filtered = parsed.filter(k => valid.has(k))
    ALL_COLUMNS.filter(c => c.always).forEach(c => filtered.push(c.key))
    return new Set(filtered)
  } catch {
    return DEFAULT_VISIBLE
  }
}

function saveCols(colSet) {
  try {
    localStorage.setItem(COLS_STORAGE_KEY, JSON.stringify([...colSet]))
  } catch {}
}

// ── Summary Cards ─────────────────────────────────────────────────────────────

function SummarySection({ summary }) {
  if (!summary) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
      {/* Totals */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Library</div>
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Total tracks</span>
            <span className="text-sm font-bold text-[var(--text-primary)]">{summary.total_tracks_in_library.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Played</span>
            <span className="text-sm font-semibold text-[var(--accent)]">{summary.played_tracks.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Unplayed</span>
            <span className="text-sm font-semibold text-[var(--text-secondary)]">{summary.unplayed_tracks.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-[var(--text-secondary)]">Artists tracked</span>
            <span className="text-sm font-semibold text-[var(--text-primary)]">{summary.total_artists}</span>
          </div>
          {summary.cooldowns && (
            <div className="flex justify-between items-center pt-2 border-t border-[var(--bg-overlay)]">
              <span className="text-xs text-[var(--text-secondary)]">On cooldown</span>
              <span className="text-sm font-semibold text-orange-400">
                {summary.cooldowns.active.toLocaleString()}
                {summary.cooldowns.permanent_dislikes > 0 && (
                  <span className="text-[10px] text-[var(--danger)] ml-1">
                    +{summary.cooldowns.permanent_dislikes} perm.
                  </span>
                )}
              </span>
            </div>
          )}
          {summary.skip_tracking && (
            <div className="flex justify-between items-center">
              <span className="text-xs text-[var(--text-secondary)]">Skips recorded</span>
              <span className="text-sm font-semibold text-[var(--danger)]">
                {summary.skip_tracking.total_skips_recorded.toLocaleString()}
                {summary.skip_tracking.tracks_with_events > 0 && (
                  <span className="text-[10px] text-[var(--text-secondary)] ml-1">
                    ({summary.skip_tracking.tracks_with_events} tracked)
                  </span>
                )}
              </span>
            </div>
          )}
          {summary.replay_signals && summary.replay_signals.last_7_days > 0 && (
            <div className="flex justify-between items-center">
              <span className="text-xs text-[var(--text-secondary)]">Replays (7d)</span>
              <span className="text-sm font-semibold text-[var(--accent)]">
                {summary.replay_signals.last_7_days}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Top track */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Highlights</div>
        {summary.top_track && (
          <div className="mb-3 pb-3 border-b border-[var(--bg-overlay)]">
            <div className="flex items-center gap-1.5 mb-1">
              <TrendingUp size={11} className="text-[var(--accent)]" />
              <span className="text-[10px] text-[var(--accent)] uppercase tracking-wider">Highest scored</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.top_track.track_name}</div>
            <div className="text-xs text-[var(--text-secondary)] truncate">{summary.top_track.artist_name}</div>
            <div className="text-xs text-[var(--accent)] mt-1">{summary.top_track.final_score.toFixed(1)} / 100</div>
          </div>
        )}
        {summary.most_played_track && (
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <Play size={11} className="text-[#7d8590]" />
              <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Most played</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.most_played_track.track_name}</div>
            <div className="text-xs text-[var(--text-secondary)] truncate">{summary.most_played_track.artist_name}</div>
            <div className="text-xs text-[var(--text-secondary)] mt-1">{summary.most_played_track.play_count} plays</div>
          </div>
        )}
      </div>

      {/* Top genres + skipped */}
      <div className="card">
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-3">Top Genres</div>
        <div className="space-y-2 mb-3">
          {summary.top_genres.slice(0, 4).map(g => (
            <div key={g.genre}>
              <div className="flex justify-between items-center mb-0.5">
                <span className="text-xs text-[var(--text-primary)] truncate">{g.genre || 'Unknown'}</span>
                <span className="text-[10px] text-[var(--text-secondary)] ml-2 flex-shrink-0">{g.affinity_score.toFixed(0)}</span>
              </div>
              <ScoreBar value={g.affinity_score} />
            </div>
          ))}
        </div>
        {summary.most_skipped_artist && (
          <div className="pt-3 border-t border-[var(--bg-overlay)]">
            <div className="flex items-center gap-1.5 mb-1">
              <SkipForward size={11} className="text-[var(--danger)]" />
              <span className="text-[10px] text-[var(--danger)] uppercase tracking-wider">Most skipped artist</span>
            </div>
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">{summary.most_skipped_artist.artist_name}</div>
            <div className="text-xs text-[var(--text-secondary)]">{(summary.most_skipped_artist.skip_rate * 100).toFixed(0)}% skip rate · {summary.most_skipped_artist.total_skips} skips</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Column Picker ─────────────────────────────────────────────────────────────

function ColumnPicker({ visible, onChange }) {
  const [open, setOpen] = useState(false)
  const toggleable = ALL_COLUMNS.filter(c => !c.always)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-overlay)] border border-[var(--border)]
                   rounded-lg text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
      >
        <Activity size={12} />
        Columns
        <ChevronDown size={10} className={open ? 'rotate-180' : ''} style={{ transition: 'transform 0.15s' }} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-[var(--bg-surface)] border border-[var(--border)]
                        rounded-xl shadow-xl p-3 min-w-[220px] space-y-1">
          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-2 px-1">Toggle columns</div>
          {toggleable.map(col => (
            <label key={col.key}
              className="flex items-start gap-2 px-1 py-1 rounded hover:bg-[var(--bg-overlay)] cursor-pointer"
              title={col.hint}>
              <input
                type="checkbox"
                checked={visible.has(col.key)}
                onChange={() => {
                  const next = new Set(visible)
                  next.has(col.key) ? next.delete(col.key) : next.add(col.key)
                  onChange(next)
                }}
                className="mt-0.5 accent-[var(--accent)]"
              />
              <div>
                <div className="text-xs text-[var(--text-primary)]">{col.label}</div>
                {col.hint && <div className="text-[10px] text-[var(--text-secondary)] leading-tight mt-0.5">{col.hint}</div>}
              </div>
            </label>
          ))}
          <button
            onClick={() => onChange(DEFAULT_VISIBLE)}
            className="mt-2 w-full text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] py-1 border-t border-[var(--bg-overlay)] transition-colors"
          >
            Reset to default
          </button>
        </div>
      )}
    </div>
  )
}

// ── Track Table ───────────────────────────────────────────────────────────────

function TrackTable({ userId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState('final_score')
  const [order, setOrder] = useState('desc')
  const [playedFilter, setPlayedFilter] = useState('all')
  const [cooldownFilter, setCooldownFilter] = useState('all')
  const [holidayFilter, setHolidayFilter] = useState('all')
  const [searchFilter, setSearchFilter] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [page, setPage] = useState(1)
  const [expandedRow, setExpandedRow] = useState(null)
  const [visibleCols, setVisibleCols] = useState(() => loadSavedCols())

  const { buildItemUrl, buildSearchUrl } = useJellyfinUrl()

  const doFetch = (uid, sb, ord, pf, af, pg, hf, cf) => {
    if (!uid) return
    setLoading(true)
    const params = new URLSearchParams({
      user_id: uid, sort_by: sb, order: ord,
      played_filter: pf, page: pg,
      page_size: 50,
      cooldown_filter: cf || 'all',
      ...(af ? { search_filter: af } : {}),
      ...(hf && hf !== 'all' ? { holiday_filter: hf } : {}),
    })
    api.get(`/api/insights/tracks?${params}`)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    doFetch(userId, sort, order, playedFilter, searchFilter, page, holidayFilter, cooldownFilter)
  }, [userId, sort, order, playedFilter, searchFilter, page, holidayFilter, cooldownFilter])

  const handleSort = (field) => {
    const newOrder = sort === field ? (order === 'desc' ? 'asc' : 'desc') : 'desc'
    setSort(field)
    setOrder(newOrder)
    setPage(1)
  }

  const handleArtistSearch = (e) => {
    e.preventDefault()
    setSearchFilter(searchInput)
    setPage(1)
  }

  const scoreColor = (s) => s >= 75 ? 'var(--accent)' : s >= 55 ? 'var(--text-primary)' : 'var(--text-secondary)'

  const col = (key) => visibleCols.has(key)

  return (
    <div>
      {/* ── Controls ── */}
      <div className="flex flex-wrap gap-2 mb-4 items-start">
        {/* Played filter */}
        <div className="flex bg-[var(--bg-overlay)] rounded-lg border border-[var(--border)] overflow-hidden text-xs">
          {['all', 'played', 'unplayed'].map(f => (
            <button key={f}
              onClick={() => { setPlayedFilter(f); setPage(1) }}
              className={`px-3 py-1.5 font-medium transition-colors capitalize
                ${playedFilter === f ? 'bg-[var(--accent)] text-[var(--bg)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
            >{f}</button>
          ))}
        </div>

        {/* Cooldown filter */}
        <div className="flex bg-[var(--bg-overlay)] rounded-lg border border-[var(--border)] overflow-hidden text-xs">
          {[
            { v: 'all',    label: 'All' },
            { v: 'active', label: '🧊 On cooldown' },
            { v: 'clear',  label: '✓ Clear' },
          ].map(({ v, label }) => (
            <button key={v}
              onClick={() => { setCooldownFilter(v); setPage(1) }}
              className={`px-3 py-1.5 font-medium transition-colors
                ${cooldownFilter === v ? 'bg-[var(--accent)] text-[var(--bg)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
            >{label}</button>
          ))}
        </div>

        {/* Artist search + holiday filter */}
        <form onSubmit={handleArtistSearch} className="flex gap-1 flex-wrap items-center">
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="Search artist, song, album…"
            className="bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg px-3 py-1.5
                       text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] outline-none
                       focus:border-[var(--accent)] transition-colors w-40"
          />
          <button type="submit" className="px-2 py-1.5 bg-[var(--bg-overlay)] border border-[var(--border)]
                                           rounded-lg text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
            Go
          </button>

          {/* Holiday filter */}
          <div className="flex gap-1 flex-wrap">
            {[
              { v: 'all',      label: 'All songs'      },
              { v: 'holiday',  label: '🎄 Holiday only' },
              { v: 'excluded', label: '✗ Out of season' },
              { v: 'normal',   label: '✓ Non-holiday'  },
            ].map(({ v, label }) => (
              <button key={v} type="button"
                onClick={() => { setHolidayFilter(v); setPage(1) }}
                className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors
                  ${holidayFilter === v
                    ? 'bg-[var(--accent)] text-[var(--bg)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] bg-[var(--bg-surface)]'}`}>
                {label}
              </button>
            ))}
          </div>

          {searchFilter && (
            <button type="button" onClick={() => { setSearchFilter(''); setSearchInput(''); setPage(1) }}
              className="px-2 py-1.5 text-xs text-[var(--danger)] hover:text-[#ff7b72] transition-colors">
              ✕
            </button>
          )}
        </form>

        <div className="ml-auto flex items-center gap-2">
          {data && (
            <span className="text-xs text-[var(--text-secondary)]">
              {data.total.toLocaleString()} tracks · page {data.page}/{data.pages}
            </span>
          )}
          <ColumnPicker visible={visibleCols} onChange={cols => { setVisibleCols(cols); saveCols(cols) }} />
        </div>
      </div>

      {/* ── Legend ── */}
      <div className="flex flex-wrap gap-3 mb-3 text-[10px] text-[var(--text-secondary)]">
        <span className="flex items-center gap-1"><Heart size={9} className="text-[var(--danger)]" /> Favorite</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-[#388bfd] inline-block" /> Unplayed</span>
        <span className="flex items-center gap-1"><Clock size={9} className="text-orange-400" /> On cooldown (skip streak ≥ 3)</span>
        <span className="flex items-center gap-1"><Zap size={9} className="text-yellow-400" /> Replay boost active</span>
        <span className="flex items-center gap-1"><Globe size={9} className="text-green-400" /> Global popularity</span>
        <span className="text-[var(--text-secondary)] italic">Click any row to expand full score breakdown</span>
      </div>

      {/* ── Table ── */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--bg-overlay)]">
                <th className="px-4 py-2.5 w-8 text-[10px] text-[var(--text-secondary)]">#</th>

                {col('track_name') && (
                  <th className="px-2 py-2.5">
                    <SortHeader label="Track" field="track_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {col('artist_name') && (
                  <th className="px-2 py-2.5 hidden sm:table-cell">
                    <SortHeader label="Artist" field="artist_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {col('genre') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]">Genre</span>
                  </th>
                )}
                {col('final_score') && (
                  <th className="px-2 py-2.5">
                    <SortHeader label="Score" field="final_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Composite recommendation score (0–100)" />
                  </th>
                )}
                {col('play_count') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <SortHeader label="Plays" field="play_count" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {col('last_played') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Last played" field="last_played" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {col('global_popularity') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Song Pop." field="global_popularity" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Song-level Last.fm popularity 0–100 (falls back to artist-level if not enriched)" />
                  </th>
                )}
                {col('artist_catalog_popularity') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Catalog rank" field="artist_catalog_popularity" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="How popular this track is within its artist's catalog (0–100). 100 = artist's #1 hit." />
                  </th>
                )}
                {col('artist_affinity') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Artist aff." field="artist_affinity" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Your affinity for this artist (0–100)" />
                  </th>
                )}
                {col('genre_affinity') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Genre aff." field="genre_affinity" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Your affinity for this track's genre (0–100). Drives 10% of the final score." />
                  </th>
                )}
                {col('play_score') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Play score" field="play_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Score from play frequency, log-normalised (45% weight)" />
                  </th>
                )}
                {col('recency_score') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Recency" field="recency_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="How recently you played this. Decays from 100 → 0 over 1 year (25% weight)" />
                  </th>
                )}
                {col('novelty_bonus') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Novelty" field="novelty_bonus" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Small bonus for unplayed tracks to surface them. 0 on played tracks." />
                  </th>
                )}
                {col('replay_boost') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Replay ↑" field="replay_boost" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Bonus points from voluntary replays within 7 days" />
                  </th>
                )}
                {col('skip_penalty') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Skip pen." field="skip_penalty" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Score multiplier penalty from skipping. High = suppressed in playlists." />
                  </th>
                )}
                {col('skip_count') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <SortHeader label="Skips" field="skip_count" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {col('skip_streak') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Streak" field="skip_streak" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Consecutive skips. Reaches 3 → cooldown triggered." />
                  </th>
                )}
                {col('on_cooldown') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <SortHeader label="Cooldown" field="cooldown_until" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Active skip-streak cooldown. Track excluded from playlists until timer expires." />
                  </th>
                )}
                {col('holiday') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]">Holiday</span>
                  </th>
                )}
                {col('bpm') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Tempo in beats per minute">BPM</span>
                  </th>
                )}
                {col('musical_key') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Detected tonal center and mode">Key</span>
                  </th>
                )}
                {col('energy') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="RMS loudness normalized 0–1">Energy</span>
                  </th>
                )}
                {col('loudness_db') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Integrated loudness in dBFS">Loudness</span>
                  </th>
                )}
                {col('beat_strength') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Rhythmic pulse clarity 0–1">Beat</span>
                  </th>
                )}
                {col('time_signature') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Beats per bar">Time sig.</span>
                  </th>
                )}
                {col('acousticness') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-[var(--text-secondary)]" title="Acousticness 0–1 (1 = fully acoustic)">Acoustic</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={20} className="px-4 py-8 text-center">
                  <Loader2 size={16} className="animate-spin text-[var(--text-secondary)] mx-auto" />
                </td></tr>
              )}
              {!loading && data?.tracks?.map((t, i) => (
                <>
                  <tr
                    key={t.jellyfin_item_id}
                    onClick={() => setExpandedRow(expandedRow === t.jellyfin_item_id ? null : t.jellyfin_item_id)}
                    className={`border-b border-[var(--bg-overlay)] hover:bg-[var(--bg-surface)] cursor-pointer transition-colors group
                      ${t.on_cooldown ? 'bg-orange-950/10' : ''}`}
                  >
                    <td className="px-4 py-2.5 text-[10px] text-[var(--text-secondary)]">
                      {(page - 1) * 50 + i + 1}
                    </td>

                    {/* Track name */}
                    {col('track_name') && (
                      <td className="px-2 py-2.5 max-w-[180px]">
                        <div className="flex items-center gap-1.5">
                          {t.is_favorite && <Heart size={10} className="text-[var(--danger)] flex-shrink-0" />}
                          {!t.is_played && <div className="w-1.5 h-1.5 rounded-full bg-[#388bfd] flex-shrink-0" title="Unplayed" />}
                          {t.on_cooldown && <Clock size={10} className="text-orange-400 flex-shrink-0" title="On cooldown" />}
                          {(t.replay_boost || 0) > 0 && <Zap size={10} className="text-yellow-400 flex-shrink-0" title={`Replay boost +${t.replay_boost}`} />}
                          <span className="text-xs text-[var(--text-primary)] truncate">{t.track_name}</span>
                        </div>
                      </td>
                    )}

                    {/* Artist */}
                    {col('artist_name') && (
                      <td className="px-2 py-2.5 hidden sm:table-cell max-w-[140px]">
                        <span className="text-xs text-[var(--text-secondary)] truncate block">{t.artist_name}</span>
                      </td>
                    )}

                    {/* Genre */}
                    {col('genre') && (
                      <td className="px-2 py-2.5 hidden md:table-cell max-w-[120px]">
                        <span className="text-xs text-[var(--text-secondary)] truncate block">{t.genre || '—'}</span>
                      </td>
                    )}

                    {/* Score */}
                    {col('final_score') && (
                      <td className="px-2 py-2.5">
                        <div className="flex items-center gap-2 min-w-[70px]">
                          <span className="text-xs font-bold tabular-nums" style={{ color: scoreColor(t.final_score) }}>
                            {t.final_score.toFixed(1)}
                          </span>
                          <ScoreBar value={t.final_score} color={scoreColor(t.final_score)} />
                        </div>
                      </td>
                    )}

                    {/* Play count */}
                    {col('play_count') && (
                      <td className="px-2 py-2.5 hidden md:table-cell text-xs text-[var(--text-secondary)] tabular-nums">
                        {t.play_count || '—'}
                      </td>
                    )}

                    {/* Last played */}
                    {col('last_played') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs text-[var(--text-secondary)] tabular-nums whitespace-nowrap">
                        {t.last_played ? fmtDateShort(t.last_played) : '—'}
                      </td>
                    )}

                    {/* Song popularity */}
                    {col('global_popularity') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell min-w-[80px]">
                        <PopularityBar value={t.global_popularity} />
                      </td>
                    )}

                    {/* Catalog rank */}
                    {col('artist_catalog_popularity') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell min-w-[80px]">
                        {t.artist_catalog_popularity != null
                          ? <PopularityBar value={t.artist_catalog_popularity} />
                          : <span className="text-xs text-[var(--text-secondary)]">—</span>
                        }
                      </td>
                    )}

                    {/* Artist affinity */}
                    {col('artist_affinity') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs text-[var(--text-secondary)] tabular-nums">
                        {t.artist_affinity.toFixed(1)}
                      </td>
                    )}

                    {/* Genre affinity */}
                    {col('genre_affinity') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell min-w-[80px]">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs tabular-nums"
                            style={{ color: t.genre_affinity >= 60 ? 'var(--accent)' : t.genre_affinity >= 30 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                            {t.genre_affinity.toFixed(1)}
                          </span>
                          <ScoreBar value={t.genre_affinity}
                            color={t.genre_affinity >= 60 ? '#ffa657' : 'var(--text-secondary)'} />
                        </div>
                      </td>
                    )}

                    {/* Play score */}
                    {col('play_score') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell min-w-[80px]">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs tabular-nums text-[var(--text-secondary)]">{t.play_score.toFixed(1)}</span>
                          <ScoreBar value={t.play_score} color="var(--accent)" />
                        </div>
                      </td>
                    )}

                    {/* Recency score */}
                    {col('recency_score') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell min-w-[80px]">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs tabular-nums text-[var(--text-secondary)]">{t.recency_score.toFixed(1)}</span>
                          <ScoreBar value={t.recency_score} color="#7ee787" />
                        </div>
                      </td>
                    )}

                    {/* Novelty bonus */}
                    {col('novelty_bonus') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums"
                          style={{ color: (t.novelty_bonus || 0) > 0 ? 'var(--accent)' : 'var(--text-secondary)' }}>
                        {(t.novelty_bonus || 0) > 0 ? `+${parseFloat(t.novelty_bonus).toFixed(1)}` : '—'}
                      </td>
                    )}

                    {/* Replay boost */}
                    {col('replay_boost') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums"
                          style={{ color: (t.replay_boost || 0) > 0 ? '#e3b341' : 'var(--text-secondary)' }}>
                        {(t.replay_boost || 0) > 0 ? `+${t.replay_boost.toFixed(1)}` : '—'}
                      </td>
                    )}

                    {/* Skip penalty */}
                    {col('skip_penalty') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs tabular-nums"
                          style={{ color: t.skip_penalty > 0.3 ? 'var(--danger)' : t.skip_rate > 0.3 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                        {t.skip_penalty > 0
                          ? `${(t.skip_penalty * 100).toFixed(0)}%`
                          : t.skip_rate > 0 ? `${(t.skip_rate * 100).toFixed(0)}%*` : '—'}
                      </td>
                    )}

                    {/* Skips */}
                    {col('skip_count') && (
                      <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                          title={t.total_events > 0 ? `${t.skip_count} skips out of ${t.total_events} plays tracked` : 'No webhook events yet'}>
                        {t.skip_count > 0
                          ? <span style={{ color: 'var(--danger)' }}>
                              {t.skip_count}<span className="text-[var(--text-secondary)] text-[10px]">/{t.total_events}</span>
                            </span>
                          : <span className="text-[var(--text-secondary)]">—</span>}
                      </td>
                    )}

                    {/* Skip streak */}
                    {col('skip_streak') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums"
                          title="Consecutive skips without finishing. ≥3 triggers cooldown.">
                        {(t.skip_streak || 0) > 0
                          ? <span style={{ color: t.skip_streak >= 3 ? 'var(--danger)' : '#d29922' }}>
                              {t.skip_streak}
                              {t.skip_streak >= 3 && ' 🧊'}
                            </span>
                          : <span className="text-[var(--text-secondary)]">—</span>}
                      </td>
                    )}

                    {/* Cooldown */}
                    {col('on_cooldown') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell">
                        <CooldownBadge cooldown_until={t.cooldown_until} on_cooldown={t.on_cooldown} />
                      </td>
                    )}

                    {/* Holiday */}
                    {col('holiday') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell">
                        <HolidayBadge tag={t.holiday_tag} exclude={t.holiday_exclude} />
                      </td>
                    )}

                    {/* BPM */}
                    {col('bpm') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums text-[var(--text-secondary)]">
                        {t.bpm != null ? `${t.bpm}` : '—'}
                      </td>
                    )}

                    {/* Musical key */}
                    {col('musical_key') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs text-[var(--text-secondary)] whitespace-nowrap">
                        {t.musical_key || '—'}
                      </td>
                    )}

                    {/* Energy */}
                    {col('energy') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell min-w-[70px]">
                        {t.energy != null
                          ? <div className="flex items-center gap-1.5">
                              <span className="text-xs tabular-nums text-[var(--text-secondary)]">{t.energy.toFixed(2)}</span>
                              <ScoreBar value={t.energy * 100} color="#f78166" />
                            </div>
                          : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                      </td>
                    )}

                    {/* Loudness */}
                    {col('loudness_db') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums text-[var(--text-secondary)]">
                        {t.loudness_db != null ? `${t.loudness_db.toFixed(1)} dB` : '—'}
                      </td>
                    )}

                    {/* Beat strength */}
                    {col('beat_strength') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell min-w-[70px]">
                        {t.beat_strength != null
                          ? <div className="flex items-center gap-1.5">
                              <span className="text-xs tabular-nums text-[var(--text-secondary)]">{t.beat_strength.toFixed(2)}</span>
                              <ScoreBar value={t.beat_strength * 100} color="#e3b341" />
                            </div>
                          : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                      </td>
                    )}

                    {/* Time signature */}
                    {col('time_signature') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs tabular-nums text-[var(--text-secondary)]">
                        {t.time_signature != null ? `${t.time_signature}/4` : '—'}
                      </td>
                    )}

                    {/* Acousticness */}
                    {col('acousticness') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell min-w-[70px]">
                        {t.acousticness != null
                          ? <div className="flex items-center gap-1.5">
                              <span className="text-xs tabular-nums text-[var(--text-secondary)]">{t.acousticness.toFixed(2)}</span>
                              <ScoreBar value={t.acousticness * 100} color="#7ee787" />
                            </div>
                          : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                      </td>
                    )}
                  </tr>

                  {/* ── Expanded row ── */}
                  {expandedRow === t.jellyfin_item_id && (
                    <tr key={`${t.jellyfin_item_id}-exp`} className="bg-[var(--bg-surface)] border-b border-[var(--bg-overlay)]">
                      <td colSpan={20} className="px-4 py-4">
                        <div className="space-y-3">
                          {/* Score breakdown header */}
                          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold flex items-center gap-1.5">
                            <Activity size={10} />
                            Score breakdown — {t.track_name}
                            {t.on_cooldown && (
                              <span className="ml-2 px-1.5 py-0.5 rounded bg-orange-900/40 text-orange-300 text-[10px] font-semibold">
                                🧊 On cooldown — excluded from playlists
                              </span>
                            )}
                            {t.holiday_exclude && (
                              <span className="ml-1 px-1.5 py-0.5 rounded bg-red-900/30 text-red-300 text-[10px] font-semibold">
                                Out-of-season holiday track — excluded
                              </span>
                            )}
                          </div>

                          {/* Score component bars */}
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <div>
                              <div className="flex justify-between mb-1">
                                <span className="text-[10px] text-[var(--text-secondary)]">Play score</span>
                                <span className="text-[10px] font-semibold text-[var(--text-primary)]">{t.play_score.toFixed(1)}</span>
                              </div>
                              <ScoreBar value={t.play_score} color="var(--accent)" />
                              <div className="text-[9px] text-[var(--text-secondary)] mt-0.5">Weight 45% of final</div>
                            </div>
                            <div>
                              <div className="flex justify-between mb-1">
                                <span className="text-[10px] text-[var(--text-secondary)]">Recency</span>
                                <span className="text-[10px] font-semibold text-[var(--text-primary)]">{t.recency_score.toFixed(1)}</span>
                              </div>
                              <ScoreBar value={t.recency_score} color="#7ee787" />
                              <div className="text-[9px] text-[var(--text-secondary)] mt-0.5">Weight 25% · decays after 30d</div>
                            </div>
                            <div>
                              <div className="flex justify-between mb-1">
                                <span className="text-[10px] text-[var(--text-secondary)]">Artist affinity</span>
                                <span className="text-[10px] font-semibold text-[var(--text-primary)]">{t.artist_affinity.toFixed(1)}</span>
                              </div>
                              <ScoreBar value={t.artist_affinity} color="#d2a8ff" />
                              <div className="text-[9px] text-[var(--text-secondary)] mt-0.5">Weight 20%</div>
                            </div>
                            <div>
                              <div className="flex justify-between mb-1">
                                <span className="text-[10px] text-[var(--text-secondary)]">Genre affinity</span>
                                <span className="text-[10px] font-semibold text-[var(--text-primary)]">{t.genre_affinity.toFixed(1)}</span>
                              </div>
                              <ScoreBar value={t.genre_affinity} color="#ffa657" />
                              <div className="text-[9px] text-[var(--text-secondary)] mt-0.5">Weight 10%</div>
                            </div>
                          </div>

                          {/* Modifiers */}
                          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 pt-1 border-t border-[var(--bg-overlay)]">
                            <StatPill label="Album" value={t.album_name || '—'} />
                            <StatPill label="Genre" value={t.genre || '—'} />
                            <StatPill label="Last played" value={fmtDateShort(t.last_played) || 'Never'} />
                            <StatPill label={t.track_popularity != null ? 'Song popularity' : 'Popularity (artist~)'}
                              value={t.global_popularity != null ? `${t.global_popularity.toFixed(0)} / 100` : 'Not enriched'}
                              hint={t.track_popularity != null ? `Per-song Last.fm popularity (${(t.track_listeners || 0).toLocaleString()} listeners, ${(t.track_playcount || 0).toLocaleString()} plays globally).` : 'Falling back to artist-level popularity — run enrichment to get per-song data.'}
                              color={t.global_popularity >= 70 ? 'text-green-400' : t.global_popularity >= 40 ? 'text-[var(--accent)]' : 'text-[var(--text-secondary)]'}
                            />
                            <StatPill label="Replay boost"
                              value={(t.replay_boost || 0) > 0 ? `+${t.replay_boost.toFixed(2)} pts` : 'None'}
                              hint="Bonus from replaying within 7 days. Max cap +12 pts."
                              color={(t.replay_boost || 0) > 0 ? 'text-yellow-400' : 'text-[var(--text-secondary)]'}
                            />
                            <StatPill label="Novelty bonus"
                              value={(t.novelty_bonus || 0) > 0 ? `+${parseFloat(t.novelty_bonus).toFixed(1)}` : '—'}
                              hint="Small bonus for unplayed tracks to surface them in playlists."
                            />
                            <StatPill label="Skip penalty"
                              value={t.skip_penalty > 0 ? `${(t.skip_penalty * 100).toFixed(0)}% suppression` : 'None'}
                              hint="How much the skip penalty multiplier reduces this track's final score."
                              color={t.skip_penalty > 0.3 ? 'text-[var(--danger)]' : 'text-[var(--text-primary)]'}
                            />
                            <StatPill label="Skip streak"
                              value={`${t.skip_streak || 0} consecutive`}
                              hint="Consecutive skips. ≥3 triggers an active cooldown."
                              color={(t.skip_streak || 0) >= 3 ? 'text-[var(--danger)]' : (t.skip_streak || 0) > 0 ? 'text-[#d29922]' : 'text-[var(--text-secondary)]'}
                            />
                            <StatPill label="Skips / events"
                              value={t.total_events > 0 ? `${t.skip_count} / ${t.total_events}` : 'No data'}
                              color={t.skip_count > 0 ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'}
                            />
                            <StatPill label="Live skip rate"
                              value={t.total_events > 0 ? `${(t.skip_rate * 100).toFixed(0)}%` : '—'}
                              color={t.skip_rate > 0.4 ? 'text-[var(--danger)]' : t.skip_rate > 0.2 ? 'text-[#d29922]' : 'text-[var(--text-secondary)]'}
                            />
                            {t.on_cooldown && (
                              <StatPill label="Cooldown expires"
                                value={fmtDate(t.cooldown_until)}
                                color="text-orange-400"
                                hint="Track excluded from all playlists until this time."
                              />
                            )}
                            {t.holiday_tag && (
                              <div className="flex flex-col gap-0.5">
                                <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Holiday</span>
                                <HolidayBadge tag={t.holiday_tag} exclude={t.holiday_exclude} />
                              </div>
                            )}
                          </div>

                          {/* Why this score explanation */}
                          <div className="mt-2 p-2.5 rounded-lg bg-[var(--bg-overlay)] text-[11px] text-[var(--text-secondary)] leading-relaxed">
                            <span className="font-semibold text-[var(--text-primary)]">Why this score? </span>
                            {t.is_played ? (
                              <>
                                This track has been played <strong className="text-[var(--text-primary)]">{t.play_count}×</strong>,
                                scoring <strong className="text-[var(--text-primary)]">{t.play_score.toFixed(0)}/100</strong> for frequency.
                                {t.last_played && <> Last heard <strong className="text-[var(--text-primary)]">{fmtDateShort(t.last_played)}</strong>, giving a recency score of <strong className="text-[var(--text-primary)]">{t.recency_score.toFixed(0)}</strong>.</>}
                                {' '}Artist affinity <strong className="text-[var(--text-primary)]">{t.artist_affinity.toFixed(0)}</strong> and genre affinity <strong className="text-[var(--text-primary)]">{t.genre_affinity.toFixed(0)}</strong> add their weighted pull.
                                {t.skip_penalty > 0 && <> A skip penalty of <strong className="text-[var(--danger)]">{(t.skip_penalty * 100).toFixed(0)}%</strong> suppresses the result.</>}
                                {(t.replay_boost || 0) > 0 && <> A replay boost of <strong className="text-yellow-400">+{t.replay_boost.toFixed(1)}</strong> was added because you voluntarily revisited this track or its artist within 7 days.</>}
                                {t.on_cooldown && <> ⚠️ <strong className="text-orange-400">Currently on cooldown</strong> — this track won't appear in playlists until {fmtDate(t.cooldown_until)}.</>}
                              </>
                            ) : (
                              <>
                                This track is <strong className="text-[#388bfd]">unplayed</strong>. It starts with a base score of 35 plus artist/genre affinity signals ({t.artist_affinity.toFixed(0)} + {t.genre_affinity.toFixed(0)}), capped at 65 to keep it below genuinely loved tracks.
                                {(t.novelty_bonus || 0) > 0 && <> A small novelty bonus of +{parseFloat(t.novelty_bonus).toFixed(1)} helps surface it.</>}
                                {t.track_popularity != null ? <> This song has <strong className="text-green-400">{(t.track_listeners || 0).toLocaleString()}</strong> Last.fm listeners (song popularity <strong className="text-green-400">{t.track_popularity.toFixed(0)}</strong>/100), which nudges its score and informs New For You ranking.</> : t.artist_popularity != null ? <> No per-song data yet — using artist popularity (<strong className="text-[var(--accent)]">{t.artist_popularity.toFixed(0)}</strong>/100) as a fallback. Run enrichment to get track-specific listener counts.</> : null}
                              </>
                            )}
                          </div>

                          {/* ── Audio analysis ── */}
                          {t.audio_analyzed_at && (
                            <div className="pt-3 border-t border-[var(--bg-overlay)]">
                              <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold flex items-center gap-1.5 mb-2">
                                <Activity size={10} />
                                Audio waveform analysis
                                <span className="ml-auto text-[9px] font-normal normal-case opacity-60">
                                  analyzed {fmtDateShort(t.audio_analyzed_at)}
                                </span>
                              </div>
                              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
                                <StatPill label="BPM" value={t.bpm != null ? `${t.bpm}` : '—'} hint="Tempo in beats per minute" />
                                <StatPill label="Key" value={t.musical_key || '—'}
                                  hint={t.key_confidence != null ? `Key confidence: ${(t.key_confidence * 100).toFixed(0)}%` : 'Musical key and mode'} />
                                <div className="flex flex-col">
                                  <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider" title="RMS loudness normalized 0–1">Energy</span>
                                  {t.energy != null
                                    ? <><span className="text-xs font-semibold mt-0.5">{t.energy.toFixed(3)}</span>
                                        <ScoreBar value={t.energy * 100} color="#f78166" /></>
                                    : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                                </div>
                                <StatPill label="Loudness" value={t.loudness_db != null ? `${t.loudness_db.toFixed(1)} dBFS` : '—'}
                                  hint="Integrated loudness. Closer to 0 = louder." />
                                <div className="flex flex-col">
                                  <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider" title="Onset envelope clarity 0–1">Beat strength</span>
                                  {t.beat_strength != null
                                    ? <><span className="text-xs font-semibold mt-0.5">{t.beat_strength.toFixed(3)}</span>
                                        <ScoreBar value={t.beat_strength * 100} color="#e3b341" /></>
                                    : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                                </div>
                                <StatPill label="Time sig." value={t.time_signature != null ? `${t.time_signature}/4` : '—'}
                                  hint="3 = waltz/triple time, 4 = common time" />
                                <div className="flex flex-col">
                                  <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider" title="1 = fully acoustic, 0 = fully electronic">Acousticness</span>
                                  {t.acousticness != null
                                    ? <><span className="text-xs font-semibold mt-0.5">{t.acousticness.toFixed(3)}</span>
                                        <ScoreBar value={t.acousticness * 100} color="#7ee787" /></>
                                    : <span className="text-xs text-[var(--text-secondary)]">—</span>}
                                </div>
                                <StatPill label="Key confidence"
                                  value={t.key_confidence != null ? `${(t.key_confidence * 100).toFixed(0)}%` : '—'}
                                  hint="Krumhansl-Schmuckler correlation score for the detected key (0–100%)" />
                              </div>
                            </div>
                          )}
                          {!t.audio_analyzed_at && (
                            <div className="pt-3 border-t border-[var(--bg-overlay)]">
                              <div className="text-[10px] text-[var(--text-secondary)] flex items-center gap-1.5">
                                <Activity size={10} />
                                Audio waveform analysis — not yet analyzed. Run the audio analysis job from Automation to populate these fields.
                              </div>
                            </div>
                          )}

                          {/* ── Jellyfin deep-links ── */}
                          <JellyfinLinkStrip
                            track={t}
                            buildItemUrl={buildItemUrl}
                            buildSearchUrl={buildSearchUrl}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {!loading && !data?.tracks?.length && (
                <tr><td colSpan={20} className="px-4 py-8 text-center text-sm text-[var(--text-secondary)]">
                  No tracks found. Run a full scan first.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-center gap-2 p-3 border-t border-[var(--bg-overlay)]">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              ← Prev
            </button>
            <span className="text-xs text-[var(--text-secondary)]">{page} / {data.pages}</span>
            <button onClick={() => setPage(p => Math.min(data.pages, p + 1))} disabled={page === data.pages}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Genre Column definitions ──────────────────────────────────────────────────

const GENRE_COLUMNS = [
  { key: 'genre',               label: 'Genre',          always: true,  hint: 'Canonical genre name' },
  { key: 'affinity_score',      label: 'Affinity',       always: true,  hint: 'Genre affinity score (0–100): how much you enjoy this genre based on plays, recency, and favorites.' },
  { key: 'normalized_affinity', label: 'Norm. affinity', default: false, hint: 'Affinity normalized relative to your top genre (0–100). This is the scale used by playlist blocks — your #1 genre always scores 100.' },
  { key: 'trend_direction',     label: 'Trend',          default: true,  hint: 'Rising = more plays in last 30 days vs prior 30. Falling = fewer. Stable = roughly the same.' },
  { key: 'recent_plays',        label: 'Recent (30d)',   default: true,  hint: 'Unique tracks in this genre you played in the last 30 days.' },
  { key: 'total_plays',         label: 'Total plays',    default: true,  hint: 'Lifetime play count across all tracks in this genre.' },
  { key: 'total_tracks_played', label: 'Tracks played',  default: true,  hint: 'Unique tracks in this genre you have played at least once.' },
  { key: 'skip_rate',           label: 'Skip rate',      default: true,  hint: 'Fraction of plays in this genre that ended in a skip.' },
  { key: 'total_skips',         label: 'Skips',          default: false, hint: 'Raw skip count for this genre.' },
  { key: 'has_favorite',        label: 'Fav',            default: true,  hint: 'You have at least one favorited artist in this genre.' },
]

const GENRE_DEFAULT_VISIBLE = new Set(
  GENRE_COLUMNS.filter(c => c.always || c.default).map(c => c.key)
)

const GENRE_COLS_STORAGE_KEY = 'jellydj_insights_genre_cols_v1'

function loadSavedGenreCols() {
  try {
    const raw = localStorage.getItem(GENRE_COLS_STORAGE_KEY)
    if (!raw) return GENRE_DEFAULT_VISIBLE
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) return GENRE_DEFAULT_VISIBLE
    const valid = new Set(GENRE_COLUMNS.map(c => c.key))
    const filtered = parsed.filter(k => valid.has(k))
    GENRE_COLUMNS.filter(c => c.always).forEach(c => filtered.push(c.key))
    return new Set(filtered)
  } catch {
    return GENRE_DEFAULT_VISIBLE
  }
}

function saveGenreCols(colSet) {
  try {
    localStorage.setItem(GENRE_COLS_STORAGE_KEY, JSON.stringify([...colSet]))
  } catch {}
}

function GenreColumnPicker({ visible, onChange }) {
  const [open, setOpen] = useState(false)
  const toggleable = GENRE_COLUMNS.filter(c => !c.always)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-overlay)] border border-[var(--border)]
                   rounded-lg text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
      >
        <Activity size={12} />
        Columns
        <ChevronDown size={10} className={open ? 'rotate-180' : ''} style={{ transition: 'transform 0.15s' }} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-[var(--bg-surface)] border border-[var(--border)]
                        rounded-xl shadow-xl p-3 min-w-[220px] space-y-1">
          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-2 px-1">Toggle columns</div>
          {toggleable.map(col => (
            <label key={col.key}
              className="flex items-start gap-2 px-1 py-1 rounded hover:bg-[var(--bg-overlay)] cursor-pointer"
              title={col.hint}>
              <input
                type="checkbox"
                checked={visible.has(col.key)}
                onChange={() => {
                  const next = new Set(visible)
                  next.has(col.key) ? next.delete(col.key) : next.add(col.key)
                  onChange(next)
                }}
                className="mt-0.5 accent-[var(--accent)]"
              />
              <div>
                <div className="text-xs text-[var(--text-primary)]">{col.label}</div>
                {col.hint && <div className="text-[10px] text-[var(--text-secondary)] leading-tight mt-0.5">{col.hint}</div>}
              </div>
            </label>
          ))}
          <button
            onClick={() => onChange(GENRE_DEFAULT_VISIBLE)}
            className="mt-2 w-full text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] py-1 border-t border-[var(--bg-overlay)] transition-colors"
          >
            Reset to default
          </button>
        </div>
      )}
    </div>
  )
}

// ── Genre Table ───────────────────────────────────────────────────────────────

function GenreTable({ userId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState('affinity_score')
  const [order, setOrder] = useState('desc')
  const [searchInput, setSearchInput] = useState('')
  const [searchFilter, setSearchFilter] = useState('')
  const [visibleCols, setVisibleCols] = useState(() => loadSavedGenreCols())

  const gcol = (key) => visibleCols.has(key)

  const doFetch = (uid, sb, ord, sf) => {
    if (!uid) return
    setLoading(true)
    const params = new URLSearchParams({ user_id: uid, sort_by: sb, order: ord })
    if (sf) params.set('search_filter', sf)
    api.get(`/api/insights/genres?${params}`)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    doFetch(userId, sort, order, searchFilter)
  }, [userId, sort, order, searchFilter])

  const handleSearch = (e) => {
    e.preventDefault()
    setSearchFilter(searchInput)
  }

  const clearSearch = () => {
    setSearchFilter('')
    setSearchInput('')
  }

  const handleSort = (field) => {
    const newOrder = sort === field ? (order === 'desc' ? 'asc' : 'desc') : 'desc'
    setSort(field)
    setOrder(newOrder)
  }

  return (
    <div>
      <div className="flex flex-wrap justify-between items-center gap-2 mb-4">
        {data && <span className="text-xs text-[var(--text-secondary)]">{data.length} genres tracked</span>}
        <div className="flex items-center gap-2 ml-auto flex-wrap">
          <form onSubmit={handleSearch} className="flex items-center gap-1">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--text-secondary)]" />
              <input
                type="text"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                placeholder="Search genre…"
                className="pl-6 pr-2 py-1.5 text-xs bg-[var(--bg-overlay)] border border-[var(--border)]
                           rounded-lg text-[var(--text-primary)] placeholder-[var(--text-secondary)]
                           focus:outline-none focus:border-[var(--accent)] w-36"
              />
            </div>
            <button type="submit"
              className="px-2 py-1.5 text-xs bg-[var(--accent)] text-white rounded-lg hover:opacity-90 transition-opacity">
              Find
            </button>
            {searchFilter && (
              <button type="button" onClick={clearSearch}
                className="p-1.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                title="Clear search">
                <X size={12} />
              </button>
            )}
          </form>
          <GenreColumnPicker visible={visibleCols} onChange={cols => { setVisibleCols(cols); saveGenreCols(cols) }} />
        </div>
      </div>
      {searchFilter && (
        <div className="mb-3 text-xs text-[var(--text-secondary)]">
          Showing results for <span className="text-[var(--text-primary)] font-medium">"{searchFilter}"</span>
          {data && ` — ${data.length} match${data.length !== 1 ? 'es' : ''}`}
        </div>
      )}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--bg-overlay)]">
                <th className="px-4 py-2.5 w-8 text-[10px] text-[var(--text-secondary)]">#</th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Genre" field="genre" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Affinity" field="affinity_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                    hint="Genre affinity score (0–100) built from play frequency, recency, and favorites." />
                </th>
                {gcol('normalized_affinity') && (
                  <th className="px-2 py-2.5 hidden sm:table-cell">
                    <SortHeader label="Norm. affinity" field="normalized_affinity" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Affinity normalized relative to your top genre. Your #1 genre = 100. This is what playlist blocks actually use." />
                  </th>
                )}
                {gcol('trend_direction') && (
                  <th className="px-2 py-2.5 hidden sm:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Trend</th>
                )}
                {gcol('recent_plays') && (
                  <th className="px-2 py-2.5 hidden sm:table-cell">
                    <SortHeader label="Recent (30d)" field="recent_plays" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Tracks played in this genre in the last 30 days." />
                  </th>
                )}
                {gcol('total_plays') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <SortHeader label="Total plays" field="total_plays" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {gcol('total_tracks_played') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <SortHeader label="Tracks" field="total_tracks_played" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Unique tracks played in this genre." />
                  </th>
                )}
                {gcol('skip_rate') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Skip rate" field="skip_rate" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {gcol('total_skips') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Skips" field="total_skips" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {gcol('has_favorite') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Fav</th>
                )}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={10} className="px-4 py-8 text-center">
                  <Loader2 size={16} className="animate-spin text-[var(--text-secondary)] mx-auto" />
                </td></tr>
              )}
              {!loading && data?.length === 0 && (
                <tr><td colSpan={10} className="px-4 py-8 text-center text-xs text-[var(--text-secondary)]">
                  No genre data yet — play some music to build your taste profile.
                </td></tr>
              )}
              {!loading && data?.map((g, i) => (
                <tr key={g.genre}
                  className="border-b border-[var(--bg-overlay)] hover:bg-[var(--bg-surface)] transition-colors">
                  <td className="px-4 py-2.5 text-[10px] text-[var(--text-secondary)]">{i + 1}</td>
                  <td className="px-2 py-2.5">
                    <div className="flex items-center gap-1.5">
                      {g.has_favorite && <Heart size={10} className="text-[var(--danger)] flex-shrink-0" />}
                      <span className="text-xs font-medium text-[var(--text-primary)] capitalize">{g.genre}</span>
                    </div>
                  </td>
                  <td className="px-2 py-2.5">
                    <div className="flex items-center gap-2 min-w-[80px]">
                      <span className="text-xs font-bold tabular-nums"
                            style={{ color: g.affinity_score >= 60 ? 'var(--accent)' : g.affinity_score >= 30 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                        {g.affinity_score.toFixed(1)}
                      </span>
                      <ScoreBar value={g.affinity_score} color={g.affinity_score >= 60 ? 'var(--accent)' : 'var(--text-secondary)'} />
                    </div>
                  </td>
                  {gcol('normalized_affinity') && (
                    <td className="px-2 py-2.5 hidden sm:table-cell">
                      <div className="flex items-center gap-2 min-w-[80px]">
                        <span className="text-xs font-bold tabular-nums"
                              style={{ color: g.normalized_affinity >= 60 ? 'var(--accent)' : g.normalized_affinity >= 30 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {g.normalized_affinity.toFixed(1)}
                        </span>
                        <ScoreBar value={g.normalized_affinity} color={g.normalized_affinity >= 60 ? 'var(--accent)' : 'var(--text-secondary)'} />
                      </div>
                    </td>
                  )}
                  {gcol('trend_direction') && (
                    <td className="px-2 py-2.5 hidden sm:table-cell text-xs">
                      {g.trend_direction === 'rising'  && <span className="text-green-400 flex items-center gap-1"><TrendingUp size={11} /> Rising</span>}
                      {g.trend_direction === 'falling' && <span className="text-[var(--danger)] flex items-center gap-1"><TrendingDown size={11} /> Falling</span>}
                      {g.trend_direction === 'stable'  && <span className="text-[var(--text-secondary)]">Stable</span>}
                    </td>
                  )}
                  {gcol('recent_plays') && (
                    <td className="px-2 py-2.5 hidden sm:table-cell text-xs tabular-nums"
                        style={{ color: g.recent_plays > 0 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                      {g.recent_plays > 0 ? g.recent_plays : '—'}
                    </td>
                  )}
                  {gcol('total_plays') && (
                    <td className="px-2 py-2.5 hidden md:table-cell text-xs text-[var(--text-secondary)] tabular-nums">{g.total_plays}</td>
                  )}
                  {gcol('total_tracks_played') && (
                    <td className="px-2 py-2.5 hidden md:table-cell text-xs text-[var(--text-secondary)] tabular-nums">{g.total_tracks_played}</td>
                  )}
                  {gcol('skip_rate') && (
                    <td className="px-2 py-2.5 hidden lg:table-cell text-xs tabular-nums"
                        style={{ color: g.skip_rate > 0.3 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                      {g.skip_rate > 0 ? `${(g.skip_rate * 100).toFixed(0)}%` : '—'}
                    </td>
                  )}
                  {gcol('total_skips') && (
                    <td className="px-2 py-2.5 hidden lg:table-cell text-xs tabular-nums"
                        style={{ color: g.total_skips > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                      {g.total_skips > 0 ? g.total_skips : '—'}
                    </td>
                  )}
                  {gcol('has_favorite') && (
                    <td className="px-2 py-2.5 hidden lg:table-cell text-xs">
                      {g.has_favorite ? <Heart size={11} className="text-[var(--danger)]" /> : <span className="text-[var(--border)]">—</span>}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Artist Column definitions ─────────────────────────────────────────────────

const ARTIST_COLUMNS = [
  { key: 'artist_name',      label: 'Artist',       always: true,  hint: 'Artist name' },
  { key: 'affinity_score',   label: 'Affinity',     always: true,  hint: 'Overall affinity score (0–100) built from play frequency, recency, and favorites.' },
  { key: 'total_plays',      label: 'Total plays',  default: true,  hint: 'Total plays across all tracks for this artist' },
  { key: 'skip_rate',        label: 'Skip rate',    default: true,  hint: 'Skip rate from webhook events' },
  { key: 'total_skips',      label: 'Skips',        default: true,  hint: 'Raw skip count vs total events' },
  { key: 'replay_boost',     label: 'Replay ↑',     default: true,  hint: 'Artist-level replay boost from voluntary returns within 7 days.' },
  { key: 'popularity_score', label: 'Popularity',   default: true,  hint: 'Global Last.fm artist popularity.' },
  { key: 'primary_genre',    label: 'Genre',        default: true,  hint: 'Primary genre tag for this artist.' },
  { key: 'trend_direction',  label: 'Trend',        default: true,  hint: 'Whether your listening of this artist is rising, stable, or falling.' },
  { key: 'has_favorite',     label: 'Fav',          default: true,  hint: 'Whether you have a favorited track by this artist.' },
  { key: 'on_cooldown',      label: 'Timeout',      default: true,  hint: 'Artist is on a skip-triggered timeout and excluded from all playlists until the date shown.' },
]

const ARTIST_DEFAULT_VISIBLE = new Set(
  ARTIST_COLUMNS.filter(c => c.always || c.default).map(c => c.key)
)

const ARTIST_COLS_STORAGE_KEY = 'jellydj_insights_artist_cols_v2'

function loadSavedArtistCols() {
  try {
    const raw = localStorage.getItem(ARTIST_COLS_STORAGE_KEY)
    if (!raw) return ARTIST_DEFAULT_VISIBLE
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) return ARTIST_DEFAULT_VISIBLE
    const valid = new Set(ARTIST_COLUMNS.map(c => c.key))
    const filtered = parsed.filter(k => valid.has(k))
    ARTIST_COLUMNS.filter(c => c.always).forEach(c => filtered.push(c.key))
    return new Set(filtered)
  } catch {
    return ARTIST_DEFAULT_VISIBLE
  }
}

function saveArtistCols(colSet) {
  try {
    localStorage.setItem(ARTIST_COLS_STORAGE_KEY, JSON.stringify([...colSet]))
  } catch {}
}

function ArtistColumnPicker({ visible, onChange }) {
  const [open, setOpen] = useState(false)
  const toggleable = ARTIST_COLUMNS.filter(c => !c.always)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-overlay)] border border-[var(--border)]
                   rounded-lg text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
      >
        <Activity size={12} />
        Columns
        <ChevronDown size={10} className={open ? 'rotate-180' : ''} style={{ transition: 'transform 0.15s' }} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-[var(--bg-surface)] border border-[var(--border)]
                        rounded-xl shadow-xl p-3 min-w-[220px] space-y-1">
          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-2 px-1">Toggle columns</div>
          {toggleable.map(col => (
            <label key={col.key}
              className="flex items-start gap-2 px-1 py-1 rounded hover:bg-[var(--bg-overlay)] cursor-pointer"
              title={col.hint}>
              <input
                type="checkbox"
                checked={visible.has(col.key)}
                onChange={() => {
                  const next = new Set(visible)
                  next.has(col.key) ? next.delete(col.key) : next.add(col.key)
                  onChange(next)
                }}
                className="mt-0.5 accent-[var(--accent)]"
              />
              <div>
                <div className="text-xs text-[var(--text-primary)]">{col.label}</div>
                {col.hint && <div className="text-[10px] text-[var(--text-secondary)] leading-tight mt-0.5">{col.hint}</div>}
              </div>
            </label>
          ))}
          <button
            onClick={() => onChange(ARTIST_DEFAULT_VISIBLE)}
            className="mt-2 w-full text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] py-1 border-t border-[var(--bg-overlay)] transition-colors"
          >
            Reset to default
          </button>
        </div>
      )}
    </div>
  )
}

// ── Artist Table ──────────────────────────────────────────────────────────────

function ArtistTable({ userId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState('affinity_score')
  const [order, setOrder] = useState('desc')
  const [page, setPage] = useState(1)
  const [expandedRow, setExpandedRow] = useState(null)
  const [visibleCols, setVisibleCols] = useState(() => loadSavedArtistCols())
  const [searchInput, setSearchInput] = useState('')
  const [searchFilter, setSearchFilter] = useState('')

  const { buildItemUrl, buildSearchUrl } = useJellyfinUrl()

  const acol = (key) => visibleCols.has(key)

  const doFetch = (uid, sb, ord, pg, sf) => {
    if (!uid) return
    setLoading(true)
    const params = new URLSearchParams({ user_id: uid, sort_by: sb, order: ord, page: pg, page_size: 50 })
    if (sf) params.set('search_filter', sf)
    api.get(`/api/insights/artists?${params}`)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    doFetch(userId, sort, order, page, searchFilter)
  }, [userId, sort, order, page, searchFilter])

  const handleSearch = (e) => {
    e.preventDefault()
    setSearchFilter(searchInput)
    setPage(1)
  }

  const clearSearch = () => {
    setSearchFilter('')
    setSearchInput('')
    setPage(1)
  }

  const handleSort = (field) => {
    const newOrder = sort === field ? (order === 'desc' ? 'asc' : 'desc') : 'desc'
    setSort(field)
    setOrder(newOrder)
    setPage(1)
  }

  return (
    <div>
      <div className="flex flex-wrap justify-between items-center gap-2 mb-4">
        {data && <span className="text-xs text-[var(--text-secondary)]">{data.total} artists in library</span>}
        <div className="flex items-center gap-2 ml-auto flex-wrap">
          {/* Artist search */}
          <form onSubmit={handleSearch} className="flex items-center gap-1">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--text-secondary)]" />
              <input
                type="text"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                placeholder="Search artist…"
                className="pl-6 pr-2 py-1.5 text-xs bg-[var(--bg-overlay)] border border-[var(--border)]
                           rounded-lg text-[var(--text-primary)] placeholder-[var(--text-secondary)]
                           focus:outline-none focus:border-[var(--accent)] w-36"
              />
            </div>
            <button type="submit"
              className="px-2 py-1.5 text-xs bg-[var(--accent)] text-white rounded-lg hover:opacity-90 transition-opacity">
              Find
            </button>
            {searchFilter && (
              <button type="button" onClick={clearSearch}
                className="p-1.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                title="Clear search">
                <X size={12} />
              </button>
            )}
          </form>
          <ArtistColumnPicker visible={visibleCols} onChange={cols => { setVisibleCols(cols); saveArtistCols(cols) }} />
        </div>
      </div>
      {searchFilter && (
        <div className="mb-3 text-xs text-[var(--text-secondary)]">
          Showing results for <span className="text-[var(--text-primary)] font-medium">"{searchFilter}"</span>
          {data && ` — ${data.total} match${data.total !== 1 ? 'es' : ''}`}
        </div>
      )}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[var(--bg-overlay)]">
                <th className="px-4 py-2.5 w-8 text-[10px] text-[var(--text-secondary)]">#</th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Artist" field="artist_name" currentSort={sort} currentOrder={order} onSort={handleSort} />
                </th>
                <th className="px-2 py-2.5">
                  <SortHeader label="Affinity" field="affinity_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                    hint="Overall affinity score (0–100) built from play frequency, recency, and favorites." />
                </th>
                {acol('total_plays') && (
                  <th className="px-2 py-2.5 hidden sm:table-cell">
                    <SortHeader label="Total plays" field="total_plays" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {acol('skip_rate') && (
                  <th className="px-2 py-2.5 hidden md:table-cell">
                    <SortHeader label="Skip rate" field="skip_rate" currentSort={sort} currentOrder={order} onSort={handleSort} />
                  </th>
                )}
                {acol('total_skips') && (
                  <th className="px-2 py-2.5 hidden md:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">
                    Skips
                  </th>
                )}
                {acol('replay_boost') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Replay ↑" field="replay_boost" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Artist-level replay boost from voluntary returns within 7 days." />
                  </th>
                )}
                {acol('popularity_score') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell">
                    <SortHeader label="Popularity" field="popularity_score" currentSort={sort} currentOrder={order} onSort={handleSort}
                      hint="Global Last.fm artist popularity." />
                  </th>
                )}
                {acol('primary_genre') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Genre</th>
                )}
                {acol('trend_direction') && (
                  <th className="px-2 py-2.5 hidden xl:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Trend</th>
                )}
                {acol('has_favorite') && (
                  <th className="px-2 py-2.5 hidden lg:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">Fav</th>
                )}
                {acol('on_cooldown') && (
                  <th className="px-2 py-2.5 hidden md:table-cell text-[10px] text-[var(--text-secondary)] uppercase tracking-wider"
                    title="Artist skip timeout — excluded from all playlists while active">
                    Timeout
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={11} className="px-4 py-8 text-center">
                  <Loader2 size={16} className="animate-spin text-[var(--text-secondary)] mx-auto" />
                </td></tr>
              )}
              {!loading && data?.artists?.map((a, i) => (
                <>
                  <tr
                    key={a.artist_name}
                    onClick={() => setExpandedRow(expandedRow === a.artist_name ? null : a.artist_name)}
                    className={`border-b border-[var(--bg-overlay)] hover:bg-[var(--bg-surface)] cursor-pointer transition-colors${a._no_profile ? ' opacity-75' : ''}`}
                  >
                    <td className="px-4 py-2.5 text-[10px] text-[var(--text-secondary)]">{(page - 1) * 50 + i + 1}</td>
                    <td className="px-2 py-2.5">
                      <div className="flex items-center gap-1.5">
                        {a.has_favorite && <Heart size={10} className="text-[var(--danger)] flex-shrink-0" />}
                        <span className="text-xs font-medium text-[var(--text-primary)]">{a.artist_name}</span>
                        {(a.replay_boost || 0) > 0 && <Zap size={9} className="text-yellow-400 flex-shrink-0" title="Replay boost active" />}
                        {a._no_profile && <span className="text-[9px] px-1 py-0.5 rounded bg-yellow-900/40 text-yellow-400 font-medium leading-none flex-shrink-0" title="No listen history — affinity score will build after plays are recorded.">not tracked</span>}
                      </div>
                    </td>
                    <td className="px-2 py-2.5">
                      <div className="flex items-center gap-2 min-w-[80px]">
                        <span className="text-xs font-bold tabular-nums"
                              style={{ color: a.affinity_score >= 60 ? 'var(--accent)' : a.affinity_score >= 30 ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {a.affinity_score.toFixed(1)}
                        </span>
                        <ScoreBar value={a.affinity_score} color={a.affinity_score >= 60 ? 'var(--accent)' : 'var(--text-secondary)'} />
                      </div>
                    </td>
                    {acol('total_plays') && (
                      <td className="px-2 py-2.5 hidden sm:table-cell text-xs text-[var(--text-secondary)] tabular-nums">{a.total_plays}</td>
                    )}
                    {acol('skip_rate') && (
                      <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                          style={{ color: a.skip_rate > 0.3 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                        {a.skip_rate > 0 ? `${(a.skip_rate * 100).toFixed(0)}%` : '—'}
                      </td>
                    )}
                    {acol('total_skips') && (
                      <td className="px-2 py-2.5 hidden md:table-cell text-xs tabular-nums"
                          style={{ color: a.total_skips > 0 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                        {a.total_skips > 0
                          ? <span title={`${a.total_skips} skips / ${a.total_events} events`}>
                              {a.total_skips}<span className="text-[var(--text-secondary)] text-[10px]">/{a.total_events}</span>
                            </span>
                          : '—'}
                      </td>
                    )}
                    {acol('replay_boost') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs tabular-nums"
                          style={{ color: (a.replay_boost || 0) > 0 ? '#e3b341' : 'var(--text-secondary)' }}>
                        {(a.replay_boost || 0) > 0 ? `+${a.replay_boost.toFixed(1)}` : '—'}
                      </td>
                    )}
                    {acol('popularity_score') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell min-w-[70px]">
                        {a.popularity_score != null
                          ? <PopularityBar value={a.popularity_score} />
                          : <span className="text-[var(--text-secondary)] text-xs">—</span>}
                      </td>
                    )}
                    {acol('primary_genre') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs text-[var(--text-secondary)] truncate max-w-[120px]">
                        {a.primary_genre || '—'}
                      </td>
                    )}
                    {acol('trend_direction') && (
                      <td className="px-2 py-2.5 hidden xl:table-cell text-xs">
                        {a.trend_direction === 'rising'  && <span className="text-green-400 flex items-center gap-1"><TrendingUp size={11} /> Rising</span>}
                        {a.trend_direction === 'falling' && <span className="text-[var(--danger)] flex items-center gap-1"><TrendingDown size={11} /> Falling</span>}
                        {a.trend_direction === 'stable'  && <span className="text-[var(--text-secondary)]">Stable</span>}
                        {!a.trend_direction && <span className="text-[var(--border)]">—</span>}
                      </td>
                    )}
                    {acol('has_favorite') && (
                      <td className="px-2 py-2.5 hidden lg:table-cell text-xs">
                        {a.has_favorite ? <Heart size={11} className="text-[var(--danger)]" /> : <span className="text-[var(--border)]">—</span>}
                      </td>
                    )}
                    {acol('on_cooldown') && (
                      <td className="px-2 py-2.5 hidden md:table-cell text-xs">
                        {a.on_cooldown
                          ? <span className="flex items-center gap-1 text-orange-400"
                              title={`Timed out until ${fmtDate(a.cooldown_until)} — skipped too many songs recently`}>
                              <Clock size={10} />
                              {a.cooldown_until ? new Date(a.cooldown_until + 'Z').toLocaleDateString() : 'Active'}
                            </span>
                          : <span className="text-[var(--border)]">—</span>}
                      </td>
                    )}
                  </tr>

                  {/* Artist expanded row */}
                  {expandedRow === a.artist_name && (
                    <tr key={`${a.artist_name}-exp`} className="bg-[var(--bg-surface)] border-b border-[var(--bg-overlay)]">
                      <td colSpan={20} className="px-4 py-4">
                        <div className="space-y-3">
                          <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                            Artist profile — {a.artist_name}
                          </div>
                          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                            <StatPill label="Affinity score" value={`${a.affinity_score.toFixed(2)} / 100`}
                              color={a.affinity_score >= 60 ? 'text-[var(--accent)]' : 'text-[var(--text-primary)]'} />
                            <StatPill label="Total plays" value={a.total_plays.toLocaleString()} />
                            <StatPill label="Tracks played" value={a.total_tracks_played} />
                            <StatPill label="Live skip rate"
                              value={a.skip_rate > 0 ? `${(a.skip_rate * 100).toFixed(0)}%` : 'None'}
                              color={a.skip_rate > 0.3 ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'} />
                            <StatPill label="Replay boost"
                              value={(a.replay_boost || 0) > 0 ? `+${a.replay_boost.toFixed(2)}` : 'None'}
                              color={(a.replay_boost || 0) > 0 ? 'text-yellow-400' : 'text-[var(--text-secondary)]'} />
                            <StatPill label="Global popularity"
                              value={a.popularity_score != null ? `${a.popularity_score.toFixed(0)} / 100` : 'Not enriched'}
                              color={a.popularity_score >= 70 ? 'text-green-400' : 'text-[var(--text-secondary)]'} />
                            <StatPill label="Trend"
                              value={a.trend_direction || 'Unknown'}
                              color={a.trend_direction === 'rising' ? 'text-green-400' : a.trend_direction === 'falling' ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'} />
                            <StatPill label="Primary genre" value={a.primary_genre || '—'} />
                            {a.on_cooldown && (
                              <StatPill label="Skip timeout"
                                value={`Until ${a.cooldown_until ? new Date(a.cooldown_until + 'Z').toLocaleDateString() : '?'} (cycle #${a.cooldown_count})`}
                                color="text-orange-400" />
                            )}
                          </div>
                          {a._no_profile && (
                            <div className="flex items-start gap-2 p-2.5 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-xs text-yellow-300">
                              <span className="flex-shrink-0 mt-0.5">⚠</span>
                              <div>
                                <span className="font-medium">Not yet tracked</span> — no listen history recorded for this artist.
                                Their affinity score will build automatically as you play or skip their songs.
                                {a.total_skips > 0 && " Skip penalties from this session are already active."}
                              </div>
                            </div>
                          )}
                          {a.on_cooldown && (
                            <div className="flex items-start gap-2 p-2.5 rounded-lg bg-orange-500/10 border border-orange-500/20 text-xs text-orange-300">
                              <Clock size={13} className="flex-shrink-0 mt-0.5" />
                              <div>
                                <span className="font-medium">Artist timed out</span> — excluded from all playlists until {fmtDate(a.cooldown_until)}.
                                This was triggered by skipping too many songs by this artist in a short period.
                                Timeout cycle #{a.cooldown_count} (escalating: 7d → 14d → 30d).
                              </div>
                            </div>
                          )}

                          {a.tags && a.tags.length > 0 && (
                            <div>
                              <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-1.5">Last.fm tags</div>
                              <div className="flex flex-wrap gap-1">
                                {a.tags.slice(0, 12).map(tag => (
                                  <span key={tag} className="px-1.5 py-0.5 rounded bg-[var(--bg-overlay)] text-[10px] text-[var(--text-secondary)]">
                                    {tag}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                          {a.related_artists && a.related_artists.length > 0 && (
                            <div>
                              <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-1.5">
                                Similar artists (Last.fm) — used for New For You
                              </div>
                              <div className="flex flex-wrap gap-1.5">
                                {a.related_artists.slice(0, 10).map(rel => (
                                  <span key={rel.name || rel}
                                    className="px-2 py-0.5 rounded-full bg-[var(--bg-overlay)] text-[10px] text-[var(--text-primary)]"
                                    title={rel.match != null ? `Similarity: ${(rel.match * 100).toFixed(0)}%` : undefined}>
                                    {rel.name || rel}
                                    {rel.match != null && (
                                      <span className="text-[var(--text-secondary)] ml-1">{(rel.match * 100).toFixed(0)}%</span>
                                    )}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* ── Jellyfin artist link ── */}
                          <JellyfinArtistLink
                            artistName={a.artist_name}
                            jellyfinArtistId={a.jellyfin_artist_id}
                            buildItemUrl={buildItemUrl}
                            buildSearchUrl={buildSearchUrl}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.pages > 1 && (
          <div className="flex items-center justify-center gap-2 p-3 border-t border-[var(--bg-overlay)]">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              ← Prev
            </button>
            <span className="text-xs text-[var(--text-secondary)]">{page} / {data.pages}</span>
            <button onClick={() => setPage(p => Math.min(data.pages, p + 1))} disabled={page === data.pages}
              className="px-3 py-1 text-xs bg-[var(--bg-overlay)] border border-[var(--border)] rounded-lg
                         text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors">
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Holiday Table ─────────────────────────────────────────────────────────────
const SEASON_LABELS = {
  christmas:    { label: 'Christmas',    window: 'Nov 25 – Jan 5',  emoji: '🎄' },
  hanukkah:     { label: 'Hanukkah',     window: 'Dec 1 – Jan 5',   emoji: '🕎' },
  halloween:    { label: 'Halloween',    window: 'Oct 1 – Nov 5',   emoji: '🎃' },
  thanksgiving: { label: 'Thanksgiving', window: 'Nov 1 – Nov 30',  emoji: '🦃' },
  easter:       { label: 'Easter',       window: 'Mar 15 – Apr 30', emoji: '🐣' },
  valentines:   { label: "Valentine's",  window: 'Feb 1 – Feb 20',  emoji: '💝' },
  new_year:     { label: 'New Year',     window: 'Dec 26 – Jan 10', emoji: '🎆' },
}

function HolidayTable() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    api.get('/api/insights/holiday')
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [])

  if (loading) return <div className="flex items-center gap-2 p-8 text-[var(--text-secondary)]"><Loader2 size={16} className="animate-spin" /> Loading holiday data...</div>
  if (error)   return <div className="flex items-center gap-2 p-8 text-[var(--danger)]"><AlertCircle size={16} /> {error}</div>
  if (!data)   return null

  const { summary, tracks, season_status } = data

  return (
    <div className="space-y-6 pt-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Tagged tracks',       val: summary.total_tagged,        color: 'text-[var(--text-primary)]' },
          { label: 'Currently excluded',  val: summary.currently_excluded,  color: 'text-[var(--danger)]' },
          { label: 'In season now',       val: summary.currently_included,  color: 'text-green-400' },
          { label: 'Holidays detected',   val: Object.keys(summary.by_holiday || {}).length, color: 'text-[var(--text-primary)]' },
        ].map(({ label, val, color }) => (
          <div key={label} className="bg-[var(--bg-surface)] rounded-lg p-3 border border-[var(--bg-overlay)]">
            <div className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-1">{label}</div>
            <div className={`text-2xl font-bold ${color}`}>{val}</div>
          </div>
        ))}
      </div>

      <div>
        <div className="text-[11px] text-[var(--text-secondary)] uppercase tracking-wider mb-2 font-semibold">Season windows</div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(SEASON_LABELS).map(([slug, { label, window: win, emoji }]) => {
            const active = season_status && season_status[slug]
            const count  = summary.by_holiday && summary.by_holiday[slug] || 0
            return (
              <div key={slug} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs border
                ${active ? 'bg-green-900/30 border-green-700/50 text-green-300' : 'bg-[var(--bg-surface)] border-[var(--bg-overlay)] text-[var(--text-secondary)]'}`}>
                <span>{emoji}</span>
                <span className="font-medium">{label}</span>
                <span className="opacity-50 hidden sm:inline">{win}</span>
                {count > 0 && <span className={`font-bold ${active ? 'text-green-200' : ''}`}>{count}</span>}
                {active && <span className="text-green-400 font-bold text-[10px]">● IN SEASON</span>}
              </div>
            )
          })}
        </div>
      </div>

      {!summary.by_holiday || Object.keys(summary.by_holiday).length === 0 ? (
        <div className="p-10 text-center text-[var(--text-secondary)] bg-[var(--bg-surface)] rounded-lg border border-[var(--bg-overlay)]">
          <Snowflake size={32} className="mx-auto mb-3 opacity-30" />
          <div className="font-medium">No holiday tracks detected yet</div>
          <div className="text-xs mt-1 opacity-60">Run a library scan — holiday songs are tagged automatically</div>
        </div>
      ) : (
        <div className="space-y-3">
          {Object.entries(summary.by_holiday).map(([slug, count]) => {
            const meta = SEASON_LABELS[slug] || { label: slug, emoji: '🎵', window: '' }
            const active = season_status && season_status[slug]
            const slugTracks = (tracks || []).filter(t => t.holiday_tag === slug)
            const isOpen = expanded === slug
            return (
              <div key={slug} className="bg-[var(--bg-surface)] rounded-lg border border-[var(--bg-overlay)] overflow-hidden">
                <button onClick={() => setExpanded(isOpen ? null : slug)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-[var(--bg-overlay)] transition-colors text-left">
                  <div className="flex items-center gap-3">
                    <span className="text-xl">{meta.emoji}</span>
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-sm text-[var(--text-primary)]">{meta.label}</span>
                        {active
                          ? <span className="text-[10px] font-bold text-green-400 bg-green-900/30 px-1.5 py-0.5 rounded">IN SEASON</span>
                          : <span className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-overlay)] px-1.5 py-0.5 rounded">out of season</span>}
                      </div>
                      <div className="text-xs text-[var(--text-secondary)] mt-0.5">{count} track{count !== 1 ? 's' : ''} · {meta.window}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {!active && count > 0 && <span className="text-[10px] text-[var(--danger)] opacity-70 hidden sm:inline">excluded from playlists</span>}
                    {isOpen ? <ChevronUp size={14} className="text-[var(--text-secondary)]" /> : <ChevronDown size={14} className="text-[var(--text-secondary)]" />}
                  </div>
                </button>
                {isOpen && (
                  <div className="border-t border-[var(--bg-overlay)]">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-[var(--bg-overlay)] bg-[var(--bg-overlay)]/30">
                          <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">Track</th>
                          <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-[var(--text-secondary)] hidden sm:table-cell">Artist</th>
                          <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-[var(--text-secondary)] hidden md:table-cell">Album</th>
                          <th className="px-4 py-2 text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {slugTracks.slice(0, 200).map(t => (
                          <tr key={t.jellyfin_item_id} className="border-b border-[var(--bg-overlay)] last:border-0 hover:bg-[var(--bg-overlay)]/20">
                            <td className="px-4 py-2 text-xs text-[var(--text-primary)] max-w-[180px] truncate">{t.track_name}</td>
                            <td className="px-4 py-2 text-xs text-[var(--text-secondary)] hidden sm:table-cell max-w-[140px] truncate">{t.artist_name}</td>
                            <td className="px-4 py-2 text-xs text-[var(--text-secondary)] hidden md:table-cell max-w-[160px] truncate">{t.album_name}</td>
                            <td className="px-4 py-2">
                              {t.holiday_exclude
                                ? <span className="text-[10px] text-[var(--danger)] font-medium">✗ excluded</span>
                                : <span className="text-[10px] text-green-400 font-medium">✓ in season</span>}
                            </td>
                          </tr>
                        ))}
                        {slugTracks.length > 200 && (
                          <tr><td colSpan={4} className="px-4 py-2 text-xs text-[var(--text-secondary)] italic">…and {slugTracks.length - 200} more</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Insights() {
  const { isAdmin, user } = useAuth()
  const [users, setUsers] = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [summary, setSummary] = useState(null)
  const [tab, setTab] = useState('tracks')

  useEffect(() => {
    if (!isAdmin) {
      if (user?.user_id) setSelectedUser(user.user_id)
      return
    }
    api.get('/api/insights/users')
      .then(data => {
        setUsers(data)
        if (data.length > 0) setSelectedUser(data[0].jellyfin_user_id)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!selectedUser) return
    setSummary(null)
    api.get(`/api/insights/summary?user_id=${selectedUser}`)
      .then(setSummary).catch(() => {})
  }, [selectedUser])

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>Insights</h1>
          <p className="text-sm text-[var(--text-secondary)] mt-1">Audit what JellyDJ knows about your taste</p>
        </div>

        {isAdmin && users.length > 1 && (
          <div className="flex bg-[var(--bg-overlay)] rounded-lg border border-[var(--border)] overflow-hidden">
            {users.map(u => (
              <button key={u.jellyfin_user_id}
                onClick={() => setSelectedUser(u.jellyfin_user_id)}
                className={`px-4 py-2 text-xs font-medium transition-colors
                  ${selectedUser === u.jellyfin_user_id
                    ? 'bg-[var(--accent)] text-[var(--bg)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
                {u.username}
              </button>
            ))}
          </div>
        )}
      </div>

      {!selectedUser ? (
        <div className="card flex flex-col items-center justify-center py-12 text-center gap-2">
          <AlertCircle size={24} className="text-[var(--border)]" />
          <div className="text-sm text-[var(--text-secondary)]">No indexed users found</div>
          <div className="text-xs text-[var(--text-secondary)]/60">Run a full scan from the Dashboard first</div>
        </div>
      ) : (
        <>
          <SummarySection summary={summary} />

          <div className="flex gap-1 bg-[var(--bg-surface)] rounded-lg p-1 w-fit border border-[var(--bg-overlay)]">
            {[
              { key: 'tracks',   label: 'Tracks',   icon: Music2 },
              { key: 'artists',  label: 'Artists',  icon: Mic2 },
              { key: 'genres',   label: 'Genres',   icon: Tag },
              { key: 'holiday',  label: 'Holiday',  icon: Snowflake },
              { key: 'universe', label: 'Universe', icon: Globe },
            ].map(({ key, label, icon: Icon }) => (
              <button key={key} onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all
                  ${tab === key
                    ? 'bg-[var(--bg-overlay)] text-[var(--text-primary)] shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
                <Icon size={12} />
                {label}
              </button>
            ))}
          </div>

          {tab === 'tracks'   && <TrackTable userId={selectedUser} key={selectedUser} />}
          {tab === 'artists'  && <ArtistTable userId={selectedUser} key={selectedUser} />}
          {tab === 'genres'   && <GenreTable userId={selectedUser} key={selectedUser} />}
          {tab === 'holiday'  && <HolidayTable />}
          {tab === 'universe' && (
            <div className="card" style={{ height: 680, padding: 0, overflow: 'hidden', borderRadius: '0.75rem' }}>
              <MusicUniverseMap userId={selectedUser} key={selectedUser} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
