/**
 * vite.config.ts
 *
 * Purpose:        Dev server + build config for the CodeXray UI.
 * Responsibility: Proxy `/api` to the local FastAPI backend (default :8000) so
 *                 the browser makes same-origin requests in dev, and emit a
 *                 static bundle into `dist/` for `uvicorn` / any static host.
 * Notes:          Backend origin is overridable with CODEXRAY_API_URL.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = process.env.CODEXRAY_API_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/health": { target: API, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
