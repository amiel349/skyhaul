import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./node-server";
import { runResets } from "./reset";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  runResets(); // fake dbs a unit test imported (sessions, users) start empty again
});
afterAll(() => server.close());
