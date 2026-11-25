import { createPinia } from "pinia";
import { page } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { makeDrone } from "../testing/factories/drone";
import { makeLiveState } from "../testing/factories/live-state";
import { worker } from "../testing/worker";
import { fakeDb, fakeEndpoints } from "./DronePanel.fakes";
import DronePanel from "./DronePanel.vue";

// Renders only DronePanel; the fleet store is filled through the fake GET /drones.
async function renderPanel(selected: string | null, ...drones: ReturnType<typeof makeDrone>[]) {
  fakeDb.add(...drones);
  worker.use(...fakeEndpoints);
  const pinia = createPinia();
  await render(DronePanel, { global: { plugins: [pinia] } });
  await useFleetStore(pinia).loadAll();
  useSelectionStore(pinia).select(selected);
}

describe("DronePanel", () => {
  it("asks to select a drone and shows an empty command log", async () => {
    await renderPanel(null, makeDrone());

    await expect.element(page.getByTestId("drone-panel-empty")).toHaveTextContent("Select a drone");
    await expect.element(page.getByTestId("command-log-empty")).toHaveTextContent("No commands yet");
  });

  it("shows the selected drone's battery, altitude, position and link, with commands disabled", async () => {
    const state = makeLiveState({
      battery_pct: 64,
      position: { lat: 32.0123, lon: 34.8123, altitude_m: 42, heading_deg: 0, ground_speed_mps: 5 },
    });
    await renderPanel("d1", makeDrone({ id: "d1", state }));

    await expect.element(page.getByTestId("drone-panel-battery")).toHaveTextContent("64%");
    await expect.element(page.getByTestId("drone-panel-altitude")).toHaveTextContent("42 m");
    await expect.element(page.getByTestId("drone-panel-position")).toHaveTextContent("32.01230, 34.81230");
    await expect.element(page.getByTestId("drone-panel-link")).toHaveTextContent("UP");
    for (const id of ["take-off", "return-home", "land"]) {
      await expect.element(page.getByTestId(`drone-panel-cmd-${id}`)).toBeDisabled();
    }
  });

  it("says No data for a drone that has not reported a position", async () => {
    await renderPanel("d3", makeDrone({ id: "d3", state: null }));

    await expect.element(page.getByTestId("drone-panel-position")).toHaveTextContent("No data");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderPanel("d1", makeDrone({ id: "d1", name: hostile }));

    const name = page.getByTestId("drone-panel-name");
    await expect.element(name).toHaveTextContent(hostile);
    expect(name.element().querySelector("img")).toBeNull();
  });
});
