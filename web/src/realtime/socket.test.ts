import { describe, expect, it, vi } from "vitest";

const io = vi.hoisted(() =>
  vi.fn(() => ({ on: vi.fn(), disconnect: vi.fn(), removeAllListeners: vi.fn() })),
);
vi.mock("socket.io-client", () => ({ io }));

import { openFleetSocket } from "./socket";

describe("fleet socket", () => {
  it("sends the token in auth, never in the URL", () => {
    openFleetSocket(() => "tok-1", {
      onConnect: vi.fn(),
      onDisconnect: vi.fn(),
      onDroneState: vi.fn(),
      onAuthError: vi.fn(),
    });

    const [target, options] = io.mock.calls[0] as unknown as [
      string,
      { auth: (cb: (data: unknown) => void) => void },
    ];
    expect(target).toBe("/fleet");
    expect(target).not.toContain("tok-1");
    const cb = vi.fn();
    options.auth(cb);
    expect(cb).toHaveBeenCalledWith({ token: "tok-1" });
  });
});
