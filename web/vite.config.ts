import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

// The API has no CORS: the dev server proxies /api and /socket.io to it, so the browser
// sees one origin (design note section 8).
const target = process.env.SKYHAUL_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@contracts": fileURLToPath(new URL("../contracts/ts", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": { target, changeOrigin: true },
      // No changeOrigin here: the API only accepts a WebSocket whose Origin matches its Host, so
      // the proxy must forward the browser's Host (localhost:5173) together with its Origin.
      "/socket.io": { target, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
  },
});
