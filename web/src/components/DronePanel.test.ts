import { commandFaketory, droneFaketory, droneStateEventFaketory, liveStateFaketory } from "../../tests/faketories";
import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { fakeDb } from "../../tests/fake-db";
import { renderComponent } from "../../tests/render";
import DronePanel from "./DronePanel.vue";

// Renders DronePanel with its real children; the fleet store is filled through the fake GET /drones.
async function renderPanel(
  selected: string | null,
  ...drones: ReturnType<typeof droneFaketory.build>[]
) {
  return renderPanelAs("dispatcher", selected, ...drones);
}

async function renderPanelAs(
  role: "admin" | "dispatcher" | "viewer",
  selected: string | null,
  ...drones: ReturnType<typeof droneFaketory.build>[]
) {
  fakeDb.drones.add(...drones);
  const { pinia } = await renderComponent(DronePanel, { user: { role } });
  await useFleetStore(pinia).loadAll();
  useSelectionStore(pinia).select(selected);
  return { pinia, fleet: useFleetStore(pinia), selection: useSelectionStore(pinia) };
}

const text = (id: string) => page.getByTestId(`drone-panel-${id}`);

describe("DronePanel", () => {
  it("asks to select a drone and shows an empty command log for a dispatcher", async () => {
    await renderPanel(null, droneFaketory.build());

    await expect.element(page.getByTestId("drone-panel-empty")).toHaveTextContent("Select a drone");
    await expect.element(page.getByTestId("command-log-empty")).toHaveTextContent("No commands yet");
  });

  it("shows every detail of the selected drone", async () => {
    const state = liveStateFaketory.build({
      flight_state: "HOVERING",
      armed: true,
      battery_pct: 64,
      est_remaining_s: 725,
      position: { lat: 32.0123, lon: 34.8123, altitude_m: 42, heading_deg: 90, ground_speed_mps: 7 },
      target: { lat: 32.5, lon: 34.9 },
      mission_id: "6f1c1c2e-0000-4000-8000-000000000001",
      last_heartbeat_at: "2026-01-01T00:00:09Z",
    });
    await renderPanel("d1", droneFaketory.build({ id: "d1", name: "Alpha", state }));

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
  });

  it("shows the command form and list for the open drone to a dispatcher", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: "cmd-d1", drone_id: "d1", type: "LAND" }));

    await renderPanel("d1", droneFaketory.build({ id: "d1" }));

    await expect.element(page.getByTestId("command-form")).toBeInTheDocument();
    await expect.element(page.getByTestId("command-row-cmd-d1")).toBeInTheDocument();
  });

  it("tells the form the link is lost", async () => {
    await renderPanel("d1", droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ link: "LOST" }) }));

    await expect.element(page.getByTestId("command-link-warning")).toMatchTextContent("Link lost");
  });

  it("shows no command form and no command log to a viewer", async () => {
    await renderPanelAs("viewer", "d1", droneFaketory.build({ id: "d1" }));

    await expect.element(text("name")).toBeInTheDocument();
    expect(page.getByTestId("drone-panel-commands").elements()).toHaveLength(0);
    expect(page.getByTestId("command-log").elements()).toHaveLength(0);
  });

  it("updates the values when a drone.state event arrives for the open drone", async () => {
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1" }));
    await expect.element(text("battery")).toHaveTextContent("80%");

    fleet.applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ battery_pct: 55, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(text("battery")).toHaveTextContent("55%");
  });

  it("says No data, never null or 0, for a drone that has not reported yet", async () => {
    await renderPanel("d3", droneFaketory.build({ id: "d3", state: null }));

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
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ battery_pct: 15 }) }));
    await expect.element(text("battery")).toHaveTextContent("LOW 15%");
    await expect.element(text("battery")).toHaveAttribute("data-critical", "false");

    fleet.applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ battery_pct: 7, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(text("battery")).toHaveTextContent("CRITICAL 7%");
    await expect.element(text("battery")).toHaveAttribute("data-critical", "true");
  });

  it("shows Last seen and marks the values stale when the link is LOST", async () => {
    await renderPanel(
      "d1",
      droneFaketory.build({
        id: "d1",
        state: liveStateFaketory.build({ link: "LOST", last_seen_at: "2026-01-01T00:00:05Z" }),
      }),
    );

    await expect.element(text("stale")).toMatchTextContent("Last seen");
    await expect.element(text("last-seen")).toHaveTextContent("2026-01-01 00:00:05 UTC");
    await expect.element(text("facts")).toHaveAttribute("data-stale", "true");
  });

  it("closes with a message when the open drone is removed", async () => {
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }));
    await expect.element(text("name")).toBeInTheDocument();

    fleet.drones.delete("d1");
    fleet.drones.set("d2", { ...fleet.drones.get("d2")!, name: "renamed" }); // a change the watcher sees

    await expect.element(text("notice")).toHaveTextContent("Drone d1 was removed");
  });

  it("turns Follow on and off with the checkbox", async () => {
    const { selection } = await renderPanel("d1", droneFaketory.build({ id: "d1" }));

    await userEvent.click(text("follow"));
    expect(selection.follow).toBe(true);
    await expect.element(text("follow")).toBeChecked();

    await userEvent.click(text("follow"));
    expect(selection.follow).toBe(false);
  });

  it("says Drone not found for an id the API answers 404 for", async () => {
    const { selection } = await renderPanel(null, droneFaketory.build({ id: "d1" }));

    await selection.open("nope");

    await expect.element(page.getByTestId("drone-not-found")).toHaveTextContent("Drone not found");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderPanel("d1", droneFaketory.build({ id: "d1", name: hostile }));

    const name = page.getByTestId("drone-panel-name");
    await expect.element(name).toHaveTextContent(hostile);
    expect(name.element().querySelector("img")).toBeNull();
  });
});
