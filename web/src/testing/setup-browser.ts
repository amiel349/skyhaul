// Global hooks for component tests (Vitest browser project). No test data, no mount helper.
import { createPinia, setActivePinia } from "pinia";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import "../styles/global.css";
import { runResets } from "./reset";
import { worker } from "./worker";

const unhandled: string[] = [];
let errors: unknown[][] = [];

beforeAll(async () => {
  await worker.start({
    serviceWorker: { url: "/mockServiceWorker.js" },
    quiet: true,
    async onUnhandledFrame({ frame, defaults }) {
      // Only API calls matter here; Vite's own module requests pass through untouched.
      const message = await frame.getUnhandledMessage();
      if (!message.includes("/api/")) return;
      unhandled.push(message);
      defaults.error();
    },
  });
});
afterAll(() => worker.stop());

beforeEach(() => {
  unhandled.length = 0;
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  worker.resetHandlers();
  runResets();
  setActivePinia(createPinia());
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.removeAttribute("data-theme");

  if (unhandled.length > 0) throw new Error(`Request with no fake endpoint: ${unhandled.join(", ")}`);
  if (errors.length > 0) throw new Error(`console.error during the test: ${String(errors[0]?.[0])}`);
});
