import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Body } from "../../tests/api";
import { makeCommand } from "../../tests/factories/command";
import { fake } from "../../tests/fake-socket";
import { loginOk, server, url } from "../../tests/node-server";
import { useAuthStore } from "./auth";
import { useCommandsStore } from "./commands";
import { useFleetStore } from "./fleet";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => {
  setActivePinia(createPinia());
  fake.reset();
});

const status = (id: string, over: Parameters<typeof makeCommand>[0] = {}) => makeCommand({ id, ...over });

describe("commands store", () => {
  it("merges command.updated by id: a late reply wins over TIMED_OUT, an older status never wins", () => {
    const store = useCommandsStore();
    store.applyUpdate(status("c1", { status: "SENT" }));
    store.applyUpdate(status("c1", { status: "TIMED_OUT" }));
    store.applyUpdate(status("c1", { status: "SENT" })); // older, ignored
    expect(store.forDrone("d1").map((c) => c.status)).toEqual(["TIMED_OUT"]);

    store.applyUpdate(status("c1", { status: "ACKED", late: true }));

    expect(store.forDrone("d1")).toHaveLength(1);
    expect(store.forDrone("d1")[0]).toMatchObject({ status: "ACKED", late: true });
  });

  it("adds no row for a duplicate event", () => {
    const store = useCommandsStore();
    store.applyUpdate(status("c1", { status: "ACKED" }));
    store.applyUpdate(status("c1", { status: "ACKED" }));

    expect(store.forDrone("d1")).toHaveLength(1);
  });

  it("merges an event that arrives before the 202 answer and keeps the newer status", async () => {
    server.use(
      http.post(url("/drones/d1/commands"), () => {
        // The reply event is processed while the request is still being answered.
        useCommandsStore().applyUpdate(status("c1", { status: "ACKED" }));
        return HttpResponse.json(status("c1", { status: "SENT" }), { status: 202 });
      }),
    );
    const store = useCommandsStore();

    const result = await store.send("d1", "ARM", {});

    expect(result.ok).toBe(true);
    expect(store.forDrone("d1")).toHaveLength(1);
    expect(store.forDrone("d1")[0]?.status).toBe("ACKED");
  });

  it("loads the history newest first", async () => {
    server.use(
      http.get(url("/drones/d1/commands"), () =>
        HttpResponse.json({
          items: [
            status("a", { created_at: "2026-01-01T00:00:00Z" }),
            status("b", { created_at: "2026-01-01T00:05:00Z" }),
          ],
          next_cursor: null,
        }),
      ),
    );
    const store = useCommandsStore();

    await store.load("d1");

    expect(store.forDrone("d1").map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("sends {type, payload} per command type", async () => {
    const bodies: Body<"/drones/{drone_id}/commands", "post">[] = [];
    server.use(
      http.post(url("/drones/d1/commands"), async ({ request }) => {
        bodies.push((await request.json()) as Body<"/drones/{drone_id}/commands", "post">);
        return HttpResponse.json(status(`c${bodies.length}`), { status: 202 });
      }),
    );
    const store = useCommandsStore();

    await store.send("d1", "ARM", {});
    await store.send("d1", "TAKEOFF", { altitude_m: 30 });
    await store.send("d1", "GOTO", { lat: 32, lon: 34, altitude_m: 40 });
    await store.send("d1", "SET_SPEED", { speed_mps: 8 });

    expect(bodies).toEqual([
      { type: "ARM", payload: {} },
      { type: "TAKEOFF", payload: { altitude_m: 30 } },
      { type: "GOTO", payload: { lat: 32, lon: 34, altitude_m: 40 } },
      { type: "SET_SPEED", payload: { speed_mps: 8 } },
    ]);
  });

  it("reuses the Idempotency-Key when the same attempt is retried, and a new one after success", async () => {
    const keys: string[] = [];
    let fail = true;
    server.use(
      http.post(url("/drones/d1/commands"), ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key") ?? "");
        if (fail) return HttpResponse.json({ error: "command_not_sent", detail: "x" }, { status: 503 });
        return HttpResponse.json(status("c1"), { status: 202 });
      }),
    );
    const store = useCommandsStore();

    const first = await store.send("d1", "TAKEOFF", { altitude_m: 30 });
    fail = false;
    await store.send("d1", "TAKEOFF", { altitude_m: 30 });
    await store.send("d1", "TAKEOFF", { altitude_m: 30 });

    expect(first).toMatchObject({ ok: false, kind: "not_sent" });
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("is emptied on logout, and a command.updated event reaches it through the fleet store", async () => {
    server.use(loginOk);
    const auth = useAuthStore();
    await auth.login("a@skyhaul.dev", "pw");
    useFleetStore().startRealtime();
    fake.handlers?.onCommandUpdated(status("c1", { status: "ACKED" }));
    const store = useCommandsStore();
    expect(store.forDrone("d1")).toHaveLength(1);

    auth.logout();

    expect(store.forDrone("d1")).toHaveLength(0);
  });
});
