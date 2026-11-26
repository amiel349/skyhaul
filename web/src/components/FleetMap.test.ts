import L from "leaflet";
import { droneFaketory, droneStateEventFaketory, liveStateFaketory } from "../../tests/faketories";
import { page, userEvent } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import area from "../../../contracts/geo/demo-area.v1.json";
import { useFleetStore } from "../stores/fleet";
import { useMapPickStore } from "../stores/mapPick";
import { useSelectionStore } from "../stores/selection";
import { fakeDb } from "../../tests/fake-db";
import { droneEndpoints } from "../../tests/fake-endpoints";
import { worker } from "../../tests/worker";
import { renderComponent } from "../../tests/render";
import FleetMap from "./FleetMap.vue";

// Renders only FleetMap, in a real browser (real canvas, real ResizeObserver), inside a box with a size.
// The fleet store is filled through the fake GET /drones.
async function renderMap(...drones: ReturnType<typeof droneFaketory.build>[]) {
  fakeDb.drones.add(...drones);
  const view = await renderComponent(FleetMap);
  const { pinia } = view;
  // A grid box with a fixed size, so the map card stretches to fill it.
  view.container.style.cssText = "display: grid; grid-template-rows: 1fr; width: 600px; height: 400px";
  await useFleetStore(pinia).loadAll();
  return { pinia, container: view.container };
}

// A pan queues a canvas redraw on the next frames; let it run before the test unmounts the map,
// or Leaflet redraws a canvas that is already gone.
const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
const settleCanvas = async () => {
  await frame();
  await frame();
  await frame();
};

const fleetMap = () => page.getByRole("region", { name: "Fleet map" });
// A marker's label is the drone's name. Its position, link and selection exist only as data attributes on that label.
const marker = (name: string) => page.getByText(name);

const at = (lat: number, lon: number) => ({ lat, lon, altitude_m: 42, heading_deg: 0, ground_speed_mps: 5 });

