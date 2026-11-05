import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api to the backend; in Docker, nginx does the proxying.
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: { manualChunks: { charts: ["recharts"] } },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": process.env.VITE_API_PROXY || "http://localhost:8000" },
  },
  preview: {
    port: 4173,
    proxy: { "/api": process.env.VITE_API_PROXY || "http://localhost:8000" },
  },
});
