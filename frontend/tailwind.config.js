/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        sidebar: '#0F1117',
        canvas:  '#F8FAFC',
        signal: {
          amber: '#F59E0B',
          red:   '#EF4444',
          green: '#10B981',
          blue:  '#3B82F6',
        },
      },
    },
  },
  plugins: [],
}