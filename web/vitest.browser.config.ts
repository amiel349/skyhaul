import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

// Component tests: each one renders a single component in Playwright's Chromium (Vitest browser mode).
// Headless by default (what CI runs). VITEST_SLOWMO=<ms> slows every browser action down so a
// visible run can be followed; see `pnpm test:components:watch`.
const slowMo = Number(process.env.VITEST_SLOWMO ?? 0);

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@contracts": fileURLToPath(new URL("../contracts/ts", import.meta.url)) },
  },
  // mockServiceWorker.js (for the MSW worker) lives next to the test setup, not in the app's public dir.
  publicDir: "tests/public",
  // Pre-bundle what the tests import so Vite does not discover it mid-run and reload the page.
  optimizeDeps: { include: ["openapi-fetch", "socket.io-client", "leaflet", "pinia", "vue-router"] },
  test: {
    include: ["src/{components,views}/[A-Z]*.test.ts", "src/App.test.ts"],
    setupFiles: ["./tests/setup-browser.ts"],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright({ launchOptions: slowMo > 0 ? { slowMo } : {} }),
      // Wide enough for the three-column layout limits (the default viewport is phone sized).
      instances: [{ browser: "chromium", viewport: { width: 1280, height: 800 } }],
    },
  },
});
