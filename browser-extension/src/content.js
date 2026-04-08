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

  // Prevent duplicate injection when background.js re-injects on reload
  if (window.__jellydj_injected) return;
  window.__jellydj_injected = true;

  // ── Platform detection ──────────────────────────────────────────────────────

  const hostname = location.hostname;
  const platform =
    hostname === 'open.spotify.com'                               ? 'spotify'
    : hostname === 'tidal.com' || hostname === 'listen.tidal.com' ? 'tidal'
    : hostname === 'music.youtube.com'                            ? 'youtube_music'
    : hostname === 'www.youtube.com'                              ? 'youtube'
    : null;

  if (!platform) return;

  // youtube_music: playlist import only.
  // youtube (regular YouTube): rip button on /watch pages only.

  // ── JellyDJ logo as inline image (the actual jellyfish DJ icon) ─────────────

  const JELLYDJ_LOGO_SRC = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAEqElEQVR4nH2VW2xURRjH/9/Muex2t3u22xZaeoEWSyu9QFQuhhDRCDzAE2SjokEhghoUlBiMvmB8M/GJN4nYBzQGGkkMSgzITQwqFxPoSrGVUgjb2/ayW3bPnj3nzIxZEEFL/JJJZibf/PL/ku/7DzAt4vzBUyy2OGJZHQ2W1d5Y3P9fbjEI0xK6BGqXBi2HXoCXXS+l066UigESAJtgTEsQGV+XlOhfDQ1dtP95Mx34lAac9mN18ZVKuHuY8loAAYILUcgBpMA0HVAEEi4g3T90zdw+lDp39EHo38C7F9FZ619mnHeSAkkpfK5xIs1nkTkzAKaQvXYLws5L+HnFldAUAYYZ3Hxr8HTnPQbd21g1657WuHECCpIMTdXUzeJ+wEPHzg2QC1qhGCGf6EH/x/vAxnIYHx4U0rGJc41FLOvZnt5vjhdZRYVUXb026DCzm+vBRlJcxJrqecsjVQi9shbRJfUYGlcgUgjFGPK/JeF9egB9V/uR7rkiSEmu6/rAqsfjrXu/fS3PAKgC4TkiapSu45sVZZxmV6N9xQKMnO3Fpf2/Qkzehsg6GNj/C1JnetG2ain0eU0wyqs5A/OV4nN+7jm2ocgqAsFY4HlSSnGNSLk2HF+gfySL0ROX4CcIubNZ5H6agpcgTJzqRv9wDj44mFTQA6UE4SvXKxRFQWtu3lw6kc20Sd8jLVTKGBSckycwPr8NVQvXIFBVCykKgC8RrGtFWLMwYY8hf/wYuB4GlYSZzOWISWrdteWopUnpVEqZt6AEQjNbYEbK4WXKMXjqR1hzWxBuKoE9agPEESzPw59KYPJiN8JVzdBLo/DsKbjjIxDSj/YPnK/UAgETNOUXK4Z0HHgsi0ImjeyNXnAyIAoeKmofgzs5hlRPH7zRIWQS3QiW1YFzBeUKCOVCKA2maYItaliWAnlpJX1M3eqHO5oE1z0s2rELynYQc0zYveegRm+i3AsDWQcd77wPowSQqUHYNwfAwKBzPbN82aYU6zy85TZnPCGUp8IhkmZYw/yd2zA63I+QN4mYWYBIDQOZQUQDNiiXwtBwH2re3IhC2EcoZkiNDBUORH9/44OKjFYsVWfmgWBlyaqV69ao8YkM1JVuBFNJxOZX4c/h80hnboNxjpyRRP3CWmQzSQSvemhZ0gRD19Wl785QNFR5QEp5Z/Ro99YLwc+OvJdw/GxDdW29iMUsPqOmFhN5G33XBkCT41AKUKURNDTWI6LrSI+kkB6bEqODSV5VNvvGJ7sPtq7eSDbF43He1dUlnlz8+jPXrl8+ns1mJFFRuOCmoUHX2V1DKPadxgGX7iydBYRULlllFay1Y/nKg9/t/KHIumMOccR5F7rEkie2brqZ7PnccWwY3PC50okBrNgynDh0ZsBgpiQo5QpbCwTDmNPQ9urhUx/tuyfsAfu6axKrV+xa3Xf98p58PjePBEHIQnGiQIqBMwMaM2HoGiKlsb5Hmxdv/+Lwju8fYl//hiYvqJKXtr394lh6eF3eTrdL6ZcVU00tNFkaiiVmVtQf2rvnwy+rF1Luvwb7kLhv65xzHDuorLc2Hpr77tYjc092qijn2v3M+PQv4C835yq6KI7gIgAAAABJRU5ErkJggg==';
  const JELLYDJ_ICON = `<img src="${JELLYDJ_LOGO_SRC}" style="width:20px;height:20px;vertical-align:middle;margin-right:6px;flex-shrink:0;border-radius:50%">`;

  // ── Inject button ──────────────────────────────────────────────────────────

  function injectButton() {
    const existing = document.getElementById('jellydj-import-btn');

    // If already in the DOM and properly placed, leave it alone
    if (existing && existing.isConnected) {
      // On Spotify, check if it's still inline (parent might have been re-rendered)
      if (platform === 'spotify' && existing.dataset.inline === 'true') {
        // Verify it's still inside the action bar area (not orphaned)
        if (existing.closest('[data-testid="action-bar-row"], [data-testid="action-bar"]') ||
            existing.parentElement?.querySelector('[data-testid="play-button"], [data-testid="more-button"]')) {
          return; // still properly placed
        }
        // Parent was destroyed by Spotify re-render, remove and re-inject
        existing.remove();
      } else if (existing.dataset.inline !== 'true') {
        // Floating button — try to upgrade to inline on Spotify
        if (platform === 'spotify') {
          const inlined = injectIntoSpotifyActionBar(existing);
          if (inlined) {
            existing.dataset.inline = 'true';
          }
        }
        return;
      }
    } else if (existing) {
      existing.remove(); // orphaned node
    }

    const btn = document.createElement('button');
    btn.id = 'jellydj-import-btn';
    btn.innerHTML = JELLYDJ_ICON + '<span>Send to JellyDJ</span>';
    btn.onclick = handleSend;

    // Try to inject inline with Spotify's action bar
    if (platform === 'spotify' && injectIntoSpotifyActionBar(btn)) {
      btn.dataset.inline = 'true';
      return;
    }

    // Fallback: floating button for Tidal, YouTube Music, or if Spotify
    // action bar hasn't rendered yet
    applyFloatingStyle(btn);
    document.body.appendChild(btn);
  }

  function findSpotifyActionBar() {
    // Strategy 1: data-testid selectors (Spotify uses these extensively)
    const testIdSelectors = [
      '[data-testid="action-bar-row"]',
      '[data-testid="playlist-action-bar"]',
      '[data-testid="action-bar"]',
    ];
    for (const sel of testIdSelectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }

    // Strategy 2: Find the play button and walk up to the action row
    const playBtn = document.querySelector(
      '[data-testid="play-button"], [aria-label="Play"], [aria-label="Pause"]'
    );
    if (playBtn) {
      let parent = playBtn.parentElement;
      for (let i = 0; i < 8 && parent; i++) {
        const style = getComputedStyle(parent);
        const isFlexRow = style.display === 'flex' && (style.flexDirection === 'row' || style.flexDirection === '');
        const hasButtons = parent.querySelectorAll('button').length >= 2;
        // The action bar is a flex row, typically > 200px wide, near top of page
        if (isFlexRow && hasButtons && parent.offsetWidth > 200) {
          return parent;
        }
        parent = parent.parentElement;
      }
    }

    // Strategy 3: "more options" button's row container
    const moreBtn = document.querySelector(
      '[data-testid="more-button"], [aria-label*="More options"]'
    );
    if (moreBtn) {
      let parent = moreBtn.parentElement;
      for (let i = 0; i < 5 && parent; i++) {
        const style = getComputedStyle(parent);
        if (style.display === 'flex' && parent.querySelectorAll('button').length >= 2) {
          return parent;
        }
        parent = parent.parentElement;
      }
    }

    return null;
  }

  function injectIntoSpotifyActionBar(btn) {
    const actionBar = findSpotifyActionBar();
    if (!actionBar) return false;

    // Style the button to match Spotify's look
    btn.style.cssText = `
      display: inline-flex;
      align-items: center;
      padding: 6px 16px;
      margin-left: 8px;
      background: #6366f1;
      color: white;
      border: none;
      border-radius: 500px;
      font-size: 13px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: background 0.15s, transform 0.1s;
      white-space: nowrap;
      height: 32px;
      line-height: 1;
    `;
    btn.onmouseenter = () => { btn.style.background = '#4f46e5'; btn.style.transform = 'scale(1.04)'; };
    btn.onmouseleave = () => { btn.style.background = '#6366f1'; btn.style.transform = 'scale(1)'; };

    actionBar.appendChild(btn);
    return true;
  }

  function applyFloatingStyle(btn) {
    btn.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99999;
      display: inline-flex;
      align-items: center;
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
  }

  // ── Scrapers per platform ───────────────────────────────────────────────────

  function getPlaylistName() {
    // Spotify Liked Songs page doesn't have a playlist header — use fixed name
    if (platform === 'spotify' && location.pathname === '/collection/tracks') {
      return 'Liked Songs';
    }

    const selectors = [
      // Spotify
      '[data-testid="playlist-page"] h1',
      '[data-testid="entityTitle"] h1',
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
    // Try the modern tracklist row selector first, then fall back
    let rows = document.querySelectorAll('[data-testid="tracklist-row"]');
    if (rows.length === 0) {
      rows = document.querySelectorAll('[role="row"][aria-rowindex]');
    }
    const tracks = [];
    rows.forEach((row, idx) => {
      // Use aria-rowindex for the real playlist position (Spotify's virtual list sets this)
      // aria-rowindex is 1-based, and the header row is typically index 1, so track 1 = index 2
      const ariaIdx = row.getAttribute('aria-rowindex');
      const rowEl = ariaIdx ? row : row.closest('[aria-rowindex]');
      const rowIndex = rowEl ? parseInt(rowEl.getAttribute('aria-rowindex'), 10) : null;
      // Spotify header row is aria-rowindex=1, first track is 2
      const position = rowIndex ? rowIndex - 1 : idx + 1;

      const titleEl  = row.querySelector('[data-testid="internal-track-link"]') ||
                       row.querySelector('a[href*="/track/"]') ||
                       row.querySelector('div[class*="TrackName"]');
      const artistEl = row.querySelector('span a[href*="/artist/"]') ||
                       row.querySelector('[class*="Artists"] a') ||
                       row.querySelector('a[href*="/artist/"]');
      const albumEl  = row.querySelector('a[href*="/album/"]');

      const trackName  = titleEl?.textContent?.trim() || '';
      const artistName = artistEl?.textContent?.trim() || '';
      const albumName  = albumEl?.textContent?.trim() || '';

      if (trackName && position > 0) {
        tracks.push({ position, track_name: trackName, artist_name: artistName, album_name: albumName });
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

  // ── Full Spotify scraper (handles virtualized list) ────────────────────────

  function findScrollableParent() {
    // Walk up from the tracklist to find the element that actually scrolls.
    // Spotify nests the tracklist inside a div with overflow-y: auto/scroll.
    const tracklist = document.querySelector('[data-testid="playlist-tracklist"]') ||
                      document.querySelector('[role="grid"]') ||
                      document.querySelector('[role="row"]')?.closest('[role="grid"], [role="presentation"]');

    if (tracklist) {
      let el = tracklist.parentElement;
      while (el && el !== document.body) {
        if (el.scrollHeight > el.clientHeight + 10) {
          const style = getComputedStyle(el);
          if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
            return el;
          }
        }
        el = el.parentElement;
      }
    }

    // Fallback: find any scrollable element that's large enough to be the main content area
    const candidates = document.querySelectorAll('main *, [class*="main-view"] *');
    for (const el of candidates) {
      if (el.scrollHeight > el.clientHeight + 200 && el.clientHeight > 300) {
        const style = getComputedStyle(el);
        if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
          return el;
        }
      }
    }

    return document.documentElement;
  }

  async function scrapeSpotifyFull(btn) {
    const scroller = findScrollableParent();

    const collected = new Map(); // position → track object
    let stableRounds = 0;
    let prevCount = 0;
    const MAX_SCROLLS = 300;

    // Save current scroll position to restore later
    const origScrollTop = scroller.scrollTop;

    // Start from the top
    scroller.scrollTop = 0;
    await new Promise(r => setTimeout(r, 400));

    for (let i = 0; i < MAX_SCROLLS; i++) {
      const batch = scrapeSpotify();
      for (const t of batch) {
        // Key by position (from aria-rowindex) to dedupe properly
        if (!collected.has(t.position)) {
          collected.set(t.position, t);
        }
      }

      if (collected.size === prevCount) {
        stableRounds++;
        // Give it more time at the end — Spotify can be slow to render final rows
        if (stableRounds >= 5) break;
      } else {
        stableRounds = 0;
        prevCount = collected.size;
        setButtonText(btn, `Scraping… ${collected.size} tracks`);
      }

      // Check if we've reached the bottom
      const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 20;
      if (atBottom && stableRounds >= 2) break;

      // Scroll down by a smaller increment for more reliable rendering
      scroller.scrollTop += Math.floor(scroller.clientHeight * 0.6);
      await new Promise(r => setTimeout(r, 350));
    }

    // Restore scroll position
    scroller.scrollTop = origScrollTop;

    // Sort by position
    const tracks = Array.from(collected.values())
      .sort((a, b) => a.position - b.position)
      .map((t, i) => ({ ...t, position: i + 1 }));

    return tracks;
  }

  // ── Send to JellyDJ ─────────────────────────────────────────────────────────

  function resetButton(btn) {
    btn.innerHTML = JELLYDJ_ICON + '<span>Send to JellyDJ</span>';
    btn.style.background = '#6366f1';
    btn.disabled = false;
  }

  function setButtonText(btn, text) {
    btn.innerHTML = JELLYDJ_ICON + '<span>' + text + '</span>';
  }

  async function handleSend() {
    const btn = document.getElementById('jellydj-import-btn');
    if (!btn || btn.disabled) return;

    setButtonText(btn, 'Scraping…');
    btn.disabled = true;

    let tracks;

    if (platform === 'spotify') {
      // Spotify uses a virtualized list — only visible rows are in the DOM.
      // We must scroll through the entire playlist to render all tracks.
      tracks = await scrapeSpotifyFull(btn);
    } else {
      tracks = scrapeTracks();
    }

    const playlistName = getPlaylistName();

    if (tracks.length === 0) {
      setButtonText(btn, 'No tracks found — try scrolling first');
      btn.style.background = '#dc2626';
      setTimeout(() => resetButton(btn), 4000);
      return;
    }

    setButtonText(btn, `Sending ${tracks.length} tracks…`);

    // Guard: after extension reload, chrome.runtime is invalidated in
    // orphaned content scripts — sendMessage will throw.
    if (!chrome?.runtime?.id) {
      setButtonText(btn, 'Extension reloaded — refresh page');
      btn.style.background = '#dc2626';
      setTimeout(() => resetButton(btn), 5000);
      return;
    }

    // Send to background script with a timeout so we don't hang forever
    const timeoutId = setTimeout(() => {
      setButtonText(btn, 'Timed out — is JellyDJ running?');
      btn.style.background = '#dc2626';
      setTimeout(() => resetButton(btn), 4000);
    }, 15000);

    try {
      chrome.runtime.sendMessage({
        action: 'importPlaylist',
        data: {
          url:           location.href,
          playlist_name: playlistName,
          tracks,
        },
      }, (response) => {
        clearTimeout(timeoutId);

        // Handle case where service worker didn't respond
        if (chrome.runtime.lastError) {
          setButtonText(btn, 'Extension error — refresh page');
          btn.style.background = '#dc2626';
          setTimeout(() => resetButton(btn), 4000);
          return;
        }

        if (response?.ok) {
          setButtonText(btn, `Sent ${tracks.length} tracks!`);
          btn.style.background = '#16a34a';
        } else {
          setButtonText(btn, response?.error || 'Failed');
          btn.style.background = '#dc2626';
        }
        setTimeout(() => resetButton(btn), 4000);
      });
    } catch (err) {
      clearTimeout(timeoutId);
      setButtonText(btn, 'Extension error — refresh page');
      btn.style.background = '#dc2626';
      setTimeout(() => resetButton(btn), 4000);
    }
  }

  // ── YouTube Rip Button ────────────────────────────────────────────────────
  //
  // Appears on regular YouTube watch pages (/watch?v=...).
  // Sends the video URL to the JellyDJ backend, which downloads the audio via
  // yt-dlp and saves it as a 320 kbps MP3 in the configured library folder.
  //
  // NOTE: YouTube serves audio at 128–160 kbps; the 320 kbps MP3 is a
  // re-encode at a higher container bitrate, not a true quality gain.
  //
  // Placement: tries to embed inline to the left of the thumbs up/down buttons.
  // Falls back to a floating bottom-right button if the actions bar isn't ready.
  // Hidden while the player is in fullscreen mode.

  function resetRipButton(btn) {
    btn.innerHTML = JELLYDJ_LOGO_SRC
      ? JELLYDJ_ICON + '<span>Rip to JellyDJ</span>'
      : '<span>Rip to JellyDJ</span>';
    btn.style.background = '#7c3aed';
    btn.disabled = false;
  }

  function setRipText(btn, text) {
    btn.innerHTML = JELLYDJ_ICON + '<span>' + text + '</span>';
  }

  // Locate the container holding YouTube's like/dislike buttons.
  // MUST be scoped to the primary video section — YouTube renders the same
  // #top-level-buttons-computed element inside every sidebar video card too,
  // and document.querySelector() returns the first one in DOM order, which is
  // often a sidebar card rather than the main video action bar.
  function findYouTubeActionsBar() {
    // Scope to the primary column of the watch page so we never pick up a
    // sidebar/recommendation card's action menu.
    const scope = document.querySelector('ytd-watch-flexy #primary, ytd-watch-flexy') ||
                  document.querySelector('#primary-inner') ||
                  document;

    // Modern YouTube: ytd-menu-renderer inside the video info section
    const topLevel = scope.querySelector('#top-level-buttons-computed');
    if (topLevel) return topLevel;

    // Older structure: ytd-like-button-renderer's parent row
    const likeRenderer = scope.querySelector('ytd-like-button-renderer');
    if (likeRenderer?.parentElement) return likeRenderer.parentElement;

    // Newer segmented like/dislike widget
    const segmented = scope.querySelector('segmented-like-dislike-button-view-model');
    if (segmented?.parentElement) return segmented.parentElement;

    return null;
  }

  function applyRipFloatingStyle(btn) {
    btn.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99999;
      display: inline-flex;
      align-items: center;
      background: #7c3aed;
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
  }

  function injectIntoYouTubeActions(btn) {
    const container = findYouTubeActionsBar();
    if (!container) return false;

    btn.style.cssText = `
      display: inline-flex;
      align-items: center;
      padding: 0 16px;
      margin-right: 8px;
      background: #7c3aed;
      color: white;
      border: none;
      border-radius: 18px;
      font-size: 14px;
      font-weight: 500;
      font-family: "YouTube Sans", "Roboto", sans-serif;
      cursor: pointer;
      height: 36px;
      white-space: nowrap;
      transition: background 0.15s;
      flex-shrink: 0;
    `;
    btn.onmouseenter = () => { if (!btn.disabled) btn.style.background = '#6d28d9'; };
    btn.onmouseleave = () => { if (!btn.disabled) btn.style.background = '#7c3aed'; };

    // Insert as first child so it sits to the left of the thumbs buttons
    container.insertBefore(btn, container.firstChild);
    return true;
  }

  // Show/hide the rip button based on fullscreen state.
  function updateRipButtonVisibility() {
    const btn = document.getElementById('jellydj-rip-btn');
    if (!btn) return;
    const moviePlayer = document.getElementById('movie_player');
    const isFullscreen = !!document.fullscreenElement ||
      moviePlayer?.classList.contains('ytp-fullscreen');
    btn.style.display = isFullscreen ? 'none' : 'inline-flex';
  }

  // Attach fullscreen listener once per page load.
  if (!window.__jellydj_fullscreen_wired) {
    window.__jellydj_fullscreen_wired = true;
    document.addEventListener('fullscreenchange', updateRipButtonVisibility);
    // YouTube fires ytp-fullscreen class changes without triggering the native
    // fullscreen API when the mini-player or cinema mode is used, so also poll
    // via a class observer on #movie_player once it exists.
    const wireFSObserver = () => {
      const player = document.getElementById('movie_player');
      if (!player) return;
      new MutationObserver(updateRipButtonVisibility)
        .observe(player, { attributeFilter: ['class'] });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', wireFSObserver);
    } else {
      wireFSObserver();
    }
  }

  // Targeted observer that fires the moment YouTube's action bar appears in the
  // DOM.  No debounce — we inject inline as soon as the container is ready.
  // Kept separate from the general MutationObserver so YouTube's constant DOM
  // churn (player progress, recommendations) cannot delay or prevent injection.
  let ytActionsWaiter = null;

  function cancelYtActionsWaiter() {
    if (ytActionsWaiter) { ytActionsWaiter.disconnect(); ytActionsWaiter = null; }
  }

  function waitForYouTubeActionsBar(btn) {
    cancelYtActionsWaiter();
    ytActionsWaiter = new MutationObserver(() => {
      const container = findYouTubeActionsBar();
      if (!container) return;
      cancelYtActionsWaiter();
      // Re-check we're still on a watch page and the button hasn't been placed yet
      if (!isYouTubeWatchPage()) {
        return;
      }
      const current = document.getElementById('jellydj-rip-btn');
      if (current?.dataset.inline === 'true' && current.isConnected) {
        return;
      }
      if (current && current !== btn) { current.remove(); }
      if (injectIntoYouTubeActions(btn)) {
        btn.dataset.inline = 'true';
        stopObserver();
        watchRipButtonContainer(btn);
      } else {
      }
    });
    ytActionsWaiter.observe(document.body, { childList: true, subtree: true });
  }

  function injectRipButton() {
    const existing = document.getElementById('jellydj-rip-btn');

    if (existing && existing.isConnected) {
      // Already inline and still properly placed — nothing to do
      if (existing.dataset.inline === 'true') {
        if (existing.closest('#top-level-buttons-computed, ytd-like-button-renderer, segmented-like-dislike-button-view-model')) {
          return;
        }
        // Container was destroyed by YT re-render — remove and re-inject below
        existing.remove();
      } else {
        // Pending inline placement — try again now
        if (injectIntoYouTubeActions(existing)) {
          cancelYtActionsWaiter();
          existing.dataset.inline = 'true';
          stopObserver();
          watchRipButtonContainer(existing);
        } else {
        }
        return;
      }
    } else if (existing) {
      existing.remove();
    }

    cancelYtActionsWaiter();

    const btn = document.createElement('button');
    btn.id = 'jellydj-rip-btn';
    btn.innerHTML = JELLYDJ_ICON + '<span>Rip to JellyDJ</span>';
    btn.onclick = handleRip;


    if (injectIntoYouTubeActions(btn)) {
      // Actions bar already in DOM — place inline immediately
      btn.dataset.inline = 'true';
      stopObserver();
      watchRipButtonContainer(btn);
    } else {
      // Actions bar not rendered yet.  Watch for it to appear without debounce
      // so we inject the moment it's ready, regardless of ongoing DOM activity.
      waitForYouTubeActionsBar(btn);
    }
  }

  async function handleRip() {
    const btn = document.getElementById('jellydj-rip-btn');
    if (!btn || btn.disabled) return;

    btn.disabled = true;
    setRipText(btn, 'Starting…');

    if (!chrome?.runtime?.id) {
      setRipText(btn, 'Extension reloaded — refresh page');
      btn.style.background = '#dc2626';
      setTimeout(() => resetRipButton(btn), 4000);
      return;
    }

    // POST to JellyDJ via background.js to get a job ID
    let jobId;
    try {
      const result = await new Promise((resolve, reject) => {
        const tid = setTimeout(() => reject(new Error('Timed out — is JellyDJ running?')), 15000);
        chrome.runtime.sendMessage({ action: 'ripYouTube', url: location.href }, (resp) => {
          clearTimeout(tid);
          if (chrome.runtime.lastError) reject(new Error('Extension error — refresh page'));
          else resolve(resp);
        });
      });

      if (!result.ok) throw new Error(result.error || 'Failed to queue rip');
      jobId = result.job_id;
    } catch (err) {
      setRipText(btn, err.message);
      btn.style.background = '#dc2626';
      setTimeout(() => resetRipButton(btn), 5000);
      return;
    }

    const STATUS_LABELS = {
      queued:       'Queued…',
      fetching_info:'Reading metadata…',
      downloading:  'Downloading audio…',
      converting:   'Converting to MP3…',
      scanning:     'Updating Jellyfin library…',
    };

    // Poll every 3 s for up to 3 minutes.
    // Routing through background.js avoids mixed-content blocks: YouTube is
    // served over HTTPS and a direct fetch() to an HTTP JellyDJ URL is blocked
    // by Chrome.  The service worker has no such restriction.
    let attempts = 0;
    const MAX = 60;

    const poll = async () => {
      if (attempts++ >= MAX) {
        setRipText(btn, 'Timed out — check JellyDJ logs');
        btn.style.background = '#dc2626';
        btn.disabled = false;
        setTimeout(() => resetRipButton(btn), 6000);
        return;
      }

      let result;
      try {
        result = await new Promise((resolve, reject) => {
          chrome.runtime.sendMessage({ action: 'pollRipStatus', jobId }, (resp) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
            else resolve(resp);
          });
        });
      } catch {
        setRipText(btn, 'Extension error — refresh page');
        btn.style.background = '#dc2626';
        btn.disabled = false;
        setTimeout(() => resetRipButton(btn), 4000);
        return;
      }

      if (!result.ok) {
        setRipText(btn, 'Connection lost');
        btn.style.background = '#dc2626';
        btn.disabled = false;
        setTimeout(() => resetRipButton(btn), 4000);
        return;
      }

      const job = result.job;
      if (job.status === 'done') {
        const label = job.title ? `Ripped: ${job.title}` : 'Ripped!';
        setRipText(btn, label);
        btn.style.background = '#16a34a';
        btn.disabled = false;
        setTimeout(() => resetRipButton(btn), 8000);
      } else if (job.status === 'error') {
        setRipText(btn, 'Failed — check JellyDJ logs');
        btn.style.background = '#dc2626';
        btn.disabled = false;
        setTimeout(() => resetRipButton(btn), 6000);
      } else {
        setRipText(btn, STATUS_LABELS[job.status] || 'Processing…');
        setTimeout(poll, 3000);
      }
    };

    setTimeout(poll, 3000);
  }

  // ── Watch for SPA navigation and inject button ────────────────────────────

  function isPlaylistPage() {
    return (
      (platform === 'spotify'       && (/\/playlist\//.test(location.pathname) || location.pathname === '/collection/tracks')) ||
      (platform === 'tidal'         && /\/playlist\//.test(location.pathname)) ||
      (platform === 'youtube_music' && /[?&]list=/.test(location.search))
    );
  }

  function isYouTubeWatchPage() {
    return platform === 'youtube'
      && location.pathname === '/watch'
      && /[?&]v=/.test(location.search);
  }

  function tryInject() {
    // Playlist import button (Spotify / Tidal / YouTube Music playlists)
    if (isPlaylistPage()) {
      injectButton();
    } else {
      const existing = document.getElementById('jellydj-import-btn');
      if (existing) existing.remove();
    }

    // Rip button (regular YouTube watch pages only)
    if (isYouTubeWatchPage()) {
      injectRipButton();
    } else {
      const ripBtn = document.getElementById('jellydj-rip-btn');
      if (ripBtn) ripBtn.remove();
    }
  }

  // Initial injection
  tryInject();

  // ── MutationObserver (mutation-driven debounce) ────────────────────────────
  //
  // Watches for DOM changes so we can detect when a SPA has finished rendering
  // a new page and inject the button.  Uses a TRAILING-edge debounce so
  // tryInject fires after things settle, giving React/Polymer time to finish.
  //
  // IMPORTANT: this timer is driven ONLY by mutations.  Navigation event
  // handlers use a separate navTimer so that constant YouTube DOM activity
  // (player progress bar, recommendations loading, etc.) cannot indefinitely
  // delay the post-navigation injection.
  //
  // On YouTube watch pages the video player mutates its DOM dozens of times
  // per second.  To avoid starving YouTube's rendering pipeline we disconnect
  // this observer once the rip button is placed inline and replace it with a
  // slim per-container watcher.  The full observer is re-enabled on every
  // navigation event.
  let debounceTimer = null;
  let navTimer = null;          // separate timer for navigation-triggered injects
  let observerActive = false;
  let parentWatcher = null;     // narrow watcher on the rip button's parent only

  const observer = new MutationObserver(() => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      tryInject();
    }, 500);
  });

  function startObserver() {
    if (observerActive) return;
    observer.observe(document.body, { childList: true, subtree: true });
    observerActive = true;
  }

  function stopObserver() {
    if (!observerActive) return;
    observer.disconnect();
    observerActive = false;
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }

  // Schedule a navigation-triggered inject.  Uses navTimer, which is NOT
  // shared with debounceTimer, so MutationObserver callbacks cannot cancel it.
  function scheduleNavInject(delay) {
    clearTimeout(navTimer);
    navTimer = setTimeout(() => { navTimer = null; tryInject(); }, delay);
  }

  // Watch only the rip button's direct parent for child removal.
  // When the button is evicted (YouTube re-renders the action bar), resume the
  // full observer so we detect the new action bar and re-inject.
  function watchRipButtonContainer(btn) {
    if (parentWatcher) { parentWatcher.disconnect(); parentWatcher = null; }
    const parent = btn.parentElement;
    if (!parent) return;
    parentWatcher = new MutationObserver(() => {
      if (!btn.isConnected) {
        parentWatcher.disconnect();
        parentWatcher = null;
        startObserver();
      }
    });
    parentWatcher.observe(parent, { childList: true });
  }

  startObserver();

  // popstate fires on browser back/forward, but Spotify's in-app navigation
  // uses history.pushState — patch it so we detect those URL changes too.
  const _origPushState = history.pushState.bind(history);
  history.pushState = function (...args) {
    _origPushState(...args);
    scheduleNavInject(600);
  };
  const _origReplaceState = history.replaceState.bind(history);
  history.replaceState = function (...args) {
    _origReplaceState(...args);
    scheduleNavInject(600);
  };

  // Also re-check on popstate for browser back/forward navigation
  window.addEventListener('popstate', () => scheduleNavInject(300));

  // YouTube fires yt-navigate-finish on document when its SPA navigation fully
  // completes (page content rendered).  This is the most reliable signal for
  // in-page YouTube navigation — pushState fires too early, and the
  // MutationObserver debounce can be continuously reset by YouTube's own DOM
  // activity, preventing it from ever settling.
  //
  // navTimer is independent of debounceTimer, so this call is guaranteed to
  // fire even while YouTube keeps mutating the DOM.
  function onYtNavigateFinish() {
    cancelYtActionsWaiter();
    if (parentWatcher) { parentWatcher.disconnect(); parentWatcher = null; }
    startObserver();
    scheduleNavInject(500);
  }
  document.addEventListener('yt-navigate-finish', onYtNavigateFinish);
  window.addEventListener('yt-navigate-finish', onYtNavigateFinish);
})();
