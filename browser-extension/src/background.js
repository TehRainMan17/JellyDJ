/**
 * JellyDJ Playlist Importer — background.js (service worker)
 *
 * Handles:
 *  - Storing the JellyDJ instance URL and auth token (set once in popup)
 *  - Receiving importPlaylist messages from content.js
 *  - POSTing the track data to the configured JellyDJ instance
 *  - Re-injecting content scripts into open tabs on extension reload
 */

// On install/reload, re-inject content.js into any matching tabs so the
// user doesn't need to manually refresh Spotify/Tidal/YTM pages.
chrome.runtime.onInstalled.addListener(() => {
  const patterns = [
    'https://open.spotify.com/*',
    'https://tidal.com/*',
    'https://listen.tidal.com/*',
    'https://music.youtube.com/*',
    'https://www.youtube.com/*',
  ];
  for (const pattern of patterns) {
    chrome.tabs.query({ url: pattern }, (tabs) => {
      for (const tab of tabs) {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['src/content.js'],
        }).catch(() => {}); // ignore tabs that can't be injected
      }
    });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'importPlaylist') {
    handleImport(message.data).then(sendResponse);
    return true; // Keep channel open for async response
  }
  if (message.action === 'saveConfig') {
    chrome.storage.local.set({ jellydjUrl: message.url, jellydjToken: message.token }, () => {
      sendResponse({ ok: true });
    });
    return true;
  }
  if (message.action === 'getConfig') {
    chrome.storage.local.get(['jellydjUrl', 'jellydjToken'], (result) => {
      sendResponse(result);
    });
    return true;
  }
});

async function handleImport(data) {
  const { jellydjUrl, jellydjToken } = await chrome.storage.local.get(['jellydjUrl', 'jellydjToken']);

  if (!jellydjUrl) {
    return { ok: false, error: 'JellyDJ URL not configured. Click the extension icon to set it.' };
  }

  const endpoint = jellydjUrl.replace(/\/$/, '') + '/api/import/playlists';

  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(jellydjToken ? { 'X-JellyDJ-Key': jellydjToken } : {}),
      },
      body: JSON.stringify({
        url:           data.url,
        playlist_name: data.playlist_name,
        tracks:        data.tracks,
      }),
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      return { ok: false, error: body.detail || `HTTP ${resp.status}` };
    }

    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message || 'Network error — is JellyDJ running?' };
  }
}
