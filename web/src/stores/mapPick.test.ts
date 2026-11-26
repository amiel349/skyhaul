import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import area from "../../../contracts/geo/demo-area.v1.json";
import { useMapPickStore } from "./mapPick";
import { useSelectionStore } from "./selection";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => setActivePinia(createPinia()));

describe("mapPick store", () => {
  it("starts, toggles and cancels pick mode, and a pick sets the point and marker and ends pick mode", () => {
    const pick = useMapPickStore();

    pick.start();
    expect(pick.active).toBe(true);
    pick.toggle();
    expect(pick.active).toBe(false);
    pick.start();
    pick.cancel();
    expect(pick.active).toBe(false);
    pick.start();
    pick.pick(32.1, 34.8);

    expect(pick.active).toBe(false);
    expect(pick.picked).toEqual({ lat: 32.1, lon: 34.8 });
    expect(pick.target).toEqual({ lat: 32.1, lon: 34.8 });
  });

  it("flags a target outside the demo area and not one inside it", () => {
    const pick = useMapPickStore();

    pick.setTarget({ lat: area.center.lat, lon: area.center.lon });
    expect(pick.outsideArea).toBe(false);
    pick.setTarget({ lat: area.bounds.lat_max + 1, lon: area.center.lon });
    expect(pick.outsideArea).toBe(true);
    pick.setTarget(null);
    expect(pick.outsideArea).toBe(false);
  });

  it("resets when the selected drone changes", async () => {
    const pick = useMapPickStore();
    const selection = useSelectionStore();
    selection.select("d1");
    await nextTick();
    pick.pick(32.1, 34.8);
    pick.start();

    selection.select("d2");
    await nextTick();

    expect(pick.active).toBe(false);
    expect(pick.picked).toBeNull();
    expect(pick.target).toBeNull();
  });
});
