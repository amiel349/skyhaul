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

const shown = (value: string) => page.getByText(value, { exact: true });

// A fact's value is the <dd> after its <dt>. Terms and definitions pair up by position, so the label picks the value.
async function fact(label: string) {
  await expect.element(shown(label)).toBeInTheDocument();
  const labels = page.getByRole("term").elements().map((term) => term.textContent?.trim());
  return page.getByRole("definition").nth(labels.indexOf(label));
}

const commandForm = () => page.getByRole("form", { name: "Send a command" });

describe("DronePanel", () => {
  it("asks to select a drone and shows an empty command log for a dispatcher", async () => {
    await renderPanel(null, droneFaketory.build());

    await expect.element(shown("Select a drone")).toBeInTheDocument();
    await expect.element(shown("No commands yet")).toBeInTheDocument();
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

    await expect.element(shown("Alpha")).toBeInTheDocument();
    await expect.element(shown("d1")).toBeInTheDocument();
    await expect.element(await fact("Flight state")).toHaveTextContent("HOVERING");
    await expect.element(await fact("Armed")).toHaveTextContent("Yes");
    await expect.element(await fact("Battery")).toHaveTextContent("64%");
    await expect.element(await fact("Remaining")).toHaveTextContent("12 min 5 s");
    await expect.element(await fact("Link")).toHaveTextContent("UP");
    await expect.element(await fact("Last heartbeat")).toHaveTextContent("2026-01-01 00:00:09 UTC");
    await expect.element(await fact("Position")).toHaveTextContent("32.01230, 34.81230");
    await expect.element(await fact("Altitude")).toHaveTextContent("42 m");
    await expect.element(await fact("Heading")).toHaveTextContent("90°");
    await expect.element(await fact("Speed")).toHaveTextContent("7 m/s");
    await expect.element(await fact("Target")).toHaveTextContent("32.50000, 34.90000");
    await expect.element(await fact("Mission")).toHaveTextContent("6f1c1c2e-0000-4000-8000-000000000001");
  });

  it("shows the command form and list for the open drone to a dispatcher", async () => {
    fakeDb.commands.add(commandFaketory.build({ id: "cmd-d1", drone_id: "d1", type: "LAND" }));

    await renderPanel("d1", droneFaketory.build({ id: "d1" }));

    await expect.element(commandForm()).toBeInTheDocument();
    await expect.element(page.getByRole("listitem")).toMatchTextContent("LAND");
  });

  it("tells the form the link is lost", async () => {
    await renderPanel("d1", droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ link: "LOST" }) }));

    await expect.element(commandForm().getByRole("status")).toMatchTextContent("Link lost");
  });

  it("shows no command form and no command log to a viewer", async () => {
    await renderPanelAs("viewer", "d1", droneFaketory.build({ id: "d1" }));

    await expect.element(shown("Drone d1")).toBeInTheDocument();
    expect(commandForm().elements()).toHaveLength(0);
    expect(page.getByRole("region", { name: "Command log" }).elements()).toHaveLength(0);
  });

  it("updates the values when a drone.state event arrives for the open drone", async () => {
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1" }));
    await expect.element(await fact("Battery")).toHaveTextContent("80%");

    fleet.applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ battery_pct: 55, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(await fact("Battery")).toHaveTextContent("55%");
  });

  it("says No data, never null or 0, for a drone that has not reported yet", async () => {
    await renderPanel("d3", droneFaketory.build({ id: "d3", state: null }));

    for (const label of [
      "Flight state",
      "Armed",
      "Battery",
      "Remaining",
      "Last heartbeat",
      "Position",
      "Altitude",
      "Heading",
      "Speed",
      "Target",
      "Mission",
    ]) {
      await expect.element(await fact(label)).toHaveTextContent("No data");
    }
    await expect.element(await fact("Link")).toHaveTextContent("NO DATA");
  });

  it("shows LOW under 20% and CRITICAL under 10% in words", async () => {
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1", state: liveStateFaketory.build({ battery_pct: 15 }) }));
    await expect.element(await fact("Battery")).toHaveTextContent("LOW 15%");
    // data-critical exists only as a data attribute, so the battery is found by its test id.
    await expect.element(page.getByTestId("drone-panel-battery")).toHaveAttribute("data-critical", "false");

    fleet.applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ battery_pct: 7, ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(await fact("Battery")).toHaveTextContent("CRITICAL 7%");
    await expect.element(page.getByTestId("drone-panel-battery")).toHaveAttribute("data-critical", "true");
  });

  it("shows Last seen and marks the values stale when the link is LOST", async () => {
    await renderPanel(
      "d1",
      droneFaketory.build({
        id: "d1",
        state: liveStateFaketory.build({ link: "LOST", last_seen_at: "2026-01-01T00:00:05Z" }),
      }),
    );

    await expect.element(page.getByRole("status").filter({ hasText: "Last seen" })).toMatchTextContent("Last seen");
    await expect.element(shown("2026-01-01 00:00:05 UTC")).toBeInTheDocument();
    // data-stale exists only as a data attribute, so the facts list is found by its test id.
    await expect.element(page.getByTestId("drone-panel-facts")).toHaveAttribute("data-stale", "true");
  });

  it("closes with a message when the open drone is removed", async () => {
    const { fleet } = await renderPanel("d1", droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }));
    await expect.element(shown("Drone d1")).toBeInTheDocument();

    fleet.drones.delete("d1");
    fleet.drones.set("d2", { ...fleet.drones.get("d2")!, name: "renamed" }); // a change the watcher sees

    await expect.element(page.getByRole("alert")).toHaveTextContent("Drone d1 was removed");
  });

  it("turns Follow on and off with the checkbox", async () => {
    const { selection } = await renderPanel("d1", droneFaketory.build({ id: "d1" }));

    const follow = page.getByRole("checkbox", { name: "Follow on map" });
    await userEvent.click(follow);
    expect(selection.follow).toBe(true);
    await expect.element(follow).toBeChecked();

    await userEvent.click(follow);
    expect(selection.follow).toBe(false);
  });

  it("says Drone not found for an id the API answers 404 for", async () => {
    const { selection } = await renderPanel(null, droneFaketory.build({ id: "d1" }));

    await selection.open("nope");

    await expect.element(page.getByRole("alert")).toHaveTextContent("Drone not found");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderPanel("d1", droneFaketory.build({ id: "d1", name: hostile }));

    await expect.element(shown(hostile)).toBeInTheDocument();
    expect(page.getByRole("img").elements()).toHaveLength(0);
  });
});
