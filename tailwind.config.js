/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        surface2: 'var(--surface-2)',
        border: 'var(--border)',
        accent: 'var(--accent)',
        success: 'var(--success)',
        warn: 'var(--warn)',
        danger: 'var(--danger)',
        muted: 'var(--muted)',
      },
      fontFamily: {
        han: ['Noto Sans SC', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
