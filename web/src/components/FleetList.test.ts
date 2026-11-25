import { createPinia } from "pinia";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-vue";
import { describe, expect, it } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { makeDrone } from "../testing/factories/drone";
import { makeLiveState } from "../testing/factories/live-state";
import { worker } from "../testing/worker";
import { fakeDb, fakeEndpoints } from "./FleetList.fakes";
import FleetList from "./FleetList.vue";

// Renders only FleetList; the fleet store is filled through the fake GET /drones.
async function renderList(...drones: ReturnType<typeof makeDrone>[]) {
  fakeDb.add(...drones);
  worker.use(...fakeEndpoints);
  const pinia = createPinia();
  await render(FleetList, { global: { plugins: [pinia] } });
  await useFleetStore(pinia).loadAll();
}

describe("FleetList", () => {
  it("shows a row with name, id, link chip and battery for each drone", async () => {
    await renderList(
      makeDrone({ id: "d1", name: "Alpha" }),
      makeDrone({ id: "d2", state: makeLiveState({ link: "LOST", battery_pct: 12 }) }),
      makeDrone({ id: "d3", state: null }),
    );

    await expect.element(page.getByTestId("fleet-row-name-d1")).toHaveTextContent("Alpha");
    await expect.element(page.getByTestId("fleet-row-id-d1")).toHaveTextContent("d1");
    await expect.element(page.getByTestId("fleet-row-chip-d1")).toHaveTextContent("UP");
    await expect.element(page.getByTestId("fleet-row-chip-d2")).toHaveTextContent("LOST");
    await expect.element(page.getByTestId("fleet-row-battery-d2")).toHaveTextContent("LOW 12%");
    await expect.element(page.getByTestId("fleet-row-chip-d3")).toHaveTextContent("NO DATA");
  });

  it("selects a drone by click and highlights its row", async () => {
    await renderList(makeDrone({ id: "d1" }), makeDrone({ id: "d2" }));

    await userEvent.click(page.getByTestId("fleet-row-d1"));

    await expect.element(page.getByTestId("fleet-row-d1")).toHaveAttribute("data-selected", "true");
    await expect.element(page.getByTestId("fleet-row-d2")).toHaveAttribute("data-selected", "false");
  });

  it("selects a drone with Enter and with Space from the keyboard", async () => {
    await renderList(makeDrone({ id: "d1" }), makeDrone({ id: "d2" }), makeDrone({ id: "d3" }));
    await expect.element(page.getByTestId("fleet-row-d3")).toBeInTheDocument();
    (page.getByTestId("fleet-row-d1").element() as HTMLElement).focus();

    await userEvent.keyboard("{Tab}{Enter}");
    await expect.element(page.getByTestId("fleet-row-d2")).toHaveAttribute("data-selected", "true");

    await userEvent.keyboard("{Tab} ");
    await expect.element(page.getByTestId("fleet-row-d3")).toHaveAttribute("data-selected", "true");
    await expect.element(page.getByTestId("fleet-row-d2")).toHaveAttribute("data-selected", "false");
  });

  it("renders a hostile drone name as text, never as HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    await renderList(makeDrone({ id: "d1", name: hostile }));

    const name = page.getByTestId("fleet-row-name-d1");
    await expect.element(name).toHaveTextContent(hostile);
    expect(name.element().querySelector("img")).toBeNull();
  });
});
