/**
 * filterTypes.js — catalog of playlist-block filter types and their defaults.
 *
 * Each entry:
 *   - label, icon, color: UI presentation
 *   - oneliner: short description for the picker
 *   - desc: long description rendered in the editor (strings + tier objects)
 *
 * To add a new filter:
 *   1. Add an entry here in FILTER_TYPES
 *   2. Add defaults in DEFAULT_PARAMS
 *   3. Add a param editor component in BlockEditor's Editors{} map
 *   4. Implement the executor in backend/services/playlist_blocks.py
 */

import {
  Sparkles, Radio, TrendingUp, Clock, Globe, Star, Users, Tag,
  Shuffle, Zap, Wind, BarChart2, SkipForward, Repeat, Heart, Activity, Music2,
} from 'lucide-react'

export const FILTER_TYPES = {
  final_score: {
    label: 'Final Score', icon: Sparkles, color: 'var(--accent)',
    oneliner: 'Your personal blended score for every track (0–99)',
    desc: [
      "Every track has a personal score combining: how often you play it, how recently you played it, how much you skip it, whether you've favourited it, and its global streaming popularity.",
      'Use the range slider to target any band you want — your absolute best tracks, your guilty pleasures, tracks you\'ve never touched.',
      { '95–99': 'Favourites & top tracks' },
      { '87–94': 'Heavily played' },
      { '77–86': 'Frequently played' },
      { '63–76': 'Regularly played' },
      { '46–62': 'Occasionally played' },
      { '38–45': 'Barely liked' },
      { '0–37':  'Unplayed, buried, or heavily skipped' },
    ],
  },
  play_recency: {
    label: 'Play Recency', icon: Clock, color: '#fbbf24',
    oneliner: 'Filter by how long ago you last played a track',
    desc: [
      '"Within last N days" keeps tracks you\'ve played recently. "More than N days ago" surfaces tracks you\'ve been neglecting.',
      'Pairs naturally with a Final Score AND child to get recently-played tracks that are also highly rated.',
    ],
  },
  genre: {
    label: 'Genre', icon: Tag, color: '#34d399',
    oneliner: 'Match tracks by genre — leave empty for all genres',
    desc: [
      'Filters to tracks whose genre tag matches your selected list. Leave the list empty to pass all genres through.',
      'Genres come from your Jellyfin library metadata.',
    ],
  },
  artist: {
    label: 'Artist', icon: Users, color: '#fb923c',
    oneliner: 'Match tracks by artist — leave empty for all artists',
    desc: [
      'Filters to tracks from your selected artists. Leave empty to include everyone.',
      'Combine with an Artist Cap AND child to keep any one artist from dominating.',
    ],
  },
  play_count: {
    label: 'Play Count', icon: TrendingUp, color: '#f87171',
    oneliner: 'Filter by lifetime play count',
    desc: [
      'Matches tracks whose total play count is within your min–max range. Set max low (e.g. 0–3) to surface rarely-played tracks.',
    ],
  },
  discovery: {
    label: 'Discovery', icon: Radio, color: '#f472b6',
    oneliner: 'Mix unheard tracks by how familiar you are with the artist',
    desc: [
      'Buckets unplayed tracks by artist familiarity: Strangers, Acquaintances, and Familiar. Takes a proportional slice from each.',
      'Crank up Stranger % for maximum exploration. Lean on Familiar % for safe discovery.',
    ],
  },
  global_popularity: {
    label: 'Global Popularity', icon: Globe, color: '#60a5fa',
    oneliner: 'Filter by worldwide streaming popularity (0 = obscure, 100 = massive)',
    desc: [
      'Scores tracks 0–100 based on aggregated listener and play data from Last.fm and Spotify.',
      'Combine with a high Final Score range to find hidden gems your taste profile loves.',
    ],
  },
  affinity: {
    label: 'Affinity Range', icon: Star, color: '#a78bfa',
    oneliner: 'Filter by your artist + genre taste alignment score',
    desc: [
      'Affinity (0–100) measures how well a track matches your taste at the artist and genre level — independent of how many times you\'ve played that specific track.',
    ],
  },
  favorites: {
    label: 'Favorites Only', icon: Star, color: '#fde68a',
    oneliner: "Only tracks you've explicitly marked as favourites",
    desc: [
      'A pure pass-through filter — only tracks with a Jellyfin favourite flag pass through. No parameters needed.',
    ],
  },
  favorite_artists: {
    label: 'Favorite Artists', icon: Heart, color: '#fb7185',
    oneliner: "All tracks from artists you've favourited at least one song by",
    desc: [
      "Surfaces every track from any artist where you've marked at least one song as a Jellyfin favourite — not just the favourited tracks themselves.",
      'Pair with Played Status (unplayed) to discover new tracks from artists you already love.',
    ],
  },
  favorite_genres: {
    label: 'Favorite Genres', icon: Heart, color: '#c084fc',
    oneliner: "All tracks in genres where you have at least one favourited track",
    desc: [
      "Passes through tracks whose genre contains at least one of your Jellyfin-favourited songs — a broad genre-level affinity signal.",
      'Pair with Played Status (unplayed) or a Final Score floor to surface the best unheard tracks in genres you clearly love.',
    ],
  },
  played_status: {
    label: 'Played Status', icon: TrendingUp, color: '#94a3b8',
    oneliner: 'Narrow to played or unplayed tracks only',
    desc: [
      'A simple pass-through filter. "Played" keeps only tracks you\'ve heard at least once. "Unplayed" keeps only tracks you\'ve never played.',
    ],
  },
  cooldown: {
    label: 'Cooldown Filter', icon: Clock, color: '#f87171',
    oneliner: "Exclude tracks you've been skipping (active skip-cooldown)",
    desc: [
      "Removes tracks that are currently on a skip-cooldown — songs you've skipped multiple times recently.",
      'The "exclude_active" mode (default) hides cooled-down tracks. "only_active" surfaces the skip pile.',
    ],
  },
  artist_cap: {
    label: 'Artist Cap', icon: Users, color: '#94a3b8',
    oneliner: 'Limit how many tracks per artist appear in this chain',
    desc: [
      "After all other filters run, caps how many tracks from any one artist can appear in this chain's share of the playlist.",
    ],
  },
  jitter: {
    label: 'Jitter', icon: Shuffle, color: '#c084fc',
    oneliner: 'Randomise track ordering so every generation feels different',
    desc: [
      'Nudges each track\'s score by a small random amount before sorting. Without jitter, the same filters always produce the same order.',
    ],
  },

  artist_catalog_popularity: {
    label: "Artist's Top Tracks", icon: BarChart2, color: '#38bdf8',
    oneliner: "Filter by how popular a track is within its artist's own catalog",
    desc: [
      "Scores each track 0–100 based on Last.fm listener counts relative to that artist's most-streamed song (which always scores 100). This is per-artist — a 70 means 'top 3 for this artist', not globally.",
      "Use this instead of Global Popularity when you want an artist's signature hits without surfacing the same mega-hits across all artists.",
      { '80–100': "Artist's #1–2 most popular songs" },
      { '50–79':  'Top 3–4 tracks in catalog' },
      { '30–49':  'Solid hits — roughly top 5–6' },
      { '10–29':  'Any track in the Last.fm top 10 for this artist' },
    ],
  },

  // ── NEW blocks ────────────────────────────────────────────────────────────

  skip_rate: {
    label: 'Skip Rate', icon: SkipForward, color: '#fb7185',
    oneliner: 'Filter by how often you skip a track (0 = never skip, 1 = always skip)',
    desc: [
      'Uses the skip penalty score (0.0–1.0) computed from your skip history. 0 means you\'ve never skipped this track; 1.0 means you almost always skip it.',
      'Set max to 0.1 as an AND child to silently exclude skip-prone tracks — a more granular alternative to the Cooldown block.',
      { '0.0–0.1': 'Rarely or never skipped' },
      { '0.1–0.3': 'Occasionally skipped' },
      { '0.3–0.6': 'Skipped fairly often' },
      { '0.6–1.0': 'Frequently skipped' },
    ],
  },
  replay_boost: {
    label: 'Replay Boost', icon: Repeat, color: '#f0abfc',
    oneliner: "Tracks from artists you've been voluntarily seeking out lately",
    desc: [
      'Detects when you\'ve deliberately returned to an artist\'s music within 7 days of a previous play — a strong signal of current enthusiasm.',
      'Great for a "what I\'m obsessed with right now" chain. Because it measures deliberate replays (not auto-play), it captures genuine enthusiasm rather than passive listening.',
      { '0.1–2.0':  'Light replay signal — mild current interest' },
      { '2.0–6.0':  'Clear replay pattern — currently enjoying this artist' },
      { '6.0–12.0': 'Strong obsession signal — you keep coming back' },
    ],
  },
  novelty: {
    label: 'Novelty', icon: Wind, color: '#67e8f9',
    oneliner: 'Unplayed tracks ranked by how well they fit your taste (0 = wild guess, 100 = safe bet)',
    desc: [
      "Every unplayed track gets a novelty score based on how much you love the artist and genre — even though you've never heard that specific song.",
      'Unlike Discovery (familiarity tiers), Novelty gives direct control over taste-fit of unplayed suggestions.',
      { '80–100': 'Unplayed tracks from your most-loved artists & genres' },
      { '50–80':  'Solid taste fit' },
      { '20–50':  'Adjacent territory' },
      { '0–20':   'Wild card — minimal taste signal' },
    ],
  },
  recency_score: {
    label: 'Recency Score', icon: BarChart2, color: '#fcd34d',
    oneliner: 'Smooth recency gradient — 100 = just played, 0 = not played in a year',
    desc: [
      'Unlike Play Recency (hard date cutoff), this uses a continuous 0–100 score that decays linearly from your last play date.',
      'Score 100 = played within 30 days; 0 = not played in over a year.',
      { '90–100': 'Played within ~30 days' },
      { '70–90':  'Played within ~3 months' },
      { '50–70':  'Played within ~5–6 months' },
      { '20–50':  'Played 6–10 months ago' },
      { '0–20':   'Played nearly a year ago or never' },
    ],
  },
  skip_streak: {
    label: 'Skip Streak', icon: Zap, color: '#fbbf24',
    oneliner: 'Filter by consecutive skips in a row (0 = clean, 3+ = on cooldown)',
    desc: [
      'Tracks the current consecutive-skip streak — how many times in a row you\'ve skipped without completing a play in between. Cooldown triggers at 3+.',
      'Use streak_max: 0 as an AND child to exclude any track skipped even once consecutively — stricter than the Cooldown block.',
      { '0':   'No current skip streak — clean slate' },
      { '1':   'Skipped once in a row' },
      { '2':   'Skipped twice in a row' },
      { '3+':  'On active cooldown' },
    ],
  },

  // ── Audio waveform analysis blocks ────────────────────────────────────────────
  // All require the audio analysis job to have run. Filters on LibraryTrack columns.

  bpm_range: {
    label: 'BPM Range', icon: Activity, color: '#f87171',
    oneliner: 'Filter tracks by tempo in beats per minute',
    desc: [
      'Uses waveform analysis (librosa) to match tracks within a tempo range. Requires the Audio Analysis job to have run on your library.',
      'Harmonic BPM also matches half and double tempo — a 120 BPM filter catches 60 and 240 BPM too, since the rhythmic feel is the same.',
      { '60–90':   'Slow / ballad' },
      { '90–110':  'Moderate / mid-tempo' },
      { '110–130': 'Upbeat / pop' },
      { '130–160': 'Fast / dance' },
      { '160–200': 'Very fast / drum & bass' },
    ],
  },
  musical_key: {
    label: 'Musical Key', icon: Music2, color: '#a78bfa',
    oneliner: 'Filter by tonal center and mode — major, minor, or specific root notes',
    desc: [
      'Detects the musical key via chromagram analysis and the Krumhansl-Schmuckler algorithm. Requires the Audio Analysis job to have run.',
      'Filter by mode (major/minor) and optional root notes. Minor keys for melancholic sets; major for upbeat. Leave notes empty to match all keys in that mode.',
    ],
  },
  energy: {
    label: 'Energy', icon: Zap, color: '#fbbf24',
    oneliner: 'Filter by audio energy level (0 = quiet, 1 = loud and dense)',
    desc: [
      'RMS loudness normalized 0–1. High-energy tracks are loud and intense; low-energy tracks are quiet and sparse. Requires Audio Analysis.',
      { '0.7–1.0': 'High energy — loud, intense' },
      { '0.4–0.7': 'Mid energy — moderate intensity' },
      { '0.0–0.4': 'Low energy — quiet, delicate' },
    ],
  },
  loudness_db: {
    label: 'Loudness', icon: BarChart2, color: '#60a5fa',
    oneliner: 'Filter by integrated loudness in dBFS (closer to 0 = louder)',
    desc: [
      'Integrated loudness in decibels relative to full scale. Always negative — 0 dBFS is the loudest possible. Heavily mastered tracks cluster near -10 to -5; dynamic acoustic recordings reach -30 or below.',
      { '-10 to 0':   'Very loud / heavily mastered' },
      { '-20 to -10': 'Moderately loud' },
      { '-30 to -20': 'Quiet / dynamic' },
      { 'below -30':  'Very quiet or sparse' },
    ],
  },
  beat_strength: {
    label: 'Beat Strength', icon: Activity, color: '#f97316',
    oneliner: 'Filter by rhythmic pulse clarity (0 = loose/ambient, 1 = strong locked-in beat)',
    desc: [
      'Measures how clear and consistent the rhythmic beat is. High values = strong, metronomic pulse. Low values = loose, rubato, or ambient texture. Requires Audio Analysis.',
      { '0.7–1.0': 'Strong clear beat — great for workouts' },
      { '0.4–0.7': 'Moderate rhythmic presence' },
      { '0.0–0.4': 'Loose / ambient / rubato' },
    ],
  },
  time_signature: {
    label: 'Time Signature', icon: Music2, color: '#34d399',
    oneliner: 'Filter by beats per bar — 3/4 (waltz) or 4/4 (common time)',
    desc: [
      'Estimated from onset autocorrelation. Most pop and rock is 4/4. Waltz, some jazz, and folk is 3/4. Requires Audio Analysis.',
      'Useful for sets with a consistent rhythmic feel — all waltzes, or strictly common-time dance tracks.',
    ],
  },
  acousticness: {
    label: 'Acousticness', icon: Wind, color: '#7ee787',
    oneliner: 'Filter by acoustic vs electronic character (0 = fully electronic, 1 = fully acoustic)',
    desc: [
      'A heuristic 0–1 estimate based on zero-crossing rate and spectral contrast. Acoustic instruments (guitar, piano, voice) score high; synths and drum machines score low. Requires Audio Analysis.',
      { '0.7–1.0': 'Strongly acoustic' },
      { '0.4–0.7': 'Mixed / semi-acoustic' },
      { '0.0–0.4': 'Electronic / produced' },
    ],
  },
}

