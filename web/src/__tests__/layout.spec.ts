import { fireEvent, render, screen } from "@testing-library/vue";
import L from "leaflet";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LAYOUT_KEY } from "../layout/useResizable";
import { useAuthStore } from "../stores/auth";
import FleetView from "../views/FleetView.vue";
import { listDrones, loginOk, server } from "./server";

vi.mock("../realtime/socket", async () => await import("./fakeSocket"));

let observed: (() => void) | null = null;

beforeEach(() => {
  localStorage.clear();
  observed = null;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(cb: () => void) {
        observed = cb;
      }
      observe(): void {}
      disconnect(): void {}
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

async function mountView() {
  server.use(loginOk, listDrones([]));
  const pinia = createPinia();
  setActivePinia(pinia);
  await useAuthStore().login("a@skyhaul.dev", "pw");
  return render(FleetView, { global: { plugins: [pinia] } });
}
const now = (id: string) => Number(screen.getByTestId(id).getAttribute("aria-valuenow"));

describe("resizable panels", () => {
  it("moves with the keyboard within limits, and Home/End/Enter jump and reset", async () => {
    await mountView();
    const bar = screen.getByTestId("splitter-fleet-map");
    expect(bar.getAttribute("role")).toBe("separator");
    expect(bar.getAttribute("aria-orientation")).toBe("vertical");
    expect(now("splitter-fleet-map")).toBe(270);

    await fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(now("splitter-fleet-map")).toBe(286);
    await fireEvent.keyDown(bar, { key: "ArrowLeft", shiftKey: true });
    expect(now("splitter-fleet-map")).toBe(222);
    await fireEvent.keyDown(bar, { key: "Home" });
    expect(now("splitter-fleet-map")).toBe(200);
    await fireEvent.keyDown(bar, { key: "ArrowLeft" });
    expect(now("splitter-fleet-map")).toBe(200);
    await fireEvent.keyDown(bar, { key: "End" });
    expect(now("splitter-fleet-map")).toBe(Number(bar.getAttribute("aria-valuemax")));
    await fireEvent.keyDown(bar, { key: "Enter" });
    expect(now("splitter-fleet-map")).toBe(270);

    // The right panel grows when its divider moves left.
    const panel = screen.getByTestId("splitter-map-panel");
    await fireEvent.keyDown(panel, { key: "ArrowLeft" });
    expect(now("splitter-map-panel")).toBe(346);
  });

  it("changes the map height with a pointer drag", async () => {
    await mountView();
    const bar = screen.getByTestId("splitter-map-alerts");
    await fireEvent.pointerDown(bar, { clientY: 500, pointerId: 1 });
    await fireEvent.pointerMove(bar, { clientY: 450, pointerId: 1 });
    expect(now("splitter-map-alerts")).toBe(370);
    await fireEvent.pointerUp(bar, { pointerId: 1 });
    await fireEvent.pointerMove(bar, { clientY: 300, pointerId: 1 });
    expect(now("splitter-map-alerts")).toBe(370);
  });

  it("saves sizes, restores them, and ignores bad stored data", async () => {
    const first = await mountView();
    await fireEvent.keyDown(screen.getByTestId("splitter-map-alerts"), { key: "ArrowUp" });
    await vi.waitFor(() =>
      expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}")).toEqual({ fleet: 270, panel: 330, map: 404 }),
    );
    first.unmount();

    const second = await mountView();
    expect(now("splitter-map-alerts")).toBe(404);
    second.unmount();

    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ fleet: "wide", panel: 9999, map: 1 }));
    await mountView();
    expect(now("splitter-fleet-map")).toBe(270);
    expect(now("splitter-map-alerts")).toBe(420);
  });

  it("re-measures the map when its container changes size", async () => {
    const spy = vi.spyOn(L.Map.prototype, "invalidateSize");
    await mountView();
    spy.mockClear();
    observed?.();
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
});
