import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Served by Flask under /terminal/ (the landing page owns `/`), so every
// built URL is prefixed with that base. Dev server on 5173; /api calls go to
// the Flask app on port 8000 - the port run.py and the axioma systemd unit
// bind (app.py's own __main__ uses 5000).
export default defineConfig({
  base: '/terminal/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
