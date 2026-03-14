import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      }
    }
  },
  preview: {
    host: '0.0.0.0',
    port: 3000,
  },
  build: {
    outDir: 'dist',
    // Disable the inline modulepreload polyfill script that Vite injects into
    // index.html by default.  The polyfill is only needed for very old browsers
    // (pre-2022 Safari / Firefox) that don't support <link rel="modulepreload">.
    // Disabling it means no inline <script> tag is emitted, which lets us drop
    // 'unsafe-inline' from the script-src CSP directive entirely.
    // Modern browsers (Chrome 66+, Firefox 115+, Safari 17+) are unaffected.
    modulePreload: { polyfill: false },
  }
})
