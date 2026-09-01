import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API and both WebSockets are proxied so the browser sees one origin.
// Without the ws proxy the live socket fails CORS preflight in dev only, which
// is a confusing way to lose an afternoon.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
