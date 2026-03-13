
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg:       'var(--bg)',
        surface:  'var(--bg-surface)',
        elevated: 'var(--bg-elevated)',
        border:   'var(--border)',
        accent:   'var(--accent)',
        purple:   'var(--purple)',
        danger:   'var(--danger)',
        warning:  'var(--warning)',
        muted:    'var(--text-muted)',
      },
      fontFamily: {
        sans:    ['"DM Sans"', 'ui-sans-serif', 'system-ui'],
        mono:    ['"JetBrains Mono"', 'monospace'],
        display: ['"Syne"', 'ui-sans-serif'],
      },
      borderRadius: {
        xl: '14px',
        '2xl': '18px',
      },
    },
  },
  plugins: [],
}
