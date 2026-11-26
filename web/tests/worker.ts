// The fake API (MSW worker). Tests add a component's endpoints with `worker.use(...)`.
// Kept out of setup-browser.ts on purpose: a test that imported the setup file would run its hooks twice.
import { setupWorker } from "msw/browser";

export const worker = setupWorker();
