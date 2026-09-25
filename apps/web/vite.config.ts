import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

// The hub's contract server, as `kinby hub` starts it by default.
const hub = "http://127.0.0.1:8080"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/ws": { target: hub, ws: true },
      "/auth": hub,
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
})
