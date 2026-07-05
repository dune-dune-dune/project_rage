import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      // Forward /config.json to the running Python backend (HTTP).
      // WebTransport connects directly to localhost:4433 — no proxy needed for it.
      "/config.json": {
        target: "http://localhost:8080",
        changeOrigin: false,
      },
    },
  },
});
