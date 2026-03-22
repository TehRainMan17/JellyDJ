/**
 * JellyDJ Playlist Importer — content.js
 *
 * Runs on supported playlist pages. Scrapes the visible track list from
 * the page DOM and injects a "Send to JellyDJ" button in the page UI.
 *
 * All scraping happens client-side — no requests to Spotify/Tidal/YT APIs.
 * The scraped data is sent to the JellyDJ backend over localhost.
 *
 * Security notes:
 *   - We only read from the DOM of pages the user is already viewing.
 *   - We POST to a URL the user has configured (their JellyDJ instance).
 *   - We never read cookies or credentials from the music service.
 */

(function () {
  'use strict';

  // ── Platform detection ──────────────────────────────────────────────────────

  const hostname = location.hostname;
  const platform =
    hostname === 'open.spotify.com'                            ? 'spotify'
    : hostname === 'tidal.com' || hostname === 'listen.tidal.com' ? 'tidal'
    : hostname === 'music.youtube.com' || hostname === 'www.youtube.com' ? 'youtube_music'
    : null;

  if (!platform) return;

  // ── Inject button once DOM is ready ────────────────────────────────────────

  function injectButton() {
    if (document.getElementById('jellydj-import-btn')) return;

    const btn = document.createElement('button');
    btn.id = 'jellydj-import-btn';
    btn.textContent = '🎵 Send to JellyDJ';
    btn.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99999;
      background: #6366f1;
      color: white;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      transition: background 0.15s;
    `;
    btn.onmouseenter = () => { btn.style.background = '#4f46e5'; };
    btn.onmouseleave = () => { btn.style.background = '#6366f1'; };
    btn.onclick = handleSend;
    document.body.appendChild(btn);
  }

  // ── Scrapers per platform ───────────────────────────────────────────────────

  function getPlaylistName() {
    const selectors = [
      // Spotify
      '[data-testid="playlist-page"] h1',
      '.playlist-playlist-header h1',
      // Tidal
      '.playlist-header h1',
      '[class*="playlistTitle"]',
      // YouTube Music
      'yt-formatted-string.title',
      'h2.ytmusic-detail-header-renderer',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) return el.textContent.trim();
    }
    return document.title.replace(/ [-–|].*/, '').trim() || 'Imported Playlist';
  }

  function scrapeSpotify() {
    const rows = document.querySelectorAll('[data-testid="tracklist-row"]');
    const tracks = [];
    rows.forEach((row, idx) => {
      const titleEl  = row.querySelector('[data-testid="internal-track-link"]') ||
                       row.querySelector('a[href*="/track/"]');
      const artistEl = row.querySelector('span a[href*="/artist/"]') ||
                       row.querySelector('[class*="Artists"] a');
      const albumEl  = row.querySelector('a[href*="/album/"]');

      const trackName  = titleEl?.textContent?.trim() || '';
      const artistName = artistEl?.textContent?.trim() || '';
      const albumName  = albumEl?.textContent?.trim() || '';

      if (trackName) {
        tracks.push({ position: idx + 1, track_name: trackName, artist_name: artistName, album_name: albumName });
      }
    });
    return tracks;
  }

  function scrapeTidal() {
    const rows = document.querySelectorAll('[class*="tableRow"], tr[class*="PlaylistTrack"]');
    const tracks = [];
    rows.forEach((row, idx) => {
      const titleEl  = row.querySelector('[class*="title"]');
      const artistEl = row.querySelector('[class*="artist"]');
      const albumEl  = row.querySelector('[class*="album"]');

      const trackName  = titleEl?.textContent?.trim() || '';
      const artistName = artistEl?.textContent?.trim() || '';
      const albumName  = albumEl?.textContent?.trim() || '';

      if (trackName) {
        tracks.push({ position: idx + 1, track_name: trackName, artist_name: artistName, album_name: albumName });
      }
    });
    return tracks;
  }

  function scrapeYouTubeMusic() {
    const rows = document.querySelectorAll('ytmusic-responsive-list-item-renderer');
    const tracks = [];
    rows.forEach((row, idx) => {
      const columns = row.querySelectorAll('yt-formatted-string');
      // YTM layout: [title, artist, album, duration] (columns vary by context)
      const trackName  = columns[0]?.textContent?.trim() || '';
      const artistName = columns[1]?.textContent?.trim() || '';
      const albumName  = columns[2]?.textContent?.trim() || '';

      if (trackName) {
        tracks.push({ position: idx + 1, track_name: trackName, artist_name: artistName, album_name: albumName });
      }
    });
    return tracks;
  }

  function scrapeTracks() {
    if (platform === 'spotify')      return scrapeSpotify();
    if (platform === 'tidal')        return scrapeTidal();
    if (platform === 'youtube_music') return scrapeYouTubeMusic();
    return [];
  }

  // ── Send to JellyDJ ─────────────────────────────────────────────────────────

  async function handleSend() {
    const btn = document.getElementById('jellydj-import-btn');
    btn.textContent = '⏳ Scraping…';
    btn.disabled = true;

    const tracks = scrapeTracks();
    const playlistName = getPlaylistName();

    if (tracks.length === 0) {
      btn.textContent = '⚠ No tracks found';
      btn.style.background = '#dc2626';
      setTimeout(() => {
        btn.textContent = '🎵 Send to JellyDJ';
        btn.style.background = '#6366f1';
        btn.disabled = false;
      }, 3000);
      return;
    }

    // Ask background script to fetch config + send the data
    chrome.runtime.sendMessage({
      action: 'importPlaylist',
      data: {
        url:           location.href,
        playlist_name: playlistName,
        tracks,
      },
    }, (response) => {
      if (response?.ok) {
        btn.textContent = `✓ Sent ${tracks.length} tracks`;
        btn.style.background = '#16a34a';
        setTimeout(() => {
          btn.textContent = '🎵 Send to JellyDJ';
          btn.style.background = '#6366f1';
          btn.disabled = false;
        }, 4000);
      } else {
        btn.textContent = '✗ ' + (response?.error || 'Failed');
        btn.style.background = '#dc2626';
        setTimeout(() => {
          btn.textContent = '🎵 Send to JellyDJ';
          btn.style.background = '#6366f1';
          btn.disabled = false;
        }, 4000);
      }
    });
  }

  // ── Wait for SPA route stabilization, then inject ──────────────────────────

  function tryInject() {
    // Only inject on playlist pages
    const isPlaylist =
      (platform === 'spotify'       && /\/playlist\//.test(location.pathname)) ||
      (platform === 'tidal'         && /\/playlist\//.test(location.pathname)) ||
      (platform === 'youtube_music' && /[?&]list=/.test(location.search));

    if (isPlaylist) {
      injectButton();
    } else {
      const existing = document.getElementById('jellydj-import-btn');
      if (existing) existing.remove();
    }
  }

  // Initial injection
  tryInject();

  // Watch for SPA navigation (Spotify/YTM are SPAs)
  const observer = new MutationObserver(() => tryInject());
  observer.observe(document.body, { childList: true, subtree: true });

  // Also re-check on popstate
  window.addEventListener('popstate', tryInject);
  window.addEventListener('locationchange', tryInject);
})();
