import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // the app calls /api/... on its own origin in dev; Vite forwards those to
    // FastAPI, so there is no CORS preflight and no hardcoded backend URL
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
