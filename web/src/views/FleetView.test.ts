import { droneFaketory } from "../../tests/faketories";
import { page, userEvent } from "vitest/browser";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useFleetStore } from "../stores/fleet";
import { useSelectionStore } from "../stores/selection";
import { fake } from "../../tests/fake-socket";
import { renderComponent } from "../../tests/render";
import { worker } from "../../tests/worker";
import { fakeDb } from "../../tests/fake-db";
import { droneEndpoints } from "../../tests/fake-endpoints";
import FleetView from "./FleetView.vue";

vi.mock("../realtime/socket", async () => await import("../../tests/fake-socket"));

beforeEach(() => fake.reset());

// Renders FleetView signed in as a dispatcher, with its real children (list, map, panel, splitters).
// It keeps the URL and the selection in step, so it runs on the app's router.
async function renderView(route = "/") {
  const view = await renderComponent(FleetView, { route, user: { role: "dispatcher" } });
  return { pinia: view.pinia, view, router: view.router };
}

const bar = (name: string) => page.getByRole("separator", { name });
const focus = (name: string) => (bar(name).element() as HTMLElement).focus();

describe("FleetView", () => {
  it("loads the drones and opens the live socket on mount, and closes it on leaving", async () => {
    fakeDb.drones.add(droneFaketory.build({ id: "d1" }), droneFaketory.build({ id: "d2" }));
    const { pinia, view } = await renderView();

    await expect.poll(() => useFleetStore(pinia).list.length).toBe(2);
    expect(fake.open).toBe(true);

    await view.unmount();
    expect(fake.open).toBe(false);
  });

  it("puts the selected drone in the URL", async () => {
    fakeDb.drones.add(droneFaketory.build({ id: "d1" }));
    const { pinia, router } = await renderView();
    await expect.poll(() => useFleetStore(pinia).list.length).toBe(1);

    useSelectionStore(pinia).select("d1");

    await expect.poll(() => router.currentRoute.value.fullPath).toBe("/drones/d1");
  });

  it("selects the drone named in the URL, loading it by id when the list does not have it yet", async () => {
    fakeDb.drones.add(droneFaketory.build({ id: "d1", name: "Alpha" }));
    const { pinia } = await renderView("/drones/d1");

    await expect.poll(() => useSelectionStore(pinia).selectedId).toBe("d1");
    expect(useFleetStore(pinia).drones.get("d1")?.name).toBe("Alpha");
  });

  it("flags an id in the URL that the API does not know", async () => {
    const { pinia, router } = await renderView("/drones/nope");

    await expect.poll(() => useSelectionStore(pinia).notFoundId).toBe("nope");
    expect(router.currentRoute.value.fullPath).toBe("/drones/nope");
  });

  it("says so when there are no drones, and shows the empty alerts placeholder", async () => {
    await renderView();

    await expect.element(page.getByRole("status")).toHaveTextContent("No drones yet");
    await expect.element(page.getByText("No alerts", { exact: true })).toBeInTheDocument();
  });

  it("says so when the drones cannot be loaded", async () => {
    worker.use(droneEndpoints.respondWith(500));
    await renderView();

    await expect.element(page.getByRole("alert")).toHaveTextContent("Could not load the drones");
  });

  it("resizes a panel with the keyboard within its limits, and Home, End and Enter jump and reset", async () => {
    await renderView();
    focus("Resize fleet list");
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", "270");

    await userEvent.keyboard("{ArrowRight}");
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", "286");
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", "222");
    await userEvent.keyboard("{Home}{ArrowLeft}");
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", "200");
    await userEvent.keyboard("{End}");
    const max = bar("Resize fleet list").element().getAttribute("aria-valuemax") ?? "";
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", max);
    await userEvent.keyboard("{Enter}");
    await expect.element(bar("Resize fleet list")).toHaveAttribute("aria-valuenow", "270");

    // The right panel grows when its divider moves left.
    focus("Resize drone panel");
    await userEvent.keyboard("{ArrowLeft}");
    await expect.element(bar("Resize drone panel")).toHaveAttribute("aria-valuenow", "346");
  });

  it("changes the map height with a pointer drag", async () => {
    await renderView();
    await expect.element(page.getByRole("status")).toBeInTheDocument(); // the layout has settled
    const handle = bar("Resize map and alerts");
    await expect.element(handle).toHaveAttribute("aria-valuenow", "420");
    const rect = handle.element().getBoundingClientRect();
    // A dot 50px above the bar's centre, placed in page coordinates so it stays put if the page scrolls.
    const target = document.createElement("div");
    target.style.cssText = `position: absolute; width: 2px; height: 2px; left: ${rect.left + window.scrollX + rect.width / 2}px; top: ${rect.top + window.scrollY + rect.height / 2 - 51}px`; // the dot is 2px: its centre is 1px below its top
    document.body.append(target);

    await userEvent.dragAndDrop(handle, target);
    target.remove();

    await expect.element(handle).toHaveAttribute("aria-valuenow", "370");
  });
});
