import { cleanup } from "@testing-library/vue";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

// jsdom has no canvas; Leaflet's canvas renderer needs a 2d context to draw into.
HTMLCanvasElement.prototype.getContext = (() =>
  new Proxy({}, { get: () => () => undefined })) as unknown as HTMLCanvasElement["getContext"];

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
