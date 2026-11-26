import { droneFaketory, droneStateEventFaketory, liveStateFaketory } from "../../tests/faketories";
import { page, userEvent } from "vitest/browser";
import { describe, expect, it } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { fakeDb } from "../../tests/fake-db";
import { renderComponent } from "../../tests/render";
import FleetList from "./FleetList.vue";

// Renders only FleetList; the fleet store is filled through the fake GET /drones.
async function renderList(...drones: ReturnType<typeof droneFaketory.build>[]) {
  fakeDb.drones.add(...drones);
  const { pinia } = await renderComponent(FleetList);
  await useFleetStore(pinia).loadAll();
  return pinia;
}

// Every faketory drone is named "Drone d1" unless told otherwise, so a row is picked by the id it shows.
const row = (id: string) => page.getByRole("button").filter({ has: page.getByText(id, { exact: true }) });
const search = () => page.getByRole("searchbox", { name: "Search drones by name or id" });

describe("FleetList", () => {
  it("shows a row with name, id, link chip and battery for each drone", async () => {
    await renderList(
      droneFaketory.build({ id: "d1", name: "Alpha" }),
      droneFaketory.build({ id: "d2", state: liveStateFaketory.build({ link: "LOST", battery_pct: 12 }) }),
      droneFaketory.build({ id: "d3", state: null }),
    );

    await expect.element(row("d1").getByText("Alpha", { exact: true })).toBeInTheDocument();
    await expect.element(row("d1").getByText("d1", { exact: true })).toBeInTheDocument();
    await expect.element(row("d1").getByText("UP", { exact: true })).toBeInTheDocument();
    await expect.element(row("d2").getByText("LOST", { exact: true })).toBeInTheDocument();
    await expect.element(row("d2").getByText("LOW 12%", { exact: true })).toBeInTheDocument();
    await expect.element(row("d3").getByText("NO DATA", { exact: true })).toBeInTheDocument();
  });

  it("shows the flight state, updates a row live and keeps the order", async () => {
    const pinia = await renderList(droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }));
    await expect.element(row("d1").getByText("FLYING", { exact: true })).toBeInTheDocument();

    useFleetStore(pinia).applyState(
      droneStateEventFaketory.build({
        drone_id: "d1",
        state: liveStateFaketory.build({ flight_state: "LANDING", ts: "2026-01-01T00:00:20.000Z" }),
      }),
    );

    await expect.element(row("d1").getByText("LANDING", { exact: true })).toBeInTheDocument();
    await expect.element(page.getByRole("button").nth(0)).toMatchTextContent("d1");
    await expect.element(page.getByRole("button").nth(1)).toMatchTextContent("d2");
    expect(page.getByRole("button").elements()).toHaveLength(2);
  });

  it("searches by name or id, ignoring case, and says so when nothing matches", async () => {
    await renderList(droneFaketory.build({ id: "gw0-d001", name: "Alpha" }), droneFaketory.build({ id: "gw0-d002", name: "Bravo" }));

    await userEvent.fill(search(), "ALPH");
    await expect.element(row("gw0-d001")).toBeInTheDocument();
    await expect.element(row("gw0-d002")).not.toBeInTheDocument();

    await userEvent.fill(search(), "D002");
    await expect.element(row("gw0-d002")).toBeInTheDocument();

    await userEvent.fill(search(), "zzz");
    await expect.element(page.getByRole("status")).toHaveTextContent("No drones match");
  });

  it("filters by link", async () => {
    await renderList(
      droneFaketory.build({ id: "d1" }),
      droneFaketory.build({ id: "d2", state: liveStateFaketory.build({ link: "LOST" }) }),
    );

    await userEvent.selectOptions(page.getByRole("combobox", { name: "Filter by link" }), "LOST");

    await expect.element(row("d2")).toBeInTheDocument();
    await expect.element(row("d1")).not.toBeInTheDocument();
  });

  it("selects a drone by click and highlights its row", async () => {
    await renderList(droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }));

    await userEvent.click(row("d1"));

    await expect.element(row("d1")).toHaveAttribute("aria-pressed", "true");
    await expect.element(row("d2")).toHaveAttribute("aria-pressed", "false");
  });

  it("selects a drone with Enter and with Space from the keyboard", async () => {
    await renderList(droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }), droneFaketory.build({ id: "d3" }));
    await expect.element(row("d3")).toBeInTheDocument();
    (row("d1").element() as HTMLElement).focus();

    await userEvent.keyboard("{Tab}{Enter}");
    await expect.element(row("d2")).toHaveAttribute("aria-pressed", "true");

    await userEvent.keyboard("{Tab} ");
    await expect.element(row("d3")).toHaveAttribute("aria-pressed", "true");
    await expect.element(row("d2")).toHaveAttribute("aria-pressed", "false");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderList(droneFaketory.build({ id: "d1", name: hostile }));

    await expect.element(page.getByText(hostile, { exact: true })).toBeInTheDocument();
    expect(page.getByRole("img").elements()).toHaveLength(0);
  });
});
