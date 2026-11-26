import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeLiveState } from "../../tests/factories/live-state";
import { useFleetStore } from "./fleet";
import { useFleetFilterStore } from "./fleetFilter";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => setActivePinia(createPinia()));

function fill() {
  const fleet = useFleetStore();
  fleet.drones.set("gw0-d001", { id: "gw0-d001", name: "Alpha", state: makeLiveState({ link: "UP" }) });
  fleet.drones.set("gw0-d002", { id: "gw0-d002", name: "Bravo", state: makeLiveState({ link: "LOST" }) });
  fleet.drones.set("gw0-d003", { id: "gw0-d003", name: "Charlie", state: null });
  return fleet;
}

describe("fleet filter store", () => {
  it("matches name or id as a case-insensitive substring", () => {
    fill();
    const filter = useFleetFilterStore();

    filter.query = "ALPH";
    expect(filter.visible.map((d) => d.id)).toEqual(["gw0-d001"]);

    filter.query = "D002";
    expect(filter.visible.map((d) => d.id)).toEqual(["gw0-d002"]);

    filter.query = "zzz";
    expect(filter.visible).toEqual([]);
  });

  it("filters by link; a drone without telemetry matches neither UP nor LOST", () => {
    fill();
    const filter = useFleetFilterStore();

    filter.link = "LOST";
    expect(filter.visible.map((d) => d.id)).toEqual(["gw0-d002"]);
    filter.link = "UP";
    expect(filter.visible.map((d) => d.id)).toEqual(["gw0-d001"]);
    filter.link = "ALL";
    expect(filter.visible).toHaveLength(3);
  });
});
