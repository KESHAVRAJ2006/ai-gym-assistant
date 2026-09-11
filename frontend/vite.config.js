import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dev-only proxy so the browser talks to one origin and CORS never
    // appears during development. Production uses VITE_API_BASE instead.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    // The MediaPipe wasm blobs are ~12 MB each and live in public/, so they
    // are copied verbatim and never bundled. Raise the warning limit so the
    // build output stays readable.
    chunkSizeWarningLimit: 1200,
  },
});
