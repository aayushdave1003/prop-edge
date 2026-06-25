import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the board is served by Vite (5173) and the FastAPI API runs on 8000.
// Proxy /api so the frontend can use same-origin relative URLs in both dev & prod.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
