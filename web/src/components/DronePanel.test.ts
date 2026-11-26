import { createPinia } from "pinia";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { makeDrone } from "../testing/factories/drone";
import { makeDroneStateEvent } from "../testing/factories/drone-state-event";
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
  return { pinia, fleet: useFleetStore(pinia), selection: useSelectionStore(pinia) };
}

const text = (id: string) => page.getByTestId(`drone-panel-${id}`);

describe("DronePanel", () => {
  it("asks to select a drone and shows an empty command log", async () => {
    await renderPanel(null, makeDrone());

    await expect.element(page.getByTestId("drone-panel-empty")).toHaveTextContent("Select a drone");
    await expect.element(page.getByTestId("command-log-empty")).toHaveTextContent("No commands yet");
  });

  it("shows every detail of the selected drone, with commands disabled", async () => {
    const state = makeLiveState({
      flight_state: "HOVERING",
      armed: true,
      battery_pct: 64,
      est_remaining_s: 725,
      position: { lat: 32.0123, lon: 34.8123, altitude_m: 42, heading_deg: 90, ground_speed_mps: 7 },
      target: { lat: 32.5, lon: 34.9 },
      mission_id: "6f1c1c2e-0000-4000-8000-000000000001",
      last_heartbeat_at: "2026-01-01T00:00:09Z",
    });
    await renderPanel("d1", makeDrone({ id: "d1", name: "Alpha", state }));

    await expect.element(text("name")).toHaveTextContent("Alpha");
    await expect.element(text("id")).toHaveTextContent("d1");
    await expect.element(text("flight-state")).toHaveTextContent("HOVERING");
    await expect.element(text("armed")).toHaveTextContent("Yes");
    await expect.element(text("battery")).toHaveTextContent("64%");
    await expect.element(text("remaining")).toHaveTextContent("12 min 5 s");
    await expect.element(text("link")).toHaveTextContent("UP");
    await expect.element(text("heartbeat")).toHaveTextContent("2026-01-01 00:00:09 UTC");
    await expect.element(text("position")).toHaveTextContent("32.01230, 34.81230");
    await expect.element(text("altitude")).toHaveTextContent("42 m");
    await expect.element(text("heading")).toHaveTextContent("90°");
    await expect.element(text("speed")).toHaveTextContent("7 m/s");
    await expect.element(text("target")).toHaveTextContent("32.50000, 34.90000");
    await expect.element(text("mission")).toHaveTextContent("6f1c1c2e-0000-4000-8000-000000000001");
    for (const id of ["take-off", "return-home", "land"]) {
      await expect.element(page.getByTestId(`drone-panel-cmd-${id}`)).toBeDisabled();
    }
  });

  it("updates the values when a drone.state event arrives for the open drone", async () => {
    const { fleet } = await renderPanel("d1", makeDrone({ id: "d1" }));
    await expect.element(text("battery")).toHaveTextContent("80%");

    fleet.applyState(
      makeDroneStateEvent({
        drone_id: "d1",
        state: makeLiveState({ battery_pct: 55, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(text("battery")).toHaveTextContent("55%");
  });

  it("says No data, never null or 0, for a drone that has not reported yet", async () => {
    await renderPanel("d3", makeDrone({ id: "d3", state: null }));

    for (const id of [
      "flight-state",
      "armed",
      "battery",
      "remaining",
      "heartbeat",
      "position",
      "altitude",
      "heading",
      "speed",
      "target",
      "mission",
    ]) {
      await expect.element(text(id)).toHaveTextContent("No data");
    }
    await expect.element(text("link")).toHaveTextContent("NO DATA");
  });

  it("shows LOW under 20% and CRITICAL under 10% in words", async () => {
    const { fleet } = await renderPanel("d1", makeDrone({ id: "d1", state: makeLiveState({ battery_pct: 15 }) }));
    await expect.element(text("battery")).toHaveTextContent("LOW 15%");
    await expect.element(text("battery")).toHaveAttribute("data-critical", "false");

    fleet.applyState(
      makeDroneStateEvent({
        drone_id: "d1",
        state: makeLiveState({ battery_pct: 7, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(text("battery")).toHaveTextContent("CRITICAL 7%");
    await expect.element(text("battery")).toHaveAttribute("data-critical", "true");
  });

  it("shows Last seen and marks the values stale when the link is LOST", async () => {
    await renderPanel(
      "d1",
      makeDrone({
        id: "d1",
        state: makeLiveState({ link: "LOST", last_seen_at: "2026-01-01T00:00:05Z" }),
      }),
    );

    await expect.element(text("stale")).toMatchTextContent("Last seen");
    await expect.element(text("last-seen")).toHaveTextContent("2026-01-01 00:00:05 UTC");
    await expect.element(text("facts")).toHaveAttribute("data-stale", "true");
  });

  it("closes with a message when the open drone is removed", async () => {
    const { fleet } = await renderPanel("d1", makeDrone({ id: "d1" }), makeDrone({ id: "d2" }));
    await expect.element(text("name")).toBeInTheDocument();

    fleet.drones.delete("d1");
    fleet.drones.set("d2", { ...fleet.drones.get("d2")!, name: "renamed" }); // a change the watcher sees

    await expect.element(text("notice")).toHaveTextContent("Drone d1 was removed");
  });

  it("turns Follow on and off with the checkbox", async () => {
    const { selection } = await renderPanel("d1", makeDrone({ id: "d1" }));

    await userEvent.click(text("follow"));
    expect(selection.follow).toBe(true);
    await expect.element(text("follow")).toBeChecked();

    await userEvent.click(text("follow"));
    expect(selection.follow).toBe(false);
  });

  it("says Drone not found for an id the API answers 404 for", async () => {
    const { selection } = await renderPanel(null, makeDrone({ id: "d1" }));

    await selection.open("nope");

    await expect.element(page.getByTestId("drone-not-found")).toHaveTextContent("Drone not found");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderPanel("d1", makeDrone({ id: "d1", name: hostile }));

    const name = page.getByTestId("drone-panel-name");
    await expect.element(name).toHaveTextContent(hostile);
    expect(name.element().querySelector("img")).toBeNull();
  });
});
