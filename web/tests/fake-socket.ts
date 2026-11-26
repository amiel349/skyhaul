// Stands in for realtime/socket.ts: tests drive the handlers the fleet store registered.
// Use it with: vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));
import type { FleetSocketHandlers } from "../src/realtime/socket";

export const fake = {
  handlers: null as FleetSocketHandlers | null,
  open: false,
  reset(): void {
    this.handlers = null;
    this.open = false;
  },
};

export function openFleetSocket(_getToken: () => string | null, handlers: FleetSocketHandlers): void {
  fake.handlers = handlers;
  fake.open = true;
}

export function closeFleetSocket(): void {
  fake.open = false;
}
