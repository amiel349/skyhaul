import { createPinia, setActivePinia } from "pinia";
import { droneFaketory, droneStateEventFaketory, liveStateFaketory } from "../../tests/faketories";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fake } from "../../tests/fake-socket";
import { listDrones, loginOk, server, url } from "../../tests/node-server";
import { useAuthStore } from "./auth";
import { useConnectionStore } from "./connection";
import { useFleetStore } from "./fleet";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

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
      listDrones([droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2", state: null })]),
      http.get(url("/drones/d9"), () => HttpResponse.json(droneFaketory.build({ id: "d9" }))),
    );
    const fleet = useFleetStore();
    await fleet.loadAll();
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.0);

    const moved = liveStateFaketory.build({ ts: "2026-01-01T00:00:20.000Z" });
    moved.position = { ...moved.position!, lat: 32.5 };
    fleet.applyState(droneStateEventFaketory.build({ drone_id: "d1", state: moved }));
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(
      droneStateEventFaketory.build({ drone_id: "d1", state: liveStateFaketory.build({ ts: "2026-01-01T00:00:05.000Z" }) }),
    );
    expect(fleet.drones.get("d1")?.state?.position?.lat).toBe(32.5);

    fleet.applyState(droneStateEventFaketory.build({ drone_id: "d9" }));
    expect(fleet.drones.has("d9")).toBe(true);
  });

  it("refetches and replaces its state after a reconnect, showing Reconnecting meanwhile", async () => {
    await signedIn();
    server.use(listDrones([droneFaketory.build({ id: "d1" })]));
    const fleet = useFleetStore();
    const connection = useConnectionStore();
    fleet.startRealtime();
    fake.handlers!.onConnect();
    expect(connection.status).toBe("live");

    fake.handlers!.onDisconnect();
    expect(connection.status).toBe("reconnecting");

    server.use(listDrones([droneFaketory.build({ id: "d2" })]));
    fake.handlers!.onConnect();
    await vi.waitFor(() => expect([...fleet.drones.keys()]).toEqual(["d2"]));
    expect(connection.status).toBe("live");
  });

  it("keeps the list order when a state arrives, and puts a new drone in id order", () => {
    const fleet = useFleetStore();
    fleet.drones.set("d2", { id: "d2", name: "b", state: null });
    fleet.drones.set("d1", { id: "d1", name: "a", state: null });
    expect(fleet.list.map((d) => d.id)).toEqual(["d1", "d2"]);

    fleet.applyState(droneStateEventFaketory.build({ drone_id: "d2" }));
    expect(fleet.list.map((d) => d.id)).toEqual(["d1", "d2"]);
  });

  it("loads one drone by id, and reports a 404 as not_found", async () => {
    await signedIn();
    server.use(
      http.get(url("/drones/d9"), () => HttpResponse.json(droneFaketory.build({ id: "d9", name: "Nine" }))),
      http.get(url("/drones/nope"), () => new HttpResponse(null, { status: 404 })),
    );
    const fleet = useFleetStore();

    expect(await fleet.loadOne("d9")).toBe("ok");
    expect(fleet.drones.get("d9")?.name).toBe("Nine");
    expect(await fleet.loadOne("nope")).toBe("not_found");
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