describe("FleetMap", () => {
  it("draws a marker for each drone with a position, and none for one without", async () => {
    await renderMap(
      droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ position: at(32.0123, 34.8123) }) }),
      droneFaketory.build({ id: "d2", state: null }),
    );

    await expect.element(fleetMap()).toHaveAttribute("data-marker-count", "1");
    const label = marker("Drone d1");
    await expect.element(label).toHaveAttribute("data-lat", "32.0123");
    await expect.element(label).toHaveAttribute("data-lon", "34.8123");
  });

  it("highlights the marker of the selected drone", async () => {
    const { pinia } = await renderMap(droneFaketory.build({ id: "d1" }));
    await expect.element(marker("Drone d1")).toHaveAttribute("data-selected", "false");

    useSelectionStore(pinia).select("d1");

    await expect.element(marker("Drone d1")).toHaveAttribute("data-selected", "true");
  });

  it("moves a marker on an event and marks a LOST drone with a text label", async () => {
    const { pinia } = await renderMap(droneFaketory.build({ id: "d1" }));
    await expect.element(marker("Drone d1")).toHaveAttribute("data-link", "UP");

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ link: "LOST", position: at(32.5, 34.9), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );

    const label = marker("Drone d1 (link lost)");
    await expect.element(label).toHaveAttribute("data-link", "LOST");
    await expect.element(label).toMatchTextContent("link lost");
    await expect.element(label).toHaveAttribute("data-lat", "32.5");
  });

  it("keeps the followed drone centred as it moves", async () => {
    const { pinia } = await renderMap(
      droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ position: at(32.0, 34.8) }) }),
    );
    const selection = useSelectionStore(pinia);
    selection.select("d1");
    selection.setFollow(true);

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ position: at(32.01, 34.81), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );

    // Leaflet pans in whole pixels, so the centre lands within a pixel of the drone.
    const centre = (name: string) => () => Number(fleetMap().element().getAttribute(name));
    await expect.poll(centre("data-center-lat")).toBeCloseTo(32.01, 3);
    await expect.poll(centre("data-center-lon")).toBeCloseTo(34.81, 3);
    await settleCanvas();
  });

  it("turns Follow off when the map is dragged", async () => {
    const { pinia } = await renderMap(droneFaketory.build({ id: "d1" }));
    const selection = useSelectionStore(pinia);
    selection.select("d1");
    selection.setFollow(true);
    const map = fleetMap();
    await expect.element(map).toBeInTheDocument();
    // Leaflet's own pane has no role or text, so it is the one element found by class.
    const pane = map.element().querySelector(".leaflet-map-pane")!;
    const mouse = (type: string, x: number, y: number, target: EventTarget) =>
      target.dispatchEvent(
        new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y, view: window }),
      );

    // Leaflet's own drag handling: press on the map, move the pointer, release.
    mouse("mousedown", 200, 200, pane);
    mouse("mousemove", 230, 215, pane);
    mouse("mousemove", 260, 230, pane);
    await vi.waitFor(() => expect(selection.follow).toBe(false)); // Leaflet fired dragstart
    mouse("mouseup", 260, 230, pane);

    await settleCanvas();
  });

  it("does not re-centre or redraw the followed drone when another drone updates", async () => {
    const { pinia } = await renderMap(
      droneFaketory.build({ id: "d1", name: "Alpha", state: liveStateFaketory.build({ position: at(32.0, 34.8) }) }),
      droneFaketory.build({ id: "d2", name: "Bravo", state: liveStateFaketory.build({ position: at(32.1, 34.9) }) }),
    );
    const selection = useSelectionStore(pinia);
    selection.select("d1");
    selection.setFollow(true);
    const d1Label = marker("Alpha");
    await expect.element(d1Label).toHaveAttribute("data-selected", "true");
    const before = d1Label.element();
    const pan = vi.spyOn(L.Map.prototype, "panTo");
    pan.mockClear();

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d2",
        state: liveStateFaketory.build({ position: at(32.2, 34.95), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );

    await expect.element(marker("Bravo")).toHaveAttribute("data-lat", "32.2");
    expect(pan).not.toHaveBeenCalled();
    expect(marker("Alpha").element()).toBe(before);
    await settleCanvas();
  });

  it("in pick mode shows the crosshair and a banner, and Escape or Cancel leaves it without picking", async () => {
    const { pinia } = await renderMap(droneFaketory.build({ id: "d1" }));
    const pick = useMapPickStore(pinia);
    const banner = page.getByRole("status").filter({ hasText: "Click the map to set the target. Esc cancels." });

    pick.start();

    await expect.element(banner).toBeInTheDocument();
    expect(getComputedStyle(fleetMap().element()).cursor).toBe("crosshair");
    await userEvent.keyboard("{Escape}");
    await expect.element(banner).not.toBeInTheDocument();
    pick.start();
    await userEvent.click(page.getByRole("button", { name: "Cancel" }));
    await expect.element(banner).not.toBeInTheDocument();
    expect(pick.picked).toBeNull();
    expect(pick.target).toBeNull();
    await settleCanvas();
  });

  it("picks the clicked point, drops a target marker and ends pick mode", async () => {
    const { pinia } = await renderMap();
    const pick = useMapPickStore(pinia);
    pick.start();
    await vi.waitFor(() => expect(Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(0));
    await frame();
    await frame();

    await userEvent.click(fleetMap()); // the middle of the map: a real click

    const centre = (name: string) => Number(fleetMap().element().getAttribute(name));
    const label = page.getByText("Target", { exact: true });
    await expect.element(label).toBeInTheDocument();
    expect(Number(label.element().getAttribute("data-lat"))).toBeCloseTo(centre("data-center-lat"), 3);
    expect(Number(label.element().getAttribute("data-lon"))).toBeCloseTo(centre("data-center-lon"), 3);
    expect(pick.picked).toEqual({ lat: Number(label.element().getAttribute("data-lat")), lon: Number(label.element().getAttribute("data-lon")) });
    await expect.element(page.getByText("Click the map to set the target. Esc cancels.")).not.toBeInTheDocument();
    await settleCanvas();
  });

  it("ignores a click on the map when pick mode is off", async () => {
    const { pinia } = await renderMap();
    const pick = useMapPickStore(pinia);
    await vi.waitFor(() => expect(Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(0));
    await frame();
    await frame();

    await userEvent.click(fleetMap()); // an ordinary click, not picking

    expect(pick.picked).toBeNull();
    expect(pick.target).toBeNull();
    await expect.element(page.getByText("Target", { exact: true })).not.toBeInTheDocument();
    await settleCanvas();
  });

  it("does not pick when the map is dragged, and the map still pans", async () => {
    const { pinia } = await renderMap();
    const pick = useMapPickStore(pinia);
    pick.start();
    await vi.waitFor(() => expect(Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(0));
    await frame();
    await frame();
    const before = fleetMap().element().getAttribute("data-center-lon");

    await userEvent.pointer([
      { target: fleetMap(), coords: { x: 250, y: 200 }, keys: "[MouseLeft>]" },
      { coords: { x: 300, y: 215 } },
      { coords: { x: 350, y: 230 } },
      { keys: "[/MouseLeft]" },
    ]);

    await expect.poll(() => fleetMap().element().getAttribute("data-center-lon")).not.toBe(before);
    expect(pick.picked).toBeNull();
    expect(pick.active).toBe(true);
    await settleCanvas();
  });

  it("accepts a click outside the demo area and warns about it", async () => {
    const { pinia } = await renderMap();
    const pick = useMapPickStore(pinia);
    await vi.waitFor(() => expect(Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(0));
    const zoom = () => Number(fleetMap().element().getAttribute("data-zoom"));
    await frame();
    await frame();
    const start = zoom();
    // One zoom animation at a time: Leaflet ignores a click that lands while one runs.
    for (let i = 1; i <= 3; i++) {
      await userEvent.click(page.getByRole("button", { name: "Zoom out" }));
      await expect.poll(zoom).toBeLessThanOrEqual(start - i);
    }
    await frame();
    await frame();
    pick.start();
    const box = fleetMap().element().getBoundingClientRect();

    await userEvent.click(fleetMap(), { position: { x: box.width - 20, y: box.height - 20 } });

    await expect.element(page.getByText("Outside the demo area, the drone may refuse it")).toBeInTheDocument();
    await expect.element(page.getByText("Target", { exact: true })).toBeInTheDocument();
    await settleCanvas();
  });

  it("pauses Follow while picking and resumes it afterwards", async () => {
    const { pinia } = await renderMap(
      droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ position: at(32.0, 34.8) }) }),
    );
    const selection = useSelectionStore(pinia);
    const pick = useMapPickStore(pinia);
    selection.select("d1");
    selection.setFollow(true);
    await expect.element(marker("Drone d1")).toHaveAttribute("data-selected", "true");
    pick.start();
    const pan = vi.spyOn(L.Map.prototype, "panTo");
    pan.mockClear();

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ position: at(32.01, 34.81), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );
    await expect.element(marker("Drone d1")).toHaveAttribute("data-lat", "32.01");

    expect(pan).not.toHaveBeenCalled();
    expect(selection.follow).toBe(true);
    pick.cancel();
    await vi.waitFor(() => expect(pan).toHaveBeenCalled());
    await settleCanvas();
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderMap(droneFaketory.build({ id: "d1", name: hostile }));

    await expect.element(page.getByText(hostile, { exact: true })).toBeInTheDocument();
    expect(page.getByRole("img").elements()).toHaveLength(0);
  });

  it("loads no tiles under test", async () => {
    vi.stubEnv("VITE_TILE_URL", "https://tiles.example/{z}/{x}/{y}.png");
    const { container } = await renderMap(droneFaketory.build({ id: "d1" }));

    await expect.element(fleetMap()).toHaveAttribute("data-tile-url", "");
    expect(container.querySelector(".leaflet-tile")).toBeNull();
  });

  it("opens on the Tel Aviv area and does not re-fit when a drone moves", async () => {
    const fit = vi.spyOn(L.Map.prototype, "fitBounds");
    const { pinia } = await renderMap(
      droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ position: at(32.05, 34.79) }) }),
    );

    // The box gets its size after the mount; the map fits the area once it has one.
    await vi.waitFor(() => expect(fit).toHaveBeenCalled());
    const centre = (name: string) => () => Number(fleetMap().element().getAttribute(name));
    await expect.poll(centre("data-center-lat")).toBeCloseTo(area.center.lat, 2);
    await expect.poll(centre("data-center-lon")).toBeCloseTo(area.center.lon, 2);
    expect(centre("data-center-lat")()).toBeGreaterThan(area.bounds.lat_min);
    expect(centre("data-center-lat")()).toBeLessThan(area.bounds.lat_max);
    expect(centre("data-center-lon")()).toBeGreaterThan(area.bounds.lon_min);
    expect(centre("data-center-lon")()).toBeLessThan(area.bounds.lon_max);
    await frame();
    await frame();
    const fitsBefore = fit.mock.calls.length;

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ position: at(32.06, 34.8), ts: "2026-01-01T00:01:00.000Z" }),
      }),
    );
    await expect.element(marker("Drone d1")).toHaveAttribute("data-lat", "32.06");

    expect(fit.mock.calls.length).toBe(fitsBefore);
    await settleCanvas();
  });

  it("keeps fitting the area while the box settles, so a half-laid-out first size does not stick", async () => {
    const { container } = await renderMap(droneFaketory.build({ id: "d1", state: null }));
    container.style.width = "260px";
    container.style.height = "160px";
    await expect.poll(() => Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(0);
    await frame();
    await frame();
    const small = Number(fleetMap().element().getAttribute("data-zoom"));

    container.style.width = "900px";
    container.style.height = "600px";

    await expect.poll(() => Number(fleetMap().element().getAttribute("data-zoom"))).toBeGreaterThan(small);
    await expect.poll(() => Number(fleetMap().element().getAttribute("data-center-lon"))).toBeCloseTo(area.center.lon, 2);
    await settleCanvas();
  });

  it("re-measures itself when its box changes size", async () => {
    const invalidate = vi.spyOn(L.Map.prototype, "invalidateSize");
    const { container } = await renderMap(droneFaketory.build({ id: "d1" }));
    await expect.element(fleetMap()).toBeInTheDocument();
    invalidate.mockClear();

    container.style.width = "400px";

    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
  });

  it("stops fitting the area once the user has taken over the map", async () => {
    const fit = vi.spyOn(L.Map.prototype, "fitBounds");
    const { container } = await renderMap(droneFaketory.build({ id: "d1", state: null }));
    await vi.waitFor(() => expect(fit).toHaveBeenCalled());
    await frame();
    await frame();
    await userEvent.click(page.getByRole("button", { name: "Zoom in" })); // a hand on the map
    const fitsBefore = fit.mock.calls.length;
    const invalidate = vi.spyOn(L.Map.prototype, "invalidateSize");
    invalidate.mockClear();

    container.style.width = "450px";
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled()); // the resize was seen

    expect(fit.mock.calls.length).toBe(fitsBefore); // and did not snap the view back to the area
    await settleCanvas();
  });

  it("says there is no live data when drones are registered but none has a position, and hides it once one does", async () => {
    const { pinia } = await renderMap(droneFaketory.build({ id: "d1", state: null }));

    await expect.element(page.getByRole("status")).toMatchTextContent("No live data yet");
    await expect.element(page.getByRole("status")).toMatchTextContent("Start the simulator: make sim, then make seed");
    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({ drone_id: "d1", state: liveStateFaketory.build({ position: at(32.1, 34.9) }) }),
    );

    await expect.element(page.getByText("No live data yet")).not.toBeInTheDocument();
  });

  it("shows no notice while any drone has a position, even if another has none yet", async () => {
    await renderMap(
      droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ position: at(32.0123, 34.8123) }) }),
      droneFaketory.build({ id: "d2", state: null }),
    );

    await expect.element(fleetMap()).toHaveAttribute("data-marker-count", "1");
    await expect.element(page.getByText("No live data yet")).not.toBeInTheDocument();
  });

  it("leaves out the simulator hint in a production build", async () => {
    vi.stubEnv("DEV", false);

    await renderMap(droneFaketory.build({ id: "d1", state: null }));

    await expect.element(page.getByText("No live data yet")).toBeInTheDocument();
    await expect.element(page.getByText(/Start the simulator/)).not.toBeInTheDocument();
  });

  it("shows no notice while the first load runs, or when there are no drones", async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    worker.use(droneEndpoints.holdUntil(gate));
    fakeDb.drones.add(droneFaketory.build({ id: "d1", state: null }));
    const { pinia } = await renderComponent(FleetMap);
    const loading = useFleetStore(pinia).loadAll();
    await expect.element(page.getByText("No live data yet")).not.toBeInTheDocument();

    release();
    await loading;
    await expect.element(page.getByText("No live data yet")).toBeInTheDocument();

    fakeDb.drones.reset();
    await useFleetStore(pinia).loadAll();
    await expect.element(page.getByText("No live data yet")).not.toBeInTheDocument();
  });
});
