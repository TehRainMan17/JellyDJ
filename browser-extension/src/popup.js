const urlInput   = document.getElementById('url');
const tokenInput = document.getElementById('token');
const saveBtn    = document.getElementById('save');
const statusEl   = document.getElementById('status');

function showStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = 'status ' + (type || '');
}

// Load saved config and show current state
chrome.storage.local.get(['jellydjUrl', 'jellydjToken'], (result) => {
  if (result?.jellydjUrl)   urlInput.value   = result.jellydjUrl;
  if (result?.jellydjToken) tokenInput.value = result.jellydjToken;
  if (result?.jellydjUrl && result?.jellydjToken) {
    showStatus('Settings configured. Click Save to re-verify.', 'ok');
  }
});

saveBtn.addEventListener('click', () => {
  const url   = urlInput.value.trim();
  const token = tokenInput.value.trim();

  if (!url) { showStatus('URL is required.', 'err'); return; }
  if (!token) { showStatus('API key is required.', 'err'); return; }

  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving…';
  showStatus('', '');

  // Save immediately so settings are always persisted
  chrome.storage.local.set({ jellydjUrl: url, jellydjToken: token }, () => {
    showStatus('Settings saved. Verifying connection…', 'ok');

    // Now try to verify — this is optional confirmation, settings are already stored
    const base = url.replace(/\/$/, '');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);

    fetch(base + '/api/import/verify', {
      headers: { 'X-JellyDJ-Key': token },
      signal: controller.signal,
    })
      .then(resp => {
        clearTimeout(timer);
        if (resp.status === 401) {
          showStatus('Settings saved, but API key was rejected — check your key.', 'err');
        } else if (!resp.ok) {
          showStatus('Settings saved, but server returned HTTP ' + resp.status, 'err');
        } else {
          return resp.json().then(body => {
            showStatus('Connected as ' + (body.username || 'user') + '. Ready to import!', 'ok');
          });
        }
      })
      .catch(() => {
        clearTimeout(timer);
        showStatus('Settings saved, but could not reach JellyDJ — check the URL.', 'err');
      })
      .finally(() => {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save settings';
      });
  });
});
