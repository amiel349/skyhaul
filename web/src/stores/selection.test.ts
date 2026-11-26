import { createPinia, setActivePinia } from "pinia";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeDrone } from "../testing/factories/drone";
import { server, url } from "../testing/node-server";
import { useFleetStore } from "./fleet";
import { useSelectionStore } from "./selection";

vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

beforeEach(() => setActivePinia(createPinia()));

describe("selection store", () => {
  it("deselects a drone that is removed from the fleet, and says why", async () => {
    const fleet = useFleetStore();
    const selection = useSelectionStore();
    fleet.drones.set("d1", { id: "d1", name: "d1", state: null });
    selection.select("d1");

    fleet.drones.delete("d1");
    fleet.drones.set("d9", { id: "d9", name: "x", state: null }); // triggers the watcher

    await vi.waitFor(() => expect(selection.selectedId).toBeNull());
    expect(selection.notice).toBe("Drone d1 was removed");
  });

  it("keeps Follow only for the drone it was turned on for", () => {
    const fleet = useFleetStore();
    const selection = useSelectionStore();
    fleet.drones.set("d1", { id: "d1", name: "d1", state: null });
    fleet.drones.set("d2", { id: "d2", name: "d2", state: null });

    selection.setFollow(true);
    expect(selection.follow).toBe(false); // nothing selected

    selection.select("d1");
    selection.setFollow(true);
    selection.select("d1");
    expect(selection.follow).toBe(true);

    selection.select("d2");
    expect(selection.follow).toBe(false);
  });

  it("opens a drone by id: loads an unknown one from the API, flags a 404", async () => {
    server.use(
      http.get(url("/drones/d9"), () => HttpResponse.json(makeDrone({ id: "d9" }))),
      http.get(url("/drones/nope"), () => new HttpResponse(null, { status: 404 })),
    );
    const selection = useSelectionStore();

    await selection.open("d9");
    expect(selection.selectedId).toBe("d9");

    await selection.open("nope");
    expect(selection.selectedId).toBeNull();
    expect(selection.notFoundId).toBe("nope");
  });
});
