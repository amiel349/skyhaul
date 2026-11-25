import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useFleetStore } from "./fleet";
import { useSelectionStore } from "./selection";

vi.mock("../realtime/socket", async () => await import("../testing/fake-socket"));

beforeEach(() => setActivePinia(createPinia()));

describe("selection store", () => {
  it("deselects a drone that is removed from the fleet", async () => {
    const fleet = useFleetStore();
    const selection = useSelectionStore();
    fleet.drones.set("d1", { id: "d1", name: "d1", state: null });
    selection.select("d1");

    fleet.drones.delete("d1");
    fleet.drones.set("d9", { id: "d9", name: "x", state: null }); // triggers the watcher

    await vi.waitFor(() => expect(selection.selectedId).toBeNull());
  });
});
