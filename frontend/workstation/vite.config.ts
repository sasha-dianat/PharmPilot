import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Honour the PORT env var injected by the preview harness,
    // fall back to 3001 (matches FastAPI CORS allowlist).
    port: parseInt(process.env.PORT || '3001', 10),
    strictPort: false,
    host: true,
  },
})
