import L from "leaflet";
import { createPinia } from "pinia";
import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it, vi } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { makeDrone } from "../testing/factories/drone";
import { makeDroneStateEvent } from "../testing/factories/drone-state-event";
import { makeLiveState } from "../testing/factories/live-state";
import { worker } from "../testing/worker";
import { fakeDb, fakeEndpoints } from "./FleetMap.fakes";
import FleetMap from "./FleetMap.vue";

// Renders only FleetMap, in a real browser (real canvas, real ResizeObserver), inside a box with a size.
// The fleet store is filled through the fake GET /drones.
async function renderMap(...drones: ReturnType<typeof makeDrone>[]) {
  fakeDb.add(...drones);
  worker.use(...fakeEndpoints);
  const pinia = createPinia();
  const view = await render(FleetMap, { global: { plugins: [pinia] } });
  // A grid box with a fixed size, so the map card stretches to fill it.
  view.container.style.cssText = "display: grid; grid-template-rows: 1fr; width: 600px; height: 400px";
  await useFleetStore(pinia).loadAll();
  return { pinia, container: view.container };
}

const at = (lat: number, lon: number) => ({ lat, lon, altitude_m: 42, heading_deg: 0, ground_speed_mps: 5 });

describe("FleetMap", () => {
  it("draws a marker for each drone with a position, and none for one without", async () => {
    await renderMap(
      makeDrone({ id: "d1", state: makeLiveState({ position: at(32.0123, 34.8123) }) }),
      makeDrone({ id: "d2", state: null }),
    );

    await expect.element(page.getByTestId("fleet-map")).toHaveAttribute("data-marker-count", "1");
    const label = page.getByTestId("fleet-marker-label-d1");
    await expect.element(label).toHaveAttribute("data-lat", "32.0123");
    await expect.element(label).toHaveAttribute("data-lon", "34.8123");
  });

  it("highlights the marker of the selected drone", async () => {
    const { pinia } = await renderMap(makeDrone({ id: "d1" }));
    await expect.element(page.getByTestId("fleet-marker-label-d1")).toHaveAttribute("data-selected", "false");

    useSelectionStore(pinia).select("d1");

    await expect.element(page.getByTestId("fleet-marker-label-d1")).toHaveAttribute("data-selected", "true");
  });

  it("moves a marker on an event and marks a LOST drone with a text label", async () => {
    const { pinia } = await renderMap(makeDrone({ id: "d1" }));
    await expect.element(page.getByTestId("fleet-marker-label-d1")).toHaveAttribute("data-link", "UP");

    useFleetStore(pinia).applyState(
      makeDroneStateEvent({
        drone_id: "d1",
        state: makeLiveState({ link: "LOST", position: at(32.5, 34.9), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );

    const label = page.getByTestId("fleet-marker-label-d1");
    await expect.element(label).toHaveAttribute("data-link", "LOST");
    await expect.element(label).toMatchTextContent("link lost");
    await expect.element(label).toHaveAttribute("data-lat", "32.5");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const { container } = await renderMap(makeDrone({ id: "d1", name: hostile }));

    const label = page.getByTestId("fleet-marker-label-d1");
    await expect.element(label).toHaveTextContent(hostile);
    expect(container.querySelector("img")).toBeNull();
  });

  it("loads no tiles under test", async () => {
    vi.stubEnv("VITE_TILE_URL", "https://tiles.example/{z}/{x}/{y}.png");
    const { container } = await renderMap(makeDrone({ id: "d1" }));

    await expect.element(page.getByTestId("fleet-map")).toHaveAttribute("data-tile-url", "");
    expect(container.querySelector(".leaflet-tile")).toBeNull();
  });

  it("re-measures itself when its box changes size", async () => {
    const invalidate = vi.spyOn(L.Map.prototype, "invalidateSize");
    const { container } = await renderMap(makeDrone({ id: "d1" }));
    await expect.element(page.getByTestId("fleet-map")).toBeInTheDocument();
    invalidate.mockClear();

    container.style.width = "400px";

    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
  });
});
