import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeDrone } from "../testing/factories/drone";
import { makeDroneStateEvent } from "../testing/factories/drone-state-event";
import { makeLiveState } from "../testing/factories/live-state";
import { fake } from "../testing/fake-socket";
import { listDrones, loginOk, server, url } from "../testing/node-server";
import { useAuthStore } from "./auth";
import { useConnectionStore } from "./connection";
import { useFleetStore } from "./fleet";

vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

beforeEach(() => {
  setActivePinia(createPinia());
  fake.reset();
});

async function signedIn() {
  server.use(loginOk);
  const auth = useAuthStore();
  await auth.login("a@skyhaul.dev", "pw");
  return auth;
}

describe("fleet store", () => {
  it("loads drones, moves a marker on drone.state, adds unknown ones, drops stale events", async () => {
    await signedIn();
    server.use(
      listDrones([makeDrone({ id: "d1" }), makeDrone({ id: "d2", state: null })]),
      http.get(url("/drones/d9"), () => HttpResponse.json(makeDrone({ id: "d9" }))),
    );
    const fleet = useFleetStore();
    await fleet.loadAll();
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.0);

    const moved = makeLiveState({ ts: "2026-01-01T00:00:20.000Z" });
    moved.position = { ...moved.position!, lat: 32.5 };
    fleet.applyState(makeDroneStateEvent({ drone_id: "d1", state: moved }));
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(
      makeDroneStateEvent({ drone_id: "d1", state: makeLiveState({ ts: "2026-01-01T00:00:05.000Z" }) }),
    );
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(makeDroneStateEvent({ drone_id: "d9" }));
    expect(fleet.drones.has("d9")).toBe(true);
  });

  it("refetches and replaces its state after a reconnect, showing Reconnecting meanwhile", async () => {
    await signedIn();
    server.use(listDrones([makeDrone({ id: "d1" })]));
    const fleet = useFleetStore();
    const connection = useConnectionStore();
    fleet.startRealtime();
    fake.handlers!.onConnect();
    expect(connection.status).toBe("live");

    fake.handlers!.onDisconnect();
    expect(connection.status).toBe("reconnecting");

    server.use(listDrones([makeDrone({ id: "d2" })]));
    fake.handlers!.onConnect();
    await vi.waitFor(() => expect([...fleet.drones.keys()]).toEqual(["d2"]));
    expect(connection.status).toBe("live");
  });

  it("logs out with Session expired when the socket answers token_expired", async () => {
    const auth = await signedIn();
    useFleetStore().startRealtime();

    fake.handlers!.onAuthError("token_expired");

    expect(auth.token).toBeNull();
    expect(auth.notice).toBe("expired");
    expect(fake.open).toBe(false);
  });
});