export const DEFAULT_PARAMS = {
  final_score:       { score_min: 0, score_max: 99 },
  play_recency:      { mode: 'within', days: 30 },
  genre:             { genres: [] },
  artist:            { artists: [] },
  play_count:        { play_count_min: 0, play_count_max: 500 },
  discovery:         { stranger_pct: 34, acquaintance_pct: 33, familiar_pct: 33 },
  global_popularity: { popularity_min: 0, popularity_max: 100 },
  affinity:          { affinity_min: 0, affinity_max: 100 },
  favorites:         {},
  artist_catalog_popularity: { catalog_min: 30, catalog_max: 100, played_filter: 'all' },
  favorite_artists:  { played_filter: 'all' },
  favorite_genres:   { played_filter: 'all' },
  played_status:     { played_filter: 'unplayed' },
  artist_cap:        { max_per_artist: 3 },
  jitter:            { jitter_pct: 0.15 },
  cooldown:          { mode: 'exclude_active' },
  // New blocks
  skip_rate:         { skip_penalty_min: 0.0, skip_penalty_max: 0.3, played_filter: 'all' },
  replay_boost:      { boost_min: 0.1, boost_max: 12, played_filter: 'all' },
  novelty:           { novelty_min: 50, novelty_max: 100 },
  recency_score:     { recency_min: 0, recency_max: 100, played_filter: 'played' },
  skip_streak:       { streak_min: 0, streak_max: 0, played_filter: 'all' },
  // Audio waveform analysis blocks
  bpm_range:         { bpm_min: 120, bpm_max: 160, harmonic: false, played_filter: 'all' },
  musical_key:       { mode: 'all', notes: [], played_filter: 'all' },
  energy:            { energy_min: 0.4, energy_max: 1.0, played_filter: 'all' },
  loudness_db:       { loudness_min: -30, loudness_max: 0, played_filter: 'all' },
  beat_strength:     { beat_min: 0.4, beat_max: 1.0, played_filter: 'all' },
  time_signature:    { time_sigs: [4], played_filter: 'all' },
  acousticness:      { acousticness_min: 0.0, acousticness_max: 1.0, played_filter: 'all' },
}
