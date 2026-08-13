import path from "node:path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    // Same-origin in production (FastAPI serves the built bundle at
    // /cockpit), but in dev the Vite server runs on its own port -- proxy
    // /api so the app never needs CORS handling on the backend.
    proxy: {
      "/api": {
        target: "http://localhost:8811",
        changeOrigin: true,
      },
    },
  },
  base: "/cockpit/",
})
