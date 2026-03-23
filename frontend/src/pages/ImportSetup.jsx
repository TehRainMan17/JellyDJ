/**
 * ImportSetup.jsx — Extension setup page for playlist import
 *
 * Route: /import/setup
 *
 * Sections:
 *  1. API key management for the browser extension
 *  2. Download the Chrome extension zip
 *  3. Step-by-step installation instructions
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api.js'

// ── API Key Management ──────────────────────────────────────────────────────

function ApiKeySection() {
  const [keyData, setKeyData]     = useState(null)
  const [newKey, setNewKey]       = useState(null)
  const [showNewKey, setShowNewKey] = useState(false)
  const [copied, setCopied]       = useState(false)
  const [loading, setLoading]     = useState(true)
  const [generating, setGenerating] = useState(false)
  const [revoking, setRevoking]   = useState(false)

  const loadKeys = useCallback(async () => {
    setLoading(true)
    try {
      const keys = await api.get('/api/import/api-keys')
      setKeyData(keys.length > 0 ? keys[0] : null)
    } catch (err) {
      console.error('Failed to load API keys:', err)
    }
    setLoading(false)
  }, [])

  useEffect(() => { loadKeys() }, [loadKeys])

  async function handleGenerate() {
    setGenerating(true)
    try {
      const result = await api.post('/api/import/api-keys', {})
      setNewKey(result.key)
      setShowNewKey(true)
      await loadKeys()
    } catch (err) {
      alert('Failed to generate key: ' + err.message)
    }
    setGenerating(false)
  }

  async function handleReroll() {
    if (!keyData || !confirm('Old key will be immediately invalid. Continue?')) return
    setGenerating(true)
    try {
      const result = await api.post(`/api/import/api-keys/${keyData.id}/reroll`, {})
      setNewKey(result.key)
      setShowNewKey(true)
      await loadKeys()
    } catch (err) {
      alert('Failed to reroll key: ' + err.message)
    }
    setGenerating(false)
  }

  async function handleRevoke() {
    if (!keyData || !confirm('This key will be permanently invalid. Continue?')) return
    setRevoking(true)
    try {
      await api.delete(`/api/import/api-keys/${keyData.id}`)
      setKeyData(null)
      setShowNewKey(false)
    } catch (err) {
      alert('Failed to revoke key: ' + err.message)
    }
    setRevoking(false)
  }

  function copyKey() {
    navigator.clipboard.writeText(newKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const dateFormatter = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })

  return (
    <div className="card" style={{ padding: '20px 24px' }}>
      <h2 className="text-sm font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
        Step 1: Generate an API Key
      </h2>
      <p className="text-xs mb-4" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        The browser extension needs an API key to communicate with your JellyDJ server.
        Generate one here and paste it into the extension's settings popup.
      </p>

      {showNewKey && newKey && (
        <div className="rounded-lg p-4 mb-4" style={{ background: '#facc1520', border: '2px solid #facc15' }}>
          <p className="text-xs mb-3" style={{ color: 'var(--text-secondary)' }}>
            Copy your key now — <strong>it will not be shown again</strong>
          </p>
          <pre className="rounded-md p-3 mb-3 text-xs font-mono" style={{
            background: 'rgba(0,0,0,0.15)', border: '1px solid #999',
            color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            userSelect: 'all', margin: 0,
          }}>{newKey}</pre>
          <button onClick={copyKey} className="btn-primary text-xs" style={{
            background: copied ? '#4ade80' : '#facc15', color: '#000',
          }}>
            {copied ? 'Copied!' : 'Copy to Clipboard'}
          </button>
        </div>
      )}

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Loading...</p>
      ) : keyData ? (
        <div>
          <div className="rounded-lg p-3 mb-3" style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          }}>
            <div className="flex items-center gap-3 mb-2">
              <code className="px-2 py-1 rounded text-xs font-mono font-semibold" style={{
                background: 'rgba(0,0,0,0.1)', color: '#6366f1',
              }}>
                {keyData.prefix}...
              </code>
              <span className="text-xs" style={{ color: '#4ade80' }}>Active</span>
            </div>
            <div className="text-[10px]" style={{ color: 'var(--text-muted)', lineHeight: 1.8 }}>
              {keyData.created_at && <div>Created: {dateFormatter.format(new Date(keyData.created_at))}</div>}
              {keyData.last_used_at && <div>Last used: {dateFormatter.format(new Date(keyData.last_used_at))}</div>}
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={handleReroll} disabled={generating} className="btn-secondary text-xs">
              {generating ? '...' : 'Reroll'}
            </button>
            <button onClick={handleRevoke} disabled={revoking} className="btn-secondary text-xs"
              style={{ borderColor: 'rgba(248,113,113,0.4)', color: 'var(--danger)' }}>
              {revoking ? '...' : 'Revoke'}
            </button>
          </div>
        </div>
      ) : (
        <button onClick={handleGenerate} disabled={generating} className="btn-primary text-xs">
          {generating ? 'Generating...' : 'Generate API Key'}
        </button>
      )}
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function ImportSetup() {
  const navigate = useNavigate()

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div className="anim-fade-up">
        <div className="flex items-center gap-3 mb-1">
          <button
            onClick={() => navigate('/import')}
            className="text-xs hover:underline"
            style={{ color: 'var(--text-muted)' }}
          >
            &larr; Back to Import
          </button>
        </div>
        <h1 style={{ fontFamily: 'Syne', fontWeight: 800, fontSize: 26, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
          Extension Setup
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          Set up the JellyDJ browser extension to import playlists with one click.
        </p>
      </div>

      {/* Step 1: API Key */}
      <div className="anim-fade-up" style={{ animationDelay: '50ms' }}>
        <ApiKeySection />
      </div>

      {/* Step 2: Download Extension */}
      <div className="anim-fade-up" style={{ animationDelay: '100ms' }}>
        <div className="card" style={{ padding: '20px 24px' }}>
          <h2 className="text-sm font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
            Step 2: Download the Extension
          </h2>
          <p className="text-xs mb-4" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Download the JellyDJ browser extension package. This works in Chrome, Edge, Brave, and any Chromium-based browser.
          </p>
          <a
            href="/jellydj-extension.zip"
            download="jellydj-extension.zip"
            className="btn-primary text-xs inline-flex items-center gap-2"
          >
            Download Extension (.zip)
          </a>
        </div>
      </div>

      {/* Step 3: Install Instructions */}
      <div className="anim-fade-up" style={{ animationDelay: '150ms' }}>
        <div className="card" style={{ padding: '20px 24px' }}>
          <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
            Step 3: Install the Extension
          </h2>

          <ol className="space-y-4" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--bg)' }}>1</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  Unzip the download
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  Extract the <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--bg-overlay)' }}>jellydj-extension.zip</code> file
                  to a folder on your computer. Remember where you put it — you'll need the path in the next step.
                </div>
              </div>
            </li>

            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--bg)' }}>2</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  Open the Extensions page
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  In Chrome, go to <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--bg-overlay)' }}>chrome://extensions</code> (or
                  in Edge: <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--bg-overlay)' }}>edge://extensions</code>).
                  You can also get there via the menu: <strong>More Tools</strong> &rarr; <strong>Extensions</strong>.
                </div>
              </div>
            </li>

            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--bg)' }}>3</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  Enable Developer Mode
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  Toggle the <strong>Developer mode</strong> switch in the top-right corner of the Extensions page. This is required to load unpacked extensions.
                </div>
              </div>
            </li>

            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--bg)' }}>4</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  Load the extension
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  Click <strong>Load unpacked</strong> and select the <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--bg-overlay)' }}>browser-extension</code> folder
                  from inside the unzipped download. The JellyDJ icon should appear in your toolbar.
                </div>
              </div>
            </li>

            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--bg)' }}>5</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  Configure the extension
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  Click the JellyDJ icon in your toolbar. In the popup, enter:
                </div>
                <ul className="mt-1.5 space-y-1">
                  <li className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    <strong style={{ color: 'var(--text-primary)' }}>Server URL</strong> — the address of your JellyDJ
                    instance (e.g. <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--bg-overlay)' }}>http://192.168.1.100:7879</code> or
                    your public domain)
                  </li>
                  <li className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    <strong style={{ color: 'var(--text-primary)' }}>API Key</strong> — paste the key you generated in Step 1
                  </li>
                </ul>
              </div>
            </li>

            <li className="flex gap-3">
              <span className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: '#4ade80', color: 'var(--bg)' }}>&#10003;</span>
              <div>
                <div className="text-xs font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  You're all set!
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  Navigate to any playlist on Spotify, Tidal, or YouTube Music and click the JellyDJ icon.
                  The extension will scrape the track list and send it to your server for matching.
                  Your imported playlists will appear on the <strong>Import</strong> page.
                </div>
              </div>
            </li>
          </ol>
        </div>
      </div>
    </div>
  )
}
